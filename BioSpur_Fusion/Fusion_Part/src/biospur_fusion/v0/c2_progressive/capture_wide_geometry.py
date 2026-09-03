"""Capture-wide information accounting for functional calibration factors.

This module is a bounded saved-array prototype, not a replacement nonlinear
solver.  It exposes whether every gap-safe action/edge cell can add lawful
information to one shared state without using action labels as a routing or
pose oracle.  The matrices use the production gap-local Savitzky-Golay center
preprocessing and fixed unit parameter/residual scales; they are excitation
and rank diagnostics, not qualified posterior covariance.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.signal import savgol_filter

from .functional_geometry import EDGE_SPECS, HINGE_EDGES, _center_terms


SEGMENTS = tuple(dict.fromkeys(name for _, parent, child in EDGE_SPECS for name in (parent, child)))
EDGE_ENDPOINTS = {edge: (parent, child) for edge, parent, child in EDGE_SPECS}


def _array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return sha256(array.view(np.uint8)).hexdigest()


def _rank_spectrum(matrix: np.ndarray) -> tuple[int, np.ndarray]:
    values = np.linalg.eigvalsh(0.5 * (matrix + matrix.T))[::-1]
    values = np.maximum(values, 0.0)
    if len(values) == 0 or values[0] == 0.0:
        return 0, values
    tolerance = values[0] * max(matrix.shape) * np.finfo(float).eps
    return int(np.count_nonzero(values > tolerance)), values


def _logdet_gain(matrix: np.ndarray) -> float:
    sign, value = np.linalg.slogdet(np.eye(len(matrix)) + matrix)
    if sign <= 0:
        raise ValueError("capture-wide information matrix is not PSD")
    return float(value)


@dataclass(frozen=True)
class CaptureWidePairContribution:
    action_index: int
    action: str
    edge: str
    usable_rows: int
    contiguous_spans: tuple[tuple[int, int], ...]
    complete_blocks: int
    retained_rows: int
    center_information: np.ndarray
    center_score: np.ndarray
    center_residual_quadratic: float
    relative_extrinsic_information: np.ndarray
    relative_extrinsic_score: np.ndarray
    relative_extrinsic_residual_quadratic: float
    hinge_axis_information: np.ndarray | None
    heading_information: float
    report: Mapping[str, Any]


def build_capture_wide_pair_contribution(
    *,
    action_index: int,
    action: str,
    edge: str,
    parent_acc: np.ndarray,
    child_acc: np.ndarray,
    parent_gyro: np.ndarray,
    child_gyro: np.ndarray,
    parent_quat_world_sensor_wxyz: np.ndarray,
    child_quat_world_sensor_wxyz: np.ndarray,
    contiguous_spans: Sequence[tuple[int, int]],
    sample_period_s: float,
    savgol_window_samples: int,
    savgol_polynomial: int,
    selection_block_rows: int,
) -> CaptureWidePairContribution:
    """Build result-independent information diagnostics for one action/edge."""

    arrays = tuple(np.asarray(value, dtype=float) for value in (
        parent_acc, child_acc, parent_gyro, child_gyro,
    ))
    quaternions = tuple(np.asarray(value, dtype=float) for value in (
        parent_quat_world_sensor_wxyz, child_quat_world_sensor_wxyz,
    ))
    count = len(arrays[0])
    if (
        edge not in EDGE_ENDPOINTS
        or any(value.shape != (count, 3) or not np.isfinite(value).all() for value in arrays)
        or any(value.shape != (count, 4) or not np.isfinite(value).all() for value in quaternions)
        or sample_period_s <= 0.0
        or savgol_window_samples < 3
        or savgol_window_samples % 2 != 1
        or selection_block_rows < savgol_window_samples
    ):
        raise ValueError("capture-wide pair contribution input is invalid")
    spans = tuple((int(start), int(stop)) for start, stop in contiguous_spans)
    if any(not 0 <= start < stop <= count for start, stop in spans):
        raise ValueError("capture-wide pair spans are invalid")
    center_information = np.zeros((6, 6), dtype=float)
    center_score = np.zeros(6, dtype=float)
    center_residual_quadratic = 0.0
    extrinsic_information = np.zeros((6, 6), dtype=float)
    extrinsic_score = np.zeros(6, dtype=float)
    extrinsic_residual_quadratic = 0.0
    hinge_information = np.zeros((4, 4), dtype=float) if edge in HINGE_EDGES else None
    heading_information = 0.0
    retained_rows = 0
    complete_blocks = 0
    endpoint = savgol_window_samples // 2
    block_rows: list[Mapping[str, Any]] = []
    for span_index, (span_start, span_stop) in enumerate(spans):
        for block_start in range(span_start, span_stop, selection_block_rows):
            block_stop = block_start + selection_block_rows
            if block_stop > span_stop:
                continue
            current = slice(block_start, block_stop)
            parent_terms, _, parent_gyro_smooth = _center_terms(
                arrays[2][current], dt=sample_period_s,
                window=savgol_window_samples, polynomial=savgol_polynomial,
            )
            child_terms, _, child_gyro_smooth = _center_terms(
                arrays[3][current], dt=sample_period_s,
                window=savgol_window_samples, polynomial=savgol_polynomial,
            )
            parent_acc_smooth = savgol_filter(
                arrays[0][current], window_length=savgol_window_samples,
                polyorder=savgol_polynomial, deriv=0, axis=0, mode="interp",
            )
            child_acc_smooth = savgol_filter(
                arrays[1][current], window_length=savgol_window_samples,
                polyorder=savgol_polynomial, deriv=0, axis=0, mode="interp",
            )
            selected = slice(endpoint, selection_block_rows - endpoint)
            pa = parent_acc_smooth[selected]
            ca = child_acc_smooth[selected]
            pt = parent_terms[selected]
            ct = child_terms[selected]
            pg = parent_gyro_smooth[selected]
            cg = child_gyro_smooth[selected]
            rows = len(pa)
            parent_unit = pa / np.maximum(np.linalg.norm(pa, axis=1, keepdims=True), 1e-12)
            child_unit = ca / np.maximum(np.linalg.norm(ca, axis=1, keepdims=True), 1e-12)
            center_jacobian = np.hstack((
                np.einsum("ni,nij->nj", parent_unit, pt),
                -np.einsum("ni,nij->nj", child_unit, ct),
            ))
            center_residual = np.linalg.norm(pa, axis=1) - np.linalg.norm(ca, axis=1)
            center_information += center_jacobian.T @ center_jacobian / float(rows)
            center_score += center_jacobian.T @ center_residual / float(rows)
            center_residual_quadratic += float(
                center_residual @ center_residual / float(rows)
            )

            parent_centered = pa - np.median(pa, axis=0)
            child_centered = ca - np.median(ca, axis=0)
            def skew_rows(value: np.ndarray) -> np.ndarray:
                output = np.zeros((len(value), 3, 3), dtype=float)
                output[:, 0, 1] = -value[:, 2]
                output[:, 0, 2] = value[:, 1]
                output[:, 1, 0] = value[:, 2]
                output[:, 1, 2] = -value[:, 0]
                output[:, 2, 0] = -value[:, 1]
                output[:, 2, 1] = value[:, 0]
                return output
            extrinsic_jacobian = np.concatenate((
                -skew_rows(parent_centered), skew_rows(child_centered),
            ), axis=2)
            extrinsic_information += (
                np.einsum("nki,nkj->ij", extrinsic_jacobian, extrinsic_jacobian)
                / float(rows)
            )
            extrinsic_residual = parent_centered - child_centered
            extrinsic_score += np.einsum(
                "nki,nk->i", extrinsic_jacobian, extrinsic_residual,
            ) / float(rows)
            extrinsic_residual_quadratic += float(
                np.sum(extrinsic_residual**2) / float(rows)
            )
            if hinge_information is not None:
                pg_centered = pg - np.median(pg, axis=0)
                cg_centered = cg - np.median(cg, axis=0)
                hinge_jacobian = np.column_stack((
                    pg_centered[:, 0], pg_centered[:, 2],
                    cg_centered[:, 0], cg_centered[:, 2],
                ))
                hinge_information += hinge_jacobian.T @ hinge_jacobian / float(rows)

            # Candidate-independent horizontal excitation: a yaw perturbation
            # changes a vector by ez cross vector.  This is sensitivity only;
            # it is not a residual likelihood or a heading point estimate.
            horizontal = child_centered[:, :2]
            heading_information += float(np.mean(np.sum(horizontal**2, axis=1)))
            retained_rows += rows
            complete_blocks += 1
            block_rows.append({
                "span_index": span_index,
                "half_open_rows": [block_start, block_stop],
                "retained_half_open_rows": [block_start + endpoint, block_stop - endpoint],
                "center_jacobian_sha256": _array_sha256(center_jacobian),
                "relative_extrinsic_jacobian_sha256": _array_sha256(extrinsic_jacobian),
            })
    center_rank, center_spectrum = _rank_spectrum(center_information)
    extrinsic_rank, extrinsic_spectrum = _rank_spectrum(extrinsic_information)
    if hinge_information is None:
        hinge_rank, hinge_spectrum = 0, np.empty(0, dtype=float)
    else:
        hinge_rank, hinge_spectrum = _rank_spectrum(hinge_information)
    usable_rows = int(sum(stop - start for start, stop in spans))
    return CaptureWidePairContribution(
        action_index=int(action_index), action=str(action), edge=str(edge),
        usable_rows=usable_rows, contiguous_spans=spans,
        complete_blocks=complete_blocks, retained_rows=retained_rows,
        center_information=center_information,
        center_score=center_score,
        center_residual_quadratic=float(center_residual_quadratic),
        relative_extrinsic_information=extrinsic_information,
        relative_extrinsic_score=extrinsic_score,
        relative_extrinsic_residual_quadratic=float(extrinsic_residual_quadratic),
        hinge_axis_information=hinge_information,
        heading_information=float(heading_information),
        report={
            "schema": "biospur-c2-capture-wide-action-edge-information-v1",
            "action_index": int(action_index),
            "action": str(action),
            "edge": str(edge),
            "action_label_used_as_pose_truth_or_factor_route": False,
            "usable_gap_safe_rows": usable_rows,
            "contiguous_spans_half_open": [list(value) for value in spans],
            "complete_gap_safe_blocks": complete_blocks,
            "retained_preprocessed_rows": retained_rows,
            "center_information_rank": center_rank,
            "center_information_eigenvalues_desc": center_spectrum.tolist(),
            "center_incremental_logdet_gain_unit_scaled": _logdet_gain(center_information),
            "center_jt_w_j_sha256": _array_sha256(center_information),
            "center_jt_w_r": center_score.tolist(),
            "center_jt_w_r_sha256": _array_sha256(center_score),
            "center_r_t_w_r": float(center_residual_quadratic),
            "center_unit_scale": "1_METER_PARAMETER_AND_1_MPS2_RESIDUAL_DIAGNOSTIC_ONLY",
            "relative_extrinsic_information_rank": extrinsic_rank,
            "relative_extrinsic_information_eigenvalues_desc": extrinsic_spectrum.tolist(),
            "relative_extrinsic_incremental_logdet_gain_unit_scaled": _logdet_gain(
                extrinsic_information
            ),
            "relative_extrinsic_jt_w_j_sha256": _array_sha256(extrinsic_information),
            "relative_extrinsic_jt_w_r": extrinsic_score.tolist(),
            "relative_extrinsic_jt_w_r_sha256": _array_sha256(extrinsic_score),
            "relative_extrinsic_r_t_w_r": float(extrinsic_residual_quadratic),
            "relative_extrinsic_unit_scale": "1_RAD_PARAMETER_AND_1_MPS2_RESIDUAL_DIAGNOSTIC_ONLY",
            "hinge_axis_information_rank": hinge_rank,
            "hinge_axis_information_eigenvalues_desc": hinge_spectrum.tolist(),
            "heading_horizontal_excitation_unit_scaled": float(heading_information),
            "affected_shared_blocks": {
                "full_r3_connection": [f"center/{edge}/parent", f"center/{edge}/child"],
                "sensor_to_segment_extrinsic": list(EDGE_ENDPOINTS[edge]),
                "hinge_axis": [edge] if edge in HINGE_EDGES else [],
                "heading": [edge],
                "rooted_tree_closure_incident": True,
            },
            "no_update_cause": (
                None if complete_blocks > 0
                else "NO_COMPLETE_GAP_SAFE_PRODUCTION_CENTER_BLOCK"
            ),
            "low_information_is_zero_or_small_matrix_not_action_exclusion": True,
            "block_rows": block_rows,
            "qualified_posterior_covariance_or_pose_solution": False,
        },
    )


class CaptureWideInformationPrototype:
    """One chronological shared information state for all action/edge cells."""

    def __init__(self) -> None:
        center_width = 6 * len(EDGE_SPECS)
        extrinsic_width = 3 * len(SEGMENTS)
        hinge_width = 4 * len(HINGE_EDGES)
        heading_width = len(EDGE_SPECS)
        self._offsets = {
            "center": 0,
            "extrinsic": center_width,
            "hinge": center_width + extrinsic_width,
            "heading": center_width + extrinsic_width + hinge_width,
        }
        self._width = center_width + extrinsic_width + hinge_width + heading_width
        self._information = np.eye(self._width, dtype=float)
        self._last_action_index = -1
        self._prefix: list[Mapping[str, Any]] = []

    def _add_block(self, indices: list[int], value: np.ndarray) -> None:
        self._information[np.ix_(indices, indices)] += np.asarray(value, dtype=float)

    def ingest_action(
        self, *, action_index: int, action: str,
        contributions: Sequence[CaptureWidePairContribution],
    ) -> Mapping[str, Any]:
        if int(action_index) != self._last_action_index + 1:
            raise ValueError("capture-wide prototype requires exact chronological actions")
        by_edge = {row.edge: row for row in contributions}
        if set(by_edge) != set(EDGE_ENDPOINTS):
            raise ValueError("capture-wide prototype requires all nine edge cells per action")
        before_logdet = float(np.linalg.slogdet(self._information)[1])
        before_trace = float(np.trace(np.linalg.inv(self._information)))
        for edge_index, (edge, parent, child) in enumerate(EDGE_SPECS):
            row = by_edge[edge]
            center_start = self._offsets["center"] + 6 * edge_index
            self._add_block(list(range(center_start, center_start + 6)), row.center_information)
            parent_index = SEGMENTS.index(parent)
            child_index = SEGMENTS.index(child)
            extrinsic_indices = [
                self._offsets["extrinsic"] + 3 * parent_index + axis for axis in range(3)
            ] + [
                self._offsets["extrinsic"] + 3 * child_index + axis for axis in range(3)
            ]
            self._add_block(extrinsic_indices, row.relative_extrinsic_information)
            if row.hinge_axis_information is not None:
                hinge_start = self._offsets["hinge"] + 4 * HINGE_EDGES.index(edge)
                self._add_block(
                    list(range(hinge_start, hinge_start + 4)),
                    row.hinge_axis_information,
                )
            heading_index = self._offsets["heading"] + edge_index
            self._information[heading_index, heading_index] += row.heading_information
        after_logdet = float(np.linalg.slogdet(self._information)[1])
        after_trace = float(np.trace(np.linalg.inv(self._information)))
        snapshot = {
            "schema": "biospur-c2-capture-wide-information-prefix-v1",
            "action_index": int(action_index),
            "action": str(action),
            "all_nine_edges_considered": True,
            "action_label_used_as_route": False,
            "incremental_logdet_gain": after_logdet - before_logdet,
            "uncertainty_trace_before": before_trace,
            "uncertainty_trace_after": after_trace,
            "uncertainty_trace_reduction": before_trace - after_trace,
            "information_sha256": _array_sha256(self._information),
        }
        self._last_action_index = int(action_index)
        self._prefix.append(snapshot)
        return snapshot

    @property
    def information(self) -> np.ndarray:
        return self._information.copy()

    @property
    def prefix_snapshots(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._prefix)
