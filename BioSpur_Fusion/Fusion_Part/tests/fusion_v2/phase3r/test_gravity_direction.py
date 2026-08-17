import numpy as np

from biospur_fusion.imu_pose_v1 import so3
from biospur_fusion.imu_pose_v1.frontend import ErrorStateImuFrontend
from biospur_fusion.imu_pose_v1.types import ImuSample


G=9.80665


def sample(i,acc,gyro=np.zeros(3),rest=True):
    return ImuSample("BSF0001",i*.005,i*5000,i,np.asarray(gyro,float),np.asarray(acc,float),0,0,rest)


def gravity_angle(q, measured):
    predicted=so3.matrix(q).T@np.array([0.,0.,1.]);measured=np.asarray(measured)/np.linalg.norm(measured)
    return np.arccos(np.clip(np.dot(predicted,measured),-1,1))


def run_error(deg,axis=(1,0,0),acc=(0,0,G)):
    f=ErrorStateImuFrontend("BSF0001");f.initialized=True;f.last_time=0;f.last_boot=0
    f.q_WI=so3.exp(np.deg2rad(deg)*np.asarray(axis,float));f.P[:3,:3]=np.eye(3)*.4
    before=gravity_angle(f.q_WI,acc)
    for i in range(1,151):f.update(sample(i,acc))
    return before,gravity_angle(f.q_WI,acc)


def test_manual_golden_vector_and_sign():
    q=so3.exp(np.deg2rad([45,0,0]));pred=so3.matrix(q).T@np.array([0,0,G])
    np.testing.assert_allclose(pred,[0,G/np.sqrt(2),G/np.sqrt(2)],atol=1e-12)
    before,after=run_error(45);assert after < before*.15


def test_plus_minus_five_and_forty_five_converge():
    for deg in (-45,-5,5,45):
        before,after=run_error(deg);assert after < before


def test_random_3d_tilt_converges_independent_oracle():
    rng=np.random.default_rng(4)
    for _ in range(12):
        axis=rng.normal(size=3);axis-=np.dot(axis,[0,0,1])*np.array([0,0,1]);axis/=np.linalg.norm(axis)
        before,after=run_error(rng.uniform(-40,40),axis);assert after < before


def test_realistic_plus_y_install_and_axis_permutation():
    q=so3.from_two_vectors([0,1,0],[0,0,1]);f=ErrorStateImuFrontend("BSF0001")
    f.initialized=True;f.last_time=0;f.last_boot=0;f.q_WI=q
    for i in range(1,100):f.update(sample(i,[0,G,0]))
    assert gravity_angle(f.q_WI,[0,1,0]) < np.deg2rad(.2)
    for acc in ([G,0,0],[-G,0,0],[0,-G,0]):
        q=so3.from_two_vectors(np.asarray(acc)/G,[0,0,1]);assert gravity_angle(q,acc)<1e-7


def test_antipodal_and_transient_do_not_positive_feedback():
    before,after=run_error(30,(0,1,0),(0,0,G));assert after<before
    f=ErrorStateImuFrontend("BSF0001");
    for i in range(1,80):f.update(sample(i,[0,0,G],gyro=[.01,-.02,.005]))
    q0=f.q_WI.copy()
    for i in range(80,100):f.update(sample(i,[3.5,0,G],rest=False))
    assert so3.geodesic(q0,f.q_WI)<np.deg2rad(8)


def test_reversed_jacobian_sign_mutation_increases_error():
    q=so3.exp(np.deg2rad([20,0,0]));p=so3.matrix(q).T@np.array([0,0,G]);y=np.array([0,0,G])-p
    H=so3.skew(p);dx=np.linalg.solve(H.T@H+np.eye(3)*1e-3,H.T@y)
    good=so3.apply_right(q,dx);bad=so3.apply_right(q,-dx)
    assert gravity_angle(good,[0,0,1]) < gravity_angle(q,[0,0,1]) < gravity_angle(bad,[0,0,1])


def test_gravity_off_ablation_cannot_correct_tilt():
    from biospur_fusion.imu_pose_v1.frontend import FrontendConfig
    f=ErrorStateImuFrontend("BSF0001",FrontendConfig(enable_gravity_update=False));f.initialized=True;f.last_time=0;f.last_boot=0
    f.q_WI=so3.exp(np.deg2rad([25,0,0]));before=gravity_angle(f.q_WI,[0,0,G])
    for i in range(1,151):f.update(sample(i,[0,0,G]))
    assert abs(gravity_angle(f.q_WI,[0,0,G])-before)<np.deg2rad(.1)
