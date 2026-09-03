"""Small SO(3)/quaternion helpers with explicit wxyz serialization."""
from __future__ import annotations

import math
import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log


def quat_wxyz_to_matrix(q: np.ndarray) -> np.ndarray:
    value = np.asarray(q, float)
    return Rotation.from_quat(value[..., [1, 2, 3, 0]]).as_matrix()


def matrix_to_quat_wxyz(rotation: np.ndarray) -> np.ndarray:
    q = Rotation.from_matrix(np.asarray(rotation, float)).as_quat()
    q = q[..., [3, 0, 1, 2]]
    if q.ndim == 1:
        return -q if q[0] < 0 else q
    for index in range(1, len(q)):
        if float(q[index - 1] @ q[index]) < 0:
            q[index] *= -1
    if len(q) and q[0, 0] < 0:
        q *= -1
    return q


def rz(angle: float | np.ndarray) -> np.ndarray:
    value = np.asarray(angle, float)
    c, s = np.cos(value), np.sin(value)
    output = np.zeros(value.shape + (3, 3), float)
    output[..., 0, 0] = c; output[..., 0, 1] = -s
    output[..., 1, 0] = s; output[..., 1, 1] = c
    output[..., 2, 2] = 1.0
    return output


def wrap_pi(angle: float | np.ndarray) -> float | np.ndarray:
    return (np.asarray(angle) + np.pi) % (2 * np.pi) - np.pi


def heading(rotation: np.ndarray) -> float:
    """Return a stable display yaw from the more horizontal local x/y axis."""
    value = np.asarray(rotation, float)
    x, y = value[:2, 0], value[:2, 1]
    if float(x @ x) >= float(y @ y):
        return math.atan2(float(x[1]), float(x[0]))
    return float(wrap_pi(math.atan2(float(y[1]), float(y[0])) - np.pi / 2))


def proper_mean(rotations: np.ndarray, iterations: int = 30) -> np.ndarray:
    values = np.asarray(rotations, float)
    mean = values[0].copy()
    for _ in range(iterations):
        delta = np.mean([so3_log(mean.T @ value) for value in values], axis=0)
        mean = mean @ so3_exp(delta)
        if np.linalg.norm(delta) < 1e-12:
            break
    return mean


def slerp_rotations(source_time_ns: np.ndarray, rotations: np.ndarray, target_time_ns: np.ndarray) -> np.ndarray:
    times = np.asarray(source_time_ns, np.int64)
    target = np.asarray(target_time_ns, np.int64)
    if len(times) < 2 or np.any(np.diff(times) <= 0):
        raise ValueError("SLERP source time must be strictly increasing")
    seconds = (times - times[0]) * 1e-9
    target_seconds = (target - times[0]) * 1e-9
    return Slerp(seconds, Rotation.from_matrix(rotations))(target_seconds).as_matrix()


def rotation_angle(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    delta = np.einsum("...ji,...jk->...ik", left, right)
    trace = np.trace(delta, axis1=-2, axis2=-1)
    return np.arccos(np.clip((trace - 1.0) / 2.0, -1.0, 1.0))
