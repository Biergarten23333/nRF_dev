"""Deterministic NPZ, NDJSON, and CSV product exports."""
from __future__ import annotations

import csv
import hashlib
import json
import time
from pathlib import Path

import numpy as np


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonable_array(value: np.ndarray):
    array = np.asarray(value)
    if array.dtype.kind in "f":
        obj = array.astype(object)
        obj[~np.isfinite(array)] = None
        return obj.tolist()
    return array.tolist()


def metadata(source_path: Path, source_sha256: str, config: dict,
             geometry: dict, frame_contract: dict, events: list[dict],
             calibration: dict, reset_events: list[dict],
             source_hashes: dict[str, str]) -> dict:
    return {
        "schema": "biospur.pure_imu.mvp_m1.export_metadata.v1",
        "source_path": str(source_path), "source_sha256": source_sha256,
        "source_hashes": source_hashes,
        "branch_labels": ["RAW", "GLOBAL_GAUGE_RECENTERED"],
        "frame_convention": config["frame_convention"],
        "quaternion_convention": config["quaternion_convention"],
        "skeleton_geometry": geometry, "segment_frame_contract": frame_contract,
        "calibration": calibration, "reset_events": reset_events,
        "root_mode": config["root_mode"], "manual_recenter_events": events,
        "automatic_heading_correction": False, "uwb_numeric_data": False,
        "legacy_stage1_confidence": "PRESERVED_IN_EXACT_NPZ_RAW_EVIDENCE_ONLY; NOT_USED_AS_PRODUCT_ACCURACY_OR_HEALTH",
    }


def npz_export(path: Path, raw: dict[str, np.ndarray], pose: dict[str, np.ndarray],
               meta: dict) -> dict:
    started = time.perf_counter()
    values = {name: value for name, value in raw.items()}
    values.update({
        "timestamp_us": pose["timestamp_us"], "frame_index": pose["frame_index"],
        "working_q_GB_wxyz": pose["working_q_GB_wxyz"],
        "display_q_GB_wxyz": pose["display_q_GB_wxyz"],
        "raw_q_PC_wxyz": raw["q_parent_child_wxyz"],
        "display_q_PC_wxyz": pose["display_q_PC_wxyz"],
        "raw_joint_positions_m": raw["joint_positions_m"],
        "display_joint_positions_m": pose["display_joint_positions_m"],
        "validity_mask": raw["valid"], "epoch_per_node": pose["epoch_per_node"],
        "reset_state_per_node": raw["filter_reset"], "quality_state": pose["quality_state"],
        "sample_age_s": pose["sample_age_s"],
        "time_since_last_valid_s": pose["time_since_last_valid_s"],
        "time_since_last_reset_s": pose["time_since_last_reset_s"],
        "last_reset_reason": pose["last_reset_reason"],
        "global_yaw_gauge_rad": pose["global_yaw_gauge_rad"],
        "global_yaw_gauge_epoch": pose["global_yaw_gauge_epoch"],
        "global_yaw_gauge_source": pose["global_yaw_gauge_source"],
        "root_mode": pose["root_mode"],
        "time_since_manual_recenter_s": pose["time_since_manual_recenter_s"],
        "metadata_json": np.array(json.dumps(meta, sort_keys=True, separators=(",", ":"), allow_nan=False)),
    })
    np.savez_compressed(path, **values)
    elapsed = time.perf_counter() - started
    return {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size,
            "frames": len(pose["frame_index"]), "elapsed_s": elapsed,
            "throughput_frames_s": len(pose["frame_index"])/elapsed}


