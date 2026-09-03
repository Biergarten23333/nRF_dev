"""Independent analytic truth generator for C2 estimator qualification.

This module deliberately does not import estimator geometry, mount, factor,
objective, solver, or progressive modules. It constructs its own kinematic
tree and raw six-axis signals so estimator implementation bugs are not copied
into the synthetic oracle.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.v0.math3d import matrix_to_quat_wxyz
from biospur_fusion.v0.raw6_heading import Raw6Episode


TRUTH_SEGMENTS = (
    "pelvis", "torso", "upper_arm_left", "forearm_left",
    "upper_arm_right", "forearm_right", "thigh_left", "shank_left",
    "thigh_right", "shank_right",
)
TRUTH_EDGES = (
    ("pelvis_torso", "pelvis", "torso"),
    ("shoulder_left", "torso", "upper_arm_left"),
    ("elbow_left", "upper_arm_left", "forearm_left"),
    ("shoulder_right", "torso", "upper_arm_right"),
    ("elbow_right", "upper_arm_right", "forearm_right"),
    ("hip_left", "pelvis", "thigh_left"),
    ("knee_left", "thigh_left", "shank_left"),
    ("hip_right", "pelvis", "thigh_right"),
    ("knee_right", "thigh_right", "shank_right"),
)
TRUTH_EPISODES = (
    "00_initial_still", "02_t_pose", "03_pelvis_hula_circle",
    "04_shoulder_left", "05_shoulder_right", "06_elbow_left",
    "07_elbow_right", "08_hip_left", "09_hip_right",
    "10_knee_left_seated", "11_knee_right_seated", "12_heel_raise_left",
    "13_heel_raise_right", "14_trunk_flex_extend", "15_trunk_axial_rotation",
    "16_squat", "17_final_still", "18_heel_to_butt_left",
    "19_heel_to_butt_right",
)


@dataclass(frozen=True)
class SyntheticTruth:
    episodes: tuple[Raw6Episode, ...]
    headings_rad: np.ndarray
    axial_offsets_m: np.ndarray
    body_from_sensor: Mapping[str, np.ndarray]
    hinge_axes_sensor: Mapping[str, tuple[np.ndarray, np.ndarray]]
    seed: int
    noise: Mapping[str, float]


def _right_handed_mount(minus_z_body: np.ndarray) -> np.ndarray:
    minus_z = np.asarray(minus_z_body, dtype=float)
    minus_z /= np.linalg.norm(minus_z)
    y = np.array([0.0, 0.0, 1.0])
    z = -minus_z
    x = np.cross(y, z); x /= np.linalg.norm(x)
    return np.column_stack((x, y, z))


def _nominal_mounts() -> dict[str, np.ndarray]:
    rear = math.radians(30.0)
    direction = {
        "forearm_left": np.array([0.0, 1.0, 0.0]),
        "forearm_right": np.array([0.0, -1.0, 0.0]),
        "upper_arm_left": np.array([-math.cos(rear), math.sin(rear), 0.0]),
        "upper_arm_right": np.array([-math.cos(rear), -math.sin(rear), 0.0]),
        "torso": np.array([1.0, 0.0, 0.0]),
        "pelvis": np.array([1.0, 0.0, 0.0]),
        "thigh_left": np.array([1.0, 0.0, 0.0]),
        "thigh_right": np.array([1.0, 0.0, 0.0]),
        "shank_left": np.array([0.0, 1.0, 0.0]),
        "shank_right": np.array([0.0, -1.0, 0.0]),
    }
    return {segment: _right_handed_mount(value) for segment, value in direction.items()}


def _edge_points(offsets: np.ndarray) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    lengths = {
        "upper_arm_left": 0.3175, "forearm_left": 0.255,
        "upper_arm_right": 0.3175, "forearm_right": 0.255,
        "thigh_left": 0.48, "shank_left": 0.43,
        "thigh_right": 0.48, "shank_right": 0.43,
    }
    limb_order = (
        "upper_arm_left", "forearm_left", "upper_arm_right", "forearm_right",
        "thigh_left", "shank_left", "thigh_right", "shank_right",
    )
    limb = {
        segment: (
            np.array([0.0, 0.0, lengths[segment] - offsets[index]]),
            np.array([0.0, 0.0, -offsets[index]]),
        )
        for index, segment in enumerate(limb_order)
    }
    return {
        "pelvis_torso": (np.array([0.0, 0.0, 0.14]), np.array([0.0, 0.0, -0.14])),
        "shoulder_left": (np.array([0.0, 0.20625, 0.145]), limb["upper_arm_left"][0]),
        "elbow_left": (limb["upper_arm_left"][1], limb["forearm_left"][0]),
        "shoulder_right": (np.array([0.0, -0.20625, 0.145]), limb["upper_arm_right"][0]),
        "elbow_right": (limb["upper_arm_right"][1], limb["forearm_right"][0]),
        "hip_left": (np.array([0.0, 0.11, -0.06]), limb["thigh_left"][0]),
        "knee_left": (limb["thigh_left"][1], limb["shank_left"][0]),
        "hip_right": (np.array([0.0, -0.11, -0.06]), limb["thigh_right"][0]),
        "knee_right": (limb["thigh_right"][1], limb["shank_right"][0]),
    }


def _phase(n: int) -> np.ndarray:
    boundaries = (0, 40, 80, n - 80, n - 40, n)
    labels = np.empty(n, dtype="U40")
    names = (
        "VERIFIED_PRE_REST", "REST_TO_ACTION_TRANSITION", "FORMAL_ACTION_OR_HOLD",
        "ACTION_TO_REST_TRANSITION", "VERIFIED_POST_REST",
    )
    for name, left, right in zip(names, boundaries[:-1], boundaries[1:]):
        labels[left:right] = name
    return labels


def _motion_wave(n: int) -> np.ndarray:
    phase = _phase(n)
    wave = np.zeros(n)
    active = np.flatnonzero(phase == "FORMAL_ACTION_OR_HOLD")
    transition_in = np.flatnonzero(phase == "REST_TO_ACTION_TRANSITION")
    transition_out = np.flatnonzero(phase == "ACTION_TO_REST_TRANSITION")
    wave[transition_in] = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, len(transition_in))))
    # Begin and end the formal action at the held transition amplitude with
    # zero slope, avoiding a synthetic acceleration impulse at phase joins.
    wave[active] = np.cos(np.linspace(0.0, 4.0 * np.pi, len(active)))
    wave[transition_out] = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, len(transition_out))))
    return wave


def _rotvec_series(axis: Sequence[float], angle: np.ndarray) -> np.ndarray:
    axis = np.asarray(axis, dtype=float); axis /= np.linalg.norm(axis)
    return Rotation.from_rotvec(angle[:, None] * axis[None, :]).as_matrix()


def _body_rotations(action: str, n: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    wave = _motion_wave(n)
    identity = np.repeat(np.eye(3)[None, :, :], n, axis=0)
    relative = {edge: identity.copy() for edge, _, _ in TRUTH_EDGES}
    root = _rotvec_series([0.2, 0.1, 1.0], 0.015 * np.sin(np.linspace(0, 2 * np.pi, n)))

    def set_edge(edge: str, axes: Sequence[tuple[Sequence[float], float]]) -> None:
        rotation = identity.copy()
        for axis, amplitude in axes:
            rotation = np.einsum("nij,njk->nik", rotation, _rotvec_series(axis, amplitude * wave))
        relative[edge] = rotation

    if action == "02_t_pose":
        set_edge("shoulder_left", [([1, 0, 0], -1.05), ([0, 0, 1], 0.12)])
        set_edge("shoulder_right", [([1, 0, 0], 1.00), ([0, 0, 1], -0.10)])
    elif action == "03_pelvis_hula_circle":
        set_edge("pelvis_torso", [([1, 0, 0], 0.25), ([0, 1, 0], 0.20)])
    elif action == "04_shoulder_left":
        set_edge("shoulder_left", [([1, 0, 0], 0.85), ([0, 1, 0], 0.40)])
    elif action == "05_shoulder_right":
        set_edge("shoulder_right", [([1, 0, 0], -0.82), ([0, 1, 0], 0.36)])
    elif action == "06_elbow_left":
        set_edge("elbow_left", [([0, 1, 0], 1.25), ([0, 0, 1], 0.12)])
    elif action == "07_elbow_right":
        set_edge("elbow_right", [([0, 1, 0], -1.18), ([0, 0, 1], -0.10)])
    elif action == "08_hip_left":
        set_edge("hip_left", [([0, 1, 0], 0.78), ([1, 0, 0], 0.28)])
    elif action == "09_hip_right":
        set_edge("hip_right", [([0, 1, 0], -0.75), ([1, 0, 0], -0.25)])
    elif action in {"10_knee_left_seated", "18_heel_to_butt_left"}:
        set_edge("knee_left", [([0, 1, 0], 1.15 if action.startswith("10") else 1.75)])
        set_edge("hip_left", [([0, 1, 0], 0.20)])
    elif action in {"11_knee_right_seated", "19_heel_to_butt_right"}:
        set_edge("knee_right", [([0, 1, 0], -1.12 if action.startswith("11") else -1.70)])
        set_edge("hip_right", [([0, 1, 0], -0.18)])
    elif action == "12_heel_raise_left":
        set_edge("knee_left", [([0, 1, 0], 0.22)])
    elif action == "13_heel_raise_right":
        set_edge("knee_right", [([0, 1, 0], -0.20)])
    elif action == "14_trunk_flex_extend":
        set_edge("pelvis_torso", [([0, 1, 0], 0.55), ([1, 0, 0], 0.08)])
    elif action == "15_trunk_axial_rotation":
        set_edge("pelvis_torso", [([0, 0, 1], 0.62), ([0, 1, 0], 0.10)])
    elif action == "16_squat":
        set_edge("hip_left", [([0, 1, 0], 0.82)])
        set_edge("hip_right", [([0, 1, 0], -0.78)])
        set_edge("knee_left", [([0, 1, 0], -1.02)])
        set_edge("knee_right", [([0, 1, 0], 0.98)])
    # Natural human coupling prevents a perfect robot/symmetry generator.
    for edge in relative:
        phase = rng.uniform(-np.pi, np.pi)
        coupling = 0.015 * wave * np.sin(np.linspace(0, 2.2 * np.pi, n) + phase)
        relative[edge] = np.einsum(
            "nij,njk->nik", relative[edge], _rotvec_series([1.0, 0.3, 0.2], coupling),
        )
    absolute = {"pelvis": root}
    for edge, parent, child in TRUTH_EDGES:
        absolute[child] = np.einsum("nij,njk->nik", absolute[parent], relative[edge])
    return absolute


def _angular_velocity_sensor(rotation_world_sensor: np.ndarray, dt: float) -> np.ndarray:
    n = len(rotation_world_sensor)
    gyro = np.zeros((n, 3))
    for index in range(1, n - 1):
        delta = rotation_world_sensor[index - 1].T @ rotation_world_sensor[index + 1]
        gyro[index] = Rotation.from_matrix(delta).as_rotvec() / (2.0 * dt)
    gyro[0] = gyro[1]; gyro[-1] = gyro[-2]
    return gyro


def generate_synthetic_truth(
    *, seed: int = 20260829, noise_mps2: float = 0.03, noise_rad_s: float = 0.003,
    episode_order: Sequence[str] = TRUTH_EPISODES, n: int = 300, rate_hz: int = 50,
    common_translation_excitation: bool = True,
) -> SyntheticTruth:
    rng = np.random.default_rng(seed)
    nominal = _nominal_mounts()
    mounts = {}
    for segment, matrix in nominal.items():
        # Random human-worn deviation remains inside the broad qualitative cone.
        correction = Rotation.from_rotvec(rng.normal(0.0, math.radians(5.0), 3)).as_matrix()
        mounts[segment] = correction @ matrix
    offsets = np.array([0.05, 0.04, 0.05, 0.04, 0.08, 0.08, 0.08, 0.08])
    offsets += rng.normal(0.0, 0.006, len(offsets))
    points = _edge_points(offsets)
    yaw_offsets = {"pelvis": 0.0}
    yaw_offsets.update({segment: rng.uniform(-np.pi, np.pi) for segment in TRUTH_SEGMENTS[1:]})
    truth_headings = np.asarray([-yaw_offsets[segment] for segment in TRUTH_SEGMENTS[1:]])
    episodes = []
    dt = 1.0 / rate_hz
    for episode_index, action in enumerate(episode_order):
        body_rotation = _body_rotations(action, n, rng)
        sensor_rotation_true = {
            segment: np.einsum("nij,jk->nik", body_rotation[segment], mounts[segment])
            for segment in TRUTH_SEGMENTS
        }
        # Smooth common-body translation supplies the horizontal specific-force
        # excitation needed to observe relative yaw at non-hinge connections.
        # It is generated independently of estimator factors and is disabled
        # for still episodes so initial_still remains deliberately weak.
        common_position = np.zeros((n, 3))
        if common_translation_excitation and action not in {"00_initial_still", "17_final_still"}:
            direction = rng.normal(size=2)
            direction /= np.linalg.norm(direction)
            amplitude = rng.uniform(0.035, 0.060)
            common_position[:, :2] = (
                amplitude * _motion_wave(n)[:, None] * direction[None, :]
            )
        origins = {"pelvis": common_position}
        for edge, parent, child in TRUTH_EDGES:
            parent_point, child_point = points[edge]
            joint = origins[parent] + np.einsum("nij,j->ni", body_rotation[parent], parent_point)
            origins[child] = joint - np.einsum("nij,j->ni", body_rotation[child], child_point)
        acceleration = {
            segment: np.gradient(np.gradient(origin, dt, axis=0, edge_order=2), dt, axis=0, edge_order=2)
            for segment, origin in origins.items()
        }
        acc = {}
        gyro = {}
        observed_rotation = {}
        quat = {}
        for segment in TRUTH_SEGMENTS:
            gravity = np.array([0.0, 0.0, -9.80665])
            force_world = acceleration[segment] - gravity
            acc_sensor = np.einsum("nji,nj->ni", sensor_rotation_true[segment], force_world)
            gyro_sensor = _angular_velocity_sensor(sensor_rotation_true[segment], dt)
            acc[segment] = acc_sensor + rng.normal(0.0, noise_mps2, acc_sensor.shape)
            gyro[segment] = gyro_sensor + rng.normal(0.0, noise_rad_s, gyro_sensor.shape)
            observed = np.einsum(
                "ij,njk->nik",
                Rotation.from_rotvec(np.array([0.0, 0.0, yaw_offsets[segment]])).as_matrix(),
                sensor_rotation_true[segment],
            )
            observed_rotation[segment] = observed
            quat[segment] = matrix_to_quat_wxyz(observed)
        time_ns = (
            episode_index * 10_000_000_000 + np.arange(n, dtype=np.int64) * int(round(1e9 / rate_hz))
        )
        phase = _phase(n)
        episodes.append(Raw6Episode(
            capture="SYNTHETIC_C2_INDEPENDENT",
            action=action,
            partition="CUMULATIVE_PROFILE",
            time_ns=time_ns,
            phase=phase,
            acc=acc,
            gyro=gyro,
            rotation_world_sensor=observed_rotation,
            quat_world_sensor_wxyz=quat,
            rest_detected={segment: np.isin(phase, ["VERIFIED_PRE_REST", "VERIFIED_POST_REST"]) for segment in TRUTH_SEGMENTS},
            bias_rad_s={segment: np.zeros((n, 3)) for segment in TRUTH_SEGMENTS},
            audit={
                "generator": "INDEPENDENT_ANALYTIC_TRUTH",
                "estimator_module_imported": False,
                "randomized_mounts": True,
                "imperfect_human_coupling": True,
                "common_translation_excitation": common_translation_excitation,
                "noise_mps2": noise_mps2,
                "noise_rad_s": noise_rad_s,
            },
        ))
    hinge_axes = {
        "elbow_left": (mounts["upper_arm_left"].T @ np.array([0.0, 1.0, 0.0]), mounts["forearm_left"].T @ np.array([0.0, 1.0, 0.0])),
        "elbow_right": (mounts["upper_arm_right"].T @ np.array([0.0, 1.0, 0.0]), mounts["forearm_right"].T @ np.array([0.0, 1.0, 0.0])),
        "knee_left": (mounts["thigh_left"].T @ np.array([0.0, 1.0, 0.0]), mounts["shank_left"].T @ np.array([0.0, 1.0, 0.0])),
        "knee_right": (mounts["thigh_right"].T @ np.array([0.0, 1.0, 0.0]), mounts["shank_right"].T @ np.array([0.0, 1.0, 0.0])),
    }
    return SyntheticTruth(
        tuple(episodes), truth_headings, offsets, mounts, hinge_axes, seed,
        {"accelerometer_mps2": noise_mps2, "gyroscope_rad_s": noise_rad_s},
    )
