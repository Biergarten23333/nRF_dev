"""Raw-COBS input adapter for a predeclared locked V0 validation action.

Only IMU payload kind 3 is decoded from the action byte bracket.  UWB spatial
fields are never parsed.  Listener Beacon/poll timing and B306 strobe timing
are used solely to map each node TIMER2 clock onto the common time axis.
"""
from __future__ import annotations

from collections import Counter
import binascii
import hashlib
import json
import os
from pathlib import Path
import struct
from typing import Any, Iterator, Mapping

import numpy as np

from .contracts import NODES, sha256_file
from biospur_fusion.time.common_clock import align_capture_bounded, models_as_json


HEADER = struct.Struct("<HBBHHIQ")
IMU_HEADER = struct.Struct("<BBHQh")
IMU_SAMPLE = struct.Struct("<Hhhhhhh")
IMU_DTYPE = np.dtype([
    ("boot_epoch", "<u2"), ("sequence", "<u2"), ("node_timer_us", "<u8"),
    ("global_time_ns", "<i8"), ("global_time_sigma_ns", "<u8"),
    ("master_arrival_ms", "<u8"), ("base_timer2_us", "<u8"),
    ("delta_us", "<u2"), ("acc_raw", "<i2", (3,)), ("gyro_raw", "<i2", (3,)),
    ("temp_raw", "<i2"), ("raw_start_offset", "<u8"), ("raw_end_offset", "<u8"),
    ("raw_sample_index", "u1"), ("status", "u1"),
])

# Keep the production path on the real primitives while giving hostile-access
# tests one explicit interception point for every byte read and seek.
_OS_OPEN = os.open
_OS_CLOSE = os.close
_OS_READ = os.read
_OS_LSEEK = os.lseek


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
    if len(raw) < HEADER.size + 2:
        raise ValueError("short envelope")
    body, expected = raw[:-2], struct.unpack_from("<H", raw, len(raw) - 2)[0]
    if binascii.crc_hqx(body, 0xFFFF) != expected:
        raise ValueError("CRC")
    magic, version, kind, node_id, length, sequence, master_ms = HEADER.unpack_from(body)
    payload = body[HEADER.size:]
    if magic != 0x5342 or version != 1 or len(payload) != length:
        raise ValueError("envelope contract")
    return int(kind), f"BSF{node_id:04X}", int(sequence), int(master_ms), payload


