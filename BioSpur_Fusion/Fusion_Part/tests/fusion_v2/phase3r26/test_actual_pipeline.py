"""The R2.6 actual-result golden is invalid and must never be replayed in R2.6C."""

import ast
from pathlib import Path

from biospur_fusion.heading_anchor_audit_v2.heading_gauge import (
    HeadingGaugeValidationError,
    validate_future_candidate_payload,
)
from biospur_fusion.heading_anchor_audit_v2.qualification import synthetic_state


HISTORICAL_CANDIDATE_SHA256 = (
    "0297d8a3e13ddcf64fe8860656e0b43916ccad62ecd0a7e8fb3fd1690a2d6a95"
)


def test_historical_result_is_provenance_only_not_a_golden():
    assert len(HISTORICAL_CANDIDATE_SHA256) == 64
    assert HISTORICAL_CANDIDATE_SHA256 != synthetic_state().payload_sha256()


def test_legacy_r26_candidate_schema_fails_closed():
    state = synthetic_state()
    legacy = {
        "schema": "biospur.phase3r26.nine_heading_conditional_candidate.v1",
        "candidate_payload_SHA256": HISTORICAL_CANDIDATE_SHA256,
    }
    try:
        validate_future_candidate_payload(legacy, state)
    except HeadingGaugeValidationError as exc:
        assert "legacy or unknown candidate schema" in str(exc)
    else:
        raise AssertionError("legacy R2.6 candidate was accepted")


def test_r26c_suite_does_not_reference_real_session_inputs():
    source = Path(__file__).read_text()
    tree = ast.parse(source)
    called_names = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "run_science" not in called_names
    assert "selected_bit_" + "vector" not in source
    assert "initial" + "_still" not in source
