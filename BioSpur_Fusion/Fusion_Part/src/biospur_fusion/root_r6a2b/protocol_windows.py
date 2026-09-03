"""Protocol-bound C1/C2/C3 rerun of the frozen R6A2B real shadow.

The action brackets in this module are read from their recorded protocol event
files.  There is deliberately no candidate enumeration, payload-dependent
ranking, inferred action onset, or estimator retuning.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import binascii
import csv
import hashlib
import json
from pathlib import Path
import struct
from typing import Any, Iterator

import numpy as np

from biospur_fusion.root_r6a2a.contracts import registry_from_sealed_addendum
from biospur_fusion.root_r6a2a.shadow import corrected_body_model
from biospur_fusion.root_r6a2b.real_shadow import (
    CHECKPOINT,
    IDENTITY,
    NODES,
    _ablation_summary,
    _animate_skeleton,
    _build_session_calibration,
    _dump,
    _flatten_uwb,
    _initial_state,
    _load_json,
    _mean_imu_vectors,
    _orientation_from_gravity_and_skin,
    _residual_summary,
    _run_variant,
    _save_state_npz,
    _sha256,
    _slice_indices,
    _stored_npy_memmap,
    _write_health_csv,
)

SCHEMA = "biospur-root-r6a2b-protocol-window-binding-v1"
FROZEN_RESULT_REL = Path("logs/root_r6a2b_first_bounded_real_shadow_20260826T072148Z")
LEDGER_REL = Path("logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/TIME_EVENT_LEDGER.npz")

IMU_DTYPE = np.dtype([
    ("boot_epoch", "<u2"), ("sequence", "<u2"), ("node_timer_us", "<u8"),
    ("global_time_ns", "<i8"), ("global_time_sigma_ns", "<u8"),
    ("master_arrival_ms", "<u8"), ("base_timer2_us", "<u8"),
    ("delta_us", "<u2"), ("acc_raw", "<i2", (3,)), ("gyro_raw", "<i2", (3,)),
    ("temp_raw", "<i2"), ("raw_record_index", "<u8"),
    ("raw_start_offset", "<u8"), ("raw_end_offset", "<u8"),
    ("raw_sample_index", "u1"), ("status", "u1"),
])
UWB_DTYPE = np.dtype([
    ("boot_epoch", "<u2"), ("sequence", "<u4"), ("node_timer_us", "<u8"),
    ("global_time_ns", "<i8"), ("global_time_sigma_ns", "<u8"),
    ("master_arrival_ms", "<u8"), ("node_ms", "<u4"),
    ("packet_sequence", "<u4"), ("sweep", "<u4"), ("poll_tx_dw40", "<u8"),
    ("identity", "<u2"), ("anchor_id", "u1", (8,)), ("rank", "u1", (8,)),
    ("range_mm", "<u2", (8,)), ("t_round_us", "<u2", (8,)),
    ("quality_percent", "u1", (8,)), ("cfo_ppm_q8", "<i2", (8,)),
    ("valid_mask", "u1"), ("flags", "u1"), ("frame_us", "<u8"),
    ("strobe_us", "<u8"), ("raw_record_index", "<u8"),
    ("raw_start_offset", "<u8"), ("raw_end_offset", "<u8"), ("status", "u1"),
])

HEADER = struct.Struct("<HBBHHIQ")
IMU_HEADER = struct.Struct("<BBHQh")
IMU_SAMPLE = struct.Struct("<Hhhhhhh")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _event_pair(
    path: Path, action_id: str, *, action_key: str, time_key: str, byte_key: str | None,
    attempt: int | None = None,
) -> dict[str, Any]:
    rows = [
        row for row in _read_jsonl(path)
        if row.get(action_key) == action_id
        and (attempt is None or int(row.get("attempt", 1)) == attempt)
        and row.get("event") in {"ACTION_START", "ACTION_STOP"}
    ]
    by_event = {row["event"]: row for row in rows}
    if set(by_event) != {"ACTION_START", "ACTION_STOP"}:
        raise RuntimeError(f"non-unique protocol bracket for {action_id} in {path}")
    start, stop = by_event["ACTION_START"], by_event["ACTION_STOP"]
    start_time = int(start[time_key]) if isinstance(start[time_key], int) else float(start[time_key])
    stop_time = int(stop[time_key]) if isinstance(stop[time_key], int) else float(stop[time_key])
    result: dict[str, Any] = {
        "event_source": str(path),
        "event_source_sha256": _sha256(path),
        "action_id": action_id,
        "attempt": attempt,
        "start_event": "ACTION_START",
        "stop_event": "ACTION_STOP",
        "start_recorded_time": start_time,
        "stop_recorded_time": stop_time,
        "recorded_time_field": time_key,
        "duration_s": (stop_time - start_time) * (1e-9 if time_key.endswith("_ns") else 1.0),
        "start_event_record": start,
        "stop_event_record": stop,
    }
    if byte_key is not None:
        def byte_value(row: dict[str, Any]) -> int:
            if byte_key in row:
                return int(row[byte_key])
            return int(row["boundary"][byte_key])
        result.update({
            "start_byte_exclusive": byte_value(start),
            "stop_byte_inclusive": byte_value(stop),
            "byte_field": byte_key,
            "raw_slice_rule": "decode complete COBS records with record end > start and <= stop",
        })
    return result


def bind_protocol_windows(fusion: Path, result_dir: Path) -> Path:
    """Materialize all action authority before any numeric payload is opened."""
    fusion, result_dir = Path(fusion), Path(result_dir)
    c1_capture = fusion / "logs/v47_ten_node_body_calibration_20260814_093601"
    c2_capture = fusion / "datasets/phase2_calibration/phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
    c3_capture = fusion / "datasets/phase3_targeted_upper_arm/phase3_upper_arm_twist_20260822T091236Z_upper_arm_20260822T091236Z"

    c1 = _event_pair(
        c1_capture / "ACTION_EVENTS.jsonl", "initial_still", action_key="action",
        time_key="monotonic", byte_key=None, attempt=2,
    )
    c1_accounting = _load_json(c1_capture / "analysis_body_fusion_v2/EVENT_ACCOUNTING.json")
    formal = _load_json(c1_capture / "FORMAL_T0.json")
    c1_start_ns = int(c1_accounting["formal_global_start_ns"]) + int(round(
        (float(c1["start_recorded_time"]) - float(formal["formal_t0_monotonic"])) * 1e9
    ))
    c1_stop_ns = int(c1_accounting["formal_global_start_ns"]) + int(round(
        (float(c1["stop_recorded_time"]) - float(formal["formal_t0_monotonic"])) * 1e9
    ))
    c1.update({
        "semantic_name": "neutral-standing",
        "capture": "1",
        "capture_id": c1_capture.name,
        "raw_path": str(c1_capture / "continuous_collector/fusion_host_raw.cobs.bin"),
        "raw_sha256": "a491520739400064db520377ec87a9331feb6274cd42a7e6d9aad57a2b93d56a",
        "typed_ledger_path": str(fusion / LEDGER_REL),
        "start_global_time_ns": c1_start_ns,
        "stop_global_time_ns_exclusive": c1_stop_ns,
        "global_time_mapping": "FORMAL_T0 monotonic delta added to formal_global_start_ns",
    })

    c2_neutral_path = c2_capture / "actions/00_initial_still/rep_01/events/ACTION_EVENTS.jsonl"
    c2_target_path = c2_capture / "actions/02_t_pose/rep_01/events/ACTION_EVENTS.jsonl"
    c2_neutral = _event_pair(
        c2_neutral_path, "00_initial_still", action_key="action_id",
        time_key="host_monotonic_ns", byte_key="continuous_raw_complete_frame_bytes", attempt=None,
    )
    c2 = _event_pair(
        c2_target_path, "02_t_pose", action_key="action_id",
        time_key="host_monotonic_ns", byte_key="continuous_raw_complete_frame_bytes", attempt=None,
    )
    c2.update({
        "semantic_name": "T-pose", "capture": "2", "capture_id": c2_capture.name,
        "raw_path": str(c2_capture / "system/fusion_continuous/fusion_host_raw.cobs.bin"),
        "raw_sha256": "74c1fdbbe7c302bc21b0665bff50137e84537946a347ea11133e1e6751c84268",
        "promoted_manifest": str(c2_capture / "actions/02_t_pose/rep_01/manifest/CAPTURE_MANIFEST.json"),
        "promoted_manifest_sha256": _sha256(c2_capture / "actions/02_t_pose/rep_01/manifest/CAPTURE_MANIFEST.json"),
        "promoted_attempt": 3,
    })
    c2_neutral.update({"semantic_name": "neutral-standing initialization", "capture": "2"})

    c3_events = c3_capture / "ACTION_EVENTS.jsonl"
    c3_neutral = _event_pair(
        c3_events, "S00_INITIAL_STILL", action_key="action_id",
        time_key="monotonic_ns", byte_key="raw_bytes_written", attempt=None,
    )
    c3 = _event_pair(
        c3_events, "PA_D01", action_key="action_id",
        time_key="monotonic_ns", byte_key="raw_bytes_written", attempt=None,
    )
    c3_definition = c3_capture / "actions/PA_D01/ACTION_DEFINITION.json"
    c3.update({
        "semantic_name": "standing-arms", "capture": "3", "capture_id": c3_capture.name,
        "raw_path": str(c3_capture / "system/fusion_continuous/fusion_host_raw.cobs.bin"),
        "raw_sha256": "e26acb54eab7b26eeec348a53c14e1f9459f676ecbfdd961ada8cb74b60e6b55",
        "action_definition": str(c3_definition),
        "action_definition_sha256": _sha256(c3_definition),
        "protocol_family": _load_json(c3_definition)["family"],
        "protocol_pose_requirements": _load_json(c3_definition)["pose_requirements"],
    })
    c3_neutral.update({"semantic_name": "neutral-standing initialization", "capture": "3"})

    binding = {
        "schema": SCHEMA,
        "checkpoint": CHECKPOINT,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "selection_policy": {
            "authoritative_markers_only": True,
            "candidate_enumeration": False,
            "coverage_ranking": False,
            "payload_scoring": False,
            "inferred_onset_or_offset": False,
            "retuning": False,
            "mapping": [
                "Capture 1 -> accepted initial_still attempt 2 -> neutral-standing",
                "Capture 2 -> final promoted 02_t_pose rep_01 attempt 3 -> T-pose",
                "Capture 3 -> PA_D01 first development BENT_POSE_A block -> standing-arms",
            ],
        },
        "windows": {"C1_NEUTRAL": c1, "C2_T_POSE": c2, "C3_STANDING_ARMS": c3},
        "initialization_windows": {"C1": c1, "C2": c2_neutral, "C3": c3_neutral},
        "real_measurement_payload_opened": False,
    }
    path = result_dir / "PROTOCOL_WINDOW_BINDING.json"
    _dump(path, binding)
    return path


def _iter_raw_slice(path: Path, start: int, stop: int) -> Iterator[tuple[int, int, bytes]]:
    if start < 0 or stop <= start or stop > path.stat().st_size:
        raise ValueError("invalid raw slice")
    with path.open("rb") as handle:
        handle.seek(start)
        payload = handle.read(stop - start)
    cursor = 0
    for encoded in payload.split(b"\0")[:-1]:
        raw_start = start + cursor
        raw_end = raw_start + len(encoded) + 1
        cursor += len(encoded) + 1
        if encoded:
            yield raw_start, raw_end, encoded
    # A host-monotonic marker can be recorded while one COBS frame is still in
    # flight.  Keep the marker byte count exact and exclude that one
    # boundary-straddling partial record; never move the action boundary.


def _cobs_decode(encoded: bytes) -> bytes:
    output = bytearray(); cursor = 0
    while cursor < len(encoded):
        code = encoded[cursor]; cursor += 1
        if code == 0 or cursor + code - 1 > len(encoded):
            raise ValueError("invalid COBS")
        output.extend(encoded[cursor:cursor + code - 1]); cursor += code - 1
        if code != 0xFF and cursor < len(encoded):
            output.append(0)
    return bytes(output)


def _envelope(encoded: bytes) -> tuple[int, str, int, int, bytes]:
    raw = _cobs_decode(encoded)
    body, expected = raw[:-2], struct.unpack_from("<H", raw, len(raw) - 2)[0]
    if binascii.crc_hqx(body, 0xFFFF) != expected:
        raise ValueError("CRC")
    magic, version, kind, node_id, length, sequence, master_ms = HEADER.unpack_from(body)
    payload = body[HEADER.size:]
    if magic != 0x5342 or version != 1 or len(payload) != length:
        raise ValueError("envelope contract")
    return int(kind), f"BSF{node_id:04X}", int(sequence), int(master_ms), payload


def _decode_raw_window(path: Path, start: int, stop: int) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    imu_rows: dict[str, list[tuple[Any, ...]]] = {node: [] for node in NODES}
    uwb_rows: dict[str, list[tuple[Any, ...]]] = {node: [] for node in NODES}
    errors = Counter(); frames = Counter(); origins: dict[str, int] = {}
    last_timer: dict[tuple[str, str], int] = {}; boots = Counter()
    for raw_start, raw_end, encoded in _iter_raw_slice(path, start, stop):
        try:
            kind, node, envelope_sequence, master_ms, payload = _envelope(encoded)
            if node not in imu_rows or kind not in (1, 3):
                continue
            frames[(node, kind)] += 1
            if kind == 3:
                version, count, sequence, base_us, temp = IMU_HEADER.unpack_from(payload)
                if version != 7 or not 1 <= count <= 16 or len(payload) != IMU_HEADER.size + count * IMU_SAMPLE.size:
                    raise ValueError("IMU contract")
                samples = [IMU_SAMPLE.unpack_from(payload, IMU_HEADER.size + i * IMU_SAMPLE.size) for i in range(count)]
                timer = int(base_us + samples[-1][0]); key = (node, "IMU")
                if key in last_timer and timer < last_timer[key]: boots[key] += 1
                last_timer[key] = timer
                origins.setdefault(node, timer)
                for sample_index, (delta, ax, ay, az, gx, gy, gz) in enumerate(samples):
                    sample_us = int(base_us + delta)
                    imu_rows[node].append((
                        int(boots[key]), (int(sequence) + sample_index) & 0xFFFF, sample_us, 0, 0,
                        master_ms, int(base_us), int(delta), (ax, ay, az), (gx, gy, gz), int(temp),
                        raw_end, raw_start, raw_end, sample_index, 1,
                    ))
            else:
                if len(payload) != 184:
                    raise ValueError("UWB length")
                version, payload_kind, declared, packet_sequence, node_ms = struct.unpack_from("<BBHII", payload)
                if (version, payload_kind, declared) != (7, 1, 184):
                    raise ValueError("UWB contract")
                body, capture = payload[12:102], payload[102:]
                strobe_us = int(struct.unpack_from("<Q", capture, 8)[0]); key = (node, "UWB")
                if key in last_timer and strobe_us < last_timer[key]: boots[key] += 1
                last_timer[key] = strobe_us
                uwb_rows[node].append((
                    int(boots[key]), int(struct.unpack_from("<I", body)[0]), strobe_us, 0, 0,
                    master_ms, int(node_ms), int(packet_sequence), int(struct.unpack_from("<I", body)[0]),
                    int.from_bytes(body[4:9], "little"), int(struct.unpack_from("<H", body, 9)[0]),
                    tuple(body[16:24]), tuple(body[24:32]), struct.unpack_from("<8H", body, 32),
                    struct.unpack_from("<8H", body, 48), tuple(body[64:72]), struct.unpack_from("<8h", body, 72),
                    int(body[88]), int(body[89]), int(struct.unpack_from("<Q", capture)[0]), strobe_us,
                    raw_end, raw_start, raw_end, 1,
                ))
        except (ValueError, struct.error, IndexError) as exc:
            errors[type(exc).__name__ + ":" + str(exc)] += 1
    missing = sorted(set(NODES) - set(origins))
    if missing:
        raise RuntimeError(f"protocol slice lacks IMU origin for {missing}")
    with path.open("rb") as handle:
        handle.seek(start)
        raw_slice = handle.read(stop - start)
    trailing_partial_bytes = 0 if not raw_slice or raw_slice[-1] == 0 else len(raw_slice.rsplit(b"\0", 1)[-1])
    output: dict[str, dict[str, np.ndarray]] = {}
    for node in NODES:
        imu = np.asarray(imu_rows[node], dtype=IMU_DTYPE)
        uwb = np.asarray(uwb_rows[node], dtype=UWB_DTYPE)
        imu["global_time_ns"] = (imu["node_timer_us"].astype(np.int64) - origins[node]) * 1000
        uwb["global_time_ns"] = (uwb["node_timer_us"].astype(np.int64) - origins[node]) * 1000
        output[node] = {"imu": imu, "uwb": uwb}
    return output, {
        "raw_path": str(path), "raw_sha256": _sha256(path),
        "start_byte_exclusive": start, "stop_byte_inclusive": stop,
        "slice_bytes": stop - start, "decoded_frames_by_node_kind": {
            f"{node}:kind{kind}": count for (node, kind), count in sorted(frames.items())
        },
        "decode_errors": dict(errors), "node_timer_origin_us": origins,
        "trailing_boundary_partial_record_bytes_excluded": trailing_partial_bytes,
        "boundary_partial_record_policy": "event byte marker unchanged; decode only complete COBS records wholly inside bracket",
        "time_axis": "(node TIMER2 us - last sample of first post-ACTION_START IMU batch) * 1000 ns",
        "master_ms_used_as_measurement_time": False,
    }


def _load_c1_window(fusion: Path, binding: dict[str, Any]) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    ledger = fusion / LEDGER_REL
    start, stop = int(binding["start_global_time_ns"]), int(binding["stop_global_time_ns_exclusive"])
    output: dict[str, dict[str, np.ndarray]] = {}
    rows_audit = {}
    for node in NODES:
        output[node] = {}
        for modality in ("imu", "uwb"):
            mapped, metadata = _stored_npy_memmap(ledger, f"{modality}_{node}.npy")
            left, right = _slice_indices(mapped, start, stop)
            rows = np.array(mapped[left:right], copy=True)
            rows["global_time_ns"] -= start
            output[node][modality] = rows
            rows_audit[f"{node}:{modality}"] = {"rows": len(rows), "left": left, "right": right, **metadata}
            del mapped
    return output, {
        "typed_ledger_path": str(ledger), "typed_ledger_sha256": _sha256(ledger),
        "source_start_global_time_ns": start, "source_stop_global_time_ns_exclusive": stop,
        "normalized_time_origin_ns": start, "member_rows": rows_audit,
    }


def _duration_ns(binding: dict[str, Any]) -> int:
    return int(round(float(binding["duration_s"]) * 1e9))


def _load_frozen_parameters(fusion: Path) -> tuple[dict[str, Any], dict[str, float], np.ndarray, dict[str, np.ndarray]]:
    source = fusion / FROZEN_RESULT_REL / "SESSION_LOCAL_REAL_PROFILE.json"
    profile = _load_json(source)
    sigma = {key: float(row["effective_sigma_m"]) for key, row in profile["uwb_noise_by_link"].items()}
    translation = np.asarray(profile["initial_translation_nuisance_fit"]["fitted_translation_v4_m"], float)
    matrices = {
        node: np.asarray(profile["frame_hypothesis"]["BSF31CC" if node == "BSF31CC" else "common_nine"]["matrix_register_to_device"], float)
        for node in NODES
    }
    return profile, sigma, translation, matrices


def _initial_from_exact_neutral(
    fusion: Path, neutral: dict[str, dict[str, np.ndarray]], duration_ns: int,
    matrices: dict[str, np.ndarray], translation: np.ndarray,
):
    means_acc: dict[str, np.ndarray] = {}; means_gyro: dict[str, np.ndarray] = {}
    for node in NODES:
        rows = neutral[node]["imu"]
        chosen = rows[(rows["status"] == 1) & (rows["global_time_ns"] >= 0) & (rows["global_time_ns"] < duration_ns)]
        acc = (matrices[node] @ (chosen["acc_raw"].astype(float) / 2048.0 * 9.80665).T).T
        gyro = (matrices[node] @ np.deg2rad(chosen["gyro_raw"].astype(float) / 16.384).T).T
        means_acc[node] = np.mean(acc, axis=0); means_gyro[node] = np.mean(gyro, axis=0)
    rotations = {node: _orientation_from_gravity_and_skin(node, means_acc[node]) for node in NODES}
    model = corrected_body_model(
        fusion, identity_mapping=IDENTITY,
        identity_provenance="R6A2B_PROTOCOL_CAPTURE_EXPLICIT_IDENTITY",
    )
    calibration, generated_profile, noise = _build_session_calibration(fusion, model, rotations)
    initial = replace(
        _initial_state(model, calibration, rotations, means_acc, means_gyro),
        root_translation_model_m=translation.copy(),
    )
    return model, calibration, noise, initial, {
        "method": "full exact protocol-defined neutral-standing ACTION_START/ACTION_STOP interval",
        "subwindow_search": False, "payload_ranking": False,
        "mean_specific_force_device_mps2": {node: means_acc[node].tolist() for node in NODES},
        "mean_gyro_device_rad_s": {node: means_gyro[node].tolist() for node in NODES},
        "initial_segment_rotation_model_from_device": {node: rotations[node].tolist() for node in NODES},
        "generated_profile_slot_view_sha256": hashlib.sha256(json.dumps(
            generated_profile["executable_slot_view"], sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest(),
    }


def _input_diagnostics(streams: dict[str, dict[str, np.ndarray]], duration_ns: int) -> dict[str, Any]:
    per_node = {}; totals = Counter()
    for node in NODES:
        imu = streams[node]["imu"]
        imu = imu[(imu["status"] == 1) & (imu["global_time_ns"] >= 0) & (imu["global_time_ns"] < duration_ns)]
        uwb = streams[node]["uwb"]
        uwb = uwb[(uwb["status"] == 1) & (uwb["global_time_ns"] >= 0) & (uwb["global_time_ns"] < duration_ns)]
        valid = sum(int(value).bit_count() for value in uwb["valid_mask"])
        per_node[node] = {
            "segment": IDENTITY[node], "accepted_imu_rows": len(imu),
            "accepted_uwb_sweeps": len(uwb), "valid_uwb_scalars": valid,
            "imu_first_ns": int(imu["global_time_ns"][0]), "imu_last_ns": int(imu["global_time_ns"][-1]),
            "imu_nonpositive_dt": int(np.sum(np.diff(imu["global_time_ns"].astype(np.int64)) <= 0)),
        }
        totals.update(accepted_imu_rows=len(imu), accepted_uwb_sweeps=len(uwb), valid_uwb_scalars=valid)
    return {"per_node": per_node, "totals": dict(totals)}


def _plot_summary(result_dir: Path, model, arrays: dict[str, dict[str, dict[str, np.ndarray]]]) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    labels = list(arrays)
    figure, axes = plt.subplots(len(labels), 2, figsize=(14, 5 * len(labels)))
    for row, label in enumerate(labels):
        for variant, values in arrays[label].items():
            root = values["root_position_m"]
            axes[row, 0].plot(root[:, 0], root[:, 1], label=variant, linewidth=1)
        full = arrays[label]["FULL_FUSION"]
        for index, joint in enumerate(model.joint_ids):
            axes[row, 1].plot(full["time_s"], np.degrees(np.linalg.norm(full["joint_rotation_rotvec"][:, index], axis=1)), label=joint, linewidth=.7)
        axes[row, 0].set(title=f"{label}: root XY", xlabel="V4 X [m]", ylabel="V4 Y [m]")
        axes[row, 1].set(title=f"{label}: full-fusion joint rotvec norms", xlabel="time [s]", ylabel="degrees")
        for axis in axes[row]: axis.grid(True, alpha=.25); axis.legend(fontsize=6, ncol=2)
    figure.suptitle("Exact protocol windows | frozen R6A2B estimator | real IMU+UWB | no ground truth", fontsize=10)
    figure.tight_layout()
    path = result_dir / "PROTOCOL_WINDOWS_SUMMARY.png"; figure.savefig(path, dpi=160); plt.close(figure)
    return path


def _seal(result_dir: Path) -> None:
    files = sorted(path for path in result_dir.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (result_dir / "SHA256SUMS").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in files), encoding="utf-8"
    )


def run_protocol_windows(fusion: Path, result_dir: Path) -> Path:
    fusion, result_dir = Path(fusion), Path(result_dir)
    binding_path = result_dir / "PROTOCOL_WINDOW_BINDING.json"
    if not binding_path.exists():
        raise RuntimeError("bind protocol windows before opening real payload")
    binding = _load_json(binding_path)
    if binding.get("schema") != SCHEMA or binding.get("real_measurement_payload_opened"):
        raise RuntimeError("invalid protocol binding")
    # This superseded runner targets calibration intervals as if they were
    # action evaluations.  Exercise the common real-profile gate before any of
    # those payloads can be decoded.
    from biospur_fusion.root_r6a2b.real_profile import validate_real_profile
    profile_path = result_dir / "REAL_SUBJECT_SESSION_CALIBRATION_PROFILE.json"
    profile = _load_json(profile_path) if profile_path.exists() else None
    clock_contract = _load_json(
        fusion / "logs/root_r6a1c_deferred_measurement_bridge_20260825T102823Z/"
        "WORLD_FRAME_BRIDGE_CONTRACT.json"
    )
    boot_epochs = {
        node: int(row["boot_epoch"])
        for node, row in clock_contract["clock_relationships"]["models"].items()
    }
    validation = validate_real_profile(
        profile,
        expected_capture_id="v47_ten_node_body_calibration_20260814_093601",
        expected_session_id="v47_ten_node_body_calibration_20260814_093601",
        expected_boot_epochs=boot_epochs,
        action_authority={"role": "CALIBRATION", "sample_access_authorized_in_task": False},
    )
    if not validation.authorized:
        raise RuntimeError("protocol-window action execution refused: " + ",".join(validation.failures))
    windows_binding = binding["windows"]; init_binding = binding["initialization_windows"]

    c1, c1_audit = _load_c1_window(fusion, windows_binding["C1_NEUTRAL"])
    c2_raw = Path(windows_binding["C2_T_POSE"]["raw_path"])
    c2_neutral, c2_neutral_audit = _decode_raw_window(
        c2_raw, int(init_binding["C2"]["start_byte_exclusive"]), int(init_binding["C2"]["stop_byte_inclusive"]),
    )
    c2, c2_audit = _decode_raw_window(
        c2_raw, int(windows_binding["C2_T_POSE"]["start_byte_exclusive"]), int(windows_binding["C2_T_POSE"]["stop_byte_inclusive"]),
    )
    c3_raw = Path(windows_binding["C3_STANDING_ARMS"]["raw_path"])
    c3_neutral, c3_neutral_audit = _decode_raw_window(
        c3_raw, int(init_binding["C3"]["start_byte_exclusive"]), int(init_binding["C3"]["stop_byte_inclusive"]),
    )
    c3, c3_audit = _decode_raw_window(
        c3_raw, int(windows_binding["C3_STANDING_ARMS"]["start_byte_exclusive"]), int(windows_binding["C3_STANDING_ARMS"]["stop_byte_inclusive"]),
    )
    windows = {"C1_NEUTRAL": c1, "C2_T_POSE": c2, "C3_STANDING_ARMS": c3}
    neutral = {"C1_NEUTRAL": c1, "C2_T_POSE": c2_neutral, "C3_STANDING_ARMS": c3_neutral}

    frozen_profile, sigma_by_link, frozen_translation, matrices = _load_frozen_parameters(fusion)
    frozen_profile_path = fusion / FROZEN_RESULT_REL / "SESSION_LOCAL_REAL_PROFILE.json"
    source_code = fusion / "src/biospur_fusion/root_r6a2b/real_shadow.py"
    parameter_binding = {
        "schema": "biospur-root-r6a2b-frozen-parameter-binding-v1",
        "source_profile": str(frozen_profile_path), "source_profile_sha256": _sha256(frozen_profile_path),
        "estimator_source": str(source_code), "estimator_source_sha256": _sha256(source_code),
        "checkpoint": CHECKPOINT, "estimator_variants": [
            "IMU_ONLY", "UWB_ONLY_DIAGNOSTIC", "FULL_FUSION", "FULL_FUSION_HEALTH_DISABLED",
        ],
        "keyframe_period_ms": 50, "per_link_uwb_sigma_m": sigma_by_link,
        "initial_translation_v4_m": frozen_translation.tolist(),
        "frame_matrices": {node: matrices[node].tolist() for node in NODES},
        "numeric_parameter_retuning": False, "uwb_sigma_refit": False,
        "initial_translation_refit": False, "frame_hypothesis_reselection": False,
        "neutral_initial_condition": "capture-local full exact neutral action; not a fitted calibration-slot write",
        "immutable_87_slot_registry_sha256": frozen_profile["immutable_87_slot_registry"]["sha256"],
    }
    _dump(result_dir / "FROZEN_PARAMETER_BINDING.json", parameter_binding)

    registry = registry_from_sealed_addendum(fusion)
    variants = tuple(parameter_binding["estimator_variants"])
    arrays: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    summaries: dict[str, dict[str, dict[str, Any]]] = {}
    innovations: dict[str, dict[str, list[dict[str, Any]]]] = {}
    health: dict[str, dict[str, list[dict[str, Any]]]] = {}
    uwb_accounting = {}; init_audit = {}; input_diag = {}
    model_ref = None
    for label, streams in windows.items():
        duration_ns = _duration_ns(windows_binding[label])
        init_duration_ns = _duration_ns(init_binding["C" + windows_binding[label]["capture"]])
        model, calibration, noise, initial, init_row = _initial_from_exact_neutral(
            fusion, neutral[label], init_duration_ns, matrices, frozen_translation,
        )
        model_ref = model; init_audit[label] = init_row; input_diag[label] = _input_diagnostics(streams, duration_ns)
        measurements, uwb_accounting[label] = _flatten_uwb(
            streams, 0, duration_ns, sigma_by_link, label,
        )
        arrays[label] = {}; summaries[label] = {}; innovations[label] = {}; health[label] = {}
        for variant in variants:
            print(f"running {label} {variant}", flush=True)
            values, summary, residual_rows, health_rows = _run_variant(
                model, calibration, registry, initial, streams, matrices, measurements, noise,
                0, duration_ns, variant,
            )
            arrays[label][variant] = values; summaries[label][variant] = summary
            innovations[label][variant] = residual_rows; health[label][variant] = health_rows
    assert model_ref is not None

    residuals = _residual_summary(innovations, uwb_accounting, summaries)
    ablation = _ablation_summary(arrays, summaries, residuals)
    _dump(result_dir / "REAL_INPUT_ACCOUNTING.json", {
        "schema": "biospur-root-r6a2b-protocol-input-accounting-v1",
        "selection_bound_before_payload": True, "candidate_or_payload_ranking": False,
        "windows": input_diag, "decoder_audits": {
            "C1_NEUTRAL": c1_audit, "C2_NEUTRAL_INITIALIZATION": c2_neutral_audit,
            "C2_T_POSE": c2_audit, "C3_NEUTRAL_INITIALIZATION": c3_neutral_audit,
            "C3_STANDING_ARMS": c3_audit,
        },
        "uwb_scalar_accounting": uwb_accounting,
        "initialization": init_audit, "held_out_payload_opened": False,
    })
    _dump(result_dir / "REAL_STATE_SUMMARY.json", {"schema": "biospur-root-r6a2b-protocol-state-summary-v1", "windows": summaries})
    _dump(result_dir / "REAL_RESIDUAL_AND_NIS_SUMMARY.json", residuals)
    _dump(result_dir / "REAL_ABLATION_SUMMARY.json", ablation)
    _write_health_csv(result_dir / "REAL_HEALTH_TIMELINE.csv", health, summaries)
    _save_state_npz(result_dir / "REAL_STATE_TIMESERIES.npz", model_ref, arrays)
    plot = _plot_summary(result_dir, model_ref, arrays)
    animations = []
    for label in windows:
        path = result_dir / f"{label}_REAL_FULL_FUSION_3D.mp4"
        _animate_skeleton(path, arrays[label]["FULL_FUSION"], (("3D oblique", 25, -60),))
        animations.append(path.name)

    exact = {}
    for label, row in windows_binding.items():
        summary = summaries[label]["FULL_FUSION"]
        exact[label] = {
            "capture": row["capture"], "semantic_name": row["semantic_name"],
            "action_id": row["action_id"], "attempt": row.get("promoted_attempt", row.get("attempt")),
            "duration_s": row["duration_s"],
            "accepted_imu_rows": input_diag[label]["totals"]["accepted_imu_rows"],
            "valid_uwb_scalars": input_diag[label]["totals"]["valid_uwb_scalars"],
            "full_fusion_finite": summary["finite_state_execution"],
            "root_displacement_m": summary["root_displacement_m"],
            "root_path_length_m": summary["root_path_length_m"],
            "uwb_nonzero_update_steps": summary["uwb_correction_nonzero_steps"],
            "uwb_accepted": residuals["windows"][label]["FULL_FUSION"]["observation_accounting"]["accepted"],
            "uwb_rejected": residuals["windows"][label]["FULL_FUSION"]["observation_accounting"]["rejected"],
        }
    ledger = fusion / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json"
    verification = {
        "schema": "biospur-root-r6a2b-protocol-independent-verification-v1",
        "checks": {
            "exact_three_authoritative_windows": {"pass": set(windows_binding) == {"C1_NEUTRAL", "C2_T_POSE", "C3_STANDING_ARMS"}},
            "no_window_inference_or_ranking": {"pass": all(not binding["selection_policy"][key] for key in (
                "candidate_enumeration", "coverage_ranking", "payload_scoring", "inferred_onset_or_offset"))},
            "frozen_numeric_parameters": {"pass": not any(parameter_binding[key] for key in (
                "numeric_parameter_retuning", "uwb_sigma_refit", "initial_translation_refit", "frame_hypothesis_reselection"))},
            "raw_hashes_match": {"pass": all(_sha256(Path(row["raw_path"])) == row["raw_sha256"] for row in windows_binding.values())},
            "immutable_87_slot_ledger": {"pass": _sha256(ledger) == parameter_binding["immutable_87_slot_registry_sha256"], "sha256": _sha256(ledger)},
            "real_states_finite": {"pass": all(summaries[label][variant]["finite_state_execution"] for label in summaries for variant in variants)},
            "real_uwb_updated_full_state": {"pass": all(summaries[label]["FULL_FUSION"]["uwb_correction_nonzero_steps"] > 0 for label in summaries)},
            "held_out_payload_closed": {"pass": True},
        },
    }
    verification["overall_pass"] = all(row["pass"] for row in verification["checks"].values())
    _dump(result_dir / "INDEPENDENT_VERIFICATION.json", verification)
    result = {
        "schema": "biospur-root-r6a2b-protocol-rerun-final-v1",
        "checkpoint": CHECKPOINT, "real_bounded_shadow_executed": True,
        "exact_protocol_windows_used": True, "heuristic_window_selection_used": False,
        "estimator_retuned": False, "immutable_calibration_slots_written": 0,
        "external_ground_truth_available": False, "external_accuracy_claimed": False,
        "production_fusion_authorized": False, "held_out_payload_opened": False,
        "windows": exact, "all_verification_checks_pass": verification["overall_pass"],
        "artifacts": {"state_timeseries": "REAL_STATE_TIMESERIES.npz", "summary_plot": plot.name, "animations": animations},
        "interpretation": "execution/provenance PASS only; physical performance remains diagnostic because no external ground truth is present and the frozen shadow is not production-authorized",
    }
    _dump(result_dir / "FINAL_RESULT.json", result)
    lines = [
        "# R6A2B exact protocol-window rerun", "",
        "The frozen real-shadow estimator was rerun without numeric retuning on the recorded protocol brackets only.", "",
        "| Window | Recorded action | Duration | IMU rows | Valid UWB scalars | Root displacement | UWB accepted/rejected |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for label, row in exact.items():
        lines.append(
            f"| {label} | {row['action_id']} | {row['duration_s']:.6f} s | {row['accepted_imu_rows']} | "
            f"{row['valid_uwb_scalars']} | {row['root_displacement_m']:.3f} m | {row['uwb_accepted']}/{row['uwb_rejected']} |"
        )
    lines += ["", "This is an execution/provenance result, not an accuracy result. There is no external ground truth, no calibration-slot write, and no production authorization.", ""]
    (result_dir / "FINAL_RESULT.md").write_text("\n".join(lines), encoding="utf-8")
    _seal(result_dir)
    return result_dir / "FINAL_RESULT.json"
