"""Conditional native tilt, not independent gravity observations or tuning."""
from dataclasses import replace
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_root_world.articulated_contact import ArticulatedContactFilter
from biospur_fusion.c2_uwb_root_world.articulated_joint_filter import SEGMENT_HEADING_GROUP
from biospur_fusion.c2_uwb_root_world.contact_raw_gain import grouped_heading_constraints,project_augmented_gain
from biospur_fusion.c2_uwb_root_world.support_velocity import SupportVelocityConfig
from biospur_fusion.c2_uwb_root_world.root_input_safety import CausalImuHold
from test_c2_articulated_contact import owner,ankles
from test_c2_contact_raw_gain import constraint
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at


def heading_owner(**policies):
    old=owner()
    rotations=Rotation.from_rotvec(np.random.default_rng(3).normal(size=(10,3))*.3).as_matrix()
    return ArticulatedContactFilter(old.state.root,rotations,geometry=old.geometry,hinges={},
        pelvis_mount_sensor_from_segment=Rotation.from_rotvec([.2,-.3,.1]).as_matrix(),
        embedding=np.eye(3),wear_yaw=np.eye(3),chest_vertical_m=.08,
        natural_geometry_only=True,conditional_imu_heading=True,**policies)


def invariant(before,after):
    np.testing.assert_allclose(after[:,2,:],before[:,2,:],atol=2e-13)
    delta=after@before.swapaxes(-1,-2)
    for group in range(6):
        ids=np.flatnonzero(SEGMENT_HEADING_GROUP==group)
        for i in ids:
            np.testing.assert_allclose(delta[i],delta[ids[0]],atol=2e-13)
        if len(ids)==2:
            a,b=ids
            np.testing.assert_allclose(after[a].T@after[b],before[a].T@before[b],atol=2e-13)


@pytest.mark.parametrize('policy',[dict(tilt_restoration=True),dict(contact_leg_only=True)])
def test_incompatible_policy_rejected(policy):
    with pytest.raises(ValueError,match='conditional IMU heading'):heading_owner(**policy)


