"""Fixture-qualified atomic coordinator for guarded C2 candidate publication.

The owner assumes one event-loop thread.  It neither locks nor rolls state back:
all expensive work and token validation precede the two assignment-only commits.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Mapping
import numpy as np

from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter
from biospur_fusion.root_r3.models import PositionObservation
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import CausalArticulatedPose
from .causal_update_guard import (
    CandidateKind,CandidateTransition,CausalContactTransitionEvidence,
    CausalImuActivitySummary,IndependentNodeConsensusEvidence,
    InnovationIntegrityEvidence,PublicHingeKinematics,ReachabilityEnvelope,
    RootKinematicState,TransitionDecision,TransitionDisposition,
    evaluate_candidate_transition,
)

_HINGES=("elbow_left","elbow_right","knee_left","knee_right")
_HINGE_OWNER="biospur_fusion.c2_articulated_biomechanics"

@dataclass(frozen=True)
class TransactionResult:
    decision: TransitionDecision
    root_decision_reason: str
    root_committed: bool
    pose_committed: bool
    rejection_recorded: bool


_PREPARED_TRANSACTION_KEY = object()
_PREPARED_SIDECAR_KEY = object()


@dataclass
class _CommitState:
    consumed: bool = False


@dataclass(frozen=True)
class PreparedCausalSidecarTicket:
    """Prevalidated assignment-only participant in a causal transaction."""

    owner: Any
    ticket: Any
    base_token: Any
    validate: Callable[[Any, Any, Any], None]
    apply: Callable[[Any, Any], None]
    rollback: Callable[[Any, Any], None]
    key: object = _PREPARED_SIDECAR_KEY


@dataclass(frozen=True)
class PreparedCausalUpdateTransaction:
    """Owner-bound, one-shot transaction plan; construction mutates no owner."""

    root: CausalDelayedRootFilter
    pose: CausalArticulatedPose | None
    root_token: Any
    pose_token: Any
    root_ticket: Any
    root_plan: Any
    root_observation: PositionObservation
    root_plan_digest: str
    pose_ticket: Any
    root_rollback: Any
    pose_rollback: Any
    result: TransactionResult
    commit_state: _CommitState
    key: object = _PREPARED_TRANSACTION_KEY


def _same_position_observation(
    left: PositionObservation, right: PositionObservation,
) -> bool:
    return bool(
        type(left) is PositionObservation
        and type(right) is PositionObservation
        and left.measurement_time_s == right.measurement_time_s
        and left.availability_time_s == right.availability_time_s
        and left.root_position_m.tobytes() == right.root_position_m.tobytes()
        and left.covariance_m2.tobytes() == right.covariance_m2.tobytes()
        and left.tag_id == right.tag_id
        and left.anchors == right.anchors
        and left.quality_state == right.quality_state
        and left.frame_valid is right.frame_valid
        and left.physical_point_valid is right.physical_point_valid
        and left.source_sequence == right.source_sequence
    )

def _root_state(state) -> RootKinematicState:
    return RootKinematicState(float(state.time_s),state.vector[:3],state.vector[3:6],state.covariance)

def _hinge(evidence) -> PublicHingeKinematics:
    if evidence is None:
        raise ValueError("POSE_PLAN_LACKS_DIRECT_HINGE_CONTINUITY")
    return PublicHingeKinematics(
        source_time_s=evidence.source_time_s,joint_ids=_HINGES,
        joint_step_rad=evidence.joint_step_maximum_rad,
        joint_angular_velocity_rad_s=evidence.joint_rate_maximum_rad_s,
        joint_angular_acceleration_rad_s2=evidence.joint_acceleration_maximum_rad_s2,
        rom_valid=evidence.rom_valid,fk_valid=evidence.fk_valid,
        owner=_HINGE_OWNER,provenance=evidence.provenance)

def prepare_causal_update_transaction(
    *,root: CausalDelayedRootFilter,observation: PositionObservation,
    kind: CandidateKind,nominal_envelope: ReachabilityEnvelope,
    pose: CausalArticulatedPose | None=None,
    correction_at_measurement: Mapping[str,np.ndarray] | None=None,
    hinge_projection_at_source: Mapping[str,Any] | None=None,
    native200_source_binding: Mapping[str,Any] | None=None,
    dynamic_envelope: ReachabilityEnvelope | None=None,
    activity: CausalImuActivitySummary | None=None,
    consensus: IndependentNodeConsensusEvidence | None=None,
    contact: CausalContactTransitionEvidence | None=None,
    record_rejection: bool=True,
    received_at_committed_horizon: bool=False,
) -> PreparedCausalUpdateTransaction:
    """Prepare and guard exactly once without mutating any participant."""
    if not isinstance(kind,CandidateKind): raise ValueError("INVALID_TRANSACTION_KIND")
    if type(record_rejection) is not bool: raise ValueError("INVALID_REJECTION_POLICY")
    if kind is CandidateKind.ROOT_POSITION and any(value is not None for value in (
            pose,correction_at_measurement,hinge_projection_at_source,native200_source_binding)):
        raise ValueError("ROOT_TRANSACTION_CANNOT_CARRY_POSE")
    if kind is CandidateKind.ARTICULATED_IK and any(value is None for value in (
            pose,correction_at_measurement,hinge_projection_at_source,native200_source_binding)):
        raise ValueError("ARTICULATED_TRANSACTION_REQUIRES_POSE")
    root_token=root.publication_token()
    root_plan=(
        root.prepare_received_position_at_measurement_horizon(
            observation,state_update_indices=(0,1,2))
        if received_at_committed_horizon
        else root.prepare_position(
            observation,processing_time_s=observation.availability_time_s,
            state_update_indices=(0,1,2))
    )
    pose_plan=None; hinge=None
    if pose is not None:
        pose_plan=pose.prepare_guarded_install(correction_at_measurement,
            measurement_time_s=observation.measurement_time_s,
            availability_time_s=observation.availability_time_s,
            projection_at_source=hinge_projection_at_source,
            source_binding=native200_source_binding)
        hinge=_hinge(pose_plan.direct_hinge)
    integrity=InnovationIntegrityEvidence(
        measurement_time_s=observation.measurement_time_s,
        nis=float(root_plan.decision.nis) if root_plan.decision.nis is not None else np.inf,
        nis_limit=float(root.config.nis_limit_3d),
        measurement_integrity_valid=bool(observation.frame_valid and observation.physical_point_valid),
        covariance_model_valid=True,provenance="root_r3 prepared update decision")
    proposal=CandidateTransition(
        kind=kind,measurement_time_s=observation.measurement_time_s,
        availability_time_s=observation.availability_time_s,
        previous_published_time_s=root_token.time_s,
        previous_published_state=_root_state(root_token.state),
        imu_prediction=_root_state(root_plan.imu_prediction),
        measurement_candidate=_root_state(root_plan.measurement_candidate),
        hinge=hinge,integrity=integrity)
    decision=evaluate_candidate_transition(proposal,nominal_envelope=nominal_envelope,
        dynamic_envelope=dynamic_envelope,activity=activity,consensus=consensus,contact=contact)
    if not decision.accepted or not root_plan.decision.accepted:
        if not record_rejection:
            result=TransactionResult(decision,root_plan.decision.reason,False,False,False)
            return PreparedCausalUpdateTransaction(
                root,pose,root_token,None,None,root_plan,
                root_plan.observation,root_plan.digest,None,None,None,
                result,_CommitState())
        rejected=root.prepare_guard_rejection(root_plan,decision.reason.value)
        root._validate_publication_token(root_token)
        root_ticket=root._prevalidate_position_plan(rejected)
        root_rollback=root._prepare_position_rollback()
        result=TransactionResult(decision,root_plan.decision.reason,False,False,True)
        return PreparedCausalUpdateTransaction(
            root,pose,root_token,None,root_ticket,rejected,
            rejected.observation,rejected.digest,None,root_rollback,None,
            result,_CommitState())
    pose_ticket=None
    # Materialize all tickets and rollback ownership while state is unchanged.
    root._validate_publication_token(root_token)
    root_ticket=root._prevalidate_position_plan(root_plan)
    pose_token=None
    if pose_plan is not None:
        pose_token=pose.publication_token()
        pose_ticket=pose._prevalidate_install_plan(pose_plan)
    root_rollback=root._prepare_position_rollback()
    pose_rollback=(
        None if pose_ticket is None else pose._prepare_install_rollback()
    )
    result=TransactionResult(
        decision,root_plan.decision.reason,True,pose_ticket is not None,False)
    return PreparedCausalUpdateTransaction(
        root,pose,root_token,pose_token,root_ticket,root_plan,
        root_plan.observation,root_plan.digest,pose_ticket,
        root_rollback,pose_rollback,result,_CommitState())


def commit_causal_update_transaction(
    prepared: PreparedCausalUpdateTransaction,
    sidecar_ticket: PreparedCausalSidecarTicket | None = None,
) -> TransactionResult:
    """Atomically apply one fully prepared transaction and optional sidecar."""
    if (
        type(prepared) is not PreparedCausalUpdateTransaction
        or prepared.key is not _PREPARED_TRANSACTION_KEY
        or prepared.commit_state.consumed
    ):
        raise RuntimeError("INVALID_OR_CONSUMED_CAUSAL_TRANSACTION")
    if sidecar_ticket is not None and (
        type(sidecar_ticket) is not PreparedCausalSidecarTicket
        or sidecar_ticket.key is not _PREPARED_SIDECAR_KEY
    ):
        raise RuntimeError("INVALID_CAUSAL_TRANSACTION_SIDECAR")
    if prepared.root_ticket is None:
        if sidecar_ticket is not None:
            raise RuntimeError("REJECTED_TRANSACTION_CANNOT_CARRY_SIDECAR")
        prepared.commit_state.consumed=True
        return prepared.result
    prepared.root._prevalidate_position_plan(prepared.root_plan)
    if (
        prepared.root_plan.digest != prepared.root_plan_digest
        or prepared.root_ticket.digest != prepared.root_plan_digest
        or not _same_position_observation(
            prepared.root_observation, prepared.root_plan.observation,
        )
    ):
        raise RuntimeError("INVALID_CAUSAL_ROOT_PLAN_OBSERVATION_BINDING")
    prepared.root._validate_publication_token(prepared.root_token)
    if prepared.pose_ticket is not None:
        if prepared.pose is None or prepared.pose.publication_token() != prepared.pose_token:
            raise RuntimeError("STALE_CAUSAL_TRANSACTION_POSE")
    if sidecar_ticket is not None:
        sidecar_ticket.validate(
            sidecar_ticket.owner,sidecar_ticket.ticket,sidecar_ticket.base_token)
    prepared.commit_state.consumed=True
    sidecar_apply_started=False
    pose_apply_started=False
    root_apply_started=False
    try:
        if sidecar_ticket is not None:
            sidecar_apply_started=True
            sidecar_ticket.apply(sidecar_ticket.owner,sidecar_ticket.ticket)
        if prepared.pose_ticket is not None:
            pose_apply_started=True
            prepared.pose._apply_prevalidated_install(prepared.pose_ticket)
        root_apply_started=True
        prepared.root._apply_prevalidated_position(prepared.root_ticket)
    except BaseException:
        if root_apply_started:
            prepared.root._rollback_prevalidated_position(prepared.root_rollback)
        if pose_apply_started:
            prepared.pose._rollback_prevalidated_install(prepared.pose_rollback)
        if sidecar_apply_started:
            sidecar_ticket.rollback(sidecar_ticket.owner,sidecar_ticket.ticket)
        raise
    return prepared.result


def execute_causal_update_transaction(
    *,root: CausalDelayedRootFilter,observation: PositionObservation,
    kind: CandidateKind,nominal_envelope: ReachabilityEnvelope,
    pose: CausalArticulatedPose | None=None,
    correction_at_measurement: Mapping[str,np.ndarray] | None=None,
    hinge_projection_at_source: Mapping[str,Any] | None=None,
    native200_source_binding: Mapping[str,Any] | None=None,
    dynamic_envelope: ReachabilityEnvelope | None=None,
    activity: CausalImuActivitySummary | None=None,
    consensus: IndependentNodeConsensusEvidence | None=None,
    contact: CausalContactTransitionEvidence | None=None,
    record_rejection: bool=True,
    _precommit_hook: Callable[[],None] | None=None,
) -> TransactionResult:
    """Compatibility wrapper: exact prepare followed by atomic commit."""
    prepared=prepare_causal_update_transaction(
        root=root,observation=observation,kind=kind,
        nominal_envelope=nominal_envelope,pose=pose,
        correction_at_measurement=correction_at_measurement,
        hinge_projection_at_source=hinge_projection_at_source,
        native200_source_binding=native200_source_binding,
        dynamic_envelope=dynamic_envelope,activity=activity,
        consensus=consensus,contact=contact,record_rejection=record_rejection)
    if prepared.root_ticket is None:
        return commit_causal_update_transaction(prepared)
    if _precommit_hook is not None:
        _precommit_hook()
    return commit_causal_update_transaction(prepared)
