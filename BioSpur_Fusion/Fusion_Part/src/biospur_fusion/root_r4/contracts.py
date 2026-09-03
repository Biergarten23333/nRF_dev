"""Frame, lineage, factor-authority, and physical-update contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable

import numpy as np


class LineageError(ValueError):
    """Raised when one physical UWB datum would enter two active factors."""


M1_PROTECTED_TARGETS = frozenset({
    "orientation", "segment_orientation", "joint_rotation", "joint_angle", "forward_kinematics",
    "validity", "reset", "bone_length", "sensor_to_segment", "left_right_mapping",
})
UWB_ROOT_TARGETS = frozenset({"root_translation", "root_velocity", "root_translational_uncertainty", "root_inertial_bias"})


def authorize_uwb_state_target(target: str) -> None:
    if target in M1_PROTECTED_TARGETS or target not in UWB_ROOT_TARGETS:
        raise PermissionError(f"UWB has no authority over {target}")


def validate_common_frame_configuration(*, frame_count: int, determinant: float = 1.0, scale: float = 1.0) -> None:
    if int(frame_count) != 1:
        raise ValueError("per-node world rotations rejected: exactly one common frame required")
    if determinant <= 0.0:
        raise ValueError("reflection rejected")
    if abs(float(scale) - 1.0) > 1e-12:
        raise ValueError("free scale rejected")


@dataclass(frozen=True)
class FrameContract:
    """The only authorized Root-R4 frame direction and rotation family."""

    source: str = "V4"
    destination: str = "N"
    vector_equation: str = "v^N = R_N_from_V4 v^V4"
    inverse_equation: str = "R_V4_from_N = R_N_from_V4^T"
    v4_axes: str = "metric right-handed layout gauge: A origin, A->B +X, C selects +Y, upper layer +Z"
    n_axes: str = "+X forward, +Y left, +Z up"
    quaternion: str = "wxyz Hamilton active local-to-global"
    rotation_family: str = "SO(2) yaw embedded in SO(3); roll/pitch fixed by both frozen +Z-up contracts"
    scale: float = 1.0

    def validate(self, rotation_n_from_v4: np.ndarray) -> dict:
        rotation = np.asarray(rotation_n_from_v4, float)
        if rotation.shape != (3, 3):
            raise ValueError("rotation must be 3x3")
        determinant = float(np.linalg.det(rotation))
        orthogonality = float(np.linalg.norm(rotation.T @ rotation - np.eye(3), ord="fro"))
        if determinant <= 0.0 or abs(determinant - 1.0) > 1e-8 or orthogonality > 1e-8:
            raise ValueError("frame must be a proper unit-scale rotation")
        return {"determinant": determinant, "orthogonality_error_fro": orthogonality, "scale": self.scale}


def yaw_rotation_v4_from_n(yaw_rad: float) -> np.ndarray:
    """Return R_V4_from_N for an active yaw about common +Z."""

    c, s = math.cos(float(yaw_rad)), math.sin(float(yaw_rad))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def wrap_degrees(value: float) -> float:
    return float((value + 180.0) % 360.0 - 180.0)


@dataclass
class FactorLedger:
    """Proves that each raw event contributes to at most one active factor."""

    ownership: dict[str, str] = field(default_factory=dict)
    factors: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def add_raw_factor(self, factor_id: str, raw_event_id: str) -> None:
        self._claim(factor_id, (raw_event_id,))

    def add_t4_factor(self, factor_id: str, constituent_raw_event_ids: Iterable[str]) -> None:
        members = tuple(constituent_raw_event_ids)
        if not members:
            raise LineageError("T4 factor has no constituent ranges")
        self._claim(factor_id, members)

    def _claim(self, factor_id: str, members: tuple[str, ...]) -> None:
        if factor_id in self.factors:
            raise LineageError(f"duplicate factor id: {factor_id}")
        if len(set(members)) != len(members):
            raise LineageError(f"one raw event repeated inside factor: {factor_id}")
        collisions = {item: self.ownership[item] for item in members if item in self.ownership}
        if collisions:
            raise LineageError(f"REJECTED_NAIVE_T4_RAW_DOUBLE_COUNTING: {collisions}")
        self.factors[factor_id] = members
        for item in members:
            self.ownership[item] = factor_id

    def audit(self) -> dict:
        flattened = [item for members in self.factors.values() for item in members]
        return {
            "factor_count": len(self.factors),
            "owned_raw_event_count": len(self.ownership),
            "maximum_active_factors_per_raw_event": 1 if flattened else 0,
            "duplicate_claim_count": len(flattened) - len(set(flattened)),
            "pass": len(flattened) == len(set(flattened)),
        }


@dataclass
class PhysicalSweepLimiter:
    """Cap one physical sweep as a unit, regardless of its range-factor count."""

    maximum_correction_m: float = 0.050
    _used_by_sweep: dict[str, np.ndarray] = field(default_factory=dict)

    def apply(self, sweep_id: str, proposed_delta_m: np.ndarray) -> np.ndarray:
        proposed = np.asarray(proposed_delta_m, float)
        used = self._used_by_sweep.setdefault(sweep_id, np.zeros(3))
        remaining = max(0.0, self.maximum_correction_m - float(np.linalg.norm(used)))
        norm = float(np.linalg.norm(proposed))
        applied = proposed.copy() if norm <= remaining or norm == 0.0 else proposed * (remaining / norm)
        candidate = used + applied
        if np.linalg.norm(candidate) > self.maximum_correction_m + 1e-12:
            scale = self.maximum_correction_m / float(np.linalg.norm(candidate))
            applied = candidate * scale - used
            candidate = used + applied
        self._used_by_sweep[sweep_id] = candidate
        return applied

    def cumulative(self, sweep_id: str) -> float:
        return float(np.linalg.norm(self._used_by_sweep.get(sweep_id, np.zeros(3))))
