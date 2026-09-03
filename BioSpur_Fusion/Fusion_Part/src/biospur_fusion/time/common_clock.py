"""Strict Listener-backed TIMER2 common-clock reconstruction for 120 ms TDMA."""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import numpy as np

SUPERFRAME_US = 120_000.0
SLOT_US = 10_000.0
DW_TICKS_PER_US = 63_897.6
CLOCK_OUTLIER_HARD_LIMIT_US = 5_000.0
CLEAN_RESIDUAL_P95_GATE_US = 500.0
CLEAN_RESIDUAL_MAX_GATE_US = 1_000.0
RAW_RESIDUAL_P99_GATE_US = 2_000.0
RAW_RESIDUAL_MAX_GATE_US = 5_000.0
MIN_CLEAN_LISTENER_PAIRS = 50
MIN_ACTION_CLEAN_LISTENER_PAIRS = 10
MIN_CAPTURE_SPAN_COVERAGE = 0.80
MAX_CLEAN_ANCHOR_GAP_S = 60.0
MAX_REJECTION_FRACTION = 0.20

UWB_RE = re.compile(
    r"^(\d+\.\d+)\s+(\d+\.\d+).*?FUSION_UWB proto=7 name=(BSF[0-9A-F]+) "
    r"master_ms=(\d+).*?sweep=(\d+).*?frame_us=(\d+) strobe_us=(\d+).*?flags=0x([0-9a-fA-F]+)"
)


def _required_clean_listener_pairs(capture_duration_s: float) -> int:
    """Require one pair per complete three-second interval, within hard bounds.

    Coverage, maximum-gap, rejection, and residual gates below independently
    constrain the time distribution and quality of those pairs.  Counting only
    complete intervals avoids making a few milliseconds of boundary padding
    demand evidence from a further three-second interval that does not exist.
    """
    complete_intervals = int(math.floor(max(0.0, float(capture_duration_s)) / 3.0))
    return min(
        MIN_CLEAN_LISTENER_PAIRS,
        max(MIN_ACTION_CLEAN_LISTENER_PAIRS, complete_intervals),
    )


def _integer_join_resolved(join: Mapping[str, Any]) -> bool:
    """An absolute epoch is resolved by a unique modal seed plus exact mod-16.

    A unique mode need not contain more than half of all noisy host-nearest
    seeds.  Requiring an absolute majority incorrectly reports ambiguity when
    the winning integer has a positive vote margin over every alternative.
    """
    return bool(
        int(join.get("integer_choice_margin_seed_votes", 0)) > 0
        and bool(join.get("unique_modal_seed_winner", False))
        and float(join.get("mod16_agreement_fraction", 0.0)) == 1.0
    )

TIMING_BRACKET_S = 2.0 * SUPERFRAME_US / 1e6

# Named indirections keep the production path on the real OS primitives while
# allowing hostile tests to independently spy on every call.
_OS_OPEN = os.open
_OS_CLOSE = os.close
_OS_READ = os.read
_OS_LSEEK = os.lseek


