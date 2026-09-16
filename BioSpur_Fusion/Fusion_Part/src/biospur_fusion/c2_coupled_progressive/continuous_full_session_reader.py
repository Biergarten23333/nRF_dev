"""One-fd authenticated decoder for the complete Capture2 calibration session."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import bisect
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Callable, Mapping

from biospur_fusion.c2_uwb_root_world.continuous_full_session import (
    FULL_SESSION_SCHEMA,
    ContinuousRegion,
    ContinuousSessionInventory,
    load_continuous_session_inventory,
)
from biospur_fusion.ingest.events import RecordType, TypedEvent

from .continuous_frontend import (
    ContinuousClockOwner,
    ContinuousEvent,
    continuous_clock_owner_digest,
    validate_precomputed_uwb_frame_availability,
)
from .continuous_stage2_adapter import (
    EventRegionOwner,
    FullSessionSourceEnvelope,
    adapt_verified_record_batch,
)
from .continuous_streaming_runner import AuthorizedByteWindow, IncrementalV47WindowDecoder


ROLE = "CAPTURE2_COMPLETE_CALIBRATION_CONTINUOUS_SOURCE"
SOURCE_SHA256 = "74c1fdbbe7c302bc21b0665bff50137e84537946a347ea11133e1e6751c84268"
SOURCE_STAT_IDENTITY = (2097, 5_266_758, 305_368_868, 1_786_977_119_411_212_631)
FORMAL_MANIFEST_SHA256 = "c2682d5dad06feb4edea801ebc8981c324d20c2c0f515be9de33ce03ac743417"
RAW_RELATIVE = Path(
    "datasets/phase2_calibration/"
    "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/"
    "system/fusion_continuous/fusion_host_raw.cobs.bin"
)
FORMAL_MANIFEST_RELATIVE = Path(
    "datasets/phase2_calibration/"
    "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/"
    "checksums/SHA256SUMS.txt"
)
_MANIFEST_RAW_NAME = "system/fusion_continuous/fusion_host_raw.cobs.bin"
HASH_EVIDENCE_RELATIVE = Path(
    "logs/c2_continuous_root_ab_full_repaired_20260908T094802Z/full_run/RESULT.json"
)
HASH_EVIDENCE_SHA256 = "ca3fe1fcddf50b44fd1b0bd13317dbd727ff4b2e8d921200c5924a3f1aa91e08"
FULL_WINDOW_SHA256 = "96367a10ed96f78adae3d92d504fd2b5f24cf238d91526d5dee8014c98d9b227"
_ZERO_RUN_RE = re.compile(b"\x00+")


def _raw_key(row: TypedEvent) -> tuple[int, int, int, str]:
    if row.raw is None:
        raise ValueError("full-session decoder lost raw identity")
    return (row.raw.record_index, row.raw.start_offset, row.raw.end_offset,
            row.raw.encoded_sha256)


def _validate_source_sequence(row: TypedEvent) -> None:
    if row.record_type is RecordType.IMU:
        if type(row.sequence) is not int or not 0 <= row.sequence <= 0xffff:
            raise ValueError("full-session IMU source sequence is not uint16")
        return
    if row.record_type is RecordType.UWB:
        sweep = row.payload.get("sweep")
        packet_sequence = row.payload.get("packet_sequence")
        if (
            type(row.sequence) is not int
            or not 0 <= row.sequence <= 0xffffffff
            or type(sweep) is not int
            or sweep != row.sequence
            or type(packet_sequence) is not int
            or not 0 <= packet_sequence <= 0xffffffff
        ):
            raise ValueError("full-session UWB sweep/packet sequence is invalid")
        return
    raise ValueError("full-session record has no sequence domain")


def _event_projection(event: ContinuousEvent, *, sensor_only: bool) -> dict[str, object]:
    row = event.payload_owner
    if type(row) is not TypedEvent or row.raw is None:
        raise TypeError("full-session event lacks original TypedEvent identity")
    raw = row.raw
    event_fields = (
        (event.event_id, event.kind, event.common_global_ns,
         event.availability_global_ns, event.node_id, event.boot_epoch,
         event.clock_domain, event.clock_mapping_digest,
         event.clock_owner_sha256, event.clock_source_sha256)
        if sensor_only else
        (event.event_id, event.kind, event.action_index, event.action_id,
         event.region_id, event.common_global_ns, event.availability_global_ns,
                  event.node_id, event.boot_epoch, event.clock_domain,
                  event.clock_mapping_digest, event.clock_owner_sha256,
                  event.clock_source_sha256, event.host_time_label)
    )
    return {
        "event": event_fields + (
                  None if event.imu_timer2 is None else (
                      event.imu_timer2.timer2_base_us,
                      event.imu_timer2.trigger_timer2_us,
                  ),
                  None if event.uwb_timer2 is None else (
                      event.uwb_timer2.strobe_timer2_us,
                      event.uwb_timer2.frame_timer2_us,
                  )),
        "record": (row.node_id, row.boot_epoch, row.record_type.name, row.sequence,
                   row.node_timer_us, row.status.name, row.payload),
        "raw": (raw.record_index, raw.sample_index, raw.start_offset,
                raw.end_offset, raw.encoded_sha256),
    }


def _event_digest(event: ContinuousEvent) -> str:
    return hashlib.sha256(json.dumps(_event_projection(event, sensor_only=False),
                                     sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _sensor_identity_digest(event: ContinuousEvent) -> str:
    """Digest source/timing/payload identity while excluding reporting labels."""

    return hashlib.sha256(json.dumps(_event_projection(event, sensor_only=True),
                                     sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _structural_identity(value: object) -> object:
    if value is None:
        return ("NoneType", None)
    if isinstance(value, bool):
        return ("bool", bool(value))
    if isinstance(value, int):
        return ("int", int(value))
    if isinstance(value, float):
        return ("float", float(value).hex())
    if isinstance(value, str):
        return ("str", str(value))
    if type(value) in (list, tuple):
        return ("array", tuple(_structural_identity(item) for item in value))
    if type(value) is dict:
        return ("object", tuple(
            (key, _structural_identity(item)) for key, item in sorted(value.items())
        ))
    raise TypeError("full-session identity contains a non-canonical value")


def _event_structural_identity(event: ContinuousEvent) -> object:
    return _structural_identity(_event_projection(event, sensor_only=False))


@dataclass(frozen=True)
class _OwnedEventAttestation:
    owner_authority: object
    structural_identity: object
    event_digest: str
    sensor_identity_digest: str


def _completed_nonempty(payload: bytes, pending: bool) -> tuple[int, bool]:
    if not payload:
        return 0, pending
    collapsed, zero_runs = _ZERO_RUN_RE.subn(b"\x00", payload)
    del collapsed
    count = zero_runs - int(payload[0] == 0 and not pending)
    return count, payload[-1] != 0


def _count_nonempty_cobs_prefix(
    source, *, stop_offset: int, chunk_bytes: int,
) -> tuple[int, int]:
    """Count source-container record ordinals without decoding the prefix."""

    if (type(stop_offset) is not int or stop_offset < 0
            or type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 1 << 20
            or source.tell() != 0):
        raise ValueError("invalid canonical COBS prefix pass")
    count = consumed = 0
    pending = False
    while consumed < stop_offset:
        block = source.read(min(chunk_bytes, stop_offset - consumed))
        if not block:
            raise OSError("source ended inside ordinal prefix")
        consumed += len(block)
        added, pending = _completed_nonempty(block, pending)
        count += added
    if pending:
        raise ValueError("full-session start cuts a COBS record")
    return count, consumed


@dataclass(frozen=True)
class FullSessionRouteAudit:
    event_count: int
    events_by_region: Mapping[str, int]
    imu_by_region_and_node: Mapping[str, Mapping[str, int]]
    exact_5ms_imu_edges: int
    imu_dropout_edges: int
    minimum_sensor_ready_lower_bound_ns: int
    maximum_sensor_ready_lower_bound_ns: int
    lifted_record_count: int
    maximum_availability_lift_ns: int
    total_availability_lift_ns: int
    maximum_owner_watermark_entries: int
    pending_event_high_water: int
    identity_sha256: str


@dataclass(frozen=True)
class FullSessionInputOrderingRecord:
    node_id: str
    record_type: str
    source_sequence: int
    node_timer_us: int
    uwb_frame_us: int | None
    uwb_strobe_us: int | None
    availability_global_ns: int
    record_index: int
    start_offset: int
    end_offset: int
    region_identity: str
    input_ordering_key: tuple[int, int, int, int, str]


@dataclass(frozen=True)
class FullSessionAvailabilityRegressionDiagnostic:
    previous: FullSessionInputOrderingRecord
    current: FullSessionInputOrderingRecord
    delta_ns: int
    classification: str


class FullSessionAvailabilityRegression(ValueError):
    """Fail-closed monotonicity error carrying immutable adjacent context."""

    def __init__(self, diagnostic: FullSessionAvailabilityRegressionDiagnostic) -> None:
        super().__init__("full-session availability regressed")
        self.diagnostic = diagnostic


def _ordering_record(
    event: ContinuousEvent, row: TypedEvent, *, availability_global_ns: int,
    region_identity: str,
) -> FullSessionInputOrderingRecord:
    if row.raw is None:
        raise ValueError("full-session record lacks raw identity")
    frame_us = strobe_us = None
    if row.record_type is RecordType.UWB:
        frame_us = row.payload.get("frame_us")
        strobe_us = row.payload.get("strobe_us")
        if type(frame_us) is not int or type(strobe_us) is not int:
            raise ValueError("full-session UWB lacks canonical timer identity")
    raw = row.raw
    return FullSessionInputOrderingRecord(
        event.node_id, row.record_type.name, row.sequence, row.node_timer_us,
        frame_us, strobe_us, availability_global_ns,
        raw.record_index, raw.start_offset, raw.end_offset, region_identity,
        (raw.record_index, raw.sample_index, raw.start_offset, raw.end_offset,
         raw.encoded_sha256),
    )


class FullSessionEventRouter:
    """Admit records to one session envelope; byte partitions are audit-only."""

    def __init__(self, inventory: ContinuousSessionInventory,
                 clock_owner: ContinuousClockOwner) -> None:
        if type(inventory) is not ContinuousSessionInventory:
            raise TypeError("full-session router requires typed inventory")
        if type(clock_owner) is not ContinuousClockOwner or len(clock_owner.bindings) != 10:
            raise TypeError("full-session router requires ten-node clock owner")
        self.inventory = inventory
        self.clock_owner = clock_owner
        self._regions = inventory.regions
        self._region_starts = tuple(row.start_offset for row in self._regions)
        self._event_count = 0
        self._counts: Counter[str] = Counter()
        self._imu_counts = {row.region_id: Counter() for row in inventory.regions}
        self._last_timer: dict[tuple[str, RecordType], int] = {}
        self._last_sequence: dict[tuple[str, RecordType], tuple[int, ...]] = {}
        self._last_availability: dict[str, int] = {}
        self._last_ordering: dict[str, FullSessionInputOrderingRecord] = {}
        self._last_raw_rank: tuple[int, int, int, str] | None = None
        self._global_source_availability_ns: int | None = None
        self._minimum_sensor_ready_lower_bound_ns: int | None = None
        self._maximum_sensor_ready_lower_bound_ns: int | None = None
        self._lifted_record_count = 0
        self._maximum_availability_lift_ns = 0
        self._total_availability_lift_ns = 0
        self._exact = 0
        self._dropouts = 0
        self._identity = hashlib.sha256()
        self._maximum_owner_watermark_entries = 0
        self._pending_event_high_water = 0
        self._pending_events: tuple[ContinuousEvent, ...] = ()
        self._pending_event_digests: tuple[str, ...] = ()
        self._pending_sensor_digests: tuple[str, ...] = ()
        self._pending_attestations: tuple[_OwnedEventAttestation, ...] = ()
        self._pending_raw_identity: tuple[int, int, int, str] | None = None
        self._pending_cursor = 0
        self._delivery_authority: object | None = None
        self._attestation_authority = object()
        self._session_owner = EventRegionOwner(
            full_session=FullSessionSourceEnvelope(),
        )

    def _source_partition(self, row: TypedEvent) -> ContinuousRegion:
        """Locate the authenticated byte slice without consulting action time."""

        if row.raw is None:
            raise ValueError("full-session record lacks raw identity")
        index = bisect.bisect_right(self._region_starts, row.raw.start_offset) - 1
        if index < 0 or index >= len(self._regions):
            raise ValueError("full-session byte partition ownership mismatch")
        region = self._regions[index]
        if not (region.start_offset <= row.raw.start_offset
                < row.raw.end_offset <= region.stop_offset):
            raise ValueError("full-session byte partition ownership mismatch")
        return region

    def route_original_record(self, rows: tuple[TypedEvent, ...]) -> tuple[ContinuousEvent, ...]:
        if self._pending_cursor != len(self._pending_events):
            raise RuntimeError("previous full-session raw record was not consumed")
        if (not rows or len(rows) > 16
                or any(type(row) is not TypedEvent or row.raw is None for row in rows)):
            raise TypeError("full-session source record is empty or unowned")
        if len({_raw_key(row) for row in rows}) != 1 or len({row.node_id for row in rows}) != 1:
            raise ValueError("full-session record identity is mixed")
        raw_rank = _raw_key(rows[0])
        if self._last_raw_rank is not None and raw_rank <= self._last_raw_rank:
            raise ValueError("full-session raw rank replayed or regressed")
        source_partition = self._source_partition(rows[0])
        kinds = {row.record_type for row in rows}
        if kinds == {RecordType.IMU}:
            pass
        elif kinds == {RecordType.UWB} and len(rows) == 1:
            frame_us = rows[0].payload.get("frame_us")
            if type(frame_us) is not int:
                raise ValueError("full-session UWB lacks frame availability")
        else:
            raise ValueError("full-session record mixes sensor kinds")
        adapted, sensor_ready_lower_bound_ns, availability = (
            adapt_verified_record_batch(
                rows,
                previous_global_source_availability_ns=(
                    self._global_source_availability_ns
                ),
                region_owner=self._session_owner, clock_owner=self.clock_owner,
            )
        )
        if kinds == {RecordType.UWB}:
            validate_precomputed_uwb_frame_availability(
                adapted[0], sensor_ready_lower_bound_ns,
            )
        availability_lift_ns = availability - sensor_ready_lower_bound_ns
        staged: list[ContinuousEvent] = []
        event_digests: list[str] = []
        sensor_digests: list[str] = []
        attestations: list[_OwnedEventAttestation] = []
        timers = dict(self._last_timer)
        sequences = dict(self._last_sequence)
        availabilities = dict(self._last_availability)
        orderings = dict(self._last_ordering)
        exact, dropouts, identity = self._exact, self._dropouts, self._identity.copy()
        region_deltas: Counter[str] = Counter()
        imu_deltas: Counter[tuple[str, str]] = Counter()
        record_event_ids: set[str] = set()
        for row, event in zip(rows, adapted):
            _validate_source_sequence(row)
            if event.event_id in record_event_ids:
                raise ValueError("full-session event replay inside raw record")
            record_event_ids.add(event.event_id)
            previous_availability = availabilities.get(event.node_id)
            current_ordering = _ordering_record(
                event, row, availability_global_ns=availability,
                region_identity=source_partition.region_id,
            )
            if previous_availability is not None and availability < previous_availability:
                previous_ordering = orderings[event.node_id]
                raise FullSessionAvailabilityRegression(
                    FullSessionAvailabilityRegressionDiagnostic(
                        previous_ordering, current_ordering,
                        availability - previous_availability,
                        ("CROSS_KIND_SAME_NODE_INTERLEAVING"
                         if previous_ordering.record_type != current_ordering.record_type
                         else "SAME_KIND_CLOCK_MAPPING_OR_SOURCE_ORDER"),
                    )
                )
            key = (event.node_id, row.record_type)
            previous_timer = timers.get(key)
            if previous_timer is not None and row.node_timer_us <= previous_timer:
                raise ValueError("full-session hardware time replayed or regressed")
            digest = _event_digest(event)
            sensor_digest = _sensor_identity_digest(event)
            attestation = _OwnedEventAttestation(
                self._attestation_authority,
                _event_structural_identity(event),
                digest, sensor_digest,
            )
            region_deltas[source_partition.region_id] += 1
            availabilities[event.node_id] = availability
            orderings[event.node_id] = current_ordering
            timers[key] = row.node_timer_us
            sequences[key] = (
                (row.sequence,)
                if row.record_type is RecordType.IMU
                else (row.sequence, int(row.payload["packet_sequence"]))
            )
            if event.kind == "IMU":
                imu_deltas[(source_partition.region_id, event.node_id)] += 1
                if previous_timer is not None:
                    if row.node_timer_us - previous_timer == 5000:
                        exact += 1
                    else:
                        dropouts += 1
            identity.update((event.event_id + "\n").encode("ascii"))
            staged.append(event)
            event_digests.append(digest)
            sensor_digests.append(sensor_digest)
            attestations.append(attestation)
        pending_events = tuple(staged)
        pending_event_digests = tuple(event_digests)
        pending_sensor_digests = tuple(sensor_digests)
        pending_attestations = tuple(attestations)
        # No validation follows this point: publish the complete raw record atomically.
        for region_id, count in region_deltas.items():
            self._counts[region_id] += count
        for (region_id, node_id), count in imu_deltas.items():
            self._imu_counts[region_id][node_id] += count
        self._event_count += len(staged)
        self._last_timer = timers
        self._last_sequence = sequences
        self._last_availability = availabilities
        self._last_ordering = orderings
        self._last_raw_rank = raw_rank
        self._global_source_availability_ns = availability
        self._minimum_sensor_ready_lower_bound_ns = (
            sensor_ready_lower_bound_ns
            if self._minimum_sensor_ready_lower_bound_ns is None
            else min(self._minimum_sensor_ready_lower_bound_ns,
                     sensor_ready_lower_bound_ns)
        )
        self._maximum_sensor_ready_lower_bound_ns = (
            sensor_ready_lower_bound_ns
            if self._maximum_sensor_ready_lower_bound_ns is None
            else max(self._maximum_sensor_ready_lower_bound_ns,
                     sensor_ready_lower_bound_ns)
        )
        self._lifted_record_count += int(availability_lift_ns > 0)
        self._maximum_availability_lift_ns = max(
            self._maximum_availability_lift_ns, availability_lift_ns,
        )
        self._total_availability_lift_ns += availability_lift_ns
        self._exact, self._dropouts, self._identity = exact, dropouts, identity
        owner_entries = (1 + len(timers) + len(sequences)
                         + len(availabilities) + len(orderings))
        self._maximum_owner_watermark_entries = max(
            self._maximum_owner_watermark_entries, owner_entries,
        )
        self._pending_events = pending_events
        self._pending_event_digests = pending_event_digests
        self._pending_sensor_digests = pending_sensor_digests
        self._pending_attestations = pending_attestations
        self._pending_raw_identity = raw_rank
        self._pending_cursor = 0
        self._pending_event_high_water = max(
            self._pending_event_high_water, len(staged),
        )
        return tuple(staged)

    def _bind_delivery(self, authority: object) -> None:
        if self._delivery_authority is not None:
            raise RuntimeError("full-session router already has a delivery owner")
        self._delivery_authority = authority

    def validate_owned_event(
        self, event: ContinuousEvent, authority: object | None = None,
    ) -> _OwnedEventAttestation:
        cursor = self._pending_cursor
        if (
            authority is not self._delivery_authority
            or type(event) is not ContinuousEvent
            or cursor >= len(self._pending_events)
            or self._pending_events[cursor] is not event
        ):
            raise ValueError("full-session event is foreign or mutated")
        attestation = self._pending_attestations[cursor]
        if (attestation.owner_authority is not self._attestation_authority
                or _event_structural_identity(event) != attestation.structural_identity):
            raise ValueError("full-session event is foreign or mutated")
        return attestation

    def validate_owned_record(
        self, authority: object | None = None,
    ) -> tuple[tuple[ContinuousEvent, ...], tuple[_OwnedEventAttestation, ...]]:
        if (authority is not self._delivery_authority
                or not self._pending_events or self._pending_cursor != 0
                or len(self._pending_events) != len(self._pending_attestations)):
            raise ValueError("full-session raw record is foreign or mutated")
        for event, attestation in zip(self._pending_events, self._pending_attestations):
            if (attestation.owner_authority is not self._attestation_authority
                    or _event_structural_identity(event) != attestation.structural_identity):
                raise ValueError("full-session raw record is foreign or mutated")
        return self._pending_events, self._pending_attestations

    def _owned_record_descriptor(
        self, authority: object,
    ) -> tuple[tuple[int, int, int, str], tuple[str, ...], tuple[str, ...],
               tuple[_OwnedEventAttestation, ...]]:
        if (authority is not self._delivery_authority
                or not self._pending_events or self._pending_cursor != 0
                or self._pending_raw_identity is None):
            raise ValueError("full-session raw record is not pending")
        return (self._pending_raw_identity, self._pending_event_digests,
                self._pending_sensor_digests, self._pending_attestations)

    def _consume_owned_event(
        self, event: ContinuousEvent, attestation: _OwnedEventAttestation,
        authority: object,
    ) -> None:
        cursor = self._pending_cursor
        if (
            authority is not self._delivery_authority
            or cursor >= len(self._pending_events)
            or self._pending_events[cursor] is not event
            or self._pending_attestations[cursor] is not attestation
            or attestation.owner_authority is not self._attestation_authority
        ):
            raise RuntimeError("full-session pending event ownership changed")
        cursor += 1
        self._pending_cursor = cursor
        if cursor == len(self._pending_events):
            self._pending_events = ()
            self._pending_event_digests = ()
            self._pending_sensor_digests = ()
            self._pending_attestations = ()
            self._pending_raw_identity = None
            self._pending_cursor = 0

    def _consume_owned_record(
        self, attestations: tuple[_OwnedEventAttestation, ...], authority: object,
    ) -> None:
        if (authority is not self._delivery_authority
                or self._pending_cursor != 0
                or attestations is not self._pending_attestations
                or not attestations):
            raise RuntimeError("full-session pending record ownership changed")
        self._pending_events = ()
        self._pending_event_digests = ()
        self._pending_sensor_digests = ()
        self._pending_attestations = ()
        self._pending_raw_identity = None
        self._pending_cursor = 0

    def finish(self) -> FullSessionRouteAudit:
        if self._pending_cursor != len(self._pending_events):
            raise RuntimeError("full-session ended with an unconsumed raw record")
        expected = {binding.node_id for binding in self.clock_owner.bindings}
        observed = {
            node
            for counts in self._imu_counts.values()
            for node in counts
        }
        if observed != expected:
            raise RuntimeError("full-session envelope lacks exact ten-node IMU conservation")
        if (self._minimum_sensor_ready_lower_bound_ns is None
                or self._maximum_sensor_ready_lower_bound_ns is None):
            raise RuntimeError("full-session envelope lacks sensor-ready bounds")
        return FullSessionRouteAudit(self._event_count, MappingProxyType(dict(self._counts)),
            MappingProxyType({key: MappingProxyType(dict(value))
                              for key, value in self._imu_counts.items()}),
            self._exact, self._dropouts,
            self._minimum_sensor_ready_lower_bound_ns,
            self._maximum_sensor_ready_lower_bound_ns,
            self._lifted_record_count,
            self._maximum_availability_lift_ns,
            self._total_availability_lift_ns,
            self._maximum_owner_watermark_entries,
            self._pending_event_high_water,
            self._identity.hexdigest())


@dataclass(frozen=True)
class FullSessionStreamAudit:
    route_audit: FullSessionRouteAudit
    access_audit: Mapping[str, object]
    role: str = ROLE


class FullSessionRecordTicket:
    """Opaque one-shot ticket for one homogeneous original raw record."""

    __slots__ = (
        "record_ordinal", "raw_identity", "event_digests",
        "sensor_identity_digests", "__authority", "__owner",
    )

    def __init__(
        self, record_ordinal: int, raw_identity: tuple[int, int, int, str],
        event_digests: tuple[str, ...], sensor_identity_digests: tuple[str, ...],
        authority: object, owner: object,
    ) -> None:
        self.record_ordinal = record_ordinal
        self.raw_identity = raw_identity
        self.event_digests = event_digests
        self.sensor_identity_digests = sensor_identity_digests
        self.__authority = authority
        self.__owner = owner

    def deliver(self, consumer: Callable[[tuple[ContinuousEvent, ...]], None]) -> None:
        if not callable(consumer):
            raise TypeError("full-session record consumer must be callable")
        self.__owner._deliver_record(self, consumer)


class FullSessionEventTicket:
    """Opaque, one-shot delivery ticket issued by the live reader-owned router."""

    __slots__ = (
        "stream_ordinal", "event_digest", "sensor_identity_digest",
        "__event", "__attestation", "__authority", "__owner",
    )

    def __init__(self, stream_ordinal: int, event: ContinuousEvent,
                 event_digest: str, sensor_identity_digest: str,
                 authority: object, owner: object,
                 attestation: _OwnedEventAttestation | None = None) -> None:
        self.stream_ordinal = stream_ordinal
        self.event_digest = event_digest
        self.sensor_identity_digest = sensor_identity_digest
        self.__event = event
        self.__attestation = attestation
        self.__authority = authority
        self.__owner = owner

    def deliver(self, consumer: Callable[[ContinuousEvent], None]) -> None:
        """Revalidate immediately before invoking one downstream consumer."""

        if not callable(consumer):
            raise TypeError("full-session event consumer must be callable")
        self.__owner._deliver(self, consumer)

    def dispatch(self) -> FullSessionImuEventTicket | FullSessionUwbEventTicket:
        """Consume this generic ticket into one authenticated typed child."""

        return self.__owner._dispatch(self)


class _FullSessionTypedEventTicket:
    """Opaque one-shot child; the authenticated event is never exposed."""

    __slots__ = (
        "stream_ordinal", "event_digest", "sensor_identity_digest",
        "__event", "__attestation", "__authority", "__owner",
    )

    def __init__(self, stream_ordinal: int, event: ContinuousEvent,
                 event_digest: str, sensor_identity_digest: str,
                 authority: object, owner: object,
                 attestation: _OwnedEventAttestation | None = None) -> None:
        self.stream_ordinal = stream_ordinal
        self.event_digest = event_digest
        self.sensor_identity_digest = sensor_identity_digest
        self.__event = event
        self.__attestation = attestation
        self.__authority = authority
        self.__owner = owner

    def deliver(self, consumer: Callable[[ContinuousEvent], None]) -> None:
        if not callable(consumer):
            raise TypeError("typed full-session event consumer must be callable")
        self.__owner._deliver_typed(self, consumer)


class FullSessionImuEventTicket(_FullSessionTypedEventTicket):
    """Reader-issued ticket containing exactly one authenticated IMU event."""


class FullSessionUwbEventTicket(_FullSessionTypedEventTicket):
    """Reader-issued ticket containing exactly one authenticated UWB event."""


class _FullSessionRecordDeliveryOwner:
    """Issue exactly one event-reference-free ticket per pending raw record."""

    def __init__(self, router: FullSessionEventRouter) -> None:
        if type(router) is not FullSessionEventRouter:
            raise TypeError("full-session record delivery requires authoritative router")
        self.__router = router
        self.__authority = object()
        self.__next_ordinal = 0
        self.__active: tuple[object, ...] | None = None
        router._bind_delivery(self.__authority)

    def issue(self) -> FullSessionRecordTicket:
        if self.__active is not None:
            raise RuntimeError("previous full-session record ticket was not consumed")
        raw_identity, event_digests, sensor_digests, attestations = (
            self.__router._owned_record_descriptor(self.__authority)
        )
        ordinal = self.__next_ordinal
        self.__active = (
            ordinal, raw_identity, event_digests, sensor_digests, attestations,
        )
        return FullSessionRecordTicket(
            ordinal, raw_identity, event_digests, sensor_digests,
            self.__authority, self,
        )

    def _deliver_record(
        self, ticket: FullSessionRecordTicket,
        consumer: Callable[[tuple[ContinuousEvent, ...]], None],
    ) -> None:
        if (type(ticket) is not FullSessionRecordTicket
                or ticket._FullSessionRecordTicket__authority is not self.__authority
                or ticket._FullSessionRecordTicket__owner is not self
                or ticket.record_ordinal != self.__next_ordinal
                or self.__active is None
                or self.__active[:4] != (
                    ticket.record_ordinal, ticket.raw_identity,
                    ticket.event_digests, ticket.sensor_identity_digests,
                )):
            raise RuntimeError("FOREIGN_MUTATED_OR_REPLAYED_FULL_SESSION_RECORD_TICKET")
        try:
            events, attestations = self.__router.validate_owned_record(self.__authority)
        except ValueError:
            raise RuntimeError(
                "FOREIGN_MUTATED_OR_REPLAYED_FULL_SESSION_RECORD_TICKET"
            ) from None
        if (self.__active[4] is not attestations
                or _raw_key(events[0].payload_owner) != ticket.raw_identity):
            raise RuntimeError("FOREIGN_MUTATED_OR_REPLAYED_FULL_SESSION_RECORD_TICKET")
        self.__router._consume_owned_record(attestations, self.__authority)
        self.__active = None
        self.__next_ordinal += 1
        consumer(events)

    def require_consumed(self) -> None:
        if self.__active is not None:
            raise RuntimeError("full-session consumer did not deliver record ticket")


class _FullSessionDeliveryOwner:
    """Keep the router private and permit at most one outstanding event."""

    def __init__(self, router: FullSessionEventRouter) -> None:
        if type(router) is not FullSessionEventRouter:
            raise TypeError("full-session delivery requires authoritative router")
        self.__router = router
        self.__authority = object()
        self.__next_ordinal = 0
        self.__active: tuple[object, ...] | None = None
        router._bind_delivery(self.__authority)

    def issue(self, event: ContinuousEvent) -> FullSessionEventTicket:
        if self.__active is not None:
            raise RuntimeError("previous full-session event ticket was not consumed")
        attestation = self.__router.validate_owned_event(
            event, self.__authority,
        )
        digest = attestation.event_digest
        sensor_digest = attestation.sensor_identity_digest
        ordinal = self.__next_ordinal
        self.__active = (ordinal, attestation)
        return FullSessionEventTicket(
            ordinal, event, digest, sensor_digest,
            self.__authority, self, attestation,
        )

    def _deliver(
        self, ticket: FullSessionEventTicket,
        consumer: Callable[[ContinuousEvent], None],
    ) -> None:
        if (
            type(ticket) is not FullSessionEventTicket
            or ticket._FullSessionEventTicket__authority is not self.__authority
            or ticket._FullSessionEventTicket__owner is not self
            or self.__active != (ticket.stream_ordinal,
                                  ticket._FullSessionEventTicket__attestation)
            or ticket.stream_ordinal != self.__next_ordinal
            or ticket._FullSessionEventTicket__attestation is None
            or ticket.event_digest != ticket._FullSessionEventTicket__attestation.event_digest
            or ticket.sensor_identity_digest != ticket._FullSessionEventTicket__attestation.sensor_identity_digest
        ):
            raise RuntimeError("FOREIGN_MUTATED_OR_REPLAYED_FULL_SESSION_EVENT_TICKET")
        event = ticket._FullSessionEventTicket__event
        try:
            attestation = self.__router.validate_owned_event(event, self.__authority)
        except ValueError:
            raise RuntimeError(
                "FOREIGN_MUTATED_OR_REPLAYED_FULL_SESSION_EVENT_TICKET"
            ) from None
        if attestation is not ticket._FullSessionEventTicket__attestation:
            raise RuntimeError("FOREIGN_MUTATED_OR_REPLAYED_FULL_SESSION_EVENT_TICKET")
        # Consume before entering caller code: exceptions cannot make a ticket replayable.
        self.__router._consume_owned_event(
            event, attestation, self.__authority,
        )
        self.__active = None
        self.__next_ordinal += 1
        consumer(event)

    def _dispatch(
        self, ticket: FullSessionEventTicket,
    ) -> FullSessionImuEventTicket | FullSessionUwbEventTicket:
        if (
            type(ticket) is not FullSessionEventTicket
            or ticket._FullSessionEventTicket__authority is not self.__authority
            or ticket._FullSessionEventTicket__owner is not self
            or self.__active != (ticket.stream_ordinal,
                                  ticket._FullSessionEventTicket__attestation)
            or ticket.stream_ordinal != self.__next_ordinal
            or ticket._FullSessionEventTicket__attestation is None
            or ticket.event_digest != ticket._FullSessionEventTicket__attestation.event_digest
            or ticket.sensor_identity_digest != ticket._FullSessionEventTicket__attestation.sensor_identity_digest
        ):
            raise RuntimeError("FOREIGN_MUTATED_OR_REPLAYED_FULL_SESSION_EVENT_TICKET")
        event = ticket._FullSessionEventTicket__event
        try:
            attestation = self.__router.validate_owned_event(event, self.__authority)
        except ValueError:
            raise RuntimeError(
                "FOREIGN_MUTATED_OR_REPLAYED_FULL_SESSION_EVENT_TICKET"
            ) from None
        if attestation is not ticket._FullSessionEventTicket__attestation:
            raise RuntimeError("FOREIGN_MUTATED_OR_REPLAYED_FULL_SESSION_EVENT_TICKET")
        record = event.payload_owner
        child_type = (
            FullSessionImuEventTicket
            if event.kind == "IMU" and type(record) is TypedEvent
            and record.record_type is RecordType.IMU
            else FullSessionUwbEventTicket
            if event.kind == "UWB" and type(record) is TypedEvent
            and record.record_type is RecordType.UWB
            else None
        )
        if child_type is None:
            self.__active = None
            self.__next_ordinal += 1
            raise TypeError("authenticated full-session event has no typed sensor route")
        child = child_type(
            ticket.stream_ordinal, event, ticket.event_digest,
            ticket.sensor_identity_digest, self.__authority, self, attestation,
        )
        self.__active = (ticket.stream_ordinal, attestation, child_type)
        return child

    def _deliver_typed(
        self, ticket: _FullSessionTypedEventTicket,
        consumer: Callable[[ContinuousEvent], None],
    ) -> None:
        expected_type = (
            FullSessionImuEventTicket
            if type(ticket) is FullSessionImuEventTicket
            else FullSessionUwbEventTicket
            if type(ticket) is FullSessionUwbEventTicket
            else None
        )
        event = ticket._FullSessionTypedEventTicket__event
        if (
            expected_type is None
            or ticket._FullSessionTypedEventTicket__authority is not self.__authority
            or ticket._FullSessionTypedEventTicket__owner is not self
            or self.__active != (ticket.stream_ordinal,
                                  ticket._FullSessionTypedEventTicket__attestation,
                                  expected_type)
            or ticket.stream_ordinal != self.__next_ordinal
            or ticket._FullSessionTypedEventTicket__attestation is None
            or ticket.event_digest != ticket._FullSessionTypedEventTicket__attestation.event_digest
            or ticket.sensor_identity_digest != ticket._FullSessionTypedEventTicket__attestation.sensor_identity_digest
            or (expected_type is FullSessionImuEventTicket
                and (event.kind != "IMU" or type(event.payload_owner) is not TypedEvent
                     or event.payload_owner.record_type is not RecordType.IMU))
            or (expected_type is FullSessionUwbEventTicket
                and (event.kind != "UWB" or type(event.payload_owner) is not TypedEvent
                     or event.payload_owner.record_type is not RecordType.UWB))
        ):
            raise RuntimeError("FOREIGN_MUTATED_OR_REPLAYED_TYPED_FULL_SESSION_TICKET")
        try:
            attestation = self.__router.validate_owned_event(event, self.__authority)
        except ValueError:
            raise RuntimeError(
                "FOREIGN_MUTATED_OR_REPLAYED_TYPED_FULL_SESSION_TICKET"
            ) from None
        if attestation is not ticket._FullSessionTypedEventTicket__attestation:
            raise RuntimeError("FOREIGN_MUTATED_OR_REPLAYED_TYPED_FULL_SESSION_TICKET")
        self.__router._consume_owned_event(
            event, attestation, self.__authority,
        )
        self.__active = None
        self.__next_ordinal += 1
        consumer(event)

    def require_consumed(self) -> None:
        if self.__active is not None:
            raise RuntimeError("full-session consumer did not deliver ticket")

    def require_record_consumed(self) -> None:
        self.require_consumed()
        if self.__router._pending_cursor != len(self.__router._pending_events):
            raise RuntimeError("full-session consumer retained part of a raw record")


class FullSessionContinuousReader:
    """Read exactly the full calibration interval once from one O_NOFOLLOW fd."""

    def __init__(self, *, root: Path, clock_owner: ContinuousClockOwner,
                 chunk_bytes: int = 1 << 20, maximum_events: int = 4_000_000) -> None:
        self.root = Path(root).resolve()
        self.inventory = load_continuous_session_inventory(self.root)
        if type(clock_owner) is not ContinuousClockOwner or len(clock_owner.bindings) != 10:
            raise TypeError("full-session reader requires authoritative ten-node clock owner")
        self.clock_owner = clock_owner
        self.chunk_bytes = chunk_bytes
        self.maximum_events = maximum_events
        self.raw_path = (self.root / RAW_RELATIVE).resolve()
        self.manifest_path = (self.root / FORMAL_MANIFEST_RELATIVE).resolve()
        if (not self.raw_path.is_relative_to(self.root)
                or not self.manifest_path.is_relative_to(self.root)):
            raise RuntimeError("full-session capture source escapes workspace")
        self._validate_capture_source_identity()
        if continuous_clock_owner_digest(clock_owner) == "":
            raise AssertionError("clock owner digest unavailable")
        self._hashes = self._load_hash_evidence()
        self._consumed = False

    def _validate_capture_source_identity(self) -> None:
        flags = (os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
        fd = os.open(self.manifest_path, flags)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise RuntimeError("formal source manifest is not regular")
            payload = os.read(fd, before.st_size + 1)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        if (self._stat(before) != self._stat(after) or len(payload) != before.st_size
                or hashlib.sha256(payload).hexdigest() != FORMAL_MANIFEST_SHA256):
            raise RuntimeError("formal source manifest identity mismatch")
        matches = []
        for line in payload.decode("utf-8").splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) == 2 and fields[1].lstrip("*") == _MANIFEST_RAW_NAME:
                matches.append(fields[0])
        if matches != [SOURCE_SHA256]:
            raise RuntimeError("formal manifest does not uniquely bind capture source")
        source_stat = self.raw_path.lstat()
        if (not stat.S_ISREG(source_stat.st_mode)
                or stat.S_ISLNK(source_stat.st_mode)
                or self._stat(source_stat) != SOURCE_STAT_IDENTITY):
            raise RuntimeError("full-session capture source stat identity mismatch")

    @staticmethod
    def _stat(value: os.stat_result) -> tuple[int, int, int, int]:
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

    def _load_hash_evidence(self) -> tuple[str, ...]:
        path = (self.root / HASH_EVIDENCE_RELATIVE).resolve()
        if not path.is_relative_to(self.root):
            raise RuntimeError("full-session hash evidence escapes workspace")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
                     getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise RuntimeError("full-session hash evidence is not regular")
            payload = os.read(fd, before.st_size + 1)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        if (self._stat(before) != self._stat(after) or len(payload) != before.st_size
                or hashlib.sha256(payload).hexdigest() != HASH_EVIDENCE_SHA256):
            raise RuntimeError("full-session hash evidence identity mismatch")
        document = json.loads(payload)
        if (document.get("schema") != FULL_SESSION_SCHEMA
                or document.get("source", {}).get("window_sha256") != FULL_WINDOW_SHA256):
            raise RuntimeError("full-session hash evidence contract mismatch")
        regions = document.get("regions", [])
        expected = [(row.region_id, row.start_offset, row.stop_offset)
                    for row in self.inventory.regions]
        observed = [(row.get("region_id"), row.get("start_offset"), row.get("stop_offset"))
                    for row in regions]
        hashes = tuple(str(row.get("sha256", "")) for row in regions)
        if observed != expected or len(hashes) != 37 or any(len(value) != 64 for value in hashes):
            raise RuntimeError("full-session hash evidence region inventory mismatch")
        for region, digest in zip(self.inventory.regions, hashes):
            if region.expected_sha256 is not None and digest != region.expected_sha256:
                raise RuntimeError("full-session action hash disagrees with source audit")
        return hashes

    def consume(
        self, consumer: Callable[[FullSessionEventTicket], None],
    ) -> FullSessionStreamAudit:
        """Decode once and synchronously deliver bounded, owner-issued tickets."""

        return self._consume(consumer, record_batches=False)

    def consume_record_batches(
        self, consumer: Callable[[FullSessionRecordTicket], None],
    ) -> FullSessionStreamAudit:
        """Decode once and synchronously deliver one ticket per original record."""

        return self._consume(consumer, record_batches=True)

    def _consume(
        self, consumer: Callable[[object], None], *, record_batches: bool,
    ) -> FullSessionStreamAudit:

        if not callable(consumer):
            raise TypeError("full-session ticket consumer must be callable")
        if self._consumed:
            raise RuntimeError("full-session reader is one-shot")
        self._consumed = True
        fd = os.open(self.raw_path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) |
                     getattr(os, "O_NOFOLLOW", 0))
        prefix_bytes = source_bytes = records = emitted = 0
        try:
            with os.fdopen(fd, "rb", closefd=True, buffering=0) as source:
                before = self._stat(os.fstat(source.fileno()))
                if before != SOURCE_STAT_IDENTITY:
                    raise RuntimeError("full-session source stat identity changed")
                first_record, prefix_bytes = _count_nonempty_cobs_prefix(source,
                    stop_offset=self.inventory.start_offset, chunk_bytes=self.chunk_bytes)
                router = FullSessionEventRouter(self.inventory, self.clock_owner)
                delivery = (
                    _FullSessionRecordDeliveryOwner(router)
                    if record_batches else _FullSessionDeliveryOwner(router)
                )
                routed = 0
                whole = hashlib.sha256()
                for index, region in enumerate(self.inventory.regions):
                    window = AuthorizedByteWindow(str(RAW_RELATIVE), SOURCE_SHA256,
                        region.start_offset, region.stop_offset, first_record,
                        self._hashes[index], region.start_ns, region.stop_ns,
                        {f"{binding.node_id}:{kind}": binding.boot_epoch
                         for binding in self.clock_owner.bindings for kind in (1, 3)}, 4096)
                    decoder = IncrementalV47WindowDecoder(window,
                        maximum_emitted_events=self.maximum_events - emitted)
                    pending = False
                    cursor = region.start_offset
                    region_records = 0
                    while cursor < region.stop_offset:
                        block = source.read(min(self.chunk_bytes, region.stop_offset - cursor))
                        if not block:
                            raise OSError("full-session source ended early")
                        whole.update(block)
                        produced = decoder.feed(block, absolute_offset=cursor)
                        count, pending = _completed_nonempty(block, pending)
                        region_records += count
                        cursor += len(block); source_bytes += len(block); emitted += len(produced)
                        for _identity, group in itertools.groupby(produced, key=_raw_key):
                            routed_record = router.route_original_record(tuple(group))
                            if record_batches:
                                ticket = delivery.issue()
                                consumer(ticket)
                                delivery.require_consumed()
                                routed += len(routed_record)
                            else:
                                for event in routed_record:
                                    ticket = delivery.issue(event)
                                    consumer(ticket)
                                    # A consumer cannot silently retain the live ticket.
                                    delivery.require_consumed()
                                    routed += 1
                                delivery.require_record_consumed()
                    decoder.finish()
                    if pending or source.tell() != region.stop_offset:
                        raise RuntimeError("full-session region cuts/crosses a record")
                    first_record += region_records; records += region_records
                if source.tell() != self.inventory.stop_offset or whole.hexdigest() != FULL_WINDOW_SHA256:
                    raise RuntimeError("full-session final boundary/hash mismatch")
                if self._stat(os.fstat(source.fileno())) != before:
                    raise RuntimeError("full-session source changed during read")
                route_audit = router.finish()
        except BaseException:
            raise
        audit = MappingProxyType({"role": ROLE, "source_bytes_read": source_bytes,
            "prefix_bytes_read": prefix_bytes, "bytes_after_session_read": 0,
            "nonempty_records": records, "decoded_events": emitted,
            "routed_events": routed, "window_sha256": FULL_WINDOW_SHA256,
            "hash_evidence_sha256": HASH_EVIDENCE_SHA256})
        return FullSessionStreamAudit(route_audit, audit)
