import numpy as np
import pytest

from biospur_fusion.root_r3.estimator import (
    AuthoritativeBaselineReconstructionError,
    CausalDelayedRootFilter,
    RootFilterConfig,
    _propagate_edge_piece,
    propagate_inertial,
    update_position,
)
from biospur_fusion.root_r3.models import (
    AdditiveRootConstraint,
    BoundedTargetRootConstraint,
    ImuSample,
    PositionObservation,
    RootState,
    SystemMode,
)


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


def test_contact_manifold_conflict_is_consumed_once_and_health_rejected():
    original = state(covariance_scale=0.2)
    filt = CausalDelayedRootFilter(original)
    obs = PositionObservation(
        0.0, 0.1, np.array([0.02, 0.0, 0.0]), np.eye(3) * 0.1,
        "T", (0, 1, 2, 3),
        quality_state="REJECT_CONTACT_MANIFOLD_CONFLICT",
    )
    decision = filt.add_position(obs, processing_time_s=0.1)

    assert not decision.accepted
    assert decision.reason == "REJECT_CONTACT_MANIFOLD_CONFLICT"
    assert filt.current_state.vector.tobytes() == original.vector.tobytes()
    assert filt.current_state.covariance.tobytes() == original.covariance.tobytes()
    health = filt.health_snapshot()["tags"]["T"]
    assert health["accepted"] == 0
    assert health["rejected"] == 1


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


def test_asynchronous_availability_advance_is_current_and_rejects_reversal():
    filt = CausalDelayedRootFilter(state())
    assert filt.add_imu(ImuSample(
        0.005, 0.005, np.array([0.0, 0.0, 9.80665]), np.eye(3), 1
    ))
    filt.advance_to_availability(0.007)
    assert np.isclose(filt.current_state.time_s, 0.007)
    np.testing.assert_allclose(filt.current_state.position_m, 0.0, atol=1e-12)
    with pytest.raises(ValueError, match="reversed time"):
        filt.advance_to_availability(0.006)


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


def _stationary_history(
    *, end_time_s=1.0, step_s=0.005, config=None, inertial=True
):
    filt = CausalDelayedRootFilter(
        state(covariance_scale=1.0),
        config or RootFilterConfig(
            maximum_position_influence_m=0.05,
            nis_limit_3d=1e12,
            fixed_lag_s=2.0,
            recovery_good_events=1,
        ),
        inertial=inertial,
    )
    for sequence, time_s in enumerate(
        np.arange(step_s, end_time_s + step_s / 2.0, step_s), start=1
    ):
        assert filt.add_imu(ImuSample(
            float(time_s),
            float(time_s),
            np.array([0.0, 0.0, 9.80665]),
            np.eye(3),
            sequence,
        ))
    return filt


def test_delayed_replay_caps_measurement_and_availability_influence():
    filt = _stationary_history()
    decision = filt.add_position(observation(
        [10.0, 0.0, 0.0],
        measurement=0.1,
        available=1.0,
        covariance=0.01,
    ), processing_time_s=1.0)

    assert decision.accepted
    assert np.linalg.norm(decision.applied_position_delta_m) <= 0.05 + 1e-12
    assert (
        np.linalg.norm(decision.availability_applied_position_delta_m)
        <= 0.05 + 1e-12
    )
    assert 0.0 < decision.availability_influence_scale < 1.0
    assert np.linalg.norm(filt.current_state.position_m) > 0.0
    assert (
        np.linalg.norm(decision.innovation_m - decision.applied_position_delta_m)
        < np.linalg.norm(decision.innovation_m)
    )


def test_delayed_replay_covariance_is_joseph_update_with_final_gain():
    filt = _stationary_history()
    original_delayed = filt._state_at(0.1)[1]
    obs = observation(
        [10.0, 0.0, 0.0], measurement=0.1, available=1.0, covariance=0.01
    )
    decision = filt.add_position(obs, processing_time_s=1.0)
    updated_delayed = filt._state_at(0.1)[1]

    h = np.zeros((3, 9)); h[:, :3] = np.eye(3)
    r = obs.covariance_m2
    s = h @ original_delayed.covariance @ h.T + r
    raw_gain = np.linalg.solve(s, h @ original_delayed.covariance).T
    final_gain = raw_gain * decision.influence_scale
    kh = final_gain @ h
    expected = (
        (np.eye(9) - kh)
        @ original_delayed.covariance
        @ (np.eye(9) - kh).T
        + final_gain @ r @ final_gain.T
    )
    np.testing.assert_allclose(updated_delayed.covariance, expected, atol=2e-14)


