#!/usr/bin/env python3
"""Fail-closed streaming projection of approved scalar fields from NPY members.

This module intentionally does not import NumPy.  Records are never assembled:
approved scalar spans are decoded individually and every other span is streamed
through a bounded scratch buffer and discarded.
"""
from __future__ import annotations

import ast
import dataclasses
import re
import struct
import zipfile
from collections.abc import Iterator
from pathlib import Path

MAGIC = b"\x93NUMPY"
ALLOWED_FIELDS = frozenset({
    "boot_epoch", "sequence", "node_timer_us", "global_time_ns",
    "global_time_sigma_ns", "raw_record_index", "raw_sample_index", "status",
})
SCALAR_FORMATS = {
    "i1": "b", "u1": "B", "i2": "h", "u2": "H",
    "i4": "i", "u4": "I", "i8": "q", "u8": "Q",
}
TYPE_RE = re.compile(r"^([<>|])([iufbV])(\d+)$")


class SelectiveNpyError(RuntimeError):
    pass


@dataclasses.dataclass(frozen=True)
class Field:
    name: str
    descriptor: str
    width: int
    count: int
    offset: int
    approved: bool


@dataclasses.dataclass
class ReaderStats:
    rows: int = 0
    approved_numeric_decodes: int = 0
    measurement_numeric_decodes: int = 0
    measurement_arrays: int = 0
    measurement_fields_retained: int = 0
    measurement_values_logged: int = 0
    max_opaque_scratch_bytes: int = 0


def _read_exact(stream, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise SelectiveNpyError("truncated NPY stream")
    return data


def _safe_header(stream):
    if _read_exact(stream, 6) != MAGIC:
        raise SelectiveNpyError("bad NPY magic")
    major, minor = _read_exact(stream, 2)
    if (major, minor) == (1, 0):
        header_len = struct.unpack("<H", _read_exact(stream, 2))[0]
    elif major in (2, 3) and minor == 0:
        header_len = struct.unpack("<I", _read_exact(stream, 4))[0]
    else:
        raise SelectiveNpyError("unsupported NPY version")
    if header_len <= 0 or header_len > 1_048_576:
        raise SelectiveNpyError("unsafe NPY header length")
    raw = _read_exact(stream, header_len)
    try:
        text = raw.decode("latin1" if major < 3 else "utf-8")
        header = ast.literal_eval(text.strip())
    except Exception as exc:
        raise SelectiveNpyError("malformed NPY header") from exc
    if not isinstance(header, dict) or set(header) != {"descr", "fortran_order", "shape"}:
        raise SelectiveNpyError("unexpected NPY header keys")
    if header["fortran_order"] is not False:
        raise SelectiveNpyError("Fortran layout forbidden")
    shape = header["shape"]
    if not isinstance(shape, tuple) or len(shape) != 1 or not isinstance(shape[0], int) or shape[0] < 0:
        raise SelectiveNpyError("only one-dimensional fixed records supported")
    return header["descr"], shape[0]


def _field_layout(descr) -> tuple[list[Field], int]:
    if not isinstance(descr, list) or not descr:
        raise SelectiveNpyError("structured descriptor required")
    fields: list[Field] = []
    seen: set[str] = set()
    offset = 0
    for item in descr:
        if not isinstance(item, tuple) or len(item) not in (2, 3):
            raise SelectiveNpyError("unsupported or overlapping field layout")
        name, scalar = item[0], item[1]
        shape = item[2] if len(item) == 3 else ()
        if not isinstance(name, str) or not name or name in seen or not isinstance(scalar, str):
            raise SelectiveNpyError("invalid/duplicate field")
        seen.add(name)
        match = TYPE_RE.fullmatch(scalar)
        if not match:
            raise SelectiveNpyError("unknown endian or dtype")
        endian, kind, width_text = match.groups()
        width = int(width_text)
        if width <= 0 or kind == "O":
            raise SelectiveNpyError("object/variable dtype forbidden")
        if shape == ():
            count = 1
        elif isinstance(shape, tuple) and shape and all(isinstance(x, int) and x > 0 for x in shape):
            count = 1
            for x in shape:
                count *= x
        else:
            raise SelectiveNpyError("variable/invalid subarray")
        approved = name in ALLOWED_FIELDS
        if approved:
            if count != 1 or kind not in "iu" or f"{kind}{width}" not in SCALAR_FORMATS:
                raise SelectiveNpyError("approved field is not a supported integer scalar")
            if width > 1 and endian not in "<>":
                raise SelectiveNpyError("unknown endian for approved scalar")
        fields.append(Field(name, scalar, width, count, offset, approved))
        offset += width * count
    if offset <= 0:
        raise SelectiveNpyError("empty record")
    return fields, offset


def _discard(stream, size: int, stats: ReaderStats, chunk_size: int) -> None:
    left = size
    while left:
        part = stream.read(min(left, chunk_size))
        if not part:
            raise SelectiveNpyError("truncated opaque span")
        stats.max_opaque_scratch_bytes = max(stats.max_opaque_scratch_bytes, len(part))
        left -= len(part)


def _decode(field: Field, raw: bytes, stats: ReaderStats) -> int:
    endian, kind, width_text = TYPE_RE.fullmatch(field.descriptor).groups()
    prefix = "<" if endian in "<|" else ">"
    stats.approved_numeric_decodes += 1
    return struct.unpack(prefix + SCALAR_FORMATS[f"{kind}{width_text}"], raw)[0]


def iter_time_projection(npz_path: str | Path, member: str, *, chunk_size: int = 4096,
                         stats: ReaderStats | None = None) -> Iterator[dict[str, int]]:
    if chunk_size <= 0 or chunk_size > 65536:
        raise SelectiveNpyError("unsafe chunk size")
    stats = stats if stats is not None else ReaderStats()
    member_name = member if member.endswith(".npy") else member + ".npy"
    if not member_name.startswith("imu_BSF"):
        raise SelectiveNpyError("only explicit IMU member names permitted")
    node = member_name[4:-4]
    if not re.fullmatch(r"BSF[0-9A-F]{4}", node):
        raise SelectiveNpyError("invalid hardware node identity")
    with zipfile.ZipFile(npz_path, "r") as archive:
        names = archive.namelist()
        if names.count(member_name) != 1:
            raise SelectiveNpyError("missing or duplicate member")
        with archive.open(member_name, "r") as stream:
            descr, rows = _safe_header(stream)
            fields, itemsize = _field_layout(descr)
            required = {"boot_epoch", "sequence", "node_timer_us", "global_time_ns",
                        "global_time_sigma_ns", "raw_record_index", "raw_sample_index", "status"}
            if not required.issubset({f.name for f in fields if f.approved}):
                raise SelectiveNpyError("required time/identity field missing")
            for _ in range(rows):
                projection: dict[str, int] = {}
                consumed = 0
                for field in fields:
                    span = field.width * field.count
                    if field.approved:
                        projection[field.name] = _decode(field, _read_exact(stream, span), stats)
                    else:
                        _discard(stream, span, stats, chunk_size)
                    consumed += span
                if consumed != itemsize:
                    raise SelectiveNpyError("record size mismatch")
                stats.rows += 1
                yield {"hardware_node_id": node, **projection}
            if stream.read(1):
                raise SelectiveNpyError("trailing NPY payload")

