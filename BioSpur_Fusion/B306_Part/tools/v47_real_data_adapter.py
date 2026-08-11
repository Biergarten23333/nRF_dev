#!/usr/bin/env python3
"""Deterministic, offline adapter for v47 Fusion host binary captures.

The adapter deliberately exposes raw values and provenance.  It performs no
interpolation, smoothing, quality rejection, or hardware I/O.
"""

from __future__ import annotations

import csv
import binascii
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

from fusion_host_binary import FrameError, HostFrame, cobs_decode

_HOST_HEADER = struct.Struct("<HBBHHIQ")


def _decode_host_frame(encoded: bytes) -> HostFrame:
    """Equivalent to fusion_host_binary.decode_frame, with C CRC for bulk replay."""
    raw = cobs_decode(encoded)
    if len(raw) < _HOST_HEADER.size + 2:
        raise FrameError("short host frame")
    body, expected = raw[:-2], struct.unpack_from("<H", raw, len(raw) - 2)[0]
    if binascii.crc_hqx(body, 0xFFFF) != expected:
        raise FrameError("host frame CRC mismatch")
    magic, version, kind, node_id, payload_len, sequence, master_ms = _HOST_HEADER.unpack_from(body)
    payload = body[_HOST_HEADER.size:]
    if magic != 0x5342 or version != 1 or len(payload) != payload_len:
        raise FrameError("invalid host frame envelope")
    return HostFrame(kind, node_id, sequence, master_ms, payload)

NODES = (
    "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
    "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35",
)
IMU_DTYPE = np.dtype([
    ("b306_us", "<u8"), ("master_ms", "<u8"), ("seq", "<u2"),
    ("delta_us", "<u2"), ("batch_n", "u1"),
    ("acc", "<i2", (3,)), ("gyro", "<i2", (3,)), ("temp_raw", "<i2"),
])
UWB_DTYPE = np.dtype([
    ("master_ms", "<u8"), ("node_ms", "<u4"), ("packet_seq", "<u4"),
    ("sweep", "<u4"), ("poll_tx", "<u8"), ("identity", "<u2"),
    ("logical", "u1"), ("guard_us", "<u2"), ("spacing_us", "<u2"),
    ("anchor_id", "u1", (8,)), ("rank", "u1", (8,)),
    ("range_mm", "<u2", (8,)), ("t_round_us", "<u2", (8,)),
    ("quality", "u1", (8,)), ("cfo_ppm_q8", "<i2", (8,)),
    ("valid_mask", "u1"), ("flags", "u1"),
    ("frame_us", "<u8"), ("strobe_us", "<u8"),
])


@dataclass(frozen=True)
class ReplayAudit:
    raw_sha256: str
    raw_size: int
    formal_offset: int
    records_before_t0: int
    decode_errors_before_t0: int
    decode_errors_formal: int
    incomplete_tail_bytes: int
    formal_kind_counts: dict[int, int]


