from __future__ import annotations

from collections import Counter
import hashlib
import pickle
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
import biospur_fusion.c2_coupled_progressive.continuous_full_session_reader as reader_module

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    NodeClockBinding,
    validate_event_clock,
)
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionEventRouter,
    _OwnedEventAttestation,
    _FullSessionDeliveryOwner,
    _FullSessionRecordDeliveryOwner,
    _event_digest,
    _event_projection,
    _event_structural_identity,
    _sensor_identity_digest,
    _structural_identity,
)
from biospur_fusion.c2_coupled_progressive.continuous_stage2_adapter import (
    EventRegionOwner,
    FullSessionSourceEnvelope,
    adapt_verified_record,
    adapt_verified_record_batch,
)
from biospur_fusion.ingest.events import (
    EventStatus,
    RawByteProvenance,
    RecordType,
    TypedEvent,
)


def _clock() -> ContinuousClockOwner:
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, (
        NodeClockBinding(
            "BSFC2CC", 7, "B306_TIMER2", "1" * 64, 1_000.0, 0.0,
            "2" * 64, "3" * 64,
        ),
    ))


def _router(action_index: int = 0) -> FullSessionEventRouter:
    router = object.__new__(FullSessionEventRouter)
    router.inventory = SimpleNamespace(reporting_label_permutation=action_index)
    router.clock_owner = _clock()
    router._regions = ()
    router._region_starts = ()
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
    router._pending_attestations = ()
    router._pending_raw_identity = None
    router._pending_cursor = 0
    router._delivery_authority = None
    router._attestation_authority = object()
    router._source_partition = lambda _row: SimpleNamespace(region_id="synthetic")
    router._session_owner = EventRegionOwner(
        full_session=FullSessionSourceEnvelope(),
    )
    return router


def _uwb(
    sweep: int, packet_sequence: int, *, timer: int = 10_000,
    frame_timer: int | None = None,
) -> TypedEvent:
    frame_timer = timer + 1_000 if frame_timer is None else frame_timer
    return TypedEvent(
        "BSFC2CC", 7, RecordType.UWB, sweep, timer, None, None, 0,
        {"sweep": sweep, "packet_sequence": packet_sequence,
         "strobe_us": timer, "frame_us": frame_timer},
        {}, EventStatus.DECODED,
        RawByteProvenance(1, 10, 20, "4" * 64, 0),
    )


def _imu(sequence: int, timer: int, sample: int) -> TypedEvent:
    return TypedEvent(
        "BSFC2CC", 7, RecordType.IMU, sequence, timer, None, None, 0,
        {"base_timer2_us": 10_000, "delta_us": timer - 10_000,
         "acc_raw": [2048, 0, 0], "gyro_raw": [0, 0, 0]},
        {}, EventStatus.DECODED,
        RawByteProvenance(1, 10, 20, "5" * 64, sample),
    )


def _owner_bytes(router: FullSessionEventRouter) -> bytes:
    return pickle.dumps((
        router._event_count, router._counts, router._imu_counts,
        router._last_timer, router._last_sequence, router._last_availability,
        router._last_raw_rank, router._exact,
        router._last_ordering, router._global_source_availability_ns,
        router._minimum_sensor_ready_lower_bound_ns,
        router._maximum_sensor_ready_lower_bound_ns,
        router._lifted_record_count, router._maximum_availability_lift_ns,
        router._total_availability_lift_ns,
        router._dropouts, router._identity.copy().digest(),
        router._maximum_owner_watermark_entries,
        router._pending_event_high_water,
        router._pending_events, router._pending_event_digests,
        router._pending_sensor_digests, router._pending_attestations,
        router._pending_raw_identity,
        router._pending_cursor,
    ), protocol=5)


