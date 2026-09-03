"""Streaming COBS/CRC IMU decoder; UWB payloads are never parsed."""
from __future__ import annotations

import binascii
import hashlib
import struct
from collections import Counter
from pathlib import Path
from typing import Iterator

import numpy as np

from .config import NODE_ORDER, CaptureSpec

HEADER = struct.Struct("<HBBHHIQ")
IMU_HEADER = struct.Struct("<BBHQh")
IMU_SAMPLE = struct.Struct("<Hhhhhhh")
DTYPE = np.dtype([
    ("timestamp_us", "<u8"), ("sequence", "<u2"),
    ("acc", "<i2", (3,)), ("gyro", "<i2", (3,)),
    ("raw_end", "<u8"),
])


class DecodeError(ValueError):
    pass


def cobs_decode(encoded: bytes) -> bytes:
    output = bytearray(); i = 0
    while i < len(encoded):
        code = encoded[i]; i += 1
        if code == 0 or i + code - 1 > len(encoded):
            raise DecodeError("invalid COBS")
        output.extend(encoded[i:i+code-1]); i += code-1
        if code != 0xFF and i < len(encoded):
            output.append(0)
    return bytes(output)


def iter_records(path: Path, chunk_size: int = 4 << 20) -> Iterator[tuple[int, bytes, bool]]:
    pending = bytearray(); consumed = 0
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            pending.extend(chunk); start = 0
            while True:
                boundary = pending.find(0, start)
                if boundary < 0:
                    if start:
                        consumed += start; del pending[:start]
                    break
                encoded = bytes(pending[start:boundary])
                end = consumed + boundary + 1; start = boundary + 1
                if encoded:
                    yield end, encoded, True
        if pending:
            yield consumed + len(pending), bytes(pending), False


def envelope(encoded: bytes) -> tuple[int, str, int, bytes]:
    raw = cobs_decode(encoded)
    if len(raw) < HEADER.size + 2:
        raise DecodeError("short envelope")
    body = raw[:-2]; expected = struct.unpack_from("<H", raw, len(raw)-2)[0]
    if binascii.crc_hqx(body, 0xFFFF) != expected:
        raise DecodeError("CRC")
    magic, version, kind, node_id, payload_len, sequence, master_ms = HEADER.unpack_from(body)
    payload = body[HEADER.size:]
    if magic != 0x5342 or version != 1 or len(payload) != payload_len:
        raise DecodeError("envelope contract")
    return kind, (f"BSF{node_id:04X}" if node_id else "-"), int(master_ms), payload


def _selected(spec: CaptureSpec, end: int, master_ms: int) -> bool:
    if spec.selection_mode == "exclusive_raw_byte_boundary":
        return end > spec.selection_value
    if spec.selection_mode == "minimum_master_ms_for_record_selection_only":
        return master_ms >= spec.selection_value
    raise ValueError(spec.selection_mode)


def _imu_count(payload: bytes) -> int:
    if len(payload) < IMU_HEADER.size:
        raise DecodeError("short IMU")
    version, count, sequence, base, temp = IMU_HEADER.unpack_from(payload)
    if version != 7 or not 1 <= count <= 16 or len(payload) != IMU_HEADER.size + count*IMU_SAMPLE.size:
        raise DecodeError("IMU contract")
    return count


def _decode_imu(payload: bytes, raw_end: int, out: np.ndarray, cursor: int) -> tuple[int, int]:
    version, count, sequence, base, temp = IMU_HEADER.unpack_from(payload)
    if _imu_count(payload) != count:
        raise DecodeError("IMU count")
    last_ts = 0
    for j in range(count):
        delta, ax, ay, az, gx, gy, gz = IMU_SAMPLE.unpack_from(payload, IMU_HEADER.size+j*IMU_SAMPLE.size)
        last_ts = int(base + delta)
        out[cursor+j] = (last_ts, (sequence+j) & 0xFFFF, (ax, ay, az), (gx, gy, gz), raw_end)
    return cursor + count, last_ts


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def decode_capture(spec: CaptureSpec) -> tuple[dict[str, np.ndarray], dict]:
    """Two-pass exact allocation. Only kind=3 payloads receive numeric parsing."""
    counts = Counter(); imu_frames = Counter(); first_anchor = {}
    errors = complete = incomplete_tail = uwb_opaque = other = 0
    for end, encoded, is_complete in iter_records(spec.raw_path):
        if not is_complete:
            incomplete_tail = len(encoded); continue
        complete += 1
        try:
            kind, node, master_ms, payload = envelope(encoded)
            if not _selected(spec, end, master_ms):
                continue
            if kind == 1:
                uwb_opaque += 1
                continue
            if kind != 3 or node not in NODE_ORDER:
                other += 1
                continue
            count = _imu_count(payload)
            counts[node] += count; imu_frames[node] += 1
            if node not in first_anchor:
                base = IMU_HEADER.unpack_from(payload)[3]
                delta = IMU_SAMPLE.unpack_from(payload, IMU_HEADER.size+(count-1)*IMU_SAMPLE.size)[0]
                first_anchor[node] = int(base + delta)
        except (DecodeError, struct.error, ValueError):
            errors += 1
    missing = sorted(set(NODE_ORDER)-set(first_anchor))
    if missing:
        raise RuntimeError(f"fewer than ten IMU streams: {missing}")
    arrays = {node: np.empty(counts[node], DTYPE) for node in NODE_ORDER}
    cursor = Counter(); second_errors = 0
    for end, encoded, is_complete in iter_records(spec.raw_path):
        if not is_complete:
            continue
        try:
            kind, node, master_ms, payload = envelope(encoded)
            if kind != 3 or node not in arrays or not _selected(spec, end, master_ms):
                continue
            cursor[node], _ = _decode_imu(payload, end, arrays[node], cursor[node])
        except (DecodeError, struct.error, ValueError):
            second_errors += 1
    if second_errors != errors:
        raise RuntimeError(f"decoder passes disagree: {errors} != {second_errors}")
    for node in NODE_ORDER:
        if cursor[node] != len(arrays[node]):
            raise RuntimeError(f"allocation mismatch {node}")
    audit = {
        "raw_path": str(spec.raw_path.resolve()), "raw_size": spec.raw_path.stat().st_size,
        "raw_sha256": sha256(spec.raw_path), "complete_transport_records_seen": complete,
        "selected_imu_frames": dict(imu_frames), "selected_imu_samples": dict(counts),
        "decode_errors": errors, "incomplete_tail_bytes": incomplete_tail,
        "uwb_transport_envelopes_skipped_opaque": uwb_opaque,
        "uwb_payload_numeric_fields_parsed": 0, "uwb_numeric_reads": 0,
        "other_selected_transport_frames": other,
        "sample_timestamp_equation": "timestamp_us = base_timer2_ts_us + delta_us",
        "master_ms_sample_time_use": False,
        "selection_mode": spec.selection_mode,
        "selection_value": spec.selection_value,
        "first_frame_last_sample_anchor_us": first_anchor,
    }
    return arrays, audit


def physical(rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    acc = rows["acc"].astype(np.float64) / 2048.0 * 9.80665
    gyr = np.deg2rad(rows["gyro"].astype(np.float64) / 16.384)
    return acc, gyr


def assert_strict_timestamps(times: np.ndarray) -> None:
    if len(times) < 2 or np.any(np.diff(np.asarray(times, dtype=np.int64)) <= 0):
        raise ValueError("timestamps are not strictly monotonic")
