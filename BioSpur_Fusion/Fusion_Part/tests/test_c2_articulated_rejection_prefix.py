from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import (
    ArticulatedRangeRejectionDiagnostic,
)

from tools import diagnose_c2_articulated_rejection_prefix as diagnostic


def _rejection() -> ArticulatedRangeRejectionDiagnostic:
    return ArticulatedRangeRejectionDiagnostic(
        "a" * 64, "b" * 64, 7, 100, 200, None, ("n0", "n1"),
        "PROJECTED_FOOTHOLD_GATE_FAILURE", "FULL_EXISTING_NORMALIZED_RESIDUAL_OBJECTIVE",
        "JOINT_FULL_RESIDUAL", True, True, 1, ("ankle_left",),
        None, None, None, "optimizer-message", 17, 12.5,
        1.0, 0.9, 0.8, True, 2.0, 1.0, 1.5, 2e-10, True, 2e-10,
        True, True, True, True, True, True, 0.3, True,
        3, 4.0, 0.1, True, True, None, True, False, None,
    )


class _Ticket:
    def __init__(self, ordinal: int, *, size: int = 1, end: int | None = None) -> None:
        self.raw_identity = (ordinal, ordinal * 100,
                             ordinal * 100 + 99 if end is None else end, "c" * 64)
        self.deliveries = 0
        self.size = size
        self.event_digests = tuple(f"{index:064x}" for index in range(size))

    def deliver(self, callback) -> None:
        self.deliveries += 1
        callback(tuple(SimpleNamespace() for _ in range(self.size)))


class _Engine:
    def __init__(self, rejection=None, *, trusted=("n0", "n1")) -> None:
        self.rejection = _rejection() if rejection is None else rejection
        self.trusted = trusted

    def prepare_admission(self, _packet, _epoch):
        return SimpleNamespace(
            trusted_partition=self.trusted,
            articulated_rejection_diagnostic=self.rejection,
        )


class _Coordinator:
    def __init__(self, *, prepare: bool = True, right_rejection=None,
                 right_trusted=("n0", "n1"), max_pairs: int = 2,
                 success_at: int | None = 0) -> None:
        self._a = SimpleNamespace(_composition=SimpleNamespace(engine=_Engine()))
        self._b = SimpleNamespace(_composition=SimpleNamespace(
            engine=_Engine(right_rejection, trusted=right_trusted)))
        self.events = 0
        self.native200_frames = 0
        self.prepare = prepare
        self.max_pairs = max_pairs
        self.success_at = success_at
        self.pairs = []

    def consume_record_ticket(self, ticket) -> None:
        ticket.deliver(lambda batch: setattr(self, "events", self.events + len(batch)))
        self.native200_frames += 1
        if self.prepare and len(self.pairs) < self.max_pairs:
            successful = len(self.pairs) == self.success_at
            admissions = {}
            for branch, engine, key in (
                ("A_BASELINE", self._a._composition.engine, "a"),
                ("B_UWB", self._b._composition.engine, "b"),
            ):
                prepared = engine.prepare_admission(None, None)
                admissions[key] = SimpleNamespace(
                    branch=branch,
                    bucket=len(self.pairs), packet_digest="1" * 64,
                    epoch_digest="2" * 64, candidate_digest="3" * 64,
                    source_sequence=len(self.pairs),
                    source_identity=("pelvis", len(self.pairs)),
                    trusted_partition=prepared.trusted_partition,
                    prepared_accepted=successful,
                    prepared_reason=("ACCEPTED" if successful
                                     else "ARTICULATED_RANGE_REJECTED"),
                    diagnostic_digest=(None if successful else "4" * 64),
                    diagnostic=(None if successful else
                                prepared.articulated_rejection_diagnostic),
                    commit_intent=branch == "B_UWB",
                    commit_attempted=(successful and branch == "B_UWB"),
                    commit_succeeded=(successful and branch == "B_UWB"),
                    outcome=("BASELINE_NO_UWB_COMMIT" if branch == "A_BASELINE"
                             else ("UWB_COMMIT_SUCCEEDED" if successful
                                   else "PREPARED_REJECTED_NO_COMMIT")),
                )
            self.pairs.append(SimpleNamespace(
                provenance_digest=f"{len(self.pairs) + 1:064x}",
                a=SimpleNamespace(reason="PREPARED_ADMISSION",
                                  admission=admissions["a"]),
                b=SimpleNamespace(reason="PREPARED_ADMISSION",
                                  admission=admissions["b"]),
            ))

    def audit(self):
        return SimpleNamespace(
            events=self.events,
            native200_frames=self.native200_frames,
            ab_transaction_journal=tuple(self.pairs),
            ab_transaction_total=len(self.pairs),
            diagnostic_routing_metrics=SimpleNamespace(
                batched_records=1, batched_frames=self.native200_frames,
                scalar_frames=0, unrouted_frames=0, scalar_fallbacks=(),
            ),
        )


