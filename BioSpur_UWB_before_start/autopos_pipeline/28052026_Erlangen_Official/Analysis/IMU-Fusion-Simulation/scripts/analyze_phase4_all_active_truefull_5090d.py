#!/usr/bin/env python3
"""Aggregate the 5090D Phase 4 all-active-L TRUEFULL and stress run."""

from __future__ import annotations

import argparse
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
STRESS_BASE = SIM_ROOT / "runs" / "phase4_stress"
OUT_BASE = SIM_ROOT / "runs" / "phase4_analysis"

ACTIVE_SENSORS = ["L0", "L1", "L2", "L3", "L4", "L5", "L7", "L8", "L10", "L11", "L12", "L13", "L14", "L15", "L16", "L17", "L18", "L19"]
SEEDS = [f"S{i:02d}" for i in range(5)]
METRIC = "trackmedian_err3d_p95_mm"


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


def run_dir_for(sensor: str, seed: str, stamp: str) -> Path:
    return RUN_BASE / f"phase4_{sensor}_TRUEFULL_{seed}_5090D_DUALLANE_{stamp}"


def load_truefull(stamp: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summaries: list[pd.DataFrame] = []
    tracks: list[pd.DataFrame] = []
    manifests: list[dict] = []
    missing: list[str] = []
    incomplete: list[str] = []

    for sensor in ACTIVE_SENSORS:
        for seed in SEEDS:
            run_dir = run_dir_for(sensor, seed, stamp)
            manifest_path = run_dir / "manifest.json"
            summary_path = run_dir / "tables" / "phase4_summary.csv"
            track_path = run_dir / "tables" / "phase4_track_metrics.csv"
            if not manifest_path.exists() or not summary_path.exists() or not track_path.exists():
                missing.append(f"{sensor}/{seed}")
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("phase_status") != "complete":
                incomplete.append(f"{sensor}/{seed}:{manifest.get('phase_status')}")
                continue
            summary = pd.read_csv(summary_path)
            track = pd.read_csv(track_path)
            summary["run_dir"] = run_dir.name
            track["run_dir"] = run_dir.name
            summaries.append(summary)
            tracks.append(track)
            manifests.append(
                {
                    "L": sensor,
                    "seed_id": seed,
                    "run_dir": run_dir.name,
                    "elapsed_s": manifest.get("elapsed_s"),
                    "declared_rows": manifest.get("declared_full_rows") or manifest.get("declared_rows"),
                    "runnable_rows": manifest.get("runnable_rows_completed") or manifest.get("runnable_rows"),
                    "excluded_rows": manifest.get("excluded_rows"),
                    "raw_backend": manifest.get("raw_backend", "cpu"),
                    "raw_device": manifest.get("raw_device", ""),
                    "raw_gpu_workers": manifest.get("raw_gpu_workers", ""),
                    "status": manifest.get("phase_status"),
                }
            )

    if missing or incomplete:
        raise RuntimeError(f"TRUEFULL not ready; missing={missing}; incomplete={incomplete}")
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
    pos = df[(df["kind"] == "position_fusion") & (df["A"] == "A0")].copy()
    agg = (
        pos.groupby(["L", "A", "U", "P", "I", "T", "kind"], dropna=False)
        .agg(
            n=("experiment_id", "count"),
            p50_mean=("trackmedian_err3d_p50_mm", "mean"),
            p95_mean=(METRIC, "mean"),
            p95_std=(METRIC, "std"),
            rmse_mean=("trackmedian_err3d_rmse_mm", "mean"),
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
            b0_uwb_p95_mean=("b0_uwb_p95_mm", "mean"),
            b0_delta_p95_mean=("b0_delta_p95_mm", "mean"),
            b0_improved_seed_fraction=("b0_delta_p95_mm", lambda s: float((s > 0).mean())),
            radius_abs_mean=("trackmedian_radius_error_abs_mm", "mean"),
            thickness_p95_mean=("trackmedian_circle_thickness_p95_mm", "mean"),
        )
        .reset_index()
    )
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


def load_stress(stress_run_id: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    run_dir = STRESS_BASE / stress_run_id
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    robust = pd.read_csv(run_dir / "tables" / "stress_robust_ranking.csv")
    by_case = pd.read_csv(run_dir / "tables" / "stress_by_case_ranking.csv")
    best = pd.read_csv(run_dir / "tables" / "stress_best_by_sensor_case.csv")
    return robust, by_case, best, manifest


def plot_best_by_sensor(top_by_sensor: pd.DataFrame, out: Path) -> None:
    best = top_by_sensor.sort_values(["L", "p95_mean"]).groupby("L", as_index=False).head(1)
    best = best.sort_values("p95_mean")
    fig, ax = plt.subplots(figsize=(13, 6))
    ax.bar(best["L"], best["p95_mean"], color="#5e81ac")
    ax.set_ylabel("best 5-seed mean 3D P95 (mm)")
    ax.set_title("Best TRUEFULL production row per active sensor")
    ax.grid(axis="y", alpha=0.25)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(out, dpi=180)
    plt.close(fig)


def plot_top_global(position_rank: pd.DataFrame, out: Path) -> None:
    top = position_rank.head(20).iloc[::-1]
    fig, ax = plt.subplots(figsize=(10, 8))
    y = np.arange(len(top))
    ax.barh(y, top["p95_mean"], color="#88c0d0", label="fusion P95")
    ax.scatter(top["sameP_uwb_p95_mean"], y, color="#bf616a", s=24, label="same-P UWB")
    ax.set_yticks(y)
    ax.set_yticklabels(top["experiment_short"], fontsize=8)
    ax.set_xlabel("track-median 3D P95 error (mm)")
    ax.set_title("Top TRUEFULL production rows across all active sensors")
    ax.grid(axis="x", alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=180)
    plt.close(fig)


def plot_stress_top(robust: pd.DataFrame, out: Path) -> None:
    top = robust.head(20).iloc[::-1]
    fig, ax = plt.subplots(figsize=(10, 8))
    labels = top["L"].astype(str) + "/" + top["P"].astype(str) + "/" + top["I"].astype(str) + "/" + top["T"].astype(str)
    ax.barh(np.arange(len(top)), top["p95_worst"], color="#d08770")
    ax.set_yticks(np.arange(len(top)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("worst stress 3D P95 (mm)")
    ax.set_title("Top robust stress candidates")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out, dpi=180)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze 5090D Phase 4 all-active-L TRUEFULL and stress outputs.")
    parser.add_argument("--stamp", default="20260606T221157Z")
    parser.add_argument("--stress-run-id", default="phase4_FULLSTRESS_ALLL_18L_5seed_allP_allI_allT_DUALLANE_20260606T221157Z")
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()

    out_id = args.run_id or f"phase4_all_active_18L_5seed_5090D_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir = OUT_BASE / out_id
    table_dir = out_dir / "tables"
    fig_dir = out_dir / "figs"
    report_dir = out_dir / "reports"
    for p in [table_dir, fig_dir, report_dir]:
        p.mkdir(parents=True, exist_ok=True)

    summary, tracks, manifests = load_truefull(args.stamp)
    summary = add_samep_deltas(summary)
    position = aggregate_position(summary)
    raw = aggregate_raw(summary)
    stress_robust, stress_by_case, stress_best, stress_manifest = load_stress(args.stress_run_id)

    position_rank = position.sort_values(["p95_mean", "sameP_delta_p95_mean"], ascending=[True, False]).reset_index(drop=True)
    rescue_rank = position.sort_values(["sameP_delta_p95_mean", "p95_mean"], ascending=[False, True]).reset_index(drop=True)
    raw_rank = raw.sort_values(["p95_mean", "b0_delta_p95_mean"], ascending=[True, False]).reset_index(drop=True)
    top_by_sensor = position_rank.groupby("L", as_index=False).head(5).sort_values(["L", "p95_mean"]).reset_index(drop=True)

    summary.to_csv(table_dir / "all_truefull_summary_with_deltas.csv", index=False)
    tracks.to_csv(table_dir / "all_truefull_track_metrics.csv", index=False)
    manifests.to_csv(table_dir / "truefull_source_run_manifests.csv", index=False)
    position_rank.to_csv(table_dir / "all_active_position_ranking_by_accuracy.csv", index=False)
    rescue_rank.to_csv(table_dir / "all_active_position_ranking_by_sameP_rescue.csv", index=False)
    raw_rank.to_csv(table_dir / "all_active_raw_range_ranking.csv", index=False)
    top_by_sensor.to_csv(table_dir / "top5_position_by_sensor.csv", index=False)
    stress_robust.to_csv(table_dir / "stress_robust_ranking.csv", index=False)
    stress_by_case.to_csv(table_dir / "stress_by_case_ranking.csv", index=False)
    stress_best.to_csv(table_dir / "stress_best_by_sensor_case.csv", index=False)

    plot_top_global(position_rank, fig_dir / "01_top_truefull_position_all_active.png")
    plot_best_by_sensor(top_by_sensor, fig_dir / "02_best_truefull_by_sensor.png")
    plot_stress_top(stress_robust, fig_dir / "03_top_stress_robust_candidates.png")

    best_acc = position_rank.iloc[0]
    best_rescue = rescue_rank.iloc[0]
    best_raw = raw_rank.iloc[0]
    best_stress = stress_robust.iloc[0]

    report = [
        "# Phase 4 All-Active-L 5090D Analysis",
        "",
        f"Generated UTC: `{datetime.now(UTC).isoformat()}`",
        "",
        "## Scope",
        "",
        f"- TRUEFULL sensors: `{', '.join(ACTIVE_SENSORS)}`",
        f"- TRUEFULL seeds: `{', '.join(SEEDS)}`",
        f"- TRUEFULL runs loaded: `{len(manifests)}`",
        f"- TRUEFULL summary rows: `{len(summary)}`",
        f"- Stress run: `{args.stress_run_id}`",
        f"- Stress bundle jobs: `{stress_manifest.get('bundle_jobs')}`",
        f"- Stress fusion rows: `{stress_manifest.get('fusion_rows')}`",
        "",
        "## Headline",
        "",
        f"Best TRUEFULL production accuracy row: `{best_acc['experiment_short']}`.",
        "",
        f"- 5-seed mean 3D P50/P95/RMSE = {fmt(best_acc['p50_mean'])} / {fmt(best_acc['p95_mean'])} / {fmt(best_acc['rmse_mean'])} mm",
        f"- same-P UWB P95 = {fmt(best_acc['sameP_uwb_p95_mean'])} mm",
        f"- same-P P95 improvement = {fmt(best_acc['sameP_delta_p95_mean'])} mm",
        f"- B0 P95 improvement = {fmt(best_acc['b0_delta_p95_mean'])} mm",
        f"- ROTO radius abs / band P95 = {fmt(best_acc['radius_abs_mean'])} / {fmt(best_acc['thickness_p95_mean'])} mm",
        "",
        f"Best same-P rescue row: `{best_rescue['experiment_short']}`.",
        "",
        f"- 5-seed mean 3D P95 = {fmt(best_rescue['p95_mean'])} mm",
        f"- same-P P95 improvement = {fmt(best_rescue['sameP_delta_p95_mean'])} mm",
        "",
        f"Best stress-robust row: `{best_stress['L']}/{best_stress['P']}/{best_stress['I']}/{best_stress['T']}`.",
        "",
        f"- stress robust score = {fmt(best_stress.get('robust_score'))}",
        f"- mean/worst stress P95 = {fmt(best_stress.get('p95_mean'))} / {fmt(best_stress.get('p95_worst'))} mm",
        "",
        "Raw-range branch status: keep as diagnostic unless later visual audit says otherwise.",
        "",
        f"- best raw row `{best_raw['experiment_short']}` has P95 = {fmt(best_raw['p95_mean'])} mm",
        f"- B0 delta = {fmt(best_raw['b0_delta_p95_mean'])} mm",
        "",
        "## Top TRUEFULL Production Rows",
        "",
        md_table(position_rank, ["experiment_short", "n", "p50_mean", "p95_mean", "p95_std", "rmse_mean", "sameP_uwb_p95_mean", "sameP_delta_p95_mean", "sameP_improved_seed_fraction", "b0_delta_p95_mean", "radius_abs_mean", "thickness_p95_mean"], 25),
        "",
        "## Top TRUEFULL Rows Per Sensor",
        "",
        md_table(top_by_sensor, ["L", "experiment_short", "p50_mean", "p95_mean", "p95_std", "sameP_delta_p95_mean", "radius_abs_mean", "thickness_p95_mean"], 90),
        "",
        "## Top Stress-Robust Rows",
        "",
        md_table(stress_robust, ["robust_rank", "L", "P", "I", "T", "p95_mean", "p95_worst", "sameP_delta_p95_mean", "thickness_p95_mean", "robust_score"], 25),
        "",
        "## Raw Range",
        "",
        md_table(raw_rank, ["experiment_short", "n", "p50_mean", "p95_mean", "p95_std", "b0_uwb_p95_mean", "b0_delta_p95_mean", "b0_improved_seed_fraction", "radius_abs_mean", "thickness_p95_mean"], 20),
        "",
        "## Figures",
        "",
        "- `figs/01_top_truefull_position_all_active.png`",
        "- `figs/02_best_truefull_by_sensor.png`",
        "- `figs/03_top_stress_robust_candidates.png`",
        "",
        "## Tables",
        "",
        "- `tables/all_active_position_ranking_by_accuracy.csv`",
        "- `tables/top5_position_by_sensor.csv`",
        "- `tables/stress_robust_ranking.csv`",
        "- `tables/stress_by_case_ranking.csv`",
        "- `tables/all_truefull_summary_with_deltas.csv`",
        "- `tables/all_truefull_track_metrics.csv`",
    ]

    report_path = report_dir / "PHASE4_ALL_ACTIVE_18L_5SEED_5090D_ANALYSIS.md"
    report_path.write_text("\n".join(report), encoding="utf-8")
    manifest = {
        "created_utc": datetime.now(UTC).isoformat(),
        "status": "complete",
        "source_stamp": args.stamp,
        "stress_run_id": args.stress_run_id,
        "truefull_run_count": int(len(manifests)),
        "truefull_summary_rows": int(len(summary)),
        "best_accuracy_experiment": str(best_acc["experiment_short"]),
        "best_rescue_experiment": str(best_rescue["experiment_short"]),
        "best_stress_row": f"{best_stress['L']}/{best_stress['P']}/{best_stress['I']}/{best_stress['T']}",
        "report": str(report_path.relative_to(out_dir)),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"out_dir": str(out_dir), **manifest}, indent=2), flush=True)


if __name__ == "__main__":
    main()
