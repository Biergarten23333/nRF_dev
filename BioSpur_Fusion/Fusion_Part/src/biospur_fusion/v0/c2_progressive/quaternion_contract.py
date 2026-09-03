"""Numeric quaternion convention gate shared by trajectory and viewer owners."""
from __future__ import annotations

from typing import Any

import numpy as np
import qmt
from scipy.spatial.transform import Rotation


def qmt_wxyz_to_scipy_active(quaternion_wxyz: np.ndarray) -> Rotation:
    """Convert QMT wxyz world-from-sensor quaternions to SciPy active rotations."""

    value = np.asarray(quaternion_wxyz, dtype=float)
    if value.shape[-1] != 4:
        raise ValueError("QMT quaternion must end in four wxyz coordinates")
    return Rotation.from_quat(value[..., [1, 2, 3, 0]])


def scipy_active_to_qmt_wxyz(rotation: Rotation) -> np.ndarray:
    value = np.asarray(rotation.as_quat(), dtype=float)
    return value[..., [3, 0, 1, 2]]


def viewer_active_matrix(quaternion_wxyz: np.ndarray) -> np.ndarray:
    """Viewer contract: active world-from-local 3x3 matrix."""

    return qmt_wxyz_to_scipy_active(quaternion_wxyz).as_matrix()


def numeric_round_trip_gate() -> dict[str, Any]:
    angles = np.array([0.0, np.pi / 7.0, -np.pi / 3.0, np.pi, 2.0 * np.pi])
    axes = np.array([
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        [1.0, 2.0, -1.0],
        [-2.0, 1.0, 3.0],
    ])
    axes /= np.linalg.norm(axes, axis=1, keepdims=True)
    qmt_quat = np.asarray(qmt.quatFromAngleAxis(angles, axes), dtype=float)
    scipy_rotation = qmt_wxyz_to_scipy_active(qmt_quat)
    round_trip = scipy_active_to_qmt_wxyz(scipy_rotation)
    dots = np.abs(np.sum(qmt_quat * round_trip, axis=1))
    vectors = np.array([
        [0.3, -0.2, 0.7],
        [-1.0, 2.0, 0.5],
        [0.0, 0.0, 1.0],
        [0.4, 0.5, -0.6],
        [1.0, 0.0, 0.0],
    ])
    scipy_vectors = scipy_rotation.apply(vectors)
    qmt_vectors = np.asarray(qmt.rotate(qmt_quat, vectors), dtype=float)
    matrices = viewer_active_matrix(qmt_quat)
    viewer_vectors = np.einsum("nij,nj->ni", matrices, vectors)
    qmt_direct_error = np.linalg.norm(qmt_vectors - scipy_vectors, axis=1)
    vector_error = np.linalg.norm(scipy_vectors - viewer_vectors, axis=1)
    inverse_error = np.linalg.norm(
        scipy_rotation.inv().apply(scipy_vectors) - vectors, axis=1,
    )
    qmt_inverse_vectors = np.asarray(qmt.rotate(qmt.qinv(qmt_quat), qmt_vectors), dtype=float)
    qmt_inverse_error = np.linalg.norm(qmt_inverse_vectors - vectors, axis=1)
    known_quarter_turn = np.asarray(qmt.quatFromAngleAxis(np.pi / 2.0, [0.0, 0.0, 1.0]))
    known_forward = np.asarray(qmt.rotate(known_quarter_turn, [1.0, 0.0, 0.0]), dtype=float)
    known_inverse = np.asarray(qmt.rotate(qmt.qinv(known_quarter_turn), known_forward), dtype=float)
    passed = bool(
        np.min(dots) >= 1.0 - 1e-12
        and np.max(qmt_direct_error) <= 1e-12
        and np.max(vector_error) <= 1e-12
        and np.max(inverse_error) <= 1e-12
        and np.max(qmt_inverse_error) <= 1e-12
        and np.linalg.norm(known_forward - np.array([0.0, 1.0, 0.0])) <= 1e-12
        and np.linalg.norm(known_inverse - np.array([1.0, 0.0, 0.0])) <= 1e-12
        and np.linalg.det(matrices).min() >= 1.0 - 1e-12
    )
    return {
        "schema": "biospur-c2-qmt-scipy-viewer-quaternion-round-trip-v1",
        "qmt_storage": "WXYZ",
        "qmt_semantics": "ACTIVE_WORLD_FROM_SENSOR",
        "scipy_storage": "XYZW",
        "scipy_semantics": "ACTIVE_ROTATION_APPLIED_TO_COLUMN_VECTOR",
        "viewer_semantics": "ACTIVE_WORLD_FROM_LOCAL_3X3_TIMES_COLUMN_VECTOR",
        "pass": passed,
        "minimum_absolute_quaternion_dot": float(np.min(dots)),
        "maximum_official_qmt_rotate_vs_scipy_active_error": float(np.max(qmt_direct_error)),
        "maximum_scipy_viewer_vector_error": float(np.max(vector_error)),
        "maximum_active_inverse_round_trip_error": float(np.max(inverse_error)),
        "maximum_official_qmt_conjugate_inverse_error": float(np.max(qmt_inverse_error)),
        "known_positive_z_quarter_turn_of_x": known_forward.tolist(),
        "known_conjugate_inverse_result": known_inverse.tolist(),
        "minimum_matrix_determinant": float(np.linalg.det(matrices).min()),
        "includes_full_circle_case": True,
    }