def ndjson_export(path: Path, raw: dict[str, np.ndarray], pose: dict[str, np.ndarray],
                  meta: dict) -> dict:
    started = time.perf_counter(); n = len(pose["frame_index"])
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps({"type": "metadata", **meta}, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        for frame in range(n):
            row = {
                "type": "pose_frame", "timestamp_us": int(pose["timestamp_us"][frame]),
                "frame_index": frame,
                "raw_q_GB_wxyz": jsonable_array(raw["q_GB_wxyz"][frame]),
                "working_q_GB_wxyz": jsonable_array(pose["working_q_GB_wxyz"][frame]),
                "display_q_GB_wxyz": jsonable_array(pose["display_q_GB_wxyz"][frame]),
                "raw_q_PC_wxyz": jsonable_array(raw["q_parent_child_wxyz"][frame]),
                "display_q_PC_wxyz": jsonable_array(pose["display_q_PC_wxyz"][frame]),
                "raw_joint_positions_m": jsonable_array(raw["joint_positions_m"][frame]),
                "display_joint_positions_m": jsonable_array(pose["display_joint_positions_m"][frame]),
                "validity_mask": raw["valid"][frame].astype(int).tolist(),
                "epoch_per_node": pose["epoch_per_node"][frame].tolist(),
                "reset_state_per_node": raw["filter_reset"][frame].astype(int).tolist(),
                "quality_state": pose["quality_state"][frame].tolist(),
                "sample_age_s": jsonable_array(pose["sample_age_s"][frame]),
                "time_since_last_valid_s": jsonable_array(pose["time_since_last_valid_s"][frame]),
                "time_since_last_reset_s": jsonable_array(pose["time_since_last_reset_s"][frame]),
                "last_reset_reason": pose["last_reset_reason"][frame].tolist(),
                "global_yaw_gauge_rad": float(pose["global_yaw_gauge_rad"][frame]),
                "global_yaw_gauge_epoch": int(pose["global_yaw_gauge_epoch"][frame]),
                "global_yaw_gauge_source": str(pose["global_yaw_gauge_source"][frame]),
                "root_mode": str(pose["root_mode"][frame]),
            }
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
    elapsed = time.perf_counter() - started
    return {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size,
            "records": n + 1, "frames": n, "elapsed_s": elapsed,
            "throughput_frames_s": n/elapsed}


def csv_export(path: Path, raw: dict[str, np.ndarray], pose: dict[str, np.ndarray],
               meta: dict) -> dict:
    started = time.perf_counter(); n = len(pose["frame_index"])
    joints = [str(value) for value in raw["joint_names"]]
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write("# metadata=" + json.dumps(meta, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("timestamp_us", "frame_index", "joint", "raw_x_m", "raw_y_m", "raw_z_m",
                         "display_x_m", "display_y_m", "display_z_m", "available",
                         "validity_mask", "epoch_per_node", "reset_state_per_node",
                         "global_yaw_gauge_rad", "global_yaw_gauge_epoch", "global_yaw_gauge_source", "root_mode"))
        for frame in range(n):
            for joint, name in enumerate(joints):
                raw_point = raw["joint_positions_m"][frame, joint]
                display_point = pose["display_joint_positions_m"][frame, joint]
                fmt = lambda value: "" if not np.isfinite(value) else format(float(value), ".9g")
                writer.writerow((int(pose["timestamp_us"][frame]), frame, name,
                                 *(fmt(value) for value in raw_point), *(fmt(value) for value in display_point),
                                 int(raw["joint_available"][frame, joint]),
                                 json.dumps(raw["valid"][frame].astype(int).tolist(), separators=(",", ":")),
                                 json.dumps(pose["epoch_per_node"][frame].tolist(), separators=(",", ":")),
                                 json.dumps(raw["filter_reset"][frame].astype(int).tolist(), separators=(",", ":")),
                                 format(float(pose["global_yaw_gauge_rad"][frame]), ".17g"),
                                 int(pose["global_yaw_gauge_epoch"][frame]),
                                 str(pose["global_yaw_gauge_source"][frame]), str(pose["root_mode"][frame])))
    elapsed = time.perf_counter() - started
    return {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size,
            "records": n*len(joints) + 2, "frames": n, "elapsed_s": elapsed,
            "throughput_frames_s": n/elapsed}
