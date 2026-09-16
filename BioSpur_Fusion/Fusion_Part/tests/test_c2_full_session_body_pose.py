from __future__ import annotations

from dataclasses import replace
import pickle
from types import SimpleNamespace

import numpy as np
import pytest

import biospur_fusion.c2_uwb_root_world.full_session_body_pose as module
from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_articulated_biomechanics.model import HingeJoint
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    ContinuousEvent,
    ImuTimer2Fields,
    NodeClockBinding,
)
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionEventRouter,
    _OwnedEventAttestation,
    _FullSessionDeliveryOwner,
    _event_digest,
    _event_structural_identity,
    _sensor_identity_digest,
)
from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
from biospur_fusion.c2_coupled_progressive.native200_publication_producer import (
    Native200PublicationProducer,
)
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS, corrected_proxy_points
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.ingest.events import EventStatus, RawByteProvenance, RecordType, TypedEvent
from biospur_fusion.root_r3 import ImuSample
from test_c2_native200_publication_producer import (
    _event as _producer_record,
    _fixture as _authentic_producer_fixture,
)


def _clock() -> ContinuousClockOwner:
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(
        NodeClockBinding(node, 7, "B306_TIMER2", f"{index + 1:064x}",
                         1_000.0, 10_000.0, "a" * 64, "b" * 64)
        for index, node in enumerate(NODE_TO_SEGMENT)
    ))


def _event(node: str, index: int, clock: ContinuousClockOwner, *, label="ignored"):
    timer = 1_000_000 + index * 5_000
    binding = clock.binding_for(node)
    raw = RawByteProvenance(index + 1, 100 + index * 20, 110 + index * 20,
                            f"{index + 100:064x}", 0)
    record = TypedEvent(
        node, 7, RecordType.IMU, index & 0xFFFF, timer,
        binding.global_ns(timer), 100, 0,
        {"base_timer2_us": timer - 5_000, "delta_us": 5_000,
         "acc_raw": [2048, 0, 0], "gyro_raw": [0, 0, 0]},
        {}, EventStatus.DECODED, raw,
    )
    return ContinuousEvent(
        f"v47:{raw.record_index}:0:{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}",
        "IMU", -1, module.SESSION_ID, binding.global_ns(timer), binding.global_ns(timer),
        node, 7, "B306_TIMER2", binding.clock_mapping_digest,
        binding.clock_owner_sha256, binding.clock_source_sha256, label, record,
        imu_timer2=ImuTimer2Fields(timer - 5_000, timer),
        region_id=module.SESSION_ID,
    )


def _ticket(event):
    router = object.__new__(FullSessionEventRouter)
    authority = object()
    router._pending_events = (event,)
    router._pending_event_digests = (_event_digest(event),)
    router._pending_sensor_digests = (_sensor_identity_digest(event),)
    router._attestation_authority = authority
    router._pending_attestations = (_OwnedEventAttestation(
        authority, _event_structural_identity(event),
        router._pending_event_digests[0], router._pending_sensor_digests[0],
    ),)
    router._pending_cursor = 0
    router._delivery_authority = None
    return _FullSessionDeliveryOwner(router).issue(event).dispatch()


def _hinges():
    specs = {
        "elbow_left": ("upper_arm_left", "forearm_left"),
        "elbow_right": ("upper_arm_right", "forearm_right"),
        "knee_left": ("thigh_left", "shank_left"),
        "knee_right": ("thigh_right", "shank_right"),
    }
    return {
        name: HingeJoint(name, parent, child, "audit-only", (1.0, 0.0, 0.0),
                         (1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), 1.0,
                         0.0, 150.0, 10, 10)
        for name, (parent, child) in specs.items()
    }


def _producer():
    producer = object.__new__(Native200PublicationProducer)
    producer.publication_owner_sha256 = "c" * 64
    return producer


def _owned_state_bytes(owner):
    """Exact bounded mutable state, excluding immutable collaborators."""
    return pickle.dumps((
        tuple((node, owner._blocks[node].obj.state) for node in sorted(owner._blocks)),
        owner._latest_vqf, owner._segment_from_vqf_right,
        owner._anchor_normals_segment, owner._pelvis_sensor_from_vqf_right,
        tuple((node, tuple(owner._orientation_history[node]))
              for node in sorted(owner._orientation_history)),
        owner._first_orientation_ns, tuple(owner._pending_pelvis),
        owner._last_timer, owner._last_availability, owner._last_offsets,
        owner._last_pelvis_ns, owner._imu_events, owner._pelvis_publications,
        owner._anchor_publications, owner._gap_publications,
        owner._precoverage_pelvis_omissions, owner._preanchor_pelvis_omissions,
        owner._revision,
        tuple((name, getattr(owner._producer, name)) for name in
              ("_revision", "_last_ns", "_rank")
              if hasattr(owner._producer, name)),
    ), protocol=5)


