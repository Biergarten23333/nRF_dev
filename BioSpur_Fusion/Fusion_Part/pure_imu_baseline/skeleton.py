"""Fixed-length forward kinematics and topology checks."""
from __future__ import annotations

import numpy as np

from .config import GEOMETRY, NODE_TO_SEGMENT, PARENT_CHILD, SEGMENT_ORDER
from .math3d import relative, rotate

JOINT_NAMES = (
    "pelvis", "torso_top", "shoulder_left", "elbow_left", "wrist_left",
    "shoulder_right", "elbow_right", "wrist_right", "hip_left", "knee_left",
    "ankle_left", "hip_right", "knee_right", "ankle_right",
)


def validate_mapping(mapping: dict[str, str]) -> None:
    if mapping != NODE_TO_SEGMENT:
        missing = sorted(set(NODE_TO_SEGMENT.items())-set(mapping.items()))
        extra = sorted(set(mapping.items())-set(NODE_TO_SEGMENT.items()))
        raise ValueError(f"ten-node mapping mutation missing={missing} extra={extra}")


def _r(q: np.ndarray, vector) -> np.ndarray:
    return rotate(q, np.broadcast_to(np.asarray(vector, float), q.shape[:-1]+(3,)))


def forward_kinematics(q_by_segment: np.ndarray, valid_by_segment: np.ndarray,
                       geometry: dict = GEOMETRY) -> tuple[np.ndarray, np.ndarray]:
    """Return joint positions and a per-joint availability mask."""
    v = {name: valid_by_segment[:, i] for i, name in enumerate(SEGMENT_ORDER)}
    q = {name: np.where(v[name][:, None], q_by_segment[:, i], [1., 0., 0., 0.])
         for i, name in enumerate(SEGMENT_ORDER)}
    n = len(q_by_segment); p = {"pelvis": np.zeros((n, 3))}; ok = {"pelvis": v["pelvis"]}

    p["torso_top"] = p["pelvis"] + _r(q["torso"], [0, 0, geometry["torso_length"]])
    ok["torso_top"] = v["pelvis"] & v["torso"]
    p["shoulder_left"] = p["torso_top"] + _r(q["torso"], [0, geometry["shoulder_width"]/2, 0])
    p["shoulder_right"] = p["torso_top"] + _r(q["torso"], [0, -geometry["shoulder_width"]/2, 0])
    ok["shoulder_left"] = ok["shoulder_right"] = ok["torso_top"]
    p["elbow_left"] = p["shoulder_left"] + _r(q["upper_arm_left"], [0, 0, -geometry["upper_arm_left"]])
    p["elbow_right"] = p["shoulder_right"] + _r(q["upper_arm_right"], [0, 0, -geometry["upper_arm_right"]])
    ok["elbow_left"] = ok["shoulder_left"] & v["upper_arm_left"]
    ok["elbow_right"] = ok["shoulder_right"] & v["upper_arm_right"]
    p["wrist_left"] = p["elbow_left"] + _r(q["forearm_left"], [0, 0, -geometry["forearm_left"]])
    p["wrist_right"] = p["elbow_right"] + _r(q["forearm_right"], [0, 0, -geometry["forearm_right"]])
    ok["wrist_left"] = ok["elbow_left"] & v["forearm_left"]
    ok["wrist_right"] = ok["elbow_right"] & v["forearm_right"]

    p["hip_left"] = p["pelvis"] + _r(q["pelvis"], [0, geometry["hip_width"]/2, 0])
    p["hip_right"] = p["pelvis"] + _r(q["pelvis"], [0, -geometry["hip_width"]/2, 0])
    ok["hip_left"] = ok["hip_right"] = v["pelvis"]
    p["knee_left"] = p["hip_left"] + _r(q["thigh_left"], [0, 0, -geometry["thigh_left"]])
    p["knee_right"] = p["hip_right"] + _r(q["thigh_right"], [0, 0, -geometry["thigh_right"]])
    ok["knee_left"] = ok["hip_left"] & v["thigh_left"]
    ok["knee_right"] = ok["hip_right"] & v["thigh_right"]
    p["ankle_left"] = p["knee_left"] + _r(q["shank_left"], [0, 0, -geometry["shank_left"]])
    p["ankle_right"] = p["knee_right"] + _r(q["shank_right"], [0, 0, -geometry["shank_right"]])
    ok["ankle_left"] = ok["knee_left"] & v["shank_left"]
    ok["ankle_right"] = ok["knee_right"] & v["shank_right"]

    positions = np.stack([p[name] for name in JOINT_NAMES], axis=1)
    availability = np.stack([ok[name] for name in JOINT_NAMES], axis=1)
    positions[~availability] = np.nan
    return positions.astype(np.float32), availability


def relative_quaternions(q_by_segment: np.ndarray, valid_by_segment: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    indexes = {name: i for i, name in enumerate(SEGMENT_ORDER)}
    out = []; validity = []
    for parent, child in PARENT_CHILD:
        qp, qc = q_by_segment[:, indexes[parent]], q_by_segment[:, indexes[child]]
        ok = valid_by_segment[:, indexes[parent]] & valid_by_segment[:, indexes[child]]
        safe_p = np.where(ok[:, None], qp, [1., 0., 0., 0.])
        safe_c = np.where(ok[:, None], qc, [1., 0., 0., 0.])
        qr = relative(safe_p, safe_c); qr[~ok] = np.nan
        out.append(qr); validity.append(ok)
    return np.stack(out, axis=1).astype(np.float32), np.stack(validity, axis=1)


def bone_lengths(positions: np.ndarray) -> dict[str, np.ndarray]:
    j = {name: i for i, name in enumerate(JOINT_NAMES)}
    pairs = {
        "torso_length": ("pelvis", "torso_top"), "shoulder_width": ("shoulder_left", "shoulder_right"),
        "upper_arm_left": ("shoulder_left", "elbow_left"), "forearm_left": ("elbow_left", "wrist_left"),
        "upper_arm_right": ("shoulder_right", "elbow_right"), "forearm_right": ("elbow_right", "wrist_right"),
        "hip_width": ("hip_left", "hip_right"), "thigh_left": ("hip_left", "knee_left"),
        "shank_left": ("knee_left", "ankle_left"), "thigh_right": ("hip_right", "knee_right"),
        "shank_right": ("knee_right", "ankle_right"),
    }
    return {name: np.linalg.norm(positions[:, j[b]]-positions[:, j[a]], axis=1) for name, (a, b) in pairs.items()}


def assert_fixed_lengths(positions: np.ndarray, geometry: dict = GEOMETRY, atol: float = 2e-6) -> dict:
    result = {}
    for name, values in bone_lengths(positions).items():
        finite = values[np.isfinite(values)]
        if not len(finite):
            raise ValueError(f"no valid bone values for {name}")
        expected = geometry[name]
        error = float(np.max(np.abs(finite-expected)))
        if error > atol:
            raise ValueError(f"bone length changed {name}: {error}")
        result[name] = {"expected_m": expected, "maximum_abs_error_m": error,
                        "finite_frames": int(len(finite))}
    return result


def assert_laterality(positions: np.ndarray) -> None:
    j = {name: i for i, name in enumerate(JOINT_NAMES)}
    first = positions[0]
    if not (first[j["hip_left"], 1] > first[j["hip_right"], 1]
            and first[j["shoulder_left"], 1] > first[j["shoulder_right"], 1]):
        raise ValueError("mirrored skeleton or left/right swap")
