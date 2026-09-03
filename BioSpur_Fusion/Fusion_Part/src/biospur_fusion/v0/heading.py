"""Soft qmt-informed relative-heading stabilization for human limbs."""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import qmt

from .math3d import matrix_to_quat_wxyz, quat_wxyz_to_matrix, rz


QMT_JOINTS = {
    "elbow_left": ("upper_arm_left", "forearm_left"),
    "elbow_right": ("upper_arm_right", "forearm_right"),
    "knee_left": ("thigh_left", "shank_left"),
    "knee_right": ("thigh_right", "shank_right"),
}


def apply_soft_qmt_heading(
    time_ns: np.ndarray,
    segment_rotation: Mapping[str, np.ndarray],
    segment_gyro: Mapping[str, np.ndarray],
    functional_axes: Mapping[str, Mapping[str, Any]],
    settings: Mapping[str, Any],
    segment_degraded: Mapping[str, np.ndarray] | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, Any]]:
    """Apply a confidence-scaled qmt estimate, never a hard hinge model.

    qmt's mature one-dimensional constraint is used only to estimate heading.
    The measured human functional-axis dispersion controls a deliberately
    bounded correction gain; all three relative-joint coordinates survive.
    """
    times = np.asarray(time_ns, np.int64)
    if len(times) < 3 or np.any(np.diff(times) <= 0):
        raise ValueError("qmt input time must be strictly increasing")
    dt = np.diff(times) * 1e-9
    if not np.allclose(dt, dt[0], rtol=0, atol=1e-9):
        raise ValueError("qmt wrapper requires the explicit uniform V0 output grid")
    t = np.arange(len(times), dtype=float) * float(dt[0])
    corrected = {name: np.asarray(value, float).copy() for name, value in segment_rotation.items()}
    confidence_by_segment = {
        name: np.ones(len(times), float) for name in segment_rotation
    }
    if segment_degraded is None:
        segment_degraded = {
            name: np.zeros(len(times), bool) for name in segment_rotation
        }
    report: dict[str, Any] = {
        "schema": "biospur-fusion-v0-soft-qmt-heading-v1",
        "qmt_version": "0.2.4",
        "global_yaw_gauge": "ONE_UNOBSERVABLE_COMMON_YAW_RETAINED",
        "hard_hinge_used": False,
        "full_so3_retained": True,
        "joints": {},
    }
    for joint, (parent, child) in QMT_JOINTS.items():
        evidence = functional_axes[joint]
        axis = np.asarray(evidence["axis_parent_segment_session_reference"], float)
        axis /= np.linalg.norm(axis)
        dispersion_rad = np.deg2rad(float(evidence["weighted_rms_dispersion_deg"]))
        # Broad human-axis dispersion weakens, rather than disables or hardens,
        # the factor. This is a confidence mapping, not a PASS threshold.
        axis_confidence = float(max(0.0, np.cos(dispersion_rad)) ** 2)
        valid = ~(
            np.asarray(segment_degraded[parent], bool)
            | np.asarray(segment_degraded[child], bool)
        )
        # Execute qmt independently on contiguous valid blocks. A degraded
        # zero-order hold is output continuity, never motion evidence or a
        # bridge for qmt's internal bias/heading state.
        delta = np.zeros(len(times), float); delta_filtered = np.zeros(len(times), float)
        rating_clipped = np.zeros(len(times), float); state = np.zeros(len(times), int)
        executed_blocks = 0; skipped_short_blocks = 0
        edges = np.diff(np.r_[False, valid, False].astype(np.int8))
        for block_start, block_stop in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            duration = float((block_stop - block_start - 1) * dt[0])
            if block_stop - block_start < 3 or duration <= 2.0:
                skipped_short_blocks += 1
                continue
            block = slice(int(block_start), int(block_stop))
            block_t = np.arange(block_stop - block_start, dtype=float) * float(dt[0])
            window_time = min(
                float(settings["window_time_s"]),
                max(2.0, duration - float(dt[0])),
            )
            est = {
                "windowTime": window_time,
                "estimationRate": float(settings["estimation_rate_hz"]),
                "dataRate": float(settings["data_rate_hz"]),
                "tauDelta": float(settings["tau_delta_s"]),
                "tauBias": float(settings["tau_bias_s"]),
                "ratingMin": float(settings["rating_min"]),
                "alignment": "backward",
                "enableStillness": True,
                "constraint": "proj",
            }
            q1 = matrix_to_quat_wxyz(segment_rotation[parent][block])
            q2 = matrix_to_quat_wxyz(segment_rotation[child][block])
            _quat_exact, block_delta, block_filtered, block_rating, block_state = qmt.headingCorrection(
                np.ascontiguousarray(segment_gyro[parent][block]),
                np.ascontiguousarray(segment_gyro[child][block]),
                np.ascontiguousarray(q1), np.ascontiguousarray(q2), block_t,
                axis, {}, estSettings=est,
            )
            delta[block] = np.asarray(block_delta, float)
            delta_filtered[block] = np.asarray(block_filtered, float)
            rating_clipped[block] = np.clip(np.asarray(block_rating, float), 0.0, 1.0)
            state[block] = np.asarray(block_state).astype(int)
            executed_blocks += 1
        delta_unwrapped = np.zeros(len(times), float)
        for block_start, block_stop in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            delta_unwrapped[block_start:block_stop] = np.unwrap(delta_filtered[block_start:block_stop])
        confidence = float(settings["maximum_soft_gain"]) * axis_confidence * rating_clipped
        confidence = np.where(valid, confidence, 0.0)
        applied = confidence * delta_unwrapped
        estimated_remaining = (1.0 - confidence) * delta_unwrapped
        child_q = matrix_to_quat_wxyz(corrected[child])
        applied_q = qmt.qmult(qmt.quatFromAngleAxis(applied, [0, 0, 1]), child_q)
        corrected[child] = quat_wxyz_to_matrix(applied_q)
        evidence_confidence = axis_confidence * rating_clipped
        confidence_by_segment[child] = np.minimum(
            confidence_by_segment[child], np.where(valid, evidence_confidence, 0.0)
        )
        report["joints"][joint] = {
            "parent": parent,
            "child": child,
            "qmt_constraint": "proj_1d_used_as_heading_evidence_only",
            "axis_parent_segment_session_reference": axis.tolist(),
            "measured_weighted_rms_dispersion_deg": float(np.rad2deg(dispersion_rad)),
            "axis_confidence": axis_confidence,
            "maximum_soft_gain": float(settings["maximum_soft_gain"]),
            "rating_median": float(np.median(rating_clipped)),
            "rating_q95": float(np.quantile(rating_clipped, 0.95)),
            "raw_delta_range_deg": [float(np.degrees(np.min(delta))), float(np.degrees(np.max(delta)))],
            "applied_delta_rms_deg": float(np.degrees(np.sqrt(np.mean(applied ** 2)))),
            "applied_delta_max_abs_deg": float(np.degrees(np.max(np.abs(applied)))),
            "estimated_heading_inconsistency_rms_before_deg": float(
                np.degrees(np.sqrt(np.mean(delta_unwrapped[valid] ** 2))) if np.any(valid) else 0.0
            ),
            "estimated_heading_inconsistency_rms_after_deg": float(
                np.degrees(np.sqrt(np.mean(estimated_remaining[valid] ** 2))) if np.any(valid) else 0.0
            ),
            "maximum_applied_step_deg": float(np.degrees(np.max(np.abs(np.diff(applied))))),
            "degraded_samples_not_used": int(np.count_nonzero(~valid)),
            "contiguous_qmt_blocks_executed": executed_blocks,
            "short_valid_blocks_skipped": skipped_short_blocks,
            "cross_gap_qmt_state_composition": False,
            "qmt_state_counts": {
                str(int(value)): int(np.count_nonzero(np.asarray(state).astype(int) == value))
                for value in np.unique(np.asarray(state).astype(int))
            },
        }
    report["executed_joint_count"] = len(report["joints"])
    report["relative_heading_executed"] = len(report["joints"]) == 4
    report["confidence_wired_to_shared_ik"] = True
    return corrected, confidence_by_segment, report
