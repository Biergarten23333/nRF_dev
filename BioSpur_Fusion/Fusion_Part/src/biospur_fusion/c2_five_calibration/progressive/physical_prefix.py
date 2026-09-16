"""Cumulative physical-prefix refinement using the established solver owners."""
import time

import numpy as np

from biospur_fusion.c2_five_calibration.geometry import OBSERVED
from biospur_fusion.c2_five_calibration.solver import solve_pose,lever_system,fit_levers


def warm_start_rotation(data,previous):
    """Transfer a past numerical guess, preserving new observed rotations.

    This does not add a prior likelihood or overwrite the fresh network target.
    New mounting/heading may change observations, so re-express the guess using
    the established solver's parameters_from_rotation path.
    """
    count=len(previous['time_s'])
    if count>len(data['time_s']) or not np.array_equal(previous['time_s'],data['time_s'][:count]):
        raise ValueError('warm start must be an exact past time prefix')
    old=np.asarray(previous['rotation'])
    if old.shape!=(count,24,3,3) or not np.isfinite(old).all():
        raise ValueError('invalid previous pose shape')
    if not np.allclose(old@old.transpose(0,1,3,2),np.eye(3),atol=1e-5) or not np.allclose(np.linalg.det(old),1.,atol=1e-5):
        raise ValueError('previous pose is not proper rotation')
    result=np.array(data['prior'],copy=True)
    result[:count]=old
    result[:,OBSERVED]=data['observed']
    return result


def refine_prefix(data,geometry,*,previous=None,iterations=30,wall_limit_s=600.,progress=None):
    started=time.monotonic();nominal=np.asarray(geometry['nominal_sensor_levers_m'])
    levers=nominal if previous is None else np.asarray(previous['levers'])
    if levers.shape!=(5,3) or not np.isfinite(levers).all():
        raise ValueError('five finite previous sensor levers required')
    initial=None if previous is None else warm_start_rotation(data,previous)
    rotation,first=solve_pose(**data,geometry=geometry,levers=levers,iterations=iterations,
                             wall_limit_s=wall_limit_s,initial_rotation=initial)
    if progress:progress('first_pose',first)
    if time.monotonic()-started>=wall_limit_s:raise TimeoutError('prefix physical budget before lever fit')
    # Replace the fit on all currently arrived data. The old lever estimate
    # is not a second prior; the regularizer remains centred at fixed geometry.
    levers,lever_audit=fit_levers([lever_system(rotation,data['acceleration'],data['valid'],geometry)],nominal)
    if progress:progress('lever',lever_audit)
    remaining=wall_limit_s-(time.monotonic()-started)
    if remaining<=0:raise TimeoutError('prefix physical budget after lever fit')
    rotation,second=solve_pose(**data,geometry=geometry,levers=levers,iterations=iterations,
                              wall_limit_s=remaining,initial_rotation=rotation)
    return dict(rotation=rotation,time_s=data['time_s'],valid=data['valid'],levers=levers),dict(
        first=first,second=second,lever=lever_audit,elapsed_seconds=time.monotonic()-started,
        warm_start_used=previous is not None,previous_state_is_observation=False,
        lever_regularization_center='fixed measured-geometry surface proxy',calibration_accepted=False)
