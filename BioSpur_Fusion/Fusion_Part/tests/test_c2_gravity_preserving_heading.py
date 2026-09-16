from dataclasses import replace
import numpy as np
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_uwb_root_world.articulated_joint_filter import GravityPreservingHeadingFilter,SEGMENT_HEADING_GROUP
from test_c2_articulated_joint_filter import owner
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at


def heading_owner():
    old=owner()
    base=Rotation.from_rotvec(np.tile([.3,-.15,.2],(10,1))).as_matrix()
    return GravityPreservingHeadingFilter(old.state.root,base,geometry=old.geometry,
        hinges={},pelvis_mount_sensor_from_segment=np.eye(3))


def test_gravity_and_hinge_invariants_with_large_heading_and_base_motion():
    j=heading_owner();j.heading_rad=np.array([.3,-.8,1.2,-1.5,.4,-.3])
    base=Rotation.from_rotvec(np.arange(30).reshape(10,3)*.01).as_matrix()
    j.observe_imu_base(0.,base);r=j.state.rotations
    np.testing.assert_allclose(r.swapaxes(1,2)@np.array([0.,0.,1.]),base.swapaxes(1,2)@np.array([0.,0.,1.]),atol=3e-16)
    for p,c in [(2,3),(4,5),(6,7),(8,9)]:
        np.testing.assert_allclose(r[p].T@r[c],base[p].T@base[c],atol=7e-16)


def test_range_heading_jacobian_matches_finite_difference():
    j=heading_owner();error=np.r_[np.zeros(9),[.2,-.3,.1,.2,-.1,.3]]
    points,jac=j._range_points_jacobian(j.state,error,'wrist_left');eps=1e-7
    numeric=np.zeros((3,6))
    for i in range(6):
        changed=error.copy();changed[9+i]+=eps
        new,_=j._range_points_jacobian(j.state,changed,'wrist_left')
        numeric[:,i]=(new['wrist_left']-points['wrist_left'])/eps
    np.testing.assert_allclose(jac,numeric,atol=2e-8,rtol=0)


def test_nonroot_heading_feedback_persists_without_gravity_tilt_change():
    j=heading_owner();j.propagate(1.,[0,0,9.80665])
    before=j.state.rotations.copy();target=j.state.root.position_m+j.points()['wrist_left']+[.2,-.1,0]
    decision=j.update_ranges(replace(row_at(1.,target),node='BSFEC35'),anchors_m=ANCHORS,clock=CLOCK)
    assert decision.accepted and np.linalg.norm(j.heading_rad)>1e-5
    heading=j.heading_rad.copy();j.propagate(1.005,[0,0,9.80665]);j.observe_imu_base(1.005,j.base)
    np.testing.assert_array_equal(j.heading_rad,heading)
    np.testing.assert_allclose(j.state.rotations.swapaxes(1,2)@np.array([0.,0.,1.]),before.swapaxes(1,2)@np.array([0.,0.,1.]),atol=4e-16)
    assert np.linalg.eigvalsh(j.state.covariance).min()>0


def test_no_ranges_keeps_exact_base_rotations_and_zero_heading():
    j=heading_owner()
    for tick in range(1,20):
        j.propagate(tick*.005,[0,0,9.80665]);base=Rotation.from_rotvec(np.tile([.1,.2,tick*.01],(10,1))).as_matrix()
        j.observe_imu_base(tick*.005,base)
        np.testing.assert_array_equal(j.state.rotations,base)
        np.testing.assert_array_equal(j.heading_rad,np.zeros(6))


def test_existing_nuisance_variance_enters_effective_noise_not_sensor_noise():
    from biospur_fusion.c2_uwb_root_world.tight_range import PersistentRangeBiasTracker
    j=heading_owner();j.propagate(1.,[0,0,9.80665])
    row=row_at(1.,j.state.root.position_m)
    prior=PersistentRangeBiasTracker().prior_snapshot(row.node,snapshot_time_s=.99)
    decision=j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,bias_prior=prior)
    assert decision.accepted
    np.testing.assert_allclose(decision.sigma_m**2-decision.sensor_sigma_m**2,np.full(8,.09),atol=1e-15)
