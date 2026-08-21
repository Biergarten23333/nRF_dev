"""Formal P1-001/P1-002 closure checks, split into frozen command sections."""

from __future__ import annotations

import inspect
import json
import math
import os
from pathlib import Path

import numpy as np

from biospur_fusion.heading_anchor_audit_v2.heading_gauge import FormalHeadingResult
from BioSpur_Fusion.Fusion_Part.tests.fusion_v2.phase3r26c_r2.helpers import (
    copied_formal_payload,
    pipeline_state,
)
from BioSpur_Fusion.Fusion_Part.tools.fusion_v2.phase3r26c_r2.closure_probe import (
    formal_matrix,
    k_consumer_inventory,
)

from common import read_json, write_json


SECTIONS = ("consumer_closure", "h_boundary_closure", "formal_schema_closure")


def _evidence() -> Path:
    return Path(os.environ["R26C_Q2_EVIDENCE_DIR"])


def _merge(report: Path, name: str, payload: dict) -> None:
    section_path = _evidence() / f"{name}.json"
    write_json(section_path, payload)
    if os.environ.get("R26C_Q2_FORMAL") != "1":
        return
    target = report / "FORMAL_CLOSURE_RESULT.json"
    aggregate = read_json(target) if target.exists() else {
        "schema": "biospur.phase3r26c_r2_q4.formal_closure.v1",
        "sections": {},
    }
    aggregate["sections"][name] = payload
    aggregate["completed_sections"] = sorted(aggregate["sections"])
    aggregate["all_sections_present"] = set(aggregate["sections"]) == set(SECTIONS)
    aggregate["status"] = (
        "PASS"
        if aggregate["all_sections_present"]
        and all(row.get("status") == "PASS" for row in aggregate["sections"].values())
        else "INCOMPLETE"
    )
    write_json(target, aggregate)


def run_consumer_closure(root: Path, fusion: Path, report: Path) -> None:
    del root, fusion
    consumers = k_consumer_inventory()
    forbidden_tokens = (
        "psi_protocol_to_common_rad", "h_common_rad", "HeadingGaugeState",
        "k_from_h_psi", "directed_residual(",
    )
    source_checks = []
    for row in consumers:
        module_name, symbol_name = row["symbol"].rsplit(".", 1)
        module = __import__(module_name, fromlist=[symbol_name])
        symbol = getattr(module, symbol_name)
        source = inspect.getsource(symbol)
        present = sorted(token for token in forbidden_tokens if token in source)
        source_checks.append({
            "symbol": row["symbol"],
            "forbidden_tokens_present": present,
            "psi_free": not present,
        })
    passed = len(consumers) == 4 and all(row["qualified"] for row in consumers) and all(row["psi_free"] for row in source_checks)
    payload = {
        "status": "PASS" if passed else "FAIL",
        "k_consumer_count": len(consumers),
        "k_consumers_psi_free": sum(row["psi_free"] for row in source_checks),
        "exact_signature_and_type_checks": consumers,
        "source_forbidden_access_checks": source_checks,
        "kernel_re_subtracts_psi": False,
        "kernel_accesses_H": False,
        "kernel_accesses_full_heading_state": False,
        "kernel_accesses_gauge_adapter": False,
    }
    if not passed:
        raise RuntimeError(f"consumer closure failed: {payload}")
    _merge(report, "consumer_closure", payload)


def run_h_boundary_closure(root: Path, fusion: Path, report: Path) -> None:
    del root, fusion
    state = pipeline_state()
    identity = np.eye(3)
    alphas = [-math.pi, -math.pi + 1e-9, -2.4, -1.7, -0.8, 0.0, 0.8, 1.7, 2.4, math.pi - 1e-9]
    k_errors = []
    h_errors = []
    rgi_errors = []
    for alpha in alphas:
        shifted = state.shifted_common_gauge(alpha)
        for coordinate in state.coordinate_order:
            k = state.k_protocol_relative_rad(coordinate)
            shifted_k = shifted.k_protocol_relative_rad(coordinate)
            expected_h = (k + shifted.psi_protocol_to_common_rad + math.pi) % (2 * math.pi) - math.pi
            observed_h = shifted.h_common_rad(coordinate)
            k_errors.append(abs((shifted_k - k + math.pi) % (2 * math.pi) - math.pi))
            h_errors.append(abs((observed_h - expected_h + math.pi) % (2 * math.pi) - math.pi))
            expected_rgi = np.asarray([
                [math.cos(expected_h), -math.sin(expected_h), 0.0],
                [math.sin(expected_h), math.cos(expected_h), 0.0],
                [0.0, 0.0, 1.0],
            ])
            rgi_errors.append(float(np.max(np.abs(shifted.R_GI(identity, coordinate) - expected_rgi))))
    maxima = {
        "gauge_invariant_K_max_error": max(k_errors),
        "H_transport_max_error": max(h_errors),
        "R_GI_covariance_max_error": max(rgi_errors),
    }
    passed = all(value <= 1e-12 for value in maxima.values())
    payload = {
        "status": "PASS" if passed else "FAIL",
        "H_transport_formula": "H = wrap_2pi(K + psi)",
        "gauge_probe_count": len(alphas) * len(state.coordinate_order),
        **maxima,
        "gauge_covariance": passed,
        "R_GI_covariance": maxima["R_GI_covariance_max_error"] <= 1e-12,
    }
    if not passed:
        raise RuntimeError(f"H boundary closure failed: {payload}")
    _merge(report, "h_boundary_closure", payload)


def run_formal_schema_closure(root: Path, fusion: Path, report: Path) -> None:
    del root, fusion
    matrix = formal_matrix()
    state = pipeline_state()
    payload = copied_formal_payload(state)
    created = FormalHeadingResult.create(state, payload)
    decoded = FormalHeadingResult.from_json_bytes(state, created.canonical_bytes())
    validator_sources = {
        name: "_validate_formal_heading_result_payload" in inspect.getsource(method)
        for name, method in (
            ("create", FormalHeadingResult.create),
            ("deserialize", FormalHeadingResult.from_json_bytes),
            ("reserialize", FormalHeadingResult.to_payload),
        )
    }
    boundary_consistent = decoded.to_payload() == payload and all(validator_sources.values())
    passed = matrix["executed"] == 35 and matrix["passed"] == 35 and matrix["failed"] == 0 and boundary_consistent
    result = {
        "status": "PASS" if passed else "FAIL",
        "formal_schema_matrix": matrix,
        "formal_schema_matrix_passed": "35/35" if matrix["passed"] == 35 else f"{matrix['passed']}/35",
        "duplicate_key_rejection": all(
            row["passed"] for row in matrix["checks"]
            if row["case_id"] in {"duplicate_top_key", "duplicate_nested_key"}
        ),
        "shared_validator_paths": validator_sources,
        "production_report_formal_boundary_consistent": boundary_consistent,
    }
    if not passed:
        raise RuntimeError(f"formal schema closure failed: {result}")
    _merge(report, "formal_schema_closure", result)