def _sha256_small_metadata(path: Path) -> str:
    """Hash a small authority document, never a continuous capture container."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _AuditedTimingFile:
    """Unbuffered timing reader whose audit is built from real OS calls.

    Binary probes read exact lines one byte at a time.  This is intentionally
    slower than buffered ``readline`` but prevents invisible readahead across
    an access boundary.  The selected sequential interval is then read in
    bounded chunks which may not cross its already-located stop offset.
    """

    def __init__(self, path: Path, trace: list[dict[str, Any]]):
        self.path = Path(path).resolve()
        self.trace = trace
        self.fd = _OS_OPEN(self.path, os.O_RDONLY)
        self.position = 0

    def close(self) -> None:
        _OS_CLOSE(self.fd)

    def __enter__(self) -> "_AuditedTimingFile":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def tell(self) -> int:
        return int(self.position)

    def seek(self, offset: int, *, purpose: str, operation_id: str) -> int:
        before = self.position
        after = int(_OS_LSEEK(self.fd, int(offset), os.SEEK_SET))
        self.position = after
        self.trace.append({
            "path": str(self.path), "call_type": "os_lseek",
            "purpose": purpose, "operation_id": operation_id,
            "from_byte": int(before), "to_byte": after,
            "actual_seek_calls": 1,
        })
        return after

    def readline_exact(self, *, purpose: str, operation_id: str,
                       stop_byte_exclusive: int) -> bytes:
        start = self.position; chunks: list[bytes] = []; calls = 0
        while self.position < int(stop_byte_exclusive):
            part = _OS_READ(self.fd, 1); calls += 1
            if not part:
                break
            chunks.append(part); self.position += len(part)
            if part == b"\n":
                break
        stop = self.position
        self.trace.append({
            "path": str(self.path), "call_type": "os_read",
            "purpose": purpose, "operation_id": operation_id,
            "start_byte_inclusive": int(start), "stop_byte_exclusive": int(stop),
            "bytes": int(stop - start), "actual_read_calls": int(calls),
            "read_request_size_bytes": 1,
        })
        return b"".join(chunks)

    def read_exact_interval(self, stop_byte_exclusive: int, *, purpose: str,
                            operation_id: str, chunk_bytes: int = 64 * 1024) -> bytes:
        start = self.position; chunks: list[bytes] = []; calls = 0
        requested_stop = int(stop_byte_exclusive)
        while self.position < requested_stop:
            request = min(int(chunk_bytes), requested_stop - self.position)
            part = _OS_READ(self.fd, request); calls += 1
            if not part:
                break
            chunks.append(part); self.position += len(part)
        stop = self.position
        self.trace.append({
            "path": str(self.path), "call_type": "os_read",
            "purpose": purpose, "operation_id": operation_id,
            "start_byte_inclusive": int(start), "stop_byte_exclusive": int(stop),
            "bytes": int(stop - start), "actual_read_calls": int(calls),
            "maximum_read_request_size_bytes": int(chunk_bytes),
        })
        if stop != requested_stop:
            raise IOError(f"short bounded timing read in {self.path}")
        return b"".join(chunks)


def _jsonl_time_ns(line: bytes) -> int | None:
    try:
        return int(json.loads(line)["arrival_monotonic_ns"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _fusion_log_time_ns(line: bytes) -> int | None:
    try:
        fields = line.split(maxsplit=2)
        return int(round(float(fields[1]) * 1e9)) if len(fields) >= 2 else None
    except (ValueError, OverflowError):
        return None


def _timed_line_at_or_after(handle: _AuditedTimingFile, path: Path, offset: int,
                            time_parser: Callable[[bytes], int | None],
                            trace: list[dict[str, Any]], purpose: str,
                            operation_id: str, stop_byte_exclusive: int) -> tuple[int, int, int] | None:
    """Return the next parseable complete line at/after an arbitrary byte offset."""
    handle.seek(max(0, int(offset)), purpose=purpose, operation_id=operation_id)
    if offset:
        handle.readline_exact(
            purpose=purpose + ":discard_partial_line", operation_id=operation_id,
            stop_byte_exclusive=stop_byte_exclusive,
        )
    for _ in range(128):
        start = handle.tell()
        line = handle.readline_exact(
            purpose=purpose, operation_id=operation_id,
            stop_byte_exclusive=stop_byte_exclusive,
        )
        if not line:
            return None
        timestamp = time_parser(line)
        if timestamp is not None:
            stop = handle.tell()
            trace.append({
                "path": str(Path(path).resolve()), "call_type": "parsed_timing_row",
                "purpose": purpose, "operation_id": operation_id,
                "start_byte_inclusive": int(start), "stop_byte_exclusive": int(stop),
                "timestamp_ns": int(timestamp),
            })
            return start, stop, timestamp
    raise ValueError(f"no timed record near byte {offset} in {path}")


def _lower_bound_timed_file(handle: _AuditedTimingFile, path: Path, target_ns: int,
                            time_parser: Callable[[bytes], int | None],
                            trace: list[dict[str, Any]], *, high: int,
                            boundary_name: str) -> int:
    """Binary-seek a monotonic line log without traversing the full file."""
    path = Path(path); size = path.stat().st_size; low = 0; high = int(high)
    probe_index = 0
    while high - low > 8192:
        middle = (low + high) // 2
        operation_id = f"{path.name}:{boundary_name}:binary_probe:{probe_index}"
        row = _timed_line_at_or_after(
            handle, path, middle, time_parser, trace, "binary_search_probe",
            operation_id, high,
        )
        probe_index += 1
        if row is None:
            high = middle
            continue
        start, stop, timestamp = row
        if timestamp < target_ns:
            low = stop
        else:
            high = start
    operation_id = f"{path.name}:{boundary_name}:lower_bound_refinement"
    handle.seek(low, purpose="lower_bound_refinement", operation_id=operation_id)
    while handle.tell() < high:
        start = handle.tell()
        line = handle.readline_exact(
            purpose="lower_bound_refinement", operation_id=operation_id,
            stop_byte_exclusive=high,
        )
        if not line:
            return size
        timestamp = time_parser(line)
        if timestamp is not None:
            trace.append({
                "path": str(path.resolve()), "call_type": "parsed_timing_row",
                "purpose": "lower_bound_refinement", "operation_id": operation_id,
                "start_byte_inclusive": int(start),
                "stop_byte_exclusive": int(handle.tell()), "timestamp_ns": int(timestamp),
            })
            if timestamp >= target_ns:
                return start
    return high


def _read_bounded_timed_lines(path: Path, start_ns: int, stop_ns: int,
                              time_parser: Callable[[bytes], int | None],
                              trace: list[dict[str, Any]], *,
                              search_ceiling_fraction: float | None = None,
                              safe_ceiling_seed_offset: int | None = None,
                              forbidden_time_intervals_ns: Iterable[tuple[int, int]] = ()) -> list[bytes]:
    """Read one half-open timing interval after an indexed binary seek."""
    if stop_ns <= start_ns:
        raise ValueError("timing interval must be non-empty")
    path = Path(path); size = path.stat().st_size
    forbidden = tuple((int(left), int(right)) for left, right in forbidden_time_intervals_ns)
    with _AuditedTimingFile(path, trace) as handle:
        search_high = size
        if forbidden:
            earliest_forbidden = min(left for left, _ in forbidden)
            if safe_ceiling_seed_offset is not None:
                operation_id = f"{path.name}:search_ceiling_seed_advance"
                seed_offset = max(1, min(size - 1, int(safe_ceiling_seed_offset)))
                handle.seek(
                    seed_offset, purpose="search_ceiling_seed_advance",
                    operation_id=operation_id,
                )
                row = None
                for _ in range(100_000):
                    row_start = handle.tell()
                    line = handle.readline_exact(
                        purpose="search_ceiling_seed_advance",
                        operation_id=operation_id, stop_byte_exclusive=size,
                    )
                    if not line:
                        break
                    timestamp = time_parser(line)
                    if timestamp is None:
                        continue
                    row_stop = handle.tell()
                    trace.append({
                        "path": str(path.resolve()), "call_type": "parsed_timing_row",
                        "purpose": "search_ceiling_seed_advance",
                        "operation_id": operation_id,
                        "start_byte_inclusive": int(row_start),
                        "stop_byte_exclusive": int(row_stop),
                        "timestamp_ns": int(timestamp),
                    })
                    row = (row_start, row_stop, timestamp)
                    if timestamp > stop_ns:
                        break
                if row is None:
                    raise ValueError(f"no timed search-ceiling row after seed in {path}")
                row_start, row_stop, timestamp = row
                ceiling_offset = seed_offset
                ceiling_method = "HASH_BOUND_EXACT_BYTE_SEED_MONOTONIC_ADVANCE"
            else:
                if search_ceiling_fraction is None or not 0.0 < search_ceiling_fraction < 1.0:
                    raise ValueError(
                        "forbidden timing intervals require an exact seed or safe ceiling fraction"
                    )
                operation_id = f"{path.name}:search_ceiling_probe"
                ceiling_offset = max(1, min(size - 1, int(size * search_ceiling_fraction)))
                row = _timed_line_at_or_after(
                    handle, path, ceiling_offset, time_parser, trace,
                    "search_ceiling_probe", operation_id, size,
                )
                if row is None:
                    raise ValueError(f"no timed search-ceiling row in {path}")
                row_start, row_stop, timestamp = row
                ceiling_method = "FRACTIONAL_PROBE"
            if not stop_ns < timestamp < earliest_forbidden:
                raise ValueError(
                    f"unsafe search-ceiling probe in {path}: {timestamp} not between "
                    f"selected stop {stop_ns} and forbidden start {earliest_forbidden}"
                )
            search_high = row_start
            trace.append({
                "path": str(path.resolve()), "call_type": "search_ceiling_contract",
                "purpose": "search_ceiling_probe", "operation_id": operation_id,
                "probe_offset": int(ceiling_offset), "safe_search_stop_byte_exclusive": int(search_high),
                "probe_row_stop_byte_exclusive": int(row_stop),
                "probe_timestamp_ns": int(timestamp),
                "ceiling_method": ceiling_method,
                "earliest_forbidden_timestamp_ns": int(earliest_forbidden),
                "monotonic_suffix_excluded_from_all_binary_and_sequential_reads": True,
            })
        start_offset = _lower_bound_timed_file(
            handle, path, start_ns, time_parser, trace, high=search_high,
            boundary_name="start",
        )
        stop_offset = _lower_bound_timed_file(
            handle, path, stop_ns, time_parser, trace, high=search_high,
            boundary_name="stop",
        )
        if not 0 <= start_offset <= stop_offset <= search_high:
            raise ValueError(f"invalid bounded timing offsets in {path}")
        operation_id = f"{path.name}:selected_timing_window"
        handle.seek(start_offset, purpose="selected_timing_window", operation_id=operation_id)
        payload = handle.read_exact_interval(
            stop_offset, purpose="selected_timing_window", operation_id=operation_id,
        )
    rows = payload.splitlines(keepends=True)
    timed_rows = []
    cursor = start_offset
    for line in rows:
        timestamp = time_parser(line)
        if timestamp is not None:
            if not start_ns <= timestamp < stop_ns:
                raise ValueError(f"sequential timing row escaped selected bracket in {path}")
            timed_rows.append(line)
            trace.append({
                "path": str(path.resolve()), "call_type": "parsed_timing_row",
                "purpose": "selected_timing_window", "operation_id": operation_id,
                "start_byte_inclusive": int(cursor),
                "stop_byte_exclusive": int(cursor + len(line)), "timestamp_ns": int(timestamp),
            })
        cursor += len(line)
    trace.append({
        "path": str(path.resolve()), "call_type": "sequential_window_contract",
        "purpose": "selected_timing_window", "operation_id": operation_id,
        "start_byte_inclusive": int(start_offset), "stop_byte_exclusive": int(stop_offset),
        "requested_time_window_ns": [int(start_ns), int(stop_ns)],
        "timed_rows": int(len(timed_rows)),
        "all_timed_rows_within_requested_window": True,
    })
    return timed_rows


def _coalesce_trace(trace: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Compact actual OS reads while preserving every path/purpose boundary."""
    ordered = sorted(
        (dict(row) for row in trace if row.get("call_type") == "os_read" and row.get("bytes", 0)),
        key=lambda row: (row["path"], row["purpose"], row["start_byte_inclusive"]),
    )
    result: list[dict[str, Any]] = []
    for row in ordered:
        if (
            result and result[-1]["path"] == row["path"]
            and result[-1]["purpose"] == row["purpose"]
            and row["start_byte_inclusive"] <= result[-1]["stop_byte_exclusive"]
        ):
            result[-1]["stop_byte_exclusive"] = max(
                result[-1]["stop_byte_exclusive"], row["stop_byte_exclusive"],
            )
            result[-1]["bytes"] = (
                result[-1]["stop_byte_exclusive"] - result[-1]["start_byte_inclusive"]
            )
            result[-1]["actual_read_calls"] += int(row.get("actual_read_calls", 0))
        else:
            result.append(row)
    return result


