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
