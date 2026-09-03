#!/usr/bin/env python3
"""Append-only successor registry/seal after qualification attempt 003."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_006 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
MONITOR_TASK_ID = base.MONITOR_TASK_ID
P1_MONITOR_ACCEPTANCE_RELATIVE = base.P1_MONITOR_ACCEPTANCE_RELATIVE
P1_MONITOR_ACCEPTANCE_SHA256 = base.P1_MONITOR_ACCEPTANCE_SHA256
REAL_ACTIVATION_RELATIVE = base.REAL_ACTIVATION_RELATIVE
SENSOR_AND_NUMERICAL_MUTATIONS = base.SENSOR_AND_NUMERICAL_MUTATIONS
ARCHITECTURE_MUTATIONS = base.ARCHITECTURE_MUTATIONS

PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_006_RELATIVE
PARENT_AMENDMENT_SHA256 = "fe7632152e537103d5f6963bb5c7a164b4902ec2f64b11192ce50905cdb1ae3b"
PARENT_SEAL_RELATIVE = base.SEAL_006_RELATIVE
PARENT_SEAL_SHA256 = "1b02e7cf4fde2a34831d4a793fdbaa8799959df06517d4e07e10dfa52fdbe86e"
FAILED_QUALIFICATION_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_SYNTHETIC_QUALIFICATION_ATTEMPT_003.json"
)
FAILED_QUALIFICATION_SHA256 = (
    "5f3b85422f0822dffa0e737ddfd947e4402472109c662072c18bfdac05a0a615"
)
QUALIFIED_TEST_GATE_RELATIVE = RUN_RELATIVE / "P2_PREFIT_QUALIFIED_TEST_GATE_003.json"
QUALIFIED_TEST_GATE_SHA256 = (
    "88333b12972fa7ee2fbc9623a6f83d228104ec6a95b2191dd309aad4d6651ff2"
)
POST_QMT_REPAIR_GATE_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_POST_QMT_PAIR_STAGE_REPAIR_GATE_168.json"
)
POST_QMT_REPAIR_GATE_SHA256 = (
    "59bda1f540521cf48e769aa7fbdd44a367f44b3b2e4fab202c6e9f8b7fe90e65"
)

AMENDMENT_007_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_007.json"
)
SEAL_007_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_007.json"

MANDATORY_QUALIFIED_SOURCE_PATHS = (
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_007.py",
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
        (POST_QMT_REPAIR_GATE_RELATIVE, POST_QMT_REPAIR_GATE_SHA256),
    ):
        _require_hash(relative, expected)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_007_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "superseded_amendment_006": {
            "path": str(PARENT_AMENDMENT_RELATIVE),
            "sha256": PARENT_AMENDMENT_SHA256,
        },
        "superseded_seal_006": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_QUALIFICATION_ATTEMPT_003_FROZEN_EVIDENCE",
        },
        "failed_qualification_attempt_003": {
            "path": str(FAILED_QUALIFICATION_RELATIVE),
            "sha256": FAILED_QUALIFICATION_SHA256,
            "failure_stage": "IMPORT_AND_RUN_INDEPENDENT_OWNER_LEVEL_QUALIFICATION",
            "exception": (
                "ClassAGuardViolation: PIPELINE_STAGE_ORDER_BYPASS in the "
                "synthetic post-QMT fixture alignment order"
            ),
        },
        "passed_qualified_tests_before_failure_003": {
            "path": str(QUALIFIED_TEST_GATE_RELATIVE),
            "sha256": QUALIFIED_TEST_GATE_SHA256,
        },
        "post_qmt_pair_stage_repair_gate": {
            "path": str(POST_QMT_REPAIR_GATE_RELATIVE),
            "sha256": POST_QMT_REPAIR_GATE_SHA256,
        },
        "repair_scope_007": (
            "ALIGN_EXACT_NINE_EDGE_RUNTIME_OWNED_PAIRS_ONCE_IN_LOCAL_FACTOR_"
            "STAGE_AND_REUSE_IDENTICAL_BINDINGS_POST_FRAME"
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
            "status": "FAILED_QUALIFICATION_ATTEMPT_003_FROZEN_EVIDENCE",
        },
        "failed_qualification": failure,
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "synthetic_outcome_observed": True,
        "observed_synthetic_outcome": "FAILED_SEAL_006_QUALIFICATION_ATTEMPT_003",
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
        "source_change_policy": (
            "ANY_QUALIFIED_SOURCE_CHANGE_INVALIDATES_SEAL_007_AND_REQUIRES_"
            "ANOTHER_APPEND_ONLY_SUCCESSOR"
        ),
    }
    amendment_path = WORKSPACE / AMENDMENT_007_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_007_RELATIVE),
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
    seal_path = WORKSPACE / SEAL_007_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_007": str(AMENDMENT_007_RELATIVE),
        "amendment_007_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_007": str(SEAL_007_RELATIVE),
        "seal_007_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "real_fit_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
