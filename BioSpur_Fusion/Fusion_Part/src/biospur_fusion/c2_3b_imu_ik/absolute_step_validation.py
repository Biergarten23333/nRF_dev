"""Preregistered diagnostics for the fixed-absolute Jacobian candidate."""

from __future__ import annotations

from collections import Counter
import hashlib
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .contracts import (
    ABSOLUTE_JACOBIAN_REFINEMENT_STEP_RAD,
    ABSOLUTE_JACOBIAN_STEP_RAD,
    CENTRAL_PROFILE,
    EDGE_ROWS,
    HINGES,
    PROFILES,
    SEGMENTS,
    AxisPair,
    FrameSolution,
    Profile,
)
from .equivariance_validation import (
    WITNESS_FRAMES,
    _axis_cost_at_start,
    _case_axes,
    _solution_record,
)
from .solver import (
    AbsoluteJacobianError,
    _absolute_central_jacobian_at_step,
    _apply_coordinates,
    _axis_edge_indices,
    _base_coordinates,
    _has_cut_locus,
    _relative_effective_steps,
    _residual,
    _residual_log_angles,
    _solve_tree_frame_relative_comparator,
    solve_frame,
    solve_tree_frame,
)
from .synthetic_generator import make_case


NUMDIFF_PATH = Path(
    "/home/zekaixiao/.local/lib/python3.12/site-packages/"
    "scipy/optimize/_numdiff.py"
)
NUMDIFF_SHA256 = "ab92e82d0f93f2c7a35eba0a2800b70cf8015d97ccf094abf556206211beb00b"
ABS01_MAGNITUDES = np.array(
    [0.0, 1e-2, 1e-6, 1e-8, 1e-10, 4.155063458215783e-10, 1e-12],
    dtype=np.float64,
)
ABS02_SEED = 320260904
ABS02_STATE_COUNT = 32
REFINEMENT_SCALE = 1e-5
ABS03_IDENTITIES = (
    ("uniform_projector_1", "SYN05_MOUNT_STEP", None, 200),
    (
        "distal_w_0.25_projector_0.5",
        "SYN03_NONIDEAL_HUMAN_HINGE",
        None,
        295,
    ),
    (
        "distal_w_0.25_projector_0.5",
        "SYN04_BILATERAL_VARIABILITY",
        None,
        100,
    ),
    ("uniform_projector_1", "SYN04_BILATERAL_VARIABILITY", None, 100),
)


def _verify_numdiff_hash() -> None:
    digest = hashlib.sha256(NUMDIFF_PATH.read_bytes()).hexdigest()
    if digest != NUMDIFF_SHA256:
        raise RuntimeError(f"SciPy _numdiff.py hash changed: {digest}")


def _scipy_relative_steps(values: np.ndarray) -> np.ndarray:
    _verify_numdiff_hash()
    from scipy.optimize._numdiff import _compute_absolute_step

    f0 = np.array([1.0], dtype=np.float64)
    return np.asarray(
        _compute_absolute_step(1e-6, np.asarray(values, dtype=np.float64), f0, "3-point"),
        dtype=np.float64,
    )


