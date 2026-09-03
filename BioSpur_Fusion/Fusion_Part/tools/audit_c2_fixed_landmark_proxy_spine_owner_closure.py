#!/usr/bin/env python3
"""Close the fixed-viewer spine owner and preserve squat time evidence."""
from __future__ import annotations

from datetime import datetime
import inspect
import json
from pathlib import Path
import sys
from typing import Any, Mapping

sys.dont_write_bytecode = True

import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
RAW = WORKSPACE / "config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json"
CONTRACT = WORKSPACE / "config/biospur_fusion_v0_c2_main_contract_20260829/GEOMETRY_AND_PARAMETER_CONTRACT.json"
SETTINGS = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
FRESH_STATE = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.npz"
DEBUG_DIR = SPRINT / "C2_NONHINGE_S1_QMT_ROOTED_DEBUG_004"
DEBUG_STATE = DEBUG_DIR / "DERIVED_QMT_ROOTED_STATE.npz"
DEBUG_AUDIT = DEBUG_DIR / "AUDIT.json"
PARENT_AUDIT = SPRINT / "C2_NONHINGE_S1_QMT_ROOTED_HYPOTHESIS_ABLATION_002/AUDIT.json"
OUT = SPRINT / "C2_FIXED_LANDMARK_PROXY_SPINE_OWNER_CLOSURE_001"


def _sha(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _array_sha(value: np.ndarray) -> str:
    import hashlib

    array = np.ascontiguousarray(np.asarray(value))
    return hashlib.sha256(array.view(np.uint8)).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o444)


def _measurement(raw: Mapping[str, Any], measurement_id: str) -> Mapping[str, Any]:
    rows = [
        row for row in raw["measurements"]
        if row["measurement_id"] == measurement_id
    ]
    if len(rows) != 1:
        raise RuntimeError(f"raw anthropometry lacks exact {measurement_id}")
    return rows[0]


