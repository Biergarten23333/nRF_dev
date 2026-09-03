"""Preregistered numerical checks for the parent-local projector pivot."""

from __future__ import annotations

from collections import Counter
import math
from typing import Any, Callable, Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from .axis_factor import (
    at_projective_cut,
    at_projective_cut_parent,
    projector_huber_pseudo_residual_parent,
    projector_huber_pseudo_residual_world,
)
from .contracts import (
    CENTRAL_PROFILE,
    CUT_LOCUS_DOT_TOL,
    EDGE_ROWS,
    HINGES,
    PROFILES,
    SEGMENTS,
    AxisPair,
    FrameSolution,
    Profile,
)
from .solver import (
    _apply_coordinates,
    _axis_edge_indices,
    _base_coordinates,
    _finalize_frame_state,
    _residual,
    _solve_tree_frame_relative_comparator,
    _solve_tree_frame_world_comparator,
)
from .synthetic_generator import full_circle_fixture, make_case


EQV_SEED = 320260902
EQV_STATE_COUNT = 512
EQV_WEIGHTS = (0.25, 0.5, 1.0)
EQV_STEPS = (1e-6, 5e-7)
EQV_TOL = 1e-12
REFINEMENT_SCALE = 1e-5
WITNESS_FRAMES = (
    ("SYN01_DISTAL_TRANSVERSE_NOISE", None, 100),
    ("SYN02_ANISOTROPIC_MOUNT_DRIFT", None, 100),
    ("SYN03_NONIDEAL_HUMAN_HINGE", None, 295),
    ("SYN04_BILATERAL_VARIABILITY", None, 100),
    ("SYN05_MOUNT_STEP", None, 200),
    ("SYN09_MONTE_CARLO_NOISE", 31100, 100),
)


def _unit(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(value))
    if value.ndim != 1 or not np.isfinite(norm) or norm == 0.0:
        raise ValueError("EQV fixture normalization failed")
    return value / norm


def _case_axes(case: Any) -> tuple[AxisPair, ...]:
    by_name = {
        axis.name: AxisPair(
            axis.name,
            axis.parent,
            axis.child,
            axis.parent_axis,
            axis.child_axis,
        )
        for axis in case.estimator_axes
    }
    return tuple(by_name[name] for name in HINGES)


def _draw_random_states() -> list[tuple[np.ndarray, ...]]:
    generator = np.random.Generator(np.random.PCG64(EQV_SEED))
    states = []
    for _ in range(EQV_STATE_COUNT):
        parent = Rotation.from_quat(_unit(generator.standard_normal(4))).as_matrix()
        child = Rotation.from_quat(_unit(generator.standard_normal(4))).as_matrix()
        common = Rotation.from_quat(_unit(generator.standard_normal(4))).as_matrix()
        parent_axis = _unit(generator.standard_normal(3))
        child_axis = _unit(generator.standard_normal(3))
        states.append((parent, child, common, parent_axis, child_axis))
    return states


def _fixed_states() -> list[tuple[str, np.ndarray, ...]]:
    measured, synthetic_axes, _ = full_circle_fixture()
    fixture_axis = synthetic_axes[0]
    states: list[tuple[str, np.ndarray, ...]] = [
        (
            "SYN08_aligned_truth",
            measured[0],
            measured[1],
            fixture_axis.parent_axis,
            fixture_axis.child_axis,
        ),
        (
            "SYN08_antiparallel_child_axis_variant",
            measured[0],
            measured[1],
            fixture_axis.parent_axis,
            -fixture_axis.child_axis,
        ),
        (
            "CUT01_exact_orthogonal",
            np.eye(3),
            np.eye(3),
            np.array([1.0, 0.0, 0.0]),
            np.array([0.0, 1.0, 0.0]),
        ),
    ]
    case = make_case("SYN01_DISTAL_TRANSVERSE_NOISE")
    index = {name: position for position, name in enumerate(SEGMENTS)}
    for axis in _case_axes(case):
        states.append(
            (
                f"SYN01_frame100_{axis.name}",
                case.measured[100, index[axis.parent]],
                case.measured[100, index[axis.child]],
                axis.parent_axis,
                axis.child_axis,
            )
        )
    return states