def _read_raw_slice(
    path: Path, start: int, stop: int, expected_sha256: str | None = None,
    *, forbidden_byte_ranges: Iterator[tuple[int, int]] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    path = Path(path).resolve()
    size = path.stat().st_size
    if start < 0 or stop <= start or stop > size:
        raise ValueError("invalid raw byte bracket")
    forbidden = tuple((int(left), int(right)) for left, right in (forbidden_byte_ranges or ()))
    if any(max(start, left) < min(stop, right) for left, right in forbidden):
        raise ValueError("raw byte bracket intersects a forbidden interval")
    fd = _OS_OPEN(path, os.O_RDONLY)
    read_trace: list[list[int]] = []
    seek_trace: list[dict[str, int]] = []
    chunks: list[bytes] = []
    position = 0
    try:
        position = int(_OS_LSEEK(fd, int(start), os.SEEK_SET))
        seek_trace.append({"from_byte": 0, "to_byte": position})
        if position != start:
            raise IOError("raw bounded read seek did not reach requested start")
        while position < stop:
            request = min(1024 * 1024, stop - position)
            part = _OS_READ(fd, request)
            if not part:
                break
            read_trace.append([int(position), int(position + len(part))])
            chunks.append(part)
            position += len(part)
    finally:
        _OS_CLOSE(fd)
    payload = b"".join(chunks)
    if len(payload) != stop - start:
        raise IOError("short bounded raw action read")
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("raw action-slice SHA-256 mismatch")
    return payload, {
        "requested_intervals": [[int(start), int(stop)]],
        "actual_read_intervals": read_trace,
        "actual_seek_operations": seek_trace,
        "all_reads_within_declared_action": len(payload) == stop - start,
        "slice_sha256": digest,
        "slice_sha256_verified": expected_sha256 is not None,
        "complete_container_hash_recalculated": False,
        "complete_container_scan_attempted": False,
        "mmap_used": False,
        "raw_io_call_accounting": {
            "instrumentation_layer": "os.read/os.lseek wrappers",
            "actual_os_open_calls": 1,
            "actual_os_seek_calls": len(seek_trace),
            "actual_os_read_calls": len(read_trace),
            "actual_os_close_calls": 1,
            "actual_read_union_bytes": int(sum(right - left for left, right in read_trace)),
            "container_bytes": int(size),
        },
        "no_full_file_traversal_proven_by_actual_read_union": (
            sum(right - left for left, right in read_trace) < size
        ),
        "forbidden_intervals": [list(row) for row in forbidden],
        "forbidden_interval_bytes_touched": False,
    }


def sha256_action_slice(path: Path, start: int, stop: int) -> str:
    """Hash exactly one declared byte slice and no other container byte."""
    return _read_raw_slice(Path(path), int(start), int(stop))[1]["slice_sha256"]


def _iter_raw_payload(payload: bytes, start: int) -> Iterator[tuple[int, int, bytes]]:
    cursor = 0
    for encoded in payload.split(b"\0")[:-1]:
        raw_start = start + cursor; raw_end = raw_start + len(encoded) + 1
        cursor += len(encoded) + 1
        if encoded:
            yield raw_start, raw_end, encoded


def _decode_imu_only(
    raw_path: Path, start_byte: int, stop_byte: int, models: Mapping[str, Any],
    annotation_start_global_ns: int, annotation_stop_global_ns: int,
    *, expected_slice_sha256: str | None = None,
    forbidden_raw_byte_ranges: Iterator[tuple[int, int]] | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    rows: dict[str, list[tuple[Any, ...]]] = {node: [] for node in NODES}
    counts: Counter[str] = Counter(); errors: Counter[str] = Counter()
    boots: Counter[str] = Counter(); last_timer: dict[str, int] = {}
    payload, raw_access = _read_raw_slice(
        raw_path, start_byte, stop_byte, expected_slice_sha256,
        forbidden_byte_ranges=forbidden_raw_byte_ranges,
    )
    for raw_start, raw_end, encoded in _iter_raw_payload(payload, start_byte):
        try:
            kind, node, _, master_ms, payload = _envelope(encoded)
            if node not in rows:
                counts["non_candidate_node_envelopes_skipped"] += 1; continue
            if kind != 3:
                counts[f"payload_kind_{kind}_envelopes_skipped_without_payload_decode"] += 1
                continue
            version, sample_count, sequence, base_us, temp = IMU_HEADER.unpack_from(payload)
            if (
                version != 7 or not 1 <= sample_count <= 16
                or len(payload) != IMU_HEADER.size + sample_count * IMU_SAMPLE.size
            ):
                raise ValueError("IMU contract")
            for sample_index in range(sample_count):
                delta, ax, ay, az, gx, gy, gz = IMU_SAMPLE.unpack_from(
                    payload, IMU_HEADER.size + sample_index * IMU_SAMPLE.size,
                )
                timer_us = int(base_us + delta)
                previous = last_timer.get(node)
                if previous is not None and timer_us < previous:
                    boots[node] += 1
                last_timer[node] = timer_us
                global_absolute_ns = models[node].map_ns(timer_us)
                normalized_ns = global_absolute_ns - annotation_start_global_ns
                accepted = annotation_start_global_ns <= global_absolute_ns < annotation_stop_global_ns
                rows[node].append((
                    int(boots[node]), (int(sequence) + sample_index) & 0xFFFF, timer_us,
                    normalized_ns, int(round(models[node].sigma_ns)), master_ms,
                    int(base_us), int(delta), (ax, ay, az), (gx, gy, gz), int(temp),
                    raw_start, raw_end, sample_index, int(accepted),
                ))
            counts["imu_envelopes_decoded"] += 1
            counts["imu_samples_decoded"] += sample_count
        except (ValueError, struct.error, IndexError) as exc:
            errors[type(exc).__name__ + ":" + str(exc)] += 1
    output = {node: np.asarray(rows[node], dtype=IMU_DTYPE) for node in NODES}
    missing = [node for node, value in output.items() if np.count_nonzero(value["status"] == 1) < 2]
    if missing:
        raise RuntimeError(f"raw action lacks accepted IMU rows for {missing}")
    node_audit = {}
    for node, value in output.items():
        accepted = value[value["status"] == 1]
        node_audit[node] = {
            "decoded_rows": int(len(value)), "accepted_rows": int(len(accepted)),
            "first_global_time_ns": int(accepted["global_time_ns"][0]),
            "last_global_time_ns": int(accepted["global_time_ns"][-1]),
            "strictly_increasing": bool(np.all(np.diff(accepted["global_time_ns"]) > 0)),
            "boot_epochs": [int(item) for item in np.unique(accepted["boot_epoch"])],
            "payload_sha256": hashlib.sha256(accepted.tobytes()).hexdigest(),
        }
    return output, {
        "raw_byte_start_inclusive": int(start_byte),
        "raw_byte_stop_exclusive": int(stop_byte),
        "raw_slice_bytes": int(stop_byte - start_byte),
        "decode_counts": dict(counts), "decode_errors": dict(errors),
        "nodes": node_audit,
        "decoded_payload_classes": ["TEN_NODE_IMU"],
        "uwb_transport_envelopes_skipped_without_payload_decode": int(
            sum(value for key, value in counts.items() if key.startswith("payload_kind_1_"))
        ),
        "uwb_spatial_fields_decoded": [],
        "range_values_consumed": False,
        "anchor_geometry_consumed": False,
        "raw_access": raw_access,
    }


def _raw_bounds(raw_input: Mapping[str, Any]) -> tuple[int, int]:
    if "start_byte_inclusive" in raw_input and "stop_byte_exclusive" in raw_input:
        return int(raw_input["start_byte_inclusive"]), int(raw_input["stop_byte_exclusive"])
    # Historical metadata names described event-edge semantics, but the values
    # were already consumed as a half-open complete-frame byte interval.
    return int(raw_input["start_byte_exclusive"]), int(raw_input["stop_byte_inclusive"])


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return max(left[0], right[0]) < min(left[1], right[1])


def load_raw_action_imu_only(predeclaration: Mapping[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Open the predeclared raw action only after all lock/role checks pass."""
    raw = Path(str(predeclaration["ledger"])).resolve()
    raw_input = predeclaration["raw_input"]
    start_byte, stop_byte = _raw_bounds(raw_input)
    expected_slice_sha = str(raw_input["slice_sha256"])
    sealed_container_sha = str(
        predeclaration.get("sealed_container_sha256", predeclaration.get("ledger_sha256", ""))
    )
    if len(sealed_container_sha) != 64:
        raise ValueError("sealed container identity must be imported, not recomputed")
    forbidden_ranges = [
        (int(row["start_byte_inclusive"]), int(row["stop_byte_exclusive"]))
        for row in predeclaration.get("forbidden_golf_boxing_raw_byte_ranges", [])
    ]
    golf_boxing_overlap = any(_overlaps((start_byte, stop_byte), row) for row in forbidden_ranges)
    if golf_boxing_overlap:
        raise ValueError("declared action overlaps forbidden Golf/Boxing raw bytes")
    timing = predeclaration["common_clock_timing_sources"]
    event_source = Path(str(predeclaration["authoritative_action_event_source"])).resolve()
    if sha256_file(event_source) != predeclaration["authoritative_action_event_source_sha256"]:
        raise ValueError("authoritative action event source changed")
    start_s = int(raw_input["start_host_monotonic_ns"]) * 1e-9
    stop_s = int(raw_input["stop_host_monotonic_ns_exclusive"]) * 1e-9
    forbidden_timing = [
        (int(row["start_host_monotonic_ns"]), int(row["stop_host_monotonic_ns_exclusive"]))
        for row in predeclaration.get("forbidden_golf_boxing_timing_intervals_ns", [])
    ]
    if not forbidden_timing:
        raise ValueError("predeclaration lacks explicit Golf/Boxing timing intervals")
    selected_host_interval = (
        int(raw_input["start_host_monotonic_ns"]),
        int(raw_input["stop_host_monotonic_ns_exclusive"]),
    )
    if any(_overlaps(selected_host_interval, row) for row in forbidden_timing):
        raise ValueError("declared action overlaps forbidden Golf/Boxing timing interval")
    first_forbidden_raw_byte = min(left for left, _ in forbidden_ranges)
    search_ceiling_fraction = (
        (int(stop_byte) + int(first_forbidden_raw_byte)) / 2.0 / raw.stat().st_size
    )
    if not 0.0 < search_ceiling_fraction < 1.0:
        raise ValueError("invalid safe timing search-ceiling fraction")
    readiness = Path(str(timing["capture_identity_authority"])).resolve()
    models, residual_rows, gate = align_capture_bounded(
        Path(str(timing["fusion_timing_log"])).resolve(),
        Path(str(timing["listener_directory"])).resolve(), readiness,
        start_s, stop_s, NODES,
        expected_readiness_sha256=str(timing["capture_identity_authority_sha256"]),
        search_ceiling_fraction=search_ceiling_fraction,
        forbidden_time_intervals_ns=forbidden_timing,
    )
    if not gate["pass"]:
        raise RuntimeError("Listener-backed common-clock gate failed")
    bridge = gate["action_annotation_bridge"]
    annotation_start = int(round((bridge["listener_global_us_per_host_s"] * start_s + bridge["listener_global_us_intercept"]) * 1000.0))
    annotation_stop = int(round((bridge["listener_global_us_per_host_s"] * stop_s + bridge["listener_global_us_intercept"]) * 1000.0))
    rows, decode = _decode_imu_only(
        raw, start_byte, stop_byte, models, annotation_start, annotation_stop,
        expected_slice_sha256=expected_slice_sha,
    )
    audit = {
        "schema": "biospur-fusion-v0-raw-action-imu-only-access-v1",
        "raw_path": str(raw),
        "sealed_container_sha256_imported": sealed_container_sha,
        "sealed_container_sha256_recomputed": False,
        "action_slice_sha256": decode["raw_access"]["slice_sha256"],
        "action_event_source": str(event_source),
        "action_event_source_sha256": sha256_file(event_source),
        "exact_predeclared_byte_bracket": True,
        "common_clock": {
            "method": "LISTENER_BEACON_POLL_PLUS_B306_UWB_STROBE_TIMER2",
            "measurement_time_source": "B306_TIMER2_MAPPED_BY_LISTENER_UWB_BEACON_NETWORK",
            "master_arrival_used_as_measurement_time": False,
            "models": models_as_json(models), "gate": gate,
            "residual_rows": residual_rows,
            "annotation_start_absolute_global_ns": annotation_start,
            "annotation_stop_absolute_global_ns_exclusive": annotation_stop,
            "normalized_start_global_time_ns": 0,
            "normalized_stop_global_time_ns_exclusive": annotation_stop - annotation_start,
        },
        "decode": decode,
        "opened_payload_classes": [
            "TEN_NODE_IMU", "UWB_BEACON_AND_STROBE_TIMING_METADATA_FOR_COMMON_CLOCK",
        ],
        "uwb_ranging_used": False, "uwb_position_aid_used": False,
        "uwb_calibration_residual_used": False, "uwb_pose_correction_used": False,
        "uwb_derived_spatial_parameters_used": False,
        "uwb_spatial_payload_decoded_or_consumed": False,
        "held_out_payloads_opened": [],
        "ten_node_coverage_complete": set(rows) == set(NODES),
        "access_sentinels": {
            "raw_reads_within_selected_half_open_range": decode["raw_access"]["all_reads_within_declared_action"],
            "complete_container_hash_recalculated": False,
            "complete_container_scan_attempted": False,
            "complete_timing_log_scan_attempted": gate["timing_access"]["complete_file_scan_attempted"],
            "timing_io_instrumented_at_os_call_layer": (
                gate["timing_access"]["raw_io_call_accounting"]["instrumentation_layer"]
                == "os.read/os.lseek on O_RDONLY descriptors; no buffered reader"
            ),
            "all_sequential_timing_rows_within_action_plus_two_superframes": gate[
                "timing_access"
            ]["all_sequential_timing_rows_within_action_plus_two_superframes"],
            "every_binary_search_probe_separately_accounted": gate[
                "timing_access"
            ]["every_binary_search_probe_separately_accounted"],
            "no_full_timing_file_traversal_proven": gate[
                "timing_access"
            ]["no_full_file_traversal_proven_by_actual_read_union"],
            "golf_boxing_timing_interval_bytes_touched": gate[
                "timing_access"
            ]["golf_boxing_timing_interval_bytes_touched"],
            "golf_boxing_byte_range_overlap": golf_boxing_overlap,
            "golf_boxing_measurements_decoded": False,
            "uwb_spatial_payload_opened": False,
        },
        "HISTORICAL_GOLF_BOXING_CONTAINER_BYTES_TOUCHED": "YES",
        "CURRENT_GOAL_GOLF_BOXING_BYTES_TOUCHED": "NO",
        "GOLF_BOXING_MEASUREMENTS_DECODED": "NO",
        "GOLF_BOXING_USED_FOR_TUNING": "NO",
        "GOLF_BOXING_USED_FOR_SCORING": "NO",
    }
    return rows, audit
