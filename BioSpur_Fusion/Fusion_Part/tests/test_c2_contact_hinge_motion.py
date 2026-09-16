import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_calibration.contact_hinge_motion import (
    ContactHingeMotionConfig, _HingeKinematics, solve_contact_hinge_motion,
)
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS, corrected_proxy_points
from biospur_fusion.c2_articulated_biomechanics.model import HingeJoint
from test_c2_articulated_range import _geometry


def fixture(bend=.08):
    base={s:np.eye(3) for s in SEGMENTS}
    model={}
    for side in ('left','right'):
        base['shank_'+side]=Rotation.from_rotvec([bend,0.,0.]).as_matrix()
        model['knee_'+side]=HingeJoint('knee_'+side,'thigh_'+side,'shank_'+side,
            'fixture',(1.,0.,0.),(1.,0.,0.),(0.,0.,0.,1.),1.,0.,150.,1,1)
    points=corrected_proxy_points(base,{s:np.zeros(3) for s in SEGMENTS},_geometry())
    return base,model,points


def solve(**overrides):
    base,model,points=fixture()
    kwargs=dict(base_rotations_world=base,geometry=_geometry(),root_target_m=np.array([0.,.01,0.]),
        root_prior_m=np.zeros(3),previous_feet_world_m={n:points[n] for n in ('ankle_left','ankle_right')},
        dt_s=.005,foot_speed_limits_m_s={n:.01 for n in ('ankle_left','ankle_right')},hinge_model=model,
        config=ContactHingeMotionConfig(maximum_iterations=32,stationary_velocity_sigma_m_s=.01))
    kwargs.update(overrides)
    return solve_contact_hinge_motion(**kwargs)


@pytest.mark.parametrize('bend',[0.,1e-7,.08])
def test_manifold_fk_and_log_jacobians_near_straight_and_interior(bend):
    base,model,_=fixture(bend)
    k=_HingeKinematics(base,_geometry(),model,np.diag([-1.,1.,1.]))
    x=np.zeros(11);x[6]=x[10]=bend
    c,p,j,cj=k.decode(x)
    full=corrected_proxy_points(base,c,_geometry())
    for n in p:
        np.testing.assert_allclose(p[n],np.diag([-1.,1.,1.])@full[n],atol=1e-14)
    legs=('thigh_left','shank_left','thigh_right','shank_right')
    for i in range(11):
        delta=np.eye(11)[i]*1e-6
        cp,pp,_,_=k.decode(x+delta,False)
        cm,pm,_,_=k.decode(x-delta,False)
        for n in p:
            numerical=(pp[n]+x[:3]+delta[:3]-pm[n]-x[:3]+delta[:3])/2e-6
            np.testing.assert_allclose(j[n][:,i],numerical,atol=2e-8)
        np.testing.assert_allclose(cj[:,i],np.concatenate([cp[s]-cm[s] for s in legs])/2e-6,atol=2e-8)


def test_actual_returned_hinge_feet_bounds_bones_and_upper_body():
    result=solve()
    assert result.accepted,result.reason
    _,_,p=fixture()
    for side in ('left','right'):
        ankle='ankle_'+side
        assert np.linalg.norm(result.feet_world_m[ankle]-p[ankle])<=.01*.005+1e-7
        np.testing.assert_allclose(np.linalg.norm(result.points[ankle]-result.points['knee_'+side]),.4)
    assert result.projection['manifold_verification_difference_rad']<1e-8
    for s,c in result.corrections.items():
        assert np.max(np.abs(c))<=.18+1e-10
        if not s.startswith(('thigh_','shank_')):
            np.testing.assert_array_equal(c,np.zeros(3))


@pytest.mark.parametrize('bend',[0.,.08])
def test_no_contact_native_parity_and_tracking_root(bend):
    base,model,points=fixture(bend)
    r=solve(base_rotations_world=base,hinge_model=model,previous_feet_world_m={},foot_speed_limits_m_s={})
    assert r.accepted
    np.testing.assert_allclose(r.root_position_m,[0.,.008,0.],atol=1e-10)
    for n,p in points.items():np.testing.assert_allclose(r.points[n],p,atol=1e-10)


