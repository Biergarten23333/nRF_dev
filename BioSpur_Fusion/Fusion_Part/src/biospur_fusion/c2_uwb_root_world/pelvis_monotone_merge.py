"""Source-owned monotone merge for pelvis IMU and UWB measurements."""
from __future__ import annotations

from dataclasses import dataclass
import heapq

from biospur_fusion.ingest.events import RecordType, TypedEvent


@dataclass(frozen=True)
class MergedPelvisEvent:
    measurement_time_ns: int
    stream_progress_ns: int
    kind_order: int
    source_record_index: int
    source_sample_index: int
    source_end_offset: int
    event: TypedEvent

    @property
    def order_key(self) -> tuple[int, int, int, int]:
        return (
            self.measurement_time_ns, self.kind_order,
            self.source_record_index, self.source_sample_index,
        )


@dataclass(frozen=True)
class CompleteWindowBarrier:
    source_sha256: str
    end_offset: int
    complete: bool


class PelvisTwoStreamMonotoneMerge:
    """Merge independently monotone streams without a guessed time horizon.

    Input order is source byte/receipt order.  A record is safe only through
    the minimum of the latest IMU and UWB measurement watermarks.  Capacity is
    a fail-closed resource bound and never advances the safe watermark.
    """

    def __init__(self, *, node: str, boot_epoch: int, source_sha256: str,
                 window_end_offset: int, capacity: int):
        if not node or type(boot_epoch) is not int or boot_epoch < 0:
            raise ValueError("invalid pelvis stream identity")
        if (len(source_sha256) != 64
                or any(character not in "0123456789abcdef" for character in source_sha256)):
            raise ValueError("invalid source SHA-256")
        if type(window_end_offset) is not int or window_end_offset <= 0:
            raise ValueError("invalid source window end")
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("invalid merge capacity")
        self.node = node
        self.boot_epoch = boot_epoch
        self.source_sha256 = source_sha256
        self.window_end_offset = window_end_offset
        self.capacity = capacity
        self._heap: list[tuple[tuple[int, int, int, int], MergedPelvisEvent]] = []
        self._last_seen = {RecordType.IMU: None, RecordType.UWB: None}
        self._last_availability_rank: tuple[int, int, int] | None = None
        self._finished = False
        self.submitted = 0
        self.dispatched = 0

    @staticmethod
    def _kind_order(kind: RecordType) -> int:
        if kind is RecordType.IMU:
            return 0
        if kind is RecordType.UWB:
            return 1
        raise TypeError("pelvis merge accepts only IMU and UWB")

    def submit(self, event: TypedEvent, *, measurement_time_ns: int,
               stream_progress_ns: int) -> tuple[MergedPelvisEvent, ...]:
        if self._finished:
            raise RuntimeError("pelvis merge already finished")
        if not isinstance(event, TypedEvent) or event.node_id != self.node:
            raise ValueError("foreign pelvis merge event")
        if event.boot_epoch != self.boot_epoch:
            raise ValueError("pelvis merge boot mismatch")
        kind_order = self._kind_order(event.record_type)
        if type(measurement_time_ns) is not int or measurement_time_ns < 0:
            raise ValueError("pelvis merge requires canonical integer nanoseconds")
        if type(stream_progress_ns) is not int or stream_progress_ns < 0:
            raise ValueError("pelvis merge requires canonical stream progress")
        if event.record_type is RecordType.IMU and stream_progress_ns != measurement_time_ns:
            raise ValueError("pelvis IMU progress must equal measurement time")
        if event.record_type is RecordType.UWB and measurement_time_ns < stream_progress_ns:
            raise ValueError("pelvis UWB reference precedes strobe progress")
        if event.raw is None:
            raise ValueError("pelvis merge event lacks source provenance")
        if event.raw.end_offset > self.window_end_offset:
            raise ValueError("pelvis event exceeds authenticated window")
        previous_time = self._last_seen[event.record_type]
        if previous_time is not None and stream_progress_ns <= previous_time:
            raise ValueError("pelvis measurement stream reversed or duplicated")
        availability_rank = (
            int(event.raw.end_offset), int(event.raw.record_index), int(event.raw.sample_index),
        )
        if (self._last_availability_rank is not None
                and availability_rank <= self._last_availability_rank):
            raise ValueError("pelvis source availability rank reversed or duplicated")

        merged = MergedPelvisEvent(
            measurement_time_ns, stream_progress_ns, kind_order, int(event.raw.record_index),
            int(event.raw.sample_index), int(event.raw.end_offset), event,
        )
        staged_heap = list(self._heap)
        heapq.heappush(staged_heap, (merged.order_key, merged))
        staged_seen = dict(self._last_seen)
        staged_seen[event.record_type] = stream_progress_ns
        ready: list[MergedPelvisEvent] = []
        if all(value is not None for value in staged_seen.values()):
            safe_watermark = min(staged_seen.values())
            while staged_heap and staged_heap[0][1].measurement_time_ns <= safe_watermark:
                ready.append(heapq.heappop(staged_heap)[1])
        if len(staged_heap) > self.capacity:
            raise OverflowError("pelvis monotone merge capacity exceeded")

        self._heap = staged_heap
        self._last_seen = staged_seen
        self._last_availability_rank = availability_rank
        self.submitted += 1
        self.dispatched += len(ready)
        return tuple(ready)

    def label_boundary(self, _action_index: int, _action_id: str) -> tuple[()]:
        """Action labels cannot drain or reset either stream."""
        return ()

    def finish(self, barrier: CompleteWindowBarrier) -> tuple[MergedPelvisEvent, ...]:
        if self._finished:
            raise RuntimeError("pelvis merge already finished")
        if (type(barrier) is not CompleteWindowBarrier or not barrier.complete
                or barrier.source_sha256 != self.source_sha256
                or barrier.end_offset != self.window_end_offset):
            raise ValueError("unauthenticated or incomplete end-of-window barrier")
        if any(value is None for value in self._last_seen.values()):
            raise ValueError("complete pelvis window lacks one measurement stream")
        ready = tuple(heapq.heappop(self._heap)[1] for _ in range(len(self._heap)))
        self._finished = True
        self.dispatched += len(ready)
        return ready

    @property
    def pending(self) -> int:
        return len(self._heap)
