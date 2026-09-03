#!/usr/bin/env python3
"""Single runtime successor making the ordinary operation wrapper throwable."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_018 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_018_RELATIVE
PARENT_AMENDMENT_SHA256 = (
    "38c8ec03b7b7b83e040148dbed13e2fa9fddeb4d70b1297ea78e9c1f6045a9f6"
)
PARENT_SEAL_RELATIVE = base.SEAL_018_RELATIVE
PARENT_SEAL_SHA256 = (
    "7993487af43d990a5b793ebed87bc177ebced7352c61fdb33be6fd4bfbc32de8"
)
FAILED_REAL_ATTEMPT_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ATTEMPT_005.json"
FAILED_REAL_ATTEMPT_SHA256 = (
    "97c033352fbcb9f8e55308cec245e70db13a6804a90d273860a9e9b391ba4d13"
)
SUPERSEDED_ACTIVATION_RELATIVE = (
    RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ACTIVATION_005.json"
)
SUPERSEDED_ACTIVATION_SHA256 = (
    "bd1bc90f28811af2622e4977afbfd91d1ff8fa06e32068118d918e5770383f61"
)
AMENDMENT_019_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_019.json"
)
SEAL_019_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_019.json"
MANDATORY_QUALIFIED_SOURCE_PATHS = tuple(dict.fromkeys((
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_019.py",
)))

_sha = base._sha
_semantic_sha = base._semantic_sha
_write_new_immutable = base._write_new_immutable


def _require(relative: Path, expected: str) -> None:
    path = WORKSPACE / relative
    if (
        not path.is_file()
        or path.stat().st_mode & 0o222
        or _sha(path) != expected
    ):
        raise RuntimeError(f"seal-019 immutable input changed: {relative}")


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("seal-019 successor requires canonical Fusion_Part")
    _require(PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256)
    _require(PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256)
    _require(FAILED_REAL_ATTEMPT_RELATIVE, FAILED_REAL_ATTEMPT_SHA256)
    _require(SUPERSEDED_ACTIVATION_RELATIVE, SUPERSEDED_ACTIVATION_SHA256)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_019_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "seal_018_real_attempt_005_exception_wrapper_failure": {
            "seal": {
                "path": str(PARENT_SEAL_RELATIVE),
                "sha256": PARENT_SEAL_SHA256,
            },
            "activation": {
                "path": str(SUPERSEDED_ACTIVATION_RELATIVE),
                "sha256": SUPERSEDED_ACTIVATION_SHA256,
            },
            "attempt": {
                "path": str(FAILED_REAL_ATTEMPT_RELATIVE),
                "sha256": FAILED_REAL_ATTEMPT_SHA256,
            },
            "cause": (
                "FROZEN_OPERATION_FAILURE_PREVENTED_CONTEXTLIB_TRACEBACK_"
                "ASSIGNMENT_AND_MASKED_REGISTERED_CENTER_BUDGET_LOCAL_NO_UPDATE"
            ),
            "payload_actions_read": 19,
            "actions_committed_before_failure": 2,
            "heldout_opened": False,
        },
    }
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("seal-019 source closure requires canonical Fusion_Part")
    output: dict[str, str] = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory seal-019 source is missing: {relative}")
        output[relative] = _sha(path)
    return output


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("run seal-019 successor only from canonical Fusion_Part")
    settings = build_effective_settings(WORKSPACE)
    source_hashes = build_qualified_source_hashes(WORKSPACE)
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
            "status": "FAILED_REAL_ATTEMPT_EXCEPTION_WRAPPER_LIFECYCLE",
        },
        "failed_real_attempt": {
            "path": str(FAILED_REAL_ATTEMPT_RELATIVE),
            "sha256": FAILED_REAL_ATTEMPT_SHA256,
        },
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "single_runtime_exception_lifecycle_successor": True,
        "operation_failure_exception_mutable_for_traceback_assignment": True,
        "production_solver_threshold_fixture_physical_renderer_changed": False,
        "full_synthetic_qualification_complete": False,
        "real_diagnostic_requires_separate_activation": True,
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
    }
    amendment_path = WORKSPACE / AMENDMENT_019_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_019_RELATIVE),
            "sha256": _sha(amendment_path),
        },
        "amendment_append_only_parent": amendment["append_only_parent"],
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "qualified_source_hashes": source_hashes,
        "synthetic_qualification_status": (
            "USER_AUTHORIZED_DIAGNOSTIC_SPRINT;FULL_QUALIFICATION_PENDING"
        ),
        "runtime_delta": "MUTABLE_OPERATION_FAILURE_EXCEPTION_WRAPPER_ONLY",
        "real_fit_authorized_after_registry_alone": False,
        "real_diagnostic_requires_separate_activation": True,
        "heldout_opened": False,
    }
    seal_path = WORKSPACE / SEAL_019_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_019": str(AMENDMENT_019_RELATIVE),
        "amendment_019_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_019": str(SEAL_019_RELATIVE),
        "seal_019_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "heldout_opened": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
