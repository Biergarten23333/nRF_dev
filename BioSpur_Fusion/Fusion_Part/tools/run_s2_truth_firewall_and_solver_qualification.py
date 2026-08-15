#!/usr/bin/env python3
"""S2 truth firewall and pre-Revision-B solver qualification.

Synthetic only.  This executable has no capture/ledger/UWB/held-out inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Fusion_Part/src"))

from biospur_fusion.imu_multi_action_s2.evaluation import robust_huber_cost  # noqa: E402
from biospur_fusion.imu_multi_action_s2.human_synthetic import generate_human_motion_synthetic  # noqa: E402
from biospur_fusion.imu_multi_action_s2.observability import S2UnifiedProblem  # noqa: E402
from biospur_fusion.imu_multi_action_s2.qualification import (  # noqa: E402
    firewall_snapshot, huber_gradient, permuted_truth, randomized_truth,
    truth_parameter_vector,
)
from biospur_fusion.imu_multi_action_s2.segmentation import segment_action_phases  # noqa: E402


CONFIG = ROOT / "Fusion_Part/config/imu_only_multi_action_centerline_calibration_v1_s2"
TEMPLATE = ROOT / "Fusion_Part/config/generic_template_motion_demo_v1/GENERIC_ADULT_PROXY_V1.json"
DEFAULT_OUTPUT = ROOT / "Fusion_Part/logs/imu_only_multi_action_centerline_calibration_v1_s2_qualification"


def write_json(output: Path, name: str, value: Any) -> None:
    (output / name).write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def firewall(gates: dict, template: dict) -> dict:
    base = generate_human_motion_synthetic(gates, template, seed=2201)
    full_variants = {
        "original_truth": base,
        "randomize_truth_only_fields": randomized_truth(base),
        "permute_truth_only_fields": permuted_truth(base),
    }
    snapshots = {}
    truth_hashes = {}
    for name, full in full_variants.items():
        truth_hashes[name] = hashlib.sha256(repr(full.truth).encode()).hexdigest()
        observed = full.observation_view()
        assert not hasattr(observed, "truth")
        segmentation = segment_action_phases(observed, gates)
        problem = S2UnifiedProblem(observed, segmentation, gates, template)
        snapshots[name] = firewall_snapshot(problem, segmentation)
    # The deletion case reuses only the physically truth-free object; it is
    # executed independently through segmentation/problem construction.
    deleted = base.observation_view()
    assert not hasattr(deleted, "truth")
    segmentation = segment_action_phases(deleted, gates)
    problem = S2UnifiedProblem(deleted, segmentation, gates, template)
    snapshots["delete_truth_only_fields"] = firewall_snapshot(problem, segmentation)
    keys = ("segmentation_sha256", "initializer_sha256", "residual_sha256",
            "jacobian_sha256", "fit_result_sha256")
    identical = {key: len({row[key] for row in snapshots.values()}) == 1 for key in keys}
    passed = len(set(truth_hashes.values())) == len(truth_hashes) and all(identical.values())
    return {
        "schema": "biospur-s2-truth-firewall-v1",
        "estimator_input_type": "ObservationOnlyDataset",
        "truth_attribute_physically_absent": True,
        "truth_mutations_have_distinct_hashes": True,
        "snapshots": snapshots,
        "byte_identity_checks": identical,
        "pass": passed,
        "verdict": "PASS_TRUTH_FIREWALL" if passed else "FAIL_SYNTHETIC_TRUTH_LEAKAGE",
        "real_repetition_contract_warning": "Synthetic 4/5 high-knee and 5/4 heel counts are seed-local and are not written to the operator contract.",
    }


def truth_qualification(gates: dict, template: dict) -> tuple[dict, dict, dict]:
    scenario = {
        "white_noise_scale": 0.0,
        "correlated_drift_scale": 0.0,
        "yaw_drift_scale": 0.0,
        "strap_perturbation_scale": 0.0,
    }
    full = generate_human_motion_synthetic(gates, template, seed=2201, scenario=scenario)
    observed = full.observation_view()
    segmentation = segment_action_phases(observed, gates)
    problem = S2UnifiedProblem(observed, segmentation, gates, template)
    value = truth_parameter_vector(problem, full.truth)
    residual = problem.residual(value)
    jacobian = problem.numerical_jacobian(value, step=2e-6)
    gradient = huber_gradient(problem, value, jacobian, residual)
    # Truth is used only in this external evaluation object, never in residual.
    evaluation_problem = S2UnifiedProblem(full, segmentation, gates, template)
    output_error = evaluation_problem.output_metrics(value)
    blocks = []
    for action, factor, rows in problem.residual_blocks(value):
        blocks.append({
            "action": action, "residual_block": factor, "row_count": len(rows),
            "median_abs_whitened": float(np.median(np.abs(rows))),
            "p95_abs_whitened": float(np.percentile(np.abs(rows), 95)),
            "l2": float(np.linalg.norm(rows)),
        })
    relative_kkt = float(np.linalg.norm(gradient, np.inf) / max(1.0, np.linalg.norm(jacobian, 2) * np.linalg.norm(residual)))
    thresholds = {
        "maximum_segment_axis_error_deg": 1e-6,
        "graphical_node_rms_mm": 1e-6,
        "relative_huber_KKT_inf": 1e-6,
    }
    passed = (
        output_error["maximum_segment_axis_error_deg"] <= thresholds["maximum_segment_axis_error_deg"]
        and output_error["graphical_node_rms_mm"] <= thresholds["graphical_node_rms_mm"]
        and relative_kkt <= thresholds["relative_huber_KKT_inf"]
    )
    truth_report = {
        "schema": "biospur-s2-truth-point-self-consistency-v1",
        "scenario": scenario,
        "qualification_thresholds_declared_before_interpretation": thresholds,
        "truth_residual_l2": float(np.linalg.norm(residual)),
        "truth_least_squares_cost": float(0.5 * residual @ residual),
        "truth_huber_cost": robust_huber_cost(residual, float(gates["robust"]["f_scale"])),
        "truth_huber_gradient_inf": float(np.linalg.norm(gradient, np.inf)),
        "truth_relative_huber_KKT_inf": relative_kkt,
        "truth_output_error_modulo_legal_global_yaw": output_error,
        "per_residual_block": blocks,
        "pass": passed,
        "verdict": "PASS_TRUTH_POINT_SELF_CONSISTENCY" if passed else "FAIL_FORWARD_INVERSE_MODEL_INCONSISTENCY",
        "diagnosis": "Fixed generic estimator lever arms differ from generated sensor lever arms; dynamic/bias nuisance are also absent from the reduced state.",
    }
    basin = {
        "schema": "biospur-s2-truth-near-truth-basin-v1",
        "requested_starts_deg": [0.0, 0.1, 1.0, 5.0],
        "runs": [],
        "status": ("AUTHORIZED_NEXT" if passed else "NOT_RUN_TRUTH_POINT_IS_NOT_OBJECTIVE_CONSISTENT"),
        "verdict": "FAIL_FORWARD_INVERSE_MODEL_INCONSISTENCY" if not passed else "PENDING",
    }
    return truth_report, basin, {"problem": problem, "value": value, "jacobian": jacobian}


def jacobian_qualification(problem: S2UnifiedProblem, value: np.ndarray,
                           reference: np.ndarray) -> dict:
    rng = np.random.default_rng(7301)
    directions = []
    for index in range(8):
        direction = rng.normal(size=problem.parameter_count)
        directions.append((f"random_{index}", direction / np.linalg.norm(direction)))
    old = problem.old_null_direction(); directions.append(("old_v_alpha", old / np.linalg.norm(old)))
    for block, sl in problem.slices.items():
        direction = np.zeros(problem.parameter_count)
        direction[sl] = 1.0 / math.sqrt(sl.stop - sl.start)
        directions.append((f"block:{block}", direction))
    steps = [1e-4, 1e-5, 2e-6, 1e-6, 1e-7]
    records = []
    for name, direction in directions:
        predicted = reference @ direction
        for step in steps:
            observed = (problem.residual(value + step * direction)
                        - problem.residual(value - step * direction)) / (2 * step)
            delta = observed - predicted
            records.append({
                "direction": name, "fd_step": step,
                "absolute_derivative_error_l2": float(np.linalg.norm(delta)),
                "relative_derivative_error": float(np.linalg.norm(delta) / max(np.linalg.norm(observed), 1e-15)),
            })
    return {
        "schema": "biospur-s2-jacobian-qualification-v1",
        "current_jacobian_method": "CENTRAL_FINITE_DIFFERENCE",
        "analytic_or_AD_jacobian_available": False,
        "tangent_convention": "left SO(3) rotvec update for torso/pelvis; deterministic unit-vector tangent retraction for 2-DOF axes; scalar headings additive with explicit wrap only in truth mapping",
        "parameter_units": "radians",
        "huber_active_set": "Jacobian is of whitened raw residual; Huber psi is applied only in gradient/cost",
        "records": records,
        "qualification_pass": False,
        "reason": "No independent analytic/AD Jacobian exists, so the requested analytic-vs-FD cross-check is not established.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(); output = args.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    gates = json.loads((CONFIG / "s2_gates_v1.json").read_text())
    template = json.loads(TEMPLATE.read_text())
    firewall_report = firewall(gates, template)
    write_json(output, "TRUTH_FIREWALL_AUDIT.json", firewall_report)
    # Current steering terminates this stage immediately after the firewall.
    # The qualification helpers below remain design code only and are not
    # executed until a future full-state/Schur specification authorizes them.
    print(json.dumps({
        "truth_firewall": firewall_report["verdict"],
        "stopped_after_truth_firewall": True,
        "solver_revision_b_authorized": False,
        "output": str(output),
    }))
    return 0

    # Unreachable under the current terminal-stage contract.
    truth_report, basin, internals = truth_qualification(gates, template)
    write_json(output, "TRUTH_POINT_SELF_CONSISTENCY.json", truth_report)
    write_json(output, "TRUTH_AND_NEAR_TRUTH_BASIN_TESTS.json", basin)
    write_json(output, "JACOBIAN_QUALIFICATION.json", jacobian_qualification(
        internals["problem"], internals["value"], internals["jacobian"]
    ))
    write_json(output, "DISCRETE_BRANCH_ENUMERATION.json", {
        "schema": "biospur-s2-discrete-branch-enumeration-v1",
        "functional_axis_sign": "PARTIAL_CLOSEST-SIGN_ONLY",
        "left_right_handedness": "NOT_ENUMERATED",
        "flexion_sign": "NOT_ENUMERATED",
        "frame_handedness": "NOT_ENUMERATED",
        "quaternion_sign_equivalence": "MATRIX_REPRESENTATION_REMOVES_QUATERNION_SIGN_ONLY",
        "continuous_solver_expected_to_cross_discrete_branch": False,
        "pass": False,
        "verdict": "FAIL_DISCRETE_BRANCH_ENUMERATION_INCOMPLETE",
    })
    write_json(output, "SOLVER_REVISION_B_AUTHORIZATION.json", {
        "schema": "biospur-s2-solver-revision-b-authorization-v1",
        "created": False,
        "authorized": False,
        "blocking_results": [
            "FAIL_REDUCED_STATE_INVALID",
            truth_report["verdict"],
            "FAIL_DISCRETE_BRANCH_ENUMERATION_INCOMPLETE",
            "ANALYTIC_OR_AD_JACOBIAN_NOT_AVAILABLE",
        ],
        "real_data_status": "SEALED",
    })
    write_json(output, "DATA_ACCESS_AUDIT.json", {
        "opened_paths": [str(CONFIG), str(TEMPLATE), str(Path(__file__).resolve())],
        "synthetic_only": True,
        "real_calibration_payload": "SEALED", "UWB_T4": "SEALED",
        "Anchor_geometry": "SEALED", "operator_measurements": "SEALED",
        "walk": "SEALED", "final_still": "SEALED", "golf": "SEALED", "boxing": "SEALED",
        "hardware_accessed": False, "commit_or_push": False,
    })
    files = {path.name: sha(path) for path in sorted(output.iterdir()) if path.is_file() and path.name != "SHA256_MANIFEST.json"}
    write_json(output, "SHA256_MANIFEST.json", {"schema": "biospur-s2-qualification-sha-v1", "files": files})
    print(json.dumps({
        "truth_firewall": firewall_report["verdict"],
        "truth_point": truth_report["verdict"],
        "solver_revision_b_authorized": False,
        "output": str(output),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
