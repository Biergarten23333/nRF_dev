"""Capture-wide C2 coupled-progressive estimator.

The estimator has three explicit owners:

* alignment tape: reconstructed from sealed metadata and whitelisted
  orientation arrays only;
* factor tape: state-independent local evidence, including complete QMT
  heading streams;
* posterior consumers: progressive and fresh-batch paths with separate loops.

Episode labels are never passed to factor selection logic. They are stored for
chronology and QA reports only.
"""

from __future__ import annotations

import contextlib
import io
import math
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import qmt
from qmt.functions.heading_correction import estimateDelta1d
from scipy.optimize import least_squares
from scipy.signal import savgol_filter
from scipy.spatial.transform import Rotation

from .contracts import EDGES, HINGE_EDGES, SEGMENT_TO_NODE, load_effective_config
from .frontend import EpisodeFrontend, NodeSeries, VerifiedFrontendArchive
from .math_utils import (
    array_binding,
    interp_quat_wxyz,
    mean_rotation_matrix,
    normalize_quat_wxyz,
    qmt_wxyz_to_rotation,
    rotation_to_qmt_wxyz,
    semantic_sha,
    skew,
    unit,
    wrap_pi,
)


SEGMENTS = tuple(SEGMENT_TO_NODE)
EDGE_BY_NAME = {edge.name: edge for edge in EDGES}
HINGE_NAMES = tuple(edge.name for edge in HINGE_EDGES)
TREE_CHILDREN = {
    "pelvis": ("torso", "thigh_left", "thigh_right"),
    "torso": ("upper_arm_left", "upper_arm_right"),
    "upper_arm_left": ("forearm_left",),
    "upper_arm_right": ("forearm_right",),
    "thigh_left": ("shank_left",),
    "thigh_right": ("shank_right",),
}
SEGMENT_EDGE = {edge.child: edge.name for edge in EDGES}
EXPECTED_STEP_US = 5000


@dataclass(frozen=True)
class AlignedSpan:
    episode_index: int
    qa_label: str
    edge: str
    span_index: int
    parent_segment: str
    child_segment: str
    parent_indices: np.ndarray
    child_indices: np.ndarray
    time_root_s: np.ndarray
    parent_time_s: np.ndarray
    child_time_s: np.ndarray
    parent_acc_mps2: np.ndarray
    child_acc_mps2: np.ndarray
    parent_gyro_rads: np.ndarray
    child_gyro_rads: np.ndarray
    parent_quat_wxyz: np.ndarray
    child_quat_wxyz: np.ndarray
    lag_uncertainty_s: float
    alignment_status: str
    timing_audit: dict[str, Any]


@dataclass(frozen=True)
class WindowFactor:
    episode_index: int
    edge: str
    span_index: int
    start: int
    stop: int
    weight: float
    rel_gyro_trace: float
    dyn_acc_trace: float
    valid_fraction: float


@dataclass(frozen=True)
class HingeAxisFactor:
    episode_index: int
    qa_label: str
    edge: str
    span_index: int
    status: str
    wall_s: float
    samples: int
    parent_axis_sensor: np.ndarray
    child_axis_sensor: np.ndarray
    information: float
    uncertainty_rad2: float
    failure: str | None


@dataclass(frozen=True)
class CenterFactor:
    episode_index: int
    qa_label: str
    edge: str
    span_index: int
    status: str
    wall_s: float
    rows: int
    rank: int
    information: float
    residual_rms_mps2: float
    parent_vector_sensor_m: np.ndarray
    child_vector_sensor_m: np.ndarray
    covariance_diag_m2: np.ndarray
    physical_gate: str


@dataclass(frozen=True)
class HeadingStreamFactor:
    episode_index: int
    qa_label: str
    edge: str
    span_index: int
    status: str
    wall_s: float
    samples: int
    time_root_s: np.ndarray
    quat2corr_child_sensor_wxyz: np.ndarray
    delta_filt_rad: np.ndarray
    rating: np.ndarray
    qmt_state: np.ndarray
    information: float
    mean_delta_rad: float
    variance_rad2: float
    joint_source: str
    joint_uncertainty_rad2: float
    failure: str | None


@dataclass(frozen=True)
class EpisodeFactorBlock:
    episode_index: int
    qa_label: str
    spans: tuple[AlignedSpan, ...]
    windows: tuple[WindowFactor, ...]
    hinge_axes: tuple[HingeAxisFactor, ...]
    centers: tuple[CenterFactor, ...]
    headings: tuple[HeadingStreamFactor, ...]


@dataclass(frozen=True)
class FactorTape:
    schema: str
    created_wall_s: float
    episodes: tuple[EpisodeFactorBlock, ...]
    alignment_audit: dict[str, Any]
    qmt_settings: dict[str, Any]

    def all_headings(self) -> Iterable[HeadingStreamFactor]:
        for block in self.episodes:
            yield from block.headings


