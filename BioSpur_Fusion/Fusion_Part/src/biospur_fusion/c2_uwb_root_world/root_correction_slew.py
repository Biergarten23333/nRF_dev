"""Causal publication smoothing for accepted UWB root corrections.

The root filter owns estimation and outlier rejection.  This module owns only
how an already-accepted, instantaneous position correction is exposed to the
200 Hz body trajectory.  It withholds the correction at its availability
epoch and releases it linearly over one configured period, preserving the
filter posterior while avoiding a non-physical one-frame body translation.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


@dataclass(frozen=True)
class RootCorrectionSlewSample:
    position_m: np.ndarray
    velocity_mps: np.ndarray
    withheld_correction_m: np.ndarray
    release_velocity_mps: np.ndarray
    active_correction_count: int

    def __post_init__(self) -> None:
        for value in (
            self.position_m,
            self.velocity_mps,
            self.withheld_correction_m,
            self.release_velocity_mps,
        ):
            value.setflags(write=False)


@dataclass(frozen=True)
class _Correction:
    availability_time_s: float
    delta_m: np.ndarray


class CausalRootCorrectionSlew:
    """Release accepted root corrections without changing the filter state."""

    def __init__(
        self,
        *,
        release_period_s: float,
        maximum_correction_m: float,
    ) -> None:
        if not math.isfinite(release_period_s) or release_period_s <= 0.0:
            raise ValueError("release period must be finite and positive")
        if not math.isfinite(maximum_correction_m) or maximum_correction_m <= 0.0:
            raise ValueError("maximum correction must be finite and positive")
        self.release_period_s = float(release_period_s)
        self.maximum_correction_m = float(maximum_correction_m)
        self.maximum_release_speed_mps = (
            self.maximum_correction_m / self.release_period_s
        )
        self._pending: list[_Correction] = []
        self._withheld_correction_m = np.zeros(3)
        self._latest_event_time_s = -math.inf
        self._latest_sample_time_s = -math.inf
        self._installed_count = 0
        self._active_correction_count = 0

    @property
    def installed_count(self) -> int:
        return self._installed_count

    def _validate_event_time(self, time_s: float) -> float:
        value = float(time_s)
        if not math.isfinite(value):
            raise ValueError("slew time must be finite")
        if value + 1e-12 < max(
            self._latest_event_time_s, self._latest_sample_time_s
        ):
            raise ValueError("root correction slew reversed time")
        self._latest_event_time_s = max(self._latest_event_time_s, value)
        return value

    def _release(self, duration_s: float) -> np.ndarray:
        distance = float(np.linalg.norm(self._withheld_correction_m))
        maximum = self.maximum_release_speed_mps * max(0.0, duration_s)
        amount = min(distance, maximum)
        if distance <= 1e-15 or amount <= 0.0:
            return np.zeros(3)
        released = self._withheld_correction_m * (amount / distance)
        self._withheld_correction_m -= released
        if np.linalg.norm(self._withheld_correction_m) <= 1e-14:
            self._withheld_correction_m[:] = 0.0
            self._active_correction_count = 0
        return released

    def install(
        self,
        availability_time_s: float,
        correction_delta_m: np.ndarray,
        *,
        enforce_filter_influence_cap: bool = True,
    ) -> None:
        """Register one position correction already accepted by the root filter."""

        if type(enforce_filter_influence_cap) is not bool:
            raise ValueError("influence-cap ownership flag must be bool")
        delta = np.asarray(correction_delta_m, dtype=float).reshape(3).copy()
        if not np.isfinite(delta).all():
            raise ValueError("root correction must be finite")
        if (
            enforce_filter_influence_cap
            and np.linalg.norm(delta) > self.maximum_correction_m + 1e-10
        ):
            raise ValueError("accepted root correction exceeds the filter influence cap")
        availability = self._validate_event_time(availability_time_s)
        if np.any(delta != 0.0):
            delta.setflags(write=False)
            self._pending.append(_Correction(availability, delta))
            self._installed_count += 1

    def sample(
        self,
        time_s: float,
        posterior_position_m: np.ndarray,
        posterior_velocity_mps: np.ndarray,
    ) -> RootCorrectionSlewSample:
        """Return the causal continuous publication at one 200 Hz output time."""

        position = np.asarray(posterior_position_m, dtype=float).reshape(3)
        velocity = np.asarray(posterior_velocity_mps, dtype=float).reshape(3)
        if not np.isfinite(position).all() or not np.isfinite(velocity).all():
            raise ValueError("posterior root state must be finite")
        query = float(time_s)
        if not math.isfinite(query):
            raise ValueError("slew time must be finite")
        if query + 1e-12 < max(
            self._latest_event_time_s, self._latest_sample_time_s
        ):
            raise ValueError("root correction slew reversed time")
        cursor = (
            query if self._latest_sample_time_s == -math.inf
            else self._latest_sample_time_s
        )
        released = np.zeros(3)
        for correction in self._pending:
            if correction.availability_time_s > query + 1e-12:
                raise RuntimeError("future correction entered causal publication")
            released += self._release(correction.availability_time_s - cursor)
            cursor = correction.availability_time_s
            self._withheld_correction_m += correction.delta_m
            self._active_correction_count += 1
        self._pending.clear()
        released += self._release(query - cursor)
        dt = (
            0.0 if self._latest_sample_time_s == -math.inf
            else query - self._latest_sample_time_s
        )
        release_velocity = (
            np.zeros(3) if dt <= 1e-15 else released / dt
        )
        if self._active_correction_count == 0:
            release_velocity = np.zeros(3)
        self._latest_sample_time_s = query
        self._latest_event_time_s = max(self._latest_event_time_s, query)
        return RootCorrectionSlewSample(
            position_m=(position - self._withheld_correction_m).copy(),
            velocity_mps=(velocity + release_velocity).copy(),
            withheld_correction_m=self._withheld_correction_m.copy(),
            release_velocity_mps=release_velocity,
            active_correction_count=self._active_correction_count,
        )