def test_delayed_replay_is_partition_deterministic():
    config = RootFilterConfig(
        maximum_position_influence_m=0.05,
        nis_limit_3d=1e12,
        fixed_lag_s=2.0,
        recovery_good_events=1,
        covariance_floor=1e-15,
    )
    dense = _stationary_history(step_s=0.005, config=config, inertial=False)
    sparse = _stationary_history(step_s=0.05, config=config, inertial=False)
    obs = observation(
        [10.0, 0.0, 0.0], measurement=0.1, available=1.0, covariance=0.01
    )
    dense_decision = dense.add_position(obs, processing_time_s=1.0)
    sparse_decision = sparse.add_position(obs, processing_time_s=1.0)

    np.testing.assert_allclose(
        dense.current_state.vector, sparse.current_state.vector, atol=2e-13
    )
    np.testing.assert_allclose(
        dense.current_state.covariance, sparse.current_state.covariance, atol=2e-12
    )
    np.testing.assert_allclose(
        dense_decision.availability_applied_position_delta_m,
        sparse_decision.availability_applied_position_delta_m,
        atol=2e-13,
    )


def test_no_delay_filter_update_matches_public_update_position():
    config = RootFilterConfig(
        maximum_position_influence_m=0.05,
        nis_limit_3d=1e12,
        recovery_good_events=1,
    )
    original = state(covariance_scale=0.3)
    obs = observation([0.02, -0.01, 0.005], covariance=0.04)
    expected_state, expected_decision = update_position(original, obs, config)
    filt = CausalDelayedRootFilter(original, config)
    actual_decision = filt.add_position(obs, processing_time_s=0.0)

    assert filt.current_state.vector.tobytes() == expected_state.vector.tobytes()
    assert (
        filt.current_state.covariance.tobytes()
        == expected_state.covariance.tobytes()
    )
    assert actual_decision.accepted == expected_decision.accepted
    assert actual_decision.reason == expected_decision.reason
    np.testing.assert_array_equal(
        actual_decision.applied_position_delta_m,
        expected_decision.applied_position_delta_m,
    )
    assert actual_decision.influence_scale == expected_decision.influence_scale
    np.testing.assert_array_equal(
        actual_decision.availability_applied_position_delta_m,
        expected_decision.applied_position_delta_m,
    )


def test_delayed_reject_does_not_mutate_history_or_current_state():
    filt = _journaled_constant_velocity_filter()
    before = [
        (snapshot.state.vector.tobytes(), snapshot.state.covariance.tobytes())
        for snapshot in filt._snapshots
    ]
    journal_before = [
        (event.sequence, event.time_s, event.owner, id(event.operator))
        for event in filt._constraint_events
    ]
    decision = filt.add_position(PositionObservation(
        0.1,
        0.3,
        np.array([0.1, 0.0, 0.0]),
        np.eye(3) * 0.1,
        "T",
        (0, 1, 2, 3),
        quality_state="REJECT_CONTACT_MANIFOLD_CONFLICT",
    ), processing_time_s=0.3)
    after = [
        (snapshot.state.vector.tobytes(), snapshot.state.covariance.tobytes())
        for snapshot in filt._snapshots
    ]

    assert not decision.accepted
    assert decision.reason == "REJECT_CONTACT_MANIFOLD_CONFLICT"
    assert after == before
    assert [
        (event.sequence, event.time_s, event.owner, id(event.operator))
        for event in filt._constraint_events
    ] == journal_before
    assert filt._next_constraint_sequence == 3


def test_zero_innovation_delayed_update_preserves_intervening_constraints():
    config = RootFilterConfig(
        maximum_position_influence_m=0.05,
        nis_limit_3d=1e12,
        fixed_lag_s=1.0,
        recovery_good_events=1,
    )
    filt = CausalDelayedRootFilter(
        state(covariance_scale=1.0), config, inertial=False
    )
    for sequence, time_s in enumerate((0.1, 0.2, 0.3), start=1):
        assert filt.add_imu(ImuSample(
            time_s,
            time_s,
            np.array([0.0, 0.0, 9.80665]),
            np.eye(3),
            sequence,
        ))
        if time_s >= 0.2:
            constrained_vector = filt.current_state.vector.copy()
            constrained_vector[0] += 0.04
            event_delta = np.zeros(9); event_delta[0] = 0.04
            filt.apply_current_constraint(RootState(
                time_s,
                constrained_vector,
                filt.current_state.covariance.copy(),
            ), operator=AdditiveRootConstraint(event_delta), owner="TEST_CONTACT")
    before = filt.current_state
    past_output = filt.emit(0.3)
    past_output_bytes = past_output.root_position_m.tobytes()
    past_covariance_bytes = past_output.root_covariance_m2.tobytes()
    original_delayed = filt._state_at(0.1)[1]
    obs = observation(
        [0.0, 0.0, 0.0],
        measurement=0.1,
        available=0.3,
        covariance=0.01,
    )
    decision = filt.add_position(obs, processing_time_s=0.3)

    assert decision.accepted
    np.testing.assert_array_equal(decision.innovation_m, np.zeros(3))
    assert past_output.root_position_m.tobytes() == past_output_bytes
    assert past_output.root_covariance_m2.tobytes() == past_covariance_bytes
    np.testing.assert_array_equal(
        filt.current_state.vector,
        before.vector,
        err_msg=(
            "zero-innovation replay erased a post-measurement contact event; "
            f"availability_delta={decision.availability_applied_position_delta_m}"
        ),
    )
    np.testing.assert_array_equal(
        decision.availability_applied_position_delta_m, np.zeros(3)
    )
    np.testing.assert_array_equal(
        decision.availability_applied_velocity_delta_mps, np.zeros(3)
    )
    updated_delayed = filt._state_at(0.1)[1]
    h = np.zeros((3, 9)); h[:, :3] = np.eye(3)
    r = obs.covariance_m2
    s = h @ original_delayed.covariance @ h.T + r
    gain = np.linalg.solve(s, h @ original_delayed.covariance).T
    kh = gain @ h
    expected_delayed_covariance = (
        (np.eye(9) - kh)
        @ original_delayed.covariance
        @ (np.eye(9) - kh).T
        + gain @ r @ gain.T
    )
    np.testing.assert_allclose(
        updated_delayed.covariance, expected_delayed_covariance, atol=2e-14
    )
    assert filt.current_state.covariance.tobytes() != before.covariance.tobytes()
    replayed, _ = filt._replay_from(
        0,
        updated_delayed,
        filt._snapshots[0].applied_constraint_cursor,
        0.3,
    )
    np.testing.assert_array_equal(
        replayed.covariance, filt.current_state.covariance
    )