def test_objective_uses_actual_manifold_gradient(monkeypatch):
    from biospur_fusion.c2_uwb_calibration import contact_hinge_motion as module
    original=module.minimize
    def inspected(fun,initial,**kwargs):
        _,gradient=fun(initial)
        for i in range(11):
            d=np.eye(11)[i]*1e-6
            np.testing.assert_allclose((fun(initial+d)[0]-fun(initial-d)[0])/2e-6,gradient[i],rtol=2e-5,atol=2e-5)
        return original(fun,initial,**kwargs)
    monkeypatch.setattr(module,'minimize',inspected)
    _,_,p=fixture()
    result=solve(predicted_knees_world_m={n:p[n] for n in ('knee_left','knee_right')})
    assert result.accepted


def test_knee_prediction_reduces_discretionary_motion_without_relaxing_feet():
    base,model,old=fixture(.05)
    base['pelvis']=Rotation.from_rotvec([0.,.0005,.001]).as_matrix()
    native=corrected_proxy_points(base,{s:np.zeros(3) for s in SEGMENTS},_geometry())
    predictions={n:old[n]+native[n]-old[n] for n in ('knee_left','knee_right')}
    args=dict(base_rotations_world=base,hinge_model=model,predicted_knees_world_m=predictions)
    free=solve(**args,config=ContactHingeMotionConfig(maximum_iterations=48,stationary_velocity_sigma_m_s=.01,knee_motion_sigma_m=1e6))
    regularized=solve(**args,config=ContactHingeMotionConfig(maximum_iterations=48,stationary_velocity_sigma_m_s=.01))
    assert free.accepted and regularized.accepted
    def error(r):return sum(np.linalg.norm(r.root_position_m+r.points[n]-predictions[n])**2 for n in predictions)
    assert error(regularized)<error(free)
    assert regularized.maximum_constraint_violation_m<=1e-7


def test_infeasible_returns_rejection_not_projected_trial():
    _,_,p=fixture()
    previous={'ankle_left':p['ankle_left']+[-2.,0.,0.],'ankle_right':p['ankle_right']+[2.,0.,0.]}
    r=solve(previous_feet_world_m=previous)
    assert not r.accepted
    np.testing.assert_array_equal(r.root_position_m,np.zeros(3))
    for c in r.corrections.values():np.testing.assert_array_equal(c,np.zeros(3))


def test_non_idempotent_verifier_cannot_change_installed_pose():
    def bad(base,c):
        c['thigh_left']+=np.array([.001,0.,0.])
        return c,{'post_projection_all_inside_rom':True}
    assert not solve(hinge_projector=bad).accepted


def test_full_whitening_preserves_coupled_bounds_and_identity_zero_residual_hessian(monkeypatch):
    from biospur_fusion.c2_uwb_calibration import contact_hinge_motion as module
    original=module.minimize
    checked=[]
    def inspected(fun,initial,**kwargs):
        np.testing.assert_array_equal(initial,np.zeros(11))
        assert 'bounds' not in kwargs  # Physical boxes are not boxes in dense y.
        linear=kwargs['constraints'][1]
        jacobian=linear['jac'](initial)
        assert jacobian.shape==(22,11)
        assert np.count_nonzero(np.abs(jacobian[:11])>1e-10)>11
        for i in range(11):
            d=np.eye(11)[i]*1e-5
            np.testing.assert_allclose((linear['fun'](d)-linear['fun'](-d))/2e-5,
                                       jacobian[:,i],atol=1e-9)
            np.testing.assert_allclose((fun(d)[1]-fun(-d)[1])/2e-5,np.eye(11)[i],atol=2e-5)
        checked.append(True)
        return original(fun,initial,**kwargs)
    monkeypatch.setattr(module,'minimize',inspected)
    result=solve(root_target_m=np.zeros(3))
    assert checked and result.accepted


