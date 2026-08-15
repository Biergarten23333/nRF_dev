"""Calibration-ledger-only V4.1 centerline solve.

Input validation completes before the NPZ ledger is opened.  Held-out data is
not accepted by this module.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from biospur_fusion.calibration.anthropometry_v4_1 import validate_anthropometry_v4_1
from biospur_fusion.calibration.articulated_batch import (
    CalibrationSamples,
    initial_static_guess,
    unpack_static,
)
from biospur_fusion.calibration.centerline_quotient_v4_1 import (
    BASE_PARAMETER_COUNT,
    LIMB_SEGMENTS,
    CenterlineQuotientProblemV41,
    QuotientStaticV41,
    evaluate_gate_decisions,
    physical_difference,
    quotient_observability,
    sensor_offset_posterior,
    sensor_offset_profile,
)
from biospur_fusion.calibration.real_capture import NODE_TO_SEGMENT, _build_samples, _solve_t4
from biospur_fusion.imu.frontend import audits_as_json, run_q1_attitude


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _initial(samples: CalibrationSamples, anthropometry) -> QuotientStaticV41:
    full = initial_static_guess(samples)
    r_nv, extrinsics, _ = unpack_static(full)
    axes = {
        segment: extrinsics[segment].T @ np.array([0.0, 0.0, 1.0])
        for segment in LIMB_SEGMENTS
    }
    return QuotientStaticV41(
        R_N_from_V4=r_nv,
        R_pelvis_from_sensor=extrinsics["Pelvis"],
        R_torso_from_sensor=extrinsics["Torso"],
        limb_axis_sensor=axes,
        capture_enclosure_to_landmark_m={
            node: placement.capture_prior_m.copy()
            for node, placement in anthropometry.placements.items()
        },
    )


def _repeatability_pass(row: dict, gates: dict) -> bool:
    value = gates["execution_gates"]
    return bool(
        row["maximum_segment_axis_angular_change_rad"]
        <= value["repeatability_maximum_segment_axis_angular_change_rad"]
        and row["maximum_joint_centre_displacement_mm"]
        <= value["repeatability_maximum_joint_centre_displacement_m"] * 1000.0
        and row["maximum_antenna_displacement_mm"]
        <= value["repeatability_maximum_antenna_displacement_m"] * 1000.0
    )


def _subset(samples: CalibrationSamples, selected: np.ndarray) -> CalibrationSamples:
    return CalibrationSamples(
        samples.time_ns[selected],
        samples.action[selected],
        samples.position_v4_m[selected],
        samples.covariance_v4_m2[selected],
        samples.orientation_N_from_B[selected],
        samples.valid_position[selected],
    )


def _stability(problem: CenterlineQuotientProblemV41, base_result, gates: dict) -> dict:
    base = base_result.x
    base_cost = float(base_result.cost)
    execution = gates["execution_gates"]
    rng = np.random.default_rng(20260814)
    parameter_scale = np.r_[np.full(BASE_PARAMETER_COUNT, 0.02),
                            np.full(len(base) - BASE_PARAMETER_COUNT, 0.002)]
    multistart = []
    for seed_index, magnitude in enumerate((0.0, 0.5, 1.0)):
        seed = base + magnitude * parameter_scale * rng.normal(size=len(base))
        _, result = problem.solve(seed, max_nfev=220)
        physical = physical_difference(problem, base, result.x)
        relative_cost = abs(float(result.cost) - base_cost) / max(1.0, abs(base_cost))
        multistart.append({
            "seed_index": seed_index,
            "success": bool(result.success),
            "cost": float(result.cost),
            "relative_cost_difference": relative_cost,
            **physical,
            "physical_pass": _repeatability_pass(physical, gates),
            "cost_pass": relative_cost <= execution["optimizer_maximum_relative_cost_difference"],
        })
    optimizer_pass = all(
        row["success"] and row["physical_pass"] and row["cost_pass"] for row in multistart)

    interleaved = []
    for parity in (0, 1):
        selected = np.arange(len(problem.samples.time_ns)) % 2 == parity
        subproblem = CenterlineQuotientProblemV41(
            _subset(problem.samples, selected), problem.anthropometry)
        _, result = subproblem.solve(base, max_nfev=180)
        physical = physical_difference(problem, base, result.x)
        interleaved.append({
            "parity": parity,
            "success": bool(result.success),
            "subset_cost": float(result.cost),
            **physical,
            "physical_pass": _repeatability_pass(physical, gates),
        })
    sampling_pass = all(row["success"] and row["physical_pass"] for row in interleaved)

    actions = frozenset(map(str, np.unique(problem.samples.action)))
    removal_rows = []
    for omitted in sorted(actions):
        _, result = problem.solve(base, include_actions=actions - {omitted}, max_nfev=160)
        physical = physical_difference(problem, base, result.x)
        removal_rows.append({
            "omitted_action": omitted,
            "success": bool(result.success),
            **physical,
            "physical_pass": _repeatability_pass(physical, gates),
        })
    mandatory = {"initial_still_attempt2", "t_pose"}
    optional_rows = [row for row in removal_rows if row["omitted_action"] not in mandatory]
    mandatory_rows = [row for row in removal_rows if row["omitted_action"] in mandatory]
    return {
        "residual_and_weighting_identity": "all refits use CenterlineQuotientProblemV41.residual unchanged",
        "optimizer_multistart_stability": {"pass": optimizer_pass, "rows": multistart},
        "interleaved_sampling_sensitivity": {"pass": sampling_pass, "rows": interleaved},
        "mandatory_action_dependence": {
            "is_acceptance_leave_one_out": False,
            "rows": mandatory_rows,
        },
        "optional_action_removal": {
            "pass": all(row["success"] and row["physical_pass"] for row in optional_rows),
            "rows": optional_rows,
        },
    }


def run(calibration_ledger: Path, layout: Path, anthropometry_path: Path,
        gates_path: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    anthropometry, anthropometry_audit = validate_anthropometry_v4_1(anthropometry_path)
    gates = json.loads(gates_path.read_text(encoding="utf-8"))
    _dump(output / "ANTHROPOMETRY_VALIDATION.json", anthropometry_audit)
    _dump(output / "PREDECLARED_PHYSICAL_GATES.json", {
        "path": str(gates_path.resolve()),
        "sha256": _sha(gates_path),
        "gates": gates,
    })
    foot_verdict = anthropometry_audit["foot_rendering"]["verdict"]
    if anthropometry is None:
        result = {
            "verdict": "BLOCKED_V4_1_INPUTS_INCOMPLETE",
            "pass": False,
            "FULL_SEGMENT_POSE_CALIBRATION": "FAIL_AXIAL_TWIST_GAUGES_UNRESOLVED",
            "STICK_FIGURE_CENTERLINE_CALIBRATION": "NOT_RUN_INPUTS_INCOMPLETE",
            "FOOT_RENDERING": foot_verdict,
            "solver_missing": anthropometry_audit["solver_missing"],
            "solver_invalid": anthropometry_audit["solver_invalid"],
            "calibration_payload_opened": False,
            "heldout_payload_opened": False,
            "anthropometric_uncertainty": anthropometry_audit["anthropometric_uncertainty"],
        }
        _dump(output / "CENTERLINE_CALIBRATION_RESULT.json", result)
        return result

    # This is the sole payload-opening point and accepts only the calibration ledger.
    with np.load(calibration_ledger, allow_pickle=False) as ledger:
        windows = {
            str(row["name"]): (int(row["start_ns"]), int(row["stop_ns"]))
            for row in ledger["action_windows"]
        }
        initial_start, initial_stop = windows["initial_still_attempt2"]
        analysis_end = windows["trunk"][1]
        observations, t4_accounting, rejections = _solve_t4(ledger, layout)
        q1 = {}
        audits = {}
        for node in NODE_TO_SEGMENT:
            q1[node], audits[node] = run_q1_attitude(
                ledger[f"imu_{node}"],
                node_id=node,
                initial_start_ns=initial_start,
                initial_end_ns=initial_stop,
                analysis_end_ns=analysis_end,
            )
        samples, sample_audit = _build_samples(ledger, observations, q1)

    problem = CenterlineQuotientProblemV41(samples, anthropometry)
    initial = problem.initial_vector(_initial(samples, anthropometry))
    _, optimization = problem.solve(initial, max_nfev=400)
    observability = quotient_observability(problem, optimization.x, gates)
    stability = _stability(problem, optimization, gates)
    posterior = sensor_offset_posterior(problem, optimization.x, gates)
    profile = sensor_offset_profile(problem, optimization.x, gates)
    normalized = np.abs(problem.measurement_residual(optimization.x))
    execution = gates["execution_gates"]
    mismatch = {
        "normalized_residual_median": float(np.median(normalized)),
        "normalized_residual_p95": float(np.percentile(normalized, 95)),
    }
    mismatch["pass"] = bool(
        mismatch["normalized_residual_median"]
        <= execution["model_mismatch_maximum_normalized_residual_median"]
        and mismatch["normalized_residual_p95"]
        <= execution["model_mismatch_maximum_normalized_residual_p95"])

    null_rows = observability["null_directions"]
    repeat_rows = (
        stability["optimizer_multistart_stability"]["rows"]
        + stability["interleaved_sampling_sensitivity"]["rows"]
        + stability["optional_action_removal"]["rows"]
    )
    posterior_rows = posterior["rows"]
    profile_rows = profile["rows"]
    execution_metrics = {
        "null_axis_rad": max((row["maximum_segment_axis_angular_change_rad"] for row in null_rows), default=0.0),
        "null_joint_m": max((row["maximum_joint_centre_displacement_mm"] / 1000.0 for row in null_rows), default=0.0),
        "null_antenna_m": max((row["maximum_antenna_displacement_mm"] / 1000.0 for row in null_rows), default=0.0),
        "repeat_axis_rad": max((row["maximum_segment_axis_angular_change_rad"] for row in repeat_rows), default=0.0),
        "repeat_joint_m": max((row["maximum_joint_centre_displacement_mm"] / 1000.0 for row in repeat_rows), default=0.0),
        "repeat_antenna_m": max((row["maximum_antenna_displacement_mm"] / 1000.0 for row in repeat_rows), default=0.0),
        "optimizer_relative_cost": max((row.get("relative_cost_difference", 0.0)
                                         for row in stability["optimizer_multistart_stability"]["rows"]), default=0.0),
        "model_median": mismatch["normalized_residual_median"],
        "model_p95": mismatch["normalized_residual_p95"],
        "offset_shift_sigma": max((row["maximum_posterior_shift_sigma"] for row in posterior_rows), default=0.0),
        "offset_bound_clearance_fraction": min((row["minimum_bound_clearance_fraction"] for row in posterior_rows), default=1.0),
        "offset_profile_axis_rad": max((row["maximum_segment_axis_angular_change_rad"] for row in profile_rows), default=0.0),
        "offset_profile_joint_m": max((row["maximum_joint_centre_displacement_mm"] / 1000.0 for row in profile_rows), default=0.0),
        "offset_profile_antenna_m": max((row["maximum_antenna_displacement_mm"] / 1000.0 for row in profile_rows), default=0.0),
    }
    gate_decisions = evaluate_gate_decisions(execution_metrics, gates)

    centerline_pass = bool(
        optimization.success
        and observability["centerline_observable"]
        and observability["sensor_offset_trade_pass"]
        and stability["optimizer_multistart_stability"]["pass"]
        and stability["interleaved_sampling_sensitivity"]["pass"]
        and stability["optional_action_removal"]["pass"]
        and mismatch["pass"]
        and posterior["pass"]
        and profile["pass"]
        and gate_decisions["pass"]
    )
    result = {
        "verdict": "CENTERLINE_CALIBRATION_PASS" if centerline_pass else "BLOCKED_CENTERLINE_CALIBRATION",
        "pass": centerline_pass,
        "FULL_SEGMENT_POSE_CALIBRATION": "FAIL_AXIAL_TWIST_GAUGES_UNRESOLVED",
        "STICK_FIGURE_CENTERLINE_CALIBRATION": "PASS" if centerline_pass else "FAIL",
        "FOOT_RENDERING": foot_verdict,
        "optimizer": {
            "success": bool(optimization.success),
            "cost": float(optimization.cost),
            "nfev": int(optimization.nfev),
        },
        "anthropometry_sha256": anthropometry.source_sha256,
        "gates_sha256": _sha(gates_path),
        "calibration_payload_opened": True,
        "heldout_payload_opened": False,
        "sample_audit": {key: value for key, value in sample_audit.items() if key != "nearest"},
        "anthropometric_uncertainty": anthropometry_audit["anthropometric_uncertainty"],
    }
    _dump(output / "CENTERLINE_CALIBRATION_RESULT.json", result)
    _dump(output / "CENTERLINE_OBSERVABILITY.json", observability)
    _dump(output / "CENTERLINE_STABILITY.json", stability)
    _dump(output / "MODEL_MISMATCH.json", mismatch)
    _dump(output / "SENSOR_OFFSET_POSTERIOR.json", posterior)
    _dump(output / "SENSOR_OFFSET_PROFILE.json", profile)
    _dump(output / "EXECUTION_GATE_DECISIONS.json", {
        "metrics": execution_metrics,
        "decisions": gate_decisions,
        "source": "all executable thresholds loaded from invariance_gates_v4_1.json",
    })
    _dump(output / "UWB_CALIBRATION_ACCOUNTING.json", t4_accounting)
    _dump(output / "IMU_CALIBRATION_AUDIT.json", audits_as_json(audits))
    _dump(output / "CALIBRATION_REJECTION_LEDGER.json", rejections)
    if centerline_pass:
        static = problem.unpack(optimization.x)
        _dump(output / "CENTERLINE_FREEZE_PARAMETERS.json", {
            "schema": "biospur-centerline-freeze-v4.1",
            "quotient_parameter_vector": optimization.x.tolist(),
            "R_N_from_V4": static.R_N_from_V4.tolist(),
            "R_V4_from_N": static.R_N_from_V4.T.tolist(),
            "R_pelvis_from_sensor": static.R_pelvis_from_sensor.tolist(),
            "R_torso_from_sensor": static.R_torso_from_sensor.tolist(),
            "limb_axis_sensor": {key: value.tolist() for key, value in static.limb_axis_sensor.items()},
            "capture_enclosure_to_landmark_m": {
                key: value.tolist()
                for key, value in static.capture_enclosure_to_landmark_m.items()
            },
            "anthropometry_sha256": anthropometry.source_sha256,
            "gates_sha256": _sha(gates_path),
            "axial_twist_gauges": [f"{segment}.axial_twist" for segment in LIMB_SEGMENTS],
            "uncertainty_scope": "conditional on fixed anthropometric scalar inputs",
        })
    return result
