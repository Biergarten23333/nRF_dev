import numpy as np
import pytest

from biospur_fusion.root_r3.estimator import (
    CausalDelayedRootFilter,
    RootFilterConfig,
    propagate_inertial,
    update_position,
)
from biospur_fusion.root_r3.models import ImuSample, PositionObservation, RootState, SystemMode


def state(time_s=0.0, position=(0.0, 0.0, 0.0), covariance_scale=1.0):
    vector = np.zeros(9); vector[:3] = position
    return RootState(time_s, vector, np.eye(9) * covariance_scale)


def observation(position, *, measurement=0.0, available=0.0, covariance=1.0, frame=True, tag="T"):
    return PositionObservation(measurement, available, np.asarray(position, float),
                               np.eye(3) * covariance, tag, (0, 1, 2, 3),
                               frame_valid=frame)


def test_stationary_specific_force_does_not_translate():
    config = RootFilterConfig()
    propagated, _ = propagate_inertial(
        state(), 1.0, np.array([0.0, 0.0, 9.80665]), np.eye(3), config)
    assert np.allclose(propagated.position_m, 0.0, atol=1e-12)
    assert np.allclose(propagated.velocity_mps, 0.0, atol=1e-12)
    np.linalg.cholesky(propagated.covariance)


def test_unqualified_frame_rejection_is_byte_stable():
    original = state(covariance_scale=0.2)
    updated, decision = update_position(
        original, observation([0.1, 0.0, 0.0], frame=False), RootFilterConfig())
    assert not decision.accepted and decision.reason == "REJECT_FRAME_UNQUALIFIED"
    assert updated.vector.tobytes() == original.vector.tobytes()
    assert updated.covariance.tobytes() == original.covariance.tobytes()


def test_nis_outlier_rejection_is_byte_stable():
    original = state(covariance_scale=0.01)
    updated, decision = update_position(
        original, observation([10.0, 0.0, 0.0], covariance=0.01), RootFilterConfig())
    assert not decision.accepted and decision.reason == "REJECT_NIS"
    assert updated.vector.tobytes() == original.vector.tobytes()
    assert updated.covariance.tobytes() == original.covariance.tobytes()


def test_single_update_influence_is_bounded():
    config = RootFilterConfig(maximum_position_influence_m=0.02, nis_limit_3d=1e9)
    updated, decision = update_position(
        state(covariance_scale=1.0), observation([1.0, 0.0, 0.0], covariance=0.01), config)
    assert decision.accepted
    assert np.linalg.norm(updated.position_m) <= 0.020000000001
    assert decision.influence_scale < 1.0
    np.linalg.cholesky(updated.covariance)


def test_delayed_update_replays_only_available_imu_and_does_not_rewrite_output():
    config = RootFilterConfig(maximum_position_influence_m=1.0, nis_limit_3d=1e9,
                              fixed_lag_s=0.2, recovery_good_events=2)
    filt = CausalDelayedRootFilter(state(covariance_scale=1.0), config)
    for i, (tm, ta) in enumerate(((0.01, 0.02), (0.02, 0.025), (0.03, 0.04))):
        assert filt.add_imu(ImuSample(tm, ta, np.array([0.0, 0.0, 9.80665]), np.eye(3), i))
    obs1 = observation([0.10, 0.0, 0.0], measurement=0.015, available=0.045, covariance=0.05, tag="A")
    d1 = filt.add_position(obs1); assert d1.accepted
    first = filt.emit(0.045, d1, obs1)
    first_bytes = first.root_position_m.tobytes()
    obs2 = observation([0.12, 0.0, 0.0], measurement=0.025, available=0.050, covariance=0.05, tag="B")
    d2 = filt.add_position(obs2); assert d2.accepted
    second = filt.emit(0.050, d2, obs2)
    assert first.root_position_m.tobytes() == first_bytes
    assert second.output_time_s >= obs2.availability_time_s
    assert second.future_imu_count == second.future_uwb_count == second.preavailability_output_count == 0
    assert second.active_mode == SystemMode.FUSED_NOMINAL


def test_future_and_preavailability_fail_closed():
    filt = CausalDelayedRootFilter(state())
    assert not filt.add_imu(ImuSample(1.0, 0.5, np.zeros(3), np.eye(3), 1))
    assert filt.mode == SystemMode.TIME_INVALID and filt.future_imu_count == 1
    assert filt.add_imu(ImuSample(0.1, 0.2, np.array([0.0, 0.0, 9.80665]), np.eye(3), 2))
    with pytest.raises(ValueError, match="output before"):
        filt.emit(0.1)


def test_inertial_bias_jacobian_matches_finite_difference():
    config = RootFilterConfig(inertial_acceleration_noise_mps2_sqrt_hz=0.0,
                              accelerometer_bias_rw_mps3_sqrt_hz=0.0)
    base = state(covariance_scale=0.1)
    force = np.array([0.2, -0.1, 9.7])
    nominal, phi = propagate_inertial(base, 0.02, force, np.eye(3), config)
    epsilon = 1e-6
    for axis in range(3):
        perturbed_vector = base.vector.copy(); perturbed_vector[6 + axis] += epsilon
        perturbed, _ = propagate_inertial(RootState(0.0, perturbed_vector, base.covariance),
                                          0.02, force, np.eye(3), config)
        numerical = (perturbed.vector - nominal.vector) / epsilon
        assert np.allclose(numerical, phi[:, 6 + axis], atol=2e-8)


def test_m1_reset_enters_explicit_recovery_without_propagation():
    filt = CausalDelayedRootFilter(state())
    before = filt.current_state.vector.tobytes()
    accepted = filt.add_imu(ImuSample(0.01, 0.02, np.array([0.0, 0.0, 9.80665]),
                                      np.eye(3), 1, m1_reset=True))
    assert not accepted and filt.mode == SystemMode.M1_RESET_RECOVERY
    assert filt.current_state.vector.tobytes() == before