def _initial_events(clock):
    nodes = [node for node in NODE_TO_SEGMENT if node != module.PELVIS_NODE]
    nodes.append(module.PELVIS_NODE)
    return [_event(node, index, clock) for index, node in enumerate(nodes)]


def _anchor(event, geometry):
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    points = corrected_proxy_points(
        rotations, {segment: np.zeros(3) for segment in SEGMENTS}, geometry,
    )
    offsets = {node: points[name] for node, name in NODE_TO_PROXY_POINT.items()}
    return SimpleNamespace(
        base_rotations_world=rotations, offsets_world_m=offsets,
        offset_velocities_world_mps={node: np.zeros(3) for node in offsets},
        normals_world={node: np.array([0.0, 0.0, 1.0]) for node in offsets},
        joints_relative_world_m=points, point_constraints_world_m={},
        imu_sample=ImuSample(event.common_global_ns * 1e-9,
                             event.availability_global_ns * 1e-9,
                             np.array([9.80665, 0.0, 0.0]), np.eye(3),
                             event.payload_owner.sequence),
        contact_owner_digest="d" * 64,
    )


def test_one_owner_consumes_all_ten_imus_and_rewraps_exact_anchor(monkeypatch):
    clock = _clock()
    kinematics = load_frozen_c2_3a()
    producer = _producer()
    events = _initial_events(clock)
    pelvis = next(event for event in events if event.node_id == module.PELVIS_NODE)
    accepted = _anchor(pelvis, kinematics.geometry)
    monkeypatch.setattr(Native200PublicationProducer, "publication_for_pelvis_event",
                        lambda _self, _record, *, availability_global_ns: accepted)
    owner = module.FullSessionBodyPoseOwner(clock, producer, _hinges(), kinematics=kinematics)
    frames = [frame for event in events for frame in owner.ingest_ticket(_ticket(event))]
    frame = frames[0]
    assert frame.action_id == module.SESSION_ID
    assert frame.provenance == "FULL_SESSION_LABEL_FREE_EXACT_ACQUIRED_NATIVE200_ANCHOR"
    assert all(np.array_equal(frame.base_rotations_world[key], accepted.base_rotations_world[key])
               for key in SEGMENTS)
    assert owner.audit() == module.FullSessionBodyPoseAudit(
        10, 1, 1, 0, 0, 0, owner.owner_digest,
    )


def test_gap_tick_uses_persistent_node_vqf_hinge_ik_and_frozen_fk(monkeypatch):
    clock = _clock()
    kinematics = load_frozen_c2_3a()
    producer = _producer()
    events = _initial_events(clock)
    pelvis = next(event for event in events if event.node_id == module.PELVIS_NODE)
    accepted = _anchor(pelvis, kinematics.geometry)
    calls = iter((accepted, ValueError("pelvis IMU tick has no acquired-pose owner")))

    def publish(_self, _record, *, availability_global_ns):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(Native200PublicationProducer, "publication_for_pelvis_event", publish)
    owner = module.FullSessionBodyPoseOwner(clock, producer, _hinges(), kinematics=kinematics)
    for event in events:
        owner.ingest_ticket(_ticket(event))
    gap = _event(module.PELVIS_NODE, len(events) + 1, clock, label="arbitrary-gap-label")
    frame = owner.ingest_ticket(_ticket(gap))[0]
    assert frame.provenance == "FULL_SESSION_LABEL_FREE_VQF_DELTA_HINGE_IK_FROZEN_FK"
    assert frame.source_global_ns == gap.common_global_ns
    assert set(frame.offsets_world_m) == set(NODE_TO_SEGMENT)
    assert owner.audit().gap_publications == 1


