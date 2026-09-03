"""Short, evidence-first C2 avatar baseline.

This module intentionally separates an engineering avatar baseline from the
research progressive solver.  It uses one capture-wide VQF state per sensor,
an explicit initial standing reset, a final-standing slow-yaw closure, and the
official QMT 1-D heading primitive for the four hinge joints.  It never uses
sensor origins as joints and never runs IK, retargeting, rebase, or viewer-side
repair.

The final-standing closure is an offline full-capture replay measurement.  It
must therefore be reported as a diagnostic baseline, not as an online or
scientific progressive PASS.
"""

from __future__ import annotations

import contextlib
import io
import math
import time
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import qmt
from qmt.functions.heading_correction import estimateDelta1d
from scipy.spatial.transform import Rotation

from .contracts import EDGES, SEGMENT_TO_NODE
from .estimator import SEGMENTS, _PersistentHeadingState, aligned_spans_for_episode
from .frontend import EpisodeFrontend
from .math_utils import (
    interp_quat_wxyz,
    normalize_quat_wxyz,
    qmt_wxyz_to_rotation,
    rotation_to_qmt_wxyz,
    wrap_pi,
)


FINAL_STILL_INDEX = 16
HINGE_CALIBRATION_WINDOWS: dict[str, tuple[tuple[int, float, float], ...]] = {
    # The elbow recordings contain two different mechanisms.  Only the first
    # formal 15 seconds are elbow flexion/extension; the second half is
    # forearm pronation/supination and must not enter a 1-DoF elbow-axis fit.
    "elbow_left": ((5, 5.0, 20.0),),
    "elbow_right": ((6, 5.0, 20.0),),
    # Hip raises deliberately hold the knee near 90 degrees, so they provide
    # little knee-axis information.  Keep the direct knee motions, squat and
    # corresponding heel-to-butt action as the complete strong windows.
    "knee_left": ((9, 5.0, 35.0), (15, 5.0, 35.0), (17, 5.0, 35.0)),
    "knee_right": ((10, 5.0, 35.0), (15, 5.0, 35.0), (18, 5.0, 35.0)),
}
HINGE_FUNCTIONAL_FORWARD_SIGN = {
    ("elbow_left", 5): 1.0,
    ("elbow_right", 6): 1.0,
    ("knee_left", 9): 1.0,
    ("knee_right", 10): 1.0,
    ("knee_left", 15): -1.0,
    ("knee_right", 15): -1.0,
    ("knee_left", 17): -1.0,
    ("knee_right", 18): -1.0,
}
HINGE_SEGMENTS = {
    edge.name: (edge.parent, edge.child)
    for edge in EDGES
    if edge.joint_kind == "hinge"
}
HIP_CALIBRATION_EPISODES: dict[str, tuple[int, ...]] = {
    "hip_left": (7, 9, 10, 15),
    "hip_right": (8, 9, 10, 15),
}
HIP_SEGMENTS = {
    "hip_left": ("thigh_left", "shank_left"),
    "hip_right": ("thigh_right", "shank_right"),
}

TORSO_SUBTREE = ("torso", "upper_arm_left", "forearm_left", "upper_arm_right", "forearm_right")
SHOULDER_SUBTREES = {
    "left": ("upper_arm_left", "forearm_left"),
    "right": ("upper_arm_right", "forearm_right"),
}
SHOULDER_PLANE_EPISODES = {
    "left": (1, 3),
    "right": (1, 4),
}
SHOULDER_FACTOR_SIGMA_DEG = {
    "COMPLETE_T_POSE_SIDE_PLANE": 12.0,
    "COMPLETE_SHOULDER_RAISE_SIDE_PLANE": 18.0,
    "OLSSON_ELBOW_AXIS_LATERAL_LINE": 10.0,
}
TRUNK_AXIAL_ROTATION_EPISODE = 14


def _unit(value: np.ndarray) -> np.ndarray:
    vector = np.asarray(value, dtype=float).reshape(3)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm < 1e-9:
        raise ValueError("zero or non-finite axis")
    return vector / norm


def _frame_with_x_axis(axis: np.ndarray) -> np.ndarray:
    """Return a right-handed factor frame whose +X is ``axis``."""

    x_axis = _unit(axis)
    reference = (
        np.array([0.0, 0.0, 1.0])
        if abs(float(x_axis[2])) < 0.9
        else np.array([0.0, 1.0, 0.0])
    )
    y_axis = _unit(np.cross(reference, x_axis))
    z_axis = _unit(np.cross(x_axis, y_axis))
    return np.column_stack([x_axis, y_axis, z_axis])


@dataclass(frozen=True)
class PoseResetCalibration:
    initial_world_sensor: dict[str, np.ndarray]
    final_world_sensor: dict[str, np.ndarray]
    yaw_closure_rad: dict[str, float]
    reference_time_s: float
    final_reference_time_s: float
    still_sample_count: dict[str, dict[str, int]]

    def audit(self) -> dict[str, Any]:
        duration = self.final_reference_time_s - self.reference_time_s
        return {
            "schema": "biospur-c2-standard-pose-reset-calibration-v1",
            "initial_pose": "NATURAL_STANDING_LONG_AXIS_REFERENCE",
            "initial_axial_twist_claimed_observable": False,
            "final_pose": "NATURAL_STANDING_SLOW_YAW_CLOSURE",
            "offline_full_capture_replay": True,
            "online_progressive_pass_claimed": False,
            "reference_interval_s": float(duration),
            "yaw_closure_deg": {
                segment: float(math.degrees(value))
                for segment, value in self.yaw_closure_rad.items()
            },
            "yaw_rate_deg_per_min": {
                segment: float(math.degrees(value) * 60.0 / duration)
                for segment, value in self.yaw_closure_rad.items()
            },
            "still_sample_count": self.still_sample_count,
        }


def _segment_series(episode: EpisodeFrontend, segment: str):
    return episode.nodes[SEGMENT_TO_NODE[segment]]


def _clock_offsets_to_pelvis(episode: EpisodeFrontend) -> dict[str, float]:
    children: dict[str, list[tuple[str, str]]] = {}
    for edge in EDGES:
        children.setdefault(edge.parent, []).append((edge.child, edge.name))
    offsets = {"pelvis": 0.0}
    pending = ["pelvis"]
    while pending:
        parent = pending.pop(0)
        for child, edge_name in children.get(parent, []):
            timing = episode.pair_alignment_reports[edge_name][
                "corresponding_timing_span_windows"
            ]
            offsets[child] = offsets[parent] + float(
                timing["predicted_parent_minus_child_offset_s"]
            )
            pending.append(child)
    return offsets


def _quat_on_pelvis_time(
    episode: EpisodeFrontend,
    segment: str,
    pelvis_time_s: np.ndarray,
) -> np.ndarray:
    series = _segment_series(episode, segment)
    node_time_s = (
        series.time_us.astype(float) * 1e-6
        + _clock_offsets_to_pelvis(episode)[segment]
    )
    return interp_quat_wxyz(
        node_time_s,
        series.quat_world_sensor_wxyz,
        pelvis_time_s,
    )


def _robust_still_mean(episode: EpisodeFrontend, segment: str) -> tuple[np.ndarray, int]:
    series = _segment_series(episode, segment)
    gyro_norm = np.linalg.norm(series.gyro_rads, axis=1)
    acc_norm = np.linalg.norm(series.acc_mps2, axis=1)
    gyro_limit = float(np.quantile(gyro_norm, 0.60))
    acc_deviation = np.abs(acc_norm - float(np.median(acc_norm)))
    acc_limit = float(np.quantile(acc_deviation, 0.80))
    mask = (gyro_norm <= gyro_limit) & (acc_deviation <= acc_limit)
    if int(np.sum(mask)) < 100:
        raise RuntimeError(f"insufficient robust still support for {segment}")
    mean = qmt_wxyz_to_rotation(
        series.quat_world_sensor_wxyz[mask]
    ).mean().as_matrix()
    return mean, int(np.sum(mask))


def estimate_pose_reset_calibration(
    episodes: Sequence[EpisodeFrontend],
) -> PoseResetCalibration:
    if len(episodes) <= FINAL_STILL_INDEX:
        raise ValueError("initial and final C2 standing episodes are required")
    initial: dict[str, np.ndarray] = {}
    final: dict[str, np.ndarray] = {}
    counts = {"initial": {}, "final": {}}
    closure: dict[str, float] = {}
    for segment in SEGMENTS:
        initial[segment], counts["initial"][segment] = _robust_still_mean(
            episodes[0], segment
        )
        final[segment], counts["final"][segment] = _robust_still_mean(
            episodes[FINAL_STILL_INDEX], segment
        )
        residual = final[segment] @ initial[segment].T
        closure[segment] = float(math.atan2(residual[1, 0], residual[0, 0]))
    t0 = float(np.median(_segment_series(episodes[0], "pelvis").time_us)) * 1e-6
    tf = float(
        np.median(_segment_series(episodes[FINAL_STILL_INDEX], "pelvis").time_us)
    ) * 1e-6
    if tf <= t0:
        raise RuntimeError("non-positive C2 standing-reference interval")
    return PoseResetCalibration(initial, final, closure, t0, tf, counts)


def _yaw_correction_matrix(angle_rad: np.ndarray) -> np.ndarray:
    rotvec = np.zeros((len(angle_rad), 3), dtype=float)
    rotvec[:, 2] = angle_rad
    return Rotation.from_rotvec(rotvec).as_matrix()


def _episode_relative_time(time_s: np.ndarray) -> np.ndarray:
    values = np.asarray(time_s, dtype=float)
    return values - float(values[0])


def _window_inside_registered_interval(
    edge_name: str,
    episode_index: int,
    relative_time_s: np.ndarray,
    begin: int,
    end: int,
) -> bool:
    start = float(relative_time_s[begin])
    stop = float(relative_time_s[end - 1])
    return any(
        registered_episode == episode_index
        and start >= registered_start
        and stop <= registered_stop
        for registered_episode, registered_start, registered_stop
        in HINGE_CALIBRATION_WINDOWS[edge_name]
    )


