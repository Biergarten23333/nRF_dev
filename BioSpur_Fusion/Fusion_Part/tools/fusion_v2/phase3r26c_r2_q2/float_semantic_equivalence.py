"""Closed, field-typed Q7 semantic comparison for qualification evidence only."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import struct
from typing import Any

from common import canonical_bytes, read_json, sha256_file, write_json


REGISTRY_PATH = Path(__file__).with_name("float_field_registry.json")
BINARY64_EPSILON = math.ulp(1.0)
ROUNDING_SITES = 24
OPERAND_SCALE = 2.0 * math.pi
GAMMA_24 = (ROUNDING_SITES * BINARY64_EPSILON) / (
    1.0 - ROUNDING_SITES * BINARY64_EPSILON
)
H_TRANSPORT_TAU_ZERO = GAMMA_24 * max(1.0, OPERAND_SCALE)
BOUND_ID = "H_TRANSPORT_BINARY64_GAMMA24_2PI"
SEMANTIC_ZERO_TOKEN = {"semantic": "THEORETICAL_ZERO_WITHIN_FROZEN_BOUND", "bound_id": BOUND_ID}


def binary64_hex(value: float) -> str:
    return float(value).hex()


def binary64_bits(value: float) -> str:
    return f"0x{struct.unpack('>Q', struct.pack('>d', float(value)))[0]:016x}"


def raw_digest(value: dict[str, Any]) -> str:
    try:
        payload = canonical_bytes(value)
    except ValueError:
        def invalid_float_bytes(item: Any) -> Any:
            if isinstance(item, float) and not math.isfinite(item):
                return {"invalid_nonfinite_binary64": binary64_bits(item), "repr": repr(item)}
            if isinstance(item, dict):
                return {key: invalid_float_bytes(child) for key, child in item.items()}
            if isinstance(item, list):
                return [invalid_float_bytes(child) for child in item]
            return item
        payload = canonical_bytes(invalid_float_bytes(value))
    return hashlib.sha256(payload).hexdigest()


def contract_payload() -> dict[str, Any]:
    return {
        "schema": "biospur.phase3r26c_r2_q7.float_semantic_equivalence_contract.v1",
        "scope": "qualification_comparison_only",
        "registry_sha256": sha256_file(REGISTRY_PATH),
        "closed_field_registry": True,
        "unknown_fields_rejected": True,
        "missing_fields_rejected": True,
        "global_float_rounding": False,
        "raw_values_preserved": True,
        "normalization": {
            "negative_zero_to_positive_zero": "FLOAT_THEORETICAL_ZERO only",
            "within_bound_to_token": SEMANTIC_ZERO_TOKEN,
            "arbitrary_float_rounding": False,
            "field_deletion": False,
            "nan_or_infinity_acceptance": False,
        },
        "bounds": {
            BOUND_ID: {
                "field": "h_transport_max_error",
                "classification": "FLOAT_THEORETICAL_ZERO",
                "unit": "rad",
                "inclusive": True,
                "sign_convention": "nonnegative_maximum; -0.0 is semantic zero",
                "binary64_epsilon": BINARY64_EPSILON,
                "binary64_epsilon_hex": binary64_hex(BINARY64_EPSILON),
                "rounding_sites": ROUNDING_SITES,
                "gamma_n": GAMMA_24,
                "gamma_n_hex": binary64_hex(GAMMA_24),
                "operand_scale": OPERAND_SCALE,
                "operand_scale_hex": binary64_hex(OPERAND_SCALE),
                "tau_zero": H_TRANSPORT_TAU_ZERO,
                "tau_zero_hex": binary64_hex(H_TRANSPORT_TAU_ZERO),
                "formula": "gamma_24 * max(1, 2*pi), gamma_n=n*eps/(1-n*eps)",
            }
        },
    }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_digest(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()


def _validate_exact_shape(name: str, value: Any, classification: str) -> None:
    if classification == "EXACT_BOOLEAN":
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, bool) for k, v in value.items()):
            raise TypeError(f"{name} must be a string-to-boolean mapping")
    elif classification in {"EXACT_INTEGER", "EXACT_COUNT", "EXACT_EXIT_CODE"}:
        values = value.values() if isinstance(value, dict) else (value,)
        if not all(_is_int(item) and (classification != "EXACT_COUNT" or item >= 0) for item in values):
            raise TypeError(f"{name} contains a non-integer/count")
    elif classification in {"EXACT_STRING", "EXACT_ENUM"}:
        values = value.values() if isinstance(value, dict) else (value,)
        if not all(isinstance(item, str) for item in values):
            raise TypeError(f"{name} contains a non-string")
    elif classification in {"EXACT_NODEID_SET", "EXACT_ORDERED_LIST", "EXACT_DIAGNOSTIC"}:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise TypeError(f"{name} must be a list of strings")
        if classification == "EXACT_NODEID_SET" and len(value) != len(set(value)):
            raise ValueError(f"{name} contains duplicate nodeids")
    elif classification in {"EXACT_AST_DIGEST", "EXACT_SOURCE_DIGEST", "EXACT_RAW_BYTES"}:
        if not isinstance(value, dict) or not all(isinstance(k, str) and _validate_digest(v) for k, v in value.items()):
            raise TypeError(f"{name} must be a string-to-SHA256 mapping")
    elif classification == "EXACT_SCHEMA_SHAPE":
        if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, list) and all(isinstance(x, str) for x in v) for k, v in value.items()):
            raise TypeError(f"{name} must be a string-to-string-list mapping")
    else:
        raise ValueError(f"unsupported exact classification: {classification}")


def _normalized_exact(value: Any, classification: str) -> Any:
    if classification == "EXACT_NODEID_SET":
        return sorted(value)
    return copy.deepcopy(value)


def compare_results(canonical: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    registry = read_json(REGISTRY_PATH)
    fields = registry["fields"]
    expected_keys = set(fields)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    normalized_canonical: dict[str, Any] = {}
    normalized_candidate: dict[str, Any] = {}
    for side, value in (("canonical", canonical), ("candidate", candidate)):
        if not isinstance(value, dict):
            errors.append(f"{side}:wrong_top_level_type")
            continue
        missing = sorted(expected_keys - set(value))
        extra = sorted(set(value) - expected_keys)
        if missing:
            errors.append(f"{side}:missing:{','.join(missing)}")
        if extra:
            errors.append(f"{side}:unknown:{','.join(extra)}")
    if errors:
        return _comparison_result(canonical, candidate, rows, errors, normalized_canonical, normalized_candidate)
    for name, rule in fields.items():
        classification = rule["classification"]
        left = canonical[name]
        right = candidate[name]
        row: dict[str, Any] = {"field": name, "classification": classification}
        try:
            if classification == "FLOAT_THEORETICAL_ZERO":
                if type(left) is not float or type(right) is not float:
                    raise TypeError("theoretical-zero values must be binary64 Python floats, not integers or booleans")
                if not math.isfinite(left) or not math.isfinite(right):
                    raise ValueError("NaN and infinity are forbidden")
                if left < 0.0 or right < 0.0:
                    raise ValueError("nonnegative maximum sign convention violated")
                left_ok = abs(left) <= H_TRANSPORT_TAU_ZERO
                right_ok = abs(right) <= H_TRANSPORT_TAU_ZERO
                passed = left_ok and right_ok
                relative = None if left == right == 0.0 else (
                    abs(left - right) / max(abs(left), abs(right))
                )
                row.update({
                    "canonical_raw_value": left,
                    "candidate_raw_value": right,
                    "canonical_binary64_hex": binary64_hex(left),
                    "candidate_binary64_hex": binary64_hex(right),
                    "canonical_binary64_bits": binary64_bits(left),
                    "candidate_binary64_bits": binary64_bits(right),
                    "absolute_difference": abs(left - right),
                    "relative_difference": relative,
                    "field_specific_tolerance": H_TRANSPORT_TAU_ZERO,
                    "inclusive_bound": True,
                    "canonical_within_bound": left_ok,
                    "candidate_within_bound": right_ok,
                    "comparison_result": "PASS" if passed else "FAIL",
                })
                if passed:
                    normalized_canonical[name] = copy.deepcopy(SEMANTIC_ZERO_TOKEN)
                    normalized_candidate[name] = copy.deepcopy(SEMANTIC_ZERO_TOKEN)
                else:
                    normalized_canonical[name] = left
                    normalized_candidate[name] = right
            elif classification in {
                "EXACT_BOOLEAN", "EXACT_INTEGER", "EXACT_STRING", "EXACT_ENUM",
                "EXACT_NODEID_SET", "EXACT_ORDERED_LIST", "EXACT_AST_DIGEST",
                "EXACT_SOURCE_DIGEST", "EXACT_SCHEMA_SHAPE", "EXACT_COUNT",
                "EXACT_EXIT_CODE", "EXACT_DIAGNOSTIC", "EXACT_RAW_BYTES",
            }:
                _validate_exact_shape(name, left, classification)
                _validate_exact_shape(name, right, classification)
                if "allowed" in rule and (left not in rule["allowed"] or right not in rule["allowed"]):
                    raise ValueError(f"value not in frozen enum: {rule['allowed']}")
                normalized_left = _normalized_exact(left, classification)
                normalized_right = _normalized_exact(right, classification)
                passed = normalized_left == normalized_right
                normalized_canonical[name] = normalized_left
                normalized_candidate[name] = normalized_right
                row.update({"comparison_result": "PASS" if passed else "FAIL", "exact": True})
            elif classification == "FLOAT_EXACT":
                if type(left) is not float or type(right) is not float or not math.isfinite(left) or not math.isfinite(right):
                    raise TypeError("FLOAT_EXACT requires finite binary64 Python floats")
                passed = binary64_bits(left) == binary64_bits(right)
                normalized_canonical[name] = left
                normalized_candidate[name] = right
                row.update({"comparison_result": "PASS" if passed else "FAIL", "exact": True})
            else:
                raise ValueError(f"unsupported field classification: {classification}")
        except (TypeError, ValueError) as exc:
            passed = False
            row.update({"comparison_result": "FAIL", "validation_error": str(exc)})
        if not passed:
            errors.append(f"{name}:{row.get('validation_error', 'mismatch')}")
        rows.append(row)
    return _comparison_result(canonical, candidate, rows, errors, normalized_canonical, normalized_candidate)


def _comparison_result(
    canonical: dict[str, Any], candidate: dict[str, Any], rows: list[dict[str, Any]],
    errors: list[str], normalized_canonical: dict[str, Any], normalized_candidate: dict[str, Any],
) -> dict[str, Any]:
    raw_canonical = raw_digest(canonical) if isinstance(canonical, dict) else None
    raw_candidate = raw_digest(candidate) if isinstance(candidate, dict) else None
    norm_can = raw_digest(normalized_canonical)
    norm_cand = raw_digest(normalized_candidate)
    status = "PASS" if not errors and norm_can == norm_cand else "FAIL"
    return {
        "schema": "biospur.phase3r26c_r2_q7.typed_float_comparison.v1",
        "status": status,
        "registry_sha256": sha256_file(REGISTRY_PATH),
        "contract_sha256": hashlib.sha256(canonical_bytes(contract_payload())).hexdigest(),
        "field_results": rows,
        "errors": errors,
        "raw_result_digests": {"canonical": raw_canonical, "candidate": raw_candidate},
        "semantic_normalized_digests": {"canonical": norm_can, "candidate": norm_cand},
        "semantic_normalized_digest_identical": norm_can == norm_cand,
        "raw_values_preserved": True,
        "normalization_was_field_typed": True,
        "normalized_canonical": normalized_canonical,
        "normalized_candidate": normalized_candidate,
    }


def base_control_projection() -> dict[str, Any]:
    zero = "0" * 64
    return {
        "schema": "biospur.phase3r26c_r2_q7.semantic_projection.v1",
        "test_nodeids": ["test_frozen_contract.py::test_a"],
        "green_counts": {"tests": 15, "passes": 15, "failures": 0, "skipped": 0, "errors": 0},
        "exit_codes": {"green": 0, "closure": 0, "r1": 0, "r2": 0},
        "diagnostic_identities": ["EXPECTED_DIAGNOSTIC"],
        "mutation_ids": ["M01", "R2K01"],
        "mutation_classifications": ["VALID_SEMANTIC_KILL", "VALID_SEMANTIC_KILL"],
        "ast_match_counts": {"M01": 1, "R2K01": 1},
        "ast_digests": {"M01": zero, "R2K01": zero},
        "source_hashes": {"core.py": zero},
        "fixture_raw_bytes_sha256": {"fixture.json": zero},
        "schema_keys": {"formal": ["schema", "status"]},
        "closure_counts": {"k_consumers": 4, "schema_executed": 35, "schema_passed": 35},
        "branch_labels": {"selected": "frozen-branch"},
        "boolean_gates": {"green": True, "closure": True},
        "reference_nonzero_float": math.pi,
        "h_transport_unit": "rad",
        "h_transport_max_error": 0.0,
    }


def write_contract_artifacts(report: Path) -> None:
    write_json(report / "FLOAT_FIELD_REGISTRY.json", read_json(REGISTRY_PATH))
    write_json(report / "FLOAT_SEMANTIC_EQUIVALENCE_CONTRACT.json", contract_payload())
    derivation = f"""# Binary64 H-transport error-bound derivation

The Q6 canonical diagnostic evaluates `observed_H` and `wrap(K + psi)` through the same binary64 expression, then wraps their difference. Those operands are bit-identical, so it reports exact `+0.0`.

The isolated diagnostic instead evaluates `wrap(wrap(K + psi) - psi)`, then compares that reconstructed K with K through another wrapped difference. Under the explicit remainder model `x % y = x - floor(x/y)*y`, each wrap contributes at most five binary64 rounding sites (add pi, divide, multiply, subtract, subtract pi); each pre-wrap sum/subtraction contributes one. Counting the shifted-psi, H, recovery, and final residual paths gives no more than {ROUNDING_SITES} sites. `floor` is discrete and exact away from a branch boundary; all frozen probes are separately checked not to straddle a boundary.

For sequential binary64 round-to-nearest operations, `gamma_n = n*eps/(1-n*eps)`. With `n={ROUNDING_SITES}`, `eps={BINARY64_EPSILON!r}` (`{binary64_hex(BINARY64_EPSILON)}`), and the largest angular scale `2*pi={OPERAND_SCALE!r}` rad, the frozen inclusive bound is:

`tau_zero = gamma_24 * max(1, 2*pi) = {H_TRANSPORT_TAU_ZERO!r} rad` (`{binary64_hex(H_TRANSPORT_TAU_ZERO)}`).

