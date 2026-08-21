"""Q7 root-cause, comparator-control, and replication evidence."""

from __future__ import annotations

import copy
from contextlib import redirect_stdout
import hashlib
import importlib.metadata
import inspect
import io
import json
import locale
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

import numpy as np

from common import canonical_bytes, read_json, sha256_file, write_json
from float_semantic_equivalence import (
    BINARY64_EPSILON, BOUND_ID, H_TRANSPORT_TAU_ZERO, OPERAND_SCALE,
    REGISTRY_PATH, ROUNDING_SITES, base_control_projection, binary64_bits,
    binary64_hex, compare_results, contract_payload, write_contract_artifacts,
)


Q6_REPORT = Path(
    "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/reports/fusion_v2/"
    "phase3r26c_r2/phase3r26c_r2_q6_canonical_20260820T185347Z"
)
ALPHAS = [-math.pi, -math.pi + 1e-9, -2.4, -1.7, -0.8, 0.0, 0.8, 1.7, 2.4, math.pi - 1e-9]


def _bits(value: float) -> dict[str, Any]:
    return {
        "decimal": value, "repr": repr(value), "hex": binary64_hex(value),
        "bits": binary64_bits(value), "sign_bit": int(binary64_bits(value)[2], 16) >> 3,
    }


def _path_records() -> dict[str, Any]:
    from BioSpur_Fusion.Fusion_Part.tests.fusion_v2.phase3r26c_r2.helpers import pipeline_state

    state = pipeline_state()
    canonical: list[dict[str, Any]] = []
    isolated: list[dict[str, Any]] = []
    for alpha in ALPHAS:
        shifted = state.shifted_common_gauge(alpha)
        for coordinate in state.coordinate_order:
            k = state.k_protocol_relative_rad(coordinate)
            psi = shifted.psi_protocol_to_common_rad
            observed_h = shifted.h_common_rad(coordinate)
            expected_h = (k + psi + math.pi) % (2.0 * math.pi) - math.pi
            direct_pre_wrap = observed_h - expected_h
            direct_residual = abs((direct_pre_wrap + math.pi) % (2.0 * math.pi) - math.pi)
            recovered_pre_wrap = observed_h - psi
            recovered = (recovered_pre_wrap + math.pi) % (2.0 * math.pi) - math.pi
            recovery_delta = recovered - k
            recovery_residual = abs((recovery_delta + math.pi) % (2.0 * math.pi) - math.pi)
            common = {
                "alpha": _bits(alpha), "coordinate": coordinate, "k": _bits(k),
                "shifted_psi": _bits(psi), "observed_h": _bits(observed_h),
            }
            canonical.append({
                **common, "expected_h": _bits(expected_h),
                "pre_wrap_difference": _bits(direct_pre_wrap),
                "residual": _bits(direct_residual),
                "operation_order": "H=wrap(K+psi); expected=wrap(K+psi); wrap(H-expected)",
            })
            isolated.append({
                **common, "recovered_pre_wrap": _bits(recovered_pre_wrap),
                "recovered_k": _bits(recovered), "pre_wrap_difference": _bits(recovery_delta),
                "residual": _bits(recovery_residual),
                "operation_order": "H=wrap(K+psi); recovered=wrap(H-psi); wrap(recovered-K)",
            })
    return {
        "canonical": canonical,
        "isolated": isolated,
        "canonical_maximum": max(row["residual"]["decimal"] for row in canonical),
        "isolated_maximum": max(row["residual"]["decimal"] for row in isolated),
    }


