"""Bounded diagnostic joint-state history; not a smoother or output filter.

The caller supplies the actual committed error map, not a map reconstructed
from covariance ratios. Transition cross covariance describes the implemented
error recursion; it is not automatically a Bayesian smoothing cross covariance
for constrained/Schmidt measurement updates.
"""
from collections import deque
from dataclasses import dataclass

import numpy as np


def notify_transition(observer, mapping, before, after, kind, *, measurement=None):
    """Preserve the old observer contract and optionally record typed evidence."""
    owner = getattr(observer, '__self__', None)
    recorder = getattr(owner, 'record_root_transition', None)
    if recorder is not None:
        recorder(mapping, before, after, kind, measurement=measurement)
    observer(mapping)


@dataclass(frozen=True)
class JointSnapshot:
    time_s: float
    mean: np.ndarray
    covariance: np.ndarray
    episodes: tuple[tuple[int, int], ...]

    @classmethod
    def capture(cls, time_s, mean, covariance, episodes=()):
        mean = np.array(mean, dtype=float, copy=True)
        covariance = np.array(covariance, dtype=float, copy=True)
        episodes = tuple(episodes)
        width = len(mean)
        if (not np.isfinite(time_s) or mean.ndim != 1
                or width != 9 + 3 * len(episodes)
                or covariance.shape != (width, width)
                or not np.isfinite(mean).all() or not np.isfinite(covariance).all()
                or len(set(episodes)) != len(episodes)
                or len({side for side, _ in episodes}) != len(episodes)
                or any(side not in (0, 1) or generation < 0
                       for side, generation in episodes)
                or not np.allclose(covariance, covariance.T, atol=1e-12, rtol=1e-12)):
            raise ValueError('invalid joint snapshot')
        np.linalg.cholesky(covariance)
        mean.setflags(write=False)
        covariance.setflags(write=False)
        return cls(float(time_s), mean, covariance, episodes)


@dataclass(frozen=True)
class JointTransition:
    kind: str
    before: JointSnapshot
    after: JointSnapshot
    error_map: np.ndarray
    error_cross: np.ndarray


class JointTransitionTape:
    """At most one bounded horizon plus an explicit finite record cap.

    Call only after commit. Disabled mode does not inspect or copy inputs.
    Prediction uses F; assimilation uses the actual I-KH; topology uses the
    explicit augmentation/marginal-selection map. Independent additive noise
    has zero cross with the previous error. Callers must attest that condition.
    """
    def __init__(self, horizon_s=.12, *, enabled=False, maximum_records=256):
        if not np.isfinite(horizon_s) or horizon_s <= 0 or maximum_records < 1:
            raise ValueError('invalid history bounds')
        self.horizon_s = float(horizon_s)
        self.enabled = bool(enabled)
        self.maximum_records = int(maximum_records)
        self.records = deque()
        self.capacity_dropped = 0

    def commit(self, kind, before, after, error_map, *, independent_noise, measurement=None):
        if not self.enabled:
            return
        if kind not in ('prediction', 'assimilation', 'topology') or not independent_noise:
            raise ValueError('explicit transition kind and independent noise required')
        if after.time_s < before.time_s:
            raise ValueError('reversed transition')
        if kind != 'prediction' and after.time_s != before.time_s:
            raise ValueError('measurement/topology must share an epoch')
        if kind != 'topology' and before.episodes != after.episodes:
            raise ValueError('episode change requires topology event')
        if self.records:
            previous = self.records[-1].after
            if (previous.time_s != before.time_s or previous.episodes != before.episodes
                    or not np.array_equal(previous.mean, before.mean)
                    or not np.array_equal(previous.covariance, before.covariance)):
                raise ValueError('unrecorded committed state transition')
        mapping = np.array(error_map, dtype=float, copy=True)
        if mapping.shape != (len(after.mean), len(before.mean)) or not np.isfinite(mapping).all():
            raise ValueError('invalid explicit error map')
        cross = before.covariance @ mapping.T
        mapping.setflags(write=False)
        cross.setflags(write=False)
        self.records.append(JointTransition(kind, before, after, mapping, cross))
        cutoff = after.time_s - self.horizon_s
        while self.records and self.records[0].after.time_s < cutoff:
            self.records.popleft()
        while len(self.records) > self.maximum_records:
            self.records.popleft()
            self.capacity_dropped += 1

    def diagnostic(self):
        def snapshot(value):
            return dict(time_s=value.time_s,mean=value.mean.tolist(),
                        covariance=value.covariance.tolist(),episodes=value.episodes)
        return dict(scope='ERROR_RECURSION_TAPE_NOT_RTS_OR_SLIP_REPAIR',
                    horizon_s=self.horizon_s,capacity_dropped=self.capacity_dropped,
                    records=[dict(kind=r.kind,before=snapshot(r.before),after=snapshot(r.after),
                                  error_map=r.error_map.tolist(),error_cross=r.error_cross.tolist())
                             for r in self.records])
