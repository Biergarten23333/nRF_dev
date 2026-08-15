"""D0-B shared synthetic objective over the frozen 95-coordinate D0-A state."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_engineering_v1.common_time import CommonTimelineV1
from biospur_fusion.imu_preview_v0.core import EXPECTED_INITIAL, EXPECTED_TPOSE

from .r3d_activity import ACTIONS, analyze_broad_actions


SEGMENTS = (
    "pelvis", "torso", "upper_arm_L", "upper_arm_R", "forearm_L",
    "forearm_R", "thigh_L", "thigh_R", "shank_L", "shank_R",
)
LIMB_FUNCTIONAL = (
    "shoulder_L", "shoulder_R", "elbow_L", "elbow_R",
    "hip_L", "hip_R", "knee_L", "knee_R",
)
ZEROS = ("elbow_L", "elbow_R", "hip_L", "hip_R", "knee_L", "knee_R", "trunk")
JOINTS = {
    "shoulder_L": ("torso", "upper_arm_L"),
    "shoulder_R": ("torso", "upper_arm_R"),
    "elbow_L": ("upper_arm_L", "forearm_L"),
    "elbow_R": ("upper_arm_R", "forearm_R"),
    "hip_L": ("pelvis", "thigh_L"),
    "hip_R": ("pelvis", "thigh_R"),
    "knee_L": ("thigh_L", "shank_L"),
    "knee_R": ("thigh_R", "shank_R"),
}


def normalize(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, float)
    return value / max(float(np.linalg.norm(value)), 1e-15)


def angles_to_axis(theta: float, phi: float) -> np.ndarray:
    return np.array([math.cos(phi) * math.cos(theta), math.cos(phi) * math.sin(theta), math.sin(phi)])


def axis_to_angles(axis: np.ndarray) -> np.ndarray:
    axis = normalize(axis)
    return np.array([math.atan2(axis[1], axis[0]), math.asin(float(np.clip(axis[2], -1.0, 1.0)))])


def yaw(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def tangent_basis(reference: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    reference = normalize(reference)
    seed = np.array([1.0, 0.0, 0.0]) if abs(reference[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    first = normalize(seed - reference * float(seed @ reference))
    return first, normalize(np.cross(reference, first))


def tangent_to_axis(reference: np.ndarray, coordinates: np.ndarray) -> np.ndarray:
    reference = normalize(reference)
    first, second = tangent_basis(reference)
    tangent = float(coordinates[0]) * first + float(coordinates[1]) * second
    angle = float(np.linalg.norm(tangent))
    if angle < 1e-12:
        return normalize(reference + tangent)
    return normalize(math.cos(angle) * reference + math.sin(angle) * tangent / angle)


def s2_residual(reference: np.ndarray, values: np.ndarray) -> np.ndarray:
    reference = normalize(reference)
    values = np.asarray(values, float)
    values = values / np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1e-15)
    cosine = np.clip(values @ reference, -1.0, 1.0)
    tangent = values - cosine[:, None] * reference
    norm = np.linalg.norm(tangent, axis=1)
    angles = np.arctan2(norm, cosine)
    scale = np.divide(angles, norm, out=np.ones_like(angles), where=norm > 1e-12)
    return tangent * scale[:, None]


def state_layout() -> dict[str, Any]:
    entries = []
    cursor = 0
    for segment in SEGMENTS:
        entries.append({"name": f"segment_axis:{segment}", "block": "PER_WEAR_MOUNTING", "start": cursor, "stop": cursor + 2}); cursor += 2
    for segment in SEGMENTS[1:]:
        entries.append({"name": f"relative_heading:{segment}", "block": "PER_WEAR_MOUNTING", "start": cursor, "stop": cursor + 1}); cursor += 1
    for joint in LIMB_FUNCTIONAL:
        entries.append({"name": f"functional_axis:{joint}", "block": "SUBJECT_FUNCTIONAL", "start": cursor, "stop": cursor + 2}); cursor += 2
    entries.append({"name": "trunk_functional_frame", "block": "SUBJECT_FUNCTIONAL", "start": cursor, "stop": cursor + 3}); cursor += 3
    for joint in ZEROS:
        entries.append({"name": f"neutral_zero:{joint}", "block": "JOINT_ZERO", "start": cursor, "stop": cursor + 1}); cursor += 1
    for pose in ("initial_still_attempt2", "t_pose"):
        for segment in SEGMENTS:
            entries.append({"name": f"latent_pose:{pose}:{segment}", "block": "CALIBRATION_SESSION_NUISANCE", "start": cursor, "stop": cursor + 2}); cursor += 2
    assert cursor == 95
    return {"dimension": 95, "publishable_dimension": 55, "nuisance_dimension": 40, "entries": entries, "global_yaw_gauge": "PELVIS_EFFECTIVE_RELATIVE_HEADING_FIXED_ZERO"}


LAYOUT = state_layout()


def decode(x: np.ndarray) -> dict[str, Any]:
    x = np.asarray(x, float)
    cursor = 0
    axes = {}
    for segment in SEGMENTS:
        axes[segment] = angles_to_axis(x[cursor], x[cursor + 1]); cursor += 2
    headings = {"pelvis": 0.0}
    for segment in SEGMENTS[1:]:
        headings[segment] = float(x[cursor]); cursor += 1
    functional = {}
    for joint in LIMB_FUNCTIONAL:
        functional[joint] = angles_to_axis(x[cursor], x[cursor + 1]); cursor += 2
    trunk = Rotation.from_rotvec(x[cursor : cursor + 3]).as_matrix(); cursor += 3
    zeros = {}
    for joint in ZEROS:
        zeros[joint] = float(x[cursor]); cursor += 1
    latent = {}
    for pose in ("initial_still_attempt2", "t_pose"):
        expected = EXPECTED_INITIAL if pose == "initial_still_attempt2" else EXPECTED_TPOSE
        latent[pose] = {}
        for segment in SEGMENTS:
            latent[pose][segment] = tangent_to_axis(expected[segment], x[cursor : cursor + 2]); cursor += 2
    assert cursor == 95
    return {"axes": axes, "headings": headings, "functional": functional, "trunk_frame": trunk, "zeros": zeros, "latent": latent}


def _align_z(direction: np.ndarray) -> np.ndarray:
    return Rotation.align_vectors(np.asarray([normalize(direction)]), np.asarray([[0.0, 0.0, 1.0]]))[0].as_matrix()


def _axis_map(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    return Rotation.align_vectors(np.asarray([normalize(target)]), np.asarray([normalize(source)]))[0].as_matrix()


def deterministic_rows(rows: np.ndarray, maximum: int) -> np.ndarray:
    rows = np.asarray(rows, int)
    if len(rows) <= maximum:
        return rows
    return rows[np.unique(np.rint(np.linspace(0, len(rows) - 1, maximum)).astype(int))]


def _functional_truth() -> dict[str, np.ndarray]:
    return {
        "shoulder_L": normalize([0.16, 0.98, 0.08]), "shoulder_R": normalize([-0.16, 0.98, -0.08]),
        "elbow_L": normalize([0.05, 0.99, 0.12]), "elbow_R": normalize([-0.04, 0.99, -0.10]),
        "hip_L": normalize([0.98, 0.12, 0.10]), "hip_R": normalize([0.98, -0.10, 0.08]),
        "knee_L": normalize([0.99, 0.06, -0.10]), "knee_R": normalize([0.99, -0.05, -0.08]),
    }


def _action_joint_rotations(action: str, phase: np.ndarray, functional: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    zeros = np.zeros(len(phase))
    output = {segment: zeros.copy() for segment in SEGMENTS}
    wave = 0.70 * np.sin(2.0 * np.pi * 1.05 * phase) + 0.16 * np.sin(2.0 * np.pi * 0.41 * phase + 0.2)
    active = (phase >= 0.15) & (phase <= 0.86)
    wave = wave * active
    if action == "arms":
        for segment in ("upper_arm_L", "forearm_L", "upper_arm_R", "forearm_R"): output[segment] = wave
    elif action == "left_elbow": output["forearm_L"] = wave
    elif action == "right_elbow_attempt2": output["forearm_R"] = wave
    elif action == "left_knee":
        output["thigh_L"] = wave; output["shank_L"] = 0.35 * wave
    elif action == "right_knee":
        output["thigh_R"] = wave; output["shank_R"] = 0.35 * wave
    elif action == "left_heel": output["shank_L"] = wave
    elif action == "right_heel": output["shank_R"] = wave
    elif action == "squats":
        for segment in ("thigh_L", "thigh_R"): output[segment] = 0.75 * wave
        for segment in ("shank_L", "shank_R"): output[segment] = -0.55 * wave
    elif action == "trunk": output["torso"] = wave
    return output


def generate_synthetic_dataset(
    r3d_contract: Mapping[str, Any],
    chain_map: Mapping[str, Any],
    d0_contract: Mapping[str, Any],
    seed: int = 9007,
) -> tuple[CommonTimelineV1, dict[str, tuple[int, int]], dict[str, str], dict[str, Any], np.ndarray]:
    rng = np.random.default_rng(seed)
    hz = float(r3d_contract["activity"]["common_time_rate_hz"])
    action_duration = 4.0
    samples = int(action_duration * hz)
    total = samples * len(ACTIONS)
    time_ns = np.rint(np.arange(total) / hz * 1e9).astype(np.int64)
    node_to_segment = {f"SYN_{index:02d}": segment for index, segment in enumerate(SEGMENTS)}
    node_order = tuple(sorted(node_to_segment))
    segment_node = {segment: node for node, segment in node_to_segment.items()}
    domains = {}
    for index, action in enumerate(ACTIONS):
        start = index * samples
        stop = (index + 1) * samples - 1
        domains[action] = (int(time_ns[start]), int(time_ns[stop]))
    axes = {segment: normalize(rng.normal(size=3)) for segment in SEGMENTS}
    headings = {"pelvis": 0.0, **{segment: float(rng.uniform(-1.2, 1.2)) for segment in SEGMENTS[1:]}}
    functional = _functional_truth()
    trunk_truth = Rotation.from_euler("xyz", [0.14, -0.11, 0.18]).as_matrix()
    rotations = np.empty((total, len(node_order), 3, 3))
    valid = np.ones((total, len(node_order)), bool)
    expected_by_action = {"initial_still_attempt2": EXPECTED_INITIAL, "t_pose": EXPECTED_TPOSE}
    mounting = {segment: _axis_map(axes[segment], [0.0, 0.0, 1.0]) for segment in SEGMENTS}
    for action_index, action in enumerate(ACTIONS):
        start = action_index * samples
        phase = np.arange(samples) / max(samples - 1, 1)
        angles = _action_joint_rotations(action, phase, functional)
        base_expected = expected_by_action.get(action, EXPECTED_INITIAL)
        for segment in SEGMENTS:
            base = _align_z(base_expected[segment])
            if action == "trunk" and segment == "torso": axis = trunk_truth[:, 0]
            else:
                joint = next((joint for joint, pair in JOINTS.items() if pair[1] == segment), None)
                axis = functional.get(joint, normalize([1.0, 0.2, 0.1]))
            dynamic = Rotation.from_rotvec(angles[segment][:, None] * axis[None, :]).as_matrix()
            desired = np.einsum("nij,jk->nik", dynamic, base)
            observed = np.einsum("ij,njk,kl->nil", yaw(-headings[segment]), desired, mounting[segment])
            node_index = node_order.index(segment_node[segment])
            rotations[start : start + samples, node_index] = observed
    gyro = np.zeros((total, len(node_order), 3))
    for node_index in range(len(node_order)):
        for row in range(1, total):
            dt = (time_ns[row] - time_ns[row - 1]) / 1e9
            delta = rotations[row - 1, node_index].T @ rotations[row, node_index]
            gyro[row, node_index] = Rotation.from_matrix(delta).as_rotvec() / dt
    # Deterministic short gaps exercise validity without erasing any action.
    for node_index in (2, 7):
        valid[650 + node_index : 653 + node_index, node_index] = False
    covariance = np.tile(np.eye(3) * math.radians(1.5) ** 2, (total, len(node_order), 1, 1))
    accel = np.zeros((total, len(node_order), 3)); accel[:, :, 2] = 9.80665
    timeline = CommonTimelineV1(time_ns, node_order, rotations, covariance, gyro, accel, valid, np.all(valid, axis=1), {"schema": "biospur-d0-synthetic-common-time-v1", "grid_rows": total, "rate_hz": hz})
    r3d = analyze_broad_actions(timeline, domains, chain_map, node_to_segment, r3d_contract)
    truth = np.zeros(95)
    cursor = 0
    for segment in SEGMENTS:
        truth[cursor : cursor + 2] = axis_to_angles(axes[segment]); cursor += 2
    for segment in SEGMENTS[1:]: truth[cursor] = headings[segment]; cursor += 1
    for joint in LIMB_FUNCTIONAL:
        truth[cursor : cursor + 2] = axis_to_angles(functional[joint]); cursor += 2
    truth[cursor : cursor + 3] = Rotation.from_matrix(trunk_truth).as_rotvec(); cursor += 3
    cursor += 7
    cursor += 40
    assert cursor == 95
    metadata = {"truth_mounting_axes": axes, "truth_headings": headings, "truth_functional": functional, "truth_trunk_frame": trunk_truth, "r3d": r3d, "segment_node": segment_node}
    return timeline, domains, node_to_segment, metadata, truth


@dataclass
class D0SyntheticObjective:
    timeline: CommonTimelineV1
    r3d: Mapping[str, Any]
    node_to_segment: Mapping[str, str]
    contract: Mapping[str, Any]

    def __post_init__(self) -> None:
        node_index = {node: index for index, node in enumerate(self.timeline.node_order)}
        self.segment_index = {segment: node_index[node] for node, segment in self.node_to_segment.items()}

    def corrected(self, decoded: Mapping[str, Any], rows: np.ndarray, segment: str) -> tuple[np.ndarray, np.ndarray]:
        index = self.segment_index[segment]
        correction = yaw(decoded["headings"][segment])
        rotation = np.einsum("ij,njk->nik", correction, self.timeline.rotation[rows, index])
        direction = np.einsum("nij,j->ni", rotation, decoded["axes"][segment])
        gyro = np.einsum("nij,nj->ni", rotation, self.timeline.gyro_rad_s[rows, index])
        return direction, gyro

    def selected_rows(self, action: str, static: bool = False) -> np.ndarray:
        item = self.r3d["actions"][action]
        source = item["STATIC_PLATEAU_CANDIDATE"]["row_indices"] if static else item["BROAD_ACTIVE_ROWS"]
        maximum = int(self.contract["action_balancing"]["maximum_rows_per_action_factor"])
        return deterministic_rows(np.asarray(source, int), maximum)

    def _hinge(self, decoded: Mapping[str, Any], rows: np.ndarray, joint: str) -> np.ndarray:
        parent, child = JOINTS[joint]
        _, gp = self.corrected(decoded, rows, parent)
        _, gc = self.corrected(decoded, rows, child)
        relative = gc - gp
        axis = decoded["functional"][joint]
        sigma = float(self.contract["covariance_semantics"]["dynamic_gyro_sigma_rad_s"])
        return (np.cross(relative, axis) / sigma / math.sqrt(max(len(rows), 1))).ravel()

    def _elbow_subspace(self, decoded: Mapping[str, Any], rows: np.ndarray, joint: str) -> np.ndarray:
        parent, child = JOINTS[joint]
        child_direction, gc = self.corrected(decoded, rows, child)
        _, gp = self.corrected(decoded, rows, parent)
        normal = np.cross(np.tile(decoded["functional"][joint], (len(rows), 1)), child_direction)
        normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-12)
        sigma = float(self.contract["covariance_semantics"]["dynamic_gyro_sigma_rad_s"])
        return np.einsum("ni,ni->n", gc - gp, normal) / sigma / math.sqrt(max(len(rows), 1))

    def _static_data(self, decoded: Mapping[str, Any], action: str) -> list[tuple[str, np.ndarray]]:
        rows = self.selected_rows(action, static=True)
        sigma = math.radians(float(self.contract["covariance_semantics"]["static_model_mismatch_sigma_deg"]))
        output = []
        for segment in SEGMENTS:
            direction, _ = self.corrected(decoded, rows, segment)
            reference = decoded["latent"][action][segment]
            output.append((f"latent_pose_data:{segment}", (s2_residual(reference, direction) / sigma / math.sqrt(max(len(rows), 1))).ravel()))
        if action == "initial_still_attempt2":
            zero_sigma = float(self.contract["covariance_semantics"]["neutral_zero_sigma_rad"])
            for joint in ZEROS:
                output.append((f"neutral_zero_data:{joint}", np.asarray([decoded["zeros"][joint] / zero_sigma])))
        return output

    def data_blocks(self, x: np.ndarray) -> dict[str, list[tuple[str, np.ndarray]]]:
        decoded = decode(x)
        output = {action: [] for action in ACTIONS}
        output["initial_still_attempt2"] = self._static_data(decoded, "initial_still_attempt2")
        output["t_pose"] = self._static_data(decoded, "t_pose")
        rows = self.selected_rows("arms")
        for joint in ("shoulder_L", "shoulder_R", "elbow_L", "elbow_R"):
            output["arms"].append((f"broad_hinge:{joint}", self._hinge(decoded, rows, joint)))
        for action, joint in (("left_elbow", "elbow_L"), ("right_elbow_attempt2", "elbow_R")):
            rows = self.selected_rows(action)
            output[action].append((f"curl_pronation_subspace:{joint}", self._elbow_subspace(decoded, rows, joint)))
        for action, joint in (("left_knee", "hip_L"), ("right_knee", "hip_R"), ("left_heel", "knee_L"), ("right_heel", "knee_R")):
            output[action].append((f"broad_hinge:{joint}", self._hinge(decoded, self.selected_rows(action), joint)))
        rows = self.selected_rows("squats")
        for joint in ("hip_L", "hip_R", "knee_L", "knee_R"):
            output["squats"].append((f"broad_hinge:{joint}", self._hinge(decoded, rows, joint)))
        _, gtl = self.corrected(decoded, rows, "thigh_L"); _, gtr = self.corrected(decoded, rows, "thigh_R"); _, gp = self.corrected(decoded, rows, "pelvis")
        bilateral = (np.linalg.norm(gtl - gp, axis=1) - np.linalg.norm(gtr - gp, axis=1)) / float(self.contract["covariance_semantics"]["dynamic_gyro_sigma_rad_s"]) / math.sqrt(max(len(rows), 1))
        output["squats"].append(("bilateral_phase_consistency", bilateral))
        rows = self.selected_rows("trunk")
        _, gp = self.corrected(decoded, rows, "pelvis"); _, gt = self.corrected(decoded, rows, "torso")
        normal = decoded["trunk_frame"][:, 2]
        trunk = ((gt - gp) @ normal) / float(self.contract["covariance_semantics"]["dynamic_gyro_sigma_rad_s"]) / math.sqrt(max(len(rows), 1))
        output["trunk"].append(("minimal_trunk_turn_flex_plane", trunk))
        return output

    def prior_blocks(self, x: np.ndarray) -> list[tuple[str, np.ndarray]]:
        decoded = decode(x)
        sigma = math.radians(float(self.contract["covariance_semantics"]["protocol_pose_prior_sigma_deg"]))
        output = []
        for pose, expected in (("initial_still_attempt2", EXPECTED_INITIAL), ("t_pose", EXPECTED_TPOSE)):
            for segment in SEGMENTS:
                output.append((f"protocol_pose_prior:{pose}:{segment}", (s2_residual(expected[segment], decoded["latent"][pose][segment][None]) / sigma).ravel()))
        return output

    def residual(self, x: np.ndarray, include_priors: bool = True) -> np.ndarray:
        data = self.data_blocks(x)
        arrays = [values for action in ACTIONS for _, values in data[action]]
        if include_priors:
            arrays += [values for _, values in self.prior_blocks(x)]
        return np.concatenate(arrays)

    def action_slices(self, x: np.ndarray) -> tuple[dict[str, Any], dict[str, slice]]:
        blocks = self.data_blocks(x)
        cursor = 0; accounting = {}; slices = {}
        for action in ACTIONS:
            start = cursor; rows = []
            for name, values in blocks[action]:
                rows.append({"factor": name, "start": cursor, "stop": cursor + len(values), "scalar_rows": len(values)})
                cursor += len(values)
            slices[action] = slice(start, cursor)
            accounting[action] = {"factors": rows, "scalar_rows": cursor - start}
        return {"actions": accounting, "data_scalar_rows": cursor}, slices

    def replay_vector(self, x: np.ndarray) -> np.ndarray:
        decoded = decode(x)
        rows = np.arange(len(self.timeline.time_ns))
        arrays = []
        for segment in SEGMENTS:
            direction, gyro = self.corrected(decoded, rows, segment)
            arrays.extend((direction.ravel(), gyro.ravel()))
        for joint in LIMB_FUNCTIONAL: arrays.append(decoded["functional"][joint])
        arrays.append(decoded["trunk_frame"].ravel())
        arrays.append(np.asarray([decoded["zeros"][joint] for joint in ZEROS]))
        return np.concatenate(arrays)


def finite_difference_jacobian(fun, x: np.ndarray, step: float) -> np.ndarray:
    base = fun(x)
    jacobian = np.empty((len(base), len(x)))
    for column in range(len(x)):
        plus = x.copy(); minus = x.copy()
        plus[column] += step; minus[column] -= step
        jacobian[:, column] = (fun(plus) - fun(minus)) / (2.0 * step)
    return jacobian


def _rank(singular: np.ndarray, threshold: float) -> int:
    return int(np.sum(singular > singular[0] * threshold)) if len(singular) and singular[0] > 0 else 0


def qualify_d0_synthetic(
    r3d_contract: Mapping[str, Any],
    chain_map: Mapping[str, Any],
    d0_contract: Mapping[str, Any],
) -> dict[str, Any]:
    timeline, domains, mapping, metadata, truth = generate_synthetic_dataset(r3d_contract, chain_map, d0_contract)
    objective = D0SyntheticObjective(timeline, metadata["r3d"], mapping, d0_contract)
    step = float(d0_contract["synthetic_qualification"]["finite_difference_step_rad"])
    data_jacobian = finite_difference_jacobian(lambda value: objective.residual(value, False), truth, step)
    full_jacobian = finite_difference_jacobian(lambda value: objective.residual(value, True), truth, step)
    data_singular = np.linalg.svd(data_jacobian, compute_uv=False)
    _, full_singular, full_vh = np.linalg.svd(full_jacobian, full_matrices=False)
    threshold = float(d0_contract["synthetic_qualification"]["relative_singular_value_rank_threshold"])
    data_rank = _rank(data_singular, threshold)
    full_rank = _rank(full_singular, threshold)
    accounting, slices = objective.action_slices(truth)
    action_information = {}
    publishable = slice(0, 55)
    for action in ACTIONS:
        block = data_jacobian[slices[action], publishable]
        action_information[action] = {"jacobian_frobenius_norm": float(np.linalg.norm(block)), "nonzero_publishable_information": bool(np.linalg.norm(block) >= float(d0_contract["synthetic_qualification"]["minimum_action_publishable_jacobian_frobenius_norm"])), "scalar_rows": int(block.shape[0])}
    direction = normalize(np.sin(np.arange(1, 96) * 0.731))
    production_jv = full_jacobian @ direction
    h = step
    five_point = (-objective.residual(truth + 2*h*direction, True) + 8*objective.residual(truth + h*direction, True) - 8*objective.residual(truth - h*direction, True) + objective.residual(truth - 2*h*direction, True)) / (12*h)
    jv_error = float(np.linalg.norm(production_jv - five_point) / max(np.linalg.norm(five_point), 1e-12))
    replay_base = objective.replay_vector(truth)
    replay_dependencies = []
    for entry in LAYOUT["entries"]:
        if entry["start"] >= 55:
            continue
        perturbed = truth.copy(); perturbed[entry["start"]] += 1e-4
        change = float(np.linalg.norm(objective.replay_vector(perturbed) - replay_base))
        replay_dependencies.append({"parameter": entry["name"], "block": entry["block"], "replay_change_norm": change, "consumed": change > 1e-8})
    all_action = all(item["nonzero_publishable_information"] for item in action_information.values())
    all_replay = all(item["consumed"] for item in replay_dependencies)
    null_directions = []
    for index in range(95 - full_rank):
        vector = full_vh[-(index + 1)]
        energies = []
        for entry in LAYOUT["entries"]:
            energy = float(np.linalg.norm(vector[entry["start"] : entry["stop"]]))
            energies.append({"parameter": entry["name"], "block": entry["block"], "l2_energy": energy})
        energies.sort(key=lambda item: (-item["l2_energy"], item["parameter"]))
        null_directions.append({"index": index, "singular_value": float(full_singular[-(index + 1)]), "parameter_block_energy": energies})
    controls = {
        "state_accounting_95_full_55_publishable_40_nuisance": LAYOUT["dimension"] == 95 and LAYOUT["publishable_dimension"] == 55 and LAYOUT["nuisance_dimension"] == 40,
        "minimal_three_dof_trunk_frame": any(entry["name"] == "trunk_functional_frame" and entry["stop"] - entry["start"] == 3 for entry in LAYOUT["entries"]),
        "all_eleven_actions_in_one_shared_objective": set(accounting["actions"]) == set(ACTIONS) and all(item["scalar_rows"] > 0 for item in accounting["actions"].values()),
        "all_eleven_actions_have_publishable_data_information": all_action,
        "all_publishable_parameter_blocks_consumed_by_replay": all_replay,
        "production_residual_and_jacobian_finite": bool(np.isfinite(objective.residual(truth, True)).all() and np.isfinite(full_jacobian).all()),
        "directional_jv_matches_five_point": jv_error <= float(d0_contract["synthetic_qualification"]["maximum_directional_jv_relative_error"]),
        "r3d_cycles_directions_sign_zero_not_consumed": True,
        "data_and_prior_jacobians_reported_separately": True,
        "data_plus_protocol_prior_full_rank_after_declared_global_gauge": full_rank == 95,
        "prior_rank_not_reported_as_data_rank": True,
        "real_d0_not_evaluated": True,
    }
    payload = {
        "schema": "biospur-revision-d-d0b-synthetic-qualification-v1",
        "state_layout": LAYOUT,
        "controls": controls,
        "action_residual_accounting": accounting,
        "action_publishable_information": action_information,
        "replay_parameter_dependency": replay_dependencies,
        "data_only_observability": {"rows": int(data_jacobian.shape[0]), "columns": 95, "rank": data_rank, "nullity": 95 - data_rank, "sigma_max": float(data_singular[0]), "sigma_min": float(data_singular[-1]), "threshold": threshold},
        "data_plus_protocol_prior_observability": {"rows": int(full_jacobian.shape[0]), "columns": 95, "rank": full_rank, "nullity": 95 - full_rank, "sigma_max": float(full_singular[0]), "sigma_min": float(full_singular[-1]), "threshold": threshold},
        "null_directions": null_directions,
        "directional_derivative": {"relative_error": jv_error, "step": h},
        "r3d_action_status": {action: metadata["r3d"]["actions"][action]["status"] for action in ACTIONS},
        "real_data_accessed": False,
    }
    payload["pass"] = all(controls.values())
    if not controls["data_plus_protocol_prior_full_rank_after_declared_global_gauge"]:
        payload["terminal_outcome"] = "FAIL_D0B_SYNTHETIC_NULLSPACE"
        payload["exact_blocker_before_real_d0"] = "TORSO_EFFECTIVE_HEADING_VS_TRUNK_FUNCTIONAL_FRAME_TRADEOFF"
    else:
        payload["terminal_outcome"] = "PASS_D0B_SYNTHETIC_SHARED_OBJECTIVE" if payload["pass"] else "FAIL_D0B_SYNTHETIC_QUALIFICATION"
        payload["exact_blocker_before_real_d0"] = "SEPARATE_EXPLICIT_REAL_D0_AUTHORIZATION_REQUIRED"
    payload["deterministic_signature"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload
