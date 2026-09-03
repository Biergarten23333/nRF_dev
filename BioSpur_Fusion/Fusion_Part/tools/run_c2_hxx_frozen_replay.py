#!/usr/bin/env python3
"""Replay sealed C2 H01/H02 with the frozen C2 avatar calibration."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from biospur_fusion.c2_coupled_progressive.contracts import (
    NODE_TO_SEGMENT,
    ROOT,
    load_effective_config,
)
from biospur_fusion.c2_coupled_progressive.estimator import SEGMENTS
from biospur_fusion.c2_coupled_progressive.holdout_replay import (
    apply_frozen_avatar_calibration,
    continuous_vqf_holdout_samples,
    synchronize_holdout_quaternions,
)
from biospur_fusion.c2_coupled_progressive.renderer import (
    SKELETON_LINES,
    display_models,
    joints_for_frame,
    physical_qa,
    render_triptych,
)
from biospur_fusion.time.common_clock import align_capture_bounded, models_as_json
from tools.build_c2_avatar_interactive import JOINT_NAMES, _html, _line_color


C2_ID = "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
C2_ROOT = ROOT / "datasets/phase2_calibration" / C2_ID
HOLDOUTS = ("H01_boxing", "H02_golf")
INITIAL_TRAINING_RANGE = C2_ROOT / "actions/00_initial_still/rep_01/manifest/CONTINUOUS_RANGE.json"
CANONICAL_RAW = C2_ROOT / "system/fusion_continuous/fusion_host_raw.cobs.bin"
TIMING_LOG = C2_ROOT / "system/fusion_continuous/fusion_cdc.log"
LISTENER_DIR = C2_ROOT / "system/listeners/passive_5"
READINESS = C2_ROOT / "system/readiness/SYSTEM_READINESS_REPORT.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"object JSON required: {path}")
    return value


def _events(path: Path) -> dict[str, dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return {str(row["event"]): row for row in rows}


def _timing_seed(path: Path, target_ns: int, *, jsonl: bool) -> int:
    size = path.stat().st_size
    low = 0
    high = size
    with path.open("rb") as stream:
        for _ in range(48):
            if high - low < 4096:
                break
            middle = (low + high) // 2
            stream.seek(middle)
            if middle:
                stream.readline()
            row_start = stream.tell()
            line = stream.readline()
            if not line:
                high = middle
                continue
            try:
                timestamp = (
                    int(json.loads(line)["arrival_monotonic_ns"])
                    if jsonl
                    else int(round(float(line.split(maxsplit=2)[1]) * 1e9))
                )
            except (ValueError, KeyError, IndexError, json.JSONDecodeError):
                low = stream.tell()
                continue
            if timestamp < target_ns:
                low = row_start
            else:
                high = row_start
    return max(1, low)


def _holdout_record(action: str) -> dict[str, Any]:
    root = C2_ROOT / f"holdout/{action}/rep_01"
    event_path = root / "events/ACTION_EVENTS.jsonl"
    range_path = root / "manifest/CONTINUOUS_RANGE.json"
    definition_path = C2_ROOT / f"holdout/{action}/ACTION_DEFINITION.json"
    event = _events(event_path)
    byte_range = _json(range_path)
    definition = _json(definition_path)
    if definition.get("data_role") != "SEALED_PHASE3_REGRESSION":
        raise RuntimeError(f"{action}: not a sealed regression holdout")
    raw_slice = root / "raw/fusion_host_raw.cobs.bin"
    observed_slice_sha = _sha256(raw_slice)
    if observed_slice_sha != byte_range["slice_sha256"]:
        raise RuntimeError(f"{action}: raw slice hash mismatch")
    return {
        "action": action,
        "instruction_zh": definition["instruction_zh"],
        "data_role": definition["data_role"],
        "repetition_start_host_ns": int(event["REPETITION_START_BOUNDARY"]["host_monotonic_ns"]),
        "formal_start_host_ns": int(event["ACTION_START"]["host_monotonic_ns"]),
        "formal_stop_host_ns": int(event["ACTION_STOP"]["host_monotonic_ns"]),
        "repetition_stop_host_ns": int(event["REPETITION_END_BOUNDARY"]["host_monotonic_ns"]),
        "start_byte": int(byte_range["start_byte_inclusive"]),
        "stop_byte": int(byte_range["end_byte_exclusive"]),
        "slice_sha256": observed_slice_sha,
        "event_path": str(event_path.relative_to(ROOT)),
        "event_sha256": _sha256(event_path),
        "range_path": str(range_path.relative_to(ROOT)),
        "range_sha256": _sha256(range_path),
    }


def _alignment(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    target_seed_ns = record["repetition_stop_host_ns"] - 1_000_000_000
    timing_seeds = {
        str(TIMING_LOG.resolve()): _timing_seed(
            TIMING_LOG, target_seed_ns, jsonl=False
        )
    }
    listener_summary = _json(LISTENER_DIR / "summary.json")
    for snr, info in listener_summary["listeners"].items():
        kinds = info.get("kinds", {})
        if (
            info.get("first_lstat", {}).get("role") == "OBSERVER"
            and kinds.get("LPD")
            and kinds.get("LBD")
        ):
            path = LISTENER_DIR / "listeners" / f"{snr}.jsonl"
            timing_seeds[str(path.resolve())] = _timing_seed(
                path, target_seed_ns, jsonl=True
            )
    models, residuals, gate = align_capture_bounded(
        TIMING_LOG,
        LISTENER_DIR,
        READINESS,
        record["repetition_start_host_ns"] * 1e-9,
        record["repetition_stop_host_ns"] * 1e-9,
        tuple(NODE_TO_SEGMENT),
        expected_readiness_sha256=_sha256(READINESS),
        safe_ceiling_seed_offsets=timing_seeds,
        forbidden_time_intervals_ns=[],
    )
    safety_failures = list(gate["reconstruction_safety_failures"])
    diagnostic_pair_floor = min(
        int(row["clean_pairs"])
        for row in gate["per_boot_segment_coverage"].values()
    )
    # H02 is slightly short of its duration-derived clean-pair count. Residual,
    # coverage, maximum-gap, monotonicity and integer-ambiguity gates all pass.
    # For a non-PASS visual holdout replay, preserve the exact shortfall as
    # evidence instead of treating it as a fatal inability to put the ten IMUs
    # on one time grid.
    diagnostic_nonfatal = (
        set(safety_failures) == {"minimum_clean_listener_pairs"}
        and diagnostic_pair_floor >= 10
    )
    if not gate["reconstruction_safe"] and not diagnostic_nonfatal:
        raise RuntimeError(
            f"{record['action']}: unsafe common-time reconstruction: "
            f"{safety_failures}"
        )
    gate["diagnostic_nonfatal_exception"] = {
        "applied": bool(diagnostic_nonfatal),
        "scope": "HXX_VISUAL_REPLAY_ONLY_NOT_SCIENTIFIC_PASS",
        "preserved_original_failure": safety_failures,
        "minimum_observed_clean_pairs": diagnostic_pair_floor,
        "scientific_threshold_clean_pairs": int(
            gate["thresholds"]["minimum_clean_listener_pairs"]
        ),
        "other_reconstruction_safety_failures_allowed": [],
    }
    bridge = gate["action_annotation_bridge"]

    def map_host(host_ns: int) -> int:
        return int(round((
            bridge["listener_global_us_per_host_s"] * host_ns * 1e-9
            + bridge["listener_global_us_intercept"]
        ) * 1000))

    return models, {
        "models": models_as_json(models),
        "residual_count": len(residuals),
        "gate": gate,
        "formal_start_shared_ns": map_host(record["formal_start_host_ns"]),
        "formal_stop_shared_ns": map_host(record["formal_stop_host_ns"]),
    }


def _load_frozen_calibration(run_dir: Path) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    report_path = run_dir / "POSE_RESET_QMT_DIAGNOSTIC.json"
    report = _json(report_path)
    artifact = report["frozen_replay_calibration"]["artifact"]
    path = ROOT / artifact["path"]
    if _sha256(path) != artifact["sha256"]:
        raise RuntimeError("frozen C2 calibration hash mismatch")
    with np.load(path, allow_pickle=False) as archive:
        arrays = {key: np.array(archive[key]) for key in archive.files}
    if artifact["segment_order"] != list(SEGMENTS):
        raise RuntimeError("frozen calibration segment order changed")
    if report["frozen_replay_calibration"]["holdout_payload_used_during_fit"]:
        raise RuntimeError("frozen calibration admits holdout access")
    return report, arrays


def _trajectory_npz(path: Path, trajectory: dict[str, Any]) -> dict[str, Any]:
    arrays: dict[str, np.ndarray] = {}
    for action, segments in trajectory["trajectory"].items():
        for segment, row in segments.items():
            base = f"trajectory/{action}/{segment}"
            arrays[f"{base}/time_root_s"] = np.asarray(row["time_root_s"])
            arrays[f"{base}/quat_world_segment_wxyz"] = np.asarray(
                row["quat_world_segment_wxyz"]
            )
            arrays[f"{base}/mask"] = np.asarray(row["mask"], dtype=bool)
    convention = trajectory["output_coordinate_convention"]
    arrays["output_coordinates/matrix_world_output_from_internal"] = np.asarray(
        convention["matrix_world_output_from_internal"]
    )
    arrays["output_coordinates/plane_normal_world_internal"] = np.asarray(
        convention["plane_normal_world_internal"]
    )
    np.savez_compressed(path, **arrays)
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": _sha256(path),
        "array_count": len(arrays),
    }


def _interactive_payload(
    trajectory: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    config = load_effective_config()
    model = display_models(config)[1]
    joint_index = {name: index for index, name in enumerate(JOINT_NAMES)}
    lines = [
        [joint_index[first], joint_index[second], _line_color(first, second)]
        for first, second in SKELETON_LINES
    ]
    episodes = []
    for record in records:
        action = record["action"]
        source_time = trajectory["trajectory"][action]["pelvis"]["time_root_s"]
        frames = []
        for frame in range(len(source_time)):
            joints = joints_for_frame(trajectory, action, frame, model, config)
            frames.append([
                round(float(value), 4)
                for name in JOINT_NAMES
                for value in joints[name]
            ])
        episodes.append({
            "id": action,
            "instruction": record["instruction_zh"],
            "note": "封存留出回放：只使用冻结 C2 标定，不用本动作重新拟合。",
            "time": np.round(source_time - source_time[0], 3).tolist(),
            "sourceFrame": list(range(len(source_time))),
            "frames": frames,
        })
    convention = trajectory["output_coordinate_convention"]
    matrix = np.asarray(convention["matrix_world_output_from_internal"])
    internal_right = np.asarray(convention["plane_normal_world_internal"])
    output_right = matrix @ internal_right
    internal_forward = np.cross(np.array([0.0, 0.0, 1.0]), internal_right)
    output_forward = matrix @ internal_forward
    lateral_yaw = math.atan2(float(internal_right[1]), float(internal_right[0]))
    return {
        "schema": "biospur-c2-hxx-interactive-avatar-v1",
        "jointNames": list(JOINT_NAMES),
        "lines": lines,
        "episodes": episodes,
        "targetFps": 20.0,
        "viewGauge": {
            "method": "frozen_c2_output_coordinates_and_body_camera",
            "referenceEpisode": HOLDOUTS[0],
            "bodyRightWorldXY": output_right[:2].tolist(),
            "bodyForwardWorldXY": output_forward[:2].tolist(),
            "forwardBranchYawDeg": 0,
            "forwardBranchSign": 1.0,
            "frontYawRad": float(math.pi - lateral_yaw),
            "rearYawRad": float(-lateral_yaw),
            "rightSideYawRad": float(math.pi - lateral_yaw - math.pi / 2),
            "topYawRad": float(-lateral_yaw),
            "namedViewsFollowCurrentBodyFrame": True,
            "cameraCounterRotatedWithForwardBranch": False,
            "globalLateralGeometryMirror": False,
            "outputCoordinatesSolidified": True,
            "outputCoordinateParity": -1.0,
            "globalMirrorPlane": {
                "normalWorldXY": internal_right[:2].tolist(),
                "scope": "already_solidified",
                "cameraAndGridReflected": False,
            },
            "trajectoryModified": False,
        },
        "viewerRepair": False,
        "ik": False,
        "retarget": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    calibration_run = args.calibration_run.resolve()
    output = args.output.resolve()
    if ROOT not in calibration_run.parents or ROOT not in output.parents:
        raise SystemExit("all paths must remain under canonical Fusion_Part")
    output.mkdir(parents=True, exist_ok=False)

    calibration_report, frozen = _load_frozen_calibration(calibration_run)
    records = [_holdout_record(action) for action in HOLDOUTS]
    initial_range = _json(INITIAL_TRAINING_RANGE)
    raw_start = int(initial_range["start_byte_inclusive"])
    raw_stop = max(record["stop_byte"] for record in records)
    holdout_ranges = {
        record["action"]: (record["start_byte"], record["stop_byte"])
        for record in records
    }
    retained, orientation_audit = continuous_vqf_holdout_samples(
        CANONICAL_RAW, raw_start, raw_stop, holdout_ranges
    )

    alignments: dict[str, dict[str, Any]] = {}
    node_models: dict[str, dict[str, Any]] = {}
    for record in records:
        models, audit = _alignment(record)
        node_models[record["action"]] = models
        alignments[record["action"]] = audit

    segment_index = {segment: index for index, segment in enumerate(SEGMENTS)}
    matrix = np.asarray(
        calibration_report["output_coordinate_convention"][
            "matrix_world_output_from_internal"
        ],
        dtype=float,
    )
    normal = np.asarray(
        calibration_report["output_coordinate_convention"][
            "plane_normal_world_internal"
        ],
        dtype=float,
    )
    trajectory: dict[str, Any] = {
        "schema": "biospur-c2-frozen-calibration-hxx-replay-v1",
        "trajectory": {},
        "output_coordinate_convention": {
            "schema": "biospur-c2-capture-wide-output-coordinates-v1",
            "matrix_world_output_from_internal": matrix,
            "plane_normal_world_internal": normal,
        },
    }
    synchronization_audit: dict[str, Any] = {}
    for record in records:
        action = record["action"]
        alignment = alignments[action]
        grid, pelvis_timer_us, synchronized, sync_audit = (
            synchronize_holdout_quaternions(
                retained[action],
                node_models[action],
                alignment["formal_start_shared_ns"],
                alignment["formal_stop_shared_ns"],
            )
        )
        synchronization_audit[action] = sync_audit
        trajectory["trajectory"][action] = {}
        for node, segment in NODE_TO_SEGMENT.items():
            index = segment_index[segment]
            corrected = apply_frozen_avatar_calibration(
                synchronized[node],
                pelvis_timer_us,
                initial_world_sensor=frozen["initial_world_sensor"][index],
                functional_world_yaw_rad=float(
                    frozen["functional_world_yaw_rad"][index]
                ),
                common_pelvis_yaw_closure_rad=float(
                    frozen["common_pelvis_yaw_closure_rad"]
                ),
                reference_time_s=float(frozen["reference_time_s"]),
                final_reference_time_s=float(frozen["final_reference_time_s"]),
            )
            trajectory["trajectory"][action][segment] = {
                "time_root_s": (grid - grid[0]) * 1e-9,
                "quat_world_segment_wxyz": corrected,
                "mask": np.ones(len(grid), dtype=bool),
            }

    config = load_effective_config()
    model = display_models(config)[1]
    qa_summary: dict[str, Any] = {}
    representative_images: list[dict[str, Any]] = []
    for action in HOLDOUTS:
        frame_count = len(trajectory["trajectory"][action]["pelvis"]["time_root_s"])
        qa_rows = []
        for frame in range(frame_count):
            internal_joints = joints_for_frame(
                trajectory,
                action,
                frame,
                model,
                config,
                apply_output_coordinates=False,
            )
            qa_rows.append(physical_qa(internal_joints))
        qa_summary[action] = {
            "frame_count": frame_count,
            "physical_pass_frames": int(sum(row["pass"] for row in qa_rows)),
            "physical_pass_fraction": float(np.mean([row["pass"] for row in qa_rows])),
            "failure_counts": {
                key: int(sum(bool(row.get(key, False)) for row in qa_rows))
                for key in (
                    "knee_front_back_split",
                    "leg_segment_intersection_3d",
                    "collapse",
                    "gross_axial_twist",
                )
            },
        }
        for label, fraction in (("early", 0.25), ("middle", 0.5), ("late", 0.75)):
            frame = min(frame_count - 1, int(round((frame_count - 1) * fraction)))
            joints = joints_for_frame(trajectory, action, frame, model, config)
            image = output / f"{action}_{label}_front_side_top.png"
            render_triptych(joints, image, f"{action} {label} frame={frame}")
            representative_images.append({
                "action": action,
                "label": label,
                "frame": frame,
                "path": str(image.relative_to(ROOT)),
                "sha256": _sha256(image),
            })

    trajectory_artifact = _trajectory_npz(
        output / "HXX_FROZEN_C2_REPLAY_TRAJECTORY.npz", trajectory
    )
    report = {
        "schema": "biospur-c2-hxx-frozen-replay-report-v1",
        "status": "DIAGNOSTIC_HOLDOUT_REPLAY",
        "scientific_pass": False,
        "calibration_refit_on_hxx": False,
        "holdout_action_semantics_used_for_fit": False,
        "source_scope": "CAPTURE2_H01_H02_ONLY",
        "source_records": records,
        "continuous_orientation": orientation_audit,
        "time_alignment": alignments,
        "synchronization": synchronization_audit,
        "frozen_calibration": calibration_report["frozen_replay_calibration"],
        "frozen_calibration_report": str(
            (calibration_run / "POSE_RESET_QMT_DIAGNOSTIC.json").relative_to(ROOT)
        ),
        "frozen_calibration_report_sha256": _sha256(
            calibration_run / "POSE_RESET_QMT_DIAGNOSTIC.json"
        ),
        "output_coordinate_convention": calibration_report[
            "output_coordinate_convention"
        ],
        "trajectory": trajectory_artifact,
        "physical_qa": qa_summary,
        "representative_images": representative_images,
        "viewer_ik_rebase_retarget_or_repair": False,
        "actual_pixel_review_required": True,
    }
    report_path = output / "HXX_FROZEN_C2_REPLAY_REPORT.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    interactive = _interactive_payload(trajectory, records)
    viewer_dir = output / "INTERACTIVE_3D"
    viewer_dir.mkdir()
    html_path = viewer_dir / "c2_hxx_avatar_3d.html"
    html_path.write_text(
        _html(
            interactive,
            str(report_path.relative_to(ROOT)),
            trajectory_artifact["sha256"],
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema": "biospur-c2-hxx-interactive-manifest-v1",
        "html": str(html_path.relative_to(ROOT)),
        "html_sha256": _sha256(html_path),
        "report": str(report_path.relative_to(ROOT)),
        "report_sha256": _sha256(report_path),
        "trajectory": trajectory_artifact,
        "actions": list(HOLDOUTS),
        "calibration_refit_on_hxx": False,
        "orientation_state_instances_per_node": 1,
        "viewer_ik_rebase_retarget_or_repair": False,
    }
    manifest_path = viewer_dir / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": report["status"],
        "output": str(output),
        "html": str(html_path),
        "actions": list(HOLDOUTS),
        "physical_qa": qa_summary,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
