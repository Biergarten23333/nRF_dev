from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

import numpy as np


SEGMENTS = (
    "pelvis", "torso", "upper_arm_left", "forearm_left",
    "upper_arm_right", "forearm_right", "thigh_left", "shank_left",
    "thigh_right", "shank_right",
)


@dataclass(frozen=True, slots=True)
class ImuObservation:
    node_id: str
    boot_epoch: int
    timer2_us: int
    common_time_ns: int
    sequence: int
    gyro_rad_s: np.ndarray
    accel_m_s2: np.ndarray
    sample_age_support_us: tuple[int, int] = (0, 5000)
    source_record_offset: int = 0
    purpose: str = "PROPAGATION_ONLY"

    @property
    def uid(self) -> str:
        return f"{self.node_id}:{self.boot_epoch}:{self.timer2_us}:{self.sequence}:{self.source_record_offset}"


@dataclass(frozen=True, slots=True)
class FrontendFrame:
    node_id: str
    boot_epoch: int
    sample_uid: str
    sample_time_ns: int
    q_WI: np.ndarray
    gyro_bias: np.ndarray
    accel_bias: np.ndarray
    covariance: np.ndarray
    rest_detected: bool
    status: str
    input_age_ns: int
    reset_epoch: int


@dataclass(frozen=True, slots=True)
class SegmentCalibration:
    node_id: str
    segment: str
    q_I_S: np.ndarray
    covariance_rad2: np.ndarray
    cross_covariance_rad2: np.ndarray
    identified_direction_rank: int
    twist_status: str
    twist_convention_rad: float
    sign_hypotheses: tuple[tuple[int, float], ...]
    prior_dominance: float
    fit_action_ids: tuple[str, ...]
    layout_class: str


@dataclass(frozen=True, slots=True)
class CalibrationBundle:
    by_node: Mapping[str, SegmentCalibration]
    mapping: Mapping[str, str]
    fit_action_ids: tuple[str, ...]
    fit_factor_counts: Mapping[str, int]
    parameter_order: tuple[str, ...]
    parameter_covariance_rad2: np.ndarray
    frozen_sha256: str
    final_still_static_factor_count: int = 0

    @staticmethod
    def freeze(by_node: dict[str, SegmentCalibration], mapping: dict[str, str],
               fit_action_ids: tuple[str, ...], fit_factor_counts: dict[str, int],
               parameter_order: tuple[str, ...], parameter_covariance_rad2: np.ndarray,
               frozen_sha256: str) -> "CalibrationBundle":
        return CalibrationBundle(
            MappingProxyType(dict(by_node)), MappingProxyType(dict(mapping)),
            tuple(fit_action_ids), MappingProxyType(dict(fit_factor_counts)),
            tuple(parameter_order), np.asarray(parameter_covariance_rad2, float).copy(),
            frozen_sha256, 0,
        )


@dataclass(frozen=True, slots=True)
class FactorLedgerRow:
    tick_ns: int
    factor: str
    source_uids: tuple[str, ...]
    accepted: bool
    residual_norm: float
    jacobian_blocks: tuple[int, ...]
    weight: float
    line_search_state_change_rad: float


@dataclass(frozen=True, slots=True)
class PoseTick:
    scheduled_time_ns: int
    status: str
    segment_quaternions_W_S: Mapping[str, np.ndarray]
    segment_covariance_rad2: np.ndarray
    joint_positions_L0: Mapping[str, np.ndarray]
    input_age_ns: Mapping[str, int]
    gauges: tuple[str, ...] = ("L0_YAW_CONVENTION_FIXED", "ROOT_WORLD_POSITION_UNAVAILABLE")
    usability: Mapping[str, bool | str] = field(default_factory=dict)
