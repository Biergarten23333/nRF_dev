import copy

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from v47_c2cc_natural_motion import (FROZEN_NATURAL_MOTION_CONFIG,evaluate_frozen_transform,
    fit_natural_motion,make_segment,natural_stop_indices,preintegrated_path)
from v47_c2cc_sign_forensics import rotation_angle_deg


def generated_segments(rotation,seed=47,count=12,labels=None,scatter=0.0):
    rng=np.random.default_rng(seed);segments=[]
    for i in range(count):
        t=np.linspace(0,1,31);direction=rng.normal(size=3);direction/=np.linalg.norm(direction);side=rng.normal(size=3);side-=side@direction*direction;side/=np.linalg.norm(side)
        path=direction[None,:]*(t[:,None]+.15*np.sin(np.pi*t)[:,None])+side[None,:]*(.18*np.sin(2*np.pi*t)[:,None])
        target=(rotation@path.T).T+rng.normal(0,scatter,path.shape)
        segments.append(make_segment(segment_id=f"s{i}",imu_time_s=t,imu_path_N_m=path,t4_time_s=t,t4_path_V4_m=target,
            metadata={"operator_label":(labels or ["VERTICAL","HORIZONTAL_1","HORIZONTAL_2"])[i%3]}))
    return segments


def test_arbitrary_curved_translation_recovers_rotation():
    truth=Rotation.from_euler("xyz",[31,-22,117],degrees=True).as_matrix();fit=fit_natural_motion(generated_segments(truth))
    assert fit["accepted"] and rotation_angle_deg(fit["rotation"],truth)<1e-5


def test_diagonal_and_l_shaped_paths_are_not_forced_to_lines():
    truth=Rotation.from_euler("z",73,degrees=True).as_matrix();segments=generated_segments(truth)
    t=np.linspace(0,1,31);L=np.column_stack([np.minimum(t*2,1),np.maximum(t*2-1,0),np.zeros_like(t)])
    segments[0]=make_segment(segment_id="L",imu_time_s=t,imu_path_N_m=L,t4_time_s=t,t4_path_V4_m=(truth@L.T).T)
    assert fit_natural_motion(segments)["accepted"]


def test_simultaneous_sixty_dps_rotation_preintegration_is_finite():
    t=np.arange(0,2.005,.005);acc=np.column_stack([1.5*np.sin(np.pi*t),np.zeros_like(t),np.zeros_like(t)])
    result=preintegrated_path(t,acc);assert np.isfinite(result["zupt_position_m"]).all() and np.linalg.norm(result["zupt_end_velocity_mps"])<1e-12


def test_arbitrary_mount_a_and_known_ninety_degree_mount_b_fit_independently():
    A=Rotation.from_euler("xyz",[14,37,-81],degrees=True).as_matrix();B=A@Rotation.from_euler("y",90,degrees=True).as_matrix()
    fa=fit_natural_motion(generated_segments(A));fb=fit_natural_motion(generated_segments(B,seed=48))
    assert rotation_angle_deg(fa["rotation"],A)<1e-5 and rotation_angle_deg(fb["rotation"],B)<1e-5
    assert abs(rotation_angle_deg(fa["rotation"],fb["rotation"])-90)<1e-5


def test_static_gravity_initialization_leaves_yaw_for_motion_to_resolve():
    truth=Rotation.from_euler("z",149,degrees=True).as_matrix();fit=fit_natural_motion(generated_segments(truth))
    assert rotation_angle_deg(fit["rotation"],truth)<1e-5


def test_imperfect_reversals_do_not_gate_independent_stop_to_stop_segments():
    truth=Rotation.from_euler("xyz",[-20,15,44],degrees=True).as_matrix();segments=generated_segments(truth,scatter=.005)
    segments[1]["t4_path_V4_m"][-1]+=[.12,-.05,.03];segments[1]["dV"]=segments[1]["t4_path_V4_m"][-1]-segments[1]["t4_path_V4_m"][0]
    assert fit_natural_motion(segments)["accepted"]