def test_opt_in_pelvis_fk_jacobian_and_hip_axis_null_direction():
    base,model,_=fixture()
    g=np.diag([-1.,1.,1.])
    k=_HingeKinematics(base,_geometry(),model,g,True)
    x=np.zeros(14);x[6]=x[10]=.08;x[11:14]=[.01,-.02,.015]
    c,p,j,cj=k.decode(x)
    full=corrected_proxy_points(base,c,_geometry())
    for n in p:np.testing.assert_allclose(p[n],g@full[n],atol=1e-14)
    for i in range(11,14):
        d=np.eye(14)[i]*1e-6
        _,plus,_,_=k.decode(x+d,False);_,minus,_,_=k.decode(x-d,False)
        for n in p:np.testing.assert_allclose((plus[n]-minus[n])/2e-6,j[n][:,i],atol=1e-9)
    np.testing.assert_array_equal(cj[12:,11:],np.eye(3))
    x[11:14]=0.;_,native,_,_=k.decode(x)
    x[11]=.1;_,twisted,_,_=k.decode(x)
    for n in native:np.testing.assert_allclose(twisted[n],native[n],atol=1e-14)


def test_pelvis_objective_gradient_and_unchanged_torso_arms(monkeypatch):
    from biospur_fusion.c2_uwb_calibration import contact_hinge_motion as module
    original=module.minimize
    def inspected(fun,initial,**kwargs):
        assert len(initial)==14
        _,gradient=fun(initial)
        for i in range(14):
            d=np.eye(14)[i]*1e-6
            np.testing.assert_allclose((fun(initial+d)[0]-fun(initial-d)[0])/2e-6,gradient[i],rtol=3e-5,atol=3e-5)
        return original(fun,initial,**kwargs)
    monkeypatch.setattr(module,'minimize',inspected)
    base,model,points=fixture()
    base['pelvis']=Rotation.from_rotvec([0.,.001,0.]).as_matrix()
    r=solve(base_rotations_world=base,hinge_model=model,
        config=ContactHingeMotionConfig(solve_pelvis_orientation=True,stationary_velocity_sigma_m_s=.01,maximum_iterations=24))
    assert r.accepted
    assert np.linalg.norm(r.corrections['pelvis'])>1e-5
    assert np.max(np.abs(r.corrections['pelvis']))<=.18
    native=corrected_proxy_points(base,{s:np.zeros(3) for s in SEGMENTS},_geometry())
    for n in native:
        if not n.startswith(('hip_','knee_','ankle_')):
            np.testing.assert_allclose(r.points[n],native[n],atol=1e-14)
    np.testing.assert_allclose(np.linalg.norm(r.points['hip_left']-r.points['hip_right']),_geometry().hip_span_m)
    for side in ('left','right'):
        np.testing.assert_allclose(np.linalg.norm(r.points['knee_'+side]-r.points['hip_'+side]),
                                   _geometry().segment_length_m['thigh_'+side])
        assert np.linalg.norm(r.feet_world_m['ankle_'+side]-points['ankle_'+side])<=.00005+1e-7


def test_pelvis_no_contact_native_parity_and_release_decay_not_reset():
    base,model,points=fixture()
    cfg=ContactHingeMotionConfig(solve_pelvis_orientation=True)
    r=solve(previous_feet_world_m={},foot_speed_limits_m_s={},config=cfg)
    assert r.accepted
    for n in points:np.testing.assert_allclose(r.points[n],points[n],atol=1e-10)
    previous={s:np.zeros(3) for s in SEGMENTS};previous['pelvis']=np.array([.01,.02,0.])
    released=solve(previous_feet_world_m={},foot_speed_limits_m_s={},previous_correction=previous,config=cfg)
    assert released.accepted
    np.testing.assert_allclose(released.corrections['pelvis'],previous['pelvis']/(1.+(.08/.1)**2),atol=1e-8)


def test_pelvis_default_off_is_unchanged():
    default=solve()
    explicit=solve(config=ContactHingeMotionConfig(maximum_iterations=32,stationary_velocity_sigma_m_s=.01,
                                                  solve_pelvis_orientation=False))
    np.testing.assert_array_equal(default.root_position_m,explicit.root_position_m)
    for s in SEGMENTS:np.testing.assert_array_equal(default.corrections[s],explicit.corrections[s])
