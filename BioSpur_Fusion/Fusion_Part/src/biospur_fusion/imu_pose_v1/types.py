from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
import numpy as np


@dataclass(frozen=True)
class ImuSample:
    node_id: str
    time_s: float
    timer2_us: int
    sequence: int
    gyro_rad_s: np.ndarray
    accel_m_s2: np.ndarray
    sample_age_s: float
    boot_id: int = 0
    rest_evidence: bool = False

    @property
    def uid(self) -> str:
        return f"{self.node_id}:{self.boot_id}:{self.sequence}:{self.timer2_us}"


@dataclass(frozen=True)
class FrontendOutput:
    node_id: str
    time_s: float
    sample_uid: str
    q_WI: np.ndarray
    gyro_bias: np.ndarray
    accel_bias: np.ndarray
    covariance: np.ndarray
    rest_detected: bool
    accel_likelihood_used: bool
    reset_epoch: int
    innovation_norm: float


@dataclass
class FactorActivation:
    count: int = 0
    residual_sq: float = 0.0
    jacobian_nonzero_blocks: int = 0
    state_delta_sq: float = 0.0
    information_trace: float = 0.0

    def add(self, residual: np.ndarray, H: np.ndarray, delta: np.ndarray, info: np.ndarray) -> None:
        self.count += 1
        self.residual_sq += float(np.dot(residual, residual))
        self.jacobian_nonzero_blocks += int(sum(np.linalg.norm(H[:, i:i+3]) > 1e-12 for i in range(0, H.shape[1], 3)))
        self.state_delta_sq += float(np.dot(delta, delta))
        self.information_trace += float(np.trace(info))


@dataclass(frozen=True)
class PoseFrame:
    time_s: float
    cutoff_time_s: float
    segment_quaternions_W_S: Mapping[str, np.ndarray]
    joint_quaternions_parent_child: Mapping[str, np.ndarray]
    normalized_joint_positions: Mapping[str, np.ndarray]
    segment_tilt_sigma_rad: Mapping[str, float]
    joint_relative_sigma_rad: Mapping[str, float]
    segment_quality: Mapping[str, str]
    joint_quality: Mapping[str, str]
    whole_body_available: bool
    degraded_reasons: tuple[str, ...]
    gauges: tuple[str, ...] = ("GLOBAL_YAW_GAUGE_ACTIVE", "ROOT_WORLD_POSITION_UNAVAILABLE")
    active_modality: str = "IMU_ONLY"
    root_position_L0: np.ndarray = field(default_factory=lambda: np.zeros(3))


SEGMENTS = (
    "pelvis", "torso", "upper_arm_left", "forearm_left",
    "upper_arm_right", "forearm_right", "thigh_left", "shank_left",
    "thigh_right", "shank_right",
)
