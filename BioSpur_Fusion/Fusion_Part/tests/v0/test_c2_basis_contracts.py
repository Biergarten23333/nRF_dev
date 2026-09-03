from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from biospur_fusion.v0.c2_basis.contracts import C2_IDENTITY, load_c2_authority
from biospur_fusion.v0.c2_basis.geometry import LIMBS, load_body_geometry
from biospur_fusion.v0.c2_basis.mounts import (
    body_from_sensor_from_directions,
    candidate_bank,
    nominal_body_from_sensor,
    qualitative_forward_projection_report,
)
from biospur_fusion.v0.c2_basis.real_data import audit_c2_raw6_decode_scope


ROOT = Path(__file__).resolve().parents[2]


def test_sealed_c2_identity_wins_over_legacy_v4_aliases() -> None:
    authority = load_c2_authority(ROOT)
    assert authority["identity"] == C2_IDENTITY
    assert C2_IDENTITY["BSFEC35"] == "forearm_left"
    assert C2_IDENTITY["BSFB165"] == "forearm_right"
    legacy = json.loads(
        (ROOT / "config/captures/v47_ten_node_body_calibration_20260814_093601.json").read_text()
    )["mapping"]
    assert legacy["BSFB165"]["placement"] == "Wrist_L"
    assert legacy["BSFEC35"]["placement"] == "Wrist_R"
    assert authority["identity"] != {
        **C2_IDENTITY,
        "BSFEC35": "forearm_right",
        "BSFB165": "forearm_left",
    }


def test_lateral_shank_mounting_is_never_front_mounted() -> None:
    authority = load_c2_authority(ROOT)
    for node in ("BSF6C53", "BSF8BC4"):
        assert authority["wear_by_node"][node]["mount_surface"] == "lateral_shank_not_front"
        sealed_row = next(
            row for row in authority["sealed"]["rows"] if row["hardware_id"] == node
        )
        assert "not front-mounted" in sealed_row["mount_landmark"]


def test_qualitative_frame_projection_semantics_are_node_specific() -> None:
    transforms = {node: nominal_body_from_sensor(node) for node in C2_IDENTITY}
    report = qualitative_forward_projection_report(transforms)
    assert report["pass"] is True
    assert report["raw_xyz_bilateral_equality_expected"] is False
    local = report["local_forward_by_node"]
    assert local["BSFEC35"][0] > 0 and local["BSFB165"][0] < 0
    assert local["BSF44AD"][2] < 0 and local["BSF3C79"][2] < 0
    assert local["BSF6C53"][0] > 0 and local["BSF8BC4"][0] < 0
    assert local["BSFAA61"][2] > abs(local["BSFAA61"][0]) > 0
    assert local["BSF1120"][2] > abs(local["BSF1120"][0]) > 0
    assert local["BSFAA61"][0] > 0 and local["BSF1120"][0] < 0


def test_mutation_wrongly_expecting_wrist_raw_forward_signs_to_match_is_rejected() -> None:
    transforms = {node: nominal_body_from_sensor(node) for node in C2_IDENTITY}
    transforms["BSFB165"] = transforms["BSFEC35"].copy()
    report = qualitative_forward_projection_report(transforms)
    assert report["gates"]["left_wrist_forward_plus_x"] is True
    assert report["gates"]["right_wrist_forward_minus_x"] is False
    assert report["pass"] is False


def test_mutation_wrongly_flipping_one_knee_forward_component_is_rejected() -> None:
    transforms = {node: nominal_body_from_sensor(node) for node in C2_IDENTITY}
    transforms["BSF3C79"] = body_from_sensor_from_directions(np.array([-1.0, 0.0, 0.0]))
    report = qualitative_forward_projection_report(transforms)
    assert report["gates"]["thigh_forward_shared_minus_z"] is False
    assert report["pass"] is False


def test_mount_bank_retains_legal_uncertain_branches_and_negative_controls() -> None:
    branches = candidate_bank()
    legal = [branch for branch in branches if branch.branch_id.startswith("LEGAL")]
    negative = [branch for branch in branches if branch.branch_id.startswith("NEGATIVE")]
    assert len(legal) == 9
    assert all(branch.metadata_gate["hard_pass"] for branch in legal)
    assert len(negative) == 2
    assert all(not branch.metadata_gate["hard_pass"] for branch in negative)


def test_anthropometry_is_fixed_bounded_and_preserves_surface_width_semantics() -> None:
    geometry = load_body_geometry(ROOT)
    audit = geometry.validate()
    assert audit["bone_length_estimator_coordinates"] == 0
    assert audit["bilateral_equality_constraint"] is False
    assert set(geometry.segments) == set(LIMBS)
    assert geometry.segments["upper_arm_left"].value_m == 0.3175
    assert geometry.segments["forearm_left"].lower_m == 0.225
    assert geometry.segments["forearm_right"].upper_m == 0.285
    assert geometry.segments["thigh_left"].value_m == 0.48
    assert geometry.segments["shank_right"].value_m == 0.43
    assert geometry.biacromial.value_m == 0.4125
    assert geometry.pelvis_imu_to_chest_imu.value_m == 0.28
    assert "NOT_DERIVED_FROM_BICRISTAL_OR_BITROCHANTERIC" in geometry.internal_hip_spacing.source


def _raw6_access_scope() -> dict:
    return {
        "uwb_spatial_payload_consumed": False,
        "hxx_payload_opened": False,
        "decode": {
            "decoded_payload_classes": ["TEN_NODE_IMU"],
            "uwb_spatial_fields_decoded": [],
            "range_values_consumed": False,
            "anchor_geometry_consumed": False,
            "uwb_transport_envelopes_skipped_without_payload_decode": 10,
            "raw_access": {
                "forbidden_interval_bytes_touched": False,
                "complete_container_scan_attempted": False,
                "slice_sha256_verified": True,
            },
        },
    }


def test_c2_raw6_scope_accepts_exact_empty_spatial_field_list_only() -> None:
    report = audit_c2_raw6_decode_scope(_raw6_access_scope())
    assert report["pass"] is True
    assert report["named_conflicts"] == []


def test_c2_raw6_scope_fails_closed_on_missing_nonempty_or_extra_payload_schema() -> None:
    missing = _raw6_access_scope()
    del missing["decode"]["uwb_spatial_fields_decoded"]
    assert audit_c2_raw6_decode_scope(missing)["pass"] is False
    nonempty = _raw6_access_scope()
    nonempty["decode"]["uwb_spatial_fields_decoded"] = ["range_m"]
    assert audit_c2_raw6_decode_scope(nonempty)["pass"] is False
    boolean_alias = _raw6_access_scope()
    boolean_alias["decode"]["uwb_spatial_fields_decoded"] = False
    assert audit_c2_raw6_decode_scope(boolean_alias)["pass"] is False
    extra_class = _raw6_access_scope()
    extra_class["decode"]["decoded_payload_classes"].append("UWB_RANGE")
    assert audit_c2_raw6_decode_scope(extra_class)["pass"] is False
