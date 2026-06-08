#!/usr/bin/env python3
"""Build an ML-ready candidate table without training a model.

The table is intentionally explicit about label quality. With the current data,
real OptiTrack labels are validation-only and too sparse for GPU/deep training.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LAYOUT_FEATURES = Path("DATASETS/features/layout_features.csv")
SCORES_V2 = Path("DATASETS/features/layout_scores_v2.csv")
DOP_SUMMARY = Path("DATASETS/features/dop_summary_by_layout.csv")
OPTI_VALIDATION = Path("DATASETS/features/optitrack_layout_validation.csv")
CAPTURE_METADATA = Path("DATASETS/processed/capture_metadata.csv")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout-features", type=Path, default=LAYOUT_FEATURES)
    parser.add_argument("--scores-v2", type=Path, default=SCORES_V2)
    parser.add_argument("--dop-summary", type=Path, default=DOP_SUMMARY)
    parser.add_argument("--opti-validation", type=Path, default=OPTI_VALIDATION)
    parser.add_argument("--capture-metadata", type=Path, default=CAPTURE_METADATA)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def choose_dop(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    by_layout: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("stress_variant") == "baseline" and row.get("mask") == "all8" and row.get("table_family") == "by_session":
            by_layout[row["layout_id"]].append(row)
    out: dict[str, dict[str, str]] = {}
    for layout_id, items in by_layout.items():
        items.sort(key=lambda row: (0 if row.get("grid_mm") == "25.0" else 1, row.get("grid_mm", "")))
        out[layout_id] = items[0]
    return out


def build_table(
    features: list[dict[str, str]],
    scores: list[dict[str, str]],
    dop_rows: list[dict[str, str]],
    opti_rows: list[dict[str, str]],
    capture_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    score_by_layout = {row["layout_id"]: row for row in scores}
    dop_by_layout = choose_dop(dop_rows)
    opti_all8 = {row["layout_id"]: row for row in opti_rows if row.get("eval_set") == "all8" and row.get("layout_id")}
    opti_noG = {row["layout_id"]: row for row in opti_rows if row.get("eval_set") == "noG" and row.get("layout_id")}
    capture_by_id = {row["capture_id"]: row for row in capture_rows if row.get("capture_id")}

    rows: list[dict[str, Any]] = []
    for feat in features:
        layout_id = feat["layout_id"]
        capture_id = feat.get("capture_id", "")
        score = score_by_layout.get(layout_id, {})
        dop = dop_by_layout.get(layout_id, {})
        opti = opti_all8.get(layout_id, {})
        opti_drop = opti_noG.get(layout_id, {})
        capture = capture_by_id.get(capture_id, {})

        has_real_label = bool(opti)
        has_proxy_label = bool(feat.get("eval_match") == "True")
        has_dop = bool(dop)
        if has_real_label:
            label_quality = "real_optitrack_sparse_validation_only"
            train_allowed = "false"
            validation_allowed = "true"
            recommended_use = "calibration_validation"
        elif has_proxy_label:
            label_quality = "proxy_existing_field_evaluation"
            train_allowed = "false"
            validation_allowed = "false"
            recommended_use = "ranking_and_proxy_analysis"
        elif capture.get("label_quality") == "multipath_unlabeled_no_tag":
            label_quality = "multipath_unlabeled_no_tag"
            train_allowed = "false"
            validation_allowed = "false"
            recommended_use = "multipath_risk_analysis"
        else:
            label_quality = "unlabeled_geometry_only"
            train_allowed = "false"
            validation_allowed = "false"
            recommended_use = "candidate_generation_only"

        rows.append(
            {
                "layout_id": layout_id,
                "capture_id": capture_id,
                "capture_environment_type": capture.get("environment_type", ""),
                "capture_condition": capture.get("condition", ""),
                "capture_label_quality": capture.get("label_quality", ""),
                "capture_has_tag_capture": capture.get("has_tag_capture", ""),
                "capture_has_ground_truth": capture.get("has_ground_truth", ""),
                "capture_no_tag_multipath_usable": capture.get("no_tag_multipath_usable", ""),
                "source_group": feat.get("source_group", ""),
                "solver_version": feat.get("solver_version", ""),
                "layout_variant": feat.get("layout_variant", ""),
                "anchor_count": feat.get("anchor_count", ""),
                "train_allowed": train_allowed,
                "validation_allowed": validation_allowed,
                "label_quality": label_quality,
                "recommended_use": recommended_use,
                "has_real_optitrack_label": str(has_real_label).lower(),
                "has_proxy_evaluation_label": str(has_proxy_label).lower(),
                "has_bound_dop": str(has_dop).lower(),
                "production_score_v2": score.get("production_score_v2", ""),
                "score_v2_rank": score.get("group_rank_v2", ""),
                "score_confidence": score.get("confidence", ""),
                "optitrack_score": score.get("optitrack_validation_score", ""),
                "optitrack_rank": score.get("validation_rank", ""),
                "target_opti_all8_3d_median_mm": opti.get("opti_err_3d_median_mm", ""),
                "target_opti_all8_3d_p95_mm": opti.get("opti_err_3d_p95_mm", ""),
                "target_opti_all8_3d_rms_mm": opti.get("opti_err_3d_rms_mm", ""),
                "target_opti_all8_vertical_median_mm": opti.get("opti_err_vertical_median_mm", ""),
                "target_opti_noG_3d_rms_mm": opti_drop.get("opti_err_3d_rms_mm", ""),
                "proxy_autopos_rms_mm": feat.get("eval_autopos_rms_mm", ""),
                "proxy_autopos_p95_mm": feat.get("eval_autopos_p95_mm", ""),
                "proxy_static_p95_mm": feat.get("eval_static_p95_mm", ""),
                "proxy_roto_abs_deltaR_p95_mm": feat.get("eval_roto_abs_deltaR_p95_mm", ""),
                "feature_x_span_mm": feat.get("x_span_mm", ""),
                "feature_y_span_mm": feat.get("y_span_mm", ""),
                "feature_z_span_mm": feat.get("z_span_mm", ""),
                "feature_xy_hull_coverage_ratio": feat.get("xy_hull_coverage_ratio", ""),
                "feature_paired_xy_offset_mean_mm": feat.get("paired_xy_offset_mean_mm", ""),
                "feature_paired_vertical_gap_mean_mm": feat.get("paired_vertical_gap_mean_mm", ""),
                "feature_paired_vertical_gap_std_mm": feat.get("paired_vertical_gap_std_mm", ""),
                "feature_anchor_delay_span_mm": feat.get("anchor_delay_span_mm", ""),
                "feature_risk_flag_count": feat.get("risk_flag_count", ""),
                "dop_grid_mm": dop.get("grid_mm", ""),
                "dop_gdop_mean": dop.get("gdop_mean", ""),
                "dop_gdop_p95": dop.get("gdop_p95", ""),
                "dop_vdop_mean": dop.get("vdop_mean", ""),
                "dop_vdop_p95": dop.get("vdop_p95", ""),
                "dop_cond_p95": dop.get("cond_p95", ""),
                "dop_bad_gdop_ratio": dop.get("bad_gdop_gt_1p2_ratio", ""),
                "source_path": feat.get("source_path", ""),
            }
        )
    return rows


def write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row["label_quality"]] += 1
    real = [row for row in rows if row["has_real_optitrack_label"] == "true"]
    no_tag_multipath = [row for row in rows if row["label_quality"] == "multipath_unlabeled_no_tag"]
    train_allowed = [row for row in rows if row["train_allowed"] == "true"]
    lines = [
        "# ML Candidate Table Readiness",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Candidate rows: `{len(rows)}`",
        f"- Real OptiTrack labeled layouts: `{len(real)}`",
        f"- No-tag multipath layout rows: `{len(no_tag_multipath)}`",
        f"- Train-allowed rows: `{len(train_allowed)}`",
        "",
        "## Label Quality Counts",
        "",
    ]
    for key, count in sorted(counts.items()):
        lines.append(f"- `{key}`: {count}")
    lines.extend(["", "## Bewertung", ""])
    lines.append("- The table is ML-ready in schema, but not training-ready in data volume.")
    lines.append("- Real labels are only 5 layouts from one OptiTrack environment, so they are validation/calibration only.")
    lines.append("- No-tag multipath rows are usable for risk analysis, not supervised error labels.")
    lines.append("- Proxy labels from field summaries can support ranking analysis, not supervised generalization claims.")
    lines.append("- GPU training is not justified yet; collect more real labeled captures first or use CPU-only exploratory models.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    rows = build_table(
        read_csv(args.layout_features),
        read_csv(args.scores_v2),
        read_csv(args.dop_summary),
        read_csv(args.opti_validation),
        read_csv(args.capture_metadata),
    )
    write_csv(args.feature_dir / "ml_candidate_table.csv", rows)
    write_report(args.report_dir / "ml_candidate_table_readiness.md", rows)
    print(f"ml_candidate_rows={len(rows)} train_allowed={sum(1 for row in rows if row['train_allowed'] == 'true')}")
    print(f"wrote {args.feature_dir / 'ml_candidate_table.csv'}")
    print(f"wrote {args.report_dir / 'ml_candidate_table_readiness.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
