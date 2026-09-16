#!/usr/bin/env python3
"""Run one complete record-batch reader-only pass and preserve bounded evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import time
from typing import Callable

from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FORMAL_MANIFEST_SHA256, FULL_WINDOW_SHA256, HASH_EVIDENCE_SHA256,
    RAW_RELATIVE, SOURCE_SHA256, SOURCE_STAT_IDENTITY,
    FullSessionContinuousReader, FullSessionRecordTicket,
)

if __package__:
    from tools.build_c2_full_session_ten_node_ab import _static_inputs
else:
    from build_c2_full_session_ten_node_ab import _static_inputs


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_EVENTS = 250_000
MAX_CHECKPOINTS = 17
EVIDENCE_PATHS = (
    "src/biospur_fusion/c2_coupled_progressive/continuous_full_session_reader.py",
    "src/biospur_fusion/c2_coupled_progressive/continuous_stage2_adapter.py",
    "src/biospur_fusion/c2_coupled_progressive/continuous_frontend.py",
    "tools/run_c2_full_session_reader_watermark_audit.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new(path: Path, document: object) -> None:
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o444)
    try:
        if os.write(fd, payload) != len(payload):
            raise RuntimeError("short reader audit write")
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_text_new(path: Path, payload: str) -> None:
    encoded = payload.encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o444)
    try:
        if os.write(fd, encoded) != len(encoded):
            raise RuntimeError("short reader metadata write")
        os.fsync(fd)
    finally:
        os.close(fd)


def _other_reader_processes() -> tuple[str, ...]:
    excluded = {os.getpid()}
    ancestor = os.getppid()
    while ancestor > 1 and ancestor not in excluded:
        excluded.add(ancestor)
        try:
            fields = (Path("/proc") / str(ancestor) / "status").read_text().splitlines()
            ancestor = int(next(line for line in fields if line.startswith("PPid:")).split()[1])
        except (FileNotFoundError, PermissionError, ProcessLookupError, StopIteration, ValueError):
            break
    observed = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in excluded:
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except (FileNotFoundError, PermissionError, ProcessLookupError, UnicodeDecodeError):
            continue
        if "run_c2_full_session_reader_watermark_audit.py" in command:
            observed.append(f"{entry.name}:{command.strip()}")
    return tuple(sorted(observed))


class _BatchAuditAccumulator:
    """Retain only counters and an incremental identity digest."""

    def __init__(self, checkpoint: Callable[[dict[str, object]], None]) -> None:
        self.record_tickets = self.delivered_batches = 0
        self.delivered_events = self.callbacks = 0
        self.nodes: set[str] = set()
        self.kinds: set[str] = set()
        self.pending_high_water = 0
        self._identity = hashlib.sha256()
        self._next_checkpoint = CHECKPOINT_EVENTS
        self._checkpoint = checkpoint

    def consume_ticket(self, ticket: FullSessionRecordTicket) -> None:
        if type(ticket) is not FullSessionRecordTicket:
            raise TypeError("reader audit requires authoritative record tickets")
        self.record_tickets += 1
        ticket.deliver(lambda batch: self._accept_batch(ticket, batch))

    def _accept_batch(
        self, ticket: FullSessionRecordTicket, batch: tuple[object, ...],
    ) -> None:
        if type(batch) is not tuple or not 1 <= len(batch) <= 16:
            raise RuntimeError("reader audit batch size outside 1..16")
        first = batch[0]
        kind, node = first.kind, first.node_id
        raw = first.payload_owner.raw
        if raw is None:
            raise RuntimeError("reader audit batch lacks raw identity")
        raw_identity = (raw.record_index, raw.start_offset, raw.end_offset,
                        raw.encoded_sha256)
        for event in batch:
            current = event.payload_owner.raw
            if (event.kind != kind or event.node_id != node or current is None
                    or (current.record_index, current.start_offset, current.end_offset,
                        current.encoded_sha256) != raw_identity):
                raise RuntimeError("reader audit batch is not one homogeneous raw record")
        if (ticket.raw_identity != raw_identity
                or len(ticket.event_digests) != len(batch)
                or len(ticket.sensor_identity_digests) != len(batch)):
            raise RuntimeError("reader audit ticket/batch identity mismatch")
        if kind not in ("IMU", "UWB") or (kind == "UWB" and len(batch) != 1):
            raise RuntimeError("reader audit sensor batch cardinality mismatch")
        self._identity.update(json.dumps((
            ticket.record_ordinal, raw_identity, node, kind,
            tuple(event.event_id for event in batch), ticket.event_digests,
            ticket.sensor_identity_digests,
        ), separators=(",", ":"), allow_nan=False).encode())
        self.pending_high_water = max(self.pending_high_water, len(batch))
        self.delivered_batches += 1
        self.callbacks += 1
        self.delivered_events += len(batch)
        self.nodes.add(node)
        self.kinds.add(kind)
        while self.delivered_events >= self._next_checkpoint:
            self._checkpoint(self.snapshot(final=False))
            self._next_checkpoint += CHECKPOINT_EVENTS

    def snapshot(self, *, final: bool) -> dict[str, object]:
        return {
            "final": final, "record_tickets": self.record_tickets,
            "delivered_batches": self.delivered_batches,
            "delivered_events": self.delivered_events, "callbacks": self.callbacks,
            "nodes": sorted(self.nodes), "kinds": sorted(self.kinds),
            "pending_events": 0, "pending_high_water": self.pending_high_water,
            "batch_identity_sha256": self._identity.hexdigest(),
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "monotonic_s": time.monotonic(),
        }


def _route_document(route: object) -> dict[str, object]:
    return {
        "event_count": route.event_count,
        "events_by_region": dict(route.events_by_region),
        "imu_by_region_and_node": {
            region: dict(counts) for region, counts in route.imu_by_region_and_node.items()
        },
        "exact_5ms_imu_edges": route.exact_5ms_imu_edges,
        "imu_dropout_edges": route.imu_dropout_edges,
        "minimum_sensor_ready_lower_bound_ns": route.minimum_sensor_ready_lower_bound_ns,
        "maximum_sensor_ready_lower_bound_ns": route.maximum_sensor_ready_lower_bound_ns,
        "lifted_record_count": route.lifted_record_count,
        "maximum_availability_lift_ns": route.maximum_availability_lift_ns,
        "total_availability_lift_ns": route.total_availability_lift_ns,
        "maximum_owner_watermark_entries": route.maximum_owner_watermark_entries,
        "pending_event_high_water": route.pending_event_high_water,
        "identity_sha256": route.identity_sha256,
    }


def _run_reader(reader: object, checkpoint: Callable[[dict[str, object]], None]) -> dict[str, object]:
    accumulator = _BatchAuditAccumulator(checkpoint)
    audit = reader.consume_record_batches(accumulator.consume_ticket)
    access = dict(audit.access_audit)
    route = audit.route_audit
    container_records = int(access["nonempty_records"])
    skipped = container_records - accumulator.delivered_batches
    if skipped < 0:
        raise RuntimeError("reader audit routed more records than source container")
    if not (accumulator.delivered_events == route.event_count
            == int(access["decoded_events"]) == int(access["routed_events"])):
        raise RuntimeError("reader audit event conservation mismatch")
    if not (accumulator.record_tickets == accumulator.delivered_batches
            == accumulator.callbacks):
        raise RuntimeError("reader audit record delivery conservation mismatch")
    final = accumulator.snapshot(final=True)
    checkpoint(final)
    return {
        "container_records": container_records,
        "routed_sensor_records": accumulator.delivered_batches,
        "skipped_non_sensor_container_records": skipped,
        **final, "route_audit": _route_document(route), "access_audit": access,
    }


class _Terminated(RuntimeError):
    pass


def _seal_output(output: Path, command: str, exit_status: int) -> int:
    if not output.is_relative_to(ROOT / "logs") or not (output / "RESULT.json").is_file():
        raise ValueError("reader audit seal requires completed evidence under logs")
    _write_text_new(output / "COMMAND.txt", command.rstrip() + "\n")
    _write_text_new(output / "EXIT_STATUS.txt", f"exit_status={exit_status}\n")
    files = tuple(sorted(path for path in output.iterdir()
                         if path.is_file() and path.name != "SHA256SUMS"))
    total = sum(path.stat().st_size for path in files)
    if total >= 50 * 1024 * 1024:
        raise RuntimeError("reader audit evidence exceeds 50 MiB")
    _write_text_new(output / "SHA256SUMS", "".join(
        f"{_sha256(path)}  {path.name}\n" for path in files
    ))
    for path in (*files, output / "SHA256SUMS"):
        os.chmod(path, 0o444)
    os.chmod(output, 0o555)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seal-only", action="store_true")
    parser.add_argument("--command")
    parser.add_argument("--exit-status", type=int)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "logs"):
        raise ValueError("reader audit output must be under workspace logs")
    if args.seal_only:
        if args.command is None or args.exit_status is None:
            raise ValueError("reader audit seal requires command and exit status")
        return _seal_output(output, args.command, args.exit_status)
    if args.command is not None or args.exit_status is not None:
        raise ValueError("reader audit run does not accept seal metadata")
    output.mkdir(parents=True, exist_ok=True)
    unexpected = {path.name for path in output.iterdir()} - {
        "RUNTIME.txt", "STDOUT.txt", "STDERR.txt",
    }
    if unexpected:
        raise FileExistsError("reader audit output is not a fresh timed directory")
    progress_fd = os.open(output / "CHECKPOINTS.ndjson",
                          os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o444)
    checkpoint_count = 0
    started = time.monotonic()

    def checkpoint(document: dict[str, object]) -> None:
        nonlocal checkpoint_count
        if checkpoint_count >= MAX_CHECKPOINTS:
            raise RuntimeError("reader audit checkpoint bound exceeded")
        payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if os.write(progress_fd, payload) != len(payload):
            raise RuntimeError("short reader checkpoint write")
        os.fsync(progress_fd)
        checkpoint_count += 1

    def terminate(_signum: int, _frame: object) -> None:
        raise _Terminated("reader audit received external TERM")

    previous_handler = signal.signal(signal.SIGTERM, terminate)
    status, exit_status = "FAILED", 1
    result: dict[str, object] = {}
    error: dict[str, object] | None = None
    try:
        clock_owner, _clocks, _anchors, _delays, _tag_delay = _static_inputs()
        reader = FullSessionContinuousReader(root=ROOT, clock_owner=clock_owner)
        result = _run_reader(reader, checkpoint)
        status, exit_status = "COMPLETE", 0
    except BaseException as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        os.fsync(progress_fd)
        os.close(progress_fd)
        readers = _other_reader_processes()
        document = {
            "schema": "biospur.c2.full_session_reader_record_batch_audit.v2",
            "status": status, "reader_only": True,
            "sole_public_entry": "FullSessionContinuousReader.consume_record_batches",
            "consume_record_batches_calls": 1,
            "coordinator_fk_ik_uwb_solve_bootstrap_render_run": False,
            "checkpoint_count": checkpoint_count, "result": result, "error": error,
            "source_identity": {
                "relative_path": str(RAW_RELATIVE), "sha256": SOURCE_SHA256,
                "stat_identity": list(SOURCE_STAT_IDENTITY),
                "full_window_sha256": FULL_WINDOW_SHA256,
                "formal_manifest_sha256": FORMAL_MANIFEST_SHA256,
                "hash_evidence_sha256": HASH_EVIDENCE_SHA256,
            },
            "evidence_sha256": {relative: _sha256(ROOT / relative)
                                for relative in EVIDENCE_PATHS},
            "wall_s": time.monotonic() - started,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        _write_new(output / "RESULT.json", document)
        _write_text_new(output / "PROCESS_FINAL.txt", (
            f"current_reader_pid={os.getpid()}\nother_reader_process_count={len(readers)}\n"
            + "".join(f"other={value}\n" for value in readers)
        ))
    return exit_status


if __name__ == "__main__":
    raise SystemExit(main())
