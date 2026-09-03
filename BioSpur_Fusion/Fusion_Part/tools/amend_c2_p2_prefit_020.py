#!/usr/bin/env python3
"""Single rollback successor preserving current-pair opaque owner identities."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_019 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_019_RELATIVE
PARENT_AMENDMENT_SHA256 = (
    "2603f7642fc84cca07be917601789b4444f9d0503b9c3c9cbf047fd820e89119"
)
PARENT_SEAL_RELATIVE = base.SEAL_019_RELATIVE
PARENT_SEAL_SHA256 = (
    "77a5ef364e5a71bc851e19f1673ec0b0097601bd0fe8db46557d585759d2aae1"
)
FAILED_REAL_ATTEMPT_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ATTEMPT_006.json"
FAILED_REAL_ATTEMPT_SHA256 = (
    "1392ddc08251c856aa6b8c62d3a98f5ec74be09767c6a0d4ea6da7c3f36996df"
)
SUPERSEDED_ACTIVATION_RELATIVE = (
    RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ACTIVATION_006.json"
)
SUPERSEDED_ACTIVATION_SHA256 = (
    "875a4f64238ca0ae142fedfd7ab94f1e5ef737525b71c36b9255277e81f03213"
)
AMENDMENT_020_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_020.json"
)
SEAL_020_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_020.json"
MANDATORY_QUALIFIED_SOURCE_PATHS = tuple(dict.fromkeys((
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_020.py",
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
        raise RuntimeError(f"seal-020 immutable input changed: {relative}")


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("seal-020 successor requires canonical Fusion_Part")
    _require(PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256)
    _require(PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256)
    _require(FAILED_REAL_ATTEMPT_RELATIVE, FAILED_REAL_ATTEMPT_SHA256)
    _require(SUPERSEDED_ACTIVATION_RELATIVE, SUPERSEDED_ACTIVATION_SHA256)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_020_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "seal_019_real_attempt_006_current_episode_rollback_gap": {
            "seal": {"path": str(PARENT_SEAL_RELATIVE), "sha256": PARENT_SEAL_SHA256},
            "activation": {
                "path": str(SUPERSEDED_ACTIVATION_RELATIVE),
                "sha256": SUPERSEDED_ACTIVATION_SHA256,
            },
            "attempt": {
                "path": str(FAILED_REAL_ATTEMPT_RELATIVE),
                "sha256": FAILED_REAL_ATTEMPT_SHA256,
            },
            "cause": (
                "SEMANTICALLY_EQUAL_DEEPCOPIED_SLICE_VALUES_WERE_HASHED_BY_"
                "PROCESS_IDENTITY_IN_CURRENT_PAIR_AND_CENTER_SELECTION_ROLLBACK"
            ),
            "mismatched_top_level_components": [
                "current_center_prefix_selections", "current_owned_aligned_pairs",
            ],
            "actions_committed_before_failure": 5,
            "heldout_opened": False,
        },
    }
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("seal-020 source closure requires canonical Fusion_Part")
    output: dict[str, str] = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory seal-020 source is missing: {relative}")
        output[relative] = _sha(path)
    return output


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("run seal-020 successor only from canonical Fusion_Part")
    settings = build_effective_settings(WORKSPACE)
    source_hashes = build_qualified_source_hashes(WORKSPACE)
    amendment = {
        "schema": "biospur-c2-active-parameter-registry-prefit-amendment-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_AMENDMENT_RELATIVE), "sha256": PARENT_AMENDMENT_SHA256,
        },
        "superseded_seal_retained": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_REAL_ATTEMPT_CURRENT_EPISODE_ROLLBACK_IDENTITY_GAP",
        },
        "failed_real_attempt": {
            "path": str(FAILED_REAL_ATTEMPT_RELATIVE),
            "sha256": FAILED_REAL_ATTEMPT_SHA256,
        },
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "single_exact_transaction_canonicalization_successor": True,
        "restored_components": [
            "current_center_prefix_selections", "current_owned_aligned_pairs",
        ],
        "slice_canonicalization": "EXACT_START_STOP_STEP_FIELDS",
        "production_solver_threshold_fixture_physical_renderer_changed": False,
        "full_synthetic_qualification_complete": False,
        "real_diagnostic_requires_separate_activation": True,
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
    }
    amendment_path = WORKSPACE / AMENDMENT_020_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE), "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_020_RELATIVE), "sha256": _sha(amendment_path),
        },
        "amendment_append_only_parent": amendment["append_only_parent"],
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "qualified_source_hashes": source_hashes,
        "synthetic_qualification_status": (
            "USER_AUTHORIZED_DIAGNOSTIC_SPRINT;FULL_QUALIFICATION_PENDING"
        ),
        "runtime_delta": "SLICE_START_STOP_STEP_CANONICALIZATION_FOR_ROLLBACK_HASH_ONLY",
        "real_fit_authorized_after_registry_alone": False,
        "real_diagnostic_requires_separate_activation": True,
        "heldout_opened": False,
    }
    seal_path = WORKSPACE / SEAL_020_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_020": str(AMENDMENT_020_RELATIVE),
        "amendment_020_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_020": str(SEAL_020_RELATIVE),
        "seal_020_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "heldout_opened": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
