"""Separated drift and absolute raw-range corrections for Capture2.

The absolute channel owns only the world-position gauge.  It is deliberately
low gain and bounded per sweep.  The drift channel owns velocity and pelvis
accelerometer-bias error states.  It uses fixed-lag differences of the same
node--anchor raw-range innovation, so a persistent link offset and an
instantaneous Cartesian UWB fix cannot masquerade as high-rate motion.

An optional single-foot contact channel constrains velocity only.  It selects
at most one support foot and never creates a ground-position or dual-foot lock.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import hmac
from itertools import combinations
import json
import math
import pickle

import numpy as np

from biospur_fusion.root_r3.estimator import _regularize
from biospur_fusion.root_r3.models import PositionObservation, RootState

from .tight_range import RawRangeDecision, _replace_marginal_covariance


@dataclass(frozen=True)
class FixedLagDriftConfig:
    minimum_lag_s: float = 0.32
    maximum_lag_s: float = 0.72
    update_period_s: float = 0.48
    minimum_rows: int = 24
    minimum_robust_weight: float = 0.05
    temporal_huber_threshold_sigma: float = 2.0
    rank_relative_tolerance: float = 1e-2
    maximum_velocity_step_mps: float = 0.025
    maximum_accel_bias_step_mps2: float = 0.012
    covariance_floor: float = 1e-12

    def validate(self) -> None:
        if not 0.0 < self.minimum_lag_s < self.maximum_lag_s:
            raise ValueError("fixed-lag interval is invalid")
        if not self.update_period_s >= self.minimum_lag_s:
            raise ValueError("drift update period must span the minimum lag")
        if self.minimum_rows < 6:
            raise ValueError("drift correction needs at least six rows")
        positive = (
            self.minimum_robust_weight,
            self.temporal_huber_threshold_sigma,
            self.rank_relative_tolerance,
            self.maximum_velocity_step_mps,
            self.maximum_accel_bias_step_mps2,
            self.covariance_floor,
        )
        if not all(math.isfinite(value) and value > 0.0 for value in positive):
            raise ValueError("fixed-lag drift parameters must be positive")


@dataclass(frozen=True)
class DriftCorrectionDecision:
    accepted: bool
    reason: str
    reference_epoch_s: float
    row_count: int
    rank: int
    condition: float
    scaled_singular_values: np.ndarray
    velocity_delta_mps: np.ndarray
    accelerometer_bias_delta_mps2: np.ndarray
    node: np.ndarray
    anchor: np.ndarray
    lag_s: np.ndarray
    innovation_difference_m: np.ndarray
    effective_weight: np.ndarray
    bias_fit_status: str = "NOT_CONFIGURED"
    bias_fit_inlier_epochs: int = 0
    bias_fit_robust_standardized_rms: float | None = None
    bias_fit_rank: int = 0
    bias_fit_condition: float = math.inf


@dataclass(frozen=True)
class _LinkObservation:
    epoch_s: float
    innovation_m: float
    sigma_m: float
    robust_weight: float
    range_bias_m: float
    cumulative_absolute_position_correction_m: np.ndarray


@dataclass(frozen=True)
class PreparedRangeDriftUpdate:
    """One owner-bound, velocity-only drift update evaluated without mutation."""

    authority: object
    base_revision: int
    base_owner_digest: str
    candidate_owner_digest: str
    candidate_state: RootState
    decision: DriftCorrectionDecision
    digest: str
    _base_fields: dict[str, object]
    _candidate_fields: dict[str, object]


def _drift_fields_digest(fields: dict[str, object]) -> str:
    return hashlib.sha256(pickle.dumps(fields, protocol=5)).hexdigest()


def _prepared_drift_digest(plan: PreparedRangeDriftUpdate) -> str:
    return hashlib.sha256(pickle.dumps((
        plan.base_revision, plan.base_owner_digest, plan.candidate_owner_digest,
        plan.candidate_state.time_s, plan.candidate_state.vector.tobytes(),
        plan.candidate_state.covariance.tobytes(), plan.decision.accepted,
        plan.decision.reason, plan.decision.velocity_delta_mps.tobytes(),
        plan.decision.accelerometer_bias_delta_mps2.tobytes(),
    ), protocol=5)).hexdigest()


class FixedLagRangeDriftCorrector:
    """Estimate only ``[velocity, accelerometer_bias]`` drift modes.

    Each row compares the current bias-corrected innovation with an older
    innovation from the exact same node--anchor identity.  Over the lag, a
    velocity error moves the predicted tag by ``dt * dv`` and a sensor-frame
    accelerometer-bias error moves it by ``-0.5 * R * dt**2 * dba``.
    """

    def __init__(self, config: FixedLagDriftConfig = FixedLagDriftConfig()):
        config.validate()
        self.config = config
        self._history: dict[tuple[str, int], deque[_LinkObservation]] = defaultdict(deque)
        self._pending: list[tuple[str, int, float, float, float, np.ndarray]] = []
        self._last_update_s: float | None = None
        self.__transaction_authority = object()
        self.__consumed_prepared: set[str] = set()
        self._revision = 0

    def _transaction_fields(self) -> dict[str, object]:
        return {
            "history": deepcopy(self._history),
            "pending": deepcopy(self._pending),
            "last_update_s": self._last_update_s,
            "revision": self._revision,
        }

    def owner_digest(self) -> str:
        return _drift_fields_digest(self._transaction_fields())

    def _restore_transaction_fields(self, fields: dict[str, object]) -> None:
        restored = deepcopy(fields)
        self._history = restored["history"]
        self._pending = restored["pending"]
        self._last_update_s = restored["last_update_s"]
        self._revision = restored["revision"]

    def prepare_velocity_only(
        self, state: RootState, **kwargs: object,
    ) -> PreparedRangeDriftUpdate:
        """Evaluate the temporal raw-link channel with bias explicitly disabled."""

        candidate = FixedLagRangeDriftCorrector(self.config)
        candidate._history = deepcopy(self._history)
        candidate._pending = deepcopy(self._pending)
        candidate._last_update_s = self._last_update_s
        candidate._revision = self._revision
        if kwargs.pop("rotation_world_from_sensor", None) is not None:
            raise ValueError("velocity-only drift must not accept caller rotation")
        updated, decision = candidate.observe(
            state, bias_enabled=False, rotation_world_from_sensor=None, **kwargs,
        )
        candidate._revision += 1
        fields = candidate._transaction_fields()
        base_fields = self._transaction_fields()
        blank = PreparedRangeDriftUpdate(
            self.__transaction_authority, self._revision, self.owner_digest(),
            _drift_fields_digest(fields), updated, decision, "",
            base_fields, fields,
        )
        return PreparedRangeDriftUpdate(**{
            **blank.__dict__, "digest": _prepared_drift_digest(blank),
        })

    def prevalidate_prepared(self, plan: PreparedRangeDriftUpdate) -> None:
        if (
            type(plan) is not PreparedRangeDriftUpdate
            or plan.authority is not self.__transaction_authority
            or plan.digest in self.__consumed_prepared
            or plan.base_revision != self._revision
            or not hmac.compare_digest(plan.base_owner_digest, self.owner_digest())
            or not hmac.compare_digest(plan.base_owner_digest,
                                       _drift_fields_digest(plan._base_fields))
            or not hmac.compare_digest(plan.candidate_owner_digest,
                                       _drift_fields_digest(plan._candidate_fields))
            or not hmac.compare_digest(plan.digest, _prepared_drift_digest(plan))
            or np.any(plan.decision.accelerometer_bias_delta_mps2 != 0.0)
        ):
            raise RuntimeError("STALE_FORGED_OR_FOREIGN_RANGE_DRIFT_PLAN")

    def commit_prepared(self, plan: PreparedRangeDriftUpdate) -> DriftCorrectionDecision:
        self.prevalidate_prepared(plan)
        fields = deepcopy(plan._candidate_fields)
        self._history = fields["history"]
        self._pending = fields["pending"]
        self._last_update_s = fields["last_update_s"]
        self._revision = fields["revision"]
        self.__consumed_prepared.add(plan.digest)
        return plan.decision

    def rollback_committed_prepared(self, plan: PreparedRangeDriftUpdate) -> None:
        """Restore one just-committed plan while permanently consuming it."""

        if (
            type(plan) is not PreparedRangeDriftUpdate
            or plan.authority is not self.__transaction_authority
            or plan.digest not in self.__consumed_prepared
            or not hmac.compare_digest(plan.digest, _prepared_drift_digest(plan))
            or not hmac.compare_digest(
                plan.candidate_owner_digest, self.owner_digest(),
            )
            or not hmac.compare_digest(
                plan.base_owner_digest, _drift_fields_digest(plan._base_fields),
            )
        ):
            raise RuntimeError("FOREIGN_OR_NONCURRENT_RANGE_DRIFT_ROLLBACK")
        self._restore_transaction_fields(plan._base_fields)

    @staticmethod
    def _limit(vector: np.ndarray, maximum_norm: float) -> np.ndarray:
        norm = float(np.linalg.norm(vector))
        if norm <= maximum_norm:
            return vector
        return vector * (maximum_norm / norm)

    @staticmethod
    def _empty(reason: str, epoch_s: float) -> DriftCorrectionDecision:
        blank = np.empty(0, dtype=float)
        return DriftCorrectionDecision(
            False, reason, float(epoch_s), 0, 0, math.inf,
            blank, np.zeros(3), np.zeros(3), np.empty(0, dtype="U1"),
            np.empty(0, dtype=int), blank, blank, blank,
        )

    def observe(
        self,
        state: RootState,
        *,
        node: str,
        decision: RawRangeDecision,
        anchors_m: np.ndarray,
        tag_offset_world_m: np.ndarray,
        tag_offset_velocity_world_mps: np.ndarray,
        rotation_world_from_sensor: np.ndarray | None,
        range_bias_m: np.ndarray | None = None,
        cumulative_absolute_position_correction_m: np.ndarray | None = None,
        bias_enabled: bool = True,
    ) -> tuple[RootState, DriftCorrectionDecision]:
        if type(bias_enabled) is not bool:
            raise TypeError("drift bias-enable mode must be bool")
        if not decision.accepted or decision.reference_epoch_s is None:
            return state, self._empty("RAW_RANGE_UPDATE_REJECTED", state.time_s)
        epoch = float(decision.reference_epoch_s)
        anchors = np.asarray(anchors_m, float)
        offset = np.asarray(tag_offset_world_m, float)
        offset_velocity = np.asarray(tag_offset_velocity_world_mps, float)
        rotation = (
            None if rotation_world_from_sensor is None
            else np.asarray(rotation_world_from_sensor, float)
        )
        range_bias = (
            np.zeros(8) if range_bias_m is None else np.asarray(range_bias_m, float)
        )
        cumulative_absolute = (
            np.zeros(3)
            if cumulative_absolute_position_correction_m is None
            else np.asarray(cumulative_absolute_position_correction_m, float)
        )
        if anchors.shape != (8, 3) or offset.shape != (3,) or offset_velocity.shape != (3,):
            raise ValueError("invalid fixed-lag drift geometry")
        if bias_enabled:
            if (rotation is None or rotation.shape != (3, 3)
                    or not np.isfinite(rotation).all()):
                raise ValueError("invalid drift sensor rotation")
        elif rotation is not None:
            raise ValueError("velocity-only drift cannot consume sensor rotation")
        if range_bias.shape != (8,) or cumulative_absolute.shape != (3,):
            raise ValueError("invalid drift-channel correction ledger")

        for local, anchor in enumerate(decision.anchors):
            link_epoch = float(decision.link_epochs_s[local])
            history = self._history[(node, int(anchor))]
            while history and link_epoch - history[0].epoch_s > self.config.maximum_lag_s:
                history.popleft()
            candidates = [
                previous for previous in history
                if self.config.minimum_lag_s <= link_epoch - previous.epoch_s <= self.config.maximum_lag_s
            ]
            if candidates:
                previous = min(
                    candidates,
                    key=lambda row: abs((link_epoch - row.epoch_s) - self.config.maximum_lag_s),
                )
                lag = link_epoch - previous.epoch_s
                tag_position = (
                    state.position_m + offset
                    + (link_epoch - epoch) * (state.velocity_mps + offset_velocity)
                )
                delta = tag_position - anchors[int(anchor)]
                distance = float(np.linalg.norm(delta))
                if distance > 1e-9:
                    unit = delta / distance
                    design = (
                        np.r_[lag * unit, -0.5 * lag * lag * (unit @ rotation)]
                        if bias_enabled else lag * unit
                    )
                    sigma = math.hypot(float(decision.sigma_m[local]), previous.sigma_m)
                    weight = min(float(decision.robust_weights[local]), previous.robust_weight)
                    if weight >= self.config.minimum_robust_weight:
                        # Remove changes owned by the other two channels before
                        # attributing a residual slope to inertial drift. A
                        # position-gauge correction changes innovation by
                        # ``-u*dp``; a range-bias correction changes it by
                        # ``-db``.
                        innovation_difference = float(
                            decision.innovations_m[local]
                            - previous.innovation_m
                            + range_bias[int(anchor)]
                            - previous.range_bias_m
                            + unit @ (
                                cumulative_absolute
                                - previous.cumulative_absolute_position_correction_m
                            )
                        )
                        temporal_standardized = abs(innovation_difference) / sigma
                        if temporal_standardized > self.config.temporal_huber_threshold_sigma:
                            weight *= self.config.temporal_huber_threshold_sigma / temporal_standardized
                        self._pending.append((
                            node, int(anchor), lag, innovation_difference,
                            weight / (sigma * sigma), design,
                        ))
            history.append(_LinkObservation(
                link_epoch,
                float(decision.innovations_m[local]),
                float(decision.sigma_m[local]),
                float(decision.robust_weights[local]),
                float(range_bias[int(anchor)]),
                cumulative_absolute.copy(),
            ))

        if self._last_update_s is None:
            self._last_update_s = epoch
            return state, self._empty("FIXED_LAG_WARMUP", epoch)
        if epoch - self._last_update_s < self.config.update_period_s:
            return state, self._empty("UPDATE_PERIOD_NOT_REACHED", epoch)
        if len(self._pending) < self.config.minimum_rows:
            return state, self._empty("INSUFFICIENT_FIXED_LAG_ROWS", epoch)

        rows = self._pending
        design = np.stack([row[5] for row in rows])
        observed = np.asarray([row[3] for row in rows])
        inverse_variance = np.asarray([row[4] for row in rows])
        # Scale only for a dimensionally meaningful rank audit. The numerical
        # update itself uses the state's active covariance in native units.
        audit_scale = np.diag(
            [1.0, 1.0, 1.0, 0.1, 0.1, 0.1]
            if bias_enabled else [1.0, 1.0, 1.0]
        )
        whitened = np.sqrt(inverse_variance)[:, None] * design @ audit_scale
        singular = np.linalg.svd(whitened, compute_uv=False)
        tolerance = singular[0] * self.config.rank_relative_tolerance
        rank = int(np.sum(singular > tolerance))
        velocity_singular = np.linalg.svd(whitened[:, :3], compute_uv=False)
        velocity_rank = int(np.sum(velocity_singular > velocity_singular[0] * self.config.rank_relative_tolerance))
        condition = math.inf if rank == 0 else float(singular[0] / singular[rank - 1])
        if velocity_rank < 3:
            return state, DriftCorrectionDecision(
                False, "VELOCITY_DRIFT_UNOBSERVABLE", epoch, len(rows), rank, condition,
                singular, np.zeros(3), np.zeros(3), np.asarray([row[0] for row in rows]),
                np.asarray([row[1] for row in rows]), np.asarray([row[2] for row in rows]),
                observed, inverse_variance,
            )

        # H01 commonly exposes three strong modes only. In that case the
        # accelerometer-bias columns are confounded with velocity and must not
        # receive a prior-driven numerical update. Bias becomes active only
        # when all six dimensionally scaled modes pass the declared rank gate.
        bias_observable = bias_enabled and rank == 6
        active = np.arange(3, 9) if bias_observable else np.arange(3, 6)
        active_design = design if bias_observable else design[:, :3]
        prior_covariance = _regularize(
            state.covariance[np.ix_(active, active)], self.config.covariance_floor
        )
        information = np.linalg.inv(prior_covariance) + active_design.T @ (
            inverse_variance[:, None] * active_design
        )
        rhs = active_design.T @ (inverse_variance * observed)
        try:
            raw_correction = np.linalg.solve(information, rhs)
            posterior_active = _regularize(
                np.linalg.inv(information), self.config.covariance_floor
            )
        except (np.linalg.LinAlgError, ValueError):
            return state, self._empty("NUMERICAL_REJECT", epoch)

        velocity_delta = self._limit(raw_correction[:3], self.config.maximum_velocity_step_mps)
        bias_delta = (
            self._limit(raw_correction[3:], self.config.maximum_accel_bias_step_mps2)
            if bias_observable else np.zeros(3)
        )
        applied = np.r_[velocity_delta, bias_delta] if bias_observable else velocity_delta
        raw_norm = float(np.linalg.norm(raw_correction))
        applied_fraction = 1.0 if raw_norm <= 1e-15 else min(
            1.0, float(np.linalg.norm(applied)) / raw_norm
        )
        contracted = prior_covariance + applied_fraction * (
            posterior_active - prior_covariance
        )
        vector = state.vector.copy()
        vector[active] += applied
        covariance = _regularize(
            _replace_marginal_covariance(state.covariance, active, contracted),
            self.config.covariance_floor,
        )
        updated = RootState(state.time_s, vector, covariance)
        result = DriftCorrectionDecision(
            True, "ACCEPTED_FULL_DRIFT" if bias_observable else "ACCEPTED_VELOCITY_ONLY",
            epoch, len(rows), rank, condition,
            singular, velocity_delta, bias_delta, np.asarray([row[0] for row in rows]),
            np.asarray([row[1] for row in rows]), np.asarray([row[2] for row in rows]),
            observed, inverse_variance,
        )
        # A correction changes the linearization of old residuals. Restarting
        # the window keeps each accepted update internally consistent.
        self._history.clear()
        self._pending.clear()
        self._last_update_s = epoch
        return updated, result


@dataclass(frozen=True)
class _ConsensusObservation:
    epoch_s: float
    availability_time_s: float
    source_sequence: int
    source_identity: tuple[str, tuple[int, ...]]
    corrected_innovation_m: np.ndarray
    covariance_m2: np.ndarray
    position_bias_jacobian_s2: np.ndarray | None


@dataclass(frozen=True)
class ConsensusAccelerationBiasConfig:
    """Explicit observability policy for the consensus acceleration-bias fit.

    No production numbers are supplied here.  A caller must own every gate;
    omitting this policy keeps the established velocity-only behaviour.
    """

    minimum_distinct_epochs: int
    maximum_scaled_condition: float
    temporal_huber_threshold_sigma: float
    maximum_robust_standardized_rms: float
    maximum_accelerometer_bias_step_mps2: float
    rotation_owner: str
    rotation_action_id: str
    minimum_inlier_epochs: int
    maximum_bias_window_epochs: int
    maximum_candidate_subsets: int

    def validate(self) -> None:
        if (
            isinstance(self.minimum_distinct_epochs, bool)
            or self.minimum_distinct_epochs < 3
            or not self.rotation_owner
            or not self.rotation_action_id
            or isinstance(self.minimum_inlier_epochs, bool)
            or self.minimum_inlier_epochs < self.minimum_distinct_epochs
            or isinstance(self.maximum_bias_window_epochs, bool)
            or self.maximum_bias_window_epochs < self.minimum_inlier_epochs
            or isinstance(self.maximum_candidate_subsets, bool)
            or self.maximum_candidate_subsets < 1
            or any(not math.isfinite(value) or value <= 0.0 for value in (
                self.maximum_scaled_condition,
                self.temporal_huber_threshold_sigma,
                self.maximum_robust_standardized_rms,
                self.maximum_accelerometer_bias_step_mps2,
            ))
        ):
            raise ValueError("invalid consensus acceleration-bias configuration")


@dataclass(frozen=True)
class ConsensusRotationObservation:
    """Immutable causal association between consensus and native-200 rotation."""

    association_measurement_time_s: float
    source_measurement_time_s: float
    availability_time_s: float
    association_sequence: int
    action_id: str
    source_frame: int
    source_span: int
    next_source_measurement_time_s: float
    next_source_frame: int
    next_source_span: int
    tag_id: str
    anchors: tuple[int, ...]
    rotation_owner: str
    rotation_world_from_sensor: np.ndarray
    canonical_digest: str

    @staticmethod
    def _digest_payload(
        association_measurement_time_s: float,
        source_measurement_time_s: float,
        availability_time_s: float,
        association_sequence: int,
        action_id: str,
        source_frame: int,
        source_span: int,
        next_source_measurement_time_s: float,
        next_source_frame: int,
        next_source_span: int,
        tag_id: str,
        anchors: tuple[int, ...],
        rotation_owner: str,
        rotation_world_from_sensor: np.ndarray,
    ) -> str:
        payload = {
            "association_measurement_time_s": float(
                association_measurement_time_s
            ).hex(),
            "source_measurement_time_s": float(source_measurement_time_s).hex(),
            "availability_time_s": float(availability_time_s).hex(),
            "association_sequence": int(association_sequence),
            "action_id": str(action_id),
            "source_frame": int(source_frame),
            "source_span": int(source_span),
            "next_source_measurement_time_s": float(
                next_source_measurement_time_s
            ).hex(),
            "next_source_frame": int(next_source_frame),
            "next_source_span": int(next_source_span),
            "tag_id": str(tag_id),
            "anchors": [int(anchor) for anchor in anchors],
            "rotation_owner": str(rotation_owner),
            "rotation_world_from_sensor": [
                [float(value).hex() for value in row]
                for row in np.asarray(rotation_world_from_sensor, dtype=float)
            ],
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_owner(
        cls,
        *,
        association_measurement_time_s: float,
        source_measurement_time_s: float,
        availability_time_s: float,
        association_sequence: int,
        action_id: str,
        source_frame: int,
        source_span: int,
        next_source_measurement_time_s: float,
        next_source_frame: int,
        next_source_span: int,
        tag_id: str,
        anchors: tuple[int, ...],
        rotation_owner: str,
        rotation_world_from_sensor: np.ndarray,
    ) -> "ConsensusRotationObservation":
        rotation = np.asarray(rotation_world_from_sensor, dtype=float).copy()
        digest = cls._digest_payload(
            association_measurement_time_s, source_measurement_time_s,
            availability_time_s, association_sequence, action_id, source_frame,
            source_span, next_source_measurement_time_s, next_source_frame,
            next_source_span, tag_id, anchors, rotation_owner, rotation,
        )
        return cls(
            association_measurement_time_s, source_measurement_time_s,
            availability_time_s, association_sequence, action_id, source_frame,
            source_span, next_source_measurement_time_s, next_source_frame,
            next_source_span, tag_id, tuple(anchors), rotation_owner,
            rotation, digest,
        )

    def __post_init__(self) -> None:
        rotation = np.asarray(self.rotation_world_from_sensor, dtype=float).copy()
        rotation.setflags(write=False)
        object.__setattr__(self, "rotation_world_from_sensor", rotation)
        object.__setattr__(self, "anchors", tuple(self.anchors))

    def validate(self) -> None:
        rotation = self.rotation_world_from_sensor
        if (
            not math.isfinite(self.association_measurement_time_s)
            or not math.isfinite(self.source_measurement_time_s)
            or not math.isfinite(self.availability_time_s)
            or self.source_measurement_time_s
            > self.association_measurement_time_s + 1e-12
            or self.availability_time_s < self.source_measurement_time_s
            or not math.isfinite(self.next_source_measurement_time_s)
            or not (
                self.source_measurement_time_s
                <= self.association_measurement_time_s
                < self.next_source_measurement_time_s
            )
            or self.next_source_measurement_time_s
            - self.source_measurement_time_s > 0.005001
            or isinstance(self.association_sequence, bool)
            or not isinstance(self.association_sequence, (int, np.integer))
            or self.association_sequence < 0
            or not self.action_id
            or isinstance(self.source_frame, bool)
            or not isinstance(self.source_frame, (int, np.integer))
            or self.source_frame < 0
            or isinstance(self.source_span, bool)
            or not isinstance(self.source_span, (int, np.integer))
            or self.source_span < 0
            or isinstance(self.next_source_frame, bool)
            or not isinstance(self.next_source_frame, (int, np.integer))
            or self.next_source_frame != self.source_frame + 1
            or isinstance(self.next_source_span, bool)
            or not isinstance(self.next_source_span, (int, np.integer))
            or self.next_source_span != self.source_span
            or not self.tag_id
            or not self.anchors
            or not self.rotation_owner
            or rotation.shape != (3, 3)
            or not np.isfinite(rotation).all()
            or not np.allclose(rotation.T @ rotation, np.eye(3), rtol=0.0, atol=1e-8)
            or not math.isclose(float(np.linalg.det(rotation)), 1.0, rel_tol=0.0, abs_tol=1e-8)
        ):
            raise ValueError("invalid consensus rotation observation")
        expected = self._digest_payload(
            self.association_measurement_time_s, self.source_measurement_time_s,
            self.availability_time_s, self.association_sequence,
            self.action_id, self.source_frame, self.source_span,
            self.next_source_measurement_time_s, self.next_source_frame,
            self.next_source_span, self.tag_id, self.anchors,
            self.rotation_owner, rotation,
        )
        if self.canonical_digest != expected:
            raise ValueError("consensus rotation digest mismatch")


@dataclass(frozen=True)
class FixedLagConsensusDriftConfig:
    minimum_lag_s: float
    maximum_lag_s: float
    update_period_s: float
    minimum_consensus_pairs: int
    rank_relative_tolerance: float
    maximum_velocity_step_mps: float
    covariance_floor: float
    acceleration_bias: ConsensusAccelerationBiasConfig | None = None

    def validate(self) -> None:
        if (
            not 0.0 < self.minimum_lag_s < self.maximum_lag_s
            or self.update_period_s < self.minimum_lag_s
            or isinstance(self.minimum_consensus_pairs, bool)
            or self.minimum_consensus_pairs < 1
            or any(not math.isfinite(value) or value <= 0.0 for value in (
                self.rank_relative_tolerance,
                self.maximum_velocity_step_mps,
                self.covariance_floor,
            ))
        ):
            raise ValueError("invalid fixed-lag consensus drift configuration")
        if self.acceleration_bias is not None:
            self.acceleration_bias.validate()


@dataclass(frozen=True)
class _ConsensusBiasFitAudit:
    accepted: bool
    status: str
    velocity_delta_mps: np.ndarray
    accelerometer_bias_delta_mps2: np.ndarray
    rank: int
    condition: float
    singular_values: np.ndarray
    inlier_epochs: int
    robust_standardized_rms: float | None


class FixedLagConsensusDriftCorrector:
    """Causal velocity/bias drift channel for body-root consensus positions.

    Absolute UWB position corrections are removed from the finite difference
    through an explicit cumulative ledger.  Missing source intervals clear
    only this derivative history; world position, pose and calibration owners
    remain untouched.  A 3-D position history cannot independently identify
    instantaneous velocity and accelerometer bias from one difference.  When
    an explicit bias policy and rotations are supplied, a multi-offset window
    instead fits

    ``e(t) = c + (t-t0) dv + (Jp(t)-Jp(t0)) dba``

    where ``Jp`` is the causal position sensitivity obtained by integrating
    ``Jv_dot=-R_world_from_sensor``.  Bias is committed only when all nine
    scaled modes and the robust residual gate pass; otherwise the established
    velocity-only update remains authoritative.
    """

    def __init__(self, config: FixedLagConsensusDriftConfig):
        config.validate()
        self.config = config
        self._history: deque[_ConsensusObservation] = deque()
        self._pending_design: list[np.ndarray] = []
        self._pending_observed: list[np.ndarray] = []
        self._pending_inverse_variance: list[np.ndarray] = []
        self._last_update_s: float | None = None
        self._last_measurement_time_s: float | None = None
        self._last_availability_time_s: float | None = None
        self._last_source_sequence: int | None = None
        self._source_identity: tuple[str, tuple[int, ...]] | None = None
        self._bias_position_jacobian_s2 = np.zeros((3, 3), dtype=float)
        self._bias_velocity_jacobian_s = np.zeros((3, 3), dtype=float)
        self._last_bias_rotation: np.ndarray | None = None
        self._last_bias_measurement_time_s: float | None = None
        self._last_rotation_availability_time_s: float | None = None
        self._last_rotation_association_sequence: int | None = None
        self._last_rotation_source_measurement_time_s: float | None = None
        self._last_rotation_source_frame: int | None = None
        self._last_rotation_source_span: int | None = None

    def clear_derivative_history(self) -> None:
        self._history.clear()
        self._pending_design.clear()
        self._pending_observed.clear()
        self._pending_inverse_variance.clear()
        self._last_update_s = None
        self._bias_position_jacobian_s2.fill(0.0)
        self._bias_velocity_jacobian_s.fill(0.0)
        self._last_bias_rotation = None
        self._last_bias_measurement_time_s = None

    def _advance_bias_sensitivity(
        self,
        measurement_time_s: float,
        rotation_world_from_sensor: np.ndarray,
    ) -> np.ndarray | None:
        """Advance the causal zero-order-held IMU bias sensitivity."""
        if self.config.acceleration_bias is None:
            return None
        rotation = np.asarray(rotation_world_from_sensor, dtype=float)
        if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
            raise ValueError("invalid consensus bias rotation")
        if self._last_bias_measurement_time_s is not None:
            dt = measurement_time_s - self._last_bias_measurement_time_s
            if dt <= 0.0:
                raise ValueError("noncausal consensus bias rotation")
            if self._last_bias_rotation is None:
                self._bias_position_jacobian_s2.fill(0.0)
                self._bias_velocity_jacobian_s.fill(0.0)
            else:
                self._bias_position_jacobian_s2 += (
                    self._bias_velocity_jacobian_s * dt
                    - 0.5 * self._last_bias_rotation * dt * dt
                )
                self._bias_velocity_jacobian_s += -self._last_bias_rotation * dt
        self._last_bias_rotation = rotation.copy()
        self._last_bias_measurement_time_s = measurement_time_s
        frozen = self._bias_position_jacobian_s2.copy()
        frozen.setflags(write=False)
        return frozen

    def _fit_velocity_and_bias(
        self,
    ) -> _ConsensusBiasFitAudit:
        policy = self.config.acceleration_bias
        blank = np.empty(0, dtype=float)

        def reject(
            status: str,
            *,
            rank: int = 0,
            condition: float = math.inf,
            singular_values: np.ndarray = blank,
            inlier_epochs: int = 0,
            robust_standardized_rms: float | None = None,
        ) -> _ConsensusBiasFitAudit:
            return _ConsensusBiasFitAudit(
                False, status, np.zeros(3), np.zeros(3), rank, condition,
                singular_values, inlier_epochs, robust_standardized_rms,
            )

        if policy is None:
            return reject("NOT_CONFIGURED")
        rows = [item for item in self._history if item.position_bias_jacobian_s2 is not None]
        if len(rows) < policy.minimum_distinct_epochs:
            return reject("INSUFFICIENT_DISTINCT_EPOCHS")
        if len(rows) > policy.maximum_bias_window_epochs:
            return reject("BIAS_WINDOW_EPOCH_CAP_EXCEEDED")
        times = np.asarray([item.epoch_s for item in rows], dtype=float)
        if np.unique(times).size < policy.minimum_distinct_epochs:
            return reject("INSUFFICIENT_DISTINCT_EPOCHS")
        span = float(times[-1] - times[0])
        if not math.isfinite(span) or span <= 0.0:
            return reject("INVALID_BIAS_WINDOW_SPAN")
        reference = rows[0]
        blocks = []
        observed = []
        whiteners = []
        for item in rows:
            dt = float(item.epoch_s - reference.epoch_s)
            blocks.append(np.c_[
                np.eye(3),
                np.eye(3) * dt,
                item.position_bias_jacobian_s2
                - reference.position_bias_jacobian_s2,
            ])
            observed.append(item.corrected_innovation_m)
            covariance = _regularize(item.covariance_m2, self.config.covariance_floor)
            try:
                whiteners.append(np.linalg.inv(np.linalg.cholesky(covariance)))
            except np.linalg.LinAlgError:
                return reject("BIAS_COVARIANCE_NOT_FACTORIZABLE")
        design = np.concatenate(blocks, axis=0)
        values = np.concatenate(observed)
        whitening = np.zeros_like(design)
        whitened_values = np.zeros_like(values)
        for index, whitener in enumerate(whiteners):
            target = slice(3 * index, 3 * index + 3)
            whitening[target] = whitener @ design[target]
            whitened_values[target] = whitener @ values[target]
        candidate_subsets = list(combinations(range(len(rows)), 3))
        if not candidate_subsets or len(candidate_subsets) > policy.maximum_candidate_subsets:
            return reject("BIAS_CANDIDATE_SUBSET_CAP_EXCEEDED")

        def epoch_row_indices(indices: tuple[int, ...] | list[int]) -> np.ndarray:
            return np.concatenate([
                np.arange(3 * index, 3 * index + 3, dtype=int) for index in indices
            ])

        def scaled_audit(
            selected_design: np.ndarray,
            selected_times: np.ndarray,
        ) -> tuple[int, float, np.ndarray]:
            selected_span = float(selected_times[-1] - selected_times[0])
            if selected_span <= 0.0:
                return 0, math.inf, np.empty(0)
            dimensional_scale = np.diag(np.r_[
                np.ones(3),
                np.full(3, 1.0 / selected_span),
                np.full(3, 1.0 / (selected_span * selected_span)),
            ])
            singular_values = np.linalg.svd(
                selected_design @ dimensional_scale, compute_uv=False,
            )
            tolerance = singular_values[0] * self.config.rank_relative_tolerance
            selected_rank = int(np.sum(singular_values > tolerance))
            selected_condition = (
                math.inf if selected_rank == 0
                else float(singular_values[0] / singular_values[selected_rank - 1])
            )
            return selected_rank, selected_condition, singular_values

        candidates = []
        for subset in candidate_subsets:
            selected = epoch_row_indices(subset)
            candidate_design = whitening[selected]
            rank, condition, _ = scaled_audit(candidate_design, times[list(subset)])
            if rank < 9 or condition > policy.maximum_scaled_condition:
                continue
            try:
                solution = np.linalg.lstsq(
                    candidate_design, whitened_values[selected], rcond=None,
                )[0]
            except np.linalg.LinAlgError:
                continue
            residual = (whitening @ solution - whitened_values).reshape(-1, 3)
            norms = np.linalg.norm(residual, axis=1)
            inliers = tuple(np.flatnonzero(
                norms <= policy.temporal_huber_threshold_sigma,
            ).tolist())
            robust_score = float(np.sum(np.minimum(
                norms * norms, policy.temporal_huber_threshold_sigma ** 2,
            )))
            candidates.append((-len(inliers), robust_score, subset, inliers))
        if not candidates:
            return reject("NO_FULL_RANK_BIAS_CANDIDATE")
        _, _, _, inliers = min(candidates, key=lambda item: item[:3])
        if len(inliers) < policy.minimum_inlier_epochs:
            return reject("INSUFFICIENT_BIAS_INLIER_SUPPORT", inlier_epochs=len(inliers))
        selected = epoch_row_indices(list(inliers))
        selected_design = whitening[selected]
        rank, condition, singular = scaled_audit(
            selected_design, times[list(inliers)],
        )
        if rank < 9 or condition > policy.maximum_scaled_condition:
            return reject(
                "BIAS_REFIT_RANK_OR_CONDITION_REJECTED", rank=rank,
                condition=condition, singular_values=singular,
                inlier_epochs=len(inliers),
            )
        try:
            solution = np.linalg.lstsq(
                selected_design, whitened_values[selected], rcond=None,
            )[0]
        except np.linalg.LinAlgError:
            return reject(
                "BIAS_REFIT_NUMERICAL_REJECT", rank=rank,
                condition=condition, singular_values=singular,
                inlier_epochs=len(inliers),
            )
        residual = (
            selected_design @ solution - whitened_values[selected]
        ).reshape(-1, 3)
        epoch_weights = np.minimum(
            1.0,
            policy.temporal_huber_threshold_sigma
            / np.maximum(np.linalg.norm(residual, axis=1), 1e-15),
        )
        robust_rms = float(np.sqrt(
            np.sum(epoch_weights * np.sum(residual * residual, axis=1))
            / (3.0 * np.sum(epoch_weights))
        ))
        if not math.isfinite(robust_rms) or robust_rms > policy.maximum_robust_standardized_rms:
            return reject(
                "BIAS_REFIT_RESIDUAL_REJECTED", rank=rank,
                condition=condition, singular_values=singular,
                inlier_epochs=len(inliers), robust_standardized_rms=robust_rms,
            )
        return _ConsensusBiasFitAudit(
            True, "BIAS_FIT_ACCEPTED", solution[3:6], solution[6:9],
            rank, condition, singular, len(inliers), robust_rms,
        )

    def observe(
        self,
        state: RootState,
        *,
        observation: PositionObservation,
        post_absolute_position_at_measurement_m: np.ndarray,
        cumulative_absolute_position_correction_m: np.ndarray,
        trusted_node_count: int,
        total_body_nodes: int,
        rotation_observation: ConsensusRotationObservation | None = None,
        stream_identity: str | None = None,
        allow_late_processing: bool = False,
    ) -> tuple[RootState, DriftCorrectionDecision]:
        processing_epoch = float(state.time_s)
        try:
            observation.validate()
        except (ValueError, np.linalg.LinAlgError):
            return state, FixedLagRangeDriftCorrector._empty(
                "INVALID_POSITION_OBSERVATION", processing_epoch,
            )
        measurement_time_s = float(observation.measurement_time_s)
        availability_time_s = float(observation.availability_time_s)
        if not observation.frame_valid or not observation.physical_point_valid:
            return state, FixedLagRangeDriftCorrector._empty(
                "POSITION_OBSERVATION_REJECTED", processing_epoch,
            )
        if (
            isinstance(observation.source_sequence, bool)
            or not isinstance(observation.source_sequence, (int, np.integer))
            or observation.source_sequence < 0
        ):
            return state, FixedLagRangeDriftCorrector._empty(
                "INVALID_SOURCE_SEQUENCE", processing_epoch,
            )
        if stream_identity is not None and (
            not isinstance(stream_identity, str) or not stream_identity
        ):
            raise ValueError("invalid consensus stream identity")
        if type(allow_late_processing) is not bool:
            raise TypeError("late-processing mode must be bool")
        source_identity = (
            (str(observation.tag_id), tuple(observation.anchors))
            if stream_identity is None else (stream_identity, ())
        )
        if self._source_identity is not None and source_identity != self._source_identity:
            return state, FixedLagRangeDriftCorrector._empty(
                "SOURCE_IDENTITY_MISMATCH", processing_epoch,
            )
        if (
            self._last_measurement_time_s is not None
            and measurement_time_s <= self._last_measurement_time_s
        ):
            return state, FixedLagRangeDriftCorrector._empty(
                "STALE_OR_REPLAYED_MEASUREMENT", processing_epoch,
            )
        if (
            self._last_availability_time_s is not None
            and availability_time_s <= self._last_availability_time_s
        ):
            return state, FixedLagRangeDriftCorrector._empty(
                "STALE_OR_REPLAYED_AVAILABILITY", processing_epoch,
            )
        if (
            self._last_source_sequence is not None
            and int(observation.source_sequence) <= self._last_source_sequence
        ):
            return state, FixedLagRangeDriftCorrector._empty(
                "STALE_OR_REPLAYED_SOURCE_SEQUENCE", processing_epoch,
            )
        if (
            processing_epoch < availability_time_s - 1e-12
            or (
                not allow_late_processing
                and abs(availability_time_s - processing_epoch) > 1e-12
            )
        ):
            return state, FixedLagRangeDriftCorrector._empty(
                "STATE_NOT_AT_OBSERVATION_AVAILABILITY", processing_epoch,
            )
        if measurement_time_s > availability_time_s + 1e-12:
            return state, FixedLagRangeDriftCorrector._empty(
                "OBSERVATION_NOT_AVAILABLE", processing_epoch,
            )

        rotation: np.ndarray | None = None
        if self.config.acceleration_bias is not None:
            if rotation_observation is None:
                return state, FixedLagRangeDriftCorrector._empty(
                    "MISSING_BIAS_ROTATION_OBSERVATION", processing_epoch,
                )
            try:
                rotation_observation.validate()
            except (ValueError, np.linalg.LinAlgError):
                return state, FixedLagRangeDriftCorrector._empty(
                    "INVALID_BIAS_ROTATION_OBSERVATION", processing_epoch,
                )
            if (
                rotation_observation.association_measurement_time_s
                != measurement_time_s
                or rotation_observation.availability_time_s > availability_time_s
                or rotation_observation.association_sequence
                != int(observation.source_sequence)
                or rotation_observation.tag_id != observation.tag_id
                or rotation_observation.anchors != tuple(observation.anchors)
                or rotation_observation.rotation_owner
                != self.config.acceleration_bias.rotation_owner
                or rotation_observation.action_id
                != self.config.acceleration_bias.rotation_action_id
            ):
                return state, FixedLagRangeDriftCorrector._empty(
                    "BIAS_ROTATION_ASSOCIATION_MISMATCH", processing_epoch,
                )
            if (
                self._last_rotation_source_measurement_time_s is not None
                and rotation_observation.source_measurement_time_s
                <= self._last_rotation_source_measurement_time_s
            ) or (
                self._last_rotation_availability_time_s is not None
                and rotation_observation.availability_time_s
                <= self._last_rotation_availability_time_s
            ) or (
                self._last_rotation_association_sequence is not None
                and rotation_observation.association_sequence
                <= self._last_rotation_association_sequence
            ) or (
                self._last_rotation_source_span is not None
                and (
                    rotation_observation.source_span,
                    rotation_observation.source_frame,
                )
                <= (
                    self._last_rotation_source_span,
                    self._last_rotation_source_frame,
                )
            ):
                return state, FixedLagRangeDriftCorrector._empty(
                    "STALE_OR_REPLAYED_BIAS_ROTATION", processing_epoch,
                )
            rotation = rotation_observation.rotation_world_from_sensor

        position = np.asarray(observation.root_position_m, dtype=float).reshape(3)
        post_absolute_at_measurement = np.asarray(
            post_absolute_position_at_measurement_m, dtype=float,
        ).reshape(3)
        covariance = np.asarray(observation.covariance_m2, dtype=float).reshape(3, 3)
        cumulative = np.asarray(
            cumulative_absolute_position_correction_m, dtype=float,
        ).reshape(3)
        if (
            not np.isfinite(position).all()
            or not np.isfinite(post_absolute_at_measurement).all()
            or not np.isfinite(covariance).all() or not np.isfinite(cumulative).all()
            or not np.allclose(covariance, covariance.T, rtol=0.0, atol=1e-12)
            or np.min(np.linalg.eigvalsh(covariance)) <= 0.0
            or isinstance(trusted_node_count, bool) or trusted_node_count < 1
            or isinstance(total_body_nodes, bool) or total_body_nodes != 10
            or trusted_node_count > total_body_nodes
        ):
            raise ValueError("invalid body-consensus drift observation")
        # The ledger includes the current absolute correction, so the paired
        # state position must be the post-absolute value. This algebraically
        # removes all absolute-gauge commits through the current epoch.
        corrected = position - post_absolute_at_measurement + cumulative
        while self._history and measurement_time_s - self._history[0].epoch_s > self.config.maximum_lag_s:
            self._history.popleft()
        candidates = [
            previous for previous in self._history
            if self.config.minimum_lag_s
            <= measurement_time_s - previous.epoch_s
            <= self.config.maximum_lag_s
        ]
        if candidates:
            previous = min(
                candidates,
                key=lambda item: abs(
                    (measurement_time_s - item.epoch_s) - self.config.maximum_lag_s
                ),
            )
            lag = measurement_time_s - previous.epoch_s
            difference = corrected - previous.corrected_innovation_m
            variance = np.diag(covariance + previous.covariance_m2)
            self._pending_design.append(np.eye(3) * lag)
            self._pending_observed.append(difference)
            self._pending_inverse_variance.append(1.0 / variance)
        bias_jacobian = self._advance_bias_sensitivity(
            measurement_time_s, rotation,
        )
        frozen_corrected = corrected.copy(); frozen_corrected.setflags(write=False)
        frozen_covariance = covariance.copy(); frozen_covariance.setflags(write=False)
        self._history.append(_ConsensusObservation(
            measurement_time_s, availability_time_s, int(observation.source_sequence),
            source_identity, frozen_corrected, frozen_covariance, bias_jacobian,
        ))
        self._last_measurement_time_s = measurement_time_s
        self._last_availability_time_s = availability_time_s
        self._last_source_sequence = int(observation.source_sequence)
        self._source_identity = source_identity
        if rotation_observation is not None:
            self._last_rotation_availability_time_s = (
                rotation_observation.availability_time_s
            )
            self._last_rotation_association_sequence = (
                rotation_observation.association_sequence
            )
            self._last_rotation_source_measurement_time_s = (
                rotation_observation.source_measurement_time_s
            )
            self._last_rotation_source_frame = rotation_observation.source_frame
            self._last_rotation_source_span = rotation_observation.source_span

        if self._last_update_s is None:
            self._last_update_s = measurement_time_s
            return state, FixedLagRangeDriftCorrector._empty(
                "FIXED_LAG_WARMUP", measurement_time_s,
            )
        if measurement_time_s - self._last_update_s < self.config.update_period_s:
            return state, FixedLagRangeDriftCorrector._empty(
                "UPDATE_PERIOD_NOT_REACHED", measurement_time_s,
            )
        pair_count = len(self._pending_design)
        row_count = 3 * pair_count
        if pair_count < self.config.minimum_consensus_pairs:
            return state, FixedLagRangeDriftCorrector._empty(
                "INSUFFICIENT_FIXED_LAG_ROWS", measurement_time_s,
            )
        design = np.concatenate(self._pending_design, axis=0)
        observed = np.concatenate(self._pending_observed)
        inverse_variance = np.concatenate(self._pending_inverse_variance)
        whitened = np.sqrt(inverse_variance)[:, None] * design
        singular = np.linalg.svd(whitened, compute_uv=False)
        tolerance = singular[0] * self.config.rank_relative_tolerance
        rank = int(np.sum(singular > tolerance))
        condition = math.inf if rank == 0 else float(singular[0] / singular[rank - 1])
        if rank < 3:
            return state, DriftCorrectionDecision(
                False, "VELOCITY_DRIFT_UNOBSERVABLE", measurement_time_s, row_count,
                rank, condition, singular, np.zeros(3), np.zeros(3),
                np.full(row_count, "BODY_CONSENSUS", dtype="U14"),
                np.full(row_count, -1, dtype=int),
                np.repeat(np.asarray([item[0, 0] for item in self._pending_design]), 3),
                observed, inverse_variance,
            )
        prior = _regularize(
            state.covariance[3:6, 3:6], self.config.covariance_floor,
        )
        information = np.linalg.inv(prior) + design.T @ (
            inverse_variance[:, None] * design
        )
        rhs = design.T @ (inverse_variance * observed)
        raw = np.linalg.solve(information, rhs)
        bias_fit = self._fit_velocity_and_bias()
        if not bias_fit.accepted:
            velocity_delta = FixedLagRangeDriftCorrector._limit(
                raw, self.config.maximum_velocity_step_mps,
            )
            bias_delta = np.zeros(3)
            reason = "ACCEPTED_CONSENSUS_VELOCITY_ONLY"
            decision_rank = rank
            decision_condition = condition
            decision_singular = singular
        else:
            velocity_delta = FixedLagRangeDriftCorrector._limit(
                bias_fit.velocity_delta_mps, self.config.maximum_velocity_step_mps,
            )
            bias_delta = FixedLagRangeDriftCorrector._limit(
                bias_fit.accelerometer_bias_delta_mps2,
                self.config.acceleration_bias.maximum_accelerometer_bias_step_mps2,
            )
            reason = "ACCEPTED_CONSENSUS_VELOCITY_AND_ACCELEROMETER_BIAS"
            decision_rank = bias_fit.rank
            decision_condition = bias_fit.condition
            decision_singular = bias_fit.singular_values
        vector = state.vector.copy()
        vector[3:6] += velocity_delta
        vector[6:9] += bias_delta
        # The finite-difference covariance owns the velocity estimate's
        # weighting, but this adapter has no independently qualified process
        # cross-covariance with the live delayed filter.  Keep the live
        # covariance byte-exact and commit only the bounded velocity mean via
        # that filter's authoritative current-constraint journal.
        updated = RootState(state.time_s, vector, state.covariance.copy())
        result = DriftCorrectionDecision(
            True, reason, measurement_time_s, row_count,
            decision_rank, decision_condition, decision_singular, velocity_delta, bias_delta,
            np.full(row_count, "BODY_CONSENSUS", dtype="U14"),
            np.full(row_count, -1, dtype=int),
            np.repeat(np.asarray([item[0, 0] for item in self._pending_design]), 3),
            observed, inverse_variance,
            bias_fit.status, bias_fit.inlier_epochs,
            bias_fit.robust_standardized_rms, bias_fit.rank,
            bias_fit.condition,
        )
        self._history.clear()
        self._pending_design.clear()
        self._pending_observed.clear()
        self._pending_inverse_variance.clear()
        self._last_update_s = measurement_time_s
        self._bias_position_jacobian_s2.fill(0.0)
        self._bias_velocity_jacobian_s.fill(0.0)
        self._last_bias_rotation = None
        self._last_bias_measurement_time_s = None
        return updated, result


@dataclass(frozen=True)
class SingleFootContactConfig:
    enabled: bool = False
    update_period_s: float = 0.05
    maximum_relative_speed_mps: float = 0.45
    velocity_sigma_mps: float = 0.18
    correction_gain: float = 0.20
    maximum_velocity_step_mps: float = 0.04
    minimum_side_dwell_s: float = 0.20
    switch_height_hysteresis_m: float = 0.03
    covariance_floor: float = 1e-12

    def validate(self) -> None:
        values = (
            self.update_period_s,
            self.maximum_relative_speed_mps,
            self.velocity_sigma_mps,
            self.correction_gain,
            self.maximum_velocity_step_mps,
            self.minimum_side_dwell_s,
            self.switch_height_hysteresis_m,
            self.covariance_floor,
        )
        if not all(math.isfinite(value) and value > 0.0 for value in values):
            raise ValueError("single-foot contact parameters must be positive")
        if self.correction_gain > 1.0:
            raise ValueError("single-foot contact gain cannot exceed one")


@dataclass(frozen=True)
class ContactDecision:
    accepted: bool
    reason: str
    side: str | None
    velocity_innovation_mps: np.ndarray
    applied_velocity_delta_mps: np.ndarray


class SingleFootVelocityCorrector:
    """Switchable one-foot near-zero-velocity information channel."""

    def __init__(self, config: SingleFootContactConfig = SingleFootContactConfig()):
        config.validate()
        self.config = config
        self._last_update_s: float | None = None
        self._active_side: str | None = None
        self._side_since_s: float | None = None

    def update(
        self,
        state: RootState,
        *,
        ankle_offset_world_m: dict[str, np.ndarray],
        ankle_offset_velocity_world_mps: dict[str, np.ndarray],
    ) -> tuple[RootState, ContactDecision]:
        zero = np.zeros(3)
        if not self.config.enabled:
            return state, ContactDecision(False, "DISABLED", None, zero, zero)
        if self._last_update_s is not None and state.time_s - self._last_update_s < self.config.update_period_s:
            return state, ContactDecision(False, "UPDATE_PERIOD_NOT_REACHED", None, zero, zero)
        candidates = {}
        for side in ("left", "right"):
            offset = np.asarray(ankle_offset_world_m[side], float)
            velocity = np.asarray(ankle_offset_velocity_world_mps[side], float)
            if offset.shape != (3,) or velocity.shape != (3,):
                raise ValueError("invalid ankle proxy")
            relative_speed = float(np.linalg.norm(velocity))
            if relative_speed <= self.config.maximum_relative_speed_mps:
                candidates[side] = (float(offset[2]), relative_speed, velocity)
        if not candidates:
            self._active_side = None
            self._side_since_s = None
            return state, ContactDecision(False, "NO_NEAR_STATIONARY_FOOT", None, zero, zero)
        # Height is the primary switch and speed breaks ties. Only one row can
        # win, even if both feet satisfy the near-stationary test.
        best_side = min(candidates, key=lambda key: candidates[key][:2])
        side = best_side
        if self._active_side in candidates:
            active_height = candidates[self._active_side][0]
            best_height = candidates[best_side][0]
            dwell = (
                math.inf if self._side_since_s is None
                else state.time_s - self._side_since_s
            )
            if (
                dwell < self.config.minimum_side_dwell_s
                or active_height <= best_height + self.config.switch_height_hysteresis_m
            ):
                side = self._active_side
        if side != self._active_side:
            self._active_side = side
            self._side_since_s = state.time_s
        relative_velocity = candidates[side][2]
        target_root_velocity = -relative_velocity
        innovation = target_root_velocity - state.velocity_mps
        prior = state.covariance[3:6, 3:6]
        observation = np.eye(3) * self.config.velocity_sigma_mps**2
        gain = prior @ np.linalg.inv(prior + observation)
        raw_delta = self.config.correction_gain * (gain @ innovation)
        norm = float(np.linalg.norm(raw_delta))
        clamp_fraction = (
            1.0 if norm <= self.config.maximum_velocity_step_mps
            else self.config.maximum_velocity_step_mps / norm
        )
        delta = raw_delta * clamp_fraction
        vector = state.vector.copy()
        vector[3:6] += delta
        contraction = min(1.0, self.config.correction_gain * clamp_fraction)
        posterior = prior - contraction * (gain @ prior)
        covariance = _regularize(
            _replace_marginal_covariance(state.covariance, np.arange(3, 6), posterior),
            self.config.covariance_floor,
        )
        self._last_update_s = state.time_s
        return RootState(state.time_s, vector, covariance), ContactDecision(
            True, "ACCEPTED_SINGLE_FOOT_VELOCITY_ONLY", side, innovation, delta,
        )
