"""Opt-in causal kinematic tracking state around the constrained motion step.

Time constants are provisional engineering settings, not PIP parameters. The
segment velocity is a derivative of native-relative right-local coordinates, not
physical segment angular velocity: changing native charts are approximated.
No covariance, raw-estimator feedback, or display interpolation is introduced.
"""
from dataclasses import dataclass, replace
from typing import Mapping

import numpy as np

from .articulated_range import SEGMENTS
from .contact_motion_step import ContactMotionStepConfig, solve_contact_motion_step


@dataclass(frozen=True)
class ContactMotionTrackingConfig:
    tau_v_s: float = .10
    tau_p_s: float = .25
    tau_c_s: float = .10
    correction_prediction: str = 'rate'

    def validate(self):
        if any(not np.isfinite(v) or v <= 0 for v in (self.tau_v_s, self.tau_p_s, self.tau_c_s)):
            raise ValueError('tracking time constants must be finite and positive')
        if self.correction_prediction not in ('rate', 'constant'):
            raise ValueError('correction_prediction must be rate or constant')


@dataclass(frozen=True)
class ContactMotionTrackingState:
    root_position_m: np.ndarray
    root_velocity_m_s: np.ndarray
    corrections: Mapping[str, np.ndarray]
    correction_velocity_rad_s: Mapping[str, np.ndarray]


def _vector(value, label):
    vector = np.asarray(value, dtype=float).reshape(3).copy()
    if not np.isfinite(vector).all():
        raise ValueError('nonfinite '+label)
    return vector


def initialize_contact_motion_tracking(root_position_m, root_velocity_m_s=None):
    """Initialize once at the first native pose; no accumulated correction."""
    return ContactMotionTrackingState(
        _vector(root_position_m, 'root position'),
        _vector(np.zeros(3) if root_velocity_m_s is None else root_velocity_m_s, 'root velocity'),
        {s: np.zeros(3) for s in SEGMENTS}, {s: np.zeros(3) for s in SEGMENTS})


def advance_contact_motion_tracking(state, *, root_target_m, upstream_root_velocity_m_s,
                                    dt_s, base_rotations_world, geometry,
                                    previous_feet_world_m, foot_speed_limits_m_s,
                                    hinge_projector, embedding=None,
                                    config=ContactMotionTrackingConfig(),
                                    solver_config=ContactMotionStepConfig(),
                                    hinge_model=None, knee_motion_prior_m=None):
    """Predict acceleration, constrain next pose, then commit actual velocities.

    The root predictor is p+dt*(v+dt/tau_v*(v_upstream-v)). The inner solver's
    target/prior precision ratio dt²/tau_p² realizes implicit position tracking.
    The analogous leg ratio dt²/tau_c² attracts corrections toward native zero.
    Constant correction prediction carries current correction values without
    extrapolating their rates. Native orientations still supply actual motion;
    this is not a frozen world pose. Root position/velocity tracking is unchanged.
    The unchanged dt² prior gives constant-mode free correction decay
    c/(1+dt²/tau_c²) per frame. Its decay over wall time is cadence-dependent;
    tau_c is not a continuous-time first-order decay constant in this mode.
    Root-ball repair and hinge projection happen BEFORE committed finite-
    difference velocities are formed. Rejection returns the identical state.

    Supply FK feet of the previous committed state. The caller owns support
    detection and upstream velocity; neither is inferred by this helper.
    Opt-in knee_motion_prior_m is previous committed WORLD knee plus current
    native ROOT-RELATIVE knee increment. This helper adds predicted root motion;
    callers must not add the noisy upstream position target a second time.
    The optional hinge solver may also correct pelvis orientation. Corrections
    and their rates already cover every segment, so support release retains
    that state instead of silently resetting the pelvis to its native input.
    """
    config.validate()
    solver_config.validate()
    if not np.isfinite(dt_s) or dt_s <= 0:
        raise ValueError('tracking dt_s must be finite and positive')
    # Explicit damping prediction requires a short resolved motion step; a gap
    # is a caller lifecycle decision, not permission to integrate unstable dt.
    if dt_s > min(config.tau_v_s, .5*config.tau_c_s):
        raise ValueError('tracking dt_s exceeds the resolved-step damping bound')
    position = _vector(state.root_position_m, 'state root position')
    velocity = _vector(state.root_velocity_m_s, 'state root velocity')
    target = _vector(root_target_m, 'root target')
    upstream_velocity = _vector(upstream_root_velocity_m_s, 'upstream root velocity')
    if set(state.corrections) != set(SEGMENTS) or set(state.correction_velocity_rad_s) != set(SEGMENTS):
        raise ValueError('tracking state must contain every inherited segment')
    corrections = {s: _vector(state.corrections[s], 'state correction') for s in SEGMENTS}
    correction_velocity = {s: _vector(state.correction_velocity_rad_s[s], 'correction velocity') for s in SEGMENTS}
    predicted_root = position+dt_s*(velocity+dt_s/config.tau_v_s*(upstream_velocity-velocity))
    if config.correction_prediction == 'constant':
        predicted_c = {s: corrections[s].copy() for s in SEGMENTS}
    else:
        predicted_c = {s: corrections[s]+dt_s*(1.-2.*dt_s/config.tau_c_s)*correction_velocity[s]
                       for s in SEGMENTS}
    step_config = replace(solver_config,
        root_prior_sigma_m=solver_config.root_target_sigma_m*dt_s/config.tau_p_s,
        temporal_orientation_sigma_rad=solver_config.orientation_prior_sigma_rad*dt_s/config.tau_c_s)
    solve = solve_contact_motion_step
    extra = {}
    if hinge_model is not None:
        from .contact_hinge_motion import solve_contact_hinge_motion
        solve = solve_contact_hinge_motion
        extra = dict(hinge_model=hinge_model, predicted_knees_world_m={
            k: _vector(v, 'knee motion prior') + predicted_root-position
            for k, v in (knee_motion_prior_m or {}).items()})
    elif knee_motion_prior_m is not None:
        raise ValueError('knee motion prior requires the hinge-consistent solver')
    result = solve(
        base_rotations_world=base_rotations_world, geometry=geometry,
        root_target_m=target, root_prior_m=predicted_root,
        previous_feet_world_m=previous_feet_world_m, dt_s=dt_s,
        foot_speed_limits_m_s=foot_speed_limits_m_s, hinge_projector=hinge_projector,
        previous_correction=predicted_c, embedding=embedding, config=step_config, **extra)
    if not result.accepted:
        return state, result
    committed_root = result.root_position_m.copy()
    committed_c = {s: np.asarray(result.corrections[s]).copy() for s in SEGMENTS}
    return ContactMotionTrackingState(committed_root, (committed_root-position)/dt_s,
        committed_c, {s: (committed_c[s]-corrections[s])/dt_s for s in SEGMENTS}), result
