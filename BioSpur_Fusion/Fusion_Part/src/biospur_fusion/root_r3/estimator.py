"""Frame-independent strict-causal common-root filters and mode management."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

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
) -> tuple[RootState, UpdateDecision]:
    """Predict, gate, bound influence, then apply a Joseph covariance update."""

    updated, decision, _effective_gain = _update_position_details(
        state,
        observation,
        config,
        influence_multiplier=influence_multiplier,
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
    gain = np.linalg.solve(s, h @ state.covariance).T
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
    ) -> _IncomingEdge:
        return _make_incoming_edge(
            start_time_s=start_time_s,
            end_time_s=end_time_s,
            force_sensor_mps2=force,
            rotation_world_from_sensor=rotation,
            inertial=self.inertial,
            config=self.config,
            input_owner=input_owner,
        )

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
        edge = self._make_edge(
            start_time_s=self.current_state.time_s,
            end_time_s=sample.measurement_time_s,
            force=sample.specific_force_sensor_mps2,
            rotation=sample.rotation_world_from_sensor,
            input_owner="DESTINATION_RIGHT_END_IMU_SAMPLE",
        )
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
        self._snapshots = retained

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

    def add_position(self, observation: PositionObservation, *,
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
            if (
                not np.allclose(
                    baseline_candidate.vector,
                    current_before.vector,
                    rtol=0.0,
                    atol=2e-10,
                )
                or not np.allclose(
                    baseline_candidate.covariance,
                    current_before.covariance,
                    rtol=0.0,
                    atol=2e-9,
                )
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
