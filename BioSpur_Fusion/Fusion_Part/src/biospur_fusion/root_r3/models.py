"""Immutable Root-R3 interface types."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np


class SystemMode(str, Enum):
    INITIALIZING = "INITIALIZING"
    FUSED_NOMINAL = "FUSED_NOMINAL"
    FUSED_UWB_DEGRADED = "FUSED_UWB_DEGRADED"
    IMU_ONLY = "IMU_ONLY"
    UWB_RECOVERY = "UWB_RECOVERY"
    TIME_INVALID = "TIME_INVALID"
    M1_RESET_RECOVERY = "M1_RESET_RECOVERY"


@dataclass(frozen=True)
class FrameBindingStatus:
    qualified: bool
    rotation_navigation_from_v4: np.ndarray | None
    reason: str
    provenance: str

    def validate(self) -> None:
        if not self.qualified:
            if self.rotation_navigation_from_v4 is not None:
                raise ValueError("unqualified frame must not carry an executable rotation")
            return
        rotation = np.asarray(self.rotation_navigation_from_v4, dtype=float)
        if rotation.shape != (3, 3):
            raise ValueError("qualified frame rotation must be 3x3")
        if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-10):
            raise ValueError("frame rotation is not orthonormal")
        if not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-10):
            raise ValueError("frame rotation is not proper")
        if not self.provenance:
            raise ValueError("qualified frame lacks provenance")


@dataclass(frozen=True)
class RootState:
    """Nominal ``[position, velocity, accelerometer bias]`` and covariance."""

    time_s: float
    vector: np.ndarray
    covariance: np.ndarray

    def __post_init__(self) -> None:
        vector = np.asarray(self.vector, dtype=float)
        covariance = np.asarray(self.covariance, dtype=float)
        if vector.shape != (9,) or covariance.shape != (9, 9):
            raise ValueError("RootState requires a 9-vector and 9x9 covariance")
        if not np.isfinite(self.time_s) or not np.isfinite(vector).all() or not np.isfinite(covariance).all():
            raise ValueError("non-finite root state")
        if not np.allclose(covariance, covariance.T, atol=1e-10):
            raise ValueError("root covariance is asymmetric")
        np.linalg.cholesky(covariance)

    @property
    def position_m(self) -> np.ndarray:
        return np.asarray(self.vector[:3])

    @property
    def velocity_mps(self) -> np.ndarray:
        return np.asarray(self.vector[3:6])

    @property
    def accelerometer_bias_mps2(self) -> np.ndarray:
        return np.asarray(self.vector[6:9])


@dataclass(frozen=True)
class AdditiveRootConstraint:
    """Immutable pose-gauge transform replayable on a revised root state."""

    vector_delta: np.ndarray

    def __post_init__(self) -> None:
        delta = np.asarray(self.vector_delta, dtype=float).copy()
        if delta.shape != (9,) or not np.isfinite(delta).all():
            raise ValueError("additive root constraint requires a finite 9-vector")
        delta.setflags(write=False)
        object.__setattr__(self, "vector_delta", delta)

    def apply(self, state: RootState) -> RootState:
        return RootState(
            state.time_s,
            state.vector + self.vector_delta,
            state.covariance.copy(),
        )


@dataclass(frozen=True)
class BoundedTargetRootConstraint:
    """Frozen event-time soft-contact rule evaluated on the replayed state."""

    constrained_axes: tuple[int, ...]
    position_target_m: np.ndarray
    velocity_target_mps: np.ndarray
    confidence: float
    position_gain: float
    velocity_gain: float
    maximum_position_step_m: float
    maximum_velocity_step_mps: float
    ankle_z_offset_m: float
    ankle_z_entry_m: float
    ankle_z_lower_m: float
    ankle_z_upper_m: float

    def __post_init__(self) -> None:
        axes = tuple(int(axis) for axis in self.constrained_axes)
        if not axes or len(set(axes)) != len(axes) or set(axes) - {0, 1, 2}:
            raise ValueError("bounded root constraint axes are invalid")
        position = np.asarray(self.position_target_m, dtype=float).copy()
        velocity = np.asarray(self.velocity_target_mps, dtype=float).copy()
        if (
            position.shape != (3,)
            or velocity.shape != (3,)
            or not np.isfinite(position).all()
            or not np.isfinite(velocity).all()
        ):
            raise ValueError("bounded root targets must be finite 3-vectors")
        scalars = (
            self.confidence,
            self.position_gain,
            self.velocity_gain,
            self.maximum_position_step_m,
            self.maximum_velocity_step_mps,
            self.ankle_z_offset_m,
            self.ankle_z_entry_m,
            self.ankle_z_lower_m,
            self.ankle_z_upper_m,
        )
        if not all(np.isfinite(value) for value in scalars):
            raise ValueError("bounded root constraint contains non-finite values")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("bounded root confidence must be in [0, 1]")
        if not 0.0 <= self.position_gain <= 1.0 or not 0.0 <= self.velocity_gain <= 1.0:
            raise ValueError("bounded root gains must be in [0, 1]")
        if self.maximum_position_step_m <= 0.0 or self.maximum_velocity_step_mps <= 0.0:
            raise ValueError("bounded root step limits must be positive")
        if not (
            self.ankle_z_lower_m
            < self.ankle_z_entry_m
            < self.ankle_z_upper_m
        ):
            raise ValueError("bounded root ankle-Z envelope is invalid")
        position.setflags(write=False)
        velocity.setflags(write=False)
        object.__setattr__(self, "constrained_axes", axes)
        object.__setattr__(self, "position_target_m", position)
        object.__setattr__(self, "velocity_target_mps", velocity)

    @staticmethod
    def _limit(vector: np.ndarray, maximum_norm: float) -> np.ndarray:
        norm = float(np.linalg.norm(vector))
        return (
            vector
            if norm <= maximum_norm
            else vector * (maximum_norm / norm)
        )

    def evaluate(
        self, state: RootState
    ) -> tuple[RootState, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return updated state, innovations, and bounded deltas."""

        position_innovation = self.position_target_m - state.position_m
        velocity_innovation = self.velocity_target_mps - state.velocity_mps
        unconstrained = set(range(3)) - set(self.constrained_axes)
        for axis in unconstrained:
            position_innovation[axis] = 0.0
            velocity_innovation[axis] = 0.0
        current_ankle_z = state.position_m[2] + self.ankle_z_offset_m
        if current_ankle_z < self.ankle_z_lower_m:
            position_innovation[2] = self.ankle_z_lower_m - current_ankle_z
        elif current_ankle_z > self.ankle_z_upper_m:
            position_innovation[2] = self.ankle_z_upper_m - current_ankle_z
        position_delta = self._limit(
            self.position_gain * self.confidence * position_innovation,
            self.maximum_position_step_m,
        )
        velocity_delta = self._limit(
            self.velocity_gain * self.confidence * velocity_innovation,
            self.maximum_velocity_step_mps,
        )
        vector = state.vector.copy()
        vector[:3] += position_delta
        vector[3:6] += velocity_delta
        return (
            RootState(state.time_s, vector, state.covariance.copy()),
            position_innovation,
            velocity_innovation,
            position_delta,
            velocity_delta,
        )

    def apply(self, state: RootState) -> RootState:
        return self.evaluate(state)[0]


