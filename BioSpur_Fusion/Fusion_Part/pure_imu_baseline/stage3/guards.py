"""Executable Stage 3 mutation and leakage firewalls."""
from __future__ import annotations

import numpy as np


def assert_shared_configuration(configurations: list[dict]) -> None:
    if not configurations or any(value != configurations[0] for value in configurations[1:]):
        raise ValueError("capture-specific gain/configuration selection is forbidden")


def reject_timestamp_knots(knots) -> None:
    if knots:
        raise ValueError("timestamp-specific correction knots are forbidden")


def assert_raw_immutable(before: np.ndarray, after: np.ndarray) -> None:
    if not np.array_equal(before, after, equal_nan=True):
        raise ValueError("raw q_GB mutation")


def assert_geometry_immutable(reference: dict, candidate: dict) -> None:
    if reference != candidate:
        raise ValueError("per-frame or configured bone scaling is forbidden")


def validate_quaternion_contract(q: np.ndarray) -> None:
    q = np.asarray(q)
    if q.shape[-1] != 4 or not np.allclose(np.linalg.norm(q, axis=-1), 1.0, atol=2e-6):
        raise ValueError("wxyz unit-quaternion contract violated")
    # Frozen neutral identity is scalar-first. This guard catches the explicit
    # wxyz->xyzw mutation used by the production negative-control suite.
    if q.ndim >= 2 and np.allclose(q.reshape(-1, 4)[0], [0, 0, 0, 1], atol=1e-12):
        raise ValueError("probable xyzw ordering mutation")


def validate_active_left_correction(raw_q: np.ndarray, corrected_q: np.ndarray,
                                    correction_rad: np.ndarray) -> None:
    from pure_imu_baseline.math3d import multiply, normalize
    from .corrector import qz
    expected = normalize(multiply(qz(correction_rad), raw_q))
    if not np.allclose(np.abs(np.sum(expected * corrected_q, axis=-1)), 1.0, atol=1e-10):
        raise ValueError("active left-applied global-Z contract violated")
