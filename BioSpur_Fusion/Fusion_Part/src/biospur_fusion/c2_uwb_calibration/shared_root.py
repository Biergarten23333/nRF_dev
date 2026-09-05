"""Joint raw-range solve for one shared C2 body root.

This module owns only the numerical shared-root mechanism.  The caller owns
the source and scientific validity of tag offsets, range calibration, link
selection, and time alignment.  In particular, display-proxy FK offsets must
not be relabelled as measured antenna phase centres.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from scipy.optimize import least_squares


@dataclass(frozen=True)
class SharedRangeLink:
    node: str
    anchor: int
    range_m: float
    tag_offset_world_m: np.ndarray
    link_dt_s: float
    sigma_m: float
    facing_score: float | None = None


@dataclass(frozen=True)
class SharedRootResult:
    root_position_m: np.ndarray
    success: bool
    reason: str
    residuals_m: np.ndarray
    standardized_residuals: np.ndarray
    anchors_used: tuple[int, ...]
    nodes_used: tuple[str, ...]
    rank: int
    condition: float
    nfev: int
    cost: float


@dataclass(frozen=True)
class SharedRootUncertainty:
    covariance_m2: np.ndarray
    leave_node_roots_m: np.ndarray
    successful_leave_node_solves: int
    expected_leave_node_solves: int
    minimum_std_m: float


def _failure(initial_root_m: np.ndarray, reason: str) -> SharedRootResult:
    return SharedRootResult(
        root_position_m=np.asarray(initial_root_m, dtype=float).reshape(3).copy(),
        success=False,
        reason=reason,
        residuals_m=np.empty(0, dtype=float),
        standardized_residuals=np.empty(0, dtype=float),
        anchors_used=(),
        nodes_used=(),
        rank=0,
        condition=math.inf,
        nfev=0,
        cost=math.inf,
    )


def evaluate_shared_root_residuals(
    links: Sequence[SharedRangeLink],
    *,
    anchors_m: Mapping[int, np.ndarray] | np.ndarray,
    root_position_m: np.ndarray,
    root_velocity_mps: np.ndarray | None = None,
) -> np.ndarray:
    """Evaluate physical range residuals without fitting the supplied links."""

    root = np.asarray(root_position_m, dtype=float).reshape(3)
    velocity = (
        np.zeros(3, dtype=float)
        if root_velocity_mps is None
        else np.asarray(root_velocity_mps, dtype=float).reshape(3)
    )
    if not np.all(np.isfinite(root)) or not np.all(np.isfinite(velocity)):
        raise ValueError("root and velocity must be finite")
    try:
        anchor_rows = np.asarray(
            [anchors_m[int(link.anchor)] for link in links], dtype=float
        )
    except (KeyError, IndexError) as exc:
        raise ValueError("link refers to an unavailable anchor") from exc
    offsets = np.asarray([link.tag_offset_world_m for link in links], dtype=float)
    ranges = np.asarray([link.range_m for link in links], dtype=float)
    link_dt = np.asarray([link.link_dt_s for link in links], dtype=float)
    if not len(links):
        return np.empty(0, dtype=float)
    if anchor_rows.shape != (len(links), 3) or offsets.shape != (len(links), 3):
        raise ValueError("anchors and tag offsets must be 3-D")
    if not (
        np.all(np.isfinite(anchor_rows))
        and np.all(np.isfinite(offsets))
        and np.all(np.isfinite(ranges))
        and np.all(np.isfinite(link_dt))
        and np.all(ranges > 0.0)
    ):
        raise ValueError("range links must be finite with positive range")
    tag = root[None, :] + offsets + link_dt[:, None] * velocity
    return ranges - np.linalg.norm(anchor_rows - tag, axis=1)


def solve_shared_root(
    links: Sequence[SharedRangeLink],
    *,
    anchors_m: Mapping[int, np.ndarray] | np.ndarray,
    initial_root_m: np.ndarray,
    root_velocity_mps: np.ndarray | None = None,
    maximum_condition: float = 1e8,
    maximum_nfev: int = 50,
) -> SharedRootResult:
    """Solve one 3-D root against ranges from multiple body-worn tags.

    Each tag position is ``root + tag_offset + velocity * link_dt``.  The
    velocity is supplied by the causal outer tracker and is not estimated in
    this instantaneous solve.  A Huber loss limits, but does not magically
    identify, positive NLOS errors.
    """

    initial = np.asarray(initial_root_m, dtype=float).reshape(3)
    velocity = (
        np.zeros(3, dtype=float)
        if root_velocity_mps is None
        else np.asarray(root_velocity_mps, dtype=float).reshape(3)
    )
    if not np.all(np.isfinite(initial)) or not np.all(np.isfinite(velocity)):
        raise ValueError("initial root and velocity must be finite")
    if len(links) < 4:
        return _failure(initial, "FEWER_THAN_FOUR_LINKS")

    identities = [(str(link.node), int(link.anchor)) for link in links]
    if len(set(identities)) != len(identities):
        return _failure(initial, "DUPLICATE_NODE_ANCHOR_LINK")
    try:
        if isinstance(anchors_m, np.ndarray):
            anchor_rows = np.asarray(
                [anchors_m[int(link.anchor)] for link in links], dtype=float
            )
        else:
            anchor_rows = np.asarray(
                [anchors_m[int(link.anchor)] for link in links], dtype=float
            )
    except (KeyError, IndexError) as exc:
        raise ValueError("link refers to an unavailable anchor") from exc
    offsets = np.asarray([link.tag_offset_world_m for link in links], dtype=float)
    ranges = np.asarray([link.range_m for link in links], dtype=float)
    link_dt = np.asarray([link.link_dt_s for link in links], dtype=float)
    sigma = np.asarray([link.sigma_m for link in links], dtype=float)
    if anchor_rows.shape != (len(links), 3) or offsets.shape != (len(links), 3):
        raise ValueError("anchors and tag offsets must be 3-D")
    if not (
        np.all(np.isfinite(anchor_rows))
        and np.all(np.isfinite(offsets))
        and np.all(np.isfinite(ranges))
        and np.all(np.isfinite(link_dt))
        and np.all(np.isfinite(sigma))
        and np.all(ranges > 0.0)
        and np.all(sigma > 0.0)
    ):
        raise ValueError("range links must be finite with positive range and sigma")

    timed_offsets = offsets + link_dt[:, None] * velocity

    def physical_residual(root: np.ndarray) -> np.ndarray:
        tag = root[None, :] + timed_offsets
        return ranges - np.linalg.norm(anchor_rows - tag, axis=1)

    def residual(root: np.ndarray) -> np.ndarray:
        return physical_residual(root) / sigma

    def jacobian(root: np.ndarray) -> np.ndarray:
        tag = root[None, :] + timed_offsets
        delta = anchor_rows - tag
        distance = np.linalg.norm(delta, axis=1)
        if np.any(distance <= np.finfo(float).eps):
            raise ValueError("range derivative is singular at an anchor")
        return delta / distance[:, None] / sigma[:, None]

    try:
        optimized = least_squares(
            residual,
            initial,
            jac=jacobian,
            loss="huber",
            f_scale=1.5,
            x_scale="jac",
            max_nfev=int(maximum_nfev),
        )
        root = np.asarray(optimized.x, dtype=float)
        standardized = residual(root)
        physical = physical_residual(root)
        geometry = jacobian(root) * sigma[:, None]
        singular = np.linalg.svd(geometry, compute_uv=False)
    except (ValueError, FloatingPointError, np.linalg.LinAlgError):
        return _failure(initial, "NUMERICAL_FAILURE")

    tolerance = max(geometry.shape) * np.finfo(float).eps * singular[0]
    rank = int(np.sum(singular > tolerance))
    condition = (
        float(np.linalg.cond(geometry.T @ geometry)) if rank == 3 else math.inf
    )
    success = bool(
        optimized.success
        and np.all(np.isfinite(root))
        and rank == 3
        and math.isfinite(condition)
        and condition <= maximum_condition
    )
    reason = "ACCEPTED" if success else (
        "REJECT_GEOMETRY" if rank < 3 or condition > maximum_condition
        else "OPTIMIZER_FAILURE"
    )
    return SharedRootResult(
        root_position_m=root,
        success=success,
        reason=reason,
        residuals_m=physical,
        standardized_residuals=standardized,
        anchors_used=tuple(sorted({int(link.anchor) for link in links})),
        nodes_used=tuple(sorted({str(link.node) for link in links})),
        rank=rank,
        condition=condition,
        nfev=int(optimized.nfev),
        cost=float(optimized.cost),
    )


def estimate_leave_node_uncertainty(
    links: Sequence[SharedRangeLink],
    *,
    anchors_m: Mapping[int, np.ndarray] | np.ndarray,
    initial_root_m: np.ndarray,
    root_velocity_mps: np.ndarray | None = None,
    minimum_std_m: float = 0.12,
) -> SharedRootUncertainty:
    """Estimate diagnostic root uncertainty by deterministic node jackknife.

    Each body node is excluded once, so a bad node cannot hide behind its own
    ranges when uncertainty is assessed.  The covariance is the conventional
    delete-one jackknife covariance plus an explicit positive floor.  It is a
    runtime quality estimate, not a substitute for the pending Vicon-derived
    scientific measurement covariance.
    """

    if not np.isfinite(minimum_std_m) or minimum_std_m <= 0.0:
        raise ValueError("minimum root uncertainty must be finite and positive")
    nodes = tuple(sorted({str(link.node) for link in links}))
    roots = []
    for node in nodes:
        result = solve_shared_root(
            [link for link in links if link.node != node],
            anchors_m=anchors_m,
            initial_root_m=initial_root_m,
            root_velocity_mps=root_velocity_mps,
        )
        if result.success:
            roots.append(result.root_position_m)
    values = np.asarray(roots, dtype=float)
    if len(values) >= 2:
        centre = np.mean(values, axis=0)
        delta = values - centre
        covariance = (len(values) - 1.0) / len(values) * (delta.T @ delta)
    else:
        covariance = np.zeros((3, 3), dtype=float)
    covariance = 0.5 * (covariance + covariance.T)
    covariance += np.eye(3) * float(minimum_std_m) ** 2
    eigenvalues = np.linalg.eigvalsh(covariance)
    if eigenvalues[0] <= 0.0:
        covariance += np.eye(3) * (np.finfo(float).eps - eigenvalues[0])
    np.linalg.cholesky(covariance)
    return SharedRootUncertainty(
        covariance_m2=covariance,
        leave_node_roots_m=values,
        successful_leave_node_solves=len(values),
        expected_leave_node_solves=len(nodes),
        minimum_std_m=float(minimum_std_m),
    )