def test_labels_do_not_change_owner_or_anchor_frame_digest(monkeypatch):
    clock = _clock()
    kinematics = load_frozen_c2_3a()
    base_events = _initial_events(clock)
    pelvis = next(event for event in base_events if event.node_id == module.PELVIS_NODE)
    accepted = _anchor(pelvis, kinematics.geometry)
    monkeypatch.setattr(Native200PublicationProducer, "publication_for_pelvis_event",
                        lambda _self, _record, *, availability_global_ns: accepted)
    owners = [module.FullSessionBodyPoseOwner(clock, _producer(), _hinges(), kinematics=kinematics)
              for _ in range(2)]
    outputs = []
    for owner, label in zip(owners, ("action00", "permuted-region")):
        for event in base_events:
            result = owner.ingest_ticket(_ticket(replace(
                event, host_time_label=label,
            )))
            outputs.extend(result)
    assert owners[0].owner_digest == owners[1].owner_digest
    assert outputs[0].digest == outputs[1].digest


def test_invalid_and_replayed_ticket_do_not_advance_audit(monkeypatch):
    clock = _clock()
    kinematics = load_frozen_c2_3a()
    owner = module.FullSessionBodyPoseOwner(clock, _producer(), _hinges(), kinematics=kinematics)
    event = _event(next(iter(NODE_TO_SEGMENT)), 0, clock)
    bad = replace(event, clock_mapping_digest="f" * 64)
    before = owner.audit()
    with pytest.raises(ValueError, match="owner mismatch"):
        owner.ingest_ticket(_ticket(bad))
    assert owner.audit() == before
    ticket = _ticket(event)
    assert owner.ingest_ticket(ticket) == ()
    committed = owner.audit()
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        owner.ingest_ticket(ticket)
    assert owner.audit() == committed


def test_pelvis_first_is_audited_and_does_not_block_later_ten_node_pose(monkeypatch):
    clock = _clock()
    kinematics = load_frozen_c2_3a()
    producer = _producer()
    owner = module.FullSessionBodyPoseOwner(
        clock, producer, _hinges(), kinematics=kinematics,
    )
    first_pelvis = _event(module.PELVIS_NODE, 0, clock)
    anchor_event = _event(module.PELVIS_NODE, 20, clock)
    accepted = _anchor(anchor_event, kinematics.geometry)
    monkeypatch.setattr(
        Native200PublicationProducer, "publication_for_pelvis_event",
        lambda _self, _record, *, availability_global_ns: accepted,
    )

    assert owner.ingest_ticket(_ticket(first_pelvis)) == ()
    for index, node in enumerate(
        (node for node in NODE_TO_SEGMENT if node != module.PELVIS_NODE), start=1,
    ):
        assert owner.ingest_ticket(_ticket(_event(node, index, clock))) == ()
    frames = owner.ingest_ticket(_ticket(anchor_event))

    assert len(frames) == 1
    assert frames[0].source_global_ns == anchor_event.common_global_ns
    assert owner.finish() == ()
    audit = owner.audit()
    assert audit.precoverage_pelvis_omissions == 1
    assert audit.pelvis_publications == 1
    assert audit.accepted_anchor_publications == 1


def test_finish_fails_if_any_node_never_started():
    clock = _clock()
    owner = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=load_frozen_c2_3a(),
    )
    owner.ingest_ticket(_ticket(_event(module.PELVIS_NODE, 0, clock)))
    with pytest.raises(RuntimeError, match="lacks IMU history"):
        owner.finish()


def test_first_unpublishable_action_tick_is_omitted_then_next_anchor_continues(monkeypatch):
    clock = _clock()
    kinematics = load_frozen_c2_3a()
    producer = _producer()
    events = _initial_events(clock)
    pelvis = next(event for event in events if event.node_id == module.PELVIS_NODE)
    later = _event(module.PELVIS_NODE, len(events) + 1, clock)
    accepted = _anchor(later, kinematics.geometry)
    calls = iter((
        ValueError("event lacks consecutive same-span source frame"), accepted,
    ))

    def publish(_self, _record, *, availability_global_ns):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(Native200PublicationProducer,
                        "publication_for_pelvis_event", publish)
    owner = module.FullSessionBodyPoseOwner(
        clock, producer, _hinges(), kinematics=kinematics,
    )
    assert [frame for event in events
            for frame in owner.ingest_ticket(_ticket(event))] == []
    frames = owner.ingest_ticket(_ticket(later))
    assert len(frames) == 1
    assert owner.audit().preanchor_pelvis_omissions == 1


