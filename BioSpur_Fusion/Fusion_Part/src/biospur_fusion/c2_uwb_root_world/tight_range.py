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
from typing import Sequence

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
    symmetric_corrected_discrepancy: bool = False
    partial_tracking: bool = False
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
        if not isinstance(self.symmetric_corrected_discrepancy,bool):
            raise ValueError('corrected discrepancy mode must be boolean')
        if not isinstance(self.partial_tracking,bool):
            raise ValueError('partial tracking mode must be boolean')
        if self.symmetric_corrected_discrepancy and self.positive_nlos_cauchy_scale_m is None:
            raise ValueError('corrected discrepancy mode requires explicit inherited tail scale')
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
    sensor_sigma_m: np.ndarray | None = None

    def __post_init__(self) -> None:
        count = len(self.anchors)
        names = (
            "link_epochs_s", "measured_ranges_m", "predicted_ranges_m",
            "innovations_m", "standardized_innovations", "robust_weights",
            "sigma_m",
        )
        for name in names:
            object.__setattr__(
                self, name, _readonly_float_array(getattr(self, name), (count,)))
        sensor = self.sigma_m if self.sensor_sigma_m is None else self.sensor_sigma_m
        sensor = _readonly_float_array(sensor, (count,))
        object.__setattr__(self, "sensor_sigma_m", sensor)
        if count:
            if (
                np.any(self.sigma_m <= 0.0)
                or np.any(sensor <= 0.0)
                or np.any(self.robust_weights <= 0.0)
                or not np.array_equal(
                    self.standardized_innovations,
                    self.innovations_m / self.sigma_m,
                )
            ):
                raise ValueError("raw range decision uncertainty is inconsistent")


def _readonly_float_array(value: object, shape: tuple[int, ...]) -> np.ndarray:
    result = np.array(value, dtype=float, copy=True)
    if result.shape != shape or not np.all(np.isfinite(result)):
        raise ValueError(f"expected finite array with shape {shape}")
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class ExternalRangeInformationWeights:
    """Outcome-independent per-anchor information multipliers."""

    node: str
    evidence_time_s: float
    weights: np.ndarray
    provenance: str

    def __post_init__(self) -> None:
        if not self.node or not self.provenance or not math.isfinite(self.evidence_time_s):
            raise ValueError("range-weight identity, time, and provenance are mandatory")
        weights = _readonly_float_array(self.weights, (8,))
        if np.any(weights <= 0.0) or np.any(weights > 1.0):
            raise ValueError("range information weights must be in (0, 1]")
        object.__setattr__(self, "weights", weights)


@dataclass(frozen=True)
class RangeBiasPriorSnapshot:
    """Read-only causal nuisance prior; root/bias cross covariance is absent."""

    node: str
    snapshot_time_s: float
    mean_m: np.ndarray
    variance_m2: np.ndarray
    last_accepted_time_s: np.ndarray
    cross_covariance_status: str = "UNAVAILABLE_NOT_PROPAGATED"

    def __post_init__(self) -> None:
        if not self.node or not math.isfinite(self.snapshot_time_s):
            raise ValueError("bias-prior identity and time are mandatory")
        mean = _readonly_float_array(self.mean_m, (8,))
        variance = _readonly_float_array(self.variance_m2, (8,))
        last = np.array(self.last_accepted_time_s, dtype=float, copy=True)
        if last.shape != (8,) or np.any(np.isinf(last)):
            raise ValueError("bias-prior accepted times must be finite or NaN")
        if np.any(variance < 0.0) or self.cross_covariance_status != "UNAVAILABLE_NOT_PROPAGATED":
            raise ValueError("bias-prior covariance provenance is invalid")
        for row in (mean, variance, last):
            row.setflags(write=False)
        object.__setattr__(self, "mean_m", mean)
        object.__setattr__(self, "variance_m2", variance)
        object.__setattr__(self, "last_accepted_time_s", last)


