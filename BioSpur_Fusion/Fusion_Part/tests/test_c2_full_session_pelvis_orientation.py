from __future__ import annotations

from dataclasses import replace
import ast
from pathlib import Path

import numpy as np
import pytest

import biospur_fusion.c2_uwb_root_world.full_session_pelvis_orientation as module
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
    FullSessionEventRouter,
    _FullSessionDeliveryOwner,
    _event_digest,
    _sensor_identity_digest,
)
from biospur_fusion.ingest.events import (
    EventStatus,
    RawByteProvenance,
    RecordType,
    TypedEvent,
)


ALL_NODES = (
    "BSFEC35", "BSFB165", "BSFAA61", "BSF1120", "BSF31CC",
    "BSFC2CC", "BSF44AD", "BSF3C79", "BSF6C53", "BSF8BC4",
)


def _clock(*, mapping="1" * 64, nodes=(module.PELVIS_NODE,)) -> ContinuousClockOwner:
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(
        NodeClockBinding(
            node, 7, "B306_TIMER2", mapping,
            1_000.0, 10_000.0, "2" * 64, "3" * 64,
        ) for node in nodes
    ))


def _event(index: int, clock: ContinuousClockOwner, *, sequence: int | None = None,
           timer_jump_us: int = 0, action_index: int = 0,
           node: str = module.PELVIS_NODE) -> ContinuousEvent:
    binding = clock.binding_for(node)
    timer = 1_000_000 + index * 5_000 + timer_jump_us
    raw = RawByteProvenance(
        index + 1, 100 + index * 10, 108 + index * 10,
        f"{index + 1:064x}", 0,
    )
    record = TypedEvent(
        node, binding.boot_epoch, RecordType.IMU,
        index & 0xFFFF if sequence is None else sequence,
        timer, binding.global_ns(timer), 100, 0,
        {"base_timer2_us": timer - 5_000, "delta_us": 5_000,
         "acc_raw": [2048, 0, 0], "gyro_raw": [0, 0, 0]},
        {}, EventStatus.DECODED, raw,
    )
    return ContinuousEvent(
        f"v47:{raw.record_index}:0:{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}",
        "IMU", action_index, CAPTURE2_PROTOCOL_SLOTS[action_index].action_id,
        binding.global_ns(timer), binding.global_ns(timer), node,
        binding.boot_epoch, binding.clock_domain, binding.clock_mapping_digest,
        binding.clock_owner_sha256, binding.clock_source_sha256, "report-label",
        record, imu_timer2=ImuTimer2Fields(timer - 5_000, timer),
    )


def _child(event: ContinuousEvent):
    router = object.__new__(FullSessionEventRouter)
    router._pending_events = (event,)
    router._pending_event_digests = (_event_digest(event),)
    router._pending_sensor_digests = (_sensor_identity_digest(event),)
    router._pending_cursor = 0
    router._delivery_authority = None
    return _FullSessionDeliveryOwner(router).issue(event).dispatch()


def _feed(owner, events):
    return tuple(owner.ingest_ticket(_child(event)) for event in events)


def test_first_100_are_preparation_only_and_101st_is_first_publication():
    clock = _clock()
    owner = module.FullSessionPelvisOrientationOwner(clock)
    frames = _feed(owner, tuple(_event(index, clock) for index in range(101)))
    assert all(frame is None for frame in frames[:100])
    assert frames[100] is not None
    assert frames[100].source_sequence == 100
    assert not frames[100].product_ready and not frames[100].scientific_pass
    assert owner.audit()["preparation_backfilled"] is False
    assert owner.audit()["labels_used"] is False
    assert owner.audit()["full_body_pose_or_fk_issued"] is False


def test_full_session_owner_import_closure_forbids_action_plan_modules():
    source_path = Path(module.__file__)
    tree = ast.parse(source_path.read_text())
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any("diagnostic_pelvis_orientation" in name for name in imports)
    assert not any("action00_gap02" in name for name in imports)


def test_sequence_is_identity_only_and_repeat_plus_uint16_wrap_are_allowed():
    clock = _clock()
    owner = module.FullSessionPelvisOrientationOwner(clock)
    events = tuple(
        _event(index, clock, sequence=sequence)
        for index, sequence in enumerate([65535, 0, 0, 65535, 1])
    )
    _feed(owner, events)
    assert owner.audit()["accepted_pelvis_imu"] == len(events)


def test_real_timer_gap_is_recorded_without_fabrication_or_reset():
    clock = _clock()
    owner = module.FullSessionPelvisOrientationOwner(clock)
    owner.ingest_ticket(_child(_event(0, clock)))
    owner.ingest_ticket(_child(_event(1, clock, timer_jump_us=5_000)))
    assert owner.audit()["timer_gap_count"] == 1
    assert owner.audit()["accepted_pelvis_imu"] == 2


def test_wrong_typed_child_rejects_without_consuming_or_mutating_owner():
    clock = _clock()
    owner = module.FullSessionPelvisOrientationOwner(clock)
    imu = _event(0, clock)
    timer = imu.payload_owner.node_timer_us
    record = replace(imu.payload_owner, record_type=RecordType.UWB, payload={})
    uwb = replace(
        imu, kind="UWB", payload_owner=record, imu_timer2=None,
        uwb_timer2=UwbTimer2Fields(timer, timer),
    )
    child = _child(uwb)
    before = owner.owner_bytes()
    with pytest.raises(TypeError, match="authenticated IMU ticket"):
        owner.ingest_ticket(child)
    assert owner.owner_bytes() == before
    seen = []
    child.deliver(lambda event: seen.append(event.event_id))
    assert seen == [uwb.event_id]


