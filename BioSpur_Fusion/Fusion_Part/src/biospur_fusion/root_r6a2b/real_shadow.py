"""First bounded real ten-node IMU+UWB shadow.

The selector deliberately accesses only timestamps, statuses, boot epochs and
UWB valid masks.  Accelerometer, gyroscope and range payload fields are opened
only after ``REAL_WINDOW_SELECTION.json`` has been materialized.
"""
from __future__ import annotations

from collections import Counter
import csv
from dataclasses import replace
from itertools import permutations, product
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
from typing import Any, Iterable
import zipfile

import numpy as np

from biospur_fusion.imu.preintegration import (
    ImuSample,
    NativeTimePreintegrator,
    NoiseParameters,
    PreintegratorConfig,
)
from biospur_fusion.root_r6a0.body import KeyframeState, StaticCalibration
from biospur_fusion.root_r6a0.contracts import CalibrationSlot, CalibrationStatus
from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a2a.contracts import registry_from_sealed_addendum
from biospur_fusion.root_r6a2a.shadow import corrected_body_model
from biospur_fusion.root_r6a2a_r2.contracts import (
    EstimatorInput,
    EstimatorOptions,
    ScheduledObservation,
    StateLayout,
    UwbMeasurement,
)
from biospur_fusion.root_r6a2a_r2.estimator import RepairedShadowEstimator

