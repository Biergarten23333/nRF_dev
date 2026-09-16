from dataclasses import replace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_root_world.articulated_joint_filter import ArticulatedJointFilter
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.root_r3.models import RootState
from biospur_fusion.root_r3.estimator import propagate_inertial,RootFilterConfig
from test_c2_articulated_range import _geometry
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at


def owner(hinges=None):
    root=RootState(0.,np.r_[[2.,1.3,1.],np.zeros(6)],np.diag([.01]*9))
    return ArticulatedJointFilter(root,np.tile(np.eye(3),(10,1,1)),geometry=_geometry(),
        hinges=hinges or {},pelvis_mount_sensor_from_segment=np.eye(3))


def test_no_uwb_preserves_base_inertial_mean_under_rotating_input():
    joint=owner(); root=joint.state.root; previous=np.tile(np.eye(3),(10,1,1))
    for tick in range(1,21):
        t=tick*.005; force=np.array([.01,0,9.80665])
        root,_=propagate_inertial(root,t,force,previous[0],RootFilterConfig())
        joint.propagate(t,force)
        current=Rotation.from_rotvec(np.tile([0,0,.01*t],(10,1))).as_matrix()
        joint.observe_imu_base(t,current)
        np.testing.assert_allclose(joint.state.root.vector,root.vector,atol=2e-14,rtol=0)
        np.testing.assert_allclose(joint.state.rotations,current,atol=1e-14,rtol=0)
        previous=current


def test_nonroot_range_feedback_persists_into_next_imu_and_range_prediction():
    joint=owner(); joint.propagate(1.,[0,0,9.80665])
    old=joint.state.rotations.copy(); before=joint.points()['wrist_left'].copy()
    target=joint.state.root.position_m+before+[.12,0,0]
    row=replace(row_at(1.,target),node='BSFEC35')
    decision=joint.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK)
    assert decision.accepted
    assert np.linalg.norm(joint.last_orientation_delta)>1e-5
    corrected=joint.state.rotations.copy()
    assert np.linalg.norm(joint.points()['wrist_left']-before)>1e-5
    delta=Rotation.from_rotvec(np.tile([.02,-.01,.03],(10,1))).as_matrix()
    joint.propagate(1.005,[0,0,9.80665]); joint.observe_imu_base(1.005,delta)
    np.testing.assert_allclose(joint.state.rotations,corrected@delta,atol=1e-12)
    assert not np.allclose(joint.state.rotations,old@delta)
    assert np.linalg.norm(joint.state.covariance[:9,9:])>0


def test_joint_covariance_counts_one_measurement_and_reset_once():
    joint=owner(); joint.propagate(1.,[0,0,9.80665])
    prior=joint.state.covariance.copy()
    # Pelvis tag at zero offset has no direct orientation columns.
    row=row_at(1.,joint.state.root.position_m)
    decision=joint.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK)
    assert decision.accepted
    h=np.zeros((8,39)); h[:,:3]=(joint.state.root.position_m-ANCHORS)/np.linalg.norm(joint.state.root.position_m-ANCHORS,axis=1)[:,None]
    r=np.diag(decision.sigma_m**2/decision.robust_weights)
    k=np.linalg.solve(h@prior@h.T+r,h@prior).T; ikh=np.eye(39)-k@h
    expected=ikh@prior@ikh.T+k@r@k.T
    # Range quantization creates a small reset; root block is unaffected by it.
    np.testing.assert_allclose(joint.state.covariance[:9,:9],expected[:9,:9],atol=1e-8,rtol=1e-5)


def test_matrix_transport_is_quaternion_sign_invariant_and_rejects_future_base():
    a,b=owner(),owner(); q=Rotation.from_rotvec([.02,.03,.04]).as_quat()
    a.observe_imu_base(0.,np.tile(Rotation.from_quat(q).as_matrix(),(10,1,1)))
    b.observe_imu_base(0.,np.tile(Rotation.from_quat(-q).as_matrix(),(10,1,1)))
    np.testing.assert_array_equal(a.state.rotations,b.state.rotations)
    with pytest.raises(ValueError): a.observe_imu_base(.1,a.base)
    with pytest.raises(ValueError): a.propagate(-.1,[0,0,9.8])


def test_existing_hinge_projection_changes_state_and_pushes_covariance():
    from biospur_fusion.c2_articulated_biomechanics.model import HingeJoint
    hinge=HingeJoint('elbow_left','upper_arm_left','forearm_left','test',
        (1.,0.,0.),(1.,0.,0.),(0.,0.,0.,1.),1.,0.,150.,1,1)
    joint=owner({'elbow_left':hinge})
    prior=joint.state.covariance.copy(); joint.project_hinges()
    assert joint.last_projection['post_projection_all_inside_rom']
    assert not np.allclose(prior,joint.state.covariance)
    assert np.linalg.eigvalsh(joint.state.covariance).min()>0
    points=joint.points()
    assert np.linalg.norm(points['wrist_left']-points['elbow_left'])==pytest.approx(.25)


def test_nonidentity_mount_force_jacobian_and_finite_injection_reset():
    from biospur_fusion.c2_uwb_calibration.articulated_range import _skew,_so3_right_jacobian
    r=Rotation.from_rotvec([.3,-.2,.4]).as_matrix()
    mount=Rotation.from_rotvec([-.4,.1,.2]).as_matrix()
    force=np.array([.2,-.3,9.7]); eps=1e-7
    analytic=-r@_skew(mount.T@force)
    measured=np.column_stack([(r@Rotation.from_rotvec(np.eye(3)[i]*eps).as_matrix()@mount.T@force-r@mount.T@force)/eps for i in range(3)])
    np.testing.assert_allclose(measured,analytic,atol=6e-7,rtol=0)
    delta=np.array([.3,-.2,.1]); nominal=Rotation.from_rotvec(delta)
    reset=np.column_stack([(nominal.inv()*Rotation.from_rotvec(delta+np.eye(3)[i]*eps)).as_rotvec()/eps for i in range(3)])
    np.testing.assert_allclose(reset,_so3_right_jacobian(delta),atol=1e-8,rtol=0)
