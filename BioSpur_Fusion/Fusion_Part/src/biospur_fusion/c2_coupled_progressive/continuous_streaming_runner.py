"""Bounded no-raw composition for a continuous Capture2 00/gap/02 slice.

This module owns streaming transport and deterministic A/B dispatch only.  It
does not open a capture, estimate pose/contact, fit a clock, or finish a partial
Capture2 session.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import struct
from dataclasses import dataclass
from types import MappingProxyType
from typing import BinaryIO, Callable, Iterable, Mapping

from biospur_fusion.ingest.events import RecordType, TypedEvent
from biospur_fusion.ingest.v47 import (
    _imu_events,
    _uwb_event,
)

from .continuous_frontend import ContinuousClockOwner, ContinuousEvent
from .continuous_native200_bridge import (
    AuthoritativeNative200HistoryBridge,
    AuthoritativeNative200PosePublication,
)
from .continuous_stage2_adapter import EventRegionOwner, adapt_verified_record
from .continuous_uwb_owner import Capture2ContinuousUwbCalibrationOwner


try:
    from fusion_host_binary import FrameError, decode_frame
except ImportError:  # pragma: no cover - normal tests expose B306 tools on PYTHONPATH
    from B306_Part.tools.fusion_host_binary import FrameError, decode_frame


STREAMING_SLICE_SCHEMA = "biospur.c2.continuous_streaming_slice.v1"
MAX_HARD_WALL_SECONDS = 120
MAX_RSS_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class AuthorizedByteWindow:
    source_name: str
    source_sha256: str
    start_offset: int
    end_offset: int
    first_record_index: int
    window_sha256: str
    start_common_global_ns: int
    end_common_global_ns: int
    boot_epochs: Mapping[str, int]
    maximum_record_bytes: int = 4096

    def __post_init__(self) -> None:
        if (
            not self.source_name
            or any(len(value) != 64 for value in (self.source_sha256, self.window_sha256))
            or any(character not in "0123456789abcdef"
                   for value in (self.source_sha256, self.window_sha256)
                   for character in value)
            or any(type(value) is not int for value in (
                self.start_offset, self.end_offset, self.first_record_index,
                self.start_common_global_ns, self.end_common_global_ns,
                self.maximum_record_bytes,
            ))
            or self.start_offset < 0
            or self.end_offset <= self.start_offset
            or self.first_record_index < 0
            or self.start_common_global_ns < 0
            or self.end_common_global_ns <= self.start_common_global_ns
            or not 64 <= self.maximum_record_bytes <= 1 << 20
        ):
            raise ValueError("invalid authorized byte/time window")
        boots = dict(self.boot_epochs)
        if not boots or any(
            not key or type(value) is not int or value < 0 for key, value in boots.items()
        ):
            raise ValueError("authorized window lacks exact stream boot epochs")
        object.__setattr__(self, "boot_epochs", MappingProxyType(boots))


class IncrementalV47WindowDecoder:
    """Decode one exact authorized byte window with bounded pending storage."""

    def __init__(
        self, authorization: AuthorizedByteWindow, *, maximum_emitted_events: int | None = None,
    ) -> None:
        if type(authorization) is not AuthorizedByteWindow:
            raise TypeError("decoder requires one exact byte-window authorization")
        self.authorization = authorization
        self._pending = bytearray()
        self._pending_start = authorization.start_offset
        self._next_offset = authorization.start_offset
        self._record_index = authorization.first_record_index
        self._window_hash = hashlib.sha256()
        self._last_timer_by_stream: dict[str, int] = {}
        self._finished = False
        if maximum_emitted_events is not None and (
            type(maximum_emitted_events) is not int or maximum_emitted_events <= 0
        ):
            raise ValueError("decoder emitted-event cap must be positive")
        self.maximum_emitted_events = maximum_emitted_events
        self.emitted_events = 0
        self.maximum_pending_bytes = 0

    def feed(self, data: bytes, *, absolute_offset: int) -> tuple[TypedEvent, ...]:
        if self._finished:
            raise RuntimeError("stream decoder already finalized")
        if type(data) is not bytes or type(absolute_offset) is not int:
            raise TypeError("stream chunk requires exact bytes and offset")
        if absolute_offset != self._next_offset:
            raise ValueError("stream chunk offset is not contiguous")
        if absolute_offset + len(data) > self.authorization.end_offset:
            raise ValueError("stream chunk exceeds authorized byte window")
        self._window_hash.update(data)
        self._pending.extend(data)
        self._next_offset += len(data)
        emitted: list[TypedEvent] = []
        consumed = 0
        try:
            while True:
                boundary = self._pending.find(0, consumed)
                if boundary < 0:
                    break
                encoded = bytes(self._pending[consumed:boundary])
                record_start = self._pending_start + consumed
                record_end = self._pending_start + boundary + 1
                consumed = boundary + 1
                if not encoded:
                    continue
                self._record_index += 1
                try:
                    frame = decode_frame(encoded)
                    if frame.kind not in (1, 3):
                        continue
                    timer = struct.unpack_from(
                        "<Q", frame.payload, 102 if frame.kind == 1 else 4,
                    )[0]
                    stream_key = f"{frame.node_name}:{frame.kind}"
                    if stream_key not in self.authorization.boot_epochs:
                        raise ValueError("authorized window lacks stream boot ownership")
                    previous = self._last_timer_by_stream.get(stream_key)
                    if previous is not None and timer < previous:
                        raise ValueError("authorized byte window crosses a boot boundary")
                    self._last_timer_by_stream[stream_key] = int(timer)
                    boot = self.authorization.boot_epochs[stream_key]
                    provenance = (
                        self._record_index, record_start, record_end, encoded,
                    )
                    produced = (
                        tuple(_imu_events(frame, boot, provenance))
                        if frame.kind == 3 else (_uwb_event(frame, boot, provenance),)
                    )
                    if (
                        self.maximum_emitted_events is not None
                        and self.emitted_events + len(produced) > self.maximum_emitted_events
                    ):
                        raise OverflowError("stream decoder emitted-event capacity exceeded")
                    emitted.extend(produced)
                    self.emitted_events += len(produced)
                except (FrameError, struct.error, IndexError, ValueError) as error:
                    raise ValueError("authorized v47 record failed closed") from error
        finally:
            if consumed:
                del self._pending[:consumed]
                self._pending_start += consumed
        self.maximum_pending_bytes = max(self.maximum_pending_bytes, len(self._pending))
        if len(self._pending) > self.authorization.maximum_record_bytes:
            raise OverflowError("stream decoder pending-record capacity exceeded")
        return tuple(emitted)

    def finish(self) -> None:
        if self._finished:
            raise RuntimeError("stream decoder already finalized")
        if self._next_offset != self.authorization.end_offset:
            raise ValueError("authorized byte window was not fully consumed")
        if self._pending:
            raise ValueError("authorized byte window ends inside a COBS record")
        if self._window_hash.hexdigest() != self.authorization.window_sha256:
            raise ValueError("authorized byte-window hash mismatch")
        self._finished = True


@dataclass(frozen=True)
class StreamingTransportAudit:
    opened_sources: int
    bytes_read: int
    emitted_events: int
    maximum_pending_record_bytes: int
    skipped_bytes: int
    window_sha256: tuple[str, ...]


def stream_authorized_windows(
    source: BinaryIO,
    windows: tuple[AuthorizedByteWindow, ...],
    *,
    chunk_bytes: int,
    transport_event_cap: int,
    on_event: Callable[[TypedEvent, AuthorizedByteWindow], None],
) -> StreamingTransportAudit:
    """Decode disjoint authorized windows without retaining a window or event list."""
    if (
        not windows
        or type(chunk_bytes) is not int
        or not 1 <= chunk_bytes <= 1 << 20
        or type(transport_event_cap) is not int
        or transport_event_cap <= 0
    ):
        raise ValueError("invalid streaming transport contract")
    if any(right.start_offset < left.end_offset for left, right in zip(windows, windows[1:])):
        raise ValueError("authorized transport windows overlap or reverse")
    emitted = bytes_read = maximum_pending = skipped = 0
    hashes: list[str] = []
    cursor = windows[0].start_offset
    source.seek(cursor)
    for window in windows:
        if window.start_offset > cursor:
            skipped += window.start_offset - cursor
            source.seek(window.start_offset)
            cursor = window.start_offset
        decoder = IncrementalV47WindowDecoder(
            window, maximum_emitted_events=transport_event_cap - emitted,
        )
        while cursor < window.end_offset:
            chunk = source.read(min(chunk_bytes, window.end_offset - cursor))
            if not chunk:
                raise RuntimeError("authorized source ended inside a byte window")
            produced = decoder.feed(chunk, absolute_offset=cursor)
            cursor += len(chunk)
            bytes_read += len(chunk)
            for event in produced:
                on_event(event, window)
            emitted += len(produced)
        decoder.finish()
        maximum_pending = max(maximum_pending, decoder.maximum_pending_bytes)
        hashes.append(window.window_sha256)
    return StreamingTransportAudit(
        1, bytes_read, emitted, maximum_pending, skipped, tuple(hashes),
    )


@dataclass(frozen=True)
class FrozenPublicationIndex:
    """Exact output index of the existing frozen pose/FK/contact publishers."""

    publications: Mapping[str, AuthoritativeNative200PosePublication]
    trajectory_sha256: str
    clock_sha256: str
    fk_owner_sha256: str
    body_shadow_owner_sha256: str
    contact_owner_sha256: str

    def __post_init__(self) -> None:
        for value in (
            self.trajectory_sha256, self.clock_sha256, self.fk_owner_sha256,
            self.body_shadow_owner_sha256, self.contact_owner_sha256,
        ):
            if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
                raise ValueError("publication index owner hash is invalid")
        frozen = dict(self.publications)
        if any(key != value.source_event_id for key, value in frozen.items()):
            raise ValueError("publication index key/source mismatch")
        object.__setattr__(self, "publications", MappingProxyType(frozen))

    def publication_for(self, event_id: str) -> AuthoritativeNative200PosePublication:
        try:
            return self.publications[event_id]
        except KeyError as error:
            raise ValueError("decoded pelvis IMU lacks a frozen upstream publication") from error


@dataclass(frozen=True)
class StreamingSlicePreregistration:
    windows: tuple[AuthorizedByteWindow, ...]
    maximum_events: int
    maximum_window_bytes: int
    hard_wall_seconds: int
    rss_cap_bytes: int
    expected_maximum_pending_buckets: int
    boundary_availability_ns: Mapping[str, int]
    gap_completion_availability_ns: int
    source_owner_hashes: Mapping[str, str]
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            not self.windows
            or self.hard_wall_seconds > MAX_HARD_WALL_SECONDS
            or self.hard_wall_seconds <= 0
            or self.rss_cap_bytes > MAX_RSS_BYTES
            or self.rss_cap_bytes <= 0
            or self.maximum_events <= 0
            or self.maximum_window_bytes <= 0
            or sum(row.end_offset - row.start_offset for row in self.windows)
            > self.maximum_window_bytes
            or self.expected_maximum_pending_buckets != 3
            or type(self.gap_completion_availability_ns) is not int
            or self.gap_completion_availability_ns < 0
        ):
            raise ValueError("invalid bounded streaming preregistration")
        owners = dict(self.source_owner_hashes)
        if not owners or any(
            not key or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
            for key, value in owners.items()
        ):
            raise ValueError("invalid preregistered source owners")
        object.__setattr__(self, "source_owner_hashes", MappingProxyType(owners))
        boundaries = dict(self.boundary_availability_ns)
        if set(boundaries) != {"00_initial_still", "01_neutral_sway", "02_t_pose"} or any(
            type(value) is not int or value < 0 for value in boundaries.values()
        ):
            raise ValueError("streaming preregistration lacks exact boundary availability")
        object.__setattr__(self, "boundary_availability_ns", MappingProxyType(boundaries))
        document = {
            "schema": STREAMING_SLICE_SCHEMA,
            "windows": [
                {**vars(row), "boot_epochs": dict(sorted(row.boot_epochs.items()))}
                for row in self.windows
            ],
            "limits": (
                self.maximum_events, self.maximum_window_bytes,
                self.hard_wall_seconds, self.rss_cap_bytes,
                self.expected_maximum_pending_buckets,
            ),
            "boundary_availability_ns": dict(sorted(boundaries.items())),
            "gap_completion_availability_ns": self.gap_completion_availability_ns,
            "owners": dict(sorted(owners.items())),
        }
        value = hashlib.sha256(json.dumps(
            document, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        if self.digest and self.digest != value:
            raise ValueError("streaming preregistration digest mismatch")
        object.__setattr__(self, "digest", value)


@dataclass(frozen=True)
class StreamingSliceResult:
    branch_a: object
    branch_b: object
    dispatched_event_ids: tuple[str, ...]
    candidate_digests_a: tuple[str, ...]
    candidate_digests_b: tuple[str, ...]
    candidate_parity: bool
    group_counters_a: tuple[tuple[str, int], ...]
    group_counters_b: tuple[tuple[str, int], ...]
    partial_slice_finished: bool = False


@dataclass(frozen=True)
class IncrementalStreamingResult:
    branch_a: object
    branch_b: object
    selected_events: int
    dispatched_events: int
    filtered_outside_time: int
    upstream_nonpelvis_imu: int
    maximum_reorder_events: int
    candidate_count: int
    candidate_chain_sha256: str
    candidate_parity: bool
    partial_slice_finished: bool = False


class IncrementalContinuousSliceSession:
    """Bounded availability reorder and A/B dispatch for two acquired windows."""

    def __init__(
        self,
        runner: "ContinuousSliceRunner",
        *,
        selected_event_cap: int,
        reorder_horizon_ns: int,
        reorder_event_cap: int,
    ) -> None:
        if (
            type(selected_event_cap) is not int or selected_event_cap <= 0
            or type(reorder_horizon_ns) is not int or reorder_horizon_ns < 0
            or type(reorder_event_cap) is not int or reorder_event_cap <= 0
        ):
            raise ValueError("invalid selected/reorder capacity contract")
        self.runner = runner
        branches = runner.seed.fork_ab()
        self.a = branches.without_uwb_commits
        self.b = branches.with_uwb_commits
        self.selected_event_cap = selected_event_cap
        self.reorder_horizon_ns = reorder_horizon_ns
        self.reorder_event_cap = reorder_event_cap
        self._heap: list[tuple[tuple[int, int, int, str, str], ContinuousEvent]] = []
        self._highest_availability_ns: int | None = None
        self._selected = 0
        self._dispatched = 0
        self._filtered = 0
        self._upstream_nonpelvis = 0
        self._maximum_heap = 0
        self._candidate_count = 0
        self._candidate_chain = hashlib.sha256()
        self._transitioned = False
        action00 = next(
            row for row in runner.seed.snapshot().action_intervals if row.action_index == 0
        )
        availability = runner.preregistration.boundary_availability_ns["00_initial_still"]
        for branch in (self.a, self.b):
            branch.enter_action(
                "00_initial_still",
                common_global_ns=action00.start_common_global_ns,
                availability_global_ns=availability,
            )

    def note_filtered_outside_time(self) -> None:
        self._filtered += 1

    def note_upstream_nonpelvis_imu(self) -> None:
        self._upstream_nonpelvis += 1

    @staticmethod
    def _latest_candidate(branch: Capture2ContinuousUwbCalibrationOwner) -> tuple[int, str | None]:
        owner = dict(branch.subowners)["continuous-group"]
        count = int(owner.counters.get("PREPARED_COMPLETE_GROUP", 0))
        candidates = tuple(row.candidate_digest for row in owner.journal if row.candidate_digest)
        return count, candidates[-1] if candidates else None

    def _dispatch(self, event: ContinuousEvent) -> None:
        before_a, _ = self._latest_candidate(self.a)
        before_b, _ = self._latest_candidate(self.b)
        self.a.ingest(event)
        self.b.ingest(event)
        after_a, digest_a = self._latest_candidate(self.a)
        after_b, digest_b = self._latest_candidate(self.b)
        if before_a != before_b or after_a != after_b or digest_a != digest_b:
            raise RuntimeError("A/B candidate digest parity failed")
        if after_a == before_a + 1:
            if digest_a is None:
                raise RuntimeError("completed group lacks candidate digest")
            self._candidate_chain.update(bytes.fromhex(digest_a))
            self._candidate_count += 1
        elif after_a != before_a:
            raise RuntimeError("one event completed multiple candidate groups")
        self._dispatched += 1

    def _drain(self, watermark_ns: int | None = None) -> None:
        while self._heap and (
            watermark_ns is None or self._heap[0][0][0] <= watermark_ns
        ):
            _key, event = heapq.heappop(self._heap)
            self._dispatch(event)

    def submit_owned(
        self,
        record: TypedEvent,
        publication: AuthoritativeNative200PosePublication | None,
    ) -> None:
        event = self.runner._adapt_with_publication(record, publication)
        self._selected += 1
        if self._selected > self.selected_event_cap:
            raise OverflowError("selected-event capacity exceeded during emission")
        heapq.heappush(self._heap, (event.dispatch_key, event))
        if len(self._heap) > self.reorder_event_cap:
            raise OverflowError("availability reorder capacity exceeded")
        self._maximum_heap = max(self._maximum_heap, len(self._heap))
        self._highest_availability_ns = (
            event.availability_global_ns
            if self._highest_availability_ns is None
            else max(self._highest_availability_ns, event.availability_global_ns)
        )
        self._drain(self._highest_availability_ns - self.reorder_horizon_ns)

    def transition_over_gap(self) -> None:
        if self._transitioned:
            raise RuntimeError("inter-action gap already transitioned")
        self._drain()
        intervals = self.runner.seed.snapshot().action_intervals
        gap = self.runner.seed.snapshot().source_gaps[0]
        action02 = next(row for row in intervals if row.action_index == 2)
        boundaries = self.runner.preregistration.boundary_availability_ns
        pelvis = self.runner.clock_owner.binding_for("BSFC2CC")
        for branch in (self.a, self.b):
            branch.enter_action(
                "01_neutral_sway", common_global_ns=gap.start_common_global_ns,
                availability_global_ns=boundaries["01_neutral_sway"],
            )
        gap_event = ContinuousEvent(
            event_id=f"gap:{gap.region_id}:{gap.left_endpoint_sha256}:{gap.right_endpoint_sha256}",
            kind="GAP", action_index=-1, action_id=gap.region_id,
            common_global_ns=gap.end_common_global_ns,
            availability_global_ns=self.runner.preregistration.gap_completion_availability_ns,
            node_id=pelvis.node_id, boot_epoch=pelvis.boot_epoch,
            clock_domain=pelvis.clock_domain,
            clock_mapping_digest=pelvis.clock_mapping_digest,
            clock_owner_sha256=pelvis.clock_owner_sha256,
            clock_source_sha256=pelvis.clock_source_sha256,
            host_time_label="", payload_owner=gap,
            gap_start_global_ns=gap.start_common_global_ns,
            gap_covariance_growth=(gap.end_common_global_ns-gap.start_common_global_ns)*1e-9,
            region_id=gap.region_id,
        )
        self._dispatch(gap_event)
        for branch in (self.a, self.b):
            branch.enter_action(
                "02_t_pose", common_global_ns=action02.start_common_global_ns,
                availability_global_ns=boundaries["02_t_pose"],
            )
        self._transitioned = True

    def result(self) -> IncrementalStreamingResult:
        if not self._transitioned:
            raise RuntimeError("bounded stream did not cross the owned gap")
        self._drain()
        snapshot_a = self.a.snapshot()
        snapshot_b = self.b.snapshot()
        if not snapshot_a.protocol_marker_and_gap_accounted:
            raise RuntimeError("marker/gap ownership is incomplete")
        return IncrementalStreamingResult(
            snapshot_a, snapshot_b, self._selected, self._dispatched,
            self._filtered, self._upstream_nonpelvis, self._maximum_heap,
            self._candidate_count, self._candidate_chain.hexdigest(), True, False,
        )


class ContinuousSliceRunner:
    """Deterministically dispatch one bounded 00/gap/02 slice to A and B."""

    def __init__(
        self, *, seed: Capture2ContinuousUwbCalibrationOwner,
        clock_owner: ContinuousClockOwner,
        bridge: AuthoritativeNative200HistoryBridge,
        publications: FrozenPublicationIndex,
        preregistration: StreamingSlicePreregistration,
    ) -> None:
        if seed.snapshot().action_index != -1 or seed.snapshot().event_count:
            raise ValueError("streaming A/B seed must be the pre-00 owner")
        self.seed = seed
        self.clock_owner = clock_owner
        self.bridge = bridge
        self.publications = publications
        self.preregistration = preregistration
        expected_owners = {
            "trajectory": publications.trajectory_sha256,
            "clock": publications.clock_sha256,
            "fk": publications.fk_owner_sha256,
            "body_shadow": publications.body_shadow_owner_sha256,
            "contact": publications.contact_owner_sha256,
            "imu": bridge.imu_owner_sha256,
            "publication": bridge.publication_owner_sha256,
        }
        if dict(preregistration.source_owner_hashes) != expected_owners:
            raise ValueError("streaming preregistration source-owner mismatch")
        intervals = seed.snapshot().action_intervals
        action00 = next(row for row in intervals if row.action_index == 0)
        action02 = next(row for row in intervals if row.action_index == 2)
        gap = seed.snapshot().source_gaps[0]
        boundaries = preregistration.boundary_availability_ns
        if (
            boundaries["00_initial_still"] < action00.start_common_global_ns
            or boundaries["01_neutral_sway"] < action00.end_common_global_ns
            or boundaries["02_t_pose"] < action02.start_common_global_ns
            or not boundaries["00_initial_still"] < boundaries["01_neutral_sway"] < boundaries["02_t_pose"]
            or preregistration.gap_completion_availability_ns < gap.end_common_global_ns
            or preregistration.gap_completion_availability_ns > boundaries["02_t_pose"]
        ):
            raise ValueError("streaming preregistration boundary availability is invalid")

    def _adapt(self, record: TypedEvent) -> ContinuousEvent:
        binding = self.clock_owner.binding_for(record.node_id)
        common_ns = binding.global_ns(record.node_timer_us)
        if record.raw is None or sum(
            window.start_offset <= record.raw.start_offset
            and record.raw.end_offset <= window.end_offset
            and window.start_common_global_ns <= common_ns < window.end_common_global_ns
            for window in self.preregistration.windows
        ) != 1:
            raise ValueError("decoded event is outside its authorized byte/time window")
        action = self.seed.snapshot().action_intervals
        gap = self.seed.snapshot().source_gaps[0]
        owned_action = next((row for row in action if row.contains(common_ns)), None)
        if owned_action is not None:
            region = EventRegionOwner(action=owned_action)
        elif gap.contains(common_ns):
            region = EventRegionOwner(gap=gap)
        else:
            raise ValueError("decoded event falls outside preregistered 00/gap/02 regions")
        if record.record_type is RecordType.IMU:
            raw = record.raw
            event_id = (
                f"v47:{raw.record_index}:{raw.sample_index}:"
                f"{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}"
            )
            publication = self.publications.publication_for(event_id)
            availability_ns = publication.availability_global_ns
        else:
            frame_us = record.payload.get("frame_us")
            if type(frame_us) is not int:
                raise ValueError("decoded UWB lacks exact frame TIMER2")
            availability_ns = binding.global_ns(frame_us)
            publication = None
        adapted = adapt_verified_record(
            record, availability_global_ns=availability_ns,
            region_owner=region, clock_owner=self.clock_owner,
        )
        return self.bridge.bind(adapted, publication)

    def _adapt_with_publication(
        self, record: TypedEvent,
        publication: AuthoritativeNative200PosePublication | None,
    ) -> ContinuousEvent:
        """Adapt one selected record without retaining a publication index."""
        binding = self.clock_owner.binding_for(record.node_id)
        common_ns = binding.global_ns(record.node_timer_us)
        if record.raw is None or sum(
            window.start_offset <= record.raw.start_offset
            and record.raw.end_offset <= window.end_offset
            and window.start_common_global_ns <= common_ns < window.end_common_global_ns
            for window in self.preregistration.windows
        ) != 1:
            raise ValueError("decoded event is outside its authorized byte/time window")
        interval = next(
            (
                row for row in self.seed.snapshot().action_intervals
                if row.contains(common_ns)
            ),
            None,
        )
        if interval is None or interval.action_index not in {0, 2}:
            raise ValueError("selected event lacks Action00/Action02 time ownership")
        if record.record_type is RecordType.IMU:
            if type(publication) is not AuthoritativeNative200PosePublication:
                raise TypeError("selected pelvis IMU lacks its upstream publication")
            availability_ns = publication.availability_global_ns
        else:
            if publication is not None:
                raise TypeError("UWB cannot carry a native200 publication")
            frame_us = record.payload.get("frame_us")
            if type(frame_us) is not int:
                raise ValueError("decoded UWB lacks exact frame TIMER2")
            availability_ns = binding.global_ns(frame_us)
        adapted = adapt_verified_record(
            record, availability_global_ns=availability_ns,
            region_owner=EventRegionOwner(action=interval), clock_owner=self.clock_owner,
        )
        return self.bridge.bind(adapted, publication)

    def start_incremental(
        self, *, selected_event_cap: int, reorder_horizon_ns: int,
        reorder_event_cap: int,
    ) -> IncrementalContinuousSliceSession:
        return IncrementalContinuousSliceSession(
            self, selected_event_cap=selected_event_cap,
            reorder_horizon_ns=reorder_horizon_ns,
            reorder_event_cap=reorder_event_cap,
        )

    def run_decoded(self, records: Iterable[TypedEvent]) -> StreamingSliceResult:
        events = [self._adapt(record) for record in records]
        if len(events) > self.preregistration.maximum_events:
            raise OverflowError("streaming slice event limit exceeded")
        events.sort(key=lambda row: row.dispatch_key)
        if any(right.dispatch_key <= left.dispatch_key for left, right in zip(events, events[1:])):
            raise ValueError("streaming slice dispatch keys are duplicate or reversed")
        branches = self.seed.fork_ab()
        a, b = branches.without_uwb_commits, branches.with_uwb_commits
        intervals = self.seed.snapshot().action_intervals
        action00 = next(row for row in intervals if row.action_index == 0)
        action02 = next(row for row in intervals if row.action_index == 2)
        gap = self.seed.snapshot().source_gaps[0]
        pelvis_binding = self.clock_owner.binding_for("BSFC2CC")
        gap_event = ContinuousEvent(
            event_id=f"gap:{gap.region_id}:{gap.left_endpoint_sha256}:{gap.right_endpoint_sha256}",
            kind="GAP", action_index=-1, action_id=gap.region_id,
            common_global_ns=gap.end_common_global_ns,
            availability_global_ns=self.preregistration.gap_completion_availability_ns,
            node_id=pelvis_binding.node_id, boot_epoch=pelvis_binding.boot_epoch,
            clock_domain=pelvis_binding.clock_domain,
            clock_mapping_digest=pelvis_binding.clock_mapping_digest,
            clock_owner_sha256=pelvis_binding.clock_owner_sha256,
            clock_source_sha256=pelvis_binding.clock_source_sha256,
            host_time_label="", payload_owner=gap,
            gap_start_global_ns=gap.start_common_global_ns,
            gap_covariance_growth=(gap.end_common_global_ns-gap.start_common_global_ns)*1e-9,
            region_id=gap.region_id,
        )
        events.append(gap_event)
        events.sort(key=lambda row: row.dispatch_key)
        for branch in (a, b):
            branch.enter_action(
                "00_initial_still", common_global_ns=action00.start_common_global_ns,
                availability_global_ns=self.preregistration.boundary_availability_ns["00_initial_still"],
            )
        dispatched: list[str] = []
        entered_01 = entered_02 = False
        boundary_01_availability = self.preregistration.boundary_availability_ns[
            "01_neutral_sway"
        ]
        boundary_02_availability = self.preregistration.boundary_availability_ns[
            "02_t_pose"
        ]
        for event in events:
            if not entered_01 and event.availability_global_ns >= boundary_01_availability:
                for branch in (a, b):
                    branch.enter_action(
                        "01_neutral_sway", common_global_ns=gap.start_common_global_ns,
                        availability_global_ns=boundary_01_availability,
                    )
                entered_01 = True
            if not entered_02 and event.availability_global_ns >= boundary_02_availability:
                if not entered_01:
                    raise ValueError("Action02 boundary precedes Action01 marker")
                for branch in (a, b):
                    branch.enter_action(
                        "02_t_pose", common_global_ns=action02.start_common_global_ns,
                        availability_global_ns=boundary_02_availability,
                    )
                entered_02 = True
            for branch in (a, b):
                branch.ingest(event)
            dispatched.append(event.event_id)
        if not entered_01 or not entered_02:
            raise ValueError("bounded slice does not reach the Action02 head")
        named_a = dict(a.subowners)
        named_b = dict(b.subowners)
        if "continuous-group" not in named_a or "continuous-group" not in named_b:
            raise ValueError("streaming composition lacks the continuous-group owner")
        journal_a = named_a["continuous-group"].journal
        journal_b = named_b["continuous-group"].journal
        candidates_a = tuple(row.candidate_digest for row in journal_a if row.candidate_digest)
        candidates_b = tuple(row.candidate_digest for row in journal_b if row.candidate_digest)
        if candidates_a != candidates_b:
            raise RuntimeError("A/B candidate digest parity failed")
        return StreamingSliceResult(
            a.snapshot(), b.snapshot(), tuple(dispatched), candidates_a, candidates_b,
            candidates_a == candidates_b,
            tuple(sorted(named_a["continuous-group"].counters.items())),
            tuple(sorted(named_b["continuous-group"].counters.items())),
            False,
        )
