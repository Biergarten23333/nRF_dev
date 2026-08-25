"""Development, frozen validation, Monte Carlo, and gate logic for R6A2A-R2."""
from __future__ import annotations

from dataclasses import asdict, replace
import csv
import hashlib
import inspect
import io
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from scipy.stats import chi2

from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a2a.contracts import ALL_NODES, COMMON_NINE, FAMILY_BSF31CC, FAMILY_COMMON_NINE
from biospur_fusion.root_r6a2a.shadow import corrected_body_model

from . import estimator as estimator_module
from .contracts import (
    DegradedMode,
    EstimatorOptions,
    EvaluationResult,
    FaultInjectionTruth,
    FaultWindow,
    ObservationStatus,
    ScenarioDefinition,
    StateLayout,
)
from .estimator import RepairedShadowEstimator, all_node_covariance_mapping, build_estimator
from .synthetic import GeneratedRun, IndependentRng, generate_run, input_digest, serialize_input


THRESHOLDS = {
    "schema": "biospur-root-r6a2a-r2-frozen-thresholds-v1",
    "development_origin": "R1 audit findings plus R6A2A predecessor envelopes",
    "root_position_rmse_max_m": 0.22,
    "root_orientation_rmse_max_rad": 0.25,
    "joint_orientation_rmse_max_rad": 0.30,
    "velocity_rmse_max_mps": 0.90,
    "detection_latency_max_s": 0.35,
    "covariance_symmetry_max_abs": 1e-9,
    "covariance_min_eigenvalue": -1e-10,
    "joint_closure_max_m": 1e-9,
    "reentry_peak_step_max_m": 0.20,
    "bias_estimate_min_abs_rad_s": 0.010,
    "bias_enabled_improvement_min": 0.01,
    "ablation_digest_must_differ": True,
    "weak_direction_variance_ratio_min": 1.05,
    "monte_carlo_runs_per_headline_class": 100,
    "normalized_nees_broad_lower": 0.05,
    "normalized_nees_broad_upper": 20.0,
    "coverage_1sigma_bounds": [0.40, 0.90],
    "coverage_2sigma_bounds": [0.80, 1.0],
    "coverage_3sigma_bounds": [0.94, 1.0],
}

SYNTHETIC_NOISE = {
    "imu_accel_white_noise_density_mps2_sqrt_hz": 0.020,
    "imu_gyro_white_noise_density_rad_s_sqrt_hz": 0.002,
    "accel_bias_random_walk_mps2_s_sqrt_s": 0.0002,
    "gyro_bias_random_walk_rad_s2_sqrt_s": 0.00002,
    "uwb_sigma_m": 0.035,
    "provenance": "ROOT_R6A2A_R2_SYNTHETIC_TEST_ONLY",
    "production_authority": False,
}


def _truth(
    kind: str = "none", *, node: str = "BSFEC35", tag: str = "BSFEC35",
    anchor: int = 2, start: int = 3, end: int = 7, magnitude: float = 1.0,
    scope: str = "none", attributions: Sequence[str] = ("NO_FAULT",),
    modes: Sequence[str] = ("NORMAL",), persistent: bool = False,
) -> FaultInjectionTruth:
    return FaultInjectionTruth(
        kind, node, tag, anchor, FaultWindow(start, end, persistent), magnitude,
        scope, tuple(attributions), tuple(modes),
        ("REAL_BODY_UPDATE", "UNIQUE_CAUSE_WITHOUT_EVIDENCE", "DIRECT_STATE_OVERWRITE"),
    )


def _scenario(
    name: str, category: str, truth: FaultInjectionTruth, *, seed: int = 6201,
    duration: float = 1.2, step: float = 0.1, geometry: str = "WELL_CONDITIONED_3D",
    low_motion: bool = False, variant: int = 0,
) -> ScenarioDefinition:
    return ScenarioDefinition(
        name, category, seed, duration, step, geometry, low_motion, truth,
        (truth.expected_scope, "expected-versus-received accounting", "normalized innovation"),
        "predeclared R2 gate metric", variant,
    )


