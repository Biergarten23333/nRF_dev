from __future__ import annotations

import copy
import inspect
import json
import math

import numpy as np
import pytest

from biospur_fusion.heading_anchor_audit_v2 import core, pipeline
from biospur_fusion.heading_anchor_audit_v2.heading_gauge import (
    FormalHeadingResult,
    HeadingGaugeValidationError,
)
from biospur_fusion.heading_anchor_audit_v2.heading_types import KProtocolRelativeByCoordinate

from .helpers import copied_formal_payload, load_json, pipeline_state
from .oracle_h import oracle_h_covariance, wrap_2pi
from .oracle_k import oracle_hinge_residual, oracle_k_residual
from .recursive_compare import compare_recursive


def _parameter_names(symbol: object) -> list[str]:
    return list(inspect.signature(symbol).parameters)


def test_k_kernel_signature_is_exact():
    contract = load_json("k_kernel_api_contract.json")
    assert callable(core.production_reduced_factor_residual)
    assert _parameter_names(core.production_reduced_factor_residual) == contract["exact_signature"], "K_KERNEL_ACCEPTS_FORBIDDEN_GAUGE_INPUT"


def test_reduced_graph_signature_is_psi_free():
    contract = load_json("k_kernel_api_contract.json")
    assert _parameter_names(core.evaluate_reduced_graph) == contract["dispatcher_exact_signature"], "PIPELINE_DISPATCH_ACCEPTS_FORBIDDEN_GAUGE_INPUT"


@pytest.mark.parametrize("case_index", [0, 1])
def test_k_kernel_values_match_independent_oracle(case_index: int):
    row = load_json("k_kernel_fixture.json")["cases"][case_index]
    typed_k = KProtocolRelativeByCoordinate(
        coordinate_order=row["coordinate_order"],
        k_protocol_relative_rad_by_coordinate=row["typed_k_values"],
    )
    edge = {"factor_type": row["factor_kind"], "endpoints": row["endpoints"]}
    observed = core.production_reduced_factor_residual(
        edge, typed_k, row["measurement_protocol_relative"]
    )
    if row["factor_kind"] == "PROTOCOL_AXIS_LINE":
        expected = oracle_k_residual(
            row["typed_k_values"][row["endpoints"][0]],
            row["measurement_protocol_relative"],
            row["modulus"],
        )
    else:
        expected = oracle_hinge_residual(
            row["typed_k_values"][row["endpoints"][0]],
            row["typed_k_values"][row["endpoints"][1]],
            row["measurement_protocol_relative"],
            row["modulus"],
        )
    compare_recursive(observed, expected, modes={"/": "MODULO_PI"})


def _k_projection(payload: dict) -> dict:
    candidates = payload["evaluation"]["candidates"]
    return {
        "typed_k_map": [
            [node["k_protocol_relative_rad"] for node in row["per_node_directed_distance"]]
            for row in candidates
        ],
        "scores": [row["total_unweighted_semantic_score_rad"] for row in candidates],
        "preferences": [
            [node["preference"] for node in row["per_node_directed_distance"]]
            for row in candidates
        ],
        "score_order": [
            row["bit_vector"] for row in sorted(
                candidates,
                key=lambda item: (item["total_unweighted_semantic_score_rad"], item["bit_vector"]),
            )
        ],
        "selected_decision": payload["selection"]["selected_bit_vector"],
    }


def test_pipeline_37_point_gauge_grid_preserves_k_projection():
    fixture = load_json("pipeline_gauge_fixture.json")
    state = pipeline_state()
    reference = {name: wrap_2pi(-0.4 + 0.11 * index) for index, name in enumerate(state.coordinate_order)}
    baseline = _k_projection(pipeline.evaluate_branches(state, reference).to_payload())
    assert len(fixture["alpha_grid"]) == 37
    for alpha in fixture["alpha_grid"]:
        shifted = state.shifted_common_gauge(alpha)
        projection = _k_projection(pipeline.evaluate_branches(shifted, reference).to_payload())
        compare_recursive(projection, baseline, tolerance=1e-11)


