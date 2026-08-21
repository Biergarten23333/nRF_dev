from __future__ import annotations

import copy
import inspect
import json
import math

from biospur_fusion.heading_anchor_audit_v2 import core, pipeline
from biospur_fusion.heading_anchor_audit_v2.heading_gauge import (
    FORMAL_ALLOWED_FIELDS,
    FORMAL_CONSUMER_COUNT_FIELDS,
    FORMAL_MACHINE_GATE_FIELDS,
    FORMAL_REQUIRED_FIELDS,
    FORMAL_SOURCE_COMMIT_FIELDS,
    FORMAL_SUPPORT_FIELDS,
    FormalHeadingResult,
    HeadingGaugeValidationError,
)

from BioSpur_Fusion.Fusion_Part.tests.fusion_v2.phase3r26c_r2.helpers import (
    copied_formal_payload,
    pipeline_state,
)


def rejected(callable_object) -> bool:
    try:
        callable_object()
    except (HeadingGaugeValidationError, TypeError, ValueError):
        return True
    return False


def k_consumer_inventory() -> list[dict[str, object]]:
    rows = []
    contracts = (
        (core.production_reduced_factor_residual, ["edge", "k_protocol_relative", "measurement_protocol_relative"]),
        (core.evaluate_reduced_graph, ["edges", "k_protocol_relative"]),
        (core.directed_residual_k, ["k_protocol_relative_rad", "axis_yaw", "target_yaw_p"]),
        (pipeline._score_k_space_branch_candidate, ["k_protocol_relative", "reference", "bits"]),
    )
    for symbol, expected in contracts:
        names = list(inspect.signature(symbol).parameters)
        source = inspect.getsource(symbol)
        forbidden_names = {
            node for node in ("psi_protocol_to_common_rad", "h_common_rad", "state")
            if node in names
        }
        rows.append({
            "symbol": f"{symbol.__module__}.{symbol.__name__}",
            "file": inspect.getsourcefile(symbol),
            "line": inspect.getsourcelines(symbol)[1],
            "signature": names,
            "expected_signature": expected,
            "forbidden_parameters": sorted(forbidden_names),
            "full_state_reference": "HeadingGaugeState" in source,
            "qualified": names == expected and not forbidden_names and "HeadingGaugeState" not in source,
        })
    return rows


