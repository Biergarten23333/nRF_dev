#!/usr/bin/env python3
"""Cm-scale static-tag localization metrics.

This is a pure post-processing pass over already generated corrected static-tag
error tables. It does not rerun solvers, layout, DOP, MC, drift, or the
additional diagnostic analyses.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


THRESHOLDS_MM = [50, 80, 100, 200, 300]
OPTITRACK_VERTICAL_AXIS = "Y"


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
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {"runs": []}
        else:
            meta = {"runs": []}
        meta.setdefault("runs", []).append(entry)
        tmp = meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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


def fmt_mm(x: float) -> str:
    if x is None or not np.isfinite(x):
        return ""
    return f"{x:.1f}"


def fmt_pct(x: float) -> str:
    if x is None or not np.isfinite(x):
        return ""
    return f"{100.0 * x:.1f}%"


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |\n"]
    out.append("| " + " | ".join("---" for _ in headers) + " |\n")
    for row in rows:
        out.append("| " + " | ".join(row) + " |\n")
    return "".join(out)


def rmse(values: pd.Series | np.ndarray) -> float:
    arr = np.asarray(values, dtype=float)
    return float(np.sqrt(np.nanmean(arr * arr)))


def percentile(values: pd.Series | np.ndarray, q: float) -> float:
    return float(np.nanpercentile(np.asarray(values, dtype=float), q))


def eval_label(eval_id: str) -> str:
    labels = {
        "production_v4io_all8": "production v4-io/all8",
        "production_v4io_noG": "production v4-io/noG",
        "raw_v4io_T3_all8": "best-case raw v4-io/T3/all8",
        "raw_v4io_T4_all8": "deployment raw v4-io/T4/all8",
    }
    return labels.get(eval_id, eval_id)


def make_eval_frames(tables_dir: Path, official_root: Path) -> tuple[list[dict], dict[str, pd.DataFrame], dict[str, tuple[float, float, str]]]:
    prod_path = tables_dir / "tag_abs_errors_per_session.csv"
    raw_path = tables_dir / "tag_raw_replay_abs_errors_per_session.csv"
    static_path = official_root / "solver/outputs/v1_to_v4_io_field_check/tables/static_all_captures.csv"

    prod = pd.read_csv(prod_path)
    raw = pd.read_csv(raw_path)
    static = pd.read_csv(static_path)

    evals = [
        {
            "eval_id": "production_v4io_all8",
            "source_kind": "production_output",
            "version": "v4-io",
            "tag_method": "production",
            "eval_set": "all8",
            "label": eval_label("production_v4io_all8"),
            "best_case_flag": False,
            "primary_headline_flag": True,
            "df": prod[(prod["version"] == "v4-io") & (prod["eval_set"] == "all8")].copy(),
        },
        {
            "eval_id": "production_v4io_noG",
            "source_kind": "production_output",
            "version": "v4-io",
            "tag_method": "production",
            "eval_set": "noG",
            "label": eval_label("production_v4io_noG"),
            "best_case_flag": False,
            "primary_headline_flag": False,
            "df": prod[(prod["version"] == "v4-io") & (prod["eval_set"] == "noG")].copy(),
        },
        {
            "eval_id": "raw_v4io_T3_all8",
            "source_kind": "raw_replay",
            "version": "v4-io",
            "tag_method": "T3",
            "eval_set": "all8",
            "label": eval_label("raw_v4io_T3_all8"),
            "best_case_flag": True,
            "primary_headline_flag": False,
            "df": raw[(raw["version"] == "v4-io") & (raw["tag_method"] == "T3") & (raw["eval_set"] == "all8")].copy(),
        },
        {
            "eval_id": "raw_v4io_T4_all8",
            "source_kind": "raw_replay",
            "version": "v4-io",
            "tag_method": "T4",
            "eval_set": "all8",
            "label": eval_label("raw_v4io_T4_all8"),
            "best_case_flag": False,
            "primary_headline_flag": False,
            "df": raw[(raw["version"] == "v4-io") & (raw["tag_method"] == "T4") & (raw["eval_set"] == "all8")].copy(),
        },
    ]
    frames = {e["eval_id"]: e["df"] for e in evals}
    for entry in evals:
        if len(entry["df"]) == 0:
            raise RuntimeError(f"no rows for {entry['eval_id']}")

    prod_d3 = static[static["version"] == "v4-io"]["D3_std"].astype(float)
    repeatability = {
        "production_v4io_all8": (
            float(prod_d3.median()),
            float(prod_d3.quantile(0.95)),
            "solver/outputs/v1_to_v4_io_field_check/tables/static_all_captures.csv",
        ),
        "production_v4io_noG": (
            float(prod_d3.median()),
            float(prod_d3.quantile(0.95)),
            "same production v4-io tag cloud; noG changes anchor transform only",
        ),
    }
    for eval_id in ["raw_v4io_T3_all8", "raw_v4io_T4_all8"]:
        d3 = frames[eval_id]["d3_std_mm"].astype(float)
        repeatability[eval_id] = (
            float(d3.median()),
            float(d3.quantile(0.95)),
            "tables/tag_raw_replay_abs_errors_per_session.csv",
        )
    return evals, frames, repeatability


def build_tables(evals: list[dict], repeatability: dict[str, tuple[float, float, str]]) -> tuple[list[dict], list[dict], list[dict]]:
    metric_rows: list[dict] = []
    axis_rows: list[dict] = []
    outlier_rows: list[dict] = []

    for entry in evals:
        eval_id = entry["eval_id"]
        df = entry["df"]
        err3d = df["err_3d_mm"].astype(float)
        metric_values = {
            "mean_3d_error_mm": float(err3d.mean()),
            "rmse_3d_error_mm": rmse(err3d),
            "p50_3d_error_mm": percentile(err3d, 50),
            "p90_3d_error_mm": percentile(err3d, 90),
            "p95_3d_error_mm": percentile(err3d, 95),
            "p99_3d_error_mm": percentile(err3d, 99),
            "max_3d_error_mm": float(err3d.max()),
            "repeatability_d3_std_p50_mm": repeatability[eval_id][0],
            "repeatability_d3_std_p95_mm": repeatability[eval_id][1],
        }
        for metric, value in metric_values.items():
            metric_rows.append(
                {
                    "eval_id": eval_id,
                    "label": entry["label"],
                    "source_kind": entry["source_kind"],
                    "version": entry["version"],
                    "tag_method": entry["tag_method"],
                    "eval_set": entry["eval_set"],
                    "metric": metric,
                    "value_mm": value,
                    "unit": "mm",
                    "n_positions": int(len(df)),
                    "best_case_flag": bool(entry["best_case_flag"]),
                    "primary_headline_flag": bool(entry["primary_headline_flag"]),
                    "repeatability_source": repeatability[eval_id][2] if metric.startswith("repeatability") else "",
                    "note": "3D is primary; horizontal 2D is secondary only" if metric.endswith("3d_error_mm") else "",
                }
            )

        for component, col, role in [
            ("X", "err_x_mm", "OptiTrack horizontal X"),
            ("Y_vertical", "err_y_vertical_mm", "OptiTrack vertical Y"),
            ("Z", "err_z_mm", "OptiTrack horizontal Z"),
        ]:
            signed = df[col].astype(float)
            axis_rows.append(
                {
                    "eval_id": eval_id,
                    "label": entry["label"],
                    "source_kind": entry["source_kind"],
                    "version": entry["version"],
                    "tag_method": entry["tag_method"],
                    "eval_set": entry["eval_set"],
                    "component": component,
                    "role": role,
                    "signed_mean_bias_mm": float(signed.mean()),
                    "signed_std_mm": float(signed.std(ddof=1)),
                    "p95_abs_error_mm": percentile(np.abs(signed), 95),
                    "horizontal_2d_rmse_mm": "",
                    "horizontal_2d_p50_mm": "",
                    "horizontal_2d_p95_mm": "",
                    "axis_convention": "OptiTrack frame; Y is vertical",
                    "note": "signed bias is solved-minus-corrected-truth",
                }
            )
        horiz = df["err_horizontal_mm"].astype(float)
        axis_rows.append(
            {
                "eval_id": eval_id,
                "label": entry["label"],
                "source_kind": entry["source_kind"],
                "version": entry["version"],
                "tag_method": entry["tag_method"],
                "eval_set": entry["eval_set"],
                "component": "horizontal_XZ_2d",
                "role": "secondary 2D horizontal summary; not the primary localization metric",
                "signed_mean_bias_mm": "",
                "signed_std_mm": "",
                "p95_abs_error_mm": "",
                "horizontal_2d_rmse_mm": rmse(horiz),
                "horizontal_2d_p50_mm": percentile(horiz, 50),
                "horizontal_2d_p95_mm": percentile(horiz, 95),
                "axis_convention": "OptiTrack frame; horizontal plane is X/Z",
                "note": "2D is shown only as secondary; 3D and vertical Y remain explicit",
            }
        )

        for threshold in THRESHOLDS_MM:
            frac_exceed = float(np.mean(err3d > threshold))
            outlier_rows.append(
                {
                    "eval_id": eval_id,
                    "label": entry["label"],
                    "source_kind": entry["source_kind"],
                    "version": entry["version"],
                    "tag_method": entry["tag_method"],
                    "eval_set": entry["eval_set"],
                    "threshold_mm": threshold,
                    "fraction_exceeding": frac_exceed,
                    "percent_exceeding": 100.0 * frac_exceed,
                    "fraction_within_or_equal": 1.0 - frac_exceed,
                    "percent_within_or_equal": 100.0 * (1.0 - frac_exceed),
                    "n_positions": int(len(err3d)),
                    "count_exceeding": int(np.sum(err3d > threshold)),
                    "count_within_or_equal": int(np.sum(err3d <= threshold)),
                    "note": "cm-scale thresholds; metre-scale generic thresholds intentionally not used",
                }
            )
    return metric_rows, axis_rows, outlier_rows


def plot_cdf(path: Path, frames: dict[str, pd.DataFrame], metrics: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(7.2, 4.5), dpi=150)
    plot_ids = ["production_v4io_all8", "raw_v4io_T3_all8", "raw_v4io_T4_all8"]
    colors = {
        "production_v4io_all8": "#005f73",
        "raw_v4io_T3_all8": "#9b2226",
        "raw_v4io_T4_all8": "#ee9b00",
    }
    for eval_id in plot_ids:
        err = np.sort(frames[eval_id]["err_3d_mm"].astype(float).to_numpy())
        y = np.arange(1, len(err) + 1) / len(err)
        ax.step(err, y, where="post", color=colors[eval_id], lw=2, label=eval_label(eval_id))
        vals = metrics[metrics["eval_id"].eq(eval_id)].set_index("metric")["value_mm"]
        p50 = float(vals["p50_3d_error_mm"])
        p95 = float(vals["p95_3d_error_mm"])
        ax.plot([p50], [0.5], marker="o", color=colors[eval_id], ms=4)
        ax.plot([p95], [0.95], marker="s", color=colors[eval_id], ms=4)
        ax.annotate(f"P50 {p50:.0f}", (p50, 0.5), xytext=(5, -14), textcoords="offset points", fontsize=7, color=colors[eval_id])
        ax.annotate(f"P95 {p95:.0f}", (p95, 0.95), xytext=(5, 5), textcoords="offset points", fontsize=7, color=colors[eval_id])
    for threshold in THRESHOLDS_MM:
        ax.axvline(threshold, color="#9aa0a6", lw=0.8, alpha=0.45, ls="--")
        ax.text(threshold, 0.02, f"{threshold}", rotation=90, va="bottom", ha="right", fontsize=7, color="#5f6368")
    ax.set_xlabel("3D position error (mm)")
    ax.set_ylabel("Cumulative fraction")
    ax.set_ylim(0, 1.02)
    ax.set_xlim(left=0)
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    ax.set_title("Static tag 3D error CDF, corrected ground truth")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def plot_axis_bias(path: Path, axis_df: pd.DataFrame) -> None:
    prod = axis_df[
        axis_df["eval_id"].isin(["production_v4io_all8", "production_v4io_noG"])
        & axis_df["component"].isin(["X", "Y_vertical", "Z"])
    ].copy()
    components = ["X", "Y_vertical", "Z"]
    labels = ["X", "Y vertical", "Z"]
    sets = ["production_v4io_all8", "production_v4io_noG"]
    colors = ["#005f73", "#ca6702"]
    x = np.arange(len(components))
    width = 0.34

    fig, axes = plt.subplots(1, 2, figsize=(8.0, 4.2), dpi=150, sharex=True)
    for i, eval_id in enumerate(sets):
        g = prod[prod["eval_id"].eq(eval_id)].set_index("component")
        bias = [float(g.loc[c, "signed_mean_bias_mm"]) for c in components]
        p95 = [float(g.loc[c, "p95_abs_error_mm"]) for c in components]
        offset = (i - 0.5) * width
        axes[0].bar(x + offset, bias, width, color=colors[i], label=eval_label(eval_id))
        axes[1].bar(x + offset, p95, width, color=colors[i], label=eval_label(eval_id))
    axes[0].axhline(0, color="#333333", lw=0.8)
    axes[0].set_title("Signed mean bias")
    axes[0].set_ylabel("mm")
    axes[1].set_title("P95 absolute axis error")
    axes[1].set_ylabel("mm")
    for ax in axes:
        ax.set_xticks(x, labels)
        ax.grid(True, axis="y", alpha=0.25)
        ax.legend(frameon=False, fontsize=8)
    fig.suptitle("Production v4-io per-axis error, all8 vs noG")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def write_summary(path: Path, metrics: pd.DataFrame, axis_df: pd.DataFrame, outlier_df: pd.DataFrame) -> None:
    metric_piv = metrics.pivot_table(index="eval_id", columns="metric", values="value_mm", aggfunc="first")
    rows = []
    for eval_id in ["production_v4io_all8", "production_v4io_noG", "raw_v4io_T3_all8", "raw_v4io_T4_all8"]:
        r = metric_piv.loc[eval_id]
        rows.append(
            [
                eval_label(eval_id),
                fmt_mm(r["mean_3d_error_mm"]),
                fmt_mm(r["rmse_3d_error_mm"]),
                fmt_mm(r["p50_3d_error_mm"]),
                fmt_mm(r["p90_3d_error_mm"]),
                fmt_mm(r["p95_3d_error_mm"]),
                fmt_mm(r["p99_3d_error_mm"]),
                fmt_mm(r["max_3d_error_mm"]),
                fmt_mm(r["repeatability_d3_std_p50_mm"]),
                fmt_mm(r["repeatability_d3_std_p95_mm"]),
            ]
        )
    full_table = md_table(
        ["line", "mean", "RMSE", "P50", "P90", "P95", "P99", "max", "D3 std P50", "D3 std P95"],
        rows,
    )

    axis_rows = []
    for eval_id in ["production_v4io_all8", "production_v4io_noG"]:
        g = axis_df[axis_df["eval_id"].eq(eval_id)]
        for comp in ["X", "Y_vertical", "Z", "horizontal_XZ_2d"]:
            r = g[g["component"].eq(comp)].iloc[0]
            if comp == "horizontal_XZ_2d":
                axis_rows.append([eval_label(eval_id), "horizontal XZ 2D", "", "", "", fmt_mm(float(r["horizontal_2d_rmse_mm"])), fmt_mm(float(r["horizontal_2d_p50_mm"])), fmt_mm(float(r["horizontal_2d_p95_mm"]))])
            else:
                axis_rows.append([eval_label(eval_id), comp, fmt_mm(float(r["signed_mean_bias_mm"])), fmt_mm(float(r["signed_std_mm"])), fmt_mm(float(r["p95_abs_error_mm"])), "", "", ""])
    axis_table = md_table(
        ["line", "component", "signed bias", "signed std", "P95 abs", "2D RMSE", "2D P50", "2D P95"],
        axis_rows,
    )

    out_rows = []
    for eval_id in ["production_v4io_all8", "production_v4io_noG", "raw_v4io_T3_all8", "raw_v4io_T4_all8"]:
        g = outlier_df[outlier_df["eval_id"].eq(eval_id)].set_index("threshold_mm")
        out_rows.append(
            [
                eval_label(eval_id),
                fmt_pct(float(g.loc[50, "fraction_exceeding"])),
                fmt_pct(float(g.loc[80, "fraction_exceeding"])),
                fmt_pct(float(g.loc[100, "fraction_exceeding"])),
                fmt_pct(float(g.loc[200, "fraction_exceeding"])),
                fmt_pct(float(g.loc[300, "fraction_exceeding"])),
                fmt_pct(float(g.loc[50, "fraction_within_or_equal"])),
                fmt_pct(float(g.loc[80, "fraction_within_or_equal"])),
            ]
        )
    out_table = md_table(
        ["line", ">50", ">80", ">100", ">200", ">300", "<=50", "<=80"],
        out_rows,
    )

    prod_out = outlier_df[outlier_df["eval_id"].eq("production_v4io_all8")].set_index("threshold_mm")
    text = f"""### Localization metric set (cm-scale)