def _equivalence_metrics(
    parent: np.ndarray,
    child: np.ndarray,
    common: np.ndarray,
    parent_axis: np.ndarray,
    child_axis: np.ndarray,
    weight: float,
    *,
    check_common_rotation: bool,
) -> dict[str, Any]:
    relative = parent.T @ child
    parent_world_axis = parent @ parent_axis
    child_world_axis = child @ child_axis
    error_world = (
        np.outer(parent_world_axis, parent_world_axis)
        - np.outer(child_world_axis, child_world_axis)
    ) / math.sqrt(2.0)
    child_in_parent = relative @ child_axis
    error_parent = (
        np.outer(parent_axis, parent_axis) - np.outer(child_in_parent, child_in_parent)
    ) / math.sqrt(2.0)
    world_residual = projector_huber_pseudo_residual_world(
        parent_world_axis, child_world_axis, weight
    )
    parent_residual = projector_huber_pseudo_residual_parent(
        parent_axis, relative, child_axis, weight
    )
    flipped = (
        projector_huber_pseudo_residual_parent(-parent_axis, relative, child_axis, weight),
        projector_huber_pseudo_residual_parent(parent_axis, relative, -child_axis, weight),
        projector_huber_pseudo_residual_parent(-parent_axis, relative, -child_axis, weight),
    )
    common_world_residual = None
    common_parent_residual = None
    finite_values = [
        error_world.reshape(-1),
        error_parent.reshape(-1),
        world_residual,
        parent_residual,
        *(value for value in flipped),
    ]
    if check_common_rotation:
        common_parent = common @ parent
        common_child = common @ child
        common_relative = common_parent.T @ common_child
        common_world_residual = projector_huber_pseudo_residual_world(
            common_parent @ parent_axis, common_child @ child_axis, weight
        )
        common_parent_residual = projector_huber_pseudo_residual_parent(
            parent_axis, common_relative, child_axis, weight
        )
        finite_values.extend((common_world_residual, common_parent_residual))
    values = np.concatenate(finite_values)
    return {
        "finite": bool(np.all(np.isfinite(values))),
        "conjugacy_frobenius": float(
            np.linalg.norm(error_world - parent @ error_parent @ parent.T)
        ),
        "z_difference": abs(float(np.linalg.norm(error_world) - np.linalg.norm(error_parent))),
        "residual_norm_difference": abs(
            float(np.linalg.norm(world_residual) - np.linalg.norm(parent_residual))
        ),
        "scalar_cost_difference": abs(
            0.5 * float(world_residual @ world_residual)
            - 0.5 * float(parent_residual @ parent_residual)
        ),
        "cut_flags_identical": bool(
            at_projective_cut(parent, child, parent_axis, child_axis)
            == at_projective_cut_parent(parent_axis, relative, child_axis)
        ),
        "single_axis_flip_component_difference": max(
            float(np.max(np.abs(parent_residual - value))) for value in flipped
        ),
        "parent_local_common_rotation_component_difference": (
            float(np.max(np.abs(parent_residual - common_parent_residual)))
            if common_parent_residual is not None
            else None
        ),
        "world_common_rotation_scalar_cost_difference": (
            abs(
                0.5 * float(world_residual @ world_residual)
                - 0.5 * float(common_world_residual @ common_world_residual)
            )
            if common_world_residual is not None
            else None
        ),
    }


