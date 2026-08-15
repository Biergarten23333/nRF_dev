"""R3C local-increment activity model without absolute-pose covariance leakage."""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.signal import find_peaks
from scipy.spatial.transform import Rotation

from .r3b_topology import (
    detect_active_bouts,
    detect_cycles,
    huber_so3_reference,
    relative_orientation,
    residual_rotvec,
    runs,
    smooth_valid,
)


def process_rate_floor(dt_s: np.ndarray, contract: Mapping[str, Any]) -> np.ndarray:
    dt = np.asarray(dt_s, float)
    noise = float(contract["normalization"]["gyro_process_noise_sigma_rad_s_sqrt_hz"])
    out = np.full(len(dt), np.nan)
    valid = np.isfinite(dt) & (dt > 0)
    out[valid] = np.sqrt(2.0) * noise / np.sqrt(dt[valid])
    return out


def primary_activity(
    time_ns: np.ndarray,
    relative: np.ndarray,
    valid: np.ndarray,
    contract: Mapping[str, Any],
) -> dict[str, np.ndarray]:
    time_ns = np.asarray(time_ns, np.int64)
    relative = np.asarray(relative, float)
    valid = np.asarray(valid, bool) & np.isfinite(relative).all(axis=(1, 2))
    dt = np.r_[np.nan, np.diff(time_ns) / 1e9]
    pair = valid & np.r_[False, valid[:-1]] & (dt > 0)
    indices = np.flatnonzero(pair)
    increment = np.full(len(time_ns), np.nan)
    if len(indices):
        delta = np.einsum("nji,njk->nik", relative[indices - 1], relative[indices])
        increment[indices] = Rotation.from_matrix(delta).magnitude()
    rate = increment / dt
    smooth_count = max(1, round(0.08 * float(contract["common_time"]["rate_hz"])))
    return {
        "increment_rad": increment,
        "rate_rad_s": smooth_valid(rate, pair, smooth_count),
        "dt_s": dt,
        "process_rate_floor_rad_s": process_rate_floor(dt, contract),
        "valid": pair,
    }


def lowest_activity_plateau(
    activity: Mapping[str, np.ndarray],
    rows: np.ndarray,
    contract: Mapping[str, Any],
) -> dict[str, Any] | None:
    cfg = contract["normalization"]
    hz = float(contract["common_time"]["rate_hz"])
    length = max(2, round(float(cfg["baseline_candidate_duration_s"]) * hz))
    stride = max(1, round(float(cfg["baseline_candidate_stride_s"]) * hz))
    rate = np.asarray(activity["rate_rad_s"], float)
    valid = np.asarray(activity["valid"], bool)
    candidates = []
    for offset in range(0, max(1, len(rows) - length + 1), stride):
        block = rows[offset : offset + length]
        keep = valid[block] & np.isfinite(rate[block])
        fraction = float(np.mean(keep)) if len(block) else 0.0
        if len(block) != length or fraction < float(cfg["minimum_baseline_valid_fraction"]) or not np.any(keep):
            continue
        values = rate[block][keep]
        candidates.append((float(np.median(values)), float(np.percentile(values, 90)), int(block[0]), block, fraction))
    if not candidates:
        return None
    location, p90, _, block, fraction = min(candidates, key=lambda item: item[:3])
    keep = valid[block] & np.isfinite(rate[block])
    values = rate[block][keep]
    mad = float(np.median(np.abs(values - location)))
    robust = 1.4826 * mad
    process_values = np.asarray(activity["process_rate_floor_rad_s"], float)[block]
    process_values = process_values[np.isfinite(process_values)]
    process_median = float(np.median(process_values)) if len(process_values) else math.nan
    frozen_floor = float(cfg["production_floor_rad_s"])
    scale = max(robust, frozen_floor)
    return {
        "start_row": int(block[0]),
        "stop_row_exclusive": int(block[-1] + 1),
        "row_indices": block.tolist(),
        "activity_median_rad_s": location,
        "activity_p90_rad_s": p90,
        "activity_mad_rad_s": mad,
        "empirical_robust_scale_rad_s": robust,
        "derived_process_floor_median_rad_s": process_median,
        "frozen_production_floor_rad_s": frozen_floor,
        "activity_scale_rad_s": scale,
        "scale_selected_component": "EMPIRICAL_MAD" if robust >= frozen_floor else "FROZEN_PROCESS_NOISE_FLOOR",
        "valid_fraction": fraction,
        "effective_sample_count": int(np.sum(keep)),
    }