DEVELOPMENT_SCENARIOS = (
    _scenario("clean_baseline_seed_6201", "clean", _truth(), seed=6201),
    _scenario("clean_baseline_seed_6223", "clean", _truth(), seed=6223, variant=1),
    _scenario("clean_low_motion_jitter", "clean", _truth(), seed=6247, low_motion=True, variant=2),
    _scenario("imu_bounded_sample_gap", "imu", _truth("bounded_sample_gap", end=3, scope="imu_stream", attributions=("NO_FAULT", "BAD_IMU_OR_TIME_STREAM"), modes=("NORMAL",))),
    _scenario("imu_long_gap", "imu", _truth("long_gap", scope="imu_stream", attributions=("BAD_IMU_OR_TIME_STREAM",), modes=("SINGLE_IMU_DEGRADED",))),
    _scenario("imu_duplicate_timestamp", "imu", _truth("duplicate_timestamp", scope="imu_stream", attributions=("BAD_IMU_OR_TIME_STREAM",), modes=("SINGLE_IMU_DEGRADED",))),
    _scenario("imu_timestamp_reversal", "imu", _truth("timestamp_reversal", scope="imu_stream", attributions=("BAD_IMU_OR_TIME_STREAM",), modes=("SINGLE_IMU_DEGRADED",))),
    _scenario("imu_boot_epoch_reset", "imu", _truth("boot_epoch_reset", scope="clock_or_imu_stream", attributions=("BAD_IMU_OR_TIME_STREAM",), modes=("SINGLE_IMU_DEGRADED",))),
    _scenario("imu_saturation", "imu", _truth("imu_saturation", scope="imu_stream", attributions=("BAD_IMU_OR_TIME_STREAM",), modes=("SINGLE_IMU_DEGRADED",))),
    _scenario("imu_invalid_value", "imu", _truth("invalid_imu_value", scope="imu_stream", attributions=("BAD_IMU_OR_TIME_STREAM",), modes=("SINGLE_IMU_DEGRADED",))),
    _scenario("imu_noise_burst", "imu", _truth("imu_noise_burst", scope="imu_stream", attributions=("BAD_IMU_OR_TIME_STREAM", "AMBIGUOUS_MULTI_CAUSE"), modes=("SINGLE_IMU_DEGRADED",), magnitude=1.0)),
    _scenario("imu_gyro_bias_step", "imu", _truth("gyro_bias_step", scope="imu_bias_or_motion", attributions=("BAD_IMU_OR_TIME_STREAM", "NO_FAULT"), modes=("SUSPECT", "SINGLE_IMU_DEGRADED"), magnitude=0.35)),
    _scenario("imu_gyro_bias_ramp", "imu", _truth("gyro_bias_ramp", scope="imu_bias_or_motion", attributions=("BAD_IMU_OR_TIME_STREAM",), modes=("SINGLE_IMU_DEGRADED",), magnitude=0.10)),
    _scenario("imu_single_node_dropout", "imu", _truth("single_node_dropout", scope="imu_stream", attributions=("BAD_IMU_OR_TIME_STREAM",), modes=("SINGLE_IMU_DEGRADED",))),
    _scenario("uwb_single_large_outlier", "uwb", _truth("single_uwb_outlier", end=3, scope="uwb_link", attributions=("SINGLE_UWB_LINK_OR_NLOS", "NO_FAULT"), modes=("SUSPECT",))),
    _scenario("uwb_positive_nlos_bias_burst", "uwb", _truth("nlos_bias_burst", scope="uwb_link", attributions=("SINGLE_UWB_LINK_OR_NLOS",), modes=("SINGLE_UWB_LINK_DEGRADED",))),
    _scenario("uwb_persistent_bad_link", "uwb", _truth("persistent_bad_link", end=11, scope="uwb_link", attributions=("SINGLE_UWB_LINK_OR_NLOS",), modes=("SINGLE_UWB_LINK_DEGRADED",))),
    _scenario("uwb_single_anchor_fault", "uwb", _truth("single_anchor_fault", scope="anchor", attributions=("BAD_ANCHOR",), modes=("SINGLE_ANCHOR_ISOLATED",))),
    _scenario("uwb_single_tag_fault", "uwb", _truth("single_tag_fault", scope="tag", attributions=("BAD_TAG_OR_NODE_UWB",), modes=("SINGLE_TAG_UWB_DEGRADED",))),
    _scenario("uwb_tag_dropout", "uwb", _truth("tag_dropout", scope="tag_missingness", attributions=("BAD_TAG_OR_NODE_UWB",), modes=("SINGLE_TAG_UWB_DEGRADED",))),
    _scenario("uwb_multi_anchor_outage", "uwb", _truth("multi_anchor_outage", scope="anchor_geometry", attributions=("GLOBAL_GEOMETRY_OR_OBSERVABILITY_LOSS",), modes=("MULTI_ANCHOR_GEOMETRY_DEGRADED",))),
    _scenario("uwb_low_vertical_diversity", "uwb", _truth("low_vertical_geometry", start=0, end=11, persistent=True, scope="weak_geometry", attributions=("GLOBAL_GEOMETRY_OR_OBSERVABILITY_LOSS",), modes=("MULTI_ANCHOR_GEOMETRY_DEGRADED",)), geometry="LOW_VERTICAL_DIVERSITY"),
    _scenario("uwb_full_outage_recovery", "uwb", _truth("global_uwb_outage", end=7, scope="global_uwb", attributions=("GLOBAL_GEOMETRY_OR_OBSERVABILITY_LOSS",), modes=("GLOBAL_UWB_OUTAGE", "RECOVERING", "REQUALIFYING", "CONTROLLED_REENTRY")), duration=1.8),
    _scenario("model_wrong_synthetic_lever", "model", _truth("wrong_synthetic_lever", start=0, end=11, persistent=True, scope="model_or_slip", attributions=("AMBIGUOUS_MULTI_CAUSE",), modes=("AMBIGUOUS_MODEL_OR_SLIP_MISMATCH",))),
    _scenario("model_rotational_skin_slip", "model", _truth("rotational_skin_slip", scope="model_or_slip", attributions=("AMBIGUOUS_MULTI_CAUSE", "BAD_IMU_OR_TIME_STREAM"), modes=("AMBIGUOUS_MODEL_OR_SLIP_MISMATCH", "SINGLE_IMU_DEGRADED"), magnitude=0.10)),
    _scenario("model_transient_wrist_ghost", "model", _truth("wrist_ghost_rotation", end=4, scope="imu_or_slip", attributions=("BAD_IMU_OR_TIME_STREAM",), modes=("SUSPECT", "SINGLE_IMU_DEGRADED", "RECOVERING", "REQUALIFYING", "CONTROLLED_REENTRY"), magnitude=0.35), duration=1.7),
    _scenario("model_wrong_bone_geometry", "model", _truth("wrong_bone_geometry", node="BSFAA61", tag="BSFAA61", start=0, end=11, persistent=True, scope="model_or_slip", attributions=("AMBIGUOUS_MULTI_CAUSE",), modes=("AMBIGUOUS_MODEL_OR_SLIP_MISMATCH",))),
    _scenario("model_persistent_post_motion_offset", "model", _truth("persistent_post_motion_offset", end=11, scope="model_or_slip", attributions=("AMBIGUOUS_MULTI_CAUSE", "BAD_TAG_OR_NODE_UWB"), modes=("AMBIGUOUS_MODEL_OR_SLIP_MISMATCH", "SINGLE_TAG_UWB_DEGRADED"))),
    _scenario("node_imu_and_uwb_dropout", "combined", _truth("node_imu_and_uwb_dropout", scope="combined_node", attributions=("BAD_IMU_AND_NODE_UWB_SCOPE",), modes=("SINGLE_NODE_IMU_AND_UWB_DEGRADED",))),
    _scenario("bias_observable_long_gyro_step", "bias", _truth("observable_gyro_bias_step", start=8, end=31, scope="gyro_bias", attributions=("NO_FAULT", "BAD_IMU_OR_TIME_STREAM", "AMBIGUOUS_MULTI_CAUSE"), modes=("NORMAL",), magnitude=0.045), seed=6301, duration=4.0, low_motion=True, variant=3),
    _scenario("recovery_long_controlled_return", "recovery", _truth("global_uwb_outage", start=4, end=11, scope="global_uwb", attributions=("GLOBAL_GEOMETRY_OR_OBSERVABILITY_LOSS",), modes=("GLOBAL_UWB_OUTAGE", "RECOVERING", "REQUALIFYING", "CONTROLLED_REENTRY")), seed=6323, duration=2.4, variant=4),
)


VALIDATION_SCENARIOS = (
    _scenario("val_clean_81001", "clean", _truth(), seed=81001, duration=1.4, variant=11),
    _scenario("val_clean_low_motion_81019", "clean", _truth(), seed=81019, duration=1.4, low_motion=True, variant=12),
    _scenario("val_missing_multi_anchor_81031", "uwb", _truth("multi_anchor_outage", start=2, end=6, scope="anchor_geometry", attributions=("GLOBAL_GEOMETRY_OR_OBSERVABILITY_LOSS",), modes=("MULTI_ANCHOR_GEOMETRY_DEGRADED",)), seed=81031, duration=1.5, variant=13),
    _scenario("val_tag_dropout_81043", "uwb", _truth("tag_dropout", start=2, end=6, scope="tag_missingness", attributions=("BAD_TAG_OR_NODE_UWB",), modes=("SINGLE_TAG_UWB_DEGRADED",)), seed=81043, duration=1.5, variant=14),
    _scenario("val_combined_dropout_81047", "combined", _truth("node_imu_and_uwb_dropout", start=2, end=6, scope="combined_node", attributions=("BAD_IMU_AND_NODE_UWB_SCOPE",), modes=("SINGLE_NODE_IMU_AND_UWB_DEGRADED",)), seed=81047, duration=1.5, variant=15),
    _scenario("val_wrist_ghost_81059", "model", _truth("wrist_ghost_rotation", start=2, end=3, scope="imu_or_slip", attributions=("BAD_IMU_OR_TIME_STREAM",), modes=("SUSPECT", "SINGLE_IMU_DEGRADED", "RECOVERING", "REQUALIFYING", "CONTROLLED_REENTRY"), magnitude=0.32), seed=81059, duration=1.6, variant=16),
    _scenario("val_persistent_lever_81071", "model", _truth("wrong_synthetic_lever", start=0, end=15, persistent=True, scope="model_or_slip", attributions=("AMBIGUOUS_MULTI_CAUSE",), modes=("AMBIGUOUS_MODEL_OR_SLIP_MISMATCH",)), seed=81071, duration=1.6, variant=17),
    _scenario("val_low_vertical_81077", "uwb", _truth("low_vertical_geometry", start=0, end=15, persistent=True, scope="weak_geometry", attributions=("GLOBAL_GEOMETRY_OR_OBSERVABILITY_LOSS",), modes=("MULTI_ANCHOR_GEOMETRY_DEGRADED",)), seed=81077, duration=1.6, geometry="LOW_VERTICAL_DIVERSITY", variant=18),
    _scenario("val_bias_long_81083", "bias", _truth("observable_gyro_bias_step", start=6, end=25, scope="gyro_bias", attributions=("NO_FAULT", "BAD_IMU_OR_TIME_STREAM", "AMBIGUOUS_MULTI_CAUSE"), modes=("NORMAL",), magnitude=0.040), seed=81083, duration=3.4, low_motion=True, variant=19),
    _scenario("val_outage_recovery_long_81097", "recovery", _truth("global_uwb_outage", start=3, end=10, scope="global_uwb", attributions=("GLOBAL_GEOMETRY_OR_OBSERVABILITY_LOSS",), modes=("GLOBAL_UWB_OUTAGE", "RECOVERING", "REQUALIFYING", "CONTROLLED_REENTRY")), seed=81097, duration=2.3, variant=20),
    _scenario("val_anchor_fault_81101", "uwb", _truth("single_anchor_fault", start=2, end=7, scope="anchor", attributions=("BAD_ANCHOR",), modes=("SINGLE_ANCHOR_ISOLATED",), magnitude=0.85), seed=81101, duration=1.6, variant=21),
    _scenario("val_nlos_link_81119", "uwb", _truth("nlos_bias_burst", start=2, end=7, scope="uwb_link", attributions=("SINGLE_UWB_LINK_OR_NLOS",), modes=("SINGLE_UWB_LINK_DEGRADED",), magnitude=0.9), seed=81119, duration=1.6, variant=22),
)