@dataclass(frozen=True)
class RawRangeFactorLinearization:
    """Immutable pre-update scalar-factor and uncertainty audit."""

    anchors: tuple[int, ...]
    link_epochs_s: np.ndarray
    reference_epoch_s: float
    measured_ranges_m: np.ndarray
    predicted_ranges_m: np.ndarray
    innovations_m: np.ndarray
    state_jacobian: np.ndarray
    information_weights: np.ndarray
    quality_sigma_m: np.ndarray
    bias_mean_m: np.ndarray
    bias_variance_m2: np.ndarray
    sensor_r_m2: np.ndarray
    r_prior_m2: np.ndarray
    s_prior_m2: np.ndarray
    prior_nis: float
    robust_weights: np.ndarray
    irls_information_weights: np.ndarray
    rank: int
    condition: float
    augmented_jacobian: np.ndarray | None
    s_augmented_m2: np.ndarray | None
    augmented_nis: float | None
    uncertainty_provenance: str
    cross_covariance_status: str

    def __post_init__(self) -> None:
        count = len(self.anchors)
        vector_fields = (
            "link_epochs_s", "measured_ranges_m", "predicted_ranges_m",
            "innovations_m", "information_weights", "quality_sigma_m",
            "bias_mean_m", "bias_variance_m2", "robust_weights",
            "irls_information_weights",
        )
        matrix_fields = {
            "state_jacobian": (count, 9), "sensor_r_m2": (count, count),
            "r_prior_m2": (count, count), "s_prior_m2": (count, count),
        }
        for name in vector_fields:
            object.__setattr__(self, name, _readonly_float_array(getattr(self, name), (count,)))
        for name, shape in matrix_fields.items():
            object.__setattr__(self, name, _readonly_float_array(getattr(self, name), shape))
        if self.augmented_jacobian is not None:
            object.__setattr__(self, "augmented_jacobian", _readonly_float_array(
                self.augmented_jacobian, (count, 9 + count)))
        if self.s_augmented_m2 is not None:
            object.__setattr__(self, "s_augmented_m2", _readonly_float_array(
                self.s_augmented_m2, (count, count)))
        if (
            not self.uncertainty_provenance
            or not self.cross_covariance_status
            or not math.isfinite(self.prior_nis)
            or self.prior_nis < 0.0
            or self.rank < 0
            or self.rank > 3
            or (self.rank == 3 and (not math.isfinite(self.condition) or self.condition <= 0.0))
        ):
            raise ValueError("raw range factor diagnostics are invalid")


@dataclass(frozen=True)
class PreparedRawRangeUpdate:
    """Identity-bound initial factors reusable by one immediate range update."""

    state: RootState
    row: UwbRow
    anchors_m: object
    clock: ClockModel
    range_bias_m: object
    bias_prior: RangeBiasPriorSnapshot | None
    information_weights: ExternalRangeInformationWeights | None
    tag_offset_world_m: object
    tag_offset_velocity_world_mps: object
    config: RawRangeUpdateConfig
    factors: RawRangeFactorLinearization
    reference_epoch_s: float | None = None

    def validate_identity(
        self, state, row, anchors_m, clock, range_bias_m, bias_prior,
        information_weights, tag_offset_world_m, tag_offset_velocity_world_mps,
        config,reference_epoch_s=None,
    ) -> None:
        supplied = (state, row, anchors_m, clock, range_bias_m, bias_prior,
            information_weights, tag_offset_world_m, tag_offset_velocity_world_mps, config)
        owned = (self.state, self.row, self.anchors_m, self.clock, self.range_bias_m,
            self.bias_prior, self.information_weights, self.tag_offset_world_m,
            self.tag_offset_velocity_world_mps, self.config)
        if any(left is not right for left, right in zip(supplied, owned)):
            raise ValueError("prepared raw range update owner mismatch")
        if reference_epoch_s!=self.reference_epoch_s:
            raise ValueError('prepared raw range reference mismatch')


