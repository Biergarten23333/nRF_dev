"""Label-free pelvis orientation owner for one complete Capture2 session."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import pickle

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

from .continuous_root_ab import (
    PELVIS_NODE,
    PELVIS_VQF_PREPARATION_SAMPLES as PREPARATION_SAMPLES,
    PelvisContinuousVQF,
)


QUALIFICATION = "DIAGNOSTIC_FULL_SESSION_PELVIS_ONLY_NON_PROMOTABLE"
NON_PELVIS_IMU_REASON = "AUTHENTICATED_NON_PELVIS_IMU_AUDIT_ONLY"


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


@dataclass(frozen=True)
class FullSessionPelvisOrientationFrame:
    source_event_id: str
    source_sequence: int
    boot_epoch: int
    source_timer_us: int
    measurement_time_s: float
    availability_time_s: float
    raw_provenance: RawByteProvenance
    specific_force_sensor_mps2: np.ndarray
    rotation_world_from_sensor: np.ndarray
    clock_owner_digest: str
    vqf_owner_digest: str
    digest: str = ""
    qualification: str = QUALIFICATION

    def __post_init__(self) -> None:
        force = np.asarray(self.specific_force_sensor_mps2, float).reshape(3).copy()
        rotation = np.asarray(self.rotation_world_from_sensor, float).reshape(3, 3).copy()
        raw = self.raw_provenance
        if (
            self.qualification != QUALIFICATION
            or not self.source_event_id
            or type(self.source_sequence) is not int
            or not 0 <= self.source_sequence <= 0xFFFF
            or type(self.boot_epoch) is not int or self.boot_epoch < 0
            or type(self.source_timer_us) is not int or self.source_timer_us < 0
            or not np.isfinite(force).all() or not np.isfinite(rotation).all()
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=2e-5)
            or np.linalg.det(rotation) < 0.999
        ):
            raise ValueError("invalid full-session pelvis orientation frame")
        force.setflags(write=False)
        rotation.setflags(write=False)
        payload = {
            "schema": "biospur.c2.full_session.pelvis_orientation_frame.v1",
            "identity": (
                self.source_event_id, self.source_sequence, self.boot_epoch,
                self.source_timer_us, self.measurement_time_s,
                self.availability_time_s,
            ),
            "raw": (
                raw.record_index, raw.sample_index, raw.start_offset,
                raw.end_offset, raw.encoded_sha256,
            ),
            "force": force.tolist(), "rotation": rotation.tolist(),
            "owners": (self.clock_owner_digest, self.vqf_owner_digest),
            "qualification": self.qualification,
        }
        actual = _sha(payload)
        if self.digest and self.digest != actual:
            raise ValueError("full-session pelvis orientation digest mismatch")
        object.__setattr__(self, "specific_force_sensor_mps2", force)
        object.__setattr__(self, "rotation_world_from_sensor", rotation)
        object.__setattr__(self, "digest", actual)

    @property
    def product_ready(self) -> bool:
        return False

    @property
    def scientific_pass(self) -> bool:
        return False


@dataclass(frozen=True)
class AuditedFullSessionNonPelvisImu:
    event_id: str
    node_id: str
    sensor_identity_digest: str
    raw_record_index: int
    raw_sample_index: int
    digest: str = ""
    reason: str = NON_PELVIS_IMU_REASON

    def __post_init__(self) -> None:
        if (not self.event_id or not self.node_id or not self.sensor_identity_digest
                or self.raw_record_index < 0 or self.raw_sample_index < 0
                or self.reason != NON_PELVIS_IMU_REASON
                or self.node_id == PELVIS_NODE):
            raise ValueError("invalid non-pelvis IMU audit outcome")
        actual = _sha({
            "schema": "biospur.c2.full_session.non_pelvis_imu_audit.v1",
            "event_id": self.event_id,
            "node_id": self.node_id,
            "sensor_identity_digest": self.sensor_identity_digest,
            "raw": [self.raw_record_index, self.raw_sample_index],
            "reason": self.reason,
        })
        if self.digest and self.digest != actual:
            raise ValueError("non-pelvis IMU audit digest mismatch")
        object.__setattr__(self, "digest", actual)


class FullSessionPelvisOrientationOwner:
    """One persistent native-200 pelvis VQF with no action-label dependency."""

    def __init__(self, clock_owner: ContinuousClockOwner) -> None:
        if type(clock_owner) is not ContinuousClockOwner:
            raise TypeError("full-session pelvis owner requires typed clock owner")
        binding = clock_owner.binding_for(PELVIS_NODE)
        self.clock_owner = clock_owner
        self._binding = binding
        self._vqf = PelvisContinuousVQF()
        self._event_ids: set[str] = set()
        self._published: dict[str, str] = {}
        self._last_timer_us: int | None = None
        self._last_measurement_ns: int | None = None
        self._last_availability_ns: int | None = None
        self._accepted = 0
        self._chain = hashlib.sha256()
        self._owner_digest = _sha({
            "schema": "biospur.c2.full_session.pelvis_orientation_owner.v1",
            "clock": continuous_clock_owner_digest(clock_owner),
            "node": PELVIS_NODE, "boot": binding.boot_epoch,
            "clock_mapping": binding.clock_mapping_digest,
            "vqf": "ONE_PRIVATE_PelvisContinuousVQF_NATIVE_0.005S",
            "preparation_samples": PREPARATION_SAMPLES,
            "preparation_output": "NONE_NO_BACKFILL",
            "labels_in_owner_or_control": False,
            "qualification": QUALIFICATION,
        })

    @property
    def owner_digest(self) -> str:
        return self._owner_digest

    def owner_bytes(self) -> bytes:
        state_sha = hashlib.sha256(
            pickle.dumps(self._vqf._block.obj.state, protocol=5)
        ).hexdigest()
        return json.dumps({
            "owner": self._owner_digest,
            "events": sorted(self._event_ids),
            "published": dict(sorted(self._published.items())),
            "last_timer_us": self._last_timer_us,
            "last_measurement_ns": self._last_measurement_ns,
            "last_availability_ns": self._last_availability_ns,
            "accepted": self._accepted,
            "chain": self._chain.copy().hexdigest(),
            "vqf_state": state_sha,
            "vqf_samples": self._vqf.samples,
            "vqf_gaps": tuple(self._vqf.gaps),
        }, sort_keys=True, separators=(",", ":")).encode()

    def _validate_authenticated_imu(self, event: ContinuousEvent) -> TypedEvent:
        if type(event) is not ContinuousEvent or event.kind != "IMU":
            raise TypeError("full-session pelvis owner accepts IMU only")
        validate_event_clock(event, self.clock_owner)
        record = event.payload_owner
        binding = self.clock_owner.binding_for(event.node_id)
        if (
            type(record) is not TypedEvent
            or record.record_type is not RecordType.IMU
            or record.status is not EventStatus.DECODED
            or record.raw is None
            or event.node_id != record.node_id
            or record.boot_epoch != binding.boot_epoch
            or event.boot_epoch != binding.boot_epoch
            or event.clock_domain != binding.clock_domain
            or event.clock_mapping_digest != binding.clock_mapping_digest
            or event.imu_timer2 is None
        ):
            raise ValueError("full-session IMU event/source owner mismatch")
        base = record.payload.get("base_timer2_us")
        delta = record.payload.get("delta_us")
        expected_id = (
            f"v47:{record.raw.record_index}:{record.raw.sample_index}:"
            f"{record.raw.start_offset}:{record.raw.end_offset}:"
            f"{record.raw.encoded_sha256}"
        )
        if (
            type(base) is not int or type(delta) is not int or delta < 0
            or base + delta != record.node_timer_us
            or event.imu_timer2.timer2_base_us != base
            or event.imu_timer2.trigger_timer2_us != record.node_timer_us
            or event.event_id != expected_id
            or type(record.sequence) is not int or not 0 <= record.sequence <= 0xFFFF
        ):
            raise ValueError("full-session IMU source identity rejected")
        return record

    def _validate(self, event: ContinuousEvent) -> TypedEvent:
        record = self._validate_authenticated_imu(event)
        if event.node_id != PELVIS_NODE or record.node_id != PELVIS_NODE:
            raise ValueError("full-session pelvis event/source owner mismatch")
        if (
            event.event_id in self._event_ids
            or (self._last_timer_us is not None and record.node_timer_us <= self._last_timer_us)
            or (self._last_measurement_ns is not None
                and event.common_global_ns <= self._last_measurement_ns)
            or (self._last_availability_ns is not None
                and event.availability_global_ns < self._last_availability_ns)
        ):
            raise ValueError("full-session pelvis identity or chronology rejected")
        return record

    def _ingest_event(
        self, event: ContinuousEvent,
    ) -> FullSessionPelvisOrientationFrame | None:
        record = self._validate(event)
        candidate = self._vqf.clone()
        oriented = candidate.step(
            boot=record.boot_epoch, timer_us=record.node_timer_us,
            acc_raw=record.payload["acc_raw"], gyro_raw=record.payload["gyro_raw"],
            preparation=self._accepted < PREPARATION_SAMPLES,
        )
        frame = None
        if oriented is not None:
            force, rotation = oriented
            frame = FullSessionPelvisOrientationFrame(
                event.event_id, record.sequence, record.boot_epoch,
                record.node_timer_us, event.common_global_ns * 1e-9,
                event.availability_global_ns * 1e-9, record.raw,
                force, rotation, continuous_clock_owner_digest(self.clock_owner),
                self._owner_digest,
            )
        self._vqf = candidate
        self._event_ids.add(event.event_id)
        self._last_timer_us = record.node_timer_us
        self._last_measurement_ns = event.common_global_ns
        self._last_availability_ns = event.availability_global_ns
        self._accepted += 1
        if frame is not None:
            self._published[frame.source_event_id] = frame.digest
            self._chain.update(bytes.fromhex(frame.digest))
        return frame

    def ingest_ticket(
        self, ticket: FullSessionImuEventTicket,
    ) -> FullSessionPelvisOrientationFrame | AuditedFullSessionNonPelvisImu | None:
        if type(ticket) is not FullSessionImuEventTicket:
            raise TypeError("full-session pelvis owner requires authenticated IMU ticket")
        result: list[
            FullSessionPelvisOrientationFrame | AuditedFullSessionNonPelvisImu | None
        ] = []

        def consume(event: ContinuousEvent) -> None:
            record = self._validate_authenticated_imu(event)
            if event.node_id != PELVIS_NODE:
                result.append(AuditedFullSessionNonPelvisImu(
                    event.event_id, event.node_id, ticket.sensor_identity_digest,
                    record.raw.record_index, record.raw.sample_index,
                ))
                return
            result.append(self._ingest_event(event))

        ticket.deliver(consume)
        if len(result) != 1:
            raise RuntimeError("pelvis ticket did not deliver exactly once")
        return result[0]

    def validate_owned_frame(self, frame: FullSessionPelvisOrientationFrame) -> None:
        if (
            type(frame) is not FullSessionPelvisOrientationFrame
            or frame.clock_owner_digest != continuous_clock_owner_digest(self.clock_owner)
            or frame.vqf_owner_digest != self._owner_digest
            or self._published.get(frame.source_event_id) != frame.digest
        ):
            raise ValueError("full-session pelvis frame is foreign or mutated")

    def audit(self) -> dict[str, object]:
        return {
            "qualification": QUALIFICATION,
            "accepted_pelvis_imu": self._accepted,
            "preparation_only_frames": min(self._accepted, PREPARATION_SAMPLES),
            "published_frames": len(self._published),
            "first_publication_index": PREPARATION_SAMPLES,
            "preparation_backfilled": False,
            "timer_gap_count": len(self._vqf.gaps),
            "full_body_pose_or_fk_issued": False,
            "labels_used": False,
            "event_chain_sha256": self._chain.copy().hexdigest(),
            "owner_digest": self._owner_digest,
            "product_ready": False,
            "scientific_pass": False,
        }
