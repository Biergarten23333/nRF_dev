"""Hostile-access-safe Capture1 adapter for the pure-IMU V0 direct path.

The historical Capture1 ledger is a ZIP_STORED NPZ containing one structured
NumPy member per node.  NumPy's normal NPZ/mmap APIs expose a complete member,
so this module deliberately does not use them.  It parses only ZIP/NPY metadata
and then reads individual rows or a pre-proven contiguous row interval through
instrumented ``os.lseek``/``os.read`` calls.

The ledger is a bounded time/index authority.  Estimator accelerometer and
gyroscope samples are decoded afresh from the raw COBS container and checked
against the bounded ledger copies solely for transport integrity.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import struct
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .contracts import NODES, dump_json, sha256_file
from .raw_validation import _decode_imu_only


SUPERFRAME_NS = 120_000_000
SEQUENTIAL_PADDING_NS = 2 * SUPERFRAME_NS
CLOSURE_INDEX_REL = Path(
    "logs/biospur_fusion_v0_dual_capture_closure_20260827T063045Z/"
    "CAPTURE1/COMPLETE_REPLAY_INDEX.json"
)
LEDGER_REFERENCE_NAME = "TIME_EVENT_LEDGER.reference.json"

_OS_OPEN = os.open
_OS_CLOSE = os.close
_OS_READ = os.read
_OS_LSEEK = os.lseek


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return max(int(left[0]), int(right[0])) < min(int(left[1]), int(right[1]))


def _union_bytes(intervals: Iterable[tuple[int, int]]) -> int:
    ordered = sorted((int(a), int(b)) for a, b in intervals if int(b) > int(a))
    if not ordered:
        return 0
    total = 0
    left, right = ordered[0]
    for start, stop in ordered[1:]:
        if start <= right:
            right = max(right, stop)
        else:
            total += right - left
            left, right = start, stop
    return total + right - left


class _AuditedBinaryFile:
    """Exact positioned reads with one trace row per real OS primitive call."""

    def __init__(self, path: Path, trace: list[dict[str, Any]]):
        self.path = Path(path).resolve()
        self.size = self.path.stat().st_size
        self.trace = trace
        self.fd = _OS_OPEN(self.path, os.O_RDONLY)
        self.position = 0
        self.trace.append({
            "path": str(self.path), "call_type": "os_open", "actual_open_calls": 1,
        })

    def __enter__(self) -> "_AuditedBinaryFile":
        return self

    def __exit__(self, *_exc: object) -> None:
        _OS_CLOSE(self.fd)
        self.trace.append({
            "path": str(self.path), "call_type": "os_close", "actual_close_calls": 1,
        })

    def read_at(self, offset: int, size: int, *, purpose: str,
                operation_id: str) -> bytes:
        offset = int(offset); size = int(size)
        if offset < 0 or size < 0 or offset + size > self.size:
            raise ValueError(f"bounded read outside {self.path}: {offset}+{size}")
        before = self.position
        self.position = int(_OS_LSEEK(self.fd, offset, os.SEEK_SET))
        self.trace.append({
            "path": str(self.path), "call_type": "os_lseek", "purpose": purpose,
            "operation_id": operation_id, "from_byte": int(before),
            "to_byte": int(self.position), "actual_seek_calls": 1,
        })
        if self.position != offset:
            raise IOError(f"seek did not reach {offset} in {self.path}")
        chunks: list[bytes] = []
        stop = offset + size
        while self.position < stop:
            request = min(1024 * 1024, stop - self.position)
            start = self.position
            part = _OS_READ(self.fd, request)
            if not part:
                break
            self.position += len(part)
            chunks.append(part)
            self.trace.append({
                "path": str(self.path), "call_type": "os_read", "purpose": purpose,
                "operation_id": operation_id, "start_byte_inclusive": int(start),
                "stop_byte_exclusive": int(self.position), "bytes": int(len(part)),
                "requested_bytes": int(request), "actual_read_calls": 1,
            })
        payload = b"".join(chunks)
        if len(payload) != size:
            raise IOError(f"short bounded read in {self.path}: {len(payload)} != {size}")
        return payload


@dataclass(frozen=True)
class _ZipMember:
    name: str
    compression: int
    compressed_bytes: int
    uncompressed_bytes: int
    local_header_offset: int
    member_data_offset: int


@dataclass(frozen=True)
class _NpyMember:
    name: str
    member_data_offset: int
    array_data_offset: int
    member_stop_offset: int
    dtype: np.dtype
    rows: int
    itemsize: int
    fortran_order: bool

    def row_interval(self, start: int, stop: int) -> tuple[int, int]:
        if not 0 <= int(start) <= int(stop) <= self.rows:
            raise ValueError(f"invalid row interval for {self.name}: {start}:{stop}")
        return (
            self.array_data_offset + int(start) * self.itemsize,
            self.array_data_offset + int(stop) * self.itemsize,
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "member_data_offset": self.member_data_offset,
            "array_data_offset": self.array_data_offset,
            "member_stop_offset": self.member_stop_offset,
            "dtype_descr": self.dtype.descr,
            "dtype_names": list(self.dtype.names or ()),
            "rows": self.rows,
            "itemsize": self.itemsize,
            "fortran_order": self.fortran_order,
        }

    @classmethod
    def from_json(cls, row: Mapping[str, Any]) -> "_NpyMember":
        descr = []
        for field in row["dtype_descr"]:
            if len(field) == 2:
                descr.append((field[0], field[1]))
            elif len(field) == 3:
                descr.append((field[0], field[1], tuple(field[2])))
            else:
                raise ValueError("unsupported dtype descriptor in bounded preflight")
        return cls(
            name=str(row["name"]),
            member_data_offset=int(row["member_data_offset"]),
            array_data_offset=int(row["array_data_offset"]),
            member_stop_offset=int(row["member_stop_offset"]),
            dtype=np.dtype(descr), rows=int(row["rows"]),
            itemsize=int(row["itemsize"]),
            fortran_order=bool(row["fortran_order"]),
        )


def _parse_zip_members(handle: _AuditedBinaryFile) -> dict[str, _ZipMember]:
    # NumPy-produced NPZ archives have no ZIP comment.  Require that exact
    # contract so metadata inspection never falls back to a 65 KiB backward
    # scan which could cross into the last member's array payload.
    if handle.size < 22:
        raise ValueError("short ZIP container")
    tail_offset = handle.size - 22
    tail = handle.read_at(
        tail_offset, 22, purpose="zip_metadata",
        operation_id="zip_end_of_central_directory_probe",
    )
    fields = struct.unpack("<4s4H2LH", tail)
    _, disk, cd_disk, disk_entries, total_entries, cd_bytes, cd_offset, comment_bytes = fields
    if fields[0] != b"PK\x05\x06" or comment_bytes != 0:
        raise ValueError("ZIP must have an exact comment-free end record")
    if disk or cd_disk or disk_entries != total_entries:
        raise ValueError("multi-disk ZIP is not supported")
    if total_entries == 0 or cd_offset + cd_bytes > handle.size:
        raise ValueError("invalid ZIP central directory bounds")
    central = handle.read_at(
        cd_offset, cd_bytes, purpose="zip_metadata",
        operation_id="zip_central_directory",
    )
    members: dict[str, _ZipMember] = {}
    cursor = 0
    for index in range(total_entries):
        if cursor + 46 > len(central):
            raise ValueError("truncated ZIP central directory")
        values = struct.unpack_from("<4s6H3L5H2L", central, cursor)
        if values[0] != b"PK\x01\x02":
            raise ValueError("invalid ZIP central-directory signature")
        compression = int(values[4])
        compressed = int(values[8]); uncompressed = int(values[9])
        name_bytes = int(values[10]); extra_bytes = int(values[11]); comment = int(values[12])
        local_offset = int(values[16])
        start = cursor + 46
        stop = start + name_bytes + extra_bytes + comment
        if stop > len(central):
            raise ValueError("truncated ZIP member metadata")
        name = central[start:start + name_bytes].decode("utf-8")
        if name in members:
            raise ValueError(f"duplicate ZIP member {name}")
        local = handle.read_at(
            local_offset, 30, purpose="zip_metadata",
            operation_id=f"zip_local_header:{index}:{name}",
        )
        local_values = struct.unpack("<4s5H3L2H", local)
        if local_values[0] != b"PK\x03\x04":
            raise ValueError(f"invalid local header for {name}")
        local_compression = int(local_values[3])
        local_name = int(local_values[9]); local_extra = int(local_values[10])
        if local_compression != compression:
            raise ValueError(f"ZIP compression mismatch for {name}")
        member_data = local_offset + 30 + local_name + local_extra
        members[name] = _ZipMember(
            name=name, compression=compression, compressed_bytes=compressed,
            uncompressed_bytes=uncompressed, local_header_offset=local_offset,
            member_data_offset=member_data,
        )
        cursor = stop
    if cursor != len(central):
        raise ValueError("unparsed ZIP central-directory bytes")
    return members


def _parse_npy_member(handle: _AuditedBinaryFile, member: _ZipMember) -> _NpyMember:
    if member.compression != 0 or member.compressed_bytes != member.uncompressed_bytes:
        raise ValueError(f"{member.name} is not ZIP_STORED")
    prefix = handle.read_at(
        member.member_data_offset, min(12, member.uncompressed_bytes),
        purpose="npy_metadata", operation_id=f"npy_prefix:{member.name}",
    )
    if len(prefix) < 10 or prefix[:6] != b"\x93NUMPY":
        raise ValueError(f"{member.name} is not an NPY member")
    major, minor = prefix[6], prefix[7]
    if major == 1:
        header_bytes = struct.unpack_from("<H", prefix, 8)[0]; prefix_bytes = 10
    elif major in (2, 3):
        if len(prefix) < 12:
            raise ValueError(f"short NPY v{major}.{minor} prefix")
        header_bytes = struct.unpack_from("<I", prefix, 8)[0]; prefix_bytes = 12
    else:
        raise ValueError(f"unsupported NPY version {major}.{minor}")
    raw_header = handle.read_at(
        member.member_data_offset + prefix_bytes, header_bytes,
        purpose="npy_metadata", operation_id=f"npy_header:{member.name}",
    )
    try:
        header = ast.literal_eval(raw_header.decode("latin1").strip())
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"invalid NPY header for {member.name}") from exc
    dtype = np.dtype(header["descr"])
    shape = tuple(int(value) for value in header["shape"])
    if len(shape) != 1 or shape[0] < 1 or dtype.itemsize < 1:
        raise ValueError(f"{member.name} is not a nonempty row array")
    array_data = member.member_data_offset + prefix_bytes + header_bytes
    member_stop = member.member_data_offset + member.uncompressed_bytes
    if array_data + shape[0] * dtype.itemsize != member_stop:
        raise ValueError(f"NPY shape/size mismatch for {member.name}")
    return _NpyMember(
        name=member.name, member_data_offset=member.member_data_offset,
        array_data_offset=array_data, member_stop_offset=member_stop,
        dtype=dtype, rows=shape[0], itemsize=dtype.itemsize,
        fortran_order=bool(header["fortran_order"]),
    )


def _inspect_stored_npy_members(path: Path, names: Sequence[str],
                                trace: list[dict[str, Any]]) -> dict[str, _NpyMember]:
    with _AuditedBinaryFile(path, trace) as handle:
        zipped = _parse_zip_members(handle)
        missing = sorted(set(names) - set(zipped))
        if missing:
            raise ValueError(f"NPZ lacks required members {missing}")
        return {name: _parse_npy_member(handle, zipped[name]) for name in names}


def _read_member_row(handle: _AuditedBinaryFile, member: _NpyMember, index: int,
                     *, purpose: str, operation_id: str) -> np.void:
    left, right = member.row_interval(index, index + 1)
    payload = handle.read_at(left, right - left, purpose=purpose, operation_id=operation_id)
    return np.frombuffer(payload, dtype=member.dtype, count=1)[0]


def _lower_bound_member(handle: _AuditedBinaryFile, member: _NpyMember,
                        target_ns: int, low: int, high: int,
                        boundary: str) -> int:
    low = int(low); high = int(high); probe = 0
    if "global_time_ns" not in (member.dtype.names or ()):
        raise ValueError(f"{member.name} lacks global_time_ns")
    while low < high:
        middle = (low + high) // 2
        row = _read_member_row(
            handle, member, middle, purpose="binary_search_probe",
            operation_id=f"{member.name}:{boundary}:binary_probe:{probe}",
        )
        probe += 1
        if int(row["global_time_ns"]) < int(target_ns):
            low = middle + 1
        else:
            high = middle
    return low


def _read_member_window(handle: _AuditedBinaryFile, member: _NpyMember,
                        start_ns: int, stop_ns: int, corridor: tuple[int, int],
                        forbidden_byte_intervals: Sequence[tuple[int, int]]) -> tuple[np.ndarray, dict[str, Any]]:
    if stop_ns <= start_ns:
        raise ValueError("member timing window must be nonempty")
    low, high = map(int, corridor)
    if not 0 <= low < high <= member.rows:
        raise ValueError(f"invalid safe row corridor for {member.name}")
    left = _lower_bound_member(handle, member, start_ns, low, high, "start")
    right = _lower_bound_member(handle, member, stop_ns, left, high, "stop")
    byte_left, byte_right = member.row_interval(left, right)
    if any(_overlaps((byte_left, byte_right), row) for row in forbidden_byte_intervals):
        raise ValueError(f"{member.name} sequential window intersects Hxx bytes")
    payload = handle.read_at(
        byte_left, byte_right - byte_left, purpose="selected_sequential_window",
        operation_id=f"{member.name}:selected_episode_plus_two_superframes",
    )
    rows = np.frombuffer(payload, dtype=member.dtype).copy()
    if len(rows) < 2:
        raise RuntimeError(f"{member.name} selected timing window has too few rows")
    times = np.asarray(rows["global_time_ns"], dtype=np.int64)
    if np.any(np.diff(times) <= 0) or int(times[0]) < start_ns or int(times[-1]) >= stop_ns:
        raise RuntimeError(f"{member.name} sequential rows escaped requested timing bracket")
    return rows, {
        "member": member.name,
        "row_start_index": int(left), "row_stop_index": int(right),
        "start_byte_inclusive": int(byte_left), "stop_byte_exclusive": int(byte_right),
        "requested_time_window_ns": [int(start_ns), int(stop_ns)],
        "first_row_global_time_ns": int(times[0]),
        "last_row_global_time_ns": int(times[-1]),
        "rows": int(len(rows)), "all_timed_rows_within_requested_window": True,
    }


def _hxx_authority(root: Path, ledger: Path) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, tuple[int, int]],
]:
    index_path = (root / CLOSURE_INDEX_REL).resolve()
    index_sha = sha256_file(index_path)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    hxx_entries = {row["action"]: row for row in index["entries"] if row["role"] == "HXX"}
    if set(hxx_entries) != {"walk", "golf_swing", "boxing"}:
        raise ValueError("Capture1 Hxx sparse-index authority is incomplete")
    audits = {}
    timing_intervals = {}
    authority_rows = []
    for action, entry in sorted(hxx_entries.items()):
        path = Path(entry["access_artifact"]).resolve()
        digest = sha256_file(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if Path(payload["ledger"]).resolve() != ledger:
            raise ValueError(f"{action}: sparse-index ledger mismatch")
        if set(payload["nodes"]) != set(NODES):
            raise ValueError(f"{action}: sparse-index node coverage mismatch")
        audits[action] = payload
        input_bounds = entry["input_bounds"]
        timing_intervals[action] = (
            int(input_bounds["start_global_time_ns"]),
            int(input_bounds["stop_global_time_ns_exclusive"]),
        )
        authority_rows.append({
            "action": action, "path": str(path), "sha256": digest,
            "source_role": "HASH_BOUND_SPARSE_OFFSET_INDEX_ONLY_NOT_SIGNAL_EVIDENCE",
        })
    return audits, {
        "complete_replay_index": str(index_path),
        "complete_replay_index_sha256": index_sha,
        "hxx_access_audits": authority_rows,
    }, timing_intervals


def _corridor_for_interval(selected: tuple[int, int], hxx_times: Mapping[str, tuple[int, int]],
                           hxx_nodes: Mapping[str, Mapping[str, Any]], node: str,
                           member_rows: int) -> tuple[int, int, str]:
    low = 0; high = int(member_rows); lower = "MEMBER_START"; upper = "MEMBER_END"
    for action, interval in sorted(hxx_times.items(), key=lambda item: item[1][0]):
        action_rows = hxx_nodes[action]
        hxx = action_rows.get("nodes", action_rows)[node]
        hxx_left = int(hxx["slice_start_index"]); hxx_right = int(hxx["slice_stop_index"])
        if _overlaps(selected, interval):
            raise ValueError(f"selected interval overlaps forbidden Capture1 {action}")
        if selected[1] <= interval[0]:
            if hxx_left < high:
                high = hxx_left; upper = f"BEFORE_{action.upper()}_START_INDEX"
            break
        if selected[0] >= interval[1] and hxx_right > low:
            low = hxx_right; lower = f"AFTER_{action.upper()}_STOP_INDEX"
    if low >= high:
        raise ValueError(f"no Hxx-free row corridor for {node}")
    return low, high, f"{lower}__{upper}"


def _trace_summary(trace: Sequence[Mapping[str, Any]], container_bytes: int,
                   forbidden: Sequence[tuple[int, int]]) -> dict[str, Any]:
    reads = [row for row in trace if row.get("call_type") == "os_read"]
    seeks = [row for row in trace if row.get("call_type") == "os_lseek"]
    intervals = [(int(row["start_byte_inclusive"]), int(row["stop_byte_exclusive"])) for row in reads]
    touched = [
        {"actual_read_interval": list(actual), "forbidden_interval": list(blocked)}
        for actual in intervals for blocked in forbidden if _overlaps(actual, blocked)
    ]
    probes: dict[str, dict[str, Any]] = {}
    for row in trace:
        operation = str(row.get("operation_id", ""))
        if ":binary_probe:" not in operation:
            continue
        bucket = probes.setdefault(operation, {
            "operation_id": operation, "actual_os_seek_calls": 0,
            "actual_os_read_calls": 0, "actual_read_intervals": [],
        })
        bucket["actual_os_seek_calls"] += int(row.get("actual_seek_calls", 0))
        bucket["actual_os_read_calls"] += int(row.get("actual_read_calls", 0))
        if row.get("call_type") == "os_read":
            bucket["actual_read_intervals"].append([
                int(row["start_byte_inclusive"]), int(row["stop_byte_exclusive"]),
            ])
    return {
        "instrumentation_layer": "os.read/os.lseek wrappers",
        "actual_os_open_calls": int(sum(row.get("actual_open_calls", 0) for row in trace)),
        "actual_os_close_calls": int(sum(row.get("actual_close_calls", 0) for row in trace)),
        "actual_os_seek_calls": int(sum(row.get("actual_seek_calls", 0) for row in seeks)),
        "actual_os_read_calls": int(sum(row.get("actual_read_calls", 0) for row in reads)),
        "actual_read_intervals": [list(row) for row in intervals],
        "actual_read_union_bytes": int(_union_bytes(intervals)),
        "container_bytes": int(container_bytes),
        "no_full_file_traversal": _union_bytes(intervals) < int(container_bytes),
        "forbidden_intersections": touched,
        "binary_search_probes": list(probes.values()),
        "every_binary_search_probe_separately_accounted": bool(probes) and all(
            row["actual_os_seek_calls"] >= 1 and row["actual_os_read_calls"] >= 1
            for row in probes.values()
        ),
    }


def prepare_capture1_bounded_preflight(
    root: Path, spec: Mapping[str, Any], all_actions: Mapping[str, Mapping[str, Any]],
    selected_actions: Sequence[Mapping[str, Any]], output_path: Path,
    *, preselection_path: Path, preselection_sha256: str,
) -> dict[str, Any]:
    """Freeze all byte corridors before the first node-array payload read."""
    root = Path(root).resolve(); output_path = Path(output_path).resolve()
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite bounded preflight {output_path}")
    ledger = (root / spec["imu_time_ledger"]).resolve()
    raw = (root / spec["raw_container"]).resolve()
    reference_path = ledger.with_name(LEDGER_REFERENCE_NAME)
    reference_sha = sha256_file(reference_path)
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    if Path(reference["path"]).resolve() != ledger or len(reference["sha256"]) != 64:
        raise ValueError("Capture1 ledger reference is invalid")
    hxx_audits, hxx_authority, hxx_times = _hxx_authority(root, ledger)
    hxx_actions = {str(row["action"]) for row in spec["hxx"]}
    if set(hxx_audits) != hxx_actions:
        raise ValueError("Capture1 Hxx action/index inventory mismatch")
    # The sparse Hxx row intervals were produced for the exact replay-index
    # input bounds, so their byte proof must use those same formal time bounds.
    # Broader five-phase episode windows can overlap a neighbouring ordinary
    # action's rest tail and therefore are not interchangeable byte authority.
    trace: list[dict[str, Any]] = []
    member_names = [f"imu_{node}.npy" for node in NODES]
    members = _inspect_stored_npy_members(ledger, member_names, trace)
    required_fields = {
        "boot_epoch", "sequence", "node_timer_us", "global_time_ns", "acc_raw",
        "gyro_raw", "raw_start_offset", "raw_end_offset", "status",
    }
    forbidden_npz: dict[str, list[dict[str, Any]]] = {node: [] for node in NODES}
    forbidden_raw = []
    for action in sorted(hxx_actions):
        audit = hxx_audits[action]
        raw_left = min(int(row["raw_start_offset"]) for row in audit["nodes"].values())
        raw_right = max(int(row["raw_end_offset"]) for row in audit["nodes"].values())
        forbidden_raw.append({
            "action": action, "start_byte_inclusive": raw_left,
            "stop_byte_exclusive": raw_right,
        })
        for node in NODES:
            member = members[f"imu_{node}.npy"]
            authority = audit["nodes"][node]
            if not required_fields.issubset(set(member.dtype.names or ())):
                raise ValueError(f"{member.name}: required bounded fields missing")
            if member.fortran_order:
                raise ValueError(f"{member.name}: Fortran-order payload is unsupported")
            if (
                int(authority["member_rows"]) != member.rows
                or int(authority["member_uncompressed_bytes"]) != (
                    member.member_stop_offset - member.member_data_offset
                )
                or authority["member"] != member.name
                or authority["zip_compression"] != "ZIP_STORED"
            ):
                raise ValueError(f"{action}/{node}: sparse-index member contract mismatch")
            row_left = int(authority["slice_start_index"])
            row_right = int(authority["slice_stop_index"])
            byte_left, byte_right = member.row_interval(row_left, row_right)
            forbidden_npz[node].append({
                "action": action, "row_start_index": row_left,
                "row_stop_index": row_right, "start_byte_inclusive": byte_left,
                "stop_byte_exclusive": byte_right,
                "time_interval_ns": list(hxx_times[action]),
            })
    selected = {}
    for frozen in selected_actions:
        action = str(frozen["action"])
        if action not in all_actions or action in hxx_actions:
            raise ValueError(f"invalid selected Capture1 action {action}")
        actual = all_actions[action]
        episode = actual["episode_bounds"]
        bracket = (
            int(episode["start_global_time_ns"]) - SEQUENTIAL_PADDING_NS,
            int(episode["stop_global_time_ns_exclusive"]) + SEQUENTIAL_PADDING_NS,
        )
        if any(_overlaps(bracket, interval) for interval in hxx_times.values()):
            raise ValueError(f"{action}: padded complete episode overlaps Hxx time")
        corridors = {}
        for node in NODES:
            low, high, basis = _corridor_for_interval(
                bracket, hxx_times, hxx_audits, node, members[f"imu_{node}.npy"].rows,
            )
            corridor_bytes = members[f"imu_{node}.npy"].row_interval(low, high)
            blocked = [
                (int(row["start_byte_inclusive"]), int(row["stop_byte_exclusive"]))
                for row in forbidden_npz[node]
            ]
            if any(_overlaps(corridor_bytes, row) for row in blocked):
                raise ValueError(f"{action}/{node}: safe corridor includes Hxx bytes")
            corridors[node] = {
                "row_start_inclusive": low, "row_stop_exclusive": high,
                "start_byte_inclusive": corridor_bytes[0],
                "stop_byte_exclusive": corridor_bytes[1], "basis": basis,
            }
        selected[action] = {
            "attempt": int(frozen["attempt"]), "partition": frozen["partition"],
            "complete_episode_bounds": dict(frozen["complete_episode_bounds"]),
            "sequential_time_window_ns": list(bracket),
            "sequential_window_semantics": "COMPLETE_EPISODE_PLUS_TWO_SUPERFRAMES_EACH_SIDE",
            "safe_member_corridors": corridors,
        }
    member_payload_intervals = [
        (member.array_data_offset, member.member_stop_offset) for member in members.values()
    ]
    metadata_reads = [
        (int(row["start_byte_inclusive"]), int(row["stop_byte_exclusive"]))
        for row in trace if row.get("call_type") == "os_read"
    ]
    if any(_overlaps(actual, payload) for actual in metadata_reads for payload in member_payload_intervals):
        raise RuntimeError("preflight metadata inspection touched a node-array payload")
    payload = {
        "schema": "biospur-pure-imu-v0-capture1-bounded-access-preflight-v1",
        "status": "IMMUTABLE_BEFORE_FIRST_FRESH_CAPTURE1_ARRAY_OR_RAW_PAYLOAD_READ",
        "capture_id": spec["capture_id"],
        "preselection_path": str(Path(preselection_path).resolve()),
        "preselection_sha256": str(preselection_sha256),
        "selection_changed": False,
        "ledger": {
            "path": str(ledger), "bytes": ledger.stat().st_size,
            "sealed_sha256_imported": reference["sha256"],
            "reference_path": str(reference_path), "reference_sha256": reference_sha,
            "complete_container_hash_recomputed": False,
        },
        "raw": {
            "path": str(raw), "bytes": raw.stat().st_size,
            "sealed_sha256_imported": spec["raw_sha256"],
            "complete_container_hash_recomputed": False,
        },
        "sparse_index_authority": hxx_authority,
        "members": {node: members[f"imu_{node}.npy"].as_json() for node in NODES},
        "forbidden_hxx_time_intervals_ns": {
            action: list(interval) for action, interval in hxx_times.items()
        },
        "forbidden_hxx_npz_member_intervals": forbidden_npz,
        "forbidden_hxx_raw_intervals": forbidden_raw,
        "selected_actions": selected,
        "metadata_only_container_access": {
            "trace": trace,
            "array_payload_bytes_read": 0,
            "action_slice_hashes_computed": 0,
            "mmap_used": False,
            "full_member_opened": False,
            "raw_payload_bytes_read": 0,
            "actual_read_union_bytes": _union_bytes(metadata_reads),
            "complete_container_traversal": False,
        },
        "source_timing_files": [
            {"path": str((root / path).resolve()), "actual_bytes_read": 0,
             "reason": "bounded sealed TIME_EVENT_LEDGER row index supplies Capture1 common time"}
            for path in spec.get("timing_sources", [])
        ],
        "gate": {
            "pass": True,
            "fresh_preselection_frozen_before_access": True,
            "all_selected_actions_have_hxx_free_row_corridors": True,
            "all_hxx_member_and_raw_byte_intervals_bound": True,
            "zip_and_npy_metadata_only": True,
            "whole_member_load_or_mmap": False,
        },
    }
    dump_json(output_path, payload)
    output_path.chmod(0o444)
    return payload


@dataclass(frozen=True)
class _BoundedClockModel:
    a_ns_per_us: float
    b_ns: float
    sigma_ns: float

    def map_ns(self, timer_us: int) -> int:
        return int(round(self.a_ns_per_us * int(timer_us) + self.b_ns))


def _clock_from_rows(rows: np.ndarray, node: str) -> _BoundedClockModel:
    accepted = rows[rows["status"] == 1]
    if len(accepted) < 10 or len(np.unique(accepted["boot_epoch"])) != 1:
        raise RuntimeError(f"{node}: bounded clock rows are insufficient or cross a boot")
    x = np.asarray(accepted["node_timer_us"], dtype=np.float64)
    y = np.asarray(accepted["global_time_ns"], dtype=np.float64)
    x0 = float(np.mean(x)); y0 = float(np.mean(y)); dx = x - x0
    denominator = float(dx @ dx)
    if denominator <= 0:
        raise RuntimeError(f"{node}: degenerate bounded TIMER2 clock")
    slope = float(dx @ (y - y0) / denominator)
    intercept = float(y0 - slope * x0)
    residual = y - (slope * x + intercept)
    if not 995.0 <= slope <= 1005.0:
        raise RuntimeError(f"{node}: implausible bounded TIMER2 slope {slope}")
    return _BoundedClockModel(slope, intercept, max(1.0, float(np.std(residual))))


def _integrity_compare(raw_rows: np.ndarray, ledger_rows: np.ndarray,
                       episode: tuple[int, int], node: str) -> dict[str, Any]:
    ledger = ledger_rows[
        (ledger_rows["status"] == 1)
        & (ledger_rows["global_time_ns"] >= episode[0])
        & (ledger_rows["global_time_ns"] < episode[1])
    ]
    raw = raw_rows[raw_rows["status"] == 1]
    ledger_by_timer = {int(row["node_timer_us"]): row for row in ledger}
    raw_by_timer = {int(row["node_timer_us"]): row for row in raw}
    common = sorted(set(ledger_by_timer) & set(raw_by_timer))
    if len(common) < max(2, int(0.995 * len(ledger))):
        raise RuntimeError(f"{node}: raw/ledger bounded timer coverage mismatch")
    mismatches = 0
    for timer in common:
        a = ledger_by_timer[timer]; b = raw_by_timer[timer]
        if not (
            np.array_equal(a["acc_raw"], b["acc_raw"])
            and np.array_equal(a["gyro_raw"], b["gyro_raw"])
        ):
            mismatches += 1
    if mismatches:
        raise RuntimeError(f"{node}: {mismatches} bounded raw/ledger signal mismatches")
    return {
        "ledger_episode_rows": int(len(ledger)), "raw_episode_rows": int(len(raw)),
        "common_timer_rows": int(len(common)), "coverage_fraction": float(len(common) / len(ledger)),
        "acc_gyro_mismatches": mismatches,
        "semantics": "LEDGER_SIGNAL_FIELDS_USED_ONLY_FOR_TRANSPORT_INTEGRITY_NOT_ESTIMATION",
    }


def load_capture1_calibration_episode_bounded(
    root: Path, spec: Mapping[str, Any], action: Mapping[str, Any],
    preflight_path: Path,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Load one selected complete episode without reading any Hxx/member suffix."""
    root = Path(root).resolve(); preflight_path = Path(preflight_path).resolve()
    if preflight_path.stat().st_mode & 0o222:
        raise RuntimeError("Capture1 bounded preflight is writable")
    preflight_sha = sha256_file(preflight_path)
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    if not preflight.get("gate", {}).get("pass"):
        raise RuntimeError("Capture1 bounded preflight gate is absent")
    name = str(action["action"])
    if name not in preflight["selected_actions"]:
        raise ValueError(f"Capture1 action {name} was not preselected")
    ledger = Path(preflight["ledger"]["path"]).resolve()
    raw_path = Path(preflight["raw"]["path"]).resolve()
    if ledger != (root / spec["imu_time_ledger"]).resolve():
        raise RuntimeError("Capture1 bounded ledger path changed")
    if raw_path != (root / spec["raw_container"]).resolve():
        raise RuntimeError("Capture1 bounded raw path changed")
    selected = preflight["selected_actions"][name]
    episode_absolute = (
        int(action["episode_bounds"]["start_global_time_ns"]),
        int(action["episode_bounds"]["stop_global_time_ns_exclusive"]),
    )
    requested = tuple(map(int, selected["sequential_time_window_ns"]))
    expected_requested = (
        episode_absolute[0] - SEQUENTIAL_PADDING_NS,
        episode_absolute[1] + SEQUENTIAL_PADDING_NS,
    )
    if requested != expected_requested:
        raise RuntimeError(f"{name}: padded timing bracket changed after preflight")
    members = {
        node: _NpyMember.from_json(preflight["members"][node]) for node in NODES
    }
    forbidden_by_node = {
        node: [
            (int(row["start_byte_inclusive"]), int(row["stop_byte_exclusive"]))
            for row in preflight["forbidden_hxx_npz_member_intervals"][node]
        ] for node in NODES
    }
    trace: list[dict[str, Any]] = []
    ledger_rows: dict[str, np.ndarray] = {}
    sequential = []
    with _AuditedBinaryFile(ledger, trace) as handle:
        for node in NODES:
            corridor_row = selected["safe_member_corridors"][node]
            corridor = (
                int(corridor_row["row_start_inclusive"]),
                int(corridor_row["row_stop_exclusive"]),
            )
            rows, window = _read_member_window(
                handle, members[node], requested[0], requested[1], corridor,
                forbidden_by_node[node],
            )
            ledger_rows[node] = rows; sequential.append(window)
    all_forbidden_npz = [row for rows in forbidden_by_node.values() for row in rows]
    trace_audit = _trace_summary(trace, ledger.stat().st_size, all_forbidden_npz)
    if (
        trace_audit["forbidden_intersections"]
        or not trace_audit["no_full_file_traversal"]
        or not trace_audit["every_binary_search_probe_separately_accounted"]
    ):
        raise RuntimeError(f"{name}: hostile Capture1 ledger access gate failed")
    models = {node: _clock_from_rows(ledger_rows[node], node) for node in NODES}
    accepted_for_bounds = {
        node: rows[(rows["status"] == 1)] for node, rows in ledger_rows.items()
    }
    if any(len(rows) < 2 for rows in accepted_for_bounds.values()):
        raise RuntimeError(f"{name}: bounded ledger bracket lacks accepted rows")
    raw_start = min(int(np.min(rows["raw_start_offset"])) for rows in accepted_for_bounds.values())
    raw_stop = max(int(np.max(rows["raw_end_offset"])) for rows in accepted_for_bounds.values())
    forbidden_raw = [
        (int(row["start_byte_inclusive"]), int(row["stop_byte_exclusive"]))
        for row in preflight["forbidden_hxx_raw_intervals"]
    ]
    if any(_overlaps((raw_start, raw_stop), row) for row in forbidden_raw):
        raise RuntimeError(f"{name}: derived raw bracket intersects Hxx before raw open")
    decoded, decode_audit = _decode_imu_only(
        raw_path, raw_start, raw_stop, models, episode_absolute[0], episode_absolute[1],
        forbidden_raw_byte_ranges=iter(forbidden_raw),
    )
    integrity = {
        node: _integrity_compare(decoded[node], ledger_rows[node], episode_absolute, node)
        for node in NODES
    }
    node_audit = {
        node: {
            **decode_audit["nodes"][node],
            "transport_integrity": integrity[node],
            "source": "FRESH_BOUNDED_RAW_COBS_DECODE",
        } for node in NODES
    }
    formal = action["formal_action_bounds"]
    normalized_formal = {
        **dict(formal),
        "start_global_time_ns": int(formal["start_global_time_ns"]) - episode_absolute[0],
        "stop_global_time_ns_exclusive": (
            int(formal["stop_global_time_ns_exclusive"]) - episode_absolute[0]
        ),
    }
    normalized_episode = {
        **dict(action["episode_bounds"]), "start_global_time_ns": 0,
        "stop_global_time_ns_exclusive": episode_absolute[1] - episode_absolute[0],
    }
    timing_access = {
        "schema": "biospur-capture1-bounded-npz-timing-index-access-v1",
        "instrumented_file": str(ledger),
        "timing_index_semantics": "SEALED_COMMON_CLOCK_LEDGER_GLOBAL_TIME_AND_RAW_OFFSET_INDEX",
        "sequential_padding_superframes_each_side": 2,
        "sequential_padding_ns_each_side": SEQUENTIAL_PADDING_NS,
        "sequential_windows": sequential,
        "search_ceiling_contracts": [
            {"node": node, **selected["safe_member_corridors"][node]}
            for node in NODES
        ],
        "binary_search_probe_count": len(trace_audit["binary_search_probes"]),
        "binary_search_probe_audit": trace_audit["binary_search_probes"],
        "all_sequential_timing_rows_within_action_plus_two_superframes": all(
            row["all_timed_rows_within_requested_window"] for row in sequential
        ),
        "every_binary_search_probe_separately_accounted": trace_audit[
            "every_binary_search_probe_separately_accounted"
        ],
        "no_full_file_traversal_proven_by_actual_read_union": trace_audit[
            "no_full_file_traversal"
        ],
        "golf_boxing_timing_interval_bytes_touched": bool(
            trace_audit["forbidden_intersections"]
        ),
        "all_hxx_timing_interval_bytes_touched": bool(
            trace_audit["forbidden_intersections"]
        ),
        "forbidden_intersections": trace_audit["forbidden_intersections"],
        "raw_io_call_accounting": trace_audit,
        "mmap_used": False,
        "complete_member_opened": False,
    }
    return decoded, {
        "schema": "biospur-pure-imu-v0-capture1-bounded-raw6-episode-access-v2",
        "capture_id": spec["capture_id"], "action": name,
        "preflight_path": str(preflight_path), "preflight_sha256": preflight_sha,
        "ledger": str(ledger),
        "ledger_sealed_sha256_imported": preflight["ledger"]["sealed_sha256_imported"],
        "ledger_complete_hash_recomputed": False,
        "raw_path": str(raw_path),
        "raw_sealed_sha256_imported": preflight["raw"]["sealed_sha256_imported"],
        "raw_complete_hash_recomputed": False,
        "read_bracket": {
            "start_byte_inclusive": raw_start, "stop_byte_exclusive": raw_stop,
            "slice_sha256": decode_audit["raw_access"]["slice_sha256"],
            "boundary": "MIN_MAX_BOUNDED_LEDGER_ROWS_FOR_COMPLETE_EPISODE_PLUS_TWO_SUPERFRAMES",
        },
        "timing_access": timing_access,
        "decode": decode_audit, "nodes": node_audit,
        "episode_bounds": normalized_episode, "formal_action_bounds": normalized_formal,
        "boundary_authority": {
            "pre": action["episode_bounds"]["pre_boundary_event"],
            "formal_start": action["episode_bounds"]["formal_start_event"],
            "formal_stop": action["episode_bounds"]["formal_stop_event"],
            "post": action["episode_bounds"]["post_boundary_policy"],
            "event_source": action["episode_bounds"]["event_source"],
            "event_source_sha256": action["episode_bounds"]["event_source_sha256"],
        },
        "estimator_signal_source": "FRESH_BOUNDED_RAW_COBS_ACCELEROMETER_GYROSCOPE_DECODE",
        "ledger_signal_fields_role": "TRANSPORT_INTEGRITY_COMPARISON_ONLY",
        "opened_payload_classes": ["TEN_NODE_RAW_ACCELEROMETER_GYROSCOPE"],
        "spatial_members_opened": [], "uwb_spatial_payload_consumed": False,
        "hxx_payload_opened": False,
        "hxx_payload_nonaccess_proof": {
            "npz_forbidden_intersections": trace_audit["forbidden_intersections"],
            "raw_forbidden_interval_bytes_touched": decode_audit["raw_access"][
                "forbidden_interval_bytes_touched"
            ],
        },
        "invalidated_attempt_payload_opened": False,
        "whole_capture_node_array_loaded_or_mmaped": False,
        "source_timing_files": preflight["source_timing_files"],
    }
