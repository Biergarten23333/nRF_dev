#!/usr/bin/env python3
"""Append-only successor registry/seal after qualification attempt 004."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_007 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
MONITOR_TASK_ID = base.MONITOR_TASK_ID
P1_MONITOR_ACCEPTANCE_RELATIVE = base.P1_MONITOR_ACCEPTANCE_RELATIVE
P1_MONITOR_ACCEPTANCE_SHA256 = base.P1_MONITOR_ACCEPTANCE_SHA256
REAL_ACTIVATION_RELATIVE = base.REAL_ACTIVATION_RELATIVE
SENSOR_AND_NUMERICAL_MUTATIONS = base.SENSOR_AND_NUMERICAL_MUTATIONS
ARCHITECTURE_MUTATIONS = base.ARCHITECTURE_MUTATIONS

PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_007_RELATIVE
PARENT_AMENDMENT_SHA256 = "0e845a5178ab0cc9d61fbe41b0a3da4f5e5b1252ed575b52a44f8a293cc5841b"
PARENT_SEAL_RELATIVE = base.SEAL_007_RELATIVE
PARENT_SEAL_SHA256 = "5560876e1c19aada03b4ea6d8549535d4fbd19bcf9799341279cc008ed908f9f"
FAILED_QUALIFICATION_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_SYNTHETIC_QUALIFICATION_ATTEMPT_004.json"
)
FAILED_QUALIFICATION_SHA256 = (
    "da3bfa7d8f518026ea8b1d799b0ec2bd33af249de43171fe4998ad1d50e88306"
)
QUALIFIED_TEST_GATE_RELATIVE = RUN_RELATIVE / "P2_PREFIT_QUALIFIED_TEST_GATE_004.json"
QUALIFIED_TEST_GATE_SHA256 = (
    "82ca4183e0ae227ec957eb0d8701118295a915484eb19be34a42a5cd22a82822"
)
SENSOR_LEDGER_REPAIR_GATE_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_SENSOR_LEDGER_SCHEMA_REPAIR_GATE_175.json"
)
SENSOR_LEDGER_REPAIR_GATE_SHA256 = (
    "1287cb6796a2812ec55c314fb3527a21a7e2e6200429d83c0899c6a298248b5b"
)

AMENDMENT_008_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_008.json"
)
SEAL_008_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_008.json"

MANDATORY_QUALIFIED_SOURCE_PATHS = (
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_008.py",
)

_sha = base._sha
_semantic_sha = base._semantic_sha
_write_new_immutable = base._write_new_immutable


def _require_hash(relative: Path, expected: str) -> None:
    path = WORKSPACE / relative
    if not path.is_file() or _sha(path) != expected:
        raise RuntimeError(f"successor prefit authority changed: {relative}")


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("successor prefit settings require canonical Fusion_Part")
    for relative, expected in (
        (PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256),
        (PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256),
        (FAILED_QUALIFICATION_RELATIVE, FAILED_QUALIFICATION_SHA256),
        (QUALIFIED_TEST_GATE_RELATIVE, QUALIFIED_TEST_GATE_SHA256),
        (SENSOR_LEDGER_REPAIR_GATE_RELATIVE, SENSOR_LEDGER_REPAIR_GATE_SHA256),
    ):
        _require_hash(relative, expected)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_008_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "superseded_amendment_007": {
            "path": str(PARENT_AMENDMENT_RELATIVE),
            "sha256": PARENT_AMENDMENT_SHA256,
        },
        "superseded_seal_007": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_QUALIFICATION_ATTEMPT_004_FROZEN_EVIDENCE",
        },
        "failed_qualification_attempt_004": {
            "path": str(FAILED_QUALIFICATION_RELATIVE),
            "sha256": FAILED_QUALIFICATION_SHA256,
            "failure_stage": "SENSOR_LEDGER_EXACT_EVIDENCE_FIELD_VALIDATION",
            "exception": (
                "RuntimeError: sensor mutation lacks owner-level evidence fields: "
                "CENTER_ACCELEROMETER_CALIBRATION_NUISANCE_OMISSION"
            ),
        },
        "passed_qualified_tests_before_failure_004": {
            "path": str(QUALIFIED_TEST_GATE_RELATIVE),
            "sha256": QUALIFIED_TEST_GATE_SHA256,
        },
        "sensor_ledger_schema_repair_gate": {
            "path": str(SENSOR_LEDGER_REPAIR_GATE_RELATIVE),
            "sha256": SENSOR_LEDGER_REPAIR_GATE_SHA256,
        },
        "repair_scope_008": (
            "ADD_EXACT_INJECTED_EXPECTED_OBSERVED_OWNER_EVIDENCE_FIELDS_TO_"
            "TWO_EXISTING_EXECUTED_CENTER_SENSOR_MUTATION_ROWS_WITHOUT_"
            "CHANGING_SCIENTIFIC_CALCULATION_OR_OUTCOME"
        ),
    }
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("successor source closure requires canonical Fusion_Part")
    closure: dict[str, str] = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory successor source is missing: {relative}")
        closure[relative] = _sha(path)
    return closure


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("run successor prefit generator only from canonical Fusion_Part")
    settings = build_effective_settings(WORKSPACE)
    source_hashes = build_qualified_source_hashes(WORKSPACE)
    failure = {
        "path": str(FAILED_QUALIFICATION_RELATIVE),
        "sha256": FAILED_QUALIFICATION_SHA256,
    }
    amendment = {
        "schema": "biospur-c2-active-parameter-registry-prefit-amendment-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_AMENDMENT_RELATIVE),
            "sha256": PARENT_AMENDMENT_SHA256,
        },
        "superseded_seal_retained": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_QUALIFICATION_ATTEMPT_004_FROZEN_EVIDENCE",
        },
        "failed_qualification": failure,
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "synthetic_outcome_observed": True,
        "observed_synthetic_outcome": "FAILED_SEAL_007_QUALIFICATION_ATTEMPT_004",
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
        "source_change_policy": (
            "ANY_QUALIFIED_SOURCE_CHANGE_INVALIDATES_SEAL_008_AND_REQUIRES_"
            "ANOTHER_APPEND_ONLY_SUCCESSOR"
        ),
    }
    amendment_path = WORKSPACE / AMENDMENT_008_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_008_RELATIVE),
            "sha256": _sha(amendment_path),
        },
        "amendment_append_only_parent": amendment["append_only_parent"],
        "failed_qualification": failure,
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "qualified_source_hashes": source_hashes,
        "synthetic_qualification_status": (
            "NOT_RUN_SUCCESSOR_PREFIT_SEAL_REAL_FIT_BLOCKED"
        ),
        "synthetic_outcome_observed_before_seal": True,
        "real_fit_authorized_after_registry_alone": False,
        "heldout_opened": False,
    }
    seal_path = WORKSPACE / SEAL_008_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_008": str(AMENDMENT_008_RELATIVE),
        "amendment_008_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_008": str(SEAL_008_RELATIVE),
        "seal_008_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "real_fit_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
