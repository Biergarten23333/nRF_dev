"""Transactional continuous-session velocity drift ownership.

This owner stages the exact authenticated articulated root observation and
evaluates it only when a causal native-200 root state reaches its true
availability time.  It never mutates a root filter; callers may atomically
compose the returned bounded velocity delta with the root's own transaction.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import hmac
import math
import pickle

import numpy as np

from biospur_fusion.root_r3.models import PositionObservation, RootState

from .authoritative_articulated_fusion import (
    PreparedArticulatedAdmission,
    _prepared_admission_candidate_digest,
)
from .split_fusion import (
    DriftCorrectionDecision,
    FixedLagConsensusDriftConfig,
    FixedLagConsensusDriftCorrector,
)


def _digest(value: object) -> str:
    return hashlib.sha256(pickle.dumps(value, protocol=5)).hexdigest()


def _readonly_array(value: np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
    result = np.asarray(value, dtype=float).reshape(shape).copy()
    result.setflags(write=False)
    return result


def _readonly_observation(value: PositionObservation) -> PositionObservation:
    result = replace(
        value,
        root_position_m=_readonly_array(value.root_position_m, (3,)),
        covariance_m2=_readonly_array(value.covariance_m2, (3, 3)),
        anchors=tuple(value.anchors),
    )
    result.validate()
    return result


def _observation_digest(value: PositionObservation) -> str:
    return _digest((
        value.measurement_time_s, value.availability_time_s,
        value.root_position_m, value.covariance_m2, value.tag_id,
        value.anchors, value.quality_state, value.frame_valid,
        value.physical_point_valid, value.source_sequence,
    ))


def _readonly_decision(value: DriftCorrectionDecision) -> DriftCorrectionDecision:
    def frozen(array: np.ndarray) -> np.ndarray:
        result = np.asarray(array).copy()
        result.setflags(write=False)
        return result
    return replace(
        value,
        scaled_singular_values=frozen(value.scaled_singular_values),
        velocity_delta_mps=frozen(value.velocity_delta_mps),
        accelerometer_bias_delta_mps2=frozen(
            value.accelerometer_bias_delta_mps2
        ),
        node=frozen(value.node), anchor=frozen(value.anchor),
        lag_s=frozen(value.lag_s),
        innovation_difference_m=frozen(value.innovation_difference_m),
        effective_weight=frozen(value.effective_weight),
    )


@dataclass(frozen=True)
class ContinuousConsensusObservation:
    """One exact accepted root observation plus its real group provenance."""

    observation: PositionObservation
    availability_time_ns: int
    trusted_nodes: tuple[str, ...]
    anchors_used: tuple[int, ...]
    packet_digest: str
    epoch_digest: str
    root_plan_digest: str
    admission_digest: str
    post_absolute_position_at_measurement_m: np.ndarray
    applied_absolute_position_delta_m: np.ndarray
    cumulative_absolute_position_correction_m: np.ndarray
    digest: str


def _package_digest(value: ContinuousConsensusObservation) -> str:
    return _digest((
        _observation_digest(value.observation), value.availability_time_ns,
        value.trusted_nodes, value.anchors_used, value.packet_digest,
        value.epoch_digest, value.root_plan_digest, value.admission_digest,
        value.post_absolute_position_at_measurement_m,
        value.applied_absolute_position_delta_m,
        value.cumulative_absolute_position_correction_m,
    ))


@dataclass(frozen=True)
class ContinuousConsensusDriftResult:
    kind: str
    accepted: bool
    reason: str
    consumed_observation_digest: str | None
    decision: DriftCorrectionDecision | None
    velocity_delta_mps: np.ndarray


@dataclass(frozen=True)
class _OwnerState:
    revision: int
    corrector: FixedLagConsensusDriftCorrector
    pending: tuple[ContinuousConsensusObservation, ...]
    cumulative_absolute_position_correction_m: np.ndarray
    last_measurement_time_s: float | None
    last_availability_time_ns: int | None
    last_source_sequence: int | None
    last_consumed_native200_time_s: float | None


@dataclass
class _CommitState:
    consumed: bool = False


_PLAN_KEY = object()


@dataclass(frozen=True)
class PreparedContinuousConsensusDrift:
    authority: object
    static_digest: str
    base_revision: int
    base_owner_digest: str
    candidate_owner_digest: str
    result: ContinuousConsensusDriftResult
    digest: str
    _base_state: _OwnerState
    _candidate_state: _OwnerState
    _changed: bool
    _commit_state: _CommitState
    dependency_digest: str | None = None
    _dependency_plan: "PreparedContinuousConsensusDrift | None" = None
    key: object = _PLAN_KEY


@dataclass(frozen=True)
class ContinuousConsensusDriftSnapshot:
    authority: object
    static_digest: str
    revision: int
    owner_digest: str
    _state: _OwnerState
    digest: str


def _state_digest(state: _OwnerState) -> str:
    return _digest(state)


def _result_digest(result: ContinuousConsensusDriftResult) -> str:
    return _digest((
        result.kind, result.accepted, result.reason,
        result.consumed_observation_digest,
        result.decision,
        result.velocity_delta_mps,
    ))


def _plan_digest(plan: PreparedContinuousConsensusDrift) -> str:
    return _digest((
        plan.static_digest, plan.base_revision, plan.base_owner_digest,
        plan.candidate_owner_digest, _result_digest(plan.result), plan._changed,
        plan.dependency_digest,
    ))


class ContinuousConsensusDriftOwner:
    """Own deferred, one-at-a-time consensus velocity corrections."""

    def __init__(
        self,
        *,
        config: FixedLagConsensusDriftConfig,
        stream_owner_digest: str,
    ) -> None:
        config.validate()
        if config.acceleration_bias is not None:
            raise ValueError("continuous consensus owner is velocity-only")
        if (
            not isinstance(stream_owner_digest, str)
            or len(stream_owner_digest) != 64
            or any(character not in "0123456789abcdef" for character in stream_owner_digest)
        ):
            raise ValueError("invalid stable consensus stream owner digest")
        self.config = config
        self.stream_owner_digest = stream_owner_digest
        self.__authority = object()
        self.__consumed_plans: set[str] = set()
        self.__static_digest = _digest((config, stream_owner_digest))
        self._state = _OwnerState(
            0, FixedLagConsensusDriftCorrector(config), (),
            _readonly_array(np.zeros(3), (3,)), None, None, None, None,
        )

    @property
    def revision(self) -> int:
        return self._state.revision

    @property
    def pending_count(self) -> int:
        return len(self._state.pending)

    @property
    def cumulative_absolute_position_correction_m(self) -> np.ndarray:
        return _readonly_array(
            self._state.cumulative_absolute_position_correction_m, (3,),
        )

    @property
    def owner_digest(self) -> str:
        return _digest((self.__static_digest, _state_digest(self._state)))

    def mutable_owner_tokens(self) -> frozenset[int]:
        return frozenset((id(self), id(self._state)))

    def _copy_state(self, state: _OwnerState | None = None) -> _OwnerState:
        source = self._state if state is None else state
        cumulative = _readonly_array(
            source.cumulative_absolute_position_correction_m, (3,),
        )
        return _OwnerState(
            source.revision, deepcopy(source.corrector),
            tuple(source.pending), cumulative,
            source.last_measurement_time_s, source.last_availability_time_ns,
            source.last_source_sequence, source.last_consumed_native200_time_s,
        )

    def snapshot(self) -> ContinuousConsensusDriftSnapshot:
        state = self._copy_state()
        blank = ContinuousConsensusDriftSnapshot(
            self.__authority, self.__static_digest, state.revision,
            self.owner_digest, state, "",
        )
        return replace(blank, digest=_digest((
            blank.static_digest, blank.revision, blank.owner_digest,
            _state_digest(blank._state),
        )))

    def restore(self, snapshot: ContinuousConsensusDriftSnapshot) -> None:
        expected = _digest((
            snapshot.static_digest, snapshot.revision, snapshot.owner_digest,
            _state_digest(snapshot._state),
        )) if type(snapshot) is ContinuousConsensusDriftSnapshot else ""
        if (
            type(snapshot) is not ContinuousConsensusDriftSnapshot
            or snapshot.authority is not self.__authority
            or snapshot.static_digest != self.__static_digest
            or not hmac.compare_digest(snapshot.digest, expected)
            or snapshot.revision != snapshot._state.revision
            or snapshot.owner_digest != _digest((
                self.__static_digest, _state_digest(snapshot._state),
            ))
        ):
            raise RuntimeError("FOREIGN_OR_TAMPERED_CONSENSUS_DRIFT_SNAPSHOT")
        self._state = self._copy_state(snapshot._state)

    def clone(self) -> "ContinuousConsensusDriftOwner":
        clone = ContinuousConsensusDriftOwner(
            config=self.config, stream_owner_digest=self.stream_owner_digest,
        )
        clone._state = clone._copy_state(self._state)
        return clone

    def _prepared(
        self,
        *,
        candidate: _OwnerState,
        result: ContinuousConsensusDriftResult,
        changed: bool,
        base: _OwnerState | None = None,
        dependency_digest: str | None = None,
        dependency_plan: PreparedContinuousConsensusDrift | None = None,
    ) -> PreparedContinuousConsensusDrift:
        base = self._copy_state(self._state if base is None else base)
        candidate = self._copy_state(candidate)
        blank = PreparedContinuousConsensusDrift(
            self.__authority, self.__static_digest, base.revision,
            self.owner_digest,
            _digest((self.__static_digest, _state_digest(candidate))),
            result, "", base, candidate, changed, _CommitState(),
            dependency_digest, dependency_plan,
        )
        return replace(blank, digest=_plan_digest(blank))

    @staticmethod
    def _zero_result(kind: str, reason: str) -> ContinuousConsensusDriftResult:
        return ContinuousConsensusDriftResult(
            kind, False, reason, None, None,
            _readonly_array(np.zeros(3), (3,)),
        )

    def prepare_admission(
        self, admission: PreparedArticulatedAdmission,
    ) -> PreparedContinuousConsensusDrift:
        """Queue one exact committable B1 observation without evaluating it."""
        if (
            type(admission) is not PreparedArticulatedAdmission
            or admission.public_candidate_digest
            != _prepared_admission_candidate_digest(admission)
            or admission.causal_transaction is None
            or admission.root_observation is None
            or admission.prepared_result is None
            or not admission.prepared_result.accepted
            or admission.commit_state.consumed
        ):
            return self._prepared(
                candidate=self._state,
                result=self._zero_result("ADMISSION", "ADMISSION_REJECTED"),
                changed=False,
            )
        transaction = admission.causal_transaction
        root_plan = transaction.root_plan
        observation = admission.root_observation
        if (
            transaction.root_plan_digest != root_plan.digest
            or _observation_digest(observation)
            != _observation_digest(transaction.root_observation)
            or _observation_digest(transaction.root_observation)
            != _observation_digest(root_plan.observation)
        ):
            raise RuntimeError("UNAUTHENTICATED_CONSENSUS_ROOT_OBSERVATION")
        trusted = tuple(admission.trusted_partition)
        if not 1 <= len(trusted) <= 10 or len(set(trusted)) != len(trusted):
            raise ValueError("invalid trusted-node provenance")
        availability_ns = int(admission.packet.availability_global_ns)
        if observation.availability_time_s != availability_ns * 1e-9:
            raise ValueError("observation availability does not match group tick")
        if (
            self._state.last_measurement_time_s is not None
            and observation.measurement_time_s <= self._state.last_measurement_time_s
        ) or (
            self._state.last_availability_time_ns is not None
            and availability_ns <= self._state.last_availability_time_ns
        ) or (
            self._state.last_source_sequence is not None
            and observation.source_sequence <= self._state.last_source_sequence
        ):
            raise RuntimeError("STALE_OR_REPLAYED_CONSENSUS_ADMISSION")
        applied = _readonly_array(
            root_plan.decision.applied_position_delta_m, (3,),
        )
        # The root plan's candidate may already be repropagated to its
        # processing horizon.  Recover the exact measurement-epoch posterior
        # from the authenticated innovation and applied p-only delta instead.
        post_absolute = _readonly_array(
            observation.root_position_m
            - root_plan.decision.innovation_m
            + applied,
            (3,),
        )
        cumulative = _readonly_array(
            self._state.cumulative_absolute_position_correction_m + applied,
            (3,),
        )
        frozen_observation = _readonly_observation(observation)
        blank = ContinuousConsensusObservation(
            frozen_observation, availability_ns, trusted,
            tuple(frozen_observation.anchors), admission.packet_digest,
            admission.epoch_digest, transaction.root_plan_digest,
            admission.public_candidate_digest, post_absolute, applied,
            cumulative, "",
        )
        package = replace(blank, digest=_package_digest(blank))
        candidate = _OwnerState(
            self._state.revision + 1, deepcopy(self._state.corrector),
            self._state.pending + (package,), cumulative,
            observation.measurement_time_s, availability_ns,
            int(observation.source_sequence),
            self._state.last_consumed_native200_time_s,
        )
        result = ContinuousConsensusDriftResult(
            "ADMISSION", True, "QUEUED", package.digest, None,
            _readonly_array(np.zeros(3), (3,)),
        )
        return self._prepared(candidate=candidate, result=result, changed=True)

    def prepare_native200(
        self, state: RootState,
    ) -> PreparedContinuousConsensusDrift:
        """Evaluate at most one package against a post-IMU frame state.

        B2 deliberately owns no native-frame authority.  The future B3
        composition is responsible for passing its authenticated post-IMU
        frame state and binding the resulting plan to that frame identity.
        """
        candidate, result, changed = self._evaluate_native200(
            owner_state=self._state, state=state,
        )
        return self._prepared(
            candidate=candidate, result=result, changed=changed,
        )

    def _evaluate_native200(
        self, *, owner_state: _OwnerState, state: RootState,
    ) -> tuple[_OwnerState, ContinuousConsensusDriftResult, bool]:
        if (
            not math.isfinite(state.time_s)
            or state.vector.shape != (9,)
            or state.covariance.shape != (9, 9)
            or not np.isfinite(state.vector).all()
            or not np.isfinite(state.covariance).all()
        ):
            raise ValueError("invalid native200 root state")
        if not owner_state.pending:
            return (
                owner_state,
                self._zero_result("NATIVE200", "NO_PENDING_OBSERVATION"),
                False,
            )
        package = owner_state.pending[0]
        if _package_digest(package) != package.digest:
            raise RuntimeError("TAMPERED_PENDING_CONSENSUS_OBSERVATION")
        if state.time_s < package.availability_time_ns * 1e-9 - 1e-12:
            return (
                owner_state,
                self._zero_result("NATIVE200", "OBSERVATION_NOT_AVAILABLE"),
                False,
            )
        if (
            owner_state.last_consumed_native200_time_s is not None
            and state.time_s <= owner_state.last_consumed_native200_time_s + 1e-12
        ):
            raise RuntimeError("REPLAYED_OR_OUT_OF_ORDER_NATIVE200_DRIFT_FRAME")
        corrector = deepcopy(owner_state.corrector)
        candidate_root, decision = corrector.observe(
            state,
            observation=package.observation,
            post_absolute_position_at_measurement_m=(
                package.post_absolute_position_at_measurement_m
            ),
            cumulative_absolute_position_correction_m=(
                package.cumulative_absolute_position_correction_m
            ),
            trusted_node_count=len(package.trusted_nodes),
            total_body_nodes=10,
            stream_identity=self.stream_owner_digest,
            allow_late_processing=True,
        )
        if (
            candidate_root.position_m.tobytes() != state.position_m.tobytes()
            or candidate_root.accelerometer_bias_mps2.tobytes()
            != state.accelerometer_bias_mps2.tobytes()
            or candidate_root.covariance.tobytes() != state.covariance.tobytes()
        ):
            raise RuntimeError("CONSENSUS_DRIFT_MODIFIED_NONVELOCITY_STATE")
        velocity_delta = _readonly_array(
            candidate_root.velocity_mps - state.velocity_mps, (3,),
        )
        if (
            not np.isfinite(velocity_delta).all()
            or np.linalg.norm(velocity_delta)
            > self.config.maximum_velocity_step_mps + 1e-12
        ):
            raise RuntimeError("CONSENSUS_VELOCITY_DELTA_EXCEEDS_CONFIGURED_BOUND")
        if not decision.accepted and np.any(velocity_delta != 0.0):
            raise RuntimeError("REJECTED_CONSENSUS_DRIFT_HAS_ROOT_DELTA")
        candidate = _OwnerState(
            owner_state.revision + 1, corrector,
            owner_state.pending[1:],
            owner_state.cumulative_absolute_position_correction_m,
            owner_state.last_measurement_time_s,
            owner_state.last_availability_time_ns,
            owner_state.last_source_sequence,
            float(state.time_s),
        )
        decision = _readonly_decision(decision)
        result = ContinuousConsensusDriftResult(
            "NATIVE200", bool(decision.accepted), decision.reason,
            package.digest, decision, velocity_delta,
        )
        return candidate, result, True

    def prepare_admission_and_native200(
        self, admission: PreparedArticulatedAdmission, state: RootState,
    ) -> PreparedContinuousConsensusDrift:
        """Stage one accepted admission and the same causal native frame."""

        admission_plan = self.prepare_admission(admission)
        return self.prepare_native200_after_admission_plan(admission_plan, state)

    def prepare_native200_after_admission_plan(
        self, admission_plan: PreparedContinuousConsensusDrift,
        state: RootState,
    ) -> PreparedContinuousConsensusDrift:
        """Evaluate against the exact authenticated admission candidate."""

        self.prevalidate(admission_plan)
        if admission_plan.result.kind != "ADMISSION":
            raise ValueError("consensus dependency is not an admission plan")
        if not admission_plan.result.accepted:
            return admission_plan
        candidate, native_result, consumed = self._evaluate_native200(
            owner_state=admission_plan._candidate_state, state=state,
        )
        result = replace(native_result, kind="ADMISSION_NATIVE200")
        return self._prepared(
            base=self._state, candidate=candidate, result=result, changed=True,
            dependency_digest=admission_plan.digest,
            dependency_plan=admission_plan,
        )

    def prepare_gap(self) -> PreparedContinuousConsensusDrift:
        corrector = deepcopy(self._state.corrector)
        corrector.clear_derivative_history()
        candidate = _OwnerState(
            self._state.revision + 1, corrector, (),
            self._state.cumulative_absolute_position_correction_m,
            self._state.last_measurement_time_s,
            self._state.last_availability_time_ns,
            self._state.last_source_sequence,
            self._state.last_consumed_native200_time_s,
        )
        result = ContinuousConsensusDriftResult(
            "GAP", True, "DERIVATIVE_HISTORY_AND_PENDING_CLEARED", None, None,
            _readonly_array(np.zeros(3), (3,)),
        )
        return self._prepared(candidate=candidate, result=result, changed=True)

    def prevalidate(self, plan: PreparedContinuousConsensusDrift) -> None:
        if (
            type(plan) is not PreparedContinuousConsensusDrift
            or plan.key is not _PLAN_KEY
            or plan.authority is not self.__authority
            or plan._commit_state.consumed
            or plan.static_digest != self.__static_digest
            or plan.base_revision != self._state.revision
            or not hmac.compare_digest(plan.base_owner_digest, self.owner_digest)
            or not hmac.compare_digest(
                plan.base_owner_digest,
                _digest((self.__static_digest, _state_digest(plan._base_state))),
            )
            or not hmac.compare_digest(
                plan.candidate_owner_digest,
                _digest((self.__static_digest, _state_digest(plan._candidate_state))),
            )
            or not hmac.compare_digest(plan.digest, _plan_digest(plan))
            or (plan._changed and plan.digest in self.__consumed_plans)
        ):
            raise RuntimeError("STALE_FORGED_FOREIGN_OR_CONSUMED_CONSENSUS_DRIFT_PLAN")
        if plan.dependency_digest is not None:
            dependency = plan._dependency_plan
            if (
                type(dependency) is not PreparedContinuousConsensusDrift
                or dependency.digest != plan.dependency_digest
                or dependency._dependency_plan is not None
            ):
                raise RuntimeError("INVALID_CONSENSUS_DRIFT_PLAN_DEPENDENCY")
            self.prevalidate(dependency)

    def _install_state(self, state: _OwnerState) -> None:
        self._state = self._copy_state(state)

    def commit(
        self, plan: PreparedContinuousConsensusDrift,
    ) -> ContinuousConsensusDriftResult:
        self.prevalidate(plan)
        plan._commit_state.consumed = True
        if plan._dependency_plan is not None:
            plan._dependency_plan._commit_state.consumed = True
        if plan._changed:
            self.__consumed_plans.add(plan.digest)
        if not plan._changed:
            return plan.result
        try:
            self._install_state(plan._candidate_state)
        except BaseException:
            self._install_state(plan._base_state)
            raise
        return plan.result