class _Reader:
    def __init__(self, *, count: int = 20, size: int = 1, end=None,
                 chunk_bytes: int = 1 << 20) -> None:
        self.inventory = SimpleNamespace(start_offset=0)
        self.chunk_bytes = chunk_bytes
        self.calls = 0
        self.tickets = []
        self.count, self.size, self.end = count, size, end

    def consume_record_batches(self, callback):
        self.calls += 1
        for ordinal in range(self.count):
            ticket = _Ticket(ordinal, size=self.size,
                             end=self.end if ordinal == 0 else None)
            self.tickets.append(ticket)
            callback(ticket)


def test_prefix_uses_public_batch_once_and_stops_with_bounded_exact_diagnostics() -> None:
    owners = SimpleNamespace(reader=_Reader(count=30), coordinator=_Coordinator())

    result = diagnostic._run_prefix(owners)

    assert owners.reader.calls == 1
    assert len(owners.reader.tickets) == 21
    assert all(ticket.deliveries == 1 for ticket in owners.reader.tickets)
    assert result["cap_reason"] == "POST_TARGET_NATIVE200_CAP"
    assert result["prepared_group_pairs"] == 2
    assert result["prepared_by_branch"] == {"a": 2, "b": 2}
    assert len(result["bounded_rejections"]) == 2
    assert result["latest_articulated_rejection"]["a"] == result["latest_articulated_rejection"]["b"]
    assert result["latest_articulated_rejection"]["a"]["contact_gate_passed"] is None
    assert result["latest_articulated_rejection"]["a"]["optimizer_success"] is None
    assert result["delivered_events"] == 21
    assert result["processed_source_bytes"] <= diagnostic.MAX_SOURCE_BYTES
    assert result["actual_bytes_read_upper_bound"] <= diagnostic.MAX_ACTUAL_BYTES_READ_UPPER_BOUND
    assert result["accounting_scope"] == "PARTIAL_PREFIX"
    assert result["full_stream_audit"] is None
    assert result["full_stream_audit_unavailable_reason"]
    assert result["diagnostic_routing_metrics"] == {
        "batched_records": 1, "batched_frames": 21,
        "scalar_frames": 0, "unrouted_frames": 0, "scalar_fallbacks": [],
    }
    assert result["diagnostic_routing_conserved"] is True


def test_prefix_supports_exact_two_terminal_target_without_post_frames() -> None:
    owners = SimpleNamespace(reader=_Reader(), coordinator=_Coordinator())
    result = diagnostic._run_prefix(
        owners, max_prepared_groups=2, required_terminal_groups=2,
        post_target_native200_frames=0,
    )
    assert result["cap_reason"] == "REQUIRED_TERMINAL_GROUPS_WITH_B_COMMIT"
    assert result["prepared_group_pairs"] == 2
    assert len(owners.reader.tickets) == 2


def test_independent_ab_diagnostic_and_trusted_partitions_are_preserved() -> None:
    divergent = replace(_rejection(), optimizer_message="different-message")
    owners = SimpleNamespace(reader=_Reader(), coordinator=_Coordinator(
        right_rejection=divergent, right_trusted=("n0",)))
    result = diagnostic._run_prefix(owners, post_target_native200_frames=0)
    assert result["latest_articulated_rejection"]["a"]["optimizer_message"] == "optimizer-message"
    assert result["latest_articulated_rejection"]["b"]["optimizer_message"] == "different-message"
    assert result["bounded_rejections"][-1]["trusted_partition"] == ["n0"]


