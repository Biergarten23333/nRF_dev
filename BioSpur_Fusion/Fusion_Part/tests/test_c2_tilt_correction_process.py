import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_root_world.tilt_correction_process import restore_tilt
from biospur_fusion.c2_uwb_root_world.articulated_joint_filter import ArticulatedJointState
from biospur_fusion.c2_uwb_root_world.support_velocity import SupportVelocityConfig
from biospur_fusion.c2_uwb_root_world.root_input_safety import CausalImuHold
from test_c2_articulated_contact import owner,ankles
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at
from dataclasses import replace


def test_pure_world_yaw_and_zero_error():
    base=Rotation.from_rotvec([[.2,-.4,.1],[-.4,.1,.3]]).as_matrix()
    rotation=Rotation.from_euler('z',.7).as_matrix()@base
    result,_,_=restore_tilt(base,rotation,.2)
    np.testing.assert_allclose(result,rotation,atol=1e-14)
    result,_,_=restore_tilt(base,base,.2)
    np.testing.assert_allclose(result,base,atol=1e-14)


def test_finite_signed_right_jacobian():
    base=Rotation.from_rotvec([[.2,-.4,.1]]).as_matrix()
    rotation=base@Rotation.from_rotvec([[.5,-.25,.3]]).as_matrix()
    nominal,jac,_=restore_tilt(base,rotation,.02)
    for axis in range(3):
        e=np.eye(3)[axis]*1e-6
        plus=restore_tilt(base,rotation@Rotation.from_rotvec(e).as_matrix(),.02)[0]
        minus=restore_tilt(base,rotation@Rotation.from_rotvec(-e).as_matrix(),.02)[0]
        numeric=(Rotation.from_matrix(nominal.swapaxes(1,2)@plus).as_rotvec()-
                 Rotation.from_matrix(nominal.swapaxes(1,2)@minus).as_rotvec())/2e-6
        np.testing.assert_allclose(numeric[0],jac[0,:,axis],atol=2e-9)


def test_cadence_ou_covariance_semigroup_and_tilt_decay():
    base=np.eye(3)[None];rotation=Rotation.from_rotvec([[.3,.2,.1]]).as_matrix()
    one=restore_tilt(base,rotation,.04)
    first=restore_tilt(base,rotation,.015)
    second=restore_tilt(base,first[0],.025)
    np.testing.assert_allclose(second[0],one[0],atol=1e-14)
    np.testing.assert_allclose(second[1]@first[1],one[1],atol=1e-13)
    np.testing.assert_allclose(second[1]@first[2]@second[1].swapaxes(1,2)+second[2],one[2],atol=1e-13)
    delta=Rotation.from_matrix(one[0]).as_rotvec()[0]
    np.testing.assert_allclose(delta,[.3*np.exp(-.04/.125),.2*np.exp(-.04/.125),.1])


def test_native_motion_excludes_restoration_and_matches_pose_derivative():
    j=owner();j.tilt_restoration=True
    old=j.state;rot=old.rotations@Rotation.from_rotvec([.2,0,0]).as_matrix()
    j.state=ArticulatedJointState(old.root,rot,old.covariance)
    j.propagate_safe(CausalImuHold(0,[0,0,9.80665],np.eye(3)),.005)
    j.observe_imu_base(.005,j.base)
    velocity,_,_=j.native_motion();np.testing.assert_allclose(velocity,0,atol=1e-12)
    j.native_increment[6]=Rotation.from_rotvec([.01,.02,0]).as_matrix()
    original=j.state;_,derivative,_=j.native_motion()
    for axis in range(3):
        values=[]
        for sign in (-1,1):
            r=original.rotations.copy();r[6]=r[6]@Rotation.from_rotvec(np.eye(3)[axis]*sign*1e-6).as_matrix()
            j.state=ArticulatedJointState(original.root,r,original.covariance);values.append(j.native_motion()[0])
        np.testing.assert_allclose((values[1]-values[0])/2e-6,derivative[:,:,18+axis],atol=2e-7)
    j.state=original


