"""Conservative shank-support velocity observations for the common root.

This is not a foot contact sensor: only low-rotation, low-activity shank
intervals qualify. No ankle zero-velocity assumption is made during pivoting.
"""
from dataclasses import dataclass,replace

import numpy as np

@dataclass(frozen=True)
class SupportVelocityConfig:
    # Engineering uncertainty, not fitted measurement statistics. Correlation
    # time matches the inherited 25-sample / 200 Hz detection window.
    velocity_sigma_mps: float = 0.10
    correlation_time_s: float = 0.125
    maximum_sample_age_s: float = 0.0075

    def __post_init__(self):
        values = (self.velocity_sigma_mps, self.correlation_time_s, self.maximum_sample_age_s)
        if not all(np.isfinite(value) and value > 0 for value in values):
            raise ValueError('support configuration must be finite positive')


def support_velocity_evidence(targets_xyz,confidence,dt_s,config=SupportVelocityConfig()):
    """One authoritative bilateral conditional target/noise calculation."""
    targets = np.asarray(targets_xyz, dtype=float)
    confidence = np.asarray(confidence, dtype=float)
    if (targets.ndim != 2 or targets.shape[1] != 3 or len(targets) == 0
            or confidence.shape != (len(targets),)
            or not np.isfinite(targets).all() or not np.isfinite(confidence).all()
            or np.any(confidence <= 0) or np.any(confidence > 1)
            or not np.isfinite(dt_s) or dt_s <= 0
            or config.velocity_sigma_mps <= 0 or config.correlation_time_s <= 0):
        raise ValueError('invalid support velocity evidence')
    weights = confidence / confidence.sum()
    target = weights @ targets
    deviations = targets - target
    disagreement = (deviations.T * weights) @ deviations
    variance = config.velocity_sigma_mps**2 / float(np.max(confidence))
    noise = (np.eye(3) * variance + disagreement) * max(1., config.correlation_time_s / dt_s)
    return target,noise,weights


def update_support_velocity(state, targets_xyz, confidence, dt_s,
                            config=SupportVelocityConfig(), *, consider_position=False,
                            transition_observer=None):
    """Joseph root/joint update; targets are native ankle offset velocities."""
    target,noise,_=support_velocity_evidence(targets_xyz,confidence,dt_s,config)
    dimension=len(state.vector)
    if dimension not in (9,39):raise ValueError('unsupported support base state')
    h = np.zeros((3, dimension))
    h[:, 3:6] = np.eye(3)
    innovation = target - state.velocity_mps
    s = h @ state.covariance @ h.T + noise
    gain = np.linalg.solve(s, h @ state.covariance).T
    if consider_position:
        # A velocity observation must not express accumulated position error
        # as an instantaneous contact-acquisition translation. Retain full
        # prior cross covariance and use the actual constrained gain in Joseph.
        gain[:3] = 0.
    vector = state.vector + gain @ innovation
    residual = np.eye(dimension) - gain @ h
    covariance = residual @ state.covariance @ residual.T + gain @ noise @ gain.T
    covariance = (covariance + covariance.T) * .5
    updated = replace(state,vector=vector,covariance=covariance)
    if transition_observer is not None:
        from .joint_transition_tape import notify_transition
        notify_transition(transition_observer,residual,state,updated,'assimilation',
                          measurement=(h,noise,innovation,gain))
    return updated, innovation, noise