@pytest.mark.parametrize(("cap", "reason"), (
    ("events", "EVENT_CAP"),
    ("source", "SOURCE_BYTE_CAP"),
    ("time", "INTERNAL_TIME_CAP"),
))
def test_each_first_cap_stops_after_one_complete_once_only_ticket(
    monkeypatch, cap: str, reason: str,
) -> None:
    monkeypatch.setattr(diagnostic, "MAX_EVENTS", 2 if cap == "events" else 999)
    monkeypatch.setattr(diagnostic, "MAX_SOURCE_BYTES", 1000)
    monkeypatch.setattr(diagnostic, "MAXIMUM_RECORD_BYTES", 10)
    if cap == "time":
        values = iter((0.0, 241.0, 242.0))
        monkeypatch.setattr(diagnostic.time, "monotonic", lambda: next(values))
    reader = _Reader(
        count=10, size=2 if cap == "events" else 1,
        end=990 if cap == "source" else 99, chunk_bytes=100,
    )
    coordinator = _Coordinator(prepare=False)
    owners = SimpleNamespace(reader=reader, coordinator=coordinator)

    result = diagnostic._run_prefix(owners)

    assert result["cap_reason"] == reason
    assert reader.calls == 1 and len(reader.tickets) == 1
    assert reader.tickets[0].deliveries == 1
    assert result["processed_source_bytes"] <= 1000
    assert result["actual_bytes_read_upper_bound"] <= 1100


def test_event_cap_completion_ignores_early_success_and_stops_exactly() -> None:
    owners = SimpleNamespace(
        reader=_Reader(count=10),
        coordinator=_Coordinator(max_pairs=10, success_at=0),
    )
    result = diagnostic._run_prefix(
        owners, completion_target="EVENT_CAP", event_cap=5,
    )
    assert result["status"] == "COMPLETE_CAP_REACHED"
    assert result["cap_reason"] == "EVENT_CAP"
    assert result["completion_target"] == "EVENT_CAP"
    assert result["completion_target_reached"] is True
    assert result["early_success_observed"] is True
    assert result["delivered_events"] == 5
    assert len(owners.reader.tickets) == 5
    assert all(ticket.deliveries == 1 for ticket in owners.reader.tickets)


def test_completion_target_mislabel_rejects_before_reader_use() -> None:
    owners = SimpleNamespace(reader=_Reader(), coordinator=_Coordinator())
    with pytest.raises(ValueError, match="unknown prefix completion target"):
        diagnostic._run_prefix(owners, completion_target="EVENTS")
    assert owners.reader.calls == 0


def test_event_cap_rejects_crossing_record_before_delivery() -> None:
    owners = SimpleNamespace(
        reader=_Reader(count=3, size=3), coordinator=_Coordinator(prepare=False),
    )
    result = diagnostic._run_prefix(
        owners, completion_target="EVENT_CAP", event_cap=5,
    )
    assert result["status"] == "FAILED_COMPLETION_TARGET"
    assert result["cap_reason"] == "EVENT_CAP_RECORD_OVERSHOOT"
    assert result["completion_target_reached"] is False
    assert result["delivered_events"] == 3
    assert [ticket.deliveries for ticket in owners.reader.tickets] == [1, 0]


