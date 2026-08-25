from __future__ import annotations

import pytest


GATES = (
    ("A", "predecessor_protected_hashes_exact"),
    ("B", "ten_node_identities_unique_complete"),
    ("C", "corrected_wrist_map_enforced"),
    ("D", "bsfc2cc_pelvis_identity_enforced"),
    ("E", "donning_axis_authority_not_overstated"),
    ("F", "neutral_minus_y_gravity_not_permanent"),
    ("G", "accelerometer_specific_force_sign_correct"),
    ("H", "all_deferred_real_measurements_null"),
    ("I", "no_population_average_anthropometry"),
    ("J", "direct_observation_separate_from_latent_bone_geometry"),
    ("K", "torso_top_resolved_without_duplicate_freedom"),
    ("L", "hardware_revisions_separated_fail_closed"),
    ("M", "transform_composition_inverse_consistency"),
    ("N", "nominal_antenna_not_phase_centre"),
    ("O", "synthetic_world_isolated_from_real_bridge"),
    ("P", "anchor_and_clock_provenance_capture_bound"),
    ("Q", "all_87_source_slots_null_frozen"),
    ("R", "no_synthetic_value_contaminates_real_registry"),
    ("S", "future_r6a2_adapter_fails_closed"),
    ("T", "deterministic_replay_output_hashes"),
)


@pytest.mark.parametrize("index,letter,name", [(index, *row) for index, row in enumerate(GATES)])
def test_mandatory_gate(qualification, index: int, letter: str, name: str) -> None:
    assert qualification["gate_count"] == 20
    gate = qualification["gates"][index]
    assert gate["gate"] == letter
    assert gate["name"] == name
    assert gate["pass"], gate
