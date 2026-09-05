import numpy as np

from biospur_fusion.c2_uwb_root_world.tight_range import (
    PersistentRangeBiasTracker,
    RawRangeUpdateConfig,
    UWB_SWEEP_PERIOD_US,
    UWB_SWEEP_RATE_HZ,
    update_raw_ranges,
)
from biospur_fusion.c2_uwb_root_world.split_fusion import (
    FixedLagDriftConfig,
    FixedLagRangeDriftCorrector,
    SingleFootContactConfig,
    SingleFootVelocityCorrector,
)
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow
from biospur_fusion.root_r3.models import RootState


ANCHORS = np.array([
    [0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0],
    [0, 0, 2], [4, 0, 2], [4, 3, 2], [0, 3, 2],
], dtype=float)


def _row(position, *, ranges_delta_m=None, valid_mask=0xFF, t_round_us=None):
    ranges = np.linalg.norm(ANCHORS - np.asarray(position), axis=1)
    if ranges_delta_m is not None:
        ranges = ranges + np.asarray(ranges_delta_m)
    return UwbRow(
        "BSFC2CC", 0, 1, 1, 1_000_000, 1_010_000, tuple(range(8)),
        tuple(int(round(value * 1000.0)) for value in ranges),
        tuple(t_round_us or (2_000,) * 8), (100,) * 8, valid_mask,
    )


def _state(time_s, position):
    vector = np.zeros(9); vector[:3] = position
    return RootState(time_s, vector, np.diag([0.5] * 3 + [0.2] * 3 + [0.1] * 3))


def test_cadence_is_exactly_120_ms_not_10_hz():
    assert UWB_SWEEP_PERIOD_US == 120_000
    assert UWB_SWEEP_RATE_HZ == 25.0 / 3.0
    assert UWB_SWEEP_RATE_HZ != 10.0


def test_joint_raw_range_update_recovers_position_without_position_observation():
    truth = np.array([2.0, 1.2, 0.9])
    row = _row(truth)
    clock = ClockModel(0, 1000.0, 0.0, 0.0)
    reference_s = np.median([
        clock.seconds(row.strobe_us + 0.5 * value) for value in row.t_round_us
    ])
    prior = _state(reference_s, [1.6, 1.5, 0.6])
    updated, decision = update_raw_ranges(prior, row, anchors_m=ANCHORS, clock=clock)
    assert decision.accepted
    assert decision.anchors == tuple(range(8))
    assert np.linalg.norm(updated.position_m - truth) < np.linalg.norm(prior.position_m - truth)
    assert decision.rank == 3


def test_each_link_uses_its_measured_round_trip_epoch():
    truth = np.array([2.0, 1.2, 0.9])
    tround = tuple(1_000 + 800 * index for index in range(8))
    row = _row(truth, t_round_us=tround)
    clock = ClockModel(0, 1000.0, 0.0, 0.0)
    expected = np.asarray([clock.seconds(row.strobe_us + 0.5 * value) for value in tround])
    prior = _state(float(np.median(expected)), truth)
    _, decision = update_raw_ranges(prior, row, anchors_m=ANCHORS, clock=clock)
    assert decision.accepted
    np.testing.assert_allclose(decision.link_epochs_s, expected, atol=0.0, rtol=0.0)
    assert np.ptp(decision.link_epochs_s) > 0.0


def test_one_large_positive_nlos_range_is_downweighted_not_whole_sweep_rejected():
    truth = np.array([2.0, 1.2, 0.9])
    delta = np.zeros(8); delta[3] = 1.5
    row = _row(truth, ranges_delta_m=delta)
    clock = ClockModel(0, 1000.0, 0.0, 0.0)
    reference_s = clock.seconds(row.strobe_us + 1_000)
    prior = _state(reference_s, [1.9, 1.25, 0.85])
    updated, decision = update_raw_ranges(prior, row, anchors_m=ANCHORS, clock=clock)
    assert decision.accepted
    assert decision.robust_weights[3] < 0.5
    assert all(decision.robust_weights[index] > decision.robust_weights[3] for index in range(8) if index != 3)
    assert np.linalg.norm(updated.position_m - truth) < 0.25


def test_enabled_one_sided_nlos_tail_downweights_positive_more_than_negative():
    truth = np.array([2.0, 1.2, 0.9])
    clock = ClockModel(0, 1000.0, 0.0, 0.0)
    config = RawRangeUpdateConfig(positive_nlos_cauchy_scale_m=0.12)
    weights = []
    for sign in (1.0, -1.0):
        delta = np.zeros(8); delta[3] = sign * 0.6
        row = _row(truth, ranges_delta_m=delta)
        prior = _state(clock.seconds(row.strobe_us + 1_000), truth)
        _, decision = update_raw_ranges(
            prior, row, anchors_m=ANCHORS, clock=clock, config=config,
        )
        assert decision.accepted
        weights.append(decision.robust_weights[3])
    assert weights[0] < weights[1]


