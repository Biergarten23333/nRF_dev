"""Diagnostic-only strict-floor lookup over bridge-owned Action00 pose frames."""

from __future__ import annotations

import json
import hashlib
import math
from dataclasses import dataclass

from biospur_fusion.c2_timing_contract import MAXIMUM_POSE_AGE_NS
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
    OwnedNative200HistoryFrame,
)
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    ContinuousClockOwner,
    continuous_clock_owner_digest,
)
from biospur_fusion.c2_coupled_progressive.continuous_native200_bridge import (
    AuthoritativeNative200HistoryBridge,
)
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import StrictFloorOffset


ACTION00_ID = "00_initial_still"


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True)
class DiagnosticAction00StrictFloorSelection:
    """One causal selection and the exact bridge-owned frame that issued it."""

    offset: StrictFloorOffset
    frame: OwnedNative200HistoryFrame
    source_owner_digest: str
    digest: str = ""

    def __post_init__(self) -> None:
        if type(self.offset) is not StrictFloorOffset:
            raise TypeError("selection requires the exact strict-floor offset type")
        if type(self.frame) is not OwnedNative200HistoryFrame:
            raise TypeError("selection requires an exact owned native200 frame")
        if self.frame.action_id != ACTION00_ID:
            raise ValueError("selection is not owned by Action00")
        if (
            self.offset.pose_time_ns != self.frame.source_global_ns
            or self.offset.pose_frame != self.frame.source_frame
        ):
            raise ValueError("strict-floor selection/frame identity mismatch")
        if len(self.source_owner_digest) != 64:
            raise ValueError("invalid diagnostic pose-source owner digest")
        value = _digest({
            "schema": "biospur.c2.diagnostic_action00_strict_floor_selection.v1",
            "offset": {
                "offset_world_m": self.offset.offset_world_m.tolist(),
                "pose_time_ns": self.offset.pose_time_ns,
                "query_time_ns": self.offset.query_time_ns,
                "pose_age_ns": self.offset.pose_age_ns,
                "pose_frame": self.offset.pose_frame,
            },
            "frame": self.frame.digest,
            "source_owner": self.source_owner_digest,
        })
        if self.digest and self.digest != value:
            raise ValueError("diagnostic strict-floor selection digest mismatch")
        object.__setattr__(self, "digest", value)