NODES = (
    "BSFEC35", "BSFB165", "BSFAA61", "BSF1120", "BSF31CC",
    "BSFC2CC", "BSF44AD", "BSF3C79", "BSF6C53", "BSF8BC4",
)
IDENTITY = {
    "BSFEC35": "forearm_left",
    "BSFB165": "forearm_right",
    "BSFAA61": "upper_arm_left",
    "BSF1120": "upper_arm_right",
    "BSF31CC": "torso",
    "BSFC2CC": "pelvis",
    "BSF44AD": "thigh_left",
    "BSF3C79": "thigh_right",
    "BSF6C53": "shank_left",
    "BSF8BC4": "shank_right",
}
HELD_OUT_ACTIONS = {"walk", "final_still", "golf_swing", "boxing"}
SELECTION_SCHEMA = "biospur-root-r6a2b-real-window-selection-v1"
CHECKPOINT = "52e2896bb6437aa19710a6c0b6f54b4193f64e4a"
CAPTURE_REL = Path("logs/v47_ten_node_body_calibration_20260814_093601")
LEDGER_REL = CAPTURE_REL / "analysis_body_fusion_v2/TIME_EVENT_LEDGER.npz"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dump(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _stored_npy_memmap(npz_path: Path, member: str) -> tuple[np.memmap, dict[str, Any]]:
    """Map one ZIP_STORED NPY member without opening another member."""
    with zipfile.ZipFile(npz_path) as archive:
        info = archive.getinfo(member)
        if info.compress_type != zipfile.ZIP_STORED:
            raise RuntimeError(f"{member} is not ZIP_STORED")
    with npz_path.open("rb") as handle:
        handle.seek(info.header_offset)
        fields = struct.unpack("<IHHHHHIIIHH", handle.read(30))
        if fields[0] != 0x04034B50:
            raise RuntimeError("invalid ZIP local header")
        handle.seek(fields[-2] + fields[-1], os.SEEK_CUR)
        version = np.lib.format.read_magic(handle)
        if version == (1, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(handle)
        elif version == (2, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(handle)
        else:
            raise RuntimeError(f"unsupported NPY version {version}")
        offset = handle.tell()
    if fortran or len(shape) != 1:
        raise RuntimeError("ledger members must be one-dimensional C-order arrays")
    return np.memmap(npz_path, dtype=dtype, mode="r", offset=offset, shape=shape), {
        "member": member,
        "member_rows": int(shape[0]),
        "member_uncompressed_bytes": int(info.file_size),
        "zip_compression": "ZIP_STORED",
    }


def _lower_bound(rows: np.ndarray, value: int) -> int:
    low, high = 0, len(rows)
    while low < high:
        middle = (low + high) // 2
        if int(rows[middle]["global_time_ns"]) < value:
            low = middle + 1
        else:
            high = middle
    return low


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _action_intervals(capture: Path, formal_global_start_ns: int) -> list[dict[str, Any]]:
    formal = _load_json(capture / "FORMAL_T0.json")
    t0_mono = float(formal["formal_t0_monotonic"])
    events = [json.loads(line) for line in (capture / "ACTION_EVENTS.jsonl").read_text().splitlines() if line.strip()]
    rejected: set[tuple[str, int]] = set()
    for row in events:
        if row.get("event") == "ACTION_RETRY_REQUESTED" and not row.get("selected_for_scoring", True):
            rejected.add((str(row["action"]), int(row.get("attempt", 1))))
    starts: dict[tuple[str, int], dict[str, Any]] = {}
    intervals: list[dict[str, Any]] = []
    for row in events:
        if "action" not in row:
            continue
        key = (str(row["action"]), int(row.get("attempt", 1)))
        if row.get("event") == "ACTION_START":
            starts[key] = row
        elif row.get("event") == "ACTION_STOP" and key in starts and key not in rejected:
            start = starts.pop(key)
            start_ns = formal_global_start_ns + int(round((float(start["monotonic"]) - t0_mono) * 1e9))
            end_ns = formal_global_start_ns + int(round((float(row["monotonic"]) - t0_mono) * 1e9))
            intervals.append({
                "action": key[0],
                "attempt": key[1],
                "label_start_monotonic": float(start["monotonic"]),
                "label_end_monotonic": float(row["monotonic"]),
                "label_start_epoch": float(start["epoch"]),
                "label_end_epoch": float(row["epoch"]),
                "start_global_time_ns": start_ns,
                "end_global_time_ns": end_ns,
                "duration_s": (end_ns - start_ns) * 1e-9,
                "description": str(start.get("description", "")),
            })
    return intervals


def _slice_indices(rows: np.ndarray, start_ns: int, end_ns: int) -> tuple[int, int]:
    return _lower_bound(rows, start_ns), _lower_bound(rows, end_ns)


def _metadata_window_score(
    mapped: dict[str, dict[str, np.ndarray]], start_ns: int, end_ns: int
) -> dict[str, Any]:
    per_node: dict[str, Any] = {}
    total_imu = 0
    total_uwb_records = 0
    total_valid_scalars = 0
    multi_anchor_sweeps = 0
    fully_clean_nodes = 0
    for node in NODES:
        imu = mapped[node]["imu"]
        left, right = _slice_indices(imu, start_ns, end_ns)
        times = np.asarray(imu[left:right]["global_time_ns"], dtype=np.int64)
        statuses = np.asarray(imu[left:right]["status"], dtype=np.uint8)
        boots = np.asarray(imu[left:right]["boot_epoch"], dtype=np.uint16)
        dt = np.diff(times)
        accepted = int(np.sum(statuses == 1))
        gaps = int(np.sum(dt > 20_000_000))
        reversals = int(np.sum(dt <= 0))
        resets = max(0, len(np.unique(boots)) - 1)
        clean = (
            accepted >= 5_990 and accepted == len(times) and gaps == 0
            and reversals == 0 and resets == 0
        )
        fully_clean_nodes += int(clean)
        total_imu += accepted

        uwb = mapped[node]["uwb"]
        uleft, uright = _slice_indices(uwb, start_ns, end_ns)
        ustatus = np.asarray(uwb[uleft:uright]["status"], dtype=np.uint8)
        masks = np.asarray(uwb[uleft:uright]["valid_mask"], dtype=np.uint8)
        accepted_masks = masks[ustatus == 1]
        popcounts = np.array([int(value).bit_count() for value in accepted_masks], dtype=int)
        valid_scalars = int(np.sum(popcounts[popcounts >= 4]))
        multi = int(np.sum(popcounts >= 4))
        total_uwb_records += int(np.sum(ustatus == 1))
        total_valid_scalars += valid_scalars
        multi_anchor_sweeps += multi
        per_node[node] = {
            "segment": IDENTITY[node],
            "imu_index_start": left,
            "imu_index_stop": right,
            "imu_rows": int(len(times)),
            "imu_accepted_rows": accepted,
            "imu_gap_over_20ms_count": gaps,
            "imu_nonpositive_dt_count": reversals,
            "imu_boot_epoch_transition_count": resets,
            "imu_clean_30s": clean,
            "uwb_index_start": uleft,
            "uwb_index_stop": uright,
            "uwb_accepted_sweeps": int(np.sum(ustatus == 1)),
            "uwb_multi_anchor_sweeps": multi,
            "uwb_valid_scalar_observations_in_multi_anchor_sweeps": valid_scalars,
        }
    return {
        "start_global_time_ns": start_ns,
        "end_global_time_ns_exclusive": end_ns,
        "duration_s": (end_ns - start_ns) * 1e-9,
        "complete_ten_node_imu_coverage": fully_clean_nodes == 10,
        "clean_imu_node_count": fully_clean_nodes,
        "imu_accepted_rows": total_imu,
        "uwb_accepted_sweeps": total_uwb_records,
        "uwb_multi_anchor_sweeps": multi_anchor_sweeps,
        "valid_multi_anchor_uwb_observations": total_valid_scalars,
        "per_node": per_node,
    }


def _candidate_windows(interval: dict[str, Any], duration_s: int = 30) -> Iterable[tuple[int, int]]:
    first = int(interval["start_global_time_ns"]) + 1_000_000_000
    last_start = int(interval["end_global_time_ns"]) - (duration_s + 1) * 1_000_000_000
    start = first
    while start <= last_start:
        yield start, start + duration_s * 1_000_000_000
        start += 1_000_000_000


def select_real_windows(fusion: Path, result_dir: Path) -> Path:
    """Freeze A/B using only label, time, status, boot and valid-mask fields."""
    fusion = Path(fusion)
    result_dir = Path(result_dir)
    capture = fusion / CAPTURE_REL
    ledger = fusion / LEDGER_REL
    event_accounting = _load_json(capture / "analysis_body_fusion_v2/EVENT_ACCOUNTING.json")
    formal_start = int(event_accounting["formal_global_start_ns"])
    intervals = _action_intervals(capture, formal_start)
    mapped: dict[str, dict[str, np.ndarray]] = {}
    member_metadata: dict[str, Any] = {}
    for node in NODES:
        imu, imu_meta = _stored_npy_memmap(ledger, f"imu_{node}.npy")
        uwb, uwb_meta = _stored_npy_memmap(ledger, f"uwb_{node}.npy")
        mapped[node] = {"imu": imu, "uwb": uwb}
        member_metadata[node] = {"imu": imu_meta, "uwb": uwb_meta}

    initial = next(row for row in intervals if row["action"] == "initial_still" and row["attempt"] == 1)
    candidates_a = []
    for start_ns, end_ns in _candidate_windows(initial):
        score = _metadata_window_score(mapped, start_ns, end_ns)
        score.update({"label": "initial_still", "attempt": 1})
        candidates_a.append(score)
    if not candidates_a:
        raise RuntimeError("no interior 30-second initial-still candidate")
    ranked_a = sorted(
        candidates_a,
        key=lambda row: (
            -int(row["complete_ten_node_imu_coverage"]),
            -row["clean_imu_node_count"],
            -row["valid_multi_anchor_uwb_observations"],
            row["start_global_time_ns"],
        ),
    )

    eligible_intervals = [
        row for row in intervals
        if row["action"] not in HELD_OUT_ACTIONS | {"initial_still", "t_pose"}
        and row["duration_s"] >= 32.0
    ]
    candidates_b = []
    for interval in eligible_intervals:
        for start_ns, end_ns in _candidate_windows(interval):
            score = _metadata_window_score(mapped, start_ns, end_ns)
            score.update({"label": interval["action"], "attempt": interval["attempt"]})
            candidates_b.append(score)
    if not candidates_b:
        raise RuntimeError("no eligible 30-second development-motion candidate")
    ranked_b = sorted(
        candidates_b,
        key=lambda row: (
            -int(row["complete_ten_node_imu_coverage"]),
            -row["clean_imu_node_count"],
            -row["valid_multi_anchor_uwb_observations"],
            row["start_global_time_ns"],
        ),
    )
    selected_a = ranked_a[0]
    selected_b = ranked_b[0]
    interval_map = {(row["action"], row["attempt"]): row for row in intervals}
    for selected in (selected_a, selected_b):
        selected["label_interval"] = interval_map[(selected["label"], selected["attempt"])]
        selected["selection_payload_fields_opened"] = []
        selected["selection_index_fields_accessed"] = [
            "global_time_ns", "status", "boot_epoch", "valid_mask",
        ]

    output = {
        "schema": SELECTION_SCHEMA,
        "checkpoint": CHECKPOINT,
        "capture": {
            "path": str(capture),
            "raw_sha256": _load_json(capture / "CAPTURE_COMPLETE.json")["raw_sha256"],
            "typed_ledger_path": str(ledger),
            "typed_ledger_sha256": _sha256(ledger),
            "formal_global_start_ns": formal_start,
        },
        "policy": {
            "candidate_duration_s": 30,
            "action_boundary_exclusion_s_each_side": 1,
            "candidate_stride_s": 1,
            "window_a_ranking": [
                "complete ten-node IMU coverage", "clean node count",
                "valid multi-anchor UWB scalar count", "earliest timestamp",
            ],
            "window_b_ranking": [
                "complete ten-node IMU coverage", "clean node count",
                "maximum valid multi-anchor UWB scalar count", "earliest timestamp",
            ],
            "window_b_exclusions": sorted(HELD_OUT_ACTIONS | {"initial_still", "t_pose"}),
            "sample_values_used_for_selection": False,
            "visual_estimator_quality_used_for_selection": False,
        },
        "metadata_only_access": {
            "labels": str(capture / "ACTION_EVENTS.jsonl"),
            "fields": ["global_time_ns", "status", "boot_epoch", "valid_mask"],
            "forbidden_fields_not_accessed": ["acc_raw", "gyro_raw", "range_mm", "quality_percent", "t_round_us"],
            "mapped_members": member_metadata,
        },
        "eligible_label_intervals": eligible_intervals,
        "window_a": selected_a,
        "window_b": selected_b,
        "candidate_accounting": {
            "window_a_candidate_count": len(candidates_a),
            "window_b_candidate_count": len(candidates_b),
            "window_b_candidates_by_label": dict(Counter(row["label"] for row in candidates_b)),
            "window_a_top_five": [
                {key: row[key] for key in (
                    "label", "attempt", "start_global_time_ns", "end_global_time_ns_exclusive",
                    "clean_imu_node_count", "imu_accepted_rows", "valid_multi_anchor_uwb_observations",
                )} for row in ranked_a[:5]
            ],
            "window_b_top_ten": [
                {key: row[key] for key in (
                    "label", "attempt", "start_global_time_ns", "end_global_time_ns_exclusive",
                    "clean_imu_node_count", "imu_accepted_rows", "valid_multi_anchor_uwb_observations",
                )} for row in ranked_b[:10]
            ],
        },
        "held_out_payload_opened": False,
        "real_measurement_payload_opened": False,
    }
    path = result_dir / "REAL_WINDOW_SELECTION.json"
    _dump(path, output)
    for streams in mapped.values():
        del streams["imu"]
        del streams["uwb"]
    return path


def _load_selected_payloads(
    fusion: Path, selection: dict[str, Any]
) -> tuple[dict[str, dict[str, dict[str, np.ndarray]]], dict[str, Any]]:
    """Copy exactly the two frozen slices; no row outside them is copied."""
    ledger = fusion / LEDGER_REL
    windows: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    audit: dict[str, Any] = {
        "typed_ledger_path": str(ledger),
        "typed_ledger_sha256": selection["capture"]["typed_ledger_sha256"],
        "held_out_opened": False,
        "opened_windows": {},
        "member_access": [],
    }
    for window_name, key in (("A", "window_a"), ("B", "window_b")):
        selected = selection[key]
        start_ns = int(selected["start_global_time_ns"])
        end_ns = int(selected["end_global_time_ns_exclusive"])
        windows[window_name] = {}
        audit["opened_windows"][window_name] = {
            "label": selected["label"], "attempt": selected["attempt"],
            "start_global_time_ns": start_ns,
            "end_global_time_ns_exclusive": end_ns,
        }
        for node in NODES:
            windows[window_name][node] = {}
            for modality in ("imu", "uwb"):
                member = f"{modality}_{node}.npy"
                mapped, metadata = _stored_npy_memmap(ledger, member)
                left, right = _slice_indices(mapped, start_ns, end_ns)
                rows = np.array(mapped[left:right], copy=True)
                del mapped
                if len(rows) and (
                    int(rows[0]["global_time_ns"]) < start_ns
                    or int(rows[-1]["global_time_ns"]) >= end_ns
                ):
                    raise RuntimeError(f"{window_name}/{member}: selection escape")
                windows[window_name][node][modality] = rows
                audit["member_access"].append({
                    "window": window_name, "node_id": node, "modality": modality,
                    "selected_index_start": left, "selected_index_stop": right,
                    "selected_rows": int(len(rows)), **metadata,
                })
    return windows, audit


def _proper_signed_permutations() -> list[tuple[str, np.ndarray]]:
    rows = []
    eye = np.eye(3, dtype=int)
    axes = "XYZ"
    for order in permutations(range(3)):
        for signs in product((-1, 1), repeat=3):
            matrix = np.diag(signs) @ eye[list(order)]
            if round(float(np.linalg.det(matrix))) != 1:
                continue
            label = ",".join(f"{('+' if signs[i] > 0 else '-')}{axes[order[i]]}" for i in range(3))
            rows.append((label, matrix.astype(float)))
    return rows


def _window_input_diagnostics(
    windows: dict[str, dict[str, dict[str, np.ndarray]]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    diagnostics: dict[str, Any] = {"schema": "biospur-root-r6a2b-selected-real-input-audit-v1", "windows": {}}
    mean_vectors: dict[str, np.ndarray] = {}
    for window_name, streams in windows.items():
        per_node: dict[str, Any] = {}
        total_imu = total_uwb = total_valid = 0
        for node, modalities in streams.items():
            imu = modalities["imu"]
            accepted = imu[imu["status"] == 1]
            acc = accepted["acc_raw"].astype(float) / 2048.0 * 9.80665
            gyro = np.deg2rad(accepted["gyro_raw"].astype(float) / 16.384)
            times = accepted["global_time_ns"].astype(np.int64)
            dt = np.diff(times)
            mean_acc = np.mean(acc, axis=0)
            mean_gyro = np.mean(gyro, axis=0)
            if window_name == "A":
                mean_vectors[node] = mean_acc
            norm = float(np.linalg.norm(mean_acc))
            cosine = float(np.clip(mean_acc[1] / max(norm, 1e-12), -1.0, 1.0))
            uwb = modalities["uwb"]
            accepted_uwb = uwb[uwb["status"] == 1]
            masks = accepted_uwb["valid_mask"].astype(np.uint8)
            valid_counts = np.array([int(value).bit_count() for value in masks], dtype=int)
            scalar_count = int(np.sum(valid_counts))
            per_node[node] = {
                "segment": IDENTITY[node],
                "imu": {
                    "rows": int(len(imu)), "accepted_rows": int(len(accepted)),
                    "mean_specific_force_device_mps2": mean_acc.tolist(),
                    "mean_specific_force_norm_mps2": norm,
                    "norm_error_from_9_80665_mps2": norm - 9.80665,
                    "angle_to_expected_device_plus_y_deg": float(np.degrees(np.arccos(cosine))),
                    "gyro_mean_rad_s": mean_gyro.tolist(),
                    "gyro_axis_std_rad_s": np.std(gyro, axis=0, ddof=1).tolist(),
                    "gyro_norm_p95_rad_s": float(np.percentile(np.linalg.norm(gyro, axis=1), 95)),
                    "gyro_norm_p99_rad_s": float(np.percentile(np.linalg.norm(gyro, axis=1), 99)),
                    "raw_saturation_count": int(
                        np.sum((accepted["acc_raw"] == -32768) | (accepted["acc_raw"] == 32767))
                        + np.sum((accepted["gyro_raw"] == -32768) | (accepted["gyro_raw"] == 32767))
                    ),
                    "gap_over_7_5ms_count": int(np.sum(dt > 7_500_000)),
                    "gap_over_20ms_count": int(np.sum(dt > 20_000_000)),
                    "time_reversal_or_duplicate_count": int(np.sum(dt <= 0)),
                    "boot_epochs": [int(value) for value in np.unique(accepted["boot_epoch"])],
                    "reset_count": max(0, len(np.unique(accepted["boot_epoch"])) - 1),
                    "first_global_time_ns": int(times[0]), "last_global_time_ns": int(times[-1]),
                },
                "uwb": {
                    "rows": int(len(uwb)), "accepted_sweeps": int(len(accepted_uwb)),
                    "valid_scalar_observations": scalar_count,
                    "multi_anchor_sweeps": int(np.sum(valid_counts >= 4)),
                    "valid_anchors_per_sweep": {
                        "minimum": int(np.min(valid_counts)),
                        "median": float(np.median(valid_counts)),
                        "maximum": int(np.max(valid_counts)),
                    },
                    "quality_percent": {
                        "minimum_valid": int(np.min(accepted_uwb["quality_percent"][
                            np.unpackbits(masks[:, None], axis=1, bitorder="little")[:, :8].astype(bool)
                        ])),
                        "median_valid": float(np.median(accepted_uwb["quality_percent"][
                            np.unpackbits(masks[:, None], axis=1, bitorder="little")[:, :8].astype(bool)
                        ])),
                    },
                },
            }
            total_imu += len(accepted)
            total_uwb += len(accepted_uwb)
            total_valid += scalar_count
        diagnostics["windows"][window_name] = {
            "per_node": per_node,
            "totals": {
                "accepted_imu_rows": int(total_imu),
                "accepted_uwb_sweeps": int(total_uwb),
                "valid_uwb_scalar_observations": int(total_valid),
            },
        }

    hypotheses = _proper_signed_permutations()
    common = [node for node in NODES if node != "BSF31CC"]
    bilateral_pairs = (
        ("BSFEC35", "BSFB165"), ("BSFAA61", "BSF1120"),
        ("BSF44AD", "BSF3C79"), ("BSF6C53", "BSF8BC4"),
    )
    normalized_means = {node: mean_vectors[node] / np.linalg.norm(mean_vectors[node]) for node in NODES}
    bilateral_angles = [
        float(np.degrees(np.arccos(np.clip(normalized_means[left] @ normalized_means[right], -1.0, 1.0))))
        for left, right in bilateral_pairs
    ]
    cross_node_coherence = float(np.mean([
        normalized_means[left] @ normalized_means[right]
        for index, left in enumerate(common) for right in common[index + 1:]
    ]))
    scores = []
    for label, matrix in hypotheses:
        transformed = {
            node: matrix @ (mean_vectors[node] / np.linalg.norm(mean_vectors[node]))
            for node in common
        }
        angles = [float(np.degrees(np.arccos(np.clip(value[1], -1.0, 1.0)))) for value in transformed.values()]
        scores.append({
            "hypothesis": label, "matrix_register_to_device": matrix.tolist(),
            "mean_gravity_angle_to_plus_y_deg": float(np.mean(angles)),
            "max_gravity_angle_to_plus_y_deg": float(np.max(angles)),
            "rms_gravity_angle_to_plus_y_deg": float(np.sqrt(np.mean(np.square(angles)))),
            "bilateral_pair_specific_force_angles_deg": bilateral_angles,
            "cross_node_mean_pairwise_cosine": cross_node_coherence,
            "joint_closure_score": "NON_DISCRIMINATING_FIXED_FK_EXACT_FOR_ALL_HYPOTHESES",
        })
    scores.sort(key=lambda row: (row["rms_gravity_angle_to_plus_y_deg"], row["hypothesis"]))
    identity = next(row for row in scores if row["matrix_register_to_device"] == np.eye(3).tolist())
    best = scores[0]
    select_best = (
        identity["max_gravity_angle_to_plus_y_deg"] > 35.0
        and identity["rms_gravity_angle_to_plus_y_deg"] - best["rms_gravity_angle_to_plus_y_deg"] > 20.0
    )
    selected_common = best if select_best else identity

    special_scores = []
    vector = mean_vectors["BSF31CC"] / np.linalg.norm(mean_vectors["BSF31CC"])
    for label, matrix in hypotheses:
        transformed = matrix @ vector
        special_scores.append({
            "hypothesis": label, "matrix_register_to_device": matrix.tolist(),
            "gravity_angle_to_plus_y_deg": float(np.degrees(np.arccos(np.clip(transformed[1], -1.0, 1.0)))),
        })
    special_scores.sort(key=lambda row: (row["gravity_angle_to_plus_y_deg"], row["hypothesis"]))
    special_identity = next(row for row in special_scores if row["matrix_register_to_device"] == np.eye(3).tolist())
    special_best = special_scores[0]
    special_select_best = (
        special_identity["gravity_angle_to_plus_y_deg"] > 35.0
        and special_identity["gravity_angle_to_plus_y_deg"] - special_best["gravity_angle_to_plus_y_deg"] > 20.0
    )
    selected_special = special_best if special_select_best else special_identity
    frame_audit = {
        "schema": "biospur-root-r6a2b-frame-hypothesis-audit-v1",
        "hypothesis_inventory": "24 proper signed permutations; det=+1",
        "truth_firewall": "action labels and expected final poses were not used as orientation truth",
        "sealed_expected_relation": "stationary specific force approximately device +Y",
        "selection_rule": "retain identity unless max identity contradiction exceeds 35 deg and RMS improvement exceeds 20 deg",
        "score_interpretation": {
            "gravity_consistency": "discriminates signed permutations",
            "bilateral_symmetry": "reported but invariant under one shared common-nine rotation",
            "cross_node_coherence": "reported but invariant under one shared common-nine rotation",
            "joint_closure": "exact by fixed FK for every tested hypothesis and therefore non-discriminating",
        },
        "common_nine": {
            "identity": identity, "best": best, "selected": selected_common,
            "top_hypotheses": scores[:8],
            "unresolved_about_gravity_yaw": True,
        },
        "BSF31CC": {
            "identity": special_identity, "best": special_best, "selected": selected_special,
            "top_hypotheses": special_scores[:8],
            "unresolved_about_gravity_yaw": True,
        },
        "session_only_not_production_calibration": True,
    }
    return diagnostics, frame_audit


def _quiet_initialization_slice(
    window_a: dict[str, dict[str, np.ndarray]], start_ns: int, end_ns: int
) -> dict[str, Any]:
    """Select one 1 s nuisance-initialization slice inside already-open Window A."""
    candidates = []
    current = start_ns
    while current + 1_000_000_000 <= end_ns:
        node_scores = []
        node_rows = {}
        for node in NODES:
            rows = window_a[node]["imu"]
            left, right = _slice_indices(rows, current, current + 1_000_000_000)
            chosen = rows[left:right]
            accepted = chosen[chosen["status"] == 1]
            gyro = np.deg2rad(accepted["gyro_raw"].astype(float) / 16.384)
            acc = accepted["acc_raw"].astype(float) / 2048.0 * 9.80665
            node_scores.append(float(np.median(np.linalg.norm(gyro, axis=1))))
            node_rows[node] = int(len(accepted))
            node_scores.append(0.15 * float(np.median(np.abs(np.linalg.norm(acc, axis=1) - 9.80665))))
        candidates.append({
            "start_global_time_ns": current,
            "end_global_time_ns_exclusive": current + 1_000_000_000,
            "score": float(np.median(node_scores)),
            "accepted_rows_by_node": node_rows,
        })
        current += 1_000_000_000
    chosen = min(candidates, key=lambda row: (row["score"], row["start_global_time_ns"]))
    chosen["selection_scope"] = "inside already-frozen Window A only"
    chosen["role"] = "session-local dynamic-state and bias initialization; not a calibration slot"
    chosen["candidate_count"] = len(candidates)
    return chosen


def _mean_imu_vectors(
    window: dict[str, dict[str, np.ndarray]], start_ns: int, end_ns: int,
    frame_audit: dict[str, Any],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray]]:
    means_acc: dict[str, np.ndarray] = {}
    means_gyro: dict[str, np.ndarray] = {}
    matrices: dict[str, np.ndarray] = {}
    common_matrix = np.asarray(frame_audit["common_nine"]["selected"]["matrix_register_to_device"], float)
    special_matrix = np.asarray(frame_audit["BSF31CC"]["selected"]["matrix_register_to_device"], float)
    for node in NODES:
        rows = window[node]["imu"]
        left, right = _slice_indices(rows, start_ns, end_ns)
        accepted = rows[left:right]
        accepted = accepted[accepted["status"] == 1]
        matrix = special_matrix if node == "BSF31CC" else common_matrix
        acc = (matrix @ (accepted["acc_raw"].astype(float) / 2048.0 * 9.80665).T).T
        gyro = (matrix @ np.deg2rad(accepted["gyro_raw"].astype(float) / 16.384).T).T
        means_acc[node] = np.mean(acc, axis=0)
        means_gyro[node] = np.mean(gyro, axis=0)
        matrices[node] = matrix
    return means_acc, means_gyro, matrices


def _orientation_from_gravity_and_skin(node: str, specific_force: np.ndarray) -> np.ndarray:
    """Map measured specific force to up and apply the declared yaw gauge."""
    y_sensor = np.asarray(specific_force, float)
    y_sensor /= np.linalg.norm(y_sensor)
    z_axis = np.array([0.0, 0.0, 1.0])
    z_sensor = z_axis - y_sensor * float(y_sensor @ z_axis)
    if np.linalg.norm(z_sensor) < 0.15:
        x_axis = np.array([1.0, 0.0, 0.0])
        z_sensor = x_axis - y_sensor * float(y_sensor @ x_axis)
    z_sensor /= np.linalg.norm(z_sensor)
    x_sensor = np.cross(y_sensor, z_sensor)
    x_sensor /= np.linalg.norm(x_sensor)
    source = np.column_stack((x_sensor, y_sensor, z_sensor))

    if IDENTITY[node] in {"torso", "pelvis", "thigh_left", "thigh_right", "shank_left", "shank_right"}:
        z_world = np.array([0.0, -1.0, 0.0])
    elif IDENTITY[node].endswith("left"):
        z_world = np.array([1.0, 0.0, 0.0])
    else:
        z_world = np.array([-1.0, 0.0, 0.0])
    y_world = np.array([0.0, 0.0, 1.0])
    x_world = np.cross(y_world, z_world)
    x_world /= np.linalg.norm(x_world)
    target = np.column_stack((x_world, y_world, z_world))
    rotation = target @ source.T
    if np.linalg.det(rotation) < 0.999999:
        raise RuntimeError("orientation construction lost handedness")
    return rotation


def _slot(
    slot_id: str, kind: str, owner: str, value: Iterable[float], covariance: np.ndarray,
    provenance: str,
) -> CalibrationSlot:
    return CalibrationSlot(
        slot_id=slot_id, kind=kind, owner_id=owner,
        status=CalibrationStatus.VERIFIED_INPUT,
        value=tuple(float(item) for item in value),
        covariance=tuple(tuple(float(item) for item in row) for row in np.asarray(covariance, float)),
        provenance=provenance, fitted_from_c1=False,
    )


def _build_session_calibration(
    fusion: Path, model, desired_rotations: dict[str, np.ndarray]
) -> tuple[StaticCalibration, dict[str, Any], dict[str, NoiseParameters]]:
    """Build isolated executable priors without changing the immutable 87 slots."""
    slots: dict[str, CalibrationSlot] = {}
    profile_rows: dict[str, Any] = {}

    def add(slot_id: str, kind: str, owner: str, value: Iterable[float], sigma: Iterable[float], provenance: str) -> None:
        values = tuple(float(item) for item in value)
        sigmas = np.asarray(tuple(float(item) for item in sigma), float)
        slots[slot_id] = _slot(slot_id, kind, owner, values, np.diag(sigmas**2), provenance)
        profile_rows[slot_id] = {
            "nominal_value": list(values), "one_sigma": sigmas.tolist(),
            "provenance": provenance, "ownership": owner,
            "registry_write": False, "session_local": True,
        }

    add("world_model_gauge", "world_model_gauge", "session_gauge", np.zeros(6),
        [np.pi, np.pi, np.pi, 10.0, 10.0, 10.0],
        "SESSION_GAUGE_T_N_V4_IDENTITY;V4_ORIGIN_AND_YAW_NOT_ACCURACY_CLAIMS")

    geometry = _load_json(fusion.parent / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json")
    for anchor in geometry["anchors"]:
        anchor_id = int(anchor["id"])
        position = [anchor["x_mm"] / 1000.0, anchor["y_mm"] / 1000.0, anchor["z_mm"] / 1000.0]
        add(f"anchor_position:{anchor_id}", "anchor_position", f"anchor:{anchor_id}", position,
            [0.08, 0.08, 0.10], "CAPTURE_BOUND_V4IO_LAYOUT_RELATIVE_GEOMETRY")
        add(f"anchor_delay:{anchor_id}", "anchor_delay", f"anchor:{anchor_id}",
            [anchor["d_anchor_mm"] / 1000.0], [0.03], "CAPTURE_BOUND_V4IO_DELAY_LEDGER")

    world_contract = _load_json(
        fusion / "logs/root_r6a1c_deferred_measurement_bridge_20260825T102823Z/WORLD_FRAME_BRIDGE_CONTRACT.json"
    )
    for node, clock in world_contract["clock_relationships"]["models"].items():
        add(f"time_relationship:{node}", "time_relationship", node,
            [clock["a_ns_per_us"], clock["b_ns"]], [0.1, clock["sigma_ns"]],
            "CAPTURE_BOUND_R6A1C_CLOCKMODEL")

    joint_geometry = {
        "pelvis_torso": ([0.0, 0.18, 0.0], [0.0, -0.18, 0.0]),
        "shoulder_left": ([-0.20, 0.15, 0.0], [0.0, 0.15, 0.0]),
        "elbow_left": ([0.0, -0.15, 0.0], [0.0, 0.125, 0.0]),
        "shoulder_right": ([0.20, 0.15, 0.0], [0.0, 0.15, 0.0]),
        "elbow_right": ([0.0, -0.15, 0.0], [0.0, 0.125, 0.0]),
        "hip_left": ([-0.16, 0.0, 0.0], [0.0, 0.21, 0.0]),
        "knee_left": ([0.0, -0.21, 0.0], [0.0, 0.21, 0.0]),
        "hip_right": ([0.16, 0.0, 0.0], [0.0, 0.21, 0.0]),
        "knee_right": ([0.0, -0.21, 0.0], [0.0, 0.21, 0.0]),
    }
    for joint in model.joints:
        parent, child = joint_geometry[joint.joint_id]
        add(joint.parent_offset_slot, "joint_parent", joint.joint_id, parent, [0.06] * 3,
            "BROAD_PHYSIOLOGICAL_SESSION_PRIOR_FIXED_DURING_SOLVE")
        add(joint.child_offset_slot, "joint_child", joint.joint_id, child, [0.06] * 3,
            "BROAD_PHYSIOLOGICAL_SESSION_PRIOR_FIXED_DURING_SOLVE")
        add(joint.rest_rotation_slot, "joint_rest", joint.joint_id, np.zeros(3), [0.35] * 3,
            "BROAD_NEUTRAL_RELATIVE_ROTATION_PRIOR;DYNAMIC_INITIAL_STATE_CARRIES_OBSERVED_RELATIVE_POSE")

    lengths = {
        "upper_arm_left": 0.30, "upper_arm_right": 0.30,
        "forearm_left": 0.25, "forearm_right": 0.25,
        "thigh_left": 0.42, "thigh_right": 0.42,
        "shank_left": 0.42, "shank_right": 0.42,
    }
    for segment, length in lengths.items():
        add(f"bone_length:{segment}", "bone_length", segment, [length], [0.08],
            "BROAD_PHYSIOLOGICAL_SESSION_PRIOR_FIXED_DURING_SOLVE")

    for node in NODES:
        add(f"imu_extrinsic:{node}", "imu_extrinsic", node, np.zeros(6),
            [0.20, 0.20, 0.35, 0.05, 0.05, 0.05],
            "SESSION_SEGMENT_FRAME_CODEFINED_WITH_DEVICE_NEUTRAL_PRIOR;SIGNED_DONNING_UNCERTAIN")
        if node == "BSF31CC":
            lever = [0.018542037084074, 0.0, 0.001538354076708]
            provenance = "BSF31CC_V0_20_N5BL_U7_COMPONENT_REFERENCE_TO_U4_REFERENCE;RF_PHASE_CENTRE_BOUNDED"
            sigma = [0.035, 0.025, 0.015]
        else:
            lever = [-0.003580048260097, 0.000005227330454661, 0.001538354076708]
            provenance = "COMMON_NINE_V0_20_PCB17_U7_COMPONENT_REFERENCE_TO_U4_REFERENCE;RF_PHASE_CENTRE_BOUNDED"
            sigma = [0.030, 0.025, 0.015]
        add(f"tag_lever:{node}", "tag_lever", node, lever, sigma, provenance)

    points = {
        "wrist_left": [0.0, -0.125, 0.0], "wrist_right": [0.0, -0.125, 0.0],
        "ankle_left": [0.0, -0.21, 0.0], "ankle_right": [0.0, -0.21, 0.0],
    }
    for name, value in points.items():
        add(f"anatomical_point:{name}", "anatomical_point", name, value, [0.05] * 3,
            "DERIVED_FROM_FIXED_BROAD_SEGMENT_LENGTH_PRIOR")
    torso_top = 0.5 * (
        np.asarray(joint_geometry["shoulder_left"][0])
        + np.asarray(joint_geometry["shoulder_right"][0])
    )
    add("anatomical_point:torso_top", "anatomical_point", "torso", torso_top, [0.06] * 3,
        "R6A1C_DERIVED_SHOULDER_CENTRE_MIDPOINT_ZERO_INDEPENDENT_FREEDOM")

    immutable = _load_json(
        fusion / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json"
    )
    immutable_ids = {row["slot_id"] for row in immutable["slots"]}
    if set(slots) != immutable_ids:
        raise RuntimeError(f"session profile slot inventory mismatch: missing={immutable_ids-set(slots)} extra={set(slots)-immutable_ids}")

    noise_audit = _load_json(
        fusion / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/NOISE_PROVENANCE_AUDIT.json"
    )
    qualified_acc = []
    qualified_gyro = []
    for axis in noise_audit["per_axis"].values():
        fit = axis["white_fit"]
        if fit["qualified"] and fit["density"] is not None:
            (qualified_acc if fit["density_units"].startswith("m/s") else qualified_gyro).append(float(fit["density"]))
    fallback_acc = max(qualified_acc) * 2.0
    fallback_gyro = max(qualified_gyro) * 2.0
    noises: dict[str, NoiseParameters] = {}
    noise_profile = {}
    for node in NODES:
        row = noise_audit["per_node"][node]
        acc = row["accelerometer_white_noise_density_mps2_sqrt_hz"]
        gyro = row["gyroscope_white_noise_density_rad_s_sqrt_hz"]
        acc_value = float(acc) if acc is not None else fallback_acc
        gyro_value = float(gyro) if gyro is not None else fallback_gyro
        noises[node] = NoiseParameters(
            acc_value, gyro_value, 0.010, 0.001,
            "R6A2B_SESSION_BOUND_FROM_R6A1A_WHITE_NOISE_PLUS_CONSERVATIVE_UNQUALIFIED_BIAS_RW",
        )
        noise_profile[node] = {
            "accelerometer_white_noise_density_mps2_sqrt_hz": acc_value,
            "gyroscope_white_noise_density_rad_s_sqrt_hz": gyro_value,
            "accelerometer_bias_random_walk_bound_mps2_s_sqrt_s": 0.010,
            "gyroscope_bias_random_walk_bound_rad_s2_sqrt_s": 0.001,
            "white_source": "per-node qualified scalar" if acc is not None and gyro is not None else "two-times maximum qualified-axis fallback",
            "bias_process_status": "BOUNDED_SESSION_PRIOR_NOT_PRODUCTION_QUALIFIED",
        }

    profile = {
        "schema": "biospur-root-r6a2b-session-local-real-profile-v1",
        "scope": "READ_ONLY_BOUNDED_C1_SHADOW_ONLY",
        "immutable_87_slot_registry": {
            "path": str(fusion / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json"),
            "sha256": _sha256(fusion / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json"),
            "writes": 0, "thawed": 0,
        },
        "signed_axes_and_donning": {
            "device_plus_z": "toward skin", "device_minus_z": "outward",
            "device_minus_y_neutral": "approximately groundward/distal-or-caudal",
            "stationary_specific_force_expected": "approximately device +Y",
            "scope": "session-specific; not permanent motion constraint",
        },
        "node_to_segment_extrinsic_priors": {
            "definition": "segment frames co-defined with device neutral priors; executable nominal identity with finite rotation/translation uncertainty",
            "rows": {node: profile_rows[f"imu_extrinsic:{node}"] for node in NODES},
        },
        "mechanical_levers": {
            "COMMON_NINE_V0_20_PCB17": profile_rows["tag_lever:BSFEC35"],
            "BSF31CC_V0_20_N5BL": profile_rows["tag_lever:BSF31CC"],
            "cross_family_reuse": False,
            "rf_phase_centre": "nominal component-reference lever plus bounded, unqualified RF offset",
            "BSF31CC_6mm_measurement": "band/contact to PCB-bottom only; not used as skin-to-IMU-die lever",
        },
        "subject_geometry": {
            "bone_lengths": {name: profile_rows[f"bone_length:{name}"] for name in lengths},
            "joint_centres": {key: value for key, value in profile_rows.items() if key.startswith(("joint_parent:", "joint_child:"))},
            "joint_rest": {key: value for key, value in profile_rows.items() if key.startswith("joint_rest:")},
            "fixed_during_each_solve": True,
        },
        "skin_slip_nuisance": {
            "orientation_bound_rad": 0.20, "smooth_time_constant_s": 0.5,
            "handling": "ambiguous innovation enters node/model health and local downweighting; no unconstrained per-sample correction",
            "explicit_nuisance_state_supported_by_qualified_R2": False,
        },
        "anchors_and_delays": {key: value for key, value in profile_rows.items() if key.startswith(("anchor_position:", "anchor_delay:"))},
        "clock_models": {key: value for key, value in profile_rows.items() if key.startswith("time_relationship:")},
        "session_gauge": profile_rows["world_model_gauge"],
        "imu_noise_and_bias_process_bounds": noise_profile,
        "torso_top": {
            **profile_rows["anatomical_point:torso_top"],
            "equation": "0.5*(p_shoulder_left+p_shoulder_right)",
            "independent_optimizer_freedom": 0,
        },
        "executable_slot_view": profile_rows,
        "real_calibration_authority": False,
        "production_fusion_authorized": False,
    }
    return StaticCalibration(slots), profile, noises


def _initial_state(
    model, calibration: StaticCalibration, rotations: dict[str, np.ndarray],
    means_acc: dict[str, np.ndarray], means_gyro: dict[str, np.ndarray],
) -> KeyframeState:
    layout = StateLayout(model.joint_ids, model.imu_ids)
    by_segment = {IDENTITY[node]: rotations[node] for node in NODES}
    by_child = {joint.child: joint for joint in model.joints}
    joints = {}
    for joint in model.joints:
        relative = by_segment[joint.parent].T @ by_segment[joint.child]
        joints[joint.joint_id] = so3_log(relative)
    covariance = np.zeros((layout.dimension, layout.dimension))
    for block, sigma in (
        (slice(0, 3), 0.35), (slice(3, 6), 0.20), (slice(6, 9), 0.30),
        (slice(9, 36), 0.30), (slice(36, 63), 0.60),
        (slice(63, 93), 0.06), (slice(93, 123), 0.60),
    ):
        covariance[block, block] = np.eye(block.stop - block.start) * sigma**2
    accel_bias = {}
    for node in NODES:
        expected = rotations[node].T @ np.array([0.0, 0.0, 9.80665])
        accel_bias[node] = np.clip(means_acc[node] - expected, -1.5, 1.5)
    state = KeyframeState(
        time_s=0.0,
        root_translation_model_m=np.zeros(3),
        root_rotation_model_rotvec=so3_log(by_segment["pelvis"]),
        root_velocity_model_mps=np.zeros(3),
        joint_rotvec=joints,
        joint_rate_rad_s={joint: np.zeros(3) for joint in model.joint_ids},
        gyro_bias_rad_s={node: means_gyro[node].copy() for node in model.imu_ids},
        accel_bias_mps2=accel_bias,
        covariance=covariance,
    )
    state.validate(model)
    return state


def _flatten_uwb(
    streams: dict[str, dict[str, np.ndarray]], start_ns: int, end_ns: int,
    sigma_by_link: dict[str, float], window_name: str,
) -> tuple[list[UwbMeasurement], dict[str, Any]]:
    measurements: list[UwbMeasurement] = []
    accounting = Counter()
    latency_ms = []
    per_link = Counter()
    for node in NODES:
        rows = streams[node]["uwb"]
        accepted = rows[rows["status"] == 1]
        if len(accepted):
            offsets = accepted["master_arrival_ms"].astype(np.int64) * 1_000_000 - accepted["global_time_ns"].astype(np.int64)
            arrival_offset = int(np.median(offsets))
        else:
            arrival_offset = 0
        for row in rows:
            if int(row["status"]) != 1:
                accounting["received_invalid_status_sweep"] += 1
                continue
            mask = int(row["valid_mask"])
            anchor_ids = [int(value) for value in row["anchor_id"]]
            if sorted(anchor_ids) != list(range(8)):
                accounting["invalid_anchor_inventory_sweep"] += 1
                continue
            accounting["accepted_sweep"] += 1
            for slot_index, anchor_id in enumerate(anchor_ids):
                if not (mask & (1 << slot_index)):
                    accounting["expected_anchor_slot_invalid_or_missing"] += 1
                    continue
                range_mm = int(row["range_mm"][slot_index])
                quality = int(row["quality_percent"][slot_index])
                if range_mm <= 0 or quality <= 0:
                    accounting["received_invalid_scalar"] += 1
                    continue
                measurement_ns = int(row["global_time_ns"]) + int(row["t_round_us"][slot_index]) * 500
                if not (start_ns <= measurement_ns < end_ns):
                    accounting["physical_epoch_outside_window"] += 1
                    continue
                aligned_arrival_ns = int(row["master_arrival_ms"]) * 1_000_000 - arrival_offset
                availability_ns = max(measurement_ns, aligned_arrival_ns)
                latency_ms.append((availability_ns - measurement_ns) * 1e-6)
                link = f"{node}:{anchor_id}"
                base_sigma = sigma_by_link.get(link, 0.25)
                sigma = min(0.80, base_sigma * np.sqrt(100.0 / max(quality, 20)))
                uid = f"uwb:{window_name}:{node}:{int(row['raw_record_index'])}:{anchor_id}"
                measurements.append(UwbMeasurement(
                    event_uid=uid, tag_id=node, anchor_id=anchor_id,
                    measurement_time_s=(measurement_ns - start_ns) * 1e-9,
                    availability_time_s=(availability_ns - start_ns) * 1e-9,
                    range_m=range_mm / 1000.0, sigma_m=float(sigma),
                    boot_epoch=int(row["boot_epoch"]), clock_valid=True,
                ))
                accounting["received_valid_scalar"] += 1
                per_link[link] += 1
    measurements.sort(key=lambda row: (row.measurement_time_s, row.tag_id, row.anchor_id, row.event_uid))
    return measurements, {
        "counts": dict(accounting),
        "per_link_valid_scalar_count": dict(sorted(per_link.items())),
        "latency_ms": {
            "count": len(latency_ms), "median": float(np.median(latency_ms)),
            "p95": float(np.percentile(latency_ms, 95)), "maximum": float(np.max(latency_ms)),
            "alignment": "master_arrival_ms aligned to global_time_ns by selected-window per-node median offset",
        },
        "measurement_epoch": "poll global_time_ns + measured t_round_us/2 per anchor",
    }


def _estimate_uwb_sigmas(
    window_a: dict[str, dict[str, np.ndarray]], quiet: dict[str, Any]
) -> tuple[dict[str, float], dict[str, Any]]:
    start = int(quiet["start_global_time_ns"])
    end = int(quiet["end_global_time_ns_exclusive"])
    sigmas = {}
    rows_out = {}
    for node in NODES:
        rows = window_a[node]["uwb"]
        left, right = _slice_indices(rows, start, end)
        for row in rows[left:right]:
            if int(row["status"]) != 1:
                continue
            for index, anchor in enumerate(row["anchor_id"]):
                if int(row["valid_mask"]) & (1 << index):
                    rows_out.setdefault(f"{node}:{int(anchor)}", []).append(int(row["range_mm"][index]) / 1000.0)
    audit = {}
    for link in (f"{node}:{anchor}" for node in NODES for anchor in range(8)):
        values = np.asarray(rows_out.get(link, []), float)
        if len(values) >= 3:
            median = float(np.median(values))
            mad_sigma = 1.4826 * float(np.median(np.abs(values - median)))
        else:
            median = None
            mad_sigma = 0.25
        sigma = float(np.clip(np.sqrt(mad_sigma**2 + 0.08**2 + 0.035**2), 0.12, 0.60))
        sigmas[link] = sigma
        audit[link] = {
            "samples": int(len(values)), "median_range_m": median,
            "mad_sigma_m": mad_sigma, "anchor_geometry_floor_m": 0.08,
            "rf_phase_centre_floor_m": 0.035, "effective_sigma_m": sigma,
        }
    return sigmas, audit


def _fit_initial_translation(
    model, calibration: StaticCalibration, state: KeyframeState,
    measurements: list[UwbMeasurement], quiet: dict[str, Any],
) -> tuple[KeyframeState, dict[str, Any]]:
    from scipy.optimize import least_squares

    # Measurements were flattened against the quiet interval for this fit.
    selected = measurements
    zero = replace(state, root_translation_model_m=np.zeros(3))
    relative = model.tag_phase_centres(zero, calibration)
    anchors = {anchor: calibration.vector(f"anchor_position:{anchor}", 3) for anchor in range(8)}
    delays = {anchor: float(calibration.vector(f"anchor_delay:{anchor}", 1)[0]) for anchor in range(8)}

    def residual(position: np.ndarray) -> np.ndarray:
        return np.asarray([
            (row.range_m - (np.linalg.norm(anchors[row.anchor_id] - (relative[row.tag_id] + position)) + delays[row.anchor_id])) / row.sigma_m
            for row in selected
        ])

    solution = least_squares(
        residual, np.array([2.1, 1.5, 0.9]), loss="huber", f_scale=2.0,
        bounds=([-1.0, -1.0, -0.5], [5.5, 4.5, 2.5]), max_nfev=300,
    )
    fitted = replace(state, root_translation_model_m=solution.x.copy())
    fitted.validate(model)
    values = residual(solution.x)
    return fitted, {
        "method": "session-local robust translation nuisance fit over Window-A quiet slice",
        "state_not_calibration_slot": True, "measurement_count": len(selected),
        "initial_guess_m": [2.1, 1.5, 0.9], "fitted_translation_v4_m": solution.x.tolist(),
        "success": bool(solution.success), "cost": float(solution.cost),
        "normalized_residual_median": float(np.median(values)),
        "normalized_residual_p95_abs": float(np.percentile(np.abs(values), 95)),
        "global_origin_and_yaw": "V4 session gauge; not an external accuracy result",
    }


def _imu_samples_for_interval(
    rows: np.ndarray, node: str, start_ns: int, end_ns: int, matrix: np.ndarray,
) -> tuple[ImuSample, ...]:
    accepted_rows = rows[rows["status"] == 1]
    left = _lower_bound(accepted_rows, start_ns)
    if left > 0:
        left -= 1
    right = _lower_bound(accepted_rows, end_ns)
    chosen = accepted_rows[left:right]
    return tuple(ImuSample(
        node_id=node, global_time_ns=int(row["global_time_ns"]), boot_epoch=int(row["boot_epoch"]),
        accel_mps2=matrix @ (row["acc_raw"].astype(float) / 2048.0 * 9.80665),
        gyro_rad_s=matrix @ np.deg2rad(row["gyro_raw"].astype(float) / 16.384),
        accepted=True,
        acc_raw=tuple(int(value) for value in row["acc_raw"]),
        gyro_raw=tuple(int(value) for value in row["gyro_raw"]),
    ) for row in chosen)


def _state_arrays(model, calibration: StaticCalibration, states: list[KeyframeState]) -> dict[str, np.ndarray]:
    segment_ids = model.segments
    joint_ids = model.joint_ids
    node_ids = model.imu_ids
    arrays: dict[str, list[Any]] = {
        "time_s": [], "root_position_m": [], "root_rotation_rotvec": [], "root_velocity_mps": [],
        "segment_position_m": [], "segment_rotation_rotvec": [], "joint_rotation_rotvec": [],
        "joint_rate_rad_s": [], "gyro_bias_rad_s": [], "accelerometer_bias_mps2": [],
        "covariance_diagonal": [], "covariance_trace": [], "root_position_covariance_eigenvalues": [],
        "covariance_minimum_eigenvalue": [], "fk_closure_max_m": [], "skeleton_endpoints_m": [],
    }
    for state in states:
        predictions = model.all_predictions(state, calibration)
        segments = predictions["segments"]
        joints = predictions["joints"]
        points = predictions["anatomical_points"]
        endpoint_pairs = [
            (joints["hip_left"], joints["hip_right"]),
            (joints["pelvis_torso"], points["torso_top"]),
            (joints["shoulder_left"], joints["elbow_left"]),
            (joints["elbow_left"], points["wrist_left"]),
            (joints["shoulder_right"], joints["elbow_right"]),
            (joints["elbow_right"], points["wrist_right"]),
            (joints["hip_left"], joints["knee_left"]),
            (joints["knee_left"], points["ankle_left"]),
            (joints["hip_right"], joints["knee_right"]),
            (joints["knee_right"], points["ankle_right"]),
        ]
        arrays["time_s"].append(state.time_s)
        arrays["root_position_m"].append(state.root_translation_model_m)
        arrays["root_rotation_rotvec"].append(state.root_rotation_model_rotvec)
        arrays["root_velocity_mps"].append(state.root_velocity_model_mps)
        arrays["segment_position_m"].append([segments[name].translation for name in segment_ids])
        arrays["segment_rotation_rotvec"].append([so3_log(segments[name].rotation) for name in segment_ids])
        arrays["joint_rotation_rotvec"].append([state.joint_rotvec[name] for name in joint_ids])
        arrays["joint_rate_rad_s"].append([state.joint_rate_rad_s[name] for name in joint_ids])
        arrays["gyro_bias_rad_s"].append([state.gyro_bias_rad_s[name] for name in node_ids])
        arrays["accelerometer_bias_mps2"].append([state.accel_bias_mps2[name] for name in node_ids])
        arrays["covariance_diagonal"].append(np.diag(state.covariance))
        arrays["covariance_trace"].append(np.trace(state.covariance))
        arrays["root_position_covariance_eigenvalues"].append(np.linalg.eigvalsh(state.covariance[:3, :3]))
        arrays["covariance_minimum_eigenvalue"].append(np.min(np.linalg.eigvalsh(state.covariance)))
        arrays["fk_closure_max_m"].append(np.max(np.abs(predictions["kinematic_residuals"])))
        arrays["skeleton_endpoints_m"].append(endpoint_pairs)
    return {key: np.asarray(value) for key, value in arrays.items()}


def _run_variant(
    model, calibration: StaticCalibration, registry, initial: KeyframeState,
    streams: dict[str, dict[str, np.ndarray]], matrices: dict[str, np.ndarray],
    measurements: list[UwbMeasurement], noise: dict[str, NoiseParameters],
    start_ns: int, end_ns: int, variant: str,
) -> tuple[dict[str, np.ndarray], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    estimator = RepairedShadowEstimator(model, initial)
    estimator.preintegrator = NativeTimePreintegrator(noise, PreintegratorConfig(
        max_gap_s=0.020, missing_sample_threshold_s=0.0075,
        accel_saturation_mps2=80.0, gyro_saturation_rad_s=12.0,
    ))
    if variant == "IMU_ONLY":
        options = EstimatorOptions(uwb_enabled=False)
    elif variant == "UWB_ONLY_DIAGNOSTIC":
        options = EstimatorOptions(
            uwb_enabled=True, health_accommodation_enabled=False,
            bias_updates_enabled=False, robust_weighting_enabled=False,
            recovery_ramp_enabled=False,
        )
    elif variant == "FULL_FUSION_HEALTH_DISABLED":
        options = EstimatorOptions(
            uwb_enabled=True, health_accommodation_enabled=False,
            robust_weighting_enabled=False, recovery_ramp_enabled=False,
        )
    elif variant == "FULL_FUSION":
        options = EstimatorOptions()
    else:
        raise ValueError(variant)

    step_ns = 50_000_000
    measurement_index = 0
    outputs = []
    accounting_rows: list[dict[str, Any]] = []
    health_rows: list[dict[str, Any]] = []
    step_index = 0
    while start_ns + step_index * step_ns < end_ns:
        interval_start = start_ns + step_index * step_ns
        interval_end = min(end_ns, interval_start + step_ns)
        start_s = (interval_start - start_ns) * 1e-9
        end_s = (interval_end - start_ns) * 1e-9
        imu_streams = {}
        schedules = []
        for node in model.imu_ids:
            samples = () if variant == "UWB_ONLY_DIAGNOSTIC" else _imu_samples_for_interval(
                streams[node]["imu"], node, interval_start, interval_end, matrices[node]
            )
            imu_streams[node] = samples
            if variant != "UWB_ONLY_DIAGNOSTIC":
                boot = samples[0].boot_epoch if samples else int(streams[node]["imu"][0]["boot_epoch"])
                schedules.append(ScheduledObservation(
                    schedule_uid=f"imu:{variant}:{step_index}:{node}", modality="IMU", node_id=node,
                    tag_id=None, anchor_id=None, expected_time_s=end_s, deadline_s=end_s + 0.02,
                    boot_epoch=boot, clock_valid=True, node_online=True,
                ))
        current_measurements = []
        if variant != "IMU_ONLY":
            while measurement_index < len(measurements) and measurements[measurement_index].measurement_time_s < end_s:
                row = measurements[measurement_index]
                measurement_index += 1
                if row.measurement_time_s + 1e-12 < start_s:
                    continue
                current_measurements.append(row)
                schedules.append(ScheduledObservation(
                    schedule_uid=row.event_uid, modality="UWB", node_id=row.tag_id,
                    tag_id=row.tag_id, anchor_id=row.anchor_id,
                    expected_time_s=row.measurement_time_s,
                    deadline_s=row.measurement_time_s + 0.250,
                    boot_epoch=row.boot_epoch, clock_valid=row.clock_valid, node_online=True,
                ))
        estimator_input = EstimatorInput(
            step_index=step_index, interval_start_s=start_s, interval_end_s=end_s,
            imu_streams=imu_streams, uwb_measurements=tuple(current_measurements),
            expected_schedule=tuple(schedules), calibration=calibration, registry=registry,
            geometry_class="REAL_CAPTURE_BOUND_V4_SESSION_GAUGE", options=options,
        )
        output = estimator.step(estimator_input)
        outputs.append(output)
        counts = Counter(row.status.value for row in output.accounting)
        accounting_rows.append({"step": step_index, "time_s": end_s, **dict(counts)})
        health_rows.append({
            "step": step_index, "time_s": end_s, "mode": output.mode.value,
            "attribution": output.attribution,
            "transition_count": len(output.health_transitions),
            "uwb_accepted_this_step": int(output.residual_evidence["uwb"].get("accepted", 0)),
            "covariance_trace": output.covariance_evidence["trace"],
            "root_position_trace_m2": output.covariance_evidence["root_position_trace_m2"],
            "minimum_eigenvalue": output.covariance_evidence["minimum_eigenvalue"],
        })
        step_index += 1

    arrays = _state_arrays(model, calibration, estimator.states)
    finite = all(np.isfinite(value).all() for value in arrays.values())
    covariance_psd = bool(np.min(arrays["covariance_minimum_eigenvalue"]) >= -1e-10)
    root = arrays["root_position_m"]
    orientation = arrays["root_rotation_rotvec"]
    joints = arrays["joint_rotation_rotvec"]
    summary = {
        "finite_state_execution": finite,
        "steps": step_index, "states": len(estimator.states),
        "root_start_m": root[0].tolist(), "root_end_m": root[-1].tolist(),
        "root_displacement_m": float(np.linalg.norm(root[-1] - root[0])),
        "root_path_length_m": float(np.sum(np.linalg.norm(np.diff(root, axis=0), axis=1))),
        "root_position_jitter_rms_about_median_m": float(np.sqrt(np.mean(np.square(
            root - np.median(root, axis=0)
        )))),
        "root_velocity_rms_mps": float(np.sqrt(np.mean(np.square(arrays["root_velocity_mps"])))),
        "root_velocity_peak_mps": float(np.max(np.linalg.norm(arrays["root_velocity_mps"], axis=1))),
        "root_orientation_change_deg": float(np.degrees(np.linalg.norm(orientation[-1] - orientation[0]))),
        "root_orientation_jitter_rms_about_median_deg": float(np.degrees(np.sqrt(np.mean(np.square(
            orientation - np.median(orientation, axis=0)
        ))))),
        "joint_rotation_span_deg": {
            joint: float(np.degrees(np.max(np.linalg.norm(joints[:, index] - joints[0, index], axis=1))))
            for index, joint in enumerate(model.joint_ids)
        },
        "joint_rate_peak_rad_s": {
            joint: float(np.max(np.linalg.norm(arrays["joint_rate_rad_s"][:, index], axis=1)))
            for index, joint in enumerate(model.joint_ids)
        },
        "covariance": {
            "finite": bool(np.isfinite(arrays["covariance_diagonal"]).all()),
            "psd": covariance_psd,
            "minimum_eigenvalue": float(np.min(arrays["covariance_minimum_eigenvalue"])),
            "trace_start": float(arrays["covariance_trace"][0]),
            "trace_end": float(arrays["covariance_trace"][-1]),
            "root_position_eigenvalues_end": arrays["root_position_covariance_eigenvalues"][-1].tolist(),
        },
        "fk_closure_max_m": float(np.max(arrays["fk_closure_max_m"])),
        "fixed_bone_invariance_max_m": float(np.max(np.ptp(
            np.linalg.norm(arrays["skeleton_endpoints_m"][:, :, 1] - arrays["skeleton_endpoints_m"][:, :, 0], axis=2), axis=0
        ))),
        "preintegration_status_counts": dict(estimator.preintegration_status),
        "uwb_correction_nonzero_steps": int(np.sum(np.asarray(estimator.correction_norms) > 1e-10)),
        "uwb_correction_max_m": float(max(estimator.correction_norms, default=0.0)),
        "health_transition_count": len(estimator.health.transitions()),
        "health_transitions": list(estimator.health.transitions()),
        "recovery_transition_count": int(sum(
            row["to"] in {"RECOVERING", "REQUALIFYING", "CONTROLLED_REENTRY", "HEALTHY"}
            and row["from"] != "HEALTHY" for row in estimator.health.transitions()
        )),
        "mode_counts": dict(Counter(estimator.mode_history)),
        "accounting_counts": dict(Counter(
            row.status.value for output in outputs for row in output.accounting
        )),
        "accounting_counts_by_modality": {
            modality: dict(Counter(
                row.status.value for output in outputs for row in output.accounting
                if row.modality == modality
            )) for modality in ("IMU", "UWB")
        },
        "bias_end": {
            node: {
                "gyro_rad_s": estimator.state.gyro_bias_rad_s[node].tolist(),
                "accelerometer_mps2": estimator.state.accel_bias_mps2[node].tolist(),
            } for node in model.imu_ids
        },
    }
    return arrays, summary, estimator.innovation_rows, health_rows


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    array = np.asarray(tuple(values), float)
    if len(array) == 0:
        return {"count": 0, "mean": None, "median": None, "rms": None, "p95_abs": None}
    return {
        "count": int(len(array)), "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "rms": float(np.sqrt(np.mean(np.square(array)))),
        "p95_abs": float(np.percentile(np.abs(array), 95)),
    }


def _residual_summary(
    innovation_rows: dict[str, dict[str, list[dict[str, Any]]]],
    state_summaries: dict[str, dict[str, dict[str, Any]]],
    uwb_accounting: dict[str, Any],
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "schema": "biospur-root-r6a2b-real-residual-nis-summary-v1",
        "scalar_nis_degrees_of_freedom": 1,
        "windows": {},
    }
    for window, variants in innovation_rows.items():
        output["windows"][window] = {}
        for variant, rows in variants.items():
            groups: dict[str, dict[str, list[dict[str, Any]]]] = {
                "node": {}, "anchor": {}, "link": {},
            }
            for row in rows:
                parts = row["event_uid"].split(":")
                node, anchor = parts[-3], parts[-1]
                for kind, key in (("node", node), ("anchor", anchor), ("link", f"{node}:{anchor}")):
                    groups[kind].setdefault(key, []).append(row)

            def summarize(selected: list[dict[str, Any]]) -> dict[str, Any]:
                return {
                    "innovation_m": _distribution(row["innovation_m"] for row in selected),
                    "raw_nominal_scalar_nis": _distribution(row["raw_nominal_scalar_nis"] for row in selected),
                    "effective_weighted_scalar_nis": _distribution(row["effective_weighted_scalar_nis"] for row in selected),
                    "weight": _distribution(row["weight"] for row in selected),
                    "downweighted_count": int(sum(float(row["weight"]) < 0.999999 for row in selected)),
                    "robust_downweighted_count": int(sum(float(row["robust_weight"]) < 0.999999 for row in selected)),
                    "health_downweighted_count": int(sum(float(row["health_weight"]) < 0.999999 for row in selected)),
                }

            modality_status = state_summaries[window][variant].get("accounting_counts_by_modality")
            if modality_status is None:
                # Compatibility with the already-exported first exposure.  Every
                # combined run scheduled one IMU interval per node and step.
                status_all = state_summaries[window][variant]["accounting_counts"]
                imu_accepted = state_summaries[window][variant]["steps"] * 10 if variant not in {
                    "UWB_ONLY_DIAGNOSTIC",
                } else 0
                status = dict(status_all)
                status["RECEIVED_ACCEPTED"] = int(status.get("RECEIVED_ACCEPTED", 0)) - imu_accepted
            else:
                status = modality_status["UWB"]
            received = int(uwb_accounting[window]["counts"].get("received_valid_scalar", 0))
            accepted = int(status.get("RECEIVED_ACCEPTED", 0))
            rejected = int(status.get("RECEIVED_REJECTED", 0))
            output["windows"][window][variant] = {
                "overall": summarize(rows),
                "by_node": {key: summarize(value) for key, value in sorted(groups["node"].items())},
                "by_anchor": {key: summarize(value) for key, value in sorted(groups["anchor"].items(), key=lambda item: int(item[0]))},
                "by_link": {key: summarize(value) for key, value in sorted(groups["link"].items())},
                "observation_accounting": {
                    "valid_real_scalars_presented": received if variant != "IMU_ONLY" else 0,
                    "accepted": accepted, "rejected": rejected,
                    "downweighted_accepted": int(sum(float(row["weight"]) < 0.999999 for row in rows)),
                    "expected_but_missing": int(status.get("EXPECTED_BUT_MISSING", 0)),
                    "late": int(status.get("LATE", 0)),
                    "boot_epoch_invalid": int(status.get("BOOT_EPOCH_INVALID", 0)),
                    "clock_invalid": int(status.get("CLOCK_INVALID", 0)),
                    "invalid_or_missing_capture_slots": int(
                        uwb_accounting[window]["counts"].get("expected_anchor_slot_invalid_or_missing", 0)
                    ) if variant != "IMU_ONLY" else 0,
                    "received_invalid_capture_scalars": int(
                        uwb_accounting[window]["counts"].get("received_invalid_scalar", 0)
                    ) if variant != "IMU_ONLY" else 0,
                    "accounting_identity_holds": (
                        variant == "IMU_ONLY" or accepted + rejected
                        + int(status.get("LATE", 0)) + int(status.get("BOOT_EPOCH_INVALID", 0))
                        + int(status.get("CLOCK_INVALID", 0)) == received
                    ),
                },
            }
    return output


def _rms_difference(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape:
        raise ValueError(f"ablation arrays differ: {left.shape} versus {right.shape}")
    return float(np.sqrt(np.mean(np.square(left - right))))


def _ablation_summary(
    arrays: dict[str, dict[str, dict[str, np.ndarray]]],
    summaries: dict[str, dict[str, dict[str, Any]]],
    residuals: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "biospur-root-r6a2b-real-ablation-summary-v1", "windows": {},
        "skin_slip_disabled": {
            "status": "NOT_SUPPORTED",
            "reason": "qualified R2 has hierarchical model/slip ambiguity and local downweighting but no explicit skin-slip state switch",
            "fabricated_variant_run": False,
        },
    }
    material = []
    for window, variants in arrays.items():
        full = variants["FULL_FUSION"]
        comparisons = {}
        for other in ("IMU_ONLY", "UWB_ONLY_DIAGNOSTIC", "FULL_FUSION_HEALTH_DISABLED"):
            candidate = variants[other]
            comparisons[f"FULL_FUSION_vs_{other}"] = {
                "root_position_rms_difference_m": _rms_difference(full["root_position_m"], candidate["root_position_m"]),
                "root_velocity_rms_difference_mps": _rms_difference(full["root_velocity_mps"], candidate["root_velocity_mps"]),
                "segment_position_rms_difference_m": _rms_difference(full["segment_position_m"], candidate["segment_position_m"]),
                "joint_orientation_rms_difference_rad": _rms_difference(full["joint_rotation_rotvec"], candidate["joint_rotation_rotvec"]),
                "covariance_trace_end_difference": float(full["covariance_trace"][-1] - candidate["covariance_trace"][-1]),
            }
        imu_effect = comparisons["FULL_FUSION_vs_UWB_ONLY_DIAGNOSTIC"]["joint_orientation_rms_difference_rad"]
        uwb_effect = comparisons["FULL_FUSION_vs_IMU_ONLY"]["root_position_rms_difference_m"]
        update_count = summaries[window]["FULL_FUSION"]["uwb_correction_nonzero_steps"]
        window_material = bool(imu_effect > 1e-4 and uwb_effect > 1e-4 and update_count > 0)
        material.append(window_material)
        result["windows"][window] = {
            "comparisons": comparisons,
            "actual_imu_material_effect": imu_effect > 1e-4,
            "actual_uwb_material_effect": uwb_effect > 1e-4 and update_count > 0,
            "both_modalities_materially_influence_full_state": window_material,
            "full_fusion_uwb_nonzero_update_steps": update_count,
            "full_fusion_uwb_accepted": residuals["windows"][window]["FULL_FUSION"]["observation_accounting"]["accepted"],
            "health_disabled_uwb_accepted": residuals["windows"][window]["FULL_FUSION_HEALTH_DISABLED"]["observation_accounting"]["accepted"],
        }
    result["both_modalities_material_in_both_windows"] = bool(all(material))
    return result


def _write_health_csv(
    path: Path, health_rows: dict[str, dict[str, list[dict[str, Any]]]],
    summaries: dict[str, dict[str, dict[str, Any]]],
) -> None:
    fields = [
        "row_type", "window", "variant", "step", "time_s", "mode", "attribution", "transition_count",
        "uwb_accepted_this_step", "covariance_trace", "root_position_trace_m2", "minimum_eigenvalue",
        "modality", "entity_id", "from", "to", "evidence", "measurement_weight",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for window, variants in health_rows.items():
            for variant, rows in variants.items():
                for row in rows:
                    writer.writerow({"row_type": "STEP", "window": window, "variant": variant, **row})
                for transition in summaries[window][variant]["health_transitions"]:
                    writer.writerow({
                        "row_type": "TRANSITION", "window": window, "variant": variant,
                        **{key: transition.get(key) for key in (
                            "time_s", "modality", "entity_id", "from", "to", "evidence", "measurement_weight",
                        )},
                    })


def _save_state_npz(
    path: Path, model, arrays: dict[str, dict[str, dict[str, np.ndarray]]]
) -> None:
    payload: dict[str, np.ndarray] = {
        "node_ids": np.asarray(model.imu_ids), "segment_ids": np.asarray(model.segments),
        "joint_ids": np.asarray(model.joint_ids),
        "identity_segments_for_node_ids": np.asarray([IDENTITY[node] for node in model.imu_ids]),
    }
    for window, variants in arrays.items():
        for variant, values in variants.items():
            for key, value in values.items():
                payload[f"{window}__{variant}__{key}"] = value
    np.savez_compressed(path, **payload)


def _plot_outputs(
    result_dir: Path, model, arrays: dict[str, dict[str, dict[str, np.ndarray]]],
    residual_summary: dict[str, Any], health_rows: dict[str, dict[str, list[dict[str, Any]]]],
    state_summaries: dict[str, dict[str, dict[str, Any]]],
) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    label = "REAL measured inputs | session-local priors | estimated states | ground truth unavailable | V4 origin/yaw gauge-fixed"
    outputs = []

    figure, axes = plt.subplots(1, 2, figsize=(14, 6))
    for axis, window in zip(axes, ("A", "B")):
        for variant, values in arrays[window].items():
            root = values["root_position_m"]
            axis.plot(root[:, 0], root[:, 1], label=variant, linewidth=1.2)
        axis.set(title=f"Window {window} root XY", xlabel="V4 X [m]", ylabel="V4 Y [m]")
        axis.axis("equal"); axis.grid(True, alpha=.3); axis.legend(fontsize=7)
    figure.suptitle(label, fontsize=9)
    figure.tight_layout()
    path = result_dir / "ROOT_TRAJECTORIES.png"; figure.savefig(path, dpi=160); plt.close(figure); outputs.append(path)

    for window in ("A", "B"):
        values = arrays[window]["FULL_FUSION"]
        figure, axes = plt.subplots(9, 2, figsize=(14, 22), sharex=True)
        for index, joint in enumerate(model.joint_ids):
            angles = np.degrees(values["joint_rotation_rotvec"][:, index])
            rates = values["joint_rate_rad_s"][:, index]
            for component, name in enumerate("XYZ"):
                axes[index, 0].plot(values["time_s"], angles[:, component], label=name, linewidth=.8)
                axes[index, 1].plot(values["time_s"], rates[:, component], label=name, linewidth=.8)
            axes[index, 0].set_ylabel(f"{joint}\nangle [deg]")
            axes[index, 1].set_ylabel("rate [rad/s]")
            axes[index, 0].grid(True, alpha=.25); axes[index, 1].grid(True, alpha=.25)
        axes[-1, 0].set_xlabel("time [s]"); axes[-1, 1].set_xlabel("time [s]")
        axes[0, 0].legend(ncol=3, fontsize=7); axes[0, 1].legend(ncol=3, fontsize=7)
        figure.suptitle(f"Window {window} full fusion — {label}", fontsize=9)
        figure.tight_layout(rect=[0, 0, 1, .99])
        path = result_dir / f"NINE_JOINT_ANGLES_AND_RATES_{window}.png"
        figure.savefig(path, dpi=140); plt.close(figure); outputs.append(path)

    figure, axes = plt.subplots(2, 2, figsize=(15, 9), sharex=False)
    for row_index, window in enumerate(("A", "B")):
        grouped = residual_summary["windows"][window]["FULL_FUSION"]
        for column, (kind, title) in enumerate((("by_node", "node"), ("by_anchor", "anchor"))):
            axis = axes[row_index, column]
            names = list(grouped[kind])
            values = [grouped[kind][name]["innovation_m"]["rms"] for name in names]
            axis.bar(names, values)
            axis.set(title=f"Window {window} residual RMS by {title}", ylabel="RMS [m]")
            axis.tick_params(axis="x", rotation=60 if kind == "by_node" else 0, labelsize=7)
            axis.grid(True, axis="y", alpha=.25)
    figure.suptitle(label, fontsize=9); figure.tight_layout()
    path = result_dir / "UWB_RESIDUALS_BY_NODE_AND_ANCHOR.png"; figure.savefig(path, dpi=180); plt.close(figure); outputs.append(path)

    figure, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    for variant, values in arrays["B"].items():
        axes[0].plot(values["time_s"], values["covariance_trace"], label=variant, linewidth=1)
        axes[1].plot(values["time_s"], values["root_position_covariance_eigenvalues"][:, 0], label=f"{variant} min")
        axes[1].plot(values["time_s"], values["root_position_covariance_eigenvalues"][:, -1], linestyle="--", label=f"{variant} max")
    axes[0].set(ylabel="full covariance trace", title="Window B uncertainty")
    axes[1].set(xlabel="time [s]", ylabel="root position covariance eigenvalue [m²]")
    for axis in axes: axis.grid(True, alpha=.25); axis.legend(fontsize=6, ncol=2)
    figure.suptitle(label, fontsize=9); figure.tight_layout()
    path = result_dir / "COVARIANCE_UNCERTAINTY_TIMELINE.png"; figure.savefig(path, dpi=160); plt.close(figure); outputs.append(path)

    figure, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    for index, window in enumerate(("A", "B")):
        rows = health_rows[window]["FULL_FUSION"]
        times = [row["time_s"] for row in rows]
        accepted = [row["uwb_accepted_this_step"] for row in rows]
        modes = [row["mode"] for row in rows]
        inventory = {mode: number for number, mode in enumerate(sorted(set(modes)))}
        axes[index].plot(times, accepted, label="accepted UWB", linewidth=.8)
        axes[index].step(times, [inventory[mode] for mode in modes], where="post", label="mode code", linewidth=.8)
        axes[index].set(title=f"Window {window} health/degraded mode", ylabel="count / mode code")
        axes[index].grid(True, alpha=.25); axes[index].legend(fontsize=7)
        axes[index].text(.995, .95, str(inventory), transform=axes[index].transAxes, ha="right", va="top", fontsize=6)
    axes[-1].set_xlabel("time [s]"); figure.suptitle(label, fontsize=9); figure.tight_layout()
    path = result_dir / "HEALTH_DEGRADED_MODE_TIMELINE.png"; figure.savefig(path, dpi=160); plt.close(figure); outputs.append(path)

    # Plot health-derived factor weights at their actual transition times.
    figure, axes = plt.subplots(2, 1, figsize=(15, 8))
    for axis, window in zip(axes, ("A", "B")):
        transitions = state_summaries[window]["FULL_FUSION"]["health_transitions"]
        for modality in ("imu_health", "uwb_link_health", "uwb_tag_health", "uwb_anchor_health"):
            chosen = [row for row in transitions if row["modality"] == modality]
            axis.scatter(
                [row["time_s"] for row in chosen], [row["measurement_weight"] for row in chosen],
                s=4, alpha=.45, label=modality,
            )
        axis.set(title=f"Window {window} health-derived measurement weights at transitions", ylabel="weight", xlabel="time [s]")
        axis.set_ylim(-.05, 1.05); axis.grid(True, alpha=.25); axis.legend(fontsize=7, ncol=4)
    figure.suptitle(label, fontsize=9); figure.tight_layout()
    path = result_dir / "IMU_UWB_MEASUREMENT_WEIGHT_TIMELINE.png"; figure.savefig(path, dpi=180); plt.close(figure); outputs.append(path)
    return outputs


def _animate_skeleton(
    path: Path, arrays: dict[str, np.ndarray], views: tuple[tuple[str, int, int], ...]
) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter

    endpoints = arrays["skeleton_endpoints_m"]
    time_s = arrays["time_s"]
    indices = np.arange(0, len(time_s), 2)
    frame_centres = np.median(endpoints.reshape(len(endpoints), -1, 3), axis=1)
    local_points = endpoints - frame_centres[:, None, None, :]
    radius = max(0.8, float(np.percentile(np.linalg.norm(local_points.reshape(-1, 3), axis=1), 99)) * 1.20)
    figure = plt.figure(figsize=(6 * len(views), 6))
    axes = [figure.add_subplot(1, len(views), index + 1, projection="3d") for index in range(len(views))]
    colors = plt.cm.tab10(np.linspace(0, 1, endpoints.shape[1]))
    lines = []
    for axis, (name, elevation, azimuth) in zip(axes, views):
        axis.set_xlabel("V4 X [m]"); axis.set_ylabel("V4 Y [m]"); axis.set_zlabel("V4 Z [m]")
        axis.set_title(name); axis.view_init(elev=elevation, azim=azimuth)
        axis.set_box_aspect((1, 1, 1))
        lines.append([axis.plot([], [], [], color=colors[segment], linewidth=2)[0] for segment in range(endpoints.shape[1])])
    title = figure.suptitle("")
    writer = FFMpegWriter(fps=10, metadata={"title": "R6A2B real full-fusion skeleton"}, bitrate=1800)
    with writer.saving(figure, str(path), dpi=110):
        for frame in indices:
            centre = frame_centres[frame]
            for axis in axes:
                axis.set(xlim=(centre[0]-radius, centre[0]+radius), ylim=(centre[1]-radius, centre[1]+radius), zlim=(centre[2]-radius, centre[2]+radius))
            for view_lines in lines:
                for segment, line in enumerate(view_lines):
                    pair = endpoints[frame, segment]
                    line.set_data(pair[:, 0], pair[:, 1]); line.set_3d_properties(pair[:, 2])
            title.set_text(
                f"REAL measured IMU+UWB | session-local priors | estimated state | t={time_s[frame]:.2f}s\n"
                "ground truth unavailable | V4 origin/yaw gauge-fixed | camera follows estimated skeleton"
            )
            writer.grab_frame()
    plt.close(figure)


def _create_animations(result_dir: Path, arrays: dict[str, dict[str, dict[str, np.ndarray]]]) -> list[Path]:
    outputs = []
    for window in ("A", "B"):
        path = result_dir / f"WINDOW_{window}_REAL_FULL_FUSION_3D.mp4"
        _animate_skeleton(path, arrays[window]["FULL_FUSION"], (("3D oblique", 25, -60),))
        outputs.append(path)
    path = result_dir / "WINDOW_B_REAL_FULL_FUSION_FRONT_SIDE_TOP.mp4"
    _animate_skeleton(
        path, arrays["B"]["FULL_FUSION"],
        (("front", 0, -90), ("side", 0, 0), ("top", 90, -90)),
    )
    outputs.append(path)
    return outputs


def _independent_verification(
    fusion: Path, result_dir: Path, final_result: dict[str, Any], ablation: dict[str, Any],
) -> dict[str, Any]:
    required = (
        "FINAL_RESULT.md", "FINAL_RESULT.json", "REAL_WINDOW_SELECTION.json",
        "SESSION_LOCAL_REAL_PROFILE.json", "REAL_INPUT_ACCOUNTING.json",
        "REAL_STATE_SUMMARY.json", "REAL_ABLATION_SUMMARY.json",
        "REAL_RESIDUAL_AND_NIS_SUMMARY.json", "REAL_HEALTH_TIMELINE.csv",
        "REAL_STATE_TIMESERIES.npz", "FRAME_HYPOTHESIS_AUDIT.json",
        "ROOT_TRAJECTORIES.png", "NINE_JOINT_ANGLES_AND_RATES_A.png",
        "NINE_JOINT_ANGLES_AND_RATES_B.png", "IMU_UWB_MEASUREMENT_WEIGHT_TIMELINE.png",
        "HEALTH_DEGRADED_MODE_TIMELINE.png", "COVARIANCE_UNCERTAINTY_TIMELINE.png",
        "UWB_RESIDUALS_BY_NODE_AND_ANCHOR.png", "WINDOW_A_REAL_FULL_FUSION_3D.mp4",
        "WINDOW_B_REAL_FULL_FUSION_3D.mp4", "WINDOW_B_REAL_FULL_FUSION_FRONT_SIDE_TOP.mp4",
    )
    checks: dict[str, Any] = {}

    def record(name: str, passed: bool, evidence: Any) -> None:
        checks[name] = {"pass": bool(passed), "evidence": evidence}

    missing = [name for name in required if not (result_dir / name).is_file()]
    record("required_artifacts_present", not missing, {"missing": missing})
    selection = _load_json(result_dir / "REAL_WINDOW_SELECTION.json")
    labels = [selection["window_a"]["label"], selection["window_b"]["label"]]
    record(
        "only_authorized_windows_opened",
        not selection["held_out_payload_opened"] and not any(label in HELD_OUT_ACTIONS for label in labels),
        {"labels": labels, "held_out_payload_opened": selection["held_out_payload_opened"]},
    )
    ledger = fusion / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json"
    ledger_hash = _sha256(ledger)
    record("immutable_87_slot_ledger_unchanged", ledger_hash == "b159043eb7da4518ac6349832ca3c50e0b3453b1e35d97bbac29223f3e85c4eb", {
        "sha256": ledger_hash, "expected": "b159043eb7da4518ac6349832ca3c50e0b3453b1e35d97bbac29223f3e85c4eb",
        "slot_count": len(_load_json(ledger)["slots"]),
    })
    profile = _load_json(result_dir / "SESSION_LOCAL_REAL_PROFILE.json")
    record("session_profile_has_no_registry_authority", (
        profile["immutable_87_slot_registry"]["writes"] == 0
        and profile["immutable_87_slot_registry"]["thawed"] == 0
        and not profile["real_calibration_authority"] and not profile["production_fusion_authorized"]
    ), profile["immutable_87_slot_registry"])
    predecessor = fusion / "logs/root_r6a2a_r2_covariance_repair_20260825T174103Z/SHA256SUMS"
    predecessor_hash = _sha256(predecessor)
    record("predecessor_manifest_unchanged", predecessor_hash == "90d45c5ab78c5ac050424b44da219ed91febeedca397ec4301e92ab127296073", {
        "sha256": predecessor_hash,
    })
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=fusion, check=True, capture_output=True, text=True,
    ).stdout.strip()
    record("qualified_checkpoint_preserved", head == CHECKPOINT, {"head": head, "expected": CHECKPOINT})

    with np.load(result_dir / "REAL_STATE_TIMESERIES.npz", allow_pickle=False) as archive:
        keys = archive.files
        finite = all(np.isfinite(archive[key]).all() for key in keys if archive[key].dtype.kind in "fiu")
        shapes = {
            window: {
                "states": int(archive[f"{window}__FULL_FUSION__time_s"].shape[0]),
                "segments": int(archive[f"{window}__FULL_FUSION__segment_position_m"].shape[1]),
                "joints": int(archive[f"{window}__FULL_FUSION__joint_rotation_rotvec"].shape[1]),
                "skeleton_lines": int(archive[f"{window}__FULL_FUSION__skeleton_endpoints_m"].shape[1]),
            } for window in ("A", "B")
        }
    record("state_timeseries_finite_and_complete", finite and all(
        row == {"states": 601, "segments": 10, "joints": 9, "skeleton_lines": 10}
        for row in shapes.values()
    ), shapes)
    record("both_real_modalities_material", ablation["both_modalities_material_in_both_windows"], {
        window: ablation["windows"][window]["both_modalities_materially_influence_full_state"]
        for window in ("A", "B")
    })
    record("classification_is_strict_binary", final_result["overall_real_shadow_direction"] in {"POSITIVE", "NEGATIVE"}, {
        "classification": final_result["overall_real_shadow_direction"],
    })
    record("negative_required_by_unresolved_axis", (
        final_result["overall_real_shadow_direction"] == "NEGATIVE"
        and not final_result["physical_coherence"]["physically_coherent"]
    ), final_result["physical_coherence"])
    video_evidence = {}
    videos_ok = True
    for name in (
        "WINDOW_A_REAL_FULL_FUSION_3D.mp4", "WINDOW_B_REAL_FULL_FUSION_3D.mp4",
        "WINDOW_B_REAL_FULL_FUSION_FRONT_SIDE_TOP.mp4",
    ):
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_name,width,height,duration", "-of", "json", str(result_dir / name)],
            capture_output=True, text=True,
        )
        video_evidence[name] = json.loads(probe.stdout) if probe.returncode == 0 and probe.stdout else {"stderr": probe.stderr}
        videos_ok &= probe.returncode == 0 and bool(video_evidence[name].get("streams"))
    record("animations_decode", videos_ok, video_evidence)
    passed = all(row["pass"] for row in checks.values())
    return {
        "schema": "biospur-root-r6a2b-independent-verification-v1",
        "method": "separate post-export artifact, authority, hash, numeric, modality, and video audit",
        "checks": checks, "overall_pass": passed,
    }


def _seal_manifest(result_dir: Path) -> Path:
    manifest = result_dir / "SHA256SUMS"
    rows = []
    for path in sorted(result_dir.iterdir(), key=lambda item: item.name):
        if path.is_file() and path != manifest:
            rows.append(f"{_sha256(path)}  {path.name}")
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return manifest


def _write_final_markdown(path: Path, result: dict[str, Any]) -> None:
    windows = result["windows"]
    row_counts = result["real_input_rows_used"]
    lines = [
        f"OVERALL_REAL_SHADOW_DIRECTION: {result['overall_real_shadow_direction']}",
        f"REAL_BOUNDED_SHADOW_EXECUTED: {'YES' if result['real_bounded_shadow_executed'] else 'NO'}",
        "",
        "# Root-R6A2B first bounded real shadow",
        "",
        f"Window A is `{windows['A']['label']}` attempt {windows['A']['attempt']}, "
        f"`[{windows['A']['start_global_time_ns']}, {windows['A']['end_global_time_ns_exclusive']})` (30 s).",
        f"Window B is `{windows['B']['label']}` attempt {windows['B']['attempt']}, "
        f"`[{windows['B']['start_global_time_ns']}, {windows['B']['end_global_time_ns_exclusive']})` (30 s).",
        "",
        f"The runs used {row_counts['accepted_imu_rows']} accepted real IMU rows and "
        f"{row_counts['valid_uwb_scalar_observations']} valid real UWB scalar observations across both windows. "
        "All four variants used identical selected windows and initial conditions.",
        "",
        "Actual IMU propagation and UWB factor updates occurred, and both modalities changed the full-fusion state. "
        "Fixed-bone FK remained exact and covariance remained finite/PSD. However, the skeleton is not physically "
        "qualified: the selected labelled initial-still window contains strong motion and multiple nodes contradict "
        "the sealed +Y stationary-specific-force relation. No signed-axis hypothesis was adopted because the finite "
        "common-family alternatives did not resolve the cross-node conflict.",
        "",
        f"Dominant failure: {result['dominant_failure']}",
        "",
        f"Most important ablation: {result['most_important_ablation']}",
        "",
        "There is no external ground truth, so these outputs report internal consistency and estimator response only. "
        "The V4 origin and yaw are gauge-fixed and are not an accuracy result.",
        "",
        f"Single next engineering action: {result['single_next_engineering_action']}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def finalize_existing_real_shadow(fusion: Path, result_dir: Path) -> Path:
    """Repair/report an already-completed exposure without rerunning estimators."""
    fusion = Path(fusion); result_dir = Path(result_dir)
    state_summary = _load_json(result_dir / "REAL_STATE_SUMMARY.json")
    residual = _load_json(result_dir / "REAL_RESIDUAL_AND_NIS_SUMMARY.json")
    input_accounting = _load_json(result_dir / "REAL_INPUT_ACCOUNTING.json")
    for window in ("A", "B"):
        valid = int(input_accounting["window_uwb_scalar_accounting"][window]["counts"]["received_valid_scalar"])
        for variant, summary in state_summary["windows"][window].items():
            all_status = summary["accounting_counts"]
            imu = {} if variant == "UWB_ONLY_DIAGNOSTIC" else {"RECEIVED_ACCEPTED": summary["steps"] * 10}
            if variant == "IMU_ONLY":
                uwb = {}
            else:
                uwb = dict(all_status)
                uwb["RECEIVED_ACCEPTED"] = int(uwb.get("RECEIVED_ACCEPTED", 0)) - int(imu.get("RECEIVED_ACCEPTED", 0))
            summary["accounting_counts_by_modality"] = {"IMU": imu, "UWB": uwb}
            observed = residual["windows"][window][variant]["observation_accounting"]
            observed.update({
                "valid_real_scalars_presented": valid if variant != "IMU_ONLY" else 0,
                "accepted": int(uwb.get("RECEIVED_ACCEPTED", 0)),
                "rejected": int(uwb.get("RECEIVED_REJECTED", 0)),
                "expected_but_missing": int(uwb.get("EXPECTED_BUT_MISSING", 0)),
                "late": int(uwb.get("LATE", 0)),
                "boot_epoch_invalid": int(uwb.get("BOOT_EPOCH_INVALID", 0)),
                "clock_invalid": int(uwb.get("CLOCK_INVALID", 0)),
            })
            observed["accounting_identity_holds"] = (
                variant == "IMU_ONLY" or sum(observed[key] for key in (
                    "accepted", "rejected", "expected_but_missing", "late",
                    "boot_epoch_invalid", "clock_invalid",
                )) == valid
            )
    _dump(result_dir / "REAL_STATE_SUMMARY.json", state_summary)
    _dump(result_dir / "REAL_RESIDUAL_AND_NIS_SUMMARY.json", residual)

    ablation = _load_json(result_dir / "REAL_ABLATION_SUMMARY.json")
    for window in ("A", "B"):
        ablation["windows"][window]["full_fusion_uwb_accepted"] = residual["windows"][window]["FULL_FUSION"]["observation_accounting"]["accepted"]
        ablation["windows"][window]["health_disabled_uwb_accepted"] = residual["windows"][window]["FULL_FUSION_HEALTH_DISABLED"]["observation_accounting"]["accepted"]
    _dump(result_dir / "REAL_ABLATION_SUMMARY.json", ablation)

    health_rows: dict[str, dict[str, list[dict[str, Any]]]] = {window: {} for window in ("A", "B")}
    with (result_dir / "REAL_HEALTH_TIMELINE.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("row_type") == "TRANSITION":
                continue
            window, variant = row["window"], row["variant"]
            health_rows[window].setdefault(variant, []).append({
                "step": int(row["step"]), "time_s": float(row["time_s"]),
                "mode": row["mode"], "attribution": row["attribution"],
                "transition_count": int(row["transition_count"]),
                "uwb_accepted_this_step": int(row["uwb_accepted_this_step"]),
                "covariance_trace": float(row["covariance_trace"]),
                "root_position_trace_m2": float(row["root_position_trace_m2"]),
                "minimum_eigenvalue": float(row["minimum_eigenvalue"]),
            })
    _write_health_csv(result_dir / "REAL_HEALTH_TIMELINE.csv", health_rows, state_summary["windows"])

    arrays: dict[str, dict[str, dict[str, np.ndarray]]] = {window: {} for window in ("A", "B")}
    with np.load(result_dir / "REAL_STATE_TIMESERIES.npz", allow_pickle=False) as archive:
        for key in archive.files:
            parts = key.split("__", 2)
            if len(parts) == 3 and parts[0] in arrays:
                arrays[parts[0]].setdefault(parts[1], {})[parts[2]] = np.array(archive[key], copy=True)
    model = corrected_body_model(
        fusion, identity_mapping=IDENTITY,
        identity_provenance="R6A2B_REAL_CAPTURE_EXPLICIT_IDENTITY",
    )
    _plot_outputs(result_dir, model, arrays, residual, health_rows, state_summary["windows"])
    _create_animations(result_dir, arrays)

    final = _load_json(result_dir / "FINAL_RESULT.json")
    for window in ("A", "B"):
        final["real_input_rows_used"]["by_window"][window]["valid_uwb_scalar_observations_used"] = int(
            input_accounting["window_uwb_scalar_accounting"][window]["counts"]["received_valid_scalar"]
        )
    window_a_full = state_summary["windows"]["A"]["FULL_FUSION"]
    a_obs = residual["windows"]["A"]["FULL_FUSION"]["observation_accounting"]
    full_vs_imu_b = ablation["windows"]["B"]["comparisons"]["FULL_FUSION_vs_IMU_ONLY"]
    full_vs_health_b = ablation["windows"]["B"]["comparisons"]["FULL_FUSION_vs_FULL_FUSION_HEALTH_DISABLED"]
    final["dominant_failure"] = (
        f"catastrophic manufactured translation: Window A FULL_FUSION moves {window_a_full['root_displacement_m']:.6g} m "
        f"in 30 s and rejects {a_obs['rejected']} of {a_obs['valid_real_scalars_presented']} valid UWB scalars; "
        "the labelled still also contradicts the sealed stationary frame contract"
    )
    final["most_important_ablation"] = (
        f"In Window B, FULL_FUSION differs from IMU_ONLY by {full_vs_imu_b['root_position_rms_difference_m']:.6g} m RMS root position, "
        f"while disabling health changes it by {full_vs_health_b['root_position_rms_difference_m']:.6g} m RMS; these enormous differences prove "
        "real factor influence but are pathological, not an improvement"
    )
    final["physical_coherence"]["reason"] = (
        "fixed FK is numerically exact, but kilometre-scale manufactured translation, unresolved signed-axis/donning contradiction, "
        "and a nonstationary labelled still prevent physical coherence"
    )
    _dump(result_dir / "FINAL_RESULT.json", final)
    _write_final_markdown(result_dir / "FINAL_RESULT.md", final)
    verification = _independent_verification(fusion, result_dir, final, ablation)
    _dump(result_dir / "INDEPENDENT_VERIFICATION.json", verification)
    if not verification["overall_pass"]:
        failed = [name for name, row in verification["checks"].items() if not row["pass"]]
        raise RuntimeError(f"independent verification failed: {failed}")
    _seal_manifest(result_dir)
    return result_dir / "FINAL_RESULT.json"


def run_bounded_real_shadow(fusion: Path, result_dir: Path) -> Path:
    selection_path = Path(result_dir) / "REAL_WINDOW_SELECTION.json"
    if not selection_path.exists():
        raise RuntimeError("run --select-only first; payload access requires frozen selection")
    selection = _load_json(selection_path)
    if selection.get("schema") != SELECTION_SCHEMA:
        raise RuntimeError("unexpected selection schema")
    # Real payload access is downstream of a qualified, checksum-bound profile.
    # The historical runner constructed broad session priors after opening the
    # payload; that fail-open path is intentionally no longer available.
    from biospur_fusion.root_r6a2b.real_profile import validate_real_profile
    profile_path = Path(result_dir) / "REAL_SUBJECT_SESSION_CALIBRATION_PROFILE.json"
    profile = _load_json(profile_path) if profile_path.exists() else None
    clock_contract = _load_json(
        Path(fusion) / "logs/root_r6a1c_deferred_measurement_bridge_20260825T102823Z/"
        "WORLD_FRAME_BRIDGE_CONTRACT.json"
    )
    boot_epochs = {
        node: int(row["boot_epoch"])
        for node, row in clock_contract["clock_relationships"]["models"].items()
    }
    capture_id = "v47_ten_node_body_calibration_20260814_093601"
    validation = validate_real_profile(
        profile,
        expected_capture_id=capture_id,
        expected_session_id=capture_id,
        expected_boot_epochs=boot_epochs,
        action_authority=selection.get("action_authority", {}),
    )
    if not validation.authorized:
        raise RuntimeError("real profile authorization refused: " + ",".join(validation.failures))
    raise RuntimeError(
        "real profile authorization refused: AUTHORIZED_87_SLOT_PROFILE_CONSUMPTION_ADAPTER_NOT_IMPLEMENTED"
    )
    windows, access = _load_selected_payloads(Path(fusion), selection)
    diagnostics, frame_audit = _window_input_diagnostics(windows)
    result_dir = Path(result_dir)
    fusion = Path(fusion)
    _dump(result_dir / "FRAME_HYPOTHESIS_AUDIT.json", frame_audit)

    quiet = _quiet_initialization_slice(
        windows["A"], int(selection["window_a"]["start_global_time_ns"]),
        int(selection["window_a"]["end_global_time_ns_exclusive"]),
    )
    means_acc, means_gyro, matrices = _mean_imu_vectors(
        windows["A"], int(quiet["start_global_time_ns"]),
        int(quiet["end_global_time_ns_exclusive"]), frame_audit,
    )
    rotations = {node: _orientation_from_gravity_and_skin(node, means_acc[node]) for node in NODES}
    model = corrected_body_model(
        fusion, identity_mapping=IDENTITY,
        identity_provenance="R6A2B_REAL_CAPTURE_EXPLICIT_IDENTITY",
    )
    calibration, profile, noise = _build_session_calibration(fusion, model, rotations)
    initial = _initial_state(model, calibration, rotations, means_acc, means_gyro)
    sigma_by_link, sigma_audit = _estimate_uwb_sigmas(windows["A"], quiet)
    quiet_measurements, quiet_uwb_accounting = _flatten_uwb(
        windows["A"], int(quiet["start_global_time_ns"]),
        int(quiet["end_global_time_ns_exclusive"]), sigma_by_link, "A_QUIET_INIT",
    )
    initial, translation_fit = _fit_initial_translation(
        model, calibration, initial, quiet_measurements, quiet,
    )
    profile.update({
        "frame_hypothesis": {
            "common_nine": frame_audit["common_nine"]["selected"],
            "BSF31CC": frame_audit["BSF31CC"]["selected"],
            "permanent_calibration_write": False,
        },
        "window_a_quiet_initialization_slice": quiet,
        "quiet_initialization_measurements": {
            "mean_specific_force_device_mps2": {node: means_acc[node].tolist() for node in NODES},
            "mean_gyro_device_rad_s": {node: means_gyro[node].tolist() for node in NODES},
            "initial_segment_rotation_model_from_device": {node: rotations[node].tolist() for node in NODES},
            "uwb_accounting": quiet_uwb_accounting,
        },
        "initial_translation_nuisance_fit": translation_fit,
        "uwb_noise_by_link": sigma_audit,
        "parameter_change_ledger": [
            "50 ms estimator keyframes match the qualified R2 cadence and divide the 5 ms IMU cadence exactly",
            "per-link UWB sigma uses Window-A quiet MAD with 80 mm anchor and 35 mm RF-phase-centre floors",
            "bias random walks remain conservative session bounds because R6A1A could not qualify them",
            "root translation is a session-local initial state nuisance, not a calibration-slot fit",
        ],
    })
    _dump(result_dir / "SESSION_LOCAL_REAL_PROFILE.json", profile)

    real_measurements: dict[str, list[UwbMeasurement]] = {}
    uwb_accounting: dict[str, Any] = {}
    for window, selection_key in (("A", "window_a"), ("B", "window_b")):
        selected = selection[selection_key]
        real_measurements[window], uwb_accounting[window] = _flatten_uwb(
            windows[window], int(selected["start_global_time_ns"]),
            int(selected["end_global_time_ns_exclusive"]), sigma_by_link, window,
        )

    input_accounting = {
        **access, **diagnostics, "real_payload_fields_accessed_after_frozen_selection": [
            "acc_raw", "gyro_raw", "range_mm", "quality_percent", "t_round_us",
            "anchor_id", "raw_record_index", "master_arrival_ms",
        ],
        "held_out_payload_opened": False,
        "window_uwb_scalar_accounting": uwb_accounting,
        "exact_selected_rows_only": True,
    }
    _dump(result_dir / "REAL_INPUT_ACCOUNTING.json", input_accounting)

    registry = registry_from_sealed_addendum(fusion)
    variants = ("IMU_ONLY", "UWB_ONLY_DIAGNOSTIC", "FULL_FUSION", "FULL_FUSION_HEALTH_DISABLED")
    all_arrays: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    all_summaries: dict[str, dict[str, dict[str, Any]]] = {}
    all_innovations: dict[str, dict[str, list[dict[str, Any]]]] = {}
    all_health: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for window, selection_key in (("A", "window_a"), ("B", "window_b")):
        selected = selection[selection_key]
        all_arrays[window] = {}; all_summaries[window] = {}; all_innovations[window] = {}; all_health[window] = {}
        for variant in variants:
            print(f"running Window {window} {variant}", flush=True)
            arrays, summary, innovation, health = _run_variant(
                model, calibration, registry, initial, windows[window], matrices,
                real_measurements[window], noise,
                int(selected["start_global_time_ns"]), int(selected["end_global_time_ns_exclusive"]), variant,
            )
            all_arrays[window][variant] = arrays
            all_summaries[window][variant] = summary
            all_innovations[window][variant] = innovation
            all_health[window][variant] = health

    residual_summary = _residual_summary(all_innovations, all_summaries, uwb_accounting)
    ablation = _ablation_summary(all_arrays, all_summaries, residual_summary)
    _dump(result_dir / "REAL_RESIDUAL_AND_NIS_SUMMARY.json", residual_summary)
    _dump(result_dir / "REAL_ABLATION_SUMMARY.json", ablation)
    _write_health_csv(result_dir / "REAL_HEALTH_TIMELINE.csv", all_health, all_summaries)
    _save_state_npz(result_dir / "REAL_STATE_TIMESERIES.npz", model, all_arrays)

    angle_conflicts = {
        node: diagnostics["windows"]["A"]["per_node"][node]["imu"]["angle_to_expected_device_plus_y_deg"]
        for node in NODES
        if diagnostics["windows"]["A"]["per_node"][node]["imu"]["angle_to_expected_device_plus_y_deg"] > 45.0
    }
    motion_conflicts = {
        node: diagnostics["windows"]["A"]["per_node"][node]["imu"]["gyro_norm_p99_rad_s"]
        for node in NODES
        if diagnostics["windows"]["A"]["per_node"][node]["imu"]["gyro_norm_p99_rad_s"] > 0.20
    }
    numerical_coherence = all(
        row["finite_state_execution"] and row["covariance"]["finite"] and row["covariance"]["psd"]
        and row["fk_closure_max_m"] < 1e-8 and row["fixed_bone_invariance_max_m"] < 1e-8
        for variants_out in all_summaries.values() for row in variants_out.values()
    )
    state_summary = {
        "schema": "biospur-root-r6a2b-real-state-summary-v1",
        "identity_map": IDENTITY,
        "node_order": list(model.imu_ids), "segment_order": list(model.segments),
        "joint_order": list(model.joint_ids), "state_dimension": 123,
        "window_a_quiet_initialization_slice": quiet,
        "initial_translation_fit": translation_fit,
        "windows": all_summaries,
        "fixed_geometry": {
            "bones_fixed_during_solve": True, "torso_top_independent_freedom": 0,
            "torso_top_equation": "0.5*(p_shoulder_left+p_shoulder_right)",
        },
        "physical_coherence": {
            "numerically_coherent_fixed_fk": numerical_coherence,
            "physically_coherent": False,
            "unresolved_axis_contract_nodes": angle_conflicts,
            "initial_still_motion_conflict_nodes": motion_conflicts,
            "reason": "unresolved signed-axis/donning contradiction and nonstationary labelled still prevent physical qualification",
        },
        "ground_truth": "UNAVAILABLE",
        "absolute_accuracy_reported": False,
    }
    _dump(result_dir / "REAL_STATE_SUMMARY.json", state_summary)

    print("rendering plots", flush=True)
    plots = _plot_outputs(result_dir, model, all_arrays, residual_summary, all_health, all_summaries)
    print("rendering real-state animations", flush=True)
    animations = _create_animations(result_dir, all_arrays)

    imu_rows = sum(diagnostics["windows"][window]["totals"]["accepted_imu_rows"] for window in ("A", "B"))
    uwb_rows = sum(uwb_accounting[window]["counts"].get("received_valid_scalar", 0) for window in ("A", "B"))
    executed = bool(ablation["both_modalities_material_in_both_windows"] and imu_rows > 0 and uwb_rows > 0)
    full_vs_imu_b = ablation["windows"]["B"]["comparisons"]["FULL_FUSION_vs_IMU_ONLY"]
    full_vs_health_b = ablation["windows"]["B"]["comparisons"]["FULL_FUSION_vs_FULL_FUSION_HEALTH_DISABLED"]
    final_result = {
        "schema": "biospur-root-r6a2b-final-result-v1",
        "overall_real_shadow_direction": "NEGATIVE",
        "real_bounded_shadow_executed": executed,
        "checkpoint": CHECKPOINT,
        "windows": {
            window: {
                "label": selection[key]["label"], "attempt": selection[key]["attempt"],
                "start_global_time_ns": selection[key]["start_global_time_ns"],
                "end_global_time_ns_exclusive": selection[key]["end_global_time_ns_exclusive"],
                "duration_s": selection[key]["duration_s"],
            } for window, key in (("A", "window_a"), ("B", "window_b"))
        },
        "real_input_rows_used": {
            "accepted_imu_rows": int(imu_rows), "valid_uwb_scalar_observations": int(uwb_rows),
            "by_window": {window: diagnostics["windows"][window]["totals"] for window in ("A", "B")},
        },
        "real_state_updates": {
            "occurred": executed,
            "both_modalities_material": ablation["both_modalities_material_in_both_windows"],
            "full_fusion_nonzero_uwb_update_steps": {
                window: all_summaries[window]["FULL_FUSION"]["uwb_correction_nonzero_steps"] for window in ("A", "B")
            },
        },
        "physical_coherence": state_summary["physical_coherence"],
        "dominant_failure": (
            f"catastrophic manufactured translation: Window A FULL_FUSION moves {all_summaries['A']['FULL_FUSION']['root_displacement_m']:.6g} m "
            f"in 30 s and rejects {residual_summary['windows']['A']['FULL_FUSION']['observation_accounting']['rejected']} of "
            f"{uwb_accounting['A']['counts'].get('received_valid_scalar', 0)} valid UWB scalars; the labelled still also has "
            f"{len(motion_conflicts)}/10 nodes above 0.20 rad/s gyro p99 and {len(angle_conflicts)}/10 nodes above 45 deg from sealed +Y gravity"
        ),
        "most_important_ablation": (
            f"In Window B, FULL_FUSION differs from IMU_ONLY by {full_vs_imu_b['root_position_rms_difference_m']:.6g} m RMS root position, "
            f"while disabling health changes it by {full_vs_health_b['root_position_rms_difference_m']:.6g} m RMS; these enormous differences prove "
            "real factor influence but are pathological, not an improvement"
        ),
        "classification_rule": "NEGATIVE because a positive result forbids unresolved axis/left-right ambiguity and requires a valid initial-still result",
        "external_ground_truth_available": False,
        "external_accuracy_claimed": False,
        "production_fusion_authorized": False,
        "immutable_calibration_slots_written": 0,
        "held_out_payload_opened": False,
        "artifacts": {
            "state_timeseries": "REAL_STATE_TIMESERIES.npz",
            "plots": [path.name for path in plots], "animations": [path.name for path in animations],
        },
        "single_next_engineering_action": (
            "perform a synchronized re-capture of a truly motionless neutral/T-pose interval with an operator pose marker, then rerun this frozen selector/runner without changing estimator parameters"
        ),
    }
    _dump(result_dir / "FINAL_RESULT.json", final_result)
    _write_final_markdown(result_dir / "FINAL_RESULT.md", final_result)
    verification = _independent_verification(fusion, result_dir, final_result, ablation)
    _dump(result_dir / "INDEPENDENT_VERIFICATION.json", verification)
    if not verification["overall_pass"]:
        failed = [name for name, row in verification["checks"].items() if not row["pass"]]
        raise RuntimeError(f"independent verification failed: {failed}")
    _seal_manifest(result_dir)
    return result_dir / "FINAL_RESULT.json"
