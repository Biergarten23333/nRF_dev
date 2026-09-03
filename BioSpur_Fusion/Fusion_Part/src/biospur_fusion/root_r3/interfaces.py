"""C1 frame, identity, physical-point, and frozen-M1 interface closure."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np

from .models import FrameBindingStatus


NODE_TO_SEGMENT = {
    "BSF31CC": "torso",
    "BSFC2CC": "pelvis",
    "BSFAA61": "upper_arm_left",
    "BSFEC35": "forearm_left",
    "BSF1120": "upper_arm_right",
    "BSFB165": "forearm_right",
    "BSF44AD": "thigh_left",
    "BSF6C53": "shank_left",
    "BSF3C79": "thigh_right",
    "BSF8BC4": "shank_right",
}

SEGMENT_POINT_JOINTS = {
    "torso": ("pelvis", "torso_top"),
    "pelvis": ("pelvis", "pelvis"),
    "upper_arm_left": ("shoulder_left", "elbow_left"),
    "forearm_left": ("elbow_left", "wrist_left"),
    "upper_arm_right": ("shoulder_right", "elbow_right"),
    "forearm_right": ("elbow_right", "wrist_right"),
    "thigh_left": ("hip_left", "knee_left"),
    "shank_left": ("knee_left", "ankle_left"),
    "thigh_right": ("hip_right", "knee_right"),
    "shank_right": ("knee_right", "ankle_right"),
}

EXPECTED_C1_NODES = tuple(NODE_TO_SEGMENT)
EXPECTED_ANCHORS = tuple(range(8))


@dataclass(frozen=True)
class PhysicalPointContract:
    uwb_point: str = "U_i_UWB_ANTENNA_PHASE_CENTER"
    imu_point: str = "I_i_IMU_SENSOR_ORIGIN"
    device_point: str = "D_i_DEVICE_ENCLOSURE_REFERENCE"
    anatomical_point: str = "A_i_ANATOMICAL_ATTACHMENT"
    m1_point: str = "S_i_M1_SEGMENT_MIDPOINT_OR_PELVIS_ORIGIN"
    lever_arm_mean_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    status: str = "ZERO_WITH_UNCERTAINTY_NOT_FITTED"


def load_frame_binding(frame_audit: Path) -> FrameBindingStatus:
    value = json.loads(frame_audit.read_text(encoding="utf-8"))
    rotation = value.get("R_N_from_V4")
    qualified = bool(value.get("qualified", value.get("pass", False))) and rotation is not None
    status = FrameBindingStatus(
        qualified=qualified,
        rotation_navigation_from_v4=None if not qualified else np.asarray(rotation, float),
        reason=str(value.get("reason", value.get("verdict", "UNSPECIFIED"))),
        provenance=f"{frame_audit.resolve()}",
    )
    status.validate()
    return status


def validate_m1_identity(m1: dict[str, np.ndarray]) -> None:
    nodes = tuple(str(value) for value in m1["node_ids"])
    segments = tuple(str(value) for value in m1["segment_names"])
    if set(nodes) != set(EXPECTED_C1_NODES) or len(nodes) != 10:
        raise ValueError(f"C1 node identity mismatch: {nodes}")
    for node, segment in zip(nodes, segments):
        if NODE_TO_SEGMENT[node] != segment:
            raise ValueError(f"node/segment mismatch {node}: {segment}")


def strict_left_indices(times_s: np.ndarray, query_s: np.ndarray) -> np.ndarray:
    indices = np.searchsorted(np.asarray(times_s, float), np.asarray(query_s, float), side="right") - 1
    return indices.astype(np.int64)


def m1_segment_points_at(
    m1: dict[str, np.ndarray],
    node_indices: np.ndarray,
    query_s: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return strict-past M1 segment points without interpolation or future use."""

    validate_m1_identity(m1)
    query = np.asarray(query_s, float)
    source = strict_left_indices(m1["time_s"], query)
    points = np.full((len(query), 3), np.nan)
    valid = np.zeros(len(query), dtype=bool)
    future = np.zeros(len(query), dtype=bool)
    joint_names = [str(value) for value in m1["joint_names"]]
    joint_index = {name: index for index, name in enumerate(joint_names)}
    nodes = [str(value) for value in m1["node_ids"]]
    positions = np.asarray(m1["joint_positions_m"], float)
    available = np.asarray(m1["joint_available"], bool)
    m1_times = np.asarray(m1["time_s"], float)
    for row, (node_index, m1_index) in enumerate(zip(np.asarray(node_indices, int), source)):
        if m1_index < 0 or m1_index >= len(m1_times):
            continue
        node = nodes[int(node_index)]
        first_name, second_name = SEGMENT_POINT_JOINTS[NODE_TO_SEGMENT[node]]
        first = joint_index[first_name]; second = joint_index[second_name]
        if not (available[m1_index, first] and available[m1_index, second]):
            continue
        points[row] = 0.5 * (positions[m1_index, first] + positions[m1_index, second])
        valid[row] = True
        future[row] = bool(m1_times[m1_index] > query[row] + 1e-12)
    return points, valid, future, source


def yaw_rotation(degrees: float) -> np.ndarray:
    angle = np.deg2rad(float(degrees)); c = float(np.cos(angle)); s = float(np.sin(angle))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
