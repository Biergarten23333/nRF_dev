"""Identity, time support and direction semantics for registered C2 phases.

Legacy single-phase factors retain their action key. Explicit phases have
separate keys, so two disjoint mechanisms in one action cannot overwrite
each other's information. Bounds are relative to ACTION_START, not preroll.
"""
import re

import numpy as np


def recorded_prefix(names, *, require_complete=False):
    """Validate actual C2 membership; no omitted interior action or future H."""
    from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
    names=list(names)
    expected=EPISODES[:len(names)]
    if not names or len(set(names))!=len(names) or set(names)!=set(expected):
        raise ValueError('exact contiguous recorded C2 prefix required')
    if require_complete and len(names)!=len(EPISODES):
        raise ValueError('complete recorded C2 required')
    return expected


def phase_key(action, phase_id=None):
    if not isinstance(action,str) or not action or ':' in action:
        raise ValueError('phase requires an unambiguous action name')
    if phase_id is None:return action
    if not isinstance(phase_id,str) or not re.fullmatch(r'[a-z][a-z0-9_]*',phase_id):
        raise ValueError('phase identifier must be a lowercase name')
    return action+':'+phase_id


def phase_bounds(factor):
    """Read explicit bounds, with compatibility for old single-phase factors."""
    phase_key(factor['action'],factor.get('phase_id'))
    interval=factor.get('formal_interval_s')
    if interval is None:
        if factor.get('phase_id') is not None or factor['source_role']=='directed_forward':
            raise ValueError('explicit phase requires formal interval bounds')
        interval=[0.,15. if factor['action'].startswith(('06','07')) else 30.]
    bounds=np.asarray(interval,dtype=float)
    if bounds.shape!=(2,) or not np.isfinite(bounds).all() or not 0<=bounds[0]<bounds[1]<=30.:
        raise ValueError('phase bounds must lie within the formal 30-second action')
    return tuple(bounds.tolist())


def direction_target(kind, limb):
    """BioSpur target axes and angular periodicity; not a measured chest pose."""
    if kind=='directed_side':return np.array([0.,1. if limb==0 else -1.,0.]),1
    if kind=='directed_forward':return np.array([1.,0.,0.]),1
    if kind=='axis_forward':return np.array([1.,0.,0.]),2
    if kind=='axis_lateral':return np.array([0.,1.,0.]),2
    raise ValueError('unknown heading factor convention')