def validate_eqv01() -> tuple[dict[str, Any], list[tuple[np.ndarray, ...]]]:
    random_states = _draw_random_states()
    rows = []
    for name, parent, child, parent_axis, child_axis in _fixed_states():
        for weight in EQV_WEIGHTS:
            rows.append(
                {
                    "fixture": name,
                    "weight": weight,
                    **_equivalence_metrics(
                        parent,
                        child,
                        np.eye(3),
                        parent_axis,
                        child_axis,
                        weight,
                        check_common_rotation=False,
                    ),
                }
            )
    random_rows = []
    for state_index, state in enumerate(random_states):
        for weight in EQV_WEIGHTS:
            random_rows.append(
                {
                    "state_index": state_index,
                    "weight": weight,
                    **_equivalence_metrics(
                        *state, weight, check_common_rotation=True
                    ),
                }
            )
    combined = rows + random_rows
    maxima = {
        name: max(float(row[name]) for row in combined)
        for name in (
            "conjugacy_frobenius",
            "z_difference",
            "residual_norm_difference",
            "scalar_cost_difference",
            "single_axis_flip_component_difference",
        )
    }
    maxima.update(
        {
            name: max(float(row[name]) for row in random_rows)
            for name in (
                "parent_local_common_rotation_component_difference",
                "world_common_rotation_scalar_cost_difference",
            )
        }
    )
    checks = {
        "fixed_comparison_count": len(rows) == 21,
        "fixed_common_rotation_acceptance_removed": all(
            row["parent_local_common_rotation_component_difference"] is None
            and row["world_common_rotation_scalar_cost_difference"] is None
            for row in rows
        ),
        "random_comparison_count": len(random_rows) == 1536,
        "all_finite": all(bool(row["finite"]) for row in combined),
        "all_cut_flags_identical": all(bool(row["cut_flags_identical"]) for row in combined),
        **{f"{name}_within_1e-12": value <= EQV_TOL for name, value in maxima.items()},
    }
    return (
        {
            "case": "EQV01_POINTWISE_EQUIVALENCE",
            "passed": bool(all(checks.values())),
            "checks": checks,
            "values": {
                "seed": EQV_SEED,
                "fixed_comparison_count": len(rows),
                "random_state_count": len(random_states),
                "random_comparison_count": len(random_rows),
                "maxima": maxima,
                "fixed": rows,
            },
        },
        random_states,
    )


def _central_jacobian(function: Callable[[np.ndarray], np.ndarray], dimension: int, step: float) -> np.ndarray:
    columns = []
    for coordinate in range(dimension):
        offset = np.zeros(dimension, dtype=np.float64)
        offset[coordinate] = step
        columns.append((function(offset) - function(-offset)) / (2.0 * step))
    return np.stack(columns, axis=1)


def _full_body_residual_functions() -> tuple[Callable[[np.ndarray], np.ndarray], Callable[[np.ndarray], np.ndarray]]:
    case = make_case("SYN01_DISTAL_TRANSVERSE_NOISE")
    measured = case.measured[100]
    axes = _case_axes(case)
    root, relative = _base_coordinates(measured, SEGMENTS, EDGE_ROWS)
    indices = _axis_edge_indices(EDGE_ROWS, axes)
    args = (
        root,
        relative,
        measured,
        SEGMENTS,
        EDGE_ROWS,
        axes,
        indices,
        CENTRAL_PROFILE.weights(),
        CENTRAL_PROFILE.axis_weight,
    )
    return (
        lambda increment: _residual(increment, *args, "world_comparator"),
        lambda increment: _residual(increment, *args, "parent_local"),
    )


