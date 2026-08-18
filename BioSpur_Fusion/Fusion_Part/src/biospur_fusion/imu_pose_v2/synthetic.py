"""Independent hand-written oracle fixtures for Phase 3-R2 qualification.

This module intentionally does not import the production SO(3), FK, residual,
Jacobian, or preintegration helpers.
"""
from __future__ import annotations

import hashlib
import math
from typing import Mapping

import numpy as np

from .calibration import CalibrationObservation, EXPECTED_NODES
from .types import FrontendFrame, ImuObservation, SEGMENTS


def oracle_quaternion(axis: np.ndarray, angle_rad: float) -> np.ndarray:
    axis = np.asarray(axis, float); axis = axis / np.linalg.norm(axis)
    return np.r_[math.cos(angle_rad / 2), axis * math.sin(angle_rad / 2)]


def oracle_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    aw, ax, ay, az = np.asarray(a, float)
    bw, bx, by, bz = np.asarray(b, float)
    return np.array([
        aw*bw-ax*bx-ay*by-az*bz,
        aw*bx+ax*bw+ay*bz-az*by,
        aw*by-ax*bz+ay*bw+az*bx,
        aw*bz+ax*by-ay*bx+az*bw,
    ])


def oracle_matrix(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, float) / np.linalg.norm(q); w, x, y, z = q
    return np.array([
        [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
    ])


def oracle_fk(q: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    def rotate(name: str, vector: np.ndarray) -> np.ndarray:
        return oracle_matrix(q[name]) @ vector
    points = {"pelvis": np.zeros(3)}
    points["torso_joint"] = rotate("pelvis", np.array([0., 0., .10]))
    points["chest"] = points["torso_joint"] + rotate("torso", np.array([0., 0., .24]))
    for side, sign in (("left", 1.), ("right", -1.)):
        points[f"shoulder_{side}"] = points["chest"] + rotate("torso", np.array([sign*.19, 0., 0.]))
        points[f"elbow_{side}"] = points[f"shoulder_{side}"] + rotate(f"upper_arm_{side}", np.array([0., 0., -.28]))
        points[f"wrist_{side}"] = points[f"elbow_{side}"] + rotate(f"forearm_{side}", np.array([0., 0., -.25]))
        points[f"hip_{side}"] = rotate("pelvis", np.array([sign*.10, 0., 0.]))
        points[f"knee_{side}"] = points[f"hip_{side}"] + rotate(f"thigh_{side}", np.array([0., 0., -.42]))
        points[f"ankle_{side}"] = points[f"knee_{side}"] + rotate(f"shank_{side}", np.array([0., 0., -.41]))
    return points


def synthetic_calibration_rows(
    mapping: Mapping[str, str], actions: tuple[str, ...],
    q_i_s: Mapping[str, np.ndarray] | None = None,
) -> list[CalibrationObservation]:
    if set(mapping) != EXPECTED_NODES:
        raise ValueError("synthetic mapping must name exact hardware")
    rows: list[CalibrationObservation] = []
    directions = (
        np.array([1., 0., 0.]), np.array([0., 1., 0.]), np.array([0., 0., 1.]),
        np.array([1., 1., 1.]) / math.sqrt(3),
    )
    for action_index, action in enumerate(actions):
        source = directions[action_index % len(directions)]
        for node in sorted(mapping):
            rotation = np.eye(3) if q_i_s is None else oracle_matrix(q_i_s[node])
            rows.append(CalibrationObservation(
                action, f"{action}:cycle:{action_index:02d}", "FIT", node,
                source.copy(), rotation @ source, 1.0,
                hashlib.sha256(f"{action}|{node}".encode()).hexdigest(),
            ))
    return rows


def synthetic_imu_stream(node: str, *, boot: int = 1, samples: int = 800,
                         dt_us: int = 5000, gyro: np.ndarray | None = None) -> list[ImuObservation]:
    gyro = np.array([0.0, 0.0, 0.02]) if gyro is None else np.asarray(gyro, float)
    return [ImuObservation(
        node, boot, index * dt_us, 1_000_000_000 + index * dt_us * 1000,
        index, gyro.copy(), np.array([0., 0., 9.80665]),
        source_record_offset=index * 64,
    ) for index in range(samples)]


def frontend_frame(node: str, segment_index: int, scheduled_ns: int, *,
                   status: str = "FILTERED", yaw_rad: float = 0.0,
                   variance: float = 2e-4) -> FrontendFrame:
    q = oracle_quaternion(np.array([0., 0., 1.]), yaw_rad)
    return FrontendFrame(
        node, 1, f"{node}:{scheduled_ns}", scheduled_ns - 2_500_000,
        q, np.zeros(3), np.zeros(3), np.eye(9) * variance,
        False, status, 2_500_000, 0,
    )


def identity_orientations() -> dict[str, np.ndarray]:
    return {segment: np.array([1., 0., 0., 0.]) for segment in SEGMENTS}