def test_h_boundary_37_point_covariance_is_separate():
    fixture = load_json("pipeline_gauge_fixture.json")
    state = pipeline_state()
    identity = np.eye(3)
    coordinate = state.coordinate_order[0]
    k_value = state.k_protocol_relative_rad(coordinate)
    for alpha in fixture["alpha_grid"]:
        shifted = state.shifted_common_gauge(alpha)
        expected = oracle_h_covariance(k_value, state.psi_protocol_to_common_rad, alpha)
        compare_recursive(shifted.psi_protocol_to_common_rad, expected["shifted_psi"], modes={"/": "MODULO_2PI"})
        compare_recursive(shifted.h_common_rad(coordinate), expected["shifted_h"], modes={"/": "MODULO_2PI"})
        compare_recursive(k_value, expected["recovered_k"], modes={"/": "MODULO_2PI"})
        expected_rotation = np.asarray([
            [math.cos(expected["shifted_h"]), -math.sin(expected["shifted_h"]), 0.0],
            [math.sin(expected["shifted_h"]), math.cos(expected["shifted_h"]), 0.0],
            [0.0, 0.0, 1.0],
        ])
        assert np.allclose(shifted.R_GI(identity, coordinate), expected_rotation, atol=1e-12)


def test_formal_exact_valid_envelope_round_trip():
    state = pipeline_state()
    payload = copied_formal_payload(state)
    result = FormalHeadingResult.create(state, payload)
    compare_recursive(result.to_payload(), payload)


def test_formal_unknown_top_level_fails_closed():
    state = pipeline_state()
    payload = copied_formal_payload(state)
    payload["heading_deg"] = 12.0
    with pytest.raises(HeadingGaugeValidationError):
        FormalHeadingResult.create(state, payload)


def test_formal_unknown_nested_fails_closed():
    state = pipeline_state()
    payload = copied_formal_payload(state)
    payload["source_commits"]["common_heading"] = "forbidden"
    with pytest.raises(HeadingGaugeValidationError):
        FormalHeadingResult.create(state, payload)


def test_formal_missing_field_fails_closed():
    state = pipeline_state()
    payload = copied_formal_payload(state)
    payload.pop("verdict")
    with pytest.raises(HeadingGaugeValidationError):
        FormalHeadingResult.create(state, payload)


def test_formal_alias_and_wrong_type_fail_closed():
    state = pipeline_state()
    alias_payload = copied_formal_payload(state)
    alias_payload["machine_gates"]["selected_heading_deg"] = 4.0
    with pytest.raises(HeadingGaugeValidationError):
        FormalHeadingResult.create(state, alias_payload)
    wrong_type = copied_formal_payload(state)
    wrong_type["verdict"] = 3
    with pytest.raises(HeadingGaugeValidationError):
        FormalHeadingResult.create(state, wrong_type)


def test_formal_deserialize_boundary_exists():
    assert hasattr(FormalHeadingResult, "from_json_bytes"), "FORMAL_DESERIALIZE_BOUNDARY_MISSING"
    state = pipeline_state()
    payload = copied_formal_payload(state)
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    result = FormalHeadingResult.from_json_bytes(state, encoded)
    compare_recursive(result.to_payload(), payload)


def test_formal_duplicate_raw_key_fails_closed():
    assert hasattr(FormalHeadingResult, "from_json_bytes"), "FORMAL_DUPLICATE_KEY_BOUNDARY_MISSING"
    state = pipeline_state()
    raw = b'{"schema":"biospur.phase3.heading_formal_result.v2","schema":"duplicate"}'
    with pytest.raises(HeadingGaugeValidationError):
        FormalHeadingResult.from_json_bytes(state, raw)


def test_formal_reserialize_revalidates():
    state = pipeline_state()
    payload = copied_formal_payload(state)
    payload["protocol_heading"] = 0.2
    forged = object.__new__(FormalHeadingResult)
    object.__setattr__(forged, "_heading_state", state)
    object.__setattr__(forged, "_payload_bytes", json.dumps(payload).encode())
    with pytest.raises(HeadingGaugeValidationError):
        forged.to_payload()


def test_formal_validator_path_is_shared():
    assert hasattr(FormalHeadingResult, "from_json_bytes"), "VALIDATOR_PATH_DIVERGENCE"
    for method in (FormalHeadingResult.create, FormalHeadingResult.from_json_bytes, FormalHeadingResult.to_payload):
        assert "_validate_formal_heading_result_payload" in inspect.getsource(method), "VALIDATOR_PATH_DIVERGENCE"