def test_record_batch_capability_matches_scalar_and_rejects_foreign_before_mutation():
    clock = _clock()
    scalar = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=load_frozen_c2_3a(),
    )
    batched = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=load_frozen_c2_3a(),
    )
    event = _event(next(node for node in NODE_TO_SEGMENT if node != module.PELVIS_NODE),
                   0, clock)
    assert scalar.ingest_ticket(_ticket(event)) == ()
    authority = object()
    batched.bind_record_batch_coordinator(authority)
    with pytest.raises(RuntimeError, match="foreign"):
        batched.ingest_record_batch((event,), authority=object(), consumer=lambda _: None)
    assert batched.audit().imu_events == 0
    raw = event.payload_owner.raw
    raw_identity = (
        raw.record_index, raw.start_offset, raw.end_offset, raw.encoded_sha256,
    )
    snapshot_calls = 0
    actual_snapshot = batched._record_batch_snapshot

    def counted_snapshot(node):
        nonlocal snapshot_calls
        snapshot_calls += 1
        return actual_snapshot(node)

    batched._record_batch_snapshot = counted_snapshot
    capability = batched._issue_record_batch_capability(
        authority=authority, node=event.node_id, raw_identity=raw_identity,
    )
    with pytest.raises(RuntimeError, match="nested"):
        batched._issue_record_batch_capability(
            authority=authority, node=event.node_id, raw_identity=raw_identity,
        )
    with pytest.raises(RuntimeError, match="stale or foreign"):
        batched._validate_record_batch_capability(
            capability, authority=authority, node=event.node_id,
            raw_identity=(raw_identity[0] + 1, *raw_identity[1:]),
        )
    batched.ingest_record_batch(
        (event,), authority=authority, consumer=lambda _: None,
        _record_capability=capability,
    )
    batched._close_record_batch_capability(capability, authority=authority)
    batched._finalize_record_batch_capability(capability, authority=authority)
    assert batched.audit() == scalar.audit()
    assert snapshot_calls == 1
    with pytest.raises(RuntimeError, match="stale or foreign"):
        batched.ingest_record_batch(
            (event,), authority=authority, consumer=lambda _: None,
            _record_capability=capability,
        )
    with pytest.raises(RuntimeError, match="already bound"):
        batched.bind_record_batch_coordinator(object())


