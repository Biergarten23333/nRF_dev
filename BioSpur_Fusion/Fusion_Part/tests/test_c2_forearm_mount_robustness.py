"""Synthetic mounting checks; no recordings, inferred parent IMU or model assets."""
import copy

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_sparse_nodes.calibration import (
    _gyro_axis_mask, _orthogonal_forearm_axes, calibrate_forearm_mounts, matrices,
)
from biospur_fusion.c2_sparse_nodes.inputs import NODES


def rows(time, rotation, gyro=None):
    result=np.zeros((len(time),11))
    result[:,0]=time
    result[:,1:5]=Rotation.from_matrix(rotation).as_quat()[:,[3,0,1,2]]
    if gyro is not None:result[:,8:11]=gyro
    return result


def fixture(*, missing=False):
    time=np.r_[np.arange(0,14.5,.05),np.arange(16,30,.005)] if missing else np.arange(0,30,.005)
    first=time<15
    mounts=Rotation.from_rotvec([[.31,-.16,.27],[-.22,.37,-.13]]).as_matrix()
    rest_bends=np.array([.17,.36])
    episodes={name:{} for name in ('00_initial_still','02_t_pose','06_elbow_left','07_elbow_right')}
    initial=np.tile(np.eye(3),(5,1,1))
    static_time=np.arange(200)*.005
    for node in NODES:
        episodes['00_initial_still'][node]={'imu':rows(static_time,np.tile(np.eye(3),(200,1,1)))}
    for side,mount in enumerate(mounts):
        node=NODES[side+1]
        initial[side+1]=Rotation.from_rotvec([0,-rest_bends[side],0]).as_matrix()@mount.T
        episodes['00_initial_still'][node]={'imu':rows(static_time,np.tile(initial[side+1],(200,1,1)))}
        tpose=Rotation.from_rotvec([(1 if side==0 else -1)*np.pi/2,0,0]).as_matrix()@mount.T
        episodes['02_t_pose'][node]={'imu':rows(static_time+40,np.tile(tpose,(200,1,1)))}
        theta=np.where(first,.75+.35*np.sin(2*np.pi*time/5),.75)
        spin=np.where(first,0,.8*np.sin(2*np.pi*(time-15)/5))
        rotation=Rotation.from_rotvec(np.column_stack([time*0,-theta,time*0])).as_matrix()
        rotation=rotation@Rotation.from_rotvec(np.column_stack([time*0,time*0,spin])).as_matrix()
        # An unobserved transition changes the world heading, not mounting.
        # Missing samples make the old first-3000-rows sign window span phases.
        if missing:
            yaw=np.where(first,0,np.pi)
            rotation=Rotation.from_rotvec(np.column_stack([time*0,time*0,yaw])).as_matrix()@rotation
        gyro=np.column_stack([time*0,np.where(first,-.35*2*np.pi/5*np.cos(2*np.pi*time/5),0),
                              np.where(first,0,.8*2*np.pi/5*np.cos(2*np.pi*(time-15)/5))])@mount.T
        episodes['06_elbow_left' if side==0 else '07_elbow_right'][node]={'imu':rows(time+100,rotation@mount.T,gyro)}
    calibration=dict(initial_sensor_rotations=initial.tolist(),functional_yaw_rad=[0.]*5,
                     pelvis_closure_rad=0.,initial_time=0.,final_time=200.,hinge_axes=[[0,-1,0]]*4)
    return episodes,calibration,mounts,rest_bends


def test_missing_samples_do_not_extend_sign_selection_into_second_phase():
    episodes,calibration,mounts,_=fixture(missing=True)
    recovered=calibrate_forearm_mounts(episodes,calibration)
    for side,mount in enumerate(mounts):
        node=NODES[side+1]
        data=episodes['06_elbow_left' if side==0 else '07_elbow_right'][node]['imu']
        expected=mount@np.array([0.,-1.,0.])
        # Independent old-window counterexample: it would reverse the known hinge.
        assert np.mean((matrices(data[:3000]).as_matrix()@expected)[:,1])>0
        audit=recovered['forearm_mount_calibration'][node]
        np.testing.assert_allclose(audit['axis_sensor_hinge'],expected,atol=1e-12)
        support=audit['hinge_sign_support']
        mask=_gyro_axis_mask(data,0,15)
        assert support['rows']==audit['flexion']['rows']==int(mask.sum())
        assert support['selected_elapsed_span_s'][1]<15
        assert support['mean_absolute_lateral']>.99
        assert support['acceptance_gate_added'] is False