def test_foreign_clock_replay_and_mutation_are_byte_inert():
    source_clock = _clock()
    foreign_owner = module.FullSessionPelvisOrientationOwner(_clock(mapping="4" * 64))
    event = _event(0, source_clock)
    before = foreign_owner.owner_bytes()
    with pytest.raises(ValueError, match="owner mismatch"):
        foreign_owner.ingest_ticket(_child(event))
    assert foreign_owner.owner_bytes() == before

    owner = module.FullSessionPelvisOrientationOwner(source_clock)
    ticket = _child(event)
    assert owner.ingest_ticket(ticket) is None
    committed = owner.owner_bytes()
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        owner.ingest_ticket(ticket)
    assert owner.owner_bytes() == committed

    poisoned = _event(1, source_clock)
    child = _child(poisoned)
    poisoned.payload_owner.payload["acc_raw"][0] = 99
    before = owner.owner_bytes()
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        owner.ingest_ticket(child)
    assert owner.owner_bytes() == before


def test_all_nine_authenticated_nonpelvis_nodes_are_audited_inert_and_replay_safe():
    clock = _clock(nodes=ALL_NODES)
    owner = module.FullSessionPelvisOrientationOwner(clock)
    nodes = tuple(node for node in ALL_NODES if node != module.PELVIS_NODE)
    events = tuple(_event(index, clock, node=node) for index, node in enumerate(nodes))
    before = owner.owner_bytes()
    outcomes = _feed(owner, events)
    assert tuple(outcome.node_id for outcome in outcomes) == nodes
    assert all(type(outcome) is module.AuditedFullSessionNonPelvisImu
               for outcome in outcomes)
    assert all(outcome.reason == module.NON_PELVIS_IMU_REASON for outcome in outcomes)
    assert owner.owner_bytes() == before

    ticket = _child(_event(20, clock, node=nodes[0]))
    outcome = owner.ingest_ticket(ticket)
    assert type(outcome) is module.AuditedFullSessionNonPelvisImu
    assert owner.owner_bytes() == before
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        owner.ingest_ticket(ticket)
    assert owner.owner_bytes() == before

    pelvis = owner.ingest_ticket(_child(_event(21, clock)))
    assert pelvis is None
    assert owner.audit()["accepted_pelvis_imu"] == 1


def test_nonpelvis_integrity_failure_still_raises_without_orientation_mutation():
    clock = _clock(nodes=ALL_NODES)
    owner = module.FullSessionPelvisOrientationOwner(clock)
    node = next(node for node in ALL_NODES if node != module.PELVIS_NODE)
    event = replace(_event(0, clock, node=node), clock_mapping_digest="4" * 64)
    before = owner.owner_bytes()
    with pytest.raises(ValueError, match="owner mismatch"):
        owner.ingest_ticket(_child(event))
    assert owner.owner_bytes() == before


def test_nonpelvis_action_label_permutation_preserves_audit_identity():
    clock = _clock(nodes=ALL_NODES)
    node = next(node for node in ALL_NODES if node != module.PELVIS_NODE)
    event = _event(0, clock, node=node)
    relabeled = replace(
        event, action_index=2,
        action_id=CAPTURE2_PROTOCOL_SLOTS[2].action_id,
        host_time_label="different-report-label",
    )
    first = module.FullSessionPelvisOrientationOwner(clock).ingest_ticket(_child(event))
    second = module.FullSessionPelvisOrientationOwner(clock).ingest_ticket(_child(relabeled))
    assert first == second


def test_post_step_frame_failure_rolls_back_and_retry_matches_pristine(monkeypatch):
    clock = _clock()
    failed = module.FullSessionPelvisOrientationOwner(clock)
    control = module.FullSessionPelvisOrientationOwner(clock)
    events = tuple(_event(index, clock) for index in range(101))
    _feed(failed, events[:100])
    _feed(control, events[:100])
    before = failed.owner_bytes()
    original = module.FullSessionPelvisOrientationFrame

    def reject_frame(*_args, **_kwargs):
        raise RuntimeError("injected post-step frame failure")

    monkeypatch.setattr(module, "FullSessionPelvisOrientationFrame", reject_frame)
    with pytest.raises(RuntimeError, match="injected post-step"):
        failed.ingest_ticket(_child(events[100]))
    assert failed.owner_bytes() == before
    monkeypatch.setattr(module, "FullSessionPelvisOrientationFrame", original)
    recovered = failed.ingest_ticket(_child(events[100]))
    reference = control.ingest_ticket(_child(events[100]))
    assert recovered.digest == reference.digest
    assert failed.owner_bytes() == control.owner_bytes()


def test_prefix_chunking_and_action_label_permutation_are_digest_invariant():
    clock = _clock()
    events = tuple(_event(index, clock) for index in range(104))
    whole = module.FullSessionPelvisOrientationOwner(clock)
    chunked = module.FullSessionPelvisOrientationOwner(clock)
    relabeled = module.FullSessionPelvisOrientationOwner(clock)
    whole_frames = tuple(filter(None, _feed(whole, events)))
    chunks = []
    for group in (events[:31], events[31:77], events[77:]):
        chunks.extend(filter(None, _feed(chunked, group)))
    permuted = tuple(replace(
        event, action_index=2,
        action_id=CAPTURE2_PROTOCOL_SLOTS[2].action_id,
        host_time_label="different-report-label",
    ) for event in events)
    relabeled_frames = tuple(filter(None, _feed(relabeled, permuted)))
    assert tuple(frame.digest for frame in whole_frames) == tuple(frame.digest for frame in chunks)
    assert tuple(frame.digest for frame in whole_frames) == tuple(
        frame.digest for frame in relabeled_frames
    )
    assert whole.owner_bytes() == chunked.owner_bytes() == relabeled.owner_bytes()
