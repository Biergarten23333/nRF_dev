"""Owned time-varying parent-child heading-correction trajectories."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from biospur_fusion.v0.raw6_heading import Raw6Episode


@dataclass(frozen=True)
class EdgeHeadingTrajectory:
    edge: str
    action: str
    time_ns: np.ndarray
    delta_filtered_rad: np.ndarray
    rating: np.ndarray
    state: np.ndarray
    corrected_child_rotation_world_sensor: np.ndarray
    source: str = "QMT_HEADING_CORRECTION_QUAT2CORR_DELTAFILT"

    def __post_init__(self) -> None:
        n = len(self.time_ns)
        if (
            np.asarray(self.time_ns).shape != (n,)
            or np.asarray(self.delta_filtered_rad).shape != (n,)
            or np.asarray(self.rating).shape != (n,)
            or np.asarray(self.state).shape != (n,)
            or np.asarray(self.corrected_child_rotation_world_sensor).shape != (n, 3, 3)
        ):
            raise ValueError(f"{self.edge}:{self.action}: invalid heading trajectory shape")
        if n < 2 or np.any(np.diff(self.time_ns) <= 0):
            raise ValueError(f"{self.edge}:{self.action}: nonmonotone heading trajectory")
        if not all(np.isfinite(value).all() for value in (
            self.delta_filtered_rad,
            self.rating,
            self.corrected_child_rotation_world_sensor,
        )):
            raise ValueError(f"{self.edge}:{self.action}: nonfinite heading trajectory")

    def rotations_at(self, time_ns: np.ndarray) -> np.ndarray:
        requested = np.asarray(time_ns, dtype=np.int64)
        indices = np.searchsorted(self.time_ns, requested)
        if (
            np.any(indices >= len(self.time_ns))
            or np.any(self.time_ns[indices] != requested)
        ):
            raise ValueError(
                f"{self.edge}:{self.action}: factor/FK time is absent from QMT trajectory"
            )
        return self.corrected_child_rotation_world_sensor[indices]

    def audit(self) -> dict[str, Any]:
        return {
            "edge": self.edge,
            "action": self.action,
            "rows": len(self.time_ns),
            "first_time_ns": int(self.time_ns[0]),
            "last_time_ns": int(self.time_ns[-1]),
            "delta_filtered_spread_deg": float(np.degrees(
                np.ptp(np.unwrap(self.delta_filtered_rad))
            )),
            "rating_median": float(np.median(self.rating)),
            "source": self.source,
            "collapsed_to_episode_scalar": False,
            "consumed_as_orientation_frontend_output": True,
        }


def extract_qmt_heading_trajectories(
    edge: str,
    report: Mapping[str, Any],
    episodes: Sequence[Raw6Episode],
) -> tuple[dict[str, EdgeHeadingTrajectory], dict[str, Any]]:
    """Move non-serial trajectory arrays out of a QMT diagnostic report."""

    episode_by_action = {episode.action: episode for episode in episodes}
    trajectories: dict[str, EdgeHeadingTrajectory] = {}
    records = []
    for source_record in report["records"]:
        record = dict(source_record)
        payload = record.pop("_orientation_trajectory", None)
        if payload is not None:
            action = str(record["action"])
            episode = episode_by_action[action]
            trajectory = EdgeHeadingTrajectory(
                edge=edge,
                action=action,
                time_ns=np.asarray(episode.time_ns, dtype=np.int64),
                delta_filtered_rad=np.asarray(payload["delta_filtered_rad"], dtype=float),
                rating=np.asarray(payload["rating"], dtype=float),
                state=np.asarray(payload["state"], dtype=int),
                corrected_child_rotation_world_sensor=np.asarray(
                    payload["corrected_child_rotation_world_sensor"], dtype=float,
                ),
            )
            trajectories[action] = trajectory
            record["orientation_trajectory_audit"] = trajectory.audit()
        records.append(record)
    clean_report = {
        **report,
        "records": records,
        "orientation_trajectory_actions": sorted(trajectories),
        "orientation_trajectory_count": len(trajectories),
        "quat2corr_and_deltafilt_discarded": False,
        "episode_scalar_is_seed_or_summary_only": True,
    }
    return trajectories, clean_report


def corrected_episode_rotations(
    episode: Raw6Episode,
    hinge_axes: Mapping[str, Any],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Apply each tree child's owned QMT trajectory once before factors/FK."""

    rotations = {
        segment: np.asarray(value, dtype=float)
        for segment, value in episode.rotation_world_sensor.items()
    }
    applied = []
    for edge, estimate in hinge_axes.items():
        trajectory = estimate.heading_trajectories.get(episode.action)
        if trajectory is None:
            continue
        rotations[estimate.child] = trajectory.rotations_at(episode.time_ns)
        applied.append(trajectory.audit())
    return rotations, {
        "action": episode.action,
        "applied_edge_trajectories": applied,
        "trajectory_count": len(applied),
        "factors_fk_use_same_corrected_orientation": True,
        "episode_scalar_heading_used_as_orientation_correction": False,
    }
