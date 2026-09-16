from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.c2_uwb_calibration.contact_motion_tracking import (
    ContactMotionTrackingConfig, advance_contact_motion_tracking, initialize_contact_motion_tracking,
)
from test_c2_contact_leg_ik import setup
from test_c2_articulated_range import _geometry


def advance(state, dt=.005, target=None, upstream=None, **overrides):
    base, _, _, projector = setup()
    kwargs = dict(root_target_m=np.zeros(3) if target is None else target,
                  upstream_root_velocity_m_s=np.zeros(3) if upstream is None else upstream,
                  dt_s=dt, base_rotations_world=base, geometry=_geometry(),
                  previous_feet_world_m={}, foot_speed_limits_m_s={}, hinge_projector=projector)
    kwargs.update(overrides)
    return advance_contact_motion_tracking(state, **kwargs)


def test_constant_velocity_target_is_followed_with_consistent_committed_velocity():
    velocity = np.array([.2, -.1, .03])
    state = initialize_contact_motion_tracking(np.zeros(3), velocity)
    for i in range(1, 101):
        previous = state
        state, result = advance(state, target=velocity*(i*.005), upstream=velocity)
        assert result.accepted
        np.testing.assert_allclose(state.root_position_m, velocity*(i*.005), atol=1e-12)
        np.testing.assert_allclose(state.root_velocity_m_s,
                                   (state.root_position_m-previous.root_position_m)/.005, atol=1e-12)


def test_100_and_200_hz_tracks_are_near_equivalent_not_frame_gain_dependent():
    endpoints = []
    for dt in (.01, .005):
        state = initialize_contact_motion_tracking([0., 0., 0.])
        for _ in range(round(.5/dt)):
            state, result = advance(state, dt=dt, target=[.1, .02, 0.])
            assert result.accepted
        endpoints.append(state.root_position_m)
    np.testing.assert_allclose(endpoints[0], endpoints[1], atol=.001)


def test_release_with_discrepancy_does_not_reset_root_or_leg_correction():
    state = initialize_contact_motion_tracking([0., 0., 0.])
    corrections = {s: c.copy() for s, c in state.corrections.items()}
    for side in ('left', 'right'):
        corrections['thigh_'+side] = np.array([.03, 0., 0.])
        corrections['shank_'+side] = np.array([.03, 0., 0.])
    state = replace(state, corrections=corrections)
    new, result = advance(state, target=[.5, 0., 0.])
    assert result.accepted
    assert 0 < new.root_position_m[0] < .001
    assert .029 < new.corrections['thigh_left'][0] < .03
    np.testing.assert_array_equal(state.corrections['thigh_left'], [.03, 0., 0.])


def test_rejected_solve_returns_identical_unmodified_state():
    state = initialize_contact_motion_tracking([0., 0., 0.], [.1, 0., 0.])
    def invalid_projector(base, corrections):
        return corrections, {'post_projection_all_inside_rom': False}
    new, result = advance(state, target=[1., 0., 0.], hinge_projector=invalid_projector)
    assert not result.accepted and new is state
    np.testing.assert_array_equal(state.root_position_m, [0., 0., 0.])
    np.testing.assert_array_equal(state.root_velocity_m_s, [.1, 0., 0.])


@pytest.mark.parametrize('dt', [0., -.005, np.nan, np.inf, .1])
def test_invalid_or_unresolved_dt_is_rejected(dt):
    with pytest.raises(ValueError):
        advance(initialize_contact_motion_tracking([0., 0., 0.]), dt=dt)


def test_nonfinite_upstream_or_state_rejected():
    state = initialize_contact_motion_tracking([0., 0., 0.])
    with pytest.raises(ValueError):
        advance(state, upstream=[np.nan, 0., 0.])
    with pytest.raises(ValueError):
        advance(replace(state, root_velocity_m_s=np.array([0., np.inf, 0.])))


def test_hinge_knee_prior_uses_predicted_motion_not_upstream_position(monkeypatch):
    from biospur_fusion.c2_uwb_calibration import contact_hinge_motion
    from types import SimpleNamespace
    captured = {}
    def capture(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(accepted=False)
    monkeypatch.setattr(contact_hinge_motion, 'solve_contact_hinge_motion', capture)
    state = initialize_contact_motion_tracking([1., 2., 3.], [.2, 0., 0.])
    prior = np.array([1., 2., 2.5])
    new, _ = advance(state, target=[100., 200., 300.], upstream=[.2, 0., 0.],
        hinge_model={}, knee_motion_prior_m={'knee_left': prior},
        solver_config=contact_hinge_motion.ContactHingeMotionConfig())
    assert new is state
    np.testing.assert_allclose(captured['predicted_knees_world_m']['knee_left'], prior+[.001, 0., 0.])
    np.testing.assert_array_equal(prior, [1., 2., 2.5])


def test_knee_prior_without_hinge_solver_is_rejected():
    with pytest.raises(ValueError, match='requires'):
        advance(initialize_contact_motion_tracking([0., 0., 0.]), knee_motion_prior_m={})


def test_post_constraint_commit_uses_actual_repaired_position():
    state = initialize_contact_motion_tracking([0., 0., 0.], [0., .1, 0.])
    _, _, points, _ = setup()
    previous = {n: points[n] for n in ('ankle_left', 'ankle_right')}
    new, result = advance(state, target=[0., .1, 0.], upstream=[0., .1, 0.],
                          previous_feet_world_m=previous,
                          foot_speed_limits_m_s={n: .01 for n in previous})
    assert result.accepted
    np.testing.assert_allclose(new.root_velocity_m_s, new.root_position_m/.005)
    for segment in new.corrections:
        np.testing.assert_allclose(new.correction_velocity_rad_s[segment], new.corrections[segment]/.005)


def test_constraint_generated_stance_then_half_second_release_has_no_deferred_snap():
    state = initialize_contact_motion_tracking([0., 0., 0.])
    _, _, points, _ = setup()
    names = ('ankle_left', 'ankle_right')
    feet = {n: points[n] for n in names}
    for i in range(30):
        state, result = advance(state, target=[0., .002*i, 0.],
            previous_feet_world_m=feet, foot_speed_limits_m_s={n: .01 for n in names})
        assert result.accepted
        feet = {n: result.feet_world_m[n].copy() for n in names}
    assert np.linalg.norm(state.corrections['thigh_left']) > 1e-5
    discrepancy = np.linalg.norm(np.array([0., .058, 0.])-state.root_position_m)
    assert discrepancy > .02
    steps, correction_steps = [], []
    for _ in range(100):
        old = state
        state, result = advance(state, target=[0., .058, 0.])
        assert result.accepted and np.isfinite(state.root_velocity_m_s).all()
        steps.append(np.linalg.norm(state.root_position_m-old.root_position_m))
        correction_steps.append(np.linalg.norm(state.corrections['thigh_left']-old.corrections['thigh_left']))
    assert max(steps) < .001
    assert max(correction_steps) < .001


@pytest.mark.parametrize('prediction', ['unknown', '', None, 1])
def test_correction_prediction_validation(prediction):
    with pytest.raises(ValueError):
        advance(initialize_contact_motion_tracking([0.,0.,0.]),
                config=ContactMotionTrackingConfig(correction_prediction=prediction))


def test_default_rate_prediction_is_bit_identical_to_explicit_rate():
    state=initialize_contact_motion_tracking([0.,0.,0.],[.1,0.,0.])
    default,rd=advance(state,target=[.01,0.,0.])
    explicit,re=advance(state,target=[.01,0.,0.],
                        config=ContactMotionTrackingConfig(correction_prediction='rate'))
    np.testing.assert_array_equal(default.root_position_m,explicit.root_position_m)
    np.testing.assert_array_equal(default.root_velocity_m_s,explicit.root_velocity_m_s)
    for s in default.corrections:
        np.testing.assert_array_equal(default.corrections[s],explicit.corrections[s])
    assert rd.reason==re.reason


def test_constant_prediction_maps_current_c_and_keeps_root_dynamics_and_rate_diagnostics(monkeypatch):
    import biospur_fusion.c2_uwb_calibration.contact_motion_tracking as module
    from types import SimpleNamespace
    state=initialize_contact_motion_tracking([1.,2.,3.],[.1,.2,.3])
    c={s:np.full(3,.01) for s in state.corrections}
    rates={s:np.full(3,.3) for s in state.corrections}
    state=replace(state,corrections=c,correction_velocity_rad_s=rates)
    captured=[]
    def solver(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(accepted=True,root_position_m=kwargs['root_prior_m'],
                               corrections={s:v+np.array([.001,0.,0.]) for s,v in kwargs['previous_correction'].items()})
    monkeypatch.setattr(module,'solve_contact_motion_step',solver)
    new,_=advance(state,upstream=[.4,.5,.6],config=ContactMotionTrackingConfig(correction_prediction='constant'))
    expected_root=state.root_position_m+.005*(state.root_velocity_m_s+.005/.1*(np.array([.4,.5,.6])-state.root_velocity_m_s))
    np.testing.assert_array_equal(captured[0]['root_prior_m'],expected_root)
    for s in state.corrections:
        np.testing.assert_array_equal(captured[0]['previous_correction'][s],state.corrections[s])
        np.testing.assert_allclose(new.correction_velocity_rad_s[s],[.2,0.,0.])
    np.testing.assert_allclose(new.root_velocity_m_s,(expected_root-state.root_position_m)/.005)


def test_constant_prediction_rejection_preserves_exact_state():
    state=initialize_contact_motion_tracking([0.,0.,0.],[.1,0.,0.])
    def invalid(base,c):return c,{'post_projection_all_inside_rom':False}
    new,result=advance(state,hinge_projector=invalid,
                       config=ContactMotionTrackingConfig(correction_prediction='constant'))
    assert not result.accepted and new is state


def test_constant_correction_does_not_freeze_current_native_motion():
    from scipy.spatial.transform import Rotation
    state=initialize_contact_motion_tracking([0.,0.,0.])
    base,_,native,projector=setup()
    for s in ('thigh_left','shank_left'):
        base[s]=Rotation.from_rotvec([.02,0.,0.]).as_matrix()
    new,result=advance(state,base_rotations_world=base,hinge_projector=projector,
                       config=ContactMotionTrackingConfig(correction_prediction='constant'))
    assert result.accepted
    assert np.linalg.norm(result.points['knee_left']-native['knee_left'])>.001
    for c in new.corrections.values():np.testing.assert_allclose(c,0.,atol=1e-12)


@pytest.mark.parametrize('dt',[.005,.01])
def test_constant_nonzero_release_keeps_original_dt_squared_prior(dt):
    state=initialize_contact_motion_tracking([0.,0.,0.])
    c={s:v.copy() for s,v in state.corrections.items()}
    c['thigh_left']=np.array([.03,0.,0.]);c['shank_left']=c['thigh_left'].copy()
    state=replace(state,corrections=c)
    new,result=advance(state,dt=dt,config=ContactMotionTrackingConfig(correction_prediction='constant'))
    assert result.accepted
    np.testing.assert_allclose(new.corrections['thigh_left'],c['thigh_left']/(1+dt**2/.1**2),atol=1e-12)
