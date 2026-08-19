from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from biospur_fusion.heading_anchor_audit_v2 import core, pipeline
from biospur_fusion.heading_anchor_audit_v2.heading_gauge import (
    BRANCH_EVALUATION_SEMANTIC_VERSION,
    FUTURE_CANDIDATE_SCHEMA,
    HEADING_GAUGE_CACHE_KEY,
    BranchEvaluation,
    FormalHeadingResult,
    HeadingGaugeState,
    HeadingGaugeValidationError,
    validate_future_candidate_payload,
    validate_semantic_cache,
)
from biospur_fusion.heading_anchor_audit_v2.heading_types import (
    KProtocolRelativeByCoordinate,
    TypedCanonicalPayload,
)
from biospur_fusion.heading_anchor_audit_v2.qualification import (
    future_candidate_payload,
    synthetic_reference,
    synthetic_state,
)


def test_nominal_k_type_is_required_and_raw_mapping_fails_closed():
    state = synthetic_state()
    with pytest.raises(HeadingGaugeValidationError, match="typed K"):
        HeadingGaugeState(
            coordinate_order=state.coordinate_order,
            k_protocol_relative=dict(state.k_protocol_relative_rad_by_coordinate),
            psi_protocol_to_common_rad=state.psi_protocol_to_common_rad,
            source_solution_sha256=state.source_solution_sha256,
            source_schema=state.source_schema,
            migration_id=state.migration_id,
        )
    assert isinstance(state.k_protocol_relative_rad_by_coordinate, KProtocolRelativeByCoordinate)


def test_canonical_serialization_rejects_every_unvalidated_mapping():
    for payload in ({}, {"heading_rad": 0.1}, {"schema": "current-looking"}):
        with pytest.raises(TypeError, match="TypedCanonicalPayload"):
            core.canonical_result_payload(payload)  # type: ignore[arg-type]
    assert isinstance(synthetic_state(), TypedCanonicalPayload)


def test_branch_envelope_direct_constructor_and_object_new_bypass_fail():
    state = synthetic_state()
    with pytest.raises(TypeError, match="direct construction"):
        BranchEvaluation(state, {}, {})
    forged = object.__new__(BranchEvaluation)
    with pytest.raises(AttributeError):
        forged.to_payload()


def test_branch_deserializer_rejects_forged_and_inconsistent_payloads():
    state = synthetic_state()
    valid = pipeline.evaluate_branches(state, synthetic_reference()).to_payload()
    forged = copy.deepcopy(valid)
    forged["semantic_version"] = "forged"
    with pytest.raises(HeadingGaugeValidationError, match="semantic version"):
        BranchEvaluation.from_payload(state, forged)
    inconsistent = copy.deepcopy(valid)
    inconsistent["evaluation"]["candidates"][0]["per_node_directed_distance"][0][
        "h_common_rad_derived"
    ] += 0.2
    with pytest.raises(HeadingGaugeValidationError, match="derived H"):
        BranchEvaluation.from_payload(state, inconsistent)
    incomplete = copy.deepcopy(valid)
    incomplete["evaluation"]["candidates"].pop()
    with pytest.raises(HeadingGaugeValidationError, match="complete branch"):
        BranchEvaluation.from_payload(state, incomplete)


def test_formal_result_factory_rejects_untyped_heading_aliases():
    state = synthetic_state()
    payload = {
        "schema": "biospur.phase3.heading_formal_result.v2",
        "heading_gauge_state": state.to_payload(),
        "heading_gauge_state_sha256": state.payload_sha256(),
        "semantic_cache_key": state.semantic_cache_key,
        "verdict": "SYNTHETIC_ONLY",
    }
    result = FormalHeadingResult.create(state, payload)
    assert core.canonical_result_payload(result)["verdict"] == "SYNTHETIC_ONLY"
    payload["nested"] = {"heading_rad": 0.0}
    with pytest.raises(HeadingGaugeValidationError, match="untyped heading"):
        FormalHeadingResult.create(state, payload)


def test_formal_result_object_new_bypass_cannot_emit_unvalidated_bytes():
    state = synthetic_state()
    forged = object.__new__(FormalHeadingResult)
    object.__setattr__(forged, "_heading_state", state)
    object.__setattr__(forged, "_payload_bytes", json.dumps({"schema": "forged"}).encode())
    with pytest.raises(HeadingGaugeValidationError, match="formal result schema"):
        forged.to_payload()


def _valid_cache(state: HeadingGaugeState) -> dict:
    return {
        "schema": "biospur.phase3.heading_gauge_independent_cache.v1",
        "semantic_cache_key": HEADING_GAUGE_CACHE_KEY,
        "heading_gauge_state_sha256": state.payload_sha256(),
        "source_solution_sha256": state.source_solution_sha256,
        "source_schema": state.source_schema,
        "migration_id": state.migration_id,
        "coordinate_order": list(state.coordinate_order),
    }


def test_cache_boundary_accepts_only_current_complete_provenance():
    state = synthetic_state()
    validate_semantic_cache(_valid_cache(state), state)
    for field in ("semantic_cache_key", "heading_gauge_state_sha256", "migration_id"):
        stale = _valid_cache(state)
        stale[field] = "0" * 64 if field.endswith("sha256") else "stale"
        with pytest.raises(HeadingGaugeValidationError):
            validate_semantic_cache(stale, state)


def test_future_candidate_validator_checks_schema_order_and_h_algebra():
    state = synthetic_state()
    payload = future_candidate_payload(state)
    validate_future_candidate_payload(payload, state)
    legacy = copy.deepcopy(payload)
    legacy["schema"] = "biospur.phase3r26.nine_heading_conditional_candidate.v1"
    with pytest.raises(HeadingGaugeValidationError, match="legacy or unknown"):
        validate_future_candidate_payload(legacy, state)
    swapped = copy.deepcopy(payload)
    swapped["nodes"][0], swapped["nodes"][1] = swapped["nodes"][1], swapped["nodes"][0]
    with pytest.raises(HeadingGaugeValidationError, match="coordinate order"):
        validate_future_candidate_payload(swapped, state)
    inconsistent = copy.deepcopy(payload)
    inconsistent["nodes"][0]["h_common_rad_derived"] += 0.2
    with pytest.raises(HeadingGaugeValidationError, match="inconsistent derived H"):
        validate_future_candidate_payload(inconsistent, state)


def test_formal_runner_has_no_heading_derived_cache_reuse_path():
    source = Path(pipeline.__file__).read_text()
    assert "validate_semantic_cache(" not in source
    assert "heading_gauge_independent_cache" not in source
    assert "cache reuse" not in source.lower()


def test_branch_semantic_version_constant_is_current():
    assert BRANCH_EVALUATION_SEMANTIC_VERSION.endswith(".v1")
    assert FUTURE_CANDIDATE_SCHEMA.endswith(".v2")