def sha256_file(path: Path, chunk_size: int = 4 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def iter_cobs_records(path: Path, chunk_size: int = 4 << 20) -> Iterator[tuple[int, bytes]]:
    """Yield (exclusive byte end, encoded bytes) without loading the file."""
    pending = bytearray()
    consumed = 0
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            pending.extend(chunk)
            start = 0
            while True:
                boundary = pending.find(0, start)
                if boundary < 0:
                    if start:
                        consumed += start
                        del pending[:start]
                    break
                encoded = bytes(pending[start:boundary])
                end = consumed + boundary + 1
                start = boundary + 1
                if encoded:
                    yield end, encoded
        if pending:
            yield consumed + len(pending), bytes(pending)


def _expected_counts(capture_dir: Path) -> tuple[dict[str, int], dict[str, int]]:
    imu, uwb = {}, {}
    with (capture_dir / "PER_BOARD_COUNTS.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            imu[row["node"]] = int(row["imu_samples"])
            uwb[row["node"]] = int(row["uwb_records"])
    return imu, uwb


def _decode_imu(frame: HostFrame, out: np.ndarray, cursor: int) -> int:
    if len(frame.payload) < 14:
        raise FrameError("short IMU payload")
    version, n, sequence, base_us, temp = struct.unpack_from("<BBHQh", frame.payload)
    if version != 7 or not 1 <= n <= 16 or len(frame.payload) != 14 + 14 * n:
        raise FrameError("invalid IMU payload")
    for index in range(n):
        delta, ax, ay, az, gx, gy, gz = struct.unpack_from(
            "<Hhhhhhh", frame.payload, 14 + 14 * index
        )
        out[cursor + index] = (
            base_us + delta, frame.master_arrival_ms, (sequence + index) & 0xFFFF,
            delta, n, (ax, ay, az), (gx, gy, gz), temp,
        )
    return cursor + n


def _decode_uwb(frame: HostFrame, out: np.ndarray, cursor: int) -> int:
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
    ranges = struct.unpack_from("<8H", body, 32)
    tround = struct.unpack_from("<8H", body, 48)
    cfo = struct.unpack_from("<8h", body, 72)
    frame_us, strobe_us = struct.unpack_from("<QQ", capture)
    out[cursor] = (
        frame.master_arrival_ms, node_ms, packet_seq, sweep, poll_tx, identity,
        body[11], guard_us, spacing_us, tuple(body[16:24]), tuple(body[24:32]),
        ranges, tround, tuple(body[64:72]), cfo, body[88], body[89],
        frame_us, strobe_us,
    )
    return cursor + 1


def load_capture(data_root: Path) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], ReplayAudit]:
    """Replay the authoritative raw capture and return exact typed arrays."""
    capture = data_root / "formal_capture"
    manifest = json.loads((capture / "RUN_MANIFEST.json").read_text(encoding="utf-8"))
    formal_offset = int(manifest["formal_health_baseline"]["raw_bytes_submitted"])
    raw = capture / "fusion_host_raw.cobs.bin"
    imu_counts, uwb_counts = _expected_counts(capture)
    imu = {node: np.empty(imu_counts[node], dtype=IMU_DTYPE) for node in NODES}
    uwb = {node: np.empty(uwb_counts[node], dtype=UWB_DTYPE) for node in NODES}
    ip = {node: 0 for node in NODES}
    up = {node: 0 for node in NODES}
    before_records = before_errors = formal_errors = tail = 0
    kinds: dict[int, int] = {}

    for end_offset, encoded in iter_cobs_records(raw):
        formal = end_offset > formal_offset
        try:
            frame = _decode_host_frame(encoded)
        except FrameError:
            if end_offset == raw.stat().st_size and not encoded.endswith(b"\0"):
                tail = len(encoded)
                # A collector shutdown-boundary fragment is evidence of file
                # closure, not a corrupt complete COBS record.
                continue
            if formal:
                formal_errors += 1
            else:
                before_errors += 1
            continue
        if not formal:
            before_records += 1
            continue
        kinds[frame.kind] = kinds.get(frame.kind, 0) + 1
        node = frame.node_name
        if node not in imu:
            continue
        if frame.kind == 3:
            ip[node] = _decode_imu(frame, imu[node], ip[node])
        elif frame.kind == 1:
            up[node] = _decode_uwb(frame, uwb[node], up[node])

    for node in NODES:
        if ip[node] != len(imu[node]) or up[node] != len(uwb[node]):
            raise RuntimeError(
                f"formal count mismatch {node}: IMU {ip[node]}/{len(imu[node])}, "
                f"UWB {up[node]}/{len(uwb[node])}"
            )
    audit = ReplayAudit(
        sha256_file(raw), raw.stat().st_size, formal_offset, before_records,
        before_errors, formal_errors, tail, dict(sorted(kinds.items())),
    )
    return imu, uwb, audit


def imu_physical(raw: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Source-proved conversions from docs/ble_protocol.md."""
    return raw["acc"].astype(float) / 2048.0, raw["gyro"].astype(float) / 16.384, raw["temp_raw"].astype(float) / 100.0


def sequence_gap_count(values: np.ndarray, modulus: int) -> int:
    if len(values) < 2:
        return 0
    delta = (values[1:].astype(np.uint64) - values[:-1].astype(np.uint64)) % modulus
    return int(np.sum(delta != 1))
