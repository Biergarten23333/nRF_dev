"""Pair-local functional axes, centers, and segment-frame branches for C2."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

import numpy as np
import qmt
from scipy.optimize import least_squares
from scipy.signal import savgol_coeffs, savgol_filter

from .architecture_guard import C2ExecutionGuard
from .orientation import OrientedAction
from .timebase import (
    PairAlignment,
    PersistentPairClockState,
    align_corresponding_gyro_span_pairs,
)


EDGE_SPECS = (
    ("pelvis_torso", "pelvis", "torso"),
    ("shoulder_left", "torso", "upper_arm_left"),
    ("elbow_left", "upper_arm_left", "forearm_left"),
    ("shoulder_right", "torso", "upper_arm_right"),
    ("elbow_right", "upper_arm_right", "forearm_right"),
    ("hip_left", "pelvis", "thigh_left"),
    ("knee_left", "thigh_left", "shank_left"),
    ("hip_right", "pelvis", "thigh_right"),
    ("knee_right", "thigh_right", "shank_right"),
)

EDGE_ACTIONS = {
    "pelvis_torso": ("03_pelvis_hula_circle", "14_trunk_flex_extend", "15_trunk_axial_rotation"),
    "shoulder_left": ("04_shoulder_left",),
    "elbow_left": ("06_elbow_left",),
    "shoulder_right": ("05_shoulder_right",),
    "elbow_right": ("07_elbow_right",),
    "hip_left": ("08_hip_left", "16_squat"),
    "knee_left": ("10_knee_left_seated", "16_squat", "18_heel_to_butt_left"),
    "hip_right": ("09_hip_right", "16_squat"),
    "knee_right": ("11_knee_right_seated", "16_squat", "19_heel_to_butt_right"),
}

HINGE_EDGES = ("elbow_left", "elbow_right", "knee_left", "knee_right")


@dataclass(frozen=True)
class AlignedPair:
    edge: str
    action: str
    parent_acc: np.ndarray
    child_acc: np.ndarray
    parent_gyro: np.ndarray
    child_gyro: np.ndarray
    parent_observed_time_s: np.ndarray
    child_observed_time_s: np.ndarray
    parent_boot_epoch: np.ndarray
    child_boot_epoch: np.ndarray
    alignment: PairAlignment
    contiguous_spans: tuple[slice, ...]
    provenance: Mapping[str, Any]


def _array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return sha256(array.view(np.uint8)).hexdigest()


@dataclass(frozen=True)
class CenterEstimate:
    edge: str
    parent: str
    child: str
    joint_to_parent_sensor_m: np.ndarray
    joint_to_child_sensor_m: np.ndarray
    covariance_m2: np.ndarray
    report: Mapping[str, Any]


@dataclass(frozen=True)
class AxisEstimate:
    edge: str
    parent_axis_sensor: np.ndarray
    child_axis_sensor: np.ndarray
    tangent_covariance_rad2: np.ndarray
    report: Mapping[str, Any]


class CenterFactorAggregateBudgetExceeded(RuntimeError):
    """Finite factor-level solver budget exhausted; caller must local-no-update."""

    def __init__(self, message: str, *, audit: Mapping[str, Any]) -> None:
        self.audit = dict(audit)
        super().__init__(message)


def _corresponding_timing_span_windows(
    *,
    parent_time_us: np.ndarray,
    child_time_us: np.ndarray,
    parent_boot_epoch: np.ndarray,
    child_boot_epoch: np.ndarray,
    parent_span_id: np.ndarray,
    child_span_id: np.ndarray,
    sample_period_s: float,
    predicted_parent_minus_child_offset_s: float | None,
    predicted_offset_sigma_s: float | None,
) -> tuple[tuple[tuple[np.ndarray, np.ndarray], ...], dict[str, Any]]:
    """Bind clock-mapped co-temporal windows without crossing a boundary.

    A boot transition inside either endpoint makes elapsed time across that
    transition unknowable.  Multiple gap-safe spans require a causal pair-clock
    prior to prove their correspondence.  A first observation may bootstrap
    only from one unambiguous span per endpoint; it cannot align independently
    selected longest spans.
    """

    arrays = (
        np.asarray(parent_time_us, dtype=np.int64),
        np.asarray(child_time_us, dtype=np.int64),
        np.asarray(parent_boot_epoch, dtype=np.int64),
        np.asarray(child_boot_epoch, dtype=np.int64),
        np.asarray(parent_span_id, dtype=np.int64),
        np.asarray(child_span_id, dtype=np.int64),
    )
    parent_time, child_time, parent_boot, child_boot, parent_span, child_span = arrays
    if (
        len(parent_time) != len(parent_boot) or len(parent_time) != len(parent_span)
        or len(child_time) != len(child_boot) or len(child_time) != len(child_span)
        or len(parent_time) == 0 or len(child_time) == 0
    ):
        raise ValueError("timing span ownership arrays have inconsistent lengths")
    if len(np.unique(parent_boot)) != 1 or len(np.unique(child_boot)) != 1:
        raise ValueError(
            "timing observation local no-update: action contains an unknown boot-transition interval"
        )
    if np.any(np.diff(parent_time) <= 0) or np.any(np.diff(child_time) <= 0):
        raise ValueError("timing observation local no-update: endpoint timer is nonmonotonic")

    def spans(span_ids: np.ndarray) -> tuple[np.ndarray, ...]:
        breaks = np.flatnonzero(np.diff(span_ids) != 0) + 1
        boundaries = np.r_[0, breaks, len(span_ids)]
        return tuple(
            np.arange(int(start), int(stop), dtype=int)
            for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True)
        )

    dt = float(sample_period_s)
    if dt <= 0.0:
        raise ValueError("timing sample period must be positive")
    parent_spans = spans(parent_span)
    child_spans = spans(child_span)
    if predicted_parent_minus_child_offset_s is None:
        if len(parent_spans) != 1 or len(child_spans) != 1:
            raise ValueError(
                "timing observation local no-update: multiple spans have no causal pair-clock prior"
            )
        windows = ((parent_spans[0], child_spans[0]),)
        return windows, {
            "schema": "biospur-c2-corresponding-timing-span-window-audit-v1",
            "co_temporal_mapping_owner": "UNIQUE_SINGLE_SPAN_PER_ENDPOINT_BOOTSTRAP",
            "causal_pair_clock_prior_available": False,
            "predicted_parent_minus_child_offset_s": None,
            "predicted_offset_sigma_s": None,
            "boot_transition_elapsed_invented": False,
            "independent_longest_endpoint_span_selection_used": False,
            "parent_boot_epoch": int(parent_boot[0]),
            "child_boot_epoch": int(child_boot[0]),
            "corresponding_window_count": 1,
            "windows": [{
                "parent_source_rows_half_open": [0, len(parent_time)],
                "child_source_rows_half_open": [0, len(child_time)],
                "parent_boot_epoch": int(parent_boot[0]),
                "child_boot_epoch": int(child_boot[0]),
            }],
            "centering_or_smoothing_across_gap_or_boot_boundary": False,
        }

    predicted_offset = float(predicted_parent_minus_child_offset_s)
    predicted_sigma = float(predicted_offset_sigma_s)
    if not np.isfinite(predicted_offset) or not np.isfinite(predicted_sigma) or predicted_sigma < 0.0:
        raise ValueError("timing pair-clock prior is nonfinite")
    parent_clock_s = parent_time.astype(float) * 1e-6
    child_on_parent_clock_s = child_time.astype(float) * 1e-6 + predicted_offset
    window_rows: list[tuple[float, np.ndarray, np.ndarray, dict[str, Any]]] = []
    for parent_indices in parent_spans:
        parent_start = float(parent_clock_s[parent_indices[0]])
        parent_stop = float(parent_clock_s[parent_indices[-1]] + dt)
        for child_indices in child_spans:
            child_start = float(child_on_parent_clock_s[child_indices[0]])
            child_stop = float(child_on_parent_clock_s[child_indices[-1]] + dt)
            overlap_start = max(parent_start, child_start)
            overlap_stop = min(parent_stop, child_stop)
            if overlap_stop <= overlap_start:
                continue
            parent_values = parent_clock_s[parent_indices]
            child_values = child_on_parent_clock_s[child_indices]
            parent_local = parent_indices[
                np.searchsorted(parent_values, overlap_start, side="left"):
                np.searchsorted(parent_values, overlap_stop, side="left")
            ]
            child_local = child_indices[
                np.searchsorted(child_values, overlap_start, side="left"):
                np.searchsorted(child_values, overlap_stop, side="left")
            ]
            if len(parent_local) == 0 or len(child_local) == 0:
                continue
            audit = {
                "predicted_parent_clock_overlap_half_open_s": [overlap_start, overlap_stop],
                "parent_source_rows_half_open": [
                    int(parent_local[0]), int(parent_local[-1]) + 1,
                ],
                "child_source_rows_half_open": [
                    int(child_local[0]), int(child_local[-1]) + 1,
                ],
                "parent_boot_epoch": int(parent_boot[parent_local[0]]),
                "child_boot_epoch": int(child_boot[child_local[0]]),
            }
            window_rows.append((overlap_start, parent_local, child_local, audit))
    window_rows.sort(key=lambda row: (row[0], int(row[1][0]), int(row[2][0])))
    windows = tuple((row[1], row[2]) for row in window_rows)
    if not windows:
        raise ValueError("timing observation local no-update: no co-temporal span overlap")
    return windows, {
        "schema": "biospur-c2-corresponding-timing-span-window-audit-v1",
        "co_temporal_mapping_owner": "CAUSAL_PERSISTENT_PAIR_CLOCK_PRIOR",
        "causal_pair_clock_prior_available": True,
        "predicted_parent_minus_child_offset_s": predicted_offset,
        "predicted_offset_sigma_s": predicted_sigma,
        "boot_transition_elapsed_invented": False,
        "independent_longest_endpoint_span_selection_used": False,
        "parent_boot_epoch": int(parent_boot[0]),
        "child_boot_epoch": int(child_boot[0]),
        "corresponding_window_count": len(windows),
        "windows": [row[3] for row in window_rows],
        "centering_or_smoothing_across_gap_or_boot_boundary": False,
    }


def _online_center_candidate_mixture(
    *,
    full_trials: Sequence[Mapping[str, Any]],
    prefix_trials: Sequence[Mapping[str, Any]],
    informed_observation_covariance_m2: np.ndarray,
    nullspace_covariance_m2: np.ndarray,
    systematic_covariance_m2: np.ndarray,
    enabled: bool,
    incomplete_nuisance_sigma_m: float,
) -> Mapping[str, Any]:
    """Moment-match finite interior nominal/prefix branches for online use."""

    informed = np.asarray(informed_observation_covariance_m2, dtype=float)
    nullspace = np.asarray(nullspace_covariance_m2, dtype=float)
    systematic = np.asarray(systematic_covariance_m2, dtype=float)
    if any(value.shape != (6, 6) for value in (informed, nullspace, systematic)):
        raise ValueError("online center mixture covariance shapes must be 6x6")
    if not all(np.all(np.isfinite(value)) for value in (informed, nullspace, systematic)):
        raise ValueError("online center mixture covariance is nonfinite")
    if enabled and (
        not np.isfinite(incomplete_nuisance_sigma_m)
        or incomplete_nuisance_sigma_m <= 0.0
    ):
        raise ValueError("online center incomplete-nuisance sigma is invalid")
    sources = [
        ("FULL_CAUSAL_PREFIX", trial) for trial in full_trials
    ] + [
        ("CHRONOLOGICAL_EARLY_PREFIX", trial) for trial in prefix_trials
    ]
    sources = [
        (scope, trial) for scope, trial in sources
        if bool(trial["result"].success)
        and bool(trial["interior"])
        and np.asarray(trial["result"].x, dtype=float).shape == (6,)
        and np.all(np.isfinite(trial["result"].x))
        and np.isfinite(float(trial["normalized_cost"]))
    ]
    branches: list[dict[str, Any]] = []
    if sources:
        minimum_cost = min(float(trial["normalized_cost"]) for _, trial in sources)
        raw_weights = np.asarray([
            np.exp(-(float(trial["normalized_cost"]) - minimum_cost))
            for _, trial in sources
        ], dtype=float)
        if not np.all(np.isfinite(raw_weights)) or float(np.sum(raw_weights)) <= 0.0:
            raise FloatingPointError("online center candidate weights are invalid")
        raw_weights /= np.sum(raw_weights)
        for candidate_index, ((scope, trial), weight) in enumerate(zip(
            sources, raw_weights, strict=True,
        )):
            solution = np.asarray(trial["result"].x, dtype=float)
            branches.append({
                "candidate_id": f"{scope}_{candidate_index:03d}",
                "scope": scope,
                "start_index": int(trial["start_index"]),
                "joint_to_parent_sensor_m": solution[:3].tolist(),
                "joint_to_child_sensor_m": solution[3:].tolist(),
                "normalized_cost": float(trial["normalized_cost"]),
                "weight": float(weight),
                "finite": True,
                "numerical_interior": True,
                "physical_topology_status": "PENDING_POST_QMT_OWNER_GATE",
            })
    between = np.zeros((6, 6), dtype=float)
    incomplete = np.zeros((6, 6), dtype=float)
    candidate_mean: np.ndarray | None = None
    if enabled and branches:
        values = np.asarray([
            [
                *row["joint_to_parent_sensor_m"],
                *row["joint_to_child_sensor_m"],
            ]
            for row in branches
        ], dtype=float)
        weights = np.asarray([row["weight"] for row in branches], dtype=float)
        candidate_mean = np.sum(weights[:, None] * values, axis=0)
        offsets = values - candidate_mean[None, :]
        between = np.einsum("n,ni,nj->ij", weights, offsets, offsets)
        incomplete = np.eye(6) * incomplete_nuisance_sigma_m**2
    updated_informed = 0.5 * (
        informed + between + incomplete
        + (informed + between + incomplete).T
    )
    statistical = 0.5 * (
        updated_informed + nullspace + (updated_informed + nullspace).T
    )
    total = 0.5 * (
        statistical + systematic + (statistical + systematic).T
    )
    return {
        "branches": branches,
        "candidate_mean_m": None if candidate_mean is None else candidate_mean,
        "between_candidate_covariance_m2": between,
        "incomplete_nuisance_covariance_m2": incomplete,
        "informed_observation_covariance_m2": updated_informed,
        "statistical_covariance_m2": statistical,
        "total_covariance_m2": total,
        "finite_interior_branch_available": bool(branches),
        "nonfinite_or_boundary_rows_retained_as_online_branches": False,
    }


def validate_center_covariance_contract(
    covariance_m2: np.ndarray,
    report: Mapping[str, Any],
) -> None:
    """Reject dimension/shape mutations on the real center output path."""

    covariance = np.asarray(covariance_m2, dtype=float)
    expected_units = "m^2_FROM_DIMENSIONLESS_STANDARDIZED_RESIDUAL_AND_1_PER_M_JACOBIAN"
    if covariance.shape != (6, 6):
        raise ValueError("COVARIANCE_UNIT_SCALE_MUTATION: center covariance must be 6x6")
    if report.get("covariance_units") != "m^2" or report.get("sandwich_covariance_units") != expected_units:
        raise ValueError("COVARIANCE_UNIT_SCALE_MUTATION: standardized-Jacobian covariance unit contract changed")
    if report.get("standardized_covariance_multiplied_by_robust_sigma_squared") is not False:
        raise ValueError("COVARIANCE_UNIT_SCALE_MUTATION: forbidden robust_sigma squared multiplication")
    if not np.allclose(covariance, covariance.T, atol=1e-10):
        raise ValueError("COVARIANCE_UNIT_SCALE_MUTATION: center covariance is not symmetric")
    if float(np.min(np.linalg.eigvalsh(covariance))) < -1e-12:
        raise ValueError("COVARIANCE_UNIT_SCALE_MUTATION: center covariance is not positive semidefinite")


def aligned_pair(
    episode: OrientedAction,
    *,
    edge: str,
    parent_node: str,
    child_node: str,
    timing: Mapping[str, Any],
    clock_state: PersistentPairClockState,
    execution_guard: C2ExecutionGuard,
) -> AlignedPair:
    execution_guard.validate_factor_fields(tuple(timing["factor_fields"]))
    parent_gyro = episode.gyro_rads_by_node[parent_node]
    child_gyro = episode.gyro_rads_by_node[child_node]
    parent_span = episode.contiguous_span_id_by_node[parent_node]
    child_span = episode.contiguous_span_id_by_node[child_node]
    parent_time = episode.time_us_by_node[parent_node]
    child_time = episode.time_us_by_node[child_node]
    clock_prior = None
    node_clock_prior = None
    if clock_state.has_observations(edge=edge):
        clock_prior = clock_state.predict(
            edge=edge,
            reference_time_s=float(np.median(parent_time)) * 1e-6,
        )
    if clock_prior is not None:
        prior_hypotheses = ({
            "hypothesis_id": "PERSISTENT_EDGE_AFFINE_STATE",
            "predicted_parent_minus_child_offset_s": float(
                clock_prior["predicted_offset_s"]
            ),
            "predicted_offset_sigma_s": float(
                clock_prior["predicted_offset_sigma_s"]
            ),
        },)
    else:
        try:
            node_clock_prior = clock_state.predict_pair_from_node_grids(
                edge=edge,
                parent_node=parent_node,
                child_node=child_node,
                chronological_index=episode.chronological_index,
            )
            prior_hypotheses = tuple(node_clock_prior["hypotheses"])
        except ValueError:
            prior_hypotheses = ({
                "hypothesis_id": "UNIQUE_SINGLE_SPAN_BOOTSTRAP",
                "predicted_parent_minus_child_offset_s": None,
                "predicted_offset_sigma_s": None,
            },)
    candidate_rows: list[tuple[PairAlignment, dict[str, Any], Mapping[str, Any]]] = []
    rejected_hypotheses: list[dict[str, Any]] = []
    for hypothesis in prior_hypotheses:
        try:
            corresponding_windows, window_audit = _corresponding_timing_span_windows(
                parent_time_us=parent_time,
                child_time_us=child_time,
                parent_boot_epoch=episode.derived_boot_epoch_by_node[parent_node],
                child_boot_epoch=episode.derived_boot_epoch_by_node[child_node],
                parent_span_id=parent_span,
                child_span_id=child_span,
                sample_period_s=float(timing["sample_period_s"]),
                predicted_parent_minus_child_offset_s=(
                    hypothesis["predicted_parent_minus_child_offset_s"]
                ),
                predicted_offset_sigma_s=hypothesis["predicted_offset_sigma_s"],
            )
            candidate = align_corresponding_gyro_span_pairs(
                parent_gyro,
                child_gyro,
                corresponding_span_index_pairs=corresponding_windows,
                sample_period_s=float(timing["sample_period_s"]),
                maximum_lag_s=float(timing["maximum_lag_s"]),
                smoothing_window_samples=int(timing["smoothing_window_samples"]),
                minimum_overlap_s=float(timing["minimum_overlap_s"]),
            )
            candidate_rows.append((candidate, window_audit, hypothesis))
        except ValueError as exc:
            rejected_hypotheses.append({
                "hypothesis_id": str(hypothesis["hypothesis_id"]),
                "status": "LOCAL_HYPOTHESIS_NO_UPDATE",
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
            })
    if not candidate_rows:
        causes = " | ".join(
            str(row["exception_message"]) for row in rejected_hypotheses
        )
        raise ValueError(
            "timing observation local no-update: no retained clock hypothesis has a "
            f"co-temporal overlap; rejected causes: {causes}"
        )
    # The existing rotational-energy timing owner selects which retained
    # correspondence supplies rows.  All alternatives remain in the audit and
    # their between-hypothesis spread is propagated; this is not a hard lock.
    selected_index = max(
        range(len(candidate_rows)),
        key=lambda index: (
            float(candidate_rows[index][0].report["peak_correlation"]),
            int(candidate_rows[index][0].report["selected_overlap_rows"]),
            -index,
        ),
    )
    local, timing_window_audit, selected_hypothesis = candidate_rows[selected_index]
    retained_offsets = np.asarray([
        float(row[2]["predicted_parent_minus_child_offset_s"])
        for row in candidate_rows
        if row[2]["predicted_parent_minus_child_offset_s"] is not None
    ], dtype=float)
    retained_sigmas = np.asarray([
        float(row[2]["predicted_offset_sigma_s"])
        for row in candidate_rows
        if row[2]["predicted_parent_minus_child_offset_s"] is not None
    ], dtype=float)
    if len(retained_offsets):
        between_hypothesis_variance_s2 = float(np.var(retained_offsets))
        node_clock_within_variance_s2 = float(np.mean(retained_sigmas**2))
    else:
        between_hypothesis_variance_s2 = 0.0
        node_clock_within_variance_s2 = 0.0
    local_parent = local.parent_indices
    local_child = local.child_indices
    observed_offset_s = float(np.median(
        parent_time[local_parent].astype(float) - child_time[local_child].astype(float)
    ) * 1e-6)
    reference_time_s = float(np.median(parent_time[local_parent]) * 1e-6)
    clock = clock_state.observe(
        edge=edge,
        action=episode.action,
        chronological_index=episode.chronological_index,
        reference_time_s=reference_time_s,
        observed_offset_s=observed_offset_s,
        observation_sigma_s=float(local.report["lag_uncertainty_s"]),
    )
    origin_offset_s = float((int(parent_time[0]) - int(child_time[0])) * 1e-6)
    predicted_lag = int(round((float(clock["predicted_offset_s"]) - origin_offset_s) / float(timing["sample_period_s"])))
    maximum_lag = int(round(float(timing["maximum_lag_s"]) / float(timing["sample_period_s"])))
    predicted_lag = int(np.clip(predicted_lag, -maximum_lag, maximum_lag))
    predicted_offset_us = float(clock["predicted_offset_s"]) * 1e6
    tolerance_us = float(timing["clock_match_tolerance_s"]) * 1e6
    parent_indices: list[int] = []
    child_indices: list[int] = []
    last_child = -1
    child_time_float = child_time.astype(float)
    for parent_index, parent_value in enumerate(parent_time.astype(float)):
        target_child = parent_value - predicted_offset_us
        insertion = int(np.searchsorted(child_time_float, target_child))
        options = [value for value in (insertion - 1, insertion) if last_child < value < len(child_time)]
        if not options:
            continue
        selected = min(options, key=lambda value: abs(child_time_float[value] - target_child))
        if abs(child_time_float[selected] - target_child) <= tolerance_us:
            parent_indices.append(parent_index)
            child_indices.append(selected)
            last_child = selected
    if len(parent_indices) < int(round(float(timing["minimum_overlap_s"]) / float(timing["sample_period_s"]))):
        raise ValueError(f"{edge}:{episode.action}: persistent clock model leaves insufficient exact matches")
    pi = np.asarray(parent_indices, dtype=int)
    ci = np.asarray(child_indices, dtype=int)
    parent_break = (
        np.diff(parent_time[pi]) != int(round(float(timing["sample_period_s"]) * 1e6))
    ) | (np.diff(episode.derived_boot_epoch_by_node[parent_node][pi]) != 0)
    child_break = (
        np.diff(child_time[ci]) != int(round(float(timing["sample_period_s"]) * 1e6))
    ) | (np.diff(episode.derived_boot_epoch_by_node[child_node][ci]) != 0)
    breaks = np.flatnonzero(parent_break | child_break) + 1
    boundaries = np.r_[0, breaks, len(pi)]
    spans = tuple(
        slice(int(start), int(stop)) for start, stop in zip(boundaries[:-1], boundaries[1:])
        if stop - start >= int(timing["minimum_contiguous_span_rows"])
    )
    if not spans:
        raise ValueError(f"{edge}:{episode.action}: no eligible contiguous matched span")
    keep = np.concatenate([np.arange(span.start, span.stop) for span in spans])
    pi = pi[keep]
    ci = ci[keep]
    lengths = [span.stop - span.start for span in spans]
    contiguous_spans = []
    cursor = 0
    for length in lengths:
        contiguous_spans.append(slice(cursor, cursor + length))
        cursor += length
    alignment = PairAlignment(
        parent_indices=pi,
        child_indices=ci,
        lag_samples=predicted_lag,
        report={
            "schema": "biospur-c2-persistent-clock-aligned-pair-v1",
            "selected_lag_samples": predicted_lag,
            "selected_lag_s": predicted_lag * float(timing["sample_period_s"]),
            "lag_uncertainty_s": float(np.hypot(
                np.hypot(
                    local.report["lag_uncertainty_s"],
                    clock["predicted_offset_sigma_s"],
                ),
                np.sqrt(
                    between_hypothesis_variance_s2
                    + node_clock_within_variance_s2
                ),
            )),
            "status": local.report["status"],
            "local_correlation_observation": local.report,
            "corresponding_timing_span_windows": timing_window_audit,
            "persistent_pair_clock": clock,
            "capture_wide_node_clock_prior": node_clock_prior,
            "retained_clock_hypotheses": [
                {
                    "hypothesis_id": str(row[2]["hypothesis_id"]),
                    "predicted_parent_minus_child_offset_s": row[2][
                        "predicted_parent_minus_child_offset_s"
                    ],
                    "predicted_offset_sigma_s": row[2]["predicted_offset_sigma_s"],
                    "peak_correlation": float(row[0].report["peak_correlation"]),
                    "selected_overlap_rows": int(row[0].report["selected_overlap_rows"]),
                    "corresponding_window_count": int(
                        row[1]["corresponding_window_count"]
                    ),
                    "selected_for_factor_rows": index == selected_index,
                }
                for index, row in enumerate(candidate_rows)
            ],
            "rejected_clock_hypotheses": rejected_hypotheses,
            "selected_clock_hypothesis_id": str(selected_hypothesis["hypothesis_id"]),
            "between_hypothesis_variance_s2": between_hypothesis_variance_s2,
            "node_clock_within_variance_s2": node_clock_within_variance_s2,
            "clock_prior_source": (
                "PERSISTENT_EDGE_AFFINE_STATE_PREFERRED_AFTER_FIRST_EDGE_OBSERVATION"
                if clock_prior is not None else
                "CAPTURE_WIDE_NODE_HYPOTHESES_FOR_EDGE_BOOTSTRAP"
            ),
            "clock_hypothesis_factor_row_policy": (
                "ONE_HYPOTHESIS_SELECTED_BY_MATURE_GAP_LOCAL_GYRO_"
                "CORRELATION;ALTERNATIVES_RETAINED_IN_MOMENT_MATCHED_WITHIN_"
                "PLUS_BETWEEN_TIMING_COVARIANCE_AND_AUDIT"
            ),
            "branch_specific_geometry_refits_per_clock_hypothesis": False,
            "full_clock_multibranch_geometry_propagation_claimed": False,
            "diagnostic_not_pass": True,
            "per_action_lag_profile_created": False,
            "clock_match_tolerance_s": float(timing["clock_match_tolerance_s"]),
            "contiguous_span_count": len(contiguous_spans),
            "contiguous_span_lengths": lengths,
            "rows_across_gap_aligned_or_differentiated": 0,
        },
    )
    return AlignedPair(
        edge=edge,
        action=episode.action,
        parent_acc=episode.acc_mps2_by_node[parent_node][pi],
        child_acc=episode.acc_mps2_by_node[child_node][ci],
        parent_gyro=parent_gyro[pi],
        child_gyro=child_gyro[ci],
        parent_observed_time_s=parent_time[pi].astype(float) * 1e-6,
        child_observed_time_s=child_time[ci].astype(float) * 1e-6,
        parent_boot_epoch=np.asarray(
            episode.derived_boot_epoch_by_node[parent_node][pi], dtype=np.int64,
        ),
        child_boot_epoch=np.asarray(
            episode.derived_boot_epoch_by_node[child_node][ci], dtype=np.int64,
        ),
        alignment=alignment,
        contiguous_spans=tuple(contiguous_spans),
        provenance={
            "schema": "biospur-c2-owner-produced-gap-safe-aligned-pair-v1",
            "owner": "functional_geometry.aligned_pair",
            "chronological_index": int(episode.chronological_index),
            "action": episode.action,
            "edge": edge,
            "parent_node": parent_node,
            "child_node": child_node,
            "parent_source_indices_sha256": _array_sha256(pi.astype(np.int64)),
            "child_source_indices_sha256": _array_sha256(ci.astype(np.int64)),
            "parent_time_us_sha256": _array_sha256(parent_time[pi].astype(np.int64)),
            "child_time_us_sha256": _array_sha256(child_time[ci].astype(np.int64)),
            "parent_quaternion_wxyz_sha256": _array_sha256(
                episode.quat_world_sensor_wxyz_by_node[parent_node][pi]
            ),
            "child_quaternion_wxyz_sha256": _array_sha256(
                episode.quat_world_sensor_wxyz_by_node[child_node][ci]
            ),
            "parent_calibration_posterior_sha256": (
                episode.calibration_posterior_by_node.get(parent_node, {}).get(
                    "semantic_sha256"
                )
            ),
            "child_calibration_posterior_sha256": (
                episode.calibration_posterior_by_node.get(child_node, {}).get(
                    "semantic_sha256"
                )
            ),
            "contiguous_span_half_open": [[span.start, span.stop] for span in contiguous_spans],
            "corresponding_timing_span_windows_sha256": sha256(
                json.dumps(
                    timing_window_audit,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "copied_or_caller_supplied_arrays": False,
        },
    )


def _uniform_cap(arrays: Sequence[np.ndarray], maximum_rows: int) -> tuple[list[np.ndarray], np.ndarray]:
    n = len(arrays[0])
    if any(len(value) != n for value in arrays):
        raise ValueError("uniform cap arrays differ in length")
    if n <= maximum_rows:
        keep = np.arange(n, dtype=int)
    else:
        keep = np.unique(np.rint(np.linspace(0, n - 1, maximum_rows)).astype(int))
    return [np.asarray(value)[keep] for value in arrays], keep


def _blockwise_uniform_cap(
    blocks: Sequence[Mapping[str, np.ndarray]],
    names: Sequence[str],
    maximum_rows: int,
) -> tuple[list[np.ndarray], list[dict[str, int]]]:
    """Cap within each already-contiguous block, never across a gap."""

    if not blocks:
        raise ValueError("blockwise cap requires at least one block")
    base = maximum_rows // len(blocks)
    remainder = maximum_rows % len(blocks)
    output = {name: [] for name in names}
    audit = []
    for index, block in enumerate(blocks):
        source_rows = len(np.asarray(block[names[0]]))
        quota = min(source_rows, base + (1 if index < remainder else 0))
        if quota <= 0:
            continue
        keep = (
            np.arange(source_rows, dtype=int)
            if quota == source_rows
            else np.unique(np.rint(np.linspace(0, source_rows - 1, quota)).astype(int))
        )
        for name in names:
            output[name].append(np.asarray(block[name])[keep])
        audit.append({"block_index": index, "source_rows": source_rows, "retained_rows": int(len(keep))})
    return [np.concatenate(output[name]) for name in names], audit


def _axis_blocks(
    pairs: Sequence[AlignedPair], block_rows: int, *, sample_period_s: float,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for pair_index, pair in enumerate(pairs):
        for span_index, span in enumerate(pair.contiguous_spans):
            for block_index, start in enumerate(range(span.start, span.stop, block_rows)):
                stop = min(span.stop, start + block_rows)
                if stop - start < block_rows:
                    continue
                selected = slice(start, stop)
                blocks.append({
                    "pair_index": int(pair_index),
                    "action": pair.action,
                    "span_index": span_index,
                    "block_index": block_index,
                    "acc1": pair.parent_acc[selected],
                    "acc2": pair.child_acc[selected],
                    "gyr1": pair.parent_gyro[selected],
                    "gyr2": pair.child_gyro[selected],
                    "time1_s": (
                        np.asarray(pair.alignment.parent_indices[selected], dtype=float)
                        * float(sample_period_s)
                    ),
                    "time2_s": (
                        np.asarray(pair.alignment.child_indices[selected], dtype=float)
                        * float(sample_period_s)
                    ),
                    "timing_sigma_s": float(pair.alignment.report["lag_uncertainty_s"]),
                })
    return blocks


def _lag1_effective_rows(values: np.ndarray) -> tuple[float, float]:
    correlations = []
    for column in np.asarray(values).T:
        centered = column - np.mean(column)
        denominator = float(centered @ centered)
        if denominator > np.finfo(float).eps:
            correlations.append(float(centered[:-1] @ centered[1:] / denominator))
    rho = float(np.clip(np.median(correlations) if correlations else 0.99, 0.0, 0.99))
    n = len(values)
    return float(max(1.0, n * (1.0 - rho) / (1.0 + rho))), rho


def _axis_centered_selection_metrics(
    block: Mapping[str, Any],
    *,
    parent_acc_covariance: np.ndarray,
    child_acc_covariance: np.ndarray,
    parent_gyro_covariance: np.ndarray,
    child_gyro_covariance: np.ndarray,
    parent_gyro_bias_covariance: np.ndarray,
    child_gyro_bias_covariance: np.ndarray,
    calibration_multiplier: float,
    accelerometer_calibration_multiplier: float,
    gyro_calibration_multiplier: float,
    accelerometer_bias_drift_rate_sigma_mps3: float,
    gyro_bias_drift_correlation_time_s: float,
    accelerometer_scale_cross_axis_fraction_sigma: float,
    gyro_scale_cross_axis_fraction_sigma: float,
    accelerometer_gyro_shared_scale_cross_axis_fraction_sigma: float,
) -> dict[str, Any]:
    """Score one block after the exact result-independent centering transform.

    Constant sensor offsets are annihilated by the componentwise median.  Slow
    drift is evaluated on centered physical time, and multiplicative nuisance
    acts only on the retained centered signal.  The full uncentered arrays and
    static-bias covariance remain available to the later official-QMT
    fixed-point push-forward; they must not suppress selection support.
    """

    gyro_signal = np.column_stack((block["gyr1"], block["gyr2"]))
    acc_signal = np.column_stack((block["acc1"], block["acc2"]))
    gyro_centered = gyro_signal - np.median(gyro_signal, axis=0)
    acc_centered = acc_signal - np.median(acc_signal, axis=0)
    parent_time_centered = np.asarray(block["time1_s"], dtype=float).copy()
    child_time_centered = np.asarray(block["time2_s"], dtype=float).copy()
    parent_time_centered -= np.median(parent_time_centered)
    child_time_centered -= np.median(child_time_centered)
    parent_acc_centered = acc_centered[:, :3]
    child_acc_centered = acc_centered[:, 3:]
    parent_gyro_centered = gyro_centered[:, :3]
    child_gyro_centered = gyro_centered[:, 3:]
    acc_observation_noise = float(np.trace(
        parent_acc_covariance + child_acc_covariance
    ) / 3.0)
    gyro_observation_noise = float(np.trace(
        parent_gyro_covariance + child_gyro_covariance
    ) / 3.0)
    shared_scale_sigma = float(
        accelerometer_gyro_shared_scale_cross_axis_fraction_sigma
    )
    acc_calibration_marginal = float(calibration_multiplier) * (
        float(accelerometer_calibration_multiplier) * (
        float(accelerometer_bias_drift_rate_sigma_mps3) ** 2
        * float(np.mean(parent_time_centered**2) + np.mean(child_time_centered**2))
        + float(accelerometer_scale_cross_axis_fraction_sigma) ** 2
        * float(np.mean(
            np.sum(parent_acc_centered**2, axis=1)
            + np.sum(child_acc_centered**2, axis=1)
        ))
        )
        + shared_scale_sigma**2
        * float(np.mean(
            np.sum(parent_acc_centered**2, axis=1)
            + np.sum(child_acc_centered**2, axis=1)
        ))
    )
    gyro_calibration_marginal = float(calibration_multiplier) * (
        float(gyro_calibration_multiplier) * (
        float(np.trace(parent_gyro_bias_covariance)) / 3.0
        * float(np.mean(parent_time_centered**2))
        / float(gyro_bias_drift_correlation_time_s) ** 2
        + float(np.trace(child_gyro_bias_covariance)) / 3.0
        * float(np.mean(child_time_centered**2))
        / float(gyro_bias_drift_correlation_time_s) ** 2
        + float(gyro_scale_cross_axis_fraction_sigma) ** 2
        * float(np.mean(
            np.sum(parent_gyro_centered**2, axis=1)
            + np.sum(child_gyro_centered**2, axis=1)
        ))
        )
        + shared_scale_sigma**2
        * float(np.mean(
            np.sum(parent_gyro_centered**2, axis=1)
            + np.sum(child_gyro_centered**2, axis=1)
        ))
    )
    gyro_standardized = float(np.sqrt(
        np.mean(gyro_centered**2)
        / max(gyro_observation_noise + gyro_calibration_marginal, np.finfo(float).eps)
    ))
    acc_standardized = float(np.sqrt(
        np.mean(acc_centered**2)
        / max(acc_observation_noise + acc_calibration_marginal, np.finfo(float).eps)
    ))
    effective, rho = _lag1_effective_rows(np.column_stack((gyro_centered, acc_centered)))
    return {
        "gyro_noise_standardized_rms": gyro_standardized,
        "acc_noise_standardized_rms": acc_standardized,
        "gyro_centered_mean_square_rad2_s2": float(np.mean(gyro_centered**2)),
        "acc_centered_mean_square_m2_s4": float(np.mean(acc_centered**2)),
        "gyro_observation_variance_for_support_rad2_s2": gyro_observation_noise,
        "gyro_shared_calibration_marginal_for_support_rad2_s2": gyro_calibration_marginal,
        "acc_observation_variance_for_support_m2_s4": acc_observation_noise,
        "acc_shared_calibration_marginal_for_support_m2_s4": acc_calibration_marginal,
        "quantization_included_in_p1_observation_covariance": True,
        "shared_calibration_counted_as_independent_support_rows": False,
        "static_accelerometer_bias_variance_in_centered_support": 0.0,
        "static_gyro_bias_variance_in_centered_support": 0.0,
        "static_bias_exactly_cancels_under_selection_median_centering": True,
        "scale_cross_axis_support_uses_centered_accelerometer_signal": True,
        "scale_cross_axis_support_uses_centered_gyro_signal": True,
        "gravity_or_removed_accelerometer_mean_used_for_scale_support": False,
        "combined_excitation_score": float(np.hypot(gyro_standardized, acc_standardized)),
        "lag1_correlation": rho,
        "effective_rows": effective,
    }


def numeric_axis_centered_support_transform_gate(
    settings: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Exact mutation oracle for the production centered support transform."""

    rows = int(settings["selection_block_rows"])
    sample_period_s = float(settings["sample_period_s"])
    time = np.arange(rows, dtype=float) * sample_period_s
    phase = 2.0 * np.pi * np.arange(rows, dtype=float) / rows
    parent_acc = np.column_stack((
        0.7 * np.sin(phase), 0.4 * np.cos(2.0 * phase),
        9.80665 + 0.3 * np.sin(3.0 * phase),
    ))
    child_acc = np.column_stack((
        -0.5 * np.sin(phase + 0.2), 0.6 * np.cos(2.0 * phase - 0.1),
        9.7 + 0.25 * np.sin(3.0 * phase + 0.3),
    ))
    parent_gyro = np.column_stack((
        0.8 * np.sin(phase), 0.3 * np.cos(2.0 * phase), 0.2 * np.sin(3.0 * phase),
    ))
    child_gyro = np.column_stack((
        0.7 * np.sin(phase + 0.15), 0.35 * np.cos(2.0 * phase),
        -0.25 * np.sin(3.0 * phase - 0.2),
    ))
    block = {
        "acc1": parent_acc, "acc2": child_acc,
        "gyr1": parent_gyro, "gyr2": child_gyro,
        "time1_s": time, "time2_s": time + 0.0007,
    }
    observation_covariance = np.diag((0.01, 0.015, 0.02))
    gyro_bias_covariance = np.diag((0.0004, 0.0005, 0.0006))

    def score(source: Mapping[str, Any], *, scale_only: bool = False) -> Mapping[str, Any]:
        return _axis_centered_selection_metrics(
            source,
            parent_acc_covariance=observation_covariance,
            child_acc_covariance=observation_covariance * 1.1,
            parent_gyro_covariance=observation_covariance * 0.1,
            child_gyro_covariance=observation_covariance * 0.12,
            parent_gyro_bias_covariance=(
                np.zeros((3, 3)) if scale_only else gyro_bias_covariance
            ),
            child_gyro_bias_covariance=(
                np.zeros((3, 3)) if scale_only else gyro_bias_covariance * 1.2
            ),
            calibration_multiplier=1.0,
            accelerometer_calibration_multiplier=1.0,
            gyro_calibration_multiplier=1.0,
            accelerometer_bias_drift_rate_sigma_mps3=(
                0.0 if scale_only else float(
                    settings["accelerometer_bias_drift_rate_sigma_mps3"]
                )
            ),
            gyro_bias_drift_correlation_time_s=float(
                settings["gyro_bias_drift_correlation_time_s"]
            ),
            accelerometer_scale_cross_axis_fraction_sigma=float(
                settings["accelerometer_scale_cross_axis_fraction_sigma"]
            ),
            gyro_scale_cross_axis_fraction_sigma=float(
                settings["gyro_scale_cross_axis_fraction_sigma"]
            ),
            accelerometer_gyro_shared_scale_cross_axis_fraction_sigma=float(
                settings["accelerometer_gyro_shared_scale_cross_axis_fraction_sigma"]
            ),
        )

    baseline = score(block)
    shifted = score({
        **block,
        "acc1": parent_acc + np.array((4.0, -3.0, 12.0)),
        "acc2": child_acc + np.array((-2.0, 5.0, -8.0)),
        "gyr1": parent_gyro + np.array((1.2, -0.7, 0.4)),
        "gyr2": child_gyro + np.array((-0.6, 1.1, -0.3)),
    })
    invariant_fields = (
        "combined_excitation_score",
        "effective_rows",
        "acc_shared_calibration_marginal_for_support_m2_s4",
        "gyro_shared_calibration_marginal_for_support_rad2_s2",
        "acc_centered_mean_square_m2_s4",
        "gyro_centered_mean_square_rad2_s2",
    )
    invariant = {
        name: bool(np.isclose(
            float(baseline[name]), float(shifted[name]), atol=1e-12, rtol=1e-12,
        ))
        for name in invariant_fields
    }

    def doubled_about_median(value: np.ndarray) -> np.ndarray:
        median = np.median(value, axis=0)
        return median + 2.0 * (value - median)

    scale_baseline = score(block, scale_only=True)
    scale_doubled = score({
        **block,
        "acc1": doubled_about_median(parent_acc),
        "acc2": doubled_about_median(child_acc),
        "gyr1": doubled_about_median(parent_gyro),
        "gyr2": doubled_about_median(child_gyro),
    }, scale_only=True)
    acc_ratio = float(
        scale_doubled["acc_shared_calibration_marginal_for_support_m2_s4"]
        / scale_baseline["acc_shared_calibration_marginal_for_support_m2_s4"]
    )
    gyro_ratio = float(
        scale_doubled["gyro_shared_calibration_marginal_for_support_rad2_s2"]
        / scale_baseline["gyro_shared_calibration_marginal_for_support_rad2_s2"]
    )
    return {
        "schema": "biospur-c2-axis-centered-support-transform-gate-v1",
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": "functional_geometry._axis_centered_selection_metrics",
        "constant_bias_invariant_fields": invariant,
        "constant_bias_invariance_pass": bool(all(invariant.values())),
        "static_accelerometer_bias_support_variance": float(
            baseline["static_accelerometer_bias_variance_in_centered_support"]
        ),
        "static_gyro_bias_support_variance": float(
            baseline["static_gyro_bias_variance_in_centered_support"]
        ),
        "centered_signal_doubling_acc_scale_variance_ratio": acc_ratio,
        "centered_signal_doubling_gyro_scale_variance_ratio": gyro_ratio,
        "centered_scale_quadratic_response_pass": bool(
            np.isclose(acc_ratio, 4.0, atol=1e-10, rtol=1e-10)
            and np.isclose(gyro_ratio, 4.0, atol=1e-10, rtol=1e-10)
        ),
        "removed_gravity_or_mean_used_for_scale_support": False,
        "full_static_bias_retained_for_later_official_qmt_push_forward": True,
        "pass": bool(
            all(invariant.values())
            and baseline["static_accelerometer_bias_variance_in_centered_support"] == 0.0
            and baseline["static_gyro_bias_variance_in_centered_support"] == 0.0
            and np.isclose(acc_ratio, 4.0, atol=1e-10, rtol=1e-10)
            and np.isclose(gyro_ratio, 4.0, atol=1e-10, rtol=1e-10)
        ),
    }


