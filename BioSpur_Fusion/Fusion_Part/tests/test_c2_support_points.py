import numpy as np
from biospur_fusion.c2_uwb_root_world.support_points import SupportPoints
from biospur_fusion.c2_uwb_root_world.support_velocity import update_support_velocity
from biospur_fusion.c2_uwb_root_world.root_input_safety import CausalImuHold
from biospur_fusion.root_r3.models import RootState
from biospur_fusion.root_r3.estimator import RootFilterConfig,propagate_inertial,propagate_constant_velocity


def initial():return RootState(0.,np.zeros(9),np.eye(9)*.1)


def test_entry_shared_root_and_release_marginal():
    owner=SupportPoints();s=initial();offset=np.array([[0,0,-1],[.2,0,-1]])
    out=owner.update(s,offset,[True,True],[True,True],[1,1],.005)
    np.testing.assert_array_equal(out.vector,s.vector)
    np.testing.assert_array_equal(owner.anchor_covariance[:3,3:],s.covariance[:3,:3])
    np.linalg.cholesky(owner.covariance(s))
    p=owner.covariance(s);owner.release(0)
    keep=np.r_[np.arange(9),np.arange(12,15)]
    np.testing.assert_array_equal(owner.covariance(s),p[np.ix_(keep,keep)])


def test_propagation_hooks_compose_once_across_expiry():
    s=initial();hold=CausalImuHold(0.,[0,0,9.81],np.eye(3));seen=[];config=RootFilterConfig()
    after,_=hold.propagate(s,.012,config,transition_observer=seen.append)
    middle,f=propagate_inertial(s,.005,hold.force,hold.rotation,config)
    expected,g=propagate_constant_velocity(middle,.012,config)
    assert len(seen)==1
    np.testing.assert_allclose(seen[0],g@f)
    np.testing.assert_allclose(after.covariance,expected.covariance)


def test_persistent_constraint_reduces_relative_error_without_gauge_collapse():
    owner=SupportPoints();s=initial();offset=np.array([[0,0,-1],[.2,0,-1]])
    owner.update(s,offset,[True,False],[True,False],[1,1],.005)
    x=s.vector.copy();x[0]=.05
    # Independent prediction process noise creates relative uncertainty.
    predicted=RootState(.005,x,s.covariance+np.eye(9)*.01)
    out=owner.update(predicted,offset,[True,False],[True,False],[1,1],.005)
    assert 0<out.position_m[0]<.05
    assert out.covariance[0,0]>=s.covariance[0,0]-.001
    np.linalg.cholesky(owner.covariance(out))


def test_root_only_schmidt_velocity_hook_matches_augmented_joseph():
    owner=SupportPoints();s=initial();owner.enter(s,0,[0,0,-1]);prior=owner.covariance(s)
    seen=[]
    def callback(a):seen.append(a);owner.root_transition(a)
    out,_,r=update_support_velocity(s,[[.1,0,0]],[1.],.005,transition_observer=callback)
    h=np.zeros((3,12));h[:,3:6]=np.eye(3)
    k=np.zeros((12,3));k[:9]=np.linalg.solve(h@prior@h.T+r,h@prior[:,:9]).T
    a=np.eye(12)-k@h
    np.testing.assert_allclose(owner.covariance(out),a@prior@a.T+k@r@k.T,atol=1e-14)
    assert len(seen)==1


def test_raw_hook_actual_gain_and_rejection_transaction():
    from test_c2_support_position_guard import fixture,args
    from biospur_fusion.c2_uwb_root_world.tight_range import update_raw_ranges,linearize_raw_range_factors
    from biospur_fusion.c2_uwb_root_world.root_input_safety import guarded_raw_update
    from dataclasses import replace
    before,row=fixture();seen=[]
    after,d=update_raw_ranges(before,row,transition_observer=seen.append,**args())
    f=linearize_raw_range_factors(before,row,**args());h=f.state_jacobian
    r=np.diag(np.square(d.sigma_m)/d.robust_weights)
    k=np.linalg.solve(h@before.covariance@h.T+r,h@before.covariance).T
    assert len(seen)==1
    np.testing.assert_allclose(seen[0],np.eye(9)-k@h)
    np.testing.assert_allclose(after.vector,before.vector+k@f.innovations_m)
    seen.clear()
    rejected,d,_,_=guarded_raw_update(before,replace(row,valid_mask=0),transition_observer=seen.append,**args())
    assert not d.accepted and not seen
    np.testing.assert_array_equal(rejected.vector,before.vector)


