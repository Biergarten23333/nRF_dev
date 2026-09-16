"""Bounded joint-trajectory search under one unchanged five-IMU objective.

Seeds change latent elbow planes, never observations, calibration, or the
learned target. Selection uses the full shared objective after continuation.
This is a local-search diagnostic, not a claim of unique observability.
"""
import time

import numpy as np
import torch

from .body_feasibility import selection_key
from .optimization import optimize_pose
from .solver import PoseObjective


def trajectory_seeds(objective, initial_rotation=None):
    base=(objective.initial.clone() if initial_rotation is None else
          objective.parameters_from_rotation(initial_rotation))
    yield 'current',base
    # Both arms remain in one objective with a shared torso. Each alternate
    # starts from a coherent trajectory rather than per-frame branch splicing.
    for arm in range(2):
        for sign in (-1,1):
            value=base.clone()
            value[:,7+arm]+=sign*np.pi/2
            yield ('left' if arm==0 else 'right')+('_minus90' if sign<0 else '_plus90'),value


def solve_multistart(prior, observed, acceleration, valid, time_s, geometry, levers,
                     *, initial_rotation=None, iterations=60, wall_limit_s=300.):
    started=time.monotonic()
    objective=PoseObjective(prior,observed,acceleration,valid,time_s,geometry)
    candidates=[];best=None;selected=None
    for name,seed in trajectory_seeds(objective,initial_rotation):
        remaining=wall_limit_s-(time.monotonic()-started)
        if remaining<=0:
            raise TimeoutError('joint multistart budget expired before all starts were compared')
        parameters,history,audit=optimize_pose(objective,levers,seed,
            iterations=iterations,wall_limit_s=remaining)
        with torch.no_grad():rotation,terms=objective.evaluate(parameters,levers)
        key=selection_key(terms['loss'],terms['body_violation'])
        candidates.append(dict(seed=name,loss=float(terms['loss']),
            acceleration_rms_mps2=float(terms['acceleration_rms_mps2']),
            body_violation_m=float(terms['body_violation']),optimizer=audit))
        if best is None or key<best:
            best=key;selected=(name,rotation.detach().numpy())
    return selected[1],dict(selected_seed=selected[0],candidates=candidates,
        selection='same full five-IMU objective; inner-core feasibility first',
        learned_target_unchanged=True,calibration_unchanged=True,
        reference_used=False,action_labels_consumed=False,
        body=objective.body.audit(torch.as_tensor(selected[1]),objective.valid),
        wall_s=time.monotonic()-started,accepted=False)
