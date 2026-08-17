"""Pre-data contract construction for Phase 2-R."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, value: Any) -> str:
    payload = canonical_bytes(value)
    path.write_bytes(payload)
    return sha256_bytes(payload)


def source_bundle_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        payload = path.read_bytes()
        digest.update(path.as_posix().encode() + b"\0" + hashlib.sha256(payload).digest())
    return digest.hexdigest()


def acceptance_contract(code_hash: str) -> dict[str, Any]:
    return {
        "schema": "biospur-phase2r-acceptance-contract-v1",
        "frozen_before_real_measurement_access": True,
        "code_bundle_sha256": code_hash,
        "association": {
            "global_bijection": "exact_K_best_subset_dynamic_programming",
            "top_k": 20,
            "complete_block_permutations_minimum": 2000,
            "stratified_block_bootstraps_minimum": 1000,
            "observed_margin_must_strictly_exceed_null_p99": True,
            "exact_top_rank_wilson_lower_one_sided_95_minimum": 0.80,
            "each_binding_wilson_lower_one_sided_95_minimum": 0.90,
            "leave_one_action_all_same_mapping": True,
            "mounting_prior_off_and_nominal_same_mapping": True,
            "imu_action_semantics_only_same_mapping": True,
            "truth_exact_matches_required": 10,
        },
        "segmentation": {
            "fixed_repetition_count": None,
            "algorithm": "multi-node robust dynamic-energy local maxima with uncertainty intervals",
            "nominal_threshold_mad": 1.3,
            "sensitivity_scales": [0.8, 1.0, 1.2],
            "minimum_peak_distance_s": 0.55,
            "guard_band_s": 0.25,
            "natural_corrections_retained": True,
        },
        "mounting_prior": {
            "family": "Student_t_tangent_S2",
            "nu": 4,
            "mahalanobis_influence_cap_chi2_2_p99": 9.210,
            "radius": 3.035,
            "weights": [0.0, 0.5, 1.0, 2.0],
            "directed_edge_sign": "BIMODAL_UNRESOLVED",
            "role_information": False,
            "production_factor_count": 0,
            "use": "initializer_and_diagnostic_to_avoid_accelerometer_double_counting",
        },
        "timing_perturbation_ms": [0.5, 1.0, 2.0, 5.0],
        "uwb": {
            "primary_mapping_factor_count": 0,
            "reason": "DEVICE_ANTENNA_METROLOGY_PENDING",
            "leave_one_anchor": "NOT_APPLICABLE_WHEN_FACTOR_COUNT_ZERO",
        },
        "identified_estimate_gate": {
            "data_only_information_nonzero_and_rank_stable": True,
            "synthetic_recovery_and_coverage": True,
            "held_block_predictive_nll_improvement_ci_strictly_positive": True,
            "leave_block_prior_and_uwb_ablation_stability": True,
            "finite_nonzero_local_marginal_not_unsupported_prior": True,
        },
        "rank_relative_svd_tolerances": [1e-4, 1e-5, 1e-6, 1e-7, 1e-8],
    }


def split_protocol() -> dict[str, Any]:
    return {
        "schema": "biospur-phase2r-split-protocol-v1",
        "frozen_before_segmentation": True,
        "unit": "complete_cycle_or_complete_action_block",
        "stratification": "action_id_and_action_family",
        "fit_fraction": 0.70,
        "validation_fraction": 0.30,
        "assignment": "sha256(seed|action_id|cycle_id), ascending within stratum",
        "guard_band_s": 0.25,
        "adjacent_sample_random_split_forbidden": True,
        "claim_scope": "internal_predictive_consistency_single_session",
    }


def seeds() -> dict[str, Any]:
    return {
        "schema": "biospur-phase2r-random-seeds-v1",
        "global": 20260817,
        "segmentation_sensitivity": 2026081701,
        "fit_validation_split": 2026081702,
        "bootstrap": 2026081703,
        "permutation_null": 2026081704,
        "synthetic": 2026081705,
        "conditional_solver": 2026081706,
    }


def p3_scope(pose_contract_path: Path, pose_contract_sha: str) -> dict[str, Any]:
    return {
        "schema": "biospur-p3-provisional-output-scope-and-sensitivity-v1",
        "frozen_before_real_phase2_numeric_access": True,
        "phase1_pose_usability_contract": {"path": str(pose_contract_path), "sha256": pose_contract_sha},
        "instrumented_segment_outputs": 10,
        "scope": "body-relative carrier-segment orientation and joint-motion conditional output",
        "excluded_claims": ["absolute world trajectory", "bone length", "joint centre", "antenna lever arm", "clinical angle", "external accuracy"],
        "gauge_alignment": "initial-standing common tilt only; global translation and yaw remain declared gauges",
        "qualified_motion_classes": "UNQUALIFIED_PENDING_PHASE3_HOLDOUT",
        "horizon": "single promoted action interval; no contact-aided extrapolation",
        "aggregation": ["worst_segment", "all_10_segments"],
        "perturbations": {
            "extrinsic_rotation_deg": [1.0, 3.0, 5.0, 10.0],
            "gyro_bias_raw": [1.0, 5.0, 10.0],
            "accelerometer_bias_raw": [10.0, 50.0, 100.0],
            "timing_ms": [0.5, 1.0, 2.0, 5.0],
            "covariance_scale": [0.5, 1.0, 2.0],
        },
        "high_sensitivity_criterion": "orientation output RMS change >=2 deg OR propagated variance increase >=10 percent",
        "high_sensitivity_unidentified_mode_blocks_authoritative_ready": True,
    }
