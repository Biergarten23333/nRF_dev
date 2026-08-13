import math

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from v47_c2cc_sign_forensics import (
    angle_deg, chronological_displacements, estimate_constant_offset,
    lever_direction_bound_deg, preintegrate_endpoint_zupt,
    quaternion_active_rotate, rotation_angle_deg,
    specific_force_to_navigation_acceleration, wahba_diagnostic,
)
from v47_q1_eskf import G_MPS2


def _stroke(sign=1.0, rotation=None):
    t=np.arange(0.0,2.005,0.005);a=np.zeros((len(t),3));a[:,0]=sign*2.0*np.sin(2*np.pi*t/2.0)
    R=np.repeat(np.eye(3)[None,:,:],len(t),axis=0) if rotation is None else rotation
    specific=np.einsum("nji,nj->ni",R,a-np.array([0.0,0.0,-G_MPS2]))
    return t,a,R,specific


def test_stationary_gravity_only_is_zero_navigation_acceleration():
    assert np.allclose(specific_force_to_navigation_acceleration(np.eye(3),[0,0,G_MPS2]),0,atol=1e-12)


def test_pure_positive_translation_has_positive_displacement():
    t,a,R,specific=_stroke(1);result=preintegrate_endpoint_zupt(t,specific_force_to_navigation_acceleration(R,specific))
    assert result["zupt_displacement_m"][0] > 1.0


def test_identical_reverse_translation_has_negative_displacement():
    t,a,R,specific=_stroke(-1);result=preintegrate_endpoint_zupt(t,specific_force_to_navigation_acceleration(R,specific))
    assert result["zupt_displacement_m"][0] < -1.0


def test_forward_reverse_chronology_closes_and_alternates():
    d=chronological_displacements([[0,0,0],[1,0,0],[0,0,0]])
    assert d[0]@d[1] < 0 and np.allclose(d.sum(axis=0),0)


def test_simultaneous_translation_and_rotation_uses_active_sensor_to_navigation_map():
    t=np.arange(0,2.005,.005);R=np.asarray([Rotation.from_euler("y",50*x/2,degrees=True).as_matrix() for x in t])
    _,a,_,specific=_stroke(1,R);result=preintegrate_endpoint_zupt(t,specific_force_to_navigation_acceleration(R,specific))
    assert result["zupt_displacement_m"][0] > 1.0


def test_arbitrary_initial_yaw_is_resolved_by_signed_axes():
    truth=Rotation.from_euler("z",137,degrees=True).as_matrix();source=np.eye(3);target=(truth@source.T).T
    fit=wahba_diagnostic(source,target);assert rotation_angle_deg(fit["proper"],truth)<1e-8


def test_known_ninety_degree_remount_is_recovered_independently():
    A=Rotation.from_euler("xyz",[10,20,30],degrees=True).as_matrix();B=A@Rotation.from_euler("x",90,degrees=True).as_matrix()
    assert abs(rotation_angle_deg(A,B)-90)<1e-8
    assert rotation_angle_deg(wahba_diagnostic(np.eye(3),(B@np.eye(3).T).T)["proper"],B)<1e-8


def test_gravity_plus_known_gyro_bias_does_not_change_specific_force_sign():
    bias=np.radians([.2,-.3,.1]);measured=bias.copy();assert np.allclose(measured-bias,0)
    assert np.allclose(specific_force_to_navigation_acceleration(np.eye(3),[0,0,G_MPS2]),0)


def test_known_constant_offset_sign_is_recovered():
    t=np.arange(0,4,.005);reference=np.sin(2*np.pi*.7*t);shift=.03
    # Samples labelled late by +shift need -shift added to their timestamps.
    result=estimate_constant_offset(t,reference,t+shift,reference,.08,.005)
    assert result["offset_added_to_shifted_s"] == pytest.approx(-shift,abs=1e-12)


def test_reflection_is_exposed_and_proper_solution_cannot_hide_it():
    source=np.eye(3);target=(np.diag([1,1,-1])@source.T).T;fit=wahba_diagnostic(source,target)
    assert fit["orthogonal_metrics"]["determinant"] < 0
    assert fit["proper_metrics"]["determinant"] > 0
    assert fit["proper_metrics"]["p95_deg"] > 80


def test_lever_arm_rotation_bound_is_zero_without_rotation_and_nonzero_with_rotation():
    assert lever_direction_bound_deg([1,0,0],np.eye(3),np.eye(3),.05)==0
    moved=Rotation.from_euler("z",60,degrees=True).as_matrix()
    assert 0 < lever_direction_bound_deg([1,0,0],np.eye(3),moved,.05) < 5


def test_row_column_and_quaternion_inverse_mutations_fail_analytic_vector():
    R=Rotation.from_euler("xyz",[25,-35,70],degrees=True);q_xyzw=R.as_quat();q_wxyz=np.r_[q_xyzw[3],q_xyzw[:3]];v=np.array([.3,-.4,.8])
    truth=R.as_matrix()@v
    assert angle_deg(quaternion_active_rotate(q_wxyz,v),truth)<1e-5
    assert angle_deg(R.as_matrix().T@v,truth)>20
    assert angle_deg(quaternion_active_rotate(q_xyzw,v),truth)>20
