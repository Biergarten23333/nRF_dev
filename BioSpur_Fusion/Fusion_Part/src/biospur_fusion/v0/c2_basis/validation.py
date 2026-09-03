"""Fail-closed scientific-contract and negative-control validation."""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np
from scipy.stats import chi2, norm as normal_distribution

from .geometry import BodyGeometry
from .model import STATE_DIMENSION
from .synthetic_truth import SyntheticTruth


FORBIDDEN_INPUT_CONCEPTS = {
    "magnetometer",
    "vendor_quaternion",
    "locked_quaternion",
    "manual_pose_truth",
    "action_metric_pose_truth",
    "uwb_spatial",
}


def validate_basis_contract(config: Mapping[str, Any], geometry: BodyGeometry) -> dict[str, Any]:
    input_contract = config["input_contract"]
    if input_contract["fields"] != [
        "acc_raw", "gyro_raw", "global_time_ns", "status", "boot_epoch",
    ]:
        raise ValueError("direct raw-six-axis input contract changed")
    forbidden_enabled = [
        key for key, value in input_contract.items()
        if key != "fields" and value is not False
    ]
    if forbidden_enabled:
        raise ValueError(f"forbidden input enabled: {forbidden_enabled}")
    if config["hard_feasibility"]["physical_gate_is_optimizer_loss"] is not False:
        raise ValueError("physical feasibility was robustified into a sensor loss")
    if config["geometry"]["lengths_are_estimator_coordinates"] is not False:
        raise ValueError("absolute bone length entered the V0 estimator")
    episode = config["episode_segmentation"]
    stationary_transition_s = float(episode["minimum_stationary_transition_duration_s"])
    metric_bin_s = float(episode["bin_width_ms"]) * 1e-3
    if stationary_transition_s <= 0.0 or abs(stationary_transition_s - metric_bin_s) > 1e-12:
        raise ValueError(
            "C2 stationary transitions must retain exactly one preregistered metric bin"
        )
    workers = {
        name: int(config["stages"][name].get("parallel_workers", 1))
        for name in (
            "D_ALL_ACTION_REFINEMENT", "PROGRESSIVE_PREFIX_UPDATE",
            "FRESH_CUMULATIVE_BATCH",
        )
    }
    if workers != {
        "D_ALL_ACTION_REFINEMENT": 8,
        "PROGRESSIVE_PREFIX_UPDATE": 2,
        "FRESH_CUMULATIVE_BATCH": 8,
    }:
        raise ValueError("C2 bounded multistart worker contract changed")
    expected_solver_walls = {
        "A_STILLNESS_BIAS_GRAVITY": 5.0,
        "B_FUNCTIONAL_MOUNTS_CONNECTIONS": 45.0,
        "C_NINE_RELATIVE_HEADINGS": 35.0,
        "D_ALL_ACTION_REFINEMENT": 60.0,
        "PROGRESSIVE_PREFIX_UPDATE": 25.0,
        "FRESH_CUMULATIVE_BATCH": 90.0,
    }
    observed_solver_walls = {
        name: float(config["stages"][name]["wall_limit_s"])
        for name in expected_solver_walls
    }
    if observed_solver_walls != expected_solver_walls:
        raise ValueError("preregistered solver wall limits were relaxed")
    pipeline = config["bounded_pipeline"]
    qmt = pipeline["qmt_hinge_axis"]
    if (
        qmt["use_official_qmt_sample_selection"] is not True
        or int(qmt["maximum_input_samples_before_selection"]) != 600
        or int(qmt["selected_data_size"]) != 300
        or int(qmt["maximum_gauss_newton_iterations"]) != 80
        or int(qmt["starts"]) != 3
        or float(qmt["minimum_hinge_axis_uncertainty_sigma_deg"]) != 10.0
        or qmt["hinge_axis_uncertainty_model"]
        != "MAX_OF_10_DEG_HUMAN_WORN_FLOOR_AND_QMT_MULTISTART_AXIS_DISPERSION"
    ):
        raise ValueError("bounded QMT public-API contract changed")
    selected_degeneracy = qmt["selection_aware_cross_denominator"]
    if (
        selected_degeneracy["excitation_statistic"]
        != "sqrt(gyro.T @ inverse(still_gyro_noise_covariance) @ gyro)"
        or float(selected_degeneracy["excitation_false_positive_probability"]) != 0.005
        or not np.isclose(
            float(selected_degeneracy["minimum_excitation_mahalanobis_norm"]),
            np.sqrt(chi2.ppf(0.995, df=3)), rtol=0.0, atol=1e-12,
        )
        or float(selected_degeneracy["minimum_excited_selected_fraction"]) != 0.2
        or float(selected_degeneracy["minimum_effective_excited_rows"]) != 5.0
        or selected_degeneracy["alignment_statistic"]
        != "sqrt(cross.T @ pinv(P_axis @ still_gyro_observation_covariance @ P_axis.T) @ cross)"
        or float(selected_degeneracy["alignment_false_positive_probability"]) != 0.005
        or not np.isclose(
            float(selected_degeneracy[
                "maximum_uninformative_cross_noise_significance"
            ]),
            np.sqrt(chi2.ppf(0.995, df=2)), rtol=0.0, atol=1e-12,
        )
        or float(selected_degeneracy["material_uninformative_fraction_among_excited"]) != 0.05
        or float(selected_degeneracy["quantile_probability"]) != 0.05
        or selected_degeneracy["material_gate_requires_fraction_and_quantile"] is not True
        or selected_degeneracy["named_conflict_requires_all_multistarts_material"] is not True
        or selected_degeneracy["isolated_near_axis_rows_are_uninformative_not_failure"] is not True
        or "capture-independent" not in selected_degeneracy["threshold_provenance"]
    ):
        raise ValueError("selection-aware QMT degeneracy thresholds changed")
    qmt_heading = pipeline["qmt_heading_signal"]
    if (
        float(qmt_heading["qmt_official_rating_minimum"]) != 0.25
        or float(qmt_heading[
            "relative_axis_rate_two_sided_false_positive_probability"
        ]) != 0.005
        or not np.isclose(
            float(qmt_heading["minimum_relative_axis_rate_mahalanobis_q90"]),
            normal_distribution.ppf(0.9975), rtol=0.0, atol=1e-12,
        )
        or float(qmt_heading["minimum_active_motion_fraction"]) != 0.05
        or float(qmt_heading["minimum_effective_informative_rows"]) != 5.0
        or float(qmt_heading["maximum_lag1_correlation"]) != 0.98
        or "capture-independent" not in qmt_heading["threshold_provenance"]
    ):
        raise ValueError("noise-standardized QMT heading thresholds changed")
    stillness = config["stillness_uncertainty"]
    if (
        not np.isclose(float(stillness["gyro_raw_lsb_dps"]), 1.0 / 16.384)
        or not np.isclose(
            float(stillness["accelerometer_raw_lsb_mps2"]), 9.80665 / 2048.0,
        )
        or stillness["quantization_variance_model"]
        != "LSB_SQUARED_OVER_12_ADDED_TO_INITIAL_STILL_SAMPLE_COVARIANCE"
        or stillness["gyro_bias_estimator"] != "PER_AXIS_MEDIAN"
        or stillness["gyro_bias_covariance_model"]
        != "PI_OVER_2_TIMES_OBSERVATION_COVARIANCE_OVER_CORRELATION_REDUCED_EFFECTIVE_ROWS"
    ):
        raise ValueError("initial-still quantization/bias uncertainty contract changed")
    required_pipeline_stages = {
        "functional_candidate_bank", "factor_construction",
        "objective_construction", "held_out_evaluation",
    }
    pipeline_walls = pipeline["wall_limits_s"]
    for mode in ("PROGRESSIVE", "FULL", "BATCH"):
        if set(pipeline_walls[mode]) != required_pipeline_stages:
            raise ValueError(f"{mode}: incomplete bounded pipeline stages")
        if any(float(value) <= 0.0 for value in pipeline_walls[mode].values()):
            raise ValueError(f"{mode}: nonpositive bounded pipeline wall")
    if pipeline["existing_solver_iteration_and_wall_limits_unchanged"] is not True:
        raise ValueError("bounded preprocessing was used to relax a solver budget")
    geometry_report = geometry.validate()
    if STATE_DIMENSION != 17:
        raise ValueError("C2 state is not nine headings plus eight mount offsets")
    return {
        "pass": True,
        "direct_input": ["raw_accelerometer", "raw_gyroscope"],
        "forbidden_input_count": 0,
        "state_dimension": STATE_DIMENSION,
        "heading_coordinates": 9,
        "bounded_mount_offset_coordinates": 8,
        "bone_length_coordinates": 0,
        "physical_gate_is_optimizer_loss": False,
        "stationary_reference_transition_partition_s": stationary_transition_s,
        "stationary_transition_claims_motion_or_pose": False,
        "bounded_multistart_parallel_workers": workers,
        "preregistered_solver_wall_limits_s": observed_solver_walls,
        "bounded_pipeline_wall_limits_s": pipeline_walls,
        "qmt_hinge_axis_bounded_public_api": qmt,
        "geometry": geometry_report,
    }


