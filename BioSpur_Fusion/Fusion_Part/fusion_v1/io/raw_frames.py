"""Independent decoder for the factual Fusion Master v1 host envelope.

No historical Fusion estimator code is imported. Offsets are checked against
raw records and the firmware/transport schema before observations are emitted.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import binascii
import hashlib
import struct
from typing import Iterator

HEADER = struct.Struct("<HBBHHIQ")
CRC = struct.Struct("<H")
MAGIC = 0x5342
HOST_VERSION = 1


class DecodeError(ValueError):
    pass


def crc16_ccitt_false(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


def cobs_decode(encoded: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(encoded):
        code = encoded[i]
        i += 1
        if code == 0 or i + code - 1 > len(encoded):
            raise DecodeError("invalid_cobs")
        out.extend(encoded[i:i + code - 1])
        i += code - 1
        if code != 0xFF and i < len(encoded):
            out.append(0)
    return bytes(out)


@dataclass(frozen=True)
class HostFrame:
    record_index: int
    byte_start: int
    byte_end: int
    encoded_sha256: str
    kind: int
    node_id: int
    transport_sequence: int
    master_arrival_ms: int
    payload: bytes

    @property
    def node(self) -> str:
        return f"BSF{self.node_id:04X}" if self.node_id else "-"


def iter_encoded(path: Path, chunk_size: int = 4 << 20) -> Iterator[tuple[int, int, int, bytes]]:
    pending = bytearray()
    absolute = 0
    index = 0
    with path.open("rb") as src:
        while chunk := src.read(chunk_size):
            pending.extend(chunk)
            consumed = 0
            while (boundary := pending.find(0, consumed)) >= 0:
                encoded = bytes(pending[consumed:boundary])
                start, end = absolute + consumed, absolute + boundary + 1
                consumed = boundary + 1
                if encoded:
                    index += 1
                    yield index, start, end, encoded
            if consumed:
                del pending[:consumed]
                absolute += consumed
    # A collector may close after receiving only part of the final USB record.
    # No unterminated bytes are emitted as a frame; callers report the tail.


def incomplete_tail_bytes(path: Path, window: int = 1 << 20) -> int:
    size = path.stat().st_size
    with path.open("rb") as src:
        src.seek(max(0, size - window))
        tail = src.read()
    boundary = tail.rfind(b"\x00")
    return len(tail) if boundary < 0 else len(tail) - boundary - 1


def decode(index: int, start: int, end: int, encoded: bytes) -> HostFrame:
    raw = cobs_decode(encoded)
    if len(raw) < HEADER.size + CRC.size:
        raise DecodeError("short_frame")
    body, crc_bytes = raw[:-2], raw[-2:]
    if crc16_ccitt_false(body) != CRC.unpack(crc_bytes)[0]:
        raise DecodeError("crc_mismatch")
    magic, version, kind, node, size, sequence, arrival = HEADER.unpack_from(body)
    payload = body[HEADER.size:]
    if magic != MAGIC or version != HOST_VERSION:
        raise DecodeError("magic_or_version")
    if size != len(payload):
        raise DecodeError("payload_size")
    return HostFrame(index, start, end, hashlib.sha256(encoded).hexdigest(), kind,
                     node, sequence, arrival, payload)


def iter_frames(path: Path) -> Iterator[HostFrame]:
    for args in iter_encoded(path):
        yield decode(*args)