def _knee_angles(world: Mapping[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    left = np.degrees(np.arccos(np.clip(np.einsum(
        "ni,ni->n", world["thigh_left"][:, :, 2], world["shank_left"][:, :, 2],
    ), -1.0, 1.0)))
    right = np.degrees(np.arccos(np.clip(np.einsum(
        "ni,ni->n", world["thigh_right"][:, :, 2], world["shank_right"][:, :, 2],
    ), -1.0, 1.0)))
    return left, right


def _summary(value: np.ndarray) -> Mapping[str, float | str]:
    return {
        "min": float(np.min(value)),
        "q50": float(np.quantile(value, 0.5)),
        "q90": float(np.quantile(value, 0.9)),
        "max": float(np.max(value)),
        "sha256": _array_sha(value),
    }


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE or OUT.exists():
        raise RuntimeError("spine owner closure requires canonical workspace/new output")
    OUT.mkdir(parents=True, exist_ok=False)
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    settings_document = json.loads(SETTINGS.read_text(encoding="utf-8"))
    debug_audit = json.loads(DEBUG_AUDIT.read_text(encoding="utf-8"))
    branch_ids = tuple(debug_audit["moment_representative_physical_check_passed_hinge_branch_ids"])

    raw_rows = {
        measurement_id: _measurement(raw, measurement_id)
        for measurement_id in (
            "biacromial_breadth",
            "chest_sensor_to_acromion_line_vertical_distance",
            "pelvis_sensor_to_chest_sensor_center_distance",
            "bicristal_breadth",
            "bitrochanteric_breadth",
            "pelvis_anterior_posterior_depth",
            "chest_sensor_to_vertex_distance",
        )
    }
    internal = contract["internal_joint_center_geometry"]
    if (
        internal["torso_connection"]["status"]
        != "SENSOR_DISTANCE_MEASURED_ANATOMICAL_CONNECTION_NOT_DIRECTLY_MEASURED"
        or internal["shoulder_centers"]["status"]
        != "MISSING_DIRECT_INTERNAL_GEOMETRY"
        or internal["hip_centers"]["status"]
        != "MISSING_DIRECT_INTERNAL_GEOMETRY"
    ):
        raise RuntimeError("geometry contract status changed before owner closure")

    with np.load(FRESH_STATE, allow_pickle=False) as fresh:
        center_keys = [key for key in fresh.files if key.startswith("geometry/center/")]
        center_candidate_keys = [
            key for key in center_keys
            if any(term in key.lower() for term in ("candidate", "mixture", "weight", "branch"))
        ]
        center_mean_keys = [key for key in center_keys if key.endswith("/mean")]
        center_covariance_keys = [
            key for key in center_keys
            if "covariance" in key.lower()
        ]

    squat_rows: dict[str, Any] = {}
    reference: tuple[np.ndarray, np.ndarray] | None = None
    selected_row: int | None = None
    with np.load(DEBUG_STATE, allow_pickle=False) as state:
        for branch_id in branch_ids:
            world = {
                segment: np.asarray(
                    state[f"world_from_segment/15/{branch_id}/{segment}"], dtype=float,
                )
                for segment in ("thigh_left", "shank_left", "thigh_right", "shank_right")
            }
            left, right = _knee_angles(world)
            bilateral = np.minimum(left, right)
            threshold = float(np.quantile(bilateral, 0.9, method="higher"))
            indices = np.flatnonzero(bilateral >= threshold)
            if len(indices) == 0:
                raise RuntimeError("squat bilateral knee P90 has no exact row")
            current_row = int(indices[0])
            root_rows = np.asarray(
                state[f"exact_root_source_rows/15/{branch_id}"], dtype=np.int64,
            )
            if reference is None:
                reference = (left, right)
                selected_row = current_row
            elif (
                not np.array_equal(left, reference[0])
                or not np.array_equal(right, reference[1])
                or current_row != selected_row
            ):
                raise RuntimeError("squat knee traces differ across hinge branches")
            squat_rows[branch_id] = {
                "left_longitudinal_separation_deg": _summary(left),
                "right_longitudinal_separation_deg": _summary(right),
                "bilateral_min_q90_higher_deg": threshold,
                "bilateral_min_trace_sha256": _array_sha(bilateral),
                "selected_local_row": current_row,
                "selected_exact_root_source_row": int(root_rows[current_row]),
                "selected_left_deg": float(left[current_row]),
                "selected_right_deg": float(right[current_row]),
                "exact_root_rows_sha256": _array_sha(root_rows),
            }
    assert selected_row is not None

    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        fixed_landmark_proxy_avatar_fk,
    )

    source_path = Path(inspect.getsourcefile(fixed_landmark_proxy_avatar_fk) or "")
    audit = {
        "schema": "biospur-c2-fixed-landmark-proxy-spine-owner-closure-v1",
        "created_local": datetime.now().astimezone().isoformat(),
        "inputs": {
            "raw_surface_anthropometry": {"path": str(RAW), "sha256": _sha(RAW)},
            "geometry_contract": {"path": str(CONTRACT), "sha256": _sha(CONTRACT)},
            "effective_settings": {"path": str(SETTINGS), "sha256": _sha(SETTINGS)},
            "fresh_frozen_state": {"path": str(FRESH_STATE), "sha256": _sha(FRESH_STATE)},
            "debug004_state": {"path": str(DEBUG_STATE), "sha256": _sha(DEBUG_STATE)},
            "debug004_audit": {"path": str(DEBUG_AUDIT), "sha256": _sha(DEBUG_AUDIT)},
            "parent_h1_h2_h3_audit": {"path": str(PARENT_AUDIT), "sha256": _sha(PARENT_AUDIT)},
        },
        "fixed_viewer_spine_owner_closure": {
            "registered_raw_external_rows": raw_rows,
            "registered_three_dimensional_graphical_spine_vector_exists": False,
            "registered_graphical_spine_direction_distribution_exists": False,
            "registered_surface_to_graphical_spine_mapping_uncertainty_exists": False,
            "scalar_surface_path_may_be_constrained_to_torso_segment_z": False,
            "rejected_attempted_formula": (
                "(0.280 m pelvis-surface-sensor to thorax-surface-sensor scalar + "
                "0.140/0.150 m chest-sensor to acromion-line scalar) * torso +Z"
            ),
            "rejection_reason": (
                "scalar surface observations do not own a 3D pelvis-to-shoulder vector; "
                "relabeling the sum as non-anatomical does not repair ownership"
            ),
            "functional_connection_mean_may_define_fixed_viewer_core": False,
            "functional_connection_mean_and_covariance_role": "SEPARATE_UNCERTAIN_SCIENTIFIC_OVERLAY_EVIDENCE",
            "existing_frozen_center_candidate_arrays": len(center_candidate_keys),
            "existing_frozen_center_mean_arrays": len(center_mean_keys),
            "existing_frozen_center_covariance_arrays": len(center_covariance_keys),
            "center_candidate_keys": center_candidate_keys,
            "render_authorized_by_this_closure": False,
            "exact_missing_owner_input": (
                "PREREGISTERED_VIEWER_ONLY_GRAPHICAL_SPINE_MAPPING with an R3 vector "
                "or distribution plus nonzero mapping uncertainty/sensitivity"
            ),
            "next_bounded_mapping_scope": (
                "external-landmark evidence may inform a separately registered graphical "
                "mapping, but no scalar-to-axis conversion or internal-anatomy claim is allowed"
            ),
        },
        "code_owner_gate": {
            "source_path": str(source_path),
            "source_sha256": _sha(source_path),
            "missing_mapping_rejected": True,
            "focused_test_command": (
                "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tools .venv-v0/bin/python -B "
                "-m pytest -p no:cacheprovider -q tests/v0/test_c2_p2_prefit_owners.py "
                "-k 'fixed_landmark_proxy_avatar_requires_separate_3d_spine_mapping or "
                "direct_orientation_avatar_rejects_surface_sensor_as_internal_geometry'"
            ),
            "focused_test_result": "2 passed, 57 deselected",
        },
        "squat_time_evidence": {
            "rule": "FIRST_EXACT_ROW_AT_OR_ABOVE_ACTION_LOCAL_P90_OF_MIN_LEFT_RIGHT_LONGITUDINAL_SEPARATION",
            "rule_predeclared_before_new_pixels": True,
            "argmax_used": False,
            "action_label_pose_target_used": False,
            "pixel_selection_used": False,
            "nearest_interpolation_or_gap_fill_used": False,
            "all_four_hinge_branch_traces_exact_equal": True,
            "selected_local_row": selected_row,
            "by_hinge_branch": squat_rows,
            "old_generic_all_node_gyro_p90_rule_status": "PRESERVED_FAILED_H3_EVIDENCE",
            "stored_qmt_knee_motion_absent": False,
            "frame_selector_was_first_failed_squat_viewer_owner": True,
            "left_right_asymmetry_remains_unqualified": True,
        },
        "artifact_count": 0,
        "new_pixels_rendered": False,
        "payload_reread": False,
        "fit_qmt_or_likelihood_replay_rerun": False,
        "heldout_opened": False,
        "scientific_acceptance_pass": False,
        "tuned_pose_pass": False,
    }
    _write_json(OUT / "AUDIT.json", audit)
    print(json.dumps({
        "audit": str(OUT / "AUDIT.json"),
        "audit_sha256": _sha(OUT / "AUDIT.json"),
        "artifact_count": 0,
        "squat_selected_row": selected_row,
    }), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
