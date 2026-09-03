"""Capture-wide complete-S1 posterior for unresolved distal longitudinal axes."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

import numpy as np


DISTAL_SEGMENTS = (
    "forearm_left", "forearm_right", "shank_left", "shank_right",
)


def _array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return sha256(array.view(np.uint8)).hexdigest()


def _normalize_positive_weights(value: np.ndarray) -> np.ndarray:
    weights = np.asarray(value, dtype=float)
    if (
        weights.ndim != 1
        or len(weights) < 3
        or not np.isfinite(weights).all()
        or np.any(weights <= 0.0)
    ):
        raise ValueError("complete-S1 prior weights must be one finite positive vector")
    return weights / float(np.sum(weights))


def _validate_complete_s1_grid(value: np.ndarray) -> np.ndarray:
    grid = np.asarray(value, dtype=float)
    if grid.ndim != 1 or len(grid) < 3 or not np.isfinite(grid).all():
        raise ValueError("distal longitudinal grid must be one finite complete-S1 vector")
    step = np.diff(grid)
    if (
        np.any(step <= 0.0)
        or not np.allclose(step, step[0], atol=1e-12, rtol=1e-10)
        or not np.isclose(grid[-1] - grid[0] + step[0], 2.0 * np.pi, atol=1e-10)
    ):
        raise ValueError("distal longitudinal grid must cover one equidistant complete S1")
    return grid


def _entropy(weights: np.ndarray) -> float:
    values = np.asarray(weights, dtype=float)
    return float(-np.sum(values * np.log(values)))


def complete_s1_sensor_from_segment_candidates(
    *,
    signed_hinge_axis_sensor: np.ndarray,
    delta_grid_rad: np.ndarray,
) -> np.ndarray:
    """Return a result-independent complete S1 of proper distal frames."""
    grid = _validate_complete_s1_grid(delta_grid_rad)
    axis = np.asarray(signed_hinge_axis_sensor, dtype=float)
    if axis.shape != (3,) or not np.isfinite(axis).all() or np.linalg.norm(axis) <= 1e-12:
        raise ValueError("complete-S1 distal hinge axis must be one finite nonzero R3 vector")
    axis = axis / np.linalg.norm(axis)
    seed = np.eye(3)[int(np.argmin(np.abs(axis)))]
    z0 = seed - axis * float(axis @ seed)
    z0 /= np.linalg.norm(z0)
    x0 = np.cross(axis, z0)
    rows = []
    for angle in grid:
        z = z0 * np.cos(angle) + x0 * np.sin(angle)
        x = np.cross(axis, z)
        x /= np.linalg.norm(x)
        z = np.cross(x, axis)
        rows.append(np.column_stack((x, axis, z)))
    result = np.asarray(rows, dtype=float)
    if (
        not np.allclose(
            np.einsum("nji,njk->nik", result, result),
            np.broadcast_to(np.eye(3), result.shape),
            atol=1e-12,
            rtol=0.0,
        )
        or not np.allclose(np.linalg.det(result), 1.0, atol=1e-12, rtol=0.0)
    ):
        raise RuntimeError("complete-S1 distal candidate construction is not proper SO(3)")
    return result


def recompute_distal_motion_through_candidate_coordinates(
    *,
    candidate_sensor_from_segment: np.ndarray,
    world_from_sensor: np.ndarray,
    accelerometer_sensor_mps2: np.ndarray,
    angular_velocity_sensor_rads: np.ndarray,
    angular_acceleration_sensor_rads2: np.ndarray,
    sensor_to_joint_lever_m: np.ndarray,
    signed_hinge_axis_sensor: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Recompute the same motion after each distal coordinate mutation.

    The returned arrays have shapes ``(candidate,row,3)`` for joint specific
    force, angular velocity, and hinge direction in world coordinates.  This
    is an executable invariance check, not a distal pose estimator.
    """
    candidates = np.asarray(candidate_sensor_from_segment, dtype=float)
    world_sensor = np.asarray(world_from_sensor, dtype=float)
    acc = np.asarray(accelerometer_sensor_mps2, dtype=float)
    omega = np.asarray(angular_velocity_sensor_rads, dtype=float)
    alpha = np.asarray(angular_acceleration_sensor_rads2, dtype=float)
    lever = np.asarray(sensor_to_joint_lever_m, dtype=float)
    axis = np.asarray(signed_hinge_axis_sensor, dtype=float)
    row_count = len(acc)
    if (
        candidates.ndim != 3
        or candidates.shape[1:] != (3, 3)
        or world_sensor.shape != (row_count, 3, 3)
        or acc.shape != (row_count, 3)
        or omega.shape != (row_count, 3)
        or alpha.shape != (row_count, 3)
        or lever.shape != (3,)
        or axis.shape != (3,)
        or not row_count
        or not all(np.isfinite(value).all() for value in (
            candidates, world_sensor, acc, omega, alpha, lever, axis,
        ))
    ):
        raise ValueError("distal coordinate mutation inputs are invalid")
    joint_rows = []
    omega_rows = []
    axis_rows = []
    for sensor_from_segment in candidates:
        segment_from_sensor = sensor_from_segment.T
        world_from_segment = np.einsum(
            "nij,jk->nik", world_sensor, sensor_from_segment,
        )
        acc_segment = np.einsum("ij,nj->ni", segment_from_sensor, acc)
        omega_segment = np.einsum("ij,nj->ni", segment_from_sensor, omega)
        alpha_segment = np.einsum("ij,nj->ni", segment_from_sensor, alpha)
        lever_segment = segment_from_sensor @ lever
        axis_segment = segment_from_sensor @ axis
        joint_segment = (
            acc_segment
            + np.cross(alpha_segment, lever_segment)
            + np.cross(
                omega_segment,
                np.cross(omega_segment, lever_segment),
            )
        )
        joint_rows.append(np.einsum("nij,nj->ni", world_from_segment, joint_segment))
        omega_rows.append(np.einsum("nij,nj->ni", world_from_segment, omega_segment))
        axis_rows.append(np.einsum("nij,j->ni", world_from_segment, axis_segment))
    return (
        np.asarray(joint_rows, dtype=float),
        np.asarray(omega_rows, dtype=float),
        np.asarray(axis_rows, dtype=float),
    )


