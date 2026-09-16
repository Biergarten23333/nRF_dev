"""Bounded continuous UWB grouping composed around authoritative owners.

This owner owns only pending sweep buckets and immutable audit sidecars.  Pose,
contact, root, robust range, and articulated estimator state remain owned by the
injected composition.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
from collections import deque
from dataclasses import dataclass, replace as dataclass_replace
from types import MappingProxyType
from typing import Callable, Mapping, Protocol

import numpy as np

from biospur_fusion.c2_articulated_biomechanics.hinge_temporal import (
    HingeTemporalRetentionContract,
    ObsoleteNative200SourcePair,
    ObsoleteNative200SourcePairDiagnostic,
)
from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import (
    ArticulatedEpochOwner,
    AuthoritativeArticulatedFusion,
    Native200ExactBasePose,
    Native200SourcePair,
    PreparedArticulatedAdmission,
)
from biospur_fusion.c2_uwb_root_world.continuous_consensus_drift import (
    ContinuousConsensusDriftOwner,
    PreparedContinuousConsensusDrift,
)
from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import (
    EPOCH_PERIOD_NS,
    MAXIMUM_POSE_AGE_NS,
    canonical_group_frame_lower_ns,
    group_epoch_times_ns,
)
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
    BShadowGeometryOwner,
    BShadowSnapshotOwner,
    BoundGroupPacket,
    DIAGNOSTIC_ASSEMBLY_PERIOD_S,
    DIAGNOSTIC_HORIZON_S,
    DIAGNOSTIC_NATIVE200_PERIOD_S,
    U3SigmaOwner,
    U5BSigmaOwner,
)
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import PoseTagLinkOwner
from biospur_fusion.c2_uwb_root_world.u0 import UwbRow
from biospur_fusion.ingest.events import RawByteProvenance, RecordType, TypedEvent
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import (
    Native200PoseBatchInput,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.root_r3.estimator import (
    PreparedRootFutureImuTransaction,
    PreparedRootFutureImuVelocityTransaction,
    PreparedRootImuTransaction,
    PreparedRootImuVelocityTransaction,
    RootTranslationEdgeMode,
)
from biospur_fusion.root_r3.models import ImuSample, RootState, SystemMode

from .continuous_frontend import ContinuousEvent, canonical_clock_global_ns
from .continuous_uwb_owner import PreparedSubownerUpdate, SubownerResult


CONTINUOUS_GROUP_EPOCH_SCHEMA = "biospur.c2.continuous_group_epoch_owner.v1"
ASSEMBLY_HORIZON_NS = int(DIAGNOSTIC_ASSEMBLY_PERIOD_S * 1_000_000_000)
MAX_PENDING_BUCKETS = 3
ROWS_PER_BUCKET = 10
ANCHORS_PER_ROW = 8
DIAGNOSTIC_RING_CAPACITY = 64
CONTINUOUS_HINGE_RETENTION_CONTRACT = HingeTemporalRetentionContract(
    maximum_source_latency_ns=int(round(DIAGNOSTIC_HORIZON_S * 1_000_000_000)),
)
NATIVE200_HISTORY_CAPACITY = (
    math.ceil(
        (DIAGNOSTIC_HORIZON_S + MAXIMUM_POSE_AGE_NS * 1e-9)
        / DIAGNOSTIC_NATIVE200_PERIOD_S
    )
    + 1
)


@dataclass(frozen=True)
class StalePoseLinkDiagnostic:
    query_ns: int
    latest_ns: int
    age_ns: int
    terminal: bool

    def __post_init__(self) -> None:
        if (type(self.query_ns) is not int or type(self.latest_ns) is not int
                or type(self.age_ns) is not int or type(self.terminal) is not bool
                or self.age_ns != self.query_ns - self.latest_ns
                or self.age_ns <= MAXIMUM_POSE_AGE_NS):
            raise ValueError("invalid stale pose-link diagnostic")


class StalePoseLinkUnavailable(ValueError):
    def __init__(self, diagnostic: StalePoseLinkDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__("retained native200 pose is too old for link")


def canonical_epoch_bucket(common_global_ns: int) -> int:
    """Match the established NumPy nearest/rint 120 ms epoch policy exactly."""
    if type(common_global_ns) is not int:
        raise TypeError("epoch time must be exact integer ns")
    return int(np.rint(np.float64(common_global_ns) / np.float64(EPOCH_PERIOD_NS)))


def canonical_group_availability_time_s(
    rows: tuple[UwbRow, ...], *, clocks: Mapping[str, object],
    availability_global_ns: int,
) -> float:
    """Gate exact source chronology before converting availability to float."""
    if type(availability_global_ns) is not int or availability_global_ns < 0:
        raise ValueError("authoritative availability must be canonical integer ns")
    frame_lower_ns = canonical_group_frame_lower_ns(rows, clocks=clocks)
    if availability_global_ns < frame_lower_ns:
        raise ValueError("authoritative availability precedes group frame lower bound")
    return availability_global_ns * 1e-9


def _digest_payload(value: object) -> str:
    def plain(item):
        if isinstance(item, np.ndarray):
            return {"dtype": item.dtype.str, "shape": item.shape, "bytes": item.tobytes().hex()}
        if isinstance(item, Mapping):
            return {str(key): plain(val) for key, val in sorted(item.items())}
        if isinstance(item, (tuple, list)):
            return [plain(val) for val in item]
        if isinstance(item, np.generic):
            return item.item()
        return item
    return hashlib.sha256(json.dumps(
        plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _valid_link_count(row: UwbRow) -> int:
    if tuple(row.anchor_ids) != tuple(range(ANCHORS_PER_ROW)):
        raise ValueError("noncanonical anchor identity")
    return sum(
        bool(row.valid_mask & (1 << anchor))
        and 0 < int(row.ranges_mm[anchor]) < 0xFFFF
        and math.isfinite(float(row.t_round_us[anchor]))
        and float(row.t_round_us[anchor]) >= 0.0
        for anchor in range(ANCHORS_PER_ROW)
    )


def _row_from_event(event: ContinuousEvent) -> UwbRow:
    source = event.payload_owner
    if isinstance(source, UwbRow):
        row = source
    elif isinstance(source, TypedEvent) and source.record_type is RecordType.UWB:
        value = source.payload
        row = UwbRow(
            node=source.node_id, boot=source.boot_epoch, sequence=source.sequence,
            sweep=int(value["sweep"]), strobe_us=int(value["strobe_us"]),
            frame_us=int(value["frame_us"]),
            anchor_ids=tuple(int(x) for x in value["anchor_id"]),
            ranges_mm=tuple(int(x) for x in value["range_mm"]),
            t_round_us=tuple(int(x) for x in value["t_round_us"]),
            quality=tuple(int(x) for x in value["quality_percent"]),
            valid_mask=int(value["valid_mask"]), identity=int(value["identity"]),
            node_ms=int(value["node_ms"]),
        )
    else:
        raise TypeError("UWB event lacks a decoded raw-range row")
    if row.node != event.node_id or row.boot != event.boot_epoch:
        raise ValueError("decoded UWB row identity differs from event clock owner")
    if row.strobe_us != event.uwb_timer2.strobe_timer2_us or row.frame_us != event.uwb_timer2.frame_timer2_us:
        raise ValueError("decoded UWB row TIMER2 ownership mismatch")
    return row


@dataclass(frozen=True)
class LowLinkMeasurementUsabilityRejection:
    event_id: str
    node: str
    boot_epoch: int
    sequence: int
    sweep: int
    strobe_us: int
    frame_us: int
    row_identity: int
    node_ms: int
    clock_mapping_digest: str
    clock_owner_sha256: str
    clock_source_sha256: str
    valid_link_count: int

    def __post_init__(self) -> None:
        if (
            not self.event_id or not self.node
            or any(type(value) is not int for value in (
                self.boot_epoch, self.sequence, self.sweep, self.strobe_us,
                self.frame_us, self.row_identity, self.node_ms,
                self.valid_link_count,
            ))
            or not 0 <= self.valid_link_count <= 3
            or any(len(value) != 64 for value in (
                self.clock_mapping_digest, self.clock_owner_sha256,
                self.clock_source_sha256,
            ))
        ):
            raise ValueError("invalid low-link measurement-usability rejection")


def _low_link_rejection(
    event: ContinuousEvent, row: UwbRow, valid_link_count: int,
) -> LowLinkMeasurementUsabilityRejection:
    return LowLinkMeasurementUsabilityRejection(
        event.event_id, row.node, row.boot, row.sequence, row.sweep,
        row.strobe_us, row.frame_us, row.identity, row.node_ms,
        event.clock_mapping_digest, event.clock_owner_sha256,
        event.clock_source_sha256, valid_link_count,
    )


def _event_region_identity(event: ContinuousEvent) -> str:
    return event.region_id or event.action_id


def _frozen_vector_map(
    values: Mapping[str, np.ndarray], *, keys: frozenset[str], shape: tuple[int, ...],
    label: str,
) -> Mapping[str, np.ndarray]:
    if set(values) != set(keys):
        raise ValueError(f"{label} inventory mismatch")
    frozen = {}
    for key in sorted(keys):
        value = np.asarray(values[key], dtype=float).reshape(shape).copy()
        if not np.isfinite(value).all():
            raise ValueError(f"{label} contains non-finite values")
        value.setflags(write=False)
        frozen[key] = value
    return MappingProxyType(frozen)


@dataclass(frozen=True)
class OwnedNative200HistoryFrame:
    """One immutable upstream 200 Hz pose publication, not a UWB group."""

    node: str
    boot_epoch: int
    timer2_base_us: int
    source_timer_us: int
    source_global_ns: int
    clock_mapping_digest: str
    clock_owner_sha256: str
    clock_source_sha256: str
    publication_revision: int
    source_frame: int
    action_id: str
    imu_sample: ImuSample
    base_rotations_world: Mapping[str, np.ndarray]
    offsets_world_m: Mapping[str, np.ndarray]
    offset_velocities_world_mps: Mapping[str, np.ndarray]
    normals_world: Mapping[str, np.ndarray]
    joints_relative_world_m: Mapping[str, np.ndarray]
    point_constraints_world_m: Mapping[str, np.ndarray]
    raw_provenance: RawByteProvenance
    imu_owner_sha256: str
    publication_owner_sha256: str
    pose_publication_digest: str
    base_pose_owner_digest: str
    body_proxy_owner_sha256: str
    contact_owner_digest: str
    provenance: str
    digest: str = ""

    def __post_init__(self) -> None:
        exact_ints = (
            self.boot_epoch, self.timer2_base_us, self.source_timer_us,
            self.source_global_ns,
            self.publication_revision, self.source_frame,
        )
        if (
            not self.node or not self.action_id or not self.provenance
            or any(isinstance(value, bool) or not isinstance(value, (int, np.integer))
                   for value in exact_ints)
            or self.timer2_base_us < 0 or self.source_timer_us < self.timer2_base_us
            or self.source_global_ns < 0
            or self.publication_revision < 0 or self.source_frame < 0
            or any(len(value) != 64 for value in (
                self.clock_mapping_digest, self.clock_owner_sha256,
                self.clock_source_sha256, self.imu_owner_sha256,
                self.publication_owner_sha256, self.pose_publication_digest,
                self.base_pose_owner_digest,
                self.body_proxy_owner_sha256, self.contact_owner_digest,
            ))
            or type(self.imu_sample) is not ImuSample
            or type(self.raw_provenance) is not RawByteProvenance
        ):
            raise ValueError("invalid native200 history-frame identity")
        raw = self.raw_provenance
        if (
            any(type(value) is not int for value in (
                raw.record_index, raw.sample_index, raw.start_offset, raw.end_offset,
            ))
            or raw.record_index < 0 or raw.sample_index < 0
            or raw.start_offset < 0 or raw.end_offset <= raw.start_offset
            or len(raw.encoded_sha256) != 64
            or any(character not in "0123456789abcdef" for character in raw.encoded_sha256)
        ):
            raise ValueError("invalid native200 raw-byte provenance")
        self.imu_sample.validate()
        if round(self.imu_sample.measurement_time_s * 1e9) != self.source_global_ns:
            raise ValueError("IMU sample does not bind native200 global tick")
        nodes = frozenset(NODE_TO_PROXY_POINT)
        object.__setattr__(self, "base_rotations_world", _frozen_vector_map(
            self.base_rotations_world, keys=frozenset(SEGMENTS), shape=(3, 3),
            label="native200 base rotations",
        ))
        for name in ("offsets_world_m", "offset_velocities_world_mps", "normals_world"):
            object.__setattr__(self, name, _frozen_vector_map(
                getattr(self, name), keys=nodes, shape=(3,), label=name,
            ))
        joints = frozenset(str(key) for key in self.joints_relative_world_m)
        if not joints:
            raise ValueError("native200 joint proxy inventory is empty")
        object.__setattr__(self, "joints_relative_world_m", _frozen_vector_map(
            self.joints_relative_world_m, keys=joints, shape=(3,),
            label="native200 joint proxies",
        ))
        point_keys = frozenset(str(key) for key in self.point_constraints_world_m)
        if point_keys - {"ankle_left", "ankle_right"}:
            raise ValueError("unsupported historical point constraint")
        object.__setattr__(self, "point_constraints_world_m", _frozen_vector_map(
            self.point_constraints_world_m, keys=point_keys, shape=(3,),
            label="native200 point constraints",
        ))
        force = np.asarray(self.imu_sample.specific_force_sensor_mps2, float).reshape(3).copy()
        rotation = np.asarray(self.imu_sample.rotation_world_from_sensor, float).reshape(3, 3).copy()
        force.setflags(write=False); rotation.setflags(write=False)
        object.__setattr__(self, "imu_sample", ImuSample(
            self.imu_sample.measurement_time_s, self.imu_sample.availability_time_s,
            force, rotation, self.imu_sample.source_sequence,
            self.imu_sample.m1_valid, self.imu_sample.m1_reset,
        ))
        value = _digest_payload({
            "schema": "biospur.c2.owned_native200_history_frame.v1",
            "identity": (self.node, int(self.boot_epoch), int(self.timer2_base_us),
                         int(self.source_timer_us),
                         int(self.source_global_ns), int(self.publication_revision),
                         int(self.source_frame), self.action_id),
            "clock": (self.clock_mapping_digest, self.clock_owner_sha256,
                      self.clock_source_sha256),
            "imu": vars(self.imu_sample),
            "base": self.base_rotations_world,
            "offsets": self.offsets_world_m,
            "velocities": self.offset_velocities_world_mps,
            "normals": self.normals_world,
            "joints": self.joints_relative_world_m,
            "points": self.point_constraints_world_m,
            "raw": vars(self.raw_provenance),
            "owners": (self.imu_owner_sha256, self.publication_owner_sha256,
                       self.pose_publication_digest, self.base_pose_owner_digest,
                       self.body_proxy_owner_sha256, self.contact_owner_digest),
            "provenance": self.provenance,
        })
        if self.digest and self.digest != value:
            raise ValueError("native200 history-frame digest mismatch")
        object.__setattr__(self, "digest", value)


@dataclass(frozen=True)
class _PreparedNative200History:
    owner_key: object
    base_revision: int
    frame: OwnedNative200HistoryFrame
    source_pair: Native200SourcePair | None
    previous_base_pose: Native200ExactBasePose | None
    current_base_pose: Native200ExactBasePose | None


@dataclass(frozen=True)
class _PreparedNative200HistoryOverride:
    owner_key: object
    base_revision: int
    native: _PreparedNative200History
    position_plan: object | None
    future_imu: PreparedRootFutureImuTransaction | None
    current_imu: PreparedRootImuTransaction | None
    root_plan: object
    frame_digest: str
    native_plan_digest: str
    digest: str


def _prepared_native200_digest(plan: _PreparedNative200History) -> str:
    def source(value):
        if value is None:
            return None
        mapping = value.clock_mapping_owner
        return (
            value.node, value.boot_epoch, value.previous_timer_us,
            value.current_timer_us, value.previous_global_ns,
            value.current_global_ns, mapping.node, mapping.clock_domain,
            mapping.boot_epoch, mapping.a_ns_per_us, mapping.b_ns,
            mapping.clock_owner_sha256, mapping.digest,
        )
    def pose(value):
        if value is None:
            return None
        return {
            "identity": (
                value.node, value.boot_epoch, value.source_timer_us,
                value.source_global_ns, value.clock_mapping_digest,
                value.base_pose_owner_digest,
            ),
            "rotations": value.base_rotations_world,
        }
    return _digest_payload({
        "base_revision": plan.base_revision,
        "frame_digest": plan.frame.digest,
        "source_pair": source(plan.source_pair),
        "previous_base_pose": pose(plan.previous_base_pose),
        "current_base_pose": pose(plan.current_base_pose),
    })


def _gap_identity_digest(gap: ContinuousEvent) -> str:
    return _digest_payload({
        "schema": "C2_CONTINUOUS_GAP_IDENTITY_V1",
        "gap": (
            gap.event_id, gap.kind, gap.action_id, gap.common_global_ns,
            gap.availability_global_ns, gap.node_id, gap.boot_epoch,
            gap.clock_domain, gap.clock_mapping_digest,
            gap.clock_owner_sha256, gap.clock_source_sha256,
            gap.gap_start_global_ns, gap.gap_covariance_growth,
        ),
    })


def _gap_endpoint_identity_digest(endpoint: ContinuousEvent) -> str:
    frame = endpoint.payload_owner
    if type(frame) is not OwnedNative200HistoryFrame:
        raise TypeError("gap endpoint lacks an owned native200 frame")
    raw = frame.raw_provenance
    return _digest_payload({
        "schema": "C2_CONTINUOUS_GAP_ENDPOINT_NATIVE200_V1",
        "endpoint": (
            endpoint.event_id, endpoint.kind, endpoint.action_id,
            endpoint.common_global_ns, endpoint.availability_global_ns,
            endpoint.node_id, endpoint.boot_epoch, endpoint.clock_domain,
            endpoint.clock_mapping_digest, endpoint.clock_owner_sha256,
            endpoint.clock_source_sha256, frame.digest,
            frame.timer2_base_us, frame.source_timer_us,
            frame.source_global_ns, frame.publication_revision,
            raw.record_index, raw.start_offset, raw.end_offset,
            raw.encoded_sha256, raw.sample_index,
        ),
    })


def _imu_sample_semantic_identity(sample: ImuSample) -> tuple[object, ...]:
    force = np.asarray(sample.specific_force_sensor_mps2)
    rotation = np.asarray(sample.rotation_world_from_sensor)
    return (
        sample.measurement_time_s, sample.availability_time_s,
        force.dtype.str, force.shape, force.tobytes(),
        rotation.dtype.str, rotation.shape, rotation.tobytes(),
        sample.source_sequence, sample.m1_valid, sample.m1_reset,
    )


@dataclass(frozen=True)
class _PreparedNative200HistoryBatch:
    owner_key: object
    base_revision: int
    frames: tuple[OwnedNative200HistoryFrame, ...]
    pose_plan: object | None


@dataclass(frozen=True)
class _PreparedContinuousGap:
    owner_key: object
    base_revision: int
    root_plan: object
    continuity_generation: int


@dataclass(frozen=True)
class _PreparedContinuousGapNative200:
    owner_key: object
    base_revision: int
    gap: ContinuousEvent
    endpoint: ContinuousEvent
    native: _PreparedNative200History
    root_plan: object
    continuity_generation: int
    gap_identity_digest: str
    endpoint_identity_digest: str
    digest: str


def _prepared_gap_native200_digest(
    plan: _PreparedContinuousGapNative200,
) -> str:
    return _digest_payload({
        "schema": "C2_PREPARED_CONTINUOUS_GAP_ENDPOINT_NATIVE200_V1",
        "base_revision": plan.base_revision,
        "native": _prepared_native200_digest(plan.native),
        "root": getattr(plan.root_plan, "digest", ""),
        "continuity_generation": plan.continuity_generation,
        "gap_identity": plan.gap_identity_digest,
        "endpoint_identity": plan.endpoint_identity_digest,
    })


@dataclass(frozen=True)
class _ContinuousHistorySnapshot:
    revision: int
    frames: tuple[OwnedNative200HistoryFrame, ...]
    retired_frames: tuple[OwnedNative200HistoryFrame, ...]
    post_gap_bootstrap: bool


class AuthoritativeContinuousHistoryOwner:
    """Bounded causal native200/pose/contact history adjacent to the engine."""

    def __init__(
        self, *, engine: AuthoritativeArticulatedFusion,
        a_sigma_owner: U3SigmaOwner, b_sigma_owner: U5BSigmaOwner,
        b_shadow_provenance: str, history_provenance: str,
        hinge_retention_contract: HingeTemporalRetentionContract,
    ) -> None:
        if (
            type(a_sigma_owner) is not U3SigmaOwner
            or type(b_sigma_owner) is not U5BSigmaOwner
            or not b_shadow_provenance or not history_provenance
        ):
            raise ValueError("invalid continuous history static ownership")
        if hinge_retention_contract != CONTINUOUS_HINGE_RETENTION_CONTRACT:
            raise ValueError("continuous history requires its exact derived hinge-retention contract")
        if engine.pose.hinge_temporal_retention_contract != hinge_retention_contract:
            raise ValueError("articulated pose was constructed with a different retention contract")
        self.engine = engine
        self.a_sigma_owner = a_sigma_owner
        self.b_sigma_owner = b_sigma_owner
        self.b_shadow_provenance = str(b_shadow_provenance)
        self.history_provenance = str(history_provenance)
        self.hinge_retention_contract = hinge_retention_contract
        self._frames: deque[OwnedNative200HistoryFrame] = deque(
            maxlen=NATIVE200_HISTORY_CAPACITY
        )
        self._retired_frames: deque[OwnedNative200HistoryFrame] = deque(
            maxlen=NATIVE200_HISTORY_CAPACITY
        )
        self._revision = 0
        self._post_gap_bootstrap = False
        self.__owner_key = object()

    def clone_for_engine(
        self, engine: AuthoritativeArticulatedFusion,
    ) -> "AuthoritativeContinuousHistoryOwner":
        clone = AuthoritativeContinuousHistoryOwner(
            engine=engine, a_sigma_owner=self.a_sigma_owner,
            b_sigma_owner=self.b_sigma_owner,
            b_shadow_provenance=self.b_shadow_provenance,
            history_provenance=self.history_provenance,
            hinge_retention_contract=self.hinge_retention_contract,
        )
        clone._frames.extend(self._frames)
        clone._retired_frames.extend(self._retired_frames)
        clone._revision = self._revision
        clone._post_gap_bootstrap = self._post_gap_bootstrap
        return clone

    def clone(self) -> "AuthoritativeContinuousHistoryOwner":
        engine = copy.deepcopy(self.engine)
        return self.clone_for_engine(engine)

    @classmethod
    def _validate_prospective_frames(
        cls, *, engine: AuthoritativeArticulatedFusion,
        frames: tuple[OwnedNative200HistoryFrame, ...],
    ) -> tuple[object, ...]:
        if not 2 <= len(frames) <= NATIVE200_HISTORY_CAPACITY:
            raise ValueError("prospective history exceeds bounded consecutive capacity")
        mappings = []
        previous = None
        for frame in frames:
            if type(frame) is not OwnedNative200HistoryFrame:
                raise TypeError("prospective history requires owned native200 frames")
            mapping = engine.native200_clock_mapping_owner(
                node=frame.node, clock_owner_sha256=frame.clock_owner_sha256,
            )
            if (
                mapping.digest != frame.clock_mapping_digest
                or mapping.global_ns(frame.source_timer_us) != frame.source_global_ns
                or frame.base_pose_owner_digest
                != engine.native200_base_pose_owner_digest
            ):
                raise ValueError("prospective native200 owner mismatch")
            if previous is not None and (
                frame.source_timer_us - previous.source_timer_us != 5_000
                or frame.publication_revision != previous.publication_revision + 1
            ):
                raise ValueError("prospective native200 chronology is not consecutive")
            mappings.append(mapping)
            previous = frame
        return tuple(mappings)

    @classmethod
    def from_prospective_frames(
        cls, *, engine: AuthoritativeArticulatedFusion,
        a_sigma_owner: U3SigmaOwner, b_sigma_owner: U5BSigmaOwner,
        b_shadow_provenance: str, history_provenance: str,
        hinge_retention_contract: HingeTemporalRetentionContract,
        frames: tuple[OwnedNative200HistoryFrame, ...],
    ) -> "AuthoritativeContinuousHistoryOwner":
        """Create located history from causal pose-only pre-location frames."""
        owner = cls(
            engine=engine, a_sigma_owner=a_sigma_owner,
            b_sigma_owner=b_sigma_owner,
            b_shadow_provenance=b_shadow_provenance,
            history_provenance=history_provenance,
            hinge_retention_contract=hinge_retention_contract,
        )
        mappings = cls._validate_prospective_frames(engine=engine, frames=frames)
        previous = None
        for frame, mapping in zip(frames, mappings):
            if previous is not None:
                pair = engine.native200_source_pair(
                    clock_mapping_owner=mapping,
                    previous_timer_us=previous.source_timer_us,
                    current_timer_us=frame.source_timer_us,
                    previous_global_ns=previous.source_global_ns,
                    current_global_ns=frame.source_global_ns,
                )
                engine.sample_native200_pose(
                    time_s=frame.source_global_ns * 1e-9,
                    native200_source_pair=pair,
                    previous_base_pose=engine.exact_native200_base_pose(
                        native200_source_pair=pair, role="previous",
                        base_rotations_world=previous.base_rotations_world,
                        base_pose_owner_digest=previous.base_pose_owner_digest,
                    ),
                    current_base_pose=engine.exact_native200_base_pose(
                        native200_source_pair=pair, role="current",
                        base_rotations_world=frame.base_rotations_world,
                        base_pose_owner_digest=frame.base_pose_owner_digest,
                    ),
                )
            previous = frame
        owner._frames.extend(frames)
        owner._revision = len(frames)
        pose_token = engine.pose.publication_token()
        if (
            pose_token.revision != 0
            or pose_token.latest_sample_s != frames[-1].source_global_ns * 1e-9
        ):
            raise RuntimeError("prospective pose token does not bind source history")
        return owner

    @classmethod
    def prospective_sidecars_without_pose_replay(
        cls, *, engine: AuthoritativeArticulatedFusion,
        a_sigma_owner: U3SigmaOwner, b_sigma_owner: U5BSigmaOwner,
        b_shadow_provenance: str, history_provenance: str,
        hinge_retention_contract: HingeTemporalRetentionContract,
        frames: tuple[OwnedNative200HistoryFrame, ...],
        rows: tuple[UwbRow, ...], measurement_time_s: float,
        availability_time_s: float,
        member_region_identities: tuple[str, ...], evidence_class: str,
    ) -> "AuthoritativeGroupSidecars":
        """Select exact prospective sidecars while deferring pose projection."""
        owner = cls(
            engine=engine, a_sigma_owner=a_sigma_owner,
            b_sigma_owner=b_sigma_owner,
            b_shadow_provenance=b_shadow_provenance,
            history_provenance=history_provenance,
            hinge_retention_contract=hinge_retention_contract,
        )
        cls._validate_prospective_frames(engine=engine, frames=frames)
        owner._frames.extend(frames)
        owner._revision = len(frames)
        return owner.sidecars_for_group(
            rows, measurement_time_s=measurement_time_s,
            availability_time_s=availability_time_s,
            member_region_identities=member_region_identities,
            evidence_class=evidence_class,
        )

    def mutable_owner_tokens(self) -> frozenset[int]:
        return frozenset((id(self), id(self._frames), id(self._retired_frames)))

    def snapshot(self) -> _ContinuousHistorySnapshot:
        return _ContinuousHistorySnapshot(
            self._revision, tuple(self._frames), tuple(self._retired_frames),
            self._post_gap_bootstrap,
        )

    def restore(self, snapshot: object) -> None:
        if type(snapshot) is not _ContinuousHistorySnapshot:
            raise TypeError("invalid continuous history snapshot")
        self._frames.clear(); self._frames.extend(snapshot.frames)
        self._retired_frames.clear(); self._retired_frames.extend(snapshot.retired_frames)
        self._revision = snapshot.revision
        self._post_gap_bootstrap = snapshot.post_gap_bootstrap

    @property
    def frames(self) -> tuple[OwnedNative200HistoryFrame, ...]:
        return tuple(self._frames)

    @property
    def revision(self) -> int:
        return self._revision

    def _prepare_native200_against(
        self, event: ContinuousEvent, prior: OwnedNative200HistoryFrame | None,
        *, base_revision: int, allow_post_gap_bootstrap: bool = False,
    ) -> _PreparedNative200History:
        if event.kind != "IMU" or type(event.payload_owner) is not OwnedNative200HistoryFrame:
            raise TypeError("native200 event requires an owned history-frame payload")
        frame = event.payload_owner
        if (
            event.node_id != frame.node or event.boot_epoch != frame.boot_epoch
            or event.clock_domain != "B306_TIMER2"
            or event.imu_timer2.timer2_base_us != frame.timer2_base_us
            or event.imu_timer2.trigger_timer2_us != frame.source_timer_us
            or event.common_global_ns != frame.source_global_ns
            or event.clock_mapping_digest != frame.clock_mapping_digest
            or event.clock_owner_sha256 != frame.clock_owner_sha256
            or event.clock_source_sha256 != frame.clock_source_sha256
            or round(frame.imu_sample.availability_time_s * 1e9)
            != event.availability_global_ns
        ):
            raise ValueError("native200 event/history ownership mismatch")
        mapping = self.engine.native200_clock_mapping_owner(
            node=frame.node, clock_owner_sha256=frame.clock_owner_sha256,
        )
        if (
            mapping.digest != frame.clock_mapping_digest
            or mapping.global_ns(frame.source_timer_us) != frame.source_global_ns
            or frame.base_pose_owner_digest
            != self.engine.native200_base_pose_owner_digest
        ):
            raise ValueError("native200 frame clock/base owner mismatch")
        pair = previous = current = None
        if prior is not None:
            if (
                prior.node != frame.node or prior.boot_epoch != frame.boot_epoch
                or frame.source_timer_us - prior.source_timer_us != 5_000
                or frame.publication_revision != prior.publication_revision + 1
            ):
                raise ValueError("native200 history is missing, gapped, or reordered")
            pair = self.engine.native200_source_pair(
                clock_mapping_owner=mapping,
                previous_timer_us=prior.source_timer_us,
                current_timer_us=frame.source_timer_us,
                previous_global_ns=prior.source_global_ns,
                current_global_ns=frame.source_global_ns,
            )
            previous = self.engine.exact_native200_base_pose(
                native200_source_pair=pair, role="previous",
                base_rotations_world=prior.base_rotations_world,
                base_pose_owner_digest=prior.base_pose_owner_digest,
            )
            current = self.engine.exact_native200_base_pose(
                native200_source_pair=pair, role="current",
                base_rotations_world=frame.base_rotations_world,
                base_pose_owner_digest=frame.base_pose_owner_digest,
            )
        elif (
            frame.publication_revision != 0
            and not self._post_gap_bootstrap
            and not allow_post_gap_bootstrap
        ):
            raise ValueError("native200 history bootstrap revision must be zero")
        return _PreparedNative200History(
            self.__owner_key, base_revision, frame, pair, previous, current,
        )

    def prepare_native200(self, event: ContinuousEvent) -> _PreparedNative200History:
        prior = self._frames[-1] if self._frames else None
        return self._prepare_native200_against(
            event, prior, base_revision=self._revision,
        )

    def prepare_native200_root_override(
        self, event: ContinuousEvent, *, position_plan: object | None,
        future_imu: PreparedRootFutureImuTransaction | None,
        current_imu: PreparedRootImuTransaction | None,
        root_plan: object,
    ) -> _PreparedNative200HistoryOverride:
        """Bind one precomputed root transaction to the exact owned frame."""

        native = self.prepare_native200(event)
        frame = native.frame
        root = self.engine.root
        if future_imu is None:
            if position_plan is not None or type(root_plan) not in (
                PreparedRootImuTransaction, PreparedRootImuVelocityTransaction,
            ):
                raise TypeError("invalid current-base native200 root override")
            imu = (
                root_plan if type(root_plan) is PreparedRootImuTransaction
                else current_imu
            )
            if type(imu) is not PreparedRootImuTransaction:
                raise TypeError("native200 root override lacks IMU plan")
            root.prevalidate_prepared_imu(imu)
            if (type(root_plan) is PreparedRootImuVelocityTransaction
                    and root_plan.imu_plan_digest != imu.digest):
                raise RuntimeError("native200 B0 override IMU binding mismatch")
        else:
            if current_imu is not None:
                raise TypeError("future native200 override cannot carry current IMU")
            if position_plan is None or type(root_plan) not in (
                PreparedRootFutureImuTransaction,
                PreparedRootFutureImuVelocityTransaction,
            ):
                raise TypeError("invalid future-base native200 root override")
            if root_plan.frame_digest != frame.digest:
                raise ValueError("future root plan does not bind native200 frame")
        planned_imu = (
            root_plan.sample
            if type(root_plan) is PreparedRootImuTransaction
            else future_imu.imu_plan.sample
            if future_imu is not None
            else current_imu.sample
        )
        if planned_imu is not None and (
            planned_imu.measurement_time_s != frame.imu_sample.measurement_time_s
            or planned_imu.availability_time_s != frame.imu_sample.availability_time_s
            or planned_imu.source_sequence != frame.imu_sample.source_sequence
            or planned_imu.specific_force_sensor_mps2.tobytes()
            != frame.imu_sample.specific_force_sensor_mps2.tobytes()
            or planned_imu.rotation_world_from_sensor.tobytes()
            != frame.imu_sample.rotation_world_from_sensor.tobytes()
        ):
            raise ValueError("root override IMU differs from native200 frame")
        root_digest = getattr(root_plan, "digest", "")
        position_digest = getattr(position_plan, "digest", "")
        future_digest = "" if future_imu is None else future_imu.digest
        current_digest = "" if current_imu is None else current_imu.digest
        native_digest = _prepared_native200_digest(native)
        digest = hashlib.sha256("|".join((
            str(self._revision), frame.digest, position_digest,
            future_digest, current_digest, root_digest, native_digest,
        )).encode()).hexdigest()
        return _PreparedNative200HistoryOverride(
            self.__owner_key, self._revision, native, position_plan,
            future_imu, current_imu, root_plan, frame.digest, native_digest,
            digest,
        )

    def prepare_native200_batch(
        self, events: tuple[ContinuousEvent, ...],
    ) -> _PreparedNative200HistoryBatch:
        """Prepare one boundary-free raw-record run without mutating history."""

        if not isinstance(events, tuple) or not 1 <= len(events) <= 16:
            raise ValueError("native200 history batch must own 1..16 rows")
        prior = self._frames[-1] if self._frames else None
        prepared = []
        pose_rows = []
        for event in events:
            row = self._prepare_native200_against(
                event, prior, base_revision=self._revision,
            )
            prepared.append(row)
            if row.source_pair is not None:
                source = row.source_pair
                pose_rows.append(Native200PoseBatchInput(
                    time_s=row.frame.source_global_ns * 1e-9,
                    source_node=source.node,
                    source_boot_epoch=source.boot_epoch,
                    previous_source_timer_us=source.previous_timer_us,
                    source_timer_us=source.current_timer_us,
                    previous_source_global_ns=source.previous_global_ns,
                    source_global_ns=source.current_global_ns,
                    source_clock_mapping_digest=source.mapping_digest,
                    previous_base_rotations_world=(
                        row.previous_base_pose.base_rotations_world
                    ),
                    current_base_rotations_world=(
                        row.current_base_pose.base_rotations_world
                    ),
                ))
            prior = row.frame
        pose_plan = (
            None if not pose_rows
            else self.engine.pose.prepare_native200_batch(tuple(pose_rows))
        )
        return _PreparedNative200HistoryBatch(
            self.__owner_key, self._revision,
            tuple(row.frame for row in prepared), pose_plan,
        )

    def commit_native200(self, prepared: object) -> None:
        if (
            type(prepared) is not _PreparedNative200History
            or prepared.owner_key is not self.__owner_key
            or prepared.base_revision != self._revision
        ):
            raise RuntimeError("STALE_OR_FOREIGN_NATIVE200_HISTORY_PLAN")
        if not self.engine.add_imu(prepared.frame.imu_sample):
            raise RuntimeError("NATIVE200_ROOT_PUBLICATION_REJECTED")
        if prepared.source_pair is not None:
            self.engine.sample_native200_pose(
                time_s=prepared.frame.source_global_ns * 1e-9,
                native200_source_pair=prepared.source_pair,
                previous_base_pose=prepared.previous_base_pose,
                current_base_pose=prepared.current_base_pose,
            )
        self._frames.append(prepared.frame)
        self._post_gap_bootstrap = False
        self._revision += 1

    def commit_native200_root_override(self, prepared: object) -> None:
        if (
            type(prepared) is not _PreparedNative200HistoryOverride
            or prepared.owner_key is not self.__owner_key
            or prepared.base_revision != self._revision
            or prepared.native.owner_key is not self.__owner_key
            or prepared.native.base_revision != self._revision
            or prepared.frame_digest != prepared.native.frame.digest
            or prepared.native_plan_digest
            != _prepared_native200_digest(prepared.native)
        ):
            raise RuntimeError("STALE_OR_FOREIGN_NATIVE200_ROOT_OVERRIDE")
        root_digest = getattr(prepared.root_plan, "digest", "")
        position_digest = getattr(prepared.position_plan, "digest", "")
        future_digest = (
            "" if prepared.future_imu is None else prepared.future_imu.digest
        )
        current_digest = (
            "" if prepared.current_imu is None else prepared.current_imu.digest
        )
        expected = hashlib.sha256("|".join((
            str(prepared.base_revision), prepared.frame_digest,
            position_digest, future_digest, current_digest, root_digest,
            prepared.native_plan_digest,
        )).encode()).hexdigest()
        if not hmac.compare_digest(prepared.digest, expected):
            raise RuntimeError("TAMPERED_NATIVE200_ROOT_OVERRIDE")
        root = self.engine.root
        if type(prepared.root_plan) is PreparedRootFutureImuVelocityTransaction:
            root.commit_imu_velocity_after_position_transaction(
                prepared.position_plan, prepared.future_imu,
                prepared.root_plan,
            )
        elif type(prepared.root_plan) is PreparedRootFutureImuTransaction:
            root.commit_imu_after_position_transaction(
                prepared.position_plan, prepared.root_plan,
            )
        elif type(prepared.root_plan) is PreparedRootImuVelocityTransaction:
            root.commit_imu_velocity_transaction(
                prepared.current_imu, prepared.root_plan,
            )
        elif type(prepared.root_plan) is PreparedRootImuTransaction:
            root.commit_prepared_imu(prepared.root_plan)
        else:
            raise TypeError("unsupported native200 root override")
        native = prepared.native
        if native.source_pair is not None:
            self.engine.sample_native200_pose(
                time_s=native.frame.source_global_ns * 1e-9,
                native200_source_pair=native.source_pair,
                previous_base_pose=native.previous_base_pose,
                current_base_pose=native.current_base_pose,
            )
        self._frames.append(native.frame)
        self._post_gap_bootstrap = False
        self._revision += 1

    def commit_native200_batch(self, prepared: object) -> None:
        """Commit root rows sequentially, then the precomputed pose batch."""

        if (
            type(prepared) is not _PreparedNative200HistoryBatch
            or prepared.owner_key is not self.__owner_key
            or prepared.base_revision != self._revision
        ):
            raise RuntimeError("STALE_OR_FOREIGN_NATIVE200_HISTORY_BATCH")
        for frame in prepared.frames:
            if not self.engine.add_imu(frame.imu_sample):
                raise RuntimeError("NATIVE200_ROOT_PUBLICATION_REJECTED")
        if prepared.pose_plan is not None:
            self.engine.pose.commit_native200_batch(prepared.pose_plan)
        self._frames.extend(prepared.frames)
        self._post_gap_bootstrap = False
        self._revision += len(prepared.frames)

    def prepare_gap(self, event: ContinuousEvent) -> _PreparedContinuousGap:
        if event.kind != "GAP":
            raise TypeError("continuous history gap requires a GAP event")
        duration = (event.common_global_ns - event.gap_start_global_ns) * 1e-9
        if not math.isclose(
            duration, float(event.gap_covariance_growth), rel_tol=0.0, abs_tol=1e-12,
        ):
            raise ValueError("gap covariance duration differs from source interval")
        root_plan = self.engine.root._prepare_no_update_gap(
            gap_start_time_s=event.gap_start_global_ns * 1e-9,
            gap_end_time_s=event.common_global_ns * 1e-9,
            availability_time_s=event.availability_global_ns * 1e-9,
        )
        generation = self.engine.pose.hinge_continuity_generation + 1
        return _PreparedContinuousGap(
            self.__owner_key, self._revision, root_plan, generation,
        )

    def commit_gap(self, prepared: object) -> None:
        if (
            type(prepared) is not _PreparedContinuousGap
            or prepared.owner_key is not self.__owner_key
            or prepared.base_revision != self._revision
        ):
            raise RuntimeError("STALE_OR_FOREIGN_CONTINUOUS_GAP_PLAN")
        self.engine.root._apply_prevalidated_no_update_gap(prepared.root_plan)
        self.engine.pose.reset_hinge_continuity(prepared.continuity_generation)
        self._retired_frames.clear()
        self._retired_frames.extend(self._frames)
        self._frames.clear()
        self._post_gap_bootstrap = True
        self._revision += 1

    def prepare_gap_native200(
        self, gap: ContinuousEvent, endpoint: ContinuousEvent,
    ) -> _PreparedContinuousGapNative200:
        """Prepare a source gap whose right endpoint is the first real IMU."""

        if gap.kind != "GAP" or endpoint.kind != "IMU":
            raise TypeError("gap endpoint transaction requires GAP then IMU")
        if not self._frames:
            raise ValueError("gap endpoint transaction lacks prior native200 history")
        prior = self._frames[-1]
        native = self._prepare_native200_against(
            endpoint, None, base_revision=self._revision,
            allow_post_gap_bootstrap=True,
        )
        frame = native.frame
        if frame.imu_sample.m1_reset or not frame.imu_sample.m1_valid:
            raise ValueError("gap endpoint IMU is not M1-valid")
        if self.engine.root.mode in {
            SystemMode.INITIALIZING,
            SystemMode.TIME_INVALID,
            SystemMode.M1_RESET_RECOVERY,
        }:
            raise RuntimeError("gap endpoint requires an operational root mode")
        duration = (gap.common_global_ns - gap.gap_start_global_ns) * 1e-9
        if (
            gap.common_global_ns != frame.source_global_ns
            or gap.availability_global_ns
            != round(frame.imu_sample.availability_time_s * 1e9)
            or gap.node_id != frame.node
            or gap.boot_epoch != frame.boot_epoch
            or gap.clock_mapping_digest != frame.clock_mapping_digest
            or gap.clock_owner_sha256 != frame.clock_owner_sha256
            or gap.clock_source_sha256 != frame.clock_source_sha256
            or gap.gap_start_global_ns != prior.source_global_ns
            or frame.source_timer_us - prior.source_timer_us <= 5_000
            or not math.isclose(
                duration, float(gap.gap_covariance_growth),
                rel_tol=0.0, abs_tol=1e-12,
            )
        ):
            raise ValueError("gap endpoint does not bind prior and endpoint frames")
        gap_identity = _gap_identity_digest(gap)
        endpoint_identity = _gap_endpoint_identity_digest(endpoint)
        root_source_owner = _digest_payload({
            "schema": "C2_ROOT_GAP_ENDPOINT_SOURCE_OWNER_V1",
            "gap_identity": gap_identity,
            "endpoint_identity": endpoint_identity,
        })
        following_mode = (
            RootTranslationEdgeMode.INERTIAL
            if self.engine.root.inertial
            else RootTranslationEdgeMode.CV_NO_ACCELERATION
        )
        root_plan = self.engine.root._prepare_no_update_gap(
            gap_start_time_s=gap.gap_start_global_ns * 1e-9,
            gap_end_time_s=gap.common_global_ns * 1e-9,
            availability_time_s=gap.availability_global_ns * 1e-9,
            post_gap_sample=frame.imu_sample,
            source_gap_owner=root_source_owner,
            following_input_mode=following_mode,
        )
        generation = self.engine.pose.hinge_continuity_generation + 1
        blank = _PreparedContinuousGapNative200(
            self.__owner_key, self._revision, gap, endpoint,
            native, root_plan, generation,
            gap_identity, endpoint_identity, "",
        )
        return dataclass_replace(blank, digest=_prepared_gap_native200_digest(blank))

    def commit_gap_native200(self, prepared: object) -> None:
        if (
            type(prepared) is not _PreparedContinuousGapNative200
            or prepared.owner_key is not self.__owner_key
            or prepared.base_revision != self._revision
        ):
            raise RuntimeError("STALE_OR_FOREIGN_CONTINUOUS_GAP_ENDPOINT_PLAN")
        gap = prepared.gap
        endpoint = prepared.endpoint
        frame = prepared.native.frame
        root = self.engine.root
        root_plan = prepared.root_plan
        expected = _prepared_gap_native200_digest(prepared)
        if (
            not hmac.compare_digest(prepared.digest, expected)
            or not hmac.compare_digest(
                prepared.gap_identity_digest,
                _gap_identity_digest(prepared.gap),
            )
            or not hmac.compare_digest(
                prepared.endpoint_identity_digest,
                _gap_endpoint_identity_digest(prepared.endpoint),
            )
            or prepared.native.frame is not prepared.endpoint.payload_owner
            or root.mode in {
                SystemMode.INITIALIZING,
                SystemMode.TIME_INVALID,
                SystemMode.M1_RESET_RECOVERY,
            }
            or root_plan.base_revision != root.publication_token().revision
            or root_plan.gap_start_time_s != gap.gap_start_global_ns * 1e-9
            or root_plan.gap_end_time_s != gap.common_global_ns * 1e-9
            or root_plan.availability_time_s
            != gap.availability_global_ns * 1e-9
            or root_plan.post_gap_sample is None
            or _imu_sample_semantic_identity(root_plan.post_gap_sample)
            != _imu_sample_semantic_identity(frame.imu_sample)
            or root_plan.source_gap_owner != _digest_payload({
                "schema": "C2_ROOT_GAP_ENDPOINT_SOURCE_OWNER_V1",
                "gap_identity": prepared.gap_identity_digest,
                "endpoint_identity": prepared.endpoint_identity_digest,
            })
            or root_plan.following_input_mode != (
                RootTranslationEdgeMode.INERTIAL
                if root.inertial
                else RootTranslationEdgeMode.CV_NO_ACCELERATION
            )
            or endpoint.common_global_ns != gap.common_global_ns
            or endpoint.availability_global_ns != gap.availability_global_ns
            or frame.imu_sample.m1_reset
            or not frame.imu_sample.m1_valid
        ):
            raise RuntimeError("TAMPERED_CONTINUOUS_GAP_ENDPOINT_PLAN")
        root._apply_prevalidated_no_update_gap(root_plan)
        self.engine.pose.reset_hinge_continuity(
            prepared.continuity_generation,
        )
        self._retired_frames.clear()
        self._retired_frames.extend(self._frames)
        self._frames.clear()
        self._frames.append(prepared.native.frame)
        self._post_gap_bootstrap = False
        self._revision += 2

    def _strict_floor(self, query_ns: float) -> OwnedNative200HistoryFrame:
        canonical_query_ns = canonical_clock_global_ns(query_ns)
        retained = (*self._retired_frames, *self._frames)
        candidates = [
            frame for frame in retained
            if frame.source_global_ns < canonical_query_ns
        ]
        if not candidates:
            raise ValueError("pose query predates retained native200 history")
        frame = candidates[-1]
        age_ns = canonical_query_ns - frame.source_global_ns
        if age_ns > MAXIMUM_POSE_AGE_NS:
            newest_ns = max(item.source_global_ns for item in retained)
            raise StalePoseLinkUnavailable(StalePoseLinkDiagnostic(
                canonical_query_ns, frame.source_global_ns, age_ns,
                newest_ns >= canonical_query_ns,
            ))
        return frame

    def _source_pair_before(self, measurement_ns: int) -> tuple[
        Native200SourcePair, OwnedNative200HistoryFrame, OwnedNative200HistoryFrame
    ]:
        eligible = [
            frame for frame in (*self._retired_frames, *self._frames)
            if frame.source_global_ns < measurement_ns
        ]
        if len(eligible) < 2:
            raise ValueError("group lacks a strict-past native200 source pair")
        previous, current = eligible[-2:]
        if current.source_timer_us - previous.source_timer_us != 5_000:
            raise ValueError("group native200 source pair is not consecutive")
        mapping = self.engine.native200_clock_mapping_owner(
            node=current.node, clock_owner_sha256=current.clock_owner_sha256,
        )
        pair = self.engine.native200_source_pair(
            clock_mapping_owner=mapping,
            previous_timer_us=previous.source_timer_us,
            current_timer_us=current.source_timer_us,
            previous_global_ns=previous.source_global_ns,
            current_global_ns=current.source_global_ns,
        )
        return pair, previous, current

    def sidecars_for_group(
        self, rows: tuple[UwbRow, ...], *, measurement_time_s: float,
        availability_time_s: float, member_region_identities: tuple[str, ...],
        evidence_class: str,
    ) -> AuthoritativeGroupSidecars:
        measurement_ns = int(round(measurement_time_s * 1e9))
        source_pair, _previous, current = self._source_pair_before(measurement_ns)
        links = []
        shadows = []
        used_frames = {}
        for row in sorted(rows, key=lambda item: item.node):
            clock = self.engine.static.clocks[row.node]
            queries = []
            for anchor in range(ANCHORS_PER_ROW):
                query = float(clock.link_time_ns(
                    event_boot_epoch=row.boot, strobe_us=row.strobe_us,
                    t_round_us=float(row.t_round_us[anchor]),
                ))
                frame = self._strict_floor(query)
                used_frames[frame.digest] = frame
                queries.append(query)
                links.append(PoseTagLinkOwner(
                    row.node, anchor, query, frame.source_global_ns,
                    frame.offsets_world_m[row.node],
                    frame.offset_velocities_world_mps[row.node],
                    frame.source_frame, frame.publication_revision,
                    frame.body_proxy_owner_sha256,
                ))
            shadow_query = min(queries)
            shadow_frame = self._strict_floor(shadow_query)
            shadows.append(BShadowSnapshotOwner(
                row.node, shadow_frame.action_id, shadow_frame.source_frame,
                shadow_frame.source_global_ns, shadow_query,
                shadow_frame.offsets_world_m, shadow_frame.normals_world,
                shadow_frame.joints_relative_world_m,
                shadow_frame.body_proxy_owner_sha256,
            ))
        history_digest = _digest_payload({
            "schema": "biospur.c2.continuous_history_selection.v1",
            "revision": self._revision,
            "frames": [used_frames[key].digest for key in sorted(used_frames)],
            "source_pair": (source_pair.previous_global_ns, source_pair.current_global_ns),
            "contact": current.contact_owner_digest,
        })
        b_shadow = BShadowGeometryOwner(
            self.engine.pose.geometry, tuple(shadows), self.b_shadow_provenance,
        )
        return AuthoritativeGroupSidecars(
            tuple(links), b_shadow, self.a_sigma_owner, self.b_sigma_owner,
            source_pair, current.base_rotations_world,
            current.point_constraints_world_m, history_digest,
            source_pair.mapping_digest, self.engine.model_digest,
            self.history_provenance, contact=None,
            action_specific_prior_used=False,
        )


@dataclass(frozen=True)
class GroupEpochMaterialization:
    packet: BoundGroupPacket
    epoch: ArticulatedEpochOwner
    audit_digest: str
    epoch_digest: str
    action_specific_prior_used: bool = False

    def __post_init__(self) -> None:
        if len(self.audit_digest) != 64 or len(self.epoch_digest) != 64:
            raise ValueError("group/epoch audit digest is invalid")
        if type(self.action_specific_prior_used) is not bool:
            raise ValueError("action-prior audit must be exact bool")


@dataclass(frozen=True)
class AuthoritativeGroupSidecars:
    """Immutable output of the existing native200/pose/contact history owner."""

    pose_links: tuple[PoseTagLinkOwner, ...]
    b_shadow_owner: BShadowGeometryOwner
    a_sigma_owner: U3SigmaOwner
    b_sigma_owner: U5BSigmaOwner
    native200_source_pair: Native200SourcePair
    base_rotations_world: Mapping[str, np.ndarray]
    point_constraints_world_m: Mapping[str, np.ndarray]
    history_owner_digest: str
    clock_owner_digest: str
    model_owner_digest: str
    provenance: str
    dynamic_envelope: object = None
    activity: object = None
    consensus: object = None
    contact: object = None
    action_specific_prior_used: bool = False
    digest: str = ""

    def __post_init__(self) -> None:
        links = tuple(self.pose_links)
        nodes = {row.node for row in links}
        if (not 1 <= len(nodes) <= ROWS_PER_BUCKET
                or len(links) != ANCHORS_PER_ROW * len(nodes)
                or {(row.node, row.anchor) for row in links}
                != {(node, anchor) for node in nodes
                    for anchor in range(ANCHORS_PER_ROW)}):
            raise ValueError("sidecars require complete pose ownership per row")
        if {row.node for row in self.b_shadow_owner.snapshots} != nodes:
            raise ValueError("sidecar shadow/pose node inventory mismatch")
        if any(len(value) != 64 for value in (
            self.history_owner_digest, self.clock_owner_digest, self.model_owner_digest,
        )) or not self.provenance:
            raise ValueError("sidecar owner provenance invalid")
        if type(self.action_specific_prior_used) is not bool:
            raise ValueError("sidecar action-prior audit must be exact bool")
        base = {}
        for key, source in self.base_rotations_world.items():
            value = np.asarray(source, dtype=float).copy()
            if value.shape != (3, 3) or not np.isfinite(value).all():
                raise ValueError("sidecar base rotation invalid")
            value.setflags(write=False)
            base[str(key)] = value
        points = {}
        for key, source in self.point_constraints_world_m.items():
            value = np.asarray(source, dtype=float).reshape(3).copy()
            if not np.isfinite(value).all():
                raise ValueError("sidecar contact point invalid")
            value.setflags(write=False)
            points[str(key)] = value
        object.__setattr__(self, "base_rotations_world", MappingProxyType(base))
        object.__setattr__(self, "point_constraints_world_m", MappingProxyType(points))
        manifest = {
            "pose_links": [
                (row.node, row.anchor, row.query_time_ns, row.pose_time_ns,
                 row.offset_world_m, row.offset_velocity_world_mps,
                 row.source_epoch, row.source_revision, row.source_sha256)
                for row in self.pose_links
            ],
            "shadow": self.b_shadow_owner.digest,
            "a_sigma": self.a_sigma_owner.digest,
            "b_sigma": self.b_sigma_owner.digest,
            "source_pair": {
                "node": self.native200_source_pair.node,
                "boot_epoch": self.native200_source_pair.boot_epoch,
                "previous_timer_us": self.native200_source_pair.previous_timer_us,
                "current_timer_us": self.native200_source_pair.current_timer_us,
                "previous_global_ns": self.native200_source_pair.previous_global_ns,
                "current_global_ns": self.native200_source_pair.current_global_ns,
                "mapping_digest": self.native200_source_pair.mapping_digest,
            },
            "base": self.base_rotations_world,
            "points": self.point_constraints_world_m,
            "history": self.history_owner_digest,
            "clock": self.clock_owner_digest,
            "model": self.model_owner_digest,
            "provenance": self.provenance,
            "action_specific_prior_used": self.action_specific_prior_used,
        }
        value = _digest_payload(manifest)
        if self.digest and self.digest != value:
            raise ValueError("sidecar digest mismatch")
        object.__setattr__(self, "digest", value)


class AuthoritativeHistoryOwner(Protocol):
    def clone(self) -> "AuthoritativeHistoryOwner": ...
    def mutable_owner_tokens(self) -> frozenset[int]: ...
    def snapshot(self) -> object: ...
    def restore(self, snapshot: object) -> None: ...
    def prepare_native200(self, event: ContinuousEvent) -> object: ...
    def commit_native200(self, prepared: object) -> None: ...
    def prepare_native200_batch(self, events: tuple[ContinuousEvent, ...]) -> object: ...
    def commit_native200_batch(self, prepared: object) -> None: ...
    def prepare_gap(self, event: ContinuousEvent) -> object: ...
    def commit_gap(self, prepared: object) -> None: ...
    def prepare_gap_native200(
        self, gap: ContinuousEvent, endpoint: ContinuousEvent,
    ) -> object: ...
    def commit_gap_native200(self, prepared: object) -> None: ...
    def native200_batch_boundary_free(self) -> bool: ...
    def sidecars_for_group(
        self, rows: tuple[UwbRow, ...], *, measurement_time_s: float,
        availability_time_s: float, member_region_identities: tuple[str, ...],
        evidence_class: str,
    ) -> AuthoritativeGroupSidecars: ...


@dataclass(frozen=True)
class _CompositionSnapshot:
    root: object
    pose: object
    robust_trackers: object
    robust_revision: int
    history: object
    drift: object | None = None


@dataclass(frozen=True)
class _PreparedCompositionNative200:
    history_plan: object
    drift_plan: PreparedContinuousConsensusDrift
    drift_admission_plan: PreparedContinuousConsensusDrift | None = None


@dataclass(frozen=True)
class _PreparedCompositionGap:
    history_plan: object
    drift_plan: PreparedContinuousConsensusDrift


@dataclass(frozen=True)
class _PreparedCompositionGapNative200:
    history_plan: object
    drift_plan: PreparedContinuousConsensusDrift | None


class AuthoritativeContinuousGroupComposition:
    """Concrete composition of qualified engine and source-owned histories."""

    def __init__(
        self, *, engine: AuthoritativeArticulatedFusion,
        history: AuthoritativeHistoryOwner,
        clone_factory: Callable[[], tuple[AuthoritativeArticulatedFusion, AuthoritativeHistoryOwner]],
        consensus_drift: ContinuousConsensusDriftOwner | None = None,
    ) -> None:
        self.engine = engine
        self.history = history
        self._clone_factory = clone_factory
        self.consensus_drift = consensus_drift

    def clone(self) -> "AuthoritativeContinuousGroupComposition":
        engine, history = self._clone_factory()
        if engine is self.engine or history is self.history:
            raise ValueError("composition clone aliases authoritative state")
        return AuthoritativeContinuousGroupComposition(
            engine=engine, history=history, clone_factory=self._clone_factory,
            consensus_drift=(
                None if self.consensus_drift is None
                else self.consensus_drift.clone()
            ),
        )

    def mutable_owner_tokens(self) -> frozenset[int]:
        tokens = self.history.mutable_owner_tokens()
        owned = frozenset((
            id(self.engine.root), id(self.engine.pose), id(self.engine.robust),
            id(self.engine.robust.trackers),
        ))
        if owned & tokens:
            raise ValueError("history aliases estimator ownership")
        drift = (
            frozenset() if self.consensus_drift is None
            else self.consensus_drift.mutable_owner_tokens()
        )
        if (owned | tokens) & drift:
            raise ValueError("consensus drift aliases composition ownership")
        return owned | tokens | drift

    def snapshot(self) -> _CompositionSnapshot:
        return _CompositionSnapshot(
            self.engine.root._prepare_position_rollback(),
            self.engine.pose._prepare_install_rollback(),
            copy.deepcopy(self.engine.robust.trackers), self.engine.robust.revision,
            self.history.snapshot(),
            None if self.consensus_drift is None else self.consensus_drift.snapshot(),
        )

    def restore(self, snapshot: _CompositionSnapshot) -> None:
        if type(snapshot) is not _CompositionSnapshot:
            raise TypeError("invalid authoritative composition snapshot")
        self.engine.root._rollback_prevalidated_position(snapshot.root)
        self.engine.pose._rollback_prevalidated_install(snapshot.pose)
        self.engine.robust.trackers = snapshot.robust_trackers
        self.engine.robust.revision = snapshot.robust_revision
        self.history.restore(snapshot.history)
        if self.consensus_drift is None:
            if snapshot.drift is not None:
                raise RuntimeError("disabled consensus drift received state")
        else:
            if snapshot.drift is None:
                raise RuntimeError("enabled consensus drift lacks state")
            self.consensus_drift.restore(snapshot.drift)

    def prepare_native200(
        self, event: ContinuousEvent, *,
        admission: PreparedArticulatedAdmission | None = None,
        commit_admission: bool = False,
        consensus_admission: PreparedContinuousConsensusDrift | None = None,
    ) -> object:
        if self.consensus_drift is None:
            return self.history.prepare_native200(event)
        native = self.history.prepare_native200(event)
        frame = native.frame
        root = self.engine.root
        position_plan = None
        future_imu = None
        current_imu = None
        drift_admission_plan = None
        if (
            commit_admission and admission is not None
            and admission.causal_transaction is not None
            and admission.prepared_result is not None
            and admission.prepared_result.accepted
        ):
            position_plan = admission.causal_transaction.root_plan
            future_imu = root.prepare_imu_after_position_transaction(
                position_plan, sample=frame.imu_sample,
                frame_digest=frame.digest,
            )
            drift_admission_plan = (
                self.consensus_drift.prepare_admission(admission)
                if consensus_admission is None else consensus_admission
            )
            self.consensus_drift.prevalidate(drift_admission_plan)
            drift_plan = self.consensus_drift.prepare_native200_after_admission_plan(
                drift_admission_plan, future_imu.imu_plan.candidate_state,
            )
            root_plan = future_imu
            if drift_plan.result.consumed_observation_digest is not None:
                root_plan = root.prepare_imu_velocity_after_position_transaction(
                    position_plan, future_imu,
                    velocity_delta_mps=drift_plan.result.velocity_delta_mps,
                    maximum_velocity_step_mps=(
                        self.consensus_drift.config.maximum_velocity_step_mps
                    ),
                    owner="C2_CONTINUOUS_CONSENSUS_DRIFT",
                )
        else:
            current_imu = root.prepare_imu_transaction(frame.imu_sample)
            drift_plan = self.consensus_drift.prepare_native200(
                current_imu.candidate_state,
            )
            root_plan = current_imu
            if drift_plan.result.consumed_observation_digest is not None:
                root_plan = root.prepare_imu_velocity_transaction(
                    current_imu,
                    velocity_delta_mps=drift_plan.result.velocity_delta_mps,
                    maximum_velocity_step_mps=(
                        self.consensus_drift.config.maximum_velocity_step_mps
                    ),
                    owner="C2_CONTINUOUS_CONSENSUS_DRIFT",
                )
        history_plan = self.history.prepare_native200_root_override(
            event, position_plan=position_plan, future_imu=future_imu,
            current_imu=current_imu, root_plan=root_plan,
        )
        return _PreparedCompositionNative200(
            history_plan, drift_plan, drift_admission_plan,
        )

    def commit_native200(self, prepared: object) -> None:
        if self.consensus_drift is None:
            self.history.commit_native200(prepared)
            return
        if type(prepared) is not _PreparedCompositionNative200:
            raise TypeError("enabled consensus drift requires compound native200 plan")
        if prepared.drift_admission_plan is not None and (
            prepared.drift_plan.dependency_digest
            != prepared.drift_admission_plan.digest
            or prepared.drift_plan._dependency_plan
            is not prepared.drift_admission_plan
        ):
            raise RuntimeError("native200 drift plan/admission dependency mismatch")
        self.history.commit_native200_root_override(prepared.history_plan)
        self.consensus_drift.commit(prepared.drift_plan)

    def prepare_native200_batch(
        self, events: tuple[ContinuousEvent, ...],
    ) -> object:
        return self.history.prepare_native200_batch(events)


    def commit_native200_batch(self, prepared: object) -> None:
        self.history.commit_native200_batch(prepared)

    def prepare_gap(self, event: ContinuousEvent) -> object:
        history = self.history.prepare_gap(event)
        if self.consensus_drift is None:
            return history
        return _PreparedCompositionGap(
            history, self.consensus_drift.prepare_gap(),
        )

    def commit_gap(self, prepared: object) -> None:
        if self.consensus_drift is None:
            self.history.commit_gap(prepared)
            return
        if type(prepared) is not _PreparedCompositionGap:
            raise TypeError("enabled consensus drift requires compound gap plan")
        self.history.commit_gap(prepared.history_plan)
        self.consensus_drift.commit(prepared.drift_plan)

    def prepare_gap_native200(
        self, gap: ContinuousEvent, endpoint: ContinuousEvent,
    ) -> object:
        history = self.history.prepare_gap_native200(gap, endpoint)
        drift = (
            None if self.consensus_drift is None
            else self.consensus_drift.prepare_gap()
        )
        return _PreparedCompositionGapNative200(history, drift)

    def commit_gap_native200(self, prepared: object) -> None:
        if type(prepared) is not _PreparedCompositionGapNative200:
            raise TypeError("gap endpoint requires compound composition plan")
        if self.consensus_drift is None:
            if prepared.drift_plan is not None:
                raise RuntimeError("disabled consensus drift received gap state")
        elif prepared.drift_plan is None:
            raise RuntimeError("enabled consensus drift lacks gap state")
        self.history.commit_gap_native200(prepared.history_plan)
        if prepared.drift_plan is not None:
            self.consensus_drift.commit(prepared.drift_plan)

    def native200_batch_boundary_free(self) -> bool:
        return self.consensus_drift is None or self.consensus_drift.pending_count == 0

    def prepare_consensus_admission(
        self, admission: PreparedArticulatedAdmission,
    ) -> PreparedContinuousConsensusDrift | None:
        if self.consensus_drift is None:
            return None
        return self.consensus_drift.prepare_admission(admission)

    def commit_consensus_admission(
        self, prepared: PreparedContinuousConsensusDrift,
    ) -> None:
        if self.consensus_drift is None:
            raise RuntimeError("consensus drift is disabled")
        self.consensus_drift.commit(prepared)

    def materialize_group(
        self, rows: tuple[UwbRow, ...], *, availability_global_ns: int,
        member_region_identities: tuple[str, ...], evidence_class: str,
    ) -> GroupEpochMaterialization:
        _epochs, measurement_ns, _frame_lower_ns = group_epoch_times_ns(
            rows, clocks=self.engine.static.clocks,
        )
        availability_time_s = canonical_group_availability_time_s(
            rows, clocks=self.engine.static.clocks,
            availability_global_ns=availability_global_ns,
        )
        measurement_time_s = measurement_ns * 1e-9
        sidecars = self.history.sidecars_for_group(
            rows, measurement_time_s=measurement_time_s,
            availability_time_s=availability_time_s,
            member_region_identities=member_region_identities,
            evidence_class=evidence_class,
        )
        if evidence_class == "DYNAMIC_ONLY" and sidecars.action_specific_prior_used:
            raise ValueError("dynamic-only sidecars used an action-specific prior")
        event = RootWorkerEvent(
            max(row.sequence for row in rows), availability_time_s, "UWB", rows,
            sidecars.dynamic_envelope, sidecars.activity, sidecars.consensus,
            sidecars.contact,
        )
        packet = BoundGroupPacket(
            self.engine.static.digest, event, sidecars.pose_links, (),
            sidecars.a_sigma_owner, sidecars.b_sigma_owner,
            sidecars.b_shadow_owner,
            availability_global_ns=availability_global_ns,
        )
        correction = self.engine.pose.transition_snapshot()["target_correction"]
        source = sidecars.native200_source_pair
        epoch = self.engine.epoch(
            measurement_time_s=measurement_time_s,
            availability_time_s=availability_time_s,
            previous_orientation_time_s=source.previous_global_ns * 1e-9,
            base_rotations_world=sidecars.base_rotations_world,
            previous_correction_rotvec=correction,
            point_constraints_world_m=sidecars.point_constraints_world_m,
            provenance=sidecars.provenance,
            native200_source_pair=source,
        )
        epoch_digest = self.engine._epoch_digest(epoch)
        audit_digest = _digest_payload({
            "schema": CONTINUOUS_GROUP_EPOCH_SCHEMA,
            "rows": [vars(row) for row in rows],
            "availability_global_ns": availability_global_ns,
            "member_regions": member_region_identities,
            "evidence_class": evidence_class,
            "static": self.engine.static.digest,
            "packet": packet.digest,
            "epoch": epoch_digest,
            "sidecars": sidecars.digest,
            "root_pre": self.engine.root.publication_token().digest,
            "pose_pre": self.engine.pose.publication_token().digest,
            "robust_revision": self.engine.robust.revision,
        })
        return GroupEpochMaterialization(
            packet, epoch, audit_digest, epoch_digest,
            sidecars.action_specific_prior_used,
        )

    def prepare_admission(
        self, packet: BoundGroupPacket, epoch: ArticulatedEpochOwner, *,
        allow_obsolete_native200_root_fallback: bool = False,
    ) -> PreparedArticulatedAdmission:
        return self.engine.prepare_admission(
            packet, epoch, received_at_committed_horizon=True,
            allow_obsolete_native200_root_fallback=(
                allow_obsolete_native200_root_fallback
            ),
        )

    def commit_admission(self, prepared: PreparedArticulatedAdmission) -> object:
        return self.engine.commit_admission(prepared)


class ContinuousGroupComposition(Protocol):
    """Adapter implemented by the existing authoritative state owners."""

    def clone(self) -> "ContinuousGroupComposition": ...
    def mutable_owner_tokens(self) -> frozenset[int]: ...
    def snapshot(self) -> object: ...
    def restore(self, snapshot: object) -> None: ...
    def prepare_native200(self, event: ContinuousEvent) -> object: ...
    def commit_native200(self, prepared: object) -> None: ...
    def prepare_native200_batch(self, events: tuple[ContinuousEvent, ...]) -> object: ...
    def commit_native200_batch(self, prepared: object) -> None: ...
    def prepare_gap(self, event: ContinuousEvent) -> object: ...
    def commit_gap(self, prepared: object) -> None: ...
    def prepare_gap_native200(
        self, gap: ContinuousEvent, endpoint: ContinuousEvent,
    ) -> object: ...
    def commit_gap_native200(self, prepared: object) -> None: ...
    def materialize_group(
        self, rows: tuple[UwbRow, ...], *, availability_global_ns: int,
        member_region_identities: tuple[str, ...], evidence_class: str,
    ) -> GroupEpochMaterialization: ...
    def prepare_admission(
        self, packet: BoundGroupPacket, epoch: ArticulatedEpochOwner, *,
        allow_obsolete_native200_root_fallback: bool = False,
    ) -> PreparedArticulatedAdmission: ...
    def commit_admission(self, prepared: PreparedArticulatedAdmission) -> object: ...
    def prepare_consensus_admission(
        self, prepared: PreparedArticulatedAdmission,
    ) -> PreparedContinuousConsensusDrift | None: ...
    def commit_consensus_admission(
        self, prepared: PreparedContinuousConsensusDrift,
    ) -> None: ...


@dataclass(frozen=True)
class ContinuousGroupAudit:
    bucket: int
    reason: str
    nodes: tuple[str, ...]
    member_regions: tuple[str, ...]
    evidence_class: str
    packet_digest: str | None = None
    epoch_digest: str | None = None
    candidate_digest: str | None = None
    stale_pose_link: StalePoseLinkDiagnostic | None = None
    obsolete_native200_source_pair: ObsoleteNative200SourcePairDiagnostic | None = None
    low_link_measurement: LowLinkMeasurementUsabilityRejection | None = None
    original_group_availability_ns: int | None = None
    effective_processing_availability_ns: int | None = None
    retry_delay_ns: int | None = None

    def __post_init__(self) -> None:
        values = (
            self.original_group_availability_ns,
            self.effective_processing_availability_ns,
            self.retry_delay_ns,
        )
        if any(value is not None for value in values):
            if (any(type(value) is not int for value in values)
                    or self.effective_processing_availability_ns
                    < self.original_group_availability_ns
                    or self.retry_delay_ns != (
                        self.effective_processing_availability_ns
                        - self.original_group_availability_ns
                    )):
                raise ValueError("invalid complete-group availability audit")


@dataclass(frozen=True)
class ContinuousAdmissionAudit:
    """Bounded immutable record of one authoritative group admission attempt."""

    bucket: int
    packet_digest: str
    epoch_digest: str
    candidate_digest: str
    source_sequence: int
    source_identity: tuple[object, ...] | None
    trusted_partition: tuple[str, ...]
    branch: str
    prepared_accepted: bool | None
    prepared_reason: str | None
    commit_intent: bool
    commit_attempted: bool
    commit_succeeded: bool
    outcome: str
    pre_pose_digest: str
    result_pose_digest: str
    diagnostic_digest: str | None
    diagnostic: object | None = None

    def __post_init__(self) -> None:
        if (
            type(self.bucket) is not int
            or any(len(value) != 64 for value in (
                self.packet_digest, self.epoch_digest, self.candidate_digest,
            ))
            or type(self.source_sequence) is not int
            or type(self.trusted_partition) is not tuple
            or self.branch not in {"A_BASELINE", "B_UWB"}
            or type(self.commit_intent) is not bool
            or type(self.commit_attempted) is not bool
            or type(self.commit_succeeded) is not bool
            or self.commit_succeeded and not self.commit_attempted
            or not self.outcome
            or any(
                type(value) is not str or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in (self.pre_pose_digest, self.result_pose_digest)
            )
            or (self.diagnostic_digest is not None
                and len(self.diagnostic_digest) != 64)
        ):
            raise ValueError("invalid continuous admission audit")


@dataclass(frozen=True)
class PreparedGroupDisposition:
    """Branch-local result for one shared raw group, before A/B commit."""

    provenance_digest: str | None
    reason: str
    admission: ContinuousAdmissionAudit | None

    def __post_init__(self) -> None:
        if (self.provenance_digest is not None
                and len(self.provenance_digest) != 64):
            raise ValueError("invalid group provenance digest")
        if not self.reason:
            raise ValueError("group disposition reason is required")


@dataclass(frozen=True)
class ContinuousGroupDiagnosticSnapshot:
    """Immutable diagnostic publication of facts already owned by this branch."""

    root_state: RootState
    publication_revision: int
    publication_time_s: float
    publication_digest: str
    counters: tuple[tuple[str, int], ...]
    journal: tuple[ContinuousGroupAudit, ...]
    direct_nodes: None = None
    propagated_nodes: None = None

    def __post_init__(self) -> None:
        vector = np.asarray(self.root_state.vector, float).copy()
        covariance = np.asarray(self.root_state.covariance, float).copy()
        vector.setflags(write=False); covariance.setflags(write=False)
        object.__setattr__(self, "root_state", RootState(
            self.root_state.time_s, vector, covariance,
        ))
        if (
            type(self.publication_revision) is not int
            or self.publication_revision < 0
            or len(self.publication_digest) != 64
            or self.direct_nodes is not None
            or self.propagated_nodes is not None
        ):
            raise ValueError("invalid continuous-group diagnostic snapshot")


@dataclass(frozen=True)
class _PendingRow:
    event: ContinuousEvent
    row: UwbRow
    valid_links: int
    commit_uwb: bool


@dataclass(frozen=True)
class _PreparedGroupToken:
    owner_key: object
    base_revision: int
    pending: Mapping[int, tuple[_PendingRow, ...]]
    finalized_watermark: int | None
    journal: tuple[ContinuousGroupAudit, ...]
    counters: tuple[tuple[str, int], ...]
    composition_snapshot: object | None
    record_capability: object | None
    native200_prepared: object | None
    gap_prepared: object | None
    gap_endpoint_native200: bool
    admission: PreparedArticulatedAdmission | None
    consensus_admission: PreparedContinuousConsensusDrift | None
    consensus_admission_embedded: bool
    commit_admission: bool
    admission_audit: ContinuousAdmissionAudit | None
    group_provenance_digest: str | None
    group_disposition_reason: str


@dataclass(frozen=True)
class _Native200RecordCapability:
    """One-shot owner token suppressing nested snapshots inside one IMU record."""

    owner_key: object
    ordinal: int
    raw_identity: tuple[int, int, int, str] | None
    state: "_Native200RecordCapabilityState"


@dataclass(frozen=True)
class _ContinuousRecordDelta:
    revision: int
    pending: Mapping[int, tuple[_PendingRow, ...]]
    finalized_watermark: int | None
    journal: tuple[ContinuousGroupAudit, ...]
    counters: tuple[tuple[str, int], ...]
    composition: object
    admission_journal: tuple[ContinuousAdmissionAudit, ...]


@dataclass
class _Native200RecordCapabilityState:
    delta: _ContinuousRecordDelta | None
    status: str = "ACTIVE"


@dataclass(frozen=True)
class _PreparedNative200GroupBatch:
    owner_key: object
    base_revision: int
    record_capability: object
    native200_prepared: object
    row_count: int


class ContinuousGroupEpochOwner:
    """Own at most three pending 120 ms sweep buckets, never estimator state."""

    def __init__(
        self, *, composition: ContinuousGroupComposition,
        static_nodes: tuple[str, ...], acquired_action_ids: frozenset[str],
    ) -> None:
        if len(static_nodes) != ROWS_PER_BUCKET or tuple(sorted(static_nodes)) != static_nodes:
            raise ValueError("group owner requires the exact sorted ten-node inventory")
        if len(set(static_nodes)) != ROWS_PER_BUCKET or not acquired_action_ids:
            raise ValueError("invalid continuous group static inventory")
        tokens = composition.mutable_owner_tokens()
        if type(tokens) is not frozenset or not tokens:
            raise ValueError("composition lacks explicit mutable ownership")
        self._composition = composition
        self._static_nodes = static_nodes
        self._acquired_action_ids = acquired_action_ids
        self._pending: dict[int, tuple[_PendingRow, ...]] = {}
        self._finalized_watermark: int | None = None
        self._journal: tuple[ContinuousGroupAudit, ...] = ()
        self._admission_journal: tuple[ContinuousAdmissionAudit, ...] = ()
        self._counters: dict[str, int] = {}
        self._revision = 0
        self.__owner_key = object()
        self.__record_coordinator_authority: object | None = None
        self.__record_capability: _Native200RecordCapability | None = None
        self.__record_capability_ordinal = 0

    def clone(self) -> "ContinuousGroupEpochOwner":
        clone = ContinuousGroupEpochOwner(
            composition=self._composition.clone(), static_nodes=self._static_nodes,
            acquired_action_ids=self._acquired_action_ids,
        )
        clone._pending = copy.deepcopy(self._pending)
        clone._finalized_watermark = self._finalized_watermark
        clone._journal = self._journal
        clone._admission_journal = self._admission_journal
        clone._counters = dict(self._counters)
        clone._revision = self._revision
        return clone

    @classmethod
    def from_consumed_bootstrap(
        cls, *, composition: ContinuousGroupComposition,
        static_nodes: tuple[str, ...], acquired_action_ids: frozenset[str],
        bootstrap_bucket: int, bootstrap_group_digest: str,
        bootstrap_region_identity: str = "00_initial_still",
    ) -> "ContinuousGroupEpochOwner":
        if (type(bootstrap_bucket) is not int or len(bootstrap_group_digest) != 64
                or not bootstrap_region_identity):
            raise ValueError("invalid consumed bootstrap identity")
        owner = cls(
            composition=composition, static_nodes=static_nodes,
            acquired_action_ids=acquired_action_ids,
        )
        owner._finalized_watermark = bootstrap_bucket
        owner._journal = (ContinuousGroupAudit(
            bootstrap_bucket, "BOOTSTRAP_GROUP_CONSUMED", static_nodes,
            (bootstrap_region_identity,) * len(static_nodes),
            ("ACTION_EVIDENCE" if bootstrap_region_identity in acquired_action_ids
             else "DYNAMIC_ONLY"),
            packet_digest=bootstrap_group_digest,
        ),)
        owner._counters = {"BOOTSTRAP_GROUP_CONSUMED": 1}
        owner._revision = 1
        return owner

    def mutable_owner_tokens(self) -> frozenset[int]:
        return frozenset((id(self), id(self._pending))) | self._composition.mutable_owner_tokens()

    def bind_native200_record_coordinator(self, authority: object) -> None:
        if authority is None:
            raise TypeError("native200 record coordinator authority is required")
        if self.__record_coordinator_authority is not None:
            raise RuntimeError("native200 record coordinator is already bound")
        self.__record_coordinator_authority = authority

    def _issue_native200_record_capability(
        self, *, authority: object,
        raw_identity: tuple[int, int, int, str] | None = None,
    ) -> object:
        if authority is not self.__record_coordinator_authority:
            raise RuntimeError("foreign native200 record coordinator authority")
        if self.__record_capability is not None:
            raise RuntimeError("nested continuous-group record transaction")
        self.__record_capability_ordinal += 1
        capability = _Native200RecordCapability(
            self.__owner_key, self.__record_capability_ordinal,
            raw_identity,
            _Native200RecordCapabilityState(_ContinuousRecordDelta(
                self._revision, MappingProxyType(dict(self._pending)),
                self._finalized_watermark, self._journal,
                tuple(sorted(self._counters.items())), self._composition.snapshot(),
                self._admission_journal,
            )),
        )
        self.__record_capability = capability
        return capability

    def _close_native200_record_capability(
        self, capability: object, *, authority: object,
    ) -> None:
        if (authority is not self.__record_coordinator_authority
                or type(capability) is not _Native200RecordCapability
                or capability.owner_key is not self.__owner_key
                or capability is not self.__record_capability
                or capability.state.status != "ACTIVE"
                or capability.state.delta is None):
            raise RuntimeError("stale or foreign native200 record capability")
        self.__record_capability = None
        capability.state.status = "CLOSED"

    def _finalize_native200_record_capability(
        self, capability: object, *, authority: object,
    ) -> None:
        self._validate_finalize_native200_record_capability(
            capability, authority=authority,
            raw_identity=getattr(capability, "raw_identity", None),
        )
        self._discard_native200_record_capability(capability)

    def _validate_finalize_native200_record_capability(
        self, capability: object, *, authority: object,
        raw_identity: tuple[int, int, int, str] | None,
    ) -> None:
        if (authority is not self.__record_coordinator_authority
                or type(capability) is not _Native200RecordCapability
                or capability.owner_key is not self.__owner_key
                or capability.raw_identity != raw_identity
                or capability.state.status != "CLOSED"
                or capability.state.delta is None):
            raise RuntimeError("stale or foreign native200 record capability")

    @staticmethod
    def _discard_native200_record_capability(capability: object) -> None:
        """Discard one prevalidated CLOSED delta; assignments cannot fail."""
        capability.state.delta = None
        capability.state.status = "FINALIZED"

    def _rollback_native200_record_capability(
        self, capability: object, *, authority: object,
    ) -> None:
        if (authority is not self.__record_coordinator_authority
                or type(capability) is not _Native200RecordCapability
                or capability.owner_key is not self.__owner_key
                or capability.state.status not in ("ACTIVE", "CLOSED")
                or capability.state.delta is None):
            raise RuntimeError("stale or foreign native200 record capability")
        delta = capability.state.delta
        self._composition.restore(delta.composition)
        self._revision = delta.revision
        self._pending = dict(delta.pending)
        self._finalized_watermark = delta.finalized_watermark
        self._journal = delta.journal
        self._counters = dict(delta.counters)
        self._admission_journal = delta.admission_journal
        if self.__record_capability is capability:
            self.__record_capability = None
        capability.state.delta = None
        capability.state.status = "ROLLED_BACK"

    def _validate_native200_record_capability(self, capability: object) -> None:
        if (type(capability) is not _Native200RecordCapability
                or capability.owner_key is not self.__owner_key
                or capability is not self.__record_capability
                or capability.state.status != "ACTIVE"
                or capability.state.delta is None):
            raise RuntimeError("stale or foreign native200 record capability")

    def native200_batch_boundary_free(self) -> bool:
        """Report whether an IMU run cannot trigger pending-UWB processing."""

        return (
            not self._pending
            and getattr(
                self._composition, "native200_batch_boundary_free",
                lambda: True,
            )()
        )

    def native200_record_batch_classification(
        self, events: tuple[ContinuousEvent, ...],
    ) -> str:
        """Classify one exact IMU run without mutating authoritative state."""

        if (type(events) is not tuple or not 1 <= len(events) <= 16
                or any(type(event) is not ContinuousEvent or event.kind != "IMU"
                       or type(event.payload_owner) is not OwnedNative200HistoryFrame
                       for event in events)):
            return "malformed_or_discontinuity"
        frames = tuple(event.payload_owner for event in events)
        first_raw = frames[0].raw_provenance
        raw_identity = (
            first_raw.record_index, first_raw.start_offset,
            first_raw.end_offset, first_raw.encoded_sha256,
        )
        first_sample_index = first_raw.sample_index
        if any(
            (
                frame.raw_provenance.record_index,
                frame.raw_provenance.start_offset,
                frame.raw_provenance.end_offset,
                frame.raw_provenance.encoded_sha256,
            ) != raw_identity
            or frame.raw_provenance.sample_index != first_sample_index + index
            for index, frame in enumerate(frames)
        ):
            return "malformed_or_discontinuity"
        if any(
            current.availability_global_ns < previous.availability_global_ns
            for previous, current in zip(events, events[1:])
        ):
            return "malformed_or_discontinuity"
        if any(
            event.node_id != frame.node
            or event.boot_epoch != frame.boot_epoch
            or event.common_global_ns != frame.source_global_ns
            or event.imu_timer2.timer2_base_us != frame.timer2_base_us
            or event.imu_timer2.trigger_timer2_us != frame.source_timer_us
            or event.clock_mapping_digest != frame.clock_mapping_digest
            or event.clock_owner_sha256 != frame.clock_owner_sha256
            or event.clock_source_sha256 != frame.clock_source_sha256
            or round(frame.imu_sample.availability_time_s * 1e9)
            != event.availability_global_ns
            for event, frame in zip(events, frames)
        ):
            return "malformed_or_discontinuity"
        if any(
            current.node != previous.node
            or current.boot_epoch != previous.boot_epoch
            or current.source_timer_us - previous.source_timer_us != 5_000
            or current.source_global_ns <= previous.source_global_ns
            or current.publication_revision != previous.publication_revision + 1
            for previous, current in zip(frames, frames[1:])
        ):
            return "malformed_or_discontinuity"
        if not getattr(
            self._composition, "native200_batch_boundary_free", lambda: True,
        )():
            # A queued consensus-drift observation is owned by the scalar
            # native200 transaction.  The batch history path cannot evaluate
            # or consume it, including when its true availability falls
            # inside this record.
            return "complete_pending"
        if any(len(rows) >= ROWS_PER_BUCKET for rows in self._pending.values()):
            return "complete_pending"
        final_availability_ns = events[-1].availability_global_ns
        if not all(
            final_availability_ns
            < (bucket + 1) * EPOCH_PERIOD_NS + ASSEMBLY_HORIZON_NS
            for bucket in self._pending
        ):
            return "deadline_or_after"
        return "batch_safe"

    def native200_record_batch_safe(
        self, events: tuple[ContinuousEvent, ...],
    ) -> bool:
        return self.native200_record_batch_classification(events) == "batch_safe"

    def prepare_native200_batch(
        self, events: tuple[ContinuousEvent, ...], *, _record_capability: object,
    ) -> _PreparedNative200GroupBatch:
        """Prepare a record-local IMU run proven not to cross a UWB boundary."""

        self._validate_native200_record_capability(_record_capability)
        if not self.native200_record_batch_safe(events):
            raise RuntimeError("NATIVE200_BATCH_CROSSES_PENDING_UWB_BOUNDARY")
        native = self._composition.prepare_native200_batch(events)
        return _PreparedNative200GroupBatch(
            self.__owner_key, self._revision, _record_capability,
            native, len(events),
        )

    def commit_native200_batch(self, prepared: object) -> None:
        if (
            type(prepared) is not _PreparedNative200GroupBatch
            or prepared.owner_key is not self.__owner_key
            or prepared.base_revision != self._revision
        ):
            raise RuntimeError("STALE_OR_FOREIGN_CONTINUOUS_GROUP_BATCH")
        self._validate_native200_record_capability(prepared.record_capability)
        self._composition.commit_native200_batch(prepared.native200_prepared)
        self._revision += prepared.row_count

    def continuous_snapshot(self) -> object:
        return (
            self._revision, copy.deepcopy(self._pending), self._finalized_watermark,
            self._journal, tuple(sorted(self._counters.items())), self._composition.snapshot(),
            self._admission_journal,
        )

    def restore_continuous_snapshot(self, snapshot: object) -> None:
        revision, pending, watermark, journal, counters, composition, admissions = snapshot
        self._composition.restore(composition)
        self._revision = revision
        self._pending = copy.deepcopy(pending)
        self._finalized_watermark = watermark
        self._journal = journal
        self._counters = dict(counters)
        self._admission_journal = admissions

    @property
    def journal(self) -> tuple[ContinuousGroupAudit, ...]:
        return self._journal

    @property
    def counters(self) -> Mapping[str, int]:
        return MappingProxyType(dict(self._counters))

    @property
    def admission_journal(self) -> tuple[ContinuousAdmissionAudit, ...]:
        return self._admission_journal

    def _publish_admission_audit(
        self, audit: ContinuousAdmissionAudit, *, attempted: bool,
        succeeded: bool, outcome: str,
    ) -> None:
        published = dataclass_replace(
            audit, commit_attempted=attempted, commit_succeeded=succeeded,
            outcome=outcome,
        )
        self._admission_journal = (
            self._admission_journal + (published,)
        )[-DIAGNOSTIC_RING_CAPACITY:]

    def record_aborted_admission(self, prepared: PreparedSubownerUpdate) -> None:
        """Close a prepared peer admission skipped by an A/B transaction failure."""
        token = prepared.token
        if (type(token) is not _PreparedGroupToken
                or token.owner_key is not self.__owner_key
                or token.base_revision != self._revision):
            raise RuntimeError("STALE_OR_FOREIGN_CONTINUOUS_GROUP_PLAN")
        if token.admission_audit is None:
            return
        self._publish_admission_audit(
            token.admission_audit, attempted=False, succeeded=False,
            outcome="AB_TRANSACTION_ABORTED_BEFORE_BRANCH_COMMIT",
        )

    def diagnostic_snapshot(self) -> ContinuousGroupDiagnosticSnapshot:
        """Publish root/counter facts without exposing composition rollback state."""
        root = self._composition.engine.root
        token = root.publication_token()
        state = root.current_state
        if token.revision < 0 or token.time_s != state.time_s:
            raise RuntimeError("root publication token/state mismatch")
        return ContinuousGroupDiagnosticSnapshot(
            state, token.revision, token.time_s, token.digest,
            tuple(sorted(self._counters.items())), self._journal,
        )

    def _freeze_pending(
        self, pending: Mapping[int, tuple[_PendingRow, ...]],
    ) -> Mapping[int, tuple[_PendingRow, ...]]:
        if len(pending) > MAX_PENDING_BUCKETS:
            raise OverflowError("continuous UWB pending-bucket capacity exceeded")
        if any(len(rows) > ROWS_PER_BUCKET for rows in pending.values()):
            raise OverflowError("continuous UWB row capacity exceeded")
        return MappingProxyType(dict(sorted(pending.items())))

    def prepare_continuous_event(
        self, event: ContinuousEvent, *, commit_uwb: bool,
        _record_capability: object | None = None,
        _gap_endpoint_event: ContinuousEvent | None = None,
    ) -> PreparedSubownerUpdate:
        if _gap_endpoint_event is not None and event.kind != "GAP":
            raise TypeError("gap endpoint may only accompany a GAP event")
        if _record_capability is not None:
            if commit_uwb or event.kind not in ("IMU", "GAP"):
                raise RuntimeError("native200 record capability requires IMU chronology")
            self._validate_native200_record_capability(_record_capability)
            base_snapshot = None
        else:
            base_snapshot = self._composition.snapshot()
        pending = (
            dict(self._pending) if _record_capability is not None
            else copy.deepcopy(self._pending)
        )
        watermark = self._finalized_watermark
        journal = list(self._journal)
        counters = dict(self._counters)
        native = None
        gap_prepared = None
        gap_endpoint_native200 = False
        admission = None
        consensus_admission = None
        consensus_admission_embedded = False
        should_commit = False
        admission_audit = None
        group_provenance_digest = None
        group_disposition_reason = "NO_COMPLETE_GROUP"

        attempted_bucket: int | None = None

        def prepare_complete(bucket: int, *, allow_partial: bool = False) -> None:
            nonlocal admission, should_commit, watermark, admission_audit
            nonlocal group_provenance_digest, group_disposition_reason
            nonlocal attempted_bucket
            if admission is not None or any(key < bucket for key in pending):
                return
            ordered = pending.get(bucket, ())
            if (not ordered or len(ordered) > ROWS_PER_BUCKET
                    or (not allow_partial and len(ordered) != ROWS_PER_BUCKET)):
                return
            nodes = tuple(item.row.node for item in ordered)
            if (nodes != tuple(sorted(nodes)) or len(set(nodes)) != len(nodes)
                    or not set(nodes) <= set(self._static_nodes)):
                raise ValueError("runtime group differs from static node inventory")
            attempted_bucket = bucket
            intents = {item.commit_uwb for item in ordered}
            if len(intents) != 1:
                raise ValueError("completed group mixes branch commit intent")
            group_commit_uwb = intents.pop()
            group_provenance_digest = _digest_payload({
                "schema": "C2_SHARED_RAW_GROUP_PROVENANCE_V1",
                "bucket": bucket,
                "events": [(
                    item.event.event_id, item.event.node_id,
                    item.event.boot_epoch, item.event.common_global_ns,
                    item.event.availability_global_ns, item.row.sequence,
                    item.row.strobe_us, item.row.frame_us,
                ) for item in ordered],
            })
            regions = tuple(_event_region_identity(item.event) for item in ordered)
            evidence_class = (
                "ACTION_EVIDENCE"
                if len(set(regions)) == 1 and regions[0] in self._acquired_action_ids
                else "DYNAMIC_ONLY"
            )
            original_group_availability_ns = max(
                item.event.availability_global_ns for item in ordered
            )
            effective_processing_availability_ns = max(
                original_group_availability_ns, event.availability_global_ns,
            )
            retry_delay_ns = (
                effective_processing_availability_ns
                - original_group_availability_ns
            )
            try:
                materialized = self._composition.materialize_group(
                    tuple(item.row for item in ordered),
                    availability_global_ns=effective_processing_availability_ns,
                    member_region_identities=regions,
                    evidence_class=evidence_class,
                )
            except StalePoseLinkUnavailable as error:
                reason = (
                    "STALE_POSE_LINK_REJECTED" if error.diagnostic.terminal
                    else "STALE_POSE_LINK_DEFERRED"
                )
                if error.diagnostic.terminal:
                    pending.pop(bucket)
                    watermark = bucket if watermark is None else max(watermark, bucket)
                audit = ContinuousGroupAudit(
                    bucket, reason, nodes, regions, evidence_class,
                    stale_pose_link=error.diagnostic,
                    original_group_availability_ns=original_group_availability_ns,
                    effective_processing_availability_ns=(
                        effective_processing_availability_ns
                    ),
                    retry_delay_ns=retry_delay_ns,
                )
                journal.append(audit)
                counters[reason] = counters.get(reason, 0) + 1
                group_disposition_reason = reason
                return
            if evidence_class == "DYNAMIC_ONLY" and materialized.action_specific_prior_used:
                raise ValueError("dynamic-only group cannot use an action-specific prior")
            try:
                delayed_multi_node = (
                    retry_delay_ns > 0
                    and 1 < len(ordered) <= ROWS_PER_BUCKET
                )
                if delayed_multi_node:
                    admission = self._composition.prepare_admission(
                        materialized.packet, materialized.epoch,
                        allow_obsolete_native200_root_fallback=True,
                    )
                else:
                    admission = self._composition.prepare_admission(
                        materialized.packet, materialized.epoch,
                    )
            except ObsoleteNative200SourcePair as error:
                diagnostic = error.diagnostic
                complete_group_assembly_obsolete = bool(
                    event.kind == "UWB"
                    and not allow_partial
                    and len(ordered) == ROWS_PER_BUCKET
                    and retry_delay_ns == 0
                    and diagnostic.requested_current_global_ns
                        < diagnostic.latest_current_global_ns
                    and diagnostic.latest_current_global_ns
                        <= original_group_availability_ns
                )
                if complete_group_assembly_obsolete:
                    admission = self._composition.prepare_admission(
                        materialized.packet, materialized.epoch,
                        allow_obsolete_native200_root_fallback=True,
                    )
                else:
                    pending.pop(bucket)
                    watermark = bucket if watermark is None else max(watermark, bucket)
                    audit = ContinuousGroupAudit(
                        bucket, "OBSOLETE_NATIVE200_SOURCE_PAIR", nodes,
                        regions, evidence_class, materialized.packet.digest,
                        materialized.epoch_digest,
                        obsolete_native200_source_pair=diagnostic,
                        original_group_availability_ns=original_group_availability_ns,
                        effective_processing_availability_ns=(
                            effective_processing_availability_ns
                        ),
                        retry_delay_ns=retry_delay_ns,
                    )
                    journal.append(audit)
                    counters[audit.reason] = counters.get(audit.reason, 0) + 1
                    group_disposition_reason = audit.reason
                    return
            should_commit = bool(
                group_commit_uwb and admission.causal_transaction is not None
            )
            prepared_result = admission.prepared_result
            rejection = (
                getattr(admission, "articulated_rejection_diagnostic", None)
                or getattr(admission, "robust_candidate_rejection_diagnostic", None)
                or getattr(
                    admission,
                    "obsolete_native200_source_pair_diagnostic",
                    None,
                )
            )
            source = getattr(materialized.epoch, "native200_source_pair", None)
            source_identity = None if source is None else (
                source.node, source.boot_epoch, source.previous_timer_us,
                source.current_timer_us, source.previous_global_ns,
                source.current_global_ns, source.mapping_digest,
            )
            admission_audit = ContinuousAdmissionAudit(
                bucket=bucket,
                packet_digest=materialized.packet.digest,
                epoch_digest=materialized.epoch_digest,
                candidate_digest=admission.public_candidate_digest,
                source_sequence=max(item.row.sequence for item in ordered),
                source_identity=source_identity,
                trusted_partition=tuple(getattr(admission, "trusted_partition", ())),
                branch="B_UWB" if group_commit_uwb else "A_BASELINE",
                prepared_accepted=(None if prepared_result is None
                                   else bool(prepared_result.accepted)),
                prepared_reason=(None if prepared_result is None
                                 else getattr(prepared_result, "reason", None)),
                commit_intent=bool(group_commit_uwb),
                commit_attempted=False,
                commit_succeeded=False,
                outcome="PREPARED",
                pre_pose_digest=admission.pose_digest,
                result_pose_digest=(
                    admission.pose_digest if prepared_result is None
                    else prepared_result.pose_token_digest
                ),
                diagnostic_digest=(None if rejection is None else
                                   _digest_payload(vars(rejection))),
                diagnostic=rejection,
            )
            group_disposition_reason = "PREPARED_ADMISSION"
            pending.pop(bucket)
            watermark = bucket if watermark is None else max(watermark, bucket)
            audit = ContinuousGroupAudit(
                bucket, "PREPARED_COMPLETE_GROUP", nodes, regions,
                evidence_class, materialized.packet.digest,
                materialized.epoch_digest, admission.public_candidate_digest,
                original_group_availability_ns=original_group_availability_ns,
                effective_processing_availability_ns=(
                    effective_processing_availability_ns
                ),
                retry_delay_ns=retry_delay_ns,
            )
            journal.append(audit)
            counters[audit.reason] = counters.get(audit.reason, 0) + 1

        expired = sorted(
            key for key in pending
            if event.availability_global_ns
            >= (key + 1) * EPOCH_PERIOD_NS + ASSEMBLY_HORIZON_NS
        )
        if expired and event.kind != "GAP":
            # One source callback owns at most one group transaction.  Prepare
            # the oldest usable partial epoch and retain the rest.
            prepare_complete(expired[0], allow_partial=True)

        if pending and attempted_bucket is None and event.kind != "GAP":
            prepare_complete(min(pending))

        if (
            admission is not None and should_commit
            and getattr(self._composition, "consensus_drift", None) is not None
        ):
            consensus_admission = self._composition.prepare_consensus_admission(
                admission,
            )

        if event.kind == "IMU":
            if (
                admission is not None and should_commit
                and getattr(self._composition, "consensus_drift", None) is not None
            ):
                native = self._composition.prepare_native200(
                    event, admission=admission, commit_admission=True,
                    consensus_admission=consensus_admission,
                )
                consensus_admission_embedded = True
            else:
                native = self._composition.prepare_native200(event)
            result = SubownerResult(False, True)
        elif event.kind == "GAP":
            if _gap_endpoint_event is None:
                gap_prepared = self._composition.prepare_gap(event)
            else:
                gap_prepared = self._composition.prepare_gap_native200(
                    event, _gap_endpoint_event,
                )
                gap_endpoint_native200 = True
            for bucket, rows in sorted(pending.items()):
                audit = ContinuousGroupAudit(
                    bucket, "INCOMPLETE_GROUP_SUPPRESSED_AT_GAP",
                    tuple(item.row.node for item in rows),
                    tuple(_event_region_identity(item.event) for item in rows),
                    "DYNAMIC_ONLY",
                )
                journal.append(audit)
                counters[audit.reason] = counters.get(audit.reason, 0) + 1
            pending.clear()
            gap_watermark = canonical_epoch_bucket(event.gap_start_global_ns)
            watermark = (
                gap_watermark if watermark is None
                else max(watermark, gap_watermark)
            )
            result = SubownerResult(False, True, gap_covariance_grown=True)
        else:
            row = _row_from_event(event)
            bucket = canonical_epoch_bucket(event.common_global_ns)
            valid_links = _valid_link_count(row)
            if valid_links < 4:
                audit = ContinuousGroupAudit(
                    bucket, "LOW_LINK_MEASUREMENT_REJECTED", (row.node,),
                    (_event_region_identity(event),), "DYNAMIC_ONLY",
                    low_link_measurement=_low_link_rejection(
                        event, row, valid_links,
                    ),
                )
                journal.append(audit)
                counters[audit.reason] = counters.get(audit.reason, 0) + 1
            elif watermark is not None and bucket <= watermark:
                audit = ContinuousGroupAudit(
                    bucket, "LATE_SEALED_BUCKET_ROW", (row.node,),
                    (_event_region_identity(event),), "DYNAMIC_ONLY",
                )
                journal.append(audit); counters[audit.reason] = counters.get(audit.reason, 0) + 1
            else:
                rows = {item.row.node: item for item in pending.get(bucket, ())}
                candidate = _PendingRow(
                    event, row, valid_links, bool(commit_uwb),
                )
                previous = rows.get(row.node)
                if previous is None or (
                    candidate.valid_links > previous.valid_links
                    or (
                        candidate.valid_links == previous.valid_links
                        and candidate.row.strobe_us < previous.row.strobe_us
                    )
                ):
                    rows[row.node] = candidate
                ordered = tuple(rows[node] for node in sorted(rows))
                pending[bucket] = ordered
                if len(ordered) == ROWS_PER_BUCKET:
                    prepare_complete(bucket)
            if (
                admission is not None and should_commit
                and consensus_admission is None
                and getattr(self._composition, "consensus_drift", None) is not None
            ):
                consensus_admission = self._composition.prepare_consensus_admission(
                    admission,
                )
            self._freeze_pending(pending)
            result = SubownerResult(False, should_commit)

        journal = journal[-DIAGNOSTIC_RING_CAPACITY:]
        token = _PreparedGroupToken(
            self.__owner_key, self._revision, self._freeze_pending(pending),
            watermark, tuple(journal), tuple(sorted(counters.items())),
            base_snapshot, _record_capability,
            native, gap_prepared, gap_endpoint_native200, admission,
            consensus_admission, consensus_admission_embedded,
            should_commit, admission_audit, group_provenance_digest,
            group_disposition_reason,
        )
        return PreparedSubownerUpdate(result, token)

    def prepared_group_disposition(
        self, prepared: PreparedSubownerUpdate,
    ) -> PreparedGroupDisposition:
        token = prepared.token
        if (type(token) is not _PreparedGroupToken
                or token.owner_key is not self.__owner_key
                or token.base_revision != self._revision):
            raise RuntimeError("STALE_OR_FOREIGN_CONTINUOUS_GROUP_PLAN")
        return PreparedGroupDisposition(
            token.group_provenance_digest, token.group_disposition_reason,
            token.admission_audit,
        )

    def commit_continuous_event(self, prepared: PreparedSubownerUpdate) -> None:
        token = prepared.token
        if (
            type(token) is not _PreparedGroupToken
            or token.owner_key is not self.__owner_key
            or token.base_revision != self._revision
        ):
            raise RuntimeError("STALE_OR_FOREIGN_CONTINUOUS_GROUP_PLAN")
        if token.record_capability is not None:
            self._validate_native200_record_capability(token.record_capability)
        try:
            if token.admission is not None and token.commit_admission:
                self._composition.commit_admission(token.admission)
            if (
                token.consensus_admission is not None
                and not token.consensus_admission_embedded
            ):
                self._composition.commit_consensus_admission(
                    token.consensus_admission,
                )
            if token.gap_prepared is not None:
                if token.gap_endpoint_native200:
                    self._composition.commit_gap_native200(token.gap_prepared)
                else:
                    self._composition.commit_gap(token.gap_prepared)
            if token.native200_prepared is not None:
                self._composition.commit_native200(token.native200_prepared)
        except BaseException as error:
            if token.record_capability is None:
                self._composition.restore(token.composition_snapshot)
            if token.admission_audit is not None:
                self._publish_admission_audit(
                    token.admission_audit,
                    attempted=bool(token.commit_admission), succeeded=False,
                    outcome=f"EVENT_COMMIT_FAILED_ROLLED_BACK:{type(error).__name__}",
                )
            raise
        self._pending = dict(token.pending)
        self._finalized_watermark = token.finalized_watermark
        self._journal = token.journal
        self._counters = dict(token.counters)
        self._revision += 2 if token.gap_endpoint_native200 else 1
        if token.admission_audit is not None:
            if token.commit_admission:
                attempted, succeeded, outcome = True, True, "UWB_COMMIT_SUCCEEDED"
            elif token.admission_audit.commit_intent:
                attempted, succeeded, outcome = False, False, "PREPARED_REJECTED_NO_COMMIT"
            else:
                attempted, succeeded, outcome = False, False, "BASELINE_NO_UWB_COMMIT"
            self._publish_admission_audit(
                token.admission_audit, attempted=attempted,
                succeeded=succeeded, outcome=outcome,
            )
