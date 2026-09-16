"""World no-slip authority, separate from inertial contact classification.

Quiet accelerometers cannot distinguish standing from constant-speed sliding.
A grant is an explicit caller-owned prior/measurement, not inferred here from
quietness, filter velocity, or the same UWB residual being assimilated.
"""
from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class WorldSupportGrant:
    side: int
    start_s: float
    stop_s: float
    available_s: float
    source: str
    episode: str

    def __post_init__(self):
        if (isinstance(self.side, bool) or self.side not in (0, 1)
                or not all(math.isfinite(t) for t in (self.start_s,self.stop_s,self.available_s))
                or self.stop_s <= self.start_s or not self.source or not self.episode):
            raise ValueError('world-support grant needs side, finite interval, source and episode')

    def active(self, time_s):
        return self.available_s <= time_s and self.start_s <= time_s < self.stop_s


class WorldSupportAuthority:
    def __init__(self, grants=()):
        self.grants=tuple(grants)
        self.previous={}
        self.last_time=-np.inf

    def _active_at(self, time_s):
        if not math.isfinite(time_s):
            raise ValueError('world-support query time must be finite')
        active={}
        for grant in self.grants:
            if grant.active(time_s):
                if grant.side in active:
                    raise ValueError('overlapping world-support authority')
                active[grant.side]=grant
        return active

    def mask_at(self, time_s):
        """Read historical authority without advancing assimilation or anchors."""
        active=self._active_at(time_s)
        return np.array([side in active for side in (0,1)])

    def apply(self, points, time_s, stationary, point_valid):
        """Revoke/rebirth anchors before assimilation; preserve contact metadata.

        A new grant cannot reuse an old world reference. The joint contact
        owner creates its new uncertain point without an entry observation.
        """
        if not math.isfinite(time_s) or time_s<self.last_time:
            raise ValueError('world-support assimilation time must be finite and monotonic')
        active=self._active_at(time_s)
        mask=np.array([side in active for side in (0,1)])
        for side in tuple(points.sides) if points is not None else ():
            if side not in active or self.previous.get(side)!=active[side]:
                points.release(side)
        self.previous=active
        self.last_time=time_s
        return np.asarray(stationary,bool)&mask,np.asarray(point_valid,bool)&mask


def calibration_protocol_grants(regions, origin_s):
    """Explicit offline C2 no-slip prior scope, not runtime terrain detection.

    All recorded formal actions are considered, never the unlabeled gaps.
    Actual swing/rolling/seated eligibility remains the classifier's job;
    this prior alone cannot create a contact or a zero-velocity observation.
    """
    return tuple(WorldSupportGrant(side,float(r['start_s'])-origin_s,
        float(r['end_s'])-origin_s,float(r['start_s'])-origin_s,
        'C2_FORMAL_PROTOCOL_PRIOR_NOT_CONTACT_TRUTH',str(r['id']))
        for r in regions if r['kind']=='ACTION' for side in (0,1))
