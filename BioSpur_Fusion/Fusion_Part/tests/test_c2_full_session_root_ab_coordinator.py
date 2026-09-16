from __future__ import annotations

from collections import Counter
from dataclasses import replace
import ast
import hashlib
from pathlib import Path
import pickle
from types import SimpleNamespace

import numpy as np
import pytest

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CAPTURE2_PROTOCOL_SLOTS, CONTINUOUS_FRONTEND_SCHEMA, ContinuousClockOwner,
    ContinuousEvent, ImuTimer2Fields, NodeClockBinding, UwbTimer2Fields,
)
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionContinuousReader, FullSessionEventRouter, FullSessionStreamAudit,
    FullSessionRouteAudit, _FullSessionDeliveryOwner, _event_digest,
    _sensor_identity_digest,
)
from biospur_fusion.c2_coupled_progressive.continuous_stage2_adapter import (
    EventRegionOwner, FullSessionSourceEnvelope,
)
from biospur_fusion.c2_uwb_root_world.continuous_root_ab import PELVIS_NODE
from biospur_fusion.c2_uwb_root_world.diagnostic_c2_static_owner import DiagnosticC2StaticOwner
from biospur_fusion.c2_uwb_root_world.full_session_root_ab_coordinator import (
    FullSessionRootABCoordinator, LabelFreePelvisBootstrapOwner,
)
from biospur_fusion.c2_uwb_root_world.full_session_pelvis_orientation import (
    NON_PELVIS_IMU_REASON,
)
from biospur_fusion.ingest.events import EventStatus, RawByteProvenance, RecordType, TypedEvent
from biospur_fusion.root_r3 import CausalDelayedRootFilter


def _clock(static):
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(
        NodeClockBinding(
            node, direct.boot_epoch, "B306_TIMER2",
            hashlib.sha256(("mapping:" + node).encode()).hexdigest(),
            direct.a_ns_per_us, direct.b_ns,
            hashlib.sha256(("owner:" + node).encode()).hexdigest(),
            hashlib.sha256(("source:" + node).encode()).hexdigest(),
        ) for node, direct in sorted(static.clocks.items())
    ))


def _imu(index, clock, *, action_index=0, timer=None, region_id=None,
         node=PELVIS_NODE):
    binding = clock.binding_for(node)
    timer = (1_000_000 + index * 5_000) if timer is None else timer
    raw = RawByteProvenance(index + 1, 100 + index * 20, 110 + index * 20,
                            f"{index + 1:064x}", 0)
    record = TypedEvent(
        node, binding.boot_epoch, RecordType.IMU, index & 0xFFFF,
        timer, binding.global_ns(timer), 100, 0,
        {"base_timer2_us": timer - 5_000, "delta_us": 5_000,
         "acc_raw": [2048, 0, 0], "gyro_raw": [0, 0, 0]},
        {}, EventStatus.DECODED, raw,
    )
    event_action_index = -1 if region_id is not None else action_index
    event_action_id = region_id if region_id is not None else CAPTURE2_PROTOCOL_SLOTS[action_index].action_id
    return ContinuousEvent(
        f"v47:{raw.record_index}:0:{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}",
        "IMU", event_action_index, event_action_id,
        binding.global_ns(timer), binding.global_ns(timer), node,
        binding.boot_epoch, binding.clock_domain, binding.clock_mapping_digest,
        binding.clock_owner_sha256, binding.clock_source_sha256, "posthoc", record,
        imu_timer2=ImuTimer2Fields(timer - 5_000, timer), region_id=region_id,
    )


