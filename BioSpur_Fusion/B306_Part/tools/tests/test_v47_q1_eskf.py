import math

import numpy as np
import pytest

from v47_q1_eskf import (
    FrameBinding, MotionVetoGate, Q1Parameters, Q1T4ESKF,
    quaternion_exp, quaternion_from_two_vectors, quaternion_multiply,
    quaternion_normalize, quaternion_to_matrix,
)


def bound_frame(rotation=None):
    return FrameBinding(R_V4_N=np.eye(3) if rotation is None else rotation,
                        origin_V4_m=np.zeros(3), provenance="synthetic-test",
                        v4_navigation_rotation_valid=True, signed_axes_valid=True)


def initialized(binding=FrameBinding(), accel=(0., 0., 9.80665), gyro=(0., 0., 0.)):
    f = Q1T4ESKF(binding=binding)
    f.initialize_from_stationary(np.asarray(accel), np.asarray(gyro))
    return f


def propagate(f, duration, accel, gyro, dt=.005):
    for index in range(int(duration/dt)+1):
        f.propagate(index*dt, np.asarray(accel, float), np.asarray(gyro, float))


def test_quaternion_convention_and_frame_round_trip():
    q = quaternion_exp(np.array([.2, -.3, .4]))
    r = quaternion_to_matrix(q)
    assert np.allclose(r.T @ r, np.eye(3), atol=1e-12)
    binding = bound_frame(r)
    point_n = np.array([.3, -1.2, 2.])
    assert np.allclose(binding.v4_position_to_navigation(r @ point_n), point_n)


@pytest.mark.parametrize("axis", range(3))
@pytest.mark.parametrize("sign", (-1., 1.))
def test_constant_positive_negative_axis_rotation(axis, sign):
    f = initialized()
    omega = np.zeros(3); omega[axis] = sign*math.pi/2
    propagate(f, 1., [0, 0, 9.80665], omega)
    expected = quaternion_exp(omega)
    assert abs(float(f.q @ expected)) > .999999
    assert abs(np.linalg.norm(f.q)-1.) < 1e-12


def test_stationary_arbitrary_attitude_and_yaw_gauge():
    q_truth = quaternion_exp(np.array([.4, -.2, .7]))
    accel_b = quaternion_to_matrix(q_truth).T @ np.array([0., 0., 9.80665])
    f = initialized(accel=accel_b)
    up = quaternion_to_matrix(f.q) @ (accel_b/np.linalg.norm(accel_b))
    assert np.allclose(up, [0, 0, 1], atol=1e-10)
    # Gravity cannot determine yaw; the chosen initializer fixes the zero gauge.
    assert f.binding.yaw_gauge == "ARBITRARY_ZERO"
    assert f.P[8, 8] == pytest.approx(math.pi**2)


def test_gyro_bias_is_genuine_state_and_removed_from_propagation():
    bias = np.array([.01, -.02, .03])
    f = initialized(gyro=bias)
    propagate(f, 20., [0, 0, 9.80665], bias)
    assert np.linalg.norm(f.q-[1, 0, 0, 0]) < 1e-10
    assert np.allclose(f.b_g, bias)


def test_accel_bias_state_and_gravity_measurement_update():
    f = initialized()
    assert f.P.shape == (15, 15)
    before = f.b_a.copy()
    for _ in range(20):
        f.gravity_update(np.array([.08, -.04, 9.83665]))
    assert np.linalg.norm(f.b_a-before) > 0
    assert f.gravity_updates == 20


def test_constant_velocity_and_known_acceleration_with_bound_frame():
    f = initialized(bound_frame())
    f.v = np.array([1., -.5, .2])
    propagate(f, 2., [0, 0, 9.80665], [0, 0, 0])
    assert np.allclose(f.p, [2., -1., .4], atol=.006)
    g = initialized(bound_frame())
    propagate(g, 2., [1., 0, 9.80665], [0, 0, 0])
    assert g.p[0] == pytest.approx(2., abs=.012)
    assert g.v[0] == pytest.approx(2., abs=.006)


def test_combined_rotation_translation_stays_finite_psd():
    f = initialized(bound_frame())
    for index in range(4000):
        t = index*.005
        f.propagate(t, [math.sin(t), .2*math.cos(t), 9.80665], [.1, -.05, .2])
    eig = np.linalg.eigvalsh(f.P)
    assert np.isfinite(np.r_[f.p, f.v, f.q, f.b_a, f.b_g, eig]).all()
    assert eig[0] >= -1e-10
    assert np.max(np.abs(f.P-f.P.T)) < 1e-12