def test_persistent_bias_tracker_learns_positive_link_and_stays_nonnegative():
    truth = np.array([2.0, 1.2, 0.9])
    delta = np.zeros(8); delta[3] = 0.6
    row = _row(truth, ranges_delta_m=delta)
    clock = ClockModel(0, 1000.0, 0.0, 0.0)
    prior = _state(clock.seconds(row.strobe_us + 1_000), truth)
    _, decision = update_raw_ranges(
        prior, row, anchors_m=ANCHORS, clock=clock,
        config=RawRangeUpdateConfig(positive_nlos_cauchy_scale_m=0.12),
    )
    tracker = PersistentRangeBiasTracker()
    first = tracker.update(row.node, decision)
    assert first[3] > 0.0
    assert np.all(first >= 0.0)
    assert tracker.bias_vector(row.node)[3] == first[3]


def test_seven_links_are_consumed_and_bad_identity_fails_closed():
    truth = np.array([2.0, 1.2, 0.9])
    row = _row(truth, valid_mask=0x7F)
    clock = ClockModel(0, 1000.0, 0.0, 0.0)
    prior = _state(clock.seconds(row.strobe_us + 1_000), truth)
    _, decision = update_raw_ranges(prior, row, anchors_m=ANCHORS, clock=clock)
    assert decision.accepted
    assert decision.anchors == tuple(range(7))

    broken = UwbRow(**{**row.__dict__, "anchor_ids": (1, 0, 2, 3, 4, 5, 6, 7)})
    unchanged, rejected = update_raw_ranges(prior, broken, anchors_m=ANCHORS, clock=clock)
    assert not rejected.accepted
    assert rejected.reason == "ANCHOR_IDENTITY_MISMATCH"
    np.testing.assert_array_equal(unchanged.vector, prior.vector)


def test_state_must_be_propagated_to_sweep_reference_epoch():
    truth = np.array([2.0, 1.2, 0.9])
    row = _row(truth)
    clock = ClockModel(0, 1000.0, 0.0, 0.0)
    prior = _state(0.0, truth)
    unchanged, decision = update_raw_ranges(prior, row, anchors_m=ANCHORS, clock=clock)
    assert not decision.accepted
    assert decision.reason == "STATE_NOT_AT_SWEEP_REFERENCE_EPOCH"
    np.testing.assert_array_equal(unchanged.vector, prior.vector)


def test_body_tag_offset_constrains_shared_root_without_cartesian_tag_position():
    root_truth = np.array([2.0, 1.2, 0.9])
    tag_offset = np.array([0.25, -0.10, 0.35])
    row = _row(root_truth + tag_offset)
    clock = ClockModel(0, 1000.0, 0.0, 0.0)
    reference_s = clock.seconds(row.strobe_us + 1_000)
    prior = _state(reference_s, root_truth + np.array([-0.3, 0.2, -0.2]))
    updated, decision = update_raw_ranges(
        prior, row, anchors_m=ANCHORS, clock=clock,
        tag_offset_world_m=tag_offset,
    )
    assert decision.accepted
    assert np.linalg.norm(updated.position_m - root_truth) < np.linalg.norm(
        prior.position_m - root_truth
    )


def test_absolute_range_channel_changes_position_but_not_drift_states():
    truth = np.array([2.0, 1.2, 0.9])
    row = _row(truth)
    clock = ClockModel(0, 1000.0, 0.0, 0.0)
    reference_s = clock.seconds(row.strobe_us + 1_000)
    prior = _state(reference_s, truth + np.array([-0.4, 0.3, -0.2]))
    prior.vector[3:6] = [0.7, -0.2, 0.1]
    prior.vector[6:9] = [0.03, -0.02, 0.01]
    updated, decision = update_raw_ranges(
        prior,
        row,
        anchors_m=ANCHORS,
        clock=clock,
        state_update_indices=(0, 1, 2),
        correction_gain=0.1,
        maximum_position_step_m=0.02,
    )
    assert decision.accepted
    np.testing.assert_array_equal(updated.vector[3:], prior.vector[3:])
    assert 0.0 < np.linalg.norm(updated.position_m - prior.position_m) <= 0.0200001


