from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import struct
from typing import Iterable

import numpy as np

from biospur_fusion.calibration_v2.phase2r.decoder import CRC, HEADER, KIND_IMU, KIND_UWB, MAGIC, VERSION, cobs_decode, crc16_ccitt_false
from biospur_fusion.articulated_v2.estimator import ImuObservation


@dataclass(frozen=True)
class ProjectionAudit:
    container_bytes_streamed: int
    headers_routing_decoded: int
    imu_numeric_fields_decoded: int
    uwb_numeric_fields_decoded: int
    imu_arrays_materialized: int
    uwb_arrays_materialized: int
    output_observations: int
    crc_or_layout_errors: int
    clock_model: str


def selective_imu_projection(payload: bytes, *, preparation_s: float, formal_s: float, recovery_s: float, include_context: bool = False) -> tuple[list[ImuObservation], ProjectionAudit]:
    """Decode only IMU numeric fields from a mixed COBS container.

    UWB records are identified from the common header and rejected before any
    UWB payload field is unpacked or materialized.
    """
    rows = []
    headers = imu_fields = errors = 0
    first_arrival = None
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
            magic, version, kind, node_id, length, _, arrival = HEADER.unpack_from(body)
            if magic != MAGIC or version != VERSION or len(body) - HEADER.size != length:
                raise ValueError("header")
            headers += 1
            first_arrival = int(arrival) if first_arrival is None else min(first_arrival, int(arrival))
            if kind == KIND_UWB:
                continue
            if kind != KIND_IMU:
                continue
            imu = memoryview(body)[HEADER.size:]
            imu_version, count, sequence, base_us, _ = struct.unpack_from("<BBHQh", imu)
            if imu_version != 7 or not 1 <= count <= 16 or len(imu) != 14 + count*14:
                raise ValueError("imu layout")
            node = f"BSF{node_id:04X}"
            for sample in range(count):
                delta, ax, ay, az, gx, gy, gz = struct.unpack_from("<Hhhhhhh", imu, 14 + sample*14)
                rows.append((int(arrival), node, int(base_us+delta), int(sequence+sample), ax, ay, az, gx, gy, gz))
                imu_fields += 7
        except (ValueError, struct.error, IndexError):
            errors += 1
    if first_arrival is None:
        raise ValueError("no decodable routing header")
    start = first_arrival if include_context else first_arrival + int(round(preparation_s*1000))
    stop = first_arrival + int(round((preparation_s + formal_s + (recovery_s if include_context else 0))*1000))
    selected = [row for row in rows if start <= row[0] <= stop]
    offsets = {}
    for node in sorted({row[1] for row in selected}):
        pairs = [(arrival*1e-3 - timer_us*1e-6) for arrival, n, timer_us, *_ in selected if n == node]
        offsets[node] = float(np.median(pairs))
    observations = [
        ImuObservation(node, timer_us*1e-6 + offsets[node], sequence,
                       np.array([gx, gy, gz], dtype=float) * (2000.0/32768.0) * np.pi/180.0,
                       np.array([ax, ay, az], dtype=float) * (16.0/32768.0) * 9.80665)
        for _, node, timer_us, sequence, ax, ay, az, gx, gy, gz in selected
    ]
    observations.sort(key=lambda x: (x.time_s, x.node_id, x.sequence))
    return observations, ProjectionAudit(len(payload), headers, imu_fields, 0, 1 if observations else 0, 0, len(observations), errors, "node_TIMER2_plus_robust_per_window_clock_segment_offset; host arrival only fits offset")


class Phase3AccessLedger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def record(self, **row) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
