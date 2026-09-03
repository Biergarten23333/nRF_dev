#!/usr/bin/env python3
"""Issue real-training activation only from one fully cross-bound synthetic PASS."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tools.amend_c2_p2_prefit_010 import (
    ARCHITECTURE_MUTATIONS,
    AMENDMENT_010_RELATIVE,
    MONITOR_TASK_ID,
    P1_MONITOR_ACCEPTANCE_RELATIVE,
    P1_MONITOR_ACCEPTANCE_SHA256,
    REAL_ACTIVATION_RELATIVE,
    RUN_RELATIVE,
    SEAL_010_RELATIVE,
    SENSOR_AND_NUMERICAL_MUTATIONS,
    WORKSPACE,
    _semantic_sha,
    _sha,
)


ACTIVATION_AUTHORITY_RELATIVE = RUN_RELATIVE / "ACTIVATION_AUTHORITY.txt"
ACTIVATION_AUTHORITY_SHA256 = "95f28d2209d6585d0f1cb00b80cd607dd3f7950ca9d79d6642e9de1644724c6f"


def _load_bound(relative: Path, expected_sha: str | None = None) -> Mapping[str, Any]:
    path = (WORKSPACE / relative).resolve()
    path.relative_to(WORKSPACE)
    if (
        not path.is_file()
        or path.stat().st_mode & 0o222
        or (expected_sha is not None and _sha(path) != expected_sha)
    ):
        raise RuntimeError(f"immutable activation evidence failed: {relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def _binding(relative: Path) -> dict[str, str]:
    path = WORKSPACE / relative
    return {"path": str(relative), "sha256": _sha(path)}


def _validated_synthetic_document(
    relative: Path,
    *,
    schema: str,
    seal_binding: Mapping[str, str],
    settings_sha: str,
    source_hashes: Mapping[str, str],
) -> Mapping[str, Any]:
    document = _load_bound(relative)
    if (
        document.get("schema") != schema
        or document.get("execution_role") != "SYNTHETIC_QUALIFICATION"
        or document.get("prefit_registry_seal") != dict(seal_binding)
        or document.get("settings_semantic_sha256") != settings_sha
        or document.get("qualified_source_hashes") != dict(source_hashes)
        or document.get("real_capture_rows_opened") is not False
        or document.get("external_holdout_opened") is not False
    ):
        raise RuntimeError(f"synthetic artifact is unrelated to this exact authority: {relative}")
    return document


def _relative_argument(value: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or relative.parent != RUN_RELATIVE:
        raise argparse.ArgumentTypeError("qualification evidence must be one direct file in the sealed run directory")
    return relative


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qualification", required=True, type=_relative_argument)
    parser.add_argument("--sensor-gate", required=True, type=_relative_argument)
    parser.add_argument("--architecture-gate", required=True, type=_relative_argument)
    parser.add_argument("--qualified-test-gate", required=True, type=_relative_argument)
    parser.add_argument("--synthetic-gate", required=True, type=_relative_argument)
    args = parser.parse_args()
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("real activation may be issued only from canonical Fusion_Part")
    if (WORKSPACE / REAL_ACTIVATION_RELATIVE).exists():
        raise FileExistsError("real activation 001 already exists; append-only evidence cannot be overwritten")
    if _sha(WORKSPACE / ACTIVATION_AUTHORITY_RELATIVE) != ACTIVATION_AUTHORITY_SHA256:
        raise RuntimeError("explicit 2026-08-29 user activation authority changed")

    seal = _load_bound(SEAL_010_RELATIVE)
    amendment = _load_bound(AMENDMENT_010_RELATIVE)
    if (
        seal.get("schema") != "biospur-c2-p2-prefit-registry-seal-v2"
        or amendment.get("schema") != "biospur-c2-active-parameter-registry-prefit-amendment-v2"
        or seal.get("amendment") != _binding(AMENDMENT_010_RELATIVE)
        or seal.get("settings_semantic_sha256") != _semantic_sha(amendment["effective_settings"])
        or seal.get("qualified_source_hashes") != amendment.get("qualified_source_hashes")
        or seal.get("synthetic_qualification_status")
        != "NOT_RUN_SUCCESSOR_PREFIT_SEAL_REAL_FIT_BLOCKED"
    ):
        raise RuntimeError("prefit seal/amendment authority is inconsistent")
    for relative, expected in seal["qualified_source_hashes"].items():
        if _sha(WORKSPACE / relative) != expected:
            raise RuntimeError(f"qualified source changed after prefit seal: {relative}")
    seal_binding = _binding(SEAL_010_RELATIVE)
    settings_sha = str(seal["settings_semantic_sha256"])
    source_hashes = dict(seal["qualified_source_hashes"])
    qualification = _validated_synthetic_document(
        args.qualification,
        schema="biospur-c2-p2-prefit-synthetic-qualification-attempt-v2",
        seal_binding=seal_binding,
        settings_sha=settings_sha,
        source_hashes=source_hashes,
    )
    sensor = _validated_synthetic_document(
        args.sensor_gate,
        schema="biospur-c2-p2-sensor-numerical-mutation-gate-v1",
        seal_binding=seal_binding,
        settings_sha=settings_sha,
        source_hashes=source_hashes,
    )
    architecture = _validated_synthetic_document(
        args.architecture_gate,
        schema="biospur-c2-p2-architecture-mutation-gate-v1",
        seal_binding=seal_binding,
        settings_sha=settings_sha,
        source_hashes=source_hashes,
    )
    qualified_test = _validated_synthetic_document(
        args.qualified_test_gate,
        schema="biospur-c2-p2-qualified-owner-test-gate-v1",
        seal_binding=seal_binding,
        settings_sha=settings_sha,
        source_hashes=source_hashes,
    )
    gate = _validated_synthetic_document(
        args.synthetic_gate,
        schema="biospur-c2-p2-prefit-synthetic-gate-v2",
        seal_binding=seal_binding,
        settings_sha=settings_sha,
        source_hashes=source_hashes,
    )
    sensor_binding = _binding(args.sensor_gate)
    architecture_binding = _binding(args.architecture_gate)
    qualification_binding = _binding(args.qualification)
    qualified_test_binding = _binding(args.qualified_test_gate)
    exact_test_paths = (
        "tests/v0/test_c2_p2_prefit_owners.py",
        "tests/v0/test_c2_progressive_range_reader.py",
    )
    qualified_test_command = qualified_test.get("command")
    qualified_test_environment = qualified_test.get("environment", {})
    exact_command_suffix = [
        "-B", "-m", "pytest", "-p", "no:cacheprovider", "--basetemp",
    ]
    if (
        qualification.get("status") != "PASS"
        or qualification.get("pass") is not True
        or qualification.get("qualification_result", {}).get("schema")
        != "biospur-c2-p2-independent-synthetic-qualification-v2"
        or qualification.get("qualification_result", {}).get("pass") is not True
        or qualification.get("sensor_mutation_gate") != sensor_binding
        or qualification.get("architecture_mutation_gate") != architecture_binding
        or qualification.get("qualified_owner_test_gate") != qualified_test_binding
    ):
        raise RuntimeError("synthetic qualification did not produce the exact complete PASS")
    if (
        gate.get("pass") is not True
        or gate.get("status") != "PASS_SYNTHETIC_ONLY"
        or gate.get("qualification") != qualification_binding
        or gate.get("sensor_mutation_gate") != sensor_binding
        or gate.get("architecture_mutation_gate") != architecture_binding
        or gate.get("qualified_owner_test_gate") != qualified_test_binding
    ):
        raise RuntimeError("synthetic gate does not bind the same complete PASS artifacts")
    if (
        qualified_test.get("status") != "PASS"
        or qualified_test.get("pass") is not True
        or qualified_test.get("returncode") != 0
        or qualified_test.get("test_sources") != [
            {"path": relative, "sha256": source_hashes[relative]}
            for relative in exact_test_paths
        ]
        or not isinstance(qualified_test_command, list)
        or len(qualified_test_command) != 9 + len(exact_test_paths)
        or qualified_test_command[1:7] != exact_command_suffix
        or qualified_test_command[8:] != ["-q", *exact_test_paths]
        or not str(qualified_test_command[7]).startswith(
            str(WORKSPACE / RUN_RELATIVE / "tmp" / "qualified_pytest_")
        )
        or qualified_test.get("working_directory") != str(WORKSPACE)
        or qualified_test_environment.get("PYTHONDONTWRITEBYTECODE") != "1"
        or qualified_test_environment.get("PYTHONPATH")
        != f"{WORKSPACE / 'src'}:{WORKSPACE}"
        or qualified_test_environment.get("TMPDIR") != str(RUN_RELATIVE / "tmp")
        or qualified_test_environment.get("pytest_cacheprovider_disabled") is not True
        or not str(qualified_test_environment.get("pytest_basetemp", "")).startswith(
            str(RUN_RELATIVE / "tmp" / "qualified_pytest_")
        )
    ):
        raise RuntimeError("qualified owner test did not execute the exact sealed source under the cache-safe policy")
    if set(sensor.get("mutations", {})) != set(SENSOR_AND_NUMERICAL_MUTATIONS):
        raise RuntimeError("sensor mutation gate differs from the mandatory exact name set")
    for name, row in sensor["mutations"].items():
        if (
            row.get("coverage_class") != "EXECUTED_OWNER_LEVEL"
            or row.get("pass") is not True
            or not row.get("owner_call_path")
            or not all(key in row for key in ("injected", "expected", "observed"))
        ):
            raise RuntimeError(f"sensor mutation is not owner-executed PASS: {name}")
    if set(architecture.get("mutations", {})) != set(ARCHITECTURE_MUTATIONS):
        raise RuntimeError("architecture mutation gate differs from the mandatory exact name set")
    for name, row in architecture["mutations"].items():
        if (
            row.get("coverage_class") != "EXECUTED_OWNER_LEVEL"
            or row.get("caught") is not True
            or row.get("expected_rejection") != name
            or row.get("observed_rejection") != name
            or not row.get("owner")
            or not row.get("callable")
        ):
            raise RuntimeError(f"architecture mutation is not owner-rejected: {name}")
    if (
        sensor.get("registered_names_match") is not True
        or sensor.get("pass") is not True
        or architecture.get("registered_names_match") is not True
        or architecture.get("declarative_only_counted_as_pass") is not False
        or architecture.get("owner_not_implemented") != []
        or architecture.get("pass") is not True
    ):
        raise RuntimeError("mutation artifacts retain declarative, missing, or failed coverage")

    activation = {
        "schema": "biospur-c2-real-training-fit-activation-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "activation_role": "REAL_TRAINING_RANGE_PRIMARY_AND_FRESH_RAW",
        "execution_authorized": True,
        "static_validator_execution_authorized_literal": False,
        "static_validator_output_mutated": False,
        "heldout_opened": False,
        "monitor_task_id": MONITOR_TASK_ID,
        "explicit_user_activation_authority": {
            "path": str(ACTIVATION_AUTHORITY_RELATIVE),
            "sha256": ACTIVATION_AUTHORITY_SHA256,
            "date": "2026-08-29",
            "message_id": "source-thread:01a03f71-e481-7e21-84f0-3c6cbeb58291/codex_delegation-input-20260829",
        },
        "prefit_registry_seal": seal_binding,
        "settings_semantic_sha256": settings_sha,
        "qualified_source_hashes": source_hashes,
        "synthetic_qualification": qualification_binding,
        "sensor_mutation_gate": sensor_binding,
        "architecture_mutation_gate": architecture_binding,
        "qualified_owner_test_gate": qualified_test_binding,
        "synthetic_gate": _binding(args.synthetic_gate),
        "p1_monitor_acceptance": {
            "path": str(P1_MONITOR_ACCEPTANCE_RELATIVE),
            "sha256": P1_MONITOR_ACCEPTANCE_SHA256,
        },
        "real_training_ranges_only": True,
        "fresh_raw_distinct_reader_session_required": True,
        "payload_or_holdout_opened_by_activation_tool": False,
    }
    target = WORKSPACE / REAL_ACTIVATION_RELATIVE
    with target.open("x", encoding="utf-8") as handle:
        json.dump(activation, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    target.chmod(0o444)
    print(json.dumps({
        "activation": str(REAL_ACTIVATION_RELATIVE),
        "activation_sha256": _sha(target),
        "execution_authorized": True,
        "heldout_opened": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
