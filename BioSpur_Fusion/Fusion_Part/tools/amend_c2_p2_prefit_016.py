#!/usr/bin/env python3
"""Single successor binding only the mature rollback fixture correction."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_015 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_015_RELATIVE
PARENT_AMENDMENT_SHA256 = "079ff0e6d7f40ed98ed5ab664acbc293ed3dd54a8f2d1d1185d7b1078e7eb62c"
PARENT_SEAL_RELATIVE = base.SEAL_015_RELATIVE
PARENT_SEAL_SHA256 = "890885063ba64fb53216771011c82a9cd2977329b553f181dad37e668777da32"
FAILED_GATE_ATTEMPT_RELATIVE = (
    RUN_RELATIVE
    / "CONTINUATION_SPRINT/BOUNDED_CENTER_BUDGET_ROLLBACK_GATE_ATTEMPT_001.json"
)
FAILED_GATE_ATTEMPT_SHA256 = "3a0ab35adfaa990ed500d5a648d0969557205c8948fad149a85bcd41a6a75156"
AMENDMENT_016_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_016.json"
)
SEAL_016_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_016.json"
MANDATORY_QUALIFIED_SOURCE_PATHS = tuple(dict.fromkeys((
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_016.py",
)))

_sha = base._sha
_semantic_sha = base._semantic_sha
_write_new_immutable = base._write_new_immutable


def _require(relative: Path, expected: str) -> None:
    path = WORKSPACE / relative
    if not path.is_file() or _sha(path) != expected:
        raise RuntimeError(f"seal-016 fixture-successor input changed: {relative}")


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("seal-016 successor requires canonical Fusion_Part")
    _require(PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256)
    _require(PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256)
    _require(FAILED_GATE_ATTEMPT_RELATIVE, FAILED_GATE_ATTEMPT_SHA256)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_016_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "failed_diagnostic_seal_015": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_FIRST_POSTSEAL_FIXTURE_HARD_SUPPORT_SET_MISMATCH",
        },
        "failed_bounded_center_gate_attempt_001": {
            "path": str(FAILED_GATE_ATTEMPT_RELATIVE),
            "sha256": FAILED_GATE_ATTEMPT_SHA256,
            "payload_opened": False,
            "scientific_owner_failure": False,
        },
        "bounded_fixture_correction_016": (
            "QMT_NO_UPDATE_ROWS_ITERATE_ONLY_AUTHENTICATED_CURRENT_HARD_SUPPORT_"
            "BRANCH_IDS;NO_PRODUCTION_THRESHOLD_SCIENCE_OWNER_OR_BUDGET_CHANGE"
        ),
    }
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("seal-016 source closure requires canonical Fusion_Part")
    output: dict[str, str] = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory seal-016 source is missing: {relative}")
        output[relative] = _sha(path)
    return output


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("run seal-016 successor only from canonical Fusion_Part")
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
            "status": "FAILED_FIRST_POSTSEAL_FIXTURE_HARD_SUPPORT_SET_MISMATCH",
        },
        "failed_gate_attempt": {
            "path": str(FAILED_GATE_ATTEMPT_RELATIVE),
            "sha256": FAILED_GATE_ATTEMPT_SHA256,
        },
        "focused_owner_test_gate": {
            "path": str(base.FOCUSED_GATE_RELATIVE),
            "sha256": base.FOCUSED_GATE_SHA256,
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
    amendment_path = WORKSPACE / AMENDMENT_016_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_016_RELATIVE),
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
    seal_path = WORKSPACE / SEAL_016_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_016": str(AMENDMENT_016_RELATIVE),
        "amendment_016_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_016": str(SEAL_016_RELATIVE),
        "seal_016_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "heldout_opened": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
