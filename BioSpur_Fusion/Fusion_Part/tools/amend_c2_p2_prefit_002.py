#!/usr/bin/env python3
"""Build and, only when explicitly executed, immutably seal C2 prefit settings.

Importing this module is side-effect free. ``build_effective_settings`` is the
single settings owner used by pre-seal smoke and tests. ``main`` refuses to
overwrite evidence and issues amendment/seal 002 only after every registered
qualification source exists and can be hashed as one exact closure.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN_RELATIVE = Path("logs/c2_basis_progressive_20260829T102836Z")
CONTRACT_RELATIVE = Path("config/biospur_fusion_v0_c2_main_contract_20260829")
PARENT_AMENDMENT_RELATIVE = RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_001.json"
PARENT_AMENDMENT_SHA256 = "0b154d470a0d5593b32af18abb070b7cbee0fea75dc9f18e821e50a2f166f504"
PARENT_SEAL_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_001.json"
PARENT_SEAL_SHA256 = "1889d35b0b8298b913655c263610b11ca9ace68ae490ce928d4d77b138bfaa4b"
RUN_START_RELATIVE = RUN_RELATIVE / "RUN_START_CONTRACT.json"
RUN_START_SHA256 = "999df2da1d71d5c702e1dda903b96926a2735e6129e35fe5942be749d7514f52"
P1_STATE_RELATIVE = RUN_RELATIVE / "P1_FRONTEND/P1_INITIAL_STILL_STOCHASTIC_STATE.json"
P1_STATE_FILE_SHA256 = "b8bc725303e67eb9cad38e11627739a4774fd7832d5f4b0b21e09f4c1f9df377"
P1_MONITOR_ACCEPTANCE_RELATIVE = RUN_RELATIVE / "P1_R3_MONITOR_ACCEPTANCE_001.json"
P1_MONITOR_ACCEPTANCE_SHA256 = "842450eb5c6ec00a5536be520b95a8ee0aaa8af8cd746c75ef4f745c6d28891a"
MONITOR_TASK_ID = "01a04d0f-58f1-7240-b72f-3bf5b44a2156"
AMENDMENT_002_RELATIVE = RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_002.json"
SEAL_002_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_002.json"
REAL_ACTIVATION_RELATIVE = RUN_RELATIVE / "P2_REAL_TRAINING_FIT_ACTIVATION_001.json"
PAYLOAD_BYTE_PLAN_RELATIVE = RUN_RELATIVE / "PAYLOAD_BYTE_ACCESS_PLAN.json"
PAYLOAD_BYTE_PLAN_SHA256 = "65933ab210790516961c17863b22b3824d69c521647ea1035272c2addef4171f"

IDENTITY_RELATIVE = Path(
    "datasets/phase2_calibration/"
    "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/"
    "identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json"
)
WEAR_RELATIVE = IDENTITY_RELATIVE.with_name("POST_SEAL_WEAR_DIRECTION_AMENDMENT_004.json")
FRAME_RELATIVE = IDENTITY_RELATIVE.with_name("POST_SEAL_FRAME_SEMANTICS_AMENDMENT_005.json")
IDENTITY_SHA256 = "8f744eee31ff505b58ee24e88c75f22c6f75dccce7ad4719a2476a42d72a0524"
WEAR_SHA256 = "25464b91b7d77f1cf14df9e0e00c98a8de6f1854edb99b7c7828f0f59815b26e"
FRAME_SHA256 = "989215949e7d33b6939258067d4dd3b9fc18826ca210efb3ad0f6a1d212e175d"

MANDATORY_QUALIFIED_SOURCE_PATHS = (
    "src/biospur_fusion/v0/c2_progressive/__init__.py",
    "src/biospur_fusion/v0/c2_progressive/architecture_guard.py",
    "src/biospur_fusion/v0/c2_progressive/calibration_posterior.py",
    "src/biospur_fusion/v0/c2_progressive/center_prefix.py",
    "src/biospur_fusion/v0/c2_progressive/functional_geometry.py",
    "src/biospur_fusion/v0/c2_progressive/geometry_posterior.py",
    "src/biospur_fusion/v0/c2_progressive/heading.py",
    "src/biospur_fusion/v0/c2_progressive/heldout_evaluation.py",
    "src/biospur_fusion/v0/c2_progressive/orientation.py",
    "src/biospur_fusion/v0/c2_progressive/orientation_uncertainty.py",
    "src/biospur_fusion/v0/c2_progressive/pipeline_runtime.py",
    "src/biospur_fusion/v0/c2_progressive/progressive.py",
    "src/biospur_fusion/v0/c2_progressive/quaternion_contract.py",
    "src/biospur_fusion/v0/c2_progressive/range_reader.py",
    "src/biospur_fusion/v0/c2_progressive/scientific_fk.py",
    "src/biospur_fusion/v0/c2_progressive/scientific_renderer.py",
    "src/biospur_fusion/v0/c2_progressive/segment_frames.py",
    "src/biospur_fusion/v0/c2_progressive/synthetic.py",
    "src/biospur_fusion/v0/c2_progressive/timebase.py",
    "tools/amend_c2_p2_prefit_002.py",
    "tools/run_c2_p2_synthetic_qualification.py",
    "tools/activate_c2_p2_real_fit.py",
    "tools/run_c2_progressive_real.py",
    "tools/render_c2_progressive_scientific.py",
    "tools/evaluate_c2_progressive_holdout.py",
    "tests/v0/test_c2_p2_prefit_owners.py",
    "tests/v0/test_c2_progressive_range_reader.py",
)

POSITIVE_DISTRIBUTION_DIMENSIONS = (
    "FULL_SO3_MOUNTS",
    "NEAR_UNINFORMATIVE_WEAR",
    "HUMAN_DIMENSIONS_ASYMMETRY",
    "IMPERFECT_MOTION_REST",
    "SOFT_TISSUE_SLOW_SLIP",
    "AXIS_CENTER_MIGRATION_NONIDEAL_HINGE",
    "BIAS_SCALE_CROSS_AXIS_NOISE_QUANTIZATION_CORRELATION",
    "JITTER_CLIPPING_DROP_DUPLICATE_GAP",
    "TIME_VARYING_YAW",
    "INTER_EPISODE_NO_UPDATE",
    "EPISODE_ORDER_PERMUTATIONS",
    "DEGENERATE_PREFIX",
    "FULL_CIRCLE_MULTIBRANCH",
    "POST_QMT_PHYSICAL_UNCERTAINTY_SENSITIVITY",
)

SENSOR_AND_NUMERICAL_MUTATIONS = (
    "STATIC_LOW_INFORMATION",
    "NEAR_AXIS_ONLY",
    "UNDERDIMENSIONED_HINGE_EFFECTIVE_SUPPORT_REJECTED",
    "INITIAL_STILL_HINGE_LOCAL_NO_UPDATE",
    "CENTER_GAP_DOES_NOT_CREATE_ELIGIBILITY",
    "CENTER_GYRO_STOCHASTIC_UNCERTAINTY_OMISSION",
    "CENTER_ACCELEROMETER_CALIBRATION_NUISANCE_OMISSION",
    "CENTER_SHARED_CALIBRATION_WHITE_NOISE_ENVELOPE_CONTAMINATION",
    "CENTER_SIGNED_PAIR_CLOCK_NUISANCE",
    "CENTER_PHYSICAL_TIME_SAME_BOOT_GAP_PRESERVED",
    "CENTER_PHYSICAL_TIME_UNKNOWN_EPOCH_LOCAL_NO_UPDATE",
    "CENTER_COHERENT_NUISANCE_REFIT_LOCAL_NO_UPDATE",
    "CENTER_COHERENT_NUISANCE_ANTITHETIC_MIDPOINT_GATE",
    "CENTER_CLOCK_REFIT_SYMMETRIC_INTERIOR_NO_CLAMP",
    "CENTER_COHERENT_NUISANCE_ORDINARY_FAILURE_RETAINED",
    "CENTER_COHERENT_NUISANCE_UNIT_RADIUS_FULL_SPAN_COVERAGE",
    "AXIS_GYRO_CALIBRATION_NUISANCE_OMISSION",
    "AXIS_ACCELEROMETER_CALIBRATION_NUISANCE_OMISSION",
    "AXIS_COMMON_MODE_NUISANCE_AS_INDEPENDENT_ROW_INFORMATION",
    "AXIS_CENTERED_SUPPORT_STATIC_BIAS_INVARIANCE",
    "AXIS_CENTERED_SUPPORT_SCALE_GRAVITY_EXCLUSION",
    "AXIS_IMPLICIT_LINEARIZATION_OFFICIAL_REFIT_DISAGREEMENT",
    "AXIS_EXACT_SCORE_HESSIAN_SINGULAR_OR_INDEFINITE",
    "ZERO_EXCITATION_AXIS_LOCAL_NO_UPDATE",
    "RANK_DEFICIENT_CENTER_NULLSPACE_NO_SHRINK",
    "WRONG_NODE_MAPPING",
    "OUTSIDE_CLOCK_SUPPORT_NONCYCLIC",
    "GAP_BRIDGE",
    "DUPLICATE_TIMESTAMP",
    "CLIPPING",
    "EXCESSIVE_DRIFT",
    "ACTION_ORDER_PERMUTATION",
    "AXIS_SIGN_FULL_CIRCLE_BRANCH",
    "QUATERNION_ACTIVE_PASSIVE_MUTATION",
    "COVARIANCE_UNIT_SCALE_MUTATION",
    "FRAME_COVARIANCE_KNOWN_ROTATION",
    "PRODUCT_S2_ANTIPODAL_NO_ZERO_INNOVATION",
    "WEAR_DIRECTION_SENSITIVITY",
    "MARGINAL_CROSSING_UNCERTAINTY_COVERED",
    "GROSS_SINGLE_PAIR_SUSTAINED_CROSSING",
    "LIMB_GRAVITY_HEMISPHERE_DIAGNOSTIC",
    "AXIAL_GRAVITY_UNCERTAINTY_COVERED",
    "AXIAL_GRAVITY_SUSTAINED_GROSS_CAUGHT",
    "SPARSE_QUANTILE_ORIENTATION_UNCERTAINTY_COLLAPSE",
)

ARCHITECTURE_MUTATIONS = (
    "TRANSACTION_PARTIAL_GEOMETRY_COMMIT",
    "TRANSACTION_PARTIAL_QMT_COMMIT",
    "ONE_KNEE_FORWARD_ONE_KNEE_BACK",
    "PER_ACTION_VQF_RESET",
    "PER_ACTION_QMT_RESET_OR_PROFILE_STITCHING",
    "CROSS_CAPTURE_STATE_SHARING",
    "OLD_WARM_START_OR_PROFILE_IMPORT",
    "CANDIDATE_LOCK",
    "BRANCH_POSTERIOR_SECOND_SOURCE_OR_DIVERGENCE",
    "LEAKED_TRUTH_OR_ACTION_POSE_TRUTH",
    "EXACTIZED_WEAR_DIRECTION_OR_HARD_NUMERIC_CONE",
    "HARD_BILATERAL_MIRROR",
    "FULL_3D_CONNECTION_TO_AXIAL_SCALAR_COLLAPSE",
    "TIME_VARYING_HEADING_TO_MEAN_OR_CONSTANT_SUBSTITUTION",
    "RESULT_DIRECTED_ROW_SELECTION",
    "INVALID_LOW_RESIDUAL_DISPLACES_LEGAL_CANDIDATE",
    "COLLAPSED_GEOMETRY",
    "DISCONNECTED_ROOTED_GRAPH",
    "VIEWER_REBASE_OR_IK_REPAIR_RESCUE",
    "SENSOR_SEGMENT_CONNECTION_FRAME_SWAP",
    "PIPELINE_STAGE_ORDER_BYPASS",
    "FAKE_COUNT_TIME_ITERATION_PROGRESS",
    "FALSE_INITIAL_STILL_COMPLETION",
    "HELDOUT_LEAK_OR_POST_HELDOUT_REFIT",
    "FRESH_BATCH_DISAGREEMENT",
    "CAUSAL_PREFIX_FRESH_DISAGREEMENT",
    "FRESH_READER_SESSION_REUSE",
    "CALLER_ATTESTED_FRESH_UNLOCK",
    "WRONG_NODE_MAPPING",
    "CALLER_COVARIANCE_SUBSTITUTION",
    "CALLER_ENDPOINT_LABEL_SUBSTITUTION",
    "CALLER_HEADING_ARRAY_SUBSTITUTION",
    "CALLER_TIME_GRID_SUBSTITUTION",
    "CALLER_ALIGNED_PHYSICAL_TIME_SUBSTITUTION",
    "CALLER_ALIGNED_BOOT_EPOCH_SUBSTITUTION",
    "CENTER_PREFIX_FUTURE_OR_HELDOUT_LEAK",
    "CENTER_PREFIX_EDGE_POOLING_OR_BACKWARD_SMOOTHING",
    "CENTER_PREFIX_NUISANCE_GATE_BYPASS_OR_HISTORY_REINGESTION",
    "CENTER_PREFIX_FAILED_FACTOR_REINGESTION",
    "CENTER_PREFIX_MEMBERSHIP_OR_ORDER_TOKEN_SUBSTITUTION",
    "PREQUENTIAL_EXACT_GAP_DIFFUSION_DELAYED_OR_OMITTED",
    "PREQUENTIAL_UNKNOWN_RESET_FLOOR_OMITTED_OR_FABRICATED",
    "RAW_UNQMT_ORIENTATION_PHYSICAL_GATE_SUBSTITUTION",
    "PHYSICAL_GATE_STAGE_BYPASS",
    "ALL_INVALID_PHYSICAL_CANDIDATE_ROLLBACK",
    "FORGED_PREFIT_SEAL_OR_ARBITRARY_SETTINGS",
    "FORGED_REAL_FIT_ACTIVATION",
    "FOREARM_DUAL_OBSERVER_PROVENANCE_COLLAPSE",
    "SOFT_WEIGHT_NUMERICAL_ZERO_CANDIDATE_LOCK",
    "HELDOUT_PARTIAL_QMT_TRANSACTION_COMMIT",
    "HELDOUT_PARTIAL_PHYSICAL_BRANCH_COMMIT",
    "SYNTHETIC_RENDERER_OFFICIAL_QMT_LABEL_SUBSTITUTION",
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _semantic_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _read_json(root: Path, relative: Path) -> Mapping[str, Any]:
    return json.loads((root / relative).read_text(encoding="utf-8"))


def _require_hash(root: Path, relative: Path, expected: str) -> None:
    path = root / relative
    if not path.is_file() or _sha(path) != expected:
        raise RuntimeError(f"immutable authority changed or is missing: {relative}")


def _write_new_immutable(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _wear_authority(root: Path) -> dict[str, Any]:
    _require_hash(root, IDENTITY_RELATIVE, IDENTITY_SHA256)
    _require_hash(root, WEAR_RELATIVE, WEAR_SHA256)
    _require_hash(root, FRAME_RELATIVE, FRAME_SHA256)
    identity = _read_json(root, IDENTITY_RELATIVE)
    wear = _read_json(root, WEAR_RELATIVE)
    frame = _read_json(root, FRAME_RELATIVE)
    identity_pairs = {(row["hardware_id"], row["body_segment"]) for row in identity["rows"]}
    wear_pairs = {(row["hardware_id"], row["body_segment"]) for row in wear["rows"]}
    if identity_pairs != wear_pairs or len(identity_pairs) != 10:
        raise RuntimeError("sealed identity and wear amendments do not bind the same exact ten nodes")
    if wear["body_frame"] != frame["body_frame"]:
        raise RuntimeError("wear and frame amendments disagree about the body frame")
    return {
        "sealed_identity_sha256": IDENTITY_SHA256,
        "wear_amendment_sha256": WEAR_SHA256,
        "frame_amendment_sha256": FRAME_SHA256,
        "authority_files": {
            "sealed_identity": {"path": str(IDENTITY_RELATIVE), "sha256": IDENTITY_SHA256},
            "wear_amendment": {"path": str(WEAR_RELATIVE), "sha256": WEAR_SHA256},
            "frame_amendment": {"path": str(FRAME_RELATIVE), "sha256": FRAME_SHA256},
        },
        "body_frame": deepcopy(wear["body_frame"]),
        "common_direction": deepcopy(wear["common_direction"]),
        "rows": deepcopy(wear["rows"]),
        "uncertainty_contract": deepcopy(wear["uncertainty_contract"]),
        "identity_hardware_to_segment": {
            row["hardware_id"]: row["body_segment"] for row in identity["rows"]
        },
        "qualitative_forward_projection_checks": deepcopy(
            frame["qualitative_forward_projection_checks"]
        ),
    }


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    """Return the complete pre-outcome settings document without writing files."""

    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("prefit settings may be built only in the canonical Fusion_Part workspace")
    for relative, expected in (
        (PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256),
        (PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256),
        (RUN_START_RELATIVE, RUN_START_SHA256),
        (P1_STATE_RELATIVE, P1_STATE_FILE_SHA256),
        (PAYLOAD_BYTE_PLAN_RELATIVE, PAYLOAD_BYTE_PLAN_SHA256),
        (P1_MONITOR_ACCEPTANCE_RELATIVE, P1_MONITOR_ACCEPTANCE_SHA256),
    ):
        _require_hash(root, relative, expected)
    parent = _read_json(root, PARENT_AMENDMENT_RELATIVE)
    startup = _read_json(root, CONTRACT_RELATIVE / "STARTUP_PARAMETERS.json")
    initial_state = _read_json(root, P1_STATE_RELATIVE)
    settings = deepcopy(parent["settings"])
    expected_forearm_observations = {
        "left": [0.245, [0.260, 0.265]],
        "right": [0.245, [0.260, 0.265]],
    }
    if settings["anthropometric_proxy"].get("forearm_m") != expected_forearm_observations:
        raise RuntimeError(
            "amendment 001 no longer preserves the exact separate bilateral forearm observer values"
        )

    from biospur_fusion.v0.c2_progressive.functional_geometry import (
        EDGE_SPECS,
        HINGE_EDGES,
        canonical_hinge_sign_branch_ids,
    )

    chronology = list(startup["chronological_actions"])
    if len(chronology) != 19 or len(set(chronology)) != 19:
        raise RuntimeError("STARTUP_PARAMETERS no longer binds 19 unique chronological actions")
    settings["execution_contract"] = {
        "canonical_workspace": str(root),
        "monitor_task_id": MONITOR_TASK_ID,
        "chronological_actions": chronology,
        "run_start_contract": {"path": str(RUN_START_RELATIVE), "sha256": RUN_START_SHA256},
        "static_validator_execution_authorized_literal": False,
        "activation_message_date": "2026-08-29",
        "prefit_registry_seal_relative_path": str(SEAL_002_RELATIVE),
        "real_fit_activation_relative_path": str(REAL_ACTIVATION_RELATIVE),
        "p1_monitor_acceptance": {
            "path": str(P1_MONITOR_ACCEPTANCE_RELATIVE),
            "sha256": P1_MONITOR_ACCEPTANCE_SHA256,
            "scope": "DIAGNOSTIC_ONLY_NON_ANATOMICAL_NOT_PASS",
        },
        "initial_stochastic_state_relative_path": str(P1_STATE_RELATIVE),
        "initial_stochastic_state_file_sha256": P1_STATE_FILE_SHA256,
        "initial_stochastic_state_semantic_sha256": _semantic_sha(initial_state),
        "payload_byte_access_plan_relative_path": str(PAYLOAD_BYTE_PLAN_RELATIVE),
        "payload_byte_access_plan_sha256": PAYLOAD_BYTE_PLAN_SHA256,
        "mandatory_qualified_source_paths": list(MANDATORY_QUALIFIED_SOURCE_PATHS),
        "qualified_source_runtime_revalidation": {
            "before_any_payload_open": True,
            "existence_and_regular_file_required": True,
            "exact_sha256_recomputed": True,
            "source_mode_recorded": True,
            "read_only_required": False,
            "read_only_rationale": "The immutable seal and activation bind exact content hashes; mode bits are recorded, while any byte change is a Class-A rejection and requires append-only reseal.",
        },
        "synthetic_role_may_open_real_rows": False,
        "real_roles_require_post_synthetic_activation": True,
        "heldout_remains_closed_until_fresh_raw_verification": True,
        "real_runner": {
            "authoritative_entrypoint": "tools/run_c2_progressive_real.py",
            "primary_execution_role": "PRIMARY_CAUSAL",
            "fresh_execution_role": "FRESH_RAW_RECOMPUTATION",
            "distinct_reader_sessions_required": True,
            "ordinary_episode_retry_limit": 32,
            "qmt_failure_pivot_scope": "CURRENT_EDGE_ALL_BRANCHES_LOCAL_NO_UPDATE_AFTER_EXACT_TRANSACTION_ROLLBACK",
            "heldout_decode_in_this_entrypoint": False,
            "legacy_c2_basis_or_ik_rebase_anthropometric_fit_allowed": False,
            "scientific_cli_parameter_overrides_allowed": False,
        },
        "pipeline_order": [
            "CONTINUOUS_CAPTURE_WIDE_ORIENTATION",
            "PREQUENTIAL_SCORE_PREFIX_I_MINUS_1",
            "CURRENT_EPISODE_AXES_AND_CAUSAL_SAME_EDGE_CENTER_PREFIX_WITH_PREINGEST_SCORE",
            "CURRENT_PREFIX_SEGMENT_FRAME_BRANCHES_PLUS_BROAD_WEAR",
            "OFFICIAL_TIME_VARYING_QMT_ALL_SUPPORTED_BRANCHES",
            "OFFICIAL_ROOTED_NINE_EDGE_PARENT_PLUS_CHILD_PROPAGATION",
            "QMT_CORRECTED_PHYSICAL_CANDIDATE_ASSESSMENT",
            "CURRENT_EPISODE_PROGRESSIVE_COMMIT",
            "FINAL_FREEZE_THEN_DISTINCT_RAW_FRESH_RECOMPUTATION",
        ],
    }

    settings["anthropometric_proxy"].update({
        "fit_role": "VIEWER_ONLY_NON_ANATOMICAL_SCALE_CONTEXT",
        "allowed_in_segment_frame_or_fit_residual": False,
        "allowed_to_replace_functional_center": False,
        "quantitative_mapping_covariance": None,
        "reason": "No qualified surface-landmark-to-internal-joint distribution exists; no anthropometric value enters functional fitting or segment frames.",
        "forearm_dual_observer_provenance": {
            side: {
                "observer_a_point_m": 0.245,
                "observer_b_interval_m": [0.260, 0.265],
                "observer_values_kept_separate": True,
                "midpoint_or_combined_value": None,
                "allowed_in_fit_or_functional_center": False,
                "claimed_equal_to_internal_bone_length": False,
            }
            for side in ("left", "right")
        },
    })
    settings["orientation"].update({
        "unknown_boot_orientation_sigma_rad": float(np.deg2rad(30.0)),
        "unknown_unusable_episode_orientation_sigma_rad": float(np.deg2rad(5.0)),
        "unknown_duration_policy": "POSITIVE_REGISTERED_UNCERTAINTY_FLOOR_WITHOUT_INVENTED_DURATION",
        "boot_floor_provenance": "Conservative prefit 30 degree uncertainty for an unobserved boot boundary; not fitted from C2.",
        "unusable_episode_floor_provenance": "Five degree local no-update floor prevents unknown-duration unusable episodes from carrying zero uncertainty.",
        "floor_sensitivity_deg": {"unknown_boot": [15.0, 30.0, 60.0], "unknown_unusable_episode": [2.5, 5.0, 10.0]},
        "inter_episode_scheduled_unobserved_time_counted_as_packet_drop": False,
    })
    settings["timing"].update({
        "maximum_abs_drift_ppm_provenance": "Two 500 ppm LFRC envelopes give 1000 ppm worst-direction pair drift; a factor-two prefit guard gives 2000 ppm.",
        "maximum_abs_drift_ppm_sensitivity": [1000.0, 2000.0, 4000.0],
        "factor_fields": ["acc", "gyro", "clock_uncertainty", "gap_safe_owner_token"],
        "local_lag_role": "OBSERVATION_OF_ONE_PERSISTENT_PAIR_CLOCK_STATE_NOT_ACTION_PROFILE",
        "selected_lag_and_uncertainty_enter_covariance": True,
        "derivative_boundary_policy": "STRICTLY_WITHIN_EACH_SELECTED_CONTIGUOUS_BLOCK",
    })
    settings["hinge_axis"].update({
        "selection_policy": "PREFIT_FIXED_NOISE_STANDARDIZED_CONTIGUOUS_BLOCKS",
        "hessian_relative_rank_tolerance": 1e-7,
        "minimum_selected_observed_rows": 300,
        "local_tangent_parameter_dimension": 4,
        "minimum_effective_support_per_parameter": 10.0,
        "minimum_allowed_sensitivity_support_per_parameter": 5.0,
        "minimum_effective_support_rows": 40.0,
        "initial_still_functional_hinge_update_allowed": False,
        "low_information_no_update_sigma_rad": float(np.deg2rad(45.0)),
        "minimum_noise_standardized_excitation_provenance": "Three-sigma prefit reference; below-threshold top blocks are diagnostic and not owner updates.",
        "minimum_noise_standardized_excitation_sensitivity": [2.0, 3.0, 5.0],
        "human_worn_axis_floor_provenance": "Five-degree shared floor covers strap/soft-tissue and axis-migration sensitivity; it is not independently resampled.",
        "human_worn_axis_floor_sensitivity_deg": [2.5, 5.0, 10.0],
        "selected_observed_rows_provenance": "At least 1.5 seconds of actual gap-safe selected 200 Hz observations; interpolation never counts as evidence.",
        "selected_observed_rows_sensitivity": [200, 300, 600],
        "effective_support_provenance": "Four product-S2 local tangent parameters require support beyond numeric Hessian rank alone. The primary pre-outcome design rule is ten correlation-adjusted effective observations per parameter (40 total); it is not inferred from a positive outcome. Initial still is explicitly low-information and cannot own this threshold.",
        "effective_support_sensitivity_rows": [20.0, 40.0, 80.0],
        "effective_support_sensitivity_provenance": "Five, ten, and twenty effective observations per each of four local tangent parameters form an eligibility-only threshold neighborhood. Reusing identical fit inputs here cannot establish estimate or covariance stability; that belongs to the separate support-information block-prefix gate.",
        "support_information_block_counts": [20, 40, 80],
        "support_information_effective_support_threshold_rows": [20.0, 40.0, 80.0],
        "support_information_sensitivity_provenance": "Separate from eligibility-only threshold reruns, the estimator consumes chronological prefixes of 20, 40 and 80 complete 200-row gap-safe blocks. These result-independent nested inputs change actual information while preserving gap/boot boundaries.",
        "hessian_rank_tolerance_sensitivity": [1e-8, 1e-7, 1e-6],
    })
    hinge_axis = settings["hinge_axis"]
    expected_primary_support = (
        int(hinge_axis["local_tangent_parameter_dimension"])
        * float(hinge_axis["minimum_effective_support_per_parameter"])
    )
    if float(hinge_axis["minimum_effective_support_rows"]) != expected_primary_support:
        raise RuntimeError("primary hinge effective support is not dimension-owned")
    minimum_sensitivity_support = (
        int(hinge_axis["local_tangent_parameter_dimension"])
        * float(hinge_axis["minimum_allowed_sensitivity_support_per_parameter"])
    )
    sensitivity_rows = [float(value) for value in hinge_axis["effective_support_sensitivity_rows"]]
    if (
        float(hinge_axis["minimum_effective_support_rows"]) not in sensitivity_rows
        or any(value < minimum_sensitivity_support for value in sensitivity_rows)
    ):
        raise RuntimeError("hinge effective-support sensitivity is below its dimension-owned bound")
    support_block_counts = [int(value) for value in hinge_axis["support_information_block_counts"]]
    support_information_thresholds = [
        float(value)
        for value in hinge_axis["support_information_effective_support_threshold_rows"]
    ]
    if (
        support_block_counts != [20, 40, 80]
        or support_information_thresholds != sensitivity_rows
        or any(
            later <= earlier
            for earlier, later in zip(support_block_counts[:-1], support_block_counts[1:])
        )
    ):
        raise RuntimeError("hinge support-information block-prefix design changed")
    settings["joint_center"].update({
        "nullspace_prior_sigma_m": 1.0,
        "random_multistarts": 12,
        "selection_block_rows": 200,
        "robust_loss": "soft_l1",
        "robust_f_scale_standardized": 0.25,
        "multistart_seed": 20260831,
        "multistart_initial_fraction_of_coordinate_bound": 0.5,
        "minimum_legal_multistarts_for_update": 2,
        "competitive_normalized_cost_relative_tolerance": 0.05,
        "competitive_normalized_cost_absolute_tolerance": 1e-6,
        "interior_basin_radius_m": 0.05,
        "minimum_consistent_interior_start_fraction": 0.75,
        "maximum_robust_bread_condition_number": 1e4,
        "minimum_informed_information_eigenvalue_m2_inv": 44.44444444444444,
        "maximum_informed_observation_sigma_m": 0.15,
        "chronological_prefix_fraction_for_stability": 0.60,
        "minimum_blocks_per_prefix_or_heldin_partition": 2,
        "maximum_prefix_heldin_residual_robust_sigma": 3.0,
        "local_parameter_dimension": 6,
        "minimum_complete_blocks_per_parameter": 2,
        "minimum_complete_blocks_for_update": 12,
        "feasible_gls_gyro_reweight_passes": 2,
        "maximum_reweight_sigma_relative_rms_change": 0.25,
        "accelerometer_unresolved_bias_sigma_mps2": 0.25,
        "accelerometer_bias_drift_rate_sigma_mps3": 0.003,
        "accelerometer_bias_drift_horizon_s": 30.0,
        "observed_physical_time_policy": {
            "schema": "biospur-c2-center-observed-physical-time-policy-v1",
            "units": "s",
            "owner": "AlignedPair_FROM_CURRENT_SEALED_ORIENTED_ACTION",
            "same_boot_gap_policy": "PRESERVE_TRUE_NODE_SPECIFIC_TIMER_SEPARATION_WITHOUT_DIFFERENTIATING_ACROSS_GAP",
            "cross_pair_policy": "SAME_NODE_BOOT_EPOCH_AND_STRICTLY_MONOTONIC_NONOVERLAPPING_TIMERS_SHARE_ONE_ELAPSED_AXIS",
            "unknown_boot_or_reset_policy": "LOCAL_CENTER_FACTOR_NO_UPDATE;NO_ELAPSED_CONTINUITY_INVENTED;RUNTIME_UNKNOWN_INTERVAL_FLOOR_MUST_BE_PROVEN_SEPARATELY",
            "cap_or_permutation_policy": "MAY_SELECT_OR_REORDER_ROWS_RESULT_INDEPENDENTLY_BUT_MUST_NOT_SYNTHESIZE_OR_CHANGE_OBSERVED_TIME_VALUES",
        },
        "physical_time_full_owner_mutation": {
            "schema": "biospur-c2-center-physical-time-full-owner-mutation-settings-v1",
            "source_fixture": "synthetic.mutation_fixtures.center_ideal_diagnostic",
            "first_pair_source_half_open_rows": [0, 3000],
            "second_pair_source_half_open_rows": [3000, 6000],
            "same_boot_additional_gap_s": [0.005, 5.0],
            "same_boot_epoch_key": [0, 0],
            "unknown_reset_second_pair_epoch_key": [1, 1],
            "unknown_reset_second_pair_time_origin_s": 0.0,
            "required_owner_calls": [
                "estimate_joint_center_pair_local:SHORT_KNOWN_GAP",
                "estimate_joint_center_pair_local:LONG_KNOWN_GAP",
                "estimate_joint_center_pair_local:UNKNOWN_CROSS_PAIR_RESET"
            ],
            "known_gap_gate": "EQUAL_RETAINED_ROWS_AND_IDENTICAL_SENSOR_ARRAYS;DISTINCT_OWNER_BOUND_PHYSICAL_TIME_HASHES;DISTINCT_ACCELEROMETER_AND_GYRO_DRIFT_STRESS;NO_CROSS_GAP_DIFFERENTIATION",
            "unknown_reset_gate": "OWNER_UPDATE_ELIGIBLE_FALSE;LOCAL_NO_UPDATE;NO_ELAPSED_CONTINUITY_INVENTED;SEPARATE_RUNTIME_PREQUENTIAL_UNKNOWN_FLOOR_MUTATION_MUST_PASS",
            "preeligibility_provenance_diagnostic": {
                "schema": "biospur-c2-center-physical-time-drift-input-provenance-v1",
                "required_even_when_nominal_factor_is_local_no_update": True,
                "centered_elapsed_transform": "NODE_SPECIFIC_MEDIAN_CENTERED_WITHIN_TRUSTWORTHY_BOOT_EPOCH_IDENTITY_THEN_CLIPPED",
                "required_fields": [
                    "observed_time_hashes",
                    "centered_clipped_elapsed_hashes_and_extrema",
                    "epoch_group_hashes",
                    "registered_accelerometer_and_gyro_drift_stress_input_hashes"
                ],
                "drift_input_semantics": "CENTERED_CLIPPED_ELAPSED_ARRAYS_TIMES_FROZEN_UNIT_MAHALANOBIS_DRIFT_DIRECTIONS_AND_REGISTERED_RATES;RESULT_INDEPENDENT_PROVENANCE_ONLY",
                "successful_nuisance_refit_required": False,
                "candidate_solution_covariance_information_or_posterior": False,
                "may_promote_owner_update": False,
                "forbidden_consumers": [
                    "SEGMENT_FRAMES",
                    "QMT_HEADING",
                    "ROOTED_PROPAGATION",
                    "SCIENTIFIC_RENDERER",
                    "PROGRESSIVE_CALIBRATION",
                    "QUALIFICATION",
                    "PREFIT_SEAL"
                ]
            },
            "threshold_or_nuisance_change_allowed": False,
            "attempt_125_or_132_may_be_rerun_before_pass": False
        },
        "causal_historical_prefix_owner": {
            "schema": "biospur-c2-causal-center-prefix-owner-settings-v1",
            "edge_route_owner": "functional_geometry.EDGE_ACTIONS_IN_EXACT_19_ACTION_CHRONOLOGY",
            "mode_before_first_accepted_center": "SAME_EDGE_COMMITTED_HISTORICAL_PREFIX_PLUS_CURRENT_ACTION_AFTER_PREQUENTIAL_SCORE",
            "mode_after_first_accepted_center": "CURRENT_ACTION_ONLY_NO_HISTORICAL_REINGESTION",
            "preingest_requirement": "PUBLISH_PREFIX_I_MINUS_1_PREQUENTIAL_PREDICTION_AND_DIAGNOSTICS_BEFORE_CURRENT_ACTION_PAIR",
            "causality": "ONLY_COMMITTED_ACTION_INDICES_STRICTLY_LESS_THAN_CURRENT;NO_FUTURE;NO_HELDOUT;NO_BACKWARD_SMOOTHING",
            "edge_pooling": "FORBIDDEN",
            "pair_identity": "PRESERVE_RUNTIME_OWNER_TOKEN_ACTION_INDEX_NODE_BOOT_GAP_AND_CONTIGUOUS_SPANS",
            "prefix_membership_and_order_binding": "SHA256_OF_EXACT_ORDERED_PAIR_ACTION_INDEX_RUNTIME_TOKEN_BOOT_EPOCH_CONTIGUOUS_SPAN_AND_CLUSTER_IDENTITY_ROWS",
            "commit_token_enforcement": "RECOMPUTE_EXACT_ORDERED_MEMBERSHIP_SHA256_AND_SELECTION_TOKEN_FROM_LIVE_SELECTION_FIELDS_AND_PAIRS_AT_COMMIT;STALE_REPORT_OR_TOKEN_REJECTED",
            "pair_cluster_correlation": "PRESERVE_DISTINCT_PAIR_ACTION_CLOCK_GROUP_AND_COMPLETE_GAP_SAFE_BLOCK_CLUSTER_IDENTITIES;NEVER_TREAT_HISTORICAL_CONCATENATION_AS_IID",
            "unknown_gap_or_boot": "UNCHANGED_ESTIMATOR_LOCAL_NO_UPDATE_AND_REGISTERED_COVARIANCE_GROWTH;NO_FABRICATED_ELAPSED",
            "nuisance_and_refit_gates": "IDENTICAL_TO_SINGLE_ACTION_CENTER_OWNER;CUMULATIVE_PREFIX_CANNOT_BYPASS",
            "ordinary_estimator_failure": "TRANSACTION_ROLLBACK_THEN_EXPLICIT_CURRENT_PAIR_LOCAL_NO_UPDATE_COMMIT_ON_BOUNDED_RETRY;PAIR_ENTERS_IMMUTABLE_ZERO_INFORMATION_EVIDENCE_LEDGER_ONLY_AND_IS_EXCLUDED_FROM_LATER_ESTIMATOR_PREFIX",
            "successful_low_information_factor": "MAY_ENTER_FUTURE_SAME_EDGE_ESTIMATOR_PREFIX_BECAUSE_ESTIMATOR_COMPLETED_AND_NO_INFORMATION_WAS_INGESTED",
            "branch_or_basin_uncertainty": "RETAINED;NO_COLLAPSE_OR_INTERIOR_SELECTION_AFTER_COMPETING_BASIN",
            "first_accepted_information_ownership": "ALL_PREFIX_ROWS_INGESTED_EXACTLY_ONCE_AT_FIRST_ACCEPTANCE",
            "post_acceptance_information_ownership": "ONLY_NEW_CURRENT_ACTION_ROWS_MAY_UPDATE;HISTORICAL_PREFIX_NEVER_REINGESTED",
            "downstream_authority": "PAIR_LOCAL_CENTER_ONLY;NO_SEGMENT_FRAME_QMT_ROOTED_PROPAGATION_RENDERER_OR_SKELETON_AUTHORITY",
            "transaction": "SELECTION_AND_COMMIT_ROLL_BACK_WITH_RUNTIME_OWNER_STATE",
            "formal_negative_mutations": [
                "CENTER_PREFIX_FUTURE_OR_HELDOUT_LEAK",
                "CENTER_PREFIX_EDGE_POOLING_OR_BACKWARD_SMOOTHING",
                "CENTER_PREFIX_NUISANCE_GATE_BYPASS_OR_HISTORY_REINGESTION",
                "CENTER_PREFIX_FAILED_FACTOR_REINGESTION",
                "CENTER_PREFIX_MEMBERSHIP_OR_ORDER_TOKEN_SUBSTITUTION"
            ]
        },
        "accelerometer_scale_cross_axis_fraction_sigma": 0.012,
        "gyro_scale_cross_axis_fraction_sigma": 0.012,
        "accelerometer_gyro_shared_scale_cross_axis_fraction_sigma": 0.012,
        "gyro_bias_drift_correlation_time_s": 30.0,
        "accelerometer_calibration_covariance_sensitivity_multipliers": [
            0.25, 1.0, 4.0
        ],
        "gyro_stochastic_covariance_sensitivity_multipliers": [0.25, 1.0, 4.0],
        "matched_gap_local_savgol_acc_gyro_alpha_preprocessing": True,
        "savgol_serial_correlation_policy": "SOURCE_SEPARATED_WHITE_OBSERVATION_DERIVATIVE_FIXED_KERNEL_SPECTRAL_VARIANCE_ENVELOPE_PLUS_COMPLETE_RAW_BLOCK_CLUSTER_SANDWICH;SHARED_CALIBRATION_J_C_JT_EXCLUDES_WHITE_ENVELOPE",
        "shared_calibration_row_weight_role": "SIGNED_J_C_JT_DIAGONAL_MARGINAL_FOR_ROBUST_STANDARDIZATION_ONLY_NOT_INDEPENDENT_EPISODE_INFORMATION",
        "shared_calibration_posterior_role": "SIGNED_LOW_RANK_SYSTEMATIC_PUSHFORWARD_NONSHRINKING_ACROSS_EPISODES",
        "shared_calibration_white_noise_multiplier": 1.0,
        "shared_calibration_serial_correlation_variance_envelope_multiplier": 1.0,
        "pair_clock_offset_convention": "POSITIVE_OFFSET_EVALUATES_CHILD_LATER_AT_FIXED_PARENT_PHYSICAL_TIME",
        "pair_clock_systematic_jacobian": "SIGNED_NEGATIVE_BLOCK_LOCAL_TIME_DERIVATIVE_OF_FULL_CORRECTED_CHILD_NORM",
        "human_worn_center_floor_provenance": "Thirty-millimetre shared envelope for soft tissue, strap slip and center migration; never repeated as episode information.",
        "robust_loss_provenance": "The pair-local Seel norm constraint uses SciPy soft_l1 as a smooth bounded approximation to the sum-absolute-error estimator evaluated by Olsson and Halvorsen for soft-tissue/outlier robustness. The 0.25 standardized transition is fixed after the preserved squared-like failure and before rerun; it is not selected from a successful outcome.",
        "robust_f_scale_sensitivity_standardized": [0.125, 0.25, 0.5],
        "robust_f_scale_sensitivity_provenance": "The half/primary/double standardized transition bracket was registered before any soft_l1 positive rerun. Every value must execute the real center owner and report identifiability, endpoint and informed-projection error, total-covariance coverage, and informed/total covariance stability. The primary remains 0.25 regardless of outcomes.",
        "gyro_stochastic_uncertainty_provenance": "The real center owner consumes each endpoint's immutable P1 gyro observation and bias covariance. Savitzky-Golay kernels and the registered serial-correlation spectral envelope apply only to filtered white gyro observation and derivative noise. Constant bias, the 30-second bias-drift state, and 1.2-percent scale/cross-axis states use signed Jacobian covariance push-forward without the white-noise multiplier or spectral envelope. Their rowwise J C J^T diagonal marginal is used only for robust standardization; their authoritative posterior contribution is a nonshrinking low-rank systematic push-forward and never repeatable episode information.",
        "accelerometer_calibration_nuisance_provenance": "Initial still cannot separate gravity direction/magnitude from accelerometer bias, drift, scale factor, or cross-axis error, so no accelerometer correction is estimated or subtracted. A deliberately broad zero-mean 0.25 m/s^2 unresolved-bias design prior, a 0.003 m/s^3 slow-drift rate over the same prefit 30-second nuisance horizon, and 1.2-percent independent accelerometer plus explicit within-node accelerometer-gyro shared 3x3 scale/cross-axis blocks are frozen before any rerun after the omission was found. These are conservative modeling envelopes, not JY61P truth and not selected from POSITIVE_00. Half/primary/double covariance sensitivity plus exact-zero omission and white-envelope-contamination mutations must execute at one fixed owner-selected parameter/Jacobian point. Filter kernels and the white-noise serial envelope apply only to independent accelerometer observation noise; coherent calibration J C J^T enters a diagonal marginal only for robust standardization and a separate nonshrinking low-rank systematic posterior push-forward, never repeatable episode information or a per-action profile.",
        "accelerometer_bias_or_gravity_estimation_policy": "INITIAL_STILL_ACCELEROMETER_MEAN_REMAINS_GRAVITY_CONFOUNDED_LOW_INFORMATION;NO_BIAS_POINT_ESTIMATE_OR_GRAVITY_TRUTH",
        "matched_preprocessing_provenance": "Within each already-registered contiguous gap/boot-safe span, the inherited 21-sample order-3 Savitzky-Golay polynomial supplies zeroth-order accelerometer and gyro observations and the matching gyro derivative. Raw accelerometer with smoothed angular acceleration is forbidden because it creates a deterministic bandwidth mismatch. Immutable P1 accelerometer/gyro covariance is not divided by the smoothing gain; induced row correlation remains owned by complete 200-row cluster-robust blocks.",
        "pair_clock_center_provenance": "The persistent pair-clock offset is defined at fixed parent physical time: a positive offset evaluates the child observation later. Feasible-GLS timing variance is the square of the signed, block-local derivative of the full corrected child norm times the persistent clock variance. The systematic nuisance push-forward uses that signed derivative; an unsigned parent/child acceleration magnitude is forbidden as a nuisance direction.",
        "center_block_support_provenance": "The six-coordinate local center requires two complete one-second gap-safe blocks per parameter, twelve total, before an update. Blocks are cut inside contiguous spans and a gap never creates a block. Cluster-robust meat treats every complete block as one correlation cluster.",
        "human_worn_center_floor_sensitivity_m": [0.015, 0.030, 0.060],
        "nullspace_prior_provenance": "One-metre broad numerical prior keeps robust-bread null directions uncertain.",
        "nullspace_prior_sensitivity_m": [0.5, 1.0, 2.0],
        "multistart_provenance": "Seel-style norm-position objectives do not guarantee a unique solution. One zero start plus twelve result-independent uniform starts in half the numerical guard box expose weak objectives. A competing boundary or remote basin makes the entire local action a no-update; an interior candidate is never selected merely by discarding a lower-cost boundary candidate.",
        "identifiability_provenance": "Gap-safe retained spans are partitioned into complete 200-row blocks; a gap only terminates a block and never creates eligibility. Update requires at least two starts and at least 75 percent of interior starts within 50 mm of one basin, no boundary or remote basin within 5 percent plus 1e-6 normalized cost, robust-bread condition at most 1e4, informed eigenvalue at least 1/(0.15 m)^2, informed observation sigma at most 0.15 m, and an identifiable result-independent 60/40 chronological block-prefix whose held-in residual is at most three preregistered robust sigmas. Prefix-to-full center delta and the full-fit held-in residual are future-informed diagnostics only and never enter pass or relax the absolute prefix gate.",
        "identifiability_sensitivity": {
            "competitive_relative_cost_tolerance": [0.01, 0.05, 0.10],
            "interior_basin_radius_m": [0.025, 0.05, 0.10],
            "minimum_consistent_start_fraction": [0.60, 0.75, 0.90],
            "maximum_condition_number": [1e3, 1e4, 1e5],
            "maximum_informed_observation_sigma_m": [0.075, 0.15, 0.30],
            "maximum_prefix_heldin_residual_robust_sigma": [2.0, 3.0, 5.0]
        },
        "multistart_sensitivity": {
            "random_multistarts": [6, 12, 24],
            "initial_fraction_of_coordinate_bound": [0.25, 0.5, 0.75],
        },
        "coherent_nuisance_refit_audit": {
            "schema": "biospur-c2-center-coherent-nuisance-refit-audit-settings-v1",
            "fixed_direction_seed": 2026082904,
            "direction_count": 18,
            "standard_deviation_scale": 1.0,
            "whitened_direction_design": "SEEDED_ORTHONORMAL_UNIT_MAHALANOBIS_DIRECTIONS_CYCLED_AFTER_FULL_DIMENSIONAL_SPAN",
            "whitened_direction_semantics": "DETERMINISTIC_UNIT_RADIUS_STRESS_ON_THE_REGISTERED_1SIGMA_COVARIANCE_ELLIPSOID;NOT_AN_N0C_DRAW;NOT_REGISTERED_PER_COORDINATE_MARGINAL_COVARIANCE",
            "required_whitened_radius": 1.0,
            "components": [
                "ACCELEROMETER_BIAS_DRIFT",
                "ACCELEROMETER_SCALE_CROSS_AXIS",
                "GYRO_BIAS_DRIFT",
                "GYRO_SCALE_CROSS_AXIS",
                "SHARED_ACCELEROMETER_GYRO_SCALE_CROSS_AXIS",
                "PERSISTENT_PAIR_CLOCK",
                "HUMAN_WORN_CENTER_MIGRATION",
            ],
            "refit_start_policy": "FROZEN_NOMINAL_SELECTED_INTERIOR_BASIN",
            "antithetic_signs": [-1, 1],
            "maximum_full_scale_candidate_displacement_m": 0.05,
            "maximum_antithetic_ensemble_midpoint_shift_m": 0.05,
            "branch_switch_radius_m": 0.05,
            "boundary_failure_or_branch_switch_consequence": "LOCAL_NO_UPDATE_WITH_ALL_REFITS_RETAINED",
            "nominal_ineligible_policy": "NOT_RUN_ALREADY_LOCAL_NO_UPDATE",
            "provenance": "Each stress is one unit-Mahalanobis-radius direction on the already registered accelerometer, gyro, shared-scale, persistent-clock, or 30 mm human-worn covariance ellipsoid. Eighteen seeded orthonormal/cycled whitened directions fully span the largest 18-dimensional block; lower-dimensional blocks are fully spanned before directions cycle. These are deterministic directional stresses, not N(0,C) samples and not per-coordinate marginal-covariance draws. Both 50 mm limits and the branch radius reuse the pre-existing result-independent interior_basin_radius_m; no value is selected from the six positive outcomes. Every direction is gated separately and its antithetic midpoint tests even-order nonlinear bias that first-order J C J^T cannot expose.",
            "human_worn_perturbation_owner": "GAP_SAFE_K_OMEGA_ALPHA_TIMES_FIXED_30_MM_CENTER_MIGRATION_DIRECTION",
            "clock_perturbation_owner": "CHILD_LATER_AT_FIXED_PARENT_TIME_WITH_ONE_SIGN_SYMMETRIC_INTERIOR_MASK_PER_COMPLETE_GAP_SAFE_CLUSTER;ALL_ALIGNED_FIELDS_SUBSET;NO_ENDPOINT_CLAMP_OR_CROSS_BLOCK_INTERPOLATION",
            "clock_group_scaling": "UNIT_WHITENED_DIRECTION_MAPPED_THROUGH_THE_DIAGONAL_REGISTERED_PAIR_CLOCK_GROUP_SIGMAS",
        },
        "robust_covariance": "SOFT_L1_SANDWICH_IN_STANDARDIZED_RESIDUAL_COORDINATES;NO_SECOND_ROBUST_SIGMA_SQUARING",
    })
    settings["calibration_posterior"] = {
        "schema": "biospur-c2-capture-wide-calibration-posterior-settings-v1",
        "authority_amendment_relative_path": (
            "logs/c2_basis_progressive_20260829T102836Z/"
            "USER_CALIBRATION_POSTERIOR_AMENDMENT_001.json"
        ),
        "authority_amendment_sha256": (
            "3465eb0705d0e308fbbd0f0d48f363917d007437b6754404483474c93c4960c8"
        ),
        "mixture_component_count": 5,
        "initial_component_weights": [0.40, 0.15, 0.15, 0.15, 0.15],
        "accelerometer_bias_sigma_mps2": float(
            settings["joint_center"]["accelerometer_unresolved_bias_sigma_mps2"]
        ),
        "accelerometer_bias_drift_rate_sigma_mps3": float(
            settings["joint_center"]["accelerometer_bias_drift_rate_sigma_mps3"]
        ),
        "gyroscope_bias_drift_rate_sigma_rad_s2": 0.0002,
        "accelerometer_scale_cross_axis_fraction_sigma": float(
            settings["joint_center"]["accelerometer_scale_cross_axis_fraction_sigma"]
        ),
        "gyroscope_scale_cross_axis_fraction_sigma": float(
            settings["joint_center"]["gyro_scale_cross_axis_fraction_sigma"]
        ),
        "initial_still_gravity_mps2": 9.80665,
        "initial_still_norm_observation_sigma_mps2": 0.12,
        "maximum_rest_rows_per_episode": 64,
        "minimum_component_weight": 1e-6,
        "maximum_calibration_matrix_condition_number": 1.20,
        "unknown_boot_bias_increment_sigma_mps2": 0.05,
        "unknown_boot_gyro_bias_increment_sigma_rad_s": 0.005,
        "class_c_center_rule": (
            "FINITE_INTERIOR_BASIN_WITH_NONEMPTY_INFORMED_SUBSPACE_IS_"
            "MARGINALIZED;INFORMATION_MAGNITUDE_OR_CALIBRATION_REFIT_"
            "FRAGILITY_WIDENS_COVARIANCE_AND_RETAINS_MIXTURE"
        ),
        "class_c_axis_rule": (
            "OFFICIAL_QMT_SELECTION_SUPPORT_RANK_AND_CURVATURE_REMAIN_REQUIRED;"
            "INCOMPLETE_CALIBRATION_NUISANCE_POINT_IDENTIFIABILITY_WIDENS_"
            "COVARIANCE_WITHOUT_GLOBAL_NO_UPDATE"
        ),
        "fixed_decode_and_units": True,
        "per_action_profiles_allowed": False,
        "backward_smoothing_allowed": False,
        "latent_synthetic_truth_allowed": False,
        "positive_qualification_target": (
            "PHYSICAL_TRAJECTORY_AND_POSTERIOR_COVERAGE_NOT_INDEPENDENT_"
            "POINT_RECOVERY_OF_EVERY_NUISANCE"
        ),
    }
    if (
        float(settings["joint_center"]["robust_f_scale_standardized"]) != 0.25
        or [
            float(value)
            for value in settings["joint_center"][
                "robust_f_scale_sensitivity_standardized"
            ]
        ]
        != [0.125, 0.25, 0.5]
    ):
        raise RuntimeError("center robust f-scale primary/sensitivity design changed")
    joint_center = settings["joint_center"]
    coherent_refit = joint_center["coherent_nuisance_refit_audit"]
    if (
        int(joint_center["local_parameter_dimension"]) != 6
        or int(joint_center["minimum_complete_blocks_for_update"])
        != int(joint_center["local_parameter_dimension"])
        * int(joint_center["minimum_complete_blocks_per_parameter"])
        or int(joint_center["feasible_gls_gyro_reweight_passes"]) != 2
        or joint_center["matched_gap_local_savgol_acc_gyro_alpha_preprocessing"] is not True
        or joint_center["savgol_serial_correlation_policy"]
        != "SOURCE_SEPARATED_WHITE_OBSERVATION_DERIVATIVE_FIXED_KERNEL_SPECTRAL_VARIANCE_ENVELOPE_PLUS_COMPLETE_RAW_BLOCK_CLUSTER_SANDWICH;SHARED_CALIBRATION_J_C_JT_EXCLUDES_WHITE_ENVELOPE"
        or joint_center["shared_calibration_row_weight_role"]
        != "SIGNED_J_C_JT_DIAGONAL_MARGINAL_FOR_ROBUST_STANDARDIZATION_ONLY_NOT_INDEPENDENT_EPISODE_INFORMATION"
        or joint_center["shared_calibration_posterior_role"]
        != "SIGNED_LOW_RANK_SYSTEMATIC_PUSHFORWARD_NONSHRINKING_ACROSS_EPISODES"
        or float(joint_center["shared_calibration_white_noise_multiplier"]) != 1.0
        or float(joint_center[
            "shared_calibration_serial_correlation_variance_envelope_multiplier"
        ]) != 1.0
        or joint_center["pair_clock_offset_convention"]
        != "POSITIVE_OFFSET_EVALUATES_CHILD_LATER_AT_FIXED_PARENT_PHYSICAL_TIME"
        or joint_center["pair_clock_systematic_jacobian"]
        != "SIGNED_NEGATIVE_BLOCK_LOCAL_TIME_DERIVATIVE_OF_FULL_CORRECTED_CHILD_NORM"
        or [
            float(value)
            for value in joint_center[
                "gyro_stochastic_covariance_sensitivity_multipliers"
            ]
        ]
        != [0.25, 1.0, 4.0]
        or coherent_refit["antithetic_signs"] != [-1, 1]
        or int(coherent_refit["direction_count"]) != 18
        or float(coherent_refit["standard_deviation_scale"]) != 1.0
        or coherent_refit["whitened_direction_design"]
        != "SEEDED_ORTHONORMAL_UNIT_MAHALANOBIS_DIRECTIONS_CYCLED_AFTER_FULL_DIMENSIONAL_SPAN"
        or float(coherent_refit["required_whitened_radius"]) != 1.0
        or float(coherent_refit["maximum_full_scale_candidate_displacement_m"])
        != float(joint_center["interior_basin_radius_m"])
        or float(coherent_refit["maximum_antithetic_ensemble_midpoint_shift_m"])
        != float(joint_center["interior_basin_radius_m"])
        or float(coherent_refit["branch_switch_radius_m"])
        != float(joint_center["interior_basin_radius_m"])
        or coherent_refit["clock_perturbation_owner"]
        != "CHILD_LATER_AT_FIXED_PARENT_TIME_WITH_ONE_SIGN_SYMMETRIC_INTERIOR_MASK_PER_COMPLETE_GAP_SAFE_CLUSTER;ALL_ALIGNED_FIELDS_SUBSET;NO_ENDPOINT_CLAMP_OR_CROSS_BLOCK_INTERPOLATION"
    ):
        raise RuntimeError("center gyro-stochastic support/reweight design changed")
    hinge_axis.update({
        "accelerometer_unresolved_bias_sigma_mps2": float(
            joint_center["accelerometer_unresolved_bias_sigma_mps2"]
        ),
        "accelerometer_bias_drift_rate_sigma_mps3": float(
            joint_center["accelerometer_bias_drift_rate_sigma_mps3"]
        ),
        "accelerometer_bias_drift_horizon_s": float(
            joint_center["accelerometer_bias_drift_horizon_s"]
        ),
        "accelerometer_scale_cross_axis_fraction_sigma": float(
            joint_center["accelerometer_scale_cross_axis_fraction_sigma"]
        ),
        "gyro_scale_cross_axis_fraction_sigma": float(
            joint_center["gyro_scale_cross_axis_fraction_sigma"]
        ),
        "accelerometer_gyro_shared_scale_cross_axis_fraction_sigma": float(
            joint_center[
                "accelerometer_gyro_shared_scale_cross_axis_fraction_sigma"
            ]
        ),
        "gyro_bias_drift_correlation_time_s": float(
            joint_center["gyro_bias_drift_correlation_time_s"]
        ),
        "calibration_nuisance_covariance_multiplier": 1.0,
        "accelerometer_calibration_nuisance_multiplier": 1.0,
        "gyro_calibration_nuisance_multiplier": 1.0,
        "calibration_nuisance_covariance_sensitivity_multipliers": [
            0.25, 1.0, 4.0
        ],
        "calibration_nuisance_ensemble_replicates": 6,
        "calibration_nuisance_ensemble_seed": 20260907,
        "linearization_validation_replicates": 1,
        "linearization_score_hessian_step_rad": 1e-5,
        "linearization_score_hessian_step_sensitivity_rad": [5e-6, 1e-5, 2e-5],
        "exact_score_hessian_required_rank": 4,
        "exact_score_hessian_maximum_condition_number": 1e7,
        "exact_score_hessian_step_gate": (
            "EVERY_REGISTERED_CENTRAL_DIFFERENCE_STEP_MUST_BE_FINITE_FULL_RANK_"
            "AND_POSITIVE_CURVATURE;CONDITION_LIMIT_EQUALS_THE_INVERSE_OF_THE_"
            "PREREGISTERED_1E-7_RELATIVE_RANK_TOLERANCE"
        ),
        "exact_score_hessian_rank_curvature_provenance": (
            "The implicit nuisance response has four spherical-chart parameters. "
            "All four exact-score curvature directions must be finite, positive, "
            "and retained at each of the result-independent 0.5x/1x/2x central-"
            "difference steps. The 1e7 condition cap is algebraically paired with "
            "the pre-existing 1e-7 relative-rank tolerance, not selected from a "
            "synthetic outcome. Canonical JtJ and the optimizer raw-chart Hessian "
            "remain diagnostics and cannot authorize inversion."
        ),
        "linearization_validation_nuisance_scales": [0.02, 1.0],
        "linearization_validation_required_scales": [1.0],
        "linearization_validation_absolute_tolerance_rad": 0.002,
        "linearization_validation_relative_tolerance": 0.35,
        "linearization_validation_minimum_informative_response_rad": 1e-5,
        "linearization_validation_minimum_endpoint_dot": float(
            np.cos(np.deg2rad(20.0))
        ),
        "centered_selection_transform": (
            "COMPONENTWISE_MEDIAN_CENTERED_ACC_GYRO;STATIC_ADDITIVE_BIAS_"
            "ANNIHILATED;DRIFT_USES_CENTERED_PHYSICAL_TIME;SCALE_CROSS_AXIS_"
            "USES_CENTERED_SIGNALS_NOT_REMOVED_GRAVITY_OR_MEAN"
        ),
        "fixed_point_systematic_push_forward": (
            "OFFICIAL_QMT_OLSSON_COST_GRADIENT_AND_CENTRAL_DIFFERENCE_SCORE_"
            "HESSIAN_AT_PRIMARY_XHAT;SIGNED_ANTITHETIC_COHERENT_NUISANCE;PRODUCT_S2_"
            "TANGENT;NO_PER_ACTION_PROFILE_OR_INDEPENDENT_ROW_VOTES"
        ),
        "observation_covariance_push_forward": (
            "IMMUTABLE_P1_ACC_GYRO_OBSERVATION_INCLUDING_QUANTIZATION;ONE_"
            "COHERENT_DRAW_PER_COMPLETE_SELECTED_GAP_SAFE_BLOCK"
        ),
        "linearization_validation_provenance": (
            "Before any comparison, one result-independent draw per covariance "
            "component is checked at both two percent and the mandatory primary one-"
            "sigma scale against actual official plus/minus refits from the fixed "
            "primary xhat. The frozen 0.002 rad absolute and 35 percent relative "
            "limits are both required at primary scale, and a primary refit response "
            "below 1e-5 rad is noninformative rather than an automatic pass. This is "
            "a bounded numerical linearization check, not a "
            "replacement estimator or result-selected parameter. A failed or branch-"
            "switched refit forces local no-update and remains diagnostic."
        ),
        "calibration_nuisance_provenance": (
            "The hinge owner shares the immutable prefit accelerometer/gyro nuisance "
            "model with the center owner. Static offsets cancel only in centered "
            "support selection; the full gravity-confounded accelerometer bias and "
            "gyro bias remain coherent inputs to the official QMT fixed-point "
            "systematic push-forward. Half/primary/double covariance sensitivity is "
            "registered before qualification and common-mode nuisance never becomes "
            "repeatable per-row or per-episode information."
        ),
    })
    shared_axis_center_keys = (
        "accelerometer_unresolved_bias_sigma_mps2",
        "accelerometer_bias_drift_rate_sigma_mps3",
        "accelerometer_bias_drift_horizon_s",
        "accelerometer_scale_cross_axis_fraction_sigma",
        "gyro_scale_cross_axis_fraction_sigma",
        "accelerometer_gyro_shared_scale_cross_axis_fraction_sigma",
        "gyro_bias_drift_correlation_time_s",
    )
    if (
        any(
            float(hinge_axis[key]) != float(joint_center[key])
            for key in shared_axis_center_keys
        )
        or [
            float(value)
            for value in hinge_axis[
                "calibration_nuisance_covariance_sensitivity_multipliers"
            ]
        ] != [0.25, 1.0, 4.0]
        or int(hinge_axis["calibration_nuisance_ensemble_replicates"]) < 4
        or int(hinge_axis["linearization_validation_replicates"]) < 1
        or [
            float(value)
            for value in hinge_axis["linearization_validation_nuisance_scales"]
        ] != [0.02, 1.0]
        or [
            float(value)
            for value in hinge_axis["linearization_validation_required_scales"]
        ] != [1.0]
    ):
        raise RuntimeError("hinge calibration nuisance ownership changed")
    settings["geometry_progressive"] = {
        "center_minimum_informed_variance_m2": 1e-8,
        "center_initial_unobserved_sigma_m": 1.0,
        "center_temporal_diffusion_m2_s": 9e-6,
        "axis_temporal_diffusion_rad2_s": float(np.deg2rad(0.2) ** 2),
        "axis_transport_step_rad": 1e-5,
        "axis_antipodal_cosine_threshold": float(np.cos(np.deg2rad(160.0))),
        "axis_antipodal_no_update_sigma_rad": float(np.deg2rad(90.0)),
        "center_unknown_interval_floor_m2": 0.03**2,
        "axis_unknown_interval_floor_rad2": float(np.deg2rad(5.0) ** 2),
        "ownership": "Statistical information accumulates; migration diffuses; shared floors do not shrink. Product-S2 covariance is transported and antipodal logs are uncertainty-inflating no-updates.",
        "parameter_provenance": {
            "center_temporal_diffusion": "Prefit 3 mm/sqrt(s) center migration sensitivity.",
            "axis_temporal_diffusion": "Prefit 0.2 deg/sqrt(s) axis migration sensitivity.",
            "axis_antipodal_threshold": "At or beyond 160 degrees the S2 log is treated as ill-conditioned.",
            "unknown_interval_floors": "A timer reset/boot/unknown boundary has no fabricated duration; one conservative 30 mm center and 5 degree axis migration floor is applied to existing state.",
        },
        "parameter_sensitivity": {
            "center_temporal_diffusion_m2_s": [2.25e-6, 9e-6, 3.6e-5],
            "axis_temporal_diffusion_deg2_s": [0.01, 0.04, 0.16],
            "axis_antipodal_threshold_deg": [150.0, 160.0, 170.0],
            "center_unknown_interval_floor_m": [0.015, 0.030, 0.060],
            "axis_unknown_interval_floor_deg": [2.5, 5.0, 10.0],
        },
    }

    settings["segment_frames"] = {
        "wear_authority": _wear_authority(root),
        "wear_prior": {"family": "BROAD_NON_COMPACT_QUALITATIVE", "hard_cone_deg": None},
        "wear_uncertainty": {
            "primary_direction_sigma_rad": float(np.deg2rad(55.0)),
            "sensitivity_direction_sigmas_rad": [float(value) for value in np.deg2rad([40.0, 55.0, 70.0])],
            "near_uninformative_hemisphere_sigma_rad": float(np.pi / 2.0),
            "gross_wrong_hemisphere_margin_rad": float(np.deg2rad(10.0)),
            "gross_guard_sigma_multiplier": 3.0,
            "prior_vs_systematic_ownership": "Wear is a broad full-support branch likelihood, not irreducible covariance; only functionally unidentified directions remain broad.",
        },
        "bilateral_hard_mirror": False,
        "knee_direction_deadband_rad": float(np.deg2rad(10.0)),
        "nonshrinking_soft_tissue_strap_frame_sigma_rad": float(np.deg2rad(3.0)),
        "unidentified_direction_sigma_rad": float(np.deg2rad(60.0)),
        "numerical_jacobian_center_step_m": 1e-5,
        "numerical_jacobian_axis_step_rad": 1e-5,
        "known_rotation_covariance_absolute_tolerance": 1e-7,
        "parameter_provenance": {
            "frame_systematic_floor": "Separate three-degree soft-tissue/strap sensitivity; not the 55-degree wear prior.",
            "unidentified_direction_sigma": "Sixty-degree uncertainty only for reported weak/gauge functional directions.",
            "jacobian_steps": "Central differences above floating-point noise and below physical scales.",
        },
        "parameter_sensitivity": {
            "frame_systematic_floor_deg": [1.5, 3.0, 6.0],
            "unidentified_direction_sigma_deg": [30.0, 60.0, 90.0],
            "numerical_jacobian_step_scale": [0.5, 1.0, 2.0],
            "wear_profiles_deg": [40.0, 55.0, 70.0],
        },
    }

    settings["heading"] = {
        "method": "OFFICIAL_QMT_0_2_4_HEADINGCORRECTION_OBSERVATIONS_PLUS_PERSISTENT_EDGE_POSTERIOR",
        "input_order_gate": "POSTERIOR_FUNCTIONAL_SEGMENT_FRAME_BRANCHES_REQUIRED_FIRST",
        "joint_construction": {
            "hinge_edges": "posterior functional axes transformed through each retained segment-frame branch",
            "nonhinge_edges": "three-DoF identity basis of posterior functional segment frames",
            "joint_info_3d": {"convention": "xyz", "angle_ranges_rad": [[-np.pi, np.pi], [-np.pi, np.pi], [-np.pi, np.pi]]},
            "action_label_pose_or_rom_truth_used": False,
            "anthropometric_proxy_used": False,
        },
        "explicit_est_settings": {
            "useRomConstraints": False,
            "windowTime": 8.0,
            "estimationRate": 1.0,
            "dataRate": 5.0,
            "tauDelta": 5.0,
            "tauBias": 5.0,
            "ratingMin": 0.4,
            "alignment": "backward",
            "enableStillness": True,
            "optimizerSteps": 5,
            "stillnessTime": 3.0,
            "stillnessThreshold_rad_s": float(np.deg2rad(4.0)),
            "stillnessRating": 0.0,
            "startRating": 0.0,
            "constraint_by_dof": {
                "1": "euler_1d",
                "2": "euler",
                "3": "default",
            },
        },
        "default_provenance": "QMT 0.2.4 defaults explicit; example-subject alignment/sign/ROM/perfect startup ratings are not imported.",
        "example_subject_startRating_1_or_stillnessRating_1_imported": False,
        "window_and_threshold_sensitivity": {
            "windowTime_s": [4.0, 8.0, 12.0], "estimationRate_hz": [0.5, 1.0, 2.0],
            "dataRate_hz": [5.0, 10.0], "tauDelta_s": [2.5, 5.0, 10.0],
            "tauBias_s": [2.5, 5.0, 10.0], "ratingMin": [0.2, 0.4, 0.6],
            "stillnessTime_s": [2.0, 3.0, 5.0], "stillnessThreshold_deg_s": [2.0, 4.0, 8.0],
            "optimizerSteps": [3, 5, 8],
        },
        "persistent_filter": {
            "initial_relative_heading_sigma_rad": float(np.pi),
            "gap_diffusion_rad2_s": float(np.deg2rad(0.5) ** 2),
            "unknown_interval_variance_floor_rad2": float(np.deg2rad(5.0) ** 2),
            "within_span_diffusion_rad2_s": float(np.deg2rad(0.1) ** 2),
            "rating_variance_floor_rad2": float(np.deg2rad(5.0) ** 2),
            "rating_variance_scale_rad2": float(np.deg2rad(30.0) ** 2),
            "minimum_effective_rating": 0.4,
            "official_state_information_multiplier": {"0": 1.0, "1": 1.0, "2": 0.0, "3": 0.0},
            "frame_tangent_variance_scale": 1.0,
            "official_estimation_epochs_only": True,
            "interpolated_200hz_rows_are_new_evidence": False,
        },
        "branch_evidence": {
            "rating_log1p_scale": 1.0,
            "effective_epoch_cap_per_edge": 16,
            "provenance": "One-second official epochs capped above typical independent action support.",
            "sensitivity_epoch_cap": [8, 16, 24],
        },
        "span_state_carry": "One edge/branch posterior crosses all spans/actions; gaps are no-update diffusion, never reset or profile stitching.",
        "tree_semantics": "delta_child_global = delta_parent_global + deltaFilt_child_edge",
        "pelvis_yaw_gauge_rad": 0.0,
        "bespoke_global_solver": "FORBIDDEN",
    }

    settings["physical_candidates"] = {
        "trajectory_sample_quantiles": [0.0, 0.25, 0.5, 0.75, 1.0],
        "maximum_pair_projection_time_error_s": 0.00251,
        "root_clock_sigma_s": 0.005,
        "hard_guard_sigma_multiplier": 3.0,
        "bilateral_crossing": {"gross_crossing_margin_m": 0.03, "gross_sustained_evidence_fraction": 0.6, "soft_likelihood_sigma_m": 0.05},
        "gross_gravity_wrong_hemisphere_margin_rad": float(np.deg2rad(10.0)),
        "gravity_evidence": {
            "hard_upright_segments": ["pelvis", "torso"],
            "gross_sustained_evidence_fraction": 0.6,
            "soft_likelihood_dot_sigma": 0.25,
            "limb_opposite_hemisphere_is_diagnostic_only": True,
        },
        "bilateral_knee_minimum_signed_flexion_rad": float(np.deg2rad(10.0)),
        "bilateral_knee_opposition_minimum_fraction": 0.6,
        "rom_soft_reference_rad": float(np.deg2rad(150.0)),
        "rom_soft_sigma_rad": float(np.deg2rad(30.0)),
        "orientation_uncertainty": {
            "gyro_white_noise_multiplier": 1.0,
            "initial_bias_correlation_time_s": 30.0,
            "vqf_residual_bias_correlation_time_s": 10.0,
            "accelerometer_tilt_sensitivity_rad_per_mps2": 1.0 / 9.80665,
            "gyro_scale_cross_axis_fraction_sigma": 0.012,
            "clock_timing_sigma_multiplier": 1.0,
        },
        "parameter_provenance": {
            "sample_quantiles": "Five result-independent samples on the sealed pelvis base grid.",
            "bilateral_margin": "Three-centimetre gross contradiction margin separate from uncertainty and transient soft evidence.",
            "sustained_fraction": "Three of five preregistered samples required for a gross contradiction.",
            "gravity_scope": "Only pelvis/torso axial segments may hard-gate; limb axes are motion-dependent.",
            "rom": "Broad 150-degree soft reference; no action-specific profile and no hard ROM cone.",
        },
        "parameter_sensitivity": {
            "gross_crossing_margin_m": [0.015, 0.03, 0.06], "sustained_fraction": [0.4, 0.6, 0.8],
            "hard_guard_sigma_multiplier": [2.0, 3.0, 5.0], "rom_reference_deg": [120.0, 150.0, 180.0],
            "rom_sigma_deg": [15.0, 30.0, 60.0],
        },
    }

    state_layout = [
        {"kind": "CENTER", "edge": edge, "dimension": 6} for edge, _, _ in EDGE_SPECS
    ] + [
        {"kind": "AXIS_PRODUCT_S2_TANGENT", "edge": edge, "dimension": 4} for edge in HINGE_EDGES
    ]
    branch_ids = list(canonical_hinge_sign_branch_ids())
    settings["progressive"] = {
        "state_layout": state_layout, "state_dimension": 70,
        "normalization": {"center_coordinate_scale_m": 0.1, "axis_tangent_scale_rad": float(np.deg2rad(10.0))},
        "initial_sigma": 10.0, "branch_ids": branch_ids, "real_branch_count": len(branch_ids),
        "synthetic_branch_count": 2, "rank_relative_tolerance": 1e-8,
        "local_information_relative_tolerance": 1e-8, "fresh_absolute_tolerance": 1e-8,
        "fresh_relative_tolerance": 1e-6, "renderer_branch_display_count": 4,
        "physical_validity_weights": {
            "geometry_information_fraction": 0.4,
            "official_heading_effective_support_fraction": 0.3,
            "trajectory_legal_branch_fraction": 0.3,
        },
        "information": "DATA_ONLY_GAUGE_REDUCED_RANK_NONZERO_SPECTRUM_AND_PSEUDOLOGDET_SEPARATE_FROM_PRIOR",
        "uncertainty": "OWNER_DERIVED_MEASUREMENT_PLUS_TEMPORAL_MIGRATION_PLUS_SHARED_SYSTEMATIC_TOTAL",
        "prediction": "PREQUENTIAL_SCORE_ON_OWNER_INFORMED_PROJECTION_BEFORE_CURRENT_EPISODE_INGEST",
        "fresh_replay_scope": "SYNTHETIC_SUFFICIENT_STAT_REPLAY_ONLY_NOT_FINAL_RAW_FRESH_BATCH",
        "parameter_provenance": {
            "initial_sigma": "Broad normalized-state prior excluded from data-information rank.",
            "rank_tolerance": "Relative eigenspectrum tolerance for gauge-reduced data information.",
            "fresh_tolerances": "Numerical equivalence tolerances for deterministic frozen-source reruns.",
            "physical_validity_weights": "Prefit convex reporting composition, not a fit residual or readiness gate.",
        },
        "parameter_sensitivity": {
            "initial_sigma": [5.0, 10.0, 20.0], "rank_relative_tolerance": [1e-9, 1e-8, 1e-7],
            "fresh_absolute_tolerance": [1e-9, 1e-8, 1e-7], "fresh_relative_tolerance": [1e-7, 1e-6, 1e-5],
        },
    }
    settings["viewer"].update({
        "scene_timestamp_rule": "ONE_COMMON_PELVIS_ROOT_PHYSICAL_REFERENCE_MAPPED_THROUGH_PERSISTENT_PAIR_CLOCK_STATE",
        "independent_per_node_normalized_midpoint_allowed": False,
        "clock_mapping_uncertainty_visible": True,
        "scientific_geometry_source": "POSTERIOR_FUNCTIONAL_FRAMES_AND_TWO_SENSOR_TO_JOINT_VECTORS_ONLY",
    })
    settings["scientific_renderer"] = {
        "schema": "biospur-c2-registered-scientific-renderer-settings-v1",
        "checkpoint_actions": [
            "00_initial_still",
            "04_shoulder_left",
            "08_hip_left",
            "09_hip_right",
            "16_squat",
            "17_final_still",
        ],
        "sample_quantiles": [0.0, 0.5, 1.0],
        "front_axes": ["y", "z"],
        "side_axes": ["x", "z"],
        "top_axes": ["x", "y"],
        "horizontal_limits_m": [-1.2, 1.2],
        "vertical_limits_m": [-1.25, 1.25],
        "equal_aspect": True,
        "figure_size_inches": [15.0, 5.6],
        "dpi": 150,
        "line_width": 2.0,
        "marker_size": 18.0,
        "joint_marker_size": 28.0,
        "segment_sensor_origin_color": "#d97706",
        "functional_joint_center_color": "#15803d",
        "parent_sensor_to_joint_color": "#1f4e79",
        "child_sensor_to_joint_color": "#7c3aed",
        "renderer_branch_display_count": int(
            settings["progressive"]["renderer_branch_display_count"]
        ),
        "camera_and_timestamp_policy": "IDENTICAL_FRONT_SIDE_TOP_CAMERAS_AND_ONE_OWNER_PHYSICAL_TIMESTAMP_PER_TRIVIEW",
        "geometry_policy": "DIRECT_TWO_SENSOR_TO_JOINT_SCIENTIFIC_FK;NO_IK_REBASE_REPAIR_OR_ANTHROPOMETRIC_SUBSTITUTION",
        "status_label": "FUNCTIONAL-GEOMETRY PROXY / NOT ANATOMICAL / NOT PASS",
        "source_label_policy": {
            "official_qmt_source": "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_CORRECTED_CURRENT_SEALED_ORIENTED_ACTION",
            "synthetic_fixture_source": "SYNTHETIC_RENDERER_PIXEL_SMOKE_ONLY",
            "official_qmt_label": "OFFICIAL TIME-VARYING QMT ROOTED TRAJECTORY",
            "synthetic_fixture_label": "SYNTHETIC RENDERER FIXTURE / NOT OFFICIAL QMT",
            "official_label_requires_manifest_fresh_verification_pass": True,
            "official_label_required_fresh_verification_schema": (
                "biospur-c2-final-raw-independent-frozen-owner-comparison-v1"
            ),
            "official_label_required_fresh_scope": "FINAL_RAW_RANGE_FULL_FROZEN_PIPELINE",
            "official_label_requires_all_comparisons_and_causal_prefix_pass": True,
            "official_label_requires_distinct_reader_sessions": True,
            "unknown_source_rejected": True,
        },
    }
    settings["heldout_evaluation"] = {
        "schema": "biospur-c2-registered-frozen-heldout-evaluation-v1",
        "authoritative_owner": (
            "biospur_fusion.v0.c2_progressive.heldout_evaluation."
            "FrozenScientificHeldoutEvaluationOwner"
        ),
        "authoritative_entrypoint": "tools/evaluate_c2_progressive_holdout.py",
        "input_health_layer_is_scientific_verdict": False,
        "continuous_orientation_reconstruction": (
            "REREAD_EXACT_TRAINING_THEN_HELDOUT_RANGES_WITH_ONE_VQF_PER_NODE;"
            "ONLY_HELDOUT_RETAINED_ROWS_ENTER_SCIENTIFIC_METRICS"
        ),
        "frozen_calibration_owners": [
            "functional_geometry",
            "segment_frame_branches",
            "persistent_pair_clock",
            "branch_ids_hard_support_and_weights",
            "progressive_posterior",
            "settings_and_thresholds",
        ],
        "evaluation_trajectory_path": [
            "CONTINUOUS_SIX_AXIS_ORIENTATION_TO_HELDOUT_ROWS",
            "FROZEN_PAIR_CLOCK_PREDICTION_WITHOUT_HELDOUT_CLOCK_REFIT",
            "OFFICIAL_QMT_TIME_VARYING_HEADING_FROM_FROZEN_EDGE_PRIOR_COPY",
            "OFFICIAL_ROOTED_NINE_EDGE_PARENT_PLUS_CHILD_DELTAFILT",
            "DIRECT_TWO_VECTOR_SCIENTIFIC_FK",
            "UNCERTAINTY_AWARE_PHYSICAL_AND_PREQUENTIAL_METRICS",
        ],
        "global_verdict_criteria": {
            "required_action_count": 19,
            "minimum_scientifically_evaluable_action_fraction": 0.80,
            "minimum_supported_branch_weight_evaluated_fraction": 0.95,
            "minimum_posterior_weighted_physical_legal_mass": 0.50,
            "minimum_official_qmt_effective_epochs": 9,
            "maximum_heading_prequential_nll_per_effective_epoch": 20.0,
            "minimum_heading_prequential_3sigma_coverage": 0.50,
            "maximum_shared_joint_closure_error_m": 1e-10,
        },
        "criterion_provenance": {
            "action_fraction": (
                "At least 16 of 19 sealed actions must traverse the complete frozen scientific path; "
                "up to three ordinary low-information actions may remain explicit no-updates."
            ),
            "branch_mass": (
                "Evaluation must cover at least 95 percent of the already-frozen hard-supported posterior mass; "
                "soft numerical underflow cannot lock a legal branch."
            ),
            "physical_legal_mass": (
                "A majority of the frozen posterior mass must remain uncertainty-aware physically legal; "
                "ROM remains soft and only registered gross structural contradictions hard-reject."
            ),
            "qmt_epochs": (
                "At least one independent official estimation epoch per rooted edge capture-wide prevents "
                "interpolated 200 Hz rows from manufacturing support."
            ),
            "heading_nll_and_coverage": (
                "Broad prefit diagnostics reject grossly incompatible frozen heading predictions without "
                "post-outcome threshold tuning; both finite NLL and uncertainty coverage are required."
            ),
            "closure": "Direct two-vector FK shared-joint closure is a numerical identity check at 1e-10 m.",
        },
        "parameter_sensitivity": {
            "minimum_scientifically_evaluable_action_fraction": [0.70, 0.80, 0.90],
            "minimum_supported_branch_weight_evaluated_fraction": [0.90, 0.95, 0.99],
            "minimum_posterior_weighted_physical_legal_mass": [0.25, 0.50, 0.75],
            "maximum_heading_prequential_nll_per_effective_epoch": [10.0, 20.0, 40.0],
            "minimum_heading_prequential_3sigma_coverage": [0.25, 0.50, 0.75],
        },
        "fit_refit_branch_reweight_threshold_override_or_feedback_allowed": False,
        "heldout_failure_causes_fit_pivot": False,
    }

    positive_scenarios = [
        {"wear_mode": "haar", "parent_dimension_m": 0.48, "child_dimension_m": 0.43, "asymmetry_fraction": 0.04, "excitation_scale": 1.0, "observation_level": 1.0, "gap_rows": 4, "duplicate_rows": 2, "jitter_us": 700, "clipping_rows": 3},
        {"wear_mode": "near_uninformative_hemisphere", "parent_dimension_m": 0.52, "child_dimension_m": 0.39, "asymmetry_fraction": -0.06, "excitation_scale": 0.8, "observation_level": 1.2, "gap_rows": 2, "duplicate_rows": 1, "jitter_us": -600, "clipping_rows": 2},
        {"wear_mode": "haar", "parent_dimension_m": 0.44, "child_dimension_m": 0.47, "asymmetry_fraction": 0.08, "excitation_scale": 1.25, "observation_level": 0.7, "gap_rows": 5, "duplicate_rows": 2, "jitter_us": 900, "clipping_rows": 1},
        {"wear_mode": "haar", "parent_dimension_m": 0.50, "child_dimension_m": 0.41, "asymmetry_fraction": -0.03, "excitation_scale": 0.65, "observation_level": 1.4, "gap_rows": 3, "duplicate_rows": 1, "jitter_us": -800, "clipping_rows": 2},
        {"wear_mode": "near_uninformative_hemisphere", "parent_dimension_m": 0.46, "child_dimension_m": 0.45, "asymmetry_fraction": 0.02, "excitation_scale": 1.1, "observation_level": 0.9, "gap_rows": 4, "duplicate_rows": 2, "jitter_us": 500, "clipping_rows": 3},
        {"wear_mode": "haar", "parent_dimension_m": 0.49, "child_dimension_m": 0.42, "asymmetry_fraction": -0.08, "excitation_scale": 0.9, "observation_level": 1.1, "gap_rows": 2, "duplicate_rows": 1, "jitter_us": -500, "clipping_rows": 2},
    ]
    for scenario in positive_scenarios:
        scenario.update({
            "duration_s": 90.0,
            "nonideal": True,
            "imperfect_rest_return": True,
        })
    settings["synthetic"].update({
        "initial_still_duration_s": 35.05,
        "initial_still_duration_provenance": (
            "P1_INITIAL_STILL_7010_ROWS_AT_REGISTERED_200_HZ_FOR_"
            "INPUT_DURATION_PLANNING_ONLY"
        ),
        "initial_still_duration_role": (
            "RESULT_INDEPENDENT_SYNTHETIC_INPUT_DESIGN_DURATION;"
            "NOT_JOINT_CENTER_COVARIANCE_HORIZON;NOT_CALIBRATION_TRUTH"
        ),
        "initial_still_latent_calibration_role": (
            "GENERATOR_ROWS_ONLY;FORBIDDEN_FROM_ESTIMATOR_INPUT_OR_POINT_"
            "CORRECTION"
        ),
        "positive_seeds": [3101, 3102, 3103, 3104, 3105, 3106],
        "positive_scenarios": positive_scenarios,
        "generator_model": {
            "rest_ramp_duration_s": 0.6,
            "rest_start_fraction": 0.08,
            "rest_final_multiplier": 0.15,
            "base_xyz_amplitude_rad": [0.30, 0.23, 0.27],
            "base_xyz_frequency_rad_s": [0.71, 1.09, 0.53],
            "base_xyz_phase_rad": [0.2, 0.7, -0.4],
            "relative_amplitude_rad": [0.85, 0.32],
            "relative_frequency_rad_s": [1.31, 2.17],
            "relative_phase_rad": [0.0, 0.5],
            "nonideal_hinge_cross_axis_deg": 3.0,
            "nonideal_hinge_axis_frequency_rad_s": [0.27, 0.31],
            "near_uninformative_wear_z": 0.01,
            "near_uninformative_parent_twist_rad": 1.7,
            "near_uninformative_child_twist_rad": -1.1,
            "strap_slip_peak_deg": 2.5,
            "strap_slip_frequency_rad_s": [0.11, 0.13],
            "strap_slip_child_phase_rad": 0.4,
            "joint_to_sensor_dimension_fraction": 0.40,
            "joint_specific_amplitude_mps2": [0.7, 0.5, 0.8],
            "joint_specific_frequency_rad_s": [0.41, 0.83, 0.57],
            "joint_specific_phase_rad": [0.0, 0.3, -0.2],
            "gravity_mps2": 9.80665,
            "center_migration_peak_m": 0.006,
            "center_migration_frequency_rad_s": [0.17, 0.19, 0.21, 0.15],
            "center_vector_under_strap_slip": "CURRENT_SENSOR_FROM_NOMINAL_SENSOR_ROTATES_THE_NOMINAL_JOINT_TO_SENSOR_VECTOR_AND_ADDED_MIGRATION",
            "scale_cross_axis_std": 0.006,
            "slow_artifact_amplitude_mps2": 0.06,
            "slow_artifact_frequency_rad_s": [0.19, 0.23, 0.17],
            "slow_artifact_phase_rad": [0.0, 0.4, 0.0],
            "child_slow_artifact_multiplier": 0.8,
            "ar1_rho_uniform": [0.0, 0.65],
            "accelerometer_noise_std_mps2": 0.025,
            "gyro_white_noise_std_rads": 0.0025,
            "gyro_static_offset_std_rads": 0.003,
            "gyro_linear_drift_bound_rads2": 0.0002,
            "provenance": "Deterministic broad pre-outcome synthetic nonideality model; sensitivity is exercised by the six registered observation-level scenarios.",
        },
        "estimator_input_noise": {
            "accelerometer_sigma_mps2": 0.025,
            "gyroscope_sigma_rads": 0.0025,
            "gyroscope_bias_sigma_rads": 0.003,
            "provenance": "Prefit synthetic estimator-input covariance, distinct from human-worn systematic floors and frozen before outcomes.",
        },
        "mutation_fixtures": {
            "center_ideal_diagnostic": {
                "seed": 9101,
                "duration_s": 30.0,
                "sample_period_s": 0.005,
                "excitation_scale": 1.0,
                "nonideal": False,
                "imperfect_rest_return": False,
                "wear_mode": "haar",
                "parent_dimension_m": 0.48,
                "child_dimension_m": 0.43,
                "asymmetry_fraction": 0.0,
                "observation_level": 0.0,
                "aligned_pair": "EXACT_ZERO_LAG_SINGLE_CONTIGUOUS_SPAN",
                "estimator_covariance_source": "REGISTERED_SYNTHETIC_ESTIMATOR_INPUT_NOISE",
                "purpose": "DIAGNOSE_GENERATOR_CENTER_EQUATION_AND_BOUNDED_LOCAL_OBJECTIVE_BEFORE_ANY_SOLVER_DESIGN_CHANGE",
            },
            "axis_linearization_diagnostic": {
                "seed": 9201,
                "duration_s": 12.0,
                "sample_period_s": 0.005,
                "excitation_scale": 1.0,
                "nonideal": False,
                "imperfect_rest_return": False,
                "wear_mode": "haar",
                "parent_dimension_m": 0.48,
                "child_dimension_m": 0.43,
                "asymmetry_fraction": 0.0,
                "observation_level": 0.0,
                "aligned_pair": "EXACT_ZERO_LAG_SINGLE_CONTIGUOUS_SPAN",
                "purpose": "RESULT_INDEPENDENT_BOUNDED_PRIMARY_BASIN_OFFICIAL_QMT_FIXED_POINT_VERSUS_PLUS_MINUS_REFIT_AGREEMENT",
            },
            "degenerate_prefix": {
                "seed": 880, "duration_s": 2.5, "excitation_scale": 0.0,
                "nonideal": False, "imperfect_rest_return": True,
                "random_gap_min_rows": 1, "random_gap_max_rows_inclusive": 4,
            },
            "low_information": {
                "seed": 777, "duration_s": 10.0,
                "excitation_scale": 0.001, "nonideal": True,
                "imperfect_rest_return": True,
                "random_gap_min_rows": 1, "random_gap_max_rows_inclusive": 4,
            },
            "wrong_mapping": {
                "parent_seed": 778, "child_seed": 779, "duration_s": 10.0,
                "excitation_scale": 1.0, "nonideal": True,
                "imperfect_rest_return": True,
            },
            "timing": {
                "signal_rows": 2400,
                "outside_support_extra_shift_samples": 40,
            },
            "persistent_clock": {
                "observation_count": 5,
                "reference_spacing_s": 100.0,
                "initial_offset_s": 0.02,
                "true_drift_ppm": 800.0,
                "alternating_jitter_s": 0.001,
                "observation_sigma_s": 0.005,
            },
            "excessive_clock": {
                "observation_count": 3,
                "reference_spacing_s": 100.0,
                "drift_fraction": 0.01,
                "observation_sigma_s": 0.001,
            },
        },
        "post_qmt_runtime_positive": {
            "chronological_index": 0,
            "long_action_rows": 2001,
            "other_action_rows": 4,
            "sample_period_us": 5000,
            "accelerometer_z_raw": 2048,
            "accelerometer_motion_amplitude_raw": 36,
            "gyroscope_motion_amplitude_raw": 320,
            "gyroscope_secondary_amplitude_raw": 170,
            "qmt_observed_branch_rule": "LEXICOGRAPHIC_FIRST_CUMULATIVE_HARD_SUPPORTED_BRANCH",
            "other_branches": "EXPLICIT_PERSISTENT_NO_UPDATE_NOT_DROPPED",
            "geometry_fixture": "REGISTERED_SYNTHETIC_FULL_TREE_OWNER_FIXTURE_ONLY_NO_REAL_ROWS",
            "required_owner_path": [
                "C2PipelineRuntime.process_current_heading_span",
                "PersistentHeadingOwner.process_span:qmt.headingCorrection",
                "PersistentHeadingOwner.assemble_action_rooted_trajectory",
                "C2PipelineRuntime._owner_derived_physical_prefix_inputs",
                "ScientificForwardKinematicsOwner.assess_prefix_trajectory",
            ],
        },
        "positive_distribution_dimensions": list(POSITIVE_DISTRIBUTION_DIMENSIONS),
        "mandatory_sensor_and_numerical_mutations": list(SENSOR_AND_NUMERICAL_MUTATIONS),
        "mandatory_architecture_negative_mutations": list(ARCHITECTURE_MUTATIONS),
        "mandatory_negative_mutations": list(SENSOR_AND_NUMERICAL_MUTATIONS + ARCHITECTURE_MUTATIONS),
        "execution_coverage_rule": "EXECUTED_OWNER_LEVEL_ONLY;DECLARATIVE_OR_GENERATED_BUT_UNCONSUMED_IS_FAIL",
        "qualification_thresholds": {
            **settings["synthetic"]["qualification_thresholds"],
            "order_sensitivity_center_m": 0.03,
            "order_sensitivity_axis_deg": 10.0,
            "axis_covariance_symmetry_tolerance_rad2": 1e-12,
            "axis_covariance_minimum_eigenvalue_rad2": -1e-12,
            "axis_support_information_maximum_truth_error_deg": 15.0,
            "axis_support_information_maximum_axis_delta_deg": 10.0,
            "axis_support_information_maximum_truth_mahalanobis_squared": 16.25,
            "axis_support_information_maximum_normalized_covariance_ratio": 10.0,
            "axis_support_information_maximum_covariance_increase_factor": 4.0,
            "center_robust_f_scale_maximum_endpoint_error_m": float(
                settings["synthetic"]["qualification_thresholds"]["center_error_80pct_m"]
            ),
            "center_robust_f_scale_maximum_informed_projection_error_m": float(
                np.sqrt(2.0)
                * settings["synthetic"]["qualification_thresholds"]["center_error_80pct_m"]
            ),
            "center_robust_f_scale_maximum_delta_from_primary_m": 0.05,
            "center_robust_f_scale_maximum_truth_mahalanobis_squared": 16.812,
            "center_robust_f_scale_maximum_covariance_trace_ratio": 4.0,
            "center_gyro_minimum_information_response_fraction": 0.01,
            "center_gyro_minimum_zeroed_covariance_response_fraction": 0.0001,
            "center_gyro_covariance_monotonic_absolute_tolerance_m2": 1e-12,
        },
        "scenario_parameter_provenance": "Six deterministic pre-outcome worlds span mounts, wear, dimensions, motion/noise severity and observed-row anomalies; every field must be consumed. Ninety seconds supplies at least eighty complete 1-second gap-safe design blocks for the highest pre-registered support-information level plus bounded observation removals. P1 initial-still statistics may inform compute-duration planning only; neither the duration nor threshold was selected from a positive center/axis outcome.",
        "distribution_acceptance": "PER_CASE_FRACTION_PLUS_80TH_PERCENTILE_ERROR_PLUS_COVERAGE_PLUS_EXACT_ALL_MUTATIONS;MEDIAN_ONLY_FORBIDDEN",
    })
    gyro_gate_thresholds = settings["synthetic"]["qualification_thresholds"]
    if (
        float(gyro_gate_thresholds["center_gyro_minimum_information_response_fraction"])
        != 0.01
        or float(gyro_gate_thresholds["center_gyro_minimum_zeroed_covariance_response_fraction"])
        != 0.0001
        or float(gyro_gate_thresholds["center_gyro_covariance_monotonic_absolute_tolerance_m2"])
        != 1e-12
    ):
        raise RuntimeError("center gyro mutation thresholds changed before seal")
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("source closure may be built only in the canonical workspace")
    closure: dict[str, str] = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory qualification source is missing: {relative}")
        closure[relative] = _sha(path)
    return closure


def main() -> None:
    root = Path.cwd().resolve()
    if root != WORKSPACE:
        raise RuntimeError("run the prefit amendment generator only from canonical Fusion_Part")
    settings = build_effective_settings(root)
    source_hashes = build_qualified_source_hashes(root)
    created = datetime.now(timezone.utc).isoformat()
    amendment = {
        "schema": "biospur-c2-active-parameter-registry-prefit-amendment-v2",
        "created_utc": created,
        "append_only_parent": {"path": str(PARENT_AMENDMENT_RELATIVE), "sha256": PARENT_AMENDMENT_SHA256},
        "superseded_seal_retained": {"path": str(PARENT_SEAL_RELATIVE), "sha256": PARENT_SEAL_SHA256, "status": "EVIDENCE_NOT_FINAL_PREFIT_SEAL"},
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "synthetic_outcome_observed": False,
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
        "source_change_policy": "ANY_QUALIFIED_SOURCE_CHANGE_INVALIDATES_THIS_SEAL_AND_REQUIRES_APPEND_ONLY_SUPERSESSION",
    }
    amendment_path = root / AMENDMENT_002_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {"path": str(PARENT_SEAL_RELATIVE), "sha256": PARENT_SEAL_SHA256},
        "amendment": {"path": str(AMENDMENT_002_RELATIVE), "sha256": _sha(amendment_path)},
        "amendment_append_only_parent": amendment["append_only_parent"],
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "qualified_source_hashes": source_hashes,
        "synthetic_qualification_status": "NOT_RUN_PREFIT_SEAL_ONLY_REAL_FIT_BLOCKED",
        "synthetic_outcome_observed_before_seal": False,
        "real_fit_authorized_after_registry_alone": False,
        "heldout_opened": False,
    }
    seal_path = root / SEAL_002_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_002": str(AMENDMENT_002_RELATIVE),
        "amendment_002_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_002": str(SEAL_002_RELATIVE),
        "seal_002_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "synthetic_or_real_fit_started": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
