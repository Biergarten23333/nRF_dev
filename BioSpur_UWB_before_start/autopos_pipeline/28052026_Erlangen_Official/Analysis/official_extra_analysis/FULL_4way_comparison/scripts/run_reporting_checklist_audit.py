#!/usr/bin/env python3
"""Reporting-completeness audit for anchor and tag positioning metrics.

This script maps the Measurement-paper reporting checklist onto the already
generated FULL / 4-way / reviewer-audit outputs. It does not rerun solvers; it
aggregates existing CSVs into a small set of core tables.
"""

from __future__ import annotations

import csv
import itertools
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


THIS = Path(__file__).resolve()
COMP_ROOT = THIS.parents[1]
EXTRA_ROOT = THIS.parents[2]
FULL_ROOT = EXTRA_ROOT / "FULL"
ALIGN_ROOT = EXTRA_ROOT / "FULL_AutoPos_align_to_Vicon"
SCALE_ROOT = EXTRA_ROOT / "FULL_AutoPos_scale_to_vicon"
ONE_BASELINE_ROOT = EXTRA_ROOT / "FULL_AutoPos_one_baseline_scale_correction"
OFFICIAL_ROOT = EXTRA_ROOT.parent.parent
SOLVER_ROOT = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check"
OUT_ROOT = COMP_ROOT / "reporting_checklist"
TABLE_DIR = OUT_ROOT / "tables"
REPORT_DIR = OUT_ROOT / "reports"
FIG_DIR = OUT_ROOT / "figs"
RESILIENCE_ROOT = COMP_ROOT / "resilience_gap_audit"
REVIEWER_ROOT = COMP_ROOT / "reviewer_audit"
PRODUCTION_T4_REAL_EVAL_ROOT = COMP_ROOT / "production_method_probe" / "production_static_method_real_run_eval"


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
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def pct(values, q: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def rmse(values) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(math.sqrt(float(np.mean(arr * arr))))


def fmt(x: float, digits: int = 1) -> str:
    if x is None:
        return ""
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return str(x)
    if not math.isfinite(xf):
        return ""
    return f"{xf:.{digits}f}"


def markdown_table(rows: list[dict], columns: list[str]) -> list[str]:
    if not rows:
        return ["_No rows._"]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        vals = []
        for col in columns:
            val = row.get(col, "")
            vals.append(fmt(val, 2) if isinstance(val, float) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def pairwise_distance_errors(per_anchor: pd.DataFrame) -> dict[str, float]:
    rows = per_anchor.sort_values("anchor").copy()
    by_anchor = {
        str(r["anchor"]): (
            np.asarray([r["aligned_x_mm"], r["aligned_y_vertical_mm"], r["aligned_z_mm"]], dtype=float),
            np.asarray([r["truth_x_mm"], r["truth_y_vertical_mm"], r["truth_z_mm"]], dtype=float),
        )
        for _, r in rows.iterrows()
    }
    diffs = []
    rel = []
    horiz_diffs = []
    vertical_sensitive = []
    for a, b in itertools.combinations(sorted(by_anchor), 2):
        pa, ta = by_anchor[a]
        pb, tb = by_anchor[b]
        da = float(np.linalg.norm(pa - pb))
        dt = float(np.linalg.norm(ta - tb))
        d = da - dt
        diffs.append(d)
        if dt > 0:
            rel.append(abs(d) / dt * 100.0)
        truth_delta = np.abs(ta - tb)
        if truth_delta[1] < 250.0:
            horiz_diffs.append(d)
        if truth_delta[1] >= 250.0:
            vertical_sensitive.append(d)
    return {
        "pairwise_distance_mae_mm": float(np.mean(np.abs(diffs))),
        "pairwise_distance_rmse_mm": rmse(diffs),
        "pairwise_distance_relative_mae_pct": float(np.mean(rel)),
        "horizontal_pair_distance_rmse_mm": rmse(horiz_diffs),
        "vertical_sensitive_pair_distance_rmse_mm": rmse(vertical_sensitive),
    }


def build_anchor_layout_absolute_table() -> list[dict]:
    summary = pd.read_csv(FULL_ROOT / "tables/layout_alignment_summary.csv")
    per = pd.read_csv(FULL_ROOT / "tables/layout_abs_errors_all8.csv")
    out = []
    for _, row in summary[summary["eval_set"] == "all8"].sort_values("version").iterrows():
        version = str(row["version"])
        p = per[(per["version"] == version) & (per["eval_set"] == "all8")].copy()
        if p.empty:
            continue
        worst = p.sort_values("err_3d_mm", ascending=False).iloc[0]
        item: dict[str, float | int | str] = {
            "method": f"AutoPos {version}",
            "version": version,
            "n_anchors": int(row["n_anchors"]),
            "se3_rmse_mm": float(row["reflection_allowed_rms_3d_mm"]),
            "se3_median_mm": pct(p["err_3d_mm"], 50),
            "se3_p95_mm": pct(p["err_3d_mm"], 95),
            "x_rmse_mm": rmse(p["err_x_mm"]),
            "y_rmse_mm": rmse(p["err_y_vertical_mm"]),
            "z_rmse_mm": rmse(p["err_z_mm"]),
            "scale_factor_sim3": float(row["similarity_scale"]),
            "scale_bias_pct": float((row["similarity_scale"] - 1.0) * 100.0),
            "sim3_rmse_mm": float(row["similarity_rms_3d_mm"]),
            "shape_rms_mm": float(row["shape_rms_mm"]),
            "worst_anchor": str(worst["anchor"]),
            "worst_anchor_error_mm": float(worst["err_3d_mm"]),
            "alignment_note": "reflection-normalized rigid alignment; proper-rotation-only is not used because the exported OptiTrack frame has a handedness convention difference",
        }
        item.update(pairwise_distance_errors(p))
        out.append(item)
    return out


def build_anchor_repeatability_table() -> list[dict]:
    rows: list[dict] = []
    opti = pd.read_csv(FULL_ROOT / "tables/opti_anchor_medians_by_file.csv")
    per_anchor_sd = []
    for anchor, g in opti.groupby("anchor"):
        if len(g) < 2:
            continue
        sd_xyz = g[["x_mm", "y_vertical_mm", "z_mm"]].std(ddof=1).to_numpy(float)
        per_anchor_sd.append({"anchor": anchor, "coord_sd_3d_mm": float(np.linalg.norm(sd_xyz))})
    pair_dists: dict[str, list[float]] = {}
    for file_id, g in opti.groupby("file_id"):
        by_anchor = {
            str(r["anchor"]): np.asarray([r["x_mm"], r["y_vertical_mm"], r["z_mm"]], dtype=float)
            for _, r in g.iterrows()
        }
        for a, b in itertools.combinations(sorted(by_anchor), 2):
            pair_dists.setdefault(f"{a}-{b}", []).append(float(np.linalg.norm(by_anchor[a] - by_anchor[b])))
    pair_sd = [float(np.std(vals, ddof=1)) for vals in pair_dists.values() if len(vals) > 1]
    rows.append(
        {
            "method_or_split": "OptiTrack anchor truth repeated static files",
            "coordinate_sd_median_mm": pct([r["coord_sd_3d_mm"] for r in per_anchor_sd], 50),
            "pairwise_distance_sd_median_mm": pct(pair_sd, 50),
            "delay_sd_mm": float("nan"),
            "worst_anchor_sd_mm": pct([r["coord_sd_3d_mm"] for r in per_anchor_sd], 100),
            "status": "measured",
            "note": "OptiTrack anchor marker repeatability across static truth files, not AutoPos solver repeatability",
        }
    )

    sigma = json.loads((SOLVER_ROOT / "tables/anchor_sigma.json").read_text(encoding="utf-8"))
    sigma_vals = [float(v) for v in sigma.values()]
    rows.append(
        {
            "method_or_split": "AutoPos solver anchor sigma prior",
            "coordinate_sd_median_mm": pct(sigma_vals, 50),
            "pairwise_distance_sd_median_mm": float("nan"),
            "delay_sd_mm": float("nan"),
            "worst_anchor_sd_mm": pct(sigma_vals, 100),
            "status": "proxy_only",
            "note": "solver sigma prior/weighting input; repeated AutoPos split coordinate SD is not present in current FULL outputs",
        }
    )

    rows.append(
        {
            "method_or_split": "Independent repeated AutoPos layout runs",
            "coordinate_sd_median_mm": float("nan"),
            "pairwise_distance_sd_median_mm": float("nan"),
            "delay_sd_mm": float("nan"),
            "worst_anchor_sd_mm": float("nan"),
            "status": "not_measured",
            "note": "no physically independent repeated AutoPos deployment/split table is present",
        }
    )

    boot_layout_path = RESILIENCE_ROOT / "tables/bootstrap_layout_repeatability.csv"
    boot_delay_path = RESILIENCE_ROOT / "tables/bootstrap_delay_sd.csv"
    boot_precision_path = RESILIENCE_ROOT / "tables/bootstrap_numerical_precision.csv"
    if boot_layout_path.exists() and boot_delay_path.exists():
        boot_layout = pd.read_csv(boot_layout_path)
        boot_delay = pd.read_csv(boot_delay_path)
        boot_precision = pd.read_csv(boot_precision_path) if boot_precision_path.exists() else pd.DataFrame()
        precision_by_case = {str(r["case_id"]): r for _, r in boot_precision.iterrows()}
        delay_by_case = {str(r["case_id"]): r for _, r in boot_delay.iterrows()}
        for _, r in boot_layout.iterrows():
            case_id = str(r["case_id"])
            d = delay_by_case.get(case_id)
            p = precision_by_case.get(case_id)
            rows.append(
                {
                    "method_or_split": f"Numerical precision: raw-pair median bootstrap {case_id}",
                    "coordinate_sd_median_mm": float(r["coordinate_sd_median_mm"]),
                    "pairwise_distance_sd_median_mm": float(r["pairwise_distance_sd_median_mm"]),
                    "delay_sd_mm": float(d["anchor_delay_rel_A_sd_median_mm"]) if d is not None else float("nan"),
                    "worst_anchor_sd_mm": float(r["coordinate_sd_worst_mm"]),
                    "status": "numerical_precision",
                    "note": (
                        "within-campaign per-pair median sampling SE, not independent deployment repeatability"
                        if p is None
                        else (
                            "within-campaign per-pair median sampling SE; "
                            f"median analytical pair SE={float(p['pair_analytical_se_median_mm']):.3f} mm, "
                            f"P95={float(p['pair_analytical_se_p95_mm']):.3f} mm"
                        )
                    ),
                }
            )

    delay = pd.read_csv(FULL_ROOT / "tables/delay_common_differential.csv")
    rows.append(
        {
            "method_or_split": "AutoPos v4-io residual delay structure",
            "coordinate_sd_median_mm": float("nan"),
            "pairwise_distance_sd_median_mm": float("nan"),
            "delay_sd_mm": float(np.std(delay["autopos_differential_mm"].to_numpy(float), ddof=1)),
            "worst_anchor_sd_mm": float("nan"),
            "status": "structure_not_repeatability",
            "note": "std of fitted layout-level residual delay corrections; not a repeated-run delay SD",
        }
    )
    return rows


def summarize_static_df(label: str, layout: str, delay: str, tag_solver: str, df: pd.DataFrame, note: str) -> dict:
    return {
        "layout_delay_config": label,
        "layout": layout,
        "delay_mode": delay,
        "tag_solver": tag_solver,
        "n_positions": int(len(df)),
        "repeatability_3d_sd_median_mm": pct(df["d3_std_mm"], 50) if "d3_std_mm" in df.columns else float("nan"),
        "absolute_3d_rmse_mm": rmse(df["err_3d_mm"]),
        "absolute_3d_median_mm": pct(df["err_3d_mm"], 50),
        "absolute_3d_p95_mm": pct(df["err_3d_mm"], 95),
        "x_rmse_mm": rmse(df["err_x_mm"]),
        "y_rmse_mm": rmse(df["err_y_vertical_mm"]),
        "z_rmse_mm": rmse(df["err_z_mm"]),
        "note": note,
    }


def build_tag_static_table() -> list[dict]:
    rows = []
    prod = pd.read_csv(FULL_ROOT / "tables/tag_abs_errors_per_session.csv")
    raw = pd.read_csv(FULL_ROOT / "tables/tag_raw_replay_abs_errors_per_session.csv")
    filtered = pd.read_csv(FULL_ROOT / "filtered_deployment/tables/filtered_static_abs_errors_per_session.csv")
    align = pd.read_csv(ALIGN_ROOT / "tables/static_abs_errors_per_session.csv")
    scale = pd.read_csv(SCALE_ROOT / "tables/static_abs_errors_per_session.csv")
    one = pd.read_csv(ONE_BASELINE_ROOT / "tables/static_abs_errors_per_session.csv")
    prod_t4_real_path = PRODUCTION_T4_REAL_EVAL_ROOT / "tables/tag_abs_errors_per_session.csv"
    if prod_t4_real_path.exists():
        prod_t4_real = pd.read_csv(prod_t4_real_path)
        rows.append(summarize_static_df(
            "Original FULL production mean-aggregated static point v4-io/T4",
            "AutoPos v4-io rigid no-scale",
            "solver residual corrections",
            "T4 production mean aggregation",
            prod_t4_real[(prod_t4_real["version"] == "v4-io") & (prod_t4_real["eval_set"] == "all8")].copy(),
            "deployed/static headline after real production export path was switched to T4; per-position mean aggregation unchanged",
        ))

    rows.append(summarize_static_df(
        "Legacy production mean-aggregated static point v4-io/T1",
        "AutoPos v4-io rigid no-scale",
        "solver residual corrections",
        "T1 production mean aggregation",
        prod[(prod["version"] == "v4-io") & (prod["eval_set"] == "all8")].copy(),
        "legacy exported production output before the production path was switched to T4",
    ))
    rows.append(summarize_static_df(
        "Original FULL median-estimator ablation v4-io/T4",
        "AutoPos v4-io rigid no-scale",
        "solver residual corrections",
        "T4",
        raw[(raw["version"] == "v4-io") & (raw["eval_set"] == "all8") & (raw["tag_method"] == "T4")].copy(),
        "median static-point estimator ablation; not the deployed production mean-aggregated static point",
    ))
    rows.append(summarize_static_df(
        "Original FULL filtered static v4-io/T4+F5",
        "AutoPos v4-io rigid no-scale",
        "solver residual corrections",
        "T4+F5 stationary smoother",
        filtered[(filtered["version"] == "v4-io") & (filtered["eval_set"] == "all8") & (filtered["solver"] == "T4+F5")].copy(),
        "offline stationary/static-lock diagnostic; reduces per-capture jitter but uses full static dwell",
    ))
    rows.append(summarize_static_df(
        "Original FULL filtered static v4-io/T3+F5",
        "AutoPos v4-io rigid no-scale",
        "solver residual corrections",
        "T3+F5 stationary smoother",
        filtered[(filtered["version"] == "v4-io") & (filtered["eval_set"] == "all8") & (filtered["solver"] == "T3+F5")].copy(),
        "best combined filtered static deployment row; static-lock/stationary smoother diagnostic",
    ))
    rows.append(summarize_static_df(
        "Vicon truth anchors + delaycal/T4",
        "OptiTrack/Vicon truth anchors",
        "vicon_inter_anchor_delaycal",
        "T4",
        align[(align["layout_solver"] == "v4-io") & (align["layout_variant"] == "vicon_truth") & (align["delay_mode"] == "vicon_inter_anchor_delaycal") & (align["tag_method"] == "T4")].copy(),
        "known-anchor + re-estimated-delay control; best static control and better than self-cal, not a lower bound on achievable error",
    ))
    rows.append(summarize_static_df(
        "Full Sim(3) scale-to-Vicon + delaycal/T4",
        "AutoPos v4-io similarity-scaled to Vicon",
        "scaled_layout_inter_anchor_delaycal",
        "T4",
        scale[(scale["layout_solver"] == "v4-io") & (scale["layout_variant"] == "solver_similarity_scale_to_vicon") & (scale["delay_mode"] == "scaled_layout_inter_anchor_delaycal") & (scale["tag_method"] == "T4")].copy(),
        "global scale removed, residual delay corrections re-estimated",
    ))
    rows.append(summarize_static_df(
        "One-baseline E-H + delaycal/T4",
        "AutoPos v4-io one-baseline E-H scale",
        "one_baseline_layout_inter_anchor_delaycal",
        "T4",
        one[(one["layout_solver"] == "v4-io") & (one["layout_variant"] == "one_baseline_scale") & (one["delay_mode"] == "one_baseline_layout_inter_anchor_delaycal") & (one["baseline_pair"] == "E-H") & (one["tag_method"] == "T4")].copy(),
        "pre-registered one-baseline engineering control",
    ))
    return rows


def dynamic_metrics_for_samples(label: str, layout: str, delay: str, tag_solver: str, path: Path, filters: dict[str, str], note: str) -> dict:
    df = pd.read_csv(path)
    for col, val in filters.items():
        if col in df.columns:
            df = df[df[col].astype(str) == str(val)].copy()
    err = df["err3d_mm"].to_numpy(float)
    rpe_parts = []
    drift_slopes = []
    update_rates = []
    loss_rates = []
    for (_cid, _tag), g in df.groupby(["capture_id", "tag"], sort=True):
        g = g.sort_values("uwb_time_s")
        t = g["uwb_time_s"].to_numpy(float)
        uwb = g[["uwb_x_mm", "uwb_y_vertical_mm", "uwb_z_mm"]].to_numpy(float)
        opti = g[["opti_x_mm", "opti_y_vertical_mm", "opti_z_mm"]].to_numpy(float)
        finite = np.isfinite(t) & np.isfinite(uwb).all(axis=1) & np.isfinite(opti).all(axis=1)
        t = t[finite]
        uwb = uwb[finite]
        opti = opti[finite]
        if t.size < 3:
            continue
        dt = np.diff(t)
        good_dt = np.isfinite(dt) & (dt > 0.0) & (dt < 0.5)
        if np.any(good_dt):
            rpe = np.linalg.norm(np.diff(uwb, axis=0)[good_dt] - np.diff(opti, axis=0)[good_dt], axis=1)
            rpe_parts.append(rpe)
            med_dt = float(np.median(dt[good_dt]))
            if med_dt > 0:
                rate = 1.0 / med_dt
                update_rates.append(rate)
                duration = float(np.max(t) - np.min(t))
                expected = max(1.0, duration * rate + 1.0)
                loss_rates.append(max(0.0, 1.0 - float(t.size) / expected))
        e = np.linalg.norm(uwb - opti, axis=1)
        if t.size >= 5 and (np.max(t) - np.min(t)) > 1.0:
            slope = float(np.polyfit((t - t[0]) / 60.0, e, 1)[0])
            drift_slopes.append(abs(slope))
    rpe_all = np.concatenate(rpe_parts) if rpe_parts else np.empty(0)
    return {
        "layout_delay_config": label,
        "layout": layout,
        "delay_mode": delay,
        "tag_solver": tag_solver,
        "ate_rmse_mm": rmse(err),
        "ate_median_mm": pct(err, 50),
        "ate_p95_mm": pct(err, 95),
        "rpe_rmse_mm": rmse(rpe_all),
        "rpe_median_mm": pct(rpe_all, 50),
        "drift_abs_slope_median_mm_per_min": pct(drift_slopes, 50),
        "effective_packet_loss_median_pct": pct(loss_rates, 50) * 100.0,
        "effective_update_rate_median_hz": pct(update_rates, 50),
        "n_samples": int(len(df)),
        "note": note + "; packet loss is inferred from solved-sample cadence, not raw radio logs",
    }


def build_tag_dynamic_table() -> list[dict]:
    return [
        dynamic_metrics_for_samples(
            "Original FULL raw replay v4-io/T4",
            "AutoPos v4-io rigid no-scale",
            "solver residual corrections",
            "T4",
            FULL_ROOT / "roto_absolute/tables/roto_abs_samples_v4io_T4.csv",
            {"layout": "v4-io", "tag_method": "T4"},
            "ROTO absolute trajectory against OptiTrack antenna markers",
        ),
        dynamic_metrics_for_samples(
            "Vicon truth anchors + delaycal/T4",
            "OptiTrack/Vicon truth anchors",
            "vicon_inter_anchor_delaycal",
            "T4",
            ALIGN_ROOT / "roto_absolute/tables/roto_abs_samples_v4io_T4.csv",
            {"layout_solver": "v4-io", "layout_variant": "vicon_truth", "delay_mode": "vicon_inter_anchor_delaycal", "tag_method": "T4"},
            "known-anchor dynamic control",
        ),
        dynamic_metrics_for_samples(
            "Full Sim(3) scale-to-Vicon + delaycal/T4",
            "AutoPos v4-io similarity-scaled to Vicon",
            "scaled_layout_inter_anchor_delaycal",
            "T4",
            SCALE_ROOT / "roto_absolute/tables/roto_abs_samples_v4io_T4.csv",
            {"layout_solver": "v4-io", "layout_variant": "solver_similarity_scale_to_vicon", "delay_mode": "scaled_layout_inter_anchor_delaycal", "tag_method": "T4"},
            "global scale removed and residual delay re-estimated",
        ),
        dynamic_metrics_for_samples(
            "One-baseline E-H + delaycal/T4",
            "AutoPos v4-io one-baseline E-H scale",
            "one_baseline_layout_inter_anchor_delaycal",
            "T4",
            ONE_BASELINE_ROOT / "roto_absolute/tables/roto_abs_samples_v4io_T4.csv",
            {"layout_solver": "v4-io", "layout_variant": "one_baseline_scale", "delay_mode": "one_baseline_layout_inter_anchor_delaycal", "baseline_pair": "E-H", "tag_method": "T4"},
            "pre-registered one-baseline dynamic control",
        ),
    ]


def first_row(df: pd.DataFrame, **filters) -> pd.Series:
    out = df.copy()
    for col, val in filters.items():
        out = out[out[col].astype(str) == str(val)]
    if out.empty:
        raise KeyError(f"no row for {filters}")
    return out.iloc[0]


def build_ablation_table() -> list[dict]:
    static = pd.read_csv(COMP_ROOT / "tables/static_4way_accuracy_summary.csv")
    ap_quality = pd.read_csv(SOLVER_ROOT / "tables/autopos_quality_summary.csv")
    auto_resid = float(first_row(ap_quality, version="v4-io", eval_set="solve")["rms"])
    vicon_diag = pd.read_csv(ALIGN_ROOT / "tables/vicon_delaycal_diagnostics.csv")
    vicon_raw_bias_rms = rmse(vicon_diag["pair_bias_mm"])
    vicon_delaycal_resid = rmse(vicon_diag["delaycal_pair_residual_mm"])

    rows = []
    configs = [
        (
            "AutoPos v4-io rigid",
            "solver residual corrections",
            first_row(static, experiment="scale_to_vicon", layout_solver="v4-io", layout_variant="original_rigid_no_scale", delay_mode="solver_delay", tag_method="T4", scale_source="rigid_no_scale"),
            auto_resid,
            "current self-cal co-fit output; geometry and layout-level residual delay corrections are coupled",
        ),
        (
            "AutoPos v4-io Sim(3)-scaled",
            "solver residual corrections",
            first_row(static, experiment="scale_to_vicon", layout_solver="v4-io", layout_variant="solver_similarity_scale_to_vicon", delay_mode="solver_delay", tag_method="T4", scale_source="all_anchor_similarity"),
            auto_resid,
            "shows AutoPos residual corrections are not transferable after changing layout scale",
        ),
        (
            "AutoPos v4-io Sim(3)-scaled",
            "re-estimated inter-anchor residual corrections",
            first_row(static, experiment="scale_to_vicon", layout_solver="v4-io", layout_variant="solver_similarity_scale_to_vicon", delay_mode="scaled_layout_inter_anchor_delaycal", tag_method="T4", scale_source="all_anchor_similarity"),
            vicon_delaycal_resid,
            "separates global scale correction from residual delay re-estimation",
        ),
        (
            "Vicon/OptiTrack truth anchors",
            "none",
            first_row(static, experiment="align_to_vicon", layout_solver="v4-io", layout_variant="vicon_truth", delay_mode="zero_delay", tag_method="T4"),
            vicon_raw_bias_rms,
            "optical geometry alone is not enough; endpoint delays dominate",
        ),
        (
            "Vicon/OptiTrack truth anchors",
            "AutoPos solver residual corrections",
            first_row(static, experiment="align_to_vicon", layout_solver="v4-io", layout_variant="vicon_truth", delay_mode="solver_delay", tag_method="T4"),
            vicon_raw_bias_rms,
            "tests delay transferability; poor result means solver residual corrections are layout-conditioned",
        ),
        (
            "Vicon/OptiTrack truth anchors",
            "re-estimated inter-anchor residual corrections",
            first_row(static, experiment="align_to_vicon", layout_solver="v4-io", layout_variant="vicon_truth", delay_mode="vicon_inter_anchor_delaycal", tag_method="T4"),
            vicon_delaycal_resid,
            "known-anchor + re-estimated-delay control; best static control and better than self-cal, not a lower bound on achievable error",
        ),
        (
            "One-baseline E-H v4-io",
            "re-estimated inter-anchor residual corrections",
            first_row(static, experiment="one_baseline", layout_solver="v4-io", layout_variant="one_baseline_scale", delay_mode="one_baseline_layout_inter_anchor_delaycal", tag_method="T4", scale_source="E-H"),
            float("nan"),
            "practical field-measurable one-baseline scale/delay correction",
        ),
    ]
    for layout, delay, row, anchor_resid, interp in configs:
        rows.append(
            {
                "layout": layout,
                "delay_or_bias": delay,
                "tag_rmse_mm": float(row["err_3d_rms_mm"]),
                "tag_median_mm": float(row["err_3d_median_mm"]),
                "tag_p95_mm": float(row["err_3d_p95_mm"]),
                "anchor_or_pair_residual_rms_mm": anchor_resid,
                "tag_solver_residual_median_mm": float(row["residual_rms_median_mm"]),
                "interpretation": interp,
            }
        )
    rows.append(
        {
            "layout": "PANS/manual",
            "delay_or_bias": "corresponding delay",
            "tag_rmse_mm": float("nan"),
            "tag_median_mm": float("nan"),
            "tag_p95_mm": float("nan"),
            "anchor_or_pair_residual_rms_mm": float("nan"),
            "tag_solver_residual_median_mm": float("nan"),
            "interpretation": "not found in current FULL dataset; add if a practical manual/PANS baseline exists",
        }
    )
    return rows


def build_delay_layout_coupling_table(
    ablation: list[dict],
    tag_static: list[dict],
    anchor_layout: list[dict],
) -> list[dict]:
    ab = pd.DataFrame(ablation)
    static = pd.DataFrame(tag_static)
    anchors = pd.DataFrame(anchor_layout)

    def ablation_row(layout: str, delay_or_bias: str) -> pd.Series:
        rows = ab[(ab["layout"] == layout) & (ab["delay_or_bias"] == delay_or_bias)]
        if rows.empty:
            raise KeyError(f"missing delay-layout row {layout!r} / {delay_or_bias!r}")
        return rows.iloc[0]

    def static_row(config: str) -> pd.Series:
        rows = static[static["layout_delay_config"] == config]
        if rows.empty:
            raise KeyError(f"missing static row {config!r}")
        return rows.iloc[0]

    v4_anchor = anchors[anchors["version"] == "v4-io"].iloc[0]
    auto_static = static_row("Original FULL production mean-aggregated static point v4-io/T4")
    mechanism = load_anchor_absorption_mechanism()
    rows = [
        {
            "case": "truth_coords_no_delay",
            "layout_frame": "Vicon/OptiTrack truth anchors",
            "delay_treatment": "none",
            "tag_rmse_mm": float(ablation_row("Vicon/OptiTrack truth anchors", "none")["tag_rmse_mm"]),
            "tag_median_mm": float(ablation_row("Vicon/OptiTrack truth anchors", "none")["tag_median_mm"]),
            "tag_p95_mm": float(ablation_row("Vicon/OptiTrack truth anchors", "none")["tag_p95_mm"]),
            "source_table": "checklist_ablation.csv",
            "claim_role": "optical geometry alone is not sufficient",
        },
        {
            "case": "truth_coords_transplanted_selfcal_delay",
            "layout_frame": "Vicon/OptiTrack truth anchors",
            "delay_treatment": "AutoPos self-cal layout-level residual corrections transplanted",
            "tag_rmse_mm": float(
                ablation_row("Vicon/OptiTrack truth anchors", "AutoPos solver residual corrections")["tag_rmse_mm"]
            ),
            "tag_median_mm": float(
                ablation_row("Vicon/OptiTrack truth anchors", "AutoPos solver residual corrections")["tag_median_mm"]
            ),
            "tag_p95_mm": float(
                ablation_row("Vicon/OptiTrack truth anchors", "AutoPos solver residual corrections")["tag_p95_mm"]
            ),
            "source_table": "checklist_ablation.csv",
            "claim_role": "residual corrections are layout-frame conditioned, not transferable",
        },
        {
            "case": "truth_coords_reestimated_delay",
            "layout_frame": "Vicon/OptiTrack truth anchors",
            "delay_treatment": "re-estimated inter-anchor residual corrections",
            "tag_rmse_mm": float(
                ablation_row("Vicon/OptiTrack truth anchors", "re-estimated inter-anchor residual corrections")[
                    "tag_rmse_mm"
                ]
            ),
            "tag_median_mm": float(
                ablation_row("Vicon/OptiTrack truth anchors", "re-estimated inter-anchor residual corrections")[
                    "tag_median_mm"
                ]
            ),
            "tag_p95_mm": float(
                ablation_row("Vicon/OptiTrack truth anchors", "re-estimated inter-anchor residual corrections")[
                    "tag_p95_mm"
                ]
            ),
            "source_table": "checklist_ablation.csv",
            "claim_role": "known-anchor + re-estimated-delay control",
        },
        {
            "case": "autopos_selfcal_rigid_solver_delay",
            "layout_frame": "AutoPos v4-io rigid no-scale",
            "delay_treatment": "self-cal layout-level residual corrections",
            "tag_rmse_mm": float(auto_static["absolute_3d_rmse_mm"]),
            "tag_median_mm": float(auto_static["absolute_3d_median_mm"]),
            "tag_p95_mm": float(auto_static["absolute_3d_p95_mm"]),
            "source_table": "checklist_tag_static.csv + checklist_anchor_layout_absolute.csv",
            "claim_role": "self-cal is competitive because geometry and residual corrections are co-fitted",
        },
    ]
    for row in rows:
        row["autopos_v4io_se3_rmse_mm"] = float(v4_anchor["se3_rmse_mm"])
        row["autopos_v4io_sim3_scale"] = float(v4_anchor["scale_factor_sim3"])
        row["mechanism_radial_absorption_r2"] = mechanism["layout_absorption_radial_r2"]
        row["mechanism_radial_absorption_slope_mm_per_mm"] = mechanism[
            "layout_absorption_radial_slope_mm_per_mm"
        ]
        row["mechanism_verdict"] = mechanism["anchor_decomp_verdict"]
        row["caption_claim"] = (
            "Imposed geometric ground-truth coordinates are not a sufficient solver input: "
            "layout-level residual delay corrections are coupled to the coordinate frame and must be "
            "re-estimated on that frame. AutoPos self-cal remains competitive because it co-fits geometry "
            "and layout-level residual corrections."
        )
    return rows


def load_anchor_absorption_mechanism() -> dict:
    path = REVIEWER_ROOT / "tables" / "why9_anchor_consistency.csv"
    default = {
        "layout_absorption_radial_r2": float("nan"),
        "layout_absorption_radial_slope_mm_per_mm": float("nan"),
        "anchor_decomp_verdict": "",
        "paper_consequence": "",
    }
    if not path.exists():
        return default
    df = pd.read_csv(path)
    overall = df[df["check_type"].astype(str) == "overall"]
    if overall.empty:
        return default
    row = overall.iloc[0]
    return {
        "layout_absorption_radial_r2": float(row.get("layout_absorption_radial_r2", float("nan"))),
        "layout_absorption_radial_slope_mm_per_mm": float(
            row.get("layout_absorption_radial_slope_mm_per_mm", float("nan"))
        ),
        "anchor_decomp_verdict": str(row.get("anchor_decomp_verdict", "")),
        "paper_consequence": str(row.get("paper_consequence", "")),
    }


def write_delay_layout_coupling_figure(rows: list[dict]) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    labels = [
        "Truth coords\nno delay",
        "Truth coords\nself-cal delay",
        "Truth coords\nre-fit delay",
        "AutoPos self-cal\nrigid/T4",
    ]
    values = [float(row["tag_rmse_mm"]) for row in rows]
    colors = ["#b9504a", "#c98b3a", "#327a65", "#3f6f9f"]
    fig, ax = plt.subplots(figsize=(8.0, 4.8))
    bars = ax.bar(labels, values, color=colors)
    ax.set_ylabel("static tag 3D RMSE (mm)")
    ax.set_title("Delay-layout coupling on the same static tag data")
    ax.grid(axis="y", alpha=0.25)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2.0, value + 6.0, f"{value:.0f}", ha="center", va="bottom")
    caption = (
        "Truth coordinates only are insufficient; residual delay corrections must be re-estimated "
        "on the imposed coordinate frame."
    )
    fig.text(0.02, 0.01, caption, ha="left", va="bottom", fontsize=9)
    fig.tight_layout(rect=(0.0, 0.06, 1.0, 1.0))
    fig.savefig(FIG_DIR / "delay_layout_coupling.png", dpi=180)
    plt.close(fig)


def build_coverage_table() -> list[dict]:
    rows = [
        ("Anchor", "Inter-anchor range residual", "covered", "checklist_anchor_layout_absolute.csv + checklist_ablation.csv", "AutoPos internal residual and Vicon delaycal residual are reported."),
        ("Anchor", "Inter-anchor distance error", "covered", "checklist_anchor_layout_absolute.csv", "Pairwise distance MAE/RMSE and relative MAE are included."),
        ("Anchor", "SE(3)-aligned anchor error", "covered", "checklist_anchor_layout_absolute.csv", "Frame-normalized rigid/reflection alignment is used; note explains handedness."),
        ("Anchor", "Axis-wise SE(3) anchor error", "covered", "checklist_anchor_layout_absolute.csv", "X/Y/Z RMSE columns included."),
        ("Anchor", "Sim(3)-aligned residual and scale bias", "covered", "checklist_anchor_layout_absolute.csv", "Sim(3) scale factor, scale bias %, and Sim(3) RMSE included."),
        ("Anchor", "Per-anchor error ranking", "partial", "layout_abs_errors_all8.csv + checklist_anchor_layout_absolute.csv", "Worst anchor is summarized; full per-anchor table already exists."),
        ("Anchor", "Per-axis scale / anisotropy", "partial", "checklist_anchor_layout_absolute.csv", "Horizontal vs vertical-sensitive pairwise RMSE proxy included, not a full anisotropic scale fit."),
        ("Anchor", "Repeatability of layout", "not_measured", "checklist_anchor_repeatability.csv", "Independent repeated AutoPos deployments/splits are not present. Raw-pair bootstrap is reported separately as numerical precision, not repeatability."),
        ("Anchor", "Delay/bias repeatability", "numerical_precision", "resilience_gap_audit/tables/bootstrap_delay_sd.csv + checklist_anchor_repeatability.csv", "Delay rel_A bootstrap SD is within-campaign median sampling SE, not independent repeated-run delay repeatability; absolute/common-mode delay remains gauge-coupled."),
        ("Anchor", "Delay-layout coupling", "covered", "checklist_ablation.csv", "Vicon solver-delay vs Vicon delaycal and scale-to-Vicon solver-delay rows show non-transferability."),
        ("Anchor", "Baseline comparison", "partial", "checklist_ablation.csv", "AutoPos/Vicon/one-baseline covered; PANS/manual missing."),
        ("Tag", "Static tag repeatability", "covered", "checklist_tag_static.csv", "Per-position d3_std median included."),
        ("Tag", "Static tag absolute error", "covered", "checklist_tag_static.csv", "RMSE/median/P95 included."),
        ("Tag", "Axis-wise static error", "covered", "checklist_tag_static.csv", "X/Y/Z RMSE included."),
        ("Tag", "Moving tag trajectory error", "covered", "checklist_tag_dynamic.csv", "ATE RMSE/median/P95 included from ROTO OptiTrack truth."),
        ("Tag", "Relative trajectory error", "covered", "checklist_tag_dynamic.csv", "Frame-to-frame RPE RMSE included."),
        ("Tag", "Rigid wand / RotoArm consistency", "covered", "FULL_4WAY_BIG_COMPARISON.md + reviewer audit WHY #2/#10/#11", "Radius, dR, turn-center repeatability already reported."),
        ("Tag", "Layout-to-tag propagation", "covered", "checklist_ablation.csv + static/dynamic tables", "Same tag data compared across AutoPos/Vicon/scale/one-baseline layouts."),
        ("Tag", "Anchor dropout robustness", "covered", "mc_keepk_combined_summary.csv + stratified_keepk_category_summary.csv", "Monte Carlo keep-k/dropout tables exist; not expanded into checklist core tables."),
        ("Tag", "LOS/NLOS or quality robustness", "partial", "tag_error_by_facing.csv + pair_raw_scatter.csv + worstpoint_range_residuals.csv", "Quality/facing/range diagnostics exist; explicit CIR/NLOS labels not found."),
        ("Tag", "Update-rate / packet-loss robustness", "covered_synthetic", "resilience_gap_audit/tables/static_dropout_stress_summary.csv + resilience_gap_audit/tables/roto_sample_dropout_stress_summary.csv", "Static raw frames are re-solved under synthetic packet/dropout stress; ROTO coverage is solved-sample thinning, not raw range re-solving."),
        ("Tag", "Long-term stability", "partial", "temporal_drift_anchor_summary.csv", "Anchor range drift exists; long moving/tag drift stress table not found."),
    ]
    return [
        {"domain": d, "check": c, "status": s, "evidence": e, "note": n}
        for d, c, s, e, n in rows
    ]


def write_report(
    anchor_layout: list[dict],
    anchor_repeat: list[dict],
    tag_static: list[dict],
    tag_dynamic: list[dict],
    ablation: list[dict],
    delay_layout_coupling: list[dict],
    coverage: list[dict],
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("# Reporting Checklist Audit")
    lines.append("")
    lines.append(f"Generated {datetime.now(UTC).isoformat()}.")
    lines.append("")
    lines.append(
        "This report maps the requested anchor/tag positioning reporting checklist onto the existing FULL and 4-way analysis outputs. "
        "It aggregates existing tables, incorporates the resilience-gap audit outputs, and marks remaining missing evidence explicitly."
    )
    lines.append("")
    lines.append("## Core Tables")
    lines.append("")
    lines.append("### Anchor Layout Absolute")
    lines.extend(markdown_table(anchor_layout, ["method", "se3_rmse_mm", "se3_median_mm", "se3_p95_mm", "x_rmse_mm", "y_rmse_mm", "z_rmse_mm", "scale_bias_pct", "sim3_rmse_mm", "pairwise_distance_rmse_mm", "worst_anchor"]))
    lines.append("")
    lines.append("### Anchor Repeatability")
    lines.extend(markdown_table(anchor_repeat, ["method_or_split", "coordinate_sd_median_mm", "pairwise_distance_sd_median_mm", "delay_sd_mm", "worst_anchor_sd_mm", "status"]))
    lines.append("")
    lines.append("### Tag Static")
    lines.extend(markdown_table(tag_static, ["layout_delay_config", "repeatability_3d_sd_median_mm", "absolute_3d_rmse_mm", "x_rmse_mm", "y_rmse_mm", "z_rmse_mm", "absolute_3d_p95_mm"]))
    lines.append("")
    lines.append("### Tag Dynamic")
    lines.extend(markdown_table(tag_dynamic, ["layout_delay_config", "ate_rmse_mm", "rpe_rmse_mm", "ate_p95_mm", "drift_abs_slope_median_mm_per_min", "effective_packet_loss_median_pct", "effective_update_rate_median_hz"]))
    lines.append("")
    lines.append("### Ablation")
    lines.extend(markdown_table(ablation, ["layout", "delay_or_bias", "tag_rmse_mm", "tag_p95_mm", "anchor_or_pair_residual_rms_mm", "interpretation"]))
    lines.append("")
    lines.append("### Delay-Layout Coupling")
    lines.extend(markdown_table(delay_layout_coupling, ["case", "layout_frame", "delay_treatment", "tag_rmse_mm", "tag_p95_mm", "claim_role"]))
    lines.append("")
    mechanism = delay_layout_coupling[0] if delay_layout_coupling else {}
    lines.append(
        "The `truth_coords_no_delay`, `truth_coords_transplanted_selfcal_delay`, and "
        "`truth_coords_reestimated_delay` rows isolate the same tag data under the imposed optical coordinate frame. "
        "The result is the 311/252/77 mm RMSE triangle: ground-truth coordinates alone are not enough, and "
        "layout-level residual delay corrections must be re-estimated on the coordinate frame being solved. "
        "This is the narrow contribution claim; it is not a novelty claim about generic joint geometry/delay fitting."
    )
    if mechanism:
        lines.append("")
        lines.append(
            "Mechanism view: WHY #9 regresses self-minus-Vicon `anchor_main_rel_A` on v4-io radial layout error and obtains "
            f"R2={fmt(mechanism.get('mechanism_radial_absorption_r2', float('nan')), 3)} with slope "
            f"{fmt(mechanism.get('mechanism_radial_absorption_slope_mm_per_mm', float('nan')), 3)} mm/mm "
            f"(`{mechanism.get('mechanism_verdict', '')}`). "
            "Outcome view: the same coupling appears as the 311/252/77 tag-RMSE triangle above. Together they explain why the self-cal layout can be geometrically off by about 105 mm rigid SE(3) RMSE yet remain ranging-competitive: the self-cal residual-delay term absorbs radial coordinate/scale error, so it is not a physical per-anchor delay and not a solver bug."
        )
    lines.append("")
    lines.append("## Coverage")
    lines.extend(markdown_table(coverage, ["domain", "check", "status", "evidence", "note"]))
    lines.append("")
    lines.append("## Output Tables")
    for name in [
        "checklist_anchor_layout_absolute.csv",
        "checklist_anchor_repeatability.csv",
        "checklist_tag_static.csv",
        "checklist_tag_dynamic.csv",
        "checklist_ablation.csv",
        "delay_layout_coupling.csv",
        "checklist_coverage.csv",
    ]:
        lines.append(f"- `../tables/{name}`")
    lines.append("- `../figs/delay_layout_coupling.png`")
    lines.append("- `../../resilience_gap_audit/reports/RESILIENCE_GAP_AUDIT.md`")
    (REPORT_DIR / "REPORTING_CHECKLIST_AUDIT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    anchor_layout = build_anchor_layout_absolute_table()
    anchor_repeat = build_anchor_repeatability_table()
    tag_static = build_tag_static_table()
    tag_dynamic = build_tag_dynamic_table()
    ablation = build_ablation_table()
    delay_layout_coupling = build_delay_layout_coupling_table(ablation, tag_static, anchor_layout)
    coverage = build_coverage_table()
    write_csv(TABLE_DIR / "checklist_anchor_layout_absolute.csv", anchor_layout)
    write_csv(TABLE_DIR / "checklist_anchor_repeatability.csv", anchor_repeat)
    write_csv(TABLE_DIR / "checklist_tag_static.csv", tag_static)
    write_csv(TABLE_DIR / "checklist_tag_dynamic.csv", tag_dynamic)
    write_csv(TABLE_DIR / "checklist_ablation.csv", ablation)
    write_csv(TABLE_DIR / "delay_layout_coupling.csv", delay_layout_coupling)
    write_csv(TABLE_DIR / "checklist_coverage.csv", coverage)
    write_delay_layout_coupling_figure(delay_layout_coupling)
    write_report(anchor_layout, anchor_repeat, tag_static, tag_dynamic, ablation, delay_layout_coupling, coverage)
    print(f"Wrote {REPORT_DIR / 'REPORTING_CHECKLIST_AUDIT.md'}")


if __name__ == "__main__":
    main()
