"""Exact and noisy time-resolved synthetic cases for unified calibration."""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_revision_d.d0b_r1_generator import ACTIONS, SEGMENTS
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_model import (
    JOINTS,
    R1Observation,
    angles_from_axis,
    articulated_pose_directions,
    unit,
    yaw,
)

from .unified_calibration import (
    B5_JOINT_EDGES,
    B5_LEVER_ENDPOINTS,
    FULL_DIMENSION,
    POSE_NUISANCE_DIMENSION,
    PRODUCT_DIMENSION,
    ZERO_JOINTS,
)


B3_HIP_CIRCUMDUCTION = "b3_bilateral_hip_circumduction_two_axis"
B3_KNEE_LEFT = "b3_left_seated_knee_flexion_tibial_axial"
B3_KNEE_RIGHT = "b3_right_seated_knee_flexion_tibial_axial"
B3_TRUNK_LATERAL = "b3_trunk_labelled_left_right_lateral_bend"
B3_ACTIONS = (
    B3_HIP_CIRCUMDUCTION,
    B3_KNEE_LEFT,
    B3_KNEE_RIGHT,
    B3_TRUNK_LATERAL,
)
B4_EN_BLOC = "b4_supported_braced_en_bloc_two_axis"
B4_NEGATIVE_CONTROLS = (
    "segment_articulation",
    "insufficient_second_axis",
    "timing_mismatch",
    "noisy_near_zero_rates",
)
B5_NEGATIVE_CONTROLS = (
    "timing_offset",
    "low_dynamics",
    "single_axis_lever_arm_degeneracy",
    "articulation_eligibility_failure",
    "noise_amplification",
)
UNIFIED_ACTIONS = ACTIONS + B3_ACTIONS + (B4_EN_BLOC,)
COMPLETE_EPISODE_ACTIONS = B3_ACTIONS + (B4_EN_BLOC,)


def _frame_from_long_axis(direction: np.ndarray, lateral_reference: np.ndarray) -> np.ndarray:
    z = unit(direction)
    seed = unit(lateral_reference)
    if abs(float(z @ seed)) > 0.85:
        seed = np.roll(seed, 1)
    x = unit(seed - z * float(seed @ z))
    y = unit(np.cross(z, x))
    return np.column_stack((x, y, z))


def _transport_frame(start: np.ndarray, stop_direction: np.ndarray) -> np.ndarray:
    """Parallel-transport a segment frame to a new longitudinal direction."""

    source = unit(start[:, 2])
    target = unit(stop_direction)
    cross = np.cross(source, target)
    sine = float(np.linalg.norm(cross))
    cosine = float(np.clip(source @ target, -1.0, 1.0))
    if sine < 1e-12:
        if cosine > 0.0:
            return start.copy()
        axis = unit(start[:, 0])
    else:
        axis = cross / sine
    angle = math.atan2(sine, cosine)
    return Rotation.from_rotvec(axis * angle).as_matrix() @ start


def _smoothstep(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3.0 - 2.0 * value)


def _action_wave(phase: float, scale: float = 0.75, cycles: float = 1.6) -> float:
    if phase <= 0.15 or phase >= 0.85:
        return 0.0
    u = (phase - 0.15) / 0.70
    return scale * math.sin(math.pi * u) ** 2 * math.sin(2.0 * math.pi * cycles * u)


def _interpolate_frame(start: np.ndarray, stop: np.ndarray, amount: float) -> np.ndarray:
    delta = Rotation.from_matrix(stop @ start.T).as_rotvec()
    return Rotation.from_rotvec(amount * delta).as_matrix() @ start


def _project_perpendicular(preferred: np.ndarray, *directions: np.ndarray) -> np.ndarray:
    """Closest unit vector orthogonal to the supplied longitudinal axes."""

    matrix = np.stack([unit(value) for value in directions])
    _, singular, vh = np.linalg.svd(matrix, full_matrices=True)
    rank = int(np.sum(singular > 1e-6))
    null = vh[rank:].T
    if null.shape[1]:
        projected = null @ (null.T @ unit(preferred))
        if float(np.linalg.norm(projected)) > 1e-8:
            return unit(projected)
        candidate = null[:, 0]
        return unit(candidate if float(candidate @ preferred) >= 0.0 else -candidate)
    raise ValueError("longitudinal directions leave no functional-axis nullspace")


