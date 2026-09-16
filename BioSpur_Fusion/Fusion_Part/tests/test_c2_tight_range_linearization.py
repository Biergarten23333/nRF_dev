import dataclasses

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.tight_range import (
    ExternalRangeInformationWeights,
    PersistentRangeBiasTracker,
    RangeBiasPriorSnapshot,
    RawRangeDecision,
    RawRangeUpdateConfig,
    linearize_raw_range_factors,
    update_raw_ranges,
)
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow
from biospur_fusion.root_r3.models import RootState


ANCHORS = np.array([
    [0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0],
    [0, 0, 2], [4, 0, 2], [4, 3, 2], [0, 3, 2],
], dtype=float)
TRUTH = np.array([2.0, 1.2, 0.9])
CLOCK = ClockModel(0, 1000.0, 0.0, 0.0)


def _row(delta=None, tround=None):
    ranges = np.linalg.norm(ANCHORS - TRUTH, axis=1)
    if delta is not None:
        ranges += np.asarray(delta, float)
    return UwbRow(
        "BSFC2CC", 0, 1, 1, 1_000_000, 1_010_000, tuple(range(8)),
        tuple(int(round(x * 1000)) for x in ranges),
        tuple(tround or (1000 + 500 * i for i in range(8))),
        (100,) * 8, 0xFF,
    )


def _state(row=None):
    row = _row() if row is None else row
    epochs = [CLOCK.seconds(row.strobe_us + 0.5 * x) for x in row.t_round_us]
    vector = np.zeros(9); vector[:3] = TRUTH; vector[3:6] = [0.2, -0.1, 0.05]
    return RootState(float(np.median(epochs)), vector, np.eye(9) * 0.04)


def _prior(row, *, variance=0.01):
    first = min(CLOCK.seconds(row.strobe_us + 0.5 * x) for x in row.t_round_us)
    return RangeBiasPriorSnapshot(
        row.node, first - 0.01, np.zeros(8), np.full(8, variance),
        np.full(8, np.nan),
    )


def test_exact_timing_h_r_s_and_nis_are_manual_and_readonly():
    row = _row()
    state = _state(row)
    prior = _prior(row)
    weight = ExternalRangeInformationWeights(row.node, prior.snapshot_time_s,
                                              np.linspace(0.2, 0.9, 8), "FIXTURE")
    out = linearize_raw_range_factors(
        state, row, anchors_m=ANCHORS, clock=CLOCK, bias_prior=prior,
        information_weights=weight,
    )
    expected_epochs = np.array([
        CLOCK.seconds(row.strobe_us + 0.5 * x) for x in row.t_round_us
    ])
    np.testing.assert_array_equal(out.link_epochs_s, expected_epochs)
    dt = expected_epochs - np.median(expected_epochs)
    position = state.position_m + dt[:, None] * state.velocity_mps
    unit = (position - ANCHORS) / np.linalg.norm(position - ANCHORS, axis=1)[:, None]
    np.testing.assert_allclose(out.state_jacobian[:, :3], unit, atol=1e-15)
    np.testing.assert_allclose(out.state_jacobian[:, 3:6], dt[:, None] * unit, atol=1e-15)
    expected_r = np.diag((0.12**2 + 0.01) / weight.weights)
    np.testing.assert_allclose(out.r_prior_m2, expected_r)
    expected_s = out.state_jacobian @ state.covariance @ out.state_jacobian.T + expected_r
    np.testing.assert_allclose(out.s_prior_m2, expected_s)
    assert out.prior_nis == pytest.approx(out.innovations_m @ np.linalg.solve(expected_s, out.innovations_m))
    with pytest.raises(ValueError):
        out.state_jacobian[0, 0] = 0


def test_state_jacobian_matches_finite_difference_without_double_motion_correction():
    row = _row()
    state = _state(row)
    base = linearize_raw_range_factors(state, row, anchors_m=ANCHORS, clock=CLOCK)
    eps = 1e-6
    for column in range(6):
        vector = state.vector.copy(); vector[column] += eps
        changed = linearize_raw_range_factors(
            RootState(state.time_s, vector, state.covariance), row,
            anchors_m=ANCHORS, clock=CLOCK)
        derivative = (changed.predicted_ranges_m - base.predicted_ranges_m) / eps
        np.testing.assert_allclose(derivative, base.state_jacobian[:, column], atol=2e-6)


def test_augmented_covariance_includes_bias_cross_terms():
    row = _row(); state = _state(row); prior = _prior(row)
    count = 8
    full = np.zeros((9 + count, 9 + count))
    full[:9, :9] = state.covariance
    full[9:, 9:] = np.eye(count) * 0.01
    full[:3, 9:] = 0.001
    full[9:, :3] = 0.001
    out = linearize_raw_range_factors(
        state, row, anchors_m=ANCHORS, clock=CLOCK, bias_prior=prior,
        augmented_covariance=full)
    expected = out.augmented_jacobian @ full @ out.augmented_jacobian.T + out.sensor_r_m2
    np.testing.assert_allclose(out.s_augmented_m2, expected)
    assert out.cross_covariance_status == "SUPPLIED_FULL_AUGMENTED_COVARIANCE"