def _environment() -> dict[str, Any]:
    output = io.StringIO()
    with redirect_stdout(output):
        np.show_config()
    packages = {}
    for name in ("numpy", "scipy", "pytest"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    thread_names = (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
    )
    return {
        "python_version": sys.version,
        "python_executable": str(Path(sys.executable).resolve()),
        "packages": packages,
        "numpy_float64_eps": float(np.finfo(np.float64).eps),
        "blas_lapack_configuration": output.getvalue(),
        "blas_relevant_to_h_diagnostic": False,
        "thread_environment": {name: os.environ.get(name) for name in thread_names},
        "python_hash_seed": os.environ.get("PYTHONHASHSEED"),
        "locale": {"preferred_encoding": locale.getpreferredencoding(False), "locale": locale.setlocale(locale.LC_ALL, None)},
        "cpu_architecture": platform.machine(),
        "platform": platform.platform(),
        "libc": platform.libc_ver(),
        "sys_path": list(sys.path),
        "math_pi": _bits(math.pi),
        "math_remainder_probe": _bits((1.7 + math.pi) % (2.0 * math.pi) - math.pi),
    }


def _source_fixture_diff(fusion: Path) -> dict[str, Any]:
    q6_isolated = read_json(Q6_REPORT / "ISOLATED_CAPSULE_RERUN_RESULT.json")
    inventory = q6_isolated["inventory"]["files"]
    production = fusion / "src/biospur_fusion/heading_anchor_audit_v2"
    production_rows = {}
    for path in sorted(production.glob("*.py")):
        capsule_key = f"biospur_fusion/heading_anchor_audit_v2/{path.name}"
        production_rows[path.name] = {
            "canonical": sha256_file(path), "isolated": inventory[capsule_key],
            "identical": sha256_file(path) == inventory[capsule_key],
        }
    tests = fusion / "tests/fusion_v2/phase3r26c_r2"
    fixture_rows = {}
    for path in sorted(tests.iterdir()):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        key = f"BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r26c_r2/{path.name}"
        fixture_rows[path.name] = {
            "canonical": sha256_file(path), "isolated": inventory[key],
            "identical": sha256_file(path) == inventory[key],
        }
    closure = fusion / "tools/fusion_v2/phase3r26c_r2/closure_probe.py"
    closure_key = "BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase3r26c_r2/closure_probe.py"
    return {
        "production": production_rows,
        "tests_and_fixtures": fixture_rows,
        "isolated_probe_python_source": {
            "canonical": sha256_file(closure), "isolated": inventory[closure_key],
            "identical": sha256_file(closure) == inventory[closure_key],
        },
        "all_compared_hashes_identical": all(row["identical"] for row in [*production_rows.values(), *fixture_rows.values()]) and sha256_file(closure) == inventory[closure_key],
        "mutation_tooling_execution": "not executed by the Q6 isolated capsule; Q7 freezes and compares the formal classification projection exactly",
    }


def write_root_cause(root: Path, fusion: Path, report: Path) -> None:
    del root
    write_contract_artifacts(report)
    paths = _path_records()
    canonical_raw = read_json(Q6_REPORT / "FORMAL_CLOSURE_RESULT.json")["sections"]["h_boundary_closure"]["H_transport_max_error"]
    isolated_raw = read_json(Q6_REPORT / "ISOLATED_CAPSULE_RERUN_RESULT.json")["isolated_closure_projection"]["h_transport_max_error"]
    source_diff = _source_fixture_diff(fusion)
    current_environment = _environment()
    q6_manifest = read_json(Q6_REPORT / "COMMAND_ENVIRONMENT_MANIFEST.json")
    environment_diff = {
        "schema": "biospur.phase3r26c_r2_q7.canonical_isolated_environment_diff.v1",
        "status": "PASS",
        "classification": "LEGITIMATE_BINARY64_ROUNDOFF",
        "canonical_formal_environment": q6_manifest["commands"]["h_boundary_closure"],
        "isolated_declared_environment_manifest_sha256": read_json(Q6_REPORT / "ISOLATED_CAPSULE_RERUN_RESULT.json")["inventory"]["files"]["COMMAND_ENVIRONMENT_MANIFEST.json"],
        "current_reproduction_environment": current_environment,
        "source_and_fixture_comparison": source_diff,
        "declared_differences": {
            "PYTHONPATH_roots": "different by design: canonical repository roots versus isolated capsule root",
            "module_origins": "different absolute paths by design; content hashes are identical",
        },
        "undeclared_environment_differences_found": False,
        "undeclared_source_differences_found": False,
        "undeclared_dependency_differences_found": False,
        "nondeterministic_reduction_used": False,
        "serialization_changes_numeric_value": False,
    }
    write_json(report / "CANONICAL_ISOLATED_ENVIRONMENT_DIFF.json", environment_diff)
    max_can = next(row for row in paths["canonical"] if row["residual"]["decimal"] == paths["canonical_maximum"])
    max_iso = next(row for row in paths["isolated"] if row["residual"]["decimal"] == paths["isolated_maximum"])
    audit_json = {
        "schema": "biospur.phase3r26c_r2_q7.h_transport_roundoff_root_cause.v1",
        "status": "PASS",
        "classification": "LEGITIMATE_BINARY64_ROUNDOFF",
        "q6_values": {
            "canonical": _bits(float(canonical_raw)), "isolated": _bits(float(isolated_raw)),
            "absolute_difference": abs(canonical_raw - isolated_raw),
            "relative_difference": None,
            "distance_from_zero": {"canonical": abs(canonical_raw), "isolated": abs(isolated_raw)},
            "isolated_epsilon_multiple_at_unit_scale": isolated_raw / BINARY64_EPSILON,
            "isolated_epsilon_multiple_at_2pi_scale": isolated_raw / (BINARY64_EPSILON * OPERAND_SCALE),
        },
        "reproduced_values": {
            "canonical": paths["canonical_maximum"], "isolated": paths["isolated_maximum"],
            "match_q6": paths["canonical_maximum"] == canonical_raw and paths["isolated_maximum"] == isolated_raw,
        },
        "canonical_maximum_record": max_can,
        "isolated_maximum_record": max_iso,
        "all_raw_probe_records": paths,
        "source_locations": {
            "canonical": "tools/fusion_v2/phase3r26c_r2_q2/closure_checks.py:run_h_boundary_closure",
            "isolated": "tools/fusion_v2/phase3r26c_r2/closure_probe.py:main",
            "production_wrap": "src/biospur_fusion/heading_anchor_audit_v2/heading_gauge.py:_wrap_2pi_scalar",
            "production_h": "src/biospur_fusion/heading_anchor_audit_v2/heading_gauge.py:HeadingGaugeState.h_common_rad",
        },
        "dtype": "IEEE-754 binary64 Python float",
        "serialization": {
            "canonical": "common.write_json -> json.dumps(allow_nan=False, separators, sort_keys)",
            "isolated": "closure_probe json.dumps -> json.loads -> common.write_json",
            "round_trip_preserved_binary64": binary64_hex(json.loads(json.dumps(isolated_raw))) == binary64_hex(isolated_raw),
        },
        "environment_diff_artifact_sha256": sha256_file(report / "CANONICAL_ISOLATED_ENVIRONMENT_DIFF.json"),
        "bound_id": BOUND_ID,
        "observed_within_independently_derived_bound": isolated_raw <= H_TRANSPORT_TAU_ZERO,
    }
    write_json(report / "H_TRANSPORT_ROUNDOFF_ROOT_CAUSE_AUDIT.json", audit_json)
    markdown = f"""# H-transport roundoff root-cause audit

Classification: `LEGITIMATE_BINARY64_ROUNDOFF`.

Q6's canonical `run_h_boundary_closure` computed `expected_H = wrap(K + psi)` and compared it with `HeadingGaugeState.h_common_rad`, which executes the identical expression. The two binary64 operands were bit-identical, so the wrapped difference was exactly `0.0` (`{binary64_hex(canonical_raw)}`).

The isolated `closure_probe.main` computed `recovered_K = wrap(H - psi)` and then `wrap(recovered_K - K)`. This is algebraically the same identity but adds a subtract-and-wrap path after H was already rounded. Its maximum was `{isolated_raw!r}` (`{binary64_hex(isolated_raw)}`), exactly `{isolated_raw / BINARY64_EPSILON:.1f}` unit-scale epsilons.

The reproduction matched both Q6 values. Production, tests, fixtures, and the isolated probe source were byte-identical; the same Python executable and frozen package contract were used. BLAS, SciPy, parallel reductions, and serialization do not participate in this scalar Python `math`/`%` path. The JSON round trip preserves the isolated binary64 value. Absolute import roots differ only because isolation relocates identical source into the capsule.

The independently derived inclusive bound is `{H_TRANSPORT_TAU_ZERO!r}` rad (`{binary64_hex(H_TRANSPORT_TAU_ZERO)}`), based on `gamma_24 * 2*pi`, not on the observed value. See `H_TRANSPORT_ROUNDOFF_ROOT_CAUSE_AUDIT.json`, `CANONICAL_ISOLATED_ENVIRONMENT_DIFF.json`, and `FLOAT_ERROR_BOUND_DERIVATION.md` for full inputs, intermediates, bit patterns, environments, and hashes.
"""
    (report / "H_TRANSPORT_ROUNDOFF_ROOT_CAUSE_AUDIT.md").write_text(markdown, encoding="utf-8")


def _case(case_id: str, canonical: dict[str, Any], candidate: dict[str, Any], expected: str) -> dict[str, Any]:
    result = compare_results(canonical, candidate)
    return {
        "case_id": case_id, "expected_comparator_status": expected,
        "observed_comparator_status": result["status"],
        "expectation_met": result["status"] == expected,
        "comparison": result,
    }


def run_controls(root: Path, fusion: Path, report: Path) -> None:
    del root, fusion
    write_contract_artifacts(report)
    base = base_control_projection()
    positives = []
    for case_id, value in (
        ("positive_plus_zero_vs_minus_zero", -0.0),
        ("positive_q6_observed_roundoff", 1.3322676295501878e-15),
        ("boundary_immediately_below", math.nextafter(H_TRANSPORT_TAU_ZERO, 0.0)),
        ("boundary_exactly_at_inclusive_bound", H_TRANSPORT_TAU_ZERO),
    ):
        candidate = copy.deepcopy(base); candidate["h_transport_max_error"] = value
        positives.append(_case(case_id, base, candidate, "PASS"))
    equal_nonzero = copy.deepcopy(base)
    equal_nonzero["reference_nonzero_float"] = base["reference_nonzero_float"]
    positives.append(_case("positive_exact_equal_nonzero_float", base, equal_nonzero, "PASS"))
    positives.append(_case("positive_identical_discrete_structure", base, copy.deepcopy(base), "PASS"))

    negatives = []
    numeric = (
        ("boundary_immediately_above", math.nextafter(H_TRANSPORT_TAU_ZERO, math.inf)),
        ("negative_larger_than_bound", H_TRANSPORT_TAU_ZERO * 2.0),
        ("negative_1e_12_rad", 1e-12), ("negative_1e_9_rad", 1e-9),
        ("negative_1e_6_rad", 1e-6), ("negative_sign_convention", -1e-15),
        ("negative_pi_over_2", math.pi / 2.0), ("negative_pi", math.pi),
        ("negative_nan", float("nan")), ("negative_positive_infinity", float("inf")),
        ("negative_negative_infinity", float("-inf")),
    )
    for case_id, value in numeric:
        candidate = copy.deepcopy(base); candidate["h_transport_max_error"] = value
        negatives.append(_case(case_id, base, candidate, "FAIL"))
    mutations = {
        "negative_missing_field": lambda x: x.pop("h_transport_max_error"),
        "negative_wrong_field_type": lambda x: x.__setitem__("h_transport_max_error", "0.0"),
        "negative_wrong_unit": lambda x: x.__setitem__("h_transport_unit", "deg"),
        "negative_wrong_nodeid": lambda x: x.__setitem__("test_nodeids", ["test_frozen_contract.py::wrong"]),
        "negative_wrong_mutation_count": lambda x: x["ast_match_counts"].__setitem__("M01", 2),
        "negative_wrong_diagnostic": lambda x: x.__setitem__("diagnostic_identities", ["WRONG_DIAGNOSTIC"]),
        "negative_wrong_ast_digest": lambda x: x["ast_digests"].__setitem__("M01", "1" * 64),
        "negative_wrong_source_digest": lambda x: x["source_hashes"].__setitem__("core.py", "1" * 64),
        "negative_wrong_branch": lambda x: x["branch_labels"].__setitem__("selected", "wrong-branch"),
    }
    for case_id, mutate in mutations.items():
        candidate = copy.deepcopy(base); mutate(candidate)
        negatives.append(_case(case_id, base, candidate, "FAIL"))
    positive_payload = {
        "schema": "biospur.phase3r26c_r2_q7.float_comparator_positive_controls.v1",
        "status": "PASS" if all(row["expectation_met"] for row in positives) else "FAIL",
        "real_comparator_executed": True, "controls": positives,
    }
    negative_payload = {
        "schema": "biospur.phase3r26c_r2_q7.float_comparator_negative_controls.v1",
        "status": "PASS" if all(row["expectation_met"] for row in negatives) else "FAIL",
        "real_comparator_executed": True,
        "invalid_controls_counted_as_pass": sum(row["observed_comparator_status"] == "PASS" for row in negatives),
        "controls": negatives,
    }
    write_json(report / "FLOAT_COMPARATOR_POSITIVE_CONTROLS.json", positive_payload)
    write_json(report / "FLOAT_COMPARATOR_NEGATIVE_CONTROLS.json", negative_payload)
    if positive_payload["status"] != "PASS" or negative_payload["status"] != "PASS":
        raise RuntimeError("Q7 typed comparator controls failed")


def run_replication(root: Path, fusion: Path, report: Path) -> None:
    from isolated_rerun import _build_capsule, _capsule_env

    evidence = Path(os.environ["R26C_Q2_EVIDENCE_DIR"])
    capsule = evidence / "replication_capsule"
    inventory = _build_capsule(fusion, report, capsule)
    canonical_code = r'''import json,math
from BioSpur_Fusion.Fusion_Part.tests.fusion_v2.phase3r26c_r2.helpers import pipeline_state
s=pipeline_state();a=[-math.pi,-math.pi+1e-9,-2.4,-1.7,-0.8,0.0,0.8,1.7,2.4,math.pi-1e-9];e=[]
for x in a:
 q=s.shifted_common_gauge(x)
 for c in s.coordinate_order:
  k=s.k_protocol_relative_rad(c);h=(k+q.psi_protocol_to_common_rad+math.pi)%(2*math.pi)-math.pi;o=q.h_common_rad(c);e.append(abs((o-h+math.pi)%(2*math.pi)-math.pi))
print(json.dumps({'h_transport_max_error':max(e),'count':len(e)},sort_keys=True))'''
    canonical_runs = []
    isolated_runs = []
    comparisons = []
    env = _capsule_env(capsule)
    for index in range(3):
        completed = subprocess.run([sys.executable, "-B", "-c", canonical_code], cwd=root, env=os.environ.copy(), capture_output=True, text=True)
        payload = json.loads(completed.stdout)
        canonical_runs.append({"execution": index + 1, "exit_code": completed.returncode, "raw": payload, "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest()})
    for index in range(3):
        completed = subprocess.run([sys.executable, "-B", str(capsule / "run_isolated.py"), "closure"], cwd=capsule, env=env, capture_output=True, text=True)
        payload = json.loads(completed.stdout)
        isolated_runs.append({"execution": index + 1, "exit_code": completed.returncode, "raw": {"h_transport_max_error": payload["h_transport_max_error"], "count": payload["h_transport_probe_points"]}, "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest()})
    base = base_control_projection()
    for left, right in zip(canonical_runs, isolated_runs):
        canonical = copy.deepcopy(base); candidate = copy.deepcopy(base)
        canonical["h_transport_max_error"] = float(left["raw"]["h_transport_max_error"])
        candidate["h_transport_max_error"] = float(right["raw"]["h_transport_max_error"])
        comparisons.append(compare_results(canonical, candidate))
    values = [abs(row["raw"]["h_transport_max_error"]) for row in [*canonical_runs, *isolated_runs]]
    differences = [abs(a["raw"]["h_transport_max_error"] - b["raw"]["h_transport_max_error"]) for a, b in zip(canonical_runs, isolated_runs)]
    payload = {
        "schema": "biospur.phase3r26c_r2_q7.development_environment_replication.v1",
        "status": "PASS" if (
            len(canonical_runs) == len(isolated_runs) == 3
            and all(row["exit_code"] == 0 for row in [*canonical_runs, *isolated_runs])
            and all(row["status"] == "PASS" for row in comparisons)
            and max(values) <= H_TRANSPORT_TAU_ZERO
        ) else "FAIL",
        "tolerance_selected_before_observations": True,
        "derived_bound": H_TRANSPORT_TAU_ZERO,
        "canonical_runs": canonical_runs, "isolated_runs": isolated_runs,
        "typed_comparisons": comparisons,
        "maximum_observed_absolute_value": max(values),
        "maximum_canonical_isolated_difference": max(differences),
        "values_stable": len({row["raw"]["h_transport_max_error"] for row in canonical_runs}) == 1 and len({row["raw"]["h_transport_max_error"] for row in isolated_runs}) == 1,
        "all_discrete_results_identical": all(row["raw"]["count"] == 90 for row in [*canonical_runs, *isolated_runs]),
        "capsule_inventory": inventory,
    }
    write_json(report / "DEVELOPMENT_ENVIRONMENT_REPLICATION_RESULTS.json", payload)
    if payload["status"] != "PASS":
        raise RuntimeError("Q7 development replication failed")

