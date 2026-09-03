"""Frame-independent strict-causal common-root filters and mode management."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

import numpy as np

from .models import (
    ImuSample,
    PositionObservation,
    RootOutput,
    RootState,
    SystemMode,
    UpdateDecision,
)

GRAVITY_WORLD_MPS2 = np.array([0.0, 0.0, -9.80665])


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


@dataclass(frozen=True)
class _Snapshot:
    state: RootState
    force_sensor_mps2: np.ndarray
    rotation_world_from_sensor: np.ndarray


@dataclass
class _Health:
    accepted: int = 0
    rejected: int = 0
    consecutive_rejected: int = 0
    last_reason: str | None = None


def _regularize(covariance: np.ndarray, floor: float) -> np.ndarray:
    covariance = 0.5 * (np.asarray(covariance, float) + np.asarray(covariance, float).T)
    eigenvalues = np.linalg.eigvalsh(covariance)
    if eigenvalues[0] < floor:
        covariance = covariance + np.eye(covariance.shape[0]) * (floor - eigenvalues[0])
    np.linalg.cholesky(covariance)
    return covariance


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
) -> tuple[RootState, UpdateDecision]:
    """Predict, gate, bound influence, then apply a Joseph covariance update."""

    observation.validate()
    innovation = np.asarray(observation.root_position_m, float) - state.position_m
    zero = np.zeros(3)
    if not observation.frame_valid:
        return state, UpdateDecision(False, "REJECT_FRAME_UNQUALIFIED", None, innovation, zero, 0.0)
    if not observation.physical_point_valid:
        return state, UpdateDecision(False, "REJECT_PHYSICAL_POINT_INVALID", None, innovation, zero, 0.0)
    if observation.quality_state.startswith("REJECT"):
        return state, UpdateDecision(False, observation.quality_state, None, innovation, zero, 0.0)
    h = np.zeros((3, 9)); h[:, :3] = np.eye(3)
    r = np.asarray(observation.covariance_m2, float)
    s = h @ state.covariance @ h.T + r
    nis = float(innovation @ np.linalg.solve(s, innovation))
    if not math.isfinite(nis) or nis > config.nis_limit_3d:
        return state, UpdateDecision(False, "REJECT_NIS", nis, innovation, zero, 0.0)
    gain = np.linalg.solve(s, h @ state.covariance).T
    delta = gain @ innovation
    cap = max(0.0, config.maximum_position_influence_m * float(influence_multiplier))
    position_norm = float(np.linalg.norm(delta[:3]))
    scale = 1.0 if position_norm <= cap or position_norm == 0.0 else cap / position_norm
    effective_gain = gain * scale
    applied = effective_gain @ innovation
    vector = state.vector + applied
    identity = np.eye(9); kh = effective_gain @ h
    covariance = (identity - kh) @ state.covariance @ (identity - kh).T + effective_gain @ r @ effective_gain.T
    covariance = _regularize(covariance, config.covariance_floor)
    return (
        RootState(state.time_s, vector, covariance),
        UpdateDecision(True, "ACCEPTED", nis, innovation, applied[:3], scale),
    )


class CausalDelayedRootFilter:
    """Delayed-state filter with immutable output records.

    UWB measurement times must be nondecreasing in availability order.  This is
    verified for C1 and asserted here.  Accepted delayed updates insert an
    internal virtual snapshot and replay only already-available IMU samples.
    Previously returned ``RootOutput`` objects are never retained or mutated.
    """

    def __init__(self, initial: RootState, config: RootFilterConfig = RootFilterConfig(), *,
                 inertial: bool = True):
        self.config = config
        self.inertial = bool(inertial)
        neutral_force = np.array([0.0, 0.0, 9.80665])
        self._snapshots: list[_Snapshot] = [_Snapshot(initial, neutral_force, np.eye(3))]
        self._last_availability_s = -math.inf
        self._last_observation_measurement_s = -math.inf
        self._last_accepted_measurement_s: float | None = None
        self._last_accepted_availability_s: float | None = None
        self._last_force = neutral_force
        self._last_rotation = np.eye(3)
        self._health: dict[str, _Health] = {}
        self._anchor_health: dict[int, _Health] = {anchor: _Health() for anchor in range(8)}
        self._recovery_good = 0
        self._mode = SystemMode.INITIALIZING
        self._emission_times: list[float] = []
        self.future_imu_count = 0
        self.future_uwb_count = 0
        self.preavailability_output_count = 0
        self.late_imu_rejected = 0

    @property
    def current_state(self) -> RootState:
        return self._snapshots[-1].state

    @property
    def mode(self) -> SystemMode:
        return self._mode

    def _propagate(self, state: RootState, target: float, force: np.ndarray, rotation: np.ndarray) -> tuple[RootState, np.ndarray]:
        if self.inertial:
            return propagate_inertial(state, target, force, rotation, self.config)
        return propagate_constant_velocity(state, target, self.config)

    def add_imu(self, sample: ImuSample) -> bool:
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
        state, _ = self._propagate(
            self.current_state,
            sample.measurement_time_s,
            sample.specific_force_sensor_mps2,
            sample.rotation_world_from_sensor,
        )
        self._snapshots.append(_Snapshot(state, np.asarray(sample.specific_force_sensor_mps2, float).copy(),
                                         np.asarray(sample.rotation_world_from_sensor, float).copy()))
        self._last_force = np.asarray(sample.specific_force_sensor_mps2, float).copy()
        self._last_rotation = np.asarray(sample.rotation_world_from_sensor, float).copy()
        self._last_availability_s = max(self._last_availability_s, sample.availability_time_s)
        self._prune()
        if self._mode in (SystemMode.INITIALIZING, SystemMode.M1_RESET_RECOVERY, SystemMode.TIME_INVALID):
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

    def _state_at(self, measurement_time_s: float) -> tuple[int, RootState, np.ndarray, np.ndarray] | None:
        times = np.asarray([snapshot.state.time_s for snapshot in self._snapshots])
        index = int(np.searchsorted(times, measurement_time_s, side="right") - 1)
        if index < 0:
            return None
        base = self._snapshots[index]
        if measurement_time_s <= base.state.time_s + 1e-12:
            return index, base.state, base.force_sensor_mps2, base.rotation_world_from_sensor
        force = base.force_sensor_mps2
        rotation = base.rotation_world_from_sensor
        state, _ = self._propagate(base.state, measurement_time_s, force, rotation)
        return index, state, force, rotation

    def _replay_after(self, base_index: int, updated: RootState, force: np.ndarray, rotation: np.ndarray) -> None:
        prior = self._snapshots
        retained = prior[: base_index + 1]
        virtual = _Snapshot(updated, np.asarray(force).copy(), np.asarray(rotation).copy())
        if retained and abs(retained[-1].state.time_s - updated.time_s) <= 1e-12:
            retained[-1] = virtual
        else:
            retained.append(virtual)
        state = updated
        for snapshot in prior[base_index + 1:]:
            if snapshot.state.time_s <= updated.time_s + 1e-12:
                continue
            state, _ = self._propagate(
                state,
                snapshot.state.time_s,
                snapshot.force_sensor_mps2,
                snapshot.rotation_world_from_sensor,
            )
            retained.append(_Snapshot(state, snapshot.force_sensor_mps2.copy(), snapshot.rotation_world_from_sensor.copy()))
        self._snapshots = retained

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

    def add_position(self, observation: PositionObservation, *,
                     processing_time_s: float | None = None) -> UpdateDecision:
        try:
            observation.validate()
        except ValueError as error:
            if str(error) == "future UWB observation":
                self.future_uwb_count += 1
            self._mode = SystemMode.TIME_INVALID
            return UpdateDecision(False, "REJECT_TIME_INVALID", None, np.zeros(3), np.zeros(3), 0.0)
        processing_time = float(observation.availability_time_s if processing_time_s is None
                                else max(processing_time_s, observation.availability_time_s))
        if processing_time + 1e-12 < self._last_availability_s:
            self._mode = SystemMode.TIME_INVALID
            return UpdateDecision(False, "REJECT_AVAILABILITY_REVERSAL", None, np.zeros(3), np.zeros(3), 0.0)
        if observation.measurement_time_s + 1e-12 < self._last_observation_measurement_s:
            self._mode = SystemMode.TIME_INVALID
            return UpdateDecision(False, "REJECT_MEASUREMENT_ORDER_REVERSAL", None, np.zeros(3), np.zeros(3), 0.0)
        self._last_observation_measurement_s = observation.measurement_time_s
        located = self._state_at(observation.measurement_time_s)
        if located is None:
            decision = UpdateDecision(False, "REJECT_OUTSIDE_FIXED_LAG", None, np.zeros(3), np.zeros(3), 0.0)
        else:
            index, delayed, force, rotation = located
            recovery_scale = 1.0
            if self._mode in (SystemMode.IMU_ONLY, SystemMode.UWB_RECOVERY, SystemMode.INITIALIZING):
                recovery_scale = min(1.0, max(0.10, (self._recovery_good + 1) / self.config.recovery_good_events))
            updated, decision = update_position(delayed, observation, self.config,
                                                influence_multiplier=recovery_scale)
            if decision.accepted:
                self._replay_after(index, updated, force, rotation)
        self._note_health(observation, decision)
        self._set_mode(observation, decision, processing_time)
        self._last_availability_s = max(self._last_availability_s, processing_time)
        return decision

    def emit(self, output_time_s: float, decision: UpdateDecision | None = None,
             observation: PositionObservation | None = None) -> RootOutput:
        if output_time_s + 1e-12 < self._last_availability_s:
            self.preavailability_output_count += 1
            raise ValueError("output before measurement availability")
        if self._emission_times and output_time_s + 1e-12 < self._emission_times[-1]:
            raise ValueError("output time reversal")
        state = self.current_state
        if output_time_s > state.time_s + 1e-12:
            state, _ = self._propagate(state, output_time_s, self._last_force, self._last_rotation)
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
