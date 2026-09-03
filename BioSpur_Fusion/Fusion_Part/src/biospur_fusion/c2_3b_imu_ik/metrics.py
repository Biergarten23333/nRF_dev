"""Preregistered SO(3) metrics and aggregation helpers for C2 3B."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from scipy.spatial.transform import Rotation

from .contracts import EDGE_ROWS, SEGMENTS, AxisPair


def rotation_angle_rad(matrices: np.ndarray) -> np.ndarray:
    matrices = np.asarray(matrices, dtype=np.float64)
    flat = matrices.reshape(-1, 3, 3)
    return Rotation.from_matrix(flat).magnitude().reshape(matrices.shape[:-2])


def pose_delta_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.degrees(rotation_angle_rad(np.swapaxes(a, -1, -2) @ b))


def measurement_residual_deg(measured: np.ndarray, candidate: np.ndarray) -> np.ndarray:
    return pose_delta_deg(measured, candidate)


def axis_angles_deg(matrices: np.ndarray, axes: Iterable[AxisPair]) -> np.ndarray:
    """Sign-invariant projective line angles in [0, 90] degrees."""

    index = {name: position for position, name in enumerate(SEGMENTS)}
    rows = []
    for axis in axes:
        parent = matrices[:, index[axis.parent]] @ axis.parent_axis
        child = matrices[:, index[axis.child]] @ axis.child_axis
        cross = np.cross(parent, child)
        dot = np.clip(np.sum(parent * child, axis=1), -1.0, 1.0)
        rows.append(np.degrees(np.arctan2(np.linalg.norm(cross, axis=1), np.abs(dot))))
    return np.stack(rows, axis=1)


def first_order_mask(valid: np.ndarray) -> np.ndarray:
    valid = np.asarray(valid, dtype=bool)
    result = np.zeros_like(valid)
    result[1:] = valid[:-1] & valid[1:]
    return result


def second_order_mask(valid: np.ndarray) -> np.ndarray:
    valid = np.asarray(valid, dtype=bool)
    result = np.zeros_like(valid)
    result[1:-1] = valid[:-2] & valid[1:-1] & valid[2:]
    return result


def geodesic_steps_deg(matrices: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mask = first_order_mask(valid)
    values = np.full(matrices.shape[:2], np.nan, dtype=np.float64)
    difference = np.swapaxes(matrices[:-1], -1, -2) @ matrices[1:]
    values[1:] = np.degrees(rotation_angle_rad(difference))
    values[~mask] = np.nan
    return values, mask


def second_differences_deg(
    matrices: np.ndarray,
    time_s: np.ndarray,
    valid: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    mask = second_order_mask(valid)
    values = np.full(matrices.shape[:2], np.nan, dtype=np.float64)
    dt0 = time_s[1:-1] - time_s[:-2]
    dt1 = time_s[2:] - time_s[1:-1]
    previous = Rotation.from_matrix(
        (np.swapaxes(matrices[:-2], -1, -2) @ matrices[1:-1]).reshape(-1, 3, 3)
    ).as_rotvec().reshape(len(dt0), len(SEGMENTS), 3)
    following = Rotation.from_matrix(
        (np.swapaxes(matrices[1:-1], -1, -2) @ matrices[2:]).reshape(-1, 3, 3)
    ).as_rotvec().reshape(len(dt1), len(SEGMENTS), 3)
    scaled = np.linalg.norm(
        following / dt1[:, None, None] - previous / dt0[:, None, None], axis=2
    ) * ((dt0 + dt1) / 2.0)[:, None]
    values[1:-1] = np.degrees(scaled)
    values[~mask] = np.nan
    return values, mask


def relative_orientations(matrices: np.ndarray) -> np.ndarray:
    index = {name: position for position, name in enumerate(SEGMENTS)}
    return np.stack(
        [
            np.swapaxes(matrices[:, index[parent]], -1, -2) @ matrices[:, index[child]]
            for _, parent, child in EDGE_ROWS
        ],
        axis=1,
    )


def relative_paths_deg(matrices: np.ndarray, valid: np.ndarray) -> np.ndarray:
    relative = relative_orientations(matrices)
    mask = first_order_mask(valid)
    difference = np.swapaxes(relative[:-1], -1, -2) @ relative[1:]
    angles = np.degrees(rotation_angle_rad(difference))
    return np.sum(angles[mask[1:]], axis=0) if np.any(mask[1:]) else np.zeros(len(EDGE_ROWS))


def linear_quantile(values: np.ndarray, q: float) -> float:
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0 or not np.all(np.isfinite(values)):
        raise ValueError("finite nonempty quantile input required")
    return float(np.quantile(values, q, method="linear"))


def finite_values(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    return values[np.isfinite(values)]


def summary(values: np.ndarray) -> dict[str, float | int]:
    values = finite_values(values)
    if values.size == 0:
        return {"count": 0, "median": math.nan, "p95": math.nan, "max": math.nan}
    return {
        "count": int(values.size),
        "median": linear_quantile(values, 0.5),
        "p95": linear_quantile(values, 0.95),
        "max": float(np.max(values)),
    }


def continuity_pass(a: float, b: float, *, ratio_limit: float, absolute_limit: float) -> bool:
    if not np.isfinite(a) or not np.isfinite(b):
        return False
    if a < 0.1:
        return b - a <= absolute_limit
    return b / a <= ratio_limit
