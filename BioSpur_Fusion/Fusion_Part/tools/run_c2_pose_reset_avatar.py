#!/usr/bin/env python3
"""Run the short C2 standard-pose + QMT hinge avatar baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from biospur_fusion.c2_coupled_progressive.contracts import ROOT, load_effective_config
from biospur_fusion.c2_coupled_progressive.estimator import SEGMENTS
from biospur_fusion.c2_coupled_progressive.frontend import VerifiedFrontendArchive
from biospur_fusion.c2_coupled_progressive.pose_reset_avatar import (
    apply_qmt_hinge_soft_updates,
    apply_protocol_hip_heading_soft_updates,
    apply_protocol_shoulder_plane_updates,
    apply_protocol_torso_heading_updates,
    build_pose_reset_trajectory,
    estimate_hinge_axes_olsson,
    estimate_pose_reset_calibration,
    freeze_pose_reset_replay_calibration,
    select_protocol_frame,
)
from biospur_fusion.c2_coupled_progressive.output_coordinates import (
    freeze_capture_wide_lateral_reflection,
)
from biospur_fusion.c2_coupled_progressive.renderer import (
    display_models,
    joints_for_frame,
    physical_qa,
    render_triptych,
)


SELECTIONS = {
    "standing": 0,
    "t_pose": 1,
    "pelvis_hula": 2,
    "shoulder_left": 3,
    "shoulder_right": 4,
    "elbow_left": 5,
    "elbow_right": 6,
    "hip_left": 7,
    "hip_right": 8,
    "knee_left": 9,
    "knee_right": 10,
    "heel_raise_left": 11,
    "heel_raise_right": 12,
    "trunk_flex": 13,
    "trunk_axial": 14,
    "squat": 15,
    "final_standing": 16,
    "heel_left": 17,
    "heel_right": 18,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _trajectory_npz(path: Path, trajectory: dict[str, Any]) -> dict[str, Any]:
    arrays: dict[str, np.ndarray] = {}
    for episode, segments in trajectory["trajectory"].items():
        for segment in SEGMENTS:
            row = segments[segment]
            base = f"trajectory/{episode}/{segment}"
            arrays[f"{base}/time_root_s"] = np.asarray(row["time_root_s"])
            arrays[f"{base}/quat_world_segment_wxyz"] = np.asarray(
                row["quat_world_segment_wxyz"]
            )
            arrays[f"{base}/mask"] = np.asarray(row["mask"], dtype=bool)
    output_coordinates = trajectory["output_coordinate_convention"]
    arrays["output_coordinates/matrix_world_output_from_internal"] = np.asarray(
        output_coordinates["matrix_world_output_from_internal"], dtype=float
    )
    arrays["output_coordinates/plane_normal_world_internal"] = np.asarray(
        output_coordinates["plane_normal_world_internal"], dtype=float
    )
    np.savez_compressed(path, **arrays)
    return {"path": str(path.relative_to(ROOT)), "sha256": _sha256(path), "array_count": len(arrays)}


def _frozen_replay_npz(path: Path, frozen: dict[str, Any]) -> dict[str, Any]:
    segment_order = list(frozen["segment_order"])
    arrays = {
        "initial_world_sensor": np.stack([
            frozen["initial_world_sensor"][segment]
            for segment in segment_order
        ]),
        "functional_world_yaw_rad": np.array([
            frozen["functional_world_yaw_rad"][segment]
            for segment in segment_order
        ]),
        "common_pelvis_yaw_closure_rad": np.array(
            frozen["common_pelvis_yaw_closure_rad"], dtype=float
        ),
        "reference_time_s": np.array(frozen["reference_time_s"], dtype=float),
        "final_reference_time_s": np.array(
            frozen["final_reference_time_s"], dtype=float
        ),
    }
    np.savez_compressed(path, **arrays)
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": _sha256(path),
        "segment_order": segment_order,
        "array_count": len(arrays),
        "holdout_payload_used_during_fit": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT not in output.parents:
        raise SystemExit("output must remain in the canonical Fusion_Part tree")
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()

    frontend = VerifiedFrontendArchive()
    seal = frontend.verify_seal_and_semantics()
    episodes = list(frontend.episodes())
    calibration = estimate_pose_reset_calibration(episodes)
    trajectory = build_pose_reset_trajectory(episodes, calibration)
    torso_heading_audit = apply_protocol_torso_heading_updates(
        trajectory, episodes
    )
    hinge_axes, hinge_axis_audit = estimate_hinge_axes_olsson(
        episodes, calibration
    )
    shoulder_heading_audit = apply_protocol_shoulder_plane_updates(
        trajectory, episodes, hinge_axes
    )
    hip_heading_audit = apply_protocol_hip_heading_soft_updates(
        trajectory, episodes
    )
    qmt_audit = apply_qmt_hinge_soft_updates(
        trajectory, episodes, hinge_axes
    )
    output_coordinate_convention = freeze_capture_wide_lateral_reflection(
        trajectory
    )
    frozen_replay = freeze_pose_reset_replay_calibration(
        trajectory, episodes, calibration
    )
    config = load_effective_config()
    model = display_models(config)[1]

    render_rows = []
    for label, episode_index in SELECTIONS.items():
        key = f"{episode_index:02d}"
        frame, selection_audit = select_protocol_frame(
            trajectory, episode_index, label
        )
        joints = joints_for_frame(trajectory, key, frame, model, config)
        physical_joints = joints_for_frame(
            trajectory,
            key,
            frame,
            model,
            config,
            apply_output_coordinates=False,
        )
        qa = physical_qa(
            physical_joints,
            standing=(label in {"standing", "final_standing"}),
        )
        image = output / f"{label}_{key}_front_side_top.png"
        render_triptych(joints, image, f"{label} episode {key} frame={frame}")
        render_rows.append({
            "label": label,
            "episode_index": episode_index,
            "frame": frame,
            "time_s": float(
                trajectory["trajectory"][key]["pelvis"]["time_root_s"][frame]
            ),
            "selection": selection_audit,
            "physical_qa": qa,
            "image": str(image.relative_to(ROOT)),
            "image_sha256": _sha256(image),
        })

    npz = _trajectory_npz(output / "POSE_RESET_QMT_TRAJECTORY.npz", trajectory)
    frozen_replay_npz = _frozen_replay_npz(
        output / "FROZEN_C2_AVATAR_REPLAY_CALIBRATION.npz",
        frozen_replay,
    )
    all_pixels_pass = all(row["physical_qa"]["pass"] for row in render_rows)
    report = {
        "schema": "biospur-c2-pose-reset-qmt-avatar-diagnostic-v1",
        "status": (
            "RUNNABLE_DIAGNOSTIC_BASELINE"
            if all_pixels_pass
            else "DIAGNOSTIC_BASELINE_WITH_PIXEL_FAILURES"
        ),
        "scientific_progressive_pass": False,
        "reason": (
            "offline final-standing drift closure and action-role QA selection are "
            "engineering baseline mechanisms, not a causal progressive qualification"
        ),
        "frontend": seal,
        "frontend_access": frontend.access_audit(),
        "episode_count": len(episodes),
        "orientation_state_per_sensor": 1,
        "episode_orientation_reset_count": 0,
        "magnetometer_used": False,
        "viewer_ik_rebase_retarget_or_repair": False,
        "sensor_origins_used_as_joints": False,
        "calibration": calibration.audit(),
        "protocol_torso_heading": torso_heading_audit,
        "protocol_shoulder_heading": shoulder_heading_audit,
        "qmt_hinges": qmt_audit,
        "qmt_olsson_hinge_axes": hinge_axis_audit,
        "protocol_hip_heading": hip_heading_audit,
        "output_coordinate_convention": {
            key: (
                value.tolist() if isinstance(value, np.ndarray) else value
            )
            for key, value in output_coordinate_convention.items()
        },
        "trajectory": npz,
        "frozen_replay_calibration": {
            "artifact": frozen_replay_npz,
            "schema": frozen_replay["schema"],
            "training_scope": frozen_replay["training_scope"],
            "holdout_payload_used_during_fit": False,
            "holdout_refit_allowed": False,
            "orientation_equation": frozen_replay["orientation_equation"],
            "decomposition_audit": frozen_replay["decomposition_audit"],
        },
        "renders": render_rows,
        "actual_pixel_review_required": True,
        "wall_s": float(time.perf_counter() - started),
    }
    _json(output / "POSE_RESET_QMT_DIAGNOSTIC.json", report)
    print(json.dumps({
        "status": report["status"],
        "wall_s": report["wall_s"],
        "output": str(output),
        "failed_pixel_labels": [
            row["label"] for row in render_rows if not row["physical_qa"]["pass"]
        ],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
