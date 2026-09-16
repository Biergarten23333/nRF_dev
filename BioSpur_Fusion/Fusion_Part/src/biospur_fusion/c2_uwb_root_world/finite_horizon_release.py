"""Finite-duration publication of accepted corrections; never changes estimation.

Unlike the legacy global speed cap, each correction expires independently.
Overlapping contributions add. This bounds delay, not physical speed or error.
"""
from collections import deque
import math

import numpy as np

from .root_correction_slew import RootCorrectionSlewSample


class FiniteHorizonRootRelease:
    def __init__(self, release_period_s: float = .12):
        if not math.isfinite(release_period_s) or release_period_s <= 0:
            raise ValueError("release period must be positive and finite")
        self.period = float(release_period_s)
        self._events = deque()
        self._time = -math.inf

    def _advance(self, time_s):
        if not math.isfinite(time_s) or time_s < self._time:
            raise ValueError("publication time must be finite and monotonic")
        self._time = float(time_s)
        while self._events and self._events[0][0] + self.period <= time_s:
            self._events.popleft()

    def install(self, time_s, delta_m):
        delta = np.asarray(delta_m, dtype=float)
        if delta.shape != (3,) or not np.isfinite(delta).all():
            raise ValueError("correction must be a finite 3-vector")
        self._advance(time_s)
        if np.any(delta):
            self._events.append((float(time_s), delta.copy()))

    def sample(self, time_s, posterior_position_m, posterior_velocity_mps):
        p = np.asarray(posterior_position_m, dtype=float)
        v = np.asarray(posterior_velocity_mps, dtype=float)
        if p.shape != (3,) or v.shape != (3,) or not np.isfinite(p).all() or not np.isfinite(v).all():
            raise ValueError("posterior must contain finite position and velocity")
        self._advance(time_s)
        withheld, release_velocity = np.zeros(3), np.zeros(3)
        for epoch, delta in self._events:
            withheld += (1 - (time_s - epoch) / self.period) * delta
            release_velocity += delta / self.period
        return RootCorrectionSlewSample(
            p - withheld, v + release_velocity, withheld,
            release_velocity, len(self._events))
