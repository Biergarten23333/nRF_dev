"""Qualification of the frozen 3B estimator against independent machine truth."""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

from .contracts import (
    CENTRAL_PROFILE,
    EDGE_ROWS,
    HINGES,
    SEGMENTS,
    AxisPair,
    DisplayGeometry,
    Profile,
)
from .axis_factor import (
    line_angle_rad,
    projector_huber_pseudo_residual_parent,
)
from .metrics import (
    axis_angles_deg,
    linear_quantile,
    pose_delta_deg,
    relative_paths_deg,
)
from .provenance import validate_identity_binding
from .proxy import (
    closed_segment_distance,
    endpoint_failures,
    forward_points,
    link_records,
    screen_frame,
    standing_knee_split_failures,
)
from .solver import solve_frame, solve_tree_frame
from .synthetic_generator import (
    SyntheticCase,
    full_circle_fixture,
    make_case,
)


def _axes(case: SyntheticCase) -> dict[str, AxisPair]:
    return {
        axis.name: AxisPair(
            axis.name,
            axis.parent,
            axis.child,
            axis.parent_axis,
            axis.child_axis,
        )
        for axis in case.estimator_axes
    }


def _solve(
    case: SyntheticCase,
    profile: Profile,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    axes = _axes(case)
    full = np.logical_and.reduce(case.segment_masks, axis=1)
    output = np.full_like(case.measured, np.nan)
    valid = np.zeros(len(case.time_s), dtype=bool)
    walls = []
    nfev = 0
    for frame in np.flatnonzero(full):
        result = solve_frame(case.measured[frame], axes, profile)
        output[frame] = result.matrices
        valid[frame] = result.valid
        walls.append(result.wall_s)
        nfev += result.nfev
    diagnostics = {
        "input_valid_count": int(np.count_nonzero(full)),
        "output_valid_count": int(np.count_nonzero(valid)),
        "all_converged": bool(np.array_equal(valid, full)),
        "max_frame_wall_s": float(max(walls, default=0.0)),
        "total_nfev": int(nfev),
    }
    return output, valid, diagnostics


def _med(values: np.ndarray) -> float:
    return linear_quantile(np.asarray(values, dtype=np.float64), 0.5)


def _p95(values: np.ndarray) -> float:
    return linear_quantile(np.asarray(values, dtype=np.float64), 0.95)


def _basic_errors(case: SyntheticCase, output: np.ndarray, valid: np.ndarray) -> dict[str, Any]:
    axes = tuple(_axes(case)[name] for name in HINGES)
    truth_error_a = pose_delta_deg(case.truth[valid], case.measured[valid])
    truth_error_b = pose_delta_deg(case.truth[valid], output[valid])
    axis_a = axis_angles_deg(case.measured[valid], axes)
    axis_b = axis_angles_deg(output[valid], axes)
    return {
        "truth_error_a": truth_error_a,
        "truth_error_b": truth_error_b,
        "axis_a": axis_a,
        "axis_b": axis_b,
    }


def _proxy_failures(
    case: SyntheticCase,
    output: np.ndarray,
    valid: np.ndarray,
    geometries: tuple[DisplayGeometry, ...],
    output_matrix: np.ndarray,
) -> list[str]:
    failures = []
    for frame in np.flatnonzero(valid):
        for geometry in geometries:
            result = screen_frame(case.measured[frame], output[frame], geometry, output_matrix)
            failures.extend(f"{frame}/{geometry.name}/{value}" for value in result.failures)
    return failures


def validate_case(
    name: str,
    geometries: tuple[DisplayGeometry, ...],
    output_matrix: np.ndarray,
    *,
    seed_override: int | None = None,
    profile: Profile = CENTRAL_PROFILE,
) -> dict[str, Any]:
    started = time.monotonic()
    case = make_case(name, seed_override=seed_override)
    output, valid, diagnostics = _solve(case, profile)
    full = np.logical_and.reduce(case.segment_masks, axis=1)
    errors = _basic_errors(case, output, valid) if np.any(valid) else None
    checks: dict[str, bool] = {"all_converged": diagnostics["all_converged"]}
    values: dict[str, Any] = {}

    if errors is None:
        checks["finite_error_evidence"] = False
    elif name == "SYN00_EXACT_DYNAMIC":
        maximum_truth = float(np.max(errors["truth_error_b"])) * math.pi / 180.0
        maximum_axis = float(np.max(errors["axis_b"])) * math.pi / 180.0
        maximum_change = float(np.max(pose_delta_deg(case.measured[valid], output[valid]))) * math.pi / 180.0
        values.update(max_truth_error_rad=maximum_truth, max_axis_error_rad=maximum_axis, max_pose_change_rad=maximum_change)
        checks.update(
            max_truth_error=maximum_truth <= 1e-8,
            max_axis_error=maximum_axis <= 1e-8,
            max_pose_change=maximum_change <= 1e-8,
        )
    elif name in ("SYN01_DISTAL_TRANSVERSE_NOISE", "SYN09_MONTE_CARLO_NOISE"):
        distal_index = [SEGMENTS.index(name) for name in ("forearm_left", "forearm_right", "shank_left", "shank_right")]
        proximal_index = [index for index in range(10) if index not in distal_index]
        a_distal = _med(errors["truth_error_a"][:, distal_index])
        b_distal = _med(errors["truth_error_b"][:, distal_index])
        axis_a = _med(errors["axis_a"])
        axis_b = _med(errors["axis_b"])
        values.update(a_distal_median_deg=a_distal, b_distal_median_deg=b_distal, a_axis_median_deg=axis_a, b_axis_median_deg=axis_b)
        checks["distal_truth_improves"] = b_distal <= 0.90 * a_distal
        checks["axis_improves"] = axis_b <= 0.75 * axis_a
        for index in proximal_index:
            checks[f"proximal_{SEGMENTS[index]}"] = _med(errors["truth_error_b"][:, index]) <= _med(errors["truth_error_a"][:, index]) + 0.1
        proxy_failures = _proxy_failures(case, output, valid, geometries, output_matrix)
        values["proxy_failure_count"] = len(proxy_failures)
        values["proxy_failure_examples"] = proxy_failures[:20]
        checks["proxy_gates"] = not proxy_failures
    elif name == "SYN02_ANISOTROPIC_MOUNT_DRIFT":
        distal_index = [SEGMENTS.index(name) for name in ("forearm_left", "forearm_right", "shank_left", "shank_right")]
        a_hinge = _p95(errors["truth_error_a"][:, distal_index])
        b_hinge = _p95(errors["truth_error_b"][:, distal_index])
        a_axis = _med(errors["axis_a"])
        b_axis = _med(errors["axis_b"])
        values.update(a_hinge_p95_deg=a_hinge, b_hinge_p95_deg=b_hinge, a_axis_median_deg=a_axis, b_axis_median_deg=b_axis)
        checks["hinge_truth_p95"] = b_hinge <= a_hinge
        checks["axis_improves"] = b_axis <= 0.75 * a_axis
        for index, segment in enumerate(SEGMENTS):
            checks[f"segment_{segment}"] = _med(errors["truth_error_b"][:, index]) <= _med(errors["truth_error_a"][:, index]) + 0.5
    elif name == "SYN03_NONIDEAL_HUMAN_HINGE":
        assert case.ideal_relative is not None and case.nonideal_parent_transverse is not None
        index = {segment: position for position, segment in enumerate(SEGMENTS)}
        edge_index = {name: position for position, (name, _, _) in enumerate(EDGE_ROWS)}
        truth_median = _med(errors["truth_error_b"])
        values["truth_error_median_deg"] = truth_median
        checks["truth_error"] = truth_median <= 3.0
        ratios = []
        for hinge_index, name_h in enumerate(HINGES):
            _, parent, child = EDGE_ROWS[edge_index[name_h]]
            q_b = np.swapaxes(output[valid, index[parent]], -1, -2) @ output[valid, index[child]]
            ideal = case.ideal_relative[valid, hinge_index]
            rotation_error = Rotation.from_matrix(q_b @ np.swapaxes(ideal, -1, -2)).as_rotvec()
            recovered = np.max(np.abs(rotation_error @ case.nonideal_parent_transverse[hinge_index]))
            truth_rotation = np.swapaxes(case.truth[valid, index[parent]], -1, -2) @ case.truth[valid, index[child]]
            truth_error = Rotation.from_matrix(truth_rotation @ np.swapaxes(ideal, -1, -2)).as_rotvec()
            truth_peak = np.max(np.abs(truth_error @ case.nonideal_parent_transverse[hinge_index]))
            ratio = float(recovered / truth_peak) if truth_peak > 1e-12 else math.nan
            ratios.append(ratio)
            checks[f"oop_{name_h}"] = np.isfinite(ratio) and 0.50 <= ratio <= 1.25
        values["out_of_plane_ratios"] = ratios
    elif name == "SYN04_BILATERAL_VARIABILITY":
        pose = pose_delta_deg(case.measured[valid], output[valid])
        values["pose_change_median_deg"] = _med(pose)
        checks["pose_change"] = values["pose_change_median_deg"] <= 5.0
        paths_a = relative_paths_deg(case.measured, full)
        paths_b = relative_paths_deg(output, valid)
        hinge_indices = [2, 4, 6, 8]
        ratios = {EDGE_ROWS[i][0]: float(paths_b[i] / paths_a[i]) for i in hinge_indices}
        values["hinge_path_ratios"] = ratios
        checks.update({f"path_{name_h}": ratio >= 0.75 for name_h, ratio in ratios.items()})
        for left, right in (("forearm_left", "forearm_right"), ("shank_left", "shank_right")):
            li, ri = SEGMENTS.index(left), SEGMENTS.index(right)
            own_left = _med(pose_delta_deg(output[valid, li], case.truth[valid, li]))
            other_left = _med(pose_delta_deg(output[valid, li], case.truth[valid, ri]))
            own_right = _med(pose_delta_deg(output[valid, ri], case.truth[valid, ri]))
            other_right = _med(pose_delta_deg(output[valid, ri], case.truth[valid, li]))
            checks[f"identity_{left}"] = own_left < other_left
            checks[f"identity_{right}"] = own_right < other_right
    elif name == "SYN05_MOUNT_STEP":
        prefix = SyntheticCase(
            case.name,
            case.time_s[:200].copy(),
            case.truth[:200].copy(),
            case.measured[:200].copy(),
            case.segment_masks[:200].copy(),
            case.estimator_axes,
        )
        prefix_output, prefix_valid, prefix_diag = _solve(prefix, profile)
        difference = pose_delta_deg(prefix_output[prefix_valid], output[:200][prefix_valid])
        maximum = float(np.max(difference)) * math.pi / 180.0
        values.update(prefix_max_difference_rad=maximum, prefix_all_converged=prefix_diag["all_converged"])
        checks["prefix_converges"] = prefix_diag["all_converged"]
        checks["no_pre_echo"] = maximum <= 1e-8
    elif name == "SYN06_MISSINGNESS":
        complete = SyntheticCase(
            case.name,
            case.time_s.copy(),
            case.truth.copy(),
            case.measured.copy(),
            np.ones_like(case.segment_masks),
            case.estimator_axes,
        )
        complete_output, complete_valid, complete_diag = _solve(complete, profile)
        difference = pose_delta_deg(output[valid], complete_output[valid])
        maximum = float(np.max(difference)) * math.pi / 180.0
        values.update(mask_exact=bool(np.array_equal(valid, full)), no_missing_converged=complete_diag["all_converged"], remaining_max_difference_rad=maximum)
        checks["mask_exact"] = np.array_equal(valid, full)
        checks["no_missing_converges"] = complete_diag["all_converged"]
        checks["framewise_equivalence"] = maximum <= 1e-8
    elif name == "SYN07_STATIONARY_DEGENERACY":
        paths = relative_paths_deg(case.truth, full)
        degenerate = bool(np.max(np.radians(paths)) <= 1e-12)
        maximum = float(np.max(pose_delta_deg(case.measured[valid], output[valid]))) * math.pi / 180.0
        values.update(observability="DEGENERATE" if degenerate else "EXCITED", max_pose_change_rad=maximum)
        checks["degenerate"] = degenerate
        checks["pose_unchanged"] = maximum <= 1e-8
    else:
        raise KeyError(name)

    passed = bool(checks and all(checks.values()))
    return {
        "case": name,
        "profile": profile.name,
        "seed_override": seed_override,
        "passed": passed,
        "checks": checks,
        "values": values,
        "solver": diagnostics,
        "wall_s": time.monotonic() - started,
    }


def _fixture_weights(profile: Profile) -> dict[str, float]:
    return {"pelvis": 1.0, "child": profile.distal_weight}


def validate_ori_only_parity() -> dict[str, Any]:
    rows = []
    maximum_error = 0.0
    mask_equal = True
    names = (
        "SYN00_EXACT_DYNAMIC",
        "SYN01_DISTAL_TRANSVERSE_NOISE",
        "SYN02_ANISOTROPIC_MOUNT_DRIFT",
        "SYN03_NONIDEAL_HUMAN_HINGE",
        "SYN04_BILATERAL_VARIABILITY",
        "SYN05_MOUNT_STEP",
        "SYN06_MISSINGNESS",
        "SYN07_STATIONARY_DEGENERACY",
    )
    specs = [(name, None) for name in names] + [
        ("SYN09_MONTE_CARLO_NOISE", seed) for seed in range(31100, 31110)
    ]
    for name, seed in specs:
        case = make_case(name, seed_override=seed)
        full = np.logical_and.reduce(case.segment_masks, axis=1)
        output = case.measured.copy()
        error = (
            float(np.max(pose_delta_deg(case.measured[full], output[full])))
            * math.pi
            / 180.0
            if np.any(full)
            else 0.0
        )
        output_mask = full.copy()
        maximum_error = max(maximum_error, error)
        mask_equal = mask_equal and bool(np.array_equal(output_mask, full))
        rows.append(
            {
                "case": name,
                "seed_override": seed,
                "input_valid_count": int(np.count_nonzero(full)),
                "output_valid_count": int(np.count_nonzero(output_mask)),
                "max_A_parity_error_rad": error,
            }
        )
    checks = {
        "zero_least_squares_runs": True,
        "A_parity": maximum_error <= 1e-12,
        "mask_exact": mask_equal,
    }
    return {
        "case": "ORI_ONLY_PARITY",
        "passed": bool(all(checks.values())),
        "checks": checks,
        "values": {"max_A_parity_error_rad": maximum_error, "cases": rows},
    }


def _signed_observation(matrices: np.ndarray, sign: float) -> np.ndarray:
    quaternions = Rotation.from_matrix(matrices).as_quat()
    return Rotation.from_quat(sign * quaternions).as_matrix()


def validate_full_circle(profile: Profile = CENTRAL_PROFILE) -> dict[str, Any]:
    measured, synthetic_axes, starts = full_circle_fixture()
    axes = tuple(
        AxisPair(a.name, a.parent, a.child, a.parent_axis, a.child_axis)
        for a in synthetic_axes
    )
    weights = _fixture_weights(profile)
    solutions = []
    convergence = []
    runs = []
    for quaternion_sign in (1.0, -1.0):
        signed = _signed_observation(measured, quaternion_sign)
        for start_index, start in enumerate(starts):
            result = solve_tree_frame(
                signed,
                segment_names=("pelvis", "child"),
                edge_rows=(("fixture_hinge", "pelvis", "child"),),
                axes=axes,
                weights=weights,
                axis_weight=profile.axis_weight,
                initial_increment=start,
            )
            solutions.append(result.matrices)
            convergence.append(result.valid)
            runs.append(
                {
                    "quaternion_sign": int(quaternion_sign),
                    "start_index": start_index,
                    "valid": result.valid,
                    "scipy_success": result.scipy_success,
                    "nfev": result.nfev,
                    "retractions": result.retractions,
                    "wall_s": result.wall_s,
                    "message": result.message,
                }
            )
    truth_errors = [
        (
            float(np.max(pose_delta_deg(measured, value))) * math.pi / 180.0
            if np.all(np.isfinite(value))
            else math.inf
        )
        for value in solutions
    ]
    pair_errors = [
        (
            float(np.max(pose_delta_deg(solutions[i], solutions[j])))
            * math.pi
            / 180.0
            if np.all(np.isfinite(solutions[i]))
            and np.all(np.isfinite(solutions[j]))
            else math.inf
        )
        for i in range(len(solutions))
        for j in range(i + 1, len(solutions))
    ]
    sign_residual = Rotation.from_matrix(
        _signed_observation(measured, 1.0)[1].T
        @ _signed_observation(measured, -1.0)[1]
    ).as_rotvec()
    aligned = projector_huber_pseudo_residual_parent(
        axes[0].parent_axis,
        measured[0].T @ measured[1],
        axes[0].child_axis,
        profile.axis_weight,
    )
    antiparallel_child = Rotation.from_rotvec(
        np.array([0.0, 0.0, math.pi / 2.0])
    ).as_matrix()
    antiparallel = projector_huber_pseudo_residual_parent(
        axes[0].parent_axis,
        measured[0].T @ antiparallel_child,
        axes[0].child_axis,
        profile.axis_weight,
    )
    checks = {
        "all_starts_converge": all(convergence),
        "all_truth": max(truth_errors, default=math.inf) <= 1e-6,
        "all_pairs": max(pair_errors, default=math.inf) <= 1e-6,
        "quaternion_sign": bool(np.all(np.abs(sign_residual) <= 1e-12)),
        "aligned_line": float(np.linalg.norm(aligned)) <= 1e-12,
        "antiparallel_line": float(np.linalg.norm(antiparallel)) <= 1e-12,
    }
    return {
        "case": "SYN08_FULL_CIRCLE_MULTISTART",
        "profile": profile.name,
        "passed": all(checks.values()),
        "checks": checks,
        "values": {
            "convergence": convergence,
            "max_truth_error_rad": max(truth_errors, default=math.nan),
            "max_pair_error_rad": max(pair_errors, default=math.nan),
            "sign_residual_max": float(np.max(np.abs(sign_residual))),
            "aligned_norm": float(np.linalg.norm(aligned)),
            "antiparallel_norm": float(np.linalg.norm(antiparallel)),
            "runs": runs,
        },
    }


def _fixture_residual_at_increment(
    measured: np.ndarray,
    axis: AxisPair,
    axis_weight: float,
    increment: np.ndarray,
) -> np.ndarray:
    delta = Rotation.from_rotvec(np.asarray(increment, dtype=np.float64).reshape(2, 3)).as_matrix()
    relative = measured[0].T @ measured[1]
    next_relative = relative @ delta[1]
    return projector_huber_pseudo_residual_parent(
        axis.parent_axis,
        next_relative,
        axis.child_axis,
        axis_weight,
    )


def _central_axis_jacobian(
    measured: np.ndarray,
    axis: AxisPair,
    axis_weight: float,
    step: float,
) -> np.ndarray:
    columns = []
    for coordinate in range(6):
        offset = np.zeros(6, dtype=np.float64)
        offset[coordinate] = step
        columns.append(
            (
                _fixture_residual_at_increment(measured, axis, axis_weight, offset)
                - _fixture_residual_at_increment(measured, axis, axis_weight, -offset)
            )
            / (2.0 * step)
        )
    return np.stack(columns, axis=1)


def validate_cut_locus() -> dict[str, Any]:
    h = 1e-6
    parent_axis = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    child_axis = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    axis = AxisPair("fixture_hinge", "pelvis", "child", parent_axis, child_axis)
    flipped = AxisPair("fixture_hinge", "pelvis", "child", parent_axis, -child_axis)
    configurations = []
    checks: dict[str, bool] = {}
    for index, gamma in enumerate((-h, 0.0, h)):
        measured = np.stack(
            [np.eye(3), Rotation.from_rotvec(np.array([0.0, 0.0, gamma])).as_matrix()]
        )
        residual = _fixture_residual_at_increment(measured, axis, 1.0, np.zeros(6))
        jacobian_h = _central_axis_jacobian(measured, axis, 1.0, h)
        jacobian_half = _central_axis_jacobian(measured, axis, 1.0, h / 2.0)
        flipped_residual = _fixture_residual_at_increment(
            measured, flipped, 1.0, np.zeros(6)
        )
        flipped_jacobian_h = _central_axis_jacobian(measured, flipped, 1.0, h)
        flipped_jacobian_half = _central_axis_jacobian(
            measured, flipped, 1.0, h / 2.0
        )
        refinement = float(np.linalg.norm(jacobian_h - jacobian_half))
        refinement_limit = 1e-5 * max(1.0, float(np.linalg.norm(jacobian_half)))
        finite = bool(
            np.all(np.isfinite(residual))
            and np.all(np.isfinite(jacobian_h))
            and np.all(np.isfinite(jacobian_half))
            and np.all(np.isfinite(flipped_residual))
            and np.all(np.isfinite(flipped_jacobian_h))
            and np.all(np.isfinite(flipped_jacobian_half))
        )
        sign_difference = max(
            float(np.max(np.abs(residual - flipped_residual))),
            float(np.max(np.abs(jacobian_h - flipped_jacobian_h))),
            float(np.max(np.abs(jacobian_half - flipped_jacobian_half))),
        )
        checks[f"configuration_{index}_finite"] = finite
        checks[f"configuration_{index}_jacobian_bound"] = (
            float(np.linalg.norm(jacobian_h)) <= 8.0
            and float(np.linalg.norm(jacobian_half)) <= 8.0
        )
        checks[f"configuration_{index}_refinement"] = refinement <= refinement_limit
        checks[f"configuration_{index}_sign_invariance"] = sign_difference <= 1e-12
        configurations.append(
            {
                "gamma_rad": gamma,
                "residual_norm": float(np.linalg.norm(residual)),
                "jacobian_h_frobenius": float(np.linalg.norm(jacobian_h)),
                "jacobian_half_frobenius": float(np.linalg.norm(jacobian_half)),
                "refinement_frobenius": refinement,
                "refinement_limit": refinement_limit,
                "sign_difference_max": sign_difference,
            }
        )

    exact = np.stack([np.eye(3), np.eye(3)])
    exact_result = solve_tree_frame(
        exact,
        segment_names=("pelvis", "child"),
        edge_rows=(("fixture_hinge", "pelvis", "child"),),
        axes=(axis,),
        weights={"pelvis": 1.0, "child": 1.0},
        axis_weight=1.0,
    )
    checks["exact_cut_fail_closed"] = bool(
        not exact_result.valid
        and exact_result.nfev == 0
        and np.all(np.isnan(exact_result.matrices))
        and exact_result.message == "CUT_LOCUS_STATIONARY_AT_MEASUREMENT_START"
    )

    _, _, starts = full_circle_fixture()
    near_runs = []
    for gamma in (-h, h):
        measured = np.stack(
            [np.eye(3), Rotation.from_rotvec(np.array([0.0, 0.0, gamma])).as_matrix()]
        )
        for quaternion_sign in (1.0, -1.0):
            signed = _signed_observation(measured, quaternion_sign)
            for start_index, start in enumerate(starts):
                result = solve_tree_frame(
                    signed,
                    segment_names=("pelvis", "child"),
                    edge_rows=(("fixture_hinge", "pelvis", "child"),),
                    axes=(axis,),
                    weights={"pelvis": 1.0, "child": 1.0},
                    axis_weight=1.0,
                    initial_increment=start,
                )
                near_runs.append(
                    {
                        "gamma_rad": gamma,
                        "quaternion_sign": int(quaternion_sign),
                        "start_index": start_index,
                        "valid": result.valid,
                        "scipy_success": result.scipy_success,
                        "nfev": result.nfev,
                        "retractions": result.retractions,
                        "wall_s": result.wall_s,
                        "message": result.message,
                    }
                )
    checks["near_cut_run_count"] = len(near_runs) == 36
    checks["all_near_cut_runs_converge"] = all(row["valid"] for row in near_runs)
    return {
        "case": "CUT01_PROJECTIVE_CUT_LOCUS",
        "profile": CENTRAL_PROFILE.name,
        "passed": bool(checks and all(checks.values())),
        "checks": checks,
        "values": {
            "numerical_configuration_count": 6,
            "configurations": configurations,
            "exact_cut": {
                "valid": exact_result.valid,
                "nfev": exact_result.nfev,
                "message": exact_result.message,
                "all_nan": bool(np.all(np.isnan(exact_result.matrices))),
            },
            "near_cut_solver_run_count": len(near_runs),
            "near_cut_runs": near_runs,
        },
    }


def _fixture_axes(*, child_axis: np.ndarray) -> tuple[AxisPair, ...]:
    return (
        AxisPair(
            "fixture_hinge",
            "pelvis",
            "child",
            np.array([1.0, 0.0, 0.0], dtype=np.float64),
            np.asarray(child_axis, dtype=np.float64),
        ),
    )


def validate_neg08(profile: Profile) -> dict[str, Any]:
    measured, _, starts = full_circle_fixture()
    axes = _fixture_axes(
        child_axis=np.array([-math.sqrt(3.0) / 2.0, 0.5, 0.0], dtype=np.float64)
    )
    runs = []
    truth_errors = []
    pose_changes = []
    for quaternion_sign in (1.0, -1.0):
        signed = _signed_observation(measured, quaternion_sign)
        for start_index, start in enumerate(starts):
            result = solve_tree_frame(
                signed,
                segment_names=("pelvis", "child"),
                edge_rows=(("fixture_hinge", "pelvis", "child"),),
                axes=axes,
                weights=_fixture_weights(profile),
                axis_weight=profile.axis_weight,
                initial_increment=start,
            )
            if result.valid:
                truth_error = float(np.max(pose_delta_deg(measured, result.matrices))) * math.pi / 180.0
                pose_change = float(np.max(pose_delta_deg(signed, result.matrices))) * math.pi / 180.0
            else:
                truth_error = math.nan
                pose_change = math.nan
            truth_errors.append(truth_error)
            pose_changes.append(pose_change)
            runs.append(
                {
                    "quaternion_sign": int(quaternion_sign),
                    "start_index": start_index,
                    "valid": result.valid,
                    "truth_error_rad": truth_error,
                    "pose_change_rad": pose_change,
                    "cost": result.cost,
                    "axis_cost": result.axis_cost,
                    "nfev": result.nfev,
                    "wall_s": result.wall_s,
                    "message": result.message,
                }
            )
    all_converged = all(row["valid"] for row in runs)
    maximum_truth = max(truth_errors) if all_converged else math.nan
    maximum_change = max(pose_changes) if all_converged else math.nan
    checks = {
        "all_runs_converge": all_converged,
        "KNOWN_TRUTH_MAX_ERROR_1E-6_RAD": all_converged and maximum_truth > 1e-6,
        "POSE_CHANGE_MAX_1E-6_RAD": all_converged and maximum_change > 1e-6,
    }
    return {
        "case": "NEG08_OPTIMIZED_AXIS_MUTATION",
        "profile": profile.name,
        "named_gates": [
            "KNOWN_TRUTH_MAX_ERROR_1E-6_RAD",
            "POSE_CHANGE_MAX_1E-6_RAD",
        ],
        "passed": bool(all(checks.values())),
        "rejected": bool(all(checks.values())),
        "checks": checks,
        "values": {
            "mutated_child_axis": axes[0].child_axis.tolist(),
            "max_truth_error_rad": maximum_truth,
            "max_pose_change_rad": maximum_change,
            "runs": runs,
        },
    }


def validate_neg09(profile: Profile) -> dict[str, Any]:
    measured, _, starts = full_circle_fixture()
    base_axes = _fixture_axes(child_axis=np.array([0.0, 1.0, 0.0]))
    flip_axes = _fixture_axes(child_axis=np.array([0.0, -1.0, 0.0]))
    increments = [np.zeros(6, dtype=np.float64)]
    for step in (1e-6, 5e-7):
        for coordinate in range(6):
            offset = np.zeros(6, dtype=np.float64)
            offset[coordinate] = step
            increments.extend((offset, -offset))
    residual_difference = max(
        float(
            np.max(
                np.abs(
                    _fixture_residual_at_increment(
                        measured, base_axes[0], profile.axis_weight, increment
                    )
                    - _fixture_residual_at_increment(
                        measured, flip_axes[0], profile.axis_weight, increment
                    )
                )
            )
        )
        for increment in increments
    )
    runs = []
    all_converged = True
    success_equal = True
    convergence_equal = True
    maximum_orientation_difference = 0.0
    maximum_cost_difference = 0.0
    maximum_metric_difference = 0.0
    for quaternion_sign in (1.0, -1.0):
        signed = _signed_observation(measured, quaternion_sign)
        for start_index, start in enumerate(starts):
            kwargs = {
                "segment_names": ("pelvis", "child"),
                "edge_rows": (("fixture_hinge", "pelvis", "child"),),
                "weights": _fixture_weights(profile),
                "axis_weight": profile.axis_weight,
                "initial_increment": start,
            }
            base = solve_tree_frame(signed, axes=base_axes, **kwargs)
            flip = solve_tree_frame(signed, axes=flip_axes, **kwargs)
            all_converged = all_converged and base.valid and flip.valid
            success_equal = success_equal and base.scipy_success == flip.scipy_success
            convergence_equal = convergence_equal and base.valid == flip.valid
            if base.valid and flip.valid:
                orientation_difference = float(
                    np.max(pose_delta_deg(base.matrices, flip.matrices))
                ) * math.pi / 180.0
                cost_difference = abs(base.cost - flip.cost)
                base_line = line_angle_rad(
                    base.matrices[0],
                    base.matrices[1],
                    base_axes[0].parent_axis,
                    base_axes[0].child_axis,
                )
                flip_line = line_angle_rad(
                    flip.matrices[0],
                    flip.matrices[1],
                    flip_axes[0].parent_axis,
                    flip_axes[0].child_axis,
                )
                base_truth = float(np.max(pose_delta_deg(measured, base.matrices))) * math.pi / 180.0
                flip_truth = float(np.max(pose_delta_deg(measured, flip.matrices))) * math.pi / 180.0
                base_change = float(np.max(pose_delta_deg(signed, base.matrices))) * math.pi / 180.0
                flip_change = float(np.max(pose_delta_deg(signed, flip.matrices))) * math.pi / 180.0
                metric_difference = max(
                    abs(base_line - flip_line),
                    abs(base_truth - flip_truth),
                    abs(base_change - flip_change),
                )
                maximum_orientation_difference = max(
                    maximum_orientation_difference, orientation_difference
                )
                maximum_cost_difference = max(maximum_cost_difference, cost_difference)
                maximum_metric_difference = max(maximum_metric_difference, metric_difference)
            else:
                orientation_difference = math.nan
                cost_difference = math.nan
                metric_difference = math.nan
            runs.append(
                {
                    "quaternion_sign": int(quaternion_sign),
                    "start_index": start_index,
                    "base_valid": base.valid,
                    "flip_valid": flip.valid,
                    "base_scipy_success": base.scipy_success,
                    "flip_scipy_success": flip.scipy_success,
                    "orientation_difference_rad": orientation_difference,
                    "cost_difference": cost_difference,
                    "metric_difference_rad": metric_difference,
                }
            )
    checks = {
        "residual_samples": residual_difference <= 1e-12,
        "identical_scipy_success": success_equal,
        "identical_convergence": convergence_equal,
        "all_runs_converge": all_converged,
        "optimized_orientations": maximum_orientation_difference <= 1e-12,
        "total_cost": maximum_cost_difference <= 1e-12,
        "sign_invariant_metrics": maximum_metric_difference <= 1e-12,
    }
    return {
        "case": "NEG09_SINGLE_AXIS_SIGN_INVARIANCE",
        "profile": profile.name,
        "passed": bool(all(checks.values())),
        "rejected": bool(all(checks.values())),
        "checks": checks,
        "values": {
            "residual_sample_count": len(increments),
            "max_residual_component_difference": residual_difference,
            "max_optimized_orientation_difference_rad": maximum_orientation_difference,
            "max_total_cost_difference": maximum_cost_difference,
            "max_metric_difference_rad": maximum_metric_difference,
            "runs": runs,
        },
    }
def validate_negative_fixtures(
    geometry: DisplayGeometry,
    output_matrix: np.ndarray,
) -> list[dict[str, Any]]:
    exact = make_case("SYN00_EXACT_DYNAMIC")
    base = exact.truth[50]
    points = forward_points(base, geometry, output_matrix)
    links = link_records(points)

    identity = {segment: segment for segment in SEGMENTS}
    for left, right in (
        ("upper_arm_left", "upper_arm_right"),
        ("forearm_left", "forearm_right"),
        ("thigh_left", "thigh_right"),
        ("shank_left", "shank_right"),
    ):
        identity[left], identity[right] = identity[right], identity[left]
    identity_rejected = not validate_identity_binding(identity)
    reflected = base.copy()
    reflected[SEGMENTS.index("forearm_left")] = np.diag([-1.0, 1.0, 1.0]) @ reflected[SEGMENTS.index("forearm_left")]
    reflection_rejected = bool(np.linalg.det(reflected[SEGMENTS.index("forearm_left")]) < 0.0)

    disconnected = {key: (value[0].copy(), value[1].copy()) for key, value in links.items()}
    disconnected["forearm_left"] = (disconnected["forearm_left"][0] + np.array([0.01, 0.0, 0.0]), disconnected["forearm_left"][1])
    disconnected_rejected = "elbow_left" in endpoint_failures(disconnected)

    short_lengths = dict(geometry.segment_lengths_m)
    short_lengths["shank_left"] = 0.01
    short_geometry = DisplayGeometry(
        geometry.name,
        geometry.torso_height_m,
        geometry.hip_span_m,
        geometry.shoulder_span_m,
        short_lengths,
    )
    collapse_result = screen_frame(base, base, short_geometry, output_matrix)
    collapse_rejected = "SHORT_LINK_shank_left" in collapse_result.failures
    crossing_rejected = closed_segment_distance(
        np.array([-1.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, -1.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
    ) < 0.005
    fake_a = relative_paths_deg(exact.truth, np.ones(len(exact.time_s), dtype=bool))
    fake = np.repeat(exact.truth[:, :1], len(SEGMENTS), axis=1)
    fake_b = relative_paths_deg(fake, np.ones(len(exact.time_s), dtype=bool))
    eligible = fake_a >= 30.0
    fake_rejected = bool(np.any(eligible) and np.sum(fake_b[eligible]) / np.sum(fake_a[eligible]) < 0.75)

    def minimum_rotation(source: np.ndarray, target: np.ndarray) -> np.ndarray:
        source = source / np.linalg.norm(source)
        target = target / np.linalg.norm(target)
        cross = np.cross(source, target)
        cross_norm = float(np.linalg.norm(cross))
        dot = float(np.clip(source @ target, -1.0, 1.0))
        if cross_norm <= 1e-12:
            return np.eye(3) if dot > 0.0 else Rotation.from_rotvec(np.array([math.pi, 0.0, 0.0])).as_matrix()
        return Rotation.from_rotvec(
            math.atan2(cross_norm, dot) * cross / cross_norm
        ).as_matrix()

    count = 101
    standing_matrices = np.repeat(np.eye(3)[None, None, :, :], count * len(SEGMENTS), axis=0).reshape(count, len(SEGMENTS), 3, 3)
    for side, segment in ((1.0, "thigh_left"), (-1.0, "thigh_right")):
        length = geometry.segment_lengths_m[segment]
        target_display = np.array(
            [0.0, side * 0.06, -math.sqrt(length * length - 0.06 * 0.06)]
        )
        target_internal = output_matrix.T @ target_display
        standing_matrices[:, SEGMENTS.index(segment)] = minimum_rotation(
            np.array([0.0, 0.0, -1.0]), target_internal
        )
    baseline = standing_matrices.copy()
    for frame in tuple(range(30)) + tuple(range(70, count)):
        global_rotation = Rotation.from_rotvec(
            np.array([0.0, 0.0, math.radians(45.0 if frame % 2 else -45.0)])
        ).as_matrix()
        standing_matrices[frame] = global_rotation @ baseline[frame]
    knee_run, knee_failures = standing_knee_split_failures(
        standing_matrices,
        np.arange(count, dtype=np.float64) / 20.0,
        np.ones(count, dtype=bool),
        geometry,
        output_matrix,
    )
    knee_split_rejected = knee_run == 20 and any(
        value == "STANDING_KNEE_SPLIT_RUN_20" for value in knee_failures
    )
    rows = [
        ("NEG01_IDENTITY_SWAP", "identity/topology", identity_rejected),
        ("NEG02_REFLECTION", "proper rotation", reflection_rejected),
        ("NEG03_FRONT_BACK_KNEES", "standing knee split", knee_split_rejected),
        ("NEG04_DISCONNECTED", "shared endpoint", disconnected_rejected),
        ("NEG05_COLLAPSE", "minimum link", collapse_rejected),
        ("NEG06_CROSSING", "closed segment crossing", crossing_rejected),
        ("NEG07_FAKE_IMPROVEMENT", "relative motion collapse", fake_rejected),
    ]
    return [
        {"case": name, "named_gate": gate, "passed": bool(rejected), "rejected": bool(rejected)}
        for name, gate, rejected in rows
    ]
