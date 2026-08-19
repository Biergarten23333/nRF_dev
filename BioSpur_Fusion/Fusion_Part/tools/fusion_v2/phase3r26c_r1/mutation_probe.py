#!/usr/bin/env python3
"""Targeted independent regression probe for one production-source mutant."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np

from biospur_fusion.heading_anchor_audit_v2 import core, pipeline
from biospur_fusion.heading_anchor_audit_v2 import heading_gauge as gauge
from biospur_fusion.heading_anchor_audit_v2.heading_types import KProtocolRelativeByCoordinate
from biospur_fusion.heading_anchor_audit_v2.qualification import (
    future_candidate_payload,
    oracle_rz,
    oracle_wrap_2pi,
    synthetic_reference,
    synthetic_state,
)


def _attest(module: object) -> None:
    imported = Path(module.__file__).resolve()  # type: ignore[attr-defined]
    root = Path(os.environ["EXPECTED_PACKAGE_ROOT"]).resolve()
    actual_sha = hashlib.sha256(imported.read_bytes()).hexdigest()
    expected_sha = os.environ["EXPECTED_MODULE_SHA256"]
    row = {
        "imported_module.__file__": str(imported),
        "imported_module_source_sha256": actual_sha,
        "mutant_root_path": str(root),
    }
    print(json.dumps(row, sort_keys=True), flush=True)
    assert imported == root / "biospur_fusion" / "heading_anchor_audit_v2" / imported.name
    assert actual_sha == expected_sha
    if os.environ.get("EXPECT_MUTANT") == "1":
        assert str(root).startswith("/tmp/")


def _expect_rejection(function) -> None:
    try:
        function()
    except (TypeError, ValueError):
        return
    raise AssertionError("invalid semantic input was accepted")


def _valid_cache(state) -> dict:
    return {
        "schema": "biospur.phase3.heading_gauge_independent_cache.v1",
        "semantic_cache_key": gauge.HEADING_GAUGE_CACHE_KEY,
        "heading_gauge_state_sha256": state.payload_sha256(),
        "source_solution_sha256": state.source_solution_sha256,
        "source_schema": state.source_schema,
        "migration_id": state.migration_id,
        "coordinate_order": list(state.coordinate_order),
    }


def run(case: str) -> None:
    state = synthetic_state()
    reference = synthetic_reference()
    coordinate = state.coordinate_order[0]
    if case in {"M01_H_MISSING_PSI", "M02_H_DOUBLE_PSI"}:
        expected = oracle_wrap_2pi(state.k_protocol_relative_rad(coordinate) + state.psi_protocol_to_common_rad)
        assert state.h_common_rad(coordinate) == expected
    elif case == "M03_K_RESIDUAL_EXTRA_MINUS_PSI":
        row = pipeline.score_branch_candidate(state, reference, [0] * 9)["per_node_directed_distance"][0]
        target = pipeline.TARGETS[coordinate]["azimuth"]
        expected = core.directed_residual_k(state.k_protocol_relative_rad(coordinate), reference[coordinate], target)
        assert row["directed_delta_rad"] == expected
    elif case == "M04_H_RESIDUAL_MISSING_MINUS_PSI":
        h, axis, psi, target = 0.7, -0.4, 0.6, -0.2
        assert abs(oracle_wrap_2pi(
            core.directed_residual(h, axis, psi, target)
            - oracle_wrap_2pi(h + axis - psi - target)
        )) < 1e-12
    elif case == "M05_K_TREATED_AS_H":
        row = pipeline.score_branch_candidate(state, reference, [0] * 9)["per_node_directed_distance"][1]
        segment = state.coordinate_order[1]
        assert row["candidate_axis_azimuth_in_P_rad"] == oracle_wrap_2pi(
            state.k_protocol_relative_rad(segment) + reference[segment]
        )
    elif case == "M06_H_TREATED_AS_K":
        matrix = oracle_rz(-0.31)
        assert np.allclose(state.R_GI(matrix, coordinate), oracle_rz(state.h_common_rad(coordinate)) @ matrix)
    elif case == "M07_SERIALIZER_DROPS_PSI":
        assert "psi_protocol_to_common_rad" in state.to_payload()
        gauge.HeadingGaugeState.from_payload(state.to_payload())
    elif case == "M08_SERIALIZER_DROPS_VERSION":
        assert state.to_payload()["semantic_version"] == gauge.HEADING_GAUGE_SEMANTIC_VERSION
        gauge.HeadingGaugeState.from_payload(state.to_payload())
    elif case == "M09_WRAP_2PI_TO_MOD_PI":
        value = core.directed_residual_k(2.8, 0.7, -0.2)
        assert value == oracle_wrap_2pi(2.8 + 0.7 + 0.2)
    elif case == "M10_BRANCH_DEPENDS_ON_GAUGE":
        bits = [0] * 9
        first = pipeline.score_branch_candidate(state, reference, bits)["total_unweighted_semantic_score_rad"]
        shifted = pipeline.score_branch_candidate(state.shifted_common_gauge(0.61), reference, bits)["total_unweighted_semantic_score_rad"]
        assert shifted == first
    elif case == "M11_STALE_CACHE_ACCEPTED":
        stale = _valid_cache(state)
        stale["semantic_cache_key"] = "stale"
        _expect_rejection(lambda: gauge.validate_semantic_cache(stale, state))
    elif case == "M12_LEGACY_CANDIDATE_ACCEPTED":
        payload = future_candidate_payload(state)
        payload["schema"] = "biospur.phase3r26.nine_heading_conditional_candidate.v1"
        _expect_rejection(lambda: gauge.validate_future_candidate_payload(payload, state))
    elif case == "M13_INCONSISTENT_H_ACCEPTED":
        payload = future_candidate_payload(state)
        payload["nodes"][0]["h_common_rad_derived"] += 0.2
        _expect_rejection(lambda: gauge.validate_future_candidate_payload(payload, state))
    elif case == "M14_COORDINATE_SWAP_ACCEPTED":
        order = list(state.coordinate_order)
        order[0], order[1] = order[1], order[0]
        values = {name: state.k_protocol_relative_rad(name) for name in order}
        typed = KProtocolRelativeByCoordinate(
            coordinate_order=order, k_protocol_relative_rad_by_coordinate=values
        )
        _expect_rejection(lambda: gauge.HeadingGaugeState(
            coordinate_order=order,
            k_protocol_relative=typed,
            psi_protocol_to_common_rad=state.psi_protocol_to_common_rad,
            source_solution_sha256=state.source_solution_sha256,
            source_schema=state.source_schema,
            migration_id=state.migration_id,
        ))
    else:
        raise AssertionError(case)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    args = parser.parse_args()
    target = {
        "M03_K_RESIDUAL_EXTRA_MINUS_PSI": pipeline,
        "M05_K_TREATED_AS_H": pipeline,
        "M10_BRANCH_DEPENDS_ON_GAUGE": pipeline,
        "M04_H_RESIDUAL_MISSING_MINUS_PSI": core,
        "M09_WRAP_2PI_TO_MOD_PI": core,
    }.get(args.case, gauge)
    _attest(target)
    run(args.case)
    print(json.dumps({"case": args.case, "targeted_regression": "PASS"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
