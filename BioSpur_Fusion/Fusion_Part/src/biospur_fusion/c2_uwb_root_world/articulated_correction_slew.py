"""Causal publication rate limit for accepted articulated corrections.

The articulated estimator remains authoritative and may accept a correction at
one UWB availability epoch.  This owner only controls how that already accepted
target is exposed by the native-200 Hz trajectory.  It never feeds a smoothed
value back into ranging, contact, IK, or the estimator state.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

import numpy as np
from scipy.spatial.transform import Rotation


@dataclass(frozen=True)
class ArticulatedCorrectionSlewSample:
    correction_rotvec: Mapping[str, np.ndarray]
    step_maximum_rad: float
    active: bool


class CausalArticulatedCorrectionSlew:
    """Rate-limit emitted SO(3) corrections without changing their target."""

    def __init__(
        self,
        *,
        segments: Sequence[str],
        release_period_s: float,
        maximum_target_norm_rad: float,
    ) -> None:
        inventory = tuple(segments)
        if not inventory or len(set(inventory)) != len(inventory):
            raise ValueError("articulated publication inventory is invalid")
        if not math.isfinite(release_period_s) or release_period_s <= 0.0:
            raise ValueError("articulated release period must be positive")
        if (
            not math.isfinite(maximum_target_norm_rad)
            or maximum_target_norm_rad <= 0.0
        ):
            raise ValueError("articulated correction cap must be positive")
        self.segments = inventory
        self.release_period_s = float(release_period_s)
        self.maximum_target_norm_rad = float(maximum_target_norm_rad)
        self._latest_time_s = -math.inf
        self._published: dict[str, np.ndarray] | None = None

    def _target(self, values: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
        if set(values) != set(self.segments):
            raise ValueError("articulated publication inventory mismatch")
        target = {
            segment: np.asarray(values[segment], dtype=float).reshape(3).copy()
            for segment in self.segments
        }
        if not all(np.isfinite(value).all() for value in target.values()):
            raise ValueError("articulated publication target must be finite")
        if max(np.linalg.norm(value) for value in target.values()) > (
            self.maximum_target_norm_rad + 1e-9
        ):
            raise ValueError("articulated publication target exceeds cap")
        return target

    def sample(
        self,
        time_s: float,
        target_correction_rotvec: Mapping[str, np.ndarray],
    ) -> ArticulatedCorrectionSlewSample:
        query = float(time_s)
        if not math.isfinite(query):
            raise ValueError("articulated publication time must be finite")
        if query + 1e-12 < self._latest_time_s:
            raise ValueError("articulated publication reversed time")
        target = self._target(target_correction_rotvec)
        if self._published is None:
            self._published = target
            self._latest_time_s = query
            return ArticulatedCorrectionSlewSample(
                correction_rotvec={
                    segment: value.copy() for segment, value in target.items()
                },
                step_maximum_rad=0.0,
                active=False,
            )

        dt = max(0.0, query - self._latest_time_s)
        maximum_step = (
            2.0 * self.maximum_target_norm_rad * dt / self.release_period_s
        )
        published: dict[str, np.ndarray] = {}
        steps = []
        remaining = []
        for segment in self.segments:
            origin = Rotation.from_rotvec(self._published[segment])
            delta = origin.inv() * Rotation.from_rotvec(target[segment])
            distance = float(delta.magnitude())
            step = min(distance, maximum_step)
            fraction = 0.0 if distance <= 1e-15 else step / distance
            published[segment] = (
                origin * Rotation.from_rotvec(delta.as_rotvec() * fraction)
            ).as_rotvec()
            steps.append(step)
            remaining.append(distance - step)
        self._published = published
        self._latest_time_s = max(self._latest_time_s, query)
        return ArticulatedCorrectionSlewSample(
            correction_rotvec={
                segment: value.copy() for segment, value in published.items()
            },
            step_maximum_rad=max(steps, default=0.0),
            active=max(remaining, default=0.0) > 1e-12,
        )