def _rotation_error(estimate: np.ndarray, truth: np.ndarray) -> float:
    return float(np.linalg.norm(so3_log(so3_exp(truth).T @ so3_exp(estimate))))


def tangent_error(state: Any, truth: Any, layout: StateLayout, injected_bias: np.ndarray | None = None, target_node: str | None = None) -> np.ndarray:
    rows = [
        state.root_translation_model_m - truth.root_translation_model_m,
        so3_log(so3_exp(truth.root_rotation_model_rotvec).T @ so3_exp(state.root_rotation_model_rotvec)),
        state.root_velocity_model_mps - truth.root_velocity_model_mps,
    ]
    rows.extend(
        so3_log(so3_exp(truth.joint_rotvec[joint]).T @ so3_exp(state.joint_rotvec[joint]))
        for joint in layout.joint_ids
    )
    rows.extend(state.joint_rate_rad_s[joint] - truth.joint_rate_rad_s[joint] for joint in layout.joint_ids)
    for node in layout.node_ids:
        reference = np.asarray(truth.gyro_bias_rad_s[node], float).copy()
        if injected_bias is not None and node == target_node:
            reference += injected_bias
        rows.append(state.gyro_bias_rad_s[node] - reference)
    rows.extend(state.accel_bias_mps2[node] - truth.accel_bias_mps2[node] for node in layout.node_ids)
    return np.concatenate(rows)


def _canonical_output(estimator: RepairedShadowEstimator) -> dict[str, Any]:
    return {
        "digest": estimator.digest(),
        "modes": list(estimator.mode_history),
        "attributions": [row.attribution for row in estimator.outputs],
        "health": [row.health_snapshot for row in estimator.outputs],
        "covariance_traces": estimator.covariance_traces,
        "root_positions": [state.root_translation_model_m.tolist() for state in estimator.states],
    }


def execute_generated(fusion: Path, generated: GeneratedRun) -> tuple[RepairedShadowEstimator, dict[str, Any]]:
    estimator, initialization = build_estimator(fusion, generated.truth_states[0])
    for value in generated.inputs:
        estimator.step(value)
    return estimator, initialization