@pytest.mark.parametrize(("cap", "reason"), (
    ("source", "SOURCE_BYTE_CAP"),
    ("time", "INTERNAL_TIME_CAP"),
    ("source_complete", "SOURCE_COMPLETE"),
))
def test_event_cap_reports_earlier_non_target_termination(
    monkeypatch, cap: str, reason: str,
) -> None:
    monkeypatch.setattr(diagnostic, "MAX_SOURCE_BYTES", 1000)
    monkeypatch.setattr(diagnostic, "MAXIMUM_RECORD_BYTES", 10)
    if cap == "time":
        values = iter((0.0, 241.0, 242.0))
        monkeypatch.setattr(diagnostic.time, "monotonic", lambda: next(values))
    reader = _Reader(
        count=1 if cap == "source_complete" else 10,
        end=990 if cap == "source" else 99,
        chunk_bytes=100,
    )
    result = diagnostic._run_prefix(
        SimpleNamespace(reader=reader, coordinator=_Coordinator(prepare=False)),
        completion_target="EVENT_CAP", event_cap=10,
    )
    assert result["status"] == "FAILED_COMPLETION_TARGET"
    assert result["cap_reason"] == reason
    assert result["completion_target"] == "EVENT_CAP"
    assert result["completion_target_reached"] is False