def complete_s1_prior_binding(
    *,
    segment: str,
    delta_grid_rad: np.ndarray,
    registered_prior_weights: np.ndarray,
    source_authority_sha256: str,
) -> Mapping[str, Any]:
    """Bind the exact normalized arrays consumed by the distal owner."""
    if segment not in DISTAL_SEGMENTS:
        raise ValueError("complete-S1 distal prior accepts only forearms and shanks")
    grid = _validate_complete_s1_grid(delta_grid_rad)
    prior = _normalize_positive_weights(registered_prior_weights)
    if len(grid) != len(prior):
        raise ValueError("complete-S1 grid and registered prior lengths differ")
    if not source_authority_sha256:
        raise ValueError("complete-S1 distal prior requires one source authority hash")
    return {
        "schema": "biospur-c2-distal-longitudinal-complete-s1-prior-binding-v1",
        "segment": segment,
        "delta_grid_rad_sha256": _array_sha256(grid),
        "registered_prior_weights_sha256": _array_sha256(prior),
        "source_authority_sha256": str(source_authority_sha256),
    }


@dataclass(frozen=True)
class DistalLongitudinalPosterior:
    segment: str
    delta_grid_rad: np.ndarray
    prior_weights: np.ndarray
    posterior_weights: np.ndarray
    action_count: int
    report: Mapping[str, Any]


