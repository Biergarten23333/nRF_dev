"""A kinematic tree whose joint coincidence and lengths hold by construction."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
from scipy.spatial.transform import Rotation

SEGMENTS = (
    "Pelvis", "Torso", "UpperArm_L", "Forearm_L", "UpperArm_R", "Forearm_R",
    "Thigh_L", "Shank_L", "Thigh_R", "Shank_R",
)


def _rotation(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, float)
    if value.shape == (3, 3):
        matrix = value
    elif value.shape == (3,):
        matrix = Rotation.from_rotvec(value).as_matrix()
    else:
        raise ValueError("rotation must be rotvec or matrix")
    if not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-9) or np.linalg.det(matrix) < 0.999999:
        raise ValueError("improper segment rotation")
    return matrix


@dataclass(frozen=True)
class Joint:
    name: str
    parent: str
    child: str
    parent_offset_m: np.ndarray
    child_offset_m: np.ndarray
    kind: str


@dataclass(frozen=True)
class BodyState:
    time_s: float
    pelvis_origin_m: np.ndarray
    rotations_N_from_S: Mapping[str, np.ndarray]
    pelvis_velocity_mps: np.ndarray


class ArticulatedBodyModel:
    def __init__(self, joints: tuple[Joint, ...], antenna_lever_m: Mapping[str, np.ndarray]):
        self.joints = joints
        self.antenna_lever_m = {k: np.asarray(v, float) for k, v in antenna_lever_m.items()}
        children = {joint.child for joint in joints}
        if "Pelvis" in children or set(SEGMENTS) - ({"Pelvis"} | children):
            raise ValueError("topology is not a Pelvis-rooted ten-segment tree")

    def forward_kinematics(self, state: BodyState) -> dict[str, np.ndarray]:
        origins = {"Pelvis": np.asarray(state.pelvis_origin_m, float)}
        rotations = {name: _rotation(state.rotations_N_from_S[name]) for name in SEGMENTS}
        pending = list(self.joints)
        while pending:
            progress = False
            for joint in pending[:]:
                if joint.parent not in origins:
                    continue
                centre = origins[joint.parent] + rotations[joint.parent] @ joint.parent_offset_m
                origins[joint.child] = centre - rotations[joint.child] @ joint.child_offset_m
                pending.remove(joint); progress = True
            if not progress:
                raise ValueError("body topology cycle or disconnected segment")
        return origins

    def joint_centres(self, state: BodyState) -> dict[str, np.ndarray]:
        origins = self.forward_kinematics(state)
        rotations = {name: _rotation(state.rotations_N_from_S[name]) for name in SEGMENTS}
        return {joint.name: origins[joint.parent] + rotations[joint.parent] @ joint.parent_offset_m
                for joint in self.joints}

    def antennas(self, state: BodyState) -> dict[str, np.ndarray]:
        origins = self.forward_kinematics(state)
        return {name: origins[name] + _rotation(state.rotations_N_from_S[name]) @ self.antenna_lever_m[name]
                for name in SEGMENTS}

    def constraint_residuals(self, state: BodyState) -> dict[str, float]:
        origins = self.forward_kinematics(state)
        rotations = {name: _rotation(state.rotations_N_from_S[name]) for name in SEGMENTS}
        out = {}
        for joint in self.joints:
            left = origins[joint.parent] + rotations[joint.parent] @ joint.parent_offset_m
            right = origins[joint.child] + rotations[joint.child] @ joint.child_offset_m
            out[joint.name] = float(np.linalg.norm(left - right))
        return out

    def immutable_lengths(self) -> dict[str, float]:
        result = {}
        by_parent = {joint.parent: joint for joint in self.joints}
        for segment in ("UpperArm_L", "UpperArm_R", "Forearm_L", "Forearm_R",
                        "Thigh_L", "Thigh_R", "Shank_L", "Shank_R"):
            proximal = next((j for j in self.joints if j.child == segment), None)
            distal = by_parent.get(segment)
            if proximal and distal:
                result[segment] = float(np.linalg.norm(distal.parent_offset_m - proximal.child_offset_m))
        return result


def default_body_model() -> ArticulatedBodyModel:
    z = np.zeros(3)
    joints = (
        Joint("torso_pelvis", "Pelvis", "Torso", np.array([0, 0, .10]), np.array([0, 0, -.20]), "soft_ball"),
        Joint("shoulder_L", "Torso", "UpperArm_L", np.array([-.19, 0, .22]), z, "conditional_ball"),
        Joint("elbow_L", "UpperArm_L", "Forearm_L", np.array([0, 0, -.30]), z, "soft_hinge"),
        Joint("shoulder_R", "Torso", "UpperArm_R", np.array([.19, 0, .22]), z, "conditional_ball"),
        Joint("elbow_R", "UpperArm_R", "Forearm_R", np.array([0, 0, -.30]), z, "soft_hinge"),
        Joint("hip_L", "Pelvis", "Thigh_L", np.array([-.10, 0, -.05]), z, "ball"),
        Joint("knee_L", "Thigh_L", "Shank_L", np.array([0, 0, -.43]), z, "soft_hinge"),
        Joint("hip_R", "Pelvis", "Thigh_R", np.array([.10, 0, -.05]), z, "ball"),
        Joint("knee_R", "Thigh_R", "Shank_R", np.array([0, 0, -.43]), z, "soft_hinge"),
    )
    levers = {
        "Pelvis": np.array([0, -.03, 0]), "Torso": np.array([0, -.04, .05]),
        "UpperArm_L": np.array([0, .02, -.24]), "Forearm_L": np.array([0, .02, -.25]),
        "UpperArm_R": np.array([0, .02, -.24]), "Forearm_R": np.array([0, .02, -.25]),
        "Thigh_L": np.array([0, .03, -.35]), "Shank_L": np.array([0, .03, -.36]),
        "Thigh_R": np.array([0, .03, -.35]), "Shank_R": np.array([0, .03, -.36]),
    }
    return ArticulatedBodyModel(joints, levers)
