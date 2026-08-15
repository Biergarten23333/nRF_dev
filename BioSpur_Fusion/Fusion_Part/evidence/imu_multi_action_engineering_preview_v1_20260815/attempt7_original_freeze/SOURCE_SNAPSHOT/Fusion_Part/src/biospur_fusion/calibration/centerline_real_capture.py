"""Real calibration-only V4 quotient solve; held-out payload is never accepted."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from biospur_fusion.calibration.anthropometry import validate_anthropometry
from biospur_fusion.calibration.articulated_batch import (
    SEGMENTS, initial_static_guess, unpack_static,
)
from biospur_fusion.calibration.centerline_quotient import (
    LIMB_SEGMENTS, CenterlineQuotientProblem, QuotientStatic, pack, predict_antennas,
    quotient_observability,
)
from biospur_fusion.calibration.real_capture import (
    NODE_TO_SEGMENT, _build_samples, _solve_t4,
)
from biospur_fusion.imu.frontend import audits_as_json, run_q1_attitude


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _initial(samples) -> np.ndarray:
    full = initial_static_guess(samples); r_nv, extrinsics, _ = unpack_static(full)
    axes = {segment: extrinsics[segment].T @ np.array([0., 0., 1.]) for segment in LIMB_SEGMENTS}
    return pack(QuotientStatic(r_nv, extrinsics["Pelvis"], extrinsics["Torso"], axes))


def _physical_difference(problem, left: np.ndarray, right: np.ndarray) -> dict:
    left_prediction, left_axes = predict_antennas(problem.samples, problem_to_static(left), problem.anthropometry)
    right_prediction, right_axes = predict_antennas(problem.samples, problem_to_static(right), problem.anthropometry)
    dots = np.clip(np.sum(left_axes * right_axes, axis=2), -1., 1.)
    angle = float(np.max(np.arccos(dots)))
    displacement = float(np.max(np.linalg.norm(left_prediction-right_prediction, axis=2)))
    return {
        "maximum_segment_axis_angular_change_rad": angle,
        "maximum_segment_axis_angular_change_deg": float(np.degrees(angle)),
        "maximum_joint_centre_displacement_mm": displacement*1000.,
        "maximum_antenna_displacement_mm": displacement*1000.,
    }


def problem_to_static(vector):
    from biospur_fusion.calibration.centerline_quotient import unpack
    return unpack(vector)


def _within_physical(row: dict, limits: dict) -> bool:
    return bool(
        row["maximum_segment_axis_angular_change_rad"] <= limits["maximum_segment_axis_angular_change_rad"]
        and row["maximum_joint_centre_displacement_mm"] <= limits["maximum_joint_centre_displacement_m"]*1000.
        and row["maximum_antenna_displacement_mm"] <= limits["maximum_antenna_displacement_m"]*1000.)


def _stability(problem, base_result, gates: dict) -> dict:
    base = base_result.x; base_cost = float(base_result.cost); limits = gates["repeatability"]
    rng = np.random.default_rng(20260814); multistart = []
    for index, scale in enumerate((0., .01, .03)):
        seed = base.copy() if scale == 0 else base + rng.normal(0., scale, len(base))
        _, result = problem.solve(seed, max_nfev=180)
        physical = _physical_difference(problem, base, result.x)
        multistart.append({"seed_index": index, "success": bool(result.success),
                           "cost": float(result.cost),
                           "relative_cost_difference": abs(float(result.cost)-base_cost)/max(1., abs(base_cost)),
                           **physical, "physical_pass": _within_physical(physical, limits)})
    optimizer_pass = all(row["success"] and row["physical_pass"]
                         and row["relative_cost_difference"] <= gates["optimizer_stability"]["maximum_relative_cost_difference"]
                         for row in multistart)
    interleaved = []
    sample_class = type(problem.samples)
    for parity in (0, 1):
        selected = np.arange(len(problem.samples.time_ns)) % 2 == parity
        s = problem.samples
        subset = sample_class(s.time_ns[selected], s.action[selected], s.position_v4_m[selected],
                              s.covariance_v4_m2[selected], s.orientation_N_from_B[selected],
                              s.valid_position[selected])
        subproblem = CenterlineQuotientProblem(subset, problem.anthropometry)
        _, result = subproblem.solve(base, max_nfev=150)
        physical = _physical_difference(problem, base, result.x)
        interleaved.append({"parity": parity, "success": bool(result.success), "subset_cost": float(result.cost),
                            **physical, "physical_pass": _within_physical(physical, limits)})
    sampling_pass = all(row["success"] and row["physical_pass"] for row in interleaved)
    actions = frozenset(map(str, np.unique(problem.samples.action))); action_rows = []
    for omitted in sorted(actions):
        _, result = problem.solve(base, include_actions=actions-{omitted}, max_nfev=120)
        physical = _physical_difference(problem, base, result.x)
        action_rows.append({"omitted_action": omitted, "success": bool(result.success),
                            **physical, "physical_pass": _within_physical(physical, limits)})
    mandatory = {"initial_still_attempt2", "t_pose"}
    optional = [row for row in action_rows if row["omitted_action"] not in mandatory]
    return {
        "residual_and_weighting_identity": "all refits use CenterlineQuotientProblem.residual unchanged",
        "optimizer_stability": {"pass": optimizer_pass, "rows": multistart},
        "sampling_weighting_sensitivity": {"pass": sampling_pass, "rows": interleaved},
        "mandatory_action_dependence": {"rows": [r for r in action_rows if r["omitted_action"] in mandatory]},
        "optional_action_removal": {"pass": all(r["success"] and r["physical_pass"] for r in optional),
                                    "rows": optional},
    }


def run(calibration_ledger: Path, layout: Path, anthropometry_path: Path,
        gates_path: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    anthropometry, anthropometry_audit = validate_anthropometry(anthropometry_path)
    gates = json.loads(gates_path.read_text())
    _dump(output/"ANTHROPOMETRY_VALIDATION.json", anthropometry_audit)
    _dump(output/"PREDECLARED_PHYSICAL_GATES.json", {"sha256": _sha(gates_path), "gates": gates})
    if anthropometry is None:
        result = {
            "verdict": "BLOCKED_ANTHROPOMETRY_INPUT_INCOMPLETE", "pass": False,
            "FULL_SEGMENT_POSE_CALIBRATION": "FAIL_AXIAL_TWIST_GAUGES_UNRESOLVED",
            "STICK_FIGURE_CENTERLINE_CALIBRATION": "NOT_RUN_ANTHROPOMETRY_INCOMPLETE",
            "missing": anthropometry_audit["missing"], "invalid": anthropometry_audit["invalid"],
            "calibration_payload_opened": False,
        }
        _dump(output/"CENTERLINE_CALIBRATION_RESULT.json", result); return result
    with np.load(calibration_ledger, allow_pickle=False) as ledger:
        windows = {str(row["name"]): (int(row["start_ns"]), int(row["stop_ns"]))
                   for row in ledger["action_windows"]}
        initial_start, initial_stop = windows["initial_still_attempt2"]
        analysis_end = windows["trunk"][1]
        observations, t4_accounting, rejections = _solve_t4(ledger, layout)
        q1 = {}; audits = {}
        for node in NODE_TO_SEGMENT:
            q1[node], audits[node] = run_q1_attitude(
                ledger[f"imu_{node}"], node_id=node, initial_start_ns=initial_start,
                initial_end_ns=initial_stop, analysis_end_ns=analysis_end)
        samples, sample_audit = _build_samples(ledger, observations, q1)
    problem = CenterlineQuotientProblem(samples, anthropometry)
    _, optimization = problem.solve(_initial(samples), max_nfev=300)
    observability = quotient_observability(problem, optimization.x, gates)
    stability = _stability(problem, optimization, gates)
    normalized = np.abs(problem.residual(optimization.x)); mismatch = {
        "normalized_residual_median": float(np.median(normalized)),
        "normalized_residual_p95": float(np.percentile(normalized, 95)),
    }
    mismatch["pass"] = bool(
        mismatch["normalized_residual_median"] <= gates["model_mismatch"]["maximum_normalized_residual_median"]
        and mismatch["normalized_residual_p95"] <= gates["model_mismatch"]["maximum_normalized_residual_p95"])
    centerline_pass = bool(optimization.success and observability["centerline_observable"]
                           and stability["optimizer_stability"]["pass"]
                           and stability["sampling_weighting_sensitivity"]["pass"]
                           and stability["optional_action_removal"]["pass"] and mismatch["pass"])
    result = {
        "verdict": "CENTERLINE_CALIBRATION_PASS" if centerline_pass else "BLOCKED_CENTERLINE_CALIBRATION",
        "pass": centerline_pass,
        "FULL_SEGMENT_POSE_CALIBRATION": "FAIL_AXIAL_TWIST_GAUGES_UNRESOLVED",
        "STICK_FIGURE_CENTERLINE_CALIBRATION": "PASS" if centerline_pass else "FAIL",
        "optimizer": {"success": bool(optimization.success), "cost": float(optimization.cost),
                      "nfev": int(optimization.nfev)},
        "anthropometry_sha256": anthropometry.source_sha256,
        "gates_sha256": _sha(gates_path), "sample_audit": {k:v for k,v in sample_audit.items() if k != "nearest"},
    }
    _dump(output/"CENTERLINE_CALIBRATION_RESULT.json", result)
    _dump(output/"CENTERLINE_OBSERVABILITY.json", observability)
    _dump(output/"CENTERLINE_STABILITY.json", stability)
    _dump(output/"MODEL_MISMATCH.json", mismatch)
    _dump(output/"UWB_CALIBRATION_ACCOUNTING.json", t4_accounting)
    _dump(output/"IMU_CALIBRATION_AUDIT.json", audits_as_json(audits))
    _dump(output/"CALIBRATION_REJECTION_LEDGER.json", rejections)
    if centerline_pass:
        static = problem_to_static(optimization.x)
        freeze = {
            "schema": "biospur-centerline-freeze-v1", "quotient_parameter_vector": optimization.x.tolist(),
            "R_N_from_V4": static.R_N_from_V4.tolist(),
            "R_pelvis_from_sensor": static.R_pelvis_from_sensor.tolist(),
            "R_torso_from_sensor": static.R_torso_from_sensor.tolist(),
            "limb_axis_sensor": {k:v.tolist() for k,v in static.limb_axis_sensor.items()},
            "anthropometry_sha256": anthropometry.source_sha256, "gates_sha256": _sha(gates_path),
            "axial_twist_gauges": [f"{s}.axial_twist" for s in LIMB_SEGMENTS],
        }
        _dump(output/"CENTERLINE_FREEZE_PARAMETERS.json", freeze)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration-ledger", type=Path, required=True)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--anthropometry", type=Path, required=True)
    parser.add_argument("--gates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.calibration_ledger, args.layout, args.anthropometry, args.gates, args.output)
    print(json.dumps(result, sort_keys=True)); return 0 if result["pass"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