def _add_x_constraint(filt, delta, *, owner):
    vector = filt.current_state.vector.copy()
    vector[0] += delta
    operator_delta = np.zeros(9); operator_delta[0] = delta
    filt.apply_current_constraint(
        RootState(
            filt.current_state.time_s,
            vector,
            filt.current_state.covariance.copy(),
        ),
        operator=AdditiveRootConstraint(operator_delta),
        owner=owner,
    )


def _journaled_constant_velocity_filter():
    config = RootFilterConfig(
        maximum_position_influence_m=0.05,
        nis_limit_3d=1e12,
        fixed_lag_s=1.0,
        recovery_good_events=1,
    )
    filt = CausalDelayedRootFilter(
        state(covariance_scale=1.0), config, inertial=False
    )
    for sequence, time_s in enumerate((0.1, 0.2, 0.3), start=1):
        assert filt.add_imu(ImuSample(
            time_s,
            time_s,
            np.array([0.0, 0.0, 9.80665]),
            np.eye(3),
            sequence,
        ))
        if time_s >= 0.2:
            _add_x_constraint(
                filt, 0.04, owner=f"CONTACT_{sequence}"
            )
    return filt


def test_nonzero_delayed_update_replays_contact_and_caps_attributable_effect():
    filt = _journaled_constant_velocity_filter()
    before = filt.current_state
    original_delayed = filt._state_at(0.1)[1]
    obs = observation(
        [1.0, 0.0, 0.0], measurement=0.1, available=0.3, covariance=0.01
    )
    decision = filt.add_position(obs, processing_time_s=0.3)

    assert decision.accepted
    availability_delta = decision.availability_applied_position_delta_m
    assert 0.0 < np.linalg.norm(availability_delta) <= 0.05 + 1e-12
    np.testing.assert_allclose(
        filt.current_state.position_m,
        before.position_m + availability_delta,
        atol=2e-14,
    )
    assert filt.current_state.position_m[0] > 0.08
    assert (
        np.linalg.norm(decision.innovation_m - decision.applied_position_delta_m)
        < np.linalg.norm(decision.innovation_m)
    )

    h = np.zeros((3, 9)); h[:, :3] = np.eye(3)
    r = obs.covariance_m2
    s = h @ original_delayed.covariance @ h.T + r
    raw_gain = np.linalg.solve(s, h @ original_delayed.covariance).T
    final_gain = raw_gain * decision.influence_scale
    kh = final_gain @ h
    expected = (
        (np.eye(9) - kh)
        @ original_delayed.covariance
        @ (np.eye(9) - kh).T
        + final_gain @ r @ final_gain.T
    )
    updated_delayed = filt._state_at(0.1)[1]
    np.testing.assert_allclose(updated_delayed.covariance, expected, atol=2e-14)


def test_authoritative_baseline_replay_matches_current_state():
    filt = _journaled_constant_velocity_filter()
    index, delayed, cursor, _edge = filt._state_at(0.15)
    replayed, _ = filt._replay_from(index, delayed, cursor, 0.3)

    np.testing.assert_allclose(
        replayed.vector, filt.current_state.vector, rtol=0.0, atol=2e-14
    )
    np.testing.assert_allclose(
        replayed.covariance,
        filt.current_state.covariance,
        rtol=0.0,
        atol=2e-9,
    )


def test_missing_authoritative_event_fails_baseline_reconstruction_closed():
    filt = _journaled_constant_velocity_filter()
    corrupted = filt.current_state.vector.copy(); corrupted[0] += 0.01
    filt._snapshots[-1] = type(filt._snapshots[-1])(
        RootState(
            filt.current_state.time_s,
            corrupted,
            filt.current_state.covariance.copy(),
        ),
        filt._snapshots[-1].applied_constraint_cursor,
        filt._snapshots[-1].incoming_edge,
    )
    before = filt.current_state.vector.tobytes()

    with pytest.raises(
        AuthoritativeBaselineReconstructionError,
        match="not reconstructible",
    ):
        filt.add_position(observation(
            [1.0, 0.0, 0.0],
            measurement=0.1,
            available=0.3,
            covariance=0.01,
        ), processing_time_s=0.3)
    assert filt.current_state.vector.tobytes() == before
    assert [event.sequence for event in filt._constraint_events] == [1, 2]