def test_later_record_event_failure_restores_all_body_owned_state(monkeypatch):
    clock = _clock()
    owner = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=load_frozen_c2_3a(),
    )
    authority = object()
    owner.bind_record_batch_coordinator(authority)
    node = next(node for node in NODE_TO_SEGMENT if node != module.PELVIS_NODE)
    first = _event(node, 0, clock)
    second = _event(node, 1, clock)
    shared_raw = first.payload_owner.raw
    second_raw = replace(shared_raw, sample_index=1)
    second_record = replace(second.payload_owner, raw=second_raw)
    second = replace(
        second, payload_owner=second_record,
        event_id=(f"v47:{second_raw.record_index}:1:{second_raw.start_offset}:"
                  f"{second_raw.end_offset}:{second_raw.encoded_sha256}"),
    )
    before = _owned_state_bytes(owner)
    ingest = owner._ingest
    calls = 0

    def fail_second(event, *, _batch_lease=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected later-event failure")
        return ingest(event, _batch_lease=_batch_lease)

    monkeypatch.setattr(owner, "_ingest", fail_second)
    with pytest.raises(RuntimeError, match="later-event"):
        owner.ingest_record_batch(
            (first, second), authority=authority, consumer=lambda _: None,
        )
    assert _owned_state_bytes(owner) == before


def _record_events(node, count, clock, *, first_index=0, label="ignored"):
    events = []
    raw_template = _event(node, first_index, clock, label=label).payload_owner.raw
    for sample_index in range(count):
        event = _event(node, first_index + sample_index, clock, label=label)
        raw = replace(raw_template, sample_index=sample_index)
        record = replace(event.payload_owner, raw=raw)
        events.append(replace(
            event, payload_owner=record,
            event_id=(f"v47:{raw.record_index}:{sample_index}:{raw.start_offset}:"
                      f"{raw.end_offset}:{raw.encoded_sha256}"),
        ))
    return tuple(events)


def _event_at_timer(node, timer, index, clock):
    event = _event(node, index, clock)
    binding = clock.binding_for(node)
    common_ns = binding.global_ns(timer)
    record = replace(
        event.payload_owner,
        node_timer_us=timer,
        global_time_ns=common_ns,
        payload={**event.payload_owner.payload,
                 "base_timer2_us": timer - 5_000, "delta_us": 5_000},
    )
    return replace(
        event, common_global_ns=common_ns, availability_global_ns=common_ns,
        payload_owner=record, imu_timer2=ImuTimer2Fields(timer - 5_000, timer),
    )


def _authentic_pelvis_record(count, clock, producer_source, producer_clock):
    template = RawByteProvenance(700, 7000, 7400, "7" * 64, 0)
    output = []
    for sample_index, frame in enumerate(range(1, count + 1)):
        record = _producer_record(producer_source, producer_clock, frame)
        raw = replace(template, sample_index=sample_index)
        record = replace(record, raw=raw)
        binding = clock.binding_for(module.PELVIS_NODE)
        event_id = (
            f"v47:{raw.record_index}:{sample_index}:{raw.start_offset}:"
            f"{raw.end_offset}:{raw.encoded_sha256}"
        )
        output.append(ContinuousEvent(
            event_id, "IMU", -1, module.SESSION_ID,
            int(record.global_time_ns), int(record.global_time_ns),
            module.PELVIS_NODE, 7, "B306_TIMER2", binding.clock_mapping_digest,
            binding.clock_owner_sha256, binding.clock_source_sha256, "ignored",
            record, imu_timer2=ImuTimer2Fields(
                int(record.payload["base_timer2_us"]), int(record.node_timer_us),
            ), region_id=module.SESSION_ID,
        ))
    return tuple(output)


def _authentic_body(count, *, scalar=False, omit_node=None):
    clock = _clock()
    producer, sources, producer_clock, _ = _authentic_producer_fixture(count + 1)
    if scalar:
        producer._prepare_pelvis_record_rows = lambda _records: None
    owner = module.FullSessionBodyPoseOwner(
        clock, producer, _hinges(), kinematics=load_frozen_c2_3a(),
    )
    authority = object()
    owner.bind_record_batch_coordinator(authority)
    for index, node in enumerate(NODE_TO_SEGMENT):
        if node in (module.PELVIS_NODE, omit_node):
            continue
        owner.ingest_record_batch(
            (_event_at_timer(node, 100_000, index, clock),),
            authority=authority, consumer=lambda _frame: None,
        )
    pelvis_zero_record = _producer_record(
        sources["00_initial_still"], producer_clock, 0,
    )
    raw = pelvis_zero_record.raw
    pelvis_zero = ContinuousEvent(
        f"v47:{raw.record_index}:0:{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}",
        "IMU", -1, module.SESSION_ID, int(pelvis_zero_record.global_time_ns),
        int(pelvis_zero_record.global_time_ns), module.PELVIS_NODE, 7,
        "B306_TIMER2", clock.binding_for(module.PELVIS_NODE).clock_mapping_digest,
        clock.binding_for(module.PELVIS_NODE).clock_owner_sha256,
        clock.binding_for(module.PELVIS_NODE).clock_source_sha256, "ignored",
        pelvis_zero_record, imu_timer2=ImuTimer2Fields(
            int(pelvis_zero_record.payload["base_timer2_us"]),
            int(pelvis_zero_record.node_timer_us),
        ), region_id=module.SESSION_ID,
    )
    owner.ingest_record_batch(
        (pelvis_zero,), authority=authority, consumer=lambda _frame: None,
    )
    record = _authentic_pelvis_record(
        count, clock, sources["00_initial_still"], producer_clock,
    )
    return owner, producer, authority, clock, record


@pytest.mark.parametrize("count", (1, 10, 16))
def test_authentic_prepared_pose_record_matches_forced_scalar(count):
    batched, _bp, ba, _clock, events = _authentic_body(count)
    scalar, _sp, sa, _clock2, scalar_events = _authentic_body(count, scalar=True)
    actual, expected = [], []
    batched.ingest_record_batch(events, authority=ba, consumer=actual.append)
    scalar.ingest_record_batch(scalar_events, authority=sa, consumer=expected.append)
    assert tuple(frame.digest for frame in actual) == tuple(frame.digest for frame in expected)
    assert _owned_state_bytes(batched) == _owned_state_bytes(scalar)
    assert batched.audit() == scalar.audit()


def test_precoverage_pelvis_record_forces_scalar_and_delayed_release_is_exact():
    missing = next(node for node in NODE_TO_SEGMENT if node != module.PELVIS_NODE)
    batched, _bp, ba, clock, events = _authentic_body(3, omit_node=missing)
    scalar, _sp, sa, scalar_clock, scalar_events = _authentic_body(
        3, scalar=True, omit_node=missing,
    )
    actual, expected = [], []
    batched.ingest_record_batch(events, authority=ba, consumer=actual.append)
    scalar.ingest_record_batch(scalar_events, authority=sa, consumer=expected.append)
    assert actual == expected == []
    assert all(pending.prepared_pose_row is None for pending in batched._pending_pelvis)
    batched.ingest_record_batch(
        (_event_at_timer(missing, 100_000, 30, clock),), authority=ba,
        consumer=actual.append,
    )
    scalar.ingest_record_batch(
        (_event_at_timer(missing, 100_000, 30, scalar_clock),), authority=sa,
        consumer=expected.append,
    )
    assert tuple(frame.digest for frame in actual) == tuple(frame.digest for frame in expected)
    assert _owned_state_bytes(batched) == _owned_state_bytes(scalar)


def test_all_first_orientations_present_but_later_owner_forces_scalar(monkeypatch):
    owner, producer, authority, _clock, events = _authentic_body(1)
    later_node = next(node for node in NODE_TO_SEGMENT if node != module.PELVIS_NODE)
    owner._first_orientation_ns[later_node] = events[0].common_global_ns + 1
    calls = 0
    original = producer._prepare_pelvis_record_rows

    def counted(records):
        nonlocal calls
        calls += 1
        return original(records)

    monkeypatch.setattr(producer, "_prepare_pelvis_record_rows", counted)
    output = []
    owner.ingest_record_batch(events, authority=authority, consumer=output.append)
    assert calls == 0
    assert output == []
    assert not owner._pending_pelvis
    assert owner.audit().precoverage_pelvis_omissions == 1


def test_prepared_pose_row_failure_and_consumer_failure_restore_then_retry(monkeypatch):
    owner, producer, authority, _clock, events = _authentic_body(3)
    clean, _cp, clean_authority, _clean_clock, clean_events = _authentic_body(3)
    before = owner._record_batch_snapshot(module.PELVIS_NODE)
    original = producer._publication_for_pelvis_event_with_row
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected prepared-row failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(producer, "_publication_for_pelvis_event_with_row", fail_second)
    with pytest.raises(RuntimeError, match="prepared-row failure"):
        owner.ingest_record_batch(events, authority=authority, consumer=lambda _frame: None)
    after = owner._record_batch_snapshot(module.PELVIS_NODE)
    assert after.block is before.block
    # Pickle each authoritative field independently: one whole-object pickle
    # encodes incidental cross-field memo aliases that rollback need not retain.
    for name in before.__dataclass_fields__:
        if name != "block":
            assert pickle.dumps(getattr(after, name), 5) == pickle.dumps(
                getattr(before, name), 5,
            )
    monkeypatch.setattr(producer, "_publication_for_pelvis_event_with_row", original)
    retried, expected = [], []
    owner.ingest_record_batch(events, authority=authority, consumer=retried.append)
    clean.ingest_record_batch(clean_events, authority=clean_authority, consumer=expected.append)
    assert tuple(frame.digest for frame in retried) == tuple(frame.digest for frame in expected)
    assert _owned_state_bytes(owner) == _owned_state_bytes(clean)

    retry_control, _rp, retry_authority, _rc, retry_events = _authentic_body(3)
    retry_clean, _rcp, retry_clean_authority, _rcc, retry_clean_events = (
        _authentic_body(3)
    )
    retry_before = retry_control._record_batch_snapshot(module.PELVIS_NODE)
    delivered = 0

    def consumer(_frame):
        nonlocal delivered
        delivered += 1
        if delivered == 2:
            raise RuntimeError("injected consumer failure")

    with pytest.raises(RuntimeError, match="consumer failure"):
        retry_control.ingest_record_batch(
            retry_events, authority=retry_authority, consumer=consumer,
        )
    retry_after = retry_control._record_batch_snapshot(module.PELVIS_NODE)
    assert retry_after.block is retry_before.block
    for name in retry_before.__dataclass_fields__:
        if name != "block":
            assert pickle.dumps(getattr(retry_after, name), 5) == pickle.dumps(
                getattr(retry_before, name), 5,
            )
    retried, clean_output = [], []
    retry_control.ingest_record_batch(
        retry_events, authority=retry_authority, consumer=retried.append,
    )
    retry_clean.ingest_record_batch(
        retry_clean_events, authority=retry_clean_authority,
        consumer=clean_output.append,
    )
    assert tuple(frame.digest for frame in retried) == tuple(
        frame.digest for frame in clean_output
    )
    assert _owned_state_bytes(retry_control) == _owned_state_bytes(retry_clean)


@pytest.mark.parametrize("count", (1, 10, 16))
@pytest.mark.parametrize(
    "node", (module.PELVIS_NODE, next(node for node in NODE_TO_SEGMENT
                                     if node != module.PELVIS_NODE)),
)
def test_record_batch_in_place_vqf_is_byte_exact_to_legacy_for_record_sizes(node, count):
    clock = _clock()
    scalar = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=load_frozen_c2_3a(),
    )
    batched = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=load_frozen_c2_3a(),
    )
    authority = object()
    batched.bind_record_batch_coordinator(authority)
    events = _record_events(node, count, clock)
    scalar_frames = tuple(
        frame for event in events for frame in scalar.ingest_ticket(_ticket(event))
    )
    batch_frames = []
    batched.ingest_record_batch(
        events, authority=authority, consumer=batch_frames.append,
    )
    assert tuple(batch_frames) == scalar_frames
    assert _owned_state_bytes(batched) == _owned_state_bytes(scalar)


