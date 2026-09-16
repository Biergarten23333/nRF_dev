from dataclasses import replace
import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_uwb_root_world.articulated_contact import ArticulatedContactFilter
from biospur_fusion.c2_uwb_root_world.articulated_joint_filter import ArticulatedJointState
from biospur_fusion.c2_uwb_root_world.root_input_safety import CausalImuHold,inherited_raw_nis_limit
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.c2_uwb_calibration.articulated_range import _skew
from biospur_fusion.root_r3.models import RootState
from test_c2_articulated_range import _geometry
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at


def owner():
    root=RootState(0.,np.r_[[2.,1.3,1.],np.zeros(6)],np.eye(9)*.01)
    return ArticulatedContactFilter(root,np.tile(np.eye(3),(10,1,1)),geometry=_geometry(),
        hinges={},pelvis_mount_sensor_from_segment=np.eye(3),embedding=np.diag([-1.,1.,1.]),
        wear_yaw=Rotation.from_euler('z',.4).as_matrix(),chest_vertical_m=.08)


def ankles(j):return np.stack([j.points()['ankle_left'],j.points()['ankle_right']])


def test_signed_mirrored_point_jacobian_and_physical_rotation():
    j=owner();old=j.state;eps=1e-6
    for segment in (0,1,6,7):
        for axis in range(3):
            numeric={}
            for sign in (-1,1):
                r=old.rotations.copy();delta=np.zeros(3);delta[axis]=sign*eps
                r[segment]=r[segment]@Rotation.from_rotvec(delta).as_matrix()
                j.state=ArticulatedJointState(old.root,r,old.covariance)
                numeric[sign]=j.tags()
                assert np.linalg.det(j.sensor_rotation())>0
                np.testing.assert_allclose(j.sensor_rotation(),j.wear_yaw@r[0])
            j.state=old
            for node in ('BSF6C53','BSF31CC'):
                np.testing.assert_allclose((numeric[1][node]-numeric[-1][node])/(2*eps),
                    j.tag_jacobian(node)[:,3*segment+axis],atol=1e-8)


def test_articulated_birth_full_cross_and_release_no_root_reset():
    j=owner();j.update_contact(ankles(j),[True,True],[True,True],[1,1],.005,[False,False])
    p=j.contacts.covariance(j.tangent());assert p.shape==(45,45)
    assert np.linalg.norm(j.contacts.cross[9:])>0
    np.linalg.cholesky(p)
    old=j.state;oldroot=old.root.vector.copy()
    j.update_contact(ankles(j),[False,False],[False,False],[1,1],.005,[False,False])
    np.testing.assert_array_equal(j.state.root.vector,oldroot)
    np.testing.assert_allclose(j.state.covariance,old.covariance,atol=1e-14)


def test_contact_changes_joint_attitude_and_keeps_bones():
    j=owner();offset=ankles(j);j.update_contact(offset,[True,False],[False,False],[1,1],.005,[False,False])
    j.propagate_safe(CausalImuHold(0,[0,0,9.80665],np.eye(3)),.005)
    base=j.base.copy();base[6]=Rotation.from_rotvec([.05,0,0]).as_matrix()
    j.observe_imu_base(.005,base)
    offset=ankles(j)
    before=j.state.rotations.copy()
    j.update_contact(offset,[True,False],[True,False],[1,1],.005,[False,False])
    assert np.linalg.norm(j.state.rotations-before)>1e-6
    p=j.points();np.testing.assert_allclose(np.linalg.norm(p['ankle_left']-p['knee_left']),j.geometry.segment_length_m['shank_left'])
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_raw_updates_pose_and_anchor_with_one_full_prior():
    j=owner();hold=CausalImuHold(0,[0,0,9.80665],np.eye(3));j.propagate_safe(hold,1.)
    j.update_contact(ankles(j),[True,False],[False,False],[1,1],.005,[False,False])
    row=replace(row_at(1.,j.state.root.position_m+j.tags()['BSFEC35']+[.03,0,0]),node='BSFEC35')
    anchor=j.contacts.means.copy();decision=j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=1.)
    assert decision.accepted
    assert np.linalg.norm(j.last_orientation_delta)>1e-6
    assert np.linalg.norm(j.contacts.means-anchor)>1e-7
    assert j.last_nis_limit==inherited_raw_nis_limit(len(decision.anchors))
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_raw_failed_candidate_is_atomic(monkeypatch):
    j=owner();j.update_contact(ankles(j),[True,False],[False,False],[1,1],.005,[False,False])
    row=replace(row_at(0.,j.state.root.position_m+j.tags()['BSFEC35']),node='BSFEC35')
    old=j.state;p=j.contacts.covariance(j.tangent()).copy();a=j.contacts.means.copy()
    def fail(*args):raise RuntimeError('projection failure')
    monkeypatch.setattr(ArticulatedContactFilter,'_inject_range_error',fail)
    with pytest.raises(RuntimeError):j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.)
    assert j.state is old
    np.testing.assert_array_equal(j.contacts.covariance(j.tangent()),p)
    np.testing.assert_array_equal(j.contacts.means,a)


