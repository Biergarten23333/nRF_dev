"""Independent human-like raw-IMU generator for D0B-R1.

This module intentionally does not import the R1 estimator, residual, replay,
or parameterization.  It emits raw integer IMU records and keeps truth in a
separate object that the estimator never receives.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation


ACTIONS = (
    "initial_still_attempt2", "t_pose", "arms", "left_elbow",
    "right_elbow_attempt2", "left_knee", "right_knee", "left_heel",
    "right_heel", "squats", "trunk",
)
SEGMENTS = (
    "pelvis", "torso", "upper_arm_L", "upper_arm_R", "forearm_L",
    "forearm_R", "thigh_L", "thigh_R", "shank_L", "shank_R",
)


def _unit(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, float)
    return value / max(float(np.linalg.norm(value)), 1e-12)


def _rotate(axis: np.ndarray, angle: float, vector: np.ndarray) -> np.ndarray:
    return Rotation.from_rotvec(_unit(axis) * float(angle)).apply(vector)


def _frame_from_long_axis(direction: np.ndarray, twist: float = 0.0) -> np.ndarray:
    z = _unit(direction)
    seed = np.array([1.0, 0.0, 0.0]) if abs(z[0]) < 0.85 else np.array([0.0, 1.0, 0.0])
    x = _unit(seed - z * float(seed @ z))
    y = _unit(np.cross(z, x))
    base = np.column_stack((x, y, z))
    return base @ Rotation.from_rotvec([0.0, 0.0, twist]).as_matrix()


def _neutral_directions() -> dict[str, np.ndarray]:
    return {
        "pelvis": _unit([0.02, 0.01, 1.0]),
        "torso": _unit([-0.04, 0.02, 1.0]),
        "upper_arm_L": _unit([0.04, 0.02, -1.0]),
        "upper_arm_R": _unit([-0.03, -0.02, -1.0]),
        "forearm_L": _unit([0.08, 0.05, -1.0]),
        "forearm_R": _unit([-0.07, -0.04, -1.0]),
        "thigh_L": _unit([0.03, 0.02, -1.0]),
        "thigh_R": _unit([-0.02, -0.01, -1.0]),
        "shank_L": _unit([-0.02, 0.01, -1.0]),
        "shank_R": _unit([0.02, -0.01, -1.0]),
    }


def _directions_for_action(action: str, phase: float, seed_phase: float) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    d = {name: value.copy() for name, value in _neutral_directions().items()}
    twist = {name: 0.0 for name in SEGMENTS}
    sway = 0.025 * math.sin(2.0 * math.pi * (0.31 * phase + seed_phase))
    root = Rotation.from_euler("xy", [0.6 * sway, -0.4 * sway]).as_matrix()
    for name in d:
        d[name] = root @ d[name]
    active = math.sin(math.pi * min(1.0, max(0.0, phase))) ** 2
    wave = active * (0.72 * math.sin(2.0 * math.pi * 2.35 * phase) + 0.16 * math.sin(2.0 * math.pi * 0.77 * phase + 0.4))
    lateral_l = np.array([1.0, 0.0, 0.0])
    lateral_r = np.array([-1.0, 0.0, 0.0])
    forward = np.array([0.0, 1.0, 0.0])
    if action == "t_pose":
        d["upper_arm_L"] = _unit([1.0, 0.03, 0.05])
        d["upper_arm_R"] = _unit([-1.0, -0.04, -0.02])
        d["forearm_L"] = _unit([1.0, 0.06, 0.02])
        d["forearm_R"] = _unit([-1.0, -0.03, -0.06])
    elif action == "arms":
        angle = 0.72 + 0.72 * math.sin(2.0 * math.pi * 1.25 * phase) * active
        d["upper_arm_L"] = _rotate(forward, angle, [0.0, 0.0, -1.0])
        d["upper_arm_R"] = _rotate(forward, -angle * 0.94, [0.0, 0.0, -1.0])
        d["forearm_L"] = _rotate(forward, angle + 0.12 * wave, [0.0, 0.0, -1.0])
        d["forearm_R"] = _rotate(forward, -angle * 0.94 - 0.10 * wave, [0.0, 0.0, -1.0])
    elif action in ("left_elbow", "right_elbow_attempt2"):
        side = "L" if action == "left_elbow" else "R"
        sign = 1.0 if side == "L" else -1.0
        parent = f"upper_arm_{side}"; child = f"forearm_{side}"
        d[parent] = _unit([0.08 * sign, 0.02, -1.0])
        # Protocol compound action: curl first, then pronation/supination.
        curl_phase = min(1.0, phase / 0.55)
        curl_active = math.sin(math.pi * curl_phase) ** 2 if phase <= 0.55 else 0.0
        curl_wave = curl_active * math.sin(2.0 * math.pi * 2.2 * curl_phase)
        d[child] = _rotate(lateral_l if side == "L" else lateral_r, 0.75 + 0.72 * curl_wave, d[parent])
        if phase >= 0.45:
            pronation_phase = (phase - 0.45) / 0.55
            pronation_active = math.sin(math.pi * pronation_phase) ** 2
            twist[child] = 0.65 * pronation_active * math.sin(2.0 * math.pi * 2.0 * pronation_phase + 0.2 * sign)
    elif action in ("left_knee", "right_knee"):
        side = "L" if action == "left_knee" else "R"
        sign = 1.0 if side == "L" else -1.0
        thigh = f"thigh_{side}"; shank = f"shank_{side}"
        hip = 0.85 + 0.62 * wave
        d[thigh] = _rotate([sign, 0.0, 0.0], hip, [0.0, 0.0, -1.0])
        d[shank] = _rotate([sign, 0.0, 0.0], hip - 0.22 - 0.16 * wave, [0.0, 0.0, -1.0])
    elif action in ("left_heel", "right_heel"):
        side = "L" if action == "left_heel" else "R"
        sign = 1.0 if side == "L" else -1.0
        thigh = f"thigh_{side}"; shank = f"shank_{side}"
        d[thigh] = _unit([0.02 * sign, 0.03, -1.0])
        d[shank] = _rotate([sign, 0.0, 0.0], -1.15 - 0.70 * wave, d[thigh])
    elif action == "squats":
        squat = 0.72 + 0.48 * math.sin(2.0 * math.pi * 1.1 * phase) * active
        d["pelvis"] = _unit([0.0, 0.10 * squat, 1.0])
        d["torso"] = _unit([0.0, -0.16 * squat, 1.0])
        d["thigh_L"] = _rotate([1.0, 0.0, 0.0], squat, [0.0, 0.0, -1.0])
        d["thigh_R"] = _rotate([-1.0, 0.0, 0.0], -0.93 * squat, [0.0, 0.0, -1.0])
        d["shank_L"] = _rotate([1.0, 0.0, 0.0], -0.78 * squat, [0.0, 0.0, -1.0])
        d["shank_R"] = _rotate([-1.0, 0.0, 0.0], 0.70 * squat, [0.0, 0.0, -1.0])
    elif action == "trunk":
        if phase < 0.34:
            value = 0.55 * math.sin(math.pi * phase / 0.34)
            twist["torso"] = value
        elif phase < 0.67:
            value = -0.50 * math.sin(math.pi * (phase - 0.34) / 0.33)
            twist["torso"] = value
        else:
            value = 0.48 * math.sin(math.pi * (phase - 0.67) / 0.33)
            d["torso"] = _rotate([1.0, 0.0, 0.0], value, d["torso"])
        d["pelvis"] = _rotate([0.0, 0.0, 1.0], 0.12 * twist["torso"], d["pelvis"])
    return {name: _unit(value) for name, value in d.items()}, twist


def _graphical_nodes(directions: Mapping[str, np.ndarray], lengths: Mapping[str, float]) -> np.ndarray:
    names = ["pelvis", "central", "shoulder_L", "shoulder_R", "elbow_L", "elbow_R", "wrist_L", "wrist_R", "hip_L", "hip_R", "knee_L", "knee_R", "ankle_L", "ankle_R"]
    node = {name: np.zeros(3) for name in names}
    node["central"] = node["pelvis"] + lengths["torso"] * directions["torso"]
    lateral = _unit(np.cross([0.0, 1.0, 0.0], directions["torso"]))
    node["shoulder_L"] = node["central"] + 0.5 * lengths["shoulder_width"] * lateral
    node["shoulder_R"] = node["central"] - 0.5 * lengths["shoulder_width"] * lateral
    node["hip_L"] = node["pelvis"] + 0.5 * lengths["hip_width"] * lateral
    node["hip_R"] = node["pelvis"] - 0.5 * lengths["hip_width"] * lateral
    for side in ("L", "R"):
        node[f"elbow_{side}"] = node[f"shoulder_{side}"] + lengths[f"upper_arm_{side}"] * directions[f"upper_arm_{side}"]
        node[f"wrist_{side}"] = node[f"elbow_{side}"] + lengths[f"forearm_{side}"] * directions[f"forearm_{side}"]
        node[f"knee_{side}"] = node[f"hip_{side}"] + lengths[f"thigh_{side}"] * directions[f"thigh_{side}"]
        node[f"ankle_{side}"] = node[f"knee_{side}"] + lengths[f"shank_{side}"] * directions[f"shank_{side}"]
    return np.stack([node[name] for name in names])


def generate_raw_imu_case(contract: Mapping[str, Any], seed: int) -> tuple[dict[str, np.ndarray], dict[str, tuple[int, int]], dict[str, str], dict[str, Any]]:
    cfg = contract["synthetic"]
    rng = np.random.default_rng(seed)
    hz = float(cfg["sample_rate_hz"])
    duration = float(cfg["action_duration_s"])
    transition = float(cfg["inter_action_transition_s"])
    total_duration = len(ACTIONS) * duration + (len(ACTIONS) - 1) * transition
    count = int(round(total_duration * hz)) + 1
    base_ns = 5_000_000_000
    jitter = rng.integers(-int(cfg["timestamp_jitter_ns"]), int(cfg["timestamp_jitter_ns"]) + 1, count)
    jitter[0] = 0
    time_ns = base_ns + np.rint(np.arange(count) / hz * 1e9).astype(np.int64) + jitter
    time_ns = np.maximum.accumulate(time_ns)
    windows: dict[str, tuple[int, int]] = {}
    cursor = 0.0
    action_at = np.full(count, -1, int)
    phase_at = np.zeros(count)
    for index, action in enumerate(ACTIONS):
        start, stop = cursor, cursor + duration
        windows[action] = (base_ns + int(round(start * 1e9)), base_ns + int(round(stop * 1e9)))
        rows = np.flatnonzero((time_ns >= windows[action][0]) & (time_ns <= windows[action][1]))
        action_at[rows] = index
        phase_at[rows] = (time_ns[rows] - windows[action][0]) / max(1, windows[action][1] - windows[action][0])
        cursor = stop + (transition if index + 1 < len(ACTIONS) else 0.0)
    node_to_segment = {f"SYN_{index:02d}": segment for index, segment in enumerate(SEGMENTS)}
    segment_to_node = {segment: node for node, segment in node_to_segment.items()}
    mounts = {segment: Rotation.random(random_state=rng).as_matrix() for segment in SEGMENTS}
    world_segment = np.empty((count, len(SEGMENTS), 3, 3))
    directions = np.empty((count, len(SEGMENTS), 3))
    last_d, last_twist = _directions_for_action(ACTIONS[0], 0.0, 0.01 * (seed % 17))
    for row in range(count):
        index = action_at[row]
        if index >= 0:
            last_d, last_twist = _directions_for_action(ACTIONS[index], float(phase_at[row]), 0.01 * (seed % 17))
        for segment_index, segment in enumerate(SEGMENTS):
            directions[row, segment_index] = last_d[segment]
            world_segment[row, segment_index] = _frame_from_long_axis(last_d[segment], last_twist[segment])
    lengths = contract["generic_rendering_lengths_m"]
    truth_nodes = np.stack([_graphical_nodes({segment: directions[row, i] for i, segment in enumerate(SEGMENTS)}, lengths) for row in range(count)])
    dtype = np.dtype([
        ("global_time_ns", "<i8"), ("status", "u1"), ("acc_raw", "<i2", (3,)),
        ("gyro_raw", "<i2", (3,)), ("boot_epoch", "<i8"),
    ])
    imus: dict[str, np.ndarray] = {}
    board_rotations = np.empty((count, len(SEGMENTS), 3, 3))
    biases = {}
    gravity = np.array([0.0, 0.0, float(contract["q2"]["gravity_mps2"])])
    for segment_index, segment in enumerate(SEGMENTS):
        node = segment_to_node[segment]
        r_bs = mounts[segment]
        r_wb = np.einsum("nij,jk->nik", world_segment[:, segment_index], r_bs.T)
        board_rotations[:, segment_index] = r_wb
        gyro = np.zeros((count, 3))
        for row in range(1, count):
            dt = max(1e-6, (int(time_ns[row]) - int(time_ns[row - 1])) / 1e9)
            gyro[row] = Rotation.from_matrix(r_wb[row - 1].T @ r_wb[row]).as_rotvec() / dt
        bias = np.deg2rad(rng.normal(0.0, float(cfg["gyro_bias_sigma_dps"]), 3))
        biases[node] = bias
        gyro += bias + np.deg2rad(rng.normal(0.0, float(cfg["gyro_noise_sigma_dps"]), gyro.shape))
        accel = np.einsum("nji,j->ni", r_wb, gravity)
        accel += rng.normal(0.0, float(cfg["accel_noise_mps2"]), accel.shape)
        record = np.zeros(count, dtype=dtype)
        record["global_time_ns"] = time_ns
        record["status"] = 1
        record["boot_epoch"] = 0
        record["acc_raw"] = np.clip(np.rint(accel / gravity[2] * float(contract["q2"]["accel_lsb_per_g"])), -32768, 32767).astype(np.int16)
        record["gyro_raw"] = np.clip(np.rint(np.rad2deg(gyro) * float(contract["q2"]["gyro_lsb_per_dps"])), -32768, 32767).astype(np.int16)
        gap_start = int((2.2 + 0.03 * segment_index) * hz)
        gap_count = max(1, int(round(float(cfg["deterministic_gap_duration_s"]) * hz)))
        record["status"][gap_start:gap_start + gap_count] = 0
        imus[node] = record
    truth = {
        "seed": seed,
        "time_ns": time_ns,
        "segment_order": SEGMENTS,
        "world_segment_rotation": world_segment,
        "segment_direction": directions,
        "graphical_nodes": truth_nodes,
        "board_rotation": board_rotations,
        "mounting_R_BS": mounts,
        "gyro_bias_rad_s": biases,
        "generator_uses_estimator_code": False,
    }
    return imus, windows, node_to_segment, truth
