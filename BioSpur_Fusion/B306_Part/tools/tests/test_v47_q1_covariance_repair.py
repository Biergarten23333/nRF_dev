import math

import numpy as np
import pytest

from v47_q1_covariance_reference import compose_discrete_map, scale_aware_psd, van_loan_reference
from v47_q1_eskf import (ERROR_STATE_SIZE, FrameBinding, Q1T4ESKF,
    discretize_attitude_only, quaternion_exp, quaternion_to_matrix, skew)


def dynamics(omega=(0.,0.,0.), spatial=False):
    F=np.zeros((15,15));G=np.zeros((15,12));omega=np.asarray(omega,float)
    if spatial:
        F[:3,3:6]=np.eye(3);F[3:6,6:9]=-skew([0,0,9.80665]);F[3:6,9:12]=-np.eye(3);G[3:6,:3]=-np.eye(3)
    F[6:9,6:9]=-skew(omega);F[6:9,12:15]=-np.eye(3);G[6:9,3:6]=-np.eye(3);G[9:12,6:9]=np.eye(3);G[12:15,9:12]=np.eye(3)
    density=np.diag(np.r_[np.full(3,.12**2),np.full(3,math.radians(.12)**2),np.full(3,.002**2),np.full(3,math.radians(.002)**2)])
    return F,G@density@G.T


def long_case(omega=(0.,0.,0.)):
    F,L=dynamics(omega);phi,qd=discretize_attitude_only(F,L,.05);a,q=compose_discrete_map(phi,qd,24*3600*20)
    P0=np.diag(np.r_[np.full(6,.01),np.full(2,math.radians(5)**2),math.pi**2,np.full(3,.01),np.full(3,math.radians(.2)**2)])
    P=a@P0@a.T+q;return P,scale_aware_psd(P)


def initialized(binding=FrameBinding()):
    f=Q1T4ESKF(binding=binding);f.initialize_from_stationary([0,0,9.80665],[0,0,0]);return f


def test_01_stationary_level_24h():
    P,s=long_case();assert np.isfinite(P).all() and not s["materially_negative"] and s["cholesky_success"]


def test_02_arbitrary_fixed_orientation_24h():
    P,s=long_case();q=quaternion_exp([.4,-.2,.7]);assert abs(np.linalg.norm(q)-1)<1e-14 and not s["materially_negative"]


@pytest.mark.parametrize("axis",range(3))
@pytest.mark.parametrize("sign",(-1,1))
def test_03_constant_9rpm_each_axis_24h(axis,sign):
    omega=np.zeros(3);omega[axis]=sign*9*2*math.pi/60;P,s=long_case(omega);assert np.isfinite(P).all() and not s["materially_negative"] and s["cholesky_success"]


def test_04_constant_rotation_with_gyro_bias():
    P,s=long_case([0,0,.94]);assert not s["materially_negative"] and s["max_eigenvalue"]<1e6


def test_05_stationary_accelerometer_bias_isolated_when_unbound():
    f=initialized();p=f.p.copy();v=f.v.copy()
    for i in range(201):f.propagate(i*.005,[.2,-.1,9.9],[0,0,0])
    assert np.array_equal(f.p,p) and np.array_equal(f.v,v) and np.allclose(f.P[:6,6:],0)


def test_06_yaw_unobservable_allowed_to_grow():
    P,s=long_case();assert P[8,8]>=math.pi**2 and not s["materially_negative"]


def test_07_t4_bounds_position():
    b=FrameBinding(R_V4_N=np.eye(3),origin_V4_m=np.zeros(3),provenance="synthetic",v4_navigation_rotation_valid=True)
    f=initialized(b);f.p[:]=[2,-1,.5];before=np.trace(f.P[:3,:3]);f.t4_position_update([0,0,0]);assert np.trace(f.P[:3,:3])<before


def test_08_zupt_bounds_velocity():
    f=initialized();f.v[:]=[1,2,3];before=np.trace(f.P[3:6,3:6]);f.zupt_update();assert np.trace(f.P[3:6,3:6])<before


def test_09_gravity_bounds_tilt():
    f=initialized();before=np.trace(f.P[6:8,6:8]);f.gravity_update([0,0,9.80665]);assert np.trace(f.P[6:8,6:8])<before


def test_10_disabled_frame_coupling_matches_nominal_and_error():
    f=initialized();f.propagate(0,[1,0,9.80665],[0,0,0]);f.propagate(.005,[1,0,9.80665],[0,0,0]);assert np.array_equal(f.p,np.zeros(3)) and not np.any(f.P[:6,6:])


def test_11_full_synthetic_frame_bound_coupling():
    b=FrameBinding(R_V4_N=np.eye(3),origin_V4_m=np.zeros(3),provenance="synthetic",v4_navigation_rotation_valid=True,signed_axes_valid=True)
    f=initialized(b)
    for i in range(201):f.propagate(i*.005,[1,0,9.80665],[0,0,0])
    assert f.p[0]==pytest.approx(.5,abs=.01) and scale_aware_psd(f.P)["cholesky_success"]


def test_12_realistic_timestamp_jitter():
    f=initialized();rng=np.random.default_rng(47);t=0
    for _ in range(10000):t+=.005+rng.uniform(-.0002,.0002);f.propagate(t,[0,0,9.80665],[0,0,.94])
    assert not scale_aware_psd(f.P)["materially_negative"]


def test_13_occasional_large_valid_dt():
    f=initialized();t=0
    for i in range(1000):t+=.05 if i%200==0 else .005;f.propagate(t,[0,0,9.80665],[0,0,.94])
    assert np.isfinite(f.P).all()


def test_14_measurement_injection_reset_cycles():
    f=initialized()
    for _ in range(200):f.gravity_update([.001,-.001,9.80665]);f.zupt_update()
    s=scale_aware_psd(f.P);assert not s["materially_negative"] and s["cholesky_success"]


def test_15_quaternion_sign_equivalent_covariance():
    q=quaternion_exp([.2,-.3,.4]);assert np.array_equal(quaternion_to_matrix(q),quaternion_to_matrix(-q))


def test_16_state_scale_stress():
    P,s=long_case([.94,-.3,.2]);P[:3,:3]*=1e6;s=scale_aware_psd(P);assert not s["materially_negative"]


def test_17_float64_condition_stress_is_scale_aware():
    P=np.diag(np.geomspace(1e-12,1e12,15));s=scale_aware_psd(P);assert not s["materially_negative"] and s["condition"]>1e20


def test_18_reference_vs_production_discretization():
    F,L=dynamics([.2,-.3,.94]);p_phi,p_q=discretize_attitude_only(F,L,.05);r_phi,r_q=van_loan_reference(F,L,.05)
    assert np.max(np.abs(p_phi-r_phi))<1e-12 and np.max(np.abs(p_q-r_q))<1e-15
