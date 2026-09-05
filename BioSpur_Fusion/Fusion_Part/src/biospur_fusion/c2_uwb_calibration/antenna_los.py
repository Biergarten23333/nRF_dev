"""Geometry-only self-shadowing priors for body-worn C2 UWB links.

The C2 operator contract defines sensor ``-Z`` as the antenna-facing outward
side and sensor ``+Z`` as the PCB back against the body.  This module turns
that contract into a *soft ordering* of tag--anchor links.  It deliberately
does not call the result measured LOS truth and never deletes a range by
itself.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from scipy.spatial.transform import Rotation


NODE_OUTWARD_MINUS_Z_IN_SEGMENT = {
    "BSFEC35": np.array([0.0, 1.0, 0.0]),
    "BSFB165": np.array([0.0, -1.0, 0.0]),
    "BSFAA61": np.array([-math.cos(math.radians(35.0)), math.sin(math.radians(35.0)), 0.0]),
    "BSF1120": np.array([-math.cos(math.radians(35.0)), -math.sin(math.radians(35.0)), 0.0]),
    "BSF31CC": np.array([1.0, 0.0, 0.0]),
    "BSFC2CC": np.array([1.0, 0.0, 0.0]),
    "BSF44AD": np.array([1.0, 0.0, 0.0]),
    "BSF3C79": np.array([1.0, 0.0, 0.0]),
    "BSF6C53": np.array([0.0, 1.0, 0.0]),
    "BSF8BC4": np.array([0.0, -1.0, 0.0]),
}


def _unit(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=float).reshape(3)
    norm = float(np.linalg.norm(value))
    if not math.isfinite(norm) or norm <= np.finfo(float).eps:
        raise ValueError("direction must have finite non-zero norm")
    return value / norm


def rotation_from_wxyz(quaternion_wxyz: np.ndarray) -> np.ndarray:
    """Return a proper world-from-segment rotation from frozen WXYZ data."""

    quaternion = np.asarray(quaternion_wxyz, dtype=float).reshape(4)
    if not np.all(np.isfinite(quaternion)):
        raise ValueError("quaternion must be finite")
    return Rotation.from_quat(quaternion[[1, 2, 3, 0]]).as_matrix()


def horizontal_yaw_alignment(
    source_forward: np.ndarray,
    target_forward: np.ndarray,
) -> np.ndarray:
    """Return the proper yaw rotation mapping one horizontal heading to another."""

    source = np.asarray(source_forward, dtype=float).reshape(3).copy()
    target = np.asarray(target_forward, dtype=float).reshape(3).copy()
    source[2] = 0.0
    target[2] = 0.0
    source = _unit(source)
    target = _unit(target)
    source_yaw = math.atan2(float(source[1]), float(source[0]))
    target_yaw = math.atan2(float(target[1]), float(target[0]))
    return Rotation.from_rotvec(np.array([0.0, 0.0, target_yaw - source_yaw])).as_matrix()


def outward_normal_world(
    node: str,
    quaternion_world_segment_wxyz: np.ndarray,
    world_from_frozen_world: np.ndarray,
) -> np.ndarray:
    """Transform the operator-attested sensor ``-Z`` outward direction."""

    if node not in NODE_OUTWARD_MINUS_Z_IN_SEGMENT:
        raise KeyError(node)
    alignment = np.asarray(world_from_frozen_world, dtype=float).reshape(3, 3)
    if not np.allclose(alignment.T @ alignment, np.eye(3), atol=1e-10):
        raise ValueError("world alignment must be orthonormal")
    if float(np.linalg.det(alignment)) < 1.0 - 1e-10:
        raise ValueError("world alignment must be a proper rotation")
    rotation = rotation_from_wxyz(quaternion_world_segment_wxyz)
    return _unit(alignment @ rotation @ NODE_OUTWARD_MINUS_Z_IN_SEGMENT[node])


def outward_facing_score(
    tag_position_world_m: np.ndarray,
    anchor_position_world_m: np.ndarray,
    outward_normal_world_vector: np.ndarray,
) -> float:
    """Cosine score: +1 is directly outward, -1 points through the PCB back."""

    direction = _unit(
        np.asarray(anchor_position_world_m, dtype=float)
        - np.asarray(tag_position_world_m, dtype=float)
    )
    return float(np.clip(direction @ _unit(outward_normal_world_vector), -1.0, 1.0))


def outward_facing_reliability(score: float) -> float:
    """Map the orientation prior to a bounded soft information multiplier.

    The score is geometric evidence, not a LOS classifier.  A link aimed
    through the PCB/body therefore keeps 25% of its nominal information while
    a directly outward link keeps 75%.  Residual-based robust weighting remains
    a separate mechanism and can increase or decrease the effective influence.
    """

    value = float(score)
    if not math.isfinite(value) or value < -1.0 or value > 1.0:
        raise ValueError("facing score must be finite and in [-1, 1]")
    return 0.5 + 0.25 * value


def select_best_geometry(
    valid_anchors: Iterable[int],
    scores: dict[int, float],
    *,
    target_count: int,
    minimum_count: int = 4,
) -> tuple[int, ...]:
    """Select the highest-scoring valid links without pretending they are LOS."""

    valid = tuple(sorted({int(anchor) for anchor in valid_anchors}))
    if target_count < minimum_count:
        raise ValueError("target count is below the geometric minimum")
    if len(valid) < minimum_count:
        return ()
    if set(valid) - set(scores):
        raise ValueError("every valid anchor requires a geometry score")
    count = min(int(target_count), len(valid))
    return tuple(sorted(valid, key=lambda anchor: (-float(scores[anchor]), anchor))[:count])
