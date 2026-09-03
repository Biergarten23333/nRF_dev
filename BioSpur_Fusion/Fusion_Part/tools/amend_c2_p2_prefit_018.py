#!/usr/bin/env python3
"""Activation-validation-only successor binding the exact mature rollback gate."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_017 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_017_RELATIVE
PARENT_AMENDMENT_SHA256 = (
    "4af139abe19cc8f271067239af675c24ef7372a11d4cddfd4e8ff3d5f6c37845"
)
PARENT_SEAL_RELATIVE = base.SEAL_017_RELATIVE
PARENT_SEAL_SHA256 = (
    "d94179fba3f10664acc41c49f9f0da0d6d0ebbb784caf8ae459df950e92c84f2"
)
BOUNDED_CENTER_GATE_RELATIVE = (
    RUN_RELATIVE
    / "CONTINUATION_SPRINT/BOUNDED_CENTER_BUDGET_ROLLBACK_GATE_001.json"
)
BOUNDED_CENTER_GATE_SHA256 = (
    "fb89a802fcdf348113618d3c105ec64041a7c75ec7be4d6d863b1131f16d8d62"
)
AMENDMENT_018_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_018.json"
)
SEAL_018_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_018.json"
MANDATORY_QUALIFIED_SOURCE_PATHS = tuple(dict.fromkeys((
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_018.py",
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
        raise RuntimeError(f"seal-018 immutable input changed: {relative}")


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("seal-018 successor requires canonical Fusion_Part")
    _require(PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256)
    _require(PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256)
    _require(BOUNDED_CENTER_GATE_RELATIVE, BOUNDED_CENTER_GATE_SHA256)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_018_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "seal_017_activation_validation_successor": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "SUPERSEDED_ONLY_FOR_EXACT_GATE001_ACTIVATION_BINDING",
        },
        "mature_center_budget_rollback_gate_001": {
            "path": str(BOUNDED_CENTER_GATE_RELATIVE),
            "sha256": BOUNDED_CENTER_GATE_SHA256,
            "committed_prefix_count": 9,
            "transaction_status": "ROLLED_BACK",
            "owner_state_hashes_equal": True,
            "mismatched_top_level_components": [],
            "physical_trajectory_history_nonempty_required": False,
        },
    }
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("seal-018 source closure requires canonical Fusion_Part")
    output: dict[str, str] = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory seal-018 source is missing: {relative}")
        output[relative] = _sha(path)
    return output


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("run seal-018 successor only from canonical Fusion_Part")
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
            "status": "SUPERSEDED_ONLY_FOR_EXACT_GATE001_ACTIVATION_BINDING",
        },
        "bounded_center_budget_rollback_gate": {
            "path": str(BOUNDED_CENTER_GATE_RELATIVE),
            "sha256": BOUNDED_CENTER_GATE_SHA256,
        },
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "activation_validation_only_successor": True,
        "production_solver_threshold_fixture_physical_renderer_changed": False,
        "full_synthetic_qualification_complete": False,
        "real_diagnostic_requires_separate_activation": True,
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
    }
    amendment_path = WORKSPACE / AMENDMENT_018_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_018_RELATIVE),
            "sha256": _sha(amendment_path),
        },
        "amendment_append_only_parent": amendment["append_only_parent"],
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "qualified_source_hashes": source_hashes,
        "synthetic_qualification_status": (
            "USER_AUTHORIZED_DIAGNOSTIC_SPRINT;FULL_QUALIFICATION_PENDING"
        ),
        "activation_validation_delta": (
            "EXACT_GATE001_MATURE_ROLLBACK_INVARIANTS;"
            "UNREGISTERED_PHYSICAL_HISTORY_NONEMPTY_PREDICATE_EXCLUDED"
        ),
        "real_fit_authorized_after_registry_alone": False,
        "real_diagnostic_requires_separate_activation": True,
        "heldout_opened": False,
    }
    seal_path = WORKSPACE / SEAL_018_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_018": str(AMENDMENT_018_RELATIVE),
        "amendment_018_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_018": str(SEAL_018_RELATIVE),
        "seal_018_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "heldout_opened": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