def test_soft_point_entry_diffusion_once_and_gap_reset():
    owner=SupportPoints();s=initial();offset=np.zeros((2,3))
    owner.update(s,offset,[True,False],[False,False],[1,1],.005,[True,False])
    np.testing.assert_allclose(owner.anchor_covariance,s.covariance[:3,:3]+np.eye(3)*.1**2)
    prior=owner.anchor_covariance.copy();s=RootState(.005,s.vector,s.covariance)
    owner.update(s,offset,[True,False],[False,False],[1,1],.005,[True,False])
    np.testing.assert_allclose(owner.anchor_covariance,prior+np.eye(3)*owner.soft_diffusion*.005)
    prior=owner.anchor_covariance.copy()
    owner.update(s,offset,[True,False],[False,False],[1,1],.005,[True,False])
    np.testing.assert_array_equal(owner.anchor_covariance,prior)
    s=RootState(1.,s.vector,s.covariance)
    owner.update(s,offset,[True,False],[False,False],[1,1],.995,[True,False])
    np.testing.assert_allclose(owner.anchor_covariance,s.covariance[:3,:3]+np.eye(3)*.1**2)


def test_new_stationary_episode_preserves_root_and_other_anchor_then_updates():
    owner=SupportPoints(restart_stationary_episode=True);s=initial();offset=np.array([[0,0,-1],[.2,0,-1]])
    owner.update(s,offset,[True,True],[False,False],[1,1],.005,[True,False])
    other_mean=owner.means[3:].copy();other_cross=owner.cross[:,3:].copy()
    other_cov=owner.anchor_covariance[3:,3:].copy()
    x=s.vector.copy();x[0]=.05
    predicted=RootState(.005,x,s.covariance)
    out=owner.update(predicted,offset,[True,True],[True,False],[1,1],.005,[False,False])
    np.testing.assert_array_equal(out.vector,predicted.vector)
    np.testing.assert_array_equal(out.covariance,predicted.covariance)
    k=owner.sides.index(1);sl=slice(3*k,3*k+3)
    np.testing.assert_array_equal(owner.means[sl],other_mean)
    np.testing.assert_array_equal(owner.cross[:,sl],other_cross)
    np.testing.assert_array_equal(owner.anchor_covariance[sl,sl],other_cov)
    np.linalg.cholesky(owner.covariance(out))
    assert owner.stationary_entries==[(.005,0)] and owner.audit[-1][2]==0
    x=out.vector.copy();x[0]+=.01
    predicted=RootState(.010,x,out.covariance+np.eye(9)*.001)
    out=owner.update(predicted,offset,[True,True],[True,False],[1,1],.005,[False,False])
    assert out.position_m[0]<predicted.position_m[0]
    assert owner.stationary_entries==[(.005,0)]
    np.linalg.cholesky(owner.covariance(out))


def test_mode_history_release_and_no_reentry_on_stationary_to_moving():
    owner=SupportPoints(restart_stationary_episode=True);s=initial();offset=np.zeros((2,3))
    owner.update(s,offset,[True,False],[False,False],[1,1],.005,[False,False])
    mean=owner.means.copy()
    s=RootState(.005,s.vector,s.covariance)
    owner.update(s,offset,[True,False],[False,False],[1,1],.005,[True,False])
    np.testing.assert_array_equal(owner.means,mean)
    assert owner.moving_by_side=={0:True} and not owner.stationary_entries
    s=RootState(.010,s.vector,s.covariance)
    owner.update(s,offset,[False,False],[False,False],[1,1],.005,[False,False])
    assert not owner.moving_by_side
    owner.update(s,offset,[True,False],[False,False],[1,1],.005,[True,False])
    s=RootState(1.,s.vector,s.covariance)
    owner.update(s,offset,[True,False],[False,False],[1,1],.99,[False,False])
    assert owner.moving_by_side=={0:False} and not owner.stationary_entries


def test_moving_roundtrip_resets_once_and_duplicate_does_not_repeat():
    owner=SupportPoints(restart_stationary_episode=True);s=initial();offset=np.zeros((2,3))
    for epoch,moving in [(0.,False),(.005,True),(.010,False),(.010,False),(.015,False)]:
        s=RootState(epoch,s.vector,s.covariance)
        owner.update(s,offset,[True,False],[False,False],[1,1],.005,[moving,False])
    assert owner.stationary_entries==[(.010,0)]
