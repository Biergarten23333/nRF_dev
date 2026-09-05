"""Causal owner for the final C2 articulated pose used by root/contact fusion.

An articulated correction is learned at a UWB measurement epoch but cannot be
used until that measurement is available.  This owner holds the latest
available correction, re-expresses it against each current analytic base via
the public biomechanics projector, and evaluates the same FK tree for every
200 Hz contact/root consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry

from .articulated_range import SEGMENTS, corrected_proxy_points


HingeProjector = Callable[
    [Mapping[str, np.ndarray], Mapping[str, np.ndarray]],
    tuple[dict[str, np.ndarray], dict[str, Any]],
]


@dataclass(frozen=True)
class CausalArticulatedPoseSample:
    time_s: float
    fraction: float
    correction_rotvec: Mapping[str, np.ndarray]
    points_root_m: Mapping[str, np.ndarray]
    ankle_offset_world_m: Mapping[str, np.ndarray]
    ankle_offset_velocity_world_mps: Mapping[str, np.ndarray]
    projection: Mapping[str, Any]
    velocity_baseline_reset: bool
    transition_active: bool
    transition_step_maximum_rad: float


class CausalArticulatedPose:
    """Hold and evaluate only corrections already available at query time."""

    def __init__(
        self,
        *,
        action_start_s: float,
        action_stop_s: float,
        rotations_at_fraction: Callable[[float], Mapping[str, np.ndarray]],
        geometry: DisplayProxyGeometry,
        hinge_projector: HingeProjector | None,
        derivative_period_s: float = 0.005,
        transition_period_s: float = 0.12,
    ) -> None:
        if not (
            math.isfinite(action_start_s)
            and math.isfinite(action_stop_s)
            and action_stop_s > action_start_s
            and math.isfinite(derivative_period_s)
            and derivative_period_s > 0.0
            and math.isfinite(transition_period_s)
            and transition_period_s >= derivative_period_s
        ):
            raise ValueError("causal articulated pose timing is invalid")
        self.action_start_s = float(action_start_s)
        self.action_stop_s = float(action_stop_s)
        self.duration_s = self.action_stop_s - self.action_start_s
        self.rotations_at_fraction = rotations_at_fraction
        self.geometry = geometry
        self.hinge_projector = hinge_projector
        self.derivative_period_s = float(derivative_period_s)
        self.transition_period_s = float(transition_period_s)
        self._transition_origin_correction = {
            segment: np.zeros(3) for segment in SEGMENTS
        }
        self._target_correction = {
            segment: np.zeros(3) for segment in SEGMENTS
        }
        self._transition_start_s = self.action_start_s
        self._latest_availability_s = -math.inf
        self._latest_sample_s = -math.inf
        self._install_count = 0
        self._velocity_baseline_reset_pending = False

    @property
    def install_count(self) -> int:
        return self._install_count

    @property
    def latest_availability_s(self) -> float:
        return self._latest_availability_s

    def transition_snapshot(self) -> dict[str, Any]:
        """Return an immutable copy of the current causal transition owner."""

        delta_maximum = max(
            float((
                Rotation.from_rotvec(
                    self._transition_origin_correction[segment]
                ).inv()
                * Rotation.from_rotvec(self._target_correction[segment])
            ).magnitude())
            for segment in SEGMENTS
        )
        return {
            "start_time_s": self._transition_start_s,
            "period_s": self.transition_period_s,
            "origin_correction": {
                segment: value.copy()
                for segment, value in self._transition_origin_correction.items()
            },
            "target_correction": {
                segment: value.copy()
                for segment, value in self._target_correction.items()
            },
            "target_delta_maximum_rad": delta_maximum,
        }

    def _fraction(self, time_s: float) -> float:
        if not math.isfinite(float(time_s)):
            raise ValueError("pose query time must be finite")
        return float(np.clip(
            (float(time_s) - self.action_start_s) / self.duration_s,
            0.0,
            1.0,
        ))

    @staticmethod
    def _copy_correction(
        correction: Mapping[str, np.ndarray],
    ) -> dict[str, np.ndarray]:
        if set(correction) != set(SEGMENTS):
            raise ValueError("articulated correction inventory mismatch")
        copied = {
            segment: np.asarray(correction[segment], dtype=float).reshape(3).copy()
            for segment in SEGMENTS
        }
        if not all(np.isfinite(value).all() for value in copied.values()):
            raise ValueError("articulated correction must be finite")
        return copied

    def _evaluate(
        self,
        time_s: float,
        correction: Mapping[str, np.ndarray],
    ) -> tuple[
        dict[str, np.ndarray],
        dict[str, np.ndarray],
        dict[str, Any],
    ]:
        fraction = self._fraction(time_s)
        base = {
            segment: np.asarray(value, dtype=float).reshape(3, 3)
            for segment, value in self.rotations_at_fraction(fraction).items()
        }
        carried = self._copy_correction(correction)
        if self.hinge_projector is None:
            projected = carried
            projection: dict[str, Any] = {}
        else:
            projected, projection = self.hinge_projector(base, carried)
            projected = self._copy_correction(projected)
            if projection.get("post_projection_all_inside_rom") is not True:
                raise RuntimeError("current 200 Hz pose projection left hinge ROM")
        points = corrected_proxy_points(base, projected, self.geometry)
        return projected, points, dict(projection)

    def _interpolated_correction(
        self, time_s: float
    ) -> dict[str, np.ndarray]:
        """Evaluate the fixed transition independently of query partitioning."""

        alpha = float(np.clip(
            (float(time_s) - self._transition_start_s)
            / self.transition_period_s,
            0.0,
            1.0,
        ))
        interpolated: dict[str, np.ndarray] = {}
        for segment in SEGMENTS:
            origin = Rotation.from_rotvec(
                self._transition_origin_correction[segment]
            )
            delta = origin.inv() * Rotation.from_rotvec(
                self._target_correction[segment]
            )
            interpolated[segment] = (
                origin * Rotation.from_rotvec(delta.as_rotvec() * alpha)
            ).as_rotvec()
        return interpolated

    def _transition_pose(
        self, time_s: float
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, Any]]:
        return self._evaluate(time_s, self._interpolated_correction(time_s))

    def install(
        self,
        correction_at_measurement: Mapping[str, np.ndarray],
        *,
        measurement_time_s: float,
        availability_time_s: float,
    ) -> None:
        """Install one correction only when its UWB observation is available."""

        measurement = float(measurement_time_s)
        availability = float(availability_time_s)
        if not (
            math.isfinite(measurement)
            and math.isfinite(availability)
            and availability + 1e-12 >= measurement
            and availability + 1e-12 >= self._latest_availability_s
            and availability + 1e-12 >= self._latest_sample_s
        ):
            raise ValueError("articulated correction availability reversed time")
        measurement_correction, _points, _projection = self._evaluate(
            measurement, correction_at_measurement
        )
        available_target, _points, _projection = self._evaluate(
            availability, measurement_correction
        )
        # Before the first emitted state there is no trajectory continuity to
        # preserve, so the bootstrap observation may initialize the owner.
        # Every later target only starts moving after its availability epoch.
        if self._install_count == 0 and self._latest_sample_s == -math.inf:
            current_at_availability = {
                segment: value.copy()
                for segment, value in available_target.items()
            }
        else:
            current_at_availability, _points, _projection = (
                self._transition_pose(availability)
            )
        self._transition_origin_correction = {
            segment: value.copy()
            for segment, value in current_at_availability.items()
        }
        self._target_correction = {
            segment: value.copy()
            for segment, value in available_target.items()
        }
        self._transition_start_s = availability
        self._latest_availability_s = availability
        self._install_count += 1
        self._velocity_baseline_reset_pending = True

    def ankle_offsets_for_published_correction(
        self,
        time_s: float,
        correction_rotvec: Mapping[str, np.ndarray],
    ) -> dict[str, np.ndarray]:
        """Evaluate an already-published correction on the exact-time base.

        The caller owns the causal history and must supply a correction that
        was published no later than ``time_s``.  This pure query does not
        install a target, advance the transition, or alter sampling state.
        """

        offsets, _velocity = self.ankle_kinematics_for_published_correction(
            time_s, correction_rotvec
        )
        return offsets

    def ankle_kinematics_for_published_correction(
        self,
        time_s: float,
        correction_rotvec: Mapping[str, np.ndarray],
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        """Evaluate one published correction's ankle pose and base velocity.

        Both current and prior points use the same correction owner.  The
        resulting velocity therefore contains native-base motion but no
        correction-transition jump, which lets root re-gauging isolate only
        the change between two published correction gauges.
        """

        query = float(time_s)
        projected, points, _projection = self._evaluate(
            query, correction_rotvec
        )
        prior_time = max(self.action_start_s, query - self.derivative_period_s)
        if query - prior_time <= 1e-12:
            prior_points = points
            dt = self.derivative_period_s
        else:
            _prior, prior_points, _projection = self._evaluate(
                prior_time, projected
            )
            dt = query - prior_time
        ankle_points = {"left": "ankle_left", "right": "ankle_right"}
        offsets = {
            side: np.asarray(points[name], dtype=float).copy()
            for side, name in ankle_points.items()
        }
        velocity = {
            side: (
                np.asarray(points[name], dtype=float)
                - np.asarray(prior_points[name], dtype=float)
            ) / dt
            for side, name in ankle_points.items()
        }
        return offsets, velocity

    def sample(self, time_s: float) -> CausalArticulatedPoseSample:
        """Return one current-base final pose without using future UWB state."""

        query = float(time_s)
        if query + 1e-12 < self._latest_availability_s:
            raise ValueError("pose requested before installed UWB availability")
        if query + 1e-12 < self._latest_sample_s:
            raise ValueError("causal articulated pose query reversed time")
        projected, points, projection = self._transition_pose(query)
        prior_time = max(self.action_start_s, query - self.derivative_period_s)
        if query - prior_time <= 1e-12:
            prior_points = points
            dt = self.derivative_period_s
        else:
            _prior_correction, prior_points, _prior_projection = self._evaluate(
                prior_time, projected
            )
            dt = query - prior_time
        prior_transition = self._interpolated_correction(prior_time)
        current_transition = self._interpolated_correction(query)
        transition_steps = [
            float((
                Rotation.from_rotvec(prior_transition[segment]).inv()
                * Rotation.from_rotvec(current_transition[segment])
            ).magnitude())
            for segment in SEGMENTS
        ]
        ankle_points = {"left": "ankle_left", "right": "ankle_right"}
        offsets = {
            side: np.asarray(points[name], dtype=float).copy()
            for side, name in ankle_points.items()
        }
        velocities = {
            side: (
                np.asarray(points[name], dtype=float)
                - np.asarray(prior_points[name], dtype=float)
            ) / dt
            for side, name in ankle_points.items()
        }
        self._latest_sample_s = max(self._latest_sample_s, query)
        baseline_reset = self._velocity_baseline_reset_pending
        self._velocity_baseline_reset_pending = False
        return CausalArticulatedPoseSample(
            time_s=query,
            fraction=self._fraction(query),
            correction_rotvec={
                segment: value.copy() for segment, value in projected.items()
            },
            points_root_m={name: value.copy() for name, value in points.items()},
            ankle_offset_world_m=offsets,
            ankle_offset_velocity_world_mps=velocities,
            projection=projection,
            velocity_baseline_reset=baseline_reset,
            transition_active=bool(max(transition_steps, default=0.0) > 1e-12),
            transition_step_maximum_rad=max(transition_steps, default=0.0),
        )