def test_positive_tail_is_more_attenuated_than_equal_negative_tail():
    config = RawRangeUpdateConfig(positive_nlos_cauchy_scale_m=0.12)
    values = []
    for sign in (1, -1):
        delta = np.zeros(8); delta[3] = sign * 0.6
        row = _row(delta)
        out = linearize_raw_range_factors(
            _state(row), row, anchors_m=ANCHORS, clock=CLOCK, config=config)
        values.append(out.robust_weights[3])
    assert values[0] < values[1]


def test_bias_prior_is_next_epoch_only_and_rejected_update_is_byte_stable():
    tracker = PersistentRangeBiasTracker()
    row = _row(); epoch = _state(row).time_s
    before = tracker.prior_snapshot(row.node, snapshot_time_s=epoch - 0.01)
    rejected = RawRangeDecision(
        False, "REJECT", tuple(range(8)), np.full(8, epoch - 0.02), epoch - 0.02,
        np.ones(8), np.ones(8), np.ones(8), np.full(8, 1.0 / 0.12), np.ones(8),
        np.full(8, 0.12), 3, 2.0, 1, "FIXTURE")
    tracker.update(row.node, rejected)
    after = tracker.prior_snapshot(row.node, snapshot_time_s=epoch - 0.01)
    np.testing.assert_array_equal(before.mean_m, after.mean_m)
    np.testing.assert_array_equal(before.variance_m2, after.variance_m2)
    accepted = dataclasses.replace(rejected, accepted=True, reason="ACCEPTED")
    tracker.update(row.node, accepted)
    later = tracker.prior_snapshot(row.node, snapshot_time_s=epoch + 0.01)
    assert np.all(later.mean_m > 0)
    with pytest.raises(ValueError):
        tracker.prior_snapshot(row.node, snapshot_time_s=epoch - 0.03)


def test_rejected_unseen_node_does_not_allocate_and_existing_is_byte_stable():
    tracker = PersistentRangeBiasTracker()
    row = _row(); epoch = _state(row).time_s
    rejected = RawRangeDecision(
        False, "REJECT", tuple(range(8)), np.full(8, epoch), epoch,
        np.ones(8), np.ones(8), np.zeros(8), np.zeros(8), np.ones(8),
        np.full(8, 0.12), 3, 2.0, 1, "FIXTURE")
    before = tracker.snapshot()
    returned = tracker.update("UNSEEN", rejected)
    np.testing.assert_array_equal(returned, np.zeros(8))
    assert tracker.snapshot() == before == {}
    tracker.prior_snapshot("EXISTING", snapshot_time_s=epoch - 1.0)
    before = tracker.prior_snapshot("EXISTING", snapshot_time_s=epoch - 0.5)
    inventory = tracker.snapshot()
    returned = tracker.update("EXISTING", rejected)
    after = tracker.prior_snapshot("EXISTING", snapshot_time_s=epoch - 0.5)
    np.testing.assert_array_equal(returned, before.mean_m)
    np.testing.assert_array_equal(after.mean_m, before.mean_m)
    np.testing.assert_array_equal(after.variance_m2, before.variance_m2)
    np.testing.assert_array_equal(after.last_accepted_time_s, before.last_accepted_time_s)
    assert tracker.snapshot() == inventory


def test_weight_identity_time_domain_and_rank_fail_closed_without_deletion():
    row = _row(); state = _state(row)
    first = min(CLOCK.seconds(row.strobe_us + 0.5 * x) for x in row.t_round_us)
    with pytest.raises(ValueError):
        ExternalRangeInformationWeights(row.node, first - 1, np.zeros(8), "BAD")
    with pytest.raises(ValueError):
        ExternalRangeInformationWeights(row.node, first - 1, np.full(8, 1.01), "BAD")
    future = ExternalRangeInformationWeights(row.node, first, np.ones(8), "FUTURE")
    with pytest.raises(ValueError):
        linearize_raw_range_factors(state, row, anchors_m=ANCHORS, clock=CLOCK,
                                    information_weights=future)
    flat = np.zeros((8, 3)); flat[:, 0] = np.arange(8)
    with pytest.raises(ValueError, match="rank/condition"):
        linearize_raw_range_factors(state, row, anchors_m=flat, clock=CLOCK)
    good = linearize_raw_range_factors(state, row, anchors_m=ANCHORS, clock=CLOCK)
    assert good.anchors == tuple(range(8))
    assert np.all(good.irls_information_weights > 0)


