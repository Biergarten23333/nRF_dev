import numpy as np
import pytest

from biospur_fusion.c2_uwb_calibration.contact_motion_step import (
    ContactMotionStepConfig, solve_contact_motion_step,
)
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from test_c2_contact_leg_ik import setup
from test_c2_articulated_range import _geometry


def solve(target=(0., .02, 0.), speed=.02, **changes):
    base, zero, points, project = setup()
    kwargs = dict(base_rotations_world=base, geometry=_geometry(), root_target_m=np.array(target),
                  root_prior_m=np.zeros(3), previous_feet_world_m={n: points[n] for n in ('ankle_left', 'ankle_right')},
                  dt_s=.005, foot_speed_limits_m_s={n: speed for n in ('ankle_left', 'ankle_right')},
                  hinge_projector=project)
    kwargs.update(changes)
    return solve_contact_motion_step(**kwargs)


def test_planted_feet_root_and_legs_compromise_actual_rom_and_speed():
    _, _, points, _ = setup()
    result = solve()
    assert result.accepted, result.reason
    assert 0 < result.root_position_m[1] < .02
    assert np.linalg.norm(result.corrections['thigh_left']) > 1e-5
    assert result.projection['post_projection_all_inside_rom']
    for side in ('left', 'right'):
        name = 'ankle_'+side
        assert np.linalg.norm(result.feet_world_m[name]-points[name]) <= .02*.005+1e-7
        np.testing.assert_allclose(np.linalg.norm(result.points[name]-result.points['knee_'+side]), .4)
    for name in SEGMENTS:
        assert np.max(np.abs(result.corrections[name])) <= .18+1e-10
        if not name.startswith(('thigh_', 'shank_')):
            np.testing.assert_array_equal(result.corrections[name], np.zeros(3))


def test_moving_limit_releases_root_and_no_support_is_native_when_prior_zero():
    planted = solve()
    moving = solve(speed=10.)
    assert moving.accepted and moving.root_position_m[1] > planted.root_position_m[1]
    free = solve(previous_feet_world_m={}, foot_speed_limits_m_s={})
    assert free.accepted and free.reason == 'NO_CONTACT'
    np.testing.assert_allclose(free.root_position_m, [0., .016, 0.])
    for value in free.corrections.values():
        np.testing.assert_allclose(value, 0., atol=1e-14)


def test_release_decays_previous_native_relative_correction_not_reset():
    stance = solve()
    release = solve(previous_feet_world_m={}, foot_speed_limits_m_s={},
                    root_prior_m=stance.root_position_m, previous_correction=stance.corrections)
    assert stance.accepted and release.accepted
    before = np.linalg.norm(stance.corrections['thigh_left'])
    after = np.linalg.norm(release.corrections['thigh_left'])
    assert 0 < after < before


def test_infeasible_committed_feet_rejected_never_returns_trial():
    _, _, points, _ = setup()
    previous = {'ankle_left': points['ankle_left']+[-2., 0., 0.],
                'ankle_right': points['ankle_right']+[2., 0., 0.]}
    result = solve(previous_feet_world_m=previous)
    assert not result.accepted and result.reason == 'FOOT_CONSTRAINT_REJECTED'
    np.testing.assert_array_equal(result.root_position_m, np.zeros(3))
    for value in result.corrections.values():
        np.testing.assert_array_equal(value, np.zeros(3))


def test_zero_speed_exact_constraint_and_improper_embedding():
    _, _, points, _ = setup()
    embedding = np.diag([-1., 1., 1.])
    result = solve(speed=0., embedding=embedding,
                   previous_feet_world_m={n: embedding@points[n] for n in ('ankle_left', 'ankle_right')})
    assert result.accepted
    for n in ('ankle_left', 'ankle_right'):
        np.testing.assert_allclose(result.feet_world_m[n], embedding@points[n], atol=1e-7)


def test_postprojection_mutation_is_checked_and_inputs_not_modified():
    base, zero, points, _ = setup()
    def bad_projection(b, c):
        c['thigh_left'] = np.array([.3, 0., 0.])
        return c, {'post_projection_all_inside_rom': True}
    result = solve(base_rotations_world=base, previous_correction=zero, hinge_projector=bad_projection)
    assert not result.accepted and result.reason == 'PROJECTED_POSE_REJECTED'
    for n in SEGMENTS:
        np.testing.assert_array_equal(base[n], np.eye(3))
        np.testing.assert_array_equal(zero[n], np.zeros(3))