def test_help_records_exact_bounded_sealing_wrapper() -> None:
    wrapper = diagnostic.PLANNED_WRAPPER
    assert wrapper == 'tools/run_c2_articulated_rejection_prefix_bounded.sh "$OUT"'
    completed = subprocess.run(
        (sys.executable, str(Path(diagnostic.__file__)), "--help"),
        check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 0
    assert "--completion-target {PRODUCT_GATE,EVENT_CAP}" in completed.stdout
    assert "--event-cap EVENT_CAP" in completed.stdout


def test_prefix_anchors_on_second_terminal_pair_then_observes_later_native200() -> None:
    coordinator = _Coordinator(prepare=False)
    left = SimpleNamespace(
        branch="A_BASELINE", bucket=0, packet_digest="1" * 64,
        epoch_digest="2" * 64, candidate_digest="3" * 64,
        source_sequence=0, source_identity=("pelvis", 0),
        trusted_partition=("n0", "n1"), prepared_accepted=True,
        prepared_reason="ACCEPTED", diagnostic_digest=None, diagnostic=None,
        commit_intent=False, commit_attempted=False, commit_succeeded=False,
        outcome="BASELINE_NO_UWB_COMMIT",
    )
    right = SimpleNamespace(
        branch="B_UWB", bucket=0, packet_digest="1" * 64,
        epoch_digest="2" * 64, candidate_digest="3" * 64,
        source_sequence=0, source_identity=("pelvis", 0),
        trusted_partition=("n0", "n1"), prepared_accepted=True,
        prepared_reason="ACCEPTED", diagnostic_digest=None, diagnostic=None,
        commit_intent=True, commit_attempted=True, commit_succeeded=True,
        outcome="UWB_COMMIT_SUCCEEDED",
    )
    coordinator.pairs.extend((
        SimpleNamespace(
            provenance_digest="5" * 64,
            a=SimpleNamespace(reason="PREPARED_ADMISSION", admission=left),
            b=SimpleNamespace(reason="PREPARED_ADMISSION", admission=right),
        ),
        SimpleNamespace(
            provenance_digest="6" * 64,
            a=SimpleNamespace(reason="PREPARED_ADMISSION", admission=left),
            b=SimpleNamespace(reason="PREPARED_ADMISSION", admission=right),
        ),
    ))
    owners = SimpleNamespace(reader=_Reader(count=25), coordinator=coordinator)
    result = diagnostic._run_prefix(owners)
    assert result["cap_reason"] == "POST_TARGET_NATIVE200_CAP"
    assert result["prepared_group_pairs"] == 2
    assert result["target_native200_frames"] == 1
    assert result["post_target_native200_frames_observed"] == 20
    assert len(owners.reader.tickets) == 21


@pytest.mark.parametrize("corruption", ("provenance", "disposition", "b_outcome"))
def test_corrupt_paired_transaction_audit_fails_closed(corruption) -> None:
    coordinator = _Coordinator()
    coordinator.consume_record_ticket(_Ticket(0))
    pair = coordinator.pairs[0]
    if corruption == "provenance":
        pair.provenance_digest = "bad"
    elif corruption == "disposition":
        pair.a = None
    else:
        pair.b.admission.outcome = "UNKNOWN"
    owners = SimpleNamespace(reader=_Reader(), coordinator=coordinator)
    with pytest.raises(RuntimeError, match="authoritative"):
        diagnostic._run_prefix(owners)


def test_absolute_cursor_accepts_repeated_and_unique_provenance_across_polls() -> None:
    coordinator = _Coordinator()
    coordinator.consume_record_ticket(_Ticket(0))
    first = coordinator.pairs[0]
    observer = diagnostic._PrefixDiagnostic(
        SimpleNamespace(coordinator=coordinator), started=0.0,
    )
    observer._capture_authoritative_journal(SimpleNamespace(
        ab_transaction_journal=(first,), ab_transaction_total=1,
    ))
    observer._capture_authoritative_journal(SimpleNamespace(
        ab_transaction_journal=(first, first), ab_transaction_total=2,
    ))
    unique = SimpleNamespace(
        provenance_digest="f" * 64, a=first.a, b=first.b,
    )
    observer._capture_authoritative_journal(SimpleNamespace(
        ab_transaction_journal=(first, first, unique), ab_transaction_total=3,
    ))
    assert observer.prepared_pairs == 3
    assert observer.terminal_group_provenances == [first.provenance_digest,
                                                   unique.provenance_digest]


def test_repeated_same_provenance_cannot_satisfy_two_terminal_groups() -> None:
    coordinator = _Coordinator()
    coordinator.consume_record_ticket(_Ticket(0))
    pair = coordinator.pairs[0]
    observer = diagnostic._PrefixDiagnostic(
        SimpleNamespace(coordinator=coordinator), started=0.0,
    )
    observer._capture_authoritative_journal(SimpleNamespace(
        ab_transaction_journal=(pair, pair, pair), ab_transaction_total=3,
    ))
    assert observer.prepared_pairs == 3
    assert observer.observed_group_provenances == [pair.provenance_digest]
    assert observer.terminal_group_provenances == [pair.provenance_digest]


def test_eight_retry_occurrences_do_not_trigger_group_cap_or_target(monkeypatch) -> None:
    monkeypatch.setattr(diagnostic, "MAX_EVENTS", 1)
    coordinator = _Coordinator(prepare=False)
    disposition = SimpleNamespace(reason="STALE_POSE_LINK_DEFERRED", admission=None)
    pair = SimpleNamespace(provenance_digest="a" * 64,
                           a=disposition, b=disposition)
    coordinator.pairs[:] = [pair] * 8
    owners = SimpleNamespace(reader=_Reader(count=2), coordinator=coordinator)
    result = diagnostic._run_prefix(owners)
    assert result["cap_reason"] == "EVENT_CAP"
    assert result["prepared_group_pairs"] == 8
    assert result["distinct_observed_groups"] == 1
    assert result["distinct_terminal_groups"] == 0
    assert result["target_native200_frames"] is None


def test_retryable_then_terminal_same_provenance_counts_once() -> None:
    coordinator = _Coordinator()
    coordinator.consume_record_ticket(_Ticket(0))
    terminal = coordinator.pairs[0]
    retryable = SimpleNamespace(
        provenance_digest=terminal.provenance_digest,
        a=SimpleNamespace(reason="STALE_POSE_LINK_DEFERRED", admission=None),
        b=SimpleNamespace(reason="STALE_POSE_LINK_DEFERRED", admission=None),
    )
    observer = diagnostic._PrefixDiagnostic(
        SimpleNamespace(coordinator=coordinator), started=0.0,
    )
    observer._capture_authoritative_journal(SimpleNamespace(
        ab_transaction_journal=(retryable,), ab_transaction_total=1,
    ))
    assert observer.terminal_group_provenances == []
    observer._capture_authoritative_journal(SimpleNamespace(
        ab_transaction_journal=(retryable, terminal), ab_transaction_total=2,
    ))
    assert observer.terminal_group_provenances == [terminal.provenance_digest]


def test_two_distinct_terminal_provenances_satisfy_target() -> None:
    coordinator = _Coordinator()
    coordinator.consume_record_ticket(_Ticket(0))
    first = coordinator.pairs[0]
    second = SimpleNamespace(provenance_digest="d" * 64, a=first.a, b=first.b)
    observer = diagnostic._PrefixDiagnostic(
        SimpleNamespace(coordinator=coordinator), started=0.0,
    )
    observer._capture_authoritative_journal(SimpleNamespace(
        ab_transaction_journal=(first, second), ab_transaction_total=2,
    ))
    assert observer.terminal_group_provenances == [
        first.provenance_digest, second.provenance_digest,
    ]


def test_absolute_cursor_fails_on_real_overflow_regression_and_counter_forgery() -> None:
    coordinator = _Coordinator()
    coordinator.consume_record_ticket(_Ticket(0))
    pair = coordinator.pairs[0]

    overflow = diagnostic._PrefixDiagnostic(
        SimpleNamespace(coordinator=coordinator), started=0.0,
    )
    with pytest.raises(RuntimeError, match="overflowed observer cursor"):
        overflow._capture_authoritative_journal(SimpleNamespace(
            ab_transaction_journal=(pair,) * 64, ab_transaction_total=65,
        ))

    regressed = diagnostic._PrefixDiagnostic(
        SimpleNamespace(coordinator=coordinator), started=0.0,
    )
    regressed._capture_authoritative_journal(SimpleNamespace(
        ab_transaction_journal=(pair,), ab_transaction_total=1,
    ))
    with pytest.raises(RuntimeError, match="counter regressed"):
        regressed._capture_authoritative_journal(SimpleNamespace(
            ab_transaction_journal=(), ab_transaction_total=0,
        ))

    forged = diagnostic._PrefixDiagnostic(
        SimpleNamespace(coordinator=coordinator), started=0.0,
    )
    with pytest.raises(RuntimeError, match="counter is invalid"):
        forged._capture_authoritative_journal(SimpleNamespace(
            ab_transaction_journal=(pair,), ab_transaction_total=0,
        ))


def test_absolute_cursor_counts_outer_rollback_once_without_b_success() -> None:
    disposition = SimpleNamespace(reason="OUTER_RECORD_BATCH_ROLLED_BACK",
                                  admission=None)
    pair = SimpleNamespace(provenance_digest="e" * 64,
                           a=disposition, b=disposition)
    observer = diagnostic._PrefixDiagnostic(
        SimpleNamespace(coordinator=object()), started=0.0,
    )
    audit = SimpleNamespace(ab_transaction_journal=(pair,), ab_transaction_total=1)
    observer._capture_authoritative_journal(audit)
    observer._capture_authoritative_journal(audit)
    assert observer.prepared_pairs == 1
    assert observer.prepared == {"a": 0, "b": 0}
    assert observer.terminal_group_provenances == []


def test_obsolete_without_admission_cannot_qualify_terminal_target() -> None:
    disposition = SimpleNamespace(
        reason="OBSOLETE_NATIVE200_SOURCE_PAIR", admission=None,
    )
    pair = SimpleNamespace(
        provenance_digest="9" * 64, a=disposition, b=disposition,
    )
    observer = diagnostic._PrefixDiagnostic(
        SimpleNamespace(coordinator=object()), started=0.0,
    )
    observer._capture_authoritative_journal(SimpleNamespace(
        ab_transaction_journal=(pair,), ab_transaction_total=1,
    ))
    assert observer.actual_admission_pairs == 0
    assert observer.terminal_group_provenances == []
    assert observer.successful_b_group_provenances == []


@pytest.mark.parametrize("missing_branch", ("a", "b"))
def test_independent_branch_admission_is_counted_without_false_pair_failure(
    missing_branch: str,
) -> None:
    coordinator = _Coordinator()
    coordinator.consume_record_ticket(_Ticket(0))
    original = coordinator.pairs[0]
    absent = SimpleNamespace(reason="STALE_POSE_LINK_DEFERRED", admission=None)
    pair = SimpleNamespace(
        provenance_digest=original.provenance_digest,
        a=absent if missing_branch == "a" else original.a,
        b=absent if missing_branch == "b" else original.b,
    )
    observer = diagnostic._PrefixDiagnostic(
        SimpleNamespace(coordinator=object()), started=0.0,
    )
    observer._capture_authoritative_journal(SimpleNamespace(
        ab_transaction_journal=(pair,), ab_transaction_total=1,
    ))
    assert observer.prepared == (
        {"a": 0, "b": 1} if missing_branch == "a" else {"a": 1, "b": 0}
    )
    assert observer.actual_admission_pairs == 0
    assert observer.terminal_group_provenances == []
    assert observer._qualifying_b_group_provenance_set == set()
    if missing_branch == "a":
        assert observer.successful_b_group_provenances == [pair.provenance_digest]
    else:
        assert observer.successful_b_group_provenances == []


def test_asymmetric_admission_then_outer_rollback_is_counted_once_and_excluded() -> None:
    coordinator = _Coordinator()
    coordinator.consume_record_ticket(_Ticket(0))
    original = coordinator.pairs[0]
    asymmetric = SimpleNamespace(
        provenance_digest=original.provenance_digest,
        a=SimpleNamespace(reason="STALE_POSE_LINK_DEFERRED", admission=None),
        b=original.b,
    )
    rolled_back_disposition = SimpleNamespace(
        reason="OUTER_RECORD_BATCH_ROLLED_BACK", admission=None,
    )
    rolled_back = SimpleNamespace(
        provenance_digest="e" * 64,
        a=rolled_back_disposition, b=rolled_back_disposition,
    )
    observer = diagnostic._PrefixDiagnostic(
        SimpleNamespace(coordinator=object()), started=0.0,
    )
    observer._capture_authoritative_journal(SimpleNamespace(
        ab_transaction_journal=(asymmetric,), ab_transaction_total=1,
    ))
    observer._capture_authoritative_journal(SimpleNamespace(
        ab_transaction_journal=(asymmetric, rolled_back), ab_transaction_total=2,
    ))
    assert observer.prepared_pairs == 2
    assert observer.observed_group_provenances == [asymmetric.provenance_digest]
    assert observer._last_ab_transaction_total == 2


def test_asymmetric_pair_still_fails_closed_on_forged_branch_outcome() -> None:
    coordinator = _Coordinator()
    coordinator.consume_record_ticket(_Ticket(0))
    original = coordinator.pairs[0]
    forged_admission = SimpleNamespace(**vars(original.b.admission))
    forged_admission.outcome = "BANANA"
    forged = SimpleNamespace(
        provenance_digest=original.provenance_digest,
        a=SimpleNamespace(reason="STALE_POSE_LINK_DEFERRED", admission=None),
        b=SimpleNamespace(reason="PREPARED_ADMISSION", admission=forged_admission),
    )
    observer = diagnostic._PrefixDiagnostic(
        SimpleNamespace(coordinator=object()), started=0.0,
    )
    with pytest.raises(RuntimeError, match="B disposition outcome is invalid"):
        observer._capture_authoritative_journal(SimpleNamespace(
            ab_transaction_journal=(forged,), ab_transaction_total=1,
        ))
    assert observer.prepared_pairs == 0


def test_four_actual_admissions_without_b_commit_fail_product_gate() -> None:
    owners = SimpleNamespace(
        reader=_Reader(count=8),
        coordinator=_Coordinator(max_pairs=4, success_at=None),
    )
    result = diagnostic._run_prefix(owners)
    assert result["status"] == "FAILED_PRODUCT_GATE"
    assert result["cap_reason"] == "PRODUCT_GATE_NO_B_UWB_COMMIT"
    assert result["actual_admission_pairs"] == 4
    assert result["successful_b_group_provenances"] == []
    assert len(owners.reader.tickets) == 4


def test_primary_record_callback_exception_propagates_without_observer_replacement() -> None:
    coordinator = _Coordinator(prepare=False)
    primary = RuntimeError("primary coordinator failure")
    coordinator.consume_record_ticket = lambda _ticket: (_ for _ in ()).throw(primary)
    owners = SimpleNamespace(reader=_Reader(), coordinator=coordinator)
    with pytest.raises(RuntimeError, match="primary coordinator failure") as caught:
        diagnostic._run_prefix(owners)
    assert caught.value is primary


def _dummy(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)
    return path


def test_bounded_wrapper_success_failure_and_evidence_failure(tmp_path: Path) -> None:
    wrapper = Path("tools/run_c2_articulated_rejection_prefix_bounded.sh").resolve()
    success = _dummy(tmp_path / "success", "printf out; printf err >&2; exit 0\n")
    failure = _dummy(tmp_path / "failure", "printf failed >&2; exit 7\n")
    oversized = _dummy(tmp_path / "oversized", "head -c 1100000 /dev/zero; exit 0\n")

    for name, child, expected in (
        ("ok", success, 0), ("failed", failure, 7), ("large", oversized, 92),
    ):
        output = tmp_path / name
        completed = subprocess.run(
            (str(wrapper), str(output), "--", str(child)), check=False,
            env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                 "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"},
        )
        assert completed.returncode == expected
        assert (output / "COMMAND.txt").read_text() == f"{child} \n"
        status = (output / "STATUS.txt").read_text()
        assert f"child_exit_status={0 if name != 'failed' else 7}" in status
        assert f"final_exit_status={expected}" in status
        assert (output / "PROCESS_FINAL.txt").read_text() == ""
        if name == "large":
            assert "finalization=EVIDENCE_SIZE_EXCEEDED" in status
            assert output.stat().st_mode & 0o777 != 0o555
            assert not (output / "SHA256SUMS").exists()
            continue
        assert all((output / item).is_file() for item in (
            "COMMAND.txt", "START_HASHES.txt", "END_HASHES.txt", "STATUS.txt",
            "RUNTIME.txt", "STDOUT.txt", "STDERR.txt", "PROCESS_FINAL.txt", "SHA256SUMS",
        ))
        assert subprocess.run(("sha256sum", "-c", "SHA256SUMS"), cwd=output,
                              check=False, capture_output=True).returncode == 0
        assert output.stat().st_mode & 0o777 == 0o555
        assert all(path.stat().st_mode & 0o777 == 0o444 for path in output.iterdir())


