"""Six-axis VQF execution, calibration, gap handling, and resampling."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import numpy as np

from .config import CALIBRATION_WINDOW_S, MAX_INTERPOLATION_GAP_S, NATIVE_RATE_HZ
from .decoder import physical
from .math3d import (apply_mounting, conjugate, mean, mounting_calibration,
                     multiply, normalize, resample_quaternions, sign_continuous)

VQF_SEARCH = (
    Path("/tmp/biospur_vqf_runtime"),
    Path("/tmp/biospur_phase3r3b_three_capture_closed_loop_20260822T144929Z/upstream/VQF"),
)


def load_vqf():
    try:
        return importlib.import_module("vqf")
    except ModuleNotFoundError:
        for path in VQF_SEARCH:
            if (path / "vqf/__init__.py").exists():
                sys.path.insert(0, str(path))
                return importlib.import_module("vqf")
    raise RuntimeError("verified local VQF runtime not found")


def session_times(rows: np.ndarray, anchor_us: int) -> np.ndarray:
    # Signed subtraction prevents uint underflow for the warm-up samples in the
    # first selected batch. No receipt/master time participates here.
    return (rows["timestamp_us"].astype(np.int64) - int(anchor_us)) * 1e-6


def select_stationary_window(streams: dict[str, np.ndarray], anchors: dict[str, int],
                             candidate_duration_s: float,
                             window_s: float = CALIBRATION_WINDOW_S) -> tuple[float, float, dict]:
    start_min = 0.5
    stop_max = max(start_min + window_s, candidate_duration_s - 0.5)
    candidates = np.arange(start_min, stop_max-window_s+1e-9, 0.25)
    if len(candidates) == 0:
        candidates = np.array([0.0])
    prepared = {}
    for node, rows in streams.items():
        t = session_times(rows, anchors[node])
        use = (t >= 0) & (t <= candidate_duration_s)
        acc, gyr = physical(rows[use])
        prepared[node] = (t[use], np.linalg.norm(acc, axis=1), np.linalg.norm(gyr, axis=1))
    scores = []
    for start in candidates:
        node_scores = []
        for node, (t, anorm, gnorm) in prepared.items():
            use = (t >= start) & (t < start+window_s)
            if np.sum(use) < int(0.8*window_s*NATIVE_RATE_HZ):
                node_scores.append(1e6); continue
            node_scores.append(float(np.sqrt(np.mean(gnorm[use]**2)) + 0.2*np.std(anorm[use])/9.80665))
        scores.append(float(np.median(node_scores)))
    index = int(np.argmin(scores)); start = float(candidates[index])
    return start, start+window_s, {
        "selection": "minimum median ten-node gyro RMS plus scaled acceleration-norm variation",
        "candidate_start_s": float(candidates[0]), "candidate_stop_s": float(stop_max),
        "window_s": window_s, "selected_score": scores[index],
        "candidate_count": len(candidates),
    }


def _strict_runs(t: np.ndarray, max_gap_s: float) -> list[slice]:
    split = np.flatnonzero((np.diff(t) <= 0) | (np.diff(t) > max_gap_s)) + 1
    bounds = np.r_[0, split, len(t)]
    return [slice(int(a), int(b)) for a, b in zip(bounds[:-1], bounds[1:]) if b-a >= 2]


def _uniform_inputs(t: np.ndarray, acc: np.ndarray, gyr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    step = 1.0/NATIVE_RATE_HZ
    grid = np.arange(t[0], t[-1] + 0.25*step, step)
    ai = np.column_stack([np.interp(grid, t, acc[:, k]) for k in range(3)])
    gi = np.column_stack([np.interp(grid, t, gyr[:, k]) for k in range(3)])
    return grid, np.ascontiguousarray(ai), np.ascontiguousarray(gi)


def filter_node(rows: np.ndarray, anchor_us: int, replay_grid: np.ndarray,
                calibration_window: tuple[float, float]) -> tuple[dict[str, np.ndarray], dict]:
    vqf_mod = load_vqf()
    t = session_times(rows, anchor_us); acc, gyr = physical(rows)
    finite = np.isfinite(t) & np.all(np.isfinite(acc), axis=1) & np.all(np.isfinite(gyr), axis=1)
    t, acc, gyr = t[finite], acc[finite], gyr[finite]
    runs = _strict_runs(t, MAX_INTERPOLATION_GAP_S)
    if not runs:
        raise RuntimeError("no continuous IMU run")
    c0, c1 = calibration_window
    init = (t >= c0) & (t < c1)
    if np.sum(init) < 20:
        raise RuntimeError("calibration window has insufficient samples")
    initial_bias = np.median(gyr[init], axis=0)

    all_t = []; all_q = []; reset_starts = []; reset_alignment = np.array([1., 0., 0., 0.])
    previous_q = None
    for run_index, run in enumerate(runs):
        tu, au, gu = _uniform_inputs(t[run], acc[run], gyr[run])
        filt = vqf_mod.VQF(1.0/NATIVE_RATE_HZ, motionBiasEstEnabled=True,
                           restBiasEstEnabled=True, magDistRejectionEnabled=False)
        filt.setBiasEstimate(np.ascontiguousarray(initial_bias), np.deg2rad(0.5))
        result = filt.updateBatch(gu, au, None)
        q = sign_continuous(np.asarray(result["quat6D"], float))
        if previous_q is not None:
            # A reset establishes a new VQF gauge. Preserve the prior global
            # gauge at the first post-gap sample; the gap itself stays invalid.
            reset_alignment = normalize(multiply(previous_q, conjugate(q[0])))
            q = normalize(multiply(reset_alignment, q))
        previous_q = q[-1].copy()
        all_t.append(tu); all_q.append(q); reset_starts.append(float(tu[0]))
    orientation_t = np.concatenate(all_t)
    orientation_q = np.concatenate(all_q)
    order = np.argsort(orientation_t, kind="stable")
    orientation_t, orientation_q = orientation_t[order], orientation_q[order]
    unique = np.r_[True, np.diff(orientation_t) > 1e-9]
    orientation_t, orientation_q = orientation_t[unique], orientation_q[unique]

    cal = (orientation_t >= c0) & (orientation_t < c1)
    q_gs_cal = mean(orientation_q[cal])
    q_sb = mounting_calibration(q_gs_cal)
    q_gb_native = apply_mounting(orientation_q, q_sb)
    q_gs, valid = resample_quaternions(orientation_t, orientation_q, replay_grid, MAX_INTERPOLATION_GAP_S)
    q_gb, valid_b = resample_quaternions(orientation_t, q_gb_native, replay_grid, MAX_INTERPOLATION_GAP_S)
    valid &= valid_b

    nearest_r = np.searchsorted(t, replay_grid)
    nearest_l = np.clip(nearest_r-1, 0, len(t)-1); nearest_r = np.clip(nearest_r, 0, len(t)-1)
    nearest_dt = np.minimum(np.abs(replay_grid-t[nearest_l]), np.abs(replay_grid-t[nearest_r]))
    confidence = np.where(valid, np.where(nearest_dt <= 0.0075, 1.0, 0.70), 0.0).astype(np.float32)
    reset = np.zeros(len(replay_grid), dtype=bool)
    for value in reset_starts:
        index = int(np.searchsorted(replay_grid, value))
        if 0 <= index < len(reset): reset[index] = True
    q_gs[~valid] = np.nan; q_gb[~valid] = np.nan

    seq = rows["sequence"].astype(np.uint32)
    seq_delta = (seq[1:] - seq[:-1]) & 0xFFFF
    native_dt = np.diff(t)
    gap_values = native_dt[(native_dt > 0.0075) | (native_dt <= 0)]
    diagnostics = {
        "samples": int(len(rows)), "finite_samples": int(np.sum(finite)),
        "first_timestamp_us": int(rows["timestamp_us"][0]),
        "last_timestamp_us": int(rows["timestamp_us"][-1]),
        "session_start_s": float(t[0]), "session_stop_s": float(t[-1]),
        "strict_runs": len(runs), "filter_resets": len(runs),
        "reset_starts_s": reset_starts,
        "initial_gyro_bias_rad_s": initial_bias.tolist(),
        "initial_gyro_bias_dps": np.rad2deg(initial_bias).tolist(),
        "q_GS_cal_wxyz": q_gs_cal.tolist(), "q_SB_wxyz": q_sb.tolist(),
        "calibration_equation": "q_SB = inverse(q_GS_cal) * q_GB_desired; q_GB(t) = q_GS(t) * q_SB",
        "desired_q_GB_cal_wxyz": [1.0, 0.0, 0.0, 0.0],
        "sequence_discontinuities": int(np.sum(seq_delta != 1)),
        "native_time_discontinuities": int(len(gap_values)),
        "longest_native_gap_s": float(np.max(native_dt)) if len(native_dt) else 0.0,
        "replay_valid_fraction": float(np.mean(valid)),
        "quaternion_norm_max_error": float(np.nanmax(np.abs(np.linalg.norm(q_gb, axis=1)-1))),
        "nonfinite_valid_quaternions": int(np.sum(valid & ~np.all(np.isfinite(q_gb), axis=1))),
        "orientation_filter": "VQF six-axis quat6D; no magnetometer",
    }
    return {"q_gs": q_gs.astype(np.float32), "q_gb": q_gb.astype(np.float32),
            "valid": valid, "confidence": confidence, "reset": reset}, diagnostics
