"""Scalar-first quaternion math with active local-to-global rotations."""
from __future__ import annotations

import numpy as np


def normalize(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    n = np.linalg.norm(q, axis=-1, keepdims=True)
    if np.any(~np.isfinite(n)) or np.any(n <= 1e-12):
        raise ValueError("non-finite or zero quaternion")
    return q / n


def conjugate(q: np.ndarray) -> np.ndarray:
    out = np.asarray(q, dtype=float).copy()
    out[..., 1:] *= -1
    return out


def multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton product: active rotation b is applied first, then a."""
    a, b = np.broadcast_arrays(np.asarray(a, float), np.asarray(b, float))
    aw, ax, ay, az = np.moveaxis(a, -1, 0)
    bw, bx, by, bz = np.moveaxis(b, -1, 0)
    return np.stack((
        aw*bw - ax*bx - ay*by - az*bz,
        aw*bx + ax*bw + ay*bz - az*by,
        aw*by - ax*bz + ay*bw + az*bx,
        aw*bz + ax*by - ay*bx + az*bw,
    ), axis=-1)


def rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    q = normalize(q)
    v = np.asarray(v, float)
    qv = q[..., 1:]
    uv = np.cross(qv, v)
    uuv = np.cross(qv, uv)
    return v + 2.0 * (q[..., :1] * uv + uuv)


def from_axis_angle(axis: np.ndarray, angle: float) -> np.ndarray:
    axis = np.asarray(axis, float)
    axis = axis / np.linalg.norm(axis)
    return np.r_[np.cos(angle/2), axis*np.sin(angle/2)]


def to_matrix(q: np.ndarray) -> np.ndarray:
    q = normalize(q)
    w, x, y, z = np.moveaxis(q, -1, 0)
    return np.stack((
        1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
        2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
        2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y),
    ), axis=-1).reshape(q.shape[:-1] + (3, 3))


def sign_continuous(q: np.ndarray) -> np.ndarray:
    q = normalize(q).copy()
    for i in range(1, len(q)):
        if np.dot(q[i-1], q[i]) < 0:
            q[i] *= -1
    return q


def equivalent(a: np.ndarray, b: np.ndarray, atol: float = 1e-9) -> bool:
    a, b = normalize(a), normalize(b)
    return bool(min(np.linalg.norm(a-b), np.linalg.norm(a+b)) <= atol)


def mean(q: np.ndarray) -> np.ndarray:
    """Markley quaternion mean, deterministic under sign equivalence."""
    q = sign_continuous(np.asarray(q, float))
    values, vectors = np.linalg.eigh(q.T @ q)
    out = vectors[:, np.argmax(values)]
    if np.dot(out, q[0]) < 0:
        out *= -1
    return normalize(out)


def slerp_pair(q0: np.ndarray, q1: np.ndarray, u: np.ndarray) -> np.ndarray:
    q0, q1 = normalize(q0), normalize(q1)
    dot = np.sum(q0*q1, axis=-1)
    flip = dot < 0
    q1 = np.where(flip[..., None], -q1, q1)
    dot = np.clip(np.abs(dot), -1.0, 1.0)
    u = np.asarray(u, float)
    linear = dot > 0.9995
    theta = np.arccos(dot)
    denom = np.sin(theta)
    safe = np.where(linear, 1.0, denom)
    a = np.where(linear, 1-u, np.sin((1-u)*theta)/safe)
    b = np.where(linear, u, np.sin(u*theta)/safe)
    return normalize(a[..., None]*q0 + b[..., None]*q1)


def resample_quaternions(times: np.ndarray, quats: np.ndarray, grid: np.ndarray,
                         max_gap_s: float) -> tuple[np.ndarray, np.ndarray]:
    times = np.asarray(times, float)
    quats = sign_continuous(quats)
    if len(times) < 2 or np.any(np.diff(times) <= 0):
        raise ValueError("orientation timestamps must be strictly monotonic")
    right = np.searchsorted(times, grid, side="right")
    left = right - 1
    valid = (left >= 0) & (right < len(times))
    li = np.clip(left, 0, len(times)-1)
    ri = np.clip(right, 0, len(times)-1)
    span = times[ri] - times[li]
    valid &= span <= max_gap_s + 1e-12
    u = np.divide(grid-times[li], span, out=np.zeros_like(grid), where=span > 0)
    out = slerp_pair(quats[li], quats[ri], np.clip(u, 0, 1))
    out[~valid] = np.nan
    return out, valid


def mounting_calibration(q_gs_cal: np.ndarray, q_gb_desired: np.ndarray | None = None) -> np.ndarray:
    """q_SB = inverse(q_GS_cal) * q_GB_desired."""
    desired = np.array([1., 0., 0., 0.]) if q_gb_desired is None else normalize(q_gb_desired)
    return normalize(multiply(conjugate(normalize(q_gs_cal)), desired))


def apply_mounting(q_gs: np.ndarray, q_sb: np.ndarray) -> np.ndarray:
    return normalize(multiply(q_gs, q_sb))


def relative(parent_gb: np.ndarray, child_gb: np.ndarray) -> np.ndarray:
    return normalize(multiply(conjugate(parent_gb), child_gb))
