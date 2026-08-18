from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping

import numpy as np

from .core import (
    SCHEMA_VERSION, atomic_json, dynamic_offset, hash_ordered_strings,
    sha256_file, uid_string, validate_exact_mapping,
)

PARTITIONS = ("fit", "guard", "propagation", "validation")
CLASSES = (
    "AXIS_FIT", "HEADING_FIT", "VALIDATION", "STATIC_FIT",
    "STATIC_VALIDATION", "GUARD", "PROPAGATION_ONLY",
)
PHASES = ("PREPARATION", "FORMAL_ACTION", "RECOVERY_OR_FINAL_REST")


def _load_manifest(root: Path, partition: str) -> dict:
    path = root/partition/"CACHE_MANIFEST.json"
    payload = json.loads(path.read_text())
    if payload.get("schema") != "biospur-phase3r21-real-imu-cache-v1":
        raise RuntimeError(f"unexpected cache schema: {path}")
    if payload.get("uwb_measurement_columns") != 0:
        raise RuntimeError("cache contains forbidden UWB measurement columns")
    for name, row in payload["columns"].items():
        source = root/partition/f"{name}.npy"
        if sha256_file(source) != row["sha256"]:
            raise RuntimeError(f"cache column SHA mismatch: {source}")
    return payload


def _coded(values: np.ndarray, names: list[str]) -> np.ndarray:
    result = np.full(len(values), 255, dtype=np.uint8)
    for code, name in enumerate(names):
        result[np.asarray(values == name)] = code
    if np.any(result == 255):
        unknown = np.unique(values[result == 255])
        raise RuntimeError(f"unknown categorical values: {unknown.tolist()}")
    return result


def _cycle_ordinals(values: np.ndarray) -> np.ndarray:
    output = np.full(len(values), -1, dtype=np.int16)
    dynamic = np.char.find(values, ":cycle:") >= 0
    if np.any(dynamic):
        output[dynamic] = np.asarray([int(str(x).rsplit(":", 1)[1]) for x in values[dynamic]], dtype=np.int16)
    return output


def _gather_column(cache_root: Path, partition_code: np.ndarray, local_index: np.ndarray,
                   name: str, shape_tail: tuple[int, ...], dtype: np.dtype) -> np.ndarray:
    result = np.empty((len(local_index),) + shape_tail, dtype=dtype)
    for code, partition in enumerate(PARTITIONS):
        selected = np.flatnonzero(partition_code == code)
        if not len(selected):
            continue
        source = np.load(cache_root/partition/f"{name}.npy", mmap_mode="r")
        result[selected] = source[local_index[selected]]
    return result


