"""Acceleration must distinguish motions with identical five IMU orientations."""
import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_sparse_nodes.inertial_window import (
    solve_window, WINDOW_CONFIG, difference_matrix,
)
from biospur_fusion.c2_sparse_nodes.calibration import calibrate_forearm_mounts,relative
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_sparse_nodes.inertial_model import orientations,STATE_SIZE

def bends(x,r):
    _,prox=orientations(x,r)
    return np.arccos(np.clip(np.sum(prox[:,0,:,2]*r[:,1,:,2],axis=1),-1,1))



def test_functional_calibration_recovers_bent_rest_pose_and_separates_two_phases():
    dt=.005;n=6000;t=np.arange(n)*dt
    mounts=Rotation.random(2,random_state=19).as_matrix()
    initial_angles=np.deg2rad([10.,20.])
    episodes={name:{} for name in ('00_initial_still','02_t_pose','06_elbow_left','07_elbow_right')}
    initial_rotations=np.tile(np.eye(3),(5,1,1))

    def rows(rotations,elapsed):
        out=np.zeros((len(rotations),11));out[:,0]=elapsed
        q=Rotation.from_matrix(rotations).as_quat();out[:,1:5]=q[:,[3,0,1,2]]
        out[1:,8:11]=Rotation.from_matrix(np.swapaxes(rotations[:-1],1,2)@rotations[1:]).as_rotvec()/dt
        return out

    for i in range(5):
        episodes['00_initial_still'][NODES[i]]={'imu':rows(np.tile(np.eye(3),(200,1,1)),np.arange(200)*dt)}
    for side in range(2):
        node=NODES[side+1];mount=mounts[side]
        start=Rotation.from_rotvec([0.,-initial_angles[side],0.]).as_matrix()@mount.T
        initial_rotations[side+1]=start
        episodes['00_initial_still'][node]={'imu':rows(np.tile(start,(200,1,1)),np.arange(200)*dt)}
        pose=Rotation.from_rotvec([(1 if side==0 else -1)*np.pi/2,0.,0.]).as_matrix()@mount.T
        episodes['02_t_pose'][node]={'imu':rows(np.tile(pose,(200,1,1)),np.arange(200)*dt+40.)}
        theta=np.where(t<15,.8+.6*np.sin(2*np.pi*t),.8)
        spin=np.where(t<15,0.,1.2*np.sin(2*np.pi*t))
        r=Rotation.from_rotvec(np.column_stack((np.zeros(n),-theta,np.zeros(n)))).as_matrix()
        r=r@Rotation.from_rotvec(np.column_stack((np.zeros((n,2)),spin))).as_matrix()@mount.T
        episodes['06_elbow_left' if side==0 else '07_elbow_right'][node]={'imu':rows(r,t+100.)}
    calibration=dict(initial_sensor_rotations=initial_rotations.tolist(),functional_yaw_rad=[0.]*5,
        pelvis_closure_rad=0.,initial_time=0.,final_time=200.,hinge_axes=[[0,-1,0]]*4)
    recovered=calibrate_forearm_mounts(episodes,calibration)
    assert np.allclose(np.deg2rad(recovered['standing_elbow_bend_estimate_deg']),initial_angles,atol=np.deg2rad(.2))
    for i in (1,2):
        audit=recovered['forearm_mount_calibration'][NODES[i]]
        assert audit['flexion']['principal_fraction']>.99
        assert audit['pronation']['principal_fraction']>.99
        assert not audit['initial_long_axis_forced_vertical']


def motion_case(upper_fraction):
    n=41;dt=.05;t=np.arange(n)*dt
    total=1.4*np.sin(np.pi*t/t[-1])**4
    upper=upper_fraction*total
    retained=np.tile(np.eye(3),(n,5,1,1))
    retained[:,1]=Rotation.from_rotvec(np.column_stack((np.zeros(n),-total,np.zeros(n)))).as_matrix()
    calibration=dict(hinge_axes=[[0,-1,0]]*2+[[0,1,0]]*2,
        lengths=dict(upper_arm=.3175,forearm=.245,thigh=.48,shank=.43,shoulder_width=.4125),
        imu_levers_from_joint_m=[[0,0,-.18]]*2+[[0,0,-.3]]*2)
    # Independent planar forward model, not the estimator's FK function.
    position=np.column_stack((.3175*np.sin(upper)+.18*np.sin(total),
        np.zeros(n),-.3175*np.cos(upper)-.18*np.cos(total)))
    acceleration=np.zeros((n,5,3))
    acceleration[1:-1,1]=difference_matrix(n,2,dt)@position
    return retained,acceleration,calibration,total-upper


