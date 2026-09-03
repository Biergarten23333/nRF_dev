#!/usr/bin/env python3
"""Bounded successor for explicit timing-owner local no-update handling."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_012 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_012_RELATIVE
PARENT_AMENDMENT_SHA256 = "e7310f3803294854a30612f19917deb669efc7b095fd5bf1ae43f9c46136bbe9"
PARENT_SEAL_RELATIVE = base.SEAL_012_RELATIVE
PARENT_SEAL_SHA256 = "97663c8d3bda5e39194a01c44548cc026571d867342e93c16570d297f235d021"
FAILED_ACTIVATION_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ACTIVATION_002.json"
FAILED_ACTIVATION_SHA256 = "c69e23a95a700dd6f877e84e0c9675ab3cc2ef4c1181597177a0bf0ed1d00063"
FAILED_ATTEMPT_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ATTEMPT_002.json"
FAILED_ATTEMPT_SHA256 = "25e52191d83a1c6f42e12d92d375fd2340f8c61b7b29ce14c29e4f7a4fc54ffe"
FAILED_READ_AUDIT_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_READ_AUDIT_002.json"
FAILED_READ_AUDIT_SHA256 = "1312d62e7f73eb75d6cdf8e9f63c5fd65a94c99f63810d81e895184bec0a1258"
AMENDMENT_013_RELATIVE = RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_013.json"
SEAL_013_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_013.json"
MANDATORY_QUALIFIED_SOURCE_PATHS = tuple(dict.fromkeys((
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_013.py",
)))

_sha = base._sha
_semantic_sha = base._semantic_sha
_write_new_immutable = base._write_new_immutable


def _require(relative: Path, expected: str) -> None:
    path = WORKSPACE / relative
    if not path.is_file() or _sha(path) != expected:
        raise RuntimeError(f"timing pivot authority changed: {relative}")


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("timing pivot requires canonical Fusion_Part")
    for relative, expected in (
        (PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256),
        (PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256),
        (FAILED_ACTIVATION_RELATIVE, FAILED_ACTIVATION_SHA256),
        (FAILED_ATTEMPT_RELATIVE, FAILED_ATTEMPT_SHA256),
        (FAILED_READ_AUDIT_RELATIVE, FAILED_READ_AUDIT_SHA256),
    ):
        _require(relative, expected)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_013_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "failed_diagnostic_seal_012": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_REAL_DIAGNOSTIC_TRANSACTION_ROLLBACK_COMPARATOR",
        },
        "failed_diagnostic_attempt_002": {
            "path": str(FAILED_ATTEMPT_RELATIVE),
            "sha256": FAILED_ATTEMPT_SHA256,
            "actions_read": 19,
            "actions_committed": 4,
            "failure": "EXPLICIT_TIMING_LOCAL_NO_UPDATE_ESCALATED_THROUGH_TRANSACTION_ROLLBACK",
            "heldout_opened": False,
        },
        "failed_attempt_002_read_audit": {
            "path": str(FAILED_READ_AUDIT_RELATIVE),
            "sha256": FAILED_READ_AUDIT_SHA256,
        },
        "bounded_pivot_013": (
            "EXACT_TIMING_OBSERVATION_LOCAL_NO_UPDATE_IS_RETAINED_IN_CURRENT_"
            "EPISODE_WITH_ZERO_PAIR_CLOCK_AND_GEOMETRY_INFORMATION;OTHER_"
            "EXCEPTIONS_RETAIN_STRICT_TRANSACTION_ROLLBACK"
        ),
    }
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("timing pivot source closure requires canonical Fusion_Part")
    output = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory timing pivot source is missing: {relative}")
        output[relative] = _sha(path)
    return output


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("run timing pivot successor only from canonical Fusion_Part")
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
            "status": "FAILED_REAL_DIAGNOSTIC_ATTEMPT_002",
        },
        "failed_diagnostic_attempt": {
            "path": str(FAILED_ATTEMPT_RELATIVE),
            "sha256": FAILED_ATTEMPT_SHA256,
        },
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "full_synthetic_qualification_complete": False,
        "real_diagnostic_requires_separate_activation": True,
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
    }
    amendment_path = WORKSPACE / AMENDMENT_013_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_013_RELATIVE),
            "sha256": _sha(amendment_path),
        },
        "amendment_append_only_parent": amendment["append_only_parent"],
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "qualified_source_hashes": source_hashes,
        "synthetic_qualification_status": (
            "USER_AUTHORIZED_DIAGNOSTIC_SPRINT;FULL_QUALIFICATION_PENDING"
        ),
        "real_fit_authorized_after_registry_alone": False,
        "real_diagnostic_requires_separate_activation": True,
        "heldout_opened": False,
    }
    seal_path = WORKSPACE / SEAL_013_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_013": str(AMENDMENT_013_RELATIVE),
        "amendment_013_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_013": str(SEAL_013_RELATIVE),
        "seal_013_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "heldout_opened": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
