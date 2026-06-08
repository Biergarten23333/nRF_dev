#!/usr/bin/env python3
"""Aggregate Phase 4 TRUEFULL L2/L16/L20 five-seed results.

The factory runners save summary and track-level metrics, not full trajectories.
This script ranks production A0 rows against Opti truth, compares fusion rows
with the same-P UWB control, and emits reproducible tables/plots.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
SIM_ROOT = THIS.parents[1]
RUN_BASE = SIM_ROOT / "runs" / "phase4_algorithm_factory"
OUT_BASE = SIM_ROOT / "runs" / "phase4_analysis"

RUNS = {
    ("L2", "S00"): RUN_BASE / "phase4_L2_singleI_TRUEFULL_seed0_1080ti_20260605T105528Z",
    ("L2", "S01"): RUN_BASE / "phase4_L2_TRUEFULL_RANKING_S01_2x1080ti_20260605T164047Z",
    ("L2", "S02"): RUN_BASE / "phase4_L2_TRUEFULL_RANKING_S02_2x1080ti_20260605T164047Z",
    ("L2", "S03"): RUN_BASE / "phase4_L2_TRUEFULL_RANKING_S03_2x1080ti_20260605T164047Z",
    ("L2", "S04"): RUN_BASE / "phase4_L2_TRUEFULL_RANKING_S04_2x1080ti_20260605T164047Z",
    ("L16", "S00"): RUN_BASE / "phase4_L16_TRUEFULL_REPLACE_L2_S00_2x1080ti_20260605T180143Z",
    ("L16", "S01"): RUN_BASE / "phase4_L16_TRUEFULL_REPLACE_L2_S01_2x1080ti_20260605T193141Z",
    ("L16", "S02"): RUN_BASE / "phase4_L16_TRUEFULL_REPLACE_L2_S02_2x1080ti_20260605T193141Z",
    ("L16", "S03"): RUN_BASE / "phase4_L16_TRUEFULL_REPLACE_L2_S03_2x1080ti_20260605T193141Z",
    ("L16", "S04"): RUN_BASE / "phase4_L16_TRUEFULL_REPLACE_L2_S04_2x1080ti_20260605T193141Z",
    ("L20", "S00"): RUN_BASE / "phase4_L20_TRUEFULL_REPLACE_L2_S00_2x1080ti_20260605T180145Z",
    ("L20", "S01"): RUN_BASE / "phase4_L20_TRUEFULL_REPLACE_L2_S01_2x1080ti_20260605T193141Z",
    ("L20", "S02"): RUN_BASE / "phase4_L20_TRUEFULL_REPLACE_L2_S02_2x1080ti_20260605T193141Z",
    ("L20", "S03"): RUN_BASE / "phase4_L20_TRUEFULL_REPLACE_L2_S03_2x1080ti_20260605T193141Z",
    ("L20", "S04"): RUN_BASE / "phase4_L20_TRUEFULL_REPLACE_L2_S04_2x1080ti_20260605T193141Z",
}

METRIC = "trackmedian_err3d_p95_mm"
SENSOR_LABEL = {
    "L2": "L2 MPU6050/JY61P-like",
    "L16": "L16 ICM-45686",
    "L20": "L20 Xsens MTi-3",
}


def fmt(value: object, digits: int = 1) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(f):
        return "nan"
    return f"{f:.{digits}f}"


def md_table(df: pd.DataFrame, columns: list[str], max_rows: int = 20) -> str:
    rows = df.head(max_rows).copy()
    if rows.empty:
        return ""
    out = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for _, row in rows.iterrows():
        vals = []
        for col in columns:
            val = row.get(col, "")
            vals.append(fmt(val) if isinstance(val, (float, np.floating)) else str(val))
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)


def load_runs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summaries = []
    tracks = []
    manifests = []
    for (sensor, seed), run_dir in RUNS.items():
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("phase_status") != "complete":
            raise RuntimeError(f"{run_dir.name} is not complete: {manifest.get('phase_status')}")
        s = pd.read_csv(run_dir / "tables" / "phase4_summary.csv")
        t = pd.read_csv(run_dir / "tables" / "phase4_track_metrics.csv")
        s["run_dir"] = run_dir.name
        t["run_dir"] = run_dir.name
        summaries.append(s)
        tracks.append(t)
        manifests.append(
            {
                "L": sensor,
                "seed_id": seed,
                "run_dir": run_dir.name,
                "elapsed_s": manifest.get("elapsed_s"),
                "rows": manifest.get("runnable_rows_completed"),
                "status": manifest.get("phase_status"),
            }
        )
    return pd.concat(summaries, ignore_index=True), pd.concat(tracks, ignore_index=True), pd.DataFrame(manifests)


def add_samep_deltas(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    baseline = out[(out["kind"] == "uwb_only") & (out["T"] == "T1")].copy()
    samep = (
        baseline.groupby(["L", "seed_id", "A", "P"], dropna=False)[METRIC]
        .median()
        .rename("sameP_uwb_p95_mm")
        .reset_index()
    )
    out = out.merge(samep, on=["L", "seed_id", "A", "P"], how="left")

    b0 = (
        baseline[(baseline["A"] == "A0") & (baseline["P"] == "P0")]
        .groupby(["L", "seed_id"], dropna=False)[METRIC]
        .median()
        .rename("b0_uwb_p95_mm")
        .reset_index()
    )
    out = out.merge(b0, on=["L", "seed_id"], how="left")

    out["sameP_delta_p95_mm"] = out["sameP_uwb_p95_mm"] - out[METRIC]
    out["sameP_ratio_p95"] = out[METRIC] / out["sameP_uwb_p95_mm"]
    out["b0_delta_p95_mm"] = out["b0_uwb_p95_mm"] - out[METRIC]
    out["b0_ratio_p95"] = out[METRIC] / out["b0_uwb_p95_mm"]
    return out


def aggregate_position(df: pd.DataFrame) -> pd.DataFrame:
    pos = df[(df["kind"].isin(["position_fusion", "uwb_only"])) & (df["A"] == "A0")].copy()
    pos_fusion = pos[pos["kind"] == "position_fusion"].copy()
    agg = (
        pos_fusion.groupby(["L", "A", "U", "P", "I", "T", "kind"], dropna=False)
        .agg(
            n=("experiment_id", "count"),
            p50_mean=("trackmedian_err3d_p50_mm", "mean"),
            p95_mean=(METRIC, "mean"),
            p95_std=(METRIC, "std"),
            rmse_mean=("trackmedian_err3d_rmse_mm", "mean"),
            legacy_xz_p95_mean=("trackmedian_horizontal_xz_p95_mm", "mean"),
            legacy_yvertical_p95_mean=("trackmedian_vertical_y_p95_mm", "mean"),
            sameP_uwb_p95_mean=("sameP_uwb_p95_mm", "mean"),
            sameP_delta_p95_mean=("sameP_delta_p95_mm", "mean"),
            sameP_improved_seed_fraction=("sameP_delta_p95_mm", lambda s: float((s > 0).mean())),
            b0_delta_p95_mean=("b0_delta_p95_mm", "mean"),
            radius_abs_mean=("trackmedian_radius_error_abs_mm", "mean"),
            thickness_p95_mean=("trackmedian_circle_thickness_p95_mm", "mean"),
            deltaR_rms_mean=("legacy_deltaR_error_rms_mm", "mean"),
        )
        .reset_index()
    )
    agg["sensor_label"] = agg["L"].map(SENSOR_LABEL).fillna(agg["L"])
    agg["experiment_short"] = (
        "X_A0_"
        + agg["U"].astype(str)
        + "_"
        + agg["P"].astype(str)
        + "_"
        + agg["L"].astype(str)
        + "_"
        + agg["I"].astype(str)
        + "_"
        + agg["T"].astype(str)
    )
    agg["accuracy_rank"] = agg["p95_mean"].rank(method="first", ascending=True).astype(int)
    agg["rescue_rank"] = agg["sameP_delta_p95_mean"].rank(method="first", ascending=False).astype(int)
    return agg


def aggregate_raw(df: pd.DataFrame) -> pd.DataFrame:
    raw = df[(df["kind"] == "range_fusion") & (df["A"] == "A0")].copy()
    agg = (
        raw.groupby(["L", "A", "R", "I", "T", "kind"], dropna=False)
        .agg(
            n=("experiment_id", "count"),
            p50_mean=("trackmedian_err3d_p50_mm", "mean"),
            p95_mean=(METRIC, "mean"),
            p95_std=(METRIC, "std"),
            rmse_mean=("trackmedian_err3d_rmse_mm", "mean"),
            legacy_xz_p95_mean=("trackmedian_horizontal_xz_p95_mm", "mean"),
            legacy_yvertical_p95_mean=("trackmedian_vertical_y_p95_mm", "mean"),
            b0_uwb_p95_mean=("b0_uwb_p95_mm", "mean"),
            b0_delta_p95_mean=("b0_delta_p95_mm", "mean"),
            b0_improved_seed_fraction=("b0_delta_p95_mm", lambda s: float((s > 0).mean())),
            radius_abs_mean=("trackmedian_radius_error_abs_mm", "mean"),
            thickness_p95_mean=("trackmedian_circle_thickness_p95_mm", "mean"),
            deltaR_rms_mean=("legacy_deltaR_error_rms_mm", "mean"),
        )
        .reset_index()
    )
    agg["sensor_label"] = agg["L"].map(SENSOR_LABEL).fillna(agg["L"])
    agg["experiment_short"] = (
        "X_A0_"
        + agg["R"].astype(str)
        + "_"
        + agg["L"].astype(str)
        + "_"
        + agg["I"].astype(str)
        + "_"
        + agg["T"].astype(str)
    )
    return agg


def baseline_by_p(df: pd.DataFrame) -> pd.DataFrame:
    b = df[(df["kind"] == "uwb_only") & (df["T"] == "T1") & (df["A"] == "A0")].copy()
    return (
        b.groupby(["P"], dropna=False)
        .agg(
            p50_mean=("trackmedian_err3d_p50_mm", "mean"),
            p95_mean=(METRIC, "mean"),
            rmse_mean=("trackmedian_err3d_rmse_mm", "mean"),
            radius_abs_mean=("trackmedian_radius_error_abs_mm", "mean"),
            thickness_p95_mean=("trackmedian_circle_thickness_p95_mm", "mean"),
            deltaR_rms_mean=("legacy_deltaR_error_rms_mm", "mean"),
        )
        .reset_index()
        .sort_values("p95_mean")
    )


def matched_sensor_table(position: pd.DataFrame) -> pd.DataFrame:
    best = position.sort_values(["p95_mean", "sameP_delta_p95_mean"], ascending=[True, False]).iloc[0]
    keys = {"A": best["A"], "U": best["U"], "P": best["P"], "I": best["I"], "T": best["T"]}
    m = position.copy()
    for k, v in keys.items():
        m = m[m[k] == v]
    return m.sort_values("L")


def plot_top_position(position: pd.DataFrame, out: Path) -> None:
    top = position.sort_values(["p95_mean", "sameP_delta_p95_mean"], ascending=[True, False]).head(15).copy()
    fig, ax = plt.subplots(figsize=(12, 6))
    labels = top["experiment_short"].tolist()
    x = np.arange(len(top))
    ax.bar(x, top["p95_mean"], yerr=top["p95_std"].fillna(0), color="#5e81ac", alpha=0.9, label="fusion P95")
    ax.scatter(x, top["sameP_uwb_p95_mean"], color="#bf616a", s=28, label="same-P UWB P95")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("track-median 3D P95 error (mm)")
    ax.set_title("Top A0 production position-fusion rows vs same-P UWB")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=180)
    plt.close(fig)


def plot_sensor_match(match: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    labels = match["L"].tolist()
    for ax, col, title in [
        (axes[0], "p95_mean", "3D P95 lower is better"),
        (axes[1], "sameP_delta_p95_mean", "same-P improvement higher is better"),
        (axes[2], "thickness_p95_mean", "ROTO thickness lower is better"),
    ]:
        ax.bar(labels, match[col], color=["#a3be8c", "#88c0d0", "#b48ead"][: len(labels)])
        ax.set_title(title)
        ax.set_ylabel("mm")
        ax.grid(axis="y", alpha=0.25)
    fig.suptitle("Matched best A0/U4/P/I/T row across L2, L16, L20")
    fig.tight_layout()
    fig.savefig(out, dpi=180)
    plt.close(fig)


def plot_heatmaps(position: pd.DataFrame, out: Path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), sharey=True)
    sensors = ["L2", "L16", "L20"]
    for ax, sensor in zip(axes, sensors):
        sub = position[(position["L"] == sensor) & (position["T"] == "T2")].copy()
        piv = sub.pivot_table(index="I", columns="P", values="p95_mean", aggfunc="min")
        im = ax.imshow(piv.to_numpy(), cmap="viridis_r", aspect="auto")
        ax.set_title(f"{sensor} T2 min P95")
        ax.set_xticks(np.arange(len(piv.columns)))
        ax.set_xticklabels(piv.columns)
        ax.set_yticks(np.arange(len(piv.index)))
        ax.set_yticklabels(piv.index)
        for i in range(piv.shape[0]):
            for j in range(piv.shape[1]):
                val = piv.iloc[i, j]
                if math.isfinite(float(val)):
                    ax.text(j, i, f"{val:.0f}", ha="center", va="center", color="white", fontsize=7)
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.suptitle("A0 position-fusion T2 P/I surface, 5-seed mean P95 mm")
    fig.tight_layout()
    fig.savefig(out, dpi=180)
    plt.close(fig)


def plot_raw(raw: pd.DataFrame, out: Path) -> None:
    top = raw.sort_values(["p95_mean", "b0_delta_p95_mean"], ascending=[True, False]).head(12).copy()
    fig, ax = plt.subplots(figsize=(11, 5))
    x = np.arange(len(top))
    ax.bar(x, top["p95_mean"], color="#d08770", label="raw-range fusion P95")
    ax.scatter(x, top["b0_uwb_p95_mean"], color="#2e3440", s=25, label="B0 UWB P95")
    ax.set_xticks(x)
    ax.set_xticklabels(top["experiment_short"], rotation=60, ha="right", fontsize=8)
    ax.set_ylabel("track-median 3D P95 error (mm)")
    ax.set_title("Best raw-range branch rows are still worse than B0 in current proxy")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=180)
    plt.close(fig)


def plot_track_distribution(tracks: pd.DataFrame, position: pd.DataFrame, out: Path) -> None:
    best = position.sort_values(["p95_mean", "sameP_delta_p95_mean"], ascending=[True, False]).iloc[0]
    exp = best["experiment_short"]
    # Baseline is same-P UWB; T1 is repeated for every I, so use I0.
    baseline_exp = f"X_A0_U4_{best['P']}_{best['L']}_I0_T1"
    sub = tracks[tracks["experiment_id"].isin([exp, baseline_exp])].copy()
    sub["row"] = np.where(sub["experiment_id"] == exp, "fusion", "same-P UWB")
    vals = [sub[sub["row"] == "same-P UWB"]["err3d_p95_mm"].dropna(), sub[sub["row"] == "fusion"]["err3d_p95_mm"].dropna()]
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.boxplot(vals, labels=["same-P UWB", exp], showfliers=True)
    ax.set_ylabel("per-track 3D P95 error (mm)")
    ax.set_title("Per-track P95 distribution for best production row")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=180)
    plt.close(fig)


def main() -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = OUT_BASE / f"l2_l16_l20_truefull_5seed_opti_truth_{stamp}"
    table_dir = out_dir / "tables"
    fig_dir = out_dir / "figs"
    report_dir = out_dir / "reports"
    for p in [table_dir, fig_dir, report_dir]:
        p.mkdir(parents=True, exist_ok=True)

    summary, tracks, manifests = load_runs()
    summary = add_samep_deltas(summary)
    position = aggregate_position(summary)
    raw = aggregate_raw(summary)
    baseline_p = baseline_by_p(summary)
    matched = matched_sensor_table(position)

    position_accuracy = position.sort_values(["p95_mean", "sameP_delta_p95_mean"], ascending=[True, False]).reset_index(drop=True)
    position_rescue = position.sort_values(["sameP_delta_p95_mean", "p95_mean"], ascending=[False, True]).reset_index(drop=True)
    raw_rank = raw.sort_values(["p95_mean", "b0_delta_p95_mean"], ascending=[True, False]).reset_index(drop=True)
    top_by_sensor = (
        position_accuracy.sort_values(["L", "p95_mean"])
        .groupby("L", as_index=False)
        .head(5)
        .sort_values(["L", "p95_mean"])
    )

    summary.to_csv(table_dir / "all_seed_summary_with_deltas.csv", index=False)
    tracks.to_csv(table_dir / "all_seed_track_metrics.csv", index=False)
    manifests.to_csv(table_dir / "source_run_manifests.csv", index=False)
    position_accuracy.to_csv(table_dir / "production_A0_position_ranking_by_accuracy.csv", index=False)
    position_rescue.to_csv(table_dir / "production_A0_position_ranking_by_sameP_rescue.csv", index=False)
    raw_rank.to_csv(table_dir / "production_A0_raw_range_ranking.csv", index=False)
    baseline_p.to_csv(table_dir / "production_A0_uwb_baseline_by_P.csv", index=False)
    matched.to_csv(table_dir / "matched_best_combo_L2_L16_L20.csv", index=False)
    top_by_sensor.to_csv(table_dir / "top5_by_sensor.csv", index=False)

    plot_top_position(position_accuracy, fig_dir / "01_top_A0_position_vs_sameP_UWB.png")
    plot_sensor_match(matched, fig_dir / "02_matched_best_combo_sensor_comparison.png")
    plot_heatmaps(position_accuracy, fig_dir / "03_T2_P_I_heatmap_by_sensor.png")
    plot_raw(raw_rank, fig_dir / "04_raw_range_branch_vs_B0.png")
    plot_track_distribution(tracks, position_accuracy, fig_dir / "05_best_row_track_distribution.png")

    best_acc = position_accuracy.iloc[0]
    best_rescue = position_rescue.iloc[0]
    best_raw = raw_rank.iloc[0]

    report = [
        "# Phase 4 L2/L16/L20 TRUEFULL 5-Seed Opti-Truth Analysis",
        "",
        f"Generated UTC: `{datetime.now(UTC).isoformat()}`",
        "",
        "## Scope",
        "",
        "Inputs:",
        "",
        "- sensors: L2, L16, L20",
        "- seeds: S00-S04 for each sensor",
        "- rows per seed: 1377 runnable rows, 5292 declared rows, 3915 recorded exclusions",
        "- evaluation truth: Opti/Vicon trajectory in the official ROTO sample tables",
        "",
        "Primary production scope is A0. A1/A2/A3 are controls/oracle layouts and are not used for the production recommendation.",
        "",
        "Important metric note: stored factory summaries contain legacy `horizontal_xz` and `vertical_y` split fields. This report does not relabel those fields as XY/Z. The primary ranking uses 3D Opti-truth metrics plus same-P improvement and ROTO shape metrics.",
        "",
        "## Headline",
        "",
        f"Best production accuracy row: `{best_acc['experiment_short']}`.",
        "",
        f"- 5-seed mean track-median 3D P50/P95/RMSE = {fmt(best_acc['p50_mean'])} / {fmt(best_acc['p95_mean'])} / {fmt(best_acc['rmse_mean'])} mm",
        f"- same-P UWB P95 = {fmt(best_acc['sameP_uwb_p95_mean'])} mm",
        f"- same-P P95 improvement = {fmt(best_acc['sameP_delta_p95_mean'])} mm",
        f"- B0 P0 P95 improvement = {fmt(best_acc['b0_delta_p95_mean'])} mm",
        f"- ROTO radius abs / band P95 = {fmt(best_acc['radius_abs_mean'])} / {fmt(best_acc['thickness_p95_mean'])} mm",
        "",
        f"Best rescue-from-raw-UWB row: `{best_rescue['experiment_short']}`.",
        "",
        f"- 5-seed mean track-median 3D P95 = {fmt(best_rescue['p95_mean'])} mm",
        f"- same-P P95 improvement = {fmt(best_rescue['sameP_delta_p95_mean'])} mm",
        "",
        "Raw-range branch status: not recommended in the current proxy implementation.",
        "",
        f"- best raw row `{best_raw['experiment_short']}` has P95 = {fmt(best_raw['p95_mean'])} mm",
        f"- B0 delta = {fmt(best_raw['b0_delta_p95_mean'])} mm, so it is worse than B0",
        "",
        "## Production A0 UWB Baselines By P",
        "",
        md_table(baseline_p, ["P", "p50_mean", "p95_mean", "rmse_mean", "radius_abs_mean", "thickness_p95_mean", "deltaR_rms_mean"], 10),
        "",
        "## Top Production Rows By Accuracy",
        "",
        md_table(
            position_accuracy,
            [
                "experiment_short",
                "n",
                "p50_mean",
                "p95_mean",
                "p95_std",
                "rmse_mean",
                "sameP_uwb_p95_mean",
                "sameP_delta_p95_mean",
                "sameP_improved_seed_fraction",
                "b0_delta_p95_mean",
                "radius_abs_mean",
                "thickness_p95_mean",
            ],
            20,
        ),
        "",
        "## Top Production Rows By Same-P Rescue",
        "",
        md_table(
            position_rescue,
            [
                "experiment_short",
                "n",
                "p95_mean",
                "sameP_uwb_p95_mean",
                "sameP_delta_p95_mean",
                "sameP_improved_seed_fraction",
                "b0_delta_p95_mean",
                "radius_abs_mean",
                "thickness_p95_mean",
            ],
            20,
        ),
        "",
        "## Matched Best Combo Across Sensors",
        "",
        md_table(
            matched,
            [
                "experiment_short",
                "n",
                "p50_mean",
                "p95_mean",
                "p95_std",
                "rmse_mean",
                "sameP_delta_p95_mean",
                "b0_delta_p95_mean",
                "radius_abs_mean",
                "thickness_p95_mean",
            ],
            10,
        ),
        "",
        "## Top Five Per Sensor",
        "",
        md_table(
            top_by_sensor,
            [
                "L",
                "experiment_short",
                "p50_mean",
                "p95_mean",
                "p95_std",
                "sameP_delta_p95_mean",
                "radius_abs_mean",
                "thickness_p95_mean",
            ],
            20,
        ),
        "",
        "## Raw-Range Branch",
        "",
        md_table(
            raw_rank,
            [
                "experiment_short",
                "n",
                "p50_mean",
                "p95_mean",
                "p95_std",
                "b0_uwb_p95_mean",
                "b0_delta_p95_mean",
                "b0_improved_seed_fraction",
                "radius_abs_mean",
                "thickness_p95_mean",
            ],
            15,
        ),
        "",
        "## Figures",
        "",
        "- `figs/01_top_A0_position_vs_sameP_UWB.png`",
        "- `figs/02_matched_best_combo_sensor_comparison.png`",
        "- `figs/03_T2_P_I_heatmap_by_sensor.png`",
        "- `figs/04_raw_range_branch_vs_B0.png`",
        "- `figs/05_best_row_track_distribution.png`",
        "",
        "## Outputs",
        "",
        "- `tables/production_A0_position_ranking_by_accuracy.csv`",
        "- `tables/production_A0_position_ranking_by_sameP_rescue.csv`",
        "- `tables/production_A0_raw_range_ranking.csv`",
        "- `tables/matched_best_combo_L2_L16_L20.csv`",
        "- `tables/top5_by_sensor.csv`",
        "- `tables/all_seed_summary_with_deltas.csv`",
        "- `tables/all_seed_track_metrics.csv`",
    ]

    report_path = report_dir / "PHASE4_L2_L16_L20_TRUEFULL_5SEED_ANALYSIS.md"
    report_path.write_text("\n".join(report), encoding="utf-8")
    manifest = {
        "created_utc": datetime.now(UTC).isoformat(),
        "status": "complete",
        "run_count": len(RUNS),
        "sensors": ["L2", "L16", "L20"],
        "seeds": ["S00", "S01", "S02", "S03", "S04"],
        "report": str(report_path.relative_to(out_dir)),
        "best_accuracy_experiment": str(best_acc["experiment_short"]),
        "best_rescue_experiment": str(best_rescue["experiment_short"]),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"out_dir": str(out_dir), **manifest}, indent=2), flush=True)


if __name__ == "__main__":
    main()
