"""Immutable Capture2 00--19 chronology and clock-domain contracts.

This module describes physical acquisition facts only.  It does not read a
capture, interpolate a gap, or choose calibration factors.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Literal

from biospur_fusion.c2_timing_contract import canonical_clock_global_ns


CONTINUOUS_FRONTEND_SCHEMA = "biospur.c2.continuous_frontend.v2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ProtocolSlot:
    index: int
    action_id: str
    acquired: bool
    acquisition_status: str


_ACTION_IDS = (
    "00_initial_still",
    "01_neutral_sway",
    "02_t_pose",
    "03_pelvis_hula_circle",
    "04_shoulder_left",
    "05_shoulder_right",
    "06_elbow_left",
    "07_elbow_right",
    "08_hip_left",
    "09_hip_right",
    "10_knee_left_seated",
    "11_knee_right_seated",
    "12_heel_raise_left",
    "13_heel_raise_right",
    "14_trunk_flex_extend",
    "15_trunk_axial_rotation",
    "16_squat",
    "17_final_still",
    "18_heel_to_butt_left",
    "19_heel_to_butt_right",
)

CAPTURE2_PROTOCOL_SLOTS = tuple(
    ProtocolSlot(
        index=index,
        action_id=action_id,
        acquired=index != 1,
        acquisition_status=(
            "OPERATOR_SKIPPED_EQUIVALENT_TO_00" if index == 1 else "ACQUIRED"
        ),
    )
    for index, action_id in enumerate(_ACTION_IDS)
)

CAPTURE2_PHYSICAL_ACTIONS = CAPTURE2_PROTOCOL_SLOTS
PhysicalAction = ProtocolSlot

if len(CAPTURE2_PROTOCOL_SLOTS) != 20:
    raise RuntimeError("Capture2 physical action manifest must contain 20 actions")


@dataclass(frozen=True)
class ActionInterval:
    action_index: int
    action_id: str
    start_common_global_ns: int
    end_common_global_ns: int
    end_inclusive: bool = False

    def __post_init__(self) -> None:
        if type(self.action_index) is not int or not 0 <= self.action_index < 20:
            raise ValueError("invalid action interval index")
        if not CAPTURE2_PROTOCOL_SLOTS[self.action_index].acquired:
            raise ValueError("unacquired protocol slot cannot own a measurement interval")
        if CAPTURE2_PROTOCOL_SLOTS[self.action_index].action_id != self.action_id:
            raise ValueError("action interval identity/index mismatch")
        if (
            type(self.start_common_global_ns) is not int
            or type(self.end_common_global_ns) is not int
            or self.start_common_global_ns < 0
            or self.end_common_global_ns <= self.start_common_global_ns
        ):
            raise ValueError("invalid action common-global interval")
        if self.end_inclusive != (self.action_index == 19):
            raise ValueError("only final action has an explicit closed end")

    def contains(self, common_global_ns: int) -> bool:
        if type(common_global_ns) is not int:
            return False
        if self.end_inclusive:
            return self.start_common_global_ns <= common_global_ns <= self.end_common_global_ns
        return self.start_common_global_ns <= common_global_ns < self.end_common_global_ns


def validate_action_intervals(intervals: tuple[ActionInterval, ...]) -> None:
    acquired_indices = tuple(row.index for row in CAPTURE2_PROTOCOL_SLOTS if row.acquired)
    if len(intervals) != 19:
        raise ValueError("continuous session requires exactly 19 acquired action intervals")
    if tuple(row.action_index for row in intervals) != acquired_indices:
        raise ValueError("acquired action intervals are missing, duplicated, or reordered")
    for index, interval in enumerate(intervals):
        if index and intervals[index - 1].end_common_global_ns > interval.start_common_global_ns:
            raise ValueError("acquired action intervals must be ordered and disjoint")


@dataclass(frozen=True)
class SourceBoundGap:
    region_id: str
    start_common_global_ns: int
    end_common_global_ns: int
    left_action_id: str
    right_action_id: str
    left_endpoint_source: str
    left_endpoint_sha256: str
    right_endpoint_source: str
    right_endpoint_sha256: str

    def __post_init__(self) -> None:
        if not self.region_id or self.region_id in _ACTION_IDS:
            raise ValueError("gap requires an independent region identity")
        if (
            type(self.start_common_global_ns) is not int
            or type(self.end_common_global_ns) is not int
            or self.start_common_global_ns < 0
            or self.end_common_global_ns <= self.start_common_global_ns
        ):
            raise ValueError("invalid source-bound gap interval")
        if self.left_action_id != _ACTION_IDS[0] or self.right_action_id != _ACTION_IDS[2]:
            raise ValueError("gap endpoint action identity mismatch")
        if not self.left_endpoint_source or not self.right_endpoint_source:
            raise ValueError("gap endpoint source is missing")
        if (
            _SHA256_RE.fullmatch(self.left_endpoint_sha256) is None
            or _SHA256_RE.fullmatch(self.right_endpoint_sha256) is None
        ):
            raise ValueError("gap endpoint source SHA is invalid")

    def contains(self, common_global_ns: int) -> bool:
        return (
            type(common_global_ns) is int
            and self.start_common_global_ns <= common_global_ns < self.end_common_global_ns
        )


def validate_source_gaps(
    gaps: tuple[SourceBoundGap, ...], intervals: tuple[ActionInterval, ...],
) -> None:
    if len(gaps) != 1:
        raise ValueError("Capture2 requires exactly one source-bound inter-action gap")
    gap = gaps[0]
    by_index = {row.action_index: row for row in intervals}
    if (
        gap.start_common_global_ns != by_index[0].end_common_global_ns
        or gap.end_common_global_ns != by_index[2].start_common_global_ns
    ):
        raise ValueError("gap endpoints do not match frozen acquired intervals")
    regions = sorted(
        [(row.start_common_global_ns, row.end_common_global_ns) for row in intervals]
        + [(gap.start_common_global_ns, gap.end_common_global_ns)]
    )
    for previous, current in zip(regions, regions[1:]):
        if previous[1] > current[0]:
            raise ValueError("action/gap regions overlap")


@dataclass(frozen=True)
class NodeClockBinding:
    node_id: str
    boot_epoch: int
    clock_domain: str
    clock_mapping_digest: str
    a_ns_per_us: float
    b_ns: float
    clock_owner_sha256: str
    clock_source_sha256: str

    def __post_init__(self) -> None:
        if not self.node_id or type(self.boot_epoch) is not int or self.boot_epoch < 0:
            raise ValueError("invalid node/boot clock binding")
        if not self.clock_domain or _SHA256_RE.fullmatch(self.clock_mapping_digest) is None:
            raise ValueError("invalid clock domain/mapping digest")
        if (
            not math.isfinite(float(self.a_ns_per_us))
            or float(self.a_ns_per_us) <= 0.0
            or not math.isfinite(float(self.b_ns))
            or _SHA256_RE.fullmatch(self.clock_owner_sha256) is None
            or _SHA256_RE.fullmatch(self.clock_source_sha256) is None
        ):
            raise ValueError("invalid clock coefficients/owner/source binding")

    def global_ns(self, timer2_us: int) -> int:
        if type(timer2_us) is not int or timer2_us < 0:
            raise ValueError("invalid TIMER2 source tick")
        return canonical_clock_global_ns(
            self.a_ns_per_us * timer2_us + self.b_ns
        )


@dataclass(frozen=True)
class ContinuousClockOwner:
    """Declares the source fields used to place IMU and UWB on one clock."""

    schema: str
    bindings: tuple[NodeClockBinding, ...]
    imu_timer2_base_field: str = "timer2_base_us"
    imu_trigger_field: str = "trigger_timer2_us"
    uwb_strobe_field: str = "strobe_timer2_us"
    uwb_frame_field: str = "frame_timer2_us"

    def __post_init__(self) -> None:
        if self.schema != CONTINUOUS_FRONTEND_SCHEMA:
            raise ValueError("unknown continuous clock-owner schema")
        nodes = tuple(binding.node_id for binding in self.bindings)
        if not nodes or len(nodes) != len(set(nodes)):
            raise ValueError("clock owner requires unique node bindings")
        declared = (
            self.imu_timer2_base_field,
            self.imu_trigger_field,
            self.uwb_strobe_field,
            self.uwb_frame_field,
        )
        if declared != (
            "timer2_base_us", "trigger_timer2_us",
            "strobe_timer2_us", "frame_timer2_us",
        ):
            raise ValueError("continuous clock source fields changed")

    def binding_for(self, node_id: str) -> NodeClockBinding:
        matches = tuple(row for row in self.bindings if row.node_id == node_id)
        if len(matches) != 1:
            raise ValueError("event node is not owned by the clock contract")
        return matches[0]


def continuous_clock_owner_digest(owner: ContinuousClockOwner) -> str:
    """Canonical identity of the existing common/global clock owner."""

    if type(owner) is not ContinuousClockOwner:
        raise TypeError("continuous clock identity requires its typed owner")
    payload = {
        "schema": owner.schema,
        "fields": [
            owner.imu_timer2_base_field,
            owner.imu_trigger_field,
            owner.uwb_strobe_field,
            owner.uwb_frame_field,
        ],
        "bindings": [row.__dict__ for row in owner.bindings],
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


@dataclass(frozen=True)
class ImuTimer2Fields:
    timer2_base_us: int
    trigger_timer2_us: int

    def __post_init__(self) -> None:
        if type(self.timer2_base_us) is not int or type(self.trigger_timer2_us) is not int:
            raise ValueError("IMU TIMER2 fields must be integers")
        if self.timer2_base_us < 0 or self.trigger_timer2_us < self.timer2_base_us:
            raise ValueError("invalid IMU TIMER2 base/trigger")


@dataclass(frozen=True)
class UwbTimer2Fields:
    strobe_timer2_us: int
    frame_timer2_us: int

    def __post_init__(self) -> None:
        if type(self.strobe_timer2_us) is not int or type(self.frame_timer2_us) is not int:
            raise ValueError("UWB TIMER2 fields must be integers")
        if self.strobe_timer2_us < 0 or self.frame_timer2_us < self.strobe_timer2_us:
            raise ValueError("invalid UWB TIMER2 strobe/frame")


@dataclass(frozen=True)
class ContinuousEvent:
    event_id: str
    kind: Literal["IMU", "UWB", "GAP"]
    action_index: int
    action_id: str
    common_global_ns: int
    availability_global_ns: int
    node_id: str
    boot_epoch: int
    clock_domain: str
    clock_mapping_digest: str
    clock_owner_sha256: str
    clock_source_sha256: str
    host_time_label: str
    payload_owner: object
    imu_timer2: ImuTimer2Fields | None = None
    uwb_timer2: UwbTimer2Fields | None = None
    gap_start_global_ns: int | None = None
    gap_covariance_growth: float | None = None
    region_id: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id or self.kind not in {"IMU", "UWB", "GAP"}:
            raise ValueError("invalid continuous event identity/kind")
        if self.region_id is None:
            if type(self.action_index) is not int or not 0 <= self.action_index < 20:
                raise ValueError("invalid protocol slot index")
            if CAPTURE2_PROTOCOL_SLOTS[self.action_index].action_id != self.action_id:
                raise ValueError("protocol slot identity/index mismatch")
        elif self.action_index != -1 or self.action_id != self.region_id:
            raise ValueError("gap-owned event must use only its independent region identity")
        if type(self.common_global_ns) is not int or type(self.availability_global_ns) is not int:
            raise ValueError("common-global event times must be integer ns")
        if self.common_global_ns < 0 or self.availability_global_ns < self.common_global_ns:
            raise ValueError("event availability precedes common-global time")
        if (
            _SHA256_RE.fullmatch(self.clock_owner_sha256) is None
            or _SHA256_RE.fullmatch(self.clock_source_sha256) is None
        ):
            raise ValueError("event clock owner/source SHA is invalid")
        if not isinstance(self.host_time_label, str):
            raise ValueError("host time label must be text")
        if self.kind == "IMU":
            if self.imu_timer2 is None or self.uwb_timer2 is not None:
                raise ValueError("IMU event requires only base/trigger TIMER2 fields")
        elif self.kind == "UWB":
            if self.uwb_timer2 is None or self.imu_timer2 is not None:
                raise ValueError("UWB event requires only strobe/frame TIMER2 fields")
        else:
            if self.imu_timer2 is not None or self.uwb_timer2 is not None:
                raise ValueError("gap event cannot impersonate a sensor sample")
            if (
                type(self.gap_start_global_ns) is not int
                or self.gap_start_global_ns < 0
                or self.gap_start_global_ns >= self.common_global_ns
                or self.gap_covariance_growth is None
                or not math.isfinite(float(self.gap_covariance_growth))
                or float(self.gap_covariance_growth) <= 0.0
            ):
                raise ValueError("gap requires a positive covariance-growth interval")

    @property
    def order_key(self) -> tuple[int, int]:
        return self.availability_global_ns, self.common_global_ns

    @property
    def dispatch_key(self) -> tuple[int, int, int, str, str]:
        kind_order = {"IMU": 0, "UWB": 1, "GAP": 2}[self.kind]
        return (
            self.availability_global_ns,
            self.common_global_ns,
            kind_order,
            self.node_id,
            self.event_id,
        )


@dataclass(frozen=True)
class ActionBoundary:
    ordinal: int
    previous_action_id: str
    next_action_id: str
    common_global_ns: int
    availability_global_ns: int
    host_time_label: str

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or not 1 <= self.ordinal <= 19:
            raise ValueError("invalid action boundary ordinal")
        if self.previous_action_id != _ACTION_IDS[self.ordinal - 1]:
            raise ValueError("action boundary previous identity mismatch")
        if self.next_action_id != _ACTION_IDS[self.ordinal]:
            raise ValueError("action boundary next identity mismatch")
        if (
            type(self.common_global_ns) is not int
            or type(self.availability_global_ns) is not int
            or self.common_global_ns < 0
            or self.availability_global_ns < self.common_global_ns
        ):
            raise ValueError("invalid action boundary time")


@dataclass(frozen=True)
class ProtocolMarker:
    action_index: int
    action_id: str
    status: str
    synthetic_data_created: bool = False

    def __post_init__(self) -> None:
        if (
            self.action_index != 1
            or self.action_id != "01_neutral_sway"
            or self.status != "OPERATOR_SKIPPED_EQUIVALENT_TO_00"
            or self.synthetic_data_created
        ):
            raise ValueError("invalid Capture2 skipped-action protocol marker")


ProtocolGap = ProtocolMarker


def validate_precomputed_uwb_frame_availability(
    event: ContinuousEvent, mapped_frame_global_ns: int,
) -> None:
    if event.availability_global_ns < mapped_frame_global_ns:
        raise ValueError("UWB availability precedes mapped source frame")


def validate_event_clock(event: ContinuousEvent, owner: ContinuousClockOwner) -> None:
    binding = owner.binding_for(event.node_id)
    if (
        event.boot_epoch != binding.boot_epoch
        or event.clock_domain != binding.clock_domain
        or event.clock_mapping_digest != binding.clock_mapping_digest
        or event.clock_owner_sha256 != binding.clock_owner_sha256
        or event.clock_source_sha256 != binding.clock_source_sha256
    ):
        raise ValueError("event node/boot/domain/mapping owner mismatch")
    if event.kind == "IMU":
        source_global_ns = binding.global_ns(event.imu_timer2.trigger_timer2_us)
    elif event.kind == "UWB":
        source_global_ns = binding.global_ns(event.uwb_timer2.strobe_timer2_us)
        validate_precomputed_uwb_frame_availability(
            event, binding.global_ns(event.uwb_timer2.frame_timer2_us),
        )
    else:
        return
    if event.common_global_ns != source_global_ns:
        raise ValueError("event common-global time mismatches source clock mapping")
