"""Frame-independent strict-causal common-root filters and mode management."""
from __future__ import annotations
from copy import deepcopy

from dataclasses import dataclass, replace
import hashlib
import hmac
import math
import struct
from enum import Enum

import numpy as np

from .models import (
    AdditiveRootConstraint,
    BoundedTargetRootConstraint,
    ImuSample,
    PositionObservation,
    RootOutput,
    RootConstraintOperator,
    RootState,
    SystemMode,
    UpdateDecision,
)

GRAVITY_WORLD_MPS2 = np.array([0.0, 0.0, -9.80665])

_AUTHORITATIVE_BASELINE_VECTOR_ATOL = 2e-10
_AUTHORITATIVE_BASELINE_COVARIANCE_ATOL = 2e-9
_AUTHORITATIVE_BASELINE_COVARIANCE_ULPS = 32.0


def _authoritative_baseline_covariance_tolerance(
    replayed: np.ndarray,
    current: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return the absolute floor plus a componentwise machine-precision budget."""

    replayed_covariance = np.asarray(replayed, dtype=float)
    current_covariance = np.asarray(current, dtype=float)
    spacing = np.maximum(
        np.abs(np.spacing(replayed_covariance)),
        np.abs(np.spacing(current_covariance)),
    )
    tolerance = (
        _AUTHORITATIVE_BASELINE_COVARIANCE_ATOL
        + _AUTHORITATIVE_BASELINE_COVARIANCE_ULPS * spacing
    )
    return tolerance, spacing


def _authoritative_baseline_equivalent(
    replayed: "RootState",
    current: "RootState",
) -> bool:
    """Compare two algebraically equivalent replay paths at machine precision."""

    covariance_tolerance, _ = _authoritative_baseline_covariance_tolerance(
        replayed.covariance,
        current.covariance,
    )
    return bool(
        np.allclose(
            replayed.vector,
            current.vector,
            rtol=0.0,
            atol=_AUTHORITATIVE_BASELINE_VECTOR_ATOL,
        )
        and np.all(
            np.abs(replayed.covariance - current.covariance)
            <= covariance_tolerance
        )
    )


@dataclass(frozen=True)
class RootFilterConfig:
    inertial_acceleration_noise_mps2_sqrt_hz: float = 0.30
    cv_acceleration_noise_mps2_sqrt_hz: float = 0.50
    accelerometer_bias_rw_mps3_sqrt_hz: float = 0.003
    nis_limit_3d: float = 16.26623619623813
    maximum_position_influence_m: float = 0.05
    fixed_lag_s: float = 0.10
    uwb_stale_s: float = 0.18
    uwb_dropout_s: float = 0.36
    recovery_good_events: int = 5
    covariance_floor: float = 1e-12


class RootTranslationEdgeMode(str, Enum):
    """Authoritative mean-propagation mode for one root history edge."""

    INERTIAL = "INERTIAL"
    CV_NO_ACCELERATION = "CV_NO_ACCELERATION"


@dataclass(frozen=True)
class _IncomingEdge:
    """One immutable propagation owner for an authoritative snapshot edge.

    The destination/right-end IMU sample owns force and rotation over the
    complete edge. Acceleration white noise is divisible across edge pieces;
    the discrete bias-noise term is owned once, at the original edge end.
    """

    start_time_s: float
    end_time_s: float
    force_sensor_mps2: np.ndarray
    rotation_world_from_sensor: np.ndarray
    inertial: bool
    acceleration_noise_variance: float
    endpoint_noise_covariance: np.ndarray
    full_edge_process_noise_covariance: np.ndarray
    input_owner: str

    def __post_init__(self) -> None:
        force = np.asarray(self.force_sensor_mps2, dtype=float).copy()
        rotation = np.asarray(
            self.rotation_world_from_sensor, dtype=float
        ).copy()
        endpoint_noise = np.asarray(
            self.endpoint_noise_covariance, dtype=float
        ).copy()
        full_noise = np.asarray(
            self.full_edge_process_noise_covariance, dtype=float
        ).copy()
        if (
            not math.isfinite(self.start_time_s)
            or not math.isfinite(self.end_time_s)
            or self.end_time_s <= self.start_time_s
        ):
            raise ValueError("incoming edge requires increasing finite times")
        if force.shape != (3,) or rotation.shape != (3, 3):
            raise ValueError("incoming edge has invalid input shape")
        if endpoint_noise.shape != (9, 9) or full_noise.shape != (9, 9):
            raise ValueError("incoming edge has invalid process-noise shape")
        if not (
            np.isfinite(force).all()
            and np.isfinite(rotation).all()
            and np.isfinite(endpoint_noise).all()
            and np.isfinite(full_noise).all()
            and math.isfinite(self.acceleration_noise_variance)
            and self.acceleration_noise_variance >= 0.0
            and self.input_owner
        ):
            raise ValueError("incoming edge contains invalid ownership data")
        for array in (force, rotation, endpoint_noise, full_noise):
            array.setflags(write=False)
        object.__setattr__(self, "force_sensor_mps2", force)
        object.__setattr__(self, "rotation_world_from_sensor", rotation)
        object.__setattr__(self, "endpoint_noise_covariance", endpoint_noise)
        object.__setattr__(
            self, "full_edge_process_noise_covariance", full_noise
        )


@dataclass(frozen=True)
class _Snapshot:
    state: RootState
    applied_constraint_cursor: int
    incoming_edge: _IncomingEdge | None = None


@dataclass(frozen=True)
class _AuthoritativeRootEvent:
    sequence: int
    time_s: float
    owner: str
    operator: RootConstraintOperator


class AuthoritativeBaselineReconstructionError(RuntimeError):
    """Fail-closed delayed-replay error with immutable diagnostic values."""

    def __init__(self, diagnostic: dict):
        self.diagnostic = diagnostic
        super().__init__(
            "authoritative root event baseline is not reconstructible; "
            f"measurement_time_s={diagnostic['measurement_time_s']}; "
            f"processing_time_s={diagnostic['processing_time_s']}; "
            "vector_max_abs_delta="
            f"{diagnostic['replayed_minus_current_vector_max_abs']}; "
            "covariance_max_abs_delta="
            f"{diagnostic['replayed_minus_current_covariance_max_abs']}"
        )


@dataclass
class _Health:
    accepted: int = 0
    rejected: int = 0
    consecutive_rejected: int = 0
    last_reason: str | None = None


@dataclass(frozen=True)
class RootPublicationToken:
    """Authoritative single-thread publication identity for U2 transactions."""

    authority: object
    revision: int
    time_s: float
    state: RootState
    digest: str


@dataclass(frozen=True)
class CommittedRootStateToken:
    """Read-only committed delayed-state query bound to one publication revision."""

    authority: object
    publication_revision: int
    query_time_s: float
    state: RootState
    digest: str


@dataclass(frozen=True)
class PreparedRootCausalState:
    """Opaque root-owned state at a causal measurement/reference epoch."""

    authority: object
    base_revision: int
    base_publication_digest: str
    reference_time_s: float
    availability_time_s: float
    state: RootState
    base_owner_digest: str
    tail_edge_digest: str | None
    digest: str
    _base_bundle: _RootRollbackBundle
    _tail_edge: _IncomingEdge | None


@dataclass(frozen=True)
class _PreparedRootPositionPlan:
    authority: object
    base_revision: int
    observation: PositionObservation
    processing_time_s: float
    decision: UpdateDecision
    imu_prediction: RootState
    measurement_candidate: RootState
    snapshots: tuple[_Snapshot, ...]
    health: tuple[tuple[str,int,int,int,str | None], ...]
    anchor_health: tuple[tuple[int,int,int,int,str | None], ...]
    recovery_good: int
    mode: SystemMode
    last_observation_measurement_s: float
    last_accepted_measurement_s: float | None
    last_accepted_availability_s: float | None
    last_availability_s: float
    causal_state_digest: str | None
    digest: str


@dataclass(frozen=True)
class _PreparedRootGapPlan:
    authority: object
    base_revision: int
    gap_start_time_s: float
    gap_end_time_s: float
    availability_time_s: float
    snapshots: tuple[_Snapshot, ...]
    post_gap_sample: ImuSample | None
    source_gap_owner: str | None
    following_input_mode: RootTranslationEdgeMode | None
    digest: str


@dataclass(frozen=True)
class _RootCommitBundle:
    authority: object
    revision: int
    digest: str
    snapshots: list[_Snapshot]
    health: dict[str,_Health]
    anchor_health: dict[int,_Health]
    recovery_good: int
    mode: SystemMode
    last_observation_measurement_s: float
    last_accepted_measurement_s: float | None
    last_accepted_availability_s: float | None
    last_availability_s: float
    decision: UpdateDecision


@dataclass(frozen=True)
class _RootRollbackBundle:
    snapshots: list[_Snapshot]
    health: dict[str, _Health]
    anchor_health: dict[int, _Health]
    recovery_good: int
    mode: SystemMode
    last_observation_measurement_s: float
    last_accepted_measurement_s: float | None
    last_accepted_availability_s: float | None
    last_availability_s: float
    publication_revision: int
    constraint_events: list[_AuthoritativeRootEvent]
    next_constraint_sequence: int
    last_force: np.ndarray
    last_rotation: np.ndarray
    following_input_mode: RootTranslationEdgeMode
    last_source_gap_owner: str | None
    terminal_missing_owner: str | None
    emission_times: list[float]
    future_imu_count: int
    future_uwb_count: int
    preavailability_output_count: int
    late_imu_rejected: int


@dataclass(frozen=True)
class PreparedRootImuTransaction:
    """Opaque, immutable result of evaluating one IMU against one root revision."""

    authority: object
    base_revision: int
    base_publication_digest: str
    sample: ImuSample
    edge_mode: RootTranslationEdgeMode
    following_input_mode: RootTranslationEdgeMode
    accepted: bool
    reason: str
    candidate_state: RootState
    candidate_revision: int
    candidate_snapshot_count: int
    base_owner_digest: str
    candidate_owner_digest: str
    digest: str
    _base_bundle: _RootRollbackBundle
    _candidate_bundle: _RootRollbackBundle


@dataclass(frozen=True)
class PreparedRootImuVelocityTransaction:
    """Opaque, one-shot native IMU plus bounded velocity-only constraint."""

    authority: object
    base_revision: int
    base_publication_digest: str
    imu_plan_digest: str
    velocity_delta_mps: np.ndarray
    maximum_velocity_step_mps: float
    owner: str
    base_owner_digest: str
    candidate_owner_digest: str
    digest: str
    _base_bundle: _RootRollbackBundle
    _candidate_bundle: _RootRollbackBundle


@dataclass(frozen=True)
class PreparedRootFutureImuTransaction:
    """One IMU plan prepared against an accepted, still-uncommitted p plan."""

    authority: object
    base_revision: int
    base_publication_digest: str
    position_plan_digest: str
    frame_digest: str
    imu_plan: PreparedRootImuTransaction
    future_base_owner_digest: str
    digest: str


@dataclass(frozen=True)
class PreparedRootFutureImuVelocityTransaction:
    """A future-base IMU plan compounded with its bounded velocity update."""

    authority: object
    base_revision: int
    base_publication_digest: str
    position_plan_digest: str
    frame_digest: str
    future_imu_digest: str
    imu_velocity_plan: PreparedRootImuVelocityTransaction
    digest: str


@dataclass(frozen=True)
class PreparedRootCurrentConstraint:
    """Opaque current-epoch additive constraint ready for atomic composition."""

    authority: object
    base_revision: int
    base_publication_digest: str
    updated: RootState
    operator: AdditiveRootConstraint
    owner: str
    candidate_owner_digest: str
    digest: str
    _candidate_bundle: _RootRollbackBundle


@dataclass(frozen=True)
class PreparedRootPositionVelocityTransaction:
    """Opaque delayed p-only plus bounded current-velocity transaction."""

    authority: object
    base_revision: int
    base_publication_digest: str
    position_plan_digest: str
    velocity_delta_mps: np.ndarray
    maximum_velocity_step_mps: float
    owner: str
    base_owner_digest: str
    candidate_owner_digest: str
    digest: str
    _base_bundle: _RootRollbackBundle
    _candidate_bundle: _RootRollbackBundle


@dataclass(frozen=True)
class PreparedRootPositionRejectionTransaction:
    """Opaque exact rejection-health transaction with no numerical update."""

    authority: object
    base_revision: int
    base_publication_digest: str
    position_plan_digest: str
    reason: str
    base_owner_digest: str
    candidate_owner_digest: str
    digest: str
    _base_bundle: _RootRollbackBundle
    _candidate_bundle: _RootRollbackBundle


def _root_hash_update(owner, value):
    if value is None: owner.update(b"N"); return
    if isinstance(value,bool): owner.update(b"B1" if value else b"B0"); return
    if isinstance(value,int): owner.update(b"I"+struct.pack("!q",value)); return
    if isinstance(value,float): owner.update(b"F"+struct.pack("!d",value)); return
    if isinstance(value,Enum): _root_hash_update(owner,value.value); return
    if isinstance(value,str): raw=value.encode(); owner.update(b"S"+struct.pack("!I",len(raw))+raw); return
    if isinstance(value,np.ndarray): owner.update(b"A"+value.dtype.str.encode()+struct.pack("!I",value.ndim)+struct.pack("!"+"q"*value.ndim,*value.shape)+value.tobytes()); return
    if isinstance(value,(tuple,list)):
        owner.update(b"T"+struct.pack("!I",len(value)))
        for item in value: _root_hash_update(owner,item)
        return
    if isinstance(value,RootState): _root_hash_update(owner,(value.time_s,value.vector,value.covariance)); return
    if isinstance(value,ImuSample): _root_hash_update(owner,(value.measurement_time_s,value.availability_time_s,value.specific_force_sensor_mps2,value.rotation_world_from_sensor,value.source_sequence,value.m1_valid,value.m1_reset)); return
    if isinstance(value,_IncomingEdge): _root_hash_update(owner,(value.start_time_s,value.end_time_s,value.force_sensor_mps2,value.rotation_world_from_sensor,value.inertial,value.acceleration_noise_variance,value.endpoint_noise_covariance,value.full_edge_process_noise_covariance,value.input_owner)); return
    if isinstance(value,_Snapshot): _root_hash_update(owner,(value.state,value.applied_constraint_cursor,value.incoming_edge)); return
    if isinstance(value,_AuthoritativeRootEvent): _root_hash_update(owner,(value.sequence,value.time_s,value.owner,value.operator)); return
    if isinstance(value,AdditiveRootConstraint): _root_hash_update(owner,("ADDITIVE",value.vector_delta)); return
    if isinstance(value,BoundedTargetRootConstraint): _root_hash_update(owner,("BOUNDED",value.constrained_axes,value.position_target_m,value.velocity_target_mps,value.confidence,value.position_gain,value.velocity_gain,value.maximum_position_step_m,value.maximum_velocity_step_mps,value.ankle_z_offset_m,value.ankle_z_entry_m,value.ankle_z_lower_m,value.ankle_z_upper_m)); return
    if isinstance(value,PositionObservation): _root_hash_update(owner,(value.measurement_time_s,value.availability_time_s,value.root_position_m,value.covariance_m2,value.tag_id,value.anchors,value.quality_state,value.frame_valid,value.physical_point_valid,value.source_sequence)); return
    if isinstance(value,UpdateDecision): _root_hash_update(owner,(value.accepted,value.reason,value.nis,value.innovation_m,value.applied_position_delta_m,value.influence_scale,value.availability_applied_position_delta_m,value.availability_applied_velocity_delta_mps,value.availability_influence_scale)); return
    raise TypeError(f"unsupported root plan digest value {type(value)!r}")


def _root_imu_bundle_digest(bundle: _RootRollbackBundle) -> str:
    owner = hashlib.sha256()
    _root_hash_update(owner, (
        tuple(bundle.snapshots),
        tuple((key, value.accepted, value.rejected, value.consecutive_rejected, value.last_reason)
              for key, value in sorted(bundle.health.items())),
        tuple((key, value.accepted, value.rejected, value.consecutive_rejected, value.last_reason)
              for key, value in sorted(bundle.anchor_health.items())),
        bundle.recovery_good, bundle.mode, bundle.last_observation_measurement_s,
        bundle.last_accepted_measurement_s, bundle.last_accepted_availability_s,
        bundle.last_availability_s, bundle.publication_revision,
        tuple(bundle.constraint_events), bundle.next_constraint_sequence,
        bundle.last_force, bundle.last_rotation, bundle.following_input_mode,
        bundle.last_source_gap_owner, bundle.terminal_missing_owner,
        tuple(bundle.emission_times), bundle.future_imu_count, bundle.future_uwb_count,
        bundle.preavailability_output_count, bundle.late_imu_rejected,
    ))
    return owner.hexdigest()


def _root_imu_plan_digest(plan: PreparedRootImuTransaction) -> str:
    owner = hashlib.sha256()
    _root_hash_update(owner, (
        plan.base_revision, plan.base_publication_digest, plan.sample,
        plan.edge_mode, plan.following_input_mode, plan.accepted, plan.reason,
        plan.candidate_state, plan.candidate_revision,
        plan.candidate_snapshot_count, plan.base_owner_digest,
        plan.candidate_owner_digest,
    ))
    return owner.hexdigest()


def _root_imu_velocity_plan_digest(
    plan: PreparedRootImuVelocityTransaction,
) -> str:
    owner = hashlib.sha256()
    _root_hash_update(owner, (
        plan.base_revision, plan.base_publication_digest,
        plan.imu_plan_digest, plan.velocity_delta_mps,
        plan.maximum_velocity_step_mps, plan.owner,
        plan.base_owner_digest, plan.candidate_owner_digest,
    ))
    return owner.hexdigest()


def _root_future_imu_plan_digest(plan: PreparedRootFutureImuTransaction) -> str:
    owner = hashlib.sha256()
    _root_hash_update(owner, (
        plan.base_revision, plan.base_publication_digest,
        plan.position_plan_digest, plan.frame_digest,
        plan.imu_plan.digest, plan.future_base_owner_digest,
    ))
    return owner.hexdigest()


def _root_future_imu_velocity_plan_digest(
    plan: PreparedRootFutureImuVelocityTransaction,
) -> str:
    owner = hashlib.sha256()
    _root_hash_update(owner, (
        plan.base_revision, plan.base_publication_digest,
        plan.position_plan_digest, plan.frame_digest,
        plan.future_imu_digest, plan.imu_velocity_plan.digest,
    ))
    return owner.hexdigest()


def _root_causal_state_digest(plan: PreparedRootCausalState) -> str:
    owner = hashlib.sha256()
    _root_hash_update(owner, (
        plan.base_revision, plan.base_publication_digest,
        plan.reference_time_s, plan.availability_time_s, plan.state,
        plan.base_owner_digest, plan.tail_edge_digest,
    ))
    return owner.hexdigest()


def _root_edge_digest(edge: _IncomingEdge | None) -> str | None:
    if edge is None:
        return None
    owner = hashlib.sha256()
    _root_hash_update(owner, edge)
    return owner.hexdigest()


def _root_current_constraint_plan_digest(
    plan: PreparedRootCurrentConstraint,
) -> str:
    owner = hashlib.sha256()
    _root_hash_update(owner, (
        plan.base_revision, plan.base_publication_digest, plan.updated,
        plan.operator, plan.owner, plan.candidate_owner_digest,
    ))
    return owner.hexdigest()


def _root_position_velocity_plan_digest(
    plan: PreparedRootPositionVelocityTransaction,
) -> str:
    owner = hashlib.sha256()
    _root_hash_update(owner, (
        plan.base_revision, plan.base_publication_digest,
        plan.position_plan_digest, plan.velocity_delta_mps,
        plan.maximum_velocity_step_mps, plan.owner,
        plan.base_owner_digest, plan.candidate_owner_digest,
    ))
    return owner.hexdigest()


def _root_position_rejection_plan_digest(
    plan: PreparedRootPositionRejectionTransaction,
) -> str:
    owner = hashlib.sha256()
    _root_hash_update(owner, (
        plan.base_revision, plan.base_publication_digest,
        plan.position_plan_digest, plan.reason,
        plan.base_owner_digest, plan.candidate_owner_digest,
    ))
    return owner.hexdigest()


def _root_plan_digest(plan: _PreparedRootPositionPlan) -> str:
    """Typed canonical equivalent of the generic owner digest.

    This hot path deliberately emits the exact byte grammar used by
    ``_root_hash_update`` while avoiding per-value dynamic type dispatch.
    """
    owner=hashlib.sha256()
    def sequence(count): owner.update(b"T"+struct.pack("!I",count))
    def integer(value): owner.update(b"I"+struct.pack("!q",value))
    def boolean(value): owner.update(b"B1" if value else b"B0")
    def floating(value): owner.update(b"F"+struct.pack("!d",value))
    def optional_float(value): owner.update(b"N") if value is None else floating(value)
    def text(value):
        raw=value.encode(); owner.update(b"S"+struct.pack("!I",len(raw))+raw)
    def optional_text(value): owner.update(b"N") if value is None else text(value)
    def array(value):
        owner.update(b"A"+value.dtype.str.encode()+struct.pack("!I",value.ndim)
            +struct.pack("!"+"q"*value.ndim,*value.shape)+value.tobytes())
    def state(value):
        sequence(3); floating(value.time_s); array(value.vector); array(value.covariance)
    def edge(value):
        if value is None: owner.update(b"N"); return
        sequence(9); floating(value.start_time_s); floating(value.end_time_s)
        array(value.force_sensor_mps2); array(value.rotation_world_from_sensor)
        boolean(value.inertial); floating(value.acceleration_noise_variance)
        array(value.endpoint_noise_covariance); array(value.full_edge_process_noise_covariance)
        text(value.input_owner)
    def snapshot(value):
        sequence(3); state(value.state); integer(value.applied_constraint_cursor); edge(value.incoming_edge)
    def observation(value):
        sequence(10); floating(value.measurement_time_s); floating(value.availability_time_s)
        array(value.root_position_m); array(value.covariance_m2); text(value.tag_id)
        sequence(len(value.anchors))
        for anchor in value.anchors: integer(anchor)
        text(value.quality_state); boolean(value.frame_valid); boolean(value.physical_point_valid)
        integer(value.source_sequence)
    def decision(value):
        sequence(9); boolean(value.accepted); text(value.reason); optional_float(value.nis)
        array(value.innovation_m); array(value.applied_position_delta_m); floating(value.influence_scale)
        array(value.availability_applied_position_delta_m)
        array(value.availability_applied_velocity_delta_mps)
        floating(value.availability_influence_scale)

    sequence(16); integer(plan.base_revision); observation(plan.observation)
    floating(plan.processing_time_s); decision(plan.decision); state(plan.imu_prediction)
    state(plan.measurement_candidate); sequence(len(plan.snapshots))
    for value in plan.snapshots: snapshot(value)
    sequence(len(plan.health))
    for key,accepted,rejected,consecutive,last_reason in plan.health:
        sequence(5); text(key); integer(accepted); integer(rejected); integer(consecutive); optional_text(last_reason)
    sequence(len(plan.anchor_health))
    for key,accepted,rejected,consecutive,last_reason in plan.anchor_health:
        sequence(5); integer(key); integer(accepted); integer(rejected); integer(consecutive); optional_text(last_reason)
    integer(plan.recovery_good); text(plan.mode.value); floating(plan.last_observation_measurement_s)
    optional_float(plan.last_accepted_measurement_s); optional_float(plan.last_accepted_availability_s)
    floating(plan.last_availability_s); optional_text(plan.causal_state_digest)
    return owner.hexdigest()


def _readonly_root_state(state: RootState) -> RootState:
    vector=state.vector.copy(); covariance=state.covariance.copy()
    vector.setflags(write=False); covariance.setflags(write=False)
    return RootState(state.time_s,vector,covariance)


def _readonly_imu_sample(sample: ImuSample) -> ImuSample:
    force=np.asarray(sample.specific_force_sensor_mps2,float).copy()
    rotation=np.asarray(sample.rotation_world_from_sensor,float).copy()
    force.setflags(write=False); rotation.setflags(write=False)
    return ImuSample(sample.measurement_time_s,sample.availability_time_s,
        force,rotation,sample.source_sequence,sample.m1_valid,sample.m1_reset)


def _readonly_decision(decision: UpdateDecision) -> UpdateDecision:
    arrays=[]
    for value in (decision.innovation_m,decision.applied_position_delta_m,
                  decision.availability_applied_position_delta_m,
                  decision.availability_applied_velocity_delta_mps):
        copied=np.asarray(value,float).copy(); copied.setflags(write=False); arrays.append(copied)
    return replace(decision,innovation_m=arrays[0],applied_position_delta_m=arrays[1],
        availability_applied_position_delta_m=arrays[2],availability_applied_velocity_delta_mps=arrays[3])


def _regularize(covariance: np.ndarray, floor: float) -> np.ndarray:
    covariance = 0.5 * (np.asarray(covariance, float) + np.asarray(covariance, float).T)
    eigenvalues = np.linalg.eigvalsh(covariance)
    if eigenvalues[0] < floor:
        covariance = covariance + np.eye(covariance.shape[0]) * (floor - eigenvalues[0])
    np.linalg.cholesky(covariance)
    return covariance


def _acceleration_process_noise(
    dt: float, variance: float
) -> np.ndarray:
    """Exact white-acceleration Q for one constant-input edge piece."""

    q = np.zeros((9, 9))
    if dt <= 0.0 or variance == 0.0:
        return q
    q[:3, :3] = np.eye(3) * variance * dt**3 / 3.0
    q[:3, 3:6] = q[3:6, :3] = (
        np.eye(3) * variance * dt**2 / 2.0
    )
    q[3:6, 3:6] = np.eye(3) * variance * dt
    return q


def _make_incoming_edge(
    *,
    start_time_s: float,
    end_time_s: float,
    force_sensor_mps2: np.ndarray,
    rotation_world_from_sensor: np.ndarray,
    inertial: bool,
    config: RootFilterConfig,
    input_owner: str,
) -> _IncomingEdge:
    dt = float(end_time_s) - float(start_time_s)
    acceleration_variance = (
        config.inertial_acceleration_noise_mps2_sqrt_hz ** 2
        if inertial else config.cv_acceleration_noise_mps2_sqrt_hz ** 2
    )
    endpoint_noise = np.zeros((9, 9))
    endpoint_noise[6:9, 6:9] = np.eye(3) * (
        config.accelerometer_bias_rw_mps3_sqrt_hz ** 2 * dt
        if inertial else config.covariance_floor
    )
    full_noise = (
        _acceleration_process_noise(dt, acceleration_variance)
        + endpoint_noise
    )
    return _IncomingEdge(
        float(start_time_s),
        float(end_time_s),
        force_sensor_mps2,
        rotation_world_from_sensor,
        bool(inertial),
        float(acceleration_variance),
        endpoint_noise,
        full_noise,
        input_owner,
    )


def _propagate_edge_piece(
    state: RootState,
    target_time_s: float,
    edge: _IncomingEdge,
    config: RootFilterConfig,
) -> tuple[RootState, np.ndarray]:
    """Propagate one piece while preserving the full edge's noise ownership."""

    target = float(target_time_s)
    if (
        state.time_s < edge.start_time_s - 1e-12
        or state.time_s > edge.end_time_s + 1e-12
        or target < state.time_s - 1e-12
        or target > edge.end_time_s + 1e-12
    ):
        raise ValueError("edge-piece propagation lies outside its owner")
    dt = target - float(state.time_s)
    if dt <= 1e-12:
        return RootState(
            target, state.vector.copy(), state.covariance.copy()
        ), np.eye(9)

    x = state.vector.copy()
    phi = np.eye(9)
    if edge.inertial:
        rotation = edge.rotation_world_from_sensor
        acceleration = (
            rotation @ (edge.force_sensor_mps2 - x[6:9])
            + GRAVITY_WORLD_MPS2
        )
        x[:3] += x[3:6] * dt + 0.5 * acceleration * dt * dt
        x[3:6] += acceleration * dt
        phi[:3, 3:6] = np.eye(3) * dt
        phi[:3, 6:9] = -0.5 * rotation * dt * dt
        phi[3:6, 6:9] = -rotation * dt
    else:
        x[:3] += x[3:6] * dt
        phi[:3, 3:6] = np.eye(3) * dt

    is_full_piece = (
        abs(state.time_s - edge.start_time_s) <= 1e-12
        and abs(target - edge.end_time_s) <= 1e-12
    )
    reaches_original_endpoint = abs(target - edge.end_time_s) <= 1e-12
    process_noise = (
        edge.full_edge_process_noise_covariance
        if is_full_piece
        else _acceleration_process_noise(
            dt, edge.acceleration_noise_variance
        ) + (
            edge.endpoint_noise_covariance
            if reaches_original_endpoint else np.zeros((9, 9))
        )
    )
    covariance = _regularize(
        phi @ state.covariance @ phi.T + process_noise,
        config.covariance_floor,
    )
    return RootState(target, x, covariance), phi


def propagate_inertial(
    state: RootState,
    target_time_s: float,
    specific_force_sensor_mps2: np.ndarray,
    rotation_world_from_sensor: np.ndarray,
    config: RootFilterConfig,
) -> tuple[RootState, np.ndarray]:
    """Propagate the affine 9-state root model and return its transition matrix."""

    dt = float(target_time_s) - float(state.time_s)
    if dt < -1e-12:
        raise ValueError("inertial propagation reversed time")
    if dt <= 1e-12:
        return RootState(float(target_time_s), state.vector.copy(), state.covariance.copy()), np.eye(9)
    force = np.asarray(specific_force_sensor_mps2, float)
    rotation = np.asarray(rotation_world_from_sensor, float)
    x = state.vector.copy()
    acceleration = rotation @ (force - x[6:9]) + GRAVITY_WORLD_MPS2
    x[:3] += x[3:6] * dt + 0.5 * acceleration * dt * dt
    x[3:6] += acceleration * dt

    phi = np.eye(9)
    phi[:3, 3:6] = np.eye(3) * dt
    phi[:3, 6:9] = -0.5 * rotation * dt * dt
    phi[3:6, 6:9] = -rotation * dt

    sigma_a2 = config.inertial_acceleration_noise_mps2_sqrt_hz ** 2
    sigma_b2 = config.accelerometer_bias_rw_mps3_sqrt_hz ** 2
    q = np.zeros((9, 9))
    q[:3, :3] = np.eye(3) * sigma_a2 * dt**3 / 3.0
    q[:3, 3:6] = q[3:6, :3] = np.eye(3) * sigma_a2 * dt**2 / 2.0
    q[3:6, 3:6] = np.eye(3) * sigma_a2 * dt
    q[6:9, 6:9] = np.eye(3) * sigma_b2 * dt
    covariance = _regularize(phi @ state.covariance @ phi.T + q, config.covariance_floor)
    return RootState(float(target_time_s), x, covariance), phi


def propagate_constant_velocity(
    state: RootState,
    target_time_s: float,
    config: RootFilterConfig,
) -> tuple[RootState, np.ndarray]:
    dt = float(target_time_s) - float(state.time_s)
    if dt < -1e-12:
        raise ValueError("constant-velocity propagation reversed time")
    x = state.vector.copy()
    x[:3] += x[3:6] * max(dt, 0.0)
    phi = np.eye(9); phi[:3, 3:6] = np.eye(3) * max(dt, 0.0)
    sigma2 = config.cv_acceleration_noise_mps2_sqrt_hz ** 2
    q = np.zeros((9, 9))
    if dt > 0:
        q[:3, :3] = np.eye(3) * sigma2 * dt**3 / 3.0
        q[:3, 3:6] = q[3:6, :3] = np.eye(3) * sigma2 * dt**2 / 2.0
        q[3:6, 3:6] = np.eye(3) * sigma2 * dt
        q[6:9, 6:9] = np.eye(3) * config.covariance_floor
    covariance = _regularize(phi @ state.covariance @ phi.T + q, config.covariance_floor)
    return RootState(float(target_time_s), x, covariance), phi


def update_position(
    state: RootState,
    observation: PositionObservation,
    config: RootFilterConfig,
    *,
    influence_multiplier: float = 1.0,
    state_update_indices: tuple[int, ...] = tuple(range(9)),
) -> tuple[RootState, UpdateDecision]:
    """Predict, gate, bound influence, then apply a Joseph covariance update."""

    updated, decision, _effective_gain = _update_position_details(
        state,
        observation,
        config,
        influence_multiplier=influence_multiplier,
        state_update_indices=state_update_indices,
    )
    return updated, decision


def _apply_position_gain(
    state: RootState,
    observation_covariance_m2: np.ndarray,
    innovation_m: np.ndarray,
    effective_gain: np.ndarray,
    config: RootFilterConfig,
) -> RootState:
    """Apply one already-qualified gain to both the mean and covariance."""

    h = np.zeros((3, 9)); h[:, :3] = np.eye(3)
    vector = state.vector + effective_gain @ innovation_m
    identity = np.eye(9); kh = effective_gain @ h
    covariance = (
        (identity - kh) @ state.covariance @ (identity - kh).T
        + effective_gain @ observation_covariance_m2 @ effective_gain.T
    )
    covariance = _regularize(covariance, config.covariance_floor)
    return RootState(state.time_s, vector, covariance)


def _update_position_details(
    state: RootState,
    observation: PositionObservation,
    config: RootFilterConfig,
    *,
    influence_multiplier: float = 1.0,
    state_update_indices: tuple[int, ...] = tuple(range(9)),
) -> tuple[RootState, UpdateDecision, np.ndarray | None]:
    """Return the public update result plus its authoritative effective gain."""

    observation.validate()
    innovation = np.asarray(observation.root_position_m, float) - state.position_m
    zero = np.zeros(3)
    if not observation.frame_valid:
        return state, UpdateDecision(False, "REJECT_FRAME_UNQUALIFIED", None, innovation, zero, 0.0), None
    if not observation.physical_point_valid:
        return state, UpdateDecision(False, "REJECT_PHYSICAL_POINT_INVALID", None, innovation, zero, 0.0), None
    if observation.quality_state.startswith("REJECT"):
        return state, UpdateDecision(False, observation.quality_state, None, innovation, zero, 0.0), None
    h = np.zeros((3, 9)); h[:, :3] = np.eye(3)
    r = np.asarray(observation.covariance_m2, float)
    s = h @ state.covariance @ h.T + r
    nis = float(innovation @ np.linalg.solve(s, innovation))
    if not math.isfinite(nis) or nis > config.nis_limit_3d:
        return state, UpdateDecision(False, "REJECT_NIS", nis, innovation, zero, 0.0), None
    indices = tuple(int(index) for index in state_update_indices)
    if (
        not indices or len(set(indices)) != len(indices)
        or any(index < 0 or index >= 9 for index in indices)
    ):
        raise ValueError("position-update state indices must be unique 0..8")
    gain = np.linalg.solve(s, h @ state.covariance).T
    inactive = sorted(set(range(9)) - set(indices))
    if inactive:
        gain[inactive, :] = 0.0
    delta = gain @ innovation
    cap = max(0.0, config.maximum_position_influence_m * float(influence_multiplier))
    position_norm = float(np.linalg.norm(delta[:3]))
    scale = 1.0 if position_norm <= cap or position_norm == 0.0 else cap / position_norm
    effective_gain = gain * scale
    applied = effective_gain @ innovation
    updated = _apply_position_gain(
        state, r, innovation, effective_gain, config
    )
    return (
        updated,
        UpdateDecision(True, "ACCEPTED", nis, innovation, applied[:3], scale),
        effective_gain,
    )


class CausalDelayedRootFilter:
    """Delayed-state filter with immutable output records.

    UWB measurement times must be nondecreasing in availability order.  This is
    verified for C1 and asserted here.  Accepted delayed updates insert an
    internal virtual snapshot and replay only already-available IMU samples
    and authoritative current-time root constraints. Previously returned
    ``RootOutput`` objects are never retained or mutated.
    """

    def __init__(self, initial: RootState, config: RootFilterConfig = RootFilterConfig(), *,
                 inertial: bool = True):
        self.config = config
        self.inertial = bool(inertial)
        neutral_force = np.array([0.0, 0.0, 9.80665])
        self._snapshots: list[_Snapshot] = [
            _Snapshot(initial, 0)
        ]
        self._constraint_events: list[_AuthoritativeRootEvent] = []
        self._next_constraint_sequence = 1
        self._last_availability_s = -math.inf
        self._last_observation_measurement_s = -math.inf
        self._last_accepted_measurement_s: float | None = None
        self._last_accepted_availability_s: float | None = None
        self._last_force = neutral_force
        self._last_rotation = np.eye(3)
        self._following_input_mode = (
            RootTranslationEdgeMode.INERTIAL
            if self.inertial else RootTranslationEdgeMode.CV_NO_ACCELERATION
        )
        self._last_source_gap_owner: str | None = None
        self._terminal_missing_owner: str | None = None
        self._health: dict[str, _Health] = {}
        self._anchor_health: dict[int, _Health] = {anchor: _Health() for anchor in range(8)}
        self._recovery_good = 0
        self._mode = SystemMode.INITIALIZING
        self._emission_times: list[float] = []
        self.future_imu_count = 0
        self.future_uwb_count = 0
        self.preavailability_output_count = 0
        self.late_imu_rejected = 0
        self.__transaction_authority = object()
        self.__consumed_imu_plans: set[str] = set()
        self.__consumed_imu_velocity_plans: set[str] = set()
        self.__consumed_position_velocity_plans: set[str] = set()
        self.__consumed_position_rejection_plans: set[str] = set()
        self._publication_revision = 0

    @property
    def current_state(self) -> RootState:
        return self._snapshots[-1].state

    @property
    def mode(self) -> SystemMode:
        return self._mode

    def bind_pristine_following_input_mode(
        self, edge_mode: RootTranslationEdgeMode,
    ) -> None:
        """Bind the first causal input mode before this owner consumes an event."""

        if type(edge_mode) is not RootTranslationEdgeMode:
            raise TypeError("initial root input mode must be authoritative")
        if edge_mode is RootTranslationEdgeMode.INERTIAL and not self.inertial:
            raise ValueError("CV root filter cannot own an inertial initial input")
        if (
            self._publication_revision != 0
            or len(self._snapshots) != 1
            or self._snapshots[0].incoming_edge is not None
            or self._constraint_events
            or self._emission_times
            or self._last_availability_s != -math.inf
        ):
            raise RuntimeError("root initial input mode is no longer bindable")
        self._following_input_mode = edge_mode
        self._publication_revision += 1

    def _bind_terminal_missing_following_mode(
        self,
        expected_publication: RootPublicationToken,
        *,
        availability_time_s: float,
        missing_owner_digest: str,
    ) -> None:
        """One-shot causal switch for an authenticated terminal MISSING owner.

        This is intentionally not a general mode setter.  It preserves every
        committed snapshot and constraint and affects only edges beginning at
        the exact current terminal epoch.
        """
        self._validate_publication_token(expected_publication)
        if (
            not math.isfinite(float(availability_time_s))
            or abs(float(availability_time_s) - self.current_state.time_s) > 1e-12
            or not isinstance(missing_owner_digest, str)
            or len(missing_owner_digest) != 64
            or any(character not in "0123456789abcdef" for character in missing_owner_digest)
            or self._terminal_missing_owner is not None
        ):
            raise RuntimeError("TERMINAL_MISSING_ROOT_BIND_REJECTED")
        self._following_input_mode = RootTranslationEdgeMode.CV_NO_ACCELERATION
        self._terminal_missing_owner = missing_owner_digest
        self._publication_revision += 1

    def publication_token(self) -> RootPublicationToken:
        state = self.current_state
        frozen=_readonly_root_state(state); owner=hashlib.sha256()
        _root_hash_update(owner,(
            self._publication_revision,float(state.time_s),frozen,
            self._last_availability_s,
            self._last_force,self._last_rotation,self._following_input_mode,
            self._last_source_gap_owner,self._terminal_missing_owner,
        ))
        return RootPublicationToken(self.__transaction_authority,self._publication_revision,
            float(state.time_s),frozen,owner.hexdigest())

    def committed_state_at(self, query_time_s: float) -> CommittedRootStateToken:
        """Return the immutable committed state at an owned history epoch."""

        query = float(query_time_s)
        if not math.isfinite(query) or query > self.current_state.time_s + 1e-12:
            raise ValueError("committed-state query is non-finite or in the future")
        located = self._state_at(query)
        if located is None:
            raise ValueError("committed-state query is outside fixed-lag history")
        _index, state, _cursor, _edge = located
        frozen = _readonly_root_state(state)
        owner = hashlib.sha256()
        _root_hash_update(owner, (
            "COMMITTED_DELAYED_ROOT_STATE_V1",
            self._publication_revision,
            query,
            frozen,
        ))
        return CommittedRootStateToken(
            self.__transaction_authority,
            self._publication_revision,
            query,
            frozen,
            owner.hexdigest(),
        )

    def prepare_causal_state(
        self, reference_time_s: float, availability_time_s: float,
    ) -> PreparedRootCausalState:
        """Own a read-only state at any reference already causal by availability.

        A reference may be newer than the latest committed IMU, but never newer
        than the event availability.  In that case the existing following input
        owns the short tail prediction; no snapshot or watermark is committed.
        """

        reference = float(reference_time_s)
        availability = float(availability_time_s)
        if (
            not math.isfinite(reference) or not math.isfinite(availability)
            or reference > availability + 1e-12
            or availability + 1e-12 < self.current_state.time_s
            or availability + 1e-12 < self._last_availability_s
        ):
            raise ValueError("causal-state reference/availability is invalid")
        tail_edge = self._make_edge(
            start_time_s=self.current_state.time_s,
            end_time_s=availability,
            force=self._last_force,
            rotation=self._last_rotation,
            input_owner="EPHEMERAL_AVAILABILITY_HELD_INPUT",
            edge_mode=self._following_input_mode,
        ) if availability > self.current_state.time_s + 1e-12 else None
        located = self._state_at(reference, tail_edge=tail_edge)
        if located is None:
            raise ValueError("causal-state reference is outside fixed-lag history")
        state = _readonly_root_state(located[1])
        base = self.publication_token()
        bundle = self._prepare_position_rollback()
        base_digest = _root_imu_bundle_digest(bundle)
        edge_digest = _root_edge_digest(tail_edge)
        blank = PreparedRootCausalState(
            self.__transaction_authority, base.revision, base.digest,
            reference, availability, state, base_digest, edge_digest, "",
            deepcopy(bundle), tail_edge,
        )
        return replace(blank, digest=_root_causal_state_digest(blank))

    def prepare_position_from_causal_state(
        self,
        causal_state: PreparedRootCausalState,
        observation: PositionObservation,
        *,
        state_update_indices: tuple[int, ...] = tuple(range(9)),
    ) -> _PreparedRootPositionPlan:
        """Cross-bind one delayed position plan to its root-owned source state."""

        if type(causal_state) is not PreparedRootCausalState:
            raise RuntimeError("STALE_FORGED_REPLAYED_OR_FOREIGN_CAUSAL_STATE")
        expected_tail = self._make_edge(
            start_time_s=self.current_state.time_s,
            end_time_s=causal_state.availability_time_s,
            force=self._last_force,
            rotation=self._last_rotation,
            input_owner="EPHEMERAL_AVAILABILITY_HELD_INPUT",
            edge_mode=self._following_input_mode,
        ) if causal_state.availability_time_s > self.current_state.time_s + 1e-12 else None
        if (
            causal_state.authority is not self.__transaction_authority
            or causal_state.base_revision != self._publication_revision
            or causal_state.base_publication_digest != self.publication_token().digest
            or not hmac.compare_digest(
                causal_state.base_owner_digest,
                _root_imu_bundle_digest(causal_state._base_bundle),
            )
            or not hmac.compare_digest(
                causal_state.base_owner_digest,
                _root_imu_bundle_digest(self._prepare_position_rollback()),
            )
            or causal_state.tail_edge_digest != _root_edge_digest(causal_state._tail_edge)
            or causal_state.tail_edge_digest != _root_edge_digest(expected_tail)
            or not hmac.compare_digest(
                causal_state.digest, _root_causal_state_digest(causal_state),
            )
            or observation.measurement_time_s != causal_state.reference_time_s
            or observation.availability_time_s != causal_state.availability_time_s
        ):
            raise RuntimeError("STALE_FORGED_REPLAYED_OR_FOREIGN_CAUSAL_STATE")
        located = self._state_at(
            causal_state.reference_time_s, tail_edge=expected_tail,
        )
        if (
            located is None
            or located[1].time_s != causal_state.state.time_s
            or located[1].vector.tobytes() != causal_state.state.vector.tobytes()
            or located[1].covariance.tobytes() != causal_state.state.covariance.tobytes()
        ):
            raise RuntimeError("CAUSAL_STATE_BASELINE_CHANGED")
        plan = self.prepare_position(
            observation, state_update_indices=state_update_indices,
        )
        plan = replace(
            plan, causal_state_digest=causal_state.digest, digest="",
        )
        plan = replace(plan, digest=_root_plan_digest(plan))
        if (
            plan.authority is not causal_state.authority
            or plan.base_revision != causal_state.base_revision
            or plan.observation.measurement_time_s != causal_state.reference_time_s
            or plan.processing_time_s != causal_state.availability_time_s
            or plan.causal_state_digest != causal_state.digest
        ):
            raise RuntimeError("CAUSAL_STATE_POSITION_PLAN_CROSS_BIND_FAILED")
        return plan

    def _validate_publication_token(self, token: RootPublicationToken) -> None:
        current=self.publication_token()
        if (not isinstance(token,RootPublicationToken) or token.authority is not self.__transaction_authority
                or token.revision!=self._publication_revision
                or not hmac.compare_digest(token.digest,current.digest)):
            raise RuntimeError("STALE_ROOT_PUBLICATION_TOKEN")

    def apply_current_constraint(
        self,
        updated: RootState,
        *,
        operator: RootConstraintOperator,
        owner: str,
    ) -> None:
        """Install a causal constraint evaluated at the latest state epoch.

        This narrow hook is for independent current-time information channels
        such as a contact episode.  Delayed measurements must continue to use
        :meth:`add_position`, which owns rewind and replay.
        """

        if abs(float(updated.time_s) - float(self.current_state.time_s)) > 1e-12:
            raise ValueError("current constraint timestamp does not match filter state")
        if not owner:
            raise ValueError("current constraint lacks an event owner")
        if not isinstance(
            operator, (AdditiveRootConstraint, BoundedTargetRootConstraint)
        ):
            raise TypeError("unsupported current root constraint operator")
        if (
            updated.covariance.tobytes()
            != self.current_state.covariance.tobytes()
        ):
            raise ValueError("current root constraint cannot mutate covariance")
        evaluated = operator.apply(self.current_state)
        if evaluated.vector.tobytes() != updated.vector.tobytes():
            raise ValueError(
                "current root constraint operator does not reproduce live update"
            )
        sequence = self._next_constraint_sequence
        self._next_constraint_sequence += 1
        self._constraint_events.append(_AuthoritativeRootEvent(
            sequence,
            float(updated.time_s),
            str(owner),
            operator,
        ))
        self._snapshots[-1] = replace(
            self._snapshots[-1], state=RootState(
                updated.time_s, updated.vector.copy(), updated.covariance.copy()
            ), applied_constraint_cursor=sequence
        )
        self._publication_revision += 1

    def prepare_current_constraint(
        self, updated: RootState, *, operator: AdditiveRootConstraint,
        owner: str,
    ) -> PreparedRootCurrentConstraint:
        """Evaluate one current-time additive constraint and restore exactly."""

        if type(operator) is not AdditiveRootConstraint:
            raise TypeError("prepared current constraint must be additive")
        before = self._prepare_position_rollback()
        base = self.publication_token()
        try:
            self.apply_current_constraint(updated, operator=operator, owner=owner)
            candidate = self._prepare_position_rollback()
        finally:
            self._rollback_prevalidated_position(before)
        candidate_digest = _root_imu_bundle_digest(candidate)
        blank = PreparedRootCurrentConstraint(
            self.__transaction_authority, base.revision, base.digest,
            _readonly_root_state(updated), operator, str(owner),
            candidate_digest, "", deepcopy(candidate),
        )
        return replace(blank, digest=_root_current_constraint_plan_digest(blank))

    def prevalidate_current_constraint(
        self, plan: PreparedRootCurrentConstraint,
    ) -> None:
        if (
            type(plan) is not PreparedRootCurrentConstraint
            or plan.authority is not self.__transaction_authority
            or plan.base_revision != self._publication_revision
            or plan.base_publication_digest != self.publication_token().digest
            or not hmac.compare_digest(plan.digest,
                                       _root_current_constraint_plan_digest(plan))
            or not hmac.compare_digest(plan.candidate_owner_digest,
                                       _root_imu_bundle_digest(plan._candidate_bundle))
        ):
            raise RuntimeError("STALE_FORGED_OR_FOREIGN_CURRENT_CONSTRAINT_PLAN")

    def commit_current_constraint(
        self, plan: PreparedRootCurrentConstraint,
    ) -> None:
        self.prevalidate_current_constraint(plan)
        self._rollback_prevalidated_position(deepcopy(plan._candidate_bundle))

    def prepare_position_velocity_transaction(
        self, position_plan: _PreparedRootPositionPlan, *,
        velocity_delta_mps: np.ndarray, maximum_velocity_step_mps: float,
        owner: str,
    ) -> PreparedRootPositionVelocityTransaction:
        """Compose a root-issued delayed p-only plan and bounded velocity delta."""

        position_bundle = self._prevalidate_position_plan(position_plan)
        zero = np.zeros(3, dtype=float)
        if (
            not position_plan.decision.accepted
            or position_plan.measurement_candidate.vector[3:].tobytes()
            != position_plan.imu_prediction.vector[3:].tobytes()
            or position_plan.decision.availability_applied_velocity_delta_mps.tobytes()
            != zero.tobytes()
        ):
            raise ValueError("compound root transaction requires accepted p-only plan")
        delta = np.asarray(velocity_delta_mps, dtype=float).reshape(3).copy()
        maximum = float(maximum_velocity_step_mps)
        if (not np.isfinite(delta).all() or not math.isfinite(maximum)
                or maximum <= 0.0 or np.linalg.norm(delta) > maximum + 1e-12
                or not owner):
            raise ValueError("compound root velocity delta exceeds its owned bound")
        before = self._prepare_position_rollback()
        base = self.publication_token()
        try:
            self._apply_prevalidated_position(position_bundle)
            current = self.current_state
            vector = current.vector.copy()
            vector[3:6] += delta
            updated = RootState(current.time_s, vector, current.covariance.copy())
            current_plan = self.prepare_current_constraint(
                updated, operator=AdditiveRootConstraint(updated.vector - current.vector),
                owner=owner,
            )
            candidate = deepcopy(current_plan._candidate_bundle)
        finally:
            self._rollback_prevalidated_position(before)
        delta.setflags(write=False)
        candidate_digest = _root_imu_bundle_digest(candidate)
        blank = PreparedRootPositionVelocityTransaction(
            self.__transaction_authority, base.revision, base.digest,
            position_plan.digest, delta, maximum, str(owner),
            _root_imu_bundle_digest(before), candidate_digest, "",
            deepcopy(before), candidate,
        )
        return replace(blank, digest=_root_position_velocity_plan_digest(blank))

    def prepare_position_rejection_transaction(
        self, position_plan: _PreparedRootPositionPlan,
    ) -> PreparedRootPositionRejectionTransaction:
        """Prepare the exact rejected plan's health/time effects only."""

        bundle = self._prevalidate_position_plan(position_plan)
        if position_plan.decision.accepted:
            raise ValueError("rejection transaction requires rejected position plan")
        before = self._prepare_position_rollback()
        numeric_before = (
            self.current_state.vector.tobytes(),
            self.current_state.covariance.tobytes(),
        )
        try:
            self._apply_prevalidated_position(bundle)
            candidate = self._prepare_position_rollback()
            if (
                self.current_state.vector.tobytes(),
                self.current_state.covariance.tobytes(),
            ) != numeric_before:
                raise RuntimeError("rejection transaction mutated root numerics")
        finally:
            self._rollback_prevalidated_position(before)
        blank = PreparedRootPositionRejectionTransaction(
            self.__transaction_authority, self._publication_revision,
            self.publication_token().digest, position_plan.digest,
            position_plan.decision.reason, _root_imu_bundle_digest(before),
            _root_imu_bundle_digest(candidate), "", deepcopy(before),
            deepcopy(candidate),
        )
        return replace(blank, digest=_root_position_rejection_plan_digest(blank))

    def prevalidate_position_rejection_transaction(
        self, position_plan: _PreparedRootPositionPlan,
        plan: PreparedRootPositionRejectionTransaction,
    ) -> None:
        self._prevalidate_position_plan(position_plan)
        if (
            type(plan) is not PreparedRootPositionRejectionTransaction
            or plan.authority is not self.__transaction_authority
            or plan.base_revision != self._publication_revision
            or plan.base_publication_digest != self.publication_token().digest
            or plan.position_plan_digest != position_plan.digest
            or position_plan.decision.accepted
            or plan.reason != position_plan.decision.reason
            or plan.digest in self.__consumed_position_rejection_plans
            or not hmac.compare_digest(
                plan.base_owner_digest, _root_imu_bundle_digest(plan._base_bundle),
            )
            or not hmac.compare_digest(
                plan.base_owner_digest,
                _root_imu_bundle_digest(self._prepare_position_rollback()),
            )
            or not hmac.compare_digest(
                plan.candidate_owner_digest,
                _root_imu_bundle_digest(plan._candidate_bundle),
            )
            or not hmac.compare_digest(
                plan.digest, _root_position_rejection_plan_digest(plan),
            )
        ):
            raise RuntimeError("STALE_FORGED_OR_FOREIGN_POSITION_REJECTION_PLAN")

    def commit_position_rejection_transaction(
        self, position_plan: _PreparedRootPositionPlan,
        plan: PreparedRootPositionRejectionTransaction,
    ) -> UpdateDecision:
        self.prevalidate_position_rejection_transaction(position_plan, plan)
        self._rollback_prevalidated_position(deepcopy(plan._candidate_bundle))
        self.__consumed_position_rejection_plans.add(plan.digest)
        return position_plan.decision

    def rollback_committed_position_rejection_transaction(
        self, plan: PreparedRootPositionRejectionTransaction,
    ) -> None:
        """Restore one committed rejection while leaving its plan consumed."""

        if (
            type(plan) is not PreparedRootPositionRejectionTransaction
            or plan.authority is not self.__transaction_authority
            or plan.digest not in self.__consumed_position_rejection_plans
            or not hmac.compare_digest(
                plan.digest, _root_position_rejection_plan_digest(plan),
            )
            or not hmac.compare_digest(
                plan.candidate_owner_digest,
                _root_imu_bundle_digest(self._prepare_position_rollback()),
            )
            or not hmac.compare_digest(
                plan.base_owner_digest, _root_imu_bundle_digest(plan._base_bundle),
            )
        ):
            raise RuntimeError("FOREIGN_OR_NONCURRENT_POSITION_REJECTION_ROLLBACK")
        self._rollback_prevalidated_position(deepcopy(plan._base_bundle))

    def prevalidate_position_velocity_transaction(
        self, position_plan: _PreparedRootPositionPlan,
        plan: PreparedRootPositionVelocityTransaction,
    ) -> None:
        self._prevalidate_position_plan(position_plan)
        if (
            type(plan) is not PreparedRootPositionVelocityTransaction
            or plan.authority is not self.__transaction_authority
            or plan.base_revision != self._publication_revision
            or plan.base_publication_digest != self.publication_token().digest
            or plan.position_plan_digest != position_plan.digest
            or plan.digest in self.__consumed_position_velocity_plans
            or not hmac.compare_digest(
                plan.base_owner_digest,
                _root_imu_bundle_digest(plan._base_bundle),
            )
            or not hmac.compare_digest(
                plan.base_owner_digest,
                _root_imu_bundle_digest(self._prepare_position_rollback()),
            )
            or not hmac.compare_digest(
                plan.candidate_owner_digest,
                _root_imu_bundle_digest(plan._candidate_bundle),
            )
            or not hmac.compare_digest(
                plan.digest, _root_position_velocity_plan_digest(plan),
            )
        ):
            raise RuntimeError("STALE_FORGED_OR_FOREIGN_POSITION_VELOCITY_PLAN")

    def commit_position_velocity_transaction(
        self, position_plan: _PreparedRootPositionPlan,
        plan: PreparedRootPositionVelocityTransaction,
    ) -> UpdateDecision:
        self.prevalidate_position_velocity_transaction(position_plan, plan)
        self._rollback_prevalidated_position(deepcopy(plan._candidate_bundle))
        self.__consumed_position_velocity_plans.add(plan.digest)
        return position_plan.decision

    def rollback_committed_position_velocity_transaction(
        self, plan: PreparedRootPositionVelocityTransaction,
    ) -> None:
        """Restore one just-committed compound plan without making it replayable."""

        if (
            type(plan) is not PreparedRootPositionVelocityTransaction
            or plan.authority is not self.__transaction_authority
            or plan.digest not in self.__consumed_position_velocity_plans
            or not hmac.compare_digest(
                plan.digest, _root_position_velocity_plan_digest(plan),
            )
            or not hmac.compare_digest(
                plan.candidate_owner_digest,
                _root_imu_bundle_digest(self._prepare_position_rollback()),
            )
            or not hmac.compare_digest(
                plan.base_owner_digest,
                _root_imu_bundle_digest(plan._base_bundle),
            )
        ):
            raise RuntimeError("FOREIGN_OR_NONCURRENT_POSITION_VELOCITY_ROLLBACK")
        self._rollback_prevalidated_position(deepcopy(plan._base_bundle))

    def advance_to_availability(self, availability_time_s: float) -> None:
        """Advance the current inertial state to an asynchronous event time.

        UWB availability can fall between two 200 Hz IMU triggers.  Advancing
        with the last already-known inertial input creates a current-time state
        on which an available pose re-gauge and foothold reconciliation may act
        without writing future information into the preceding IMU snapshot.
        """

        target = float(availability_time_s)
        if not math.isfinite(target) or target + 1e-12 < self.current_state.time_s:
            raise ValueError("availability advance reversed time")
        if target <= self.current_state.time_s + 1e-12:
            self._last_availability_s = max(self._last_availability_s, target)
            return
        edge = self._make_edge(
            start_time_s=self.current_state.time_s,
            end_time_s=target,
            force=self._last_force,
            rotation=self._last_rotation,
            input_owner="HELD_LAST_AVAILABLE_INPUT",
            edge_mode=self._following_input_mode,
        )
        state, _ = _propagate_edge_piece(
            self.current_state, target, edge, self.config
        )
        self._snapshots.append(_Snapshot(
            state,
            self._snapshots[-1].applied_constraint_cursor,
            edge,
        ))
        self._last_availability_s = max(self._last_availability_s, target)
        self._prune()
        self._publication_revision += 1

    def _prepare_no_update_gap(
        self,
        *,
        gap_start_time_s: float,
        gap_end_time_s: float,
        availability_time_s: float,
        post_gap_sample: ImuSample | None = None,
        source_gap_owner: str | None = None,
        following_input_mode: RootTranslationEdgeMode | None = None,
    ) -> _PreparedRootGapPlan:
        """Prepare elapsed-time covariance growth without an invented IMU input."""

        start = float(gap_start_time_s)
        end = float(gap_end_time_s)
        availability = float(availability_time_s)
        if not all(math.isfinite(value) for value in (start, end, availability)):
            raise ValueError("root gap times must be finite")
        if end <= start or availability + 1e-12 < end:
            raise ValueError("root gap requires increasing source and availability times")
        if self.current_state.time_s > start + 1e-12:
            raise ValueError("root state already passed the gap start")
        if availability + 1e-12 < self._last_availability_s:
            raise ValueError("root gap availability reversed time")

        # No endpoint accelerometer sample owns an unobserved interval.  Keep
        # the complete mean state unchanged and add only the existing CV
        # process noise.  The post-gap snapshot deliberately has no incoming
        # edge: delayed replay may not reinterpret the gap as mean motion.
        edge = _make_incoming_edge(
            start_time_s=self.current_state.time_s,
            end_time_s=end,
            force_sensor_mps2=np.zeros(3),
            rotation_world_from_sensor=np.eye(3),
            inertial=False,
            config=self.config,
            input_owner="SOURCE_BOUND_GAP_NO_UPDATE_CONSTANT_VELOCITY",
        )
        state = RootState(
            end,
            self.current_state.vector.copy(),
            _regularize(
                self.current_state.covariance
                + edge.full_edge_process_noise_covariance,
                self.config.covariance_floor,
            ),
        )
        snapshots = tuple(self._snapshots) + (_Snapshot(
            state, self._snapshots[-1].applied_constraint_cursor, None,
        ),)
        supplied = (
            post_gap_sample is not None,
            source_gap_owner is not None,
            following_input_mode is not None,
        )
        if any(supplied) and not all(supplied):
            raise ValueError("post-gap input ownership must be complete")
        frozen_sample = None
        if post_gap_sample is not None:
            post_gap_sample.validate()
            if (
                abs(post_gap_sample.measurement_time_s - end) > 1e-12
                or abs(post_gap_sample.availability_time_s - availability) > 1e-12
                or len(source_gap_owner) != 64
                or any(character not in "0123456789abcdef" for character in source_gap_owner)
                or type(following_input_mode) is not RootTranslationEdgeMode
            ):
                raise ValueError("post-gap input does not bind the gap endpoint")
            frozen_sample = _readonly_imu_sample(post_gap_sample)
        owner = hashlib.sha256()
        _root_hash_update(owner, (
            self._publication_revision, start, end, availability, snapshots,
            frozen_sample, source_gap_owner, following_input_mode,
        ))
        return _PreparedRootGapPlan(
            self.__transaction_authority, self._publication_revision,
            start, end, availability, snapshots, frozen_sample,
            source_gap_owner, following_input_mode, owner.hexdigest(),
        )

    def _apply_prevalidated_no_update_gap(
        self, plan: _PreparedRootGapPlan,
    ) -> None:
        if (
            type(plan) is not _PreparedRootGapPlan
            or plan.authority is not self.__transaction_authority
            or plan.base_revision != self._publication_revision
        ):
            raise RuntimeError("STALE_OR_FOREIGN_ROOT_GAP_PLAN")
        owner = hashlib.sha256()
        _root_hash_update(owner, (
            plan.base_revision, plan.gap_start_time_s, plan.gap_end_time_s,
            plan.availability_time_s, plan.snapshots, plan.post_gap_sample,
            plan.source_gap_owner, plan.following_input_mode,
        ))
        if not hmac.compare_digest(plan.digest, owner.hexdigest()):
            raise RuntimeError("ROOT_GAP_PLAN_DIGEST_MISMATCH")
        self._snapshots = list(plan.snapshots)
        self._last_availability_s = max(
            self._last_availability_s, plan.availability_time_s,
        )
        if plan.post_gap_sample is not None:
            self._last_force = np.asarray(
                plan.post_gap_sample.specific_force_sensor_mps2, float,
            ).copy()
            self._last_rotation = np.asarray(
                plan.post_gap_sample.rotation_world_from_sensor, float,
            ).copy()
            self._following_input_mode = plan.following_input_mode
            self._last_source_gap_owner = plan.source_gap_owner
        self._prune()
        self._publication_revision += 1

    def _propagate(self, state: RootState, target: float, force: np.ndarray, rotation: np.ndarray) -> tuple[RootState, np.ndarray]:
        if self.inertial:
            return propagate_inertial(state, target, force, rotation, self.config)
        return propagate_constant_velocity(state, target, self.config)

    def _make_edge(
        self,
        *,
        start_time_s: float,
        end_time_s: float,
        force: np.ndarray,
        rotation: np.ndarray,
        input_owner: str,
        edge_mode: RootTranslationEdgeMode | None = None,
    ) -> _IncomingEdge:
        mode = edge_mode
        if mode is None:
            mode = (
                RootTranslationEdgeMode.INERTIAL
                if self.inertial else RootTranslationEdgeMode.CV_NO_ACCELERATION
            )
        if type(mode) is not RootTranslationEdgeMode:
            raise TypeError("root translation edge mode must be authoritative")
        if mode is RootTranslationEdgeMode.INERTIAL and not self.inertial:
            raise ValueError("CV root filter cannot admit an inertial edge")
        return _make_incoming_edge(
            start_time_s=start_time_s,
            end_time_s=end_time_s,
            force_sensor_mps2=force,
            rotation_world_from_sensor=rotation,
            inertial=mode is RootTranslationEdgeMode.INERTIAL,
            config=self.config,
            input_owner=input_owner,
        )

    def prepare_imu_transaction(
        self, sample: ImuSample, *,
        edge_mode: RootTranslationEdgeMode | None = None,
        following_input_mode: RootTranslationEdgeMode | None = None,
    ) -> PreparedRootImuTransaction:
        """Evaluate one legacy IMU transition and restore this owner exactly."""
        incoming = edge_mode if edge_mode is not None else (
            RootTranslationEdgeMode.INERTIAL if self.inertial
            else RootTranslationEdgeMode.CV_NO_ACCELERATION
        )
        following = following_input_mode if following_input_mode is not None else incoming
        if type(incoming) is not RootTranslationEdgeMode or type(following) is not RootTranslationEdgeMode:
            raise TypeError("prepared IMU modes must be authoritative")
        frozen_sample = _readonly_imu_sample(sample)
        before = self._prepare_position_rollback()
        base = self.publication_token()
        try:
            accepted = self.add_imu(
                frozen_sample, edge_mode=incoming,
                following_input_mode=following,
            )
            candidate = self._prepare_position_rollback()
            candidate_token = self.publication_token()
        finally:
            self._rollback_prevalidated_position(before)
        candidate_digest = _root_imu_bundle_digest(candidate)
        blank = PreparedRootImuTransaction(
            self.__transaction_authority, base.revision, base.digest,
            frozen_sample, incoming, following, accepted,
            "ACCEPTED" if accepted else "REJECTED_LEGACY_ADD_IMU",
            _readonly_root_state(candidate_token.state), candidate_token.revision,
            len(candidate.snapshots), _root_imu_bundle_digest(before),
            candidate_digest, "", deepcopy(before), deepcopy(candidate),
        )
        return replace(blank, digest=_root_imu_plan_digest(blank))

    def prevalidate_prepared_imu(self, plan: PreparedRootImuTransaction) -> None:
        """Prove commit readiness without mutating the root owner."""
        if (
            type(plan) is not PreparedRootImuTransaction
            or plan.authority is not self.__transaction_authority
            or plan.digest in self.__consumed_imu_plans
            or plan.base_revision != self._publication_revision
            or plan.base_publication_digest != self.publication_token().digest
            or not hmac.compare_digest(
                plan.base_owner_digest,
                _root_imu_bundle_digest(plan._base_bundle),
            )
            or not hmac.compare_digest(
                plan.base_owner_digest,
                _root_imu_bundle_digest(self._prepare_position_rollback()),
            )
            or not hmac.compare_digest(plan.digest, _root_imu_plan_digest(plan))
            or not hmac.compare_digest(
                plan.candidate_owner_digest,
                _root_imu_bundle_digest(plan._candidate_bundle),
            )
            or plan.candidate_revision != plan._candidate_bundle.publication_revision
            or plan.candidate_snapshot_count != len(plan._candidate_bundle.snapshots)
        ):
            raise RuntimeError("STALE_FORGED_OR_FOREIGN_ROOT_IMU_PLAN")

    def commit_prepared_imu(self, plan: PreparedRootImuTransaction) -> bool:
        """Commit an already evaluated transition, including legacy rejection effects."""
        self.prevalidate_prepared_imu(plan)
        self._rollback_prevalidated_position(deepcopy(plan._candidate_bundle))
        self.__consumed_imu_plans.add(plan.digest)
        return plan.accepted

    def rollback_committed_prepared_imu(
        self, plan: PreparedRootImuTransaction,
    ) -> None:
        """Restore one just-committed IMU plan without making it replayable."""

        if (
            type(plan) is not PreparedRootImuTransaction
            or plan.authority is not self.__transaction_authority
            or plan.digest not in self.__consumed_imu_plans
            or not hmac.compare_digest(plan.digest, _root_imu_plan_digest(plan))
            or not hmac.compare_digest(
                plan.candidate_owner_digest,
                _root_imu_bundle_digest(self._prepare_position_rollback()),
            )
            or not hmac.compare_digest(
                plan.base_owner_digest,
                _root_imu_bundle_digest(plan._base_bundle),
            )
        ):
            raise RuntimeError("FOREIGN_OR_NONCURRENT_ROOT_IMU_ROLLBACK")
        self._rollback_prevalidated_position(deepcopy(plan._base_bundle))

    @staticmethod
    def _validate_future_frame_digest(frame_digest: str) -> None:
        if (
            not isinstance(frame_digest, str) or len(frame_digest) != 64
            or any(character not in "0123456789abcdef" for character in frame_digest)
        ):
            raise ValueError("invalid future native200 frame digest")

    def prepare_imu_after_position_transaction(
        self, position_plan: _PreparedRootPositionPlan, *,
        sample: ImuSample, frame_digest: str,
    ) -> PreparedRootFutureImuTransaction:
        """Prepare an IMU against an accepted future p-only publication.

        The accepted position candidate is installed only inside this root
        owner, without consuming it, and the complete live bundle is restored
        in ``finally``.  Consequently the original position plan remains the
        sole authority for the real admission commit.
        """

        position_bundle = self._prevalidate_position_plan(position_plan)
        zero = np.zeros(3, dtype=float)
        if (
            not position_plan.decision.accepted
            or position_plan.measurement_candidate.vector[3:].tobytes()
            != position_plan.imu_prediction.vector[3:].tobytes()
            or position_plan.decision.availability_applied_velocity_delta_mps.tobytes()
            != zero.tobytes()
        ):
            raise ValueError("future IMU requires an accepted p-only position plan")
        self._validate_future_frame_digest(frame_digest)
        frozen_sample = _readonly_imu_sample(sample)
        before = self._prepare_position_rollback()
        base = self.publication_token()
        before_digest = _root_imu_bundle_digest(before)
        try:
            self._apply_prevalidated_position(position_bundle)
            future_base_digest = _root_imu_bundle_digest(
                self._prepare_position_rollback()
            )
            imu_plan = self.prepare_imu_transaction(frozen_sample)
            if not imu_plan.accepted:
                raise ValueError("future position publication rejects bound IMU")
        finally:
            self._rollback_prevalidated_position(before)
        if _root_imu_bundle_digest(self._prepare_position_rollback()) != before_digest:
            raise RuntimeError("future IMU preparation changed live root owner")
        blank = PreparedRootFutureImuTransaction(
            self.__transaction_authority, base.revision, base.digest,
            position_plan.digest, str(frame_digest), imu_plan,
            future_base_digest, "",
        )
        return replace(blank, digest=_root_future_imu_plan_digest(blank))

    def prevalidate_imu_after_position_transaction(
        self, position_plan: _PreparedRootPositionPlan,
        plan: PreparedRootFutureImuTransaction,
    ) -> None:
        if (
            type(plan) is not PreparedRootFutureImuTransaction
            or plan.authority is not self.__transaction_authority
            or plan.position_plan_digest != position_plan.digest
            or not hmac.compare_digest(
                position_plan.digest, _root_plan_digest(position_plan)
            )
            or plan.imu_plan.sample.measurement_time_s
            != plan.imu_plan.candidate_state.time_s
            or not hmac.compare_digest(
                plan.future_base_owner_digest,
                plan.imu_plan.base_owner_digest,
            )
            or not hmac.compare_digest(
                plan.digest, _root_future_imu_plan_digest(plan)
            )
        ):
            raise RuntimeError("FORGED_OR_FOREIGN_FUTURE_ROOT_IMU_PLAN")
        self.prevalidate_prepared_imu(plan.imu_plan)

    def commit_imu_after_position_transaction(
        self, position_plan: _PreparedRootPositionPlan,
        plan: PreparedRootFutureImuTransaction,
    ) -> bool:
        self.prevalidate_imu_after_position_transaction(position_plan, plan)
        return self.commit_prepared_imu(plan.imu_plan)

    def prepare_imu_velocity_after_position_transaction(
        self, position_plan: _PreparedRootPositionPlan,
        future_imu: PreparedRootFutureImuTransaction, *,
        velocity_delta_mps: np.ndarray, maximum_velocity_step_mps: float,
        owner: str,
    ) -> PreparedRootFutureImuVelocityTransaction:
        """Compound B0 against the exact future p-only root revision."""

        position_bundle = self._prevalidate_position_plan(position_plan)
        if (
            type(future_imu) is not PreparedRootFutureImuTransaction
            or future_imu.authority is not self.__transaction_authority
            or future_imu.position_plan_digest != position_plan.digest
            or not hmac.compare_digest(
                future_imu.digest, _root_future_imu_plan_digest(future_imu)
            )
        ):
            raise RuntimeError("FORGED_OR_FOREIGN_FUTURE_ROOT_IMU_PLAN")
        before = self._prepare_position_rollback()
        base = self.publication_token()
        before_digest = _root_imu_bundle_digest(before)
        try:
            self._apply_prevalidated_position(position_bundle)
            self.prevalidate_prepared_imu(future_imu.imu_plan)
            compound = self.prepare_imu_velocity_transaction(
                future_imu.imu_plan,
                velocity_delta_mps=velocity_delta_mps,
                maximum_velocity_step_mps=maximum_velocity_step_mps,
                owner=owner,
            )
        finally:
            self._rollback_prevalidated_position(before)
        if _root_imu_bundle_digest(self._prepare_position_rollback()) != before_digest:
            raise RuntimeError("future IMU velocity preparation changed live root owner")
        blank = PreparedRootFutureImuVelocityTransaction(
            self.__transaction_authority, base.revision, base.digest,
            position_plan.digest, future_imu.frame_digest,
            future_imu.digest, compound, "",
        )
        return replace(
            blank, digest=_root_future_imu_velocity_plan_digest(blank),
        )

    def commit_imu_velocity_after_position_transaction(
        self, position_plan: _PreparedRootPositionPlan,
        future_imu: PreparedRootFutureImuTransaction,
        plan: PreparedRootFutureImuVelocityTransaction,
    ) -> bool:
        if (
            type(plan) is not PreparedRootFutureImuVelocityTransaction
            or plan.authority is not self.__transaction_authority
            or plan.position_plan_digest != position_plan.digest
            or plan.future_imu_digest != future_imu.digest
            or plan.frame_digest != future_imu.frame_digest
            or not hmac.compare_digest(
                plan.digest, _root_future_imu_velocity_plan_digest(plan)
            )
        ):
            raise RuntimeError("FORGED_OR_FOREIGN_FUTURE_ROOT_IMU_VELOCITY_PLAN")
        self.prevalidate_imu_after_position_transaction(position_plan, future_imu)
        return self.commit_imu_velocity_transaction(
            future_imu.imu_plan, plan.imu_velocity_plan,
        )

    def prepare_imu_velocity_transaction(
        self, imu_plan: PreparedRootImuTransaction, *,
        velocity_delta_mps: np.ndarray, maximum_velocity_step_mps: float,
        owner: str,
    ) -> PreparedRootImuVelocityTransaction:
        """Compose one accepted IMU plan and one bounded velocity constraint."""

        self.prevalidate_prepared_imu(imu_plan)
        if not imu_plan.accepted:
            raise ValueError("compound root transaction requires accepted IMU plan")
        delta = np.asarray(velocity_delta_mps, dtype=float).reshape(3).copy()
        maximum = float(maximum_velocity_step_mps)
        if (
            not np.isfinite(delta).all() or not math.isfinite(maximum)
            or maximum <= 0.0 or np.linalg.norm(delta) > maximum + 1e-12
            or not owner
        ):
            raise ValueError("compound root velocity delta exceeds its owned bound")
        before = self._prepare_position_rollback()
        before_digest = _root_imu_bundle_digest(before)
        detached = deepcopy(self)
        detached._rollback_prevalidated_position(
            deepcopy(imu_plan._candidate_bundle)
        )
        current = detached.current_state
        vector = current.vector.copy()
        vector[3:6] += delta
        updated = RootState(current.time_s, vector, current.covariance.copy())
        constraint = detached.prepare_current_constraint(
            updated,
            operator=AdditiveRootConstraint(updated.vector-current.vector),
            owner=owner,
        )
        candidate = deepcopy(constraint._candidate_bundle)
        candidate_state = candidate.snapshots[-1].state
        expected_vector = imu_plan.candidate_state.vector.copy()
        expected_vector[3:6] += delta
        if (
            candidate_state.vector[:3].tobytes()
            != imu_plan.candidate_state.vector[:3].tobytes()
            or candidate_state.vector[6:9].tobytes()
            != imu_plan.candidate_state.vector[6:9].tobytes()
            or candidate_state.covariance.tobytes()
            != imu_plan.candidate_state.covariance.tobytes()
            or candidate_state.vector[3:6].tobytes()
            != expected_vector[3:6].tobytes()
            or _root_imu_bundle_digest(self._prepare_position_rollback())
            != before_digest
        ):
            raise RuntimeError("compound IMU velocity preparation changed protected root state")
        delta.setflags(write=False)
        blank = PreparedRootImuVelocityTransaction(
            self.__transaction_authority, self._publication_revision,
            self.publication_token().digest, imu_plan.digest, delta, maximum,
            str(owner), before_digest, _root_imu_bundle_digest(candidate), "",
            deepcopy(before), candidate,
        )
        return replace(blank,digest=_root_imu_velocity_plan_digest(blank))

    def prevalidate_imu_velocity_transaction(
        self, imu_plan: PreparedRootImuTransaction,
        plan: PreparedRootImuVelocityTransaction,
    ) -> None:
        self.prevalidate_prepared_imu(imu_plan)
        if (
            type(plan) is not PreparedRootImuVelocityTransaction
            or plan.authority is not self.__transaction_authority
            or plan.base_revision != self._publication_revision
            or plan.base_publication_digest != self.publication_token().digest
            or plan.imu_plan_digest != imu_plan.digest
            or plan.digest in self.__consumed_imu_velocity_plans
            or not plan.owner
            or not math.isfinite(plan.maximum_velocity_step_mps)
            or plan.maximum_velocity_step_mps <= 0.0
            or not np.isfinite(plan.velocity_delta_mps).all()
            or np.linalg.norm(plan.velocity_delta_mps)
            > plan.maximum_velocity_step_mps + 1e-12
            or not hmac.compare_digest(
                plan.base_owner_digest,
                _root_imu_bundle_digest(plan._base_bundle),
            )
            or not hmac.compare_digest(
                plan.base_owner_digest,
                _root_imu_bundle_digest(self._prepare_position_rollback()),
            )
            or not hmac.compare_digest(
                plan.candidate_owner_digest,
                _root_imu_bundle_digest(plan._candidate_bundle),
            )
            or not hmac.compare_digest(
                plan.digest, _root_imu_velocity_plan_digest(plan),
            )
        ):
            raise RuntimeError("STALE_FORGED_OR_FOREIGN_IMU_VELOCITY_PLAN")

    def commit_imu_velocity_transaction(
        self, imu_plan: PreparedRootImuTransaction,
        plan: PreparedRootImuVelocityTransaction,
    ) -> bool:
        """Commit and consume the compound plan and its underlying IMU plan."""

        self.prevalidate_imu_velocity_transaction(imu_plan,plan)
        self.__consumed_imu_plans.add(imu_plan.digest)
        self.__consumed_imu_velocity_plans.add(plan.digest)
        try:
            self._rollback_prevalidated_position(deepcopy(plan._candidate_bundle))
        except BaseException:
            self._rollback_prevalidated_position(deepcopy(plan._base_bundle))
            raise
        return imu_plan.accepted

    def rollback_committed_imu_velocity_transaction(
        self, plan: PreparedRootImuVelocityTransaction,
    ) -> None:
        """Restore the compound base while keeping both plans consumed."""

        if (
            type(plan) is not PreparedRootImuVelocityTransaction
            or plan.authority is not self.__transaction_authority
            or plan.digest not in self.__consumed_imu_velocity_plans
            or plan.imu_plan_digest not in self.__consumed_imu_plans
            or not hmac.compare_digest(
                plan.digest, _root_imu_velocity_plan_digest(plan),
            )
            or not hmac.compare_digest(
                plan.candidate_owner_digest,
                _root_imu_bundle_digest(self._prepare_position_rollback()),
            )
            or not hmac.compare_digest(
                plan.base_owner_digest,
                _root_imu_bundle_digest(plan._base_bundle),
            )
        ):
            raise RuntimeError("FOREIGN_OR_NONCURRENT_IMU_VELOCITY_ROLLBACK")
        self._rollback_prevalidated_position(deepcopy(plan._base_bundle))

    def add_imu(
        self,
        sample: ImuSample,
        *,
        edge_mode: RootTranslationEdgeMode | None = None,
        following_input_mode: RootTranslationEdgeMode | None = None,
    ) -> bool:
        try:
            sample.validate()
        except ValueError as error:
            if str(error) == "future IMU sample":
                self.future_imu_count += 1
            self._mode = SystemMode.TIME_INVALID
            return False
        if sample.availability_time_s + 1e-12 < self._last_availability_s:
            self._mode = SystemMode.TIME_INVALID
            return False
        if sample.measurement_time_s <= self.current_state.time_s + 1e-12:
            self.late_imu_rejected += 1
            return False
        if sample.m1_reset or not sample.m1_valid:
            self._mode = SystemMode.M1_RESET_RECOVERY
            return False
        edge = self._make_edge(
            start_time_s=self.current_state.time_s,
            end_time_s=sample.measurement_time_s,
            force=sample.specific_force_sensor_mps2,
            rotation=sample.rotation_world_from_sensor,
            input_owner="DESTINATION_RIGHT_END_IMU_SAMPLE",
            edge_mode=edge_mode,
        )
        following_mode = following_input_mode
        if following_mode is None:
            following_mode = (
                RootTranslationEdgeMode.INERTIAL
                if edge.inertial else RootTranslationEdgeMode.CV_NO_ACCELERATION
            )
        if type(following_mode) is not RootTranslationEdgeMode:
            raise TypeError("following input mode must be authoritative")
        if following_mode is RootTranslationEdgeMode.INERTIAL and not self.inertial:
            raise ValueError("CV root filter cannot own an inertial following input")
        state, _ = _propagate_edge_piece(
            self.current_state, sample.measurement_time_s, edge, self.config
        )
        self._snapshots.append(_Snapshot(
            state,
            self._snapshots[-1].applied_constraint_cursor,
            edge,
        ))
        self._last_force = np.asarray(sample.specific_force_sensor_mps2, float).copy()
        self._last_rotation = np.asarray(sample.rotation_world_from_sensor, float).copy()
        self._following_input_mode = following_mode
        self._last_availability_s = max(self._last_availability_s, sample.availability_time_s)
        self._prune()
        if self._mode in (SystemMode.INITIALIZING, SystemMode.M1_RESET_RECOVERY, SystemMode.TIME_INVALID):
            self._mode = SystemMode.IMU_ONLY
        self._publication_revision += 1
        return True

    def ingest_imu_after_source_gap(
        self,
        sample: ImuSample,
        *,
        gap_start_time_s: float,
        source_gap_owner: str,
        following_input_mode: RootTranslationEdgeMode,
    ) -> bool:
        """Advance an unobserved source gap, then own its first real IMU sample.

        No IMU value is extended over the missing interval.  The existing
        no-update gap transaction holds the mean and grows CV covariance; the
        supplied sample becomes the right-end input only for subsequent edges.
        """

        try:
            sample.validate()
        except ValueError:
            return False
        if (
            not isinstance(source_gap_owner, str)
            or len(source_gap_owner) != 64
            or any(character not in "0123456789abcdef" for character in source_gap_owner)
        ):
            raise ValueError("source gap lacks a canonical owner digest")
        if type(following_input_mode) is not RootTranslationEdgeMode:
            raise TypeError("post-gap edge mode must be authoritative")
        start = float(gap_start_time_s)
        if (
            abs(start - self.current_state.time_s) > 1e-12
            or sample.measurement_time_s <= start + 1e-12
            or sample.availability_time_s + 1e-12 < self._last_availability_s
            or sample.m1_reset
            or not sample.m1_valid
        ):
            return False
        plan = self._prepare_no_update_gap(
            gap_start_time_s=start,
            gap_end_time_s=sample.measurement_time_s,
            availability_time_s=sample.availability_time_s,
            post_gap_sample=sample,
            source_gap_owner=source_gap_owner,
            following_input_mode=following_input_mode,
        )
        self._apply_prevalidated_no_update_gap(plan)
        if self._mode in (
            SystemMode.INITIALIZING,
            SystemMode.M1_RESET_RECOVERY,
            SystemMode.TIME_INVALID,
        ):
            self._mode = SystemMode.IMU_ONLY
        return True

    def _prune(self) -> None:
        cutoff = self.current_state.time_s - self.config.fixed_lag_s
        keep_from = 0
        for index, snapshot in enumerate(self._snapshots):
            if snapshot.state.time_s <= cutoff:
                keep_from = index
            else:
                break
        if keep_from > 0:
            self._snapshots = self._snapshots[keep_from:]
            folded_cursor = self._snapshots[0].applied_constraint_cursor
            self._constraint_events = [
                event for event in self._constraint_events
                if event.sequence > folded_cursor
            ]

    def _state_at(
        self,
        measurement_time_s: float,
        *,
        tail_edge: _IncomingEdge | None = None,
    ) -> tuple[
        int,
        RootState,
        int,
        _IncomingEdge | None,
    ] | None:
        times = np.asarray([snapshot.state.time_s for snapshot in self._snapshots])
        index = int(np.searchsorted(times, measurement_time_s, side="right") - 1)
        if index < 0:
            return None
        base = self._snapshots[index]
        if measurement_time_s <= base.state.time_s + 1e-12:
            return (
                index,
                base.state,
                base.applied_constraint_cursor,
                base.incoming_edge,
            )
        following = (
            self._snapshots[index + 1]
            if index + 1 < len(self._snapshots) else None
        )
        incoming_edge = (
            following.incoming_edge if following is not None else tail_edge
        )
        if incoming_edge is None:
            raise RuntimeError(
                "virtual delayed state lacks an authoritative incoming edge"
            )
        state, _ = _propagate_edge_piece(
            base.state, measurement_time_s, incoming_edge, self.config
        )
        return (
            index,
            state,
            base.applied_constraint_cursor,
            incoming_edge,
        )

    def _apply_constraint_events(
        self,
        state: RootState,
        start_cursor: int,
        target_cursor: int,
    ) -> tuple[RootState, int]:
        """Apply one contiguous journal interval in authoritative order."""

        if target_cursor < start_cursor:
            raise RuntimeError("constraint journal cursor reversed")
        cursor = start_cursor
        for event in self._constraint_events:
            if event.sequence <= cursor:
                continue
            if event.sequence > target_cursor:
                break
            if event.sequence != cursor + 1:
                raise RuntimeError("constraint journal sequence is incomplete")
            if abs(event.time_s - state.time_s) > 1e-12:
                raise RuntimeError("constraint event is not at replay epoch")
            covariance_before = state.covariance.tobytes()
            state = event.operator.apply(state)
            if state.covariance.tobytes() != covariance_before:
                raise RuntimeError("constraint replay mutated covariance")
            cursor = event.sequence
        if cursor != target_cursor:
            raise RuntimeError("constraint journal cursor is not reconstructible")
        return state, cursor

    def _replay_from(
        self,
        base_index: int,
        start_state: RootState,
        start_cursor: int,
        target_time_s: float,
        *,
        tail_edge: _IncomingEdge | None = None,
    ) -> tuple[RootState, list[_Snapshot]]:
        """Purely replay authoritative IMU and constraint events to a target."""

        target = float(target_time_s)
        if target + 1e-12 < start_state.time_s:
            raise ValueError("candidate replay reversed time")
        state = start_state
        cursor = int(start_cursor)
        rebuilt: list[_Snapshot] = []
        remaining_edge: _IncomingEdge | None = None
        for snapshot in self._snapshots[base_index + 1:]:
            if snapshot.state.time_s <= start_state.time_s + 1e-12:
                continue
            if snapshot.state.time_s > target + 1e-12:
                remaining_edge = snapshot.incoming_edge
                break
            if snapshot.incoming_edge is None:
                raise RuntimeError(
                    "authoritative snapshot lacks an incoming edge"
                )
            state, _ = _propagate_edge_piece(
                state, snapshot.state.time_s, snapshot.incoming_edge,
                self.config,
            )
            state, cursor = self._apply_constraint_events(
                state, cursor, snapshot.applied_constraint_cursor
            )
            rebuilt.append(_Snapshot(
                state,
                cursor,
                snapshot.incoming_edge,
            ))
        if target > state.time_s + 1e-12:
            edge = remaining_edge if remaining_edge is not None else tail_edge
            if edge is None:
                raise RuntimeError(
                    "candidate replay tail lacks an authoritative incoming edge"
                )
            state, _ = _propagate_edge_piece(
                state, target, edge, self.config
            )
        return state, rebuilt

    def _replay_after(
        self,
        base_index: int,
        updated: RootState,
        start_cursor: int,
        start_edge: _IncomingEdge | None,
        target_time_s: float,
        tail_edge: _IncomingEdge | None,
    ) -> None:
        """Commit the same replay path used by baseline and candidate audits."""

        self._snapshots = list(self._prepared_replay_snapshots(
            base_index, updated, start_cursor, start_edge, target_time_s,
            tail_edge,
        ))

    def _prepared_replay_snapshots(
        self,
        base_index: int,
        updated: RootState,
        start_cursor: int,
        start_edge: _IncomingEdge | None,
        target_time_s: float,
        tail_edge: _IncomingEdge | None,
    ) -> tuple[_Snapshot, ...]:
        """Build the authoritative replay assignment without mutating owner state."""

        prior = self._snapshots
        current, rebuilt = self._replay_from(
            base_index,
            updated,
            start_cursor,
            target_time_s,
            tail_edge=tail_edge,
        )
        retained = prior[: base_index + 1]
        virtual = _Snapshot(
            updated,
            start_cursor,
            start_edge,
        )
        if retained and abs(retained[-1].state.time_s - updated.time_s) <= 1e-12:
            retained[-1] = virtual
        else:
            retained.append(virtual)
        retained.extend(rebuilt)
        if target_time_s > retained[-1].state.time_s + 1e-12:
            if tail_edge is None:
                raise RuntimeError(
                    "committed availability endpoint lacks an incoming edge"
                )
            retained.append(_Snapshot(
                current,
                retained[-1].applied_constraint_cursor,
                tail_edge,
            ))
        return tuple(retained)

    def _current_state_at(
        self,
        target_time_s: float,
        *,
        tail_edge: _IncomingEdge | None = None,
    ) -> RootState:
        """Read the current state at an available time without committing it."""

        target = float(target_time_s)
        if target + 1e-12 < self.current_state.time_s:
            raise ValueError("current-state audit reversed time")
        if target <= self.current_state.time_s + 1e-12:
            return self.current_state
        if tail_edge is None:
            raise RuntimeError(
                "current-state tail lacks an authoritative incoming edge"
            )
        propagated, _ = _propagate_edge_piece(
            self.current_state, target, tail_edge, self.config
        )
        return propagated

    def _baseline_reconstruction_diagnostic(
        self,
        *,
        base_index: int,
        measurement_time_s: float,
        processing_time_s: float,
        start_cursor: int,
        start_edge: _IncomingEdge | None,
        replayed: RootState,
        current: RootState,
    ) -> dict:
        """Describe interval-input ownership at one failed baseline audit."""

        preceding = self._snapshots[base_index]
        following = (
            self._snapshots[base_index + 1]
            if base_index + 1 < len(self._snapshots)
            else None
        )
        vector_delta = replayed.vector - current.vector
        covariance_delta = replayed.covariance - current.covariance
        covariance_tolerance, covariance_spacing = (
            _authoritative_baseline_covariance_tolerance(
                replayed.covariance,
                current.covariance,
            )
        )
        covariance_excess = np.maximum(
            np.abs(covariance_delta)
            - _AUTHORITATIVE_BASELINE_COVARIANCE_ATOL,
            0.0,
        )
        normalized_ulp_excess = np.zeros_like(covariance_excess)
        positive_excess = covariance_excess > 0.0
        if np.any(positive_excess):
            normalized_ulp_excess[positive_excess] = np.asarray(
                covariance_excess[positive_excess], dtype=np.longdouble
            ) / np.asarray(
                _AUTHORITATIVE_BASELINE_COVARIANCE_ULPS
                * covariance_spacing[positive_excess],
                dtype=np.longdouble,
            )
        current_cursor = self._snapshots[-1].applied_constraint_cursor
        replay_events = [
            event for event in self._constraint_events
            if start_cursor < event.sequence <= current_cursor
            and event.time_s <= processing_time_s + 1e-12
        ]
        return {
            "measurement_time_s": float(measurement_time_s),
            "processing_time_s": float(processing_time_s),
            "measurement_is_virtual": bool(
                abs(measurement_time_s - preceding.state.time_s) > 1e-12
            ),
            "preceding_snapshot_time_s": float(preceding.state.time_s),
            "following_snapshot_time_s": (
                None if following is None
                else float(following.state.time_s)
            ),
            "preceding_snapshot_force_sensor_mps2": (
                None if preceding.incoming_edge is None
                else preceding.incoming_edge.force_sensor_mps2.tolist()
            ),
            "following_snapshot_force_sensor_mps2": (
                None if following is None
                else following.incoming_edge.force_sensor_mps2.tolist()
            ),
            "original_preceding_to_following_interval_force_owner": (
                None if following is None else "FOLLOWING_RIGHT_END_SAMPLE"
            ),
            "virtual_preceding_to_measurement_force_owner": (
                None if start_edge is None else start_edge.input_owner
            ),
            "virtual_measurement_to_following_force_owner": (
                None if start_edge is None else start_edge.input_owner
            ),
            "start_constraint_cursor": int(start_cursor),
            "current_constraint_cursor": int(current_cursor),
            "replayed_event_count": len(replay_events),
            "replayed_event_sequence": [event.sequence for event in replay_events],
            "replayed_event_owners": [event.owner for event in replay_events],
            "current_vector": current.vector.tolist(),
            "replayed_vector": replayed.vector.tolist(),
            "replayed_minus_current_vector": vector_delta.tolist(),
            "replayed_minus_current_vector_max_abs": float(
                np.max(np.abs(vector_delta))
            ),
            "replayed_minus_current_covariance_max_abs": float(
                np.max(np.abs(covariance_delta))
            ),
            "replayed_minus_current_covariance_frobenius": float(
                np.linalg.norm(covariance_delta)
            ),
            "covariance_max_abs_scale": float(max(
                np.max(np.abs(replayed.covariance)),
                np.max(np.abs(current.covariance)),
            )),
            "covariance_max_componentwise_tolerance": float(
                np.max(covariance_tolerance)
            ),
            "replayed_minus_current_covariance_max_normalized_ulp_excess": (
                float(np.max(normalized_ulp_excess))
            ),
        }

    def _note_health(self, observation: PositionObservation, decision: UpdateDecision) -> None:
        health = self._health.setdefault(observation.tag_id, _Health())
        targets = [health, *(self._anchor_health[a] for a in observation.anchors)]
        for target in targets:
            if decision.accepted:
                target.accepted += 1; target.consecutive_rejected = 0; target.last_reason = None
            else:
                target.rejected += 1; target.consecutive_rejected += 1; target.last_reason = decision.reason

    def _set_mode(self, observation: PositionObservation, decision: UpdateDecision,
                  processing_time_s: float) -> None:
        if decision.reason == "REJECT_FRAME_UNQUALIFIED":
            self._mode = SystemMode.TIME_INVALID
            return
        if decision.accepted:
            had_gap = (self._last_accepted_availability_s is None or
                       processing_time_s - self._last_accepted_availability_s > self.config.uwb_dropout_s)
            self._recovery_good = 1 if had_gap else self._recovery_good + 1
            self._mode = (SystemMode.UWB_RECOVERY if self._recovery_good < self.config.recovery_good_events
                          else SystemMode.FUSED_NOMINAL)
            self._last_accepted_measurement_s = observation.measurement_time_s
            self._last_accepted_availability_s = processing_time_s
        else:
            self._recovery_good = 0
            age = math.inf if self._last_accepted_availability_s is None else processing_time_s - self._last_accepted_availability_s
            self._mode = SystemMode.IMU_ONLY if age > self.config.uwb_dropout_s else SystemMode.FUSED_UWB_DEGRADED

    def _add_position_reference(self, observation: PositionObservation, *,
                     processing_time_s: float | None = None,
                     influence_multiplier: float = 1.0) -> UpdateDecision:
        try:
            observation.validate()
        except ValueError as error:
            if str(error) == "future UWB observation":
                self.future_uwb_count += 1
            self._mode = SystemMode.TIME_INVALID
            return UpdateDecision(False, "REJECT_TIME_INVALID", None, np.zeros(3), np.zeros(3), 0.0)
        processing_time = float(observation.availability_time_s if processing_time_s is None
                                else max(processing_time_s, observation.availability_time_s))
        if not math.isfinite(float(influence_multiplier)) or not 0.0 <= influence_multiplier <= 1.0:
            raise ValueError("position influence multiplier must be in [0, 1]")
        if processing_time + 1e-12 < self._last_availability_s:
            self._mode = SystemMode.TIME_INVALID
            return UpdateDecision(False, "REJECT_AVAILABILITY_REVERSAL", None, np.zeros(3), np.zeros(3), 0.0)
        if observation.measurement_time_s + 1e-12 < self._last_observation_measurement_s:
            self._mode = SystemMode.TIME_INVALID
            return UpdateDecision(False, "REJECT_MEASUREMENT_ORDER_REVERSAL", None, np.zeros(3), np.zeros(3), 0.0)
        self._last_observation_measurement_s = observation.measurement_time_s
        tail_edge = (
            self._make_edge(
                start_time_s=self.current_state.time_s,
                end_time_s=processing_time,
                force=self._last_force,
                rotation=self._last_rotation,
                input_owner="EPHEMERAL_AVAILABILITY_HELD_INPUT",
                edge_mode=self._following_input_mode,
            )
            if processing_time > self.current_state.time_s + 1e-12
            else None
        )
        located = self._state_at(
            observation.measurement_time_s, tail_edge=tail_edge
        )
        if located is None:
            decision = UpdateDecision(False, "REJECT_OUTSIDE_FIXED_LAG", None, np.zeros(3), np.zeros(3), 0.0)
        else:
            (
                index,
                delayed,
                start_cursor,
                start_edge,
            ) = located
            # Prove that the journal can reconstruct the authoritative
            # processing-time state before constructing any delayed-update
            # candidate. A missing root event is therefore an ownership error,
            # never something a UWB replay may silently overwrite.
            current_before = self._current_state_at(
                processing_time, tail_edge=tail_edge
            )
            baseline_candidate, _baseline_rebuilt = self._replay_from(
                index, delayed, start_cursor, processing_time,
                tail_edge=tail_edge,
            )
            if not _authoritative_baseline_equivalent(
                baseline_candidate, current_before
            ):
                raise AuthoritativeBaselineReconstructionError(
                    self._baseline_reconstruction_diagnostic(
                        base_index=index,
                        measurement_time_s=observation.measurement_time_s,
                        processing_time_s=processing_time,
                        start_cursor=start_cursor,
                        start_edge=start_edge,
                        replayed=baseline_candidate,
                        current=current_before,
                    )
                )
            recovery_scale = 1.0
            if self._mode in (SystemMode.IMU_ONLY, SystemMode.UWB_RECOVERY, SystemMode.INITIALIZING):
                recovery_scale = min(1.0, max(0.10, (self._recovery_good + 1) / self.config.recovery_good_events))
            updated, decision, effective_gain = _update_position_details(
                delayed, observation, self.config,
                influence_multiplier=recovery_scale * influence_multiplier,
            )
            if decision.accepted:
                if effective_gain is None:
                    raise RuntimeError("accepted root update lacks an effective gain")
                current_candidate, _candidate_rebuilt = self._replay_from(
                    index, updated, start_cursor, processing_time,
                    tail_edge=tail_edge,
                )
                availability_delta = (
                    current_candidate.vector - baseline_candidate.vector
                )
                availability_position_norm = float(
                    np.linalg.norm(availability_delta[:3])
                )
                availability_cap = max(
                    0.0, self.config.maximum_position_influence_m
                )
                replay_scale = (
                    1.0
                    if availability_position_norm <= availability_cap
                    or availability_position_norm == 0.0
                    else availability_cap / availability_position_norm
                )
                if replay_scale < 1.0:
                    effective_gain = effective_gain * replay_scale
                    updated = _apply_position_gain(
                        delayed,
                        np.asarray(observation.covariance_m2, float),
                        decision.innovation_m,
                        effective_gain,
                        self.config,
                    )
                    current_candidate, _candidate_rebuilt = self._replay_from(
                        index, updated, start_cursor, processing_time,
                        tail_edge=tail_edge,
                    )
                    availability_delta = (
                        current_candidate.vector - baseline_candidate.vector
                    )
                if (
                    float(np.linalg.norm(availability_delta[:3]))
                    > availability_cap + 1e-10
                ):
                    raise RuntimeError(
                        "availability-time UWB influence remains above cap"
                    )
                applied = effective_gain @ decision.innovation_m
                decision = replace(
                    decision,
                    applied_position_delta_m=applied[:3],
                    influence_scale=decision.influence_scale * replay_scale,
                    availability_applied_position_delta_m=(
                        availability_delta[:3].copy()
                    ),
                    availability_applied_velocity_delta_mps=(
                        availability_delta[3:6].copy()
                    ),
                    availability_influence_scale=replay_scale,
                )
                self._replay_after(
                    index,
                    updated,
                    start_cursor,
                    start_edge,
                    processing_time,
                    tail_edge,
                )
        self._note_health(observation, decision)
        self._set_mode(observation, decision, processing_time)
        self._last_availability_s = max(self._last_availability_s, processing_time)
        return decision

    def prepare_position(
        self,
        observation: PositionObservation,
        *,
        processing_time_s: float | None = None,
        influence_multiplier: float = 1.0,
        state_update_indices: tuple[int, ...] = tuple(range(9)),
    ) -> _PreparedRootPositionPlan:
        """Prepare one delayed update without mutating any filter owner."""

        observation.validate()
        processing_time = float(
            observation.availability_time_s if processing_time_s is None
            else max(processing_time_s, observation.availability_time_s)
        )
        return self._prepare_position_at_horizon(
            observation, processing_time_s=processing_time,
            health_availability_time_s=processing_time,
            influence_multiplier=influence_multiplier,
            state_update_indices=state_update_indices,
            admission_digest=None,
            require_watermark_order=True,
        )

    def prepare_received_position_at_measurement_horizon(
        self,
        observation: PositionObservation,
        *,
        influence_multiplier: float = 1.0,
        state_update_indices: tuple[int, ...] = tuple(range(9)),
    ) -> _PreparedRootPositionPlan:
        """Prepare an already-available observation without advancing to receipt time.

        This is the transaction-facing counterpart of the availability-receipt
        deferred path: availability remains the causal/health watermark while
        numeric replay stops at the later of the committed inertial horizon and
        the observation epoch.  Any short tail therefore uses only the causal
        zero-order-held input already owned at receipt.
        """

        observation.validate()
        horizon = max(
            float(self.current_state.time_s),
            float(observation.measurement_time_s),
        )
        return self._prepare_position_at_horizon(
            observation,
            processing_time_s=horizon,
            health_availability_time_s=observation.availability_time_s,
            influence_multiplier=influence_multiplier,
            state_update_indices=state_update_indices,
            admission_digest=None,
            require_watermark_order=False,
        )

    def _prepare_position_at_horizon(
        self, observation: PositionObservation, *, processing_time_s: float,
        health_availability_time_s: float, influence_multiplier: float,
        state_update_indices: tuple[int, ...], admission_digest: str | None,
        require_watermark_order: bool,
    ) -> _PreparedRootPositionPlan:
        """Common owner for normal and already-received delayed observations."""

        observation.validate()
        processing_time = float(processing_time_s)
        health_availability = float(health_availability_time_s)
        if (
            not math.isfinite(processing_time)
            or not math.isfinite(health_availability)
            or processing_time + 1e-12 < observation.measurement_time_s
            or health_availability + 1e-12 < observation.measurement_time_s
        ):
            raise ValueError("position processing horizon is invalid")
        if not math.isfinite(float(influence_multiplier)) or not 0.0 <= influence_multiplier <= 1.0:
            raise ValueError("position influence multiplier must be in [0, 1]")
        if require_watermark_order and processing_time + 1e-12 < self._last_availability_s:
            raise ValueError("position availability reversed time")
        if observation.measurement_time_s + 1e-12 < self._last_observation_measurement_s:
            raise ValueError("position measurement order reversed")
        tail_edge = self._make_edge(
            start_time_s=self.current_state.time_s,
            end_time_s=processing_time,
            force=self._last_force,
            rotation=self._last_rotation,
            input_owner="EPHEMERAL_AVAILABILITY_HELD_INPUT",
            edge_mode=self._following_input_mode,
        ) if processing_time > self.current_state.time_s + 1e-12 else None
        current_before = self._current_state_at(processing_time, tail_edge=tail_edge)
        decision = UpdateDecision(False, "REJECT_OUTSIDE_FIXED_LAG", None, np.zeros(3), np.zeros(3), 0.0)
        candidate = current_before
        snapshots: list[_Snapshot] = list(self._snapshots)
        located = self._state_at(observation.measurement_time_s, tail_edge=tail_edge)
        if located is not None:
            index, delayed, start_cursor, start_edge = located
            baseline, _ = self._replay_from(index, delayed, start_cursor, processing_time, tail_edge=tail_edge)
            if not _authoritative_baseline_equivalent(
                baseline, current_before
            ):
                raise AuthoritativeBaselineReconstructionError(self._baseline_reconstruction_diagnostic(
                    base_index=index,measurement_time_s=observation.measurement_time_s,
                    processing_time_s=processing_time,start_cursor=start_cursor,start_edge=start_edge,
                    replayed=baseline,current=current_before))
            recovery_scale=1.0
            if self._mode in (SystemMode.IMU_ONLY,SystemMode.UWB_RECOVERY,SystemMode.INITIALIZING):
                recovery_scale=min(1.0,max(.10,(self._recovery_good+1)/self.config.recovery_good_events))
            updated,decision,gain=_update_position_details(
                delayed, observation, self.config,
                influence_multiplier=recovery_scale*influence_multiplier,
                state_update_indices=state_update_indices,
            )
            if decision.accepted:
                if gain is None: raise RuntimeError("accepted root update lacks an effective gain")
                candidate,_=self._replay_from(index,updated,start_cursor,processing_time,tail_edge=tail_edge)
                delta=candidate.vector-baseline.vector; cap=max(0.,self.config.maximum_position_influence_m)
                scale=1.0 if np.linalg.norm(delta[:3])<=cap or np.linalg.norm(delta[:3])==0 else cap/float(np.linalg.norm(delta[:3]))
                if scale<1.0:
                    gain=gain*scale
                    updated=_apply_position_gain(delayed,np.asarray(observation.covariance_m2,float),decision.innovation_m,gain,self.config)
                    candidate,_=self._replay_from(index,updated,start_cursor,processing_time,tail_edge=tail_edge)
                    delta=candidate.vector-baseline.vector
                if np.linalg.norm(delta[:3])>cap+1e-10: raise RuntimeError("availability-time UWB influence remains above cap")
                applied=gain@decision.innovation_m
                decision=replace(decision,applied_position_delta_m=applied[:3],
                    influence_scale=decision.influence_scale*scale,
                    availability_applied_position_delta_m=delta[:3].copy(),
                    availability_applied_velocity_delta_mps=delta[3:6].copy(),
                    availability_influence_scale=scale)
                snapshots=list(self._prepared_replay_snapshots(index,updated,start_cursor,start_edge,processing_time,tail_edge))
                inactive = np.asarray([
                    state_index for state_index in range(9)
                    if state_index not in state_update_indices
                ], dtype=int)
                if inactive.size:
                    if (
                        not snapshots
                        or abs(snapshots[-1].state.time_s - current_before.time_s)
                        > 1e-12
                    ):
                        raise RuntimeError(
                            "position transaction lacks availability-time snapshot"
                        )
                    availability_snapshot = snapshots[-1]
                    canonical_vector = availability_snapshot.state.vector.copy()
                    canonical_vector[inactive] = current_before.vector[inactive]
                    canonical_state = RootState(
                        availability_snapshot.state.time_s,
                        canonical_vector,
                        availability_snapshot.state.covariance.copy(),
                    )
                    snapshots[-1] = replace(
                        availability_snapshot, state=canonical_state,
                    )
                    candidate = canonical_state
        health={key:_Health(**vars(value)) for key,value in self._health.items()}
        anchor_health={key:_Health(**vars(value)) for key,value in self._anchor_health.items()}
        target_health=health.setdefault(observation.tag_id,_Health())
        for target in [target_health,*(anchor_health[a] for a in observation.anchors)]:
            if decision.accepted:
                target.accepted+=1; target.consecutive_rejected=0; target.last_reason=None
            else:
                target.rejected+=1; target.consecutive_rejected+=1; target.last_reason=decision.reason
        recovery_good=self._recovery_good
        last_measurement=self._last_accepted_measurement_s
        last_availability=self._last_accepted_availability_s
        if decision.reason=="REJECT_FRAME_UNQUALIFIED": mode=SystemMode.TIME_INVALID
        elif decision.accepted:
            had_gap=last_availability is None or health_availability-last_availability>self.config.uwb_dropout_s
            recovery_good=1 if had_gap else recovery_good+1
            mode=SystemMode.UWB_RECOVERY if recovery_good<self.config.recovery_good_events else SystemMode.FUSED_NOMINAL
            last_measurement=observation.measurement_time_s; last_availability=health_availability
        else:
            recovery_good=0
            age=math.inf if last_availability is None else health_availability-last_availability
            mode=SystemMode.IMU_ONLY if age>self.config.uwb_dropout_s else SystemMode.FUSED_UWB_DEGRADED
        position=np.asarray(observation.root_position_m,float).copy(); covariance=np.asarray(observation.covariance_m2,float).copy()
        position.setflags(write=False); covariance.setflags(write=False)
        frozen_observation=replace(observation,root_position_m=position,covariance_m2=covariance)
        frozen_snapshots=tuple(_Snapshot(_readonly_root_state(value.state),value.applied_constraint_cursor,value.incoming_edge) for value in snapshots)
        health_rows=tuple((key,value.accepted,value.rejected,value.consecutive_rejected,value.last_reason) for key,value in sorted(health.items()))
        anchor_rows=tuple((key,value.accepted,value.rejected,value.consecutive_rejected,value.last_reason) for key,value in sorted(anchor_health.items()))
        blank=_PreparedRootPositionPlan(self.__transaction_authority,self._publication_revision,
            frozen_observation,processing_time,_readonly_decision(decision),
            _readonly_root_state(current_before),_readonly_root_state(candidate),frozen_snapshots,
            health_rows,anchor_rows,recovery_good,mode,observation.measurement_time_s,
            last_measurement,last_availability,
            max(self._last_availability_s,health_availability),admission_digest,"")
        return replace(blank,digest=_root_plan_digest(blank))

    def _prevalidate_position_plan(self, plan: _PreparedRootPositionPlan) -> _RootCommitBundle:
        if (not isinstance(plan,_PreparedRootPositionPlan) or plan.authority is not self.__transaction_authority
                or plan.base_revision!=self._publication_revision
                or not hmac.compare_digest(plan.digest,_root_plan_digest(plan))):
            raise RuntimeError("STALE_ROOT_POSITION_PLAN")
        health={row[0]:_Health(*row[1:]) for row in plan.health}
        anchor={row[0]:_Health(*row[1:]) for row in plan.anchor_health}
        return _RootCommitBundle(self.__transaction_authority,self._publication_revision,plan.digest,
            list(plan.snapshots),health,anchor,plan.recovery_good,plan.mode,
            plan.last_observation_measurement_s,plan.last_accepted_measurement_s,
            plan.last_accepted_availability_s,plan.last_availability_s,plan.decision)

    def _apply_prevalidated_position(self, bundle: _RootCommitBundle) -> UpdateDecision:
        self._snapshots=bundle.snapshots; self._health=bundle.health; self._anchor_health=bundle.anchor_health
        self._recovery_good=bundle.recovery_good; self._mode=bundle.mode
        self._last_observation_measurement_s=bundle.last_observation_measurement_s
        self._last_accepted_measurement_s=bundle.last_accepted_measurement_s
        self._last_accepted_availability_s=bundle.last_accepted_availability_s
        self._last_availability_s=bundle.last_availability_s
        self._publication_revision+=1
        return bundle.decision

    def _prepare_position_rollback(self) -> _RootRollbackBundle:
        return _RootRollbackBundle(
            list(self._snapshots),
            {key:_Health(**vars(value)) for key,value in self._health.items()},
            {key:_Health(**vars(value)) for key,value in self._anchor_health.items()},
            self._recovery_good, self._mode,
            self._last_observation_measurement_s,
            self._last_accepted_measurement_s,
            self._last_accepted_availability_s,
            self._last_availability_s, self._publication_revision,
            list(self._constraint_events), self._next_constraint_sequence,
            self._last_force.copy(), self._last_rotation.copy(),
            self._following_input_mode, self._last_source_gap_owner,
            self._terminal_missing_owner,
            list(self._emission_times), self.future_imu_count,
            self.future_uwb_count, self.preavailability_output_count,
            self.late_imu_rejected,
        )

    def _rollback_prevalidated_position(self, bundle: _RootRollbackBundle) -> None:
        self._snapshots = bundle.snapshots
        self._health = bundle.health
        self._anchor_health = bundle.anchor_health
        self._recovery_good = bundle.recovery_good
        self._mode = bundle.mode
        self._last_observation_measurement_s = bundle.last_observation_measurement_s
        self._last_accepted_measurement_s = bundle.last_accepted_measurement_s
        self._last_accepted_availability_s = bundle.last_accepted_availability_s
        self._last_availability_s = bundle.last_availability_s
        self._publication_revision = bundle.publication_revision
        self._constraint_events = bundle.constraint_events
        self._next_constraint_sequence = bundle.next_constraint_sequence
        self._last_force = bundle.last_force
        self._last_rotation = bundle.last_rotation
        self._following_input_mode = bundle.following_input_mode
        self._last_source_gap_owner = bundle.last_source_gap_owner
        self._terminal_missing_owner = bundle.terminal_missing_owner
        self._emission_times = bundle.emission_times
        self.future_imu_count = bundle.future_imu_count
        self.future_uwb_count = bundle.future_uwb_count
        self.preavailability_output_count = bundle.preavailability_output_count
        self.late_imu_rejected = bundle.late_imu_rejected

    def prepare_guard_rejection(self, plan: _PreparedRootPositionPlan, reason: str) -> _PreparedRootPositionPlan:
        self._prevalidate_position_plan(plan)
        decision=replace(plan.decision,accepted=False,reason=str(reason),applied_position_delta_m=np.zeros(3),availability_applied_position_delta_m=np.zeros(3),availability_applied_velocity_delta_mps=np.zeros(3))
        health={key:_Health(**vars(value)) for key,value in self._health.items()}; anchor={key:_Health(**vars(value)) for key,value in self._anchor_health.items()}
        targets=[health.setdefault(plan.observation.tag_id,_Health()),*(anchor[a] for a in plan.observation.anchors)]
        for target in targets: target.rejected+=1; target.consecutive_rejected+=1; target.last_reason=decision.reason
        age=math.inf if self._last_accepted_availability_s is None else plan.processing_time_s-self._last_accepted_availability_s
        mode=SystemMode.IMU_ONLY if age>self.config.uwb_dropout_s else SystemMode.FUSED_UWB_DEGRADED
        replacement=replace(plan,decision=_readonly_decision(decision),measurement_candidate=plan.imu_prediction,
            snapshots=tuple(_Snapshot(_readonly_root_state(value.state),value.applied_constraint_cursor,value.incoming_edge) for value in self._snapshots),
            health=tuple((key,value.accepted,value.rejected,value.consecutive_rejected,value.last_reason) for key,value in sorted(health.items())),
            anchor_health=tuple((key,value.accepted,value.rejected,value.consecutive_rejected,value.last_reason) for key,value in sorted(anchor.items())),recovery_good=0,mode=mode,
            last_observation_measurement_s=plan.observation.measurement_time_s,
            last_accepted_measurement_s=self._last_accepted_measurement_s,
            last_accepted_availability_s=self._last_accepted_availability_s,
            last_availability_s=max(self._last_availability_s,plan.processing_time_s),digest="")
        return replace(replacement,digest=_root_plan_digest(replacement))

    def add_position(self, observation: PositionObservation, *,
                     processing_time_s: float | None = None,
                     influence_multiplier: float = 1.0,
                     state_update_indices: tuple[int, ...] = tuple(range(9))) -> UpdateDecision:
        """Legacy entry point delegated through the transactional plan owner."""
        try:
            plan=self.prepare_position(
                observation, processing_time_s=processing_time_s,
                influence_multiplier=influence_multiplier,
                state_update_indices=state_update_indices,
            )
        except ValueError as error:
            if str(error)=="future UWB observation": self.future_uwb_count+=1
            self._mode=SystemMode.TIME_INVALID
            return UpdateDecision(False,"REJECT_TIME_INVALID",None,np.zeros(3),np.zeros(3),0.0)
        return self._apply_prevalidated_position(self._prevalidate_position_plan(plan))

    def emit(self, output_time_s: float, decision: UpdateDecision | None = None,
             observation: PositionObservation | None = None) -> RootOutput:
        if output_time_s + 1e-12 < self._last_availability_s:
            self.preavailability_output_count += 1
            raise ValueError("output before measurement availability")
        if self._emission_times and output_time_s + 1e-12 < self._emission_times[-1]:
            raise ValueError("output time reversal")
        state = self.current_state
        if output_time_s > state.time_s + 1e-12:
            edge = self._make_edge(
                start_time_s=state.time_s,
                end_time_s=output_time_s,
                force=self._last_force,
                rotation=self._last_rotation,
                input_owner="EPHEMERAL_OUTPUT_HELD_INPUT",
                edge_mode=self._following_input_mode,
            )
            state, _ = _propagate_edge_piece(
                state, output_time_s, edge, self.config,
            )
        self._emission_times.append(float(output_time_s))
        age = None if self._last_accepted_measurement_s is None else float(output_time_s - self._last_accepted_measurement_s)
        rejected = None
        reason = None
        accepted = None
        if observation is not None and decision is not None:
            if decision.accepted:
                accepted = observation.tag_id
            else:
                rejected = observation.tag_id; reason = decision.reason
        degraded_tags = sum(1 for health in self._health.values() if health.consecutive_rejected >= 3)
        degraded_anchors = sum(1 for health in self._anchor_health.values() if health.consecutive_rejected >= 3)
        return RootOutput(
            float(output_time_s), state.position_m.copy(), state.velocity_mps.copy(),
            state.covariance[:3, :3].copy(), age, self._mode, accepted, rejected, reason,
            f"degraded_tags={degraded_tags};degraded_anchors={degraded_anchors}",
            self.future_uwb_count, self.future_imu_count, self.preavailability_output_count,
            {"state_time_s": state.time_s,
             "accepted_updates": sum(h.accepted for h in self._health.values()),
             "rejected_updates": sum(h.rejected for h in self._health.values())},
        )

    def health_snapshot(self) -> dict:
        return {
            "tags": {tag: vars(health).copy() for tag, health in sorted(self._health.items())},
            "anchors": {str(anchor): vars(health).copy() for anchor, health in sorted(self._anchor_health.items())},
        }
