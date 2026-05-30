#!/usr/bin/env python3
"""Stratified OptiTrack validation analysis.

This script separates real error behavior by height, location, and facing. It is
CPU-only and does not train any model.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


TABLE_DIR = Path("DATASETS/raw_captures/28052026_Erlangen_Official/Analysis/official_extra_analysis/tables")
SESSION_VALIDATION = Path("DATASETS/features/optitrack_session_validation.csv")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")
FIG_DIR = Path("outputs/reports/figures")


ERROR_FIELDS = [
    "err_3d_mm",
    "err_horizontal_mm",
    "err_vertical_mm",
]

DOP_FIELDS = [
    "gdop",
    "hdop",
    "vdop",
    "cond",
    "dop_radial_p95_mm",
    "pct_ge8",
    "distance_to_array_centroid_mm",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-dir", type=Path, default=TABLE_DIR)
    parser.add_argument("--session-validation", type=Path, default=SESSION_VALIDATION)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--fig-dir", type=Path, default=FIG_DIR)
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


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def rms(values: list[float]) -> float:
    return math.sqrt(mean([value * value for value in values])) if values else 0.0


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx = mean(xs)
    my = mean(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / math.sqrt(vx * vy)


def ranks(values: list[float]) -> list[float]:
    ordered = sorted((value, idx) for idx, value in enumerate(values))
    out = [0.0] * len(values)
    pos = 0
    while pos < len(ordered):
        end = pos + 1
        while end < len(ordered) and ordered[end][0] == ordered[pos][0]:
            end += 1
        rank = (pos + 1 + end) / 2.0
        for _value, idx in ordered[pos:end]:
            out[idx] = rank
        pos = end
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    return pearson(ranks(xs), ranks(ys))


def strata(row: dict[str, str]) -> list[tuple[str, str]]:
    return [
        ("overall", "all"),
        ("height", row.get("height", "")),
        ("location", row.get("location", "")),
        ("facing", row.get("facing", "")),
        ("location_height", f"{row.get('location', '')}/{row.get('height', '')}"),
        ("facing_height", f"{row.get('facing', '')}/{row.get('height', '')}"),
    ]


def summarize_errors(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        for strata_type, strata_value in strata(row):
            if strata_value:
                groups[(row.get("version", ""), row.get("eval_set", ""), strata_type, strata_value)].append(row)

    out: list[dict[str, Any]] = []
    for (version, eval_set, strata_type, strata_value), items in sorted(groups.items()):
        summary: dict[str, Any] = {
            "version": version,
            "eval_set": eval_set,
            "strata_type": strata_type,
            "strata_value": strata_value,
            "n": len(items),
        }
        for field in ERROR_FIELDS:
            values = [value for value in (as_float(row.get(field)) for row in items) if value is not None]
            summary[f"{field}_median"] = f"{percentile(values, 0.5):.9g}" if values else ""
            summary[f"{field}_p75"] = f"{percentile(values, 0.75):.9g}" if values else ""
            summary[f"{field}_p95"] = f"{percentile(values, 0.95):.9g}" if values else ""
            summary[f"{field}_rms"] = f"{rms(values):.9g}" if values else ""
            summary[f"{field}_max"] = f"{max(values):.9g}" if values else ""
        pct = [value for value in (as_float(row.get("pct_ge8")) for row in items) if value is not None]
        dist = [value for value in (as_float(row.get("distance_to_array_centroid_mm")) for row in items) if value is not None]
        summary["pct_ge8_median"] = f"{percentile(pct, 0.5):.9g}" if pct else ""
        summary["distance_to_centroid_median_mm"] = f"{percentile(dist, 0.5):.9g}" if dist else ""
        out.append(summary)
    return out


def dop_correlations(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        for strata_type, strata_value in strata(row):
            if strata_value:
                groups[(row.get("version", ""), row.get("eval_set", ""), row.get("grid_mm", ""), strata_type, strata_value)].append(row)

    out: list[dict[str, Any]] = []
    for (version, eval_set, grid_mm, strata_type, strata_value), items in sorted(groups.items()):
        for x_field in DOP_FIELDS:
            for y_field in ERROR_FIELDS:
                xs: list[float] = []
                ys: list[float] = []
                for row in items:
                    x = as_float(row.get(x_field))
                    y = as_float(row.get(y_field))
                    if x is None or y is None:
                        continue
                    xs.append(x)
                    ys.append(y)
                if len(xs) < 4:
                    continue
                p = pearson(xs, ys)
                s = spearman(xs, ys)
                out.append(
                    {
                        "version": version,
                        "eval_set": eval_set,
                        "grid_mm": grid_mm,
                        "strata_type": strata_type,
                        "strata_value": strata_value,
                        "x_feature": x_field,
                        "y_error": y_field,
                        "n": len(xs),
                        "pearson_r": "" if p is None else f"{p:.6f}",
                        "spearman_r": "" if s is None else f"{s:.6f}",
                        "x_min": f"{min(xs):.6g}",
                        "x_max": f"{max(xs):.6g}",
                        "y_min": f"{min(ys):.6g}",
                        "y_max": f"{max(ys):.6g}",
                    }
                )
    return out


def bar_plot(summary: list[dict[str, Any]], strata_type: str, metric: str, title: str, out: Path) -> None:
    rows = [
        row for row in summary
        if row["strata_type"] == strata_type
        and row["eval_set"] == "all8"
        and row["strata_value"] != "all"
    ]
    versions = sorted({row["version"] for row in rows})
    strata_values = sorted({row["strata_value"] for row in rows})
    lookup = {(row["version"], row["strata_value"]): as_float(row.get(metric)) for row in rows}

    x = list(range(len(strata_values)))
    width = 0.8 / max(1, len(versions))
    colors = ["#2f6f73", "#7b5ea7", "#d18739", "#4e7ab5", "#9a4d4d", "#6d6d6d"]
    plt.figure(figsize=(11, 5.5))
    for idx, version in enumerate(versions):
        vals = [lookup.get((version, value)) for value in strata_values]
        ys = [0.0 if value is None else value for value in vals]
        pos = [base + (idx - (len(versions) - 1) / 2) * width for base in x]
        plt.bar(pos, ys, width=width, label=version, color=colors[idx % len(colors)])
    plt.xticks(x, strata_values)
    plt.ylabel(metric)
    plt.title(title)
    plt.legend(ncol=min(5, len(versions)), fontsize=8)
    plt.grid(axis="y", alpha=0.2)
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close()


def scatter_plot(rows: list[dict[str, str]], x_field: str, y_field: str, color_field: str, title: str, out: Path) -> None:
    values = sorted({row.get(color_field, "") for row in rows if row.get(color_field, "")})
    colors = ["#2f6f73", "#7b5ea7", "#d18739", "#4e7ab5", "#9a4d4d", "#6d6d6d"]
    plt.figure(figsize=(7, 5.2))
    for idx, value in enumerate(values):
        pts = []
        for row in rows:
            if row.get(color_field) != value:
                continue
            x = as_float(row.get(x_field))
            y = as_float(row.get(y_field))
            if x is not None and y is not None:
                pts.append((x, y))
        if pts:
            plt.scatter([p[0] for p in pts], [p[1] for p in pts], label=value, s=55, color=colors[idx % len(colors)])
    plt.xlabel(x_field)
    plt.ylabel(y_field)
    plt.title(title)
    plt.grid(True, alpha=0.25)
    plt.legend(title=color_field)
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(out, dpi=160)
    plt.close()


def best_overall(summary: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    rows = [
        row for row in summary
        if row["strata_type"] == "overall"
        and row["strata_value"] == "all"
        and row["eval_set"] == "all8"
        and as_float(row.get(metric)) is not None
    ]
    return sorted(rows, key=lambda row: as_float(row.get(metric)) or float("inf"))


def strongest_corr(rows: list[dict[str, Any]], strata_type: str, n: int = 10) -> list[dict[str, Any]]:
    candidates = [
        row for row in rows
        if row["strata_type"] == strata_type
        and row["eval_set"] == "all8"
        and row["grid_mm"] == "25.0"
    ]

    def key(row: dict[str, Any]) -> float:
        val = as_float(row.get("spearman_r"))
        return abs(val) if val is not None else -1.0

    return sorted(candidates, key=key, reverse=True)[:n]


def write_report(path: Path, summary: list[dict[str, Any]], corr: list[dict[str, Any]], figures: list[Path]) -> None:
    ranking = best_overall(summary, "err_3d_mm_rms")
    lines = [
        "# Stratified OptiTrack Bewertung",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Scope",
        "",
        "This report uses OptiTrack validation data to inspect error structure by height, location, and facing.",
        "It does not train any model and uses no GPU.",
        "",
        "## Figures",
        "",
    ]
    for fig in figures:
        rel = fig.relative_to(path.parent)
        lines.append(f"- ![{fig.stem}]({rel.as_posix()})")

    lines.extend(["", "## Overall All8 Version Ranking By 3D RMS", ""])
    lines.extend(["| Rank | Version | N | 3D RMS | 3D Median | 3D p95 | Vertical Median |", "|---:|---|---:|---:|---:|---:|---:|"])
    for idx, row in enumerate(ranking, start=1):
        lines.append(
            f"| {idx} | `{row['version']}` | {row['n']} | {row['err_3d_mm_rms']} | "
            f"{row['err_3d_mm_median']} | {row['err_3d_mm_p95']} | {row['err_vertical_mm_median']} |"
        )

    lines.extend(["", "## Strongest DOP/Error Correlations By Stratum", ""])
    for strata_type in ["overall", "height", "location", "facing"]:
        lines.append(f"### {strata_type}")
        lines.extend(["| Stratum | X feature | Y error | N | Pearson r | Spearman r |", "|---|---|---|---:|---:|---:|"])
        for row in strongest_corr(corr, strata_type, 8):
            lines.append(
                f"| `{row['strata_value']}` | `{row['x_feature']}` | `{row['y_error']}` | "
                f"{row['n']} | {row['pearson_r']} | {row['spearman_r']} |"
            )
        lines.append("")

    lines.extend(["## Bewertung", ""])
    if ranking:
        lines.append(f"- By all8 OptiTrack 3D RMS, `{ranking[0]['version']}` is currently best.")
    lines.append("- v2 and v3-lite remain very close; treat them as a pair until additional validation separates them.")
    lines.append("- DOP/error correlations are not uniformly positive; they are confounded by location and height in this dataset.")
    lines.append("- Score v3 should be calibrated with stratified objectives, especially vertical p95/RMS and edge/high cases.")
    lines.append("- Still no reason to start GPU training.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    args.fig_dir.mkdir(parents=True, exist_ok=True)

    error_rows = read_csv(args.table_dir / "tag_abs_errors_per_session.csv")
    dop_rows = read_csv(args.session_validation)
    summary = summarize_errors(error_rows)
    corr = dop_correlations(dop_rows)
    write_csv(args.feature_dir / "optitrack_stratified_error_summary.csv", summary)
    write_csv(args.feature_dir / "optitrack_stratified_dop_correlations.csv", corr)

    grid25_all8 = [
        row for row in dop_rows
        if row.get("version") == "v4-io"
        and row.get("eval_set") == "all8"
        and row.get("grid_mm") == "25.0"
    ]
    figures = [
        args.fig_dir / "error_by_height_version.png",
        args.fig_dir / "error_by_location_version.png",
        args.fig_dir / "error_by_facing_version.png",
        args.fig_dir / "vdop_vs_vertical_error_by_height.png",
        args.fig_dir / "gdop_vs_3d_error_by_location.png",
    ]
    bar_plot(summary, "height", "err_3d_mm_rms", "All8 3D RMS by height and version", figures[0])
    bar_plot(summary, "location", "err_3d_mm_rms", "All8 3D RMS by location and version", figures[1])
    bar_plot(summary, "facing", "err_3d_mm_rms", "All8 3D RMS by facing and version", figures[2])
    scatter_plot(grid25_all8, "vdop", "err_vertical_mm", "height", "VDOP vs vertical error by height", figures[3])
    scatter_plot(grid25_all8, "gdop", "err_3d_mm", "location", "GDOP vs 3D error by location", figures[4])
    write_report(args.report_dir / "stratified_optitrack_bewertung.md", summary, corr, figures)

    print(f"stratified_summary={len(summary)} dop_correlations={len(corr)} figures={len(figures)}")
    print(f"wrote {args.report_dir / 'stratified_optitrack_bewertung.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
