"""Small explicit SE(3)/SO(3) kernels with Root-R6A0 conventions."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial.transform import Rotation


def so3_exp(rotvec: np.ndarray) -> np.ndarray:
    value = np.asarray(rotvec, float)
    if value.shape != (3,) or not np.isfinite(value).all():
        raise ValueError("rotation vector must be finite shape (3,)")
    return Rotation.from_rotvec(value).as_matrix()


def so3_log(rotation: np.ndarray) -> np.ndarray:
    value = np.asarray(rotation, float)
    if value.shape != (3, 3) or not np.isfinite(value).all():
        raise ValueError("rotation must be finite shape (3,3)")
    if np.linalg.det(value) < 0.999999 or not np.allclose(value.T @ value, np.eye(3), atol=1e-8):
        raise ValueError("rotation must be proper")
    return Rotation.from_matrix(value).as_rotvec()


def central_jacobian(function, vector: np.ndarray, step: float = 1e-6) -> np.ndarray:
    x = np.asarray(vector, float)
    baseline = np.atleast_1d(np.asarray(function(x), float))
    result = np.empty((baseline.size, x.size), float)
    for index in range(x.size):
        delta = np.zeros_like(x)
        delta[index] = step
        result[:, index] = (
            np.atleast_1d(np.asarray(function(x + delta), float))
            - np.atleast_1d(np.asarray(function(x - delta), float))
        ) / (2.0 * step)
    return result


@dataclass(frozen=True)
class Pose:
    rotation: np.ndarray
    translation: np.ndarray

    def __post_init__(self) -> None:
        rotation = np.asarray(self.rotation, float)
        translation = np.asarray(self.translation, float)
        if rotation.shape != (3, 3) or translation.shape != (3,):
            raise ValueError("Pose expects R(3,3), t(3,)")
        if not np.isfinite(rotation).all() or not np.isfinite(translation).all():
            raise ValueError("Pose must be finite")
        if np.linalg.det(rotation) < 0.999999 or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8):
            raise ValueError("Pose rotation must be proper")

    @staticmethod
    def identity() -> "Pose":
        return Pose(np.eye(3), np.zeros(3))

    def compose(self, child: "Pose") -> "Pose":
        return Pose(self.rotation @ child.rotation, self.translation + self.rotation @ child.translation)

    def transform_point(self, point: np.ndarray) -> np.ndarray:
        return self.translation + self.rotation @ np.asarray(point, float)

    def inverse(self) -> "Pose":
        inverse_rotation = self.rotation.T
        return Pose(inverse_rotation, -inverse_rotation @ self.translation)
