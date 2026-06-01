#!/usr/bin/env python3
"""Create a system-level axis-DOP evaluation for the current anchor layouts."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DROP_INPUT = Path("DATASETS/features/axis_dop_gpu_dense_25mm_drop1-4.csv")
BASE_INPUT = Path("DATASETS/features/axis_dop_gpu_dense_25mm_all8_dropA-H.csv")
LAYOUT_DB = Path("DATASETS/processed/layout_database.jsonl")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drop-input", type=Path, default=DROP_INPUT)
    parser.add_argument("--base-input", type=Path, default=BASE_INPUT)
    parser.add_argument("--layout-db", type=Path, default=LAYOUT_DB)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--output-prefix", default="axis_dop_system_evaluation")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def read_layout_db(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            layout_id = row.get("layout_id")
            if layout_id:
                rows[str(layout_id)] = row
    return rows


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
    return int(as_float(row["anchor_count"]) - as_float(row["mask_anchor_count"]))


def dropped_labels(mask: str) -> str:
    return "".join(ch for ch in mask.removeprefix("drop") if ch.isalpha()).upper()


def anchor_records(layout: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for idx, anchor in enumerate(layout.get("anchors", [])):
        try:
            records.append(
                {
                    "label": str(anchor.get("label", chr(ord("A") + idx))).upper(),
                    "x": float(anchor["x_mm"]),
                    "y": float(anchor["y_mm"]),
                    "z": float(anchor["z_mm"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return records


def span(values: list[float]) -> float:
    if not values:
        return float("nan")
    return max(values) - min(values)


def convex_hull_area(points: list[tuple[float, float]]) -> float:
    unique = sorted(set(points))
    if len(unique) < 3:
        return 0.0

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = lower[:-1] + upper[:-1]
    area = 0.0
    for idx, point in enumerate(hull):
        nxt = hull[(idx + 1) % len(hull)]
        area += point[0] * nxt[1] - nxt[0] * point[1]
    return abs(area) / 2.0


def geometry(records: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "x_span_mm": span([record["x"] for record in records]),
        "y_span_mm": span([record["y"] for record in records]),
        "z_span_mm": span([record["z"] for record in records]),
        "xy_hull_area_m2": convex_hull_area([(record["x"], record["y"]) for record in records]) / 1_000_000.0,
    }


def ratio(num: float, den: float) -> float:
    if not math.isfinite(num) or not math.isfinite(den) or abs(den) < 1e-12:
        return float("nan")
    return num / den


def weakness_level(score: float) -> str:
    if score >= 20:
        return "critical"
    if score >= 16:
        return "high"
    if score >= 12:
        return "medium"
    return "acceptable"


def axis_diagnosis(drop_row: dict[str, str], base_row: dict[str, str]) -> dict[str, Any]:
    axes = {
        "X": (as_float(drop_row["xdop_p95"]), as_float(base_row["xdop_p95"])),
        "Y": (as_float(drop_row["ydop_p95"]), as_float(base_row["ydop_p95"])),
        "Z": (as_float(drop_row["vdop_p95"]), as_float(base_row["vdop_p95"])),
    }
    abs_axis = max(axes, key=lambda key: axes[key][0])
    ratio_axis = max(axes, key=lambda key: ratio(axes[key][0], axes[key][1]))
    return {
        "abs_worst_axis": abs_axis,
        "ratio_worst_axis": ratio_axis,
        "x_p95": axes["X"][0],
        "y_p95": axes["Y"][0],
        "z_p95": axes["Z"][0],
        "x_ratio": ratio(axes["X"][0], axes["X"][1]),
        "y_ratio": ratio(axes["Y"][0], axes["Y"][1]),
        "z_ratio": ratio(axes["Z"][0], axes["Z"][1]),
    }


def build_rows(
    drop_rows: list[dict[str, str]],
    base_rows: list[dict[str, str]],
    layouts: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    base_by_layout = {
        row["layout_id"]: row
        for row in base_rows
        if row.get("status") == "ok" and row.get("mask") == "all8"
    }
    drop4_by_layout: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in drop_rows:
        if row.get("status") == "ok" and drop_count(row) == 4:
            drop4_by_layout[row["layout_id"]].append(row)

    out: list[dict[str, Any]] = []
    for layout_id, rows in sorted(drop4_by_layout.items()):
        base = base_by_layout.get(layout_id)
        layout = layouts.get(layout_id)
        if not base or not layout:
            continue
        worst = max(rows, key=lambda row: as_float(row["axis_dop_score"]))
        dropped = dropped_labels(worst["mask"])
        records = anchor_records(layout)
        all_geo = geometry(records)
        keep_records = [record for record in records if record["label"] not in set(dropped)]
        keep_geo = geometry(keep_records)
        axis = axis_diagnosis(worst, base)
        row = {
            "layout_id": layout_id,
            "capture_id": base["capture_id"],
            "source_group": base["source_group"],
            "solver_version": base["solver_version"],
            "layout_variant": base["layout_variant"],
            "source_path": base["source_path"],
            "worst_drop4_mask": worst["mask"],
            "dropped_labels": dropped,
            "surviving_labels": "".join(record["label"] for record in keep_records),
            "risk_level": weakness_level(as_float(worst["axis_dop_score"])),
            "all8_score": as_float(base["axis_dop_score"]),
            "drop4_score": as_float(worst["axis_dop_score"]),
            "score_ratio": ratio(as_float(worst["axis_dop_score"]), as_float(base["axis_dop_score"])),
            "abs_worst_axis": axis["abs_worst_axis"],
            "ratio_worst_axis": axis["ratio_worst_axis"],
            "drop4_xdop_p95": axis["x_p95"],
            "drop4_ydop_p95": axis["y_p95"],
            "drop4_vdop_p95": axis["z_p95"],
            "xdop_ratio": axis["x_ratio"],
            "ydop_ratio": axis["y_ratio"],
            "vdop_ratio": axis["z_ratio"],
            "drop4_hdop_p95": as_float(worst["hdop_p95"]),
            "drop4_gdop_p95": as_float(worst["gdop_p95"]),
            "drop4_axis_imbalance_p95": as_float(worst["axis_imbalance_p95"]),
            "all_x_span_mm": all_geo["x_span_mm"],
            "all_y_span_mm": all_geo["y_span_mm"],
            "all_z_span_mm": all_geo["z_span_mm"],
            "all_xy_hull_area_m2": all_geo["xy_hull_area_m2"],
            "surviving_x_span_mm": keep_geo["x_span_mm"],
            "surviving_y_span_mm": keep_geo["y_span_mm"],
            "surviving_z_span_mm": keep_geo["z_span_mm"],
            "surviving_xy_hull_area_m2": keep_geo["xy_hull_area_m2"],
            "surviving_x_span_ratio": ratio(keep_geo["x_span_mm"], all_geo["x_span_mm"]),
            "surviving_y_span_ratio": ratio(keep_geo["y_span_mm"], all_geo["y_span_mm"]),
            "surviving_z_span_ratio": ratio(keep_geo["z_span_mm"], all_geo["z_span_mm"]),
            "surviving_xy_area_ratio": ratio(keep_geo["xy_hull_area_m2"], all_geo["xy_hull_area_m2"]),
        }
        out.append(row)
    return out


def quantile(values: list[float], q: float) -> float:
    values = sorted(value for value in values if math.isfinite(value))
    if not values:
        return float("nan")
    idx = (len(values) - 1) * q
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - idx) + values[hi] * (idx - lo)


def fmt_num(value: Any, digits: int = 2) -> str:
    value = as_float(value)
    if not math.isfinite(value):
        return ""
    return f"{value:.{digits}f}"


def write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    scores = [as_float(row["drop4_score"]) for row in rows]
    ratios = [as_float(row["score_ratio"]) for row in rows]
    levels = Counter(row["risk_level"] for row in rows)
    masks = Counter(row["worst_drop4_mask"] for row in rows)
    abs_axes = Counter(row["abs_worst_axis"] for row in rows)
    ratio_axes = Counter(row["ratio_worst_axis"] for row in rows)
    survivors = Counter(row["surviving_labels"] for row in rows)

    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_group[row["source_group"]].append(row)
    group_best = [min(items, key=lambda row: as_float(row["drop4_score"])) for items in by_group.values()]
    group_worst = [max(items, key=lambda row: as_float(row["drop4_score"])) for items in by_group.values()]

    lines = [
        "# 这套系统的 Surviving-4 Axis-DOP 大评估",
        "",
        f"生成时间: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## 结论先说",
        "",
        "- 这不是通用 UWB 结论；这是针对当前 `A-H` 编号、当前 anchor 坐标、当前 117 个 layout 的评估。",
        "- 全 8 anchor 的 DOP 不能代表冗余能力；真正的问题在 `drop4`，也就是只剩 4 个 anchor 的最坏几何。",
        f"- `drop4` score 中位数 `{fmt_num(quantile(scores, 0.50))}`，p90 `{fmt_num(quantile(scores, 0.90))}`，p95 `{fmt_num(quantile(scores, 0.95))}`，最大 `{fmt_num(max(scores))}`。",
        f"- 相对 all8 的恶化倍率中位数 `{fmt_num(quantile(ratios, 0.50))}x`，p95 `{fmt_num(quantile(ratios, 0.95))}x`，最大 `{fmt_num(max(ratios))}x`。",
        f"- 风险分布: critical={levels['critical']}, high={levels['high']}, medium={levels['medium']}, acceptable={levels['acceptable']}。",
        "",
        "## 系统性问题",
        "",
        "1. `drop4` 主要打爆 Z/高度方向。绝对最差轴统计: "
        + ", ".join(f"{axis}={count}" for axis, count in abs_axes.most_common())
        + "。",
        "",
        "2. 从相对 all8 的恶化倍率看，最常被放大的轴是: "
        + ", ".join(f"{axis}={count}" for axis, count in ratio_axes.most_common())
        + "。",
        "",
        "3. 最危险的 drop4 不是随机分散，而是集中在少数几组:",
        "",
    ]
    for mask, count in masks.most_common(8):
        lines.append(f"- `{mask}`: {count}/117")

    lines.extend(
        [
            "",
            "4. 换成 surviving anchors 看，本质是某些 4-anchor 子集几何不够立体或水平覆盖不均衡:",
            "",
        ]
    )
    for keep, count in survivors.most_common(8):
        lines.append(f"- 剩 `{keep}`: {count}/117")

    lines.extend(
        [
            "",
            "## 最脆弱 Layout",
            "",
            "| Capture | Version | Variant | Drop | Survive | Score | Ratio | Worst Axis | X p95 | Y p95 | Z p95 | XY area keep/all | Z span keep/all |",
            "|---|---|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in sorted(rows, key=lambda item: as_float(item["drop4_score"]), reverse=True)[:20]:
        lines.append(
            f"| `{row['capture_id']}` | `{row['solver_version']}` | `{row['layout_variant']}` | "
            f"`{row['worst_drop4_mask']}` | `{row['surviving_labels']}` | "
            f"{fmt_num(row['drop4_score'])} | {fmt_num(row['score_ratio'])}x | `{row['abs_worst_axis']}` | "
            f"{fmt_num(row['drop4_xdop_p95'])} | {fmt_num(row['drop4_ydop_p95'])} | {fmt_num(row['drop4_vdop_p95'])} | "
            f"{fmt_num(row['surviving_xy_area_ratio'])} | {fmt_num(row['surviving_z_span_ratio'])} |"
        )

    lines.extend(
        [
            "",
            "## 每个数据组建议优先候选",
            "",
            "| Group | 推荐 Version | Variant | Worst Drop4 | Survive | Score | Worst Axis | 最差 Version | 最差 Score |",
            "|---|---|---|---|---|---:|---|---|---:|",
        ]
    )
    worst_by_group = {row["source_group"]: row for row in group_worst}
    for row in sorted(group_best, key=lambda item: item["source_group"]):
        worst = worst_by_group[row["source_group"]]
        lines.append(
            f"| `{row['source_group']}` | `{row['solver_version']}` | `{row['layout_variant']}` | "
            f"`{row['worst_drop4_mask']}` | `{row['surviving_labels']}` | {fmt_num(row['drop4_score'])} | "
            f"`{row['abs_worst_axis']}` | `{worst['solver_version']}:{worst['layout_variant']}` | {fmt_num(worst['drop4_score'])} |"
        )

    lines.extend(
        [
            "",
            "## 针对这套系统的优化方向",
            "",
            "1. 先按 `drop4_score` 选型，不要按 all8 score 选型。all8 只是正常状态，drop4 才暴露冗余。",
            "2. 把 `{ABGH}`, `{CDEF}`, `{ADFG}`, `{BCEH}` 当成当前编号体系下的高风险 4-anchor 子集来审查。",
            "3. 重新编号或重新布点时，要让任何连续/同侧/同高度倾向的 4 个 anchor 不会同时构成唯一 surviving 子集。编号应该跨高度、跨对角、跨场地边界交错。",
            "4. 如果硬件位置可改，优先增加 surviving-4 的 Z 方向可观测性: 上下层高度差要保留，不能让 surviving 4 几乎都落在同一高度结构或同一倾斜平面。",
            "5. 对 outdoor_20260513 的 `v4-io-roto` 和部分 `v4-io/v5`，不要只因为实测误差看起来好就直接部署；它们在 surviving-4 下有明显冗余风险。",
            "6. 对 Garage/Garage_test_nah 的 `v4-io` 类布局，要重点检查 `dropCDEF` 后剩余 `ABGH` 的几何；这是当前最严重的崩溃模式。",
            "",
            "## 使用口径",
            "",
            "- `critical`: drop4 score >= 20，说明只剩 4 anchor 时几何已经严重退化。",
            "- `high`: 16-20，能比较但不建议作为高可靠冗余。",
            "- `medium`: 12-16，需要结合实测误差和应用容忍度。",
            "- `acceptable`: <12，只代表当前采样空间内较稳，不等于所有环境都安全。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    rows = build_rows(read_csv(args.drop_input), read_csv(args.base_input), read_layout_db(args.layout_db))
    layout_path = args.feature_dir / f"{args.output_prefix}_by_layout.csv"
    report_path = args.report_dir / f"{args.output_prefix}.md"
    write_csv(layout_path, rows)
    write_report(report_path, rows)
    print(f"layouts={len(rows)}")
    print(f"wrote {layout_path}")
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