def _b4_common_rotation(
    phase: float,
    neutral: Mapping[str, np.ndarray],
    amplitude_scale: float,
    control: str | None,
) -> np.ndarray:
    """Two measured non-collinear rigid rotations with no estimator template."""

    pelvis = neutral["pelvis"]
    axis_a = unit(pelvis @ np.array([0.31, -0.18, 0.93]))
    axis_b = unit(pelvis @ np.array([-0.23, 0.95, 0.21]))
    if control == "insufficient_second_axis":
        axis_b = axis_a
    scale = 0.002 if control == "noisy_near_zero_rates" else amplitude_scale
    axis = None
    angle = 0.0
    if 0.15 < phase < 0.48:
        u = (phase - 0.15) / 0.33
        axis = axis_a
        angle = scale * 0.68 * math.sin(math.pi * u) ** 2 * math.sin(2.0 * math.pi * 1.15 * u)
    elif 0.52 < phase < 0.85:
        u = (phase - 0.52) / 0.33
        axis = axis_b
        angle = scale * 0.62 * math.sin(math.pi * u) ** 2 * math.sin(2.0 * math.pi * 1.15 * u)
    return np.eye(3) if axis is None else Rotation.from_rotvec(axis * angle).as_matrix()


def _static_pose_frames(
    q: np.ndarray,
    action: str,
    reference: Mapping[str, np.ndarray] | None = None,
) -> dict[str, np.ndarray]:
    directions = articulated_pose_directions(action, q)
    if reference is not None:
        return {segment: _transport_frame(reference[segment], direction) for segment, direction in directions.items()}
    root = Rotation.from_rotvec(q[:3]).as_matrix()
    lateral = root @ np.array([1.0, 0.0, 0.0])
    return {segment: _frame_from_long_axis(direction, lateral) for segment, direction in directions.items()}


