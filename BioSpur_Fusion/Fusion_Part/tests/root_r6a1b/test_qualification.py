from __future__ import annotations

import pytest


GATE_NAMES = (
    "exact_87_slot_enumeration_and_counts",
    "exactly_one_authority_per_slot",
    "source_values_and_statuses_immutable",
    "frame_chain_type_and_unit_consistency",
    "shared_parent_child_joint_centre_closure",
    "bone_length_invariance",
    "no_duplicated_free_bone_or_joint_geometry",
    "no_duplicated_free_imu_tag_transform",
    "no_second_free_time_mapping",
    "world_gauge_fixed",
    "acyclic_derivation_graph",
    "minimal_state_serialization_determinism",
    "synthetic_jacobian_rank",
    "remaining_gauges_explicit",
    "bounded_skin_slip_cannot_replace_arbitrary_motion",
    "zero_skin_slip_reproduces_nominal_rigid_model",
    "no_root_r6a0_or_r6a1a_regression",
    "deterministic_replay_identical_artifact_hashes",
)


@pytest.mark.parametrize("number,name", tuple(enumerate(GATE_NAMES, start=1)))
def test_exact_synthetic_gate(qualification, number: int, name: str) -> None:
    assert qualification["gate_count"] == 18
    gate = qualification["gates"][number - 1]
    assert gate["gate"] == number
    assert gate["name"] == name
    assert gate["pass"], gate
