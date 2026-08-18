"""Phase 3-R2 field-selective timing readers.

The public records in this module deliberately have no raw-line/payload escape
hatch.  Mixed transport bytes may transit the scanner, but UWB measurements
are never converted, indexed, returned, logged, or made available to callers.

Frame convention for the binary reader is the deployed Fusion v7 COBS
envelope.  Only the common routing header plus the UWB sweep/strobe/sync fields
are decoded.  All measurement spans remain opaque bytes.
"""
from __future__ import annotations

import binascii
from dataclasses import dataclass, fields
from pathlib import Path
import re
import struct
from typing import BinaryIO, Iterable, Iterator


MAGIC = 0x5342
ENVELOPE_VERSION = 1
KIND_UWB = 1
UWB_LAYOUT_VERSION = 7
UWB_LAYOUT_BYTES = 184
COMMON_HEADER_BYTES = 20

# Exact deployed v7 offsets within the UWB payload.  No measurement offset is
# declared here, which makes accidental measurement indexing a structural
# test failure rather than a code-review convention.
_UWB_VERSION = 0
_UWB_RECORD_KIND = 1
_UWB_DECLARED_LENGTH = slice(2, 4)
_UWB_SWEEP = slice(12, 16)
_UWB_FLAGS = 101
_UWB_FRAME_TIMER2 = slice(102, 110)
_UWB_STROBE_TIMER2 = slice(110, 118)

_FORBIDDEN_NAMES = frozenset({
    "range_mm", "ranges", "t_round", "distance", "quality", "cfo",
    "rssi", "anchor_measurement_payload", "payload", "raw_line",
})


class SelectiveTimingError(ValueError):
    """Safe parser failure whose text never includes source record contents."""


@dataclass(frozen=True, slots=True)
class TimingRoutingRecord:
    record_kind: int
    hardware_node_id: str
    boot_epoch: int
    sweep_id: int
    frame_timer2_us: int
    strobe_timer2_us: int
    superframe_valid: bool
    superframe_mod16: int | None
    required_transport_flags: int
    source_byte_offset: int
    source_record_length: int


@dataclass(frozen=True, slots=True)
class SelectiveTimingAudit:
    transport_records: int
    uwb_routing_records: int
    skipped_non_uwb_records: int
    crc_or_layout_errors: int
    incomplete_tail_bytes: int
    boot_resets: int
    measurement_semantic_numeric_decodes: int = 0
    measurement_array_materializations: int = 0
    measurement_statistics_or_plots: int = 0


def assert_public_schema_safe() -> None:
    names = {field.name.lower() for field in fields(TimingRoutingRecord)}
    overlap = names & _FORBIDDEN_NAMES
    if overlap:
        raise RuntimeError(f"forbidden timing schema field(s): {sorted(overlap)}")


def _u16(raw: bytes | memoryview, offset: int) -> int:
    return struct.unpack_from("<H", raw, offset)[0]


def _u32_span(raw: bytes | memoryview, span: slice) -> int:
    return struct.unpack("<I", raw[span])[0]


def _u64_span(raw: bytes | memoryview, span: slice) -> int:
    return struct.unpack("<Q", raw[span])[0]


def cobs_decode(encoded: bytes) -> bytes:
    output = bytearray()
    cursor = 0
    while cursor < len(encoded):
        code = encoded[cursor]
        cursor += 1
        stop = cursor + code - 1
        if code == 0 or stop > len(encoded):
            raise SelectiveTimingError("invalid COBS record")
        output.extend(encoded[cursor:stop])
        cursor = stop
        if code != 0xFF and cursor < len(encoded):
            output.append(0)
    return bytes(output)


def cobs_encode(raw: bytes) -> bytes:
    """Small deterministic encoder used by independent parser fixtures."""
    out = bytearray(b"\x00")
    code_index = 0
    code = 1
    for value in raw:
        if value == 0:
            out[code_index] = code
            code_index = len(out)
            out.append(0)
            code = 1
        else:
            out.append(value)
            code += 1
            if code == 0xFF:
                out[code_index] = code
                code_index = len(out)
                out.append(0)
                code = 1
    out[code_index] = code
    return bytes(out)


