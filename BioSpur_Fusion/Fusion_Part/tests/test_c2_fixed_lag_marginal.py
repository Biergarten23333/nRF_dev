import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.fixed_lag_marginal import FixedLagMarginal
from biospur_fusion.c2_uwb_root_world.joint_transition_tape import JointSnapshot,notify_transition
from biospur_fusion.c2_uwb_root_world.support_points import SupportPoints
from biospur_fusion.c2_uwb_root_world.support_velocity import update_support_velocity
from biospur_fusion.root_r3.models import RootState


def snap(t,p,mean=None,episodes=()):
    return JointSnapshot.capture(t,np.zeros(len(p)) if mean is None else mean,p,episodes)


@pytest.mark.parametrize('constrained',[False,True])
def test_augmented_joseph_including_current_constrained_gain(constrained):
    rng=np.random.default_rng(4);matrix=rng.normal(size=(12,12))
    p=matrix@matrix.T+np.eye(12);before=snap(0.,p,episodes=((0,0),))
    lag=FixedLagMarginal();lag.add_native(0,before)
    # Propagation adds independent current noise while historical P is fixed.
    f=np.eye(12);f[0,3]=.01
    pcur=f@p@f.T+np.eye(12)*.1
    prior=snap(.01,pcur,episodes=before.episodes)
    lag.commit('prediction',before,prior,f,independent_noise=True)
    c=p[:9]@f.T
    h=rng.normal(size=(2,12));r=np.eye(2)*.3;innovation=np.array([.2,-.1])
    s=h@pcur@h.T+r;k=np.linalg.solve(s,h@pcur).T
    shadow_gain=k.copy()
    if constrained:k[:3]=0.;k[9:]=0.
    kp=np.linalg.solve(s,h@c.T).T
    joint=np.block([[p[:9,:9],c],[c.T,pcur]])
    full_h=np.column_stack((np.zeros((2,9)),h));full_k=np.vstack((kp,shadow_gain))
    a=np.eye(21)-full_k@full_h
    expected=a@joint@a.T+full_k@r@full_k.T
    current_a=np.eye(12)-k@h
    live_p=current_a@pcur@current_a.T+k@r@k.T
    after=snap(.01,live_p,k@innovation,before.episodes)
    lag.commit('assimilation',prior,after,current_a,independent_noise=True,measurement=(h,r,innovation,k))
    row=lag.pending[0]
    np.testing.assert_allclose(row.mean,kp@innovation,atol=1e-13)
    np.testing.assert_allclose(row.covariance,expected[:9,:9],atol=1e-12)
    np.testing.assert_allclose(row.cross,expected[:9,9:],atol=1e-12)
    np.testing.assert_allclose(lag.shadow_mean,shadow_gain@innovation,atol=1e-12)
    np.testing.assert_allclose(lag.shadow_covariance,expected[9:,9:],atol=1e-12)
    np.linalg.cholesky(expected)


def test_real_owner_birth_release_and_measurement_hooks_leave_current_exact():
    p=np.eye(9);p[0,3]=p[3,0]=.2
    root=RootState(0.,np.zeros(9),p)
    points=SupportPoints();lag=FixedLagMarginal();points.tape=lag;points._record_state=root
    lag.add_native(0,points.snapshot(root))
    points.enter(root,0,np.zeros(3))
    np.testing.assert_allclose(lag.pending[0].cross,np.column_stack((p,p[:,:3])))
    updated,*_=update_support_velocity(root,np.array([[.2,0,0]]),np.ones(1),.005,
        consider_position=True,transition_observer=points.root_transition)
    control,*_=update_support_velocity(root,np.array([[.2,0,0]]),np.ones(1),.005,consider_position=True)
    np.testing.assert_array_equal(updated.vector,control.vector)
    np.testing.assert_array_equal(updated.covariance,control.covariance)
    assert np.linalg.norm(lag.pending[0].mean)>0
    before=lag.pending[0].cross.copy();points.release(0)
    np.testing.assert_array_equal(lag.pending[0].cross,before[:,:9])
    points.enter(updated,0,np.ones(3))
    assert points._episodes[0]==1