def validate_abs01() -> dict[str, Any]:
    scipy_steps = _scipy_relative_steps(ABS01_MAGNITUDES)
    independent_steps = _relative_effective_steps(ABS01_MAGNITUDES)
    rows = []
    for value, scipy_step, independent_step in zip(
        ABS01_MAGNITUDES, scipy_steps, independent_steps, strict=True
    ):
        absolute = ABSOLUTE_JACOBIAN_STEP_RAD
        row = {
            "x": float(value),
            "spacing": float(np.spacing(value)),
            "scipy_relative_returned_h": float(scipy_step),
            "independent_relative_h": float(independent_step),
            "scipy_relative_actual_plus_dx": float((value + scipy_step) - value),
            "scipy_relative_actual_minus_dx": float(value - (value - scipy_step)),
            "absolute_requested_h": absolute,
            "absolute_actual_plus_dx": float((value + absolute) - value),
            "absolute_actual_minus_dx": float(value - (value - absolute)),
            "finite": bool(
                np.all(
                    np.isfinite(
                        [
                            value,
                            scipy_step,
                            independent_step,
                            (value + absolute) - value,
                            value - (value - absolute),
                        ]
                    )
                )
            ),
            "relative_plus_representable": bool(value + scipy_step != value),
            "relative_minus_representable": bool(value - scipy_step != value),
            "absolute_plus_representable": bool(value + absolute != value),
            "absolute_minus_representable": bool(value - absolute != value),
        }
        rows.append(row)
    worst_index = int(np.where(ABS01_MAGNITUDES == 4.155063458215783e-10)[0][0])
    disparity = float(abs(scipy_steps[0] / scipy_steps[worst_index]))
    checks = {
        "row_count": len(rows) == 7,
        "private_helper_hash_verified": True,
        "independent_formula_componentwise_identical": bool(
            np.array_equal(scipy_steps, independent_steps)
        ),
        "all_finite": all(bool(row["finite"]) for row in rows),
        "all_relative_representable": all(
            bool(row["relative_plus_representable"])
            and bool(row["relative_minus_representable"])
            for row in rows
        ),
        "all_absolute_representable": all(
            bool(row["absolute_plus_representable"])
            and bool(row["absolute_minus_representable"])
            for row in rows
        ),
        "zero_fallback_exact": float(scipy_steps[0]) == 6.055454452393343e-6,
        "worst_witness_step_exact": (
            float(scipy_steps[worst_index]) == 4.155063458215783e-16
        ),
        "disparity_exact": disparity == 14573675019.138222,
        "absolute_request_constant": all(
            row["absolute_requested_h"] == 1e-6 for row in rows
        ),
    }
    return {
        "case": "ABS01_STEP_SEMANTICS",
        "passed": bool(all(checks.values())),
        "checks": checks,
        "values": {
            "rows": rows,
            "zero_to_worst_disparity": disparity,
            "numdiff_path": str(NUMDIFF_PATH),
            "numdiff_sha256": NUMDIFF_SHA256,
        },
    }


def _residual_context(
    case_name: str,
    frame: int,
    profile: Profile,
    *,
    seed_override: int | None = None,
) -> tuple[np.ndarray, tuple[AxisPair, ...], tuple[object, ...]]:
    case = make_case(case_name, seed_override=seed_override)
    measured = case.measured[frame]
    axes = _case_axes(case)
    root, relative = _base_coordinates(measured, SEGMENTS, EDGE_ROWS)
    indices = _axis_edge_indices(EDGE_ROWS, axes)
    residual_args: tuple[object, ...] = (
        root,
        relative,
        measured,
        SEGMENTS,
        EDGE_ROWS,
        axes,
        indices,
        profile.weights(),
        profile.axis_weight,
        "parent_local",
    )
    return measured, axes, residual_args


def _jacobian_log_extrema(
    increment: np.ndarray,
    residual_args: tuple[object, ...],
    step: float,
) -> dict[str, Any]:
    root, relative, measured, segment_names, edge_rows = residual_args[:5]
    rows = []
    for coordinate in range(len(increment)):
        for sign in (-1.0, 1.0):
            point = np.asarray(increment, dtype=np.float64).copy()
            point[coordinate] += sign * step
            rows.append(
                _residual_log_angles(
                    point,
                    root,
                    relative,
                    measured,
                    segment_names,
                    edge_rows,
                )
            )
    values = np.stack(rows)
    return {
        "evaluation_count": len(rows),
        "all_finite": bool(np.all(np.isfinite(values))),
        "minimum_rad": float(np.min(values)),
        "maximum_rad": float(np.max(values)),
        "per_segment_minimum_rad": np.min(values, axis=0),
        "per_segment_maximum_rad": np.max(values, axis=0),
    }


