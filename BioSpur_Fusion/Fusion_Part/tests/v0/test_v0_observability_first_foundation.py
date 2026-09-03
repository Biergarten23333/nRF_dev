import json
from pathlib import Path

import numpy as np

from biospur_fusion.v0.observability_first import (
    SEGMENTS,
    TposeAzimuthCounterexample,
    foundation_qualification,
    heading_graph_observability,
    heading_incidence_jacobian,
)


def test_heading_graph_has_exactly_one_common_yaw_gauge():
    jacobian = heading_incidence_jacobian()
    assert jacobian.shape == (9, 10)
    assert np.array_equal(jacobian @ np.ones(len(SEGMENTS)), np.zeros(9))
    result = heading_graph_observability()
    assert result["unquotiented"]["rank"] == 9
    assert result["unquotiented"]["nullity"] == 1
    assert result["unquotiented"]["common_yaw_null_alignment"] > 1.0 - 1e-10
    assert result["pelvis_gauge_quotient"]["rank"] == 9
    assert result["pelvis_gauge_quotient"]["nullity"] == 0
    assert result["pass"] is True


def test_rejected_one_common_yaw_cannot_repair_independent_tpose_azimuths():
    result = TposeAzimuthCounterexample.decisive().evaluate()
    assert result["one_common_yaw_q90_error_deg"] > 80.0
    assert result["one_common_yaw_max_error_deg"] > 80.0
    assert result["legacy_one_common_yaw_fails"] is True
    assert result["nine_relative_heading_max_error_deg"] < 1e-10
    assert result["required_nine_heading_formulation_passes"] is True


def test_foundation_qualification_is_payload_free_and_fail_closed():
    result = foundation_qualification()
    assert result["real_capture_payload_accessed"] is False
    assert result["hxx_golf_boxing_payload_accessed"] is False
    assert result["pass"] is True


def test_every_authorized_non_h_action_has_explicit_residual_and_parameter_accounting():
    root = Path(__file__).parents[2]
    protocol = json.loads((root / "config/biospur_fusion_v0/dual_capture_protocol.json").read_text())
    contract = json.loads((root / "config/biospur_fusion_v0_observability_first/ACTION_FACTOR_CONTRACT.json").read_text())
    for capture_name, capture in protocol["captures"].items():
        expected = set(capture["calibration_inputs"])
        mapped = contract["per_capture_action_parameter_map"][capture_name]
        assert set(mapped) == expected
        for action, row in mapped.items():
            assert row["residuals"], action
            assert row["parameters"], action
    assert "H00_walk" not in contract["per_capture_action_parameter_map"]["CAPTURE2"]