@dataclass(frozen=True)
class PersistentRangeBiasConfig:
    """Causal nuisance state; signed discrepancy is an engineering opt-in."""

    initial_sigma_m: float = 0.30
    random_walk_sigma_m_sqrt_s: float = 0.05
    measurement_sigma_floor_m: float = 0.08
    maximum_bias_m: float = 3.0
    maximum_nodes: int = 10
    signed_effective_discrepancy: bool = False

    def validate(self) -> None:
        values = (
            self.initial_sigma_m,
            self.random_walk_sigma_m_sqrt_s,
            self.measurement_sigma_floor_m,
            self.maximum_bias_m,
        )
        if not all(math.isfinite(value) and value > 0.0 for value in values):
            raise ValueError("persistent range-bias parameters must be positive")
        if isinstance(self.maximum_nodes, bool) or self.maximum_nodes != 10:
            raise ValueError("C2 persistent bias state is fixed at ten nodes")
        if not isinstance(self.signed_effective_discrepancy, bool):
            raise ValueError("signed effective discrepancy must be a boolean")


class PersistentRangeBiasTracker:
    """Track one persistent discrepancy for every node--anchor link.

    The tracker consumes post-root innovations and supplies the bias prior to
    the next raw-range update.  It is intentionally causal and never creates a
    Cartesian UWB position.  Root/bias cross-covariance is not retained, so
    this remains a diagnostic nuisance-state approximation rather than a
    scientific full-state smoother.

    The default retains a non-negative body-blocking bias. The explicit signed
    mode instead represents effective geometry/radio discrepancy, NOT physical
    NLOS calibration. It changes only the lower mean bound, retaining the same
    next-observation timing and uncalibrated covariance approximation.
    """

    def __init__(self, config: PersistentRangeBiasConfig = PersistentRangeBiasConfig()):
        config.validate()
        self.config = config
        self._mean: dict[str, np.ndarray] = {}
        self._variance: dict[str, np.ndarray] = {}
        self._last_time: dict[str, np.ndarray] = {}

    def _ensure(self, node: str) -> None:
        if node not in self._mean:
            if not node or len(self._mean) >= self.config.maximum_nodes:
                raise ValueError("persistent bias state exceeds the fixed C2 inventory")
            self._mean[node] = np.zeros(8, dtype=float)
            self._variance[node] = np.full(8, self.config.initial_sigma_m**2)
            self._last_time[node] = np.full(8, np.nan)

    def bias_vector(self, node: str) -> np.ndarray:
        self._ensure(node)
        return self._mean[node].copy()

    def prior_snapshot(self, node: str, *, snapshot_time_s: float) -> RangeBiasPriorSnapshot:
        """Return the causal prior without advancing tracker state."""

        self._ensure(node)
        query = float(snapshot_time_s)
        if not math.isfinite(query):
            raise ValueError("bias-prior snapshot time must be finite")
        last = self._last_time[node]
        finite = np.isfinite(last)
        if np.any(last[finite] >= query):
            raise ValueError("bias-prior snapshot must strictly follow accepted evidence")
        variance = self._variance[node].copy()
        variance[finite] += (
            self.config.random_walk_sigma_m_sqrt_s**2 * (query - last[finite])
        )
        return RangeBiasPriorSnapshot(
            str(node), query, self._mean[node], variance, last,
        )

    def update(self, node: str, decision: RawRangeDecision) -> np.ndarray:
        if not decision.accepted:
            return (
                self._mean[node].copy()
                if node in self._mean else np.zeros(8, dtype=float)
            )
        self._ensure(node)
        if decision.reference_epoch_s is None:
            raise ValueError("accepted bias evidence lacks a reference epoch")
        count = len(decision.anchors)
        if (
            count == 0
            or len(set(decision.anchors)) != count
            or any(anchor < 0 or anchor >= 8 for anchor in decision.anchors)
            or np.asarray(decision.link_epochs_s).shape != (count,)
            or np.asarray(decision.innovations_m).shape != (count,)
            or np.asarray(decision.sigma_m).shape != (count,)
            or not np.all(np.isfinite(decision.link_epochs_s))
        ):
            raise ValueError("accepted bias evidence is malformed")
        means = self._mean[node]
        variances = self._variance[node]
        last = self._last_time[node]
        for local, anchor in enumerate(decision.anchors):
            if math.isfinite(last[anchor]) and float(decision.link_epochs_s[local]) <= last[anchor]:
                raise ValueError("accepted bias evidence is not strictly chronological")
        for local, anchor in enumerate(decision.anchors):
            epoch = float(decision.link_epochs_s[local])
            dt = 0.0 if not math.isfinite(last[anchor]) else max(0.0, epoch - last[anchor])
            predicted_variance = (
                variances[anchor]
                + self.config.random_walk_sigma_m_sqrt_s**2 * dt
            )
            measurement_variance = max(
                float(decision.sensor_sigma_m[local]),
                self.config.measurement_sigma_floor_m,
            ) ** 2
            gain = predicted_variance / (predicted_variance + measurement_variance)
            # The decision innovation has the prior bias removed. Add it back
            # to obtain the observed nuisance component. Signed mode permits
            # negative effective discrepancy, not negative physical NLOS.
            observed_bias = float(decision.innovations_m[local] + means[anchor])
            means[anchor] = float(np.clip(
                means[anchor] + gain * (observed_bias - means[anchor]),
                -self.config.maximum_bias_m if self.config.signed_effective_discrepancy else 0.0,
                self.config.maximum_bias_m,
            ))
            variances[anchor] = max((1.0 - gain) * predicted_variance, 1e-12)
            last[anchor] = epoch
        return means.copy()

    def snapshot(self) -> dict[str, list[float]]:
        return {node: values.tolist() for node, values in sorted(self._mean.items())}


