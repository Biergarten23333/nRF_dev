"""Owner-bound full-session raw-range, delayed-root, and velocity transaction."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json

import numpy as np

from biospur_fusion.c2_coupled_progressive.continuous_frontend import ContinuousEvent
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionUwbEventTicket,
)
from biospur_fusion.ingest.events import RecordType, TypedEvent
from biospur_fusion.root_r3 import (
    CausalDelayedRootFilter,
    PreparedRootPositionRejectionTransaction,
    PositionObservation,
    RootState,
)
from biospur_fusion.root_r3.estimator import RootPublicationToken

from .continuous_root_ab import PELVIS_NODE, uwb_row_from_event
from .diagnostic_c2_static_owner import DiagnosticC2StaticOwner
from .split_fusion import FixedLagDriftConfig, FixedLagRangeDriftCorrector
from .tight_range import (
    RawRangeDecision,
    RawRangeUpdateConfig,
    _robust_weights,
    _valid_slots,
    prepare_raw_range_update,
)
from .u0 import ClockModel


_EXPECTED_RAW_GEOMETRY_ERRORS = frozenset({
    "raw range derivative is singular",
    "raw range factor geometry failed rank/condition",
    "raw likelihood derivative is singular",
    "raw likelihood position geometry is rank deficient",
    "raw likelihood position geometry failed condition gate",
    "bounded root range derivative is singular",
    "bounded root raw geometry failed condition gate",
})


def _is_expected_raw_geometry_error(error: ValueError) -> bool:
    return type(error) is ValueError and str(error) in _EXPECTED_RAW_GEOMETRY_ERRORS


def _sha(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _ro3(value: object) -> np.ndarray:
    result = np.asarray(value, dtype=float).reshape(3).copy()
    if not np.isfinite(result).all():
        raise ValueError("compound correction is not finite")
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class PreparedC2TightRootTransaction:
    """Opaque summary; candidate state remains private to its issuing owner."""

    authority: object
    owner_revision: int
    event_id: str
    sensor_identity_digest: str
    root_base_digest: str
    raw_factor_digest: str
    raw_decision_digest: str
    likelihood_information_digest: str
    root_plan_digest: str
    drift_plan_digest: str
    measurement_applied_position_delta_m: np.ndarray
    velocity_delta_mps: np.ndarray
    ledger_after_m: np.ndarray
    digest: str

    def __post_init__(self) -> None:
        for name in (
            "measurement_applied_position_delta_m", "velocity_delta_mps",
            "ledger_after_m",
        ):
            object.__setattr__(self, name, _ro3(getattr(self, name)))


@dataclass
class _Pending:
    public: PreparedC2TightRootTransaction
    position_plan: object
    root_plan: object
    drift_plan: object


@dataclass(frozen=True)
class SkippedC2TightRootEvent:
    event_id: str
    sensor_identity_digest: str
    reason: str = "AUTHENTICATED_NON_PELVIS_UWB_AUDIT_ONLY"


@dataclass(frozen=True)
class PreparedC2TightRootRejection:
    authority: object
    owner_revision: int
    event_id: str
    sensor_identity_digest: str
    root_base_digest: str
    root_rejection_plan_digest: str
    reason: str
    digest: str


@dataclass(frozen=True)
class AuditedC2TightRootRejection:
    event_id: str
    reason: str
    root_health_committed: bool = True
    numerical_state_mutated: bool = False
    drift_mutated: bool = False
    correction_ledger_mutated: bool = False


@dataclass
class _PendingRejection:
    public: PreparedC2TightRootRejection
    position_plan: object
    root_plan: PreparedRootPositionRejectionTransaction


def _rejection_digest(plan: PreparedC2TightRootRejection) -> str:
    return _sha({
        "schema": "biospur.c2.full_session.tight_rejection.v1",
        "owner_revision": plan.owner_revision,
        "event_id": plan.event_id,
        "sensor_identity": plan.sensor_identity_digest,
        "root_base": plan.root_base_digest,
        "root_plan": plan.root_rejection_plan_digest,
        "reason": plan.reason,
        "labels_used": False,
    })


def _plan_digest(plan: PreparedC2TightRootTransaction) -> str:
    return _sha({
        "schema": "biospur.c2.full_session.tight_delayed_root.v1",
        "owner_revision": plan.owner_revision,
        "event_id": plan.event_id,
        "sensor_identity": plan.sensor_identity_digest,
        "root_base": plan.root_base_digest,
        "raw_factor": plan.raw_factor_digest,
        "raw_decision": plan.raw_decision_digest,
        "likelihood_information": plan.likelihood_information_digest,
        "root_plan": plan.root_plan_digest,
        "drift_plan": plan.drift_plan_digest,
        "measurement_applied_position_delta_m": plan.measurement_applied_position_delta_m.tolist(),
        "velocity_delta_mps": plan.velocity_delta_mps.tolist(),
        "ledger_after_m": plan.ledger_after_m.tolist(),
        "labels_used": False,
        "bias_mode": "DISABLED_VELOCITY_ONLY",
        "product_ready": False,
        "scientific_pass": False,
    })


class C2TightRangeDelayedRootOwner:
    """Stateful non-promotable coordinator for one full-session B branch."""

    def __init__(self, *, root: CausalDelayedRootFilter,
                 drift: FixedLagRangeDriftCorrector,
                 static: DiagnosticC2StaticOwner) -> None:
        if (type(root) is not CausalDelayedRootFilter
                or type(drift) is not FixedLagRangeDriftCorrector
                or type(static) is not DiagnosticC2StaticOwner):
            raise TypeError("tight root owner requires exact typed owners")
        static.validate_integrity()
        if root.config != static.root_config or drift.config != FixedLagDriftConfig():
            raise ValueError("tight root owner configuration differs from sealed defaults")
        self.__root = root
        self.__drift = drift
        self.__static = static
        self.__range_config = RawRangeUpdateConfig()
        self.__authority = object()
        self.__revision = 0
        self.__ledger = np.zeros(3)
        self.__pending: _Pending | _PendingRejection | None = None

    @property
    def product_ready(self) -> bool:
        return False

    @property
    def scientific_pass(self) -> bool:
        return False

    def owner_digest(self) -> str:
        """Return a mutation-sensitive diagnostic owner fingerprint."""

        current = self.__root.publication_token()
        return _sha({
            "schema": "biospur.c2.full_session.tight_owner_state.v1",
            "revision": self.__revision,
            "root": current.digest,
            "drift": self.__drift.owner_digest(),
            "static": self.__static.digest,
            "ledger": self.__ledger.tolist(),
            "pending": None if self.__pending is None else self.__pending.public.digest,
            "product_ready": False,
            "scientific_pass": False,
        })

    def _likelihood(
        self, state: RootState, row, clock: ClockModel,
    ) -> tuple[object, RawRangeDecision, np.ndarray, np.ndarray, str]:
        bias = self.__static.anchor_delay_m + self.__static.tag_delay_m
        prepared = prepare_raw_range_update(
            state, row, anchors_m=self.__static.anchors_m, clock=clock,
            range_bias_m=bias, tag_offset_world_m=np.zeros(3),
            tag_offset_velocity_world_mps=np.zeros(3), config=self.__range_config,
        )
        factors = prepared.factors
        ids = np.asarray(factors.anchors, dtype=int)
        anchors = self.__static.anchors_m[ids]
        dt = factors.link_epochs_s - factors.reference_epoch_s
        measured = factors.measured_ranges_m - factors.bias_mean_m
        variance = np.diag(factors.r_prior_m2)
        position = state.position_m.copy()
        velocity = state.velocity_mps.copy()
        iterations = 0
        for iterations in range(1, self.__range_config.maximum_iterations + 1):
            delta = position[None, :] + dt[:, None] * velocity[None, :] - anchors
            predicted = np.linalg.norm(delta, axis=1)
            if np.any(predicted <= 1e-9):
                raise ValueError("raw likelihood derivative is singular")
            h = delta / predicted[:, None]
            innovation = measured - predicted
            robust = _robust_weights(innovation, np.sqrt(variance), self.__range_config)
            weight = robust / variance
            information = h.T @ (weight[:, None] * h)
            rhs = h.T @ (weight * innovation)
            if np.linalg.matrix_rank(information) != 3:
                raise ValueError("raw likelihood position geometry is rank deficient")
            step = np.linalg.solve(information, rhs)
            position += step
            if np.linalg.norm(step) <= self.__range_config.convergence_tolerance:
                break
        delta = position[None, :] + dt[:, None] * velocity[None, :] - anchors
        predicted = np.linalg.norm(delta, axis=1)
        h = delta / predicted[:, None]
        innovation = measured - predicted
        robust = _robust_weights(innovation, np.sqrt(variance), self.__range_config)
        weight = robust / variance
        information = h.T @ (weight[:, None] * h)
        covariance = np.linalg.inv(information)
        pseudo = position + covariance @ (h.T @ (weight * innovation))
        singular = np.linalg.svd(information, compute_uv=False)
        condition = float(singular[0] / singular[-1])
        if not np.isfinite(condition) or condition > 1e10:
            raise ValueError("raw likelihood position geometry failed condition gate")
        decision = RawRangeDecision(
            True, "ACCEPTED_LIKELIHOOD_ONLY", tuple(int(value) for value in ids),
            factors.link_epochs_s, factors.reference_epoch_s,
            factors.measured_ranges_m, predicted, innovation,
            innovation / np.sqrt(variance), robust, np.sqrt(variance),
            3, condition, iterations, self.__range_config.uncertainty_provenance,
            np.sqrt(np.diag(factors.sensor_r_m2)),
        )
        factor_digest = _sha({
            "anchors": list(factors.anchors), "epochs": factors.link_epochs_s.tolist(),
            "measured": factors.measured_ranges_m.tolist(),
            "r_prior": factors.r_prior_m2.tolist(),
            "state_jacobian": factors.state_jacobian.tolist(),
        })
        return prepared, decision, pseudo, covariance, factor_digest

    def _drift_decision_at_bounded_position(
        self, factors, *, position_m: np.ndarray, velocity_mps: np.ndarray,
        iterations: int,
    ) -> RawRangeDecision:
        ids = np.asarray(factors.anchors, dtype=int)
        dt = factors.link_epochs_s - factors.reference_epoch_s
        delta = (np.asarray(position_m)[None, :]
                 + dt[:, None] * np.asarray(velocity_mps)[None, :]
                 - self.__static.anchors_m[ids])
        predicted = np.linalg.norm(delta, axis=1)
        if np.any(predicted <= 1e-9):
            raise ValueError("bounded root range derivative is singular")
        innovation = factors.measured_ranges_m - factors.bias_mean_m - predicted
        variance = np.diag(factors.r_prior_m2)
        robust = _robust_weights(innovation, np.sqrt(variance), self.__range_config)
        h = delta / predicted[:, None]
        information = h.T @ ((robust / variance)[:, None] * h)
        singular = np.linalg.svd(information, compute_uv=False)
        condition = float(singular[0] / singular[-1])
        if (np.linalg.matrix_rank(information) != 3
                or not np.isfinite(condition) or condition > 1e10):
            raise ValueError("bounded root raw geometry failed condition gate")
        return RawRangeDecision(
            True, "ACCEPTED_BOUND_POST_ABSOLUTE_DRIFT_RESIDUAL", tuple(int(x) for x in ids),
            factors.link_epochs_s, factors.reference_epoch_s,
            factors.measured_ranges_m, predicted, innovation,
            innovation / np.sqrt(variance), robust, np.sqrt(variance),
            3, condition, int(iterations), self.__range_config.uncertainty_provenance,
            np.sqrt(np.diag(factors.sensor_r_m2)),
        )

    def prepare(self, ticket: FullSessionUwbEventTicket, *,
                expected_root: RootPublicationToken,
                ) -> (PreparedC2TightRootTransaction
                      | PreparedC2TightRootRejection | SkippedC2TightRootEvent):
        if type(ticket) is not FullSessionUwbEventTicket:
            raise TypeError("tight root owner requires authenticated UWB ticket")
        if self.__pending is not None:
            raise RuntimeError("tight root owner already has a pending transaction")
        current = self.__root.publication_token()
        if (type(expected_root) is not RootPublicationToken
                or expected_root.authority is not current.authority
                or expected_root.digest != current.digest):
            raise RuntimeError("tight root expected publication is stale or foreign")
        staged: list[_Pending] = []
        rejected: list[_PendingRejection] = []
        skipped: list[SkippedC2TightRootEvent] = []

        def consume(event: ContinuousEvent) -> None:
            if (type(event) is not ContinuousEvent or event.kind != "UWB"
                    or type(event.payload_owner) is not TypedEvent
                    or event.payload_owner.record_type is not RecordType.UWB):
                raise TypeError("tight root owner accepts authenticated UWB only")
            row = uwb_row_from_event(event.payload_owner)
            if row.node != PELVIS_NODE:
                skipped.append(SkippedC2TightRootEvent(
                    event.event_id, ticket.sensor_identity_digest,
                ))
                return
            direct = self.__static.clocks.get(row.node)
            if direct is None or row.boot != direct.boot_epoch:
                raise ValueError("UWB node/boot is outside sealed static owner")
            if (event.uwb_timer2.strobe_timer2_us != row.strobe_us
                    or event.uwb_timer2.frame_timer2_us != row.frame_us
                    or event.common_global_ns != int(round(
                        direct.a_ns_per_us * row.strobe_us + direct.b_ns))
                    or event.availability_global_ns < int(round(
                        direct.a_ns_per_us * row.frame_us + direct.b_ns))):
                raise ValueError("UWB event timing differs from sealed clock geometry")
            clock = ClockModel(direct.boot_epoch, direct.a_ns_per_us, direct.b_ns, 0.0)
            if tuple(row.anchor_ids) != tuple(range(8)):
                skipped.append(SkippedC2TightRootEvent(
                    event.event_id, ticket.sensor_identity_digest,
                    "REJECT_ANCHOR_IDENTITY_INVALID",
                ))
                return
            valid = _valid_slots(row)
            if len(valid) < 4:
                skipped.append(SkippedC2TightRootEvent(
                    event.event_id, ticket.sensor_identity_digest,
                    "REJECT_FEWER_THAN_FOUR_LINKS",
                ))
                return
            link_ns = [direct.link_time_ns(
                event_boot_epoch=row.boot, strobe_us=row.strobe_us,
                t_round_us=row.t_round_us[index],
            ) for index in valid]
            reference_s = float(np.median(link_ns)) * 1e-9
            causal_state = self.__root.prepare_causal_state(
                reference_s, event.availability_global_ns * 1e-9,
            )
            try:
                prepared, raw, pseudo, covariance, factor_digest = self._likelihood(
                    causal_state.state, row, clock,
                )
            except ValueError as error:
                if not _is_expected_raw_geometry_error(error):
                    raise
                skipped.append(SkippedC2TightRootEvent(
                    event.event_id, ticket.sensor_identity_digest,
                    "REJECT_RAW_GEOMETRY",
                ))
                return
            observation = PositionObservation(
                reference_s, event.availability_global_ns * 1e-9,
                pseudo, covariance, event.event_id, raw.anchors,
                "FULL_SESSION_RAW_LIKELIHOOD_ONLY_DIAGNOSTIC",
                source_sequence=row.sequence,
            )
            position_plan = self.__root.prepare_position_from_causal_state(
                causal_state, observation, state_update_indices=(0, 1, 2),
            )
            if not position_plan.decision.accepted:
                root_plan = self.__root.prepare_position_rejection_transaction(position_plan)
                blank = PreparedC2TightRootRejection(
                    self.__authority, self.__revision, event.event_id,
                    ticket.sensor_identity_digest, current.digest,
                    root_plan.digest, position_plan.decision.reason, "",
                )
                public = PreparedC2TightRootRejection(**{
                    **blank.__dict__, "digest": _rejection_digest(blank),
                })
                rejected.append(_PendingRejection(public, position_plan, root_plan))
                return
            applied = position_plan.decision.applied_position_delta_m.copy()
            ledger_after = self.__ledger + applied
            drift_state = position_plan.measurement_candidate
            try:
                drift_raw = self._drift_decision_at_bounded_position(
                    prepared.factors, position_m=drift_state.position_m,
                    velocity_mps=drift_state.velocity_mps,
                    iterations=raw.iterations,
                )
            except ValueError as error:
                if not _is_expected_raw_geometry_error(error):
                    raise
                skipped.append(SkippedC2TightRootEvent(
                    event.event_id, ticket.sensor_identity_digest,
                    "REJECT_RAW_GEOMETRY",
                ))
                return
            drift_plan = self.__drift.prepare_velocity_only(
                drift_state, node=row.node, decision=drift_raw,
                anchors_m=self.__static.anchors_m,
                tag_offset_world_m=np.zeros(3),
                tag_offset_velocity_world_mps=np.zeros(3),
                range_bias_m=self.__static.anchor_delay_m + self.__static.tag_delay_m,
                cumulative_absolute_position_correction_m=ledger_after,
            )
            velocity_delta = (drift_plan.decision.velocity_delta_mps
                              if drift_plan.decision.accepted else np.zeros(3))
            root_plan = self.__root.prepare_position_velocity_transaction(
                position_plan, velocity_delta_mps=velocity_delta,
                maximum_velocity_step_mps=(
                    self.__drift.config.maximum_velocity_step_mps
                ),
                owner="C2_FULL_SESSION_FIXED_LAG_RANGE_VELOCITY_ONLY",
            )
            raw_digest = _sha({
                "accepted": drift_raw.accepted, "anchors": list(drift_raw.anchors),
                "epochs": drift_raw.link_epochs_s.tolist(),
                "measured": drift_raw.measured_ranges_m.tolist(),
                "predicted": drift_raw.predicted_ranges_m.tolist(),
                "innovation": drift_raw.innovations_m.tolist(),
                "weight": drift_raw.robust_weights.tolist(),
            })
            info_digest = _sha({"pseudo": pseudo.tolist(), "covariance": covariance.tolist()})
            blank = PreparedC2TightRootTransaction(
                self.__authority, self.__revision, event.event_id,
                ticket.sensor_identity_digest, current.digest, factor_digest,
                raw_digest, info_digest, root_plan.digest,
                drift_plan.digest, applied, velocity_delta, ledger_after, "",
            )
            public = PreparedC2TightRootTransaction(**{
                **blank.__dict__, "digest": _plan_digest(blank),
            })
            staged.append(_Pending(public, position_plan, root_plan, drift_plan))

        ticket.deliver(consume)
        if skipped:
            if staged or rejected or len(skipped) != 1:
                raise RuntimeError("full-session ticket produced mixed admission")
            return skipped[0]
        if rejected:
            if staged or len(rejected) != 1:
                raise RuntimeError("full-session ticket produced mixed rejection")
            self.__pending = rejected[0]
            return rejected[0].public
        if len(staged) != 1:
            raise RuntimeError("full-session ticket did not yield exactly one UWB")
        self.__pending = staged[0]
        return staged[0].public

    def commit(self, plan: PreparedC2TightRootTransaction | PreparedC2TightRootRejection):
        pending = self.__pending
        if type(pending) is _PendingRejection:
            if (type(plan) is not PreparedC2TightRootRejection
                    or plan is not pending.public
                    or plan.authority is not self.__authority
                    or plan.owner_revision != self.__revision
                    or not hmac.compare_digest(plan.digest, _rejection_digest(plan))):
                raise RuntimeError("STALE_FORGED_REPLAYED_OR_FOREIGN_TIGHT_REJECTION")
            self.__root.prevalidate_position_rejection_transaction(
                pending.position_plan, pending.root_plan,
            )
            root_committed = False
            try:
                decision = self.__root.commit_position_rejection_transaction(
                    pending.position_plan, pending.root_plan,
                )
                root_committed = True
                if decision.accepted or decision.reason != plan.reason:
                    raise RuntimeError("root rejection transaction changed decision")
                self.__revision += 1
                self.__pending = None
            except BaseException:
                if root_committed:
                    self.__root.rollback_committed_position_rejection_transaction(
                        pending.root_plan,
                    )
                self.__pending = None
                raise
            return AuditedC2TightRootRejection(plan.event_id, decision.reason)
        if (pending is None or plan is not pending.public
                or type(plan) is not PreparedC2TightRootTransaction
                or plan.authority is not self.__authority
                or plan.owner_revision != self.__revision
                or not hmac.compare_digest(plan.digest, _plan_digest(plan))):
            raise RuntimeError("STALE_FORGED_REPLAYED_OR_FOREIGN_TIGHT_ROOT_PLAN")
        self.__root.prevalidate_position_velocity_transaction(
            pending.position_plan, pending.root_plan,
        )
        if plan.root_plan_digest != pending.root_plan.digest:
            raise RuntimeError("FORGED_TIGHT_ROOT_CANDIDATE")
        self.__drift.prevalidate_prepared(pending.drift_plan)
        ledger_before = self.__ledger.copy()
        root_committed = False
        drift_committed = False
        try:
            position_decision = self.__root.commit_position_velocity_transaction(
                pending.position_plan, pending.root_plan,
            )
            root_committed = True
            drift_decision = self.__drift.commit_prepared(pending.drift_plan)
            drift_committed = True
            self.__ledger = plan.ledger_after_m.copy()
            self.__revision += 1
            self.__pending = None
        except BaseException:
            try:
                self.__drift.rollback_committed_prepared(pending.drift_plan)
            except RuntimeError:
                if drift_committed:
                    raise
            try:
                self.__root.rollback_committed_position_velocity_transaction(
                    pending.root_plan,
                )
            except RuntimeError:
                if root_committed:
                    raise
            self.__ledger = ledger_before
            self.__pending = None
            raise
        return position_decision, drift_decision