def validate_eqv02() -> dict[str, Any]:
    world_function, local_function = _full_body_residual_functions()
    orientation_rows = 3 * len(SEGMENTS)
    world_jacobians = [_central_jacobian(world_function, 30, step) for step in EQV_STEPS]
    local_jacobians = [_central_jacobian(local_function, 30, step) for step in EQV_STEPS]
    world_axis = [value[orientation_rows:] for value in world_jacobians]
    local_axis = [value[orientation_rows:] for value in local_jacobians]
    local_root = [value[:, :3] for value in local_axis]
    root_refinement = float(np.linalg.norm(local_root[0] - local_root[1]))
    refinement_limit = REFINEMENT_SCALE * max(1.0, float(np.linalg.norm(local_root[1])))
    finite = all(
        np.all(np.isfinite(value))
        for value in world_jacobians + local_jacobians
    )
    checks = {
        "all_finite": bool(finite),
        "root_h_frobenius": float(np.linalg.norm(local_root[0])) <= 1e-8,
        "root_half_frobenius": float(np.linalg.norm(local_root[1])) <= 1e-8,
        "root_refinement": root_refinement <= refinement_limit,
    }
    values = {
        "world_axis_root_jacobian_frobenius": [
            float(np.linalg.norm(value[:, :3])) for value in world_axis
        ],
        "parent_local_axis_root_jacobian_frobenius": [
            float(np.linalg.norm(value)) for value in local_root
        ],
        "world_full_jacobian_frobenius": [float(np.linalg.norm(value)) for value in world_jacobians],
        "parent_local_full_jacobian_frobenius": [float(np.linalg.norm(value)) for value in local_jacobians],
        "world_full_condition": [float(np.linalg.cond(value)) for value in world_jacobians],
        "parent_local_full_condition": [float(np.linalg.cond(value)) for value in local_jacobians],
        "differences": {
            "root_frobenius": [
                float(
                    np.linalg.norm(world_axis[index][:, :3])
                    - np.linalg.norm(local_root[index])
                )
                for index in range(2)
            ],
            "full_condition": [
                float(np.linalg.cond(world_jacobians[index]) - np.linalg.cond(local_jacobians[index]))
                for index in range(2)
            ],
        },
        "root_refinement_frobenius": root_refinement,
        "root_refinement_limit": refinement_limit,
    }
    return {"case": "EQV02_COMMON_ROOT_JACOBIAN", "passed": bool(all(checks.values())), "checks": checks, "values": values}


def _two_segment_residual(
    state: tuple[np.ndarray, ...], weight: float, increment: np.ndarray
) -> np.ndarray:
    parent, child, _, parent_axis, child_axis = state
    delta = Rotation.from_rotvec(np.asarray(increment).reshape(2, 3)).as_matrix()
    relative = parent.T @ child
    return projector_huber_pseudo_residual_parent(
        parent_axis, relative @ delta[1], child_axis, weight
    )


def validate_eqv03(random_states: Sequence[tuple[np.ndarray, ...]]) -> dict[str, Any]:
    _, local_function = _full_body_residual_functions()
    orientation_rows = 3 * len(SEGMENTS)
    full_jacobians = [
        _central_jacobian(lambda value: local_function(value)[orientation_rows:], 30, step)
        for step in EQV_STEPS
    ]
    full_refinement = float(np.linalg.norm(full_jacobians[0] - full_jacobians[1]))
    full_limit = REFINEMENT_SCALE * max(1.0, float(np.linalg.norm(full_jacobians[1])))
    random_rows = []
    for state_index, state in enumerate(random_states[:32]):
        parent, child, _, parent_axis, child_axis = state
        relative = parent.T @ child
        at_cut = at_projective_cut_parent(parent_axis, relative, child_axis)
        for weight in EQV_WEIGHTS:
            function = lambda value, state=state, weight=weight: _two_segment_residual(
                state, weight, value
            )
            jacobians = [_central_jacobian(function, 6, step) for step in EQV_STEPS]
            refinement = float(np.linalg.norm(jacobians[0] - jacobians[1]))
            limit = REFINEMENT_SCALE * max(1.0, float(np.linalg.norm(jacobians[1])))
            random_rows.append(
                {
                    "state_index": state_index,
                    "weight": weight,
                    "at_or_within_cut": at_cut,
                    "finite": bool(all(np.all(np.isfinite(value)) for value in jacobians)),
                    "jacobian_h_frobenius": float(np.linalg.norm(jacobians[0])),
                    "jacobian_half_frobenius": float(np.linalg.norm(jacobians[1])),
                    "refinement_frobenius": refinement,
                    "refinement_limit": limit,
                    "refinement_pass": refinement <= limit,
                }
            )
    checks = {
        "full_body_finite": bool(all(np.all(np.isfinite(value)) for value in full_jacobians)),
        "full_body_root_h": float(np.linalg.norm(full_jacobians[0][:, :3])) <= 1e-8,
        "full_body_root_half": float(np.linalg.norm(full_jacobians[1][:, :3])) <= 1e-8,
        "full_body_refinement": full_refinement <= full_limit,
        "random_pair_count": len(random_rows) == 96,
        "random_no_cut": not any(bool(row["at_or_within_cut"]) for row in random_rows),
        "random_all_finite": all(bool(row["finite"]) for row in random_rows),
        "random_all_refine": all(bool(row["refinement_pass"]) for row in random_rows),
    }
    return {
        "case": "EQV03_FINITE_DIFFERENCE_REFINEMENT",
        "passed": bool(all(checks.values())),
        "checks": checks,
        "values": {
            "full_body": {
                "jacobian_h_frobenius": float(np.linalg.norm(full_jacobians[0])),
                "jacobian_half_frobenius": float(np.linalg.norm(full_jacobians[1])),
                "root_h_frobenius": float(np.linalg.norm(full_jacobians[0][:, :3])),
                "root_half_frobenius": float(np.linalg.norm(full_jacobians[1][:, :3])),
                "refinement_frobenius": full_refinement,
                "refinement_limit": full_limit,
            },
            "random": random_rows,
        },
    }


