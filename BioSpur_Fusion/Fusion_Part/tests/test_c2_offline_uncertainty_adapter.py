import dataclasses

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.offline_uncertainty_adapter import (
    SUPPLIED,
    UNAVAILABLE,
    adapt_raw_range_uncertainty,
)
from biospur_fusion.c2_uwb_root_world.tight_range import (
    ExternalRangeInformationWeights,
    PersistentRangeBiasTracker,
    RangeBiasPriorSnapshot,
    RawRangeDecision,
    linearize_raw_range_factors,
)
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow
from biospur_fusion.root_r3.models import RootState


ANCHORS = np.array([
    [0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0],
    [0, 0, 2], [4, 0, 2], [4, 3, 2], [0, 3, 2],
], dtype=float)
TRUTH = np.array([2.0, 1.2, 0.9])
CLOCK = ClockModel(0, 1000.0, 0.0, 0.0)


def _fixture(*, bias_variance=0.01, weighted=True, augmented=False):
    ranges = np.linalg.norm(ANCHORS - TRUTH, axis=1)
    ranges[2] += 0.35
    row = UwbRow(
        "BSFC2CC", 0, 1, 1, 1_000_000, 1_010_000, tuple(range(8)),
        tuple(int(round(value * 1000)) for value in ranges),
        tuple(1000 + 500 * index for index in range(8)), (100,) * 8, 0xFF,
    )
    epochs = np.array([
        CLOCK.seconds(row.strobe_us + 0.5 * value) for value in row.t_round_us
    ])
    vector = np.zeros(9); vector[:3] = TRUTH; vector[3:6] = [0.2, -0.1, 0.05]
    state = RootState(float(np.median(epochs)), vector, np.eye(9) * 0.04)
    prior = RangeBiasPriorSnapshot(
        row.node, float(epochs.min() - 0.01), np.zeros(8),
        np.full(8, bias_variance), np.full(8, np.nan),
    )
    weights = ExternalRangeInformationWeights(
        row.node, prior.snapshot_time_s,
        np.linspace(0.3, 1.0, 8) if weighted else np.ones(8), "FIXTURE",
    )
    full = None
    if augmented:
        full = np.zeros((17, 17))
        full[:9, :9] = state.covariance
        full[9:, 9:] = np.eye(8) * bias_variance
        full[:3, 9:] = 0.001
        full[9:, :3] = 0.001
    factor = linearize_raw_range_factors(
        state, row, anchors_m=ANCHORS, clock=CLOCK, bias_prior=prior,
        information_weights=weights, augmented_covariance=full,
    )
    return state, factor, full


def test_recomputes_full_uncertainty_algebra_without_joint_covariance_claim():
    state, factor, _ = _fixture()
    audit = adapt_raw_range_uncertainty(factor, state)
    np.testing.assert_array_equal(audit.state_jacobian, factor.state_jacobian)
    np.testing.assert_array_equal(audit.sensor_r_m2, factor.sensor_r_m2)
    np.testing.assert_array_equal(audit.bias_prior_total_r_m2, factor.r_prior_m2)
    np.testing.assert_allclose(
        audit.robust_effective_r_m2,
        np.diag(np.diag(factor.r_prior_m2) / factor.robust_weights),
    )
    assert audit.prior_nis == pytest.approx(
        factor.innovations_m @ np.linalg.solve(factor.s_prior_m2, factor.innovations_m))
    assert audit.cross_covariance_status == UNAVAILABLE
    assert audit.root_bias_cross_covariance_m2 is None
    assert audit.deleted_link_count == 0
    assert audit.structurally_valid_link_count == len(factor.anchors)
    assert not audit.calibrated_R and not audit.scientific_pass and not audit.production_ready
    for value in (
        audit.innovation_m, audit.state_jacobian, audit.sensor_r_m2,
        audit.bias_prior_total_r_m2, audit.robust_effective_r_m2,
        audit.prior_s_m2, audit.robust_effective_s_m2,
    ):
        assert not value.flags.writeable


def test_sensor_and_bias_prior_uncertainty_are_separate_and_not_double_counted():
    state, factor, _ = _fixture(bias_variance=0.04)
    audit = adapt_raw_range_uncertainty(factor, state)
    sensor = np.square(factor.quality_sigma_m) / factor.information_weights
    bias = factor.bias_variance_m2 / factor.information_weights
    np.testing.assert_allclose(np.diag(audit.sensor_r_m2), sensor)
    np.testing.assert_allclose(np.diag(audit.bias_prior_total_r_m2), sensor + bias)
    np.testing.assert_allclose(
        np.diag(audit.robust_effective_r_m2),
        (sensor + bias) / factor.robust_weights,
    )


def test_joint_claim_fails_closed_when_cross_covariance_was_not_propagated():
    state, factor, _ = _fixture()
    with pytest.raises(ValueError, match="UNAVAILABLE_NOT_PROPAGATED"):
        adapt_raw_range_uncertainty(
            factor, state, require_joint_root_bias_covariance=True)


