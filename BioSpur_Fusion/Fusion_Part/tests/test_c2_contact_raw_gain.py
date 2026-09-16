"""First-order supported-point raw routing, not native-motion locking."""
from dataclasses import replace
import copy
import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.contact_raw_gain import project_contact_gain
from biospur_fusion.c2_uwb_root_world.tight_range import RawRangeUpdateConfig, linearize_raw_range_factors
from biospur_fusion.c2_uwb_calibration.articulated_range import _so3_right_jacobian
from test_c2_natural_geometry_contact import natural_owner
from test_c2_articulated_contact import ankles
from test_c2_continuous_full_state_feedback import ANCHORS, CLOCK, row_at


def supported():
    j=natural_owner()
    j.update_contact(ankles(j),[True,True],[True,True],[1,1],.005,[False,False])
    return j


def measurement(j,node='BSFEC35'):
    return replace(row_at(0.,j.state.root.position_m+j.tags()[node]+[.02,.01,0.]),node=node)


def constraint(j,sides):
    value=np.zeros((len(sides),3,39));value[:,:,:3]=np.eye(3)
    jac=j.point_jacobians()
    for i,side in enumerate(sides):value[i,:,9:]=jac[('ankle_left','ankle_right')[side]]
    return value.reshape(-1,39)


@pytest.mark.parametrize('redundant',[False,True])
def test_covariance_weighted_projection_is_rank_revealing_and_idempotent(redundant):
    rng=np.random.default_rng(10)
    a=rng.normal(size=(39,39));p=a@a.T+np.eye(39)*.1
    c=rng.normal(size=(3,39))
    if redundant:c=np.vstack((c,c))
    k=rng.normal(size=(39,8))
    original=k.copy()
    projected,audit=project_contact_gain(k,p,c)
    np.testing.assert_allclose(c@projected,0.,atol=1e-12)
    expected=k-p@c.T@np.linalg.pinv(c@p@c.T)@c@k
    np.testing.assert_allclose(projected,expected,atol=2e-13)
    twice,_=project_contact_gain(projected,p,c)
    np.testing.assert_allclose(twice,projected,atol=2e-13)
    np.testing.assert_array_equal(k,original)
    assert audit['constraint_rank']==3
    assert not audit['finite_endpoint_lock_guaranteed']


@pytest.mark.parametrize('node',['BSFC2CC','BSF31CC','BSFAA61','BSF1120',
                                'BSFEC35','BSFB165','BSF44AD','BSF3C79','BSF8BC4'])
def test_stationary_protection_allows_root_and_nonroot_pose_updates(node):
    j=supported();old=j.state;anchor=j.contacts.means.copy()
    before=old.root.position_m+j.points()['ankle_left']
    decision=j.update_ranges(measurement(j,node),anchors_m=ANCHORS,clock=CLOCK,
        reference_epoch_s=0.,consider_position=True,protected_contact_sides=(0,))
    assert decision.accepted
    assert np.linalg.norm(j.last_orientation_delta)>1e-7
    assert np.linalg.norm(j.state.root.position_m-old.root.position_m)>1e-7
    np.testing.assert_array_equal(j.contacts.means,anchor)
    audit=j.last_contact_routing_audit
    assert audit['root_position_mask_replaced']
    assert audit['linear_gain_residual_max']<1e-12
    assert audit['linear_endpoint_correction_max_m']<1e-12
    shift=j.state.root.position_m+j.points()['ankle_left']-before
    np.testing.assert_allclose(audit['finite_endpoint_shift_m'],shift[None],atol=1e-14)
    # Taylor remainder of the fixed-length FK vectors: a finite correction is
    # not exactly locked, but its residual is quadratic at this smooth pose.
    assert np.linalg.norm(shift)<=2*np.linalg.norm(j.last_orientation_delta)**2
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_linear_stationary_retraction_remainder_is_quadratic():
    j=supported();old=j.state;c=constraint(j,(0,1))
    rng=np.random.default_rng(20)
    gain,_=project_contact_gain(rng.normal(size=(39,1)),old.covariance,c)
    error=gain[:,0]*.001
    before=old.root.position_m+ankles(j)
    residual=[]
    for scale in (1.,.5):
        trial=copy.deepcopy(j)
        trial._inject_range_error(trial.state,error*scale,trial.state.covariance)
        residual.append(np.linalg.norm(trial.state.root.position_m+ankles(trial)-before))
    assert residual[0]>1e-10
    assert residual[0]/residual[1]==pytest.approx(4.,rel=.01)


