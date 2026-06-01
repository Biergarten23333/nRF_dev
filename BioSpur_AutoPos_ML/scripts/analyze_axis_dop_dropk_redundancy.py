#!/usr/bin/env python3
"""Analyze exhaustive drop-k axis-DOP summaries.

This is meant for the expensive dense run produced by:
  compute_axis_dop_gpu_batch.py --grid-mm 25 --masks drop1-4

The key output is the drop4 / surviving-four-anchor worst case per layout.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DROP_INPUT = Path("DATASETS/features/axis_dop_gpu_dense_25mm_drop1-4.csv")
BASE_INPUT = Path("DATASETS/features/axis_dop_gpu_dense_25mm_all8_dropA-H.csv")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drop-input", type=Path, default=DROP_INPUT)
    parser.add_argument("--base-input", type=Path, default=BASE_INPUT)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--output-prefix", default="axis_dop_drop1-4_redundancy")
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


def drop_count(row: dict[str, str]) -> int:
    try:
        anchor_count = int(float(row.get("anchor_count", "nan")))
        mask_anchor_count = int(float(row.get("mask_anchor_count", "nan")))
    except ValueError:
        return 0
    return max(0, anchor_count - mask_anchor_count)


def mask_labels(mask: str) -> str:
    return mask[4:] if mask.startswith("drop") else ""


def analyze(
    drop_rows: list[dict[str, str]],
    base_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base_by_layout = {
        row["layout_id"]: row
        for row in base_rows
        if row.get("status") == "ok" and row.get("mask") == "all8"
    }

    drops_by_layout: dict[str, dict[int, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for row in drop_rows:
        if row.get("status") != "ok":
            continue
        count = drop_count(row)
        if 1 <= count <= 4:
            drops_by_layout[row["layout_id"]][count].append(row)

    layout_rows: list[dict[str, Any]] = []
    for layout_id, by_count in sorted(drops_by_layout.items()):
        base = base_by_layout.get(layout_id)
        if not base or 4 not in by_count:
            continue

        out: dict[str, Any] = {
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
        }

        for count in range(1, 5):
            rows = by_count.get(count, [])
            if not rows:
                continue
            worst_score = max(rows, key=lambda row: as_float(row["axis_dop_score"]))
            worst_vdop = max(rows, key=lambda row: as_float(row["vdop_p95"]))
            prefix = f"drop{count}"
            out[f"{prefix}_mask_count"] = len(rows)
            out[f"{prefix}_worst_score_mask"] = worst_score["mask"]
            out[f"{prefix}_worst_score_dropped"] = mask_labels(worst_score["mask"])
            out[f"{prefix}_worst_score"] = as_float(worst_score["axis_dop_score"])
            out[f"{prefix}_worst_score_vdop_p95"] = as_float(worst_score["vdop_p95"])
            out[f"{prefix}_worst_score_gdop_p95"] = as_float(worst_score["gdop_p95"])
            out[f"{prefix}_worst_vdop_mask"] = worst_vdop["mask"]
            out[f"{prefix}_worst_vdop_dropped"] = mask_labels(worst_vdop["mask"])
            out[f"{prefix}_worst_vdop_p95"] = as_float(worst_vdop["vdop_p95"])
            out[f"{prefix}_score_ratio_vs_all8"] = (
                out[f"{prefix}_worst_score"] / out["all8_score"] if out["all8_score"] > 0 else float("nan")
            )
            out[f"{prefix}_vdop_ratio_vs_all8"] = (
                out[f"{prefix}_worst_vdop_p95"] / out["all8_vdop_p95"] if out["all8_vdop_p95"] > 0 else float("nan")
            )
        layout_rows.append(out)

    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in layout_rows:
        by_group[row["source_group"]].append(row)

    group_rows: list[dict[str, Any]] = []
    for group, items in sorted(by_group.items()):
        best_surviving4 = min(items, key=lambda row: row["drop4_worst_score"])
        worst_surviving4 = max(items, key=lambda row: row["drop4_worst_score"])
        group_rows.append(
            {
                "source_group": group,
                "layout_count": len(items),
                "best_surviving4_layout_id": best_surviving4["layout_id"],
                "best_surviving4_version": best_surviving4["solver_version"],
                "best_surviving4_variant": best_surviving4["layout_variant"],
                "best_surviving4_worst_mask": best_surviving4["drop4_worst_score_mask"],
                "best_surviving4_worst_score": best_surviving4["drop4_worst_score"],
                "best_surviving4_worst_vdop_p95": best_surviving4["drop4_worst_vdop_p95"],
                "worst_surviving4_layout_id": worst_surviving4["layout_id"],
                "worst_surviving4_version": worst_surviving4["solver_version"],
                "worst_surviving4_variant": worst_surviving4["layout_variant"],
                "worst_surviving4_mask": worst_surviving4["drop4_worst_score_mask"],
                "worst_surviving4_score": worst_surviving4["drop4_worst_score"],
                "worst_surviving4_vdop_p95": worst_surviving4["drop4_worst_vdop_p95"],
            }
        )
    return layout_rows, group_rows


def write_report(path: Path, layout_rows: list[dict[str, Any]], group_rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Axis DOP Drop1-4 Redundancy Analysis",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Layouts analyzed: `{len(layout_rows)}`",
        f"- Source groups: `{len(group_rows)}`",
        "- Input masks: exhaustive `drop1`, `drop2`, `drop3`, and `drop4` combinations.",
        "- `drop4` is the surviving-four-anchor case: each row keeps only four anchors.",
        "- Lower score/DOP is better. Worst columns mean the most fragile outage combination.",
        "",
        "## Best Surviving-4 Layout Per Group",
        "",
        "| Group | Best Version | Variant | Worst Drop4 | Worst Score | Worst VDOP p95 | Worst Layout | Worst Layout Score |",
        "|---|---|---|---|---:|---:|---|---:|",
    ]
    for row in group_rows:
        lines.append(
            f"| `{row['source_group']}` | `{row['best_surviving4_version']}` | "
            f"`{row['best_surviving4_variant']}` | `{row['best_surviving4_worst_mask']}` | "
            f"{row['best_surviving4_worst_score']:.3f} | {row['best_surviving4_worst_vdop_p95']:.3f} | "
            f"`{row['worst_surviving4_version']}:{row['worst_surviving4_variant']}` | "
            f"{row['worst_surviving4_score']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## Most Fragile Surviving-4 Layouts Overall",
            "",
            "| Capture | Group | Version | Variant | Worst Drop4 | Worst Score | all8 Score | Score Ratio | Worst VDOP p95 |",
            "|---|---|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in sorted(layout_rows, key=lambda item: item["drop4_worst_score"], reverse=True)[:25]:
        lines.append(
            f"| `{row['capture_id']}` | `{row['source_group']}` | `{row['solver_version']}` | "
            f"`{row['layout_variant']}` | `{row['drop4_worst_score_mask']}` | "
            f"{row['drop4_worst_score']:.3f} | {row['all8_score']:.3f} | "
            f"{row['drop4_score_ratio_vs_all8']:.3f} | {row['drop4_worst_vdop_p95']:.3f} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    layout_rows, group_rows = analyze(read_csv(args.drop_input), read_csv(args.base_input))
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