def test_variable_force_virtual_time_uses_one_right_end_interval_owner():
    config = RootFilterConfig(
        maximum_position_influence_m=0.05,
        nis_limit_3d=1e12,
        fixed_lag_s=1.0,
        recovery_good_events=1,
    )
    filt = CausalDelayedRootFilter(
        state(covariance_scale=0.1), config, inertial=True
    )
    stationary_force = np.array([0.0, 0.0, 9.80665])
    accelerating_force = np.array([1.0, 0.0, 9.80665])
    assert filt.add_imu(ImuSample(
        0.1, 0.1, stationary_force, np.eye(3), 1
    ))
    assert filt.add_imu(ImuSample(
        0.2, 0.2, accelerating_force, np.eye(3), 2
    ))
    virtual_state = filt._state_at(0.15)[1]
    np.testing.assert_allclose(
        virtual_state.position_m, [0.00125, 0.0, 0.0], atol=2e-14
    )
    zero_innovation = observation(
        virtual_state.position_m.copy(), measurement=0.15, available=0.2,
        covariance=0.01,
    )

    decision = filt.add_position(zero_innovation, processing_time_s=0.2)

    assert decision.accepted
    np.testing.assert_array_equal(decision.innovation_m, np.zeros(3))
    np.testing.assert_array_equal(
        decision.availability_applied_position_delta_m, np.zeros(3)
    )
    np.testing.assert_array_equal(
        decision.availability_applied_velocity_delta_mps, np.zeros(3)
    )
    np.testing.assert_allclose(
        filt.current_state.position_m, [0.005, 0.0, 0.0], atol=2e-14
    )
    np.testing.assert_allclose(
        filt.current_state.velocity_mps, [0.1, 0.0, 0.0], atol=2e-14
    )
    edge = filt._snapshots[-1].incoming_edge
    assert edge.input_owner == "DESTINATION_RIGHT_END_IMU_SAMPLE"
    np.testing.assert_array_equal(edge.force_sensor_mps2, accelerating_force)


def _variable_input_filter():
    config = RootFilterConfig(
        maximum_position_influence_m=0.05,
        nis_limit_3d=1e12,
        fixed_lag_s=1.0,
        recovery_good_events=1,
        covariance_floor=1e-15,
    )
    filt = CausalDelayedRootFilter(
        state(covariance_scale=0.1), config, inertial=True
    )
    stationary_force = np.array([0.0, 0.0, 9.80665])
    rotation_a = np.eye(3)
    angle = np.deg2rad(35.0)
    rotation_b = np.array([
        [np.cos(angle), -np.sin(angle), 0.0],
        [np.sin(angle), np.cos(angle), 0.0],
        [0.0, 0.0, 1.0],
    ])
    assert filt.add_imu(ImuSample(
        0.1, 0.1, stationary_force, rotation_a, 1
    ))
    assert filt.add_imu(ImuSample(
        0.2, 0.2, np.array([1.0, -0.4, 9.80665]), rotation_b, 2
    ))
    return filt


@pytest.mark.parametrize("fraction", (0.25, 0.5, 0.75))
def test_existing_edge_split_reconstructs_variable_input_endpoint(fraction):
    filt = _variable_input_filter()
    endpoint = filt.current_state
    measurement_time = 0.1 + 0.1 * fraction

    index, virtual, cursor, owner = filt._state_at(
        measurement_time
    )
    replayed, _ = filt._replay_from(
        index, virtual, cursor, endpoint.time_s
    )

    assert owner is filt._snapshots[-1].incoming_edge
    assert owner.input_owner == "DESTINATION_RIGHT_END_IMU_SAMPLE"
    np.testing.assert_allclose(
        replayed.vector, endpoint.vector, rtol=0.0, atol=2e-14
    )
    np.testing.assert_allclose(
        replayed.covariance, endpoint.covariance, rtol=0.0, atol=2e-13
    )


def test_virtual_existing_edge_requires_already_available_right_end_sample():
    config = RootFilterConfig(fixed_lag_s=1.0)
    filt = CausalDelayedRootFilter(
        state(covariance_scale=0.1), config, inertial=True
    )
    assert filt.add_imu(ImuSample(
        0.1, 0.1, np.array([0.0, 0.0, 9.80665]), np.eye(3), 1
    ))

    with pytest.raises(RuntimeError, match="authoritative incoming edge"):
        filt._state_at(0.15)

    assert filt.add_imu(ImuSample(
        0.2, 0.2, np.array([1.0, 0.0, 9.80665]), np.eye(3), 2
    ))
    assert filt._state_at(0.15) is not None