def test_bounded_wrapper_detects_and_cleans_same_process_group_residual(
    tmp_path: Path,
) -> None:
    wrapper = Path("tools/run_c2_articulated_rejection_prefix_bounded.sh").resolve()
    residual = _dummy(tmp_path / "residual", "sleep 30 & echo $!; exit 0\n")
    output = tmp_path / "residual-output"

    completed = subprocess.run((str(wrapper), str(output), "--", str(residual)),
                               check=False, capture_output=True, text=True)

    assert completed.returncode == 91
    assert "finalization=RESIDUAL_PROCESS_GROUP" in (output / "STATUS.txt").read_text()
    assert (output / "PROCESS_FINAL.txt").read_text()
    assert not (output / "SHA256SUMS").exists()
    assert output.stat().st_mode & 0o777 != 0o555
    residual_pid = int((output / "STDOUT.txt").read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(residual_pid, 0)


def test_wrapper_requires_start_end_hash_identity() -> None:
    wrapper = Path("tools/run_c2_articulated_rejection_prefix_bounded.sh").read_text()
    assert 'cmp -s "$output/START_HASHES.txt" "$output/END_HASHES.txt"' in wrapper
    assert "HASH_INPUT_CHANGED" in wrapper
    assert "src/biospur_fusion/c2_coupled_progressive/full_session_ten_node_ab.py" in wrapper
    assert "tests/test_c2_full_session_ten_node_ab.py" in wrapper


def test_wrapper_records_explicit_event_cap_child_arguments(tmp_path: Path) -> None:
    wrapper = Path("tools/run_c2_articulated_rejection_prefix_bounded.sh").resolve()
    child = _dummy(tmp_path / "success-args", "exit 0\n")
    output = tmp_path / "event-cap-args"
    completed = subprocess.run(
        (str(wrapper), str(output), "--", str(child),
         "--completion-target", "EVENT_CAP", "--event-cap", "50000"),
        check=False,
        env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
             "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"},
    )
    assert completed.returncode == 0
    assert (output / "COMMAND.txt").read_text() == (
        f"{child} --completion-target EVENT_CAP --event-cap 50000 \n"
    )
