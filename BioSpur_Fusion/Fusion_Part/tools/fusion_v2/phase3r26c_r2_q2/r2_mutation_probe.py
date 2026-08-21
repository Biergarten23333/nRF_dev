"""Independent semantic assertions for the structured R2 mutant campaign."""

from __future__ import annotations

import copy
import inspect
import json
import math

from biospur_fusion.heading_anchor_audit_v2 import core, pipeline
from biospur_fusion.heading_anchor_audit_v2.heading_gauge import (
    FormalHeadingResult,
    HeadingGaugeValidationError,
)
from biospur_fusion.heading_anchor_audit_v2.heading_types import (
    KProtocolRelativeByCoordinate,
)
from BioSpur_Fusion.Fusion_Part.tests.fusion_v2.phase3r26c_r2.helpers import (
    copied_formal_payload,
    pipeline_state,
)


def _rejected(function) -> bool:
    try:
        function()
    except (HeadingGaugeValidationError, TypeError, ValueError):
        return True
    return False


def _kernel_inputs(k_value: float = 0.35) -> tuple[dict, KProtocolRelativeByCoordinate]:
    edge = {"factor_type": "PROTOCOL_AXIS_LINE", "endpoints": ["torso", "protocol_origin"]}
    typed = KProtocolRelativeByCoordinate(
        coordinate_order=["torso", "upper_arm_left"],
        k_protocol_relative_rad_by_coordinate={"torso": k_value, "upper_arm_left": 0.1},
    )
    return edge, typed


def _assert_kernel_value(expected: float, *, measurement: float = 0.2, k_value: float = 0.35) -> None:
    edge, typed = _kernel_inputs(k_value)
    observed = core.production_reduced_factor_residual(edge, typed, measurement)
    assert abs(float(core.wrap_mod_pi(observed - expected))) <= 1e-12


def _payload():
    state = pipeline_state()
    return state, copied_formal_payload(state)


def run(case: str) -> None:
    if case == "R2K01_KERNEL_ACCEPTS_PSI":
        assert list(inspect.signature(core.production_reduced_factor_residual).parameters) == [
            "edge", "k_protocol_relative", "measurement_protocol_relative"
        ]
    elif case == "R2K02_KERNEL_SUBTRACTS_PSI":
        _assert_kernel_value(0.15)
    elif case == "R2K03_H_USED_AS_K":
        _assert_kernel_value(0.15)
    elif case == "R2K04_WRONG_MEASUREMENT_FRAME":
        _assert_kernel_value(0.15)
    elif case == "R2K05_WRONG_WRAP_DOMAIN":
        expected = float(core.wrap_mod_pi(2.4))
        edge, typed = _kernel_inputs(2.4)
        observed = core.production_reduced_factor_residual(edge, typed, 0.0)
        assert abs(observed - expected) <= 1e-12
    elif case == "R2K06_ACCEPTS_FULL_HEADING_STATE":
        assert list(inspect.signature(core.production_reduced_factor_residual).parameters) == [
            "edge", "k_protocol_relative", "measurement_protocol_relative"
        ]
    elif case == "R2K07_ACCESSES_FULL_HEADING_STATE":
        source = inspect.getsource(core.production_reduced_factor_residual)
        assert "heading_state" not in source and "HeadingGaugeState" not in source
    elif case == "R2K08_ADAPTER_LEAKS_GAUGE":
        assert list(inspect.signature(pipeline._score_k_space_branch_candidate).parameters) == [
            "k_protocol_relative", "reference", "bits"
        ]
    elif case == "R2K09_CONSUMER_GAUGE_DEPENDENCE":
        state = pipeline_state()
        reference = {
            name: float(core.wrap_2pi(-0.4 + 0.11 * index))
            for index, name in enumerate(state.coordinate_order)
        }
        baseline = pipeline.score_branch_candidate(state, reference, [0] * 9)
        shifted = pipeline.score_branch_candidate(state.shifted_common_gauge(0.41), reference, [0] * 9)
        assert shifted["total_unweighted_semantic_score_rad"] == baseline["total_unweighted_semantic_score_rad"]
        assert [row["directed_delta_rad"] for row in shifted["per_node_directed_distance"]] == [
            row["directed_delta_rad"] for row in baseline["per_node_directed_distance"]
        ]
    elif case == "R2K10_DISPATCH_ACCEPTS_PSI":
        assert list(inspect.signature(core.evaluate_reduced_graph).parameters) == [
            "edges", "k_protocol_relative"
        ]
    elif case == "R2S01_UNKNOWN_TOP_LEVEL_ACCEPTED":
        state, payload = _payload()
        payload["unknown"] = 1
        assert _rejected(lambda: FormalHeadingResult.create(state, payload))
    elif case == "R2S02_MISSING_REQUIRED_ACCEPTED":
        state, payload = _payload()
        payload.pop("verdict")
        assert _rejected(lambda: FormalHeadingResult.create(state, payload))
    elif case == "R2S03_ALIAS_ACCEPTED":
        state, payload = _payload()
        payload["consumer_counts"]["counts_by_classification"]["heading"] = 1
        assert _rejected(lambda: FormalHeadingResult.create(state, payload))
    elif case == "R2S04_WRONG_SCALAR_ACCEPTED":
        state, payload = _payload()
        payload["verdict"] = 3
        assert _rejected(lambda: FormalHeadingResult.create(state, payload))
    elif case == "R2S05_WRONG_CONTAINER_ACCEPTED":
        state, payload = _payload()
        payload["consumer_counts"]["counts_by_classification"] = []
        assert _rejected(lambda: FormalHeadingResult.create(state, payload))
    elif case == "R2S06_UNKNOWN_NESTED_ACCEPTED":
        state, payload = _payload()
        payload["source_commits"]["unknown_nested"] = "x"
        assert _rejected(lambda: FormalHeadingResult.create(state, payload))
    elif case == "R2S07_MIXED_TYPED_UNTYPED_ACCEPTED":
        state, payload = _payload()
        payload["consumer_counts"]["counts_by_classification"]["protocol_heading"] = 1
        assert _rejected(lambda: FormalHeadingResult.create(state, payload))
    elif case == "R2S08_DUPLICATE_KEY_ACCEPTED":
        state, payload = _payload()
        raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        raw = raw.replace(b'"verdict":', b'"verdict":"DUPLICATE","verdict":', 1)
        assert _rejected(lambda: FormalHeadingResult.from_json_bytes(state, raw))
    elif case == "R2S09_CREATE_BYPASSES_VALIDATOR":
        state, payload = _payload()
        payload["unknown"] = 1
        assert _rejected(lambda: FormalHeadingResult.create(state, payload))
    elif case == "R2S10_DESERIALIZE_BYPASSES_VALIDATOR":
        state, payload = _payload()
        payload["unknown"] = 1
        raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        assert _rejected(lambda: FormalHeadingResult.from_json_bytes(state, raw))
    elif case == "R2S11_RESERIALIZE_BYPASSES_VALIDATOR":
        state, payload = _payload()
        payload["unknown"] = 1
        forged = object.__new__(FormalHeadingResult)
        object.__setattr__(forged, "_heading_state", state)
        object.__setattr__(forged, "_payload_bytes", json.dumps(payload).encode())
        assert _rejected(forged.to_payload)
    elif case == "R2S12_WRONG_SUPPORT_CONTAINER_ACCEPTED":
        state, payload = _payload()
        payload["support"] = []
        assert _rejected(lambda: FormalHeadingResult.create(state, payload))
    else:
        raise AssertionError(f"unknown R2 mutation case: {case}")
