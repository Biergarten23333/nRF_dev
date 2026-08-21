"""Frozen fail-closed controls for environment, tracing, and mutant validity."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import sys

from environment_gate import (
    EnvironmentGateError,
    assert_collection_matches,
    assert_contract_matches,
    assert_runtime_matches,
)
from mutation_runner import classify_mutant
from common import read_json, write_json


def _detected(function) -> tuple[bool, str]:
    try:
        function()
    except (EnvironmentGateError, PermissionError) as exc:
        return True, type(exc).__name__
    return False, "NOT_DETECTED"


def run_negative_controls(root: Path, fusion: Path, report: Path) -> None:
    del root, fusion
    manifest = report / "COMMAND_ENVIRONMENT_MANIFEST.json"
    if manifest.exists():
        frozen = read_json(manifest)["commands"]["negative_controls"]
    else:
        attempts = sorted(
            (report / "development/preflight/negative_controls").glob(
                "attempt_*/COMMAND_RESULT.json"
            )
        )
        if not attempts:
            raise RuntimeError("negative controls require a qualified preflight")
        preflight = read_json(attempts[-1])
        if not preflight.get("qualified") or not preflight.get("runtime_preflight"):
            raise RuntimeError("latest negative-control preflight is not qualified")
        frozen = {
            **preflight["command_environment"],
            "runtime_preflight": preflight["runtime_preflight"],
        }
    runtime = frozen["runtime_preflight"]
    rows = []

    def contract_case(case_id: str, field: str, value: object) -> None:
        actual = copy.deepcopy(frozen)
        actual[field] = value
        detected, error = _detected(lambda: assert_contract_matches(frozen, actual))
        rows.append({"case_id": case_id, "detected": detected, "error": error})

    contract_case("wrong_pythonpath", "PYTHONPATH", frozen["PYTHONPATH"] + os.pathsep + "/wrong")
    contract_case("wrong_cwd", "cwd", "/wrong/cwd")
    contract_case("wrapper_sha_drift", "wrapper_sha256", "0" * 64)
    contract_case("environment_sha_drift", "environment_sha256", "0" * 64)

    wrong_origin = copy.deepcopy(runtime)
    wrong_origin["module_origins"][0]["module_file"] = "/outside/canonical/module.py"
    detected, error = _detected(lambda: assert_runtime_matches(runtime, wrong_origin))
    rows.append({"case_id": "wrong_module_origin", "detected": detected, "error": error})

    nodeids = runtime["pytest"]["collected_nodeids"]
    detected, error = _detected(lambda: assert_collection_matches(nodeids, [*nodeids, "drift::nodeid"]))
    rows.append({"case_id": "collection_drift", "detected": detected, "error": error})

    classifier_cases = {
        "invalid_syntax": classify_mutant(syntax_ok=False, import_ok=False, collection_ok=False, semantic_marker_ok=False),
        "invalid_import": classify_mutant(syntax_ok=True, import_ok=False, collection_ok=False, semantic_marker_ok=False),
        "invalid_collection": classify_mutant(syntax_ok=True, import_ok=True, collection_ok=False, semantic_marker_ok=False),
    }
    rows.append({
        "case_id": "invalid_mutant_classifier",
        "detected": all("NOT_A_KILL" in value for value in classifier_cases.values()),
        "classifications": classifier_cases,
    })

    detected, error = _detected(lambda: sys.audit("subprocess.Popen", "strace", ["strace"], None, None))
    rows.append({"case_id": "nested_ptrace", "detected": detected, "error": error})

    forbidden = "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/datasets/blocked/17_final_still/numeric.bin"
    detected, error = _detected(lambda: open(forbidden, "rb"))
    rows.append({
        "case_id": "forbidden_path_access",
        "detected": detected,
        "error": error,
        "operation_completed": False,
        "numeric_read": False,
    })

    payload = {
        "schema": "biospur.phase3r26c_r2_q4.negative_controls.v1",
        "status": "PASS" if all(row["detected"] for row in rows) else "FAIL",
        "expected_failures_are_controls_not_qualification_failures": True,
        "control_count": len(rows),
        "detected_count": sum(row["detected"] for row in rows),
        "controls": rows,
    }
    target = (
        report / "NEGATIVE_CONTROL_RESULTS.json"
        if os.environ.get("R26C_Q2_FORMAL") == "1"
        else Path(os.environ["R26C_Q2_EVIDENCE_DIR"]) / "NEGATIVE_CONTROL_RESULTS.json"
    )
    write_json(target, payload)
    if payload["status"] != "PASS":
        raise RuntimeError(f"negative controls failed: {payload}")
