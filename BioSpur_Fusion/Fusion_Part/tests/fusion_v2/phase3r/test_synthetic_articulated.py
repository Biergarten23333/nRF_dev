import numpy as np

from biospur_fusion.imu_pose_v1 import so3
from biospur_fusion.imu_pose_v1.estimator import EstimatorConfig
from biospur_fusion.imu_pose_v1.frontend import FrontendConfig
from biospur_fusion.imu_pose_v1.metrics import synthetic_errors
from biospur_fusion.imu_pose_v1.pipeline import run_frontends,run_coupled,calibration_from_known
from biospur_fusion.imu_pose_v1.synthetic import generate
from conftest import mapping_for


def test_independent_noiseless_articulated_gate():
    d=generate(seed=2,duration_s=6,noise=False,irregular=False,gaps=False,biases=False,transients=False,outliers=False);m=mapping_for(d)
    fc=FrontendConfig(gyro_noise_rad_s_sqrt_hz=1e-7,gyro_bias_walk_rad_s2_sqrt_hz=1e-9,
        accel_bias_walk_m_s3_sqrt_hz=1e-9,accel_noise_m_s2=.001,initial_orientation_sigma_rad=1e-5,
        initial_gyro_bias_sigma_rad_s=1e-6,initial_accel_bias_sigma_m_s2=1e-6)
    f,_=run_frontends(d.samples_by_node,fc,{n:so3.inv(q) for n,q in d.q_I_S.items()})
    cfg=EstimatorConfig(measurement_floor_sigma_rad=np.deg2rad(.001),temporal_relative_sigma_rad=np.deg2rad(60),
                        hinge_orthogonal_sigma_rad=np.deg2rad(30),multi_rom_sigma=.001)
    frames,_=run_coupled(f,m,calibration_from_known(m,d.q_I_S,np.deg2rad(.001)),cfg)
    x=synthetic_errors(frames,d.truth_time_s,d.truth_q_W_S)
    assert np.rad2deg(x['orientation_rad'].max())<=.1
    assert x['bone_length_max_variation']<=1e-9


def test_noisy_normal_relative_joint_and_static_tilt_gates(synthetic_short):
    d=synthetic_short;m=mapping_for(d);f,_=run_frontends(d.samples_by_node,initial_q_WI={n:so3.inv(q) for n,q in d.q_I_S.items()})
    frames,_=run_coupled(f,m,calibration_from_known(m,d.q_I_S));x=synthetic_errors(frames,d.truth_time_s,d.truth_q_W_S)
    assert np.rad2deg(np.percentile(x['relative_joint_rad'],95))<=5
    assert np.rad2deg(np.sqrt(np.mean(x['static_tilt_rad']**2)))<=2


def test_gap_duplicate_reset_spike_saturation_remain_finite():
    d=generate(seed=8,duration_s=16,noise=True,irregular=True,gaps=True,biases=True,transients=True,outliers=True);m=mapping_for(d)
    f,_=run_frontends(d.samples_by_node,initial_q_WI={n:so3.inv(q) for n,q in d.q_I_S.items()})
    frames,e=run_coupled(f,m,calibration_from_known(m,d.q_I_S));assert frames
    assert all(np.isfinite(q).all() for frame in frames for q in frame.segment_quaternions_W_S.values())
    assert min(np.linalg.eigvalsh(e.P))>=-1e-8


def test_prefix_invariance_is_causal(synthetic_short):
    d=synthetic_short;m=mapping_for(d);cut={n:v[:350] for n,v in d.samples_by_node.items()}
    f1,_=run_frontends(cut,initial_q_WI={n:so3.inv(q) for n,q in d.q_I_S.items()});a,_=run_coupled(f1,m,calibration_from_known(m,d.q_I_S))
    f2,_=run_frontends(d.samples_by_node,initial_q_WI={n:so3.inv(q) for n,q in d.q_I_S.items()});b,_=run_coupled(f2,m,calibration_from_known(m,d.q_I_S))
    for x,y in zip(a,b):
        assert x.time_s==y.time_s
        for s in x.segment_quaternions_W_S:np.testing.assert_array_equal(x.segment_quaternions_W_S[s],y.segment_quaternions_W_S[s])
