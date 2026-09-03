"""Global common-frame estimation, gauge elimination, and observability diagnostics."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy.optimize import minimize_scalar

from .contracts import FrameContract, wrap_degrees, yaw_rotation_v4_from_n
from .data import C1Data


@dataclass(frozen=True)
class FrameFit:
    layer: str
    yaw_v4_from_n_rad: float
    objective: float
    residual_median_m: float
    residual_p95_m: float
    samples: int
    epochs: int
    likelihood: str

    @property
    def yaw_v4_from_n_deg(self) -> float:
        return float(np.degrees(self.yaw_v4_from_n_rad))

    @property
    def rotation_v4_from_n(self) -> np.ndarray:
        return yaw_rotation_v4_from_n(self.yaw_v4_from_n_rad)

    @property
    def rotation_n_from_v4(self) -> np.ndarray:
        return self.rotation_v4_from_n.T

    def record(self) -> dict:
        yaw = self.yaw_v4_from_n_rad
        contract = FrameContract(); proper = contract.validate(self.rotation_n_from_v4)
        return {
            "layer": self.layer,
            "transform_direction": "R_N_from_V4 maps v^V4 to v^N; reported fit parameter is its inverse yaw R_V4_from_N",
            "R_N_from_V4": self.rotation_n_from_v4.tolist(),
            "R_V4_from_N": self.rotation_v4_from_n.tolist(),
            "quaternion_N_from_V4_wxyz": [math.cos(-yaw / 2.0), 0.0, 0.0, math.sin(-yaw / 2.0)],
            "yaw_pitch_roll_N_from_V4_deg": [-self.yaw_v4_from_n_deg, 0.0, 0.0],
            "yaw_V4_from_N_deg": self.yaw_v4_from_n_deg,
            "objective": self.objective, "robust_objective": self.objective,
            "residual_median_m": self.residual_median_m, "residual_p95_m": self.residual_p95_m,
            "samples": self.samples, "epochs": self.epochs, "likelihood": self.likelihood,
            **proper,
        }


def _epoch_centered(data: C1Data, event_mask: np.ndarray | None = None,
                    omit_tag: int | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mask = np.ones(data.event_count, bool) if event_mask is None else np.asarray(event_mask, bool).copy()
    mask &= np.all(np.isfinite(data.root_relative_n_m), axis=1) & np.all(np.isfinite(data.t4_xyz_m), axis=1)
    if omit_tag is not None:
        mask &= data.node_index != int(omit_tag)
    ids = np.flatnonzero(mask)
    order = ids[np.argsort(data.epoch[ids], kind="stable")]
    epoch = data.epoch[order]
    starts = np.r_[0, np.flatnonzero(np.diff(epoch)) + 1]
    stops = np.r_[starts[1:], len(order)]
    x_values: list[np.ndarray] = []; y_values: list[np.ndarray] = []; tags: list[np.ndarray] = []; epochs: list[np.ndarray] = []
    for start, stop in zip(starts, stops):
        group = order[start:stop]
        if len(group) < 4:
            continue
        x = data.root_relative_n_m[group]; y = data.t4_xyz_m[group]
        x_values.append(x - np.mean(x, axis=0)); y_values.append(y - np.mean(y, axis=0))
        tags.append(data.node_index[group]); epochs.append(np.full(len(group), data.epoch[group[0]], np.int64))
    if not x_values:
        raise ValueError("insufficient multi-tag epochs")
    return np.concatenate(x_values), np.concatenate(y_values), np.concatenate(tags), np.concatenate(epochs)


def _yaw_from_xy(x: np.ndarray, y: np.ndarray, weights: np.ndarray | None = None) -> float:
    w = np.ones(len(x)) if weights is None else np.asarray(weights, float)
    cosine = np.sum(w * (x[:, 0] * y[:, 0] + x[:, 1] * y[:, 1]))
    sine = np.sum(w * (x[:, 0] * y[:, 1] - x[:, 1] * y[:, 0]))
    return float(math.atan2(sine, cosine))


def _t4_residuals(x: np.ndarray, y: np.ndarray, yaw: float) -> np.ndarray:
    return y - x @ yaw_rotation_v4_from_n(yaw).T


def fit_centered_yaw(x: np.ndarray, y: np.ndarray) -> tuple[float, np.ndarray]:
    """Robust public array primitive used by real and synthetic qualification."""

    source = np.asarray(x, float); destination = np.asarray(y, float)
    if source.shape != destination.shape or source.ndim != 2 or source.shape[1] != 3 or len(source) < 4:
        raise ValueError("centered frame arrays must be matching Nx3 with N>=4")
    weights = np.ones(len(source)); yaw = _yaw_from_xy(source, destination, weights)
    for _ in range(8):
        norms = np.linalg.norm(_t4_residuals(source, destination, yaw), axis=1)
        scale = max(0.01, 1.4826 * float(np.median(np.abs(norms - np.median(norms)))))
        weights = np.minimum(1.0, 1.345 * scale / np.maximum(norms, 1e-12))
        candidate = _yaw_from_xy(source, destination, weights)
        if abs(wrap_degrees(np.degrees(candidate - yaw))) < 1e-8:
            yaw = candidate; break
        yaw = candidate
    return yaw, np.linalg.norm(_t4_residuals(source, destination, yaw), axis=1)


def fit_t4_yaw(data: C1Data, event_mask: np.ndarray | None = None, *, omit_tag: int | None = None,
               layer: str = "T4") -> FrameFit:
    x, y, _, epoch = _epoch_centered(data, event_mask, omit_tag)
    yaw, norms = fit_centered_yaw(x, y)
    delta = 0.25; objective = float(np.mean(np.where(norms <= delta, 0.5 * norms**2, delta * (norms - 0.5 * delta))))
    return FrameFit(layer, yaw, objective, float(np.median(norms)), float(np.quantile(norms, 0.95)),
                    len(x), len(np.unique(epoch)), "robust multi-tag T4 position residual after per-epoch translation elimination")


def fit_t4_yaw_linear(data: C1Data, event_mask: np.ndarray | None = None,
                      *, layer: str = "T4_LINEAR_LOSS_SENSITIVITY") -> FrameFit:
    """Non-robust least-squares yaw used only as a loss-sensitivity diagnostic."""

    x, y, _, epoch = _epoch_centered(data, event_mask)
    yaw = _yaw_from_xy(x, y)
    norms = np.linalg.norm(_t4_residuals(x, y, yaw), axis=1)
    return FrameFit(layer, yaw, float(np.mean(0.5 * norms**2)), float(np.median(norms)),
                    float(np.quantile(norms, 0.95)), len(x), len(np.unique(epoch)),
                    "non-robust least-squares T4 diagnostic; never an authorized production factor")


def _raw_groups(data: C1Data, event_mask: np.ndarray | None, sample_stride: int,
                omit_tag: int | None = None) -> list[np.ndarray]:
    mask = np.ones(data.event_count, bool) if event_mask is None else np.asarray(event_mask, bool).copy()
    mask &= np.all(np.isfinite(data.root_relative_n_m), axis=1) & np.all(np.isfinite(data.t4_xyz_m), axis=1)
    if omit_tag is not None:
        mask &= data.node_index != int(omit_tag)
    chosen_epochs = np.unique(data.epoch[mask])[::max(1, sample_stride)]
    groups = [np.flatnonzero(mask & (data.epoch == epoch)) for epoch in chosen_epochs]
    return [group for group in groups if len(group) >= 5]


def _profile_raw(data: C1Data, groups: list[np.ndarray], yaw: float,
                 omit_anchor: int | None = None, *, use_link_timing: bool = True) -> tuple[float, np.ndarray]:
    rotation = yaw_rotation_v4_from_n(yaw)
    losses: list[np.ndarray] = []; residuals: list[np.ndarray] = []
    for group in groups:
        relative = data.root_relative_n_m[group] @ rotation.T
        if use_link_timing:
            link_relative = data.raw_root_relative_n_m[group] @ rotation.T
        else:
            link_relative = np.broadcast_to(relative[:, None, :], (len(group), 8, 3))
        # T4 is a numerical initializer only. The optimized likelihood below is raw ranges.
        root = np.median(data.t4_xyz_m[group] - relative, axis=0)
        mask = data.raw_valid[group].copy() & np.all(np.isfinite(link_relative), axis=2)
        if omit_anchor is not None:
            mask[:, int(omit_anchor)] = False
        for _ in range(5):
            vectors = root + link_relative - data.anchors_v4_m[None, :, :]
            distance = np.linalg.norm(vectors, axis=2)
            error = distance + data.anchor_delay_m[None, :] - data.raw_range_m[group]
            jacobian = vectors / np.maximum(distance[..., None], 1e-12)
            e = error[mask]; j = jacobian[mask]
            weight = np.minimum(1.0, 0.20 / np.maximum(np.abs(e), 1e-12))
            hessian = j.T @ (weight[:, None] * j) + np.eye(3) * 1e-8
            gradient = j.T @ (weight * e)
            root -= np.linalg.solve(hessian, gradient)
        vectors = root + link_relative - data.anchors_v4_m[None, :, :]
        error = np.linalg.norm(vectors, axis=2) + data.anchor_delay_m[None, :] - data.raw_range_m[group]
        e = error[mask]; u = np.abs(e); delta = 0.25
        losses.append(np.where(u <= delta, 0.5 * u**2, delta * (u - 0.5 * delta)))
        residuals.append(e)
    all_loss = np.concatenate(losses); all_residual = np.concatenate(residuals)
    return float(np.mean(all_loss)), all_residual


def fit_raw_yaw(data: C1Data, event_mask: np.ndarray | None = None, *, sample_stride: int = 20,
                omit_tag: int | None = None, omit_anchor: int | None = None,
                layer: str = "RAW_RANGE", use_link_timing: bool = True) -> FrameFit:
    groups = _raw_groups(data, event_mask, sample_stride, omit_tag)
    objective = lambda yaw: _profile_raw(data, groups, float(yaw), omit_anchor,
                                          use_link_timing=use_link_timing)[0]
    # Grid initialization prevents a bounded golden-section search from settling on a secondary basin.
    grid = np.linspace(-math.pi, math.pi, 73)
    values = np.asarray([objective(value) for value in grid])
    best = int(np.argmin(values)); half = 2 * math.pi / 72
    centre = float(grid[best])
    result = minimize_scalar(objective, bounds=(centre - half, centre + half), method="bounded",
                             options={"xatol": 2e-5})
    yaw = float((result.x + math.pi) % (2 * math.pi) - math.pi)
    value, residual = _profile_raw(data, groups, yaw, omit_anchor, use_link_timing=use_link_timing)
    absolute = np.abs(residual)
    return FrameFit(layer, yaw, value, float(np.median(absolute)), float(np.quantile(absolute, 0.95)),
                    len(residual), len(groups),
                    "Huber raw range likelihood; per-epoch root profiled; per-link midpoint M1 geometry; "
                    "T4 numerical initialization is not a factor" if use_link_timing else
                    "timing counterfactual: all links use the T4 event-time M1 geometry")


def fit_hybrid_yaw(data: C1Data, *, sample_stride: int = 20) -> FrameFit:
    raw_mask = (data.epoch & 1) == 0; t4_mask = ~raw_mask
    raw_groups = _raw_groups(data, raw_mask, sample_stride)
    x, y, _, epoch = _epoch_centered(data, t4_mask)
    raw_zero, _ = _profile_raw(data, raw_groups, 0.0)
    zero_norm = np.linalg.norm(_t4_residuals(x, y, 0.0), axis=1)
    delta = 0.25
    t4_zero = float(np.mean(np.where(zero_norm <= delta, 0.5 * zero_norm**2, delta * (zero_norm - 0.5 * delta))))
    def objective(yaw: float) -> float:
        raw_value, _ = _profile_raw(data, raw_groups, yaw)
        norms = np.linalg.norm(_t4_residuals(x, y, yaw), axis=1)
        t4_value = float(np.mean(np.where(norms <= delta, 0.5 * norms**2, delta * (norms - 0.5 * delta))))
        # Normalize at identity so neither representation gains authority from sample count or units.
        return raw_value / raw_zero + t4_value / t4_zero
    grid = np.linspace(-math.pi, math.pi, 73); values = np.asarray([objective(value) for value in grid])
    centre = float(grid[int(np.argmin(values))]); half = 2 * math.pi / 72
    result = minimize_scalar(objective, bounds=(centre - half, centre + half), method="bounded")
    yaw = float((result.x + math.pi) % (2 * math.pi) - math.pi)
    raw_value, raw_residual = _profile_raw(data, raw_groups, yaw)
    t4_norm = np.linalg.norm(_t4_residuals(x, y, yaw), axis=1)
    combined = np.r_[np.abs(raw_residual), t4_norm]
    return FrameFit("LINEAGE_SAFE_HYBRID", yaw, float(result.fun), float(np.median(combined)),
                    float(np.quantile(combined, 0.95)), len(combined), len(raw_groups) + len(np.unique(epoch)),
                    "disjoint even-epoch raw likelihood plus odd-epoch replacing T4 likelihood")


def t4_objective_at(data: C1Data, yaw: float, event_mask: np.ndarray | None = None) -> float:
    x, y, _, _ = _epoch_centered(data, event_mask)
    norms = np.linalg.norm(_t4_residuals(x, y, yaw), axis=1); delta = 0.25
    return float(np.mean(np.where(norms <= delta, 0.5 * norms**2, delta * (norms - 0.5 * delta))))


def t4_observability(data: C1Data, fit: FrameFit) -> dict:
    x, y, tags, epochs = _epoch_centered(data)
    rotation = fit.rotation_v4_from_n
    derivative = np.c_[-(x @ rotation.T)[:, 1], (x @ rotation.T)[:, 0], np.zeros(len(x))]
    residual = _t4_residuals(x, y, fit.yaw_v4_from_n_rad)
    sigma = max(0.05, 1.4826 * float(np.median(np.abs(np.linalg.norm(residual, axis=1) - np.median(np.linalg.norm(residual, axis=1))))))
    fisher = float(np.sum(derivative * derivative) / sigma**2)
    cross = np.asarray([np.array([[0.0, -row[2], row[1]], [row[2], 0.0, -row[0]], [-row[1], row[0], 0.0]])
                        for row in x])
    jacobian_so3 = -np.einsum("ij,njk->nik", rotation, cross).reshape(-1, 3)
    singular = np.linalg.svd(jacobian_so3 / sigma, compute_uv=False)
    return {
        "correct_transform_family": "yaw-only embedded in SO(3)",
        "nuisance_elimination": "per-epoch common root translation removed by centering",
        "yaw_schur_fisher": fisher,
        "yaw_rank_by_tolerance": {str(tol): int(fisher > tol) for tol in (1e-3, 1e-6, 1e-9, 1e-12)},
        "yaw_singular_values": [math.sqrt(max(fisher, 0.0))], "yaw_condition_number": 1.0 if fisher > 0 else None,
        "diagnostic_full_so3_jacobian_singular_values": singular.tolist(),
        "diagnostic_full_so3_rank_by_relative_tolerance": {str(tol): int(np.sum(singular > singular[0] * tol))
                                                             for tol in (1e-3, 1e-6, 1e-9)},
        "events": len(x), "epochs": len(np.unique(epochs)), "tags": len(np.unique(tags)),
    }


def stability_audits(data: C1Data, t4_fit: FrameFit, *, raw_stride: int = 20) -> tuple[dict, dict, dict, dict]:
    quantiles = np.quantile(data.measurement_s, np.linspace(0.0, 1.0, 6))
    blocks = []
    for index in range(5):
        mask = ((data.measurement_s >= quantiles[index]) &
                (data.measurement_s <= quantiles[index + 1]))
        fit = fit_t4_yaw(data, mask, layer=f"T4_TIME_BLOCK_{index}")
        blocks.append({"block": index, "time_s": [float(quantiles[index]), float(quantiles[index + 1])], **fit.record()})
    tag_loo = []
    for tag, name in enumerate(data.nodes):
        fit = fit_t4_yaw(data, omit_tag=tag, layer=f"T4_TAG_LOO_{name}")
        tag_loo.append({"omitted_tag": name, **fit.record(),
                        "yaw_change_deg": wrap_degrees(fit.yaw_v4_from_n_deg - t4_fit.yaw_v4_from_n_deg)})
    anchor_loo = []
    for anchor in range(8):
        fit = fit_raw_yaw(data, sample_stride=raw_stride, omit_anchor=anchor, layer=f"RAW_ANCHOR_LOO_{anchor}")
        anchor_loo.append({"omitted_anchor": anchor, **fit.record()})
    # Independent contiguous-block bootstrap: concatenate five sampled blocks with
    # replacement so repeated blocks retain their multiplicity in the fit.
    rng = np.random.default_rng(416204)
    bootstrap = []
    block_ids = np.digitize(data.measurement_s, quantiles[1:-1], right=False)
    centered_blocks = []
    for block in range(5):
        centered_blocks.append(_epoch_centered(data, block_ids == block)[:2])
    for iteration in range(100):
        selected = rng.integers(0, 5, 5)
        x = np.concatenate([centered_blocks[int(block)][0] for block in selected])
        y = np.concatenate([centered_blocks[int(block)][1] for block in selected])
        yaw, _ = fit_centered_yaw(x, y)
        bootstrap.append(float(np.degrees(yaw)))
    bootstrap_array = np.asarray(bootstrap)
    return (
        {"schema": "biospur.root_r4.time_block_stability.v1", "blocks": blocks,
         "yaw_range_deg": float(np.ptp([row["yaw_V4_from_N_deg"] for row in blocks]))},
        {"schema": "biospur.root_r4.tag_loo.v1", "rows": tag_loo,
         "maximum_abs_yaw_change_deg": float(max(abs(row["yaw_change_deg"]) for row in tag_loo))},
        {"schema": "biospur.root_r4.anchor_loo.v1", "rows": anchor_loo},
        {"schema": "biospur.root_r4.block_bootstrap.v1", "iterations": len(bootstrap),
         "yaw_deg": {"median": float(np.median(bootstrap_array)), "p05": float(np.quantile(bootstrap_array, 0.05)),
                     "p95": float(np.quantile(bootstrap_array, 0.95)), "standard_deviation": float(np.std(bootstrap_array))}},
    )


def counterfactuals(data: C1Data, fit: FrameFit) -> dict:
    x, y, _, _ = _epoch_centered(data)
    base = t4_objective_at(data, fit.yaw_v4_from_n_rad)
    candidates = {
        "authorized_family_optimum": fit.yaw_v4_from_n_rad,
        "inverse_active_passive": -fit.yaw_v4_from_n_rad,
        "plus_90_deg": fit.yaw_v4_from_n_rad + math.pi / 2,
        "plus_180_deg": fit.yaw_v4_from_n_rad + math.pi,
    }
    rows = {name: {"yaw_deg": float(np.degrees(value)), "objective": t4_objective_at(data, value),
                   "objective_ratio_to_optimum": t4_objective_at(data, value) / base}
            for name, value in candidates.items()}
    # Orthogonal Procrustes reflection and free scale are diagnostics only.
    h = x.T @ y; u, singular, vt = np.linalg.svd(h); reflected = vt.T @ u.T
    if np.linalg.det(reflected) > 0:
        vt[-1] *= -1; reflected = vt.T @ u.T
    reflection_residual = np.linalg.norm(y - x @ reflected.T, axis=1)
    rotation = fit.rotation_v4_from_n
    rotated = x @ rotation.T
    scale = float(np.sum(rotated * y) / np.sum(rotated * rotated))
    scale_residual = np.linalg.norm(y - scale * rotated, axis=1)
    return {
        "schema": "biospur.root_r4.frame_counterfactuals.v1", "yaw_counterfactuals": rows,
        "reflection": {"classification": "DIAGNOSTIC_COUNTERFACTUAL_NOT_AUTHORIZED_FRAME",
                       "determinant": float(np.linalg.det(reflected)), "median_residual_m": float(np.median(reflection_residual))},
        "free_scale": {"classification": "DIAGNOSTIC_COUNTERFACTUAL_NOT_AUTHORIZED_FRAME",
                       "scale": scale, "median_residual_m": float(np.median(scale_residual)), "authorized": False},
        "fixed_0_915_m_disagreement_absorbed_by_scale": False,
        "procrustes_singular_values": singular.tolist(),
    }
