#!/usr/bin/env python3
"""Freeze the append-only P2+ registry before any real C2 fit is allowed."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


RUN_START_SHA256 = "999df2da1d71d5c702e1dda903b96926a2735e6129e35fe5942be749d7514f52"
BASE_REGISTRY_SHA256 = "45373b680bef40c4a7619f4cbe22f735bd184d97f780ddc4fca632dd2a0e0229"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _parameter(
    parameter_id: str,
    owner: str,
    value: Any,
    *,
    units: str,
    state: str,
    source: str,
    formula: str,
    uncertainty: str,
    manifold: str,
    consumers: list[str],
    consequence: str,
    sensitivity: str,
) -> dict[str, Any]:
    return {
        "parameter_id": parameter_id,
        "owner": owner,
        "dimension_or_shape": "scalar_or_structure_as_value",
        "units": units,
        "fixed_derived_fitted_state_or_gauge": state,
        "source_path_or_primary_reference": source,
        "raw_observations_or_formula": formula,
        "prior_or_uncertainty": uncertainty,
        "bounds_or_manifold": manifold,
        "consumer_modules": consumers,
        "consequence_class": consequence,
        "sensitivity_or_identifiability_test": sensitivity,
        "may_change_after_real_fit_begins": False,
        "value": value,
    }


def build_settings() -> dict[str, Any]:
    return {
        "timing": {
            "sample_period_s": 0.005,
            "maximum_lag_s": 1.0,
            "smoothing_window_samples": 21,
            "minimum_overlap_s": 2.0,
            "maximum_abs_drift_ppm": 2000.0,
            "jitter_floor_s": 0.005,
            "clock_match_tolerance_s": 0.00251,
            "minimum_contiguous_span_rows": 101,
            "model": "ONE_PERSISTENT_AFFINE_OFFSET_DRIFT_STATE_PER_ROOTED_PAIR;LOCAL_CORRELATION_LAGS_ARE_CHRONOLOGICAL_OBSERVATIONS_ONLY",
        },
        "hinge_axis": {
            "sample_period_s": 0.005,
            "selection_block_rows": 200,
            "minimum_noise_standardized_excitation": 3.0,
            "maximum_rows": 8000,
            "w0": 50.0,
            "multistarts": 4,
            "multistart_seed": 20260829,
            "tolerance": 1e-8,
            "maximum_steps": 120,
            "bootstrap_replicates": 6,
            "bootstrap_seed": 20260830,
            "human_worn_axis_floor_deg": 5.0,
            "method": "OFFICIAL_QMT_0_2_4_JOINTAXISESTHINGEOLSSON_AFTER_GAP_SAFE_NOISE_STANDARDIZED_BLOCK_SELECTION",
        },
        "joint_center": {
            "sample_period_s": 0.005,
            "savgol_window_samples": 21,
            "savgol_polynomial": 3,
            "maximum_rows": 6000,
            "noise_sigma_multiplier": 3.0,
            "coordinate_bound_m": 1.0,
            "solver_tolerance": 1e-10,
            "maximum_function_evaluations": 400,
            "relative_rank_tolerance": 1e-7,
            "boundary_fraction": 0.98,
            "human_worn_center_floor_m": 0.03,
            "method": "PAIR_LOCAL_SIX_COORDINATE_SEEL_STYLE_NORM_CONSTRAINT_WITH_SOFT_L1_SANDWICH_COVARIANCE",
        },
        "orientation": {
            "vqf_version": "2.0.1",
            "sample_period_s": 0.005,
            "instances_per_node": 1,
            "magnetometer_used": False,
            "initial_bias": "P1_CAPTURE_WIDE_MEDIAN_WITH_NONZERO_COVARIANCE",
            "vqf_bias_interaction": "VQF_ESTIMATES_AND_APPLIES_RESIDUAL_BIAS_AFTER_INITIAL_MEDIAN_SUBTRACTION;BIAS_AND_BIASSIGMA_PRESERVED;FACTOR_GYRO_SUBTRACTS_THE_SAME_RESIDUAL",
            "gap_covariance_semantics": "GAP_ONLY_DIAGNOSTIC_NOT_TOTAL_FIT_WEIGHT",
            "missing_samples": "NO_UPDATE_PLUS_COVARIANCE_GROWTH;NO_INTERPOLATION_ACROSS_GAP;NO_RESET_OR_NEW_GAUGE",
        },
        "quaternion": {
            "qmt": "WXYZ_ACTIVE_WORLD_FROM_SENSOR",
            "scipy": "XYZW_ACTIVE_ROTATION",
            "viewer": "ACTIVE_WORLD_FROM_LOCAL_MATRIX_TIMES_COLUMN_VECTOR",
            "gate": "OFFICIAL_QMT_ROTATE_AND_QINV_PLUS_SCIPY_AND_VIEWER_NUMERIC_ROUND_TRIP",
            "absolute_tolerance": 1e-12,
        },
        "anthropometric_proxy": {
            "name": "LANDMARK_PROXY_C2_V1",
            "surface_readings_preserved_separately": True,
            "surface_to_internal_truth_claimed": False,
            "upper_arm_m": {"left": [0.310, 0.325], "right": [0.310, 0.325]},
            "forearm_m": {"left": [0.245, [0.260, 0.265]], "right": [0.245, [0.260, 0.265]]},
            "thigh_m": {"left": [0.480], "right": [0.480]},
            "shank_m": {"left": [0.430], "right": [0.430]},
            "torso_sensor_distance_m": [0.280],
            "shoulder_surface_proxy": {"biacromial_m": [0.400, 0.425], "chest_to_acromion_line_m": [0.140, 0.150]},
            "hip_surface_proxy": {"bitrochanteric_m": [0.335]},
            "mapping_uncertainty": "NONZERO_UNQUALIFIED_LANDMARK_MAPPING;VIEWER_PROXY_ONLY;FUNCTIONAL_POSTERIOR_COVARIANCE_REPORTED_SEPARATELY",
            "forbidden": ["GH_CENTER_TRUTH", "HIP_CENTER_TRUTH", "SPINE_LENGTH_TRUTH", "HARD_BILATERAL_SYMMETRY"],
        },
        "heading": {
            "method": "OFFICIAL_QMT_0_2_4_HEADINGCORRECTION",
            "input": "GAP_SAFE_EQUIDISTANT_SPANS_AFTER_FUNCTIONAL_SEGMENT_FRAMES",
            "example_subject_settings_reused": False,
            "est_settings": {},
            "outputs_consumed": ["quat2Corr", "delta", "deltaFilt", "rating", "stateOut"],
            "tree_semantics": "delta_child_global = delta_parent_global + deltaFilt_child_edge",
            "pelvis_yaw_gauge_rad": 0.0,
            "gap_policy": "NO_UPDATE_EDGE_STATE_WITH_COVARIANCE_GROWTH",
            "bespoke_global_solver": "FORBIDDEN",
        },
        "progressive": {
            "state": "ONE_CHRONOLOGICAL_CAPTURE_STATE_FOR_ALL_19_EPISODES",
            "prediction": "PREQUENTIAL_NEGATIVE_LOG_PREDICTIVE_DENSITY_SCORED_BEFORE_EPISODE_INGEST",
            "information": "GAUGE_REDUCED_SINGULAR_VALUES_RANK_AND_LOGDET",
            "uncertainty": "POSTERIOR_TANGENT_AND_CONNECTION_COVARIANCE",
            "branches": "NORMALIZED_POSTERIOR_WEIGHTS_WITH_UNIFORM_BROAD_INITIAL_SIGN_PRIOR;NO_EARLY_LOCK",
            "conflict": "PROGRESS_MAY_DECREASE",
            "prefix": "SNAPSHOT_OF_PERSISTENT_STATE_NOT_REFIT",
            "fresh_batch_equivalence": {"relative_parameter_tolerance": 1e-6, "absolute_metric_tolerance": 1e-8},
        },
        "heldout": {
            "open_before_fit_freeze": False,
            "may_refit_after_open": False,
            "external_holdouts": "FORBIDDEN",
            "score": "POST_FREEZE_BLOCKED_PREDICTIVE_LOG_DENSITY_AND_PHYSICAL_VALIDITY_ONLY",
        },
        "viewer": {
            "renderer": "ROOTED_PAIR_CONNECTION_FORWARD_PROPAGATION;NO_IK_REBASE_OR_REPAIR",
            "scene_timestamp_rule": "PREREGISTERED_NORMALIZED_ACTION_MIDPOINT_NEAREST_VALID_ROW_PER_NODE",
            "views": ["front", "side", "top"],
            "camera_extent_m": 1.35,
            "same_camera_geometry_timestamp_across_comparisons": True,
            "labels": "BRANCH_PROXY_UNCERTAINTY_AND_NOT_PASS",
        },
        "synthetic": {
            "positive_seeds": [3101, 3102, 3103, 3104, 3105, 3106],
            "timing_lag_samples": [-150, -47, 0, 31, 120],
            "distributions": {
                "full_SO3_mount": "SCIPY_HAAR_RANDOM",
                "joint_to_sensor_coordinate_m": [-0.22, 0.22],
                "acc_noise_sigma_mps2": [0.015, 0.040],
                "gyro_noise_sigma_rad_s": [0.0015, 0.0040],
                "ar1_correlation": [0.0, 0.65],
                "scale_and_cross_axis_sigma": [0.002, 0.012],
                "bias_drift_rad_s_per_s": [-0.0002, 0.0002],
                "strap_slip_deg": [0.0, 4.0],
                "axis_migration_deg": [0.0, 3.0],
                "center_migration_m": [0.0, 0.008],
                "nonideal_hinge_cross_axis_deg": [0.0, 5.0],
                "timestamp_jitter_s": [-0.001, 0.001],
                "gap_rows": [0, 4],
                "duplicate_rows": [0, 2],
                "quantization": "JY61P_ACC_AND_GYRO_LSB",
            },
            "mandatory_negative_mutations": [
                "STATIC_LOW_INFORMATION", "NEAR_AXIS_ONLY", "WRONG_NODE_MAPPING",
                "OUTSIDE_CLOCK_SUPPORT_NONCYCLIC", "GAP_BRIDGE", "DUPLICATE_TIMESTAMP",
                "CLIPPING", "EXCESSIVE_DRIFT", "ACTION_ORDER_PERMUTATION",
                "AXIS_SIGN_FULL_CIRCLE_BRANCH", "QUATERNION_ACTIVE_PASSIVE_MUTATION",
                "COVARIANCE_UNIT_SCALE_MUTATION",
            ],
            "qualification_thresholds": {
                "positive_case_required_fraction": 0.8,
                "axis_error_80pct_deg": 15.0,
                "center_error_80pct_m": 0.09,
                "coordinate_2sigma_coverage_fraction": 0.75,
                "maximum_timing_error_samples": 2,
                "low_information_condition_number": 1000000.0,
                "wrong_mapping_residual_ratio": 1.5,
                "covariance_symmetry_tolerance": 1e-10,
                "covariance_minimum_eigenvalue_m2": -1e-12,
            },
        },
    }


def main() -> None:
    root = Path.cwd().resolve()
    run_dir = root / "logs/c2_basis_progressive_20260829T102836Z"
    if _sha256(run_dir / "RUN_START_CONTRACT.json") != RUN_START_SHA256:
        raise RuntimeError("run-start seal changed")
    observed_registry = _sha256(run_dir / "ACTIVE_PARAMETER_REGISTRY.json")
    if observed_registry != BASE_REGISTRY_SHA256:
        raise RuntimeError(f"base registry hash differs: {observed_registry}")
    source_paths = [
        root / "src/biospur_fusion/v0/c2_progressive/timebase.py",
        root / "src/biospur_fusion/v0/c2_progressive/orientation.py",
        root / "src/biospur_fusion/v0/c2_progressive/functional_geometry.py",
        root / "src/biospur_fusion/v0/c2_progressive/quaternion_contract.py",
        root / "src/biospur_fusion/v0/c2_progressive/synthetic.py",
    ]
    development = {
        "schema": "biospur-c2-p2-prefit-development-repair-audit-v1",
        "created_utc": _utc_now(),
        "real_rows_fitted_before_or_during_repair": False,
        "failed_wip_preserved_as_findings": [
            "covariance_standardized_was_incorrectly_multiplied_by_robust_sigma_squared",
            "savgol_derivative_was_applied_to_whole_pair_arrays_without_gap_spans",
            "qmt_input_was_uniform_cap_only_with_official_selection_disabled",
            "axis_covariance_was_an_invalid_two_by_two_isotropic_placeholder",
            "synthetic_suite_was_incomplete_and_used_a_cyclic_np_roll_delay_mutation",
            "local_lags_were_not_yet_owned_by_a_persistent_pair_clock_state",
            "quaternion_gate_initially_compared_two_paths_both_derived_from_scipy",
        ],
        "repair": "SUPERSEDED_BEFORE_FIRST_REAL_FIT_BY_APPEND_ONLY_REGISTRY_AND_SYNTHETIC_GATE",
        "current_source_hashes": [{"path": str(path), "sha256": _sha256(path)} for path in source_paths],
    }
    development_path = run_dir / "P2_PREFIT_DEVELOPMENT_REPAIR_AUDIT_001.json"
    _write(development_path, development)
    settings = build_settings()
    parameters = [
        _parameter("persistent_pair_clock_nuisance", "P2_TIMEBASE", settings["timing"], units="s,ppm", state="FIXED_METHOD_AND_FITTED_NUISANCE_STATE", source="Sealed B306 node timers plus P1 timing diagnostics", formula=settings["timing"]["model"], uncertainty="offset/drift covariance, jitter floor, local correlation peak width", manifold="affine pair clock with bounded drift; no per-action profiles", consumers=["timebase", "functional_geometry", "heading"], consequence="A", sensitivity="synthetic lags, drift, jitter, gaps, outside-support mutation"),
        _parameter("continuous_vqf_and_bias_interaction", "P2_ORIENTATION", settings["orientation"], units="rad,s", state="FIXED_METHOD_PLUS_TIME_VARYING_DERIVED_STATE", source="VQF 2.0.1 public updateBatch", formula=settings["orientation"]["vqf_bias_interaction"], uncertainty="P1 median covariance plus VQF biasSigma; gap-only covariance never used as total", manifold="SO3 per node plus bias state; one yaw gauge per node until QMT", consumers=["orientation", "functional_geometry", "heading", "viewer"], consequence="A", sensitivity="bias, drift, gaps, scale/cross-axis synthetic matrix"),
        _parameter("qmt_scipy_viewer_quaternion_contract", "ROTATION_CONVENTION", settings["quaternion"], units="dimensionless", state="FIXED_CONVENTION", source="QMT 0.2.4 qmt.rotate/qinv plus SciPy Rotation", formula=settings["quaternion"]["gate"], uncertainty="numeric tolerance only", manifold="unit quaternion modulo sign and proper SO3", consumers=["orientation", "heading", "viewer"], consequence="A", sensitivity="known quarter-turn, conjugate inverse, full circle, active/passive mutation"),
        _parameter("qmt_olsson_hinge_axis_selection_and_uncertainty", "P2_FUNCTIONAL_AXIS", settings["hinge_axis"], units="rad", state="FIXED_METHOD_PLUS_FITTED_PRODUCT_S2_POSTERIOR", source="QMT 0.2.4 and Olsson et al. Sensors 2020 20 3534", formula=settings["hinge_axis"]["method"], uncertainty="4D product-S2 tangent covariance from block bootstrap, multistart, timing sensitivity, human-worn floor", manifold="S2xS2 modulo simultaneous sign", consumers=["functional_geometry", "segment_frames", "heading"], consequence="A", sensitivity="noise-standardized effective support, nonideal hinge, migration, sign/full-circle branches"),
        _parameter("pair_local_joint_center", "P2_FUNCTIONAL_CENTER", settings["joint_center"], units="m,m2", state="FIXED_METHOD_PLUS_FITTED_PAIR_POSTERIOR", source="Seel Schauer Raisch CCA 2012 / Sensors 2014; QMT missing public center capability", formula=settings["joint_center"]["method"], uncertainty="dimensionally correct soft-L1 sandwich m2 plus separate human-worn model floor", manifold="two full R3 joint-to-sensor vectors per edge; one edge at a time", consumers=["functional_geometry", "segment_frames", "viewer"], consequence="A", sensitivity="coverage, units, gaps, center migration, wrong mapping, low excitation"),
        _parameter("landmark_proxy_geometry", "P2_VIEWER_GEOMETRY", settings["anthropometric_proxy"], units="m", state="FIXED_NON_ANATOMICAL_PROXY", source="GEOMETRY_AND_PARAMETER_CONTRACT plus USER_ANTHROPOMETRY_AMENDMENT_001", formula="preserve each reading/range separately; proxy only where posterior functional geometry is absent", uncertainty=settings["anthropometric_proxy"]["mapping_uncertainty"], manifold="non-anatomical surface landmark proxy", consumers=["segment_frames", "viewer", "A_B_C"], consequence="A", sensitivity="no internal shoulder/hip/torso substitution; all proxy labels visible"),
        _parameter("official_qmt_heading_rooted_tree", "P3_HEADING", settings["heading"], units="rad", state="FIXED_METHOD_PLUS_TIME_VARYING_EDGE_STATE_AND_ROOT_GAUGE", source="QMT 0.2.4 headingCorrection and advanced example propagation semantics", formula=settings["heading"]["tree_semantics"], uncertainty="rating/stateOut, gap growth, branch covariance", manifold="nine circle-valued edge corrections plus one pelvis gauge", consumers=["heading", "progressive", "viewer"], consequence="A", sensitivity="official-vs-mutated propagation and gap/state carry tests"),
        _parameter("genuine_progressive_metrics", "P5_PROGRESSIVE", settings["progressive"], units="mixed", state="FIXED_RULE_PLUS_CHRONOLOGICAL_DERIVED_STATE", source="MASTER_CONTRACT progressive calibration rules", formula=settings["progressive"]["prediction"], uncertainty="posterior and predictive covariance; conflicts may reduce progress", manifold="one persistent chronological state; prefixes are snapshots", consumers=["progressive", "report"], consequence="A", sensitivity="future-leak mutation, order permutation, fresh-batch equivalence"),
        _parameter("post_fit_freeze_heldout", "P6_HELDOUT", settings["heldout"], units="mixed", state="FIXED_FIREWALL", source="PAYLOAD_BYTE_ACCESS_PLAN sealed heldout intervals", formula=settings["heldout"]["score"], uncertainty="blocked predictive score", manifold="read-once after immutable fit freeze; no feedback", consumers=["heldout", "final_report"], consequence="A", sensitivity="file-access audit and no-refit state hash"),
        _parameter("scientific_fk_viewer", "VISUAL_EVIDENCE", settings["viewer"], units="m", state="FIXED_RENDERER_AND_CAMERA", source="USER_VISUAL_MILESTONE_AMENDMENT_001", formula=settings["viewer"]["renderer"], uncertainty="branch/proxy/posterior labels", manifold="identical camera/timestamps/geometry within comparisons", consumers=["P2_visuals", "P3_visuals", "P4_visuals", "P5_visuals"], consequence="A", sensitivity="pixel audit and renderer identity hashes"),
        _parameter("independent_synthetic_qualification", "PREFIT_SYNTHETIC", settings["synthetic"], units="mixed", state="FIXED_DISTRIBUTIONS_THRESHOLDS_AND_SEEDS", source="independent world-frame rigid-body oracle", formula="distribution-level positives plus mandatory mutation matrix", uncertainty="coverage and case fractions, not median-only", manifold="full SO3 mounts, 3D offsets, nonideal human/IMU effects", consumers=["prefit_gate"], consequence="A", sensitivity="every named mandatory mutation must be caught"),
    ]
    amendment = {
        "schema": "biospur-c2-active-parameter-registry-prefit-amendment-v1",
        "created_utc": _utc_now(),
        "append_only_parent": {"path": str(run_dir / "ACTIVE_PARAMETER_REGISTRY.json"), "sha256": observed_registry},
        "run_start_contract_sha256": RUN_START_SHA256,
        "real_fit_started": False,
        "settings": settings,
        "parameters": parameters,
        "all_required_fields_present": all(all(key in row for key in (
            "parameter_id", "owner", "dimension_or_shape", "units",
            "fixed_derived_fitted_state_or_gauge", "source_path_or_primary_reference",
            "raw_observations_or_formula", "prior_or_uncertainty", "bounds_or_manifold",
            "consumer_modules", "consequence_class", "sensitivity_or_identifiability_test",
            "may_change_after_real_fit_begins",
        )) for row in parameters),
        "unregistered_real_fit_or_viewer_parameter_allowed": False,
        "frozen_before_first_real_fit": True,
        "post_fit_change_policy": "NEW_APPEND_ONLY_RUN_AND_RESEAL",
    }
    amendment_path = run_dir / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_001.json"
    _write(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v1",
        "created_utc": _utc_now(),
        "registry_amendment": {"path": str(amendment_path), "sha256": _sha256(amendment_path)},
        "development_repair_audit": {"path": str(development_path), "sha256": _sha256(development_path)},
        "real_fit_authorized_after_registry_alone": False,
        "remaining_gate": "INDEPENDENT_SYNTHETIC_QUALIFICATION_MUST_PASS",
    }
    seal_path = run_dir / "P2_PREFIT_REGISTRY_SEAL_001.json"
    _write(seal_path, seal)
    print(json.dumps({
        "amendment": str(amendment_path), "amendment_sha256": _sha256(amendment_path),
        "seal": str(seal_path), "seal_sha256": _sha256(seal_path),
        "development_audit_sha256": _sha256(development_path),
    }, indent=2))


if __name__ == "__main__":
    main()
