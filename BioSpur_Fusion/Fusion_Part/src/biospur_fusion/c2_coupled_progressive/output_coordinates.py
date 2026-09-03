"""Capture-wide Cartesian output-coordinate ownership.

QMT/VQF orientations remain proper SO(3) rotations.  A handedness correction
is an improper transform and therefore belongs after fixed-geometry FK, at the
single boundary shared by every output consumer.  It must never be encoded in
a quaternion or selected independently per action.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from .math_utils import qmt_wxyz_to_rotation


OUTPUT_COORDINATE_SCHEMA = "biospur-c2-capture-wide-output-coordinates-v1"


def _validated_reflection(matrix: np.ndarray) -> np.ndarray:
    value = np.asarray(matrix, dtype=float).reshape(3, 3)
    if not np.all(np.isfinite(value)):
        raise ValueError("output-coordinate matrix contains non-finite values")
    if not np.allclose(value.T @ value, np.eye(3), atol=1e-10):
        raise ValueError("output-coordinate matrix is not orthogonal")
    if not np.allclose(value @ value, np.eye(3), atol=1e-10):
        raise ValueError("output-coordinate reflection is not involutive")
    if not np.isclose(np.linalg.det(value), -1.0, atol=1e-10):
        raise ValueError("output-coordinate transform must be one reflection")
    return value


def freeze_capture_wide_lateral_reflection(
    trajectory: dict[str, Any],
    *,
    reference_episode: str = "00",
) -> dict[str, Any]:
    """Freeze the user-confirmed mirror plane from initial pelvis +X.

    The horizontal normal is estimated from every finite initial-still frame,
    not from a selected action result.  The resulting Householder reflection
    is constant for the whole capture and leaves the vertical axis unchanged.
    """

    if "output_coordinate_convention" in trajectory:
        raise ValueError("output-coordinate convention is already frozen")
    pelvis_row = trajectory["trajectory"][reference_episode]["pelvis"]
    rotation = qmt_wxyz_to_rotation(
        pelvis_row["quat_world_segment_wxyz"]
    ).as_matrix()
    horizontal_right = np.asarray(rotation[:, :, 0], dtype=float)
    horizontal_right[:, 2] = 0.0
    norms = np.linalg.norm(horizontal_right, axis=1)
    valid = np.isfinite(horizontal_right).all(axis=1) & (norms > 1e-9)
    if not np.any(valid):
        raise RuntimeError("initial-still pelvis lateral axis is not observable")
    unit = horizontal_right[valid] / norms[valid, None]
    normal = np.mean(unit, axis=0)
    normal /= np.linalg.norm(normal)
    matrix = _validated_reflection(np.eye(3) - 2.0 * np.outer(normal, normal))
    convention = {
        "schema": OUTPUT_COORDINATE_SCHEMA,
        "scope": "ONE_CONSTANT_TRANSFORM_FOR_ALL_CAPTURE_FRAMES_AND_ACTIONS",
        "owner": "POST_FK_CARTESIAN_OUTPUT_BOUNDARY",
        "operation": "HOUSEHOLDER_REFLECTION_ACROSS_INITIAL_BODY_SAGITTAL_PLANE",
        "reference_episode": reference_episode,
        "reference_frame_count": int(np.count_nonzero(valid)),
        "plane_normal_world_internal": normal,
        "matrix_world_output_from_internal": matrix,
        "determinant": float(np.linalg.det(matrix)),
        "involutive": True,
        "vertical_axis_preserved": bool(np.allclose(matrix[:, 2], [0.0, 0.0, 1.0])),
        "quaternions_modified": False,
        "per_action_selection": False,
        "user_confirmed_visual_parity": "THREE_DIMENSIONAL_GLOBAL_MIRROR",
    }
    trajectory["output_coordinate_convention"] = convention
    return convention


def apply_output_coordinate_convention(
    trajectory: Mapping[str, Any],
    joints: Mapping[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Apply the frozen output transform exactly once to FK points."""

    convention = trajectory.get("output_coordinate_convention")
    if convention is None:
        return {name: np.asarray(point, dtype=float).copy() for name, point in joints.items()}
    if convention.get("schema") != OUTPUT_COORDINATE_SCHEMA:
        raise ValueError("unknown output-coordinate convention")
    matrix = _validated_reflection(
        convention["matrix_world_output_from_internal"]
    )
    return {
        name: matrix @ np.asarray(point, dtype=float)
        for name, point in joints.items()
    }
