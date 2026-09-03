"""Pair-local, result-independent timing adapter for sealed C2 IMU ranges."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class PairAlignment:
    """Aligned arrays and an explicit uncertainty report.

    ``lag_samples`` uses the convention that a positive value discards the
    first ``lag_samples`` parent rows and the last child rows. Alignment is
    estimated only from rotational-energy invariants; no listener, readiness,
    UWB, pose label, quaternion, or spatial field enters this adapter.
    """

    parent_indices: np.ndarray
    child_indices: np.ndarray
    lag_samples: int
    report: dict[str, Any]


class PersistentPairClockState:
    """One affine offset/drift nuisance state for each rooted sensor pair.

    Action-window correlations are observations of this state, never retained
    as per-action calibration profiles. The state is updated in chronology and
    exposes offset, drift, innovation, jitter, and prediction covariance.
    """

    def __init__(self, *, maximum_abs_drift_ppm: float, jitter_floor_s: float) -> None:
        self.maximum_abs_drift_ppm = float(maximum_abs_drift_ppm)
        self.jitter_floor_s = float(jitter_floor_s)
        self._observations: dict[str, list[dict[str, float | int | str]]] = {}
        self._node_grid_observations: dict[
            str, dict[str, list[dict[str, float | int | str]]]
        ] = {}

    @staticmethod
    def _array_sha256(value: np.ndarray) -> str:
        array = np.ascontiguousarray(np.asarray(value))
        return sha256(array.view(np.uint8)).hexdigest()

    def observe_episode_node_grids(
        self,
        *,
        action: str,
        chronological_index: int,
        root_node: str,
        time_us_by_node: Mapping[str, np.ndarray],
        boot_epoch_by_node: Mapping[str, np.ndarray],
        contiguous_span_id_by_node: Mapping[str, np.ndarray],
    ) -> dict[str, Any]:
        """Update capture-wide node clocks from result-independent grid anchors.

        START, MIDPOINT, and END are retained as separate correspondence
        hypotheses.  They are not averaged into one action lag and no timer
        values are created across a gap or boot transition.  The update occurs
        only after the current action's prequential prediction has been issued.
        """

        node_names = tuple(sorted(str(node) for node in time_us_by_node))
        if str(root_node) not in node_names:
            raise ValueError("node-clock root is absent from the sealed oriented action")
        if set(node_names) != set(str(node) for node in boot_epoch_by_node):
            raise ValueError("node-clock time/boot node sets differ")
        if set(node_names) != set(str(node) for node in contiguous_span_id_by_node):
            raise ValueError("node-clock time/span node sets differ")

        def accepted_grid(node: str) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
            time = np.asarray(time_us_by_node[node], dtype=np.int64)
            boot = np.asarray(boot_epoch_by_node[node], dtype=np.int64)
            span = np.asarray(contiguous_span_id_by_node[node], dtype=np.int64)
            if time.ndim != 1 or boot.shape != time.shape or span.shape != time.shape:
                raise ValueError("node-clock time/boot/span arrays are inconsistent")
            if len(time) == 0 or len(np.unique(boot)) != 1 or np.any(np.diff(time) <= 0):
                return None
            return time, boot, span

        root_grid = accepted_grid(str(root_node))
        if root_grid is None:
            return {
                "schema": "biospur-c2-capture-wide-node-clock-grid-update-v1",
                "action": str(action),
                "chronological_index": int(chronological_index),
                "root_node": str(root_node),
                "status": "LOCAL_NO_UPDATE_ROOT_GRID_UNUSABLE",
                "node_updates": [],
                "spatial_or_pose_truth_used": False,
                "future_or_heldout_used": False,
            }
        root_time, root_boot, root_span = root_grid
        anchor_indices = {
            "START": lambda length: 0,
            "MIDPOINT": lambda length: length // 2,
            "END": lambda length: length - 1,
        }
        updates: list[dict[str, Any]] = []
        for node in node_names:
            grid = accepted_grid(node)
            if grid is None:
                updates.append({"node": node, "status": "LOCAL_NO_UPDATE_GRID_UNUSABLE"})
                continue
            node_time, node_boot, node_span = grid
            branch_rows = self._node_grid_observations.setdefault(node, {})
            for hypothesis, index_owner in anchor_indices.items():
                root_index = int(index_owner(len(root_time)))
                node_index = int(index_owner(len(node_time)))
                rows = branch_rows.setdefault(hypothesis, [])
                if rows and chronological_index <= int(rows[-1]["chronological_index"]):
                    raise ValueError(f"{node}:{hypothesis}: node-clock observations are not chronological")
                rows.append({
                    "action": str(action),
                    "chronological_index": int(chronological_index),
                    "reference_time_s": float(root_time[root_index]) * 1e-6,
                    "observed_offset_s": float(root_time[root_index] - node_time[node_index]) * 1e-6,
                    "observation_sigma_s": float(self.jitter_floor_s),
                    "root_boot_epoch": int(root_boot[root_index]),
                    "node_boot_epoch": int(node_boot[node_index]),
                    "root_span_id": int(root_span[root_index]),
                    "node_span_id": int(node_span[node_index]),
                })
            updates.append({
                "node": node,
                "status": "THREE_HYPOTHESIS_CAPTURE_WIDE_UPDATE",
                "time_us_sha256": self._array_sha256(node_time),
                "boot_epoch_sha256": self._array_sha256(node_boot),
                "contiguous_span_id_sha256": self._array_sha256(node_span),
                "row_count": int(len(node_time)),
                "span_count": int(len(np.unique(node_span))),
                "boot_epoch": int(node_boot[0]),
                "hypothesis_observation_counts": {
                    name: len(branch_rows[name]) for name in anchor_indices
                },
            })
        return {
            "schema": "biospur-c2-capture-wide-node-clock-grid-update-v1",
            "action": str(action),
            "chronological_index": int(chronological_index),
            "root_node": str(root_node),
            "status": "CAPTURE_WIDE_NODE_CLOCK_UPDATED",
            "hypothesis_ids": list(anchor_indices),
            "node_updates": updates,
            "per_action_clock_profiles_created": False,
            "spatial_or_pose_truth_used": False,
            "future_or_heldout_used": False,
            "prequential_prediction_already_issued": True,
        }

    def predict_pair_from_node_grids(
        self,
        *,
        edge: str,
        parent_node: str,
        child_node: str,
        chronological_index: int,
    ) -> dict[str, Any]:
        """Return retained parent-minus-child offset hypotheses.

        Each node is fitted independently against the same capture-wide root
        clock.  Pair offset is child-to-root minus parent-to-root, with the two
        predictive variances conservatively added.  Between-hypothesis spread
        remains explicit and is later added to timing uncertainty.
        """

        parent_rows = self._node_grid_observations.get(str(parent_node), {})
        child_rows = self._node_grid_observations.get(str(child_node), {})
        hypotheses: list[dict[str, Any]] = []
        for hypothesis in ("START", "MIDPOINT", "END"):
            prows = list(parent_rows.get(hypothesis, ()))
            crows = list(child_rows.get(hypothesis, ()))
            if not prows or not crows:
                continue
            if (
                int(prows[-1]["chronological_index"]) != int(chronological_index)
                or int(crows[-1]["chronological_index"]) != int(chronological_index)
                or prows[-1]["action"] != crows[-1]["action"]
            ):
                continue
            reference_time_s = float(prows[-1]["reference_time_s"])
            parent_fit = self._fit_rows(prows, reference_time_s=reference_time_s)
            child_fit = self._fit_rows(crows, reference_time_s=reference_time_s)
            offset = float(
                child_fit["predicted_offset_s"] - parent_fit["predicted_offset_s"]
            )
            variance = float(
                child_fit["predicted_offset_variance_s2"]
                + parent_fit["predicted_offset_variance_s2"]
            )
            hypotheses.append({
                "hypothesis_id": hypothesis,
                "predicted_parent_minus_child_offset_s": offset,
                "predicted_offset_sigma_s": float(np.sqrt(max(variance, 0.0))),
                "parent_observation_count": len(prows),
                "child_observation_count": len(crows),
                "reference_time_s": reference_time_s,
                "parent_state": dict(parent_fit["state"]),
                "child_state": dict(child_fit["state"]),
                "latest_action": str(prows[-1]["action"]),
                "latest_parent_boot_epoch": int(prows[-1]["node_boot_epoch"]),
                "latest_child_boot_epoch": int(crows[-1]["node_boot_epoch"]),
            })
        if not hypotheses:
            raise ValueError("timing observation local no-update: no causal node-clock hypothesis")
        offsets = np.asarray([
            row["predicted_parent_minus_child_offset_s"] for row in hypotheses
        ], dtype=float)
        within = np.asarray([
            row["predicted_offset_sigma_s"] ** 2 for row in hypotheses
        ], dtype=float)
        weights = np.full(len(hypotheses), 1.0 / len(hypotheses), dtype=float)
        mean = float(weights @ offsets)
        variance = float(weights @ (within + (offsets - mean) ** 2))
        payload = {
            "edge": str(edge),
            "parent_node": str(parent_node),
            "child_node": str(child_node),
            "chronological_index": int(chronological_index),
            "hypotheses": hypotheses,
        }
        return {
            "schema": "biospur-c2-capture-wide-node-clock-pair-prediction-v1",
            **payload,
            "hypothesis_weights": weights.tolist(),
            "moment_matched_offset_s": mean,
            "moment_matched_offset_sigma_s": float(np.sqrt(max(variance, 0.0))),
            "between_hypothesis_variance_s2": float(weights @ (offsets - mean) ** 2),
            "semantic_sha256": sha256(json.dumps(
                payload, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest(),
            "node_clock_hypotheses_retained_before_factor_row_selection": True,
            "per_action_lag_profile_created": False,
            "future_or_heldout_used": False,
        }

    def observe(
        self,
        *,
        edge: str,
        action: str,
        chronological_index: int,
        reference_time_s: float,
        observed_offset_s: float,
        observation_sigma_s: float,
    ) -> dict[str, Any]:
        rows = self._observations.setdefault(str(edge), [])
        if rows and chronological_index <= int(rows[-1]["chronological_index"]):
            raise ValueError(f"{edge}: clock observations are not chronological")
        rows.append({
            "action": str(action),
            "chronological_index": int(chronological_index),
            "reference_time_s": float(reference_time_s),
            "observed_offset_s": float(observed_offset_s),
            "observation_sigma_s": float(max(observation_sigma_s, self.jitter_floor_s)),
        })
        fitted = self._fit_rows(rows, reference_time_s=float(reference_time_s))
        predicted = float(fitted["predicted_offset_s"])
        predicted_variance = float(fitted["predicted_offset_variance_s2"])
        innovation = float(observed_offset_s - predicted)
        return {
            "schema": "biospur-c2-persistent-pair-clock-state-v1",
            "edge": str(edge),
            "observation_count": len(rows),
            "state": dict(fitted["state"]),
            "current_observation": dict(rows[-1]),
            "predicted_offset_s": predicted,
            "predicted_offset_sigma_s": float(np.sqrt(max(predicted_variance, 0.0))),
            "innovation_s": innovation,
            "jitter_floor_s": self.jitter_floor_s,
            "per_action_lag_profile_created": False,
            "local_lag_role": "OBSERVATION_OF_PERSISTENT_PAIR_CLOCK_NUISANCE_STATE",
        }

    def _fit_rows(
        self,
        rows: list[dict[str, float | int | str]],
        *,
        reference_time_s: float,
    ) -> dict[str, Any]:
        if not rows:
            raise ValueError("persistent pair clock prediction requires prior observations")
        t = np.asarray([float(row["reference_time_s"]) for row in rows])
        y = np.asarray([float(row["observed_offset_s"]) for row in rows])
        sigma = np.asarray([float(row["observation_sigma_s"]) for row in rows])
        origin = float(t[0])
        x = t - origin
        if len(rows) == 1:
            intercept = float(y[0])
            drift = 0.0
            covariance = np.diag((sigma[0] ** 2, (self.maximum_abs_drift_ppm * 1e-6) ** 2 / 3.0))
        else:
            design = np.column_stack((np.ones_like(x), x))
            weight = 1.0 / np.maximum(sigma, np.finfo(float).eps) ** 2
            information = design.T @ (weight[:, None] * design)
            solution = np.linalg.pinv(information) @ design.T @ (weight * y)
            drift_bound = self.maximum_abs_drift_ppm * 1e-6
            intercept = float(solution[0])
            drift = float(np.clip(solution[1], -drift_bound, drift_bound))
            prediction = intercept + drift * x
            jitter_variance = max(
                self.jitter_floor_s**2,
                float(np.sum(weight * (y - prediction) ** 2) / max(np.sum(weight), np.finfo(float).eps)),
            )
            covariance = np.linalg.pinv(information) + np.diag((jitter_variance, 0.0))
        evaluation = np.array([1.0, float(reference_time_s) - origin])
        predicted = float(intercept + drift * evaluation[1])
        predicted_variance = float(evaluation @ covariance @ evaluation)
        return {
            "state": {
                "reference_origin_s": origin,
                "offset_at_origin_s": intercept,
                "drift_fraction": drift,
                "drift_ppm": drift * 1e6,
                "state_covariance": covariance.tolist(),
                "maximum_abs_drift_ppm": self.maximum_abs_drift_ppm,
            },
            "predicted_offset_s": predicted,
            "predicted_offset_variance_s2": predicted_variance,
        }

    def predict(self, *, edge: str, reference_time_s: float) -> dict[str, Any]:
        """Predict from a frozen clock state without appending an observation."""

        rows = self._observations.get(str(edge), [])
        fitted = self._fit_rows(rows, reference_time_s=float(reference_time_s))
        return {
            "schema": "biospur-c2-frozen-pair-clock-prediction-v1",
            "edge": str(edge),
            "observation_count": len(rows),
            "state": dict(fitted["state"]),
            "predicted_offset_s": float(fitted["predicted_offset_s"]),
            "predicted_offset_sigma_s": float(np.sqrt(max(
                float(fitted["predicted_offset_variance_s2"]), 0.0,
            ))),
            "frozen_observation_state_updated": False,
            "heldout_local_lag_observation_consumed": False,
        }

    def has_observations(self, *, edge: str) -> bool:
        """Return whether a causal clock prior exists for one exact edge."""

        return bool(self._observations.get(str(edge), ()))

    def audit(self) -> dict[str, Any]:
        return {
            "schema": "biospur-c2-persistent-pair-and-node-clock-audit-v2",
            "pair_count": len(self._observations),
            "node_count": len(self._node_grid_observations),
            "maximum_abs_drift_ppm": self.maximum_abs_drift_ppm,
            "jitter_floor_s": self.jitter_floor_s,
            "observations": {edge: list(rows) for edge, rows in self._observations.items()},
            "node_grid_observations": deepcopy(self._node_grid_observations),
            "per_action_profiles": False,
        }

    def checkpoint(self) -> Mapping[str, Any]:
        return {
            "observations": deepcopy(self._observations),
            "node_grid_observations": deepcopy(self._node_grid_observations),
        }

    def restore(self, checkpoint: Mapping[str, Any]) -> None:
        self._observations = deepcopy(checkpoint["observations"])
        self._node_grid_observations = deepcopy(
            checkpoint.get("node_grid_observations", {})
        )


def _moving_average(values: np.ndarray, width: int) -> np.ndarray:
    if width <= 1:
        return np.asarray(values, dtype=float)
    kernel = np.full(width, 1.0 / width, dtype=float)
    return np.convolve(np.asarray(values, dtype=float), kernel, mode="same")


def _overlap_local_gyro_energy(
    parent_overlap: np.ndarray,
    child_overlap: np.ndarray,
    *,
    smoothing_window_samples: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Build like-for-like invariant signals on one candidate overlap.

    Centering and smoothing belong to the candidate overlap.  Applying either
    transform to the two complete, independently truncated inputs first makes
    their unrelated outer boundaries part of the lag score and can displace an
    exact noncyclic shift.  Static sensor offsets still cancel because each
    endpoint is vector-median centered before its norm is formed.
    """

    parent = np.asarray(parent_overlap, dtype=float)
    child = np.asarray(child_overlap, dtype=float)
    if parent.shape != child.shape or parent.ndim != 2 or parent.shape[1] != 3:
        raise ValueError("candidate timing overlap must contain like-shaped Nx3 arrays")
    width = int(smoothing_window_samples)
    if width <= 0 or width > len(parent):
        raise ValueError("timing smoothing window must fit inside every candidate overlap")
    parent_energy = np.linalg.norm(parent - np.median(parent, axis=0), axis=1)
    child_energy = np.linalg.norm(child - np.median(child, axis=0), axis=1)
    parent_energy = _moving_average(parent_energy, width)
    child_energy = _moving_average(child_energy, width)
    return parent_energy - np.mean(parent_energy), child_energy - np.mean(child_energy)