@pytest.mark.parametrize('sides',[(),(0,),(0,1)])
def test_raw_heading_exact_finite_invariants_and_supported_linear_constraints(sides):
    j=heading_owner();j.update_contact(ankles(j),[True,True],[False,False],[1,1],.005,[True,True])
    old=j.state;anchors=j.contacts.means.copy()
    row=replace(row_at(0.,old.root.position_m+j.tags()['BSFEC35']+[.03,.02,0.]),node='BSFEC35')
    d=j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.,
        protected_contact_sides=sides,consider_position=True,preserve_anchor_mean=True)
    assert d.accepted
    assert np.linalg.norm(j.last_orientation_delta)>1e-8
    invariant(old.rotations,j.state.rotations)
    np.testing.assert_allclose(j.contacts.means,anchors,atol=1e-13)
    if sides:assert j.last_contact_routing_audit['linear_endpoint_correction_max_m']<1e-12
    else:np.testing.assert_allclose(j.state.root.position_m,old.root.position_m,atol=1e-13)
    assert np.linalg.norm(j.contacts.cross)>0
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_contact_velocity_and_native_use_same_heading_policy():
    j=heading_owner();j.update_contact(ankles(j),[True,True],[False,False],[1,1],.005,[False,False])
    j.propagate_safe(CausalImuHold(0,[0,0,9.80665],j.sensor_rotation()),.005)
    first_native=j.base@Rotation.from_rotvec(np.random.default_rng(7).normal(size=(10,3))*.02).as_matrix()
    j.observe_imu_base(.005,first_native)
    j.contacts.means+=np.tile([.025,-.01,.005],2)
    before=j.state.rotations.copy()
    j.update_contact(ankles(j),[True,True],[True,True],[1,1],.005,[False,False])
    invariant(before,j.state.rotations)
    assert np.linalg.norm(before-j.state.rotations)>1e-8
    prior_corrected=j.state.rotations.copy();prior_base=j.base.copy()
    j.propagate_safe(CausalImuHold(.005,[0,0,9.80665],j.sensor_rotation()),.010)
    native=prior_base@Rotation.from_rotvec(np.random.default_rng(5).normal(size=(10,3))*.02).as_matrix()
    j.observe_imu_base(.010,native)
    np.testing.assert_allclose(j.state.rotations,prior_corrected@prior_base.swapaxes(-1,-2)@native,atol=1e-13)
    # Accumulated common group yaw remains conditional on CURRENT native tilt.
    invariant(native,j.state.rotations)
    before=j.state.rotations.copy();rootpos=j.state.root.position_m.copy();anchors=j.contacts.means.copy()
    j.update_stationary_velocity([True,True],[1,1],.005,SupportVelocityConfig())
    invariant(before,j.state.rotations)
    assert np.linalg.norm(before-j.state.rotations)>1e-8
    np.testing.assert_allclose(j.state.root.position_m,rootpos,atol=1e-13)
    np.testing.assert_allclose(j.contacts.means,anchors,atol=1e-13)
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_combined_constraints_actual_joseph_and_reset(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.contact_raw_gain as module
    from biospur_fusion.c2_uwb_root_world.tight_range import linearize_raw_range_factors,RawRangeUpdateConfig
    from biospur_fusion.c2_uwb_calibration.articulated_range import _so3_right_jacobian
    j=heading_owner();j.update_contact(ankles(j),[True,True],[False,False],[1,1],.005,[False,False])
    old=j.state;p=j.contacts.covariance(j.tangent());row=replace(row_at(0.,old.root.position_m+j.tags()['BSFEC35']+[.03,.01,0]),node='BSFEC35')
    f=linearize_raw_range_factors(old.root,row,anchors_m=ANCHORS,clock=CLOCK,
        tag_offset_world_m=j.tags()[row.node],reference_epoch_s=0.,config=RawRangeUpdateConfig(),_enforce_geometry=False)
    h=np.zeros((len(f.anchors),len(p)));h[:,:9]=f.state_jacobian;h[:,9:39]=h[:,:3]@j.tag_jacobian(row.node)
    noise=np.diag(np.diag(f.r_prior_m2)/f.robust_weights)
    original=np.linalg.solve(h@p@h.T+noise,h@p).T
    c=np.vstack((grouped_heading_constraints(old.rotations),constraint(j,(0,1))))
    gain,_=project_augmented_gain(original,p,c,tuple(range(39,len(p))))
    np.testing.assert_allclose(c@gain[:39],0.,atol=1e-12)
    np.testing.assert_allclose(gain[39:],0.,atol=1e-12)
    error=gain@f.innovations_m;a=np.eye(len(p))-gain@h
    expected=a@p@a.T+gain@noise@gain.T
    reset=np.eye(len(p))
    for i in range(10):reset[9+3*i:12+3*i,9+3*i:12+3*i]=_so3_right_jacobian(error[9+3*i:12+3*i])
    expected=reset@expected@reset.T
    calls=[];real=module.project_contact_gain
    def observed(*args):calls.append(args[2].copy());return real(*args)
    monkeypatch.setattr(module,'project_contact_gain',observed)
    assert j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.,protected_contact_sides=(0,1)).accepted
    assert len(calls)==1
    np.testing.assert_allclose(j.contacts.covariance(j.tangent()),expected,atol=2e-12)


def test_support_requires_active_episode_and_release_does_not_authorize_raw():
    j=heading_owner()
    row=replace(row_at(0.,j.state.root.position_m+j.tags()['BSFEC35']),node='BSFEC35')
    with pytest.raises(ValueError,match='active support'):j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,protected_contact_sides=(0,))
    j.update_contact(ankles(j),[True,False],[False,False],[1,1],.005,[True,False])
    episode=j.contacts._episodes[0]
    j.update_contact(ankles(j),[False,False],[False,False],[1,1],.005,[False,False])
    with pytest.raises(ValueError,match='active support'):j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,protected_contact_sides=(0,))
    j.update_contact(ankles(j),[True,False],[False,False],[1,1],.005,[True,False])
    assert j.contacts._episodes[0]!=episode


