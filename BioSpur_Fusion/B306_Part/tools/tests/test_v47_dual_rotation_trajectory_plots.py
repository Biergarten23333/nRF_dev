import numpy as np

from plot_v47_dual_rotation_trajectories import (
    NOMINAL_S, deterministic_basis, project, robust_orbit_fit,
)


def synthetic_orbit(radius_mm=500.0, rpm=9.0, duration_s=120.0):
    time=np.arange(0,duration_s,.02);angle=time*rpm*2*np.pi/60
    center=np.array([2500.,1600.,-1300.]);u=np.array([1.,0.,0.]);v=np.array([0.,.98,.199])
    v/=np.linalg.norm(v);xyz=center+radius_mm*(np.cos(angle)[:,None]*u+np.sin(angle)[:,None]*v)
    return time,xyz,center


def test_nominal_interval_is_registered_common_bound():
    assert NOMINAL_S == 7.283928561*3600


def test_deterministic_basis_is_right_handed_and_sign_fixed():
    u,v,n=deterministic_basis([.1,-.2,-.97])
    assert n[2]>0
    assert np.allclose(np.c_[u,v,n].T@np.c_[u,v,n],np.eye(3),atol=1e-12)
    assert np.allclose(np.cross(u,v),n,atol=1e-12)


def test_robust_orbit_recovers_radius_rpm_and_full_residuals():
    time,xyz,center=synthetic_orbit();xyz[100]=[5000,5000,5000]
    fit=robust_orbit_fit(time,xyz)
    assert fit["valid_points"]==len(xyz)
    assert fit["fit_retained_points"]==int(np.sum(np.linalg.norm(xyz-np.median(xyz,axis=0),axis=1)<=np.quantile(np.linalg.norm(xyz-np.median(xyz,axis=0),axis=1),.99)))
    assert np.isclose(fit["radius_mm"],500,rtol=0,atol=2)
    assert np.isclose(fit["apparent_rpm"],9,rtol=0,atol=.02)
    assert fit["radial_residual_rms_mm"]>0


def test_common_origin_projection_preserves_relative_translation():
    points=np.array([[1.,2.,3.],[4.,5.,6.]])
    u,v,_=deterministic_basis([0,0,1]);origin=np.array([10.,20.,30.])
    projected=project(points,origin,u,v)
    assert np.allclose(projected[1]-projected[0],[3,3])
