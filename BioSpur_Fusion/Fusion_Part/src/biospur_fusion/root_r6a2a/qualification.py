"""Predeclared deterministic R6A2A fault qualification and gates A--AB."""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
import hashlib
from itertools import repeat
import json
from pathlib import Path
from typing import Any

import numpy as np

from biospur_fusion.root_r6a1c.adapter import OBSOLETE_WRIST_MAP, evaluate_r6a2_request

from .contracts import (
    ALL_NODES,
    COMMON_NINE,
    FAMILY_BSF31CC,
    FAMILY_COMMON_NINE,
    UnifiedHardwareRegistry,
    build_contract_bundle,
    registry_from_sealed_addendum,
    synthetic_authorization_request,
)
from .shadow import ScenarioSpec, run_scenario


FROZEN_THRESHOLDS = {
    "schema": "biospur-root-r6a2a-frozen-qualification-thresholds-v1",
    "frozen_before_execution": True,
    "clean_root_position_rmse_max_m": 0.20,
    "clean_root_orientation_rmse_max_rad": 0.20,
    "clean_joint_orientation_rmse_max_rad": 0.25,
    "clean_velocity_rmse_max_mps": 0.75,
    "covariance_symmetry_max_abs": 1e-10,
    "covariance_min_eigenvalue": -1e-10,
    "bone_length_change_max_m": 1e-12,
    "joint_closure_max_m": 1e-10,
    "fault_detection_latency_max_s": 0.35,
    "reentry_correction_max_m": 0.0800001,
    "reentry_state_step_max_m": 0.20,
    "low_motion_root_error_max_m": 0.09,
    "minimum_preintegration_covariance_consumed": 80,
    "minimum_preintegration_bias_jacobian_consumed": 80,
}


SCENARIOS = (
    ScenarioSpec("clean_baseline_seed_6201", "clean", seed=6201),
    ScenarioSpec("clean_baseline_seed_6223", "clean", seed=6223),
    ScenarioSpec("clean_low_motion_jitter", "clean", seed=6247, low_motion=True),
    ScenarioSpec("imu_bounded_sample_gap", "imu", "bounded_sample_gap", fault_end_step=3),
    ScenarioSpec("imu_long_gap", "imu", "long_gap"),
    ScenarioSpec("imu_duplicate_timestamp", "imu", "duplicate_timestamp"),
    ScenarioSpec("imu_timestamp_reversal", "imu", "timestamp_reversal"),
    ScenarioSpec("imu_boot_epoch_reset", "imu", "boot_epoch_reset"),
    ScenarioSpec("imu_saturation", "imu", "imu_saturation"),
    ScenarioSpec("imu_invalid_value", "imu", "invalid_imu_value"),
    ScenarioSpec("imu_noise_burst", "imu", "imu_noise_burst"),
    ScenarioSpec("imu_gyro_bias_step", "imu", "gyro_bias_step"),
    ScenarioSpec("imu_gyro_bias_ramp", "imu", "gyro_bias_ramp"),
    ScenarioSpec("imu_single_node_dropout", "imu", "single_node_dropout"),
    ScenarioSpec("uwb_single_large_outlier", "uwb", "single_uwb_outlier", fault_end_step=3),
    ScenarioSpec("uwb_positive_nlos_bias_burst", "uwb", "nlos_bias_burst"),
    ScenarioSpec("uwb_persistent_bad_link", "uwb", "persistent_bad_link", fault_end_step=11),
    ScenarioSpec("uwb_single_anchor_fault", "uwb", "single_anchor_fault"),
    ScenarioSpec("uwb_single_tag_fault", "uwb", "single_tag_fault"),
    ScenarioSpec("uwb_tag_dropout", "uwb", "tag_dropout"),
    ScenarioSpec("uwb_multi_anchor_outage", "uwb", "multi_anchor_outage"),
    ScenarioSpec("uwb_low_vertical_diversity", "uwb", "low_vertical_geometry", geometry="LOW_VERTICAL_DIVERSITY"),
    ScenarioSpec("uwb_full_outage_recovery", "uwb", "global_uwb_outage", duration_s=1.6, fault_end_step=7),
    ScenarioSpec("model_wrong_synthetic_lever", "model", "wrong_synthetic_lever"),
    ScenarioSpec("model_rotational_skin_slip", "model", "rotational_skin_slip"),
    ScenarioSpec("model_transient_wrist_ghost", "model", "wrist_ghost_rotation", fault_end_step=4),
    ScenarioSpec("model_wrong_bone_geometry", "model", "wrong_bone_geometry"),
    ScenarioSpec("model_persistent_post_motion_offset", "model", "persistent_post_motion_offset", fault_end_step=11),
    ScenarioSpec("node_imu_and_uwb_dropout", "combined", "node_imu_and_uwb_dropout"),
)