def test_augmented_joseph_uses_actual_gain_and_retains_cross_covariance(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.contact_raw_gain as module
    j=supported();old=j.state;prior=j.contacts.covariance(j.tangent()).copy()
    row=measurement(j);config=RawRangeUpdateConfig()
    f=linearize_raw_range_factors(old.root,row,anchors_m=ANCHORS,clock=CLOCK,
        tag_offset_world_m=j.tags()[row.node],reference_epoch_s=0.,config=config,_enforce_geometry=False)
    h=np.zeros((len(f.anchors),len(prior)));h[:,:9]=f.state_jacobian
    h[:,9:39]=h[:,:3]@j.tag_jacobian(row.node)
    noise=np.diag(np.diag(f.r_prior_m2)/f.robust_weights)
    gain=np.linalg.solve(h@prior@h.T+noise,h@prior).T
    projected,_=project_contact_gain(gain[:39],prior[:39,:39],constraint(j,(0,1)))
    gain[:39]=projected;gain[39:]=0.
    expected=(np.eye(len(prior))-gain@h)@prior@(np.eye(len(prior))-gain@h).T+gain@noise@gain.T
    captured={}
    original=module.project_contact_gain
    def spy(k,p,c):
        result=original(k,p,c);captured['gain']=result[0].copy();captured['constraint']=c.copy()
        return result
    monkeypatch.setattr(module,'project_contact_gain',spy)
    decision=j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.,
                             protected_contact_sides=(0,1))
    assert decision.accepted
    np.testing.assert_allclose(captured['constraint']@captured['gain'],0.,atol=1e-12)
    reset=np.eye(len(prior))
    for i,d in enumerate(j.last_orientation_delta):reset[9+3*i:12+3*i,9+3*i:12+3*i]=_so3_right_jacobian(d)
    np.testing.assert_allclose(j.contacts.covariance(j.tangent()),reset@expected@reset.T,atol=2e-12,rtol=1e-10)
    # The existing full-Joseph path symmetrizes last-bit skew in the prior.
    np.testing.assert_allclose(j.contacts.anchor_covariance,prior[39:,39:],atol=1e-18,rtol=1e-14)
    assert np.linalg.norm(j.contacts.cross)>0
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_no_explicit_protection_keeps_existing_default_even_with_support():
    j=supported();other=copy.deepcopy(j);row=measurement(j)
    kwargs=dict(anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.,consider_position=True,preserve_anchor_mean=True)
    j.update_ranges(row,**kwargs)
    other.update_ranges(row,protected_contact_sides=(),**kwargs)
    np.testing.assert_array_equal(j.state.rotations,other.state.rotations)
    np.testing.assert_array_equal(j.contacts.covariance(j.tangent()),other.contacts.covariance(other.tangent()))
    assert j.last_contact_routing_audit['protected_sides']==()
    assert j.last_contact_routing_audit['update_applied']
    assert j.last_contact_routing_audit['linear_gain_residual_max'] is None
    np.testing.assert_array_equal(j.state.root.position_m,[2.,1.3,1.])


@pytest.mark.parametrize('sides',[(0,0),(2,),(-1,),(True,),('left',)])
def test_bad_side_authority_is_rejected(sides):
    j=supported();old=j.state
    with pytest.raises(ValueError):
        j.update_ranges(measurement(j),anchors_m=ANCHORS,clock=CLOCK,protected_contact_sides=sides)
    assert j.state is old


@pytest.mark.parametrize('mode',['absent','missing_episode','legacy','leg_only'])
def test_inapplicable_stationary_authority_fails_closed(mode):
    j=supported()
    if mode=='absent':j.contacts.release(0)
    elif mode=='missing_episode':j.contacts._episodes.pop(0)
    elif mode=='legacy':j.natural_geometry_only=False
    else:j.contact_leg_only=True
    old=j.state;cov=j.contacts.covariance(j.tangent()).copy()
    with pytest.raises(ValueError):
        j.update_ranges(measurement(j),anchors_m=ANCHORS,clock=CLOCK,protected_contact_sides=(0,))
    assert j.state is old
    np.testing.assert_array_equal(j.contacts.covariance(j.tangent()),cov)


def test_failed_projection_candidate_is_atomic(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.contact_raw_gain as module
    j=supported();old=j.state;cov=j.contacts.covariance(j.tangent()).copy();anchors=j.contacts.means.copy()
    def fail(*args):raise FloatingPointError('deliberate numeric rejection')
    monkeypatch.setattr(module,'project_contact_gain',fail)
    with pytest.raises(FloatingPointError):
        j.update_ranges(measurement(j),anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.,protected_contact_sides=(0,))
    assert j.state is old
    np.testing.assert_array_equal(j.contacts.covariance(j.tangent()),cov)
    np.testing.assert_array_equal(j.contacts.means,anchors)


def test_moving_support_routing_does_not_change_native_increment():
    from scipy.spatial.transform import Rotation
    j=supported();j.contacts.moving_by_side[0]=True
    j.native_dt_s=.005
    d=np.zeros((10,3));d[6]=[.001,.002,0.];d[7]=[-.001,0.,.001]
    j.native_increment=Rotation.from_rotvec(d).as_matrix()
    native=j.native_increment.copy();base=j.base.copy()
    before_velocity=j.native_motion()[0]
    assert np.linalg.norm(before_velocity[0])>1e-3
    decision=j.update_ranges(measurement(j),anchors_m=ANCHORS,clock=CLOCK,
        reference_epoch_s=0.,protected_contact_sides=(0,))
    assert decision.accepted
    np.testing.assert_array_equal(j.native_increment,native)
    np.testing.assert_array_equal(j.base,base)
    assert j.native_dt_s==.005
    # Corrected geometry may alter the calculated velocity, but routing must
    # not replace it by zero or discard the external native increments.
    assert np.linalg.norm(j.native_motion()[0][0])>1e-3
    assert j.last_contact_routing_audit['active_episode_ids']==(j.contacts._episodes[0],)


def test_rejected_raw_attempt_reports_zero_applied_shift_and_reason():
    j=supported();old=j.state
    row=replace(measurement(j),boot=CLOCK.boot_epoch+1)
    decision=j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,protected_contact_sides=(0,))
    assert not decision.accepted
    assert j.state is old
    audit=j.last_contact_routing_audit
    assert not audit['update_applied']
    assert audit['decision_reason']=='CLOCK_BOOT_UNAVAILABLE'
    assert audit['endpoint_shift_status']=='NO_UPDATE_APPLIED'
    np.testing.assert_array_equal(audit['ankle_endpoint_shift_m'],np.zeros((2,3)))
