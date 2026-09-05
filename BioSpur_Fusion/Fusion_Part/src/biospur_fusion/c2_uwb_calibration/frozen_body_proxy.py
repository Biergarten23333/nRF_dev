"""Read-only adapter from frozen C2 3A display-proxy FK to UWB diagnostics.

The returned offsets are useful for causal mechanism tests.  They are not
measured UWB antenna phase centres, anatomical joint centres, or a completed
biomechanical model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from .antenna_los import (
    horizontal_yaw_alignment,
    outward_normal_world,
    rotation_from_wxyz,
)


TARGET_FORWARD_V4 = np.array([0.0, -1.0, 0.0])

NODE_TO_PROXY_POINT = {
    "BSF31CC": "shoulder_mid",
    "BSFC2CC": "pelvis_center",
    "BSFAA61": "elbow_left",
    "BSF1120": "elbow_right",
    "BSFEC35": "wrist_left",
    "BSFB165": "wrist_right",
    "BSF44AD": "knee_left",
    "BSF3C79": "knee_right",
    "BSF6C53": "ankle_left",
    "BSF8BC4": "ankle_right",
}


def output_from_uwb_world(
    output_from_internal: np.ndarray,
    uwb_from_internal: np.ndarray,
) -> np.ndarray:
    """Return the authoritative UWB-world to frozen-display transform.

    The UWB adapter defines ``p_uwb = A @ p_internal`` and the frozen viewer
    defines ``p_output = M @ p_internal``.  Thus the complete coordinate
    binding is ``M @ A.T``; a hand-written mirror omits the heading rotation.
    """

    output = np.asarray(output_from_internal, dtype=float).reshape(3, 3)
    uwb = np.asarray(uwb_from_internal, dtype=float).reshape(3, 3)
    for name, matrix in (("output_from_internal", output), ("uwb_from_internal", uwb)):
        if not np.all(np.isfinite(matrix)) or not np.allclose(
            matrix.T @ matrix, np.eye(3), atol=1e-8
        ):
            raise ValueError(f"{name} must be a finite orthogonal matrix")
    return output @ uwb.T


def frozen_world_alignment(kinematics: Any) -> tuple[np.ndarray, np.ndarray]:
    """Align frozen initial heading to the operator-attested ABEF heading."""

    series = kinematics.series("00", "pelvis")
    forward = [
        rotation_from_wxyz(quaternion) @ np.array([1.0, 0.0, 0.0])
        for quaternion in series.quat_world_segment_wxyz[series.mask]
    ]
    source = np.median(np.asarray(forward), axis=0)
    return horizontal_yaw_alignment(source, TARGET_FORWARD_V4), source


def nearest_frame(kinematics: Any, episode_key: str, fraction: float) -> int:
    """Choose one common valid frozen frame by relative episode time."""

    series = kinematics.series(episode_key, "pelvis")
    indices = np.flatnonzero(series.mask)
    if not len(indices):
        raise ValueError(f"empty frozen episode: {episode_key}")
    relative = min(1.0, max(0.0, float(fraction)))
    target = float(series.time_root_s[indices[0]]) + relative * float(
        series.time_root_s[indices[-1]] - series.time_root_s[indices[0]]
    )
    local = int(np.searchsorted(series.time_root_s[indices], target))
    local = min(local, len(indices) - 1)
    if local and abs(series.time_root_s[indices[local - 1]] - target) < abs(
        series.time_root_s[indices[local]] - target
    ):
        local -= 1
    return int(indices[local])


def body_proxy_at_fraction(
    kinematics: Any,
    episode_key: str,
    fraction: float,
    world_from_frozen_world: np.ndarray,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], int]:
    """Return pelvis-relative node offsets and outward normals in V4 axes."""

    alignment = np.asarray(world_from_frozen_world, dtype=float).reshape(3, 3)
    frame = nearest_frame(kinematics, episode_key, fraction)
    points = kinematics.forward_kinematics(
        episode_key, frame, coordinates="internal"
    )
    pelvis = np.asarray(points["pelvis_center"], dtype=float)
    offsets = {
        node: alignment @ (np.asarray(points[point], dtype=float) - pelvis)
        for node, point in NODE_TO_PROXY_POINT.items()
    }
    normals = {}
    for node, segment in kinematics.node_to_segment.items():
        series = kinematics.series(episode_key, segment)
        normals[node] = outward_normal_world(
            node,
            series.quat_world_segment_wxyz[frame],
            alignment,
        )
    return offsets, normals, frame


@dataclass(frozen=True)
class FrozenHoldoutBodyProxy:
    """Exact H01/H02 counterpart of the frozen 00--19 proxy adapter.

    H01/H02 intentionally live on a separate no-refit interface without a
    public FK method.  This adapter invokes the same frozen renderer primitive
    and the same ten proxy-point identities used by :func:`body_proxy_at_fraction`.
    It does not use action semantics, UWB ranges, or a holdout-derived heading.
    """

    holdout: Any
    calibration: Any
    trajectory: dict[str, Any]
    config: dict[str, Any]
    model: Any

    @classmethod
    def create(
        cls,
        holdout: Any,
        calibration: Any,
        *,
        trajectory: Mapping[str, Any] | None = None,
    ) -> "FrozenHoldoutBodyProxy":
        from biospur_fusion.c2_coupled_progressive.contracts import (
            load_effective_config,
        )
        from biospur_fusion.c2_coupled_progressive.renderer import display_models

        config = load_effective_config()
        if trajectory is None:
            trajectory_data: dict[str, Any] = {
                "trajectory": {},
                "output_coordinate_convention": {
                    "matrix_world_output_from_internal": (
                        holdout.output_matrix_world_display_from_internal
                    ),
                },
            }
            for episode_key, episode in holdout.episodes.items():
                trajectory_data["trajectory"][episode_key] = {
                    segment: {
                        "time_root_s": series.time_root_s,
                        "quat_world_segment_wxyz": series.quat_world_segment_wxyz,
                        "mask": series.mask,
                    }
                    for segment, series in episode.segments.items()
                }
        else:
            trajectory_data = {
                "trajectory": {
                    episode_key: {
                        segment: {
                            field: np.asarray(value).copy()
                            for field, value in row.items()
                        }
                        for segment, row in segments.items()
                    }
                    for episode_key, segments in trajectory["trajectory"].items()
                },
                "output_coordinate_convention": dict(
                    trajectory.get("output_coordinate_convention", {})
                ),
            }
        for episode_key in holdout.episodes:
            if episode_key not in trajectory_data["trajectory"]:
                raise ValueError(f"trajectory is missing holdout {episode_key}")
            reference_time = None
            for segment in calibration.node_to_segment.values():
                row = trajectory_data["trajectory"][episode_key][segment]
                time_s = np.asarray(row["time_root_s"], dtype=float)
                quaternion = np.asarray(
                    row["quat_world_segment_wxyz"], dtype=float
                )
                mask = np.asarray(row["mask"], dtype=bool)
                if (
                    time_s.ndim != 1
                    or quaternion.shape != (len(time_s), 4)
                    or mask.shape != (len(time_s),)
                    or len(time_s) < 2
                    or not np.all(np.diff(time_s) > 0.0)
                    or not np.all(np.isfinite(quaternion))
                ):
                    raise ValueError(
                        f"invalid trajectory series: {episode_key}/{segment}"
                    )
                if reference_time is None:
                    reference_time = time_s
                elif not np.array_equal(time_s, reference_time):
                    raise ValueError(
                        f"trajectory time grids differ in {episode_key}"
                    )
        return cls(
            holdout=holdout,
            calibration=calibration,
            trajectory=trajectory_data,
            config=config,
            model=display_models(config)[1],
        )

    def _row(self, episode_key: str, segment: str) -> dict[str, np.ndarray]:
        return self.trajectory["trajectory"][episode_key][segment]

    def frame_at_fraction(self, episode_key: str, fraction: float) -> int:
        """Return a valid frame from this adapter's authoritative trajectory."""

        row = self._row(episode_key, "pelvis")
        time_s = np.asarray(row["time_root_s"], dtype=float)
        indices = np.flatnonzero(np.asarray(row["mask"], dtype=bool))
        if not len(indices):
            raise ValueError(f"empty trajectory episode: {episode_key}")
        relative = min(1.0, max(0.0, float(fraction)))
        target = time_s[indices[0]] + relative * (
            time_s[indices[-1]] - time_s[indices[0]]
        )
        local = min(
            int(np.searchsorted(time_s[indices], target)), len(indices) - 1
        )
        if local and abs(time_s[indices[local - 1]] - target) < abs(
            time_s[indices[local]] - target
        ):
            local -= 1
        return int(indices[local])

    def time_grid_s(self, episode_key: str) -> np.ndarray:
        return np.asarray(
            self._row(episode_key, "pelvis")["time_root_s"], dtype=float
        ).copy()

    def rotations_at_fraction(
        self,
        episode_key: str,
        fraction: float,
        world_from_frozen_world: np.ndarray,
    ) -> dict[str, np.ndarray]:
        alignment = np.asarray(world_from_frozen_world, dtype=float).reshape(3, 3)
        frame = self.frame_at_fraction(episode_key, fraction)
        return {
            segment: alignment @ rotation_from_wxyz(
                self._row(episode_key, segment)[
                    "quat_world_segment_wxyz"
                ][frame]
            )
            for segment in self.calibration.node_to_segment.values()
        }

    def at_fraction(
        self,
        episode_key: str,
        fraction: float,
        world_from_frozen_world: np.ndarray,
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], int]:
        from biospur_fusion.c2_coupled_progressive.renderer import joints_for_frame

        alignment = np.asarray(world_from_frozen_world, dtype=float).reshape(3, 3)
        frame = self.frame_at_fraction(episode_key, fraction)
        joints = joints_for_frame(
            self.trajectory,
            episode_key,
            frame,
            self.model,
            self.config,
            apply_output_coordinates=False,
        )
        pelvis = np.asarray(joints["pelvis_center"], dtype=float)
        offsets = {
            node: alignment @ (np.asarray(joints[point], dtype=float) - pelvis)
            for node, point in NODE_TO_PROXY_POINT.items()
        }
        normals = {}
        for node, segment in self.calibration.node_to_segment.items():
            normals[node] = outward_normal_world(
                node,
                self._row(episode_key, segment)[
                    "quat_world_segment_wxyz"
                ][frame],
                alignment,
            )
        return offsets, normals, frame
