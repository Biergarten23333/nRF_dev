#!/usr/bin/env python3
"""Append-only successor registry/seal after qualification attempt 005."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_008 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
MONITOR_TASK_ID = base.MONITOR_TASK_ID
P1_MONITOR_ACCEPTANCE_RELATIVE = base.P1_MONITOR_ACCEPTANCE_RELATIVE
P1_MONITOR_ACCEPTANCE_SHA256 = base.P1_MONITOR_ACCEPTANCE_SHA256
REAL_ACTIVATION_RELATIVE = base.REAL_ACTIVATION_RELATIVE
SENSOR_AND_NUMERICAL_MUTATIONS = base.SENSOR_AND_NUMERICAL_MUTATIONS
ARCHITECTURE_MUTATIONS = base.ARCHITECTURE_MUTATIONS

PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_008_RELATIVE
PARENT_AMENDMENT_SHA256 = "b083c6a37883ac7aa5592bb552ec0b209d2933394fbf58ce1811e1ab40b7ce12"
PARENT_SEAL_RELATIVE = base.SEAL_008_RELATIVE
PARENT_SEAL_SHA256 = "31d89338271f0605e5d0a83ad47145b57b507577202f281664ac3c67d2b9096c"
FAILED_QUALIFICATION_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_SYNTHETIC_QUALIFICATION_ATTEMPT_005.json"
)
FAILED_QUALIFICATION_SHA256 = (
    "5019a077ddae414b8ddaa615b3465992ca40155b57677eabb96c69d4e347a1b4"
)
QUALIFIED_TEST_GATE_RELATIVE = RUN_RELATIVE / "P2_PREFIT_QUALIFIED_TEST_GATE_005.json"
QUALIFIED_TEST_GATE_SHA256 = (
    "75c463681aab48c1849f10b7fc6f11147f492c9a3f9b629845bf5a638e1ee2d1"
)
SENSOR_COVERAGE_REPAIR_GATE_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_SENSOR_COVERAGE_CLASS_REPAIR_GATE_181.json"
)
SENSOR_COVERAGE_REPAIR_GATE_SHA256 = (
    "86f7f3db42086a6b60b4868b1ac5783aea45e9149cd1c7c5e4d7ccdea252626f"
)

AMENDMENT_009_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_009.json"
)
SEAL_009_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_009.json"

MANDATORY_QUALIFIED_SOURCE_PATHS = (
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_009.py",
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
        (SENSOR_COVERAGE_REPAIR_GATE_RELATIVE, SENSOR_COVERAGE_REPAIR_GATE_SHA256),
    ):
        _require_hash(relative, expected)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_009_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "superseded_amendment_008": {
            "path": str(PARENT_AMENDMENT_RELATIVE),
            "sha256": PARENT_AMENDMENT_SHA256,
        },
        "superseded_seal_008": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_QUALIFICATION_ATTEMPT_005_FROZEN_EVIDENCE",
        },
        "failed_qualification_attempt_005": {
            "path": str(FAILED_QUALIFICATION_RELATIVE),
            "sha256": FAILED_QUALIFICATION_SHA256,
            "failure_stage": "SENSOR_LEDGER_CANONICAL_COVERAGE_CLASS_VALIDATION",
            "exception": (
                "RuntimeError: sensor mutation lacks owner-level evidence fields: "
                "CENTER_PHYSICAL_TIME_SAME_BOOT_GAP_PRESERVED"
            ),
        },
        "passed_qualified_tests_before_failure_005": {
            "path": str(QUALIFIED_TEST_GATE_RELATIVE),
            "sha256": QUALIFIED_TEST_GATE_SHA256,
        },
        "sensor_coverage_class_repair_gate": {
            "path": str(SENSOR_COVERAGE_REPAIR_GATE_RELATIVE),
            "sha256": SENSOR_COVERAGE_REPAIR_GATE_SHA256,
        },
        "repair_scope_009": (
            "NORMALIZE_TWO_EXECUTED_PHYSICAL_TIME_MUTATION_ROWS_TO_CANONICAL_"
            "EXECUTED_OWNER_LEVEL_WHILE_PRESERVING_SPECIFIC_SCOPE_AS_"
            "COVERAGE_DETAIL_WITHOUT_SCIENTIFIC_CHANGE"
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
            "status": "FAILED_QUALIFICATION_ATTEMPT_005_FROZEN_EVIDENCE",
        },
        "failed_qualification": failure,
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "synthetic_outcome_observed": True,
        "observed_synthetic_outcome": "FAILED_SEAL_008_QUALIFICATION_ATTEMPT_005",
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
        "source_change_policy": (
            "ANY_QUALIFIED_SOURCE_CHANGE_INVALIDATES_SEAL_009_AND_REQUIRES_"
            "ANOTHER_APPEND_ONLY_SUCCESSOR"
        ),
    }
    amendment_path = WORKSPACE / AMENDMENT_009_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_009_RELATIVE),
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
    seal_path = WORKSPACE / SEAL_009_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_009": str(AMENDMENT_009_RELATIVE),
        "amendment_009_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_009": str(SEAL_009_RELATIVE),
        "seal_009_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "real_fit_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