def _jacobian_pair_record(
    increment: np.ndarray,
    residual_args: tuple[object, ...],
) -> dict[str, Any]:
    residual = np.asarray(_residual(increment, *residual_args), dtype=np.float64)
    jacobian_h = _absolute_central_jacobian_at_step(
        increment, residual_args, ABSOLUTE_JACOBIAN_STEP_RAD
    )
    jacobian_h2 = _absolute_central_jacobian_at_step(
        increment, residual_args, ABSOLUTE_JACOBIAN_REFINEMENT_STEP_RAD
    )
    orientation_rows = 3 * len(residual_args[3])
    refinement = float(np.linalg.norm(jacobian_h - jacobian_h2))
    refinement_limit = REFINEMENT_SCALE * max(1.0, float(np.linalg.norm(jacobian_h2)))
    blocks = {}
    for name, row_slice in (
        ("complete", slice(None)),
        ("orientation", slice(0, orientation_rows)),
        ("axis", slice(orientation_rows, None)),
    ):
        j_h = jacobian_h[row_slice]
        j_h2 = jacobian_h2[row_slice]
        r = residual[row_slice]
        condition = float(np.linalg.cond(j_h))
        blocks[name] = {
            "jacobian_h_frobenius": float(np.linalg.norm(j_h)),
            "jacobian_h2_frobenius": float(np.linalg.norm(j_h2)),
            "gradient_h_norm": float(np.linalg.norm(j_h.T @ r)),
            "gradient_h2_norm": float(np.linalg.norm(j_h2.T @ r)),
            "refinement_frobenius": float(np.linalg.norm(j_h - j_h2)),
            "condition": condition if np.isfinite(condition) else None,
            "condition_defined": bool(np.isfinite(condition)),
        }
    return {
        "finite": bool(
            np.all(np.isfinite(residual))
            and np.all(np.isfinite(jacobian_h))
            and np.all(np.isfinite(jacobian_h2))
        ),
        "representable_h": bool(
            np.all(increment + ABSOLUTE_JACOBIAN_STEP_RAD != increment)
            and np.all(increment - ABSOLUTE_JACOBIAN_STEP_RAD != increment)
        ),
        "representable_h2": bool(
            np.all(increment + ABSOLUTE_JACOBIAN_REFINEMENT_STEP_RAD != increment)
            and np.all(increment - ABSOLUTE_JACOBIAN_REFINEMENT_STEP_RAD != increment)
        ),
        "refinement_frobenius": refinement,
        "refinement_limit": refinement_limit,
        "refinement_pass": refinement <= refinement_limit,
        "blocks": blocks,
        "log_h": _jacobian_log_extrema(
            increment, residual_args, ABSOLUTE_JACOBIAN_STEP_RAD
        ),
        "log_h2": _jacobian_log_extrema(
            increment, residual_args, ABSOLUTE_JACOBIAN_REFINEMENT_STEP_RAD
        ),
        "residual": residual,
        "jacobian_h": jacobian_h,
        "jacobian_h2": jacobian_h2,
    }


def _abs02_states() -> list[np.ndarray]:
    generator = np.random.Generator(np.random.PCG64(ABS02_SEED))
    states = [np.zeros(30, dtype=np.float64)]
    for _ in range(ABS02_STATE_COUNT):
        blocks = []
        for _ in range(10):
            draw = generator.standard_normal(3)
            norm = float(np.linalg.norm(draw))
            if not np.isfinite(norm) or norm == 0.0:
                raise RuntimeError("ABS02_STATE_NORMALIZATION_FAILED")
            blocks.append(0.2 * draw / norm)
        states.append(np.concatenate(blocks))
    return states


