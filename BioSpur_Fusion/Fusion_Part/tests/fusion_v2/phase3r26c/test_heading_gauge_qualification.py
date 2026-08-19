from __future__ import annotations

from dataclasses import FrozenInstanceError
import math

import numpy as np
import pytest

from biospur_fusion.heading_anchor_audit_v2.heading_gauge import (
    HEADING_GAUGE_CACHE_KEY,
    HeadingGaugeState,
    HeadingGaugeValidationError,
    validate_semantic_cache,
)
from biospur_fusion.heading_anchor_audit_v2.qualification import (
    AUTHORIZED_R23_SOURCE_SHA256,
    oracle_rz,
    oracle_wrap_2pi,
    migrate_synthetic_authorized,
    run_gauge_equivariance,
    run_required_mutations,
    run_serialization_and_validation,
    synthetic_legacy_candidate,
    synthetic_state,
)


def test_heading_gauge_state_is_immutable_and_h_is_derived_only():
    state = synthetic_state()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        state.psi_protocol_to_common_rad = 0.0
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        state.h_common_rad_by_coordinate = {}
    with pytest.raises(TypeError):
        state.k_protocol_relative_rad_by_coordinate[state.coordinate_order[0]] = 0.0
    assert "h_common_rad" not in state.to_payload()


def test_explicit_rotation_views_match_independent_oracle():
    state = synthetic_state()
    coordinate = state.coordinate_order[3]
    R_EiI = oracle_rz(-0.27)
    assert np.allclose(
        state.R_PI(R_EiI, coordinate),
        oracle_rz(state.k_protocol_relative_rad(coordinate)) @ R_EiI,
        atol=1e-14,
    )
    assert np.allclose(
        state.R_GI(R_EiI, coordinate),
        oracle_rz(state.h_common_rad(coordinate)) @ R_EiI,
        atol=1e-14,
    )


def test_legacy_migration_is_k_without_adding_psi():
    candidate = synthetic_legacy_candidate()
    state = migrate_synthetic_authorized(candidate)
    zero = candidate["joint_modes"][0]
    assert all(
        state.k_protocol_relative_rad(name)
        == pytest.approx(zero["relative_heading_rad"][name], abs=1e-15)
        for name in state.coordinate_order
    )
    assert state.psi_protocol_to_common_rad == 0.0


def test_gauge_equivariance_covers_fixed_random_and_all_branches():
    result = run_gauge_equivariance()
    assert result["alpha_count"] == 70
    assert result["random_alpha_count"] == 64
    assert result["branch_vectors_per_alpha"] == 512
    assert result["all_passed"] is True
    assert result["max_abs_error"] <= result["tolerance"]


def test_serialization_and_validator_matrix():
    result = run_serialization_and_validation()
    assert result["executed_count"] >= 18
    assert result["passed_count"] == result["executed_count"]


def test_all_required_mutations_are_executed_and_detected():
    result = run_required_mutations()
    assert result["executed_count"] == 14
    assert result["passed_count"] == 14
    assert result["literal_true_count"] == 0
    assert all(row["actual_altered_value"] is not None for row in result["mutations"])


def test_stale_derived_cache_is_rejected_even_with_current_key():
    state = synthetic_state()
    with pytest.raises(HeadingGaugeValidationError, match="stale R2.6 derived"):
        validate_semantic_cache(
            {
                "semantic_cache_key": HEADING_GAUGE_CACHE_KEY,
                "heading_gauge_state_sha256": state.payload_sha256(),
                "branch_scores": [0.0],
            },
            state,
        )


def test_noncanonical_degree_like_input_is_rejected():
    state = synthetic_state()
    payload = state.to_payload()
    payload["k_protocol_relative_rad_by_coordinate"][state.coordinate_order[0]] = 180.0
    with pytest.raises(HeadingGaugeValidationError, match="degrees"):
        HeadingGaugeState.from_payload(payload)


@pytest.mark.parametrize("alpha", [math.pi, math.pi - 1e-9, -math.pi + 1e-9])
def test_wrap_boundary_h_covariance(alpha):
    state = synthetic_state()
    shifted = state.shifted_common_gauge(alpha)
    for coordinate in state.coordinate_order:
        assert oracle_wrap_2pi(
            shifted.h_common_rad(coordinate)
            - state.h_common_rad(coordinate) - alpha
        ) == pytest.approx(0.0, abs=1e-12)