def test_explicit_augmented_covariance_exposes_exact_cross_block_and_nis():
    state, factor, full = _fixture(augmented=True)
    audit = adapt_raw_range_uncertainty(
        factor, state, augmented_covariance=full,
        require_joint_root_bias_covariance=True,
    )
    assert audit.cross_covariance_status == SUPPLIED
    np.testing.assert_array_equal(audit.root_bias_cross_covariance_m2, full[:9, 9:])
    np.testing.assert_allclose(audit.augmented_s_m2, factor.s_augmented_m2)
    assert audit.augmented_nis == pytest.approx(factor.augmented_nis)


def test_zero_bias_unit_weight_is_exact_legacy_covariance_parity():
    state, factor, _ = _fixture(bias_variance=0.0, weighted=False)
    audit = adapt_raw_range_uncertainty(factor, state)
    np.testing.assert_array_equal(audit.sensor_r_m2, audit.bias_prior_total_r_m2)
    np.testing.assert_array_equal(audit.prior_s_m2, factor.s_prior_m2)
    assert audit.prior_nis == factor.prior_nis


@pytest.mark.parametrize("field", ["s_prior_m2", "prior_nis", "rank", "condition"])
def test_factor_tamper_is_rejected(field):
    state, factor, _ = _fixture()
    replacements = {
        "s_prior_m2": factor.s_prior_m2 + np.eye(8) * 1e-3,
        "prior_nis": factor.prior_nis + 1.0,
        "rank": 2,
        "condition": factor.condition + 1.0,
    }
    with pytest.raises(ValueError):
        adapt_raw_range_uncertainty(dataclasses.replace(factor, **{field: replacements[field]}), state)


def test_asymmetric_non_psd_singular_shape_and_robust_weight_tamper_reject():
    state, factor, _ = _fixture()
    asymmetric = factor.sensor_r_m2.copy(); asymmetric[0, 1] = 1e-3
    with pytest.raises(ValueError, match="asymmetric"):
        adapt_raw_range_uncertainty(dataclasses.replace(factor, sensor_r_m2=asymmetric), state)
    non_psd = factor.r_prior_m2.copy(); non_psd[0, 0] = -1.0
    with pytest.raises(ValueError):
        adapt_raw_range_uncertainty(dataclasses.replace(factor, r_prior_m2=non_psd), state)
    singular = factor.state_jacobian.copy(); singular[:, :3] = 0.0
    with pytest.raises(ValueError):
        adapt_raw_range_uncertainty(dataclasses.replace(factor, state_jacobian=singular), state)
    with pytest.raises(ValueError):
        dataclasses.replace(factor, state_jacobian=np.zeros((7, 9)))
    bad_robust = factor.robust_weights.copy(); bad_robust[0] = 0.0
    with pytest.raises(ValueError, match="robust weights"):
        adapt_raw_range_uncertainty(dataclasses.replace(factor, robust_weights=bad_robust), state)


@pytest.mark.parametrize("mutation", ["asymmetric", "non_psd", "root", "bias"])
def test_augmented_covariance_pathologies_fail_closed(mutation):
    state, factor, full = _fixture(augmented=True)
    changed = full.copy()
    if mutation == "asymmetric": changed[0, 9] += 0.01
    elif mutation == "non_psd": changed[16, 16] = -1.0
    elif mutation == "root": changed[0, 0] += 0.01
    else: changed[9, 9] += 0.01
    with pytest.raises(ValueError):
        adapt_raw_range_uncertainty(factor, state, augmented_covariance=changed)


def test_bias_tracker_accepts_only_completed_decisions_and_rejection_is_stable():
    tracker = PersistentRangeBiasTracker()
    state, factor, _ = _fixture()
    epoch = factor.reference_epoch_s
    rejected = RawRangeDecision(
        False, "REJECT", factor.anchors, factor.link_epochs_s, epoch,
        factor.measured_ranges_m, factor.predicted_ranges_m, factor.innovations_m,
        factor.innovations_m / np.sqrt(np.diag(factor.r_prior_m2)),
        factor.robust_weights, np.sqrt(np.diag(factor.r_prior_m2)),
        factor.rank, factor.condition, 1, "FIXTURE",
        sensor_sigma_m=np.sqrt(np.diag(factor.sensor_r_m2)),
    )
    before = tracker.snapshot()
    tracker.update("NEW", rejected)
    assert tracker.snapshot() == before == {}
    accepted = dataclasses.replace(rejected, accepted=True, reason="ACCEPTED")
    tracker.update("NEW", accepted)
    assert "NEW" in tracker.snapshot()
    query = np.nextafter(float(np.max(factor.link_epochs_s)), np.inf)
    later = tracker.prior_snapshot("NEW", snapshot_time_s=query)
    np.testing.assert_array_equal(later.last_accepted_time_s, factor.link_epochs_s)