def run_scenario(
    fusion: Path,
    scenario: ScenarioDefinition,
    options: EstimatorOptions = EstimatorOptions(),
) -> dict[str, Any]:
    generated = generate_run(fusion, scenario, options)
    estimator, initialization = execute_generated(fusion, generated)
    layout = estimator.layout
    position = [
        float(np.linalg.norm(state.root_translation_model_m - truth.root_translation_model_m))
        for state, truth in zip(estimator.states, generated.truth_states, strict=True)
    ]
    orientation = [
        _rotation_error(state.root_rotation_model_rotvec, truth.root_rotation_model_rotvec)
        for state, truth in zip(estimator.states, generated.truth_states, strict=True)
    ]
    joint = [
        float(np.mean([
            _rotation_error(state.joint_rotvec[name], truth.joint_rotvec[name])
            for name in layout.joint_ids
        ]))
        for state, truth in zip(estimator.states, generated.truth_states, strict=True)
    ]
    velocity = [
        float(np.linalg.norm(state.root_velocity_model_mps - truth.root_velocity_model_mps))
        for state, truth in zip(estimator.states, generated.truth_states, strict=True)
    ]
    outputs = estimator.outputs
    attributions = [output.attribution for output in outputs]
    modes = estimator.mode_history
    private = scenario.private_truth
    attribution_allowed = any(value in private.allowed_attributions for value in attributions)
    nonnormal_expected = [value for value in private.expected_modes if value != "NORMAL"]
    expected_modes_seen = True if not nonnormal_expected else any(value in modes for value in nonnormal_expected)
    first_non_normal = next((i for i, mode in enumerate(modes[1:]) if mode != "NORMAL"), None)
    start_s = private.window.start_step * scenario.step_s
    detection_latency = None if first_non_normal is None else max(0.0, (first_non_normal + 1) * scenario.step_s - start_s)
    accounting = [row for output in outputs for row in output.accounting]
    expected = sum(row.status is not ObservationStatus.NOT_SCHEDULED for row in accounting)
    terminal = len(accounting)
    target_axis = np.array([private.magnitude, 0.0, 0.0])
    evaluation_step = min(private.window.end_step, len(outputs) - 1)
    bias_estimate = outputs[evaluation_step].state.gyro_bias_rad_s[private.target_node]
    baseline_truth = generated.truth_states[evaluation_step + 1].gyro_bias_rad_s[private.target_node]
    evaluated_truth = baseline_truth + target_axis
    bias_error = float(np.linalg.norm(bias_estimate - evaluated_truth)) if private.kind == "observable_gyro_bias_step" else None
    covariance_finite = all(np.isfinite(state.covariance).all() for state in estimator.states)
    symmetry = max(float(np.max(np.abs(state.covariance - state.covariance.T))) for state in estimator.states)
    minimum_eigenvalue = min(float(np.min(np.linalg.eigvalsh(state.covariance))) for state in estimator.states)
    jumps = [
        float(np.linalg.norm(second.root_translation_model_m - first.root_translation_model_m))
        for first, second in zip(estimator.states[:-1], estimator.states[1:], strict=True)
    ]
    window_start = max(0, min(private.window.start_step, len(estimator.root_covariance_traces) - 1))
    window_end = max(window_start + 1, min(private.window.end_step + 2, len(estimator.root_covariance_traces)))
    outage_root_growth = max(estimator.root_covariance_traces[window_start:window_end]) - estimator.root_covariance_traces[window_start]
    outage_yaw_growth = max(estimator.yaw_variances[window_start:window_end]) - estimator.yaw_variances[window_start]
    return_start = private.window.end_step + 1
    early_return_corrections = [
        row["correction_norm_m"] for row in estimator.information_rows
        if return_start <= row["step"] <= return_start + 3
    ]
    result = {
        "schema": "biospur-root-r6a2a-r2-scenario-result-v1",
        "scenario": asdict(scenario),
        "input_digest": input_digest(generated.inputs),
        "output_digest": estimator.digest(),
        "source_digest": generated.source_digest,
        "fault_window_audit": generated.fault_window_audit,
        "rng_lineage": generated.rng_lineage,
        "evaluation": asdict(EvaluationResult(
            scenario.scenario_id, estimator.digest(), attribution_allowed,
            expected_modes_seen, {"allowed_attributions": list(private.allowed_attributions)},
        )),
        "metrics": {
            "root_position_rmse_m": float(np.sqrt(np.mean(np.square(position)))),
            "root_position_initial_error_m": position[0], "root_position_final_error_m": position[-1],
            "root_orientation_rmse_rad": float(np.sqrt(np.mean(np.square(orientation)))),
            "joint_orientation_rmse_rad": float(np.sqrt(np.mean(np.square(joint)))),
            "velocity_rmse_mps": float(np.sqrt(np.mean(np.square(velocity)))),
            "covariance_finite": covariance_finite, "covariance_symmetry_max_abs": symmetry,
            "covariance_min_eigenvalue": minimum_eigenvalue,
            "root_covariance_initial": estimator.root_covariance_traces[0],
            "root_covariance_final": estimator.root_covariance_traces[-1],
            "root_covariance_max": max(estimator.root_covariance_traces),
            "yaw_variance_initial": estimator.yaw_variances[0],
            "yaw_variance_final": estimator.yaw_variances[-1],
            "yaw_variance_max": max(estimator.yaw_variances),
            "outage_window_root_covariance_growth": outage_root_growth,
            "outage_window_yaw_variance_growth": outage_yaw_growth,
            "reentry_early_peak_correction_m": max(early_return_corrections, default=0.0),
            "maximum_root_step_m": max(jumps, default=0.0),
            "maximum_uwb_correction_m": max(estimator.correction_norms, default=0.0),
            "accounted_expected_count": expected, "terminal_accounting_count": terminal,
            "missing_count": sum(row.status is ObservationStatus.EXPECTED_BUT_MISSING for row in accounting),
            "rejected_count": sum(row.status is ObservationStatus.RECEIVED_REJECTED for row in accounting),
            "detection_latency_s": detection_latency,
            "attribution_allowed": attribution_allowed, "expected_modes_seen": expected_modes_seen,
            "attribution_sequence": attributions, "mode_sequence": modes,
            "bias_estimate_at_evaluation_rad_s": bias_estimate.tolist(),
            "bias_error_at_evaluation_rad_s": bias_error,
            "bias_total_update_count": int(sum(
                np.sum(estimator.bias_update_count[node][kind])
                for node in layout.node_ids for kind in ("gyro", "accel")
            )),
            "all_node_covariance_mapping_counts": dict(estimator.covariance_mapping_counts),
            "joint_closure_max_m": max(output.residual_evidence["shared_fk_joint_closure_max_m"] for output in outputs),
            "weak_directional_inflation_max_m2": max(output.residual_evidence["uwb"].get("directional_inflation_m2", 0.0) for output in outputs),
            "explicit_weak_directional_inflation_max_m2": max(output.residual_evidence["uwb"].get("explicit_directional_inflation_m2", 0.0) for output in outputs),
            "full_3d_observability_ever_disabled": any(not output.residual_evidence["uwb"].get("full_3d_absolute_observability", False) for output in outputs),
        },
        "states": [{
            "time_s": state.time_s,
            "root_position_m": state.root_translation_model_m.tolist(),
            "root_orientation_rotvec": state.root_rotation_model_rotvec.tolist(),
            "root_velocity_mps": state.root_velocity_model_mps.tolist(),
            "covariance_diagonal": np.diag(state.covariance).tolist(),
        } for state in estimator.states],
        "health_transitions": list(estimator.health.transitions()),
        "accounting": [asdict(row) for row in accounting],
        "bias_evidence": outputs[-1].bias_evidence,
        "bias_evaluation": {
            node: {
                "gyro": [
                    {
                        "component": axis,
                        "observability": "LOCALLY_OBSERVABLE" if outputs[-1].bias_evidence[node]["gyro_update_count"][axis] > 0 else "UNOBSERVABLE_UNDER_SCENARIO",
                        "estimated_rad_s": float(outputs[evaluation_step].state.gyro_bias_rad_s[node][axis]),
                        "truth_rad_s": float(generated.truth_states[evaluation_step + 1].gyro_bias_rad_s[node][axis] + (target_axis[axis] if private.kind == "observable_gyro_bias_step" and node == private.target_node else 0.0)),
                        "error_rad_s": float(outputs[evaluation_step].state.gyro_bias_rad_s[node][axis] - generated.truth_states[evaluation_step + 1].gyro_bias_rad_s[node][axis] - (target_axis[axis] if private.kind == "observable_gyro_bias_step" and node == private.target_node else 0.0)),
                        "variance_rad2_s2": float(outputs[evaluation_step].state.covariance[layout.gyro_bias(node).start + axis, layout.gyro_bias(node).start + axis]),
                        "update_count": int(outputs[-1].bias_evidence[node]["gyro_update_count"][axis]),
                        "maximum_correction_rad_s": float(outputs[-1].bias_evidence[node]["gyro_max_correction_rad_s"][axis]),
                    } for axis in range(3)
                ],
                "accelerometer": [
                    {
                        "component": axis,
                        "observability": "LOCALLY_OBSERVABLE" if outputs[-1].bias_evidence[node]["accelerometer_update_count"][axis] > 0 else "UNOBSERVABLE_UNDER_SCENARIO",
                        "estimated_mps2": float(outputs[evaluation_step].state.accel_bias_mps2[node][axis]),
                        "truth_mps2": float(generated.truth_states[evaluation_step + 1].accel_bias_mps2[node][axis]),
                        "error_mps2": float(outputs[evaluation_step].state.accel_bias_mps2[node][axis] - generated.truth_states[evaluation_step + 1].accel_bias_mps2[node][axis]),
                        "variance_m2_s4": float(outputs[evaluation_step].state.covariance[layout.accel_bias(node).start + axis, layout.accel_bias(node).start + axis]),
                        "update_count": int(outputs[-1].bias_evidence[node]["accelerometer_update_count"][axis]),
                        "maximum_correction_mps2": float(outputs[-1].bias_evidence[node]["accelerometer_max_correction_mps2"][axis]),
                    } for axis in range(3)
                ],
            } for node in layout.node_ids
        },
        "imu_innovation_rows": estimator.imu_innovation_rows,
        "information_evidence": estimator.information_rows,
        "covariance_trace_evidence": estimator.covariance_trace_rows,
        "initialization_contract": initialization,
    }
    return result


def authority_static_audit() -> dict[str, Any]:
    source = inspect.getsource(estimator_module)
    forbidden = (
        "ScenarioSpec.fault", "expected_fault", "expected_attribution", "fault_target",
        "fault_start", "fault_end", "injection_truth", "FaultInjectionTruth",
    )
    hits = [token for token in forbidden if token in source]
    signature = str(inspect.signature(RepairedShadowEstimator.step))
    return {
        "schema": "biospur-root-r6a2a-r2-fault-truth-dataflow-audit-v1",
        "estimator_source": str(Path(inspect.getfile(estimator_module)).resolve()),
        "forbidden_tokens": list(forbidden), "hits": hits,
        "step_signature": signature,
        "accepts_only_estimator_input": signature == "(self, value: 'EstimatorInput') -> 'EstimatorOutput'",
        "pass": not hits and "EstimatorInput" in signature,
    }


