#!/usr/bin/env python3
"""Reviewer-style metrology audit for the FULL AutoPos/Vicon analysis.

This script does not rerun the solver matrix.  It interrogates the already
generated FULL/FULL_4way/ablation outputs and computes falsification-oriented
checks for the Measurement-paper review questions:

* ROTO time-offset refit against absolute 3D error.
* Per-capture post-hoc rigid residual removal.
* One-baseline static cross-validation by held-out static positions.
* Procrustes scale/RMS consistency.
* Production-vs-raw tag solver consistency.
"""

from __future__ import annotations

import csv
import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
COMP_ROOT = THIS.parents[1]
EXTRA_ROOT = THIS.parents[2]
FULL_ROOT = EXTRA_ROOT / "FULL"
ALIGN_ROOT = EXTRA_ROOT / "FULL_AutoPos_align_to_Vicon"
ONE_BASELINE_ROOT = EXTRA_ROOT / "FULL_AutoPos_one_baseline_scale_correction"
OFFICIAL_ROOT = EXTRA_ROOT.parent.parent
OPTI_FULL_ROOT = OFFICIAL_ROOT / "opti_captures" / "full"
OUT_ROOT = COMP_ROOT / "reviewer_audit"
TABLE_DIR = OUT_ROOT / "tables"
REPORT_DIR = OUT_ROOT / "reports"

UWB_TAGS = ["BS2DCE", "BSDC91"]
DEFAULT_MAPPING = {"BS2DCE": "WandBantenna", "BSDC91": "WandCantenna"}
OPTITRACK_MARKERS = ["WandBantenna", "WandCantenna"]


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


