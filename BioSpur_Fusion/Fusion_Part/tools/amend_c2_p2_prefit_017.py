#!/usr/bin/env python3
"""Final fixture-only successor aligning mature knee-center ownership."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_016 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_016_RELATIVE
PARENT_AMENDMENT_SHA256 = "278fcc5bad1c9600dd0b669338a476c957e7cf3d699b881878cb0081e63453ca"
PARENT_SEAL_RELATIVE = base.SEAL_016_RELATIVE
PARENT_SEAL_SHA256 = "45af70813c9990a418d5ee953dc67e56a76af5647d69fd1327d117de11cc77e6"
FAILED_GATE_ATTEMPT_RELATIVE = (
    RUN_RELATIVE
    / "CONTINUATION_SPRINT/BOUNDED_CENTER_BUDGET_ROLLBACK_GATE_ATTEMPT_002.json"
)
FAILED_GATE_ATTEMPT_SHA256 = "863024a40622914fee15fe64cb49232ba1a9707e4b0492c386d54682abf061e7"
AMENDMENT_017_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_017.json"
)
SEAL_017_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_017.json"
MANDATORY_QUALIFIED_SOURCE_PATHS = tuple(dict.fromkeys((
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_017.py",
)))

_sha = base._sha
_semantic_sha = base._semantic_sha
_write_new_immutable = base._write_new_immutable


def _require(relative: Path, expected: str) -> None:
    path = WORKSPACE / relative
    if not path.is_file() or _sha(path) != expected:
        raise RuntimeError(f"seal-017 fixture-successor input changed: {relative}")


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("seal-017 successor requires canonical Fusion_Part")
    _require(PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256)
    _require(PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256)
    _require(FAILED_GATE_ATTEMPT_RELATIVE, FAILED_GATE_ATTEMPT_SHA256)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_017_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "failed_diagnostic_seal_016": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_MATURE_FIXTURE_CENTER_ACCEPTANCE_OWNER_DIVERGENCE",
        },
        "failed_bounded_center_gate_attempt_002": {
            "path": str(FAILED_GATE_ATTEMPT_RELATIVE),
            "sha256": FAILED_GATE_ATTEMPT_SHA256,
            "payload_opened": False,
            "scientific_owner_failure": False,
        },
        "bounded_fixture_correction_017": (
            "AFTER_NINE_MATURE_PREFIXES_REMOVE_ONLY_TEMPORARY_KNEE_LEFT_"
            "GEOMETRY_SEED_BEFORE_BASELINE_SO_GEOMETRY_AND_CENTER_PREFIX_"
            "OWNERS_BOTH_REMAIN_UNACCEPTED;NO_PRODUCTION_CHANGE"
        ),
    }
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("seal-017 source closure requires canonical Fusion_Part")
    output: dict[str, str] = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory seal-017 source is missing: {relative}")
        output[relative] = _sha(path)
    return output


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("run seal-017 successor only from canonical Fusion_Part")
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
            "status": "FAILED_MATURE_FIXTURE_CENTER_ACCEPTANCE_OWNER_DIVERGENCE",
        },
        "failed_gate_attempt": {
            "path": str(FAILED_GATE_ATTEMPT_RELATIVE),
            "sha256": FAILED_GATE_ATTEMPT_SHA256,
        },
        "focused_owner_test_gate": {
            "path": str(base.base.FOCUSED_GATE_RELATIVE),
            "sha256": base.base.FOCUSED_GATE_SHA256,
        },
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "fixture_only_successor": True,
        "production_threshold_science_or_owner_changed": False,
        "full_synthetic_qualification_complete": False,
        "real_diagnostic_requires_separate_activation": True,
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
    }
    amendment_path = WORKSPACE / AMENDMENT_017_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_017_RELATIVE),
            "sha256": _sha(amendment_path),
        },
        "amendment_append_only_parent": amendment["append_only_parent"],
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "qualified_source_hashes": source_hashes,
        "synthetic_qualification_status": (
            "USER_AUTHORIZED_THREE_HOUR_DIAGNOSTIC_SPRINT;FULL_QUALIFICATION_PENDING"
        ),
        "real_fit_authorized_after_registry_alone": False,
        "real_diagnostic_requires_separate_activation": True,
        "heldout_opened": False,
    }
    seal_path = WORKSPACE / SEAL_017_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_017": str(AMENDMENT_017_RELATIVE),
        "amendment_017_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_017": str(SEAL_017_RELATIVE),
        "seal_017_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "heldout_opened": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
