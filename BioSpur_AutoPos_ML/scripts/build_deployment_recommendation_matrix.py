#!/usr/bin/env python3
"""Build a deployment recommendation matrix and high-risk geometry figures."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


SYSTEM_EVAL = Path("DATASETS/features/axis_dop_system_evaluation_by_layout.csv")
LAYOUT_DB = Path("DATASETS/processed/layout_database.jsonl")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")
FIG_DIR = Path("outputs/reports/figures")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-eval", type=Path, default=SYSTEM_EVAL)
    parser.add_argument("--layout-db", type=Path, default=LAYOUT_DB)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--fig-dir", type=Path, default=FIG_DIR)
    parser.add_argument("--output-prefix", default="deployment_recommendation_matrix")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def read_layout_db(path: Path) -> dict[str, dict[str, Any]]:
    layouts: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            layout_id = row.get("layout_id")
            if layout_id:
                layouts[str(layout_id)] = row
    return layouts


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


def deploy_class(row: dict[str, str]) -> tuple[str, str, str]:
    score = as_float(row["drop4_score"])
    axis = row["abs_worst_axis"]
    ratio = as_float(row["score_ratio"])
    if score < 12:
        return (
            "A",
            "可部署候选",
            "drop4 最坏几何仍相对可控；仍建议保留运行时组合监控。",
        )
    if score < 16:
        return (
            "B",
            "可用但要降级策略",
            f"drop4 进入中等风险，{axis} 轴会放大；只剩高风险组合时应降低置信度。",
        )
    if score < 20:
        return (
            "C",
            "不建议高可靠部署",
            f"drop4 高风险，约 {ratio:.1f}x all8；可做分析候选，不适合高可靠冗余。",
        )
    return (
        "D",
        "淘汰或需重布点",
        f"drop4 critical，约 {ratio:.1f}x all8；几何冗余不达标。",
    )


def build_matrix(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    by_group: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_group[row["source_group"]].append(row)

    best_by_group = {
        group: min(items, key=lambda item: as_float(item["drop4_score"]))
        for group, items in by_group.items()
    }
    worst_by_group = {
        group: max(items, key=lambda item: as_float(item["drop4_score"]))
        for group, items in by_group.items()
    }

    matrix: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (item["source_group"], as_float(item["drop4_score"]))):
        grade, recommendation, reason = deploy_class(row)
        group = row["source_group"]
        is_best = row["layout_id"] == best_by_group[group]["layout_id"]
        is_worst = row["layout_id"] == worst_by_group[group]["layout_id"]
        if is_best and grade in {"A", "B"}:
            group_decision = "推荐"
        elif is_best:
            group_decision = "组内最好但仍需优化"
        elif is_worst:
            group_decision = "组内最差"
        else:
            group_decision = "备选/不优先"
        matrix.append(
            {
                "source_group": group,
                "group_decision": group_decision,
                "deploy_class": grade,
                "recommendation": recommendation,
                "reason": reason,
                "layout_id": row["layout_id"],
                "capture_id": row["capture_id"],
                "solver_version": row["solver_version"],
                "layout_variant": row["layout_variant"],
                "source_path": row["source_path"],
                "all8_score": as_float(row["all8_score"]),
                "drop4_score": as_float(row["drop4_score"]),
                "score_ratio": as_float(row["score_ratio"]),
                "worst_drop4_mask": row["worst_drop4_mask"],
                "surviving_labels": row["surviving_labels"],
                "abs_worst_axis": row["abs_worst_axis"],
                "ratio_worst_axis": row["ratio_worst_axis"],
                "xdop_p95": as_float(row["drop4_xdop_p95"]),
                "ydop_p95": as_float(row["drop4_ydop_p95"]),
                "vdop_p95": as_float(row["drop4_vdop_p95"]),
                "xdop_ratio": as_float(row["xdop_ratio"]),
                "ydop_ratio": as_float(row["ydop_ratio"]),
                "vdop_ratio": as_float(row["vdop_ratio"]),
                "surviving_xy_area_ratio": as_float(row["surviving_xy_area_ratio"]),
                "surviving_z_span_ratio": as_float(row["surviving_z_span_ratio"]),
            }
        )
    return matrix


def sanitize(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return value.strip("_")[:120]


def axis_limits(records: list[dict[str, Any]], a: str, b: str) -> tuple[tuple[float, float], tuple[float, float]]:
    xs = [record[a] for record in records]
    ys = [record[b] for record in records]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_pad = max((x_max - x_min) * 0.08, 100.0)
    y_pad = max((y_max - y_min) * 0.08, 100.0)
    return (x_min - x_pad, x_max + x_pad), (y_min - y_pad, y_max + y_pad)


def scatter_layout(ax: Any, records: list[dict[str, Any]], dropped: set[str], x_key: str, y_key: str, title: str) -> None:
    for record in records:
        label = record["label"]
        dropped_anchor = label in dropped
        color = "#cf3f37" if dropped_anchor else "#2f7d5b"
        marker = "x" if dropped_anchor else "o"
        size = 100 if dropped_anchor else 80
        ax.scatter(record[x_key], record[y_key], s=size, c=color, marker=marker, linewidths=2)
        ax.text(record[x_key], record[y_key], f" {label}", fontsize=10, va="center")
    x_lim, y_lim = axis_limits(records, x_key, y_key)
    ax.set_xlim(*x_lim)
    ax.set_ylim(*y_lim)
    ax.set_xlabel(f"{x_key.upper()} mm")
    ax.set_ylabel(f"{y_key.upper()} mm")
    ax.set_title(title)
    ax.grid(True, alpha=0.25)
    ax.set_aspect("equal", adjustable="box")


def plot_high_risk(row: dict[str, str], layout: dict[str, Any], out: Path) -> None:
    records = anchor_records(layout)
    dropped = set(row["worst_drop4_mask"].removeprefix("drop"))
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5))
    scatter_layout(axes[0], records, dropped, "x", "y", "XY footprint")
    scatter_layout(axes[1], records, dropped, "x", "z", "XZ height geometry")
    fig.suptitle(
        "\n".join(
            [
                f"{row['capture_id']} {row['solver_version']}:{row['layout_variant']}  {row['worst_drop4_mask']} -> survive {row['surviving_labels']}",
                f"drop4 score {as_float(row['drop4_score']):.2f}, axis {row['abs_worst_axis']}, "
                f"X/Y/Z p95 {as_float(row['drop4_xdop_p95']):.2f}/{as_float(row['drop4_ydop_p95']):.2f}/{as_float(row['drop4_vdop_p95']):.2f}",
            ]
        ),
        fontsize=12,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(out, dpi=170)
    plt.close(fig)


def make_figures(rows: list[dict[str, str]], layouts: dict[str, dict[str, Any]], fig_dir: Path) -> list[tuple[str, Path, dict[str, str]]]:
    fig_dir.mkdir(parents=True, exist_ok=True)
    target_masks = ["dropABGH", "dropCDEF", "dropADFG", "dropBCEH"]
    figures: list[tuple[str, Path, dict[str, str]]] = []
    for mask in target_masks:
        candidates = [row for row in rows if row["worst_drop4_mask"] == mask and row["layout_id"] in layouts]
        if not candidates:
            continue
        row = max(candidates, key=lambda item: as_float(item["drop4_score"]))
        stem = sanitize(f"high_risk_geometry_{mask}_{row['capture_id']}_{row['solver_version']}_{row['layout_variant']}")
        out = fig_dir / f"{stem}.png"
        plot_high_risk(row, layouts[row["layout_id"]], out)
        figures.append((mask, out, row))
    return figures


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def fmt_num(value: Any, digits: int = 2) -> str:
    value = as_float(value)
    if not math.isfinite(value):
        return ""
    return f"{value:.{digits}f}"


def write_report(path: Path, matrix: list[dict[str, Any]], figures: list[tuple[str, Path, dict[str, str]]]) -> None:
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in matrix:
        by_group[row["source_group"]].append(row)
    best_rows = [min(items, key=lambda row: as_float(row["drop4_score"])) for items in by_group.values()]
    class_counts: dict[str, int] = defaultdict(int)
    for row in matrix:
        class_counts[row["deploy_class"]] += 1

    lines = [
        "# 部署推荐矩阵 + 高风险几何图",
        "",
        f"生成时间: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## 怎么读",
        "",
        "- A: 可部署候选，drop4 最坏情况相对可控。",
        "- B: 可用但要降级策略，只剩高风险组合时要降低置信度。",
        "- C: 不建议高可靠部署，适合分析或临时验证。",
        "- D: 淘汰或需要重布点。",
        "",
        "## 总体分布",
        "",
        f"- A: {class_counts['A']}",
        f"- B: {class_counts['B']}",
        f"- C: {class_counts['C']}",
        f"- D: {class_counts['D']}",
        "",
        "## 每组推荐",
        "",
        "| Group | 决策 | Class | Version | Variant | Worst Drop4 | Survive | Score | Ratio | Axis | 说明 |",
        "|---|---|---|---|---|---|---|---:|---:|---|---|",
    ]
    for row in sorted(best_rows, key=lambda item: item["source_group"]):
        lines.append(
            f"| `{row['source_group']}` | `{row['group_decision']}` | `{row['deploy_class']}` | "
            f"`{row['solver_version']}` | `{row['layout_variant']}` | `{row['worst_drop4_mask']}` | "
            f"`{row['surviving_labels']}` | {fmt_num(row['drop4_score'])} | {fmt_num(row['score_ratio'])}x | "
            f"`{row['abs_worst_axis']}` | {row['recommendation']} |"
        )

    lines.extend(
        [
            "",
            "## 明确不优先/淘汰 Top 20",
            "",
            "| Capture | Version | Variant | Class | Drop | Survive | Score | Ratio | X/Y/Z p95 |",
            "|---|---|---|---|---|---|---:|---:|---:|",
        ]
    )
    for row in sorted(matrix, key=lambda item: as_float(item["drop4_score"]), reverse=True)[:20]:
        lines.append(
            f"| `{row['capture_id']}` | `{row['solver_version']}` | `{row['layout_variant']}` | "
            f"`{row['deploy_class']}` | `{row['worst_drop4_mask']}` | `{row['surviving_labels']}` | "
            f"{fmt_num(row['drop4_score'])} | {fmt_num(row['score_ratio'])}x | "
            f"{fmt_num(row['xdop_p95'])}/{fmt_num(row['ydop_p95'])}/{fmt_num(row['vdop_p95'])} |"
        )

    lines.extend(["", "## 高风险几何图", ""])
    for mask, fig, row in figures:
        lines.extend(
            [
                f"### {mask} -> survive `{row['surviving_labels']}`",
                "",
                f"- 代表 layout: `{row['capture_id']} {row['solver_version']}:{row['layout_variant']}`",
                f"- drop4 score: `{fmt_num(row['drop4_score'])}`, worst axis: `{row['abs_worst_axis']}`",
                f"- X/Y/Z p95: `{fmt_num(row['drop4_xdop_p95'])}` / `{fmt_num(row['drop4_ydop_p95'])}` / `{fmt_num(row['drop4_vdop_p95'])}`",
                "",
                f"![{mask}]({rel(fig, path.parent)})",
                "",
            ]
        )

    lines.extend(
        [
            "## 下一步执行建议",
            "",
            "1. 部署候选只从 A/B 类里选；如果某个 group 最好也只是 C/D，说明这个 group 没有合格冗余 layout。",
            "2. 运行时监控 surviving anchor set；一旦落入 `ABGH`, `CDEF`, `BCEH`, `ADFG` 这几类，降低 Z/3D 输出置信度。",
            "3. 能改硬件时，优先重布这些 surviving set 的高度结构，让任意 4 个幸存 anchor 都保留足够立体角。",
            "4. 不能改硬件时，至少做 anchor 编号交错和故障策略，避免某一类失效直接暴露最弱 4-anchor 子系统。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    rows = read_csv(args.system_eval)
    layouts = read_layout_db(args.layout_db)
    matrix = build_matrix(rows)
    figures = make_figures(rows, layouts, args.fig_dir)
    csv_path = args.feature_dir / f"{args.output_prefix}.csv"
    report_path = args.report_dir / f"{args.output_prefix}.md"
    write_csv(csv_path, matrix)
    write_report(report_path, matrix, figures)
    print(f"matrix_rows={len(matrix)} figures={len(figures)}")
    print(f"wrote {csv_path}")
    print(f"wrote {report_path}")
    for _, fig, _ in figures:
        print(f"wrote {fig}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
