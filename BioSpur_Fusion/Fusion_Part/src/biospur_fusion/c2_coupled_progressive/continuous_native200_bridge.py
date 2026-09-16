"""Source-owned bridge from decoded IMU records to continuous history frames.

The bridge is deliberately derivation-free: it binds one already-decoded B306
IMU event to one already-published pose/FK/contact envelope.  It does not
estimate orientation, geometry, contact, action ownership, or time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Mapping

import numpy as np

from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.ingest.events import EventStatus, RawByteProvenance, RecordType, TypedEvent
from biospur_fusion.root_r3.models import ImuSample

from .continuous_frontend import ContinuousEvent
from .continuous_group_epoch_owner import (
    OwnedNative200HistoryFrame,
    _digest_payload,
    _frozen_vector_map,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PELVIS_NODE = "BSFC2CC"
_FULL_SESSION_REGION = "FULL_SESSION_CONTINUOUS_00_TO_19"


def _validate_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"invalid {label} SHA-256")


def _validate_raw(raw: RawByteProvenance) -> None:
    if type(raw) is not RawByteProvenance:
        raise TypeError("pose publication requires exact raw-byte provenance")
    if (
        any(type(value) is not int for value in (
            raw.record_index, raw.sample_index, raw.start_offset, raw.end_offset,
        ))
        or raw.record_index < 0
        or raw.sample_index < 0
        or raw.start_offset < 0
        or raw.end_offset <= raw.start_offset
    ):
        raise ValueError("invalid pose-publication raw-byte offsets")
    _validate_sha256(raw.encoded_sha256, "raw-byte provenance")


def _freeze_imu(sample: ImuSample) -> ImuSample:
    if type(sample) is not ImuSample:
        raise TypeError("pose publication requires an authoritative ImuSample")
    sample.validate()
    force = np.asarray(sample.specific_force_sensor_mps2, dtype=float).reshape(3).copy()
    rotation = np.asarray(sample.rotation_world_from_sensor, dtype=float).reshape(3, 3).copy()
    force.setflags(write=False)
    rotation.setflags(write=False)
    return ImuSample(
        sample.measurement_time_s,
        sample.availability_time_s,
        force,
        rotation,
        sample.source_sequence,
        sample.m1_valid,
        sample.m1_reset,
    )


@dataclass(frozen=True)
class AuthoritativeNative200PosePublication:
    """Immutable upstream pose/FK/contact publication for one decoded IMU."""

    source_event_id: str
    node: str
    boot_epoch: int
    timer2_base_us: int
    source_timer_us: int
    source_global_ns: int
    availability_global_ns: int
    clock_domain: str
    clock_mapping_digest: str
    clock_owner_sha256: str
    clock_source_sha256: str
    publication_revision: int
    source_frame: int
    action_id: str
    raw_provenance: RawByteProvenance
    raw_acc_lsb: tuple[int, int, int]
    imu_sample: ImuSample
    base_rotations_world: Mapping[str, np.ndarray]
    offsets_world_m: Mapping[str, np.ndarray]
    offset_velocities_world_mps: Mapping[str, np.ndarray]
    normals_world: Mapping[str, np.ndarray]
    joints_relative_world_m: Mapping[str, np.ndarray]
    point_constraints_world_m: Mapping[str, np.ndarray]
    imu_owner_sha256: str
    publication_owner_sha256: str
    base_pose_owner_digest: str
    body_proxy_owner_sha256: str
    contact_owner_digest: str
    provenance: str
    digest: str = ""

    def __post_init__(self) -> None:
        exact_ints = (
            self.boot_epoch,
            self.timer2_base_us,
            self.source_timer_us,
            self.source_global_ns,
            self.availability_global_ns,
            self.publication_revision,
            self.source_frame,
        )
        if (
            not self.source_event_id
            or not self.node
            or not self.action_id
            or not self.provenance
            or self.clock_domain != "B306_TIMER2"
            or any(type(value) is not int for value in exact_ints)
            or self.boot_epoch < 0
            or self.timer2_base_us < 0
            or self.source_timer_us < self.timer2_base_us
            or self.source_global_ns < 0
            or self.availability_global_ns < self.source_global_ns
            or self.publication_revision < 0
            or self.source_frame < 0
        ):
            raise ValueError("invalid native200 pose-publication identity")
        _validate_raw(self.raw_provenance)
        if (
            type(self.raw_acc_lsb) is not tuple
            or len(self.raw_acc_lsb) != 3
            or any(type(value) is not int or value < -32768 or value > 32767 for value in self.raw_acc_lsb)
        ):
            raise ValueError("invalid decoded raw acceleration identity")
        for label, value in (
            ("clock mapping", self.clock_mapping_digest),
            ("clock owner", self.clock_owner_sha256),
            ("clock source", self.clock_source_sha256),
            ("IMU owner", self.imu_owner_sha256),
            ("publication owner", self.publication_owner_sha256),
            ("base-pose owner", self.base_pose_owner_digest),
            ("body-proxy owner", self.body_proxy_owner_sha256),
            ("contact owner", self.contact_owner_digest),
        ):
            _validate_sha256(value, label)

        imu = _freeze_imu(self.imu_sample)
        if (
            round(imu.measurement_time_s * 1e9) != self.source_global_ns
            or round(imu.availability_time_s * 1e9) != self.availability_global_ns
        ):
            raise ValueError("pose-publication IMU time mismatch")
        object.__setattr__(self, "imu_sample", imu)
        object.__setattr__(self, "base_rotations_world", _frozen_vector_map(
            self.base_rotations_world,
            keys=frozenset(SEGMENTS),
            shape=(3, 3),
            label="pose-publication base rotations",
        ))
        nodes = frozenset(NODE_TO_PROXY_POINT)
        for name in ("offsets_world_m", "offset_velocities_world_mps", "normals_world"):
            object.__setattr__(self, name, _frozen_vector_map(
                getattr(self, name), keys=nodes, shape=(3,), label=name,
            ))
        joint_keys = frozenset(str(key) for key in self.joints_relative_world_m)
        if not joint_keys:
            raise ValueError("pose publication requires joint proxy geometry")
        object.__setattr__(self, "joints_relative_world_m", _frozen_vector_map(
            self.joints_relative_world_m,
            keys=joint_keys,
            shape=(3,),
            label="pose-publication joint proxies",
        ))
        point_keys = frozenset(str(key) for key in self.point_constraints_world_m)
        if point_keys - {"ankle_left", "ankle_right"}:
            raise ValueError("unsupported pose-publication foothold constraint")
        object.__setattr__(self, "point_constraints_world_m", _frozen_vector_map(
            self.point_constraints_world_m,
            keys=point_keys,
            shape=(3,),
            label="pose-publication foothold constraints",
        ))
        value = _digest_payload({
            "schema": "biospur.c2.authoritative_native200_pose_publication.v1",
            "source_event_id": self.source_event_id,
            "identity": (
                self.node, self.boot_epoch, self.timer2_base_us,
                self.source_timer_us, self.source_global_ns,
                self.availability_global_ns, self.publication_revision,
                self.source_frame, self.action_id,
            ),
            "clock": (
                self.clock_domain, self.clock_mapping_digest,
                self.clock_owner_sha256, self.clock_source_sha256,
            ),
            "raw": (vars(self.raw_provenance), self.raw_acc_lsb),
            "imu": vars(self.imu_sample),
            "base": self.base_rotations_world,
            "offsets": self.offsets_world_m,
            "velocities": self.offset_velocities_world_mps,
            "normals": self.normals_world,
            "joints": self.joints_relative_world_m,
            "footholds": self.point_constraints_world_m,
            "owners": (
                self.imu_owner_sha256, self.publication_owner_sha256,
                self.base_pose_owner_digest, self.body_proxy_owner_sha256,
                self.contact_owner_digest,
            ),
            "provenance": self.provenance,
        })
        if self.digest and self.digest != value:
            raise ValueError("native200 pose-publication digest mismatch")
        object.__setattr__(self, "digest", value)


def _bind_continuous_history_payload(
    event: ContinuousEvent,
    publication: AuthoritativeNative200PosePublication | None = None,
) -> ContinuousEvent:
    """Bind an adapted IMU to its source publication; leave UWB untouched."""
    if event.kind != "IMU":
        if publication is not None:
            raise TypeError("pose publication cannot be attached to a non-IMU event")
        return event
    if type(event.payload_owner) is not TypedEvent:
        raise TypeError("native200 bridge requires the decoded TypedEvent owner")
    if type(publication) is not AuthoritativeNative200PosePublication:
        raise TypeError("native200 bridge requires an authoritative pose publication")
    record = event.payload_owner
    label_owner_matches = (
        publication.action_id == event.action_id
        if event.region_id != _FULL_SESSION_REGION
        else event.action_index == -1 and event.action_id == _FULL_SESSION_REGION
    )
    if (
        record.record_type is not RecordType.IMU
        or record.status is not EventStatus.DECODED
        or record.raw is None
    ):
        raise ValueError("native200 bridge requires one decoded IMU with raw provenance")
    _validate_raw(record.raw)
    expected_event_id = (
        f"v47:{record.raw.record_index}:{record.raw.sample_index}:"
        f"{record.raw.start_offset}:{record.raw.end_offset}:{record.raw.encoded_sha256}"
    )
    base_timer2 = record.payload.get("base_timer2_us")
    delta_us = record.payload.get("delta_us")
    acc_raw = record.payload.get("acc_raw")
    if type(base_timer2) is not int:
        raise ValueError("decoded IMU lacks exact TIMER2 base")
    if type(delta_us) is not int or delta_us < 0:
        raise ValueError("decoded IMU lacks exact TIMER2 delta")
    if record.node_id != _PELVIS_NODE:
        raise ValueError("native200 history bridge accepts only the pelvis IMU owner")
    if record.node_timer_us != base_timer2 + delta_us:
        raise ValueError("decoded IMU TIMER2 base/delta mismatch")
    decoded_force = np.asarray(acc_raw)
    if (
        decoded_force.shape != (3,)
        or not np.issubdtype(decoded_force.dtype, np.integer)
        or any(int(value) < -32768 or int(value) > 32767 for value in decoded_force)
    ):
        raise ValueError("decoded IMU acceleration inventory is invalid")
    if tuple(int(value) for value in decoded_force) != publication.raw_acc_lsb:
        raise ValueError("decoded raw acceleration differs from publication identity")
    if (
        event.event_id != expected_event_id
        or publication.source_event_id != expected_event_id
        or publication.raw_provenance != record.raw
        or record.node_id != event.node_id
        or publication.node != event.node_id
        or record.boot_epoch != event.boot_epoch
        or publication.boot_epoch != event.boot_epoch
        or record.node_timer_us != event.imu_timer2.trigger_timer2_us
        or publication.source_timer_us != event.imu_timer2.trigger_timer2_us
        or base_timer2 != event.imu_timer2.timer2_base_us
        or publication.timer2_base_us != event.imu_timer2.timer2_base_us
        or publication.source_global_ns != event.common_global_ns
        or publication.availability_global_ns != event.availability_global_ns
        or publication.clock_domain != event.clock_domain
        or publication.clock_mapping_digest != event.clock_mapping_digest
        or publication.clock_owner_sha256 != event.clock_owner_sha256
        or publication.clock_source_sha256 != event.clock_source_sha256
        or not label_owner_matches
        or publication.imu_sample.source_sequence != record.sequence
    ):
        raise ValueError("decoded IMU/event/pose-publication ownership mismatch")
    if record.global_time_ns is not None and record.global_time_ns != event.common_global_ns:
        raise ValueError("decoded IMU global time mismatch")

    frame = OwnedNative200HistoryFrame(
        node=publication.node,
        boot_epoch=publication.boot_epoch,
        timer2_base_us=publication.timer2_base_us,
        source_timer_us=publication.source_timer_us,
        source_global_ns=publication.source_global_ns,
        clock_mapping_digest=publication.clock_mapping_digest,
        clock_owner_sha256=publication.clock_owner_sha256,
        clock_source_sha256=publication.clock_source_sha256,
        publication_revision=publication.publication_revision,
        source_frame=publication.source_frame,
        action_id=publication.action_id,
        imu_sample=publication.imu_sample,
        base_rotations_world=publication.base_rotations_world,
        offsets_world_m=publication.offsets_world_m,
        offset_velocities_world_mps=publication.offset_velocities_world_mps,
        normals_world=publication.normals_world,
        joints_relative_world_m=publication.joints_relative_world_m,
        point_constraints_world_m=publication.point_constraints_world_m,
        raw_provenance=publication.raw_provenance,
        imu_owner_sha256=publication.imu_owner_sha256,
        publication_owner_sha256=publication.publication_owner_sha256,
        pose_publication_digest=publication.digest,
        base_pose_owner_digest=publication.base_pose_owner_digest,
        body_proxy_owner_sha256=publication.body_proxy_owner_sha256,
        contact_owner_digest=publication.contact_owner_digest,
        provenance=publication.provenance,
    )
    return replace(event, payload_owner=frame)


@dataclass(frozen=True)
class AuthoritativeNative200HistoryBridge:
    """Frozen owner of the accepted IMU and pose-publication producers."""

    imu_owner_sha256: str
    publication_owner_sha256: str

    def __post_init__(self) -> None:
        _validate_sha256(self.imu_owner_sha256, "bridge IMU owner")
        _validate_sha256(self.publication_owner_sha256, "bridge publication owner")

    def bind(
        self,
        event: ContinuousEvent,
        publication: AuthoritativeNative200PosePublication | None = None,
    ) -> ContinuousEvent:
        if publication is not None and (
            publication.imu_owner_sha256 != self.imu_owner_sha256
            or publication.publication_owner_sha256 != self.publication_owner_sha256
        ):
            raise ValueError("pose publication differs from bridge source owner")
        return _bind_continuous_history_payload(event, publication)
