#!/usr/bin/env python3
"""Recompute the C2 00-19 calibration on the native 200 Hz IMU grid.

This produces a parallel diagnostic artifact.  It never overwrites or promotes
the formally frozen 20 Hz calibration.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_coupled_progressive.contracts import ROOT, load_effective_config
from biospur_fusion.c2_coupled_progressive.estimator import SEGMENTS
from biospur_fusion.c2_coupled_progressive.frontend import VerifiedFrontendArchive
from biospur_fusion.c2_coupled_progressive.output_coordinates import (
    freeze_capture_wide_lateral_reflection,
)
from biospur_fusion.c2_coupled_progressive.renderer import (
    display_models,
    joints_for_frame,
    physical_qa,
    render_triptych,
)
from biospur_fusion.c2_native200_calibration import load_native200_pose_reset_module
from tools import run_c2_pose_reset_avatar as legacy


LEGACY_RUN = ROOT / "logs/c2_pose_reset_qmt_avatar_v16_20260831_215300"
RATE_HZ = 200.0
GRID_PERIOD_NS = 5_000_000


def _load_trajectory(path: Path) -> dict[str, dict[str, dict[str, np.ndarray]]]:
    result: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    with np.load(path, allow_pickle=False) as archive:
        for key in archive.files:
            parts = key.split("/")
            if len(parts) != 4 or parts[0] != "trajectory":
                continue
            _, episode, segment, field = parts
            result.setdefault(episode, {}).setdefault(segment, {})[field] = np.array(
                archive[key], copy=True
            )
    return result


def _quat_error_rad(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    a = Rotation.from_quat(np.asarray(first)[:, [1, 2, 3, 0]])
    b = Rotation.from_quat(np.asarray(second)[:, [1, 2, 3, 0]])
    return (a.inv() * b).magnitude()


def _comparison(
    native_trajectory: dict[str, Any], old_path: Path
) -> dict[str, Any]:
    old = _load_trajectory(old_path)
    rows: dict[str, Any] = {}
    all_errors: list[np.ndarray] = []
    time_mismatches = 0
    for episode, segments in old.items():
        rows[episode] = {}
        for segment, old_row in segments.items():
            new_row = native_trajectory["trajectory"][episode][segment]
            old_time = np.asarray(old_row["time_root_s"], dtype=float)
            new_time = np.asarray(new_row["time_root_s"], dtype=float)
            index = np.searchsorted(new_time, old_time)
            index = np.clip(index, 0, len(new_time) - 1)
            exact = np.isclose(new_time[index], old_time, atol=1e-8, rtol=0.0)
            time_mismatches += int(np.sum(~exact))
            errors = _quat_error_rad(
                np.asarray(old_row["quat_world_segment_wxyz"], dtype=float)[exact],
                np.asarray(new_row["quat_world_segment_wxyz"], dtype=float)[index[exact]],
            )
            all_errors.append(errors)
            rows[episode][segment] = {
                "old_frames": int(len(old_time)),
                "native_frames": int(len(new_time)),
                "matched_old_timestamps": int(np.sum(exact)),
                "rms_orientation_difference_deg": float(
                    math.degrees(math.sqrt(float(np.mean(errors * errors))))
                ),
                "maximum_orientation_difference_deg": float(
                    math.degrees(float(np.max(errors)))
                ),
            }
    combined = np.concatenate(all_errors)
    return {
        "old_trajectory": str(old_path.relative_to(ROOT)),
        "old_trajectory_sha256": legacy._sha256(old_path),
        "comparison_grid": "exact legacy timestamps inside native 5 ms grid",
        "time_mismatch_count": time_mismatches,
        "rms_orientation_difference_deg": float(
            math.degrees(math.sqrt(float(np.mean(combined * combined))))
        ),
        "p95_orientation_difference_deg": float(
            math.degrees(float(np.quantile(combined, 0.95)))
        ),
        "maximum_orientation_difference_deg": float(
            math.degrees(float(np.max(combined)))
        ),
        "episodes": rows,
    }


def _same_time_physical_qa(
    native_trajectory: dict[str, Any], old_path: Path, model: Any, config: Any
) -> dict[str, Any]:
    old_segments = _load_trajectory(old_path)
    old_trajectory = {
        "trajectory": old_segments,
        "output_coordinate_convention": native_trajectory[
            "output_coordinate_convention"
        ],
    }
    episodes: dict[str, Any] = {}
    old_pass_total = 0
    native_pass_total = 0
    old_pass_native_fail_total = 0
    old_fail_native_pass_total = 0
    for episode, segments in old_segments.items():
        old_time = np.asarray(segments["pelvis"]["time_root_s"], dtype=float)
        native_time = np.asarray(
            native_trajectory["trajectory"][episode]["pelvis"]["time_root_s"],
            dtype=float,
        )
        native_index = np.searchsorted(native_time, old_time)
        native_index = np.clip(native_index, 0, len(native_time) - 1)
        old_pass = []
        native_pass = []
        for old_frame, new_frame in enumerate(native_index):
            old_pass.append(bool(physical_qa(joints_for_frame(
                old_trajectory,
                episode,
                old_frame,
                model,
                config,
                apply_output_coordinates=False,
            ))["pass"]))
            native_pass.append(bool(physical_qa(joints_for_frame(
                native_trajectory,
                episode,
                int(new_frame),
                model,
                config,
                apply_output_coordinates=False,
            ))["pass"]))
        old_mask = np.asarray(old_pass, dtype=bool)
        native_mask = np.asarray(native_pass, dtype=bool)
        regression = int(np.sum(old_mask & ~native_mask))
        improvement = int(np.sum(~old_mask & native_mask))
        old_pass_total += int(np.sum(old_mask))
        native_pass_total += int(np.sum(native_mask))
        old_pass_native_fail_total += regression
        old_fail_native_pass_total += improvement
        episodes[episode] = {
            "common_legacy_timestamps": int(len(old_time)),
            "old_pass": int(np.sum(old_mask)),
            "native_pass": int(np.sum(native_mask)),
            "old_pass_native_fail": regression,
            "old_fail_native_pass": improvement,
        }
    return {
        "comparison_grid": "same legacy timestamps",
        "old_pass_total": old_pass_total,
        "native_pass_total": native_pass_total,
        "old_pass_native_fail_total": old_pass_native_fail_total,
        "old_fail_native_pass_total": old_fail_native_pass_total,
        "episodes": episodes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT not in output.parents:
        raise SystemExit("output must remain in canonical Fusion_Part")
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()

    native, adapter_audit = load_native200_pose_reset_module()
    frontend = VerifiedFrontendArchive()
    seal = frontend.verify_seal_and_semantics()
    episodes = list(frontend.episodes())
    calibration = native.estimate_pose_reset_calibration(episodes)
    trajectory = native.build_pose_reset_trajectory(
        episodes, calibration, sample_step=1
    )
    torso_heading_audit = native.apply_protocol_torso_heading_updates(
        trajectory, episodes
    )
    hinge_axes, hinge_axis_audit = native.estimate_hinge_axes_olsson(
        episodes, calibration
    )
    shoulder_heading_audit = native.apply_protocol_shoulder_plane_updates(
        trajectory, episodes, hinge_axes
    )
    hip_heading_audit = native.apply_protocol_hip_heading_soft_updates(
        trajectory, episodes
    )
    qmt_audit = native.apply_qmt_hinge_soft_updates(
        trajectory, episodes, hinge_axes
    )
    output_coordinate_convention = freeze_capture_wide_lateral_reflection(trajectory)
    frozen_replay = native.freeze_pose_reset_replay_calibration(
        trajectory, episodes, calibration
    )

    config = load_effective_config()
    model = display_models(config)[1]
    legacy_report = json.loads(
        (LEGACY_RUN / "POSE_RESET_QMT_DIAGNOSTIC.json").read_text(encoding="utf-8")
    )
    legacy_selection = {
        row["label"]: row for row in legacy_report["renders"]
    }
    render_rows = []
    qa_summary: dict[str, Any] = {}
    for label, episode_index in legacy.SELECTIONS.items():
        key = f"{episode_index:02d}"
        native_candidate, native_selection_audit = native.select_protocol_frame(
            trajectory, episode_index, label
        )
        source_time = np.asarray(
            trajectory["trajectory"][key]["pelvis"]["time_root_s"], dtype=float
        )
        legacy_time_s = float(legacy_selection[label]["time_s"])
        frame = int(np.argmin(np.abs(source_time - legacy_time_s)))
        selection_audit = {
            "method": "MATCHED_LEGACY_PHYSICAL_TIME_FOR_AB",
            "legacy_time_s": legacy_time_s,
            "native_time_s": float(source_time[frame]),
            "absolute_time_error_s": float(abs(source_time[frame] - legacy_time_s)),
            "native_rate_candidate_frame": int(native_candidate),
            "native_rate_candidate_audit": native_selection_audit,
        }
        internal_joints = joints_for_frame(
            trajectory, key, frame, model, config, apply_output_coordinates=False
        )
        qa = physical_qa(
            internal_joints, standing=(label in {"standing", "final_standing"})
        )
        image = output / f"{label}_{key}_front_side_top.png"
        render_triptych(
            joints_for_frame(trajectory, key, frame, model, config),
            image,
            f"native200 {label} episode {key} frame={frame}",
        )
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
            "image_sha256": legacy._sha256(image),
        })
        qa_summary[key] = qa

    trajectory_artifact = legacy._trajectory_npz(
        output / "POSE_RESET_QMT_TRAJECTORY.npz", trajectory
    )
    frozen_artifact = legacy._frozen_replay_npz(
        output / "FROZEN_C2_AVATAR_REPLAY_CALIBRATION.npz", frozen_replay
    )
    comparison = _comparison(
        trajectory,
        LEGACY_RUN / "POSE_RESET_QMT_TRAJECTORY.npz",
    )
    same_time_qa = _same_time_physical_qa(
        trajectory,
        LEGACY_RUN / "POSE_RESET_QMT_TRAJECTORY.npz",
        model,
        config,
    )
    report = {
        "schema": "biospur-c2-native200-pose-reset-calibration-diagnostic-v1",
        "status": "NATIVE200_CALIBRATION_DIAGNOSTIC",
        "scientific_pass": False,
        "promoted_to_frozen_baseline": False,
        "sample_rate_hz": RATE_HZ,
        "grid_period_ns": GRID_PERIOD_NS,
        "pose_interpolation": False,
        "physical_time_windows_unchanged": True,
        "legacy_sealed_owner_unchanged": True,
        "adapter": adapter_audit,
        "frontend": seal,
        "frontend_access": frontend.access_audit(),
        "episode_count": len(episodes),
        "calibration": calibration.audit(),
        "protocol_torso_heading": torso_heading_audit,
        "protocol_shoulder_heading": shoulder_heading_audit,
        "protocol_hip_heading": hip_heading_audit,
        "qmt_hinges": qmt_audit,
        "qmt_olsson_hinge_axes": hinge_axis_audit,
        "output_coordinate_convention": {
            key: value.tolist() if isinstance(value, np.ndarray) else value
            for key, value in output_coordinate_convention.items()
        },
        "trajectory": trajectory_artifact,
        "frozen_replay_calibration": {
            "artifact": frozen_artifact,
            "schema": frozen_replay["schema"],
            "training_scope": frozen_replay["training_scope"],
            "holdout_payload_used_during_fit": False,
            "holdout_refit_allowed": False,
            "orientation_equation": frozen_replay["orientation_equation"],
            "decomposition_audit": frozen_replay["decomposition_audit"],
        },
        "legacy_ab": comparison,
        "same_time_physical_qa": same_time_qa,
        "representative_frame_qa": qa_summary,
        "renders": render_rows,
        "actual_pixel_review_required": True,
        "wall_s": float(time.perf_counter() - started),
    }
    legacy._json(output / "POSE_RESET_QMT_DIAGNOSTIC.json", report)
    print(json.dumps({
        "status": report["status"],
        "output": str(output),
        "wall_s": report["wall_s"],
        "legacy_ab": comparison,
        "failed_representative_labels": [
            row["label"] for row in render_rows if not row["physical_qa"]["pass"]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