def _route(
    router: FullSessionEventRouter, rows: tuple[TypedEvent, ...],
) -> tuple:
    delivery = getattr(router, "_test_delivery", None)
    if delivery is None:
        delivery = _FullSessionDeliveryOwner(router)
        router._test_delivery = delivery
    events = router.route_original_record(rows)
    for event in events:
        delivery.issue(event).deliver(lambda _event: None)
        delivery.require_consumed()
    delivery.require_record_consumed()
    return events


def test_record_adapter_matches_scalar_and_resolves_clock_once_per_record() -> None:
    class CountingBinding:
        def __init__(self, binding):
            self.binding = binding
            self.global_calls = 0

        def global_ns(self, timer):
            self.global_calls += 1
            return self.binding.global_ns(timer)

        def __getattr__(self, name):
            return getattr(self.binding, name)

    class CountingOwner:
        def __init__(self):
            self.binding = CountingBinding(_clock().binding_for("BSFC2CC"))
            self.binding_calls = 0

        def binding_for(self, node):
            assert node == "BSFC2CC"
            self.binding_calls += 1
            return self.binding

    region = EventRegionOwner(full_session=FullSessionSourceEnvelope())
    real_clock = _clock()
    for count in (10, 16):
        rows = tuple(_imu(index, 10_000 + index * 5_000, index)
                     for index in range(count))
        availability = real_clock.binding_for("BSFC2CC").global_ns(
            rows[-1].node_timer_us)
        scalar = tuple(adapt_verified_record(
            row, availability_global_ns=availability,
            region_owner=region, clock_owner=real_clock,
        ) for row in rows)
        counted = CountingOwner()
        batch, sensor_ready, published = adapt_verified_record_batch(
            rows, previous_global_source_availability_ns=None,
            region_owner=region, clock_owner=counted,
        )
        assert batch == scalar
        assert sensor_ready == published == availability
        assert counted.binding_calls == 1
        assert counted.binding.global_calls == count
        assert tuple(_event_digest(event) for event in batch) == tuple(
            _event_digest(event) for event in scalar)
        assert tuple(_sensor_identity_digest(event) for event in batch) == tuple(
            _sensor_identity_digest(event) for event in scalar)
        assert tuple(_event_structural_identity(event) for event in batch) == tuple(
            _event_structural_identity(event) for event in scalar)

    uwb = _uwb(8, 9, timer=50_000, frame_timer=51_000)
    counted = CountingOwner()
    batch, sensor_ready, published = adapt_verified_record_batch(
        (uwb,), previous_global_source_availability_ns=None,
        region_owner=region, clock_owner=counted,
    )
    scalar = adapt_verified_record(
        uwb, availability_global_ns=sensor_ready,
        region_owner=region, clock_owner=real_clock,
    )
    assert batch == (scalar,) and published == sensor_ready
    assert counted.binding_calls == 1 and counted.binding.global_calls == 2
    assert _event_digest(batch[0]) == _event_digest(scalar)
    assert _sensor_identity_digest(batch[0]) == _sensor_identity_digest(scalar)


def test_scalar_uwb_frame_window_is_compatible_but_validators_reject_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    region = EventRegionOwner(full_session=FullSessionSourceEnvelope())
    clock = _clock()
    row = _uwb(12, 13, timer=50_000, frame_timer=51_000)
    binding = clock.binding_for(row.node_id)
    common = binding.global_ns(row.node_timer_us)
    mapped_frame = binding.global_ns(row.payload["frame_us"])
    event = adapt_verified_record(
        row, availability_global_ns=common,
        region_owner=region, clock_owner=clock,
    )
    assert event.availability_global_ns == common < mapped_frame
    with pytest.raises(ValueError, match="^UWB availability precedes mapped source frame$"):
        validate_event_clock(event, clock)

    router = _router()
    before = _owner_bytes(router)
    monkeypatch.setattr(
        reader_module, "adapt_verified_record_batch",
        lambda *_args, **_kwargs: ((event,), mapped_frame, common),
    )
    with pytest.raises(ValueError, match="^UWB availability precedes mapped source frame$"):
        router.route_original_record((row,))
    assert _owner_bytes(router) == before


