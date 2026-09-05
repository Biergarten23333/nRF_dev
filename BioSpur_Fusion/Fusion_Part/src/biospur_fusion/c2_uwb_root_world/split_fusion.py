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
from dataclasses import dataclass
import math

import numpy as np

from biospur_fusion.root_r3.estimator import _regularize
from biospur_fusion.root_r3.models import RootState

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


@dataclass(frozen=True)
class _LinkObservation:
    epoch_s: float
    innovation_m: float
    sigma_m: float
    robust_weight: float
    range_bias_m: float
    cumulative_absolute_position_correction_m: np.ndarray


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
        rotation_world_from_sensor: np.ndarray,
        range_bias_m: np.ndarray | None = None,
        cumulative_absolute_position_correction_m: np.ndarray | None = None,
    ) -> tuple[RootState, DriftCorrectionDecision]:
        if not decision.accepted or decision.reference_epoch_s is None:
            return state, self._empty("RAW_RANGE_UPDATE_REJECTED", state.time_s)
        epoch = float(decision.reference_epoch_s)
        anchors = np.asarray(anchors_m, float)
        offset = np.asarray(tag_offset_world_m, float)
        offset_velocity = np.asarray(tag_offset_velocity_world_mps, float)
        rotation = np.asarray(rotation_world_from_sensor, float)
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
        if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
            raise ValueError("invalid drift sensor rotation")
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
                    design = np.r_[
                        lag * unit,
                        -0.5 * lag * lag * (unit @ rotation),
                    ]
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
        audit_scale = np.diag([1.0, 1.0, 1.0, 0.1, 0.1, 0.1])
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
        bias_observable = rank == 6
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
