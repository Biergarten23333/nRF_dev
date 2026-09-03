#!/usr/bin/env python3
"""One bounded successor binding the capture-wide node clock posterior pivot."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_013 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
PARENT_AMENDMENT_RELATIVE = base.AMENDMENT_013_RELATIVE
PARENT_AMENDMENT_SHA256 = "07661d5050317a572e9e1176ecbd38955fcc9deace19ec1c8c9264a670f539ca"
PARENT_SEAL_RELATIVE = base.SEAL_013_RELATIVE
PARENT_SEAL_SHA256 = "31e63f56dd4e235baf638ff54ebd2a1f371e0f87c479803c3811c50c954a7a42"
FAILED_ACTIVATION_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ACTIVATION_003.json"
FAILED_ACTIVATION_SHA256 = "e1d063addbe8071d1a2da27552c58eef504aa4107f8e409dde5d1273003ce8b9"
FAILED_ATTEMPT_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ATTEMPT_003.json"
FAILED_ATTEMPT_SHA256 = "d9d6bbd46f783662d995536edbc103fa0a6fcd84798cc84ed8f302303058342b"
FAILED_READ_AUDIT_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_READ_AUDIT_003.json"
FAILED_READ_AUDIT_SHA256 = "5843e9e7cc3beeb0f6bdc6f67669bdcbc1ff0a472cbaaa2cb2532b88e16eea7e"
PREFIX_04_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_PREFIX_04_RUN_003.json"
PREFIX_04_SHA256 = "04984d91e9c45a50d78abd1681dfd0f69e9527208e773b1f9584fc608260eed6"
FOCUSED_GATE_RELATIVE = RUN_RELATIVE / "CONTINUATION_SPRINT/FOCUSED_CALIBRATION_TEST_GATE_005.json"
AMENDMENT_014_RELATIVE = RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_014.json"
SEAL_014_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_014.json"
MANDATORY_QUALIFIED_SOURCE_PATHS = tuple(dict.fromkeys((
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_014.py",
)))

_sha = base._sha
_semantic_sha = base._semantic_sha
_write_new_immutable = base._write_new_immutable


def _require(relative: Path, expected: str) -> None:
    path = WORKSPACE / relative
    if not path.is_file() or _sha(path) != expected:
        raise RuntimeError(f"clock-posterior pivot authority changed: {relative}")


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("clock-posterior pivot requires canonical Fusion_Part")
    for relative, expected in (
        (PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256),
        (PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256),
        (FAILED_ACTIVATION_RELATIVE, FAILED_ACTIVATION_SHA256),
        (FAILED_ATTEMPT_RELATIVE, FAILED_ATTEMPT_SHA256),
        (FAILED_READ_AUDIT_RELATIVE, FAILED_READ_AUDIT_SHA256),
        (PREFIX_04_RELATIVE, PREFIX_04_SHA256),
    ):
        _require(relative, expected)
    settings = deepcopy(base.build_effective_settings(root))
    timing = settings["timing"]
    timing["model"] = (
        "CAPTURE_WIDE_PER_SENSOR_AFFINE_CLOCK_HYPOTHESES_FEED_ONE_"
        "PERSISTENT_PAIR_OFFSET_DRIFT_STATE;LOCAL_CORRELATION_LAGS_ARE_"
        "CHRONOLOGICAL_OBSERVATIONS_ONLY"
    )
    timing["capture_wide_node_clock"] = {
        "schema": "biospur-c2-capture-wide-node-clock-settings-v1",
        "root": "SEALED_PELVIS_HARDWARE_NODE",
        "hypothesis_ids": ["START", "MIDPOINT", "END"],
        "anchor_semantics": (
            "RESULT_INDEPENDENT_OBSERVED_GRID_ANCHORS_RETAINED_SEPARATELY;"
            "NOT_PER_ACTION_LAG_PROFILES"
        ),
        "update_boundary": "AFTER_PREQUENTIAL_SCORE_BEFORE_CURRENT_LOCAL_FACTORS",
        "state_scope": "CAPTURE_WIDE_PER_SENSOR_PERSISTENT_AFFINE_OFFSET_DRIFT",
        "pair_projection": "CHILD_TO_ROOT_MINUS_PARENT_TO_ROOT",
        "within_node_prediction_covariance_combination": "CONSERVATIVE_SUM",
        "between_hypothesis_variance_propagated": True,
        "hypothesis_factor_row_selection": (
            "EXISTING_GAP_LOCAL_CENTERED_GYRO_ENERGY_CORRELATION;ALL_"
            "ALTERNATIVES_AND_FAILURES_RETAINED"
        ),
        "factor_row_hypothesis_policy": (
            "ONE_SELECTED_BY_MATURE_GAP_LOCAL_GYRO_CORRELATION"
        ),
        "alternatives_retained_in": (
            "MOMENT_MATCHED_WITHIN_PLUS_BETWEEN_TIMING_COVARIANCE_AND_AUDIT"
        ),
        "branch_specific_geometry_refits_per_clock_hypothesis": False,
        "full_clock_multibranch_geometry_propagation_claimed": False,
        "persistent_edge_affine_prior_preferred_after_first_edge_observation": True,
        "scientific_status": "DIAGNOSTIC_NOT_PASS",
        "independent_longest_span_selection_allowed": False,
        "gap_or_boot_stitching_allowed": False,
        "future_or_heldout_allowed": False,
        "spatial_or_pose_truth_allowed": False,
        "no_valid_hypothesis_policy": "LOCAL_NO_UPDATE_WITH_CAUSE_RETENTION",
    }
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_014_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["append_only_failed_prefit_lineage"] = {
        **dict(execution["append_only_failed_prefit_lineage"]),
        "failed_diagnostic_seal_013": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "SUPERSEDED_AFTER_UNIQUE_RIGHT_SHOULDER_TIMING_NO_UPDATE",
        },
        "failed_diagnostic_attempt_003": {
            "path": str(FAILED_ATTEMPT_RELATIVE),
            "sha256": FAILED_ATTEMPT_SHA256,
            "actions_read": 19,
            "actions_committed": 9,
            "heldout_opened": False,
        },
        "failed_attempt_003_read_audit": {
            "path": str(FAILED_READ_AUDIT_RELATIVE),
            "sha256": FAILED_READ_AUDIT_SHA256,
        },
        "material_prefix_04_timing_no_update": {
            "path": str(PREFIX_04_RELATIVE),
            "sha256": PREFIX_04_SHA256,
            "edge": "shoulder_right",
            "only_registered_geometry_action": "05_shoulder_right",
            "geometry_information_added": False,
            "cause": "MULTIPLE_SPANS_WITHOUT_CAUSAL_PAIR_CLOCK_PRIOR",
        },
        "bounded_pivot_014": (
            "CAPTURE_WIDE_PER_SENSOR_THREE_HYPOTHESIS_AFFINE_CLOCK_"
            "POSTERIOR_BOOTSTRAPS_GAP_SAFE_CORRESPONDENCE_BEFORE_THE_"
            "EXISTING_PERSISTENT_PAIR_CLOCK_CORRELATION_OWNER"
        ),
    }
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("clock-posterior source closure requires canonical Fusion_Part")
    output = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory clock-posterior source is missing: {relative}")
        output[relative] = _sha(path)
    return output


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("run clock-posterior successor only from canonical Fusion_Part")
    gate = WORKSPACE / FOCUSED_GATE_RELATIVE
    if not gate.is_file():
        raise RuntimeError("focused clock-posterior gate is absent")
    gate_document = json.loads(gate.read_text(encoding="utf-8"))
    if (
        gate_document.get("status") != "PASS_FOCUSED_OWNER_ONLY"
        or gate_document.get("failed") != 0
        or gate_document.get("payload_opened") is not False
        or gate_document.get("heldout_opened") is not False
    ):
        raise RuntimeError("focused clock-posterior gate is not eligible")
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
            "status": "FAILED_REAL_DIAGNOSTIC_MATERIAL_CLOCK_OWNERSHIP_GAP",
        },
        "failed_diagnostic_attempt": {
            "path": str(FAILED_ATTEMPT_RELATIVE),
            "sha256": FAILED_ATTEMPT_SHA256,
        },
        "material_prefix_evidence": {
            "path": str(PREFIX_04_RELATIVE),
            "sha256": PREFIX_04_SHA256,
        },
        "focused_owner_test_gate": {
            "path": str(FOCUSED_GATE_RELATIVE),
            "sha256": _sha(gate),
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
    amendment_path = WORKSPACE / AMENDMENT_014_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_014_RELATIVE),
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
    seal_path = WORKSPACE / SEAL_014_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_014": str(AMENDMENT_014_RELATIVE),
        "amendment_014_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_014": str(SEAL_014_RELATIVE),
        "seal_014_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "heldout_opened": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
