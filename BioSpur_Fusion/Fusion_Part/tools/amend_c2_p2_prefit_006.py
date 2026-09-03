#!/usr/bin/env python3
"""Append-only successor registry/seal after qualification attempt 002."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_005 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
MONITOR_TASK_ID = base.MONITOR_TASK_ID
P1_MONITOR_ACCEPTANCE_RELATIVE = base.P1_MONITOR_ACCEPTANCE_RELATIVE
P1_MONITOR_ACCEPTANCE_SHA256 = base.P1_MONITOR_ACCEPTANCE_SHA256
REAL_ACTIVATION_RELATIVE = base.REAL_ACTIVATION_RELATIVE
SENSOR_AND_NUMERICAL_MUTATIONS = base.SENSOR_AND_NUMERICAL_MUTATIONS
ARCHITECTURE_MUTATIONS = base.ARCHITECTURE_MUTATIONS

PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_005_RELATIVE
PARENT_AMENDMENT_SHA256 = "5b5331c194306fa2a11bfb14dd4735c122d26010ca0e57832d219e6efaa9cf7b"
PARENT_SEAL_RELATIVE = base.SEAL_005_RELATIVE
PARENT_SEAL_SHA256 = "b37ea5494387304e77b268117c45018a9a52dcab8c48110796b72deda836069f"
FAILED_QUALIFICATION_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_SYNTHETIC_QUALIFICATION_ATTEMPT_002.json"
)
FAILED_QUALIFICATION_SHA256 = (
    "3ff0ce4203ac00eff524b7fbe77133003fe5f300ebf8f826823b7ca13c0162cb"
)
QUALIFIED_TEST_GATE_RELATIVE = RUN_RELATIVE / "P2_PREFIT_QUALIFIED_TEST_GATE_002.json"
QUALIFIED_TEST_GATE_SHA256 = (
    "721a462cdf8e214bc31e60beb8a6c02dcac2ebb283e44c91c13b2954dadff5c0"
)
AXIS_REPAIR_GATE_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_AXIS_MULTI_PAIR_OWNER_REPAIR_GATE_163.json"
)
AXIS_REPAIR_GATE_SHA256 = (
    "937efcbb505790c6e487e865cbd50b833408dcf2e438452616bc2595b66a506e"
)

AMENDMENT_006_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_006.json"
)
SEAL_006_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_006.json"

MANDATORY_QUALIFIED_SOURCE_PATHS = (
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_006.py",
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
        (AXIS_REPAIR_GATE_RELATIVE, AXIS_REPAIR_GATE_SHA256),
    ):
        _require_hash(relative, expected)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_006_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "superseded_amendment_005": {
            "path": str(PARENT_AMENDMENT_RELATIVE),
            "sha256": PARENT_AMENDMENT_SHA256,
        },
        "superseded_seal_005": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_QUALIFICATION_ATTEMPT_002_FROZEN_EVIDENCE",
        },
        "failed_qualification_attempt_002": {
            "path": str(FAILED_QUALIFICATION_RELATIVE),
            "sha256": FAILED_QUALIFICATION_SHA256,
            "failure_stage": "IMPORT_AND_RUN_INDEPENDENT_OWNER_LEVEL_QUALIFICATION",
            "exception": "NameError: pair_index is not defined in _axis_blocks",
        },
        "passed_qualified_tests_before_failure": {
            "path": str(QUALIFIED_TEST_GATE_RELATIVE),
            "sha256": QUALIFIED_TEST_GATE_SHA256,
        },
        "axis_multi_pair_owner_repair_gate": {
            "path": str(AXIS_REPAIR_GATE_RELATIVE),
            "sha256": AXIS_REPAIR_GATE_SHA256,
        },
        "repair_scope_006": (
            "ENUMERATE_CAUSAL_AXIS_INPUT_PAIRS_AND_AUDIT_EXACT_"
            "PAIR_ACTION_SPAN_BLOCK_PROVENANCE"
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
            "status": "FAILED_QUALIFICATION_ATTEMPT_002_FROZEN_EVIDENCE",
        },
        "failed_qualification": failure,
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "synthetic_outcome_observed": True,
        "observed_synthetic_outcome": "FAILED_SEAL_005_QUALIFICATION_ATTEMPT_002",
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
        "source_change_policy": (
            "ANY_QUALIFIED_SOURCE_CHANGE_INVALIDATES_SEAL_006_AND_REQUIRES_"
            "ANOTHER_APPEND_ONLY_SUCCESSOR"
        ),
    }
    amendment_path = WORKSPACE / AMENDMENT_006_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_006_RELATIVE),
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
    seal_path = WORKSPACE / SEAL_006_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_006": str(AMENDMENT_006_RELATIVE),
        "amendment_006_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_006": str(SEAL_006_RELATIVE),
        "seal_006_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "real_fit_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
