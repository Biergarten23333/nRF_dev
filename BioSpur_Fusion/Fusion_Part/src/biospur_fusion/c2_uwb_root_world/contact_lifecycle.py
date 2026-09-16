"""Causal stationary-ankle eligibility, distinct from physical sole contact."""
import math
from collections import deque


class RotationPositionBridge:
    """Position-only consideration; never creates velocity evidence."""
    def __init__(self, horizon_s=.125, maximum_age_s=.0075):
        if not all(math.isfinite(x) and x>0 for x in (horizon_s,maximum_age_s)):
            raise ValueError('invalid position bridge intervals')
        self.horizon_s=horizon_s;self.maximum_age_s=maximum_age_s
        self.last_epoch=None;self.last_direct=None
        self.history=deque(maxlen=32);self.bridge_samples=0

    def update(self,epoch,pose_epoch,*,fresh,acceleration_quiet,lifted,direct_stationary,rotation_only):
        if not math.isfinite(epoch) or (self.last_epoch is not None and epoch<self.last_epoch):
            raise ValueError('invalid position bridge epoch')
        if epoch==self.last_epoch:return
        if self.last_epoch is not None and epoch-self.last_epoch>self.maximum_age_s:
            self.last_direct=None
        self.last_epoch=epoch
        if not fresh or not acceleration_quiet or lifted:self.last_direct=None
        elif direct_stationary:self.last_direct=epoch
        elif not rotation_only:self.last_direct=None
        deadline=-math.inf if self.last_direct is None else self.last_direct+self.horizon_s
        active=bool(rotation_only and fresh and acceleration_quiet and not lifted and epoch<deadline)
        self.bridge_samples+=int(active)
        self.history.append((epoch,pose_epoch,active,deadline))

    def evidence(self,query):
        for sample,pose,active,deadline in reversed(self.history):
            if sample<=query+1e-9:
                return bool(active and query<deadline
                    and -1e-9<=query-sample<=self.maximum_age_s
                    and -1e-9<=query-pose<=self.maximum_age_s)
        return False


class NavigationPositionHandoff:
    """Aggregate protection intervals, then finite navigation gain recovery.

    Forecast freshness expirations are truncated by actual veto events. Queries
    never mutate timing, so tags and rejected ranges cannot restart recovery.
    """
    def __init__(self, recovery_s=.125):
        if not math.isfinite(recovery_s) or recovery_s<=0:raise ValueError('invalid recovery interval')
        self.recovery_s=recovery_s;self.intervals=deque(maxlen=64);self.last_epoch=None

    def advance(self,epoch,protected_until):
        if not math.isfinite(epoch) or (self.last_epoch is not None and epoch<self.last_epoch):
            raise ValueError('handoff chronology reversed')
        self.last_epoch=epoch
        if self.intervals and self.intervals[-1][1]>epoch:
            start,_=self.intervals.pop();self.intervals.append((start,epoch))
        if protected_until>epoch:
            if self.intervals and abs(self.intervals[-1][1]-epoch)<=1e-9:
                start,_=self.intervals.pop();self.intervals.append((start,protected_until))
            else:self.intervals.append((epoch,protected_until))

    def gain(self,query):
        for start,end in reversed(self.intervals):
            if start<=query+1e-9:
                return min(1.,max(0.,(query-end)/self.recovery_s))
        return 1.


class ContactLifecycle:
    RELEASED, QUIET, BRIDGED, SEATED_STATIONARY = range(4)

    def __init__(self, confirmation_s=.125, maximum_age_s=.0075):
        if not all(math.isfinite(v) and v > 0 for v in (confirmation_s, maximum_age_s)):
            raise ValueError('lifecycle intervals must be finite positive')
        self.confirmation_s = confirmation_s
        self.maximum_age_s = maximum_age_s
        self.last_epoch = None
        self.quiet_since = None
        self.last_direct = None
        self.last_confidence = 0.
        self.mode = self.RELEASED
        self.reason = 0

    def update(self, epoch, raw_class, confidence, *, fresh, quiet_motion, lifted, seated_context):
        if not math.isfinite(epoch) or (self.last_epoch is not None and epoch < self.last_epoch):
            raise ValueError('invalid lifecycle epoch')
        if self.last_epoch == epoch:
            return self.mode, self.last_confidence
        gap = self.last_epoch is not None and epoch-self.last_epoch > self.maximum_age_s
        self.last_epoch = epoch
        if gap:
            self.quiet_since = self.last_direct = None
        veto = not fresh or lifted or raw_class in (1, 2)
        if veto or not quiet_motion:
            self.quiet_since = self.last_direct = None
            self.mode, self.last_confidence = self.RELEASED, 0.
            self.reason = 1 if not fresh else 3 if lifted else 4 if raw_class == 1 else 5 if raw_class == 2 else 2
            return self.mode, 0.
        if self.quiet_since is None:
            self.quiet_since = epoch
        if raw_class == 0:
            self.reason = 0
            self.mode = self.QUIET
            self.last_direct = epoch
            self.last_confidence = confidence
        elif seated_context and epoch-self.quiet_since >= self.confirmation_s:
            self.reason = 0
            # This is zero world ankle velocity evidence, not a sole-contact
            # label: the moving opposite leg may supply seated posture context.
            self.mode = self.SEATED_STATIONARY
            self.last_direct = epoch
            self.last_confidence = .5
        elif raw_class == -1 and self.last_direct is not None and epoch-self.last_direct < self.confirmation_s:
            self.reason = 0
            self.mode = self.BRIDGED
            # UNKNOWN never refreshes last_direct or adds held-sample evidence.
            self.last_confidence = min(self.last_confidence, 1-((epoch-self.last_direct)/self.confirmation_s))
        else:
            self.mode, self.last_confidence = self.RELEASED, 0.
            self.reason = 6 if seated_context else 7 if raw_class == -1 else 8
        return self.mode, self.last_confidence