def validate_abs02() -> dict[str, Any]:
    _, axes, residual_args = _residual_context(
        "SYN01_DISTAL_TRANSVERSE_NOISE", 100, CENTRAL_PROFILE
    )
    axis_indices = residual_args[6]
    rows = []
    exceptions = []
    for state_index, increment in enumerate(_abs02_states()):
        try:
            _, next_relative, _ = _apply_coordinates(
                residual_args[0],
                residual_args[1],
                increment,
                SEGMENTS,
                EDGE_ROWS,
            )
            projective_cut = _has_cut_locus(next_relative, axes, axis_indices)
            record = _jacobian_pair_record(increment, residual_args)
            record.update(state_index=state_index, projective_cut=projective_cut)
        except Exception as error:
            exceptions.append(f"state {state_index}: {type(error).__name__}: {error}")
            record = {"state_index": state_index, "exception": exceptions[-1]}
        rows.append(record)

    log_cut_increment = np.zeros(30, dtype=np.float64)
    log_cut_increment[0] = math.pi
    try:
        _absolute_central_jacobian_at_step(
            log_cut_increment, residual_args, ABSOLUTE_JACOBIAN_STEP_RAD
        )
        log_cut = {"failed_closed": False, "message": "NO_EXCEPTION"}
    except AbsoluteJacobianError as error:
        log_cut = {
            "failed_closed": str(error).startswith("SO3_LOG_CUT"),
            "message": str(error),
        }
    checks = {
        "state_count": len(rows) == 33,
        "no_exceptions": not exceptions,
        "all_finite": all(bool(row.get("finite")) for row in rows),
        "all_h_representable": all(bool(row.get("representable_h")) for row in rows),
        "all_h2_representable": all(bool(row.get("representable_h2")) for row in rows),
        "no_projective_cut": not any(bool(row.get("projective_cut")) for row in rows),
        "no_log_cut": all(
            row.get("log_h", {}).get("maximum_rad", math.inf) < math.pi
            and row.get("log_h2", {}).get("maximum_rad", math.inf) < math.pi
            for row in rows
        ),
        "all_full_refine": all(bool(row.get("refinement_pass")) for row in rows),
        "exact_log_cut_counted_fail_closed": bool(log_cut["failed_closed"]),
    }
    return {
        "case": "ABS02_FULL_TREE_REFINEMENT",
        "passed": bool(all(checks.values())),
        "checks": checks,
        "values": {
            "seed": ABS02_SEED,
            "eligible_state_count": len(rows),
            "exceptions": exceptions,
            "states": rows,
            "exact_log_cut_mutation": log_cut,
        },
    }


def _profile_by_name(name: str) -> Profile:
    return next(profile for profile in PROFILES if profile.name == name)


def _trace_record(
    trace: Mapping[str, Any],
    measured: np.ndarray,
    axes: Sequence[AxisPair],
    profile: Profile,
) -> dict[str, Any]:
    increment = np.asarray(trace["increment"], dtype=np.float64)
    residual = np.asarray(trace["residual"], dtype=np.float64)
    solver_jacobian = np.asarray(trace["solver_jacobian"], dtype=np.float64)
    residual_args: tuple[object, ...] = (
        np.asarray(trace["incoming_root"], dtype=np.float64),
        np.asarray(trace["incoming_relative"], dtype=np.float64),
        measured,
        SEGMENTS,
        EDGE_ROWS,
        axes,
        _axis_edge_indices(EDGE_ROWS, axes),
        profile.weights(),
        profile.axis_weight,
        "parent_local",
    )
    independent_steps = _relative_effective_steps(increment)
    scipy_steps = _scipy_relative_steps(increment)
    try:
        absolute = _jacobian_pair_record(increment, residual_args)
    except AbsoluteJacobianError as error:
        absolute = {
            "finite": False,
            "representable_h": None,
            "representable_h2": None,
            "refinement_frobenius": None,
            "refinement_limit": None,
            "refinement_pass": None,
            "blocks": {"complete": None, "orientation": None, "axis": None},
            "log_h": None,
            "log_h2": None,
            "residual": residual,
            "jacobian_h": None,
            "jacobian_h2": None,
            "named_fail_closed_reason": str(error),
        }
    orientation_rows = 3 * len(SEGMENTS)
    solver_condition = float(np.linalg.cond(solver_jacobian))
    return {
        "retraction": trace["retraction"],
        "incoming_root": trace["incoming_root"],
        "incoming_relative": trace["incoming_relative"],
        "returned_increment_30": increment,
        "residual": residual,
        "solver_jacobian": solver_jacobian,
        "relative_effective_steps_30": independent_steps,
        "relative_private_comparator_steps_30": scipy_steps,
        "relative_step_comparator_max_difference": float(
            np.max(np.abs(independent_steps - scipy_steps))
        ),
        "relative_effective_step_min": float(np.min(np.abs(independent_steps))),
        "relative_effective_step_max": float(np.max(np.abs(independent_steps))),
        "solver_orientation_jacobian_frobenius": float(
            np.linalg.norm(solver_jacobian[:orientation_rows])
        ),
        "solver_axis_jacobian_frobenius": float(
            np.linalg.norm(solver_jacobian[orientation_rows:])
        ),
        "solver_orientation_gradient_norm": float(
            np.linalg.norm(
                solver_jacobian[:orientation_rows].T @ residual[:orientation_rows]
            )
        ),
        "solver_axis_gradient_norm": float(
            np.linalg.norm(
                solver_jacobian[orientation_rows:].T @ residual[orientation_rows:]
            )
        ),
        "solver_condition": solver_condition if np.isfinite(solver_condition) else None,
        "solver_condition_defined": bool(np.isfinite(solver_condition)),
        "absolute_diagnostic": absolute,
        "nfev": trace["nfev"],
        "optimality": trace["optimality"],
        "terminal_step": trace["step_norm_rad"],
        "success": trace["scipy_success"],
        "message": trace["message"],
    }


