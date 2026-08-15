"""Offline v47 binary ingest with byte-exact provenance.

The B306 module owns the transport envelope. This module consumes that stable
decoder and assigns scientific event semantics without using receipt time as a
measurement timestamp.
"""
from __future__ import annotations

import hashlib
import struct
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .events import EventStatus, RawByteProvenance, RecordType, TypedEvent

_REPO = Path(__file__).resolve().parents[4]
_B306_TOOLS = _REPO / "B306_Part" / "tools"
if str(_B306_TOOLS) not in sys.path:
    sys.path.insert(0, str(_B306_TOOLS))
from fusion_host_binary import FrameError, decode_frame, decode_superframe_flags  # noqa: E402


@dataclass(frozen=True)
class DecodeAudit:
    complete_records: int
    empty_records: int
    decode_errors: int
    incomplete_tail_bytes: int
    emitted_measurements: int
    kinds: dict[str, int]


def iter_cobs_records(path: Path, chunk_size: int = 4 << 20) -> Iterator[tuple[int, int, int, bytes, bool]]:
    """Yield index/start/end/encoded/complete without loading the capture."""
    pending = bytearray()
    absolute_start = 0
    record_index = 0
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            pending.extend(chunk)
            cursor = 0
            while True:
                boundary = pending.find(0, cursor)
                if boundary < 0:
                    if cursor:
                        absolute_start += cursor
                        del pending[:cursor]
                    break
                encoded = bytes(pending[cursor:boundary])
                start = absolute_start + cursor
                end = absolute_start + boundary + 1
                cursor = boundary + 1
                if encoded:
                    record_index += 1
                    yield record_index, start, end, encoded, True
        if pending:
            record_index += 1
            yield record_index, absolute_start, absolute_start + len(pending), bytes(pending), False


def _raw(index: int, start: int, end: int, encoded: bytes, sample: int = 0) -> RawByteProvenance:
    return RawByteProvenance(index, start, end, hashlib.sha256(encoded).hexdigest(), sample)


def _imu_events(frame, boot: int, provenance: tuple[int, int, int, bytes]) -> Iterator[TypedEvent]:
    if len(frame.payload) < 14:
        raise FrameError("short IMU payload")
    version, count, sequence, base_us, temp = struct.unpack_from("<BBHQh", frame.payload)
    if version != 7 or not 1 <= count <= 16 or len(frame.payload) != 14 + 14 * count:
        raise FrameError("invalid IMU payload")
    index, start, end, encoded = provenance
    for sample in range(count):
        delta, ax, ay, az, gx, gy, gz = struct.unpack_from("<Hhhhhhh", frame.payload, 14 + 14 * sample)
        yield TypedEvent(
            frame.node_name, boot, RecordType.IMU, (sequence + sample) & 0xFFFF,
            int(base_us + delta), None, None, int(frame.master_arrival_ms),
            {"base_timer2_us": int(base_us), "delta_us": int(delta),
             "acc_raw": [ax, ay, az], "gyro_raw": [gx, gy, gz], "temp_raw": temp},
            {"batch_count": count}, EventStatus.DECODED,
            _raw(index, start, end, encoded, sample),
        )


def _uwb_event(frame, boot: int, provenance: tuple[int, int, int, bytes]) -> TypedEvent:
    if len(frame.payload) != 184:
        raise FrameError("invalid UWB payload size")
    version, kind, declared, packet_seq, node_ms = struct.unpack_from("<BBHII", frame.payload)
    if version != 7 or kind != 1 or declared != 184:
        raise FrameError("invalid UWB payload header")
    body = frame.payload[12:102]
    capture = frame.payload[102:]
    sweep = struct.unpack_from("<I", body)[0]
    poll_tx = int.from_bytes(body[4:9], "little")
    identity = struct.unpack_from("<H", body, 9)[0]
    guard_us, spacing_us = struct.unpack_from("<HH", body, 12)
    ranges = list(struct.unpack_from("<8H", body, 32))
    tround = list(struct.unpack_from("<8H", body, 48))
    cfo = list(struct.unpack_from("<8h", body, 72))
    frame_us, strobe_us = struct.unpack_from("<QQ", capture)
    valid_mask, flags = body[88], body[89]
    sf_valid, sf_mod16 = decode_superframe_flags(flags)
    index, start, end, encoded = provenance
    return TypedEvent(
        frame.node_name, boot, RecordType.UWB, int(sweep), int(strobe_us), None, None,
        int(frame.master_arrival_ms),
        {"node_ms": int(node_ms), "packet_sequence": int(packet_seq), "sweep": int(sweep),
         "poll_tx_dw40": int(poll_tx), "identity": int(identity),
         "anchor_id": list(body[16:24]), "rank": list(body[24:32]),
         "range_mm": ranges, "t_round_us": tround, "quality_percent": list(body[64:72]),
         "cfo_ppm_q8": cfo, "valid_mask": int(valid_mask), "flags": int(flags),
         "frame_us": int(frame_us), "strobe_us": int(strobe_us)},
        {"guard_us": int(guard_us), "spacing_us": int(spacing_us),
         "superframe_valid": sf_valid, "superframe_mod16": sf_mod16},
        EventStatus.DECODED, _raw(index, start, end, encoded),
    )


def decode_measurements(path: Path) -> tuple[list[TypedEvent], DecodeAudit]:
    """Decode every complete IMU/UWB measurement; receipt time stays diagnostic."""
    events: list[TypedEvent] = []
    boot_by_node: Counter[str] = Counter()
    last_timer: dict[str, int] = {}
    kinds: Counter[str] = Counter()
    complete = empty = errors = tail = 0
    for index, start, end, encoded, is_complete in iter_cobs_records(path):
        if not is_complete:
            tail = len(encoded)
            continue
        complete += 1
        try:
            frame = decode_frame(encoded)
            kinds[str(frame.kind)] += 1
            if frame.kind not in (1, 3):
                continue
            timer = struct.unpack_from("<Q", frame.payload, 102 if frame.kind == 1 else 4)[0]
            previous = last_timer.get(frame.node_name)
            if previous is not None and timer < previous:
                boot_by_node[frame.node_name] += 1
            last_timer[frame.node_name] = int(timer)
            provenance = (index, start, end, encoded)
            if frame.kind == 3:
                events.extend(_imu_events(frame, boot_by_node[frame.node_name], provenance))
            else:
                events.append(_uwb_event(frame, boot_by_node[frame.node_name], provenance))
        except (FrameError, struct.error, IndexError, ValueError):
            errors += 1
    return events, DecodeAudit(complete, empty, errors, tail, len(events), dict(sorted(kinds.items())))
