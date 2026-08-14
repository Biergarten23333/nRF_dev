"""Calibration-capsule implementation for the v47 ten-node capture.

This module deliberately accepts one payload-bearing input: the calibration
ledger.  Action labels are embedded in that ledger before the process
firewall is entered.  It never imports the capture parser and has no path to
the held-out ledger.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.calibration.articulated_batch import (
    CalibrationSamples, GEOMETRY_NAMES, SEGMENTS, SEGMENT_INDEX,
    ArticulatedCalibrationProblem, candidate_manifest, initial_static_guess,
    observability_report, pack_static,
)
from biospur_fusion.imu.frontend import audits_as_json, run_q1_attitude
from biospur_fusion.imu.q1 import quaternion_to_matrix
from biospur_fusion.uwb.frontend import CanonicalT4Frontend


NODE_TO_SEGMENT = {
    "BSFC2CC": "Pelvis", "BSF31CC": "Torso",
    "BSFAA61": "UpperArm_L", "BSFB165": "Forearm_L",
    "BSF1120": "UpperArm_R", "BSFEC35": "Forearm_R",
    "BSF44AD": "Thigh_L", "BSF6C53": "Shank_L",
    "BSF3C79": "Thigh_R", "BSF8BC4": "Shank_R",
}
SEGMENT_TO_NODE = {segment: node for node, segment in NODE_TO_SEGMENT.items()}
CALIBRATION_ACTIONS = (
    "initial_still_attempt2", "t_pose", "arms", "left_elbow", "right_elbow_attempt2",
    "left_knee", "right_knee", "left_heel", "right_heel", "squats", "trunk",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 << 20):
            digest.update(block)
    return digest.hexdigest()


def _dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _nearest(times: np.ndarray, target: int) -> tuple[int, int]:
    at = int(np.searchsorted(times, target))
    choices = [index for index in (at - 1, at) if 0 <= index < len(times)]
    index = min(choices, key=lambda value: abs(int(times[value]) - target))
    return index, abs(int(times[index]) - target)


def _causal_accept(times: np.ndarray, positions: np.ndarray) -> tuple[np.ndarray, list[dict]]:
    """Immutable, one-pass flyer gate; no rejected point can update its predictor."""
    accepted = np.zeros(len(times), bool); history: list[int] = []; rejected = []
    for index, time_ns in enumerate(times):
        if len(history) < 2:
            accepted[index] = True; history.append(index); continue
        one, two = history[-1], history[-2]
        dt_history = max(1e-6, (int(times[one]) - int(times[two])) / 1e9)
        velocity = (positions[one] - positions[two]) / dt_history
        dt = max(0.0, (int(time_ns) - int(times[one])) / 1e9)
        prediction = positions[one] + velocity * min(dt, .5)
        innovation = float(np.linalg.norm(positions[index] - prediction))
        limit = 0.75 + 3.5 * dt
        if np.isfinite(innovation) and innovation <= limit:
            accepted[index] = True; history.append(index)
        else:
            rejected.append({"time_ns": int(time_ns), "reason": "CAUSAL_KINEMATIC_FLYER",
                             "innovation_m": innovation, "limit_m": limit,
                             "inserted_into_calibration": False})
    return accepted, rejected


def _solve_t4(ledger, layout: Path) -> tuple[dict, dict, list[dict]]:
    frontend = CanonicalT4Frontend(layout); observations = {}; accounting = {}; rejections = []
    for node in NODE_TO_SEGMENT:
        records = ledger[f"uwb_{node}"]
        times = []; positions = []; covariances = []; sweeps = []
        solver_failed = geometry_rejected = 0
        for record in records:
            result = frontend.solve(
                node_id=node, sweep=int(record["sweep"]), global_time_ns=int(record["global_time_ns"]),
                global_time_sigma_ns=int(record["global_time_sigma_ns"]), anchor_ids=record["anchor_id"],
                ranges_mm=record["range_mm"], quality=record["quality_percent"],
                valid_mask=int(record["valid_mask"]), t_round_us=record["t_round_us"],
            )
            if result is None:
                solver_failed += 1
                rejections.append({"node": node, "time_ns": int(record["global_time_ns"]),
                                   "sweep": int(record["sweep"]), "reason": "T4_SOLVER_FAILURE",
                                   "inserted_into_calibration": False})
                continue
            if result.acceptability != "ACCEPTED":
                geometry_rejected += 1
                rejections.append({"node": node, "time_ns": result.effective_time_ns,
                                   "sweep": result.sweep, "reason": result.acceptability,
                                   "inserted_into_calibration": False})
                continue
            times.append(result.effective_time_ns); positions.append(result.xyz_m)
            covariances.append(result.covariance_m2); sweeps.append(result.sweep)
        times_a = np.asarray(times, dtype=np.int64)
        positions_a = np.asarray(positions, dtype=float)
        covariance_a = np.asarray(covariances, dtype=float)
        causal, causal_rejections = _causal_accept(times_a, positions_a)
        sweep_by_time = {int(time): int(sweep) for time, sweep in zip(times, sweeps)}
        for row in causal_rejections:
            row.update({"node": node, "sweep": sweep_by_time.get(row["time_ns"], -1)})
        rejections.extend(causal_rejections)
        observations[node] = {
            "time_ns": times_a[causal], "position": positions_a[causal],
            "covariance": covariance_a[causal], "sweep": np.asarray(sweeps, dtype=np.int64)[causal],
        }
        accounting[node] = {
            "input": len(records), "t4_solutions": len(times), "solver_failed": solver_failed,
            "geometry_rejected": geometry_rejected, "causal_rejected": int((~causal).sum()),
            "accepted_for_calibration": int(causal.sum()),
        }
        if sum(accounting[node][key] for key in ("solver_failed", "geometry_rejected",
                                                  "causal_rejected", "accepted_for_calibration")) != len(records):
            raise RuntimeError(f"T4 accounting did not close for {node}")
    return observations, accounting, rejections


def _build_samples(ledger, observations: dict, q1: dict) -> tuple[CalibrationSamples, dict]:
    windows = {str(row["name"]): (int(row["start_ns"]), int(row["stop_ns"]))
               for row in ledger["action_windows"]}
    missing = sorted(set(CALIBRATION_ACTIONS) - set(windows))
    if missing:
        raise RuntimeError(f"calibration ledger action metadata incomplete: {missing}")
    knot_times = []; actions = []
    for action in CALIBRATION_ACTIONS:
        start, stop = windows[action]
        # Six deterministic interior knots per labelled action retain movement
        # excitation while avoiding token/STOP boundary uncertainty.
        fractions = np.linspace(.08, .92, 6)
        knot_times.extend(int(round(start + value * (stop - start))) for value in fractions)
        actions.extend([action] * len(fractions))
    knot_times_a = np.asarray(knot_times, dtype=np.int64)
    count = len(knot_times_a); segments = len(SEGMENTS)
    position = np.full((count, segments, 3), np.nan)
    covariance = np.full((count, segments, 3, 3), np.nan)
    orientation = np.full((count, segments, 3, 3), np.nan)
    valid = np.zeros((count, segments), bool); nearest_audit = []
    for segment_index, segment in enumerate(SEGMENTS):
        node = SEGMENT_TO_NODE[segment]; uwb = observations[node]; imu = q1[node]
        for knot, time_ns in enumerate(knot_times_a):
            ui, ugap = _nearest(uwb["time_ns"], int(time_ns))
            qi, qgap = _nearest(imu["global_time_ns"], int(time_ns))
            orientation[knot, segment_index] = quaternion_to_matrix(imu[qi]["q_wxyz"])
            if ugap <= 300_000_000:
                position[knot, segment_index] = uwb["position"][ui]
                covariance[knot, segment_index] = uwb["covariance"][ui]
                valid[knot, segment_index] = True
            nearest_audit.append({"action": actions[knot], "knot_time_ns": int(time_ns),
                                  "node": node, "uwb_gap_ns": ugap, "q1_gap_ns": qgap,
                                  "uwb_used": bool(ugap <= 300_000_000)})
    # Root translation is eliminated with the pelvis antenna.  A limb factor
    # is therefore usable only when both its own T4 observation and the pelvis
    # reference at the same knot are finite.
    finite = np.isfinite(position).all(axis=2) & np.isfinite(covariance).all(axis=(2, 3))
    valid &= finite
    valid &= valid[:, SEGMENT_INDEX["Pelvis"]][:, None]
    if not np.isfinite(orientation).all():
        raise RuntimeError("non-finite Q1 orientation in calibration samples")
    if np.any(valid & ~finite):
        raise RuntimeError("non-finite T4 factor survived calibration validity mask")
    samples = CalibrationSamples(knot_times_a, np.asarray(actions), position, covariance, orientation, valid)
    return samples, {"knots": count, "valid_uwb_factors": int(valid.sum()),
                     "missing_uwb_factors": int((~valid).sum()), "nearest": nearest_audit}


def _rotation_angle(a: np.ndarray, b: np.ndarray) -> float:
    return float(Rotation.from_matrix(a @ b.T).magnitude())


def _fit_evidence(problem, vector: np.ndarray, candidate, result) -> dict:
    lower, upper = problem.bounds(); distances = np.minimum(vector - lower, upper - vector)
    bound_hits = [problem.parameter_names[index] for index in np.flatnonzero(distances <= 1e-4)]
    physical = problem.physical_residual(vector)
    return {
        "optimizer_cost": float(result.cost),
        "full_physical_sse": float(physical @ physical),
        "geometry": asdict(candidate.geometry),
        "bound_hits_1e_4": bound_hits,
        "success": bool(result.success),
    }


def _stability(problem, fitted: np.ndarray, actions: np.ndarray) -> dict:
    # Re-decode once without relying on optimizer internals.
    from biospur_fusion.calibration.articulated_batch import unpack_static
    base_r, base_e, base_g = unpack_static(fitted)
    rows = []
    all_actions = frozenset(map(str, np.unique(actions)))
    for omitted in sorted(all_actions):
        include = all_actions - {omitted}
        candidate, result = problem.solve(fitted, include_actions=include, max_nfev=55)
        axis_delta = 0.0
        for segment in SEGMENTS:
            before = base_e[segment].T @ np.array([0., 0., 1.])
            after = candidate.R_segment_from_sensor[segment].T @ np.array([0., 0., 1.])
            axis_delta = max(axis_delta, float(np.arccos(np.clip(before @ after, -1., 1.))))
        rows.append({
            "omitted_action": omitted, "success": bool(result.success),
            "frame_delta_deg": np.degrees(_rotation_angle(candidate.R_N_from_V4, base_r)),
            "max_stick_axis_delta_deg": np.degrees(axis_delta),
            "max_dimension_delta_m": float(np.max(abs(candidate.geometry.vector() - base_g.vector()))),
            "fit": _fit_evidence(problem, result.x, candidate, result),
        })
    thresholds = {"frame_delta_deg": 5.0, "max_stick_axis_delta_deg": 10.0,
                  "max_dimension_delta_m": .05}
    passed = all(row["success"] and row["frame_delta_deg"] <= thresholds["frame_delta_deg"]
                 and row["max_stick_axis_delta_deg"] <= thresholds["max_stick_axis_delta_deg"]
                 and row["max_dimension_delta_m"] <= thresholds["max_dimension_delta_m"] for row in rows)
    mandatory_names = {"initial_still_attempt2", "t_pose"}
    mandatory = [row for row in rows if row["omitted_action"] in mandatory_names]
    optional = [row for row in rows if row["omitted_action"] not in mandatory_names]
    def rows_pass(selected):
        return all(row["success"] and row["frame_delta_deg"] <= thresholds["frame_delta_deg"]
                   and row["max_stick_axis_delta_deg"] <= thresholds["max_stick_axis_delta_deg"]
                   and row["max_dimension_delta_m"] <= thresholds["max_dimension_delta_m"]
                   for row in selected)
    return {"method": "actual leave-one-labelled-action-out refit", "thresholds": thresholds,
            "rows": rows, "pass": passed,
            "mandatory_action_dependence": {"actions": sorted(mandatory_names), "rows": mandatory,
                                             "pass_without_mandatory": rows_pass(mandatory)},
            "optional_action_leave_one_out": {"rows": optional, "pass": rows_pass(optional)}}


def _repeatability(samples: CalibrationSamples, fitted: np.ndarray) -> dict:
    results = []
    for parity in (0, 1):
        selected = np.arange(len(samples.time_ns)) % 2 == parity
        subset = CalibrationSamples(samples.time_ns[selected], samples.action[selected],
                                    samples.position_v4_m[selected], samples.covariance_v4_m2[selected],
                                    samples.orientation_N_from_B[selected], samples.valid_position[selected])
        candidate, result = ArticulatedCalibrationProblem(subset).solve(fitted, max_nfev=70)
        results.append((candidate, result))
    first, second = results[0][0], results[1][0]
    axis_delta = max(
        np.degrees(np.arccos(np.clip(
            (first.R_segment_from_sensor[s].T @ [0., 0., 1.])
            @ (second.R_segment_from_sensor[s].T @ [0., 0., 1.]), -1., 1.))) for s in SEGMENTS)
    dimension_delta = float(np.max(abs(first.geometry.vector() - second.geometry.vector())))
    frame_delta = np.degrees(_rotation_angle(first.R_N_from_V4, second.R_N_from_V4))
    passed = bool(all(result.success for _, result in results) and axis_delta <= 10.0
                  and dimension_delta <= .05 and frame_delta <= 5.0)
    problem_full = ArticulatedCalibrationProblem(samples)
    fit_rows = [_fit_evidence(problem_full, result.x, candidate, result)
                for candidate, result in results]
    geometry_delta = {
        name: float(getattr(first.geometry, name) - getattr(second.geometry, name))
        for name in GEOMETRY_NAMES
    }
    return {"method": "deterministic interleaved-knot refit", "pass": passed,
            "frame_delta_deg": float(frame_delta), "max_stick_axis_delta_deg": float(axis_delta),
            "max_dimension_delta_m": dimension_delta,
            "dimension_delta_fit0_minus_fit1_m": geometry_delta,
            "fit_success": [bool(result.success) for _, result in results],
            "fits_evaluated_on_full_calibration_residual": fit_rows}


def _multistart(problem: ArticulatedCalibrationProblem, fitted: np.ndarray) -> dict:
    rng = np.random.default_rng(20260814)
    seeds = [fitted.copy()]
    for scale in (.01, .03):
        seeds.append(fitted + rng.normal(0.0, scale, len(fitted)))
    fits = []
    for index, seed in enumerate(seeds):
        candidate, result = problem.solve(seed, max_nfev=160)
        row = _fit_evidence(problem, result.x, candidate, result)
        row["seed_index"] = index; row["parameter_vector"] = result.x.tolist()
        fits.append((candidate, result, row))
    base = fits[0][0]
    comparisons = []
    for candidate, result, row in fits[1:]:
        comparisons.append({
            "seed_index": row["seed_index"],
            "cost_relative_to_seed0": ((row["optimizer_cost"] - fits[0][2]["optimizer_cost"])
                                       / max(1.0, abs(fits[0][2]["optimizer_cost"]))),
            "max_dimension_delta_m": float(np.max(abs(candidate.geometry.vector() - base.geometry.vector()))),
            "frame_delta_deg": float(np.degrees(_rotation_angle(candidate.R_N_from_V4, base.R_N_from_V4))),
        })
    thresholds = {"relative_cost": 1e-6, "max_dimension_delta_m": .05, "frame_delta_deg": 5.0}
    passed = all(abs(row["cost_relative_to_seed0"]) <= thresholds["relative_cost"]
                 and row["max_dimension_delta_m"] <= thresholds["max_dimension_delta_m"]
                 and row["frame_delta_deg"] <= thresholds["frame_delta_deg"] for row in comparisons)
    return {"method": "three deterministic full-data initializations", "thresholds": thresholds,
            "pass": passed, "fits": [row for _, _, row in fits], "comparisons": comparisons}


def run(calibration_ledger: Path, layout: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    ledger_hash = _sha256(calibration_ledger); layout_hash = _sha256(layout)
    with np.load(calibration_ledger, allow_pickle=False) as ledger:
        windows = {str(row["name"]): (int(row["start_ns"]), int(row["stop_ns"]))
                   for row in ledger["action_windows"]}
        initial_start, initial_stop = windows["initial_still_attempt2"]
        analysis_end = windows["trunk"][1]
        observations, t4_accounting, rejections = _solve_t4(ledger, layout)
        q1 = {}; q1_audits = {}; biases = {}
        for node in NODE_TO_SEGMENT:
            timeline, audit = run_q1_attitude(
                ledger[f"imu_{node}"], node_id=node, initial_start_ns=initial_start,
                initial_end_ns=initial_stop, analysis_end_ns=analysis_end,
            )
            q1[node] = timeline; q1_audits[node] = audit
            biases[node] = {
                "treatment": "Q1_CALIBRATION_ESTIMATE_FROZEN_IN_PREINTEGRATED_ATTITUDE",
                "gyro_bias_rad_s": timeline[-1]["gyro_bias_rad_s"].tolist(),
                "accel_bias_mps2": timeline[-1]["accel_bias_mps2"].tolist(),
                "source_time_ns": int(timeline[-1]["global_time_ns"]),
            }
        samples, sample_audit = _build_samples(ledger, observations, q1)
    problem = ArticulatedCalibrationProblem(samples)
    initial = initial_static_guess(samples)
    candidate, result = problem.solve(initial, max_nfev=300)
    fitted = result.x.copy(); observability = observability_report(problem, fitted)
    stability = _stability(problem, fitted, samples.action)
    repeatability = _repeatability(samples, fitted)
    multistart = _multistart(problem, fitted)
    manifest = candidate_manifest(candidate); manifest["imu_bias_parameters"] = biases
    proper = bool(np.linalg.det(candidate.R_N_from_V4) > .999999 and all(
        np.linalg.det(value) > .999999 for value in candidate.R_segment_from_sensor.values()))
    geometry = candidate.geometry.vector()
    geometry_low = problem.bounds()[0][-len(geometry):]
    geometry_high = problem.bounds()[1][-len(geometry):]
    bound_margin = np.minimum(geometry - geometry_low, geometry_high - geometry)
    finite_plausible = bool(np.isfinite(geometry).all() and np.all(bound_margin > 1e-4))
    allowed_nulls = {"LONGITUDINAL_SEGMENT_TWIST_STICK_FIGURE_INVARIANT"}
    observability_pass = bool(
        all(row["classification"] in allowed_nulls for row in observability["nullspace_vectors"])
        and all(row["stick_figure_axis_observable"] for row in observability["per_segment"].values())
        and not any(row["classification"] == "GENUINELY_UNOBSERVABLE_BODY_GEOMETRY"
                    for row in observability["nullspace_vectors"])
    )
    q1_finite = all(audit.finite and audit.cholesky_failures == 0 for audit in q1_audits.values())
    gate = {
        "optimizer_success": bool(result.success), "proper_rotations": proper,
        "finite_plausible_geometry": finite_plausible, "q1_numerical": q1_finite,
        "actual_observability": observability_pass,
        "optional_action_leave_one_out": stability["optional_action_leave_one_out"]["pass"],
        "repeatability": repeatability["pass"],
    }
    passed = all(gate.values())
    if not result.success or not np.isfinite(result.x).all():
        verdict = "BLOCKED_FRAME_CALIBRATION_NUMERICAL"
    elif not observability_pass:
        verdict = "BLOCKED_FRAME_OBSERVABILITY"
    elif not proper or not finite_plausible or not q1_finite:
        verdict = "BLOCKED_FRAME_CALIBRATION_INVALID_PARAMETERS"
    elif not stability["optional_action_leave_one_out"]["pass"] or not repeatability["pass"]:
        verdict = "BLOCKED_FRAME_CALIBRATION_UNSTABLE"
    else:
        verdict = "FRAME_CALIBRATION_PASS"
    frame_result = {
        "verdict": verdict, "pass": passed, "gates": gate,
        "calibration_ledger_sha256": ledger_hash, "layout_sha256": layout_hash,
        "initialization_window": "initial_still attempt 2",
        "global_yaw": "fixed to zero as coordinate convention; not measured heading",
        "candidate": manifest, "sample_audit": {k: v for k, v in sample_audit.items() if k != "nearest"},
        "optimizer": {"cost": float(result.cost), "optimality": float(result.optimality),
                      "nfev": int(result.nfev), "message": str(result.message)},
        "geometry_bound_margin_m": {
            name: float(margin) for name, margin in zip(GEOMETRY_NAMES, bound_margin)
        },
    }
    null_effects = [row["physical_effect"] for row in observability["nullspace_vectors"]]
    full_pose_pass = observability["nullity"] == 0
    centreline_observability_pass = all(effect == "segment axial twist only" for effect in null_effects)
    if not centreline_observability_pass:
        centreline_verdict = "FAIL_CENTERLINE_OBSERVABILITY"
    elif not finite_plausible and not repeatability["pass"]:
        centreline_verdict = "FAIL_GEOMETRY_BOUND_SATURATION_AND_NOT_REPEATABLE"
    elif not finite_plausible:
        centreline_verdict = "FAIL_GEOMETRY_BOUND_SATURATION"
    elif not repeatability["pass"]:
        centreline_verdict = "FAIL_GEOMETRY_NOT_REPEATABLE"
    elif not stability["optional_action_leave_one_out"]["pass"]:
        centreline_verdict = "FAIL_OPTIONAL_ACTION_INSTABILITY"
    else:
        centreline_verdict = "PASS"
    repeat_fits = repeatability["fits_evaluated_on_full_calibration_residual"]
    repeat_cost_relative = abs(
        repeat_fits[0]["full_physical_sse"] - repeat_fits[1]["full_physical_sse"]
    ) / max(1.0, min(repeat_fits[0]["full_physical_sse"], repeat_fits[1]["full_physical_sse"]))
    changed_dimensions = [
        {"parameter": name, "fit0_minus_fit1_m": delta, "absolute_change_m": abs(delta)}
        for name, delta in sorted(
            repeatability["dimension_delta_fit0_minus_fit1_m"].items(),
            key=lambda item: abs(item[1]), reverse=True)
    ]
    if multistart["pass"] and not repeatability["pass"]:
        instability_mechanism = "GENUINE_DATA_SUBSET_INSUFFICIENCY_OR_ACTION_DEPENDENCE"
    elif not multistart["pass"]:
        instability_mechanism = "LOCAL_MINIMUM_OR_NONCONVEX_PARAMETER_TRADEOFF"
    else:
        instability_mechanism = "NO_REPEATABILITY_BLOCK"
    interpretation = {
        "FULL_SEGMENT_POSE_CALIBRATION": {
            "pass": full_pose_pass,
            "verdict": ("PASS" if full_pose_pass else
                        ("FAIL_AXIAL_TWIST_UNAVAILABLE" if centreline_observability_pass
                         else "FAIL_SEGMENT_POSE_NULLSPACE")),
            "unavailable_dof": null_effects,
        },
        "STICK_FIGURE_CENTERLINE_CALIBRATION": {
            "pass": centreline_verdict == "PASS", "verdict": centreline_verdict,
            "observability_subgate_pass": centreline_observability_pass,
            "axial_twist_rendering": "OMIT_OR_VISIBLY_LABEL_UNAVAILABLE",
        },
        "null_direction_physical_effects": [
            {"index": row["index"], "physical_effect": row["physical_effect"],
             "finite_effect": row["finite_effect"],
             "finite_perturbation_norm": row["finite_perturbation_norm"],
             "tolerances": row["invariance_tolerances"]}
            for row in observability["nullspace_vectors"]
        ],
        "stability_separation": {
            "mandatory_action_dependence": stability["mandatory_action_dependence"],
            "optional_action_leave_one_out": stability["optional_action_leave_one_out"],
            "independent_multistart": multistart,
            "interleaved_repeatability": repeatability,
        },
        "repeatability_blocker_diagnosis": {
            "changed_dimensions_ranked": changed_dimensions,
            "fit_bound_hits": [row["bound_hits_1e_4"] for row in repeat_fits],
            "full_residual_sse": [row["full_physical_sse"] for row in repeat_fits],
            "relative_full_residual_cost_difference": repeat_cost_relative,
            "equivalent_cost_at_1_percent": repeat_cost_relative <= .01,
            "lever_arms_fitted": False,
            "lever_arm_length_tradeoff_possible": False,
            "diagnosis": instability_mechanism,
        },
    }
    _dump(output / "FRAME_CALIBRATION_RESULT.json", frame_result)
    _dump(output / "OBSERVABILITY_SVD.json", observability)
    _dump(output / "CALIBRATION_STABILITY.json", {"leave_one_action_out": stability,
                                                     "repeatability": repeatability,
                                                     "independent_multistart": multistart})
    _dump(output / "CALIBRATION_PHYSICAL_INTERPRETATION.json", interpretation)
    _dump(output / "CALIBRATION_CANDIDATE.json", manifest)
    _dump(output / "UWB_CALIBRATION_ACCOUNTING.json", t4_accounting)
    _dump(output / "IMU_CALIBRATION_AUDIT.json", {
        "initialization_window": "initial_still attempt 2", "nodes": audits_as_json(q1_audits),
        "bias_treatment": biases,
    })
    _dump(output / "CALIBRATION_REJECTION_LEDGER.json", rejections)
    _dump(output / "CALIBRATION_CAPSULE_RESULT.json", {
        "verdict": verdict, "pass": passed, "only_payload_input": str(calibration_ledger),
        "calibration_ledger_sha256": ledger_hash, "layout_sha256": layout_hash,
    })
    return frame_result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration-ledger", type=Path, required=True)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.calibration_ledger, args.layout, args.output)
    print(json.dumps({"verdict": result["verdict"], "pass": result["pass"]}, sort_keys=True))
    return 0 if result["pass"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
