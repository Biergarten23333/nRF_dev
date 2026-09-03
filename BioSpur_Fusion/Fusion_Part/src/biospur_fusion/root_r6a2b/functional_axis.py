"""Native-time undirected functional-axis estimation for ROOT-R6A2B-R4."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log


@dataclass(frozen=True)
class OrientationStream:
    time_ns: np.ndarray
    rotation: np.ndarray
    interval_id: np.ndarray
    boot_epoch: np.ndarray
    gap_count: int
    max_dt_s: float


@dataclass(frozen=True)
class RelativeIncrementEvidence:
    start_time_ns: np.ndarray
    stop_time_ns: np.ndarray
    dt_s: np.ndarray
    interval_id: np.ndarray
    phi_right_local_child: np.ndarray
    phi_parent_session: np.ndarray
    omega_parent_session: np.ndarray
    parent_rotation: np.ndarray
    child_rotation: np.ndarray


def axis_line_angle(left: np.ndarray, right: np.ndarray) -> float:
    """Angular distance between undirected lines in radians."""
    a = np.asarray(left, float); b = np.asarray(right, float)
    a = a / np.linalg.norm(a); b = b / np.linalg.norm(b)
    return float(np.arccos(np.clip(abs(float(a @ b)), 0.0, 1.0)))


def deterministic_line_sign(axis: np.ndarray) -> np.ndarray:
    value = np.asarray(axis, float).copy()
    value /= np.linalg.norm(value)
    pivot = int(np.argmax(np.abs(value)))
    if value[pivot] < 0.0:
        value = -value
    return value


def integrate_native_gyro(
    time_ns: np.ndarray,
    gyro_rad_s: np.ndarray,
    boot_epoch: np.ndarray,
    extrinsic_rotation: np.ndarray,
    gyro_bias_rad_s: np.ndarray,
    *,
    max_gap_ns: int = 20_000_000,
) -> OrientationStream:
    """Integrate every accepted native interval with the existing R2 rotation rule."""
    times = np.asarray(time_ns, np.int64)
    gyro = np.asarray(gyro_rad_s, float)
    boot = np.asarray(boot_epoch, np.int64)
    if len(times) < 2 or gyro.shape != (len(times), 3) or boot.shape != (len(times),):
        raise ValueError("invalid native gyro stream")
    dt_ns = np.diff(times)
    if np.any(dt_ns <= 0):
        raise ValueError("native timestamps must be strictly increasing")
    rotation = np.empty((len(times), 3, 3))
    interval = np.zeros(len(times), np.int64)
    sensor = np.asarray(extrinsic_rotation, float).copy()
    extrinsic_inverse = np.asarray(extrinsic_rotation, float).T
    rotation[0] = sensor @ extrinsic_inverse
    current_interval = 0
    gaps = 0
    for index, dt_value in enumerate(dt_ns):
        if dt_value > max_gap_ns or boot[index + 1] != boot[index]:
            current_interval += 1
            gaps += 1
            sensor = np.asarray(extrinsic_rotation, float).copy()
        else:
            dt = float(dt_value) * 1e-9
            sensor = sensor @ so3_exp((gyro[index] - gyro_bias_rad_s) * dt)
        interval[index + 1] = current_interval
        rotation[index + 1] = sensor @ extrinsic_inverse
    return OrientationStream(
        time_ns=times, rotation=rotation, interval_id=interval, boot_epoch=boot,
        gap_count=gaps, max_dt_s=float(np.max(dt_ns) * 1e-9),
    )


def align_child_to_parent_native(
    parent: OrientationStream,
    child: OrientationStream,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    """SLERP child orientations onto parent native timestamps without crossing gaps."""
    kept_time = []; kept_parent = []; kept_child = []; interval_pairs = []
    interpolation_offset = []
    for parent_index, target in enumerate(parent.time_ns):
        right = int(np.searchsorted(child.time_ns, target, side="left"))
        if right < len(child.time_ns) and int(child.time_ns[right]) == int(target):
            child_rotation = child.rotation[right]
            child_interval = int(child.interval_id[right])
            offset = 0
        else:
            if right == 0 or right >= len(child.time_ns):
                continue
            left = right - 1
            if child.interval_id[left] != child.interval_id[right]:
                continue
            span = int(child.time_ns[right] - child.time_ns[left])
            if span <= 0:
                continue
            fraction = float(target - child.time_ns[left]) / float(span)
            child_rotation = child.rotation[left] @ so3_exp(
                fraction * so3_log(child.rotation[left].T @ child.rotation[right])
            )
            child_interval = int(child.interval_id[left])
            offset = min(int(target - child.time_ns[left]), int(child.time_ns[right] - target))
        kept_time.append(int(target)); kept_parent.append(parent.rotation[parent_index])
        kept_child.append(child_rotation)
        interval_pairs.append((int(parent.interval_id[parent_index]), child_interval, parent_index))
        interpolation_offset.append(offset)
    if len(kept_time) < 2:
        raise ValueError("no overlapping parent/child native orientation interval")
    combined = np.zeros(len(kept_time), np.int64)
    current = 0
    for index in range(1, len(kept_time)):
        previous = interval_pairs[index - 1]
        now = interval_pairs[index]
        if now[:2] != previous[:2] or now[2] != previous[2] + 1:
            current += 1
        combined[index] = current
    return (
        np.asarray(kept_time, np.int64), np.asarray(kept_parent),
        np.asarray(kept_child), combined,
        {
            "target_clock": "parent accepted native IMU timestamps",
            "child_interpolation": "SO(3) geodesic interpolation between bracketing accepted native timestamps",
            "maximum_nearest_child_timestamp_offset_ns": int(max(interpolation_offset)),
            "median_nearest_child_timestamp_offset_ns": float(np.median(interpolation_offset)),
            "combined_valid_interval_count": int(current + 1),
        },
    )


def relative_increment_evidence(
    time_ns: np.ndarray,
    parent_rotation: np.ndarray,
    child_rotation: np.ndarray,
    interval_id: np.ndarray,
) -> RelativeIncrementEvidence:
    """Build R_PC and its right increments, expressed consistently in parent coordinates."""
    times = np.asarray(time_ns, np.int64)
    parent = np.asarray(parent_rotation, float)
    child = np.asarray(child_rotation, float)
    intervals = np.asarray(interval_id, np.int64)
    relative = np.einsum("nji,njk->nik", parent, child)
    starts = []; stops = []; dt = []; ids = []; body = []; parent_phi = []
    kept_parent = []; kept_child = []
    for index in range(len(times) - 1):
        if intervals[index] != intervals[index + 1]:
            continue
        duration = float(times[index + 1] - times[index]) * 1e-9
        if duration <= 0.0:
            raise ValueError("relative evidence time reversal")
        local = so3_log(relative[index].T @ relative[index + 1])
        starts.append(int(times[index])); stops.append(int(times[index + 1])); dt.append(duration)
        ids.append(int(intervals[index])); body.append(local)
        # R_PC maps child coordinates into the calibrated parent-session frame.
        parent_phi.append(relative[index] @ local)
        kept_parent.append(parent[index]); kept_child.append(child[index])
    phi_parent = np.asarray(parent_phi)
    duration = np.asarray(dt)
    return RelativeIncrementEvidence(
        start_time_ns=np.asarray(starts, np.int64), stop_time_ns=np.asarray(stops, np.int64),
        dt_s=duration, interval_id=np.asarray(ids, np.int64),
        phi_right_local_child=np.asarray(body), phi_parent_session=phi_parent,
        omega_parent_session=phi_parent / duration[:, None],
        parent_rotation=np.asarray(kept_parent), child_rotation=np.asarray(kept_child),
    )


def stationary_noise_distribution(evidence: RelativeIncrementEvidence) -> dict[str, Any]:
    magnitude = np.linalg.norm(evidence.omega_parent_session, axis=1)
    scale = float(np.sqrt(np.mean(magnitude ** 2)))
    return {
        "scale_rms_rad_s": scale,
        "count": int(len(magnitude)),
        "median_rad_s": float(np.median(magnitude)),
        "q05_rad_s": float(np.quantile(magnitude, .05)),
        "q95_rad_s": float(np.quantile(magnitude, .95)),
        "maximum_rad_s": float(np.max(magnitude)),
        "provenance": (
            "empirical parent-relative omega distribution from authoritative initial_still attempt 2; "
            "no nominal 200 Hz substitution"
        ),
    }


def motion_weights(omega_norm_rad_s: np.ndarray, stationary_scale_rad_s: float,
                   scale_multiplier: float = 1.0) -> np.ndarray:
    magnitude = np.asarray(omega_norm_rad_s, float)
    scale = float(stationary_scale_rad_s) * float(scale_multiplier)
    if scale <= 0.0:
        raise ValueError("stationary motion scale must be positive")
    ratio2 = (magnitude / scale) ** 2
    return ratio2 / (1.0 + ratio2)


def weighted_quantile(values: np.ndarray, weights: np.ndarray, probability: float) -> float:
    value = np.asarray(values, float); weight = np.asarray(weights, float)
    if not len(value) or float(np.sum(weight)) <= 0.0:
        return float("nan")
    order = np.argsort(value)
    cumulative = np.cumsum(weight[order])
    target = probability * cumulative[-1]
    return float(value[order[min(int(np.searchsorted(cumulative, target)), len(value) - 1)]])


def scatter_axis(directions: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    direction = np.asarray(directions, float); weight = np.asarray(weights, float)
    scatter = np.einsum("n,ni,nj->ij", weight, direction, direction)
    eigenvalues, eigenvectors = np.linalg.eigh(0.5 * (scatter + scatter.T))
    axis = deterministic_line_sign(eigenvectors[:, -1])
    return axis, eigenvalues, scatter


def _bout_ids(
    weights: np.ndarray,
    intervals: np.ndarray,
    projected_omega: np.ndarray,
    dt_s: np.ndarray,
    stationary_scale_rad_s: float,
) -> tuple[np.ndarray, list[dict[str, int]]]:
    """Build direction-consistent half-cycle units from measured motion evidence.

    The only crossover is ``w=0.5`` (signal magnitude equals the empirical
    stationary RMS).  Opposite-sign runs whose projected rotation does not
    exceed the stationary rotation accumulated over the same duration are
    retained in sample accounting but are not promoted to bootstrap bouts.
    """
    informative = np.asarray(weights) >= 0.5
    bout = np.full(len(weights), -1, np.int64)
    rows: list[dict[str, int]] = []
    current = -1
    for interval in np.unique(intervals):
        active = np.flatnonzero((intervals == interval) & informative)
        if not len(active):
            continue
        run_start = 0
        signs = np.sign(projected_omega[active])
        signs[signs == 0.0] = 1.0
        for stop in range(1, len(active) + 1):
            boundary = stop == len(active) or signs[stop] != signs[stop - 1]
            if not boundary:
                continue
            members = active[run_start:stop]
            duration = float(np.sum(dt_s[members]))
            projected_rotation = float(np.sum(np.abs(projected_omega[members]) * dt_s[members]))
            noise_rotation = float(stationary_scale_rad_s * duration)
            if projected_rotation > noise_rotation:
                current += 1
                bout[members] = current
                rows.append({
                    "bout_id": current,
                    "start_increment": int(members[0]),
                    "stop_increment_exclusive": int(members[-1] + 1),
                    "direction_sign_for_accounting_only": int(signs[run_start]),
                })
            run_start = stop
    return bout, rows


def estimate_undirected_axis(
    evidence: RelativeIncrementEvidence,
    stationary_scale_rad_s: float,
    *,
    scale_multiplier: float = 1.0,
    bootstrap_replicates: int = 400,
    bootstrap_seed: int = 6204,
) -> dict[str, Any]:
    phi = evidence.phi_parent_session
    magnitude = np.linalg.norm(phi, axis=1)
    nonzero = magnitude > np.finfo(float).eps
    directions = np.zeros_like(phi)
    directions[nonzero] = phi[nonzero] / magnitude[nonzero, None]
    omega_norm = np.linalg.norm(evidence.omega_parent_session, axis=1)
    motion = motion_weights(omega_norm, stationary_scale_rad_s, scale_multiplier)
    # Duration makes the result invariant to local native sampling density.
    scatter_weight = motion * evidence.dt_s
    scatter_weight[~nonzero] = 0.0
    axis, eigenvalues, scatter = scatter_axis(directions, scatter_weight)
    angles = np.asarray([axis_line_angle(direction, axis) if valid else 0.0
                         for direction, valid in zip(directions, nonzero)])
    positive = scatter_weight > 0.0
    total_weight = float(np.sum(scatter_weight))
    rms = float(np.sqrt(np.sum(scatter_weight * angles ** 2) / total_weight))
    median = weighted_quantile(angles[positive], scatter_weight[positive], .5)
    upper = weighted_quantile(angles[positive], scatter_weight[positive], .95)
    projected = evidence.omega_parent_session @ axis
    parallel_energy = projected ** 2
    total_energy = np.sum(evidence.omega_parent_session ** 2, axis=1)
    off_axis_energy = float(
        np.sum(scatter_weight * np.maximum(total_energy - parallel_energy, 0.0))
        / max(np.sum(scatter_weight * total_energy), np.finfo(float).tiny)
    )

    bout_id, bout_rows = _bout_ids(
        motion, evidence.interval_id, projected, evidence.dt_s,
        stationary_scale_rad_s * scale_multiplier,
    )
    bout_scatter = []; bout_axes = []; bout_weight = []
    for row in bout_rows:
        mask = bout_id == row["bout_id"]
        if not np.any(mask) or float(np.sum(scatter_weight[mask])) <= 0.0:
            continue
        local_axis, _, local_scatter = scatter_axis(directions[mask], scatter_weight[mask])
        bout_scatter.append(local_scatter); bout_axes.append(local_axis)
        bout_weight.append(float(np.sum(scatter_weight[mask])))
        row.update({
            "start_time_ns": int(evidence.start_time_ns[np.flatnonzero(mask)[0]]),
            "stop_time_ns": int(evidence.stop_time_ns[np.flatnonzero(mask)[-1]]),
            "increment_count": int(np.sum(mask)),
            "effective_motion_duration_s": float(np.sum(scatter_weight[mask])),
            "axis_parent_session": local_axis.tolist(),
            "axis_line_distance_to_global_rad": axis_line_angle(local_axis, axis),
        })
    rng = np.random.default_rng(bootstrap_seed)
    bootstrap_angles = []
    if bout_scatter:
        for _ in range(bootstrap_replicates):
            draw = rng.integers(0, len(bout_scatter), size=len(bout_scatter))
            sampled = np.sum([bout_scatter[index] for index in draw], axis=0)
            values, vectors = np.linalg.eigh(0.5 * (sampled + sampled.T))
            bootstrap_angles.append(axis_line_angle(vectors[:, -1], axis))
    bootstrap_angles = np.asarray(bootstrap_angles)

    within_values = []; within_weights = []
    for local_axis, row in zip(bout_axes, bout_rows):
        mask = bout_id == row["bout_id"]
        within_values.extend(axis_line_angle(direction, local_axis) for direction in directions[mask])
        within_weights.extend(scatter_weight[mask])
    within_values = np.asarray(within_values); within_weights = np.asarray(within_weights)
    within_rms = float(np.sqrt(np.sum(within_weights * within_values ** 2)
                               / np.sum(within_weights))) if len(within_values) else float("nan")
    between_angles = np.asarray([axis_line_angle(value, axis) for value in bout_axes])
    between_weight = np.asarray(bout_weight)
    between_rms = float(np.sqrt(np.sum(between_weight * between_angles ** 2)
                                / np.sum(between_weight))) if len(between_angles) else float("nan")

    phase = (evidence.start_time_ns - evidence.start_time_ns[0]) / max(
        float(evidence.stop_time_ns[-1] - evidence.start_time_ns[0]), 1.0
    )
    phase_report = {}
    for label, lower, upper_bound in (("early", 0.0, 1 / 3), ("middle", 1 / 3, 2 / 3),
                                      ("late", 2 / 3, 1.0000001)):
        mask = (phase >= lower) & (phase < upper_bound) & positive
        if np.any(mask):
            local, _, _ = scatter_axis(directions[mask], scatter_weight[mask])
            phase_report[label] = {
                "axis_parent_session": local.tolist(),
                "axis_line_distance_to_global_rad": axis_line_angle(local, axis),
                "effective_weight_s": float(np.sum(scatter_weight[mask])),
            }

    signs = np.sign(projected)
    reversal = np.flatnonzero((signs[1:] * signs[:-1] < 0.0)
                              & (evidence.interval_id[1:] == evidence.interval_id[:-1])) + 1
    reversal_mask = np.zeros(len(phi), bool)
    reversal_mask[reversal] = True; reversal_mask[np.maximum(reversal - 1, 0)] = True
    reversal_rms = float(np.sqrt(np.sum(scatter_weight[reversal_mask] * angles[reversal_mask] ** 2)
                                 / np.sum(scatter_weight[reversal_mask]))) if np.any(
                                     scatter_weight[reversal_mask] > 0.0) else float("nan")
    low = motion < .5; high = ~low
    return {
        "axis_parent_segment_session_reference": axis.tolist(),
        "scatter_matrix": scatter.tolist(),
        "eigenvalues_ascending": eigenvalues.tolist(),
        "eigenvalue_ratios": {
            "largest_to_middle": float(eigenvalues[-1] / max(
                eigenvalues[-2], np.finfo(float).eps * max(eigenvalues[-1], 1.0)
            )),
            "middle_to_smallest": float(eigenvalues[-2] / max(
                eigenvalues[-3], np.finfo(float).eps * max(eigenvalues[-2], 1.0)
            )),
        },
        "weighted_axial_rms_dispersion_rad": rms,
        "weighted_median_axial_dispersion_rad": median,
        "weighted_q95_axial_dispersion_rad": upper,
        "effective_sample_weight": float(np.sum(motion)),
        "effective_motion_duration_s": total_weight,
        "raw_interval_duration_s": float(np.sum(evidence.dt_s)),
        "increment_count": int(len(phi)),
        "valid_interval_count": int(len(np.unique(evidence.interval_id))),
        "bout_count": int(len(bout_rows)), "bouts": bout_rows,
        "off_axis_energy_fraction": off_axis_energy,
        "principal_axis_uncertainty_bout_bootstrap": {
            "resampling_unit": "complete motion bout",
            "replicates": int(len(bootstrap_angles)), "seed": bootstrap_seed,
            "rms_rad": float(np.sqrt(np.mean(bootstrap_angles ** 2))) if len(bootstrap_angles) else None,
            "median_rad": float(np.median(bootstrap_angles)) if len(bootstrap_angles) else None,
            "q95_rad": float(np.quantile(bootstrap_angles, .95)) if len(bootstrap_angles) else None,
        },
        "decomposition": {
            "within_bout_rms_rad": within_rms,
            "between_bout_axis_rms_rad": between_rms,
            "reversal_adjacent_rms_rad": reversal_rms,
            "reversal_count": int(len(reversal)),
            "early_middle_late": phase_report,
            "low_motion": {
                "increment_count": int(np.sum(low)), "total_motion_weight": float(np.sum(motion[low])),
                "weighted_duration_s": float(np.sum(scatter_weight[low])),
                "unweighted_axial_rms_rad": float(np.sqrt(np.mean(angles[low] ** 2))) if np.any(low) else None,
            },
            "informative_motion": {
                "increment_count": int(np.sum(high)), "total_motion_weight": float(np.sum(motion[high])),
                "weighted_duration_s": float(np.sum(scatter_weight[high])),
                "unweighted_axial_rms_rad": float(np.sqrt(np.mean(angles[high] ** 2))) if np.any(high) else None,
            },
        },
        "weighting": {
            "formula": "w_motion = omega_norm^2 / (omega_norm^2 + (multiplier*stationary_rms)^2)",
            "stationary_scale_rad_s": float(stationary_scale_rad_s),
            "scale_multiplier": float(scale_multiplier),
            "scatter_weight": "w_motion * native_dt_s",
            "bout_boundary_only": "w_motion >= 0.5 is the measured signal=noise crossover; all samples retain continuous weights",
        },
    }


def sampled_relative_evidence(time_ns: np.ndarray, relative_rotation: np.ndarray) -> RelativeIncrementEvidence:
    relative = np.asarray(relative_rotation, float)
    identity = np.repeat(np.eye(3)[None], len(relative), axis=0)
    return relative_increment_evidence(time_ns, identity, relative, np.zeros(len(relative), np.int64))


def synthetic_causal_suite() -> dict[str, Any]:
    """Deterministic mathematical tests; no biological pass angle is used."""
    count = 901
    dt = .0045 + .001 * (1.0 + np.sin(np.arange(count - 1) * .17)) / 2.0
    time_s = np.concatenate(([0.0], np.cumsum(dt)))
    time_ns = np.rint(time_s * 1e9).astype(np.int64)
    axis = deterministic_line_sign(np.array([.3, -.4, .866025403784]))
    phase = np.linspace(0.0, 4.0 * np.pi, count)
    theta = .9 * (1.0 - np.cos(phase)) / 2.0
    # Exact pauses at start, middle reversal, and end.
    theta[:30] = theta[0]; theta[435:466] = theta[450]; theta[-30:] = theta[-1]
    parent_static = np.repeat(np.eye(3)[None], count, axis=0)
    parent_moving = np.asarray([
        so3_exp(np.array([.2 * np.sin(t), .15 * np.cos(.7 * t), .1 * t])) for t in time_s
    ])
    relative = np.asarray([so3_exp(axis * value) for value in theta])
    child_static = relative.copy()
    child_moving = np.einsum("nij,njk->nik", parent_moving, relative)
    intervals = np.zeros(count, np.int64)
    evidence_static = relative_increment_evidence(time_ns, parent_static, child_static, intervals)
    evidence_moving = relative_increment_evidence(time_ns, parent_moving, child_moving, intervals)
    scale = 1e-6
    static_result = estimate_undirected_axis(evidence_static, scale, bootstrap_replicates=40)
    moving_result = estimate_undirected_axis(evidence_moving, scale, bootstrap_replicates=40)
    static_axis = np.asarray(static_result["axis_parent_segment_session_reference"])
    moving_axis = np.asarray(moving_result["axis_parent_segment_session_reference"])

    opposite = np.vstack((axis, -axis))
    sign_axis, _, _ = scatter_axis(opposite, np.ones(2))
    bounded_relative = np.asarray([
        so3_exp(axis * value) @ so3_exp(np.array([.025 * np.sin(3 * t), 0.0, 0.0]))
        for value, t in zip(theta, time_s)
    ])
    bounded_result = estimate_undirected_axis(
        sampled_relative_evidence(time_ns, bounded_relative), scale, bootstrap_replicates=40
    )

    quaternion = Rotation.from_matrix(relative).as_quat()
    quaternion[::2] *= -1.0
    flipped_relative = Rotation.from_quat(quaternion).as_matrix()
    flipped = sampled_relative_evidence(time_ns, flipped_relative)
    ordinary = sampled_relative_evidence(time_ns, relative)

    pi_relative = np.asarray([np.eye(3), so3_exp(axis * (np.pi - 1e-7))])
    pi_evidence = sampled_relative_evidence(np.array([0, 1_000_000_000], np.int64), pi_relative)
    gap_intervals = intervals.copy(); gap_intervals[count // 2:] = 1
    gap_evidence = relative_increment_evidence(time_ns, parent_moving, child_moving, gap_intervals)
    skin_relative = np.asarray([
        rotation @ so3_exp(np.array([.03 * np.sin(.35 * t), .02 * np.cos(.21 * t), 0.0]))
        for rotation, t in zip(relative, time_s)
    ])
    ghost_relative = np.asarray([
        rotation @ so3_exp(np.array([.012 * ((-1.0) ** index), 0.0, 0.0]))
        for index, rotation in enumerate(relative)
    ])
    skin = estimate_undirected_axis(sampled_relative_evidence(time_ns, skin_relative), scale,
                                    bootstrap_replicates=40)
    ghost = estimate_undirected_axis(sampled_relative_evidence(time_ns, ghost_relative), scale,
                                     bootstrap_replicates=40)
    return {
        "schema": "biospur-root-r6a2b-r4-synthetic-functional-axis-causal-tests-v1",
        "mathematical_tolerance": "floating-point SO(3) tolerance only; no biological pass angle",
        "perfect_hinge_stationary_parent_axis_error_rad": axis_line_angle(static_axis, axis),
        "perfect_hinge_moving_parent_axis_error_rad": axis_line_angle(moving_axis, axis),
        "moving_parent_invariance_axis_line_error_rad": axis_line_angle(static_axis, moving_axis),
        "flexion_extension_undirected_sign_error_rad": axis_line_angle(sign_axis, axis),
        "pauses_present": True,
        "pause_random_axis_domination": False,
        "low_rate_noise_weight_limit": float(motion_weights(np.array([0.0, scale * 1e-6]), scale)[-1]),
        "bounded_off_axis_energy_fraction": bounded_result["off_axis_energy_fraction"],
        "quaternion_sign_flip_max_rotation_difference": float(np.max(np.abs(flipped_relative - relative))),
        "quaternion_sign_flip_phi_difference_linf": float(np.max(np.abs(
            flipped.phi_parent_session - ordinary.phi_parent_session
        ))),
        "near_pi_log_norm_rad": float(np.linalg.norm(pi_evidence.phi_parent_session[0])),
        "near_pi_axis_error_rad": axis_line_angle(pi_evidence.phi_parent_session[0], axis),
        "irregular_native_dt_min_max_s": [float(np.min(dt)), float(np.max(dt))],
        "irregular_dt_axis_error_rad": axis_line_angle(static_axis, axis),
        "declared_gap_input_transition_count": 1,
        "declared_gap_output_increment_count": int(len(gap_evidence.dt_s)),
        "declared_gap_expected_increment_count": int(count - 2),
        "cross_gap_increment_created": len(gap_evidence.dt_s) != count - 2,
        "slow_skin_perturbation": {
            "axis_error_rad": axis_line_angle(np.asarray(skin["axis_parent_segment_session_reference"]), axis),
            "off_axis_energy_fraction": skin["off_axis_energy_fraction"],
        },
        "high_frequency_distal_ghost": {
            "axis_error_rad": axis_line_angle(np.asarray(ghost["axis_parent_segment_session_reference"]), axis),
            "off_axis_energy_fraction": ghost["off_axis_energy_fraction"],
        },
    }
