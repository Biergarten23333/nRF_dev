from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import biospur_fusion.c2_uwb_root_world.diagnostic_pelvis_orientation as orientation_module
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
    UwbTimer2Fields,
)
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionEventRouter,
    _FullSessionDeliveryOwner,
    _event_digest,
    _sensor_identity_digest,
)
from biospur_fusion.c2_uwb_root_world.action00_gap02_diagnostic_plan import (
    DiagnosticEventRouter,
    load_action00_gap02_diagnostic_plan,
)
from biospur_fusion.c2_uwb_root_world.diagnostic_pelvis_orientation import (
    DiagnosticPelvisGapOrientationOwner,
    PELVIS_NODE,
    PREPARATION_SAMPLES,
)
from biospur_fusion.ingest.events import RawByteProvenance, RecordType, TypedEvent


ROOT = Path(__file__).resolve().parents[1]


def _clock() -> ContinuousClockOwner:
    nodes = (PELVIS_NODE,) + tuple(f"BSF{index:04X}" for index in range(9))
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(
        NodeClockBinding(
            node, 7, "B306_TIMER2", f"{index + 1:064x}",
            1_000.0, 0.0, "a" * 64, "b" * 64,
        )
        for index, node in enumerate(nodes)
    ))


def _events(count: int = 104):
    clock = _clock()
    plan = load_action00_gap02_diagnostic_plan(ROOT, clock)
    router = DiagnosticEventRouter(plan, clock)
    action = plan.regions[0]
    first = (action.start_ns + 999) // 1_000
    result = []
    for index in range(count):
        timer = first + index * 5_000
        record = TypedEvent(
            PELVIS_NODE, 7, RecordType.IMU, index & 0xFFFF, timer,
            None, None, 0,
            payload={
                "base_timer2_us": timer - 5_000,
                "delta_us": 5_000,
                "acc_raw": (2048, 0, 0),
                "gyro_raw": (0, 0, 0),
            },
            raw=RawByteProvenance(
                index + 1, action.start_offset + index * 3,
                action.start_offset + index * 3 + 2,
                f"{index + 100:064x}", 0,
            ),
        )
        result.extend(router.route_original_record((record,)))
    return plan, clock, tuple(result)


def _gap_event(plan, clock):
    gap = plan.regions[1]
    timer = (gap.start_ns + 999) // 1_000
    record = TypedEvent(
        PELVIS_NODE, 7, RecordType.IMU, 500, timer, None, None, 0,
        payload={
            "base_timer2_us": timer - 5_000, "delta_us": 5_000,
            "acc_raw": (2048, 0, 0), "gyro_raw": (0, 0, 0),
        },
        raw=RawByteProvenance(
            500, gap.start_offset, gap.start_offset + 2, "e" * 64, 0,
        ),
    )
    return DiagnosticEventRouter(plan, clock).route_original_record((record,))[0]


def _imu_ticket(event):
    router = object.__new__(FullSessionEventRouter)
    router._pending_events = (event,)
    router._pending_event_digests = (_event_digest(event),)
    router._pending_sensor_digests = (_sensor_identity_digest(event),)
    router._pending_cursor = 0
    router._delivery_authority = None
    return _FullSessionDeliveryOwner(router).issue(event).dispatch()


def test_fixed_preparation_has_no_backfill_and_next_frame_is_owned() -> None:
    plan, clock, events = _events()
    owner = DiagnosticPelvisGapOrientationOwner(plan, clock)
    assert all(owner._ingest_event(event) is None for event in events[:PREPARATION_SAMPLES])
    frame = owner._ingest_event(events[PREPARATION_SAMPLES])
    assert frame is not None
    assert frame.source_event_id == events[PREPARATION_SAMPLES].event_id
    assert frame.availability_time_s == events[PREPARATION_SAMPLES].availability_global_ns * 1e-9
    assert not frame.product_ready and not frame.scientific_pass
    assert owner.audit()["preparation_backfilled"] is False
    assert owner.audit()["full_body_pose_or_fk_issued"] is False
    with pytest.raises(ValueError):
        frame.rotation_world_from_sensor[0, 0] = 2.0


def test_public_orientation_admission_consumes_only_authenticated_imu_child():
    plan, clock, events = _events(1)
    owner = DiagnosticPelvisGapOrientationOwner(plan, clock)
    ticket = _imu_ticket(events[0])
    assert owner.ingest_ticket(ticket) is None
    with pytest.raises(RuntimeError, match="FOREIGN_MUTATED_OR_REPLAYED"):
        owner.ingest_ticket(ticket)