def _iter_cobs_records(handle: BinaryIO, chunk_bytes: int = 4 << 20) -> Iterator[tuple[int, int, bytes, bool]]:
    pending = bytearray()
    absolute = 0
    while chunk := handle.read(chunk_bytes):
        pending.extend(chunk)
        cursor = 0
        while True:
            boundary = pending.find(0, cursor)
            if boundary < 0:
                if cursor:
                    absolute += cursor
                    del pending[:cursor]
                break
            encoded = bytes(pending[cursor:boundary])
            start = absolute + cursor
            length = boundary - cursor + 1
            cursor = boundary + 1
            if encoded:
                yield start, length, encoded, True
    if pending:
        yield absolute, len(pending), bytes(pending), False


def _decode_binary_routing(encoded: bytes, offset: int, record_length: int) -> tuple[str, int, int, int, int, int] | None:
    raw = cobs_decode(encoded)
    if len(raw) < COMMON_HEADER_BYTES + 2:
        raise SelectiveTimingError(f"short transport record at byte {offset}")
    body = memoryview(raw)[:-2]
    expected_crc = _u16(raw, len(raw) - 2)
    if binascii.crc_hqx(body, 0xFFFF) != expected_crc:
        raise SelectiveTimingError(f"CRC mismatch at byte {offset}")
    magic = _u16(body, 0)
    envelope_version = body[2]
    record_kind = body[3]
    node_numeric_id = _u16(body, 4)
    declared_payload_bytes = _u16(body, 6)
    if magic != MAGIC or envelope_version != ENVELOPE_VERSION:
        raise SelectiveTimingError(f"routing header mismatch at byte {offset}")
    if len(body) - COMMON_HEADER_BYTES != declared_payload_bytes:
        raise SelectiveTimingError(f"routing length mismatch at byte {offset}")
    if record_kind != KIND_UWB:
        return None
    uwb = body[COMMON_HEADER_BYTES:]
    if len(uwb) != UWB_LAYOUT_BYTES:
        raise SelectiveTimingError(f"UWB timing-layout length mismatch at byte {offset}")
    if uwb[_UWB_VERSION] != UWB_LAYOUT_VERSION or uwb[_UWB_RECORD_KIND] != KIND_UWB:
        raise SelectiveTimingError(f"UWB timing-layout version mismatch at byte {offset}")
    if _u16(uwb, _UWB_DECLARED_LENGTH.start) != UWB_LAYOUT_BYTES:
        raise SelectiveTimingError(f"UWB declared timing-layout mismatch at byte {offset}")
    sweep_id = _u32_span(uwb, _UWB_SWEEP)
    frame_timer2_us = _u64_span(uwb, _UWB_FRAME_TIMER2)
    strobe_timer2_us = _u64_span(uwb, _UWB_STROBE_TIMER2)
    flags = int(uwb[_UWB_FLAGS])
    return f"BSF{node_numeric_id:04X}", sweep_id, frame_timer2_us, strobe_timer2_us, flags, record_length


def iter_binary_timing_records(path: Path, *, chunk_bytes: int = 4 << 20) -> tuple[tuple[TimingRoutingRecord, ...], SelectiveTimingAudit]:
    """Read only timing/routing fields from a mixed binary transport file."""
    assert_public_schema_safe()
    records: list[TimingRoutingRecord] = []
    previous_strobe: dict[str, int] = {}
    boot_by_node: dict[str, int] = {}
    transport = skipped = errors = tail = resets = 0
    with Path(path).open("rb") as handle:
        for offset, length, encoded, complete in _iter_cobs_records(handle, chunk_bytes):
            if not complete:
                tail += len(encoded)
                continue
            transport += 1
            try:
                decoded = _decode_binary_routing(encoded, offset, length)
                if decoded is None:
                    skipped += 1
                    continue
                node, sweep, frame_us, strobe_us, flags, source_length = decoded
                boot = boot_by_node.setdefault(node, 0)
                previous = previous_strobe.get(node)
                if previous is not None and strobe_us < previous:
                    boot += 1
                    boot_by_node[node] = boot
                    resets += 1
                previous_strobe[node] = strobe_us
                sf_valid = bool(flags & 0x80)
                records.append(TimingRoutingRecord(
                    KIND_UWB, node, boot, sweep, frame_us, strobe_us,
                    sf_valid, ((flags >> 3) & 0x0F) if sf_valid else None,
                    flags, offset, source_length,
                ))
            except (SelectiveTimingError, struct.error, IndexError, ValueError):
                errors += 1
    audit = SelectiveTimingAudit(
        transport, len(records), skipped, errors, tail, resets,
    )
    return tuple(records), audit


