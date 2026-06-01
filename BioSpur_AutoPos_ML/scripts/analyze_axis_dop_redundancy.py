#!/usr/bin/env python3
"""Analyze dense axis-DOP redundancy summaries."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INPUT = Path("DATASETS/features/axis_dop_gpu_dense_25mm_all8_dropA-H.csv")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--output-prefix", default="axis_dop_redundancy")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def as_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out if math.isfinite(out) else float("nan")


def fmt(value: Any) -> str:
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return f"{value:.9g}"
    return "" if value is None else str(value)


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
            writer.writerow({key: fmt(value) for key, value in row.items()})


def analyze(rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_layout: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in rows:
        if row.get("status") != "ok":
            continue
        by_layout[row["layout_id"]][row["mask"]] = row

    layout_rows: list[dict[str, Any]] = []
    for layout_id, masks in sorted(by_layout.items()):
        base = masks.get("all8")
        drops = [row for mask, row in masks.items() if mask.startswith("drop") and len(mask) == 5]
        if not base or not drops:
            continue
        worst_score = max(drops, key=lambda row: as_float(row["axis_dop_score"]))
        worst_vdop = max(drops, key=lambda row: as_float(row["vdop_p95"]))
        worst_gdop = max(drops, key=lambda row: as_float(row["gdop_p95"]))
        worst_cond = max(drops, key=lambda row: as_float(row["cond_p95"]))
        row = {
            "layout_id": layout_id,
            "capture_id": base["capture_id"],
            "source_group": base["source_group"],
            "solver_version": base["solver_version"],
            "layout_variant": base["layout_variant"],
            "source_path": base["source_path"],
            "all8_score": as_float(base["axis_dop_score"]),
            "all8_xdop_p95": as_float(base["xdop_p95"]),
            "all8_ydop_p95": as_float(base["ydop_p95"]),
            "all8_vdop_p95": as_float(base["vdop_p95"]),
            "all8_gdop_p95": as_float(base["gdop_p95"]),
            "worst_drop_score_mask": worst_score["mask"],
            "worst_drop_score": as_float(worst_score["axis_dop_score"]),
            "worst_drop_score_vdop_p95": as_float(worst_score["vdop_p95"]),
            "worst_drop_score_gdop_p95": as_float(worst_score["gdop_p95"]),
            "worst_vdop_mask": worst_vdop["mask"],
            "worst_vdop_p95": as_float(worst_vdop["vdop_p95"]),
            "worst_gdop_mask": worst_gdop["mask"],
            "worst_gdop_p95": as_float(worst_gdop["gdop_p95"]),
            "worst_cond_mask": worst_cond["mask"],
            "worst_cond_p95": as_float(worst_cond["cond_p95"]),
        }
        row["score_degradation_ratio"] = row["worst_drop_score"] / row["all8_score"] if row["all8_score"] > 0 else float("nan")
        row["vdop_degradation_ratio"] = row["worst_vdop_p95"] / row["all8_vdop_p95"] if row["all8_vdop_p95"] > 0 else float("nan")
        layout_rows.append(row)

    group_rows: list[dict[str, Any]] = []
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in layout_rows:
        by_group[row["source_group"]].append(row)
    for group, items in sorted(by_group.items()):
        best_worst = min(items, key=lambda row: row["worst_drop_score"])
        best_all8 = min(items, key=lambda row: row["all8_score"])
        worst_layout = max(items, key=lambda row: row["worst_drop_score"])
        group_rows.append(
            {
                "source_group": group,
                "layout_count": len(items),
                "best_robust_layout_id": best_worst["layout_id"],
                "best_robust_version": best_worst["solver_version"],
                "best_robust_variant": best_worst["layout_variant"],
                "best_robust_worst_drop_score": best_worst["worst_drop_score"],
                "best_robust_worst_drop_mask": best_worst["worst_drop_score_mask"],
                "best_robust_worst_vdop_p95": best_worst["worst_vdop_p95"],
                "best_all8_layout_id": best_all8["layout_id"],
                "best_all8_version": best_all8["solver_version"],
                "best_all8_variant": best_all8["layout_variant"],
                "best_all8_score": best_all8["all8_score"],
                "worst_layout_id": worst_layout["layout_id"],
                "worst_version": worst_layout["solver_version"],
                "worst_variant": worst_layout["layout_variant"],
                "worst_drop_score": worst_layout["worst_drop_score"],
                "worst_drop_mask": worst_layout["worst_drop_score_mask"],
            }
        )
    return layout_rows, group_rows


def write_report(path: Path, layout_rows: list[dict[str, Any]], group_rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Axis DOP Redundancy Analysis",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Layouts analyzed: `{len(layout_rows)}`",
        f"- Source groups: `{len(group_rows)}`",
        "- Input masks: `all8` plus every single-anchor drop `dropA`...`dropH`.",
        "- Lower score/DOP is better. Worst-drop columns mean the most fragile single-anchor outage.",
        "",
        "## Best Robust Layout Per Group",
        "",
        "| Group | Best Robust Version | Variant | Worst Drop | Worst Score | Worst VDOP p95 | Best all8 Version | Worst Layout |",
        "|---|---|---|---|---:|---:|---|---|",
    ]
    for row in group_rows:
        lines.append(
            f"| `{row['source_group']}` | `{row['best_robust_version']}` | `{row['best_robust_variant']}` | "
            f"`{row['best_robust_worst_drop_mask']}` | {row['best_robust_worst_drop_score']:.3f} | "
            f"{row['best_robust_worst_vdop_p95']:.3f} | `{row['best_all8_version']}` | "
            f"`{row['worst_version']}:{row['worst_variant']}` |"
        )
    lines.extend(
        [
            "",
            "## Most Fragile Layouts Overall",
            "",
            "| Capture | Group | Version | Variant | Worst Drop | Worst Score | all8 Score | Score Ratio | Worst VDOP p95 |",
            "|---|---|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in sorted(layout_rows, key=lambda item: item["worst_drop_score"], reverse=True)[:20]:
        lines.append(
            f"| `{row['capture_id']}` | `{row['source_group']}` | `{row['solver_version']}` | `{row['layout_variant']}` | "
            f"`{row['worst_drop_score_mask']}` | {row['worst_drop_score']:.3f} | {row['all8_score']:.3f} | "
            f"{row['score_degradation_ratio']:.3f} | {row['worst_vdop_p95']:.3f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    layout_rows, group_rows = analyze(read_csv(args.input))
    write_csv(args.feature_dir / f"{args.output_prefix}_by_layout.csv", layout_rows)
    write_csv(args.feature_dir / f"{args.output_prefix}_by_group.csv", group_rows)
    write_report(args.report_dir / f"{args.output_prefix}.md", layout_rows, group_rows)
    print(f"layouts={len(layout_rows)} groups={len(group_rows)}")
    print(f"wrote {args.feature_dir / f'{args.output_prefix}_by_layout.csv'}")
    print(f"wrote {args.feature_dir / f'{args.output_prefix}_by_group.csv'}")
    print(f"wrote {args.report_dir / f'{args.output_prefix}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