def fault_injection_manifest() -> dict[str, Any]:
    validators = [
        "obsolete_wrist_map_attempt",
        "wrong_hardware_family_attempt",
        "missing_real_calibration_real_mode_attempt",
    ]
    return {
        "schema": "biospur-root-r6a2a-fault-injection-manifest-v1",
        "parameters_predeclared_before_execution": True,
        "thresholds": FROZEN_THRESHOLDS,
        "health_thresholds_predeclared": {
            "uwb_absolute_normalized_innovation_suspect": 6.0,
            "robust_huber_transition_sigma": 2.5,
            "imu_cross_time_angular_rate_departure_rad_s": 0.18,
            "bad_anchor_or_tag_fraction": 0.45,
            "low_motion_speed_mps": 0.04,
            "low_motion_jitter_correction_m": 0.045,
            "maximum_single_uwb_correction_m": 0.08,
        },
        "scenario_count": len(SCENARIOS) + len(validators),
        "executable_scenarios": [spec.__dict__ for spec in SCENARIOS],
        "fail_closed_validator_scenarios": validators,
        "multiple_deterministic_seeds": sorted({spec.seed for spec in SCENARIOS}),
        "real_payload_access": False,
    }


def _validator_scenarios(fusion: Path) -> dict[str, Any]:
    registry = registry_from_sealed_addendum(fusion)
    request = synthetic_authorization_request(registry)
    obsolete = dict(request)
    obsolete["node_identity_map"] = dict(OBSOLETE_WRIST_MAP)
    obsolete_decision = evaluate_r6a2_request(obsolete)

    wrong_family = dict(registry.family_by_node)
    wrong_family["BSF31CC"] = FAMILY_COMMON_NINE
    wrong_family_failed = False
    wrong_family_reason = None
    try:
        UnifiedHardwareRegistry(wrong_family, registry.sealed_binding_sha256)
    except ValueError as error:
        wrong_family_failed = True
        wrong_family_reason = str(error)

    real = dict(request)
    real.update(
        {
            "mode": "REAL_BODY_UPDATE",
            "geometry_kind": "REAL_DEFERRED",
            "hardware_revision_ids": {node: None for node in ALL_NODES},
            "all_real_geometry_qualified": False,
            "signed_axis_validation": "PENDING",
            "process_noise_qualified": False,
            "hardware_levers_qualified": False,
            "v4_to_navigation_qualified": False,
            "clock_boot_epochs_match": False,
        }
    )
    real_decision = evaluate_r6a2_request(real)
    return {
        "obsolete_wrist_map_attempt": {
            "authorized": obsolete_decision.authorized,
            "blockers": list(obsolete_decision.blockers),
            "state_execution_count": 0,
        },
        "wrong_hardware_family_attempt": {
            "failed_closed": wrong_family_failed,
            "reason": wrong_family_reason,
            "state_execution_count": 0,
        },
        "missing_real_calibration_real_mode_attempt": {
            "authorized": real_decision.authorized,
            "blockers": list(real_decision.blockers),
            "state_execution_count": 0,
        },
    }


def _gate(gates: dict[str, Any], key: str, name: str, passed: bool, **metrics: Any) -> None:
    gates[key] = {"name": name, "pass": bool(passed), "metrics": metrics}


