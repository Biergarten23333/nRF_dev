from __future__ import annotations

import numpy as np


def normalize(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    n = np.linalg.norm(q, axis=-1, keepdims=True)
    if np.any(n < 1e-15):
        raise ValueError("zero quaternion")
    return q / n


def mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton product of scalar-first quaternions."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    aw, ax, ay, az = np.moveaxis(a, -1, 0)
    bw, bx, by, bz = np.moveaxis(b, -1, 0)
    return np.stack((
        aw*bw-ax*bx-ay*by-az*bz,
        aw*bx+ax*bw+ay*bz-az*by,
        aw*by-ax*bz+ay*bw+az*bx,
        aw*bz+ax*by-ay*bx+az*bw,
    ), axis=-1)


def inv(q: np.ndarray) -> np.ndarray:
    q = normalize(q)
    out = q.copy()
    out[..., 1:] *= -1
    return out


def exp(phi: np.ndarray) -> np.ndarray:
    phi = np.asarray(phi, float)
    angle = np.linalg.norm(phi, axis=-1, keepdims=True)
    half = 0.5 * angle
    scale = np.empty_like(angle)
    np.divide(np.sin(half), angle, out=scale, where=angle > 1e-10)
    scale = np.where(angle > 1e-10, scale, 0.5-angle*angle/48.0)
    return normalize(np.concatenate((np.cos(half), scale*phi), axis=-1))


def log(q: np.ndarray) -> np.ndarray:
    q = normalize(q)
    q = np.where((q[..., :1] < 0), -q, q)
    v = q[..., 1:]
    nv = np.linalg.norm(v, axis=-1, keepdims=True)
    angle = 2*np.arctan2(nv, np.clip(q[..., :1], -1, 1))
    scale = np.empty_like(nv)
    np.divide(angle, nv, out=scale, where=nv > 1e-10)
    return np.where(nv > 1e-10, scale*v, 2*v)


def matrix(q: np.ndarray) -> np.ndarray:
    q = normalize(q)
    w, x, y, z = np.moveaxis(q, -1, 0)
    return np.stack((
        1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
        2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
        2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y),
    ), axis=-1).reshape(q.shape[:-1]+(3, 3))


def from_matrix(R: np.ndarray) -> np.ndarray:
    R = np.asarray(R, float)
    if R.shape != (3, 3):
        raise ValueError("single 3x3 matrix required")
    vals, vecs = np.linalg.eigh(np.array([
        [R[0,0]-R[1,1]-R[2,2], R[1,0]+R[0,1], R[2,0]+R[0,2], R[1,2]-R[2,1]],
        [R[1,0]+R[0,1], R[1,1]-R[0,0]-R[2,2], R[2,1]+R[1,2], R[2,0]-R[0,2]],
        [R[2,0]+R[0,2], R[2,1]+R[1,2], R[2,2]-R[0,0]-R[1,1], R[0,1]-R[1,0]],
        [R[1,2]-R[2,1], R[2,0]-R[0,2], R[0,1]-R[1,0], R.trace()],
    ])/3.0)
    q_xyzw = vecs[:, np.argmax(vals)]
    q = q_xyzw[[3, 0, 1, 2]]
    return normalize(q if q[0] >= 0 else -q)


def rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    return np.einsum("...ij,...j->...i", matrix(q), np.asarray(v, float))


def between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return mul(inv(a), b)


def from_two_vectors(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Return q whose rotation maps unit vector a onto unit vector b."""
    a = np.asarray(a, float); b = np.asarray(b, float)
    a /= np.linalg.norm(a); b /= np.linalg.norm(b)
    d = float(np.dot(a, b))
    if d < -1+1e-10:
        basis = np.eye(3)[np.argmin(np.abs(a))]
        axis = np.cross(a, basis); axis /= np.linalg.norm(axis)
        return exp(np.pi*axis)
    return normalize(np.r_[1+d, np.cross(a, b)])


def continuous(q: np.ndarray) -> np.ndarray:
    q = normalize(q).copy()
    for i in range(1, len(q)):
        if np.dot(q[i-1], q[i]) < 0:
            q[i] *= -1
    return q


def apply_right(q: np.ndarray, delta: np.ndarray) -> np.ndarray:
    return normalize(mul(q, exp(delta)))


def geodesic(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.linalg.norm(log(between(a, b)), axis=-1)


def skew(v: np.ndarray) -> np.ndarray:
    x, y, z = np.asarray(v, float)
    return np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]], float)


def right_jacobian_inverse(phi: np.ndarray) -> np.ndarray:
    """Inverse SO(3) right Jacobian for ``Log(Exp(phi) Exp(delta))``."""
    phi = np.asarray(phi, float)
    if phi.shape != (3,):
        raise ValueError("single three-vector required")
    theta = float(np.linalg.norm(phi)); K = skew(phi)
    if theta < 1e-5:
        return np.eye(3)+0.5*K+(1.0/12.0)*K@K
    coefficient = 1/theta**2-(1+np.cos(theta))/(2*theta*np.sin(theta))
    return np.eye(3)+0.5*K+coefficient*K@K