def validate_cut02() -> dict[str, Any]:
    identity = np.stack([np.eye(3), np.eye(3)])
    final = _finalize_frame_state(
        identity,
        cut_final=True,
        pre_cut_finite=True,
        pre_cut_proper=True,
        pre_cut_converged=True,
        message="otherwise passing solver diagnostics",
    )
    aligned_input = np.stack(
        [
            np.eye(3),
            Rotation.from_rotvec(np.array([0.0, 0.0, -math.pi / 2.0])).as_matrix(),
        ]
    )
    aligned = _finalize_frame_state(
        aligned_input,
        cut_final=False,
        pre_cut_finite=True,
        pre_cut_proper=True,
        pre_cut_converged=True,
        message="otherwise passing solver diagnostics",
    )
    checks = {
        "final_cut_predicate": at_projective_cut_parent(
            np.array([1.0, 0.0, 0.0]), np.eye(3), np.array([0.0, 1.0, 0.0])
        ),
        "final_cut_returned_true": final.cut_final,
        "final_matrices_all_nan": bool(np.all(np.isnan(final.matrices))),
        "final_public_finite_false": not final.finite,
        "final_public_proper_false": not final.proper,
        "final_converged_false": not final.converged,
        "final_valid_false": not final.valid,
        "final_pre_cut_finite_true": final.pre_cut_finite,
        "final_pre_cut_proper_true": final.pre_cut_proper,
        "final_message_suffix": final.message.endswith("CUT_LOCUS_STATIONARY_AT_FINAL"),
        "aligned_not_cut": not at_projective_cut_parent(
            np.array([1.0, 0.0, 0.0]),
            aligned_input[0].T @ aligned_input[1],
            np.array([0.0, 1.0, 0.0]),
        ),
        "aligned_cut_returned_false": not aligned.cut_final,
        "aligned_matrices_unchanged": bool(np.array_equal(aligned.matrices, aligned_input)),
        "aligned_pre_cut_finite_true": aligned.pre_cut_finite,
        "aligned_pre_cut_proper_true": aligned.pre_cut_proper,
        "aligned_public_finite_true": aligned.finite,
        "aligned_public_proper_true": aligned.proper,
        "aligned_converged_true": aligned.converged,
        "aligned_valid_true": aligned.valid,
        "aligned_message_no_cut_suffix": "CUT_LOCUS" not in aligned.message,
    }
    return {
        "case": "CUT02_FINAL_CUT_REPORTING",
        "passed": bool(all(checks.values())),
        "checks": checks,
        "values": {
            "final": {
                "cut_final": final.cut_final,
                "finite": final.finite,
                "proper": final.proper,
                "converged": final.converged,
                "valid": final.valid,
                "pre_cut_finite": final.pre_cut_finite,
                "pre_cut_proper": final.pre_cut_proper,
                "message": final.message,
            },
            "aligned": {
                "cut_final": aligned.cut_final,
                "finite": aligned.finite,
                "proper": aligned.proper,
                "converged": aligned.converged,
                "valid": aligned.valid,
                "pre_cut_finite": aligned.pre_cut_finite,
                "pre_cut_proper": aligned.pre_cut_proper,
                "message": aligned.message,
            },
        },
    }