def _union_interval_bytes(rows: Iterable[Mapping[str, Any]]) -> int:
    intervals = sorted(
        (int(row["start_byte_inclusive"]), int(row["stop_byte_exclusive"]))
        for row in rows if int(row.get("stop_byte_exclusive", 0)) > int(row.get("start_byte_inclusive", 0))
    )
    if not intervals:
        return 0
    total = 0; start, stop = intervals[0]
    for left, right in intervals[1:]:
        if left <= stop:
            stop = max(stop, right)
        else:
            total += stop - start; start, stop = left, right
    return total + stop - start


def _binary_probe_audit(trace: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for original in trace:
        row = dict(original)
        if ":binary_probe:" in str(row.get("operation_id", "")):
            grouped[(str(row["path"]), str(row["operation_id"]))].append(row)
    output = []
    for (path, operation_id), rows in sorted(grouped.items()):
        reads = [row for row in rows if row.get("call_type") == "os_read"]
        seeks = [row for row in rows if row.get("call_type") == "os_lseek"]
        parsed = [row for row in rows if row.get("call_type") == "parsed_timing_row"]
        output.append({
            "path": path, "operation_id": operation_id,
            "actual_os_seek_calls": int(sum(row.get("actual_seek_calls", 0) for row in seeks)),
            "actual_os_read_calls": int(sum(row.get("actual_read_calls", 0) for row in reads)),
            "actual_read_intervals": [[
                int(row["start_byte_inclusive"]), int(row["stop_byte_exclusive"]),
            ] for row in reads],
            "parsed_row_timestamp_ns": int(parsed[-1]["timestamp_ns"]) if parsed else None,
            "parsed_row_byte_interval": [
                int(parsed[-1]["start_byte_inclusive"]), int(parsed[-1]["stop_byte_exclusive"]),
            ] if parsed else None,
        })
    return output


@dataclass(frozen=True)
class UwbClockAnchor:
    node: str
    host_monotonic_s: float
    master_arrival_ms: int
    sweep: int
    frame_us: int
    strobe_us: int
    sf_valid: bool
    sf_mod16: int | None


@dataclass(frozen=True)
class ListenerPoll:
    listener: str
    src: int
    sequence: int
    absolute_epoch: int
    phase_us: float
    host_monotonic_s: float


@dataclass(frozen=True)
class ClockModel:
    node_id: str
    boot_epoch: int
    a_ns_per_us: float
    b_ns: float
    sigma_ns: float
    first_timer_us: int
    last_timer_us: int
    integer_epoch_offset: int
    integer_choice_margin_epochs: int
    listener_pairs: int
    clean_pairs: int
    rejected_pairs: int
    clean_residual_p95_us: float
    clean_residual_max_us: float
    raw_residual_p95_us: float
    raw_residual_p99_us: float
    raw_residual_max_us: float
    capture_span_coverage: float
    max_clean_anchor_gap_s: float
    rejection_fraction: float
    drift_ppm: float
    mod16_agreement_fraction: float
    timestamp_reversals: int

    def map_ns(self, timer_us: int) -> int:
        return int(round(self.a_ns_per_us * int(timer_us) + self.b_ns))


def _robust_line(x: np.ndarray, y: np.ndarray, iterations: int = 12,
                 hard_limit: float = CLOCK_OUTLIER_HARD_LIMIT_US,
                 floor: float = 300.0) -> tuple[float, float, np.ndarray]:
    x = np.asarray(x, float); y = np.asarray(y, float)
    if x.size < 3 or x.size != y.size:
        raise ValueError("clock fit needs at least three pairs")
    x0 = float(np.mean(x)); y0 = float(np.mean(y)); keep = np.ones(x.size, bool)
    for _ in range(iterations):
        xx = x[keep] - x0; yy = y[keep] - y0
        slope = float(xx @ yy / (xx @ xx)); intercept = y0 - slope * x0
        residual = y - (slope * x + intercept)
        centre = float(np.median(residual[keep]))
        mad = 1.4826 * float(np.median(np.abs(residual[keep] - centre)))
        # Predeclared physical clean-anchor classifier.  Its 5 ms hard ceiling
        # is deliberately looser than the independent 1 ms clean-max gate, so
        # the acceptance gate cannot be made tautological by classification.
        limit = min(float(hard_limit), max(float(floor), 6.0 * mad))
        new_keep = np.abs(residual - centre) <= limit
        if np.array_equal(new_keep, keep):
            break
        keep = new_keep
    xx = x[keep] - float(np.mean(x[keep])); yy = y[keep] - float(np.mean(y[keep]))
    slope = float(xx @ yy / (xx @ xx))
    intercept = float(np.mean(y[keep]) - slope * np.mean(x[keep]))
    return slope, intercept, keep


def parse_fusion_anchors(log_path: Path, start_s: float, end_s: float) -> dict[str, list[UwbClockAnchor]]:
    out: dict[str, list[UwbClockAnchor]] = defaultdict(list)
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "FUSION_UWB" not in line:
                continue
            match = UWB_RE.search(line)
            if not match:
                continue
            _, mono, node, master, sweep, frame, strobe, flags_text = match.groups()
            host = float(mono)
            if not start_s <= host <= end_s:
                continue
            flags = int(flags_text, 16)
            valid = bool(flags & 0x80)
            mod16 = ((flags >> 3) & 0x0F) if valid else None
            out[node].append(UwbClockAnchor(node, host, int(master), int(sweep), int(frame), int(strobe), valid, mod16))
    return dict(out)


def parse_fusion_anchors_bounded(
    log_path: Path, start_s: float, end_s: float, trace: list[dict[str, Any]],
    *, search_ceiling_fraction: float | None = None,
    safe_ceiling_seed_offsets: Mapping[str, int] | None = None,
    forbidden_time_intervals_ns: Iterable[tuple[int, int]] = (),
) -> dict[str, list[UwbClockAnchor]]:
    """Parse only the action interval plus two superframes of timing brackets."""
    read_start_s = float(start_s) - TIMING_BRACKET_S
    read_stop_s = float(end_s) + TIMING_BRACKET_S
    lines = _read_bounded_timed_lines(
        Path(log_path), int(round(read_start_s * 1e9)), int(round(read_stop_s * 1e9)),
        _fusion_log_time_ns, trace, search_ceiling_fraction=search_ceiling_fraction,
        safe_ceiling_seed_offset=(safe_ceiling_seed_offsets or {}).get(
            str(Path(log_path).resolve())
        ),
        forbidden_time_intervals_ns=forbidden_time_intervals_ns,
    )
    out: dict[str, list[UwbClockAnchor]] = defaultdict(list)
    for raw in lines:
        line = raw.decode("utf-8", errors="replace")
        if "FUSION_UWB" not in line:
            continue
        match = UWB_RE.search(line)
        if not match:
            continue
        _, mono, node, master, sweep, frame, strobe, flags_text = match.groups()
        host = float(mono)
        if not read_start_s <= host < read_stop_s:
            continue
        flags = int(flags_text, 16); valid = bool(flags & 0x80)
        mod16 = ((flags >> 3) & 0x0F) if valid else None
        out[node].append(UwbClockAnchor(
            node, host, int(master), int(sweep), int(frame), int(strobe), valid, mod16,
        ))
    return dict(out)


def reconstruct_local_epochs(strobes: Iterable[int]) -> tuple[np.ndarray, float]:
    timer = np.asarray(list(strobes), float)
    if timer.size < 3 or np.any(np.diff(timer) <= 0):
        raise ValueError("TIMER2 reversal or insufficient UWB anchors")
    period = SUPERFRAME_US
    for _ in range(20):
        multiples = np.maximum(1, np.rint(np.diff(timer) / period).astype(np.int64))
        epochs = np.r_[0, np.cumsum(multiples)]
        slope, _, _ = _robust_line(epochs.astype(float), timer)
        if abs(slope - period) < 1e-7:
            period = slope
            break
        period = slope
    return epochs.astype(np.int64), period


def _listener_beacon_fit(rows: list[tuple[int, int]]) -> tuple[float, float]:
    values = np.asarray(rows, float)
    slope, intercept, keep = _robust_line(
        values[:, 0], values[:, 1],
        hard_limit=999.0 * DW_TICKS_PER_US,
        floor=300.0 * DW_TICKS_PER_US,
    )
    if int(keep.sum()) < 10:
        raise ValueError("insufficient clean Listener Beacon records")
    return intercept, slope


def load_listener_polls(listener_dir: Path, start_s: float, end_s: float,
                        src_slots: Mapping[int, int]) -> tuple[list[ListenerPoll], dict]:
    summary = json.loads((listener_dir / "summary.json").read_text(encoding="utf-8"))
    polls: list[ListenerPoll] = []
    audit: dict[str, dict] = {}
    for snr, info in sorted(summary["listeners"].items()):
        role = info.get("first_lstat", {}).get("role")
        kinds = info.get("kinds", {})
        usable = role == "OBSERVER" and kinds.get("LPD", 0) and kinds.get("LBD", 0)
        row_audit = {"listener_key": info["listener_key"], "role": role, "usable": bool(usable)}
        audit[snr] = row_audit
        if not usable:
            continue
        beacons: list[tuple[int, int]] = []
        raw_polls: list[dict] = []
        with (listener_dir / "listeners" / f"{snr}.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                host = int(row["arrival_monotonic_ns"]) / 1e9
                if host < start_s - 1.0 or host > end_s + 1.0:
                    continue
                fields = row.get("fields", {})
                if row.get("kind") == "LBD" and row.get("rx_unwrapped_ticks") is not None:
                    beacons.append((int(fields["superframe_counter"]), int(row["rx_unwrapped_ticks"])))
                elif row.get("kind") == "LPD" and int(fields.get("src", -1)) in src_slots:
                    raw_polls.append(row)
        intercept, ticks_per_epoch = _listener_beacon_fit(beacons)
        phase_errors = []
        for row in raw_polls:
            fields = row["fields"]; src = int(fields["src"]); slot = src_slots[src]
            fractional = (int(row["rx_unwrapped_ticks"]) - intercept) / ticks_per_epoch
            expected_phase = slot * SLOT_US + 3900.0
            epoch = int(round(fractional - expected_phase / SUPERFRAME_US))
            phase = (fractional - epoch) * SUPERFRAME_US
            phase_errors.append(phase - expected_phase)
            polls.append(ListenerPoll(info["listener_key"], src, int(fields["poll_seq"]), epoch,
                                      float(phase), int(row["arrival_monotonic_ns"]) / 1e9))
        row_audit.update({"beacons_used": len(beacons), "polls_used": len(raw_polls),
                          "beacon_period_us": ticks_per_epoch / DW_TICKS_PER_US,
                          "phase_choice_max_error_us": max(map(abs, phase_errors), default=None)})
    return polls, {"capture_summary_pass": summary.get("pass"), "listeners": audit}


def _load_capture_identity_authority(
    readiness_path: Path, expected_nodes: Iterable[str], expected_sha256: str | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Resolve B306, logical tag/slot, DWM identity, and DWM boot per capture."""
    readiness_path = Path(readiness_path).resolve()
    actual_sha = _sha256_small_metadata(readiness_path)
    if expected_sha256 is not None and actual_sha != expected_sha256:
        raise ValueError("capture readiness/identity authority SHA-256 mismatch")
    payload = json.loads(readiness_path.read_text(encoding="utf-8"))
    mapping = payload.get("tdma_verify", {}).get("mapping", {})
    cfg_rows = {
        str(row.get("node")): row
        for row in payload.get("tdma_verify", {}).get("rows", [])
        if row.get("command") == "CFG_STATUS"
    }
    expected = set(expected_nodes)
    if set(mapping) != expected or set(cfg_rows) != expected:
        raise ValueError(
            "capture identity authority fleet mismatch "
            f"mapping_missing={sorted(expected-set(mapping))} cfg_missing={sorted(expected-set(cfg_rows))}"
        )
    identity: dict[str, dict[str, Any]] = {}
    for node in sorted(expected):
        slot = int(mapping[node]["slot"]); tag = int(mapping[node]["tag"])
        fields = cfg_rows[node].get("fields", {})
        cfg_slot = int(str(fields["slot"]).split("/", 1)[0])
        cfg_tag = int(fields["tag"])
        if slot != cfg_slot or tag != cfg_tag:
            raise ValueError(f"capture identity authority disagrees for {node}")
        identity[node] = {
            "capture_node_identity": node,
            "logical_slot": slot,
            "logical_tag_id": tag,
            "dwm_identity": str(fields["bs"]),
            "dwm_boot_identity": int(fields["boot"]),
            "b306_firmware_identity": payload.get("fusion", {}).get("identity", {}).get(node),
        }
    slots = [row["logical_slot"] for row in identity.values()]
    tags = [row["logical_tag_id"] for row in identity.values()]
    dwm = [row["dwm_identity"] for row in identity.values()]
    if len(set(slots)) != len(identity) or len(set(tags)) != len(identity) or len(set(dwm)) != len(identity):
        raise ValueError("duplicate slot, tag, or DWM identity in capture authority")
    return identity, {
        "schema": "biospur-capture-bound-identity-authority-v1",
        "authority_path": str(readiness_path),
        "authority_sha256": actual_sha,
        "mapping": identity,
        "coverage_exactly_once": True,
        "cross_capture_mapping_reuse": False,
    }


def _bounded_listener_rows(
    listener_dir: Path, start_s: float, end_s: float, trace: list[dict[str, Any]],
    *, search_ceiling_fraction: float | None = None,
    safe_ceiling_seed_offsets: Mapping[str, int] | None = None,
    forbidden_time_intervals_ns: Iterable[tuple[int, int]] = (),
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    listener_dir = Path(listener_dir).resolve()
    summary_path = listener_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    read_start_ns = int(round((float(start_s) - TIMING_BRACKET_S) * 1e9))
    read_stop_ns = int(round((float(end_s) + TIMING_BRACKET_S) * 1e9))
    rows_by_snr: dict[str, list[dict[str, Any]]] = {}
    listener_meta: dict[str, Any] = {}
    for snr, info in sorted(summary["listeners"].items()):
        role = info.get("first_lstat", {}).get("role")
        kinds = info.get("kinds", {})
        usable = role == "OBSERVER" and kinds.get("LPD", 0) and kinds.get("LBD", 0)
        listener_meta[snr] = {
            "listener_key": info["listener_key"], "role": role, "usable": bool(usable),
        }
        if not usable:
            continue
        path = listener_dir / "listeners" / f"{snr}.jsonl"
        raw_rows = _read_bounded_timed_lines(
            path, read_start_ns, read_stop_ns, _jsonl_time_ns, trace,
            search_ceiling_fraction=search_ceiling_fraction,
            safe_ceiling_seed_offset=(safe_ceiling_seed_offsets or {}).get(
                str(path.resolve())
            ),
            forbidden_time_intervals_ns=forbidden_time_intervals_ns,
        )
        rows_by_snr[snr] = [json.loads(line) for line in raw_rows]
    return rows_by_snr, {
        "summary_path": str(summary_path.resolve()),
        "summary_sha256": _sha256_small_metadata(summary_path),
        "capture_summary_pass": summary.get("pass"),
        "listeners": listener_meta,
        "requested_time_window_ns": [read_start_ns, read_stop_ns],
        "bracket_superframes_each_side": 2,
    }


def _resolve_observed_sources(
    rows_by_snr: Mapping[str, Iterable[Mapping[str, Any]]],
    identity: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, int], dict[str, Any]]:
    """Bind tag identities to actual Listener Poll sources in this capture."""
    node_by_tag = {int(row["logical_tag_id"]): node for node, row in identity.items()}
    observed: dict[int, Counter[int]] = defaultdict(Counter)
    response_destinations: Counter[int] = Counter()
    for rows in rows_by_snr.values():
        for row in rows:
            fields = row.get("fields", {})
            if row.get("kind") == "LPD":
                tag = int(fields.get("tag_id", -1)); source = int(fields.get("src", -1))
                if tag in node_by_tag and source >= 0:
                    observed[tag][source] += 1
            elif row.get("kind") == "LRD":
                destination = int(fields.get("dst", -1))
                if destination >= 0:
                    response_destinations[destination] += 1
    source_by_node: dict[str, int] = {}
    evidence = {}
    unresolved_tags = []
    for tag, node in sorted(node_by_tag.items()):
        counts = observed.get(tag, Counter())
        if not counts:
            unresolved_tags.append(tag)
            continue
        if len(counts) != 1:
            raise ValueError(f"ambiguous capture-bound Listener source for tag {tag}/{node}: {counts}")
        source, count = counts.most_common(1)[0]
        source_by_node[node] = int(source)
        evidence[node] = {
            **dict(identity[node]),
            "listener_on_air_source": f"0x{source:04X}",
            "listener_poll_observations": int(count),
            "source_binding_method": "DIRECT_LPD_TAG_ID_TO_SOURCE",
        }
    unresolved_sources = sorted(set(response_destinations) - set(source_by_node.values()))
    if len(unresolved_tags) == len(unresolved_sources) == 1:
        tag = unresolved_tags[0]; source = unresolved_sources[0]; node = node_by_tag[tag]
        source_by_node[node] = int(source)
        evidence[node] = {
            **dict(identity[node]),
            "listener_on_air_source": f"0x{source:04X}",
            "listener_poll_observations": 0,
            "listener_response_destination_observations": int(response_destinations[source]),
            "source_binding_method": "EXACTLY_ONE_BIJECTIVE_LRD_DESTINATION_ELIMINATION",
        }
        unresolved_tags = []; unresolved_sources = []
    if unresolved_tags or unresolved_sources:
        raise ValueError(
            "ambiguous capture-bound Listener source coverage "
            f"unresolved_tags={unresolved_tags} unresolved_response_destinations="
            f"{[f'0x{x:04X}' for x in unresolved_sources]}"
        )
    if len(set(source_by_node.values())) != len(source_by_node):
        raise ValueError("duplicate capture-bound Listener Poll source")
    return source_by_node, {
        "schema": "biospur-capture-bound-listener-source-binding-v1",
        "source_kind": "LPD_BROADCAST_POLL",
        "derivation": "AUTHORITATIVE_TAG_IDENTITY_JOINED_TO_ACTION_BOUNDED_LISTENER_LPD",
        "mapping": evidence,
        "coverage_exactly_once": True,
        "universal_address_prefix_assumed": False,
    }


def _listener_polls_from_bounded_rows(
    rows_by_snr: Mapping[str, Iterable[Mapping[str, Any]]], listener_meta: Mapping[str, Any],
    src_slots: Mapping[int, int],
) -> tuple[list[ListenerPoll], dict[str, Any]]:
    polls: list[ListenerPoll] = []; audit: dict[str, Any] = {}
    for snr, rows_iter in sorted(rows_by_snr.items()):
        rows = list(rows_iter); meta = listener_meta["listeners"][snr]
        beacons: list[tuple[int, int]] = []; raw_polls: list[Mapping[str, Any]] = []
        raw_responses: list[Mapping[str, Any]] = []
        for row in rows:
            fields = row.get("fields", {})
            if row.get("kind") == "LBD" and row.get("rx_unwrapped_ticks") is not None:
                beacons.append((int(fields["superframe_counter"]), int(row["rx_unwrapped_ticks"])))
            elif row.get("kind") == "LPD" and int(fields.get("src", -1)) in src_slots:
                raw_polls.append(row)
            elif row.get("kind") == "LRD" and int(fields.get("dst", -1)) in src_slots:
                raw_responses.append(row)
        intercept, ticks_per_epoch = _listener_beacon_fit(beacons)
        phase_errors = []
        for row in raw_polls:
            fields = row["fields"]; src = int(fields["src"]); slot = int(src_slots[src])
            fractional = (int(row["rx_unwrapped_ticks"]) - intercept) / ticks_per_epoch
            expected_phase = slot * SLOT_US + 3900.0
            epoch = int(round(fractional - expected_phase / SUPERFRAME_US))
            phase = (fractional - epoch) * SUPERFRAME_US
            phase_errors.append(phase - expected_phase)
            polls.append(ListenerPoll(
                meta["listener_key"], src, int(fields["poll_seq"]), epoch,
                float(phase), int(row["arrival_monotonic_ns"]) / 1e9,
            ))
        # If an observer missed every broadcast Poll for one tag, recover the
        # Poll receive epoch from response timing.  Per-listener/per-anchor
        # response offsets are learned from other tags that have both LPD and
        # LRD records in this same bounded action interval. No range value or
        # nominal range model is decoded or consumed.
        poll_ticks_by_source: dict[int, list[int]] = defaultdict(list)
        for row in raw_polls:
            poll_ticks_by_source[int(row["fields"]["src"])].append(int(row["rx_unwrapped_ticks"]))
        for values in poll_ticks_by_source.values():
            values.sort()
        offset_ticks_by_anchor: dict[int, list[int]] = defaultdict(list)
        for row in raw_responses:
            fields = row["fields"]; destination = int(fields["dst"])
            ticks = int(row["rx_unwrapped_ticks"]); candidates = poll_ticks_by_source.get(destination, [])
            position = bisect.bisect_right(candidates, ticks) - 1
            if position < 0:
                continue
            delta = ticks - candidates[position]
            delta_us = delta / DW_TICKS_PER_US
            if 100.0 <= delta_us <= 20_000.0:
                offset_ticks_by_anchor[int(fields["anchor_id"])].append(delta)
        learned_offsets = {
            anchor: float(np.median(values))
            for anchor, values in offset_ticks_by_anchor.items() if len(values) >= 3
        }
        directly_observed_sources = set(poll_ticks_by_source)
        response_recovered = 0; missing_offset_rows = 0
        for row in raw_responses:
            fields = row["fields"]; destination = int(fields["dst"])
            if destination in directly_observed_sources:
                continue
            offset = learned_offsets.get(int(fields["anchor_id"]))
            if offset is None:
                missing_offset_rows += 1
                continue
            inferred_poll_ticks = float(row["rx_unwrapped_ticks"]) - offset
            fractional = (inferred_poll_ticks - intercept) / ticks_per_epoch
            expected_phase = int(src_slots[destination]) * SLOT_US + 3900.0
            epoch = int(round(fractional - expected_phase / SUPERFRAME_US))
            phase = (fractional - epoch) * SUPERFRAME_US
            phase_errors.append(phase - expected_phase)
            polls.append(ListenerPoll(
                meta["listener_key"], destination, int(fields["resp_seq"]), epoch,
                float(phase), int(row["arrival_monotonic_ns"]) / 1e9,
            ))
            response_recovered += 1
        audit[snr] = {
            **dict(meta), "beacons_used": len(beacons), "polls_used": len(raw_polls),
            "response_records_considered": len(raw_responses),
            "response_derived_poll_records": response_recovered,
            "response_rows_missing_learned_anchor_offset": missing_offset_rows,
            "learned_response_offsets_by_anchor_us": {
                str(anchor): value / DW_TICKS_PER_US for anchor, value in sorted(learned_offsets.items())
            },
            "response_timing_spatial_fields_consumed": [],
            "beacon_period_us": ticks_per_epoch / DW_TICKS_PER_US,
            "phase_choice_max_error_us": max(map(abs, phase_errors), default=None),
        }
    return polls, {
        "capture_summary_pass": listener_meta["capture_summary_pass"], "listeners": audit,
    }


def _match_node(anchors: list[UwbClockAnchor], epochs: np.ndarray, polls: list[ListenerPoll],
                src: int) -> tuple[int, int, list[tuple[int, float]], dict]:
    available = sorted((p for p in polls if p.src == src), key=lambda row: row.host_monotonic_s)
    anchor_times = [row.host_monotonic_s for row in anchors]
    seeds: list[int] = []
    association_pairs: list[tuple[int, ListenerPoll]] = []
    # Public sweep and on-air Poll sequence are independent counters.  Host
    # monotonic time is used only to choose the discrete Listener epoch; the
    # measurement timestamp itself remains TIMER2 mapped by UWB Beacon time.
    for poll in available:
        pos = bisect.bisect_left(anchor_times, poll.host_monotonic_s)
        choices = [j for j in (pos - 1, pos) if 0 <= j < len(anchors)]
        if not choices:
            continue
        chosen = min(
            choices,
            key=lambda j: abs(anchor_times[j] - poll.host_monotonic_s),
        )
        if abs(poll.host_monotonic_s - anchor_times[chosen]) <= 0.5:
            seeds.append(poll.absolute_epoch - int(epochs[chosen]))
            association_pairs.append((chosen, poll))
    if not seeds:
        raise ValueError("no capture-identity-backed Listener epoch matches")
    seed_counts = Counter(seeds)
    selected, selected_count = seed_counts.most_common(1)[0]

    mod16_offsets = Counter()
    for anchor, epoch in zip(anchors, epochs):
        if anchor.sf_valid and anchor.sf_mod16 is not None:
            mod16_offsets[(anchor.sf_mod16 - int(epoch)) & 0x0F] += 1
    required_mod16, mod_count = mod16_offsets.most_common(1)[0]
    # The carried label is allowed a segment-constant acquisition offset (see
    # epoch_transition_segmentation.md). Listener chooses the absolute integer;
    # mod16 proves that the offset is constant and has no unexplained transition.
    next_count = seed_counts.most_common(2)[1][1] if len(seed_counts) > 1 else 0
    if selected_count <= next_count:
        raise ValueError(f"unresolved Listener integer epoch tie: {seed_counts}")

    by_epoch: dict[int, list[ListenerPoll]] = defaultdict(list)
    for poll in available:
        by_epoch[poll.absolute_epoch].append(poll)
    pairs: list[tuple[int, float]] = []
    sequence_offsets = Counter()
    for index, epoch in enumerate(epochs):
        seen = by_epoch.get(int(epoch) + selected, [])
        if not seen:
            continue
        phases = [p.phase_us for p in seen]
        for poll in seen:
            sequence_offsets[((anchors[index].sweep & 0xFF) - poll.sequence) & 0xFF] += 1
        target_us = (int(epoch) + selected) * SUPERFRAME_US + float(np.median(phases))
        pairs.append((index, target_us))
    modal_seq, modal_seq_count = sequence_offsets.most_common(1)[0]
    host_seed_residual_s = [
        abs(anchors[index].host_monotonic_s - poll.host_monotonic_s)
        for index, poll in association_pairs
        if poll.absolute_epoch - int(epochs[index]) == selected
    ]
    details = {
        "integer_seed_counts": {str(k): v for k, v in sorted(seed_counts.items())},
        "selected_integer_epoch": selected,
        "selected_seed_fraction": selected_count / len(seeds),
        "integer_choice_margin_seed_votes": int(selected_count - next_count),
        "unique_modal_seed_winner": bool(selected_count > next_count),
        "mod16_required_offset": required_mod16,
        "mod16_agreement_fraction": mod_count / sum(mod16_offsets.values()),
        "listener_minus_carried_mod16_offset": (selected - required_mod16) & 0x0F,
        "sequence_offset_sweep_minus_poll": modal_seq,
        "sequence_offset_modal_fraction": modal_seq_count / sum(sequence_offsets.values()),
        "unique_fusion_listener_pairs": len(pairs),
        "discrete_epoch_association": "NEAREST_HOST_MONOTONIC_THEN_INTEGER_UWB_EPOCH_MODE",
        "host_monotonic_used_as_measurement_time": False,
        "public_sweep_equals_poll_sequence_assumed": False,
        "host_seed_pair_count": len(host_seed_residual_s),
        "host_seed_residual_p95_ms": (
            float(np.percentile(host_seed_residual_s, 95) * 1000.0)
            if host_seed_residual_s else None
        ),
    }
    return selected, selected_count - next_count, pairs, details


def _align_observations(
    anchors: Mapping[str, list[UwbClockAnchor]], polls: list[ListenerPoll],
    listener_audit: Mapping[str, Any], slots: Mapping[str, int], src: Mapping[str, int],
    *, identity_audit: Mapping[str, Any] | None = None,
    timing_access_audit: Mapping[str, Any] | None = None,
) -> tuple[dict[str, ClockModel], list[dict], dict]:
    expected = set(slots)
    if set(anchors) != expected:
        raise ValueError(f"clock fleet mismatch missing={sorted(expected-set(anchors))} unexpected={sorted(set(anchors)-expected)}")
    if set(src) != expected or len(set(src.values())) != len(src):
        raise ValueError("capture Listener source mapping is incomplete or duplicated")
    models: dict[str, ClockModel] = {}; residual_rows: list[dict] = []; joins = {}
    for node in sorted(slots, key=slots.get):
        node_anchors = anchors[node]
        epochs, local_period = reconstruct_local_epochs(a.strobe_us for a in node_anchors)
        try:
            integer, margin, pairs, join = _match_node(node_anchors, epochs, polls, src[node])
        except ValueError as exc:
            raise ValueError(f"{node}: {exc}") from exc
        indices = np.asarray([x[0] for x in pairs], int)
        target = np.asarray([x[1] for x in pairs], float)
        timer = np.asarray([node_anchors[i].strobe_us for i in indices], float)
        slope, intercept, clean = _robust_line(timer, target)
        residual = target - (slope * timer + intercept)
        centre = float(np.median(residual[clean])); centred = residual - centre
        raw_values = np.abs(centred)
        clean_values = raw_values[clean]
        paired_timer_span = float(timer[-1] - timer[0]) if len(timer) > 1 else 0.0
        full_timer_span = float(node_anchors[-1].strobe_us - node_anchors[0].strobe_us)
        coverage = paired_timer_span / full_timer_span if full_timer_span > 0 else 0.0
        clean_timer = timer[clean]
        max_clean_gap_s = (float(np.max(np.diff(clean_timer))) / 1e6
                           if len(clean_timer) > 1 else float("inf"))
        rejection_fraction = float((~clean).sum() / len(clean))
        mapped_all = slope * np.asarray([a.strobe_us for a in node_anchors]) + intercept
        reversals = int(np.sum(np.diff(mapped_all) <= 0))
        clean_std_us = float(np.std(centred[clean], ddof=1)) if int(clean.sum()) > 1 else 0.0
        clean_mean_timer = float(np.mean(clean_timer))
        clean_timer_ss = float(np.sum((clean_timer - clean_mean_timer) ** 2))
        endpoint_prediction_factor = 1.0
        if clean_timer_ss > 0.0:
            endpoint_prediction_factor = max(
                1.0 + 1.0 / len(clean_timer)
                + (float(endpoint) - clean_mean_timer) ** 2 / clean_timer_ss
                for endpoint in (node_anchors[0].strobe_us, node_anchors[-1].strobe_us)
            )
        prediction_three_sigma_us = 3.0 * clean_std_us * math.sqrt(endpoint_prediction_factor)
        sigma_us = max(
            1.0,
            1.4826 * float(np.median(clean_values)),
            float(np.percentile(clean_values, 95)),
            prediction_three_sigma_us,
        )
        model = ClockModel(
            node, 0, slope * 1000.0, intercept * 1000.0, sigma_us * 1000.0,
            node_anchors[0].strobe_us, node_anchors[-1].strobe_us, integer, margin,
            len(pairs), int(clean.sum()), int((~clean).sum()),
            float(np.percentile(clean_values, 95)), float(np.max(clean_values)),
            float(np.percentile(raw_values, 95)), float(np.percentile(raw_values, 99)),
            float(np.max(raw_values)), coverage, max_clean_gap_s, rejection_fraction,
            (local_period / SUPERFRAME_US - 1.0) * 1e6,
            float(join["mod16_agreement_fraction"]), reversals,
        )
        models[node] = model; joins[node] = join
        for pair_index, (event_index, target_us) in enumerate(pairs):
            residual_rows.append({
                "node_id": node, "boot_epoch": 0, "fusion_event_index": event_index,
                "strobe_us": node_anchors[event_index].strobe_us,
                "listener_global_us": f"{target_us:.6f}",
                "residual_us": f"{centred[pair_index]:.6f}",
                "classification": "accepted-clean" if clean[pair_index] else "rejected-timing-outlier",
                "sweep": node_anchors[event_index].sweep,
                "sf_mod16": node_anchors[event_index].sf_mod16,
            })
    # This bridge exists only because operator action tokens are timestamped in
    # host-monotonic time. It maps those annotations onto Listener global time;
    # it is never used as a measurement clock or as a per-record correction.
    bridge_host = []; bridge_global = []
    for node, model in models.items():
        for anchor in anchors[node][::20]:
            bridge_host.append(anchor.host_monotonic_s)
            bridge_global.append(model.a_ns_per_us * anchor.strobe_us / 1000.0 + model.b_ns / 1000.0)
    bridge_slope, bridge_intercept, bridge_clean = _robust_line(
        np.asarray(bridge_host), np.asarray(bridge_global),
        hard_limit=50_000.0, floor=5_000.0,
    )
    bridge_residual = np.asarray(bridge_global) - (
        bridge_slope * np.asarray(bridge_host) + bridge_intercept)
    action_bridge = {
        "listener_global_us_per_host_s": bridge_slope,
        "listener_global_us_intercept": bridge_intercept,
        "pairs": len(bridge_host),
        "clean_pairs": int(bridge_clean.sum()),
        "clean_residual_p95_us": float(np.percentile(np.abs(bridge_residual[bridge_clean]), 95)),
        "semantics": "annotation bridge only; never a measurement-time source",
    }
    capture_duration_s = max(
        rows[-1].host_monotonic_s - rows[0].host_monotonic_s
        for rows in anchors.values()
    )
    required_clean_pairs = _required_clean_listener_pairs(capture_duration_s)
    gate = {
        "superframe_us": SUPERFRAME_US,
        "capture_duration_s": capture_duration_s,
        "nodes": len(models),
        "listener_poll_records": len(polls),
        "no_unresolved_integer_ambiguity": all(_integer_join_resolved(j) for j in joins.values()),
        "minimum_clean_listener_pairs": all(m.clean_pairs >= required_clean_pairs for m in models.values()),
        "minimum_capture_span_coverage": all(m.capture_span_coverage >= MIN_CAPTURE_SPAN_COVERAGE for m in models.values()),
        "maximum_clean_anchor_gap": all(m.max_clean_anchor_gap_s <= MAX_CLEAN_ANCHOR_GAP_S for m in models.values()),
        "maximum_rejection_fraction": all(m.rejection_fraction <= MAX_REJECTION_FRACTION for m in models.values()),
        "raw_residual_p99_bounded": all(m.raw_residual_p99_us < RAW_RESIDUAL_P99_GATE_US for m in models.values()),
        "raw_residual_max_bounded": all(m.raw_residual_max_us < RAW_RESIDUAL_MAX_GATE_US for m in models.values()),
        "clean_residual_p95_lt_0_5_ms": all(m.clean_residual_p95_us < CLEAN_RESIDUAL_P95_GATE_US for m in models.values()),
        "clean_residual_max_lt_1_ms": all(m.clean_residual_max_us < CLEAN_RESIDUAL_MAX_GATE_US for m in models.values()),
        "classifier_hard_limit_not_tautological": CLOCK_OUTLIER_HARD_LIMIT_US > CLEAN_RESIDUAL_MAX_GATE_US,
        "all_boot_segments_explicit": all(m.boot_epoch == 0 for m in models.values()),
        "no_timestamp_reversal": all(m.timestamp_reversals == 0 for m in models.values()),
        "listener_audit": listener_audit,
        "integer_join": joins,
        "action_annotation_bridge": action_bridge,
        "capture_identity": dict(identity_audit or {}),
        "timing_access": dict(timing_access_audit or {}),
    }
    gate["thresholds"] = {
        "minimum_clean_listener_pairs": required_clean_pairs,
        "minimum_clean_listener_pairs_policy": (
            "MIN(50,MAX(10,FLOOR(COMPLETE_ACTION_DURATION_S/3)))_PLUS_INDEPENDENT_RESIDUAL_COVERAGE_GATES"
        ),
        "minimum_capture_span_coverage": MIN_CAPTURE_SPAN_COVERAGE,
        "maximum_clean_anchor_gap_s": MAX_CLEAN_ANCHOR_GAP_S,
        "maximum_rejection_fraction": MAX_REJECTION_FRACTION,
        "raw_residual_p99_gate_us": RAW_RESIDUAL_P99_GATE_US,
        "raw_residual_max_gate_us": RAW_RESIDUAL_MAX_GATE_US,
        "clean_residual_p95_gate_us": CLEAN_RESIDUAL_P95_GATE_US,
        "clean_residual_max_gate_us": CLEAN_RESIDUAL_MAX_GATE_US,
        "outlier_classifier_hard_limit_us": CLOCK_OUTLIER_HARD_LIMIT_US,
    }
    gate["per_boot_segment_coverage"] = {
        f"{node}:{model.boot_epoch}": {
            "clean_pairs": model.clean_pairs,
            "capture_span_coverage": model.capture_span_coverage,
            "max_clean_anchor_gap_s": model.max_clean_anchor_gap_s,
            "rejection_fraction": model.rejection_fraction,
        }
        for node, model in sorted(models.items())
    }
    strict_gate_names = (
        "no_unresolved_integer_ambiguity", "minimum_clean_listener_pairs",
        "minimum_capture_span_coverage", "maximum_clean_anchor_gap",
        "maximum_rejection_fraction", "raw_residual_p99_bounded",
        "raw_residual_max_bounded", "clean_residual_p95_lt_0_5_ms",
        "clean_residual_max_lt_1_ms", "classifier_hard_limit_not_tautological",
        "all_boot_segments_explicit", "no_timestamp_reversal")
    reconstruction_gate_names = (
        "no_unresolved_integer_ambiguity", "minimum_clean_listener_pairs",
        "maximum_clean_anchor_gap", "maximum_rejection_fraction",
        "raw_residual_p99_bounded", "raw_residual_max_bounded",
        "classifier_hard_limit_not_tautological", "all_boot_segments_explicit",
        "no_timestamp_reversal")
    gate["pass"] = all(gate[k] for k in strict_gate_names)
    gate["strict_validation_pass"] = gate["pass"]
    gate["reconstruction_safe"] = all(gate[k] for k in reconstruction_gate_names)
    gate["quality_degraded"] = bool(gate["reconstruction_safe"] and not gate["pass"])
    gate["strict_validation_failures"] = [name for name in strict_gate_names if not gate[name]]
    gate["reconstruction_safety_failures"] = [
        name for name in reconstruction_gate_names if not gate[name]
    ]
    gate["degraded_reconstruction_contract"] = (
        "STRICT_QUALITY_FAILURE_IS_PRESERVED;_RECONSTRUCTION_ALLOWED_ONLY_WHEN_"
        "INTEGER_PAIR_GAP_REJECTION_RAW_RESIDUAL_BOOT_AND_MONOTONIC_SAFETY_GATES_PASS"
    )
    return models, residual_rows, gate


def align_capture(log_path: Path, listener_dir: Path, start_s: float, end_s: float,
                  slots: Mapping[str, int]) -> tuple[dict[str, ClockModel], list[dict], dict]:
    """Legacy full-scan entry point retained for historical callers.

    New action validation must use :func:`align_capture_bounded`; this function
    intentionally records that it assumes the legacy address convention.
    """
    anchors = parse_fusion_anchors(log_path, start_s, end_s)
    src = {node: 0xB100 + slot for node, slot in slots.items()}
    polls, listener_audit = load_listener_polls(
        listener_dir, start_s, end_s, {src[node]: slots[node] for node in slots},
    )
    return _align_observations(
        anchors, polls, listener_audit, slots, src,
        identity_audit={
            "legacy_full_scan": True,
            "universal_address_prefix_assumed": True,
            "eligible_for_action_isolated_validation": False,
        },
        timing_access_audit={"scope": "LEGACY_UNBOUNDED_NOT_ACTION_VALIDATION"},
    )


def align_capture_bounded(
    log_path: Path, listener_dir: Path, readiness_path: Path,
    start_s: float, end_s: float, expected_nodes: Iterable[str],
    *, expected_readiness_sha256: str | None = None,
    search_ceiling_fraction: float | None = None,
    safe_ceiling_seed_offsets: Mapping[str, int] | None = None,
    forbidden_time_intervals_ns: Iterable[tuple[int, int]] = (),
) -> tuple[dict[str, ClockModel], list[dict], dict]:
    """Capture-generic, action-bounded TIMER2-to-Listener alignment."""
    nodes = tuple(expected_nodes); trace: list[dict[str, Any]] = []
    identity, identity_authority = _load_capture_identity_authority(
        readiness_path, nodes, expected_readiness_sha256,
    )
    slots = {node: int(identity[node]["logical_slot"]) for node in nodes}
    rows_by_snr, bounded_listener_meta = _bounded_listener_rows(
        listener_dir, start_s, end_s, trace,
        search_ceiling_fraction=search_ceiling_fraction,
        safe_ceiling_seed_offsets=safe_ceiling_seed_offsets,
        forbidden_time_intervals_ns=forbidden_time_intervals_ns,
    )
    source_by_node, source_binding = _resolve_observed_sources(rows_by_snr, identity)
    polls, listener_audit = _listener_polls_from_bounded_rows(
        rows_by_snr, bounded_listener_meta,
        {source_by_node[node]: slots[node] for node in nodes},
    )
    anchors = parse_fusion_anchors_bounded(
        log_path, start_s, end_s, trace,
        search_ceiling_fraction=search_ceiling_fraction,
        safe_ceiling_seed_offsets=safe_ceiling_seed_offsets,
        forbidden_time_intervals_ns=forbidden_time_intervals_ns,
    )
    coalesced = _coalesce_trace(trace)
    file_sizes = {str(Path(row["path"]).resolve()): Path(row["path"]).stat().st_size for row in coalesced}
    binary_probes = _binary_probe_audit(trace)
    forbidden = tuple((int(left), int(right)) for left, right in forbidden_time_intervals_ns)
    sequential_contracts = [
        dict(row) for row in trace if row.get("call_type") == "sequential_window_contract"
    ]
    ceiling_contracts = {
        str(row["path"]): dict(row) for row in trace
        if row.get("call_type") == "search_ceiling_contract"
    }
    actual_reads = [dict(row) for row in trace if row.get("call_type") == "os_read"]
    actual_seeks = [dict(row) for row in trace if row.get("call_type") == "os_lseek"]
    parsed_rows = [dict(row) for row in trace if row.get("call_type") == "parsed_timing_row"]
    parsed_forbidden_hits = [
        row for row in parsed_rows
        if any(left <= int(row["timestamp_ns"]) < right for left, right in forbidden)
    ]
    ceiling_proof = bool(forbidden) and set(ceiling_contracts) == set(file_sizes)
    if ceiling_proof:
        for path, contract in ceiling_contracts.items():
            safe_stop = int(contract["safe_search_stop_byte_exclusive"])
            probe_stop = int(contract["probe_row_stop_byte_exclusive"])
            for row in actual_reads:
                if row["path"] != path:
                    continue
                allowed_stop = (
                    probe_stop if row["purpose"].startswith("search_ceiling_") else safe_stop
                )
                if int(row["stop_byte_exclusive"]) > allowed_stop:
                    ceiling_proof = False
                    break
    timing_access = {
        "schema": "biospur-action-bounded-timing-access-v3",
        "selection_method": "AUDITED_OS_READ_LSEEK_HASH_BOUND_SEED_OR_SAFE_CEILING_BINARY_BOUNDS_EXACT_SEQUENTIAL_INTERVAL",
        "requested_action_host_monotonic_ns": [
            int(round(start_s * 1e9)), int(round(end_s * 1e9)),
        ],
        "bracket_superframes_each_side": 2,
        "read_intervals": coalesced,
        "raw_io_call_accounting": {
            "instrumentation_layer": "os.read/os.lseek on O_RDONLY descriptors; no buffered reader",
            "actual_os_read_calls": int(sum(row.get("actual_read_calls", 0) for row in actual_reads)),
            "actual_os_seek_calls": int(sum(row.get("actual_seek_calls", 0) for row in actual_seeks)),
            "aggregated_read_operations": actual_reads,
            "seek_operations": actual_seeks,
        },
        "binary_search_probes": binary_probes,
        "binary_search_probe_count": len(binary_probes),
        "every_binary_search_probe_separately_accounted": bool(binary_probes),
        "sequential_windows": sequential_contracts,
        "all_sequential_timing_rows_within_action_plus_two_superframes": bool(
            sequential_contracts
            and all(row["all_timed_rows_within_requested_window"] for row in sequential_contracts)
        ),
        "forbidden_golf_boxing_timing_intervals_ns": [list(row) for row in forbidden],
        "search_ceiling_contracts": list(ceiling_contracts.values()),
        "golf_boxing_timing_interval_parsed_row_hits": parsed_forbidden_hits,
        "golf_boxing_timing_interval_bytes_touched": not (
            ceiling_proof and not parsed_forbidden_hits
        ) if forbidden else None,
        "golf_boxing_timing_exclusion_proof": (
            "Every actual read is at/before a per-file monotonic safe ceiling whose exact "
            "unbuffered probe row precedes the first forbidden interval."
        ) if forbidden else "NOT_REQUESTED",
        "files": [
            {
                "path": path,
                "file_size": size,
                "bytes_read_with_repeats": int(sum(
                    row["bytes"] for row in actual_reads if row["path"] == path
                )),
                "unique_bytes_read": int(_union_interval_bytes(
                    row for row in actual_reads if row["path"] == path
                )),
                "full_traversal_proven_absent": int(_union_interval_bytes(
                    row for row in actual_reads if row["path"] == path
                )) < size,
            }
            for path, size in sorted(file_sizes.items())
        ],
        "complete_file_scan_attempted": False,
        "no_full_file_traversal_proven_by_actual_read_union": all(
            _union_interval_bytes(row for row in actual_reads if row["path"] == path) < size
            for path, size in file_sizes.items()
        ),
        "spatial_payload_opened": False,
    }
    identity_audit = {
        "authority": identity_authority,
        "listener_source_binding": source_binding,
    }
    return _align_observations(
        anchors, polls, listener_audit, slots, source_by_node,
        identity_audit=identity_audit, timing_access_audit=timing_access,
    )


def models_as_json(models: Mapping[str, ClockModel]) -> dict:
    return {node: asdict(model) for node, model in sorted(models.items())}
