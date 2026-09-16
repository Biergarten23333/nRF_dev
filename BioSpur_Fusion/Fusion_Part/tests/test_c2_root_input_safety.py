from dataclasses import replace
import numpy as np
import pytest
from scipy.stats import chi2

from biospur_fusion.root_r3.models import RootState
from biospur_fusion.root_r3.estimator import (
    RootFilterConfig,propagate_inertial,propagate_constant_velocity,
)
from biospur_fusion.c2_uwb_root_world.root_input_safety import (
    CausalImuHold,guarded_raw_update,inherited_raw_nis_limit,
)
from biospur_fusion.c2_uwb_root_world.tight_range import PersistentRangeBiasTracker
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at

CFG=RootFilterConfig()


def test_invalid_input_fails_closed_without_replacing_held_sample():
    for t,r in [(np.nan,np.eye(3)),(0.,np.diag([1.,1.,-1.]))]:
        with pytest.raises(ValueError):
            CausalImuHold(t,np.zeros(3),r)
    hold=CausalImuHold(0.,np.zeros(3),np.eye(3))
    with pytest.raises(ValueError):
        hold.observe(np.inf,np.ones(3),np.eye(3))
    assert hold.time_s==0.
    np.testing.assert_array_equal(hold.force,np.zeros(3))
    with pytest.raises(ValueError):
        hold.propagate(root(),np.nan)


def root():
    return RootState(0.,np.r_[[2.,1.3,1.],[.2,0,.1],np.zeros(3)],np.eye(9)*.01)


def guarded(state,row):
    return guarded_raw_update(state,row,anchors_m=ANCHORS,clock=CLOCK,
        range_bias_m=np.zeros(8),tag_offset_world_m=np.zeros(3),
        tag_offset_velocity_world_mps=np.zeros(3),reference_epoch_s=state.time_s)


def test_fresh_moving_input_matches_existing_propagation_exactly():
    state=root();baseline=state;force=np.array([.3,0,9.9]);hold=CausalImuHold(0,force,np.eye(3))
    for i in range(1,101):
        t=i*.00499994
        baseline,_=propagate_inertial(baseline,t,force,np.eye(3),CFG)
        state,audit=hold.propagate(state,t,CFG)
        np.testing.assert_array_equal(state.vector,baseline.vector)
        np.testing.assert_array_equal(state.covariance,baseline.covariance)
        assert audit.stale_cv_duration_s==0
        force=np.array([.3+.01*i,0,9.9])
        hold.observe(t,force,np.eye(3))


def test_expiry_split_preserves_velocity_grows_covariance_and_resumes():
    state=root();force=np.array([0,0,11.80665]);hold=CausalImuHold(0,force,np.eye(3))
    expected,_=propagate_inertial(state,.005,force,np.eye(3),CFG)
    expected,_=propagate_constant_velocity(expected,1.5,CFG)
    actual,audit=hold.propagate(state,1.5,CFG)
    np.testing.assert_array_equal(actual.vector,expected.vector)
    np.testing.assert_array_equal(actual.covariance,expected.covariance)
    assert audit.stale_cv_duration_s==1.495
    assert actual.velocity_mps[0]==.2 and actual.velocity_mps[2]==.11
    assert np.trace(actual.covariance)>np.trace(state.covariance)
    hold.observe(1.5,[0,0,8.80665],np.eye(3))
    resumed,audit=hold.propagate(actual,1.504,CFG)
    assert audit.stale_cv_duration_s==0
    np.testing.assert_allclose(resumed.velocity_mps,[.2,0,.106],atol=1e-14)


def test_intervening_events_do_not_extend_imu_hold():
    start=root();hold=CausalImuHold(0,[0,0,11.80665],np.eye(3));state=start
    for t in [.003,.007,.1,.3,1.5]:
        state,_=hold.propagate(state,t,CFG)
    direct,_=hold.propagate(start,1.5,CFG)
    np.testing.assert_allclose(state.vector,direct.vector,atol=1e-14)
    np.testing.assert_allclose(state.covariance,direct.covariance,atol=1e-10)


def test_bad_sweep_is_atomic_noop_including_external_bias_then_recovers():
    state,_=propagate_inertial(root(),.1,[0,0,9.80665],np.eye(3),CFG)
    row=row_at(.1,state.position_m,True)
    bias=PersistentRangeBiasTracker();bias.bias_vector(row.node);before=bias.snapshot()
    new,decision,nis,limit=guarded(state,row)
    assert not decision.accepted and decision.reason=='PRIOR_RAW_SWEEP_NIS_REJECT'
    assert new is state and nis>limit
    bias.update(row.node,decision)
    assert bias.snapshot()==before
    new,decision,nis,limit=guarded(state,row_at(.1,state.position_m))
    assert decision.accepted and nis<=limit


def test_valid_ranges_update_through_dropout_without_velocity_pin():
    state=root();hold=CausalImuHold(0,[0,0,9.80665],np.eye(3));start=state.position_m.copy()
    for t in [.1,.2,.3,.4]:
        state,audit=hold.propagate(state,t,CFG)
        assert audit.stale_cv_duration_s>0
        state,decision,_,_=guarded(state,row_at(t,start+[.2*t,0,.1*t]))
        assert decision.accepted
    assert np.linalg.norm(state.velocity_mps)>.15
    assert np.linalg.norm(state.position_m-(start+[.08,0,.04]))<.005
    confidence=chi2.cdf(CFG.nis_limit_3d,3)
    for dof in range(4,9):
        assert abs(chi2.cdf(inherited_raw_nis_limit(dof),dof)-confidence)<1e-12