RootConstraintOperator = AdditiveRootConstraint | BoundedTargetRootConstraint


@dataclass(frozen=True)
class ImuSample:
    measurement_time_s: float
    availability_time_s: float
    specific_force_sensor_mps2: np.ndarray
    rotation_world_from_sensor: np.ndarray
    source_sequence: int
    m1_valid: bool = True
    m1_reset: bool = False

    def validate(self) -> None:
        if self.measurement_time_s > self.availability_time_s + 1e-12:
            raise ValueError("future IMU sample")
        force = np.asarray(self.specific_force_sensor_mps2, dtype=float)
        rotation = np.asarray(self.rotation_world_from_sensor, dtype=float)
        if force.shape != (3,) or rotation.shape != (3, 3):
            raise ValueError("invalid IMU shape")
        if not np.isfinite(force).all() or not np.isfinite(rotation).all():
            raise ValueError("non-finite IMU sample")
        if not np.allclose(rotation.T @ rotation, np.eye(3), atol=2e-5):
            raise ValueError("IMU orientation is not orthonormal")
        if np.linalg.det(rotation) < 0.999:
            raise ValueError("IMU orientation is not proper")


@dataclass(frozen=True)
class PositionObservation:
    measurement_time_s: float
    availability_time_s: float
    root_position_m: np.ndarray
    covariance_m2: np.ndarray
    tag_id: str
    anchors: tuple[int, ...] = ()
    quality_state: str = "NOMINAL"
    frame_valid: bool = True
    physical_point_valid: bool = True
    source_sequence: int = 0

    def validate(self) -> None:
        if self.measurement_time_s > self.availability_time_s + 1e-12:
            raise ValueError("future UWB observation")
        position = np.asarray(self.root_position_m, dtype=float)
        covariance = np.asarray(self.covariance_m2, dtype=float)
        if position.shape != (3,) or covariance.shape != (3, 3):
            raise ValueError("invalid position observation shape")
        if not np.isfinite(position).all() or not np.isfinite(covariance).all():
            raise ValueError("non-finite position observation")
        if not np.allclose(covariance, covariance.T, atol=1e-10):
            raise ValueError("observation covariance is asymmetric")
        np.linalg.cholesky(covariance)
        if not self.tag_id:
            raise ValueError("position observation lacks tag identity")
        if any(anchor < 0 or anchor > 7 for anchor in self.anchors):
            raise ValueError("anchor identity outside canonical 0..7")


@dataclass(frozen=True)
class UpdateDecision:
    accepted: bool
    reason: str
    nis: float | None
    innovation_m: np.ndarray
    applied_position_delta_m: np.ndarray
    influence_scale: float
    availability_applied_position_delta_m: np.ndarray = field(
        default_factory=lambda: np.zeros(3)
    )
    availability_applied_velocity_delta_mps: np.ndarray = field(
        default_factory=lambda: np.zeros(3)
    )
    availability_influence_scale: float = 1.0


@dataclass(frozen=True)
class RootOutput:
    output_time_s: float
    root_position_m: np.ndarray
    root_velocity_mps: np.ndarray
    root_covariance_m2: np.ndarray
    measurement_age_s: float | None
    active_mode: SystemMode
    accepted_uwb_source: str | None
    rejected_uwb_source: str | None
    rejection_reason: str | None
    degradation_state: str
    future_uwb_count: int = 0
    future_imu_count: int = 0
    preavailability_output_count: int = 0
    metadata: dict = field(default_factory=dict)