def _slices(n_parent: int, n_child: int, lag: int) -> tuple[slice, slice, int]:
    if lag >= 0:
        length = min(n_parent - lag, n_child)
        return slice(lag, lag + max(0, length)), slice(0, max(0, length)), max(0, length)
    shift = -lag
    length = min(n_parent, n_child - shift)
    return slice(0, max(0, length)), slice(shift, shift + max(0, length)), max(0, length)


def alignment_from_lag(
    n_parent: int,
    n_child: int,
    lag_samples: int,
    *,
    report: dict[str, Any],
) -> PairAlignment:
    parent_slice, child_slice, length = _slices(n_parent, n_child, int(lag_samples))
    if length <= 0:
        raise ValueError("clock-model lag leaves no aligned rows")
    return PairAlignment(
        parent_indices=np.arange(n_parent, dtype=int)[parent_slice],
        child_indices=np.arange(n_child, dtype=int)[child_slice],
        lag_samples=int(lag_samples),
        report={**report, "selected_overlap_rows": int(length)},
    )


def align_corresponding_gyro_span_pairs(
    parent_gyro: np.ndarray,
    child_gyro: np.ndarray,
    *,
    corresponding_span_index_pairs: Sequence[tuple[np.ndarray, np.ndarray]],
    sample_period_s: float,
    maximum_lag_s: float,
    smoothing_window_samples: int,
    minimum_overlap_s: float,
) -> PairAlignment:
    """Estimate one lag from corresponding, boundary-safe span windows.

    The selected lag is never promoted to a readiness gate. Flat signals and
    boundary peaks are retained with low information and conservative timing
    uncertainty instead of being treated as data failure.  Every candidate is
    transformed separately inside each supplied co-temporal window; values are
    never centered or smoothed across a gap or boot boundary.
    """

    parent = np.asarray(parent_gyro, dtype=float)
    child = np.asarray(child_gyro, dtype=float)
    if parent.ndim != 2 or child.ndim != 2 or parent.shape[1] != 3 or child.shape[1] != 3:
        raise ValueError("pair timing requires Nx3 gyroscope arrays")
    dt = float(sample_period_s)
    if dt <= 0:
        raise ValueError("sample period must be positive")
    max_lag = int(round(float(maximum_lag_s) / dt))
    minimum_overlap = int(round(float(minimum_overlap_s) / dt))
    windows: list[tuple[np.ndarray, np.ndarray]] = []
    previous_parent_stop = -1
    previous_child_stop = -1
    for parent_indices_raw, child_indices_raw in corresponding_span_index_pairs:
        parent_indices = np.asarray(parent_indices_raw, dtype=int)
        child_indices = np.asarray(child_indices_raw, dtype=int)
        if parent_indices.ndim != 1 or child_indices.ndim != 1:
            raise ValueError("timing span indices must be one-dimensional")
        if len(parent_indices) == 0 or len(child_indices) == 0:
            raise ValueError("timing span windows must be nonempty")
        if (
            np.any(np.diff(parent_indices) != 1)
            or np.any(np.diff(child_indices) != 1)
        ):
            raise ValueError("timing span windows must be contiguous within each endpoint")
        if (
            parent_indices[0] <= previous_parent_stop
            or child_indices[0] <= previous_child_stop
        ):
            raise ValueError("timing span windows must be chronological and nonoverlapping")
        if (
            parent_indices[0] < 0 or parent_indices[-1] >= len(parent)
            or child_indices[0] < 0 or child_indices[-1] >= len(child)
        ):
            raise ValueError("timing span index is outside its endpoint array")
        previous_parent_stop = int(parent_indices[-1])
        previous_child_stop = int(child_indices[-1])
        windows.append((parent_indices, child_indices))
    if not windows:
        raise ValueError("pair timing has no corresponding co-temporal span windows")

    scores: list[tuple[int, float, int, int]] = []
    for lag in range(-max_lag, max_lag + 1):
        parent_energy_rows: list[np.ndarray] = []
        child_energy_rows: list[np.ndarray] = []
        overlap_rows = 0
        contributing_windows = 0
        for parent_indices, child_indices in windows:
            ps, cs, length = _slices(len(parent_indices), len(child_indices), lag)
            if length < int(smoothing_window_samples):
                continue
            x, y = _overlap_local_gyro_energy(
                parent[parent_indices[ps]], child[child_indices[cs]],
                smoothing_window_samples=int(smoothing_window_samples),
            )
            parent_energy_rows.append(x)
            child_energy_rows.append(y)
            overlap_rows += int(length)
            contributing_windows += 1
        if overlap_rows < minimum_overlap:
            continue
        x = np.concatenate(parent_energy_rows)
        y = np.concatenate(child_energy_rows)
        denominator = float(np.linalg.norm(x) * np.linalg.norm(y))
        score = float(x @ y / denominator) if denominator > np.finfo(float).eps else 0.0
        scores.append((lag, score, overlap_rows, contributing_windows))
    if not scores:
        raise ValueError("pair timing has no preregistered minimum overlap")
    ordered = sorted(scores, key=lambda row: (-row[1], abs(row[0]), row[0]))
    lag, peak, overlap, contributing_windows = ordered[0]
    score_values = np.asarray([row[1] for row in scores], dtype=float)
    median = float(np.median(score_values))
    mad = float(1.4826 * np.median(np.abs(score_values - median)))
    prominence_z = float((peak - median) / max(mad, np.finfo(float).eps))
    near_peak = [row[0] for row in scores if row[1] >= peak - max(mad, 0.01)]
    half_width = max(abs(value - lag) for value in near_peak) if near_peak else max_lag
    selected_parent_indices: list[np.ndarray] = []
    selected_child_indices: list[np.ndarray] = []
    for parent_indices, child_indices in windows:
        ps, cs, length = _slices(len(parent_indices), len(child_indices), lag)
        if length < int(smoothing_window_samples):
            continue
        selected_parent_indices.append(parent_indices[ps])
        selected_child_indices.append(child_indices[cs])
    status = "INFORMATIVE_INTERIOR_PEAK"
    if abs(lag) == max_lag:
        status = "BOUNDARY_PEAK_LOW_INFORMATION"
    elif prominence_z < 3.0:
        status = "BROAD_OR_FLAT_PEAK_LOW_INFORMATION"
    return PairAlignment(
        parent_indices=np.concatenate(selected_parent_indices),
        child_indices=np.concatenate(selected_child_indices),
        lag_samples=int(lag),
        report={
            "schema": "biospur-c2-pair-gyro-energy-time-alignment-v1",
            "method": "CANDIDATE_OVERLAP_LOCAL_CENTERED_GYRO_NORM_MOVING_AVERAGE_BOUNDED_NORMALIZED_CORRELATION",
            "candidate_overlap_selected_before_preprocessing": True,
            "complete_truncated_signal_boundaries_enter_candidate_score": False,
            "corresponding_span_window_count": len(windows),
            "selected_contributing_span_window_count": int(contributing_windows),
            "centering_or_smoothing_across_span_boundary": False,
            "independent_longest_endpoint_span_selection_used": False,
            "sample_period_s": dt,
            "maximum_lag_s": float(maximum_lag_s),
            "maximum_lag_samples": max_lag,
            "smoothing_window_samples": int(smoothing_window_samples),
            "minimum_overlap_s": float(minimum_overlap_s),
            "selected_lag_samples": int(lag),
            "selected_lag_s": float(lag * dt),
            "selected_overlap_rows": int(overlap),
            "peak_correlation": peak,
            "peak_prominence_robust_z": prominence_z,
            "near_peak_half_width_samples": int(half_width),
            "lag_uncertainty_s": float(max(dt, half_width * dt)),
            "status": status,
            "low_information_terminates_task": False,
            "listener_or_readiness_timing_used": False,
            "spatial_or_pose_truth_used": False,
        },
    )


def align_pair_by_gyro_energy(
    parent_gyro: np.ndarray,
    child_gyro: np.ndarray,
    *,
    sample_period_s: float,
    maximum_lag_s: float,
    smoothing_window_samples: int,
    minimum_overlap_s: float,
) -> PairAlignment:
    """Estimate one bounded lag for a single proven-contiguous span pair."""

    parent = np.asarray(parent_gyro)
    child = np.asarray(child_gyro)
    return align_corresponding_gyro_span_pairs(
        parent,
        child,
        corresponding_span_index_pairs=((
            np.arange(len(parent), dtype=int),
            np.arange(len(child), dtype=int),
        ),),
        sample_period_s=sample_period_s,
        maximum_lag_s=maximum_lag_s,
        smoothing_window_samples=smoothing_window_samples,
        minimum_overlap_s=minimum_overlap_s,
    )
