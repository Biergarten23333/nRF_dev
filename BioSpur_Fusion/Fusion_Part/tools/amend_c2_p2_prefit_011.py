#!/usr/bin/env python3
"""Append-only sprint seal for the user-selected calibration posterior."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_010 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_010_RELATIVE
PARENT_AMENDMENT_SHA256 = "3b695e89f23ae35da55dfa3cf9c40c28bcd175686d875f69500def58ffc0e300"
PARENT_SEAL_RELATIVE = base.SEAL_010_RELATIVE
PARENT_SEAL_SHA256 = "68dd2777711d948adb618ea65787a650630764aad6423b4e961aaa3148f96844"
USER_AMENDMENT_RELATIVE = RUN_RELATIVE / "USER_CALIBRATION_POSTERIOR_AMENDMENT_001.json"
USER_AMENDMENT_SHA256 = "3465eb0705d0e308fbbd0f0d48f363917d007437b6754404483474c93c4960c8"
USER_VALIDATION_RELATIVE = RUN_RELATIVE / "USER_CALIBRATION_POSTERIOR_AMENDMENT_VALIDATION_001.json"
USER_VALIDATION_SHA256 = "16a1fc118de5681dacfd793b13b4fa0d73b51063379fd967420b14696b524872"
AMENDMENT_011_RELATIVE = RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_011.json"
SEAL_011_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_011.json"
MANDATORY_QUALIFIED_SOURCE_PATHS = tuple(dict.fromkeys((
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "src/biospur_fusion/v0/c2_progressive/calibration_posterior.py",
    "tools/run_c2_calibration_diagnostic.py",
    "tools/activate_c2_calibration_diagnostic.py",
    "tools/amend_c2_p2_prefit_011.py",
)))

_sha = base._sha
_semantic_sha = base._semantic_sha
_write_new_immutable = base._write_new_immutable


def _require(relative: Path, expected: str) -> None:
    path = WORKSPACE / relative
    if not path.is_file() or _sha(path) != expected:
        raise RuntimeError(f"sprint successor authority changed: {relative}")


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("sprint successor requires canonical Fusion_Part")
    for relative, expected in (
        (PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256),
        (PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256),
        (USER_AMENDMENT_RELATIVE, USER_AMENDMENT_SHA256),
        (USER_VALIDATION_RELATIVE, USER_VALIDATION_SHA256),
    ):
        _require(relative, expected)
    settings = deepcopy(base.build_effective_settings(root))
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_011_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["user_calibration_posterior_amendment"] = {
        "path": str(USER_AMENDMENT_RELATIVE),
        "sha256": USER_AMENDMENT_SHA256,
    }
    execution["real_diagnostic_runner"] = {
        "authoritative_entrypoint": "tools/run_c2_calibration_diagnostic.py",
        "activation_entrypoint": "tools/activate_c2_calibration_diagnostic.py",
        "execution_role": "REAL_DIAGNOSTIC",
        "training_ranges_only": True,
        "one_bounded_reader_session": True,
        "heldout_opened": False,
        "fresh_verification_or_final_pass_claim_allowed": False,
        "legacy_c2_basis_ik_rebase_anthropometric_fit_allowed": False,
        "ordinary_local_failure_policy": "ROLLBACK_LOCAL_NO_UPDATE_AND_CONTINUE",
    }
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "superseded_failed_seal_010": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "FAILED_QUALIFICATION_ATTEMPT_007_FROZEN_EVIDENCE",
        },
        "user_no_recapture_authority": {
            "path": str(USER_AMENDMENT_RELATIVE),
            "sha256": USER_AMENDMENT_SHA256,
        },
        "repair_scope_011": (
            "CAPTURE_WIDE_PER_SENSOR_PERSISTENT_MULTI_BRANCH_RESIDUAL_"
            "CALIBRATION_POSTERIOR_WITH_CLASS_C_MARGINALIZATION_AND_ONE_"
            "TRAINING_RANGE_ONLY_REAL_DIAGNOSTIC_BEFORE_FULL_QUALIFICATION"
        ),
    }
    policy = settings["scientific_renderer"]["source_label_policy"]
    policy["real_diagnostic_label"] = (
        "REAL C2 TRAINING-RANGE DIAGNOSTIC / NOT FRESH-VERIFIED / NOT PASS"
    )
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("sprint source closure requires canonical Fusion_Part")
    output = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory sprint source is missing: {relative}")
        output[relative] = _sha(path)
    return output


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("run sprint successor only from canonical Fusion_Part")
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
            "status": "FAILED_QUALIFICATION_ATTEMPT_007_FROZEN_EVIDENCE",
        },
        "user_calibration_posterior_amendment": {
            "path": str(USER_AMENDMENT_RELATIVE),
            "sha256": USER_AMENDMENT_SHA256,
        },
        "user_calibration_posterior_amendment_validation": {
            "path": str(USER_VALIDATION_RELATIVE),
            "sha256": USER_VALIDATION_SHA256,
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
    amendment_path = WORKSPACE / AMENDMENT_011_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_011_RELATIVE),
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
    seal_path = WORKSPACE / SEAL_011_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_011": str(AMENDMENT_011_RELATIVE),
        "amendment_011_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_011": str(SEAL_011_RELATIVE),
        "seal_011_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "real_diagnostic_requires_separate_activation": True,
        "heldout_opened": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