@pytest.mark.parametrize('hinge,long', [([0,0,1],[0,0,1]),([0,0,-1],[0,0,1]),
    ([0,0,0],[0,0,1]),([0,1,0],[0,0,0]),([0,1,0],[np.nan,0,1])])
def test_numerically_degenerate_axes_fail_before_division(hinge,long):
    with np.errstate(divide='raise',invalid='raise'):
        with pytest.raises(ValueError,match='degenerate|collinear'):
            _orthogonal_forearm_axes(hinge,long)


def test_same_motion_in_both_registered_phases_fails_as_unobservable_mount():
    episodes,calibration,_,_=fixture()
    data=episodes['06_elbow_left'][NODES[1]]['imu']
    data[:,8:11]=[0,0,1]
    with np.errstate(divide='raise',invalid='raise'):
        with pytest.raises(ValueError,match='numerically collinear'):
            calibrate_forearm_mounts(episodes,calibration)


def test_normal_asymmetric_mounts_and_bent_rest_are_preserved():
    episodes,calibration,mounts,bends=fixture()
    saved=copy.deepcopy(episodes)
    recovered=calibrate_forearm_mounts(episodes,calibration)
    np.testing.assert_allclose(np.deg2rad(recovered['standing_elbow_bend_estimate_deg']),bends,atol=1e-12)
    for i,mount in enumerate(mounts,1):
        np.testing.assert_allclose(recovered['segment_axes_in_sensor'][i],mount,atol=1e-12)
        evidence=recovered['forearm_mount_calibration'][NODES[i]]['axis_conditioning']
        np.testing.assert_allclose(evidence['axis_pair_condition_number'],1.,atol=1e-12)
        assert evidence['human_angle_acceptance_gate_added'] is False
        support=recovered['forearm_mount_calibration'][NODES[i]]['longitudinal_sign_support']
        np.testing.assert_allclose(support['absolute_up_projection'],np.cos(bends[i-1]),atol=1e-12)
        assert support['acceptance_gate_added'] is False
    for name,nodes in episodes.items():
        for node,data in nodes.items():np.testing.assert_array_equal(data['imu'],saved[name][node]['imu'])


def test_oblique_finite_axes_report_conditioning_without_human_angle_gate():
    for angle in (.001,.15,.6):
        hinge=np.array([np.sin(angle),0,np.cos(angle)])
        h,long,evidence=_orthogonal_forearm_axes(hinge,[0,0,1])
        np.testing.assert_allclose(h,[1,0,0],atol=1e-10)
        np.testing.assert_allclose(h@long,0.,atol=1e-10)
        assert np.isfinite(evidence['axis_pair_condition_number'])
        np.testing.assert_allclose(evidence['orthogonalization_sine'],np.sin(angle),atol=1e-12)


def test_measured_obliquity_is_preserved_without_changing_mount_or_claiming_anatomy():
    episodes,calibration,mounts,_=fixture()
    angle=.14
    for side,mount in enumerate(mounts):
        q=episodes['06_elbow_left' if side==0 else '07_elbow_right'][NODES[side+1]]['imu']
        first=q[:,0]-q[0,0]<15
        sensor_omega=q[first,8:11]@mount
        sensor_omega[:,2]=sensor_omega[:,1]*np.tan(angle)
        q[first,8:11]=sensor_omega@mount.T
    recovered=calibrate_forearm_mounts(episodes,calibration)
    for i,mount in enumerate(mounts,1):
        np.testing.assert_allclose(recovered['segment_axes_in_sensor'][i],mount,atol=1e-12)
        e=recovered['forearm_mount_calibration'][NODES[i]]
        raw=np.asarray(e['measured_axis_sensor_hinge']);long=np.asarray(e['axis_sensor_long'])
        assert abs(raw@long)>.1
        np.testing.assert_allclose(np.asarray(e['axis_sensor_hinge'])@long,0,atol=1e-12)
        meta=e['functional_axis_obliquity']
        np.testing.assert_allclose(abs(meta['signed_departure_from_orthogonal_deg']),np.rad2deg(angle),atol=1e-10)
        assert not meta['anatomical_carrying_angle_identified']
        assert not meta['applied_to_joint_model']