def _dynamic_frames(
    action: str,
    phase: float,
    neutral: Mapping[str, np.ndarray],
    local_axes: Mapping[str, np.ndarray],
    amplitude_scale: float = 1.0,
    b4_control: str | None = None,
    b5_control: str | None = None,
) -> dict[str, np.ndarray]:
    frames = {segment: value.copy() for segment, value in neutral.items()}
    wave = amplitude_scale * _action_wave(phase)

    def child_about_parent(joint: str, angle: float, parent_frame: np.ndarray | None = None) -> np.ndarray:
        parent, child = JOINTS[joint]
        rp = frames[parent] if parent_frame is None else parent_frame
        relative0 = neutral[parent].T @ neutral[child]
        return rp @ Rotation.from_rotvec(local_axes[joint] * angle).as_matrix() @ relative0

    if action == "arms":
        for side, sign in (("L", 1.0), ("R", -1.0)):
            shoulder = f"shoulder_{side}"
            elbow = f"elbow_{side}"
            upper = f"upper_arm_{side}"
            forearm = f"forearm_{side}"
            frames[upper] = child_about_parent(shoulder, sign * wave)
            frames[forearm] = child_about_parent(elbow, 0.28 * sign * wave, frames[upper])
    elif action in ("left_elbow", "right_elbow_attempt2"):
        side = "L" if action == "left_elbow" else "R"
        joint = f"elbow_{side}"
        child = f"forearm_{side}"
        if phase < 0.50:
            local = phase / 0.50
            curl = amplitude_scale * 0.90 * math.sin(math.pi * local) ** 2 * math.sin(2.0 * math.pi * 1.2 * local)
            frames[child] = child_about_parent(joint, curl)
        elif b5_control == "single_axis_lever_arm_degeneracy":
            local = (phase - 0.50) / 0.50
            curl = amplitude_scale * 0.90 * math.sin(math.pi * local) ** 2 * math.sin(2.0 * math.pi * 1.2 * local)
            frames[child] = child_about_parent(joint, curl)
        else:
            local = (phase - 0.50) / 0.50
            pronation = amplitude_scale * 0.75 * math.sin(math.pi * local) ** 2 * math.sin(2.0 * math.pi * 1.2 * local)
            frames[child] = neutral[child] @ Rotation.from_rotvec([0.0, 0.0, pronation]).as_matrix()
    elif action in ("left_knee", "right_knee"):
        side = "L" if action == "left_knee" else "R"
        hip = f"hip_{side}"
        thigh = f"thigh_{side}"
        shank = f"shank_{side}"
        frames[thigh] = child_about_parent(hip, wave)
        frames[shank] = frames[thigh] @ (neutral[thigh].T @ neutral[shank])
    elif action in ("left_heel", "right_heel"):
        side = "L" if action == "left_heel" else "R"
        frames[f"shank_{side}"] = child_about_parent(f"knee_{side}", wave)
    elif action == "squats":
        for side, sign in (("L", 1.0), ("R", -1.0)):
            thigh = f"thigh_{side}"
            shank = f"shank_{side}"
            frames[thigh] = child_about_parent(f"hip_{side}", sign * wave)
            frames[shank] = child_about_parent(f"knee_{side}", -0.72 * sign * wave, frames[thigh])
    elif action == "trunk":
        relative0 = neutral["pelvis"].T @ neutral["torso"]
        if phase < 2.0 / 3.0:
            local = phase * 1.5
            sign = 1.0 if local < 0.5 else -1.0
            subphase = (local % 0.5) * 2.0
            angle = amplitude_scale * sign * 0.70 * math.sin(math.pi * subphase) ** 2 * math.sin(2.0 * math.pi * 1.1 * subphase)
            axis = np.array([0.0, 0.0, 1.0])
        elif b5_control == "single_axis_lever_arm_degeneracy":
            subphase = (phase - 2.0 / 3.0) * 3.0
            angle = amplitude_scale * 0.65 * math.sin(math.pi * subphase) ** 2 * math.sin(2.0 * math.pi * 1.1 * subphase)
            axis = np.array([0.0, 0.0, 1.0])
        else:
            subphase = (phase - 2.0 / 3.0) * 3.0
            angle = amplitude_scale * 0.65 * math.sin(math.pi * subphase) ** 2 * math.sin(2.0 * math.pi * 1.1 * subphase)
            axis = np.array([1.0, 0.0, 0.0])
        frames["torso"] = frames["pelvis"] @ Rotation.from_rotvec(axis * angle).as_matrix() @ relative0
    elif action == B3_HIP_CIRCUMDUCTION:
        # This is deliberately not the existing Capture2 pelvis-hula label.
        # The pelvis is held fixed while both thighs execute synchronized,
        # mirrored two-axis rotations relative to it.
        if 0.15 < phase < 0.85:
            if phase < 0.50:
                u = (phase - 0.15) / 0.35
                lateral_angle = amplitude_scale * 0.62 * math.sin(math.pi * u) ** 2
                forward_angle = 0.0
            elif b5_control == "single_axis_lever_arm_degeneracy":
                u = (phase - 0.50) / 0.35
                lateral_angle = amplitude_scale * 0.62 * math.sin(math.pi * u) ** 2
                forward_angle = 0.0
            else:
                u = (phase - 0.50) / 0.35
                lateral_angle = 0.0
                forward_angle = amplitude_scale * 0.46 * math.sin(math.pi * u) ** 2
            for side, lateral_sign in (("L", 1.0), ("R", -1.0)):
                thigh = f"thigh_{side}"
                shank = f"shank_{side}"
                relative0 = neutral["pelvis"].T @ neutral[thigh]
                rotation = Rotation.from_rotvec([
                    lateral_sign * lateral_angle,
                    forward_angle,
                    0.0,
                ]).as_matrix()
                frames[thigh] = frames["pelvis"] @ rotation @ relative0
                frames[shank] = frames[thigh] @ (neutral[thigh].T @ neutral[shank])
    elif action in (B3_KNEE_LEFT, B3_KNEE_RIGHT):
        # Unlike the existing Capture2 seated-knee labels, eligibility here
        # requires a stationary thigh and two measured shank phases: flexion
        # about the knee axis, then small tibial axial rotation.
        side = "L" if action == B3_KNEE_LEFT else "R"
        joint = f"knee_{side}"
        thigh = f"thigh_{side}"
        shank = f"shank_{side}"
        if 0.15 < phase < 0.50:
            u = (phase - 0.15) / 0.35
            angle = amplitude_scale * 0.82 * math.sin(math.pi * u) ** 2
            relative0 = neutral[thigh].T @ neutral[shank]
            frames[shank] = frames[thigh] @ Rotation.from_rotvec(local_axes[joint] * angle).as_matrix() @ relative0
        elif 0.50 < phase < 0.85 and b5_control == "single_axis_lever_arm_degeneracy":
            u = (phase - 0.50) / 0.35
            angle = amplitude_scale * 0.82 * math.sin(math.pi * u) ** 2
            relative0 = neutral[thigh].T @ neutral[shank]
            frames[shank] = frames[thigh] @ Rotation.from_rotvec(local_axes[joint] * angle).as_matrix() @ relative0
        elif 0.50 < phase < 0.85:
            u = (phase - 0.50) / 0.35
            angle = amplitude_scale * 0.34 * math.sin(math.pi * u) ** 2
            frames[shank] = neutral[shank] @ Rotation.from_rotvec([0.0, 0.0, angle]).as_matrix()
    elif action == B3_TRUNK_LATERAL:
        # A labelled left excursion and labelled right excursion orient the
        # lateral-bend axis sign that unsigned axial/flexion factors cannot.
        relative0 = neutral["pelvis"].T @ neutral["torso"]
        angle = 0.0
        if 0.15 < phase < 0.45:
            u = (phase - 0.15) / 0.30
            angle = amplitude_scale * 0.52 * math.sin(math.pi * u) ** 2
        elif 0.55 < phase < 0.85:
            u = (phase - 0.55) / 0.30
            angle = -amplitude_scale * 0.52 * math.sin(math.pi * u) ** 2
        axis = np.array([0.0, 0.0, 1.0]) if b5_control == "single_axis_lever_arm_degeneracy" else np.array([0.0, 1.0, 0.0])
        frames["torso"] = frames["pelvis"] @ Rotation.from_rotvec(axis * angle).as_matrix() @ relative0
    elif action == B4_EN_BLOC:
        # The factor sees only simultaneous measured angular velocities.  The
        # generator chooses arbitrary non-collinear truth axes solely to make
        # the synthetic challenge; those axes never enter the estimator.
        common = _b4_common_rotation(phase, neutral, amplitude_scale, b4_control)
        frames = {segment: common @ neutral[segment] for segment in SEGMENTS}
        if b4_control == "segment_articulation":
            extra = 0.24 * _action_wave(phase, scale=1.0, cycles=1.1)
            frames["shank_L"] = frames["shank_L"] @ Rotation.from_rotvec([extra, 0.0, 0.0]).as_matrix()
        elif b4_control == "timing_mismatch":
            delayed = _b4_common_rotation(max(0.0, phase - 0.055), neutral, amplitude_scale, None)
            frames["forearm_R"] = delayed @ neutral["forearm_R"]
    return frames


