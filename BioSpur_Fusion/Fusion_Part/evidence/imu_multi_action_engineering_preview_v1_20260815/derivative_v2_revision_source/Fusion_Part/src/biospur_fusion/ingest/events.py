"""Typed immutable measurement events and accounting states."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class RecordType(str, Enum):
    IMU = "IMU"
    UWB = "UWB"
    TELEMETRY = "TELEMETRY"
    REPLY = "REPLY"
    OTHER = "OTHER"


class EventStatus(str, Enum):
    DECODED = "decoded"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    INVALID = "invalid"
    OUTSIDE_WINDOW = "outside-window"
    OUTSIDE_CLOCK_SEGMENT = "outside-clock-segment"
    UNUSED_BOUNDARY = "unused-boundary"


@dataclass(frozen=True)
class RawByteProvenance:
    record_index: int
    start_offset: int
    end_offset: int
    encoded_sha256: str
    sample_index: int = 0


@dataclass(frozen=True)
class TypedEvent:
    node_id: str
    boot_epoch: int
    record_type: RecordType
    sequence: int
    node_timer_us: int | None
    global_time_ns: int | None
    global_time_sigma_ns: int | None
    master_arrival_ms: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    quality: Mapping[str, Any] = field(default_factory=dict)
    status: EventStatus = EventStatus.DECODED
    raw: RawByteProvenance | None = None

    def __post_init__(self) -> None:
        if self.record_type in (RecordType.IMU, RecordType.UWB) and self.node_timer_us is None:
            raise ValueError("measurement event lacks authoritative node time")
        if self.global_time_ns is None and self.global_time_sigma_ns is not None:
            raise ValueError("uncertainty without global timestamp")