def test_multiple_virtual_splits_do_not_duplicate_endpoint_bias_noise():
    filt = _variable_input_filter()
    start = filt._snapshots[-2].state
    owner = filt._snapshots[-1].incoming_edge
    direct = filt.current_state

    split = start
    for target in (0.125, 0.15, 0.175, 0.2):
        split, _ = _propagate_edge_piece(
            split, target, owner, filt.config
        )

    np.testing.assert_allclose(split.vector, direct.vector, atol=2e-14)
    np.testing.assert_allclose(
        split.covariance, direct.covariance, rtol=0.0, atol=2e-13
    )
    before_endpoint, _ = _propagate_edge_piece(
        start, 0.175, owner, filt.config
    )
    expected_bias_increment = (
        filt.config.accelerometer_bias_rw_mps3_sqrt_hz ** 2 * 0.1
    )
    np.testing.assert_allclose(
        before_endpoint.covariance[6:9, 6:9],
        start.covariance[6:9, 6:9],
        rtol=0.0,
        atol=2e-14,
    )
    np.testing.assert_allclose(
        split.covariance[6:9, 6:9]
        - before_endpoint.covariance[6:9, 6:9],
        np.eye(3) * expected_bias_increment,
        rtol=0.0,
        atol=2e-13,
    )


def test_multiple_delayed_updates_share_one_existing_edge_owner():
    filt = _variable_input_filter()
    owner = filt._snapshots[-1].incoming_edge
    manual = filt._snapshots[-2].state

    for measurement_time in (0.125, 0.15):
        manual, _ = _propagate_edge_piece(
            manual, measurement_time, owner, filt.config
        )
        obs = observation(
            manual.position_m.copy(),
            measurement=measurement_time,
            available=0.2,
            covariance=0.02,
        )
        manual, expected_decision = update_position(manual, obs, filt.config)
        actual_decision = filt.add_position(obs, processing_time_s=0.2)
        assert expected_decision.accepted and actual_decision.accepted
        np.testing.assert_array_equal(
            actual_decision.availability_applied_position_delta_m,
            np.zeros(3),
        )

    manual, _ = _propagate_edge_piece(manual, 0.2, owner, filt.config)
    np.testing.assert_allclose(
        filt.current_state.vector, manual.vector, rtol=0.0, atol=2e-14
    )
    np.testing.assert_allclose(
        filt.current_state.covariance,
        manual.covariance,
        rtol=0.0,
        atol=2e-13,
    )
    edge_ids = {
        id(snapshot.incoming_edge)
        for snapshot in filt._snapshots
        if snapshot.state.time_s > 0.1
    }
    assert edge_ids == {id(owner)}


def test_tail_virtual_update_commits_processing_endpoint_once():
    config = RootFilterConfig(
        fixed_lag_s=1.0,
        nis_limit_3d=1e12,
        recovery_good_events=1,
        covariance_floor=1e-15,
    )
    filt = CausalDelayedRootFilter(
        state(covariance_scale=0.1), config, inertial=True
    )
    force = np.array([0.8, -0.2, 9.80665])
    assert filt.add_imu(ImuSample(0.1, 0.1, force, np.eye(3), 1))
    tail = filt._make_edge(
        start_time_s=0.1,
        end_time_s=0.2,
        force=force,
        rotation=np.eye(3),
        input_owner="TEST_EPHEMERAL_TAIL",
    )
    virtual, _ = _propagate_edge_piece(
        filt.current_state, 0.15, tail, config
    )
    expected_endpoint, _ = _propagate_edge_piece(
        filt.current_state, 0.2, tail, config
    )
    expected_virtual, expected_decision = update_position(
        virtual,
        observation(
            virtual.position_m.copy(),
            measurement=0.15,
            available=0.2,
            covariance=0.01,
        ),
        config,
    )
    assert expected_decision.accepted
    expected_posterior, _ = _propagate_edge_piece(
        expected_virtual, 0.2, tail, config
    )

    decision = filt.add_position(observation(
        virtual.position_m.copy(),
        measurement=0.15,
        available=0.2,
        covariance=0.01,
    ), processing_time_s=0.2)

    assert decision.accepted
    np.testing.assert_array_equal(decision.innovation_m, np.zeros(3))
    np.testing.assert_array_equal(
        decision.availability_applied_position_delta_m, np.zeros(3)
    )
    np.testing.assert_array_equal(
        decision.availability_applied_velocity_delta_mps, np.zeros(3)
    )
    assert filt.current_state.time_s == pytest.approx(0.2)
    np.testing.assert_allclose(
        filt.current_state.vector, expected_endpoint.vector, atol=2e-14
    )
    np.testing.assert_allclose(
        filt.current_state.covariance,
        expected_posterior.covariance,
        rtol=0.0,
        atol=2e-13,
    )
    assert filt._snapshots[-1].incoming_edge.input_owner == (
        "EPHEMERAL_AVAILABILITY_HELD_INPUT"
    )
    before_repeat = filt.current_state
    filt.advance_to_availability(0.2)
    np.testing.assert_array_equal(filt.current_state.vector, before_repeat.vector)
    np.testing.assert_array_equal(
        filt.current_state.covariance, before_repeat.covariance
    )


