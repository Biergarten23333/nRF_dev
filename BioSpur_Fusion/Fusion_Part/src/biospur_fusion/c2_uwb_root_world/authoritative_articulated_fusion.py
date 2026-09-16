"""Causal no-raw composition of existing robust-root and articulated owners.

This module owns sequencing only. The range model, frozen FK display proxy,
hinge projector/ROM, root filter, robust weights, and historical footholds
remain owned by their existing modules. The geometry is diagnostic, not
subject-specific anatomical metrology.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from functools import partial
import hashlib
import hmac
import json
import math
from types import MappingProxyType
from typing import Mapping

import numpy as np

from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
    project_hinge_corrections,
)
from biospur_fusion.c2_articulated_biomechanics.hinge_temporal import (
    ObsoleteNative200SourcePair,
    ObsoleteNative200SourcePairDiagnostic,
)
from biospur_fusion.c2_uwb_calibration.articulated_range import (
    DEFAULT_POINT_CONSTRAINT_SIGMA_M,
    NODE_KINEMATIC_PATHS,
    POINT_CONSTRAINT_GATE_SIGMA,
    SEGMENTS,
    ArticulatedRangeConfig,
    active_segments_for_nodes,
    corrected_proxy_points,
    solve_articulated_ranges,
)
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import (
    CausalArticulatedPose,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.c2_uwb_calibration.adaptive_nodes import (
    adaptive_root_minimum_std_m,
)
from biospur_fusion.root_r3.models import PositionObservation

from .causal_update_guard import CandidateKind
from .causal_update_transaction import (
    PreparedCausalSidecarTicket,
    PreparedCausalUpdateTransaction,
    TransactionResult,
    commit_causal_update_transaction,
    prepare_causal_update_transaction,
)
from .owner_bound_async_worker import (
    BoundGroupPacket,
    CausalRobustSharedRootOwner,
    RobustCandidateMeasurementRejection,
    _prepare_dynamic_owner,
)


NATIVE200_PERIOD_S = 0.005
ANATOMICAL_STATUS = (
    "DIAGNOSTIC_DISPLAY_PROXY_AND_POPULATION_ROM_NOT_SUBJECT_SPECIFIC_ANATOMY"
)
JOINT_COVARIANCE_STATUS = "UNAVAILABLE_NOT_PROPAGATED"
_POINT_TO_NODE = {point: node for node, point in NODE_TO_PROXY_POINT.items()}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _digest_payload(value: object) -> str:
    def convert(item):
        if isinstance(item, Mapping):
            return {str(key): convert(item[key]) for key in sorted(item)}
        if isinstance(item, np.ndarray):
            return {
                "dtype": item.dtype.str, "shape": item.shape,
                "bytes": item.tobytes().hex(),
            }
        if isinstance(item, (np.integer, np.floating)):
            return item.item()
        if isinstance(item, tuple):
            return [convert(value) for value in item]
        return item
    return hashlib.sha256(_canonical(convert(value))).hexdigest()


def _frozen_vectors(values, keys, shape) -> Mapping[str, np.ndarray]:
    if set(values) != set(keys):
        raise ValueError("articulated epoch inventory mismatch")
    result = {}
    for key in keys:
        value = np.asarray(values[key], dtype=float).reshape(shape).copy()
        if not np.isfinite(value).all():
            raise ValueError("articulated epoch contains non-finite values")
        value.setflags(write=False)
        result[key] = value
    return MappingProxyType(result)


def _canonical_orientation_ns(value: object, *, name: str) -> int:
    """Return one owned integer nanosecond tick without accepting coercions."""

    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an exact integer nanosecond tick")
    tick = int(value)
    if tick != value:
        raise ValueError(f"{name} is not canonical")
    return tick


def _orientation_ns_from_seconds(value: object, *, name: str) -> int:
    """Legacy adapter: canonicalize one finite timestamp independently."""

    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        raise ValueError(f"{name} must be finite seconds")
    seconds = float(value)
    if not math.isfinite(seconds):
        raise ValueError(f"{name} must be finite seconds")
    scaled = seconds * 1_000_000_000.0
    if not math.isfinite(scaled):
        raise ValueError(f"{name} is outside integer-nanosecond range")
    return int(round(scaled))


def _native200_mapping_digest(
    *, node: str, boot_epoch: int, a_ns_per_us: float, b_ns: float,
    clock_owner_sha256: str,
) -> str:
    return hashlib.sha256(_canonical({
        "node": node,
        "boot_epoch": boot_epoch,
        "a_ns_per_us": a_ns_per_us,
        "b_ns": b_ns,
        "clock_owner_sha256": clock_owner_sha256,
    })).hexdigest()


@dataclass(frozen=True)
class Native200ClockMappingOwner:
    """Single owner of pelvis TIMER2 -> common-global integer nanoseconds."""

    node: str
    clock_domain: str
    boot_epoch: int
    a_ns_per_us: float
    b_ns: float
    clock_owner_sha256: str
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            not self.node
            or self.clock_domain != "B306_TIMER2"
            or isinstance(self.boot_epoch, bool)
            or not isinstance(self.boot_epoch, (int, np.integer))
            or not math.isfinite(float(self.a_ns_per_us))
            or float(self.a_ns_per_us) <= 0.0
            or not math.isfinite(float(self.b_ns))
            or len(self.clock_owner_sha256) != 64
        ):
            raise ValueError("invalid native200 clock mapping owner")
        expected = _native200_mapping_digest(
            node=self.node, boot_epoch=int(self.boot_epoch),
            a_ns_per_us=float(self.a_ns_per_us), b_ns=float(self.b_ns),
            clock_owner_sha256=self.clock_owner_sha256,
        )
        if self.digest and not hmac.compare_digest(self.digest, expected):
            raise ValueError("native200 clock mapping digest mismatch")
        object.__setattr__(self, "boot_epoch", int(self.boot_epoch))
        object.__setattr__(self, "a_ns_per_us", float(self.a_ns_per_us))
        object.__setattr__(self, "b_ns", float(self.b_ns))
        object.__setattr__(self, "digest", expected)

    def global_ns(self, timer_us: object) -> int:
        tick = _canonical_orientation_ns(timer_us, name="source_timer_us")
        return int(round(self.a_ns_per_us * tick + self.b_ns))


@dataclass(frozen=True)
class Native200SourcePair:
    """Two actual consecutive pelvis TIMER2 samples and their clock owner."""

    node: str
    boot_epoch: int
    previous_timer_us: int
    current_timer_us: int
    previous_global_ns: int
    current_global_ns: int
    clock_mapping_owner: Native200ClockMappingOwner

    def __post_init__(self) -> None:
        integers = (
            self.boot_epoch, self.previous_timer_us, self.current_timer_us,
            self.previous_global_ns, self.current_global_ns,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, (int, np.integer))
            for value in integers
        ):
            raise ValueError("native200 source ownership requires exact integers")
        if (
            not self.node
            or self.current_timer_us - self.previous_timer_us != 5_000
            or self.current_global_ns <= self.previous_global_ns
            or type(self.clock_mapping_owner) is not Native200ClockMappingOwner
            or self.node != self.clock_mapping_owner.node
            or self.boot_epoch != self.clock_mapping_owner.boot_epoch
            or self.previous_global_ns
            != self.clock_mapping_owner.global_ns(self.previous_timer_us)
            or self.current_global_ns
            != self.clock_mapping_owner.global_ns(self.current_timer_us)
        ):
            raise ValueError("native200 source pair is not consecutive/owned")

    @property
    def clock_owner_sha256(self) -> str:
        return self.clock_mapping_owner.clock_owner_sha256

    @property
    def mapping_digest(self) -> str:
        return self.clock_mapping_owner.digest


@dataclass(frozen=True)
class Native200ExactBasePose:
    """One immutable exact-tick base pose bound to its source and trajectory owner."""

    node: str
    boot_epoch: int
    source_timer_us: int
    source_global_ns: int
    clock_mapping_digest: str
    base_pose_owner_digest: str
    base_rotations_world: Mapping[str, np.ndarray]

    def __post_init__(self) -> None:
        if (
            not self.node
            or isinstance(self.boot_epoch, bool)
            or not isinstance(self.boot_epoch, (int, np.integer))
            or isinstance(self.source_timer_us, bool)
            or not isinstance(self.source_timer_us, (int, np.integer))
            or isinstance(self.source_global_ns, bool)
            or not isinstance(self.source_global_ns, (int, np.integer))
            or len(self.clock_mapping_digest) != 64
            or len(self.base_pose_owner_digest) != 64
        ):
            raise ValueError("native200 exact base pose identity is invalid")
        object.__setattr__(self, "boot_epoch", int(self.boot_epoch))
        object.__setattr__(self, "source_timer_us", int(self.source_timer_us))
        object.__setattr__(self, "source_global_ns", int(self.source_global_ns))
        object.__setattr__(
            self, "base_rotations_world",
            _frozen_vectors(self.base_rotations_world, SEGMENTS, (3, 3)),
        )


@dataclass(frozen=True)
class ArticulatedEpochOwner:
    measurement_time_s: float
    availability_time_s: float
    orientation_time_s: float
    previous_orientation_time_s: float
    native_period_s: float
    base_rotations_world: Mapping[str, np.ndarray]
    previous_correction_rotvec: Mapping[str, np.ndarray]
    point_constraints_world_m: Mapping[str, np.ndarray]
    pose_token_digest: str
    geometry_digest: str
    model_digest: str
    provenance: str
    orientation_time_ns: int | None = None
    previous_orientation_time_ns: int | None = None
    native200_source_pair: Native200SourcePair | None = None

    def __post_init__(self) -> None:
        claimed_orientation_ns = _orientation_ns_from_seconds(
            self.orientation_time_s, name="orientation_time_s"
        )
        claimed_previous_ns = _orientation_ns_from_seconds(
            self.previous_orientation_time_s,
            name="previous_orientation_time_s",
        )
        orientation_ns = (
            claimed_orientation_ns
            if self.orientation_time_ns is None
            else _canonical_orientation_ns(
                self.orientation_time_ns, name="orientation_time_ns"
            )
        )
        previous_ns = (
            claimed_previous_ns
            if self.previous_orientation_time_ns is None
            else _canonical_orientation_ns(
                self.previous_orientation_time_ns,
                name="previous_orientation_time_ns",
            )
        )
        measurement_ns = _orientation_ns_from_seconds(
            self.measurement_time_s, name="measurement_time_s"
        )
        if self.native200_source_pair is None:
            cadence_valid = orientation_ns - previous_ns == 5_000_000
            measurement_binding_valid = measurement_ns == orientation_ns
        else:
            source = self.native200_source_pair
            cadence_valid = (
                orientation_ns == source.current_global_ns
                and previous_ns == source.previous_global_ns
                and source.current_timer_us - source.previous_timer_us == 5_000
            )
            source_age_ns = measurement_ns - source.current_global_ns
            measurement_binding_valid = 0 < source_age_ns <= 5_005_000
        timing_valid = (
            math.isfinite(self.measurement_time_s)
            and math.isfinite(self.availability_time_s)
            and self.measurement_time_s <= self.availability_time_s
            and claimed_orientation_ns == orientation_ns
            and claimed_previous_ns == previous_ns
            and measurement_binding_valid
            and cadence_valid
            and self.native_period_s == NATIVE200_PERIOD_S
        )
        digests_valid = (
            bool(self.provenance)
            and len(self.pose_token_digest) == 64
            and len(self.geometry_digest) == 64
            and len(self.model_digest) == 64
        )
        if not timing_valid or not digests_valid:
            raise ValueError("articulated epoch timing/ownership invalid")
        object.__setattr__(self, "orientation_time_ns", orientation_ns)
        object.__setattr__(self, "previous_orientation_time_ns", previous_ns)
        # Seconds are presentation/legacy values derived from the owned ticks.
        object.__setattr__(self, "orientation_time_s", orientation_ns * 1e-9)
        object.__setattr__(self, "previous_orientation_time_s", previous_ns * 1e-9)
        object.__setattr__(
            self, "base_rotations_world",
            _frozen_vectors(self.base_rotations_world, SEGMENTS, (3, 3)),
        )
        object.__setattr__(
            self, "previous_correction_rotvec",
            _frozen_vectors(self.previous_correction_rotvec, SEGMENTS, (3,)),
        )
        if set(self.point_constraints_world_m) - {"ankle_left", "ankle_right"}:
            raise ValueError("only existing ankle foothold constraints are supported")
        points = {}
        for key, source in self.point_constraints_world_m.items():
            value = np.asarray(source, dtype=float).reshape(3).copy()
            if not np.isfinite(value).all():
                raise ValueError("invalid foothold snapshot")
            value.setflags(write=False)
            points[key] = value
        object.__setattr__(self, "point_constraints_world_m", MappingProxyType(points))


@dataclass(frozen=True)
class ArticulatedRangeRejectionDiagnostic:
    packet_digest: str
    epoch_digest: str
    source_sequence: int
    measurement_time_ns: int
    availability_global_ns: int
    native200_source_identity: tuple[object, ...] | None
    trusted_partition: tuple[str, ...]
    solver_reason: str
    projection_acceptance_owner: str | None
    projection_acceptance_branch: str | None
    projection_applied: bool | None
    point_constraints_present: bool | None
    point_constraint_count: int | None
    point_constraint_identity: tuple[str, ...] | None
    optimizer_success: bool | None
    optimizer_status: int | None
    optimizer_message_class: str | None
    optimizer_message: str | None
    optimizer_nfev: int | None
    optimizer_cost: float | None
    prefit_range_median_abs_m: float | None
    raw_optimized_range_median_abs_m: float | None
    projected_range_median_abs_m: float | None
    range_projection_gate: bool | None
    prefit_full_residual_objective: float | None
    raw_optimized_full_residual_objective: float | None
    projected_full_residual_objective: float | None
    full_residual_objective_tolerance: float | None
    joint_projection_gate: bool | None
    projection_acceptance_tolerance: float | None
    numerical_geometry_success: bool | None
    optimizer_root_finite: bool | None
    rank_gate: bool | None
    condition_finite: bool | None
    condition_gate: bool | None
    correction_gate: bool | None
    correction_tolerance_rad: float | None
    physical_residual_finite: bool | None
    rank: int | None
    condition: float | str | None
    maximum_correction_rad: float | None
    projection_inside_rom: bool | None
    rom_gate_passed: bool | None
    fk_gate_passed: bool | None
    projection_acceptance_gate: bool | None
    foothold_projection_gate: bool | None
    contact_gate_passed: bool | None

    def __post_init__(self) -> None:
        if (
            len(self.packet_digest) != 64 or len(self.epoch_digest) != 64
            or type(self.source_sequence) is not int
            or type(self.measurement_time_ns) is not int
            or type(self.availability_global_ns) is not int
            or not self.solver_reason
            or type(self.trusted_partition) is not tuple
        ):
            raise ValueError("invalid articulated rejection diagnostic")


@dataclass(frozen=True)
class AuthoritativeArticulatedResult:
    accepted: bool
    reason: str
    transaction: TransactionResult | None
    sequence: int
    trusted_nodes: tuple[str, ...]
    direct_nodes: tuple[str, ...]
    propagated_nodes: tuple[str, ...]
    root_position_m: np.ndarray
    node_position_m: Mapping[str, np.ndarray]
    segment_correction_rotvec: Mapping[str, np.ndarray]
    pose_token_digest: str
    root_covariance_m2: np.ndarray
    maximum_foothold_residual_m: float
    native_period_s: float
    joint_covariance_status: str
    root_joint_cross_covariance_status: str
    anatomical_status: str
    articulated_rejection_diagnostic: ArticulatedRangeRejectionDiagnostic | None = None
    robust_candidate_rejection_diagnostic: RobustCandidateMeasurementRejection | None = None
    obsolete_native200_source_pair_diagnostic: ObsoleteNative200SourcePairDiagnostic | None = None


@dataclass
class _AdmissionCommitState:
    consumed: bool = False


_PREPARED_ADMISSION_KEY = object()


@dataclass(frozen=True)
class PreparedArticulatedAdmission:
    """Immutable, owner-bound candidate prepared from one engine pre-state."""

    static_digest: str
    packet_digest: str
    epoch_digest: str
    root_revision: int
    root_digest: str
    pose_revision: int
    pose_digest: str
    robust_revision: int
    robust_digest: str
    trusted_partition: tuple[str, ...]
    root_candidate_m: np.ndarray
    correction_candidate: Mapping[str, np.ndarray]
    root_observation: PositionObservation | None
    causal_transaction: PreparedCausalUpdateTransaction | None
    sidecar_ticket: PreparedCausalSidecarTicket | None
    prepared_result: AuthoritativeArticulatedResult | None
    public_candidate_digest: str
    packet: BoundGroupPacket
    epoch: ArticulatedEpochOwner
    maximum_foothold_residual_m: float
    root_only: bool
    engine_key: object
    commit_state: _AdmissionCommitState
    key: object = _PREPARED_ADMISSION_KEY
    articulated_rejection_diagnostic: ArticulatedRangeRejectionDiagnostic | None = None
    robust_candidate_rejection_diagnostic: RobustCandidateMeasurementRejection | None = None
    obsolete_native200_source_pair_diagnostic: ObsoleteNative200SourcePairDiagnostic | None = None


def _validate_robust_sidecar(owner, ticket, base_token) -> None:
    if owner.revision != base_token:
        raise RuntimeError("STALE_ROBUST_SHARED_ROOT_PLAN")


def _apply_robust_sidecar(owner, ticket) -> None:
    owner._apply_prevalidated_commit(ticket)


def _rollback_robust_sidecar(owner, ticket) -> None:
    owner._rollback_prevalidated_commit(ticket)


def _readonly_position_observation(
    observation: PositionObservation,
) -> PositionObservation:
    position = np.asarray(observation.root_position_m, dtype=float).copy()
    covariance = np.asarray(observation.covariance_m2, dtype=float).copy()
    position.setflags(write=False); covariance.setflags(write=False)
    frozen = replace(
        observation, root_position_m=position, covariance_m2=covariance,
        anchors=tuple(observation.anchors),
    )
    frozen.validate()
    return frozen


def _prepared_admission_candidate_digest(prepared: PreparedArticulatedAdmission) -> str:
    return _digest_payload({
        "schema":"C2_PREPARED_ARTICULATED_ADMISSION_V1",
        "static":prepared.static_digest,"packet":prepared.packet_digest,
        "epoch":prepared.epoch_digest,"root_revision":prepared.root_revision,
        "root_digest":prepared.root_digest,"pose_revision":prepared.pose_revision,
        "pose_digest":prepared.pose_digest,"robust_revision":prepared.robust_revision,
        "robust_digest":prepared.robust_digest,"trusted":prepared.trusted_partition,
        "root_candidate_m":prepared.root_candidate_m,
        "correction":prepared.correction_candidate,
        "root_observation":None if prepared.root_observation is None else
        vars(prepared.root_observation),
        "root_only":prepared.root_only,
        "maximum_contact":prepared.maximum_foothold_residual_m,
        "committable":prepared.causal_transaction is not None,
        "reason":None if prepared.prepared_result is None else prepared.prepared_result.reason,
        "articulated_rejection_diagnostic":None
        if prepared.articulated_rejection_diagnostic is None else
        vars(prepared.articulated_rejection_diagnostic),
        "robust_candidate_rejection_diagnostic":None
        if prepared.robust_candidate_rejection_diagnostic is None else
        vars(prepared.robust_candidate_rejection_diagnostic),
        "obsolete_native200_source_pair_diagnostic":None
        if prepared.obsolete_native200_source_pair_diagnostic is None else
        vars(prepared.obsolete_native200_source_pair_diagnostic),
    })


class AuthoritativeArticulatedFusion:
    """Publish root, pose, and robust bias as one prevalidated transaction."""

    def __init__(self, *, static_owner, pose: CausalArticulatedPose,
                 config: ArticulatedRangeConfig = ArticulatedRangeConfig(),
                 native200_clock_owner_sha256: str | None = None,
                 native200_base_pose_owner_digest: str | None = None) -> None:
        static_owner.validate_integrity()
        config.validate()
        self.static = static_owner
        self.root = static_owner.make_root()
        self.pose = pose
        self.robust = CausalRobustSharedRootOwner(static_owner)
        self.__admission_key = object()
        self.config = config
        if (
            native200_clock_owner_sha256 is not None
            and len(native200_clock_owner_sha256) != 64
        ):
            raise ValueError("native200 clock owner SHA is invalid")
        self.native200_clock_owner_sha256 = native200_clock_owner_sha256
        if (
            native200_base_pose_owner_digest is not None
            and len(native200_base_pose_owner_digest) != 64
        ):
            raise ValueError("native200 base-pose owner digest is invalid")
        self.native200_base_pose_owner_digest = native200_base_pose_owner_digest
        projector = pose.hinge_projector
        function = projector.func if isinstance(projector, partial) else projector
        if function is not project_hinge_corrections:
            raise ValueError("existing public orientation hinge projector required")
        model = projector.keywords.get("model") if isinstance(projector, partial) else None
        if not isinstance(model, Mapping) or not model:
            raise ValueError("existing hinge model owner required")
        geometry_payload = {
            "torso_height_m": pose.geometry.torso_height_m,
            "hip_span_m": pose.geometry.hip_span_m,
            "shoulder_span_m": pose.geometry.shoulder_span_m,
            "segment_length_m": dict(pose.geometry.segment_length_m),
            "scope": pose.geometry.scope,
            "physical_joint_centre_geometry": pose.geometry.physical_joint_centre_geometry,
            "uwb_antenna_prediction_geometry": pose.geometry.uwb_antenna_prediction_geometry,
        }
        self.geometry_digest = hashlib.sha256(_canonical(geometry_payload)).hexdigest()
        self.model_digest = hashlib.sha256(_canonical(
            {key: asdict(value) for key, value in sorted(model.items())}
        )).hexdigest()

    def add_imu(self, sample) -> bool:
        return self.root.add_imu(sample)

    def exact_native200_base_pose(
        self, *, native200_source_pair, role, base_rotations_world,
        base_pose_owner_digest,
    ) -> Native200ExactBasePose:
        if type(native200_source_pair) is not Native200SourcePair:
            raise ValueError("native200 source pair owner required")
        self._validate_native200_source_pair(native200_source_pair)
        if role not in {"previous", "current"}:
            raise ValueError("native200 exact base role is invalid")
        if (
            self.native200_base_pose_owner_digest is None
            or not hmac.compare_digest(
                str(base_pose_owner_digest), self.native200_base_pose_owner_digest
            )
        ):
            raise ValueError("native200 base-pose owner mismatch")
        source = native200_source_pair
        timer_us = (
            source.previous_timer_us if role == "previous" else source.current_timer_us
        )
        global_ns = (
            source.previous_global_ns if role == "previous" else source.current_global_ns
        )
        return Native200ExactBasePose(
            source.node, source.boot_epoch, timer_us, global_ns,
            source.mapping_digest, str(base_pose_owner_digest),
            base_rotations_world,
        )

    def sample_native200_pose(
        self, *, time_s, native200_source_pair,
        previous_base_pose, current_base_pose,
    ):
        """Publish one real pose sample with its authenticated TIMER2 owner."""
        if type(native200_source_pair) is not Native200SourcePair:
            raise ValueError("native200 source pair owner required")
        self._validate_native200_source_pair(native200_source_pair)
        source = native200_source_pair
        if (
            type(previous_base_pose) is not Native200ExactBasePose
            or type(current_base_pose) is not Native200ExactBasePose
        ):
            raise ValueError("complete native200 exact base pair required")
        for role, base_pose, timer_us, base_global_ns in (
            ("previous", previous_base_pose, source.previous_timer_us,
             source.previous_global_ns),
            ("current", current_base_pose, source.current_timer_us,
             source.current_global_ns),
        ):
            if (
                base_pose.node != source.node
                or base_pose.boot_epoch != source.boot_epoch
                or base_pose.source_timer_us != timer_us
                or base_pose.source_global_ns != base_global_ns
                or not hmac.compare_digest(
                    base_pose.clock_mapping_digest, source.mapping_digest
                )
                or self.native200_base_pose_owner_digest is None
                or not hmac.compare_digest(
                    base_pose.base_pose_owner_digest,
                    self.native200_base_pose_owner_digest,
                )
            ):
                raise ValueError(f"native200 {role} base/source identity mismatch")
        global_ns = source.current_global_ns
        if _orientation_ns_from_seconds(time_s, name="time_s") != global_ns:
            raise ValueError("native200 pose sample time mismatch")
        return self.pose.sample(
            global_ns * 1e-9, source_node=source.node,
            source_boot_epoch=source.boot_epoch,
            previous_source_timer_us=source.previous_timer_us,
            source_timer_us=source.current_timer_us,
            previous_source_global_ns=source.previous_global_ns,
            source_global_ns=source.current_global_ns,
            source_clock_mapping_digest=source.mapping_digest,
            previous_base_rotations_world=previous_base_pose.base_rotations_world,
            current_base_rotations_world=current_base_pose.base_rotations_world,
        )

    def native200_clock_mapping_owner(
        self, *, node, clock_owner_sha256,
    ) -> Native200ClockMappingOwner:
        if node not in self.static.clocks:
            raise ValueError("native200 source node is not owned by static clock")
        clock = self.static.clocks[node]
        if (
            self.native200_clock_owner_sha256 is not None
            and not hmac.compare_digest(
                str(clock_owner_sha256), self.native200_clock_owner_sha256
            )
        ):
            raise ValueError("native200 clock owner SHA differs from sealed owner")
        return Native200ClockMappingOwner(
            node=str(node), clock_domain="B306_TIMER2",
            boot_epoch=int(clock.boot_epoch),
            a_ns_per_us=float(clock.a_ns_per_us), b_ns=float(clock.b_ns),
            clock_owner_sha256=str(clock_owner_sha256),
        )

    def _validate_native200_mapping_owner(
        self, owner: Native200ClockMappingOwner
    ) -> None:
        expected = self.native200_clock_mapping_owner(
            node=owner.node, clock_owner_sha256=owner.clock_owner_sha256
        )
        if not hmac.compare_digest(expected.digest, owner.digest):
            raise ValueError("native200 source clock-domain owner mismatch")

    def native200_source_pair(
        self, *, clock_mapping_owner, previous_timer_us, current_timer_us,
        previous_global_ns, current_global_ns,
    ) -> Native200SourcePair:
        if type(clock_mapping_owner) is not Native200ClockMappingOwner:
            raise ValueError("native200 clock mapping owner required")
        self._validate_native200_mapping_owner(clock_mapping_owner)
        previous_timer = _canonical_orientation_ns(
            previous_timer_us, name="previous_timer_us"
        )
        current_timer = _canonical_orientation_ns(
            current_timer_us, name="current_timer_us"
        )
        previous_global = _canonical_orientation_ns(
            previous_global_ns, name="previous_global_ns"
        )
        current_global = _canonical_orientation_ns(
            current_global_ns, name="current_global_ns"
        )
        if (
            previous_global
            != clock_mapping_owner.global_ns(previous_timer)
            or current_global
            != clock_mapping_owner.global_ns(current_timer)
        ):
            raise ValueError("native200 source/global clock mapping mismatch")
        return Native200SourcePair(
            clock_mapping_owner.node, clock_mapping_owner.boot_epoch,
            previous_timer, current_timer, previous_global, current_global,
            clock_mapping_owner,
        )

    def _validate_native200_source_pair(self, source: Native200SourcePair) -> None:
        self._validate_native200_mapping_owner(source.clock_mapping_owner)
        expected = self.native200_source_pair(
            clock_mapping_owner=source.clock_mapping_owner,
            previous_timer_us=source.previous_timer_us,
            current_timer_us=source.current_timer_us,
            previous_global_ns=source.previous_global_ns,
            current_global_ns=source.current_global_ns,
        )
        if not hmac.compare_digest(expected.mapping_digest, source.mapping_digest):
            raise ValueError("native200 source clock-domain owner mismatch")

    def epoch(self, *, measurement_time_s, availability_time_s,
              previous_orientation_time_s, base_rotations_world,
              previous_correction_rotvec, point_constraints_world_m=None,
              provenance, orientation_time_ns=None,
              previous_orientation_time_ns=None,
              native200_source_pair=None) -> ArticulatedEpochOwner:
        if native200_source_pair is not None:
            if type(native200_source_pair) is not Native200SourcePair:
                raise ValueError("native200 source pair owner required")
            self._validate_native200_source_pair(native200_source_pair)
            orientation_time_ns = native200_source_pair.current_global_ns
            previous_orientation_time_ns = native200_source_pair.previous_global_ns
        current_ns = (
            _orientation_ns_from_seconds(
                measurement_time_s, name="measurement_time_s"
            )
            if orientation_time_ns is None
            else _canonical_orientation_ns(
                orientation_time_ns, name="orientation_time_ns"
            )
        )
        previous_ns = (
            _orientation_ns_from_seconds(
                previous_orientation_time_s,
                name="previous_orientation_time_s",
            )
            if previous_orientation_time_ns is None
            else _canonical_orientation_ns(
                previous_orientation_time_ns,
                name="previous_orientation_time_ns",
            )
        )
        return ArticulatedEpochOwner(
            measurement_time_s, availability_time_s, current_ns * 1e-9,
            previous_ns * 1e-9, NATIVE200_PERIOD_S,
            base_rotations_world, previous_correction_rotvec,
            {} if point_constraints_world_m is None else point_constraints_world_m,
            self.pose.publication_token().digest, self.geometry_digest,
            self.model_digest, provenance, current_ns, previous_ns,
            native200_source_pair,
        )

    def _published_result(self, *, accepted, reason, transaction, sequence,
                          trusted_nodes, base_rotations_world,
                          maximum_foothold_residual_m,
                          articulated_rejection_diagnostic=None,
                          robust_candidate_rejection_diagnostic=None,
                          obsolete_native200_source_pair_diagnostic=None):
        root_token = self.root.publication_token()
        correction = self.pose.transition_snapshot()["target_correction"]
        return self._materialize_result(
            accepted=accepted,reason=reason,transaction=transaction,
            sequence=sequence,trusted_nodes=trusted_nodes,
            base_rotations_world=base_rotations_world,
            maximum_foothold_residual_m=maximum_foothold_residual_m,
            root_state=root_token.state,correction=correction,
            pose_token_digest=self.pose.publication_token().digest,
            articulated_rejection_diagnostic=articulated_rejection_diagnostic,
            robust_candidate_rejection_diagnostic=(
                robust_candidate_rejection_diagnostic),
            obsolete_native200_source_pair_diagnostic=(
                obsolete_native200_source_pair_diagnostic))

    def _materialize_result(self, *, accepted, reason, transaction, sequence,
                            trusted_nodes, base_rotations_world,
                            maximum_foothold_residual_m, root_state, correction,
                            pose_token_digest, articulated_rejection_diagnostic=None,
                            robust_candidate_rejection_diagnostic=None,
                            obsolete_native200_source_pair_diagnostic=None):
        """Allocate and freeze every public result byte before owner mutation."""
        points = corrected_proxy_points(base_rotations_world, correction, self.pose.geometry)
        root_position = root_state.vector[:3].copy()
        covariance = root_state.covariance[:3, :3].copy()
        nodes = {
            node: root_position + points[point]
            for node, point in NODE_TO_PROXY_POINT.items()
        }
        direct = trusted_nodes if accepted else ()
        propagated = tuple(sorted(set(nodes) - set(direct)))
        frozen_correction = {key: np.asarray(value).copy() for key, value in correction.items()}
        for value in (root_position, covariance, *nodes.values(), *frozen_correction.values()):
            value.setflags(write=False)
        return AuthoritativeArticulatedResult(
            accepted, reason, transaction, sequence, trusted_nodes, direct,
            propagated, root_position, MappingProxyType(nodes),
            MappingProxyType(frozen_correction), pose_token_digest,
            covariance, maximum_foothold_residual_m, NATIVE200_PERIOD_S,
            JOINT_COVARIANCE_STATUS, JOINT_COVARIANCE_STATUS,
            ANATOMICAL_STATUS,
            articulated_rejection_diagnostic,
            robust_candidate_rejection_diagnostic,
            obsolete_native200_source_pair_diagnostic,
        )

    def _articulated_rejection_diagnostic(self, packet, epoch, trusted, solved):
        source = epoch.native200_source_pair
        source_identity = None if source is None else (
            source.node, source.boot_epoch, source.previous_timer_us,
            source.current_timer_us, source.previous_global_ns,
            source.current_global_ns, source.mapping_digest,
        )
        projection = solved.hinge_projection
        corrections = tuple(solved.segment_correction_rotvec.values())
        maximum_correction = max(
            (float(np.linalg.norm(value)) for value in corrections), default=0.0,
        )
        projection_inside = projection.get("post_projection_all_inside_rom")
        projection_gate = projection.get("projection_acceptance_gate")
        foothold_gate = projection.get("foothold_projection_gate")
        condition = (
            solved.condition if math.isfinite(solved.condition) else
            ("NAN" if math.isnan(solved.condition) else
             ("POSITIVE_INFINITY" if solved.condition > 0.0 else
              "NEGATIVE_INFINITY"))
        )
        return ArticulatedRangeRejectionDiagnostic(
            packet.digest, self._epoch_digest(epoch), packet.event.sequence,
            int(round(epoch.measurement_time_s * 1_000_000_000)),
            packet.availability_global_ns, source_identity, tuple(trusted),
            solved.reason,
            projection.get("projection_acceptance_owner"),
            projection.get("projection_acceptance_branch"),
            projection.get("projection_applied"),
            projection.get("point_constraints_present"),
            projection.get("point_constraint_count"),
            projection.get("point_constraint_identity"),
            projection.get("optimizer_success"),
            projection.get("optimizer_status"),
            projection.get("optimizer_message_class"),
            projection.get("optimizer_message"),
            solved.nfev, solved.cost,
            projection.get("prefit_range_median_abs_m"),
            projection.get("raw_optimized_range_median_abs_m"),
            projection.get("projected_range_median_abs_m"),
            projection.get("range_projection_gate"),
            projection.get("prefit_full_residual_objective"),
            projection.get("raw_optimized_full_residual_objective"),
            projection.get("projected_full_residual_objective"),
            projection.get("full_residual_objective_tolerance"),
            projection.get("joint_projection_gate"),
            projection.get("projection_acceptance_tolerance"),
            projection.get("numerical_geometry_success"),
            projection.get("optimizer_root_finite"),
            projection.get("rank_gate"),
            projection.get("condition_finite"),
            projection.get("condition_gate"),
            projection.get("correction_gate"),
            projection.get("correction_tolerance_rad"),
            projection.get("physical_residual_finite"),
            solved.rank, condition,
            maximum_correction,
            projection_inside if type(projection_inside) is bool else None,
            projection_inside if type(projection_inside) is bool else None,
            None,  # no distinct FK gate is exported by the solve result
            projection_gate if type(projection_gate) is bool else None,
            foothold_gate if type(foothold_gate) is bool else None,
            None,  # no distinct contact gate is exported by the solve result
        )

    def _prospective_pose_digest(
        self, transaction: PreparedCausalUpdateTransaction
    ) -> str:
        if transaction.pose_ticket is None:
            return transaction.pose_token.digest
        plan=transaction.pose_ticket.plan
        return self.pose._stable_hash({
            "revision":plan.pose_revision,
            "latest_sample_s":plan.latest_sample_s,
            "latest_availability_s":plan.latest_availability_s,
            "install_count":plan.install_count,
            "transition_start_s":plan.transition_start_s,
            "origin":plan.transition_origin_correction,
            "target":plan.target_correction,
            "continuity_generation":plan.hinge_continuity_generation,
        })

    def _epoch_digest(self, epoch: ArticulatedEpochOwner) -> str:
        source=epoch.native200_source_pair
        return _digest_payload({
            "measurement_time_s":epoch.measurement_time_s,
            "availability_time_s":epoch.availability_time_s,
            "orientation_time_ns":epoch.orientation_time_ns,
            "previous_orientation_time_ns":epoch.previous_orientation_time_ns,
            "base_rotations_world":epoch.base_rotations_world,
            "previous_correction_rotvec":epoch.previous_correction_rotvec,
            "point_constraints_world_m":epoch.point_constraints_world_m,
            "pose_token_digest":epoch.pose_token_digest,
            "geometry_digest":epoch.geometry_digest,
            "model_digest":epoch.model_digest,
            "provenance":epoch.provenance,
            "source":None if source is None else {
                "node":source.node,"boot_epoch":source.boot_epoch,
                "previous_timer_us":source.previous_timer_us,
                "current_timer_us":source.current_timer_us,
                "previous_global_ns":source.previous_global_ns,
                "current_global_ns":source.current_global_ns,
                "mapping_digest":source.mapping_digest,
            },
        })

    def _prepared_admission(
        self, *, packet, epoch, root_before, pose_before, robust_revision,
        trusted, root_candidate, correction, maximum_contact, root_only,
        causal_transaction=None, sidecar_ticket=None, prepared_result=None,
        robust_candidate_rejection_diagnostic=None,
        obsolete_native200_source_pair_diagnostic=None,
    ) -> PreparedArticulatedAdmission:
        frozen_root=np.asarray(root_candidate,dtype=float).reshape(3).copy()
        frozen_root.setflags(write=False)
        frozen_correction={
            key:np.asarray(value,dtype=float).reshape(3).copy()
            for key,value in correction.items()
        }
        for value in frozen_correction.values(): value.setflags(write=False)
        frozen_correction=MappingProxyType(frozen_correction)
        root_observation = (
            None if causal_transaction is None
            else causal_transaction.root_observation
        )
        frozen_observation = (
            None if root_observation is None
            else _readonly_position_observation(root_observation)
        )
        epoch_digest=self._epoch_digest(epoch)
        blank = PreparedArticulatedAdmission(
            self.static.digest,packet.digest,epoch_digest,root_before.revision,
            root_before.digest,pose_before.revision,pose_before.digest,
            robust_revision,self.robust.owner_digest,tuple(trusted),frozen_root,
            frozen_correction,frozen_observation,causal_transaction,sidecar_ticket,
            prepared_result,"",packet,epoch,float(maximum_contact),bool(root_only),
            self.__admission_key,_AdmissionCommitState(),
            articulated_rejection_diagnostic=(None if prepared_result is None else
                prepared_result.articulated_rejection_diagnostic),
            robust_candidate_rejection_diagnostic=(
                robust_candidate_rejection_diagnostic),
            obsolete_native200_source_pair_diagnostic=(
                obsolete_native200_source_pair_diagnostic))
        return replace(
            blank, public_candidate_digest=_prepared_admission_candidate_digest(blank)
        )

    def prepare_admission(
        self, packet: BoundGroupPacket, epoch: ArticulatedEpochOwner, *,
        received_at_committed_horizon: bool = False,
        allow_obsolete_native200_root_fallback: bool = False,
    ) -> PreparedArticulatedAdmission:
        """Compute one admission and every commit ticket without mutation."""
        root_before = self.root.publication_token()
        pose_before = self.pose.publication_token()
        robust_revision = self.robust.revision
        prepared = _prepare_dynamic_owner(self.static, packet)
        plan = self.robust.prepare(self.static, self.root, packet, prepared)
        if epoch.native200_source_pair is not None:
            self._validate_native200_source_pair(epoch.native200_source_pair)
        measurement_s = (
            plan.measurement_s
            if isinstance(plan, RobustCandidateMeasurementRejection)
            else plan.measurement_s
        )
        availability_s = (
            plan.availability_global_ns * 1e-9
            if isinstance(plan, RobustCandidateMeasurementRejection)
            else plan.availability_s
        )
        if (
            epoch.measurement_time_s != measurement_s
            or epoch.availability_time_s != availability_s
            or not hmac.compare_digest(epoch.pose_token_digest, pose_before.digest)
            or not hmac.compare_digest(epoch.geometry_digest, self.geometry_digest)
            or not hmac.compare_digest(epoch.model_digest, self.model_digest)
        ):
            raise ValueError("articulated epoch is not bound to this causal group/pre-state")

        if isinstance(plan, RobustCandidateMeasurementRejection):
            trusted = tuple(plan.trusted_partition)
            correction = self.pose.transition_snapshot()["target_correction"]
            if plan.solver_success and plan.rank != 3:
                public_reason = "ROBUST_RANK_INVALID"
            elif plan.solver_success and isinstance(plan.condition, str):
                public_reason = "ROBUST_CONDITION_NONFINITE"
            else:
                public_reason = plan.solver_reason
            rejected = self._published_result(
                accepted=False,
                reason=public_reason,
                transaction=None, sequence=packet.event.sequence,
                trusted_nodes=trusted,
                base_rotations_world=epoch.base_rotations_world,
                maximum_foothold_residual_m=0.0,
                robust_candidate_rejection_diagnostic=plan,
            )
            return self._prepared_admission(
                packet=packet, epoch=epoch, root_before=root_before,
                pose_before=pose_before, robust_revision=robust_revision,
                trusted=trusted, root_candidate=root_before.state.vector[:3],
                correction=correction, maximum_contact=0.0, root_only=True,
                prepared_result=rejected,
                robust_candidate_rejection_diagnostic=plan,
            )

        trusted = tuple(plan.selection.trusted_nodes)
        previous = {key: value.copy() for key, value in epoch.previous_correction_rotvec.items()}
        contact_segments = {
            segment for point in epoch.point_constraints_world_m
            for segment in NODE_KINEMATIC_PATHS[_POINT_TO_NODE[point]]
        }
        active = tuple(segment for segment in SEGMENTS if segment in (
            set(active_segments_for_nodes(trusted)) | contact_segments
        ))
        root_candidate = plan.candidate.root_position_m.copy()
        maximum_contact = 0.0
        root_position_mode = len(trusted) == 1
        fallback_diagnostic = None
        fallback_reason = None
        obsolete_diagnostic = None
        source = epoch.native200_source_pair
        source_binding = None if source is None else {
            "source_node": source.node,
            "source_boot_epoch": source.boot_epoch,
            "previous_timer_us": source.previous_timer_us,
            "current_timer_us": source.current_timer_us,
            "previous_global_ns": source.previous_global_ns,
            "current_global_ns": source.current_global_ns,
            "source_clock_mapping_digest": source.mapping_digest,
        }
        if (
            allow_obsolete_native200_root_fallback
            and len(trusted) > 1
            and source_binding is not None
        ):
            obsolete_diagnostic = (
                self.pose.obsolete_native200_source_pair_diagnostic(
                    **source_binding,
                )
            )
        if obsolete_diagnostic is not None:
            correction = previous
            points = corrected_proxy_points(
                epoch.base_rotations_world, correction, self.pose.geometry,
            )
            maximum_contact = max((
                float(np.linalg.norm(root_candidate + points[key] - target))
                for key, target in epoch.point_constraints_world_m.items()
            ), default=0.0)
            if maximum_contact > (
                POINT_CONSTRAINT_GATE_SIGMA * DEFAULT_POINT_CONSTRAINT_SIGMA_M
            ):
                rejected = self._published_result(
                    accepted=False, reason="STANCE_FOOT_SLIP_REJECTED",
                    transaction=None, sequence=packet.event.sequence,
                    trusted_nodes=trusted,
                    base_rotations_world=epoch.base_rotations_world,
                    maximum_foothold_residual_m=maximum_contact,
                    obsolete_native200_source_pair_diagnostic=obsolete_diagnostic,
                )
                return self._prepared_admission(
                    packet=packet, epoch=epoch, root_before=root_before,
                    pose_before=pose_before, robust_revision=robust_revision,
                    trusted=trusted, root_candidate=root_candidate,
                    correction=correction, maximum_contact=maximum_contact,
                    root_only=True, prepared_result=rejected,
                    obsolete_native200_source_pair_diagnostic=obsolete_diagnostic,
                )
            root_position_mode = True
            fallback_reason = "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
        elif root_position_mode:
            correction = previous
            points = corrected_proxy_points(epoch.base_rotations_world, correction, self.pose.geometry)
            maximum_contact = max((
                float(np.linalg.norm(root_candidate + points[key] - target))
                for key, target in epoch.point_constraints_world_m.items()
            ), default=0.0)
            if maximum_contact > (
                POINT_CONSTRAINT_GATE_SIGMA * DEFAULT_POINT_CONSTRAINT_SIGMA_M
            ):
                rejected=self._published_result(
                    accepted=False, reason="STANCE_FOOT_SLIP_REJECTED",
                    transaction=None, sequence=packet.event.sequence,
                    trusted_nodes=trusted,
                    base_rotations_world=epoch.base_rotations_world,
                    maximum_foothold_residual_m=maximum_contact,
                )
                return self._prepared_admission(
                    packet=packet,epoch=epoch,root_before=root_before,
                    pose_before=pose_before,robust_revision=robust_revision,
                    trusted=trusted,root_candidate=root_candidate,
                    correction=correction,maximum_contact=maximum_contact,
                    root_only=True,prepared_result=rejected)
        else:
            solved = solve_articulated_ranges(
                plan.selection.trusted_links, anchors_m=self.static.anchors_m,
                base_rotations_world=epoch.base_rotations_world,
                geometry=self.pose.geometry, initial_root_m=root_candidate,
                fixed_root_position_m=root_candidate,
                root_velocity_mps=root_before.state.vector[3:6],
                previous_correction_rotvec=previous, active_segments=active,
                point_constraints_world_m=epoch.point_constraints_world_m,
                hinge_projector=self.pose.hinge_projector, config=self.config,
            )
            if not solved.success:
                fallback_diagnostic = self._articulated_rejection_diagnostic(
                    packet, epoch, trusted, solved,
                )
                correction = previous
                points = corrected_proxy_points(
                    epoch.base_rotations_world, correction, self.pose.geometry,
                )
                maximum_contact = max((
                    float(np.linalg.norm(root_candidate + points[key] - target))
                    for key, target in epoch.point_constraints_world_m.items()
                ), default=0.0)
                if maximum_contact > (
                    POINT_CONSTRAINT_GATE_SIGMA * DEFAULT_POINT_CONSTRAINT_SIGMA_M
                ):
                    rejected=self._published_result(
                        accepted=False, reason="STANCE_FOOT_SLIP_REJECTED",
                        transaction=None, sequence=packet.event.sequence,
                        trusted_nodes=trusted,
                        base_rotations_world=epoch.base_rotations_world,
                        maximum_foothold_residual_m=maximum_contact,
                        articulated_rejection_diagnostic=fallback_diagnostic,
                    )
                    return self._prepared_admission(
                        packet=packet,epoch=epoch,root_before=root_before,
                        pose_before=pose_before,robust_revision=robust_revision,
                        trusted=trusted,root_candidate=root_candidate,
                        correction=correction,maximum_contact=maximum_contact,
                        root_only=True,prepared_result=rejected)
                root_position_mode = True
                fallback_reason = (
                    "ACCEPTED_ROOT_FALLBACK_ARTICULATED_REJECTED:"
                    f"{solved.reason}"
                )
            else:
                correction = {key: value.copy() for key, value in solved.segment_correction_rotvec.items()}
                projection = solved.hinge_projection
                maximum_contact = solved.maximum_joint_closure_m
        if fallback_diagnostic is not None and epoch.native200_source_pair is None:
            rejected=self._published_result(
                accepted=False,
                reason="ARTICULATED_ROOT_FALLBACK_NATIVE200_SOURCE_REQUIRED",
                transaction=None, sequence=packet.event.sequence,
                trusted_nodes=trusted,
                base_rotations_world=epoch.base_rotations_world,
                maximum_foothold_residual_m=maximum_contact,
                articulated_rejection_diagnostic=fallback_diagnostic,
            )
            return self._prepared_admission(
                packet=packet,epoch=epoch,root_before=root_before,
                pose_before=pose_before,robust_revision=robust_revision,
                trusted=trusted,root_candidate=root_candidate,
                correction=correction,maximum_contact=maximum_contact,
                root_only=True,prepared_result=rejected)
        if not root_position_mode and projection.get("post_projection_all_inside_rom") is not True:
            rejected=self._published_result(
                accepted=False, reason="ARTICULATED_ROM_REJECTED",
                transaction=None, sequence=packet.event.sequence,
                trusted_nodes=trusted,
                base_rotations_world=epoch.base_rotations_world,
                maximum_foothold_residual_m=maximum_contact,
            )
            return self._prepared_admission(
                packet=packet,epoch=epoch,root_before=root_before,
                pose_before=pose_before,robust_revision=robust_revision,
                trusted=trusted,root_candidate=root_candidate,
                correction=correction,maximum_contact=maximum_contact,
                root_only=False,prepared_result=rejected)

        observation = PositionObservation(
            plan.measurement_s, plan.availability_s, root_candidate,
            np.eye(3) * adaptive_root_minimum_std_m(len(trusted)) ** 2,
            "C2_ROBUST_ARTICULATED_ROOT",
            plan.candidate.anchors_used,
            "ROBUST_SHARED_ROOT_POSTERIOR_FROM_PRE_GROUP_STATE",
            source_sequence=packet.event.sequence,
        )
        if not root_position_mode and epoch.native200_source_pair is None:
            raise ValueError("articulated update requires authenticated native200 source pair")
        def prepare_transaction(*, root_only):
            return prepare_causal_update_transaction(
                root=self.root, observation=observation,
                kind=(CandidateKind.ROOT_POSITION if root_only
                      else CandidateKind.ARTICULATED_IK),
                nominal_envelope=self.static.nominal_envelope,
                pose=None if root_only else self.pose,
                correction_at_measurement=None if root_only else correction,
                hinge_projection_at_source=None if root_only else projection,
                native200_source_binding=None if root_only else source_binding,
                dynamic_envelope=packet.event.dynamic_envelope,
                activity=packet.event.activity, consensus=packet.event.consensus,
                contact=packet.event.contact, record_rejection=False,
                received_at_committed_horizon=received_at_committed_horizon,
            )

        try:
            transaction = prepare_transaction(root_only=root_position_mode)
        except ObsoleteNative200SourcePair as error:
            if (
                not allow_obsolete_native200_root_fallback
                or root_position_mode
                or len(trusted) <= 1
            ):
                raise
            obsolete_diagnostic = error.diagnostic
            correction = previous
            points = corrected_proxy_points(
                epoch.base_rotations_world, correction, self.pose.geometry,
            )
            maximum_contact = max((
                float(np.linalg.norm(root_candidate + points[key] - target))
                for key, target in epoch.point_constraints_world_m.items()
            ), default=0.0)
            if maximum_contact > (
                POINT_CONSTRAINT_GATE_SIGMA * DEFAULT_POINT_CONSTRAINT_SIGMA_M
            ):
                rejected = self._published_result(
                    accepted=False, reason="STANCE_FOOT_SLIP_REJECTED",
                    transaction=None, sequence=packet.event.sequence,
                    trusted_nodes=trusted,
                    base_rotations_world=epoch.base_rotations_world,
                    maximum_foothold_residual_m=maximum_contact,
                    obsolete_native200_source_pair_diagnostic=obsolete_diagnostic,
                )
                return self._prepared_admission(
                    packet=packet, epoch=epoch, root_before=root_before,
                    pose_before=pose_before, robust_revision=robust_revision,
                    trusted=trusted, root_candidate=root_candidate,
                    correction=correction, maximum_contact=maximum_contact,
                    root_only=True, prepared_result=rejected,
                    obsolete_native200_source_pair_diagnostic=obsolete_diagnostic,
                )
            root_position_mode = True
            fallback_reason = "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
            transaction = prepare_transaction(root_only=True)
        if not transaction.result.root_committed:
            rejected=self._published_result(
                accepted=False,reason=transaction.result.decision.reason.value,
                transaction=transaction.result,sequence=packet.event.sequence,
                trusted_nodes=trusted,base_rotations_world=epoch.base_rotations_world,
                maximum_foothold_residual_m=maximum_contact,
                articulated_rejection_diagnostic=fallback_diagnostic,
                obsolete_native200_source_pair_diagnostic=obsolete_diagnostic)
            return self._prepared_admission(
                packet=packet,epoch=epoch,root_before=root_before,
                pose_before=pose_before,robust_revision=robust_revision,
                trusted=trusted,root_candidate=root_candidate,
                correction=correction,maximum_contact=maximum_contact,
                root_only=root_position_mode,prepared_result=rejected,
                obsolete_native200_source_pair_diagnostic=obsolete_diagnostic)
        robust_ticket=self.robust.prevalidate_commit(plan,self.root,packet)
        sidecar=PreparedCausalSidecarTicket(
            self.robust,robust_ticket,robust_revision,
            _validate_robust_sidecar,_apply_robust_sidecar,_rollback_robust_sidecar)
        prospective_root=transaction.root_ticket.snapshots[-1].state
        result_correction=(
            self.pose.transition_snapshot()["target_correction"]
            if root_position_mode else transaction.pose_ticket.plan.target_correction
        )
        prospective_pose_digest=(
            pose_before.digest if root_position_mode
            else self._prospective_pose_digest(transaction)
        )
        accepted_result=self._materialize_result(
            accepted=True,reason=(fallback_reason or "ACCEPTED"),transaction=transaction.result,
            sequence=packet.event.sequence,trusted_nodes=trusted,
            base_rotations_world=epoch.base_rotations_world,
            maximum_foothold_residual_m=maximum_contact,
            root_state=prospective_root,correction=result_correction,
            pose_token_digest=prospective_pose_digest,
            articulated_rejection_diagnostic=fallback_diagnostic,
            obsolete_native200_source_pair_diagnostic=obsolete_diagnostic)
        return self._prepared_admission(
            packet=packet,epoch=epoch,root_before=root_before,
            pose_before=pose_before,robust_revision=robust_revision,
            trusted=trusted,root_candidate=root_candidate,
            correction=correction,maximum_contact=maximum_contact,
            root_only=root_position_mode,causal_transaction=transaction,
            sidecar_ticket=sidecar,prepared_result=accepted_result,
            obsolete_native200_source_pair_diagnostic=obsolete_diagnostic)

    def commit_admission(
        self, prepared: PreparedArticulatedAdmission
    ) -> AuthoritativeArticulatedResult:
        """Commit one plan prepared by this exact engine, at most once."""
        if (
            type(prepared) is not PreparedArticulatedAdmission
            or prepared.key is not _PREPARED_ADMISSION_KEY
            or prepared.engine_key is not self.__admission_key
            or prepared.commit_state.consumed
            or not hmac.compare_digest(
                prepared.public_candidate_digest,
                _prepared_admission_candidate_digest(prepared),
            )
        ):
            raise RuntimeError("STALE_INVALID_OR_CONSUMED_ARTICULATED_ADMISSION")
        if prepared.causal_transaction is None or prepared.prepared_result is None:
            raise RuntimeError("REJECTED_ARTICULATED_ADMISSION_CANNOT_COMMIT")
        if (
            prepared.root_observation is None
            or prepared.root_observation.root_position_m.flags.writeable
            or prepared.root_observation.covariance_m2.flags.writeable
            or _digest_payload(vars(prepared.root_observation))
            != _digest_payload(vars(prepared.causal_transaction.root_observation))
            or prepared.causal_transaction.root_plan_digest
            != prepared.causal_transaction.root_plan.digest
            or _digest_payload(vars(prepared.causal_transaction.root_observation))
            != _digest_payload(vars(
                prepared.causal_transaction.root_plan.observation
            ))
        ):
            raise RuntimeError("INVALID_ARTICULATED_ROOT_OBSERVATION")
        root_token=self.root.publication_token()
        pose_token=self.pose.publication_token()
        if (
            not hmac.compare_digest(prepared.static_digest,self.static.digest)
            or not hmac.compare_digest(prepared.packet_digest,prepared.packet.digest)
            or not hmac.compare_digest(prepared.epoch_digest,self._epoch_digest(prepared.epoch))
            or root_token.revision != prepared.root_revision
            or not hmac.compare_digest(root_token.digest,prepared.root_digest)
            or pose_token.revision != prepared.pose_revision
            or not hmac.compare_digest(pose_token.digest,prepared.pose_digest)
            or self.robust.revision != prepared.robust_revision
            or not hmac.compare_digest(self.robust.owner_digest,prepared.robust_digest)
        ):
            raise RuntimeError("STALE_ARTICULATED_ADMISSION")
        prepared.commit_state.consumed=True
        commit_causal_update_transaction(
            prepared.causal_transaction,prepared.sidecar_ticket)
        return prepared.prepared_result

    def admit(self, packet: BoundGroupPacket,
              epoch: ArticulatedEpochOwner) -> AuthoritativeArticulatedResult:
        """Compatibility wrapper: prepare once and commit when accepted."""
        prepared=self.prepare_admission(packet,epoch)
        if prepared.causal_transaction is None:
            return prepared.prepared_result
        return self.commit_admission(prepared)
