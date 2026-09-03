#!/usr/bin/env python3
"""Append-only successor registry/seal after failed sealed replay 156."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_004 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
MONITOR_TASK_ID = base.MONITOR_TASK_ID
P1_MONITOR_ACCEPTANCE_RELATIVE = base.P1_MONITOR_ACCEPTANCE_RELATIVE
P1_MONITOR_ACCEPTANCE_SHA256 = base.P1_MONITOR_ACCEPTANCE_SHA256
REAL_ACTIVATION_RELATIVE = base.REAL_ACTIVATION_RELATIVE
SENSOR_AND_NUMERICAL_MUTATIONS = base.SENSOR_AND_NUMERICAL_MUTATIONS
ARCHITECTURE_MUTATIONS = base.ARCHITECTURE_MUTATIONS

PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_004_RELATIVE
PARENT_AMENDMENT_SHA256 = "3601ed440a09f01a93559c9c2f9f064a9ef6b2bb8590f844e0d2224aa5611013"
PARENT_SEAL_RELATIVE = base.SEAL_004_RELATIVE
PARENT_SEAL_SHA256 = "f42f16227a5c1fe48d68a291dbd2a0f358cc4e8b65608f23791bc18c8f962e7c"
FAILED_SEALED_REPLAY_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_EIGHT_ARCHITECTURE_REPAIR_REPLAY_156_TERMINAL.json"
)
FAILED_SEALED_REPLAY_SHA256 = (
    "e58e002a6ee48be8ea8b11a161e7e8e0c84ac677c8df611dc4cf1cb54556bf0e"
)
STATE_DIFF_DIAGNOSTIC_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_QMT_RETRY_CLEAN_STATE_DIFF_DIAGNOSTIC_157_TERMINAL.json"
)
STATE_DIFF_DIAGNOSTIC_SHA256 = (
    "b860eb2443394b34502026e88f6e4dd8e4d9622f68c77c85ba0db48bc0c88236"
)

AMENDMENT_005_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_005.json"
)
SEAL_005_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_005.json"

MANDATORY_QUALIFIED_SOURCE_PATHS = (
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_005.py",
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
    _require_hash(FAILED_SEALED_REPLAY_RELATIVE, FAILED_SEALED_REPLAY_SHA256)
    _require_hash(STATE_DIFF_DIAGNOSTIC_RELATIVE, STATE_DIFF_DIAGNOSTIC_SHA256)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_005_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "superseded_amendment_004": {
            "path": str(PARENT_AMENDMENT_RELATIVE),
            "sha256": PARENT_AMENDMENT_SHA256,
        },
        "superseded_seal_004": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_FIRST_SEALED_RUNTIME_ACTION_FROZEN_EVIDENCE",
        },
        "failed_sealed_replay_156": {
            "path": str(FAILED_SEALED_REPLAY_RELATIVE),
            "sha256": FAILED_SEALED_REPLAY_SHA256,
            "target_pass_count": 7,
            "target_fail_count": 1,
        },
        "state_diff_diagnostic_157": {
            "path": str(STATE_DIFF_DIAGNOSTIC_RELATIVE),
            "sha256": STATE_DIFF_DIAGNOSTIC_SHA256,
        },
        "repair_scope_005": (
            "TRANSACTION_ENTRY_GUARD_INSIDE_CHECKPOINT_PLUS_EXPLICITLY_"
            "VALIDATED_RUNTIME_LOCAL_IDENTITY_NORMALIZATION_ONLY"
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
    failed_replay = {
        "path": str(FAILED_SEALED_REPLAY_RELATIVE),
        "sha256": FAILED_SEALED_REPLAY_SHA256,
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
            "status": "FAILED_FIRST_SEALED_RUNTIME_ACTION_FROZEN_EVIDENCE",
        },
        "failed_sealed_runtime_action": failed_replay,
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "synthetic_outcome_observed": True,
        "observed_synthetic_outcome": "FAILED_SEAL_004_EIGHT_ROW_REPLAY_156",
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
        "source_change_policy": (
            "ANY_QUALIFIED_SOURCE_CHANGE_INVALIDATES_SEAL_005_AND_REQUIRES_"
            "ANOTHER_APPEND_ONLY_SUCCESSOR"
        ),
    }
    amendment_path = WORKSPACE / AMENDMENT_005_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_005_RELATIVE),
            "sha256": _sha(amendment_path),
        },
        "amendment_append_only_parent": amendment["append_only_parent"],
        "failed_sealed_runtime_action": failed_replay,
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "qualified_source_hashes": source_hashes,
        "synthetic_qualification_status": (
            "NOT_RUN_SUCCESSOR_PREFIT_SEAL_REAL_FIT_BLOCKED"
        ),
        "synthetic_outcome_observed_before_seal": True,
        "real_fit_authorized_after_registry_alone": False,
        "heldout_opened": False,
    }
    seal_path = WORKSPACE / SEAL_005_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_005": str(AMENDMENT_005_RELATIVE),
        "amendment_005_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_005": str(SEAL_005_RELATIVE),
        "seal_005_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "real_fit_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
