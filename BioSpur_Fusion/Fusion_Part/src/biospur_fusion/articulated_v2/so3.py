from __future__ import annotations

import numpy as np


def normalize(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=np.float64)
    n = float(np.linalg.norm(q))
    if not np.isfinite(n) or n == 0.0:
        raise ValueError("invalid quaternion")
    return q / n


def multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return normalize(np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ]))


def exp(rotation_vector: np.ndarray) -> np.ndarray:
    v = np.asarray(rotation_vector, dtype=np.float64)
    theta = float(np.linalg.norm(v))
    if theta < 1e-12:
        return normalize(np.r_[1.0, 0.5 * v])
    return np.r_[np.cos(theta / 2), np.sin(theta / 2) * v / theta]


def conjugate(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=np.float64)
    return np.array([q[0], -q[1], -q[2], -q[3]])


def rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    q = normalize(q)
    w, xyz = q[0], q[1:]
    v = np.asarray(v, dtype=np.float64)
    return v + 2.0 * np.cross(xyz, np.cross(xyz, v) + w * v)


def log(q: np.ndarray) -> np.ndarray:
    q = normalize(q)
    if q[0] < 0:
        q = -q
    s = float(np.linalg.norm(q[1:]))
    if s < 1e-12:
        return 2.0 * q[1:]
    return 2.0 * np.arctan2(s, q[0]) * q[1:] / s


def geodesic(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(log(multiply(conjugate(a), b))))
