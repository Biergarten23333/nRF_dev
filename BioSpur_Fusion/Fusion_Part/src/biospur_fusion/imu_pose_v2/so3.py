"""SO(3) algebra for scalar-first active rotations.

``R_AB`` maps coordinates in frame B to coordinates in frame A.  Quaternion
multiplication follows the same left-to-right frame composition.  Tangent
errors are right-local unless a function explicitly says otherwise.
"""
from __future__ import annotations

import numpy as np


def normalize(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    norm = np.linalg.norm(q, axis=-1, keepdims=True)
    if np.any(norm < 1e-15):
        raise ValueError("zero quaternion")
    return q / norm


def mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
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
    result = normalize(q).copy()
    result[..., 1:] *= -1
    return result


def exp(phi: np.ndarray) -> np.ndarray:
    phi = np.asarray(phi, float)
    theta = np.linalg.norm(phi, axis=-1, keepdims=True)
    half = theta / 2
    scale = np.where(theta > 1e-10, np.sin(half) / np.maximum(theta, 1e-300), 0.5-theta*theta/48)
    return normalize(np.concatenate((np.cos(half), scale*phi), axis=-1))


def log(q: np.ndarray) -> np.ndarray:
    q = normalize(q)
    q = np.where(q[..., :1] < 0, -q, q)
    vector = q[..., 1:]
    norm = np.linalg.norm(vector, axis=-1, keepdims=True)
    angle = 2*np.arctan2(norm, np.clip(q[..., :1], -1, 1))
    scale = np.where(norm > 1e-10, angle / np.maximum(norm, 1e-300), 2.0)
    return scale*vector


def matrix(q: np.ndarray) -> np.ndarray:
    q = normalize(q)
    w, x, y, z = np.moveaxis(q, -1, 0)
    return np.stack((
        1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
        2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
        2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y),
    ), axis=-1).reshape(q.shape[:-1]+(3, 3))


def from_matrix(rotation: np.ndarray) -> np.ndarray:
    rotation = np.asarray(rotation, float)
    if rotation.shape != (3, 3): raise ValueError("single 3x3 rotation required")
    # Shepperd branches avoid importing any calibration solver convention.
    trace = float(np.trace(rotation))
    if trace > 0:
        scale = np.sqrt(trace+1.0)*2
        q = np.array([0.25*scale, (rotation[2,1]-rotation[1,2])/scale,
                      (rotation[0,2]-rotation[2,0])/scale, (rotation[1,0]-rotation[0,1])/scale])
    else:
        index = int(np.argmax(np.diag(rotation)))
        if index == 0:
            scale = np.sqrt(1+rotation[0,0]-rotation[1,1]-rotation[2,2])*2
            q = np.array([(rotation[2,1]-rotation[1,2])/scale, .25*scale,
                          (rotation[0,1]+rotation[1,0])/scale, (rotation[0,2]+rotation[2,0])/scale])
        elif index == 1:
            scale = np.sqrt(1+rotation[1,1]-rotation[0,0]-rotation[2,2])*2
            q = np.array([(rotation[0,2]-rotation[2,0])/scale,
                          (rotation[0,1]+rotation[1,0])/scale, .25*scale,
                          (rotation[1,2]+rotation[2,1])/scale])
        else:
            scale = np.sqrt(1+rotation[2,2]-rotation[0,0]-rotation[1,1])*2
            q = np.array([(rotation[1,0]-rotation[0,1])/scale,
                          (rotation[0,2]+rotation[2,0])/scale,
                          (rotation[1,2]+rotation[2,1])/scale, .25*scale])
    q = normalize(q)
    return q if q[0] >= 0 else -q


def rotate(q: np.ndarray, vector: np.ndarray) -> np.ndarray:
    return np.einsum("...ij,...j->...i", matrix(q), np.asarray(vector, float))


def between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return mul(inv(a), b)


def apply_right(q: np.ndarray, delta: np.ndarray) -> np.ndarray:
    return normalize(mul(q, exp(delta)))


def skew(vector: np.ndarray) -> np.ndarray:
    x, y, z = np.asarray(vector, float)
    return np.array(((0, -z, y), (z, 0, -x), (-y, x, 0)), float)


def from_two_vectors(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    source = np.asarray(source, float); source /= np.linalg.norm(source)
    target = np.asarray(target, float); target /= np.linalg.norm(target)
    dot = float(source @ target)
    if dot < -1 + 1e-10:
        basis = np.eye(3)[np.argmin(np.abs(source))]
        axis = np.cross(source, basis); axis /= np.linalg.norm(axis)
        return exp(np.pi*axis)
    return normalize(np.r_[1+dot, np.cross(source, target)])


def right_jacobian_inverse(phi: np.ndarray) -> np.ndarray:
    phi = np.asarray(phi, float)
    theta = float(np.linalg.norm(phi)); cross = skew(phi)
    if theta < 1e-5:
        return np.eye(3)+0.5*cross+(1/12)*cross@cross
    coefficient = 1/theta**2-(1+np.cos(theta))/(2*theta*np.sin(theta))
    return np.eye(3)+0.5*cross+coefficient*cross@cross


def compose_right_covariance(q_I_S: np.ndarray, covariance_WI: np.ndarray,
                             covariance_IS: np.ndarray, cross: np.ndarray | None = None) -> np.ndarray:
    """Covariance of ``q_WS=q_WI*q_IS`` in the right-local S tangent."""
    adjoint_SI = matrix(inv(q_I_S))
    covariance_WI = np.asarray(covariance_WI, float)
    covariance_IS = np.asarray(covariance_IS, float)
    result = adjoint_SI@covariance_WI@adjoint_SI.T + covariance_IS
    if cross is not None:
        cross = np.asarray(cross, float)
        result += adjoint_SI@cross + cross.T@adjoint_SI.T
    return 0.5*(result+result.T)
