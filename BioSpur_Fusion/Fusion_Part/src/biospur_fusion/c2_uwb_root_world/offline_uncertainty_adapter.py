"""Offline-only covariance audit for C2 scalar raw-range factors.

This module neither estimates measurement noise nor mutates a Root-R3 state.
It verifies and exposes the uncertainty algebra already represented by a
``RawRangeFactorLinearization``. Missing root--bias cross covariance remains
missing; it is never replaced with an invented diagonal calibration.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from biospur_fusion.c2_uwb_root_world.tight_range import RawRangeFactorLinearization
from biospur_fusion.root_r3.models import RootState


UNAVAILABLE = "UNAVAILABLE_NOT_PROPAGATED"
SUPPLIED = "SUPPLIED_FULL_AUGMENTED_COVARIANCE"


def _readonly(value: object, shape: tuple[int, ...]) -> np.ndarray:
    result = np.array(value, dtype=float, copy=True)
    if result.shape != shape or not np.all(np.isfinite(result)):
        raise ValueError(f"expected finite array with shape {shape}")
    result.setflags(write=False)
    return result


def _covariance(value: object, shape: tuple[int, int], name: str) -> np.ndarray:
    result = _readonly(value, shape)
    if not np.allclose(result, result.T, rtol=0.0, atol=1e-12):
        raise ValueError(f"{name} is asymmetric")
    scale = max(1.0, float(np.linalg.norm(result, 2)))
    if float(np.linalg.eigvalsh(result)[0]) < -100.0 * np.finfo(float).eps * scale:
        raise ValueError(f"{name} is not positive semidefinite")
    return result


def _same(left: np.ndarray, right: np.ndarray, name: str) -> None:
    if not np.allclose(left, right, rtol=2e-14, atol=2e-14):
        raise ValueError(f"factor {name} is internally inconsistent or tampered")


@dataclass(frozen=True)
class OfflineRangeUncertaintyAudit:
    anchors: tuple[int, ...]
    innovation_m: np.ndarray
    state_jacobian: np.ndarray
    sensor_r_m2: np.ndarray
    bias_prior_total_r_m2: np.ndarray
    robust_effective_r_m2: np.ndarray
    prior_s_m2: np.ndarray
    robust_effective_s_m2: np.ndarray
    prior_nis: float
    robust_effective_nis: float
    rank: int
    condition: float
    root_bias_cross_covariance_m2: np.ndarray | None
    augmented_s_m2: np.ndarray | None
    augmented_nis: float | None
    cross_covariance_status: str
    structurally_valid_link_count: int
    deleted_link_count: int = 0
    calibrated_R: bool = False
    scientific_pass: bool = False
    production_ready: bool = False

    def __post_init__(self) -> None:
        count = len(self.anchors)
        for name, shape in (
            ("innovation_m", (count,)), ("state_jacobian", (count, 9)),
            ("sensor_r_m2", (count, count)),
            ("bias_prior_total_r_m2", (count, count)),
            ("robust_effective_r_m2", (count, count)),
            ("prior_s_m2", (count, count)),
            ("robust_effective_s_m2", (count, count)),
        ):
            object.__setattr__(self, name, _readonly(getattr(self, name), shape))
        if self.root_bias_cross_covariance_m2 is not None:
            object.__setattr__(self, "root_bias_cross_covariance_m2", _readonly(
                self.root_bias_cross_covariance_m2, (9, count)))
        if self.augmented_s_m2 is not None:
            object.__setattr__(self, "augmented_s_m2", _readonly(
                self.augmented_s_m2, (count, count)))
        if (
            self.deleted_link_count != 0
            or self.structurally_valid_link_count != count
            or self.rank != 3
            or not math.isfinite(self.condition)
            or self.condition <= 0.0
            or self.calibrated_R
            or self.scientific_pass
            or self.production_ready
        ):
            raise ValueError("offline uncertainty boundary is invalid")


def adapt_raw_range_uncertainty(
    factor: RawRangeFactorLinearization,
    state: RootState,
    *,
    augmented_covariance: np.ndarray | None = None,
    require_joint_root_bias_covariance: bool = False,
) -> OfflineRangeUncertaintyAudit:
    """Recompute the complete prior/effective covariance audit fail-closed."""

    if type(require_joint_root_bias_covariance) is not bool:
        raise ValueError("joint covariance request must be exact bool")
    count = len(factor.anchors)
    if count < 4 or len(set(factor.anchors)) != count:
        raise ValueError("factor identities are insufficient or duplicated")
    if any(anchor < 0 or anchor >= 8 for anchor in factor.anchors):
        raise ValueError("factor anchor identity is outside the C2 inventory")
    h = _readonly(factor.state_jacobian, (count, 9))
    innovation = _readonly(factor.innovations_m, (count,))
    p = _covariance(state.covariance, (9, 9), "root covariance")
    sensor_r = _covariance(factor.sensor_r_m2, (count, count), "sensor R")
    prior_r = _covariance(factor.r_prior_m2, (count, count), "bias-prior total R")
    if np.any(np.diag(sensor_r) <= 0.0) or np.any(np.diag(prior_r) <= 0.0):
        raise ValueError("range covariance diagonal must be strictly positive")
    if np.any(np.abs(sensor_r - np.diag(np.diag(sensor_r))) > 1e-14):
        raise ValueError("sensor R must remain diagonal")
    if np.any(np.abs(prior_r - np.diag(np.diag(prior_r))) > 1e-14):
        raise ValueError("bias-prior total R must remain diagonal")
    expected_sensor = np.square(factor.quality_sigma_m) / factor.information_weights
    expected_prior = (
        np.square(factor.quality_sigma_m) + factor.bias_variance_m2
    ) / factor.information_weights
    _same(np.diag(sensor_r), expected_sensor, "sensor sigma/R separation")
    _same(np.diag(prior_r), expected_prior, "bias variance/R separation")
    if np.any(factor.robust_weights <= 0.0) or np.any(factor.robust_weights > 1.0):
        raise ValueError("robust weights must be in (0,1]")
    effective_r = np.diag(np.diag(prior_r) / factor.robust_weights)
    prior_s = h @ p @ h.T + prior_r
    effective_s = h @ p @ h.T + effective_r
    _covariance(prior_s, (count, count), "prior S")
    _covariance(effective_s, (count, count), "robust effective S")
    _same(prior_s, factor.s_prior_m2, "prior S")
    prior_nis = float(innovation @ np.linalg.solve(prior_s, innovation))
    if not math.isclose(prior_nis, factor.prior_nis, rel_tol=2e-14, abs_tol=2e-14):
        raise ValueError("factor prior NIS is internally inconsistent or tampered")
    effective_nis = float(innovation @ np.linalg.solve(effective_s, innovation))
    design = h[:, :3] / np.sqrt(np.diag(prior_r))[:, None]
    singular = np.linalg.svd(design, compute_uv=False)
    tolerance = max(design.shape) * np.finfo(float).eps * singular[0]
    rank = int(np.sum(singular > tolerance))
    condition = math.inf if rank < 3 else float(np.linalg.cond(design.T @ design))
    if rank != factor.rank or not math.isclose(
        condition, factor.condition, rel_tol=2e-13, abs_tol=2e-13
    ):
        raise ValueError("factor rank/condition is internally inconsistent or tampered")
    if rank != 3 or not math.isfinite(condition):
        raise ValueError("range geometry is singular")

    cross = augmented_s = augmented_nis = None
    status = UNAVAILABLE
    factor_has_augmented = factor.augmented_jacobian is not None
    if augmented_covariance is None:
        if factor_has_augmented or factor.s_augmented_m2 is not None:
            raise ValueError("factor claims augmented covariance without supplied owner")
        if require_joint_root_bias_covariance:
            raise ValueError("JOINT_ROOT_BIAS_COVARIANCE_UNAVAILABLE_NOT_PROPAGATED")
    else:
        augmented = _covariance(
            augmented_covariance, (9 + count, 9 + count), "augmented covariance")
        if not factor_has_augmented or factor.s_augmented_m2 is None:
            raise ValueError("supplied augmented covariance is not bound by the factor")
        if not np.array_equal(augmented[:9, :9], state.covariance):
            raise ValueError("augmented root marginal changed")
        if not np.array_equal(
            augmented[9:, 9:], np.diag(factor.bias_variance_m2)
        ):
            raise ValueError("augmented bias marginal changed")
        expected_h = np.concatenate((h, np.eye(count)), axis=1)
        _same(expected_h, factor.augmented_jacobian, "augmented Jacobian")
        augmented_s = expected_h @ augmented @ expected_h.T + sensor_r
        _same(augmented_s, factor.s_augmented_m2, "augmented S")
        augmented_nis = float(innovation @ np.linalg.solve(augmented_s, innovation))
        if factor.augmented_nis is None or not math.isclose(
            augmented_nis, factor.augmented_nis, rel_tol=2e-14, abs_tol=2e-14
        ):
            raise ValueError("factor augmented NIS is inconsistent or tampered")
        cross = augmented[:9, 9:]
        status = SUPPLIED

    return OfflineRangeUncertaintyAudit(
        tuple(factor.anchors), innovation, h, sensor_r, prior_r, effective_r,
        prior_s, effective_s, prior_nis, effective_nis, rank, condition,
        cross, augmented_s, augmented_nis, status, count,
    )
