"""Causal dual-ankle support inference and world-foothold constraints.

The ankle IMUs are not force sensors.  This module therefore exposes a
continuous support confidence derived from inertial stillness and an explicit
FK height gate.  A contact episode owns one fixed world-space ankle proxy;
the root is softly corrected toward that point until the episode is released.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
import math
from typing import Mapping, Sequence

import numpy as np

from biospur_fusion.root_r3.models import (
    AdditiveRootConstraint,
    BoundedTargetRootConstraint,
    RootState,
)


SIDES = ("left", "right")


class FootSupportState(str, Enum):
    """Observable support state without conflating shank motion and flight."""

    STANCE_CONFIRMED = "STANCE_CONFIRMED"
    UNCERTAIN = "UNCERTAIN"
    SWING_CONFIRMED = "SWING_CONFIRMED"
    UNOBSERVABLE = "UNOBSERVABLE"


@dataclass(frozen=True)
class FootStillnessProfile:
    gyro_rms_rad_s: float
    acceleration_std_mps2: float

    def validate(self) -> None:
        if not (
            math.isfinite(self.gyro_rms_rad_s)
            and self.gyro_rms_rad_s > 0.0
            and math.isfinite(self.acceleration_std_mps2)
            and self.acceleration_std_mps2 > 0.0
        ):
            raise ValueError("foot stillness thresholds must be finite and positive")


@dataclass(frozen=True)
class AnkleContactConfig:
    window_samples: int = 25
    calibration_quantile: float = 0.99
    threshold_scale: float = 1.5
    enter_confidence: float = 0.65
    exit_confidence: float = 0.25
    enter_samples: int = 24
    exit_samples: int = 12
    full_height_margin_m: float = 0.025
    maximum_height_margin_m: float = 0.075
    kinematic_speed_scale_mps: float = 0.30

    def validate(self) -> None:
        if self.window_samples < 5 or self.enter_samples < 1 or self.exit_samples < 1:
            raise ValueError("contact window and dwell counts must be positive")
        if not 0.5 < self.calibration_quantile < 1.0:
            raise ValueError("contact calibration quantile is invalid")
        if not self.threshold_scale >= 1.0:
            raise ValueError("contact threshold scale must be at least one")
        if not 0.0 < self.exit_confidence < self.enter_confidence < 1.0:
            raise ValueError("contact confidence hysteresis is invalid")
        if not 0.0 <= self.full_height_margin_m < self.maximum_height_margin_m:
            raise ValueError("contact height gate is invalid")
        if not math.isfinite(self.kinematic_speed_scale_mps) or self.kinematic_speed_scale_mps <= 0.0:
            raise ValueError("contact kinematic speed scale must be positive")


@dataclass(frozen=True)
class FootContactEvidence:
    side: str
    time_s: float
    confidence: float
    contact: bool
    gyro_rms_rad_s: float
    acceleration_std_mps2: float
    relative_height_m: float
    reason: str
    support_state: str | None = None
    prior_held: bool = False
    positive_swing: bool = False
    swing_observable: bool = False
    activity_prior_conflict: bool = False
    prior_held_support_confidence: float | None = None

    @property
    def resolved_support_state(self) -> FootSupportState:
        """Map legacy boolean evidence into the explicit support state."""

        if self.support_state is None:
            return (
                FootSupportState.STANCE_CONFIRMED
                if self.contact else FootSupportState.SWING_CONFIRMED
            )
        return FootSupportState(self.support_state)

    @property
    def owns_root_constraint(self) -> bool:
        """Whether this evidence may currently constrain horizontal root."""

        return (
            self.resolved_support_state is FootSupportState.STANCE_CONFIRMED
            or (
                self.resolved_support_state is FootSupportState.UNCERTAIN
                and self.prior_held
            )
        )

    @property
    def is_confirmed_stance(self) -> bool:
        """True only for observed stance, never for an episode prior."""

        return self.resolved_support_state is FootSupportState.STANCE_CONFIRMED


def _rolling_features(
    acceleration_mps2: np.ndarray,
    gyro_rad_s: np.ndarray,
    window_samples: int,
) -> tuple[np.ndarray, np.ndarray]:
    acceleration = np.asarray(acceleration_mps2, dtype=float)
    gyro = np.asarray(gyro_rad_s, dtype=float)
    if acceleration.ndim != 2 or acceleration.shape[1] != 3 or gyro.shape != acceleration.shape:
        raise ValueError("static ankle samples must be matching Nx3 arrays")
    if len(acceleration) < window_samples:
        raise ValueError("insufficient static ankle samples")
    kernel = np.ones(window_samples, dtype=float) / window_samples
    acceleration_norm = np.linalg.norm(acceleration, axis=1)
    gyro_norm2 = np.sum(gyro * gyro, axis=1)
    acceleration_mean = np.convolve(acceleration_norm, kernel, mode="valid")
    acceleration_mean2 = np.convolve(
        acceleration_norm * acceleration_norm, kernel, mode="valid"
    )
    acceleration_std = np.sqrt(np.maximum(0.0, acceleration_mean2 - acceleration_mean**2))
    gyro_rms = np.sqrt(np.convolve(gyro_norm2, kernel, mode="valid"))
    return gyro_rms, acceleration_std


def fit_stillness_profiles(
    samples: Mapping[str, Sequence[tuple[np.ndarray, np.ndarray]]],
    config: AnkleContactConfig = AnkleContactConfig(),
) -> dict[str, FootStillnessProfile]:
    """Fit per-foot thresholds from labelled still episodes only."""

    config.validate()
    profiles: dict[str, FootStillnessProfile] = {}
    for side in SIDES:
        gyro_blocks = []
        acceleration_blocks = []
        for acceleration, gyro in samples[side]:
            gyro_rms, acceleration_std = _rolling_features(
                acceleration, gyro, config.window_samples
            )
            gyro_blocks.append(gyro_rms)
            acceleration_blocks.append(acceleration_std)
        if not gyro_blocks:
            raise ValueError(f"no static samples for {side} ankle")
        profile = FootStillnessProfile(
            gyro_rms_rad_s=float(
                config.threshold_scale
                * np.quantile(np.concatenate(gyro_blocks), config.calibration_quantile)
            ),
            acceleration_std_mps2=float(
                config.threshold_scale
                * np.quantile(
                    np.concatenate(acceleration_blocks), config.calibration_quantile
                )
            ),
        )
        profile.validate()
        profiles[side] = profile
    return profiles


class AnkleContactDetector:
    """Infer independent left/right support with causal hysteresis."""

    def __init__(
        self,
        profiles: Mapping[str, FootStillnessProfile],
        config: AnkleContactConfig = AnkleContactConfig(),
        *,
        stationary_no_flight_prior: bool = False,
    ):
        config.validate()
        if set(profiles) != set(SIDES):
            raise ValueError("contact profiles must contain left and right")
        for profile in profiles.values():
            profile.validate()
        self.profiles = dict(profiles)
        self.config = config
        self.stationary_no_flight_prior = bool(stationary_no_flight_prior)
        self._acceleration_norm = {
            side: deque(maxlen=config.window_samples) for side in SIDES
        }
        self._gyro_norm2 = {
            side: deque(maxlen=config.window_samples) for side in SIDES
        }
        self._support_state = {
            side: FootSupportState.UNOBSERVABLE for side in SIDES
        }
        self._candidate_count = {side: 0 for side in SIDES}
        self._release_count = {side: 0 for side in SIDES}
        self._last_data_confirmed_confidence: dict[str, float] = {}
        self._prior_held_support_confidence: dict[str, float] = {}

    def support_state(self, side: str) -> FootSupportState:
        if side not in SIDES:
            raise ValueError(f"unsupported foot side: {side}")
        return self._support_state[side]

    def _evidence(
        self,
        side: str,
        *,
        time_s: float,
        confidence: float,
        gyro_rms: float,
        acceleration_std: float,
        relative_height_m: float,
        reason: str,
        positive_swing: bool,
        swing_observable: bool,
    ) -> FootContactEvidence:
        state = self._support_state[side]
        prior_held = bool(
            state is FootSupportState.UNCERTAIN
            and self.stationary_no_flight_prior
        )
        return FootContactEvidence(
            side,
            float(time_s),
            float(confidence),
            state is FootSupportState.STANCE_CONFIRMED,
            float(gyro_rms),
            float(acceleration_std),
            float(relative_height_m),
            reason,
            support_state=state.value,
            prior_held=prior_held,
            positive_swing=bool(positive_swing),
            swing_observable=bool(swing_observable),
            activity_prior_conflict=reason.startswith(
                "ACTIVITY_PRIOR_CONFLICT_"
            ),
            prior_held_support_confidence=(
                self._prior_held_support_confidence.get(side)
                if prior_held else None
            ),
        )

    def update(
        self,
        side: str,
        *,
        time_s: float,
        acceleration_mps2: np.ndarray,
        gyro_rad_s: np.ndarray,
        relative_height_m: float,
        relative_speed_mps: float,
        positive_swing: bool | None = None,
        swing_observable: bool | None = None,
    ) -> FootContactEvidence:
        if side not in SIDES:
            raise ValueError(f"unsupported foot side: {side}")
        acceleration = np.asarray(acceleration_mps2, dtype=float)
        gyro = np.asarray(gyro_rad_s, dtype=float)
        if acceleration.shape != (3,) or gyro.shape != (3,):
            raise ValueError("ankle IMU sample must be a pair of 3-vectors")
        if not (
            math.isfinite(float(time_s))
            and np.isfinite(acceleration).all()
            and np.isfinite(gyro).all()
            and math.isfinite(float(relative_height_m))
            and math.isfinite(float(relative_speed_mps))
        ):
            raise ValueError("ankle contact input is non-finite")
        height = max(0.0, float(relative_height_m))
        if positive_swing is None:
            positive_swing = height >= self.config.maximum_height_margin_m
        if swing_observable is None:
            swing_observable = True
        positive_swing = bool(positive_swing)
        swing_observable = bool(swing_observable)
        if positive_swing and not swing_observable:
            raise ValueError("positive swing requires an observable height owner")

        acceleration_window = self._acceleration_norm[side]
        gyro_window = self._gyro_norm2[side]
        acceleration_window.append(float(np.linalg.norm(acceleration)))
        gyro_window.append(float(gyro @ gyro))
        if len(acceleration_window) < self.config.window_samples:
            return self._evidence(
                side,
                time_s=time_s,
                confidence=0.0,
                gyro_rms=math.nan,
                acceleration_std=math.nan,
                relative_height_m=relative_height_m,
                reason="WINDOW_WARMUP",
                positive_swing=positive_swing,
                swing_observable=swing_observable,
            )

        acceleration_std = float(np.std(acceleration_window, ddof=0))
        gyro_rms = math.sqrt(float(np.mean(gyro_window)))
        profile = self.profiles[side]
        motion_ratio = max(
            gyro_rms / profile.gyro_rms_rad_s,
            acceleration_std / profile.acceleration_std_mps2,
        )
        inertial_confidence = 1.0 / (1.0 + motion_ratio**4)
        # These sensors are mounted on the distal shank, not in a shoe.  The
        # shank can rotate while the planted ankle point remains nearly fixed,
        # so classic foot-mounted zero-angular-rate detection would reject a
        # real stance.  Frozen-FK ankle speed is therefore an independent
        # support cue; inertial stillness remains useful when FK is quiet.
        kinematic_ratio = max(
            0.0, float(relative_speed_mps)
        ) / self.config.kinematic_speed_scale_mps
        kinematic_confidence = 1.0 / (1.0 + kinematic_ratio**4)
        if height <= self.config.full_height_margin_m:
            height_confidence = 1.0
        elif height >= self.config.maximum_height_margin_m:
            height_confidence = 0.0
        else:
            height_confidence = (
                self.config.maximum_height_margin_m - height
            ) / (
                self.config.maximum_height_margin_m
                - self.config.full_height_margin_m
            )
        confidence = float(
            max(inertial_confidence, kinematic_confidence) * height_confidence
        )

        state = self._support_state[side]
        if state is FootSupportState.STANCE_CONFIRMED:
            if self.stationary_no_flight_prior and positive_swing:
                # The H02 activity prior owns only an already-established
                # stance identity.  Conflicting lift evidence is retained as
                # evidence, but cannot release or re-anchor that identity.
                self._support_state[side] = FootSupportState.UNCERTAIN
                self._candidate_count[side] = 0
                self._release_count[side] = 0
                reason = "ACTIVITY_PRIOR_CONFLICT_POSITIVE_LIFT_PRIOR_HELD"
            elif positive_swing:
                self._release_count[side] += 1
                if self._release_count[side] >= self.config.exit_samples:
                    self._support_state[side] = FootSupportState.SWING_CONFIRMED
                    self._release_count[side] = 0
                    reason = "CONTACT_EXIT_SWING_CONFIRMED"
                else:
                    reason = "SWING_CANDIDATE"
            elif confidence <= self.config.exit_confidence:
                # A distal-shank IMU can move substantially while the foot
                # remains planted. Motion alone removes confirmed stance but
                # cannot prove swing or release the episode-owned foothold.
                self._support_state[side] = FootSupportState.UNCERTAIN
                self._candidate_count[side] = 0
                self._release_count[side] = 0
                reason = (
                    "ACTIVITY_PRIOR_CONFLICT_MOTION_ONLY_PRIOR_HELD"
                    if self.stationary_no_flight_prior
                    else "SUPPORT_UNCERTAIN_MOTION_ONLY"
                )
            else:
                self._release_count[side] = 0
                reason = "CONTACT_HELD"
        elif state is FootSupportState.UNCERTAIN:
            if self.stationary_no_flight_prior and positive_swing:
                self._candidate_count[side] = 0
                self._release_count[side] = 0
                reason = "ACTIVITY_PRIOR_CONFLICT_POSITIVE_LIFT_PRIOR_HELD"
            elif positive_swing:
                self._release_count[side] += 1
                if self._release_count[side] >= self.config.exit_samples:
                    self._support_state[side] = FootSupportState.SWING_CONFIRMED
                    self._release_count[side] = 0
                    reason = "CONTACT_EXIT_SWING_CONFIRMED"
                else:
                    reason = "SWING_CANDIDATE"
            elif confidence >= self.config.enter_confidence:
                self._candidate_count[side] += 1
                if self._candidate_count[side] >= self.config.enter_samples:
                    self._support_state[side] = (
                        FootSupportState.STANCE_CONFIRMED
                    )
                    self._candidate_count[side] = 0
                    reason = "STANCE_RECOVERED"
                else:
                    reason = "STANCE_RECOVERY_CANDIDATE"
            else:
                self._candidate_count[side] = 0
                self._release_count[side] = 0
                reason = (
                    "ACTIVITY_PRIOR_CONFLICT_MOTION_ONLY_PRIOR_HELD"
                    if self.stationary_no_flight_prior
                    else "SUPPORT_UNCERTAIN_MOTION_ONLY"
                )
        else:
            self._candidate_count[side] = (
                self._candidate_count[side] + 1
                if (
                    confidence >= self.config.enter_confidence
                    and not positive_swing
                ) else 0
            )
            if self._candidate_count[side] >= self.config.enter_samples:
                self._support_state[side] = FootSupportState.STANCE_CONFIRMED
                self._candidate_count[side] = 0
                reason = "CONTACT_ENTER"
            elif positive_swing:
                self._support_state[side] = FootSupportState.SWING_CONFIRMED
                reason = "SWING_CONFIRMED"
            elif not swing_observable:
                self._support_state[side] = FootSupportState.UNOBSERVABLE
                reason = "SUPPORT_UNOBSERVABLE"
            else:
                reason = "NO_CONTACT"
        resolved = self._support_state[side]
        if resolved is FootSupportState.STANCE_CONFIRMED:
            self._last_data_confirmed_confidence[side] = confidence
            self._prior_held_support_confidence.pop(side, None)
        elif (
            resolved is FootSupportState.UNCERTAIN
            and self.stationary_no_flight_prior
            and side not in self._prior_held_support_confidence
            and side in self._last_data_confirmed_confidence
        ):
            self._prior_held_support_confidence[side] = (
                self._last_data_confirmed_confidence[side]
            )
        elif resolved in (
            FootSupportState.SWING_CONFIRMED,
            FootSupportState.UNOBSERVABLE,
        ):
            self._last_data_confirmed_confidence.pop(side, None)
            self._prior_held_support_confidence.pop(side, None)
        return self._evidence(
            side,
            time_s=time_s,
            confidence=confidence,
            gyro_rms=gyro_rms,
            acceleration_std=acceleration_std,
            relative_height_m=relative_height_m,
            reason=reason,
            positive_swing=positive_swing,
            swing_observable=swing_observable,
        )


@dataclass(frozen=True)
class FootholdConstraintConfig:
    position_gain: float = 0.75
    velocity_gain: float = 0.50
    maximum_position_step_m: float = 0.04
    maximum_velocity_step_mps: float = 0.12
    maximum_bilateral_root_target_disagreement_m: float = 0.06
    constrained_axes: tuple[int, ...] = (0, 1)
    supported_ankle_lower_margin_m: float = 0.05
    supported_ankle_upper_excursion_m: float = 0.43

    def validate(self) -> None:
        values = (
            self.position_gain,
            self.velocity_gain,
            self.maximum_position_step_m,
            self.maximum_velocity_step_mps,
            self.maximum_bilateral_root_target_disagreement_m,
            self.supported_ankle_lower_margin_m,
            self.supported_ankle_upper_excursion_m,
        )
        if not all(math.isfinite(value) and value > 0.0 for value in values):
            raise ValueError("foothold constraint parameters must be positive")
        if self.position_gain > 1.0 or self.velocity_gain > 1.0:
            raise ValueError("foothold gains cannot exceed one")
        if not self.constrained_axes or set(self.constrained_axes) - {0, 1, 2}:
            raise ValueError("foothold constrained axes are invalid")
        if self.supported_ankle_upper_excursion_m <= self.supported_ankle_lower_margin_m:
            raise ValueError("supported ankle vertical envelope is invalid")


@dataclass(frozen=True)
class FootholdConstraintDecision:
    accepted: bool
    active_sides: tuple[str, ...]
    constrained_sides: tuple[str, ...]
    entered_sides: tuple[str, ...]
    released_sides: tuple[str, ...]
    position_innovation_m: np.ndarray
    velocity_innovation_mps: np.ndarray
    applied_position_delta_m: np.ndarray
    applied_velocity_delta_mps: np.ndarray
    replay_operator: BoundedTargetRootConstraint | None = None


@dataclass(frozen=True)
class FootholdPoseReconcileDecision:
    accepted: bool
    active_sides: tuple[str, ...]
    constrained_sides: tuple[str, ...]
    primary_side: str | None
    applied_position_delta_m: np.ndarray
    applied_velocity_delta_mps: np.ndarray
    pre_xy_residual_m: Mapping[str, float]
    post_xy_residual_m: Mapping[str, float]
    replay_operator: AdditiveRootConstraint | None = None


@dataclass(frozen=True)
class FootholdOwnership:
    """One causally owned world foothold over a half-open time interval."""

    side: str
    start_time_s: float
    release_time_s: float | None
    world_point_m: np.ndarray


@dataclass(frozen=True)
class FootholdRootGaugeDecision:
    active_sides: tuple[str, ...]
    constrained_sides: tuple[str, ...]
    primary_side: str | None
    root_position_m: np.ndarray
    applied_position_delta_m: np.ndarray


class DualFootFootholdCorrector:
    """Maintain one soft world-space ankle proxy per active contact episode."""

    def __init__(
        self, config: FootholdConstraintConfig = FootholdConstraintConfig()
    ):
        config.validate()
        self.config = config
        self._footholds: dict[str, np.ndarray] = {}
        self._foothold_starts_s: dict[str, float] = {}
        self._released_ownership: list[FootholdOwnership] = []
        self._primary_side: str | None = None
        self._prior_held_support_confidence: dict[str, float] = {}

    def footholds_world_m(self) -> dict[str, np.ndarray]:
        """Return a read-only snapshot of active episode-owned footholds."""

        return {
            side: foothold.copy() for side, foothold in self._footholds.items()
        }

    def prior_held_support_confidence(self) -> dict[str, float]:
        """Return episode-owned H02 prior confidence without mutable aliases."""

        return dict(self._prior_held_support_confidence)

    @property
    def primary_side(self) -> str | None:
        """Return the current live stable-primary support owner."""

        return self._primary_side

    @staticmethod
    def _ownership_copy(owner: FootholdOwnership) -> FootholdOwnership:
        return FootholdOwnership(
            owner.side,
            float(owner.start_time_s),
            None if owner.release_time_s is None else float(owner.release_time_s),
            np.asarray(owner.world_point_m, dtype=float).copy(),
        )

    def foothold_ownership_history(self) -> tuple[FootholdOwnership, ...]:
        """Return all closed and active ownership intervals without aliasing.

        Ownership begins at the :class:`RootState` time at which ``update``
        actually creates the foothold, not at an earlier detector sample.  A
        released interval is half-open, so delayed UWB can query the exact
        measurement time without using contact knowledge from its later
        availability time.
        """

        ownership = list(self._released_ownership)
        ownership.extend(
            FootholdOwnership(
                side,
                self._foothold_starts_s[side],
                None,
                foothold,
            )
            for side, foothold in self._footholds.items()
        )
        return tuple(
            self._ownership_copy(owner)
            for owner in sorted(
                ownership, key=lambda row: (row.start_time_s, row.side)
            )
        )

    def footholds_at_time(self, time_s: float) -> dict[str, np.ndarray]:
        """Return footholds genuinely owned at ``time_s``.

        This historical query is intentionally independent of current contact
        state.  It prevents an evidence change between range measurement and
        availability from being backfilled into a delayed UWB update.
        """

        if not math.isfinite(time_s):
            raise ValueError("foothold query time must be finite")
        selected: dict[str, np.ndarray] = {}
        for owner in self.foothold_ownership_history():
            if (
                owner.start_time_s <= time_s
                and (
                    owner.release_time_s is None
                    or time_s < owner.release_time_s
                )
            ):
                if owner.side in selected:
                    raise RuntimeError("overlapping foothold ownership intervals")
                selected[owner.side] = owner.world_point_m.copy()
        return selected

    def _root_targets(
        self,
        *,
        evidence: Mapping[str, FootContactEvidence],
        ankle_offset_world_m: Mapping[str, np.ndarray],
        ankle_offset_velocity_world_mps: Mapping[str, np.ndarray],
    ) -> tuple[
        tuple[str, ...], tuple[str, ...], np.ndarray, np.ndarray, np.ndarray
    ]:
        active = tuple(side for side in SIDES if side in self._footholds)
        if not active:
            zero = np.zeros(3)
            return active, (), zero, zero, np.empty(0)
        eligible = tuple(
            side for side in active if evidence[side].owns_root_constraint
        )
        if not eligible:
            zero = np.zeros(3)
            return active, (), zero, zero, np.empty(0)
        root_position_targets = np.stack([
            self._footholds[side] - np.asarray(ankle_offset_world_m[side], float)
            for side in eligible
        ])
        root_velocity_targets = np.stack([
            -np.asarray(ankle_offset_velocity_world_mps[side], float)
            for side in eligible
        ])
        if self._primary_side not in eligible:
            self._primary_side = max(
                eligible, key=lambda side: evidence[side].confidence
            )
        constrained, selected, weights, _primary = (
            self._select_root_target_rows_pure(
                active=eligible,
                evidence=evidence,
                root_position_targets=root_position_targets,
                primary_side=self._primary_side,
            )
        )
        root_position_targets = root_position_targets[selected]
        root_velocity_targets = root_velocity_targets[selected]
        return (
            active,
            constrained,
            np.average(root_position_targets, axis=0, weights=weights),
            np.average(root_velocity_targets, axis=0, weights=weights),
            weights,
        )

    def _select_root_target_rows_pure(
        self,
        *,
        active: tuple[str, ...],
        evidence: Mapping[str, FootContactEvidence],
        root_position_targets: np.ndarray,
        primary_side: str,
    ) -> tuple[tuple[str, ...], np.ndarray, np.ndarray, str]:
        if primary_side not in active:
            # Contact eligibility can be stricter than identity ownership.  In
            # particular, a measurement-time prior-held side remains an owned
            # foothold but is excluded from UWB contact residuals.  Resolve the
            # eligible subset from the same historical evidence without
            # reading or mutating the live primary owner.
            primary_side = max(
                active, key=lambda side: evidence[side].confidence
            )
        primary_index = active.index(primary_side)
        if len(active) == 1:
            return active, np.asarray([primary_index]), np.ones(1), primary_side

        secondary_side = next(side for side in active if side != primary_side)
        secondary_index = active.index(secondary_side)
        disagreement = float(np.linalg.norm(
            (root_position_targets[primary_index]
             - root_position_targets[secondary_index])[
                list(self.config.constrained_axes)
            ]
        ))
        gate = self.config.maximum_bilateral_root_target_disagreement_m
        primary_confidence = max(0.0, float(evidence[primary_side].confidence))
        secondary_confidence = max(
            0.0, float(evidence[secondary_side].confidence)
        )
        denominator = primary_confidence + secondary_confidence
        if disagreement >= gate or denominator <= 0.0:
            # An incoherent pair, or a pair without finite positive evidence,
            # is strictly primary-owned.  This is the fail-closed endpoint of
            # the same 6 cm coherence contract, not another threshold.
            return (
                (primary_side,),
                np.asarray([primary_index]),
                np.ones(1),
                primary_side,
            )

        # Restore the secondary continuously as the two root targets agree.
        # These are final affine blend coefficients, not evidence weights to
        # be renormalized again: doing so would recreate the threshold jump
        # when the primary confidence is zero.
        u = float(np.clip(1.0 - disagreement / gate, 0.0, 1.0))
        rho = u * u * (3.0 - 2.0 * u)
        alpha = rho * secondary_confidence / denominator
        coefficients = np.zeros(2, dtype=float)
        coefficients[primary_index] = 1.0 - alpha
        coefficients[secondary_index] = alpha
        return active, np.arange(2), coefficients, primary_side

    def reexpress_root_between_pose_gauges(
        self,
        source_root_position_m: np.ndarray,
        *,
        owned_footholds_world_m: Mapping[str, np.ndarray],
        evidence: Mapping[str, FootContactEvidence],
        source_ankle_offset_world_m: Mapping[str, np.ndarray],
        target_ankle_offset_world_m: Mapping[str, np.ndarray],
        primary_side_at_measurement: str | None,
    ) -> FootholdRootGaugeDecision:
        """Express one root in another pose gauge without changing foot world points.

        ``source`` is the pose gauge in which an estimator produced the root;
        ``target`` is a pose already causally published at the same measurement
        time.  Only horizontal root axes are changed.  Bilateral weighting and
        stable-primary conflict selection have the same single owner used by
        ordinary foothold correction.
        """

        source_root = np.asarray(source_root_position_m, dtype=float).reshape(3)
        active = tuple(
            side for side in SIDES if side in owned_footholds_world_m
        )
        if not active:
            zero = np.zeros(3)
            return FootholdRootGaugeDecision(
                (), (), self._primary_side, source_root.copy(), zero
            )
        if primary_side_at_measurement is None:
            raise ValueError("measurement-time primary snapshot is required")
        if set(evidence) != set(SIDES):
            raise ValueError("root gauge reexpression requires bilateral evidence")
        for offsets in (
            source_ankle_offset_world_m,
            target_ankle_offset_world_m,
        ):
            if set(offsets) != set(SIDES):
                raise ValueError("root gauge reexpression requires bilateral offsets")
            if any(
                np.asarray(offsets[side], dtype=float).shape != (3,)
                or not np.isfinite(np.asarray(offsets[side], dtype=float)).all()
                for side in SIDES
            ):
                raise ValueError("root gauge reexpression offsets must be finite")
        root_targets = np.stack([
            source_root
            + np.asarray(source_ankle_offset_world_m[side], dtype=float)
            - np.asarray(target_ankle_offset_world_m[side], dtype=float)
            for side in active
        ])
        constrained, selected, weights, historical_primary = (
            self._select_root_target_rows_pure(
                active=active,
                evidence=evidence,
                root_position_targets=root_targets,
                primary_side=primary_side_at_measurement,
            )
        )
        selected_target = np.average(
            root_targets[selected], axis=0, weights=weights
        )
        reexpressed = source_root.copy()
        axes = list(self.config.constrained_axes)
        reexpressed[axes] = selected_target[axes]
        return FootholdRootGaugeDecision(
            active,
            constrained,
            historical_primary,
            reexpressed,
            reexpressed - source_root,
        )

    def root_target_for_owned_footholds(
        self,
        source_root_position_m: np.ndarray,
        *,
        owned_footholds_world_m: Mapping[str, np.ndarray],
        evidence: Mapping[str, FootContactEvidence],
        ankle_offset_world_m: Mapping[str, np.ndarray],
        primary_side_at_measurement: str | None,
    ) -> FootholdRootGaugeDecision:
        """Evaluate one root against the historical contact manifold.

        This is a pure consistency query.  It uses the same stable-primary and
        bilateral weighting owner as runtime contact correction, but neither
        mutates live foothold state nor projects the supplied estimator root.
        The returned root is the contact-manifold target on constrained axes;
        callers decide whether an independent observation is compatible.
        """

        source_root = np.asarray(source_root_position_m, dtype=float).reshape(3)
        active = tuple(
            side for side in SIDES if side in owned_footholds_world_m
        )
        if not active:
            zero = np.zeros(3)
            return FootholdRootGaugeDecision(
                (), (), self._primary_side, source_root.copy(), zero
            )
        if primary_side_at_measurement is None:
            raise ValueError("measurement-time primary snapshot is required")
        if set(evidence) != set(SIDES):
            raise ValueError("contact root target requires bilateral evidence")
        if set(ankle_offset_world_m) != set(SIDES):
            raise ValueError("contact root target requires bilateral offsets")
        if any(
            np.asarray(ankle_offset_world_m[side], dtype=float).shape != (3,)
            or not np.isfinite(
                np.asarray(ankle_offset_world_m[side], dtype=float)
            ).all()
            for side in SIDES
        ):
            raise ValueError("contact root target offsets must be finite")
        root_targets = np.stack([
            np.asarray(owned_footholds_world_m[side], dtype=float)
            - np.asarray(ankle_offset_world_m[side], dtype=float)
            for side in active
        ])
        constrained, selected, weights, historical_primary = (
            self._select_root_target_rows_pure(
                active=active,
                evidence=evidence,
                root_position_targets=root_targets,
                primary_side=primary_side_at_measurement,
            )
        )
        selected_target = np.average(
            root_targets[selected], axis=0, weights=weights
        )
        target = source_root.copy()
        axes = list(self.config.constrained_axes)
        target[axes] = selected_target[axes]
        return FootholdRootGaugeDecision(
            active,
            constrained,
            historical_primary,
            target,
            target - source_root,
        )

    def reconcile_pose_change(
        self,
        state: RootState,
        *,
        evidence: Mapping[str, FootContactEvidence],
        previous_ankle_offset_world_m: Mapping[str, np.ndarray],
        previous_ankle_offset_velocity_world_mps: Mapping[str, np.ndarray],
        ankle_offset_world_m: Mapping[str, np.ndarray],
        ankle_offset_velocity_world_mps: Mapping[str, np.ndarray],
    ) -> tuple[RootState, FootholdPoseReconcileDecision]:
        """Exactly re-gauge root XY after an available pose correction changes.

        This is not another soft contact update.  It preserves the displayed
        ankle residual across a pose-gauge change using the same bilateral
        conflict/primary rule as :meth:`update`.  It applies only the old-to-
        new ankle offset and velocity differences; the ordinary bounded
        contact update remains the sole owner of any root-to-foothold residual.
        """

        if set(evidence) != set(SIDES):
            raise ValueError("foothold reconciliation requires bilateral evidence")
        for offsets in (
            previous_ankle_offset_world_m,
            previous_ankle_offset_velocity_world_mps,
            ankle_offset_world_m,
            ankle_offset_velocity_world_mps,
        ):
            if set(offsets) != set(SIDES) or any(
                np.asarray(offsets[side], dtype=float).shape != (3,)
                or not np.isfinite(np.asarray(offsets[side], dtype=float)).all()
                for side in SIDES
            ):
                raise ValueError("foothold reconciliation requires finite bilateral 3-vectors")
        active = tuple(side for side in SIDES if side in self._footholds)
        zero = np.zeros(3)
        if not active:
            return state, FootholdPoseReconcileDecision(
                False, active, (), self._primary_side, zero, zero, {}, {}
            )
        eligible = tuple(
            side for side in active if evidence[side].owns_root_constraint
        )
        if not eligible:
            return state, FootholdPoseReconcileDecision(
                False, active, (), self._primary_side, zero, zero, {}, {}
            )
        if self._primary_side not in eligible:
            self._primary_side = max(
                eligible, key=lambda side: evidence[side].confidence
            )
        current_root_targets = np.stack([
            self._footholds[side]
            - np.asarray(ankle_offset_world_m[side], dtype=float)
            for side in eligible
        ])
        constrained, selected, weights, _primary = (
            self._select_root_target_rows_pure(
                active=eligible,
                evidence=evidence,
                root_position_targets=current_root_targets,
                primary_side=self._primary_side,
            )
        )
        position_gauge_delta = np.average(
            np.stack([
                np.asarray(previous_ankle_offset_world_m[side], dtype=float)
                - np.asarray(ankle_offset_world_m[side], dtype=float)
                for side in eligible
            ])[selected],
            axis=0,
            weights=weights,
        )
        velocity_gauge_delta = np.average(
            np.stack([
                np.asarray(
                    previous_ankle_offset_velocity_world_mps[side],
                    dtype=float,
                )
                - np.asarray(
                    ankle_offset_velocity_world_mps[side], dtype=float
                )
                for side in eligible
            ])[selected],
            axis=0,
            weights=weights,
        )
        pre_residual = {
            side: float(np.linalg.norm(
                (state.position_m
                 + np.asarray(previous_ankle_offset_world_m[side], float)
                 - self._footholds[side])[:2]
            ))
            for side in eligible
        }
        position_delta = np.zeros(3)
        velocity_delta = np.zeros(3)
        axes = list(self.config.constrained_axes)
        position_delta[axes] = position_gauge_delta[axes]
        velocity_delta[axes] = velocity_gauge_delta[axes]
        vector_delta = np.zeros(9)
        vector_delta[:3] = position_delta
        vector_delta[3:6] = velocity_delta
        replay_operator = AdditiveRootConstraint(vector_delta)
        updated = replay_operator.apply(state)
        post_residual = {
            side: float(np.linalg.norm(
                (updated.position_m
                 + np.asarray(ankle_offset_world_m[side], float)
                 - self._footholds[side])[:2]
            ))
            for side in eligible
        }
        return updated, FootholdPoseReconcileDecision(
            True,
            active,
            constrained,
            self._primary_side,
            position_delta,
            velocity_delta,
            pre_residual,
            post_residual,
            replay_operator,
        )

    def update(
        self,
        state: RootState,
        *,
        evidence: Mapping[str, FootContactEvidence],
        ankle_offset_world_m: Mapping[str, np.ndarray],
        ankle_offset_velocity_world_mps: Mapping[str, np.ndarray],
    ) -> tuple[RootState, FootholdConstraintDecision]:
        if set(evidence) != set(SIDES):
            raise ValueError("foothold correction requires bilateral evidence")
        entered = []
        released = []
        for side in SIDES:
            offset = np.asarray(ankle_offset_world_m[side], dtype=float)
            velocity = np.asarray(
                ankle_offset_velocity_world_mps[side], dtype=float
            )
            if offset.shape != (3,) or velocity.shape != (3,):
                raise ValueError("foothold correction requires 3-vector FK proxies")
            support = evidence[side].resolved_support_state
            if (
                support is FootSupportState.STANCE_CONFIRMED
                and side not in self._footholds
            ):
                self._prior_held_support_confidence.pop(side, None)
                self._footholds[side] = state.position_m + offset
                self._foothold_starts_s[side] = float(state.time_s)
                entered.append(side)
            elif (
                support is FootSupportState.SWING_CONFIRMED
                and side in self._footholds
            ):
                self._released_ownership.append(FootholdOwnership(
                    side,
                    self._foothold_starts_s.pop(side),
                    float(state.time_s),
                    self._footholds[side].copy(),
                ))
                del self._footholds[side]
                self._prior_held_support_confidence.pop(side, None)
                released.append(side)

            confidence = float(evidence[side].confidence)
            if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
                raise ValueError("foothold evidence confidence must be in [0, 1]")
            if support is FootSupportState.STANCE_CONFIRMED:
                # A recovered stance retains its existing foothold identity but
                # returns ordinary contact gain ownership to live evidence.
                self._prior_held_support_confidence.pop(side, None)
            elif evidence[side].prior_held and side in self._footholds:
                evidence_owner = evidence[side].prior_held_support_confidence
                if evidence_owner is None:
                    # The detector-carried episode owner is authoritative.  A
                    # missing owner preserves foothold identity but disables
                    # ordinary correction rather than consulting local history.
                    self._prior_held_support_confidence.pop(side, None)
                else:
                    if not (
                        math.isfinite(evidence_owner)
                        and 0.0 <= evidence_owner <= 1.0
                    ):
                        raise ValueError(
                            "prior-held support confidence must be in [0, 1]"
                        )
                    owned = self._prior_held_support_confidence.get(side)
                    if owned is not None and owned != float(evidence_owner):
                        raise RuntimeError(
                            "prior-held support confidence changed within episode"
                        )
                    self._prior_held_support_confidence[side] = float(
                        evidence_owner
                    )
            elif not evidence[side].prior_held:
                self._prior_held_support_confidence.pop(side, None)

        active = tuple(side for side in SIDES if side in self._footholds)
        zero = np.zeros(3)
        if not active:
            self._primary_side = None
            return state, FootholdConstraintDecision(
                False, active, (), tuple(entered), tuple(released), zero, zero, zero, zero
            )

        ordinary_evidence = dict(evidence)
        for side in SIDES:
            if evidence[side].prior_held:
                frozen_confidence = self._prior_held_support_confidence.get(side)
                if frozen_confidence is None:
                    # A prior cannot invent a support-gain owner.  The foothold
                    # identity remains intact, but ordinary correction is disabled.
                    ordinary_evidence[side] = replace(
                        evidence[side], prior_held=False
                    )
                else:
                    # One authoritative episode owner feeds the shared selector,
                    # so its target, velocity target and final gain cannot depend
                    # on the detector's instantaneous uncertain confidence.
                    ordinary_evidence[side] = replace(
                        evidence[side], confidence=frozen_confidence
                    )
        active, constrained, position_target, velocity_target, weights = (
            self._root_targets(
                evidence=ordinary_evidence,
                ankle_offset_world_m=ankle_offset_world_m,
                ankle_offset_velocity_world_mps=ankle_offset_velocity_world_mps,
            )
        )
        if not constrained:
            return state, FootholdConstraintDecision(
                False,
                active,
                (),
                tuple(entered),
                tuple(released),
                zero,
                zero,
                zero,
                zero,
            )
        # Freeze the event-time contact rule in one immutable operator.  Its
        # first evaluation produces the live update; delayed replay later uses
        # the same rule on the revised root without reading live footholds or
        # future evidence.
        ankle_z_offset = float(np.average([
            np.asarray(ankle_offset_world_m[side], float)[2]
            for side in constrained
        ], weights=weights))
        entry_ankle_z = float(np.average([
            self._footholds[side][2] for side in constrained
        ], weights=weights))
        operator_confidence = np.asarray([
            ordinary_evidence[side].confidence for side in constrained
        ], dtype=float)
        confidence = float(np.average(operator_confidence, weights=weights))
        replay_operator = BoundedTargetRootConstraint(
            constrained_axes=self.config.constrained_axes,
            position_target_m=position_target,
            velocity_target_mps=velocity_target,
            confidence=confidence,
            position_gain=self.config.position_gain,
            velocity_gain=self.config.velocity_gain,
            maximum_position_step_m=self.config.maximum_position_step_m,
            maximum_velocity_step_mps=self.config.maximum_velocity_step_mps,
            ankle_z_offset_m=ankle_z_offset,
            ankle_z_entry_m=entry_ankle_z,
            ankle_z_lower_m=(
                entry_ankle_z - self.config.supported_ankle_lower_margin_m
            ),
            ankle_z_upper_m=(
                entry_ankle_z + self.config.supported_ankle_upper_excursion_m
            ),
        )
        (
            updated,
            position_innovation,
            velocity_innovation,
            position_delta,
            velocity_delta,
        ) = replay_operator.evaluate(state)
        return updated, FootholdConstraintDecision(
            True, active, constrained, tuple(entered), tuple(released), position_innovation,
            velocity_innovation, position_delta, velocity_delta, replay_operator,
        )
