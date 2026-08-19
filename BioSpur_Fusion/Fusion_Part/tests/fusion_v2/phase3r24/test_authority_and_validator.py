import json
from pathlib import Path

import pytest

from biospur_fusion.heading_anchor_audit_v1.pipeline import golden_tests
from biospur_fusion.heading_anchor_audit_v1.validator import REQUIRED_SCOPE, ValidationError, validate_raw_metrics


ROOT = Path(__file__).resolve().parents[5]
CONFIG = ROOT / "BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase3r24"


def _load(name):
    return json.loads((CONFIG / name).read_text())


def _metrics():
    authority = _load("PHYSICAL_SOURCE_AUTHORITY.json")
    return {
        "route": "C", "pelvis_chain_classification": "CONFLICTING_OR_REVISION_UNBOUND",
        "pelvis_authority": authority, "single_candidate_created": False,
        "minimal_capture_plan_created": True,
        "opensense_common_heading_prerequisite_ready": False,
        "opensense_full_input_pipeline_ready": False,
        "r23_reproduction": {"exact_match": True},
        "actual_symmetry": {"generator_rank": 9, "exact_branch_count": 512, "remaining_generator_rank": 9},
        "scope_qualifiers": REQUIRED_SCOPE,
        "consumption": _load("R24_VALIDATOR_RULES.json")["required_zero_consumption"],
    }


def test_action_authority_uses_closed_taxonomy():
    allowed = {"DIRECTED_VECTOR", "AXIS_LINE_PLUS_MINUS", "DIRECTED_PLANE_NORMAL",
               "UNDIRECTED_PLANE", "TILT_ONLY_ZERO_HEADING_JACOBIAN", "NO_AUTHORIZED_HEADING_SEMANTIC"}
    table = _load("R24_ACTION_DIRECTIONAL_AUTHORITY.json")
    assert {row["classification"] for row in table["rows"]} <= allowed


def test_directed_rows_have_both_sign_bits():
    rows = _load("R24_ACTION_DIRECTIONAL_AUTHORITY.json")["rows"]
    for row in rows:
        if row["classification"] == "DIRECTED_VECTOR":
            assert row["sensor_local_direction_sign_authorized"]
            assert row["protocol_target_direction_sign_authorized"]


def test_r23_no_directed_ready_upgrade_without_resegmentation():
    rows = _load("R24_ACTION_DIRECTIONAL_AUTHORITY.json")["rows"]
    assert all(not row["existing_r23_factor_ready"] for row in rows if row["classification"] == "DIRECTED_VECTOR")


def test_tpose_remains_axis_line():
    rows = {r["action_id"]: r for r in _load("R24_ACTION_DIRECTIONAL_AUTHORITY.json")["rows"]}
    assert rows["02_t_pose"]["classification"] == "AXIS_LINE_PLUS_MINUS"


def test_hula_has_no_heading_semantic():
    rows = {r["action_id"]: r for r in _load("R24_ACTION_DIRECTIONAL_AUTHORITY.json")["rows"]}
    assert rows["03_pelvis_hula_circle"]["classification"] == "NO_AUTHORIZED_HEADING_SEMANTIC"


def test_still_and_axial_rotation_zero_heading_jacobian():
    rows = {r["action_id"]: r for r in _load("R24_ACTION_DIRECTIONAL_AUTHORITY.json")["rows"]}
    for action in ("00_initial_still", "15_trunk_axial_rotation", "17_final_still"):
        assert rows[action]["classification"] == "TILT_ONLY_ZERO_HEADING_JACOBIAN"


def test_golden_mutations_all_rejected():
    golden = golden_tests()
    assert golden["factor_geometry"]["directed_changed"]
    assert golden["factor_geometry"]["line_invariant"]
    assert golden["wxyz_xyzw_mutation_rejected"]
    assert golden["R_transpose_mutation_rejected"]


def test_validator_derives_route_c_verdict():
    result = validate_raw_metrics(_metrics(), _load("R24_VALIDATOR_RULES.json"))
    assert result["verdict"] == "FAIL_PHASE3R24_EXISTING_EVIDENCE_CANNOT_ANCHOR_PELVIS_TO_PROTOCOL"
    assert result["opensense_common_heading_prerequisite_ready"] is False


def test_validator_rejects_route_a_without_chain():
    metrics = _metrics(); metrics["route"] = "A"
    with pytest.raises(ValidationError):
        validate_raw_metrics(metrics, _load("R24_VALIDATOR_RULES.json"))


def test_validator_rejects_candidate_in_route_c():
    metrics = _metrics(); metrics["single_candidate_created"] = True
    with pytest.raises(ValidationError):
        validate_raw_metrics(metrics, _load("R24_VALIDATOR_RULES.json"))


def test_validator_rejects_uwb_consumption():
    metrics = _metrics(); metrics["consumption"] = dict(metrics["consumption"])
    metrics["consumption"]["UWB_measurement"] = 1
    with pytest.raises(ValidationError):
        validate_raw_metrics(metrics, _load("R24_VALIDATOR_RULES.json"))


def test_validator_rejects_opensense_readiness():
    metrics = _metrics(); metrics["opensense_common_heading_prerequisite_ready"] = True
    with pytest.raises(ValidationError):
        validate_raw_metrics(metrics, _load("R24_VALIDATOR_RULES.json"))


def test_validator_rejects_hardcoded_wrong_branch_count():
    metrics = _metrics(); metrics["actual_symmetry"]["exact_branch_count"] = 511
    with pytest.raises(ValidationError):
        validate_raw_metrics(metrics, _load("R24_VALIDATOR_RULES.json"))


def test_validator_requires_all_scope_qualifiers():
    metrics = _metrics(); metrics["scope_qualifiers"] = metrics["scope_qualifiers"][:-1]
    with pytest.raises(ValidationError):
        validate_raw_metrics(metrics, _load("R24_VALIDATOR_RULES.json"))
