"""Bias-corrected residual likelihood, not symmetric physical NLOS."""
from dataclasses import replace
import numpy as np
import pytest
from biospur_fusion.c2_uwb_root_world.tight_range import (
    RawRangeUpdateConfig,_robust_weights,update_raw_ranges,PersistentRangeBiasTracker,prepare_raw_range_update)
from biospur_fusion.c2_uwb_root_world.root_input_safety import guarded_raw_update
from biospur_fusion.root_r3.models import RootState
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at


def config(enabled=False):
    return RawRangeUpdateConfig(nominal_sigma_m=.25,positive_nlos_cauchy_scale_m=.12,
                                symmetric_corrected_discrepancy=enabled)


def test_zero_small_signed_residual_and_positive_tail_unchanged():
    residual=np.array([-.5,-.005,0.,.005,.5]);sigma=np.full(5,.25)
    old=_robust_weights(residual,sigma,config());new=_robust_weights(residual,sigma,config(True))
    np.testing.assert_array_equal(old[2:],new[2:])
    assert new[2]==1 and new[1]>.998 and new[0]<.06
    np.testing.assert_allclose(new,new[::-1])
    np.testing.assert_array_equal(old,_robust_weights(residual,sigma,replace(config(),symmetric_corrected_discrepancy=False)))


def test_negative_stale_bias_recovery_uses_same_prior_and_joseph():
    state=RootState(1.,np.r_[[1.95,1.3,1.],np.zeros(6)],np.eye(9)*.01)
    row=row_at(1.,state.position_m)
    tracker=PersistentRangeBiasTracker()
    prior=tracker.prior_snapshot(row.node,snapshot_time_s=.9)
    mean=np.zeros(8);mean[0]=.5 # one link returns to LOS, others remain consistent
    prior=replace(prior,mean_m=mean,variance_m2=np.full(8,.005))
    args=dict(anchors_m=ANCHORS,clock=CLOCK,bias_prior=prior,
              tag_offset_world_m=np.zeros(3),tag_offset_velocity_world_mps=np.zeros(3),reference_epoch_s=1.)
    maps=[]
    old,_=update_raw_ranges(state,row,config=config(),gain_scale=1.,**args)
    new,decision,nis,limit=guarded_raw_update(state,row,config=config(True),transition_observer=maps.append,**args)
    assert decision.accepted and nis<=limit and len(maps)==1
    assert np.linalg.norm(new.position_m-state.position_m)<np.linalg.norm(old.position_m-state.position_m)
    assert np.linalg.eigvalsh(new.covariance).min()>0
    np.testing.assert_array_equal(prior.mean_m,mean)
    np.testing.assert_allclose(decision.sensor_sigma_m,.25)
    np.testing.assert_allclose(decision.sigma_m,np.sqrt(.25**2+.005))


def test_small_true_translation_feedback_remains_nonzero():
    state=RootState(1.,np.r_[[1.95,1.3,1.],np.zeros(6)],np.eye(9)*.01)
    row=row_at(1.,state.position_m+np.array([.01,0,0]))
    out,decision=update_raw_ranges(state,row,anchors_m=ANCHORS,clock=CLOCK,config=config(True),gain_scale=1.)
    assert decision.accepted and out.position_m[0]>state.position_m[0]


@pytest.mark.parametrize('consider',[False,True])
def test_prepared_reference_reuse_exact_state_covariance_and_callback(consider):
    state=RootState(1.,np.r_[[1.95,1.3,1.],np.zeros(6)],np.eye(9)*.01)
    row=row_at(1.,state.position_m+np.array([.02,0,0]))
    args=dict(anchors_m=ANCHORS,clock=CLOCK,config=config(True),reference_epoch_s=1.)
    prepared=prepare_raw_range_update(state,row,**args)
    callbacks_a=[];callbacks_b=[]
    a,da=update_raw_ranges(state,row,**args,consider_position=consider,transition_observer=callbacks_a.append)
    b,db=update_raw_ranges(state,row,**args,consider_position=consider,transition_observer=callbacks_b.append,prepared=prepared)
    np.testing.assert_array_equal(a.vector,b.vector);np.testing.assert_array_equal(a.covariance,b.covariance)
    np.testing.assert_array_equal(callbacks_a[0],callbacks_b[0])
    for field in ('innovations_m','robust_weights','sigma_m','predicted_ranges_m'):
        np.testing.assert_array_equal(getattr(da,field),getattr(db,field))
    with pytest.raises(ValueError,match='reference mismatch'):
        update_raw_ranges(state,row,**{**args,'reference_epoch_s':.999},gain_scale=1.,prepared=prepared)
