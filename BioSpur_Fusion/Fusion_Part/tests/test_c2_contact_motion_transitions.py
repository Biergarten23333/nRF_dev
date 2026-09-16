"""Bounded independent transition checks, not physical-accuracy validation."""
import numpy as np
import pytest

from biospur_fusion.c2_uwb_calibration.contact_motion_step import ContactMotionStepConfig
from biospur_fusion.c2_uwb_calibration.contact_motion_tracking import (
    advance_contact_motion_tracking, initialize_contact_motion_tracking,
)
from test_c2_articulated_range import _geometry
from test_c2_contact_leg_ik import setup


@pytest.mark.parametrize('stationary_sigma', [None, .01])
def test_actual_stance_release_stance_keeps_committed_motion_and_release_tail(stationary_sigma):
    base, _, points, projector = setup()
    state = initialize_contact_motion_tracking(np.zeros(3))
    feet = ('ankle_left', 'ankle_right')
    previous_feet = {name: points[name].copy() for name in feet}
    target = np.array([0., .05, 0.])
    dt = .005
    positions = [state.root_position_m.copy()]
    velocities = [state.root_velocity_m_s.copy()]
    corrections = []
    foot_steps = []
    # Actual solves build the discrepancy, then release long enough to inspect
    # the response tail, then re-enter against the last committed FK endpoints.
    for index in range(160):
        supported = index < 30 or index >= 130
        before = state
        state, result = advance_contact_motion_tracking(
            state, root_target_m=target, upstream_root_velocity_m_s=np.zeros(3),
            dt_s=dt, base_rotations_world=base, geometry=_geometry(),
            previous_feet_world_m=previous_feet if supported else {},
            foot_speed_limits_m_s={name: .01 for name in feet} if supported else {},
            hinge_projector=projector,
            solver_config=ContactMotionStepConfig(maximum_iterations=24,
                stationary_velocity_sigma_m_s=stationary_sigma))
        assert result.accepted, (index, result.reason)
        np.testing.assert_allclose(state.root_velocity_m_s,
            (state.root_position_m-before.root_position_m)/dt, atol=1e-12)
        steps = np.array([np.linalg.norm(result.feet_world_m[name]-previous_feet[name])
                          for name in feet])
        if supported:
            assert np.max(steps) <= .01*dt+1.01e-7
        previous_feet = {name: result.feet_world_m[name].copy() for name in feet}
        positions.append(state.root_position_m.copy())
        velocities.append(state.root_velocity_m_s.copy())
        corrections.append(np.concatenate(list(state.corrections.values())))
        foot_steps.append(steps)
    positions, velocities = np.array(positions), np.array(velocities)
    corrections, foot_steps = np.array(corrections), np.array(foot_steps)
    assert np.linalg.norm(positions[30]-target) > .03
    assert np.linalg.norm(corrections[29]) > 1e-5
    # Not merely the first release frame: inspect the whole 0.5-second tail.
    release_steps = np.linalg.norm(np.diff(positions, axis=0)[30:130], axis=1)
    assert release_steps.max() < .002
    assert foot_steps[30:130].max() < .002
    assert np.max(np.linalg.norm(velocities[31:131], axis=1)) < .4
    assert np.linalg.norm(positions[130]-target) < np.linalg.norm(positions[30]-target)
    assert np.linalg.norm(corrections[30]-corrections[29]) < .005
    assert np.isfinite(positions).all() and np.isfinite(velocities).all()


@pytest.mark.parametrize('stationary_sigma', [None, .01])
def test_100_200_hz_entire_position_velocity_response_and_peaks_agree(stationary_sigma):
    base, _, _, projector = setup()
    responses = []
    target = np.array([.10, .02, 0.])
    for dt in (.01, .005):
        state = initialize_contact_motion_tracking(np.zeros(3))
        positions, velocities = [], []
        for _ in range(round(.8/dt)):
            state, result = advance_contact_motion_tracking(
                state, root_target_m=target, upstream_root_velocity_m_s=np.zeros(3),
                dt_s=dt, base_rotations_world=base, geometry=_geometry(),
                previous_feet_world_m={}, foot_speed_limits_m_s={},
                hinge_projector=projector,
                solver_config=ContactMotionStepConfig(
                    stationary_velocity_sigma_m_s=stationary_sigma))
            assert result.accepted
            positions.append(state.root_position_m.copy())
            velocities.append(state.root_velocity_m_s.copy())
        responses.append((np.array(positions), np.array(velocities)))
    coarse_p, coarse_v = responses[0]
    fine_p, fine_v = responses[1]
    # Compare all coincident epochs, not just a coincident final endpoint.
    np.testing.assert_allclose(coarse_p, fine_p[1::2], atol=.001, rtol=0.)
    np.testing.assert_allclose(coarse_v, fine_v[1::2], atol=.005, rtol=0.)
    np.testing.assert_allclose(np.linalg.norm(coarse_p, axis=1).max(),
                               np.linalg.norm(fine_p, axis=1).max(), rtol=.02)
    np.testing.assert_allclose(np.linalg.norm(coarse_v, axis=1).max(),
                               np.linalg.norm(fine_v, axis=1).max(), rtol=.05)