def test_default_equals_explicit_disabled_across_all_routes():
    default=owner()
    explicit=ArticulatedContactFilter(default.state.root,default.base.copy(),geometry=default.geometry,
        hinges={},pelvis_mount_sensor_from_segment=default.mount.copy(),embedding=default.embedding.copy(),
        wear_yaw=default.wear_yaw.copy(),chest_vertical_m=.08,conditional_imu_heading=False)
    for j in (default,explicit):
        j.update_contact(ankles(j),[True,True],[False,False],[1,1],.005,[False,False])
        row=replace(row_at(0.,j.state.root.position_m+j.tags()['BSFEC35']+[.02,.01,0]),node='BSFEC35')
        assert j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.,consider_position=True).accepted
        j.propagate_safe(CausalImuHold(0,[0,0,9.80665],j.sensor_rotation()),.005)
        j.observe_imu_base(.005,j.base@Rotation.from_rotvec(np.tile([.01,.02,.03],(10,1))).as_matrix())
        j.update_stationary_velocity([True,True],[1,1],.005,SupportVelocityConfig())
        j.update_contact(ankles(j),[True,True],[True,True],[1,1],.005,[False,False])
    np.testing.assert_array_equal(default.state.rotations,explicit.state.rotations)
    np.testing.assert_array_equal(default.state.root.vector,explicit.state.root.vector)
    np.testing.assert_array_equal(default.contacts.covariance(default.tangent()),explicit.contacts.covariance(explicit.tangent()))


def test_non_natural_mode_rejected():
    old=owner()
    with pytest.raises(ValueError,match='conditional IMU heading'):
        ArticulatedContactFilter(old.state.root,old.base,geometry=old.geometry,hinges={},
            pelvis_mount_sensor_from_segment=old.mount,embedding=old.embedding,wear_yaw=old.wear_yaw,
            chest_vertical_m=.08,conditional_imu_heading=True)


@pytest.mark.parametrize('row',[True,-1,39,1.5,'1'])
def test_invalid_consider_rows_rejected(row):
    with pytest.raises(ValueError,match='consider rows'):
        project_augmented_gain(np.ones((39,2)),np.eye(39),np.eye(39)[:1],(row,))


@pytest.mark.parametrize('bad',[np.zeros((10,3,3)),np.tile(np.diag([-1.,1.,1.]),(10,1,1)),np.full((10,3,3),np.nan)])
def test_invalid_heading_rotations_rejected(bad):
    with pytest.raises(ValueError,match='proper rotations'):grouped_heading_constraints(bad)


def test_support_constraint_hook_combines_boolean_mask_before_joseph(monkeypatch):
    import biospur_fusion.c2_uwb_root_world.contact_raw_gain as module
    j=heading_owner();j.update_contact(ankles(j),[True,True],[False,False],[1,1],.005,[False,False])
    j.propagate_safe(CausalImuHold(0,[0,0,9.80665],j.sensor_rotation()),.005)
    c=grouped_heading_constraints(j.state.rotations);mask=np.ones(39,bool);mask[:3]=False
    jac=j.point_jacobians();h=np.zeros((2,3,39));h[:,:,:3]=np.eye(3)
    for i,name in enumerate(('ankle_left','ankle_right')):h[i,:,9:]=jac[name]
    calls=[];real=module.project_contact_gain
    def checked(gain,p,constraints):
        result,audit=real(gain,p,constraints);calls.append(result.copy())
        np.testing.assert_allclose(c@result[:39],0.,atol=1e-12)
        np.testing.assert_allclose(result[:3],0.,atol=1e-12)
        return result,audit
    monkeypatch.setattr(module,'project_contact_gain',checked)
    old=j.tangent()
    new=j.contacts.update(old,ankles(j)+[.01,.02,0], [True,True],[True,True],[1,1],.005,
        [False,False],point_jacobians=h,base_gain_row_mask=mask,base_gain_constraints=c)
    assert len(calls)==1
    np.testing.assert_allclose(new.vector[:3],old.vector[:3],atol=1e-13)
    np.testing.assert_allclose(c@(new.vector-old.vector),0.,atol=1e-12)
    np.linalg.cholesky(j.contacts.covariance(new))
