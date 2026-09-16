"""Causal publication of calibration corrections, not filtering IMU motion."""
from dataclasses import dataclass
import math
import numpy as np


@dataclass(frozen=True)
class HeadingPublicationState:
    time_s: float
    applied_rad: float
    target_rad: float


def publish_heading_correction(time_s, target_rad, *, settling_s,
                               state=None):
    """Apply a calibration target with a time-based first-order response.

    Raw orientations do not enter this function. The target supplied at the
    preceding timestamp acts over the next interval; a new target cannot
    retroactively move the state. The returned state permits streaming use.
    """
    t=np.asarray(time_s,dtype=float); target=np.asarray(target_rad,dtype=float)
    if (t.ndim!=1 or t.shape!=target.shape or len(t)==0
            or not np.isfinite(t).all() or not np.isfinite(target).all()
            or np.any(np.diff(t)<=0) or not np.isfinite(settling_s) or settling_s<=0):
        raise ValueError('finite increasing clock, matching targets and positive settling time required')
    # State includes the preceding target, making chunking exactly equivalent.
    if state is None:
        previous_time=float(t[0]); applied=float(target[0]); previous_target=float(target[0])
    else:
        previous_time,applied,previous_target=state.time_s,state.applied_rad,state.target_rad
        if not all(np.isfinite([previous_time,applied,previous_target])) or t[0]<=previous_time:
            raise ValueError('publication state must precede the new chunk')
    out=np.empty_like(target)
    for i,(now,new_target) in enumerate(zip(t,target)):
        dt=float(now-previous_time)
        error=math.atan2(math.sin(previous_target-applied),math.cos(previous_target-applied))
        applied+=-math.expm1(-dt/settling_s)*error
        out[i]=applied
        previous_time=float(now); previous_target=float(new_target)
    return out,HeadingPublicationState(previous_time,applied,previous_target)