This is derived independently of the observed maximum and is {1e-12 / H_TRANSPORT_TAU_ZERO:.6f} times smaller than `1e-12` rad. The Q6 isolated value is exactly six epsilons and lies within the bound. The comparator additionally requires a finite float, the exact `rad` unit, and a nonnegative maximum (with `-0.0` accepted as semantic zero).
"""
    (report / "FLOAT_ERROR_BOUND_DERIVATION.md").write_text(derivation, encoding="utf-8")


def semantic_projection(
    *, report: Path, fusion: Path, green: dict[str, Any], closure: dict[str, Any],
    r1: dict[str, Any], r2: dict[str, Any], source_hashes: dict[str, str],
    fixture_hashes: dict[str, str], exit_codes: dict[str, int],
) -> dict[str, Any]:
    """Build the sole closed projection accepted by the Q7 comparator."""
    r1_rows = r1["mutants"]
    r2_rows = r2["mutants"]
    rows = [*r1_rows, *r2_rows]
    mutation_ids = [row["mutant_id"] for row in rows]
    classifications = [row["classification"] for row in rows]
    ast_counts: dict[str, int] = {}
    ast_digests: dict[str, str] = {}
    diagnostics: list[str] = []
    for row in rows:
        mutant_id = row["mutant_id"]
        structural = row.get("structural_mutation", row)
        count = structural.get("exact_match_count", structural.get("anchor_count_actual"))
        ast_counts[mutant_id] = int(count)
        for label in ("original_ast_digest", "mutated_ast_digest"):
            digest = structural.get(label)
            if digest is not None:
                ast_digests[f"{mutant_id}:{label}"] = digest
        diagnostic = row.get("expected_diagnostic") or row.get("semantic_marker")
        if diagnostic:
            diagnostics.append(str(diagnostic))
    red = read_json(report / "FROZEN_RED_RESULT.json") if (report / "FROZEN_RED_RESULT.json").exists() else None
    if red:
        diagnostics.extend(
            json.dumps(value, sort_keys=True, separators=(",", ":"))
            for _, value in sorted(red["comparison"]["expected_diagnostics"].items())
        )
    formal = closure.get("formal", {})
    tests = int(green["tests"])
    passes = int(green["passes"])
    nodeids = list(green["nodeids"])
    return {
        "schema": "biospur.phase3r26c_r2_q7.semantic_projection.v1",
        "test_nodeids": nodeids,
        "green_counts": {
            "tests": tests, "passes": passes,
            "failures": int(green.get("failure_count", 0)),
            "skipped": int(green.get("skip_count", 0)),
            "errors": int(green.get("error_count", 0)),
        },
        "exit_codes": exit_codes,
        "diagnostic_identities": diagnostics,
        "mutation_ids": mutation_ids,
        "mutation_classifications": classifications,
        "ast_match_counts": ast_counts,
        "ast_digests": ast_digests,
        "source_hashes": source_hashes,
        "fixture_raw_bytes_sha256": fixture_hashes,
        "schema_keys": {
            "projection": sorted(read_json(REGISTRY_PATH)["fields"]),
            "closure": sorted(closure),
            "formal": sorted(formal),
        },
        "closure_counts": {
            "k_consumers": int(closure["k_consumer_count"]),
            "k_consumers_psi_free": int(closure["k_consumers_psi_free"]),
            "schema_executed": int(formal["executed"]),
            "schema_passed": int(formal["passed"]),
        },
        "branch_labels": {
            "heading_state": "K_PROTOCOL_RELATIVE_PLUS_PSI_PROTOCOL_TO_COMMON",
            "wrap": "[-pi,pi)",
        },
        "boolean_gates": {
            "green": tests == passes == 15,
            "closure": closure["k_consumer_count"] == closure["k_consumers_psi_free"] == 4,
            "schema": formal["executed"] == formal["passed"] == 35,
            "r1": len(r1_rows) == 14 and all(row["classification"] == "VALID_SEMANTIC_KILL" for row in r1_rows),
            "r2": len(r2_rows) == 22 and all(row["classification"] == "VALID_SEMANTIC_KILL" for row in r2_rows),
        },
        "reference_nonzero_float": math.pi,
        "h_transport_unit": "rad",
        "h_transport_max_error": float(closure["h_transport_max_error"]),
    }
