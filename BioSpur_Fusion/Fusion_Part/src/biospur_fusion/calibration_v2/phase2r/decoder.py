"""Minimal Phase 2-R decoder for promoted Fusion COBS slices.

Only IMU and UWB measurement records are materialized.  TIMER2 is the
measurement time.  Master arrival is retained solely to route the formal action
window and is never substituted for measurement time.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import struct
from typing import Any

import numpy as np

MAGIC = 0x5342
VERSION = 1
HEADER = struct.Struct("<HBBHHIQ")
CRC = struct.Struct("<H")
KIND_UWB = 1
KIND_IMU = 3


class DecodeError(ValueError):
    pass


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def cobs_decode(encoded: bytes) -> bytes:
    output = bytearray()
    index = 0
    while index < len(encoded):
        code = encoded[index]
        index += 1
        if code == 0 or index + code - 1 > len(encoded):
            raise DecodeError("invalid COBS record")
        output.extend(encoded[index:index + code - 1])
        index += code - 1
        if code != 0xFF and index < len(encoded):
            output.append(0)
    return bytes(output)


@dataclass(frozen=True)
class DecodedWindow:
    nodes: tuple[str, ...]
    imu: dict[str, dict[str, np.ndarray]]
    uwb: dict[str, dict[str, np.ndarray]]
    audit: dict[str, Any]


def _frame(encoded: bytes):
    raw = cobs_decode(encoded)
    if len(raw) < HEADER.size + CRC.size:
        raise DecodeError("short frame")
    body, expected = raw[:-2], CRC.unpack_from(raw, len(raw) - 2)[0]
    if crc16_ccitt_false(body) != expected:
        raise DecodeError("CRC mismatch")
    magic, version, kind, node_id, length, sequence, arrival = HEADER.unpack_from(body)
    payload = body[HEADER.size:]
    if magic != MAGIC or version != VERSION or len(payload) != length:
        raise DecodeError("header or payload mismatch")
    return kind, f"BSF{node_id:04X}", sequence, int(arrival), payload


def decode_promoted_slice(payload: bytes, preparation_s: float, action_s: float) -> DecodedWindow:
    """Decode one complete promoted slice and retain only its formal interval."""
    imu_rows: dict[str, list[tuple]] = defaultdict(list)
    uwb_rows: dict[str, list[tuple]] = defaultdict(list)
    errors = complete = imu_records = uwb_records = 0
    first_arrival: int | None = None
    for encoded in payload.split(b"\0"):
        if not encoded:
            continue
        complete += 1
        try:
            kind, node, _, arrival_ms, body = _frame(encoded)
            first_arrival = arrival_ms if first_arrival is None else min(first_arrival, arrival_ms)
            if kind == KIND_IMU:
                if len(body) < 14:
                    raise DecodeError("short IMU")
                version, count, sequence, base_us, temp = struct.unpack_from("<BBHQh", body)
                if version != 7 or not 1 <= count <= 16 or len(body) != 14 + count * 14:
                    raise DecodeError("bad IMU layout")
                imu_records += 1
                for sample in range(count):
                    delta, ax, ay, az, gx, gy, gz = struct.unpack_from("<Hhhhhhh", body, 14 + sample * 14)
                    imu_rows[node].append((int(base_us + delta), arrival_ms, sequence + sample, ax, ay, az, gx, gy, gz, temp))
            elif kind == KIND_UWB:
                if len(body) != 184:
                    raise DecodeError("bad UWB length")
                version, record_kind, declared, packet_seq, node_ms = struct.unpack_from("<BBHII", body)
                if version != 7 or record_kind != 1 or declared != 184:
                    raise DecodeError("bad UWB layout")
                sweep_body, capture = body[12:102], body[102:]
                sweep = struct.unpack_from("<I", sweep_body)[0]
                anchors = list(sweep_body[16:24])
                ranges = list(struct.unpack_from("<8H", sweep_body, 32))
                tround = list(struct.unpack_from("<8H", sweep_body, 48))
                quality = list(sweep_body[64:72])
                valid_mask = int(sweep_body[88])
                frame_us, strobe_us = struct.unpack_from("<QQ", capture)
                uwb_records += 1
                for rank, (anchor, distance, round_us, q) in enumerate(zip(anchors, ranges, tround, quality)):
                    if anchor == 0xFF or not (valid_mask >> rank) & 1:
                        continue
                    # Individual range time is the captured poll epoch plus its
                    # measured exchange midpoint, never host arrival.
                    measurement_us = int(strobe_us + round_us / 2.0)
                    uwb_rows[node].append((measurement_us, arrival_ms, sweep, anchor, distance, round_us, q, frame_us))
        except (DecodeError, struct.error, IndexError, ValueError):
            errors += 1
    if first_arrival is None:
        raise DecodeError("slice contained no decodable frames")
    formal_start = first_arrival + int(round(preparation_s * 1000))
    formal_stop = formal_start + int(round(action_s * 1000))
    imu: dict[str, dict[str, np.ndarray]] = {}
    for node, rows in imu_rows.items():
        selected = [r for r in rows if formal_start <= r[1] <= formal_stop]
        if not selected:
            continue
        a = np.asarray(selected, dtype=np.float64)
        imu[node] = {
            "timer2_us": a[:, 0].astype(np.int64),
            "master_arrival_ms": a[:, 1].astype(np.int64),
            "sequence": a[:, 2].astype(np.int64),
            "acc_raw": a[:, 3:6].astype(np.float64),
            "gyro_raw": a[:, 6:9].astype(np.float64),
            "temperature_raw": a[:, 9].astype(np.float64),
        }
    uwb: dict[str, dict[str, np.ndarray]] = {}
    for node, rows in uwb_rows.items():
        selected = [r for r in rows if formal_start <= r[1] <= formal_stop]
        if not selected:
            continue
        a = np.asarray(selected, dtype=np.float64)
        uwb[node] = {
            "measurement_time_us": a[:, 0].astype(np.int64),
            "master_arrival_ms": a[:, 1].astype(np.int64),
            "sweep": a[:, 2].astype(np.int64),
            "anchor": a[:, 3].astype(np.int16),
            "range_mm": a[:, 4].astype(np.float64),
            "t_round_us": a[:, 5].astype(np.float64),
            "quality": a[:, 6].astype(np.float64),
            "frame_time_us": a[:, 7].astype(np.int64),
        }
    return DecodedWindow(
        tuple(sorted(imu)), imu, uwb,
        {
            "complete_frames": complete,
            "decode_errors": errors,
            "imu_records": imu_records,
            "uwb_records": uwb_records,
            "formal_window_routing": "master_arrival_marker_diagnostic_only",
            "measurement_time": "node_TIMER2_us",
            "preparation_s": preparation_s,
            "formal_action_s": action_s,
            "formal_boundary_uncertainty_ms": 150.0,
        },
    )