def test_within_bound_projector_can_still_break_actual_foot_feasibility():
    def inconsistent_projection(base, corrections):
        corrections['shank_left'] = np.array([.15, 0., 0.])
        corrections['shank_right'] = np.array([-.15, 0., 0.])
        return corrections, {'post_projection_all_inside_rom': True}
    result = solve(hinge_projector=inconsistent_projection)
    assert not result.accepted
    assert result.reason == 'FOOT_CONSTRAINT_REJECTED'


def test_root_only_repair_cannot_cross_increment_bound():
    _, _, points, _ = setup()
    shifted = {n: points[n]+[0., 0., 2.] for n in ('ankle_left', 'ankle_right')}
    result = solve(previous_feet_world_m=shifted)
    assert not result.accepted
    assert result.reason in ('ROOT_STEP_REJECTED', 'FOOT_CONSTRAINT_REJECTED')


def test_whitened_objective_gradient_and_full_hessian_with_strong_tracking_prior(monkeypatch):
    from biospur_fusion.c2_uwb_calibration import contact_motion_step as module
    original = module.minimize
    checked = []
    def inspect_objective(fun, initial, **kwargs):
        y = np.linspace(-.1, .1, 15)
        _, gradient = fun(y)
        for i in range(15):
            step = np.eye(15)[i]*1e-5
            plus, gplus = fun(y+step)
            minus, gminus = fun(y-step)
            np.testing.assert_allclose((plus-minus)/2e-5, gradient[i], atol=1e-8)
            np.testing.assert_allclose((gplus-gminus)/2e-5, np.eye(15)[i], atol=1e-8)
        checked.append(True)
        return original(fun, initial, **kwargs)
    monkeypatch.setattr(module, 'minimize', inspect_objective)
    result = solve(config=ContactMotionStepConfig(root_prior_sigma_m=.0008,
                                                  temporal_orientation_sigma_rad=.005))
    assert checked and result.accepted


def test_optional_stationary_velocity_gradient_and_both_feet_improve(monkeypatch):
    from biospur_fusion.c2_uwb_calibration import contact_motion_step as module
    original = module.minimize
    checked = []
    def inspect(fun, initial, **kwargs):
        y = initial+np.linspace(-.0001, .0001, 15)
        _, gradient = fun(y)
        for i in range(15):
            d = np.eye(15)[i]*1e-6
            numerical = (fun(y+d)[0]-fun(y-d)[0])/2e-6
            np.testing.assert_allclose(numerical, gradient[i], rtol=2e-5, atol=2e-5)
        checked.append(True)
        return original(fun, initial, **kwargs)
    baseline = solve()
    monkeypatch.setattr(module, 'minimize', inspect)
    fitted = solve(config=ContactMotionStepConfig(stationary_velocity_sigma_m_s=.01,
                                                 maximum_iterations=24))
    assert checked and fitted.accepted
    _, _, points, _ = setup()
    for n in ('ankle_left', 'ankle_right'):
        assert np.linalg.norm(fitted.feet_world_m[n]-points[n]) < np.linalg.norm(baseline.feet_world_m[n]-points[n])


def test_stationary_objective_default_off_parity_and_validation():
    default = solve()
    explicit = solve(config=ContactMotionStepConfig(stationary_velocity_sigma_m_s=None))
    np.testing.assert_array_equal(default.root_position_m, explicit.root_position_m)
    for s in SEGMENTS:
        np.testing.assert_array_equal(default.corrections[s], explicit.corrections[s])
    for value in (0., -1., np.nan, np.inf):
        with pytest.raises(ValueError):
            solve(config=ContactMotionStepConfig(stationary_velocity_sigma_m_s=value))


@pytest.mark.parametrize('change', [dict(dt_s=0), dict(dt_s=np.nan),
    dict(foot_speed_limits_m_s={'ankle_left': .02}), dict(root_target_m=[np.nan, 0., 0.]),
    dict(config=ContactMotionStepConfig(maximum_iterations=0))])
def test_invalid_inputs(change):
    with pytest.raises(ValueError):
        solve(**change)