_TOKEN = re.compile(rb"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=([^\s]*)")
_TEXT_ALLOWED = frozenset({"name", "sweep", "frame_us", "strobe_us", "flags"})


def _allowed_text_tokens(line: bytes) -> dict[str, bytes]:
    # Only allowlisted token spans are copied.  The source line and every other
    # token span go out of scope when this function returns.
    selected: dict[str, bytes] = {}
    for match in _TOKEN.finditer(line):
        name = match.group(1).decode("ascii", errors="ignore")
        if name in _TEXT_ALLOWED:
            selected[name] = bytes(match.group(2))
    return selected


def iter_text_timing_records(lines: Iterable[bytes]) -> tuple[TimingRoutingRecord, ...]:
    """Lexically project legacy FUSION_UWB text without retaining raw lines."""
    assert_public_schema_safe()
    output: list[TimingRoutingRecord] = []
    previous: dict[str, int] = {}
    boots: dict[str, int] = {}
    byte_offset = 0
    for line in lines:
        length = len(line)
        if b"FUSION_UWB" not in line:
            byte_offset += length
            continue
        selected = _allowed_text_tokens(line)
        missing = _TEXT_ALLOWED - set(selected)
        if missing:
            names = ",".join(sorted(missing))
            raise SelectiveTimingError(f"missing allowed field(s) {names} at byte {byte_offset}")
        try:
            node = selected["name"].decode("ascii")
            sweep = int(selected["sweep"], 10)
            frame_us = int(selected["frame_us"], 10)
            strobe_us = int(selected["strobe_us"], 10)
            flags = int(selected["flags"], 16)
        except (UnicodeError, ValueError) as exc:
            raise SelectiveTimingError(f"invalid allowed timing field at byte {byte_offset}") from exc
        boot = boots.setdefault(node, 0)
        if node in previous and strobe_us < previous[node]:
            boot += 1
            boots[node] = boot
        previous[node] = strobe_us
        valid = bool(flags & 0x80)
        output.append(TimingRoutingRecord(
            KIND_UWB, node, boot, sweep, frame_us, strobe_us, valid,
            ((flags >> 3) & 0x0F) if valid else None, flags,
            byte_offset, length,
        ))
        byte_offset += length
    return tuple(output)


def build_binary_fixture(*, node_id: int, sweep: int, frame_us: int, strobe_us: int,
                         flags: int, opaque_measurement_bytes: bytes) -> bytes:
    """Independent fixture builder; opaque bytes fill every unallowlisted span."""
    payload = bytearray((opaque_measurement_bytes * (UWB_LAYOUT_BYTES // max(1, len(opaque_measurement_bytes)) + 1))[:UWB_LAYOUT_BYTES])
    payload[_UWB_VERSION] = UWB_LAYOUT_VERSION
    payload[_UWB_RECORD_KIND] = KIND_UWB
    payload[_UWB_DECLARED_LENGTH] = struct.pack("<H", UWB_LAYOUT_BYTES)
    payload[_UWB_SWEEP] = struct.pack("<I", sweep)
    payload[_UWB_FLAGS] = flags
    payload[_UWB_FRAME_TIMER2] = struct.pack("<Q", frame_us)
    payload[_UWB_STROBE_TIMER2] = struct.pack("<Q", strobe_us)
    # Common sequence and receipt clock are intentionally zero and inaccessible.
    header = struct.pack("<HBBHHIQ", MAGIC, ENVELOPE_VERSION, KIND_UWB,
                         node_id, UWB_LAYOUT_BYTES, 0, 0)
    body = header + bytes(payload)
    crc = struct.pack("<H", binascii.crc_hqx(body, 0xFFFF))
    return cobs_encode(body + crc) + b"\0"
