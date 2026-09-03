from __future__ import annotations

import numpy as np
import pytest

from pure_imu_baseline.config import GEOMETRY, NODE_TO_SEGMENT, SEGMENT_ORDER
from pure_imu_baseline.decoder import assert_strict_timestamps
from pure_imu_baseline.math3d import (apply_mounting, conjugate, equivalent,
    from_axis_angle, mounting_calibration, multiply, relative, resample_quaternions,
    rotate, slerp_pair)
from pure_imu_baseline.skeleton import (assert_fixed_lengths, assert_laterality,
    forward_kinematics, validate_mapping)

I = np.array([1., 0., 0., 0.])


def test_01_identity_quaternion():
    assert np.allclose(rotate(I, [1, 2, 3]), [1, 2, 3])


def test_02_known_positive_90_rotation():
    q = from_axis_angle([0, 0, 1], np.pi/2)
    assert np.allclose(rotate(q, [1, 0, 0]), [0, 1, 0], atol=1e-9)


def test_03_known_negative_90_rotation():
    q = from_axis_angle([0, 0, 1], -np.pi/2)
    assert np.allclose(rotate(q, [1, 0, 0]), [0, -1, 0], atol=1e-9)


def test_04_quaternion_sign_equivalence():
    q = from_axis_angle([1, 2, 3], .7)
    assert equivalent(q, -q)


def test_05_composition_order():
    qx = from_axis_angle([1, 0, 0], np.pi/2); qz = from_axis_angle([0, 0, 1], np.pi/2)
    assert np.allclose(rotate(multiply(qz, qx), [0, 1, 0]), [0, 0, 1], atol=1e-9)
    assert not np.allclose(rotate(multiply(qx, qz), [0, 1, 0]), [0, 0, 1], atol=1e-9)


def test_06_active_passive_mutation():
    q = from_axis_angle([0, 0, 1], .4)
    assert not np.allclose(rotate(q, [1, 0, 0]), rotate(conjugate(q), [1, 0, 0]))


def test_07_wxyz_xyzw_mutation():
    q = from_axis_angle([0, 1, 0], .8); mutated = q[[1, 2, 3, 0]]
    assert not equivalent(q, mutated)


def test_08_parent_child_inversion():
    p = from_axis_angle([1, 0, 0], .2); c = multiply(p, from_axis_angle([0, 1, 0], .5))
    expected = from_axis_angle([0, 1, 0], .5)
    assert equivalent(relative(p, c), expected)
    assert not equivalent(relative(c, p), expected)


def test_09_left_right_mapping_swap_rejected():
    mutation = dict(NODE_TO_SEGMENT)
    mutation["BSFEC35"], mutation["BSFB165"] = mutation["BSFB165"], mutation["BSFEC35"]
    with pytest.raises(ValueError): validate_mapping(mutation)


def test_10_calibration_pose_exact_recovery():
    q_gs = from_axis_angle([1, 2, 3], 1.1); q_sb = mounting_calibration(q_gs)
    assert equivalent(apply_mounting(q_gs, q_sb), I)


def test_11_common_yaw_recenter():
    yaw = from_axis_angle([0, 0, 1], .9); q_sb = mounting_calibration(yaw)
    assert equivalent(apply_mounting(yaw, q_sb), I)


def test_12_slerp_sign_continuity():
    q = from_axis_angle([0, 0, 1], .6)
    mid = slerp_pair(I[None], (-q)[None], np.array([.5]))[0]
    assert equivalent(mid, from_axis_angle([0, 0, 1], .3))


def test_13_fixed_bone_length_invariance():
    n = 20; q = np.tile(I, (n, len(SEGMENT_ORDER), 1)); valid = np.ones((n, len(SEGMENT_ORDER)), bool)
    for i in range(n): q[i, 2] = from_axis_angle([1, 0, 0], i*.03)
    positions, _ = forward_kinematics(q, valid)
    checks = assert_fixed_lengths(positions)
    assert all(v["maximum_abs_error_m"] < 2e-6 for v in checks.values())


def test_14_timestamp_monotonicity():
    assert_strict_timestamps(np.array([10, 20, 30]))
    with pytest.raises(ValueError): assert_strict_timestamps(np.array([10, 20, 20]))


def test_15_gap_rejection():
    times = np.array([0., .01, .20, .21]); q = np.tile(I, (4, 1))
    _, valid = resample_quaternions(times, q, np.array([.005, .10, .205]), .05)
    assert valid.tolist() == [True, False, True]


def test_16_missing_node_degradation():
    q = np.tile(I, (1, len(SEGMENT_ORDER), 1)); valid = np.ones((1, len(SEGMENT_ORDER)), bool)
    valid[0, SEGMENT_ORDER.index("forearm_left")] = False
    pos, available = forward_kinematics(q, valid)
    assert np.all(np.isfinite(pos[0, 7]))  # right wrist remains available
    assert not np.any(np.isfinite(pos[0, 4]))  # left wrist unavailable


def test_17_mirrored_skeleton_negative_control():
    q = np.tile(I, (1, len(SEGMENT_ORDER), 1)); valid = np.ones((1, len(SEGMENT_ORDER)), bool)
    pos, _ = forward_kinematics(q, valid); assert_laterality(pos)
    mirrored = pos.copy(); mirrored[..., 1] *= -1
    with pytest.raises(ValueError): assert_laterality(mirrored)
