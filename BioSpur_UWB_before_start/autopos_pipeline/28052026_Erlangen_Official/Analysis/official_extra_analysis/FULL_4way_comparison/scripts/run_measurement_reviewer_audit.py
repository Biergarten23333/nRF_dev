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

import argparse
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
SCALE_ROOT = EXTRA_ROOT / "FULL_AutoPos_scale_to_vicon"
ONE_BASELINE_ROOT = EXTRA_ROOT / "FULL_AutoPos_one_baseline_scale_correction"
OFFICIAL_ROOT = EXTRA_ROOT.parent.parent
OPTI_FULL_ROOT = OFFICIAL_ROOT / "opti_captures" / "full"
OUT_ROOT = COMP_ROOT / "reviewer_audit"
TABLE_DIR = OUT_ROOT / "tables"
REPORT_DIR = OUT_ROOT / "reports"
PRODUCTION_T4_REAL_EVAL_ROOT = COMP_ROOT / "production_method_probe" / "production_static_method_real_run_eval"

UWB_TAGS = ["BS2DCE", "BSDC91"]
PHYSICAL_TAGS = ["BSF66F", "BS2DCE", "BSDC91"]
ANCHOR_LABELS = list("ABCDEFGH")
DEFAULT_MAPPING = {"BS2DCE": "WandBantenna", "BSDC91": "WandCantenna"}
OPTITRACK_MARKERS = ["WandBantenna", "WandCantenna"]
ROTO_RADIUS_MM = {"BS2DCE": 440.0, "BSDC91": 560.0}
FIRMWARE_ANTENNA_DELAY_DTU = 16436
WHY9_MIN_CELL_N = 30
WHY9_PAIRWISE_TAG_DIFFS = [
    ("BS2DCE", "BSF66F"),
    ("BSDC91", "BSF66F"),
    ("BS2DCE", "BSDC91"),
]
WHY9_TERMINOLOGY_NOTE = (
    "firmware 16436 DTU TX=RX all devices is the antenna-delay setting; "
    "solver d_anchor_mm/tag_delay_mm are layout-level residual delay corrections "
    "fitted on top of firmware-16436 data"
)
WHY9_GAUGE_NOTE = (
    "grand is common-mode firmware-16436 miscal plus global scale plus mean coordinate; "
    "only anchor_main/tag_main differences are identifiable from tag-to-anchor ranges"
)


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


def import_static_ablation_module():
    path = COMP_ROOT / "scripts" / "run_static_layout_ablation.py"
    spec = importlib.util.spec_from_file_location("full_static_ablation", path)
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
    return capture_errors_at_beta_alpha(samples, opti_cache, mapping, roto_mod, beta_s, alpha=0.0)


