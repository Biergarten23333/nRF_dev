import numpy as np

from biospur_fusion.imu_pose_v1 import so3
from biospur_fusion.imu_pose_v1.frontend import ErrorStateImuFrontend,FrontendConfig
from biospur_fusion.imu_pose_v1.types import ImuSample


def static_run(enable=True,gap=False,cfg=None):
    bg=np.array([.018,-.012,.009]);ba=np.array([.12,-.08,.06]);cfg=cfg or FrontendConfig(enable_gyro_bias_estimation=enable,enable_accel_bias_estimation=enable)
    f=ErrorStateImuFrontend('BSF0001',cfg);outs=[];t=0
    rng=np.random.default_rng(2)
    for i in range(1000):
        dt=.005+rng.uniform(-.0007,.0007);t+=dt
        if gap and i==600:t+=.22
        gyro=bg+rng.normal(0,.001,3);acc=np.array([0,0,9.80665])+ba+rng.normal(0,.015,3)
        outs.append(f.update(ImuSample('BSF0001',t,int(t*1e6),i,gyro,acc,0,0,True)))
    return f,outs,bg,ba


def test_gyro_and_accel_bias_really_update_and_converge():
    f,_,bg,ba=static_run(True);assert f.factor_counts['gyro_bias_update']>0 and f.factor_counts['accel_bias_update']>0
    assert np.linalg.norm(f.bg-bg)<.003
    # At one static attitude only the gravity-parallel accelerometer-bias mode
    # is separable; horizontal components remain finite/prior-dominated.
    assert abs(f.ba[2]-ba[2])<.02 and np.isfinite(f.ba).all() and np.max(np.diag(f.P)[6:])>0
    assert f.bias_update_norm['gyro']>0 and f.bias_update_norm['accel']>0


def test_bias_off_worsens_orientation_or_innovation():
    on,o1,_,_=static_run(True);off,o0,_,_=static_run(False)
    e1=np.nanmean([x.innovation_norm for x in o1]);e0=np.nanmean([x.innovation_norm for x in o0])
    assert e0>e1*1.2 or so3.geodesic(off.q_WI,[1,0,0,0])>so3.geodesic(on.q_WI,[1,0,0,0])
    np.testing.assert_array_equal(off.bg,np.zeros(3));np.testing.assert_array_equal(off.ba,np.zeros(3))


def test_gap_increases_covariance_against_matched_no_gap():
    _,a,_,_=static_run(True,False);_,b,_,_=static_run(True,True)
    assert np.trace(b[601].covariance[:3,:3])>np.trace(a[601].covariance[:3,:3])
    assert min(np.linalg.eigvalsh(b[-1].covariance))>=-1e-10


def test_low_dynamic_without_external_marker_cannot_update_gyro_bias():
    f=ErrorStateImuFrontend('BSF0001')
    for i in range(300):f.update(ImuSample('BSF0001',i*.005,i*5000,i,np.array([.02,0,0]),np.array([0,0,9.80665]),0,0,False))
    assert f.factor_counts['gyro_bias_update']==0


def test_individual_bias_ablations_are_not_silent():
    on,o,_,_=static_run()
    no_bg,ob,_,_=static_run(cfg=FrontendConfig(enable_gyro_bias_estimation=False))
    no_ba,oa,_,_=static_run(cfg=FrontendConfig(enable_accel_bias_estimation=False))
    assert np.linalg.norm(no_bg.bg)==0 and np.linalg.norm(on.bg)>0
    assert np.linalg.norm(no_ba.ba)==0 and np.linalg.norm(on.ba)>0
    assert np.nanmean([x.innovation_norm for x in oa])>np.nanmean([x.innovation_norm for x in o])