def validate_numerical_equivariance() -> dict[str, Any]:
    eqv01, random_states = validate_eqv01()
    eqv02 = validate_eqv02()
    eqv03 = validate_eqv03(random_states)
    cut02 = validate_cut02()
    rows = [eqv01, eqv02, eqv03, cut02]
    return {
        "schema": "biospur-c2-3b-parent-local-numerical-gates-v1",
        "passed": bool(all(row["passed"] for row in rows)),
        "gates": rows,
    }


def _axis_cost_at_start(measured: np.ndarray, axes: Sequence[AxisPair], weight: float, mode: str) -> float:
    root, relative = _base_coordinates(measured, SEGMENTS, EDGE_ROWS)
    indices = _axis_edge_indices(EDGE_ROWS, axes)
    residual = _residual(
        np.zeros(30),
        root,
        relative,
        measured,
        SEGMENTS,
        EDGE_ROWS,
        axes,
        indices,
        CENTRAL_PROFILE.weights(),
        weight,
        mode,
    )[3 * len(SEGMENTS) :]
    return 0.5 * float(residual @ residual)


def _solution_record(solution: FrameSolution) -> dict[str, Any]:
    traces = list(solution.retraction_trace)
    reasons = []
    if not solution.scipy_success:
        reasons.append("SCIPY_NOT_SUCCESS")
    if not solution.pre_cut_finite:
        reasons.append("PRE_CUT_NONFINITE")
    if not solution.pre_cut_proper:
        reasons.append("PRE_CUT_IMPROPER")
    if not solution.finite:
        reasons.append("PUBLIC_NONFINITE")
    if not solution.proper:
        reasons.append("PUBLIC_IMPROPER")
    if solution.cut_start:
        reasons.append("CUT_START")
    if solution.cut_final:
        reasons.append("CUT_FINAL")
    if solution.optimality > 1e-8 or not np.isfinite(solution.optimality):
        reasons.append("OPTIMALITY")
    if solution.final_step_norm_rad > 1e-8 or not np.isfinite(solution.final_step_norm_rad):
        reasons.append("TERMINAL_STEP")
    if solution.wall_s > 0.5 or not np.isfinite(solution.wall_s):
        reasons.append("FRAME_WALL")
    if not solution.valid:
        reasons.append("NOT_VALID")
    return {
        "scipy_success": solution.scipy_success,
        "pre_cut_finite": solution.pre_cut_finite,
        "pre_cut_proper": solution.pre_cut_proper,
        "public_finite": solution.finite,
        "public_proper": solution.proper,
        "valid": solution.valid,
        "cut_start": solution.cut_start,
        "cut_final": solution.cut_final,
        "total_cost": solution.cost,
        "orientation_cost": solution.orientation_cost,
        "axis_cost": solution.axis_cost,
        "optimality": solution.optimality,
        "final_step_norm_rad": solution.final_step_norm_rad,
        "nfev": solution.nfev,
        "retractions": solution.retractions,
        "wall_s": solution.wall_s,
        "per_retraction_gradient_norms": [
            {
                "orientation": row["orientation_gradient_norm"],
                "axis": row["axis_gradient_norm"],
            }
            for row in traces
        ],
        "per_retraction_jacobian_norms": [
            {
                "orientation": row["orientation_jacobian_frobenius"],
                "axis": row["axis_jacobian_frobenius"],
            }
            for row in traces
        ],
        "per_retraction_condition": [row["stacked_jacobian_condition"] for row in traces],
        "failure_reasons": reasons,
        "message": solution.message,
    }