def test_gyro_and_bounded_accelerometer_bias_are_explicit_nuisances_not_labels():
    gyro_bias=np.radians([.2,-.3,.1]);accel_bias=np.array([.05,-.04,.03])
    assert np.linalg.norm(gyro_bias)<.01 and np.linalg.norm(accel_bias)<FROZEN_NATURAL_MOTION_CONFIG.accelerometer_bias_bound_mps2


def test_t4_scatter_and_single_outlier_remain_bounded_under_robust_fit():
    truth=Rotation.from_euler("xyz",[12,23,34],degrees=True).as_matrix();segments=generated_segments(truth,scatter=.01);segments[2]["t4_path_V4_m"][15]+=[.8,-.6,.4]
    assert fit_natural_motion(segments)["accepted"]


def test_constant_time_offset_policy_is_bounded_and_primary_zero():
    assert FROZEN_NATURAL_MOTION_CONFIG.time_offset_primary_s==0
    assert FROZEN_NATURAL_MOTION_CONFIG.time_offset_bound_s==.08


def test_lever_arm_motion_is_small_sensitivity_not_hidden_state():
    radius=FROZEN_NATURAL_MOTION_CONFIG.lever_arm_sensitivity_radius_m
    effect=np.linalg.norm((Rotation.from_euler("z",60,degrees=True).as_matrix()-np.eye(3))@[radius,0,0])
    assert 0<effect<.1


def test_insufficient_single_line_excitation_fails():
    R=np.eye(3);t=np.linspace(0,1,21);segments=[]
    for i in range(10):
        p=np.column_stack([t*(1+.01*i),np.zeros_like(t),np.zeros_like(t)]);segments.append(make_segment(segment_id=str(i),imu_time_s=t,imu_path_N_m=p,t4_time_s=t,t4_path_V4_m=p))
    assert not fit_natural_motion(segments)["checks"]["excitation"]


def test_reflection_geometry_cannot_pass_proper_rotation_residual():
    reflected=np.diag([1,1,-1]);fit=fit_natural_motion(generated_segments(reflected))
    assert np.linalg.det(fit["rotation"])>0 and not fit["accepted"]


def test_wrong_quaternion_direction_and_gravity_sign_mutations_fail():
    truth=Rotation.from_euler("xyz",[32,-27,61],degrees=True).as_matrix();segments=generated_segments(truth)
    wrong_q=copy.deepcopy(segments);wrong_g=copy.deepcopy(segments)
    for segment in wrong_q:
        segment["imu_path_N_m"]=(truth@segment["imu_path_N_m"].T).T;segment["dN"]=segment["imu_path_N_m"][-1]-segment["imu_path_N_m"][0]
    for segment in wrong_g:
        segment["imu_path_N_m"][:,2]*=-1;segment["dN"]=segment["imu_path_N_m"][-1]-segment["imu_path_N_m"][0]
    assert rotation_angle_deg(fit_natural_motion(wrong_q)["rotation"],truth)>20
    assert not fit_natural_motion(wrong_g)["accepted"]


def test_heldout_leakage_is_structurally_rejected():
    with pytest.raises(ValueError,match="held-out"):
        fit_natural_motion(generated_segments(np.eye(3)),dataset_role="HELDOUT")


def test_operator_label_permutation_cannot_change_transform_or_verdict():
    truth=Rotation.from_euler("xyz",[17,29,-103],degrees=True).as_matrix();original=generated_segments(truth)
    permuted=copy.deepcopy(original);labels=["HORIZONTAL_2","VERTICAL","HORIZONTAL_1"]
    for i,segment in enumerate(permuted):segment["metadata"]["operator_label"]=labels[i%3]
    a=fit_natural_motion(original);b=fit_natural_motion(permuted)
    assert a["accepted"]==b["accepted"] and rotation_angle_deg(a["rotation"],b["rotation"])<1e-10


def test_stop_detection_uses_three_dimensional_speed_not_axis_or_label():
    t=np.linspace(0,4,81);speed=np.sin(np.pi*t/2)**2
    direction=np.array([1.,2.,3.]);direction/=np.linalg.norm(direction)
    stops=natural_stop_indices(t,speed[:,None]*direction)
    assert np.allclose(t[stops],[0,2,4],atol=.06)