def formal_matrix() -> dict[str, object]:
    state = pipeline_state()
    full = copied_formal_payload(state)
    minimal = {key: copy.deepcopy(full[key]) for key in FORMAL_REQUIRED_FIELDS}
    checks: list[dict[str, object]] = []

    def record(case_id: str, passed: bool) -> None:
        checks.append({"case_id": case_id, "passed": passed})

    minimal_result = FormalHeadingResult.create(state, minimal)
    full_result = FormalHeadingResult.create(state, full)
    record("minimal_valid_exact", minimal_result.to_payload() == minimal)
    record("full_valid_exact", full_result.to_payload() == full)

    for field in sorted(FORMAL_REQUIRED_FIELDS):
        altered = copy.deepcopy(full)
        altered.pop(field)
        record(f"missing.{field}", rejected(lambda altered=altered: FormalHeadingResult.create(state, altered)))
    for field in ("unknown", "heading_deg", "selected_heading_deg", "psi_GP_rad", "common_heading", "protocol_heading"):
        altered = copy.deepcopy(full)
        altered[field] = 0.0
        record(f"unknown_top.{field}", rejected(lambda altered=altered: FormalHeadingResult.create(state, altered)))

    nested_targets = (
        ("source_commits",),
        ("machine_gates",),
        ("support",),
        ("support", "bootstrap"),
        ("support", "within_donning_block_support"),
        ("support", "between_donning_repeatability"),
        ("support", "external_accuracy"),
        ("consumer_counts",),
        ("heading_gauge_state",),
    )
    for target in nested_targets:
        altered = copy.deepcopy(full)
        cursor = altered
        for component in target:
            cursor = cursor[component]
        cursor["protocol_heading"] = 0.0
        label = ".".join(target)
        record(f"unknown_nested.{label}", rejected(lambda altered=altered: FormalHeadingResult.create(state, altered)))

    wrong_types = {
        "verdict": 1,
        "run_id": [],
        "selected_branch_count": True,
        "production_mutation_count": -1,
        "machine_gates": [],
        "support": [],
        "consumer_counts": [],
    }
    for field, bad_value in wrong_types.items():
        altered = copy.deepcopy(full)
        altered[field] = bad_value
        record(f"wrong_type.{field}", rejected(lambda altered=altered: FormalHeadingResult.create(state, altered)))

    stale_sha = copy.deepcopy(full)
    stale_sha["heading_gauge_state_sha256"] = "0" * 64
    record("stale_state_sha", rejected(lambda: FormalHeadingResult.create(state, stale_sha)))
    stale_key = copy.deepcopy(full)
    stale_key["semantic_cache_key"] = "stale"
    record("stale_cache_key", rejected(lambda: FormalHeadingResult.create(state, stale_key)))

    canonical = full_result.canonical_bytes()
    decoded = FormalHeadingResult.from_json_bytes(state, canonical)
    record("create_deserialize_reserialize_equivalence", decoded.canonical_bytes() == canonical)
    record(
        "duplicate_top_key",
        rejected(lambda: FormalHeadingResult.from_json_bytes(
            state, b'{"schema":"biospur.phase3.heading_formal_result.v2","schema":"duplicate"}'
        )),
    )
    duplicate_nested = canonical.replace(
        b'"source_commits":{',
        b'"source_commits":{"r24_implementation":"duplicate",',
        1,
    )
    record(
        "duplicate_nested_key",
        rejected(lambda: FormalHeadingResult.from_json_bytes(state, duplicate_nested)),
    )
    forged_payload = copy.deepcopy(full)
    forged_payload["unknown"] = 1
    forged = object.__new__(FormalHeadingResult)
    object.__setattr__(forged, "_heading_state", state)
    object.__setattr__(forged, "_payload_bytes", json.dumps(forged_payload).encode())
    record("reserialize_revalidates", rejected(forged.to_payload))

    return {
        "top_level_required_fields": sorted(FORMAL_REQUIRED_FIELDS),
        "top_level_allowed_fields": sorted(FORMAL_ALLOWED_FIELDS),
        "source_commit_fields": sorted(FORMAL_SOURCE_COMMIT_FIELDS),
        "machine_gate_fields": sorted(FORMAL_MACHINE_GATE_FIELDS),
        "support_fields": sorted(FORMAL_SUPPORT_FIELDS),
        "consumer_count_fields": sorted(FORMAL_CONSUMER_COUNT_FIELDS),
        "executed": len(checks),
        "passed": sum(row["passed"] for row in checks),
        "failed": sum(not row["passed"] for row in checks),
        "checks": checks,
    }


def main() -> int:
    state = pipeline_state()
    alphas = [-math.pi, -math.pi + 1e-9, -2.4, -1.7, -0.8, 0.0, 0.8, 1.7, 2.4, math.pi - 1e-9]
    h_errors = []
    for alpha in alphas:
        shifted = state.shifted_common_gauge(alpha)
        for coordinate in state.coordinate_order:
            recovered = (shifted.h_common_rad(coordinate) - shifted.psi_protocol_to_common_rad + math.pi) % (2 * math.pi) - math.pi
            expected = state.k_protocol_relative_rad(coordinate)
            h_errors.append(abs((recovered - expected + math.pi) % (2 * math.pi) - math.pi))
    consumers = k_consumer_inventory()
    formal = formal_matrix()
    payload = {
        "status": "PASS" if all(row["qualified"] for row in consumers) and max(h_errors) <= 1e-12 and formal["failed"] == 0 else "FAIL",
        "k_consumers": consumers,
        "k_consumer_count": len(consumers),
        "h_transport_probe_points": len(alphas) * len(state.coordinate_order),
        "h_transport_max_error": max(h_errors),
        "h_transport_formula": "wrap_2pi(K + psi)",
        "formal": formal,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
