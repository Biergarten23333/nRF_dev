"""Source-bound pelvis-only orientation for the 00/gap/02 root diagnostic.

This owner intentionally emits no full-body pose or FK.  It owns one existing
``PelvisContinuousVQF`` instance for the complete routed source prefix.  The
first 100 Action00 pelvis samples establish the heading gauge and have no root
translation output; the next sample is the first publishable orientation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Mapping

import numpy as np

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    ContinuousClockOwner,
    ContinuousEvent,
    continuous_clock_owner_digest,
    validate_event_clock,
)
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionImuEventTicket,
)
from biospur_fusion.ingest.events import EventStatus, RawByteProvenance, RecordType, TypedEvent

from .action00_gap02_diagnostic_plan import Action00Gap02DiagnosticPlan
from .continuous_root_ab import (
    PELVIS_NODE,
    PELVIS_VQF_PREPARATION_SAMPLES as PREPARATION_SAMPLES,
    PelvisContinuousVQF,
)


QUALIFICATION = "DIAGNOSTIC_ROOT_ONLY_NON_PROMOTABLE"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _raw_payload(raw: RawByteProvenance) -> tuple[object, ...]:
    return (
        raw.record_index, raw.sample_index, raw.start_offset,
        raw.end_offset, raw.encoded_sha256,
    )


@dataclass(frozen=True)
class DiagnosticPelvisOrientationFrame:
    source_event_id: str
    source_sequence: int
    boot_epoch: int
    source_timer_us: int
    measurement_time_s: float
    availability_time_s: float
    region_id: str
    raw_provenance: RawByteProvenance
    specific_force_sensor_mps2: np.ndarray
    rotation_world_from_sensor: np.ndarray
    plan_digest: str
    clock_owner_digest: str
    vqf_owner_digest: str
    digest: str = ""
    qualification: str = QUALIFICATION

    def __post_init__(self) -> None:
        force = np.asarray(self.specific_force_sensor_mps2, dtype=float).reshape(3).copy()
        rotation = np.asarray(self.rotation_world_from_sensor, dtype=float).reshape(3, 3).copy()
        if (
            self.qualification != QUALIFICATION
            or not self.source_event_id or not self.region_id
            or type(self.source_sequence) is not int
            or not 0 <= self.source_sequence <= 0xFFFF
            or type(self.boot_epoch) is not int or self.boot_epoch < 0
            or type(self.source_timer_us) is not int or self.source_timer_us < 0
            or not np.isfinite(force).all() or not np.isfinite(rotation).all()
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=2e-5)
            or np.linalg.det(rotation) < 0.999
        ):
            raise ValueError("invalid diagnostic pelvis orientation frame")
        force.setflags(write=False)
        rotation.setflags(write=False)
        payload = {
            "schema": "biospur.c2.diagnostic_pelvis_orientation_frame.v1",
            "identity": (
                self.source_event_id, self.source_sequence, self.boot_epoch,
                self.source_timer_us, self.measurement_time_s,
                self.availability_time_s, self.region_id,
            ),
            "raw": _raw_payload(self.raw_provenance),
            "force": force.tolist(),
            "rotation": rotation.tolist(),
            "owners": (self.plan_digest, self.clock_owner_digest, self.vqf_owner_digest),
            "qualification": self.qualification,
        }
        actual = _digest(payload)
        if self.digest and self.digest != actual:
            raise ValueError("diagnostic pelvis orientation digest mismatch")
        object.__setattr__(self, "specific_force_sensor_mps2", force)
        object.__setattr__(self, "rotation_world_from_sensor", rotation)
        object.__setattr__(self, "digest", actual)

    @property
    def product_ready(self) -> bool:
        return False

    @property
    def scientific_pass(self) -> bool:
        return False


class DiagnosticPelvisGapOrientationOwner:
    """One private persistent pelvis VQF over routed Action00/gap/Action02."""

    def __init__(
        self, plan: Action00Gap02DiagnosticPlan, clock_owner: ContinuousClockOwner,
    ) -> None:
        if type(plan) is not Action00Gap02DiagnosticPlan:
            raise TypeError("pelvis orientation owner requires the typed source plan")
        if type(clock_owner) is not ContinuousClockOwner:
            raise TypeError("pelvis orientation owner requires the typed clock owner")
        binding = clock_owner.binding_for(PELVIS_NODE)
        self.plan = plan
        self.clock_owner = clock_owner
        self._binding = binding
        self._vqf = PelvisContinuousVQF()
        self._event_ids: set[str] = set()
        self._published_frame_digests: dict[str, str] = {}
        self._last_timer_us: int | None = None
        self._last_measurement_ns: int | None = None
        self._last_availability_ns: int | None = None
        self._accepted = 0
        self._published = 0
        self._chain = hashlib.sha256()
        self._owner_digest = _digest({
            "schema": "biospur.c2.diagnostic_pelvis_gap_orientation_owner.v1",
            "plan": plan.digest,
            "clock": continuous_clock_owner_digest(clock_owner),
            "node": PELVIS_NODE,
            "boot": binding.boot_epoch,
            "clock_mapping": binding.clock_mapping_digest,
            "vqf": "ONE_PRIVATE_PelvisContinuousVQF_NATIVE_0.005S",
            "preparation_samples": PREPARATION_SAMPLES,
            "preparation_output": "NONE_NO_BACKFILL",
            "qualification": QUALIFICATION,
        })

    @property
    def owner_digest(self) -> str:
        return self._owner_digest

    def owner_bytes(self) -> bytes:
        return json.dumps({
            "owner": self._owner_digest,
            "events": sorted(self._event_ids),
            "published_frame_digests": dict(sorted(self._published_frame_digests.items())),
            "last_timer_us": self._last_timer_us,
            "last_measurement_ns": self._last_measurement_ns,
            "last_availability_ns": self._last_availability_ns,
            "accepted": self._accepted,
            "published": self._published,
            "chain": self._chain.copy().hexdigest(),
            "vqf_samples": self._vqf.samples,
            "vqf_gaps": tuple(self._vqf.gaps),
        }, sort_keys=True, separators=(",", ":")).encode()

    def _validate(self, event: ContinuousEvent) -> TypedEvent:
        if type(event) is not ContinuousEvent or event.kind != "IMU":
            raise TypeError("pelvis orientation owner accepts routed IMU events only")
        validate_event_clock(event, self.clock_owner)
        record = event.payload_owner
        if (
            type(record) is not TypedEvent
            or record.record_type is not RecordType.IMU
            or record.status is not EventStatus.DECODED
            or record.raw is None
            or event.node_id != PELVIS_NODE
            or record.node_id != PELVIS_NODE
            or record.boot_epoch != self._binding.boot_epoch
            or event.boot_epoch != self._binding.boot_epoch
            or event.clock_mapping_digest != self._binding.clock_mapping_digest
            or event.imu_timer2 is None
        ):
            raise ValueError("pelvis orientation event/source owner mismatch")
        base = record.payload.get("base_timer2_us")
        delta = record.payload.get("delta_us")
        if (
            type(base) is not int or type(delta) is not int or delta < 0
            or base + delta != record.node_timer_us
            or event.imu_timer2.timer2_base_us != base
            or event.imu_timer2.trigger_timer2_us != record.node_timer_us
            or event.event_id != (
                f"v47:{record.raw.record_index}:{record.raw.sample_index}:"
                f"{record.raw.start_offset}:{record.raw.end_offset}:{record.raw.encoded_sha256}"
            )
            or type(record.sequence) is not int or not 0 <= record.sequence <= 0xFFFF
        ):
            raise ValueError("pelvis orientation TIMER2/raw/sequence identity mismatch")
        matches = tuple(row for row in self.plan.regions if row.contains_ns(event.common_global_ns))
        if len(matches) != 1:
            raise ValueError("pelvis orientation event is outside the source plan")
        region = matches[0]
        expected_region = region.region_id if region.kind == "INTER_ACTION_GAP" else None
        if (
            event.region_id != expected_region
            or event.action_id != region.region_id
            or event.event_id in self._event_ids
            or (self._last_timer_us is not None and record.node_timer_us <= self._last_timer_us)
            or (self._last_measurement_ns is not None
                and event.common_global_ns <= self._last_measurement_ns)
            or (self._last_availability_ns is not None
                and event.availability_global_ns < self._last_availability_ns)
        ):
            raise ValueError("pelvis orientation chronology/region rejected")
        if self._accepted < PREPARATION_SAMPLES and region.region_id != "00_initial_still":
            raise ValueError("pelvis heading preparation left Action00")
        return record

    def _ingest_event(self, event: ContinuousEvent) -> DiagnosticPelvisOrientationFrame | None:
        record = self._validate(event)
        preparation = self._accepted < PREPARATION_SAMPLES
        candidate_vqf = self._vqf.clone()
        oriented = candidate_vqf.step(
            boot=record.boot_epoch,
            timer_us=record.node_timer_us,
            acc_raw=record.payload["acc_raw"],
            gyro_raw=record.payload["gyro_raw"],
            preparation=preparation,
        )
        if oriented is None:
            self._vqf = candidate_vqf
            self._event_ids.add(event.event_id)
            self._last_timer_us = record.node_timer_us
            self._last_measurement_ns = event.common_global_ns
            self._last_availability_ns = event.availability_global_ns
            self._accepted += 1
            return None
        force, rotation = oriented
        region = next(row for row in self.plan.regions if row.contains_ns(event.common_global_ns))
        frame = DiagnosticPelvisOrientationFrame(
            event.event_id, record.sequence, record.boot_epoch, record.node_timer_us,
            event.common_global_ns * 1e-9, event.availability_global_ns * 1e-9,
            region.region_id, record.raw, force, rotation, self.plan.digest,
            continuous_clock_owner_digest(self.clock_owner), self._owner_digest,
        )
        self._vqf = candidate_vqf
        self._event_ids.add(event.event_id)
        self._last_timer_us = record.node_timer_us
        self._last_measurement_ns = event.common_global_ns
        self._last_availability_ns = event.availability_global_ns
        self._accepted += 1
        self._published += 1
        self._published_frame_digests[frame.source_event_id] = frame.digest
        self._chain.update(bytes.fromhex(frame.digest))
        return frame

    def ingest_ticket(
        self, ticket: FullSessionImuEventTicket,
    ) -> DiagnosticPelvisOrientationFrame | None:
        """Consume exactly one reader-authenticated pelvis IMU child ticket."""

        if type(ticket) is not FullSessionImuEventTicket:
            raise TypeError("pelvis orientation requires an authenticated IMU ticket")
        result: list[DiagnosticPelvisOrientationFrame | None] = []
        ticket.deliver(lambda event: result.append(self._ingest_event(event)))
        if len(result) != 1:
            raise RuntimeError("pelvis IMU ticket did not deliver exactly once")
        return result[0]

    def validate_owned_frame(self, frame: DiagnosticPelvisOrientationFrame) -> None:
        if type(frame) is not DiagnosticPelvisOrientationFrame:
            raise TypeError("pelvis issuer requires its exact frame type")
        if (
            frame.plan_digest != self.plan.digest
            or frame.clock_owner_digest != continuous_clock_owner_digest(self.clock_owner)
            or frame.vqf_owner_digest != self._owner_digest
            or self._published_frame_digests.get(frame.source_event_id) != frame.digest
        ):
            raise ValueError("pelvis orientation frame is foreign or mutated")

    def audit(self) -> Mapping[str, object]:
        return MappingProxyType({
            "qualification": QUALIFICATION,
            "accepted_pelvis_imu": self._accepted,
            "preparation_only_frames": min(self._accepted, PREPARATION_SAMPLES),
            "published_frames": self._published,
            "first_publication_index": PREPARATION_SAMPLES,
            "preparation_backfilled": False,
            "full_body_pose_or_fk_issued": False,
            "event_chain_sha256": self._chain.copy().hexdigest(),
            "owner_digest": self._owner_digest,
            "product_ready": False,
            "scientific_pass": False,
        })
