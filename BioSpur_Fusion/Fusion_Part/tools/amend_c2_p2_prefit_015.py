#!/usr/bin/env python3
"""Schema-only successor restoring the runtime-required false authority key."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_014 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_014_RELATIVE
PARENT_AMENDMENT_SHA256 = "866edaa21a62fc1e4f038fedd821fb643afc940f498c988867d7e39b7c725762"
PARENT_SEAL_RELATIVE = base.SEAL_014_RELATIVE
PARENT_SEAL_SHA256 = "e9d387009971ea2bd4b18dcfe293d93e54c8123447124e206c345793a7fd752a"
FAILED_ACTIVATION_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ACTIVATION_004.json"
FAILED_ACTIVATION_SHA256 = "dd11848b79b6c91b9395bb59ebead406c3fae24536bf48f7a8c927c1741e2a2f"
FAILED_ATTEMPT_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ATTEMPT_004.json"
FAILED_ATTEMPT_SHA256 = "d9ba4b289bd95ce16e5881a07f4cc2d53553ed8d7dc4e106807c009f9ffaaa5b"
FAILED_RUNNING_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_RUNNING_004.json"
FAILED_RUNNING_SHA256 = "e6ee2bd12f129853152b93f3b9101df2deeea5dc0cb221820ea5b96118d0e166"
FOCUSED_GATE_RELATIVE = RUN_RELATIVE / "CONTINUATION_SPRINT/FOCUSED_CALIBRATION_TEST_GATE_008.json"
FOCUSED_GATE_SHA256 = "bbebeea5765af4f06fcb843774d523568ca1d4dc7956111b62a5b3d043323c57"
AMENDMENT_015_RELATIVE = RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_015.json"
SEAL_015_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_015.json"
MANDATORY_QUALIFIED_SOURCE_PATHS = tuple(dict.fromkeys((
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_015.py",
    "tools/check_c2_center_budget_rollback.py",
)))

_sha = base._sha
_semantic_sha = base._semantic_sha
_write_new_immutable = base._write_new_immutable


def _require(relative: Path, expected: str) -> None:
    path = WORKSPACE / relative
    if not path.is_file() or _sha(path) != expected:
        raise RuntimeError(f"schema-compatibility successor input changed: {relative}")


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("schema-compatibility successor requires canonical Fusion_Part")
    for relative, expected in (
        (PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256),
        (PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256),
        (FAILED_ACTIVATION_RELATIVE, FAILED_ACTIVATION_SHA256),
        (FAILED_ATTEMPT_RELATIVE, FAILED_ATTEMPT_SHA256),
        (FAILED_RUNNING_RELATIVE, FAILED_RUNNING_SHA256),
        (FOCUSED_GATE_RELATIVE, FOCUSED_GATE_SHA256),
    ):
        _require(relative, expected)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_015_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    settings["joint_center"]["factor_maximum_solver_calls"] = 100
    settings["joint_center"]["factor_maximum_total_function_evaluations"] = 40000
    settings["joint_center"]["factor_aggregate_budget_provenance"] = (
        "PREOUTCOME_THREE_HOUR_SPRINT_BOUND:100_CALLS_IS_ABOVE_THE_78_CALLS_"
        "NEEDED_FOR_13_FULL_AND_13_CAUSAL_PREFIX_MULTISTARTS_AT_THREE_"
        "FEASIBLE_GLS_PASSES_EACH_BUT_IS_MATERIALLY_BELOW_THE_834_CALL_"
        "FULL_252_REFIT_QUALIFICATION_PATH;40000_IS_100_TIMES_THE_EXISTING_"
        "400_MAX_NFEV_PER_CALL;NOT_SELECTED_FROM_A_SUCCESSFUL_TRAJECTORY"
    )
    settings["joint_center"]["factor_aggregate_budget_exhaustion_policy"] = (
        "ESCAPE_COHERENT_REFIT_FAILURE_RETENTION;ROLLBACK_EXACT_EPISODE_"
        "CHECKPOINT;COMMIT_CURRENT_PAIR_AS_LOCAL_NO_UPDATE_ON_ONE_BOUNDED_RETRY"
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "failed_diagnostic_seal_014": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "PREPAYLOAD_FOCUSED_GATE_SCHEMA_COMPATIBILITY_FAILURE",
        },
        "failed_diagnostic_attempt_004": {
            "path": str(FAILED_ATTEMPT_RELATIVE),
            "sha256": FAILED_ATTEMPT_SHA256,
            "actions_read": 0,
            "actions_committed": 0,
            "heldout_opened": False,
        },
        "bounded_pivot_015": (
            "GATE_008_PRESERVES_THE_NEW_DIAGNOSTIC_AUTHORITY_FALSE_FIELD_AND_"
            "RESTORES_LEGACY_REAL_FIT_AUTHORIZED_BY_THIS_GATE_FALSE;NO_"
            "SCIENTIFIC_OWNER_THRESHOLD_OR_TEST_RESULT_CHANGED;THE_SAME_FIVE_"
            "FOCUSED_TESTS_BIND_THE_FINAL_BOUNDED_SOLVER_SOURCE"
        ),
    }
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("schema-compatibility source closure requires canonical Fusion_Part")
    output = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory successor source is missing: {relative}")
        output[relative] = _sha(path)
    return output


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("run schema-compatibility successor only from canonical Fusion_Part")
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
            "status": "FAILED_PREPAYLOAD_GATE_SCHEMA_COMPATIBILITY",
        },
        "failed_diagnostic_attempt": {
            "path": str(FAILED_ATTEMPT_RELATIVE),
            "sha256": FAILED_ATTEMPT_SHA256,
        },
        "focused_owner_test_gate": {
            "path": str(FOCUSED_GATE_RELATIVE),
            "sha256": FOCUSED_GATE_SHA256,
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
    amendment_path = WORKSPACE / AMENDMENT_015_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_015_RELATIVE),
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
    seal_path = WORKSPACE / SEAL_015_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_015": str(AMENDMENT_015_RELATIVE),
        "amendment_015_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_015": str(SEAL_015_RELATIVE),
        "seal_015_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "heldout_opened": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
