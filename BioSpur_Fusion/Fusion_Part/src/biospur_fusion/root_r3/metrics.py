"""Continuous Root-R3 interface, trajectory, and Pareto metrics."""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Iterable

import numpy as np

from .data import C1UwbTable
from .interfaces import yaw_rotation


def summary(values: Iterable[float]) -> dict:
    array = np.asarray(list(values) if not isinstance(values, np.ndarray) else values, float).ravel()
    array = array[np.isfinite(array)]
    if not len(array):
        return {"count": 0, "p50": None, "mad": None, "p95": None, "p99": None, "max": None}
    median = float(np.median(array))
    return {
        "count": int(len(array)),
        "p50": median,
        "mad": float(np.median(np.abs(array - median))),
        "p95": float(np.quantile(array, 0.95)),
        "p99": float(np.quantile(array, 0.99)),
        "max": float(np.max(array)),
    }


def _snapshot_residuals(node: np.ndarray, measurement: np.ndarray, availability: np.ndarray,
                        root: np.ndarray, valid: np.ndarray, start: float, end: float,
                        *, stale_s: float = 0.18, allowed_nodes: set[int] | None = None,
                        disallowed_anchor_rows: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(availability, kind="stable")
    latest = np.full((10, 3), np.nan); latest_time = np.full(10, np.nan)
    residuals: list[float] = []; source_rows: list[int] = []
    for event in order:
        if measurement[event] < start or measurement[event] > end:
            continue
        ni = int(node[event])
        allowed = allowed_nodes is None or ni in allowed_nodes
        row_allowed = disallowed_anchor_rows is None or not bool(disallowed_anchor_rows[event])
        if valid[event] and allowed and row_allowed:
            latest[ni] = root[event]; latest_time[ni] = measurement[event]
        age = availability[event] - latest_time
        use = np.isfinite(latest_time) & (age >= -1e-9) & (age <= stale_s) & np.all(np.isfinite(latest), axis=1)
        if allowed_nodes is not None:
            use &= np.asarray([index in allowed_nodes for index in range(10)])
        if valid[event] and allowed and row_allowed and int(use.sum()) >= min(4, len(allowed_nodes) if allowed_nodes else 4):
            residuals.append(float(np.linalg.norm(root[event] - np.median(latest[use], axis=0))))
            source_rows.append(int(event))
    return np.asarray(residuals), np.asarray(source_rows, dtype=np.int64)


def reproduce_historical_0902(table: C1UwbTable, start_s: float, end_s: float) -> dict:
    residual, rows = _snapshot_residuals(
        table.node_index, table.measurement_s, table.availability_s,
        table.historical_root_candidate_m, table.historical_candidate_valid,
        start_s, end_s,
    )
    robust = max(0.020, 1.4826 * float(np.median(residual)))
    return {
        "definition": "max(0.020 m, 1.4826 * median Euclidean distance of each valid event from coordinate-wise median of latest <=0.18 s tag roots in availability order)",
        "sample_interval_s": [float(start_s), float(end_s)],
        "events": int(len(residual)),
        "robust_cross_tag_scale_m": robust,
        "residual_m": summary(residual),
        "source_rows": rows,
    }


def dispersion_decomposition(table: C1UwbTable, start_s: float, end_s: float,
                             heading_grid_deg: Iterable[float]) -> dict:
    reproduced = reproduce_historical_0902(table, start_s, end_s)
    rows = reproduced.pop("source_rows")
    historical = table.historical_root_candidate_m
    prefix = ((table.measurement_s >= start_s) & (table.measurement_s <= end_s) &
              table.historical_candidate_valid & np.all(np.isfinite(historical), axis=1))
    tag_medians = np.full((10, 3), np.nan)
    within = {}; tag_contribution = {}
    for node_index, node in enumerate(table.node_ids):
        values = historical[prefix & (table.node_index == node_index)]
        if len(values):
            tag_medians[node_index] = np.median(values, axis=0)
            distances = np.linalg.norm(values - tag_medians[node_index], axis=1)
            within[node] = {"robust_radial_m": 1.4826 * float(np.median(distances)),
                            "radial_m": summary(distances),
                            "xy_radial_m": summary(np.linalg.norm(values[:, :2] - tag_medians[node_index, :2], axis=1)),
                            "z_abs_m": summary(np.abs(values[:, 2] - tag_medians[node_index, 2]))}
    global_tag_median = np.nanmedian(tag_medians, axis=0)
    between_distance = np.linalg.norm(tag_medians - global_tag_median, axis=1)
    for node_index, node in enumerate(table.node_ids):
        selected = rows[table.node_index[rows] == node_index]
        tag_contribution[node] = {
            "events": int(len(selected)),
            "historical_root_distance_to_global_tag_median_m": summary(
                np.linalg.norm(historical[selected] - global_tag_median, axis=1) if len(selected) else np.array([])),
        }

    strict_common = table.m1_valid & table.historical_candidate_valid
    timing_delta = np.linalg.norm(table.root_observation_identity_m[strict_common] - historical[strict_common], axis=1)
    heading_rows = []
    for degrees in heading_grid_deg:
        rotation = yaw_rotation(float(degrees))
        root = table.xyz_m - table.relative_point_m @ rotation.T
        valid = table.m1_valid & np.all(np.isfinite(root), axis=1)
        residual, _ = _snapshot_residuals(table.node_index, table.measurement_s, table.availability_s,
                                          root, valid, start_s, end_s)
        heading_rows.append({"yaw_deg": float(degrees),
                             "robust_scale_m": None if not len(residual) else max(0.020, 1.4826 * float(np.median(residual))),
                             "events": int(len(residual))})

    anchor = {}
    historical_residual = []
    if len(rows):
        # Use the exact historical residual definition again, retaining row identity.
        residual_values, source_rows = _snapshot_residuals(
            table.node_index, table.measurement_s, table.availability_s,
            historical, table.historical_candidate_valid, start_s, end_s)
        historical_residual = residual_values
        for aid in range(8):
            values = residual_values[np.asarray([aid in table.anchors[row] for row in source_rows], bool)]
            anchor[str(aid)] = summary(values)
    gross_cut = float(np.quantile(historical_residual, 0.99)) if len(historical_residual) else math.nan
    clean = historical_residual[historical_residual <= gross_cut] if len(historical_residual) else np.array([])
    relative_radius = np.linalg.norm(table.relative_point_m[prefix], axis=1)
    theoretical_90 = 2.0 * relative_radius * math.sin(math.pi / 4.0)
    return {
        "schema": "biospur.root_r3.dispersion_decomposition.v1",
        "historical_reproduction": reproduced,
        "within_tag_temporal_jitter": within,
        "between_tag_fixed_median_disagreement": {
            "global_coordinate_median_m": global_tag_median.tolist(),
            "per_tag_median_m": {node: tag_medians[index].tolist() for index, node in enumerate(table.node_ids)},
            "distance_m": summary(between_distance),
            "robust_radial_m": 1.4826 * float(np.nanmedian(between_distance)),
            "xy_distance_m": summary(np.linalg.norm(tag_medians[:, :2] - global_tag_median[:2], axis=1)),
            "z_abs_m": summary(np.abs(tag_medians[:, 2] - global_tag_median[2])),
        },
        "tag_specific_contribution": tag_contribution,
        "anchor_association": anchor,
        "strict_past_vs_historical_m1_timing_delta_m": summary(timing_delta),
        "gross_outlier_contribution": {
            "p99_cut_m": None if not np.isfinite(gross_cut) else gross_cut,
            "fraction": 0.01 if len(historical_residual) else None,
            "robust_scale_without_top_1pct_m": None if not len(clean) else 1.4826 * float(np.median(clean)),
        },
        "heading_sensitivity_not_for_selection": heading_rows,
        "heading_formula_90deg_at_observed_m1_radius_m": summary(theoretical_90),
        "orientation_radius_m": summary(relative_radius),
        "frame_conclusion": "Root-R2 subtracted M1 points directly from V4 positions despite V4 being RELATIVE_GEOMETRY_ONLY and M1 yaw being an arbitrary gauge; yaw sweep is diagnostic and no row is an accepted transform.",
        "point_conclusion": "U_i, I_i, D_i, A_i, and S_i were not measured as coincident; zero lever arm remains uncertainty, not a fitted correction.",
    }


def pairwise_invariants(table: C1UwbTable, *, stale_s: float = 0.18) -> dict:
    latest_xyz = np.full((10, 3), np.nan); latest_relative = np.full((10, 3), np.nan)
    latest_time = np.full(10, np.nan); latest_anchors: list[tuple[int, ...]] = [()] * 10
    by_pair: dict[str, list[tuple[float, float, float, float, float, float]]] = defaultdict(list)
    order = np.argsort(table.availability_s, kind="stable")
    for row in order:
        if not table.m1_valid[row]:
            continue
        ni = int(table.node_index[row]); now = float(table.availability_s[row])
        latest_xyz[ni] = table.xyz_m[row]; latest_relative[ni] = table.relative_point_m[row]
        latest_time[ni] = table.measurement_s[row]; latest_anchors[ni] = table.anchors[row]
        for other in range(10):
            if other == ni or not np.isfinite(latest_time[other]):
                continue
            if now - latest_time[other] > stale_s:
                continue
            lo, hi = sorted((ni, other)); key = f"{table.node_ids[lo]}__{table.node_ids[hi]}"
            uwb_delta = latest_xyz[ni] - latest_xyz[other]
            m1_delta = latest_relative[ni] - latest_relative[other]
            by_pair[key].append((
                float(np.linalg.norm(uwb_delta)), float(np.linalg.norm(m1_delta)),
                float(np.linalg.norm(uwb_delta[:2])), float(np.linalg.norm(m1_delta[:2])),
                float(abs(uwb_delta[2])), float(abs(m1_delta[2])),
            ))
    result = {}; all_error = []
    for key, values in sorted(by_pair.items()):
        array = np.asarray(values)
        error = array[:, 0] - array[:, 1]; all_error.extend(error.tolist())
        result[key] = {
            "samples": len(values),
            "separation_error_m": summary(np.abs(error)),
            "signed_separation_error_m": summary(error),
            "uwb_separation_m": summary(array[:, 0]),
            "m1_point_separation_m": summary(array[:, 1]),
            "uwb_native_xy_m": summary(array[:, 2]),
            "m1_native_xy_m": summary(array[:, 3]),
            "uwb_native_z_m": summary(array[:, 4]),
            "m1_native_z_m": summary(array[:, 5]),
        }
    return {
        "schema": "biospur.root_r3.pairwise_invariants.v1",
        "pairs_expected": 45,
        "pairs_reported": len(result),
        "all_pair_absolute_separation_error_m": summary(np.abs(all_error)),
        "pairs": result,
        "axis_warning": "3D separation is rotation invariant. UWB and M1 XY/Z columns are native-frame diagnostics and are not subtracted because R_N_from_V4 is unqualified.",
    }


def redundancy_ablation(table: C1UwbTable, start_s: float, end_s: float) -> dict:
    root = table.root_observation_identity_m
    valid = table.m1_valid & np.all(np.isfinite(root), axis=1)
    subsets = {
        "pelvis_only": {1},
        "pelvis_plus_torso": {0, 1},
        "all_trunk_tags": {0, 1},
        "all_ten_tags": set(range(10)),
    }
    subset_rows = {}
    for name, nodes in subsets.items():
        residual, _ = _snapshot_residuals(table.node_index, table.measurement_s, table.availability_s,
                                          root, valid, start_s, end_s, allowed_nodes=nodes)
        subset_rows[name] = {"nodes": [table.node_ids[index] for index in sorted(nodes)],
                             "residual_m": summary(residual),
                             "robust_scale_m": None if not len(residual) else 1.4826 * float(np.median(residual))}
    leave_tag = {}
    for excluded in range(10):
        nodes = set(range(10)) - {excluded}
        residual, _ = _snapshot_residuals(table.node_index, table.measurement_s, table.availability_s,
                                          root, valid, start_s, end_s, allowed_nodes=nodes)
        leave_tag[table.node_ids[excluded]] = {"residual_m": summary(residual),
                                              "robust_scale_m": None if not len(residual) else 1.4826 * float(np.median(residual))}
    leave_anchor = {}
    for anchor in range(8):
        # This is deliberately an event-exclusion diagnostic. Re-solving T4
        # after removing a range would be B5 and is frame-blocked for C1.
        disallowed = np.asarray([anchor in anchors for anchors in table.anchors], bool)
        residual, _ = _snapshot_residuals(table.node_index, table.measurement_s, table.availability_s,
                                          root, valid, start_s, end_s,
                                          disallowed_anchor_rows=disallowed)
        leave_anchor[str(anchor)] = {"event_exclusion_only": True,
                                     "events_retained": int((~disallowed & valid).sum()),
                                     "residual_m": summary(residual)}
    prefix = (table.measurement_s >= start_s) & (table.measurement_s <= end_s) & valid
    centre = np.nanmedian(root[prefix], axis=0)
    return {
        "schema": "biospur.root_r3.redundancy_ablation.v1",
        "frame_status": "IDENTITY_ASSUMPTION_DIAGNOSTIC_NOT_SCIENTIFIC",
        "subsets": subset_rows,
        "leave_one_tag_out": leave_tag,
        "leave_one_anchor_out": leave_anchor,
        "xy_vs_full_xyz": {
            "xy_distance_to_prefix_median_m": summary(np.linalg.norm(root[prefix, :2] - centre[:2], axis=1)),
            "z_abs_to_prefix_median_m": summary(np.abs(root[prefix, 2] - centre[2])),
            "full_distance_to_prefix_median_m": summary(np.linalg.norm(root[prefix] - centre, axis=1)),
        },
        "direct_range_ablation": "BLOCKED_FRAME_BINDING_NOT_EXECUTED",
    }


def trajectory_metrics(times_s: np.ndarray, positions_m: np.ndarray, valid: np.ndarray | None = None) -> dict:
    times = np.asarray(times_s, float); positions = np.asarray(positions_m, float)
    mask = np.all(np.isfinite(positions), axis=1) if valid is None else np.asarray(valid, bool) & np.all(np.isfinite(positions), axis=1)
    indices = np.flatnonzero(mask)
    if len(indices) < 3:
        return {"samples": int(len(indices)), "status": "INSUFFICIENT"}
    t = times[indices]; p = positions[indices]
    dt = np.diff(t); keep = dt > 1e-9
    increments = np.linalg.norm(np.diff(p, axis=0)[keep], axis=1)
    velocity = np.diff(p, axis=0)[keep] / dt[keep, None]
    velocity_t = 0.5 * (t[1:][keep] + t[:-1][keep])
    vdt = np.diff(velocity_t); vkeep = vdt > 1e-9
    acceleration = np.diff(velocity, axis=0)[vkeep] / vdt[vkeep, None]
    acceleration_t = 0.5 * (velocity_t[1:][vkeep] + velocity_t[:-1][vkeep])
    adt = np.diff(acceleration_t); akeep = adt > 1e-9
    jerk = np.diff(acceleration, axis=0)[akeep] / adt[akeep, None]
    return {
        "samples": int(len(indices)),
        "span_s": float(t[-1] - t[0]),
        "root_increment_m": summary(increments),
        "speed_mps": summary(np.linalg.norm(velocity, axis=1)),
        "acceleration_mps2": summary(np.linalg.norm(acceleration, axis=1)),
        "jerk_mps3": summary(np.linalg.norm(jerk, axis=1)),
        "xy_span_m": float(np.linalg.norm(np.ptp(p[:, :2], axis=0))),
        "z_span_m": float(np.ptp(p[:, 2])),
        "end_to_start_m": float(np.linalg.norm(p[-1] - p[0])),
    }


def pareto_frontier(rows: list[dict], minimize: tuple[str, ...], maximize: tuple[str, ...] = ()) -> list[dict]:
    frontier = []
    for candidate in rows:
        dominated = False
        for other in rows:
            if other is candidate:
                continue
            weak = all(other[key] <= candidate[key] for key in minimize) and all(other[key] >= candidate[key] for key in maximize)
            strict = any(other[key] < candidate[key] for key in minimize) or any(other[key] > candidate[key] for key in maximize)
            if weak and strict:
                dominated = True; break
        if not dominated:
            frontier.append(candidate)
    return frontier
