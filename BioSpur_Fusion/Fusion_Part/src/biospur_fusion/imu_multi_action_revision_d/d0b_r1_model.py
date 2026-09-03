"""D0B-R1 observation-backed shared calibration model.

The estimator consumes only Q2/common-time observations and R3D broad/static
row masks.  Synthetic truth is deliberately absent from ``R1Observation``.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from .d0b_r1_generator import ACTIONS, SEGMENTS


JOINTS = {
    "shoulder_L": ("torso", "upper_arm_L"), "shoulder_R": ("torso", "upper_arm_R"),
    "elbow_L": ("upper_arm_L", "forearm_L"), "elbow_R": ("upper_arm_R", "forearm_R"),
    "hip_L": ("pelvis", "thigh_L"), "hip_R": ("pelvis", "thigh_R"),
    "knee_L": ("thigh_L", "shank_L"), "knee_R": ("thigh_R", "shank_R"),
}
FUNCTIONAL_JOINTS = tuple(JOINTS)
STATIC_ACTIONS = ("initial_still_attempt2", "t_pose")


def unit(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, float)
    return value / max(float(np.linalg.norm(value)), 1e-12)


def axis_from_angles(theta: float, phi: float) -> np.ndarray:
    return np.array([math.cos(phi) * math.cos(theta), math.cos(phi) * math.sin(theta), math.sin(phi)])


def angles_from_axis(axis: np.ndarray) -> np.ndarray:
    axis = unit(axis)
    return np.array([math.atan2(axis[1], axis[0]), math.asin(float(np.clip(axis[2], -1.0, 1.0)))])


def yaw(angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def tangent_basis(reference: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    reference = unit(reference)
    seed = np.array([1.0, 0.0, 0.0]) if abs(reference[0]) < 0.85 else np.array([0.0, 1.0, 0.0])
    first = unit(seed - reference * float(seed @ reference))
    return first, unit(np.cross(reference, first))


def tangent_axis(reference: np.ndarray, coordinate: np.ndarray) -> np.ndarray:
    first, second = tangent_basis(reference)
    tangent = float(coordinate[0]) * first + float(coordinate[1]) * second
    angle = float(np.linalg.norm(tangent))
    if angle < 1e-12:
        return unit(reference + tangent)
    return unit(math.cos(angle) * unit(reference) + math.sin(angle) * tangent / angle)


def s2_residual(predicted: np.ndarray, observed: np.ndarray) -> np.ndarray:
    predicted = np.asarray(predicted, float)
    observed = np.asarray(observed, float)
    predicted = predicted / np.maximum(np.linalg.norm(predicted, axis=-1, keepdims=True), 1e-12)
    observed = observed / np.maximum(np.linalg.norm(observed, axis=-1, keepdims=True), 1e-12)
    cosine = np.clip(np.sum(predicted * observed, axis=-1), -1.0, 1.0)
    tangent = observed - cosine[..., None] * predicted
    norm = np.linalg.norm(tangent, axis=-1)
    angle = np.arctan2(norm, cosine)
    return tangent * np.divide(angle, norm, out=np.ones_like(angle), where=norm > 1e-12)[..., None]


def canonical_pose_bases(action: str) -> dict[str, np.ndarray]:
    output = {segment: np.array([0.0, 0.0, -1.0]) for segment in SEGMENTS}
    output["pelvis"] = np.array([0.0, 0.0, 1.0])
    output["torso"] = np.array([0.0, 0.0, 1.0])
    if action == "t_pose":
        output["upper_arm_L"] = np.array([1.0, 0.0, 0.0])
        output["upper_arm_R"] = np.array([-1.0, 0.0, 0.0])
        output["forearm_L"] = np.array([1.0, 0.0, 0.0])
        output["forearm_R"] = np.array([-1.0, 0.0, 0.0])
    return output


def articulated_pose_directions(action: str, q: np.ndarray) -> dict[str, np.ndarray]:
    """Generate all ten directions from one 17-coordinate tree state."""
    q = np.asarray(q, float)
    if q.shape != (17,):
        raise ValueError("static articulated pose must have 17 coordinates")
    root = Rotation.from_rotvec(q[:3]).as_matrix()
    bases = canonical_pose_bases(action)
    cursor = 3
    torso_local = tangent_axis(bases["torso"], q[cursor:cursor + 2]); cursor += 2
    shoulder_local = {}
    for side in ("L", "R"):
        shoulder_local[side] = tangent_axis(bases[f"upper_arm_{side}"], q[cursor:cursor + 2]); cursor += 2
    elbow = {"L": float(q[cursor]), "R": float(q[cursor + 1])}; cursor += 2
    hip_local = {}
    for side in ("L", "R"):
        hip_local[side] = tangent_axis(bases[f"thigh_{side}"], q[cursor:cursor + 2]); cursor += 2
    knee = {"L": float(q[cursor]), "R": float(q[cursor + 1])}; cursor += 2
    assert cursor == 17
    output = {"pelvis": root @ bases["pelvis"], "torso": root @ torso_local}
    for side, sign in (("L", 1.0), ("R", -1.0)):
        upper = root @ shoulder_local[side]
        bend_axis = root @ np.array([sign, 0.0, 0.0])
        forearm = Rotation.from_rotvec(bend_axis * elbow[side]).apply(upper)
        thigh = root @ hip_local[side]
        shank = Rotation.from_rotvec(bend_axis * knee[side]).apply(thigh)
        output[f"upper_arm_{side}"] = unit(upper)
        output[f"forearm_{side}"] = unit(forearm)
        output[f"thigh_{side}"] = unit(thigh)
        output[f"shank_{side}"] = unit(shank)
    return {name: unit(value) for name, value in output.items()}


def product_layout() -> list[dict[str, Any]]:
    entries = []
    cursor = 0
    for segment in SEGMENTS:
        entries.append({"name": f"sensor_axis:{segment}", "start": cursor, "stop": cursor + 2, "block": "SENSOR_LONGITUDINAL_AXIS"}); cursor += 2
    for segment in SEGMENTS[1:]:
        entries.append({"name": f"effective_heading:{segment}", "start": cursor, "stop": cursor + 1, "block": "EFFECTIVE_RELATIVE_HEADING"}); cursor += 1
    for joint in FUNCTIONAL_JOINTS:
        entries.append({"name": f"functional_axis:{joint}", "start": cursor, "stop": cursor + 2, "block": "LIMB_FUNCTIONAL_AXIS"}); cursor += 2
    entries.append({"name": "trunk_motion_plane_normal", "start": cursor, "stop": cursor + 2, "block": "TRUNK_MOTION_PLANE_NORMAL"}); cursor += 2
    assert cursor == 47
    return entries


PRODUCT_LAYOUT = product_layout()
PRODUCT_DIMENSION = 47
NUISANCE_DIMENSION = 34
FULL_DIMENSION = 81


def decode_product(x: np.ndarray) -> dict[str, Any]:
    x = np.asarray(x, float)
    axes = {}; headings = {"pelvis": 0.0}; functional = {}; cursor = 0
    for segment in SEGMENTS:
        axes[segment] = axis_from_angles(x[cursor], x[cursor + 1]); cursor += 2
    for segment in SEGMENTS[1:]:
        headings[segment] = float(x[cursor]); cursor += 1
    for joint in FUNCTIONAL_JOINTS:
        functional[joint] = axis_from_angles(x[cursor], x[cursor + 1]); cursor += 2
    trunk_normal = axis_from_angles(x[cursor], x[cursor + 1]); cursor += 2
    assert cursor == PRODUCT_DIMENSION
    return {"axes": axes, "headings": headings, "functional": functional, "trunk_normal": trunk_normal}


def decode_full(x: np.ndarray) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    x = np.asarray(x, float)
    return decode_product(x[:PRODUCT_DIMENSION]), {
        "initial_still_attempt2": x[PRODUCT_DIMENSION:PRODUCT_DIMENSION + 17],
        "t_pose": x[PRODUCT_DIMENSION + 17:PRODUCT_DIMENSION + 34],
    }


@dataclass
class R1Observation:
    time_ns: np.ndarray
    node_order: tuple[str, ...]
    rotation: np.ndarray
    gyro_rad_s: np.ndarray
    valid: np.ndarray
    windows: dict[str, tuple[int, int]]
    node_to_segment: dict[str, str]
    r3d_actions: dict[str, Any]
    source: str = "PRODUCTION_Q2_COMMON_TIME_WITH_R3D_ROWS"
    accel_mps2: np.ndarray | None = None

    def __post_init__(self) -> None:
        forbidden = ("truth", "mounting_R_BS", "world_segment_rotation", "graphical_nodes")
        if any(hasattr(self, name) for name in forbidden):
            raise ValueError("truth leaked into estimator observation")
        if self.accel_mps2 is not None and self.accel_mps2.shape != self.gyro_rad_s.shape:
            raise ValueError("accelerometer array must match gyro shape")

    def signature(self) -> str:
        h = hashlib.sha256()
        arrays = [self.time_ns, self.rotation, self.gyro_rad_s, self.valid.astype(np.uint8)]
        if self.accel_mps2 is not None:
            arrays.append(self.accel_mps2)
        for array in arrays:
            value = np.ascontiguousarray(array)
            h.update(value.dtype.str.encode() + b"\0")
            h.update(np.asarray(value.shape, dtype="<i8").tobytes())
            h.update(value.tobytes())
        h.update(json.dumps(self.windows, sort_keys=True).encode())
        h.update(json.dumps(self.node_to_segment, sort_keys=True).encode())
        return h.hexdigest()


@dataclass
class ResidualBlock:
    action: str
    factor: str
    classification: str
    values: np.ndarray
    rows: np.ndarray
    node_pair: tuple[str, ...]
    measurement_unit: str
    prediction_equation: str
    whitening_source: str
    parameter_blocks: tuple[str, ...]


class R1Objective:
    def __init__(self, observation: R1Observation, contract: Mapping[str, Any]):
        self.obs = observation
        self.contract = contract
        node_index = {node: index for index, node in enumerate(observation.node_order)}
        self.segment_node = {segment: node for node, segment in observation.node_to_segment.items()}
        self.segment_index = {segment: node_index[node] for segment, node in self.segment_node.items()}

    def _rows(self, action: str, segments: tuple[str, ...], static: bool = False) -> np.ndarray:
        item = self.obs.r3d_actions[action]
        if static:
            source = np.asarray(item["STATIC_PLATEAU_CANDIDATE"]["row_indices"], int)
            maximum = int(self.contract["row_selection"]["maximum_static_rows_per_segment"])
        else:
            source = np.asarray(item["BROAD_ACTIVE_ROWS"], int)
            maximum = int(self.contract["row_selection"]["maximum_dynamic_rows_per_factor"])
        keep = np.ones(len(source), bool)
        for segment in segments:
            keep &= self.obs.valid[source, self.segment_index[segment]]
        source = source[keep]
        if not len(source):
            return source
        if len(source) > maximum:
            source = source[np.unique(np.rint(np.linspace(0, len(source) - 1, maximum)).astype(int))]
        return source

    def corrected_direction(self, product: Mapping[str, Any], segment: str, rows: np.ndarray) -> np.ndarray:
        index = self.segment_index[segment]
        rotation = np.einsum("ij,njk->nik", yaw(product["headings"][segment]), self.obs.rotation[rows, index])
        return np.einsum("nij,j->ni", rotation, product["axes"][segment])

    def corrected_omega(self, product: Mapping[str, Any], segment: str, rows: np.ndarray) -> np.ndarray:
        index = self.segment_index[segment]
        rotation = np.einsum("ij,njk->nik", yaw(product["headings"][segment]), self.obs.rotation[rows, index])
        return np.einsum("nij,nj->ni", rotation, self.obs.gyro_rad_s[rows, index])

    def static_blocks(self, product: Mapping[str, Any], nuisance: Mapping[str, np.ndarray]) -> list[ResidualBlock]:
        sigma = math.radians(float(self.contract["measurement_covariance"]["static_direction_sigma_deg"]))
        output = []
        for action in STATIC_ACTIONS:
            predicted = articulated_pose_directions(action, nuisance[action])
            for segment in SEGMENTS:
                rows = self._rows(action, (segment,), static=True)
                if not len(rows):
                    continue
                observed = self.corrected_direction(product, segment, rows)
                values = (s2_residual(np.tile(predicted[segment], (len(rows), 1)), observed) / sigma / math.sqrt(len(rows))).ravel()
                output.append(ResidualBlock(
                    action, f"articulated_static_direction:{segment}", "PROTOCOL_CONDITIONED_MEASUREMENT",
                    values, rows, (self.segment_node[segment],), "unit_direction/rad",
                    "LogS2(d_articulated(q_pose), Rz(h_i) R_Ni_Bi a_Bi)",
                    f"measured plateau covariance plus {sigma} rad human model mismatch",
                    ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING", f"ARTICULATED_POSE_{action}"),
                ))
        return output

    def _relative_omega(self, product: Mapping[str, Any], parent: str, child: str, rows: np.ndarray) -> np.ndarray:
        return self.corrected_omega(product, child, rows) - self.corrected_omega(product, parent, rows)

    def hinge_block(self, action: str, joint: str) -> ResidualBlock | None:
        parent, child = JOINTS[joint]
        rows = self._rows(action, (parent, child))
        if not len(rows): return None
        return ResidualBlock(action, f"soft_functional_axis:{joint}", "MEASURED_OBSERVATION", np.empty(0), rows,
            (self.segment_node[parent], self.segment_node[child]), "rad/s",
            "cross(omega_child-omega_parent, functional_axis)/sigma",
            "dynamic gyro covariance plus functional-axis variation", ("EFFECTIVE_RELATIVE_HEADING", "LIMB_FUNCTIONAL_AXIS"))

    def dynamic_blocks(self, product: Mapping[str, Any]) -> list[ResidualBlock]:
        sigma = float(self.contract["measurement_covariance"]["dynamic_hinge_sigma_rad_s"])
        elbow_sigma = float(self.contract["measurement_covariance"]["elbow_curl_pronation_subspace_sigma_rad_s"])
        output: list[ResidualBlock] = []
        schedule = {
            "arms": ("shoulder_L", "shoulder_R", "elbow_L", "elbow_R"),
            "left_knee": ("hip_L",), "right_knee": ("hip_R",),
            "left_heel": ("knee_L",), "right_heel": ("knee_R",),
            "squats": ("hip_L", "hip_R", "knee_L", "knee_R"),
        }
        for action, joints in schedule.items():
            for joint in joints:
                block = self.hinge_block(action, joint)
                if block is None: continue
                parent, child = JOINTS[joint]
                rel = self._relative_omega(product, parent, child, block.rows)
                block.values = (np.cross(rel, product["functional"][joint]) / sigma / math.sqrt(len(block.rows))).ravel()
                output.append(block)
        for action, joint in (("left_elbow", "elbow_L"), ("right_elbow_attempt2", "elbow_R")):
            parent, child = JOINTS[joint]
            rows = self._rows(action, (parent, child))
            if not len(rows): continue
            midpoint = self.obs.windows[action][0] + (self.obs.windows[action][1] - self.obs.windows[action][0]) // 2
            phase_rows = (("curl", rows[self.obs.time_ns[rows] <= midpoint]), ("pronation_supination", rows[self.obs.time_ns[rows] > midpoint]))
            for phase, selected in phase_rows:
                if not len(selected): continue
                rel = self._relative_omega(product, parent, child, selected)
                axis = (np.tile(product["functional"][joint], (len(selected), 1)) if phase == "curl" else self.corrected_direction(product, child, selected))
                values = (np.cross(rel, axis) / elbow_sigma / math.sqrt(len(selected))).ravel()
                output.append(ResidualBlock(action, f"{phase}_functional_axis:{joint}", "MEASURED_OBSERVATION", values, selected,
                    (self.segment_node[parent], self.segment_node[child]), "rad/s",
                    "cross(relative_gyro, curl_functional_axis)/sigma" if phase == "curl" else "cross(relative_gyro, instantaneous_forearm_long_axis)/sigma",
                    "phase-conditioned dynamic gyro covariance plus off-axis human motion", ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING", "LIMB_FUNCTIONAL_AXIS")))
        rows = self._rows("trunk", ("pelvis", "torso"))
        if len(rows):
            sigma_t = float(self.contract["measurement_covariance"]["trunk_motion_plane_sigma_rad_s"])
            start, stop = self.obs.windows["trunk"]
            cuts = (start + (stop - start) // 3, start + 2 * (stop - start) // 3)
            phases = (
                ("left_turn", rows[self.obs.time_ns[rows] <= cuts[0]]),
                ("right_turn", rows[(self.obs.time_ns[rows] > cuts[0]) & (self.obs.time_ns[rows] <= cuts[1])]),
                ("forward_flexion_recovery", rows[self.obs.time_ns[rows] > cuts[1]]),
            )
            for phase, selected in phases:
                if not len(selected): continue
                rel = self._relative_omega(product, "pelvis", "torso", selected)
                values = (rel @ product["trunk_normal"]) / sigma_t / math.sqrt(len(selected))
                output.append(ResidualBlock("trunk", f"{phase}_motion_plane", "MEASURED_OBSERVATION", values, selected,
                    (self.segment_node["pelvis"], self.segment_node["torso"]), "rad/s",
                    "dot(omega_torso-omega_pelvis, minimal_trunk_motion_plane_normal)/sigma",
                    "phase-conditioned interior-time relative gyro covariance with pelvis compensation", ("EFFECTIVE_RELATIVE_HEADING", "TRUNK_MOTION_PLANE_NORMAL")))
        return output

    def prior_blocks(self, nuisance: Mapping[str, np.ndarray]) -> list[ResidualBlock]:
        output = []
        for action in STATIC_ACTIONS:
            q = nuisance[action]
            sigma = math.radians(float(self.contract["measurement_covariance"]["tpose_protocol_sigma_deg"] if action == "t_pose" else self.contract["measurement_covariance"]["natural_pose_sigma_deg"]))
            # Root yaw remains unconstrained; all other pose coordinates receive broad human protocol support.
            selected = np.r_[q[:2], q[3:]] / sigma
            output.append(ResidualBlock(action, "broad_human_pose_protocol", "PARAMETER_ONLY_PRIOR", selected, np.empty(0, int), tuple(), "rad",
                "broad zero-mean deviation in the pose-specific articulated coordinate chart",
                f"protocol semantic covariance {sigma} rad; not counted as measurement", (f"ARTICULATED_POSE_{action}",)))
        return output

    def blocks(self, x: np.ndarray, include_nonmeasurement: bool = True) -> list[ResidualBlock]:
        product, nuisance = decode_full(x)
        output = self.static_blocks(product, nuisance) + self.dynamic_blocks(product)
        if include_nonmeasurement:
            output += self.prior_blocks(nuisance)
        return output

    def residual(self, x: np.ndarray, include_nonmeasurement: bool = True) -> np.ndarray:
        blocks = self.blocks(x, include_nonmeasurement)
        if not blocks or any(not len(block.values) for block in blocks):
            raise ValueError("missing/empty observation-backed residual block")
        return np.concatenate([np.asarray(block.values, float) for block in blocks])

    def observation_lineage(self, x: np.ndarray) -> list[dict[str, Any]]:
        records = []
        for block in self.blocks(x, True):
            records.append({
                "action": block.action, "factor": block.factor, "classification": block.classification,
                "observation_array": "common_time.rotation+gyro_rad_s" if len(block.rows) else None,
                "node_pair": list(block.node_pair), "timestamps_ns": self.obs.time_ns[block.rows].tolist(),
                "row_indices": block.rows.tolist(), "validity_mask": [True] * len(block.rows),
                "measurement_unit": block.measurement_unit, "prediction_equation": block.prediction_equation,
                "whitening_covariance_source": block.whitening_source, "parameter_blocks": list(block.parameter_blocks),
                "residual_scalar_rows": len(block.values),
            })
        return records


def blind_initialization(observation: R1Observation, contract: Mapping[str, Any]) -> np.ndarray:
    objective = R1Objective(observation, contract)
    x = np.zeros(FULL_DIMENSION)
    # Observation-derived axis initialization from the two static protocols.
    for segment_index, segment in enumerate(SEGMENTS):
        candidates = []
        for action in STATIC_ACTIONS:
            rows = objective._rows(action, (segment,), static=True)
            if not len(rows): continue
            expected = canonical_pose_bases(action)[segment]
            index = objective.segment_index[segment]
            candidates.extend(np.einsum("nji,j->ni", observation.rotation[rows, index], expected))
        axis = unit(np.median(np.asarray(candidates), axis=0)) if candidates else np.array([0.0, 0.0, 1.0])
        x[2 * segment_index:2 * segment_index + 2] = angles_from_axis(axis)
    cursor = 29
    # PCA is initializer only. Every functional coordinate remains in the joint objective.
    action_for_joint = {
        "shoulder_L": "arms", "shoulder_R": "arms", "elbow_L": "left_elbow", "elbow_R": "right_elbow_attempt2",
        "hip_L": "left_knee", "hip_R": "right_knee", "knee_L": "left_heel", "knee_R": "right_heel",
    }
    product = decode_product(x[:PRODUCT_DIMENSION])
    for joint in FUNCTIONAL_JOINTS:
        parent, child = JOINTS[joint]; action = action_for_joint[joint]
        rows = objective._rows(action, (parent, child))
        rel = objective._relative_omega(product, parent, child, rows)
        _, _, vh = np.linalg.svd(rel, full_matrices=False)
        x[cursor:cursor + 2] = angles_from_axis(vh[0]); cursor += 2
    rows = objective._rows("trunk", ("pelvis", "torso"))
    rel = objective._relative_omega(product, "pelvis", "torso", rows)
    _, _, vh = np.linalg.svd(rel, full_matrices=False)
    x[cursor:cursor + 2] = angles_from_axis(vh[-1]); cursor += 2
    assert cursor == PRODUCT_DIMENSION
    return x


def bounds() -> tuple[np.ndarray, np.ndarray]:
    lower = np.full(FULL_DIMENSION, -math.pi)
    upper = np.full(FULL_DIMENSION, math.pi)
    # Every S2 latitude is bounded away from chart singularity.
    s2_latitudes = [2 * i + 1 for i in range(10)] + [29 + 2 * i + 1 for i in range(8)] + [45 + 1]
    for index in s2_latitudes:
        lower[index], upper[index] = -1.45, 1.45
    lower[PRODUCT_DIMENSION:] = -1.6
    upper[PRODUCT_DIMENSION:] = 1.6
    return lower, upper


def deterministic_starts(x0: np.ndarray, count: int, seed: int) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    low, high = bounds()
    output = [np.clip(x0, low + 1e-6, high - 1e-6)]
    for _ in range(1, count):
        perturb = rng.normal(0.0, 0.12, len(x0))
        output.append(np.clip(x0 + perturb, low + 1e-6, high - 1e-6))
    return output


def production_jacobian(objective: R1Objective, x: np.ndarray, include_nonmeasurement: bool = True) -> np.ndarray:
    """Frozen absolute-step three-point Jacobian used by fit and audit."""
    x = np.asarray(x, float)
    step = float(objective.contract["solver"]["finite_difference_step"])
    low, high = bounds()
    base = objective.residual(x, include_nonmeasurement)
    jacobian = np.empty((len(base), len(x)))
    for column in range(len(x)):
        if x[column] - step >= low[column] and x[column] + step <= high[column]:
            minus = x.copy(); minus[column] -= step
            plus = x.copy(); plus[column] += step
            jacobian[:, column] = (
                objective.residual(plus, include_nonmeasurement)
                - objective.residual(minus, include_nonmeasurement)
            ) / (2.0 * step)
        elif x[column] + 2.0 * step <= high[column]:
            one = x.copy(); one[column] += step
            two = x.copy(); two[column] += 2.0 * step
            jacobian[:, column] = (
                -3.0 * base
                + 4.0 * objective.residual(one, include_nonmeasurement)
                - objective.residual(two, include_nonmeasurement)
            ) / (2.0 * step)
        elif x[column] - 2.0 * step >= low[column]:
            one = x.copy(); one[column] -= step
            two = x.copy(); two[column] -= 2.0 * step
            jacobian[:, column] = (
                3.0 * base
                - 4.0 * objective.residual(one, include_nonmeasurement)
                + objective.residual(two, include_nonmeasurement)
            ) / (2.0 * step)
        else:
            raise ValueError(f"no finite-difference support for coordinate {column}")
    if not np.isfinite(jacobian).all():
        raise ValueError("production Jacobian contains non-finite values")
    return jacobian


def fit_multistart(objective: R1Objective, x0: np.ndarray, contract: Mapping[str, Any], seed: int) -> list[dict[str, Any]]:
    cfg = contract["solver"]; low, high = bounds(); results = []
    for index, start in enumerate(deterministic_starts(x0, int(cfg["starts"]), seed)):
        result = least_squares(
            lambda value: objective.residual(value, True), start, bounds=(low, high),
            jac=lambda value: production_jacobian(objective, value, True),
            loss=cfg["loss"], f_scale=float(cfg["f_scale"]), max_nfev=int(cfg["maximum_function_evaluations"]),
            xtol=float(cfg["xtol"]), ftol=float(cfg["ftol"]), gtol=float(cfg["gtol"]), verbose=0,
        )
        results.append({
            "start": index, "x": result.x, "cost": float(result.cost), "optimality": float(result.optimality),
            "nfev": int(result.nfev), "njev": int(result.njev or 0), "status": int(result.status),
            "message": str(result.message), "success": bool(result.success), "finite": bool(np.isfinite(result.x).all() and np.isfinite(result.fun).all()),
        })
    return results