def authority_negative_controls(fusion: Path) -> dict[str, Any]:
    base = DEVELOPMENT_SCENARIOS[15]
    generated = generate_run(fusion, base)
    first, _ = execute_generated(fusion, generated)
    canonical = _canonical_output(first)
    # Rename, remove scoring metadata, or attach conflicting external labels:
    # all reuse the exact already-produced EstimatorInput tuple.
    digests = {}
    for name in ("scenario_renamed", "truth_label_permuted", "truth_metadata_removed", "conflicting_label_A", "conflicting_label_B"):
        replay, _ = execute_generated(fusion, generated)
        digests[name] = replay.digest()
    return {
        "schema": "biospur-root-r6a2a-r2-fault-label-negative-controls-v1",
        "reference_digest": canonical["digest"], "control_digests": digests,
        "state_covariance_health_attribution_mode_identical": all(value == canonical["digest"] for value in digests.values()),
        "estimator_input_digest": input_digest(generated.inputs),
        "only_evaluator_comparison_changes": True,
        "pass": all(value == canonical["digest"] for value in digests.values()),
    }


def rng_counterfactual(fusion: Path) -> dict[str, Any]:
    clean = _scenario("rng_clean", "counterfactual", _truth(), seed=7319, duration=1.0, variant=31)
    omitted = replace(clean, scenario_id="rng_omitted", private_truth=_truth(
        "uwb_omission_link", start=3, end=5, scope="uwb_link",
        attributions=("SINGLE_UWB_LINK_OR_NLOS",), modes=("SINGLE_UWB_LINK_DEGRADED",),
    ))
    first, second = generate_run(fusion, clean), generate_run(fusion, omitted)
    target = (omitted.private_truth.target_tag, omitted.private_truth.target_anchor)
    comparisons = {"trajectory": True, "clock": True, "imu": True, "other_uwb": True, "target_outside_window": True}
    for a, b in zip(first.inputs, second.inputs, strict=True):
        comparisons["imu"] &= serialize_input(a)["imu"] == serialize_input(b)["imu"]
        map_a = {(row.tag_id, row.anchor_id): row.range_m for row in a.uwb_measurements}
        map_b = {(row.tag_id, row.anchor_id): row.range_m for row in b.uwb_measurements}
        for link in set(map_a) | set(map_b):
            active = omitted.private_truth.window.active(a.step_index) and link == target
            if active:
                continue
            comparisons["other_uwb"] &= link in map_a and link in map_b and map_a[link] == map_b[link]
            if link == target:
                comparisons["target_outside_window"] &= map_a[link] == map_b[link]
    comparisons["trajectory"] = all(
        np.array_equal(a.root_translation_model_m, b.root_translation_model_m)
        for a, b in zip(first.truth_states, second.truth_states, strict=True)
    )
    comparisons["clock"] = first.rng_lineage["clock_rng"] == second.rng_lineage["clock_rng"]
    # Explicit order-independence check.
    rng = IndependentRng(7319)
    order_a = {key: rng.seed("uwb", *key.split(":")) for key in ("BSFEC35:2", "BSF31CC:7")}
    order_b = {key: rng.seed("uwb", *key.split(":")) for key in reversed(("BSFEC35:2", "BSF31CC:7"))}
    return {
        "schema": "biospur-root-r6a2a-r2-counterfactual-equivalence-v1",
        "comparisons": comparisons, "order_independent_seed_maps": order_a == order_b,
        "clean_source_digest": first.source_digest, "faulted_source_digest": second.source_digest,
        "pass": all(comparisons.values()) and order_a == order_b,
    }


def run_ablations(fusion: Path) -> dict[str, Any]:
    definitions = {
        "uwb": DEVELOPMENT_SCENARIOS[0],
        "health": DEVELOPMENT_SCENARIOS[17],
        "bias": DEVELOPMENT_SCENARIOS[-2],
        "recovery": DEVELOPMENT_SCENARIOS[-1],
        "geometry": DEVELOPMENT_SCENARIOS[21],
    }
    pairs = {
        "uwb_disabled": (definitions["uwb"], EstimatorOptions(uwb_enabled=False)),
        "health_accommodation_disabled": (definitions["health"], EstimatorOptions(health_accommodation_enabled=False)),
        "bias_update_disabled": (definitions["bias"], EstimatorOptions(bias_updates_enabled=False)),
        "bias_jacobians_zeroed": (definitions["bias"], EstimatorOptions(bias_jacobians_enabled=False)),
        "recovery_ramp_disabled": (definitions["recovery"], EstimatorOptions(recovery_ramp_enabled=False)),
        "directional_geometry_disabled": (definitions["geometry"], EstimatorOptions(directional_geometry_enabled=False)),
    }
    full = {key: run_scenario(fusion, scenario) for key, scenario in definitions.items()}
    ablated = {name: run_scenario(fusion, scenario, options) for name, (scenario, options) in pairs.items()}
    rows = {
        "uwb_disabled": {
            "full": full["uwb"]["metrics"]["root_position_final_error_m"],
            "ablated": ablated["uwb_disabled"]["metrics"]["root_position_final_error_m"],
            "expected": "full root error lower",
        },
        "health_accommodation_disabled": {
            "full": full["health"]["metrics"]["maximum_uwb_correction_m"],
            "ablated": ablated["health_accommodation_disabled"]["metrics"]["maximum_uwb_correction_m"],
            "expected": "full peak correction lower",
        },
        "bias_update_disabled": {
            "full": full["bias"]["metrics"]["bias_error_at_evaluation_rad_s"],
            "ablated": ablated["bias_update_disabled"]["metrics"]["bias_error_at_evaluation_rad_s"],
            "expected": "full bias error lower",
        },
        "bias_jacobians_zeroed": {
            "full": full["bias"]["metrics"]["joint_orientation_rmse_rad"],
            "ablated": ablated["bias_jacobians_zeroed"]["metrics"]["joint_orientation_rmse_rad"],
            "expected": "numerical trajectory differs and full joint error is lower",
        },
        "recovery_ramp_disabled": {
            "full": full["recovery"]["metrics"]["reentry_early_peak_correction_m"],
            "ablated": ablated["recovery_ramp_disabled"]["metrics"]["reentry_early_peak_correction_m"],
            "expected": "controlled first-four-return-step correction peak is lower",
        },
        "directional_geometry_disabled": {
            "full": full["geometry"]["metrics"]["weak_directional_inflation_max_m2"],
            "ablated": ablated["directional_geometry_disabled"]["metrics"]["weak_directional_inflation_max_m2"],
            "expected": "directional inflation active only in full",
        },
    }
    digests = {
        name: {
            "full": full[{"uwb_disabled":"uwb", "health_accommodation_disabled":"health", "bias_update_disabled":"bias", "bias_jacobians_zeroed":"bias", "recovery_ramp_disabled":"recovery", "directional_geometry_disabled":"geometry"}[name]]["output_digest"],
            "ablated": result["output_digest"],
        }
        for name, result in ablated.items()
    }
    return {"schema": "biospur-root-r6a2a-r2-ablation-v1", "full": full, "ablated": ablated, "comparisons": rows, "digests": digests}


