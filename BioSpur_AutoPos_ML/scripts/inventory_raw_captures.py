#!/usr/bin/env python3
"""Inventory raw AutoPos capture folders.

The script is intentionally conservative: it never mutates raw capture files.
It scans file names, lightweight CSV headers, and selected JSON metadata to
produce a searchable file inventory plus a per-capture manifest.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RAW_ROOT = Path("DATASETS/raw_captures")
PROCESSED_DIR = Path("DATASETS/processed")
REPORT_DIR = Path("outputs/reports")


TEXT_EXTS = {".csv", ".json", ".md", ".txt", ".log", ".yaml", ".yml"}
SKIP_DIR_NAMES = {"__pycache__", ".git"}


@dataclass
class InventoryRow:
    capture_id: str
    rel_path: str
    file_name: str
    extension: str
    size_bytes: int
    category: str
    data_role: str
    solver_version: str
    layout_variant: str
    is_optitrack_related: bool
    is_generated_analysis: bool
    csv_columns: str = ""
    csv_row_count: str = ""
    json_kind: str = ""
    anchor_count: str = ""
    notes: str = ""


@dataclass
class CaptureManifest:
    capture_id: str
    root: str
    file_count: int = 0
    total_size_bytes: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    data_roles: dict[str, int] = field(default_factory=dict)
    layout_files: list[str] = field(default_factory=list)
    validation_tables: list[str] = field(default_factory=list)
    dop_tables: list[str] = field(default_factory=list)
    residual_tables: list[str] = field(default_factory=list)
    static_tables: list[str] = field(default_factory=list)
    roto_tables: list[str] = field(default_factory=list)
    wand_tables: list[str] = field(default_factory=list)
    optitrack_files: list[str] = field(default_factory=list)
    report_files: list[str] = field(default_factory=list)
    metadata_files: list[str] = field(default_factory=list)
    has_optitrack: bool = False
    has_static: bool = False
    has_roto: bool = False
    has_wand: bool = False
    has_dop: bool = False
    has_layouts: bool = False
    notes: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument(
        "--csv-row-count-limit-mb",
        type=float,
        default=20.0,
        help="Only count CSV rows for files at or below this size.",
    )
    return parser.parse_args()


def capture_id_for(path: Path, raw_root: Path) -> str:
    rel = path.relative_to(raw_root)
    return rel.parts[0] if rel.parts else ""


def classify_file(path: Path, raw_root: Path) -> tuple[str, str, str, str, bool, bool, str]:
    rel = path.relative_to(raw_root)
    lower_parts = [part.lower() for part in rel.parts]
    name = path.name.lower()
    stem = path.stem.lower()
    suffix = path.suffix.lower()
    path_text = "/".join(lower_parts)

    solver_version = ""
    for part in rel.parts:
        pl = part.lower()
        if pl in {
            "v1",
            "v1-old",
            "v2",
            "v3",
            "v3full",
            "v3-full",
            "v3lite",
            "v3-lite",
            "v4",
            "v4-io",
            "v4-io-roto",
            "v4-io-td",
            "v4-io-wand",
            "v5",
        }:
            solver_version = part
            break

    layout_variant = ""
    if name.startswith("layout") and suffix == ".json":
        if "consensus" in name:
            layout_variant = "consensus"
        elif "first500" in name:
            layout_variant = "first500"
        elif "last500" in name:
            layout_variant = "last500_aligned"
        elif "us_height" in name:
            layout_variant = "us_height"
        else:
            layout_variant = "default"

    is_optitrack = any(token in path_text for token in ("opti", "mocap", "ground_truth"))
    is_analysis = any(
        token in lower_parts
        for token in (
            "analysis",
            "reports",
            "figures",
            "figs",
            "tables",
            "outputs",
            "solves",
            "positioning",
            "monte-carlo-simulation",
        )
    )

    if suffix == ".json" and name.startswith("layout"):
        category = "layout"
    elif suffix == ".csv" and "dop" in name:
        category = "dop_table"
    elif suffix == ".csv" and "residual" in name:
        category = "residual_table"
    elif suffix == ".csv" and "wand" in name:
        category = "wand_table"
    elif suffix == ".csv" and "roto" in name:
        category = "roto_table"
    elif suffix == ".csv" and "static" in name:
        category = "static_table"
    elif suffix == ".csv" and ("error" in name or "accuracy" in name or "abs_errors" in name):
        category = "validation_table"
    elif suffix == ".csv" and ("summary" in name or "quality" in name or "comparison" in name):
        category = "summary_table"
    elif suffix == ".json" and name in {"workspace.json", "run_meta.json"}:
        category = "metadata"
    elif suffix == ".json" and ("summary" in name or "metadata" in name or "manifest" in name):
        category = "metadata"
    elif suffix in {".md", ".txt"}:
        category = "report_or_note"
    elif suffix == ".log":
        category = "log"
    elif suffix in {".png", ".jpg", ".jpeg", ".svg", ".pdf"}:
        category = "figure"
    elif suffix in {".py", ".ipynb"}:
        category = "script"
    else:
        category = "other"

    if category == "layout":
        data_role = "layout_candidate"
    elif category in {"validation_table"}:
        data_role = "ground_truth_validation"
    elif category == "dop_table":
        data_role = "geometry_quality"
    elif category in {"residual_table", "static_table", "roto_table", "wand_table", "summary_table"}:
        data_role = "evaluation_metric"
    elif category == "metadata":
        data_role = "metadata"
    elif category in {"report_or_note", "figure"}:
        data_role = "human_report"
    elif category == "log":
        data_role = "runtime_log"
    elif category == "script":
        data_role = "analysis_code"
    elif is_optitrack:
        data_role = "optitrack_capture"
    else:
        data_role = "unclassified"

    notes = []
    if "monte-carlo" in path_text:
        notes.append("monte_carlo")
    if "keepk" in name:
        notes.append("keepk")
    if "first500" in name or "last500" in name or "holdout" in name:
        notes.append("split_or_holdout")
    if "tag_delay" in name:
        notes.append("tag_delay")
    if "pairdrop" in name or "rangebias" in name:
        notes.append("stress_variant")

    return (
        category,
        data_role,
        solver_version,
        layout_variant,
        is_optitrack,
        is_analysis,
        ";".join(notes),
    )


def csv_info(path: Path, row_count_limit_bytes: int) -> tuple[str, str]:
    try:
        with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            columns = "|".join(header)
            if path.stat().st_size > row_count_limit_bytes:
                return columns, ""
            count = sum(1 for _ in reader)
            return columns, str(count)
    except Exception as exc:  # noqa: BLE001 - inventory should continue.
        return "", f"error:{exc.__class__.__name__}"


def json_info(path: Path) -> tuple[str, str, str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
    except Exception as exc:  # noqa: BLE001 - inventory should continue.
        return "", "", f"json_error:{exc.__class__.__name__}"

    if isinstance(data, dict) and isinstance(data.get("anchors"), list):
        return "layout", str(len(data["anchors"])), ""
    if isinstance(data, dict):
        if "workspace_name" in data:
            return "workspace", "", ""
        if "manifest" in path.name.lower() or "metadata" in path.name.lower():
            return "metadata", "", ""
        if "summary" in path.name.lower():
            return "summary", "", ""
        return "object", "", ""
    if isinstance(data, list):
        return "list", "", ""
    return type(data).__name__, "", ""


def iter_files(raw_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in raw_root.rglob("*"):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return sorted(files)


def build_inventory(args: argparse.Namespace) -> list[InventoryRow]:
    raw_root = args.raw_root
    row_count_limit_bytes = int(args.csv_row_count_limit_mb * 1024 * 1024)
    rows: list[InventoryRow] = []

    for path in iter_files(raw_root):
        rel_path = path.relative_to(raw_root).as_posix()
        stat = path.stat()
        (
            category,
            data_role,
            solver_version,
            layout_variant,
            is_optitrack,
            is_analysis,
            notes,
        ) = classify_file(path, raw_root)

        csv_columns = ""
        csv_row_count = ""
        json_kind = ""
        anchor_count = ""
        if path.suffix.lower() == ".csv":
            csv_columns, csv_row_count = csv_info(path, row_count_limit_bytes)
        elif path.suffix.lower() == ".json" and stat.st_size <= 5 * 1024 * 1024:
            json_kind, anchor_count, json_notes = json_info(path)
            notes = ";".join(part for part in (notes, json_notes) if part)

        rows.append(
            InventoryRow(
                capture_id=capture_id_for(path, raw_root),
                rel_path=rel_path,
                file_name=path.name,
                extension=path.suffix.lower(),
                size_bytes=stat.st_size,
                category=category,
                data_role=data_role,
                solver_version=solver_version,
                layout_variant=layout_variant,
                is_optitrack_related=is_optitrack,
                is_generated_analysis=is_analysis,
                csv_columns=csv_columns,
                csv_row_count=csv_row_count,
                json_kind=json_kind,
                anchor_count=anchor_count,
                notes=notes,
            )
        )
    return rows


def write_inventory(rows: list[InventoryRow], processed_dir: Path) -> None:
    processed_dir.mkdir(parents=True, exist_ok=True)
    csv_path = processed_dir / "raw_inventory.csv"
    json_path = processed_dir / "raw_inventory.json"
    fieldnames = list(asdict(rows[0]).keys()) if rows else list(InventoryRow.__dataclass_fields__)

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump([asdict(row) for row in rows], handle, indent=2)
        handle.write("\n")


def build_manifest(rows: list[InventoryRow], raw_root: Path) -> list[CaptureManifest]:
    by_capture: dict[str, list[InventoryRow]] = defaultdict(list)
    for row in rows:
        by_capture[row.capture_id].append(row)

    manifests: list[CaptureManifest] = []
    for capture_id in sorted(by_capture):
        capture_rows = by_capture[capture_id]
        category_counts = Counter(row.category for row in capture_rows)
        role_counts = Counter(row.data_role for row in capture_rows)
        manifest = CaptureManifest(
            capture_id=capture_id,
            root=(raw_root / capture_id).as_posix(),
            file_count=len(capture_rows),
            total_size_bytes=sum(row.size_bytes for row in capture_rows),
            categories=dict(sorted(category_counts.items())),
            data_roles=dict(sorted(role_counts.items())),
        )

        for row in capture_rows:
            if row.category == "layout":
                manifest.layout_files.append(row.rel_path)
            if row.category == "validation_table":
                manifest.validation_tables.append(row.rel_path)
            if row.category == "dop_table":
                manifest.dop_tables.append(row.rel_path)
            if row.category == "residual_table":
                manifest.residual_tables.append(row.rel_path)
            if row.category == "static_table":
                manifest.static_tables.append(row.rel_path)
            if row.category == "roto_table":
                manifest.roto_tables.append(row.rel_path)
            if row.category == "wand_table":
                manifest.wand_tables.append(row.rel_path)
            if row.is_optitrack_related:
                manifest.optitrack_files.append(row.rel_path)
            if row.category in {"report_or_note", "figure"}:
                manifest.report_files.append(row.rel_path)
            if row.category == "metadata":
                manifest.metadata_files.append(row.rel_path)

        manifest.has_optitrack = bool(manifest.optitrack_files)
        manifest.has_static = bool(manifest.static_tables)
        manifest.has_roto = bool(manifest.roto_tables)
        manifest.has_wand = bool(manifest.wand_tables)
        manifest.has_dop = bool(manifest.dop_tables)
        manifest.has_layouts = bool(manifest.layout_files)

        if capture_id == "28052026_Erlangen_Official":
            manifest.notes.append("official optical validation capture")
        if manifest.has_layouts and (manifest.has_static or manifest.has_roto or manifest.has_wand):
            manifest.notes.append("full_process_candidate")
        if manifest.has_dop and manifest.has_optitrack:
            manifest.notes.append("dop_and_ground_truth_available")

        manifests.append(manifest)
    return manifests


def write_manifest(manifests: list[CaptureManifest], processed_dir: Path) -> None:
    path = processed_dir / "capture_manifest.json"
    payload: dict[str, Any] = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "captures": [asdict(item) for item in manifests],
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def fmt_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GB"


def write_report(rows: list[InventoryRow], manifests: list[CaptureManifest], report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "raw_inventory.md"
    total_size = sum(row.size_bytes for row in rows)
    category_counts = Counter(row.category for row in rows)
    role_counts = Counter(row.data_role for row in rows)

    lines = [
        "# Raw Capture Inventory",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Captures: `{len(manifests)}`",
        f"- Files: `{len(rows)}`",
        f"- Total size: `{fmt_bytes(total_size)}`",
        "",
        "## Captures",
        "",
        "| Capture | Size | Files | Layouts | DOP | Validation | Static | Roto | Wand | Notes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]

    for manifest in manifests:
        notes = ", ".join(manifest.notes)
        lines.append(
            "| "
            + " | ".join(
                [
                    manifest.capture_id,
                    fmt_bytes(manifest.total_size_bytes),
                    str(manifest.file_count),
                    str(len(manifest.layout_files)),
                    str(len(manifest.dop_tables)),
                    str(len(manifest.validation_tables)),
                    str(len(manifest.static_tables)),
                    str(len(manifest.roto_tables)),
                    str(len(manifest.wand_tables)),
                    notes,
                ]
            )
            + " |"
        )

    lines.extend(["", "## Category Counts", ""])
    for category, count in sorted(category_counts.items()):
        lines.append(f"- `{category}`: {count}")

    lines.extend(["", "## Data Role Counts", ""])
    for role, count in sorted(role_counts.items()):
        lines.append(f"- `{role}`: {count}")

    lines.extend(["", "## Key Files By Capture", ""])
    for manifest in manifests:
        lines.extend([f"### {manifest.capture_id}", ""])
        for label, values in [
            ("layouts", manifest.layout_files),
            ("dop tables", manifest.dop_tables),
            ("validation tables", manifest.validation_tables),
            ("residual tables", manifest.residual_tables),
            ("static tables", manifest.static_tables),
            ("roto tables", manifest.roto_tables),
            ("wand tables", manifest.wand_tables),
            ("metadata", manifest.metadata_files),
        ]:
            lines.append(f"- {label}: `{len(values)}`")
            for rel_path in values[:8]:
                lines.append(f"  - `{rel_path}`")
            if len(values) > 8:
                lines.append(f"  - ... {len(values) - 8} more")
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if not args.raw_root.exists():
        raise SystemExit(f"raw root not found: {args.raw_root}")

    rows = build_inventory(args)
    manifests = build_manifest(rows, args.raw_root)
    write_inventory(rows, args.processed_dir)
    write_manifest(manifests, args.processed_dir)
    write_report(rows, manifests, args.report_dir)

    print(f"files={len(rows)} captures={len(manifests)}")
    print(f"wrote {args.processed_dir / 'raw_inventory.csv'}")
    print(f"wrote {args.processed_dir / 'raw_inventory.json'}")
    print(f"wrote {args.processed_dir / 'capture_manifest.json'}")
    print(f"wrote {args.report_dir / 'raw_inventory.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
