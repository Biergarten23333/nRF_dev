#!/usr/bin/env python3
"""Append-only successor registry/seal after the failed sealed qualification 001.

This module deliberately reuses the fully audited settings construction from
amendment 002, then changes only the active seal path, failure lineage, and
qualified source closure needed for the repaired source revision. Importing it
is side-effect free.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_002 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
MONITOR_TASK_ID = base.MONITOR_TASK_ID
P1_MONITOR_ACCEPTANCE_RELATIVE = base.P1_MONITOR_ACCEPTANCE_RELATIVE
P1_MONITOR_ACCEPTANCE_SHA256 = base.P1_MONITOR_ACCEPTANCE_SHA256
REAL_ACTIVATION_RELATIVE = base.REAL_ACTIVATION_RELATIVE
SENSOR_AND_NUMERICAL_MUTATIONS = base.SENSOR_AND_NUMERICAL_MUTATIONS
ARCHITECTURE_MUTATIONS = base.ARCHITECTURE_MUTATIONS

PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_002_RELATIVE
PARENT_AMENDMENT_SHA256 = "437c6243bceeb532ca41e2e95362065ff6725b99ae7dead0fab53718a30e2376"
PARENT_SEAL_RELATIVE = base.SEAL_002_RELATIVE
PARENT_SEAL_SHA256 = "34c8f5686dc891fa841588b74fa965bd48b58c596cd7501d98079368cf9d50e8"
FAILED_QUALIFICATION_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_SYNTHETIC_QUALIFICATION_ATTEMPT_001.json"
)
FAILED_QUALIFICATION_SHA256 = (
    "848c1b79a02d68201713619e6e0dd3c402d285820bf491bdc141a3eb596c7dd4"
)
FAILED_ARCHITECTURE_DIAGNOSTIC_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ARCHITECTURE_MUTATION_DIAGNOSTIC_147_TERMINAL.json"
)
FAILED_ARCHITECTURE_DIAGNOSTIC_SHA256 = (
    "85a26003394193d6815ba8289923ebbbd0e4aee639fbf1577f8faf51742e2a49"
)

AMENDMENT_003_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_003.json"
)
SEAL_003_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_003.json"

MANDATORY_QUALIFIED_SOURCE_PATHS = (
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_003.py",
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
    _require_hash(PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256)
    _require_hash(PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256)
    _require_hash(FAILED_QUALIFICATION_RELATIVE, FAILED_QUALIFICATION_SHA256)
    _require_hash(
        FAILED_ARCHITECTURE_DIAGNOSTIC_RELATIVE,
        FAILED_ARCHITECTURE_DIAGNOSTIC_SHA256,
    )
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_003_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        "superseded_amendment": {
            "path": str(PARENT_AMENDMENT_RELATIVE),
            "sha256": PARENT_AMENDMENT_SHA256,
        },
        "superseded_seal": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_QUALIFICATION_FROZEN_EVIDENCE_NOT_REAL_AUTHORITY",
        },
        "failed_qualification": {
            "path": str(FAILED_QUALIFICATION_RELATIVE),
            "sha256": FAILED_QUALIFICATION_SHA256,
            "status": "FAIL_EXCEPTION_PRESERVED",
        },
        "failed_architecture_diagnostic": {
            "path": str(FAILED_ARCHITECTURE_DIAGNOSTIC_RELATIVE),
            "sha256": FAILED_ARCHITECTURE_DIAGNOSTIC_SHA256,
            "failed_mutation_count": 8,
        },
        "repair_scope": (
            "EIGHT_OWNER_LINKED_MUTATION_REACHABILITY_AND_LIKE_FOR_LIKE_"
            "TRANSACTION_STATE_COMPARISON_ONLY"
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
    created = datetime.now(timezone.utc).isoformat()
    amendment = {
        "schema": "biospur-c2-active-parameter-registry-prefit-amendment-v2",
        "created_utc": created,
        "append_only_parent": {
            "path": str(PARENT_AMENDMENT_RELATIVE),
            "sha256": PARENT_AMENDMENT_SHA256,
        },
        "superseded_seal_retained": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_QUALIFICATION_FROZEN_EVIDENCE_NOT_REAL_AUTHORITY",
        },
        "failed_qualification": {
            "path": str(FAILED_QUALIFICATION_RELATIVE),
            "sha256": FAILED_QUALIFICATION_SHA256,
        },
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "synthetic_outcome_observed": True,
        "observed_synthetic_outcome": "FAILED_SEAL_002_OWNER_TEST_GATE",
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
        "source_change_policy": (
            "ANY_QUALIFIED_SOURCE_CHANGE_INVALIDATES_SEAL_003_AND_REQUIRES_"
            "ANOTHER_APPEND_ONLY_SUCCESSOR"
        ),
    }
    amendment_path = WORKSPACE / AMENDMENT_003_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_003_RELATIVE),
            "sha256": _sha(amendment_path),
        },
        "amendment_append_only_parent": amendment["append_only_parent"],
        "failed_qualification": amendment["failed_qualification"],
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "qualified_source_hashes": source_hashes,
        "synthetic_qualification_status": "NOT_RUN_SUCCESSOR_PREFIT_SEAL_REAL_FIT_BLOCKED",
        "synthetic_outcome_observed_before_seal": True,
        "real_fit_authorized_after_registry_alone": False,
        "heldout_opened": False,
    }
    seal_path = WORKSPACE / SEAL_003_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_003": str(AMENDMENT_003_RELATIVE),
        "amendment_003_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_003": str(SEAL_003_RELATIVE),
        "seal_003_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "real_fit_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
