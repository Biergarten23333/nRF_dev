from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import itertools
import os
import random
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CAPTURE2_PROTOCOL_SLOTS,
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    ContinuousEvent,
    ImuTimer2Fields,
    NodeClockBinding,
    UwbTimer2Fields,
)
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionContinuousReader,
    FullSessionEventRouter,
    FullSessionEventTicket,
    FullSessionRecordTicket,
    FullSessionImuEventTicket,
    FullSessionUwbEventTicket,
    FullSessionRouteAudit,
    FullSessionStreamAudit,
    _OwnedEventAttestation,
    _FullSessionDeliveryOwner,
    _FullSessionRecordDeliveryOwner,
    _event_digest,
    _event_projection,
    _raw_key,
    _sensor_identity_digest,
    _structural_identity,
    _ZERO_RUN_RE,
    _completed_nonempty,
    _count_nonempty_cobs_prefix,
)
from biospur_fusion.ingest.events import (
    EventStatus,
    RawByteProvenance,
    RecordType,
    TypedEvent,
)


def _scalar_completed_nonempty(payload, pending):
    count = 0
    for value in payload:
        if value:
            pending = True
        else:
            count += int(pending)
            pending = False
    return count, pending


def test_c_backed_nonempty_counter_exhaustively_matches_scalar_oracle():
    for size in range(9):
        for values in itertools.product((0, 1, 255), repeat=size):
            payload = bytes(values)
            for pending in (False, True):
                assert _completed_nonempty(payload, pending) == (
                    _scalar_completed_nonempty(payload, pending)
                )


def test_c_backed_counter_matches_every_short_chunk_partition():
    for size in range(8):
        for values in itertools.product((0, 1), repeat=size):
            payload = bytes(values)
            for mask in range(1 << max(0, size - 1)):
                stops = tuple(
                    index for index in range(1, size)
                    if mask & (1 << (index - 1))
                ) + (size,)
                for initial in (False, True):
                    count = 0
                    pending = initial
                    start = 0
                    for stop in stops:
                        added, pending = _completed_nonempty(
                            payload[start:stop], pending,
                        )
                        count += added
                        start = stop
                    assert (count, pending) == _scalar_completed_nonempty(
                        payload, initial,
                    )


def test_c_backed_nonempty_counter_matches_random_chunk_partitions():
    rng = random.Random(0xC0B5)
    for _ in range(500):
        payload = bytes(rng.randrange(0, 4) for _ in range(rng.randrange(0, 257)))
        cuts = sorted(set(rng.randrange(0, len(payload) + 1) for _ in range(12)))
        for initial in (False, True):
            count = 0
            pending = initial
            start = 0
            for stop in (*cuts, len(payload)):
                added, pending = _completed_nonempty(payload[start:stop], pending)
                count += added
                start = stop
            assert (count, pending) == _scalar_completed_nonempty(payload, initial)


def test_prefix_counter_preserves_empty_truncated_and_chunk_boundaries():
    payload = b"\0\0a\0\0bc\0d\0\0"
    expected, pending = _scalar_completed_nonempty(payload, False)
    assert not pending
    for chunk_bytes in range(1, len(payload) + 1):
        source = io.BytesIO(payload)
        assert _count_nonempty_cobs_prefix(
            source, stop_offset=len(payload), chunk_bytes=chunk_bytes,
        ) == (expected, len(payload))
    with pytest.raises(ValueError, match="cuts a COBS record"):
        _count_nonempty_cobs_prefix(
            io.BytesIO(b"a\0tail"), stop_offset=6, chunk_bytes=2,
        )
    with pytest.raises(OSError, match="ended inside"):
        _count_nonempty_cobs_prefix(
            io.BytesIO(b"a\0"), stop_offset=3, chunk_bytes=1,
        )


