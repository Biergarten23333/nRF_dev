"""Signal-only D-1 R3 cycle topology for human calibration actions.

The detector has no calibration parameters, fitted functional axes, anatomical
template, solver state, or held-out input.  It operates on validity-aware
parent-to-child SO(3) relative orientation and propagated uncertainty.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.signal import find_peaks
from scipy.spatial.transform import Rotation


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.r_[False, np.asarray(mask, bool), False].astype(np.int8))
    return [(int(a), int(b)) for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))]


def _smooth_valid(values: np.ndarray, valid: np.ndarray, count: int) -> np.ndarray:
    count = max(1, int(count))
    kernel = np.ones(count, float)
    numerator = np.convolve(np.where(valid, values, 0.0), kernel, mode="same")
    denominator = np.convolve(np.asarray(valid, float), kernel, mode="same")
    return np.divide(numerator, denominator, out=np.full(len(values), np.nan), where=denominator > 0)


def _rotation_mean(matrices: np.ndarray) -> np.ndarray:
    matrices = np.asarray(matrices, float)
    finite = np.isfinite(matrices).all(axis=(1, 2))
    if not np.any(finite):
        return np.full((3, 3), np.nan)
    return Rotation.from_matrix(matrices[finite]).mean().as_matrix()


def _distance(reference: np.ndarray, matrices: np.ndarray) -> np.ndarray:
    out = np.full(len(matrices), np.nan)
    finite = np.isfinite(reference).all() & np.isfinite(matrices).all(axis=(1, 2))
    if np.any(finite):
        out[finite] = Rotation.from_matrix(
            np.einsum("ji,njk->nik", reference, matrices[finite])
        ).magnitude()
    return out


def relative_orientation(parent: np.ndarray, child: np.ndarray) -> np.ndarray:
    return np.einsum("nji,njk->nik", np.asarray(parent, float), np.asarray(child, float))


def relative_rate_signal(
    time_ns: np.ndarray,
    relative: np.ndarray,
    covariance_rad2: np.ndarray,
    valid: np.ndarray,
    config: Mapping[str, Any],
) -> dict[str, np.ndarray]:
    """Return relative angular-rate SNR without interpolation across invalid rows."""
    time_ns = np.asarray(time_ns, np.int64)
    valid = np.asarray(valid, bool) & np.isfinite(relative).all(axis=(1, 2))
    dt = np.r_[np.nan, np.diff(time_ns) / 1e9]
    pair = valid & np.r_[False, valid[:-1]] & (dt > 0)
    step = np.full(len(relative), np.nan)
    indices = np.flatnonzero(pair)
    if len(indices):
        delta = np.einsum("nji,njk->nik", relative[indices - 1], relative[indices])
        step[indices] = Rotation.from_matrix(delta).magnitude()
    rate = step / dt
    covariance_rad2 = np.asarray(covariance_rad2, float)
    sigma = np.full(len(relative), np.nan)
    if len(indices):
        pair_variance = np.maximum(
            np.trace(covariance_rad2[indices] + covariance_rad2[indices - 1], axis1=1, axis2=2) / 3.0,
            0.0,
        )
        sigma[indices] = np.sqrt(pair_variance) / dt[indices]
    floor = float(config["signal"]["orientation_uncertainty_floor_rad"])
    nominal_dt = 1.0 / float(config["signal"]["rate_hz"])
    uncertainty = np.hypot(np.nan_to_num(sigma, nan=0.0), floor / nominal_dt)
    snr = rate / np.maximum(uncertainty, 1e-12)
    count = round(float(config["signal"]["derivative_smoothing_s"]) * float(config["signal"]["rate_hz"]))
    return {
        "rate_rad_s": _smooth_valid(rate, pair, count),
        "uncertainty_rad_s": uncertainty,
        "snr": _smooth_valid(snr, pair, count),
        "valid": pair,
    }


def select_pre_reference(
    rate_snr: np.ndarray,
    valid: np.ndarray,
    domain_rows: np.ndarray,
    config: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Select the last quiet run before the first sustained signal onset."""
    hz = float(config["signal"]["rate_hz"])
    onset_n = max(1, round(float(config["active_bout"]["minimum_onset_duration_s"]) * hz))
    ref_n = max(1, round(float(config["pre_reference"]["minimum_duration_s"]) * hz))
    onset = float(config["active_bout"]["onset_relative_rate_snr"])
    quiet = float(config["pre_reference"]["maximum_relative_rate_snr"])
    domain_rows = np.asarray(domain_rows, int)
    active_mask = valid[domain_rows] & np.isfinite(rate_snr[domain_rows]) & (rate_snr[domain_rows] >= onset)
    active_runs = [(a, b) for a, b in _runs(active_mask) if b - a >= onset_n]
    search_stop = active_runs[0][0] if active_runs else max(ref_n, len(domain_rows) // 3)
    quiet_mask = valid[domain_rows[:search_stop]] & np.isfinite(rate_snr[domain_rows[:search_stop]])
    quiet_mask &= rate_snr[domain_rows[:search_stop]] <= quiet
    candidates = [(a, b) for a, b in _runs(quiet_mask) if b - a >= ref_n]
    if not candidates:
        return None
    a, b = candidates[-1]
    rows = domain_rows[a:b]
    return {"start_row": int(rows[0]), "stop_row_exclusive": int(rows[-1] + 1), "row_indices": rows.tolist()}


def build_excursion_signal(
    time_ns: np.ndarray,
    parent_rotation: np.ndarray,
    child_rotation: np.ndarray,
    parent_covariance_rad2: np.ndarray,
    child_covariance_rad2: np.ndarray,
    parent_valid: np.ndarray,
    child_valid: np.ndarray,
    reference_rows: Sequence[int],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Build geodesic excursion and propagated uncertainty from a robust reference."""
    relative = relative_orientation(parent_rotation, child_rotation)
    valid = np.asarray(parent_valid, bool) & np.asarray(child_valid, bool)
    valid &= np.isfinite(relative).all(axis=(1, 2))
    rows = np.asarray(reference_rows, int)
    rows = rows[valid[rows]]
    if len(rows) < 3:
        raise ValueError("insufficient valid pre-reference rows")
    reference = _rotation_mean(relative[rows])
    distances = _distance(reference, relative[rows])
    median = float(np.median(distances))
    mad = float(np.median(np.abs(distances - median)))
    robust_scale = max(float(config["signal"]["orientation_uncertainty_floor_rad"]), 1.4826 * mad)
    retain = distances <= median + float(config["pre_reference"]["robust_trim_mad_multiplier"]) * robust_scale
    if float(np.mean(retain)) < float(config["pre_reference"]["minimum_retained_fraction"]):
        raise ValueError("pre-reference robust retention below frozen minimum")
    reference = _rotation_mean(relative[rows[retain]])
    reference_distance = _distance(reference, relative[rows[retain]])
    reference_sigma = max(
        float(config["signal"]["orientation_uncertainty_floor_rad"]),
        float(np.percentile(reference_distance, 68)),
    )
    excursion = _distance(reference, relative)
    covariance = np.asarray(parent_covariance_rad2, float) + np.asarray(child_covariance_rad2, float)
    row_variance = np.maximum(np.trace(covariance, axis1=1, axis2=2) / 3.0, 0.0)
    uncertainty = np.sqrt(reference_sigma**2 + row_variance + float(config["signal"]["orientation_uncertainty_floor_rad"])**2)
    valid &= np.isfinite(excursion) & np.isfinite(uncertainty)
    smooth_n = round(float(config["signal"]["smoothing_s"]) * float(config["signal"]["rate_hz"]))
    smooth = _smooth_valid(excursion, valid, smooth_n)
    dt = np.r_[np.nan, np.diff(np.asarray(time_ns, np.int64)) / 1e9]
    derivative = np.full(len(smooth), np.nan)
    pair = valid & np.r_[False, valid[:-1]] & (dt > 0)
    derivative[pair] = np.diff(smooth)[np.flatnonzero(pair) - 1] / dt[pair]
    derivative = _smooth_valid(
        derivative,
        pair,
        round(float(config["signal"]["derivative_smoothing_s"]) * float(config["signal"]["rate_hz"])),
    )
    rate = relative_rate_signal(time_ns, relative, covariance, valid, config)
    return {
        "relative_rotation": relative,
        "valid": valid,
        "reference_rotation": reference,
        "reference_rows": rows[retain].tolist(),
        "reference_retained_fraction": float(np.mean(retain)),
        "reference_sigma_rad": reference_sigma,
        "excursion_rad": excursion,
        "smoothed_excursion_rad": smooth,
        "uncertainty_rad": uncertainty,
        "excursion_sigma": excursion / np.maximum(uncertainty, 1e-12),
        "derivative_rad_s": derivative,
        "relative_rate_rad_s": rate["rate_rad_s"],
        "relative_rate_uncertainty_rad_s": rate["uncertainty_rad_s"],
        "relative_rate_snr": rate["snr"],
        "relative_rate_valid": rate["valid"],
    }


def detect_active_bouts(signal: Mapping[str, Any], rows: Sequence[int], config: Mapping[str, Any]) -> list[dict[str, Any]]:
    hz = float(config["signal"]["rate_hz"])
    domain = np.asarray(rows, int)
    snr = np.asarray(signal["relative_rate_snr"], float)
    valid = np.asarray(signal["relative_rate_valid"], bool)
    onset = valid[domain] & np.isfinite(snr[domain]) & (snr[domain] >= float(config["active_bout"]["onset_relative_rate_snr"]))
    minimum = max(1, round(float(config["active_bout"]["minimum_onset_duration_s"]) * hz))
    seeds = [(a, b) for a, b in _runs(onset) if b - a >= minimum]
    candidates = []
    offset = float(config["active_bout"]["offset_relative_rate_snr"])
    for local_a, local_b in seeds:
        a, b = local_a, local_b
        while a > 0 and valid[domain[a - 1]] and np.isfinite(snr[domain[a - 1]]) and snr[domain[a - 1]] > offset:
            a -= 1
        while b < len(domain) and valid[domain[b]] and np.isfinite(snr[domain[b]]) and snr[domain[b]] > offset:
            b += 1
        candidates.append((a, b))
    gap = round(float(config["active_bout"]["maximum_within_bout_low_activity_gap_s"]) * hz)
    merged: list[tuple[int, int]] = []
    for a, b in candidates:
        if merged and a - merged[-1][1] <= gap and np.all(valid[domain[merged[-1][1]:a]]):
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    minimum_bout = round(float(config["active_bout"]["minimum_duration_s"]) * hz)
    return [
        {"start_row": int(domain[a]), "stop_row_exclusive": int(domain[b - 1] + 1), "duration_s": float((b - a) / hz)}
        for a, b in merged if b - a >= minimum_bout
    ]


def detect_cycles(signal: Mapping[str, Any], rows: Sequence[int], config: Mapping[str, Any]) -> dict[str, Any]:
    """Detect amplitude-relative rise/extremum/reversal/recovery topology."""
    domain = np.asarray(rows, int)
    q = np.asarray(signal["smoothed_excursion_rad"], float)
    uncertainty = np.asarray(signal["uncertainty_rad"], float)
    valid = np.asarray(signal["valid"], bool)
    hz = float(config["signal"]["rate_hz"])
    cfg = config["cycle_topology"]
    minimum_half = max(1, round(float(cfg["minimum_half_cycle_duration_s"]) * hz))
    minimum_distance = max(1, round(float(cfg["minimum_peak_separation_s"]) * hz))
    cycles: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    domain_valid = valid[domain] & np.isfinite(q[domain]) & np.isfinite(uncertainty[domain])
    for local_start, local_stop in _runs(domain_valid):
        run_rows = domain[local_start:local_stop]
        if len(run_rows) < 2 * minimum_half + 1:
            continue
        values = q[run_rows]
        sigma = uncertainty[run_rows]
        threshold = max(float(cfg["minimum_prominence_rad"]), float(cfg["minimum_prominence_sigma"]) * float(np.median(sigma)))
        peaks, properties = find_peaks(values, prominence=threshold, distance=minimum_distance, plateau_size=1)
        for ordinal, local_peak in enumerate(peaks):
            left_limit = 0 if ordinal == 0 else int(peaks[ordinal - 1]) + 1
            right_limit = len(values) if ordinal + 1 == len(peaks) else int(peaks[ordinal + 1])
            if local_peak - left_limit < minimum_half or right_limit - local_peak <= minimum_half:
                rejected.append({"peak_row": int(run_rows[local_peak]), "reason": "INSUFFICIENT_HALF_CYCLE_DURATION"})
                continue
            left = left_limit + int(np.argmin(values[left_limit:local_peak + 1]))
            right = local_peak + int(np.argmin(values[local_peak:right_limit]))
            peak_value = float(values[local_peak])
            rise = peak_value - float(values[left])
            drop = peak_value - float(values[right])
            local_sigma = float(np.median(sigma[left:right + 1]))
            recovery = drop / max(rise, 1e-12)
            rise_diff = np.diff(values[left:local_peak + 1])
            fall_diff = np.diff(values[local_peak:right + 1])
            diff_floor = float(cfg["derivative_deadband_sigma"]) * local_sigma / max(minimum_half, 1)
            rise_informative = rise_diff[np.abs(rise_diff) > diff_floor]
            fall_informative = fall_diff[np.abs(fall_diff) > diff_floor]
            rise_fraction = float(np.mean(rise_informative > 0)) if len(rise_informative) else 1.0
            fall_fraction = float(np.mean(fall_informative < 0)) if len(fall_informative) else 1.0
            prominence = float(properties["prominences"][ordinal])
            prominence_sigma = prominence / max(local_sigma, 1e-12)
            plateau_rows = int(properties.get("plateau_sizes", np.ones(len(peaks), int))[ordinal])
            record = {
                "start_row": int(run_rows[left]),
                "peak_row": int(run_rows[local_peak]),
                "stop_row_exclusive": int(run_rows[right] + 1),
                "start_excursion_rad": float(values[left]),
                "peak_excursion_rad": peak_value,
                "end_excursion_rad": float(values[right]),
                "amplitude_rad": rise,
                "prominence_rad": prominence,
                "prominence_sigma": prominence_sigma,
                "recovery_fraction": float(recovery),
                "rise_duration_s": float((local_peak - left) / hz),
                "fall_duration_s": float((right - local_peak) / hz),
                "rise_trend_fraction": rise_fraction,
                "fall_trend_fraction": fall_fraction,
                "extremum_hold_s": float(plateau_rows / hz),
                "quiet_plateau_required": False,
                "exact_pre_pose_return_required": False,
            }
            reasons = []
            if rise < threshold or prominence < threshold:
                reasons.append("PROMINENCE_BELOW_UNCERTAINTY_SCALED_THRESHOLD")
            if rise_fraction < float(cfg["minimum_rise_fall_trend_fraction"]):
                reasons.append("RISE_TOPOLOGY_NOT_SUSTAINED")
            if fall_fraction < float(cfg["minimum_rise_fall_trend_fraction"]):
                reasons.append("FALL_TOPOLOGY_NOT_SUSTAINED")
            if recovery < float(cfg["minimum_recovery_fraction"]):
                reasons.append("PARTIAL_RECOVERY")
            if float(record["extremum_hold_s"]) > float(cfg["extremum_hold_maximum_s"]):
                reasons.append("EXTREMUM_HOLD_EXCEEDS_DECLARED_MAXIMUM")
            if not reasons:
                record["classification"] = "COMPLETE_CYCLE"
                cycles.append(record)
            elif reasons == ["PARTIAL_RECOVERY"]:
                record["classification"] = "PARTIAL_REPETITION_DISCLOSED"
                record["rejection_reasons"] = reasons
                partial.append(record)
            else:
                record["classification"] = "REJECTED_CANDIDATE"
                record["rejection_reasons"] = reasons
                rejected.append(record)
    bouts = detect_active_bouts(signal, domain, config)
    return {
        "complete_cycles": cycles,
        "partial_repetitions": partial,
        "rejected_candidates": rejected,
        "active_bouts": bouts,
        "ACTIVE_BOUT_VALID": bool(bouts),
        "CYCLE_TOPOLOGY_VALID": bool(cycles),
        "detected_repetition_count": len(cycles),
        "invalid_row_count": int(np.sum(~domain_valid)),
    }


def associate_bilateral_cycles(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]], time_ns: np.ndarray, config: Mapping[str, Any]) -> dict[str, Any]:
    limit = float(config["arms"]["maximum_bilateral_peak_offset_s"])
    candidates = []
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            overlap = max(0, min(int(a["stop_row_exclusive"]), int(b["stop_row_exclusive"])) - max(int(a["start_row"]), int(b["start_row"])))
            peak_offset = abs(int(time_ns[int(a["peak_row"])]) - int(time_ns[int(b["peak_row"])])) / 1e9
            if overlap > 0 and peak_offset <= limit:
                candidates.append((peak_offset, -overlap, i, j))
    used_l: set[int] = set(); used_r: set[int] = set(); pairs = []
    for peak_offset, negative_overlap, i, j in sorted(candidates):
        if i in used_l or j in used_r:
            continue
        used_l.add(i); used_r.add(j)
        pairs.append({"left_cycle_index": i, "right_cycle_index": j, "peak_offset_s": float(peak_offset), "overlap_rows": int(-negative_overlap)})
    events = []
    for pair in pairs:
        a, b = left[pair["left_cycle_index"]], right[pair["right_cycle_index"]]
        events.append({"class": "bilateral_arms", "start_row": min(a["start_row"], b["start_row"]), "stop_row_exclusive": max(a["stop_row_exclusive"], b["stop_row_exclusive"]), **pair})
    events.extend({"class": "left_arm", "start_row": x["start_row"], "stop_row_exclusive": x["stop_row_exclusive"], "left_cycle_index": i} for i, x in enumerate(left) if i not in used_l)
    events.extend({"class": "right_arm", "start_row": x["start_row"], "stop_row_exclusive": x["stop_row_exclusive"], "right_cycle_index": i} for i, x in enumerate(right) if i not in used_r)
    events.sort(key=lambda x: (x["start_row"], x["stop_row_exclusive"], x["class"]))
    classes = []
    for event in events:
        if not classes or classes[-1] != event["class"]:
            classes.append(event["class"])
    required = list(config["arms"]["required_detected_classes"])
    observed_required = [name for name in classes if name in required]
    return {
        "pairs": pairs,
        "chronological_events": events,
        "chronological_block_classes": classes,
        "required_classes_present": {name: name in classes for name in required},
        "all_required_classes_present": all(name in classes for name in required),
        "soft_protocol_order": list(config["arms"]["protocol_order"]),
        "signal_order_matches_soft_protocol": observed_required == required,
    }


def _synthetic_rotations(angle: np.ndarray, parent_angle: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    parent_angle = np.zeros_like(angle) if parent_angle is None else np.asarray(parent_angle, float)
    parent = Rotation.from_rotvec(np.c_[np.zeros(len(angle)), parent_angle, np.zeros(len(angle))]).as_matrix()
    child = Rotation.from_rotvec(np.c_[np.zeros(len(angle)), parent_angle + angle, np.zeros(len(angle))]).as_matrix()
    return parent, child


def _cycle_wave(amplitudes: Sequence[float], low_fraction: float = 0.0, partial_final: bool = False, hold_samples: int = 0, samples_half: int = 35) -> np.ndarray:
    values = [0.0] * 50
    low = 0.0
    for ordinal, amplitude in enumerate(amplitudes):
        high = float(amplitude)
        rise = low + (high - low) * 0.5 * (1 - np.cos(np.linspace(0, np.pi, samples_half, endpoint=False)))
        values.extend(rise.tolist())
        if hold_samples:
            values.extend([high] * hold_samples)
        if partial_final and ordinal == len(amplitudes) - 1:
            partial_low = high * 0.80
            fall = partial_low + (high - partial_low) * 0.5 * (
                1 + np.cos(np.linspace(0, np.pi, samples_half, endpoint=False))
            )
            values.extend(fall.tolist())
            values.extend([partial_low] * 40)
            return np.asarray(values, float)
        next_low = high * float(low_fraction)
        fall = next_low + (high - next_low) * 0.5 * (1 + np.cos(np.linspace(0, np.pi, samples_half, endpoint=False)))
        values.extend(fall.tolist())
        low = next_low
    values.extend([low] * 40)
    return np.asarray(values, float)


def _run_synthetic_angle(angle: np.ndarray, config: Mapping[str, Any], parent_angle: np.ndarray | None = None, invalid: np.ndarray | None = None) -> dict[str, Any]:
    n = len(angle); time_ns = (np.arange(n) * int(1e9 / float(config["signal"]["rate_hz"]))).astype(np.int64)
    parent, child = _synthetic_rotations(angle, parent_angle)
    covariance = np.tile(np.eye(3) * 1e-6, (n, 1, 1))
    valid = np.ones(n, bool) if invalid is None else ~np.asarray(invalid, bool)
    reference = np.arange(5, 45)
    signal = build_excursion_signal(time_ns, parent, child, covariance, covariance, valid, valid, reference, config)
    result = detect_cycles(signal, np.arange(n), config)
    result["signal"] = signal
    result["time_ns"] = time_ns
    return result


def run_r3_synthetic_qualification(config: Mapping[str, Any]) -> dict[str, Any]:
    """Run all predeclared controls without real capture input."""
    five = _run_synthetic_angle(_cycle_wave([0.55] * 5), config)
    partial_return = _run_synthetic_angle(_cycle_wave([0.60] * 5, low_fraction=0.30), config)
    unequal = _run_synthetic_angle(_cycle_wave([0.35, 0.55, 0.75, 0.45, 0.65]), config)
    partial_final = _run_synthetic_angle(_cycle_wave([0.55] * 5, partial_final=True), config)
    hold = _run_synthetic_angle(_cycle_wave([0.60] * 3, hold_samples=25), config)
    base = _cycle_wave([0.55] * 5)
    forearm_only_shoulder = _run_synthetic_angle(np.zeros_like(base), config)
    compensated = _run_synthetic_angle(base, config, parent_angle=0.20 * base)
    left = _run_synthetic_angle(_cycle_wave([0.55] * 5), config)
    shifted = np.r_[np.zeros(18), _cycle_wave([0.42, 0.60, 0.48, 0.65, 0.50])]
    if len(shifted) < len(left["time_ns"]): shifted = np.pad(shifted, (0, len(left["time_ns"]) - len(shifted)))
    shifted = shifted[:len(left["time_ns"])]
    right = _run_synthetic_angle(shifted, config)
    association = associate_bilateral_cycles(left["complete_cycles"], right["complete_cycles"], left["time_ns"], config)
    heel = _run_synthetic_angle(base, config, parent_angle=0.30 * base)
    rigid = _run_synthetic_angle(np.zeros_like(base), config, parent_angle=base)
    rng = np.random.default_rng(int(config["synthetic_rng_seed"]))
    noisy_angle = _cycle_wave([0.55] * 5) + rng.normal(0.0, 0.004, len(base))
    noisy = _run_synthetic_angle(noisy_angle, config)
    invalid_mask = np.zeros(len(base), bool); invalid_mask[80:90] = True
    invalid = _run_synthetic_angle(base, config, invalid=invalid_mask)
    replay_a = _run_synthetic_angle(_cycle_wave([0.55] * 5, low_fraction=0.25), config)
    replay_b = _run_synthetic_angle(_cycle_wave([0.55] * 5, low_fraction=0.25), config)
    controls = {
        "five_smooth_cycles_without_quiet_pause": five["detected_repetition_count"] == 5,
        "cycles_returning_only_60_to_80_percent": partial_return["detected_repetition_count"] == 5,
        "unequal_amplitudes": unequal["detected_repetition_count"] == 5,
        "one_partial_final_repetition": partial_final["detected_repetition_count"] == 4 and len(partial_final["partial_repetitions"]) == 1,
        "hold_at_maximum_excursion": hold["detected_repetition_count"] == 3,
        "stable_upper_arm_with_forearm_only_motion": forearm_only_shoulder["detected_repetition_count"] == 0,
        "upper_arm_raise_with_natural_elbow_compensation": compensated["detected_repetition_count"] == 5,
        "bilateral_arms_with_timing_mismatch": len(association["pairs"]) >= 4,
        "heel_to_butt_with_thigh_compensation": heel["detected_repetition_count"] == 5,
        "rigid_torso_limb_motion_no_relative_excursion": rigid["detected_repetition_count"] == 0,
        "noisy_derivative_near_extremum": noisy["detected_repetition_count"] == 5,
        "invalid_gap_not_crossed": invalid["detected_repetition_count"] < 5 and invalid["invalid_row_count"] == 10,
        "deterministic_double_replay": replay_a["complete_cycles"] == replay_b["complete_cycles"] and replay_a["partial_repetitions"] == replay_b["partial_repetitions"],
    }
    required_outcomes = {
        "EXTREMUM_HOLD_IS_NOT_NEUTRAL_BUT_PRESERVES_CYCLE_TOPOLOGY": controls["hold_at_maximum_excursion"],
        "PARTIAL_RETURN_CAN_COMPLETE_A_CYCLE": controls["cycles_returning_only_60_to_80_percent"],
        "FOREARM_ONLY_MOTION_IS_NOT_AN_ARM_RAISE": controls["stable_upper_arm_with_forearm_only_motion"],
        "RIGID_CHAIN_MOTION_IS_NOT_A_JOINT_CYCLE": controls["rigid_torso_limb_motion_no_relative_excursion"],
        "PARTIAL_FINAL_REPETITION_IS_DISCLOSED_NOT_FORCED": controls["one_partial_final_repetition"],
    }
    summaries = {
        "five_smooth": five["detected_repetition_count"],
        "partial_return": partial_return["detected_repetition_count"],
        "unequal": unequal["detected_repetition_count"],
        "partial_final_complete": partial_final["detected_repetition_count"],
        "partial_final_disclosed": len(partial_final["partial_repetitions"]),
        "extremum_hold": hold["detected_repetition_count"],
        "forearm_only_shoulder": forearm_only_shoulder["detected_repetition_count"],
        "compensated_upper_arm": compensated["detected_repetition_count"],
        "bilateral_pairs": len(association["pairs"]),
        "heel_thigh_compensation": heel["detected_repetition_count"],
        "rigid_chain": rigid["detected_repetition_count"],
        "noisy_extremum": noisy["detected_repetition_count"],
        "invalid_gap_cycles": invalid["detected_repetition_count"],
    }
    passed = all(controls.values()) and all(required_outcomes.values())
    return {
        "schema": "biospur-revision-d-minus-1-r3-synthetic-cycle-qualification-v1",
        "controls": controls,
        "required_outcomes": required_outcomes,
        "summaries": summaries,
        "pass": passed,
        "terminal_outcome": "PASS_R3_SYNTHETIC_CYCLE_QUALIFICATION" if passed else "FAIL_R3_SYNTHETIC_CONTROL",
        "real_capture_accessed": False,
    }