def test_record_batch_copies_only_mutating_node_once_and_constructs_no_vqf_blocks(
    monkeypatch,
):
    clock = _clock()
    owner = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=load_frozen_c2_3a(),
    )
    legacy = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=load_frozen_c2_3a(),
    )
    authority = object()
    owner.bind_record_batch_coordinator(authority)
    node = next(node for node in NODE_TO_SEGMENT if node != module.PELVIS_NODE)
    events = _record_events(node, 16, clock)
    history_ids = {id(history): key for key, history in owner._orientation_history.items()}
    copied_states = []
    copied_histories = []
    snapshot_nodes = []
    real_deepcopy = module.deepcopy
    real_constructor = module.qmt.OriEstVQFBlock
    real_snapshot = owner._record_batch_snapshot
    constructions = 0

    def counted_deepcopy(value):
        if isinstance(value, dict) and "gyrQuat" in value:
            copied_states.append(node)
        if id(value) in history_ids:
            copied_histories.append(history_ids[id(value)])
        return real_deepcopy(value)

    def counted_constructor(*args, **kwargs):
        nonlocal constructions
        constructions += 1
        return real_constructor(*args, **kwargs)

    def counted_snapshot(snapshot_node):
        snapshot_nodes.append(snapshot_node)
        return real_snapshot(snapshot_node)

    other_blocks = {key: value for key, value in owner._blocks.items() if key != node}
    monkeypatch.setattr(module, "deepcopy", counted_deepcopy)
    monkeypatch.setattr(module.qmt, "OriEstVQFBlock", counted_constructor)
    monkeypatch.setattr(owner, "_record_batch_snapshot", counted_snapshot)
    owner.ingest_record_batch(events, authority=authority, consumer=lambda _: None)
    assert snapshot_nodes == [node]
    assert copied_states == [node]
    assert copied_histories == [node]
    assert constructions == 0
    assert all(owner._blocks[key] is value for key, value in other_blocks.items())
    legacy.ingest_ticket(_ticket(events[0]))
    assert constructions == 1


