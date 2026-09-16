#!/usr/bin/env python3
"""Bounded real-source diagnostic for early articulated admission rejections."""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import time
from typing import Any

from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import (
    ArticulatedRangeRejectionDiagnostic,
)

if __package__:
    from tools.build_c2_full_session_ten_node_ab import build, _jsonable
else:
    from build_c2_full_session_ten_node_ab import build, _jsonable


ROOT = Path(__file__).resolve().parents[1]
MAX_GROUPS = 4
MAX_EVENTS = 50_000
MAX_SOURCE_BYTES = 16 << 20
MAX_ACTUAL_BYTES_READ_UPPER_BOUND = 17 << 20
MAXIMUM_RECORD_BYTES = 4096
INTERNAL_SECONDS = 240.0
REQUIRED_TERMINAL_GROUPS = 2
POST_TARGET_NATIVE200_FRAMES = 20
DEFAULT_COMPLETION_TARGET = "PRODUCT_GATE"
EVENT_CAP_COMPLETION_TARGET = "EVENT_CAP"
PLANNED_WRAPPER = (
    'tools/run_c2_articulated_rejection_prefix_bounded.sh "$OUT"'
)


class _PrefixComplete(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _PrefixProductGate(RuntimeError):
    pass


class _PrefixCompletionTargetFailure(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


class _PrefixDiagnostic:
    """Bounded observer around the canonical record-batch coordinator path."""

    def __init__(self, owners: object, *, started: float,
                 max_prepared_groups: int = MAX_GROUPS,
                 required_terminal_groups: int = REQUIRED_TERMINAL_GROUPS,
                 post_target_native200_frames: int = POST_TARGET_NATIVE200_FRAMES,
                 completion_target: str = DEFAULT_COMPLETION_TARGET,
                 event_cap: int | None = None) -> None:
        if not 1 <= max_prepared_groups <= MAX_GROUPS:
            raise ValueError("prepared-group cap must be within 1..4")
        self.owners = owners
        self.started = started
        self.max_prepared_groups = max_prepared_groups
        if not 1 <= required_terminal_groups <= max_prepared_groups:
            raise ValueError("required terminal groups must be within prepared-group cap")
        if not 0 <= post_target_native200_frames <= 100:
            raise ValueError("post-target native200 cap must be within 0..100")
        if completion_target not in (
            DEFAULT_COMPLETION_TARGET, EVENT_CAP_COMPLETION_TARGET,
        ):
            raise ValueError("unknown prefix completion target")
        if event_cap is None:
            event_cap = MAX_EVENTS
        if type(event_cap) is not int or not 1 <= event_cap <= MAX_EVENTS:
            raise ValueError("event cap must be within 1..50000")
        self.required_terminal_groups = required_terminal_groups
        self.post_target_native200_frames = post_target_native200_frames
        self.completion_target = completion_target
        self.event_cap = event_cap
        self.batches = 0
        self.source_bytes = 0
        self.prepared = {"a": 0, "b": 0}
        self.prepared_pairs = 0
        self.observed_group_provenances: list[str] = []
        self.terminal_group_provenances: list[str] = []
        self.successful_b_group_provenances: list[str] = []
        self._observed_group_provenance_set: set[str] = set()
        self._terminal_group_provenance_set: set[str] = set()
        self._successful_b_group_provenance_set: set[str] = set()
        self._qualifying_b_group_provenance_set: set[str] = set()
        self.actual_admission_pairs = 0
        self.latest = {"a": None, "b": None}
        self.rejections: deque[dict[str, Any]] = deque(maxlen=16)
        self._last_ab_transaction_total = 0
        self._target_native200_frames: int | None = None
        self.terminated = False

    @staticmethod
    def _validate_disposition(branch: str, disposition: object) -> None:
        if disposition is None or not getattr(disposition, "reason", None):
            raise RuntimeError("authoritative A/B transaction disposition is invalid")
        item = disposition.admission
        if item is None:
            return
        if branch == "a":
            valid = (
                item.branch == "A_BASELINE" and not item.commit_intent
                and not item.commit_attempted and not item.commit_succeeded
                and item.outcome == "BASELINE_NO_UWB_COMMIT"
            )
        else:
            state = (item.prepared_accepted, item.commit_intent,
                     item.commit_attempted, item.commit_succeeded)
            valid = item.branch == "B_UWB" and (
                (item.outcome == "UWB_COMMIT_SUCCEEDED"
                 and state == (True, True, True, True))
                or (item.outcome == "PREPARED_REJECTED_NO_COMMIT"
                    and state == (False, True, False, False))
                or (item.outcome == "AB_TRANSACTION_ABORTED_BEFORE_BRANCH_COMMIT"
                    and state[1:] == (True, False, False))
                or (item.outcome.startswith("EVENT_COMMIT_FAILED_ROLLED_BACK:")
                    and state[1:] == (True, True, False)
                    and item.outcome.split(":", 1)[1].isidentifier())
            )
        if not valid:
            raise RuntimeError(f"authoritative {branch.upper()} disposition outcome is invalid")

    def _capture_authoritative_journal(self, audit: object) -> None:
        journal = tuple(audit.ab_transaction_journal)
        total = audit.ab_transaction_total
        if (type(total) is not int or total < 0 or len(journal) > 64
                or total < len(journal)):
            raise RuntimeError("authoritative A/B transaction counter is invalid")
        ring_start = total - len(journal)
        if self._last_ab_transaction_total > total:
            raise RuntimeError("authoritative A/B transaction counter regressed")
        if self._last_ab_transaction_total < ring_start:
            raise RuntimeError("authoritative A/B transaction journal overflowed observer cursor")
        unseen = journal[self._last_ab_transaction_total - ring_start:]
        for pair in unseen:
            if (type(pair.provenance_digest) is not str
                    or len(pair.provenance_digest) != 64):
                raise RuntimeError("authoritative A/B transaction provenance is invalid")
            self._validate_disposition("a", pair.a)
            self._validate_disposition("b", pair.b)
            a_item = pair.a.admission
            b_item = pair.b.admission
            for branch, disposition in (("a", pair.a), ("b", pair.b)):
                item = disposition.admission
                if item is None:
                    continue
                payload = None if item.diagnostic is None else _jsonable(item.diagnostic)
                row = {
                    "branch": branch,
                    "trusted_partition": list(item.trusted_partition),
                    "diagnostic": payload,
                    "admission": _jsonable(item),
                }
                self.prepared[branch] += 1
                self.latest[branch] = payload
                if payload is not None:
                    self.rejections.append(row)
            self.prepared_pairs += 1
            reasons = {pair.a.reason, pair.b.reason}
            rolled_back = "OUTER_RECORD_BATCH_ROLLED_BACK" in reasons
            if (not rolled_back
                    and pair.provenance_digest not in self._observed_group_provenance_set):
                if len(self._observed_group_provenance_set) >= MAX_EVENTS:
                    raise RuntimeError("distinct observed-group capacity exceeded")
                self._observed_group_provenance_set.add(pair.provenance_digest)
                if len(self.observed_group_provenances) < MAX_GROUPS:
                    self.observed_group_provenances.append(pair.provenance_digest)
            actual_admission = a_item is not None and b_item is not None
            if actual_admission:
                self.actual_admission_pairs += 1
            if (actual_admission
                    and pair.provenance_digest not in self._terminal_group_provenance_set):
                if len(self._terminal_group_provenance_set) >= MAX_EVENTS:
                    raise RuntimeError("distinct terminal-group capacity exceeded")
                self._terminal_group_provenance_set.add(pair.provenance_digest)
                if len(self.terminal_group_provenances) < MAX_GROUPS:
                    self.terminal_group_provenances.append(pair.provenance_digest)
            if (
                b_item is not None
                and b_item.outcome == "UWB_COMMIT_SUCCEEDED"
                and pair.provenance_digest not in self._successful_b_group_provenance_set
            ):
                self._successful_b_group_provenance_set.add(pair.provenance_digest)
                if len(self.successful_b_group_provenances) < MAX_GROUPS:
                    self.successful_b_group_provenances.append(pair.provenance_digest)
                if actual_admission:
                    self._qualifying_b_group_provenance_set.add(
                        pair.provenance_digest
                    )
        self._last_ab_transaction_total = total

    def consume(self, ticket: object) -> None:
        if self.completion_target == EVENT_CAP_COMPLETION_TARGET:
            event_digests = getattr(ticket, "event_digests", None)
            if (type(event_digests) is not tuple or not event_digests
                    or any(type(value) is not str or len(value) != 64
                           for value in event_digests)):
                raise RuntimeError("record ticket event cardinality is invalid")
            current_events = int(self.owners.coordinator.audit().events)
            if current_events + len(event_digests) > self.event_cap:
                raise _PrefixCompletionTargetFailure(
                    "EVENT_CAP_RECORD_OVERSHOOT",
                    "next complete raw record would exceed exact event cap",
                )
        self.owners.coordinator.consume_record_ticket(ticket)
        self.batches += 1
        self.source_bytes = max(
            self.source_bytes,
            int(ticket.raw_identity[2]) - int(self.owners.reader.inventory.start_offset),
        )
        audit = self.owners.coordinator.audit()
        self._capture_authoritative_journal(audit)
        if (
            self._qualifying_b_group_provenance_set
            and self._target_native200_frames is None
        ):
            self._target_native200_frames = int(audit.native200_frames)
        target_qualified = (
            len(self._terminal_group_provenance_set) >= self.required_terminal_groups
            and bool(self._qualifying_b_group_provenance_set)
        )
        if (self.completion_target == DEFAULT_COMPLETION_TARGET
                and target_qualified and self.post_target_native200_frames == 0):
            raise _PrefixComplete("REQUIRED_TERMINAL_GROUPS_WITH_B_COMMIT")
        if (self.completion_target == DEFAULT_COMPLETION_TARGET
                and target_qualified and self._target_native200_frames is not None and (
            int(audit.native200_frames) - self._target_native200_frames
            >= self.post_target_native200_frames
        )):
            raise _PrefixComplete("POST_TARGET_NATIVE200_CAP")
        if (self.completion_target == DEFAULT_COMPLETION_TARGET
                and not self._qualifying_b_group_provenance_set
                and self.actual_admission_pairs >= MAX_GROUPS):
            raise _PrefixProductGate(
                "four authoritative admission pairs produced no successful B commit"
            )
        if self.completion_target == EVENT_CAP_COMPLETION_TARGET:
            if int(audit.events) == self.event_cap:
                raise _PrefixComplete("EVENT_CAP")
            if int(audit.events) > self.event_cap:
                raise RuntimeError("delivered events exceeded exact event cap")
        elif int(audit.events) >= self.event_cap:
            raise _PrefixComplete("EVENT_CAP")
        read_trigger = MAX_SOURCE_BYTES - MAXIMUM_RECORD_BYTES
        if read_trigger <= 0:
            raise ValueError("reader chunk leaves no conservative source budget")
        if self.source_bytes >= read_trigger:
            raise _PrefixComplete("SOURCE_BYTE_CAP")
        if self.terminated or time.monotonic() - self.started >= INTERNAL_SECONDS:
            raise _PrefixComplete("INTERNAL_TIME_CAP")

    def snapshot(self, reason: str) -> dict[str, object]:
        audit = self.owners.coordinator.audit()
        diagnostics = list(self.rejections)
        routing = getattr(audit, "diagnostic_routing_metrics", None)
        routing_conserved = routing is not None and (
            int(routing.unrouted_frames) + int(routing.batched_frames)
            + int(routing.scalar_frames) == int(audit.native200_frames)
        )
        if routing is not None and not routing_conserved:
            raise RuntimeError("diagnostic native200 routing does not conserve frames")
        return {
            "status": "COMPLETE_CAP_REACHED", "cap_reason": reason,
            "accounting_scope": "PARTIAL_PREFIX",
            "full_stream_audit": None,
            "full_stream_audit_unavailable_reason": (
                "INTENTIONAL_SYNCHRONOUS_CALLBACK_STOP_BEFORE_READER_FINISH"
            ),
            "completion_target": self.completion_target,
            "completion_target_reached": (
                reason == EVENT_CAP_COMPLETION_TARGET
                if self.completion_target == EVENT_CAP_COMPLETION_TARGET
                else reason in (
                    "REQUIRED_TERMINAL_GROUPS_WITH_B_COMMIT",
                    "POST_TARGET_NATIVE200_CAP", "EVENT_CAP",
                )
            ),
            "early_success_observed": bool(self._successful_b_group_provenance_set),
            "limits": {"prepared_groups": self.max_prepared_groups,
                       "events": self.event_cap,
                       "processed_source_bytes": MAX_SOURCE_BYTES,
                       "actual_bytes_read_upper_bound": MAX_ACTUAL_BYTES_READ_UPPER_BOUND,
                       "internal_seconds": INTERNAL_SECONDS},
            "consume_record_batches_calls": 1, "record_batches": self.batches,
            "delivered_events": int(audit.events),
            "processed_source_bytes": self.source_bytes,
            "reader_chunk_bytes": int(self.owners.reader.chunk_bytes),
            "maximum_record_bytes": MAXIMUM_RECORD_BYTES,
            "actual_bytes_read_upper_bound": (
                self.source_bytes + int(self.owners.reader.chunk_bytes)
            ),
            "prepared_by_branch": dict(self.prepared),
            "prepared_group_pairs": self.prepared_pairs,
            "actual_admission_pairs": self.actual_admission_pairs,
            "distinct_observed_groups": len(self._observed_group_provenance_set),
            "distinct_terminal_groups": len(self._terminal_group_provenance_set),
            "terminal_group_provenances": list(self.terminal_group_provenances),
            "successful_b_group_provenances": list(
                self.successful_b_group_provenances
            ),
            "successful_b_groups": len(self._successful_b_group_provenance_set),
            "qualifying_paired_b_successes": len(
                self._qualifying_b_group_provenance_set
            ),
            "required_terminal_groups": self.required_terminal_groups,
            "post_target_native200_frames_required": self.post_target_native200_frames,
            "target_native200_frames": self._target_native200_frames,
            "post_target_native200_frames_observed": (
                None if self._target_native200_frames is None else
                int(audit.native200_frames) - self._target_native200_frames
            ),
            "latest_articulated_rejection": self.latest,
            "bounded_rejections": diagnostics,
            "bounded_rejection_capacity": 16,
            "diagnostic_sha256": hashlib.sha256(_canonical(diagnostics)).hexdigest(),
            "diagnostic_routing_metrics": _jsonable(routing),
            "diagnostic_routing_conserved": routing_conserved,
            "coordinator_audit": _jsonable(audit),
            "wall_s": time.monotonic() - self.started,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }


def _run_prefix(
    owners: object, *, max_prepared_groups: int = MAX_GROUPS,
    required_terminal_groups: int = REQUIRED_TERMINAL_GROUPS,
    post_target_native200_frames: int = POST_TARGET_NATIVE200_FRAMES,
    completion_target: str = DEFAULT_COMPLETION_TARGET,
    event_cap: int | None = None,
) -> dict[str, object]:
    observer = _PrefixDiagnostic(
        owners, started=time.monotonic(), max_prepared_groups=max_prepared_groups,
        required_terminal_groups=required_terminal_groups,
        post_target_native200_frames=post_target_native200_frames,
        completion_target=completion_target, event_cap=event_cap,
    )
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda *_args: setattr(observer, "terminated", True))
    try:
        try:
            owners.reader.consume_record_batches(observer.consume)
        except _PrefixComplete as complete:
            result = observer.snapshot(complete.reason)
            if (completion_target == EVENT_CAP_COMPLETION_TARGET
                    and complete.reason != EVENT_CAP_COMPLETION_TARGET):
                result["status"] = "FAILED_COMPLETION_TARGET"
                result["failure"] = {
                    "type": "CompletionTargetNotReached",
                    "message": f"{complete.reason} preceded exact EVENT_CAP",
                }
            return result
        except _PrefixCompletionTargetFailure as failure:
            result = observer.snapshot(failure.reason)
            result["status"] = "FAILED_COMPLETION_TARGET"
            result["failure"] = {
                "type": type(failure).__name__, "message": str(failure),
            }
            return result
        except _PrefixProductGate as failure:
            result = observer.snapshot("PRODUCT_GATE_NO_B_UWB_COMMIT")
            result["status"] = "FAILED_PRODUCT_GATE"
            result["failure"] = {
                "type": type(failure).__name__, "message": str(failure),
            }
            return result
        result = observer.snapshot("SOURCE_COMPLETE")
        if completion_target == EVENT_CAP_COMPLETION_TARGET:
            result["status"] = "FAILED_COMPLETION_TARGET"
            result["failure"] = {
                "type": "CompletionTargetNotReached",
                "message": "source completed before exact EVENT_CAP",
            }
        return result
    finally:
        signal.signal(signal.SIGTERM, previous)


def _write_new(path: Path, value: object) -> None:
    payload = _canonical(value) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o444)
    try:
        if os.write(fd, payload) != len(payload):
            raise RuntimeError("short diagnostic write")
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(
        epilog=f"Approved bounded wrapper: {PLANNED_WRAPPER}",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-prepared-groups", type=int, default=MAX_GROUPS)
    parser.add_argument("--required-terminal-groups", type=int,
                        default=REQUIRED_TERMINAL_GROUPS)
    parser.add_argument("--post-target-native200-frames", type=int,
                        default=POST_TARGET_NATIVE200_FRAMES)
    parser.add_argument(
        "--completion-target",
        choices=(DEFAULT_COMPLETION_TARGET, EVENT_CAP_COMPLETION_TARGET),
        default=DEFAULT_COMPLETION_TARGET,
    )
    parser.add_argument("--event-cap", type=int, default=MAX_EVENTS)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "logs"):
        raise ValueError("diagnostic evidence must be under workspace logs")
    try:
        result = _run_prefix(
            build(), max_prepared_groups=args.max_prepared_groups,
            required_terminal_groups=args.required_terminal_groups,
            post_target_native200_frames=args.post_target_native200_frames,
            completion_target=args.completion_target,
            event_cap=args.event_cap,
        )
    except BaseException as error:
        result = {"status": "FAILED", "failure": {
            "type": type(error).__name__, "message": str(error),
        }, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    _write_new(output, result)
    return 0 if result["status"] == "COMPLETE_CAP_REACHED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
