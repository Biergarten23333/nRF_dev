from __future__ import annotations

import itertools
import importlib
import math
from typing import Mapping

import pytest

from biospur_fusion.heading_anchor_audit_v2 import core
from biospur_fusion.heading_anchor_audit_v2 import pipeline


R23_SCHEMA = "biospur-phase3r23-prevalidation-session-static-heading-candidate-v1"
ORDER = tuple(core.COORDINATE_ORDER)


def oracle_wrap(angle: float) -> float:
    value = math.fmod(angle + math.pi, 2.0 * math.pi)
    if value < 0.0:
        value += 2.0 * math.pi
    return value - math.pi


def synthetic_legacy_candidate(*, include_representative: bool = True) -> dict:
    k = {name: oracle_wrap(-1.1 + 0.23 * index) for index, name in enumerate(ORDER)}
    modes = []
    for mode_index, bits in enumerate(itertools.product((0, 1), repeat=len(ORDER))):
        row = {
            "mode_id": f"synthetic-{mode_index:03d}",
            "relative_heading_rad": {
                name: oracle_wrap(k[name] + math.pi * bits[index])
                for index, name in enumerate(ORDER)
            },
            "pi_branch_bits": list(bits),
            "objective": 1.25,
            "continuous_orbit": "add common alpha to every h_i and psi_GP for alpha in S1",
        }
        if include_representative:
            row["representative_psi_GP_rad"] = 0.0
        modes.append(row)
    return {
        "schema": R23_SCHEMA,
        "parameter_order": list(ORDER),
        "joint_mode_count": len(modes),
        "continuous_psi_orbit": True,
        "symmetries": [
            "continuous common h_i/psi_GP shift",
            "independent per-heading pi axis-line branches",
        ],
        "joint_modes": modes,
    }


def load_base(candidate: Mapping):
    """One compatibility shim; the test inputs and oracle are version-independent."""
    if hasattr(pipeline, "HeadingGaugeState"):
        gauge = importlib.import_module(
            "biospur_fusion.heading_anchor_audit_v2.heading_gauge"
        )
        source_sha = gauge.legacy_r23_solution_sha(candidate)
        original = gauge.AUTHORIZED_R23_SOURCE_SHA256
        gauge.AUTHORIZED_R23_SOURCE_SHA256 = source_sha
        try:
            return pipeline._base_solution(
                candidate, source_solution_sha256=source_sha
            )
        finally:
            gauge.AUTHORIZED_R23_SOURCE_SHA256 = original
    try:
        value = pipeline._base_solution(
            candidate, source_solution_sha256="synthetic"
        )
    except TypeError:
        value = pipeline._base_solution(candidate)
    return value[0] if isinstance(value, tuple) else value


def evaluate(state, reference: Mapping[str, float], psi: float):
    """Call the production evaluator through its old or repaired typed boundary."""
    if hasattr(state, "semantic_version"):
        shifted = state.with_common_gauge(psi)
        result = pipeline.evaluate_branches(shifted, reference)
        return result.to_payload()
    evaluation, selection = pipeline.evaluate_branches(state, reference, psi)
    return {"evaluation": evaluation, "selection": selection}


def candidate_by_bits(result: Mapping, bits: list[int]) -> Mapping:
    rows = result["evaluation"]["candidates"]
    return next(row for row in rows if row["bit_vector"] == bits)


def score_vector(result: Mapping) -> list[float]:
    return [float(row["total_unweighted_semantic_score_rad"])
            for row in result["evaluation"]["candidates"]]


def test_missing_gauge_transport():
    candidate = synthetic_legacy_candidate()
    state = load_base(candidate)
    psi = 0.71
    result = evaluate(state, {name: 0.0 for name in ORDER}, psi)
    zero = candidate_by_bits(result, [0] * len(ORDER))
    first = zero["per_node_directed_distance"][0]
    observed_h = first.get("h_common_rad_derived", first.get("selected_heading_rad"))
    expected_h = oracle_wrap(candidate["joint_modes"][0]["relative_heading_rad"][ORDER[0]] + psi)
    assert observed_h == pytest.approx(expected_h, abs=1e-12)


def test_k_as_h_non_equivariance():
    state = load_base(synthetic_legacy_candidate())
    reference = {name: oracle_wrap(0.31 + 0.17 * index) for index, name in enumerate(ORDER)}
    first = evaluate(state, reference, 0.43)
    shifted = evaluate(state, reference, oracle_wrap(0.43 + math.pi / 7.0))
    assert score_vector(shifted) == pytest.approx(score_vector(first), abs=1e-12)


def test_branch_score_changes_under_common_gauge_shift():
    state = load_base(synthetic_legacy_candidate())
    reference = {name: oracle_wrap(-0.27 + 0.09 * index) for index, name in enumerate(ORDER)}
    baseline = evaluate(state, reference, -0.62)
    shifted = evaluate(state, reference, oracle_wrap(-0.62 - math.pi / 3.0))
    assert shifted["selection"]["selected_bit_vector"] == baseline["selection"]["selected_bit_vector"]
    assert score_vector(shifted) == pytest.approx(score_vector(baseline), abs=1e-12)


def test_untyped_serialization_accepted():
    with pytest.raises((TypeError, ValueError), match="(?i)(heading|semantic|typed)"):
        core.canonical_result_payload({"heading_rad": 0.25, "psi_GP_rad": 0.1})


def test_legacy_input_not_fail_closed():
    candidate = synthetic_legacy_candidate(include_representative=False)
    with pytest.raises((TypeError, ValueError), match="representative_psi_GP_rad"):
        load_base(candidate)
