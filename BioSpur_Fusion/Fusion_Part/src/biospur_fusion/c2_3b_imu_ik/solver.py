"""Framewise SO(3) orientation IK with soft functional-axis factors."""

from __future__ import annotations

import math
import time
from typing import Mapping, Sequence

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from .axis_factor import (
    at_projective_cut_parent,
    projector_huber_pseudo_residual_parent,
    projector_huber_pseudo_residual_world,
)
from .contracts import (
    ABSOLUTE_JACOBIAN_STEP_RAD,
    EDGE_ROWS,
    HINGES,
    SEGMENTS,
    SO3_LOG_CUT_TOL,
    AxisPair,
    EpisodeData,
    EpisodeSolution,
    FinalizedFrameState,
    FrameSolution,
    Profile,
)


class AbsoluteJacobianError(RuntimeError):
    """Named fail-closed error for the production absolute Jacobian."""


def _base_coordinates(
    measured: np.ndarray,
    segment_names: Sequence[str],
    edge_rows: Sequence[tuple[str, str, str]],
) -> tuple[np.ndarray, np.ndarray]:
    index = {name: position for position, name in enumerate(segment_names)}
    root = measured[index["pelvis"]].copy()
    relative = np.stack(
        [measured[index[parent]].T @ measured[index[child]] for _, parent, child in edge_rows]
    )
    return root, relative


