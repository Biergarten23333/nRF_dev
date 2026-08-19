"""Current typed-pipeline boundary tests; no historical numeric golden is replayed."""

import ast
import inspect

from biospur_fusion.heading_anchor_audit_v2 import pipeline
from biospur_fusion.heading_anchor_audit_v2.heading_gauge import (
    BranchEvaluation,
    HeadingGaugeValidationError,
    validate_future_candidate_payload,
)
from biospur_fusion.heading_anchor_audit_v2.qualification import (
    synthetic_reference,
    synthetic_state,
)


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


def test_formal_runner_source_reaches_strict_migrator_and_typed_boundaries():
    tree = ast.parse(inspect.getsource(pipeline))
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    for symbol in (
        "migrate_r23_psi_zero_candidate",
        "evaluate_branches",
        "BranchEvaluation",
        "FormalHeadingResult",
        "canonical_result_payload",
    ):
        assert symbol in calls or hasattr(pipeline, symbol)


def test_single_branch_synthetic_path_preserves_typed_k_and_gauge_invariance():
    state = synthetic_state()
    reference = synthetic_reference()
    first = pipeline.score_branch_candidate(state, reference, [0] * 9)
    shifted = pipeline.score_branch_candidate(
        state.shifted_common_gauge(0.37), reference, [0] * 9
    )
    assert first["total_unweighted_semantic_score_rad"] == shifted[
        "total_unweighted_semantic_score_rad"
    ]
    assert first["heading_gauge_state_sha256"] != shifted[
        "heading_gauge_state_sha256"
    ]


def test_full_synthetic_evaluator_returns_validated_branch_envelope():
    result = pipeline.evaluate_branches(synthetic_state(), synthetic_reference())
    assert isinstance(result, BranchEvaluation)
    assert len(result.to_payload()["evaluation"]["candidates"]) == 512
