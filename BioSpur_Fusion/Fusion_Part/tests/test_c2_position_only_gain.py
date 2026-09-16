"""Opt-in row-selective gain, not a universal nonlinear stability claim."""
from dataclasses import replace
import numpy as np
import pytest
from biospur_fusion.c2_uwb_root_world.tight_range import update_raw_ranges,linearize_raw_range_factors
from biospur_fusion.c2_uwb_root_world.root_input_safety import guarded_raw_update
from biospur_fusion.c2_uwb_root_world.support_points import SupportPoints
from biospur_fusion.c2_uwb_root_world.correction_budget import GlobalCorrectionBudget
from biospur_fusion.root_r3.models import RootState
from biospur_fusion.root_r3.estimator import propagate_inertial,RootFilterConfig
from test_c2_correction_budget import fixture,args
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at


@pytest.mark.parametrize('alpha',[0.,.1,1.])
@pytest.mark.parametrize('consider',[False,True])
def test_same_prior_position_only_gain_and_full_contact_joseph(alpha,consider):
    state,row=fixture();points=SupportPoints()
    state=points.update(state,np.array([[0,0,-.8],[.2,0,-.8]]),[True,True],[False,False],[1,1],.005,[False,False])
    full=points.covariance(state);oldmeans=points.means.copy()
    factors=linearize_raw_range_factors(state,row,**args())
    h=np.zeros((len(factors.anchors),len(full)));h[:,:9]=factors.state_jacobian
    r=np.diag(np.diag(factors.r_prior_m2)/factors.robust_weights)
    k=np.linalg.solve(h@full@h.T+r,h@full).T;k[9:]=0.
    full_vb=k[3:9].copy();k[:3]*=alpha
    if consider:k[:3]=0.
    a=np.eye(len(full))-k@h
    result,decision,_,_=guarded_raw_update(state,row,**args(),gain_scale=alpha,
        correction_gain_scope='position-only',consider_position=consider,transition_observer=points.root_transition)
    assert decision.accepted
    np.testing.assert_allclose(result.vector,state.vector+k[:9]@factors.innovations_m,atol=1e-14)
    np.testing.assert_allclose(result.vector[3:]-state.vector[3:],full_vb@factors.innovations_m,atol=1e-14)
    assert np.linalg.norm(result.vector[3:]-state.vector[3:])>1e-6
    np.testing.assert_allclose(points.covariance(result),a@full@a.T+k@r@k.T,atol=1e-13)
    np.testing.assert_array_equal(points.means,oldmeans)
    np.linalg.cholesky(points.covariance(result))


def test_default_exact_and_scope_validation():
    state,row=fixture()
    a,da=update_raw_ranges(state,row,**args(),gain_scale=.1)
    b,db=update_raw_ranges(state,row,**args(),gain_scale=.1,correction_gain_scope='full-state')
    np.testing.assert_array_equal(a.vector,b.vector);np.testing.assert_array_equal(a.covariance,b.covariance)
    assert da.reason==db.reason
    with pytest.raises(ValueError):update_raw_ranges(state,row,**args(),correction_gain_scope='position-only')
    with pytest.raises(ValueError):update_raw_ranges(state,row,**args(),gain_scale=.1,correction_gain_scope='unknown')


def test_repeated_actual_range_inertial_bias_loop_is_finite_and_contracts():
    truth=np.array([2.,1.3,1.]);sensor_bias=np.array([.04,-.03,.02])
    state=RootState(0.,np.r_[truth+[.12,-.08,.06],[.1,-.08,.06],np.zeros(3)],np.eye(9)*.1)
    budget=GlobalCorrectionBudget(.25);time=0.;history=[];zero_count=0;rejected=0
    force=sensor_bias+np.array([0,0,9.80665])
    for i in range(1200):
        dt=(.007,.013,.01)[i%3] if i!=500 else .2
        time+=dt;state,f=propagate_inertial(state,time,force,np.eye(3),RootFilterConfig())
        np.testing.assert_allclose(f[3:6,6:9],-np.eye(3)*dt,atol=1e-14)
        alpha,_=budget.consume(time);zero_count+=alpha==0
        row=row_at(time,truth)
        if i%17==0:row=replace(row,valid_mask=0);rejected+=1
        state,decision=update_raw_ranges(state,row,anchors_m=ANCHORS,clock=CLOCK,
            reference_epoch_s=time,gain_scale=alpha,correction_gain_scope='position-only')
        error=np.r_[state.position_m-truth,state.velocity_mps,state.vector[6:]-sensor_bias]
        history.append(error);assert np.isfinite(state.covariance).all()
        if i%100==0:np.linalg.cholesky(state.covariance)
    history=np.array(history)
    metrics={'peak_position_m':float(np.linalg.norm(history[:,:3],axis=1).max()),
        'terminal_position_m':float(np.linalg.norm(history[-1,:3])),
        'terminal_velocity_mps':float(np.linalg.norm(history[-1,3:6])),
        'terminal_bias_mps2':float(np.linalg.norm(history[-1,6:])),
        'zero_budget_events':int(zero_count),'rejected_events':rejected}
    print(metrics)
    assert metrics['peak_position_m']<.5
    assert metrics['terminal_position_m']<.06
    assert metrics['terminal_velocity_mps']<.06
    assert metrics['terminal_bias_mps2']<np.linalg.norm(sensor_bias)
    assert zero_count>=2 and rejected>0