def _decision(epoch_s, innovations):
    from biospur_fusion.c2_uwb_root_world.tight_range import RawRangeDecision

    return RawRangeDecision(
        True,
        "ACCEPTED",
        tuple(range(8)),
        np.full(8, epoch_s),
        epoch_s,
        np.ones(8),
        np.ones(8),
        np.asarray(innovations, float),
        np.asarray(innovations, float) / 0.12,
        np.ones(8),
        np.full(8, 0.12),
        3,
        2.0,
        1,
        "SYNTHETIC_TEST",
    )


def test_fixed_lag_channel_updates_velocity_without_position_jump():
    position = np.array([2.0, 1.2, 0.9])
    velocity_error = np.array([0.20, -0.10, 0.05])
    config = FixedLagDriftConfig(
        minimum_lag_s=0.30,
        maximum_lag_s=0.75,
        update_period_s=0.48,
        minimum_rows=8,
    )
    corrector = FixedLagRangeDriftCorrector(config)
    state = _state(0.0, position)
    offset = np.zeros(3)
    for epoch in (0.0, 0.36, 0.72):
        state = RootState(epoch, state.vector.copy(), state.covariance.copy())
        unit = (position - ANCHORS) / np.linalg.norm(position - ANCHORS, axis=1)[:, None]
        innovations = unit @ (epoch * velocity_error)
        prior_position = state.position_m.copy()
        state, decision = corrector.observe(
            state,
            node="BSFC2CC",
            decision=_decision(epoch, innovations),
            anchors_m=ANCHORS,
            tag_offset_world_m=offset,
            tag_offset_velocity_world_mps=offset,
            rotation_world_from_sensor=np.eye(3),
        )
    assert decision.accepted
    np.testing.assert_array_equal(state.position_m, prior_position)
    assert np.dot(decision.velocity_delta_mps, velocity_error) > 0.0
    np.testing.assert_array_equal(decision.accelerometer_bias_delta_mps2, np.zeros(3))
    np.testing.assert_array_equal(state.accelerometer_bias_mps2, np.zeros(3))
    assert decision.rank >= 3


def test_fixed_lag_ledger_does_not_reinterpret_absolute_steps_as_drift():
    position = np.array([2.0, 1.2, 0.9])
    corrector = FixedLagRangeDriftCorrector(FixedLagDriftConfig(
        minimum_lag_s=0.30,
        maximum_lag_s=0.75,
        update_period_s=0.48,
        minimum_rows=8,
    ))
    state = _state(0.0, position)
    cumulative_rate = np.array([0.01, -0.005, 0.002])
    for epoch in (0.0, 0.36, 0.72):
        cumulative = epoch * cumulative_rate
        current_position = position + cumulative
        state = RootState(epoch, np.r_[current_position, np.zeros(6)], state.covariance.copy())
        unit = (current_position - ANCHORS) / np.linalg.norm(
            current_position - ANCHORS, axis=1
        )[:, None]
        innovations = -(unit @ cumulative)
        state, decision = corrector.observe(
            state,
            node="BSFC2CC",
            decision=_decision(epoch, innovations),
            anchors_m=ANCHORS,
            tag_offset_world_m=np.zeros(3),
            tag_offset_velocity_world_mps=np.zeros(3),
            rotation_world_from_sensor=np.eye(3),
            cumulative_absolute_position_correction_m=cumulative,
        )
    assert decision.accepted
    assert np.linalg.norm(decision.velocity_delta_mps) < 2e-4


def test_contact_channel_is_switchable_and_never_updates_position():
    state = _state(1.0, [2.0, 1.2, 0.9])
    state.vector[3:6] = [0.3, -0.1, 0.0]
    disabled = SingleFootVelocityCorrector()
    unchanged, decision = disabled.update(
        state,
        ankle_offset_world_m={"left": np.array([0.0, 0.0, -0.9]), "right": np.array([0.0, 0.0, -0.8])},
        ankle_offset_velocity_world_mps={"left": np.zeros(3), "right": np.zeros(3)},
    )
    assert not decision.accepted
    np.testing.assert_array_equal(unchanged.vector, state.vector)

    enabled = SingleFootVelocityCorrector(SingleFootContactConfig(enabled=True))
    updated, decision = enabled.update(
        state,
        ankle_offset_world_m={"left": np.array([0.0, 0.0, -0.9]), "right": np.array([0.0, 0.0, -0.8])},
        ankle_offset_velocity_world_mps={"left": np.zeros(3), "right": np.zeros(3)},
    )
    assert decision.accepted
    assert decision.side == "left"
    np.testing.assert_array_equal(updated.position_m, state.position_m)
    assert np.linalg.norm(updated.velocity_mps) < np.linalg.norm(state.velocity_mps)