def _b5_truth_lever_arms(mounts: Mapping[str, np.ndarray]) -> dict[tuple[str, str], np.ndarray]:
    """Physically plausible sensor-to-joint vectors, sealed from estimator use."""

    anatomical = {
        ("trunk", "pelvis"): np.array([0.0, 0.0, 0.14]),
        ("trunk", "torso"): np.array([0.0, 0.0, -0.22]),
        ("shoulder_L", "torso"): np.array([0.18, 0.0, 0.18]),
        ("shoulder_L", "upper_arm_L"): np.array([0.0, 0.0, -0.16]),
        ("shoulder_R", "torso"): np.array([-0.18, 0.0, 0.18]),
        ("shoulder_R", "upper_arm_R"): np.array([0.0, 0.0, -0.16]),
        ("elbow_L", "upper_arm_L"): np.array([0.0, 0.0, 0.16]),
        ("elbow_L", "forearm_L"): np.array([0.0, 0.0, -0.13]),
        ("elbow_R", "upper_arm_R"): np.array([0.0, 0.0, 0.16]),
        ("elbow_R", "forearm_R"): np.array([0.0, 0.0, -0.13]),
        ("hip_L", "pelvis"): np.array([0.14, 0.0, -0.10]),
        ("hip_L", "thigh_L"): np.array([0.0, 0.0, -0.21]),
        ("hip_R", "pelvis"): np.array([-0.14, 0.0, -0.10]),
        ("hip_R", "thigh_R"): np.array([0.0, 0.0, -0.21]),
        ("knee_L", "thigh_L"): np.array([0.0, 0.0, 0.21]),
        ("knee_L", "shank_L"): np.array([0.0, 0.0, -0.21]),
        ("knee_R", "thigh_R"): np.array([0.0, 0.0, 0.21]),
        ("knee_R", "shank_R"): np.array([0.0, 0.0, -0.21]),
    }
    assert set(anatomical) == set(B5_LEVER_ENDPOINTS)
    return {
        endpoint: mounts[endpoint[1]].T @ vector
        for endpoint, vector in anatomical.items()
    }