@dataclass
class HingePosterior:
    parent_outer: np.ndarray = field(default_factory=lambda: np.zeros((3, 3), dtype=float))
    child_outer: np.ndarray = field(default_factory=lambda: np.zeros((3, 3), dtype=float))
    information: float = 0.0
    observation_count: int = 0
    branch_logit: float = 0.0

    def add(self, factor: HingeAxisFactor) -> None:
        if factor.information <= 0.0:
            return
        p = unit(factor.parent_axis_sensor)
        c = unit(factor.child_axis_sensor)
        info = float(factor.information)
        self.parent_outer += info * np.outer(p, p)
        self.child_outer += info * np.outer(c, c)
        self.information += info
        self.observation_count += 1
        self.branch_logit += float(np.clip(info, 0.0, 4.0) * np.dot(p, _broad_axis_hint(factor.edge)))

    @staticmethod
    def _principal(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        evals, evecs = np.linalg.eigh(matrix + 1e-9 * np.eye(3))
        order = np.argsort(evals)[::-1]
        return unit(evecs[:, order[0]]), evals[order]

    def axes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        p, pe = self._principal(self.parent_outer)
        c, ce = self._principal(self.child_outer)
        return p, c, pe, ce

    def sign_probability(self) -> float:
        if self.information <= 0.0:
            return 0.5
        return float(1.0 / (1.0 + math.exp(-np.clip(0.1 * self.branch_logit, -30.0, 30.0))))


@dataclass
class HeadingPosterior:
    sin_sum: float = 0.0
    cos_sum: float = 0.0
    information: float = 0.0
    observation_count: int = 0

    def mean(self) -> float:
        if self.information <= 0.0:
            return 0.0
        return float(math.atan2(self.sin_sum, self.cos_sum))

    def variance(self) -> float:
        return float(1.0 / max(self.information, 1e-6))

    def add(self, factor: HeadingStreamFactor) -> None:
        if factor.information <= 0.0:
            return
        info = float(factor.information)
        self.sin_sum += info * math.sin(factor.mean_delta_rad)
        self.cos_sum += info * math.cos(factor.mean_delta_rad)
        self.information += info
        self.observation_count += 1


@dataclass
class CenterPosterior:
    hessian: np.ndarray = field(default_factory=lambda: np.zeros((6, 6), dtype=float))
    gradient: np.ndarray = field(default_factory=lambda: np.zeros(6, dtype=float))
    information: float = 0.0
    observation_count: int = 0
    residual_information_sum: float = 0.0
    residual_information_weight: float = 0.0

    def add(self, factor: CenterFactor) -> None:
        if factor.information <= 0.0 or factor.status not in {"PASS", "LOW_RANK"}:
            return
        x = np.r_[factor.parent_vector_sensor_m, factor.child_vector_sensor_m]
        precision = np.diag(1.0 / np.maximum(factor.covariance_diag_m2, 1e-5))
        info = float(factor.information)
        self.hessian += info * precision
        self.gradient += info * precision @ x
        self.information += info
        self.observation_count += 1
        if np.isfinite(factor.residual_rms_mps2):
            self.residual_information_sum += info * float(factor.residual_rms_mps2)
            self.residual_information_weight += info

    def residual_rms_mps2(self) -> float:
        if self.residual_information_weight <= 0.0:
            return np.inf
        return float(self.residual_information_sum / self.residual_information_weight)

    def solve(self) -> tuple[np.ndarray, np.ndarray, int, np.ndarray]:
        if self.information <= 0.0:
            return np.zeros(3), np.zeros(3), 0, np.full(6, np.inf)
        cov = np.linalg.pinv(self.hessian + 1e-6 * np.eye(6), rcond=1e-10)
        x = cov @ self.gradient
        evals = np.linalg.eigvalsh(self.hessian)
        rank = int(np.sum(evals > max(1e-8, float(np.max(evals)) * 1e-6))) if np.max(evals) > 0.0 else 0
        return x[:3], x[3:], rank, np.diag(cov)


@dataclass
class MountPosterior:
    sensor_from_segment_mean: np.ndarray
    covariance_rad2: np.ndarray
    information: float
    rank: int
    evidence: tuple[str, ...]


@dataclass
class BranchCandidate:
    branch_id: int
    hinge_signs: dict[str, int]
    prior_weight: float
    physical_valid: bool
    physical_qa: dict[str, Any]
    weight: float


@dataclass
class PosteriorState:
    headings: dict[str, HeadingPosterior] = field(default_factory=lambda: {edge.name: HeadingPosterior() for edge in EDGES})
    hinges: dict[str, HingePosterior] = field(default_factory=lambda: {edge.name: HingePosterior() for edge in HINGE_EDGES})
    centers: dict[str, CenterPosterior] = field(default_factory=lambda: {edge.name: CenterPosterior() for edge in EDGES})
    mounts: dict[str, MountPosterior] = field(default_factory=dict)
    branch_candidates: list[BranchCandidate] = field(default_factory=list)
    pelvis_yaw_gauge_rad: float = 0.0
    prefix_index: int = -1

    def refresh_mounts_and_branches(self) -> None:
        self.mounts = estimate_mount_posteriors(self)
        self.branch_candidates = branch_candidates_after_physical_gate(self)

    def summary(self) -> dict[str, Any]:
        branch_weights = np.asarray([row.weight for row in self.branch_candidates], dtype=float)
        if len(branch_weights) == 0 or np.sum(branch_weights) <= 0.0:
            branch_weights = np.full(16, 1.0 / 16.0)
        else:
            branch_weights = branch_weights / np.sum(branch_weights)
        center_rank: dict[str, int] = {}
        center_vectors: dict[str, Any] = {}
        for edge, posterior in self.centers.items():
            parent, child, rank, cov = posterior.solve()
            center_rank[edge] = rank
            center_vectors[edge] = {
                "parent_sensor_m": parent.tolist(),
                "child_sensor_m": child.tolist(),
                "covariance_diag_m2": finite_json(cov),
            }
        mount_info = {seg: self.mounts.get(seg, _broad_mount(seg)).information for seg in SEGMENTS}
        mount_rank = {seg: self.mounts.get(seg, _broad_mount(seg)).rank for seg in SEGMENTS}
        heading_info = {edge: self.headings[edge].information for edge in self.headings}
        hinge_info = {edge: self.hinges[edge].information for edge in self.hinges}
        total_rank = (
            sum(1 for v in heading_info.values() if v > 0.5)
            + sum(2 for v in hinge_info.values() if v > 0.5)
            + sum(center_rank.values())
            + sum(mount_rank.values())
        )
        max_rank = 9 + 2 * 4 + 6 * 9 + 3 * 10
        log_terms: list[float] = []
        log_terms.extend(math.log1p(v) for v in heading_info.values() if v > 0.0)
        log_terms.extend(2.0 * math.log1p(v) for v in hinge_info.values() if v > 0.0)
        for posterior in self.centers.values():
            evals = np.linalg.eigvalsh(posterior.hessian)
            log_terms.extend(math.log1p(float(max(v, 0.0))) for v in evals)
        log_terms.extend(math.log1p(v) for v in mount_info.values() if v > 0.0)
        return {
            "prefix_index": self.prefix_index,
            "pelvis_yaw_gauge_rad": self.pelvis_yaw_gauge_rad,
            "pelvis_yaw_gauge_information": 0.0,
            "heading_mean_rad": {edge: self.headings[edge].mean() for edge in self.headings},
            "heading_variance_rad2": {edge: self.headings[edge].variance() for edge in self.headings},
            "heading_information": heading_info,
            "hinge_information": hinge_info,
            "hinge_axes_sensor": {
                edge: {
                    "parent": self.hinges[edge].axes()[0].tolist(),
                    "child": self.hinges[edge].axes()[1].tolist(),
                    "parent_eigenvalues": self.hinges[edge].axes()[2].tolist(),
                    "child_eigenvalues": self.hinges[edge].axes()[3].tolist(),
                    "sign_positive_probability": self.hinges[edge].sign_probability(),
                }
                for edge in self.hinges
            },
            "center_information": {edge: self.centers[edge].information for edge in self.centers},
            "center_residual_rms_mps2": {
                edge: finite_json(self.centers[edge].residual_rms_mps2())
                for edge in self.centers
            },
            "center_rank": center_rank,
            "center_vectors": center_vectors,
            "mount_information": mount_info,
            "mount_rank": mount_rank,
            "mount_covariance_rad2": {
                seg: finite_json(self.mounts.get(seg, _broad_mount(seg)).covariance_rad2)
                for seg in SEGMENTS
            },
            "sensor_from_segment_mean": {
                seg: self.mounts.get(seg, _broad_mount(seg)).sensor_from_segment_mean.tolist()
                for seg in SEGMENTS
            },
            "valid_branch_count": int(sum(1 for row in self.branch_candidates if row.physical_valid)),
            "branch_weights": branch_weights.tolist(),
            "branch_entropy": entropy(branch_weights),
            "branch_candidates": [jsonable(row.__dict__) for row in self.branch_candidates],
            "gauge_reduced_rank": int(total_rank),
            "max_declared_rank": int(max_rank),
            "rank_fraction": float(total_rank / max_rank),
            "information_logdet": float(np.sum(log_terms)) if log_terms else 0.0,
        }


def finite_json(value: Any) -> Any:
    arr = np.asarray(value)
    if arr.ndim == 0:
        v = float(arr)
        if math.isinf(v):
            return "Infinity" if v > 0.0 else "-Infinity"
        if math.isnan(v):
            return "NaN"
        return v
    return [finite_json(v) for v in arr]


def jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return finite_json(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return finite_json(np.asarray(value))
    return value


def entropy(weights: np.ndarray) -> float:
    positive = np.asarray(weights, dtype=float)
    positive = positive[positive > 0.0]
    return float(-np.sum(positive * np.log(positive))) if len(positive) else 0.0


def _slices(n_parent: int, n_child: int, lag: int) -> tuple[slice, slice, int]:
    if lag >= 0:
        length = min(n_parent - lag, n_child)
        return slice(lag, lag + max(0, length)), slice(0, max(0, length)), max(0, length)
    shift = -lag
    length = min(n_parent, n_child - shift)
    return slice(0, max(0, length)), slice(shift, shift + max(0, length)), max(0, length)


def _clock_offsets_to_pelvis(episode: EpisodeFrontend) -> dict[str, float]:
    offsets = {"pelvis": 0.0}
    pending = ["pelvis"]
    while pending:
        parent = pending.pop(0)
        for child in TREE_CHILDREN.get(parent, ()):
            edge_name = SEGMENT_EDGE[child]
            timing = episode.pair_alignment_reports[edge_name]["corresponding_timing_span_windows"]
            offsets[child] = offsets[parent] + float(timing["predicted_parent_minus_child_offset_s"])
            pending.append(child)
    return offsets


def _series_for_segment(episode: EpisodeFrontend, segment: str) -> NodeSeries:
    return episode.nodes[SEGMENT_TO_NODE[segment]]


def _split_gap_safe_indices(
    episode: EpisodeFrontend,
    edge_name: str,
    parent_indices: np.ndarray,
    child_indices: np.ndarray,
) -> list[tuple[np.ndarray, np.ndarray]]:
    edge = EDGE_BY_NAME[edge_name]
    parent = _series_for_segment(episode, edge.parent)
    child = _series_for_segment(episode, edge.child)
    pi = np.asarray(parent_indices, dtype=int)
    ci = np.asarray(child_indices, dtype=int)
    if len(pi) != len(ci):
        raise ValueError("aligned parent/child indices must have same length")
    if len(pi) < 3:
        return []
    parent_break = (
        (np.diff(parent.time_us[pi]) != EXPECTED_STEP_US)
        | (np.diff(parent.derived_boot_epoch[pi]) != 0)
        | (np.diff(parent.contiguous_span_id[pi]) != 0)
    )
    child_break = (
        (np.diff(child.time_us[ci]) != EXPECTED_STEP_US)
        | (np.diff(child.derived_boot_epoch[ci]) != 0)
        | (np.diff(child.contiguous_span_id[ci]) != 0)
    )
    breaks = np.flatnonzero(parent_break | child_break) + 1
    boundaries = np.r_[0, breaks, len(pi)]
    spans = []
    for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
        if stop - start >= 80:
            spans.append((pi[start:stop], ci[start:stop]))
    return spans


def aligned_spans_for_episode(episode: EpisodeFrontend) -> tuple[AlignedSpan, ...]:
    offsets = _clock_offsets_to_pelvis(episode)
    out: list[AlignedSpan] = []
    for edge in EDGES:
        report = episode.pair_alignment_reports[edge.name]
        lag = int(report["selected_lag_samples"])
        window_audit = report["corresponding_timing_span_windows"]
        span_counter = 0
        for window in window_audit["windows"]:
            p0, p1 = [int(v) for v in window["parent_source_rows_half_open"]]
            c0, c1 = [int(v) for v in window["child_source_rows_half_open"]]
            parent_base = np.arange(p0, p1, dtype=int)
            child_base = np.arange(c0, c1, dtype=int)
            ps, cs, n = _slices(len(parent_base), len(child_base), lag)
            if n < 80:
                continue
            for pi, ci in _split_gap_safe_indices(episode, edge.name, parent_base[ps], child_base[cs]):
                parent = _series_for_segment(episode, edge.parent)
                child = _series_for_segment(episode, edge.child)
                parent_time_s = parent.time_us[pi].astype(float) * 1e-6
                child_time_s = child.time_us[ci].astype(float) * 1e-6
                if not np.allclose(np.diff(parent_time_s), 0.005, atol=1e-9):
                    raise RuntimeError("aligned span parent time is not contiguous 200 Hz")
                if not np.allclose(np.diff(child_time_s), 0.005, atol=1e-9):
                    raise RuntimeError("aligned span child time is not contiguous 200 Hz")
                out.append(AlignedSpan(
                    episode_index=episode.chronological_index,
                    qa_label=episode.qa_label,
                    edge=edge.name,
                    span_index=span_counter,
                    parent_segment=edge.parent,
                    child_segment=edge.child,
                    parent_indices=pi,
                    child_indices=ci,
                    time_root_s=parent_time_s + offsets[edge.parent],
                    parent_time_s=parent_time_s,
                    child_time_s=child_time_s,
                    parent_acc_mps2=parent.acc_mps2[pi],
                    child_acc_mps2=child.acc_mps2[ci],
                    parent_gyro_rads=parent.gyro_rads[pi],
                    child_gyro_rads=child.gyro_rads[ci],
                    parent_quat_wxyz=parent.quat_world_sensor_wxyz[pi],
                    child_quat_wxyz=child.quat_world_sensor_wxyz[ci],
                    lag_uncertainty_s=float(report["lag_uncertainty_s"]),
                    alignment_status=str(report["status"]),
                    timing_audit={
                        "source": "sealed_pair_alignment_metadata",
                        "selected_lag_samples": lag,
                        "window": window,
                        "rows_cross_gap": False,
                        "parent_source_indices_binding": array_binding(pi.astype(np.int64)),
                        "child_source_indices_binding": array_binding(ci.astype(np.int64)),
                    },
                ))
                span_counter += 1
    return tuple(out)


def windows_for_span(span: AlignedSpan, *, window_s: float = 1.0, stride_s: float = 0.5) -> tuple[WindowFactor, ...]:
    dt = float(np.median(np.diff(span.time_root_s)))
    win = max(8, int(round(window_s / dt)))
    stride = max(1, int(round(stride_s / dt)))
    rows: list[WindowFactor] = []
    for start in range(0, max(0, len(span.time_root_s) - win + 1), stride):
        stop = start + win
        rel = span.parent_gyro_rads[start:stop] - span.child_gyro_rads[start:stop]
        dyn = np.vstack([
            span.parent_acc_mps2[start:stop] - np.mean(span.parent_acc_mps2[start:stop], axis=0),
            span.child_acc_mps2[start:stop] - np.mean(span.child_acc_mps2[start:stop], axis=0),
        ])
        rel_trace = float(np.trace(np.cov(rel.T))) if len(rel) > 3 else 0.0
        dyn_trace = float(np.trace(np.cov(dyn.T))) if len(dyn) > 3 else 0.0
        status_scale = 0.35 if span.alignment_status != "INFORMATIVE_INTERIOR_PEAK" else 1.0
        lag_scale = 1.0 / (1.0 + span.lag_uncertainty_s / 0.15)
        weight = float(max(0.0, rel_trace + 0.02 * dyn_trace) * status_scale * lag_scale)
        rows.append(WindowFactor(
            episode_index=span.episode_index,
            edge=span.edge,
            span_index=span.span_index,
            start=start,
            stop=stop,
            weight=weight,
            rel_gyro_trace=rel_trace,
            dyn_acc_trace=dyn_trace,
            valid_fraction=1.0,
        ))
    if not rows:
        rows.append(WindowFactor(span.episode_index, span.edge, span.span_index, 0, len(span.time_root_s), 0.0, 0.0, 0.0, 1.0))
    return tuple(rows)


def _window_information(windows: Sequence[WindowFactor]) -> float:
    values = np.asarray([row.weight for row in windows], dtype=float)
    if not np.any(values > 0.0):
        return 0.0
    scale = float(np.percentile(values[values > 0.0], 75))
    return float(np.sum(np.clip(values / max(scale, 1e-9), 0.0, 1.0)) / 10.0)


def _principal_axis(vectors: np.ndarray) -> np.ndarray:
    cov = np.cov(np.asarray(vectors, dtype=float).T)
    evals, evecs = np.linalg.eigh(cov)
    return unit(evecs[:, int(np.argmax(evals))])


def _frame_with_y_axis(y_axis_sensor: np.ndarray, fallback_x: np.ndarray | None = None) -> np.ndarray:
    y = unit(y_axis_sensor, np.array([0.0, 1.0, 0.0]))
    x0 = np.array([1.0, 0.0, 0.0]) if fallback_x is None else unit(fallback_x)
    if abs(float(np.dot(x0, y))) > 0.9:
        x0 = np.array([0.0, 0.0, 1.0])
    x = unit(x0 - y * np.dot(x0, y))
    z = unit(np.cross(x, y))
    frame = np.column_stack([x, y, z])
    if np.linalg.det(frame) < 0.0:
        z *= -1.0
        frame = np.column_stack([x, y, z])
    return frame


def hinge_axis_factor(span: AlignedSpan, windows: Sequence[WindowFactor]) -> HingeAxisFactor:
    info = _window_information(windows)
    if info <= 0.0:
        axis = _principal_axis(span.parent_gyro_rads - span.child_gyro_rads)
        return HingeAxisFactor(span.episode_index, span.qa_label, span.edge, span.span_index, "LOW_INFORMATION", 0.0, len(span.time_root_s), axis, axis, 0.0, np.inf, None)
    start = time.perf_counter()
    try:
        settings = {
            "useSampleSelection": True,
            "dataSize": 800,
            "winSize": 21,
            "angRateEnergyThreshold": 0.05,
            "w0": 50.0,
        }
        with contextlib.redirect_stdout(io.StringIO()):
            parent_axis, child_axis = qmt.jointAxisEstHingeOlsson(
                np.ascontiguousarray(span.parent_acc_mps2),
                np.ascontiguousarray(span.child_acc_mps2),
                np.ascontiguousarray(span.parent_gyro_rads),
                np.ascontiguousarray(span.child_gyro_rads),
                estSettings=settings,
                debug=False,
                plot=False,
            )
        return HingeAxisFactor(
            span.episode_index,
            span.qa_label,
            span.edge,
            span.span_index,
            "PASS",
            float(time.perf_counter() - start),
            len(span.time_root_s),
            unit(np.asarray(parent_axis, dtype=float).reshape(3)),
            unit(np.asarray(child_axis, dtype=float).reshape(3)),
            info,
            float(1.0 / max(info, 1e-6)),
            None,
        )
    except Exception as exc:
        axis = _principal_axis(span.parent_gyro_rads - span.child_gyro_rads)
        return HingeAxisFactor(
            span.episode_index,
            span.qa_label,
            span.edge,
            span.span_index,
            "QMT_FAIL_LOW_WEIGHT_PCA_AXIS_RECORDED",
            float(time.perf_counter() - start),
            len(span.time_root_s),
            axis,
            axis,
            0.05 * info,
            float(20.0 / max(info, 1e-6)),
            f"{type(exc).__name__}: {exc}",
        )


def center_factor(span: AlignedSpan, windows: Sequence[WindowFactor]) -> CenterFactor:
    """Estimate one sensor-local joint-centre pair without a shared yaw.

    Let ``r`` point from a sensor origin to the common joint centre.  The
    specific force transported to that point is ``f + K(omega, alpha) r``.
    The two transported vectors live in different sensor coordinates, but
    their norms are equal.  Using that scalar constraint avoids the circular
    dependency in the old implementation, which first rotated both sensors
    through not-yet-calibrated world headings and then fitted vector equality.

    The broad initialization is deliberately weak.  It stabilizes genuinely
    low-information spans but does not manufacture information: the returned
    information is continuously scaled by measured angular excitation and
    the data-only Jacobian rank.
    """
    start = time.perf_counter()
    usable = [row for row in windows if row.weight > 0.0]
    if not usable:
        zeros = np.zeros(3)
        return CenterFactor(span.episode_index, span.qa_label, span.edge, span.span_index, "LOW_INFORMATION", 0.0, 0, 0, 0.0, 0.0, zeros, zeros, np.full(6, np.inf), "NO_RANK")
    dt = float(np.median(np.diff(span.time_root_s)))
    filter_window = min(21, len(span.time_root_s) // 2 * 2 - 1)
    if filter_window < 7:
        zeros = np.zeros(3)
        return CenterFactor(span.episode_index, span.qa_label, span.edge, span.span_index, "LOW_INFORMATION", 0.0, 0, 0, 0.0, 0.0, zeros, zeros, np.full(6, np.inf), "TOO_FEW_ROWS_FOR_LOCAL_DERIVATIVE")
    polynomial = min(3, filter_window - 2)
    parent_gyro = savgol_filter(span.parent_gyro_rads, filter_window, polynomial, axis=0)
    child_gyro = savgol_filter(span.child_gyro_rads, filter_window, polynomial, axis=0)
    parent_alpha = savgol_filter(
        span.parent_gyro_rads, filter_window, polynomial, deriv=1, delta=dt, axis=0,
    )
    child_alpha = savgol_filter(
        span.child_gyro_rads, filter_window, polynomial, deriv=1, delta=dt, axis=0,
    )
    selected: list[int] = []
    row_weights: list[float] = []
    endpoint = filter_window // 2
    for window in usable:
        begin = max(endpoint, window.start)
        end = min(len(span.time_root_s) - endpoint, window.stop)
        if end <= begin:
            continue
        step = max(1, (end - begin) // 48)
        indices = list(range(begin, end, step))
        selected.extend(indices)
        row_weights.extend([math.sqrt(max(window.weight, 1e-9))] * len(indices))
    if len(selected) < 12:
        zeros = np.zeros(3)
        return CenterFactor(span.episode_index, span.qa_label, span.edge, span.span_index, "LOW_INFORMATION", float(time.perf_counter() - start), len(selected), 0, 0.0, 0.0, zeros, zeros, np.full(6, np.inf), "TOO_FEW_SELECTED_ROWS")
    # Adjacent one-second windows overlap.  Duplicate rows would manufacture
    # precision and dominate runtime, so each physical sample contributes at
    # most once.  The deterministic cap retains the whole span rather than a
    # result-dependent handful of rows.
    selected_array = np.asarray(selected, dtype=int)
    unique_keep, first = np.unique(selected_array, return_index=True)
    unique_weight = np.asarray(row_weights, dtype=float)[first]
    if len(unique_keep) > 240:
        retained = np.linspace(0, len(unique_keep) - 1, 240, dtype=int)
        unique_keep = unique_keep[retained]
        unique_weight = unique_weight[retained]
    keep = unique_keep
    weight = unique_weight
    weight /= max(float(np.median(weight[weight > 0.0])), 1e-9)
    parent_k = np.asarray([
        skew(parent_alpha[index]) + skew(parent_gyro[index]) @ skew(parent_gyro[index])
        for index in keep
    ])
    child_k = np.asarray([
        skew(child_alpha[index]) + skew(child_gyro[index]) @ skew(child_gyro[index])
        for index in keep
    ])
    parent_force = span.parent_acc_mps2[keep]
    child_force = span.child_acc_mps2[keep]

    edge = EDGE_BY_NAME[span.edge]
    parent_z = _broad_mount(edge.parent).sensor_from_segment_mean[:, 2]
    child_z = _broad_mount(edge.child).sensor_from_segment_mean[:, 2]
    nominal_by_segment = {
        "torso": 0.28,
        "pelvis": 0.18,
        "upper_arm_left": 0.3175,
        "upper_arm_right": 0.3175,
        "forearm_left": 0.255,
        "forearm_right": 0.255,
        "thigh_left": 0.480,
        "thigh_right": 0.480,
        "shank_left": 0.430,
        "shank_right": 0.430,
    }
    parent_scale = nominal_by_segment.get(edge.parent, 0.25)
    child_scale = nominal_by_segment.get(edge.child, 0.25)
    # Parent endpoints are normally distal and child endpoints proximal.  The
    # pelvis/torso and shoulder/hip offsets are surface-mounted and receive the
    # same broad prior scale rather than invented anatomical coordinates.
    initial = np.r_[
        -0.35 * parent_scale * parent_z,
        0.35 * child_scale * child_z,
    ]
    data_sigma_mps2 = 0.35
    broad_prior_sigma_m = 0.25

    def residual(value: np.ndarray, *, include_prior: bool = True) -> np.ndarray:
        parent_joint = parent_force + np.einsum("nij,j->ni", parent_k, value[:3])
        child_joint = child_force + np.einsum("nij,j->ni", child_k, value[3:])
        data = weight * (
            np.linalg.norm(parent_joint, axis=1)
            - np.linalg.norm(child_joint, axis=1)
        ) / data_sigma_mps2
        if not include_prior:
            return data
        return np.r_[data, (value - initial) / broad_prior_sigma_m]

    solution = least_squares(
        residual,
        initial,
        bounds=(-0.65, 0.65),
        loss="soft_l1",
        f_scale=1.5,
        max_nfev=60,
    )
    x = np.asarray(solution.x, dtype=float)
    data_residual = residual(x, include_prior=False) * data_sigma_mps2
    rms = float(np.sqrt(np.mean(data_residual * data_residual)))
    # A six-column data-only Jacobian prevents the broad initialization rows
    # from being counted as measured rank.
    epsilon = 2e-5
    base = residual(x, include_prior=False)
    jacobian = np.column_stack([
        (residual(x + epsilon * np.eye(6)[axis], include_prior=False) - base) / epsilon
        for axis in range(6)
    ])
    h = jacobian.T @ jacobian
    evals = np.linalg.eigvalsh(h)
    rank = int(np.sum(evals > max(1e-8, float(np.max(evals)) * 1e-5))) if np.max(evals) > 0.0 else 0
    cov = np.linalg.pinv(h + np.eye(6) / broad_prior_sigma_m**2, rcond=1e-10)
    # The raw Jacobian covariance assumes the 200 Hz rows are perfectly
    # explained independent samples.  Human soft tissue, differentiated gyro
    # noise and model mismatch violate that assumption.  Scale uncertainty by
    # the measured lack of fit so a high-rank but poor centre solution cannot
    # masquerade as millimetre-accurate mounting geometry.
    covariance_mismatch_scale = float(max(1.0, (rms / data_sigma_mps2) ** 2))
    cov *= covariance_mismatch_scale
    rotational_excitation = 0.2 * np.maximum(
        np.linalg.norm(parent_alpha[keep], axis=1) + np.linalg.norm(parent_gyro[keep], axis=1) ** 2,
        np.linalg.norm(child_alpha[keep], axis=1) + np.linalg.norm(child_gyro[keep], axis=1) ** 2,
    )
    excitation_scale = float(np.clip(np.median(rotational_excitation) / 0.5, 0.0, 1.0))
    fit_scale = float(1.0 / (1.0 + (rms / 1.0) ** 2))
    info = float(_window_information(windows) * excitation_scale * fit_scale * rank / 6.0)
    parent_norm = float(np.linalg.norm(x[:3]))
    child_norm = float(np.linalg.norm(x[3:]))
    if parent_norm > 0.60 or child_norm > 0.60:
        status = "PHYSICAL_VECTOR_BOUND_REJECTED"
        info = 0.0
        gate = "REJECTED_BEFORE_RESIDUAL_RANKING"
    elif rank < 4:
        status = "LOW_RANK"
        gate = "RETAINED_LOW_WEIGHT_UNOBSERVED_COMPONENTS"
        info *= rank / 6.0
    else:
        status = "PASS"
        gate = "PASS"
    return CenterFactor(
        span.episode_index,
        span.qa_label,
        span.edge,
        span.span_index,
        status,
        float(time.perf_counter() - start),
        int(len(keep)),
        rank,
        float(info),
        rms,
        x[:3],
        x[3:],
        np.diag(cov),
        gate,
    )


def heading_stream_factor(
    span: AlignedSpan,
    windows: Sequence[WindowFactor],
    hinge: HingeAxisFactor | None,
    center: CenterFactor,
) -> HeadingStreamFactor:
    start = time.perf_counter()
    edge = EDGE_BY_NAME[span.edge]
    info0 = _window_information(windows)
    joint_unc = 4.0
    joint_source = "WIDE_DATA_DERIVED_SUBSPACE_LOW_INFORMATION"
    if edge.joint_kind == "hinge" and hinge is not None and hinge.information > 0.0:
        parent_frame = _frame_with_y_axis(hinge.parent_axis_sensor, _principal_axis(span.parent_gyro_rads))
        child_frame = _frame_with_y_axis(hinge.child_axis_sensor, _principal_axis(span.child_gyro_rads))
        joint = np.array([0.0, 1.0, 0.0])
        constraint = "euler_1d"
        joint_unc = float(hinge.uncertainty_rad2)
        joint_source = "QMT_OLSSON_LOCAL_HINGE_AXIS_FACTOR"
    else:
        if center.information > 0.0 and np.linalg.norm(center.parent_vector_sensor_m) > 1e-6 and np.linalg.norm(center.child_vector_sensor_m) > 1e-6:
            parent_axis = unit(center.parent_vector_sensor_m)
            child_axis = unit(center.child_vector_sensor_m)
            joint_source = "SEEL_STYLE_LOCAL_CENTER_VECTOR_SUBSPACE"
            joint_unc = float(np.mean(np.maximum(center.covariance_diag_m2, 1e-5)))
        else:
            parent_axis = _principal_axis(span.parent_gyro_rads)
            child_axis = _principal_axis(span.child_gyro_rads)
        parent_frame = _frame_with_y_axis(parent_axis)
        child_frame = _frame_with_y_axis(child_axis)
        joint = np.array([[0.0, 1.0, 0.0], [0.0, 1.0, 0.0]])
        constraint = "euler"
    parent_factor_q = normalize_quat_wxyz(rotation_to_qmt_wxyz(
        Rotation.from_matrix(qmt_wxyz_to_rotation(span.parent_quat_wxyz).as_matrix() @ parent_frame)
    ))
    child_factor_q = normalize_quat_wxyz(rotation_to_qmt_wxyz(
        Rotation.from_matrix(qmt_wxyz_to_rotation(span.child_quat_wxyz).as_matrix() @ child_frame)
    ))
    qmt_decimation = 5 if len(span.time_root_s) >= 400 else 1
    qmt_sample_dt_s = 0.005 * qmt_decimation
    qmt_rate_hz = 200.0 / qmt_decimation
    qmt_index = np.arange(0, len(span.time_root_s), qmt_decimation, dtype=int)
    settings = {
        "windowTime": 2.0,
        "estimationRate": 2.0,
        "dataRate": qmt_rate_hz,
        "tauDelta": 5.0,
        "tauBias": 5.0,
        "ratingMin": 0.4,
        "alignment": "backward",
        "enableStillness": True,
        "optimizerSteps": 2,
        "stillnessTime": 1.0,
        "stillnessThreshold": np.deg2rad(4.0),
        "stillnessRating": 0.0,
        "startRating": 0.0,
        "constraint": constraint,
        "useRomConstraints": False,
        "deltaRange": np.array([0.0]),
    }
    try:
        qmt_time_s = np.arange(len(qmt_index), dtype=float) * qmt_sample_dt_s
        with contextlib.redirect_stdout(io.StringIO()):
            quat2corr_factor, _delta, delta_filt, rating, qmt_state = qmt.headingCorrection(
                np.ascontiguousarray(span.parent_gyro_rads[qmt_index]),
                np.ascontiguousarray(span.child_gyro_rads[qmt_index]),
                np.ascontiguousarray(parent_factor_q[qmt_index]),
                np.ascontiguousarray(child_factor_q[qmt_index]),
                np.ascontiguousarray(qmt_time_s),
                joint,
                {},
                estSettings=settings,
                verbose=False,
                debug=False,
                plot=False,
            )
        factor_from_sensor = child_frame.T
        child_sensor_m = qmt_wxyz_to_rotation(quat2corr_factor).as_matrix() @ factor_from_sensor
        quat2corr_sensor_ds = normalize_quat_wxyz(rotation_to_qmt_wxyz(Rotation.from_matrix(child_sensor_m)))
        rating_ds = np.clip(np.asarray(rating, dtype=float), 0.0, 1.0)
        qmt_state_ds = np.asarray(qmt_state, dtype=int)
        delta_filt_ds = np.asarray(delta_filt, dtype=float)
        ds_time_root = span.time_root_s[qmt_index][:len(delta_filt_ds)]
        quat2corr_sensor_ds = quat2corr_sensor_ds[:len(ds_time_root)]
        rating_ds = rating_ds[:len(ds_time_root)]
        qmt_state_ds = qmt_state_ds[:len(ds_time_root)]
        delta_filt_ds = delta_filt_ds[:len(ds_time_root)]
        if len(ds_time_root) == 0:
            raise RuntimeError("QMT headingCorrection returned an empty correction stream")
        if qmt_decimation == 1:
            quat2corr_sensor = quat2corr_sensor_ds
            rating = rating_ds
            qmt_state = qmt_state_ds
            delta_filt = delta_filt_ds
        else:
            quat2corr_sensor = interp_quat_wxyz(ds_time_root, quat2corr_sensor_ds, span.time_root_s)
            rating = np.clip(np.interp(span.time_root_s, ds_time_root, rating_ds), 0.0, 1.0)
            qmt_state = np.rint(np.interp(span.time_root_s, ds_time_root, qmt_state_ds)).astype(int)
            delta_filt = np.interp(span.time_root_s, ds_time_root, delta_filt_ds)
        regular = (rating > 0.0) & (qmt_state == 1)
        if np.any(regular):
            weights = rating[regular] * max(info0, 1e-6)
            angles = delta_filt[regular]
            mean = float(math.atan2(np.sum(weights * np.sin(angles)), np.sum(weights * np.cos(angles))))
            centered = wrap_pi(angles - mean)
            spread = float(np.average(centered * centered, weights=weights))
            info = float(np.sum(weights) / max(0.05 + spread + joint_unc, 0.05))
            var = float(1.0 / max(info, 1e-6))
        else:
            mean = 0.0
            var = np.inf
            info = 0.0
        return HeadingStreamFactor(
            span.episode_index,
            span.qa_label,
            span.edge,
            span.span_index,
            "PASS" if info > 0.0 else "LOW_INFORMATION_QMT_STREAM_RETAINED",
            float(time.perf_counter() - start),
            len(span.time_root_s),
            span.time_root_s.copy(),
            np.asarray(quat2corr_sensor, dtype=float),
            delta_filt,
            rating,
            qmt_state,
            info,
            mean,
            var,
            joint_source,
            joint_unc,
            None,
        )
    except Exception as exc:
        empty_q = np.empty((0, 4), dtype=float)
        empty = np.empty(0, dtype=float)
        return HeadingStreamFactor(
            span.episode_index,
            span.qa_label,
            span.edge,
            span.span_index,
            "QMT_FAIL_NO_TRAJECTORY_CANDIDATE",
            float(time.perf_counter() - start),
            len(span.time_root_s),
            np.empty(0, dtype=float),
            empty_q,
            empty,
            empty,
            np.empty(0, dtype=int),
            0.0,
            0.0,
            np.inf,
            joint_source,
            joint_unc,
            f"{type(exc).__name__}: {exc}",
        )


@dataclass
class _PersistentHeadingState:
    """One time-continuous relative-heading state for one graph edge."""

    delta_rad: float = 0.0
    bias_rad_s: float = 0.0
    variance_rad2: float = math.pi**2
    last_time_s: float | None = None
    # Zero is the deliberately broad relative-yaw prior implied by the common
    # VQF world initialization and qualitative wear likelihood.  Its pi-radian
    # variance means it is not treated as a known pose, but it prevents the
    # first arbitrarily weak window from becoming an exact new gauge.
    initialized: bool = True

    def update(
        self,
        time_s: float,
        measurement_rad: float,
        measurement_variance_rad2: float,
        rating: float,
    ) -> tuple[float, float]:
        now = float(time_s)
        if self.last_time_s is None:
            dt = 0.0
        else:
            dt = max(0.0, now - self.last_time_s)
        # A gap carries uncertainty and the last bias; it never creates a new
        # action-local yaw gauge or interpolated evidence.
        gap_without_samples = bool(dt > 2.5)
        if gap_without_samples:
            # No raw rows exist in the inter-episode hole.  Extrapolating the
            # last fitted slope through a 10--150 s hole caused RUN_008's
            # multi-radian tree wind-up.  Hold the mean, forget the slope and
            # grow covariance instead; this is propagation, not a reset/gauge.
            prediction = self.delta_rad
            self.bias_rad_s = 0.0
        else:
            prediction = float(wrap_pi(self.delta_rad + self.bias_rad_s * dt))
        predicted_variance = float(
            self.variance_rad2 + math.radians(0.20) ** 2 * dt
        )
        quality = float(np.clip(rating, 0.0, 1.0))
        if quality <= 0.0 or not np.isfinite(measurement_variance_rad2):
            self.delta_rad = prediction
            self.variance_rad2 = predicted_variance
            self.last_time_s = now
            return self.delta_rad, self.variance_rad2
        effective_variance = float(
            max(measurement_variance_rad2, math.radians(1.0) ** 2)
            / max(quality, 0.05)
        )
        if not self.initialized:
            self.delta_rad = float(wrap_pi(measurement_rad))
            self.variance_rad2 = effective_variance
            self.initialized = True
        else:
            gain = float(predicted_variance / (predicted_variance + effective_variance))
            innovation = float(wrap_pi(measurement_rad - prediction))
            self.delta_rad = float(wrap_pi(prediction + gain * innovation))
            if dt > 1e-6 and not gap_without_samples:
                bias_gain = min(0.08, 0.25 * gain)
                correction_rate = float(np.clip(
                    innovation / dt, -math.radians(2.0), math.radians(2.0),
                ))
                self.bias_rad_s = float(np.clip(
                    (1.0 - bias_gain) * self.bias_rad_s + bias_gain * correction_rate,
                    -math.radians(2.0), math.radians(2.0),
                ))
            self.variance_rad2 = float((1.0 - gain) * predicted_variance)
        self.last_time_s = now
        return self.delta_rad, self.variance_rad2


def _span_segment_quaternions(
    span: AlignedSpan,
    state: PosteriorState,
) -> tuple[np.ndarray, np.ndarray]:
    parent_mount = state.mounts[span.parent_segment].sensor_from_segment_mean
    child_mount = state.mounts[span.child_segment].sensor_from_segment_mean
    parent_matrix = qmt_wxyz_to_rotation(span.parent_quat_wxyz).as_matrix() @ parent_mount
    child_matrix = qmt_wxyz_to_rotation(span.child_quat_wxyz).as_matrix() @ child_mount
    return (
        normalize_quat_wxyz(rotation_to_qmt_wxyz(Rotation.from_matrix(parent_matrix))),
        normalize_quat_wxyz(rotation_to_qmt_wxyz(Rotation.from_matrix(child_matrix))),
    )


def _heading_window_slices(row_count: int) -> list[tuple[int, int]]:
    # Two-second windows with one-second stride.  Every row belongs to a
    # predeclared temporal neighbourhood; selection never examines a desired
    # pose, fitted heading, action name or result residual.
    width = 400
    stride = 200
    if row_count < width:
        return [(0, row_count)] if row_count >= 80 else []
    starts = list(range(0, row_count - width + 1, stride))
    if starts[-1] + width < row_count:
        starts.append(row_count - width)
    return [(start, start + width) for start in starts]


def _hinge_heading_observation(
    parent_quat: np.ndarray,
    child_quat: np.ndarray,
    start_rad: float,
) -> tuple[float, float, float]:
    # The shared sensor-to-segment frames put both fitted hinge lines on the
    # segment +X line.  QMT's official 1D estimator is called unmodified; only
    # the persistent wrapper/filter is ours.
    index = np.arange(0, len(parent_quat), 5, dtype=int)
    q1 = np.ascontiguousarray(parent_quat[index])
    q2 = np.ascontiguousarray(child_quat[index])
    joint = np.array([1.0, 0.0, 0.0])
    candidates: list[tuple[float, float, float]] = []
    for initial in (start_rad, start_rad + math.pi):
        try:
            delta, rating, cost = estimateDelta1d(
                q1, q2, joint, initial, "euler_1d", 5,
            )
            candidates.append((
                float(wrap_pi(np.asarray(delta).reshape(-1)[0])),
                float(np.clip(np.asarray(rating).reshape(-1)[0], 0.0, 1.0)),
                float(np.asarray(cost).reshape(-1)[0]),
            ))
        except Exception:
            continue
    if not candidates:
        return 0.0, 0.0, np.inf
    candidates.sort(key=lambda row: (row[2], abs(float(wrap_pi(row[0] - start_rad)))))
    delta, rating, cost = candidates[0]
    effective_rows = max(1.0, len(index) / 8.0)
    variance = float(
        math.radians(25.0) ** 2
        / max(rating * rating * effective_rows, 0.05)
    )
    if not np.isfinite(cost):
        rating = 0.0
        variance = np.inf
    return delta, rating, variance


def _center_heading_observation(
    span: AlignedSpan,
    state: PosteriorState,
    start: int,
    stop: int,
) -> tuple[float, float, float]:
    """Heading from common-joint specific-force vector closure.

    This is the vector companion to the Seel-style norm factor.  It is only
    evaluated after the sensor-local centre pair exists, and therefore does
    not require an action pose, ROM label or a pre-aligned cross-sensor yaw.
    """
    parent_center, child_center, rank, covariance = state.centers[span.edge].solve()
    if rank < 4 or not np.all(np.isfinite(covariance)):
        return 0.0, 0.0, np.inf
    dt = float(np.median(np.diff(span.time_root_s)))
    n = len(span.time_root_s)
    filter_window = min(21, n // 2 * 2 - 1)
    if filter_window < 7:
        return 0.0, 0.0, np.inf
    polynomial = min(3, filter_window - 2)
    parent_gyro = savgol_filter(span.parent_gyro_rads, filter_window, polynomial, axis=0)
    child_gyro = savgol_filter(span.child_gyro_rads, filter_window, polynomial, axis=0)
    parent_alpha = savgol_filter(
        span.parent_gyro_rads, filter_window, polynomial, deriv=1, delta=dt, axis=0,
    )
    child_alpha = savgol_filter(
        span.child_gyro_rads, filter_window, polynomial, deriv=1, delta=dt, axis=0,
    )
    index = np.arange(max(start, filter_window // 2), min(stop, n - filter_window // 2), 5, dtype=int)
    if len(index) < 12:
        return 0.0, 0.0, np.inf
    parent_k = np.asarray([
        skew(parent_alpha[row]) + skew(parent_gyro[row]) @ skew(parent_gyro[row])
        for row in index
    ])
    child_k = np.asarray([
        skew(child_alpha[row]) + skew(child_gyro[row]) @ skew(child_gyro[row])
        for row in index
    ])
    parent_local = span.parent_acc_mps2[index] + np.einsum(
        "nij,j->ni", parent_k, parent_center,
    )
    child_local = span.child_acc_mps2[index] + np.einsum(
        "nij,j->ni", child_k, child_center,
    )
    parent_world = np.einsum(
        "nij,nj->ni",
        qmt_wxyz_to_rotation(span.parent_quat_wxyz[index]).as_matrix(),
        parent_local,
    )
    child_world = np.einsum(
        "nij,nj->ni",
        qmt_wxyz_to_rotation(span.child_quat_wxyz[index]).as_matrix(),
        child_local,
    )
    # Candidate-independent excitation weights suppress static gravity and
    # noisy differentiated rows continuously rather than with a hard stop.
    dynamic = np.minimum(
        np.linalg.norm(parent_k, axis=(1, 2)),
        np.linalg.norm(child_k, axis=(1, 2)),
    )
    weight = np.clip(dynamic / max(float(np.percentile(dynamic, 75)), 1e-6), 0.0, 1.0)
    horizontal_energy = np.sqrt(
        np.sum(parent_world[:, :2] ** 2, axis=1)
        * np.sum(child_world[:, :2] ** 2, axis=1)
    )
    weight *= np.clip(horizontal_energy / 2.0, 0.0, 1.0)
    c = float(np.sum(weight * (
        child_world[:, 0] * parent_world[:, 0]
        + child_world[:, 1] * parent_world[:, 1]
    )))
    s = float(np.sum(weight * (
        child_world[:, 0] * parent_world[:, 1]
        - child_world[:, 1] * parent_world[:, 0]
    )))
    if c * c + s * s <= 1e-10 or np.sum(weight) < 2.0:
        return 0.0, 0.0, np.inf
    delta = float(math.atan2(s, c))
    cosine = math.cos(delta)
    sine = math.sin(delta)
    corrected_child = child_world.copy()
    corrected_child[:, 0] = cosine * child_world[:, 0] - sine * child_world[:, 1]
    corrected_child[:, 1] = sine * child_world[:, 0] + cosine * child_world[:, 1]
    residual = parent_world - corrected_child
    rms = float(np.sqrt(np.average(np.sum(residual * residual, axis=1), weights=np.maximum(weight, 1e-9))))
    baseline = float(np.sqrt(np.average(
        np.sum((parent_world - child_world) ** 2, axis=1),
        weights=np.maximum(weight, 1e-9),
    )))
    improvement = max(0.0, baseline - rms) / max(baseline, 0.25)
    support = float(np.clip(np.sum(weight) / 20.0, 0.0, 1.0))
    # A centre fitted on the same capture can be algebraically full rank yet
    # physically mismatch soft tissue or a non-rigid connection.  Propagate
    # that measured mismatch into heading information instead of allowing the
    # number of windows to overpower it.  Pelvis<->torso is a distributed,
    # flexible spine connection rather than a single rigid joint centre, so
    # its centre-closure evidence remains deliberately broad.
    centre_fit_rms = float(state.centers[span.edge].residual_rms_mps2())
    fit_reliability = float(
        math.exp(-0.5 * (centre_fit_rms / 1.5) ** 2)
        if np.isfinite(centre_fit_rms) else 0.0
    )
    if span.edge == "pelvis_torso":
        fit_reliability *= 0.20
    rating = float(np.clip(improvement * support * fit_reliability, 0.0, 1.0))
    center_sigma = float(np.sqrt(np.mean(np.maximum(covariance, 0.0))))
    variance = float(
        (math.radians(35.0) ** 2 + min(math.pi**2, 4.0 * center_sigma**2))
        / max(rating * max(np.sum(weight) / 12.0, 0.1), 0.05)
    )
    return delta, rating, variance


def attach_capture_wide_heading_streams(
    tape: FactorTape,
    state: PosteriorState,
) -> FactorTape:
    """Attach nine persistent, capture-wide heading streams to a raw tape.

    Each edge is processed once in physical chronology.  Episode boundaries
    delimit valid windows but never instantiate another heading state.  The
    returned per-span objects are slices of that one edge state, not standalone
    QMT calibrations.
    """
    span_lookup: dict[tuple[int, str, int], AlignedSpan] = {}
    for block in tape.episodes:
        for span in block.spans:
            span_lookup[(span.episode_index, span.edge, span.span_index)] = span
    factors: dict[tuple[int, str, int], HeadingStreamFactor] = {}
    for edge in EDGES:
        persistent = _PersistentHeadingState()
        edge_spans = sorted(
            (span for span in span_lookup.values() if span.edge == edge.name),
            key=lambda row: (row.time_root_s[0], row.episode_index, row.span_index),
        )
        for span in edge_spans:
            start_wall = time.perf_counter()
            parent_segment_q, child_segment_q = _span_segment_quaternions(span, state)
            measurement_time: list[float] = []
            filtered_delta: list[float] = []
            filtered_variance: list[float] = []
            ratings: list[float] = []
            sources: list[str] = []
            for begin, end in _heading_window_slices(len(span.time_root_s)):
                measurements: list[tuple[float, float, float, str]] = []
                if edge.joint_kind == "hinge":
                    delta, rating, variance = _hinge_heading_observation(
                        parent_segment_q[begin:end], child_segment_q[begin:end], persistent.delta_rad,
                    )
                    measurements.append((delta, rating, variance, "QMT_OFFICIAL_1D_WINDOW"))
                delta, rating, variance = _center_heading_observation(span, state, begin, end)
                measurements.append((delta, rating, variance, "SEEL_CENTER_VECTOR_CLOSURE_WINDOW"))
                valid = [row for row in measurements if row[1] > 0.0 and np.isfinite(row[2])]
                if valid:
                    precision = np.asarray([row[1] / max(row[2], 1e-9) for row in valid])
                    measurement = float(math.atan2(
                        np.sum(precision * np.sin([row[0] for row in valid])),
                        np.sum(precision * np.cos([row[0] for row in valid])),
                    ))
                    total_precision = float(np.sum(precision))
                    combined_rating = float(np.clip(np.mean([row[1] for row in valid]), 0.0, 1.0))
                    combined_variance = float(1.0 / max(total_precision, 1e-9))
                    source = "+".join(row[3] for row in valid)
                else:
                    measurement = persistent.delta_rad
                    combined_rating = 0.0
                    combined_variance = np.inf
                    source = "NO_INFORMATIVE_HEADING_UPDATE"
                update_time = float(span.time_root_s[min(end - 1, len(span.time_root_s) - 1)])
                value, variance_out = persistent.update(
                    update_time, measurement, combined_variance, combined_rating,
                )
                measurement_time.append(update_time)
                filtered_delta.append(value)
                filtered_variance.append(variance_out)
                ratings.append(combined_rating)
                sources.append(source)
            if not measurement_time:
                time_values = span.time_root_s.copy()
                delta_values = np.full(len(time_values), persistent.delta_rad, dtype=float)
                rating_values = np.zeros(len(time_values), dtype=float)
                state_values = np.full(len(time_values), 3 if persistent.initialized else 2, dtype=int)
                info = 0.0
                variance = np.inf
                status = "LOW_INFORMATION_CAPTURE_WIDE_STATE_PROPAGATED"
                source = "NO_INFORMATIVE_HEADING_UPDATE"
            else:
                mt = np.asarray(measurement_time, dtype=float)
                fd = np.unwrap(np.asarray(filtered_delta, dtype=float))
                time_values = span.time_root_s.copy()
                delta_values = wrap_pi(np.interp(time_values, mt, fd, left=fd[0], right=fd[-1]))
                rating_values = np.interp(
                    time_values, mt, np.asarray(ratings), left=ratings[0], right=ratings[-1],
                )
                state_values = np.where(rating_values > 0.0, 1, 3).astype(int)
                finite_variance = np.asarray(filtered_variance)[np.isfinite(filtered_variance)]
                variance = float(np.median(finite_variance)) if len(finite_variance) else np.inf
                info = float(1.0 / max(variance, 1e-9)) if np.isfinite(variance) else 0.0
                status = "PASS_CAPTURE_WIDE_PERSISTENT_STATE" if info > 0.0 else "LOW_INFORMATION_CAPTURE_WIDE_STATE_PROPAGATED"
                source = "+".join(sorted(set(sources)))
            raw_child_matrix = qmt_wxyz_to_rotation(span.child_quat_wxyz).as_matrix()
            correction = Rotation.from_rotvec(
                np.column_stack([
                    np.zeros(len(delta_values)),
                    np.zeros(len(delta_values)),
                    delta_values,
                ])
            ).as_matrix()
            corrected_child_matrix = correction @ raw_child_matrix
            corrected_child_quat = normalize_quat_wxyz(rotation_to_qmt_wxyz(
                Rotation.from_matrix(corrected_child_matrix)
            ))
            weights = np.maximum(rating_values, 1e-9)
            mean = float(math.atan2(
                np.sum(weights * np.sin(delta_values)),
                np.sum(weights * np.cos(delta_values)),
            ))
            factors[(span.episode_index, span.edge, span.span_index)] = HeadingStreamFactor(
                span.episode_index,
                span.qa_label,
                span.edge,
                span.span_index,
                status,
                float(time.perf_counter() - start_wall),
                len(span.time_root_s),
                time_values,
                corrected_child_quat,
                np.asarray(delta_values, dtype=float),
                np.asarray(rating_values, dtype=float),
                state_values,
                info,
                mean,
                variance,
                source,
                variance,
                None,
            )
    blocks = tuple(replace(
        block,
        headings=tuple(
            factors[(span.episode_index, span.edge, span.span_index)]
            for span in block.spans
        ),
    ) for block in tape.episodes)
    settings = dict(tape.qmt_settings)
    settings["capture_wide_heading_owner"] = {
        "schema": "biospur-c2-capture-wide-persistent-heading-v1",
        "state_count": 9,
        "episode_reset_count": 0,
        "per_action_yaw_gauge_count": 0,
        "window_boundaries_may_cross_episode_gap": False,
        "gap_policy": "PROPAGATE_STATE_AND_COVARIANCE_WITHOUT_MEASUREMENT",
        "qmt_primitive": "qmt.functions.heading_correction.estimateDelta1d_unmodified",
        "nonhinge_mechanism": "SEEL_STYLE_COMMON_JOINT_VECTOR_CLOSURE",
    }
    alignment = dict(tape.alignment_audit)
    alignment.update({
        "heading_state_count": 9,
        "heading_episode_reset_count": 0,
        "per_action_heading_stitching": False,
    })
    return replace(tape, episodes=blocks, qmt_settings=settings, alignment_audit=alignment)


def build_factor_tape(episodes: Sequence[EpisodeFrontend]) -> FactorTape:
    start = time.perf_counter()
    blocks: list[EpisodeFactorBlock] = []
    alignment_audit: dict[str, Any] = {
        "orientation_arrays_only": True,
        "replay_input_arrays_consumed": False,
        "old_fit_state_consumed": False,
        "action_labels_used_for_factor_routing": False,
        "local_time_rebase_used": False,
        "gap_interpolation_used": False,
        "rows_across_gap_aligned": 0,
        "span_count_by_episode_edge": {},
    }
    for episode in episodes:
        spans = aligned_spans_for_episode(episode)
        alignment_audit["span_count_by_episode_edge"][str(episode.chronological_index)] = {
            edge.name: sum(1 for span in spans if span.edge == edge.name)
            for edge in EDGES
        }
        windows: list[WindowFactor] = []
        # This first tape owns raw, state-independent evidence only.  A hinge
        # axis or centre fitted here would be an episode-local calibration and
        # averaging those fitted answers later is not a progressive joint fit.
        # ``refit_cumulative_calibration_tape`` below instead relinearizes one
        # shared body calibration against every raw span seen so far.
        hinges: list[HingeAxisFactor] = []
        centers: list[CenterFactor] = []
        headings: list[HeadingStreamFactor] = []
        for span in spans:
            span_windows = windows_for_span(span)
            windows.extend(span_windows)
            # Heading is deliberately attached only after the cumulative
            # centre/axis state exists.  Calling QMT here would create one
            # independent startup and yaw gauge per episode span.
        blocks.append(EpisodeFactorBlock(
            episode.chronological_index,
            episode.qa_label,
            spans,
            tuple(windows),
            tuple(hinges),
            tuple(centers),
            tuple(headings),
        ))
    return FactorTape(
        schema="biospur-c2-coupled-progressive-factor-tape-v2",
        created_wall_s=float(time.perf_counter() - start),
        episodes=tuple(blocks),
        alignment_audit=alignment_audit,
        qmt_settings={
            "headingCorrection": {
                "startRating": 0.0,
                "stillnessRating": 0.0,
                "useRomConstraints": False,
                "deltaRange": [0.0],
                "windowTime": 2.0,
                "estimationRate": 2.0,
                "input_rate_hz": 200.0,
                "qmt_rate_hz": 40.0,
                "deterministic_decimation": 5,
                "full_span_stream_interpolated_for_fk": True,
            },
            "jointAxisEstHingeOlsson": {
                "combined_acc_gyro": True,
                "useSampleSelection": True,
                "dataSize": 800,
                "w0": 50.0,
            },
        },
    )


def _cumulative_edge_span(
    evidence: Sequence[tuple[AlignedSpan, Sequence[WindowFactor]]],
    *,
    prefix_index: int,
) -> tuple[AlignedSpan, tuple[WindowFactor, ...]]:
    """Concatenate one edge's retained raw evidence without crossing gaps.

    The Olsson objective treats rows as paired observations, so concatenation
    does not invent an inter-episode time interval.  Centre derivatives do use
    local neighbourhoods; shifted windows therefore exclude twelve samples on
    either side of every original span boundary.  No row in a gap is created.
    """
    if not evidence:
        raise ValueError("cumulative edge refit requires at least one span")
    ordered = sorted(
        evidence,
        key=lambda row: (
            row[0].episode_index,
            float(row[0].time_root_s[0]),
            row[0].span_index,
        ),
    )
    spans = [row[0] for row in ordered]
    first = spans[0]
    total = sum(len(span.time_root_s) for span in spans)
    synthetic_time = np.arange(total, dtype=float) * 0.005
    shifted_windows: list[WindowFactor] = []
    offset = 0
    boundary_guard = 12
    for span, windows in ordered:
        span_rows = len(span.time_root_s)
        for window in windows:
            start = max(int(window.start), boundary_guard)
            stop = min(int(window.stop), span_rows - boundary_guard)
            if stop - start < 12:
                continue
            shifted_windows.append(WindowFactor(
                prefix_index,
                first.edge,
                0,
                offset + start,
                offset + stop,
                float(window.weight),
                float(window.rel_gyro_trace),
                float(window.dyn_acc_trace),
                float(window.valid_fraction),
            ))
        offset += span_rows

    def rows(name: str) -> np.ndarray:
        return np.concatenate([np.asarray(getattr(span, name)) for span in spans], axis=0)

    cumulative = AlignedSpan(
        episode_index=prefix_index,
        qa_label=f"CUMULATIVE_RAW_PREFIX_00_TO_{prefix_index:02d}",
        edge=first.edge,
        span_index=0,
        parent_segment=first.parent_segment,
        child_segment=first.child_segment,
        parent_indices=np.arange(total, dtype=int),
        child_indices=np.arange(total, dtype=int),
        time_root_s=synthetic_time,
        parent_time_s=synthetic_time.copy(),
        child_time_s=synthetic_time.copy(),
        parent_acc_mps2=rows("parent_acc_mps2"),
        child_acc_mps2=rows("child_acc_mps2"),
        parent_gyro_rads=rows("parent_gyro_rads"),
        child_gyro_rads=rows("child_gyro_rads"),
        parent_quat_wxyz=rows("parent_quat_wxyz"),
        child_quat_wxyz=rows("child_quat_wxyz"),
        lag_uncertainty_s=float(max(span.lag_uncertainty_s for span in spans)),
        alignment_status="CUMULATIVE_RAW_ROWS_NO_GAP_INTERPOLATION",
        timing_audit={
            "source_span_count": len(spans),
            "source_episode_count": len({span.episode_index for span in spans}),
            "raw_row_count": total,
            "rows_across_gap_aligned": 0,
            "boundary_guard_rows_per_side": boundary_guard,
        },
    )
    return cumulative, tuple(shifted_windows)


def refit_cumulative_calibration_tape(tape: FactorTape) -> FactorTape:
    """Fit one shared body calibration at every chronological prefix.

    Each block contains a relinearization against *all raw rows retained up to
    that prefix*.  It is not an increment and must replace, not be added to,
    the previous axes/centres.  Original per-episode spans remain attached so
    the persistent heading filter and direct trajectory keep physical time.
    """
    start = time.perf_counter()
    history: dict[str, list[tuple[AlignedSpan, Sequence[WindowFactor]]]] = {
        edge.name: [] for edge in EDGES
    }
    blocks: list[EpisodeFactorBlock] = []
    for prefix_index, block in enumerate(tape.episodes):
        windows_by_span: dict[tuple[str, int], list[WindowFactor]] = {}
        for window in block.windows:
            windows_by_span.setdefault((window.edge, window.span_index), []).append(window)
        for span in block.spans:
            history[span.edge].append((
                span,
                tuple(windows_by_span.get((span.edge, span.span_index), ())),
            ))
        hinges: list[HingeAxisFactor] = []
        centers: list[CenterFactor] = []
        for edge in EDGES:
            cumulative_span, cumulative_windows = _cumulative_edge_span(
                history[edge.name], prefix_index=prefix_index,
            )
            if edge.joint_kind == "hinge":
                hinges.append(hinge_axis_factor(cumulative_span, cumulative_windows))
            centers.append(center_factor(cumulative_span, cumulative_windows))
        blocks.append(replace(
            block,
            hinge_axes=tuple(hinges),
            centers=tuple(centers),
        ))
    settings = dict(tape.qmt_settings)
    settings["calibration_owner"] = {
        "schema": "biospur-c2-genuine-cumulative-calibration-v1",
        "state_count": 1,
        "prefix_count": len(blocks),
        "episode_local_axis_or_center_estimates_added": False,
        "prefix_refit_source": "ALL_RETAINED_RAW_ROWS_FROM_00_THROUGH_PREFIX",
        "new_prefix_semantics": "REPLACE_SHARED_LINEARIZATION_NOT_ADD_LOCAL_SOLUTION",
        "final_refinement": "ONE_ALL_EPISODE_CUMULATIVE_REFIT",
        "rows_across_gap_aligned": 0,
        "wall_s": float(time.perf_counter() - start),
    }
    alignment = dict(tape.alignment_audit)
    alignment.update({
        "episode_local_axis_factor_count": 0,
        "episode_local_center_factor_count": 0,
        "cumulative_prefix_refit_count": len(blocks),
        "single_persistent_body_calibration_state": True,
    })
    return replace(
        tape,
        schema="biospur-c2-genuine-cumulative-factor-tape-v3",
        episodes=tuple(blocks),
        qmt_settings=settings,
        alignment_audit=alignment,
    )


def prior_state() -> PosteriorState:
    state = PosteriorState()
    state.mounts = {segment: _broad_mount(segment) for segment in SEGMENTS}
    state.branch_candidates = branch_candidates_after_physical_gate(state)
    state.pelvis_yaw_gauge_rad = 0.0
    return state


def _broad_mount(segment: str) -> MountPosterior:
    return MountPosterior(
        sensor_from_segment_mean=_broad_wear_prior_matrix(segment),
        covariance_rad2=np.array([2.5, 2.5, 9.87], dtype=float),
        information=0.0,
        rank=0,
        evidence=("BROAD_WEAR_LIKELIHOOD_ONLY_NOT_EXACT_MOUNT",),
    )


def _broad_wear_prior_matrix(segment: str) -> np.ndarray:
    """Mean of the qualitative C2 wear-direction likelihood.

    The matrix is only a broad prior mean; :func:`_broad_mount` deliberately
    assigns it rank zero and very large twist covariance.  Unlike the previous
    implementation, every declared ``sensor -Z`` region participates in this
    mean instead of being silently ignored for forearms, shanks and upper arms.
    """
    z_sensor = np.array([0.0, 1.0, 0.0])
    sensor_x = np.array([1.0, 0.0, 0.0])
    sensor_z = np.array([0.0, 0.0, 1.0])
    if segment in {"forearm_left", "shank_left"}:
        x_sensor = sensor_z
    elif segment in {"forearm_right", "shank_right"}:
        x_sensor = -sensor_z
    elif segment == "upper_arm_left":
        # sensor -Z is left/rear with posterior dominance.  The 30-degree
        # lateral component is a broad mean, not a mechanical fixture.
        x_sensor = -math.cos(math.radians(30.0)) * sensor_x + 0.5 * sensor_z
    elif segment == "upper_arm_right":
        x_sensor = -math.cos(math.radians(30.0)) * sensor_x - 0.5 * sensor_z
    else:
        # sensor -Z approximately forward for torso, pelvis and thighs.
        x_sensor = sensor_x
    x_sensor = unit(x_sensor - z_sensor * np.dot(x_sensor, z_sensor))
    y_sensor = unit(np.cross(z_sensor, x_sensor))
    frame = np.column_stack([x_sensor, y_sensor, z_sensor])
    if np.linalg.det(frame) < 0.0:
        frame[:, 1] *= -1.0
    return frame


def _broad_axis_hint(edge: str) -> np.ndarray:
    if "left" in edge:
        return np.array([-1.0, 0.0, 0.0])
    if "right" in edge:
        return np.array([1.0, 0.0, 0.0])
    return np.array([1.0, 0.0, 0.0])


def _center_vector(state: PosteriorState, edge: str, side: str) -> tuple[np.ndarray, float, int]:
    parent, child, rank, _cov = state.centers[edge].solve()
    return (parent if side == "parent" else child), float(state.centers[edge].information), rank


def _limb_frame_from_vectors(
    segment: str,
    proximal: tuple[np.ndarray, float, int] | None,
    distal: tuple[np.ndarray, float, int] | None,
    hinge_axis: tuple[np.ndarray, float] | None,
) -> MountPosterior:
    prior = _broad_mount(segment)
    evidence: list[str] = []
    info = 0.0
    nominal_length = {
        "upper_arm_left": 0.3175,
        "upper_arm_right": 0.3175,
        "thigh_left": 0.480,
        "thigh_right": 0.480,
    }.get(segment)
    center_difference = (
        np.asarray(proximal[0]) - np.asarray(distal[0])
        if proximal is not None and distal is not None
        else None
    )
    center_pair_available = bool(
        center_difference is not None
        and proximal is not None and distal is not None
        and proximal[1] > 0.0 and distal[1] > 0.0
        and nominal_length is not None
    )
    prior_z = prior.sensor_from_segment_mean[:, 2]
    center_pair_geometry_qualified = False
    if center_pair_available:
        measured_length = float(np.linalg.norm(center_difference))
        ratio = measured_length / max(float(nominal_length), 1e-9)
        pair_info = float(min(proximal[1], distal[1]))
        # This is a continuous likelihood, not a hard robot-link equality.
        # The 35%--140% binary gate in RUN_010 let a half-length thigh centre
        # pair become an exact long axis.  A log-normal human/landmark scale
        # keeps mildly imperfect functional estimates useful while smoothly
        # returning gross conflicts to the broad wear distribution.
        length_likelihood = float(math.exp(-0.5 * (math.log(max(ratio, 1e-6)) / 0.22) ** 2))
        information_likelihood = float(pair_info / (pair_info + 2.0))
        centre_weight = float(np.clip(length_likelihood * information_likelihood, 0.0, 0.85))
        measured_z = unit(center_difference, prior_z)
        if float(np.dot(measured_z, prior_z)) < 0.0:
            measured_z = -measured_z
        z_axis = unit((1.0 - centre_weight) * prior_z + centre_weight * measured_z, prior_z)
        info += centre_weight * pair_info
        center_pair_geometry_qualified = bool(centre_weight > 0.20)
        evidence.append(
            "TWO_CENTER_LONG_AXIS_CONTINUOUS_BONE_LENGTH_INFORMATION_BLEND_"
            f"WEIGHT_{centre_weight:.3f}_RATIO_{ratio:.3f}"
        )
    elif proximal is not None and proximal[1] > 1.0:
        measured_z = unit(proximal[0], prior_z)
        if float(np.dot(measured_z, prior_z)) < 0.0:
            measured_z = -measured_z
        centre_weight = float(np.clip(proximal[1] / (proximal[1] + 6.0), 0.0, 0.45))
        z_axis = unit((1.0 - centre_weight) * prior_z + centre_weight * measured_z, prior_z)
        info += centre_weight * proximal[1]
        evidence.append(f"ONE_CENTER_VECTOR_CONTINUOUS_BLEND_WEIGHT_{centre_weight:.3f}")
    elif distal is not None and distal[1] > 1.0:
        measured_z = -unit(distal[0], prior_z)
        if float(np.dot(measured_z, prior_z)) < 0.0:
            measured_z = -measured_z
        centre_weight = float(np.clip(distal[1] / (distal[1] + 6.0), 0.0, 0.45))
        z_axis = unit((1.0 - centre_weight) * prior_z + centre_weight * measured_z, prior_z)
        info += centre_weight * distal[1]
        evidence.append(f"ONE_CENTER_VECTOR_CONTINUOUS_BLEND_WEIGHT_{centre_weight:.3f}")
    else:
        z_axis = prior_z
        evidence.append("LONG_AXIS_UNOBSERVED_BROAD_PRIOR")
    if hinge_axis is not None and hinge_axis[1] > 0.0:
        x0 = unit(hinge_axis[0])
        prior_x = prior.sensor_from_segment_mean[:, 0]
        # A functional hinge is an unoriented line.  Choosing its sign is a
        # coordinate convention, not anatomical evidence; retain the broad
        # wear hemisphere instead of inheriting numpy eigensolver sign noise.
        if float(np.dot(x0, prior_x)) < 0.0:
            x0 = -x0
        info += hinge_axis[1]
        evidence.append("HINGE_LINE_DEFINES_TRANSVERSE_SUBSPACE_SIGN_ALIGNED_TO_SOFT_WEAR_HEMISPHERE")
    else:
        x0 = prior.sensor_from_segment_mean[:, 0]
        evidence.append("TRANSVERSE_TWIST_UNOBSERVED_BROAD_PRIOR")
    x_axis = unit(x0 - z_axis * np.dot(x0, z_axis), prior.sensor_from_segment_mean[:, 0])
    y_axis = unit(np.cross(z_axis, x_axis), prior.sensor_from_segment_mean[:, 1])
    frame = np.column_stack([x_axis, y_axis, z_axis])
    if np.linalg.det(frame) < 0.0:
        y_axis *= -1.0
        frame = np.column_stack([x_axis, y_axis, z_axis])
    rank = min(3, int(info > 0.5) + int(info > 2.0) + int(center_pair_geometry_qualified and hinge_axis is not None))
    covariance = np.array([
        1.0 / max(info, 0.2),
        1.0 / max(info, 0.2),
        9.87 if "TRANSVERSE_TWIST_UNOBSERVED_BROAD_PRIOR" in evidence else 1.0 / max(info, 0.2),
    ])
    return MountPosterior(frame, covariance, float(info), rank, tuple(evidence))


def estimate_mount_posteriors(state: PosteriorState) -> dict[str, MountPosterior]:
    out = {segment: _broad_mount(segment) for segment in SEGMENTS}
    for side in ("left", "right"):
        elbow = f"elbow_{side}"
        knee = f"knee_{side}"
        shoulder = f"shoulder_{side}"
        hip = f"hip_{side}"
        p_axis, c_axis, _, _ = state.hinges[elbow].axes()
        out[f"upper_arm_{side}"] = _limb_frame_from_vectors(
            f"upper_arm_{side}",
            _center_vector(state, shoulder, "child"),
            _center_vector(state, elbow, "parent"),
            (p_axis, state.hinges[elbow].information),
        )
        out[f"forearm_{side}"] = _limb_frame_from_vectors(
            f"forearm_{side}",
            _center_vector(state, elbow, "child"),
            None,
            (c_axis, state.hinges[elbow].information),
        )
        p_axis, c_axis, _, _ = state.hinges[knee].axes()
        out[f"thigh_{side}"] = _limb_frame_from_vectors(
            f"thigh_{side}",
            _center_vector(state, hip, "child"),
            _center_vector(state, knee, "parent"),
            (p_axis, state.hinges[knee].information),
        )
        out[f"shank_{side}"] = _limb_frame_from_vectors(
            f"shank_{side}",
            _center_vector(state, knee, "child"),
            None,
            (c_axis, state.hinges[knee].information),
        )
    # A single surface-mounted pelvis<->torso centre vector is not either
    # segment's anatomical long axis.  Treating it as one caused the historical
    # folded/leaning trunk.  Their long-axis/twist means therefore remain the
    # broad wear likelihood until multiple independent connections constrain
    # them jointly; the fitted centre vectors remain available to heading and
    # connection factors.
    out["torso"] = _broad_mount("torso")
    out["pelvis"] = _broad_mount("pelvis")
    return out


def branch_weight_vector(hinges: dict[str, HingePosterior]) -> np.ndarray:
    weights = np.ones(16, dtype=float)
    for bit, edge in enumerate(HINGE_NAMES):
        p = hinges[edge].sign_probability()
        for index in range(16):
            weights[index] *= p if ((index >> bit) & 1) else (1.0 - p)
    total = float(np.sum(weights))
    return weights / total if total > 0.0 else np.full(16, 1.0 / 16.0)


def _default_branch_qa(branch_id: int, weight: float) -> dict[str, Any]:
    return {
        "branch_id": branch_id,
        "checked_before_residual_ranking": False,
        "standing_upright_topology": None,
        "front_back_knee_split": None,
        "crossing": None,
        "collapse": None,
        "connected": None,
        "gravity_feasible": None,
        "rom_feasible": None,
        "reason": "UNASSESSED_UNTIL_BRANCH_SPECIFIC_TRAJECTORY_EXISTS",
        "weight_before_gate": weight,
    }


def branch_candidates_after_physical_gate(state: PosteriorState) -> list[BranchCandidate]:
    base = branch_weight_vector(state.hinges)
    rows: list[BranchCandidate] = []
    for branch_id, weight in enumerate(base):
        signs = {edge: (1 if ((branch_id >> bit) & 1) else -1) for bit, edge in enumerate(HINGE_NAMES)}
        qa = _default_branch_qa(branch_id, float(weight))
        # Keep the probability mass while it is unresolved, but never label an
        # unevaluated branch physically valid.  RUN_006 hard-coded every field
        # above to a passing value and therefore let invalid pixels claim PASS.
        rows.append(BranchCandidate(branch_id, signs, float(weight), False, qa, float(weight)))
    total = sum(row.weight for row in rows)
    if total > 0.0:
        for row in rows:
            row.weight = float(row.weight / total)
    return rows


def _broad_replacement(segment: str, previous: MountPosterior, reason: str) -> MountPosterior:
    broad = _broad_mount(segment)
    return MountPosterior(
        broad.sensor_from_segment_mean,
        np.maximum(broad.covariance_rad2, previous.covariance_rad2),
        0.0,
        0,
        tuple(list(previous.evidence) + [reason]),
    )


def _candidate_mounts_with_fallbacks(
    mounts: dict[str, MountPosterior],
    fallback_segments: Sequence[str],
    reason: str,
) -> dict[str, MountPosterior]:
    out = dict(mounts)
    for segment in fallback_segments:
        out[segment] = _broad_replacement(segment, out.get(segment, _broad_mount(segment)), reason)
    return out


def _standing_mean_segment_matrices(
    mounts: dict[str, MountPosterior],
    episode: EpisodeFrontend,
    *,
    yaw_rad: float,
) -> dict[str, np.ndarray]:
    gauge = Rotation.from_euler("z", yaw_rad).as_matrix()
    out: dict[str, np.ndarray] = {}
    for segment in SEGMENTS:
        series = _series_for_segment(episode, segment)
        n = min(500, len(series.quat_world_sensor_wxyz))
        sensor_world = qmt_wxyz_to_rotation(series.quat_world_sensor_wxyz[:n]).as_matrix()
        segment_world = sensor_world @ mounts[segment].sensor_from_segment_mean
        out[segment] = gauge @ mean_rotation_matrix(segment_world)
    return out


def apply_initial_still_gravity_tilt_update(
    state: PosteriorState,
    episodes: Sequence[EpisodeFrontend],
) -> dict[str, Any]:
    """Use initial rest for tilt only, never yaw or anatomical twist.

    The declared sensor ``-Y`` ground direction is approximate.  Treating it
    as the exact segment long axis maps a 7--25 degree surface-mount tilt into
    every reconstructed motion.  Initial stillness directly observes gravity
    in each sensor frame; a broad natural-rest model can therefore update the
    two long-axis tilt degrees of freedom.  Rotation about that axis remains
    exactly as supplied by functional evidence / its broad wear likelihood.
    """
    if not episodes:
        raise ValueError("gravity tilt update requires the C2 episode list")
    episode = episodes[0]
    # Rough uncertainty ratio: qualitative wear direction ~25 degrees and a
    # natural human rest long axis ~14 degrees.  The resulting 0.76 weight is
    # intentionally soft and does not exactize a human standing pose.
    wear_sigma = math.radians(25.0)
    rest_sigma = math.radians(14.0)
    gravity_weight = float(
        (1.0 / rest_sigma**2) / (1.0 / rest_sigma**2 + 1.0 / wear_sigma**2)
    )
    rows: dict[str, Any] = {}
    world_up = np.array([0.0, 0.0, 1.0])
    for segment in SEGMENTS:
        mount = state.mounts.get(segment, _broad_mount(segment))
        series = _series_for_segment(episode, segment)
        n = min(500, len(series.quat_world_sensor_wxyz))
        sensor_world = qmt_wxyz_to_rotation(series.quat_world_sensor_wxyz[:n]).as_matrix()
        observed_up_sensor = unit(np.mean(
            np.einsum("nji,j->ni", sensor_world, world_up), axis=0,
        ))
        old_z = unit(mount.sensor_from_segment_mean[:, 2])
        if float(np.dot(observed_up_sensor, old_z)) < 0.0:
            observed_up_sensor = -observed_up_sensor
        new_z = unit(
            (1.0 - gravity_weight) * old_z + gravity_weight * observed_up_sensor,
            old_z,
        )
        # Preserve the existing functional transverse direction as far as
        # orthogonality permits.  This operation introduces no axial twist.
        old_x = unit(mount.sensor_from_segment_mean[:, 0])
        new_x = unit(old_x - new_z * np.dot(old_x, new_z), old_x)
        new_y = unit(np.cross(new_z, new_x), mount.sensor_from_segment_mean[:, 1])
        matrix = np.column_stack([new_x, new_y, new_z])
        if np.linalg.det(matrix) < 0.0:
            matrix[:, 1] *= -1.0
        before_angle = float(math.acos(np.clip(np.dot(old_z, observed_up_sensor), -1.0, 1.0)))
        after_angle = float(math.acos(np.clip(np.dot(new_z, observed_up_sensor), -1.0, 1.0)))
        # Tilt is observed, axial twist is not.  Keep the latter variance from
        # the preceding state and never claim a complete mount rotation.
        covariance = np.asarray(mount.covariance_rad2, dtype=float).copy()
        covariance[:2] = np.maximum(
            covariance[:2] * (1.0 - gravity_weight),
            rest_sigma**2,
        )
        covariance[2] = max(float(covariance[2]), math.radians(55.0) ** 2)
        state.mounts[segment] = MountPosterior(
            matrix,
            covariance,
            float(mount.information + 2.0 / rest_sigma**2),
            min(3, mount.rank + 2),
            tuple(list(mount.evidence) + [
                "INITIAL_STILL_GRAVITY_TILT_SOFT_UPDATE_NOT_YAW_OR_AXIAL_TWIST",
            ]),
        )
        rows[segment] = {
            "rows": n,
            "observed_world_up_in_sensor": observed_up_sensor.tolist(),
            "before_disagreement_deg": math.degrees(before_angle),
            "after_disagreement_deg": math.degrees(after_angle),
            "gravity_weight": gravity_weight,
            "yaw_or_axial_twist_updated": False,
        }
    state.branch_candidates = branch_candidates_after_physical_gate(state)
    return {
        "schema": "biospur-c2-initial-still-gravity-tilt-soft-update-v1",
        "status": "PASS",
        "initial_still_information_owner": "GRAVITY_TILT_ONLY",
        "natural_rest_is_not_exact_pose_truth": True,
        "yaw_axis_centres_lengths_or_sagittal_branch_estimated": False,
        "rows": rows,
    }


def _standing_proxy_joints(matrices: dict[str, np.ndarray], torso_height_m: float, hip_span_m: float) -> dict[str, np.ndarray]:
    cfg = load_effective_config()
    geom = cfg["proxy_geometry"]
    length = {
        "upper_arm_left": float(geom["upper_arm_left_m"]["nominal"]),
        "upper_arm_right": float(geom["upper_arm_right_m"]["nominal"]),
        "forearm_left": float(geom["forearm_left_m"]["nominal"]),
        "forearm_right": float(geom["forearm_right_m"]["nominal"]),
        "thigh_left": float(geom["thigh_left_m"]["nominal"]),
        "thigh_right": float(geom["thigh_right_m"]["nominal"]),
        "shank_left": float(geom["shank_left_m"]["nominal"]),
        "shank_right": float(geom["shank_right_m"]["nominal"]),
        "shoulder_span": float(geom["acromion_proxy_span_m"]["nominal"]),
    }
    root = np.zeros(3, dtype=float)
    shoulder_mid = root + matrices["torso"] @ np.array([0.0, 0.0, torso_height_m])
    shoulder_left = shoulder_mid + matrices["torso"] @ np.array([-0.5 * length["shoulder_span"], 0.0, 0.0])
    shoulder_right = shoulder_mid + matrices["torso"] @ np.array([0.5 * length["shoulder_span"], 0.0, 0.0])
    hip_left = root + matrices["pelvis"] @ np.array([-0.5 * hip_span_m, 0.0, 0.0])
    hip_right = root + matrices["pelvis"] @ np.array([0.5 * hip_span_m, 0.0, 0.0])
    elbow_left = shoulder_left + matrices["upper_arm_left"] @ np.array([0.0, 0.0, -length["upper_arm_left"]])
    wrist_left = elbow_left + matrices["forearm_left"] @ np.array([0.0, 0.0, -length["forearm_left"]])
    elbow_right = shoulder_right + matrices["upper_arm_right"] @ np.array([0.0, 0.0, -length["upper_arm_right"]])
    wrist_right = elbow_right + matrices["forearm_right"] @ np.array([0.0, 0.0, -length["forearm_right"]])
    knee_left = hip_left + matrices["thigh_left"] @ np.array([0.0, 0.0, -length["thigh_left"]])
    ankle_left = knee_left + matrices["shank_left"] @ np.array([0.0, 0.0, -length["shank_left"]])
    knee_right = hip_right + matrices["thigh_right"] @ np.array([0.0, 0.0, -length["thigh_right"]])
    ankle_right = knee_right + matrices["shank_right"] @ np.array([0.0, 0.0, -length["shank_right"]])
    return {
        "pelvis_center": root,
        "shoulder_mid": shoulder_mid,
        "shoulder_left": shoulder_left,
        "shoulder_right": shoulder_right,
        "hip_left": hip_left,
        "hip_right": hip_right,
        "elbow_left": elbow_left,
        "wrist_left": wrist_left,
        "elbow_right": elbow_right,
        "wrist_right": wrist_right,
        "knee_left": knee_left,
        "ankle_left": ankle_left,
        "knee_right": knee_right,
        "ankle_right": ankle_right,
    }


def _standing_proxy_qa(joints: dict[str, np.ndarray]) -> dict[str, Any]:
    line_pairs = [
        ("pelvis_center", "shoulder_mid"),
        ("shoulder_left", "shoulder_right"),
        ("hip_left", "hip_right"),
        ("shoulder_left", "elbow_left"),
        ("elbow_left", "wrist_left"),
        ("shoulder_right", "elbow_right"),
        ("elbow_right", "wrist_right"),
        ("hip_left", "knee_left"),
        ("knee_left", "ankle_left"),
        ("hip_right", "knee_right"),
        ("knee_right", "ankle_right"),
    ]
    lengths = [float(np.linalg.norm(joints[b] - joints[a])) for a, b in line_pairs]
    left_order = bool(joints["shoulder_left"][2] > joints["hip_left"][2] > joints["knee_left"][2] > joints["ankle_left"][2])
    right_order = bool(joints["shoulder_right"][2] > joints["hip_right"][2] > joints["knee_right"][2] > joints["ankle_right"][2])
    left_right = bool(
        joints["shoulder_left"][0] < joints["shoulder_right"][0]
        and joints["hip_left"][0] < joints["hip_right"][0]
        and joints["knee_left"][0] < joints["knee_right"][0]
        and joints["ankle_left"][0] < joints["ankle_right"][0]
    )
    knee_forward = np.array([
        joints["knee_left"][1] - joints["hip_left"][1],
        joints["knee_right"][1] - joints["hip_right"][1],
    ])
    knee_split = bool(knee_forward[0] * knee_forward[1] < -0.02)
    collapse = bool(min(lengths) < 0.05)
    return {
        "pass": bool(left_order and right_order and left_right and not knee_split and not collapse),
        "shoulders_above_hips_above_knees_above_ankles_left": left_order,
        "shoulders_above_hips_above_knees_above_ankles_right": right_order,
        "left_right_order": left_right,
        "front_back_knee_split": knee_split,
        "collapse": collapse,
        "minimum_segment_length_m": float(min(lengths)),
        "knee_forward_offsets_m": knee_forward.tolist(),
    }


def _standing_topology_candidates(
    mounts: dict[str, MountPosterior],
    episode: EpisodeFrontend,
) -> list[dict[str, Any]]:
    cfg = load_effective_config()
    torso_models = [float(v) for v in cfg["proxy_geometry"]["torso_display_geometry"]["models_m"]]
    hip_models = [0.18, 0.23, 0.28]
    # A physical gate judges a fitted state; it must not search over replacing
    # whichever body parts make a picture pass.  Gravity-rejected individual
    # mounts may already have reverted to their declared broad uncertainty
    # above, but topology never performs a second result-seeking repair.
    fallback_options = [()]
    yaw_options = (0.0, math.pi, 0.5 * math.pi, -0.5 * math.pi)
    rows: list[dict[str, Any]] = []
    for fallback_segments in fallback_options:
        candidate_mounts = _candidate_mounts_with_fallbacks(
            mounts,
            fallback_segments,
            "PHYSICAL_STANDING_TOPOLOGY_GATE_REJECTED_CENTER_MOUNT_BEFORE_RENDER",
        )
        for yaw in yaw_options:
            matrices = _standing_mean_segment_matrices(candidate_mounts, episode, yaw_rad=yaw)
            qas = []
            for torso, hip in zip(torso_models, hip_models, strict=True):
                qas.append(_standing_proxy_qa(_standing_proxy_joints(matrices, torso, hip)))
            score = sum(1 for qa in qas if qa["pass"])
            rows.append({
                "fallback_segments": list(fallback_segments),
                "pelvis_yaw_gauge_rad": float(yaw),
                "pass_all_display_models": bool(score == len(qas)),
                "pass_count": int(score),
                "qa": qas,
            })
    rows.sort(key=lambda row: (
        not row["pass_all_display_models"],
        len(row["fallback_segments"]),
        abs(float(row["pelvis_yaw_gauge_rad"])),
        -row["pass_count"],
    ))
    return rows


def apply_standing_gravity_mount_gate(
    state: PosteriorState,
    episodes: Sequence[EpisodeFrontend],
    *,
    minimum_world_z: float = 0.15,
) -> dict[str, Any]:
    """Reject mount candidates that invert standing segment long axes.

    Initial stillness contributes only gravity/topology feasibility. It does
    not estimate anatomical yaw or exact twist; rejected mounts fall back to
    broad covariance/rank-zero wear state. A single capture-wide pelvis yaw
    coordinate convention may be selected, but it carries no information rank.
    """

    if not episodes:
        raise ValueError("mount physical gate requires the C2 episode list")
    episode = episodes[0]
    rows: dict[str, Any] = {}
    for segment in SEGMENTS:
        mount = state.mounts.get(segment, _broad_mount(segment))
        series = _series_for_segment(episode, segment)
        n = min(500, len(series.quat_world_sensor_wxyz))
        sensor_world = qmt_wxyz_to_rotation(series.quat_world_sensor_wxyz[:n]).as_matrix()
        candidate_z = np.einsum("nij,j->ni", sensor_world, mount.sensor_from_segment_mean[:, 2]).mean(axis=0)
        candidate_z = unit(candidate_z)
        broad = _broad_mount(segment)
        broad_z = np.einsum("nij,j->ni", sensor_world, broad.sensor_from_segment_mean[:, 2]).mean(axis=0)
        broad_z = unit(broad_z)
        accepted = bool(candidate_z[2] >= minimum_world_z)
        if not accepted and broad_z[2] >= minimum_world_z:
            state.mounts[segment] = _broad_replacement(segment, mount, "PHYSICAL_GRAVITY_GATE_REJECTED_CENTER_MOUNT_BEFORE_RENDER")
        rows[segment] = {
            "candidate_world_plus_z": candidate_z.tolist(),
            "broad_world_plus_z": broad_z.tolist(),
            "accepted": accepted,
            "fallback_to_broad_uncertain_prior": bool(not accepted and broad_z[2] >= minimum_world_z),
            "minimum_world_z": minimum_world_z,
        }
    topology_candidates = _standing_topology_candidates(state.mounts, episode)
    selected_topology = topology_candidates[0]
    if selected_topology["pass_all_display_models"]:
        state.pelvis_yaw_gauge_rad = float(selected_topology["pelvis_yaw_gauge_rad"])
    state.branch_candidates = branch_candidates_after_physical_gate(state)
    return {
        "schema": "biospur-c2-coupled-progressive-standing-gravity-mount-gate-v1",
        "status": "PASS" if (
            all(row["accepted"] or row["fallback_to_broad_uncertain_prior"] for row in rows.values())
            and selected_topology["pass_all_display_models"]
        ) else "FAIL",
        "initial_still_used_for": "gravity_topology_feasibility_only",
        "exact_mount_or_yaw_estimated_from_still": False,
        "single_capture_wide_pelvis_yaw_gauge_selected": True,
        "pelvis_yaw_gauge_is_coordinate_convention_not_posterior_evidence": True,
        "standing_topology_selected": selected_topology,
        "standing_topology_candidates": topology_candidates,
        "rows": rows,
    }


def _predictive_nll_heading(state: PosteriorState, factor: HeadingStreamFactor) -> float:
    if factor.information <= 0.0:
        return 0.0
    prior = state.headings[factor.edge]
    residual = float(wrap_pi(factor.mean_delta_rad - prior.mean()))
    var = prior.variance() + factor.variance_rad2
    return float(0.5 * (residual * residual / max(var, 1e-6) + math.log(max(var, 1e-6))))


def _ingest_block(state: PosteriorState, block: EpisodeFactorBlock) -> dict[str, Any]:
    pre_nll = sum(_predictive_nll_heading(state, heading) for heading in block.headings)
    for center in block.centers:
        state.centers[center.edge].add(center)
    for hinge in block.hinge_axes:
        state.hinges[hinge.edge].add(hinge)
    for heading in block.headings:
        state.headings[heading.edge].add(heading)
    state.refresh_mounts_and_branches()
    episode_info = float(
        sum(f.information for f in block.centers)
        + sum(f.information for f in block.hinge_axes)
        + sum(f.information for f in block.headings)
    )
    return {
        "episode_prequential_nll": float(pre_nll),
        "episode_information": episode_info,
        "episode_window_count": len(block.windows),
        "episode_span_count": len(block.spans),
    }


def _replace_cumulative_calibration(
    state: PosteriorState,
    block: EpisodeFactorBlock,
) -> None:
    """Replace one state's axes/centres with an all-raw prefix refit."""
    state.centers = {edge.name: CenterPosterior() for edge in EDGES}
    state.hinges = {edge.name: HingePosterior() for edge in HINGE_EDGES}
    for center in block.centers:
        state.centers[center.edge].add(center)
    for hinge in block.hinge_axes:
        state.hinges[hinge.edge].add(hinge)


def solve_progressive_from_tape(tape: FactorTape) -> tuple[PosteriorState, list[dict[str, Any]]]:
    state = prior_state()
    prefixes: list[dict[str, Any]] = []
    cumulative = "calibration_owner" in tape.qmt_settings
    for index, block in enumerate(tape.episodes):
        if cumulative:
            # The axis/centre factors in this block already contain all raw
            # evidence through this prefix.  Adding them to the preceding fit
            # would count early actions repeatedly.  Mutate the same state by
            # replacing its current linearization, then advance the nine
            # persistent heading posteriors with this block only.
            pre_nll = sum(_predictive_nll_heading(state, heading) for heading in block.headings)
            _replace_cumulative_calibration(state, block)
            for heading in block.headings:
                state.headings[heading.edge].add(heading)
            state.refresh_mounts_and_branches()
            metrics = {
                "episode_prequential_nll": float(pre_nll),
                "episode_information": float(
                    sum(f.information for f in block.centers)
                    + sum(f.information for f in block.hinge_axes)
                    + sum(f.information for f in block.headings)
                ),
                "episode_window_count": len(block.windows),
                "episode_span_count": len(block.spans),
                "calibration_update_semantics": "REPLACE_SHARED_ALL_RAW_PREFIX_REFIT",
            }
        else:
            metrics = _ingest_block(state, block)
        state.prefix_index = index
        row = state.summary()
        row.update({
            "progressive_update_index": index,
            "chronological_index": block.episode_index,
            "qa_label": block.qa_label,
            **metrics,
        })
        prefixes.append(jsonable(row))
    return state, prefixes


def solve_fresh_batch_from_tape(tape: FactorTape) -> PosteriorState:
    state = prior_state()
    cumulative = "calibration_owner" in tape.qmt_settings
    if cumulative and tape.episodes:
        # The last prefix is an independently stored all-episode refit.
        _replace_cumulative_calibration(state, tape.episodes[-1])
    else:
        for block in tape.episodes:
            for center in block.centers:
                state.centers[center.edge].add(center)
        for block in tape.episodes:
            for hinge in block.hinge_axes:
                state.hinges[hinge.edge].add(hinge)
    for block in tape.episodes:
        for heading in block.headings:
            state.headings[heading.edge].add(heading)
    state.refresh_mounts_and_branches()
    state.prefix_index = len(tape.episodes) - 1
    return state


def solve_order_from_tape(tape: FactorTape, order: Sequence[int]) -> PosteriorState:
    if sorted(order) != list(range(len(tape.episodes))):
        raise ValueError("order must be a permutation of episode indices")
    state = prior_state()
    if "calibration_owner" in tape.qmt_settings and tape.episodes:
        # Axis/centre order independence is owned by the final all-raw refit;
        # heading sufficient statistics are ingested in the requested order.
        _replace_cumulative_calibration(state, tape.episodes[-1])
        for index in order:
            for heading in tape.episodes[index].headings:
                state.headings[heading.edge].add(heading)
        state.refresh_mounts_and_branches()
    else:
        for index in order:
            _ingest_block(state, tape.episodes[index])
    state.prefix_index = len(tape.episodes) - 1
    return state


def _summary_difference(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    heading = {
        edge.name: float(abs(wrap_pi(a["heading_mean_rad"][edge.name] - b["heading_mean_rad"][edge.name])))
        for edge in EDGES
    }
    branch = float(np.max(np.abs(np.asarray(a["branch_weights"]) - np.asarray(b["branch_weights"]))))
    info = float(abs(a["information_logdet"] - b["information_logdet"]))
    yaw = float(abs(wrap_pi(a["pelvis_yaw_gauge_rad"] - b["pelvis_yaw_gauge_rad"])))
    mount_max = 0.0
    for segment in SEGMENTS:
        ma = np.asarray(a["sensor_from_segment_mean"][segment], dtype=float)
        mb = np.asarray(b["sensor_from_segment_mean"][segment], dtype=float)
        mount_max = max(mount_max, float(np.linalg.norm(ma - mb)))
    return {
        "max_heading_abs_diff_rad": float(max(heading.values()) if heading else 0.0),
        "branch_linf_diff": branch,
        "information_logdet_abs_diff": info,
        "pelvis_yaw_gauge_abs_diff_rad": yaw,
        "mount_matrix_frobenius_max_diff": mount_max,
    }


def incremental_vs_batch_audit(progressive_state: PosteriorState, batch_state: PosteriorState) -> dict[str, Any]:
    diff = _summary_difference(progressive_state.summary(), batch_state.summary())
    passed = (
        diff["max_heading_abs_diff_rad"] < 1e-12
        and diff["branch_linf_diff"] < 1e-12
        and diff["information_logdet_abs_diff"] < 1e-8
        and diff["pelvis_yaw_gauge_abs_diff_rad"] < 1e-12
        and diff["mount_matrix_frobenius_max_diff"] < 1e-12
    )
    return {
        "schema": "biospur-c2-coupled-progressive-incremental-vs-fresh-batch-v2",
        "status": "PASS" if passed else "FAIL",
        "independent_batch_path": True,
        "progressive_driver_recalled": False,
        **diff,
    }


def order_permutation_audit(tape: FactorTape, reference_state: PosteriorState) -> dict[str, Any]:
    orders = {
        "reverse": list(reversed(range(len(tape.episodes)))),
        "even_then_odd": list(range(0, len(tape.episodes), 2)) + list(range(1, len(tape.episodes), 2)),
    }
    results = {}
    passed = True
    for name, order in orders.items():
        state = solve_order_from_tape(tape, order)
        diff = _summary_difference(reference_state.summary(), state.summary())
        ok = (
            diff["max_heading_abs_diff_rad"] < 1e-12
            and diff["branch_linf_diff"] < 1e-12
            and diff["information_logdet_abs_diff"] < 1e-8
            and diff["pelvis_yaw_gauge_abs_diff_rad"] < 1e-12
            and diff["mount_matrix_frobenius_max_diff"] < 1e-12
        )
        passed = passed and ok
        results[name] = {"status": "PASS" if ok else "FAIL", **diff}
    return {
        "schema": "biospur-c2-coupled-progressive-order-permutation-v2",
        "status": "PASS" if passed else "FAIL",
        "results": results,
    }


def order_permutation_audit_with_mount_gate(
    tape: FactorTape,
    reference_state: PosteriorState,
    episodes: Sequence[EpisodeFrontend],
) -> dict[str, Any]:
    orders = {
        "reverse": list(reversed(range(len(tape.episodes)))),
        "even_then_odd": list(range(0, len(tape.episodes), 2)) + list(range(1, len(tape.episodes), 2)),
    }
    results = {}
    passed = True
    for name, order in orders.items():
        state = solve_order_from_tape(tape, order)
        apply_initial_still_gravity_tilt_update(state, episodes)
        gate = apply_standing_gravity_mount_gate(state, episodes)
        diff = _summary_difference(reference_state.summary(), state.summary())
        ok = (
            gate["status"] == "PASS"
            and diff["max_heading_abs_diff_rad"] < 1e-12
            and diff["branch_linf_diff"] < 1e-12
            and diff["information_logdet_abs_diff"] < 1e-8
            and diff["pelvis_yaw_gauge_abs_diff_rad"] < 1e-12
            and diff["mount_matrix_frobenius_max_diff"] < 1e-12
        )
        passed = passed and ok
        results[name] = {"status": "PASS" if ok else "FAIL", "mount_gate_status": gate["status"], **diff}
    return {
        "schema": "biospur-c2-coupled-progressive-order-permutation-v2",
        "status": "PASS" if passed else "FAIL",
        "results": results,
    }


def _raw_quat_on_root_time(episode: EpisodeFrontend, segment: str, root_time_s: np.ndarray) -> np.ndarray:
    series = _series_for_segment(episode, segment)
    offsets = _clock_offsets_to_pelvis(episode)
    node_root_time = series.time_us.astype(float) * 1e-6 + offsets[segment]
    return interp_quat_wxyz(node_root_time, series.quat_world_sensor_wxyz, root_time_s)


def build_corrected_trajectory(
    episodes: Sequence[EpisodeFrontend],
    tape: FactorTape,
    state: PosteriorState,
    *,
    sample_step: int = 10,
) -> dict[str, Any]:
    start = time.perf_counter()
    trajectory: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    edge_stream_audit: dict[str, Any] = {}
    headings_by_episode_edge: dict[tuple[int, str], list[HeadingStreamFactor]] = {}
    for heading in tape.all_headings():
        if heading.status != "QMT_FAIL_NO_TRAJECTORY_CANDIDATE":
            headings_by_episode_edge.setdefault((heading.episode_index, heading.edge), []).append(heading)
    gauge_m = Rotation.from_euler("z", state.pelvis_yaw_gauge_rad).as_matrix()
    for episode in episodes:
        root_series = _series_for_segment(episode, "pelvis")
        root_time_s = root_series.time_us.astype(float) * 1e-6
        grid = root_time_s[::sample_step]
        segment_quats: dict[str, np.ndarray] = {}
        masks: dict[str, np.ndarray] = {}
        root_sensor_q = interp_quat_wxyz(root_time_s, root_series.quat_world_sensor_wxyz, grid)
        root_m = gauge_m @ qmt_wxyz_to_rotation(root_sensor_q).as_matrix() @ state.mounts["pelvis"].sensor_from_segment_mean
        segment_quats["pelvis"] = normalize_quat_wxyz(rotation_to_qmt_wxyz(Rotation.from_matrix(root_m)))
        masks["pelvis"] = np.ones(len(grid), dtype=bool)
        pending = ["pelvis"]
        while pending:
            parent = pending.pop(0)
            for child in TREE_CHILDREN.get(parent, ()):
                edge_name = SEGMENT_EDGE[child]
                streams = headings_by_episode_edge.get((episode.chronological_index, edge_name), [])
                child_q = np.full((len(grid), 4), np.nan, dtype=float)
                child_mask = np.zeros(len(grid), dtype=bool)
                for stream in streams:
                    inside = (grid >= stream.time_root_s[0]) & (grid <= stream.time_root_s[-1]) & masks[parent]
                    if not np.any(inside):
                        continue
                    qmt_child_sensor = interp_quat_wxyz(stream.time_root_s, stream.quat2corr_child_sensor_wxyz, grid[inside])
                    raw_parent_sensor = _raw_quat_on_root_time(episode, parent, grid[inside])
                    parent_segment_m = qmt_wxyz_to_rotation(segment_quats[parent][inside]).as_matrix()
                    parent_sensor_from_segment = state.mounts[parent].sensor_from_segment_mean
                    parent_sensor_m = parent_segment_m @ parent_sensor_from_segment.T
                    path_delta_m = parent_sensor_m @ np.transpose(qmt_wxyz_to_rotation(raw_parent_sensor).as_matrix(), (0, 2, 1))
                    child_sensor_m = path_delta_m @ qmt_wxyz_to_rotation(qmt_child_sensor).as_matrix()
                    child_segment_m = child_sensor_m @ state.mounts[child].sensor_from_segment_mean
                    child_q[inside] = normalize_quat_wxyz(rotation_to_qmt_wxyz(Rotation.from_matrix(child_segment_m)))
                    child_mask[inside] = True
                if not np.any(child_mask):
                    child_q[:] = np.array([1.0, 0.0, 0.0, 0.0])
                segment_quats[child] = child_q
                masks[child] = child_mask
                pending.append(child)
                edge_stream_audit[f"{episode.chronological_index:02d}/{edge_name}"] = {
                    "stream_count": len(streams),
                    "mask_fraction": float(np.mean(child_mask)),
                    "qmt_quat2corr_consumed": True,
                    "delta_filt_time_varying_consumed": True,
                    "fallback_used": False,
                }
        trajectory[f"{episode.chronological_index:02d}"] = {
            segment: {
                "time_root_s": grid.copy(),
                "quat_world_segment_wxyz": segment_quats[segment],
                "mask": masks[segment],
            }
            for segment in SEGMENTS
        }
    return {
        "schema": "biospur-c2-coupled-progressive-corrected-trajectory-v2",
        "wall_s": float(time.perf_counter() - start),
        "pelvis_yaw_gauge_rad": state.pelvis_yaw_gauge_rad,
        "single_capture_wide_pelvis_yaw_gauge": True,
        "viewer_qmt_rerun_required": False,
        "trajectory": trajectory,
        "edge_stream_audit": edge_stream_audit,
    }


def save_tape_npz(path: Path, tape: FactorTape, progressive: list[dict[str, Any]], trajectory: dict[str, Any]) -> dict[str, Any]:
    arrays: dict[str, np.ndarray] = {}
    prefix_rows = []
    branch_rows = []
    heading_rows = []
    for row in progressive:
        prefix_rows.append([
            row["episode_prequential_nll"],
            row["episode_information"],
            row["information_logdet"],
            row["gauge_reduced_rank"],
            row["rank_fraction"],
            row["branch_entropy"],
        ])
        branch_rows.append(row["branch_weights"])
        heading_rows.append([row["heading_mean_rad"][edge.name] for edge in EDGES])
    arrays["progressive/prefix_scalars"] = np.asarray(prefix_rows, dtype=float)
    arrays["progressive/branch_weights"] = np.asarray(branch_rows, dtype=float)
    arrays["progressive/heading_mean_rad"] = np.asarray(heading_rows, dtype=float)
    for block in tape.episodes:
        for heading in block.headings:
            base = f"edge_stream/{heading.episode_index:02d}/{heading.edge}/{heading.span_index:02d}"
            arrays[f"{base}/time_root_s"] = heading.time_root_s.astype(float)
            arrays[f"{base}/quat2corr_child_sensor_wxyz"] = heading.quat2corr_child_sensor_wxyz.astype(float)
            arrays[f"{base}/delta_filt_rad"] = heading.delta_filt_rad.astype(float)
            arrays[f"{base}/rating"] = heading.rating.astype(float)
            arrays[f"{base}/qmt_state"] = heading.qmt_state.astype(np.int16)
    for episode_key, per_segment in trajectory["trajectory"].items():
        for segment, fields in per_segment.items():
            base = f"trajectory/{episode_key}/{segment}"
            arrays[f"{base}/time_root_s"] = fields["time_root_s"].astype(float)
            arrays[f"{base}/quat_world_segment_wxyz"] = fields["quat_world_segment_wxyz"].astype(float)
            arrays[f"{base}/mask"] = fields["mask"].astype(bool)
    np.savez_compressed(path, **arrays)
    return {name: array_binding(value) for name, value in sorted(arrays.items())}


def factor_tape_manifest(tape: FactorTape) -> dict[str, Any]:
    return {
        "schema": tape.schema,
        "created_wall_s": tape.created_wall_s,
        "episode_count": len(tape.episodes),
        "span_count": sum(len(block.spans) for block in tape.episodes),
        "window_count": sum(len(block.windows) for block in tape.episodes),
        "hinge_factor_count": sum(len(block.hinge_axes) for block in tape.episodes),
        "center_factor_count": sum(len(block.centers) for block in tape.episodes),
        "heading_stream_count": sum(len(block.headings) for block in tape.episodes),
        "heading_qmt_failure_count": sum(
            1 for block in tape.episodes for row in block.headings
            if row.status == "QMT_FAIL_NO_TRAJECTORY_CANDIDATE"
        ),
        "alignment_audit": tape.alignment_audit,
        "qmt_settings": tape.qmt_settings,
        "time_varying_quat2corr_and_deltaFilt_persisted": True,
        "qmt_internal_time": "deterministic_40hz_decimation_after_capture_wide_span_gate_then_full_span_interpolation",
        "semantic_sha256": semantic_sha({
            "span_count": sum(len(block.spans) for block in tape.episodes),
            "heading_stream_count": sum(len(block.headings) for block in tape.episodes),
            "qmt_settings": tape.qmt_settings,
        }),
    }


def load_real_episodes() -> list[EpisodeFrontend]:
    front = VerifiedFrontendArchive()
    front.verify_seal_and_semantics()
    return list(front.episodes())


def run_real_pipeline() -> dict[str, Any]:
    episodes = load_real_episodes()
    raw_tape = build_factor_tape(episodes)
    tape = refit_cumulative_calibration_tape(raw_tape)
    base_state = solve_fresh_batch_from_tape(tape)
    apply_initial_still_gravity_tilt_update(base_state, episodes)
    apply_standing_gravity_mount_gate(base_state, episodes)
    tape = attach_capture_wide_heading_streams(tape, base_state)
    progressive_state, prefixes = solve_progressive_from_tape(tape)
    batch_state = solve_fresh_batch_from_tape(tape)
    apply_initial_still_gravity_tilt_update(progressive_state, episodes)
    apply_initial_still_gravity_tilt_update(batch_state, episodes)
    apply_standing_gravity_mount_gate(progressive_state, episodes)
    apply_standing_gravity_mount_gate(batch_state, episodes)
    trajectory = build_corrected_trajectory(episodes, tape, progressive_state)
    return {
        "episodes": episodes,
        "tape": tape,
        "progressive_state": progressive_state,
        "prefixes": prefixes,
        "batch_state": batch_state,
        "trajectory": trajectory,
        "batch_audit": incremental_vs_batch_audit(progressive_state, batch_state),
        "order_audit": order_permutation_audit_with_mount_gate(tape, progressive_state, episodes),
    }