def _uwb(index, clock, static, *, timer, truth=(2.0, 1.2, 0.9), action_index=0):
    binding = clock.binding_for(PELVIS_NODE)
    rounds = np.arange(2_000, 2_800, 100, dtype=int)
    link_s = np.asarray([
        binding.global_ns(timer + int(value) // 2) * 1e-9 for value in rounds
    ])
    reference_s = float(np.median(link_s))
    position = np.asarray(truth, float)
    bias = static.anchor_delay_m + static.tag_delay_m
    ranges = np.linalg.norm(position[None, :] - static.anchors_m, axis=1) + bias
    frame = timer + 4_000
    raw = RawByteProvenance(index + 1, 10_000 + index * 20, 10_010 + index * 20,
                            hashlib.sha256(f"uwb:{index}".encode()).hexdigest(), 0)
    payload = {
        "packet_sequence": index & 0xFFFF, "sweep": index,
        "strobe_us": timer, "frame_us": frame, "anchor_id": list(range(8)),
        "range_mm": [int(round(value * 1_000)) for value in ranges],
        "t_round_us": rounds.tolist(), "quality_percent": [100] * 8,
        "valid_mask": 0xFF, "identity": 1, "node_ms": 1,
    }
    record = TypedEvent(
        PELVIS_NODE, binding.boot_epoch, RecordType.UWB, index,
        timer, None, None, 0, payload, {}, EventStatus.DECODED, raw,
    )
    return ContinuousEvent(
        f"v47:{raw.record_index}:0:{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}",
        "UWB", action_index, CAPTURE2_PROTOCOL_SLOTS[action_index].action_id,
        binding.global_ns(timer), binding.global_ns(frame), PELVIS_NODE,
        binding.boot_epoch, binding.clock_domain, binding.clock_mapping_digest,
        binding.clock_owner_sha256, binding.clock_source_sha256, "posthoc", record,
        uwb_timer2=UwbTimer2Fields(timer, frame),
    )


def _pending_delivery(events):
    router = object.__new__(FullSessionEventRouter)
    router._pending_events = tuple(events)
    router._pending_event_digests = tuple(_event_digest(event) for event in events)
    router._pending_sensor_digests = tuple(
        _sensor_identity_digest(event) for event in events
    )
    router._pending_cursor = 0
    router._delivery_authority = None
    return _FullSessionDeliveryOwner(router)


def _feed(coordinator, events):
    delivery = _pending_delivery(events)
    for event in events:
        coordinator.consume_ticket(delivery.issue(event))
    delivery.require_consumed()


def _group_router(clock):
    router = object.__new__(FullSessionEventRouter)
    router.inventory = SimpleNamespace()
    router.clock_owner = clock
    router._event_count = 0
    router._counts = Counter()
    router._imu_counts = {"synthetic": Counter()}
    router._last_timer = {}
    router._last_sequence = {}
    router._last_availability = {}
    router._last_ordering = {}
    router._last_raw_rank = None
    router._global_source_availability_ns = None
    router._minimum_sensor_ready_lower_bound_ns = None
    router._maximum_sensor_ready_lower_bound_ns = None
    router._lifted_record_count = 0
    router._maximum_availability_lift_ns = 0
    router._total_availability_lift_ns = 0
    router._exact = 0
    router._dropouts = 0
    router._identity = hashlib.sha256()
    router._maximum_owner_watermark_entries = 0
    router._pending_event_high_water = 0
    router._pending_events = ()
    router._pending_event_digests = ()
    router._pending_sensor_digests = ()
    router._pending_cursor = 0
    router._delivery_authority = None
    router._source_partition = lambda _row: SimpleNamespace(region_id="synthetic")
    router._session_owner = EventRegionOwner(
        full_session=FullSessionSourceEnvelope(),
    )
    return router


def _grouped_imu_rows(clock, *, first_timer):
    binding = clock.binding_for(PELVIS_NODE)
    raw_fields = (900, 90_000, 90_100, "9" * 64)
    rows = []
    for sample_index, delta in enumerate((0, 5_000)):
        timer = first_timer + delta
        rows.append(TypedEvent(
            PELVIS_NODE, binding.boot_epoch, RecordType.IMU,
            (900 + sample_index) & 0xFFFF, timer, None, None, 123, {
                "base_timer2_us": first_timer, "delta_us": delta,
                "acc_raw": [2048, 0, 0], "gyro_raw": [0, 0, 0],
            }, {"batch_count": 2}, EventStatus.DECODED,
            RawByteProvenance(*raw_fields, sample_index),
        ))
    return tuple(rows)


def _session(action_index=0):
    static = DiagnosticC2StaticOwner.from_sealed_archives()
    clock = _clock(static)
    base = static.clocks[PELVIS_NODE].first_timer_us + 10_000
    imus = [_imu(index, clock, action_index=action_index,
                 timer=base + index * 5_000) for index in range(101)]
    timer = base + 101 * 5_000
    imus.append(_imu(499, clock, action_index=action_index, timer=timer))
    bootstrap = _uwb(500, clock, static, timer=timer, action_index=action_index)
    after = _imu(501, clock, action_index=action_index, timer=timer + 5_000)
    return static, clock, imus, bootstrap, after


def _root_bytes(root):
    return pickle.dumps(root._prepare_position_rollback(), protocol=5)


def _audit(count):
    return FullSessionStreamAudit(
        FullSessionRouteAudit(
            count, {}, {}, 0, 0, 0, 0, 0, 0, 0, 0, 0, "0" * 64,
        ), {},
    )


def test_prebootstrap_conservation_and_only_joint_imu_creates_trajectory_samples():
    static, clock, imus, bootstrap, after = _session()
    owner = FullSessionRootABCoordinator(static=static, clock_owner=clock)
    _feed(owner, [*imus, bootstrap])
    assert owner._prebootstrap == 102
    assert owner._trajectory_count == 0
    _feed(owner, [after])
    assert owner._trajectory_count == 1
    assert owner._trajectory[0].measurement_time_s == owner._trajectory[0].availability_time_s
    before = tuple(owner._trajectory)
    uwb = _uwb(502, clock, static,
                timer=after.imu_timer2.trigger_timer2_us - 4_000)
    _feed(owner, [uwb])
    assert tuple(owner._trajectory) == before


def test_all_nonpelvis_imus_are_audited_inert_and_pelvis_continues():
    static, clock, imus, bootstrap, after = _session()
    owner = FullSessionRootABCoordinator(static=static, clock_owner=clock)
    _feed(owner, [*imus, bootstrap, after])
    assert owner.a is not None and owner.b is not None
    other_nodes = tuple(
        binding.node_id for binding in clock.bindings
        if binding.node_id != PELVIS_NODE
    )
    base_timer = after.imu_timer2.trigger_timer2_us
    events = tuple(
        _imu(700 + index, clock, node=node, timer=base_timer + (index + 1) * 5_000)
        for index, node in enumerate(other_nodes)
    )
    before = (
        owner.orientation.owner_bytes(), _root_bytes(owner.a), _root_bytes(owner.b),
        tuple(owner._trajectory), owner._events_consumed,
    )
    _feed(owner, events)
    assert owner.orientation.owner_bytes() == before[0]
    assert _root_bytes(owner.a) == before[1]
    assert _root_bytes(owner.b) == before[2]
    assert tuple(owner._trajectory) == before[3]
    assert owner._events_consumed == before[4] + len(other_nodes)
    assert owner._reasons[NON_PELVIS_IMU_REASON] == len(other_nodes)
    assert tuple(row.node_id for row in owner._nonpelvis_imu_audit) == other_nodes

    later = _imu(
        800, clock, node=PELVIS_NODE,
        timer=after.imu_timer2.trigger_timer2_us + 5_000,
    )
    _feed(owner, [later])
    assert owner._trajectory_count == len(before[3]) + 1
    assert owner._events_consumed == before[4] + len(other_nodes) + 1


def test_grouped_imu_record_uses_measurement_axis_and_shared_availability():
    static, clock, imus, bootstrap, after = _session()
    owner = FullSessionRootABCoordinator(static=static, clock_owner=clock)
    _feed(owner, [*imus, bootstrap, after])
    assert owner.a is not None and owner.b is not None
    identities = (id(owner.orientation), id(owner.a), id(owner.b))
    count_before = owner._events_consumed
    trajectory_before = owner._trajectory_count
    gaps_before = owner.orientation.audit()["timer_gap_count"]
    first_timer = after.imu_timer2.trigger_timer2_us + 5_000
    rows = _grouped_imu_rows(clock, first_timer=first_timer)
    router = _group_router(clock)
    routed = router.route_original_record(rows)
    assert len(routed) == 2
    assert routed[0].common_global_ns != routed[1].common_global_ns
    assert routed[0].availability_global_ns == routed[1].availability_global_ns
    delivery = _FullSessionDeliveryOwner(router)
    for event in routed:
        owner.consume_ticket(delivery.issue(event))
        delivery.require_consumed()

    samples = owner._trajectory[-2:]
    expected_measurements = tuple(
        clock.binding_for(PELVIS_NODE).global_ns(row.node_timer_us) * 1e-9
        for row in rows
    )
    expected_availability = expected_measurements[-1]
    assert tuple(sample.measurement_time_s for sample in samples) == expected_measurements
    assert tuple(sample.availability_time_s for sample in samples) == (
        expected_availability, expected_availability,
    )
    assert owner.a.current_state.time_s == owner.b.current_state.time_s
    assert owner.a.current_state.time_s == expected_measurements[-1]
    assert owner._events_consumed == count_before + 2
    assert owner._trajectory_count == trajectory_before + 2
    assert (id(owner.orientation), id(owner.a), id(owner.b)) == identities
    assert owner.orientation.audit()["timer_gap_count"] == gaps_before

    later = _imu(902, clock, timer=first_timer + 10_000)
    _feed(owner, [later])
    assert owner._events_consumed == count_before + 3
    assert owner._trajectory_count == trajectory_before + 3
    assert owner._trajectory[-1].measurement_time_s > samples[-1].measurement_time_s


def test_bootstrap_cross_binds_uint32_sweep_separately_from_packet_sequence():
    static, clock, imus, _bootstrap, _after = _session()
    owner = FullSessionRootABCoordinator(static=static, clock_owner=clock)
    timer = imus[-1].imu_timer2.trigger_timer2_us
    bootstrap = _uwb(70_377, clock, static, timer=timer)
    assert bootstrap.payload_owner.sequence == 70_377
    assert bootstrap.payload_owner.payload["packet_sequence"] == (70_377 & 0xffff)
    _feed(owner, [*imus, bootstrap])
    assert owner._bootstrap_event_id == bootstrap.event_id


def _typed_uwb_ticket(event):
    return _pending_delivery((event,)).issue(event).dispatch()


def test_bootstrap_optional_source_global_time_matches_clock_derived_authority():
    static, clock, _imus, decoder_shaped, _after = _session()
    assert decoder_shaped.payload_owner.global_time_ns is None
    matching = replace(decoder_shaped, payload_owner=replace(
        decoder_shaped.payload_owner,
        global_time_ns=decoder_shaped.common_global_ns,
        global_time_sigma_ns=None,
    ))
    results = []
    for event in (decoder_shaped, matching):
        owner = LabelFreePelvisBootstrapOwner(static, clock)
        reason, bootstrap = owner.consider(
            _typed_uwb_ticket(event), gauge_availability_s=0.0,
        )
        assert reason == "BOOTSTRAP_ACCEPTED"
        assert bootstrap is not None
        results.append(bootstrap)
    assert results[0].event_id == results[1].event_id
    assert results[0].owner_digest == results[1].owner_digest
    assert results[0].state.vector.tobytes() == results[1].state.vector.tobytes()
    assert results[0].state.covariance.tobytes() == results[1].state.covariance.tobytes()


def test_bootstrap_present_mismatched_source_global_time_is_owner_inert():
    static, clock, _imus, decoder_shaped, _after = _session()
    poison = replace(decoder_shaped, payload_owner=replace(
        decoder_shaped.payload_owner,
        global_time_ns=decoder_shaped.common_global_ns + 1,
        global_time_sigma_ns=None,
    ))
    owner = LabelFreePelvisBootstrapOwner(static, clock)
    before = (owner._resolved, owner.owner_digest)
    with pytest.raises(ValueError, match="source event identity is inconsistent"):
        owner.consider(_typed_uwb_ticket(poison), gauge_availability_s=0.0)
    assert (owner._resolved, owner.owner_digest) == before
    reason, bootstrap = owner.consider(
        _typed_uwb_ticket(decoder_shaped), gauge_availability_s=0.0,
    )
    assert reason == "BOOTSTRAP_ACCEPTED"
    assert bootstrap is not None


def test_ordinary_uwb_rejection_is_audited_and_next_event_continues_same_owner():
    static, clock, imus, bootstrap, after = _session()
    owner = FullSessionRootABCoordinator(static=static, clock_owner=clock)
    _feed(owner, [*imus, bootstrap, after])
    trajectory_before = tuple(owner._trajectory)
    rejected = _uwb(
        600, clock, static, timer=after.imu_timer2.trigger_timer2_us - 4_000,
        truth=(20.0, 20.0, 20.0),
    )
    continuation = _uwb(
        601, clock, static, timer=after.imu_timer2.trigger_timer2_us - 3_000,
        truth=owner.b.current_state.position_m,
    )
    _feed(owner, [rejected, continuation])
    assert len(owner._uwb_audit) == 2
    assert owner._uwb_audit[0].accepted is False
    assert owner._uwb_audit[1].event_id == continuation.event_id
    assert tuple(owner._trajectory) == trajectory_before


def test_insufficient_link_audit_changes_only_counters_then_continues():
    static, clock, imus, bootstrap, after = _session()
    owner = FullSessionRootABCoordinator(static=static, clock_owner=clock)
    _feed(owner, [*imus, bootstrap, after])
    assert owner.a is not None and owner.b is not None and owner.tight is not None
    a_before = _root_bytes(owner.a)
    b_before = _root_bytes(owner.b)
    tight_before = owner.tight.owner_digest()
    trajectory_before = tuple(owner._trajectory)
    rejected = _uwb(
        610, clock, static,
        timer=after.imu_timer2.trigger_timer2_us - 4_000,
    )
    rejected = replace(rejected, payload_owner=replace(
        rejected.payload_owner,
        payload={**rejected.payload_owner.payload, "valid_mask": 0x03},
    ))
    continuation = _uwb(
        611, clock, static,
        timer=after.imu_timer2.trigger_timer2_us - 3_000,
        truth=owner.b.current_state.position_m,
    )
    _feed(owner, [rejected])
    assert _root_bytes(owner.a) == a_before
    assert _root_bytes(owner.b) == b_before
    assert owner.tight.owner_digest() == tight_before
    assert tuple(owner._trajectory) == trajectory_before
    assert owner._uwb_audit[-1].reason == "REJECT_FEWER_THAN_FOUR_LINKS"
    assert owner._reasons["REJECT_FEWER_THAN_FOUR_LINKS"] == 1

    _feed(owner, [continuation])
    assert owner._uwb_audit[-1].event_id == continuation.event_id
    assert _root_bytes(owner.a) == a_before
    assert tuple(owner._trajectory) == trajectory_before


def test_measurement_identity_and_geometry_skips_only_change_audit_counters(monkeypatch):
    static, clock, imus, bootstrap, after = _session()
    owner = FullSessionRootABCoordinator(static=static, clock_owner=clock)
    _feed(owner, [*imus, bootstrap, after])
    assert owner.a is not None and owner.b is not None and owner.tight is not None
    numerical_before = (
        _root_bytes(owner.a), _root_bytes(owner.b), owner.tight.owner_digest(),
        tuple(owner._trajectory),
    )

    identity = _uwb(
        620, clock, static,
        timer=after.imu_timer2.trigger_timer2_us - 4_000,
    )
    identity = replace(identity, payload_owner=replace(
        identity.payload_owner,
        payload={**identity.payload_owner.payload,
                 "anchor_id": [0, 1, 2, 3, 4, 5, 6, 6]},
    ))
    _feed(owner, [identity])
    assert owner._uwb_audit[-1].reason == "REJECT_ANCHOR_IDENTITY_INVALID"
    assert owner._reasons["REJECT_ANCHOR_IDENTITY_INVALID"] == 1
    assert (
        _root_bytes(owner.a), _root_bytes(owner.b), owner.tight.owner_digest(),
        tuple(owner._trajectory),
    ) == numerical_before

    geometry = _uwb(
        621, clock, static,
        timer=after.imu_timer2.trigger_timer2_us - 3_000,
    )
    original = owner.tight._likelihood

    def reject_geometry(*_args, **_kwargs):
        raise ValueError("raw likelihood position geometry is rank deficient")

    monkeypatch.setattr(owner.tight, "_likelihood", reject_geometry)
    _feed(owner, [geometry])
    assert owner._uwb_audit[-1].reason == "REJECT_RAW_GEOMETRY"
    assert owner._reasons["REJECT_RAW_GEOMETRY"] == 1
    assert (
        _root_bytes(owner.a), _root_bytes(owner.b), owner.tight.owner_digest(),
        tuple(owner._trajectory),
    ) == numerical_before
    monkeypatch.setattr(owner.tight, "_likelihood", original)


def test_label_permutation_gives_identical_whole_session_summary(monkeypatch):
    summaries = []
    for label in (0, 2):
        static, clock, imus, bootstrap, after = _session(label)
        owner = FullSessionRootABCoordinator(static=static, clock_owner=clock)
        events = [*imus, bootstrap, after]
        reader = object.__new__(FullSessionContinuousReader)

        def consume(_reader, callback, rows=events):
            delivery = _pending_delivery(rows)
            for event in rows:
                callback(delivery.issue(event))
            delivery.require_consumed()
            return _audit(len(rows))

        monkeypatch.setattr(FullSessionContinuousReader, "consume", consume)
        summaries.append(owner.run(reader))
        assert summaries[-1].events_consumed == summaries[-1].reader_audit.route_audit.event_count
    assert summaries[0] == summaries[1]


def test_action_boundary_and_real_gap_label_do_not_reset_root_or_orientation():
    static, clock, imus, bootstrap, after = _session()
    owner = FullSessionRootABCoordinator(static=static, clock_owner=clock)
    _feed(owner, [*imus, bootstrap, after])
    identities = (id(owner.a), id(owner.b), owner.orientation.owner_digest)
    consumed_before = owner._events_consumed
    trajectory_before = owner._trajectory_count
    gap = [
        _imu(
            502 + offset, clock,
            timer=after.imu_timer2.trigger_timer2_us + (offset + 1) * 5_000,
            region_id="gap_00_to_02",
        )
        for offset in range(400)
    ]
    action02 = _imu(
        902, clock, timer=gap[-1].imu_timer2.trigger_timer2_us + 5_000,
        action_index=2,
    )
    _feed(owner, [*gap, action02])
    assert (id(owner.a), id(owner.b), owner.orientation.owner_digest) == identities
    assert owner._events_consumed - consumed_before == 401
    assert owner._trajectory_count - trajectory_before == 401
    assert owner.orientation.audit()["timer_gap_count"] == 0
    assert owner.orientation.audit()["preparation_backfilled"] is False


def test_atomic_two_root_imu_failure_rolls_back_both_and_can_continue(monkeypatch):
    static, clock, imus, bootstrap, after = _session()
    owner = FullSessionRootABCoordinator(static=static, clock_owner=clock)
    _feed(owner, [*imus, bootstrap])
    before = (_root_bytes(owner.a), _root_bytes(owner.b))
    original = CausalDelayedRootFilter.commit_prepared_imu

    def commit_then_fail(root, plan):
        result = original(root, plan)
        if root is owner.b:
            raise RuntimeError("injected B failure")
        return result

    monkeypatch.setattr(CausalDelayedRootFilter, "commit_prepared_imu", commit_then_fail)
    with pytest.raises(RuntimeError, match="injected B"):
        _feed(owner, [after])
    assert (_root_bytes(owner.a), _root_bytes(owner.b)) == before
    monkeypatch.setattr(CausalDelayedRootFilter, "commit_prepared_imu", original)
    later = _imu(502, clock, timer=after.imu_timer2.trigger_timer2_us + 5_000)
    _feed(owner, [later])
    assert owner._trajectory_count == 1


def test_incomplete_stream_fails_closed(monkeypatch):
    static, clock, imus, _bootstrap, _after = _session()
    owner = FullSessionRootABCoordinator(static=static, clock_owner=clock)
    reader = object.__new__(FullSessionContinuousReader)

    def consume(_reader, callback):
        delivery = _pending_delivery(imus[:3])
        for event in imus[:3]:
            callback(delivery.issue(event))
        return _audit(3)

    monkeypatch.setattr(FullSessionContinuousReader, "consume", consume)
    with pytest.raises(RuntimeError, match="ended without label-free bootstrap"):
        owner.run(reader)


def test_coordinator_import_and_control_closure_forbids_action_specific_paths():
    import biospur_fusion.c2_uwb_root_world.full_session_root_ab_coordinator as module
    tree = ast.parse(Path(module.__file__).read_text())
    imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = ("action00", "epoch_assembler", "shared_root_ab", "h01", "consensus")
    assert not any(any(value in name.lower() for value in forbidden) for name in imports)
    source = Path(module.__file__).read_text()
    assert ".action_index" not in source and ".action_id" not in source