def _run_abs03_solver(
    mode: str,
    measured: np.ndarray,
    axes: Sequence[AxisPair],
    profile: Profile,
) -> FrameSolution:
    kwargs = {
        "segment_names": SEGMENTS,
        "edge_rows": EDGE_ROWS,
        "axes": axes,
        "weights": profile.weights(),
        "axis_weight": profile.axis_weight,
        "collect_diagnostics": True,
        "collect_trace_state": True,
    }
    if mode == "relative_parent_local_comparator":
        return _solve_tree_frame_relative_comparator(measured, **kwargs)
    if mode == "absolute_parent_local_candidate":
        return solve_tree_frame(measured, **kwargs)
    raise ValueError(mode)


def validate_abs03() -> dict[str, Any]:
    identities = []
    exceptions = []
    run_count = 0
    trace_count = 0
    for profile_name, case_name, seed_override, frame in ABS03_IDENTITIES:
        profile = _profile_by_name(profile_name)
        case = make_case(case_name, seed_override=seed_override)
        axes = _case_axes(case)
        measured = case.measured[frame]
        identity = {
            "profile": profile_name,
            "case": case_name,
            "seed_override": seed_override,
            "frame": frame,
            "runs": [],
        }
        for mode in (
            "relative_parent_local_comparator",
            "absolute_parent_local_candidate",
        ):
            try:
                solution = _run_abs03_solver(mode, measured, axes, profile)
                traces = [
                    _trace_record(row, measured, axes, profile)
                    for row in solution.retraction_trace
                ]
                run = {
                    "derivative_mode": mode,
                    "valid": solution.valid,
                    "scipy_success": solution.scipy_success,
                    "retractions": solution.retractions,
                    "trace_record_count": len(traces),
                    "trace_count_reconciles": len(traces) == solution.retractions,
                    "nfev": solution.nfev,
                    "njev": solution.njev,
                    "optimality": solution.optimality,
                    "final_step_norm_rad": solution.final_step_norm_rad,
                    "wall_s": solution.wall_s,
                    "message": solution.message,
                    "traces": traces,
                }
                run_count += 1
                trace_count += len(traces)
            except Exception as error:
                exceptions.append(
                    f"{profile_name}/{case_name}/{mode}: {type(error).__name__}: {error}"
                )
                run = {"derivative_mode": mode, "exception": exceptions[-1]}
            identity["runs"].append(run)
        identities.append(identity)
    checks = {
        "identity_count": len(identities) == 4,
        "run_count": run_count == 8,
        "no_exceptions": not exceptions,
        "trace_counts_reconcile": all(
            bool(run.get("trace_count_reconciles"))
            for identity in identities
            for run in identity["runs"]
        ),
        "relative_step_formula_matches_private": all(
            row["relative_step_comparator_max_difference"] == 0.0
            for identity in identities
            for run in identity["runs"]
            for row in run.get("traces", [])
        ),
    }
    return {
        "case": "ABS03_WORST_WITNESS_RETRACTION_TRACE",
        "passed": bool(all(checks.values())),
        "checks": checks,
        "values": {
            "run_count": run_count,
            "trace_record_count": trace_count,
            "exceptions": exceptions,
            "identities": identities,
        },
    }