def _apply_coordinates(
    root: np.ndarray,
    relative: np.ndarray,
    increment: np.ndarray,
    segment_names: Sequence[str],
    edge_rows: Sequence[tuple[str, str, str]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    index = {name: position for position, name in enumerate(segment_names)}
    delta = Rotation.from_rotvec(increment.reshape(-1, 3)).as_matrix()
    next_root = root @ delta[0]
    next_relative = relative @ delta[1:]
    world = np.empty((len(segment_names), 3, 3), dtype=np.float64)
    world[index["pelvis"]] = next_root
    for edge_index, (_, parent, child) in enumerate(edge_rows):
        world[index[child]] = world[index[parent]] @ next_relative[edge_index]
    return next_root, next_relative, world


def _axis_edge_indices(
    edge_rows: Sequence[tuple[str, str, str]],
    axes: Sequence[AxisPair],
) -> tuple[int, ...]:
    owner = {row: index for index, row in enumerate(edge_rows)}
    if len(owner) != len(edge_rows):
        raise ValueError("duplicate edge owner")
    indices = []
    for axis in axes:
        for value in (axis.parent_axis, axis.child_axis):
            value = np.asarray(value, dtype=np.float64)
            if (
                value.shape != (3,)
                or not np.all(np.isfinite(value))
                or not np.isclose(np.linalg.norm(value), 1.0, rtol=0.0, atol=1e-12)
            ):
                raise ValueError(f"finite unit axes required for {axis.name}")
        key = (axis.name, axis.parent, axis.child)
        if key not in owner:
            raise ValueError(f"axis has no exact edge owner: {key}")
        indices.append(owner[key])
    return tuple(indices)


def _residual(
    increment: np.ndarray,
    root: np.ndarray,
    relative: np.ndarray,
    measured: np.ndarray,
    segment_names: Sequence[str],
    edge_rows: Sequence[tuple[str, str, str]],
    axes: Sequence[AxisPair],
    axis_edge_indices: Sequence[int],
    weights: Mapping[str, float],
    axis_weight: float,
    axis_coordinates: str,
) -> np.ndarray:
    _, next_relative, world = _apply_coordinates(
        root, relative, increment, segment_names, edge_rows
    )
    index = {name: position for position, name in enumerate(segment_names)}
    differences = np.swapaxes(measured, -1, -2) @ world
    orientation_errors = Rotation.from_matrix(differences).as_rotvec()
    orientation_weights = np.sqrt(
        np.asarray([weights[name] for name in segment_names], dtype=np.float64)
    )
    rows = list(orientation_weights[:, None] * orientation_errors)
    if axis_coordinates == "parent_local":
        rows.extend(
            projector_huber_pseudo_residual_parent(
                axis.parent_axis,
                next_relative[edge_index],
                axis.child_axis,
                axis_weight,
            )
            for axis, edge_index in zip(axes, axis_edge_indices, strict=True)
        )
    elif axis_coordinates == "world_comparator":
        rows.extend(
            projector_huber_pseudo_residual_world(
                world[index[axis.parent]] @ axis.parent_axis,
                world[index[axis.child]] @ axis.child_axis,
                axis_weight,
            )
            for axis in axes
        )
    else:
        raise ValueError(f"unknown private axis coordinate mode: {axis_coordinates}")
    return np.concatenate(rows)


def _residual_log_angles(
    increment: np.ndarray,
    root: np.ndarray,
    relative: np.ndarray,
    measured: np.ndarray,
    segment_names: Sequence[str],
    edge_rows: Sequence[tuple[str, str, str]],
) -> np.ndarray:
    """Return principal observation angles before taking the rotation Log."""

    _, _, world = _apply_coordinates(
        root, relative, increment, segment_names, edge_rows
    )
    differences = np.swapaxes(measured, -1, -2) @ world
    cosines = np.clip(
        (np.trace(differences, axis1=-2, axis2=-1) - 1.0) / 2.0,
        -1.0,
        1.0,
    )
    return np.arccos(cosines)


def _absolute_central_jacobian_at_step(
    increment: np.ndarray,
    residual_args: tuple[object, ...],
    step_rad: float,
) -> np.ndarray:
    """Dense central Jacobian with one absolute angular step in every column."""

    value = np.asarray(increment, dtype=np.float64)
    step = float(step_rad)
    if value.ndim != 1 or not np.all(np.isfinite(value)):
        raise AbsoluteJacobianError("ABSOLUTE_JACOBIAN_NONFINITE_INPUT")
    if not np.isfinite(step) or step <= 0.0:
        raise AbsoluteJacobianError("ABSOLUTE_JACOBIAN_INVALID_STEP")
    root, relative, measured, segment_names, edge_rows = residual_args[:5]
    state_angles = _residual_log_angles(
        value,
        root,
        relative,
        measured,
        segment_names,
        edge_rows,
    )
    if not np.all(np.isfinite(state_angles)):
        raise AbsoluteJacobianError("ABSOLUTE_JACOBIAN_NONFINITE_LOG_AT_STATE")
    if np.any(math.pi - state_angles <= SO3_LOG_CUT_TOL):
        raise AbsoluteJacobianError("SO3_LOG_CUT_AT_STATE")
    columns = []
    expected_shape: tuple[int, ...] | None = None
    for coordinate in range(value.size):
        plus = value.copy()
        minus = value.copy()
        plus[coordinate] += step
        minus[coordinate] -= step
        if plus[coordinate] == value[coordinate] or minus[coordinate] == value[coordinate]:
            raise AbsoluteJacobianError(
                f"ABSOLUTE_JACOBIAN_UNREPRESENTABLE_COORDINATE_{coordinate}"
            )
        for label, point in (("PLUS", plus), ("MINUS", minus)):
            angles = _residual_log_angles(
                point,
                root,
                relative,
                measured,
                segment_names,
                edge_rows,
            )
            if not np.all(np.isfinite(angles)):
                raise AbsoluteJacobianError(
                    f"ABSOLUTE_JACOBIAN_NONFINITE_LOG_{label}_{coordinate}"
                )
            if np.any(math.pi - angles <= SO3_LOG_CUT_TOL):
                raise AbsoluteJacobianError(
                    f"SO3_LOG_CUT_{label}_COORDINATE_{coordinate}"
                )
        residual_plus = np.asarray(_residual(plus, *residual_args), dtype=np.float64)
        residual_minus = np.asarray(_residual(minus, *residual_args), dtype=np.float64)
        if expected_shape is None:
            expected_shape = residual_plus.shape
        if (
            residual_plus.shape != expected_shape
            or residual_minus.shape != expected_shape
            or not np.all(np.isfinite(residual_plus))
            or not np.all(np.isfinite(residual_minus))
        ):
            raise AbsoluteJacobianError(
                f"ABSOLUTE_JACOBIAN_INVALID_RESIDUAL_COORDINATE_{coordinate}"
            )
        columns.append((residual_plus - residual_minus) / (2.0 * step))
    jacobian = np.stack(columns, axis=1)
    if not np.all(np.isfinite(jacobian)):
        raise AbsoluteJacobianError("ABSOLUTE_JACOBIAN_NONFINITE_OUTPUT")
    return jacobian


def _absolute_central_jacobian(
    increment: np.ndarray,
    *residual_args: object,
) -> np.ndarray:
    return _absolute_central_jacobian_at_step(
        increment,
        residual_args,
        ABSOLUTE_JACOBIAN_STEP_RAD,
    )


def _relative_effective_steps(increment: np.ndarray) -> np.ndarray:
    """Independent reproduction of installed SciPy's bound relative-step rule."""

    value = np.asarray(increment, dtype=np.float64)
    sign = np.where(value >= 0.0, 1.0, -1.0)
    steps = 1e-6 * sign * np.abs(value)
    indistinguishable = (value + steps) - value == 0.0
    fallback = (
        np.finfo(np.float64).eps ** (1.0 / 3.0)
        * sign
        * np.maximum(1.0, np.abs(value))
    )
    return np.where(indistinguishable, fallback, steps)


def _has_cut_locus(
    relative: np.ndarray,
    axes: Sequence[AxisPair],
    axis_edge_indices: Sequence[int],
) -> bool:
    return any(
        at_projective_cut_parent(
            axis.parent_axis,
            relative[edge_index],
            axis.child_axis,
        )
        for axis, edge_index in zip(axes, axis_edge_indices, strict=True)
    )


def _finalize_frame_state(
    matrices: np.ndarray,
    *,
    cut_final: bool,
    pre_cut_finite: bool,
    pre_cut_proper: bool,
    pre_cut_converged: bool,
    message: str,
) -> FinalizedFrameState:
    """Return the complete public state after final-cut ownership."""

    if not cut_final:
        return FinalizedFrameState(
            matrices=matrices,
            cut_final=False,
            pre_cut_finite=pre_cut_finite,
            pre_cut_proper=pre_cut_proper,
            finite=pre_cut_finite,
            proper=pre_cut_proper,
            converged=pre_cut_converged,
            valid=pre_cut_converged,
            message=message,
        )
    return FinalizedFrameState(
        matrices=np.full_like(matrices, np.nan),
        cut_final=True,
        pre_cut_finite=pre_cut_finite,
        pre_cut_proper=pre_cut_proper,
        finite=False,
        proper=False,
        converged=False,
        valid=False,
        message=f"{message}; CUT_LOCUS_STATIONARY_AT_FINAL",
    )


def _cut_solution(
    measured: np.ndarray,
    message: str,
    *,
    derivative_mode: str,
) -> FrameSolution:
    return FrameSolution(
        matrices=np.full_like(measured, np.nan),
        valid=False,
        scipy_success=False,
        finite=False,
        proper=False,
        converged=False,
        message=message,
        cost=math.nan,
        optimality=math.nan,
        nfev=0,
        retractions=0,
        final_step_norm_rad=math.nan,
        wall_s=0.0,
        derivative_mode=derivative_mode,
        cut_start="MEASUREMENT_START" in message,
        cut_final="FINAL" in message,
    )


def _jacobian_failure_solution(
    measured: np.ndarray,
    message: str,
    *,
    wall_s: float,
    derivative_mode: str,
    nfev: int,
    njev: int,
) -> FrameSolution:
    return FrameSolution(
        matrices=np.full_like(measured, np.nan),
        valid=False,
        scipy_success=False,
        finite=False,
        proper=False,
        converged=False,
        message=message,
        cost=math.nan,
        optimality=math.nan,
        nfev=nfev,
        retractions=0,
        final_step_norm_rad=math.nan,
        wall_s=wall_s,
        njev=njev,
        derivative_mode=derivative_mode,
    )


def _solve_tree_frame_impl(
    measured: np.ndarray,
    *,
    segment_names: Sequence[str],
    edge_rows: Sequence[tuple[str, str, str]],
    axes: Sequence[AxisPair],
    weights: Mapping[str, float],
    axis_weight: float,
    initial_increment: np.ndarray | None = None,
    frame_wall_limit_s: float = 0.5,
    collect_diagnostics: bool = False,
    collect_trace_state: bool = False,
    axis_coordinates: str,
    derivative_mode: str,
) -> FrameSolution:
    measured = np.asarray(measured, dtype=np.float64)
    expected_shape = (len(segment_names), 3, 3)
    if measured.shape != expected_shape:
        raise ValueError(f"measured shape must be {expected_shape}")
    root, relative = _base_coordinates(measured, segment_names, edge_rows)
    if not np.isfinite(axis_weight) or axis_weight < 0.0:
        raise ValueError("finite nonnegative axis weight required")
    axis_edge_indices = _axis_edge_indices(edge_rows, axes)
    if axis_weight > 0.0 and _has_cut_locus(relative, axes, axis_edge_indices):
        return _cut_solution(
            measured,
            "CUT_LOCUS_STATIONARY_AT_MEASUREMENT_START",
            derivative_mode=derivative_mode,
        )
    dimension = 3 * (1 + len(edge_rows))
    first_start = (
        np.zeros(dimension, dtype=np.float64)
        if initial_increment is None
        else np.asarray(initial_increment, dtype=np.float64).reshape(dimension)
    )
    started = time.monotonic()
    total_nfev = 0
    total_njev = 0
    last_result = None
    final_step = math.inf
    world = measured.copy()
    retractions = 0
    orientation_cost = math.nan
    axis_cost = math.nan
    trace: list[dict[str, object]] = []

    for retraction in range(1, 4):
        x0 = first_start if retraction == 1 else np.zeros(dimension, dtype=np.float64)
        incoming_root = root.copy()
        incoming_relative = relative.copy()
        residual_args = (
            root,
            relative,
            measured,
            segment_names,
            edge_rows,
            axes,
            axis_edge_indices,
            weights,
            axis_weight,
            axis_coordinates,
        )
        solver_arguments = {
            "args": residual_args,
            "method": "trf",
            "bounds": (-np.inf, np.inf),
            "x_scale": 1.0,
            "loss": "linear",
            "f_scale": 1.0,
            "tr_solver": "exact",
            "tr_options": {},
            "ftol": 1e-10,
            "xtol": 1e-10,
            "gtol": 1e-10,
            "max_nfev": 80,
            "jac_sparsity": None,
            "verbose": 0,
            "callback": None,
            "workers": None,
        }
        try:
            if derivative_mode == "absolute_central_1e-6":
                result = least_squares(
                    _residual,
                    x0,
                    jac=_absolute_central_jacobian,
                    **solver_arguments,
                )
            elif derivative_mode == "relative_scipy_3point_1e-6":
                result = least_squares(
                    _residual,
                    x0,
                    jac="3-point",
                    diff_step=1e-6,
                    **solver_arguments,
                )
            else:
                raise ValueError(f"unknown derivative mode: {derivative_mode}")
        except AbsoluteJacobianError as error:
            return _jacobian_failure_solution(
                measured,
                str(error),
                wall_s=time.monotonic() - started,
                derivative_mode=derivative_mode,
                nfev=total_nfev,
                njev=total_njev,
            )
        total_nfev += int(result.nfev)
        total_njev += int(result.njev or 0)
        root, relative, world = _apply_coordinates(
            root, relative, result.x, segment_names, edge_rows
        )
        final_step = float(
            max(np.linalg.norm(result.x[offset : offset + 3]) for offset in range(0, dimension, 3))
        )
        last_result = result
        retractions = retraction
        residual = np.asarray(result.fun, dtype=np.float64)
        orientation_rows = 3 * len(segment_names)
        orientation_residual = residual[:orientation_rows]
        axis_residual = residual[orientation_rows:]
        orientation_cost = 0.5 * float(orientation_residual @ orientation_residual)
        axis_cost = 0.5 * float(axis_residual @ axis_residual)
        if collect_diagnostics:
            jacobian = np.asarray(result.jac, dtype=np.float64)
            orientation_jacobian = jacobian[:orientation_rows]
            axis_jacobian = jacobian[orientation_rows:]
            trace.append(
                dict(
                    {
                    "retraction": retraction,
                    "nfev": int(result.nfev),
                    "total_cost": float(result.cost),
                    "orientation_cost": orientation_cost,
                    "axis_cost": axis_cost,
                    "orientation_gradient_norm": float(
                        np.linalg.norm(orientation_jacobian.T @ orientation_residual)
                    ),
                    "axis_gradient_norm": float(
                        np.linalg.norm(axis_jacobian.T @ axis_residual)
                    ),
                    "orientation_jacobian_frobenius": float(
                        np.linalg.norm(orientation_jacobian)
                    ),
                    "axis_jacobian_frobenius": float(np.linalg.norm(axis_jacobian)),
                    "stacked_jacobian_condition": float(np.linalg.cond(jacobian)),
                    "optimality": float(result.optimality),
                    "step_norm_rad": final_step,
                    "scipy_success": bool(result.success),
                    "message": str(result.message),
                    },
                    **(
                        {
                            "incoming_root": incoming_root,
                            "incoming_relative": incoming_relative,
                            "increment": np.asarray(result.x, dtype=np.float64),
                            "residual": residual,
                            "solver_jacobian": jacobian,
                        }
                        if collect_trace_state
                        else {}
                    ),
                )
            )
        if final_step <= 1e-8:
            break

    wall_s = time.monotonic() - started
    assert last_result is not None
    determinant_error = np.max(np.abs(np.linalg.det(world) - 1.0))
    orthogonality_error = np.max(
        np.linalg.norm(np.swapaxes(world, -1, -2) @ world - np.eye(3), axis=(-2, -1))
    )
    pre_cut_finite = bool(
        np.all(np.isfinite(world))
        and np.isfinite(last_result.cost)
        and np.all(np.isfinite(last_result.fun))
        and np.all(np.isfinite(np.asarray(last_result.jac)))
        and np.isfinite(last_result.optimality)
    )
    pre_cut_proper = bool(
        determinant_error <= 1e-10 and orthogonality_error <= 1e-10
    )
    cut_final = bool(
        axis_weight > 0.0 and _has_cut_locus(relative, axes, axis_edge_indices)
    )
    pre_cut_converged = bool(
        last_result.success
        and pre_cut_finite
        and pre_cut_proper
        and final_step <= 1e-8
        and float(last_result.optimality) <= 1e-8
        and wall_s <= frame_wall_limit_s
    )
    message = str(last_result.message)
    if wall_s > frame_wall_limit_s:
        message = f"FRAME_WALL_LIMIT_EXCEEDED: {wall_s:.9f}s; {message}"
    finalized = _finalize_frame_state(
        world,
        cut_final=cut_final,
        pre_cut_finite=pre_cut_finite,
        pre_cut_proper=pre_cut_proper,
        pre_cut_converged=pre_cut_converged,
        message=message,
    )
    if derivative_mode == "absolute_central_1e-6":
        effective_steps = np.full(dimension, ABSOLUTE_JACOBIAN_STEP_RAD)
        jacobian_residual_evaluations = 2 * dimension * total_njev
    else:
        effective_steps = np.abs(_relative_effective_steps(last_result.x))
        jacobian_residual_evaluations = 0
    return FrameSolution(
        matrices=finalized.matrices,
        valid=finalized.valid,
        scipy_success=bool(last_result.success),
        finite=finalized.finite,
        proper=finalized.proper,
        converged=finalized.converged,
        message=finalized.message,
        cost=float(last_result.cost),
        optimality=float(last_result.optimality),
        nfev=total_nfev,
        retractions=retractions,
        final_step_norm_rad=final_step,
        wall_s=wall_s,
        njev=total_njev,
        derivative_mode=derivative_mode,
        jacobian_residual_evaluations=jacobian_residual_evaluations,
        effective_step_min_rad=float(np.min(effective_steps)),
        effective_step_max_rad=float(np.max(effective_steps)),
        orientation_cost=orientation_cost,
        axis_cost=axis_cost,
        pre_cut_finite=pre_cut_finite,
        pre_cut_proper=pre_cut_proper,
        cut_final=cut_final,
        retraction_trace=tuple(trace),
    )


def solve_tree_frame(
    measured: np.ndarray,
    *,
    segment_names: Sequence[str],
    edge_rows: Sequence[tuple[str, str, str]],
    axes: Sequence[AxisPair],
    weights: Mapping[str, float],
    axis_weight: float,
    initial_increment: np.ndarray | None = None,
    frame_wall_limit_s: float = 0.5,
    collect_diagnostics: bool = False,
    collect_trace_state: bool = False,
) -> FrameSolution:
    return _solve_tree_frame_impl(
        measured,
        segment_names=segment_names,
        edge_rows=edge_rows,
        axes=axes,
        weights=weights,
        axis_weight=axis_weight,
        initial_increment=initial_increment,
        frame_wall_limit_s=frame_wall_limit_s,
        collect_diagnostics=collect_diagnostics,
        collect_trace_state=collect_trace_state,
        axis_coordinates="parent_local",
        derivative_mode="absolute_central_1e-6",
    )


def _solve_tree_frame_relative_comparator(
    measured: np.ndarray,
    *,
    segment_names: Sequence[str],
    edge_rows: Sequence[tuple[str, str, str]],
    axes: Sequence[AxisPair],
    weights: Mapping[str, float],
    axis_weight: float,
    initial_increment: np.ndarray | None = None,
    frame_wall_limit_s: float = 0.5,
    collect_diagnostics: bool = False,
    collect_trace_state: bool = False,
) -> FrameSolution:
    """Private immutable parent-local comparator for ABS03/ABS04."""

    return _solve_tree_frame_impl(
        measured,
        segment_names=segment_names,
        edge_rows=edge_rows,
        axes=axes,
        weights=weights,
        axis_weight=axis_weight,
        initial_increment=initial_increment,
        frame_wall_limit_s=frame_wall_limit_s,
        collect_diagnostics=collect_diagnostics,
        collect_trace_state=collect_trace_state,
        axis_coordinates="parent_local",
        derivative_mode="relative_scipy_3point_1e-6",
    )


def _solve_tree_frame_world_comparator(
    measured: np.ndarray,
    *,
    segment_names: Sequence[str],
    edge_rows: Sequence[tuple[str, str, str]],
    axes: Sequence[AxisPair],
    weights: Mapping[str, float],
    axis_weight: float,
    initial_increment: np.ndarray | None = None,
    frame_wall_limit_s: float = 0.5,
    collect_diagnostics: bool = False,
) -> FrameSolution:
    """Private causal comparator; never exposed through a candidate entry point."""

    return _solve_tree_frame_impl(
        measured,
        segment_names=segment_names,
        edge_rows=edge_rows,
        axes=axes,
        weights=weights,
        axis_weight=axis_weight,
        initial_increment=initial_increment,
        frame_wall_limit_s=frame_wall_limit_s,
        collect_diagnostics=collect_diagnostics,
        collect_trace_state=False,
        axis_coordinates="world_comparator",
        derivative_mode="relative_scipy_3point_1e-6",
    )


def solve_frame(
    measured: np.ndarray,
    axes: Mapping[str, AxisPair],
    profile: Profile,
    *,
    collect_diagnostics: bool = False,
) -> FrameSolution:
    return solve_tree_frame(
        measured,
        segment_names=SEGMENTS,
        edge_rows=EDGE_ROWS,
        axes=tuple(axes[name] for name in HINGES),
        weights=profile.weights(),
        axis_weight=profile.axis_weight,
        collect_diagnostics=collect_diagnostics,
    )


def solve_episode(
    episode: EpisodeData,
    axes: Mapping[str, AxisPair],
    profile: Profile,
) -> EpisodeSolution:
    count = len(episode.time_s)
    matrices = np.full_like(episode.matrices, np.nan)
    valid = np.zeros(count, dtype=bool)
    walls = np.zeros(count, dtype=np.float64)
    nfev = np.zeros(count, dtype=np.int32)
    costs = np.full(count, np.nan, dtype=np.float64)
    optimality = np.full(count, np.nan, dtype=np.float64)
    steps = np.full(count, np.nan, dtype=np.float64)
    messages: list[str] = []
    started = time.monotonic()
    for frame in range(count):
        if not episode.full_body_valid[frame]:
            messages.append("INPUT_MASKED")
            continue
        result = solve_frame(episode.matrices[frame], axes, profile)
        matrices[frame] = result.matrices
        valid[frame] = result.valid
        walls[frame] = result.wall_s
        nfev[frame] = result.nfev
        costs[frame] = result.cost
        optimality[frame] = result.optimality
        steps[frame] = result.final_step_norm_rad
        messages.append(result.message)
    wall = time.monotonic() - started
    if wall > 120.0:
        valid[:] = False
        messages = [f"EPISODE_PROFILE_WALL_LIMIT_EXCEEDED: {wall:.9f}s"] * count
    return EpisodeSolution(
        episode=episode.key,
        profile=profile.name,
        matrices=matrices,
        valid=valid,
        frame_wall_s=walls,
        nfev=nfev,
        costs=costs,
        optimality=optimality,
        final_step_norm_rad=steps,
        messages=tuple(messages),
    )
