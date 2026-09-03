#!/usr/bin/env python3
"""One append-only successor correcting calibration-posterior provenance."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_011 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_011_RELATIVE
PARENT_AMENDMENT_SHA256 = "f82108323cbad85fad23b7fc8d4aabc8794dcbdf4bf0f320370902ec54043751"
PARENT_SEAL_RELATIVE = base.SEAL_011_RELATIVE
PARENT_SEAL_SHA256 = "d70335e337bd39d4acc908f8831f9dc08fb6ac6c021484807387b761e986d473"
SUPERSEDED_ACTIVATION_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ACTIVATION_001.json"
SUPERSEDED_ACTIVATION_SHA256 = "1ca79afc6dc119f67910b1090824771915cb3c9df53c1966a05be9c2f2e41a10"
SUPERSEDED_ATTEMPT_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ATTEMPT_001.json"
SUPERSEDED_ATTEMPT_SHA256 = "c00b74346a4a25f7bc7500c2a6da272db4001b0198075773a14d895402cac87d"
SUPERSEDED_READ_AUDIT_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_READ_AUDIT_001.json"
SUPERSEDED_READ_AUDIT_SHA256 = "c35dfe47d0203b0b0a13c68e9a8d7a4ba5424f3494ec604bb29c01d576d77b58"
AMENDMENT_012_RELATIVE = RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_012.json"
SEAL_012_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_012.json"
MANDATORY_QUALIFIED_SOURCE_PATHS = tuple(dict.fromkeys((
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_012.py",
)))

_sha = base._sha
_semantic_sha = base._semantic_sha
_write_new_immutable = base._write_new_immutable


def _require(relative: Path, expected: str) -> None:
    path = WORKSPACE / relative
    if not path.is_file() or _sha(path) != expected:
        raise RuntimeError(f"provenance successor authority changed: {relative}")


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("provenance successor requires canonical Fusion_Part")
    for relative, expected in (
        (PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256),
        (PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256),
        (SUPERSEDED_ACTIVATION_RELATIVE, SUPERSEDED_ACTIVATION_SHA256),
        (SUPERSEDED_ATTEMPT_RELATIVE, SUPERSEDED_ATTEMPT_SHA256),
        (SUPERSEDED_READ_AUDIT_RELATIVE, SUPERSEDED_READ_AUDIT_SHA256),
    ):
        _require(relative, expected)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_012_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "superseded_seal_011": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "SUPERSEDED_STALE_CALIBRATION_PROVENANCE_AFTER_READ_ONLY_AUDIT",
        },
        "superseded_diagnostic_activation_001": {
            "path": str(SUPERSEDED_ACTIVATION_RELATIVE),
            "sha256": SUPERSEDED_ACTIVATION_SHA256,
        },
        "terminated_diagnostic_attempt_001": {
            "path": str(SUPERSEDED_ATTEMPT_RELATIVE),
            "sha256": SUPERSEDED_ATTEMPT_SHA256,
            "actions_read": 19,
            "actions_committed_before_interrupt": 2,
            "heldout_opened": False,
            "result_usable_for_acceptance": False,
        },
        "attempt_001_exact_read_audit": {
            "path": str(SUPERSEDED_READ_AUDIT_RELATIVE),
            "sha256": SUPERSEDED_READ_AUDIT_SHA256,
        },
        "repair_scope_012": (
            "PROVENANCE_ONLY_GRAVITY_NORM_INFORMED_BROAD_CALIBRATION_"
            "POSTERIOR_AND_PREDICTIVE_ACCELEROMETER_CORRECTION_DISCLOSED_"
            "WITHOUT_OWNER_THRESHOLD_OR_ALGORITHM_CHANGE"
        ),
    }
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("provenance source closure requires canonical Fusion_Part")
    output = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory provenance source is missing: {relative}")
        output[relative] = _sha(path)
    return output


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("run provenance successor only from canonical Fusion_Part")
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
            "status": "SUPERSEDED_STALE_CALIBRATION_PROVENANCE",
        },
        "superseded_diagnostic_attempt": {
            "path": str(SUPERSEDED_ATTEMPT_RELATIVE),
            "sha256": SUPERSEDED_ATTEMPT_SHA256,
            "result_usable_for_acceptance": False,
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
    amendment_path = WORKSPACE / AMENDMENT_012_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_012_RELATIVE),
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
    seal_path = WORKSPACE / SEAL_012_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_012": str(AMENDMENT_012_RELATIVE),
        "amendment_012_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_012": str(SEAL_012_RELATIVE),
        "seal_012_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "heldout_opened": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
