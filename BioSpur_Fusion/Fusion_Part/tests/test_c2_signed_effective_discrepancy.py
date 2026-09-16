"""One-sided bound contrast and unchanged causal measurement use."""
from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.tight_range import (
    PersistentRangeBiasConfig, PersistentRangeBiasTracker, RawRangeDecision,
    RawRangeUpdateConfig, update_raw_ranges,
)
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow
from biospur_fusion.root_r3.models import RootState


def _decision(epoch, residual):
    residual = np.broadcast_to(np.asarray(residual, float), (8,)).copy()
    return RawRangeDecision(
        True, 'ACCEPTED', tuple(range(8)), np.full(8, epoch), epoch,
        np.full(8, 2.), np.full(8, 2.) - residual, residual,
        residual / .25, np.ones(8), np.full(8, .25), 3, 1., 1, 'FIXTURE')


def _tracker(signed=True):
    return PersistentRangeBiasTracker(PersistentRangeBiasConfig(
        signed_effective_discrepancy=signed))


def test_negative_discrepancy_is_learned_only_in_explicit_signed_mode():
    signed, legacy = _tracker(), _tracker(False)
    for epoch in np.arange(20) * .12 + 1.:
        signed.update('BSFC2CC', _decision(epoch, -.1))
        legacy.update('BSFC2CC', _decision(epoch, -.1))
    assert np.all(signed.bias_vector('BSFC2CC') < 0.)
    np.testing.assert_array_equal(legacy.bias_vector('BSFC2CC'), np.zeros(8))


def test_default_matches_frozen_legacy_arithmetic_exactly():
    tracker = PersistentRangeBiasTracker()
    mean = np.zeros(8); variance = np.full(8, .30**2)
    last = None
    for k in range(20):
        epoch = 1. + k * .12
        residual = np.linspace(-.2, .3, 8) * (-1 if k % 3 == 0 else 1)
        decision = _decision(epoch, residual)
        for a in range(8):
            predicted = variance[a] + .05**2 * (0. if last is None else epoch-last)
            gain = predicted / (predicted + .25**2)
            observed = float(residual[a] + mean[a])
            mean[a] = float(np.clip(mean[a] + gain * (observed - mean[a]), 0., 3.))
            variance[a] = max((1.-gain)*predicted, 1e-12)
        last = epoch
        np.testing.assert_array_equal(tracker.update('BSFC2CC', decision), mean)
        prior = tracker.prior_snapshot('BSFC2CC', snapshot_time_s=epoch+.01)
        np.testing.assert_array_equal(prior.variance_m2, variance + .05**2*.01)


@pytest.mark.parametrize('sign', [-1., 1.])
def test_inherited_absolute_bound_applies_to_both_signs(sign):
    tracker = _tracker()
    np.testing.assert_array_equal(
        tracker.update('BSFC2CC', _decision(1., sign*100.)), np.full(8, sign*3.))


@pytest.mark.parametrize('signed', [False, True])
def test_rejected_evidence_is_inert_and_nonmonotonic_evidence_fails(signed):
    tracker = _tracker(signed)
    decision = _decision(1., -.1)
    tracker.update('BSFC2CC', decision)
    before = tracker.prior_snapshot('BSFC2CC', snapshot_time_s=1.1)
    tracker.update('BSFC2CC', replace(_decision(2., 100.), accepted=False, reason='REJECT'))
    after = tracker.prior_snapshot('BSFC2CC', snapshot_time_s=1.1)
    for field in ('mean_m', 'variance_m2', 'last_accepted_time_s'):
        np.testing.assert_array_equal(getattr(before, field), getattr(after, field))
    with pytest.raises(ValueError, match='chronological'):
        tracker.update('BSFC2CC', decision)
    np.testing.assert_array_equal(tracker.bias_vector('BSFC2CC'), before.mean_m)


def test_prior_snapshot_is_not_retroactively_changed_by_current_evidence():
    tracker = _tracker()
    prior = tracker.prior_snapshot('BSFC2CC', snapshot_time_s=.9)
    tracker.update('BSFC2CC', _decision(1., -.2))
    np.testing.assert_array_equal(prior.mean_m, np.zeros(8))
    assert np.all(tracker.prior_snapshot('BSFC2CC', snapshot_time_s=1.1).mean_m < 0.)


def test_coherent_common_translation_still_corrects_root_with_signed_prior():
    anchors = np.array([[0,0,0],[4,0,0],[4,3,0],[0,3,0],
                        [0,0,2],[4,0,2],[4,3,2],[0,3,2]], float)
    tracker = _tracker()
    tracker.update('BSFC2CC', _decision(.8, -.1))
    prior_bias = tracker.bias_vector('BSFC2CC')
    old = np.array([2., 1.2, .9]); truth = old + np.array([.2, -.15, .1])
    ranges = np.linalg.norm(anchors-truth, axis=1) + prior_bias
    row = UwbRow('BSFC2CC',0,1,1,1_000_000,1_010_000,tuple(range(8)),
        tuple(np.rint(ranges*1000).astype(int)),(2000,)*8,(100,)*8,255)
    x = np.zeros(9); x[:3] = old
    state = RootState(1.001, x, np.eye(9)*.5)
    posterior, decision = update_raw_ranges(state,row,anchors_m=anchors,
        clock=ClockModel(0,1000.,0.,0.),range_bias_m=prior_bias,
        config=RawRangeUpdateConfig(nominal_sigma_m=.25,positive_nlos_cauchy_scale_m=.12))
    assert decision.accepted
    assert np.linalg.norm(posterior.position_m-truth) < .25*np.linalg.norm(old-truth)
    assert np.dot(posterior.position_m-old,truth-old) > 0.
    np.testing.assert_array_equal(tracker.bias_vector('BSFC2CC'), prior_bias)


def test_signed_mode_requires_an_explicit_boolean():
    with pytest.raises(ValueError, match='boolean'):
        PersistentRangeBiasConfig(signed_effective_discrepancy='yes').validate()
