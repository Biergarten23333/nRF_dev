#!/usr/bin/env python3
"""Generate a current pipeline summary from produced tables."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


PROCESSED_DIR = Path("DATASETS/processed")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def read_manifest(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("captures", []))


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle)


def csv_rows(path: Path) -> int:
    line_count = count_lines(path)
    return max(0, line_count - 1)


def bool_true(value: str) -> bool:
    return str(value).strip().lower() == "true"


def counter_lines(counter: Counter[str]) -> list[str]:
    return [f"- `{key}`: `{counter[key]}`" for key in sorted(counter)]


def main() -> int:
    args = parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=True)

    manifest = read_manifest(args.processed_dir / "capture_manifest.json")
    capture_metadata = read_csv(args.processed_dir / "capture_metadata.csv")
    layout_features = read_csv(args.feature_dir / "layout_features.csv")
    deployment = read_csv(args.feature_dir / "deployment_recommendation_matrix.csv")
    ml_candidates = read_csv(args.feature_dir / "ml_candidate_table.csv")

    capture_label_counts = Counter(row.get("label_quality", "") for row in capture_metadata)
    capture_env_counts = Counter(row.get("environment_type", "") for row in capture_metadata)
    deploy_counts = Counter(row.get("deploy_class", "") for row in deployment)
    ml_label_counts = Counter(row.get("label_quality", "") for row in ml_candidates)

    total_raw_files = sum(int(item.get("file_count", 0) or 0) for item in manifest)
    no_tag_multipath = [row for row in capture_metadata if bool_true(row.get("no_tag_multipath_usable", ""))]
    train_allowed = [row for row in ml_candidates if bool_true(row.get("train_allowed", ""))]
    real_gt_layouts = [row for row in ml_candidates if row.get("label_quality") == "real_optitrack_sparse_validation_only"]

    lines = [
        "# AutoPos Layout Evaluation Pipeline Summary",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Scope",
        "",
        "This is a deterministic layout evaluation and deployment-screening pipeline.",
        "No model training is started by the CPU pipeline.",
        "",
        "GPU policy remains unchanged: GPU0 can be used only for an explicitly justified future training job; GPU1 is reserved and must not be touched.",
        "",
        "## Current Inputs",
        "",
        f"- Raw capture groups: `{len(manifest)}`",
        f"- Raw files inventoried: `{total_raw_files}`",
        f"- Canonical layout files: `{csv_rows(args.processed_dir / 'layout_index.csv')}`",
        f"- Capture metadata rows: `{len(capture_metadata)}`",
        f"- No-tag multipath captures: `{len(no_tag_multipath)}`",
        "",
        "## Generated Tables",
        "",
        f"- `DATASETS/processed/raw_inventory.csv`: `{csv_rows(args.processed_dir / 'raw_inventory.csv')}` rows",
        f"- `DATASETS/processed/layout_index.csv`: `{csv_rows(args.processed_dir / 'layout_index.csv')}` rows",
        f"- `DATASETS/processed/capture_metadata.csv`: `{len(capture_metadata)}` rows",
        f"- `DATASETS/features/layout_features.csv`: `{len(layout_features)}` rows",
        f"- `DATASETS/features/axis_dop_system_evaluation_by_layout.csv`: `{csv_rows(args.feature_dir / 'axis_dop_system_evaluation_by_layout.csv')}` rows",
        f"- `DATASETS/features/deployment_recommendation_matrix.csv`: `{len(deployment)}` rows",
        f"- `DATASETS/features/ml_candidate_table.csv`: `{len(ml_candidates)}` rows",
        "",
        "## Capture Label Quality",
        "",
    ]
    lines.extend(counter_lines(capture_label_counts) or ["- none"])
    lines.extend(["", "## Capture Environments", ""])
    lines.extend(counter_lines(capture_env_counts) or ["- none"])
    lines.extend(["", "## Deployment Class Distribution", ""])
    for key in ("A", "B", "C", "D"):
        lines.append(f"- `{key}`: `{deploy_counts[key]}`")
    lines.extend(["", "## ML Candidate Label Quality", ""])
    lines.extend(counter_lines(ml_label_counts) or ["- none"])
    lines.extend(
        [
            "",
            "## Training Gate",
            "",
            f"- Real OptiTrack layout labels: `{len(real_gt_layouts)}`",
            f"- Train-allowed rows: `{len(train_allowed)}`",
            "- Current decision: do not start GPU training.",
            "",
            "Reason: real labels are still sparse and environment-limited. Proxy and no-tag multipath rows are valuable for ranking, robustness, and risk analysis, but they are not supervised localization-error labels.",
            "",
            "## Immediate Engineering Work",
            "",
            "1. Use `capture_metadata_overrides.csv` to label future NLOS, basement, and outdoor captures before rerunning the pipeline.",
            "2. Keep full-anchor ranking and degraded-anchor deployment screening as separate decisions.",
            "3. Treat no-tag multipath captures as residual/risk evidence, not ground-truth model targets.",
            "4. Add new true-position captures before considering ML training.",
        ]
    )
    path = args.report_dir / "pipeline_summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