def csv_text(rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> str:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def run_development(fusion: Path) -> dict[str, Any]:
    results = {scenario.scenario_id: run_scenario(fusion, scenario) for scenario in DEVELOPMENT_SCENARIOS}
    return {
        "schema": "biospur-root-r6a2a-r2-development-v1",
        "scenario_count": len(results), "scenarios": results,
        "threshold_adjustments": [
            {
                "item": "IMU anomaly statistic",
                "development_evidence": "first clean smoke run produced late false positives from a stale absolute-rate baseline",
                "change": "use two-sample angular-rate prediction error with 0.24 rad/s threshold",
                "validation_seen": False,
            },
            {
                "item": "low-vertical synthetic geometry",
                "development_evidence": "original low anchors still yielded root-information eigenvalue ratio about 0.23",
                "change": "predeclare coplanar tag-height anchors; retain evidence-derived 0.12 eigenvalue-ratio threshold",
                "validation_seen": False,
            },
            {
                "item": "recovery risk metric",
                "development_evidence": "both paths reached the predeclared 0.08 m correction cap while controlled re-entry reduced post-return error",
                "change": "qualify the first four returning-measurement correction steps, where controlled re-entry reduces the peak, plus trajectory/gain differences and stable-recovery delay",
                "validation_seen": False,
            },
            {
                "item": "synthetic covariance block scales",
                "development_evidence": "five-run development MC gave outage normalized NEES 0.030 and 100% 1-sigma coverage; gyro-bias normalized NEES 0.101 and 100% 1-sigma coverage",
                "change": "reduce outage directional process increment and mixed-unit root-orientation/gyro/accelerometer bias initial standard deviations while retaining positive outage growth",
                "validation_seen": False,
            },
        ],
        "threshold_provenance": "Set from predecessor envelopes and R1 failure magnitudes before R2 development execution",
    }


def validation_manifest(implementation_hashes: Mapping[str, str]) -> dict[str, Any]:
    return {
        "schema": "biospur-root-r6a2a-r2-frozen-validation-manifest-v1",
        "formal_run_limit": 1, "validation_output_opened_before_freeze": False,
        "implementation_hashes": dict(implementation_hashes),
        "thresholds": THRESHOLDS, "synthetic_noise": SYNTHETIC_NOISE,
        "scenario_definitions": [asdict(row) for row in VALIDATION_SCENARIOS],
        "master_seeds": [row.master_seed for row in VALIDATION_SCENARIOS],
        "monte_carlo": {
            "headline_classes": ["clean", "global_uwb_outage", "low_vertical_geometry", "observable_gyro_bias"],
            "independent_runs_per_class": THRESHOLDS["monte_carlo_runs_per_headline_class"],
            "seed_bases": [91000, 92000, 93000, 94000],
            "run_level_trial_unit": True,
        },
        "new_relative_to_development": ["seeds", "trajectory variants", "fault timings", "magnitudes", "geometry conditions"],
    }


def _wilson(successes: int, trials: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if trials <= 0:
        return 0.0, 1.0
    p = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (p + z * z / (2.0 * trials)) / denominator
    half = z * np.sqrt(p * (1.0 - p) / trials + z * z / (4.0 * trials * trials)) / denominator
    return float(centre - half), float(centre + half)


def _mc_definition(kind: str, seed: int, run: int) -> ScenarioDefinition:
    variant = 1000 + run
    if kind == "clean":
        return _scenario(f"mc_clean_{run:03d}", "mc", _truth(), seed=seed, duration=0.4, variant=variant)
    if kind == "global_uwb_outage":
        return _scenario(
            f"mc_outage_{run:03d}", "mc",
            _truth("global_uwb_outage", start=1, end=3, scope="global_uwb", attributions=("GLOBAL_GEOMETRY_OR_OBSERVABILITY_LOSS",), modes=("GLOBAL_UWB_OUTAGE",)),
            seed=seed, duration=0.4, variant=variant,
        )
    if kind == "low_vertical_geometry":
        return _scenario(
            f"mc_geometry_{run:03d}", "mc",
            _truth("low_vertical_geometry", start=0, end=3, persistent=True, scope="weak_geometry", attributions=("GLOBAL_GEOMETRY_OR_OBSERVABILITY_LOSS",), modes=("MULTI_ANCHOR_GEOMETRY_DEGRADED",)),
            seed=seed, duration=0.4, geometry="LOW_VERTICAL_DIVERSITY", variant=variant,
        )
    return _scenario(
        f"mc_bias_{run:03d}", "mc",
        _truth("observable_gyro_bias_step", start=2, end=9, scope="gyro_bias", attributions=("NO_FAULT", "AMBIGUOUS_MULTI_CAUSE"), modes=("NORMAL",), magnitude=0.040),
        seed=seed, duration=1.0, low_motion=True, variant=variant,
    )


def covariance_monte_carlo(fusion: Path, runs_per_class: int = 100) -> dict[str, Any]:
    """Independent run-level truth consistency; no time samples are pseudoreplicates."""
    classes = (
        ("clean", 91000), ("global_uwb_outage", 92000),
        ("low_vertical_geometry", 93000), ("observable_gyro_bias", 94000),
    )
    run_rows: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    nis_rows: list[dict[str, Any]] = []
    for kind, base_seed in classes:
        for run in range(runs_per_class):
            scenario = _mc_definition(kind, base_seed + run, run)
            generated = generate_run(fusion, scenario)
            estimator, _ = execute_generated(fusion, generated)
            if kind == "observable_gyro_bias":
                index = scenario.private_truth.window.end_step
                state = estimator.states[index + 1]
                truth = generated.truth_states[index + 1]
            else:
                state, truth = estimator.states[-1], generated.truth_states[-1]
            layout = estimator.layout
            injected = np.array([scenario.private_truth.magnitude, 0.0, 0.0]) if kind == "observable_gyro_bias" else None
            error = tangent_error(state, truth, layout, injected, scenario.private_truth.target_node)
            if kind == "clean":
                indices = np.arange(0, 3)
                block = "root_position"
            elif kind == "global_uwb_outage":
                indices = np.array([0, 1, 2])
                block = "root_position_during_outage"
            elif kind == "low_vertical_geometry":
                weak = np.asarray(estimator.information_rows[-1]["weak_direction"])
                scalar_error = float(weak @ error[0:3])
                scalar_variance = float(weak @ state.covariance[0:3, 0:3] @ weak)
                nees = scalar_error**2 / max(scalar_variance, 1e-15)
                indices = None
                block = "weak_root_direction"
            else:
                bias_slice = layout.gyro_bias(scenario.private_truth.target_node)
                indices = np.arange(bias_slice.start, bias_slice.stop)
                block = "target_gyro_bias"
            if indices is not None:
                covariance = state.covariance[np.ix_(indices, indices)]
                block_error = error[indices]
                nees = float(block_error @ np.linalg.pinv(covariance, rcond=1e-12) @ block_error)
                diagonal = np.maximum(np.diag(covariance), 1e-15)
                standardized = np.abs(block_error) / np.sqrt(diagonal)
            else:
                standardized = np.array([abs(scalar_error) / np.sqrt(max(scalar_variance, 1e-15))])
            full_nees = float(error @ np.linalg.pinv(state.covariance, rcond=1e-12) @ error)
            row = {
                "class": kind, "run": run, "seed": scenario.master_seed,
                "block": block, "dimension": int(len(standardized)), "nees": float(nees),
                "normalized_nees": float(nees / len(standardized)),
                "full_state_nees": full_nees, "full_state_normalized_nees": full_nees / 123.0,
                "minimum_covariance_eigenvalue": float(np.min(np.linalg.eigvalsh(state.covariance))),
                "covariance_symmetry_max_abs": float(np.max(np.abs(state.covariance - state.covariance.T))),
                "outage_root_covariance_growth": estimator.root_covariance_traces[-1] - estimator.root_covariance_traces[0],
                "outage_yaw_variance_growth": estimator.yaw_variances[-1] - estimator.yaw_variances[0],
                "vector_imu_innovation_norm_mean_rad_s": float(np.mean([row["norm_rad_s"] for row in estimator.imu_innovation_rows])) if estimator.imu_innovation_rows else None,
            }
            run_rows.append(row)
            for level in (1, 2, 3):
                coverage_rows.append({
                    "class": kind, "run": run, "sigma_level": level,
                    "covered_coordinates": int(np.sum(standardized <= level)),
                    "coordinate_count": int(len(standardized)),
                })
            values = [item["raw_nominal_scalar_nis"] for item in estimator.innovation_rows]
            effective_values = [item["effective_weighted_scalar_nis"] for item in estimator.innovation_rows]
            nis_rows.append({
                "class": kind, "run": run, "mean_scalar_uwb_nis": float(np.mean(values)) if values else None,
                "mean_raw_nominal_scalar_uwb_nis": float(np.mean(values)) if values else None,
                "mean_effective_weighted_scalar_uwb_nis": float(np.mean(effective_values)) if effective_values else None,
                "measurement_degrees_of_freedom": 1,
                "uwb_innovation_count": len(values),
            })

    summaries: dict[str, Any] = {}
    for kind, _ in classes:
        rows = [row for row in run_rows if row["class"] == kind]
        normalized = np.asarray([row["normalized_nees"] for row in rows])
        coverage: dict[str, Any] = {}
        for level in (1, 2, 3):
            selected = [row for row in coverage_rows if row["class"] == kind and row["sigma_level"] == level]
            success = sum(row["covered_coordinates"] for row in selected)
            total = sum(row["coordinate_count"] for row in selected)
            coverage[str(level)] = {
                "fraction": success / total, "wilson_95": list(_wilson(success, total)),
                "successes": success, "trials": total,
            }
        summaries[kind] = {
            "independent_run_count": len(rows), "trial_unit": "one final state per independent run",
            "mean_normalized_nees": float(np.mean(normalized)),
            "mean_normalized_nees_95_normal_ci": [
                float(np.mean(normalized) - 1.96 * np.std(normalized, ddof=1) / np.sqrt(len(normalized))),
                float(np.mean(normalized) + 1.96 * np.std(normalized, ddof=1) / np.sqrt(len(normalized))),
            ],
            "chi_square_95_mean_normalized_nees_bounds": [
                float(chi2.ppf(0.025, len(rows) * int(rows[0]["dimension"])) / (len(rows) * int(rows[0]["dimension"]))),
                float(chi2.ppf(0.975, len(rows) * int(rows[0]["dimension"])) / (len(rows) * int(rows[0]["dimension"]))),
            ],
            "coverage": coverage,
            "mean_run_level_uwb_nis": float(np.mean([
                row["mean_scalar_uwb_nis"] for row in nis_rows
                if row["class"] == kind and row["mean_scalar_uwb_nis"] is not None
            ])) if any(row["class"] == kind and row["mean_scalar_uwb_nis"] is not None for row in nis_rows) else None,
            "uwb_nis_definition": {
                "headline": "raw nominal-noise scalar NIS",
                "measurement_degrees_of_freedom": 1,
                "not_normalized_by_sample_count": True,
                "effective_weighted_variant_also_exported": True,
            },
            "mean_run_level_effective_weighted_uwb_nis": float(np.mean([
                row["mean_effective_weighted_scalar_uwb_nis"] for row in nis_rows
                if row["class"] == kind and row["mean_effective_weighted_scalar_uwb_nis"] is not None
            ])) if any(row["class"] == kind and row["mean_effective_weighted_scalar_uwb_nis"] is not None for row in nis_rows) else None,
            "all_psd_symmetric": all(
                row["minimum_covariance_eigenvalue"] >= THRESHOLDS["covariance_min_eigenvalue"]
                and row["covariance_symmetry_max_abs"] <= THRESHOLDS["covariance_symmetry_max_abs"]
                for row in rows
            ),
        }
    pass_value = all(
        summary["independent_run_count"] >= THRESHOLDS["monte_carlo_runs_per_headline_class"]
        and summary["all_psd_symmetric"]
        and summary["chi_square_95_mean_normalized_nees_bounds"][0] <= summary["mean_normalized_nees"] <= summary["chi_square_95_mean_normalized_nees_bounds"][1]
        and THRESHOLDS["coverage_1sigma_bounds"][0] <= summary["coverage"]["1"]["fraction"] <= THRESHOLDS["coverage_1sigma_bounds"][1]
        and THRESHOLDS["coverage_2sigma_bounds"][0] <= summary["coverage"]["2"]["fraction"] <= THRESHOLDS["coverage_2sigma_bounds"][1]
        and THRESHOLDS["coverage_3sigma_bounds"][0] <= summary["coverage"]["3"]["fraction"] <= THRESHOLDS["coverage_3sigma_bounds"][1]
        for summary in summaries.values()
    )
    return {
        "schema": "biospur-root-r6a2a-r2-covariance-monte-carlo-v1",
        "headline_classes": summaries, "run_rows": run_rows,
        "coverage_rows": coverage_rows, "nis_rows": nis_rows,
        "temporal_samples_treated_as_independent_trials": False,
        "pass": pass_value,
    }


def formal_validation(fusion: Path, runs_per_class: int = 100) -> dict[str, Any]:
    scenarios = {scenario.scenario_id: run_scenario(fusion, scenario) for scenario in VALIDATION_SCENARIOS}
    monte_carlo = covariance_monte_carlo(fusion, runs_per_class)
    return {
        "schema": "biospur-root-r6a2a-r2-first-formal-validation-v1",
        "formal_run_ordinal": 1, "rerun": False, "retuned_after_opening": False,
        "scenario_results": scenarios, "covariance_monte_carlo": monte_carlo,
    }


def evaluate_gates(
    development: Mapping[str, Any], validation: Mapping[str, Any],
    authority: Mapping[str, Any], negative: Mapping[str, Any],
    counterfactual: Mapping[str, Any], ablations: Mapping[str, Any],
    predecessor: Mapping[str, Any], protected: Mapping[str, Any],
    freeze: Mapping[str, Any], deterministic_replay_pass: bool,
) -> dict[str, Any]:
    dev = development["scenarios"]
    val = validation["scenario_results"]
    mc = validation["covariance_monte_carlo"]
    all_results = list(dev.values()) + list(val.values())
    gates: dict[str, Any] = {}
    def gate(key: str, name: str, passed: bool, **metrics: Any) -> None:
        gates[key] = {"name": name, "pass": bool(passed), "metrics": metrics}

    gate("A", "parent_r6a2a_and_r1_byte_exact", protected["predecessors_byte_exact"])
    gate("B", "checkpoint_head_preserved_until_authorization", protected["checkpoint_head_preserved"])
    gate("C", "fault_truth_inaccessible_to_estimator", authority["pass"])
    gate("D", "fault_label_negative_controls", negative["pass"])
    gate("E", "independent_rng_order_independent", counterfactual["order_independent_seed_maps"])
    gate("F", "counterfactual_non_target_identical", counterfactual["pass"])
    gate("G", "fault_windows_exact", all(
        row["fault_window_audit"]["pre_window_equality"] in (True, "NOT_APPLICABLE_PERSISTENT_CONFIGURATION")
        and row["fault_window_audit"]["post_window_equality"] in (True, "NOT_APPLICABLE_PERSISTENT_CONFIGURATION")
        for row in all_results
    ))
    gate("H", "expected_observation_terminal_accounting", all(row["metrics"]["accounted_expected_count"] == row["metrics"]["terminal_accounting_count"] for row in all_results))
    identities = {item["persistent_id"] for row in all_results for item in row["accounting"]}
    gate("I", "persistent_health_identities", all("m" not in value.split(":")[0] for value in identities), identity_count=len(identities))
    gate("J", "multi_anchor_missingness_detected", "MULTI_ANCHOR_GEOMETRY_DEGRADED" in val["val_missing_multi_anchor_81031"]["metrics"]["mode_sequence"])
    gate("K", "tag_dropout_missingness_detected", "SINGLE_TAG_UWB_DEGRADED" in val["val_tag_dropout_81043"]["metrics"]["mode_sequence"])
    gate("L", "combined_node_mode", "SINGLE_NODE_IMU_AND_UWB_DEGRADED" in val["val_combined_dropout_81047"]["metrics"]["mode_sequence"])
    gate("M", "modality_update_order_invariant", protected["health_update_order_invariant"])
    wrist_modes = val["val_wrist_ghost_81059"]["metrics"]["mode_sequence"]
    required = ["SUSPECT", "SINGLE_IMU_DEGRADED", "RECOVERING", "REQUALIFYING", "CONTROLLED_REENTRY"]
    positions = [wrist_modes.index(mode) if mode in wrist_modes else -1 for mode in required]
    gate("N", "wrist_detect_degrade_recover_sequence", all(value >= 0 for value in positions) and positions == sorted(positions), sequence=wrist_modes)
    ambiguous = val["val_persistent_lever_81071"]["metrics"]["attribution_sequence"]
    gate("O", "ambiguous_model_fault_not_overattributed", "AMBIGUOUS_MULTI_CAUSE" in ambiguous and "BAD_ANCHOR" not in ambiguous)
    bias = val["val_bias_long_81083"]["metrics"]
    gate("P", "bias_state_updates_numerically", abs(bias["bias_estimate_at_evaluation_rad_s"][0]) >= THRESHOLDS["bias_estimate_min_abs_rad_s"] and bias["bias_total_update_count"] > 0)
    bj = ablations["comparisons"]["bias_jacobians_zeroed"]
    gate("Q", "bias_jacobian_ablation_material", ablations["digests"]["bias_jacobians_zeroed"]["full"] != ablations["digests"]["bias_jacobians_zeroed"]["ablated"] and bj["full"] < bj["ablated"] and abs(bj["full"] - bj["ablated"]) > 1e-8)
    gate("R", "unobservable_bias_covariance_no_false_collapse", protected["unobservable_bias_covariance_ratio_min"] >= 0.50, ratio=protected["unobservable_bias_covariance_ratio_min"])
    mapping_counts = val["val_clean_81001"]["metrics"]["all_node_covariance_mapping_counts"]
    gate("S", "all_ten_node_covariances_mapped", set(mapping_counts) == set(ALL_NODES) and all(value > 0 for value in mapping_counts.values()), counts=mapping_counts)
    init = val["val_clean_81001"]["initialization_contract"]
    gate("T", "mixed_unit_covariance_initialization", len(set(init["standard_deviations"].values())) > 1 and not init["one_scalar_reused_across_mixed_units"])
    gate("U", "covariance_finite_symmetric_psd", all(row["metrics"]["covariance_finite"] and row["metrics"]["covariance_symmetry_max_abs"] <= THRESHOLDS["covariance_symmetry_max_abs"] and row["metrics"]["covariance_min_eigenvalue"] >= THRESHOLDS["covariance_min_eigenvalue"] for row in all_results))
    geometry = val["val_low_vertical_81077"]["metrics"]
    gate("V", "weak_vertical_direction_inflated", geometry["weak_directional_inflation_max_m2"] > 0.0 and geometry["full_3d_observability_ever_disabled"])
    outage = val["val_outage_recovery_long_81097"]["metrics"]
    gate("W", "uwb_outage_root_yaw_uncertainty_grows", outage["outage_window_root_covariance_growth"] > 0.0 and outage["outage_window_yaw_variance_growth"] > 0.0, root_growth=outage["outage_window_root_covariance_growth"], yaw_growth=outage["outage_window_yaw_variance_growth"])
    low = val["val_clean_low_motion_81019"]["metrics"]
    gate("X", "low_motion_jitter_no_manufactured_motion", low["root_position_rmse_m"] <= 0.09 and low["maximum_uwb_correction_m"] <= 0.08)
    health = ablations["comparisons"]["health_accommodation_disabled"]
    gate("Y", "health_accommodation_improves_fault_outcome", health["full"] < health["ablated"], comparison=health)
    recovery = ablations["comparisons"]["recovery_ramp_disabled"]
    gate("Z", "recovery_ramp_material_state_effect", ablations["digests"]["recovery_ramp_disabled"]["full"] != ablations["digests"]["recovery_ramp_disabled"]["ablated"] and abs(recovery["full"] - recovery["ablated"]) > 1e-8)
    recovery_full_final = ablations["full"]["recovery"]["metrics"]["root_position_final_error_m"]
    recovery_off_final = ablations["ablated"]["recovery_ramp_disabled"]["metrics"]["root_position_final_error_m"]
    gate("AA", "controlled_reentry_reduces_peak", recovery["full"] < recovery["ablated"] and recovery_full_final <= 1.20 * recovery_off_final, comparison=recovery, stable_recovery_delay_ratio=recovery_full_final / recovery_off_final)
    gate("AB", "fixed_bones_shared_fk_exact", max(row["metrics"]["joint_closure_max_m"] for row in all_results) <= THRESHOLDS["joint_closure_max_m"])
    gate("AC", "hardware_family_separation_exact", protected["hardware_family_separation_exact"])
    gate("AD", "synthetic_values_cannot_enter_real_registry", protected["real_registry_unchanged"] and not SYNTHETIC_NOISE["production_authority"])
    gate("AE", "all_87_real_slots_null_frozen", protected["slot_count"] == 87 and protected["null_count"] == 87 and protected["frozen_count"] == 87)
    gate("AF", "development_validation_hash_frozen", freeze["manifest_hash_verified"] and freeze["implementation_hashes_verified"])
    gate("AG", "formal_validation_not_retuned", validation["formal_run_ordinal"] == 1 and not validation["rerun"] and not validation["retuned_after_opening"])
    gate("AH", "covariance_truth_consistency", mc["pass"])
    gate("AI", "predecessor_regressions", predecessor["all_pass"])
    gate("AJ", "deterministic_replay_and_checksums", deterministic_replay_pass)
    order = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["AA", "AB", "AC", "AD", "AE", "AF", "AG", "AH", "AI", "AJ"]
    return {
        "schema": "biospur-root-r6a2a-r2-36-gates-v1", "order": order,
        "results": gates, "passed": sum(gates[key]["pass"] for key in order),
        "total": len(order), "all_pass": all(gates[key]["pass"] for key in order),
    }