def _save_class(root: Path, name: str, indexes: np.ndarray, arrays: Mapping[str, np.ndarray],
                cache_root: Path) -> dict:
    target = root/name.lower()
    target.mkdir(parents=True, exist_ok=False)
    order = np.lexsort((arrays["source_offset"][indexes], arrays["sequence"][indexes],
                        arrays["node_code"][indexes], arrays["common_time_ns"][indexes]))
    indexes = indexes[order]
    columns: dict[str, dict] = {}
    direct = {
        "action_code": arrays["action_code"][indexes],
        "phase_code": arrays["phase_code"][indexes],
        "cycle_ordinal": arrays["cycle_ordinal"][indexes],
        "node_code": arrays["node_code"][indexes],
        "boot": arrays["boot"][indexes],
        "timer2_us": arrays["timer2_us"][indexes],
        "common_time_ns": arrays["common_time_ns"][indexes],
        "sequence": arrays["sequence"][indexes],
        "source_offset": arrays["source_offset"][indexes],
        "source_length": arrays["source_length"][indexes],
        "q_EI_wxyz": arrays["q_EI_wxyz"][indexes],
    }
    part = arrays["partition_code"][indexes]
    local = arrays["local_index"][indexes]
    direct["gyro_rad_s"] = _gather_column(cache_root, part, local, "gyro", (3,), np.dtype("<f8"))
    direct["accel_m_s2"] = _gather_column(cache_root, part, local, "accel", (3,), np.dtype("<f8"))
    for column, value in direct.items():
        path = target/f"{column}.npy"
        np.save(path, value, allow_pickle=False)
        columns[column] = {
            "shape": list(value.shape), "dtype": str(value.dtype),
            "sha256": sha256_file(path),
        }
    payload = {
        "schema": "biospur-phase3r23-frontend-class-cache-v1",
        "class": name, "rows": int(len(indexes)), "columns": columns,
        "official_vqf": True, "uwb_measurement_columns": 0,
        "h_rows": 0, "p_rows": 0, "b1_rows": 0, "opensense_rows": 0,
    }
    payload["closure_sha256"] = hashlib.sha256(json.dumps(columns, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    atomic_json(target/"CACHE_MANIFEST.json", payload)
    return payload


def build_frontend_cache(*, cache_root: Path, output_root: Path, contract: Mapping,
                         session_manifest: Mapping, actual_mapping: Mapping[str, str]) -> dict:
    """Reconstruct continuous per-node official VQF, then physically split factor views.

    Validation measurements participate only in the causal VQF recurrence here.
    They are written to a separate sealed class cache and are not returned to the
    FIT consumer. No H cache or archived B0/B1/P trajectory is opened.
    """
    if output_root.exists():
        raise FileExistsError(output_root)
    output_root.mkdir(parents=True)
    validate_exact_mapping(actual_mapping, contract["operator_mapping"])
    if session_manifest["session_id"] != "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2":
        raise RuntimeError("session identity mismatch")
    if session_manifest["subject_id"] != "capture_2_with_joint_label":
        raise RuntimeError("subject identity mismatch")

    manifests = {part: _load_manifest(cache_root, part) for part in PARTITIONS}
    if sum(int(x["rows"]) for x in manifests.values()) != 1_522_793:
        raise RuntimeError("development-only row total is not 1,522,793")

    nodes = sorted(contract["operator_mapping"])
    actions = [
        "00_initial_still", "02_t_pose", "03_pelvis_hula_circle",
        "04_shoulder_left", "05_shoulder_right", "06_elbow_left",
        "07_elbow_right", "08_hip_left", "09_hip_right",
        "10_knee_left_seated", "11_knee_right_seated", "12_heel_raise_left",
        "13_heel_raise_right", "14_trunk_flex_extend", "15_trunk_axial_rotation",
        "16_squat", "17_final_still", "18_heel_to_butt_left",
        "19_heel_to_butt_right",
    ]
    pieces: dict[str, list[np.ndarray]] = defaultdict(list)
    for part_code, part in enumerate(PARTITIONS):
        base = cache_root/part
        count = int(manifests[part]["rows"])
        action = np.load(base/"action.npy", mmap_mode="r")
        node = np.load(base/"node.npy", mmap_mode="r")
        phase = np.load(base/"phase.npy", mmap_mode="r")
        cycle = np.load(base/"cycle.npy", mmap_mode="r")
        pieces["partition_code"].append(np.full(count, part_code, dtype=np.uint8))
        pieces["local_index"].append(np.arange(count, dtype=np.int32))
        pieces["action_code"].append(_coded(action, actions))
        pieces["node_code"].append(_coded(node, nodes))
        pieces["phase_code"].append(_coded(phase, list(PHASES)))
        pieces["cycle_ordinal"].append(_cycle_ordinals(cycle))
        for name, dtype in (("boot", "<i4"), ("timer2_us", "<i8"),
                            ("common_time_ns", "<i8"), ("sequence", "<i8"),
                            ("source_offset", "<i8"), ("source_length", "<i4")):
            pieces[name].append(np.asarray(np.load(base/f"{name}.npy", mmap_mode="r"), dtype=dtype))
    arrays = {name: np.concatenate(value) for name, value in pieces.items()}
    n_rows = len(arrays["common_time_ns"])
    if n_rows != 1_522_793:
        raise RuntimeError("concatenation row mismatch")

    # Official VQF state is continuous per node, per boot, across action boundaries.
    from vqf import VQF
    q = np.empty((n_rows, 4), dtype="<f8")
    vqf_rows = {}
    for node_code, node in enumerate(nodes):
        selected = np.flatnonzero(arrays["node_code"] == node_code)
        order = np.lexsort((arrays["source_offset"][selected], arrays["sequence"][selected],
                            arrays["common_time_ns"][selected]))
        selected = selected[order]
        gyro = _gather_column(cache_root, arrays["partition_code"][selected], arrays["local_index"][selected],
                              "gyro", (3,), np.dtype("<f8"))
        accel = _gather_column(cache_root, arrays["partition_code"][selected], arrays["local_index"][selected],
                               "accel", (3,), np.dtype("<f8"))
        state = VQF(gyrTs=0.005, accTs=0.005).updateBatchFullState(gyro, accel)
        node_q = np.asarray(state["quat6D"], dtype="<f8")
        if node_q.shape != (len(selected), 4) or not np.all(np.isfinite(node_q)):
            raise RuntimeError(f"invalid VQF state for {node}")
        q[selected] = node_q
        vqf_rows[node] = {
            "rows": int(len(selected)), "boots": sorted(map(int, np.unique(arrays["boot"][selected]))),
            "first_common_time_ns": int(arrays["common_time_ns"][selected[0]]),
            "last_common_time_ns": int(arrays["common_time_ns"][selected[-1]]),
        }
    arrays["q_EI_wxyz"] = q

    class_code = np.full(n_rows, CLASSES.index("GUARD"), dtype=np.uint8)
    formal = arrays["phase_code"] == PHASES.index("FORMAL_ACTION")
    class_code[~formal] = CLASSES.index("PROPAGATION_ONLY")
    action_cycles: dict[str, dict] = {}
    complete_cycles: dict[int, set[int]] = {}
    for action_code, action in enumerate(actions):
        selected_action = arrays["action_code"] == action_code
        if action in {"00_initial_still", "02_t_pose"}:
            times = arrays["common_time_ns"][selected_action & formal]
            start, stop = int(times.min()), int(times.max()+1)
            duration = stop-start
            fit_boundary = start+int(duration*float(contract["static_split"]["fit_fraction"]))
            validation_boundary = start+int(duration*(float(contract["static_split"]["fit_fraction"])+float(contract["static_split"]["guard_fraction"])))
            lo_support = arrays["common_time_ns"]-2_500_000
            hi_support = arrays["common_time_ns"]+2_500_000
            fit = selected_action & formal & (hi_support < fit_boundary)
            validation = selected_action & formal & (lo_support > validation_boundary)
            class_code[fit] = CLASSES.index("STATIC_FIT")
            class_code[validation] = CLASSES.index("STATIC_VALIDATION")
            action_cycles[action] = {"kind": "static", "fit_boundary_ns": fit_boundary,
                                     "validation_boundary_ns": validation_boundary}
            continue
        if action == "17_final_still":
            class_code[selected_action & formal] = CLASSES.index("STATIC_VALIDATION")
            action_cycles[action] = {"kind": "final_still_validation_only"}
            continue
        complete: list[int] = []
        for cycle in sorted(map(int, np.unique(arrays["cycle_ordinal"][selected_action & formal]))):
            if cycle < 0:
                continue
            m = selected_action & formal & (arrays["cycle_ordinal"] == cycle)
            span = (int(arrays["common_time_ns"][m].max())-int(arrays["common_time_ns"][m].min()))/1e9
            node_count = len(np.unique(arrays["node_code"][m]))
            if span >= float(contract["dynamic_split"]["complete_cycle_min_span_s"]) and node_count == 10:
                complete.append(cycle)
        complete_cycles[action_code] = set(complete)
        offset = dynamic_offset(int(contract["master_seed"]), action)
        assignments = {}
        for ordinal, cycle in enumerate(complete):
            split_name = contract["dynamic_split"]["classes"][(ordinal+offset) % 3]
            assignments[str(cycle)] = split_name
            m = selected_action & formal & (arrays["cycle_ordinal"] == cycle)
            # R2.1 GUARD rows are boundary-uncertain and remain GUARD.
            prior_guard = arrays["partition_code"] == PARTITIONS.index("guard")
            class_code[m & ~prior_guard] = CLASSES.index(split_name)
        action_cycles[action] = {"kind": "dynamic", "complete_cycles": complete,
                                 "partial_cycles": sorted(set(map(int, np.unique(arrays["cycle_ordinal"][selected_action & formal])))-set(complete)),
                                 "offset_mod3": offset, "assignments": assignments}

    arrays["class_code"] = class_code
    class_manifests = {}
    for code, name in enumerate(CLASSES):
        class_manifests[name] = _save_class(output_root, name, np.flatnonzero(class_code == code), arrays, cache_root)

    per_group: dict[str, dict] = {}
    uid_sets: dict[str, set[str]] = {}
    overall_uid = set()
    for class_id, class_name in enumerate(CLASSES):
        class_index = np.flatnonzero(class_code == class_id)
        class_values = []
        for idx in class_index:
            value = uid_string(nodes[int(arrays["node_code"][idx])], arrays["boot"][idx], arrays["timer2_us"][idx],
                               arrays["sequence"][idx], arrays["source_offset"][idx])
            class_values.append(value)
        uid_sets[class_name] = set(class_values)
        if len(uid_sets[class_name]) != len(class_values):
            raise RuntimeError(f"duplicate UID inside {class_name}")
        overlap = overall_uid & uid_sets[class_name]
        if overlap:
            raise RuntimeError(f"UID overlap across R2.3 classes: {next(iter(overlap))}")
        overall_uid |= uid_sets[class_name]
        for action_code, action in enumerate(actions):
            for node_code, node in enumerate(nodes):
                m = class_index[(arrays["action_code"][class_index] == action_code) & (arrays["node_code"][class_index] == node_code)]
                if not len(m):
                    continue
                values = [uid_string(node, arrays["boot"][i], arrays["timer2_us"][i], arrays["sequence"][i], arrays["source_offset"][i]) for i in m]
                per_group[f"{action}|{node}|{class_name}"] = {"count": len(values), "uid_sha256": hash_ordered_strings(sorted(values))}
    if len(overall_uid) != n_rows:
        raise RuntimeError("R2.3 UID reconciliation did not close")

    report = {
        "schema": "biospur-phase3r23-split-and-uid-manifest-v1", "core_schema": SCHEMA_VERSION,
        "capture_id": "Capture_2_with_JOINT_LABEL", "session_id": session_manifest["session_id"],
        "subject_id": session_manifest["subject_id"], "mapping": dict(contract["operator_mapping"]),
        "donning_scope": "same operator-attested donning; no module exchange/strap rotation/redonning",
        "source_broker_manifest_sha256": sha256_file(cache_root/"BROKER_MANIFEST.json"),
        "source_partition_manifests": {name: sha256_file(cache_root/name/"CACHE_MANIFEST.json") for name in PARTITIONS},
        "total_development_rows": n_rows, "unique_uid_count": len(overall_uid), "uid_overlap": 0,
        "raw_numeric_decode_passes_per_uid_inherited": 1, "current_raw_decode_count": 0,
        "cache_read_rows": n_rows, "factor_consumption_rows": 0,
        "class_counts": {name: class_manifests[name]["rows"] for name in CLASSES},
        "class_closures": {name: class_manifests[name]["closure_sha256"] for name in CLASSES},
        "per_action_node_class": per_group, "action_cycle_split": action_cycles,
        "vqf": {"implementation": "official VQF 2.1.2 updateBatchFullState", "action_boundary_resets": 0,
                "nodes": vqf_rows, "validation_role": "FRONTEND_CAUSALLY_SHARED_FACTOR_HELD_OUT_VALIDATION"},
        "forbidden": {"h_numeric_rows": 0, "combined_h_array_materialized": False, "p_rows": 0, "b1_rows": 0,
                      "opensense_rows": 0, "uwb_semantic_numeric_decode": 0, "uwb_measurement_array_materialization": 0,
                      "uwb_statistics_or_plot": 0, "uwb_factor_or_initializer_consumption": 0,
                      "uwb_influence_on_config_or_threshold": 0},
    }
    report["manifest_payload_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    atomic_json(output_root/"FRONTEND_AND_SPLIT_MANIFEST.json", report)
    return report


def load_class_cache(root: Path, class_name: str) -> dict[str, np.ndarray]:
    path = root/class_name.lower()
    manifest = json.loads((path/"CACHE_MANIFEST.json").read_text())
    if manifest["class"] != class_name or manifest.get("uwb_measurement_columns") != 0:
        raise RuntimeError(f"invalid class cache {class_name}")
    output = {}
    for name, info in manifest["columns"].items():
        source = path/f"{name}.npy"
        if sha256_file(source) != info["sha256"]:
            raise RuntimeError(f"class cache SHA mismatch: {source}")
        output[name] = np.load(source, mmap_mode="r")
    return output
