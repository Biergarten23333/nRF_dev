from __future__ import annotations

import copy
import json
from pathlib import Path

from biospur_fusion.heading_anchor_audit_v2.heading_gauge import (
    HEADING_GAUGE_CACHE_KEY,
    R23_MIGRATION_ID,
    HeadingGaugeState,
)
from biospur_fusion.heading_anchor_audit_v2.heading_types import KProtocolRelativeByCoordinate


HERE = Path(__file__).resolve().parent
ORDER = (
    "torso", "upper_arm_left", "forearm_left", "upper_arm_right",
    "forearm_right", "thigh_left", "shank_left", "thigh_right", "shank_right",
)


def load_json(name: str) -> dict:
    return json.loads((HERE / name).read_text())


def pipeline_state() -> HeadingGaugeState:
    fixture = load_json("pipeline_gauge_fixture.json")
    typed_k = KProtocolRelativeByCoordinate(
        coordinate_order=ORDER,
        k_protocol_relative_rad_by_coordinate=fixture["fixed_typed_k"],
    )
    return HeadingGaugeState(
        coordinate_order=ORDER,
        k_protocol_relative=typed_k,
        psi_protocol_to_common_rad=fixture["base_psi"],
        source_solution_sha256=fixture["source_solution_sha256"],
        source_schema=fixture["source_schema"],
        migration_id=R23_MIGRATION_ID,
    )


def formal_payload(state: HeadingGaugeState) -> dict:
    gates = {
        "r24_audit_literal_checks_removed": False,
        "gf2_derived_from_actual_factor_behaviour": False,
        "I3_reassembled_not_copied": False,
        "all_production_mutations_executed": False,
        "pelvis_sign_golden_test_pass": False,
        "real_R_EiI_reference_loaded": False,
        "all_ten_horizontal_projections_nondegenerate": False,
        "all_512_actual_candidates_evaluated": False,
        "exactly_one_actual_branch_selected": False,
        "all_selected_branch_margins_positive": False,
        "seven_action_sign_crosschecks_no_conflict": False,
        "seven_action_sign_crosschecks_consistent": False,
        "forearm_selection_source_explicit": False,
        "all_joint_bootstrap_half_width_le_15deg": False,
        "axis_families_at_least_5_blocks": False,
        "heading_families_at_least_5_blocks": False,
        "opensense_common_heading_prerequisite_ready": False,
        "opensense_full_input_pipeline_ready": False,
        "phase4_ready": False,
        "sealed_consumer_count_zero": True,
        "deterministic_replay": "SYNTHETIC_PENDING",
    }
    support = {
        "schema": "biospur.phase3r26.support_bootstrap_gate_audit.v1",
        "resampling_unit": "independent action/cycle block",
        "frame_samples_treated_independent": False,
        "bootstrap": {
            "source_sha256": "2" * 64,
            "valid_replicates": 0,
            "intervals": {},
            "single_heading_bootstrap_half_width_max_deg": 15.0,
            "all_joint_bootstrap_half_width_le_15deg": False,
        },
        "within_donning_block_support": {
            "families": [],
            "all_families_at_least_5_blocks": False,
            "deficient_family_count": 0,
        },
        "between_donning_repeatability": {
            "independent_donning_count": 1,
            "qualified": False,
            "note": "synthetic-only",
        },
        "external_accuracy": {"available": False, "in_scope": False},
        "immediate_new_capture_required": False,
    }
    consumers = {
        "schema": "biospur.phase3r26.data_access_summary.v1",
        "consumer_count": 0,
        "numeric_consumer_count": 0,
        "sealed_consumer_count": 0,
        "forbidden_consumer_count": 0,
        "counts_by_classification": {},
        "UWB_measurement_consumer_count": 0,
        "OpenSense_consumer_count": 0,
        "Vicon_consumer_count": 0,
        "Phase4_consumer_count": 0,
        "synthetic_truth_formal_consumer_count": 0,
    }
    return {
        "schema": "biospur.phase3.heading_formal_result.v2",
        "run_id": "synthetic-r26c-r2",
        "verdict": "SYNTHETIC_ONLY",
        "source_commits": {"r24_implementation": "a" * 40, "r24_attestation": "b" * 40},
        "heading_gauge_state": state.to_payload(),
        "heading_gauge_state_sha256": state.payload_sha256(),
        "semantic_cache_key": HEADING_GAUGE_CACHE_KEY,
        "selected_GF2_bit_vector": None,
        "selected_branch_count": 0,
        "minimum_branch_margin": None,
        "candidate_payload_sha256": None,
        "production_mutation_count": 0,
        "production_mutation_passed": 0,
        "machine_gates": gates,
        "support": support,
        "consumer_counts": consumers,
        "implementation_commit": "PENDING",
        "attestation_commit": "PENDING",
        "remote_commit": "PENDING",
    }


def copied_formal_payload(state: HeadingGaugeState) -> dict:
    return copy.deepcopy(formal_payload(state))