def test_unit_weights_zero_bias_prior_preserve_legacy_factor_values():
    row = _row(); state = _state(row)
    out = linearize_raw_range_factors(state, row, anchors_m=ANCHORS, clock=CLOCK)
    np.testing.assert_array_equal(out.information_weights, np.ones(8))
    np.testing.assert_array_equal(out.r_prior_m2, out.sensor_r_m2)
    np.testing.assert_array_equal(out.state_jacobian[:, 6:], np.zeros((8, 3)))
    assert out.cross_covariance_status == "ZERO_BIAS_PRIOR_LEGACY_EQUIVALENCE"


def test_update_keeps_legacy_geometry_reject_policy_and_reason():
    row = _row(); state = _state(row)
    flat = np.zeros((8, 3)); flat[:, 0] = np.arange(8)
    unchanged, decision = update_raw_ranges(
        state, row, anchors_m=flat, clock=CLOCK)
    assert not decision.accepted
    assert decision.reason == "SOLVER_OR_GEOMETRY_REJECT"
    np.testing.assert_array_equal(unchanged.vector, state.vector)


def test_update_consumes_same_factor_weights_and_preserves_exact_legacy_boundary():
    delta = np.zeros(8); delta[3] = 0.6
    row = _row(delta); state = _state(row)
    legacy_state, legacy_decision = update_raw_ranges(
        state, row, anchors_m=ANCHORS, clock=CLOCK)
    first = min(CLOCK.seconds(row.strobe_us + 0.5 * x) for x in row.t_round_us)
    zero = RangeBiasPriorSnapshot(
        row.node, first - 0.01, np.zeros(8), np.zeros(8), np.full(8, np.nan))
    unit = ExternalRangeInformationWeights(
        row.node, first - 0.01, np.ones(8), "FIXTURE")
    parity_state, parity_decision = update_raw_ranges(
        state, row, anchors_m=ANCHORS, clock=CLOCK,
        bias_prior=zero, information_weights=unit)
    np.testing.assert_array_equal(parity_state.vector, legacy_state.vector)
    np.testing.assert_array_equal(parity_state.covariance, legacy_state.covariance)
    np.testing.assert_array_equal(parity_decision.innovations_m, legacy_decision.innovations_m)
    down = np.ones(8); down[3] = 0.1
    weighted = ExternalRangeInformationWeights(
        row.node, first - 0.01, down, "FIXTURE")
    weighted_state, weighted_decision = update_raw_ranges(
        state, row, anchors_m=ANCHORS, clock=CLOCK,
        bias_prior=zero, information_weights=weighted)
    assert weighted_decision.sigma_m[3] > legacy_decision.sigma_m[3]
    assert not np.array_equal(weighted_state.vector, legacy_state.vector)


def test_total_prior_sigma_owns_robust_standardization():
    delta = np.zeros(8); delta[3] = 0.6
    row = _row(delta); state = _state(row); prior = _prior(row, variance=0.04)
    out = linearize_raw_range_factors(
        state, row, anchors_m=ANCHORS, clock=CLOCK, bias_prior=prior,
        config=RawRangeUpdateConfig(positive_nlos_cauchy_scale_m=0.12))
    total_sigma = np.sqrt(np.diag(out.r_prior_m2))
    magnitude = abs(out.innovations_m[3] / total_sigma[3])
    huber = 1.0 if magnitude <= 2.5 else 2.5 / magnitude
    cauchy = 1.0 / (1.0 + (out.innovations_m[3] / 0.12) ** 2)
    assert out.robust_weights[3] == pytest.approx(huber * cauchy)

    updated, decision = update_raw_ranges(
        state, row, anchors_m=ANCHORS, clock=CLOCK, bias_prior=prior,
        config=RawRangeUpdateConfig(positive_nlos_cauchy_scale_m=0.12))
    assert updated.time_s == state.time_s
    np.testing.assert_array_equal(
        decision.standardized_innovations,
        decision.innovations_m / decision.sigma_m)
    assert np.all(decision.sigma_m > decision.sensor_sigma_m)
    for value in (
        decision.link_epochs_s, decision.measured_ranges_m,
        decision.predicted_ranges_m, decision.innovations_m,
        decision.standardized_innovations, decision.robust_weights,
        decision.sigma_m, decision.sensor_sigma_m,
    ):
        assert not value.flags.writeable


def test_augmented_covariance_must_be_psd():
    row = _row(); state = _state(row)
    full = np.eye(17); full[:9, :9] = state.covariance; full[-1, -1] = -1
    with pytest.raises(ValueError, match="positive semidefinite"):
        linearize_raw_range_factors(
            state, row, anchors_m=ANCHORS, clock=CLOCK,
            bias_prior=_prior(row, variance=1.0), augmented_covariance=full)
