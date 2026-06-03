#!/usr/bin/env python3
"""Temporal drift diagnostics for official static tag captures.

This script intentionally works on raw per-anchor range rows, before any tag
position solver.  For each static session and anchor it fits:

    range_residual_mm = range_mm - median(range_mm)
    range_residual_mm ~ elapsed_minutes

The output answers whether a UWB link drifts during a nominally static 120 s
capture.  It does not use OptiTrack truth and does not fit tag positions.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


LAYOUT_HORIZONTAL_AXES = ("x_mm", "y_mm")
LAYOUT_VERTICAL_AXIS = "z_mm"
LAYOUT_UPPER_LAYER_SIGN = "negative_z"
REPORTED_HEIGHT_EXPR = "-z_mm"
OPTITRACK_VERTICAL_AXIS = "Y"
ANCHORS = list("ABCDEFGH")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def append_run_meta(out_dir: Path, entry: dict) -> None:
    meta_path = out_dir / "run_meta.json"
    lock_path = out_dir / "run_meta.json.lock"
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError:
                meta = {"runs": []}
        else:
            meta = {"runs": []}
        meta.setdefault("runs", []).append(entry)
        tmp = meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
        tmp.replace(meta_path)
        fcntl.flock(lock, fcntl.LOCK_UN)


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def mad(values: np.ndarray) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    med = np.nanmedian(arr)
    return float(1.4826 * np.nanmedian(np.abs(arr - med)))


def pctl(values: np.ndarray, pct: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.nanpercentile(arr, pct))


def session_id_from_path(path: Path) -> str:
    for parent in path.parents:
        m = re.search(r"(static_ID\d+)_", parent.name)
        if m:
            return m.group(1).replace("static_", "")
    return path.parents[1].name


def capture_name_from_path(path: Path) -> str:
    for parent in path.parents:
        if parent.name.startswith("static_ID"):
            return parent.name
    return path.parents[1].name


def linear_fit(x_min: np.ndarray, y_mm: np.ndarray) -> dict[str, float]:
    mask = np.isfinite(x_min) & np.isfinite(y_mm)
    x = np.asarray(x_min[mask], dtype=float)
    y = np.asarray(y_mm[mask], dtype=float)
    n = int(x.size)
    if n < 3:
        return {
            "n": n,
            "slope_mm_per_min": float("nan"),
            "intercept_mm": float("nan"),
            "slope_ci95_low_mm_per_min": float("nan"),
            "slope_ci95_high_mm_per_min": float("nan"),
            "slope_se_mm_per_min": float("nan"),
            "r2": float("nan"),
        }
    x0 = x - np.mean(x)
    y0 = y - np.mean(y)
    sxx = float(np.sum(x0 * x0))
    if sxx <= 1e-12:
        return {
            "n": n,
            "slope_mm_per_min": float("nan"),
            "intercept_mm": float("nan"),
            "slope_ci95_low_mm_per_min": float("nan"),
            "slope_ci95_high_mm_per_min": float("nan"),
            "slope_se_mm_per_min": float("nan"),
            "r2": float("nan"),
        }
    slope = float(np.sum(x0 * y0) / sxx)
    intercept = float(np.mean(y) - slope * np.mean(x))
    pred = intercept + slope * x
    resid = y - pred
    sse = float(np.sum(resid * resid))
    sst = float(np.sum((y - np.mean(y)) ** 2))
    r2 = float(1.0 - sse / sst) if sst > 1e-12 else 0.0
    dof = max(1, n - 2)
    sigma2 = sse / dof
    se = math.sqrt(max(0.0, sigma2 / sxx))
    ci = 1.96 * se
    return {
        "n": n,
        "slope_mm_per_min": slope,
        "intercept_mm": intercept,
        "slope_ci95_low_mm_per_min": slope - ci,
        "slope_ci95_high_mm_per_min": slope + ci,
        "slope_se_mm_per_min": se,
        "r2": r2,
    }


def collect_static_files(captures_root: Path) -> list[Path]:
    return sorted(captures_root.glob("static_ID*/tag_capture*/tr_all.csv"))


def load_static_metadata(layout_table: Path) -> dict[str, dict]:
    if not layout_table.exists():
        return {}
    df = pd.read_csv(layout_table)
    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        sid = str(row.get("ID", "")).strip()
        if sid:
            out[sid] = {
                "location": row.get("location", ""),
                "height": row.get("height", ""),
                "facing": row.get("facing", ""),
            }
    return out


def analyze_file(path: Path, *, range_column: str, min_rows: int, metadata: dict[str, dict]) -> tuple[list[dict], dict, pd.DataFrame]:
    sid = session_id_from_path(path)
    cap = capture_name_from_path(path)
    meta = metadata.get(sid, {})
    df = pd.read_csv(path)
    total_rows = int(len(df))
    exclusions = {
        "session_id": sid,
        "capture": cap,
        "path": str(path),
        "total_rows": total_rows,
        "excluded_invalid": 0,
        "excluded_bad_status": 0,
        "excluded_bad_anchor": 0,
        "excluded_missing_time_or_range": 0,
        "excluded_nonpositive_range": 0,
        "rows_used": 0,
    }
    if range_column not in df.columns:
        raise KeyError(f"{range_column} missing in {path}")

    valid = pd.to_numeric(df.get("valid", 0), errors="coerce").fillna(0).astype(int) == 1
    status = df.get("status", "")
    if isinstance(status, pd.Series):
        good_status = status.fillna("").astype(str).isin(["", "O"])
    else:
        good_status = pd.Series([True] * len(df))
    anchor = pd.to_numeric(df.get("anchor_id", -1), errors="coerce")
    good_anchor = anchor.between(0, 7)
    time_s = pd.to_numeric(df.get("host_elapsed_s", np.nan), errors="coerce")
    epoch_s = pd.to_numeric(df.get("host_epoch_s", np.nan), errors="coerce")
    rng = pd.to_numeric(df[range_column], errors="coerce")
    good_time_range = np.isfinite(time_s) & np.isfinite(rng)
    positive = rng > 0

    exclusions["excluded_invalid"] = int((~valid).sum())
    exclusions["excluded_bad_status"] = int((valid & ~good_status).sum())
    exclusions["excluded_bad_anchor"] = int((valid & good_status & ~good_anchor).sum())
    exclusions["excluded_missing_time_or_range"] = int((valid & good_status & good_anchor & ~good_time_range).sum())
    exclusions["excluded_nonpositive_range"] = int((valid & good_status & good_anchor & good_time_range & ~positive).sum())

    keep = valid & good_status & good_anchor & good_time_range & positive
    clean = df.loc[keep].copy()
    clean["anchor_id"] = pd.to_numeric(clean["anchor_id"], errors="coerce").astype(int)
    clean["anchor_label"] = clean["anchor_id"].map(lambda x: ANCHORS[int(x)])
    clean["time_min"] = pd.to_numeric(clean["host_elapsed_s"], errors="coerce") / 60.0
    clean["range_used_mm"] = pd.to_numeric(clean[range_column], errors="coerce")
    clean["session_id"] = sid
    clean["capture"] = cap
    exclusions["rows_used"] = int(len(clean))

    rows: list[dict] = []
    for aid, g in clean.groupby("anchor_id"):
        if len(g) < min_rows:
            continue
        t = g["time_min"].to_numpy(dtype=float)
        r = g["range_used_mm"].to_numpy(dtype=float)
        med = float(np.nanmedian(r))
        residual = r - med
        fit = linear_fit(t, residual)
        duration_s = float((np.nanmax(t) - np.nanmin(t)) * 60.0)
        rows.append(
            {
                "session_id": sid,
                "capture": cap,
                "anchor_id": int(aid),
                "anchor": ANCHORS[int(aid)],
                "range_column": range_column,
                "n_rows": int(len(g)),
                "duration_s": duration_s,
                "median_range_mm": med,
                "mean_range_mm": float(np.nanmean(r)),
                "std_range_mm": float(np.nanstd(r)),
                "mad_range_mm": mad(r),
                "p95_abs_residual_mm": pctl(np.abs(residual), 95),
                "max_abs_residual_mm": float(np.nanmax(np.abs(residual))),
                "slope_mm_per_min": fit["slope_mm_per_min"],
                "slope_ci95_low_mm_per_min": fit["slope_ci95_low_mm_per_min"],
                "slope_ci95_high_mm_per_min": fit["slope_ci95_high_mm_per_min"],
                "slope_se_mm_per_min": fit["slope_se_mm_per_min"],
                "drift_over_capture_mm": fit["slope_mm_per_min"] * (duration_s / 60.0)
                if np.isfinite(fit["slope_mm_per_min"])
                else float("nan"),
                "r2": fit["r2"],
                "location": meta.get("location", ""),
                "height": meta.get("height", ""),
                "facing": meta.get("facing", ""),
                "source_tr_all": str(path),
            }
        )
    return rows, exclusions, clean


def make_anchor_summary(rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    out: list[dict] = []
    for anchor, g in df.groupby("anchor"):
        slopes = g["slope_mm_per_min"].to_numpy(dtype=float)
        drifts = g["drift_over_capture_mm"].to_numpy(dtype=float)
        abs_slopes = np.abs(slopes)
        worst_idx = int(np.nanargmax(abs_slopes)) if np.isfinite(abs_slopes).any() else 0
        worst = g.iloc[worst_idx]
        out.append(
            {
                "anchor": anchor,
                "sessions": int(len(g)),
                "median_slope_mm_per_min": float(np.nanmedian(slopes)),
                "median_abs_slope_mm_per_min": float(np.nanmedian(abs_slopes)),
                "p95_abs_slope_mm_per_min": pctl(abs_slopes, 95),
                "median_drift_over_capture_mm": float(np.nanmedian(drifts)),
                "median_abs_drift_over_capture_mm": float(np.nanmedian(np.abs(drifts))),
                "p95_abs_drift_over_capture_mm": pctl(np.abs(drifts), 95),
                "median_mad_range_mm": float(np.nanmedian(g["mad_range_mm"].to_numpy(dtype=float))),
                "median_p95_abs_residual_mm": float(np.nanmedian(g["p95_abs_residual_mm"].to_numpy(dtype=float))),
                "worst_session": worst["session_id"],
                "worst_slope_mm_per_min": float(worst["slope_mm_per_min"]),
                "worst_drift_over_capture_mm": float(worst["drift_over_capture_mm"]),
            }
        )
    return sorted(out, key=lambda r: r["anchor"])


def plot_slope_heatmap(rows: list[dict], out: Path) -> None:
    df = pd.DataFrame(rows)
    if df.empty:
        return
    pivot = df.pivot_table(index="anchor", columns="session_id", values="slope_mm_per_min", aggfunc="median")
    pivot = pivot.reindex(ANCHORS)
    fig, ax = plt.subplots(figsize=(14, 4.2), constrained_layout=True)
    arr = pivot.to_numpy(dtype=float)
    vmax = max(1.0, float(np.nanpercentile(np.abs(arr), 95))) if np.isfinite(arr).any() else 1.0
    im = ax.imshow(arr, aspect="auto", cmap="coolwarm", vmin=-vmax, vmax=vmax)
    ax.set_title("Static raw range drift slope per anchor/session")
    ax.set_xlabel("Static session")
    ax.set_ylabel("Anchor")
    ax.set_xticks(np.arange(len(pivot.columns)))
    ax.set_xticklabels(pivot.columns, rotation=55, ha="right", fontsize=8)
    ax.set_yticks(np.arange(len(pivot.index)))
    ax.set_yticklabels(pivot.index)
    fig.colorbar(im, ax=ax, label="slope mm/min")
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_slope_box(rows: list[dict], out: Path) -> None:
    df = pd.DataFrame(rows)
    if df.empty:
        return
    data = [df[df["anchor"] == a]["slope_mm_per_min"].dropna().to_numpy(dtype=float) for a in ANCHORS]
    fig, ax = plt.subplots(figsize=(9, 4.5), constrained_layout=True)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.boxplot(data, tick_labels=ANCHORS, showfliers=True)
    ax.set_title("Static raw range drift slope distribution")
    ax.set_xlabel("Anchor")
    ax.set_ylabel("slope mm/min")
    ax.grid(axis="y", alpha=0.25)
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_worst_timeseries(clean_by_path: dict[str, pd.DataFrame], rows: list[dict], out: Path, top_n: int) -> None:
    df = pd.DataFrame(rows)
    if df.empty:
        return
    df["abs_slope"] = np.abs(df["slope_mm_per_min"])
    worst = df.sort_values("abs_slope", ascending=False).head(top_n)
    n = len(worst)
    if n == 0:
        return
    cols = 2
    rows_n = int(math.ceil(n / cols))
    fig, axs = plt.subplots(rows_n, cols, figsize=(12, 3.0 * rows_n), constrained_layout=True)
    axes = np.asarray(axs).reshape(-1)
    for ax, (_, row) in zip(axes, worst.iterrows()):
        path = row["source_tr_all"]
        g = clean_by_path[path]
        sub = g[g["anchor_id"] == int(row["anchor_id"])].copy()
        if sub.empty:
            continue
        t = sub["time_min"].to_numpy(dtype=float)
        r = sub["range_used_mm"].to_numpy(dtype=float)
        residual = r - np.nanmedian(r)
        ax.scatter(t * 60.0, residual, s=4, alpha=0.35)
        slope = float(row["slope_mm_per_min"])
        intercept = float(np.nanmean(residual) - slope * np.nanmean(t))
        tt = np.array([np.nanmin(t), np.nanmax(t)])
        ax.plot(tt * 60.0, intercept + slope * tt, color="red", linewidth=1.2)
        ax.set_title(f"{row['session_id']} anchor {row['anchor']} slope {slope:.1f} mm/min")
        ax.set_xlabel("elapsed s")
        ax.set_ylabel("range residual mm")
        ax.grid(alpha=0.25)
    for ax in axes[n:]:
        ax.axis("off")
    fig.savefig(out, dpi=150)
    plt.close(fig)


def write_markdown_summary(path: Path, per_rows: list[dict], anchor_rows: list[dict], exclusion_rows: list[dict]) -> None:
    df = pd.DataFrame(per_rows)
    adf = pd.DataFrame(anchor_rows)
    lines = ["# Temporal / Thermal Drift Diagnostics\n\n"]
    lines.append("Source: static `tr_all.csv` raw per-anchor ranging rows.\n\n")
    lines.append("Method: for each static session and anchor, fit `range_mm - median(range_mm)` against elapsed minutes. This is a raw-link drift diagnostic, not a tag-position solver result.\n\n")
    if df.empty:
        lines.append("No valid rows were found.\n")
        path.write_text("".join(lines), encoding="utf-8")
        return
    total_sessions = int(df["session_id"].nunique())
    total_pairs = int(len(df))
    median_abs_slope = float(np.nanmedian(np.abs(df["slope_mm_per_min"].to_numpy(dtype=float))))
    p95_abs_slope = pctl(np.abs(df["slope_mm_per_min"].to_numpy(dtype=float)), 95)
    median_abs_drift = float(np.nanmedian(np.abs(df["drift_over_capture_mm"].to_numpy(dtype=float))))
    p95_abs_drift = pctl(np.abs(df["drift_over_capture_mm"].to_numpy(dtype=float)), 95)
    lines.append("## Headline\n\n")
    lines.append(f"- Static sessions analyzed: {total_sessions}\n")
    lines.append(f"- Anchor-session links analyzed: {total_pairs}\n")
    lines.append(f"- Median absolute drift slope: {median_abs_slope:.2f} mm/min\n")
    lines.append(f"- P95 absolute drift slope: {p95_abs_slope:.2f} mm/min\n")
    lines.append(f"- Median absolute drift over capture: {median_abs_drift:.2f} mm\n")
    lines.append(f"- P95 absolute drift over capture: {p95_abs_drift:.2f} mm\n\n")
    lines.append("Interpretation: compare drift-over-capture to static tag repeatability. A few-mm drift is negligible; tens of mm would be report-relevant.\n\n")
    lines.append("## Per-Anchor Summary\n\n")
    lines.append("| anchor | sessions | median_abs_slope_mm_min | p95_abs_slope_mm_min | median_abs_drift_mm | p95_abs_drift_mm | median_MAD_mm | worst_session | worst_slope_mm_min |\n")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |\n")
    for _, r in adf.iterrows():
        lines.append(
            f"| {r['anchor']} | {int(r['sessions'])} | {r['median_abs_slope_mm_per_min']:.2f} | "
            f"{r['p95_abs_slope_mm_per_min']:.2f} | {r['median_abs_drift_over_capture_mm']:.2f} | "
            f"{r['p95_abs_drift_over_capture_mm']:.2f} | {r['median_mad_range_mm']:.2f} | "
            f"{r['worst_session']} | {r['worst_slope_mm_per_min']:.2f} |\n"
        )
    worst = df.assign(abs_slope=np.abs(df["slope_mm_per_min"])).sort_values("abs_slope", ascending=False).head(10)
    lines.append("\n## Worst 10 Links By Absolute Slope\n\n")
    lines.append("| session | anchor | slope_mm_min | drift_capture_mm | r2 | p95_abs_residual_mm | facing | height |\n")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | --- | --- |\n")
    for _, r in worst.iterrows():
        lines.append(
            f"| {r['session_id']} | {r['anchor']} | {r['slope_mm_per_min']:.2f} | "
            f"{r['drift_over_capture_mm']:.2f} | {r['r2']:.3f} | {r['p95_abs_residual_mm']:.1f} | "
            f"{r.get('facing','')} | {r.get('height','')} |\n"
        )
    excl = pd.DataFrame(exclusion_rows)
    if not excl.empty:
        lines.append("\n## Row Accounting\n\n")
        lines.append(f"- Total rows: {int(excl['total_rows'].sum())}\n")
        lines.append(f"- Rows used: {int(excl['rows_used'].sum())}\n")
        for col in ["excluded_invalid", "excluded_bad_status", "excluded_bad_anchor", "excluded_missing_time_or_range", "excluded_nonpositive_range"]:
            lines.append(f"- {col}: {int(excl[col].sum())}\n")
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Temporal drift analysis for Erlangen official static captures.")
    parser.add_argument("--official-root", default="autopos_pipeline/28052026_Erlangen_Official")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--range-column", default="range_mm", choices=["range_mm", "raw_mm"])
    parser.add_argument("--min-rows-per-link", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--top-timeseries", type=int, default=8)
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else Path(__file__).resolve().parents[1]
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)

    captures_root = official_root / "captures/erlangen_20260528_optitrack"
    static_files = collect_static_files(captures_root)
    if not static_files:
        raise FileNotFoundError(f"no static tr_all.csv files under {captures_root}")
    layout_table = official_root / "solver/outputs/v1_to_v4_io_field_check/tables/static_all_captures.csv"
    metadata = load_static_metadata(layout_table)

    per_rows: list[dict] = []
    exclusion_rows: list[dict] = []
    clean_by_path: dict[str, pd.DataFrame] = {}
    for path in static_files:
        rows, exclusions, clean = analyze_file(path, range_column=args.range_column, min_rows=args.min_rows_per_link, metadata=metadata)
        per_rows.extend(rows)
        exclusion_rows.append(exclusions)
        clean_by_path[str(path)] = clean

    anchor_rows = make_anchor_summary(per_rows)
    write_csv(tables_dir / "temporal_drift_per_anchor_session.csv", per_rows)
    write_csv(tables_dir / "temporal_drift_anchor_summary.csv", anchor_rows)
    write_csv(tables_dir / "temporal_drift_exclusions.csv", exclusion_rows)
    write_markdown_summary(tables_dir / "temporal_drift_summary.md", per_rows, anchor_rows, exclusion_rows)
    plot_slope_heatmap(per_rows, figs_dir / "temporal_drift_slope_heatmap.png")
    plot_slope_box(per_rows, figs_dir / "temporal_drift_slope_boxplot.png")
    plot_worst_timeseries(clean_by_path, per_rows, figs_dir / "temporal_drift_worst_timeseries.png", args.top_timeseries)

    append_run_meta(
        out_dir,
        {
            "script": "temporal_drift_analysis.py",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "args": vars(args),
            "seed": args.seed,
            "axis_convention": {
                "layout_horizontal_axes": LAYOUT_HORIZONTAL_AXES,
                "layout_vertical_axis": LAYOUT_VERTICAL_AXIS,
                "layout_upper_layer_sign": LAYOUT_UPPER_LAYER_SIGN,
                "reported_height_mm": REPORTED_HEIGHT_EXPR,
                "optitrack_vertical_axis": OPTITRACK_VERTICAL_AXIS,
            },
            "captures_root": str(captures_root),
            "static_files": [str(p) for p in static_files],
            "static_file_sha256": {str(p): sha256_file(p) for p in static_files},
            "range_column": args.range_column,
            "outputs": [
                "tables/temporal_drift_per_anchor_session.csv",
                "tables/temporal_drift_anchor_summary.csv",
                "tables/temporal_drift_exclusions.csv",
                "tables/temporal_drift_summary.md",
                "figs/temporal_drift_slope_heatmap.png",
                "figs/temporal_drift_slope_boxplot.png",
                "figs/temporal_drift_worst_timeseries.png",
            ],
        },
    )
    print(
        f"[drift] sessions={len(static_files)} links={len(per_rows)} "
        f"summary={tables_dir / 'temporal_drift_summary.md'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
