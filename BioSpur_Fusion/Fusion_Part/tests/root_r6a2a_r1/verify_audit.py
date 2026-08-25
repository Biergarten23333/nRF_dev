#!/usr/bin/env python3
"""Independent verifier for a Root-R6A2A-R1 execution-audit directory."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

import numpy as np

from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a2a.contracts import registry_from_sealed_addendum
from biospur_fusion.root_r6a2a.qualification import SCENARIOS, _validator_scenarios
from biospur_fusion.root_r6a2a.shadow import (
    IntegratedShadowEstimator,
    _attribution,
    _state_payload,
    build_synthetic_calibration,
    corrected_body_model,
    generate_imu_streams,
    generate_uwb_observations,
    run_scenario,
    truth_state,
)

from audit_common import (
    CHECKPOINT_HEAD,
    CONFIG_PATHS,
    IMPLEMENTATION_PATHS,
    NOT_RECORDED,
    PARENT_NAME,
    PARENT_SHA256SUMS_SHA,
    canonical_sha256,
    phase_for_step,
    rotation_error_rad,
    sha256_file,
    snapshot_files,
    write_json,
)


REQUIRED = (
    "FINAL_AUDIT.md",
    "FINAL_AUDIT.json",
    "PARENT_ARTIFACT_INVENTORY.json",
    "PARENT_CLAIM_TO_EVIDENCE.csv",
    "PARENT_MISSING_METRICS.json",
    "PARENT_SCENARIO_SUMMARY.csv",
    "PARENT_MODE_TRANSITIONS.csv",
    "PARENT_FAULT_ATTRIBUTION.csv",
    "PARENT_COVARIANCE_SUMMARY.csv",
    "PARENT_VALIDATOR_RESULTS.csv",
    "EXECUTION_PATH_TRACE.json",
    "EXECUTION_COUNTERS.csv",
    "CLEAN_BASELINE_EXECUTION_TRACE.csv",
    "FULL_UWB_OUTAGE_EXECUTION_TRACE.csv",
    "SCENARIO_STATE_ERROR_SUMMARY.csv",
    "PER_JOINT_ERROR_SUMMARY.csv",
    "PER_NODE_BIAS_ERROR_SUMMARY.csv",
    "UWB_RESIDUAL_SUMMARY.csv",
    "RECOVERY_METRICS.csv",
    "FAULT_TRUTH_DATAFLOW_AUDIT.md",
    "FAULT_TRUTH_DATAFLOW_AUDIT.json",
    "FAULT_LABEL_NEGATIVE_CONTROLS.json",
    "ANOMALOUS_SCENARIO_AUDIT.csv",
    "COVARIANCE_TRUTH_CONSISTENCY.csv",
    "INNOVATION_CONSISTENCY.csv",
    "COVARIANCE_COVERAGE.json",
    "COVARIANCE_AUDIT.md",
    "EXECUTION_ABLATION_RESULTS.csv",
    "EXECUTION_ABLATION_ANALYSIS.md",
    "REPLAY_EQUIVALENCE.json",
    "PROTECTED_HASHES_BEFORE_AFTER.json",
    "SCENARIO_TIMESERIES.parquet",
    "MODE_AND_HEALTH_TIMESERIES.parquet",
    "STATE_AND_COVARIANCE_TIMESERIES.parquet",
    "RECONSTRUCTED_STATE_AND_COVARIANCE.npz",
)


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: Any = None) -> None:
    checks.append({"name": name, "pass": bool(passed), "detail": detail})


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def checksum_parent(parent: Path) -> tuple[bool, dict[str, Any]]:
    expected = {}
    for line in (parent / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        expected[name.lstrip("* ")] = digest
    mismatches = {
        name: {"expected": digest, "actual": sha256_file(parent / name)}
        for name, digest in expected.items()
        if sha256_file(parent / name) != digest
    }
    detail = {
        "sha256sums_sha256": sha256_file(parent / "SHA256SUMS"),
        "verified_files": len(expected),
        "mismatches": mismatches,
    }
    return not mismatches and detail["sha256sums_sha256"] == PARENT_SHA256SUMS_SHA, detail


def replay_worker(fusion_text: str, spec: Any) -> tuple[str, dict[str, Any]]:
    return spec.name, run_scenario(Path(fusion_text), spec)


def independently_run_control(fusion: Path) -> dict[str, Any]:
    spec = next(row for row in SCENARIOS if row.name == "uwb_single_anchor_fault")
    registry = registry_from_sealed_addendum(fusion)
    model = corrected_body_model(fusion)
    truth_calibration = build_synthetic_calibration(model, registry, geometry=spec.geometry)
    estimator_calibration = build_synthetic_calibration(model, registry, geometry=spec.geometry)
    initial_truth = truth_state(model, 0.0, spec.seed, spec.low_motion)
    initial = replace(
        initial_truth,
        root_translation_model_m=initial_truth.root_translation_model_m + np.array([0.055, -0.035, 0.018]),
        root_velocity_model_mps=initial_truth.root_velocity_model_mps + np.array([0.015, -0.01, 0.0]),
        gyro_bias_rad_s={node: np.zeros(3) for node in model.imu_ids},
        accel_bias_mps2={node: np.zeros(3) for node in model.imu_ids},
        covariance=np.eye(initial_truth.covariance.shape[0]) * 0.0025,
    )
    estimator = IntegratedShadowEstimator(model, estimator_calibration, registry, initial)
    rng = np.random.default_rng(spec.seed)
    for step_index in range(int(round(spec.duration_s / spec.step_s))):
        t0, t1 = step_index * spec.step_s, (step_index + 1) * spec.step_s
        streams = generate_imu_streams(model, truth_calibration, spec, step_index, t0, t1, rng)
        observations = generate_uwb_observations(model, truth_calibration, spec, step_index, t0, t1, rng)
        estimator.step(streams, observations, t1, spec.geometry)
    payload = {
        "states": [_state_payload(state) for state in estimator.states],
        "modes": estimator.mode_history,
        "transitions": estimator.health.transitions(),
        "preintegration_status_counts": dict(estimator.preintegration_status_counts),
    }
    original = _attribution(estimator, spec)
    renamed = _attribution(estimator, replace(spec, name="INDEPENDENT_RENAME"))
    permuted = _attribution(estimator, replace(spec, fault="wrong_bone_geometry"))
    conflicting = _attribution(estimator, replace(spec, fault="single_tag_fault"))
    return {
        "estimator_output_sha256": canonical_sha256(payload),
        "original": original,
        "renamed": renamed,
        "permuted": permuted,
        "conflicting": conflicting,
        "rename_pass": renamed == original,
        "permutation_pass": permuted == original,
        "conflicting_pass": conflicting == original,
        "hard_failure_reproduced": permuted != original or conflicting != original,
    }


def verify_parent_exports(parent: Path, audit: Path) -> tuple[bool, dict[str, Any]]:
    scenarios = json.loads((parent / "SYNTHETIC_SCENARIO_RESULTS.json").read_text())["scenarios"]
    scenario_rows = {row["scenario_id"]: row for row in read_csv(audit / "PARENT_SCENARIO_SUMMARY.csv")}
    attribution_rows = {row["scenario_id"]: row for row in read_csv(audit / "PARENT_FAULT_ATTRIBUTION.csv")}
    covariance_rows = {row["scenario_id"]: row for row in read_csv(audit / "PARENT_COVARIANCE_SUMMARY.csv")}
    mode_rows = read_csv(audit / "PARENT_MODE_TRANSITIONS.csv")
    failures = []
    expected_mode_count = 0
    for scenario_id, result in scenarios.items():
        if scenario_id not in scenario_rows or scenario_id not in attribution_rows or scenario_id not in covariance_rows:
            failures.append(f"missing:{scenario_id}")
            continue
        spec, metrics = result["scenario"], result["metrics"]
        row = scenario_rows[scenario_id]
        expected_mode_count += len(metrics["mode_sequence"])
        checks = (
            row["seed"] == str(spec["seed"]),
            row["fault_type"] == spec["fault"],
            row["native_sample_counts_by_node"] == NOT_RECORDED,
            row["uwb_observation_count"] == NOT_RECORDED,
            row["fault_magnitude"] == NOT_RECORDED,
            row["expected_attribution"] == NOT_RECORDED,
            attribution_rows[scenario_id]["reported_attribution"] == metrics["fault_attribution"],
            covariance_rows[scenario_id]["minimum_eigenvalue"] == str(metrics["covariance_min_eigenvalue"]),
            covariance_rows[scenario_id]["maximum_eigenvalue"] == NOT_RECORDED,
        )
        if not all(checks):
            failures.append(f"field:{scenario_id}")
    mode_failures = []
    by_scenario: dict[str, list[dict[str, str]]] = {}
    for row in mode_rows:
        by_scenario.setdefault(row["scenario_id"], []).append(row)
    for scenario_id, result in scenarios.items():
        rows = by_scenario.get(scenario_id, [])
        rows.sort(key=lambda value: int(value["sequence_index"]))
        modes = [row["to_mode"] for row in rows]
        times = [float(row["time_s"]) for row in rows]
        expected_times = [index * result["scenario"]["step_s"] for index in range(len(modes))]
        if modes != result["metrics"]["mode_sequence"] or not np.allclose(times, expected_times, atol=0.0, rtol=0.0):
            mode_failures.append(scenario_id)
    validators = json.loads((parent / "FAULT_INJECTION_QUALIFICATION.json").read_text())["validator_results"]
    validator_rows = read_csv(audit / "PARENT_VALIDATOR_RESULTS.csv")
    detail = {
        "scenario_count": len(scenario_rows),
        "expected_scenario_count": len(scenarios),
        "mode_row_count": len(mode_rows),
        "expected_mode_row_count": expected_mode_count,
        "validator_count": len(validator_rows),
        "expected_validator_count": len(validators),
        "field_failures": failures,
        "mode_failures": mode_failures,
    }
    return (
        len(scenario_rows) == len(scenarios) == 29
        and len(validator_rows) == len(validators) == 3
        and len(mode_rows) == expected_mode_count
        and not failures
        and not mode_failures
    ), detail


def verify_state_and_covariance(audit: Path) -> tuple[bool, dict[str, Any]]:
    archive = np.load(audit / "RECONSTRUCTED_STATE_AND_COVARIANCE.npz")
    ordering = json.loads((audit / "STATE_ORDER.json").read_text())["ordering"]
    joint_names = [
        label.split(":")[1]
        for label in ordering
        if label.startswith("joint_orientation:") and ":x_rad" in label
    ]
    node_names = [
        label.split(":")[1]
        for label in ordering
        if label.startswith("gyro_bias:") and ":x_rad_s" in label
    ]
    state_rows = {}
    with (audit / "RECONSTRUCTED_STATE_ERROR_EVIDENCE.jsonl").open() as handle:
        for line in handle:
            row = json.loads(line)
            state_rows[(row["scenario_id"], int(row["state_index"]))] = row
    scenario_summary = {
        (row["scenario_id"], row["phase"]): row
        for row in read_csv(audit / "SCENARIO_STATE_ERROR_SUMMARY.csv")
    }
    joint_summary = {
        (row["scenario_id"], row["phase"], row["joint_id"]): row
        for row in read_csv(audit / "PER_JOINT_ERROR_SUMMARY.csv")
    }
    bias_summary = {
        (row["scenario_id"], row["phase"], row["node_id"]): row
        for row in read_csv(audit / "PER_NODE_BIAS_ERROR_SUMMARY.csv")
    }
    covariance_summary = {
        (row["scenario_id"], row["phase"]): row
        for row in read_csv(audit / "COVARIANCE_TRUTH_CONSISTENCY.csv")
    }
    failures = []
    verified_states = 0
    verified_joints = 0
    verified_biases = 0
    for spec in SCENARIOS:
        estimate = archive[f"{spec.name}__estimate"]
        truth = archive[f"{spec.name}__truth"]
        tangent = archive[f"{spec.name}__tangent_error"]
        covariance = archive[f"{spec.name}__covariance"]
        root_errors = np.linalg.norm(estimate[:, 0:3] - truth[:, 0:3], axis=1)
        root_orientation = np.asarray(
            [rotation_error_rad(truth[index, 3:6], estimate[index, 3:6]) for index in range(len(estimate))]
        )
        velocity = np.linalg.norm(estimate[:, 6:9] - truth[:, 6:9], axis=1)
        joint_errors = np.zeros((len(estimate), 9))
        for state_index in range(len(estimate)):
            expected_root_tangent = so3_log(
                so3_exp(truth[state_index, 3:6]).T @ so3_exp(estimate[state_index, 3:6])
            )
            if not np.allclose(tangent[state_index, 0:3], estimate[state_index, 0:3] - truth[state_index, 0:3], atol=1e-14, rtol=0.0):
                failures.append(f"root_tangent:{spec.name}:{state_index}")
            if not np.allclose(tangent[state_index, 3:6], expected_root_tangent, atol=1e-14, rtol=0.0):
                failures.append(f"rotation_tangent:{spec.name}:{state_index}")
            for joint_index in range(9):
                start = 9 + 3 * joint_index
                joint_errors[state_index, joint_index] = rotation_error_rad(
                    truth[state_index, start:start + 3], estimate[state_index, start:start + 3]
                )
            row = state_rows[(spec.name, state_index)]
            nees = float(tangent[state_index] @ np.linalg.solve(covariance[state_index], tangent[state_index]))
            comparisons = (
                np.isclose(float(row["root_position_error_m"]), root_errors[state_index], atol=1e-14, rtol=0.0),
                np.isclose(float(row["root_orientation_geodesic_error_rad"]), root_orientation[state_index], atol=1e-14, rtol=0.0),
                np.isclose(float(row["root_velocity_error_mps"]), velocity[state_index], atol=1e-14, rtol=0.0),
                np.isclose(float(row["full_state_tangent_nees"]), nees, atol=1e-11, rtol=1e-12),
                np.isclose(float(row["covariance_min_eigenvalue"]), np.linalg.eigvalsh(covariance[state_index])[0], atol=1e-13, rtol=1e-12),
                np.isclose(float(row["covariance_max_eigenvalue"]), np.linalg.eigvalsh(covariance[state_index])[-1], atol=1e-13, rtol=1e-12),
            )
            if not all(comparisons):
                failures.append(f"state_metric:{spec.name}:{state_index}")
            verified_states += 1
        overall = scenario_summary[(spec.name, "ALL")]
        if not np.isclose(float(overall["root_position_rmse_m"]), np.sqrt(np.mean(root_errors**2)), atol=1e-14, rtol=0.0):
            failures.append(f"scenario_rmse:{spec.name}")
        if not np.isclose(float(overall["root_orientation_rmse_rad"]), np.sqrt(np.mean(root_orientation**2)), atol=1e-14, rtol=0.0):
            failures.append(f"orientation_rmse:{spec.name}")
        covariance_row = covariance_summary[(spec.name, "ALL")]
        nees_values = np.asarray(
            [tangent[index] @ np.linalg.solve(covariance[index], tangent[index]) for index in range(len(tangent))]
        )
        if not np.isclose(float(covariance_row["full_state_tangent_nees_mean"]), nees_values.mean(), atol=1e-11, rtol=1e-12):
            failures.append(f"covariance_nees:{spec.name}")
        phase_by_state = [
            phase_for_step(spec, None if index == 0 else index - 1, state_rows[(spec.name, index)]["mode"])
            for index in range(len(estimate))
        ]
        for phase in sorted(set(phase_by_state)):
            indices = [index for index, value in enumerate(phase_by_state) if value == phase]
            for joint_index in range(9):
                name = joint_names[joint_index]
                row = joint_summary[(spec.name, phase, name)]
                values = joint_errors[indices, joint_index]
                if not np.isclose(float(row["geodesic_error_rmse_rad"]), np.sqrt(np.mean(values**2)), atol=1e-14, rtol=0.0):
                    failures.append(f"joint:{spec.name}:{phase}:{name}")
                verified_joints += 1
            gyro_start = 9 + 54
            accel_start = gyro_start + 30
            for node_index, node in enumerate(node_names):
                gyro_values = np.linalg.norm(
                    estimate[indices, gyro_start + 3 * node_index:gyro_start + 3 * node_index + 3]
                    - truth[indices, gyro_start + 3 * node_index:gyro_start + 3 * node_index + 3], axis=1
                )
                accel_values = np.linalg.norm(
                    estimate[indices, accel_start + 3 * node_index:accel_start + 3 * node_index + 3]
                    - truth[indices, accel_start + 3 * node_index:accel_start + 3 * node_index + 3], axis=1
                )
                row = bias_summary[(spec.name, phase, node)]
                if not np.isclose(float(row["gyro_bias_error_rmse_rad_s"]), np.sqrt(np.mean(gyro_values**2)), atol=1e-14, rtol=0.0):
                    failures.append(f"gyro_bias:{spec.name}:{phase}:{node}")
                if not np.isclose(float(row["accel_bias_error_rmse_mps2"]), np.sqrt(np.mean(accel_values**2)), atol=1e-14, rtol=0.0):
                    failures.append(f"accel_bias:{spec.name}:{phase}:{node}")
                verified_biases += 1
    return not failures, {
        "verified_states": verified_states,
        "verified_joint_phase_groups": verified_joints,
        "verified_bias_phase_groups": verified_biases,
        "failures": failures[:50],
        "failure_count": len(failures),
    }


def verify_bone_and_fk(fusion: Path) -> dict[str, Any]:
    registry = registry_from_sealed_addendum(fusion)
    model = corrected_body_model(fusion)
    calibration = build_synthetic_calibration(model, registry)
    closures = []
    for time_s in np.linspace(0.0, 1.2, 13):
        state = truth_state(model, float(time_s), 6201)
        closures.append(float(np.max(np.abs(model.all_predictions(state, calibration)["kinematic_residuals"]))))
    bone_slots = {key: value.value for key, value in calibration.slots.items() if key.startswith("bone_length:")}
    return {
        "pass": len(bone_slots) > 0 and max(closures) <= 1e-10,
        "bone_stretch_state_present": False,
        "bone_slot_count": len(bone_slots),
        "maximum_fk_closure_m": max(closures),
        "finding": "Bone invariance is structural/static-slot evidence, not a dynamic estimated bone metric.",
    }


def seal(audit: Path) -> int:
    paths = sorted(path for path in audit.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    lines = [f"{sha256_file(path)}  {path.name}" for path in paths]
    (audit / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    return len(paths)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit", type=Path)
    parser.add_argument("--fusion", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    fusion, audit = args.fusion.resolve(), args.audit.resolve()
    parent = fusion / "logs" / PARENT_NAME
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    checks: list[dict[str, Any]] = []

    missing = [name for name in REQUIRED if not (audit / name).is_file()]
    add(checks, "required_artifacts_present", not missing, missing)
    parent_pass, parent_detail = checksum_parent(parent)
    add(checks, "parent_byte_exact", parent_pass, parent_detail)

    implementation_manifest = json.loads((parent / "IMPLEMENTATION_FILES.json").read_text())
    expected = {row["path"]: row["sha256"] for row in implementation_manifest["files"]}
    actual = {key: value["sha256"] for key, value in snapshot_files(fusion, IMPLEMENTATION_PATHS).items()}
    add(checks, "current_implementation_hashes_match", actual == expected, {"expected": expected, "actual": actual})

    exports_pass, exports_detail = verify_parent_exports(parent, audit)
    add(checks, "29_scenarios_3_validators_and_parent_exports_recomputed", exports_pass, exports_detail)
    add(checks, "chronological_mode_sequences_recomputed", not exports_detail["mode_failures"], exports_detail["mode_failures"])

    parent_scenarios = json.loads((parent / "SYNTHETIC_SCENARIO_RESULTS.json").read_text())["scenarios"]
    replays = {}
    with ProcessPoolExecutor(max_workers=min(args.workers, len(SCENARIOS))) as executor:
        futures = [executor.submit(replay_worker, str(fusion), spec) for spec in SCENARIOS]
        for future in futures:
            scenario_id, result = future.result()
            replays[scenario_id] = result
    replay_failures = [name for name, result in replays.items() if result != parent_scenarios[name]]
    add(checks, "all_29_parent_scenarios_independently_replayed_exactly", not replay_failures, replay_failures)
    equivalence = json.loads((audit / "REPLAY_EQUIVALENCE.json").read_text())
    add(
        checks,
        "replay_equivalence_artifact_cross_checked",
        equivalence["all_exact"] and equivalence["instrumentation_all_exact"] and not replay_failures,
        {"reported": equivalence["all_exact"], "instrumented": equivalence["instrumentation_all_exact"]},
    )

    state_pass, state_detail = verify_state_and_covariance(audit)
    add(checks, "state_joint_bias_and_covariance_truth_calculations_recomputed", state_pass, state_detail)
    bone = verify_bone_and_fk(fusion)
    add(checks, "bone_invariance_and_fk_recomputed", bone["pass"], bone)

    control = independently_run_control(fusion)
    reported_control = json.loads((audit / "FAULT_LABEL_NEGATIVE_CONTROLS.json").read_text())
    control_pass = (
        control["hard_failure_reproduced"]
        and control["rename_pass"]
        and not control["permutation_pass"]
        and not control["conflicting_pass"]
        and reported_control["hard_scientific_failure"]
        and control["original"] == reported_control["truth_label_permutation_test"]["original_attribution"]
        and control["permuted"] == reported_control["truth_label_permutation_test"]["permuted_attribution"]
        and control["conflicting"] == reported_control["same_measurements_conflicting_label_test"]["conflicting_attribution"]
    )
    add(checks, "fault_label_negative_controls_independently_rerun", control_pass, control)

    ledger = json.loads((fusion / CONFIG_PATHS[-1]).read_text())
    ledger_pass = len(ledger["slots"]) == 87 and all(
        row["value"] is None and row["status"] == "FROZEN_UNCERTAIN" for row in ledger["slots"]
    )
    add(checks, "real_87_slots_remain_null_and_frozen", ledger_pass, {"sha256": sha256_file(fusion / CONFIG_PATHS[-1]), "count": len(ledger["slots"])})

    protected = json.loads((audit / "PROTECTED_HASHES_BEFORE_AFTER.json").read_text())
    current_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=fusion, check=True, text=True, capture_output=True).stdout.strip()
    merge_head = subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=fusion, text=True, capture_output=True).returncode == 0
    source_text = (fusion / "tests/root_r6a2a_r1/run_audit.py").read_text() + (fusion / "tests/root_r6a2a_r1/verify_audit.py").read_text()
    # Assemble forbidden path fragments so the verifier does not flag its own
    # literal search terms as an apparent payload reference.
    forbidden_source_fragments = (
        "data" + "sets/",
        "C1" + "_arrays",
        "held" + "_out_actions",
    )
    real_payload_source_pass = all(token not in source_text for token in forbidden_source_fragments)
    add(
        checks,
        "no_real_payload_opened_or_registry_written",
        protected["real_payloads_opened"] is False
        and protected["real_registry_writes"] == 0
        and real_payload_source_pass
        and ledger_pass,
        {"source_path_audit": real_payload_source_pass, "declared_accesses": protected["real_payload_path_accesses"]},
    )
    add(
        checks,
        "no_commit_merge_or_push_performed",
        current_head == CHECKPOINT_HEAD
        and protected["git_head_unchanged"]
        and not merge_head
        and not protected["r6a2a_commit_performed"]
        and not protected["merge_performed"]
        and not protected["push_performed"],
        {"head": current_head, "merge_head_present": merge_head, "push_basis": "audit procedure contains no git-network or push operation; local checkpoint/ref remained unchanged"},
    )
    final = json.loads((audit / "FINAL_AUDIT.json").read_text())
    add(
        checks,
        "narrow_failure_verdict_matches_reproduced_evidence",
        final["principal_verdict"] == "FAIL_ROOT_R6A2A_R1_FAULT_TRUTH_LEAKAGE" and control["hard_failure_reproduced"],
        final["principal_verdict"],
    )

    result = {
        "schema": "biospur-root-r6a2a-r1-independent-verification-v1",
        "verifier_sha256": sha256_file(Path(__file__).resolve()),
        "primary_report_trusted_without_recomputation": False,
        "check_count": len(checks),
        "checks": checks,
        "all_pass": all(row["pass"] for row in checks),
    }
    write_json(audit / "INDEPENDENT_VERIFICATION.json", result)
    file_count = seal(audit)
    result["sealed_file_count_excluding_SHA256SUMS"] = file_count
    write_json(audit / "INDEPENDENT_VERIFICATION.json", result)
    file_count = seal(audit)
    if not result["all_pass"]:
        raise SystemExit(1)
    print(json.dumps({"all_pass": True, "check_count": len(checks), "sealed_file_count": file_count}, sort_keys=True))


if __name__ == "__main__":
    main()