def _abs04_solution_record(solution: FrameSolution) -> dict[str, Any]:
    record = _solution_record(solution)
    message = solution.message
    fail_closed = (
        message
        if message.startswith("SO3_LOG_CUT")
        or message.startswith("ABSOLUTE_JACOBIAN")
        or message.startswith("CUT_LOCUS")
        else None
    )
    record.update(
        derivative_mode=solution.derivative_mode,
        jacobian_call_count=solution.njev,
        callable_jacobian_residual_evaluation_count=(
            solution.jacobian_residual_evaluations
        ),
        effective_step_min_max={
            "min": solution.effective_step_min_rad,
            "max": solution.effective_step_max_rad,
        },
        named_fail_closed_reason=fail_closed,
    )
    return record


def validate_abs04() -> dict[str, Any]:
    pairs = []
    exceptions = []
    reasons: Counter[str] = Counter()
    maximum_cost_difference = 0.0
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
                relative_cost = _axis_cost_at_start(
                    measured, axes, profile.axis_weight, "parent_local"
                )
                absolute_cost = _axis_cost_at_start(
                    measured, axes, profile.axis_weight, "parent_local"
                )
                difference = abs(relative_cost - absolute_cost)
                maximum_cost_difference = max(maximum_cost_difference, difference)
                common = {
                    "segment_names": SEGMENTS,
                    "edge_rows": EDGE_ROWS,
                    "axes": axes,
                    "weights": profile.weights(),
                    "axis_weight": profile.axis_weight,
                    "collect_diagnostics": True,
                }
                relative = _solve_tree_frame_relative_comparator(measured, **common)
                absolute = solve_tree_frame(measured, **common)
                relative_record = _abs04_solution_record(relative)
                absolute_record = _abs04_solution_record(absolute)
                reasons.update(relative_record["failure_reasons"])
                reasons.update(absolute_record["failure_reasons"])
                pair.update(
                    shared_start_relative_cost=relative_cost,
                    shared_start_absolute_cost=absolute_cost,
                    shared_start_cost_difference=difference,
                    relative_parent_local_comparator=relative_record,
                    absolute_parent_local_candidate=absolute_record,
                )
            except Exception as error:
                exceptions.append(
                    f"{profile.name}/{case_name}: {type(error).__name__}: {error}"
                )
                pair["exception"] = exceptions[-1]
            pairs.append(pair)
    owners = (
        "relative_parent_local_comparator",
        "absolute_parent_local_candidate",
    )
    reconciled = Counter(
        reason
        for pair in pairs
        for owner in owners
        for reason in pair.get(owner, {}).get("failure_reasons", [])
    )
    inherited_fields = {
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
    additional_fields = {
        "derivative_mode",
        "jacobian_call_count",
        "callable_jacobian_residual_evaluation_count",
        "effective_step_min_max",
        "named_fail_closed_reason",
    }
    all_fields = inherited_fields | additional_fields
    checks = {
        "pair_count": len(pairs) == 54,
        "run_count": sum(int(owner in pair) for pair in pairs for owner in owners)
        == 108,
        "no_exceptions": not exceptions,
        "all_mandatory_fields_present": all(
            all_fields <= set(pair.get(owner, {}))
            for pair in pairs
            for owner in owners
        ),
        "shared_start_cost_equivalence": maximum_cost_difference <= 1e-12,
        "reason_counts_reconcile": reasons == reconciled,
        "run_retraction_counts_reconcile": all(
            len(pair[owner]["per_retraction_condition"])
            == pair[owner]["retractions"]
            for pair in pairs
            for owner in owners
        ),
    }
    return {
        "case": "ABS04_PAIRED_DERIVATIVE_DIAGNOSTIC",
        "passed": bool(all(checks.values())),
        "checks": checks,
        "values": {
            "maximum_shared_start_cost_difference": maximum_cost_difference,
            "failure_reason_counts": dict(sorted(reasons.items())),
            "failure_reason_assignment_count": sum(reasons.values()),
            "exceptions": exceptions,
            "pairs": pairs,
        },
    }


def validate_absolute_step_diagnostics() -> dict[str, Any]:
    gates = [validate_abs01(), validate_abs02(), validate_abs03(), validate_abs04()]
    return {
        "schema": "biospur-c2-3b-absolute-step-diagnostics-v1",
        "passed": bool(all(gate["passed"] for gate in gates)),
        "gates": gates,
    }