class CompleteS1DistalLongitudinalOwner:
    """Retain a static distal-axis S1 posterior without inventing pose evidence.

    A single proximal joint center plus a qualified hinge axis leaves rotation
    about that axis as a gauge.  Joint-acceleration equality and the transverse
    hinge-motion residual are useful model checks, but both are invariant to
    this coordinate.  This owner therefore evaluates and hash-binds those
    complete-motion arrays while contributing a constant likelihood across
    S1.  A broad registered mount/wear prior remains broad; duplication of the
    same gauge-invariant motion cannot create false concentration.
    """

    def __init__(
        self,
        *,
        segment: str,
        delta_grid_rad: np.ndarray,
        registered_prior_weights: np.ndarray,
        prior_binding: Mapping[str, Any],
    ) -> None:
        if segment not in DISTAL_SEGMENTS:
            raise ValueError("complete-S1 distal owner accepts only forearms and shanks")
        grid = _validate_complete_s1_grid(delta_grid_rad)
        prior = _normalize_positive_weights(registered_prior_weights)
        if len(grid) != len(prior):
            raise ValueError("complete-S1 grid and registered prior lengths differ")
        binding = dict(prior_binding)
        expected = {
            "segment": segment,
            "delta_grid_rad_sha256": _array_sha256(grid),
            "registered_prior_weights_sha256": _array_sha256(prior),
        }
        if (
            binding.get("schema")
            != "biospur-c2-distal-longitudinal-complete-s1-prior-binding-v1"
            or any(binding.get(key) != value for key, value in expected.items())
            or not binding.get("source_authority_sha256")
        ):
            raise ValueError("complete-S1 distal prior binding differs from actual arrays")
        self.segment = segment
        self._grid = grid.copy()
        self._prior = prior.copy()
        self._weights = prior.copy()
        self._binding = binding
        self._last_action_index = -1
        self._actions: list[Mapping[str, Any]] = []

    def process_gauge_invariant_action(
        self,
        *,
        chronological_index: int,
        action: str,
        joint_acceleration_residual_world_mps2: np.ndarray,
        relative_angular_velocity_perpendicular_rads: np.ndarray,
        owner_input_binding: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if chronological_index != self._last_action_index + 1:
            raise ValueError("distal complete-S1 actions must be processed exactly chronologically")
        joint = np.asarray(joint_acceleration_residual_world_mps2, dtype=float)
        angular = np.asarray(relative_angular_velocity_perpendicular_rads, dtype=float)
        if (
            joint.ndim != 2 or joint.shape[1] != 3 or not len(joint)
            or angular.ndim != 1 or len(angular) != len(joint)
            or not np.isfinite(joint).all() or not np.isfinite(angular).all()
        ):
            raise ValueError("distal complete-motion evidence must be finite aligned R3/scalar rows")
        binding = dict(owner_input_binding)
        expected = {
            "segment": self.segment,
            "chronological_index": int(chronological_index),
            "action": str(action),
            "joint_acceleration_residual_world_mps2_sha256": _array_sha256(joint),
            "relative_angular_velocity_perpendicular_rads_sha256": _array_sha256(angular),
        }
        if (
            binding.get("schema")
            != "biospur-c2-distal-longitudinal-complete-motion-input-v1"
            or any(binding.get(key) != value for key, value in expected.items())
            or binding.get("action_pose_truth_used") is not False
            or binding.get("pixel_or_manual_axis_selection_used") is not False
        ):
            raise ValueError("distal complete-motion binding differs from actual owner input")

        # These residuals are formed entirely in world/sensor coordinates
        # from full-R3 center levers and the signed hinge direction.  Rotating
        # the *coordinate definition* of distal +Z about that hinge changes
        # neither array.  Their normalized candidate likelihood is therefore
        # exactly constant and cancels; retaining this zero vector is the
        # scientifically meaningful complete-S1 likelihood evaluation.
        action_log_likelihood = np.zeros(len(self._grid), dtype=float)
        before = self._weights.copy()
        after = before.copy()
        if not np.array_equal(before, after):
            raise RuntimeError("gauge-invariant complete motion changed distal S1 weights")
        self._weights = after
        self._last_action_index = int(chronological_index)
        row = {
            "chronological_index": int(chronological_index),
            "action": str(action),
            "owner_input_binding": binding,
            "row_count": int(len(joint)),
            "joint_acceleration_residual_norm_mps2": {
                "q50": float(np.quantile(np.linalg.norm(joint, axis=1), 0.5)),
                "q90": float(np.quantile(np.linalg.norm(joint, axis=1), 0.9)),
                "maximum": float(np.max(np.linalg.norm(joint, axis=1))),
            },
            "relative_angular_velocity_perpendicular_rads": {
                "q50": float(np.quantile(angular, 0.5)),
                "q90": float(np.quantile(angular, 0.9)),
                "maximum": float(np.max(angular)),
            },
            "complete_s1_action_log_likelihood_sha256": _array_sha256(
                action_log_likelihood
            ),
            "candidate_dependent_motion_information_gain_nats": 0.0,
            "posterior_weights_sha256": _array_sha256(self._weights),
            "joint_acceleration_equality_is_longitudinal_gauge_invariant": True,
            "hinge_transverse_motion_residual_is_longitudinal_gauge_invariant": True,
        }
        self._actions.append(row)
        return row

    def posterior(self) -> DistalLongitudinalPosterior:
        weights = self._weights.copy()
        resultant = abs(np.sum(weights * np.exp(1j * self._grid)))
        return DistalLongitudinalPosterior(
            segment=self.segment,
            delta_grid_rad=self._grid.copy(),
            prior_weights=self._prior.copy(),
            posterior_weights=weights,
            action_count=len(self._actions),
            report={
                "schema": "biospur-c2-distal-longitudinal-complete-s1-posterior-v1",
                "segment": self.segment,
                "prior_binding": dict(self._binding),
                "action_audits": list(self._actions),
                "complete_s1_grid_count": int(len(self._grid)),
                "complete_s1_grid_sha256": _array_sha256(self._grid),
                "prior_weights_sha256": _array_sha256(self._prior),
                "posterior_weights_sha256": _array_sha256(weights),
                "posterior_resultant": float(resultant),
                "posterior_entropy_nats": _entropy(weights),
                "prior_entropy_nats": _entropy(self._prior),
                "motion_information_gain_nats": 0.0,
                "complete_motion_likelihood_evaluated": True,
                "complete_motion_identifies_longitudinal_gauge": False,
                "broad_or_multimodal_support_retained": True,
                "hard_argmax_or_map_exposed": False,
                "sole_joint_lever_used_as_longitudinal_axis": False,
                "action_label_pose_truth_used": False,
                "pixel_or_manual_axis_selection_used": False,
                "tuned_distal_endpoint_pose_authorized": False,
            },
        )
