#!/usr/bin/env python3
"""Audit the frozen single-joint distal-axis ownership defect without rerunning fit."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z/CONTINUATION_SPRINT"
ARRAYS = RUN / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.npz"
MANIFEST = RUN / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.json"
OUTPUT_DIR = RUN / "C2_FRESH_DISTAL_AXIS_OWNERSHIP_AUDIT_001"
OUTPUT = OUTPUT_DIR / "AUDIT.json"
BRANCH = "HINGE_SIGN_elbow_left:neg_elbow_right:neg_knee_left:pos_knee_right:neg"
PREFIX = f"physical_trajectory/18/{BRANCH}"
DISTAL = {
    "forearm_left": "elbow_left",
    "forearm_right": "elbow_right",
    "shank_left": "knee_left",
    "shank_right": "knee_right",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_array(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(np.asarray(array.shape, dtype=np.int64).tobytes())
    digest.update(array.tobytes())
    return digest.hexdigest()


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    rows: dict[str, object] = {}
    with np.load(ARRAYS, allow_pickle=False) as arrays:
        for segment, edge in DISTAL.items():
            lever_key = f"{PREFIX}/connection/{edge}/child"
            frame_key = f"{PREFIX}/segment_from_sensor/{segment}"
            lever = np.asarray(arrays[lever_key], dtype=float)
            segment_from_sensor = np.asarray(arrays[frame_key], dtype=float)
            sensor_from_segment = segment_from_sensor.T
            constructed_plus_z_sensor = sensor_from_segment[:, 2]
            normalized_lever = lever / np.linalg.norm(lever)
            lever_in_segment = segment_from_sensor @ lever
            dot = float(normalized_lever @ constructed_plus_z_sensor)
            equality_error = float(np.max(np.abs(
                normalized_lever - constructed_plus_z_sensor
            )))
            transverse = float(np.linalg.norm(lever_in_segment[:2]))
            if not np.isclose(dot, 1.0, atol=1e-12, rtol=0.0):
                raise RuntimeError(f"{segment}: frozen +Z is not the normalized sole lever")
            if equality_error > 1e-12 or transverse > 1e-12:
                raise RuntimeError(f"{segment}: frozen lever/frame identity check failed")
            rows[segment] = {
                "sole_proximal_joint_edge": edge,
                "sole_sensor_to_joint_lever_m": lever.tolist(),
                "sole_sensor_to_joint_lever_norm_m": float(np.linalg.norm(lever)),
                "constructed_segment_plus_z_in_sensor": constructed_plus_z_sensor.tolist(),
                "normalized_lever_dot_constructed_plus_z": dot,
                "maximum_absolute_normalized_lever_minus_plus_z": equality_error,
                "lever_expressed_in_segment_coordinates_m": lever_in_segment.tolist(),
                "transverse_lever_norm_in_segment_coordinates_m": transverse,
                "lever_array_sha256": sha256_array(lever),
                "segment_from_sensor_array_sha256": sha256_array(segment_from_sensor),
            }

    audit = {
        "schema": "biospur-c2-fresh-distal-axis-ownership-audit-v1",
        "created_local": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "role": "READ_ONLY_FROZEN_STATE_NUMERIC_OWNERSHIP_AUDIT",
        "authority": {
            "direct_user_planning_thread": "01a03f71-e481-7e21-84f0-3c6cbeb58291",
            "relay_and_independent_monitor_thread": "01a04d0f-58f1-7240-b72f-3bf5b44a2156",
        },
        "frozen_state": {
            "manifest_path": str(MANIFEST.relative_to(WORKSPACE)),
            "manifest_sha256": sha256_file(MANIFEST),
            "arrays_path": str(ARRAYS.relative_to(WORKSPACE)),
            "arrays_sha256": sha256_file(ARRAYS),
            "prefix": 18,
            "branch_id": BRANCH,
        },
        "verified_distal_segments": rows,
        "exact_finding": (
            "THE_FROZEN_FOREARM_AND_SHANK_SEGMENT_PLUS_Z_COLUMNS_ARE_EXACTLY_THE_"
            "NORMALIZED_SOLE_SENSOR_TO_ELBOW_OR_KNEE_LEVERS;ALL_RADIAL_SURFACE_"
            "OFFSET_COMPONENTS_WERE_HARD_PROMOTED_TO_LONGITUDINAL_DIRECTION"
        ),
        "scientific_scope": {
            "single_joint_lever_is_bone_longitudinal_truth": False,
            "distal_axis_visual_qualification_valid": False,
            "wrist_or_ankle_tuned_mean_pose_authorized": False,
            "existing_frozen_pixels_requiring_these_distal_axes": "INVALID_DISTAL_AXIS_VISUAL_QUALIFICATION",
            "existing_frozen_arrays_modified": False,
            "fit_qmt_progressive_or_heldout_rerun": False,
            "heldout_accessed": False,
        },
        "current_owner_correction_boundary": {
            "sole_joint_lever_excluded_from_longitudinal_z": True,
            "single_wear_quadrature_is_only_an_unresolved_placeholder": True,
            "placeholder_is_recovered_direction": False,
            "complete_motion_likelihood_evaluated": False,
            "explicit_broad_longitudinal_candidate_support_materialized": False,
            "distal_axis_remains_unresolved": True,
            "tuned_wrist_ankle_render_must_remain_disabled": True,
        },
        "current_source_hashes": {
            path: sha256_file(WORKSPACE / path)
            for path in (
                "src/biospur_fusion/v0/c2_progressive/segment_frames.py",
                "src/biospur_fusion/v0/c2_progressive/pipeline_runtime.py",
                "tests/v0/test_c2_p2_prefit_owners.py",
                "tools/audit_c2_fresh_distal_axis_ownership.py",
            )
        },
        "scientific_acceptance_pass": False,
        "tuned_human_pose_pass": False,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=False)
    with OUTPUT.open("x", encoding="utf-8") as handle:
        json.dump(audit, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    OUTPUT.chmod(0o444)
    print(json.dumps({
        "output": str(OUTPUT),
        "sha256": sha256_file(OUTPUT),
        "segments": len(rows),
        "scientific_acceptance_pass": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