def test_record_batch_release_ready_callback_failure_restores_delta_and_lease(
    monkeypatch,
):
    clock = _clock()
    kinematics = load_frozen_c2_3a()
    owner = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=kinematics,
    )
    authority = object()
    owner.bind_record_batch_coordinator(authority)
    initial = [event for event in _initial_events(clock) if event.node_id != module.PELVIS_NODE]
    for event in initial:
        owner.ingest_record_batch((event,), authority=authority, consumer=lambda _: None)
    pelvis = _record_events(module.PELVIS_NODE, 1, clock, first_index=20)[0]
    accepted = _anchor(pelvis, kinematics.geometry)
    monkeypatch.setattr(
        Native200PublicationProducer, "publication_for_pelvis_event",
        lambda _self, _record, *, availability_global_ns: accepted,
    )
    before = _owned_state_bytes(owner)
    other_blocks = {
        key: value for key, value in owner._blocks.items() if key != module.PELVIS_NODE
    }
    captured = []
    real_ingest = owner._ingest

    def capture(event, *, _batch_lease=None):
        captured.append(_batch_lease)
        return real_ingest(event, _batch_lease=_batch_lease)

    monkeypatch.setattr(owner, "_ingest", capture)
    with pytest.raises(RuntimeError, match="consumer failure"):
        owner.ingest_record_batch(
            (pelvis,), authority=authority,
            consumer=lambda _frame: (_ for _ in ()).throw(RuntimeError("consumer failure")),
        )
    assert _owned_state_bytes(owner) == before
    assert all(owner._blocks[key] is value for key, value in other_blocks.items())
    with pytest.raises(RuntimeError, match="foreign, replayed, or cross-record"):
        real_ingest(pelvis, _batch_lease=captured[0])
    assert _owned_state_bytes(owner) == before