def test_availability_edge_matches_legacy_full_edge_endpoint():
    config = RootFilterConfig(covariance_floor=1e-15)
    filt = CausalDelayedRootFilter(
        state(covariance_scale=0.1), config, inertial=True
    )
    force = np.array([0.4, -0.3, 9.7])
    rotation = np.array([
        [0.0, -1.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ])
    assert filt.add_imu(ImuSample(0.1, 0.1, force, rotation, 1))
    before = filt.current_state
    expected, _ = propagate_inertial(before, 0.17, force, rotation, config)

    filt.advance_to_availability(0.17)

    np.testing.assert_array_equal(filt.current_state.vector, expected.vector)
    np.testing.assert_array_equal(
        filt.current_state.covariance, expected.covariance
    )
    assert filt._snapshots[-1].incoming_edge.input_owner == (
        "HELD_LAST_AVAILABLE_INPUT"
    )


def test_add_imu_edge_matches_legacy_right_end_endpoint():
    config = RootFilterConfig(covariance_floor=1e-15)
    initial = state(covariance_scale=0.1)
    force = np.array([0.4, -0.3, 9.7])
    rotation = np.array([
        [0.0, -1.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ])
    expected, _ = propagate_inertial(
        initial, 0.1, force, rotation, config
    )
    filt = CausalDelayedRootFilter(initial, config, inertial=True)

    assert filt.add_imu(ImuSample(0.1, 0.1, force, rotation, 1))

    np.testing.assert_array_equal(filt.current_state.vector, expected.vector)
    np.testing.assert_array_equal(
        filt.current_state.covariance, expected.covariance
    )


def test_rejected_tail_observation_does_not_commit_ephemeral_edge():
    config = RootFilterConfig(fixed_lag_s=1.0)
    filt = CausalDelayedRootFilter(state(), config, inertial=True)
    force = np.array([0.5, 0.0, 9.80665])
    assert filt.add_imu(ImuSample(0.1, 0.1, force, np.eye(3), 1))
    before = [
        (
            snapshot.state.vector.tobytes(),
            snapshot.state.covariance.tobytes(),
            id(snapshot.incoming_edge),
        )
        for snapshot in filt._snapshots
    ]

    decision = filt.add_position(PositionObservation(
        0.15,
        0.2,
        np.zeros(3),
        np.eye(3) * 0.1,
        "T",
        (0, 1, 2, 3),
        quality_state="REJECT_TEST",
    ), processing_time_s=0.2)

    assert not decision.accepted and decision.reason == "REJECT_TEST"
    assert [
        (
            snapshot.state.vector.tobytes(),
            snapshot.state.covariance.tobytes(),
            id(snapshot.incoming_edge),
        )
        for snapshot in filt._snapshots
    ] == before


def test_incoming_edge_payload_is_deeply_immutable():
    filt = _variable_input_filter()
    force_source = np.array([0.2, -0.1, 9.7])
    rotation_source = np.eye(3)
    edge = filt._make_edge(
        start_time_s=0.2,
        end_time_s=0.3,
        force=force_source,
        rotation=rotation_source,
        input_owner="IMMUTABLE_ALIAS_FIXTURE",
    )
    force_before = edge.force_sensor_mps2.copy()
    rotation_before = edge.rotation_world_from_sensor.copy()
    endpoint_noise_before = edge.endpoint_noise_covariance.copy()
    full_noise_before = edge.full_edge_process_noise_covariance.copy()

    force_source[:] = 99.0
    rotation_source[:] = 99.0

    for payload in (
        edge.force_sensor_mps2,
        edge.rotation_world_from_sensor,
        edge.endpoint_noise_covariance,
        edge.full_edge_process_noise_covariance,
    ):
        with pytest.raises(ValueError, match="read-only"):
            payload.flat[0] = 99.0

    np.testing.assert_array_equal(edge.force_sensor_mps2, force_before)
    np.testing.assert_array_equal(edge.rotation_world_from_sensor, rotation_before)
    np.testing.assert_array_equal(
        edge.endpoint_noise_covariance, endpoint_noise_before
    )
    np.testing.assert_array_equal(
        edge.full_edge_process_noise_covariance, full_noise_before
    )


def test_constraint_journal_exact_and_virtual_time_cursor_boundaries():
    filt = _journaled_constant_velocity_filter()
    exact = filt._state_at(0.2)
    virtual = filt._state_at(0.25)

    assert exact[2] == 1
    assert virtual[2] == 1
    exact_replay, _ = filt._replay_from(exact[0], exact[1], exact[2], 0.3)
    virtual_replay, _ = filt._replay_from(
        virtual[0], virtual[1], virtual[2], 0.3
    )
    np.testing.assert_allclose(exact_replay.position_m[0], 0.08, atol=1e-14)
    np.testing.assert_allclose(virtual_replay.position_m[0], 0.08, atol=1e-14)


def test_multiple_same_time_constraint_events_preserve_sequence_order():
    config = RootFilterConfig(
        maximum_position_influence_m=0.05,
        nis_limit_3d=1e12,
        fixed_lag_s=1.0,
        recovery_good_events=1,
    )
    filt = CausalDelayedRootFilter(state(), config, inertial=False)
    assert filt.add_imu(ImuSample(
        0.1, 0.1, np.zeros(3), np.eye(3), 1
    ))
    _add_x_constraint(filt, 0.03, owner="FIRST")
    _add_x_constraint(filt, -0.01, owner="SECOND")
    before = filt.current_state.vector.copy()
    decision = filt.add_position(observation(
        [0.0, 0.0, 0.0], measurement=0.0, available=0.1, covariance=0.1
    ), processing_time_s=0.1)

    assert decision.accepted
    np.testing.assert_array_equal(filt.current_state.vector, before)
    assert [event.owner for event in filt._constraint_events] == [
        "FIRST", "SECOND"
    ]
    assert filt._snapshots[-1].applied_constraint_cursor == 2


def test_pose_regauge_then_soft_contact_same_time_replays_in_call_order():
    config = RootFilterConfig(
        maximum_position_influence_m=0.05,
        nis_limit_3d=1e12,
        fixed_lag_s=1.0,
        recovery_good_events=1,
    )
    filt = CausalDelayedRootFilter(state(), config, inertial=False)
    assert filt.add_imu(ImuSample(
        0.1, 0.1, np.zeros(3), np.eye(3), 1
    ))
    pose_delta = np.zeros(9); pose_delta[0] = 0.1
    pose_operator = AdditiveRootConstraint(pose_delta)
    pose_state = pose_operator.apply(filt.current_state)
    filt.apply_current_constraint(
        pose_state, operator=pose_operator, owner="POSE_REGAUGE"
    )
    soft_operator = BoundedTargetRootConstraint(
        constrained_axes=(0, 1),
        position_target_m=np.zeros(3),
        velocity_target_mps=np.zeros(3),
        confidence=1.0,
        position_gain=1.0,
        velocity_gain=1.0,
        maximum_position_step_m=0.04,
        maximum_velocity_step_mps=0.12,
        ankle_z_offset_m=0.0,
        ankle_z_entry_m=0.0,
        ankle_z_lower_m=-1.0,
        ankle_z_upper_m=1.0,
    )
    soft_state = soft_operator.apply(filt.current_state)
    filt.apply_current_constraint(
        soft_state, operator=soft_operator, owner="SOFT_CONTACT"
    )
    assert filt.current_state.position_m[0] == pytest.approx(0.06)

    decision = filt.add_position(observation(
        [0.0, 0.0, 0.0], measurement=0.0, available=0.1, covariance=0.1
    ), processing_time_s=0.1)
    assert decision.accepted
    assert filt.current_state.position_m[0] == pytest.approx(0.06)
    assert [event.owner for event in filt._constraint_events] == [
        "POSE_REGAUGE", "SOFT_CONTACT"
    ]


def test_constraint_journal_replay_is_dense_sparse_partition_deterministic():
    config = RootFilterConfig(
        maximum_position_influence_m=0.05,
        nis_limit_3d=1e12,
        fixed_lag_s=2.0,
        recovery_good_events=1,
        cv_acceleration_noise_mps2_sqrt_hz=0.0,
        covariance_floor=1e-15,
    )

    def build(times):
        filt = CausalDelayedRootFilter(
            state(covariance_scale=1.0), config, inertial=False
        )
        for sequence, time_s in enumerate(times, start=1):
            assert filt.add_imu(ImuSample(
                time_s, time_s, np.zeros(3), np.eye(3), sequence
            ))
            if np.isclose(time_s, 0.2):
                delta = np.zeros(9)
                delta[0] = 0.03
                delta[3] = 0.02
                operator = AdditiveRootConstraint(delta)
                filt.apply_current_constraint(
                    operator.apply(filt.current_state),
                    operator=operator,
                    owner="POSE_REGAUGE",
                )
            if np.isclose(time_s, 0.6):
                operator = BoundedTargetRootConstraint(
                    constrained_axes=(0, 1),
                    position_target_m=np.zeros(3),
                    velocity_target_mps=np.zeros(3),
                    confidence=0.8,
                    position_gain=0.75,
                    velocity_gain=0.5,
                    maximum_position_step_m=0.04,
                    maximum_velocity_step_mps=0.12,
                    ankle_z_offset_m=-0.9,
                    ankle_z_entry_m=-0.9,
                    ankle_z_lower_m=-0.95,
                    ankle_z_upper_m=-0.47,
                )
                filt.apply_current_constraint(
                    operator.apply(filt.current_state),
                    operator=operator,
                    owner="SOFT_CONTACT",
                )
        return filt

    dense = build(np.arange(0.1, 1.0001, 0.1))
    sparse = build((0.1, 0.2, 0.4, 0.6, 0.8, 1.0))
    obs = observation(
        [0.3, 0.0, 0.0], measurement=0.1, available=1.0,
        covariance=0.02,
    )
    dense_decision = dense.add_position(obs, processing_time_s=1.0)
    sparse_decision = sparse.add_position(obs, processing_time_s=1.0)

    assert dense_decision.accepted and sparse_decision.accepted
    assert [event.sequence for event in dense._constraint_events] == [1, 2]
    assert [event.sequence for event in sparse._constraint_events] == [1, 2]
    np.testing.assert_allclose(
        dense.current_state.vector, sparse.current_state.vector,
        rtol=0.0, atol=2e-13,
    )
    np.testing.assert_allclose(
        dense.current_state.covariance, sparse.current_state.covariance,
        rtol=0.0, atol=2e-12,
    )
    np.testing.assert_allclose(
        dense_decision.availability_applied_position_delta_m,
        sparse_decision.availability_applied_position_delta_m,
        rtol=0.0, atol=2e-13,
    )


def test_constraint_journal_prunes_folded_events_without_reusing_ids():
    config = RootFilterConfig(fixed_lag_s=0.15)
    filt = CausalDelayedRootFilter(state(), config, inertial=False)
    for sequence, time_s in enumerate((0.1, 0.2), start=1):
        assert filt.add_imu(ImuSample(
            time_s, time_s, np.zeros(3), np.eye(3), sequence
        ))
        _add_x_constraint(filt, 0.01, owner=f"EVENT_{sequence}")
    assert filt.add_imu(ImuSample(
        0.3, 0.3, np.zeros(3), np.eye(3), 3
    ))

    assert [event.sequence for event in filt._constraint_events] == [2]
    _add_x_constraint(filt, 0.01, owner="EVENT_3")
    assert [event.sequence for event in filt._constraint_events] == [2, 3]
    assert filt._next_constraint_sequence == 4


def test_constraint_install_rejects_covariance_mutation_and_operator_mismatch():
    filt = CausalDelayedRootFilter(state())
    delta = np.zeros(9); delta[0] = 0.01
    operator = AdditiveRootConstraint(delta)
    vector = filt.current_state.vector + delta
    with pytest.raises(ValueError, match="covariance"):
        filt.apply_current_constraint(
            RootState(0.0, vector, filt.current_state.covariance * 0.9),
            operator=operator,
            owner="BAD_COVARIANCE",
        )
    wrong = vector.copy(); wrong[0] += 0.01
    with pytest.raises(ValueError, match="reproduce"):
        filt.apply_current_constraint(
            RootState(0.0, wrong, filt.current_state.covariance.copy()),
            operator=operator,
            owner="BAD_OPERATOR",
        )
    assert filt._constraint_events == []


def test_root_constraint_operator_payloads_are_deeply_immutable():
    additive_source = np.zeros(9)
    additive_source[0] = 0.04
    additive = AdditiveRootConstraint(additive_source)
    additive_source[0] = 99.0
    assert additive.vector_delta[0] == 0.04
    with pytest.raises(ValueError, match="read-only"):
        additive.vector_delta[0] = 1.0

    position_source = np.array([1.0, 2.0, 3.0])
    velocity_source = np.array([0.1, 0.2, 0.3])
    bounded = BoundedTargetRootConstraint(
        constrained_axes=(0, 1),
        position_target_m=position_source,
        velocity_target_mps=velocity_source,
        confidence=0.8,
        position_gain=0.75,
        velocity_gain=0.5,
        maximum_position_step_m=0.04,
        maximum_velocity_step_mps=0.12,
        ankle_z_offset_m=-0.9,
        ankle_z_entry_m=0.0,
        ankle_z_lower_m=-0.05,
        ankle_z_upper_m=0.43,
    )
    position_source[:] = 99.0
    velocity_source[:] = 99.0
    np.testing.assert_array_equal(
        bounded.position_target_m, np.array([1.0, 2.0, 3.0])
    )
    np.testing.assert_array_equal(
        bounded.velocity_target_mps, np.array([0.1, 0.2, 0.3])
    )
    with pytest.raises(ValueError, match="read-only"):
        bounded.position_target_m[0] = 1.5
    with pytest.raises(ValueError, match="read-only"):
        bounded.velocity_target_mps[0] = 1.5


def test_external_operator_mutation_cannot_change_journal_replay():
    config = RootFilterConfig(
        maximum_position_influence_m=0.05,
        nis_limit_3d=1e12,
        fixed_lag_s=1.0,
        recovery_good_events=1,
    )
    filt = CausalDelayedRootFilter(state(), config, inertial=False)
    assert filt.add_imu(ImuSample(
        0.1, 0.1, np.zeros(3), np.eye(3), 1
    ))
    source = np.zeros(9); source[0] = 0.04
    operator = AdditiveRootConstraint(source)
    filt.apply_current_constraint(
        operator.apply(filt.current_state),
        operator=operator,
        owner="IMMUTABLE_EVENT",
    )
    source[0] = 9.0
    with pytest.raises(ValueError, match="read-only"):
        operator.vector_delta[0] = 9.0

    assert filt.add_imu(ImuSample(
        0.2, 0.2, np.zeros(3), np.eye(3), 2
    ))
    decision = filt.add_position(observation(
        [0.0, 0.0, 0.0], measurement=0.0, available=0.2,
        covariance=0.1,
    ), processing_time_s=0.2)

    assert decision.accepted
    np.testing.assert_allclose(filt.current_state.position_m[0], 0.04)
    assert filt._constraint_events[0].operator.vector_delta[0] == 0.04
