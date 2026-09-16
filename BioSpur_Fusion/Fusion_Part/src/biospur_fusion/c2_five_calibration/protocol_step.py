"""Optional coarse search along existing C2 bend-intent factors.

This changes numerical initialization, not the objective or admissible poses.
Every trial is scored by the caller's complete continuous objective. The
direction is not a runtime pose template and must not be used for H tracking.
"""
import numpy as np
import torch

from .body_feasibility import selection_key


def bend_search_direction(protocol, parameters, time_s, *, taper_s=2.):
    """Smooth low-frequency correction toward the already registered intent.

    The taper only defines a search direction; it adds no residual or bound.
    Invalid samples remain on the original trajectory, with likelihood masks
    owned by the unchanged objective. There is no reset at phase boundaries.
    """
    t=torch.as_tensor(time_s,dtype=parameters.dtype,device=parameters.device)
    if (parameters.ndim!=2 or parameters.shape[1] not in (9,21)
            or t.shape!=(len(parameters),) or not torch.isfinite(t).all()
            or not torch.isfinite(parameters).all() or torch.any(t[1:]<=t[:-1])
            or not np.isfinite(taper_s) or taper_s<=0):
        raise ValueError('finite ordered C2 pose tape and positive taper required')
    direction=torch.zeros_like(parameters)
    occupied=torch.zeros((len(t),4),dtype=torch.bool,device=t.device)
    for row in protocol.rows:
        if row.action.startswith('H'):
            raise ValueError('C2 intent search is forbidden in H inference')
        lo,hi=row.interval
        mask=(t>lo)&(t<hi)
        if torch.any(occupied[:,row.limb]&mask):
            raise ValueError('overlapping intent intervals on one limb')
        occupied[:,row.limb]|=mask
        distance=torch.minimum(t-lo,hi-t)
        window=torch.sin((distance/taper_s).clamp(0,1)*torch.pi/2).square()
        direction[:,3+row.limb]+=window*(protocol.nominal_bend_rad-parameters[:,3+row.limb])
    return direction


def search_bend_step(evaluate, parameters, direction, maximum_bend, *, backtracks=4):
    """Evaluate a bounded coarse line search using full energy and body gates.

    ``evaluate`` returns (scalar total energy, body violation). No data/prior
    terms may be omitted. Returning the original point is always permitted.
    This is neither a convergence certificate nor calibration acceptance.
    """
    if (direction.shape!=parameters.shape or not torch.isfinite(direction).all()
            or not isinstance(backtracks,int) or not 0<=backtracks<=12):
        raise ValueError('finite matching direction and bounded search required')
    maximum=torch.as_tensor(maximum_bend,dtype=parameters.dtype,device=parameters.device)
    if maximum.shape!=(4,):raise ValueError('four bend limits required')
    history=[];best=None;selected=None
    with torch.no_grad():
        for fraction in [0.]+[.5**k for k in range(backtracks+1)]:
            candidate=parameters+fraction*direction
            if (not torch.isfinite(candidate).all() or torch.any(candidate[:,3:7]<0)
                    or torch.any(candidate[:,3:7]>maximum)):
                raise ValueError('search direction must preserve existing bend bounds')
            energy,violation=map(float,evaluate(candidate))
            if not np.isfinite([energy,violation]).all() or violation<0:
                raise ValueError('nonfinite objective or invalid body violation')
            key=selection_key(energy,violation)
            history.append(dict(fraction=fraction,energy=energy,body_violation_m=violation))
            if best is None or key<best:
                best=key;selected=candidate.clone();selected_fraction=fraction
    return selected,dict(history=history,selected_fraction=selected_fraction,
        objective_changed=False,convergence_claimed=False,calibration_accepted=False)