def run_qualification(fusion: Path) -> dict[str, Any]:
    fusion = Path(fusion)
    manifest = fault_injection_manifest()
    contracts = build_contract_bundle(fusion)
    ledger_path = fusion / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json"
    ledger_before = ledger_path.read_bytes()
    with ProcessPoolExecutor(max_workers=8) as executor:
        executed = executor.map(run_scenario, repeat(fusion), SCENARIOS)
        scenario_results = {spec.name: result for spec, result in zip(SCENARIOS, executed, strict=True)}
    validators = _validator_scenarios(fusion)
    ledger_after = ledger_path.read_bytes()

    clean = [scenario_results[name] for name in ("clean_baseline_seed_6201", "clean_baseline_seed_6223")]
    low_motion = scenario_results["clean_low_motion_jitter"]
    outlier = scenario_results["uwb_single_large_outlier"]
    anchor = scenario_results["uwb_single_anchor_fault"]
    tag = scenario_results["uwb_single_tag_fault"]
    dropout = scenario_results["imu_single_node_dropout"]
    outage = scenario_results["uwb_full_outage_recovery"]
    slip = scenario_results["model_rotational_skin_slip"]
    wrong_lever = scenario_results["model_wrong_synthetic_lever"]
    recovery = outage
    checkpoint = json.loads(
        (fusion / "logs/root_r6a1c_checkpoint_20260825T110857Z/CHECKPOINT_ATTESTATION.json").read_text()
    )
    checkpoint_tests = json.loads(
        (fusion / "logs/root_r6a1c_checkpoint_20260825T110857Z/PREDECESSOR_TEST_RESULTS.json").read_text()
    )
    registry = contracts["registry"]
    isolation = contracts["isolation"]
    all_metrics = [result["metrics"] for result in scenario_results.values()]
    gates: dict[str, Any] = {}
    _gate(gates, "A", "r6a1c_checkpoint_verified", checkpoint["pass"] and checkpoint["root_r6a2a_gate_authorized"], commit=checkpoint["checkpoint_commit"])
    _gate(gates, "B", "sealed_parent_and_addendum_exact", checkpoint["sealed_parent_r6a1c_byte_exact"] and checkpoint["bsf31cc_addendum_byte_exact"])
    _gate(gates, "C", "one_suite_two_family_registry", set(registry["families"]) == {FAMILY_COMMON_NINE, FAMILY_BSF31CC} and not contracts["architecture"]["separate_family_estimators"])
    _gate(gates, "D", "ten_nodes_covered_exactly_once", set(registry["family_by_node"]) == set(ALL_NODES) and len(registry["family_by_node"]) == 10)
    _gate(gates, "E", "no_cross_family_transform_reuse", registry["cross_family_mechanical_transform_reuse"] == "FORBIDDEN" and registry["family_by_node"]["BSF31CC"] == FAMILY_BSF31CC and all(registry["family_by_node"][node] == FAMILY_COMMON_NINE for node in COMMON_NINE))
    _gate(gates, "F", "synthetic_real_registry_isolation", ledger_before == ledger_after and isolation["real_registry"]["writes_performed"] == 0)
    _gate(gates, "G", "production_native_time_preintegrator_exercised", all(result["state_execution_count"] > 0 and result["metrics"]["preintegration_covariance_consumed_count"] > 0 for result in clean), implementation="NativeTimePreintegrator.integrate_async")
    _gate(gates, "H", "native_variable_dt_used", all(result["metrics"]["native_variable_dt_exercised"] and result["metrics"]["native_start_time_unique_count"] == 10 for result in clean))
    _gate(gates, "I", "shared_fk_only_geometry_path", contracts["architecture"]["shared_fk_only"] and all(result["metrics"]["joint_closure_max_m"] <= FROZEN_THRESHOLDS["joint_closure_max_m"] for result in scenario_results.values()), implementation="BodyModel.all_predictions/tag_phase_centres")
    _gate(gates, "J", "fixed_bone_length_invariance", all(result["metrics"]["bone_length_max_change_m"] <= FROZEN_THRESHOLDS["bone_length_change_max_m"] for result in scenario_results.values()), maximum=max(result["metrics"]["bone_length_max_change_m"] for result in scenario_results.values()))
    _gate(gates, "K", "preintegration_covariance_and_bias_jacobians_consumed", all(result["metrics"]["preintegration_covariance_consumed_count"] >= FROZEN_THRESHOLDS["minimum_preintegration_covariance_consumed"] and result["metrics"]["preintegration_bias_jacobian_consumed_count"] >= FROZEN_THRESHOLDS["minimum_preintegration_bias_jacobian_consumed"] and result["metrics"]["preintegration_bias_jacobian_norm_min"] > 0.0 for result in clean))
    _gate(gates, "L", "clean_asynchronous_baseline", all(result["metrics"]["root_position_rmse_m"] <= FROZEN_THRESHOLDS["clean_root_position_rmse_max_m"] and result["metrics"]["root_orientation_rmse_rad"] <= FROZEN_THRESHOLDS["clean_root_orientation_rmse_max_rad"] and result["metrics"]["relative_joint_orientation_rmse_rad"] <= FROZEN_THRESHOLDS["clean_joint_orientation_rmse_max_rad"] and result["metrics"]["velocity_rmse_mps"] <= FROZEN_THRESHOLDS["clean_velocity_rmse_max_mps"] and result["metrics"]["false_positive_count"] == 0 for result in clean))
    _gate(gates, "M", "low_motion_uwb_jitter_no_artificial_motion", low_motion["metrics"]["root_position_max_error_m"] <= FROZEN_THRESHOLDS["low_motion_root_error_max_m"] and any(row["uwb"].get("low_motion_jitter_hold", False) for row in low_motion["step_audit"]) and not low_motion["metrics"]["trust_depends_on_acceleration"])
    _gate(gates, "N", "coherent_uwb_motion_corrects_imu_drift", all(result["metrics"]["root_position_final_error_m"] < result["metrics"]["root_position_initial_error_m"] for result in clean), initial=[r["metrics"]["root_position_initial_error_m"] for r in clean], final=[r["metrics"]["root_position_final_error_m"] for r in clean])
    _gate(gates, "O", "individual_uwb_outlier_rejected", outlier["metrics"]["fault_attribution"] == "SINGLE_UWB_LINK_OR_NLOS" and outlier["metrics"]["maximum_uwb_correction_m"] <= FROZEN_THRESHOLDS["reentry_correction_max_m"])
    _gate(gates, "P", "bad_anchor_attributed_across_tags", anchor["metrics"]["fault_attribution"] == "BAD_ANCHOR" and any(row["scope"] == "anchor" and row["entity_id"] == str(SCENARIOS[17].target_anchor) for row in anchor["health_transitions"]))
    _gate(gates, "Q", "bad_tag_attributed_across_anchors", tag["metrics"]["fault_attribution"] == "BAD_TAG_OR_NODE_UWB" and any(row["scope"] == "tag" and row["entity_id"] == SCENARIOS[18].target_tag for row in tag["health_transitions"]))
    _gate(gates, "R", "imu_dropout_bounded_degradation_covariance_growth", dropout["metrics"]["fault_attribution"] == "BAD_IMU_OR_TIME_STREAM" and dropout["metrics"]["covariance_finite"] and dropout["metrics"]["total_covariance_max_step_growth"] > 0.0)
    _gate(gates, "S", "global_uwb_outage_local_motion_absolute_uncertainty", outage["metrics"]["local_articulated_motion_continued"] and outage["metrics"]["root_position_covariance_max"] > outage["metrics"]["root_position_covariance_initial"] and outage["metrics"]["global_yaw_variance_final"] > outage["metrics"]["global_yaw_variance_initial"] and not outage["metrics"]["absolute_no_drift_position_claimed"] and "GLOBAL_UWB_OUTAGE" in outage["metrics"]["mode_sequence"])
    _gate(gates, "T", "skin_slip_cannot_change_bone_length", slip["metrics"]["bone_length_max_change_m"] <= FROZEN_THRESHOLDS["bone_length_change_max_m"] and slip["metrics"]["fault_attribution"] == "AMBIGUOUS_MULTI_CAUSE")
    _gate(gates, "U", "unidentifiable_faults_remain_ambiguous", wrong_lever["metrics"]["fault_attribution"] == "AMBIGUOUS_MULTI_CAUSE" and slip["metrics"]["fault_attribution"] == "AMBIGUOUS_MULTI_CAUSE")
    _gate(gates, "V", "controlled_reentry", recovery["metrics"]["recovery_transition_count"] >= 3 and "CONTROLLED_REENTRY" in recovery["metrics"]["mode_sequence"] and recovery["metrics"]["maximum_uwb_correction_m"] <= FROZEN_THRESHOLDS["reentry_correction_max_m"] and recovery["metrics"]["reentry_max_state_step_m"] <= FROZEN_THRESHOLDS["reentry_state_step_max_m"])
    _gate(gates, "W", "obsolete_wrist_map_fails_closed", not validators["obsolete_wrist_map_attempt"]["authorized"] and "OBSOLETE_WRIST_MAP" in validators["obsolete_wrist_map_attempt"]["blockers"] and validators["obsolete_wrist_map_attempt"]["state_execution_count"] == 0)
    _gate(gates, "X", "wrong_hardware_family_fails_closed", validators["wrong_hardware_family_attempt"]["failed_closed"] and validators["wrong_hardware_family_attempt"]["state_execution_count"] == 0)
    _gate(gates, "Y", "missing_real_prerequisites_fail_before_execution", not validators["missing_real_calibration_real_mode_attempt"]["authorized"] and validators["missing_real_calibration_real_mode_attempt"]["state_execution_count"] == 0)
    _gate(gates, "Z", "real_registry_87_null_frozen", isolation["real_registry"]["total"] == 87 and isolation["real_registry"]["value_null"] == 87 and isolation["real_registry"]["FROZEN_UNCERTAIN"] == 87 and ledger_before == ledger_after)
    _gate(gates, "AA", "predecessor_regressions_green", checkpoint_tests["all_pass"] and checkpoint_tests["expected_test_count"] == 103, checkpoint_commit=checkpoint["checkpoint_commit"])
    replay = run_scenario(fusion, SCENARIOS[0])
    _gate(gates, "AB", "deterministic_replay_and_checksum_contract", replay["deterministic_replay_sha256"] == scenario_results[SCENARIOS[0].name]["deterministic_replay_sha256"], replay_sha256=replay["deterministic_replay_sha256"], final_checksum_verification="REQUIRED_BY_SEPARATE_VERIFIER")

    covariance = {
        "schema": "biospur-root-r6a2a-covariance-qualification-v1",
        "scenario_count": len(scenario_results),
        "all_finite": all(row["covariance_finite"] for row in all_metrics),
        "maximum_symmetry_error": max(row["covariance_symmetry_max_abs"] for row in all_metrics),
        "minimum_eigenvalue": min(row["covariance_min_eigenvalue"] for row in all_metrics),
        "outage_root_covariance_growth": outage["metrics"]["root_position_covariance_max"] - outage["metrics"]["root_position_covariance_initial"],
        "outage_yaw_covariance_growth": outage["metrics"]["global_yaw_variance_final"] - outage["metrics"]["global_yaw_variance_initial"],
        "pass": all(row["covariance_finite"] for row in all_metrics) and max(row["covariance_symmetry_max_abs"] for row in all_metrics) <= FROZEN_THRESHOLDS["covariance_symmetry_max_abs"] and min(row["covariance_min_eigenvalue"] for row in all_metrics) >= FROZEN_THRESHOLDS["covariance_min_eigenvalue"],
    }
    order = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["AA", "AB"]
    return {
        "schema": "biospur-root-r6a2a-qualification-v1",
        "manifest": manifest,
        "contracts": contracts,
        "scenario_results": scenario_results,
        "validator_results": validators,
        "covariance": covariance,
        "gates": {
            "order": order,
            "results": gates,
            "passed": sum(gates[key]["pass"] for key in order),
            "total": len(order),
            "all_pass": len(gates) == len(order) and all(gates[key]["pass"] for key in order) and covariance["pass"],
        },
        "real_ledger_before_sha256": hashlib.sha256(ledger_before).hexdigest(),
        "real_ledger_after_sha256": hashlib.sha256(ledger_after).hexdigest(),
    }
