#!/usr/bin/env python3
"""Generate plots and a human-readable Bewertung report."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


SCORES_V2 = Path("DATASETS/features/layout_scores_v2.csv")
OPTI_LAYOUT = Path("DATASETS/features/optitrack_layout_validation.csv")
OPTI_SESSION = Path("DATASETS/features/optitrack_session_validation.csv")
LAYOUT_DB = Path("DATASETS/processed/layout_database.jsonl")
REPORT_DIR = Path("outputs/reports")
FIG_DIR = Path("outputs/reports/figures")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores-v2", type=Path, default=SCORES_V2)
    parser.add_argument("--opti-layout", type=Path, default=OPTI_LAYOUT)
    parser.add_argument("--opti-session", type=Path, default=OPTI_SESSION)
    parser.add_argument("--layout-db", type=Path, default=LAYOUT_DB)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--fig-dir", type=Path, default=FIG_DIR)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def read_layouts(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            out[item["layout_id"]] = item
    return out


def f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def savefig(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=160)
    plt.close()


def plot_top_by_group(scores: list[dict[str, str]], fig_dir: Path) -> Path:
    tops = [row for row in scores if row.get("group_rank_v2") == "1"]
    labels = [
        row["score_group"].replace("outdoor_20260513/", "").replace("28052026_Erlangen_Official/", "Erlangen/")
        + "\n"
        + row["solver_version"]
        + " "
        + row["layout_variant"]
        for row in tops
    ]
    values = [f(row["production_score_v2"]) or 0.0 for row in tops]
    fig_h = max(4.5, 0.7 * len(tops))
    plt.figure(figsize=(11, fig_h))
    plt.barh(range(len(tops)), values, color="#2f6f73")
    plt.yticks(range(len(tops)), labels, fontsize=8)
    plt.xlabel("Score v2 (lower is better)")
    plt.title("Top layout per source group")
    plt.gca().invert_yaxis()
    for idx, value in enumerate(values):
        plt.text(value + 0.8, idx, f"{value:.1f}", va="center", fontsize=8)
    out = fig_dir / "score_v2_top_by_group.png"
    savefig(out)
    return out


def plot_erlangen_components(scores: list[dict[str, str]], fig_dir: Path) -> Path:
    rows = [
        row for row in scores
        if row["capture_id"] == "28052026_Erlangen_Official"
        and row["source_group"].endswith("v1_to_v4_io_field_check")
    ]
    rows.sort(key=lambda row: int(row["group_rank_v2"]))
    labels = [row["solver_version"] for row in rows]
    fields = [
        ("production_score_v2", "Production"),
        ("evaluation_score", "Eval"),
        ("geometry_score", "Geometry"),
        ("dop_score", "DOP"),
        ("optitrack_validation_score", "OptiTrack"),
    ]
    x = list(range(len(rows)))
    width = 0.15
    plt.figure(figsize=(11, 5.5))
    colors = ["#2f6f73", "#7b5ea7", "#d18739", "#4e7ab5", "#9a4d4d"]
    for offset, ((field, label), color) in enumerate(zip(fields, colors)):
        vals = [f(row.get(field)) for row in rows]
        y = [0.0 if value is None else value for value in vals]
        positions = [idx + (offset - 2) * width for idx in x]
        plt.bar(positions, y, width=width, label=label, color=color, alpha=0.9)
        for xpos, ypos, raw in zip(positions, y, vals):
            if raw is not None and raw > 0:
                plt.text(xpos, ypos + 1.5, f"{raw:.0f}", ha="center", fontsize=7, rotation=90)
    plt.xticks(x, labels)
    plt.ylabel("Score (lower is better)")
    plt.title("Erlangen official component scores")
    plt.legend(ncol=5, fontsize=8)
    out = fig_dir / "erlangen_score_components.png"
    savefig(out)
    return out


def plot_opti_layout_scatter(opti_layout: list[dict[str, str]], fig_dir: Path) -> Path:
    rows = [row for row in opti_layout if row.get("eval_set") == "all8"]
    xs = [f(row.get("layout_eval_roto_abs_deltaR_p95_mm")) for row in rows]
    ys = [f(row.get("opti_err_3d_rms_mm")) for row in rows]
    labels = [row.get("version", "") for row in rows]
    points = [(x, y, label) for x, y, label in zip(xs, ys, labels) if x is not None and y is not None]
    plt.figure(figsize=(7, 5))
    plt.scatter([p[0] for p in points], [p[1] for p in points], s=70, color="#7b5ea7")
    for x, y, label in points:
        plt.text(x + 0.4, y, label, fontsize=8)
    plt.xlabel("Roto abs DeltaR p95 (mm)")
    plt.ylabel("OptiTrack 3D RMS error (mm)")
    plt.title("Layout-level validation: roto metric vs real error")
    plt.grid(True, alpha=0.25)
    out = fig_dir / "opti_roto_p95_vs_3d_rms.png"
    savefig(out)
    return out


def plot_session_dop_scatter(opti_session: list[dict[str, str]], fig_dir: Path) -> Path:
    rows = [
        row for row in opti_session
        if row.get("version") == "v4-io"
        and row.get("eval_set") == "all8"
        and row.get("grid_mm") == "25.0"
    ]
    xs = [f(row.get("vdop")) for row in rows]
    ys = [f(row.get("err_vertical_mm")) for row in rows]
    locs = [row.get("height", "") for row in rows]
    color_by_height = {"low": "#2f6f73", "mid": "#d18739", "high": "#9a4d4d"}
    plt.figure(figsize=(7, 5))
    for height in ["low", "mid", "high"]:
        pts = [(x, y) for x, y, h in zip(xs, ys, locs) if x is not None and y is not None and h == height]
        if pts:
            plt.scatter([p[0] for p in pts], [p[1] for p in pts], s=55, label=height, color=color_by_height[height])
    plt.xlabel("VDOP at session position")
    plt.ylabel("Vertical error (mm)")
    plt.title("Session-level DOP vs vertical error (Erlangen v4-io all8 grid25)")
    plt.grid(True, alpha=0.25)
    plt.legend(title="height")
    out = fig_dir / "session_vdop_vs_vertical_error.png"
    savefig(out)
    return out


def plot_geometry_overview(scores: list[dict[str, str]], layouts: dict[str, dict[str, Any]], fig_dir: Path) -> Path:
    selected = [row for row in scores if row.get("group_rank_v2") == "1"][:6]
    cols = 3
    rows_n = math.ceil(len(selected) / cols)
    plt.figure(figsize=(12, 4 * rows_n))
    for idx, score in enumerate(selected, start=1):
        layout = layouts.get(score["layout_id"])
        if not layout:
            continue
        ax = plt.subplot(rows_n, cols, idx)
        anchors = layout["anchors"]
        lower = [a for a in anchors if int(a["anchor_id"]) <= 3]
        upper = [a for a in anchors if int(a["anchor_id"]) >= 4]
        for group, color, label in [(lower, "#2f6f73", "ABCD"), (upper, "#9a4d4d", "EFGH")]:
            xs = [float(a["x_mm"]) for a in group]
            ys = [float(a["y_mm"]) for a in group]
            ax.scatter(xs, ys, color=color, label=label)
            if len(group) >= 3:
                closed = group + [group[0]]
                ax.plot([float(a["x_mm"]) for a in closed], [float(a["y_mm"]) for a in closed], color=color, alpha=0.6)
            for anchor in group:
                ax.text(float(anchor["x_mm"]), float(anchor["y_mm"]), str(anchor["label"]), fontsize=9)
        ax.set_aspect("equal", adjustable="box")
        ax.set_title(f"{score['solver_version']} {score['layout_variant']}\n{score['capture_id']}", fontsize=9)
        ax.grid(True, alpha=0.2)
    handles, labels = plt.gca().get_legend_handles_labels()
    if handles:
        plt.figlegend(handles[:2], labels[:2], loc="lower center", ncol=2)
    out = fig_dir / "top_layout_geometry_overview.png"
    savefig(out)
    return out


def write_report(path: Path, figures: list[Path], scores: list[dict[str, str]]) -> None:
    erlangen = [
        row for row in scores
        if row["capture_id"] == "28052026_Erlangen_Official"
        and row["source_group"].endswith("v1_to_v4_io_field_check")
    ]
    erlangen.sort(key=lambda row: int(row["group_rank_v2"]))
    lines = [
        "# Bewertung Report",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Figures",
        "",
    ]
    for fig in figures:
        rel = fig.relative_to(path.parent)
        lines.append(f"- ![{fig.stem}]({rel.as_posix()})")

    lines.extend(["", "## Erlangen Official Bewertung", ""])
    lines.extend(["| Rank | Version | Score v2 | Validation rank | Opti 3D RMS | Opti 3D p95 | Comment |", "|---:|---|---:|---:|---:|---:|---|"])
    for row in erlangen:
        comment = ""
        if row["solver_version"] == "v2":
            comment = "Best current production and validation balance."
        elif row["solver_version"] == "v3-lite":
            comment = "Very close to v2; strong fallback."
        elif row["solver_version"] == "v4-io":
            comment = "Best DOP-backed candidate; median/vertical behavior is good, p95/RMS weaker."
        elif row["solver_version"] == "v3-full":
            comment = "Not recommended in current evidence."
        elif row["solver_version"] == "v1-old":
            comment = "Legacy baseline; useful for comparison only."
        lines.append(
            f"| {row['group_rank_v2']} | `{row['solver_version']}` | {float(row['production_score_v2']):.3f} | "
            f"{row['validation_rank']} | {row['opti_err_3d_rms_mm']} | {row['opti_err_3d_p95_mm']} | {comment} |"
        )

    lines.extend(["", "## Current Decision", ""])
    lines.append("For the Erlangen official dataset, keep `v2` as the current best scored layout, with `v3-lite` as near-tie backup.")
    lines.append("For outdoor 20260513 evaluated runs, `v4-io-roto` is the most consistent top candidate.")
    lines.append("Do not start ML training yet; Score v2 and OptiTrack validation should be reviewed first.")
    lines.append("")
    lines.append("No GPU was used.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=True)
    args.fig_dir.mkdir(parents=True, exist_ok=True)

    scores = read_csv(args.scores_v2)
    opti_layout = read_csv(args.opti_layout)
    opti_session = read_csv(args.opti_session)
    layouts = read_layouts(args.layout_db)

    figures = [
        plot_top_by_group(scores, args.fig_dir),
        plot_erlangen_components(scores, args.fig_dir),
        plot_opti_layout_scatter(opti_layout, args.fig_dir),
        plot_session_dop_scatter(opti_session, args.fig_dir),
        plot_geometry_overview(scores, layouts, args.fig_dir),
    ]
    write_report(args.report_dir / "bewertung_report.md", figures, scores)

    print(f"figures={len(figures)}")
    print(f"wrote {args.report_dir / 'bewertung_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
