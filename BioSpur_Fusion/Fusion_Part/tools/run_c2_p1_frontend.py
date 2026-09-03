#!/usr/bin/env python3
"""Run the sealed-range P1 frontend and emit non-anatomical diagnostics."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from biospur_fusion.v0.c2_progressive.health import (
    build_health_report,
    estimate_initial_still,
    render_bias,
    render_frame_graph,
    render_health,
    render_time_gap,
)
from biospur_fusion.v0.c2_progressive.range_reader import SealedPrefitRangeReader


RUN_START_SHA256 = "999df2da1d71d5c702e1dda903b96926a2735e6129e35fe5942be749d7514f52"
PLAN_SHA256 = "65933ab210790516961c17863b22b3824d69c521647ea1035272c2addef4171f"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _write_json_exclusive(path: Path, value: Any, *, immutable: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(_jsonable(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    if immutable:
        path.chmod(0o444)


def _hash_artifacts(paths: list[Path]) -> list[dict[str, Any]]:
    return [{
        "path": str(path),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    } for path in paths]


def run(root: Path, run_dir: Path) -> dict[str, Any]:
    root = root.resolve()
    run_dir = run_dir.resolve()
    if not run_dir.is_relative_to(root):
        raise RuntimeError("P1 output must stay inside the canonical project")
    run_start = run_dir / "RUN_START_CONTRACT.json"
    plan_path = run_dir / "PAYLOAD_BYTE_ACCESS_PLAN.json"
    if _sha256(run_start) != RUN_START_SHA256:
        raise RuntimeError("run-start seal changed before P1")
    if _sha256(plan_path) != PLAN_SHA256:
        raise RuntimeError("payload byte plan changed before P1")
    startup_path = root / "config/biospur_fusion_v0_c2_main_contract_20260829/STARTUP_PARAMETERS.json"
    startup = json.loads(startup_path.read_text(encoding="utf-8"))
    node_to_segment = {
        row["hardware_id"]: row["segment"] for row in startup["node_mapping"]
    }
    edges = startup["global_graph"]["directed_edges"]
    if len(node_to_segment) != 10 or len(edges) != 9:
        raise RuntimeError("review-fixed node/edge topology changed")
    output_dir = run_dir / "P1_FRONTEND"
    output_dir.mkdir(parents=False, exist_ok=False)
    access_dir = output_dir / "access"
    access_dir.mkdir()
    reader = SealedPrefitRangeReader(
        root=root,
        plan_path=plan_path,
        expected_plan_sha256=PLAN_SHA256,
        nodes=list(node_to_segment),
    )
    actions = []
    first_result_path = run_dir / "FIRST_PAYLOAD_ACCESS.json"
    for index in range(len(reader.ranges)):
        print(f"P1 exact prefit read {index + 1:02d}/19 {reader.ranges[index]['action']}", flush=True)
        action = reader.read_action(index)
        if index == 0:
            _write_json_exclusive(first_result_path, {
                "schema": "biospur-c2-first-payload-access-v1",
                "recorded_utc": _utc_now(),
                "run_start_contract_sha256": RUN_START_SHA256,
                "payload_byte_access_plan_sha256": PLAN_SHA256,
                "intent_path": str(run_dir / "FIRST_PAYLOAD_ACCESS_INTENT.json"),
                "intent_sha256": _sha256(run_dir / "FIRST_PAYLOAD_ACCESS_INTENT.json"),
                "action": action.action,
                "chronological_index": action.chronological_index,
                "access": action.access_audit,
                "decode": action.decode_audit,
                "payload_whole_file_stat_hash_or_traversal": False,
                "heldout_bytes_touched": False,
                "listener_readiness_or_uwb_timing_opened": False,
            })
        action_access_path = access_dir / f"{index:02d}_{action.action}.json"
        _write_json_exclusive(action_access_path, {
            "access": action.access_audit,
            "decode": action.decode_audit,
        })
        actions.append(action)
    continuity = reader.state.audit()
    initial = estimate_initial_still(actions[0], node_to_segment=node_to_segment)
    health = build_health_report(
        actions,
        plan_ranges=reader.ranges,
        node_to_segment=node_to_segment,
        continuity_audit=continuity,
        initial=initial,
    )
    initial_path = output_dir / "P1_INITIAL_STILL_STOCHASTIC_STATE.json"
    health_path = output_dir / "P1_INPUT_HEALTH_AND_GAPS.json"
    continuity_path = output_dir / "P1_CAPTURE_WIDE_STATE_AUDIT.json"
    _write_json_exclusive(initial_path, initial)
    _write_json_exclusive(health_path, health)
    _write_json_exclusive(continuity_path, continuity)
    health_png = output_dir / "P1_INPUT_HEALTH.png"
    time_png = output_dir / "P1_TIME_AND_GAP.png"
    bias_png = output_dir / "P1_BIAS_AND_DRIFT.png"
    frame_png = output_dir / "P1_SENSOR_SEGMENT_FRAME_DIAGNOSTIC.png"
    for path in (health_png, time_png, bias_png, frame_png):
        if path.exists():
            raise FileExistsError(path)
    render_health(health, health_png)
    render_time_gap(health, time_png)
    render_bias(initial, health, bias_png)
    render_frame_graph(
        initial,
        node_to_segment=node_to_segment,
        edges=edges,
        output=frame_png,
    )
    union = [list(map(int, row["prefit_training_interval"])) for row in reader.ranges]
    total_read = sum(stop - start for start, stop in union)
    all_ranges_path = output_dir / "P1_EXACT_READ_UNION_AUDIT.json"
    _write_json_exclusive(all_ranges_path, {
        "schema": "biospur-c2-p1-exact-read-union-v1",
        "payload_path": str(reader.raw_path),
        "plan_path": str(plan_path),
        "plan_sha256": PLAN_SHA256,
        "open_count": len(actions),
        "exact_prefit_intervals": union,
        "read_union_bytes": total_read,
        "plan_training_bytes": int(reader.plan["training_bytes_planned"]),
        "read_union_equals_plan_training_bytes": total_read == int(reader.plan["training_bytes_planned"]),
        "whole_file_stat_performed": False,
        "whole_file_hash_performed": False,
        "whole_file_traversal_performed": False,
        "fit_freeze_heldout_intervals_opened": [],
        "default_external_holdouts_resolved_or_opened": False,
        "listener_readiness_or_uwb_timing_opened": False,
        "record_payload_classes_decoded": ["V47_TEN_NODE_IMU_KIND_3"],
        "other_record_payloads_interpreted": False,
        "decoded_measurement_fields": [
            "acc_raw", "gyro_raw", "node_timer_us",
            "derived_boot_epoch_from_timer_regression",
            "imu_sample_sequence_from_imu_header_sequence_plus_sample_index",
            "decode_acceptance_status_not_device_carried_status",
        ],
    })
    registry_path = output_dir / "P1_PARAMETER_OUTPUT_AMENDMENT_001.json"
    _write_json_exclusive(registry_path, {
        "schema": "biospur-c2-p1-parameter-output-amendment-v1",
        "created_utc": _utc_now(),
        "parent_registry": {
            "path": str(run_dir / "ACTIVE_PARAMETER_REGISTRY.json"),
            "sha256": _sha256(run_dir / "ACTIVE_PARAMETER_REGISTRY.json"),
        },
        "fit_authority": False,
        "parameters": {
            "gyro_bias_rad_s_per_node_axis": {
                node: row.get("gyro_bias_rad_s") for node, row in initial["nodes"].items()
            },
            "gyro_bias_covariance_per_node": {
                node: row.get("gyro_bias_covariance_rad2_s2") for node, row in initial["nodes"].items()
            },
            "duplicate_gap_saturation_and_status_statistics": health["totals"],
            "orientation_gap_process_noise_and_covariance_growth": health["gap_orientation_covariance"],
            "working_time_policy": "NATIVE_NODE_LOCAL_B306_TIMER_US;200_HZ_NOMINAL;NO_CROSS_NODE_ALIGNMENT_IN_P1",
        },
        "unresolved_before_real_fit": health["unresolved"],
        "real_fit_remains_forbidden": True,
    })
    images = [health_png, time_png, bias_png, frame_png]
    result_path = output_dir / "P1_RESULT.json"
    result = {
        "schema": "biospur-c2-p1-result-v1",
        "completed_utc": _utc_now(),
        "status": "MATERIALLY_RUNNABLE_DIAGNOSTIC_NON_ANATOMICAL_NOT_PASS",
        "real_fit_performed": False,
        "joint_centers_created": False,
        "segment_frames_claimed_resolved": False,
        "cross_node_time_alignment_claimed": False,
        "capture_wide_states_per_node": 1,
        "episode_resets": 0,
        "gap_concatenation": False,
        "images": _hash_artifacts(images),
        "evidence": _hash_artifacts([
            first_result_path,
            initial_path,
            health_path,
            continuity_path,
            all_ranges_path,
            registry_path,
        ]),
        "what_images_prove": "The sealed prefit ranges are decodable as ten-node v47 IMU, native timer/gap/bias diagnostics are visible, and the rooted nine-edge connectivity can be drawn without inventing joint centers.",
        "what_images_do_not_prove": "No anatomical pose, segment frame, joint center, cross-node synchronization, functional geometry, heading correction, calibration readiness, or PASS.",
    }
    _write_json_exclusive(result_path, result)
    return {**result, "result_path": str(result_path), "result_sha256": _sha256(result_path)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.root, args.run_dir)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
