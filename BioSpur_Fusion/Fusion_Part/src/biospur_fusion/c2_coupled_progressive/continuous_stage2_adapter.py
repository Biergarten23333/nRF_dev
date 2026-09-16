"""Stateless translation from verified v47 records to continuous envelopes.

The caller supplies an already frozen action or gap owner.  This adapter does
not infer actions, fit clocks, select results, or retain estimator state.
"""

from __future__ import annotations

from dataclasses import dataclass

from biospur_fusion.ingest.events import EventStatus, RecordType, TypedEvent

from .continuous_frontend import (
    ActionInterval,
    ContinuousClockOwner,
    ContinuousEvent,
    ImuTimer2Fields,
    SourceBoundGap,
    UwbTimer2Fields,
)


@dataclass(frozen=True)
class EventRegionOwner:
    action: ActionInterval | None = None
    gap: SourceBoundGap | None = None
    full_session: FullSessionSourceEnvelope | None = None

    def __post_init__(self) -> None:
        if sum(value is not None for value in (
            self.action, self.gap, self.full_session,
        )) != 1:
            raise ValueError("event requires exactly one action, gap, or full-session owner")


@dataclass(frozen=True)
class FullSessionSourceEnvelope:
    """Label-free source owner for one authenticated contiguous byte window."""

    region_id: str = "FULL_SESSION_CONTINUOUS_00_TO_19"

    def __post_init__(self) -> None:
        if self.region_id != "FULL_SESSION_CONTINUOUS_00_TO_19":
            raise ValueError("invalid label-free full-session source envelope")


def adapt_verified_record(
    record: TypedEvent,
    *,
    availability_global_ns: int,
    region_owner: EventRegionOwner,
    clock_owner: ContinuousClockOwner,
) -> ContinuousEvent:
    """Translate one decoded record without deriving time or action ownership."""
    binding = clock_owner.binding_for(record.node_id)
    common_ns, frame_global_ns = _validate_and_map_record(record, binding)
    return _build_event(record, common_ns=common_ns,
                        frame_global_ns=frame_global_ns,
                        availability_global_ns=availability_global_ns,
                        region_owner=region_owner, binding=binding)


def adapt_verified_record_batch(
    records: tuple[TypedEvent, ...], *,
    previous_global_source_availability_ns: int | None,
    region_owner: EventRegionOwner,
    clock_owner: ContinuousClockOwner,
) -> tuple[tuple[ContinuousEvent, ...], int, int]:
    """Atomically map one homogeneous original record with one clock lookup."""
    if (not records or len(records) > 16
            or any(type(record) is not TypedEvent or record.raw is None
                   for record in records)):
        raise TypeError("continuous adapter batch is empty, oversized, or unowned")
    first = records[0]
    raw = first.raw
    raw_identity = (raw.record_index, raw.start_offset, raw.end_offset,
                    raw.encoded_sha256)
    if (any(record.node_id != first.node_id
            or record.record_type is not first.record_type
            or (record.raw.record_index, record.raw.start_offset,
                record.raw.end_offset, record.raw.encoded_sha256) != raw_identity
            for record in records)
            or (first.record_type is RecordType.UWB and len(records) != 1)
            or first.record_type not in (RecordType.IMU, RecordType.UWB)):
        raise ValueError("continuous adapter batch mixes raw-record ownership")
    if (previous_global_source_availability_ns is not None
            and type(previous_global_source_availability_ns) is not int):
        raise TypeError("previous global source availability must be integer nanoseconds")
    binding = clock_owner.binding_for(first.node_id)
    mapped = tuple(_validate_and_map_record(record, binding) for record in records)
    sensor_ready_lower_bound_ns = (
        max(common_ns for common_ns, _frame_ns in mapped)
        if first.record_type is RecordType.IMU else mapped[0][1]
    )
    if sensor_ready_lower_bound_ns is None:
        raise AssertionError("UWB frame mapping is unavailable")
    availability_global_ns = max(
        sensor_ready_lower_bound_ns,
        sensor_ready_lower_bound_ns if previous_global_source_availability_ns is None
        else previous_global_source_availability_ns,
    )
    events = tuple(_build_event(
        record, common_ns=common_ns, frame_global_ns=frame_ns,
        availability_global_ns=availability_global_ns,
        region_owner=region_owner, binding=binding,
    ) for record, (common_ns, frame_ns) in zip(records, mapped))
    return events, sensor_ready_lower_bound_ns, availability_global_ns