def validate_eqv04() -> dict[str, Any]:
    pairs = []
    reason_counts: Counter[str] = Counter()
    exceptions = []
    maximum_start_cost_difference = 0.0
    for profile in PROFILES:
        for case_name, seed_override, frame in WITNESS_FRAMES:
            pair: dict[str, Any] = {
                "profile": profile.name,
                "case": case_name,
                "seed_override": seed_override,
                "frame": frame,
            }
            try:
                case = make_case(case_name, seed_override=seed_override)
                axes = _case_axes(case)
                measured = case.measured[frame]
                world_cost = _axis_cost_at_start(measured, axes, profile.axis_weight, "world_comparator")
                local_cost = _axis_cost_at_start(measured, axes, profile.axis_weight, "parent_local")
                difference = abs(world_cost - local_cost)
                maximum_start_cost_difference = max(maximum_start_cost_difference, difference)
                world = _solve_tree_frame_world_comparator(
                    measured,
                    segment_names=SEGMENTS,
                    edge_rows=EDGE_ROWS,
                    axes=axes,
                    weights=profile.weights(),
                    axis_weight=profile.axis_weight,
                    collect_diagnostics=True,
                )
                local = _solve_tree_frame_relative_comparator(
                    measured,
                    segment_names=SEGMENTS,
                    edge_rows=EDGE_ROWS,
                    axes=axes,
                    weights=profile.weights(),
                    axis_weight=profile.axis_weight,
                    collect_diagnostics=True,
                )
                world_record = _solution_record(world)
                local_record = _solution_record(local)
                for record in (world_record, local_record):
                    reason_counts.update(record["failure_reasons"])
                pair.update(
                    shared_start_world_axis_cost=world_cost,
                    shared_start_parent_local_axis_cost=local_cost,
                    shared_start_axis_cost_difference=difference,
                    world_comparator=world_record,
                    parent_local_candidate=local_record,
                )
            except Exception as error:  # evidence must retain every failed identity
                exceptions.append(f"{profile.name}/{case_name}: {type(error).__name__}: {error}")
                pair["exception"] = exceptions[-1]
            pairs.append(pair)
    reconciled = Counter(
        reason
        for pair in pairs
        for owner in ("world_comparator", "parent_local_candidate")
        for reason in pair.get(owner, {}).get("failure_reasons", [])
    )
    required_fields = {
        "scipy_success",
        "pre_cut_finite",
        "pre_cut_proper",
        "public_finite",
        "public_proper",
        "valid",
        "cut_start",
        "cut_final",
        "total_cost",
        "orientation_cost",
        "axis_cost",
        "optimality",
        "final_step_norm_rad",
        "nfev",
        "retractions",
        "wall_s",
        "per_retraction_gradient_norms",
        "per_retraction_jacobian_norms",
        "per_retraction_condition",
    }
    fields_present = all(
        required_fields <= set(pair.get(owner, {}))
        for pair in pairs
        for owner in ("world_comparator", "parent_local_candidate")
    )
    checks = {
        "witness_pair_count": len(pairs) == 54,
        "run_count": sum(
            int(owner in pair)
            for pair in pairs
            for owner in ("world_comparator", "parent_local_candidate")
        )
        == 108,
        "no_exceptions": not exceptions,
        "mandatory_fields_present": fields_present,
        "shared_start_cost_equivalence": maximum_start_cost_difference <= EQV_TOL,
        "reason_counts_reconcile": reason_counts == reconciled,
    }
    return {
        "case": "EQV04_PAIRED_COORDINATE_DIAGNOSTIC",
        "passed": bool(all(checks.values())),
        "checks": checks,
        "values": {
            "maximum_shared_start_axis_cost_difference": maximum_start_cost_difference,
            "failure_reason_counts": dict(sorted(reason_counts.items())),
            "failure_reason_assignment_count": sum(reason_counts.values()),
            "exceptions": exceptions,
            "pairs": pairs,
        },
    }