def validate_independent_synthetic_truth(truth: SyntheticTruth) -> dict[str, Any]:
    if not truth.episodes:
        raise ValueError("synthetic oracle has no complete episodes")
    for episode in truth.episodes:
        audit = episode.audit
        if audit.get("generator") != "INDEPENDENT_ANALYTIC_TRUTH":
            raise ValueError("synthetic generator provenance changed")
        if audit.get("estimator_module_imported") is not False:
            raise ValueError("synthetic truth leaked estimator implementation")
        if set(episode.acc) != set(episode.gyro):
            raise ValueError("synthetic raw-six-axis node sets differ")
        if episode.partition != "CUMULATIVE_PROFILE":
            raise ValueError("synthetic episodes were stitched into action profiles")
    return {
        "pass": True,
        "episode_count": len(truth.episodes),
        "independent_analytic_generator": True,
        "estimator_module_imported": False,
        "raw_accelerometer_and_gyroscope_generated": True,
    }


def validate_progressive_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    retained = list(snapshot.get("retained_episodes", []))
    if snapshot.get("single_persistent_profile") is not True:
        raise ValueError("progressive state is not one persistent profile")
    if snapshot.get("per_action_profile_count") != 0:
        raise ValueError("per-action stitching detected")
    if len(retained) != len(set(retained)):
        raise ValueError("progressive profile duplicated an episode")
    if snapshot.get("raw_row_count_used_as_progress") is not False:
        raise ValueError("raw row count was promoted to progress")
    if snapshot.get("action_count_used_as_readiness") is not False:
        raise ValueError("action count was promoted to readiness")
    if snapshot.get("initial_still_complete_calibration") is not False:
        raise ValueError("initial still was falsely declared complete")
    if snapshot.get("step") == 1 and snapshot.get("ready"):
        raise ValueError("initial-still-only prefix cannot be ready")
    return {
        "pass": True,
        "retained_episode_count": len(retained),
        "persistent_profile": True,
        "stitching": False,
        "count_based_progress": False,
    }