def _rotate_trajectory_subtree(
    trajectory: dict[str, Any],
    episode_key: str,
    segments: Sequence[str],
    correction_angle_rad: np.ndarray,
) -> None:
    correction = _yaw_correction_matrix(np.asarray(correction_angle_rad, dtype=float))
    for segment in segments:
        row = trajectory["trajectory"][episode_key][segment]
        corrected = (
            correction
            @ qmt_wxyz_to_rotation(row["quat_world_segment_wxyz"]).as_matrix()
        )
        row["quat_world_segment_wxyz"] = normalize_quat_wxyz(
            rotation_to_qmt_wxyz(Rotation.from_matrix(corrected))
        )


def build_pose_reset_trajectory(
    episodes: Sequence[EpisodeFrontend],
    calibration: PoseResetCalibration,
    *,
    sample_step: int = 10,
) -> dict[str, Any]:
    """Build one continuous-state trajectory with one common yaw gauge.

    The reset fixes the renderer's segment-frame gauge.  The final standing
    observation estimates only the pelvis/world gauge trend; the same common
    rotation is applied to every segment.  Per-sensor endpoint closure is
    deliberately forbidden because it manufactures relative body twist.
    Relative headings are owned by the nine-edge functional update stages.
    """

    if sample_step <= 0:
        raise ValueError("sample_step must be positive")
    duration = calibration.final_reference_time_s - calibration.reference_time_s
    output: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    for episode in episodes:
        root = _segment_series(episode, "pelvis")
        grid = root.time_us.astype(float)[::sample_step] * 1e-6
        alpha = (grid - calibration.reference_time_s) / duration
        per_segment: dict[str, dict[str, np.ndarray]] = {}
        common_yaw_closure = calibration.yaw_closure_rad["pelvis"]
        for segment in SEGMENTS:
            quat = _quat_on_pelvis_time(episode, segment, grid)
            reset_matrix = (
                qmt_wxyz_to_rotation(quat).as_matrix()
                @ calibration.initial_world_sensor[segment].T
            )
            correction = _yaw_correction_matrix(
                -alpha * common_yaw_closure
            )
            matrix = correction @ reset_matrix
            per_segment[segment] = {
                "time_root_s": grid.copy(),
                "quat_world_segment_wxyz": normalize_quat_wxyz(
                    rotation_to_qmt_wxyz(Rotation.from_matrix(matrix))
                ),
                "mask": np.ones(len(grid), dtype=bool),
            }
        output[f"{episode.chronological_index:02d}"] = per_segment
    return {
        "schema": "biospur-c2-pose-reset-avatar-trajectory-v1",
        "trajectory": output,
        "single_capture_wide_orientation_state": True,
        "episode_orientation_reset_count": 0,
        "offline_final_standing_replay": True,
        "common_pelvis_yaw_gauge_only": True,
        "per_sensor_endpoint_yaw_closure_applied": False,
        "viewer_rebase_or_repair": False,
    }