def test_record_batch_is_bounded_at_16_and_preserves_within_record_order() -> None:
    rows = tuple(_imu(index, 10_000 + index * 5_000, index) for index in range(16))
    router = _router()
    delivery = _FullSessionRecordDeliveryOwner(router)
    events = router.route_original_record(rows)
    seen = []
    delivery.issue().deliver(lambda batch: seen.extend(
        event.payload_owner.raw.sample_index for event in batch))
    delivery.require_consumed()
    assert seen == list(range(16))
    assert router._event_count == router._pending_event_high_water == 16
    assert router._pending_events == ()

    over_bound = _router()
    before = _owner_bytes(over_bound)
    with pytest.raises(TypeError, match="empty or unowned"):
        over_bound.route_original_record(tuple(
            _imu(index, 10_000 + index * 5_000, index) for index in range(17)
        ))
    assert _owner_bytes(over_bound) == before


@pytest.mark.parametrize("sweep,packet", [(0x10000, 17), (0xffffffff, 0xfffffffe)])
def test_uwb_sweep_is_uint32_and_distinct_packet_sequence_is_owned(
    sweep: int, packet: int,
) -> None:
    event = _route(_router(), (_uwb(sweep, packet),))[0]
    assert event.payload_owner.sequence == sweep
    assert event.payload_owner.payload["sweep"] == sweep
    assert event.payload_owner.payload["packet_sequence"] == packet


@pytest.mark.parametrize("row", [
    _uwb(0x1_0000_0000, 1),
    _uwb(0x10000, -1),
    _uwb(0x10000, 0x1_0000_0000),
    _uwb(0x10000, True),
    TypedEvent(
        "BSFC2CC", 7, RecordType.UWB, 0x10000, 10_000, None, None, 0,
        {"sweep": 0x10001, "packet_sequence": 1,
         "strobe_us": 10_000, "frame_us": 11_000},
        {}, EventStatus.DECODED,
        RawByteProvenance(1, 10, 20, "4" * 64, 0),
    ),
])
def test_invalid_uwb_domain_or_sweep_mismatch_is_owner_byte_inert(row: TypedEvent) -> None:
    router = _router()
    before = _owner_bytes(router)
    with pytest.raises(ValueError, match="UWB sweep/packet sequence"):
        router.route_original_record((row,))
    assert _owner_bytes(router) == before


def test_grouped_imu_uint16_wrap_is_valid_but_out_of_range_is_inert() -> None:
    router = _router()
    events = _route(router, (_imu(0xffff, 10_000, 0), _imu(0, 15_000, 1)))
    assert tuple(event.payload_owner.sequence for event in events) == (0xffff, 0)
    poison = _imu(0x10000, 20_000, 0)
    poison = TypedEvent(**{**poison.__dict__, "raw": RawByteProvenance(
        2, 20, 30, "6" * 64, 0,
    )})
    before = _owner_bytes(router)
    with pytest.raises(ValueError, match="IMU source sequence"):
        router.route_original_record((poison,))
    assert _owner_bytes(router) == before


def test_action_label_permutation_is_absent_from_runtime_event_identity() -> None:
    source = _uwb(0x10000, 17)
    first = _route(_router(0), (source,))[0]
    second = _route(_router(2), (source,))[0]
    assert first.action_index == second.action_index == -1
    assert first.action_id == second.action_id == "FULL_SESSION_CONTINUOUS_00_TO_19"
    assert _event_digest(first) == _event_digest(second)
    assert _sensor_identity_digest(first) == _sensor_identity_digest(second)


