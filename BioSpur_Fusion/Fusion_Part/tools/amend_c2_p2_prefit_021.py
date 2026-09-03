#!/usr/bin/env python3
"""One permitted source-closure correction to online-owner seal 021."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tools import amend_c2_p2_prefit_020 as base


WORKSPACE = base.WORKSPACE
RUN_RELATIVE = base.RUN_RELATIVE
PARENT_AMENDMENT_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021.json"
)
PARENT_AMENDMENT_SHA256 = (
    "3fc014e5279b37b40239b47946044e2bace3432264d8cb273be511a50a67ef6a"
)
PARENT_SEAL_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_021.json"
PARENT_SEAL_SHA256 = (
    "ff69946c963db1dc16c50a26cc3c14040c8a34371581ea1e4a28133ac5158235"
)
USER_OWNER_AMENDMENT_RELATIVE = (
    RUN_RELATIVE / "USER_ONLINE_BRANCH_POSTERIOR_OWNER_AMENDMENT_002.json"
)
USER_OWNER_AMENDMENT_SHA256 = (
    "0dc5b1dfbada882aa7fd6d070e9f10480f278df34da28902a2b917268154cf27"
)
RUN007_ATTEMPT_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ATTEMPT_007.json"
RUN007_ATTEMPT_SHA256 = (
    "3a1c1d19792824153a3997d2f240880d28904b7438caa23b6f2494647c0b61bc"
)
RUN007_TRACE_RELATIVE = (
    RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_RUN_007_TERMINAL_TRACE_AUDIT.json"
)
RUN007_TRACE_SHA256 = (
    "0d85103b5afe7c96b72fe86eae831f5f1beb466dfdc097b808aef3e55c3f1c80"
)
AMENDMENT_021_RELATIVE = (
    RUN_RELATIVE
    / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
)
SEAL_021_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_021_SOURCE_CORRECTION_001.json"
)
MANDATORY_QUALIFIED_SOURCE_PATHS = tuple(dict.fromkeys((
    *base.MANDATORY_QUALIFIED_SOURCE_PATHS,
    "tools/amend_c2_p2_prefit_021.py",
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
        raise RuntimeError(f"seal-021 immutable input changed: {relative}")


def build_effective_settings(root: Path = WORKSPACE) -> dict[str, Any]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("seal-021 successor requires canonical Fusion_Part")
    _require(PARENT_AMENDMENT_RELATIVE, PARENT_AMENDMENT_SHA256)
    _require(PARENT_SEAL_RELATIVE, PARENT_SEAL_SHA256)
    _require(USER_OWNER_AMENDMENT_RELATIVE, USER_OWNER_AMENDMENT_SHA256)
    _require(RUN007_ATTEMPT_RELATIVE, RUN007_ATTEMPT_SHA256)
    _require(RUN007_TRACE_RELATIVE, RUN007_TRACE_SHA256)
    settings = deepcopy(json.loads(
        (root / PARENT_AMENDMENT_RELATIVE).read_text(encoding="utf-8")
    )["effective_settings"])
    execution = settings["execution_contract"]
    execution["prefit_registry_seal_relative_path"] = str(SEAL_021_RELATIVE)
    execution["mandatory_qualified_source_paths"] = list(
        MANDATORY_QUALIFIED_SOURCE_PATHS
    )
    execution["user_online_branch_posterior_owner_amendment"] = {
        "path": str(USER_OWNER_AMENDMENT_RELATIVE),
        "sha256": USER_OWNER_AMENDMENT_SHA256,
    }
    execution["run007_owner_replacement_evidence"] = {
        "attempt": {
            "path": str(RUN007_ATTEMPT_RELATIVE),
            "sha256": RUN007_ATTEMPT_SHA256,
        },
        "terminal_trace": {
            "path": str(RUN007_TRACE_RELATIVE),
            "sha256": RUN007_TRACE_SHA256,
        },
        "committed_prefixes": 19,
        "center_budget_pivots": 12,
        "cause": (
            "OFFLINE_834_CALL_COHERENT_NUISANCE_VALIDATION_WAS_AN_"
            "INCOMPATIBLE_ONLINE_ADMISSION_PREREQUISITE"
        ),
    }
    owner_settings = {
        "schema": "biospur-c2-online-branch-posterior-owner-v1",
        "enabled": True,
        "full_coherent_nuisance_refits_online": False,
        "full_coherent_nuisance_refits_role": (
            "OFFLINE_FINAL_VALIDATION_ONLY_AFTER_REAL_TRAJECTORY_PROOF"
        ),
        "finite_candidate_retention": (
            "EVERY_SUCCESSFUL_NUMERICAL_INTERIOR_NOMINAL_OR_CAUSAL_PREFIX_"
            "CANDIDATE_WITH_NORMALIZED_COST_WEIGHT"
        ),
        "incomplete_nuisance_center_sigma_m": 0.05,
        "incomplete_nuisance_center_sigma_provenance": (
            "PREEXISTING_FROZEN_50_MM_INTERIOR_BASIN_STRESS_RADIUS;USED_AS_"
            "ONLINE_UNRESOLVED_NUISANCE_COVARIANCE_NOT_AS_ANATOMICAL_TRUTH"
        ),
        "incomplete_nuisance_axis_sigma_rad": 0.2617993877991494,
        "incomplete_nuisance_axis_sigma_provenance": (
            "THREE_TIMES_THE_PREEXISTING_FIVE_DEGREE_HUMAN_WORN_AXIS_FLOOR;"
            "REGISTERED_BROAD_ONLINE_UNRESOLVED_NUISANCE_ANGLE"
        ),
        "between_candidate_covariance_required": True,
        "candidate_point_identifiability_required": False,
        "physical_topology_hard_gates_changed": False,
        "threshold_relaxation": False,
    }
    settings["joint_center"]["online_branch_posterior_owner"] = deepcopy(
        owner_settings
    )
    settings["hinge_axis"]["online_branch_posterior_owner"] = deepcopy(
        owner_settings
    )
    settings["scientific_renderer"]["partial_sensor_axis_proxy"] = {
        "schema": "biospur-c2-real-array-sensor-axis-proxy-renderer-v1",
        "source": (
            "IMMUTABLE_CONTINUOUS_VQF_WORLD_FROM_SENSOR_QUATERNIONS_AND_"
            "ACCEPTED_FUNCTIONAL_HINGE_AXIS_ARRAYS"
        ),
        "sample_quantile_by_action": {
            "00_initial_still": 0.0,
            "04_shoulder_left": 0.5,
            "08_hip_left": 0.5,
            "09_hip_right": 0.5,
            "16_squat": 0.5,
            "17_final_still": 1.0,
        },
        "schematic_position_m_by_segment": {
            "pelvis": [-0.85, -0.09, 0.47],
            "torso": [-0.66, 0.47, -0.28],
            "upper_arm_left": [-0.47, 0.85, 0.09],
            "forearm_left": [-0.28, -0.66, 0.85],
            "upper_arm_right": [-0.09, 0.28, -0.66],
            "forearm_right": [0.09, -0.85, 0.66],
            "thigh_left": [0.28, 0.66, -0.47],
            "shank_left": [0.47, -0.28, 0.28],
            "thigh_right": [0.66, 0.09, -0.85],
            "shank_right": [0.85, -0.47, -0.09],
        },
        "schematic_positions_are_scientific_geometry": False,
        "allowed_as_fk_input": False,
        "sensor_axis_glyph_length_m": 0.12,
        "functional_axis_glyph_length_m": 0.20,
        "sensor_axis_colors": ["#d73027", "#1a9850", "#4575b4"],
        "sensor_axis_labels": ["+X", "+Y", "+Z"],
        "functional_axis_color": "#7e22ce",
        "schematic_origin_color": "#111827",
        "uncertainty_label_required": True,
        "official_qmt_trajectory_claim_allowed": False,
    }
    return settings


def build_qualified_source_hashes(root: Path = WORKSPACE) -> dict[str, str]:
    root = Path(root).resolve()
    if root != WORKSPACE:
        raise RuntimeError("seal-021 source closure requires canonical Fusion_Part")
    output: dict[str, str] = {}
    for relative in MANDATORY_QUALIFIED_SOURCE_PATHS:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"mandatory seal-021 source is missing: {relative}")
        output[relative] = _sha(path)
    return output


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("run seal-021 successor only from canonical Fusion_Part")
    settings = build_effective_settings(WORKSPACE)
    source_hashes = build_qualified_source_hashes(WORKSPACE)
    amendment = {
        "schema": "biospur-c2-active-parameter-registry-prefit-amendment-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_AMENDMENT_RELATIVE),
            "sha256": PARENT_AMENDMENT_SHA256,
        },
        "source_closure_parent_seal_retained": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
            "status": "RETAINED_PARENT_OF_SINGLE_PERMITTED_SOURCE_CLOSURE_CORRECTION",
        },
        "user_online_branch_posterior_owner_amendment": {
            "path": str(USER_OWNER_AMENDMENT_RELATIVE),
            "sha256": USER_OWNER_AMENDMENT_SHA256,
        },
        "effective_settings": settings,
        "settings_semantic_sha256": _semantic_sha(settings),
        "qualified_source_hashes": source_hashes,
        "online_owner_replacement": True,
        "full_coherent_nuisance_refits_online": False,
        "finite_candidate_branches_retained": True,
        "incomplete_nuisance_widens_covariance": True,
        "physical_hard_gates_changed": False,
        "solver_or_threshold_relaxation": False,
        "p0_p1_or_upstream_qmt_rerun": False,
        "successor_seal_count_for_this_continuation": 1,
        "source_closure_correction_count_for_this_continuation": 1,
        "source_closure_correction_scope": [
            "PREALIGN_ALL_NINE_RUNTIME_OWNED_PAIRS_IN_LOCAL_FACTOR_STAGE",
            "REUSE_IDENTICAL_PAIR_BINDINGS_AFTER_FRAME_UPDATE",
            "AUTHORITY_ENTRYPOINT_PATH_BINDINGS_ONLY"
        ],
        "full_synthetic_qualification_complete": False,
        "real_diagnostic_requires_separate_activation": True,
        "real_fit_started": False,
        "heldout_opened": False,
        "post_fit_change_allowed": False,
        "operative_prefit_registry": True,
    }
    amendment_path = WORKSPACE / AMENDMENT_021_RELATIVE
    _write_new_immutable(amendment_path, amendment)
    seal = {
        "schema": "biospur-c2-p2-prefit-registry-seal-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only_parent": {
            "path": str(PARENT_SEAL_RELATIVE),
            "sha256": PARENT_SEAL_SHA256,
        },
        "amendment": {
            "path": str(AMENDMENT_021_RELATIVE),
            "sha256": _sha(amendment_path),
        },
        "amendment_append_only_parent": amendment["append_only_parent"],
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "qualified_source_hashes": source_hashes,
        "synthetic_qualification_status": (
            "USER_AUTHORIZED_REAL_DIAGNOSTIC_OWNER_REPLACEMENT;FOCUSED_TESTS_ONLY;"
            "FULL_QUALIFICATION_PENDING"
        ),
        "runtime_delta": (
            "ONLINE_WEIGHTED_AXIS_CENTER_CANDIDATE_BRANCHES_PLUS_IMMUTABLE_"
            "VQF_AXIS_CENTER_CHECKPOINT_ARRAYS_AND_PARTIAL_REAL_ARRAY_RENDERER"
        ),
        "real_fit_authorized_after_registry_alone": False,
        "real_diagnostic_requires_separate_activation": True,
        "physical_hard_gates_changed": False,
        "source_closure_correction_count_for_this_continuation": 1,
        "heldout_opened": False,
    }
    seal_path = WORKSPACE / SEAL_021_RELATIVE
    _write_new_immutable(seal_path, seal)
    print(json.dumps({
        "amendment_021_source_correction_001": str(AMENDMENT_021_RELATIVE),
        "amendment_021_source_correction_001_sha256": _sha(amendment_path),
        "settings_semantic_sha256": amendment["settings_semantic_sha256"],
        "seal_021_source_correction_001": str(SEAL_021_RELATIVE),
        "seal_021_source_correction_001_sha256": _sha(seal_path),
        "qualified_source_count": len(source_hashes),
        "successor_seal_count_for_this_continuation": 1,
        "heldout_opened": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