def test_t4_update_correction_and_unbound_fail_closed():
    unbound = initialized()
    assert unbound.t4_position_update([1, 2, 3]) is None
    assert unbound.blocked_t4_updates == 1 and unbound.t4_updates == 0
    f = initialized(bound_frame())
    f.p[:] = [1., -1., .5]
    before = np.linalg.norm(f.p)
    nis = f.t4_position_update([0., 0., 0.])
    assert nis is not None and np.linalg.norm(f.p) < before and f.t4_updates == 1


def test_zupt_recovers_velocity_and_contracts_covariance():
    f = initialized(bound_frame()); f.v[:] = [.3, -.2, .1]
    before_p = np.trace(f.P)
    f.zupt_update()
    assert np.linalg.norm(f.v) < np.linalg.norm([.3, -.2, .1])
    assert np.trace(f.P) < before_p


def test_timestamp_jitter_and_long_stability():
    f = initialized(); rng = np.random.default_rng(47); t = 0.
    for _ in range(20000):
        t += .005 + rng.uniform(-.0002, .0002)
        f.propagate(t, [0, 0, 9.80665], [0, 0, 0])
    assert f.max_quaternion_norm_error < 1e-12
    assert f.reinitializations == 0
    assert f.min_covariance_eigenvalue >= -1e-10


def test_quaternion_sign_equivalence_and_continuity():
    q = quaternion_exp([.2, .3, -.4])
    assert np.allclose(quaternion_normalize(-q, q), q)
    f = initialized(); propagate(f, 4., [0, 0, 9.80665], [0, 0, math.pi])
    assert f.max_quaternion_sign_jump < .01


def test_invalid_frame_binding_fails_consistency_gate():
    with pytest.raises(ValueError, match="proper rotation"):
        Q1T4ESKF(binding=FrameBinding(R_V4_N=np.diag([1., 1., -1.]),
            origin_V4_m=np.zeros(3), provenance="bad", v4_navigation_rotation_valid=True))


def test_hard_motion_veto_and_no_relock_during_rotation():
    gate = MotionVetoGate(); gate.set_lock([1, 2, 3])
    for i in range(200):
        gate.update(i*.05, gyro_rms_dps=20., gyro_angle_deg=10.,
                    accel_deviation_g=0., candidate_stable=True)
    assert gate.state == "MOVING"
    assert not any(x["to_state"] == "STATIONARY" for x in gate.transitions)
    assert np.array_equal(gate.locked_position, [1, 2, 3])


def test_settling_dwell_fully_resets_and_quiet_islands_do_not_concatenate():
    gate = MotionVetoGate()
    for i in range(10):
        gate.update(i*.05, gyro_rms_dps=2., gyro_angle_deg=1.,
                    accel_deviation_g=0., candidate_stable=True)
    for i in range(10, 35):
        gate.update(i*.05, gyro_rms_dps=0., gyro_angle_deg=0.,
                    accel_deviation_g=0., candidate_stable=True)
    assert gate.state == "SETTLING"
    gate.update(1.75, gyro_rms_dps=2., gyro_angle_deg=1.,
                accel_deviation_g=0., candidate_stable=True)
    assert gate.state == "MOVING" and gate.settling_elapsed == 0.
    # A sub-threshold quiet island after renewed motion cannot complete old dwell.
    for i in range(1, 20):
        gate.update(1.75+i*.05, gyro_rms_dps=0., gyro_angle_deg=0.,
                    accel_deviation_g=0., candidate_stable=True)
    assert gate.state != "STATIONARY"


def test_small_position_change_gyro_only_release_medium_high_regression():
    gate = MotionVetoGate()
    for i in range(8):
        gate.update(i*.05, gyro_rms_dps=.8, gyro_angle_deg=.8,
                    accel_deviation_g=0., candidate_stable=True)
    assert gate.state == "MOVING"


def test_common_vibration_fleet_context_does_not_release_node():
    gate = MotionVetoGate()
    for i in range(20):
        gate.update(i*.05, gyro_rms_dps=5., gyro_angle_deg=2.,
                    accel_deviation_g=.2, candidate_stable=True,
                    fleet_common_mode=True)
    assert gate.state == "STATIONARY"


def test_deterministic_replay():
    def once():
        f = initialized(bound_frame())
        for i in range(1000):
            f.propagate(i*.005, [0.1, 0, 9.80665], [0, 0, .05])
            if i and i % 100 == 0: f.t4_position_update([0, 0, 0])
        return np.r_[f.p, f.v, f.q, f.b_a, f.b_g, f.P.ravel()]
    assert np.array_equal(once(), once())
