"""Small numeric helpers for C2 coupled-progressive calibration."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import qmt
from scipy.spatial.transform import Rotation


EPS = 1e-12


def array_binding(value: np.ndarray) -> dict[str, Any]:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
    ).encode("utf-8")
    return {
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "sha256": hashlib.sha256(header + array.tobytes()).hexdigest(),
    }


def semantic_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def unit(value: np.ndarray, fallback: np.ndarray | None = None) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    norm = np.linalg.norm(vector, axis=-1, keepdims=True)
    if fallback is None:
        fallback = np.zeros(vector.shape[-1], dtype=float)
        fallback[0] = 1.0
    return np.where(norm > EPS, vector / np.maximum(norm, EPS), np.asarray(fallback, dtype=float))


def skew(vector: np.ndarray) -> np.ndarray:
    x, y, z = np.asarray(vector, dtype=float)
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]], dtype=float)


def qmt_wxyz_to_rotation(quat_wxyz: np.ndarray) -> Rotation:
    q = np.asarray(quat_wxyz, dtype=float)
    if q.shape[-1] != 4:
        raise ValueError("quaternion must end in WXYZ coordinates")
    return Rotation.from_quat(q[..., [1, 2, 3, 0]])


def rotation_to_qmt_wxyz(rotation: Rotation) -> np.ndarray:
    q = np.asarray(rotation.as_quat(), dtype=float)
    return q[..., [3, 0, 1, 2]]


def normalize_quat_wxyz(quat: np.ndarray) -> np.ndarray:
    q = np.asarray(quat, dtype=float)
    norm = np.linalg.norm(q, axis=-1, keepdims=True)
    out = q / np.maximum(norm, EPS)
    flip = out[..., 0:1] < 0.0
    return np.where(flip, -out, out)


def interp_quat_wxyz(t_src: np.ndarray, quat: np.ndarray, t_dst: np.ndarray) -> np.ndarray:
    src = np.asarray(t_src, dtype=float)
    q = np.asarray(quat, dtype=float)
    dst = np.asarray(t_dst, dtype=float)
    aligned = q.copy()
    for index in range(1, len(aligned)):
        if float(np.dot(aligned[index - 1], aligned[index])) < 0.0:
            aligned[index] *= -1.0
    out = np.column_stack([np.interp(dst, src, aligned[:, axis]) for axis in range(4)])
    return normalize_quat_wxyz(out)


def interp_vectors(t_src: np.ndarray, vectors: np.ndarray, t_dst: np.ndarray) -> np.ndarray:
    src = np.asarray(t_src, dtype=float)
    value = np.asarray(vectors, dtype=float)
    dst = np.asarray(t_dst, dtype=float)
    return np.column_stack([np.interp(dst, src, value[:, axis]) for axis in range(value.shape[1])])


def wrap_pi(angle: np.ndarray | float) -> np.ndarray | float:
    return (np.asarray(angle) + np.pi) % (2.0 * np.pi) - np.pi


def yaw_rotation(angle_rad: np.ndarray | float) -> Rotation:
    return Rotation.from_euler("z", angle_rad)


def mean_rotation_matrix(matrices: np.ndarray) -> np.ndarray:
    m = np.mean(np.asarray(matrices, dtype=float), axis=0)
    u, _, vt = np.linalg.svd(m)
    r = u @ vt
    if np.linalg.det(r) < 0.0:
        u[:, -1] *= -1.0
        r = u @ vt
    return r


def angular_distance(a: np.ndarray, b: np.ndarray) -> float:
    aa = np.asarray(a, dtype=float)
    bb = np.asarray(b, dtype=float)
    return float(np.arccos(np.clip(abs(float(np.dot(unit(aa), unit(bb)))), -1.0, 1.0)))


def qmt_heading_corrected_quat(
    delta_filt: np.ndarray,
    child_quat_wxyz: np.ndarray,
) -> np.ndarray:
    return np.asarray(qmt.qmult(qmt.quatFromAngleAxis(delta_filt, [0.0, 0.0, 1.0]), child_quat_wxyz), dtype=float)
