"""Conservative target/still/return-walk boundary detection."""
from __future__ import annotations
import numpy as np

class SegmentationError(ValueError): pass

def find_post_action_transition(t_s, motion_score, *, action_start_s, stop_upper_s,
                                quiet_threshold, quiet_required_s=3.0):
    """Return scored end and post-transition start from a >=3 s quiet interval.

    STOP is only an upper bound. The final motion burst before a qualifying
    still interval closes the scored target; renewed motion after that still is
    POST_ACTION_TRANSITION_UNSCORED.
    """
    t=np.asarray(t_s,float);m=np.asarray(motion_score,float)
    if len(t)!=len(m) or len(t)<2 or np.any(np.diff(t)<=0):raise SegmentationError("invalid timeline")
    mask=(t>=action_start_s)&(t<=stop_upper_s);idx=np.flatnonzero(mask)
    for i in idx:
        if m[i]>quiet_threshold:continue
        j=i
        while j+1<len(t) and t[j+1]<=stop_upper_s and m[j+1]<=quiet_threshold:j+=1
        if t[j]-t[i]>=quiet_required_s:
            prior=np.flatnonzero((t>=action_start_s)&(t<t[i])&(m>quiet_threshold))
            later=np.flatnonzero((t>t[j])&(t<=stop_upper_s)&(m>quiet_threshold))
            if len(prior):
                return {"scored_end_s":float(t[prior[-1]]),"quiet_start_s":float(t[i]),
                        "quiet_end_s":float(t[j]),"post_transition_start_s":float(t[later[0]]) if len(later) else None}
    raise SegmentationError("no target-motion to >=3 s still boundary before STOP")
