from __future__ import annotations

import math

import numpy as np
import pytest

from biospur_fusion.imu.profiles import (
    JY61P_200HZ,
    LSM6DSV32X_200HZ_HAODR,
    profile_for_node,
    validate_axis_map,
)


def test_new_b120_unit_uses_lsm6dsv32x_scale() -> None:
    profile = profile_for_node("BSF857B")
    assert profile is LSM6DSV32X_200HZ_HAODR
    accel, gyro = profile.raw_to_si(
        np.array([0, 0, 1024]), np.array([0, 0, 1000]),
    )
    assert accel[2] == pytest.approx(9.80665 * 0.999424)
    assert gyro[2] == pytest.approx(math.radians(70.0))


def test_existing_b306_unit_retains_jy61p_scale() -> None:
    profile = profile_for_node("BSF31CC")
    assert profile is JY61P_200HZ
    accel, gyro = profile.raw_to_si(
        np.array([0, 0, 2048]), np.array([0, 0, 16384]),
    )
    assert accel[2] == pytest.approx(9.80665)
    assert gyro[2] == pytest.approx(math.radians(1000.0))


def test_unknown_identity_fails_closed() -> None:
    with pytest.raises(KeyError):
        profile_for_node("unit-under-test")
    with pytest.raises(KeyError):
        profile_for_node("BSFFFFF")


def test_axis_map_requires_proper_rotation() -> None:
    proper = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]], dtype=float)
    assert np.array_equal(validate_axis_map(proper), proper)
    with pytest.raises(ValueError, match="right-handed"):
        validate_axis_map(np.diag([-1.0, 1.0, 1.0]))
