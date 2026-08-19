import json
import math
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.heading_anchor_audit_v1.core import (
    canonical_json_bytes, classify_pelvis_chain, directed_residual, gf2_rank,
    independent_rz, line_residual, wrap_pi,
)
from biospur_fusion.heading_anchor_audit_v1.pipeline import golden_tests


ROOT = Path(__file__).resolve().parents[5]
CONFIG = ROOT / "BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase3r24"


def test_canonical_json_is_order_independent():
    assert canonical_json_bytes({"b": 2, "a": 1}) == canonical_json_bytes({"a": 1, "b": 2})


def test_wrap_pi_range():
    values = wrap_pi(np.linspace(-30, 30, 200))
    assert np.all(values >= -np.pi) and np.all(values < np.pi)


def test_directed_factor_breaks_pi_flip():
    assert abs(directed_residual(0.25 + np.pi, 0.25)) > 3.0


def test_axis_line_preserves_pi_flip():
    assert abs(line_residual(0.25 + np.pi, 0.25)) < 1e-12


def test_gf2_identity_rank():
    assert gf2_rank(np.eye(9, dtype=int).tolist(), 9) == 9


def test_gf2_dependent_rank():
    assert gf2_rank([[1, 0, 1], [0, 1, 1], [1, 1, 0]], 3) == 2


def test_gf2_rejects_nonbinary():
    with pytest.raises(ValueError):
        gf2_rank([[2]], 1)


def test_rz_is_orthogonal_and_proper():
    r = independent_rz(0.73)
    assert np.allclose(r.T @ r, np.eye(3), atol=1e-13)
    assert np.isclose(np.linalg.det(r), 1.0)


def test_left_yaw_cannot_be_fixed_right_extrinsic_dynamically():
    result = golden_tests()["non_collinear_dynamic_trajectory"]
    assert result["fixed_right_absorbs_per_node_left_yaw_for_all_motion"] is False
    assert max(result["errors_frobenius_with_fixed_right_extrinsic"]) > 0.1


def test_configured_pelvis_chain_fails_closed():
    authority = json.loads((CONFIG / "PHYSICAL_SOURCE_AUTHORITY.json").read_text())
    assert classify_pelvis_chain(authority) == "CONFLICTING_OR_REVISION_UNBOUND"


def test_complete_bounded_chain_classifies_route_a_eligible():
    authority = {
        "required_links": [{"source_bound": True, "directed_sign_bound": True, "geometry": "DIRECTED_3D"}],
        "uncertainty_bounded": True, "propagated_uncertainty_deg": 4, "gate_deg": 15,
    }
    assert classify_pelvis_chain(authority) == "DIRECTED_CHAIN_COMPLETE_BOUNDED"


def test_unbounded_chain_not_route_a_eligible():
    authority = {
        "required_links": [{"source_bound": True, "directed_sign_bound": True, "geometry": "DIRECTED_3D"}],
        "uncertainty_bounded": False, "propagated_uncertainty_deg": None, "gate_deg": 15,
    }
    assert classify_pelvis_chain(authority) == "DIRECTED_CHAIN_COMPLETE_UNBOUNDED"


def test_vertical_chain_has_zero_heading_authority():
    authority = {
        "required_links": [{"source_bound": True, "directed_sign_bound": True, "geometry": "VERTICAL_OR_POSITION_ONLY"}],
        "uncertainty_bounded": True, "propagated_uncertainty_deg": 1, "gate_deg": 15,
    }
    assert classify_pelvis_chain(authority) == "VERTICAL_OR_POSITION_ONLY_NO_HEADING"
