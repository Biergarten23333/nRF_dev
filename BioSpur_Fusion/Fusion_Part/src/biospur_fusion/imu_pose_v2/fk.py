from __future__ import annotations

from typing import Mapping
import numpy as np

from . import so3


LENGTHS = {"pelvis_to_torso_joint": .10, "torso_joint_to_chest": .24,
           "upper_arm": .28, "forearm": .25, "thigh": .42, "shank": .41}


def articulated_fk(q: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Normalized visualization FK; root translation remains L0 convention."""
    point = {"pelvis": np.zeros(3)}
    point["torso_joint"] = point["pelvis"]+so3.rotate(q["pelvis"], np.array([0.0, 0.0, LENGTHS["pelvis_to_torso_joint"]]))
    point["chest"] = point["torso_joint"]+so3.rotate(q["torso"], np.array([0.0, 0.0, LENGTHS["torso_joint_to_chest"]]))
    point["neck"] = point["chest"]+so3.rotate(q["torso"], np.array([0.0, 0.0, .12]))
    for side, sign in (("left", 1.0), ("right", -1.0)):
        point[f"shoulder_{side}"] = point["chest"]+so3.rotate(q["torso"], np.array([sign*.19, 0.0, 0.0]))
        point[f"elbow_{side}"] = point[f"shoulder_{side}"]+so3.rotate(q[f"upper_arm_{side}"], np.array([0.0, 0.0, -LENGTHS["upper_arm"]]))
        point[f"wrist_{side}"] = point[f"elbow_{side}"]+so3.rotate(q[f"forearm_{side}"], np.array([0.0, 0.0, -LENGTHS["forearm"]]))
        point[f"hip_{side}"] = point["pelvis"]+so3.rotate(q["pelvis"], np.array([sign*.10, 0.0, 0.0]))
        point[f"knee_{side}"] = point[f"hip_{side}"]+so3.rotate(q[f"thigh_{side}"], np.array([0.0, 0.0, -LENGTHS["thigh"]]))
        point[f"ankle_{side}"] = point[f"knee_{side}"]+so3.rotate(q[f"shank_{side}"], np.array([0.0, 0.0, -LENGTHS["shank"]]))
    return point


def old_torso_mutation(q: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Qualification-only mutation reproducing the rejected pelvis-only torso."""
    result = articulated_fk(q)
    result["chest"] = result["pelvis"]+so3.rotate(q["pelvis"], np.array([0.0, 0.0, .34]))
    return result