def freeze_pose_reset_replay_calibration(
    trajectory: dict[str, Any],
    episodes: Sequence[EpisodeFrontend],
    calibration: PoseResetCalibration,
) -> dict[str, Any]:
    """Freeze the final C2 state for later holdout replay without refitting.

    All functional updates in this baseline are world-vertical yaw rotations.
    They commute and hold their latest state after the last registered factor.
    This function separates that final state from the raw sensor-to-segment
    reset so a later Capture2 holdout can consume the same calibration without
    opening its payload during fitting.
    """

    if not episodes:
        raise ValueError("at least one C2 training episode is required")
    final_episode = episodes[-1]
    final_key = f"{final_episode.chronological_index:02d}"
    duration = calibration.final_reference_time_s - calibration.reference_time_s
    functional_yaw: dict[str, float] = {}
    decomposition_audit: dict[str, Any] = {}
    for segment in SEGMENTS:
        row = trajectory["trajectory"][final_key][segment]
        grid = np.asarray(row["time_root_s"], dtype=float)
        raw_quat = _quat_on_pelvis_time(final_episode, segment, grid)
        alpha = (grid - calibration.reference_time_s) / duration
        base = (
            _yaw_correction_matrix(-alpha * calibration.yaw_closure_rad["pelvis"])
            @ qmt_wxyz_to_rotation(raw_quat).as_matrix()
            @ calibration.initial_world_sensor[segment].T
        )
        corrected = qmt_wxyz_to_rotation(
            row["quat_world_segment_wxyz"]
        ).as_matrix()
        delta = corrected @ np.swapaxes(base, 1, 2)
        angles = np.arctan2(delta[:, 1, 0], delta[:, 0, 0])
        tail_count = min(len(angles), max(20, len(angles) // 5))
        tail = angles[-tail_count:]
        # The replay state is causal and persistent: the state after the last
        # C2 training sample, not a retrospective average over an interval in
        # which the final registered factor may still be updating.
        frozen = float(angles[-1])
        functional_yaw[segment] = frozen
        off_axis = Rotation.from_matrix(delta).as_rotvec()[:, :2]
        tail_residual = np.angle(np.exp(1j * (tail - frozen)))
        decomposition_audit[segment] = {
            "tail_frame_count": int(tail_count),
            "freeze_owner": "LAST_C2_TRAINING_STATE",
            "frozen_functional_world_yaw_deg": float(math.degrees(frozen)),
            "tail_yaw_max_abs_residual_deg": float(
                np.degrees(np.max(np.abs(tail_residual)))
            ),
            "all_frame_off_axis_rotation_max_deg": float(
                np.degrees(np.max(np.linalg.norm(off_axis, axis=1)))
            ),
        }
    return {
        "schema": "biospur-c2-frozen-avatar-replay-calibration-v1",
        "training_scope": "CAPTURE2_19_REGISTERED_CALIBRATION_EPISODES_ONLY",
        "holdout_payload_used_during_fit": False,
        "holdout_refit_allowed": False,
        "segment_order": list(SEGMENTS),
        "initial_world_sensor": {
            segment: np.asarray(calibration.initial_world_sensor[segment], dtype=float)
            for segment in SEGMENTS
        },
        "common_pelvis_yaw_closure_rad": float(
            calibration.yaw_closure_rad["pelvis"]
        ),
        "reference_time_s": float(calibration.reference_time_s),
        "final_reference_time_s": float(calibration.final_reference_time_s),
        "functional_world_yaw_rad": functional_yaw,
        "decomposition_audit": decomposition_audit,
        "quaternion_order": "wxyz",
        "orientation_equation": (
            "R_world_segment=Yaw(functional_final)*Yaw(common_drift(t))*"
            "R_world_sensor*R_initial_world_sensor.T"
        ),
        "ik_rebase_retarget_or_repair": False,
    }


def _weighted_circular_mean(angle: np.ndarray, weight: np.ndarray) -> float:
    values = np.asarray(angle, dtype=float)
    weights = np.asarray(weight, dtype=float)
    total = float(np.sum(weights))
    if total <= 1e-12:
        return 0.0
    return float(math.atan2(
        np.sum(weights * np.sin(values)),
        np.sum(weights * np.cos(values)),
    ))


def _circular_concentration(angle: np.ndarray, weight: np.ndarray) -> float:
    values = np.asarray(angle, dtype=float)
    weights = np.asarray(weight, dtype=float)
    total = float(np.sum(weights))
    if total <= 1e-12:
        return 0.0
    sine = float(np.sum(weights * np.sin(values)))
    cosine = float(np.sum(weights * np.cos(values)))
    return float(math.hypot(sine, cosine) / total)


def _axial_heading_delta(
    axis_world: np.ndarray,
    target_world: np.ndarray,
    branch_reference_rad: float,
) -> tuple[float, dict[str, float]]:
    """Align two undirected 3-D axis lines around gravity.

    A fitted hinge axis is a line, not a directed arrow.  Double-angle fusion
    therefore keeps the physical pi ambiguity until the preceding persistent
    heading state selects the continuous branch.  All registered rows
    contribute with horizontal support; there is no exact-parallel sample
    selection.
    """

    axis = np.asarray(axis_world, dtype=float)
    target = np.asarray(target_world, dtype=float)
    support = (
        np.linalg.norm(axis[:, :2], axis=1)
        * np.linalg.norm(target[:, :2], axis=1)
    )
    weight = support * support
    angle = _signed_horizontal_angle(axis, target)
    sine = float(np.sum(weight * np.sin(2.0 * angle)))
    cosine = float(np.sum(weight * np.cos(2.0 * angle)))
    axial = 0.5 * math.atan2(sine, cosine)
    candidates = [float(wrap_pi(axial + k * math.pi)) for k in range(-2, 3)]
    resolved = min(
        candidates,
        key=lambda value: abs(float(wrap_pi(value - branch_reference_rad))),
    )
    concentration = math.hypot(sine, cosine) / max(float(np.sum(weight)), 1e-9)
    return resolved, {
        "row_count": int(len(axis)),
        "mean_horizontal_support": float(np.mean(support)),
        "double_angle_concentration": float(concentration),
        "resolved_delta_deg": float(math.degrees(resolved)),
        "branch_reference_deg": float(math.degrees(branch_reference_rad)),
    }


def _select_upper_arm_close_body_branch(
    axial_delta_rad: float,
    upper_direction_world: np.ndarray,
    torso_outward_world: np.ndarray,
    branch_reference_rad: float,
) -> tuple[float, dict[str, Any]]:
    """Audit the close-body cue without overriding a signed prior branch.

    The registered elbow manoeuvres move the forearm while the upper arm stays
    near the torso.  That qualitative cue cannot decide which end of an
    undirected Olsson axis is the anatomical positive direction: an upper arm
    can naturally lean slightly inward or outward while remaining close to the
    torso.  The preceding complete T-pose and shoulder-raise factors *are*
    directed evidence, so they own the pi branch.  The close-body projection is
    retained as an explicit conflict diagnostic instead of silently flipping a
    previously established branch by 180 degrees.
    """

    direction = np.asarray(upper_direction_world, dtype=float)
    outward = np.asarray(torso_outward_world, dtype=float)
    support = np.linalg.norm(direction[:, :2], axis=1) ** 2
    candidates = [
        float(wrap_pi(axial_delta_rad)),
        float(wrap_pi(axial_delta_rad + math.pi)),
    ]
    rows: list[dict[str, float]] = []
    for candidate in candidates:
        corrected = (
            _yaw_correction_matrix(np.full(len(direction), candidate))
            @ direction[..., None]
        )[..., 0]
        projection = np.sum(corrected * outward, axis=1)
        score = float(np.average(projection, weights=np.maximum(support, 1e-6)))
        rows.append({
            "delta_deg": float(math.degrees(candidate)),
            "mean_outward_projection": score,
            "continuity_to_directed_reference_deg": float(math.degrees(abs(
                float(wrap_pi(candidate - branch_reference_rad))
            ))),
        })
    selected_index = int(np.argmin([
        row["continuity_to_directed_reference_deg"] for row in rows
    ]))
    return candidates[selected_index], {
        "mechanism": (
            "DIRECTED_T_POSE_SHOULDER_BRANCH_WITH_CLOSE_BODY_AUDIT"
        ),
        "exact_upper_arm_angle_used": False,
        "contralateral_mirror_used": False,
        "close_body_cue_used_as_pi_branch_owner": False,
        "directed_branch_reference_deg": float(math.degrees(
            branch_reference_rad
        )),
        "candidates": rows,
        "selected_index": selected_index,
        "selected_delta_deg": rows[selected_index]["delta_deg"],
        # A pi yaw flip negates the horizontal projection but preserves its
        # magnitude.  "Close to the torso" therefore cannot distinguish the
        # two axis-line directions without inventing an inward/outward sign.
        "close_body_pi_branch_resolvable": False,
    }


def _factor_interval_replay(
    time_s: np.ndarray,
    factors: Sequence[dict[str, Any]],
) -> np.ndarray:
    """Replay factor states without anticipating evidence from the future.

    A complete registered factor may calibrate its own interval in an offline
    reconstruction.  Between factors, however, the latest available state is
    held: linearly ramping toward the next factor would make an earlier action
    depend on evidence that had not occurred yet and can manufacture motion in
    an otherwise unobserved gap.
    """

    if not factors:
        return np.zeros(len(time_s), dtype=float)
    ordered = sorted(factors, key=lambda row: row["start_time_s"])
    values = np.unwrap(
        np.asarray([row["filtered_delta_rad"] for row in ordered], dtype=float)
    )
    result = np.full(len(time_s), values[0], dtype=float)
    for index, row in enumerate(ordered):
        inside = (
            (time_s >= float(row["start_time_s"]))
            & (time_s <= float(row["stop_time_s"]))
        )
        result[inside] = values[index]
        if index == 0:
            result[time_s < float(row["start_time_s"])] = values[index]
        if index + 1 < len(ordered):
            next_row = ordered[index + 1]
            gap = (
                (time_s > float(row["stop_time_s"]))
                & (time_s < float(next_row["start_time_s"]))
            )
            result[gap] = values[index]
        else:
            result[time_s > float(row["stop_time_s"])] = values[index]
    return wrap_pi(result)


def _extended_hinge_replay_delta(
    parent_quat_wxyz: np.ndarray,
    child_quat_wxyz: np.ndarray,
) -> tuple[float, dict[str, float]]:
    """Resolve a hinge heading branch from a complete extended-limb interval.

    The T-pose interval is a functional elbow-extension observation.  It does
    not impose an exact world direction or perfect horizontal arm: it only
    states that upper arm and forearm continue in the same direction.  Every
    row contributes with continuous horizontal support, so sensor noise,
    natural droop and small human elbow flexion remain soft evidence.
    """

    parent_direction = qmt_wxyz_to_rotation(parent_quat_wxyz).apply(
        np.array([0.0, 0.0, -1.0])
    )
    child_direction = qmt_wxyz_to_rotation(child_quat_wxyz).apply(
        np.array([0.0, 0.0, -1.0])
    )
    support = (
        np.linalg.norm(parent_direction[:, :2], axis=1)
        * np.linalg.norm(child_direction[:, :2], axis=1)
    )
    delta_samples = _signed_horizontal_angle(
        child_direction, parent_direction
    )
    delta = _weighted_circular_mean(delta_samples, support * support)
    corrected_child = Rotation.from_matrix(
        _yaw_correction_matrix(np.full(len(child_direction), delta))
        @ qmt_wxyz_to_rotation(child_quat_wxyz).as_matrix()
    ).apply(np.array([0.0, 0.0, -1.0]))
    alignment = np.degrees(_angle(parent_direction, corrected_child))
    return delta, {
        "row_count": int(len(parent_direction)),
        "effective_horizontal_support": float(np.mean(support)),
        "resolved_delta_deg": float(math.degrees(delta)),
        "post_replay_alignment_median_deg": float(np.median(alignment)),
        "post_replay_alignment_p90_deg": float(np.quantile(alignment, 0.90)),
    }


def _flexed_elbow_replay_delta(
    torso_quat_wxyz: np.ndarray,
    upper_arm_quat_wxyz: np.ndarray,
    forearm_quat_wxyz: np.ndarray,
) -> tuple[float, dict[str, float]]:
    """Measure forearm heading from a complete flexed-elbow interval.

    During the registered second half of the elbow manoeuvre, the elbow is
    held near 90 degrees while pronation/supination excites axial twist.  That
    motion must not rotate the forearm long axis out of the torso sagittal
    plane.  Every row contributes continuously; upper-arm verticality and
    forearm horizontal support down-weight natural whole-body adjustments.
    """

    torso_rotation = qmt_wxyz_to_rotation(torso_quat_wxyz)
    torso_lateral = torso_rotation.apply(np.array([1.0, 0.0, 0.0]))
    torso_forward = np.cross(
        np.broadcast_to(np.array([0.0, 0.0, 1.0]), torso_lateral.shape),
        torso_lateral,
    )
    upper_direction = qmt_wxyz_to_rotation(upper_arm_quat_wxyz).apply(
        np.array([0.0, 0.0, -1.0])
    )
    forearm_direction = qmt_wxyz_to_rotation(forearm_quat_wxyz).apply(
        np.array([0.0, 0.0, -1.0])
    )
    horizontal_support = np.linalg.norm(forearm_direction[:, :2], axis=1)
    upper_vertical_support = np.abs(upper_direction[:, 2])
    weight = horizontal_support**2 * upper_vertical_support**2
    delta_samples = _signed_horizontal_angle(
        forearm_direction, torso_forward
    )
    delta = _weighted_circular_mean(delta_samples, weight)
    corrected = Rotation.from_matrix(
        _yaw_correction_matrix(np.full(len(forearm_direction), delta))
        @ qmt_wxyz_to_rotation(forearm_quat_wxyz).as_matrix()
    ).apply(np.array([0.0, 0.0, -1.0]))
    alignment = np.degrees(_angle(corrected, torso_forward))
    return delta, {
        "row_count": int(len(forearm_direction)),
        "effective_horizontal_vertical_support": float(np.mean(weight)),
        "resolved_delta_deg": float(math.degrees(delta)),
        "post_replay_forward_alignment_median_deg": float(
            np.median(alignment)
        ),
        "post_replay_forward_alignment_p90_deg": float(
            np.quantile(alignment, 0.90)
        ),
    }


def _qmt_axis_heading_mod_pi(
    updates: Sequence[dict[str, Any]],
    branch_reference_rad: float,
) -> tuple[float, dict[str, float]]:
    """Fuse QMT 1-D headings as axial observations with pi ambiguity."""

    usable = [
        row
        for row in updates
        if row["registered_functional_window"]
        and row["quality"] > 0.0
        and np.isfinite(row["delta_measurement_rad"])
    ]
    if not usable:
        return branch_reference_rad, {
            "row_count": 0,
            "double_angle_concentration": 0.0,
            "resolved_delta_deg": float(math.degrees(branch_reference_rad)),
        }
    angle = np.asarray(
        [row["delta_measurement_rad"] for row in usable], dtype=float
    )
    weight = np.asarray([row["quality"] for row in usable], dtype=float)
    sine = float(np.sum(weight * np.sin(2.0 * angle)))
    cosine = float(np.sum(weight * np.cos(2.0 * angle)))
    axial = 0.5 * math.atan2(sine, cosine)
    candidates = [axial + k * math.pi for k in range(-2, 3)]
    resolved = min(
        candidates,
        key=lambda value: abs(float(wrap_pi(value - branch_reference_rad))),
    )
    resolved = float(wrap_pi(resolved))
    concentration = math.hypot(sine, cosine) / max(float(np.sum(weight)), 1e-9)
    return resolved, {
        "row_count": len(usable),
        "double_angle_concentration": float(concentration),
        "resolved_delta_deg": float(math.degrees(resolved)),
        "branch_reference_deg": float(math.degrees(branch_reference_rad)),
    }


def apply_protocol_torso_heading_updates(
    trajectory: dict[str, Any],
    episodes: Sequence[EpisodeFrontend],
) -> dict[str, Any]:
    """Track pelvis-to-torso drift without suppressing real trunk rotation.

    Every non-axial-rotation episode contributes stable two-second windows to
    one capture-wide relative-heading state.  Within-window changes remain in
    the trajectory; only the slowly varying median offset is removed from the
    complete torso subtree.  This is deliberately broad human evidence, not a
    rigid-spine or exact-zero-twist fixture.
    """

    state = _PersistentHeadingState()
    audit_rows: list[dict[str, Any]] = []
    for episode in episodes:
        key = f"{episode.chronological_index:02d}"
        pelvis_row = trajectory["trajectory"][key]["pelvis"]
        torso_row = trajectory["trajectory"][key]["torso"]
        time_s = np.asarray(pelvis_row["time_root_s"], dtype=float)
        pelvis_lateral = qmt_wxyz_to_rotation(
            pelvis_row["quat_world_segment_wxyz"]
        ).apply(np.array([1.0, 0.0, 0.0]))
        torso_lateral = qmt_wxyz_to_rotation(
            torso_row["quat_world_segment_wxyz"]
        ).apply(np.array([1.0, 0.0, 0.0]))
        relative = _signed_horizontal_angle(pelvis_lateral, torso_lateral)
        unwrapped = np.unwrap(relative)
        dt = max(float(np.median(np.diff(time_s))), 1e-3)
        relative_rate = np.gradient(unwrapped, dt)
        measurement_time: list[float] = []
        filtered_correction: list[float] = []
        for begin in _qmt_window_starts(len(time_s)):
            end = min(len(time_s), begin + 40)
            rate_rms = float(np.sqrt(np.mean(relative_rate[begin:end] ** 2)))
            stability = float(math.exp(
                -0.5 * (rate_rms / math.radians(18.0)) ** 2
            ))
            support = np.minimum(
                np.linalg.norm(pelvis_lateral[begin:end, :2], axis=1),
                np.linalg.norm(torso_lateral[begin:end, :2], axis=1),
            )
            quality = float(stability * np.mean(support))
            if episode.chronological_index == TRUNK_AXIAL_ROTATION_EPISODE:
                quality = 0.0
            measured_offset = _weighted_circular_mean(
                relative[begin:end], np.maximum(support, 1e-6)
            )
            correction_measurement = float(wrap_pi(-measured_offset))
            variance = (
                math.radians(14.0) ** 2
                / max(quality * max((end - begin) / 20.0, 1.0), 0.03)
                if quality > 0.0
                else math.inf
            )
            update_time = float(time_s[end - 1])
            value, variance_out = state.update(
                update_time, correction_measurement, variance, quality
            )
            measurement_time.append(update_time)
            filtered_correction.append(value)
            audit_rows.append({
                "episode_index": episode.chronological_index,
                "time_s": update_time,
                "quality": quality,
                "relative_rate_rms_deg_s": math.degrees(rate_rms),
                "measured_torso_minus_pelvis_deg": math.degrees(measured_offset),
                "filtered_correction_deg": math.degrees(value),
                "variance_rad2": variance_out,
            })
        if measurement_time:
            correction = wrap_pi(np.interp(
                time_s,
                np.asarray(measurement_time),
                np.unwrap(np.asarray(filtered_correction)),
                left=filtered_correction[0],
                right=filtered_correction[-1],
            ))
        else:
            correction = np.full(len(time_s), state.delta_rad)
        _rotate_trajectory_subtree(trajectory, key, TORSO_SUBTREE, correction)
    positive = [row for row in audit_rows if row["quality"] > 0.0]
    result = {
        "mechanism": "CAPTURE_WIDE_STABLE_RELATIVE_HEADING_SOFT_STATE",
        "state_instances": 1,
        "episode_reset_count": 0,
        "excluded_axial_rotation_episode": TRUNK_AXIAL_ROTATION_EPISODE,
        "rigid_spine_or_exact_zero_twist_used": False,
        "window_count": len(audit_rows),
        "positive_quality_window_count": len(positive),
        "maximum_quality": float(max((row["quality"] for row in positive), default=0.0)),
        "final_correction_deg": math.degrees(state.delta_rad),
        "updates": audit_rows,
    }
    trajectory["protocol_torso_heading_updates"] = result
    return result


def apply_protocol_shoulder_plane_updates(
    trajectory: dict[str, Any],
    episodes: Sequence[EpisodeFrontend],
    hinge_axes: dict[str, tuple[np.ndarray, np.ndarray]],
) -> dict[str, Any]:
    """Fuse broad shoulder-plane and fitted elbow-axis information.

    The declared manoeuvres identify the arm's side/coronal plane but do not
    provide an exact elevation angle.  Horizontal support weights every row
    continuously, so vertical/down-arm rows become low information instead of
    being selected or rejected by a brittle threshold.  The later elbow
    flexion contributes an independent Olsson hinge-axis line: an anatomical
    elbow flexion axis is approximately lateral, but its sign is deliberately
    left unresolved.  The three complete-interval factors update one
    capture-wide state, and replay holds the state constant inside each
    evidence interval so the calibration cannot manufacture motion while the
    user is performing T-pose.
    """

    audit: dict[str, Any] = {}
    for side in ("left", "right"):
        state = _PersistentHeadingState()
        sign = -1.0 if side == "left" else 1.0
        subtree = SHOULDER_SUBTREES[side]
        uncorrected_quat = {
            f"{episode.chronological_index:02d}": {
                segment: np.asarray(
                    trajectory["trajectory"][
                        f"{episode.chronological_index:02d}"
                    ][segment]["quat_world_segment_wxyz"],
                    dtype=float,
                ).copy()
                for segment in subtree
            }
            for episode in episodes
        }
        factors: list[dict[str, Any]] = []
        for factor_index, episode_index in enumerate(
            SHOULDER_PLANE_EPISODES[side]
        ):
            key = f"{episode_index:02d}"
            torso_row = trajectory["trajectory"][key]["torso"]
            arm_row = trajectory["trajectory"][key][f"upper_arm_{side}"]
            time_s = np.asarray(torso_row["time_root_s"], dtype=float)
            relative_time_s = _episode_relative_time(time_s)
            formal = (relative_time_s >= 5.0) & (relative_time_s <= 35.0)
            if int(np.sum(formal)) < 100:
                raise RuntimeError(
                    f"insufficient complete shoulder-plane support for {side}"
                )
            torso_lateral = qmt_wxyz_to_rotation(
                torso_row["quat_world_segment_wxyz"]
            ).apply(np.array([sign, 0.0, 0.0]))
            arm_direction = qmt_wxyz_to_rotation(
                arm_row["quat_world_segment_wxyz"]
            ).apply(np.array([0.0, 0.0, -1.0]))
            correction_samples = _signed_horizontal_angle(
                arm_direction, torso_lateral
            )
            horizontal_support = np.linalg.norm(arm_direction[:, :2], axis=1)
            weight = horizontal_support[formal] ** 2
            measurement = _weighted_circular_mean(
                correction_samples[formal], weight
            )
            concentration = _circular_concentration(
                correction_samples[formal], weight
            )
            quality = float(np.mean(weight) * concentration)
            role = (
                "COMPLETE_T_POSE_SIDE_PLANE"
                if factor_index == 0
                else "COMPLETE_SHOULDER_RAISE_SIDE_PLANE"
            )
            factors.append({
                "episode_index": episode_index,
                "source_role": role,
                "start_time_s": float(time_s[formal][0]),
                "stop_time_s": float(time_s[formal][-1]),
                "measurement_time_s": float(np.median(time_s[formal])),
                "measurement_delta_rad": float(measurement),
                "quality": quality,
                "circular_concentration": float(concentration),
                "mean_horizontal_support": float(
                    np.mean(horizontal_support[formal])
                ),
                "base_sigma_deg": SHOULDER_FACTOR_SIGMA_DEG[role],
                "row_count": int(np.sum(formal)),
                "single_frame_selected": False,
            })

        # The elbow flexion axis is an undirected functional line.  It gives
        # the late-capture shoulder heading without assuming that the upper
        # arm is perfectly vertical or mirroring the contralateral arm.
        elbow_episode = 5 if side == "left" else 6
        elbow_edge = f"elbow_{side}"
        elbow_key = f"{elbow_episode:02d}"
        elbow_time = np.asarray(
            trajectory["trajectory"][elbow_key]["torso"]["time_root_s"],
            dtype=float,
        )
        elbow_relative = _episode_relative_time(elbow_time)
        elbow_formal = (elbow_relative >= 5.0) & (elbow_relative <= 20.0)
        if int(np.sum(elbow_formal)) < 100:
            raise RuntimeError(
                f"insufficient complete elbow-axis support for shoulder {side}"
            )
        torso_lateral = qmt_wxyz_to_rotation(
            trajectory["trajectory"][elbow_key]["torso"][
                "quat_world_segment_wxyz"
            ]
        ).apply(np.array([sign, 0.0, 0.0]))
        upper_rotation = qmt_wxyz_to_rotation(
            trajectory["trajectory"][elbow_key][f"upper_arm_{side}"][
                "quat_world_segment_wxyz"
            ]
        )
        axis_world = upper_rotation.apply(hinge_axes[elbow_edge][0])
        branch_reference = factors[-1]["measurement_delta_rad"]
        axis_delta, axis_audit = _axial_heading_delta(
            axis_world[elbow_formal],
            torso_lateral[elbow_formal],
            float(branch_reference),
        )
        upper_direction = upper_rotation.apply(np.array([0.0, 0.0, -1.0]))
        axis_delta, close_body_audit = _select_upper_arm_close_body_branch(
            axis_delta,
            upper_direction[elbow_formal],
            torso_lateral[elbow_formal],
            float(branch_reference),
        )
        role = "OLSSON_ELBOW_AXIS_LATERAL_LINE"
        axis_quality = float(
            axis_audit["mean_horizontal_support"] ** 2
            * axis_audit["double_angle_concentration"]
        )
        factors.append({
            "episode_index": elbow_episode,
            "source_role": role,
            "start_time_s": float(elbow_time[elbow_formal][0]),
            "stop_time_s": float(elbow_time[elbow_formal][-1]),
            "measurement_time_s": float(np.median(elbow_time[elbow_formal])),
            "measurement_delta_rad": float(axis_delta),
            "quality": axis_quality,
            "base_sigma_deg": SHOULDER_FACTOR_SIGMA_DEG[role],
            "row_count": int(np.sum(elbow_formal)),
            "single_frame_selected": False,
            "axis_line_audit": axis_audit,
            "axis_line_pi_branch_resolution": close_body_audit,
        })

        factors.sort(key=lambda row: row["measurement_time_s"])
        for factor in factors:
            base_variance = math.radians(
                float(factor["base_sigma_deg"])
            ) ** 2
            if factor["source_role"] == "OLSSON_ELBOW_AXIS_LATERAL_LINE":
                # The preceding complete directed actions own the pi branch.
                # The Olsson axis refines that surviving branch with finite
                # covariance; the qualitative close-body cue is audit-only.
                filtered = float(factor["measurement_delta_rad"])
                variance = float(
                    base_variance / max(float(factor["quality"]), 0.05)
                )
                state.delta_rad = filtered
                state.bias_rad_s = 0.0
                state.variance_rad2 = variance
                state.last_time_s = float(factor["measurement_time_s"])
                factor["directed_physical_branch_gate_applied"] = True
            else:
                filtered, variance = state.update(
                    float(factor["measurement_time_s"]),
                    float(factor["measurement_delta_rad"]),
                    base_variance,
                    float(factor["quality"]),
                )
                factor["directed_physical_branch_gate_applied"] = False
            factor["filtered_delta_rad"] = float(filtered)
            factor["filtered_delta_deg"] = float(math.degrees(filtered))
            factor["posterior_variance_rad2"] = float(variance)

        for episode in episodes:
            key = f"{episode.chronological_index:02d}"
            time_s = np.asarray(
                trajectory["trajectory"][key][subtree[0]]["time_root_s"],
                dtype=float,
            )
            correction_angle = _factor_interval_replay(time_s, factors)
            correction = _yaw_correction_matrix(correction_angle)
            for segment in subtree:
                corrected = (
                    correction
                    @ qmt_wxyz_to_rotation(
                        uncorrected_quat[key][segment]
                    ).as_matrix()
                )
                trajectory["trajectory"][key][segment][
                    "quat_world_segment_wxyz"
                ] = normalize_quat_wxyz(
                    rotation_to_qmt_wxyz(Rotation.from_matrix(corrected))
                )
        audit[side] = {
            "mechanism": (
                "CAPTURE_WIDE_SIDE_PLANE_PLUS_OLSSON_AXIS_SOFT_STATE"
            ),
            "pre_registered_complete_episodes": list(SHOULDER_PLANE_EPISODES[side]),
            "elbow_axis_episode": elbow_episode,
            "exact_elevation_or_pose_angle_used": False,
            "hard_bilateral_mirror_used": False,
            "state_instances": 1,
            "episode_reset_count": 0,
            "factor_count": len(factors),
            "positive_quality_factor_count": int(sum(
                row["quality"] > 0.0 for row in factors
            )),
            "maximum_quality": float(max(
                (row["quality"] for row in factors), default=0.0
            )),
            "final_correction_deg": math.degrees(state.delta_rad),
            "final_full_batch_replay_applied": True,
            "replayed_episode_count": len(episodes),
            "episode_specific_shoulder_profile_used": False,
            "state_held_constant_inside_factor_intervals": True,
            "static_correlation_accounting": (
                "ONE_COVARIANCE_UPDATE_PER_COMPLETE_FACTOR_INTERVAL"
            ),
            "factors": factors,
        }
    trajectory["protocol_shoulder_plane_updates"] = audit
    return audit


def _qmt_window_starts(row_count: int, width: int = 40, stride: int = 20) -> list[int]:
    if row_count < 16:
        return []
    if row_count <= width:
        return [0]
    starts = list(range(0, row_count - width + 1, stride))
    if starts[-1] + width < row_count:
        starts.append(row_count - width)
    return starts


def estimate_hinge_axes_olsson(
    episodes: Sequence[EpisodeFrontend],
    calibration: PoseResetCalibration,
) -> tuple[dict[str, tuple[np.ndarray, np.ndarray]], dict[str, Any]]:
    """Estimate the four real hinge axes from complete registered actions.

    QMT's Olsson implementation receives every aligned raw row from the
    pre-registered strong-excitation episodes.  Sample selection is disabled:
    no exact-parallel rows or desired-result subset is chosen.
    """

    axes: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    audit: dict[str, Any] = {}
    for edge_name, registered_windows in HINGE_CALIBRATION_WINDOWS.items():
        spans = []
        window_audit = []
        for episode_index, start_s, stop_s in registered_windows:
            edge_spans = [
                span
                for span in aligned_spans_for_episode(episodes[episode_index])
                if span.edge == edge_name
            ]
            if not edge_spans:
                continue
            episode_start_s = float(min(span.time_root_s[0] for span in edge_spans))
            for span in edge_spans:
                relative_time_s = span.time_root_s - episode_start_s
                mask = (relative_time_s >= start_s) & (relative_time_s <= stop_s)
                if not np.any(mask):
                    continue
                spans.append((span, mask))
                window_audit.append({
                    "episode_index": episode_index,
                    "registered_start_s": start_s,
                    "registered_stop_s": stop_s,
                    "row_count": int(np.sum(mask)),
                })
        if not spans:
            raise RuntimeError(f"no aligned functional rows for {edge_name}")
        parent_acc = np.concatenate([span.parent_acc_mps2[mask] for span, mask in spans])
        child_acc = np.concatenate([span.child_acc_mps2[mask] for span, mask in spans])
        parent_gyro = np.concatenate([span.parent_gyro_rads[mask] for span, mask in spans])
        child_gyro = np.concatenate([span.child_gyro_rads[mask] for span, mask in spans])
        started = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            parent_axis_sensor, child_axis_sensor = qmt.jointAxisEstHingeOlsson(
                parent_acc,
                child_acc,
                parent_gyro,
                child_gyro,
                {"useSampleSelection": False},
                debug=False,
                plot=False,
            )
        parent_segment, child_segment = HINGE_SEGMENTS[edge_name]
        # R_world_segment = R_world_sensor @ R_initial_world_sensor.T.
        # Therefore a sensor-local vector is mapped into the reset segment
        # frame by R_initial_world_sensor.
        parent_axis = _unit(
            calibration.initial_world_sensor[parent_segment]
            @ np.asarray(parent_axis_sensor).reshape(3)
        )
        child_axis = _unit(
            calibration.initial_world_sensor[child_segment]
            @ np.asarray(child_axis_sensor).reshape(3)
        )
        sign_flip = bool(float(np.dot(parent_axis, child_axis)) < 0.0)
        if sign_flip:
            child_axis = -child_axis
        parent_long_axis_fraction = float(abs(parent_axis[2]))
        child_long_axis_fraction = float(abs(child_axis[2]))
        gross_long_axis_conflict = bool(
            max(parent_long_axis_fraction, child_long_axis_fraction) > 0.75
        )
        if gross_long_axis_conflict:
            raise RuntimeError(
                f"{edge_name} Olsson axis follows the segment long axis; "
                "registered hinge window is not physically usable"
            )
        axes[edge_name] = (parent_axis, child_axis)
        audit[edge_name] = {
            "primitive": "qmt.jointAxisEstHingeOlsson_unmodified",
            "registered_windows": window_audit,
            "raw_aligned_row_count": int(len(parent_acc)),
            "qmt_sample_selection": False,
            "parent_axis_reset_segment": parent_axis.tolist(),
            "child_axis_reset_segment": child_axis.tolist(),
            "child_sign_flipped_for_common_line": sign_flip,
            "parent_long_axis_fraction": parent_long_axis_fraction,
            "child_long_axis_fraction": child_long_axis_fraction,
            "gross_long_axis_conflict": gross_long_axis_conflict,
            "wall_s": float(time.perf_counter() - started),
        }
    return axes, audit


def apply_qmt_hinge_soft_updates(
    trajectory: dict[str, Any],
    episodes: Sequence[EpisodeFrontend],
    hinge_axes: dict[str, tuple[np.ndarray, np.ndarray]],
) -> dict[str, Any]:
    """Apply official QMT 1-D estimates as four persistent soft streams.

    Selection is pre-registered by complete action windows.  Every two-second
    neighbourhood contributes continuously according to measured excitation;
    there is no exact-parallel row search and no hard low-information stop.

    The elbow pass can be replayed from the earlier complete T-pose observation
    and the later flexed-elbow observation.  Knee evidence begins only in the
    seated-knee manoeuvres, so its one persistent causal state is not projected
    backwards across earlier hip raises and unobserved inter-action gaps.
    Neither path creates an episode-local estimator or stitched calibration.
    """

    started = time.perf_counter()
    audit: dict[str, Any] = {}
    for edge_name, (parent, child) in HINGE_SEGMENTS.items():
        state = _PersistentHeadingState()
        updates: list[dict[str, Any]] = []
        uncorrected_child_quat: dict[str, np.ndarray] = {
            f"{episode.chronological_index:02d}": np.asarray(
                trajectory["trajectory"][f"{episode.chronological_index:02d}"][
                    child
                ]["quat_world_segment_wxyz"],
                dtype=float,
            ).copy()
            for episode in episodes
        }
        parent_factor = _frame_with_x_axis(hinge_axes[edge_name][0])
        child_factor = _frame_with_x_axis(hinge_axes[edge_name][1])
        for episode in episodes:
            key = f"{episode.chronological_index:02d}"
            parent_row = trajectory["trajectory"][key][parent]
            child_row = trajectory["trajectory"][key][child]
            time_s = np.asarray(parent_row["time_root_s"], dtype=float)
            parent_q = np.asarray(parent_row["quat_world_segment_wxyz"], dtype=float)
            child_q = np.asarray(child_row["quat_world_segment_wxyz"], dtype=float)
            relative_time_s = _episode_relative_time(time_s)
            reference_segment = (
                "torso" if edge_name.startswith("elbow_") else "pelvis"
            )
            body_lateral = qmt_wxyz_to_rotation(
                trajectory["trajectory"][key][reference_segment][
                    "quat_world_segment_wxyz"
                ]
            ).apply(np.array([1.0, 0.0, 0.0]))
            body_forward = np.cross(
                np.broadcast_to(np.array([0.0, 0.0, 1.0]), body_lateral.shape),
                body_lateral,
            )
            parent_factor_q = normalize_quat_wxyz(rotation_to_qmt_wxyz(
                Rotation.from_matrix(
                    qmt_wxyz_to_rotation(parent_q).as_matrix() @ parent_factor
                )
            ))
            child_factor_q = normalize_quat_wxyz(rotation_to_qmt_wxyz(
                Rotation.from_matrix(
                    qmt_wxyz_to_rotation(child_q).as_matrix() @ child_factor
                )
            ))
            measurement_time: list[float] = []
            filtered_delta: list[float] = []
            for begin in _qmt_window_starts(len(time_s)):
                end = min(len(time_s), begin + 40)
                relative = Rotation.from_matrix(
                    np.transpose(
                        qmt_wxyz_to_rotation(parent_q[begin:end]).as_matrix(),
                        (0, 2, 1),
                    )
                    @ qmt_wxyz_to_rotation(child_q[begin:end]).as_matrix()
                ).as_rotvec()
                increment = np.diff(relative, axis=0)
                excitation = float(
                    np.sqrt(np.mean(np.sum(increment * increment, axis=1)))
                )
                registered = _window_inside_registered_interval(
                    edge_name,
                    episode.chronological_index,
                    relative_time_s,
                    begin,
                    end,
                )
                try:
                    if not registered:
                        raise LookupError("propagation-only episode")
                    candidates = []
                    for initial in (state.delta_rad, state.delta_rad + math.pi):
                        delta, rating, cost = estimateDelta1d(
                            np.ascontiguousarray(parent_factor_q[begin:end]),
                            np.ascontiguousarray(child_factor_q[begin:end]),
                            np.array([1.0, 0.0, 0.0]),
                            initial,
                            "euler_1d",
                            5,
                        )
                        delta = float(wrap_pi(float(np.asarray(delta).reshape(-1)[0])))
                        rating = float(
                            np.clip(np.asarray(rating).reshape(-1)[0], 0.0, 1.0)
                        )
                        cost = float(np.asarray(cost).reshape(-1)[0])
                        corrected_direction = Rotation.from_matrix(
                            _yaw_correction_matrix(np.full(end - begin, delta))
                            @ qmt_wxyz_to_rotation(child_q[begin:end]).as_matrix()
                        ).apply(np.array([0.0, 0.0, -1.0]))
                        lateral = body_lateral[begin:end]
                        horizontal = np.linalg.norm(corrected_direction[:, :2], axis=1)
                        plane_error = float(np.average(
                            np.abs(np.sum(corrected_direction * lateral, axis=1)),
                            weights=np.maximum(horizontal * horizontal, 1e-6),
                        ))
                        forward_sign = HINGE_FUNCTIONAL_FORWARD_SIGN.get(
                            (edge_name, episode.chronological_index), 0.0
                        )
                        if forward_sign:
                            forward_projection = np.sum(
                                corrected_direction * body_forward[begin:end], axis=1
                            ) / np.maximum(horizontal, 1e-6)
                            direction_error = float(np.average(
                                0.5 * (1.0 - forward_sign * forward_projection),
                                weights=np.maximum(horizontal * horizontal, 1e-6),
                            ))
                        else:
                            direction_error = 0.0
                        continuity = abs(float(wrap_pi(delta - state.delta_rad)))
                        candidates.append(
                            (
                                plane_error + 0.35 * direction_error,
                                cost,
                                continuity,
                                delta,
                                rating,
                                plane_error,
                                direction_error,
                            )
                        )
                    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
                    (
                        _branch_score,
                        cost,
                        _continuity,
                        delta,
                        rating,
                        plane_error,
                        direction_error,
                    ) = candidates[0]
                except Exception:
                    delta, rating, cost, plane_error, direction_error = (
                        state.delta_rad,
                        0.0,
                        math.inf,
                        math.inf,
                        math.inf,
                    )
                excitation_weight = excitation / (
                    excitation + math.radians(0.35)
                )
                quality = rating * excitation_weight
                if not registered:
                    quality = 0.0
                variance = (
                    math.radians(18.0) ** 2
                    / max(quality * max((end - begin) / 20.0, 1.0), 0.03)
                    if np.isfinite(cost)
                    else math.inf
                )
                update_time = float(time_s[end - 1])
                value, variance_out = state.update(
                    update_time, delta, variance, quality
                )
                measurement_time.append(update_time)
                filtered_delta.append(value)
                updates.append({
                    "episode_index": episode.chronological_index,
                    "time_s": update_time,
                    "quality": float(quality),
                    "registered_functional_window": registered,
                    "excitation_rad_per_sample": excitation,
                    "anatomical_plane_error": plane_error,
                    "functional_direction_error": direction_error,
                    "delta_measurement_rad": delta,
                    "delta_filtered_rad": value,
                    "variance_rad2": variance_out,
                })
            if measurement_time:
                unwrapped = np.unwrap(np.asarray(filtered_delta, dtype=float))
                correction_angle = wrap_pi(np.interp(
                    time_s,
                    np.asarray(measurement_time),
                    unwrapped,
                    left=unwrapped[0],
                    right=unwrapped[-1],
                ))
            else:
                correction_angle = np.full(len(time_s), state.delta_rad)
            corrected = (
                _yaw_correction_matrix(correction_angle)
                @ qmt_wxyz_to_rotation(child_q).as_matrix()
            )
            child_row["quat_world_segment_wxyz"] = normalize_quat_wxyz(
                rotation_to_qmt_wxyz(Rotation.from_matrix(corrected))
            )
        # A completed calibration must be replayable over data recorded before
        # the informative hinge manoeuvre.  Elbows retain two capture-wide
        # functional factors because independent six-axis yaw can drift between
        # T-pose and the later elbow action.  The earlier factor is held until
        # the later evidence interval begins; future evidence is never blended
        # backwards across the intervening shoulder actions.  Knees retain one
        # persistent causal state.  Neither path uses per-episode profiles or
        # viewer-side repair.
        final_replay_angle = float(state.delta_rad)
        extension_resolution: dict[str, Any] | None = None
        extension_resolutions: list[dict[str, Any]] = []
        flexion_resolution: dict[str, Any] | None = None
        qmt_axis_resolution: dict[str, Any] | None = None
        replay_anchor_time: list[float] = []
        replay_anchor_delta: list[float] = []
        replay_factors: list[dict[str, float]] = []
        if edge_name.startswith("elbow_"):
            # The user-confirmed T-pose is a true extended-arm manoeuvre.  Use
            # its complete 30-second formal interval to resolve the QMT pi
            # branch and forearm mount heading.  This is a full-interval
            # functional factor, not a hand-picked desired-result frame.
            t_pose_key = "01"
            time_s = np.asarray(
                trajectory["trajectory"][t_pose_key][parent]["time_root_s"],
                dtype=float,
            )
            relative_time_s = _episode_relative_time(time_s)
            formal = (relative_time_s >= 5.0) & (relative_time_s <= 35.0)
            if int(np.sum(formal)) < 100:
                raise RuntimeError(
                    f"insufficient complete T-pose support for {edge_name}"
                )
            parent_quat = np.asarray(
                trajectory["trajectory"][t_pose_key][parent][
                    "quat_world_segment_wxyz"
                ],
                dtype=float,
            )[formal]
            child_quat = uncorrected_child_quat[t_pose_key][formal]
            final_replay_angle, extension_resolution = (
                _extended_hinge_replay_delta(parent_quat, child_quat)
            )
            extension_resolution.update({
                "source_episode_index": 1,
                "source_role": "COMPLETE_T_POSE_EXTENDED_ARM_SOFT_FACTOR",
                "single_frame_selected": False,
                "exact_world_direction_used": False,
                "qmt_state_before_extension_resolution_deg": float(
                    math.degrees(state.delta_rad)
                ),
            })
            replay_anchor_time.append(float(np.median(time_s[formal])))
            replay_anchor_delta.append(float(final_replay_angle))
            extension_resolution["start_time_s"] = float(time_s[formal][0])
            extension_resolution["stop_time_s"] = float(time_s[formal][-1])
            extension_resolutions.append(extension_resolution)
            replay_factors.append({
                "start_time_s": extension_resolution["start_time_s"],
                "stop_time_s": extension_resolution["stop_time_s"],
                "filtered_delta_rad": float(final_replay_angle),
            })

            # The corresponding shoulder raise also keeps the elbow extended.
            # It is independent, later directed evidence for the same fixed
            # upper-arm/forearm connection and for intervening six-axis yaw
            # drift.  Use the complete registered interval; never select a
            # desired-looking frame or impose an exact elbow angle.
            shoulder_episode = 3 if edge_name == "elbow_left" else 4
            shoulder_key = f"{shoulder_episode:02d}"
            shoulder_time = np.asarray(
                trajectory["trajectory"][shoulder_key][parent]["time_root_s"],
                dtype=float,
            )
            shoulder_relative = _episode_relative_time(shoulder_time)
            shoulder_formal = (
                (shoulder_relative >= 5.0) & (shoulder_relative <= 35.0)
            )
            if int(np.sum(shoulder_formal)) < 100:
                raise RuntimeError(
                    f"insufficient complete shoulder-raise extension support "
                    f"for {edge_name}"
                )
            shoulder_delta, shoulder_extension_resolution = (
                _extended_hinge_replay_delta(
                    np.asarray(
                        trajectory["trajectory"][shoulder_key][parent][
                            "quat_world_segment_wxyz"
                        ],
                        dtype=float,
                    )[shoulder_formal],
                    uncorrected_child_quat[shoulder_key][shoulder_formal],
                )
            )
            shoulder_extension_resolution.update({
                "source_episode_index": shoulder_episode,
                "source_role": (
                    "COMPLETE_SHOULDER_RAISE_EXTENDED_ARM_SOFT_FACTOR"
                ),
                "single_frame_selected": False,
                "exact_joint_angle_used": False,
                "start_time_s": float(shoulder_time[shoulder_formal][0]),
                "stop_time_s": float(shoulder_time[shoulder_formal][-1]),
            })
            extension_resolutions.append(shoulder_extension_resolution)
            replay_anchor_time.append(float(np.median(
                shoulder_time[shoulder_formal]
            )))
            replay_anchor_delta.append(float(shoulder_delta))
            replay_factors.append({
                "start_time_s": shoulder_extension_resolution["start_time_s"],
                "stop_time_s": shoulder_extension_resolution["stop_time_s"],
                "filtered_delta_rad": float(shoulder_delta),
            })
            final_replay_angle = float(shoulder_delta)

            # In the registered second half of the corresponding elbow
            # manoeuvre, the elbow is held near 90 degrees while the forearm
            # pronates/supinates.  This is independent evidence for the
            # sagittal branch at the later capture time.  QMT's 1-D result is
            # axial and therefore fused modulo pi before that physical branch
            # is selected; treating it as an ordinary directed angle caused
            # the left-arm 180-degree branch instability.
            flex_episode = 5 if edge_name == "elbow_left" else 6
            flex_key = f"{flex_episode:02d}"
            flex_time = np.asarray(
                trajectory["trajectory"][flex_key][parent]["time_root_s"],
                dtype=float,
            )
            flex_relative = _episode_relative_time(flex_time)
            flex_formal = (flex_relative >= 20.0) & (flex_relative <= 35.0)
            if int(np.sum(flex_formal)) < 100:
                raise RuntimeError(
                    f"insufficient complete flexed-elbow support for {edge_name}"
                )
            flex_delta, flexion_resolution = _flexed_elbow_replay_delta(
                np.asarray(
                    trajectory["trajectory"][flex_key]["torso"][
                        "quat_world_segment_wxyz"
                    ],
                    dtype=float,
                )[flex_formal],
                np.asarray(
                    trajectory["trajectory"][flex_key][parent][
                        "quat_world_segment_wxyz"
                    ],
                    dtype=float,
                )[flex_formal],
                uncorrected_child_quat[flex_key][flex_formal],
            )
            qmt_axis_delta, qmt_axis_resolution = _qmt_axis_heading_mod_pi(
                updates, flex_delta
            )
            qmt_weight = qmt_axis_resolution["double_angle_concentration"]
            fused_late_delta = _weighted_circular_mean(
                np.asarray([flex_delta, qmt_axis_delta], dtype=float),
                np.asarray([2.0, qmt_weight], dtype=float),
            )
            flexion_resolution.update({
                "source_episode_index": flex_episode,
                "source_role": (
                    "COMPLETE_FLEXED_ELBOW_SAGITTAL_SOFT_FACTOR"
                ),
                "single_frame_selected": False,
                "exact_joint_angle_used": False,
                "qmt_mod_pi_weight": float(qmt_weight),
                "fused_qmt_functional_delta_deg": float(
                    math.degrees(fused_late_delta)
                ),
            })
            replay_anchor_time.append(float(np.median(flex_time[flex_formal])))
            replay_anchor_delta.append(float(fused_late_delta))
            final_replay_angle = float(fused_late_delta)
            flexion_resolution["start_time_s"] = float(flex_time[flex_formal][0])
            flexion_resolution["stop_time_s"] = float(flex_time[flex_formal][-1])
            replay_factors.append({
                "start_time_s": flexion_resolution["start_time_s"],
                "stop_time_s": flexion_resolution["stop_time_s"],
                "filtered_delta_rad": float(fused_late_delta),
            })
        # The complete T-pose occurs before the elbow manoeuvres and directly
        # observes the extended forearm branch.  The later flexed factor may
        # update the state only when its own evidence interval begins; it must
        # not be interpolated backwards through the shoulder actions.  The
        # knees have no corresponding early observation, so they retain the
        # already-computed single persistent causal state as before.
        full_capture_replay_applied = edge_name.startswith("elbow_")
        if full_capture_replay_applied:
            for episode in episodes:
                key = f"{episode.chronological_index:02d}"
                child_row = trajectory["trajectory"][key][child]
                source_quat = uncorrected_child_quat[key]
                source_time = np.asarray(child_row["time_root_s"], dtype=float)
                replay_angle = _factor_interval_replay(
                    source_time, replay_factors
                )
                replayed = (
                    _yaw_correction_matrix(replay_angle)
                    @ qmt_wxyz_to_rotation(source_quat).as_matrix()
                )
                child_row["quat_world_segment_wxyz"] = normalize_quat_wxyz(
                    rotation_to_qmt_wxyz(Rotation.from_matrix(replayed))
                )
        positive = [row for row in updates if row["quality"] > 0.0]
        audit[edge_name] = {
            "pre_registered_windows": [
                {
                    "episode_index": row[0],
                    "start_s": row[1],
                    "stop_s": row[2],
                }
                for row in HINGE_CALIBRATION_WINDOWS[edge_name]
            ],
            "full_circle_branch_starts": 2,
            "branch_selection": "MINIMUM_DECLARED_SAGITTAL_PLANE_ERROR_THEN_QMT_COST_AND_CONTINUITY",
            "window_count": len(updates),
            "positive_quality_window_count": len(positive),
            "maximum_quality": float(
                max((row["quality"] for row in positive), default=0.0)
            ),
            "final_delta_deg": float(math.degrees(state.delta_rad)),
            "state_instances": 1,
            "episode_reset_count": 0,
            "parent_axis_reset_segment": hinge_axes[edge_name][0].tolist(),
            "child_axis_reset_segment": hinge_axes[edge_name][1].tolist(),
            "final_full_batch_replay_applied": full_capture_replay_applied,
            "final_replay_delta_deg": float(math.degrees(final_replay_angle)),
            "extended_limb_branch_resolution": extension_resolution,
            "extended_limb_branch_resolutions": extension_resolutions,
            "flexed_elbow_branch_resolution": flexion_resolution,
            "qmt_axis_mod_pi_resolution": qmt_axis_resolution,
            "capture_wide_replay_anchor_time_s": replay_anchor_time,
            "capture_wide_replay_anchor_delta_deg": [
                float(math.degrees(value)) for value in replay_anchor_delta
            ],
            "capture_wide_time_varying_replay": bool(replay_anchor_time),
            "inter_factor_replay": "ZERO_ORDER_HOLD_NO_FUTURE_ANTICIPATION",
            "replayed_episode_count": (
                len(episodes) if full_capture_replay_applied else 0
            ),
            "episode_specific_hinge_profile_used": False,
            "single_persistent_causal_state_retained": True,
            "future_evidence_retroactively_applied_to_past": False,
            "updates": updates,
        }
    trajectory["qmt_hinge_updates"] = audit
    trajectory["qmt_wall_s"] = float(time.perf_counter() - started)
    trajectory["qmt_primitive"] = (
        "qmt.functions.heading_correction.estimateDelta1d_unmodified"
    )
    return audit


def _signed_horizontal_angle(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Angle around world +Z that rotates ``first`` toward ``second``."""

    first_h = np.asarray(first, dtype=float).copy()
    second_h = np.asarray(second, dtype=float).copy()
    first_h[:, 2] = 0.0
    second_h[:, 2] = 0.0
    first_norm = np.linalg.norm(first_h, axis=1)
    second_norm = np.linalg.norm(second_h, axis=1)
    denom = np.maximum(first_norm * second_norm, 1e-9)
    dot = np.sum(first_h * second_h, axis=1) / denom
    cross_z = (
        first_h[:, 0] * second_h[:, 1]
        - first_h[:, 1] * second_h[:, 0]
    ) / denom
    return np.arctan2(cross_z, np.clip(dot, -1.0, 1.0))


def apply_protocol_hip_heading_soft_updates(
    trajectory: dict[str, Any],
    episodes: Sequence[EpisodeFrontend],
) -> dict[str, Any]:
    """Use declared sagittal hip-flexion manoeuvres as soft heading evidence.

    The four registered actions ask for forward thigh flexion (single-side
    hip raise, seated knee extension, and squat).  They identify a sagittal
    plane but not an exact joint angle.  Complete two-second neighbourhoods
    update one persistent heading state per hip; the same correction rotates
    thigh and shank together so this stage cannot repair or alter knee flexion.
    """

    audit: dict[str, Any] = {}
    for edge_name, (thigh, shank) in HIP_SEGMENTS.items():
        state = _PersistentHeadingState()
        allowed = set(HIP_CALIBRATION_EPISODES[edge_name])
        updates: list[dict[str, Any]] = []
        for episode in episodes:
            key = f"{episode.chronological_index:02d}"
            pelvis_row = trajectory["trajectory"][key]["pelvis"]
            thigh_row = trajectory["trajectory"][key][thigh]
            shank_row = trajectory["trajectory"][key][shank]
            time_s = np.asarray(pelvis_row["time_root_s"], dtype=float)
            pelvis_q = np.asarray(
                pelvis_row["quat_world_segment_wxyz"], dtype=float
            )
            thigh_q = np.asarray(
                thigh_row["quat_world_segment_wxyz"], dtype=float
            )
            pelvis_forward = qmt_wxyz_to_rotation(pelvis_q).apply(
                np.array([0.0, 1.0, 0.0])
            )
            thigh_direction = qmt_wxyz_to_rotation(thigh_q).apply(
                np.array([0.0, 0.0, -1.0])
            )
            measurement_time: list[float] = []
            filtered_delta: list[float] = []
            for begin in _qmt_window_starts(len(time_s)):
                end = min(len(time_s), begin + 40)
                horizontal_support = np.linalg.norm(
                    thigh_direction[begin:end, :2], axis=1
                )
                angles = _signed_horizontal_angle(
                    thigh_direction[begin:end], pelvis_forward[begin:end]
                )
                weights = horizontal_support * horizontal_support
                if float(np.sum(weights)) > 0.0:
                    measurement = float(math.atan2(
                        np.sum(weights * np.sin(angles)),
                        np.sum(weights * np.cos(angles)),
                    ))
                    support = float(np.mean(horizontal_support))
                else:
                    measurement = state.delta_rad
                    support = 0.0
                quality = support if episode.chronological_index in allowed else 0.0
                # A plane likelihood is deliberately broader than a hinge
                # axis likelihood.  Natural ab/adduction is retained instead
                # of forcing every thigh exactly into the sagittal plane.
                variance = (
                    math.radians(25.0) ** 2
                    / max(quality * max((end - begin) / 20.0, 1.0), 0.03)
                )
                update_time = float(time_s[end - 1])
                value, variance_out = state.update(
                    update_time, measurement, variance, quality
                )
                measurement_time.append(update_time)
                filtered_delta.append(value)
                updates.append({
                    "episode_index": episode.chronological_index,
                    "time_s": update_time,
                    "quality": quality,
                    "delta_measurement_rad": measurement,
                    "delta_filtered_rad": value,
                    "variance_rad2": variance_out,
                })
            if measurement_time:
                unwrapped = np.unwrap(np.asarray(filtered_delta, dtype=float))
                correction_angle = wrap_pi(np.interp(
                    time_s,
                    np.asarray(measurement_time),
                    unwrapped,
                    left=unwrapped[0],
                    right=unwrapped[-1],
                ))
            else:
                correction_angle = np.full(len(time_s), state.delta_rad)
            correction = _yaw_correction_matrix(correction_angle)
            for segment, row in ((thigh, thigh_row), (shank, shank_row)):
                corrected = (
                    correction
                    @ qmt_wxyz_to_rotation(
                        row["quat_world_segment_wxyz"]
                    ).as_matrix()
                )
                row["quat_world_segment_wxyz"] = normalize_quat_wxyz(
                    rotation_to_qmt_wxyz(Rotation.from_matrix(corrected))
                )
        positive = [row for row in updates if row["quality"] > 0.0]
        audit[edge_name] = {
            "mechanism": "DECLARED_SAGITTAL_FLEXION_PLANE_SOFT_LIKELIHOOD",
            "pre_registered_complete_episodes": list(
                HIP_CALIBRATION_EPISODES[edge_name]
            ),
            "window_count": len(updates),
            "positive_quality_window_count": len(positive),
            "maximum_quality": float(
                max((row["quality"] for row in positive), default=0.0)
            ),
            "final_delta_deg": float(math.degrees(state.delta_rad)),
            "state_instances": 1,
            "episode_reset_count": 0,
            "exact_pose_angle_used": False,
            "thigh_and_shank_rotated_together": True,
            "updates": updates,
        }
    trajectory["protocol_hip_heading_updates"] = audit
    return audit


def _segment_directions(
    trajectory: dict[str, Any], episode_key: str
) -> dict[str, np.ndarray]:
    return {
        segment: qmt_wxyz_to_rotation(
            trajectory["trajectory"][episode_key][segment][
                "quat_world_segment_wxyz"
            ]
        ).apply(np.array([0.0, 0.0, -1.0]))
        for segment in SEGMENTS
    }


def _angle(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    return np.arccos(np.clip(np.sum(first * second, axis=1), -1.0, 1.0))


def _robust_quantile_frame(
    score: np.ndarray,
    candidates: np.ndarray,
    quantile: float,
) -> tuple[int, float, dict[str, float]]:
    """Select a pre-declared representative quantile, never a lucky extreme.

    Calibration manoeuvres contain several repetitions and natural balance
    corrections.  The absolute maximum routinely selects a single unstable
    row.  A fixed action-role quantile uses the complete formal interval and
    remains independent of the fitted residual or desired picture.
    """

    values = np.asarray(score, dtype=float)[np.asarray(candidates, dtype=int)]
    finite = np.isfinite(values)
    if not np.any(finite):
        raise RuntimeError("no finite protocol QA score in formal interval")
    valid_candidates = np.asarray(candidates, dtype=int)[finite]
    valid_values = values[finite]
    q = float(np.clip(quantile, 0.0, 1.0))
    target = float(np.quantile(valid_values, q))
    local = int(np.argmin(np.abs(valid_values - target)))
    frame = int(valid_candidates[local])
    return frame, float(score[frame]), {
        "registered_quantile": q,
        "formal_score_min": float(np.min(valid_values)),
        "formal_score_median": float(np.median(valid_values)),
        "formal_score_max": float(np.max(valid_values)),
        "formal_score_quantile_target": target,
    }


def select_protocol_frame(
    trajectory: dict[str, Any], episode_index: int, label: str
) -> tuple[int, dict[str, Any]]:
    """Select an inspection frame from the declared calibration manoeuvre.

    The action role is used only for visual QA frame selection; it never enters
    orientation fitting or becomes an exact angle/pose residual.
    """

    key = f"{episode_index:02d}"
    direction = _segment_directions(trajectory, key)
    count = len(direction["pelvis"])
    time_s = np.asarray(
        trajectory["trajectory"][key]["pelvis"]["time_root_s"], dtype=float
    )
    relative_time_s = _episode_relative_time(time_s)
    if label in {"elbow_left", "elbow_right"}:
        candidate_mask = (relative_time_s >= 5.0) & (relative_time_s <= 20.0)
    else:
        candidate_mask = (relative_time_s >= 5.0) & (relative_time_s <= 35.0)
    candidates = np.flatnonzero(candidate_mask)
    if not len(candidates):
        candidates = np.arange(max(0, int(0.15 * count)), max(1, int(0.85 * count)))
    torso_rotation = qmt_wxyz_to_rotation(
        trajectory["trajectory"][key]["torso"]["quat_world_segment_wxyz"]
    )
    torso_lateral = torso_rotation.apply(np.array([1.0, 0.0, 0.0]))
    torso_forward = torso_rotation.apply(np.array([0.0, 1.0, 0.0]))
    if label in {"standing", "final_standing"}:
        frame = int(min(count - 1, max(0, int(0.10 * count))))
        score_name = "EARLY_ROBUST_STANDING"
        selected_score = 0.0
        selection_distribution = {
            "registered_quantile": 0.10,
            "formal_score_min": 0.0,
            "formal_score_median": 0.0,
            "formal_score_max": 0.0,
            "formal_score_quantile_target": 0.0,
        }
    else:
        if label == "t_pose":
            left = direction["upper_arm_left"]
            right = direction["upper_arm_right"]
            score = -(
                np.abs(left[:, 2])
                + np.abs(right[:, 2])
                + 0.75 * (1.0 + np.sum(left * torso_lateral, axis=1))
                + 0.75 * (1.0 - np.sum(right * torso_lateral, axis=1))
            )
            score_name = "BILATERAL_HORIZONTAL_OPPOSED_TORSO_SIDE_PLANE"
            representative_quantile = 0.90
        elif label == "pelvis_hula":
            pelvis = direction["pelvis"]
            score = np.linalg.norm(pelvis[:, :2], axis=1)
            score_name = "PELVIS_TILT_MAGNITUDE"
            representative_quantile = 0.75
        elif label == "shoulder_left":
            arm = direction["upper_arm_left"]
            score = arm[:, 2] - np.abs(np.sum(arm * torso_forward, axis=1))
            score_name = "LEFT_UPPER_ARM_ELEVATION_IN_SIDE_PLANE"
            representative_quantile = 0.85
        elif label == "shoulder_right":
            arm = direction["upper_arm_right"]
            score = arm[:, 2] - np.abs(np.sum(arm * torso_forward, axis=1))
            score_name = "RIGHT_UPPER_ARM_ELEVATION_IN_SIDE_PLANE"
            representative_quantile = 0.85
        elif label == "elbow_left":
            upper = direction["upper_arm_left"]
            forearm = direction["forearm_left"]
            score = (
                _angle(upper, forearm)
                - 0.75 * np.abs(np.sum(forearm * torso_lateral, axis=1))
                - 0.25 * np.linalg.norm(upper[:, :2], axis=1)
            )
            score_name = "LEFT_ELBOW_FIRST_PHASE_FLEXION_IN_SAGITTAL_PLANE"
            representative_quantile = 0.85
        elif label == "elbow_right":
            upper = direction["upper_arm_right"]
            forearm = direction["forearm_right"]
            score = (
                _angle(upper, forearm)
                - 0.75 * np.abs(np.sum(forearm * torso_lateral, axis=1))
                - 0.25 * np.linalg.norm(upper[:, :2], axis=1)
            )
            score_name = "RIGHT_ELBOW_FIRST_PHASE_FLEXION_IN_SAGITTAL_PLANE"
            representative_quantile = 0.85
        elif label in {"hip_left", "hip_right"}:
            side = label.rsplit("_", 1)[1]
            score = _angle(direction["pelvis"], direction[f"thigh_{side}"])
            score_name = f"{side.upper()}_HIP_FLEXION"
            representative_quantile = 0.85
        elif label in {"knee_left", "knee_right"}:
            side = label.rsplit("_", 1)[1]
            # The seated manoeuvre explicitly extends the shank.  Score the
            # negative flexion angle so a high registered quantile represents
            # a robust extension, not the previous maximum-flexion mistake.
            score = -_angle(
                direction[f"thigh_{side}"], direction[f"shank_{side}"]
            )
            score_name = f"{side.upper()}_SEATED_KNEE_EXTENSION"
            representative_quantile = 0.90
        elif label in {"heel_left", "heel_right"}:
            side = label.rsplit("_", 1)[1]
            score = _angle(
                direction[f"thigh_{side}"], direction[f"shank_{side}"]
            )
            score_name = f"{side.upper()}_HEEL_TO_BUTT_KNEE_FLEXION"
            representative_quantile = 0.85
        elif label in {"heel_raise_left", "heel_raise_right"}:
            # There is no foot IMU.  A midpoint row can show the observable
            # shank state, but the ankle/heel displacement itself is outside
            # this ten-node topology and must not be inferred from the prompt.
            score = relative_time_s
            score_name = "FORMAL_INTERVAL_MIDPOINT_NO_FOOT_IMU"
            representative_quantile = 0.50
        elif label == "trunk_flex":
            score = _angle(direction["pelvis"], direction["torso"])
            score_name = "TORSO_PELVIS_FLEXION_MAGNITUDE"
            representative_quantile = 0.85
        elif label == "trunk_axial":
            pelvis_lateral = qmt_wxyz_to_rotation(
                trajectory["trajectory"][key]["pelvis"][
                    "quat_world_segment_wxyz"
                ]
            ).apply(np.array([1.0, 0.0, 0.0]))
            score = np.abs(_signed_horizontal_angle(
                pelvis_lateral, torso_lateral
            ))
            score_name = "TORSO_PELVIS_AXIAL_ROTATION_MAGNITUDE"
            representative_quantile = 0.85
        elif label == "squat":
            left = _angle(direction["pelvis"], direction["thigh_left"])
            right = _angle(direction["pelvis"], direction["thigh_right"])
            score = left + right - 2.0 * np.abs(left - right)
            score_name = "BILATERAL_BALANCED_HIP_FLEXION"
            representative_quantile = 0.75
        else:
            raise ValueError(f"unknown protocol frame label: {label}")
        frame, selected_score, selection_distribution = _robust_quantile_frame(
            score, candidates, representative_quantile
        )
    return frame, {
        "source": "DECLARED_ACTION_ROLE_FOR_QA_ONLY",
        "score": score_name,
        "selected_score": selected_score,
        **selection_distribution,
        "fit_residual_or_desired_result_used": False,
        "exact_pose_truth_used": False,
        "single_extreme_row_selected": False,
    }
