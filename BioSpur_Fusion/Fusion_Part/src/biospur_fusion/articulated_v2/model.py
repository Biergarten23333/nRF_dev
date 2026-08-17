from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np


PARENT = {
    "torso": "pelvis", "upper_arm_left": "torso", "forearm_left": "upper_arm_left",
    "upper_arm_right": "torso", "forearm_right": "upper_arm_right",
    "thigh_left": "pelvis", "shank_left": "thigh_left",
    "thigh_right": "pelvis", "shank_right": "thigh_right",
}


@dataclass
class SegmentState:
    q_L0_segment: np.ndarray = field(default_factory=lambda: np.array([1.0, 0.0, 0.0, 0.0]))
    gyro_bias_rad_s: np.ndarray = field(default_factory=lambda: np.zeros(3))
    accel_bias_m_s2: np.ndarray = field(default_factory=lambda: np.zeros(3))
    # Synthetic coverage revision B: 0.080 rad under-covered after causal
    # propagation (Wilson CI excluded 0.95). The product cone threshold remains
    # unchanged; this finite conditional initialization uncertainty is 0.0875.
    covariance: np.ndarray = field(default_factory=lambda: np.diag([0.0875**2] * 3 + [0.02**2] * 3 + [0.2**2] * 3))
    last_time_s: float | None = None
    last_sequence: int | None = None
    measurement_count: int = 0
    degraded_reasons: set[str] = field(default_factory=set)


@dataclass
class RootLocalState:
    position_m: np.ndarray = field(default_factory=lambda: np.zeros(3))
    velocity_m_s: np.ndarray = field(default_factory=lambda: np.zeros(3))
    covariance: np.ndarray = field(default_factory=lambda: np.diag([100.0] * 3 + [25.0] * 3))


@dataclass(frozen=True)
class FactorAudit:
    gyro_propagation: int
    gyro_bias_process: int
    accel_low_dynamic: int
    accel_dynamic: int
    accel_bias_state: int
    soft_joint_closure: int
    dominant_axis_rom: int
    temporal_process: int
    contact: int
    hard_zupt: int
    uwb: int
    phase1_orientation: int
    mounting_cluster: int