class DiagnosticAction00StrictFloorPoseSource:
    """Non-promotable causal index of bridge-owned Action00 pose/FK frames.

    Frames can enter only after ``AuthoritativeNative200HistoryBridge.bind`` has
    converted a decoded event/publication pair into the exact owned frame type.
    The class owns lookup chronology; callers cannot inject a lookup callback.
    """

    execution_status = "DIAGNOSTIC_ONLY_NON_PROMOTABLE"
    product_ready = False
    scientific_pass = False

    def __init__(
        self, *, bridge: AuthoritativeNative200HistoryBridge,
        clock_owner: ContinuousClockOwner,
    ) -> None:
        if type(bridge) is not AuthoritativeNative200HistoryBridge:
            raise TypeError("pose source requires the exact native200 history bridge")
        if type(clock_owner) is not ContinuousClockOwner:
            raise TypeError("pose source requires the typed continuous clock owner")
        binding = clock_owner.binding_for("BSFC2CC")
        if binding.clock_domain != "B306_TIMER2":
            raise ValueError("pelvis clock owner is not TIMER2")
        self.__bridge = bridge
        self.__clock_owner = clock_owner
        self.__clock_binding = binding
        self.__imu_owner = bridge.imu_owner_sha256
        self.__publication_owner = bridge.publication_owner_sha256
        self.__base_pose_owner: str | None = None
        self.__frames: tuple[OwnedNative200HistoryFrame, ...] = ()
        self.__seen: frozenset[str] = frozenset()
        self.__owner_digest = _digest({
            "schema": "biospur.c2.diagnostic_action00_strict_floor_pose_source.v1",
            "action": ACTION00_ID,
            "imu_owner": self.__imu_owner,
            "publication_owner": self.__publication_owner,
            "continuous_clock_owner": continuous_clock_owner_digest(clock_owner),
            "pelvis_clock_binding": binding.__dict__,
            "strict_rule": "MAX_SOURCE_GLOBAL_NS_STRICTLY_LESS_THAN_QUERY",
            "maximum_pose_age_ns": MAXIMUM_POSE_AGE_NS,
            "qualification": self.execution_status,
        })

    @property
    def owner_digest(self) -> str:
        return self.__owner_digest

    @property
    def frame_count(self) -> int:
        return len(self.__frames)

    def accept_bound_frame(self, frame: OwnedNative200HistoryFrame) -> None:
        """Atomically append one exact bridge-owned Action00 history frame."""
        if type(frame) is not OwnedNative200HistoryFrame:
            raise TypeError("pose source requires an exact bridge-owned history frame")
        if frame.action_id != ACTION00_ID:
            raise ValueError("pose source accepts Action00 frames only")
        if (
            frame.imu_owner_sha256 != self.__imu_owner
            or frame.publication_owner_sha256 != self.__publication_owner
        ):
            raise ValueError("bridge-owned frame source mismatch")
        binding = self.__clock_binding
        if (
            frame.node != binding.node_id
            or frame.boot_epoch != binding.boot_epoch
            or frame.clock_mapping_digest != binding.clock_mapping_digest
            or frame.clock_owner_sha256 != binding.clock_owner_sha256
            or frame.clock_source_sha256 != binding.clock_source_sha256
            or frame.source_global_ns != binding.global_ns(frame.source_timer_us)
        ):
            raise ValueError("bridge-owned frame clock mismatch")
        if (
            self.__base_pose_owner is not None
            and frame.base_pose_owner_digest != self.__base_pose_owner
        ):
            raise ValueError("bridge-owned frame base-pose owner mismatch")
        if frame.digest in self.__seen:
            raise ValueError("bridge-owned frame replay")
        if self.__frames:
            previous = self.__frames[-1]
            if (
                frame.source_global_ns <= previous.source_global_ns
                or frame.source_timer_us <= previous.source_timer_us
                or frame.source_frame <= previous.source_frame
                or frame.imu_sample.availability_time_s < previous.imu_sample.availability_time_s
                or frame.publication_revision != previous.publication_revision + 1
            ):
                raise ValueError("bridge-owned frame chronology mismatch")
        if self.__base_pose_owner is None:
            self.__base_pose_owner = frame.base_pose_owner_digest
        self.__frames = (*self.__frames, frame)
        self.__seen = self.__seen | {frame.digest}

    def selection_for(self, node: str, query_time_ns: float) -> DiagnosticAction00StrictFloorSelection:
        """Return the latest strictly-past owned frame for one body-node link."""
        if not isinstance(node, str) or not node:
            raise ValueError("strict-floor node identity is missing")
        if not math.isfinite(float(query_time_ns)):
            raise ValueError("strict-floor query time is invalid")
        eligible = [frame for frame in self.__frames if frame.source_global_ns < query_time_ns]
        if not eligible:
            raise ValueError("query predates source-owned Action00 pose history")
        frame = eligible[-1]
        try:
            offset_world_m = frame.offsets_world_m[node]
        except KeyError as error:
            raise ValueError("strict-floor node is absent from owned pose frame") from error
        offset = StrictFloorOffset(
            offset_world_m,
            frame.source_global_ns,
            query_time_ns,
            query_time_ns - frame.source_global_ns,
            frame.source_frame,
        )
        return DiagnosticAction00StrictFloorSelection(
            offset, frame, self.__owner_digest,
        )

    def owner_bytes(self) -> bytes:
        return json.dumps({
            "schema": "biospur.c2.diagnostic_action00_strict_floor_pose_source.state.v1",
            "owner": self.__owner_digest,
            "base_pose_owner": self.__base_pose_owner,
            "frames": [frame.digest for frame in self.__frames],
        }, sort_keys=True, separators=(",", ":")).encode()
