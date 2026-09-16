#!/usr/bin/env python3
"""Bounded reader-only prefix profiler for the authenticated Capture2 session."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import resource
import shlex
import time

import biospur_fusion.c2_coupled_progressive.continuous_full_session_reader as reader_module
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FULL_WINDOW_SHA256, RAW_RELATIVE, SOURCE_SHA256, SOURCE_STAT_IDENTITY,
    FullSessionContinuousReader, FullSessionEventRouter,
    _FullSessionRecordDeliveryOwner,
    _raw_key,
)
from biospur_fusion.c2_coupled_progressive.continuous_streaming_runner import (
    AuthorizedByteWindow, IncrementalV47WindowDecoder,
)
from biospur_fusion.c2_uwb_root_world.continuous_full_session import FULL_SESSION_BYTE_COUNT
if __package__:
    from tools.build_c2_full_session_ten_node_ab import _static_inputs
else:
    from build_c2_full_session_ten_node_ab import _static_inputs


ROOT = Path(__file__).resolve().parents[1]
CAP_BYTES = 64 << 20
READ_CHUNK = 1 << 20
DEADLINE_S = 45.0
TARGETS = tuple(value << 20 for value in (2, 4, 6, 8, 16, 24, 32, 40, 48, 56, 64))
ANCHOR_START = 213_717_241
ANCHOR_END = 213_717_449
ANCHOR_INDEX = 1_169_570
ANCHOR_SHA256 = "77479052885ede6221416e1ff39168f2d340103fc74e5c1b4e6ce0d5ea2e0bb2"


def _write_new(path: Path, payload: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o644)
    try:
        if os.write(fd, payload) != len(payload):
            raise RuntimeError("short profile evidence write")
        os.fsync(fd)
    finally:
        os.close(fd)


def _json_new(path: Path, document: object) -> None:
    _write_new(path, (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode())


def _complete_records(block: bytes) -> int:
    if not block or block[-1] != 0:
        raise ValueError("profile block must end at a raw-record boundary")
    return sum(bool(part) for part in block.split(b"\0")[:-1])


def _nearest_boundary(buffer: bytearray, desired: int, limit: int) -> int:
    """Return a byte count ending at the nearest complete record within limit."""
    if not 0 < desired <= limit <= len(buffer):
        raise ValueError("invalid checkpoint boundary request")
    left = buffer.rfind(0, 0, desired)
    right = buffer.find(0, desired - 1, limit)
    candidates = tuple(value + 1 for value in (left, right) if value >= 0)
    if not candidates:
        raise RuntimeError("no complete record within bounded checkpoint buffer")
    return min(candidates, key=lambda value: (abs(value - desired), value))


def _shape(router: FullSessionEventRouter) -> dict[str, int]:
    return {
        "last_timer": len(router._last_timer),
        "last_sequence": len(router._last_sequence),
        "last_availability": len(router._last_availability),
        "last_ordering": len(router._last_ordering),
        "pending_events": len(router._pending_events),
        "pending_event_digests": len(router._pending_event_digests),
        "pending_sensor_digests": len(router._pending_sensor_digests),
        "region_counts": len(router._counts),
        "imu_region_maps": len(router._imu_counts),
    }


def _checkpoint(*, processed: int, source_read: int, records: int, events: int,
                started_wall: int, started_cpu: int, prefix, router, stage,
                source_start: int, accounting) -> dict[str, object]:
    return {
        "bytes": processed,
        "source_bytes_read": source_read,
        "source_end_offset": source_start + processed,
        "complete_raw_records": records,
        "events": events,
        "wall_s": (time.perf_counter_ns() - started_wall) * 1e-9,
        "cpu_s": (time.process_time_ns() - started_cpu) * 1e-9,
        "stage_cpu_s": {key: value * 1e-9 for key, value in stage.items()},
        "rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "fixed_container_sizes": _shape(router),
        "pending_ticket_high_water": router._pending_event_high_water,
        "batch_accounting": dict(accounting),
        "prefix_sha256": prefix.copy().hexdigest(),
    }


def _evaluate(checkpoints: list[dict[str, object]]) -> dict[str, object]:
    intervals = []
    for left, right in zip(checkpoints, checkpoints[1:]):
        delta_bytes = int(right["bytes"]) - int(left["bytes"])
        delta_wall = float(right["wall_s"]) - float(left["wall_s"])
        if delta_bytes > 0 and delta_wall > 0:
            intervals.append(delta_bytes / delta_wall)
    last = intervals[-3:]
    ratio = max(last) / min(last) if len(last) == 3 else None
    projection = FULL_SESSION_BYTE_COUNT / min(last) if len(last) == 3 else None
    fixed_keys = ("last_timer", "last_sequence", "last_availability", "last_ordering")
    shapes = [row["fixed_container_sizes"] for row in checkpoints]
    fixed = bool(shapes) and all(
        tuple(shape[key] for key in fixed_keys) == tuple(shapes[0][key] for key in fixed_keys)
        for shape in shapes
    )
    pending_zero = all(shape["pending_events"] == 0 for shape in shapes)
    rss_growth = int(checkpoints[-1]["rss_kib"]) - int(checkpoints[len(checkpoints) // 2]["rss_kib"])
    stage = checkpoints[-1]["stage_cpu_s"]
    cpu = float(checkpoints[-1]["cpu_s"])
    route_digest_delivery = (
        float(stage["route_inclusive"]) + float(stage["delivery_inclusive"])
    ) / cpu if cpu > 0 else 1.0
    gates = {
        "last_three_interval_throughput_ratio_le_1_25": ratio is not None and ratio <= 1.25,
        "fixed_identity_containers_constant": fixed,
        "pending_zero_at_checkpoints": pending_zero,
        "rss_second_half_growth_le_32_mib": rss_growth <= 32 << 10,
        "projected_full_source_le_360_s": projection is not None and projection <= 360.0,
    }
    dominant = max(
        ("digest", float(stage["digest_nested"])),
        ("region_lookup", float(stage["region_lookup_nested"])),
        ("route_other", max(0.0, float(stage["route_inclusive"])
                            - float(stage["digest_in_route"])
                            - float(stage["region_lookup_nested"]))),
        key=lambda row: row[1],
    )
    return {
        "interval_throughput_bytes_s": intervals,
        "last_three_throughput_ratio": ratio,
        "conservative_full_source_projection_s": projection,
        "rss_second_half_net_growth_kib": rss_growth,
        "route_digest_delivery_cpu_fraction": route_digest_delivery,
        "gates": gates,
        "all_gates_pass": all(gates.values()),
        "dominant_repeated_owner": {"name": dominant[0], "cpu_s": dominant[1]},
    }


def _self_and_ancestor_pids(proc_root: Path, current_pid: int) -> frozenset[int]:
    excluded = {current_pid}
    cursor = current_pid
    while cursor > 1:
        try:
            lines = (proc_root / str(cursor) / "status").read_text().splitlines()
            parent = int(next(line for line in lines if line.startswith("PPid:")).split()[1])
        except (OSError, StopIteration, ValueError):
            break
        if parent <= 1 or parent in excluded:
            break
        excluded.add(parent)
        cursor = parent
    return frozenset(excluded)


def _residual_profile_processes(
    proc_root: Path = Path("/proc"), *, current_pid: int | None = None,
) -> tuple[str, ...]:
    current = os.getpid() if current_pid is None else current_pid
    excluded = _self_and_ancestor_pids(proc_root, current)
    observed = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) in excluded:
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except (OSError, UnicodeDecodeError):
            continue
        is_profiler = "profile_c2_full_session_reader_prefix.py profile" in command
        is_focused_pytest = "pytest" in command and "test_c2_reader_prefix_profile.py" in command
        is_profile_timeout = "timeout" in command and "profile_c2_full_session_reader_prefix.py" in command
        if is_profiler or is_focused_pytest or is_profile_timeout:
            observed.append(f"{entry.name}:{command.strip()}")
    return tuple(sorted(observed))


def profile(output: Path) -> int:
    output = output.resolve()
    if not output.is_relative_to(ROOT / "logs") or not output.is_dir() or any(output.iterdir()):
        raise ValueError("profile output must be a new empty logs directory")
    clock_owner, *_ = _static_inputs()
    reader = FullSessionContinuousReader(root=ROOT, clock_owner=clock_owner)
    inventory = reader.inventory
    source_start = inventory.start_offset
    started_wall, started_cpu = time.perf_counter_ns(), time.process_time_ns()
    deadline = started_wall + int(DEADLINE_S * 1e9)
    stage = {key: 0 for key in (
        "decoder_feed", "route_inclusive", "delivery_inclusive", "digest_nested",
        "digest_in_route", "digest_in_delivery", "region_lookup_nested",
    )}
    active_stage = [""]
    original_event_digest = reader_module._event_digest
    original_sensor_digest = reader_module._sensor_identity_digest
    original_structural = reader_module._event_structural_identity
    original_record_ticket_init = reader_module.FullSessionRecordTicket.__init__
    original_event_ticket_init = reader_module.FullSessionEventTicket.__init__
    original_imu_ticket_init = reader_module.FullSessionImuEventTicket.__init__
    original_uwb_ticket_init = reader_module.FullSessionUwbEventTicket.__init__
    accounting = {key: 0 for key in (
        "batch_callbacks", "record_tickets", "event_tickets", "imu_tickets",
        "uwb_tickets", "event_digests", "sensor_digests", "structural_validations",
    )}

    def timed_digest(original):
        def wrapped(event):
            before = time.process_time_ns()
            try:
                return original(event)
            finally:
                elapsed = time.process_time_ns() - before
                stage["digest_nested"] += elapsed
                if active_stage[0] == "route":
                    stage["digest_in_route"] += elapsed
                elif active_stage[0] == "delivery":
                    stage["digest_in_delivery"] += elapsed
        return wrapped

    def counted(name, original):
        def wrapped(value):
            accounting[name] += 1
            return original(value)
        return wrapped

    def counted_init(name, original):
        def wrapped(instance, *args, **kwargs):
            accounting[name] += 1
            original(instance, *args, **kwargs)
        return wrapped

    reader_module._event_digest = counted("event_digests", timed_digest(original_event_digest))
    reader_module._sensor_identity_digest = counted("sensor_digests", timed_digest(original_sensor_digest))
    reader_module._event_structural_identity = counted("structural_validations", original_structural)
    reader_module.FullSessionRecordTicket.__init__ = counted_init("record_tickets", original_record_ticket_init)
    reader_module.FullSessionEventTicket.__init__ = counted_init("event_tickets", original_event_ticket_init)
    reader_module.FullSessionImuEventTicket.__init__ = counted_init("imu_tickets", original_imu_ticket_init)
    reader_module.FullSessionUwbEventTicket.__init__ = counted_init("uwb_tickets", original_uwb_ticket_init)
    fd = os.open(reader.raw_path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    checkpoints: list[dict[str, object]] = []
    source_read = processed = records = events = 0
    prefix = hashlib.sha256()
    try:
        before_stat = reader._stat(os.fstat(fd))
        if before_stat != SOURCE_STAT_IDENTITY:
            raise RuntimeError("profile source stat identity mismatch")
        os.lseek(fd, source_start, os.SEEK_SET)
        buffer = bytearray(os.read(fd, READ_CHUNK))
        source_read += len(buffer)
        anchor_a, anchor_b = ANCHOR_START - source_start, ANCHOR_END - source_start
        if len(buffer) < anchor_b or hashlib.sha256(buffer[anchor_a:anchor_b - 1]).hexdigest() != ANCHOR_SHA256:
            raise RuntimeError("profile ordinal anchor identity mismatch")
        first_record = ANCHOR_INDEX - _complete_records(bytes(buffer[:anchor_a])) - 1
        router = FullSessionEventRouter(inventory, clock_owner)
        delivery = _FullSessionRecordDeliveryOwner(router)
        original_partition = router._source_partition

        def timed_partition(row):
            partition_started = time.process_time_ns()
            try:
                return original_partition(row)
            finally:
                stage["region_lookup_nested"] += time.process_time_ns() - partition_started

        router._source_partition = timed_partition
        region_index = 0
        region_records = 0

        def new_decoder(index: int, ordinal: int):
            region = inventory.regions[index]
            window = AuthorizedByteWindow(
                str(RAW_RELATIVE), SOURCE_SHA256, region.start_offset, region.stop_offset,
                ordinal, reader._hashes[index], region.start_ns, region.stop_ns,
                {f"{binding.node_id}:{kind}": binding.boot_epoch
                 for binding in clock_owner.bindings for kind in (1, 3)}, 4096,
            )
            return IncrementalV47WindowDecoder(window, maximum_emitted_events=4_000_000)

        decoder = new_decoder(region_index, first_record)
        target_index = 0
        while processed < CAP_BYTES and time.perf_counter_ns() < deadline:
            if not buffer and source_read < CAP_BYTES:
                data = os.read(fd, min(READ_CHUNK, CAP_BYTES - source_read))
                if not data:
                    raise OSError("profile source ended early")
                buffer.extend(data); source_read += len(data)
            region = inventory.regions[region_index]
            region_remaining = region.stop_offset - (source_start + processed)
            limit = min(len(buffer), region_remaining, CAP_BYTES - processed)
            if limit <= 0:
                break
            checkpoint_now = False
            if target_index < len(TARGETS) and processed < TARGETS[target_index] <= processed + limit:
                desired = TARGETS[target_index] - processed
                cut = _nearest_boundary(buffer, desired, limit)
                checkpoint_now = True
            elif limit == region_remaining:
                cut = limit
            else:
                boundary = buffer.rfind(0, 0, limit)
                if boundary < 0:
                    if source_read >= CAP_BYTES:
                        break
                    data = os.read(fd, min(READ_CHUNK, CAP_BYTES - source_read))
                    buffer.extend(data); source_read += len(data)
                    continue
                cut = boundary + 1
            block = bytes(buffer[:cut]); del buffer[:cut]
            feed_started = time.process_time_ns()
            produced = decoder.feed(block, absolute_offset=source_start + processed)
            stage["decoder_feed"] += time.process_time_ns() - feed_started
            prefix.update(block)
            block_records = _complete_records(block)
            processed += cut; records += block_records; region_records += block_records
            for _identity, group in itertools.groupby(produced, key=_raw_key):
                active_stage[0] = "route"; route_started = time.process_time_ns()
                routed = router.route_original_record(tuple(group))
                stage["route_inclusive"] += time.process_time_ns() - route_started
                active_stage[0] = "delivery"; delivery_started = time.process_time_ns()
                delivery.issue().deliver(lambda batch: accounting.__setitem__(
                    "batch_callbacks", accounting["batch_callbacks"] + 1))
                delivery.require_consumed()
                stage["delivery_inclusive"] += time.process_time_ns() - delivery_started
                events += len(routed)
                active_stage[0] = ""
            if source_start + processed == region.stop_offset:
                decoder.finish()
                first_record += region_records; region_records = 0; region_index += 1
                if processed < CAP_BYTES:
                    decoder = new_decoder(region_index, first_record)
            if checkpoint_now:
                checkpoints.append(_checkpoint(
                    processed=processed, source_read=source_read, records=records,
                    events=events, started_wall=started_wall, started_cpu=started_cpu,
                    prefix=prefix, router=router, stage=stage, source_start=source_start,
                    accounting=accounting,
                ))
                target_index += 1
        if not checkpoints or int(checkpoints[-1]["bytes"]) != processed:
            checkpoints.append(_checkpoint(
                processed=processed, source_read=source_read, records=records,
                events=events, started_wall=started_wall, started_cpu=started_cpu,
                prefix=prefix, router=router, stage=stage, source_start=source_start,
                accounting=accounting,
            ))
        after_stat = reader._stat(os.fstat(fd))
        if after_stat != before_stat or source_read > CAP_BYTES:
            raise RuntimeError("profile source identity/read cap changed")
    finally:
        os.close(fd)
        reader_module._event_digest = original_event_digest
        reader_module._sensor_identity_digest = original_sensor_digest
        reader_module._event_structural_identity = original_structural
        reader_module.FullSessionRecordTicket.__init__ = original_record_ticket_init
        reader_module.FullSessionEventTicket.__init__ = original_event_ticket_init
        reader_module.FullSessionImuEventTicket.__init__ = original_imu_ticket_init
        reader_module.FullSessionUwbEventTicket.__init__ = original_uwb_ticket_init
    evaluation = _evaluate(checkpoints)
    result = {
        "schema": "biospur.c2.reader_prefix_profile.v1", "status": "COMPLETE",
        "reader_only": True, "fusion_run": False, "retry_count": 0,
        "limits": {"source_bytes": CAP_BYTES, "internal_deadline_s": DEADLINE_S,
                   "vm_kib": 1_048_576, "external_timeout_s": 60},
        "source": {"path": str(reader.raw_path.relative_to(ROOT)),
                   "sha256": SOURCE_SHA256, "stat_before": before_stat,
                   "stat_after": after_stat, "session_start_offset": source_start,
                   "profile_source_bytes_read": source_read,
                   "profile_processed_bytes": processed,
                   "full_window_sha256": FULL_WINDOW_SHA256},
        "checkpoints": checkpoints, "evaluation": evaluation,
        "batch_accounting": accounting,
        "batch_accounting_valid": (
            accounting["batch_callbacks"] == records
            and accounting["record_tickets"] == records
            and accounting["event_tickets"] == accounting["imu_tickets"]
                == accounting["uwb_tickets"] == 0
            and accounting["event_digests"] == accounting["sensor_digests"] == events
            and accounting["structural_validations"] == 2 * events
            and router._pending_event_high_water <= 16
        ),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    _json_new(output / "RESULT.json", result)
    return 0


def seal(output: Path, command: str) -> int:
    output = output.resolve()
    required = ("RESULT.json", "RUNTIME.txt")
    if not output.is_relative_to(ROOT / "logs") or any(not (output / name).is_file() for name in required):
        raise ValueError("profile evidence is incomplete")
    _write_new(output / "COMMAND.txt", (command + "\n").encode())
    others = _residual_profile_processes()
    _write_new(output / "PROCESS_FINAL.txt",
               (f"profile_process_count={len(others)}\n" + "".join(f"process={row}\n" for row in others)).encode())
    names = ("COMMAND.txt", "PROCESS_FINAL.txt", "RESULT.json", "RUNTIME.txt")
    _write_new(output / "SHA256SUMS", "".join(
        f"{hashlib.sha256((output / name).read_bytes()).hexdigest()}  {name}\n" for name in names
    ).encode())
    for name in (*names, "SHA256SUMS"):
        os.chmod(output / name, 0o444)
    os.chmod(output, 0o555)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    run = sub.add_parser("profile"); run.add_argument("--output", required=True, type=Path)
    done = sub.add_parser("seal"); done.add_argument("--output", required=True, type=Path)
    done.add_argument("--command", required=True)
    args = parser.parse_args()
    return profile(args.output) if args.mode == "profile" else seal(args.output, args.command)


if __name__ == "__main__":
    raise SystemExit(main())
