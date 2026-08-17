from __future__ import annotations

from types import MappingProxyType
from typing import Mapping
import numpy as np

from . import so3


LENGTHS = MappingProxyType({"torso": .34, "upper_arm": .28, "forearm": .25, "thigh": .42, "shank": .41})


def normalized_fk(q: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Fixed normalized skeleton; root is a visualization convention, not a position estimate."""
    p: dict[str, np.ndarray] = {"pelvis": np.zeros(3)}
    p["chest"] = p["pelvis"]+so3.rotate(q["pelvis"], np.array([0, 0, LENGTHS["torso"]]))
    p["neck"] = p["chest"]+so3.rotate(q["torso"], np.array([0, 0, .12]))
    for side, sign in (("left", 1.), ("right", -1.)):
        shoulder = f"shoulder_{side}"; elbow = f"elbow_{side}"; wrist = f"wrist_{side}"
        hip = f"hip_{side}"; knee = f"knee_{side}"; ankle = f"ankle_{side}"
        p[shoulder] = p["chest"]+so3.rotate(q["torso"], np.array([sign*.19, 0, 0]))
        p[elbow] = p[shoulder]+so3.rotate(q[f"upper_arm_{side}"], np.array([0, 0, -LENGTHS["upper_arm"]]))
        p[wrist] = p[elbow]+so3.rotate(q[f"forearm_{side}"], np.array([0, 0, -LENGTHS["forearm"]]))
        p[hip] = p["pelvis"]+so3.rotate(q["pelvis"], np.array([sign*.10, 0, 0]))
        p[knee] = p[hip]+so3.rotate(q[f"thigh_{side}"], np.array([0, 0, -LENGTHS["thigh"]]))
        p[ankle] = p[knee]+so3.rotate(q[f"shank_{side}"], np.array([0, 0, -LENGTHS["shank"]]))
    return p


def bone_lengths(points: Mapping[str, np.ndarray]) -> np.ndarray:
    edges = (("pelvis","chest"),("shoulder_left","elbow_left"),("elbow_left","wrist_left"),
             ("shoulder_right","elbow_right"),("elbow_right","wrist_right"),
             ("hip_left","knee_left"),("knee_left","ankle_left"),
             ("hip_right","knee_right"),("knee_right","ankle_right"))
    return np.array([np.linalg.norm(points[b]-points[a]) for a,b in edges])