def test_future_measurement_cannot_change_expired_sample_and_exact_deadline_included():
    def run(future):
        lag=FixedLagMarginal();p=np.eye(9);initial=snap(0.,p);lag.add_native(0,initial)
        h=np.eye(9)[:1];r=np.eye(1);k=h.T/2;a=np.eye(9)-k@h
        prior=snap(.12,p);after=snap(.12,a@p@a.T+k@r@k.T)
        lag.commit('prediction',initial,prior,np.eye(9),independent_noise=True)
        lag.commit('assimilation',prior,after,a,independent_noise=True,measurement=(h,r,np.ones(1),k))
        lag.advance(.120001)
        later=snap(.13,after.covariance)
        lag.commit('prediction',after,later,np.eye(9),independent_noise=True)
        k2=later.covariance@h.T/(h@later.covariance@h.T+r)
        a2=np.eye(9)-k2@h
        final=snap(.13,a2@later.covariance@a2.T+k2@r@k2.T)
        lag.commit('assimilation',later,final,a2,independent_noise=True,measurement=(h,r,np.array([future]),k2))
        return lag.arrays()
    a,b=run(1.),run(1e6)
    for key in a:np.testing.assert_array_equal(a[key],b[key])
    assert a['lag_root_state'][0,0]==.5


def test_initialization_future_prefix_and_tail_are_unavailable():
    lag=FixedLagMarginal();lag.information_time_s=.131
    lag.add_native(0,snap(0.,np.eye(9)));lag.advance(.121)
    assert lag.unavailable==1 and not lag.completed
    later=snap(.125,np.eye(9))
    lag.commit('prediction',lag.last_snapshot,later,np.eye(9),independent_noise=True)
    lag.add_native(1,later)
    assert lag.diagnostic()['tail_unavailable']==1
    lag.advance(.246)
    assert lag.arrays()['lag_native_index'].tolist()==[1]


def test_beyond_horizon_source_skips_historical_update_but_transports_cross():
    lag=FixedLagMarginal();initial=snap(0.,np.eye(9));lag.add_native(0,initial)
    lag.observation_time_s=.2
    h=np.eye(9)[:1];r=np.eye(1);k=h.T/2;a=np.eye(9)-k@h
    after=snap(0.,a@a.T+k@r@k.T)
    lag.commit('assimilation',initial,after,a,independent_noise=True,measurement=(h,r,np.ones(1),k))
    np.testing.assert_array_equal(lag.pending[0].cross,a.T)
    lag.advance(.121)
    assert lag.arrays()['lag_root_state'][0,0]==0.
    assert lag.arrays()['lag_last_observation_time_s'][0]==0.


def test_future_availability_does_not_expire_or_remove_model_horizon_outputs():
    lag=FixedLagMarginal();initial=snap(0.,np.eye(9));lag.add_native(0,initial)
    lag.observation_time_s=.01;lag.availability_time_s=.2
    h=np.eye(9)[:1];r=np.eye(1);k=h.T/2;a=np.eye(9)-k@h
    after=snap(0.,a@a.T+k@r@k.T)
    lag.commit('assimilation',initial,after,a,independent_noise=True,measurement=(h,r,np.ones(1),k))
    assert not lag.completed and len(lag.pending)==1
    lag.advance(.121);out=lag.arrays()
    assert out['lag_root_state'][0,0]==.5
    assert out['lag_last_observation_time_s'][0]==.01
    assert out['lag_earliest_information_ready_time_s'][0]==.2
    assert out['lag_emission_time_s'][0]==.121


