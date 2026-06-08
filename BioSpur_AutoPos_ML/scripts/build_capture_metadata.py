#!/usr/bin/env python3
"""Build capture-level metadata and label-quality gates.

This script separates capture usefulness from supervised training usefulness.
No-tag multipath captures are explicitly allowed for residual/risk analysis, but
they are not treated as ground-truth localization labels.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST = Path("DATASETS/processed/capture_manifest.json")
OVERRIDES = Path("DATASETS/processed/capture_metadata_overrides.csv")
PROCESSED_DIR = Path("DATASETS/processed")
REPORT_DIR = Path("outputs/reports")


@dataclass
class CaptureMetadataRow:
    capture_id: str
    root: str
    environment_type: str
    condition: str
    capture_role: str
    file_count: int
    layout_count: int
    static_table_count: int
    roto_table_count: int
    wand_table_count: int
    validation_table_count: int
    dop_table_count: int
    residual_table_count: int
    optitrack_file_count: int
    has_layouts: bool
    has_tag_capture: bool
    has_ground_truth: bool
    has_proxy_evaluation: bool
    has_dop: bool
    has_residual: bool
    multipath_usable: bool
    no_tag_multipath_usable: bool
    label_quality: str
    recommended_use: str
    train_allowed: bool
    validation_allowed: bool
    evidence: str
    notes: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--overrides", type=Path, default=OVERRIDES)
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    return parser.parse_args()


def read_manifest(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("captures", []))


def read_overrides(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        return {row["capture_id"]: row for row in csv.DictReader(handle) if row.get("capture_id")}


def bool_override(value: str, default: bool) -> bool:
    value = (value or "").strip().lower()
    if value in {"true", "1", "yes", "y"}:
        return True
    if value in {"false", "0", "no", "n"}:
        return False
    return default


def infer_environment(capture_id: str) -> str:
    lower = capture_id.lower()
    if "outdoor" in lower:
        return "outdoor"
    if "garage" in lower:
        return "garage"
    if "basement" in lower or "keller" in lower:
        return "basement"
    if "erlangen" in lower:
        return "indoor_lab"
    return "unknown"


def infer_condition(capture_id: str, environment_type: str) -> str:
    lower = capture_id.lower()
    if "nlos" in lower:
        return "nlos"
    if "los" in lower:
        return "los"
    if "multipath" in lower or "mpath" in lower:
        return "multipath"
    if environment_type in {"garage", "basement"}:
        return "multipath_possible"
    if environment_type == "outdoor":
        return "outdoor_unspecified"
    if environment_type == "indoor_lab":
        return "controlled_unspecified"
    return "unknown"


def infer_role(has_layouts: bool, has_tag_capture: bool, has_ground_truth: bool) -> str:
    if has_layouts and has_tag_capture and has_ground_truth:
        return "full_process_ground_truth"
    if has_layouts and has_tag_capture:
        return "full_process_proxy"
    if has_layouts:
        return "layout_only"
    if has_tag_capture:
        return "tag_capture_only"
    return "raw_capture_only"


def is_multipath_like(environment_type: str, condition: str) -> bool:
    text = f"{environment_type} {condition}".lower()
    return any(token in text for token in ("multipath", "nlos", "garage", "basement"))


def default_label_quality(
    has_ground_truth: bool,
    has_proxy_evaluation: bool,
    has_layouts: bool,
    multipath_usable: bool,
    has_tag_capture: bool,
) -> tuple[str, str, bool, bool]:
    if has_ground_truth:
        return "real_ground_truth_validation", "calibration_validation", False, True
    if multipath_usable and not has_tag_capture:
        return "multipath_unlabeled_no_tag", "multipath_risk_analysis", False, False
    if has_proxy_evaluation:
        return "proxy_existing_field_evaluation", "ranking_and_proxy_analysis", False, False
    if has_layouts:
        return "unlabeled_geometry_only", "candidate_generation_only", False, False
    return "raw_unclassified", "inventory_only", False, False


def list_count(manifest: dict[str, Any], key: str) -> int:
    value = manifest.get(key, [])
    return len(value) if isinstance(value, list) else 0


def build_rows(manifests: list[dict[str, Any]], overrides: dict[str, dict[str, str]]) -> list[CaptureMetadataRow]:
    rows: list[CaptureMetadataRow] = []
    for manifest in sorted(manifests, key=lambda item: str(item.get("capture_id", ""))):
        capture_id = str(manifest.get("capture_id", ""))
        override = overrides.get(capture_id, {})

        layout_count = list_count(manifest, "layout_files")
        static_count = list_count(manifest, "static_tables")
        roto_count = list_count(manifest, "roto_tables")
        wand_count = list_count(manifest, "wand_tables")
        validation_count = list_count(manifest, "validation_tables")
        dop_count = list_count(manifest, "dop_tables")
        residual_count = list_count(manifest, "residual_tables")
        optitrack_count = list_count(manifest, "optitrack_files")

        has_layouts = layout_count > 0
        inferred_tag = static_count > 0 or roto_count > 0 or wand_count > 0
        inferred_gt = capture_id == "28052026_Erlangen_Official" and optitrack_count > 0 and validation_count > 0

        environment_type = override.get("environment_type") or infer_environment(capture_id)
        condition = override.get("condition") or infer_condition(capture_id, environment_type)
        has_tag_capture = bool_override(override.get("has_tag_capture", ""), inferred_tag)
        has_ground_truth = bool_override(override.get("has_ground_truth", ""), inferred_gt)
        has_proxy = has_layouts and has_tag_capture and not has_ground_truth
        has_dop = dop_count > 0
        has_residual = residual_count > 0
        multipath_usable = is_multipath_like(environment_type, condition) and (
            has_layouts or has_tag_capture or has_residual or bool(manifest.get("file_count"))
        )
        no_tag_multipath_usable = multipath_usable and not has_tag_capture

        label_quality, recommended_use, train_allowed, validation_allowed = default_label_quality(
            has_ground_truth=has_ground_truth,
            has_proxy_evaluation=has_proxy,
            has_layouts=has_layouts,
            multipath_usable=multipath_usable,
            has_tag_capture=has_tag_capture,
        )
        label_quality = override.get("label_quality") or label_quality
        recommended_use = override.get("recommended_use") or recommended_use

        evidence_parts = []
        if has_layouts:
            evidence_parts.append("layouts")
        if has_tag_capture:
            evidence_parts.append("tag_capture")
        if has_ground_truth:
            evidence_parts.append("ground_truth")
        if has_dop:
            evidence_parts.append("dop")
        if has_residual:
            evidence_parts.append("residual")
        if no_tag_multipath_usable:
            evidence_parts.append("no_tag_multipath")

        rows.append(
            CaptureMetadataRow(
                capture_id=capture_id,
                root=str(manifest.get("root", "")),
                environment_type=environment_type,
                condition=condition,
                capture_role=infer_role(has_layouts, has_tag_capture, has_ground_truth),
                file_count=int(manifest.get("file_count", 0)),
                layout_count=layout_count,
                static_table_count=static_count,
                roto_table_count=roto_count,
                wand_table_count=wand_count,
                validation_table_count=validation_count,
                dop_table_count=dop_count,
                residual_table_count=residual_count,
                optitrack_file_count=optitrack_count,
                has_layouts=has_layouts,
                has_tag_capture=has_tag_capture,
                has_ground_truth=has_ground_truth,
                has_proxy_evaluation=has_proxy,
                has_dop=has_dop,
                has_residual=has_residual,
                multipath_usable=multipath_usable,
                no_tag_multipath_usable=no_tag_multipath_usable,
                label_quality=label_quality,
                recommended_use=recommended_use,
                train_allowed=train_allowed,
                validation_allowed=validation_allowed,
                evidence=";".join(evidence_parts),
                notes=override.get("notes", ""),
            )
        )
    return rows


def fmt(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def write_csv(path: Path, rows: list[CaptureMetadataRow]) -> None:
    fieldnames = list(CaptureMetadataRow.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: fmt(value) for key, value in asdict(row).items()})


def write_json(path: Path, rows: list[CaptureMetadataRow]) -> None:
    payload = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "captures": [asdict(row) for row in rows],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_report(path: Path, rows: list[CaptureMetadataRow]) -> None:
    label_counts = Counter(row.label_quality for row in rows)
    env_counts = Counter(row.environment_type for row in rows)
    condition_counts = Counter(row.condition for row in rows)
    no_tag_multipath = [row for row in rows if row.no_tag_multipath_usable]

    lines = [
        "# Capture Metadata",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Captures: `{len(rows)}`",
        f"- Ground-truth captures: `{sum(1 for row in rows if row.has_ground_truth)}`",
        f"- Tag-capture available: `{sum(1 for row in rows if row.has_tag_capture)}`",
        f"- No-tag multipath usable: `{len(no_tag_multipath)}`",
        f"- Train-allowed captures: `{sum(1 for row in rows if row.train_allowed)}`",
        "",
        "## Label Quality Counts",
        "",
    ]
    for key, count in sorted(label_counts.items()):
        lines.append(f"- `{key}`: {count}")
    lines.extend(["", "## Environment Counts", ""])
    for key, count in sorted(env_counts.items()):
        lines.append(f"- `{key}`: {count}")
    lines.extend(["", "## Condition Counts", ""])
    for key, count in sorted(condition_counts.items()):
        lines.append(f"- `{key}`: {count}")
    lines.extend(
        [
            "",
            "## Capture Table",
            "",
            "| Capture | Env | Condition | Role | Layouts | Tag | GT | Label Quality | Use | Evidence |",
            "|---|---|---|---|---:|---|---|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| `{row.capture_id}` | `{row.environment_type}` | `{row.condition}` | `{row.capture_role}` | "
            f"{row.layout_count} | `{str(row.has_tag_capture).lower()}` | `{str(row.has_ground_truth).lower()}` | "
            f"`{row.label_quality}` | `{row.recommended_use}` | `{row.evidence}` |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_multipath_report(path: Path, rows: list[CaptureMetadataRow]) -> None:
    multipath_rows = [row for row in rows if row.multipath_usable]
    no_tag_rows = [row for row in rows if row.no_tag_multipath_usable]
    lines = [
        "# No-Tag Multipath Intake",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Policy",
        "",
        "- No-tag multipath captures can be used for environment and residual-risk analysis.",
        "- They must not be used as real localization-error labels.",
        "- They remain `train_allowed=false` until a ground-truth tag trajectory is available.",
        "",
        "## Current Intake",
        "",
        f"- Multipath-like captures: `{len(multipath_rows)}`",
        f"- No-tag multipath captures: `{len(no_tag_rows)}`",
        "",
        "| Capture | Env | Condition | Layouts | Tag | GT | Use | Notes |",
        "|---|---|---|---:|---|---|---|---|",
    ]
    for row in multipath_rows:
        lines.append(
            f"| `{row.capture_id}` | `{row.environment_type}` | `{row.condition}` | {row.layout_count} | "
            f"`{str(row.has_tag_capture).lower()}` | `{str(row.has_ground_truth).lower()}` | "
            f"`{row.recommended_use}` | `{row.notes}` |"
        )
    lines.extend(
        [
            "",
            "## Required Metadata For Future Basement/NLOS Captures",
            "",
            "- `environment_type`: `basement`, `garage`, `indoor_lab`, or `outdoor`.",
            "- `condition`: `multipath`, `nlos`, `multipath_possible`, or `los`.",
            "- `has_tag_capture`: set `false` when static/roto/wand/tag replay is missing.",
            "- `has_ground_truth`: set `false` unless OptiTrack or equivalent true trajectory exists.",
            "- `notes`: describe reflectors, wall material, obstacles, and anchor visibility.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.processed_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    rows = build_rows(read_manifest(args.manifest), read_overrides(args.overrides))
    write_csv(args.processed_dir / "capture_metadata.csv", rows)
    write_json(args.processed_dir / "capture_metadata.json", rows)
    write_report(args.report_dir / "capture_metadata.md", rows)
    write_multipath_report(args.report_dir / "multipath_no_tag_intake.md", rows)
    print(f"capture_metadata_rows={len(rows)}")
    print(f"no_tag_multipath={sum(1 for row in rows if row.no_tag_multipath_usable)}")
    print(f"wrote {args.processed_dir / 'capture_metadata.csv'}")
    print(f"wrote {args.report_dir / 'capture_metadata.md'}")
    print(f"wrote {args.report_dir / 'multipath_no_tag_intake.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