def linearize_raw_range_factors(
    state: RootState,
    row: UwbRow,
    *,
    anchors_m: np.ndarray,
    clock: ClockModel,
    range_bias_m: np.ndarray | None = None,
    bias_prior: RangeBiasPriorSnapshot | None = None,
    information_weights: ExternalRangeInformationWeights | None = None,
    tag_offset_world_m: np.ndarray | None = None,
    tag_offset_velocity_world_mps: np.ndarray | None = None,
    augmented_covariance: np.ndarray | None = None,
    maximum_condition: float = 1e10,
    reference_epoch_s: float | None = None,
    config: RawRangeUpdateConfig = RawRangeUpdateConfig(),
    _enforce_geometry: bool = True,
) -> RawRangeFactorLinearization:
    """Linearize every valid raw link at its measured round-trip epoch.

    Opt-in partial tracking retains one or more directional factors under an
    initialized finite prior; measurement rank is diagnostic, not a 3D fix.
    The default and standalone initialization still require four links.
    The optional augmented covariance is ordered as Root-R3's nine states
    followed by one bias state per returned anchor.  Its use is the only path
    that claims root/bias cross-covariance was propagated.
    """

    config.validate()
    anchors = _readonly_float_array(anchors_m, (8, 3))
    if row.boot != clock.boot_epoch or tuple(row.anchor_ids) != tuple(range(8)):
        raise ValueError("raw range clock or anchor identity is invalid")
    slots = _valid_slots(row)
    if len(slots) < (1 if config.partial_tracking else 4):
        raise ValueError("no valid raw range factors" if config.partial_tracking else "fewer than four raw range factors")
    ids = np.asarray([row.anchor_ids[slot] for slot in slots], dtype=int)
    epochs = np.asarray([
        clock.seconds(row.strobe_us + 0.5 * row.t_round_us[slot]) for slot in slots
    ], dtype=float)
    reference = float(np.median(epochs)) if reference_epoch_s is None else float(reference_epoch_s)
    if not math.isfinite(reference):
        raise ValueError('raw range reference must be finite')
    if abs(float(state.time_s) - reference) > 5e-6:
        raise ValueError("state is not at the raw-range reference epoch")
    if not math.isfinite(maximum_condition) or maximum_condition <= 0.0:
        raise ValueError("maximum condition must be finite and positive")
    count = len(slots)
    if range_bias_m is not None and bias_prior is not None:
        raise ValueError("range_bias_m and bias_prior are mutually exclusive")
    if bias_prior is None:
        if range_bias_m is None:
            bias_mean = np.zeros(count)
            cross_status = "ZERO_BIAS_PRIOR_LEGACY_EQUIVALENCE"
        else:
            legacy_bias = _readonly_float_array(range_bias_m, (8,))
            bias_mean = legacy_bias[ids]
            cross_status = "LEGACY_EXTERNAL_MEAN_NO_COVARIANCE"
        bias_variance = np.zeros(count)
    else:
        if bias_prior.node != row.node or not bias_prior.snapshot_time_s < float(np.min(epochs)):
            raise ValueError("bias prior is not strictly pre-epoch or has wrong identity")
        bias_mean = bias_prior.mean_m[ids]
        bias_variance = bias_prior.variance_m2[ids]
        cross_status = bias_prior.cross_covariance_status
    if information_weights is None:
        info = np.ones(count)
    else:
        if information_weights.node != row.node or not information_weights.evidence_time_s < float(np.min(epochs)):
            raise ValueError("information weights are not strictly pre-epoch or have wrong identity")
        info = information_weights.weights[ids]
    offset = np.zeros(3) if tag_offset_world_m is None else _readonly_float_array(
        tag_offset_world_m, (3,))
    offset_velocity = (
        np.zeros(3) if tag_offset_velocity_world_mps is None
        else _readonly_float_array(tag_offset_velocity_world_mps, (3,))
    )
    dt = epochs - reference
    positions = (
        state.vector[:3] + offset
        + dt[:, None] * (state.vector[3:6] + offset_velocity)
    )
    delta = positions - anchors[ids]
    predicted = np.linalg.norm(delta, axis=1)
    if np.any(predicted <= 1e-9):
        raise ValueError("raw range derivative is singular")
    unit = delta / predicted[:, None]
    h = np.zeros((count, 9), dtype=float)
    h[:, :3] = unit
    h[:, 3:6] = dt[:, None] * unit
    measured = np.asarray([row.ranges_mm[slot] for slot in slots], dtype=float) / 1000.0
    innovation = measured - bias_mean - predicted
    quality = np.asarray([max(1, row.quality[slot]) for slot in slots], dtype=float)
    sigma = config.nominal_sigma_m * np.sqrt(100.0 / quality)
    sensor_r = np.diag(np.square(sigma) / info)
    r_prior = np.diag((np.square(sigma) + bias_variance) / info)
    state_covariance = _regularize(state.covariance, config.covariance_floor)
    s_prior = h @ state_covariance @ h.T + r_prior
    prior_nis = float(innovation @ np.linalg.solve(s_prior, innovation))
    total_sigma = np.sqrt(np.diag(r_prior))
    # Robust residuals are standardized by the same total prior uncertainty
    # consumed by the update. This equals the legacy quality sigma exactly
    # for unit weights and zero bias variance.
    robust = _robust_weights(innovation, total_sigma, config)
    irls = robust / np.diag(r_prior)
    design = h[:, :3] / np.sqrt(np.diag(r_prior))[:, None]
    singular = np.linalg.svd(design, compute_uv=False)
    tolerance = max(design.shape) * np.finfo(float).eps * singular[0]
    rank = int(np.sum(singular > tolerance))
    condition = math.inf if rank < 3 else float(np.linalg.cond(design.T @ design))
    if _enforce_geometry and not config.partial_tracking and (
        rank != 3 or not math.isfinite(condition) or condition > maximum_condition
    ):
        raise ValueError("raw range factor geometry failed rank/condition")
    augmented_h = augmented_s = augmented_nis = None
    if augmented_covariance is not None:
        if bias_prior is None:
            raise ValueError("augmented covariance requires an authenticated bias prior")
        augmented = _readonly_float_array(augmented_covariance, (9 + count, 9 + count))
        if not np.allclose(augmented, augmented.T, rtol=0.0, atol=1e-12):
            raise ValueError("augmented covariance is asymmetric")
        if float(np.linalg.eigvalsh(augmented)[0]) < -1e-12:
            raise ValueError("augmented covariance is not positive semidefinite")
        if not np.array_equal(augmented[:9, :9], state.covariance):
            raise ValueError("augmented covariance root marginal changed")
        if not np.array_equal(augmented[9:, 9:], np.diag(bias_variance)):
            raise ValueError("augmented covariance bias marginal changed")
        augmented_h = np.concatenate((h, np.eye(count)), axis=1)
        augmented_s = augmented_h @ augmented @ augmented_h.T + sensor_r
        augmented_nis = float(innovation @ np.linalg.solve(augmented_s, innovation))
        cross_status = "SUPPLIED_FULL_AUGMENTED_COVARIANCE"
    return RawRangeFactorLinearization(
        tuple(int(value) for value in ids), epochs, reference, measured, predicted,
        innovation, h, info, sigma, bias_mean, bias_variance, sensor_r, r_prior,
        s_prior, prior_nis, robust, irls, rank, condition, augmented_h,
        augmented_s, augmented_nis, config.uncertainty_provenance, cross_status,
    )


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
    innovations retain the symmetric Huber model by default. The explicit
    symmetric_corrected_discrepancy option applies the same tail to either
    sign after nuisance subtraction; it does not assert symmetric physical
    NLOS. This is a measurement likelihood, not range deletion or solved-position
    preprocessing, and it can also attenuate large genuine prediction errors.
    """

    standardized = innovation_m / sigma_m
    magnitude = np.abs(standardized)
    weights = np.ones_like(magnitude)
    outside = magnitude > config.huber_threshold_sigma
    weights[outside] = config.huber_threshold_sigma / magnitude[outside]
    scale = config.positive_nlos_cauchy_scale_m
    if scale is not None:
        # Once a persistent nuisance mean is subtracted, either residual sign
        # can indicate stale radio/geometry discrepancy. This opt-in likelihood
        # is not a claim that physical NLOS has symmetric range bias.
        positive = np.ones_like(innovation_m,dtype=bool) if config.symmetric_corrected_discrepancy else innovation_m > 0.0
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


def prepare_raw_range_update(
    state: RootState,
    row: UwbRow,
    *,
    anchors_m: np.ndarray,
    clock: ClockModel,
    range_bias_m: np.ndarray | None = None,
    bias_prior: RangeBiasPriorSnapshot | None = None,
    information_weights: ExternalRangeInformationWeights | None = None,
    tag_offset_world_m: np.ndarray | None = None,
    tag_offset_velocity_world_mps: np.ndarray | None = None,
    config: RawRangeUpdateConfig = RawRangeUpdateConfig(),
    reference_epoch_s: float | None = None,
) -> PreparedRawRangeUpdate:
    """Own the initial factorization for one immediately matching update."""

    factors = linearize_raw_range_factors(
        state, row, anchors_m=anchors_m, clock=clock,
        range_bias_m=range_bias_m, bias_prior=bias_prior,
        information_weights=information_weights,
        tag_offset_world_m=tag_offset_world_m,
        tag_offset_velocity_world_mps=tag_offset_velocity_world_mps,
        maximum_condition=1e10, config=config, _enforce_geometry=False,
        reference_epoch_s=reference_epoch_s,
    )
    return PreparedRawRangeUpdate(
        state, row, anchors_m, clock, range_bias_m, bias_prior,
        information_weights, tag_offset_world_m, tag_offset_velocity_world_mps,
        config, factors,reference_epoch_s,
    )


def update_raw_ranges(
    state: RootState,
    row: UwbRow,
    *,
    anchors_m: np.ndarray,
    clock: ClockModel,
    range_bias_m: np.ndarray | None = None,
    bias_prior: RangeBiasPriorSnapshot | None = None,
    information_weights: ExternalRangeInformationWeights | None = None,
    tag_offset_world_m: np.ndarray | None = None,
    tag_offset_velocity_world_mps: np.ndarray | None = None,
    state_update_indices: tuple[int, ...] | None = None,
    correction_gain: float = 1.0,
    maximum_position_step_m: float | None = None,
    config: RawRangeUpdateConfig = RawRangeUpdateConfig(),
    prepared: PreparedRawRangeUpdate | None = None,
    reference_epoch_s: float | None = None,
    consider_position: bool = False,
    gain_scale: float | None = None,
    correction_gain_scope: str = 'full-state',
    transition_observer=None,
) -> tuple[RootState, RawRangeDecision]:
    """Apply a robust raw-link update, using iterated MAP by default.

    The caller must propagate ``state`` to the median link epoch first.  The
    measured per-anchor round-trip intervals then place each scalar factor at
    its own epoch using the state's velocity over the few-millisecond sweep.
    ``tag_offset_world_m`` permits several body-mounted tags to constrain one
    shared root without first solving Cartesian tag positions.  Its value and
    derivative must come from an explicitly owned body model. Missing links
    simply omit their factors.

    ``reference_epoch_s`` can preserve the original sweep's state reference
    after a caller removes links. Measured link epochs are never shifted; their
    offsets are computed relative to that explicit reference. The default is
    the retained-link median, preserving existing callers. Explicit references
    cannot reuse a prepared factorization that did not bind that reference.

    With the default nine active states and unit gain, propagated position /
    velocity / accelerometer-bias cross covariance carries each range residual
    into all observable inertial error states. The fixed prior is counted once
    across nonlinear iterations, and the final robust information covariance
    is equivalent to a Joseph update at that linearization. Selecting position
    only deliberately disables this inertial feedback; it is not a substitute
    for a complete navigation update in a long continuous replay.

    ``partial_tracking`` permits directional updates with one or more links
    using the finite full prior; it never constitutes standalone localization.
    It, ``consider_position`` or ``gain_scale`` selects a single-prior EKF
    linearization instead. The full prior produces the gain; optional position
    rows are zeroed and the actual gain is scaled before both the state and
    Joseph covariance updates. In opt-in position-only scope, velocity/bias
    rows retain their full-prior gain. A zero scale is a root no-op only in
    full-state scope; admitted observations still feed external bias tracking.
    """

    config.validate()
    if correction_gain_scope not in ('full-state','position-only'):
        raise ValueError('unknown correction gain scope')
    if correction_gain_scope=='position-only' and gain_scale is None:
        raise ValueError('position-only gain scope requires an explicit correction budget')
    if config.partial_tracking and gain_scale is None:
        gain_scale=1.0 # Directional tracking uses the finite full-state prior.
    if transition_observer is not None and gain_scale is None:
        # Explicit single-linearization EKF supplies its actual residual map
        # to an attached stochastic contact owner. Never infer it from P ratios.
        gain_scale = 1.0
    if gain_scale is not None and (not math.isfinite(gain_scale) or not 0. <= gain_scale <= 1.):
        raise ValueError('gain_scale must be finite within [0, 1]')
    if (consider_position or gain_scale is not None) and (state_update_indices is not None or correction_gain != 1.0 or maximum_position_step_m is not None):
        raise ValueError('consider-position/gain scaling requires unprojected full-state raw factors')
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
    if len(slots) < (1 if config.partial_tracking else 4):
        return state, _empty_decision("NO_VALID_LINKS" if config.partial_tracking else "FEWER_THAN_FOUR_LINKS", config)

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

    try:
        if prepared is None:
            factors = linearize_raw_range_factors(
                state, row, anchors_m=anchors, clock=clock,
                range_bias_m=range_bias_m, bias_prior=bias_prior,
                information_weights=information_weights,
                tag_offset_world_m=offset,
                tag_offset_velocity_world_mps=offset_velocity,
                maximum_condition=1e10, config=config, _enforce_geometry=False,
                reference_epoch_s=reference_epoch_s,
            )
        else:
            prepared.validate_identity(
                state, row, anchors_m, clock, range_bias_m, bias_prior,
                information_weights, tag_offset_world_m,
                tag_offset_velocity_world_mps, config,reference_epoch_s,
            )
            factors = prepared.factors
    except ValueError as exc:
        if str(exc) == "state is not at the raw-range reference epoch":
            return state, _empty_decision("STATE_NOT_AT_SWEEP_REFERENCE_EPOCH", config)
        if str(exc) == "raw range derivative is singular":
            return state, _empty_decision("RANGE_DERIVATIVE_SINGULAR", config)
        raise
    ids = np.asarray(factors.anchors, dtype=int)
    measured = np.asarray(factors.measured_ranges_m)
    epochs = np.asarray(factors.link_epochs_s)
    reference_epoch = float(factors.reference_epoch_s)
    offsets = epochs - reference_epoch
    sigma = np.sqrt(np.diag(factors.r_prior_m2))
    decision_sigma = np.sqrt(np.diag(factors.sensor_r_m2))
    bias = np.zeros(8, dtype=float)
    bias[ids] = factors.bias_mean_m

    if consider_position or gain_scale is not None:
        # Keep the full prior and cross covariance in S/K, including when
        # position is an optional Schmidt-style consider state. A marginal
        # active-subspace update would lose position-to-velocity/bias feedback.
        if not config.partial_tracking and (factors.rank < 3 or not math.isfinite(factors.condition) or factors.condition > 1e10):
            return state, _empty_decision('SOLVER_OR_GEOMETRY_REJECT', config)
        h = factors.state_jacobian
        r = np.diag(np.square(sigma) / factors.robust_weights)
        p = state.covariance
        gain = np.linalg.solve(h @ p @ h.T + r, h @ p).T
        if consider_position:
            gain[:3] = 0.0
        if gain_scale is not None:
            if correction_gain_scope=='position-only':gain[:3] *= gain_scale
            else:gain *= gain_scale
        estimate = state.vector + gain @ factors.innovations_m
        ikh = np.eye(9) - gain @ h
        posterior = ikh @ p @ ikh.T + gain @ r @ gain.T
        posterior = (posterior + posterior.T) * .5
        updated = state if gain_scale == 0. and correction_gain_scope=='full-state' else RootState(reference_epoch, estimate, posterior)
        # The external range-bias tracker consumes posterior residuals. Keep
        # the same bias prior and measured epochs, never substitute prior
        # innovations merely because this branch uses one linearization.
        after = linearize_raw_range_factors(updated, row, anchors_m=anchors, clock=clock,
            range_bias_m=range_bias_m, bias_prior=bias_prior, information_weights=information_weights,
            tag_offset_world_m=offset, tag_offset_velocity_world_mps=offset_velocity,
            reference_epoch_s=reference_epoch_s, config=config, _enforce_geometry=False)
        if transition_observer is not None:
            from .joint_transition_tape import notify_transition
            notify_transition(transition_observer,ikh,state,updated,'assimilation',
                              measurement=(h,r,factors.innovations_m,gain))
        return updated, RawRangeDecision(True, 'ACCEPTED', factors.anchors, epochs,
            reference_epoch, measured, after.predicted_ranges_m, after.innovations_m,
            after.innovations_m / sigma, factors.robust_weights, sigma,
            factors.rank, factors.condition, 1, config.uncertainty_provenance, decision_sigma)

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
            config.uncertainty_provenance, decision_sigma,
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
        config.uncertainty_provenance, decision_sigma,
    )