@pytest.mark.parametrize('node', ['BSF31CC','BSFAA61','BSF1120','BSFEC35',
                                  'BSFB165','BSF44AD','BSF3C79','BSF6C53','BSF8BC4'])
def test_each_nonroot_tag_updates_body_with_position_and_anchor_protection(node):
    j=owner()
    j.update_contact(ankles(j),[True,True],[True,True],[1,1],.005,[False,False])
    before_root=j.state.root.position_m.copy()
    before_anchors=j.contacts.means.copy()
    row=replace(row_at(0.,before_root+j.tags()[node]+[.02,.01,0]),node=node)
    decision=j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.,
                             consider_position=True,preserve_anchor_mean=True)
    assert decision.accepted
    assert np.linalg.norm(j.last_orientation_delta)>1e-6
    np.testing.assert_array_equal(j.state.root.position_m,before_root)
    np.testing.assert_array_equal(j.contacts.means,before_anchors)
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_force_derivative_nonidentity_mount_not_mirrored():
    j=owner();j.mount=Rotation.from_rotvec([.2,-.1,.3]).as_matrix()
    force=np.array([.6,-.2,9.7]);r=j.state.rotations[0]
    analytic=-j.wear_yaw@r@_skew(j.mount.T@force)
    for axis in range(3):
        d=np.zeros(3);d[axis]=1e-6
        a=j.wear_yaw@r@Rotation.from_rotvec(d).as_matrix()@j.mount.T@force
        b=j.wear_yaw@r@Rotation.from_rotvec(-d).as_matrix()@j.mount.T@force
        np.testing.assert_allclose((a-b)/2e-6,analytic[:,axis],atol=1e-8)


@pytest.mark.parametrize('contact',[False,True])
def test_native_transaction_projects_even_without_contact(contact):
    from biospur_fusion.c2_articulated_biomechanics.model import HingeJoint
    j=owner();j.hinges={'elbow_left':HingeJoint('elbow_left','upper_arm_left','forearm_left','test',
        (1.,0.,0.),(1.,0.,0.),(0.,0.,0.,1.),1.,0.,150.,1,1)}
    j.propagate_safe(CausalImuHold(0,[0,0,9.80665],np.eye(3)),.005)
    base=j.base.copy();base[3]=Rotation.from_rotvec([-.3,.05,.02]).as_matrix()
    j.observe_imu_base(.005,base)
    j.update_contact(ankles(j),[contact,False],[False,False],[1,1],.005,[False,False])
    assert j.last_projection['post_projection_all_inside_rom']
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_fk_cache_invalidates_on_injection():
    j=owner();before=j.tags()['BSFEC35'].copy();assert j.tags() is j.tags()
    error=np.zeros(39);error[9+3*2]=.1
    j._inject_range_error(j.state,error,j.state.covariance)
    assert np.linalg.norm(j.tags()['BSFEC35']-before)>1e-4