def test_record_batch_callback_reentry_and_hostile_leases_fail_before_mutation(monkeypatch):
    clock = _clock()
    kinematics = load_frozen_c2_3a()
    owner = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=kinematics,
    )
    authority = object()
    owner.bind_record_batch_coordinator(authority)
    initial = [event for event in _initial_events(clock) if event.node_id != module.PELVIS_NODE]
    for event in initial:
        owner.ingest_record_batch((event,), authority=authority, consumer=lambda _: None)
    pelvis = _record_events(module.PELVIS_NODE, 1, clock, first_index=20)[0]
    accepted = _anchor(pelvis, kinematics.geometry)
    monkeypatch.setattr(
        Native200PublicationProducer, "publication_for_pelvis_event",
        lambda _self, _record, *, availability_global_ns: accepted,
    )
    other_node = next(node for node in NODE_TO_SEGMENT if node != module.PELVIS_NODE)
    nested = _event(other_node, 40, clock)
    cross_raw = _record_events(module.PELVIS_NODE, 1, clock, first_index=41)[0]
    before = _owned_state_bytes(owner)
    block_identities = {key: value for key, value in owner._blocks.items()}
    real_ingest = owner._ingest
    lease_box = []

    def capture(event, *, _batch_lease=None):
        if _batch_lease is not None and not lease_box:
            lease_box.append(_batch_lease)
        return real_ingest(event, _batch_lease=_batch_lease)

    monkeypatch.setattr(owner, "_ingest", capture)

    def reenter(_frame):
        lease = lease_box[0]
        with pytest.raises(RuntimeError, match="foreign, replayed, or cross-record"):
            real_ingest(pelvis, _batch_lease=replace(lease, owner=object()))
        with pytest.raises(RuntimeError, match="foreign, replayed, or cross-record"):
            real_ingest(nested, _batch_lease=lease)
        with pytest.raises(RuntimeError, match="foreign, replayed, or cross-record"):
            real_ingest(cross_raw, _batch_lease=lease)
        with pytest.raises(RuntimeError, match="nested body pose"):
            owner.ingest_record_batch(
                (cross_raw,), authority=authority, consumer=lambda _: None,
            )
        with pytest.raises(RuntimeError, match="legacy body pose ingest"):
            owner.ingest_ticket(_ticket(nested))
        with pytest.raises(RuntimeError, match="finish"):
            owner.finish()
        raise RuntimeError("consumer failure after hostile reentry")

    with pytest.raises(RuntimeError, match="consumer failure after hostile reentry"):
        owner.ingest_record_batch((pelvis,), authority=authority, consumer=reenter)
    assert _owned_state_bytes(owner) == before
    assert all(owner._blocks[key] is value for key, value in block_identities.items())


def test_complete_ten_node_record_batches_match_scalar_and_ignore_labels(monkeypatch):
    clock = _clock()
    kinematics = load_frozen_c2_3a()
    events = _initial_events(clock)
    pelvis = next(event for event in events if event.node_id == module.PELVIS_NODE)
    accepted = _anchor(pelvis, kinematics.geometry)
    monkeypatch.setattr(
        Native200PublicationProducer, "publication_for_pelvis_event",
        lambda _self, _record, *, availability_global_ns: accepted,
    )
    scalar = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=kinematics,
    )
    batched = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=kinematics,
    )
    permuted = module.FullSessionBodyPoseOwner(
        clock, _producer(), _hinges(), kinematics=kinematics,
    )
    batch_authority, permuted_authority = object(), object()
    batched.bind_record_batch_coordinator(batch_authority)
    permuted.bind_record_batch_coordinator(permuted_authority)
    scalar_frames = [frame for event in events
                     for frame in scalar.ingest_ticket(_ticket(event))]
    batch_frames = []
    permuted_frames = []
    for event in events:
        batched.ingest_record_batch(
            (event,), authority=batch_authority, consumer=batch_frames.append,
        )
        permuted.ingest_record_batch(
            (replace(event, host_time_label=f"permuted-{event.node_id}"),),
            authority=permuted_authority, consumer=permuted_frames.append,
        )
    identity = lambda frames: tuple(
        (frame.digest, frame.pose_publication_digest, frame.source_timer_us,
         frame.publication_revision) for frame in frames
    )
    assert identity(batch_frames) == identity(scalar_frames)
    assert identity(permuted_frames) == identity(scalar_frames)
    assert batched.audit() == scalar.audit() == permuted.audit()
