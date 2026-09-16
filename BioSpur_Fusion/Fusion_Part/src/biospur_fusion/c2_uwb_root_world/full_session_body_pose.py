"""One-owner ten-node orientation and native-200 body pose for Capture2."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Callable, Mapping

import numpy as np
import qmt
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_3a_kinematics import FrozenC2Kinematics3A, load_frozen_c2_3a
from biospur_fusion.c2_articulated_biomechanics.model import HingeJoint
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
    reconstruct_distal_orientation,
    solve_hinge_flexion_deg,
)
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    ContinuousClockOwner,
    ContinuousEvent,
    continuous_clock_owner_digest,
    validate_event_clock,
)
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionImuEventTicket,
)
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
    OwnedNative200HistoryFrame,
)
from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
from biospur_fusion.c2_coupled_progressive.native200_publication_producer import (
    Native200PublicationProducer,
)
from biospur_fusion.c2_uwb_calibration.articulated_range import (
    SEGMENTS,
    corrected_proxy_points,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.ingest.events import EventStatus, RecordType, TypedEvent
from biospur_fusion.root_r3 import ImuSample


PELVIS_NODE = "BSFC2CC"
SESSION_ID = "FULL_SESSION_CONTINUOUS_00_TO_19"


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _matrix_from_qmt(quaternion: np.ndarray) -> np.ndarray:
    return Rotation.from_quat(np.asarray(quaternion)[[1, 2, 3, 0]]).as_matrix()


def _wxyz(matrix: np.ndarray) -> np.ndarray:
    value = Rotation.from_matrix(matrix).as_quat()
    return np.r_[value[3], value[:3]][None, :]


@dataclass(frozen=True)
class FullSessionBodyPoseAudit:
    imu_events: int
    pelvis_publications: int
    accepted_anchor_publications: int
    gap_publications: int
    precoverage_pelvis_omissions: int
    preanchor_pelvis_omissions: int
    owner_digest: str


@dataclass(frozen=True)
class _OrientationSample:
    common_global_ns: int
    availability_global_ns: int
    rotation_vqf: np.ndarray


@dataclass(frozen=True)
class _PendingPelvis:
    event: ContinuousEvent
    record: TypedEvent
    acceleration: np.ndarray
    prepared_pose_row: object | None = None


@dataclass(frozen=True)
class _RecordBatchLease:
    owner: object
    coordinator: object
    generation: int
    node: str
    raw_identity: tuple[int, int, int, str]


@dataclass
class _RecordBatchCapabilityState:
    delta: "_RecordBatchDelta | None"
    status: str = "ACTIVE"


@dataclass(frozen=True)
class _RecordBatchCapability:
    owner: object
    coordinator: object
    generation: int
    node: str
    raw_identity: tuple[int, int, int, str]
    state: _RecordBatchCapabilityState


@dataclass(frozen=True)
class _RecordBatchDelta:
    node: str
    block: object
    block_state: object
    latest_present: bool
    latest_vqf: object
    orientation_history: object
    first_present: bool
    first_orientation_ns: int | None
    timer_present: bool
    last_timer: int | None
    availability_present: bool
    last_availability: int | None
    segment_from_vqf_right: object
    anchor_normals_segment: object
    pelvis_sensor_from_vqf_right: object
    pending_pelvis: object
    last_offsets: object
    last_pelvis_ns: int | None
    counters: tuple[int, ...]
    producer_state: tuple[tuple[str, object], ...]


class FullSessionBodyPoseOwner:
    """Consume every IMU once and publish one pose at every pelvis tick.

    Acquired-pose labels are consulted only inside the sealed upstream producer
    to obtain an exact anchor.  This owner publishes one label-free session
    identity and carries the anchor action solely in its provenance string.
    """

    def __init__(
        self, clock_owner: ContinuousClockOwner,
        producer: Native200PublicationProducer,
        hinge_model: Mapping[str, HingeJoint], *,
        kinematics: FrozenC2Kinematics3A | None = None,
    ) -> None:
        if type(clock_owner) is not ContinuousClockOwner:
            raise TypeError("body pose requires the full-session clock owner")
        if type(producer) is not Native200PublicationProducer:
            raise TypeError("body pose requires the sealed native200 producer")
        kinematics = load_frozen_c2_3a() if kinematics is None else kinematics
        if type(kinematics) is not FrozenC2Kinematics3A:
            raise TypeError("body pose requires frozen C2 kinematics")
        if set(hinge_model) != {
            "elbow_left", "elbow_right", "knee_left", "knee_right",
        } or any(type(value) is not HingeJoint for value in hinge_model.values()):
            raise ValueError("body pose requires the accepted four-hinge model")
        if {binding.node_id for binding in clock_owner.bindings} != set(NODE_TO_SEGMENT):
            raise ValueError("body pose requires all ten node clocks")
        self._clock = clock_owner
        self._producer = producer
        self._geometry = kinematics.geometry
        self._geometry_digest = kinematics.verification.manifest_sha256
        self._hinges = MappingProxyType(dict(hinge_model))
        self._blocks = {node: qmt.OriEstVQFBlock(0.005) for node in NODE_TO_SEGMENT}
        self._latest_vqf: dict[str, np.ndarray] = {}
        self._segment_from_vqf_right: dict[str, np.ndarray] = {}
        self._anchor_normals_segment: dict[str, np.ndarray] = {}
        self._pelvis_sensor_from_vqf_right: np.ndarray | None = None
        self._orientation_history = {
            node: deque(maxlen=64) for node in NODE_TO_SEGMENT
        }
        self._first_orientation_ns: dict[str, int] = {}
        self._pending_pelvis: deque[_PendingPelvis] = deque(maxlen=64)
        self._last_timer: dict[str, int] = {}
        self._last_availability: dict[str, int] = {}
        self._last_offsets: dict[str, np.ndarray] | None = None
        self._last_pelvis_ns: int | None = None
        self._imu_events = 0
        self._pelvis_publications = 0
        self._anchor_publications = 0
        self._gap_publications = 0
        self._precoverage_pelvis_omissions = 0
        self._preanchor_pelvis_omissions = 0
        self._revision = 0
        self.__record_batch_coordinator: object | None = None
        self.__record_batch_owner = object()
        self.__record_batch_generation = 0
        self.__active_record_batch: _RecordBatchLease | None = None
        self.__active_record_capability: _RecordBatchCapability | None = None
        self._owner_digest = _sha({
            "schema": "biospur.c2.full_session.body_pose_owner.v1",
            "session": SESSION_ID,
            "clock": continuous_clock_owner_digest(clock_owner),
            "producer": producer.publication_owner_sha256,
            "geometry": self._geometry_digest,
            "hinges": {key: value.to_json() for key, value in sorted(self._hinges.items())},
            "vqf": "ONE_PERSISTENT_QMT_ORIESTVQF_BLOCK_PER_NODE_0.005S",
            "action_labels_affect_control": False,
        })

    @property
    def owner_digest(self) -> str:
        return self._owner_digest

    def bind_record_batch_coordinator(self, authority: object) -> None:
        """Bind the sole coordinator allowed to submit authenticated records."""
        if authority is None:
            raise TypeError("body pose batch coordinator authority is required")
        if self.__record_batch_coordinator is not None:
            raise RuntimeError("body pose batch coordinator is already bound")
        self.__record_batch_coordinator = authority

    def _validate_against(
        self, event: ContinuousEvent, *, last_timer: int | None,
        last_availability: int | None,
    ) -> TypedEvent:
        if type(event) is not ContinuousEvent or event.kind != "IMU":
            raise TypeError("body pose owner accepts IMU tickets only")
        validate_event_clock(event, self._clock)
        record = event.payload_owner
        binding = self._clock.binding_for(event.node_id)
        if (
            type(record) is not TypedEvent
            or record.record_type is not RecordType.IMU
            or record.status is not EventStatus.DECODED
            or record.raw is None
            or record.node_id != event.node_id
            or record.boot_epoch != binding.boot_epoch
            or event.imu_timer2 is None
        ):
            raise ValueError("body pose IMU identity is invalid")
        expected_event_id = (
            f"v47:{record.raw.record_index}:{record.raw.sample_index}:"
            f"{record.raw.start_offset}:{record.raw.end_offset}:"
            f"{record.raw.encoded_sha256}"
        )
        base = record.payload.get("base_timer2_us")
        delta = record.payload.get("delta_us")
        if (
            type(base) is not int or type(delta) is not int or delta < 0
            or base + delta != record.node_timer_us
            or event.imu_timer2.timer2_base_us != base
            or event.imu_timer2.trigger_timer2_us != record.node_timer_us
            or event.event_id != expected_event_id
            or (last_timer is not None and record.node_timer_us <= last_timer)
            or (last_availability is not None
                and event.availability_global_ns < last_availability)
        ):
            raise ValueError("body pose IMU chronology is invalid")
        return record

    def _validate(self, event: ContinuousEvent) -> TypedEvent:
        return self._validate_against(
            event, last_timer=self._last_timer.get(event.node_id),
            last_availability=self._last_availability.get(event.node_id),
        )

    def _project_hinges(self, rotations: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
        result = {key: np.asarray(value, float).copy() for key, value in rotations.items()}
        for joint in self._hinges.values():
            parent = _wxyz(result[joint.parent])
            child = _wxyz(result[joint.child])
            flexion, _ = solve_hinge_flexion_deg(parent, child, joint)
            projected, _ = reconstruct_distal_orientation(parent, child, flexion, joint)
            result[joint.child] = Rotation.from_quat(projected[0, [1, 2, 3, 0]]).as_matrix()
        return result

    def _frame(
        self, event: ContinuousEvent, record: TypedEvent,
        acceleration: np.ndarray, latest_vqf: Mapping[str, np.ndarray],
        *, availability_global_ns: int, anchor=None,
    ) -> OwnedNative200HistoryFrame:
        if (set(latest_vqf) != set(NODE_TO_SEGMENT)
                or set(self._segment_from_vqf_right) != set(SEGMENTS)):
            raise RuntimeError("body pose gap publication precedes a complete accepted anchor")
        if anchor is None:
            rotations = self._project_hinges({
                segment: latest_vqf[node] @ self._segment_from_vqf_right[segment]
                for node, segment in NODE_TO_SEGMENT.items()
            })
            points = corrected_proxy_points(
                rotations, {segment: np.zeros(3) for segment in SEGMENTS}, self._geometry,
            )
            offsets = {node: points[point] for node, point in NODE_TO_PROXY_POINT.items()}
            dt = None if self._last_pelvis_ns is None else (event.common_global_ns - self._last_pelvis_ns) * 1e-9
            velocities = {
                node: np.zeros(3) if self._last_offsets is None or not dt or dt <= 0
                else (offsets[node] - self._last_offsets[node]) / dt
                for node in offsets
            }
            normals = {
                node: rotations[NODE_TO_SEGMENT[node]]
                @ self._anchor_normals_segment[node]
                for node in NODE_TO_SEGMENT
            }
            pelvis_sensor_rotation = (
                latest_vqf[PELVIS_NODE] @ self._pelvis_sensor_from_vqf_right
            )
            joints = points
            point_constraints = {}
            contact_owner = "0" * 64
            source = "VQF_DELTA_HINGE_IK_FROZEN_FK"
        else:
            rotations = anchor.base_rotations_world
            offsets = anchor.offsets_world_m
            velocities = anchor.offset_velocities_world_mps
            normals = anchor.normals_world
            joints = anchor.joints_relative_world_m
            point_constraints = anchor.point_constraints_world_m
            pelvis_sensor_rotation = anchor.imu_sample.rotation_world_from_sensor
            contact_owner = anchor.contact_owner_digest
            source = "EXACT_ACQUIRED_NATIVE200_ANCHOR"
        imu = ImuSample(
            event.common_global_ns * 1e-9, availability_global_ns * 1e-9,
            acceleration, pelvis_sensor_rotation, record.sequence,
        )
        pose_digest = _sha({
            "schema": "biospur.c2.full_session.body_pose.v1",
            "event": event.event_id, "revision": self._revision,
            "rotations": {key: value.tolist() for key, value in rotations.items()},
            "geometry": self._geometry_digest, "source": source,
        })
        return OwnedNative200HistoryFrame(
            node=PELVIS_NODE, boot_epoch=record.boot_epoch,
            timer2_base_us=int(record.payload["base_timer2_us"]),
            source_timer_us=record.node_timer_us,
            source_global_ns=event.common_global_ns,
            clock_mapping_digest=event.clock_mapping_digest,
            clock_owner_sha256=event.clock_owner_sha256,
            clock_source_sha256=event.clock_source_sha256,
            publication_revision=self._revision,
            source_frame=self._pelvis_publications,
            action_id=SESSION_ID, imu_sample=imu,
            base_rotations_world=rotations, offsets_world_m=offsets,
            offset_velocities_world_mps=velocities, normals_world=normals,
            joints_relative_world_m=joints,
            point_constraints_world_m=point_constraints,
            raw_provenance=record.raw, imu_owner_sha256=self._owner_digest,
            publication_owner_sha256=self._owner_digest,
            pose_publication_digest=pose_digest,
            base_pose_owner_digest=self._owner_digest,
            body_proxy_owner_sha256=self._geometry_digest,
            contact_owner_digest=contact_owner,
            provenance=f"FULL_SESSION_LABEL_FREE_{source}",
        )

    def _floor_or_none(self, node: str, query_ns: int) -> _OrientationSample | None:
        for sample in reversed(self._orientation_history[node]):
            if sample.common_global_ns <= query_ns:
                return sample
        return None

    def _release_ready(
        self, current_availability_ns: int,
    ) -> tuple[OwnedNative200HistoryFrame, ...]:
        output: list[OwnedNative200HistoryFrame] = []
        while self._pending_pelvis:
            pending = self._pending_pelvis[0]
            if set(self._first_orientation_ns) != set(NODE_TO_SEGMENT):
                break
            if any(
                self._first_orientation_ns[node] > pending.event.common_global_ns
                for node in NODE_TO_SEGMENT
            ):
                self._pending_pelvis.popleft()
                self._precoverage_pelvis_omissions += 1
                continue
            selected = {
                node: self._floor_or_none(node, pending.event.common_global_ns)
                for node in NODE_TO_SEGMENT
            }
            if any(sample is None for sample in selected.values()):
                raise RuntimeError(
                    "ten-node orientation history expired before a pending pelvis tick"
                )
            self._pending_pelvis.popleft()
            latest = {
                node: selected[node].rotation_vqf for node in NODE_TO_SEGMENT
            }
            availability_ns = max(
                current_availability_ns,
                pending.event.availability_global_ns,
                *(selected[node].availability_global_ns for node in NODE_TO_SEGMENT),
            )
            try:
                if pending.prepared_pose_row is None:
                    anchor = self._producer.publication_for_pelvis_event(
                        pending.record, availability_global_ns=availability_ns,
                    )
                else:
                    anchor = self._producer._publication_for_pelvis_event_with_row(
                        pending.record, availability_global_ns=availability_ns,
                        prepared_row=pending.prepared_pose_row,
                    )
            except ValueError as error:
                if str(error) not in (
                    "pelvis IMU tick has no acquired-pose owner",
                    "event lacks consecutive same-span source frame",
                ):
                    raise
                if not self._segment_from_vqf_right:
                    self._preanchor_pelvis_omissions += 1
                    continue
                frame = self._frame(
                    pending.event, pending.record, pending.acceleration, latest,
                    availability_global_ns=availability_ns,
                )
                self._gap_publications += 1
            else:
                self._segment_from_vqf_right = {
                    segment: latest[node].T @ anchor.base_rotations_world[segment]
                    for node, segment in NODE_TO_SEGMENT.items()
                }
                self._anchor_normals_segment = {
                    node: anchor.base_rotations_world[NODE_TO_SEGMENT[node]].T
                    @ np.asarray(anchor.normals_world[node], float)
                    for node in NODE_TO_SEGMENT
                }
                self._pelvis_sensor_from_vqf_right = (
                    latest[PELVIS_NODE].T
                    @ anchor.imu_sample.rotation_world_from_sensor
                )
                frame = self._frame(
                    pending.event, pending.record, pending.acceleration, latest,
                    availability_global_ns=availability_ns, anchor=anchor,
                )
                self._anchor_publications += 1
            self._last_offsets = {
                key: np.asarray(value).copy()
                for key, value in frame.offsets_world_m.items()
            }
            self._last_pelvis_ns = pending.event.common_global_ns
            self._pelvis_publications += 1
            self._revision += 1
            output.append(frame)
        return tuple(output)

    def _ingest(
        self, event: ContinuousEvent, *, _batch_lease: _RecordBatchLease | None = None,
        _prepared_pose_row: object | None = None,
    ) -> tuple[OwnedNative200HistoryFrame, ...]:
        if _batch_lease is None and (
            self.__active_record_batch is not None
            or self.__active_record_capability is not None
        ):
            raise RuntimeError("legacy body pose ingest is forbidden during a record batch")
        if _batch_lease is not None:
            raw = event.payload_owner.raw
            raw_identity = None if raw is None else (
                raw.record_index, raw.start_offset, raw.end_offset, raw.encoded_sha256,
            )
            if (
                type(_batch_lease) is not _RecordBatchLease
                or _batch_lease is not self.__active_record_batch
                or _batch_lease.owner is not self.__record_batch_owner
                or _batch_lease.coordinator is not self.__record_batch_coordinator
                or _batch_lease.generation != self.__record_batch_generation
                or event.node_id != _batch_lease.node
                or raw_identity != _batch_lease.raw_identity
            ):
                raise RuntimeError("foreign, replayed, or cross-record body pose batch lease")
        record = self._validate(event)
        if (event.node_id == PELVIS_NODE
                and len(self._pending_pelvis) == self._pending_pelvis.maxlen):
            raise OverflowError("body-pose pelvis queue exceeded 320 ms")
        if _batch_lease is None:
            block = qmt.OriEstVQFBlock(0.005)
            block.obj.state = deepcopy(self._blocks[event.node_id].obj.state)
        else:
            block = self._blocks[event.node_id]
        acceleration = np.asarray(record.payload["acc_raw"], float) / 2048.0 * 9.80665
        gyroscope = np.deg2rad(np.asarray(record.payload["gyro_raw"], float) / 16.384)
        quaternion = np.asarray(block.step(gyroscope, acceleration, None), float)
        rotation_vqf = _matrix_from_qmt(quaternion)
        if _batch_lease is None:
            self._blocks[event.node_id] = block
        self._latest_vqf[event.node_id] = rotation_vqf
        self._orientation_history[event.node_id].append(_OrientationSample(
            event.common_global_ns, event.availability_global_ns, rotation_vqf,
        ))
        self._first_orientation_ns.setdefault(event.node_id, event.common_global_ns)
        if event.node_id == PELVIS_NODE:
            self._pending_pelvis.append(_PendingPelvis(
                event, record, acceleration, _prepared_pose_row,
            ))
        self._last_timer[event.node_id] = record.node_timer_us
        self._last_availability[event.node_id] = event.availability_global_ns
        self._imu_events += 1
        return self._release_ready(event.availability_global_ns)

    def ingest_ticket(
        self, ticket: FullSessionImuEventTicket,
    ) -> tuple[OwnedNative200HistoryFrame, ...]:
        if type(ticket) is not FullSessionImuEventTicket:
            raise TypeError("body pose requires an authenticated IMU ticket")
        output = []
        ticket.deliver(lambda event: output.append(self._ingest(event)))
        if len(output) != 1:
            raise RuntimeError("body pose ticket did not deliver exactly once")
        return output[0]

    def ingest_record_batch(
        self, events: tuple[ContinuousEvent, ...], *, authority: object,
        consumer: Callable[[OwnedNative200HistoryFrame], None],
        _record_capability: object | None = None,
    ) -> None:
        """Validate a complete IMU raw record, then ingest it in source order."""
        if authority is not self.__record_batch_coordinator:
            raise RuntimeError("foreign body pose batch coordinator authority")
        if not callable(consumer):
            raise TypeError("body pose batch consumer must be callable")
        if (type(events) is not tuple or not 1 <= len(events) <= 16
                or any(type(event) is not ContinuousEvent for event in events)
                or {event.kind for event in events} != {"IMU"}
                or len({event.node_id for event in events}) != 1):
            raise ValueError("body pose requires one homogeneous IMU raw record")
        node = events[0].node_id
        raw_keys = {
            (event.payload_owner.raw.record_index,
             event.payload_owner.raw.start_offset,
             event.payload_owner.raw.end_offset,
             event.payload_owner.raw.encoded_sha256)
            for event in events
            if type(event.payload_owner) is TypedEvent
            and event.payload_owner.raw is not None
        }
        if len(raw_keys) != 1:
            raise ValueError("body pose IMU raw record identity is mixed")
        if self.__active_record_batch is not None:
            raise RuntimeError("nested body pose record batch is forbidden")
        if (self.__active_record_capability is not None
                and _record_capability is None):
            raise RuntimeError("legacy body pose batch is forbidden during a record delta")
        capability = None
        if _record_capability is not None:
            capability = self._validate_record_batch_capability(
                _record_capability, authority=authority, node=node,
                raw_identity=next(iter(raw_keys)),
            )
        timer = self._last_timer.get(node)
        availability = self._last_availability.get(node)
        records = []
        for event in events:
            record = self._validate_against(
                event, last_timer=timer, last_availability=availability,
            )
            records.append(record)
            timer = record.node_timer_us
            availability = event.availability_global_ns
        if tuple(record.raw.sample_index for record in records) != tuple(range(len(records))):
            raise ValueError("body pose IMU raw-record sample order is invalid")
        prepared_rows = None
        if (
            node == PELVIS_NODE
            and not self._pending_pelvis
            and set(self._first_orientation_ns) == set(NODE_TO_SEGMENT)
            and all(
                first_ns <= events[0].common_global_ns
                for first_ns in self._first_orientation_ns.values()
            )
        ):
            prepared_rows = self._producer._prepare_pelvis_record_rows(tuple(records))
        snapshot = None if capability is not None else self._record_batch_snapshot(node)
        self.__record_batch_generation += 1
        lease = _RecordBatchLease(
            self.__record_batch_owner, authority, self.__record_batch_generation,
            node, next(iter(raw_keys)),
        )
        self.__active_record_batch = lease
        try:
            for index, event in enumerate(events):
                prepared_row = None if prepared_rows is None else prepared_rows[index]
                if prepared_row is None:
                    frames = self._ingest(event, _batch_lease=lease)
                else:
                    frames = self._ingest(
                        event, _batch_lease=lease,
                        _prepared_pose_row=prepared_row,
                    )
                for frame in frames:
                    consumer(frame)
        except BaseException:
            if snapshot is not None:
                self._restore_record_batch_snapshot(snapshot)
            raise
        finally:
            self.__active_record_batch = None

    def _issue_record_batch_capability(
        self, *, authority: object, node: str,
        raw_identity: tuple[int, int, int, str],
    ) -> object:
        if authority is not self.__record_batch_coordinator:
            raise RuntimeError("foreign body pose batch coordinator authority")
        if self.__active_record_capability is not None:
            raise RuntimeError("nested body pose record delta")
        if node not in self._blocks:
            raise ValueError("body pose record delta node is unknown")
        self.__record_batch_generation += 1
        capability = _RecordBatchCapability(
            self.__record_batch_owner, authority, self.__record_batch_generation,
            node, raw_identity,
            _RecordBatchCapabilityState(self._record_batch_snapshot(node)),
        )
        self.__active_record_capability = capability
        return capability

    def _validate_record_batch_capability(
        self, capability: object, *, authority: object, node: str,
        raw_identity: tuple[int, int, int, str],
    ) -> _RecordBatchCapability:
        if (
            type(capability) is not _RecordBatchCapability
            or capability.owner is not self.__record_batch_owner
            or capability.coordinator is not authority
            or authority is not self.__record_batch_coordinator
            or capability is not self.__active_record_capability
            or capability.node != node
            or capability.raw_identity != raw_identity
            or capability.state.status != "ACTIVE"
            or capability.state.delta is None
        ):
            raise RuntimeError("stale or foreign body pose record capability")
        return capability

    def _close_record_batch_capability(
        self, capability: object, *, authority: object,
    ) -> None:
        active = self._validate_record_batch_capability(
            capability, authority=authority, node=capability.node,
            raw_identity=capability.raw_identity,
        )
        self.__active_record_capability = None
        active.state.status = "CLOSED"

    def _finalize_record_batch_capability(
        self, capability: object, *, authority: object,
    ) -> None:
        self._validate_finalize_record_batch_capability(
            capability, authority=authority,
            raw_identity=getattr(capability, "raw_identity", None),
        )
        self._discard_record_batch_capability(capability)

    def _validate_finalize_record_batch_capability(
        self, capability: object, *, authority: object,
        raw_identity: tuple[int, int, int, str] | None,
    ) -> None:
        if (
            type(capability) is not _RecordBatchCapability
            or capability.owner is not self.__record_batch_owner
            or capability.coordinator is not authority
            or authority is not self.__record_batch_coordinator
            or capability.raw_identity != raw_identity
            or capability.state.status != "CLOSED"
            or capability.state.delta is None
        ):
            raise RuntimeError("stale or foreign body pose record capability")

    @staticmethod
    def _discard_record_batch_capability(capability: object) -> None:
        """Discard one prevalidated CLOSED delta; assignments cannot fail."""
        capability.state.delta = None
        capability.state.status = "FINALIZED"

    def _rollback_record_batch_capability(
        self, capability: object, *, authority: object,
    ) -> None:
        if (
            type(capability) is not _RecordBatchCapability
            or capability.owner is not self.__record_batch_owner
            or capability.coordinator is not authority
            or authority is not self.__record_batch_coordinator
            or capability.state.status not in ("ACTIVE", "CLOSED")
            or capability.state.delta is None
        ):
            raise RuntimeError("stale or foreign body pose record capability")
        self._restore_record_batch_snapshot(capability.state.delta)
        if self.__active_record_capability is capability:
            self.__active_record_capability = None
        capability.state.delta = None
        capability.state.status = "ROLLED_BACK"

    def _record_batch_snapshot(self, node: str) -> _RecordBatchDelta:
        return _RecordBatchDelta(
            node=node,
            block=self._blocks[node],
            block_state=deepcopy(self._blocks[node].obj.state),
            latest_present=node in self._latest_vqf,
            latest_vqf=deepcopy(self._latest_vqf.get(node)),
            orientation_history=deepcopy(self._orientation_history[node]),
            first_present=node in self._first_orientation_ns,
            first_orientation_ns=self._first_orientation_ns.get(node),
            timer_present=node in self._last_timer,
            last_timer=self._last_timer.get(node),
            availability_present=node in self._last_availability,
            last_availability=self._last_availability.get(node),
            segment_from_vqf_right=deepcopy(self._segment_from_vqf_right),
            anchor_normals_segment=deepcopy(self._anchor_normals_segment),
            pelvis_sensor_from_vqf_right=deepcopy(self._pelvis_sensor_from_vqf_right),
            pending_pelvis=deepcopy(self._pending_pelvis),
            last_offsets=deepcopy(self._last_offsets),
            last_pelvis_ns=self._last_pelvis_ns,
            counters=(
                self._imu_events, self._pelvis_publications,
                self._anchor_publications, self._gap_publications,
                self._precoverage_pelvis_omissions,
                self._preanchor_pelvis_omissions, self._revision,
            ),
            producer_state=tuple(
                (name, getattr(self._producer, name)) for name in
                ("_revision", "_last_ns", "_rank") if hasattr(self._producer, name)
            ),
        )

    def _restore_record_batch_snapshot(self, snapshot: _RecordBatchDelta) -> None:
        node = snapshot.node
        if self._blocks[node] is not snapshot.block:
            raise RuntimeError("body pose batch changed its persistent VQF block identity")
        snapshot.block.obj.state = snapshot.block_state
        if snapshot.latest_present:
            self._latest_vqf[node] = snapshot.latest_vqf
        else:
            self._latest_vqf.pop(node, None)
        self._orientation_history[node] = snapshot.orientation_history
        if snapshot.first_present:
            self._first_orientation_ns[node] = snapshot.first_orientation_ns
        else:
            self._first_orientation_ns.pop(node, None)
        if snapshot.timer_present:
            self._last_timer[node] = snapshot.last_timer
        else:
            self._last_timer.pop(node, None)
        if snapshot.availability_present:
            self._last_availability[node] = snapshot.last_availability
        else:
            self._last_availability.pop(node, None)
        self._segment_from_vqf_right = snapshot.segment_from_vqf_right
        self._anchor_normals_segment = snapshot.anchor_normals_segment
        self._pelvis_sensor_from_vqf_right = snapshot.pelvis_sensor_from_vqf_right
        self._pending_pelvis = snapshot.pending_pelvis
        self._last_offsets = snapshot.last_offsets
        self._last_pelvis_ns = snapshot.last_pelvis_ns
        (self._imu_events, self._pelvis_publications,
         self._anchor_publications, self._gap_publications,
         self._precoverage_pelvis_omissions,
         self._preanchor_pelvis_omissions, self._revision) = snapshot.counters
        for name, value in snapshot.producer_state:
            setattr(self._producer, name, value)

    def finish(self) -> tuple[OwnedNative200HistoryFrame, ...]:
        """Release final causal frames and fail closed on unresolved ownership."""
        if self.__active_record_batch is not None:
            raise RuntimeError("body pose finish is forbidden during a record batch")
        if set(self._first_orientation_ns) != set(NODE_TO_SEGMENT):
            missing = sorted(set(NODE_TO_SEGMENT) - set(self._first_orientation_ns))
            raise RuntimeError(f"full session lacks IMU history for nodes: {missing}")
        availability_ns = max(self._last_availability.values(), default=0)
        output = self._release_ready(availability_ns)
        if self._pending_pelvis:
            raise RuntimeError(
                f"full session ended with {len(self._pending_pelvis)} unresolved pelvis ticks"
            )
        return output

    def audit(self) -> FullSessionBodyPoseAudit:
        return FullSessionBodyPoseAudit(
            self._imu_events, self._pelvis_publications,
            self._anchor_publications, self._gap_publications,
            self._precoverage_pelvis_omissions,
            self._preanchor_pelvis_omissions,
            self._owner_digest,
        )
