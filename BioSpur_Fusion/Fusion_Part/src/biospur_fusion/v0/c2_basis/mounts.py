"""Qualitative wear priors and node-wise sensor-frame transforms."""
from __future__ import annotations

from dataclasses import dataclass
import itertools
import math
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from .contracts import C2_IDENTITY


SIMPLE_MINUS_Z = {
    "BSFEC35": np.array([0.0, 1.0, 0.0]),
    "BSFB165": np.array([0.0, -1.0, 0.0]),
    "BSF31CC": np.array([1.0, 0.0, 0.0]),
    "BSFC2CC": np.array([1.0, 0.0, 0.0]),
    "BSF44AD": np.array([1.0, 0.0, 0.0]),
    "BSF3C79": np.array([1.0, 0.0, 0.0]),
    "BSF6C53": np.array([0.0, 1.0, 0.0]),
    "BSF8BC4": np.array([0.0, -1.0, 0.0]),
}
UPPER_ARM_NODES = {"BSFAA61": 1.0, "BSF1120": -1.0}


def _unit(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, dtype=float)
    norm = float(np.linalg.norm(value))
    if norm <= np.finfo(float).eps:
        raise ValueError("zero direction")
    return value / norm


def body_from_sensor_from_directions(minus_z_body: np.ndarray) -> np.ndarray:
    """Construct a right-handed nominal transform with sensor -Y body-down."""

    minus_z = _unit(minus_z_body)
    sensor_y_body = np.array([0.0, 0.0, 1.0])
    sensor_z_body = -minus_z
    if abs(float(sensor_y_body @ sensor_z_body)) > 1e-9:
        raise ValueError("wear directions must be orthogonal in natural rest")
    sensor_x_body = _unit(np.cross(sensor_y_body, sensor_z_body))
    matrix = np.column_stack((sensor_x_body, sensor_y_body, sensor_z_body))
    if not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-12) or np.linalg.det(matrix) < 0.999:
        raise ValueError("wear transform is not a proper rotation")
    return matrix


def nominal_body_from_sensor(node: str, *, upper_arm_lateral_deg: float = 35.0) -> np.ndarray:
    if node in SIMPLE_MINUS_Z:
        return body_from_sensor_from_directions(SIMPLE_MINUS_Z[node])
    if node in UPPER_ARM_NODES:
        angle = math.radians(float(upper_arm_lateral_deg))
        side = UPPER_ARM_NODES[node]
        minus_z = np.array([-math.cos(angle), side * math.sin(angle), 0.0])
        return body_from_sensor_from_directions(minus_z)
    raise KeyError(node)


def transform_to_body(body_from_sensor: np.ndarray, values_sensor: np.ndarray) -> np.ndarray:
    matrix = np.asarray(body_from_sensor, dtype=float)
    values = np.asarray(values_sensor, dtype=float)
    if matrix.shape != (3, 3):
        raise ValueError("body-from-sensor transform must be 3x3")
    return np.einsum("ij,...j->...i", matrix, values)


def body_direction_in_sensor(body_from_sensor: np.ndarray, direction_body: np.ndarray) -> np.ndarray:
    return np.asarray(body_from_sensor, dtype=float).T @ _unit(direction_body)


@dataclass(frozen=True)
class MountBranch:
    branch_id: str
    body_from_sensor: Mapping[str, np.ndarray]
    prior_variant: Mapping[str, Any]
    metadata_gate: Mapping[str, Any]