def test_orientation_rejects_uwb_child_without_consuming_ticket_or_owner():
    plan, clock, events = _events(1)
    owner = DiagnosticPelvisGapOrientationOwner(plan, clock)
    source = events[0]
    timer = source.payload_owner.node_timer_us
    record = replace(
        source.payload_owner, record_type=RecordType.UWB,
        payload={
            "packet_sequence": 1, "sweep": 1,
            "strobe_us": timer, "frame_us": timer,
            "anchor_id": list(range(8)), "range_mm": [2_000] * 8,
            "t_round_us": [100] * 8, "quality_percent": [100] * 8,
            "valid_mask": 0xFF, "identity": 1, "node_ms": 1,
        },
    )
    event = replace(
        source, kind="UWB", payload_owner=record, imu_timer2=None,
        uwb_timer2=UwbTimer2Fields(timer, timer),
    )
    router = object.__new__(FullSessionEventRouter)
    router._pending_events = (event,)
    router._pending_event_digests = (_event_digest(event),)
    router._pending_sensor_digests = (_sensor_identity_digest(event),)
    router._pending_cursor = 0
    router._delivery_authority = None
    child = _FullSessionDeliveryOwner(router).issue(event).dispatch()
    before = owner.owner_bytes()
    with pytest.raises(TypeError, match="authenticated IMU ticket"):
        owner.ingest_ticket(child)
    assert owner.owner_bytes() == before
    seen = []
    child.deliver(lambda delivered: seen.append(delivered.event_id))
    assert seen == [event.event_id]


def test_partition_and_prefix_ingestion_have_identical_frame_chain() -> None:
    plan, clock, events = _events()
    whole = DiagnosticPelvisGapOrientationOwner(plan, clock)
    partitioned = DiagnosticPelvisGapOrientationOwner(plan, clock)
    whole_frames = tuple(filter(None, (whole._ingest_event(event) for event in events)))
    split_frames = []
    for part in (events[:37], events[37:100], events[100:]):
        split_frames.extend(filter(None, (partitioned._ingest_event(event) for event in part)))
    assert tuple(frame.digest for frame in whole_frames) == tuple(
        frame.digest for frame in split_frames
    )
    assert whole.owner_bytes() == partitioned.owner_bytes()
    gap_frame = whole._ingest_event(_gap_event(plan, clock))
    assert gap_frame is not None
    assert gap_frame.region_id == plan.regions[1].region_id
    assert whole.audit()["full_body_pose_or_fk_issued"] is False


def test_replay_foreign_node_and_clock_mutation_are_exact_owner_noops() -> None:
    plan, clock, events = _events()
    owner = DiagnosticPelvisGapOrientationOwner(plan, clock)
    for event in events[:PREPARATION_SAMPLES + 1]:
        owner._ingest_event(event)
    for invalid, match in (
        (events[PREPARATION_SAMPLES], "chronology/region"),
        (replace(events[PREPARATION_SAMPLES + 1], node_id="BSF0000"), "owner mismatch"),
        (replace(events[PREPARATION_SAMPLES + 1], clock_mapping_digest="f" * 64), "owner mismatch"),
    ):
        before = owner.owner_bytes()
        with pytest.raises((ValueError, TypeError), match=match):
            owner._ingest_event(invalid)
        assert owner.owner_bytes() == before


def test_post_step_frame_failure_discards_candidate_vqf_and_can_continue(
    monkeypatch,
) -> None:
    plan, clock, events = _events()
    failed = DiagnosticPelvisGapOrientationOwner(plan, clock)
    control = DiagnosticPelvisGapOrientationOwner(plan, clock)
    for event in events[:PREPARATION_SAMPLES]:
        assert failed._ingest_event(event) is None
        assert control._ingest_event(event) is None
    before = failed.owner_bytes()
    original = orientation_module.DiagnosticPelvisOrientationFrame

    def reject(*_args, **_kwargs):
        raise RuntimeError("INJECTED_POST_STEP_FRAME_FAILURE")

    monkeypatch.setattr(orientation_module, "DiagnosticPelvisOrientationFrame", reject)
    with pytest.raises(RuntimeError, match="INJECTED_POST_STEP"):
        failed._ingest_event(events[PREPARATION_SAMPLES])
    assert failed.owner_bytes() == before
    monkeypatch.setattr(orientation_module, "DiagnosticPelvisOrientationFrame", original)
    recovered = failed._ingest_event(events[PREPARATION_SAMPLES])
    reference = control._ingest_event(events[PREPARATION_SAMPLES])
    assert recovered is not None and reference is not None
    assert recovered.digest == reference.digest
    assert failed.owner_bytes() == control.owner_bytes()