This metric pass is limited to the existing corrected static-tag error outputs. It
does not rerun solvers or regenerate layout, DOP, MC, drift, or the nine additional
diagnostics.

Excluded standard metrics: latency, update rate, drop rate, max gap, availability,
and jitter are not reported because this is an offline static replay, not a real-time
online stream. The static analogue of jitter is repeatability (`D3 std`), which is
cross-referenced below from existing outputs. 2D is also not promoted to the headline:
the system contribution is pure-UWB 3D self-calibration, so 3D remains primary and
OptiTrack Y vertical error is reported explicitly.

3D error percentile set, mm:

{full_table}

Per-axis signed bias and P95 absolute error, OptiTrack frame with Y vertical:

{axis_table}

Cm-scale 3D outlier rates:

{out_table}

![Static tag 3D error CDF](fig/tag_error_cdf.png)

![Static tag per-axis bias](fig/tag_error_per_axis_bias.png)

Read: production `v4-io/all8` is P50 {metric_piv.loc['production_v4io_all8', 'p50_3d_error_mm']:.1f} mm and P95 {metric_piv.loc['production_v4io_all8', 'p95_3d_error_mm']:.1f} mm. It has {100.0 * prod_out.loc[50, 'fraction_within_or_equal']:.1f}% of positions within 50 mm and {100.0 * prod_out.loc[80, 'fraction_within_or_equal']:.1f}% within 80 mm, but {100.0 * prod_out.loc[200, 'fraction_exceeding']:.1f}% above 200 mm and {100.0 * prod_out.loc[300, 'fraction_exceeding']:.1f}% above 300 mm. This matches the established radial/scale structure: the median is respectable, but the tail is not random isotropic noise and must stay visible in the report.
"""
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--analysis-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    official_root = args.official_root.resolve()
    analysis_root = args.analysis_root.resolve()
    tables_dir = analysis_root / "tables"
    figs_dir = analysis_root / "figs"

    evals, frames, repeatability = make_eval_frames(tables_dir, official_root)
    metric_rows, axis_rows, outlier_rows = build_tables(evals, repeatability)

    metrics_path = tables_dir / "tag_metrics_full.csv"
    axis_path = tables_dir / "tag_per_axis_bias.csv"
    outlier_path = tables_dir / "tag_outlier_rates.csv"
    summary_path = tables_dir / "tag_localization_metrics_summary.md"
    cdf_path = figs_dir / "tag_error_cdf.png"
    axis_fig_path = figs_dir / "tag_error_per_axis_bias.png"

    write_csv(metrics_path, metric_rows)
    write_csv(axis_path, axis_rows)
    write_csv(outlier_path, outlier_rows)

    metrics_df = pd.DataFrame(metric_rows)
    axis_df = pd.DataFrame(axis_rows)
    outlier_df = pd.DataFrame(outlier_rows)
    plot_cdf(cdf_path, frames, metrics_df)
    plot_axis_bias(axis_fig_path, axis_df)
    write_summary(summary_path, metrics_df, axis_df, outlier_df)

    for rel in ["reports/fig", "reports/to_be_discuess/fig"]:
        dest = analysis_root / rel
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cdf_path, dest / cdf_path.name)
        shutil.copy2(axis_fig_path, dest / axis_fig_path.name)

    outputs = [
        str(metrics_path.relative_to(analysis_root)),
        str(outlier_path.relative_to(analysis_root)),
        str(axis_path.relative_to(analysis_root)),
        str(summary_path.relative_to(analysis_root)),
        str(cdf_path.relative_to(analysis_root)),
        str(axis_fig_path.relative_to(analysis_root)),
        "reports/fig/tag_error_cdf.png",
        "reports/fig/tag_error_per_axis_bias.png",
        "reports/to_be_discuess/fig/tag_error_cdf.png",
        "reports/to_be_discuess/fig/tag_error_per_axis_bias.png",
    ]
    sources = [
        tables_dir / "tag_abs_errors_per_session.csv",
        tables_dir / "tag_raw_replay_abs_errors_per_session.csv",
        official_root / "solver/outputs/v1_to_v4_io_field_check/tables/static_all_captures.csv",
    ]
    append_run_meta(
        analysis_root,
        {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "script": str(Path(__file__).relative_to(analysis_root)),
            "args": {"official_root": str(official_root), "analysis_root": str(analysis_root)},
            "seed": None,
            "axis_convention": "OptiTrack frame; Y vertical. 3D primary; horizontal X/Z 2D secondary.",
            "source_hashes": {str(p.relative_to(analysis_root) if p.is_relative_to(analysis_root) else p): sha256_file(p) for p in sources},
            "outputs": outputs,
            "notes": [
                "Pure post-processing of existing corrected static-tag error outputs.",
                "No latency/availability/dropout/jitter metrics for offline static replay.",
                "Cm-scale thresholds are 50/80/100/200/300 mm.",
            ],
        },
    )
    prod_out = outlier_df[outlier_df["eval_id"].eq("production_v4io_all8")].set_index("threshold_mm")
    print(
        "[tag_localization_metrics] wrote "
        f"{len(outputs)} outputs; production within50={prod_out.loc[50, 'fraction_within_or_equal']:.3f}, "
        f"within80={prod_out.loc[80, 'fraction_within_or_equal']:.3f}, "
        f"over200={prod_out.loc[200, 'fraction_exceeding']:.3f}, "
        f"over300={prod_out.loc[300, 'fraction_exceeding']:.3f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