def _b5_sensor_positions(
    board_to_world: np.ndarray,
    lever_arms: Mapping[tuple[str, str], np.ndarray],
) -> np.ndarray:
    """Build a joint-connected sensor translation tree from sealed truth."""

    positions = np.zeros((board_to_world.shape[0], len(SEGMENTS), 3), dtype=float)
    segment_index = {segment: index for index, segment in enumerate(SEGMENTS)}
    edge_order = (
        "trunk", "shoulder_L", "shoulder_R", "elbow_L", "elbow_R",
        "hip_L", "hip_R", "knee_L", "knee_R",
    )
    for joint in edge_order:
        parent, child = B5_JOINT_EDGES[joint]
        parent_index, child_index = segment_index[parent], segment_index[child]
        parent_vector = np.einsum(
            "nij,j->ni", board_to_world[:, parent_index], lever_arms[(joint, parent)],
        )
        child_vector = np.einsum(
            "nij,j->ni", board_to_world[:, child_index], lever_arms[(joint, child)],
        )
        joint_position = positions[:, parent_index] + parent_vector
        positions[:, child_index] = joint_position - child_vector
    return positions


def generate_unified_case(
    contract: Mapping[str, Any],
    seed: int,
    *,
    noisy: bool,
    weak_motion: bool = False,
    b4_control: str | None = None,
    b5_control: str | None = None,
) -> tuple[R1Observation, np.ndarray, dict[str, Any]]:
    """Generate an estimator-facing case with a sealed coordinate truth."""

    if b4_control is not None and b4_control not in B4_NEGATIVE_CONTROLS:
        raise ValueError(f"unsupported B4 negative control {b4_control}")
    if b5_control is not None and b5_control not in B5_NEGATIVE_CONTROLS:
        raise ValueError(f"unsupported B5 negative control {b5_control}")
    if b4_control is not None and b5_control is not None:
        raise ValueError("B4 and B5 negative controls are mutually exclusive")
    rng = np.random.default_rng(seed)
    rate_hz = float(contract["common_time"]["rate_hz"])
    rows_per_action = 150
    total = rows_per_action * len(UNIFIED_ACTIONS)
    time_ns = np.rint(np.arange(total) / rate_hz * 1e9).astype(np.int64) + 7_000_000_000
    windows: dict[str, tuple[int, int]] = {}
    action_rows: dict[str, np.ndarray] = {}
    static_rows: dict[str, np.ndarray] = {}
    episode_phase_rows: dict[str, dict[str, np.ndarray]] = {}
    phase_values = np.linspace(0.0, 1.0, rows_per_action)
    for action_index, action in enumerate(UNIFIED_ACTIONS):
        rows = np.arange(action_index * rows_per_action, (action_index + 1) * rows_per_action)
        action_rows[action] = rows[(phase_values >= 0.12) & (phase_values <= 0.88)]
        static_rows[action] = rows[(phase_values >= 0.34) & (phase_values <= 0.66)]
        windows[action] = (int(time_ns[rows[0]]), int(time_ns[rows[-1]]))
        if action in COMPLETE_EPISODE_ACTIONS:
            episode_phase_rows[action] = {
                "PRE_REST": rows[phase_values < 0.12],
                "TRANSITION_TO_ACTION": rows[(phase_values >= 0.12) & (phase_values < 0.22)],
                "FORMAL_ACTION": rows[(phase_values >= 0.22) & (phase_values <= 0.78)],
                "TRANSITION_TO_POST_REST": rows[(phase_values > 0.78) & (phase_values <= 0.88)],
                "POST_REST": rows[phase_values > 0.88],
            }
        if action == B4_EN_BLOC:
            episode_phase_rows[action]["COMMON_ROTATION_AXIS_A"] = rows[
                (phase_values >= 0.17) & (phase_values <= 0.46)
            ]
            episode_phase_rows[action]["COMMON_ROTATION_AXIS_B"] = rows[
                (phase_values >= 0.54) & (phase_values <= 0.83)
            ]

    root_yaw = rng.uniform(-math.pi, math.pi)
    # Structured, non-textbook human variation keeps the synthetic motion on
    # the declared hinge/connectivity manifold.  Independent random chart
    # perturbations would manufacture model mismatch by tilting a segment into
    # its own hinge axis and are therefore not a valid noise-free truth.
    q_initial = np.zeros(17)
    q_tpose = np.zeros(17)
    initial_scale = 0.025 if noisy or weak_motion else 0.004
    tpose_scale = 0.035 if noisy or weak_motion else 0.006
    q_initial[:2] = rng.normal(0.0, initial_scale, 2)
    q_tpose[:2] = rng.normal(0.0, tpose_scale, 2)
    q_initial[[5, 7, 9, 10, 12, 14]] = rng.normal(0.0, initial_scale, 6)
    q_tpose[[5, 7, 9, 10, 12, 14]] = rng.normal(0.0, tpose_scale, 6)
    q_initial[2] = root_yaw
    q_tpose[2] = root_yaw
    neutral = _static_pose_frames(q_initial, "initial_still_attempt2")
    tpose = _static_pose_frames(q_tpose, "t_pose", neutral)

    mounts = {segment: Rotation.from_rotvec(rng.normal(0.0, 0.45, 3)).as_matrix() for segment in SEGMENTS}
    headings = {"pelvis": 0.0, **{segment: float(rng.uniform(-1.4, 1.4)) for segment in SEGMENTS[1:]}}
    root = Rotation.from_rotvec(q_initial[:3]).as_matrix()
    body_lateral = root @ np.array([1.0, 0.0, 0.0])
    body_forward = root @ np.array([0.0, 1.0, 0.0])
    body_up = root @ np.array([0.0, 0.0, 1.0])
    off_axis = 1.0 if weak_motion else 0.0
    world_axes = {
        "shoulder_L": unit(body_forward + off_axis * 0.05 * body_up),
        "shoulder_R": unit(body_forward - off_axis * 0.05 * body_up),
        "elbow_L": unit(body_lateral + off_axis * 0.04 * body_forward),
        "elbow_R": unit(body_lateral - off_axis * 0.04 * body_forward),
        "hip_L": unit(body_lateral + off_axis * 0.03 * body_forward),
        "hip_R": unit(body_lateral - off_axis * 0.03 * body_forward),
        # Knee axes are deliberately non-collinear with the hip axes; otherwise
        # the distal chain has a genuine twist ambiguity even with perfect
        # hinge motion.
        "knee_L": unit(body_lateral + (0.10 + off_axis * 0.02) * body_forward),
        "knee_R": unit(body_lateral - (0.10 + off_axis * 0.02) * body_forward),
    }
    for joint, preferred in tuple(world_axes.items()):
        parent, child = JOINTS[joint]
        world_axes[joint] = _project_perpendicular(
            preferred,
            neutral[parent][:, 2],
            neutral[child][:, 2],
        )
    local_axes = {
        joint: unit(neutral[JOINTS[joint][0]].T @ axis)
        for joint, axis in world_axes.items()
    }

    world_segment = np.empty((total, len(SEGMENTS), 3, 3))
    for action_index, action in enumerate(UNIFIED_ACTIONS):
        rows = np.arange(action_index * rows_per_action, (action_index + 1) * rows_per_action)
        for local_row, row in enumerate(rows):
            phase = local_row / (rows_per_action - 1)
            if action == "initial_still_attempt2":
                frames = neutral
            elif action == "t_pose":
                if phase < 0.18:
                    amount = 0.0
                elif phase < 0.32:
                    amount = _smoothstep((phase - 0.18) / 0.14)
                elif phase <= 0.68:
                    amount = 1.0
                elif phase <= 0.84:
                    amount = 1.0 - _smoothstep((phase - 0.68) / 0.16)
                else:
                    amount = 0.0
                frames = {segment: _interpolate_frame(neutral[segment], tpose[segment], amount) for segment in SEGMENTS}
            else:
                b5_scale = 0.002 if b5_control == "low_dynamics" and action != B4_EN_BLOC else 1.0
                frames = _dynamic_frames(
                    action,
                    phase,
                    neutral,
                    local_axes,
                    amplitude_scale=(0.035 if weak_motion else 1.0) * b5_scale,
                    b4_control=b4_control,
                    b5_control=b5_control,
                )
            for segment_index, segment in enumerate(SEGMENTS):
                world_segment[row, segment_index] = frames[segment]

    node_to_segment = {f"SYN_{index:02d}": segment for index, segment in enumerate(SEGMENTS)}
    node_order = tuple(node_to_segment)
    rotation = np.empty_like(world_segment)
    board_to_world_all = np.empty_like(world_segment)
    gyro = np.zeros((total, len(SEGMENTS), 3))
    for segment_index, segment in enumerate(SEGMENTS):
        board_to_world = np.einsum("nij,jk->nik", world_segment[:, segment_index], mounts[segment])
        board_to_world_all[:, segment_index] = board_to_world
        clean_rotation = np.einsum("ij,njk->nik", yaw(-headings[segment]), board_to_world)
        rotation[:, segment_index] = clean_rotation
        for row in range(1, total):
            dt = (time_ns[row] - time_ns[row - 1]) / 1e9
            gyro[row, segment_index] = Rotation.from_matrix(
                clean_rotation[row - 1].T @ clean_rotation[row]
            ).as_rotvec() / dt
        if noisy:
            noise = Rotation.from_rotvec(rng.normal(0.0, math.radians(0.12), (total, 3))).as_matrix()
            rotation[:, segment_index] = np.einsum("nij,njk->nik", clean_rotation, noise)
            gyro[:, segment_index] += rng.normal(0.0, 0.006, (total, 3))

    if b5_control == "noise_amplification":
        alternating = ((-1.0) ** np.arange(total))[:, None, None]
        gyro += alternating * rng.normal(0.0, 0.055, (1, len(SEGMENTS), 3))

    lever_truth = _b5_truth_lever_arms(mounts)
    sensor_position = _b5_sensor_positions(board_to_world_all, lever_truth)
    if b5_control == "articulation_eligibility_failure":
        rows = action_rows["arms"]
        local_phase = np.linspace(0.0, 1.0, len(rows))
        # A 25 mm non-rigid sensor/segment excursion is large enough to be a
        # clear articulation/strap-migration control, while remaining in the
        # scale of a physically possible bad episode rather than an impulse.
        slip = 0.025 * np.sin(math.pi * local_phase) ** 2 * np.sin(8.0 * math.pi * local_phase)
        sensor_position[rows, SEGMENTS.index("forearm_L"), 1] += slip

    time_s = time_ns.astype(float) / 1e9
    velocity = np.gradient(sensor_position, time_s, axis=0, edge_order=2)
    acceleration_world = np.gradient(velocity, time_s, axis=0, edge_order=2)
    gravity = np.array([0.0, 0.0, -float(contract["q2"]["gravity_mps2"])])
    accel = np.einsum(
        "nsji,nsj->nsi", board_to_world_all, acceleration_world - gravity[None, None, :],
    )
    if noisy or b5_control == "noise_amplification":
        accel_noise = float(contract["synthetic"]["accel_noise_mps2"])
        if b5_control == "noise_amplification":
            accel_noise *= 8.0
        accel += rng.normal(0.0, accel_noise, accel.shape)
    if b5_control == "timing_offset":
        segment_index = SEGMENTS.index("forearm_R")
        for rows in action_rows.values():
            accel[rows, segment_index] = np.roll(accel[rows, segment_index], 3, axis=0)

    valid = np.ones((total, len(SEGMENTS)), dtype=bool)
    if noisy:
        for segment_index in (2, 7):
            start = 45 + 3 * segment_index
            valid[start:start + 2, segment_index] = False
    r3d_actions = {
        action: {
            "BROAD_ACTIVE_ROWS": action_rows[action].tolist(),
            "STATIC_PLATEAU_CANDIDATE": {"row_indices": static_rows[action].tolist()},
            **({
                "EPISODE_PHASE_ROWS": {
                    phase: rows.tolist() for phase, rows in episode_phase_rows[action].items()
                    if not phase.startswith("COMMON_ROTATION_AXIS_")
                },
                "same_rest_required": True,
                "b3_minimal_motion": True,
            } if action in B3_ACTIONS else {}),
            **({
                "EPISODE_PHASE_ROWS": {
                    phase: rows.tolist() for phase, rows in episode_phase_rows[action].items()
                    if not phase.startswith("COMMON_ROTATION_AXIS_")
                },
                "B4_COMMON_RATE_PHASE_ROWS": {
                    phase: episode_phase_rows[action][phase].tolist()
                    for phase in ("COMMON_ROTATION_AXIS_A", "COMMON_ROTATION_AXIS_B")
                },
                "same_rest_required": True,
                "b4_supported_braced_en_bloc": True,
            } if action == B4_EN_BLOC else {}),
            "status": "PASS",
        }
        for action in UNIFIED_ACTIONS
    }
    observation = R1Observation(
        time_ns=time_ns,
        node_order=node_order,
        rotation=rotation,
        gyro_rad_s=gyro,
        valid=valid,
        windows=windows,
        node_to_segment=node_to_segment,
        r3d_actions=r3d_actions,
        source="EXACT_TIME_RESOLVED_UNIFIED_SYNTHETIC",
        accel_mps2=accel,
    )

    truth = np.zeros(FULL_DIMENSION)
    cursor = 0
    for segment in SEGMENTS:
        axis_board = mounts[segment].T @ np.array([0.0, 0.0, 1.0])
        truth[cursor:cursor + 2] = angles_from_axis(axis_board)
        cursor += 2
    for segment in SEGMENTS[1:]:
        truth[cursor] = headings[segment]
        cursor += 1
    for joint in JOINTS:
        parent, _ = JOINTS[joint]
        truth[cursor:cursor + 2] = angles_from_axis(mounts[parent].T @ local_axes[joint])
        cursor += 2
    truth[cursor:cursor + 3] = Rotation.from_matrix(mounts["pelvis"].T).as_rotvec()
    cursor += 3

    # The seven zeros are capture-defined from the same exact initial rest and
    # therefore have no clinical-zero claim.
    initial_row = action_rows["initial_still_attempt2"][0]
    for zero_name in ZERO_JOINTS:
        if zero_name == "trunk":
            parent, child = "pelvis", "torso"
            axis_world = world_segment[initial_row, SEGMENTS.index("pelvis")] @ np.array([1.0, 0.0, 0.0])
        else:
            parent, child = JOINTS[zero_name]
            axis_world = world_segment[initial_row, SEGMENTS.index(parent)] @ local_axes[zero_name]
        dp = world_segment[initial_row, SEGMENTS.index(parent)][:, 2]
        dc = world_segment[initial_row, SEGMENTS.index(child)][:, 2]
        truth[cursor] = math.atan2(float(axis_world @ np.cross(dp, dc)), float(dp @ dc))
        cursor += 1
    assert cursor == PRODUCT_DIMENSION
    truth[PRODUCT_DIMENSION:PRODUCT_DIMENSION + 17] = q_initial
    truth[PRODUCT_DIMENSION + 17:PRODUCT_DIMENSION + POSE_NUISANCE_DIMENSION] = q_tpose
    cursor = PRODUCT_DIMENSION + POSE_NUISANCE_DIMENSION
    for endpoint in B5_LEVER_ENDPOINTS:
        truth[cursor:cursor + 3] = lever_truth[endpoint]
        cursor += 3
    assert cursor == FULL_DIMENSION

    complete_episode_audit = {}
    for action in COMPLETE_EPISODE_ACTIONS:
        action_index = UNIFIED_ACTIONS.index(action)
        first = action_index * rows_per_action
        last = (action_index + 1) * rows_per_action - 1
        closure = Rotation.from_matrix(np.einsum(
            "sji,sjk->sik", world_segment[first], world_segment[last],
        )).magnitude()
        complete_episode_audit[action] = {
            "phase_row_counts": {
                phase: int(len(rows)) for phase, rows in episode_phase_rows[action].items()
                if not phase.startswith("COMMON_ROTATION_AXIS_")
            },
            "same_rest_max_segment_geodesic_deg": math.degrees(float(np.max(closure))),
            "complete_rest_transition_action_return_same_rest": bool(
                all(len(rows) > 0 for rows in episode_phase_rows[action].values())
                and float(np.max(closure)) < 1e-10
            ),
        }

    metadata = {
        "schema": "biospur-unified-calibration-synthetic-truth-v1",
        "seed": seed,
        "noisy": noisy,
        "weak_motion": weak_motion,
        "b4_negative_control": b4_control,
        "b5_negative_control": b5_control,
        "world_segment_rotation": world_segment,
        "b5_sensor_position": sensor_position,
        "segment_directions": world_segment[:, :, :, 2],
        "complete_episode_phases_present": True,
        "b3_episode_audit": {
            action: complete_episode_audit[action] for action in B3_ACTIONS
        },
        "b4_episode_audit": complete_episode_audit[B4_EN_BLOC],
        "b4_phase_row_counts": {
            phase: int(len(episode_phase_rows[B4_EN_BLOC][phase]))
            for phase in ("COMMON_ROTATION_AXIS_A", "COMMON_ROTATION_AXIS_B")
        },
        "b3_label_distinctions": {
            B3_HIP_CIRCUMDUCTION: "NOT_CAPTURE2_03_PELVIS_HULA: pelvis held fixed; requires bilateral two-axis pelvis-to-thigh relative motion",
            B3_KNEE_LEFT: "NOT_CAPTURE2_10_KNEE_LEFT_SEATED: requires stationary thigh plus separate flexion and tibial-axial phases",
            B3_KNEE_RIGHT: "NOT_CAPTURE2_11_KNEE_RIGHT_SEATED: requires stationary thigh plus separate flexion and tibial-axial phases",
            B3_TRUNK_LATERAL: "NEW_LABELLED_LEFT_RIGHT_LATERAL_BEND: signed excursions beyond existing axial/flexion trunk actions",
        },
        "truth_firewall": "truth returned separately and absent from R1Observation",
    }
    return observation, truth, metadata
