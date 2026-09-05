"""Raw-range updates for the C2 pelvis navigation state.

The UWB observation owned here is one 120 ms broadcast sweep, not a solved
Cartesian position.  Every valid A--H link contributes one scalar factor at
its measured ``strobe_us + t_round_us / 2`` epoch.  The state is the existing
Root-R3 ``[position, velocity, accelerometer bias]`` state; IMU propagation is
owned by :mod:`biospur_fusion.root_r3.estimator`.

This module deliberately has no dependency on T4 or ``PositionObservation``.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from biospur_fusion.root_r3.estimator import _regularize
from biospur_fusion.root_r3.models import RootState
from biospur_fusion.time.common_clock import SUPERFRAME_US

from .u0 import ClockModel, UwbRow


UWB_SWEEP_PERIOD_US = int(SUPERFRAME_US)
UWB_SWEEP_RATE_HZ = 1_000_000.0 / UWB_SWEEP_PERIOD_US


@dataclass(frozen=True)
class RawRangeUpdateConfig:
    """Provisional robust measurement model for an uncalibrated capture.

    ``nominal_sigma_m`` and ``huber_threshold_sigma`` must be replaced by the
    measured per-link residual model before a scientific result is claimed.
    Keeping them explicit prevents a diagnostic default from becoming hidden
    calibration data.
    """

    nominal_sigma_m: float = 0.12
    huber_threshold_sigma: float = 2.5
    maximum_iterations: int = 8
    convergence_tolerance: float = 1e-7
    covariance_floor: float = 1e-12
    positive_nlos_cauchy_scale_m: float | None = None
    uncertainty_provenance: str = "PROVISIONAL_UNCALIBRATED_DIAGNOSTIC"

    def validate(self) -> None:
        if not math.isclose(UWB_SWEEP_RATE_HZ, 25.0 / 3.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("C2 UWB cadence is not exactly 120 ms / 8.333 Hz")
        if not math.isfinite(self.nominal_sigma_m) or self.nominal_sigma_m <= 0.0:
            raise ValueError("nominal range sigma must be positive")
        if not math.isfinite(self.huber_threshold_sigma) or self.huber_threshold_sigma <= 0.0:
            raise ValueError("Huber threshold must be positive")
        if self.maximum_iterations < 1:
            raise ValueError("at least one robust iteration is required")
        if (
            self.positive_nlos_cauchy_scale_m is not None
            and (
                not math.isfinite(self.positive_nlos_cauchy_scale_m)
                or self.positive_nlos_cauchy_scale_m <= 0.0
            )
        ):
            raise ValueError("positive NLOS Cauchy scale must be positive when enabled")
        if not self.uncertainty_provenance:
            raise ValueError("range uncertainty provenance is mandatory")


@dataclass(frozen=True)
class RawRangeDecision:
    accepted: bool
    reason: str
    anchors: tuple[int, ...]
    link_epochs_s: np.ndarray
    reference_epoch_s: float | None
    measured_ranges_m: np.ndarray
    predicted_ranges_m: np.ndarray
    innovations_m: np.ndarray
    standardized_innovations: np.ndarray
    robust_weights: np.ndarray
    sigma_m: np.ndarray
    rank: int
    condition: float
    iterations: int
    uncertainty_provenance: str


@dataclass(frozen=True)
class PersistentRangeBiasConfig:
    """Causal non-negative nuisance state for persistent body-blocked links."""

    initial_sigma_m: float = 0.30
    random_walk_sigma_m_sqrt_s: float = 0.05
    measurement_sigma_floor_m: float = 0.08
    maximum_bias_m: float = 3.0

    def validate(self) -> None:
        values = (
            self.initial_sigma_m,
            self.random_walk_sigma_m_sqrt_s,
            self.measurement_sigma_floor_m,
            self.maximum_bias_m,
        )
        if not all(math.isfinite(value) and value > 0.0 for value in values):
            raise ValueError("persistent range-bias parameters must be positive")


class PersistentRangeBiasTracker:
    """Track one non-negative bias for every node--anchor link.

    The tracker consumes post-root innovations and supplies the bias prior to
    the next raw-range update.  It is intentionally causal and never creates a
    Cartesian UWB position.  Root/bias cross-covariance is not retained, so
    this remains a diagnostic nuisance-state approximation rather than a
    scientific full-state smoother.
    """

    def __init__(self, config: PersistentRangeBiasConfig = PersistentRangeBiasConfig()):
        config.validate()
        self.config = config
        self._mean: dict[str, np.ndarray] = {}
        self._variance: dict[str, np.ndarray] = {}
        self._last_time: dict[str, np.ndarray] = {}

    def _ensure(self, node: str) -> None:
        if node not in self._mean:
            self._mean[node] = np.zeros(8, dtype=float)
            self._variance[node] = np.full(8, self.config.initial_sigma_m**2)
            self._last_time[node] = np.full(8, np.nan)

    def bias_vector(self, node: str) -> np.ndarray:
        self._ensure(node)
        return self._mean[node].copy()

    def update(self, node: str, decision: RawRangeDecision) -> np.ndarray:
        self._ensure(node)
        if decision.reference_epoch_s is None:
            return self.bias_vector(node)
        means = self._mean[node]
        variances = self._variance[node]
        last = self._last_time[node]
        for local, anchor in enumerate(decision.anchors):
            epoch = float(decision.link_epochs_s[local])
            dt = 0.0 if not math.isfinite(last[anchor]) else max(0.0, epoch - last[anchor])
            predicted_variance = (
                variances[anchor]
                + self.config.random_walk_sigma_m_sqrt_s**2 * dt
            )
            measurement_variance = max(
                float(decision.sigma_m[local]),
                self.config.measurement_sigma_floor_m,
            ) ** 2
            gain = predicted_variance / (predicted_variance + measurement_variance)
            # The decision innovation has the prior bias removed. Add it back
            # to obtain the observed non-negative nuisance component.
            observed_bias = float(decision.innovations_m[local] + means[anchor])
            means[anchor] = float(np.clip(
                means[anchor] + gain * (observed_bias - means[anchor]),
                0.0,
                self.config.maximum_bias_m,
            ))
            variances[anchor] = max((1.0 - gain) * predicted_variance, 1e-12)
            last[anchor] = epoch
        return means.copy()

    def snapshot(self) -> dict[str, list[float]]:
        return {node: values.tolist() for node, values in sorted(self._mean.items())}


def _empty_decision(reason: str, config: RawRangeUpdateConfig) -> RawRangeDecision:
    blank = np.empty(0, dtype=float)
    return RawRangeDecision(
        False, reason, (), blank, None, blank, blank, blank, blank, blank,
        blank, 0, math.inf, 0, config.uncertainty_provenance,
    )


def _valid_slots(row: UwbRow) -> list[int]:
    return [
        slot for slot in range(8)
        if row.valid_mask & (1 << slot) and 0 < row.ranges_mm[slot] < 0xFFFF
    ]


def _robust_weights(
    innovation_m: np.ndarray,
    sigma_m: np.ndarray,
    config: RawRangeUpdateConfig,
) -> np.ndarray:
    """Return symmetric Huber weights with an optional positive NLOS tail.

    Body obstruction normally lengthens a UWB range.  When explicitly
    enabled, positive innovations receive the influence function of a Cauchy
    long-tail component in addition to the ordinary Huber guard.  Negative
    innovations retain the symmetric Huber model and therefore continue to
    constrain the state.  This is a measurement likelihood, not range
    deletion or solved-position preprocessing.
    """

    standardized = innovation_m / sigma_m
    magnitude = np.abs(standardized)
    weights = np.ones_like(magnitude)
    outside = magnitude > config.huber_threshold_sigma
    weights[outside] = config.huber_threshold_sigma / magnitude[outside]
    scale = config.positive_nlos_cauchy_scale_m
    if scale is not None:
        positive = innovation_m > 0.0
        ratio = innovation_m[positive] / scale
        weights[positive] *= 1.0 / (1.0 + np.square(ratio))
    return weights


def _replace_marginal_covariance(
    covariance: np.ndarray,
    indices: np.ndarray,
    replacement: np.ndarray,
) -> np.ndarray:
    """Replace one covariance marginal while preserving its conditional.

    A naive block assignment can make the complete covariance indefinite when
    the selected state has cross-covariance with the remainder.  This helper
    retains the old conditional distribution of the remainder given the
    selected block and only changes the selected marginal.
    """

    size = covariance.shape[0]
    remainder = np.asarray([index for index in range(size) if index not in indices])
    if not len(remainder):
        return replacement
    selected = covariance[np.ix_(indices, indices)]
    cross = covariance[np.ix_(remainder, indices)]
    regression = cross @ np.linalg.inv(selected)
    conditional = covariance[np.ix_(remainder, remainder)] - regression @ selected @ regression.T
    result = np.empty_like(covariance)
    result[np.ix_(indices, indices)] = replacement
    result[np.ix_(remainder, indices)] = regression @ replacement
    result[np.ix_(indices, remainder)] = result[np.ix_(remainder, indices)].T
    result[np.ix_(remainder, remainder)] = conditional + regression @ replacement @ regression.T
    return result


def update_raw_ranges(
    state: RootState,
    row: UwbRow,
    *,
    anchors_m: np.ndarray,
    clock: ClockModel,
    range_bias_m: np.ndarray | None = None,
    tag_offset_world_m: np.ndarray | None = None,
    tag_offset_velocity_world_mps: np.ndarray | None = None,
    state_update_indices: tuple[int, ...] | None = None,
    correction_gain: float = 1.0,
    maximum_position_step_m: float | None = None,
    config: RawRangeUpdateConfig = RawRangeUpdateConfig(),
) -> tuple[RootState, RawRangeDecision]:
    """Apply one joint robust MAP update from the raw links in ``row``.

    The caller must propagate ``state`` to the median link epoch first.  The
    measured per-anchor round-trip intervals then place each scalar factor at
    its own epoch using the state's velocity over the few-millisecond sweep.
    ``tag_offset_world_m`` permits several body-mounted tags to constrain one
    shared root without first solving Cartesian tag positions.  Its value and
    derivative must come from an explicitly owned body model. Missing links
    simply omit their factors.
    """

    config.validate()
    active = np.asarray(
        tuple(range(9)) if state_update_indices is None else state_update_indices,
        dtype=int,
    )
    if active.ndim != 1 or not len(active) or len(np.unique(active)) != len(active):
        raise ValueError("state_update_indices must contain unique state indices")
    if np.any(active < 0) or np.any(active >= 9):
        raise ValueError("state_update_indices outside RootState")
    if not math.isfinite(correction_gain) or not 0.0 < correction_gain <= 1.0:
        raise ValueError("correction_gain must be in (0, 1]")
    if maximum_position_step_m is not None:
        if not math.isfinite(maximum_position_step_m) or maximum_position_step_m <= 0.0:
            raise ValueError("maximum_position_step_m must be positive")
        if not set(active).issubset({0, 1, 2}):
            raise ValueError("position step limit requires a position-only update")
    anchors = np.asarray(anchors_m, dtype=float)
    if anchors.shape != (8, 3) or not np.isfinite(anchors).all():
        raise ValueError("anchors_m must be a finite canonical 8x3 layout")
    if row.boot != clock.boot_epoch:
        return state, _empty_decision("CLOCK_BOOT_UNAVAILABLE", config)
    if tuple(row.anchor_ids) != tuple(range(8)):
        return state, _empty_decision("ANCHOR_IDENTITY_MISMATCH", config)
    slots = _valid_slots(row)
    if len(slots) < 4:
        return state, _empty_decision("FEWER_THAN_FOUR_LINKS", config)

    ids = np.asarray([row.anchor_ids[slot] for slot in slots], dtype=int)
    measured = np.asarray([row.ranges_mm[slot] for slot in slots], dtype=float) / 1000.0
    epochs = np.asarray(
        [clock.seconds(row.strobe_us + 0.5 * row.t_round_us[slot]) for slot in slots],
        dtype=float,
    )
    reference_epoch = float(np.median(epochs))
    if abs(state.time_s - reference_epoch) > 5e-6:
        return state, _empty_decision("STATE_NOT_AT_SWEEP_REFERENCE_EPOCH", config)
    offsets = epochs - reference_epoch
    quality = np.asarray([max(1, row.quality[slot]) for slot in slots], dtype=float)
    sigma = config.nominal_sigma_m * np.sqrt(100.0 / quality)
    if range_bias_m is None:
        bias = np.zeros(8, dtype=float)
    else:
        bias = np.asarray(range_bias_m, dtype=float)
        if bias.shape != (8,) or not np.isfinite(bias).all():
            raise ValueError("range_bias_m must be a finite canonical length-8 vector")
    offset = np.zeros(3) if tag_offset_world_m is None else np.asarray(
        tag_offset_world_m, dtype=float
    )
    offset_velocity = (
        np.zeros(3) if tag_offset_velocity_world_mps is None
        else np.asarray(tag_offset_velocity_world_mps, dtype=float)
    )
    if offset.shape != (3,) or not np.isfinite(offset).all():
        raise ValueError("tag_offset_world_m must be a finite three-vector")
    if offset_velocity.shape != (3,) or not np.isfinite(offset_velocity).all():
        raise ValueError("tag_offset_velocity_world_mps must be a finite three-vector")

    prior = state.vector.copy()
    covariance = _regularize(state.covariance, config.covariance_floor)
    active_prior_covariance = covariance[np.ix_(active, active)]
    prior_information = np.linalg.inv(active_prior_covariance)
    estimate = prior.copy()
    final_weight = np.ones(len(slots), dtype=float)
    final_h = np.empty((len(slots), 9), dtype=float)
    predicted = np.empty(len(slots), dtype=float)
    innovation = np.empty(len(slots), dtype=float)

    for iteration in range(1, config.maximum_iterations + 1):
        positions = (
            estimate[:3] + offset
            + offsets[:, None] * (estimate[3:6] + offset_velocity)
        )
        delta = positions - anchors[ids]
        predicted = np.linalg.norm(delta, axis=1)
        if np.any(predicted <= 1e-9):
            return state, _empty_decision("RANGE_DERIVATIVE_SINGULAR", config)
        unit = delta / predicted[:, None]
        final_h.fill(0.0)
        final_h[:, :3] = unit
        final_h[:, 3:6] = offsets[:, None] * unit
        innovation = measured - bias[ids] - predicted
        standardized = innovation / sigma
        final_weight = _robust_weights(innovation, sigma, config)
        inverse_r = final_weight / np.square(sigma)

        active_h = final_h[:, active]
        information = prior_information + active_h.T @ (inverse_r[:, None] * active_h)
        rhs = (
            active_h.T @ (inverse_r * innovation)
            - prior_information @ (estimate[active] - prior[active])
        )
        try:
            step = np.linalg.solve(information, rhs)
        except np.linalg.LinAlgError:
            return state, _empty_decision("NUMERICAL_REJECT", config)
        estimate[active] += step
        if float(np.linalg.norm(step)) <= config.convergence_tolerance:
            break

    solved_delta = estimate - prior
    applied_gain = float(correction_gain)
    if maximum_position_step_m is not None:
        position_norm = float(np.linalg.norm(solved_delta[:3]))
        if position_norm > maximum_position_step_m:
            applied_gain = min(applied_gain, maximum_position_step_m / position_norm)
    estimate = prior + applied_gain * solved_delta

    # Recompute final diagnostics and posterior at the converged point.
    positions = (
        estimate[:3] + offset
        + offsets[:, None] * (estimate[3:6] + offset_velocity)
    )
    delta = positions - anchors[ids]
    predicted = np.linalg.norm(delta, axis=1)
    unit = delta / predicted[:, None]
    final_h.fill(0.0)
    final_h[:, :3] = unit
    final_h[:, 3:6] = offsets[:, None] * unit
    innovation = measured - bias[ids] - predicted
    standardized = innovation / sigma
    final_weight = _robust_weights(innovation, sigma, config)
    inverse_r = final_weight / np.square(sigma)

    weighted_geometry = final_h[:, :3].T @ (inverse_r[:, None] * final_h[:, :3])
    singular = np.linalg.svd(weighted_geometry, compute_uv=False)
    tolerance = max(weighted_geometry.shape) * np.finfo(float).eps * singular[0]
    rank = int(np.sum(singular > tolerance))
    condition = math.inf if rank < 3 else float(singular[0] / singular[-1])
    if rank < 3 or not math.isfinite(condition) or condition > 1e10:
        return state, RawRangeDecision(
            False, "SOLVER_OR_GEOMETRY_REJECT", tuple(int(value) for value in ids),
            epochs, reference_epoch, measured, predicted, innovation, standardized,
            final_weight, sigma, rank, condition, iteration,
            config.uncertainty_provenance,
        )
    active_h = final_h[:, active]
    information = prior_information + active_h.T @ (inverse_r[:, None] * active_h)
    try:
        solved_active_covariance = _regularize(
            np.linalg.inv(information), config.covariance_floor
        )
        applied_active_covariance = (
            active_prior_covariance
            + applied_gain * (solved_active_covariance - active_prior_covariance)
        )
        posterior_covariance = _regularize(
            _replace_marginal_covariance(covariance, active, applied_active_covariance),
            config.covariance_floor,
        )
    except (np.linalg.LinAlgError, ValueError):
        return state, _empty_decision("NUMERICAL_REJECT", config)
    if not np.isfinite(estimate).all() or not np.isfinite(posterior_covariance).all():
        return state, _empty_decision("NUMERICAL_REJECT", config)

    updated = RootState(reference_epoch, estimate, posterior_covariance)
    return updated, RawRangeDecision(
        True, "ACCEPTED", tuple(int(value) for value in ids), epochs,
        reference_epoch, measured, predicted, innovation, standardized,
        final_weight, sigma, rank, condition, iteration,
        config.uncertainty_provenance,
    )