def test_zero_run_work_buffer_is_bounded_and_faster_than_scalar_megabyte_scan():
    payload = (b"x" * 99 + b"\0") * ((1 << 20) // 100)
    collapsed, _runs = _ZERO_RUN_RE.subn(b"\0", payload)
    assert len(collapsed) <= len(payload) <= 1 << 20
    started = time.perf_counter()
    _scalar_completed_nonempty(payload, False)
    scalar_s = time.perf_counter() - started
    samples = []
    for _ in range(3):
        started = time.perf_counter()
        _completed_nonempty(payload, False)
        samples.append(time.perf_counter() - started)
    assert min(samples) < scalar_s


def _event(*, ordinal: int = 0, action_index: int = 0) -> ContinuousEvent:
    timer = 10_000 + ordinal * 5_000
    payload = {
        "base_timer2_us": timer - 5_000,
        "acc_raw": [0, 0, 2048],
        "gyro_raw": [0, 0, 0],
    }
    raw = RawByteProvenance(
        100 + ordinal, 200 + ordinal * 10, 210 + ordinal * 10,
        f"{ordinal + 1:064x}", 0,
    )
    record = TypedEvent(
        "BSFC2CC", 7, RecordType.IMU, ordinal & 0xFFFF, timer, timer * 1_000,
        100, 0, payload, {}, EventStatus.DECODED, raw,
    )
    return ContinuousEvent(
        event_id=f"v47:{raw.record_index}:0:{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}",
        kind="IMU", action_index=action_index,
        action_id=CAPTURE2_PROTOCOL_SLOTS[action_index].action_id,
        common_global_ns=timer * 1_000,
        availability_global_ns=timer * 1_000,
        node_id=record.node_id, boot_epoch=record.boot_epoch,
        clock_domain="B306_TIMER2", clock_mapping_digest="1" * 64,
        clock_owner_sha256="2" * 64, clock_source_sha256="3" * 64,
        host_time_label="0", payload_owner=record,
        imu_timer2=ImuTimer2Fields(timer - 5_000, timer),
    )


def _pending_router(*events: ContinuousEvent) -> FullSessionEventRouter:
    # Construct the exact router type with one synthetic bounded pending record;
    # no capture path is opened and production reader pins remain untouched.
    router = object.__new__(FullSessionEventRouter)
    router._pending_events = tuple(events)
    router._pending_event_digests = tuple(_event_digest(event) for event in events)
    router._pending_sensor_digests = tuple(
        _sensor_identity_digest(event) for event in events
    )
    router._attestation_authority = object()
    router._pending_attestations = tuple(
        _OwnedEventAttestation(
            router._attestation_authority,
            _structural_identity(_event_projection(event, sensor_only=False)),
            digest, sensor_digest,
        )
        for event, digest, sensor_digest in zip(
            events, router._pending_event_digests, router._pending_sensor_digests,
        )
    )
    router._pending_raw_identity = _raw_key(events[0].payload_owner)
    router._pending_cursor = 0
    router._delivery_authority = None
    return router


def _delivery(*events: ContinuousEvent) -> _FullSessionDeliveryOwner:
    return _FullSessionDeliveryOwner(_pending_router(*events))


def _record_events(count: int = 10) -> tuple[ContinuousEvent, ...]:
    raw = RawByteProvenance(777, 8_000, 8_400, "d" * 64, 0)
    events = []
    for sample_index in range(count):
        event = _event(ordinal=sample_index)
        sample_raw = replace(raw, sample_index=sample_index)
        record = replace(event.payload_owner, raw=sample_raw)
        events.append(replace(
            event,
            event_id=(f"v47:{raw.record_index}:{sample_index}:"
                      f"{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}"),
            payload_owner=record,
        ))
    return tuple(events)


def _uwb_event(*, ordinal: int = 20, action_index: int = 0) -> ContinuousEvent:
    timer = 100_000 + ordinal * 10_000
    raw = RawByteProvenance(
        100 + ordinal, 2_000 + ordinal * 100, 2_080 + ordinal * 100,
        f"{ordinal + 1:064x}", 0,
    )
    payload = {
        "packet_sequence": ordinal & 0xFFFF, "sweep": ordinal,
        "strobe_us": timer, "frame_us": timer + 1_000,
        "anchor_id": list(range(8)), "range_mm": [2_000] * 8,
        "t_round_us": [100] * 8, "quality_percent": [100] * 8,
        "valid_mask": 0xFF, "identity": 1, "node_ms": 1,
    }
    record = TypedEvent(
        "BSFC2CC", 7, RecordType.UWB, ordinal & 0xFFFF, timer,
        timer * 1_000, 100, 0, payload, {}, EventStatus.DECODED, raw,
    )
    return ContinuousEvent(
        f"v47:{raw.record_index}:0:{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}",
        "UWB", action_index, CAPTURE2_PROTOCOL_SLOTS[action_index].action_id,
        timer * 1_000, (timer + 1_000) * 1_000, record.node_id,
        record.boot_epoch, "B306_TIMER2", "1" * 64, "2" * 64,
        "3" * 64, "label", record,
        uwb_timer2=UwbTimer2Fields(timer, timer + 1_000),
    )


def test_tickets_deliver_in_exact_order_and_hide_event_until_validation():
    first, second = _event(ordinal=0), _event(ordinal=1)
    owner = _delivery(first, second)
    seen = []
    first_ticket = owner.issue(first)
    assert not hasattr(first_ticket, "event")
    first_ticket.deliver(lambda event: seen.append(event.event_id))
    second_ticket = owner.issue(second)
    assert (first_ticket.stream_ordinal, second_ticket.stream_ordinal) == (0, 1)
    second_ticket.deliver(lambda event: seen.append(event.event_id))
    assert seen == [first.event_id, second.event_id]


def test_record_ticket_delivers_ten_imu_events_once_without_child_tickets(
    monkeypatch: pytest.MonkeyPatch,
):
    import biospur_fusion.c2_coupled_progressive.continuous_full_session_reader as module

    events = _record_events()
    router = _pending_router(*events)
    owner = _FullSessionRecordDeliveryOwner(router)
    monkeypatch.setattr(module.FullSessionEventTicket, "__init__",
                        lambda *_args, **_kwargs: pytest.fail("event ticket created"))
    monkeypatch.setattr(module.FullSessionImuEventTicket, "__init__",
                        lambda *_args, **_kwargs: pytest.fail("IMU child created"))
    ticket = owner.issue()
    assert type(ticket) is FullSessionRecordTicket
    assert not hasattr(ticket, "event") and not hasattr(ticket, "events")
    assert ticket.raw_identity == _raw_key(events[0].payload_owner)
    assert ticket.event_digests == tuple(_event_digest(event) for event in events)
    seen = []
    ticket.deliver(lambda batch: seen.append(tuple(event.event_id for event in batch)))
    owner.require_consumed()
    assert seen == [tuple(event.event_id for event in events)]
    assert router._pending_events == ()
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        ticket.deliver(lambda _batch: None)


def test_record_ticket_uwb_mutation_foreign_retention_and_callback_are_fail_closed():
    event = _uwb_event()
    router = _pending_router(event)
    owner = _FullSessionRecordDeliveryOwner(router)
    retained = owner.issue()
    with pytest.raises(RuntimeError, match="did not deliver"):
        owner.require_consumed()
    with pytest.raises(RuntimeError, match="previous.*not consumed"):
        owner.issue()
    with pytest.raises(RuntimeError, match="already has a delivery owner"):
        _FullSessionRecordDeliveryOwner(router)
    before = (router._pending_events, router._pending_attestations, router._pending_cursor)
    retained.event_digests = ("0" * 64,)
    called = []
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        retained.deliver(lambda batch: called.append(batch))
    assert called == []
    assert (router._pending_events, router._pending_attestations,
            router._pending_cursor) == before

    mutated_events = _record_events(2)
    mutated_router = _pending_router(*mutated_events)
    mutated_owner = _FullSessionRecordDeliveryOwner(mutated_router)
    mutated_ticket = mutated_owner.issue()
    mutated_before = (
        mutated_router._pending_events, mutated_router._pending_attestations,
        mutated_router._pending_cursor,
    )
    mutated_events[1].payload_owner.payload["acc_raw"][0] = 99
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        mutated_ticket.deliver(lambda batch: called.append(batch))
    assert called == []
    assert (mutated_router._pending_events, mutated_router._pending_attestations,
            mutated_router._pending_cursor) == mutated_before

    before_issue_events = _record_events(2)
    before_issue_router = _pending_router(*before_issue_events)
    before_issue_owner = _FullSessionRecordDeliveryOwner(before_issue_router)
    before_issue_events[0].payload_owner.payload["gyro_raw"][1] = 77
    before_issue_ticket = before_issue_owner.issue()
    before_issue_state = (
        before_issue_router._pending_events, before_issue_router._pending_attestations,
        before_issue_router._pending_cursor,
    )
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        before_issue_ticket.deliver(lambda batch: called.append(batch))
    assert called == []
    assert (before_issue_router._pending_events,
            before_issue_router._pending_attestations,
            before_issue_router._pending_cursor) == before_issue_state

    equal_event = _uwb_event(ordinal=22)
    equal_router = _pending_router(equal_event)
    equal_owner = _FullSessionRecordDeliveryOwner(equal_router)
    equal_ticket = equal_owner.issue()
    original_attestations = equal_router._pending_attestations
    equal_router._pending_attestations = tuple([*original_attestations])
    assert equal_router._pending_attestations == original_attestations
    assert equal_router._pending_attestations is not original_attestations
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        equal_ticket.deliver(lambda batch: called.append(batch))
    assert called == []

    pristine = _uwb_event(ordinal=21)
    owner = _FullSessionRecordDeliveryOwner(_pending_router(pristine))
    valid = owner.issue()
    foreign = FullSessionRecordTicket(
        valid.record_ordinal, valid.raw_identity, valid.event_digests,
        valid.sensor_identity_digests, object(), owner,
    )
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        foreign.deliver(lambda batch: called.append(batch))
    with pytest.raises(ValueError, match="batch callback"):
        valid.deliver(lambda _batch: (_ for _ in ()).throw(
            ValueError("batch callback")))
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        valid.deliver(lambda _batch: None)


def test_authenticated_dispatch_issues_only_hidden_one_shot_typed_children():
    imu, uwb = _event(ordinal=10), _uwb_event(ordinal=11)
    owner = _delivery(imu, uwb)
    imu_child = owner.issue(imu).dispatch()
    assert type(imu_child) is FullSessionImuEventTicket
    assert not hasattr(imu_child, "event")
    seen = []
    imu_child.deliver(lambda event: seen.append((event.kind, event.event_id)))
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        imu_child.deliver(lambda _event: None)
    uwb_child = owner.issue(uwb).dispatch()
    assert type(uwb_child) is FullSessionUwbEventTicket
    uwb_child.deliver(lambda event: seen.append((event.kind, event.event_id)))
    assert seen == [("IMU", imu.event_id), ("UWB", uwb.event_id)]


def test_typed_child_mutation_forgery_undelivered_and_callback_failure_fail_closed():
    event = _event(ordinal=12)
    owner = _delivery(event)
    child = owner.issue(event).dispatch()
    event.payload_owner.payload["acc_raw"][0] = 99
    called = []
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        child.deliver(lambda value: called.append(value))
    assert called == []

    pristine = _event(ordinal=13)
    owner = _delivery(pristine)
    child = owner.issue(pristine).dispatch()
    with pytest.raises(RuntimeError, match="previous.*not consumed"):
        owner.issue(_event(ordinal=14))
    forged = FullSessionImuEventTicket(
        child.stream_ordinal, pristine, child.event_digest,
        child.sensor_identity_digest, object(), owner,
    )
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        forged.deliver(lambda value: called.append(value))
    with pytest.raises(ValueError, match="typed downstream"):
        child.deliver(lambda _event: (_ for _ in ()).throw(
            ValueError("typed downstream")))
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        child.deliver(lambda _event: None)


def test_label_permutation_preserves_typed_route_and_sensor_identity():
    original = _event(ordinal=15, action_index=0)
    relabeled = replace(
        original, action_index=2,
        action_id=CAPTURE2_PROTOCOL_SLOTS[2].action_id,
    )
    children = [
        _delivery(event).issue(event).dispatch()
        for event in (original, relabeled)
    ]
    assert all(type(child) is FullSessionImuEventTicket for child in children)
    assert children[0].sensor_identity_digest == children[1].sensor_identity_digest


def test_mutated_foreign_and_replayed_tickets_reject_before_callback():
    event = _event()
    owner = _delivery(event)
    ticket = owner.issue(event)
    called = []
    event.payload_owner.payload["acc_raw"][0] = 1
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        ticket.deliver(lambda value: called.append(value))
    assert called == []

    pristine = _event(ordinal=2)
    owner = _delivery(pristine)
    valid = owner.issue(pristine)
    forged = FullSessionEventTicket(
        valid.stream_ordinal, pristine, valid.event_digest,
        valid.sensor_identity_digest, object(), owner,
    )
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        forged.deliver(lambda value: called.append(value))
    valid.deliver(lambda value: called.append(value.event_id))
    before = tuple(called)
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        valid.deliver(lambda value: called.append(value))
    assert tuple(called) == before


def test_callback_failure_consumes_ticket_and_cannot_be_replayed():
    event = _event(ordinal=3)
    owner = _delivery(event)
    ticket = owner.issue(event)
    with pytest.raises(ValueError, match="downstream"):
        ticket.deliver(lambda _event: (_ for _ in ()).throw(ValueError("downstream")))
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        ticket.deliver(lambda _event: None)


def test_retained_mutated_and_second_owner_tickets_fail_closed_without_history():
    first, second = _event(ordinal=30), _event(ordinal=31)
    router = _pending_router(first, second)
    owner = _FullSessionDeliveryOwner(router)
    retained = owner.issue(first)
    with pytest.raises(RuntimeError, match="consumer did not deliver"):
        owner.require_consumed()
    with pytest.raises(RuntimeError, match="previous.*not consumed"):
        owner.issue(second)
    with pytest.raises(RuntimeError, match="already has a delivery owner"):
        _FullSessionDeliveryOwner(router)
    retained.deliver(lambda _event: None)
    owner.issue(second).deliver(lambda _event: None)
    owner.require_record_consumed()

    mutable_event = _event(ordinal=32)
    mutated_owner = _delivery(mutable_event)
    mutated = mutated_owner.issue(mutable_event)
    mutated.event_digest = "0" * 64
    called = []
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        mutated.deliver(lambda event: called.append(event))
    assert called == []


def test_action_labels_are_excluded_from_sensor_identity_only():
    action0 = _event(ordinal=4, action_index=0)
    action2 = replace(
        action0, action_index=2,
        action_id=CAPTURE2_PROTOCOL_SLOTS[2].action_id,
    )
    assert action0.event_id == action2.event_id
    assert _sensor_identity_digest(action0) == _sensor_identity_digest(action2)
    assert _event_digest(action0) != _event_digest(action2)


def test_golden_imu_uwb_digests_and_availability_sensitivity_are_unchanged():
    imu = _event(ordinal=0)
    uwb = _uwb_event(ordinal=20)
    assert (_event_digest(imu), _sensor_identity_digest(imu)) == (
        "a22ec70895f57f3a8c028687223403cd86fcd515426fa9c21e52ee0c3f41ca0b",
        "aede42bd2886f0bfffce6769f4065cf29a8a01bad6392839ef93eed92b00653a",
    )
    assert (_event_digest(uwb), _sensor_identity_digest(uwb)) == (
        "0811cac6d8204862f2db3232c24b3630b71cebc6069a86f90000a494d6812166",
        "e00919280790000842d6cddbf7b3c8d5b2d6070e378a75b89aa9fa7ea40d7427",
    )
    later = replace(imu, availability_global_ns=imu.availability_global_ns + 1)
    assert _event_digest(later) != _event_digest(imu)
    assert _sensor_identity_digest(later) != _sensor_identity_digest(imu)


def test_nested_mutation_before_issue_and_before_dispatch_preserves_attestation():
    before_issue = _event(ordinal=40)
    router = _pending_router(before_issue)
    owner = _FullSessionDeliveryOwner(router)
    private_before = (router._pending_attestations, router._pending_cursor)
    before_issue.payload_owner.payload["acc_raw"][1] = 7
    with pytest.raises(ValueError, match="foreign or mutated"):
        owner.issue(before_issue)
    assert (router._pending_attestations, router._pending_cursor) == private_before

    before_dispatch = _event(ordinal=41)
    owner = _delivery(before_dispatch)
    ticket = owner.issue(before_dispatch)
    before_dispatch.payload_owner.payload["gyro_raw"][2] = 8
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        ticket.dispatch()


def test_signed_zero_nested_mutation_rejects_direct_and_typed_before_callback_or_state():
    direct_event = _event(ordinal=42)
    direct_event.payload_owner.payload["gyro_raw"][0] = 0.0
    direct_router = _pending_router(direct_event)
    direct_owner = _FullSessionDeliveryOwner(direct_router)
    direct_ticket = direct_owner.issue(direct_event)
    direct_before = (direct_router._pending_attestations, direct_router._pending_cursor)
    direct_event.payload_owner.payload["gyro_raw"][0] = -0.0
    called = []
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        direct_ticket.deliver(lambda event: called.append(event))
    assert called == []
    assert (direct_router._pending_attestations, direct_router._pending_cursor) == direct_before

    typed_event = _event(ordinal=43)
    typed_event.payload_owner.payload["gyro_raw"][0] = 0.0
    typed_router = _pending_router(typed_event)
    typed_owner = _FullSessionDeliveryOwner(typed_router)
    child = typed_owner.issue(typed_event).dispatch()
    typed_before = (typed_router._pending_attestations, typed_router._pending_cursor)
    typed_event.payload_owner.payload["gyro_raw"][0] = -0.0
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        child.deliver(lambda event: called.append(event))
    assert called == []
    assert (typed_router._pending_attestations, typed_router._pending_cursor) == typed_before


def test_reader_consume_is_bounded_one_shot_and_closes_on_success_or_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import biospur_fusion.c2_coupled_progressive.continuous_full_session_reader as module

    prefix = b"x\0"
    window = b"abc\0"
    source_path = tmp_path / module.RAW_RELATIVE
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(prefix + window + b"FORBIDDEN")
    region = SimpleNamespace(
        ordinal=0, region_id="WHOLE_SESSION", start_offset=len(prefix),
        stop_offset=len(prefix) + len(window), start_ns=0, stop_ns=10**9,
    )
    inventory = SimpleNamespace(
        regions=(region,), start_offset=region.start_offset,
        stop_offset=region.stop_offset,
    )
    event = _event()

    class FakeWindow:
        def __init__(self, *_args, **_kwargs):
            pass

    class FakeDecoder:
        def __init__(self, *_args, **_kwargs):
            self.emitted = False

        def feed(self, _block, *, absolute_offset):
            assert absolute_offset == region.start_offset
            if self.emitted:
                return ()
            self.emitted = True
            return (event.payload_owner,)

        def finish(self):
            return None

    class FakeRouter:
        def __init__(self, received_inventory, received_clock):
            assert received_inventory is inventory
            self.clock = received_clock
            self.digest = _event_digest(event)
            self.sensor_digest = _sensor_identity_digest(event)
            self._pending_events = ()
            self._pending_event_digests = ()
            self._pending_sensor_digests = ()
            self._pending_attestations = ()
            self._pending_raw_identity = None
            self._pending_cursor = 0
            self._delivery_authority = None
            self._attestation_authority = object()

        def _bind_delivery(self, authority):
            assert self._delivery_authority is None
            self._delivery_authority = authority

        def route_original_record(self, rows):
            assert rows == (event.payload_owner,)
            self._pending_events = (event,)
            self._pending_event_digests = (self.digest,)
            self._pending_sensor_digests = (self.sensor_digest,)
            self._pending_attestations = (_OwnedEventAttestation(
                self._attestation_authority,
                _structural_identity(_event_projection(event, sensor_only=False)),
                self.digest, self.sensor_digest,
            ),)
            self._pending_raw_identity = _raw_key(event.payload_owner)
            return (event,)

        def _owned_record_descriptor(self, authority):
            if authority is not self._delivery_authority:
                raise ValueError("foreign synthetic record")
            return (self._pending_raw_identity, self._pending_event_digests,
                    self._pending_sensor_digests, self._pending_attestations)

        def validate_owned_event(self, candidate, authority=None):
            if (authority is not self._delivery_authority or candidate is not event
                    or _structural_identity(_event_projection(
                        candidate, sensor_only=False,
                    )) != self._pending_attestations[0].structural_identity):
                raise ValueError("foreign synthetic event")
            return self._pending_attestations[0]

        def _consume_owned_event(self, candidate, attestation, authority):
            if (authority is not self._delivery_authority or candidate is not event
                    or attestation is not self._pending_attestations[0]):
                raise RuntimeError("synthetic pending ownership changed")
            self._pending_events = ()
            self._pending_event_digests = ()
            self._pending_sensor_digests = ()
            self._pending_attestations = ()
            self._pending_raw_identity = None
            self._pending_cursor = 0

        def validate_owned_record(self, authority=None):
            if authority is not self._delivery_authority or not self._pending_events:
                raise ValueError("foreign synthetic record")
            return self._pending_events, self._pending_attestations

        def _consume_owned_record(self, attestations, authority):
            if (authority is not self._delivery_authority
                    or attestations is not self._pending_attestations):
                raise RuntimeError("synthetic pending ownership changed")
            self._pending_events = ()
            self._pending_event_digests = ()
            self._pending_sensor_digests = ()
            self._pending_attestations = ()
            self._pending_raw_identity = None
            self._pending_cursor = 0

        def finish(self):
            return FullSessionRouteAudit(
                1, {}, {}, 0, 0, 1_000_000, 1_000_000, 0, 0, 0,
                1, 1, "4" * 64,
            )

    clock = ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(
        NodeClockBinding(
            "BSFC2CC" if index == 0 else f"BSF{index:04X}", 7,
            "B306_TIMER2", f"{index + 1:064x}", 1_000.0, 0.0,
            "2" * 64, "3" * 64,
        )
        for index in range(10)
    ))
    monkeypatch.setattr(module, "load_continuous_session_inventory", lambda _root: inventory)
    monkeypatch.setattr(module.FullSessionContinuousReader,
                        "_validate_capture_source_identity", lambda _self: None)
    monkeypatch.setattr(module.FullSessionContinuousReader,
                        "_load_hash_evidence", lambda _self: ("5" * 64,))
    monkeypatch.setattr(module, "continuous_clock_owner_digest", lambda _owner: "6" * 64)
    monkeypatch.setattr(module, "AuthorizedByteWindow", FakeWindow)
    monkeypatch.setattr(module, "IncrementalV47WindowDecoder", FakeDecoder)
    monkeypatch.setattr(module, "FullSessionEventRouter", FakeRouter)
    monkeypatch.setattr(module, "FULL_WINDOW_SHA256", hashlib.sha256(window).hexdigest())
    monkeypatch.setattr(module.FullSessionContinuousReader, "_stat",
                        staticmethod(lambda _value: module.SOURCE_STAT_IDENTITY))

    real_open = os.open
    base_fd = real_open(source_path, os.O_RDONLY)
    opened = []

    def fake_open(path, flags, *args, **kwargs):
        if Path(path) == source_path:
            fd = os.dup(base_fd)
            opened.append(fd)
            return fd
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", fake_open)
    try:
        reader = FullSessionContinuousReader(root=tmp_path, clock_owner=clock)
        delivered = []
        audit = reader.consume(
            lambda ticket: ticket.deliver(lambda value: delivered.append(value.event_id))
        )
        assert isinstance(audit, FullSessionStreamAudit)
        assert not hasattr(audit, "events")
        assert delivered == [event.event_id]
        assert audit.access_audit["routed_events"] == 1
        assert audit.access_audit["bytes_after_session_read"] == 0
        assert os.lseek(base_fd, 0, os.SEEK_CUR) == inventory.stop_offset
        with pytest.raises(OSError):
            os.fstat(opened[-1])
        with pytest.raises(RuntimeError, match="one-shot"):
            reader.consume(lambda _ticket: None)

        os.lseek(base_fd, 0, os.SEEK_SET)
        batch_reader = FullSessionContinuousReader(root=tmp_path, clock_owner=clock)
        delivered_batches = []
        batch_audit = batch_reader.consume_record_batches(
            lambda ticket: ticket.deliver(lambda batch: delivered_batches.append(
                tuple(value.event_id for value in batch)))
        )
        assert delivered_batches == [(event.event_id,)]
        assert batch_audit == audit

        os.lseek(base_fd, 0, os.SEEK_SET)
        failed = FullSessionContinuousReader(root=tmp_path, clock_owner=clock)
        with pytest.raises(ValueError, match="callback failed"):
            failed.consume(lambda ticket: ticket.deliver(
                lambda _value: (_ for _ in ()).throw(ValueError("callback failed"))))
        with pytest.raises(OSError):
            os.fstat(opened[-1])
        assert os.lseek(base_fd, 0, os.SEEK_CUR) == inventory.stop_offset
        with pytest.raises(RuntimeError, match="one-shot"):
            failed.consume(lambda _ticket: None)
    finally:
        os.close(base_fd)
