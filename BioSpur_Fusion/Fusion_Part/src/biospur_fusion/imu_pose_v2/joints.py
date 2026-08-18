from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True, slots=True)
class JointSpec:
    name: str
    parent: str
    child: str
    kind: str
    rom_rad: np.ndarray


JOINTS = (
    JointSpec("trunk", "pelvis", "torso", "multi", np.deg2rad([55, 55, 75])),
    JointSpec("hip_left", "pelvis", "thigh_left", "multi", np.deg2rad([130, 60, 70])),
    JointSpec("knee_left", "thigh_left", "shank_left", "hinge", np.deg2rad([155, 25, 25])),
    JointSpec("hip_right", "pelvis", "thigh_right", "multi", np.deg2rad([130, 60, 70])),
    JointSpec("knee_right", "thigh_right", "shank_right", "hinge", np.deg2rad([155, 25, 25])),
    JointSpec("shoulder_left", "torso", "upper_arm_left", "multi", np.deg2rad([180, 130, 180])),
    JointSpec("elbow_left", "upper_arm_left", "forearm_left", "hinge", np.deg2rad([155, 35, 35])),
    JointSpec("shoulder_right", "torso", "upper_arm_right", "multi", np.deg2rad([180, 130, 180])),
    JointSpec("elbow_right", "upper_arm_right", "forearm_right", "hinge", np.deg2rad([155, 35, 35])),
)