def _blockwise_angular_acceleration_rms(
    blocks: Sequence[Mapping[str, np.ndarray]],
    *,
    sample_period_s: float,
) -> tuple[float, dict[str, Any]]:
    """Differentiate each endpoint inside each selected block only.

    Concatenating blocks before ``gradient`` would create synthetic derivatives
    at gaps. Concatenating parent and child arrays would create a still worse
    parent-to-child boundary derivative. This helper owns that firewall and
    exposes a numeric audit used by the prefit mutation suite.
    """

    dt = float(sample_period_s)
    squared: list[np.ndarray] = []
    rows = []
    for block_index, block in enumerate(blocks):
        for endpoint in ("gyr1", "gyr2"):
            values = np.asarray(block[endpoint], dtype=float)
            if values.ndim != 2 or values.shape[1] != 3 or len(values) < 2:
                raise ValueError("clock-lag sensitivity requires block-local Nx3 gyro")
            derivative = np.gradient(values, dt, axis=0)
            squared.append(derivative**2)
            rows.append({
                "block_index": block_index,
                "endpoint": "parent" if endpoint == "gyr1" else "child",
                "rows": int(len(values)),
                "derivative_rows": int(len(derivative)),
            })
    if not squared:
        raise ValueError("clock-lag sensitivity has no selected blocks")
    return float(np.sqrt(np.mean(np.concatenate(squared, axis=0)))), {
        "method": "NP_GRADIENT_SEPARATELY_INSIDE_EACH_SELECTED_CONTIGUOUS_BLOCK_AND_ENDPOINT",
        "endpoint_block_calls": rows,
        "cross_block_derivative_count": 0,
        "parent_to_child_boundary_derivative_count": 0,
    }


def _qmt_fit(
    acc1: np.ndarray,
    acc2: np.ndarray,
    gyr1: np.ndarray,
    gyr2: np.ndarray,
    *,
    settings: Mapping[str, Any],
    x0: np.ndarray,
) -> dict[str, Any]:
    j1, j2, debug = qmt.jointAxisEstHingeOlsson(
        acc1, acc2, gyr1, gyr2,
        estSettings={
            "w0": float(settings["w0"]),
            "useSampleSelection": False,
            "x0": np.asarray(x0, dtype=float),
            "tol": float(settings["tolerance"]),
            "maxSteps": int(settings["maximum_steps"]),
            "quiet": True,
        },
        debug=True,
    )
    optim = debug["optimVarsAxis"]
    canonical_xhat = np.asarray(debug["xhat"], dtype=float).reshape(4)
    _, _, _, canonical_jacobian, _ = optim["costFunc"](
        canonical_xhat.reshape(4, 1)
    )
    canonical_hessian = np.asarray(canonical_jacobian, dtype=float).T @ np.asarray(
        canonical_jacobian, dtype=float
    )
    optimizer_raw_chart_hessian = np.asarray(optim["Hessian"], dtype=float)
    return {
        "cost": float(optim["f"]),
        "parent": np.asarray(j1, dtype=float).reshape(3),
        "child": np.asarray(j2, dtype=float).reshape(3),
        "hessian": canonical_hessian,
        "optimizer_raw_chart_hessian": optimizer_raw_chart_hessian,
        "canonical_vs_optimizer_raw_hessian_frobenius": float(
            np.linalg.norm(canonical_hessian - optimizer_raw_chart_hessian)
        ),
        "hessian_chart": "OFFICIAL_COSTFUNC_REEVALUATED_AT_CANONICAL_DEBUG_XHAT",
        "xhat": canonical_xhat,
        "steps": int(np.count_nonzero(np.isfinite(np.asarray(optim["ftraj"]).reshape(-1)))),
    }


def _tangent_basis(axis: np.ndarray) -> np.ndarray:
    axis = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    seed = np.eye(3)[int(np.argmin(np.abs(axis)))]
    first = seed - axis * float(seed @ axis)
    first /= np.linalg.norm(first)
    second = np.cross(axis, first)
    return np.column_stack((first, second))


def _sphere_tangent(axis: np.ndarray, basis: np.ndarray, value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, dtype=float) / np.linalg.norm(value)
    if float(value @ axis) < 0:
        value = -value
    dot = float(np.clip(value @ axis, -1.0, 1.0))
    angle = float(np.arccos(dot))
    tangent = value - dot * axis
    norm = float(np.linalg.norm(tangent))
    return np.zeros(2) if norm <= 1e-12 else angle * (basis.T @ tangent) / norm