def _crossing_router() -> FullSessionEventRouter:
    router = _router()
    router.inventory = SimpleNamespace(regions=(
        SimpleNamespace(region_id="source_partition_left", start_offset=0, stop_offset=100),
        SimpleNamespace(region_id="source_partition_right", start_offset=100, stop_offset=200),
    ))
    router._imu_counts = {
        "source_partition_left": Counter(),
        "source_partition_right": Counter(),
    }
    router._source_partition = FullSessionEventRouter._source_partition.__get__(
        router, FullSessionEventRouter,
    )
    router._regions = router.inventory.regions
    router._region_starts = tuple(row.start_offset for row in router._regions)
    router._session_owner = EventRegionOwner(
        full_session=FullSessionSourceEnvelope(),
    )
    return router


def _with_raw(
    row: TypedEvent, *, start: int, end: int, digest: str, record_index: int = 1,
) -> TypedEvent:
    return replace(row, raw=RawByteProvenance(
        record_index, start, end, digest, 0,
    ))


def test_async_rows_cross_internal_byte_time_labels_both_directions_once() -> None:
    left_byte_right_time = _with_raw(
        _uwb(1, 11, timer=11_000), start=10, end=20, digest="7" * 64,
    )
    left_router = _crossing_router()
    left_event, = _route(left_router, (left_byte_right_time,))
    assert left_event.action_id == "FULL_SESSION_CONTINUOUS_00_TO_19"
    assert left_router._counts == {"source_partition_left": 1}

    right_byte_left_time = _with_raw(
        _uwb(2, 12, timer=9_000), start=110, end=120, digest="8" * 64,
    )
    right_router = _crossing_router()
    right_event, = _route(right_router, (right_byte_left_time,))
    assert right_event.action_id == "FULL_SESSION_CONTINUOUS_00_TO_19"
    assert right_router._counts == {"source_partition_right": 1}
    assert left_router._event_count == 1
    assert right_router._event_count == 1


def test_byte_owned_rows_outside_outer_action_label_times_are_conserved_once() -> None:
    router = _crossing_router()
    below_action00_label_lo = _with_raw(
        _uwb(3, 13, timer=1_000), start=10, end=20, digest="9" * 64,
        record_index=3,
    )
    above_action19_label_hi = _with_raw(
        _uwb(4, 14, timer=30_000), start=110, end=120, digest="a" * 64,
        record_index=4,
    )
    first, = _route(router, (below_action00_label_lo,))
    second, = _route(router, (above_action19_label_hi,))
    assert first.common_global_ns == 1_000_000
    assert second.common_global_ns == 30_000_000
    assert router._counts == {
        "source_partition_left": 1,
        "source_partition_right": 1,
    }
    assert router._event_count == 2


def test_outside_whole_session_byte_envelope_is_atomic() -> None:
    router = _crossing_router()
    row = _with_raw(
        _uwb(5, 15, timer=10_000), start=200, end=210, digest="b" * 64,
    )
    before = _owner_bytes(router)
    with pytest.raises(ValueError, match="full-session byte partition"):
        router.route_original_record((row,))
    assert _owner_bytes(router) == before


@pytest.mark.parametrize("poison", [
    replace(_with_raw(
        _uwb(6, 16, timer=11_000), start=10, end=20, digest="c" * 64,
    ), boot_epoch=8),
    replace(_with_raw(
        _uwb(7, 17, timer=11_000), start=10, end=20, digest="d" * 64,
    ), global_time_ns=123),
])
def test_foreign_boot_or_clock_fact_is_owner_byte_inert(poison: TypedEvent) -> None:
    router = _crossing_router()
    before = _owner_bytes(router)
    with pytest.raises(ValueError, match="boot|global time"):
        router.route_original_record((poison,))
    assert _owner_bytes(router) == before