def test_joint_contact_cross_psd_and_no_double_native_noise():
    j=owner();j.tilt_restoration=True
    j.update_contact(ankles(j),[True,True],[False,False],[1,1],.005,[False,False])
    p=j.state.covariance.copy();cross=j.contacts.cross.copy()
    j.propagate_safe(CausalImuHold(0,[0,0,9.80665],np.eye(3)),.005)
    np.testing.assert_array_equal(j.state.covariance[9:,9:],p[9:,9:])
    j.observe_imu_base(.005,j.base)
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))
    assert not np.array_equal(j.contacts.cross,cross)
    p=j.state.covariance.copy();j.observe_imu_base(.005,j.base)
    np.testing.assert_allclose(j.state.covariance,p,atol=1e-14)


def test_stationary_velocity_and_raw_preserve_root_p_and_anchor_means():
    j=owner();j.update_contact(ankles(j),[True,True],[False,False],[1,1],.005,[False,False])
    j.native_dt_s=.005;j.native_increment[6]=Rotation.from_rotvec([.001,0,0]).as_matrix()
    old=j.state.root.position_m.copy();anchors=j.contacts.means.copy()
    innovation,noise=j.update_stationary_velocity([True,False],[1,1],.005,SupportVelocityConfig())
    assert np.linalg.norm(innovation)>0
    np.testing.assert_array_equal(j.state.root.position_m,old)
    np.testing.assert_array_equal(j.contacts.means,anchors)
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))
    row=replace(row_at(0,j.state.root.position_m+j.tags()['BSFEC35']+[.03,0,0]),node='BSFEC35')
    decision=j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0,
        consider_position=True,preserve_anchor_mean=True)
    assert decision.accepted
    np.testing.assert_array_equal(j.state.root.position_m,old)
    np.testing.assert_array_equal(j.contacts.means,anchors)
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_native_velocity_defers_projection_to_final_contact(monkeypatch):
    from build_c2_full_session_ten_node_ab import _hinges
    j=owner();j.hinges=_hinges();j.tilt_restoration=True
    j.native_dt_s=.005;j.native_increment[6]=Rotation.from_rotvec([.001,0,0]).as_matrix()
    calls=[];original=type(j).project_hinges
    def counted(self):
        calls.append(self.state.root.time_s)
        return original(self)
    monkeypatch.setattr(type(j),'project_hinges',counted)
    j.update_stationary_velocity([True,False],[1,1],.005,SupportVelocityConfig())
    assert calls==[]
    j.update_contact(ankles(j),[False,False],[False,False],[1,1],.005,[False,False])
    assert len(calls)==1
    assert j.last_projection['post_projection_all_inside_rom']
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))


def test_disabled_process_is_exact_and_release_restores_raw_position_gain():
    j=owner();other=owner();other.tilt_restoration=False
    for candidate in (j,other):
        candidate.propagate_safe(CausalImuHold(0,[0,0,9.80665],np.eye(3)),.005)
        candidate.observe_imu_base(.005,candidate.base)
    np.testing.assert_array_equal(j.state.covariance,other.state.covariance)
    np.testing.assert_array_equal(j.state.rotations,other.state.rotations)
    old=j.state.root.position_m.copy()
    row=replace(row_at(.005,old+j.tags()['BSFEC35']+[.03,0,0]),node='BSFEC35')
    decision=j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=.005,
        consider_position=False,preserve_anchor_mean=True)
    assert decision.accepted
    assert np.linalg.norm(j.state.root.position_m-old)>1e-6


def test_natural_geometry_tilt_process_preserves_native_base_and_zero_motion():
    """Restoring estimation error must not manufacture an IMU motion input."""
    j=owner();j.natural_geometry_only=True;j.tilt_restoration=True
    base=j.base.copy();old=j.state
    j.state=ArticulatedJointState(old.root,
        Rotation.from_rotvec([.08,-.04,0.]).as_matrix()@old.rotations,
        old.covariance)
    j.propagate_safe(CausalImuHold(0,[0,0,9.80665],np.eye(3)),.005)
    j.observe_imu_base(.005,base)
    np.testing.assert_array_equal(j.base,base)
    velocity,_,tags=j.native_motion()
    np.testing.assert_allclose(velocity,0.,atol=1e-12)
    for value in tags.values():np.testing.assert_allclose(value,0.,atol=1e-12)
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))
    assert j._natural_geometry()[2]['physical_rotations_modified'] is False