def test_acceleration_resolves_elbow_vs_shoulder_motion_with_same_five_rotations():
    results=[]
    for upper_fraction in (0., .5):
        r,acc,c,truth=motion_case(upper_fraction)
        x,audit=solve_window(r,acc,c,np.zeros((len(r),STATE_SIZE)))
        assert audit['success'],audit
        rmse=np.rad2deg(np.sqrt(np.mean((bends(x,r)-truth)**2)))
        assert rmse<5.,(upper_fraction,rmse,audit)
        assert audit['after_acceleration_rms_mps2']<.05,audit
        results.append(bends(x,r))
    assert np.rad2deg(abs(results[0][20]-results[1][20]))>20.


def test_acceleration_is_not_replaced_by_a_pose_prior():
    r,acc,c,truth=motion_case(.5)
    x,_=solve_window(r,acc,c,np.zeros((len(r),STATE_SIZE)))
    ignored,_=solve_window(r,acc,c,np.zeros((len(r),STATE_SIZE)),
        config={**WINDOW_CONFIG,'acceleration_sigma_mps2':1e6})
    physics=np.mean((bends(x,r)-truth)**2)
    prior=np.mean((bends(ignored,r)-truth)**2)
    assert physics<prior*.5,(physics,prior)


def test_static_calibrated_forearm_bend_is_preserved_and_tail_is_solved():
    from biospur_fusion.c2_sparse_nodes.inertial_replay import solve_stream
    r,acc,c,_=motion_case(0.)
    n=42;r=np.tile(np.eye(3),(n,5,1,1));acc=np.zeros((n,5,3))
    for i,angle in ((1,10.),(2,20.)):
        r[:,i]=Rotation.from_euler('y',-angle,degrees=True).as_matrix()
    c.update(torso_display_models_m=[.36,.425,.49],hip_display_half_width_models_m=[.10,.14,.18])
    arrays,audit=solve_stream(np.arange(n)*.05,r,acc,np.ones(n,bool),c,
        config={**WINDOW_CONFIG,'seconds':2.})
    assert arrays['optimizer_success'].all(),audit
    assert np.allclose(arrays['bend_deg'][:,:2],[10.,20.],atol=.01)
    assert np.array_equal(arrays['retained_rotations'],r)


def test_pelvis_functional_frame_recovers_pitch_with_arbitrary_sensor_mount():
    from biospur_fusion.c2_sparse_nodes.functional_frames import signed_functional_frame
    dt=.005;t=np.arange(6000)*dt
    angle=.55*np.sin(2*np.pi*t/10.)
    body=Rotation.from_euler('y',angle[:,None]).as_matrix()
    mounting=Rotation.from_rotvec([.6,-.3,1.2]).as_matrix()
    world_gauge=Rotation.from_euler('z',.8).as_matrix()
    sensor=world_gauge@body@mounting.T
    rows=np.zeros((len(t),11));rows[:,0]=t
    q=Rotation.from_matrix(sensor).as_quat();rows[:,1:5]=q[:,[3,0,1,2]]
    rows[1:,8:]=Rotation.from_matrix(np.swapaxes(sensor[:-1],1,2)@sensor[1:]).as_rotvec()/dt
    recovered,yaw,audit=signed_functional_frame(rows,sensor[0],1.)
    estimate=Rotation.from_euler('z',yaw).as_matrix()@sensor@recovered
    assert np.max(Rotation.from_matrix(np.swapaxes(body,1,2)@estimate).magnitude())<1e-6
    assert audit['principal_fraction']>.999