def wear_direction_report(
    transforms: Mapping[str, np.ndarray], *, cone_half_angle_deg: float = 55.0,
) -> dict[str, Any]:
    down = np.array([0.0, 0.0, -1.0])
    rows = []
    hard_pass = True
    soft_cost = 0.0
    for node, matrix in transforms.items():
        minus_y = matrix @ np.array([0.0, -1.0, 0.0])
        down_angle = math.degrees(math.acos(float(np.clip(minus_y @ down, -1.0, 1.0))))
        violations: list[str] = []
        costs = [max(0.0, down_angle - cone_half_angle_deg) / 15.0]
        if float(minus_y @ down) < -math.sin(math.radians(10.0)):
            violations.append("SENSOR_MINUS_Y_OPPOSITE_GROUND_HEMISPHERE")
        minus_z = matrix @ np.array([0.0, 0.0, -1.0])
        if node in SIMPLE_MINUS_Z:
            target = SIMPLE_MINUS_Z[node]
            angle = math.degrees(math.acos(float(np.clip(minus_z @ target, -1.0, 1.0))))
            costs.append(max(0.0, angle - cone_half_angle_deg) / 15.0)
            if float(minus_z @ target) < -math.sin(math.radians(10.0)):
                violations.append("SENSOR_MINUS_Z_OPPOSITE_ATTESTED_HEMISPHERE")
            semantic = {"target": target.tolist(), "angle_deg": angle}
        else:
            side = UPPER_ARM_NODES[node]
            posterior = -float(minus_z[0])
            lateral = side * float(minus_z[1])
            semantic = {"posterior_component": posterior, "side_component": lateral}
            if posterior <= 0.0 or lateral <= 0.0 or posterior + 1e-12 < abs(lateral):
                violations.append("UPPER_ARM_MINUS_Z_OUTSIDE_REAR_DOMINANT_SIDE_REGION")
        hard_pass = hard_pass and not violations
        soft_cost += float(np.sum(np.square(costs)))
        rows.append({
            "node": node,
            "segment": C2_IDENTITY[node],
            "minus_y_body": minus_y.tolist(),
            "minus_z_body": minus_z.tolist(),
            "down_angle_deg": down_angle,
            "semantic": semantic,
            "hard_violations": violations,
        })
    return {
        "hard_pass": bool(hard_pass),
        "soft_cost": soft_cost,
        "cone_half_angle_deg": float(cone_half_angle_deg),
        "rows": rows,
        "qualitative_not_exact": True,
    }


def candidate_bank() -> tuple[MountBranch, ...]:
    """Create legal broad upper-arm sectors plus explicit wrong branches."""

    branches: list[MountBranch] = []
    for left_deg, right_deg in itertools.product((20.0, 35.0, 44.0), repeat=2):
        transforms = {
            node: nominal_body_from_sensor(
                node,
                upper_arm_lateral_deg=(left_deg if node == "BSFAA61" else right_deg),
            )
            for node in C2_IDENTITY
        }
        report = wear_direction_report(transforms)
        branches.append(MountBranch(
            branch_id=f"LEGAL_L{left_deg:.0f}_R{right_deg:.0f}",
            body_from_sensor=transforms,
            prior_variant={"left_upper_arm_lateral_deg": left_deg, "right_upper_arm_lateral_deg": right_deg},
            metadata_gate=report,
        ))
    base = {node: nominal_body_from_sensor(node) for node in C2_IDENTITY}
    flipped_front = {node: matrix.copy() for node, matrix in base.items()}
    for node in ("BSF31CC", "BSFC2CC", "BSF44AD", "BSF3C79"):
        flipped_front[node] = Rotation.from_rotvec(np.array([0.0, 0.0, math.pi])).as_matrix() @ flipped_front[node]
    branches.append(MountBranch(
        "NEGATIVE_FRONT_BACK", flipped_front, {"mutation": "front_back"},
        wear_direction_report(flipped_front),
    ))
    inverted = {
        node: Rotation.from_rotvec(np.array([math.pi, 0.0, 0.0])).as_matrix() @ matrix
        for node, matrix in base.items()
    }
    branches.append(MountBranch(
        "NEGATIVE_UP_DOWN", inverted, {"mutation": "up_down"},
        wear_direction_report(inverted),
    ))
    return tuple(branches)


def qualitative_forward_projection_report(transforms: Mapping[str, np.ndarray]) -> dict[str, Any]:
    forward = np.array([1.0, 0.0, 0.0])
    local = {node: body_direction_in_sensor(matrix, forward) for node, matrix in transforms.items()}
    gates = {
        "left_wrist_forward_plus_x": bool(local["BSFEC35"][0] > 0.0),
        "right_wrist_forward_minus_x": bool(local["BSFB165"][0] < 0.0),
        "thigh_forward_shared_minus_z": bool(local["BSF44AD"][2] < 0.0 and local["BSF3C79"][2] < 0.0),
        "left_shank_forward_plus_x": bool(local["BSF6C53"][0] > 0.0),
        "right_shank_forward_minus_x": bool(local["BSF8BC4"][0] < 0.0),
        "upper_arms_forward_shared_dominant_plus_z": bool(
            local["BSFAA61"][2] > abs(local["BSFAA61"][0])
            and local["BSF1120"][2] > abs(local["BSF1120"][0])
        ),
        "upper_arm_secondary_x_mirrored": bool(
            local["BSFAA61"][0] > 0.0 and local["BSF1120"][0] < 0.0
        ),
    }
    return {
        "local_forward_by_node": {node: value.tolist() for node, value in local.items()},
        "gates": gates,
        "pass": all(gates.values()),
        "raw_xyz_bilateral_equality_expected": False,
        "generic_side_sign_flip_used": False,
        "specific_force_phase_caveat": True,
    }
