from __future__ import annotations

import pytest


EXPECTED = (
    ("A", "all_source_hashes_exact"),
    ("B", "ten_nodes_partitioned_into_two_hardware_families"),
    ("C", "cross_family_transform_reuse_forbidden"),
    ("D", "fusion_core_interface_names_match"),
    ("E", "whole_board_identity_not_overclaimed"),
    ("F", "component_side_outward_bottom_side_bandward"),
    ("G", "pogo_row_is_negative_y_down_marker_not_attachment"),
    ("H", "six_mm_direct_attachment_measurement_recorded"),
    ("I", "enclosure_not_attachment_authority"),
    ("J", "attachment_not_overpromoted_to_full_transform"),
    ("K", "uwb_region_not_phase_centre"),
    ("L", "cad_reference_transform_inverse_consistent"),
    ("M", "immutable_87_slot_registry_unchanged"),
    ("N", "deterministic_serialization"),
)


@pytest.mark.parametrize("index,letter,name", [(i, *row) for i, row in enumerate(EXPECTED)])
def test_gate(gates, index: int, letter: str, name: str) -> None:
    assert gates["gate_count"] == 14
    row = gates["gates"][index]
    assert row["gate"] == letter
    assert row["name"] == name
    assert row["pass"], row
