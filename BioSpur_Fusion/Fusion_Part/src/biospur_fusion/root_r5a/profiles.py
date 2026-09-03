"""Nuisance-profiled circular yaw objectives on matched C1 support."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable

import numpy as np
from scipy.optimize import minimize_scalar

from biospur_fusion.root_r4.contracts import wrap_degrees
from biospur_fusion.root_r4.data import C1Data

from .constants import HUBER_DELTA_M, PROFILE_FINE_STEP_DEG, PROFILE_GRID_STEP_DEG


def _rotation(yaw: float) -> np.ndarray:
    c, s = math.cos(float(yaw)), math.sin(float(yaw))
    return np.asarray([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _huber(values: np.ndarray, delta: float = HUBER_DELTA_M) -> np.ndarray:
    absolute = np.abs(values)
    return np.where(absolute <= delta, 0.5 * absolute**2, delta * (absolute - 0.5 * delta))


def centered_t4(data: C1Data, event_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mask = np.asarray(event_mask, bool).copy()
    mask &= np.all(np.isfinite(data.root_relative_n_m), axis=1) & np.all(np.isfinite(data.t4_xyz_m), axis=1)
    ids = np.flatnonzero(mask); order = ids[np.argsort(data.epoch[ids], kind="stable")]
    epochs = data.epoch[order]; starts = np.r_[0, np.flatnonzero(np.diff(epochs)) + 1]; stops = np.r_[starts[1:], len(order)]
    xs = []; ys = []; tags = []; epoch_rows = []
    for start, stop in zip(starts, stops):
        group = order[start:stop]
        if len(group) < 4:
            continue
        x = data.root_relative_n_m[group]; y = data.t4_xyz_m[group]
        xs.append(x - np.mean(x, axis=0)); ys.append(y - np.mean(y, axis=0))
        tags.append(data.node_index[group]); epoch_rows.append(np.full(len(group), data.epoch[group[0]], np.int64))
    if not xs:
        raise ValueError("insufficient matched multi-tag epochs")
    return np.concatenate(xs), np.concatenate(ys), np.concatenate(tags), np.concatenate(epoch_rows)


@dataclass(frozen=True)
class RawGroups:
    event_relative_n: np.ndarray
    link_relative_n: np.ndarray
    t4_v4: np.ndarray
    ranges_m: np.ndarray
    valid: np.ndarray
    anchors_v4: np.ndarray
    delays_m: np.ndarray
    group_count: int
    event_count: int
    link_count: int
    tag_count: int
    anchor_count: int


def raw_groups(data: C1Data, event_mask: np.ndarray) -> RawGroups:
    mask = np.asarray(event_mask, bool).copy()
    mask &= np.all(np.isfinite(data.root_relative_n_m), axis=1) & np.all(np.isfinite(data.t4_xyz_m), axis=1)
    epochs = np.unique(data.epoch[mask]); groups = [np.flatnonzero(mask & (data.epoch == epoch)) for epoch in epochs]
    groups = [group for group in groups if len(group) >= 4]
    width = max(len(group) for group in groups); count = len(groups)
    event_relative = np.zeros((count, width, 3)); link_relative = np.zeros((count, width, 8, 3))
    t4 = np.zeros((count, width, 3)); ranges = np.zeros((count, width, 8)); valid = np.zeros((count, width, 8), bool)
    tags = set(); anchors = set(); events = 0
    for index, group in enumerate(groups):
        size = len(group); events += size; tags.update(data.node_index[group].tolist())
        event_relative[index, :size] = data.root_relative_n_m[group]
        link_relative[index, :size] = data.raw_root_relative_n_m[group]
        t4[index, :size] = data.t4_xyz_m[group]; ranges[index, :size] = data.raw_range_m[group]
        finite = np.all(np.isfinite(data.raw_root_relative_n_m[group]), axis=2)
        valid[index, :size] = data.raw_valid[group] & finite
        anchors.update(np.flatnonzero(np.any(valid[index, :size], axis=0)).tolist())
    return RawGroups(event_relative, link_relative, t4, ranges, valid, data.anchors_v4_m,
                     data.anchor_delay_m, count, events, int(np.sum(valid)), len(tags), len(anchors))


def t4_evaluator(data: C1Data, event_mask: np.ndarray) -> tuple[Callable[[float], float], Callable[[float], np.ndarray], dict]:
    x, y, tags, epochs = centered_t4(data, event_mask)
    def residual(yaw: float) -> np.ndarray:
        return np.linalg.norm(y - x @ _rotation(yaw).T, axis=1)
    def objective(yaw: float) -> float:
        return float(np.mean(_huber(residual(yaw))))
    support = {"samples": len(x), "events": len(x), "epochs": len(np.unique(epochs)),
               "tags": len(np.unique(tags)), "anchors": 8,
               "nuisance": "per-epoch common 3-D root translation eliminated exactly by centering"}
    return objective, residual, support


def raw_evaluator(groups: RawGroups, omit_anchor: int | None = None) -> tuple[Callable[[float], float], Callable[[float], np.ndarray], dict]:
    valid_base = groups.valid.copy()
    if omit_anchor is not None:
        valid_base[..., int(omit_anchor)] = False

    def evaluate(yaw: float, return_residual: bool = False):
        rotation = _rotation(yaw)
        event_relative = groups.event_relative_n @ rotation.T
        link_relative = groups.link_relative_n @ rotation.T
        padded = np.any(valid_base, axis=2)
        candidates = np.where(padded[..., None], groups.t4_v4 - event_relative, np.nan)
        root = np.nanmedian(candidates, axis=1)
        valid = valid_base
        for _ in range(5):
            vectors = root[:, None, None, :] + link_relative - groups.anchors_v4[None, None, :, :]
            distance = np.linalg.norm(vectors, axis=3)
            error = distance + groups.delays_m[None, None, :] - groups.ranges_m
            jacobian = vectors / np.maximum(distance[..., None], 1e-12)
            weight = np.minimum(1.0, 0.20 / np.maximum(np.abs(error), 1e-12)) * valid
            hessian = np.einsum("gtai,gta,gtaj->gij", jacobian, weight, jacobian) + np.eye(3)[None] * 1e-7
            gradient = np.einsum("gtai,gta->gi", jacobian, weight * error)
            try:
                root -= np.linalg.solve(hessian, gradient[..., None])[..., 0]
            except np.linalg.LinAlgError:
                root -= np.asarray([np.linalg.lstsq(h, g, rcond=None)[0] for h, g in zip(hessian, gradient)])
        vectors = root[:, None, None, :] + link_relative - groups.anchors_v4[None, None, :, :]
        error = np.linalg.norm(vectors, axis=3) + groups.delays_m[None, None, :] - groups.ranges_m
        selected = error[valid]
        return selected if return_residual else float(np.mean(_huber(selected)))

    support = {"samples": int(np.sum(valid_base)), "events": groups.event_count,
               "epochs": groups.group_count, "tags": groups.tag_count,
               "anchors": int(np.sum(np.any(valid_base, axis=(0, 1)))),
               "nuisance": "one common 3-D root translation per global epoch profiled by robust Gauss-Newton"}
    return lambda yaw: evaluate(yaw, False), lambda yaw: evaluate(yaw, True), support


def _circular_local_minima(values: np.ndarray) -> list[int]:
    return [index for index in range(len(values))
            if values[index] <= values[(index - 1) % len(values)] and values[index] < values[(index + 1) % len(values)]]


def circular_profile(layer: str, block: str, objective: Callable[[float], float],
                     residual: Callable[[float], np.ndarray], support: dict) -> dict:
    grid_deg = np.arange(-180.0, 180.0, PROFILE_GRID_STEP_DEG)
    grid_rad = np.deg2rad(grid_deg); values = np.asarray([objective(value) for value in grid_rad])
    minima = _circular_local_minima(values); minima = sorted(minima, key=lambda index: values[index])
    material = [index for index in minima if values[index] <= values[minima[0]] + max(1e-8, 0.05 * np.ptp(values))]
    refined = []
    for index in material:
        centre = grid_rad[index]; half = np.deg2rad(PROFILE_GRID_STEP_DEG)
        result = minimize_scalar(lambda value: objective((value + math.pi) % (2 * math.pi) - math.pi),
                                 bounds=(centre - half, centre + half), method="bounded",
                                 options={"xatol": np.deg2rad(0.005)})
        yaw = (float(result.x) + math.pi) % (2 * math.pi) - math.pi
        refined.append((yaw, float(result.fun)))
    refined.sort(key=lambda item: item[1]); mode, optimum = refined[0]
    errors = residual(mode); median = float(np.median(np.abs(errors)))
    scale = max(0.05, 1.4826 * float(np.median(np.abs(np.abs(errors) - median))))
    step = np.deg2rad(PROFILE_FINE_STEP_DEG)
    curvature = max(0.0, (objective(mode + step) - 2 * optimum + objective(mode - step)) / step**2)
    information = float(support["samples"] * curvature / scale**2)
    ci_width = float(np.degrees(2 * 1.96 / math.sqrt(information))) if information > 0 else 360.0
    fine_grid = mode + np.deg2rad(np.arange(-2.0, 2.0 + PROFILE_FINE_STEP_DEG, PROFILE_FINE_STEP_DEG))
    fine_values = np.asarray([objective((value + math.pi) % (2 * math.pi) - math.pi) for value in fine_grid])
    fine_mode = (float(fine_grid[int(np.argmin(fine_values))]) + math.pi) % (2 * math.pi) - math.pi
    convergence = abs(wrap_degrees(np.degrees(fine_mode - mode))) <= PROFILE_FINE_STEP_DEG + 1e-9
    normalized = (values - optimum) / scale**2
    mode_rows = [{"yaw_deg": wrap_degrees(float(np.degrees(yaw))), "objective": value,
                  "delta_objective": value - optimum,
                  "separation_from_global_deg": abs(wrap_degrees(float(np.degrees(yaw - mode))))}
                 for yaw, value in refined]
    return {
        "schema": "biospur.root_r5a.circular_profile.v1", "layer": layer, "block": block,
        "frame_parameter": "yaw of R_V4_from_N; R_N_from_V4 is its transpose/inverse",
        "grid_step_deg": PROFILE_GRID_STEP_DEG, "grid_yaw_deg": grid_deg.tolist(),
        "objective": values.tolist(), "normalized_delta_objective": normalized.tolist(),
        "global_mode_deg": wrap_degrees(float(np.degrees(mode))), "material_modes": mode_rows,
        "objective_minimum": optimum, "objective_range": float(np.ptp(values)),
        "robust_residual_scale_m": scale, "residual_median_abs_m": median,
        "nuisance_eliminated_yaw_information": information,
        "asymptotic_profile_interval_width_95_deg": ci_width,
        "interval_semantics": "local robust pseudo-likelihood curvature; internal precision, not external accuracy",
        "profile_grid_converged": convergence, "fine_check_step_deg": PROFILE_FINE_STEP_DEG,
        "fine_mode_disagreement_deg": abs(wrap_degrees(np.degrees(fine_mode - mode))),
        "practical_sharpness_status": "INCONCLUSIVE_NO_PREDECLARED_PRACTICAL_GATE",
        "support": support,
    }


def profile_set(data: C1Data, masks: list[np.ndarray], layer: str) -> list[dict]:
    profiles = []
    for block, mask in enumerate(masks + [np.logical_or.reduce(masks)]):
        label = f"BLOCK_{block}" if block < len(masks) else "COMBINED"
        if layer == "T4":
            evaluator = t4_evaluator(data, mask)
        elif layer == "RAW":
            evaluator = raw_evaluator(raw_groups(data, mask))
        else:
            raise ValueError(layer)
        profiles.append(circular_profile(layer, label, *evaluator))
    return profiles


def profile_value(profile: dict, yaw_rad: float) -> float:
    """Periodic deterministic linear interpolation of a stored 1-degree profile."""
    degrees = wrap_degrees(float(np.degrees(yaw_rad)))
    coordinate = degrees + 180.0
    lower = int(math.floor(coordinate)) % 360; upper = (lower + 1) % 360; weight = coordinate - math.floor(coordinate)
    values = profile["objective"]
    return float((1.0 - weight) * values[lower] + weight * values[upper])
