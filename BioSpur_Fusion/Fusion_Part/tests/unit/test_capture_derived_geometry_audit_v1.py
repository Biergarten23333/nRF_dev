from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.visualization.capture_derived_audit_v1 import (
    DIMENSIONS, PairData, PairProblem, _verdict,
)


ROOT = Path(__file__).resolve().parents[3]
GATES_PATH = ROOT / "Fusion_Part/config/visualization_centerline_v1/capture_derived_geometry_audit_gates_v1.json"


def gates() -> dict:
    return json.loads(GATES_PATH.read_text())


def synthetic_problem() -> PairProblem:
    count = 24
    rotation = np.repeat(np.eye(3)[None], count, axis=0)
    proximal = np.zeros((count, 3)); distal = np.tile([.31, 0, 0], (count, 1))
    data = PairData(np.asarray(["t_pose"] * count), np.arange(count, dtype=np.int64),
                    proximal, distal, np.repeat(np.eye(3)[None] * 1e-4, count, axis=0),
                    rotation, rotation)
    return PairProblem("rendering_forearm_length_L", ("A", "B"), data, gates())


def passing_evidence() -> tuple[dict, dict]:
    evidence = {
        "profile_interval": {"parameter_bound_reached": False, "full_width_mm": 10.0},
        "maximum_absolute_placement_correlation": .2,
        "relevant_bound_hits": [], "multistart_spread_mm": 1.0,
        "interleaved_spread_mm": 1.0, "optional_action_removal_spread_mm": 1.0,
    }
    common = {"optimizer_success": True, "normalized_residual": {"median": 1.0, "p95": 2.0}}
    return evidence, common


def test_gate_document_seals_operator_and_heldout_payloads():
    value = gates()
    assert value["operator_measurements"] == "SEALED_AND_FORBIDDEN"
    assert value["heldout"] == {"walk": "SEALED", "final_still": "SEALED"}
    assert value["sealed_future_comparison_contract"]["enabled_during_this_audit"] is False
    assert "20.0" in value["sealed_future_comparison_contract"]["agreement_gate"]


def test_dimension_matrix_is_exact_and_has_independent_sides():
    assert len(DIMENSIONS) == 12
    assert "rendering_forearm_length_L" in DIMENSIONS and "rendering_forearm_length_R" in DIMENSIONS
    assert "rendering_shank_length_L" in DIMENSIONS and "rendering_shank_length_R" in DIMENSIONS


def test_correct_lever_arm_equation_has_zero_residual_at_fixture_truth():
    problem = synthetic_problem(); x = problem.initial()
    x[0] = .31; x[1:4] = [.02, .01, -.01]; x[7:10] = [.02, .01, -.01]
    assert np.max(np.abs(problem.residual(x))) < 1e-12


@pytest.mark.parametrize("field,value,expected", [
    ("multistart_spread_mm", 10.01, "FAIL_ACTION_DEPENDENCE"),
    ("interleaved_spread_mm", 10.01, "FAIL_ACTION_DEPENDENCE"),
    ("optional_action_removal_spread_mm", 10.01, "FAIL_ACTION_DEPENDENCE"),
    ("maximum_absolute_placement_correlation", .981, "FAIL_PLACEMENT_COUPLING"),
])
def test_each_declared_stability_gate_controls_its_decision(field, value, expected):
    evidence, common = passing_evidence(); evidence[field] = value
    assert _verdict(evidence, common, gates())[0] == expected


def test_profile_and_model_mismatch_gates_control_decisions():
    evidence, common = passing_evidence(); evidence["profile_interval"]["full_width_mm"] = 20.01
    assert _verdict(evidence, common, gates())[0] == "FAIL_UNOBSERVABLE"
    evidence, common = passing_evidence(); common["normalized_residual"]["p95"] = 8.01
    assert _verdict(evidence, common, gates())[0] == "FAIL_MODEL_MISMATCH"


def test_tool_exposes_no_operator_measurement_argument():
    source = (ROOT / "Fusion_Part/tools/run_capture_derived_rendering_geometry_audit.py").read_text()
    assert "operator-measurement" not in source
    assert "--calibration-ledger" in source


def test_default_result_provenance_never_claims_measured_or_clinical():
    source = (ROOT / "Fusion_Part/src/biospur_fusion/visualization/capture_derived_audit_v1.py").read_text()
    assert '"CAPTURE_DERIVED_RENDERING_LENGTH"' in source
    assert "NOT_ANATOMICAL_GROUND_TRUTH_NOT_CLINICAL" in source
