from __future__ import annotations

from dataclasses import dataclass
import struct
import numpy as np

from biospur_fusion.calibration_v2.phase2r.decoder import (
    CRC, HEADER, KIND_IMU, KIND_UWB, MAGIC, VERSION, cobs_decode, crc16_ccitt_false,
)
from .types import ImuSample


@dataclass(frozen=True)
class DecodeAudit:
    bytes_streamed: int
    routing_headers: int
    imu_samples: int
    imu_numeric_scalars: int
    uwb_numeric_scalars: int
    uwb_arrays: int
    crc_or_layout_errors: int
    duplicate_samples: int
    boot_resets: int


def decode_imu_only(payload: bytes, *, include_start_s: float, include_stop_s: float) -> tuple[list[ImuSample], DecodeAudit]:
    """Decode IMU records while rejecting UWB immediately after the common header."""
    rows: list[tuple] = []
    headers = errors = duplicates = resets = 0
    first_arrival: int | None = None
    for encoded in payload.split(b"\0"):
        if not encoded:
            continue
        try:
            raw = cobs_decode(encoded)
            if len(raw) < HEADER.size + CRC.size:
                raise ValueError("short")
            body, expected = raw[:-2], CRC.unpack_from(raw, len(raw)-2)[0]
            if crc16_ccitt_false(body) != expected:
                raise ValueError("crc")
            magic, version, kind, node_id, length, _, arrival_ms = HEADER.unpack_from(body)
            if magic != MAGIC or version != VERSION or len(body)-HEADER.size != length:
                raise ValueError("header")
            headers += 1
            first_arrival = int(arrival_ms) if first_arrival is None else min(first_arrival, int(arrival_ms))
            if kind == KIND_UWB:
                continue
            if kind != KIND_IMU:
                continue
            imu = memoryview(body)[HEADER.size:]
            imu_version, count, sequence, base_us, _ = struct.unpack_from("<BBHQh", imu)
            if imu_version != 7 or not 1 <= count <= 16 or len(imu) != 14+count*14:
                raise ValueError("imu layout")
            node = f"BSF{node_id:04X}"
            for k in range(count):
                delta, ax, ay, az, gx, gy, gz = struct.unpack_from("<Hhhhhhh", imu, 14+k*14)
                rows.append((int(arrival_ms), node, int(base_us+delta), int(sequence+k), ax, ay, az, gx, gy, gz))
        except (ValueError, struct.error, IndexError):
            errors += 1
    if first_arrival is None:
        raise ValueError("no decodable common header")
    lo = first_arrival + int(round(include_start_s*1000))
    hi = first_arrival + int(round(include_stop_s*1000))
    rows = [r for r in rows if lo <= r[0] <= hi]
    offsets: dict[str, float] = {}
    for node in sorted({r[1] for r in rows}):
        values = np.array([arrival*1e-3-timer*1e-6 for arrival, n, timer, *_ in rows if n == node])
        offsets[node] = float(np.median(values))
    samples: list[ImuSample] = []
    seen: set[tuple] = set()
    boot_by_node: dict[str, int] = {}
    previous_timer: dict[str, int] = {}
    for arrival, node, timer, seq, ax, ay, az, gx, gy, gz in sorted(rows, key=lambda r: (r[2]+offsets[r[1]]*1e6, r[1], r[3])):
        key = (node, timer, seq)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        boot = boot_by_node.setdefault(node, 0)
        if node in previous_timer and timer+1_000_000 < previous_timer[node]:
            boot += 1; boot_by_node[node] = boot; resets += 1
        previous_timer[node] = timer
        common = timer*1e-6+offsets[node]
        age = max(0.0, arrival*1e-3-common)
        samples.append(ImuSample(
            node, common, timer, seq,
            np.array([gx, gy, gz], float)*(2000.0/32768.0)*np.pi/180.0,
            np.array([ax, ay, az], float)*(16.0/32768.0)*9.80665,
            age, boot,
        ))
    return samples, DecodeAudit(len(payload), headers, len(samples), len(samples)*6, 0, 0, errors, duplicates, resets)