def capture_errors_at_beta_alpha(
    samples: pd.DataFrame,
    opti_cache: dict[str, dict[str, object]],
    mapping: dict[str, str],
    roto_mod,
    beta_s: float,
    alpha: float,
    t_ref_s: float | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if samples.empty:
        empty = np.empty((0, 3), dtype=float)
        return np.empty(0), empty, empty, np.empty(0)
    if t_ref_s is None:
        t_ref_s = float(np.nanmin(samples["uwb_time_s"].to_numpy(float)))
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
        uwb_time = group["uwb_time_s"].to_numpy(float)
        q = uwb_time + beta_s + float(alpha) * (uwb_time - float(t_ref_s))
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


def beta_alpha_grid_scores(
    samples: pd.DataFrame,
    opti_cache: dict[str, dict[str, object]],
    mapping: dict[str, str],
    beta_candidates_s: np.ndarray,
    alpha_candidates: np.ndarray,
    t_ref_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized pooled median/P95 errors for candidate beta/alpha pairs."""
    beta = np.asarray(beta_candidates_s, dtype=float)
    alpha = np.asarray(alpha_candidates, dtype=float)
    if beta.shape != alpha.shape:
        raise ValueError(f"candidate shape mismatch: {beta.shape} vs {alpha.shape}")
    candidate_count = beta.size
    err_blocks = []
    for tag, group in samples.groupby("tag"):
        if tag not in mapping:
            continue
        marker = mapping[tag]
        cid = str(group["capture_id"].iloc[0])
        traj = opti_cache[cid][marker]
        uwb_time = group["uwb_time_s"].to_numpy(float)
        uwb_xyz = group[["uwb_x_mm", "uwb_y_vertical_mm", "uwb_z_mm"]].to_numpy(float)
        q = uwb_time[None, :] + beta[:, None] + alpha[:, None] * (uwb_time[None, :] - float(t_ref_s))
        q_flat = q.reshape(-1)
        opti_xyz = np.empty((candidate_count, uwb_time.size, 3), dtype=float)
        for axis in range(3):
            opti_xyz[:, :, axis] = np.interp(
                q_flat,
                traj.time_s,
                traj.xyz_mm[:, axis],
                left=np.nan,
                right=np.nan,
            ).reshape(candidate_count, uwb_time.size)
        diff = uwb_xyz[None, :, :] - opti_xyz
        err = np.linalg.norm(diff, axis=2)
        err[~np.isfinite(err)] = np.nan
        err_blocks.append(err)
    if not err_blocks:
        return (
            np.full(candidate_count, np.inf),
            np.full(candidate_count, np.inf),
            np.zeros(candidate_count, dtype=int),
        )
    pooled = np.concatenate(err_blocks, axis=1)
    n_finite = np.sum(np.isfinite(pooled), axis=1).astype(int)
    with np.errstate(all="ignore"):
        med = np.nanmedian(pooled, axis=1)
        p95 = np.nanpercentile(pooled, 95, axis=1)
    med[~np.isfinite(med)] = np.inf
    p95[~np.isfinite(p95)] = np.inf
    return med, p95, n_finite


def optimize_capture_beta_alpha(
    samples: pd.DataFrame,
    const_beta_s: float,
    opti_cache: dict[str, dict[str, object]],
    mapping: dict[str, str],
    roto_mod,
) -> dict:
    if samples.empty:
        raise ValueError("cannot optimize skew on empty capture")
    t_ref_s = float(np.nanmin(samples["uwb_time_s"].to_numpy(float)))

    coarse_alpha_ppm = np.arange(-300.0, 300.0 + 1e-9, 10.0)
    coarse_beta = np.arange(float(const_beta_s) - 0.050, float(const_beta_s) + 0.050 + 0.0005, 0.001)
    alpha_grid_ppm, beta_grid = np.meshgrid(coarse_alpha_ppm, coarse_beta, indexing="ij")
    coarse_med, _coarse_p95, coarse_n = beta_alpha_grid_scores(
        samples,
        opti_cache,
        mapping,
        beta_grid.reshape(-1),
        (alpha_grid_ppm.reshape(-1) * 1e-6),
        t_ref_s,
    )
    coarse_best_i = int(np.nanargmin(coarse_med))
    coarse_best_beta = float(beta_grid.reshape(-1)[coarse_best_i])
    coarse_best_alpha_ppm = float(alpha_grid_ppm.reshape(-1)[coarse_best_i])

    fine_alpha_ppm = np.arange(coarse_best_alpha_ppm - 20.0, coarse_best_alpha_ppm + 20.0 + 1e-9, 1.0)
    fine_beta = np.arange(coarse_best_beta - 0.005, coarse_best_beta + 0.005 + 0.00025, 0.0005)
    fine_alpha_grid_ppm, fine_beta_grid = np.meshgrid(fine_alpha_ppm, fine_beta, indexing="ij")
    fine_med, fine_p95, fine_n = beta_alpha_grid_scores(
        samples,
        opti_cache,
        mapping,
        fine_beta_grid.reshape(-1),
        (fine_alpha_grid_ppm.reshape(-1) * 1e-6),
        t_ref_s,
    )
    fine_best_i = int(np.nanargmin(fine_med))
    beta_best = float(fine_beta_grid.reshape(-1)[fine_best_i])
    alpha_best_ppm = float(fine_alpha_grid_ppm.reshape(-1)[fine_best_i])
    alpha_best = alpha_best_ppm * 1e-6
    best_err, best_uwb, best_opti, best_time = capture_errors_at_beta_alpha(
        samples,
        opti_cache,
        mapping,
        roto_mod,
        beta_best,
        alpha_best,
        t_ref_s=t_ref_s,
    )
    return {
        "t_ref_s": t_ref_s,
        "beta_best_s": beta_best,
        "alpha_best": alpha_best,
        "alpha_best_ppm": alpha_best_ppm,
        "skew_best_p50_mm": pct(best_err, 50),
        "skew_best_p95_mm": pct(best_err, 95),
        "n_best": int(best_err.size),
        "coarse_best_beta_s": coarse_best_beta,
        "coarse_best_alpha_ppm": coarse_best_alpha_ppm,
        "coarse_best_p50_mm": float(coarse_med[coarse_best_i]),
        "coarse_best_n": int(coarse_n[coarse_best_i]),
        "fine_best_n": int(fine_n[fine_best_i]),
        "best_err": best_err,
        "best_uwb": best_uwb,
        "best_opti": best_opti,
        "best_time": best_time,
    }


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


def add_roto_stat_aliases(row: dict, model_prefixes: list[str]) -> None:
    for prefix in model_prefixes:
        if f"{prefix}_sample_p50_mm" in row:
            row[f"{prefix}_samplepooled_p50_mm"] = row[f"{prefix}_sample_p50_mm"]
            row[f"{prefix}_samplepooled_p95_mm"] = row.get(f"{prefix}_sample_p95_mm", float("nan"))
        track_p50_key = f"{prefix}_track_p50_of_p50_mm"
        track_p95_key = f"{prefix}_track_p50_of_p95_mm"
        if track_p50_key in row:
            row[f"{prefix}_trackmedian_p50_mm"] = row[track_p50_key]
            row[f"{prefix}_trackmedian_p95_mm"] = row.get(track_p95_key, float("nan"))


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
        current_err_by_track: list[np.ndarray] = []
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
            for tag in UWB_TAGS:
                cap_tag = cap[cap["tag"] == tag].copy()
                if cap_tag.empty:
                    continue
                err_tag, _u, _o, _t = capture_errors_at_beta(cap_tag, opti_cache, mapping, roto_mod, beta0)
                if err_tag.size:
                    current_err_by_track.append(err_tag)

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
        row.update(track_level_stats(current_err_by_track, "current_"))
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
        add_roto_stat_aliases(row, ["current", "best_time", "posthoc_rigid"])
        summary_rows.append(row)

    # A compact dynamic budget for WHY #1.  These are descriptive median/P95
    # quantities, not independent RMS components; do not quadrature-sum them.
    self_row = next(r for r in summary_rows if r["scenario"] == "self_cal_v4io_T4")
    excluded_constant_offset = float(self_row["constant_time_refit_drop_p50_mm"])
    motion = float(self_row["motion_0p8ms_p95_mm"])
    oracle_rigid_removable = float(self_row["best_time_sample_p50_mm"] - self_row["posthoc_rigid_sample_p50_mm"])
    unattributed = float(self_row["posthoc_rigid_sample_p50_mm"])
    speed_p50 = float(self_row["speed_p50_mm_s"])
    timing_bound_ms = 1000.0 * unattributed / speed_p50 if speed_p50 > 0.0 else float("nan")
    timing_bound_skew_ppm_120s = (timing_bound_ms / 1000.0) / 120.0 * 1e6 if math.isfinite(timing_bound_ms) else float("nan")
    budget = {
        "observed_self_cal_current_samplepooled_p50_mm": float(self_row["current_sample_p50_mm"]),
        "observed_self_cal_current_trackmedian_p50_mm": float(self_row["current_trackmedian_p50_mm"]),
        "constant_offset_best_samplepooled_p50_mm": float(self_row["best_time_sample_p50_mm"]),
        "constant_offset_best_trackmedian_p50_mm": float(self_row["best_time_trackmedian_p50_mm"]),
        "excluded_constant_offset_p50_mm": excluded_constant_offset,
        "excluded_motion_window_p95_mm": motion,
        "oracle_rigid_removable_p50_mm": oracle_rigid_removable,
        "unattributed_per_sample_residual_p50_mm": unattributed,
        "median_roto_speed_mm_s": speed_p50,
        "equivalent_timing_error_needed_ms": timing_bound_ms,
        "equivalent_skew_needed_over_120s_ppm": timing_bound_skew_ppm_120s,
        "budget_note": "descriptive medians/P95 only; no quadrature sum because that is tautological for percentiles",
    }
    return time_rows, rigid_rows, summary_rows, budget


def rms(values: np.ndarray | list[float]) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(math.sqrt(float(np.mean(arr * arr))))


def fit_roto_circle_self_consistency(points: np.ndarray) -> dict:
    """Fit the old no-groundtruth ROTO circle model on UWB points only."""
    pts = np.asarray(points, dtype=float)
    finite = np.isfinite(pts).all(axis=1)
    pts = pts[finite]
    if pts.shape[0] < 20:
        return {"status": "insufficient", "n": int(pts.shape[0])}
    center0 = np.mean(pts, axis=0)
    _u, _s, vh = np.linalg.svd(pts - center0, full_matrices=False)
    normal = vh[-1]
    e1, e2 = vh[0], vh[1]
    uv = np.column_stack([(pts - center0) @ e1, (pts - center0) @ e2])
    x, y = uv[:, 0], uv[:, 1]
    a = np.column_stack([2.0 * x, 2.0 * y, np.ones_like(x)])
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    cx, cy, c = sol
    radius = math.sqrt(max(0.0, float(c + cx * cx + cy * cy)))
    radial = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - radius
    zplane = (pts - center0) @ normal
    total = np.sqrt(radial * radial + zplane * zplane)
    center3 = center0 + cx * e1 + cy * e2
    theta = np.unwrap(np.arctan2(y - cy, x - cx))
    if theta.size and theta[-1] < theta[0]:
        theta = -theta
    return {
        "status": "ok",
        "n": int(pts.shape[0]),
        "radius_mm": float(radius),
        "center": center3,
        "circle_thickness_rms_mm": rms(total),
        "circle_thickness_p95_mm": pct(total, 95),
        "theta": theta,
    }


def per_turn_center_self_consistency(points: np.ndarray, theta: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    th = np.asarray(theta, dtype=float)
    if pts.shape[0] < 80 or th.size != pts.shape[0]:
        return {"turn_count": 0}
    th = th - th[0]
    estimated_turns = float((th[-1] - th[0]) / (2.0 * math.pi))
    bins = np.floor(th / (2.0 * math.pi)).astype(int)
    centers = []
    for b in range(int(np.min(bins)), int(np.max(bins)) + 1):
        idx = np.where(bins == b)[0]
        if idx.size < 30:
            continue
        fit = fit_roto_circle_self_consistency(pts[idx])
        if fit.get("status") == "ok":
            centers.append(np.asarray(fit["center"], dtype=float))
    if len(centers) < 2:
        return {"turn_count": len(centers), "estimated_turns": estimated_turns}
    c = np.vstack(centers)
    mean = np.mean(c, axis=0)
    dist = np.linalg.norm(c - mean, axis=1)
    std = np.std(c, axis=0, ddof=1)
    return {
        "turn_count": int(len(centers)),
        "estimated_turns": estimated_turns,
        "turn_center_rms_3d_mm": rms(dist),
        "turn_center_p95_3d_mm": pct(dist, 95),
        "turn_center_x_std_mm": float(std[0]),
        "turn_center_y_std_mm": float(std[1]),
        "turn_center_z_std_mm": float(std[2]),
    }


def filter_track_table(df: pd.DataFrame, filters: dict[str, str]) -> pd.DataFrame:
    out = df.copy()
    if "status" in out.columns:
        out = out[out["status"].astype(str) == "ok"]
    for key, value in filters.items():
        if key not in out.columns:
            raise KeyError(f"missing filter column {key!r} in ROTO per-track table")
        out = out[out[key].astype(str) == str(value)]
    return out.copy()


def legacy_roto_self_consistency(source_root: Path, filters: dict[str, str]) -> dict:
    empty = {
        "legacy_track_count": 0,
        "legacy_capture_pairs": 0,
        "legacy_deltaR_error_mean_mm": float("nan"),
        "legacy_deltaR_error_rms_mm": float("nan"),
        "legacy_abs_deltaR_error_median_mm": float("nan"),
        "legacy_abs_deltaR_error_p95_mm": float("nan"),
        "legacy_inner_outer_center_sep_median_mm": float("nan"),
        "legacy_inner_outer_center_sep_p95_mm": float("nan"),
        "legacy_turn_center_rms_median_mm": float("nan"),
        "legacy_turn_center_rms_p95_mm": float("nan"),
        "legacy_turn_center_p95_median_mm": float("nan"),
        "legacy_circle_thickness_rms_median_mm": float("nan"),
        "legacy_circle_thickness_rms_p95_mm": float("nan"),
        "legacy_radius_median_mm": float("nan"),
        "legacy_self_consistency_note": "unavailable for this row",
    }
    path = source_root / "roto_absolute" / "tables" / "roto_abs_samples_v4io_T4.csv"
    df = pd.read_csv(path)
    rows = filter_track_table(df, filters)
    if rows.empty:
        return empty
    track_rows = []
    for (capture_id, tag), group in rows.groupby(["capture_id", "tag"]):
        pts = group.sort_values("uwb_time_s")[
            ["uwb_x_mm", "uwb_y_vertical_mm", "uwb_z_mm"]
        ].to_numpy(float)
        finite = np.isfinite(pts).all(axis=1)
        pts = pts[finite]
        fit = fit_roto_circle_self_consistency(pts)
        if fit.get("status") != "ok":
            continue
        turns = per_turn_center_self_consistency(pts, np.asarray(fit["theta"], dtype=float))
        track_rows.append(
            {
                "capture_id": str(capture_id),
                "tag": str(tag),
                "radius_mm": float(fit["radius_mm"]),
                "center": np.asarray(fit["center"], dtype=float),
                "circle_thickness_rms_mm": float(fit["circle_thickness_rms_mm"]),
                "circle_thickness_p95_mm": float(fit["circle_thickness_p95_mm"]),
                "turn_center_rms_3d_mm": float(turns.get("turn_center_rms_3d_mm", float("nan"))),
                "turn_center_p95_3d_mm": float(turns.get("turn_center_p95_3d_mm", float("nan"))),
                "turn_count": int(turns.get("turn_count", 0)),
            }
        )
    by_capture: dict[str, dict[str, dict]] = {}
    for row in track_rows:
        by_capture.setdefault(row["capture_id"], {})[row["tag"]] = row
    pair_rows = []
    for capture_id, by_tag in sorted(by_capture.items()):
        if "BS2DCE" not in by_tag or "BSDC91" not in by_tag:
            continue
        inner = by_tag["BS2DCE"]
        outer = by_tag["BSDC91"]
        delta_r = float(outer["radius_mm"] - inner["radius_mm"])
        pair_rows.append(
            {
                "capture_id": capture_id,
                "deltaR_mm": delta_r,
                "deltaR_error_mm": delta_r - (ROTO_RADIUS_MM["BSDC91"] - ROTO_RADIUS_MM["BS2DCE"]),
                "inner_outer_center_sep_mm": float(np.linalg.norm(outer["center"] - inner["center"])),
            }
        )
    delta_errors = [r["deltaR_error_mm"] for r in pair_rows]
    center_sep = [r["inner_outer_center_sep_mm"] for r in pair_rows]
    turn_rms = [r["turn_center_rms_3d_mm"] for r in track_rows]
    turn_p95 = [r["turn_center_p95_3d_mm"] for r in track_rows]
    thickness = [r["circle_thickness_rms_mm"] for r in track_rows]
    radius_vals = [r["radius_mm"] for r in track_rows]
    return {
        "legacy_track_count": int(len(track_rows)),
        "legacy_capture_pairs": int(len(pair_rows)),
        "legacy_deltaR_error_mean_mm": float(np.nanmean(delta_errors)) if delta_errors else float("nan"),
        "legacy_deltaR_error_rms_mm": rms(delta_errors),
        "legacy_abs_deltaR_error_median_mm": pct(np.abs(np.asarray(delta_errors, dtype=float)), 50),
        "legacy_abs_deltaR_error_p95_mm": pct(np.abs(np.asarray(delta_errors, dtype=float)), 95),
        "legacy_inner_outer_center_sep_median_mm": pct(center_sep, 50),
        "legacy_inner_outer_center_sep_p95_mm": pct(center_sep, 95),
        "legacy_turn_center_rms_median_mm": pct(turn_rms, 50),
        "legacy_turn_center_rms_p95_mm": pct(turn_rms, 95),
        "legacy_turn_center_p95_median_mm": pct(turn_p95, 50),
        "legacy_circle_thickness_rms_median_mm": pct(thickness, 50),
        "legacy_circle_thickness_rms_p95_mm": pct(thickness, 95),
        "legacy_radius_median_mm": pct(radius_vals, 50),
        "legacy_self_consistency_note": "computed from UWB samples only; no OptiTrack truth used",
    }


def circle_metrics_row(case: str, source_root: Path, filters: dict[str, str], note: str) -> dict:
    path = source_root / "roto_absolute" / "tables" / "roto_abs_per_track.csv"
    df = pd.read_csv(path)
    rows = filter_track_table(df, filters)
    if rows.empty:
        raise RuntimeError(f"no ROTO center rows for {case} using filters {filters}")
    center_3d = rows["turn_center_abs_error_3d_mm"].to_numpy(float)
    center_xz = rows["turn_center_abs_error_horizontal_xz_mm"].to_numpy(float)
    center_y = rows["turn_center_abs_error_vertical_y_mm"].to_numpy(float)
    radius_abs = np.abs(rows["radius_error_mm"].to_numpy(float))
    row = {
        "case": case,
        "source_root": str(source_root.relative_to(EXTRA_ROOT)),
        "n_tracks": int(len(rows)),
        "filters": "; ".join(f"{k}={v}" for k, v in filters.items()),
        "opti_turn_center_abs_error_3d_p50_mm": pct(center_3d, 50),
        "opti_turn_center_abs_error_3d_p95_mm": pct(center_3d, 95),
        "opti_turn_center_abs_error_3d_rms_mm": rms(center_3d),
        "opti_turn_center_abs_error_xz_p50_mm": pct(center_xz, 50),
        "opti_turn_center_abs_error_xz_p95_mm": pct(center_xz, 95),
        "opti_turn_center_abs_error_xz_rms_mm": rms(center_xz),
        "opti_turn_center_abs_error_y_p50_mm": pct(center_y, 50),
        "opti_turn_center_abs_error_y_p95_mm": pct(center_y, 95),
        "opti_turn_center_abs_error_y_rms_mm": rms(center_y),
        "opti_radius_error_abs_p50_mm": pct(radius_abs, 50),
        "opti_radius_error_abs_p95_mm": pct(radius_abs, 95),
        "opti_radius_error_rms_mm": rms(rows["radius_error_mm"].to_numpy(float)),
        "interpretation_note": note,
    }
    row.update(legacy_roto_self_consistency(source_root, filters))
    return row


def audit_roto_circle_metrics() -> list[dict]:
    """Old no-groundtruth ROTO self-consistency plus new OptiTrack absolute circle metrics."""
    configs = [
        (
            "full_original_v4io_T4",
            FULL_ROOT,
            {"layout": "v4-io", "tag_method": "T4"},
            "original FULL self-cal v4-io/T4",
        ),
        (
            "vicon_truth_delaycal_v4io_T4",
            ALIGN_ROOT,
            {
                "layout_solver": "v4-io",
                "layout_variant": "vicon_truth",
                "delay_mode": "vicon_inter_anchor_delaycal",
                "tag_method": "T4",
            },
            "Vicon/OptiTrack anchor truth with inter-anchor delaycal",
        ),
        (
            "scale_to_vicon_delaycal_v4io_T4",
            SCALE_ROOT,
            {
                "layout_solver": "v4-io",
                "layout_variant": "solver_similarity_scale_to_vicon",
                "delay_mode": "scaled_layout_inter_anchor_delaycal",
                "tag_method": "T4",
            },
            "full similarity scale-to-Vicon with re-estimated delaycal",
        ),
        (
            "one_baseline_EH_delaycal_v4io_T4",
            ONE_BASELINE_ROOT,
            {
                "layout_solver": "v4-io",
                "layout_variant": "one_baseline_scale",
                "delay_mode": "one_baseline_layout_inter_anchor_delaycal",
                "tag_method": "T4",
                "baseline_pair": "E-H",
            },
            "pre-registered one-baseline E-H control",
        ),
        (
            "one_baseline_best_roto_solver_delay_v4io_T4_BC",
            ONE_BASELINE_ROOT,
            {
                "layout_solver": "v4-io",
                "layout_variant": "one_baseline_scale",
                "delay_mode": "solver_delay",
                "tag_method": "T4",
                "baseline_pair": "B-C",
            },
            "best overall ROTO row in the 4-way comparison",
        ),
    ]
    return [circle_metrics_row(case, root, filters, note) for case, root, filters, note in configs]


def import_roto_filtered_module():
    path = COMP_ROOT / "scripts" / "run_roto_filtered_replay.py"
    spec = importlib.util.spec_from_file_location("full_roto_filtered_replay", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def audit_roto_filtered_replay() -> tuple[list[dict], list[dict]]:
    """ROTO post-solve trajectory filtering across the 4-way comparison cases."""
    summary_path = COMP_ROOT / "roto_filtered" / "tables" / "roto_filtered_summary.csv"
    per_track_path = COMP_ROOT / "roto_filtered" / "tables" / "roto_filtered_per_track.csv"
    if not summary_path.exists() or not per_track_path.exists():
        filt_mod = import_roto_filtered_module()
        summary_rows, per_track_rows = filt_mod.run_matrix()
        filt_mod.write_csv(filt_mod.TABLE_DIR / "roto_filtered_summary.csv", summary_rows)
        filt_mod.write_csv(filt_mod.TABLE_DIR / "roto_filtered_per_track.csv", per_track_rows)
        filt_mod.write_report(summary_rows)
        return summary_rows, per_track_rows
    return pd.read_csv(summary_path).to_dict("records"), pd.read_csv(per_track_path).to_dict("records")


def import_roto_pseudo_imu_module():
    path = COMP_ROOT / "scripts" / "run_roto_pseudo_imu_replay.py"
    spec = importlib.util.spec_from_file_location("full_roto_pseudo_imu_replay", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def audit_roto_pseudo_imu_replay() -> tuple[list[dict], list[dict], list[dict]]:
    """OptiTrack-derived pseudo-IMU relative-motion prior across the 4-way ROTO cases."""
    summary_path = COMP_ROOT / "roto_pseudo_imu" / "tables" / "roto_pseudo_imu_summary.csv"
    per_track_path = COMP_ROOT / "roto_pseudo_imu" / "tables" / "roto_pseudo_imu_per_track.csv"
    extrinsics_path = COMP_ROOT / "roto_pseudo_imu" / "tables" / "roto_pseudo_imu_extrinsics.csv"
    if not summary_path.exists() or not per_track_path.exists() or not extrinsics_path.exists():
        pseudo_mod = import_roto_pseudo_imu_module()
        summary_rows, per_track_rows, extrinsic_rows = pseudo_mod.run_matrix()
        pseudo_mod.write_csv(pseudo_mod.TABLE_DIR / "roto_pseudo_imu_summary.csv", summary_rows)
        pseudo_mod.write_csv(pseudo_mod.TABLE_DIR / "roto_pseudo_imu_per_track.csv", per_track_rows)
        pseudo_mod.write_csv(pseudo_mod.TABLE_DIR / "roto_pseudo_imu_extrinsics.csv", extrinsic_rows)
        pseudo_mod.write_report(summary_rows, extrinsic_rows)
        return summary_rows, per_track_rows, extrinsic_rows
    return (
        pd.read_csv(summary_path).to_dict("records"),
        pd.read_csv(per_track_path).to_dict("records"),
        pd.read_csv(extrinsics_path).to_dict("records"),
    )


def skew_verdict_for_summary(row: dict) -> tuple[str, str]:
    drop = float(row["marginal_drop_p50_pooled_mm"])
    sign_consistency = float(row["alpha_sign_consistency"])
    alpha_median_abs = abs(float(row["alpha_ppm_median"]))
    if drop >= 10.0 and sign_consistency >= 0.8 and 10.0 <= alpha_median_abs <= 200.0:
        return (
            "SKEW_PHYSICAL",
            "Apply a per-capture linear time model before quoting dynamic accuracy; the narrative shifts to identified cross-clock skew.",
        )
    if drop < 10.0:
        return (
            "SKEW_EXCLUDED",
            "Offset/skew/jitter have no material leverage on the observed dynamic residual; do not convert this into a clock-synchronization ppm claim.",
        )
    return (
        "SKEW_INCONCLUSIVE_OVERFIT_RISK",
        "Do not act on the skew fit yet; the alpha pattern is not physically stable enough without held-out validation.",
    )


def audit_roto_time_skew(const_time_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    roto_mod = import_roto_module()
    mapping = selected_wand_mapping()
    offsets = pd.read_csv(FULL_ROOT / "roto_absolute" / "tables" / "roto_time_offsets_v4io_T4.csv")
    capture_ids = sorted(offsets.loc[offsets["status"] == "ok", "capture_id"].astype(str).tolist())
    opti_cache = load_opti_cache(roto_mod, capture_ids)
    const_by_key = {(str(r["scenario"]), str(r["capture_id"])): r for r in const_time_rows}

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
    per_capture_rows: list[dict] = []
    summary_rows: list[dict] = []

    for scenario in scenarios:
        scenario_name = scenario["scenario"]
        samples_all = pd.read_csv(scenario["sample_path"])
        current_all = []
        const_all = []
        skew_all = []
        current_tracks: list[np.ndarray] = []
        const_tracks: list[np.ndarray] = []
        skew_tracks: list[np.ndarray] = []
        alpha_ppm_values = []

        for _, off in offsets[offsets["status"] == "ok"].iterrows():
            cid = str(off["capture_id"])
            key = (scenario_name, cid)
            if key not in const_by_key:
                raise KeyError(f"missing const-offset row for {key}")
            const_row = const_by_key[key]
            beta0 = float(const_row["beta0_s"])
            const_beta = float(const_row["beta_best_s"])
            cap = samples_all[samples_all["capture_id"].astype(str) == cid].copy()
            if cap.empty:
                raise ValueError(f"empty ROTO sample set for {scenario_name}/{cid}")
            t_ref_s = float(np.nanmin(cap["uwb_time_s"].to_numpy(float)))
            skew = optimize_capture_beta_alpha(cap, const_beta, opti_cache, mapping, roto_mod)
            alpha_ppm_values.append(float(skew["alpha_best_ppm"]))

            current_err, _u0, _o0, _t0 = capture_errors_at_beta_alpha(
                cap, opti_cache, mapping, roto_mod, beta0, 0.0, t_ref_s=t_ref_s
            )
            const_err, _uc, _oc, _tc = capture_errors_at_beta_alpha(
                cap, opti_cache, mapping, roto_mod, const_beta, 0.0, t_ref_s=t_ref_s
            )
            skew_err = skew["best_err"]
            current_all.append(current_err)
            const_all.append(const_err)
            skew_all.append(skew_err)

            for tag in UWB_TAGS:
                cap_tag = cap[cap["tag"] == tag].copy()
                if cap_tag.empty:
                    continue
                err0, _u, _o, _t = capture_errors_at_beta_alpha(
                    cap_tag, opti_cache, mapping, roto_mod, beta0, 0.0, t_ref_s=t_ref_s
                )
                errc, _u, _o, _t = capture_errors_at_beta_alpha(
                    cap_tag, opti_cache, mapping, roto_mod, const_beta, 0.0, t_ref_s=t_ref_s
                )
                errs, _u, _o, _t = capture_errors_at_beta_alpha(
                    cap_tag,
                    opti_cache,
                    mapping,
                    roto_mod,
                    float(skew["beta_best_s"]),
                    float(skew["alpha_best"]),
                    t_ref_s=t_ref_s,
                )
                if err0.size:
                    current_tracks.append(err0)
                if errc.size:
                    const_tracks.append(errc)
                if errs.size:
                    skew_tracks.append(errs)

            per_capture_rows.append(
                {
                    "scenario": scenario_name,
                    "capture_id": cid,
                    "n": int(skew["n_best"]),
                    "t_ref_s": t_ref_s,
                    "beta0_s": beta0,
                    "const_offset_beta_best_s": const_beta,
                    "beta_best_s": float(skew["beta_best_s"]),
                    "alpha_best_ppm": float(skew["alpha_best_ppm"]),
                    "const_offset_best_p50_mm": float(const_row["best_p50_mm"]),
                    "const_offset_best_p95_mm": float(const_row["best_p95_mm"]),
                    "skew_best_p50_mm": float(skew["skew_best_p50_mm"]),
                    "skew_best_p95_mm": float(skew["skew_best_p95_mm"]),
                    "marginal_drop_vs_const_p50_mm": float(const_row["best_p50_mm"] - skew["skew_best_p50_mm"]),
                    "marginal_drop_vs_const_p95_mm": float(const_row["best_p95_mm"] - skew["skew_best_p95_mm"]),
                    "coarse_best_beta_s": float(skew["coarse_best_beta_s"]),
                    "coarse_best_alpha_ppm": float(skew["coarse_best_alpha_ppm"]),
                    "coarse_best_p50_mm": float(skew["coarse_best_p50_mm"]),
                }
            )

        current_arr = np.concatenate(current_all) if current_all else np.empty(0)
        const_arr = np.concatenate(const_all) if const_all else np.empty(0)
        skew_arr = np.concatenate(skew_all) if skew_all else np.empty(0)
        alpha_arr = np.asarray(alpha_ppm_values, dtype=float)
        finite_alpha = alpha_arr[np.isfinite(alpha_arr)]
        pos = int(np.sum(finite_alpha > 0.0))
        neg = int(np.sum(finite_alpha < 0.0))
        sign_consistency = float(max(pos, neg) / finite_alpha.size) if finite_alpha.size else float("nan")
        row = {
            "scenario": scenario_name,
            "capture_count": int(len(finite_alpha)),
            "current_beta0_samplepooled_p50_mm": pct(current_arr, 50),
            "current_beta0_samplepooled_p95_mm": pct(current_arr, 95),
            "const_offset_best_samplepooled_p50_mm": pct(const_arr, 50),
            "const_offset_best_samplepooled_p95_mm": pct(const_arr, 95),
            "skew_best_samplepooled_p50_mm": pct(skew_arr, 50),
            "skew_best_samplepooled_p95_mm": pct(skew_arr, 95),
            "current_beta0_trackmedian_p50_mm": pct([pct(e, 50) for e in current_tracks], 50),
            "current_beta0_trackmedian_p95_mm": pct([pct(e, 95) for e in current_tracks], 50),
            "const_offset_best_trackmedian_p50_mm": pct([pct(e, 50) for e in const_tracks], 50),
            "const_offset_best_trackmedian_p95_mm": pct([pct(e, 95) for e in const_tracks], 50),
            "skew_best_trackmedian_p50_mm": pct([pct(e, 50) for e in skew_tracks], 50),
            "skew_best_trackmedian_p95_mm": pct([pct(e, 95) for e in skew_tracks], 50),
            "alpha_ppm_median": pct(finite_alpha, 50),
            "alpha_ppm_iqr": pct(finite_alpha, 75) - pct(finite_alpha, 25),
            "alpha_sign_consistency": sign_consistency,
            "alpha_ppm_min": float(np.min(finite_alpha)) if finite_alpha.size else float("nan"),
            "alpha_ppm_max": float(np.max(finite_alpha)) if finite_alpha.size else float("nan"),
        }
        row["marginal_drop_p50_pooled_mm"] = (
            row["const_offset_best_samplepooled_p50_mm"] - row["skew_best_samplepooled_p50_mm"]
        )
        row["marginal_drop_p50_trackmedian_mm"] = (
            row["const_offset_best_trackmedian_p50_mm"] - row["skew_best_trackmedian_p50_mm"]
        )
        verdict, consequence = skew_verdict_for_summary(row)
        row["skew_verdict"] = verdict
        row["paper_consequence"] = consequence
        summary_rows.append(row)

    return per_capture_rows, summary_rows


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
    prod_t4_p50 = float("nan")
    prod_t4_p95 = float("nan")
    prod_t4_rmse = float("nan")
    prod_t4_summary_path = PRODUCTION_T4_REAL_EVAL_ROOT / "tables" / "tag_accuracy_summary.csv"
    if prod_t4_summary_path.exists():
        prod_t4_summary = pd.read_csv(prod_t4_summary_path)
        prod_t4 = prod_t4_summary[
            (prod_t4_summary["version"] == "v4-io") & (prod_t4_summary["eval_set"] == "all8")
        ].iloc[0]
        prod_t4_p50 = float(prod_t4["err_3d_median_mm"])
        prod_t4_p95 = float(prod_t4["err_3d_p95_mm"])
        prod_t4_rmse = float(prod_t4["err_3d_rms_mm"])
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
        "legacy_production_T1_mean_p50_mm": float(prod_v4["err_3d_median_mm"]),
        "legacy_production_T1_mean_p95_mm": float(prod_v4["err_3d_p95_mm"]),
        "legacy_production_T1_mean_rmse_mm": float(prod_v4["err_3d_rms_mm"]),
        "production_T4_mean_p50_mm": prod_t4_p50,
        "production_T4_mean_p95_mm": prod_t4_p95,
        "production_T4_mean_rmse_mm": prod_t4_rmse,
        "raw_T4_median_p50_mm": float(raw_v4[raw_v4["tag_method"] == "T4"].iloc[0]["err_3d_median_mm"]),
        "raw_T4_median_rmse_mm": float(raw_v4[raw_v4["tag_method"] == "T4"].iloc[0]["err_3d_rms_mm"]),
        "raw_T1_p95_mm": float(raw_v4[raw_v4["tag_method"] == "T1"].iloc[0]["err_3d_p95_mm"]),
        "raw_T4_p95_mm": float(raw_v4[raw_v4["tag_method"] == "T4"].iloc[0]["err_3d_p95_mm"]),
        "legacy_production_T1_mean_minus_raw_T4_p95_mm": float(
            prod_v4["err_3d_p95_mm"] - raw_v4[raw_v4["tag_method"] == "T4"].iloc[0]["err_3d_p95_mm"]
        ),
    }
    return rows, summary


def production_t4_real_run_rows(prod_summary: dict) -> list[dict]:
    return [
        {
            "case": "legacy_production_T1_mean",
            "source": "FULL/tables/tag_accuracy_summary.csv",
            "p50_3d_mm": prod_summary["legacy_production_T1_mean_p50_mm"],
            "p95_3d_mm": prod_summary["legacy_production_T1_mean_p95_mm"],
            "rmse_3d_mm": prod_summary["legacy_production_T1_mean_rmse_mm"],
            "interpretation": "legacy production mean-aggregated static point before switching solve_positions to T4",
        },
        {
            "case": "real_production_T4_mean",
            "source": "production_method_probe/production_static_method_real_run_eval/tables/tag_accuracy_summary.csv",
            "p50_3d_mm": prod_summary["production_T4_mean_p50_mm"],
            "p95_3d_mm": prod_summary["production_T4_mean_p95_mm"],
            "rmse_3d_mm": prod_summary["production_T4_mean_rmse_mm"],
            "interpretation": "real production export path with solve_positions switched to T4 and position_summary mean aggregation unchanged",
        },
        {
            "case": "median_estimator_ablation_T4",
            "source": "FULL/tables/tag_raw_replay_accuracy_summary.csv",
            "p50_3d_mm": prod_summary["raw_T4_median_p50_mm"],
            "p95_3d_mm": prod_summary["raw_T4_p95_mm"],
            "rmse_3d_mm": prod_summary["raw_T4_median_rmse_mm"],
            "interpretation": "median static-point estimator ablation; not the deployed production mean-aggregated static point",
        },
    ]


def position_dop(anchors_xyz: np.ndarray, point_xyz: np.ndarray) -> float:
    anchors = np.asarray(anchors_xyz, dtype=float)
    point = np.asarray(point_xyz, dtype=float)
    if anchors.ndim != 2 or anchors.shape[1] != 3 or point.shape != (3,):
        raise ValueError(f"bad DOP inputs anchors={anchors.shape} point={point.shape}")
    vec = anchors - point[None, :]
    dist = np.linalg.norm(vec, axis=1)
    good = np.isfinite(dist) & (dist > 1e-9) & np.isfinite(vec).all(axis=1)
    if int(np.sum(good)) < 4:
        return float("nan")
    h = vec[good] / dist[good, None]
    normal = h.T @ h
    try:
        q = np.linalg.inv(normal)
    except np.linalg.LinAlgError:
        return float("nan")
    tr = float(np.trace(q))
    return float(math.sqrt(tr)) if math.isfinite(tr) and tr >= 0.0 else float("nan")


def assert_position_dop() -> None:
    anchors = np.asarray(
        [
            [1.0, 1.0, 1.0],
            [1.0, -1.0, -1.0],
            [-1.0, 1.0, -1.0],
            [-1.0, -1.0, 1.0],
        ],
        dtype=float,
    )
    dop = position_dop(anchors, np.zeros(3, dtype=float))
    if not math.isclose(dop, 1.5, rel_tol=1e-10, abs_tol=1e-10):
        raise AssertionError(f"regular tetrahedron DOP sanity check failed: got {dop}")


def metric_columns(df: pd.DataFrame) -> tuple[str, str, str]:
    if "err_3d_mm" in df.columns:
        err3_col = "err_3d_mm"
    else:
        err3_col = "err3d_mm"
    if "err_horizontal_xz_mm" in df.columns:
        horiz_col = "err_horizontal_xz_mm"
    else:
        horiz_col = "err_horizontal_mm"
    if "err_vertical_y_mm" in df.columns:
        vert_col = "err_vertical_y_mm"
    else:
        vert_col = "err_vertical_mm"
    return err3_col, horiz_col, vert_col


def component_stats(df: pd.DataFrame, prefix: str) -> dict[str, float]:
    err3_col, horiz_col, vert_col = metric_columns(df)
    return {
        f"{prefix}_n": int(len(df)),
        f"{prefix}_p50_3d_mm": pct(df[err3_col].to_numpy(float), 50),
        f"{prefix}_p95_3d_mm": pct(df[err3_col].to_numpy(float), 95),
        f"{prefix}_p50_xz_mm": pct(df[horiz_col].to_numpy(float), 50),
        f"{prefix}_p95_xz_mm": pct(df[horiz_col].to_numpy(float), 95),
        f"{prefix}_p50_y_mm": pct(df[vert_col].to_numpy(float), 50),
        f"{prefix}_p95_y_mm": pct(df[vert_col].to_numpy(float), 95),
    }


def difference_metrics(row: dict, left_prefix: str, right_prefix: str, out_prefix: str) -> None:
    for q in ["p50", "p95"]:
        for comp in ["3d", "xz", "y"]:
            row[f"{out_prefix}_{q}_{comp}_mm"] = (
                float(row[f"{left_prefix}_{q}_{comp}_mm"]) - float(row[f"{right_prefix}_{q}_{comp}_mm"])
            )


def static_position_table_for_why7(scenario_name: str) -> pd.DataFrame:
    if scenario_name == "self_cal_v4io_T4":
        df = pd.read_csv(FULL_ROOT / "tables" / "tag_raw_replay_abs_errors_per_session.csv")
        out = df[(df["version"] == "v4-io") & (df["tag_method"] == "T4") & (df["eval_set"] == "all8")].copy()
        out = out.rename(columns={"err_horizontal_mm": "err_horizontal_xz_mm", "err_vertical_mm": "err_vertical_y_mm"})
        return out
    if scenario_name == "vicon_truth_delaycal_T4":
        df = pd.read_csv(ALIGN_ROOT / "tables" / "static_abs_errors_per_session.csv")
        return df[
            (df["layout_solver"] == "v4-io")
            & (df["tag_method"] == "T4")
            & (df["layout_variant"] == "vicon_truth")
            & (df["delay_mode"] == "vicon_inter_anchor_delaycal")
        ].copy()
    raise ValueError(f"unknown WHY #7 scenario {scenario_name!r}")


def make_static_single_shot_scenarios(ablation_mod) -> tuple[list[dict], list[Path]]:
    assert_position_dop()
    layout_base = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check"
    sigma_path = layout_base / "tables/anchor_sigma.json"
    pair_quality = layout_base / "tables/pair_quality_solve.csv"
    captures_root = OFFICIAL_ROOT / "captures/erlangen_20260528_optitrack"

    anchor_truth, tag_truth, tag_truth_meta, _corr = ablation_mod.load_corrected_static_truth(
        OPTI_FULL_ROOT,
        ablation_mod.ANCHORS,
        ablation_mod.PRIMARY_IDS,
    )
    truth_coords = np.vstack([anchor_truth[a] for a in ablation_mod.ANCHORS])
    sigma_by_id = ablation_mod.load_anchor_sigma(sigma_path)
    labels, coords, solver_delays, solver_tag_delay = ablation_mod.load_layout_json_raw(layout_base / "v4-io" / "layout.json")
    by_label = {label: coords[i] for i, label in enumerate(labels)}
    src = np.vstack([by_label[a] for a in ablation_mod.ANCHORS])
    rigid = ablation_mod.fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=False)
    self_cal_coords = ablation_mod.apply_fit(src, rigid)
    delaycal_delays, delaycal_tag_delay, _delay_rows = ablation_mod.estimate_delaycal(anchor_truth, pair_quality)

    static_files = sorted(captures_root.glob("static_ID*/tag_capture*/tr_all.csv"))
    scenarios = [
        {
            "scenario": "self_cal_v4io_T4",
            "layout": ablation_mod.build_layout(
                name="why7_self_cal_v4io_rigid_opti_frame",
                labels=ablation_mod.ANCHORS,
                coords_opti_frame=self_cal_coords,
                delays=solver_delays,
                tag_delay_mm=solver_tag_delay,
                sigma_by_id=sigma_by_id,
                metadata={"scenario": "self_cal_v4io_T4"},
            ),
            "anchor_coords": self_cal_coords,
            "tag_truth": tag_truth,
            "tag_truth_meta": tag_truth_meta,
        },
        {
            "scenario": "vicon_truth_delaycal_T4",
            "layout": ablation_mod.build_layout(
                name="why7_vicon_truth_delaycal",
                labels=ablation_mod.ANCHORS,
                coords_opti_frame=truth_coords,
                delays=delaycal_delays,
                tag_delay_mm=delaycal_tag_delay,
                sigma_by_id=sigma_by_id,
                metadata={"scenario": "vicon_truth_delaycal_T4"},
            ),
            "anchor_coords": truth_coords,
            "tag_truth": tag_truth,
            "tag_truth_meta": tag_truth_meta,
        },
    ]
    return scenarios, static_files


def solve_static_single_shot_for_scenario(ablation_mod, scenario: dict, static_files: list[Path]) -> tuple[pd.DataFrame, list[dict]]:
    solver = ablation_mod.TagPositionSolver(scenario["layout"], ablation_mod.SolverConfig(method="T4"))
    anchor_coords = np.asarray(scenario["anchor_coords"], dtype=float)
    tag_truth = scenario["tag_truth"]
    tag_truth_meta = scenario["tag_truth_meta"]
    sample_rows: list[dict] = []
    bias_rows: list[dict] = []
    for path in static_files:
        sid = ablation_mod.session_id_from_path(path)
        truth = tag_truth.get(sid)
        if truth is None:
            continue
        frames = ablation_mod.read_tr_all_frames(path, tags={"BSF66F"}, min_anchors=4)
        frames = ablation_mod.filter_frames(frames, set(range(8)))
        points = []
        residuals = []
        anchors_used = []
        dop = position_dop(anchor_coords, truth)
        for frame_idx, frame in enumerate(frames):
            result = solver.solve_frame(frame)
            if result is None or result.status != "ok":
                continue
            solved = np.asarray([result.x_mm, result.y_mm, result.z_mm], dtype=float)
            if not np.isfinite(solved).all():
                continue
            diff = solved - truth
            points.append(solved)
            residuals.append(float(getattr(result, "residual_rms_mm", float("nan"))))
            anchors_used.append(float(getattr(result, "anchors_used", float("nan"))))
            sample_rows.append(
                {
                    "scenario": scenario["scenario"],
                    "ID": sid,
                    "capture": ablation_mod.capture_name_from_path(path),
                    "frame_idx": int(frame_idx),
                    "host_elapsed_s": float(getattr(frame, "host_elapsed_s", float("nan"))),
                    "solved_x_mm": float(solved[0]),
                    "solved_y_vertical_mm": float(solved[1]),
                    "solved_z_mm": float(solved[2]),
                    "truth_x_mm": float(truth[0]),
                    "truth_y_vertical_mm": float(truth[1]),
                    "truth_z_mm": float(truth[2]),
                    "err_x_mm": float(diff[0]),
                    "err_y_vertical_mm": float(diff[1]),
                    "err_z_mm": float(diff[2]),
                    "err_3d_mm": float(np.linalg.norm(diff)),
                    "err_horizontal_xz_mm": float(math.sqrt(diff[0] * diff[0] + diff[2] * diff[2])),
                    "err_vertical_y_mm": float(abs(diff[1])),
                    "gdop": float(dop),
                    "residual_rms_mm": residuals[-1],
                    "anchors_used": anchors_used[-1],
                }
            )
        if not points:
            continue
        pts = np.vstack(points)
        mean_est = np.mean(pts, axis=0)
        bias_vec = mean_est - truth
        centered = pts - mean_est[None, :]
        truth_info = tag_truth_meta.get(sid, {})
        bias_rows.append(
            {
                "scenario": scenario["scenario"],
                "ID": sid,
                "capture": ablation_mod.capture_name_from_path(path),
                "n_samples": int(pts.shape[0]),
                "bias_3d_mm": float(np.linalg.norm(bias_vec)),
                "bias_xz_mm": float(math.sqrt(bias_vec[0] * bias_vec[0] + bias_vec[2] * bias_vec[2])),
                "bias_y_mm": float(abs(bias_vec[1])),
                "scatter_3d_rms_mm": float(math.sqrt(np.mean(np.sum(centered * centered, axis=1)))),
                "scatter_xz_rms_mm": float(math.sqrt(np.mean(centered[:, 0] * centered[:, 0] + centered[:, 2] * centered[:, 2]))),
                "scatter_y_rms_mm": float(math.sqrt(np.mean(centered[:, 1] * centered[:, 1]))),
                "gdop": float(dop),
                "residual_rms_median_mm": pct(residuals, 50),
                "anchors_used_median": pct(anchors_used, 50),
                "tag_truth_source": truth_info.get("tag_truth_source", ""),
                "tag_truth_corrected": truth_info.get("tag_truth_corrected", False),
            }
        )
    return pd.DataFrame(sample_rows), bias_rows


def roto_samples_for_why7(scenario_name: str, anchor_coords: np.ndarray) -> pd.DataFrame:
    if scenario_name == "self_cal_v4io_T4":
        path = FULL_ROOT / "roto_absolute" / "tables" / "roto_abs_samples_v4io_T4.csv"
    elif scenario_name == "vicon_truth_delaycal_T4":
        path = ALIGN_ROOT / "roto_absolute" / "tables" / "roto_abs_samples_v4io_T4.csv"
    else:
        raise ValueError(f"unknown WHY #7 scenario {scenario_name!r}")
    df = pd.read_csv(path).copy()
    points = df[["opti_x_mm", "opti_y_vertical_mm", "opti_z_mm"]].to_numpy(float)
    df["gdop"] = [position_dop(anchor_coords, p) for p in points]
    df = df.rename(columns={"err3d_mm": "err_3d_mm"})
    return df


def build_gdop_rows(scenario_name: str, static_samples: pd.DataFrame, roto_samples: pd.DataFrame) -> tuple[list[dict], dict]:
    static_good = static_samples[np.isfinite(static_samples["gdop"].to_numpy(float))].copy()
    roto_good = roto_samples[np.isfinite(roto_samples["gdop"].to_numpy(float))].copy()
    all_gdop = np.concatenate([static_good["gdop"].to_numpy(float), roto_good["gdop"].to_numpy(float)])
    all_gdop = all_gdop[np.isfinite(all_gdop)]
    if all_gdop.size < 10:
        return [], {"gdop_shared_bin_count": 0, "gdop_median_abs_gap_p50_3d_mm": float("nan"), "gdop_bins_gap_gt15_count": 0}
    edges = np.quantile(all_gdop, np.linspace(0.0, 1.0, 11))
    edges = np.unique(edges)
    if edges.size < 3:
        edges = np.linspace(float(np.min(all_gdop)), float(np.max(all_gdop)), 11)
    rows: list[dict] = []
    shared_gaps = []
    gap_gt15 = 0
    for bin_idx in range(edges.size - 1):
        lo = float(edges[bin_idx])
        hi = float(edges[bin_idx + 1])
        if bin_idx == edges.size - 2:
            smask = (static_good["gdop"] >= lo) & (static_good["gdop"] <= hi)
            rmask = (roto_good["gdop"] >= lo) & (roto_good["gdop"] <= hi)
        else:
            smask = (static_good["gdop"] >= lo) & (static_good["gdop"] < hi)
            rmask = (roto_good["gdop"] >= lo) & (roto_good["gdop"] < hi)
        s = static_good[smask]
        r = roto_good[rmask]
        gap = abs(pct(r["err_3d_mm"], 50) - pct(s["err_3d_mm"], 50)) if len(s) and len(r) else float("nan")
        if math.isfinite(gap):
            shared_gaps.append(gap)
            if gap > 15.0:
                gap_gt15 += 1
        rows.append(
            {
                "scenario": scenario_name,
                "gdop_bin": int(bin_idx),
                "gdop_lo": lo,
                "gdop_hi": hi,
                "static_n": int(len(s)),
                "roto_n": int(len(r)),
                "static_p50_3d_mm": pct(s["err_3d_mm"], 50),
                "roto_p50_3d_mm": pct(r["err_3d_mm"], 50),
                "abs_gap_p50_3d_mm": gap,
                "static_p50_xz_mm": pct(s["err_horizontal_xz_mm"], 50),
                "roto_p50_xz_mm": pct(r["err_horizontal_xz_mm"], 50),
                "static_p50_y_mm": pct(s["err_vertical_y_mm"], 50),
                "roto_p50_y_mm": pct(r["err_vertical_y_mm"], 50),
            }
        )
    summary = {
        "gdop_shared_bin_count": int(len(shared_gaps)),
        "gdop_median_abs_gap_p50_3d_mm": pct(shared_gaps, 50),
        "gdop_bins_gap_gt15_count": int(gap_gt15),
    }
    return rows, summary


def audit_single_shot_decomposition() -> tuple[list[dict], list[dict], list[dict]]:
    ablation_mod = import_static_ablation_module()
    scenarios, static_files = make_static_single_shot_scenarios(ablation_mod)
    summary_rows: list[dict] = []
    gdop_rows: list[dict] = []
    bias_rows_all: list[dict] = []
    for scenario in scenarios:
        scenario_name = scenario["scenario"]
        static_samples, bias_rows = solve_static_single_shot_for_scenario(ablation_mod, scenario, static_files)
        bias_rows_all.extend(bias_rows)
        if static_samples.empty:
            summary_rows.append(
                {
                    "scenario": scenario_name,
                    "verdict": "STATIC_SINGLE_SHOT_UNAVAILABLE",
                    "paper_consequence": "Static per-sample replay failed; withhold the single-shot decomposition.",
                }
            )
            continue
        static_positions = static_position_table_for_why7(scenario_name)
        roto_samples = roto_samples_for_why7(scenario_name, np.asarray(scenario["anchor_coords"], dtype=float))
        rows, gdop_summary = build_gdop_rows(scenario_name, static_samples, roto_samples)
        gdop_rows.extend(rows)
        bias_df = pd.DataFrame(bias_rows)
        row: dict[str, float | int | str] = {"scenario": scenario_name}
        row.update(component_stats(static_samples, "static_single_shot"))
        row.update(component_stats(static_positions, "static_per_position"))
        row.update(component_stats(roto_samples, "roto_single_shot"))
        difference_metrics(row, "static_single_shot", "static_per_position", "averaging_benefit")
        difference_metrics(row, "roto_single_shot", "static_single_shot", "dynamic_excess")
        row.update(gdop_summary)
        row.update(
            {
                "static_bias_3d_median_mm": pct(bias_df["bias_3d_mm"], 50),
                "static_scatter_3d_rms_median_mm": pct(bias_df["scatter_3d_rms_mm"], 50),
                "static_bias_xz_median_mm": pct(bias_df["bias_xz_mm"], 50),
                "static_scatter_xz_rms_median_mm": pct(bias_df["scatter_xz_rms_mm"], 50),
                "static_bias_y_median_mm": pct(bias_df["bias_y_mm"], 50),
                "static_scatter_y_rms_median_mm": pct(bias_df["scatter_y_rms_mm"], 50),
            }
        )
        if (
            float(row["dynamic_excess_p50_3d_mm"]) < 15.0
            and float(row["gdop_median_abs_gap_p50_3d_mm"]) < 15.0
            and int(row["gdop_bins_gap_gt15_count"]) <= 1
        ):
            row["verdict"] = "DYNAMIC_EXCESS_NEGLIGIBLE"
            row["paper_consequence"] = (
                "ROTO absolute error is consistent with un-averaged single-shot 3D precision; "
                "static looks better because dwell-time averaging suppresses that scatter."
            )
        else:
            row["verdict"] = "DYNAMIC_EXCESS_PRESENT"
            if float(row["dynamic_excess_p50_3d_mm"]) < 15.0:
                row["paper_consequence"] = (
                    "Pooled 3D P50 is nearly explained by lost static averaging, but XZ and GDOP-conditioned bins retain a small dynamic excess; "
                    "report this as a limited real dynamic term, not a timing artifact."
                )
            else:
                row["paper_consequence"] = (
                    "ROTO retains excess error beyond static single-shot precision; inspect dynamic/orientation/ranging physics."
                )
        summary_rows.append(row)
    return summary_rows, gdop_rows, bias_rows_all


def aggregate_bias_scatter(static_bias_rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(static_bias_rows)
    if df.empty:
        return []
    rows: list[dict] = []
    for scenario, g in df.groupby("scenario"):
        rows.append(
            {
                "scenario": scenario,
                "n_positions": int(len(g)),
                "static_bias_3d_median_mm": pct(g["bias_3d_mm"], 50),
                "static_bias_3d_p95_mm": pct(g["bias_3d_mm"], 95),
                "static_bias_xz_median_mm": pct(g["bias_xz_mm"], 50),
                "static_bias_y_median_mm": pct(g["bias_y_mm"], 50),
                "static_scatter_3d_rms_median_mm": pct(g["scatter_3d_rms_mm"], 50),
                "static_scatter_3d_rms_p95_mm": pct(g["scatter_3d_rms_mm"], 95),
                "static_scatter_xz_rms_median_mm": pct(g["scatter_xz_rms_mm"], 50),
                "static_scatter_y_rms_median_mm": pct(g["scatter_y_rms_mm"], 50),
            }
        )
    return rows


def discover_roto_tr_all(roto_mod) -> dict[str, Path]:
    captures_root = OFFICIAL_ROOT / "captures" / "erlangen_20260528_optitrack"
    out = roto_mod.discover_roto_capture_files(captures_root)
    if not out:
        raise FileNotFoundError(f"no ROTO tr_all files under {captures_root}")
    return out


def interpolate_one(roto_mod, traj, query_time_s: float) -> tuple[np.ndarray, bool]:
    xyz, good = roto_mod.interpolate_opti(traj, np.asarray([query_time_s], dtype=float))
    return xyz[0], bool(good[0])


def collect_tag_anchor_range_residuals(
    ablation_mod,
    roto_mod,
    scenario: dict,
    roto_tr_all: dict[str, Path],
    offsets: pd.DataFrame,
    opti_cache: dict[str, dict[str, object]],
    mapping: dict[str, str],
) -> dict[tuple[str, int], list[tuple[float, float]]]:
    """Collect per-tag/per-anchor range residuals.

    Each stored tuple is (raw, model_corrected), where raw is measured -
    geometric truth and model_corrected additionally subtracts layout-level
    residual delay corrections. WHY #9 must consume only raw.
    """
    layout = scenario["layout"]
    anchor_coords = np.asarray(scenario["anchor_coords"], dtype=float)
    anchor_by_id = {aid: layout.anchors[aid] for aid in layout.anchors}
    beta_by_capture = {
        str(row["capture_id"]): float(row["beta_s"])
        for _, row in offsets[offsets["status"] == "ok"].iterrows()
        if math.isfinite(float(row["beta_s"]))
    }
    residuals: dict[tuple[str, int], list[tuple[float, float]]] = {}

    # Dynamic wand tags: use validated per-capture beta and interpolated OptiTrack truth.
    for cid, tr_path in sorted(roto_tr_all.items()):
        if cid not in beta_by_capture or cid not in opti_cache:
            continue
        beta = beta_by_capture[cid]
        for tag in UWB_TAGS:
            if tag not in mapping:
                continue
            marker = mapping[tag]
            if marker not in opti_cache[cid]:
                continue
            frames = ablation_mod.read_tr_all_frames(tr_path, tags={tag}, min_anchors=4)
            traj = opti_cache[cid][marker]
            for frame in frames:
                truth, good = interpolate_one(roto_mod, traj, float(frame.host_elapsed_s) + beta)
                if not good or not np.isfinite(truth).all():
                    continue
                for obs in frame.observations:
                    if obs.anchor_id not in anchor_by_id or obs.anchor_id >= anchor_coords.shape[0]:
                        continue
                    anchor = anchor_by_id[obs.anchor_id]
                    geom = float(np.linalg.norm(truth - anchor_coords[obs.anchor_id]))
                    raw = float(obs.range_mm - geom)
                    corrected = float(raw - anchor.d_anchor_mm - layout.tag_delay_mm)
                    residuals.setdefault((tag, obs.anchor_id), []).append((raw, corrected))

    # Static reference tag: no beta needed, truth is fixed per capture.
    captures_root = OFFICIAL_ROOT / "captures" / "erlangen_20260528_optitrack"
    static_files = sorted(captures_root.glob("static_ID*/tag_capture*/tr_all.csv"))
    tag_truth = scenario["tag_truth"]
    for tr_path in static_files:
        sid = ablation_mod.session_id_from_path(tr_path)
        truth = tag_truth.get(sid)
        if truth is None:
            continue
        frames = ablation_mod.read_tr_all_frames(tr_path, tags={"BSF66F"}, min_anchors=4)
        for frame in frames:
            for obs in frame.observations:
                if obs.anchor_id not in anchor_by_id or obs.anchor_id >= anchor_coords.shape[0]:
                    continue
                anchor = anchor_by_id[obs.anchor_id]
                geom = float(np.linalg.norm(truth - anchor_coords[obs.anchor_id]))
                raw = float(obs.range_mm - geom)
                corrected = float(raw - anchor.d_anchor_mm - layout.tag_delay_mm)
                residuals.setdefault(("BSF66F", obs.anchor_id), []).append((raw, corrected))

    return residuals


def tag_range_residual_rows_for_scenario(
    ablation_mod,
    roto_mod,
    scenario: dict,
    roto_tr_all: dict[str, Path],
    offsets: pd.DataFrame,
    opti_cache: dict[str, dict[str, object]],
    mapping: dict[str, str],
) -> tuple[list[dict], dict[str, float]]:
    scenario_name = str(scenario["scenario"])
    residuals = collect_tag_anchor_range_residuals(
        ablation_mod,
        roto_mod,
        scenario,
        roto_tr_all,
        offsets,
        opti_cache,
        mapping,
    )
    rows: list[dict] = []
    bias_by_tag: dict[str, float] = {}
    for tag in sorted({tag for tag, _aid in residuals}):
        anchor_rows = []
        raw_all = []
        corrected_all = []
        for aid in range(8):
            vals = residuals.get((tag, aid), [])
            raw_vals = [v[0] for v in vals]
            corrected_vals = [v[1] for v in vals]
            if raw_vals:
                raw_all.extend(raw_vals)
                corrected_all.extend(corrected_vals)
                anchor_rows.append(
                    {
                        "scenario": scenario_name,
                        "tag": tag,
                        "anchor": ANCHOR_LABELS[aid],
                        "anchor_id": aid,
                        "n": int(len(vals)),
                        "raw_measured_minus_geom_median_mm": pct(raw_vals, 50),
                        "model_corrected_median_mm": pct(corrected_vals, 50),
                    }
                )
        corrected_anchor_medians = [r["model_corrected_median_mm"] for r in anchor_rows]
        raw_anchor_medians = [r["raw_measured_minus_geom_median_mm"] for r in anchor_rows]
        overall_raw = pct(raw_all, 50)
        overall_corrected = pct(corrected_all, 50)
        corrected_iqr = pct(corrected_anchor_medians, 75) - pct(corrected_anchor_medians, 25)
        raw_iqr = pct(raw_anchor_medians, 75) - pct(raw_anchor_medians, 25)
        uniform_gate = bool(
            tag in UWB_TAGS
            and math.isfinite(overall_corrected)
            and math.isfinite(corrected_iqr)
            and abs(overall_corrected) >= 10.0
            and corrected_iqr < 0.5 * abs(overall_corrected)
        )
        bias_by_tag[tag] = overall_corrected if tag in UWB_TAGS else 0.0
        for row in anchor_rows:
            row.update(
                {
                    "tag_overall_raw_median_mm": overall_raw,
                    "tag_overall_model_corrected_median_mm": overall_corrected,
                    "tag_raw_anchor_median_iqr_mm": raw_iqr,
                    "tag_model_corrected_anchor_median_iqr_mm": corrected_iqr,
                    "tag_delay_uniform_gate": uniform_gate,
                    "residual_definition_note": "raw=measured-geometric; model_corrected=measured-geometric-d_anchor-tag_delay",
                }
            )
            rows.append(row)
        rows.append(
            {
                "scenario": scenario_name,
                "tag": tag,
                "anchor": "ALL",
                "anchor_id": -1,
                "n": int(len(raw_all)),
                "raw_measured_minus_geom_median_mm": overall_raw,
                "model_corrected_median_mm": overall_corrected,
                "tag_overall_raw_median_mm": overall_raw,
                "tag_overall_model_corrected_median_mm": overall_corrected,
                "tag_raw_anchor_median_iqr_mm": raw_iqr,
                "tag_model_corrected_anchor_median_iqr_mm": corrected_iqr,
                "tag_delay_uniform_gate": uniform_gate,
                "residual_definition_note": "raw=measured-geometric; model_corrected=measured-geometric-d_anchor-tag_delay",
            }
        )
    return rows, bias_by_tag


def adjusted_frame(frame, bias_mm: float):
    obs_cls = frame.observations[0].__class__ if frame.observations else None
    if obs_cls is None:
        return frame
    obs = tuple(
        obs_cls(
            anchor_id=o.anchor_id,
            range_mm=max(1.0, float(o.range_mm) - float(bias_mm)),
            quality_percent=o.quality_percent,
            status=o.status,
        )
        for o in frame.observations
    )
    return frame.__class__(
        tag=frame.tag,
        sweep=frame.sweep,
        host_elapsed_s=frame.host_elapsed_s,
        host_epoch_s=frame.host_epoch_s,
        observations=obs,
        imu=frame.imu,
    )


def solve_roto_with_tag_bias(
    ablation_mod,
    roto_mod,
    scenario: dict,
    roto_tr_all: dict[str, Path],
    offsets: pd.DataFrame,
    opti_cache: dict[str, dict[str, object]],
    mapping: dict[str, str],
    bias_by_tag: dict[str, float],
) -> pd.DataFrame:
    rows: list[dict] = []
    beta_by_capture = {
        str(row["capture_id"]): float(row["beta_s"])
        for _, row in offsets[offsets["status"] == "ok"].iterrows()
        if math.isfinite(float(row["beta_s"]))
    }
    anchor_coords = np.asarray(scenario["anchor_coords"], dtype=float)
    for cid, tr_path in sorted(roto_tr_all.items()):
        if cid not in beta_by_capture or cid not in opti_cache:
            continue
        beta = beta_by_capture[cid]
        for tag in UWB_TAGS:
            if tag not in mapping or mapping[tag] not in opti_cache[cid]:
                continue
            solver = ablation_mod.TagPositionSolver(scenario["layout"], ablation_mod.SolverConfig(method="T4"))
            frames = ablation_mod.read_tr_all_frames(tr_path, tags={tag}, min_anchors=4)
            frames = [adjusted_frame(frame, bias_by_tag.get(tag, 0.0)) for frame in frames]
            traj = opti_cache[cid][mapping[tag]]
            for frame in sorted(frames, key=lambda f: (f.host_elapsed_s, f.sweep)):
                result = solver.solve_frame(frame)
                if result is None or result.status != "ok":
                    continue
                truth, good = interpolate_one(roto_mod, traj, float(result.host_elapsed_s) + beta)
                if not good or not np.isfinite(truth).all():
                    continue
                solved = np.asarray([result.x_mm, result.y_mm, result.z_mm], dtype=float)
                diff = solved - truth
                rows.append(
                    {
                        "scenario": scenario["scenario"],
                        "capture_id": cid,
                        "tag": tag,
                        "uwb_time_s": float(result.host_elapsed_s),
                        "err_3d_mm": float(np.linalg.norm(diff)),
                        "err_horizontal_xz_mm": float(math.sqrt(diff[0] * diff[0] + diff[2] * diff[2])),
                        "err_vertical_y_mm": float(abs(diff[1])),
                        "err_x_mm": float(diff[0]),
                        "err_y_vertical_mm": float(diff[1]),
                        "err_z_mm": float(diff[2]),
                        "gdop": position_dop(anchor_coords, truth),
                        "tag_bias_removed_mm": float(bias_by_tag.get(tag, 0.0)),
                        "anchors_input": int(result.anchors_input),
                        "anchors_used": int(result.anchors_used),
                        "residual_rms_mm": float(result.residual_rms_mm),
                    }
                )
    return pd.DataFrame(rows)


def gdop_overlap_rows_for_scenario(scenario_name: str, static_samples: pd.DataFrame, roto_samples: pd.DataFrame, gdop_bin_rows: list[dict]) -> list[dict]:
    sgdop = static_samples["gdop"].to_numpy(float)
    rgdop = roto_samples["gdop"].to_numpy(float)
    sgdop = sgdop[np.isfinite(sgdop)]
    rgdop = rgdop[np.isfinite(rgdop)]
    if sgdop.size == 0 or rgdop.size == 0:
        raise ValueError(f"empty GDOP distribution for {scenario_name}")
    overlap_lo = max(float(np.min(sgdop)), float(np.min(rgdop)))
    overlap_hi = min(float(np.max(sgdop)), float(np.max(rgdop)))
    overlap_p5p95_lo = max(pct(sgdop, 5), pct(rgdop, 5))
    overlap_p5p95_hi = min(pct(sgdop, 95), pct(rgdop, 95))
    rows = [
        {
            "scenario": scenario_name,
            "row_type": "summary",
            "static_gdop_p5": pct(sgdop, 5),
            "static_gdop_p50": pct(sgdop, 50),
            "static_gdop_p95": pct(sgdop, 95),
            "roto_gdop_p5": pct(rgdop, 5),
            "roto_gdop_p50": pct(rgdop, 50),
            "roto_gdop_p95": pct(rgdop, 95),
            "overlap_gdop_minmax_lo": overlap_lo,
            "overlap_gdop_minmax_hi": overlap_hi,
            "overlap_gdop_p5p95_lo": overlap_p5p95_lo,
            "overlap_gdop_p5p95_hi": overlap_p5p95_hi,
            "static_min_anchors": 4,
            "roto_min_anchors": 4,
            "static_anchor_set": "all8",
            "roto_anchor_set": "all8",
            "static_external_rms_gate": "none",
            "roto_external_rms_gate": "none",
            "gating_parity": "MATCH",
        }
    ]
    for row in gdop_bin_rows:
        if row.get("scenario") != scenario_name:
            continue
        if int(row.get("static_n", 0)) <= 0 or int(row.get("roto_n", 0)) <= 0:
            continue
        rows.append(
            {
                "scenario": scenario_name,
                "row_type": "shared_bin",
                "gdop_bin": row.get("gdop_bin"),
                "gdop_lo": row.get("gdop_lo"),
                "gdop_hi": row.get("gdop_hi"),
                "static_n": row.get("static_n"),
                "roto_n": row.get("roto_n"),
                "thin_bin_flag": bool(int(row.get("static_n", 0)) < 30 or int(row.get("roto_n", 0)) < 30),
                "abs_gap_p50_3d_mm": row.get("abs_gap_p50_3d_mm"),
                "static_p50_3d_mm": row.get("static_p50_3d_mm"),
                "roto_p50_3d_mm": row.get("roto_p50_3d_mm"),
            }
        )
    return rows


def audit_tag_delay_and_overlap(single_shot_summary: list[dict], gdop_rows: list[dict], static_bias_rows: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    ablation_mod = import_static_ablation_module()
    roto_mod = import_roto_module()
    mapping = selected_wand_mapping()
    scenarios, static_files = make_static_single_shot_scenarios(ablation_mod)
    roto_tr_all = discover_roto_tr_all(roto_mod)
    offsets = pd.read_csv(FULL_ROOT / "roto_absolute" / "tables" / "roto_time_offsets_v4io_T4.csv")
    capture_ids = sorted(offsets.loc[offsets["status"] == "ok", "capture_id"].astype(str).tolist())
    opti_cache = load_opti_cache(roto_mod, capture_ids)

    bias_summary_rows = aggregate_bias_scatter(static_bias_rows)
    range_rows: list[dict] = []
    resolve_rows: list[dict] = []
    overlap_rows: list[dict] = []
    summary_by_scenario = {str(r["scenario"]): r for r in single_shot_summary}

    for scenario in scenarios:
        scenario_name = str(scenario["scenario"])
        static_samples, _bias_rows = solve_static_single_shot_for_scenario(ablation_mod, scenario, static_files)
        if static_samples.empty:
            raise ValueError(f"empty static samples for WHY #8 {scenario_name}")
        roto_before = roto_samples_for_why7(scenario_name, np.asarray(scenario["anchor_coords"], dtype=float))
        overlap_rows.extend(gdop_overlap_rows_for_scenario(scenario_name, static_samples, roto_before, gdop_rows))

        rows, bias_by_tag = tag_range_residual_rows_for_scenario(
            ablation_mod,
            roto_mod,
            scenario,
            roto_tr_all,
            offsets,
            opti_cache,
            mapping,
        )
        if not rows:
            raise RuntimeError(f"no range residual rows for WHY #8 {scenario_name}")
        range_rows.extend(rows)
        gate_rows = [r for r in rows if r.get("anchor") == "ALL" and r.get("tag") in UWB_TAGS]
        gate_pass = any(bool(r.get("tag_delay_uniform_gate")) for r in gate_rows)
        gate_by_tag = {str(r["tag"]): r for r in gate_rows}
        before = summary_by_scenario[scenario_name]
        resolve_row: dict[str, float | int | str] = {
            "scenario": scenario_name,
            "range_residual_gate_pass": bool(gate_pass),
            "BS2DCE_bias_removed_mm": float(bias_by_tag.get("BS2DCE", 0.0)),
            "BSDC91_bias_removed_mm": float(bias_by_tag.get("BSDC91", 0.0)),
            "BS2DCE_overall_model_corrected_median_mm": float(
                gate_by_tag.get("BS2DCE", {}).get("tag_overall_model_corrected_median_mm", float("nan"))
            ),
            "BS2DCE_anchor_iqr_mm": float(
                gate_by_tag.get("BS2DCE", {}).get("tag_model_corrected_anchor_median_iqr_mm", float("nan"))
            ),
            "BS2DCE_uniform_gate": bool(gate_by_tag.get("BS2DCE", {}).get("tag_delay_uniform_gate", False)),
            "BSDC91_overall_model_corrected_median_mm": float(
                gate_by_tag.get("BSDC91", {}).get("tag_overall_model_corrected_median_mm", float("nan"))
            ),
            "BSDC91_anchor_iqr_mm": float(
                gate_by_tag.get("BSDC91", {}).get("tag_model_corrected_anchor_median_iqr_mm", float("nan"))
            ),
            "BSDC91_uniform_gate": bool(gate_by_tag.get("BSDC91", {}).get("tag_delay_uniform_gate", False)),
            "before_dynamic_excess_p50_3d_mm": float(before["dynamic_excess_p50_3d_mm"]),
            "before_dynamic_excess_p50_xz_mm": float(before["dynamic_excess_p50_xz_mm"]),
            "before_dynamic_excess_p50_y_mm": float(before["dynamic_excess_p50_y_mm"]),
            "before_gdop_median_gap_p50_3d_mm": float(before["gdop_median_abs_gap_p50_3d_mm"]),
        }
        if not gate_pass:
            resolve_row.update(
                {
                    "after_dynamic_excess_p50_3d_mm": float("nan"),
                    "after_dynamic_excess_p50_xz_mm": float("nan"),
                    "after_dynamic_excess_p50_y_mm": float("nan"),
                    "after_gdop_median_gap_p50_3d_mm": float("nan"),
                    "dynamic_excess_reduction_p50_3d_mm": 0.0,
                    "gdop_gap_reduction_mm": 0.0,
                    "tag_delay_verdict": "TAG_DELAY_EXCLUDED",
                    "paper_consequence": "Per-tag range residuals are not a uniform constant offset; keep WHY #7's small dynamic/region residual wording.",
                }
            )
            resolve_rows.append(resolve_row)
            continue

        roto_after = solve_roto_with_tag_bias(
            ablation_mod,
            roto_mod,
            scenario,
            roto_tr_all,
            offsets,
            opti_cache,
            mapping,
            bias_by_tag,
        )
        if roto_after.empty:
            raise RuntimeError(f"bias-removal re-solve produced no ROTO samples for {scenario_name}")
        after_dyn_3d = pct(roto_after["err_3d_mm"], 50) - pct(static_samples["err_3d_mm"], 50)
        after_dyn_xz = pct(roto_after["err_horizontal_xz_mm"], 50) - pct(static_samples["err_horizontal_xz_mm"], 50)
        after_dyn_y = pct(roto_after["err_vertical_y_mm"], 50) - pct(static_samples["err_vertical_y_mm"], 50)
        _rows_after, gdop_after = build_gdop_rows(scenario_name, static_samples, roto_after)
        after_gap = float(gdop_after["gdop_median_abs_gap_p50_3d_mm"])
        dyn_reduction = float(before["dynamic_excess_p50_3d_mm"]) - float(after_dyn_3d)
        gap_reduction = float(before["gdop_median_abs_gap_p50_3d_mm"]) - after_gap
        if after_dyn_3d < 5.0 and after_gap < 15.0:
            verdict = "TAG_DELAY_EXPLAINS"
            consequence = (
                "The WHY #7 residual is explained by a per-tag residual delay mismatch; recommend deployable per-device residual calibration."
            )
        elif dyn_reduction >= 5.0 and gap_reduction >= 5.0:
            verdict = "TAG_DELAY_PARTIAL"
            consequence = "Per-tag delay removes part of the residual, but a region/dynamic component remains."
        elif abs(dyn_reduction) < 5.0 and abs(gap_reduction) < 5.0:
            verdict = "TAG_DELAY_EXCLUDED"
            consequence = "Bias removal has negligible effect; keep WHY #7's small dynamic/region residual wording."
        else:
            verdict = "TAG_DELAY_PARTIAL"
            consequence = "Per-tag delay changes one residual criterion but does not fully explain the WHY #7 excess."
        resolve_row.update(
            {
                "after_dynamic_excess_p50_3d_mm": float(after_dyn_3d),
                "after_dynamic_excess_p50_xz_mm": float(after_dyn_xz),
                "after_dynamic_excess_p50_y_mm": float(after_dyn_y),
                "after_gdop_median_gap_p50_3d_mm": after_gap,
                "dynamic_excess_reduction_p50_3d_mm": dyn_reduction,
                "gdop_gap_reduction_mm": gap_reduction,
                "tag_delay_verdict": verdict,
                "paper_consequence": consequence,
                "diagnostic_caveat": "bias estimated against Vicon truth; deployable confirmation requires per-tag known-baseline delay calibration",
            }
        )
        resolve_rows.append(resolve_row)
    return bias_summary_rows, range_rows, resolve_rows, overlap_rows


def median_polish_nanaware(table: np.ndarray, *, tol_mm: float = 0.1, max_iters: int = 50) -> dict:
    arr = np.asarray(table, dtype=float)
    if arr.ndim != 2:
        raise ValueError(f"median polish expects a 2D table, got {arr.shape}")
    if not np.isfinite(arr).any():
        raise ValueError("median polish input has no finite cells")
    for idx, row in enumerate(arr):
        if not np.isfinite(row).any():
            raise ValueError(f"median polish input has no finite cells for tag row {idx}")
    for idx in range(arr.shape[1]):
        if not np.isfinite(arr[:, idx]).any():
            raise ValueError(f"median polish input has no finite cells for anchor column {idx}")

    grand = pct(arr.reshape(-1), 50)
    residual = arr - grand
    tag_effect = np.zeros(arr.shape[0], dtype=float)
    anchor_effect = np.zeros(arr.shape[1], dtype=float)
    converged = False
    max_update = float("inf")
    iterations = 0
    for iterations in range(1, max_iters + 1):
        row_updates = np.asarray([pct(row, 50) for row in residual], dtype=float)
        row_updates[~np.isfinite(row_updates)] = 0.0
        residual = residual - row_updates[:, None]
        tag_effect += row_updates

        col_updates = np.asarray([pct(residual[:, i], 50) for i in range(residual.shape[1])], dtype=float)
        col_updates[~np.isfinite(col_updates)] = 0.0
        residual = residual - col_updates[None, :]
        anchor_effect += col_updates

        max_update = float(max(np.max(np.abs(row_updates)), np.max(np.abs(col_updates))))
        if max_update < tol_mm:
            converged = True
            break

    # Fix the additive gauge so row/column effects have median zero.
    tag_center = pct(tag_effect, 50)
    if math.isfinite(tag_center):
        tag_effect -= tag_center
        grand += tag_center
    anchor_center = pct(anchor_effect, 50)
    if math.isfinite(anchor_center):
        anchor_effect -= anchor_center
        grand += anchor_center

    finite_interactions = residual[np.isfinite(residual)]
    return {
        "grand": float(grand),
        "tag_effect": tag_effect,
        "anchor_effect": anchor_effect,
        "interaction": residual,
        "interaction_median_abs_mm": pct(np.abs(finite_interactions), 50),
        "interaction_max_abs_mm": float(np.max(np.abs(finite_interactions))) if finite_interactions.size else float("nan"),
        "iterations": int(iterations),
        "converged": bool(converged),
        "max_update_mm": float(max_update),
    }


def why9_pair_key(left: str, right: str) -> str:
    return f"tagdiff_{left}_minus_{right}_mm"


def build_why9_cells_and_effects(
    scenario_name: str,
    residuals: dict[tuple[str, int], list[tuple[float, float]]],
    *,
    min_cell_n: int = WHY9_MIN_CELL_N,
) -> tuple[list[dict], dict, dict]:
    table = np.full((len(PHYSICAL_TAGS), len(ANCHOR_LABELS)), np.nan, dtype=float)
    cell_rows: list[dict] = []
    thin_cell_count = 0
    for ti, tag in enumerate(PHYSICAL_TAGS):
        for aid, anchor_label in enumerate(ANCHOR_LABELS):
            vals = residuals.get((tag, aid), [])
            raw = np.asarray([v[0] for v in vals], dtype=float)
            raw = raw[np.isfinite(raw)]
            n = int(raw.size)
            thin = n < min_cell_n
            if thin:
                thin_cell_count += 1
                raw_median = float("nan")
                raw_iqr = float("nan")
                scatter_rms = float("nan")
            else:
                raw_median = pct(raw, 50)
                raw_iqr = pct(raw, 75) - pct(raw, 25)
                scatter_rms = float(math.sqrt(float(np.mean((raw - raw_median) * (raw - raw_median)))))
                table[ti, aid] = raw_median
            cell_rows.append(
                {
                    "scenario": scenario_name,
                    "tag": tag,
                    "anchor": anchor_label,
                    "anchor_id": aid,
                    "n": n,
                    "min_cell_n": int(min_cell_n),
                    "thin_cell_flag": bool(thin),
                    "raw_median": raw_median,
                    "raw_median_mm": raw_median,
                    "raw_iqr": raw_iqr,
                    "raw_iqr_mm": raw_iqr,
                    "scatter_rms": scatter_rms,
                    "scatter_rms_mm": scatter_rms,
                    "input_residual_definition": "raw=measured-geometric; d_anchor_mm/tag_delay_mm not subtracted",
                    "firmware_antenna_delay_setting_dtu": FIRMWARE_ANTENNA_DELAY_DTU,
                    "terminology_note": WHY9_TERMINOLOGY_NOTE,
                }
            )

    if not np.isfinite(table).any():
        raise RuntimeError(f"WHY #9 {scenario_name}: all cell medians are NaN")
    for ti, tag in enumerate(PHYSICAL_TAGS):
        if not np.isfinite(table[ti]).any():
            raise RuntimeError(f"WHY #9 {scenario_name}: no usable cell medians for tag {tag}")
    for aid, anchor_label in enumerate(ANCHOR_LABELS):
        if not np.isfinite(table[:, aid]).any():
            raise RuntimeError(f"WHY #9 {scenario_name}: no usable cell medians for anchor {anchor_label}")

    polish = median_polish_nanaware(table)
    effect_row: dict[str, float | int | str | bool] = {
        "scenario": scenario_name,
        "grand": float(polish["grand"]),
        "grand_common_mode_mm": float(polish["grand"]),
        "interaction_median_abs_mm": float(polish["interaction_median_abs_mm"]),
        "interaction_max_abs_mm": float(polish["interaction_max_abs_mm"]),
        "median_polish_iterations": int(polish["iterations"]),
        "median_polish_converged": bool(polish["converged"]),
        "median_polish_max_update_mm": float(polish["max_update_mm"]),
        "thin_cell_count": int(thin_cell_count),
        "min_cell_n": int(min_cell_n),
        "firmware_antenna_delay_setting_dtu": FIRMWARE_ANTENNA_DELAY_DTU,
        "terminology_note": WHY9_TERMINOLOGY_NOTE,
        "gauge_note": WHY9_GAUGE_NOTE,
        "input_residual_definition": "raw=measured-geometric; no model_corrected/d_anchor/tag_delay subtraction",
    }
    for ti, tag in enumerate(PHYSICAL_TAGS):
        effect_row[f"tag_main_{tag}_mm"] = float(polish["tag_effect"][ti])
    for aid, anchor_label in enumerate(ANCHOR_LABELS):
        effect_row[f"anchor_main_{anchor_label}_mm"] = float(polish["anchor_effect"][aid])
    for ti, tag in enumerate(PHYSICAL_TAGS):
        for aid, anchor_label in enumerate(ANCHOR_LABELS):
            val = float(polish["interaction"][ti, aid])
            effect_row[f"interaction_{tag}_{anchor_label}_mm"] = val
    return cell_rows, effect_row, polish


def why9_stability_rows(effect_rows: list[dict]) -> tuple[list[dict], dict]:
    effects = {str(row["scenario"]): row for row in effect_rows}
    required = ["self_cal_v4io_T4", "vicon_truth_delaycal_T4"]
    missing = [name for name in required if name not in effects]
    if missing:
        raise RuntimeError(f"WHY #9 missing scenario effects: {missing}")

    pair_delta: dict[str, float] = {}
    max_pairwise_abs = 0.0
    rows: list[dict] = []
    for scenario_name in required:
        row = effects[scenario_name]
        out: dict[str, float | str | int | bool] = {
            "row_type": "scenario",
            "scenario": scenario_name,
            "grand_common_mode_mm": float(row["grand_common_mode_mm"]),
            "interaction_median_abs_mm": float(row["interaction_median_abs_mm"]),
            "interaction_max_abs_mm": float(row["interaction_max_abs_mm"]),
            "median_polish_iterations": int(row["median_polish_iterations"]),
            "median_polish_converged": bool(row["median_polish_converged"]),
            "firmware_antenna_delay_setting_dtu": FIRMWARE_ANTENNA_DELAY_DTU,
            "terminology_note": WHY9_TERMINOLOGY_NOTE,
            "gauge_note": WHY9_GAUGE_NOTE,
        }
        for left, right in WHY9_PAIRWISE_TAG_DIFFS:
            diff = float(row[f"tag_main_{left}_mm"]) - float(row[f"tag_main_{right}_mm"])
            out[why9_pair_key(left, right)] = diff
            max_pairwise_abs = max(max_pairwise_abs, abs(diff))
        rows.append(out)

    for left, right in WHY9_PAIRWISE_TAG_DIFFS:
        key = why9_pair_key(left, right)
        pair_delta[key] = abs(float(rows[0][key]) - float(rows[1][key]))
    for row in rows:
        for key, val in pair_delta.items():
            row[f"cross_scenario_abs_delta_{key}"] = float(val)

    max_pairwise_delta = max(pair_delta.values()) if pair_delta else float("nan")
    max_interaction_median = max(float(effects[name]["interaction_median_abs_mm"]) for name in required)
    if max_pairwise_abs <= 1e-9:
        verdict = "INTERACTION_DOMINATED"
        consequence = "No identifiable pairwise tag residual structure was recovered; do not quote per-tag residual delay differences."
    elif max_interaction_median >= max_pairwise_abs:
        verdict = "INTERACTION_DOMINATED"
        consequence = "Geometry/marker/NLOS interaction is at least as large as the additive tag structure; do not quote a clean per-tag scalar."
    elif max_pairwise_delta >= 10.0:
        verdict = "PER_TAG_MODEL_COUPLED"
        consequence = "Pairwise tag residual differences change across coordinate scenarios; the additive tag scalar is model-coupled."
    elif max_interaction_median < 0.5 * max_pairwise_abs:
        verdict = "PER_TAG_PHYSICAL_STABLE"
        consequence = (
            "Pairwise tag_main differences are stable and are the deployable per-device residual trim targets; "
            "confirm with an independent known-baseline loop before changing firmware antenna-delay settings."
        )
    else:
        verdict = "INTERACTION_DOMINATED"
        consequence = "Pairwise tag residual differences are stable but interaction is too large for a clean scalar calibration claim."

    overall: dict[str, float | str | int] = {
        "row_type": "overall",
        "scenario": "cross_scenario",
        "max_pairwise_tagdiff_abs_mm": float(max_pairwise_abs),
        "max_cross_scenario_abs_delta_tagdiff_mm": float(max_pairwise_delta),
        "max_interaction_median_abs_mm": float(max_interaction_median),
        "why9_verdict": verdict,
        "paper_consequence": consequence,
        "firmware_antenna_delay_setting_dtu": FIRMWARE_ANTENNA_DELAY_DTU,
        "terminology_note": WHY9_TERMINOLOGY_NOTE,
        "gauge_note": WHY9_GAUGE_NOTE,
    }
    for key, val in pair_delta.items():
        overall[f"cross_scenario_abs_delta_{key}"] = float(val)
    rows.append(overall)
    return rows, overall


def rel_a(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1 or arr.size == 0:
        raise ValueError(f"rel_a expects a non-empty 1D array, got {arr.shape}")
    return arr - arr[0]


def corr_gap(left: np.ndarray, right: np.ndarray) -> tuple[float, float]:
    left_arr = np.asarray(left, dtype=float)
    right_arr = np.asarray(right, dtype=float)
    finite = np.isfinite(left_arr) & np.isfinite(right_arr)
    if int(np.sum(finite)) < 4:
        raise RuntimeError("WHY #9 anchor consistency has fewer than four finite anchors")
    if float(np.std(left_arr[finite])) > 1e-9 and float(np.std(right_arr[finite])) > 1e-9:
        corr = float(np.corrcoef(left_arr[finite], right_arr[finite])[0, 1])
    else:
        corr = float("nan")
    return corr, pct(np.abs(left_arr[finite] - right_arr[finite]), 50)


def anchor_check_consistent(corr: float, median_abs_gap_mm: float) -> bool:
    return bool(math.isfinite(corr) and corr >= 0.8 and math.isfinite(median_abs_gap_mm) and median_abs_gap_mm <= 20.0)


def simple_linear_regression(y: np.ndarray, x: np.ndarray) -> tuple[float, float, float, np.ndarray, np.ndarray]:
    y_arr = np.asarray(y, dtype=float)
    x_arr = np.asarray(x, dtype=float)
    finite = np.isfinite(y_arr) & np.isfinite(x_arr)
    if int(np.sum(finite)) < 4:
        nan_arr = np.full_like(y_arr, float("nan"), dtype=float)
        return float("nan"), float("nan"), float("nan"), nan_arr, nan_arr
    design = np.column_stack([np.ones(int(np.sum(finite))), x_arr[finite]])
    intercept, slope = np.linalg.lstsq(design, y_arr[finite], rcond=None)[0]
    pred_finite = design @ np.asarray([intercept, slope], dtype=float)
    ss_res = float(np.sum((y_arr[finite] - pred_finite) ** 2))
    ss_tot = float(np.sum((y_arr[finite] - float(np.mean(y_arr[finite]))) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0.0 else float("nan")
    pred = np.full_like(y_arr, float("nan"), dtype=float)
    resid = np.full_like(y_arr, float("nan"), dtype=float)
    pred[finite] = pred_finite
    resid[finite] = y_arr[finite] - pred_finite
    return float(intercept), float(slope), r2, pred, resid


def v4io_layout_error_by_anchor() -> dict[str, dict[str, float]]:
    path = FULL_ROOT / "tables" / "layout_abs_errors_all8.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    df = df[(df["version"].astype(str) == "v4-io") & (df["eval_set"].astype(str) == "all8")].copy()
    if df.empty:
        return {}
    truth = df[["truth_x_mm", "truth_y_vertical_mm", "truth_z_mm"]].to_numpy(float)
    centroid = np.nanmean(truth, axis=0)
    out: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        label = str(row["anchor"])
        err_vec = np.asarray([row["err_x_mm"], row["err_y_vertical_mm"], row["err_z_mm"]], dtype=float)
        truth_vec = np.asarray([row["truth_x_mm"], row["truth_y_vertical_mm"], row["truth_z_mm"]], dtype=float)
        radial = truth_vec - centroid
        radial_norm = float(np.linalg.norm(radial))
        radial_out = float(np.dot(err_vec, radial / radial_norm)) if radial_norm > 0.0 else float("nan")
        out[label] = {
            "layout_error_3d_mm": float(row["err_3d_mm"]),
            "layout_error_horizontal_mm": float(row["err_horizontal_mm"]),
            "layout_error_vertical_mm": float(row["err_vertical_mm"]),
            "layout_error_radial_outward_mm": radial_out,
            "layout_error_radial_inward_mm": -radial_out if math.isfinite(radial_out) else float("nan"),
        }
    return out


def why9_anchor_consistency_rows(effect_rows: list[dict], solver_d_anchor_by_scenario: dict[str, dict[int, float]]) -> list[dict]:
    effects = {str(row["scenario"]): row for row in effect_rows}
    required = ["self_cal_v4io_T4", "vicon_truth_delaycal_T4"]
    missing_effects = [name for name in required if name not in effects]
    missing_delays = [name for name in required if name not in solver_d_anchor_by_scenario]
    if missing_effects or missing_delays:
        raise RuntimeError(f"WHY #9 anchor consistency missing effects={missing_effects} delays={missing_delays}")

    anchor_main_rel_by_scenario: dict[str, np.ndarray] = {}
    d_anchor_rel_by_scenario: dict[str, np.ndarray] = {}
    for scenario_name in required:
        anchor_main = np.asarray(
            [float(effects[scenario_name][f"anchor_main_{label}_mm"]) for label in ANCHOR_LABELS],
            dtype=float,
        )
        d_anchor = np.asarray(
            [float(solver_d_anchor_by_scenario[scenario_name].get(aid, 0.0)) for aid in range(len(ANCHOR_LABELS))],
            dtype=float,
        )
        anchor_main_rel_by_scenario[scenario_name] = rel_a(anchor_main)
        d_anchor_rel_by_scenario[scenario_name] = rel_a(d_anchor)
        if not math.isclose(float(anchor_main_rel_by_scenario[scenario_name][0]), 0.0, abs_tol=1e-9):
            raise AssertionError(f"WHY #9 anchor_main_rel_A convention failed for {scenario_name}")
        if not math.isclose(float(d_anchor_rel_by_scenario[scenario_name][0]), 0.0, abs_tol=1e-9):
            raise AssertionError(f"WHY #9 d_anchor_rel_A convention failed for {scenario_name}")

    self_main_rel = anchor_main_rel_by_scenario["self_cal_v4io_T4"]
    vicon_main_rel = anchor_main_rel_by_scenario["vicon_truth_delaycal_T4"]
    self_d_rel = d_anchor_rel_by_scenario["self_cal_v4io_T4"]
    vicon_d_rel = d_anchor_rel_by_scenario["vicon_truth_delaycal_T4"]
    self_sign_direct_corr, _self_sign_gap = corr_gap(self_main_rel, self_d_rel)
    self_sign_negated_corr, _self_neg_gap = corr_gap(self_main_rel, -self_d_rel)
    vicon_sign_direct_corr, _vicon_sign_gap = corr_gap(vicon_main_rel, vicon_d_rel)
    vicon_sign_negated_corr, _vicon_neg_gap = corr_gap(vicon_main_rel, -vicon_d_rel)
    if not (
        math.isfinite(self_sign_direct_corr)
        and math.isfinite(self_sign_negated_corr)
        and math.isfinite(vicon_sign_direct_corr)
        and math.isfinite(vicon_sign_negated_corr)
        and self_sign_direct_corr > self_sign_negated_corr
        and vicon_sign_direct_corr > vicon_sign_negated_corr
    ):
        raise AssertionError(
            "WHY #9 d_anchor sign convention failed: negated solver d_anchor correlates at least as well as direct sign"
        )

    check_defs = [
        {
            "check_type": "within_self_cal",
            "scenario_label": "self_cal_v4io_T4",
            "left_label": "self_anchor_main_rel_A_mm",
            "right_label": "self_d_anchor_rel_A_mm",
            "left": self_main_rel,
            "right": self_d_rel,
            "interpretation_pass": "median polish reproduces the self-cal layout-level residual delay correction",
            "interpretation_fail": "self-cal median polish does not reproduce self-cal d_anchor; inspect centering/sign/reference handling",
        },
        {
            "check_type": "within_vicon",
            "scenario_label": "vicon_truth_delaycal_T4",
            "left_label": "vicon_anchor_main_rel_A_mm",
            "right_label": "vicon_d_anchor_rel_A_mm",
            "left": vicon_main_rel,
            "right": vicon_d_rel,
            "interpretation_pass": "median polish reproduces the Vicon-truth delaycal layout-level residual delay correction",
            "interpretation_fail": "Vicon median polish does not reproduce Vicon delaycal d_anchor; inspect additive model or delaycal reference",
        },
        {
            "check_type": "cross_vicon_main_vs_self_danchor",
            "scenario_label": "vicon_main_vs_self_danchor",
            "left_label": "vicon_anchor_main_rel_A_mm",
            "right_label": "self_d_anchor_rel_A_mm",
            "left": vicon_main_rel,
            "right": self_d_rel,
            "interpretation_pass": "cross-scenario comparison broadly matches the old anchor consistency check",
            "interpretation_fail": "cross-scenario comparison is flagged; this may be layout/coordinate absorption rather than a decomposition bug",
        },
    ]

    rows: list[dict] = []
    check_stats: dict[str, dict[str, float | bool | str]] = {}
    for check in check_defs:
        left = np.asarray(check["left"], dtype=float)
        right = np.asarray(check["right"], dtype=float)
        corr, median_abs_gap = corr_gap(left, right)
        if check["check_type"] == "cross_vicon_main_vs_self_danchor":
            bcd_med = pct([left[1], left[2], left[3]], 50)
            consistent = bool(
                math.isfinite(corr)
                and corr >= 0.50
                and math.isfinite(median_abs_gap)
                and median_abs_gap <= 25.0
                and math.isfinite(bcd_med)
                and bcd_med > left[0]
            )
            cross_verdict = "ANCHOR_CONSISTENCY_CONFIRMED" if consistent else "ANCHOR_CONSISTENCY_FLAGGED"
        else:
            consistent = anchor_check_consistent(corr, median_abs_gap)
            cross_verdict = ""
        interpretation = str(check["interpretation_pass"] if consistent else check["interpretation_fail"])
        check_stats[str(check["check_type"])] = {
            "corr": corr,
            "median_abs_gap": median_abs_gap,
            "consistent": consistent,
            "cross_verdict": cross_verdict,
            "interpretation": interpretation,
        }
        for aid, label in enumerate(ANCHOR_LABELS):
            gap = float(left[aid] - right[aid])
            rows.append(
                {
                    "check_type": check["check_type"],
                    "scenario_label": check["scenario_label"],
                    "scenario": check["scenario_label"],
                    "anchor": label,
                    "anchor_id": aid,
                    "left_column": check["left_label"],
                    "right_column": check["right_label"],
                    "left_value_rel_A_mm": float(left[aid]),
                    "right_value_rel_A_mm": float(right[aid]),
                    "gap_left_minus_right_mm": gap,
                    "anchor_main_rel_A_mm": float(left[aid]),
                    "solver_d_anchor_rel_A_mm": float(right[aid]),
                    "gap_anchor_main_minus_solver_d_anchor_mm": gap,
                    "anchor_consistency_corr": corr,
                    "anchor_consistency_median_abs_gap_mm": median_abs_gap,
                    "check_consistent": bool(consistent),
                    "anchor_consistency_verdict": cross_verdict,
                    "interpretation": interpretation,
                    "firmware_antenna_delay_setting_dtu": FIRMWARE_ANTENNA_DELAY_DTU,
                    "terminology_note": WHY9_TERMINOLOGY_NOTE,
                    "gauge_note": WHY9_GAUGE_NOTE,
                    "identifiability_note": "only relative-to-A anchor_main/d_anchor differences are identifiable from tag-to-anchor ranges",
                }
            )

    coord_delta = self_main_rel - vicon_main_rel
    coord_median_abs = pct(np.abs(coord_delta[np.isfinite(coord_delta)]), 50)
    layout_error = v4io_layout_error_by_anchor()
    layout_3d = np.asarray([layout_error.get(label, {}).get("layout_error_3d_mm", float("nan")) for label in ANCHOR_LABELS])
    layout_radial_out = np.asarray(
        [layout_error.get(label, {}).get("layout_error_radial_outward_mm", float("nan")) for label in ANCHOR_LABELS],
        dtype=float,
    )
    layout_3d_intercept, layout_3d_slope, layout_3d_r2, layout_3d_pred, layout_3d_resid = simple_linear_regression(
        coord_delta,
        layout_3d,
    )
    radial_intercept, radial_slope, radial_r2, radial_pred, radial_resid = simple_linear_regression(
        coord_delta,
        layout_radial_out,
    )
    absorption_explained = bool(math.isfinite(radial_r2) and radial_r2 >= 0.8 and math.isfinite(radial_slope))
    for aid, label in enumerate(ANCHOR_LABELS):
        layout_info = layout_error.get(label, {})
        rows.append(
            {
                "check_type": "coord_scale_error",
                "scenario_label": "self_minus_vicon",
                "scenario": "self_minus_vicon",
                "anchor": label,
                "anchor_id": aid,
                "left_column": "self_anchor_main_rel_A_mm",
                "right_column": "vicon_anchor_main_rel_A_mm",
                "left_value_rel_A_mm": float(self_main_rel[aid]),
                "right_value_rel_A_mm": float(vicon_main_rel[aid]),
                "gap_left_minus_right_mm": float(coord_delta[aid]),
                "coord_scale_error_self_minus_vicon_mm": float(coord_delta[aid]),
                "layout_error_3d_mm": layout_info.get("layout_error_3d_mm", float("nan")),
                "layout_error_horizontal_mm": layout_info.get("layout_error_horizontal_mm", float("nan")),
                "layout_error_vertical_mm": layout_info.get("layout_error_vertical_mm", float("nan")),
                "layout_error_radial_outward_mm": layout_info.get("layout_error_radial_outward_mm", float("nan")),
                "layout_error_radial_inward_mm": layout_info.get("layout_error_radial_inward_mm", float("nan")),
                "layout_absorption_pred_radial_mm": float(radial_pred[aid]),
                "layout_absorption_residual_radial_mm": float(radial_resid[aid]),
                "layout_absorption_pred_3d_mm": float(layout_3d_pred[aid]),
                "layout_absorption_residual_3d_mm": float(layout_3d_resid[aid]),
                "layout_absorption_radial_r2": radial_r2,
                "layout_absorption_radial_slope_mm_per_mm": radial_slope,
                "layout_absorption_radial_intercept_mm": radial_intercept,
                "layout_absorption_3d_r2": layout_3d_r2,
                "layout_absorption_3d_slope_mm_per_mm": layout_3d_slope,
                "layout_absorption_3d_intercept_mm": layout_3d_intercept,
                "anchor_consistency_corr": float("nan"),
                "anchor_consistency_median_abs_gap_mm": coord_median_abs,
                "check_consistent": "",
                "anchor_consistency_verdict": "",
                "interpretation": "per-anchor coordinate/scale absorption signature: self-cal anchor_main relative to A minus Vicon anchor_main relative to A",
                "firmware_antenna_delay_setting_dtu": FIRMWARE_ANTENNA_DELAY_DTU,
                "terminology_note": WHY9_TERMINOLOGY_NOTE,
                "gauge_note": WHY9_GAUGE_NOTE,
                "identifiability_note": "this is a relative per-anchor self-minus-vicon pattern, not an absolute physical delay",
            }
        )

    within_self_ok = bool(check_stats["within_self_cal"]["consistent"])
    within_vicon_ok = bool(check_stats["within_vicon"]["consistent"])
    no_sign_flip = bool(
        math.isfinite(self_sign_direct_corr)
        and math.isfinite(self_sign_negated_corr)
        and math.isfinite(vicon_sign_direct_corr)
        and math.isfinite(vicon_sign_negated_corr)
        and self_sign_direct_corr > self_sign_negated_corr
        and vicon_sign_direct_corr > vicon_sign_negated_corr
    )
    if absorption_explained and no_sign_flip:
        overall_verdict = "ANCHOR_DECOMP_GAUGE_ABSORBED"
        consequence = (
            "The cross-scenario anchor mismatch is explained by coordinate/scale gauge absorption: self-minus-Vicon anchor_main_rel_A "
            f"regresses on v4-io radial layout error with R^2={radial_r2:.3f} and slope={radial_slope:.3f} mm/mm. "
            "Self-cal d_anchor should therefore be described as a layout-level residual correction, not a physical anchor delay. "
            "Within-scenario d_anchor checks are not exact because median-polish range residuals and solver delaycal use different objectives/references."
        )
    elif within_self_ok and within_vicon_ok:
        overall_verdict = "ANCHOR_DECOMP_SOUND_FLAG_IS_LAYOUT"
        consequence = (
            "Median polish reproduces each scenario's own d_anchor; the cross-scenario flag is a real self-cal-vs-truth "
            "layout difference (coordinate/scale absorption), not a decomposition error. The self-cal d_anchor is therefore not a physical anchor delay."
        )
    else:
        overall_verdict = "ANCHOR_DECOMP_INCONSISTENT_INVESTIGATE"
        consequence = (
            "At least one within-scenario anchor check fails; resolve a centering/sign/reference bug before interpreting the cross-scenario flag."
        )
    rows.append(
        {
            "check_type": "overall",
            "scenario_label": "all",
            "scenario": "all",
            "anchor": "ALL",
            "anchor_id": -1,
            "within_self_cal_corr": check_stats["within_self_cal"]["corr"],
            "within_self_cal_negated_corr": self_sign_negated_corr,
            "within_self_cal_median_abs_gap_mm": check_stats["within_self_cal"]["median_abs_gap"],
            "within_self_cal_consistent": within_self_ok,
            "within_vicon_corr": check_stats["within_vicon"]["corr"],
            "within_vicon_negated_corr": vicon_sign_negated_corr,
            "within_vicon_median_abs_gap_mm": check_stats["within_vicon"]["median_abs_gap"],
            "within_vicon_consistent": within_vicon_ok,
            "anchor_reference_convention": "anchor_main_rel_A = anchor_main - anchor_main[A]; d_anchor_rel_A = d_anchor - d_anchor[A] for both scenarios",
            "sign_convention_check": "direct d_anchor sign is used for correlation; negated d_anchor sign is worse for both within-scenario checks",
            "no_sign_flip_detected": no_sign_flip,
            "cross_vicon_main_vs_self_danchor_corr": check_stats["cross_vicon_main_vs_self_danchor"]["corr"],
            "cross_vicon_main_vs_self_danchor_median_abs_gap_mm": check_stats["cross_vicon_main_vs_self_danchor"]["median_abs_gap"],
            "cross_vicon_main_vs_self_danchor_verdict": check_stats["cross_vicon_main_vs_self_danchor"]["cross_verdict"],
            "coord_scale_error_median_abs_mm": coord_median_abs,
            "layout_absorption_explained": absorption_explained,
            "layout_absorption_radial_r2": radial_r2,
            "layout_absorption_radial_slope_mm_per_mm": radial_slope,
            "layout_absorption_radial_intercept_mm": radial_intercept,
            "layout_absorption_3d_r2": layout_3d_r2,
            "layout_absorption_3d_slope_mm_per_mm": layout_3d_slope,
            "layout_absorption_3d_intercept_mm": layout_3d_intercept,
            "anchor_decomp_verdict": overall_verdict,
            "paper_consequence": consequence,
            "firmware_antenna_delay_setting_dtu": FIRMWARE_ANTENNA_DELAY_DTU,
            "terminology_note": WHY9_TERMINOLOGY_NOTE,
            "gauge_note": WHY9_GAUGE_NOTE,
            "identifiability_note": "within-scenario differences test decomposition soundness; cross-scenario differences test layout coordinate/scale absorption",
        }
    )
    return rows


def audit_why9_twoway_range_decomposition() -> tuple[list[dict], list[dict], list[dict], list[dict], dict]:
    ablation_mod = import_static_ablation_module()
    roto_mod = import_roto_module()
    mapping = selected_wand_mapping()
    scenarios, _static_files = make_static_single_shot_scenarios(ablation_mod)
    roto_tr_all = discover_roto_tr_all(roto_mod)
    offsets = pd.read_csv(FULL_ROOT / "roto_absolute" / "tables" / "roto_time_offsets_v4io_T4.csv")
    capture_ids = sorted(offsets.loc[offsets["status"] == "ok", "capture_id"].astype(str).tolist())
    opti_cache = load_opti_cache(roto_mod, capture_ids)

    cell_rows: list[dict] = []
    effect_rows: list[dict] = []
    solver_d_anchor_by_scenario: dict[str, dict[int, float]] = {}
    for scenario in scenarios:
        scenario_name = str(scenario["scenario"])
        residuals = collect_tag_anchor_range_residuals(
            ablation_mod,
            roto_mod,
            scenario,
            roto_tr_all,
            offsets,
            opti_cache,
            mapping,
        )
        if not residuals:
            raise RuntimeError(f"WHY #9 no raw residuals collected for {scenario_name}")
        scenario_cell_rows, effect_row, _polish = build_why9_cells_and_effects(scenario_name, residuals)
        cell_rows.extend(scenario_cell_rows)
        effect_rows.append(effect_row)
        solver_d_anchor_by_scenario[scenario_name] = {
            aid: float(scenario["layout"].anchors[aid].d_anchor_mm)
            for aid in range(len(ANCHOR_LABELS))
        }

    stability_rows, verdict_summary = why9_stability_rows(effect_rows)
    anchor_consistency_rows = why9_anchor_consistency_rows(effect_rows, solver_d_anchor_by_scenario)
    return cell_rows, effect_rows, stability_rows, anchor_consistency_rows, verdict_summary


def build_report(
    roto_summary: list[dict],
    dynamic_budget: dict[str, float],
    center_rms_rows: list[dict],
    roto_filtered_summary: list[dict],
    roto_pseudo_summary: list[dict],
    roto_pseudo_extrinsics: list[dict],
    skew_summary: list[dict],
    single_shot_summary: list[dict],
    bias_scatter_summary: list[dict],
    tag_delay_summary: list[dict],
    gdop_overlap_rows: list[dict],
    why9_effect_rows: list[dict],
    why9_stability_rows: list[dict],
    why9_anchor_consistency_rows: list[dict],
    why9_verdict_summary: dict,
    one_cv_summary: dict,
    procrustes_rows: list[dict],
    prod_summary: dict,
) -> str:
    self_roto = next(r for r in roto_summary if r["scenario"] == "self_cal_v4io_T4")
    vicon_roto = next(r for r in roto_summary if r["scenario"] == "vicon_truth_delaycal_T4")
    center_by_case = {str(r["case"]): r for r in center_rms_rows}
    center_self = center_by_case["full_original_v4io_T4"]
    center_vicon = center_by_case["vicon_truth_delaycal_v4io_T4"]
    center_scale = center_by_case["scale_to_vicon_delaycal_v4io_T4"]
    center_one_eh = center_by_case["one_baseline_EH_delaycal_v4io_T4"]
    center_one_best = center_by_case["one_baseline_best_roto_solver_delay_v4io_T4_BC"]
    filtered_by_case = {
        (str(r["case"]), str(r["filter_id"])): r
        for r in roto_filtered_summary
    }
    filt_self_f0 = filtered_by_case[("full_original_v4io_T4", "F0")]
    filt_self_f3 = filtered_by_case[("full_original_v4io_T4", "F3")]
    filt_self_f4 = filtered_by_case[("full_original_v4io_T4", "F4")]
    filt_self_f5 = filtered_by_case[("full_original_v4io_T4", "F5")]
    filt_vicon_f0 = filtered_by_case[("vicon_truth_delaycal_v4io_T4", "F0")]
    filt_vicon_f3 = filtered_by_case[("vicon_truth_delaycal_v4io_T4", "F3")]
    filt_vicon_f4 = filtered_by_case[("vicon_truth_delaycal_v4io_T4", "F4")]
    filt_vicon_f5 = filtered_by_case[("vicon_truth_delaycal_v4io_T4", "F5")]
    filt_scale_f4 = filtered_by_case[("scale_to_vicon_delaycal_v4io_T4", "F4")]
    filt_one_eh_f4 = filtered_by_case[("one_baseline_EH_delaycal_v4io_T4", "F4")]
    pseudo_by_case = {
        (str(r["case"]), str(r["fusion_id"])): r
        for r in roto_pseudo_summary
    }
    pseudo_self_pi0 = pseudo_by_case[("full_original_v4io_T4", "PI0")]
    pseudo_self_pi1 = pseudo_by_case[("full_original_v4io_T4", "PI1")]
    pseudo_self_pi2 = pseudo_by_case[("full_original_v4io_T4", "PI2")]
    pseudo_self_pi4 = pseudo_by_case[("full_original_v4io_T4", "PI4")]
    pseudo_vicon_pi1 = pseudo_by_case[("vicon_truth_delaycal_v4io_T4", "PI1")]
    pseudo_vicon_pi4 = pseudo_by_case[("vicon_truth_delaycal_v4io_T4", "PI4")]
    pseudo_scale_pi1 = pseudo_by_case[("scale_to_vicon_delaycal_v4io_T4", "PI1")]
    pseudo_one_eh_pi1 = pseudo_by_case[("one_baseline_EH_delaycal_v4io_T4", "PI1")]
    pseudo_extrinsic_p50 = pct([float(r["bodyfit_antenna_residual_p50_mm"]) for r in roto_pseudo_extrinsics], 50)
    pseudo_extrinsic_p95_of_p95 = pct([float(r["bodyfit_antenna_residual_p95_mm"]) for r in roto_pseudo_extrinsics], 50)
    self_skew = next(r for r in skew_summary if r["scenario"] == "self_cal_v4io_T4")
    vicon_skew = next(r for r in skew_summary if r["scenario"] == "vicon_truth_delaycal_T4")
    self_single = next(r for r in single_shot_summary if r["scenario"] == "self_cal_v4io_T4")
    vicon_single = next(r for r in single_shot_summary if r["scenario"] == "vicon_truth_delaycal_T4")
    self_bias = next(r for r in bias_scatter_summary if r["scenario"] == "self_cal_v4io_T4")
    self_tag_delay = next(r for r in tag_delay_summary if r["scenario"] == "self_cal_v4io_T4")
    self_gdop_overlap = next(
        r for r in gdop_overlap_rows if r.get("scenario") == "self_cal_v4io_T4" and r.get("row_type") == "summary"
    )
    self_shared_gdop_bins = [
        r for r in gdop_overlap_rows if r.get("scenario") == "self_cal_v4io_T4" and r.get("row_type") == "shared_bin"
    ]
    thin_self_gdop_bins = sum(1 for r in self_shared_gdop_bins if bool(r.get("thin_bin_flag", False)))
    why9_self = next(r for r in why9_effect_rows if r["scenario"] == "self_cal_v4io_T4")
    why9_vicon = next(r for r in why9_effect_rows if r["scenario"] == "vicon_truth_delaycal_T4")
    why9_stability_by_scenario = {str(r["scenario"]): r for r in why9_stability_rows if r.get("row_type") == "scenario"}
    why9_self_stability = why9_stability_by_scenario["self_cal_v4io_T4"]
    why9_vicon_stability = why9_stability_by_scenario["vicon_truth_delaycal_T4"]
    why9_anchor_cross_a = next(
        r for r in why9_anchor_consistency_rows
        if r.get("check_type") == "cross_vicon_main_vs_self_danchor" and r.get("anchor") == "A"
    )
    why9_anchor_within_self_a = next(
        r for r in why9_anchor_consistency_rows
        if r.get("check_type") == "within_self_cal" and r.get("anchor") == "A"
    )
    why9_anchor_within_vicon_a = next(
        r for r in why9_anchor_consistency_rows
        if r.get("check_type") == "within_vicon" and r.get("anchor") == "A"
    )
    why9_anchor_coord_a = next(
        r for r in why9_anchor_consistency_rows
        if r.get("check_type") == "coord_scale_error" and r.get("anchor") == "A"
    )
    why9_anchor_overall = next(
        r for r in why9_anchor_consistency_rows
        if r.get("check_type") == "overall" and r.get("anchor") == "ALL"
    )
    why9_anchor_cross_by_label = {
        str(r["anchor"]): r
        for r in why9_anchor_consistency_rows
        if r.get("check_type") == "cross_vicon_main_vs_self_danchor"
    }
    why9_anchor_coord_by_label = {
        str(r["anchor"]): r
        for r in why9_anchor_consistency_rows
        if r.get("check_type") == "coord_scale_error"
    }
    proc = procrustes_rows[0]
    timing_bound_ms = float(dynamic_budget["equivalent_timing_error_needed_ms"])
    timing_bound_skew_ppm_120s = float(dynamic_budget["equivalent_skew_needed_over_120s_ppm"])
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
        f"- The corresponding track-median P50 values are {fmt(self_roto['current_trackmedian_p50_mm'])} -> "
        f"{fmt(self_roto['best_time_trackmedian_p50_mm'])} mm. The paper headline uses track-median; this audit's pooled diagnostics also show sample-pooled values."
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
    lines.append("**Non-circular budget.** These are descriptive median/P95 quantities, not independent RMS components; they must not be quadrature-summed.")
    lines.append("")
    lines.append(
        f"- excluded constant-offset effect: {fmt(dynamic_budget['excluded_constant_offset_p50_mm'], 2)} mm P50 "
        f"({fmt(dynamic_budget['observed_self_cal_current_samplepooled_p50_mm'])} -> {fmt(dynamic_budget['constant_offset_best_samplepooled_p50_mm'])} mm sample-pooled)."
    )
    lines.append(
        f"- excluded 0.8 ms protocol-window motion: {fmt(dynamic_budget['excluded_motion_window_p95_mm'], 2)} mm P95 displacement."
    )
    lines.append(
        f"- oracle post-hoc rigid removable term: {fmt(dynamic_budget['oracle_rigid_removable_p50_mm'])} mm P50 "
        "(truth-fitted, diagnostic only)."
    )
    lines.append(
        f"- unattributed per-sample residual after that oracle rigid fit: "
        f"{fmt(dynamic_budget['unattributed_per_sample_residual_p50_mm'])} mm P50."
    )
    lines.append("")
    lines.append(
        "**Verdict.** The 0.8 ms protocol-window motion is negligible, and constant capture-level time offset is not the bottleneck. "
        "The oracle rigid fit removes only about 11 mm P50; the dominant remaining term is the ~92 mm per-sample residual, not a clean spatial registration error."
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
    lines.append(
        f"- Legacy no-groundtruth ROTO self-consistency, FULL self-cal v4-io/T4: dR RMS "
        f"{fmt(center_self['legacy_deltaR_error_rms_mm'])} mm; abs dR median/P95 "
        f"{fmt(center_self['legacy_abs_deltaR_error_median_mm'])}/{fmt(center_self['legacy_abs_deltaR_error_p95_mm'])} mm; "
        f"turn-center repeatability median/P95 {fmt(center_self['legacy_turn_center_rms_median_mm'])}/"
        f"{fmt(center_self['legacy_turn_center_rms_p95_mm'])} mm; inner/outer center separation median/P95 "
        f"{fmt(center_self['legacy_inner_outer_center_sep_median_mm'])}/"
        f"{fmt(center_self['legacy_inner_outer_center_sep_p95_mm'])} mm."
    )
    lines.append(
        f"- The same legacy dR RMS across FULL controls: self-cal {fmt(center_self['legacy_deltaR_error_rms_mm'])} mm; "
        f"Vicon-truth+delaycal {fmt(center_vicon['legacy_deltaR_error_rms_mm'])} mm; "
        f"scale-to-Vicon+delaycal {fmt(center_scale['legacy_deltaR_error_rms_mm'])} mm; "
        f"one-baseline E-H+delaycal {fmt(center_one_eh['legacy_deltaR_error_rms_mm'])} mm; "
        f"best one-baseline solver-delay row {fmt(center_one_best['legacy_deltaR_error_rms_mm'])} mm."
    )
    lines.append(
        f"- New OptiTrack/Vicon circle-level absolute metric: turn-center absolute 3D RMS, 34 tag-tracks: "
        f"FULL self-cal {fmt(center_self['opti_turn_center_abs_error_3d_rms_mm'])} mm; "
        f"Vicon-truth+delaycal {fmt(center_vicon['opti_turn_center_abs_error_3d_rms_mm'])} mm; "
        f"scale-to-Vicon+delaycal {fmt(center_scale['opti_turn_center_abs_error_3d_rms_mm'])} mm; "
        f"one-baseline E-H+delaycal {fmt(center_one_eh['opti_turn_center_abs_error_3d_rms_mm'])} mm; "
        f"best one-baseline solver-delay row {fmt(center_one_best['opti_turn_center_abs_error_3d_rms_mm'])} mm."
    )
    lines.append(
        f"- For comparison, the corresponding FULL self-cal sample-pooled absolute 3D RMSE is "
        f"{fmt(self_roto['current_sample_rmse_mm'])} mm, and Vicon-truth+delaycal sample-pooled absolute 3D RMSE is "
        f"{fmt(vicon_roto['current_sample_rmse_mm'])} mm."
    )
    lines.append("")
    lines.append(
        "**Verdict.** Absolute error does not collapse under a better constant time offset, so the current capture-level beta is not the main bottleneck. "
        "The old no-groundtruth circle metrics remain useful but must be labeled as self-consistency: they test radius separation, per-turn center repeatability, and two-tag center agreement. "
        "The 72-77 mm number is the new OptiTrack/Vicon absolute turn-center error, not the old turn-center repeatability metric. "
        "Thus the traditional circle-level ROTO metrics survive as relative/physical consistency checks, while absolute per-sample dynamic accuracy remains around the 105 mm P50 / 125-141 mm RMSE class."
    )
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** The relative-distance claim survives as a scale/delay-consistency metric. It must not be sold as absolute dynamic accuracy."
    )
    lines.append("")
    lines.append("## WHY #10: Does Post-Solve Dynamic Filtering Change ROTO?")
    lines.append("")
    lines.append("**Tests run.**")
    lines.append("")
    lines.append(
        "- Applied post-solve trajectory filters to the already solved, OptiTrack-aligned ROTO `v4-io/T4` sample trajectories, keeping layout, delay mode, tag solver, and capture-level beta fixed."
    )
    lines.append(
        "- Filter variants: `F0` passthrough; `F1` online constant-velocity Kalman; `F2` online robust innovation down-weighting; `F3` online adaptive-acceleration robust Kalman; `F4` bounded fixed-lag smoother; `F5` full-sequence RTS smoother."
    )
    lines.append(
        "- `F5` is an offline upper bound because it uses future samples. `F4` is deployable only with output latency. `F1-F3` are the online post-solve filters."
    )
    lines.append("")
    lines.append("**Numbers computed.**")
    lines.append("")
    lines.append(
        f"- Self-cal FULL track-median 3D P50/P95, F0/F3/F4/F5: "
        f"{fmt(filt_self_f0['trackmedian_err3d_p50_mm'])}/{fmt(filt_self_f0['trackmedian_err3d_p95_mm'])}, "
        f"{fmt(filt_self_f3['trackmedian_err3d_p50_mm'])}/{fmt(filt_self_f3['trackmedian_err3d_p95_mm'])}, "
        f"{fmt(filt_self_f4['trackmedian_err3d_p50_mm'])}/{fmt(filt_self_f4['trackmedian_err3d_p95_mm'])}, "
        f"{fmt(filt_self_f5['trackmedian_err3d_p50_mm'])}/{fmt(filt_self_f5['trackmedian_err3d_p95_mm'])} mm."
    )
    lines.append(
        f"- Vicon-truth+delaycal track-median 3D P50/P95, F0/F3/F4/F5: "
        f"{fmt(filt_vicon_f0['trackmedian_err3d_p50_mm'])}/{fmt(filt_vicon_f0['trackmedian_err3d_p95_mm'])}, "
        f"{fmt(filt_vicon_f3['trackmedian_err3d_p50_mm'])}/{fmt(filt_vicon_f3['trackmedian_err3d_p95_mm'])}, "
        f"{fmt(filt_vicon_f4['trackmedian_err3d_p50_mm'])}/{fmt(filt_vicon_f4['trackmedian_err3d_p95_mm'])}, "
        f"{fmt(filt_vicon_f5['trackmedian_err3d_p50_mm'])}/{fmt(filt_vicon_f5['trackmedian_err3d_p95_mm'])} mm."
    )
    lines.append(
        f"- Fixed-lag F4 across the main controls: self-cal {fmt(filt_self_f4['trackmedian_err3d_p50_mm'])}/{fmt(filt_self_f4['trackmedian_err3d_p95_mm'])} mm; "
        f"Vicon-truth {fmt(filt_vicon_f4['trackmedian_err3d_p50_mm'])}/{fmt(filt_vicon_f4['trackmedian_err3d_p95_mm'])} mm; "
        f"scale-to-Vicon {fmt(filt_scale_f4['trackmedian_err3d_p50_mm'])}/{fmt(filt_scale_f4['trackmedian_err3d_p95_mm'])} mm; "
        f"one-baseline E-H {fmt(filt_one_eh_f4['trackmedian_err3d_p50_mm'])}/{fmt(filt_one_eh_f4['trackmedian_err3d_p95_mm'])} mm."
    )
    lines.append(
        f"- Self-cal F4 improves track-median P50 by {fmt(filt_self_f4['improvement_vs_F0_trackmedian_err3d_p50_mm'])} mm versus F0; "
        f"F5 improves by {fmt(filt_self_f5['improvement_vs_F0_trackmedian_err3d_p50_mm'])} mm but is offline-only."
    )
    lines.append(
        f"- The best online self-cal row among F1-F3 is F3 at {fmt(filt_self_f3['trackmedian_err3d_p50_mm'])}/"
        f"{fmt(filt_self_f3['trackmedian_err3d_p95_mm'])} mm, which is worse in median than F0; "
        f"the same F3 row under Vicon-truth improves to {fmt(filt_vicon_f3['trackmedian_err3d_p50_mm'])}/"
        f"{fmt(filt_vicon_f3['trackmedian_err3d_p95_mm'])} mm."
    )
    lines.append("")
    lines.append(
        "**Verdict.** Dynamic filtering is real but conditional. Bounded-lag and offline smoothing can suppress the ROTO single-shot scatter and move the main result from the ~105 mm P50 class to the mid-80 mm class, but pure online post-solve filtering is not enough for the self-cal FULL trajectory and can even worsen the median. "
        "This means filtering addresses temporal scatter, not the full layout/ranging residual structure."
    )
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** Report unfiltered ROTO as the calibration-level dynamic validation. Report F1-F4 as deployment trajectory-filter ablations with latency/causality stated, and F5 only as an offline upper bound."
    )
    lines.append("")
    lines.append("## WHY #11: What If ROTO Had A Correctly Lever-Armed IMU Prior?")
    lines.append("")
    lines.append("**Tests run.**")
    lines.append("")
    lines.append(
        "- Fitted each wand's rigid-body pose from non-antenna OptiTrack markers, then estimated the body-to-UWB-antenna lever arm using `WandBantenna`/`WandCantenna`."
    )
    lines.append(
        "- Used the fitted antenna-point trajectory as an OptiTrack-derived pseudo-IMU relative-motion prior for already solved UWB antenna positions across the same 4x FULL ROTO cases."
    )
    lines.append(
        "- Variants: `PI0` passthrough; `PI1` strong causal pseudo-IMU prior; `PI2` balanced causal pseudo-IMU prior; `PI3` fixed-lag over PI1; `PI4/PI5` full-sequence RTS upper bounds."
    )
    lines.append("")
    lines.append("**Numbers computed.**")
    lines.append("")
    lines.append(
        f"- Lever-arm sanity: body-fit antenna residual across 34 capture/tag tracks is "
        f"{fmt(pseudo_extrinsic_p50, 2)} mm P50-of-P50 and {fmt(pseudo_extrinsic_p95_of_p95, 2)} mm P50-of-P95. "
        "So the prior is applied to the antenna point, not to the marker-body centroid."
    )
    lines.append(
        f"- Self-cal FULL track-median 3D P50/P95, PI0/PI1/PI2/PI4: "
        f"{fmt(pseudo_self_pi0['trackmedian_err3d_p50_mm'])}/{fmt(pseudo_self_pi0['trackmedian_err3d_p95_mm'])}, "
        f"{fmt(pseudo_self_pi1['trackmedian_err3d_p50_mm'])}/{fmt(pseudo_self_pi1['trackmedian_err3d_p95_mm'])}, "
        f"{fmt(pseudo_self_pi2['trackmedian_err3d_p50_mm'])}/{fmt(pseudo_self_pi2['trackmedian_err3d_p95_mm'])}, "
        f"{fmt(pseudo_self_pi4['trackmedian_err3d_p50_mm'])}/{fmt(pseudo_self_pi4['trackmedian_err3d_p95_mm'])} mm."
    )
    lines.append(
        f"- Vicon-truth+delaycal PI1/PI4: "
        f"{fmt(pseudo_vicon_pi1['trackmedian_err3d_p50_mm'])}/{fmt(pseudo_vicon_pi1['trackmedian_err3d_p95_mm'])} mm and "
        f"{fmt(pseudo_vicon_pi4['trackmedian_err3d_p50_mm'])}/{fmt(pseudo_vicon_pi4['trackmedian_err3d_p95_mm'])} mm."
    )
    lines.append(
        f"- PI1 across the 4x FULL cases: self-cal {fmt(pseudo_self_pi1['trackmedian_err3d_p50_mm'])}/{fmt(pseudo_self_pi1['trackmedian_err3d_p95_mm'])} mm; "
        f"Vicon-truth {fmt(pseudo_vicon_pi1['trackmedian_err3d_p50_mm'])}/{fmt(pseudo_vicon_pi1['trackmedian_err3d_p95_mm'])} mm; "
        f"scale-to-Vicon {fmt(pseudo_scale_pi1['trackmedian_err3d_p50_mm'])}/{fmt(pseudo_scale_pi1['trackmedian_err3d_p95_mm'])} mm; "
        f"one-baseline E-H {fmt(pseudo_one_eh_pi1['trackmedian_err3d_p50_mm'])}/{fmt(pseudo_one_eh_pi1['trackmedian_err3d_p95_mm'])} mm."
    )
    lines.append(
        f"- Self-cal PI1 improves track-median P50 by {fmt(pseudo_self_pi1['improvement_vs_PI0_trackmedian_err3d_p50_mm'])} mm versus PI0; "
        f"offline PI4 improves by {fmt(pseudo_self_pi4['improvement_vs_PI0_trackmedian_err3d_p50_mm'])} mm."
    )
    lines.append("")
    lines.append(
        "**Verdict.** A correctly lever-armed inertial relative-motion prior would materially reduce ROTO dynamic scatter: the self-cal trajectory moves from the 105.8/231.8 mm class to 66.1/97.5 mm under the strong causal pseudo-IMU prior. "
        "This is stronger than post-solve position filtering, but it is an OptiTrack-derived oracle diagnostic, not a deployable UWB+IMU result."
    )
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** Keep this as an upper-bound sensor-fusion argument. A real paper claim needs actual IMU data, IMU-to-antenna extrinsic calibration, and raw-range EKF/UKF validation; this audit only proves the lever-armed motion-prior channel has enough leverage to matter."
    )
    lines.append("")
    lines.append("## WHY #6: Does Intra-Capture Clock Skew Explain The ROTO Residual?")
    lines.append("")
    lines.append("**Test run.** For each capture, both wand tags share one affine time model:")
    lines.append("")
    lines.append("`t_query = uwb_time_s + beta + alpha * (uwb_time_s - t_ref)`")
    lines.append("")
    lines.append("with `t_ref` at the capture start. Alpha is reported in ppm. Search used coarse ±300 ppm / 10 ppm with beta ±50 ms / 1 ms, then fine ±20 ppm / 1 ppm with beta ±5 ms / 0.5 ms.")
    lines.append("")
    lines.append("**Numbers computed.**")
    lines.append("")
    lines.append(
        f"- Self-cal sample-pooled P50 beta0 / const-best / skew-best: "
        f"{fmt(self_skew['current_beta0_samplepooled_p50_mm'])} / "
        f"{fmt(self_skew['const_offset_best_samplepooled_p50_mm'])} / "
        f"{fmt(self_skew['skew_best_samplepooled_p50_mm'])} mm."
    )
    lines.append(
        f"- Self-cal track-median P50 beta0 / const-best / skew-best: "
        f"{fmt(self_skew['current_beta0_trackmedian_p50_mm'])} / "
        f"{fmt(self_skew['const_offset_best_trackmedian_p50_mm'])} / "
        f"{fmt(self_skew['skew_best_trackmedian_p50_mm'])} mm."
    )
    lines.append(
        f"- Self-cal alpha median ± IQR: {fmt(self_skew['alpha_ppm_median'], 1)} ± "
        f"{fmt(self_skew['alpha_ppm_iqr'], 1)} ppm; sign consistency "
        f"{fmt(100.0 * self_skew['alpha_sign_consistency'], 1)}%."
    )
    lines.append(
        f"- Vicon-truth+delaycal sample-pooled P50 const-best / skew-best: "
        f"{fmt(vicon_skew['const_offset_best_samplepooled_p50_mm'])} / "
        f"{fmt(vicon_skew['skew_best_samplepooled_p50_mm'])} mm; alpha median "
        f"{fmt(vicon_skew['alpha_ppm_median'], 1)} ppm."
    )
    lines.append(
        f"- Timing leverage bound: at the observed median ROTO speed of {fmt(self_roto['speed_p50_mm_s'])} mm/s, "
        f"explaining the remaining {fmt(dynamic_budget['unattributed_per_sample_residual_p50_mm'])} mm residual would require "
        f"about {fmt(timing_bound_ms, 0)} ms equivalent timing error."
    )
    lines.append(
        f"- If that were interpreted as a linear skew accumulated across a full 120 s capture, it would be "
        f"about {fmt(timing_bound_skew_ppm_120s, 0)} ppm; over any shorter local interval the required ppm is even larger."
    )
    lines.append("")
    lines.append(f"**Verdict.** `{self_skew['skew_verdict']}`.")
    lines.append("")
    lines.append(
        "**Consequence for the paper.** Do not claim the clocks are synchronized to a ppm bound; that is not what this test measures. "
        "The valid claim is stronger for this metric: offset/skew/jitter have no material leverage on the observed 90 mm-class dynamic residual."
    )
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** This survives as the correct falsification test because it checks both error reduction and cross-capture alpha consistency. "
        "If alpha is unstable, a lower skew-fit error is not enough evidence for physical clock drift; here the error surface is effectively flat."
    )
    lines.append("")
    lines.append("## WHY #7: Is ROTO Mostly Un-Averaged Single-Shot Static Precision?")
    lines.append("")
    lines.append("**Test run.** Replayed the raw static frames through the same `v4-io/T4` and `Vicon-truth+delaycal/T4` layouts, without per-position averaging, then compared those per-frame errors to ROTO per-sample errors. The same test also bins both distributions by range-only GDOP deciles.")
    lines.append("")
    lines.append("**Numbers computed.**")
    lines.append("")
    lines.append(
        f"- Self-cal static single-shot 3D P50/P95: {fmt(self_single['static_single_shot_p50_3d_mm'])} / "
        f"{fmt(self_single['static_single_shot_p95_3d_mm'])} mm; static per-position aggregate: "
        f"{fmt(self_single['static_per_position_p50_3d_mm'])} / {fmt(self_single['static_per_position_p95_3d_mm'])} mm."
    )
    lines.append(
        f"- Self-cal ROTO single-shot 3D P50/P95: {fmt(self_single['roto_single_shot_p50_3d_mm'])} / "
        f"{fmt(self_single['roto_single_shot_p95_3d_mm'])} mm."
    )
    lines.append(
        f"- Self-cal dynamic excess over static single-shot: 3D P50 {fmt(self_single['dynamic_excess_p50_3d_mm'])} mm; "
        f"XZ P50 {fmt(self_single['dynamic_excess_p50_xz_mm'])} mm; Y P50 {fmt(self_single['dynamic_excess_p50_y_mm'])} mm."
    )
    lines.append(
        f"- Self-cal averaging benefit from dwell-time static aggregation: 3D P50 "
        f"{fmt(self_single['averaging_benefit_p50_3d_mm'])} mm; XZ P50 "
        f"{fmt(self_single['averaging_benefit_p50_xz_mm'])} mm; Y P50 "
        f"{fmt(self_single['averaging_benefit_p50_y_mm'])} mm."
    )
    lines.append(
        f"- Self-cal GDOP-bin median absolute static-vs-ROTO P50 gap: "
        f"{fmt(self_single['gdop_median_abs_gap_p50_3d_mm'])} mm across "
        f"{int(self_single['gdop_shared_bin_count'])} shared bins; bins above 15 mm gap: "
        f"{int(self_single['gdop_bins_gap_gt15_count'])}."
    )
    lines.append(
        f"- Vicon-truth+delaycal dynamic excess over static single-shot: 3D P50 "
        f"{fmt(vicon_single['dynamic_excess_p50_3d_mm'])} mm; XZ P50 "
        f"{fmt(vicon_single['dynamic_excess_p50_xz_mm'])} mm; Y P50 "
        f"{fmt(vicon_single['dynamic_excess_p50_y_mm'])} mm."
    )
    lines.append("")
    lines.append(f"**Verdict.** Self-cal `{self_single['verdict']}`; Vicon-truth `{vicon_single['verdict']}`.")
    lines.append("")
    lines.append(f"**Consequence for the paper.** {self_single['paper_consequence']}")
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** This is the right next falsification test because it compares like with like: per-sample dynamic error versus per-sample static error, with GDOP controlled. "
        "If dynamic excess remains, it is a real dynamic/orientation/ranging term rather than a timing artifact or a lost-averaging artifact."
    )
    lines.append("")
    lines.append("## WHY #8: Is The WHY #7 Residual A Per-Tag Delay Mismatch?")
    lines.append("")
    lines.append("**Tests run.**")
    lines.append("")
    lines.append("- Aggregated static bias/scatter from the WHY #7 per-position table.")
    lines.append("- Read raw ROTO `tr_all.csv` per-anchor ranges and computed `measured - geometric truth` residuals at the fixed validated ROTO beta.")
    lines.append("- Also computed model-corrected residuals `measured - geometry - d_anchor - tag_delay`; the uniformity gate uses this corrected quantity because the question is per-tag delay, not anchor-delay pattern.")
    lines.append("- Re-solved ROTO after subtracting per-tag constant bias only if the residual gate passed.")
    lines.append("")
    lines.append("**Numbers computed.**")
    lines.append("")
    lines.append(
        f"- Static bias/scatter split, self-cal: bias 3D/XZ/Y medians "
        f"{fmt(self_bias['static_bias_3d_median_mm'])} / {fmt(self_bias['static_bias_xz_median_mm'])} / "
        f"{fmt(self_bias['static_bias_y_median_mm'])} mm; scatter RMS 3D/XZ/Y medians "
        f"{fmt(self_bias['static_scatter_3d_rms_median_mm'])} / {fmt(self_bias['static_scatter_xz_rms_median_mm'])} / "
        f"{fmt(self_bias['static_scatter_y_rms_median_mm'])} mm."
    )
    lines.append(
        f"- BS2DCE model-corrected range residual median/IQR: "
        f"{fmt(self_tag_delay['BS2DCE_overall_model_corrected_median_mm'])} / "
        f"{fmt(self_tag_delay['BS2DCE_anchor_iqr_mm'])} mm; uniform gate={self_tag_delay['BS2DCE_uniform_gate']}."
    )
    lines.append(
        f"- BSDC91 model-corrected range residual median/IQR: "
        f"{fmt(self_tag_delay['BSDC91_overall_model_corrected_median_mm'])} / "
        f"{fmt(self_tag_delay['BSDC91_anchor_iqr_mm'])} mm; uniform gate={self_tag_delay['BSDC91_uniform_gate']}."
    )
    if math.isfinite(float(self_tag_delay["after_dynamic_excess_p50_3d_mm"])):
        lines.append(
            f"- Bias-removal re-solve, self-cal: dynamic excess 3D P50 "
            f"{fmt(self_tag_delay['before_dynamic_excess_p50_3d_mm'])} -> "
            f"{fmt(self_tag_delay['after_dynamic_excess_p50_3d_mm'])} mm; GDOP-bin gap "
            f"{fmt(self_tag_delay['before_gdop_median_gap_p50_3d_mm'])} -> "
            f"{fmt(self_tag_delay['after_gdop_median_gap_p50_3d_mm'])} mm."
        )
    else:
        lines.append("- Bias-removal re-solve skipped because the uniform delay gate failed.")
    lines.append(
        f"- GDOP overlap, self-cal: static P5/P50/P95 {fmt(self_gdop_overlap['static_gdop_p5'], 3)} / "
        f"{fmt(self_gdop_overlap['static_gdop_p50'], 3)} / {fmt(self_gdop_overlap['static_gdop_p95'], 3)}; "
        f"ROTO P5/P50/P95 {fmt(self_gdop_overlap['roto_gdop_p5'], 3)} / "
        f"{fmt(self_gdop_overlap['roto_gdop_p50'], 3)} / {fmt(self_gdop_overlap['roto_gdop_p95'], 3)}."
    )
    lines.append(
        f"- Shared GDOP bins used by WHY #7: {len(self_shared_gdop_bins)}; thin bins with either side n<30: "
        f"{thin_self_gdop_bins}; gating parity: {self_gdop_overlap['gating_parity']}."
    )
    lines.append("")
    lines.append(f"**Verdict.** `{self_tag_delay['tag_delay_verdict']}`.")
    lines.append("")
    lines.append(f"**Consequence for the paper.** {self_tag_delay['paper_consequence']}")
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** The per-tag bias estimate is diagnostic because it uses Vicon truth here. "
        "Unlike post-hoc rigid fitting, however, a constant per-tag residual delay is deployable as a firmware antenna-delay trim if confirmed on an independent known baseline."
    )
    lines.append("")
    lines.append("## WHY #9: Raw Tag × Anchor Residual Decomposition")
    lines.append("")
    lines.append("**Terminology.**")
    lines.append("")
    lines.append(
        f"- The firmware antenna-delay setting is `{FIRMWARE_ANTENNA_DELAY_DTU}` DTU, TX=RX on all devices, with no OTP antenna-delay read in this firmware."
    )
    lines.append(
        "- Solver `d_anchor_mm` and `tag_delay_mm` are layout-level residual delay corrections: software terms fitted on top of data already generated with the firmware-16436 setting."
    )
    lines.append(
        "- In this decomposition, `grand` is common-mode firmware-16436 miscalibration plus global scale plus mean-coordinate gauge. Only tag/anchor differences are identifiable."
    )
    lines.append("")
    lines.append("**Tests run.**")
    lines.append("")
    lines.append(
        "- Built a raw `measured - geometric truth` median table over three tags and eight anchors for both self-cal and Vicon-truth scenarios."
    )
    lines.append(
        "- Ran NaN-aware median polish to estimate `grand`, `tag_main`, `anchor_main`, and tag-by-anchor interaction without subtracting solver `d_anchor_mm` or `tag_delay_mm`."
    )
    lines.append(
        "- Checked cross-scenario stability of pairwise `tag_main` differences, then disambiguated the anchor flag with within-scenario `anchor_main` versus each scenario's own `d_anchor_mm`."
    )
    lines.append("")
    lines.append("**Numbers computed.**")
    lines.append("")
    lines.append(
        f"- Self-cal grand common-mode: {fmt(why9_self['grand_common_mode_mm'])} mm; "
        f"Vicon-truth grand common-mode: {fmt(why9_vicon['grand_common_mode_mm'])} mm."
    )
    lines.append(
        f"- Self-cal interaction median/max abs: {fmt(why9_self['interaction_median_abs_mm'])} / "
        f"{fmt(why9_self['interaction_max_abs_mm'])} mm; Vicon-truth: "
        f"{fmt(why9_vicon['interaction_median_abs_mm'])} / {fmt(why9_vicon['interaction_max_abs_mm'])} mm."
    )
    for left, right in WHY9_PAIRWISE_TAG_DIFFS:
        key = why9_pair_key(left, right)
        delta_key = f"cross_scenario_abs_delta_{key}"
        lines.append(
            f"- `{left} - {right}` tag_main difference: self-cal {fmt(why9_self_stability[key])} mm; "
            f"Vicon-truth {fmt(why9_vicon_stability[key])} mm; cross-scenario |delta| "
            f"{fmt(why9_self_stability[delta_key])} mm."
        )
    lines.append(
        f"- Anchor consistency against solver `d_anchor_mm` relative to A: verdict "
        f"`{why9_anchor_cross_a['anchor_consistency_verdict']}`, correlation "
        f"{fmt(why9_anchor_cross_a['anchor_consistency_corr'], 2)}, median absolute gap "
        f"{fmt(why9_anchor_cross_a['anchor_consistency_median_abs_gap_mm'])} mm."
    )
    lines.append(
        f"- Within-scenario anchor check, self-cal: corr {fmt(why9_anchor_within_self_a['anchor_consistency_corr'], 2)}, "
        f"median abs gap {fmt(why9_anchor_within_self_a['anchor_consistency_median_abs_gap_mm'])} mm; "
        f"Vicon-truth: corr {fmt(why9_anchor_within_vicon_a['anchor_consistency_corr'], 2)}, "
        f"median abs gap {fmt(why9_anchor_within_vicon_a['anchor_consistency_median_abs_gap_mm'])} mm."
    )
    lines.append(
        f"- Anchor convention/sign check: both scenarios use `rel_A = value - value[A]`; direct `d_anchor` sign is better than negated sign "
        f"(self corr {fmt(why9_anchor_overall.get('within_self_cal_corr', float('nan')), 2)} vs "
        f"{fmt(why9_anchor_overall.get('within_self_cal_negated_corr', float('nan')), 2)}, Vicon corr "
        f"{fmt(why9_anchor_overall.get('within_vicon_corr', float('nan')), 2)} vs "
        f"{fmt(why9_anchor_overall.get('within_vicon_negated_corr', float('nan')), 2)})."
    )
    lines.append(
        f"- Coordinate/scale absorption signature, self `anchor_main_rel_A` minus Vicon `anchor_main_rel_A`: "
        f"median abs {fmt(why9_anchor_coord_a['anchor_consistency_median_abs_gap_mm'])} mm; B/C/D "
        f"{fmt(why9_anchor_coord_by_label['B']['coord_scale_error_self_minus_vicon_mm'])} / "
        f"{fmt(why9_anchor_coord_by_label['C']['coord_scale_error_self_minus_vicon_mm'])} / "
        f"{fmt(why9_anchor_coord_by_label['D']['coord_scale_error_self_minus_vicon_mm'])} mm."
    )
    lines.append(
        f"- Layout absorption regression: self-minus-Vicon `anchor_main_rel_A` versus v4-io radial layout error gives "
        f"R2 {fmt(why9_anchor_overall.get('layout_absorption_radial_r2', float('nan')), 3)} and slope "
        f"{fmt(why9_anchor_overall.get('layout_absorption_radial_slope_mm_per_mm', float('nan')), 2)} mm/mm; "
        f"against 3D layout error magnitude R2 is "
        f"{fmt(why9_anchor_overall.get('layout_absorption_3d_r2', float('nan')), 3)}."
    )
    lines.append(
        f"- Vicon `anchor_main` relative to A for B/C/D: "
        f"{fmt(why9_anchor_cross_by_label['B']['anchor_main_rel_A_mm'])} / "
        f"{fmt(why9_anchor_cross_by_label['C']['anchor_main_rel_A_mm'])} / "
        f"{fmt(why9_anchor_cross_by_label['D']['anchor_main_rel_A_mm'])} mm; "
        "solver `d_anchor_mm` relative to A for B/C/D: "
        f"{fmt(why9_anchor_cross_by_label['B']['solver_d_anchor_rel_A_mm'])} / "
        f"{fmt(why9_anchor_cross_by_label['C']['solver_d_anchor_rel_A_mm'])} / "
        f"{fmt(why9_anchor_cross_by_label['D']['solver_d_anchor_rel_A_mm'])} mm."
    )
    lines.append(f"- Anchor disambiguation verdict: `{why9_anchor_overall['anchor_decomp_verdict']}`.")
    lines.append("")
    lines.append(f"**Verdict.** `{why9_verdict_summary['why9_verdict']}`.")
    lines.append("")
    lines.append(
        f"**Consequence for the paper.** {why9_verdict_summary['paper_consequence']} "
        f"{why9_anchor_overall['paper_consequence']}"
    )
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** Quote only differences. For tags, use pairwise `tag_main` differences; for anchors, use `anchor_main` relative to A and carry the "
        "anchor-consistency verdict with it. Absolute per-anchor or per-tag delay still requires a known baseline or inter-anchor ranging."
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
        f"- Legacy production mean-aggregated static point v4-io/T1: P50 "
        f"{fmt(prod_summary['legacy_production_T1_mean_p50_mm'])} / "
        f"P95 {fmt(prod_summary['legacy_production_T1_mean_p95_mm'])} mm, "
        f"RMSE {fmt(prod_summary['legacy_production_T1_mean_rmse_mm'])} mm."
    )
    lines.append(
        f"- Real production mean-aggregated static point v4-io/T4: P50 "
        f"{fmt(prod_summary['production_T4_mean_p50_mm'])} / "
        f"P95 {fmt(prod_summary['production_T4_mean_p95_mm'])} mm, "
        f"RMSE {fmt(prod_summary['production_T4_mean_rmse_mm'])} mm."
    )
    lines.append(
        f"- Median-estimator ablation v4-io/T4: P50 {fmt(prod_summary['raw_T4_median_p50_mm'])} / "
        f"P95 {fmt(prod_summary['raw_T4_p95_mm'])} mm, "
        f"RMSE {fmt(prod_summary['raw_T4_median_rmse_mm'])} mm."
    )
    lines.append(
        f"- The legacy T1 production minus median-estimator T4 P95 gap was "
        f"{fmt(prod_summary['legacy_production_T1_mean_minus_raw_T4_p95_mm'])} mm."
    )
    lines.append("")
    lines.append(
        "**Verdict.** The old production export tracked the T1/T2-class tail because the real production path used the T1-style solver. "
        "After switching the real production export to T4 while keeping production mean aggregation, the deployed static headline becomes the T4 mean row above."
    )
    lines.append("")
    lines.append(
        "**Reviewer-survivability.** A paper can report both, but it must define them cleanly: production mean-aggregated static point versus median-estimator ablation. "
        "Do not call 69.7/173.9 the deployed static number unless production also switches from mean aggregation to the median static-point estimator."
    )
    lines.append("")
    lines.append("## Report Coverage Check")
    lines.append("")
    lines.append(
        "**Reviewer-audit coverage.** This report now covers every table generated under `reviewer_audit/tables`: "
        "WHY #1/#2 dynamic time, rigid, and circle metrics; WHY #3 one-baseline LOOCV; WHY #4 Procrustes scale; "
        "WHY #5 production-vs-raw; WHY #6 clock skew; WHY #7 single-shot/GDOP/static-bias decomposition; "
        "WHY #8 bias/scatter, tag-delay, and GDOP-overlap checks; WHY #9 raw tag-by-anchor residual decomposition; "
        "WHY #10 ROTO post-solve dynamic filtering; and WHY #11 lever-armed pseudo-IMU motion-prior replay. "
        "The separate `resilience_gap_audit` adds raw-pair bootstrap numerical precision, delay-bootstrap SD, and synthetic dropout stress for 4x FULL."
    )
    lines.append("")
    lines.append(
        "**What is summarized rather than printed row-by-row.** Large per-capture, per-anchor, per-tag, and full solver-matrix tables are intentionally indexed in Output Tables rather than expanded in text. "
        "They are comparison evidence, but not headline claims."
    )
    lines.append("")
    lines.append(
        "**Separate FULL diagnostics.** The broader FULL directory also contains DOP grids, Monte Carlo keep-k/drop-anchor runs, temporal drift checks, pair residual diagnostics, and anchor-health scorecards. "
        "Those are not missing from this reviewer audit; they are robustness/sensor-health appendices and should be pulled into the paper only if a reviewer asks about geometry sensitivity, anchor removal, or acquisition drift."
    )
    lines.append("")
    lines.append(
        "**Paper reporting checklist.** The separate `reporting_checklist` audit now maps the requested reporting structure onto the FULL outputs. "
        "It splits anchor absolute error, anchor repeatability, scale bias, Sim(3) shape distortion, delay-layout coupling, static tag error, dynamic tag ATE/RPE, and missing robustness evidence. "
        "Raw-pair bootstrap and delay-bootstrap SD are now labeled numerical precision rather than repeatability; synthetic dropout stress covers the feasible stress diagnostics. "
        "The remaining true gaps are independent repeated AutoPos deployments, a PANS/manual baseline, explicit CIR/NLOS labels, and raw dynamic ROTO range re-solve or physical packet-loss stress sweeps."
    )
    lines.append("")
    lines.append("## Headline Recommendation")
    lines.append("")
    lines.append(
        "For the paper headline, use the real production mean-aggregated static point `v4-io/T4`: "
        "`72.7 mm P50 / 171.5 mm P95 / 109.8 mm RMSE` 3D, with XY/Z split reported separately. "
        "Report `69.7 / 173.9` as a median-estimator ablation and `74.0 / 282.1` as the legacy T1 production-output ablation. "
        "For dynamic ROTO, the honest claim is `about 105.8 mm P50 / 231.8 mm P95` absolute 3D for self-cal v4-io/T4, "
        "and `105.6 mm P50 / 200.4 mm P95` for Vicon-truth+delaycal; this shows ROTO absolute error is not primarily a layout-calibration issue. "
        "Report ROTO filtering separately: fixed-lag F4 can reach about "
        f"`{fmt(filt_self_f4['trackmedian_err3d_p50_mm'])} / {fmt(filt_self_f4['trackmedian_err3d_p95_mm'])} mm` "
        "on self-cal FULL, but it is a trajectory-filter/latency ablation, not the calibration-level dynamic claim. "
        "Report pseudo-IMU replay as an oracle upper bound: a correctly lever-armed motion prior can reach "
        f"`{fmt(pseudo_self_pi1['trackmedian_err3d_p50_mm'])} / {fmt(pseudo_self_pi1['trackmedian_err3d_p95_mm'])} mm` "
        "causally on self-cal FULL, but it is not a real IMU deployment claim. "
        "Demote similarity-scale, one-baseline-best, offline F5/PI4 smoothing, pseudo-IMU oracle replay, and per-capture post-hoc rigid results to diagnostic/ablation status."
    )
    lines.append("")
    lines.append("## Output Tables")
    lines.append("")
    for name in [
        "why1_dynamic_error_budget.csv",
        "why2_time_offset_refit.csv",
        "why2_posthoc_rigid_per_capture.csv",
        "why2_roto_refit_summary.csv",
        "why2_roto_circle_metrics.csv",
        "why10_roto_filtered_summary.csv",
        "why10_roto_filtered_per_track.csv",
        "why11_roto_pseudo_imu_summary.csv",
        "why11_roto_pseudo_imu_per_track.csv",
        "why11_roto_pseudo_imu_extrinsics.csv",
        "why3_one_baseline_loocv.csv",
        "why3_one_baseline_cv_summary.csv",
        "why4_procrustes_check.csv",
        "why5_production_vs_raw_methods.csv",
        "why5_production_T4_real_run_summary.csv",
        "why6_time_skew_per_capture.csv",
        "why6_time_skew_summary.csv",
        "why7_single_shot_summary.csv",
        "why7_error_vs_gdop.csv",
        "why7_static_bias_scatter.csv",
        "why8_bias_scatter_summary.csv",
        "why8_tag_range_residuals.csv",
        "why8_tag_delay_resolve_summary.csv",
        "why8_gdop_overlap.csv",
        "why9_residual_cells.csv",
        "why9_twoway_effects.csv",
        "why9_stability_summary.csv",
        "why9_anchor_consistency.csv",
    ]:
        lines.append(f"- `../tables/{name}`")
    for name in [
        "checklist_anchor_layout_absolute.csv",
        "checklist_anchor_repeatability.csv",
        "checklist_tag_static.csv",
        "checklist_tag_dynamic.csv",
        "checklist_ablation.csv",
        "checklist_coverage.csv",
    ]:
        lines.append(f"- `../../reporting_checklist/tables/{name}`")
    lines.append("- `../../reporting_checklist/reports/REPORTING_CHECKLIST_AUDIT.md`")
    lines.append("- `../../resilience_gap_audit/tables/bootstrap_layout_repeatability.csv`")
    lines.append("- `../../resilience_gap_audit/tables/bootstrap_delay_sd.csv`")
    lines.append("- `../../resilience_gap_audit/tables/static_dropout_stress_summary.csv`")
    lines.append("- `../../resilience_gap_audit/tables/roto_sample_dropout_stress_summary.csv`")
    lines.append("- `../../resilience_gap_audit/reports/RESILIENCE_GAP_AUDIT.md`")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reviewer-style metrology audit for the FULL AutoPos/Vicon analysis."
    )
    parser.add_argument(
        "--only",
        choices=["all", "why9", "center-rms", "circle-metrics", "roto-filtered", "pseudo-imu", "report"],
        default="all",
        help=(
            "Run only one audit section. Use why9 to resume the raw tag x anchor residual decomposition; "
            "use circle-metrics/center-rms for ROTO circle self-consistency and absolute center metrics; "
            "use roto-filtered for post-solve ROTO trajectory filter replay; "
            "use pseudo-imu for OptiTrack-derived lever-armed pseudo-IMU replay; "
            "use report to rebuild markdown from existing CSV tables."
        ),
    )
    return parser.parse_args(argv)


def write_why9_tables(
    cell_rows: list[dict],
    effect_rows: list[dict],
    stability_rows: list[dict],
    anchor_consistency_rows: list[dict],
) -> None:
    write_csv(TABLE_DIR / "why9_residual_cells.csv", cell_rows)
    write_csv(TABLE_DIR / "why9_twoway_effects.csv", effect_rows)
    write_csv(TABLE_DIR / "why9_stability_summary.csv", stability_rows)
    write_csv(TABLE_DIR / "why9_anchor_consistency.csv", anchor_consistency_rows)


def print_why9_summary(
    why9_effect_rows: list[dict],
    why9_stability_rows: list[dict],
    why9_anchor_consistency_rows: list[dict],
    why9_verdict_summary: dict,
) -> None:
    why9_self = next(r for r in why9_effect_rows if r["scenario"] == "self_cal_v4io_T4")
    why9_vicon = next(r for r in why9_effect_rows if r["scenario"] == "vicon_truth_delaycal_T4")
    why9_scen = {str(r["scenario"]): r for r in why9_stability_rows if r.get("row_type") == "scenario"}
    why9_anchor_by_check = {
        (str(r.get("check_type")), str(r.get("anchor"))): r
        for r in why9_anchor_consistency_rows
    }
    why9_anchor_cross_a = why9_anchor_by_check[("cross_vicon_main_vs_self_danchor", "A")]
    why9_anchor_within_self_a = why9_anchor_by_check[("within_self_cal", "A")]
    why9_anchor_within_vicon_a = why9_anchor_by_check[("within_vicon", "A")]
    why9_anchor_coord_a = why9_anchor_by_check[("coord_scale_error", "A")]
    why9_anchor_overall = why9_anchor_by_check[("overall", "ALL")]
    print(
        "WHY #9 two-way verdict: "
        f"{why9_verdict_summary['why9_verdict']} | "
        f"grand self/vicon={fmt(why9_self['grand_common_mode_mm'])}/"
        f"{fmt(why9_vicon['grand_common_mode_mm'])} mm | "
        f"interaction median self/vicon={fmt(why9_self['interaction_median_abs_mm'])}/"
        f"{fmt(why9_vicon['interaction_median_abs_mm'])} mm"
    )
    for left, right in WHY9_PAIRWISE_TAG_DIFFS:
        key = why9_pair_key(left, right)
        delta_key = f"cross_scenario_abs_delta_{key}"
        print(
            "WHY #9 tag diff: "
            f"{left}-{right} self/vicon/delta="
            f"{fmt(why9_scen['self_cal_v4io_T4'][key])}/"
            f"{fmt(why9_scen['vicon_truth_delaycal_T4'][key])}/"
            f"{fmt(why9_scen['self_cal_v4io_T4'][delta_key])} mm"
        )
    print(
        "WHY #9 anchor consistency: "
        f"cross={why9_anchor_cross_a['anchor_consistency_verdict']} "
        f"corr/gap={fmt(why9_anchor_cross_a['anchor_consistency_corr'], 2)}/"
        f"{fmt(why9_anchor_cross_a['anchor_consistency_median_abs_gap_mm'])} mm | "
        f"within_self corr/gap={fmt(why9_anchor_within_self_a['anchor_consistency_corr'], 2)}/"
        f"{fmt(why9_anchor_within_self_a['anchor_consistency_median_abs_gap_mm'])} mm | "
        f"within_vicon corr/gap={fmt(why9_anchor_within_vicon_a['anchor_consistency_corr'], 2)}/"
        f"{fmt(why9_anchor_within_vicon_a['anchor_consistency_median_abs_gap_mm'])} mm | "
        f"coord_scale_median_abs={fmt(why9_anchor_coord_a['anchor_consistency_median_abs_gap_mm'])} mm"
    )
    print(
        "WHY #9 anchor disambiguation: "
        f"{why9_anchor_overall['anchor_decomp_verdict']} | "
        f"radial_layout_R2/slope={fmt(why9_anchor_overall.get('layout_absorption_radial_r2', float('nan')), 3)}/"
        f"{fmt(why9_anchor_overall.get('layout_absorption_radial_slope_mm_per_mm', float('nan')), 2)} | "
        f"layout3d_R2={fmt(why9_anchor_overall.get('layout_absorption_3d_r2', float('nan')), 3)} | "
        f"no_sign_flip={why9_anchor_overall.get('no_sign_flip_detected', '')}"
    )
    vicon_main = []
    self_d = []
    for label in ANCHOR_LABELS:
        row = why9_anchor_by_check[("cross_vicon_main_vs_self_danchor", label)]
        vicon_main.append(f"{label}:{fmt(row['anchor_main_rel_A_mm'])}")
        self_d.append(f"{label}:{fmt(row['solver_d_anchor_rel_A_mm'])}")
    print(
        "WHY #9 anchor side-by-side: "
        f"vicon_anchor_main_rel_A=[{', '.join(vicon_main)}] | "
        f"self_d_anchor_rel_A=[{', '.join(self_d)}]"
    )


def run_why9_only() -> None:
    print("WHY #9 only: collecting raw residuals and running tag x anchor decomposition...", flush=True)
    why9_cell_rows, why9_effect_rows, why9_stability_rows, why9_anchor_consistency_rows, why9_verdict_summary = (
        audit_why9_twoway_range_decomposition()
    )
    write_why9_tables(why9_cell_rows, why9_effect_rows, why9_stability_rows, why9_anchor_consistency_rows)
    print_why9_summary(
        why9_effect_rows,
        why9_stability_rows,
        why9_anchor_consistency_rows,
        why9_verdict_summary,
    )
    print(f"Wrote {TABLE_DIR / 'why9_anchor_consistency.csv'}")


def run_center_rms_only() -> None:
    rows = audit_roto_circle_metrics()
    write_csv(TABLE_DIR / "why2_roto_circle_metrics.csv", rows)
    print("ROTO circle metrics:", flush=True)
    for row in rows:
        print(
            f"  {row['case']}: "
            f"legacy dR RMS={fmt(row['legacy_deltaR_error_rms_mm'])} mm, "
            f"legacy turn-center med={fmt(row['legacy_turn_center_rms_median_mm'])} mm, "
            f"Opti center RMS={fmt(row['opti_turn_center_abs_error_3d_rms_mm'])} mm "
            f"(n={row['n_tracks']})"
        )
    print(f"Wrote {TABLE_DIR / 'why2_roto_circle_metrics.csv'}")


def run_roto_filtered_only() -> None:
    summary_rows, per_track_rows = audit_roto_filtered_replay()
    write_csv(TABLE_DIR / "why10_roto_filtered_summary.csv", summary_rows)
    write_csv(TABLE_DIR / "why10_roto_filtered_per_track.csv", per_track_rows)
    print("ROTO filtered replay:", flush=True)
    for row in sorted(summary_rows, key=lambda r: (str(r["case"]), float(r["trackmedian_err3d_p50_mm"]))):
        print(
            f"  {row['case']} {row['filter_id']}: "
            f"track P50/P95={fmt(row['trackmedian_err3d_p50_mm'])}/"
            f"{fmt(row['trackmedian_err3d_p95_mm'])} mm, "
            f"gain={fmt(row['improvement_vs_F0_trackmedian_err3d_p50_mm'])} mm, "
            f"verdict={row['filter_verdict']}"
        )
    print(f"Wrote {TABLE_DIR / 'why10_roto_filtered_summary.csv'}")


def run_pseudo_imu_only() -> None:
    summary_rows, per_track_rows, extrinsic_rows = audit_roto_pseudo_imu_replay()
    write_csv(TABLE_DIR / "why11_roto_pseudo_imu_summary.csv", summary_rows)
    write_csv(TABLE_DIR / "why11_roto_pseudo_imu_per_track.csv", per_track_rows)
    write_csv(TABLE_DIR / "why11_roto_pseudo_imu_extrinsics.csv", extrinsic_rows)
    print("ROTO pseudo-IMU replay:", flush=True)
    for row in sorted(summary_rows, key=lambda r: (str(r["case"]), str(r["fusion_id"]))):
        print(
            f"  {row['case']} {row['fusion_id']}: "
            f"track P50/P95={fmt(row['trackmedian_err3d_p50_mm'])}/"
            f"{fmt(row['trackmedian_err3d_p95_mm'])} mm, "
            f"gain={fmt(row['improvement_vs_PI0_trackmedian_err3d_p50_mm'])} mm, "
            f"verdict={row['fusion_verdict']}"
        )
    print(f"Wrote {TABLE_DIR / 'why11_roto_pseudo_imu_summary.csv'}")


def read_table_rows(name: str) -> list[dict]:
    path = TABLE_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"missing required report table: {path}")
    return pd.read_csv(path).to_dict("records")


def read_table_row(name: str) -> dict:
    rows = read_table_rows(name)
    if not rows:
        raise RuntimeError(f"required report table is empty: {TABLE_DIR / name}")
    return rows[0]


def run_report_only() -> None:
    _prod_rows, prod_summary = audit_production_vs_raw()
    why9_stability_rows = read_table_rows("why9_stability_summary.csv")
    why9_verdict_summary = next(
        r for r in why9_stability_rows
        if r.get("row_type") == "overall" and r.get("scenario") == "cross_scenario"
    )
    report = build_report(
        read_table_rows("why2_roto_refit_summary.csv"),
        read_table_row("why1_dynamic_error_budget.csv"),
        read_table_rows("why2_roto_circle_metrics.csv"),
        read_table_rows("why10_roto_filtered_summary.csv"),
        read_table_rows("why11_roto_pseudo_imu_summary.csv"),
        read_table_rows("why11_roto_pseudo_imu_extrinsics.csv"),
        read_table_rows("why6_time_skew_summary.csv"),
        read_table_rows("why7_single_shot_summary.csv"),
        read_table_rows("why8_bias_scatter_summary.csv"),
        read_table_rows("why8_tag_delay_resolve_summary.csv"),
        read_table_rows("why8_gdop_overlap.csv"),
        read_table_rows("why9_twoway_effects.csv"),
        why9_stability_rows,
        read_table_rows("why9_anchor_consistency.csv"),
        why9_verdict_summary,
        read_table_row("why3_one_baseline_cv_summary.csv"),
        read_table_rows("why4_procrustes_check.csv"),
        prod_summary,
    )
    out = REPORT_DIR / "MEASUREMENT_REVIEWER_AUDIT.md"
    out.write_text(report, encoding="utf-8")
    print(f"Wrote {out}")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    if args.only == "why9":
        run_why9_only()
        return
    if args.only in {"center-rms", "circle-metrics"}:
        run_center_rms_only()
        return
    if args.only == "roto-filtered":
        run_roto_filtered_only()
        return
    if args.only == "pseudo-imu":
        run_pseudo_imu_only()
        return
    if args.only == "report":
        run_report_only()
        return

    time_rows, rigid_rows, roto_summary, dynamic_budget = audit_roto_time_and_rigid()
    center_rms_rows = audit_roto_circle_metrics()
    roto_filtered_summary, roto_filtered_per_track = audit_roto_filtered_replay()
    roto_pseudo_summary, roto_pseudo_per_track, roto_pseudo_extrinsics = audit_roto_pseudo_imu_replay()
    skew_rows, skew_summary = audit_roto_time_skew(time_rows)
    single_shot_summary, gdop_rows, static_bias_rows = audit_single_shot_decomposition()
    bias_scatter_summary, tag_range_rows, tag_delay_summary, gdop_overlap_rows = audit_tag_delay_and_overlap(
        single_shot_summary,
        gdop_rows,
        static_bias_rows,
    )
    why9_cell_rows, why9_effect_rows, why9_stability_rows, why9_anchor_consistency_rows, why9_verdict_summary = (
        audit_why9_twoway_range_decomposition()
    )
    cv_rows, cv_summary = audit_one_baseline_cv()
    procrustes_rows = audit_procrustes()
    prod_rows, prod_summary = audit_production_vs_raw()

    write_csv(TABLE_DIR / "why2_time_offset_refit.csv", time_rows)
    write_csv(TABLE_DIR / "why2_posthoc_rigid_per_capture.csv", rigid_rows)
    write_csv(TABLE_DIR / "why2_roto_refit_summary.csv", roto_summary)
    write_csv(TABLE_DIR / "why2_roto_circle_metrics.csv", center_rms_rows)
    write_csv(TABLE_DIR / "why10_roto_filtered_summary.csv", roto_filtered_summary)
    write_csv(TABLE_DIR / "why10_roto_filtered_per_track.csv", roto_filtered_per_track)
    write_csv(TABLE_DIR / "why11_roto_pseudo_imu_summary.csv", roto_pseudo_summary)
    write_csv(TABLE_DIR / "why11_roto_pseudo_imu_per_track.csv", roto_pseudo_per_track)
    write_csv(TABLE_DIR / "why11_roto_pseudo_imu_extrinsics.csv", roto_pseudo_extrinsics)
    write_csv(TABLE_DIR / "why1_dynamic_error_budget.csv", [dynamic_budget])
    write_csv(TABLE_DIR / "why3_one_baseline_loocv.csv", cv_rows)
    write_csv(TABLE_DIR / "why3_one_baseline_cv_summary.csv", [cv_summary])
    write_csv(TABLE_DIR / "why4_procrustes_check.csv", procrustes_rows)
    write_csv(TABLE_DIR / "why5_production_vs_raw_methods.csv", prod_rows)
    write_csv(TABLE_DIR / "why5_production_T4_real_run_summary.csv", production_t4_real_run_rows(prod_summary))
    write_csv(TABLE_DIR / "why6_time_skew_per_capture.csv", skew_rows)
    write_csv(TABLE_DIR / "why6_time_skew_summary.csv", skew_summary)
    write_csv(TABLE_DIR / "why7_single_shot_summary.csv", single_shot_summary)
    write_csv(TABLE_DIR / "why7_error_vs_gdop.csv", gdop_rows)
    write_csv(TABLE_DIR / "why7_static_bias_scatter.csv", static_bias_rows)
    write_csv(TABLE_DIR / "why8_bias_scatter_summary.csv", bias_scatter_summary)
    write_csv(TABLE_DIR / "why8_tag_range_residuals.csv", tag_range_rows)
    write_csv(TABLE_DIR / "why8_tag_delay_resolve_summary.csv", tag_delay_summary)
    write_csv(TABLE_DIR / "why8_gdop_overlap.csv", gdop_overlap_rows)
    write_csv(TABLE_DIR / "why9_residual_cells.csv", why9_cell_rows)
    write_csv(TABLE_DIR / "why9_twoway_effects.csv", why9_effect_rows)
    write_csv(TABLE_DIR / "why9_stability_summary.csv", why9_stability_rows)
    write_csv(TABLE_DIR / "why9_anchor_consistency.csv", why9_anchor_consistency_rows)

    report = build_report(
        roto_summary,
        dynamic_budget,
        center_rms_rows,
        roto_filtered_summary,
        roto_pseudo_summary,
        roto_pseudo_extrinsics,
        skew_summary,
        single_shot_summary,
        bias_scatter_summary,
        tag_delay_summary,
        gdop_overlap_rows,
        why9_effect_rows,
        why9_stability_rows,
        why9_anchor_consistency_rows,
        why9_verdict_summary,
        cv_summary,
        procrustes_rows,
        prod_summary,
    )
    (REPORT_DIR / "MEASUREMENT_REVIEWER_AUDIT.md").write_text(report, encoding="utf-8")
    self_skew = next(r for r in skew_summary if r["scenario"] == "self_cal_v4io_T4")
    self_single = next(r for r in single_shot_summary if r["scenario"] == "self_cal_v4io_T4")
    self_bias = next(r for r in bias_scatter_summary if r["scenario"] == "self_cal_v4io_T4")
    self_tag_delay = next(r for r in tag_delay_summary if r["scenario"] == "self_cal_v4io_T4")
    why9_self = next(r for r in why9_effect_rows if r["scenario"] == "self_cal_v4io_T4")
    why9_vicon = next(r for r in why9_effect_rows if r["scenario"] == "vicon_truth_delaycal_T4")
    why9_scen = {str(r["scenario"]): r for r in why9_stability_rows if r.get("row_type") == "scenario"}
    why9_anchor_by_check = {
        (str(r.get("check_type")), str(r.get("anchor"))): r
        for r in why9_anchor_consistency_rows
    }
    why9_anchor_cross_a = why9_anchor_by_check[("cross_vicon_main_vs_self_danchor", "A")]
    why9_anchor_within_self_a = why9_anchor_by_check[("within_self_cal", "A")]
    why9_anchor_within_vicon_a = why9_anchor_by_check[("within_vicon", "A")]
    why9_anchor_coord_a = why9_anchor_by_check[("coord_scale_error", "A")]
    why9_anchor_overall = why9_anchor_by_check[("overall", "ALL")]
    print(
        "WHY #6 skew verdict: "
        f"{self_skew['skew_verdict']} | "
        f"P50 beta0/const/skew = {fmt(self_skew['current_beta0_samplepooled_p50_mm'])}/"
        f"{fmt(self_skew['const_offset_best_samplepooled_p50_mm'])}/"
        f"{fmt(self_skew['skew_best_samplepooled_p50_mm'])} mm | "
        f"alpha median±IQR = {fmt(self_skew['alpha_ppm_median'], 1)}±"
        f"{fmt(self_skew['alpha_ppm_iqr'], 1)} ppm | "
        f"sign consistency = {fmt(100.0 * self_skew['alpha_sign_consistency'], 1)}%"
    )
    print(f"WHY #6 consequence: {self_skew['paper_consequence']}")
    print(
        "WHY #7 single-shot verdict: "
        f"{self_single['verdict']} | "
        f"static single-shot P50={fmt(self_single['static_single_shot_p50_3d_mm'])} mm | "
        f"ROTO P50={fmt(self_single['roto_single_shot_p50_3d_mm'])} mm | "
        f"dynamic excess={fmt(self_single['dynamic_excess_p50_3d_mm'])} mm"
    )
    print(
        "WHY #8 bias/scatter: "
        f"bias3D={fmt(self_bias['static_bias_3d_median_mm'])} mm | "
        f"scatter3D={fmt(self_bias['static_scatter_3d_rms_median_mm'])} mm"
    )
    print(
        "WHY #8 tag-delay verdict: "
        f"{self_tag_delay['tag_delay_verdict']} | "
        f"BS2DCE median/IQR={fmt(self_tag_delay['BS2DCE_overall_model_corrected_median_mm'])}/"
        f"{fmt(self_tag_delay['BS2DCE_anchor_iqr_mm'])} mm | "
        f"BSDC91 median/IQR={fmt(self_tag_delay['BSDC91_overall_model_corrected_median_mm'])}/"
        f"{fmt(self_tag_delay['BSDC91_anchor_iqr_mm'])} mm"
    )
    print(
        "WHY #9 two-way verdict: "
        f"{why9_verdict_summary['why9_verdict']} | "
        f"grand self/vicon={fmt(why9_self['grand_common_mode_mm'])}/"
        f"{fmt(why9_vicon['grand_common_mode_mm'])} mm | "
        f"interaction median self/vicon={fmt(why9_self['interaction_median_abs_mm'])}/"
        f"{fmt(why9_vicon['interaction_median_abs_mm'])} mm"
    )
    for left, right in WHY9_PAIRWISE_TAG_DIFFS:
        key = why9_pair_key(left, right)
        delta_key = f"cross_scenario_abs_delta_{key}"
        print(
            "WHY #9 tag diff: "
            f"{left}-{right} self/vicon/delta="
            f"{fmt(why9_scen['self_cal_v4io_T4'][key])}/"
            f"{fmt(why9_scen['vicon_truth_delaycal_T4'][key])}/"
            f"{fmt(why9_scen['self_cal_v4io_T4'][delta_key])} mm"
        )
    print(
        "WHY #9 anchor consistency: "
        f"cross={why9_anchor_cross_a['anchor_consistency_verdict']} "
        f"corr/gap={fmt(why9_anchor_cross_a['anchor_consistency_corr'], 2)}/"
        f"{fmt(why9_anchor_cross_a['anchor_consistency_median_abs_gap_mm'])} mm | "
        f"within_self corr/gap={fmt(why9_anchor_within_self_a['anchor_consistency_corr'], 2)}/"
        f"{fmt(why9_anchor_within_self_a['anchor_consistency_median_abs_gap_mm'])} mm | "
        f"within_vicon corr/gap={fmt(why9_anchor_within_vicon_a['anchor_consistency_corr'], 2)}/"
        f"{fmt(why9_anchor_within_vicon_a['anchor_consistency_median_abs_gap_mm'])} mm | "
        f"coord_scale_median_abs={fmt(why9_anchor_coord_a['anchor_consistency_median_abs_gap_mm'])} mm"
    )
    print(f"WHY #9 anchor disambiguation: {why9_anchor_overall['anchor_decomp_verdict']}")
    vicon_main = []
    self_d = []
    for label in ANCHOR_LABELS:
        row = why9_anchor_by_check[("cross_vicon_main_vs_self_danchor", label)]
        vicon_main.append(f"{label}:{fmt(row['anchor_main_rel_A_mm'])}")
        self_d.append(f"{label}:{fmt(row['solver_d_anchor_rel_A_mm'])}")
    print(
        "WHY #9 anchor side-by-side: "
        f"vicon_anchor_main_rel_A=[{', '.join(vicon_main)}] | "
        f"self_d_anchor_rel_A=[{', '.join(self_d)}]"
    )
    print(f"Wrote {REPORT_DIR / 'MEASUREMENT_REVIEWER_AUDIT.md'}")


if __name__ == "__main__":
    main()
