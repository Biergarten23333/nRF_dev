from __future__ import annotations

import importlib
import math

import pytest


ORDER = (
    "torso", "upper_arm_left", "forearm_left", "upper_arm_right",
    "forearm_right", "thigh_left", "shank_left", "thigh_right", "shank_right",
)
FIXTURE_K = {name: -1.2 + 0.2 * index for index, name in enumerate(ORDER)}
FIXTURE_PSI = 0.61
FIXTURE_SHA = "1" * 64


def oracle_wrap(angle: float) -> float:
    return (float(angle) + math.pi) % (2.0 * math.pi) - math.pi


def OLD_COMMIT_ADAPTER(gauge):
    """Signature-only adapter for 0f9be699; contains no expected mathematics."""
    return gauge.HeadingGaugeState(
        coordinate_order=ORDER,
        k_protocol_relative_rad_by_coordinate=FIXTURE_K,
        psi_protocol_to_common_rad=FIXTURE_PSI,
        source_solution_sha256=FIXTURE_SHA,
        source_schema="synthetic-command-bound-v1",
        migration_id=gauge.R23_MIGRATION_ID,
    )


def NEW_COMMIT_ADAPTER(gauge):
    """Signature-only adapter for R1; contains no expected mathematics."""
    types = importlib.import_module("biospur_fusion.heading_anchor_audit_v2.heading_types")
    typed = types.KProtocolRelativeByCoordinate(
        coordinate_order=ORDER, k_protocol_relative_rad_by_coordinate=FIXTURE_K
    )
    return gauge.HeadingGaugeState(
        coordinate_order=ORDER,
        k_protocol_relative=typed,
        psi_protocol_to_common_rad=FIXTURE_PSI,
        source_solution_sha256=FIXTURE_SHA,
        source_schema="synthetic-command-bound-v1",
        migration_id=gauge.R23_MIGRATION_ID,
    )


def _adapted_state(gauge):
    try:
        importlib.import_module("biospur_fusion.heading_anchor_audit_v2.heading_types")
    except ModuleNotFoundError:
        return OLD_COMMIT_ADAPTER(gauge)
    return NEW_COMMIT_ADAPTER(gauge)


def _modules():
    gauge = importlib.import_module("biospur_fusion.heading_anchor_audit_v2.heading_gauge")
    core = importlib.import_module("biospur_fusion.heading_anchor_audit_v2.core")
    qualification = importlib.import_module("biospur_fusion.heading_anchor_audit_v2.qualification")
    return gauge, core, qualification


def test_same_fixture_and_independent_oracle_cross_both_api_signatures():
    gauge, _core, _qualification = _modules()
    state = _adapted_state(gauge)
    for coordinate in ORDER:
        assert state.h_common_rad(coordinate) == pytest.approx(
            oracle_wrap(FIXTURE_K[coordinate] + FIXTURE_PSI), abs=1e-12
        )


def test_nominal_k_type_exists_and_is_exposed_by_state():
    gauge, _core, qualification = _modules()
    types = importlib.import_module("biospur_fusion.heading_anchor_audit_v2.heading_types")
    state = qualification.synthetic_state()
    assert isinstance(state.k_protocol_relative_rad_by_coordinate, types.KProtocolRelativeByCoordinate)


def test_branch_evaluation_direct_constructor_is_forbidden():
    gauge, _core, qualification = _modules()
    state = qualification.synthetic_state()
    with pytest.raises(TypeError, match="direct construction"):
        gauge.BranchEvaluation(state, {}, {})


def test_unvalidated_mapping_cannot_cross_canonical_result_boundary():
    _gauge, core, _qualification = _modules()
    with pytest.raises(TypeError, match="TypedCanonicalPayload"):
        core.canonical_result_payload({"schema": "looks-current"})


def test_branch_evaluation_has_validating_deserializer():
    gauge, _core, qualification = _modules()
    state = qualification.synthetic_state()
    assert callable(gauge.BranchEvaluation.from_payload)
    with pytest.raises((TypeError, ValueError)):
        gauge.BranchEvaluation.from_payload(state, {})


def test_formal_result_requires_a_typed_factory():
    gauge, _core, qualification = _modules()
    state = qualification.synthetic_state()
    assert callable(gauge.FormalHeadingResult.create)
    with pytest.raises(TypeError, match="direct construction"):
        gauge.FormalHeadingResult(state, {})
