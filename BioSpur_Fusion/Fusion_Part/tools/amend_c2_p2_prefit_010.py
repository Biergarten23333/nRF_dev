#!/usr/bin/env python3
"""Append-only successor registry/seal after qualification attempt 006."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_009 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
MONITOR_TASK_ID = base.MONITOR_TASK_ID
P1_MONITOR_ACCEPTANCE_RELATIVE = base.P1_MONITOR_ACCEPTANCE_RELATIVE
P1_MONITOR_ACCEPTANCE_SHA256 = base.P1_MONITOR_ACCEPTANCE_SHA256
REAL_ACTIVATION_RELATIVE = base.REAL_ACTIVATION_RELATIVE
SENSOR_AND_NUMERICAL_MUTATIONS = base.SENSOR_AND_NUMERICAL_MUTATIONS
ARCHITECTURE_MUTATIONS = base.ARCHITECTURE_MUTATIONS

PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_009_RELATIVE
PARENT_AMENDMENT_SHA256 = "d8140603a06343d0917db5b88d1a0fca45fe1569fb1e1de50c788ce5ec232f93"
PARENT_SEAL_RELATIVE = base.SEAL_009_RELATIVE
PARENT_SEAL_SHA256 = "2b448e5b0cd88516735e2a96bdbe70d252227146069ffdfbff0c1df276e6b016"
FAILED_QUALIFICATION_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_SYNTHETIC_QUALIFICATION_ATTEMPT_006.json"
)
FAILED_QUALIFICATION_SHA256 = (
    "5ada72f8c1dfd28ed7a9e45823675394127b9a562d662c1d05d64c1e79b1e8b4"
)
QUALIFIED_TEST_GATE_RELATIVE = RUN_RELATIVE / "P2_PREFIT_QUALIFIED_TEST_GATE_006.json"
QUALIFIED_TEST_GATE_SHA256 = (
    "0ef911b6616a55f49db14e572788f8974da37b65fa3c8a82e799f93bd6a8ce18"
)
SENSOR_MUTATION_GATE_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_SENSOR_NUMERICAL_MUTATION_GATE_006.json"
)
SENSOR_MUTATION_GATE_SHA256 = (
    "3566a690c1ff93922d2b7178f0235e74df87b83c98a6ecda1c394378b568bdf7"
)
ARCHITECTURE_MUTATION_GATE_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ARCHITECTURE_MUTATION_GATE_006.json"
)
ARCHITECTURE_MUTATION_GATE_SHA256 = (
    "ec3222606157e93f19e71010145aa69d489e06bd3745247096666ff34e2078c1"
)
FAILURE_AUDIT_RELATIVE = RUN_RELATIVE / "P2_PREFIT_QUALIFICATION_FAILURE_AUDIT_006.json"
FAILURE_AUDIT_SHA256 = (
    "71832ce5d028f5f3cb6c1b192e6eab100abb25dea12209f292e0af9f60cef0f5"
)
PREDICATE_REPAIR_GATE_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_LOW_INFORMATION_PREDICATE_REPAIR_GATE_188.json"
)
PREDICATE_REPAIR_GATE_SHA256 = (
    "2edd160f6bcc4cbee7bc6c6229588fdf74b2f7aabd9096e16e2a4496b6b0e22d"
)

AMENDMENT_010_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_010.json"
)
SEAL_010_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_010.json"

MANDATORY_QUALIFIED_SOURCE_PATHS = (
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_010.py",
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
        (SENSOR_MUTATION_GATE_RELATIVE, SENSOR_MUTATION_GATE_SHA256),
        (ARCHITECTURE_MUTATION_GATE_RELATIVE, ARCHITECTURE_MUTATION_GATE_SHA256),
        (FAILURE_AUDIT_RELATIVE, FAILURE_AUDIT_SHA256),
        (PREDICATE_REPAIR_GATE_RELATIVE, PREDICATE_REPAIR_GATE_SHA256),
    ):
        _require_hash(relative, expected)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_010_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "superseded_amendment_009": {
            "path": str(PARENT_AMENDMENT_RELATIVE),
            "sha256": PARENT_AMENDMENT_SHA256,
        },
        "superseded_seal_009": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_QUALIFICATION_ATTEMPT_006_FROZEN_EVIDENCE",
        },
        "failed_qualification_attempt_006": {
            "path": str(FAILED_QUALIFICATION_RELATIVE),
            "sha256": FAILED_QUALIFICATION_SHA256,
            "failure_stage": "SENSOR_NUMERICAL_MUTATION_RESULT",
            "failed_mutations": ["STATIC_LOW_INFORMATION", "NEAR_AXIS_ONLY"],
            "cause": (
                "MUTATION_PREDICATES_IGNORED_EXPLICIT_OWNER_LOCAL_NO_UPDATE_"
                "EVIDENCE_WHILE_SCIENTIFIC_OWNERS_CORRECTLY_REJECTED_UPDATE"
            ),
        },
        "passed_qualified_tests_before_failure_006": {
            "path": str(QUALIFIED_TEST_GATE_RELATIVE),
            "sha256": QUALIFIED_TEST_GATE_SHA256,
        },
        "failed_sensor_mutation_gate_006": {
            "path": str(SENSOR_MUTATION_GATE_RELATIVE),
            "sha256": SENSOR_MUTATION_GATE_SHA256,
            "passed_count": 42,
            "registered_count": 44,
        },
        "bounded_architecture_mutation_gate_006": {
            "path": str(ARCHITECTURE_MUTATION_GATE_RELATIVE),
            "sha256": ARCHITECTURE_MUTATION_GATE_SHA256,
            "passed_count": 52,
            "registered_count": 52,
            "scope": "ATTEMPT_006_BOUNDED_EVIDENCE_ONLY",
        },
        "qualification_failure_audit_006": {
            "path": str(FAILURE_AUDIT_RELATIVE),
            "sha256": FAILURE_AUDIT_SHA256,
        },
        "low_information_predicate_repair_gate_188": {
            "path": str(PREDICATE_REPAIR_GATE_RELATIVE),
            "sha256": PREDICATE_REPAIR_GATE_SHA256,
        },
        "repair_scope_010": (
            "BIND_STATIC_AND_NEAR_AXIS_MUTATIONS_TO_EXPLICIT_AUTHORITATIVE_"
            "OWNER_LOCAL_NO_UPDATE_EVIDENCE_WITH_SUCCESSFUL_SELECTION_HESSIAN_"
            "SUPPORT_AND_RANK_UNABLE_TO_OVERRIDE_INCOMPLETE_NUISANCE_PROPAGATION_"
            "WITHOUT_SETTINGS_OR_THRESHOLD_CHANGE"
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
            "status": "FAILED_QUALIFICATION_ATTEMPT_006_FROZEN_EVIDENCE",
        },
        "failed_qualification": failure,
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "synthetic_outcome_observed": True,
        "observed_synthetic_outcome": "FAILED_SEAL_009_QUALIFICATION_ATTEMPT_006",
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
        "source_change_policy": (
            "ANY_QUALIFIED_SOURCE_CHANGE_INVALIDATES_SEAL_010_AND_REQUIRES_"
            "ANOTHER_APPEND_ONLY_SUCCESSOR"
        ),
    }
    amendment_path = WORKSPACE / AMENDMENT_010_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_010_RELATIVE),
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
    seal_path = WORKSPACE / SEAL_010_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_010": str(AMENDMENT_010_RELATIVE),
        "amendment_010_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_010": str(SEAL_010_RELATIVE),
        "seal_010_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "real_fit_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