def test_forbidden_source_prior_cannot_reenter_through_earlier_source_observation():
    lag=FixedLagMarginal();prior=snap(0.,np.eye(9));lag.add_native(0,prior)
    h=np.eye(9)[:1];r=np.eye(1)
    for source in (.13,.01):
        lag.observation_time_s=source
        k=np.linalg.solve(h@prior.covariance@h.T+r,h@prior.covariance).T
        a=np.eye(9)-k@h
        after=snap(0.,a@prior.covariance@a.T+k@r@k.T,prior.mean+k[:,0])
        lag.commit('assimilation',prior,after,a,independent_noise=True,measurement=(h,r,np.ones(1),k))
        prior=after
    np.testing.assert_array_equal(lag.pending[0].mean,np.zeros(9))
    np.testing.assert_array_equal(lag.pending[0].covariance,np.eye(9))
    assert lag.source_horizon_skips==2


def test_capacity_and_missing_likelihood_fail_closed():
    lag=FixedLagMarginal(maximum_roots=1);s=snap(0.,np.eye(9));lag.add_native(0,s)
    next_state=snap(.005,np.eye(9))
    lag.commit('prediction',s,next_state,np.eye(9),independent_noise=True)
    with pytest.raises(RuntimeError):lag.add_native(1,next_state)
    with pytest.raises(ValueError):lag.commit('assimilation',next_state,next_state,np.eye(9),independent_noise=True)


def test_repeated_observations_match_exact_batch_despite_zero_live_gain():
    lag=FixedLagMarginal();live=snap(0.,np.eye(9));lag.add_native(0,live)
    h=np.eye(9)[:1];r=np.eye(1);zero_gain=np.zeros((9,1))
    for count in range(1,7):
        lag.commit('assimilation',live,live,np.eye(9),independent_noise=True,
                   measurement=(h,r,np.ones(1),zero_gain))
        assert lag.pending[0].mean[0]==pytest.approx(count/(count+1))
        assert lag.pending[0].covariance[0,0]==pytest.approx(1/(count+1))
        assert lag.shadow_mean[0]==pytest.approx(count/(count+1))
        assert lag.shadow_covariance[0,0]==pytest.approx(1/(count+1))
        np.testing.assert_array_equal(live.mean,np.zeros(9))
        np.testing.assert_array_equal(live.covariance,np.eye(9))
    next_live=snap(.005,np.eye(9))
    lag.commit('prediction',live,next_live,np.eye(9),independent_noise=True)
    lag.add_native(1,next_live)
    assert lag.pending[-1].mean[0]==pytest.approx(6/7)


def test_shadow_affine_contact_birth_release_preserves_prior_information():
    lag=FixedLagMarginal();live=snap(0.,np.eye(9));lag.add_native(0,live)
    h=np.eye(9)[:1]
    lag.commit('assimilation',live,live,np.eye(9),independent_noise=True,
               measurement=(h,np.eye(1),np.ones(1),np.zeros((9,1))))
    a=np.vstack((np.eye(9),np.eye(9)[:3]));q=np.zeros((12,12));q[9:,9:]=np.eye(3)*.01
    mean=np.r_[np.zeros(9),[2.,3.,4.]]
    born=snap(0.,a@live.covariance@a.T+q,mean,((0,0),))
    expected_mean=mean+a@lag.shadow_mean;expected_p=a@lag.shadow_covariance@a.T+q
    lag.commit('topology',live,born,a,independent_noise=True)
    np.testing.assert_allclose(lag.shadow_mean,expected_mean)
    np.testing.assert_allclose(lag.shadow_covariance,expected_p)
    select=np.eye(12)[:9];released=snap(0.,born.covariance[:9,:9])
    lag.commit('topology',born,released,select,independent_noise=True)
    np.testing.assert_allclose(lag.shadow_mean,expected_mean[:9])
    np.testing.assert_allclose(lag.shadow_covariance,expected_p[:9,:9])


def test_prediction_does_not_accept_negative_inferred_noise():
    lag=FixedLagMarginal();live=snap(0.,np.eye(9));lag.add_native(0,live)
    with pytest.raises(ValueError,match='positive semidefinite'):
        lag.commit('prediction',live,snap(.01,np.eye(9)*.5),np.eye(9),independent_noise=True)
