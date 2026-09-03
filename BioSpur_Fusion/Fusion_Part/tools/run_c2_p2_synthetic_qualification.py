#!/usr/bin/env python3
"""Run one sealed, payload-free P2 qualification with append-only evidence."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import traceback
from typing import Any, Mapping

import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN_RELATIVE = Path("logs/c2_basis_progressive_20260829T102836Z")
AMENDMENT_RELATIVE = RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_010.json"
SEAL_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_010.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _semantic_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _write_new_immutable(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(_jsonable(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _binding(relative: Path) -> dict[str, str]:
    return {"path": str(relative), "sha256": _sha(WORKSPACE / relative)}


def _next_attempt_index() -> int:
    run_dir = WORKSPACE / RUN_RELATIVE
    stems = (
        "P2_PREFIT_SYNTHETIC_QUALIFICATION_RUNNING",
        "P2_PREFIT_SYNTHETIC_QUALIFICATION_ATTEMPT",
        "P2_PREFIT_SENSOR_NUMERICAL_MUTATION_GATE",
        "P2_PREFIT_ARCHITECTURE_MUTATION_GATE",
        "P2_PREFIT_QUALIFIED_TEST_GATE",
        "P2_PREFIT_SYNTHETIC_GATE",
    )
    for index in range(1, 1000):
        if not any((run_dir / f"{stem}_{index:03d}.json").exists() for stem in stems):
            return index
    raise RuntimeError("synthetic qualification attempt namespace exhausted")


def _validated_authority() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    amendment_path = WORKSPACE / AMENDMENT_RELATIVE
    seal_path = WORKSPACE / SEAL_RELATIVE
    if not amendment_path.is_file() or not seal_path.is_file():
        raise RuntimeError("active successor prefit amendment/seal do not exist")
    if amendment_path.stat().st_mode & 0o222 or seal_path.stat().st_mode & 0o222:
        raise RuntimeError("prefit amendment and seal must be immutable")
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    if (
        amendment.get("schema") != "biospur-c2-active-parameter-registry-prefit-amendment-v2"
        or seal.get("schema") != "biospur-c2-p2-prefit-registry-seal-v2"
        or seal.get("amendment") != _binding(AMENDMENT_RELATIVE)
        or seal.get("amendment_append_only_parent") != amendment.get("append_only_parent")
        or seal.get("settings_semantic_sha256") != _semantic_sha(amendment["effective_settings"])
        or amendment.get("settings_semantic_sha256") != seal.get("settings_semantic_sha256")
        or seal.get("qualified_source_hashes") != amendment.get("qualified_source_hashes")
        or seal.get("synthetic_qualification_status")
        != "NOT_RUN_SUCCESSOR_PREFIT_SEAL_REAL_FIT_BLOCKED"
        or seal.get("real_fit_authorized_after_registry_alone") is not False
        or seal.get("heldout_opened") is not False
    ):
        raise RuntimeError("prefit amendment/seal authority is inconsistent")
    source_hashes = seal.get("qualified_source_hashes")
    if not isinstance(source_hashes, Mapping):
        raise RuntimeError("prefit source closure must be a path-to-SHA mapping")
    mandatory = set(amendment["effective_settings"]["execution_contract"]["mandatory_qualified_source_paths"])
    if set(source_hashes) != mandatory:
        raise RuntimeError("prefit source closure differs from the exact mandatory path set")
    for relative, expected in source_hashes.items():
        path = (WORKSPACE / relative).resolve()
        path.relative_to(WORKSPACE)
        if not path.is_file() or _sha(path) != expected:
            raise RuntimeError(f"sealed qualification source changed: {relative}")
    common = {
        "execution_role": "SYNTHETIC_QUALIFICATION",
        "prefit_registry_seal": _binding(SEAL_RELATIVE),
        "settings_semantic_sha256": seal["settings_semantic_sha256"],
        "qualified_source_hashes": dict(source_hashes),
        "real_capture_rows_opened": False,
        "external_holdout_opened": False,
    }
    return amendment, seal, common


def _validate_sensor_ledger(settings: Mapping[str, Any], ledger: Mapping[str, Any]) -> None:
    expected = set(settings["synthetic"]["mandatory_sensor_and_numerical_mutations"])
    if set(ledger) != expected:
        raise RuntimeError("qualification sensor mutation ledger differs from registered exact names")
    for name, row in ledger.items():
        if (
            row.get("coverage_class") != "EXECUTED_OWNER_LEVEL"
            or not row.get("owner_call_path")
            or not all(key in row for key in ("injected", "expected", "observed", "pass"))
        ):
            raise RuntimeError(f"sensor mutation lacks owner-level evidence fields: {name}")


def _validate_architecture_ledger(settings: Mapping[str, Any], document: Mapping[str, Any]) -> None:
    expected = set(settings["synthetic"]["mandatory_architecture_negative_mutations"])
    ledger = document.get("mutations")
    if not isinstance(ledger, Mapping) or set(ledger) != expected:
        raise RuntimeError("qualification architecture ledger differs from registered exact names")
    if document.get("declarative_only_counted_as_pass") is not False:
        raise RuntimeError("architecture qualification attempted to count declarative coverage")
    for name, row in ledger.items():
        if (
            row.get("coverage_class") != "EXECUTED_OWNER_LEVEL"
            or not row.get("owner")
            or not row.get("callable")
            or row.get("expected_rejection") != name
            or "observed_rejection" not in row
            or "caught" not in row
        ):
            raise RuntimeError(f"architecture mutation lacks owner-path rejection evidence: {name}")


def _run_exact_qualified_test(
    *,
    common: Mapping[str, Any],
    attempt_index: int,
    artifact_path: Path,
    run_tmp: Path,
) -> Mapping[str, Any]:
    relative_tests = (
        "tests/v0/test_c2_p2_prefit_owners.py",
        "tests/v0/test_c2_progressive_range_reader.py",
    )
    test_sources = []
    for relative_test in relative_tests:
        expected_hash = common["qualified_source_hashes"].get(relative_test)
        test_path = WORKSPACE / relative_test
        if expected_hash is None or _sha(test_path) != expected_hash:
            raise RuntimeError(
                f"exact qualified test is absent from or differs from the source closure: {relative_test}"
            )
        test_sources.append({"path": relative_test, "sha256": expected_hash})
    base_temp = run_tmp / f"qualified_pytest_{attempt_index:03d}"
    command = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(base_temp),
        "-q",
        *relative_tests,
    ]
    environment = os.environ.copy()
    environment.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": (
            "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/src:"
            "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part"
        ),
        "TMPDIR": str(run_tmp),
    })
    started = _utc_now()
    completed = subprocess.run(
        command,
        cwd=WORKSPACE,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    document = {
        "schema": "biospur-c2-p2-qualified-owner-test-gate-v1",
        **dict(common),
        "attempt_index": int(attempt_index),
        "started_utc": started,
        "completed_utc": _utc_now(),
        "test_sources": test_sources,
        "command": command,
        "working_directory": str(WORKSPACE),
        "environment": {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": environment["PYTHONPATH"],
            "TMPDIR": str(run_tmp.relative_to(WORKSPACE)),
            "pytest_cacheprovider_disabled": True,
            "pytest_basetemp": str(base_temp.relative_to(WORKSPACE)),
        },
        "returncode": int(completed.returncode),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "pass": completed.returncode == 0,
        "status": "PASS" if completed.returncode == 0 else "FAIL_TEST_RESULT_PRESERVED",
        "real_capture_rows_opened": False,
        "external_holdout_opened": False,
        "real_fit_authorized": False,
    }
    _write_new_immutable(artifact_path, document)
    return document


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("synthetic qualification must run only from canonical Fusion_Part")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    run_tmp = (WORKSPACE / RUN_RELATIVE / "tmp").resolve()
    run_tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(run_tmp)
    index = _next_attempt_index()
    run_dir = WORKSPACE / RUN_RELATIVE
    running_relative = RUN_RELATIVE / f"P2_PREFIT_SYNTHETIC_QUALIFICATION_RUNNING_{index:03d}.json"
    attempt_relative = RUN_RELATIVE / f"P2_PREFIT_SYNTHETIC_QUALIFICATION_ATTEMPT_{index:03d}.json"
    sensor_relative = RUN_RELATIVE / f"P2_PREFIT_SENSOR_NUMERICAL_MUTATION_GATE_{index:03d}.json"
    architecture_relative = RUN_RELATIVE / f"P2_PREFIT_ARCHITECTURE_MUTATION_GATE_{index:03d}.json"
    qualified_test_relative = RUN_RELATIVE / f"P2_PREFIT_QUALIFIED_TEST_GATE_{index:03d}.json"
    gate_relative = RUN_RELATIVE / f"P2_PREFIT_SYNTHETIC_GATE_{index:03d}.json"
    running_path = WORKSPACE / running_relative
    attempt_path = WORKSPACE / attempt_relative
    sensor_path = WORKSPACE / sensor_relative
    architecture_path = WORKSPACE / architecture_relative
    qualified_test_path = WORKSPACE / qualified_test_relative
    gate_path = WORKSPACE / gate_relative
    started = _utc_now()
    stage = "VALIDATE_PREFIT_AUTHORITY"
    running_written = False
    common: dict[str, Any] | None = None
    try:
        amendment, _, common = _validated_authority()
        settings = amendment["effective_settings"]
        stage = "WRITE_RUNNING_MANIFEST_BEFORE_PROJECT_QUALIFICATION_IMPORT"
        _write_new_immutable(running_path, {
            "schema": "biospur-c2-p2-prefit-synthetic-running-v2",
            **common,
            "attempt_index": index,
            "started_utc": started,
            "status": "RUNNING_APPEND_ONLY_TERMINAL_STATUS_IN_SEPARATE_ATTEMPT_RECORD",
            "planned_artifacts": {
                "attempt": str(attempt_relative),
                "sensor_mutation_gate": str(sensor_relative),
                "architecture_mutation_gate": str(architecture_relative),
                "qualified_owner_test_gate": str(qualified_test_relative),
                "synthetic_gate_if_pass": str(gate_relative),
            },
            "failure_recording_policy": "EVERY_EXCEPTION_GETS_ONE_NEW_TERMINAL_RECORD_WITH_TRACEBACK_AND_STAGE",
            "bytecode_write_disabled_before_project_import": bool(sys.dont_write_bytecode),
            "tmpdir": str(run_tmp.relative_to(WORKSPACE)),
            "real_fit_authorized": False,
        })
        running_written = True
        stage = "RUN_EXACT_HASHED_OWNER_TEST_WITH_BYTECODE_AND_CACHE_DISABLED"
        qualified_test = _run_exact_qualified_test(
            common=common,
            attempt_index=index,
            artifact_path=qualified_test_path,
            run_tmp=run_tmp,
        )
        if qualified_test["pass"] is not True:
            raise RuntimeError("exact hashed owner test did not pass")
        stage = "IMPORT_AND_RUN_INDEPENDENT_OWNER_LEVEL_QUALIFICATION"
        from biospur_fusion.v0.c2_progressive.synthetic import run_qualification

        result = run_qualification(
            settings,
            prefit_registry_seal_path=WORKSPACE / SEAL_RELATIVE,
        )
        sensor_ledger = result["sensor_and_numerical_mutation_ledger"]
        architecture_document = result["architecture_mutations"]
        _validate_sensor_ledger(settings, sensor_ledger)
        _validate_architecture_ledger(settings, architecture_document)
        sensor_pass = bool(all(bool(row["pass"]) for row in sensor_ledger.values()))
        architecture_pass = bool(
            architecture_document.get("pass")
            and all(bool(row["caught"]) for row in architecture_document["mutations"].values())
            and architecture_document.get("owner_not_implemented") == []
        )
        stage = "WRITE_SEPARATE_SENSOR_MUTATION_ARTIFACT"
        _write_new_immutable(sensor_path, {
            "schema": "biospur-c2-p2-sensor-numerical-mutation-gate-v1",
            **common,
            "attempt_index": index,
            "created_utc": _utc_now(),
            "mutations": sensor_ledger,
            "registered_names_match": set(sensor_ledger)
            == set(settings["synthetic"]["mandatory_sensor_and_numerical_mutations"]),
            "pass": sensor_pass,
        })
        stage = "WRITE_SEPARATE_ARCHITECTURE_MUTATION_ARTIFACT"
        _write_new_immutable(architecture_path, {
            "schema": "biospur-c2-p2-architecture-mutation-gate-v1",
            **common,
            "attempt_index": index,
            "created_utc": _utc_now(),
            "mutations": architecture_document["mutations"],
            "registered_names_match": set(architecture_document["mutations"])
            == set(settings["synthetic"]["mandatory_architecture_negative_mutations"]),
            "declarative_only_counted_as_pass": False,
            "owner_not_implemented": list(architecture_document.get("owner_not_implemented", [])),
            "pass": architecture_pass,
        })
        complete_pass = bool(
            result["pass"] and sensor_pass and architecture_pass
            and qualified_test["pass"]
        )
        stage = "WRITE_TERMINAL_QUALIFICATION_ATTEMPT"
        _write_new_immutable(attempt_path, {
            "schema": "biospur-c2-p2-prefit-synthetic-qualification-attempt-v2",
            **common,
            "attempt_index": index,
            "started_utc": started,
            "completed_utc": _utc_now(),
            "status": "PASS" if complete_pass else "FAIL_QUALIFICATION_RESULT",
            "pass": complete_pass,
            "running_manifest": _binding(running_relative),
            "sensor_mutation_gate": _binding(sensor_relative),
            "architecture_mutation_gate": _binding(architecture_relative),
            "qualified_owner_test_gate": _binding(qualified_test_relative),
            "qualification_result": result,
            "real_fit_authorized": False,
            "exit_status": 0 if complete_pass else 2,
        })
        if complete_pass:
            stage = "WRITE_FINAL_SYNTHETIC_ONLY_GATE"
            _write_new_immutable(gate_path, {
                "schema": "biospur-c2-p2-prefit-synthetic-gate-v2",
                **common,
                "attempt_index": index,
                "created_utc": _utc_now(),
                "status": "PASS_SYNTHETIC_ONLY",
                "pass": True,
                "qualification": _binding(attempt_relative),
                "sensor_mutation_gate": _binding(sensor_relative),
                "architecture_mutation_gate": _binding(architecture_relative),
                "qualified_owner_test_gate": _binding(qualified_test_relative),
                "real_fit_authorized_by_this_artifact_alone": False,
            })
        print(json.dumps({
            "pass": complete_pass,
            "attempt_index": index,
            "running": _binding(running_relative),
            "attempt": _binding(attempt_relative),
            "sensor_mutation_gate": _binding(sensor_relative),
            "architecture_mutation_gate": _binding(architecture_relative),
            "qualified_owner_test_gate": _binding(qualified_test_relative),
            "synthetic_gate": _binding(gate_relative) if complete_pass else None,
            "summary": result.get("summary"),
            "real_fit_authorized": False,
        }, indent=2, sort_keys=True, default=_jsonable))
        return 0 if complete_pass else 2
    except BaseException as exc:
        failure = {
            "schema": "biospur-c2-p2-prefit-synthetic-qualification-attempt-v2",
            **({} if common is None else common),
            "attempt_index": index,
            "started_utc": started,
            "completed_utc": _utc_now(),
            "status": "FAIL_EXCEPTION_PRESERVED",
            "pass": False,
            "failure_stage": stage,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback.format_exc(),
            "running_manifest": _binding(running_relative) if running_written else None,
            "sensor_mutation_gate": _binding(sensor_relative) if sensor_path.exists() else None,
            "architecture_mutation_gate": (
                _binding(architecture_relative) if architecture_path.exists() else None
            ),
            "qualified_owner_test_gate": (
                _binding(qualified_test_relative) if qualified_test_path.exists() else None
            ),
            "real_capture_rows_opened": False,
            "external_holdout_opened": False,
            "real_fit_authorized": False,
            "exit_status": 2,
            "causal_pivot_required_before_retry": True,
            "retry_naming_rule": "NEXT_UNUSED_THREE_DIGIT_ATTEMPT_INDEX;NO_OVERWRITE",
        }
        if attempt_path.exists():
            failure_relative = (
                RUN_RELATIVE
                / f"P2_PREFIT_SYNTHETIC_QUALIFICATION_EXCEPTION_AFTER_ATTEMPT_{index:03d}.json"
            )
            failure_path = WORKSPACE / failure_relative
        else:
            failure_relative = attempt_relative
            failure_path = attempt_path
        _write_new_immutable(failure_path, failure)
        print(json.dumps({
            "pass": False,
            "attempt_index": index,
            "failure": _binding(failure_relative),
            "failure_stage": stage,
            "exception_type": type(exc).__name__,
            "real_fit_authorized": False,
        }, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
