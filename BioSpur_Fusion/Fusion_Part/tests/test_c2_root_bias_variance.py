"""Consistent admission/update uncertainty without changing inherited policy."""
from dataclasses import replace
import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world import root_input_safety as safety
from biospur_fusion.c2_uwb_root_world.tight_range import (
    PersistentRangeBiasTracker, RangeBiasPriorSnapshot,
    linearize_raw_range_factors, update_raw_ranges,
)
from biospur_fusion.root_r3.models import RootState
from test_c2_continuous_full_state_feedback import ANCHORS, CLOCK, row_at


def _fixture():
    state=RootState(1.,np.r_[[1.95,1.3,1.],np.zeros(6)],np.eye(9)*.01)
    row=row_at(1.,np.array([2.,1.3,1.]))
    return state,row


def _args(state):
    return dict(anchors_m=ANCHORS,clock=CLOCK,tag_offset_world_m=np.zeros(3),
        tag_offset_velocity_world_mps=np.zeros(3),reference_epoch_s=state.time_s)


def test_legacy_none_prior_is_exact_existing_factor_and_update_path():
    state,row=_fixture();args=_args(state);args['range_bias_m']=np.zeros(8)
    factors=linearize_raw_range_factors(state,row,**args,_enforce_geometry=False)
    expected,decision=update_raw_ranges(state,row,**args)
    actual,got,nis,limit=safety.guarded_raw_update(state,row,**args)
    assert got.accepted and nis==factors.prior_nis and nis<=limit
    np.testing.assert_array_equal(actual.vector,expected.vector)
    np.testing.assert_array_equal(actual.covariance,expected.covariance)
    for field in ('innovations_m','sigma_m','sensor_sigma_m','robust_weights'):
        np.testing.assert_array_equal(getattr(got,field),getattr(decision,field))


def test_same_immutable_prior_reaches_admission_and_update_with_original_epoch(monkeypatch):
    state,row=_fixture()
    original=replace(row,t_round_us=tuple(1000+500*k for k in range(8)))
    epochs=np.array([CLOCK.seconds(original.strobe_us+.5*v) for v in original.t_round_us])
    state=RootState(float(np.median(epochs)),state.vector,state.covariance)
    retained=replace(original,valid_mask=0xF0)
    tracker=PersistentRangeBiasTracker()
    prior=tracker.prior_snapshot(row.node,snapshot_time_s=float(np.nextafter(epochs.min(),-np.inf)))
    before=prior.mean_m.copy();seen=[]
    def linear(*args,**kwargs):
        seen.append(('admission',kwargs['bias_prior'],kwargs['reference_epoch_s']))
        return linearize_raw_range_factors(*args,**kwargs)
    def update(*args,**kwargs):
        seen.append(('update',kwargs['bias_prior'],kwargs['reference_epoch_s']))
        return update_raw_ranges(*args,**kwargs)
    monkeypatch.setattr(safety,'linearize_raw_range_factors',linear)
    monkeypatch.setattr(safety,'update_raw_ranges',update)
    new,decision,nis,limit=safety.guarded_raw_update(state,retained,bias_prior=prior,**_args(state))
    assert decision.accepted and nis<=limit
    assert [s[0] for s in seen]==['admission','update']
    assert all(s[1] is prior and s[2]==state.time_s for s in seen)
    assert decision.reference_epoch_s==float(np.median(epochs))
    np.testing.assert_array_equal(prior.mean_m,before)
    np.testing.assert_array_equal(decision.sensor_sigma_m,np.full(4,.12))
    np.testing.assert_allclose(decision.sigma_m,np.full(4,np.sqrt(.12**2+.30**2)))
    # The tracker sees raw sensor sigma, not the augmented total uncertainty.
    tracker.update(row.node,decision)
    after=tracker.prior_snapshot(row.node,snapshot_time_s=float(epochs.max()+.01))
    last=decision.link_epochs_s
    expected=.30**2*.12**2/(.30**2+.12**2)+.05**2*(epochs.max()+.01-last)
    np.testing.assert_allclose(after.variance_m2[4:],expected,atol=1e-15)


def test_prior_and_legacy_mean_are_mutually_exclusive_even_for_incomplete_sweep():
    state,row=_fixture();row=replace(row,valid_mask=1)
    prior=PersistentRangeBiasTracker().prior_snapshot(row.node,snapshot_time_s=.9)
    with pytest.raises(ValueError,match='mutually exclusive'):
        safety.guarded_raw_update(state,row,bias_prior=prior,range_bias_m=np.zeros(8),**_args(state))


@pytest.mark.parametrize('delay',[0.,.1])
def test_nonprior_snapshot_is_rejected_before_state_change(delay):
    state,row=_fixture()
    epoch=min(CLOCK.seconds(row.strobe_us+.5*v) for v in row.t_round_us)+delay
    prior=RangeBiasPriorSnapshot(row.node,epoch,np.zeros(8),np.ones(8)*.09,np.full(8,np.nan))
    before=state.vector.copy()
    with pytest.raises(ValueError,match='strictly pre-epoch'):
        safety.guarded_raw_update(state,row,bias_prior=prior,**_args(state))
    np.testing.assert_array_equal(state.vector,before)


def test_rejection_leaves_root_and_prior_tracker_unchanged():
    state,row=_fixture();row=replace(row,ranges_mm=tuple(x+3000 for x in row.ranges_mm))
    tracker=PersistentRangeBiasTracker();prior=tracker.prior_snapshot(row.node,snapshot_time_s=.9)
    new,decision,nis,limit=safety.guarded_raw_update(state,row,bias_prior=prior,**_args(state))
    assert not decision.accepted and nis>limit and new is state
    tracker.update(row.node,decision)
    after=tracker.prior_snapshot(row.node,snapshot_time_s=.9)
    for field in ('mean_m','variance_m2','last_accepted_time_s'):
        np.testing.assert_array_equal(getattr(prior,field),getattr(after,field))