def _axis_vectors_from_qmt_spherical(xhat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    value = np.asarray(xhat, dtype=float).reshape(4)
    vectors = []
    for offset in (0, 2):
        elevation, azimuth = value[offset:offset + 2]
        vectors.append(np.array((
            np.cos(elevation) * np.cos(azimuth),
            np.cos(elevation) * np.sin(azimuth),
            np.sin(elevation),
        )))
    return vectors[0], vectors[1]


def _qmt_official_cost_closure(
    acc1: np.ndarray,
    acc2: np.ndarray,
    gyr1: np.ndarray,
    gyr2: np.ndarray,
    *,
    settings: Mapping[str, Any],
    xhat: np.ndarray,
) -> Any:
    """Return QMT's official cost closure for fixed input arrays."""

    _, _, debug = qmt.jointAxisEstHingeOlsson(
        acc1, acc2, gyr1, gyr2,
        estSettings={
            "w0": float(settings["w0"]),
            "useSampleSelection": False,
            "x0": np.asarray(xhat, dtype=float),
            "tol": float(settings["tolerance"]),
            # QMT's public settings validator requires maxSteps > 1.  The two
            # initialization steps are not consumed: only the returned official
            # cost closure evaluated at the caller's frozen xhat is used below.
            "maxSteps": 2,
            "quiet": True,
        },
        debug=True,
    )
    return debug["optimVarsAxis"]["costFunc"]


def _qmt_official_fixed_point_gradient(
    acc1: np.ndarray,
    acc2: np.ndarray,
    gyr1: np.ndarray,
    gyr2: np.ndarray,
    *,
    settings: Mapping[str, Any],
    xhat: np.ndarray,
) -> np.ndarray:
    """Evaluate the official Olsson objective gradient without consuming a refit."""

    cost_closure = _qmt_official_cost_closure(
        acc1, acc2, gyr1, gyr2, settings=settings, xhat=xhat,
    )
    _, gradient, _, _, _ = cost_closure(np.asarray(xhat, dtype=float).reshape(4, 1))
    return np.asarray(gradient, dtype=float).reshape(4)


def _qmt_exact_score_hessian_audit(
    acc1: np.ndarray,
    acc2: np.ndarray,
    gyr1: np.ndarray,
    gyr2: np.ndarray,
    *,
    settings: Mapping[str, Any],
    xhat: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Audit the exact-score chart used by the implicit nuisance response."""

    primary_step = float(settings["linearization_score_hessian_step_rad"])
    steps = tuple(
        float(value)
        for value in settings["linearization_score_hessian_step_sensitivity_rad"]
    )
    required_rank = int(settings["exact_score_hessian_required_rank"])
    relative_rank_tolerance = float(settings["hessian_relative_rank_tolerance"])
    maximum_condition = float(
        settings["exact_score_hessian_maximum_condition_number"]
    )
    if (
        primary_step <= 0.0
        or not steps
        or primary_step not in steps
        or any(step <= 0.0 for step in steps)
        or len(set(steps)) != len(steps)
        or required_rank != 4
        or not 0.0 < relative_rank_tolerance < 1.0
        or maximum_condition <= 1.0
    ):
        raise ValueError("axis exact-score Hessian audit design is invalid")
    cost_closure = _qmt_official_cost_closure(
        acc1, acc2, gyr1, gyr2, settings=settings, xhat=xhat,
    )
    xhat = np.asarray(xhat, dtype=float).reshape(4)
    rows: list[dict[str, Any]] = []
    hessians: dict[float, np.ndarray] = {}
    for step in steps:
        hessian = np.zeros((4, 4), dtype=float)
        for coordinate in range(4):
            delta = np.zeros(4, dtype=float)
            delta[coordinate] = step
            _, positive_gradient, _, _, _ = cost_closure(
                (xhat + delta).reshape(4, 1)
            )
            _, negative_gradient, _, _, _ = cost_closure(
                (xhat - delta).reshape(4, 1)
            )
            hessian[:, coordinate] = (
                np.asarray(positive_gradient, dtype=float).reshape(4)
                - np.asarray(negative_gradient, dtype=float).reshape(4)
            ) / (2.0 * step)
        hessian = 0.5 * (hessian + hessian.T)
        try:
            eigenvalues = np.linalg.eigvalsh(hessian)
        except np.linalg.LinAlgError:
            eigenvalues = np.full(4, np.nan, dtype=float)
        finite = bool(np.all(np.isfinite(hessian)) and np.all(np.isfinite(eigenvalues)))
        maximum = float(np.max(eigenvalues)) if finite else float("nan")
        positive_threshold = maximum * relative_rank_tolerance if maximum > 0.0 else float("inf")
        retained = eigenvalues >= positive_threshold
        rank = int(np.count_nonzero(retained & (eigenvalues > 0.0))) if finite else 0
        minimum = float(np.min(eigenvalues)) if finite else float("nan")
        condition = (
            float(maximum / minimum)
            if finite and minimum > 0.0 else float("inf")
        )
        step_pass = bool(
            finite
            and rank == required_rank
            and minimum > 0.0
            and condition <= maximum_condition
        )
        hessians[step] = hessian
        rows.append({
            "step_rad": step,
            "eigenvalues": [
                float(value) if np.isfinite(value) else None
                for value in eigenvalues
            ],
            "finite": finite,
            "positive_local_curvature": bool(finite and minimum > 0.0),
            "relative_rank_tolerance": relative_rank_tolerance,
            "relative_rank_threshold": (
                positive_threshold if np.isfinite(positive_threshold) else None
            ),
            "rank": rank,
            "required_rank": required_rank,
            "condition_number": condition if np.isfinite(condition) else None,
            "condition_number_defined": bool(np.isfinite(condition)),
            "maximum_condition_number": maximum_condition,
            "pass": step_pass,
        })
    primary_hessian = hessians[primary_step]
    primary_norm = max(float(np.linalg.norm(primary_hessian)), np.finfo(float).eps)
    for row in rows:
        relative_difference = float(
            np.linalg.norm(hessians[float(row["step_rad"])] - primary_hessian)
            / primary_norm
        )
        row["relative_frobenius_difference_from_primary_step"] = (
            relative_difference if np.isfinite(relative_difference) else None
        )
    return primary_hessian, {
        "schema": "biospur-c2-axis-exact-score-hessian-audit-v1",
        "chart": "OFFICIAL_COST_GRADIENT_AT_CANONICAL_FROZEN_XHAT",
        "central_difference_steps_rad": list(steps),
        "primary_step_rad": primary_step,
        "step_results": rows,
        "every_registered_step_finite_full_rank_positive_curvature": bool(
            all(row["pass"] for row in rows)
        ),
        "optimizer_raw_chart_hessian_used": False,
        "canonical_jtj_used_as_exact_score_hessian": False,
        "pass": bool(all(row["pass"] for row in rows)),
    }


def _axis_antithetic_implicit_covariance(
    *,
    baseline_arrays: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    perturbations: Sequence[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]],
    settings: Mapping[str, Any],
    xhat: np.ndarray,
    canonical_jtj: np.ndarray,
    exact_score_hessian: np.ndarray,
    exact_score_hessian_audit: Mapping[str, Any],
    parent_axis: np.ndarray,
    child_axis: np.ndarray,
    parent_basis: np.ndarray,
    child_basis: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Push coherent input perturbations through the official fixed-point score."""

    acc1, acc2, gyr1, gyr2 = baseline_arrays
    online_owner = dict(settings.get("online_branch_posterior_owner", {}))
    defer_full_refit_validation = bool(
        online_owner.get("enabled", False)
        and online_owner.get("full_coherent_nuisance_refits_online", True) is False
    )
    validation_replicates = int(settings["linearization_validation_replicates"])
    validation_scales = tuple(
        float(value) for value in settings["linearization_validation_nuisance_scales"]
    )
    validation_required_scales = tuple(
        float(value) for value in settings["linearization_validation_required_scales"]
    )
    validation_absolute_tolerance = float(
        settings["linearization_validation_absolute_tolerance_rad"]
    )
    validation_relative_tolerance = float(
        settings["linearization_validation_relative_tolerance"]
    )
    validation_minimum_response = float(
        settings["linearization_validation_minimum_informative_response_rad"]
    )
    validation_minimum_endpoint_dot = float(
        settings["linearization_validation_minimum_endpoint_dot"]
    )
    if (
        validation_replicates < 1
        or not validation_scales
        or any(not 0.0 < value <= 1.0 for value in validation_scales)
        or any(value not in validation_scales for value in validation_required_scales)
        or 1.0 not in validation_required_scales
        or validation_absolute_tolerance <= 0.0
        or validation_relative_tolerance < 0.0
        or validation_minimum_response <= 0.0
        or not 0.0 <= validation_minimum_endpoint_dot < 1.0
    ):
        raise ValueError("axis fixed-point linearization validation design is invalid")
    exact_score_hessian = np.asarray(exact_score_hessian, dtype=float)
    exact_hessian_eligible = bool(exact_score_hessian_audit["pass"])
    inverse_score_hessian = (
        np.linalg.inv(exact_score_hessian) if exact_hessian_eligible else None
    )
    tangent_responses = []
    failures = []
    validation_rows = []

    def implicit_response(
        perturbation: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    ) -> np.ndarray:
        if inverse_score_hessian is None:
            raise RuntimeError(
                "exact official score Hessian failed finite full-rank positive-curvature gate"
            )
        dacc1, dacc2, dgyr1, dgyr2 = perturbation
        positive_gradient = _qmt_official_fixed_point_gradient(
            acc1 + dacc1, acc2 + dacc2, gyr1 + dgyr1, gyr2 + dgyr2,
            settings=settings, xhat=xhat,
        )
        negative_gradient = _qmt_official_fixed_point_gradient(
            acc1 - dacc1, acc2 - dacc2, gyr1 - dgyr1, gyr2 - dgyr2,
            settings=settings, xhat=xhat,
        )
        gradient_response = 0.5 * (positive_gradient - negative_gradient)
        spherical_response = -inverse_score_hessian @ gradient_response
        response_parent, response_child = _axis_vectors_from_qmt_spherical(
            np.asarray(xhat, dtype=float) + spherical_response
        )
        return np.r_[
            _sphere_tangent(parent_axis, parent_basis, response_parent),
            _sphere_tangent(child_axis, child_basis, response_child),
        ]

    def refit_tangent(
        perturbation: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    ) -> tuple[np.ndarray, dict[str, Any]]:
        dacc1, dacc2, dgyr1, dgyr2 = perturbation
        fit = _qmt_fit(
            acc1 + dacc1, acc2 + dacc2, gyr1 + dgyr1, gyr2 + dgyr2,
            settings=settings, x0=xhat,
        )
        refit_parent = np.asarray(fit["parent"], dtype=float)
        refit_child = np.asarray(fit["child"], dtype=float)
        if float(refit_parent @ parent_axis + refit_child @ child_axis) < 0.0:
            refit_parent = -refit_parent
            refit_child = -refit_child
        endpoint_dots = np.array((
            float(refit_parent @ parent_axis), float(refit_child @ child_axis),
        ))
        if np.any(endpoint_dots < validation_minimum_endpoint_dot):
            raise RuntimeError(
                "official QMT validation refit switched branch or left the primary basin"
            )
        return np.r_[
            _sphere_tangent(parent_axis, parent_basis, refit_parent),
            _sphere_tangent(child_axis, child_basis, refit_child),
        ], {
            "endpoint_dots_after_simultaneous_sign_alignment": endpoint_dots.tolist(),
            "selected_cost": float(fit["cost"]),
            "official_refit_steps": int(fit["steps"]),
        }

    for index, (dacc1, dacc2, dgyr1, dgyr2) in enumerate(perturbations):
        try:
            perturbation = (dacc1, dacc2, dgyr1, dgyr2)
            tangent = implicit_response(perturbation)
            if not np.all(np.isfinite(tangent)):
                raise FloatingPointError("axis nuisance tangent response is nonfinite")
            tangent_responses.append(tangent)
            if index < validation_replicates and not defer_full_refit_validation:
                for validation_scale in validation_scales:
                    scaled = tuple(validation_scale * value for value in perturbation)
                    predicted = implicit_response(scaled)
                    positive, positive_audit = refit_tangent(scaled)
                    negative, negative_audit = refit_tangent(
                        tuple(-value for value in scaled)
                    )
                    observed = 0.5 * (positive - negative)
                    difference_norm = float(np.linalg.norm(predicted - observed))
                    observed_norm = float(np.linalg.norm(observed))
                    relative_difference = float(
                        difference_norm / max(observed_norm, validation_minimum_response)
                    )
                    required = validation_scale in validation_required_scales
                    informative = observed_norm >= validation_minimum_response
                    row_pass = bool(
                        difference_norm <= validation_absolute_tolerance
                        and relative_difference <= validation_relative_tolerance
                        and (informative or not required)
                    )
                    validation_rows.append({
                        "replicate": index,
                        "nuisance_scale": validation_scale,
                        "required_for_owner_update": required,
                        "predicted_tangent_response_rad": predicted.tolist(),
                        "official_refit_antithetic_tangent_response_rad": observed.tolist(),
                        "absolute_difference_norm_rad": difference_norm,
                        "relative_difference": relative_difference,
                        "official_refit_response_norm_rad": observed_norm,
                        "minimum_informative_response_rad": validation_minimum_response,
                        "informative_response": informative,
                        "absolute_tolerance_rad": validation_absolute_tolerance,
                        "relative_tolerance": validation_relative_tolerance,
                        "pass": row_pass,
                        "positive_refit": positive_audit,
                        "negative_refit": negative_audit,
                        "failed_or_branch_switched_refit_dropped": False,
                    })
        except (ValueError, RuntimeError, FloatingPointError, np.linalg.LinAlgError) as exc:
            failures.append({"replicate": index, "exception": f"{type(exc).__name__}:{exc}"})
            if index < validation_replicates:
                validation_rows.append({
                    "replicate": index,
                    "nuisance_scale": None,
                    "required_for_owner_update": True,
                    "pass": False,
                    "failure": f"{type(exc).__name__}:{exc}",
                    "failed_or_branch_switched_refit_dropped": False,
                })
    if tangent_responses:
        responses = np.asarray(tangent_responses, dtype=float)
        covariance = sum(
            (np.outer(row, row) for row in responses),
            start=np.zeros((4, 4), dtype=float),
        ) / len(responses)
    else:
        responses = np.empty((0, 4), dtype=float)
        covariance = np.eye(4) * float(settings["low_information_no_update_sigma_rad"]) ** 2
    covariance = 0.5 * (covariance + covariance.T)
    primary_hessian_step = float(settings["linearization_score_hessian_step_rad"])
    primary_hessian_row = next(
        row for row in exact_score_hessian_audit["step_results"]
        if float(row["step_rad"]) == primary_hessian_step
    )
    jtj_approximation_difference = float(np.linalg.norm(
        exact_score_hessian - 2.0 * np.asarray(canonical_jtj, dtype=float)
    ))
    return covariance, {
        "official_public_qmt_called_for_every_successful_antithetic_side": (
            not defer_full_refit_validation
        ),
        "official_cost_closure_initialization_steps": 2,
        "official_cost_closure_initialization_fit_consumed_as_response": False,
        "method": (
            "IMPLICIT_FIXED_POINT_SIGNED_SCORE_SENSITIVITY_USING_OFFICIAL_"
            "QMT_COST_AND_EXACT_SCORE_HESSIAN_AT_CANONICAL_FROZEN_XHAT"
        ),
        "requested_replicates": len(perturbations),
        "successful_replicates": len(tangent_responses),
        "failed_replicates": failures,
        "tangent_response_rms_rad": (
            float(np.sqrt(np.mean(responses**2))) if len(responses) else None
        ),
        "common_nuisance_draw_reused_coherently_across_rows": True,
        "nuisance_rows_counted_as_independent_votes": False,
        "official_refit_linearization_validation": {
            "schema": "biospur-c2-axis-official-qmt-fixed-point-linearization-agreement-v1",
            "score_hessian": "CENTRAL_DIFFERENCE_OF_OFFICIAL_COST_GRADIENT_AT_FROZEN_PRIMARY_XHAT",
            "score_hessian_step_rad": primary_hessian_step,
            "score_hessian_eigenvalues": primary_hessian_row["eigenvalues"],
            "exact_score_hessian_audit": dict(exact_score_hessian_audit),
            "exact_score_hessian_eligible_for_inverse": exact_hessian_eligible,
            "singular_or_indefinite_exact_hessian_pseudoinverse_used": False,
            "two_times_official_jtj_approximation_difference_frobenius": float(
                jtj_approximation_difference
            ) if np.isfinite(jtj_approximation_difference) else None,
            "two_times_official_jtj_used_as_authoritative_response_hessian": False,
            "spherical_response_then_product_s2_tangent_transport": True,
            "validation_replicates": validation_replicates,
            "nuisance_scales": list(validation_scales),
            "required_scales": list(validation_required_scales),
            "absolute_tolerance_rad": validation_absolute_tolerance,
            "relative_tolerance": validation_relative_tolerance,
            "minimum_informative_response_rad": validation_minimum_response,
            "minimum_endpoint_dot_after_simultaneous_sign_alignment": (
                validation_minimum_endpoint_dot
            ),
            "rows": validation_rows,
            "all_rows_pass": bool(
                not defer_full_refit_validation
                and
                len(validation_rows)
                == min(validation_replicates, len(perturbations)) * len(validation_scales)
                and all(
                    row["pass"] for row in validation_rows
                    if row["required_for_owner_update"]
                )
                and any(
                    row["required_for_owner_update"]
                    for row in validation_rows
                )
            ),
            "failed_or_branch_switched_refits_retained": True,
            "status": (
                "DEFERRED_TO_OFFLINE_FINAL_VALIDATION"
                if defer_full_refit_validation else "EXECUTED_ONLINE"
            ),
            "required_for_online_owner_admission": False,
            "replacement_estimator": False,
        },
    }


def _online_axis_candidate_mixture(
    trials: Sequence[Mapping[str, Any]],
    *,
    best_cost: float,
    enabled: bool,
    initial_still_present: bool,
    core_axis_candidate_eligible: bool,
    low_information_sigma_rad: float,
) -> Mapping[str, Any]:
    """Retain finite official-QMT candidates at the online owner boundary.

    Low excitation/rank/support remains explicit evidence, but it widens the
    retained product-S2 posterior instead of erasing a finite numerical
    candidate.  Nonfinite candidates and initial-still hinge updates remain
    ineligible.
    """

    finite = [
        trial for trial in trials
        if np.isfinite(float(trial["cost"]))
        and np.all(np.isfinite(np.asarray(trial["parent"], dtype=float)))
        and np.all(np.isfinite(np.asarray(trial["child"], dtype=float)))
    ]
    unnormalized = np.asarray([
        np.exp(-(float(trial["cost"]) - float(best_cost))) for trial in finite
    ], dtype=float)
    if len(unnormalized) and (
        not np.all(np.isfinite(unnormalized)) or float(np.sum(unnormalized)) <= 0.0
    ):
        raise RuntimeError("finite QMT axis candidates have invalid posterior weights")
    weights = (
        unnormalized / float(np.sum(unnormalized))
        if len(unnormalized) else np.empty(0, dtype=float)
    )
    branches = [
        {
            "candidate_index": int(trial["index"]),
            "parent_axis_sensor": np.asarray(trial["parent"], dtype=float).tolist(),
            "child_axis_sensor": np.asarray(trial["child"], dtype=float).tolist(),
            "official_qmt_cost": float(trial["cost"]),
            "weight": float(weight),
            "finite": True,
        }
        for trial, weight in zip(finite, weights, strict=True)
    ]
    owner_update_eligible = bool(
        enabled and branches and not initial_still_present
    )
    low_information_retained = bool(
        owner_update_eligible and not core_axis_candidate_eligible
    )
    low_information_covariance = (
        np.eye(4) * float(low_information_sigma_rad) ** 2
        if low_information_retained else np.zeros((4, 4), dtype=float)
    )
    return {
        "branches": branches,
        "owner_update_eligible": owner_update_eligible,
        "low_information_candidate_retained": low_information_retained,
        "low_information_systematic_covariance_rad2": low_information_covariance,
        "nonfinite_candidate_count": int(len(trials) - len(finite)),
        "candidate_weight_sum": float(np.sum(weights)),
    }


def estimate_hinge_axis_qmt(
    edge: str,
    pairs: Sequence[AlignedPair],
    *,
    settings: Mapping[str, Any],
    parent_acc_covariance: np.ndarray,
    child_acc_covariance: np.ndarray,
    parent_gyro_covariance: np.ndarray,
    child_gyro_covariance: np.ndarray,
    parent_gyro_bias_covariance: np.ndarray,
    child_gyro_bias_covariance: np.ndarray,
    execution_guard: C2ExecutionGuard,
) -> AxisEstimate:
    """Execute official QMT/Olsson after frozen noise-aware block selection."""

    execution_guard.validate_row_selection(str(settings["selection_policy"]))
    online_owner = dict(settings.get("online_branch_posterior_owner", {}))
    online_owner_enabled = bool(online_owner.get("enabled", False))
    if online_owner_enabled and (
        online_owner.get("schema")
        != "biospur-c2-online-branch-posterior-owner-v1"
        or online_owner.get("full_coherent_nuisance_refits_online") is not False
        or float(online_owner.get("incomplete_nuisance_axis_sigma_rad", 0.0)) <= 0.0
    ):
        raise ValueError("online axis branch-posterior owner settings are invalid")
    if edge not in HINGE_EDGES or not pairs:
        raise ValueError("QMT hinge estimator requires a routed hinge edge")
    parameter_dimension = int(settings["local_tangent_parameter_dimension"])
    if parameter_dimension != 4:
        raise ValueError("hinge product-S2 local tangent parameter dimension must be four")
    if bool(settings["initial_still_functional_hinge_update_allowed"]):
        raise ValueError("initial still may not authorize a functional hinge-axis update")
    minimum_allowed_support = (
        parameter_dimension
        * float(settings["minimum_allowed_sensitivity_support_per_parameter"])
    )
    minimum_effective_support = float(settings["minimum_effective_support_rows"])
    if minimum_effective_support < minimum_allowed_support:
        raise ValueError(
            "effective-support floor is below the registered per-parameter "
            "uncertainty-stability sensitivity bound"
        )
    parent_acc_covariance = _validated_center_covariance(
        "parent axis accelerometer observation", parent_acc_covariance,
    )
    child_acc_covariance = _validated_center_covariance(
        "child axis accelerometer observation", child_acc_covariance,
    )
    parent_gyro_covariance = _validated_center_covariance(
        "parent axis gyro observation", parent_gyro_covariance,
    )
    child_gyro_covariance = _validated_center_covariance(
        "child axis gyro observation", child_gyro_covariance,
    )
    parent_gyro_bias_covariance = _validated_center_covariance(
        "parent axis gyro bias", parent_gyro_bias_covariance,
    )
    child_gyro_bias_covariance = _validated_center_covariance(
        "child axis gyro bias", child_gyro_bias_covariance,
    )
    calibration_multiplier = float(
        settings.get("calibration_nuisance_covariance_multiplier", 1.0)
    )
    accelerometer_calibration_multiplier = float(
        settings.get("accelerometer_calibration_nuisance_multiplier", 1.0)
    )
    gyro_calibration_multiplier = float(
        settings.get("gyro_calibration_nuisance_multiplier", 1.0)
    )
    if min(
        calibration_multiplier,
        accelerometer_calibration_multiplier,
        gyro_calibration_multiplier,
    ) < 0.0:
        raise ValueError("axis calibration nuisance covariance multiplier must be nonnegative")
    acc_bias_sigma = float(settings["accelerometer_unresolved_bias_sigma_mps2"])
    acc_drift_rate = float(settings["accelerometer_bias_drift_rate_sigma_mps3"])
    acc_drift_horizon = float(settings["accelerometer_bias_drift_horizon_s"])
    gyro_drift_horizon = float(settings["gyro_bias_drift_correlation_time_s"])
    acc_scale_sigma = float(settings["accelerometer_scale_cross_axis_fraction_sigma"])
    gyro_scale_sigma = float(settings["gyro_scale_cross_axis_fraction_sigma"])
    shared_scale_sigma = float(
        settings["accelerometer_gyro_shared_scale_cross_axis_fraction_sigma"]
    )
    nuisance_replicates = int(settings["calibration_nuisance_ensemble_replicates"])
    nuisance_seed = int(settings["calibration_nuisance_ensemble_seed"])
    if (
        acc_bias_sigma <= 0.0 or acc_drift_rate <= 0.0
        or acc_drift_horizon <= 0.0 or gyro_drift_horizon <= 0.0
        or min(acc_scale_sigma, gyro_scale_sigma, shared_scale_sigma) < 0.0
        or nuisance_replicates < 4
    ):
        raise ValueError("axis calibration nuisance design is invalid")
    initial_still_present = any(pair.action == "00_initial_still" for pair in pairs)
    block_rows = int(settings["selection_block_rows"])
    blocks = _axis_blocks(
        pairs, block_rows, sample_period_s=float(settings["sample_period_s"]),
    )
    if not blocks:
        raise ValueError(f"{edge}: no gap-safe QMT blocks")
    audits = []
    for block in blocks:
        metrics = _axis_centered_selection_metrics(
            block,
            parent_acc_covariance=parent_acc_covariance,
            child_acc_covariance=child_acc_covariance,
            parent_gyro_covariance=parent_gyro_covariance,
            child_gyro_covariance=child_gyro_covariance,
            parent_gyro_bias_covariance=parent_gyro_bias_covariance,
            child_gyro_bias_covariance=child_gyro_bias_covariance,
            calibration_multiplier=calibration_multiplier,
            accelerometer_calibration_multiplier=accelerometer_calibration_multiplier,
            gyro_calibration_multiplier=gyro_calibration_multiplier,
            accelerometer_bias_drift_rate_sigma_mps3=acc_drift_rate,
            gyro_bias_drift_correlation_time_s=gyro_drift_horizon,
            accelerometer_scale_cross_axis_fraction_sigma=acc_scale_sigma,
            gyro_scale_cross_axis_fraction_sigma=gyro_scale_sigma,
            accelerometer_gyro_shared_scale_cross_axis_fraction_sigma=shared_scale_sigma,
        )
        score = float(metrics["combined_excitation_score"])
        block["score"] = score
        block["effective_rows"] = float(metrics["effective_rows"])
        audits.append({
            "pair_index": block["pair_index"],
            "action": block["action"],
            "span_index": block["span_index"],
            "block_index": block["block_index"],
            "rows": block_rows,
            **metrics,
        })
    threshold = float(settings["minimum_noise_standardized_excitation"])
    selected = [block for block in blocks if block["score"] >= threshold]
    selection_status = "NOISE_STANDARDIZED_THRESHOLD_MET"
    if not selected:
        selected = [max(blocks, key=lambda value: value["score"])]
        selection_status = "NO_BLOCK_MET_THRESHOLD_LOW_INFORMATION_TOP_BLOCK_RETAINED"
    selected_with_cluster = [
        {**block, "cluster_id": np.full(len(block["acc1"]), index, dtype=np.int64)}
        for index, block in enumerate(selected)
    ]
    (
        acc1, acc2, gyr1, gyr2, time1_s, time2_s, cluster_id,
    ), cap_audit = _blockwise_uniform_cap(
        selected_with_cluster,
        ("acc1", "acc2", "gyr1", "gyr2", "time1_s", "time2_s", "cluster_id"),
        int(settings["maximum_rows"]),
    )
    rng = np.random.default_rng(int(settings["multistart_seed"]))
    starts = rng.uniform(-np.pi, np.pi, size=(int(settings["multistarts"]), 4))
    trials = []
    for index, x0 in enumerate(starts):
        trial = _qmt_fit(acc1, acc2, gyr1, gyr2, settings=settings, x0=x0)
        trials.append({**trial, "index": index})
    best = min(trials, key=lambda row: (row["cost"], row["index"]))
    best_parent = best["parent"] / np.linalg.norm(best["parent"])
    best_child = best["child"] / np.linalg.norm(best["child"])
    parent_basis = _tangent_basis(best_parent)
    child_basis = _tangent_basis(best_child)
    multistart_tangent = np.asarray([
        np.r_[
            _sphere_tangent(best_parent, parent_basis, trial["parent"]),
            _sphere_tangent(best_child, child_basis, trial["child"]),
        ] for trial in trials
    ])
    bootstrap_tangent = []
    bootstrap_rng = np.random.default_rng(int(settings["bootstrap_seed"]))
    for _ in range(int(settings["bootstrap_replicates"])):
        chosen = bootstrap_rng.integers(0, len(selected), size=len(selected))
        bootstrap_blocks = [selected[index] for index in chosen]
        (ba1, ba2, bg1, bg2), _ = _blockwise_uniform_cap(
            bootstrap_blocks, ("acc1", "acc2", "gyr1", "gyr2"), int(settings["maximum_rows"]),
        )
        fit = _qmt_fit(ba1, ba2, bg1, bg2, settings=settings, x0=best["xhat"])
        bootstrap_tangent.append(np.r_[
            _sphere_tangent(best_parent, parent_basis, fit["parent"]),
            _sphere_tangent(best_child, child_basis, fit["child"]),
        ])
    bootstrap_tangent = np.asarray(bootstrap_tangent)
    covariance = np.zeros((4, 4), dtype=float)
    if len(bootstrap_tangent) > 1:
        covariance += np.atleast_2d(np.cov(bootstrap_tangent.T, ddof=1))
    if len(multistart_tangent) > 1:
        covariance += np.atleast_2d(np.cov(multistart_tangent.T, ddof=1))
    bootstrap_multistart_statistical_covariance = covariance.copy()

    def covariance_sqrt(value: np.ndarray) -> np.ndarray:
        eigenvalues_local, eigenvectors_local = np.linalg.eigh(
            0.5 * (np.asarray(value, dtype=float) + np.asarray(value, dtype=float).T)
        )
        return (
            eigenvectors_local * np.sqrt(np.maximum(eigenvalues_local, 0.0))
        ) @ eigenvectors_local.T

    baseline_arrays = (acc1, acc2, gyr1, gyr2)
    exact_score_hessian, exact_score_hessian_audit = (
        _qmt_exact_score_hessian_audit(
            acc1, acc2, gyr1, gyr2,
            settings=settings,
            xhat=best["xhat"],
        )
    )
    zero_acc1 = np.zeros_like(acc1)
    zero_acc2 = np.zeros_like(acc2)
    zero_gyr1 = np.zeros_like(gyr1)
    zero_gyr2 = np.zeros_like(gyr2)
    acc_observation_roots = (
        covariance_sqrt(parent_acc_covariance),
        covariance_sqrt(child_acc_covariance),
    )
    gyro_observation_roots = (
        covariance_sqrt(parent_gyro_covariance),
        covariance_sqrt(child_gyro_covariance),
    )
    acc_static_bias_root = np.eye(3) * acc_bias_sigma
    acc_drift_rate_root = np.eye(3) * acc_drift_rate
    gyro_bias_roots = (
        covariance_sqrt(parent_gyro_bias_covariance),
        covariance_sqrt(child_gyro_bias_covariance),
    )
    time1_centered = np.asarray(time1_s, dtype=float) - float(np.median(time1_s))
    time2_centered = np.asarray(time2_s, dtype=float) - float(np.median(time2_s))
    nuisance_amplitude = float(np.sqrt(calibration_multiplier))
    accelerometer_nuisance_amplitude = float(np.sqrt(
        calibration_multiplier * accelerometer_calibration_multiplier
    ))
    gyro_nuisance_amplitude = float(np.sqrt(
        calibration_multiplier * gyro_calibration_multiplier
    ))

    def component_covariance(
        name: str,
        seed_offset: int,
        draw: Any,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        component_rng = np.random.default_rng(nuisance_seed + seed_offset)
        perturbations = [draw(component_rng) for _ in range(nuisance_replicates)]
        component, audit = _axis_antithetic_implicit_covariance(
            baseline_arrays=baseline_arrays,
            perturbations=perturbations,
            settings=settings,
            xhat=best["xhat"],
            canonical_jtj=best["hessian"],
            exact_score_hessian=exact_score_hessian,
            exact_score_hessian_audit=exact_score_hessian_audit,
            parent_axis=best_parent,
            child_axis=best_child,
            parent_basis=parent_basis,
            child_basis=child_basis,
        )
        return component, {**audit, "component": name, "seed": nuisance_seed + seed_offset}

    def observation_draw(local_rng: np.random.Generator) -> tuple[np.ndarray, ...]:
        dacc1 = np.zeros_like(acc1)
        dacc2 = np.zeros_like(acc2)
        dgyr1 = np.zeros_like(gyr1)
        dgyr2 = np.zeros_like(gyr2)
        for value in np.unique(cluster_id):
            mask = cluster_id == value
            dacc1[mask] = acc_observation_roots[0] @ local_rng.normal(size=3)
            dacc2[mask] = acc_observation_roots[1] @ local_rng.normal(size=3)
            dgyr1[mask] = gyro_observation_roots[0] @ local_rng.normal(size=3)
            dgyr2[mask] = gyro_observation_roots[1] @ local_rng.normal(size=3)
        return dacc1, dacc2, dgyr1, dgyr2

    observation_covariance, observation_audit = component_covariance(
        "P1_OBSERVATION_QUANTIZATION_AND_COMPLETE_BLOCK_CORRELATION",
        0,
        observation_draw,
    )
    accelerometer_bias_drift_covariance, acc_bias_audit = component_covariance(
        "ACCELEROMETER_BIAS_DRIFT",
        100,
        lambda local_rng: (
            accelerometer_nuisance_amplitude * (
                (acc_static_bias_root @ local_rng.normal(size=3))[None, :]
                + time1_centered[:, None]
                * (acc_drift_rate_root @ local_rng.normal(size=3))[None, :]
            ),
            accelerometer_nuisance_amplitude * (
                (acc_static_bias_root @ local_rng.normal(size=3))[None, :]
                + time2_centered[:, None]
                * (acc_drift_rate_root @ local_rng.normal(size=3))[None, :]
            ),
            zero_gyr1.copy(),
            zero_gyr2.copy(),
        ),
    )
    gyro_bias_covariance, gyro_bias_audit = component_covariance(
        "GYRO_BIAS",
        200,
        lambda local_rng: (
            zero_acc1.copy(),
            zero_acc2.copy(),
            np.broadcast_to(
                gyro_nuisance_amplitude
                * (gyro_bias_roots[0] @ local_rng.normal(size=3)),
                gyr1.shape,
            ).copy(),
            np.broadcast_to(
                gyro_nuisance_amplitude
                * (gyro_bias_roots[1] @ local_rng.normal(size=3)),
                gyr2.shape,
            ).copy(),
        ),
    )
    gyro_bias_drift_covariance, gyro_drift_audit = component_covariance(
        "GYRO_BIAS_DRIFT",
        300,
        lambda local_rng: (
            zero_acc1.copy(),
            zero_acc2.copy(),
            time1_centered[:, None]
            * gyro_nuisance_amplitude
            * (gyro_bias_roots[0] @ local_rng.normal(size=3))[None, :]
            / gyro_drift_horizon,
            time2_centered[:, None]
            * gyro_nuisance_amplitude
            * (gyro_bias_roots[1] @ local_rng.normal(size=3))[None, :]
            / gyro_drift_horizon,
        ),
    )

    def scale_draw(
        local_rng: np.random.Generator,
        *,
        acc_sigma: float,
        gyro_sigma: float,
        shared: bool,
    ) -> tuple[np.ndarray, ...]:
        parent_acc_scale = local_rng.normal(size=(3, 3)) * acc_sigma * nuisance_amplitude
        child_acc_scale = local_rng.normal(size=(3, 3)) * acc_sigma * nuisance_amplitude
        if shared:
            parent_gyro_scale = parent_acc_scale
            child_gyro_scale = child_acc_scale
        else:
            parent_gyro_scale = local_rng.normal(size=(3, 3)) * gyro_sigma * nuisance_amplitude
            child_gyro_scale = local_rng.normal(size=(3, 3)) * gyro_sigma * nuisance_amplitude
        return (
            acc1 @ parent_acc_scale.T,
            acc2 @ child_acc_scale.T,
            gyr1 @ parent_gyro_scale.T,
            gyr2 @ child_gyro_scale.T,
        )

    accelerometer_scale_covariance, acc_scale_audit = component_covariance(
        "ACCELEROMETER_SCALE_CROSS_AXIS",
        400,
        lambda local_rng: (
            acc1 @ (
                local_rng.normal(size=(3, 3))
                * acc_scale_sigma * accelerometer_nuisance_amplitude
            ).T,
            acc2 @ (
                local_rng.normal(size=(3, 3))
                * acc_scale_sigma * accelerometer_nuisance_amplitude
            ).T,
            zero_gyr1.copy(),
            zero_gyr2.copy(),
        ),
    )
    gyro_scale_covariance, gyro_scale_audit = component_covariance(
        "GYRO_SCALE_CROSS_AXIS",
        500,
        lambda local_rng: (
            zero_acc1.copy(),
            zero_acc2.copy(),
            gyr1 @ (
                local_rng.normal(size=(3, 3))
                * gyro_scale_sigma * gyro_nuisance_amplitude
            ).T,
            gyr2 @ (
                local_rng.normal(size=(3, 3))
                * gyro_scale_sigma * gyro_nuisance_amplitude
            ).T,
        ),
    )
    shared_scale_covariance, shared_scale_audit = component_covariance(
        "ACCELEROMETER_GYRO_SHARED_SCALE_CROSS_AXIS",
        600,
        lambda local_rng: scale_draw(
            local_rng,
            acc_sigma=shared_scale_sigma,
            gyro_sigma=shared_scale_sigma,
            shared=True,
        ),
    )
    statistical_covariance = (
        bootstrap_multistart_statistical_covariance + observation_covariance
    )
    floor = np.deg2rad(float(settings["human_worn_axis_floor_deg"]))
    angular_acceleration_rms, derivative_audit = _blockwise_angular_acceleration_rms(
        selected, sample_period_s=float(settings["sample_period_s"]),
    )
    pair_clock_sigma_s = max(
        float(row.alignment.report["lag_uncertainty_s"]) for row in pairs
    )
    timing_sigma = min(np.pi / 2.0, pair_clock_sigma_s * angular_acceleration_rms)

    def block_local_derivative(values: np.ndarray) -> np.ndarray:
        derivative = np.zeros_like(values)
        for value in np.unique(cluster_id):
            mask = np.flatnonzero(cluster_id == value)
            if len(mask) >= 2:
                derivative[mask] = np.gradient(
                    values[mask], float(settings["sample_period_s"]), axis=0,
                )
        return derivative

    child_acc_clock_derivative = block_local_derivative(acc2)
    child_gyro_clock_derivative = block_local_derivative(gyr2)

    def pair_clock_draw(local_rng: np.random.Generator) -> tuple[np.ndarray, ...]:
        offset_s = pair_clock_sigma_s * local_rng.normal()
        return (
            zero_acc1.copy(),
            child_acc_clock_derivative * offset_s,
            zero_gyr1.copy(),
            child_gyro_clock_derivative * offset_s,
        )

    pair_clock_covariance, pair_clock_audit = component_covariance(
        "PERSISTENT_PAIR_CLOCK_OFFSET",
        700,
        pair_clock_draw,
    )
    systematic_components = {
        "accelerometer_bias_drift": accelerometer_bias_drift_covariance,
        "accelerometer_scale_cross_axis": accelerometer_scale_covariance,
        "accelerometer_gyro_shared_scale_cross_axis": shared_scale_covariance,
        "gyro_bias": gyro_bias_covariance,
        "gyro_bias_drift": gyro_bias_drift_covariance,
        "gyro_scale_cross_axis": gyro_scale_covariance,
        "persistent_pair_clock": pair_clock_covariance,
        "human_worn": np.eye(4) * floor**2,
    }
    systematic_covariance = sum(
        systematic_components.values(), start=np.zeros((4, 4), dtype=float),
    )
    nuisance_audits = {
        "observation_statistical": observation_audit,
        "accelerometer_bias_drift": acc_bias_audit,
        "accelerometer_scale_cross_axis": acc_scale_audit,
        "accelerometer_gyro_shared_scale_cross_axis": shared_scale_audit,
        "gyro_bias": gyro_bias_audit,
        "gyro_bias_drift": gyro_drift_audit,
        "gyro_scale_cross_axis": gyro_scale_audit,
        "persistent_pair_clock": pair_clock_audit,
    }
    nuisance_sensitivity_complete = all(
        row["successful_replicates"] == row["requested_replicates"]
        and row["official_refit_linearization_validation"]["all_rows_pass"]
        for row in nuisance_audits.values()
    )
    incomplete_nuisance_axis_covariance = np.zeros((4, 4), dtype=float)
    if online_owner_enabled and not nuisance_sensitivity_complete:
        incomplete_nuisance_axis_covariance = np.eye(4) * float(
            online_owner["incomplete_nuisance_axis_sigma_rad"]
        ) ** 2
        statistical_covariance = (
            statistical_covariance + incomplete_nuisance_axis_covariance
        )
    tangent_spread = np.linalg.norm(multistart_tangent, axis=1)
    eigenvalues = np.linalg.eigvalsh(best["hessian"])
    effective_support = float(sum(block["effective_rows"] for block in selected))
    hessian_maximum = max(float(np.max(eigenvalues)), 0.0)
    hessian_keep = (
        (eigenvalues >= hessian_maximum * float(settings["hessian_relative_rank_tolerance"]))
        & (eigenvalues > 0.0)
        if hessian_maximum > 0.0 else np.zeros(4, dtype=bool)
    )
    hessian_rank = int(np.count_nonzero(hessian_keep))
    selected_observed_rows = int(sum(len(block["acc1"]) for block in selected))
    core_axis_candidate_eligible = bool(
        selection_status == "NOISE_STANDARDIZED_THRESHOLD_MET"
        and selected_observed_rows >= int(settings["minimum_selected_observed_rows"])
        and effective_support >= minimum_effective_support
        and hessian_rank == 4
        and exact_score_hessian_audit["pass"]
        and not initial_still_present
    )
    online_axis_mixture = _online_axis_candidate_mixture(
        trials,
        best_cost=float(best["cost"]),
        enabled=online_owner_enabled,
        initial_still_present=initial_still_present,
        core_axis_candidate_eligible=core_axis_candidate_eligible,
        low_information_sigma_rad=float(settings["low_information_no_update_sigma_rad"]),
    )
    online_low_information_covariance = np.asarray(
        online_axis_mixture["low_information_systematic_covariance_rad2"],
        dtype=float,
    )
    if bool(online_axis_mixture["low_information_candidate_retained"]):
        systematic_components["online_low_information_retained_candidate"] = (
            online_low_information_covariance
        )
        systematic_covariance = (
            systematic_covariance + online_low_information_covariance
        )
    owner_update_eligible = bool(
        bool(online_axis_mixture["owner_update_eligible"])
        or (
            core_axis_candidate_eligible
            and nuisance_sensitivity_complete
        )
    )
    if not owner_update_eligible:
        statistical_covariance += np.eye(4) * float(settings["low_information_no_update_sigma_rad"]) ** 2
    covariance = statistical_covariance + systematic_covariance
    return AxisEstimate(
        edge=edge,
        parent_axis_sensor=best_parent,
        child_axis_sensor=best_child,
        tangent_covariance_rad2=covariance,
        report={
            "schema": "biospur-c2-qmt-olsson-hinge-axis-v3",
            "official_public_function": "qmt.jointAxisEstHingeOlsson",
            "qmt_version": "0.2.4",
            "actions": [row.action for row in pairs],
            "gap_safe_blocks_total": len(blocks),
            "gap_safe_blocks_selected": len(selected),
            "block_selection_status": selection_status,
            "selection": "FIXED_CONTIGUOUS_BLOCKS;P1_NOISE_STANDARDIZED_EXCITATION_THRESHOLD;RESULT_INDEPENDENT_UNIFORM_CAP_ONLY_AFTER_SELECTION",
            "selection_block_audit": audits,
            "effective_support_rows": effective_support,
            "minimum_effective_support_rows": minimum_effective_support,
            "local_tangent_parameter_dimension": parameter_dimension,
            "minimum_effective_support_per_parameter": float(
                settings["minimum_effective_support_per_parameter"]
            ),
            "minimum_allowed_sensitivity_support_per_parameter": float(
                settings["minimum_allowed_sensitivity_support_per_parameter"]
            ),
            "initial_still_present": initial_still_present,
            "initial_still_functional_hinge_update_allowed": bool(
                settings["initial_still_functional_hinge_update_allowed"]
            ),
            "input_rows_after_selection_before_cap": selected_observed_rows,
            "minimum_selected_observed_rows": int(settings["minimum_selected_observed_rows"]),
            "input_rows_after_result_independent_cap": int(len(acc1)),
            "blockwise_cap_audit": cap_audit,
            "rows_crossing_gap_or_boot_transition": 0,
            "w0": float(settings["w0"]),
            "multistarts": len(trials),
            "multistart_seed": int(settings["multistart_seed"]),
            "bootstrap_replicates": int(settings["bootstrap_replicates"]),
            "bootstrap_seed": int(settings["bootstrap_seed"]),
            "selected_start": int(best["index"]),
            "selected_cost": float(best["cost"]),
            "axis_sign_resolved": False,
            "tangent_parameterization": "PRODUCT_S2_LOCAL_TANGENT_[PARENT_T1,PARENT_T2,CHILD_T1,CHILD_T2]",
            "parent_tangent_basis_sensor": parent_basis.tolist(),
            "child_tangent_basis_sensor": child_basis.tolist(),
            "tangent_covariance_shape": list(covariance.shape),
            "bootstrap_multistart_statistical_tangent_covariance_rad2": (
                bootstrap_multistart_statistical_covariance.tolist()
            ),
            "observation_quantization_block_correlation_statistical_tangent_covariance_rad2": (
                observation_covariance.tolist()
            ),
            "statistical_tangent_covariance_rad2": statistical_covariance.tolist(),
            "systematic_component_tangent_covariances_rad2": {
                name: component.tolist()
                for name, component in systematic_components.items()
            },
            "total_systematic_tangent_covariance_rad2": systematic_covariance.tolist(),
            "systematic_human_worn_tangent_covariance_rad2": (
                systematic_components["human_worn"].tolist()
            ),
            "systematic_human_worn_floor_may_shrink_across_episodes": False,
            "tangent_covariance_sources": [
                "GAP_SAFE_BLOCK_BOOTSTRAP_AND_MULTISTART",
                "P1_OBSERVATION_QUANTIZATION_COMPLETE_BLOCK_PERTURBATION",
                "OFFICIAL_QMT_FIXED_POINT_SIGNED_CALIBRATION_NUISANCE_PUSH_FORWARD",
                "PERSISTENT_PAIR_CLOCK_CHILD_OFFSET_BLOCK_LOCAL_PUSH_FORWARD",
                "HUMAN_WORN_SHARED_SYSTEMATIC_FLOOR",
            ],
            "axis_calibration_nuisance_audit": nuisance_audits,
            "axis_calibration_nuisance_covariance_multiplier": calibration_multiplier,
            "axis_accelerometer_calibration_nuisance_multiplier": (
                accelerometer_calibration_multiplier
            ),
            "axis_gyro_calibration_nuisance_multiplier": gyro_calibration_multiplier,
            "axis_calibration_nuisance_ensemble_replicates": nuisance_replicates,
            "axis_calibration_nuisance_ensemble_seed": nuisance_seed,
            "axis_calibration_nuisance_push_forward_complete": (
                nuisance_sensitivity_complete
            ),
            "online_branch_posterior_owner_enabled": online_owner_enabled,
            "full_coherent_nuisance_refits_required_for_online_admission": False,
            "core_axis_candidate_eligible_before_offline_validation": (
                core_axis_candidate_eligible
            ),
            "online_finite_candidate_owner_update_eligible": bool(
                online_axis_mixture["owner_update_eligible"]
            ),
            "online_low_information_candidate_retained": bool(
                online_axis_mixture["low_information_candidate_retained"]
            ),
            "online_low_information_systematic_covariance_rad2": (
                online_low_information_covariance.tolist()
            ),
            "low_information_erased_finite_official_qmt_candidate": False,
            "incomplete_nuisance_axis_covariance_rad2": (
                incomplete_nuisance_axis_covariance.tolist()
            ),
            "raw_fraction_squared_relabelled_as_tangent_rad2": False,
            "online_axis_candidate_branches": list(
                online_axis_mixture["branches"]
            ),
            "online_axis_candidate_weight_sum": float(
                online_axis_mixture["candidate_weight_sum"]
            ),
            "online_axis_nonfinite_candidate_count": int(
                online_axis_mixture["nonfinite_candidate_count"]
            ),
            "axis_calibration_nuisance_counted_as_independent_rows": False,
            "accelerometer_static_bias_retained_in_official_qmt_push_forward": True,
            "accelerometer_static_bias_used_in_centered_support": False,
            "gyro_static_bias_retained_in_official_qmt_push_forward": True,
            "gyro_static_bias_used_in_centered_support": False,
            "scale_cross_axis_support_uses_centered_signals": True,
            "scale_cross_axis_fixed_point_push_forward_uses_full_official_qmt_inputs": True,
            "human_worn_axis_floor_deg": float(settings["human_worn_axis_floor_deg"]),
            "clock_lag_sensitivity_sigma_deg": float(np.degrees(timing_sigma)),
            "clock_lag_angular_acceleration_rms_rad_s2": angular_acceleration_rms,
            "clock_lag_derivative_audit": derivative_audit,
            "persistent_pair_clock_fixed_point_audit": pair_clock_audit,
            "persistent_pair_clock_offset_convention": (
                "POSITIVE_OFFSET_PERTURBS_CHILD_AT_FIXED_PARENT_PHYSICAL_TIME"
            ),
            "persistent_pair_clock_cross_block_derivative_count": 0,
            "max_sign_invariant_multistart_spread_deg": float(np.degrees(np.max(tangent_spread))),
            "hessian_eigenvalues_diagnostic_only": eigenvalues.tolist(),
            "hessian_chart": best["hessian_chart"],
            "optimizer_raw_chart_hessian_not_used": True,
            "canonical_jtj_at_canonical_xhat_diagnostic": best[
                "hessian"
            ].tolist(),
            "optimizer_raw_chart_jtj_diagnostic_only": best[
                "optimizer_raw_chart_hessian"
            ].tolist(),
            "optimizer_raw_chart_jtj_eigenvalues_diagnostic_only": (
                np.linalg.eigvalsh(
                    0.5 * (
                        best["optimizer_raw_chart_hessian"]
                        + best["optimizer_raw_chart_hessian"].T
                    )
                ).tolist()
            ),
            "canonical_vs_optimizer_raw_hessian_frobenius": (
                best["canonical_vs_optimizer_raw_hessian_frobenius"]
            ),
            "hessian_informed_rank": hessian_rank,
            "hessian_relative_rank_tolerance": float(settings["hessian_relative_rank_tolerance"]),
            "canonical_jtj_informed_rank": hessian_rank,
            "canonical_jtj_chart": best["hessian_chart"],
            "optimizer_raw_chart_hessian_used_for_rank_or_uncertainty": False,
            "exact_score_hessian_audit": exact_score_hessian_audit,
            "exact_score_hessian_required_for_owner_update": True,
            "owner_update_mode": (
                (
                    "ONLINE_PRODUCT_S2_BRANCH_POSTERIOR_WITH_MARGINALIZED_"
                    "INCOMPLETE_NUISANCE"
                ) if core_axis_candidate_eligible and online_owner_enabled
                else (
                    "ONLINE_FINITE_LOW_INFORMATION_PRODUCT_S2_BRANCH_"
                    "POSTERIOR_WITH_BROAD_NONSHRINKING_COVARIANCE"
                ) if bool(online_axis_mixture["low_information_candidate_retained"])
                else "FULL_PRODUCT_S2_INFORMED_UPDATE" if owner_update_eligible
                else "LOCAL_NO_UPDATE_LOW_EXCITATION_OR_RANK_OR_EFFECTIVE_SUPPORT"
            ),
            "owner_update_eligible": owner_update_eligible,
            "low_information_no_update_sigma_rad": float(settings["low_information_no_update_sigma_rad"]),
            "top_block_retained_for_diagnostics_not_ingested_when_below_threshold": True,
            "upstream_example_alignment_flip_rom_or_rating_reused": False,
            "ordinary_nonideal_axis_widens_uncertainty": True,
        },
    )


def _center_terms(
    gyro: np.ndarray, *, dt: float, window: int, polynomial: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    smoothed_gyro = savgol_filter(
        gyro, window_length=window, polyorder=polynomial,
        deriv=0, axis=0, mode="interp",
    )
    alpha = savgol_filter(
        gyro, window_length=window, polyorder=polynomial,
        deriv=1, delta=dt, axis=0, mode="interp",
    )
    basis = np.zeros((len(gyro), 3, 3), dtype=float)
    eye = np.eye(3)
    for column in range(3):
        direction = np.repeat(eye[column][None, :], len(gyro), axis=0)
        basis[:, :, column] = (
            np.cross(alpha, direction)
            + np.cross(smoothed_gyro, np.cross(smoothed_gyro, direction))
        )
    return basis, alpha, smoothed_gyro


def _center_boot_safe_centered_elapsed(
    observed_time_s: np.ndarray,
    physical_time_epoch_group: np.ndarray,
    *,
    horizon_s: float,
) -> np.ndarray:
    """Center real time within each trustworthy boot-epoch identity.

    Disjoint spans and actions carrying the same epoch identity remain on the
    same physical axis, preserving known gap duration. Cross-epoch intervals
    are never joined; the caller must mark the whole local factor no-update.
    """

    observed = np.asarray(observed_time_s, dtype=float)
    epoch_groups = np.asarray(physical_time_epoch_group, dtype=np.int64)
    if (
        observed.ndim != 1
        or epoch_groups.shape != observed.shape
        or not np.all(np.isfinite(observed))
        or horizon_s <= 0.0
    ):
        raise ValueError("center observed physical-time transform input is invalid")
    centered = np.empty_like(observed)
    for group in np.unique(epoch_groups):
        mask = epoch_groups == group
        values = observed[mask]
        centered[mask] = values - float(np.median(values))
    return np.clip(centered, -float(horizon_s), float(horizon_s))


def _center_cross_pair_interval_event(
    *,
    pair_index: int,
    previous_action: str,
    action: str,
    previous_epoch_key: tuple[int, int],
    current_epoch_key: tuple[int, int],
    previous_stop_time_s: tuple[float, float],
    current_start_time_s: tuple[float, float],
) -> dict[str, Any] | None:
    """Classify a chronological pair boundary without inventing elapsed time."""

    epoch_changed = current_epoch_key != previous_epoch_key
    nonmonotonic_reset = bool(
        current_start_time_s[0] <= previous_stop_time_s[0]
        or current_start_time_s[1] <= previous_stop_time_s[1]
    )
    if not epoch_changed and not nonmonotonic_reset:
        return None
    return {
        "pair_index": int(pair_index),
        "previous_action": str(previous_action),
        "action": str(action),
        "previous_epoch_key": list(previous_epoch_key),
        "current_epoch_key": list(current_epoch_key),
        "previous_stop_time_s": list(previous_stop_time_s),
        "current_start_time_s": list(current_start_time_s),
        "epoch_changed": bool(epoch_changed),
        "nonmonotonic_reset": bool(nonmonotonic_reset),
        "elapsed_interval_invented": False,
        "owner_disposition": "LOCAL_NO_UPDATE_UNKNOWN_CROSS_PAIR_INTERVAL",
    }


def numeric_center_physical_time_ownership_gate() -> dict[str, Any]:
    """Exact production-helper mutation for known gaps and unknown epochs."""

    within_span = np.arange(6, dtype=float) * 0.005
    short_gap_time = np.r_[within_span, 0.050 + within_span]
    long_gap_time = np.r_[within_span, 5.000 + within_span]
    same_epoch = np.zeros(len(short_gap_time), dtype=np.int64)
    short_elapsed = _center_boot_safe_centered_elapsed(
        short_gap_time, same_epoch, horizon_s=10.0,
    )
    long_elapsed = _center_boot_safe_centered_elapsed(
        long_gap_time, same_epoch, horizon_s=10.0,
    )
    selected = np.asarray([0, 2, 4, 7, 9, 11], dtype=np.int64)
    permutation = np.asarray([5, 0, 3, 1, 4, 2], dtype=np.int64)
    capped_values = long_gap_time[selected]
    permuted_values = capped_values[permutation]
    restored_values = permuted_values[np.argsort(permutation)]
    known_boundary = _center_cross_pair_interval_event(
        pair_index=1,
        previous_action="KNOWN_A",
        action="KNOWN_B",
        previous_epoch_key=(0, 0),
        current_epoch_key=(0, 0),
        previous_stop_time_s=(0.025, 0.025),
        current_start_time_s=(5.000, 5.000),
    )
    changed_epoch = _center_cross_pair_interval_event(
        pair_index=1,
        previous_action="BOOT_A",
        action="BOOT_B",
        previous_epoch_key=(0, 0),
        current_epoch_key=(1, 1),
        previous_stop_time_s=(10.0, 10.0),
        current_start_time_s=(0.0, 0.0),
    )
    nonmonotonic_reset = _center_cross_pair_interval_event(
        pair_index=1,
        previous_action="RESET_A",
        action="RESET_B",
        previous_epoch_key=(0, 0),
        current_epoch_key=(0, 0),
        previous_stop_time_s=(10.0, 10.0),
        current_start_time_s=(0.0, 0.0),
    )
    return {
        "schema": "biospur-c2-center-physical-time-ownership-gate-v1",
        "owner_call_path": [
            "functional_geometry._center_boot_safe_centered_elapsed",
            "functional_geometry._center_cross_pair_interval_event",
            "functional_geometry.estimate_joint_center_pair_local",
        ],
        "units": "s",
        "equal_retained_row_count": len(short_gap_time) == len(long_gap_time),
        "short_gap_centered_elapsed_sha256": _array_sha256(short_elapsed),
        "long_gap_centered_elapsed_sha256": _array_sha256(long_elapsed),
        "different_known_same_boot_gap_changes_drift_time": bool(
            not np.array_equal(short_elapsed, long_elapsed)
        ),
        "same_boot_cross_pair_boundary_is_trustworthy": known_boundary is None,
        "cap_selected_physical_time_sha256": _array_sha256(capped_values),
        "permutation_restored_physical_time_sha256": _array_sha256(restored_values),
        "cap_did_not_synthesize_physical_time": bool(
            np.array_equal(capped_values, long_gap_time[selected])
        ),
        "permutation_did_not_change_physical_time_values": bool(
            np.array_equal(restored_values, capped_values)
        ),
        "cross_pair_epoch_transition": changed_epoch,
        "cross_pair_nonmonotonic_reset": nonmonotonic_reset,
        "unknown_epoch_or_reset_local_no_update": bool(
            changed_epoch is not None
            and nonmonotonic_reset is not None
            and changed_epoch["owner_disposition"]
            == "LOCAL_NO_UPDATE_UNKNOWN_CROSS_PAIR_INTERVAL"
            and nonmonotonic_reset["owner_disposition"]
            == "LOCAL_NO_UPDATE_UNKNOWN_CROSS_PAIR_INTERVAL"
        ),
        "progressive_unknown_interval_floor_consumption_proven": False,
        "pass": bool(
            len(short_gap_time) == len(long_gap_time)
            and not np.array_equal(short_elapsed, long_elapsed)
            and known_boundary is None
            and np.array_equal(restored_values, capped_values)
            and changed_epoch is not None
            and nonmonotonic_reset is not None
        ),
    }


def _validated_center_covariance(name: str, value: np.ndarray) -> np.ndarray:
    covariance = np.asarray(value, dtype=float)
    if (
        covariance.shape != (3, 3)
        or not np.all(np.isfinite(covariance))
        or not np.allclose(covariance, covariance.T, atol=1e-12, rtol=0.0)
        or float(np.min(np.linalg.eigvalsh(covariance))) < -1e-12
    ):
        raise ValueError(f"{name} center stochastic covariance must be finite symmetric PSD 3x3")
    return 0.5 * (covariance + covariance.T)


def _skew(value: np.ndarray) -> np.ndarray:
    x, y, z = np.asarray(value, dtype=float)
    return np.array(((0.0, -z, y), (z, 0.0, -x), (-y, x, 0.0)))


def _center_stochastic_residual_sigma(
    parameters: np.ndarray,
    *,
    parent_acc: np.ndarray,
    child_acc: np.ndarray,
    parent_terms: np.ndarray,
    child_terms: np.ndarray,
    parent_gyro: np.ndarray,
    child_gyro: np.ndarray,
    parent_alpha: np.ndarray,
    child_alpha: np.ndarray,
    clock_lag_variance_s2: np.ndarray,
    cluster_id: np.ndarray,
    parent_acc_covariance: np.ndarray,
    child_acc_covariance: np.ndarray,
    parent_acc_bias_drift_covariance: np.ndarray,
    child_acc_bias_drift_covariance: np.ndarray,
    parent_gyro_observation_covariance: np.ndarray,
    child_gyro_observation_covariance: np.ndarray,
    parent_gyro_bias_covariance: np.ndarray,
    child_gyro_bias_covariance: np.ndarray,
    alpha_observation_noise_gain_s2_inv: float,
    accelerometer_scale_cross_axis_fraction_sigma: float,
    gyro_scale_cross_axis_fraction_sigma: float,
    accelerometer_gyro_shared_scale_cross_axis_fraction_sigma: float,
    gyro_bias_drift_correlation_time_s: float,
    noise_sigma_multiplier: float,
    serial_correlation_variance_envelope_multiplier: float,
    sample_period_s: float,
) -> tuple[np.ndarray, dict[str, Any], dict[str, np.ndarray]]:
    """Propagate immutable sensor noise and coherent nuisances without mixing roles.

    Filtered white observation/derivative noise receives the registered
    kernel-correlation envelope.  Bias, drift, and scale/cross-axis states
    remain coherent signed-Jacobian nuisances: their diagonal marginal is used
    only to standardize robust rows, while their cross-row covariance is owned
    by the separate low-rank systematic push-forward below.
    """

    rp, rc = np.asarray(parameters[:3], dtype=float), np.asarray(parameters[3:], dtype=float)
    corrected_parent = parent_acc - np.einsum("nij,j->ni", parent_terms, rp)
    corrected_child = child_acc - np.einsum("nij,j->ni", child_terms, rc)
    parent_unit = corrected_parent / np.maximum(
        np.linalg.norm(corrected_parent, axis=1, keepdims=True), np.finfo(float).eps,
    )
    child_unit = corrected_child / np.maximum(
        np.linalg.norm(corrected_child, axis=1, keepdims=True), np.finfo(float).eps,
    )

    def endpoint_variance(
        unit: np.ndarray,
        r: np.ndarray,
        acc_observation: np.ndarray,
        omega: np.ndarray,
        alpha: np.ndarray,
        acc_covariance: np.ndarray,
        acc_bias_drift_covariance: np.ndarray,
        gyro_observation_covariance: np.ndarray,
        gyro_bias_covariance: np.ndarray,
        residual_sign: float,
    ) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
        alpha_gradient = unit @ _skew(r)
        omega_jacobian = (
            np.einsum("n,ij->nij", omega @ r, np.eye(3))
            + np.einsum("ni,j->nij", omega, r)
            - 2.0 * np.einsum("i,nj->nij", r, omega)
        )
        omega_gradient = -np.einsum("ni,nij->nj", unit, omega_jacobian)
        acc_variance = np.einsum("ni,ij,nj->n", unit, acc_covariance, unit)
        acc_bias_drift_variance = np.einsum(
            "ni,ij,nj->n", unit, acc_bias_drift_covariance, unit,
        )
        omega_observation_variance = np.einsum(
            "ni,ij,nj->n", omega_gradient, gyro_observation_covariance,
            omega_gradient,
        )
        omega_bias_variance = np.einsum(
            "ni,ij,nj->n", omega_gradient, gyro_bias_covariance,
            omega_gradient,
        )
        alpha_observation_covariance = (
            float(alpha_observation_noise_gain_s2_inv) * gyro_observation_covariance
        )
        alpha_observation_variance = np.einsum(
            "ni,ij,nj->n", alpha_gradient, alpha_observation_covariance,
            alpha_gradient,
        )
        alpha_drift_covariance = gyro_bias_covariance / float(
            gyro_bias_drift_correlation_time_s
        ) ** 2
        alpha_drift_variance = np.einsum(
            "ni,ij,nj->n", alpha_gradient, alpha_drift_covariance,
            alpha_gradient,
        )
        acc_scale_jacobian = np.einsum(
            "ni,nj->nij", residual_sign * unit, acc_observation,
        ).reshape(len(unit), 9)
        gyro_scale_jacobian = residual_sign * (
            np.einsum("ni,nj->nij", omega_gradient, omega)
            + np.einsum("ni,nj->nij", alpha_gradient, alpha)
        ).reshape(len(unit), 9)
        acc_scale_variance = (
            float(accelerometer_scale_cross_axis_fraction_sigma) ** 2
            * np.sum(acc_scale_jacobian**2, axis=1)
        )
        gyro_scale_variance = (
            float(gyro_scale_cross_axis_fraction_sigma) ** 2
            * np.sum(gyro_scale_jacobian**2, axis=1)
        )
        shared_scale_jacobian = acc_scale_jacobian + gyro_scale_jacobian
        shared_scale_variance = (
            float(accelerometer_gyro_shared_scale_cross_axis_fraction_sigma) ** 2
            * np.sum(shared_scale_jacobian**2, axis=1)
        )
        components = {
            "acc": acc_variance,
            "acc_bias_drift": acc_bias_drift_variance,
            "gyro_observation": omega_observation_variance,
            "gyro_bias": omega_bias_variance,
            "alpha_observation": alpha_observation_variance,
            "alpha_bias_drift": alpha_drift_variance,
            "accelerometer_scale_cross_axis": acc_scale_variance,
            "gyro_scale_cross_axis": gyro_scale_variance,
            "accelerometer_gyro_shared_scale_cross_axis": shared_scale_variance,
            "acc_bias_gradient": residual_sign * unit,
            "acc_scale_jacobian": acc_scale_jacobian,
            "gyro_scale_jacobian": gyro_scale_jacobian,
            "shared_scale_jacobian": shared_scale_jacobian,
            "omega_gradient": omega_gradient,
            "alpha_gradient": alpha_gradient,
        }
        independent_observation_variance = sum(
            components[key]
            for key in ("acc", "gyro_observation", "alpha_observation")
        )
        shared_calibration_variance = sum(
            components[key]
            for key in (
                "acc_bias_drift", "gyro_bias", "alpha_bias_drift",
                "accelerometer_scale_cross_axis", "gyro_scale_cross_axis",
                "accelerometer_gyro_shared_scale_cross_axis",
            )
        )
        components["independent_observation_total"] = (
            independent_observation_variance
        )
        components["shared_calibration_total"] = shared_calibration_variance
        return (
            independent_observation_variance,
            shared_calibration_variance,
            components,
        )

    (
        parent_independent_variance,
        parent_shared_calibration_variance,
        parent_components,
    ) = endpoint_variance(
        parent_unit, rp, parent_acc, parent_gyro, parent_alpha, parent_acc_covariance,
        parent_acc_bias_drift_covariance, parent_gyro_observation_covariance,
        parent_gyro_bias_covariance, 1.0,
    )
    (
        child_independent_variance,
        child_shared_calibration_variance,
        child_components,
    ) = endpoint_variance(
        child_unit, rc, child_acc, child_gyro, child_alpha, child_acc_covariance,
        child_acc_bias_drift_covariance, child_gyro_observation_covariance,
        child_gyro_bias_covariance, -1.0,
    )
    independent_filtered_observation_variance = (
        max(float(noise_sigma_multiplier), 0.0) ** 2
        * float(serial_correlation_variance_envelope_multiplier)
        * (parent_independent_variance + child_independent_variance)
    )
    shared_calibration_marginal_variance = (
        parent_shared_calibration_variance
        + child_shared_calibration_variance
    )
    signed_clock_offset_gradient = np.zeros(len(corrected_child), dtype=float)
    for block_id in np.unique(cluster_id):
        mask = np.asarray(cluster_id) == block_id
        child_corrected_norm = np.linalg.norm(corrected_child[mask], axis=1)
        if len(child_corrected_norm) >= 2:
            # Positive pair-clock offset evaluates the child observation later
            # while the parent physical time remains fixed.  Therefore
            # d(||fp||-||fc(t+delta)||)/d(delta) = -d||fc||/dt.
            signed_clock_offset_gradient[mask] = -np.gradient(
                child_corrected_norm, float(sample_period_s),
            )
    timing_variance_mps4 = (
        signed_clock_offset_gradient**2
        * np.asarray(clock_lag_variance_s2, dtype=float)
    )
    total_variance = (
        independent_filtered_observation_variance
        + shared_calibration_marginal_variance
        + timing_variance_mps4
    )
    sigma = np.sqrt(np.maximum(total_variance, np.finfo(float).eps))
    component_means = {
        f"parent_{key}_variance_mean_m2ps4": float(np.mean(value))
        for key, value in parent_components.items()
        if not key.endswith("gradient") and not key.endswith("jacobian")
    }
    component_means.update({
        f"child_{key}_variance_mean_m2ps4": float(np.mean(value))
        for key, value in child_components.items()
        if not key.endswith("gradient") and not key.endswith("jacobian")
    })
    component_means.update({
        "independent_filtered_observation_variance_mean_m2ps4": float(
            np.mean(independent_filtered_observation_variance)
        ),
        "shared_calibration_marginal_variance_mean_m2ps4": float(
            np.mean(shared_calibration_marginal_variance)
        ),
        "timing_variance_mean_m2ps4": float(np.mean(timing_variance_mps4)),
        "residual_sigma_rms_mps2": float(np.sqrt(np.mean(sigma**2))),
        "residual_sigma_minimum_mps2": float(np.min(sigma)),
        "residual_sigma_maximum_mps2": float(np.max(sigma)),
        "white_observation_noise_sigma_multiplier": float(
            noise_sigma_multiplier
        ),
        "white_observation_serial_correlation_variance_envelope_multiplier": float(
            serial_correlation_variance_envelope_multiplier
        ),
        "shared_calibration_noise_sigma_multiplier": 1.0,
        "shared_calibration_serial_correlation_variance_envelope_multiplier": 1.0,
        "shared_calibration_marginal_used_only_for_robust_row_standardization": True,
        "shared_calibration_covariance_added_to_repeatable_episode_information": False,
        "shared_calibration_row_marginal_role": (
            "ROBUST_STANDARDIZATION_ONLY_NOT_AN_INDEPENDENT_COVARIANCE_ADDITION"
        ),
        "shared_calibration_low_rank_pushforward_role": (
            "AUTHORITATIVE_SHARED_SYSTEMATIC_POSTERIOR_COMPONENT"
        ),
        "shared_calibration_white_noise_filter_envelope_applied": False,
        "shared_calibration_white_noise_sigma_multiplier_applied": False,
    })
    gradients = {
        "parent_acc_bias": parent_components["acc_bias_gradient"],
        "child_acc_bias": child_components["acc_bias_gradient"],
        "parent_acc_scale": parent_components["acc_scale_jacobian"],
        "child_acc_scale": child_components["acc_scale_jacobian"],
        "parent_gyro_scale": parent_components["gyro_scale_jacobian"],
        "child_gyro_scale": child_components["gyro_scale_jacobian"],
        "parent_shared_scale": parent_components["shared_scale_jacobian"],
        "child_shared_scale": child_components["shared_scale_jacobian"],
        "parent_omega": parent_components["omega_gradient"],
        "parent_alpha": parent_components["alpha_gradient"],
        "child_omega": -child_components["omega_gradient"],
        "child_alpha": -child_components["alpha_gradient"],
        "signed_clock_offset": signed_clock_offset_gradient,
    }
    return sigma, component_means, gradients


def estimate_joint_center_pair_local(
    edge: str,
    parent: str,
    child: str,
    pairs: Sequence[AlignedPair],
    *,
    settings: Mapping[str, Any],
    parent_acc_covariance: np.ndarray,
    child_acc_covariance: np.ndarray,
    parent_gyro_observation_covariance: np.ndarray,
    child_gyro_observation_covariance: np.ndarray,
    parent_gyro_bias_covariance: np.ndarray,
    child_gyro_bias_covariance: np.ndarray,
    execution_guard: C2ExecutionGuard,
) -> CenterEstimate:
    """Bounded Seel-style norm constraint; never a body-wide optimizer.

    The fitted coordinates are joint-to-sensor vectors in each local sensor
    frame. Corrected specific force is ``f - K(omega, alpha) r``. Its norm is
    equal at the common joint without requiring cross-sensor orientation.
    """

    execution_guard.validate_factor_fields(("acc", "gyro", "clock_uncertainty"))
    online_owner = dict(settings.get("online_branch_posterior_owner", {}))
    online_owner_enabled = bool(online_owner.get("enabled", False))
    if online_owner_enabled and (
        online_owner.get("schema")
        != "biospur-c2-online-branch-posterior-owner-v1"
        or online_owner.get("full_coherent_nuisance_refits_online") is not False
        or float(online_owner.get("incomplete_nuisance_center_sigma_m", 0.0)) <= 0.0
    ):
        raise ValueError("online center branch-posterior owner settings are invalid")
    dt = float(settings["sample_period_s"])
    window = int(settings["savgol_window_samples"])
    polynomial = int(settings["savgol_polynomial"])
    endpoint = window // 2
    center_block_rows = int(settings["selection_block_rows"])
    parameter_dimension = int(settings["local_parameter_dimension"])
    minimum_blocks = int(settings["minimum_complete_blocks_for_update"])
    if (
        parameter_dimension != 6
        or minimum_blocks
        != parameter_dimension * int(settings["minimum_complete_blocks_per_parameter"])
    ):
        raise ValueError("center complete-block support is not dimension-owned")
    parent_acc_covariance = _validated_center_covariance(
        "parent accelerometer", parent_acc_covariance,
    )
    child_acc_covariance = _validated_center_covariance(
        "child accelerometer", child_acc_covariance,
    )
    parent_gyro_observation_covariance = _validated_center_covariance(
        "parent gyro observation", parent_gyro_observation_covariance,
    )
    child_gyro_observation_covariance = _validated_center_covariance(
        "child gyro observation", child_gyro_observation_covariance,
    )
    parent_gyro_bias_covariance = _validated_center_covariance(
        "parent gyro bias", parent_gyro_bias_covariance,
    )
    child_gyro_bias_covariance = _validated_center_covariance(
        "child gyro bias", child_gyro_bias_covariance,
    )
    accelerometer_bias_sigma = float(
        settings["accelerometer_unresolved_bias_sigma_mps2"]
    )
    physical_time_policy = settings.get("observed_physical_time_policy")
    if (
        not isinstance(physical_time_policy, Mapping)
        or physical_time_policy.get("schema")
        != "biospur-c2-center-observed-physical-time-policy-v1"
        or physical_time_policy.get("units") != "s"
        or physical_time_policy.get("owner")
        != "AlignedPair_FROM_CURRENT_SEALED_ORIENTED_ACTION"
        or physical_time_policy.get("unknown_boot_or_reset_policy")
        != "LOCAL_CENTER_FACTOR_NO_UPDATE;NO_ELAPSED_CONTINUITY_INVENTED;RUNTIME_UNKNOWN_INTERVAL_FLOOR_MUST_BE_PROVEN_SEPARATELY"
    ):
        raise RuntimeError("center observed physical-time ownership policy is not registered")
    physical_time_diagnostic_policy = settings.get(
        "physical_time_full_owner_mutation", {}
    ).get("preeligibility_provenance_diagnostic")
    if (
        not isinstance(physical_time_diagnostic_policy, Mapping)
        or physical_time_diagnostic_policy.get("schema")
        != "biospur-c2-center-physical-time-drift-input-provenance-v1"
        or not bool(physical_time_diagnostic_policy.get(
            "required_even_when_nominal_factor_is_local_no_update"
        ))
        or bool(physical_time_diagnostic_policy.get(
            "successful_nuisance_refit_required"
        ))
        or bool(physical_time_diagnostic_policy.get(
            "candidate_solution_covariance_information_or_posterior"
        ))
        or bool(physical_time_diagnostic_policy.get("may_promote_owner_update"))
    ):
        raise RuntimeError(
            "center preeligibility physical-time provenance diagnostic is not registered"
        )
    accelerometer_bias_drift_rate = float(
        settings["accelerometer_bias_drift_rate_sigma_mps3"]
    )
    accelerometer_bias_drift_horizon = float(
        settings["accelerometer_bias_drift_horizon_s"]
    )
    accelerometer_scale_cross_sigma = float(
        settings["accelerometer_scale_cross_axis_fraction_sigma"]
    )
    scale_cross_sigma = float(settings["gyro_scale_cross_axis_fraction_sigma"])
    shared_scale_cross_sigma = float(
        settings["accelerometer_gyro_shared_scale_cross_axis_fraction_sigma"]
    )
    bias_drift_horizon = float(settings["gyro_bias_drift_correlation_time_s"])
    reweight_passes = int(settings["feasible_gls_gyro_reweight_passes"])
    if (
        accelerometer_bias_sigma <= 0.0
        or accelerometer_bias_drift_rate <= 0.0
        or accelerometer_bias_drift_horizon <= 0.0
        or accelerometer_scale_cross_sigma < 0.0
        or scale_cross_sigma < 0.0
        or shared_scale_cross_sigma < 0.0
        or bias_drift_horizon <= 0.0
        or reweight_passes <= 0
    ):
        raise ValueError("center accelerometer/gyro calibration nuisance settings are invalid")
    accelerometer_bias_drift_variance = (
        accelerometer_bias_sigma**2
        + (
            accelerometer_bias_drift_rate
            * accelerometer_bias_drift_horizon
        ) ** 2
    )
    parent_acc_bias_drift_covariance = (
        np.eye(3) * accelerometer_bias_drift_variance
    )
    child_acc_bias_drift_covariance = (
        np.eye(3) * accelerometer_bias_drift_variance
    )
    derivative_coefficients = savgol_coeffs(
        window, polynomial, deriv=1, delta=dt, use="dot",
    )
    smoothing_coefficients = savgol_coeffs(
        window, polynomial, deriv=0, use="dot",
    )
    alpha_observation_noise_gain = float(derivative_coefficients @ derivative_coefficients)
    smoothing_observation_noise_gain = float(
        smoothing_coefficients @ smoothing_coefficients
    )
    retained_rows_per_complete_block = center_block_rows - 2 * endpoint
    if retained_rows_per_complete_block <= parameter_dimension:
        raise ValueError("center block is too short after its independent filter guard")

    def convolution_matrix(coefficients: np.ndarray) -> np.ndarray:
        matrix = np.zeros(
            (retained_rows_per_complete_block, center_block_rows), dtype=float,
        )
        for row in range(retained_rows_per_complete_block):
            matrix[row, row:row + window] = coefficients
        return matrix

    smoothing_matrix = convolution_matrix(smoothing_coefficients)
    derivative_matrix = convolution_matrix(derivative_coefficients)
    smoothing_spectral_variance_gain = float(
        np.max(np.linalg.eigvalsh(smoothing_matrix @ smoothing_matrix.T))
    )
    derivative_spectral_variance_gain = float(
        np.max(np.linalg.eigvalsh(derivative_matrix @ derivative_matrix.T))
    )
    serial_correlation_variance_envelope_multiplier = max(
        1.0,
        smoothing_spectral_variance_gain,
        derivative_spectral_variance_gain
        / max(alpha_observation_noise_gain, np.finfo(float).eps),
    )
    smoothing_envelope_minimum_eigenvalue = float(np.min(np.linalg.eigvalsh(
        serial_correlation_variance_envelope_multiplier
        * np.eye(retained_rows_per_complete_block)
        - smoothing_matrix @ smoothing_matrix.T
    )))
    derivative_envelope_minimum_eigenvalue = float(np.min(np.linalg.eigvalsh(
        serial_correlation_variance_envelope_multiplier
        * alpha_observation_noise_gain
        * np.eye(retained_rows_per_complete_block)
        - derivative_matrix @ derivative_matrix.T
    )))
    kernel_numerical_tolerance = 100.0 * np.finfo(float).eps * max(
        1.0, derivative_spectral_variance_gain,
    )
    if (
        smoothing_envelope_minimum_eigenvalue < -kernel_numerical_tolerance
        or derivative_envelope_minimum_eigenvalue < -kernel_numerical_tolerance
    ):
        raise RuntimeError("Savitzky-Golay serial-correlation envelope is not PSD-dominating")
    blocks: list[dict[str, Any]] = []
    timing_reports = []
    physical_span_reports: list[dict[str, Any]] = []
    physical_time_epoch_group_by_key: dict[tuple[int, int], int] = {}
    unknown_boot_transition_pairs: list[dict[str, Any]] = []
    previous_pair_time_boundary: dict[str, Any] | None = None
    for pair_index, pair in enumerate(pairs):
        pair_row_count = len(pair.parent_acc)
        for field_name, value in (
            ("child_acc", pair.child_acc),
            ("parent_gyro", pair.parent_gyro),
            ("child_gyro", pair.child_gyro),
            ("parent_observed_time_s", pair.parent_observed_time_s),
            ("child_observed_time_s", pair.child_observed_time_s),
            ("parent_boot_epoch", pair.parent_boot_epoch),
            ("child_boot_epoch", pair.child_boot_epoch),
        ):
            if len(value) != pair_row_count:
                raise ValueError(f"{edge}:{pair.action}: {field_name} length mismatch")
        parent_pair_boots = np.unique(np.asarray(pair.parent_boot_epoch, dtype=np.int64))
        child_pair_boots = np.unique(np.asarray(pair.child_boot_epoch, dtype=np.int64))
        if len(parent_pair_boots) != 1 or len(child_pair_boots) != 1:
            unknown_boot_transition_pairs.append({
                "pair_index": int(pair_index),
                "action": pair.action,
                "parent_boot_epochs": parent_pair_boots.tolist(),
                "child_boot_epochs": child_pair_boots.tolist(),
                "elapsed_interval_invented": False,
                "owner_disposition": "LOCAL_NO_UPDATE_UNKNOWN_BOOT_INTERVAL",
            })
        else:
            pair_epoch_key = (int(parent_pair_boots[0]), int(child_pair_boots[0]))
            pair_start = (
                float(np.asarray(pair.parent_observed_time_s, dtype=float)[0]),
                float(np.asarray(pair.child_observed_time_s, dtype=float)[0]),
            )
            pair_stop = (
                float(np.asarray(pair.parent_observed_time_s, dtype=float)[-1]),
                float(np.asarray(pair.child_observed_time_s, dtype=float)[-1]),
            )
            if previous_pair_time_boundary is not None:
                previous_key = tuple(previous_pair_time_boundary["epoch_key"])
                previous_stop = tuple(previous_pair_time_boundary["stop_time_s"])
                boundary_event = _center_cross_pair_interval_event(
                    pair_index=pair_index,
                    previous_action=str(previous_pair_time_boundary["action"]),
                    action=pair.action,
                    previous_epoch_key=previous_key,
                    current_epoch_key=pair_epoch_key,
                    previous_stop_time_s=previous_stop,
                    current_start_time_s=pair_start,
                )
                if boundary_event is not None:
                    unknown_boot_transition_pairs.append(boundary_event)
            previous_pair_time_boundary = {
                "action": pair.action,
                "epoch_key": pair_epoch_key,
                "stop_time_s": pair_stop,
            }
        for span_index, span in enumerate(pair.contiguous_spans):
            parent_span_time = np.asarray(
                pair.parent_observed_time_s[span], dtype=float,
            )
            child_span_time = np.asarray(
                pair.child_observed_time_s[span], dtype=float,
            )
            parent_span_boot = np.asarray(pair.parent_boot_epoch[span], dtype=np.int64)
            child_span_boot = np.asarray(pair.child_boot_epoch[span], dtype=np.int64)
            if (
                not np.all(np.isfinite(parent_span_time))
                or not np.all(np.isfinite(child_span_time))
                or np.any(np.diff(parent_span_time) <= 0.0)
                or np.any(np.diff(child_span_time) <= 0.0)
                or len(np.unique(parent_span_boot)) != 1
                or len(np.unique(child_span_boot)) != 1
            ):
                raise ValueError(
                    f"{edge}:{pair.action}: aligned physical-time span is not monotonic and boot-safe"
                )
            physical_span_group = len(physical_span_reports)
            epoch_key = (int(parent_span_boot[0]), int(child_span_boot[0]))
            if epoch_key not in physical_time_epoch_group_by_key:
                physical_time_epoch_group_by_key[epoch_key] = len(
                    physical_time_epoch_group_by_key
                )
            physical_time_epoch_group = physical_time_epoch_group_by_key[epoch_key]
            physical_span_reports.append({
                "pair_index": int(pair_index),
                "action": pair.action,
                "span_index": int(span_index),
                "parent_boot_epoch": int(parent_span_boot[0]),
                "child_boot_epoch": int(child_span_boot[0]),
                "parent_observed_time_s_sha256": _array_sha256(parent_span_time),
                "child_observed_time_s_sha256": _array_sha256(child_span_time),
                "parent_elapsed_s": float(parent_span_time[-1] - parent_span_time[0]),
                "child_elapsed_s": float(child_span_time[-1] - child_span_time[0]),
                "physical_time_epoch_group": int(physical_time_epoch_group),
                "cross_gap_or_boot_elapsed_invented": False,
            })
            span_rows = span.stop - span.start
            for block_index, local_start in enumerate(
                range(0, span_rows, center_block_rows)
            ):
                local_stop = local_start + center_block_rows
                if local_stop > span_rows:
                    continue
                raw = slice(span.start + local_start, span.start + local_stop)
                parent_gyro_block = pair.parent_gyro[raw]
                child_gyro_block = pair.child_gyro[raw]
                (
                    parent_terms_full,
                    parent_alpha_full,
                    parent_gyro_smoothed,
                ) = _center_terms(
                    parent_gyro_block, dt=dt, window=window,
                    polynomial=polynomial,
                )
                (
                    child_terms_full,
                    child_alpha_full,
                    child_gyro_smoothed,
                ) = _center_terms(
                    child_gyro_block, dt=dt, window=window,
                    polynomial=polynomial,
                )
                parent_acc_smoothed = savgol_filter(
                    pair.parent_acc[raw], window_length=window,
                    polyorder=polynomial, deriv=0, axis=0, mode="interp",
                )
                child_acc_smoothed = savgol_filter(
                    pair.child_acc[raw], window_length=window,
                    polyorder=polynomial, deriv=0, axis=0, mode="interp",
                )
                selected = slice(endpoint, center_block_rows - endpoint)
                parent_acc_block = parent_acc_smoothed[selected]
                child_acc_block = child_acc_smoothed[selected]
                parent_time_block = np.asarray(
                    pair.parent_observed_time_s[raw], dtype=float,
                )[selected]
                child_time_block = np.asarray(
                    pair.child_observed_time_s[raw], dtype=float,
                )[selected]
                timing_derivative = np.column_stack((
                    np.gradient(np.linalg.norm(parent_acc_block, axis=1), dt),
                    np.gradient(np.linalg.norm(child_acc_block, axis=1), dt),
                ))
                lag_uncertainty_s = float(
                    pair.alignment.report["lag_uncertainty_s"]
                )
                blocks.append({
                    "pair_index": int(pair_index),
                    "parent_acc": parent_acc_block,
                    "child_acc": child_acc_block,
                    "parent_terms": parent_terms_full[selected],
                    "child_terms": child_terms_full[selected],
                    "parent_gyro": parent_gyro_smoothed[selected],
                    "child_gyro": child_gyro_smoothed[selected],
                    "parent_alpha": parent_alpha_full[selected],
                    "child_alpha": child_alpha_full[selected],
                    "timing_variance_mps4": (
                        lag_uncertainty_s**2 * np.sum(timing_derivative**2, axis=1)
                    ),
                    "clock_lag_variance_s2": np.full(
                        retained_rows_per_complete_block,
                        lag_uncertainty_s**2,
                        dtype=float,
                    ),
                    "clock_group": np.full(
                        retained_rows_per_complete_block, pair_index, dtype=np.int64,
                    ),
                    "block_local_time_s": (
                        np.arange(retained_rows_per_complete_block, dtype=float)
                        - 0.5 * (retained_rows_per_complete_block - 1)
                    ) * dt,
                    "parent_observed_time_s": parent_time_block,
                    "child_observed_time_s": child_time_block,
                    "physical_span_group": np.full(
                        retained_rows_per_complete_block,
                        physical_span_group,
                        dtype=np.int64,
                    ),
                    "physical_time_epoch_group": np.full(
                        retained_rows_per_complete_block,
                        physical_time_epoch_group,
                        dtype=np.int64,
                    ),
                    "timing_sigma_mps2": float(
                        lag_uncertainty_s
                        * np.sqrt(np.mean(timing_derivative**2))
                    ),
                    "action": pair.action,
                    "span_index": span_index,
                    "block_index": block_index,
                    "source_rows_within_contiguous_span_half_open": [
                        local_start, local_stop,
                    ],
                    "retained_rows_within_raw_block_half_open": [
                        endpoint, center_block_rows - endpoint,
                    ],
                })
        timing_reports.append(pair.alignment.report)
    if not blocks:
        raise ValueError(f"{edge}: no eligible center-estimation rows")
    (
        parent_acc,
        child_acc,
        parent_terms,
        child_terms,
        parent_gyro,
        child_gyro,
        parent_alpha,
        child_alpha,
        timing_variance_mps4,
        clock_lag_variance_s2,
        clock_group,
        block_local_time_s,
        parent_observed_time_s,
        child_observed_time_s,
        physical_span_group,
        physical_time_epoch_group,
        cluster_id,
    ), cap_audit = _blockwise_uniform_cap(
        [
            {
                **block,
                "cluster_id": np.full(
                    len(block["parent_acc"]), index, dtype=np.int64,
                ),
            }
            for index, block in enumerate(blocks)
        ],
        (
            "parent_acc", "child_acc", "parent_terms", "child_terms",
            "parent_gyro", "child_gyro", "parent_alpha", "child_alpha",
            "timing_variance_mps4", "clock_lag_variance_s2", "clock_group",
            "block_local_time_s", "parent_observed_time_s",
            "child_observed_time_s", "physical_span_group",
            "physical_time_epoch_group", "cluster_id",
        ),
        int(settings["maximum_rows"]),
    )
    sensor_sigma = float(np.sqrt(
        np.trace(np.asarray(parent_acc_covariance) + np.asarray(child_acc_covariance)) / 3.0
    ))
    timing_sigma = float(np.sqrt(np.mean([
        float(block["timing_sigma_mps2"]) ** 2 for block in blocks
    ])))
    base_sensor_sigma = (
        sensor_sigma
        * float(settings["noise_sigma_multiplier"])
        * np.sqrt(serial_correlation_variance_envelope_multiplier)
    )
    base_sigma = np.sqrt(np.maximum(
        base_sensor_sigma**2 + np.asarray(timing_variance_mps4, dtype=float),
        np.finfo(float).eps,
    ))
    robust_sigma = float(np.sqrt(np.mean(base_sigma**2)))

    bound = float(settings["coordinate_bound_m"])
    random_multistarts = int(settings["random_multistarts"])
    multistart_rng = np.random.default_rng(int(settings["multistart_seed"]))
    initial_limit = bound * float(settings["multistart_initial_fraction_of_coordinate_bound"])
    starts = np.vstack((
        np.zeros((1, 6), dtype=float),
        multistart_rng.uniform(-initial_limit, initial_limit, size=(random_multistarts, 6)),
    ))
    robust_f_scale = float(settings["robust_f_scale_standardized"])
    if str(settings["robust_loss"]) != "soft_l1" or robust_f_scale <= 0.0:
        raise ValueError("center robust loss must be registered positive soft_l1")
    budget_coherent_settings = settings.get("coherent_nuisance_refit_audit", {})
    budget_coherent_component_count = len(tuple(
        budget_coherent_settings.get("components", ())
    ))
    budget_direction_count = int(budget_coherent_settings.get("direction_count", 0))
    if budget_coherent_component_count != 7 or budget_direction_count != 18:
        raise ValueError("center aggregate budget requires the registered 7x18 nuisance design")
    minimum_nominal_and_prefix_solver_calls = int(
        2 * len(starts) * (1 + reweight_passes)
    )
    expected_full_qualification_solver_calls = int(
        minimum_nominal_and_prefix_solver_calls
        + budget_coherent_component_count * budget_direction_count * 2
        * (1 + reweight_passes)
    )
    factor_maximum_solver_calls = int(settings.get(
        "factor_maximum_solver_calls", expected_full_qualification_solver_calls + 5,
    ))
    maximum_function_evaluations = int(settings["maximum_function_evaluations"])
    factor_maximum_total_function_evaluations = int(settings.get(
        "factor_maximum_total_function_evaluations",
        factor_maximum_solver_calls * maximum_function_evaluations,
    ))
    if (
        factor_maximum_solver_calls < minimum_nominal_and_prefix_solver_calls
        or factor_maximum_total_function_evaluations
        < factor_maximum_solver_calls
    ):
        raise ValueError("center factor aggregate solver budget is structurally inconsistent")
    factor_solver_calls = 0
    factor_total_function_evaluations = 0
    factor_budget_context: dict[str, Any] = {"phase": "NOT_STARTED"}
    completed_solver_contexts: list[dict[str, Any]] = []

    def budget_audit() -> dict[str, Any]:
        return {
            "schema": "biospur-c2-center-factor-aggregate-solver-budget-v1",
            "minimum_nominal_and_prefix_solver_calls": (
                minimum_nominal_and_prefix_solver_calls
            ),
            "expected_full_qualification_solver_calls": (
                expected_full_qualification_solver_calls
            ),
            "maximum_solver_calls": factor_maximum_solver_calls,
            "observed_solver_calls": factor_solver_calls,
            "maximum_function_evaluations_per_call": maximum_function_evaluations,
            "maximum_total_function_evaluations": (
                factor_maximum_total_function_evaluations
            ),
            "observed_total_function_evaluations": (
                factor_total_function_evaluations
            ),
            "active_context": dict(factor_budget_context),
            "completed_solver_context_count": len(completed_solver_contexts),
            "completed_solver_contexts": list(completed_solver_contexts),
            "full_coherent_sensitivity_completed": False,
            "coherent_sensitivity_pass_claimed": False,
            "partial_candidates_are_diagnostic_only": True,
            "budget_exhaustion_disposition": "LOCAL_NO_UPDATE_BUDGET_EXHAUSTED",
            "keyboard_interrupt_used_as_budget": False,
        }

    def bounded_least_squares(*args: Any, **kwargs: Any) -> Any:
        nonlocal factor_solver_calls, factor_total_function_evaluations
        if factor_solver_calls >= factor_maximum_solver_calls:
            raise CenterFactorAggregateBudgetExceeded(
                f"{edge}: center factor solver-call budget exhausted "
                f"({factor_solver_calls}/{factor_maximum_solver_calls}; "
                f"total_nfev={factor_total_function_evaluations}/"
                f"{factor_maximum_total_function_evaluations})",
                audit=budget_audit(),
            )
        factor_solver_calls += 1
        result = least_squares(*args, **kwargs)
        factor_total_function_evaluations += int(result.nfev)
        if (
            factor_total_function_evaluations
            > factor_maximum_total_function_evaluations
        ):
            raise CenterFactorAggregateBudgetExceeded(
                f"{edge}: center factor total-nfev budget exhausted "
                f"({factor_solver_calls}/{factor_maximum_solver_calls}; "
                f"total_nfev={factor_total_function_evaluations}/"
                f"{factor_maximum_total_function_evaluations})",
                audit=budget_audit(),
            )
        return result

    def make_data(arrays: Sequence[np.ndarray]) -> dict[str, np.ndarray]:
        names = (
            "parent_acc", "child_acc", "parent_terms", "child_terms",
            "parent_gyro", "child_gyro", "parent_alpha", "child_alpha",
            "timing_variance_mps4", "clock_lag_variance_s2", "clock_group",
            "block_local_time_s", "parent_observed_time_s",
            "child_observed_time_s", "physical_span_group",
            "physical_time_epoch_group", "cluster_id",
        )
        data = {name: np.asarray(value) for name, value in zip(names, arrays, strict=True)}
        data["base_sigma"] = np.sqrt(np.maximum(
            base_sensor_sigma**2 + data["timing_variance_mps4"],
            np.finfo(float).eps,
        ))
        return data

    full_data = make_data((
        parent_acc, child_acc, parent_terms, child_terms,
        parent_gyro, child_gyro, parent_alpha, child_alpha,
        timing_variance_mps4, clock_lag_variance_s2, clock_group,
        block_local_time_s, parent_observed_time_s, child_observed_time_s,
        physical_span_group, physical_time_epoch_group, cluster_id,
    ))

    def make_raw_residual(data: Mapping[str, np.ndarray]) -> Any:
        def evaluate(parameters: np.ndarray) -> np.ndarray:
            rp_local = parameters[:3]
            rc_local = parameters[3:]
            corrected_parent_local = data["parent_acc"] - np.einsum(
                "nij,j->ni", data["parent_terms"], rp_local,
            )
            corrected_child_local = data["child_acc"] - np.einsum(
                "nij,j->ni", data["child_terms"], rc_local,
            )
            return (
                np.linalg.norm(corrected_parent_local, axis=1)
                - np.linalg.norm(corrected_child_local, axis=1)
            )
        return evaluate

    def stochastic_sigma(
        parameters: np.ndarray, data: Mapping[str, np.ndarray],
    ) -> tuple[np.ndarray, dict[str, Any], dict[str, np.ndarray]]:
        return _center_stochastic_residual_sigma(
            parameters,
            parent_acc=data["parent_acc"],
            child_acc=data["child_acc"],
            parent_terms=data["parent_terms"],
            child_terms=data["child_terms"],
            parent_gyro=data["parent_gyro"],
            child_gyro=data["child_gyro"],
            parent_alpha=data["parent_alpha"],
            child_alpha=data["child_alpha"],
            clock_lag_variance_s2=data["clock_lag_variance_s2"],
            cluster_id=data["cluster_id"],
            parent_acc_covariance=parent_acc_covariance,
            child_acc_covariance=child_acc_covariance,
            parent_acc_bias_drift_covariance=(
                parent_acc_bias_drift_covariance
            ),
            child_acc_bias_drift_covariance=(
                child_acc_bias_drift_covariance
            ),
            parent_gyro_observation_covariance=parent_gyro_observation_covariance,
            child_gyro_observation_covariance=child_gyro_observation_covariance,
            parent_gyro_bias_covariance=parent_gyro_bias_covariance,
            child_gyro_bias_covariance=child_gyro_bias_covariance,
            alpha_observation_noise_gain_s2_inv=alpha_observation_noise_gain,
            accelerometer_scale_cross_axis_fraction_sigma=(
                accelerometer_scale_cross_sigma
            ),
            gyro_scale_cross_axis_fraction_sigma=scale_cross_sigma,
            accelerometer_gyro_shared_scale_cross_axis_fraction_sigma=(
                shared_scale_cross_sigma
            ),
            gyro_bias_drift_correlation_time_s=bias_drift_horizon,
            noise_sigma_multiplier=float(settings["noise_sigma_multiplier"]),
            serial_correlation_variance_envelope_multiplier=(
                serial_correlation_variance_envelope_multiplier
            ),
            sample_period_s=dt,
        )

    def run_one_trial(
        data: Mapping[str, np.ndarray], *, start: np.ndarray, start_index: int,
        budget_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        factor_budget_context.clear()
        factor_budget_context.update({
            "phase": "UNSPECIFIED" if budget_context is None else str(
                budget_context.get("phase", "UNSPECIFIED")
            ),
            **({} if budget_context is None else dict(budget_context)),
            "start_index": int(start_index),
        })
        raw = make_raw_residual(data)
        trial = bounded_least_squares(
            lambda value: raw(value) / data["base_sigma"],
            start,
            bounds=(-bound, bound),
            loss=str(settings["robust_loss"]),
            f_scale=robust_f_scale,
            x_scale="jac",
            ftol=float(settings["solver_tolerance"]),
            xtol=float(settings["solver_tolerance"]),
            gtol=float(settings["solver_tolerance"]),
            max_nfev=maximum_function_evaluations,
        )
        sigma_audits = []
        weight_sigma = np.asarray(data["base_sigma"], dtype=float)
        for _ in range(reweight_passes):
            weight_sigma, sigma_audit, _ = stochastic_sigma(trial.x, data)
            sigma_audits.append(sigma_audit)
            trial = bounded_least_squares(
                lambda value, sigma=weight_sigma: raw(value) / sigma,
                trial.x,
                bounds=(-bound, bound),
                loss=str(settings["robust_loss"]),
                f_scale=robust_f_scale,
                x_scale="jac",
                ftol=float(settings["solver_tolerance"]),
                xtol=float(settings["solver_tolerance"]),
                gtol=float(settings["solver_tolerance"]),
                max_nfev=maximum_function_evaluations,
            )
        final_sigma, final_sigma_audit, final_input_gradients = stochastic_sigma(
            trial.x, data,
        )
        sigma_relative_change = float(
            np.sqrt(np.mean((final_sigma - weight_sigma) ** 2))
            / max(float(np.sqrt(np.mean(weight_sigma**2))), np.finfo(float).eps)
        )
        trial_at_guard = bool(np.any(
            np.abs(trial.x) >= bound * float(settings["boundary_fraction"])
        ))
        output = {
            "start_index": int(start_index),
            "start": np.asarray(start, dtype=float).copy(),
            "result": trial,
            "normalized_cost": float(trial.cost) / max(1, len(trial.fun)),
            "boundary_guard_active": trial_at_guard,
            "interior": bool(trial.success and not trial_at_guard),
            "weight_sigma_mps2": weight_sigma,
            "final_sigma_mps2": final_sigma,
            "final_sigma_audit": final_sigma_audit,
            "final_input_gradients": final_input_gradients,
            "sigma_relative_fixed_point_change": sigma_relative_change,
            "feasible_gls_sigma_audits": sigma_audits,
        }
        completed_solver_contexts.append({
            **dict(factor_budget_context),
            "terminal_nfev": int(trial.nfev),
            "terminal_success": bool(trial.success),
        })
        return output

    def run_trials(data: Mapping[str, np.ndarray]) -> list[dict[str, Any]]:
        return [
            run_one_trial(
                data, start=start, start_index=start_index,
                budget_context={"phase": "NOMINAL_OR_PREFIX_MULTISTART"},
            )
            for start_index, start in enumerate(starts)
        ]

    def assess_trials(trial_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        successful = [row for row in trial_rows if bool(row["result"].success)]
        interior = [row for row in successful if bool(row["interior"])]
        boundary = [row for row in successful if bool(row["boundary_guard_active"])]
        best_any = min(
            successful if successful else trial_rows,
            key=lambda row: (float(row["normalized_cost"]), int(row["start_index"])),
        )
        best_interior = (
            min(
                interior,
                key=lambda row: (float(row["normalized_cost"]), int(row["start_index"])),
            )
            if interior else None
        )
        best_boundary = (
            min(
                boundary,
                key=lambda row: (float(row["normalized_cost"]), int(row["start_index"])),
            )
            if boundary else None
        )
        if best_interior is None:
            return {
                "selected": best_any,
                "interior": interior,
                "primary_cluster": [],
                "boundary_competitive": bool(boundary),
                "remote_basin_competitive": False,
                "consistent_interior_fraction": 0.0,
                "basin_identifiable": False,
                "best_boundary": best_boundary,
            }
        tolerance = (
            float(best_interior["normalized_cost"])
            * float(settings["competitive_normalized_cost_relative_tolerance"])
            + float(settings["competitive_normalized_cost_absolute_tolerance"])
        )
        competitive_limit = float(best_interior["normalized_cost"]) + tolerance
        radius = float(settings["interior_basin_radius_m"])
        primary_cluster = [
            row for row in interior
            if float(np.linalg.norm(row["result"].x - best_interior["result"].x)) <= radius
        ]
        competitive_remote = [
            row for row in interior
            if float(row["normalized_cost"]) <= competitive_limit
            and float(np.linalg.norm(row["result"].x - best_interior["result"].x)) > radius
        ]
        boundary_competitive = bool(
            best_boundary is not None
            and float(best_boundary["normalized_cost"]) <= competitive_limit
        )
        consistent_fraction = len(primary_cluster) / max(1, len(interior))
        basin_identifiable = bool(
            len(interior) >= int(settings["minimum_legal_multistarts_for_update"])
            and consistent_fraction
            >= float(settings["minimum_consistent_interior_start_fraction"])
            and not boundary_competitive
            and not competitive_remote
        )
        return {
            "selected": best_interior,
            "interior": interior,
            "primary_cluster": primary_cluster,
            "boundary_competitive": boundary_competitive,
            "remote_basin_competitive": bool(competitive_remote),
            "consistent_interior_fraction": float(consistent_fraction),
            "basin_identifiable": basin_identifiable,
            "best_boundary": best_boundary,
        }

    raw_residual_function = make_raw_residual(full_data)
    trials = run_trials(full_data)
    multistart_assessment = assess_trials(trials)
    selected_trial = multistart_assessment["selected"]
    result = selected_trial["result"]
    selected_sigma = np.asarray(selected_trial["final_sigma_mps2"], dtype=float)
    residual = lambda value: raw_residual_function(value) / selected_sigma
    rp = result.x[:3]
    rc = result.x[3:]
    corrected_parent = parent_acc - np.einsum("nij,j->ni", parent_terms, rp)
    corrected_child = child_acc - np.einsum("nij,j->ni", child_terms, rc)
    parent_unit = corrected_parent / np.maximum(
        np.linalg.norm(corrected_parent, axis=1, keepdims=True), np.finfo(float).eps,
    )
    child_unit = corrected_child / np.maximum(
        np.linalg.norm(corrected_child, axis=1, keepdims=True), np.finfo(float).eps,
    )
    original_jacobian = np.column_stack((
        -np.einsum("ni,nij->nj", parent_unit, parent_terms) / selected_sigma[:, None],
        np.einsum("ni,nij->nj", child_unit, child_terms) / selected_sigma[:, None],
    ))
    singular = np.linalg.svd(original_jacobian, compute_uv=False)
    relative = singular / max(float(singular[0]), np.finfo(float).eps)
    rank = int(np.count_nonzero(relative >= float(settings["relative_rank_tolerance"])))
    standardized = residual(result.x)
    scaled_standardized = standardized / robust_f_scale
    psi = standardized / np.sqrt(1.0 + scaled_standardized**2)
    psi_derivative = 1.0 / np.power(1.0 + scaled_standardized**2, 1.5)
    bread = original_jacobian.T @ (psi_derivative[:, None] * original_jacobian)
    score_rows = psi[:, None] * original_jacobian
    cluster_values = np.unique(full_data["cluster_id"])
    cluster_scores = np.asarray([
        np.sum(score_rows[full_data["cluster_id"] == value], axis=0)
        for value in cluster_values
    ])
    meat = cluster_scores.T @ cluster_scores
    bread_inverse = np.linalg.pinv(bread, rcond=float(settings["relative_rank_tolerance"]))
    input_gradients = selected_trial["final_input_gradients"]
    accelerometer_bias_nuisance_jacobian = np.column_stack((
        input_gradients["parent_acc_bias"],
        input_gradients["child_acc_bias"],
    )) / selected_sigma[:, None]
    gyro_bias_nuisance_jacobian = np.column_stack((
        input_gradients["parent_omega"],
        input_gradients["child_omega"],
    )) / selected_sigma[:, None]
    gyro_bias_drift_nuisance_jacobian = np.column_stack((
        input_gradients["parent_alpha"],
        input_gradients["child_alpha"],
    )) / selected_sigma[:, None]
    accelerometer_scale_nuisance_jacobian = np.column_stack((
        input_gradients["parent_acc_scale"],
        input_gradients["child_acc_scale"],
    )) / selected_sigma[:, None]
    gyro_scale_nuisance_jacobian = np.column_stack((
        input_gradients["parent_gyro_scale"],
        input_gradients["child_gyro_scale"],
    )) / selected_sigma[:, None]
    shared_scale_nuisance_jacobian = np.column_stack((
        input_gradients["parent_shared_scale"],
        input_gradients["child_shared_scale"],
    )) / selected_sigma[:, None]
    clock_groups = np.unique(full_data["clock_group"])
    signed_clock_offset_gradient = input_gradients["signed_clock_offset"]
    clock_nuisance_jacobian = np.column_stack([
        np.where(
            full_data["clock_group"] == group,
            signed_clock_offset_gradient / selected_sigma,
            0.0,
        )
        for group in clock_groups
    ])
    nuisance_jacobian = np.column_stack((
        accelerometer_bias_nuisance_jacobian,
        gyro_bias_nuisance_jacobian,
        gyro_bias_drift_nuisance_jacobian,
        accelerometer_scale_nuisance_jacobian,
        gyro_scale_nuisance_jacobian,
        shared_scale_nuisance_jacobian,
        clock_nuisance_jacobian,
    ))
    nuisance_covariance = np.zeros(
        (nuisance_jacobian.shape[1], nuisance_jacobian.shape[1]), dtype=float,
    )
    nuisance_covariance[:3, :3] = parent_acc_bias_drift_covariance
    nuisance_covariance[3:6, 3:6] = child_acc_bias_drift_covariance
    nuisance_covariance[6:9, 6:9] = parent_gyro_bias_covariance
    nuisance_covariance[9:12, 9:12] = child_gyro_bias_covariance
    nuisance_covariance[12:15, 12:15] = (
        parent_gyro_bias_covariance / bias_drift_horizon**2
    )
    nuisance_covariance[15:18, 15:18] = (
        child_gyro_bias_covariance / bias_drift_horizon**2
    )
    nuisance_covariance[18:36, 18:36] = (
        np.eye(18) * accelerometer_scale_cross_sigma**2
    )
    nuisance_covariance[36:54, 36:54] = (
        np.eye(18) * scale_cross_sigma**2
    )
    nuisance_covariance[54:72, 54:72] = (
        np.eye(18) * shared_scale_cross_sigma**2
    )
    clock_sigmas = np.asarray([
        float(timing_reports[int(group)]["lag_uncertainty_s"])
        for group in clock_groups
    ])
    nuisance_covariance[72:, 72:] = np.diag(clock_sigmas**2)
    cross_information = original_jacobian.T @ (
        psi_derivative[:, None] * nuisance_jacobian
    )
    nuisance_influence = -bread_inverse @ cross_information
    sensor_calibration_clock_systematic_covariance = (
        nuisance_influence @ nuisance_covariance @ nuisance_influence.T
    )
    accelerometer_bias_drift_systematic_covariance = (
        nuisance_influence[:, :6]
        @ nuisance_covariance[:6, :6]
        @ nuisance_influence[:, :6].T
    )
    gyro_bias_systematic_covariance = (
        nuisance_influence[:, 6:12]
        @ nuisance_covariance[6:12, 6:12]
        @ nuisance_influence[:, 6:12].T
    )
    gyro_bias_drift_systematic_covariance = (
        nuisance_influence[:, 12:18]
        @ nuisance_covariance[12:18, 12:18]
        @ nuisance_influence[:, 12:18].T
    )
    accelerometer_scale_cross_systematic_covariance = (
        nuisance_influence[:, 18:36]
        @ nuisance_covariance[18:36, 18:36]
        @ nuisance_influence[:, 18:36].T
    )
    gyro_scale_cross_systematic_covariance = (
        nuisance_influence[:, 36:54]
        @ nuisance_covariance[36:54, 36:54]
        @ nuisance_influence[:, 36:54].T
    )
    accelerometer_gyro_shared_scale_cross_systematic_covariance = (
        nuisance_influence[:, 54:72]
        @ nuisance_covariance[54:72, 54:72]
        @ nuisance_influence[:, 54:72].T
    )
    clock_systematic_covariance = (
        nuisance_influence[:, 72:]
        @ nuisance_covariance[72:, 72:]
        @ nuisance_influence[:, 72:].T
    )
    information_eigenvalues, information_eigenvectors = np.linalg.eigh(0.5 * (bread + bread.T))
    information_maximum = max(float(np.max(information_eigenvalues)), 0.0)
    information_keep = information_eigenvalues >= information_maximum * float(settings["relative_rank_tolerance"])
    information_keep &= information_eigenvalues > 0.0
    gauge_reduced_information = (
        information_eigenvectors[:, information_keep]
        @ np.diag(information_eigenvalues[information_keep])
        @ information_eigenvectors[:, information_keep].T
        if np.any(information_keep) else np.zeros((6, 6), dtype=float)
    )
    finite_sample = len(cluster_values) / max(1, len(cluster_values) - rank)
    sandwich_covariance = bread_inverse @ meat @ bread_inverse * finite_sample
    informed_basis = information_eigenvectors[:, information_keep]
    null_basis = information_eigenvectors[:, ~information_keep]
    nullspace_sigma = float(settings["nullspace_prior_sigma_m"])
    nullspace_covariance = null_basis @ null_basis.T * nullspace_sigma**2
    primary_cluster = list(multistart_assessment["primary_cluster"])
    legal_parameters = np.asarray(
        [row["result"].x for row in primary_cluster], dtype=float,
    )
    multistart_covariance = (
        np.atleast_2d(np.cov(legal_parameters.T, ddof=1))
        if len(legal_parameters) > 1 else np.zeros((6, 6), dtype=float)
    )
    informed_observation_covariance = sandwich_covariance + multistart_covariance
    informed_observation_covariance_projected = (
        informed_basis.T @ informed_observation_covariance @ informed_basis
        if informed_basis.shape[1] else np.zeros((0, 0), dtype=float)
    )
    maximum_informed_observation_sigma = (
        float(np.sqrt(max(
            float(np.max(np.linalg.eigvalsh(informed_observation_covariance_projected))),
            0.0,
        )))
        if informed_basis.shape[1] else float("inf")
    )
    minimum_informed_information_eigenvalue = (
        float(np.min(information_eigenvalues[information_keep]))
        if np.any(information_keep) else 0.0
    )
    statistical_covariance = informed_observation_covariance + nullspace_covariance
    model_floor = float(settings["human_worn_center_floor_m"])
    human_worn_systematic_covariance = np.eye(6) * model_floor**2
    systematic_covariance = (
        sensor_calibration_clock_systematic_covariance
        + human_worn_systematic_covariance
    )
    systematic_covariance = 0.5 * (
        systematic_covariance + systematic_covariance.T
    )
    systematic_component_sum = sum(
        (
            accelerometer_bias_drift_systematic_covariance,
            accelerometer_scale_cross_systematic_covariance,
            gyro_bias_systematic_covariance,
            gyro_bias_drift_systematic_covariance,
            gyro_scale_cross_systematic_covariance,
            accelerometer_gyro_shared_scale_cross_systematic_covariance,
            clock_systematic_covariance,
            human_worn_systematic_covariance,
        ),
        start=np.zeros((6, 6), dtype=float),
    )
    if not np.allclose(
        systematic_covariance,
        systematic_component_sum,
        atol=1e-10,
        rtol=1e-7,
    ):
        raise RuntimeError(
            "center sensor-calibration/clock/human systematic decomposition changed"
        )
    covariance = statistical_covariance + systematic_covariance
    covariance = 0.5 * (covariance + covariance.T)

    def fixed_linearization_covariance_sensitivity(
        *,
        gyro_covariance_multiplier: float,
        accelerometer_calibration_covariance_multiplier: float,
    ) -> dict[str, Any]:
        """Reweight one fixed fitted point; do not confound covariance with refit drift."""

        fixed_sigma, fixed_sigma_audit, _ = _center_stochastic_residual_sigma(
            result.x,
            parent_acc=full_data["parent_acc"],
            child_acc=full_data["child_acc"],
            parent_terms=full_data["parent_terms"],
            child_terms=full_data["child_terms"],
            parent_gyro=full_data["parent_gyro"],
            child_gyro=full_data["child_gyro"],
            parent_alpha=full_data["parent_alpha"],
            child_alpha=full_data["child_alpha"],
            clock_lag_variance_s2=full_data["clock_lag_variance_s2"],
            cluster_id=full_data["cluster_id"],
            parent_acc_covariance=parent_acc_covariance,
            child_acc_covariance=child_acc_covariance,
            parent_acc_bias_drift_covariance=(
                parent_acc_bias_drift_covariance
                * accelerometer_calibration_covariance_multiplier
            ),
            child_acc_bias_drift_covariance=(
                child_acc_bias_drift_covariance
                * accelerometer_calibration_covariance_multiplier
            ),
            parent_gyro_observation_covariance=(
                parent_gyro_observation_covariance * gyro_covariance_multiplier
            ),
            child_gyro_observation_covariance=(
                child_gyro_observation_covariance * gyro_covariance_multiplier
            ),
            parent_gyro_bias_covariance=(
                parent_gyro_bias_covariance * gyro_covariance_multiplier
            ),
            child_gyro_bias_covariance=(
                child_gyro_bias_covariance * gyro_covariance_multiplier
            ),
            alpha_observation_noise_gain_s2_inv=alpha_observation_noise_gain,
            accelerometer_scale_cross_axis_fraction_sigma=(
                accelerometer_scale_cross_sigma
                * np.sqrt(accelerometer_calibration_covariance_multiplier)
            ),
            gyro_scale_cross_axis_fraction_sigma=scale_cross_sigma,
            accelerometer_gyro_shared_scale_cross_axis_fraction_sigma=(
                shared_scale_cross_sigma
                * np.sqrt(accelerometer_calibration_covariance_multiplier)
            ),
            gyro_bias_drift_correlation_time_s=bias_drift_horizon,
            noise_sigma_multiplier=float(settings["noise_sigma_multiplier"]),
            serial_correlation_variance_envelope_multiplier=(
                serial_correlation_variance_envelope_multiplier
            ),
            sample_period_s=dt,
        )
        fixed_jacobian = np.column_stack((
            -np.einsum("ni,nij->nj", parent_unit, parent_terms)
            / fixed_sigma[:, None],
            np.einsum("ni,nij->nj", child_unit, child_terms)
            / fixed_sigma[:, None],
        ))
        fixed_raw_residual = raw_residual_function(result.x)
        fixed_standardized = fixed_raw_residual / fixed_sigma
        fixed_scaled = fixed_standardized / robust_f_scale
        fixed_psi = fixed_standardized / np.sqrt(1.0 + fixed_scaled**2)
        fixed_psi_derivative = 1.0 / np.power(1.0 + fixed_scaled**2, 1.5)
        fixed_bread = fixed_jacobian.T @ (
            fixed_psi_derivative[:, None] * fixed_jacobian
        )
        fixed_bread_inverse = np.linalg.pinv(
            fixed_bread, rcond=float(settings["relative_rank_tolerance"]),
        )
        fixed_score_rows = fixed_psi[:, None] * fixed_jacobian
        fixed_cluster_scores = np.asarray([
            np.sum(fixed_score_rows[full_data["cluster_id"] == value], axis=0)
            for value in cluster_values
        ])
        fixed_meat = fixed_cluster_scores.T @ fixed_cluster_scores
        fixed_information_values, fixed_information_vectors = np.linalg.eigh(
            0.5 * (fixed_bread + fixed_bread.T)
        )
        fixed_information_maximum = max(
            float(np.max(fixed_information_values)), 0.0,
        )
        fixed_keep = (
            fixed_information_values
            >= fixed_information_maximum * float(settings["relative_rank_tolerance"])
        ) & (fixed_information_values > 0.0)
        fixed_rank = int(np.count_nonzero(fixed_keep))
        fixed_finite_sample = len(cluster_values) / max(
            1, len(cluster_values) - fixed_rank,
        )
        fixed_sandwich = (
            fixed_bread_inverse @ fixed_meat @ fixed_bread_inverse
            * fixed_finite_sample
        )
        fixed_null_basis = fixed_information_vectors[:, ~fixed_keep]
        fixed_null_covariance = (
            fixed_null_basis @ fixed_null_basis.T * nullspace_sigma**2
        )
        fixed_nuisance_covariance = nuisance_covariance.copy()
        fixed_nuisance_covariance[:6, :6] *= (
            accelerometer_calibration_covariance_multiplier
        )
        fixed_nuisance_covariance[18:36, 18:36] *= (
            accelerometer_calibration_covariance_multiplier
        )
        fixed_nuisance_covariance[54:72, 54:72] *= (
            accelerometer_calibration_covariance_multiplier
        )
        fixed_nuisance_covariance[6:9, 6:9] = (
            parent_gyro_bias_covariance * gyro_covariance_multiplier
        )
        fixed_nuisance_covariance[9:12, 9:12] = (
            child_gyro_bias_covariance * gyro_covariance_multiplier
        )
        fixed_nuisance_covariance[12:15, 12:15] = (
            parent_gyro_bias_covariance
            / bias_drift_horizon**2
            * gyro_covariance_multiplier
        )
        fixed_nuisance_covariance[15:18, 15:18] = (
            child_gyro_bias_covariance
            / bias_drift_horizon**2
            * gyro_covariance_multiplier
        )
        fixed_sensor_calibration_clock_systematic = (
            nuisance_influence
            @ fixed_nuisance_covariance
            @ nuisance_influence.T
        )
        fixed_systematic = 0.5 * (
            fixed_sensor_calibration_clock_systematic
            + fixed_sensor_calibration_clock_systematic.T
        ) + human_worn_systematic_covariance
        fixed_model_informed = 0.5 * (
            fixed_bread_inverse + fixed_bread_inverse.T
        )
        fixed_statistical_model = (
            fixed_model_informed + multistart_covariance + fixed_null_covariance
        )
        fixed_total_model = fixed_statistical_model + fixed_systematic
        return {
            "gyro_covariance_multiplier": float(gyro_covariance_multiplier),
            "accelerometer_calibration_covariance_multiplier": float(
                accelerometer_calibration_covariance_multiplier
            ),
            "residual_sigma_rms_mps2": float(np.sqrt(np.mean(fixed_sigma**2))),
            "robust_bread_information_trace_m2_inv": float(np.trace(fixed_bread)),
            "rank": fixed_rank,
            "model_based_informed_covariance_trace_m2": float(
                np.trace(fixed_model_informed)
            ),
            "robust_cluster_sandwich_covariance_trace_m2": float(
                np.trace(fixed_sandwich)
            ),
            "systematic_covariance_trace_m2": float(np.trace(fixed_systematic)),
            "total_model_covariance_trace_m2": float(np.trace(fixed_total_model)),
            "independent_filtered_observation_variance_mean_m2ps4": float(
                fixed_sigma_audit[
                    "independent_filtered_observation_variance_mean_m2ps4"
                ]
            ),
            "shared_calibration_marginal_variance_mean_m2ps4": float(
                fixed_sigma_audit[
                    "shared_calibration_marginal_variance_mean_m2ps4"
                ]
            ),
            "white_observation_noise_sigma_multiplier": float(
                fixed_sigma_audit["white_observation_noise_sigma_multiplier"]
            ),
            "white_observation_serial_correlation_variance_envelope_multiplier": float(
                fixed_sigma_audit[
                    "white_observation_serial_correlation_variance_envelope_multiplier"
                ]
            ),
            "shared_calibration_noise_sigma_multiplier": float(
                fixed_sigma_audit["shared_calibration_noise_sigma_multiplier"]
            ),
            "shared_calibration_serial_correlation_variance_envelope_multiplier": float(
                fixed_sigma_audit[
                    "shared_calibration_serial_correlation_variance_envelope_multiplier"
                ]
            ),
            "shared_calibration_marginal_used_only_for_robust_row_standardization": bool(
                fixed_sigma_audit[
                    "shared_calibration_marginal_used_only_for_robust_row_standardization"
                ]
            ),
            "shared_calibration_covariance_added_to_repeatable_episode_information": bool(
                fixed_sigma_audit[
                    "shared_calibration_covariance_added_to_repeatable_episode_information"
                ]
            ),
            "shared_calibration_white_noise_filter_envelope_applied": bool(
                fixed_sigma_audit[
                    "shared_calibration_white_noise_filter_envelope_applied"
                ]
            ),
            "shared_calibration_white_noise_sigma_multiplier_applied": bool(
                fixed_sigma_audit[
                    "shared_calibration_white_noise_sigma_multiplier_applied"
                ]
            ),
            "same_fitted_parameter_vector_used": True,
            "same_primary_nuisance_influence_and_gradients_used": True,
            "refit_performed": False,
        }

    fixed_linearization_sensitivity = [
        fixed_linearization_covariance_sensitivity(
            gyro_covariance_multiplier=multiplier,
            accelerometer_calibration_covariance_multiplier=1.0,
        )
        for multiplier in (0.0, *[
            float(value)
            for value in settings[
                "gyro_stochastic_covariance_sensitivity_multipliers"
            ]
        ])
    ]
    fixed_linearization_accelerometer_calibration_sensitivity = [
        fixed_linearization_covariance_sensitivity(
            gyro_covariance_multiplier=1.0,
            accelerometer_calibration_covariance_multiplier=multiplier,
        )
        for multiplier in (0.0, *[
            float(value)
            for value in settings[
                "accelerometer_calibration_covariance_sensitivity_multipliers"
            ]
        ])
    ]

    prefix_fraction = float(settings["chronological_prefix_fraction_for_stability"])
    prefix_block_count = int(np.floor(len(blocks) * prefix_fraction))
    heldin_block_count = len(blocks) - prefix_block_count
    minimum_partition_blocks = int(settings["minimum_blocks_per_prefix_or_heldin_partition"])
    prefix_stability: dict[str, Any]
    prefix_trials: list[dict[str, Any]] = []
    if (
        prefix_block_count >= minimum_partition_blocks
        and heldin_block_count >= minimum_partition_blocks
    ):
        cap_names = (
            "parent_acc", "child_acc", "parent_terms", "child_terms",
            "parent_gyro", "child_gyro", "parent_alpha", "child_alpha",
            "timing_variance_mps4", "clock_lag_variance_s2", "clock_group",
            "block_local_time_s", "parent_observed_time_s",
            "child_observed_time_s", "physical_span_group",
            "physical_time_epoch_group", "cluster_id",
        )
        prefix_arrays, prefix_cap_audit = _blockwise_uniform_cap(
            [
                {
                    **block,
                    "cluster_id": np.full(
                        len(block["parent_acc"]), index, dtype=np.int64,
                    ),
                }
                for index, block in enumerate(blocks[:prefix_block_count])
            ],
            cap_names,
            int(settings["maximum_rows"]),
        )
        heldin_arrays, heldin_cap_audit = _blockwise_uniform_cap(
            [
                {
                    **block,
                    "cluster_id": np.full(
                        len(block["parent_acc"]), index, dtype=np.int64,
                    ),
                }
                for index, block in enumerate(blocks[prefix_block_count:])
            ],
            cap_names,
            int(settings["maximum_rows"]),
        )
        prefix_data = make_data(prefix_arrays)
        heldin_data = make_data(heldin_arrays)
        prefix_trials = run_trials(prefix_data)
        prefix_assessment = assess_trials(prefix_trials)
        prefix_selected = prefix_assessment["selected"]
        prefix_to_full_delta = float(np.linalg.norm(
            prefix_selected["result"].x - result.x
        ))
        heldin_raw = make_raw_residual(heldin_data)
        prefix_heldin_raw = heldin_raw(prefix_selected["result"].x)
        prefix_heldin_sigma, _, _ = stochastic_sigma(
            prefix_selected["result"].x, heldin_data,
        )
        full_heldin_raw = heldin_raw(result.x)
        full_heldin_sigma, _, _ = stochastic_sigma(result.x, heldin_data)
        prefix_heldin_rms = float(np.sqrt(np.mean(prefix_heldin_raw**2)))
        full_heldin_rms = float(np.sqrt(np.mean(full_heldin_raw**2)))
        prefix_heldin_standardized_rms = float(np.sqrt(np.mean(
            (prefix_heldin_raw / prefix_heldin_sigma) ** 2
        )))
        full_heldin_standardized_rms = float(np.sqrt(np.mean(
            (full_heldin_raw / full_heldin_sigma) ** 2
        )))
        heldin_standardized_limit = float(
            settings["maximum_prefix_heldin_residual_robust_sigma"]
        )
        prefix_reweight_change = float(
            prefix_selected["sigma_relative_fixed_point_change"]
        )
        prefix_stability = {
            "available": True,
            "chronological_prefix_fraction": prefix_fraction,
            "prefix_block_count": prefix_block_count,
            "heldin_block_count": heldin_block_count,
            "prefix_cap_audit": prefix_cap_audit,
            "heldin_cap_audit": heldin_cap_audit,
            "prefix_basin_identifiable": bool(prefix_assessment["basin_identifiable"]),
            "prefix_boundary_competitive": bool(prefix_assessment["boundary_competitive"]),
            "prefix_remote_basin_competitive": bool(
                prefix_assessment["remote_basin_competitive"]
            ),
            "prefix_to_full_center_delta_m": prefix_to_full_delta,
            "prefix_to_full_center_delta_role": "DIAGNOSTIC_ONLY_FUTURE_INFORMED_NOT_A_PASS_TERM",
            "prefix_heldin_residual_rms_mps2": prefix_heldin_rms,
            "prefix_heldin_standardized_residual_rms": (
                prefix_heldin_standardized_rms
            ),
            "full_heldin_residual_rms_mps2": full_heldin_rms,
            "full_heldin_standardized_residual_rms": full_heldin_standardized_rms,
            "full_heldin_residual_role": "DIAGNOSTIC_ONLY_NOT_USED_TO_RELAX_PREFIX_GATE",
            "prefix_heldin_standardized_residual_limit": heldin_standardized_limit,
            "prefix_feasible_gls_sigma_relative_fixed_point_change": (
                prefix_reweight_change
            ),
            "pass": bool(
                prefix_assessment["basin_identifiable"]
                and prefix_reweight_change <= float(
                    settings["maximum_reweight_sigma_relative_rms_change"]
                )
                and prefix_heldin_standardized_rms <= heldin_standardized_limit
            ),
        }
    else:
        prefix_stability = {
            "available": False,
            "chronological_prefix_fraction": prefix_fraction,
            "prefix_block_count": prefix_block_count,
            "heldin_block_count": heldin_block_count,
            "minimum_blocks_per_partition": minimum_partition_blocks,
            "pass": False,
            "cause": "INSUFFICIENT_COMPLETE_GAP_SAFE_BLOCKS_FOR_PREFIX_HELDIN_PARTITION",
        }

    # Preserve every finite numerical-interior candidate as explicit posterior
    # branch evidence.  The progressive geometry owner consumes the primary
    # moment while the full branch rows remain immutable renderer/audit data.
    # Coherent nuisance sensitivity is deliberately not run here in online
    # mode; its uncertainty is marginalized below and final refits remain an
    # offline validation obligation.
    online_mixture = _online_center_candidate_mixture(
        full_trials=trials,
        prefix_trials=prefix_trials,
        informed_observation_covariance_m2=informed_observation_covariance,
        nullspace_covariance_m2=nullspace_covariance,
        systematic_covariance_m2=systematic_covariance,
        enabled=online_owner_enabled,
        incomplete_nuisance_sigma_m=float(
            online_owner.get("incomplete_nuisance_center_sigma_m", 0.0)
        ),
    )
    online_candidate_branches = list(online_mixture["branches"])
    online_between_candidate_covariance = np.asarray(
        online_mixture["between_candidate_covariance_m2"], dtype=float,
    )
    online_incomplete_nuisance_covariance = np.asarray(
        online_mixture["incomplete_nuisance_covariance_m2"], dtype=float,
    )
    informed_observation_covariance = np.asarray(
        online_mixture["informed_observation_covariance_m2"], dtype=float,
    )
    statistical_covariance = np.asarray(
        online_mixture["statistical_covariance_m2"], dtype=float,
    )
    covariance = np.asarray(online_mixture["total_covariance_m2"], dtype=float)
    execution_guard.validate_connection_vector(rp)
    execution_guard.validate_connection_vector(rc)
    at_guard = bool(selected_trial["boundary_guard_active"])
    information_condition_eligible = bool(
        rank > 0
        and len(blocks) >= minimum_blocks
        and len(cluster_values) > rank
        and float(selected_trial["sigma_relative_fixed_point_change"])
        <= float(settings["maximum_reweight_sigma_relative_rms_change"])
        and float(singular[0] / max(singular[-1], np.finfo(float).eps))
        <= float(settings["maximum_robust_bread_condition_number"])
        and minimum_informed_information_eigenvalue
        >= float(settings["minimum_informed_information_eigenvalue_m2_inv"])
        and maximum_informed_observation_sigma
        <= float(settings["maximum_informed_observation_sigma_m"])
    )
    pre_coherent_refit_owner_update_eligible = bool(
        result.success
        and not at_guard
        and multistart_assessment["basin_identifiable"]
        and information_condition_eligible
        and prefix_stability["pass"]
        and not unknown_boot_transition_pairs
    )
    online_candidate_owner_update_eligible = bool(
        online_owner_enabled
        and online_candidate_branches
        and result.success
        and not at_guard
        and rank > 0
        and not unknown_boot_transition_pairs
    )
    coherent_settings = settings.get("coherent_nuisance_refit_audit")
    expected_coherent_components = (
        "ACCELEROMETER_BIAS_DRIFT",
        "ACCELEROMETER_SCALE_CROSS_AXIS",
        "GYRO_BIAS_DRIFT",
        "GYRO_SCALE_CROSS_AXIS",
        "SHARED_ACCELEROMETER_GYRO_SCALE_CROSS_AXIS",
        "PERSISTENT_PAIR_CLOCK",
        "HUMAN_WORN_CENTER_MIGRATION",
    )
    if (
        not isinstance(coherent_settings, Mapping)
        or coherent_settings.get("schema")
        != "biospur-c2-center-coherent-nuisance-refit-audit-settings-v1"
        or tuple(coherent_settings.get("components", ()))
        != expected_coherent_components
        or tuple(int(value) for value in coherent_settings.get("antithetic_signs", ()))
        != (-1, 1)
        or int(coherent_settings.get("direction_count", 0)) != 18
        or coherent_settings.get("whitened_direction_design")
        != "SEEDED_ORTHONORMAL_UNIT_MAHALANOBIS_DIRECTIONS_CYCLED_AFTER_FULL_DIMENSIONAL_SPAN"
        or float(coherent_settings.get("required_whitened_radius", 0.0)) != 1.0
        or float(coherent_settings.get("standard_deviation_scale", 0.0)) != 1.0
        or float(coherent_settings.get("maximum_full_scale_candidate_displacement_m", -1.0))
        != float(settings["interior_basin_radius_m"])
        or float(coherent_settings.get("maximum_antithetic_ensemble_midpoint_shift_m", -1.0))
        != float(settings["interior_basin_radius_m"])
        or float(coherent_settings.get("branch_switch_radius_m", -1.0))
        != float(settings["interior_basin_radius_m"])
    ):
        raise RuntimeError("center coherent nuisance refit audit is not preregistered")

    def center_terms_from_filtered(
        omega: np.ndarray, alpha_value: np.ndarray,
    ) -> np.ndarray:
        terms = np.empty((len(omega), 3, 3), dtype=float)
        identity = np.eye(3)
        for column in range(3):
            direction = np.repeat(identity[column][None, :], len(omega), axis=0)
            terms[:, :, column] = (
                np.cross(alpha_value, direction)
                + np.cross(omega, np.cross(omega, direction))
            )
        return terms

    coherent_rng = np.random.default_rng(
        int(coherent_settings["fixed_direction_seed"])
    )

    direction_count = int(coherent_settings["direction_count"])

    def fixed_unit_directions(size: int) -> np.ndarray:
        dimension = int(size)
        raw = coherent_rng.normal(size=(dimension, dimension))
        orthogonal, upper = np.linalg.qr(raw)
        signs = np.where(np.diag(upper) < 0.0, -1.0, 1.0)
        orthogonal = orthogonal * signs[None, :]
        directions = np.asarray([
            orthogonal[:, index % dimension]
            for index in range(direction_count)
        ])
        radii = np.linalg.norm(directions, axis=1)
        if (
            not np.all(np.isfinite(directions))
            or not np.allclose(radii, 1.0, atol=1e-12, rtol=0.0)
            or np.linalg.matrix_rank(directions[:dimension]) != dimension
        ):
            raise RuntimeError("center unit-Mahalanobis direction design is invalid")
        return directions

    coherent_directions = {
        "ACCELEROMETER_BIAS_DRIFT": fixed_unit_directions(12),
        "ACCELEROMETER_SCALE_CROSS_AXIS": fixed_unit_directions(18),
        "GYRO_BIAS_DRIFT": fixed_unit_directions(12),
        "GYRO_SCALE_CROSS_AXIS": fixed_unit_directions(18),
        "SHARED_ACCELEROMETER_GYRO_SCALE_CROSS_AXIS": fixed_unit_directions(18),
        "PERSISTENT_PAIR_CLOCK": fixed_unit_directions(len(clock_groups)),
        "HUMAN_WORN_CENTER_MIGRATION": fixed_unit_directions(6),
    }

    def covariance_square_root(covariance_value: np.ndarray) -> np.ndarray:
        values, vectors = np.linalg.eigh(0.5 * (covariance_value + covariance_value.T))
        return vectors @ np.diag(np.sqrt(np.maximum(values, 0.0))) @ vectors.T

    parent_gyro_bias_root = covariance_square_root(parent_gyro_bias_covariance)
    child_gyro_bias_root = covariance_square_root(child_gyro_bias_covariance)
    def boot_safe_centered_elapsed(endpoint: str) -> np.ndarray:
        return _center_boot_safe_centered_elapsed(
            np.asarray(full_data[f"{endpoint}_observed_time_s"], dtype=float),
            np.asarray(full_data["physical_time_epoch_group"], dtype=np.int64),
            horizon_s=accelerometer_bias_drift_horizon,
        )

    parent_nuisance_time = boot_safe_centered_elapsed("parent")
    child_nuisance_time = boot_safe_centered_elapsed("child")
    physical_time_epoch_groups = np.asarray(
        full_data["physical_time_epoch_group"], dtype=np.int64,
    )

    def drift_stress_input_hash(
        elapsed_s: np.ndarray,
        direction_vectors: np.ndarray,
    ) -> str:
        stress = (
            np.asarray(elapsed_s, dtype=float)[:, None, None]
            * np.asarray(direction_vectors, dtype=float)[None, :, :]
        )
        return _array_sha256(stress)

    acc_directions = coherent_directions["ACCELEROMETER_BIAS_DRIFT"]
    gyro_directions = coherent_directions["GYRO_BIAS_DRIFT"]
    parent_acc_drift_vectors = (
        accelerometer_bias_drift_rate * acc_directions[:, 6:9]
    )
    child_acc_drift_vectors = (
        accelerometer_bias_drift_rate * acc_directions[:, 9:12]
    )
    parent_gyro_drift_vectors = (
        gyro_directions[:, 6:9] @ parent_gyro_bias_root.T
    ) / bias_drift_horizon
    child_gyro_drift_vectors = (
        gyro_directions[:, 9:12] @ child_gyro_bias_root.T
    ) / bias_drift_horizon
    physical_time_elapsed_diagnostic = {
        "schema": "biospur-c2-center-physical-time-drift-input-provenance-v1",
        "registered_policy": dict(physical_time_diagnostic_policy),
        "role": "RESULT_INDEPENDENT_PROVENANCE_DIAGNOSTIC_ONLY",
        "computed_before_coherent_nuisance_refit_loop": True,
        "available_when_nominal_factor_is_local_no_update": True,
        "owner_update_eligibility_or_successful_refit_required": False,
        "observed_physical_time_owner": "AlignedPair_FROM_CURRENT_SEALED_ORIENTED_ACTION",
        "observed_physical_time_units": "s",
        "elapsed_transform": "NODE_SPECIFIC_MEDIAN_CENTERED_WITHIN_TRUSTWORTHY_BOOT_EPOCH_IDENTITY_THEN_CLIPPED",
        "parent_observed_physical_time_s_sha256_after_cap": _array_sha256(
            np.asarray(full_data["parent_observed_time_s"], dtype=float)
        ),
        "child_observed_physical_time_s_sha256_after_cap": _array_sha256(
            np.asarray(full_data["child_observed_time_s"], dtype=float)
        ),
        "physical_time_epoch_group_sha256_after_cap": _array_sha256(
            physical_time_epoch_groups
        ),
        "parent_centered_clipped_elapsed_s_sha256": _array_sha256(
            parent_nuisance_time
        ),
        "child_centered_clipped_elapsed_s_sha256": _array_sha256(
            child_nuisance_time
        ),
        "parent_centered_clipped_elapsed_minimum_s": float(
            np.min(parent_nuisance_time)
        ),
        "parent_centered_clipped_elapsed_maximum_s": float(
            np.max(parent_nuisance_time)
        ),
        "child_centered_clipped_elapsed_minimum_s": float(
            np.min(child_nuisance_time)
        ),
        "child_centered_clipped_elapsed_maximum_s": float(
            np.max(child_nuisance_time)
        ),
        "parent_elapsed_horizon_clipped_row_count": int(np.count_nonzero(
            np.abs(parent_nuisance_time)
            >= accelerometer_bias_drift_horizon
        )),
        "child_elapsed_horizon_clipped_row_count": int(np.count_nonzero(
            np.abs(child_nuisance_time)
            >= accelerometer_bias_drift_horizon
        )),
        "epoch_groups": [{
            "physical_time_epoch_group": int(group),
            "row_count": int(np.count_nonzero(physical_time_epoch_groups == group)),
            "parent_observed_time_s_sha256": _array_sha256(np.asarray(
                full_data["parent_observed_time_s"], dtype=float,
            )[physical_time_epoch_groups == group]),
            "child_observed_time_s_sha256": _array_sha256(np.asarray(
                full_data["child_observed_time_s"], dtype=float,
            )[physical_time_epoch_groups == group]),
            "parent_centered_clipped_elapsed_s_sha256": _array_sha256(
                parent_nuisance_time[physical_time_epoch_groups == group]
            ),
            "child_centered_clipped_elapsed_s_sha256": _array_sha256(
                child_nuisance_time[physical_time_epoch_groups == group]
            ),
        } for group in np.unique(physical_time_epoch_groups)],
        "registered_unit_mahalanobis_direction_count": direction_count,
        "accelerometer_bias_drift_rate_sigma_mps3": (
            accelerometer_bias_drift_rate
        ),
        "gyro_bias_drift_horizon_s": bias_drift_horizon,
        "accelerometer_bias_drift_directions_sha256": _array_sha256(
            acc_directions
        ),
        "gyro_bias_drift_directions_sha256": _array_sha256(gyro_directions),
        "drift_stress_input_hashes": {
            "parent_accelerometer": drift_stress_input_hash(
                parent_nuisance_time, parent_acc_drift_vectors,
            ),
            "child_accelerometer": drift_stress_input_hash(
                child_nuisance_time, child_acc_drift_vectors,
            ),
            "parent_gyroscope": drift_stress_input_hash(
                parent_nuisance_time, parent_gyro_drift_vectors,
            ),
            "child_gyroscope": drift_stress_input_hash(
                child_nuisance_time, child_gyro_drift_vectors,
            ),
        },
        "known_same_boot_gaps_preserved_without_cross_gap_differentiation": True,
        "cross_epoch_elapsed_continuity_invented": False,
        "unknown_boot_transition_local_no_update": bool(
            unknown_boot_transition_pairs
        ),
        "candidate_solution_or_center": False,
        "covariance_or_information": False,
        "posterior_or_branch": False,
        "may_promote_owner_update": False,
        "permitted_consumers": [
            "PHYSICAL_TIME_PROVENANCE_AND_MUTATION_DIAGNOSTIC_ONLY"
        ],
        "forbidden_consumers": [
            "SEGMENT_FRAMES", "QMT_HEADING", "ROOTED_PROPAGATION",
            "SCIENTIFIC_RENDERER", "PROGRESSIVE_CALIBRATION",
            "QUALIFICATION", "PREFIT_SEAL",
        ],
    }

    def coherent_perturbed_data(
        component: str, direction_index: int, sign: int,
    ) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
        data = {
            key: np.asarray(value).copy()
            for key, value in full_data.items()
        }
        direction = coherent_directions[component][direction_index]
        audit: dict[str, Any] = {
            "component": component,
            "direction_index": int(direction_index),
            "sign": int(sign),
            "standard_deviation_scale": 1.0,
            "direction": direction.tolist(),
            "whitened_radius": float(np.linalg.norm(direction)),
            "direction_semantics": (
                "DETERMINISTIC_UNIT_MAHALANOBIS_RADIUS_NOT_N0C_DRAW"
            ),
            "gap_or_block_boundary_crossed": False,
        }
        if component == "ACCELEROMETER_BIAS_DRIFT":
            parent_bias = accelerometer_bias_sigma * direction[0:3]
            child_bias = accelerometer_bias_sigma * direction[3:6]
            parent_drift = accelerometer_bias_drift_rate * direction[6:9]
            child_drift = accelerometer_bias_drift_rate * direction[9:12]
            data["parent_acc"] += sign * (
                parent_bias[None, :]
                + parent_nuisance_time[:, None] * parent_drift[None, :]
            )
            data["child_acc"] += sign * (
                child_bias[None, :]
                + child_nuisance_time[:, None] * child_drift[None, :]
            )
            audit["bias_sigma_mps2"] = accelerometer_bias_sigma
            audit["drift_rate_sigma_mps3"] = accelerometer_bias_drift_rate
            audit["drift_observed_time_clipped_to_registered_horizon"] = True
            audit["parent_observed_physical_time_s_sha256"] = _array_sha256(
                np.asarray(data["parent_observed_time_s"], dtype=float)
            )
            audit["child_observed_physical_time_s_sha256"] = _array_sha256(
                np.asarray(data["child_observed_time_s"], dtype=float)
            )
            audit["physical_span_group_sha256"] = _array_sha256(
                np.asarray(data["physical_span_group"], dtype=np.int64)
            )
            audit["physical_time_epoch_group_sha256"] = _array_sha256(
                np.asarray(data["physical_time_epoch_group"], dtype=np.int64)
            )
            audit["parent_centered_elapsed_s_sha256"] = _array_sha256(
                parent_nuisance_time
            )
            audit["child_centered_elapsed_s_sha256"] = _array_sha256(
                child_nuisance_time
            )
            audit["parent_centered_elapsed_minimum_s"] = float(
                np.min(parent_nuisance_time)
            )
            audit["parent_centered_elapsed_maximum_s"] = float(
                np.max(parent_nuisance_time)
            )
            audit["child_centered_elapsed_minimum_s"] = float(
                np.min(child_nuisance_time)
            )
            audit["child_centered_elapsed_maximum_s"] = float(
                np.max(child_nuisance_time)
            )
            audit["known_same_boot_gaps_preserved_in_elapsed_time"] = True
            audit["cross_gap_or_boot_elapsed_invented"] = False
        elif component == "ACCELEROMETER_SCALE_CROSS_AXIS":
            parent_matrix = direction[:9].reshape(3, 3)
            child_matrix = direction[9:].reshape(3, 3)
            data["parent_acc"] += sign * accelerometer_scale_cross_sigma * (
                data["parent_acc"] @ parent_matrix.T
            )
            data["child_acc"] += sign * accelerometer_scale_cross_sigma * (
                data["child_acc"] @ child_matrix.T
            )
            audit["fraction_sigma"] = accelerometer_scale_cross_sigma
        elif component == "GYRO_BIAS_DRIFT":
            parent_bias = parent_gyro_bias_root @ direction[0:3]
            child_bias = child_gyro_bias_root @ direction[3:6]
            parent_drift = (
                parent_gyro_bias_root @ direction[6:9]
            ) / bias_drift_horizon
            child_drift = (
                child_gyro_bias_root @ direction[9:12]
            ) / bias_drift_horizon
            data["parent_gyro"] += sign * (
                parent_bias[None, :]
                + parent_nuisance_time[:, None] * parent_drift[None, :]
            )
            data["child_gyro"] += sign * (
                child_bias[None, :]
                + child_nuisance_time[:, None] * child_drift[None, :]
            )
            data["parent_alpha"] += sign * parent_drift[None, :]
            data["child_alpha"] += sign * child_drift[None, :]
            audit["drift_horizon_s"] = bias_drift_horizon
            audit["parent_observed_physical_time_s_sha256"] = _array_sha256(
                np.asarray(data["parent_observed_time_s"], dtype=float)
            )
            audit["child_observed_physical_time_s_sha256"] = _array_sha256(
                np.asarray(data["child_observed_time_s"], dtype=float)
            )
            audit["physical_span_group_sha256"] = _array_sha256(
                np.asarray(data["physical_span_group"], dtype=np.int64)
            )
            audit["physical_time_epoch_group_sha256"] = _array_sha256(
                np.asarray(data["physical_time_epoch_group"], dtype=np.int64)
            )
            audit["parent_centered_elapsed_s_sha256"] = _array_sha256(
                parent_nuisance_time
            )
            audit["child_centered_elapsed_s_sha256"] = _array_sha256(
                child_nuisance_time
            )
            audit["parent_centered_elapsed_minimum_s"] = float(
                np.min(parent_nuisance_time)
            )
            audit["parent_centered_elapsed_maximum_s"] = float(
                np.max(parent_nuisance_time)
            )
            audit["child_centered_elapsed_minimum_s"] = float(
                np.min(child_nuisance_time)
            )
            audit["child_centered_elapsed_maximum_s"] = float(
                np.max(child_nuisance_time)
            )
            audit["known_same_boot_gaps_preserved_in_elapsed_time"] = True
            audit["cross_gap_or_boot_elapsed_invented"] = False
        elif component in {
            "GYRO_SCALE_CROSS_AXIS",
            "SHARED_ACCELEROMETER_GYRO_SCALE_CROSS_AXIS",
        }:
            parent_matrix = direction[:9].reshape(3, 3)
            child_matrix = direction[9:].reshape(3, 3)
            sigma_value = (
                scale_cross_sigma
                if component == "GYRO_SCALE_CROSS_AXIS"
                else shared_scale_cross_sigma
            )
            data["parent_gyro"] += sign * sigma_value * (
                data["parent_gyro"] @ parent_matrix.T
            )
            data["child_gyro"] += sign * sigma_value * (
                data["child_gyro"] @ child_matrix.T
            )
            data["parent_alpha"] += sign * sigma_value * (
                data["parent_alpha"] @ parent_matrix.T
            )
            data["child_alpha"] += sign * sigma_value * (
                data["child_alpha"] @ child_matrix.T
            )
            if component == "SHARED_ACCELEROMETER_GYRO_SCALE_CROSS_AXIS":
                data["parent_acc"] += sign * sigma_value * (
                    data["parent_acc"] @ parent_matrix.T
                )
                data["child_acc"] += sign * sigma_value * (
                    data["child_acc"] @ child_matrix.T
                )
            audit["fraction_sigma"] = sigma_value
        elif component == "PERSISTENT_PAIR_CLOCK":
            symmetric_interior_keep = np.ones(len(full_data["child_acc"]), dtype=bool)
            group_offsets_s: dict[int, float] = {}
            for group_index, group in enumerate(clock_groups):
                group_mask = np.asarray(full_data["clock_group"] == group)
                group_sigma = float(np.sqrt(np.mean(
                    full_data["clock_lag_variance_s2"][group_mask]
                )))
                offset_s = sign * direction[group_index] * group_sigma
                group_offsets_s[int(group)] = offset_s
                for cluster in np.unique(full_data["cluster_id"][group_mask]):
                    indices = np.flatnonzero(
                        group_mask & (full_data["cluster_id"] == cluster)
                    )
                    local_time = np.asarray(
                        full_data["block_local_time_s"][indices], dtype=float,
                    )
                    absolute_offset = abs(offset_s)
                    cluster_keep = (
                        (local_time - absolute_offset >= local_time[0])
                        & (local_time + absolute_offset <= local_time[-1])
                    )
                    symmetric_interior_keep[indices[~cluster_keep]] = False
            retained_indices = np.flatnonzero(symmetric_interior_keep)
            shifted_values = {
                key: np.empty((len(retained_indices), 3), dtype=float)
                for key in ("child_acc", "child_gyro", "child_alpha")
            }
            retained_cursor = {int(index): position for position, index in enumerate(retained_indices)}
            for group in clock_groups:
                group_mask = np.asarray(full_data["clock_group"] == group)
                offset_s = group_offsets_s[int(group)]
                for cluster in np.unique(full_data["cluster_id"][group_mask]):
                    source_indices = np.flatnonzero(
                        group_mask & (full_data["cluster_id"] == cluster)
                    )
                    target_indices = source_indices[symmetric_interior_keep[source_indices]]
                    local_time = np.asarray(
                        full_data["block_local_time_s"][source_indices], dtype=float,
                    )
                    query = np.asarray(
                        full_data["block_local_time_s"][target_indices], dtype=float,
                    ) + offset_s
                    if (
                        len(query)
                        and (
                            float(np.min(query)) < float(local_time[0])
                            or float(np.max(query)) > float(local_time[-1])
                        )
                    ):
                        raise RuntimeError(
                            "clock coherent refit attempted cross-block interpolation"
                        )
                    output_positions = np.asarray([
                        retained_cursor[int(index)] for index in target_indices
                    ], dtype=int)
                    for key in shifted_values:
                        source = np.asarray(full_data[key][source_indices], dtype=float)
                        shifted_values[key][output_positions] = np.column_stack([
                            np.interp(query, local_time, source[:, column])
                            for column in range(3)
                        ])
            data = {
                key: np.asarray(value)[retained_indices].copy()
                for key, value in data.items()
            }
            for key, value in shifted_values.items():
                data[key] = value
            audit["offset_convention"] = (
                "POSITIVE_OFFSET_EVALUATES_CHILD_LATER_AT_FIXED_PARENT_TIME"
            )
            audit["symmetric_interior_support_for_both_antithetic_signs"] = True
            audit["guarded_endpoint_rows"] = int(np.count_nonzero(
                ~symmetric_interior_keep
            ))
            audit["retained_interior_rows"] = int(len(retained_indices))
            audit["retained_interior_indices_sha256"] = _array_sha256(
                retained_indices.astype(np.int64)
            )
            audit["endpoint_clamped_or_repeated_rows"] = 0
            audit["cross_block_or_gap_interpolation_count"] = 0
        elif component == "HUMAN_WORN_CENTER_MIGRATION":
            center_delta = model_floor * direction
            data["parent_acc"] += sign * np.einsum(
                "nij,j->ni", data["parent_terms"], center_delta[:3],
            )
            data["child_acc"] += sign * np.einsum(
                "nij,j->ni", data["child_terms"], center_delta[3:],
            )
            audit["center_migration_sigma_m"] = model_floor
        else:  # pragma: no cover - exact component closure is validated above
            raise AssertionError("unknown center coherent nuisance component")

        if component in {
            "GYRO_BIAS_DRIFT",
            "GYRO_SCALE_CROSS_AXIS",
            "SHARED_ACCELEROMETER_GYRO_SCALE_CROSS_AXIS",
            "PERSISTENT_PAIR_CLOCK",
        }:
            data["parent_terms"] = center_terms_from_filtered(
                data["parent_gyro"], data["parent_alpha"],
            )
            data["child_terms"] = center_terms_from_filtered(
                data["child_gyro"], data["child_alpha"],
            )
        return data, audit

    coherent_refit_rows: list[dict[str, Any]] = []
    coherent_refit_component_rows: list[dict[str, Any]] = []
    if pre_coherent_refit_owner_update_eligible and not online_owner_enabled:
        for component in expected_coherent_components:
            direction_rows = []
            for direction_index in range(direction_count):
                signed_rows = []
                for sign in (-1, 1):
                    direction = coherent_directions[component][direction_index]
                    perturbation_audit = {
                        "component": component,
                        "direction_index": int(direction_index),
                        "sign": int(sign),
                        "standard_deviation_scale": 1.0,
                        "direction": direction.tolist(),
                        "whitened_radius": float(np.linalg.norm(direction)),
                        "direction_semantics": (
                            "DETERMINISTIC_UNIT_MAHALANOBIS_RADIUS_NOT_N0C_DRAW"
                        ),
                        "gap_or_block_boundary_crossed": False,
                        "perturbation_construction_completed": False,
                    }
                    try:
                        perturbed_data, constructed_audit = coherent_perturbed_data(
                            component, direction_index, sign,
                        )
                        perturbation_audit = {
                            **constructed_audit,
                            "perturbation_construction_completed": True,
                        }
                        refit = run_one_trial(
                            perturbed_data,
                            start=result.x.copy(),
                            start_index=-1,
                            budget_context={
                                "phase": "COHERENT_NUISANCE_REFIT",
                                "component": component,
                                "direction_index": int(direction_index),
                                "sign": int(sign),
                            },
                        )
                        refit_result = refit["result"]
                        solution = np.asarray(refit_result.x, dtype=float)
                        displacement = float(np.linalg.norm(solution - result.x))
                        boundary_active = bool(refit["boundary_guard_active"])
                        branch_switched = bool(
                            displacement
                            > float(coherent_settings["branch_switch_radius_m"])
                        )
                        signed_row = {
                            **perturbation_audit,
                            "solver_success": bool(refit_result.success),
                            "solver_status": int(refit_result.status),
                            "solver_message": str(refit_result.message),
                            "function_evaluations": int(refit_result.nfev),
                            "normalized_cost": float(refit["normalized_cost"]),
                            "solution_m": solution.tolist(),
                            "candidate_displacement_from_nominal_m": displacement,
                            "boundary_guard_active": boundary_active,
                            "branch_or_basin_switched": branch_switched,
                            "exception_type": None,
                            "exception_message": None,
                        }
                    except CenterFactorAggregateBudgetExceeded:
                        raise
                    except (
                        ValueError,
                        RuntimeError,
                        FloatingPointError,
                        np.linalg.LinAlgError,
                    ) as exc:
                        signed_row = {
                            **perturbation_audit,
                            "solver_success": False,
                            "solution_m": None,
                            "candidate_displacement_from_nominal_m": None,
                            "boundary_guard_active": False,
                            "branch_or_basin_switched": False,
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                        }
                    signed_rows.append(signed_row)
                    coherent_refit_rows.append(signed_row)
                solutions_available = all(
                    row["solution_m"] is not None for row in signed_rows
                )
                if solutions_available:
                    minus_solution = np.asarray(
                        signed_rows[0]["solution_m"], dtype=float,
                    )
                    plus_solution = np.asarray(
                        signed_rows[1]["solution_m"], dtype=float,
                    )
                    midpoint_shift = float(np.linalg.norm(
                        0.5 * (minus_solution + plus_solution) - result.x
                    ))
                    half_spread = float(0.5 * np.linalg.norm(
                        plus_solution - minus_solution
                    ))
                    maximum_displacement = max(
                        float(row["candidate_displacement_from_nominal_m"])
                        for row in signed_rows
                    )
                else:
                    midpoint_shift = None
                    half_spread = None
                    maximum_displacement = None
                direction_pass = bool(
                    solutions_available
                    and all(row["solver_success"] for row in signed_rows)
                    and not any(row["boundary_guard_active"] for row in signed_rows)
                    and not any(row["branch_or_basin_switched"] for row in signed_rows)
                    and all(
                        int(row.get("endpoint_clamped_or_repeated_rows", 0)) == 0
                        and int(row.get("cross_block_or_gap_interpolation_count", 0)) == 0
                        and row.get("gap_or_block_boundary_crossed", False) is False
                        for row in signed_rows
                    )
                    and maximum_displacement
                    <= float(coherent_settings[
                        "maximum_full_scale_candidate_displacement_m"
                    ])
                    and midpoint_shift
                    <= float(coherent_settings[
                        "maximum_antithetic_ensemble_midpoint_shift_m"
                    ])
                )
                direction_rows.append({
                    "direction_index": int(direction_index),
                    "whitened_radius": float(np.linalg.norm(
                        coherent_directions[component][direction_index]
                    )),
                    "signed_refits": signed_rows,
                    "maximum_full_scale_candidate_displacement_m": (
                        maximum_displacement
                    ),
                    "antithetic_half_spread_m": half_spread,
                    "antithetic_midpoint_shift_m": midpoint_shift,
                    "pass": direction_pass,
                })
            component_pass = bool(all(row["pass"] for row in direction_rows))
            valid_midpoint_vectors = [
                0.5 * (
                    np.asarray(row["signed_refits"][0]["solution_m"], dtype=float)
                    + np.asarray(row["signed_refits"][1]["solution_m"], dtype=float)
                ) - result.x
                for row in direction_rows
                if all(
                    signed["solution_m"] is not None
                    for signed in row["signed_refits"]
                )
            ]
            coherent_refit_component_rows.append({
                "component": component,
                "direction_semantics": (
                    "DETERMINISTIC_UNIT_MAHALANOBIS_RADIUS_NOT_N0C_DRAW"
                ),
                "direction_count": direction_count,
                "whitened_dimension": int(coherent_directions[component].shape[1]),
                "full_dimension_spanned_before_cycle": True,
                "direction_rows": direction_rows,
                "maximum_directional_midpoint_shift_m": (
                    max(
                        float(row["antithetic_midpoint_shift_m"])
                        for row in direction_rows
                        if row["antithetic_midpoint_shift_m"] is not None
                    )
                    if valid_midpoint_vectors else None
                ),
                "directional_midpoint_mean_shift_m": (
                    float(np.linalg.norm(np.mean(valid_midpoint_vectors, axis=0)))
                    if valid_midpoint_vectors else None
                ),
                "pass": component_pass,
            })
        coherent_refit_pass = bool(all(
            row["pass"] for row in coherent_refit_component_rows
        ))
        coherent_refit_status = (
            "PASS" if coherent_refit_pass
            else "FAIL_LOCAL_NO_UPDATE_COHERENT_NUISANCE_FRAGILITY"
        )
    elif online_candidate_owner_update_eligible:
        coherent_refit_pass = False
        coherent_refit_status = "DEFERRED_TO_OFFLINE_FINAL_VALIDATION"
    else:
        coherent_refit_pass = False
        coherent_refit_status = "NOT_RUN_NOMINAL_ALREADY_LOCAL_NO_UPDATE"

    coherent_refit_provenance_rows = [
        {
            "component": str(component_row["component"]),
            "direction_index": int(direction_row["direction_index"]),
            "sign": int(signed["sign"]),
            "solution_m": (
                None if signed["solution_m"] is None
                else list(signed["solution_m"])
            ),
            "solver_success": bool(signed["solver_success"]),
            "boundary_guard_active": bool(signed["boundary_guard_active"]),
            "branch_or_basin_switched": bool(
                signed["branch_or_basin_switched"]
            ),
            "exception_type": signed["exception_type"],
        }
        for component_row in coherent_refit_component_rows
        for direction_row in component_row["direction_rows"]
        for signed in direction_row["signed_refits"]
    ]
    clean_successful_rows = [
        row for row in coherent_refit_provenance_rows
        if (
            row["solution_m"] is not None
            and row["solver_success"]
            and row["exception_type"] is None
            and not row["boundary_guard_active"]
            and not row["branch_or_basin_switched"]
        )
    ]
    nonclean_successful_rows = [
        row for row in coherent_refit_provenance_rows
        if (
            row["solution_m"] is not None
            and row["solver_success"]
            and row["exception_type"] is None
            and (
                row["boundary_guard_active"]
                or row["branch_or_basin_switched"]
            )
        )
    ]
    failed_or_missing_count = int(sum(
        row["solution_m"] is None
        or not row["solver_success"]
        or row["exception_type"] is not None
        for row in coherent_refit_provenance_rows
    ))
    forbidden_extent_consumers = [
        "CENTER_OWNER_UPDATE",
        "SEGMENT_FRAMES",
        "QMT_HEADING",
        "ROOTED_PROPAGATION",
        "SCIENTIFIC_RENDERER",
        "PROGRESSIVE_CALIBRATION",
        "SYNTHETIC_QUALIFICATION_PASS_OR_MUTATION_PASS",
        "PREFIT_REGISTRY_SEAL_AUTHORITY",
    ]
    if clean_successful_rows and not coherent_refit_pass:
        clean_solutions = np.asarray([
            row["solution_m"] for row in clean_successful_rows
        ], dtype=float)
        extent_offsets = clean_solutions - result.x[None, :]
        _, descriptive_singular_values, descriptive_axes = np.linalg.svd(
            extent_offsets, full_matrices=False,
        )
        extent_projections = extent_offsets @ descriptive_axes.T
        per_component_extent = []
        for component in expected_coherent_components:
            rows = [
                row for row in clean_successful_rows
                if row["component"] == component
            ]
            solutions = np.asarray(
                [row["solution_m"] for row in rows], dtype=float,
            ).reshape((-1, 6))
            per_component_extent.append({
                "component": component,
                "clean_successful_count": len(rows),
                "direction_sign_rows": [
                    [int(row["direction_index"]), int(row["sign"])]
                    for row in rows
                ],
                "solutions_sha256": _array_sha256(solutions),
                "coordinate_minimum_m": (
                    np.min(solutions, axis=0).tolist() if len(rows) else None
                ),
                "coordinate_maximum_m": (
                    np.max(solutions, axis=0).tolist() if len(rows) else None
                ),
            })
        coherent_refit_extent_diagnostic = {
            "schema": "biospur-c2-center-successful-refit-extent-diagnostic-v1",
            "status": "DIAGNOSTIC_ATTACHED_TO_LOCAL_NO_UPDATE_ONLY",
            "source_signed_refit_count": len(coherent_refit_provenance_rows),
            "clean_within_basin_successful_count": len(clean_successful_rows),
            "clean_within_basin_successful_rows": clean_successful_rows,
            "clean_within_basin_solutions_sha256": _array_sha256(clean_solutions),
            "per_component_clean_extent": per_component_extent,
            "nonclean_successful_count": len(nonclean_successful_rows),
            "nonclean_successful_rows": nonclean_successful_rows,
            "failed_or_missing_refit_count": failed_or_missing_count,
            "failed_rows_retained_explicitly_at": (
                "coherent_nuisance_refit_audit.components[].direction_rows[]"
                ".signed_refits[]"
            ),
            "clean_coordinate_minimum_m": np.min(
                clean_solutions, axis=0,
            ).tolist(),
            "clean_coordinate_maximum_m": np.max(
                clean_solutions, axis=0,
            ).tolist(),
            "clean_maximum_offset_from_nominal_m": float(np.max(np.linalg.norm(
                extent_offsets, axis=1,
            ))),
            "descriptive_duplicate_design_weighted_svd_axes": (
                descriptive_axes.tolist()
            ),
            "descriptive_duplicate_design_weighted_singular_values_m": (
                descriptive_singular_values.tolist()
            ),
            "descriptive_svd_projection_minimum_m": np.min(
                extent_projections, axis=0,
            ).tolist(),
            "descriptive_svd_projection_maximum_m": np.max(
                extent_projections, axis=0,
            ).tolist(),
            "cycled_duplicate_directions_deduplicated_for_svd": False,
            "svd_axes_are_uncertainty_axes": False,
            "probability_sample_or_posterior_covariance_interpretation": False,
            "extent_is_an_ellipsoid_gaussian_or_single_branch_collapse": False,
            "anatomical_center_posterior_or_branch_interpretation": False,
            "scientific_owner_output_consumer_reachable": False,
            "may_promote_owner_update": False,
            "permitted_consumers": ["LOCAL_NO_UPDATE_DIAGNOSTIC_AUDIT_ONLY"],
            "forbidden_consumers": forbidden_extent_consumers,
        }
    else:
        coherent_refit_extent_diagnostic = {
            "schema": "biospur-c2-center-successful-refit-extent-diagnostic-v1",
            "status": (
                "NOT_EMITTED_OWNER_UPDATE_ELIGIBLE"
                if coherent_refit_pass
                else "NOT_AVAILABLE_NOMINAL_ALREADY_LOCAL_NO_UPDATE_OR_NO_CLEAN_SUCCESS"
            ),
            "source_signed_refit_count": len(coherent_refit_provenance_rows),
            "clean_within_basin_successful_count": 0,
            "clean_within_basin_successful_rows": [],
            "nonclean_successful_count": len(nonclean_successful_rows),
            "nonclean_successful_rows": nonclean_successful_rows,
            "failed_or_missing_refit_count": failed_or_missing_count,
            "svd_axes_are_uncertainty_axes": False,
            "probability_sample_or_posterior_covariance_interpretation": False,
            "extent_is_an_ellipsoid_gaussian_or_single_branch_collapse": False,
            "anatomical_center_posterior_or_branch_interpretation": False,
            "scientific_owner_output_consumer_reachable": False,
            "may_promote_owner_update": False,
            "permitted_consumers": ["LOCAL_NO_UPDATE_DIAGNOSTIC_AUDIT_ONLY"],
            "forbidden_consumers": forbidden_extent_consumers,
        }

    coherent_nuisance_refit_audit = {
        "schema": "biospur-c2-center-coherent-nuisance-refit-audit-v3",
        "nominal_candidate_pre_audit_eligible": (
            pre_coherent_refit_owner_update_eligible
        ),
        "fixed_direction_seed": int(coherent_settings["fixed_direction_seed"]),
        "direction_count": direction_count,
        "whitened_direction_design": str(
            coherent_settings["whitened_direction_design"]
        ),
        "direction_semantics": str(
            coherent_settings["whitened_direction_semantics"]
        ),
        "every_direction_has_unit_whitened_radius": bool(all(
            np.allclose(
                np.linalg.norm(directions, axis=1), 1.0,
                atol=1e-12, rtol=0.0,
            )
            for directions in coherent_directions.values()
        )),
        "standard_deviation_scale": 1.0,
        "components": coherent_refit_component_rows,
        "observed_physical_time_owner": "AlignedPair",
        "observed_physical_time_units": "s",
        "observed_physical_time_policy": dict(physical_time_policy),
        "physical_boot_safe_spans": physical_span_reports,
        "parent_observed_physical_time_s_sha256_after_cap": _array_sha256(
            np.asarray(full_data["parent_observed_time_s"], dtype=float)
        ),
        "child_observed_physical_time_s_sha256_after_cap": _array_sha256(
            np.asarray(full_data["child_observed_time_s"], dtype=float)
        ),
        "physical_span_group_sha256_after_cap": _array_sha256(
            np.asarray(full_data["physical_span_group"], dtype=np.int64)
        ),
        "physical_time_epoch_group_sha256_after_cap": _array_sha256(
            np.asarray(full_data["physical_time_epoch_group"], dtype=np.int64)
        ),
        "unknown_boot_transition_pairs": unknown_boot_transition_pairs,
        "unknown_boot_transition_local_no_update": bool(
            unknown_boot_transition_pairs
        ),
        "unknown_boot_interval_local_factor_disposition": "LOCAL_NO_UPDATE",
        "progressive_unknown_interval_floor_consumption_proven_in_this_owner": False,
        "progressive_unknown_interval_floor_requires_runtime_mutation": True,
        "elapsed_time_synthesized_from_block_count_rank_action_order_or_cap": False,
        "cross_gap_or_boot_elapsed_invented": False,
        "physical_time_elapsed_and_drift_input_provenance": (
            physical_time_elapsed_diagnostic
        ),
        "successful_refit_extent_diagnostic": coherent_refit_extent_diagnostic,
        "all_failed_boundary_or_branch_switched_refits_retained": True,
        "full_scale_displacement_limit_m": float(
            coherent_settings["maximum_full_scale_candidate_displacement_m"]
        ),
        "antithetic_midpoint_shift_limit_m": float(
            coherent_settings["maximum_antithetic_ensemble_midpoint_shift_m"]
        ),
        "branch_switch_radius_m": float(
            coherent_settings["branch_switch_radius_m"]
        ),
        "synthetic_truth_or_anthropometry_consumed": False,
        "global_solver_used": False,
        "status": coherent_refit_status,
        "pass": coherent_refit_pass,
        "factor_aggregate_solver_budget": {
            "schema": "biospur-c2-center-factor-aggregate-solver-budget-v1",
            "minimum_nominal_and_prefix_solver_calls": (
                minimum_nominal_and_prefix_solver_calls
            ),
            "expected_full_qualification_solver_calls": (
                expected_full_qualification_solver_calls
            ),
            "maximum_solver_calls": factor_maximum_solver_calls,
            "observed_solver_calls": factor_solver_calls,
            "maximum_function_evaluations_per_call": maximum_function_evaluations,
            "maximum_total_function_evaluations": (
                factor_maximum_total_function_evaluations
            ),
            "observed_total_function_evaluations": (
                factor_total_function_evaluations
            ),
            "every_least_squares_call_is_bounded": True,
            "budget_exhaustion_escapes_refit_failure_retention": True,
            "budget_exhaustion_disposition": (
                "TRANSACTION_ROLLBACK_THEN_CURRENT_PAIR_LOCAL_NO_UPDATE"
            ),
            "keyboard_interrupt_used_as_budget": False,
        },
    }
    owner_update_eligible = bool(
        online_candidate_owner_update_eligible
        or (
            pre_coherent_refit_owner_update_eligible
            and coherent_refit_pass
        )
    )
    status = "POSTERIOR_CANDIDATE" if owner_update_eligible else "LOW_INFORMATION_OR_NUMERICAL_GUARD_CANDIDATE"
    if (
        pre_coherent_refit_owner_update_eligible
        and not coherent_refit_pass
        and not online_owner_enabled
    ):
        status = "COHERENT_NUISANCE_FRAGILITY_LOCAL_NO_UPDATE"
    if (
        not online_candidate_owner_update_eligible
        and (rank < 6 or at_guard or not result.success)
    ):
        status = "LOW_INFORMATION_OR_NUMERICAL_GUARD_CANDIDATE"
    raw_residual = raw_residual_function(result.x)
    pair_block_cluster_identity_rows = [
        {
            "pair_index": int(block["pair_index"]),
            "action": str(block["action"]),
            "span_index": int(block["span_index"]),
            "block_index": int(block["block_index"]),
            "source_rows_within_contiguous_span_half_open": list(
                block["source_rows_within_contiguous_span_half_open"]
            ),
            "retained_rows_within_raw_block_half_open": list(
                block["retained_rows_within_raw_block_half_open"]
            ),
        }
        for block in blocks
    ]
    report = {
            "schema": "biospur-c2-pair-local-seel-style-joint-position-v1",
            "missing_capability_proof": "QMT_0_2_4_HAS_NO_PUBLIC_JOINT_CENTER_FUNCTION",
            "scope": "ONE_EDGE_SIX_COORDINATES;NO_BODY_WIDE_OR_HEADING_VARIABLES",
            "actions": [row.action for row in pairs],
            "ordered_pair_action_membership": [
                {
                    "pair_index": int(pair_index),
                    "action": row.action,
                    "runtime_owner_token": str(
                        row.provenance.get("runtime_owner_token", "")
                    ),
                }
                for pair_index, row in enumerate(pairs)
            ],
            "pair_block_cluster_identity_rows": pair_block_cluster_identity_rows,
            "pair_block_cluster_identity_sha256": sha256(
                json.dumps(
                    pair_block_cluster_identity_rows,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "per_action_pair_cluster_identity_preserved": True,
            "historical_rows_treated_as_iid_after_concatenation": False,
            "contiguous_span_blocks": len(blocks),
            "minimum_complete_blocks_for_update": minimum_blocks,
            "complete_blocks_are_cut_within_contiguous_spans": True,
            "gap_boundary_creates_center_block": False,
            "rows_before_blockwise_cap": int(sum(len(row["parent_acc"]) for row in blocks)),
            "rows_after_blockwise_cap": int(len(parent_acc)),
            "blockwise_cap_audit": cap_audit,
            "rows_differentiated_or_capped_across_gap": 0,
            "observed_physical_time_owner": "AlignedPair",
            "observed_physical_time_units": "s",
            "observed_physical_time_policy": dict(physical_time_policy),
            "physical_boot_safe_spans": physical_span_reports,
            "unknown_boot_transition_pairs": unknown_boot_transition_pairs,
            "unknown_boot_transition_local_no_update": bool(
                unknown_boot_transition_pairs
            ),
            "unknown_boot_interval_local_factor_disposition": "LOCAL_NO_UPDATE",
            "progressive_unknown_interval_floor_consumption_proven_in_this_owner": False,
            "progressive_unknown_interval_floor_requires_runtime_mutation": True,
            "elapsed_time_synthesized_from_block_count_rank_action_order_or_cap": False,
            "cross_gap_or_boot_elapsed_invented": False,
            "joint_to_sensor_vector_convention": True,
            "robust_loss": "SCIPY_LEAST_SQUARES_SOFT_L1",
            "robust_f_scale_standardized": robust_f_scale,
            "robust_scale_mps2": robust_sigma,
            "robust_scale_source": "ROWWISE_FEASIBLE_GLS_PROPAGATION_OF_IMMUTABLE_P1_ACC_GYRO_OBSERVATION_COVARIANCE_PLUS_REGISTERED_ACCELEROMETER_BIAS_DRIFT_AND_ACC_GYRO_INDEPENDENT_SHARED_SCALE_CROSS_AXIS_NUISANCES_PLUS_SAVGOL_ALPHA_AND_SIGNED_CLOCK_SENSITIVITY",
            "timing_uncertainty_contribution_mps2": timing_sigma,
            "rowwise_timing_variance_role": (
                "FEASIBLE_GLS_USES_SIGNED_FULL_CORRECTED_CHILD_RESIDUAL_DERIVATIVE_"
                "SQUARED_TIMES_PAIR_CLOCK_VARIANCE"
            ),
            "pair_clock_offset_convention": (
                "POSITIVE_OFFSET_EVALUATES_CHILD_LATER_AT_FIXED_PARENT_PHYSICAL_TIME"
            ),
            "systematic_clock_nuisance_jacobian": (
                "SIGNED_NEGATIVE_BLOCK_LOCAL_TIME_DERIVATIVE_OF_CORRECTED_CHILD_NORM"
            ),
            "unsigned_acceleration_norm_magnitude_used_as_systematic_clock_direction": False,
            "signed_clock_gradient_min_mps3": float(
                np.min(signed_clock_offset_gradient)
            ),
            "signed_clock_gradient_max_mps3": float(
                np.max(signed_clock_offset_gradient)
            ),
            "signed_clock_gradient_negative_row_count": int(np.count_nonzero(
                signed_clock_offset_gradient < 0.0
            )),
            "signed_clock_gradient_positive_row_count": int(np.count_nonzero(
                signed_clock_offset_gradient > 0.0
            )),
            "signed_clock_gradient_cross_block_or_gap_derivative_count": 0,
            "savgol_alpha_observation_noise_gain_s2_inv": (
                alpha_observation_noise_gain
            ),
            "savgol_derivative_coefficients_s_inv": derivative_coefficients.tolist(),
            "savgol_zeroth_order_coefficients": smoothing_coefficients.tolist(),
            "savgol_zeroth_order_white_noise_gain": smoothing_observation_noise_gain,
            "savgol_smoothing_spectral_variance_gain": (
                smoothing_spectral_variance_gain
            ),
            "savgol_derivative_spectral_variance_gain_s2_inv": (
                derivative_spectral_variance_gain
            ),
            "serial_correlation_variance_envelope_multiplier": (
                serial_correlation_variance_envelope_multiplier
            ),
            "smoothing_kernel_covariance_envelope_minimum_eigenvalue": (
                smoothing_envelope_minimum_eigenvalue
            ),
            "derivative_kernel_covariance_envelope_minimum_eigenvalue": (
                derivative_envelope_minimum_eigenvalue
            ),
            "kernel_covariance_envelope_psd_dominates": True,
            "matched_gap_local_acc_gyro_alpha_preprocessing": True,
            "p1_accelerometer_or_gyro_covariance_reduced_by_smoothing": False,
            "smoothing_induced_row_correlation_owned_by_complete_block_cluster_sandwich": True,
            "complete_raw_block_rows": center_block_rows,
            "retained_rows_per_independently_filtered_block": (
                retained_rows_per_complete_block
            ),
            "retained_to_raw_complete_block_row_ratio": (
                retained_rows_per_complete_block / center_block_rows
            ),
            "filter_half_window_guard_rows_each_block_side": endpoint,
            "cross_prefix_heldin_filter_support_rows": 0,
            "cross_complete_block_filter_support_rows": 0,
            "filtered_rows_counted_as_more_information_than_raw_rows": False,
            "feasible_gls_gyro_reweight_passes": reweight_passes,
            "selected_reweight_sigma_relative_fixed_point_change": float(
                selected_trial["sigma_relative_fixed_point_change"]
            ),
            "maximum_reweight_sigma_relative_rms_change": float(
                settings["maximum_reweight_sigma_relative_rms_change"]
            ),
            "selected_stochastic_residual_sigma_audit": dict(
                selected_trial["final_sigma_audit"]
            ),
            "accelerometer_unresolved_bias_sigma_mps2": (
                accelerometer_bias_sigma
            ),
            "accelerometer_bias_drift_rate_sigma_mps3": (
                accelerometer_bias_drift_rate
            ),
            "accelerometer_bias_drift_horizon_s": (
                accelerometer_bias_drift_horizon
            ),
            "parent_accelerometer_bias_drift_covariance_m2_s4": (
                parent_acc_bias_drift_covariance.tolist()
            ),
            "child_accelerometer_bias_drift_covariance_m2_s4": (
                child_acc_bias_drift_covariance.tolist()
            ),
            "accelerometer_scale_cross_axis_fraction_sigma": (
                accelerometer_scale_cross_sigma
            ),
            "gyro_scale_cross_axis_fraction_sigma": scale_cross_sigma,
            "accelerometer_gyro_shared_scale_cross_axis_fraction_sigma": (
                shared_scale_cross_sigma
            ),
            "accelerometer_bias_or_gravity_fitted_from_initial_still": False,
            "per_action_accelerometer_calibration_profile_used": False,
            "accelerometer_bias_residual_jacobian": (
                "SIGNED_PARENT_PLUS_UNIT_CHILD_MINUS_UNIT"
            ),
            "accelerometer_scale_cross_axis_residual_jacobian": (
                "SIGNED_OUTER_CORRECTED_FORCE_UNIT_WITH_OBSERVED_SPECIFIC_FORCE"
            ),
            "accelerometer_gyro_scale_cross_axis_correlation_model": (
                "INDEPENDENT_ACC_AND_GYRO_3X3_BLOCKS_PLUS_EXPLICIT_SHARED_3X3_BLOCK"
            ),
            "gyro_bias_drift_correlation_time_s": bias_drift_horizon,
            "parent_gyro_observation_covariance_rad2_s2": (
                parent_gyro_observation_covariance.tolist()
            ),
            "child_gyro_observation_covariance_rad2_s2": (
                child_gyro_observation_covariance.tolist()
            ),
            "parent_gyro_bias_covariance_rad2_s2": (
                parent_gyro_bias_covariance.tolist()
            ),
            "child_gyro_bias_covariance_rad2_s2": (
                child_gyro_bias_covariance.tolist()
            ),
            "coordinate_bound_m": bound,
            "bound_is_numerical_guard_not_anatomical_truth": True,
            "solver_success": bool(result.success),
            "solver_status": int(result.status),
            "solver_message": str(result.message),
            "function_evaluations": int(result.nfev),
            "multistart_seed": int(settings["multistart_seed"]),
            "random_multistarts": random_multistarts,
            "total_multistarts_including_zero": len(trials),
            "multistart_initial_fraction_of_coordinate_bound": float(
                settings["multistart_initial_fraction_of_coordinate_bound"]
            ),
            "minimum_interior_multistarts_for_update": int(
                settings["minimum_legal_multistarts_for_update"]
            ),
            "interior_multistart_count": len(multistart_assessment["interior"]),
            "primary_interior_cluster_count": len(primary_cluster),
            "consistent_interior_start_fraction": float(
                multistart_assessment["consistent_interior_fraction"]
            ),
            "minimum_consistent_interior_start_fraction": float(
                settings["minimum_consistent_interior_start_fraction"]
            ),
            "interior_basin_radius_m": float(settings["interior_basin_radius_m"]),
            "boundary_candidate_competitive": bool(
                multistart_assessment["boundary_competitive"]
            ),
            "remote_interior_basin_competitive": bool(
                multistart_assessment["remote_basin_competitive"]
            ),
            "multistart_basin_identifiable": bool(
                multistart_assessment["basin_identifiable"]
            ),
            "competitive_normalized_cost_relative_tolerance": float(
                settings["competitive_normalized_cost_relative_tolerance"]
            ),
            "competitive_normalized_cost_absolute_tolerance": float(
                settings["competitive_normalized_cost_absolute_tolerance"]
            ),
            "selected_multistart_index": int(selected_trial["start_index"]),
            "competing_boundary_or_remote_basin_causes_local_no_update": True,
            "interior_candidate_selected_after_discarding_competing_boundary": False,
            "multistart_trials": [{
                "start_index": int(row["start_index"]),
                "start_m": row["start"].tolist(),
                "solution_m": row["result"].x.tolist(),
                "cost": float(row["result"].cost),
                "normalized_cost": float(row["normalized_cost"]),
                "solver_success": bool(row["result"].success),
                "boundary_guard_active": bool(row["boundary_guard_active"]),
                "numerical_interior": bool(row["interior"]),
                "function_evaluations": int(row["result"].nfev),
                "sigma_relative_fixed_point_change": float(
                    row["sigma_relative_fixed_point_change"]
                ),
            } for row in trials],
            "jacobian_singular_values": singular.tolist(),
            "gauge_reduced_rank": rank,
            "gauge_reduced_robust_bread_information_m2_inv": gauge_reduced_information.tolist(),
            "gauge_reduced_robust_bread_nonzero_eigenvalues_m2_inv": information_eigenvalues[information_keep].tolist(),
            "gauge_reduced_robust_bread_informed_basis": informed_basis.tolist(),
            "gauge_reduced_information_excludes_model_floor": True,
            "cluster_robust_complete_block_count": int(len(cluster_values)),
            "cluster_robust_meat_uses_rows_as_independent_samples": False,
            "condition_number": float(singular[0] / max(singular[-1], np.finfo(float).eps)),
            "maximum_robust_bread_condition_number": float(
                settings["maximum_robust_bread_condition_number"]
            ),
            "minimum_informed_information_eigenvalue_m2_inv": (
                minimum_informed_information_eigenvalue
            ),
            "required_minimum_informed_information_eigenvalue_m2_inv": float(
                settings["minimum_informed_information_eigenvalue_m2_inv"]
            ),
            "maximum_informed_observation_sigma_m": maximum_informed_observation_sigma,
            "required_maximum_informed_observation_sigma_m": float(
                settings["maximum_informed_observation_sigma_m"]
            ),
            "information_condition_eligible": information_condition_eligible,
            "chronological_prefix_heldin_stability": prefix_stability,
            "pre_coherent_nuisance_refit_owner_update_eligible": (
                pre_coherent_refit_owner_update_eligible
            ),
            "online_candidate_owner_update_eligible": (
                online_candidate_owner_update_eligible
            ),
            "coherent_nuisance_refit_audit": coherent_nuisance_refit_audit,
            "coherent_nuisance_refit_required_before_owner_update": (
                not online_owner_enabled
            ),
            "full_coherent_nuisance_refits_online": (
                not online_owner_enabled
            ),
            "full_coherent_nuisance_refits_offline_final_validation_pending": (
                online_owner_enabled
            ),
            "online_branch_posterior_owner_enabled": online_owner_enabled,
            "online_candidate_branches": online_candidate_branches,
            "online_candidate_weight_sum": float(sum(
                row["weight"] for row in online_candidate_branches
            )),
            "online_candidate_moment_mean_m": (
                None
                if online_mixture["candidate_mean_m"] is None
                else np.asarray(
                    online_mixture["candidate_mean_m"], dtype=float,
                ).tolist()
            ),
            "online_between_candidate_covariance_m2": (
                online_between_candidate_covariance.tolist()
            ),
            "online_incomplete_nuisance_covariance_m2": (
                online_incomplete_nuisance_covariance.tolist()
            ),
            "incomplete_nuisance_erased_finite_candidate": False,
            "nonfinite_or_boundary_rows_retained_as_online_branches": (
                online_mixture[
                    "nonfinite_or_boundary_rows_retained_as_online_branches"
                ]
            ),
            "physical_topology_gate_bypassed": False,
            "first_order_j_c_jt_coverage_alone_qualifies_nominal_mean": False,
            "boundary_guard_active": at_guard,
            "covariance_shape": list(covariance.shape),
            "covariance_units": "m^2",
            "sandwich_covariance_units": "m^2_FROM_DIMENSIONLESS_STANDARDIZED_RESIDUAL_AND_1_PER_M_JACOBIAN",
            "standardized_covariance_multiplied_by_robust_sigma_squared": False,
            "sandwich_covariance_m2": sandwich_covariance.tolist(),
            "multistart_primary_interior_cluster_covariance_m2": multistart_covariance.tolist(),
            "informed_observation_covariance_m2": informed_observation_covariance.tolist(),
            "nullspace_prior_sigma_m": nullspace_sigma,
            "nullspace_covariance_m2": nullspace_covariance.tolist(),
            "statistical_covariance_including_nullspace_prior_m2": statistical_covariance.tolist(),
            "human_worn_model_floor_m": model_floor,
            "accelerometer_bias_drift_systematic_covariance_m2": (
                accelerometer_bias_drift_systematic_covariance.tolist()
            ),
            "accelerometer_scale_cross_axis_systematic_covariance_m2": (
                accelerometer_scale_cross_systematic_covariance.tolist()
            ),
            "gyro_bias_systematic_covariance_m2": gyro_bias_systematic_covariance.tolist(),
            "gyro_bias_drift_systematic_covariance_m2": (
                gyro_bias_drift_systematic_covariance.tolist()
            ),
            "gyro_scale_cross_axis_systematic_covariance_m2": (
                gyro_scale_cross_systematic_covariance.tolist()
            ),
            "accelerometer_gyro_shared_scale_cross_axis_systematic_covariance_m2": (
                accelerometer_gyro_shared_scale_cross_systematic_covariance.tolist()
            ),
            "persistent_clock_systematic_covariance_m2": (
                clock_systematic_covariance.tolist()
            ),
            "sensor_calibration_clock_systematic_covariance_m2": (
                sensor_calibration_clock_systematic_covariance.tolist()
            ),
            "human_worn_systematic_covariance_m2": (
                human_worn_systematic_covariance.tolist()
            ),
            "total_systematic_covariance_m2": systematic_covariance.tolist(),
            "shared_accelerometer_gyro_bias_drift_scale_cross_axis_and_clock_shrink_as_episode_information": False,
            "stochastic_residual_variance_partition": {
                "independent_observation_and_derivative_noise": (
                    "WHITE_SOURCE_ONLY;FILTER_KERNEL_AND_SERIAL_CORRELATION_"
                    "ENVELOPE_APPLIED"
                ),
                "shared_calibration_nuisance": (
                    "SIGNED_J_C_JT_DIAGONAL_MARGINAL_FOR_ROBUST_STANDARDIZATION_"
                    "ONLY;NO_WHITE_NOISE_MULTIPLIER_OR_FILTER_ENVELOPE"
                ),
                "shared_calibration_posterior_ownership": (
                    "LOW_RANK_SIGNED_NUISANCE_PUSHFORWARD;NONSHRINKING;NOT_"
                    "REPEATABLE_EPISODE_INFORMATION"
                ),
            },
            "fixed_linearization_gyro_covariance_sensitivity": (
                fixed_linearization_sensitivity
            ),
            "fixed_linearization_accelerometer_calibration_covariance_sensitivity": (
                fixed_linearization_accelerometer_calibration_sensitivity
            ),
            "fixed_linearization_sensitivity_role": (
                "INPUT_UNCERTAINTY_MONOTONICITY_AT_ONE_OWNER_SELECTED_POINT;"
                "CROSS_REFIT_POINT_DRIFT_REMAINS_SEPARATE_DIAGNOSTIC"
            ),
            "covariance_derivation": "SOFT_L1_FEASIBLE_GLS_ROWWISE_P1_ACC_GYRO_ALPHA_CLOCK_WEIGHTING_PLUS_REGISTERED_ACCELEROMETER_BIAS_DRIFT_AND_ACC_GYRO_INDEPENDENT_SHARED_SCALE_CROSS_AXIS_WITH_COMPLETE_BLOCK_CLUSTER_SANDWICH_PLUS_RESULT_INDEPENDENT_PRIMARY_INTERIOR_BASIN_SPREAD_PLUS_BROAD_NULLSPACE_PRIOR_PLUS_NONSHRINKING_SENSOR_CALIBRATION_CLOCK_AND_HUMAN_WORN_SYSTEMATIC_PUSHFORWARD",
            "owner_update_mode": (
                (
                    "ONLINE_WEIGHTED_CANDIDATE_BRANCH_POSTERIOR_WITH_"
                    "MARGINALIZED_INCOMPLETE_NUISANCE"
                )
                if owner_update_eligible and online_owner_enabled
                else "GAUGE_REDUCED_ROBUST_BREAD_INFORMED_SUBSPACE"
                if owner_update_eligible
                else (
                    "LOCAL_NO_UPDATE_COHERENT_NUISANCE_REFIT_FRAGILITY"
                    if pre_coherent_refit_owner_update_eligible
                    and not coherent_refit_pass
                    else "LOCAL_NO_UPDATE_SOLVER_BOUNDARY_OR_BASIN_COMPETITION_INFORMATION_MAGNITUDE_OR_PREFIX_STABILITY"
                )
            ),
            "owner_update_eligible": owner_update_eligible,
            "residual_rms_mps2": float(np.sqrt(np.mean(raw_residual**2))),
            "residual_median_abs_mps2": float(np.median(np.abs(raw_residual))),
            "status": status,
            "timing": timing_reports,
            "ordinary_soft_tissue_axis_or_center_migration_terminates_task": False,
    }
    validate_center_covariance_contract(covariance, report)
    return CenterEstimate(
        edge=edge,
        parent=parent,
        child=child,
        joint_to_parent_sensor_m=result.x[:3].copy(),
        joint_to_child_sensor_m=result.x[3:].copy(),
        covariance_m2=covariance,
        report=report,
    )


def hinge_sign_branch_descriptors(
    edges: Sequence[str] = HINGE_EDGES,
) -> tuple[dict[str, Any], ...]:
    """Return the deterministic sign-branch identity/order from the rooted hinge order."""

    keys = tuple(str(edge) for edge in edges)
    if len(set(keys)) != len(keys) or any(edge not in HINGE_EDGES for edge in keys):
        raise ValueError("hinge sign branch edges must be a unique subset of the canonical hinge order")
    output: list[dict[str, Any]] = []
    for mask in range(1 << len(keys)):
        signs = {edge: (-1 if (mask >> index) & 1 else 1) for index, edge in enumerate(keys)}
        output.append({
            "branch_id": "HINGE_SIGN_" + "_".join(
                f"{edge}:{'neg' if signs[edge] < 0 else 'pos'}" for edge in keys
            ),
            "axis_sign_by_edge": signs,
            "prior_weight": 1.0 / max(1, 1 << len(keys)),
            "wear_prior": "BROAD_NON_COMPACT_QUALITATIVE;NO_NUMERIC_CONE",
            "retained": True,
        })
    return tuple(output)


def canonical_hinge_sign_branch_ids() -> tuple[str, ...]:
    return tuple(str(row["branch_id"]) for row in hinge_sign_branch_descriptors())


def enumerate_hinge_sign_branches(
    axes: Mapping[str, AxisEstimate],
    *,
    execution_guard: C2ExecutionGuard,
    register_initial_weights: bool = True,
) -> list[dict[str, Any]]:
    """Retain the simultaneous endpoint sign ambiguity for every hinge."""

    keys = [edge for edge in HINGE_EDGES if edge in axes]
    output = [dict(row) for row in hinge_sign_branch_descriptors(keys)]
    execution_guard.validate_wear_prior({
        "family": "BROAD_NON_COMPACT_QUALITATIVE",
        "hard_cone_deg": None,
    })
    if register_initial_weights:
        execution_guard.update_branch_weights(
            [float(row["prior_weight"]) for row in output], lock_requested=False,
        )
    return output
