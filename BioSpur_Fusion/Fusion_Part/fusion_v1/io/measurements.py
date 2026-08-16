"""Decode IMU samples and individual UWB ranges without inherited labels."""
from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Iterator

from .raw_frames import DecodeError, HostFrame


@dataclass(frozen=True)
class Observation:
    source_record: int
    byte_start: int
    byte_end: int
    record_sha256: str
    node: str
    measurement: str
    native_time_us: float
    sequence: int
    anchor: int | None
    values: tuple[float, ...]
    units: str
    valid: bool
    reason: str
    master_arrival_ms: int


def decode_imu(frame: HostFrame) -> Iterator[Observation]:
    if frame.kind != 3 or len(frame.payload) < 14:
        raise DecodeError("imu_header")
    version, count, sequence, base_us, _temperature = struct.unpack_from("<BBHQh", frame.payload)
    if version != 7 or not 1 <= count <= 16 or len(frame.payload) != 14 + 14 * count:
        raise DecodeError("imu_schema")
    for sample in range(count):
        delta, ax, ay, az, gx, gy, gz = struct.unpack_from(
            "<Hhhhhhh", frame.payload, 14 + 14 * sample)
        yield Observation(frame.record_index, frame.byte_start, frame.byte_end,
            frame.encoded_sha256, frame.node, "imu6_raw", float(base_us + delta),
            (sequence + sample) & 0xFFFF, None,
            (ax, ay, az, gx, gy, gz), "acc_raw;gyro_raw", True, "",
            frame.master_arrival_ms)


def decode_uwb(frame: HostFrame) -> Iterator[Observation]:
    p = frame.payload
    if frame.kind != 1 or len(p) != 184:
        raise DecodeError("uwb_size")
    version, kind, declared, _packet, _node_ms = struct.unpack_from("<BBHII", p)
    if version != 7 or kind != 1 or declared != 184:
        raise DecodeError("uwb_schema")
    body = p[12:102]
    sweep = struct.unpack_from("<I", body)[0]
    anchors = body[16:24]
    ranges = struct.unpack_from("<8H", body, 32)
    tround = struct.unpack_from("<8H", body, 48)
    quality = body[64:72]
    cfo = struct.unpack_from("<8h", body, 72)
    valid_mask = body[88]
    _frame_us, strobe_us = struct.unpack_from("<QQ", p, 102)
    for j, anchor in enumerate(anchors):
        valid = bool(valid_mask & (1 << j)) and ranges[j] not in (0, 0xFFFF)
        # SS-TWR's effective range epoch is represented at half measured round
        # time after poll TX. This is retained explicitly, not sweep-collapsed.
        native = float(strobe_us) + 0.5 * float(tround[j])
        yield Observation(frame.record_index, frame.byte_start, frame.byte_end,
            frame.encoded_sha256, frame.node, "uwb_range", native, sweep,
            int(anchor), (float(ranges[j]), float(tround[j]), float(quality[j]), float(cfo[j])),
            "mm;us;percent;ppm_q8", valid, "" if valid else "firmware_invalid_mask_or_sentinel",
            frame.master_arrival_ms)