def test_regressing_timer_or_availability_is_owner_byte_inert() -> None:
    timer_router = _crossing_router()
    initial = _with_raw(
        _uwb(8, 18, timer=10_000), start=10, end=20, digest="e" * 64,
        record_index=8,
    )
    _route(timer_router, (initial,))
    timer_regression = _with_raw(
        _uwb(9, 19, timer=9_000, frame_timer=12_000),
        start=20, end=30, digest="f" * 64, record_index=9,
    )
    before = _owner_bytes(timer_router)
    with pytest.raises(ValueError, match="hardware time replayed or regressed"):
        timer_router.route_original_record((timer_regression,))
    assert _owner_bytes(timer_router) == before

    availability_router = _crossing_router()
    delayed = _with_raw(
        _uwb(10, 20, timer=10_000, frame_timer=20_000),
        start=10, end=20, digest="1" * 64, record_index=10,
    )
    _route(availability_router, (delayed,))
    availability_regression = _with_raw(
        _imu(11, 12_000, 0),
        start=20, end=30, digest="2" * 64, record_index=11,
    )
    lifted, = _route(availability_router, (availability_regression,))
    assert lifted.availability_global_ns == 20_000_000
    assert lifted.common_global_ns == 12_000_000
    assert lifted.payload_owner.raw == availability_regression.raw
    assert availability_router._lifted_record_count == 1
    assert availability_router._maximum_availability_lift_ns == 8_000_000
    assert availability_router._total_availability_lift_ns == 8_000_000

    direct, = _route(_crossing_router(), (availability_regression,))
    assert direct.common_global_ns == lifted.common_global_ns
    assert direct.payload_owner == lifted.payload_owner
    assert _event_digest(direct) != _event_digest(lifted)
    assert _sensor_identity_digest(direct) != _sensor_identity_digest(lifted)


def test_cross_node_sensor_ready_regression_is_lifted_in_source_order() -> None:
    router = _crossing_router()
    second = NodeClockBinding(
        "BSF0001", 7, "B306_TIMER2", "6" * 64, 1_000.0, 0.0,
        "2" * 64, "3" * 64,
    )
    router.clock_owner = ContinuousClockOwner(
        CONTINUOUS_FRONTEND_SCHEMA, router.clock_owner.bindings + (second,),
    )
    first_row = _with_raw(
        _uwb(12, 22, timer=10_000, frame_timer=20_000),
        start=10, end=20, digest="7" * 64, record_index=12,
    )
    second_row = replace(_with_raw(
        _imu(12, 12_000, 0), start=20, end=30,
        digest="8" * 64, record_index=13,
    ), node_id="BSF0001")
    first, = _route(router, (first_row,))
    second_event, = _route(router, (second_row,))
    assert first.availability_global_ns == second_event.availability_global_ns == 20_000_000
    assert second_event.common_global_ns == 12_000_000
    assert first.payload_owner.raw.end_offset == second_event.payload_owner.raw.start_offset
    assert router._lifted_record_count == 1


def test_raw_rank_regression_is_rejected_before_any_owner_mutation() -> None:
    router = _router()
    _route(router, (_with_raw(
        _uwb(20, 30, timer=20_000), start=100, end=120,
        digest="a" * 64, record_index=20,
    ),))
    before = _owner_bytes(router)
    regressed = _with_raw(
        _uwb(21, 31, timer=30_000), start=120, end=140,
        digest="b" * 64, record_index=19,
    )
    with pytest.raises(ValueError, match="raw rank replayed or regressed"):
        router.route_original_record((regressed,))
    assert _owner_bytes(router) == before


def test_mid_record_failure_is_byte_inert_after_a_valid_staged_sample() -> None:
    router = _router()
    first = _with_raw(
        _imu(100, 10_000, 0), start=100, end=140,
        digest="c" * 64, record_index=100,
    )
    second = replace(_with_raw(
        _imu(101, 15_000, 1), start=100, end=140,
        digest="c" * 64, record_index=100,
    ), sequence=0x1_0000)
    before = _owner_bytes(router)
    with pytest.raises(ValueError, match="IMU source sequence"):
        router.route_original_record((first, second))
    assert _owner_bytes(router) == before