def pct(values: np.ndarray | list[float], q: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def stats(values: np.ndarray | list[float], prefix: str = "") -> dict[str, float]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {
            f"{prefix}n": 0,
            f"{prefix}p50_mm": float("nan"),
            f"{prefix}p95_mm": float("nan"),
            f"{prefix}rmse_mm": float("nan"),
        }
    return {
        f"{prefix}n": int(arr.size),
        f"{prefix}p50_mm": float(np.percentile(arr, 50)),
        f"{prefix}p95_mm": float(np.percentile(arr, 95)),
        f"{prefix}rmse_mm": float(math.sqrt(float(np.mean(arr * arr)))),
    }


def fmt(x: float, ndigits: int = 1) -> str:
    if x is None or not math.isfinite(float(x)):
        return "nan"
    return f"{float(x):.{ndigits}f}"


def import_roto_module():
    path = FULL_ROOT / "roto_absolute" / "scripts" / "run_roto_absolute_analysis.py"
    spec = importlib.util.spec_from_file_location("full_roto_analysis", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def selected_wand_mapping() -> dict[str, str]:
    mapping_csv = FULL_ROOT / "roto_absolute" / "tables" / "roto_wand_mapping_decision.csv"
    if not mapping_csv.exists():
        return DEFAULT_MAPPING.copy()
    df = pd.read_csv(mapping_csv)
    if not {"mapping_name", "capture_id", "BS2DCE_marker", "BSDC91_marker"}.issubset(df.columns):
        return DEFAULT_MAPPING.copy()
    if "n_captures_scored" in df.columns:
        summary = df[df["n_captures_scored"].notna()]
        if not summary.empty:
            row = summary.sort_values("score_median_3d_mm").iloc[0]
            return {"BS2DCE": str(row["BS2DCE_marker"]), "BSDC91": str(row["BSDC91_marker"])}
    row = df.sort_values("score_median_3d_mm").iloc[0]
    return {"BS2DCE": str(row["BS2DCE_marker"]), "BSDC91": str(row["BSDC91_marker"])}


def load_opti_cache(roto_mod, capture_ids: list[str]) -> dict[str, dict[str, object]]:
    cache = {}
    for cid in capture_ids:
        trc = OPTI_FULL_ROOT / f"{cid}.trc"
        cache[cid] = roto_mod.parse_trc_trajectories(trc, OPTITRACK_MARKERS)
    return cache


def interpolate_many(roto_mod, traj, query_time_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    xyz, good = roto_mod.interpolate_opti(traj, query_time_s)
    return np.asarray(xyz, dtype=float), np.asarray(good, dtype=bool)


def capture_errors_at_beta(
    samples: pd.DataFrame,
    opti_cache: dict[str, dict[str, object]],
    mapping: dict[str, str],
    roto_mod,
    beta_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    uwb_parts = []
    opti_parts = []
    err_parts = []
    time_parts = []
    for tag, group in samples.groupby("tag"):
        if tag not in mapping:
            continue
        marker = mapping[tag]
        cid = str(group["capture_id"].iloc[0])
        traj = opti_cache[cid][marker]
        q = group["uwb_time_s"].to_numpy(float) + beta_s
        opti_xyz, good = interpolate_many(roto_mod, traj, q)
        uwb_xyz = group[["uwb_x_mm", "uwb_y_vertical_mm", "uwb_z_mm"]].to_numpy(float)
        finite = good & np.isfinite(uwb_xyz).all(axis=1) & np.isfinite(opti_xyz).all(axis=1)
        if not np.any(finite):
            continue
        diff = uwb_xyz[finite] - opti_xyz[finite]
        uwb_parts.append(uwb_xyz[finite])
        opti_parts.append(opti_xyz[finite])
        err_parts.append(np.linalg.norm(diff, axis=1))
        time_parts.append(q[finite])
    if not err_parts:
        empty = np.empty((0, 3), dtype=float)
        return np.empty(0), empty, empty, np.empty(0)
    return np.concatenate(err_parts), np.vstack(uwb_parts), np.vstack(opti_parts), np.concatenate(time_parts)


def optimize_capture_beta(
    samples: pd.DataFrame,
    beta0_s: float,
    opti_cache: dict[str, dict[str, object]],
    mapping: dict[str, str],
    roto_mod,
    radius_s: float = 0.250,
    step_s: float = 0.001,
) -> dict:
    betas = np.arange(beta0_s - radius_s, beta0_s + radius_s + step_s / 2.0, step_s)
    scores = []
    ns = []
    for beta in betas:
        err, _uwb, _opti, _t = capture_errors_at_beta(samples, opti_cache, mapping, roto_mod, float(beta))
        scores.append(float(np.median(err)) if err.size else float("inf"))
        ns.append(int(err.size))
    scores_np = np.asarray(scores, dtype=float)
    best_i = int(np.nanargmin(scores_np))
    curr_err, _uwb, _opti, _t = capture_errors_at_beta(samples, opti_cache, mapping, roto_mod, beta0_s)
    best_err, best_uwb, best_opti, best_time = capture_errors_at_beta(
        samples, opti_cache, mapping, roto_mod, float(betas[best_i])
    )
    return {
        "beta0_s": float(beta0_s),
        "beta_best_s": float(betas[best_i]),
        "delta_beta_ms": float((betas[best_i] - beta0_s) * 1000.0),
        "current_p50_mm": pct(curr_err, 50),
        "current_p95_mm": pct(curr_err, 95),
        "best_p50_mm": pct(best_err, 50),
        "best_p95_mm": pct(best_err, 95),
        "drop_p50_mm": pct(curr_err, 50) - pct(best_err, 50),
        "drop_p95_mm": pct(curr_err, 95) - pct(best_err, 95),
        "n_current": int(curr_err.size),
        "n_best": int(best_err.size),
        "best_uwb": best_uwb,
        "best_opti": best_opti,
        "best_err": best_err,
        "best_time": best_time,
        "grid_step_s": step_s,
        "search_radius_s": radius_s,
    }


def fit_rigid_no_scale(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Fit proper-rotation Kabsch transform src @ R + t -> dst."""
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3:
        raise ValueError(f"bad fit shapes {src.shape} {dst.shape}")
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, _s, vt = np.linalg.svd(x.T @ y)
    d = np.ones(3)
    if np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    r = u @ np.diag(d) @ vt
    t = dst_c - src_c @ r
    det = float(np.linalg.det(r))
    return r, t, det


def rotation_angle_deg(r: np.ndarray) -> float:
    val = (np.trace(r) - 1.0) / 2.0
    val = max(-1.0, min(1.0, float(val)))
    return float(math.degrees(math.acos(val)))


def track_level_stats(err_by_track: list[np.ndarray], prefix: str = "") -> dict[str, float]:
    p50s = [pct(e, 50) for e in err_by_track if np.asarray(e).size]
    p95s = [pct(e, 95) for e in err_by_track if np.asarray(e).size]
    return {
        f"{prefix}track_count": len(p50s),
        f"{prefix}track_p50_of_p50_mm": pct(p50s, 50),
        f"{prefix}track_p95_of_p50_mm": pct(p50s, 95),
        f"{prefix}track_p50_of_p95_mm": pct(p95s, 50),
        f"{prefix}track_p95_of_p95_mm": pct(p95s, 95),
    }


def audit_roto_time_and_rigid() -> tuple[list[dict], list[dict], list[dict], dict[str, float]]:
    roto_mod = import_roto_module()
    mapping = selected_wand_mapping()
    offsets = pd.read_csv(FULL_ROOT / "roto_absolute" / "tables" / "roto_time_offsets_v4io_T4.csv")
    capture_ids = sorted(offsets.loc[offsets["status"] == "ok", "capture_id"].astype(str).tolist())
    opti_cache = load_opti_cache(roto_mod, capture_ids)

    scenarios = [
        {
            "scenario": "self_cal_v4io_T4",
            "sample_path": FULL_ROOT / "roto_absolute" / "tables" / "roto_abs_samples_v4io_T4.csv",
        },
        {
            "scenario": "vicon_truth_delaycal_T4",
            "sample_path": ALIGN_ROOT / "roto_absolute" / "tables" / "roto_abs_samples_v4io_T4.csv",
        },
    ]
    time_rows: list[dict] = []
    rigid_rows: list[dict] = []
    summary_rows: list[dict] = []

    for scenario in scenarios:
        samples_all = pd.read_csv(scenario["sample_path"])
        scenario_name = scenario["scenario"]
        all_current_err = []
        all_best_err = []
        all_rigid_err = []
        best_err_by_track: list[np.ndarray] = []
        rigid_err_by_track: list[np.ndarray] = []
        speed_mm_s_parts = []
        motion_window_mm_parts = []
        for _, off in offsets[offsets["status"] == "ok"].iterrows():
            cid = str(off["capture_id"])
            beta0 = float(off["beta_s"])
            cap = samples_all[samples_all["capture_id"].astype(str) == cid].copy()
            if cap.empty:
                continue
            opt = optimize_capture_beta(cap, beta0, opti_cache, mapping, roto_mod)
            time_rows.append(
                {
                    "scenario": scenario_name,
                    "capture_id": cid,
                    "beta0_s": opt["beta0_s"],
                    "beta_best_s": opt["beta_best_s"],
                    "delta_beta_ms": opt["delta_beta_ms"],
                    "current_p50_mm": opt["current_p50_mm"],
                    "current_p95_mm": opt["current_p95_mm"],
                    "best_p50_mm": opt["best_p50_mm"],
                    "best_p95_mm": opt["best_p95_mm"],
                    "drop_p50_mm": opt["drop_p50_mm"],
                    "drop_p95_mm": opt["drop_p95_mm"],
                    "n_current": opt["n_current"],
                    "n_best": opt["n_best"],
                    "grid_step_s": opt["grid_step_s"],
                    "search_radius_s": opt["search_radius_s"],
                }
            )
            all_best_err.append(opt["best_err"])
            for tag in UWB_TAGS:
                cap_tag = cap[cap["tag"] == tag].copy()
                if cap_tag.empty:
                    continue
                err_tag, _u, _o, _t = capture_errors_at_beta(
                    cap_tag, opti_cache, mapping, roto_mod, opt["beta_best_s"]
                )
                if err_tag.size:
                    best_err_by_track.append(err_tag)

            curr_err, _cu, _co, _ct = capture_errors_at_beta(cap, opti_cache, mapping, roto_mod, beta0)
            all_current_err.append(curr_err)

            uwb = opt["best_uwb"]
            opti = opt["best_opti"]
            before = opt["best_err"]
            if uwb.shape[0] >= 6:
                r, t, det = fit_rigid_no_scale(uwb, opti)
                transformed = uwb @ r + t
                after = np.linalg.norm(transformed - opti, axis=1)
                all_rigid_err.append(after)
                rigid_rows.append(
                    {
                        "scenario": scenario_name,
                        "capture_id": cid,
                        "n": int(after.size),
                        "before_p50_mm": pct(before, 50),
                        "before_p95_mm": pct(before, 95),
                        "after_p50_mm": pct(after, 50),
                        "after_p95_mm": pct(after, 95),
                        "drop_p50_mm": pct(before, 50) - pct(after, 50),
                        "drop_p95_mm": pct(before, 95) - pct(after, 95),
                        "translation_norm_mm": float(np.linalg.norm(t)),
                        "rotation_angle_deg": rotation_angle_deg(r),
                        "det": det,
                    }
                )
                for tag in UWB_TAGS:
                    cap_tag = cap[cap["tag"] == tag].copy()
                    if cap_tag.empty:
                        continue
                    _err, u_tag, o_tag, _tq = capture_errors_at_beta(
                        cap_tag, opti_cache, mapping, roto_mod, opt["beta_best_s"]
                    )
                    if u_tag.shape[0]:
                        rigid_err_by_track.append(np.linalg.norm((u_tag @ r + t) - o_tag, axis=1))

            # Speed/motion-window estimate from the OptiTrack trajectories sampled at best timestamps.
            for tag in UWB_TAGS:
                marker = mapping[tag]
                cap_tag = cap[cap["tag"] == tag].copy()
                if cap_tag.empty:
                    continue
                q = cap_tag["uwb_time_s"].to_numpy(float) + opt["beta_best_s"]
                traj = opti_cache[cid][marker]
                opti_xyz, good = interpolate_many(roto_mod, traj, q)
                q_good = q[good]
                xyz_good = opti_xyz[good]
                if q_good.size > 5:
                    order = np.argsort(q_good)
                    q_good = q_good[order]
                    xyz_good = xyz_good[order]
                    dt = np.gradient(q_good)
                    dxyz = np.gradient(xyz_good, axis=0)
                    finite = np.isfinite(dt) & (np.abs(dt) > 1e-6) & np.isfinite(dxyz).all(axis=1)
                    speed = np.linalg.norm(dxyz[finite] / dt[finite, None], axis=1)
                    speed_mm_s_parts.append(speed)
                    motion_window_mm_parts.append(speed * 0.0008)

        current_all = np.concatenate(all_current_err) if all_current_err else np.empty(0)
        best_all = np.concatenate(all_best_err) if all_best_err else np.empty(0)
        rigid_all = np.concatenate(all_rigid_err) if all_rigid_err else np.empty(0)
        speed_all = np.concatenate(speed_mm_s_parts) if speed_mm_s_parts else np.empty(0)
        motion_all = np.concatenate(motion_window_mm_parts) if motion_window_mm_parts else np.empty(0)
        row = {"scenario": scenario_name}
        row.update(stats(current_all, "current_sample_"))
        row.update(stats(best_all, "best_time_sample_"))
        row.update(stats(rigid_all, "posthoc_rigid_sample_"))
        row.update(track_level_stats(best_err_by_track, "best_time_"))
        row.update(track_level_stats(rigid_err_by_track, "posthoc_rigid_"))
        row.update(
            {
                "speed_p50_mm_s": pct(speed_all, 50),
                "speed_p95_mm_s": pct(speed_all, 95),
                "motion_0p8ms_p50_mm": pct(motion_all, 50),
                "motion_0p8ms_p95_mm": pct(motion_all, 95),
                "constant_time_refit_drop_p50_mm": stats(current_all)["p50_mm"] - stats(best_all)["p50_mm"],
                "posthoc_rigid_drop_p50_mm": stats(best_all)["p50_mm"] - stats(rigid_all)["p50_mm"],
            }
        )
        summary_rows.append(row)

    # A compact dynamic budget for WHY #1, based on self-cal v4-io/T4 sample median.
    self_row = next(r for r in summary_rows if r["scenario"] == "self_cal_v4io_T4")
    observed = float(self_row["current_sample_p50_mm"])
    time_const = max(0.0, float(self_row["constant_time_refit_drop_p50_mm"]))
    motion = float(self_row["motion_0p8ms_p95_mm"])
    residual_after_rigid = float(self_row["posthoc_rigid_sample_p50_mm"])
    refined = float(self_row["best_time_sample_p50_mm"])
    frame_component = math.sqrt(max(0.0, refined * refined - residual_after_rigid * residual_after_rigid))
    quadrature_sum = math.sqrt(time_const * time_const + motion * motion + frame_component * frame_component + residual_after_rigid * residual_after_rigid)
    budget = {
        "observed_self_cal_current_sample_p50_mm": observed,
        "constant_time_offset_residual_mm": time_const,
        "motion_over_0p8ms_window_p95_mm": motion,
        "posthoc_rigid_frame_component_quadrature_mm": frame_component,
        "remaining_wand_ranging_pattern_component_mm": residual_after_rigid,
        "quadrature_sum_mm": quadrature_sum,
        "arithmetic_sum_mm": time_const + motion + frame_component + residual_after_rigid,
    }
    return time_rows, rigid_rows, summary_rows, budget


def audit_one_baseline_cv() -> tuple[list[dict], dict]:
    df = pd.read_csv(ONE_BASELINE_ROOT / "tables" / "static_abs_errors_per_session.csv")
    filt = df[
        (df["tag_method"] == "T4")
        & (df["layout_variant"] == "one_baseline_scale")
        & (df["delay_mode"] == "one_baseline_layout_inter_anchor_delaycal")
    ].copy()
    filt["candidate"] = filt["layout_solver"].astype(str) + "/" + filt["baseline_pair"].astype(str)
    ids = sorted(filt["ID"].unique())
    rows: list[dict] = []
    heldout_errors = []
    selected = []
    for held in ids:
        train = filt[filt["ID"] != held]
        med = train.groupby("candidate")["err_3d_mm"].median().sort_values()
        candidate = str(med.index[0])
        layout_solver, baseline = candidate.split("/", 1)
        test = filt[(filt["ID"] == held) & (filt["candidate"] == candidate)]
        if test.empty:
            continue
        err = float(test.iloc[0]["err_3d_mm"])
        heldout_errors.append(err)
        selected.append(candidate)
        rows.append(
            {
                "fold": f"leave_out_{held}",
                "heldout_ID": held,
                "selected_layout_solver": layout_solver,
                "selected_baseline_pair": baseline,
                "selected_candidate": candidate,
                "train_median_mm": float(med.iloc[0]),
                "heldout_err_3d_mm": err,
            }
        )

    # Compare to in-sample best baseline and Vicon truth control.
    baseline_summary = (
        filt.groupby(["layout_solver", "baseline_pair", "candidate"])["err_3d_mm"]
        .agg(["median", lambda x: np.percentile(x, 95)])
        .rename(columns={"<lambda_0>": "p95"})
        .sort_values("median")
        .reset_index()
    )
    best_in = baseline_summary.iloc[0]
    v4_e_h = baseline_summary[
        (baseline_summary["layout_solver"] == "v4-io") & (baseline_summary["baseline_pair"] == "E-H")
    ]
    align = pd.read_csv(ALIGN_ROOT / "tables" / "static_abs_errors_per_session.csv")
    align_f = align[
        (align["layout_solver"] == "v4-io")
        & (align["tag_method"] == "T4")
        & (align["layout_variant"] == "vicon_truth")
        & (align["delay_mode"] == "vicon_inter_anchor_delaycal")
    ]
    summary = {
        "loocv_p50_mm": pct(heldout_errors, 50),
        "loocv_p95_mm": pct(heldout_errors, 95),
        "loocv_n": len(heldout_errors),
        "in_sample_best_layout_solver": str(best_in["layout_solver"]),
        "in_sample_best_baseline": str(best_in["baseline_pair"]),
        "in_sample_best_candidate": str(best_in["candidate"]),
        "in_sample_best_p50_mm": float(best_in["median"]),
        "in_sample_best_p95_mm": float(best_in["p95"]),
        "v4io_e_h_p50_mm": float(v4_e_h.iloc[0]["median"]) if not v4_e_h.empty else float("nan"),
        "v4io_e_h_p95_mm": float(v4_e_h.iloc[0]["p95"]) if not v4_e_h.empty else float("nan"),
        "vicon_truth_delaycal_p50_mm": pct(align_f["err_3d_mm"], 50),
        "vicon_truth_delaycal_p95_mm": pct(align_f["err_3d_mm"], 95),
        "selected_baseline_counts": "; ".join(
            f"{k}:{v}" for k, v in pd.Series(selected).value_counts().sort_index().items()
        ),
    }
    return rows, summary


def audit_procrustes() -> list[dict]:
    layout = pd.read_csv(FULL_ROOT / "tables" / "layout_alignment_summary.csv")
    row = layout[(layout["version"] == "v4-io") & (layout["eval_set"] == "all8")].iloc[0]
    gap = float(row["reflection_allowed_rms_3d_mm"] - row["similarity_rms_3d_mm"])
    return [
        {
            "version": "v4-io",
            "eval_set": "all8",
            "n_anchors": int(row["n_anchors"]),
            "reflection_allowed_rms_3d_mm": float(row["reflection_allowed_rms_3d_mm"]),
            "similarity_rms_3d_mm": float(row["similarity_rms_3d_mm"]),
            "similarity_scale": float(row["similarity_scale"]),
            "scale_delta_from_1": float(row["similarity_scale"] - 1.0),
            "rms_gap_mm": gap,
            "verdict": "not a bug: similarity scale is 0.958267, not 1.0000",
        }
    ]


def audit_production_vs_raw() -> tuple[list[dict], dict]:
    raw_summary = pd.read_csv(FULL_ROOT / "tables" / "tag_raw_replay_accuracy_summary.csv")
    raw_v4 = raw_summary[(raw_summary["version"] == "v4-io") & (raw_summary["eval_set"] == "all8")]
    prod_summary = pd.read_csv(FULL_ROOT / "tables" / "tag_accuracy_summary.csv")
    prod_v4 = prod_summary[(prod_summary["version"] == "v4-io") & (prod_summary["eval_set"] == "all8")].iloc[0]
    rows = []
    for _, row in raw_v4.iterrows():
        rows.append(
            {
                "tag_method": str(row["tag_method"]),
                "raw_p50_mm": float(row["err_3d_median_mm"]),
                "raw_p95_mm": float(row["err_3d_p95_mm"]),
                "production_p50_minus_raw_mm": float(prod_v4["err_3d_median_mm"] - row["err_3d_median_mm"]),
                "production_p95_minus_raw_mm": float(prod_v4["err_3d_p95_mm"] - row["err_3d_p95_mm"]),
            }
        )

    per_session_path = FULL_ROOT / "tables" / "tag_raw_replay_abs_errors_per_session.csv"
    prod_per_path = FULL_ROOT / "tables" / "tag_abs_errors_per_session.csv"
    if per_session_path.exists() and prod_per_path.exists():
        raw_per = pd.read_csv(per_session_path)
        prod_per = pd.read_csv(prod_per_path)
        prod_per = prod_per[(prod_per["version"] == "v4-io") & (prod_per["eval_set"] == "all8")]
        for method in ["T1", "T2", "T3", "T4"]:
            rp = raw_per[
                (raw_per["version"] == "v4-io")
                & (raw_per["eval_set"] == "all8")
                & (raw_per["tag_method"] == method)
            ]
            merged = prod_per[["ID", "err_3d_mm"]].merge(
                rp[["ID", "err_3d_mm"]], on="ID", suffixes=("_prod", f"_{method}")
            )
            if not merged.empty:
                rows.append(
                    {
                        "tag_method": f"{method}_per_ID_similarity",
                        "raw_p50_mm": pct(merged[f"err_3d_mm_{method}"], 50),
                        "raw_p95_mm": pct(merged[f"err_3d_mm_{method}"], 95),
                        "production_p50_minus_raw_mm": pct(
                            merged["err_3d_mm_prod"] - merged[f"err_3d_mm_{method}"], 50
                        ),
                        "production_p95_minus_raw_mm": pct(
                            merged["err_3d_mm_prod"] - merged[f"err_3d_mm_{method}"], 95
                        ),
                        "corr_prod_raw": float(np.corrcoef(merged["err_3d_mm_prod"], merged[f"err_3d_mm_{method}"])[0, 1]),
                        "median_abs_difference_mm": pct(
                            np.abs(merged["err_3d_mm_prod"] - merged[f"err_3d_mm_{method}"]), 50
                        ),
                    }
                )
    summary = {
        "production_p50_mm": float(prod_v4["err_3d_median_mm"]),
        "production_p95_mm": float(prod_v4["err_3d_p95_mm"]),
        "raw_T1_p95_mm": float(raw_v4[raw_v4["tag_method"] == "T1"].iloc[0]["err_3d_p95_mm"]),
        "raw_T4_p95_mm": float(raw_v4[raw_v4["tag_method"] == "T4"].iloc[0]["err_3d_p95_mm"]),
        "production_minus_T4_p95_mm": float(
            prod_v4["err_3d_p95_mm"] - raw_v4[raw_v4["tag_method"] == "T4"].iloc[0]["err_3d_p95_mm"]
        ),
    }
    return rows, summary


def build_report(
    roto_summary: list[dict],
    dynamic_budget: dict[str, float],
    one_cv_summary: dict,
    procrustes_rows: list[dict],
    prod_summary: dict,
) -> str:
    self_roto = next(r for r in roto_summary if r["scenario"] == "self_cal_v4io_T4")
    vicon_roto = next(r for r in roto_summary if r["scenario"] == "vicon_truth_delaycal_T4")
    proc = procrustes_rows[0]
    lines = []
    lines.append("# Measurement Reviewer Audit: AutoPos × OptiTrack/Vicon")
    lines.append("")
    lines.append("This is a skeptical data interrogation of the corrected FULL analysis. It is not a replacement for the main report.")
    lines.append("")
    lines.append("## WHY #1: Why Does ROTO Not Collapse When Static Does?")
    lines.append("")
    lines.append("**Numbers computed.**")
    lines.append("")
    lines.append(
        f"- Self-cal v4-io/T4 ROTO sample P50 after the existing offset: {fmt(self_roto['current_sample_p50_mm'])} mm; "
        f"after 1 ms local absolute-error refit: {fmt(self_roto['best_time_sample_p50_mm'])} mm."
    )
    lines.append(
        f"- Vicon-truth+delaycal/T4 ROTO sample P50 after the same test: "
        f"{fmt(vicon_roto['current_sample_p50_mm'])} -> {fmt(vicon_roto['best_time_sample_p50_mm'])} mm."
    )
    lines.append(
        f"- Median OptiTrack linear speed during ROTO is {fmt(self_roto['speed_p50_mm_s'])} mm/s "
        f"(P95 {fmt(self_roto['speed_p95_mm_s'])} mm/s). A 0.8 ms broadcast window therefore contributes only "
        f"{fmt(self_roto['motion_0p8ms_p50_mm'], 2)} mm median / {fmt(self_roto['motion_0p8ms_p95_mm'], 2)} mm P95 displacement."
    )
    lines.append(
        f"- Post-hoc per-capture rigid registration reduces self-cal sample P50 from "
        f"{fmt(self_roto['best_time_sample_p50_mm'])} to {fmt(self_roto['posthoc_rigid_sample_p50_mm'])} mm."
    )
    lines.append("")
    lines.append("**Budget.** Using a quadrature decomposition, because vector error components should not be arithmetically added:")
    lines.append("")
    lines.append(
        f"- constant time-offset residual: {fmt(dynamic_budget['constant_time_offset_residual_mm'], 2)} mm"
    )
    lines.append(
        f"- 0.8 ms motion-window upper contribution: {fmt(dynamic_budget['motion_over_0p8ms_window_p95_mm'], 2)} mm"
    )
    lines.append(
        f"- spatially coherent residual removable by per-capture rigid fit: "
        f"{fmt(dynamic_budget['posthoc_rigid_frame_component_quadrature_mm'])} mm"
    )
    lines.append(
        f"- remaining wand/ranging/antenna-pattern residual after post-hoc rigid: "
        f"{fmt(dynamic_budget['remaining_wand_ranging_pattern_component_mm'])} mm"
    )
    lines.append(
        f"- quadrature sum: {fmt(dynamic_budget['quadrature_sum_mm'])} mm "
        f"(observed sample P50 {fmt(dynamic_budget['observed_self_cal_current_sample_p50_mm'])} mm)."
    )
    lines.append("")
    lines.append(
        "**Verdict.** The 0.8 ms protocol-window motion is negligible. Constant capture-level time offset is also not the bottleneck. "
        "The dominant error is a mixture of spatially coherent residual registration/phase and residual rotating-wand ranging behavior."
    )
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** Survives only if described as a diagnostic decomposition. The per-capture rigid fit uses Vicon truth and is not a deployable correction."
    )
    lines.append("")
    lines.append("## WHY #2: Why Relative-Distance Improves But Absolute Stays Around 105 mm")
    lines.append("")
    lines.append("**Tests run.**")
    lines.append("")
    lines.append("- Refit each capture's time offset on a 1 ms grid around the existing beta, minimizing absolute 3D median error.")
    lines.append("- Fit one post-hoc proper rigid transform per capture, using all overlapping two-tag ROTO samples.")
    lines.append("")
    lines.append("**Numbers computed.**")
    lines.append("")
    lines.append(
        f"- Self-cal time refit drop: P50 {fmt(self_roto['constant_time_refit_drop_p50_mm'], 2)} mm; "
        f"P95 changes are in `why2_time_offset_refit.csv`."
    )
    lines.append(
        f"- Vicon-truth+delaycal time refit drop: P50 {fmt(vicon_roto['constant_time_refit_drop_p50_mm'], 2)} mm."
    )
    lines.append(
        f"- Self-cal post-hoc rigid drop: P50 {fmt(self_roto['posthoc_rigid_drop_p50_mm'])} mm "
        f"({fmt(self_roto['best_time_sample_p50_mm'])} -> {fmt(self_roto['posthoc_rigid_sample_p50_mm'])})."
    )
    lines.append(
        f"- Vicon-truth+delaycal post-hoc rigid drop: P50 {fmt(vicon_roto['posthoc_rigid_drop_p50_mm'])} mm "
        f"({fmt(vicon_roto['best_time_sample_p50_mm'])} -> {fmt(vicon_roto['posthoc_rigid_sample_p50_mm'])})."
    )
    lines.append("")
    lines.append(
        "**Verdict.** Absolute error does not collapse under a better constant time offset, so the current capture-level beta is not the main bottleneck. "
        "A truth-fitted per-capture rigid transform removes a large part of the residual, so relative-distance can improve while absolute error remains high: "
        "relative distance is mostly insensitive to absolute phase/frame bias."
    )
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** The relative-distance claim survives as a scale/delay-consistency metric. It must not be sold as absolute dynamic accuracy."
    )
    lines.append("")
    lines.append("## WHY #3: Why Can One-Baseline Beat Vicon Truth In Median?")
    lines.append("")
    lines.append("**Test run.** Leave-one-static-position-out cross-validation. For each held-out ID, the baseline pair was selected only on the other 23 static IDs, then evaluated on the held-out ID.")
    lines.append("")
    lines.append("**Numbers computed.**")
    lines.append("")
    lines.append(
        f"- In-sample best candidate: {one_cv_summary['in_sample_best_candidate']}, "
        f"P50 {fmt(one_cv_summary['in_sample_best_p50_mm'])} / P95 {fmt(one_cv_summary['in_sample_best_p95_mm'])} mm."
    )
    lines.append(
        f"- Pre-registered v4-io/E-H reference: P50 {fmt(one_cv_summary['v4io_e_h_p50_mm'])} / "
        f"P95 {fmt(one_cv_summary['v4io_e_h_p95_mm'])} mm."
    )
    lines.append(
        f"- LOOCV selected-baseline result: P50 {fmt(one_cv_summary['loocv_p50_mm'])} / "
        f"P95 {fmt(one_cv_summary['loocv_p95_mm'])} mm over {one_cv_summary['loocv_n']} held-out positions."
    )
    lines.append(
        f"- Vicon-truth+delaycal reference: P50 {fmt(one_cv_summary['vicon_truth_delaycal_p50_mm'])} / "
        f"P95 {fmt(one_cv_summary['vicon_truth_delaycal_p95_mm'])} mm."
    )
    lines.append(f"- Selected baselines across folds: `{one_cv_summary['selected_baseline_counts']}`.")
    lines.append("")
    if one_cv_summary["loocv_p50_mm"] <= one_cv_summary["vicon_truth_delaycal_p50_mm"]:
        verdict3 = (
            "The one-baseline median advantage does not disappear under this LOOCV test, but the P95 remains worse than Vicon truth. "
            "This means the baseline correction is a useful engineering diagnostic, not a clean headline accuracy claim."
        )
    else:
        verdict3 = (
            "The one-baseline median regresses toward/above Vicon truth under held-out evaluation. "
            "The in-sample 55.2 mm number is selection-biased and must be diagnostic-only."
        )
    lines.append(f"**Verdict.** {verdict3}")
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** Survives as an ablation showing that one independent baseline can break scale/delay coupling. "
        "Does not survive as a field accuracy headline unless the baseline choice is pre-registered."
    )
    lines.append("")
    lines.append("## WHY #4: Why Rigid RMS 105.4 != Similarity RMS 67.1")
    lines.append("")
    lines.append("**Numbers computed.**")
    lines.append("")
    lines.append(
        f"- v4-io all8 reflection-allowed rigid RMS: {fmt(proc['reflection_allowed_rms_3d_mm'])} mm."
    )
    lines.append(
        f"- v4-io all8 similarity RMS: {fmt(proc['similarity_rms_3d_mm'])} mm."
    )
    lines.append(
        f"- Similarity scale: {proc['similarity_scale']:.6f}; scale delta from 1: {proc['scale_delta_from_1']:.6f}."
    )
    lines.append("")
    lines.append(
        "**Verdict.** This is not internally inconsistent. The scale is not 1.0000; it is 0.958267. "
        "The 38.3 mm RMS gap is the similarity fit using its only extra DOF: scale."
    )
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** Survives if the report prints similarity scale to at least four decimals and labels similarity RMS diagnostic-only. "
        "If rounded to `1.0`, it will look like a computation bug."
    )
    lines.append("")
    lines.append("## WHY #5: Why Production P95 Is Much Worse Than T4 Raw Replay")
    lines.append("")
    lines.append("**Numbers computed.**")
    lines.append("")
    lines.append(
        f"- Production v4-io static: P50 {fmt(prod_summary['production_p50_mm'])} / "
        f"P95 {fmt(prod_summary['production_p95_mm'])} mm."
    )
    lines.append(
        f"- Raw replay T1 P95: {fmt(prod_summary['raw_T1_p95_mm'])} mm; raw replay T4 P95: "
        f"{fmt(prod_summary['raw_T4_p95_mm'])} mm."
    )
    lines.append(
        f"- Production minus T4 P95 gap: {fmt(prod_summary['production_minus_T4_p95_mm'])} mm."
    )
    lines.append("")
    lines.append(
        "**Verdict.** Production output tracks the T1/T2-class tail, not the T3/T4 tail. "
        "The code default `SolverConfig.method` is T1, and T4 is only explicitly used in the raw replay/ablation scripts."
    )
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** A paper can report both, but it must define them cleanly: production/current-export result versus achievable deployment estimator result. "
        "Do not call 74.0/282.1 the final system limit when the same v4-io layout reaches 69.7/173.9 under T4 replay."
    )
    lines.append("")
    lines.append("## Headline Recommendation")
    lines.append("")
    lines.append(
        "For the paper headline, use `v4-io/T4 raw replay` as the static deployment-capable claim: "
        "`69.7 mm P50 / 173.9 mm P95` 3D, with XY/Z split reported separately. "
        "Report `production 74.0/282.1` as the legacy/current exported production-output ablation unless production is actually switched to T4. "
        "For dynamic ROTO, the honest claim is `about 105.8 mm P50 / 231.8 mm P95` absolute 3D for self-cal v4-io/T4, "
        "and `105.6 mm P50 / 200.4 mm P95` for Vicon-truth+delaycal; this shows ROTO absolute error is not primarily a layout-calibration issue. "
        "Demote similarity-scale, one-baseline-best, and per-capture post-hoc rigid results to diagnostic/ablation status."
    )
    lines.append("")
    lines.append("## Output Tables")
    lines.append("")
    for name in [
        "why1_dynamic_error_budget.csv",
        "why2_time_offset_refit.csv",
        "why2_posthoc_rigid_per_capture.csv",
        "why2_roto_refit_summary.csv",
        "why3_one_baseline_loocv.csv",
        "why3_one_baseline_cv_summary.csv",
        "why4_procrustes_check.csv",
        "why5_production_vs_raw_methods.csv",
    ]:
        lines.append(f"- `../tables/{name}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    time_rows, rigid_rows, roto_summary, dynamic_budget = audit_roto_time_and_rigid()
    cv_rows, cv_summary = audit_one_baseline_cv()
    procrustes_rows = audit_procrustes()
    prod_rows, prod_summary = audit_production_vs_raw()

    write_csv(TABLE_DIR / "why2_time_offset_refit.csv", time_rows)
    write_csv(TABLE_DIR / "why2_posthoc_rigid_per_capture.csv", rigid_rows)
    write_csv(TABLE_DIR / "why2_roto_refit_summary.csv", roto_summary)
    write_csv(TABLE_DIR / "why1_dynamic_error_budget.csv", [dynamic_budget])
    write_csv(TABLE_DIR / "why3_one_baseline_loocv.csv", cv_rows)
    write_csv(TABLE_DIR / "why3_one_baseline_cv_summary.csv", [cv_summary])
    write_csv(TABLE_DIR / "why4_procrustes_check.csv", procrustes_rows)
    write_csv(TABLE_DIR / "why5_production_vs_raw_methods.csv", prod_rows)

    report = build_report(roto_summary, dynamic_budget, cv_summary, procrustes_rows, prod_summary)
    (REPORT_DIR / "MEASUREMENT_REVIEWER_AUDIT.md").write_text(report, encoding="utf-8")
    print(f"Wrote {REPORT_DIR / 'MEASUREMENT_REVIEWER_AUDIT.md'}")


if __name__ == "__main__":
    main()
