"""Branch-free soft factors for unoriented functional-axis lines."""

from __future__ import annotations

import math

import numpy as np

from .contracts import CUT_LOCUS_DOT_TOL, PROJECTOR_HUBER_CHORD


def _unit_axis(axis: np.ndarray) -> np.ndarray:
    value = np.asarray(axis, dtype=np.float64)
    if (
        value.shape != (3,)
        or not np.all(np.isfinite(value))
        or not np.isclose(np.linalg.norm(value), 1.0, rtol=0.0, atol=1e-12)
    ):
        raise ValueError("finite unit axis required")
    return value


def world_axis(rotation: np.ndarray, local_axis: np.ndarray) -> np.ndarray:
    rotation = np.asarray(rotation, dtype=np.float64)
    if rotation.shape != (3, 3) or not np.all(np.isfinite(rotation)):
        raise ValueError("finite 3x3 rotation required")
    return rotation @ _unit_axis(local_axis)


def projector_error(
    parent_world: np.ndarray,
    child_world: np.ndarray,
    parent_axis: np.ndarray,
    child_axis: np.ndarray,
) -> tuple[np.ndarray, float, float]:
    """Return E, chordal line distance, and absolute line dot product."""

    parent = world_axis(parent_world, parent_axis)
    child = world_axis(child_world, child_axis)
    error = (
        np.outer(parent, parent) - np.outer(child, child)
    ) / math.sqrt(2.0)
    chord = float(np.linalg.norm(error))
    absolute_dot = abs(float(np.clip(parent @ child, -1.0, 1.0)))
    return error, chord, absolute_dot


def projector_huber_pseudo_residual(
    parent_world: np.ndarray,
    child_world: np.ndarray,
    parent_axis: np.ndarray,
    child_axis: np.ndarray,
    axis_weight: float,
) -> np.ndarray:
    """Nine-row residual whose half squared norm is lambda*rho(||E||)."""

    weight = float(axis_weight)
    if not np.isfinite(weight) or weight < 0.0:
        raise ValueError("finite nonnegative axis weight required")
    if weight == 0.0:
        return np.zeros(9, dtype=np.float64)
    parent = world_axis(parent_world, parent_axis)
    child = world_axis(child_world, child_axis)
    return projector_huber_pseudo_residual_world(parent, child, weight)


def projector_huber_pseudo_residual_world(
    parent_world_axis: np.ndarray,
    child_world_axis: np.ndarray,
    axis_weight: float,
) -> np.ndarray:
    """Fast production path for already validated world unit axes."""

    weight = float(axis_weight)
    if weight == 0.0:
        return np.zeros(9, dtype=np.float64)
    error = (
        np.outer(parent_world_axis, parent_world_axis)
        - np.outer(child_world_axis, child_world_axis)
    ) / math.sqrt(2.0)
    chord = float(np.linalg.norm(error))
    if chord <= 1e-15:
        return np.zeros(9, dtype=np.float64)
    huber = (
        0.5 * chord * chord
        if chord <= PROJECTOR_HUBER_CHORD
        else PROJECTOR_HUBER_CHORD * (chord - 0.5 * PROJECTOR_HUBER_CHORD)
    )
    scale = math.sqrt(2.0 * weight * huber) / chord
    return scale * error.reshape(9, order="C")


def projector_huber_pseudo_residual_parent(
    parent_axis: np.ndarray,
    relative_parent_child: np.ndarray,
    child_axis: np.ndarray,
    axis_weight: float,
) -> np.ndarray:
    """Return the projector residual in the named parent segment frame."""

    return projector_huber_pseudo_residual_world(
        parent_axis,
        relative_parent_child @ child_axis,
        axis_weight,
    )


def line_angle_rad(
    parent_world: np.ndarray,
    child_world: np.ndarray,
    parent_axis: np.ndarray,
    child_axis: np.ndarray,
) -> float:
    parent = world_axis(parent_world, parent_axis)
    child = world_axis(child_world, child_axis)
    cross_norm = float(np.linalg.norm(np.cross(parent, child)))
    return math.atan2(cross_norm, abs(float(np.clip(parent @ child, -1.0, 1.0))))


def at_projective_cut(
    parent_world: np.ndarray,
    child_world: np.ndarray,
    parent_axis: np.ndarray,
    child_axis: np.ndarray,
) -> bool:
    _, _, absolute_dot = projector_error(
        parent_world, child_world, parent_axis, child_axis
    )
    return absolute_dot <= CUT_LOCUS_DOT_TOL


def at_projective_cut_parent(
    parent_axis: np.ndarray,
    relative_parent_child: np.ndarray,
    child_axis: np.ndarray,
) -> bool:
    """Test the same projective cut directly in the parent segment frame."""

    child_in_parent = relative_parent_child @ child_axis
    absolute_dot = abs(
        float(np.clip(np.asarray(parent_axis) @ child_in_parent, -1.0, 1.0))
    )
    return absolute_dot <= CUT_LOCUS_DOT_TOL