def _validate_and_map_record(record: TypedEvent, binding) -> tuple[int, int | None]:
    if record.status is not EventStatus.DECODED:
        raise ValueError("only decoded measurement records can be adapted")
    if record.record_type not in (RecordType.IMU, RecordType.UWB):
        raise ValueError("continuous adapter accepts only IMU or raw UWB records")
    if record.raw is None:
        raise ValueError("measurement record lacks raw-byte provenance")
    if record.boot_epoch != binding.boot_epoch:
        raise ValueError("decoded record boot does not match the clock owner")
    timer_us = record.node_timer_us
    common_ns = binding.global_ns(timer_us)
    if record.global_time_ns is not None and record.global_time_ns != common_ns:
        raise ValueError("supplied global time disagrees with the clock owner")
    frame_global_ns = None
    if record.record_type is RecordType.UWB:
        frame_us = record.payload.get("frame_us")
        strobe_us = record.payload.get("strobe_us")
        if type(frame_us) is not int or type(strobe_us) is not int or strobe_us != timer_us:
            raise ValueError("decoded UWB lacks matching strobe/frame TIMER2 ownership")
        frame_global_ns = binding.global_ns(frame_us)
    return common_ns, frame_global_ns


def _build_event(
    record: TypedEvent, *, common_ns: int, frame_global_ns: int | None,
    availability_global_ns: int, region_owner: EventRegionOwner, binding,
) -> ContinuousEvent:
    if type(availability_global_ns) is not int or availability_global_ns < common_ns:
        raise ValueError("invalid authoritative availability time")

    action = region_owner.action
    gap = region_owner.gap
    if action is not None:
        if not action.contains(common_ns):
            raise ValueError("action interval does not own decoded measurement")
        action_index, action_id, region_id = action.action_index, action.action_id, None
    elif gap is not None:
        if not gap.contains(common_ns):
            raise ValueError("source-bound gap does not own decoded measurement")
        action_index, action_id, region_id = -1, gap.region_id, gap.region_id
    else:
        full_session = region_owner.full_session
        if full_session is None:
            raise ValueError("full-session source envelope is missing")
        action_index = -1
        action_id = region_id = full_session.region_id

    raw = record.raw
    event_id = (
        f"v47:{raw.record_index}:{raw.sample_index}:"
        f"{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}"
    )
    common = dict(
        event_id=event_id,
        action_index=action_index,
        action_id=action_id,
        common_global_ns=common_ns,
        availability_global_ns=availability_global_ns,
        node_id=record.node_id,
        boot_epoch=record.boot_epoch,
        clock_domain=binding.clock_domain,
        clock_mapping_digest=binding.clock_mapping_digest,
        clock_owner_sha256=binding.clock_owner_sha256,
        clock_source_sha256=binding.clock_source_sha256,
        host_time_label=str(record.master_arrival_ms),
        payload_owner=record,
        region_id=region_id,
    )
    if record.record_type is RecordType.IMU:
        base = record.payload.get("base_timer2_us")
        if type(base) is not int:
            raise ValueError("decoded IMU lacks exact TIMER2 base")
        return ContinuousEvent(
            kind="IMU", imu_timer2=ImuTimer2Fields(base, record.node_timer_us), **common,
        )
    frame_us = record.payload["frame_us"]
    strobe_us = record.payload["strobe_us"]
    return ContinuousEvent(
        kind="UWB", uwb_timer2=UwbTimer2Fields(strobe_us, frame_us), **common,
    )