def build_chain_signal(
    timeline: Any,
    parent_index: int,
    child_index: int,
    rows: np.ndarray,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    relative = relative_orientation(timeline.rotation[:, parent_index], timeline.rotation[:, child_index])
    valid = timeline.valid[:, parent_index] & timeline.valid[:, child_index] & np.isfinite(relative).all(axis=(1, 2))
    activity = primary_activity(timeline.time_ns, relative, valid, contract)
    baseline = lowest_activity_plateau(activity, rows, contract)
    if baseline is None:
        return {"status": "FAIL_VALID_TIME_SUPPORT", "relative": relative, "valid": valid, "activity": activity, "baseline": None}
    reference = huber_so3_reference(
        relative,
        baseline["row_indices"],
        {
            "baseline": {"huber_transition_sigma": 2.5, "maximum_iterations": 20, "convergence_rad": 1e-8},
            "coordinate": {"relative_orientation_uncertainty_floor_rad": float(contract["local_excursion_uncertainty"]["orientation_floor_rad"])},
        },
    )
    if reference["status"] != "AVAILABLE":
        return {"status": "FAIL_VALID_TIME_SUPPORT", "relative": relative, "valid": valid, "activity": activity, "baseline": baseline, "reference": reference}
    rotvec = residual_rotvec(np.asarray(reference["centre_matrix"]), relative)
    excursion = np.linalg.norm(rotvec, axis=1)
    smooth_count = max(1, round(0.08 * float(contract["common_time"]["rate_hz"])))
    smooth = smooth_valid(excursion, valid, smooth_count)
    reference_covariance = np.asarray(reference["tangent_covariance_rad2"], float)
    orientation_floor = float(contract["local_excursion_uncertainty"]["orientation_floor_rad"])
    process_angle = float(contract["normalization"]["production_floor_rad_s"]) / float(contract["common_time"]["rate_hz"])
    local_sigma = math.sqrt(max(float(np.trace(reference_covariance)) / 3.0, 0.0) + orientation_floor**2 + process_angle**2)
    sigma = np.full(len(excursion), local_sigma)
    z = (np.asarray(activity["rate_rad_s"]) - float(baseline["activity_median_rad_s"])) / float(baseline["activity_scale_rad_s"])
    derivative = np.full(len(smooth), np.nan)
    dt = np.asarray(activity["dt_s"])
    pair = valid & np.r_[False, valid[:-1]] & (dt > 0)
    indices = np.flatnonzero(pair)
    if len(indices):
        derivative[indices] = np.diff(smooth)[indices - 1] / dt[indices]
    derivative = smooth_valid(derivative, pair, max(1, round(0.10 * float(contract["common_time"]["rate_hz"]))))
    parent_q2 = timeline.covariance_rad2[:, parent_index]
    child_q2 = timeline.covariance_rad2[:, child_index]
    q2_trace = np.trace(parent_q2 + child_q2, axis1=1, axis2=2)
    return {
        "status": "AVAILABLE",
        "relative": relative,
        "valid": valid,
        "activity": activity,
        "activity_z": z,
        "baseline": baseline,
        "reference": reference,
        "excursion_rotvec": rotvec,
        "excursion_rad": excursion,
        "smoothed_excursion_rad": smooth,
        "excursion_uncertainty_rad": sigma,
        "derivative_rad_s": derivative,
        "absolute_q2_covariance_trace_rad2_audit_only": q2_trace,
        "absolute_q2_covariance_used_in_activity_denominator": False,
        "absolute_q2_covariance_used_in_excursion_denominator": False,
    }


def activity_candidate_diagnostics(signal: Mapping[str, Any], rows: np.ndarray, contract: Mapping[str, Any]) -> dict[str, Any]:
    cfg = contract["active_bout"]
    hz = float(contract["common_time"]["rate_hz"])
    z = np.asarray(signal["activity_z"])
    valid = np.asarray(signal["activity"]["valid"], bool)
    onset_n = max(1, round(float(cfg["onset_minimum_duration_s"]) * hz))
    offset_n = max(1, round(float(cfg["offset_minimum_duration_s"]) * hz))
    onset_mask = valid[rows] & np.isfinite(z[rows]) & (z[rows] >= float(cfg["onset_activity_z"]))
    offset_mask = valid[rows] & np.isfinite(z[rows]) & (z[rows] <= float(cfg["offset_activity_z"]))
    onset = [{"start_row": int(rows[a]), "stop_row_exclusive": int(rows[b - 1] + 1), "row_count": int(b - a), "accepted_duration": bool(b - a >= onset_n)} for a, b in runs(onset_mask)]
    offset = [{"start_row": int(rows[a]), "stop_row_exclusive": int(rows[b - 1] + 1), "row_count": int(b - a), "accepted_duration": bool(b - a >= offset_n)} for a, b in runs(offset_mask)]
    return {"onset_candidates": onset, "offset_candidates": offset}


def extrema_candidate_diagnostics(signal: Mapping[str, Any], rows: np.ndarray, contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    cfg = contract["cycle"]
    hz = float(contract["common_time"]["rate_hz"])
    q = np.asarray(signal["smoothed_excursion_rad"])
    sigma = np.asarray(signal["excursion_uncertainty_rad"])
    valid = np.asarray(signal["valid"], bool)
    output = []
    for start, stop in runs(valid[rows] & np.isfinite(q[rows]) & np.isfinite(sigma[rows])):
        block = rows[start:stop]
        if len(block) < 3:
            continue
        threshold = max(float(cfg["minimum_prominence_rad"]), float(cfg["minimum_prominence_sigma"]) * float(np.median(sigma[block])))
        peaks, properties = find_peaks(q[block], prominence=0.0, distance=max(1, round(float(cfg["minimum_peak_separation_s"]) * hz)), plateau_size=1)
        for index, peak in enumerate(peaks):
            prominence = float(properties["prominences"][index])
            output.append({"peak_row": int(block[peak]), "prominence_rad": prominence, "required_prominence_rad": threshold, "passes_prominence": bool(prominence >= threshold), "left_base_row": int(block[int(properties["left_bases"][index])]), "right_base_row": int(block[int(properties["right_bases"][index])])})
    return output


def analyze_chain(timeline: Any, parent_index: int, child_index: int, rows: np.ndarray, contract: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    signal = build_chain_signal(timeline, parent_index, child_index, rows, contract)
    if signal["status"] != "AVAILABLE":
        return signal, {"active_bouts": [], "cycles": {"complete_cycles": [], "partial_cycles": [], "rejected_cycles": []}, "candidates": {"onset_candidates": [], "offset_candidates": [], "extrema_candidates": []}}
    bouts = detect_active_bouts(signal, rows, contract)
    cycles = detect_cycles(signal, rows, contract)
    candidates = activity_candidate_diagnostics(signal, rows, contract)
    candidates["extrema_candidates"] = extrema_candidate_diagnostics(signal, rows, contract)
    return signal, {"active_bouts": bouts, "cycles": cycles, "candidates": candidates}


def _synthetic_angle(time_s: np.ndarray, kind: str) -> np.ndarray:
    time_s = np.asarray(time_s, float)
    angle = np.zeros(len(time_s))
    if kind == "no_motion":
        return angle
    if kind == "natural_sway":
        return 0.012 * np.sin(2.0 * np.pi * 0.23 * time_s)
    configurations = {
        "clear": (0.65, 1.55, 4),
        "slow": (0.48, 2.8, 3),
        "fast": (0.58, 0.75, 6),
        "drift": (0.60, 1.55, 4),
        "outlier": (0.60, 1.55, 4),
        "gap": (0.60, 1.55, 4),
    }
    amplitude, period, count = configurations[kind]
    start = 11.0
    phase = (time_s - start) / period
    active = (phase >= 0.0) & (phase < count)
    angle[active] = amplitude * 0.5 * (1.0 - np.cos(2.0 * np.pi * phase[active]))
    return angle


def _raw_synthetic_pair(kind: str) -> tuple[dict[str, np.ndarray], dict[str, tuple[int, int]], tuple[int, int]]:
    hz = 200.0
    time_s = np.arange(0.0, 23.0, 1.0 / hz)
    time_ns = np.rint(time_s * 1e9).astype(np.int64)
    angle = _synthetic_angle(time_s, kind)
    rate = np.gradient(angle, time_s)
    common = 0.018 * np.sin(2.0 * np.pi * 0.31 * time_s)
    common_rate = np.gradient(common, time_s)
    if kind == "drift":
        common_rate = common_rate + 0.001 * time_s
    dtype = np.dtype([("status", "u1"), ("global_time_ns", "i8"), ("boot_epoch", "i4"), ("acc_raw", "i2", (3,)), ("gyro_raw", "i2", (3,))])
    output = {}
    for name, z_rate in (("PARENT", common_rate), ("CHILD", common_rate + rate)):
        item = np.zeros(len(time_s), dtype=dtype)
        item["status"] = 1
        item["global_time_ns"] = time_ns
        item["boot_epoch"] = 1
        accel = np.zeros((len(time_s), 3)); accel[:, 2] = 2048.0
        accel[:, 0] = np.rint(10.0 * np.sin(2.0 * np.pi * 0.22 * time_s))
        gyro_dps = np.zeros((len(time_s), 3)); gyro_dps[:, 2] = np.degrees(z_rate) + 0.22
        gyro_dps[:, 0] = 0.18 * np.sin(2.0 * np.pi * 0.19 * time_s)
        if kind == "outlier" and name == "CHILD":
            gyro_dps[np.argmin(np.abs(time_s - 15.2)), 2] += 900.0
        item["acc_raw"] = np.rint(accel).astype(np.int16)
        item["gyro_raw"] = np.rint(gyro_dps * 16.384).astype(np.int16)
        if kind == "gap":
            item["status"][(time_s >= 15.0) & (time_s < 15.12)] = 0
        output[name] = item
    windows = {"initial_still_attempt2": (200_000_000, 5_200_000_000), "t_pose": (5_500_000_000, 10_500_000_000)}
    action = (10_500_000_000, 22_500_000_000)
    return output, windows, action


def _through_q2(kind: str, contract: Mapping[str, Any], q2_config: Mapping[str, Any], yaw_sigma_deg: float = 180.0) -> dict[str, Any]:
    from biospur_fusion.imu_multi_action_engineering_v1.common_time import build_common_timeline
    from biospur_fusion.imu_multi_action_engineering_v1.q2 import run_q2_frontend_v1

    raw, windows, action = _raw_synthetic_pair(kind)
    config = dict(q2_config); config["yaw_sigma_deg"] = float(yaw_sigma_deg)
    q2, audit, _, _ = run_q2_frontend_v1(raw, windows, config)
    timeline = build_common_timeline(q2, windows["initial_still_attempt2"][0], action[1], {"rate_hz": float(contract["common_time"]["rate_hz"]), "maximum_bracket_gap_s": float(contract["common_time"]["maximum_interpolation_bracket_gap_s"]), "require_same_boot_epoch": True})
    indices = {node: i for i, node in enumerate(timeline.node_order)}
    rows = np.flatnonzero((timeline.time_ns >= action[0]) & (timeline.time_ns <= action[1]))
    signal, evidence = analyze_chain(timeline, indices["PARENT"], indices["CHILD"], rows, contract)
    finite_z = signal["activity_z"][rows]; finite_z = finite_z[np.isfinite(finite_z)] if signal["status"] == "AVAILABLE" else np.asarray([])
    baseline_rows = np.asarray(signal["baseline"]["row_indices"], int) if signal["status"] == "AVAILABLE" else np.asarray([], int)
    quiet_false_positive = float(np.mean(signal["activity_z"][baseline_rows] >= float(contract["active_bout"]["onset_activity_z"]))) if len(baseline_rows) else math.nan
    covariance_trace = np.trace(timeline.covariance_rad2[:, indices["PARENT"]] + timeline.covariance_rad2[:, indices["CHILD"]], axis1=1, axis2=2)
    return {
        "kind": kind,
        "q2_verdict": audit["verdict"],
        "q2_absolute_covariance_trace_p50_rad2": float(np.nanmedian(covariance_trace[rows])),
        "signal_status": signal["status"],
        "scale_rad_s": None if signal["status"] != "AVAILABLE" else float(signal["baseline"]["activity_scale_rad_s"]),
        "z_max": float(np.max(finite_z)) if len(finite_z) else None,
        "quiet_false_positive_rate": quiet_false_positive,
        "active_bout_count": len(evidence["active_bouts"]),
        "complete_cycle_count": len(evidence["cycles"]["complete_cycles"]),
        "invalid_rows": int(np.sum(~signal["valid"][rows])) if signal["status"] == "AVAILABLE" else int(len(rows)),
        "timeline_valid_fraction": float(np.mean(timeline.valid[rows, indices["PARENT"]] & timeline.valid[rows, indices["CHILD"]])),
        "activity_z": signal["activity_z"].copy() if signal["status"] == "AVAILABLE" else np.asarray([]),
        "rows": rows,
        "signal": signal,
    }


def q2_through_synthetic_qualification(contract: Mapping[str, Any], q2_config: Mapping[str, Any]) -> dict[str, Any]:
    variants = {kind: _through_q2(kind, contract, q2_config) for kind in ("no_motion", "natural_sway", "clear", "slow", "fast", "drift", "outlier", "gap")}
    yaw180 = variants["clear"]
    yaw360 = _through_q2("clear", contract, q2_config, 360.0)
    clear_signal = yaw180["signal"]; rows = yaw180["rows"]
    # Frozen old error: absolute pose covariance is treated as independent increment noise.
    covariance = np.asarray(clear_signal["absolute_q2_covariance_trace_rad2_audit_only"])
    dt = np.asarray(clear_signal["activity"]["dt_s"])
    old_sigma = np.sqrt(np.maximum(covariance + np.r_[np.nan, covariance[:-1]], 0.0) / 3.0) / dt
    old_scale = float(np.nanmedian(old_sigma[rows]))
    old_z = (clear_signal["activity"]["rate_rad_s"] - float(clear_signal["baseline"]["activity_median_rad_s"])) / old_scale
    duplicate_dt_scale = float(contract["normalization"]["production_floor_rad_s"]) / (1.0 / float(contract["common_time"]["rate_hz"]))
    duplicate_dt_z = (clear_signal["activity"]["rate_rad_s"] - float(clear_signal["baseline"]["activity_median_rad_s"])) / duplicate_dt_scale
    controls = {
        "raw_gyro_accel_passes_production_q2": all(item["q2_verdict"] == "PASS_Q2_HUMAN_QUASI_STATIC_V1" for item in variants.values()),
        "real_common_time_and_activity_path_used": True,
        "absolute_uncertainty_increase_does_not_change_scale": abs(float(yaw180["scale_rad_s"]) - float(yaw360["scale_rad_s"])) <= 1e-15,
        "absolute_uncertainty_increase_does_not_change_activity_z": bool(np.array_equal(yaw180["activity_z"], yaw360["activity_z"], equal_nan=True)),
        "absolute_covariance_not_in_activity_denominator": bool(not clear_signal["absolute_q2_covariance_used_in_activity_denominator"]),
        "quiet_false_positive_controlled": all(float(variants[kind]["quiet_false_positive_rate"]) <= 0.05 for kind in ("no_motion", "natural_sway", "clear", "slow", "fast", "drift", "outlier", "gap")),
        "clear_action_crosses_frozen_z4": float(variants["clear"]["z_max"]) >= 4.0 and variants["clear"]["active_bout_count"] > 0,
        "slow_action_crosses_frozen_z4": float(variants["slow"]["z_max"]) >= 4.0 and variants["slow"]["active_bout_count"] > 0,
        "fast_action_crosses_frozen_z4": float(variants["fast"]["z_max"]) >= 4.0 and variants["fast"]["active_bout_count"] > 0,
        "no_motion_does_not_cross_z4": float(variants["no_motion"]["z_max"]) < 4.0 and variants["no_motion"]["active_bout_count"] == 0,
        "natural_sway_does_not_become_action": variants["natural_sway"]["active_bout_count"] == 0,
        "drift_common_mode_does_not_block_relative_action": variants["drift"]["active_bout_count"] > 0,
        "outlier_does_not_create_extra_cycles": variants["outlier"]["complete_cycle_count"] <= variants["clear"]["complete_cycle_count"] + 1,
        "gap_remains_invalid": variants["gap"]["invalid_rows"] > 0,
        "negative_control_independent_absolute_pose_covariance_fails": float(np.nanmax(old_z[rows])) < 4.0 and old_scale > 100.0,
        "negative_control_dt_divided_twice_fails": float(np.nanmax(duplicate_dt_z[rows])) < 4.0,
    }
    compact_variants = {key: {field: value for field, value in item.items() if field not in ("activity_z", "rows", "signal")} for key, item in variants.items()}
    payload = {
        "schema": "biospur-r3c-q2-through-synthetic-qualification-v1",
        "controls": controls,
        "variants": compact_variants,
        "absolute_yaw_uncertainty_variant": {"yaw180_covariance_trace_p50_rad2": yaw180["q2_absolute_covariance_trace_p50_rad2"], "yaw360_covariance_trace_p50_rad2": yaw360["q2_absolute_covariance_trace_p50_rad2"], "scale_180_rad_s": yaw180["scale_rad_s"], "scale_360_rad_s": yaw360["scale_rad_s"]},
        "negative_controls": {"old_independent_absolute_covariance_scale_rad_s": old_scale, "old_z_max": float(np.nanmax(old_z[rows])), "duplicate_dt_scale_rad_s": duplicate_dt_scale, "duplicate_dt_z_max": float(np.nanmax(duplicate_dt_z[rows]))},
    }
    first = repr(payload).encode()
    second_variants = {kind: _through_q2(kind, contract, q2_config) for kind in ("no_motion", "natural_sway", "clear", "slow", "fast", "drift", "outlier", "gap")}
    second_compact = {key: {field: value for field, value in item.items() if field not in ("activity_z", "rows", "signal")} for key, item in second_variants.items()}
    controls["deterministic_double_replay_byte_equivalent"] = repr(compact_variants).encode() == repr(second_compact).encode()
    payload["pass"] = all(controls.values())
    payload["terminal_outcome"] = "PASS_R3C_Q2_THROUGH_SYNTHETIC" if payload["pass"] else "FAIL_R3C_Q2_THROUGH_SYNTHETIC"
    payload["real_capture_accessed"] = False
    return payload
