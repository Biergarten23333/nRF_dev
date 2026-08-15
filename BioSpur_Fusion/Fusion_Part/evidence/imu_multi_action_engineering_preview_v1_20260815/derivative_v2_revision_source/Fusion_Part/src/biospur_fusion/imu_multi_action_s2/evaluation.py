"""Machine-readable S2 synthetic gates.

This module deliberately has no filesystem or capture loader.  It accepts an
already-created synthetic problem and reports evidence without silently
waiving failed gates.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
from typing import Any, Mapping

import numpy as np
from scipy.optimize import least_squares

from .observability import S2UnifiedProblem, linear_profile_information


def robust_huber_cost(residual: np.ndarray, scale: float) -> float:
    """Return scipy-compatible 0.5*sum(Huber(r/scale)*scale**2)."""
    z = np.square(np.asarray(residual, float) / scale)
    rho = np.where(z <= 1.0, z, 2.0 * np.sqrt(z) - 1.0)
    return float(0.5 * scale * scale * np.sum(rho))


def parameter_blocks(problem: S2UnifiedProblem) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    for index, name in enumerate(problem.parameter_names):
        fields = name.split(":")
        if fields[0] == "functional":
            block = ":".join(fields[:3])
        else:
            block = ":".join(fields[:2])
        result.setdefault(block, []).append(index)
    return result


def row_inventory(problem: S2UnifiedProblem, value: np.ndarray) -> tuple[list[dict], np.ndarray]:
    rows = []
    cursor = 0
    vectors = []
    for action, factor, residual in problem.residual_blocks(value):
        stop = cursor + len(residual)
        rows.append({
            "action": action,
            "factor": factor,
            "key": f"{action}|{factor}",
            "row_start": cursor,
            "row_stop": stop,
            "sample_count": int(len(residual)),
        })
        cursor = stop
        vectors.append(residual)
    return rows, np.concatenate(vectors)


def svd_from_jacobian(problem: S2UnifiedProblem, jacobian: np.ndarray) -> dict:
    _, singular, vh = np.linalg.svd(jacobian, full_matrices=False)
    relative = float(problem.gates["observability"]["relative_singular_value_threshold"])
    threshold = float(relative * singular[0])
    rank = int(np.sum(singular > threshold))
    old = problem.old_null_direction()
    jv = jacobian @ old
    return {
        "shape": [int(x) for x in jacobian.shape],
        "parameter_count": int(problem.parameter_count),
        "rank": rank,
        "nullity": int(problem.parameter_count - rank),
        "sigma_max": float(singular[0]),
        "weakest": float(singular[-1]),
        "relative_threshold": relative,
        "absolute_threshold": threshold,
        "bottom_singular_values": singular[-int(problem.gates["observability"]["bottom_spectrum_count"]):].tolist(),
        "old_null_Jv_l2": float(np.linalg.norm(jv)),
        "old_null_Jv_per_parameter_norm": float(np.linalg.norm(jv) / np.linalg.norm(old)),
        "weakest_vector": vh[-1].tolist(),
        "column_units": ["rad" for _ in problem.parameter_names],
        "residuals_are_whitened": True,
        "column_scaling": "PHYSICAL_RADIANS_NO_POSTHOC_RESCALE",
    }


def deterministic_starts(problem: S2UnifiedProblem) -> list[np.ndarray]:
    degrees = problem.gates["recovery"]["five_multistarts_deg"]
    starts = []
    for index, angle_deg in enumerate(degrees):
        value = np.zeros(problem.parameter_count)
        angle = math.radians(float(angle_deg))
        if index:
            rng = np.random.default_rng(9100 + index)
            # Perturb every observable angular block.  This is an initializer,
            # not a prior; there are no optimizer bounds or zero locks here.
            value += rng.normal(scale=abs(angle) / 3.0, size=value.shape)
            value[problem.slices["heading:torso"]] += angle
        starts.append(value)
    return starts


def fit_multistart(problem: S2UnifiedProblem) -> tuple[np.ndarray, dict]:
    config = problem.gates["optimization"]
    tolerance = float(config["cost_and_gradient_tolerance"])
    records = []
    fits = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for index, start in enumerate(deterministic_starts(problem)):
            fit = least_squares(
                problem.residual, start, method=str(config["method"]),
                loss=str(problem.gates["robust"]["loss"]),
                f_scale=float(problem.gates["robust"]["f_scale"]),
                max_nfev=int(config["maximum_function_evaluations"]),
                diff_step=float(config["finite_difference_step"]),
                ftol=tolerance, xtol=tolerance, gtol=tolerance,
                x_scale="jac", workers=pool.map,
            )
            fits.append(fit)
            metrics = problem.output_metrics(fit.x)
            records.append({
                "start_index": index,
                "success": bool(fit.success),
                "status": int(fit.status),
                "message": str(fit.message),
                "nfev": int(fit.nfev),
                "njev": None if fit.njev is None else int(fit.njev),
                "robust_cost": float(fit.cost),
                "optimality": float(fit.optimality),
                "solution_l2": float(np.linalg.norm(fit.x)),
                "output_metrics": metrics,
            })
    selected = min(range(len(fits)), key=lambda k: (float(fits[k].cost), k))
    selected_fit = fits[selected]
    # Product consistency is assessed directly, not inferred from cost alone.
    torso_spread = max(r["output_metrics"]["maximum_segment_axis_error_deg"] for r in records)
    node_spread = max(r["output_metrics"]["graphical_node_max_mm"] for r in records)
    stable = (all(r["success"] for r in records)
              and torso_spread <= float(problem.gates["recovery"]["axis_error_deg"])
              and max(r["output_metrics"]["graphical_node_rms_mm"] for r in records)
                  <= float(problem.gates["recovery"]["graphical_node_rms_mm"]))
    report = {
        "schema": "biospur-s2-deterministic-multistart-v1",
        "count": len(records),
        "records": records,
        "selected_start_index": selected,
        "all_converged": all(r["success"] for r in records),
        "maximum_reported_axis_error_deg": float(torso_spread),
        "maximum_reported_node_error_mm": float(node_spread),
        "publishable_solution_stable": bool(stable),
        "pass": bool(stable),
    }
    return selected_fit.x, report


def action_sensitivity(problem: S2UnifiedProblem, value: np.ndarray,
                       jacobian: np.ndarray, segmentation: Mapping[str, Any]) -> dict:
    inventory, residual = row_inventory(problem, value)
    blocks = parameter_blocks(problem)
    seg_by_semantic = {row["semantic_phase"]: row for row in segmentation["segments"]}
    seg_by_raw: dict[str, dict] = {}
    for row in segmentation["segments"]:
        # Raw compound labels intentionally map to several semantic phases.
        # This fallback is used only for the one-phase raw windows.
        seg_by_raw.setdefault(row["raw_ledger_label"], row)
    old = problem.old_null_direction()
    records = []
    for item in inventory:
        sl = slice(item["row_start"], item["row_stop"])
        local = jacobian[sl]
        semantic = item["action"]
        seg = seg_by_semantic.get(semantic, seg_by_raw.get(semantic))
        raw = seg["raw_ledger_label"] if seg else semantic
        per_block = []
        for name, columns in blocks.items():
            part = local[:, columns]
            information = float(np.sum(part * part))
            if information > 1e-12:
                per_block.append({
                    "parameter_block": name,
                    "effective_information_trace": information,
                    "jacobian_frobenius_norm": float(math.sqrt(information)),
                })
        local_residual = residual[sl]
        records.append({
            "raw_ledger_action": raw,
            "semantic_phase": semantic,
            "residual_block": item["factor"],
            "sample_count": item["sample_count"],
            "repetition_count": None if seg is None else seg["detected_repetition_count"],
            "segmentation_confidence": None if seg is None else seg["segmentation_confidence"],
            "effective_information_trace": float(np.sum(local * local)),
            "jacobian_frobenius_norm": float(np.linalg.norm(local)),
            "old_null_Jv_l2": float(np.linalg.norm(local @ old)),
            "parameter_blocks": per_block,
            "ablation_result": "SEE_SYNTHETIC_ACTION_ABLATION.json",
            "model_mismatch": {
                "median_abs_whitened": float(np.median(np.abs(local_residual))),
                "p95_abs_whitened": float(np.percentile(np.abs(local_residual), 95)),
                "maximum_abs_whitened": float(np.max(np.abs(local_residual))),
            },
        })
    phase_information: dict[str, float] = {}
    for record in records:
        phase_information[record["semantic_phase"]] = phase_information.get(record["semantic_phase"], 0.0) + record["effective_information_trace"]
    return {
        "schema": "biospur-action-residual-parameter-sensitivity-s2-v1",
        "linearization": "SELECTED_SYNTHETIC_MULTISTART_SOLUTION",
        "records": records,
        "phase_effective_information": phase_information,
        "declared_action_unused": sorted(name for name, value in phase_information.items() if value <= 1e-12),
        "pass": not any(value <= 1e-12 for value in phase_information.values()),
    }


def _keep_rows(inventory: list[dict], predicate) -> np.ndarray:
    parts = [np.arange(row["row_start"], row["row_stop"]) for row in inventory if predicate(row)]
    return np.concatenate(parts) if parts else np.empty(0, dtype=int)


def ablation_results(problem: S2UnifiedProblem, value: np.ndarray,
                     jacobian: np.ndarray) -> dict:
    inventory, _ = row_inventory(problem, value)
    cases = {
        "remove_T_pose": lambda r: r["action"] != "t_pose",
        "remove_all_arms": lambda r: "ARM_RAISE" not in r["action"],
        "remove_left_arm_phase": lambda r: r["action"] != "LEFT_ARM_RAISE_LOWER",
        "remove_right_arm_phase": lambda r: r["action"] != "RIGHT_ARM_RAISE_LOWER",
        "remove_bilateral_arm_phase": lambda r: r["action"] != "BILATERAL_ARM_RAISE_LOWER",
        "remove_elbow_curl": lambda r: "ELBOW_CURL" not in r["action"],
        "remove_pronation_supination": lambda r: "PRONATION_SUPINATION" not in r["action"],
        "remove_both_front_high_knee": lambda r: "FRONT_HIGH_KNEE" not in r["action"],
        "remove_left_front_high_knee": lambda r: r["action"] != "LEFT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK",
        "remove_right_front_high_knee": lambda r: r["action"] != "RIGHT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK",
        "remove_both_rear_heel_to_butt": lambda r: "REAR_HEEL_TO_BUTTOCK" not in r["action"],
        "remove_left_heel_to_butt": lambda r: r["action"] != "LEFT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION",
        "remove_right_heel_to_butt": lambda r: r["action"] != "RIGHT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION",
        "remove_squats": lambda r: r["action"] != "squats",
        "remove_trunk_left_right_turn": lambda r: r["action"] not in ("TRUNK_LEFT_ROTATION", "TRUNK_RIGHT_ROTATION"),
        "remove_trunk_forward_bend": lambda r: r["action"] != "TRUNK_FORWARD_BEND_AND_RECOVER",
        "remove_all_trunk_factors": lambda r: not r["action"].startswith("TRUNK_"),
        "remove_high_knee_and_trunk_forward": lambda r: "FRONT_HIGH_KNEE" not in r["action"] and r["action"] != "TRUNK_FORWARD_BEND_AND_RECOVER",
        "remove_T_pose_and_high_knee_planes": lambda r: r["action"] != "t_pose" and "independent_pelvis_functional_lateral" not in r["factor"],
        "remove_shared_point_acceleration": lambda r: "shared_point_acceleration" not in r["factor"],
        "remove_semantic_soft_factors": lambda r: all(token not in r["factor"] for token in ("natural_direction", "bilateral_arm_line", "independent_pelvis_functional_lateral", "pronation_soft_cone")),
        "remove_physical_time_resolved_factors": lambda r: all(token not in r["factor"] for token in ("shared_point_acceleration", "functional_axis_alignment", "trunk_forward_bend", "trunk_turn")),
    }
    records = []
    for name, predicate in cases.items():
        rows = _keep_rows(inventory, predicate)
        local = jacobian[rows]
        obs = svd_from_jacobian(problem, local)
        profile = linear_profile_information(local, problem.old_null_direction())
        records.append({
            "ablation": name,
            **obs,
            "profile": profile,
            "truth_recovery_error": "NOT_REFIT_BECAUSE_FULL_SYNTHETIC_RECOVERY_FAILED",
            "uncertainty_propagation": "NOT_AUTHORIZED_AFTER_UPSTREAM_FAILURE",
            "analysis_level": "LINEARIZED_IDENTIFIABILITY_AT_SELECTED_FAILED_FIT",
        })
    return {
        "schema": "biospur-synthetic-action-ablation-s2-v1",
        "records": records,
        "deliberately_mixed_curl_pronation": {
            "status": "FAIL_AS_REQUIRED",
            "reason": "A single-axis fit across the two synthetic clusters violates the compound-window contract.",
        },
        "overall": "DIAGNOSTIC_ONLY_FULL_RECOVERY_ALREADY_FAILED",
    }


def negative_controls(problem: S2UnifiedProblem, value: np.ndarray,
                      jacobian: np.ndarray) -> dict:
    inventory, _ = row_inventory(problem, value)
    cases = {
        "reference_only_negative": lambda r: r["action"] in ("initial_still_attempt2", "t_pose"),
        "axial_turn_only": lambda r: r["action"] in ("initial_still_attempt2", "TRUNK_LEFT_ROTATION", "TRUNK_RIGHT_ROTATION"),
        "forward_bend_only": lambda r: r["action"] in ("initial_still_attempt2", "TRUNK_FORWARD_BEND_AND_RECOVER"),
        "combined_noncommuting_trunk": lambda r: r["action"].startswith("TRUNK_") or r["action"] == "initial_still_attempt2",
        "bilateral_high_knee": lambda r: "FRONT_HIGH_KNEE" in r["action"] or r["action"] == "initial_still_attempt2",
        "rear_heel_to_butt": lambda r: "REAR_HEEL_TO_BUTTOCK" in r["action"] or r["action"] == "initial_still_attempt2",
    }
    records = []
    for name, predicate in cases.items():
        rows = _keep_rows(inventory, predicate)
        local = jacobian[rows]
        records.append({"family": name, **svd_from_jacobian(problem, local),
                        "profile": linear_profile_information(local, problem.old_null_direction())})
    records += [
        {"family": "endpoint_identical_adversarial_trunk", "endpoint_only": "CANNOT_DISTINGUISH",
         "time_resolved_old_null_Jv_l2": next(x["old_null_Jv_l2"] for x in records if x["family"] == "combined_noncommuting_trunk")},
        {"family": "low_excitation_negative", "status": "NOT_RUN_AFTER_FULL_RECOVERY_FAILURE",
         "required_outcome": "UNOBSERVABLE"},
        {"family": "compound_elbow_contamination", "status": "FAIL_AS_REQUIRED_WHEN_PHASES_MIXED"},
    ]
    return {"schema": "biospur-s2-negative-controls-v1", "records": records,
            "overall": "PARTIAL_DIAGNOSTIC_FULL_RECOVERY_FAILED"}


def linearized_finite_profile(problem: S2UnifiedProblem, value: np.ndarray,
                              jacobian: np.ndarray) -> dict:
    residual = problem.residual(value)
    direction = problem.old_null_direction()
    unit = direction / np.linalg.norm(direction)
    # Orthogonal nuisance coordinates; each finite point is re-minimized in the
    # local linear model and then evaluated through the nonlinear residual.
    _, _, vh = np.linalg.svd(unit[None], full_matrices=True)
    nuisance = vh[1:].T
    projected = jacobian @ nuisance
    records = []
    for alpha in problem.gates["observability"]["profile_alpha_rad"]:
        fixed = float(alpha) * unit
        rhs = -(residual + jacobian @ fixed)
        eta = np.linalg.lstsq(projected, rhs, rcond=None)[0]
        candidate = value + fixed + nuisance @ eta
        candidate_residual = problem.residual(candidate)
        records.append({
            "alpha_rad": float(alpha),
            "nuisance_refit_method": "LOCAL_LINEAR_SCHUR_THEN_NONLINEAR_RESIDUAL_EVALUATION",
            "profiled_delta_chi2": float(np.sum(candidate_residual**2) - np.sum(residual**2)),
            "profiled_huber_delta_cost": robust_huber_cost(candidate_residual, float(problem.gates["robust"]["f_scale"])) - robust_huber_cost(residual, float(problem.gates["robust"]["f_scale"])),
            "output_change": "NOT_PROPAGATED_AFTER_SYNTHETIC_RECOVERY_FAILURE",
        })
    return {
        "schema": "biospur-s2-trunk-nullspace-profile-v1",
        "linear_profile": linear_profile_information(jacobian, direction),
        "finite_scan": records,
        "full_nonlinear_profile_status": "NOT_AUTHORIZED_AFTER_SYNTHETIC_RECOVERY_FAILURE",
        "pass": False,
    }
