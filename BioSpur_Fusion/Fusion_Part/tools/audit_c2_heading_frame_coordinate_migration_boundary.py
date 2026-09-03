#!/usr/bin/env python3
"""Bind the saved C2 QMT frame-coordinate history boundary without replay."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import sys
from typing import Any, Mapping

sys.dont_write_bytecode = True

import numpy as np

from biospur_fusion.v0.c2_progressive.heading import PersistentHeadingOwner


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
SPRINT = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z/CONTINUATION_SPRINT"
PARENT = SPRINT / "C2_SAVED_ARRAY_SEGMENT_FRAME_QMT_ABLATION_001/AUDIT.json"
DISTAL = SPRINT / "C2_DISTAL_LONGITUDINAL_COMPLETE_S1_REPLAY_002/AUDIT.json"
FAILED_DISTAL = SPRINT / "C2_DISTAL_LONGITUDINAL_COMPLETE_S1_REPLAY_001/FAILURE.json"
HEADING_SOURCE = WORKSPACE / "src/biospur_fusion/v0/c2_progressive/heading.py"
FOCUSED_TEST = WORKSPACE / "tests/v0/test_c2_p2_prefit_owners.py"
OUT = SPRINT / "C2_HEADING_FRAME_COORDINATE_MIGRATION_BOUNDARY_AUDIT_001"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(_jsonable(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _summary(parent: Mapping[str, Any], edge: str) -> Mapping[str, Any]:
    rows = []
    for branch_id, branch in parent["by_branch"].items():
        evidence = branch["qmt_edge_application"][edge]
        rows.append({
            "branch_id": branch_id,
            "debug004_fixed_prefix15_frame_delta_q50_rad": float(
                evidence["edge_delta_filt_rad"]["q50"]
            ),
            "earlier_saved_progressive_frame_official_delta_q50_rad": float(
                evidence["official_saved_span_evidence"][
                    "persistent_delta_filt_rad"
                ]["q50"]
            ),
            "tree_reconstruction_max_abs_error_deg": float(
                evidence["maximum_absolute_reconstruction_error_deg"]
            ),
            "tree_application_exact": bool(
                evidence["parent_plus_child_tree_application_exactly_reproduced"]
            ),
        })
    return {
        "edge": edge,
        "rows": rows,
        "debug004_delta_identical_across_retained_branches": len({
            row["debug004_fixed_prefix15_frame_delta_q50_rad"] for row in rows
        }) == 1,
        "earlier_official_delta_identical_across_retained_branches": len({
            row["earlier_saved_progressive_frame_official_delta_q50_rad"]
            for row in rows
        }) == 1,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=False)
    parent = json.loads(PARENT.read_text(encoding="utf-8"))
    distal = json.loads(DISTAL.read_text(encoding="utf-8"))
    update_source = inspect.getsource(PersistentHeadingOwner.update_frame_branches)
    required_fragments = (
        "changed_segments_by_branch",
        "span_count",
        "observation_count",
        "_external_nonhinge_circular_state",
        "coherent corrected-frame replay",
        "state-coordinate migration",
    )
    if any(fragment not in update_source for fragment in required_fragments):
        raise RuntimeError("heading frame-coordinate update guard is incomplete")
    knee_left = _summary(parent, "knee_left")
    knee_right = _summary(parent, "knee_right")
    all_rows = knee_left["rows"] + knee_right["rows"]
    if not all(row["tree_application_exact"] for row in all_rows):
        raise RuntimeError("saved tree application is not exact")
    if max(row["tree_reconstruction_max_abs_error_deg"] for row in all_rows) > 1e-10:
        raise RuntimeError("saved parent-plus-child reconstruction exceeds tolerance")
    if distal["conclusions"]["complete_motion_identifies_distal_longitudinal_direction"]:
        raise RuntimeError("distal complete-S1 audit unexpectedly claims identification")
    audit = {
        "schema": "biospur-c2-heading-frame-coordinate-migration-boundary-audit-v1",
        "created_local": _now(),
        "inputs": {
            "saved_frame_qmt_ablation": {"path": str(PARENT), "sha256": _sha(PARENT)},
            "accepted_complete_s1_distal_observability": {
                "path": str(DISTAL), "sha256": _sha(DISTAL),
            },
            "preserved_distal_ordinary_failure": {
                "path": str(FAILED_DISTAL), "sha256": _sha(FAILED_DISTAL),
            },
            "heading_owner_source": {
                "path": str(HEADING_SOURCE), "sha256": _sha(HEADING_SOURCE),
                "update_frame_branches_source_sha256": hashlib.sha256(
                    update_source.encode()
                ).hexdigest(),
            },
            "focused_test_source": {
                "path": str(FOCUSED_TEST), "sha256": _sha(FOCUSED_TEST),
                "test": (
                    "test_heading_owner_rejects_silent_frame_coordinate_change_"
                    "after_edge_state"
                ),
            },
        },
        "saved_numeric_evidence": {
            "knee_left": knee_left,
            "knee_right": knee_right,
            "maximum_parent_plus_child_reconstruction_error_deg": max(
                row["tree_reconstruction_max_abs_error_deg"] for row in all_rows
            ),
        },
        "owner_boundary": {
            "tree_composition_bug_supported": False,
            "fixed_prefix15_debug_replay_and_earlier_progressive_frame_history_are_same_coordinate_history": False,
            "scalar_heading_state_has_a_general_closed_form_migration_for_arbitrary_so3_frame_change": False,
            "byte_identical_frame_coordinate_refresh_after_state_is_allowed": True,
            "affected_unprocessed_edge_frame_coordinate_install_is_allowed": True,
            "affected_processed_edge_frame_coordinate_change_is_rejected": True,
            "legal_next_qmt_path": (
                "ONE_COHERENT_CORRECTED_FRAME_REPLAY_OR_EXPLICIT_"
                "OWNER_AUTHENTICATED_STATE_COORDINATE_MIGRATION"
            ),
            "current_distal_complete_s1_posterior_may_be_collapsed_for_that_replay": False,
        },
        "execution": {
            "source_audit_only": True,
            "raw_payload_reread": False,
            "fit_progressive_qmt_or_tree_rerun": False,
            "heldout_opened": False,
            "pixels_generated": False,
        },
        "scientific_acceptance_pass": False,
        "tuned_pose_pass": False,
    }
    path = OUT / "AUDIT.json"
    _write_new_json(path, audit)
    print(json.dumps({
        "audit": str(path),
        "audit_sha256": _sha(path),
        "execution_complete": True,
        "scientific_pass": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