def _run_fixed_memory_records(count: int) -> tuple[float, int, tuple[int, ...]]:
    router = _router()
    started = time.perf_counter()
    for ordinal in range(count):
        row = _with_raw(
            _uwb(ordinal, ordinal, timer=10_000 + ordinal * 2_000),
            start=ordinal * 20, end=ordinal * 20 + 10,
            digest=f"{ordinal + 1:064x}", record_index=ordinal + 1,
        )
        _route(router, (row,))
    elapsed = time.perf_counter() - started
    shape = (
        len(router._last_timer), len(router._last_sequence),
        len(router._last_availability), len(router._last_ordering),
        len(router._pending_events), len(router._pending_event_digests),
        len(router._pending_sensor_digests),
        router._maximum_owner_watermark_entries,
        router._pending_event_high_water,
    )
    assert not hasattr(router, "_ids") and not hasattr(router, "_digests")
    return elapsed, len(_owner_bytes(router)), shape


def test_large_n_router_runtime_is_linear_and_owner_storage_is_history_free() -> None:
    small_s, small_bytes, small_shape = _run_fixed_memory_records(300)
    large_s, large_bytes, large_shape = _run_fixed_memory_records(1_200)
    assert large_s <= small_s * 8.0 + 0.05
    # Only fixed scalar integer encodings may widen; no history container exists.
    assert large_bytes <= small_bytes + 16
    assert large_shape == small_shape == (1, 1, 1, 1, 0, 0, 0, 5, 1)


def test_each_event_hashes_two_projections_once_and_never_hashes_at_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"event": 0, "sensor": 0, "structural": 0}
    original_event = reader_module._event_digest
    original_sensor = reader_module._sensor_identity_digest
    original_structural = reader_module._event_structural_identity

    def counted_event(event):
        calls["event"] += 1
        return original_event(event)

    def counted_sensor(event):
        calls["sensor"] += 1
        return original_sensor(event)

    def counted_structural(event):
        calls["structural"] += 1
        return original_structural(event)

    monkeypatch.setattr(reader_module, "_event_digest", counted_event)
    monkeypatch.setattr(reader_module, "_sensor_identity_digest", counted_sensor)
    monkeypatch.setattr(reader_module, "_event_structural_identity", counted_structural)
    router = _router()
    delivery = _FullSessionRecordDeliveryOwner(router)
    events = router.route_original_record((
        _imu(0xffff, 10_000, 0), _imu(0, 15_000, 1),
    ))
    assert calls == {"event": 2, "sensor": 2, "structural": 2}
    monkeypatch.setattr(reader_module, "_event_digest",
                        lambda _event: pytest.fail("delivery rehashed event JSON"))
    monkeypatch.setattr(reader_module, "_sensor_identity_digest",
                        lambda _event: pytest.fail("delivery rehashed sensor JSON"))
    ticket = delivery.issue()
    assert calls == {"event": 2, "sensor": 2, "structural": 2}
    ticket.deliver(lambda batch: None)
    delivery.require_consumed()
    assert calls == {"event": 2, "sensor": 2, "structural": 4}


def test_bisected_region_owner_matches_all_37_contiguous_boundaries() -> None:
    router = _router()
    router._regions = tuple(
        SimpleNamespace(region_id=f"r{index}", start_offset=index * 100,
                        stop_offset=(index + 1) * 100)
        for index in range(37)
    )
    router._region_starts = tuple(row.start_offset for row in router._regions)
    router._source_partition = FullSessionEventRouter._source_partition.__get__(
        router, FullSessionEventRouter,
    )
    for index, region in enumerate(router._regions):
        row = _with_raw(_uwb(index, index), start=region.start_offset,
                        end=region.stop_offset, digest=f"{index + 1:064x}")
        assert router._source_partition(row) is region
        if index + 1 < len(router._regions):
            crossing = _with_raw(
                _uwb(index, index), start=region.stop_offset - 1,
                end=region.stop_offset + 1, digest=f"{index + 1:064x}",
            )
            with pytest.raises(ValueError, match="partition ownership"):
                router._source_partition(crossing)
