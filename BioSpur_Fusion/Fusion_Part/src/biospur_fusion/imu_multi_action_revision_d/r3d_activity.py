"""Gauge-invariant broad activity masks for Revision-D R3D.

This module deliberately does not infer physical relative segment orientation.
Each Q2 node is reduced only to the magnitude of its own right increment, which
is invariant to that node's constant, independently unobservable left yaw.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial.transform import Rotation


ACTIONS = (
    "initial_still_attempt2",
    "t_pose",
    "arms",
    "left_elbow",
    "right_elbow_attempt2",
    "left_knee",
    "right_knee",
    "left_heel",
    "right_heel",
    "squats",
    "trunk",
)


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edge = np.diff(np.r_[False, np.asarray(mask, bool), False].astype(np.int8))
    return [
        (int(start), int(stop))
        for start, stop in zip(np.flatnonzero(edge == 1), np.flatnonzero(edge == -1))
    ]


def smooth_valid(values: np.ndarray, valid: np.ndarray, count: int) -> np.ndarray:
    count = max(1, int(count))
    kernel = np.ones(count)
    numerator = np.convolve(np.where(valid, values, 0.0), kernel, mode="same")
    denominator = np.convolve(np.asarray(valid, float), kernel, mode="same")
    return np.divide(
        numerator,
        denominator,
        out=np.full(len(values), np.nan),
        where=denominator > 0,
    )


def incremental_activity(
    time_ns: np.ndarray,
    rotation: np.ndarray,
    valid: np.ndarray,
    contract: Mapping[str, Any],
) -> dict[str, np.ndarray]:
    """Return norm(Log(R(t-dt)^T R(t)))/dt for one Q2 node."""
    time_ns = np.asarray(time_ns, np.int64)
    rotation = np.asarray(rotation, float)
    valid = np.asarray(valid, bool) & np.isfinite(rotation).all(axis=(1, 2))
    dt_s = np.r_[np.nan, np.diff(time_ns) / 1e9]
    maximum_gap = float(contract["activity"]["maximum_sample_gap_s"])
    pair_valid = valid & np.r_[False, valid[:-1]] & (dt_s > 0.0) & (dt_s <= maximum_gap)
    increment = np.full(len(time_ns), np.nan)
    indices = np.flatnonzero(pair_valid)
    if len(indices):
        delta = np.einsum(
            "nji,njk->nik", rotation[indices - 1], rotation[indices]
        )
        increment[indices] = Rotation.from_matrix(delta).magnitude()
    rate = increment / dt_s
    nominal_hz = 1.0 / float(np.median(dt_s[np.isfinite(dt_s) & (dt_s > 0)]))
    smooth_count = max(
        1, round(float(contract["activity"]["smoothing_duration_s"]) * nominal_hz)
    )
    return {
        "increment_rad": increment,
        "rate_rad_s": smooth_valid(rate, pair_valid, smooth_count),
        "dt_s": dt_s,
        "valid": pair_valid,
    }


def quiet_baseline(
    activity: Mapping[str, np.ndarray],
    quiet_rows: np.ndarray,
    contract: Mapping[str, Any],
) -> dict[str, Any] | None:
    cfg = contract["activity"]
    time_step = np.asarray(activity["dt_s"], float)
    positive = time_step[np.isfinite(time_step) & (time_step > 0)]
    if not len(positive):
        return None
    hz = 1.0 / float(np.median(positive))
    length = max(2, round(float(cfg["baseline_candidate_duration_s"]) * hz))
    stride = max(1, round(float(cfg["baseline_candidate_stride_s"]) * hz))
    rate = np.asarray(activity["rate_rad_s"], float)
    valid = np.asarray(activity["valid"], bool)
    candidates: list[tuple[float, float, int, np.ndarray, float]] = []
    for start in range(0, max(1, len(quiet_rows) - length + 1), stride):
        block = quiet_rows[start : start + length]
        keep = valid[block] & np.isfinite(rate[block])
        fraction = float(np.mean(keep)) if len(block) else 0.0
        if (
            len(block) != length
            or fraction < float(cfg["minimum_baseline_valid_fraction"])
            or not np.any(keep)
        ):
            continue
        values = rate[block][keep]
        candidates.append(
            (
                float(np.median(values)),
                float(np.percentile(values, 90)),
                int(block[0]),
                block,
                fraction,
            )
        )
    if not candidates:
        return None
    location, p90, _, block, fraction = min(candidates, key=lambda x: x[:3])
    keep = valid[block] & np.isfinite(rate[block])
    values = rate[block][keep]
    mad = float(np.median(np.abs(values - location)))
    robust_scale = 1.4826 * mad
    floor = float(cfg["production_floor_rad_s"])
    scale = max(robust_scale, floor)
    return {
        "row_indices": block.tolist(),
        "start_row": int(block[0]),
        "stop_row_exclusive": int(block[-1] + 1),
        "activity_median_rad_s": location,
        "activity_p90_rad_s": p90,
        "activity_mad_rad_s": mad,
        "empirical_robust_scale_rad_s": robust_scale,
        "production_floor_rad_s": floor,
        "activity_scale_rad_s": scale,
        "selected_scale": "EMPIRICAL_MAD" if robust_scale >= floor else "FROZEN_0P035_RAD_S_FLOOR",
        "valid_fraction": fraction,
        "effective_sample_count": int(np.sum(keep)),
        "role": "TRIGGER_NORMALIZER_ONLY_NOT_D0_ORIENTATION_COVARIANCE",
    }


def normalized_activity(
    activity: Mapping[str, np.ndarray], baseline: Mapping[str, Any]
) -> np.ndarray:
    rate = np.asarray(activity["rate_rad_s"], float)
    return (rate - float(baseline["activity_median_rad_s"])) / float(
        baseline["activity_scale_rad_s"]
    )


def broad_active_mask(
    z: np.ndarray,
    valid: np.ndarray,
    rows: np.ndarray,
    contract: Mapping[str, Any],
) -> np.ndarray:
    """Deterministic hysteretic broad mask; it never estimates cycles."""
    cfg = contract["broad_mask"]
    rows = np.asarray(rows, int)
    z = np.asarray(z, float)
    valid = np.asarray(valid, bool)
    output = np.zeros(len(z), bool)
    if not len(rows):
        return output
    hz = float(contract["activity"]["common_time_rate_hz"])
    onset_n = max(1, round(float(cfg["onset_minimum_duration_s"]) * hz))
    offset_n = max(1, round(float(cfg["offset_minimum_duration_s"]) * hz))
    bridge_n = max(0, round(float(cfg["maximum_bridgeable_low_activity_gap_s"]) * hz))
    minimum_n = max(1, round(float(cfg["minimum_bout_duration_s"]) * hz))
    local_valid = valid[rows] & np.isfinite(z[rows])
    high = local_valid & (z[rows] >= float(cfg["onset_z"]))
    low = local_valid & (z[rows] <= float(cfg["offset_z"]))
    accepted: list[tuple[int, int]] = []
    for start, stop in runs(high):
        if stop - start < onset_n:
            continue
        left = start
        right = stop
        cursor = stop
        while cursor < len(rows):
            if not local_valid[cursor]:
                break
            if cursor + offset_n <= len(rows) and np.all(low[cursor : cursor + offset_n]):
                right = cursor
                break
            right = cursor + 1
            cursor += 1
        accepted.append((left, max(right, stop)))
    if not accepted:
        return output
    merged: list[list[int]] = []
    for start, stop in sorted(accepted):
        if merged and start - merged[-1][1] <= bridge_n:
            merged[-1][1] = max(merged[-1][1], stop)
        else:
            merged.append([start, stop])
    for start, stop in merged:
        if stop - start >= minimum_n:
            output[rows[start:stop]] = True
    return output


def _action_roles(
    action: str,
    mapping: Mapping[str, Any],
    segment_to_node: Mapping[str, str],
    all_nodes: Sequence[str],
) -> dict[str, list[str]]:
    item = mapping["actions"][action]
    if action in ("initial_still_attempt2", "t_pose"):
        nodes = sorted(all_nodes)
        return {"relevant": nodes, "primary": nodes, "proximal": nodes}
    primary: set[str] = set()
    proximal: set[str] = set()
    relevant: set[str] = set()
    for group in ("primary_chains", "diagnostic_chains"):
        for parent, child in item.get(group, {}).values():
            parent_node = segment_to_node[parent]
            child_node = segment_to_node[child]
            relevant.update((parent_node, child_node))
            proximal.add(parent_node)
            if group == "primary_chains":
                primary.add(child_node)
    return {
        "relevant": sorted(relevant),
        "primary": sorted(primary),
        "proximal": sorted(proximal),
    }


def row_hash(action: str, node: str, array_key: str, rows: np.ndarray, time_ns: np.ndarray) -> str:
    h = hashlib.sha256()
    for value in (action, node, array_key):
        h.update(value.encode("utf-8") + b"\0")
    rows = np.asarray(rows, np.int64)
    h.update(rows.astype("<i8", copy=False).tobytes())
    h.update(np.asarray(time_ns, np.int64)[rows].astype("<i8", copy=False).tobytes())
    return h.hexdigest()


def static_plateau(
    per_node: Mapping[str, Mapping[str, Any]],
    nodes: Sequence[str],
    rows: np.ndarray,
    contract: Mapping[str, Any],
) -> dict[str, Any] | None:
    cfg = contract["broad_mask"]
    hz = float(contract["activity"]["common_time_rate_hz"])
    length = max(2, round(float(cfg["static_plateau_duration_s"]) * hz))
    stride = max(1, round(0.1 * hz))
    candidates = []
    for start in range(0, max(1, len(rows) - length + 1), stride):
        block = rows[start : start + length]
        if len(block) != length:
            continue
        matrix = np.stack([per_node[node]["activity"]["rate_rad_s"][block] for node in nodes], axis=1)
        valid = np.stack([per_node[node]["activity"]["valid"][block] for node in nodes], axis=1)
        keep = np.all(valid & np.isfinite(matrix), axis=1)
        if float(np.mean(keep)) < float(cfg["minimum_action_valid_fraction"]):
            continue
        score = float(np.median(matrix[keep]))
        candidates.append((score, int(block[0]), block, float(np.percentile(matrix[keep], 95))))
    if not candidates:
        return None
    score, _, block, p95 = min(candidates, key=lambda x: x[:2])
    return {
        "row_indices": block.tolist(),
        "start_row": int(block[0]),
        "stop_row_exclusive": int(block[-1] + 1),
        "row_count": int(len(block)),
        "aggregate_activity_median_rad_s": score,
        "aggregate_activity_p95_rad_s": p95,
    }


def analyze_broad_actions(
    timeline: Any,
    domains: Mapping[str, tuple[int, int]],
    mapping: Mapping[str, Any],
    node_to_segment: Mapping[str, str],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Build per-node activity and action masks without relative orientation."""
    if tuple(domains) != ACTIONS and set(domains) != set(ACTIONS):
        missing = sorted(set(ACTIONS) - set(domains))
        if missing:
            raise ValueError(f"missing actions: {missing}")
    node_index = {node: index for index, node in enumerate(timeline.node_order)}
    segment_to_node = {segment: node for node, segment in node_to_segment.items()}
    domain_rows = {
        action: np.flatnonzero(
            (timeline.time_ns >= domains[action][0]) & (timeline.time_ns <= domains[action][1])
        )
        for action in ACTIONS
    }
    quiet_rows = np.unique(
        np.r_[domain_rows["initial_still_attempt2"], domain_rows["t_pose"]]
    )
    per_node: dict[str, dict[str, Any]] = {}
    for node in timeline.node_order:
        index = node_index[node]
        activity = incremental_activity(
            timeline.time_ns,
            timeline.rotation[:, index],
            timeline.valid[:, index],
            contract,
        )
        baseline = quiet_baseline(activity, quiet_rows, contract)
        if baseline is None:
            per_node[node] = {"status": "FAIL_QUIET_REFERENCE", "activity": activity}
            continue
        z = normalized_activity(activity, baseline)
        per_node[node] = {
            "status": "AVAILABLE",
            "activity": activity,
            "baseline": baseline,
            "z": z,
        }
    actions: dict[str, Any] = {}
    coverage: list[dict[str, Any]] = []
    dynamic = set(contract["broad_mask"]["dynamic_actions"])
    for action in ACTIONS:
        rows = domain_rows[action]
        roles = _action_roles(action, mapping, segment_to_node, timeline.node_order)
        node_masks: dict[str, np.ndarray] = {}
        for node in roles["relevant"]:
            item = per_node[node]
            if item["status"] != "AVAILABLE":
                node_masks[node] = np.zeros(len(timeline.time_ns), bool)
            else:
                node_masks[node] = broad_active_mask(
                    item["z"], item["activity"]["valid"], rows, contract
                )
        valid_rows = np.asarray(
            [
                row
                for row in rows
                if all(per_node[node]["activity"]["valid"][row] for node in roles["relevant"])
            ],
            int,
        )
        if roles["relevant"]:
            broad_mask = np.any(np.stack([node_masks[node] for node in roles["relevant"]]), axis=0)
        else:
            broad_mask = np.zeros(len(timeline.time_ns), bool)
        primary_mask = (
            np.any(np.stack([node_masks[node] for node in roles["primary"]]), axis=0)
            if roles["primary"]
            else broad_mask.copy()
        )
        proximal_mask = (
            np.any(np.stack([node_masks[node] for node in roles["proximal"]]), axis=0)
            if roles["proximal"]
            else np.zeros(len(timeline.time_ns), bool)
        )
        broad_rows = rows[broad_mask[rows]]
        primary_rows = rows[primary_mask[rows]]
        proximal_rows = rows[proximal_mask[rows]]
        plateau = static_plateau(per_node, roles["relevant"], rows, contract)
        action_status = "PASS"
        if action in dynamic and (
            len(broad_rows) < int(contract["broad_mask"]["minimum_dynamic_action_active_rows"])
            or not len(primary_rows)
        ):
            action_status = "FAIL_REQUIRED_ACTION_ACTIVITY_MISSING"
        if len(valid_rows) / max(len(rows), 1) < float(contract["broad_mask"]["minimum_action_valid_fraction"]):
            action_status = "FAIL_VALID_SUPPORT"
        if action not in dynamic and (
            plateau is None
            or plateau["row_count"] < int(contract["broad_mask"]["minimum_static_plateau_rows"])
        ):
            action_status = "FAIL_STATIC_PLATEAU_MISSING"
        binding = []
        for node in roles["relevant"]:
            selected = rows[node_masks[node][rows]]
            item = {
                "action": action,
                "node": node,
                "segment": node_to_segment[node],
                "array_key": f"{action}__{node}__broad_active_rows",
                "row_count": int(len(selected)),
                "row_hash": row_hash(action, node, f"{action}__{node}__broad_active_rows", selected, timeline.time_ns),
            }
            binding.append(item)
            coverage.append({**item, "role_primary": node in roles["primary"], "role_proximal": node in roles["proximal"]})
        actions[action] = {
            "status": action_status,
            "kind": "DYNAMIC" if action in dynamic else "STATIC_LATENT_POSE",
            "domain_rows": rows,
            "BROAD_ACTIVE_ROWS": broad_rows,
            "PRIMARY_BOARD_ACTIVITY": primary_rows,
            "PROXIMAL_BOARD_ACTIVITY": proximal_rows,
            "VALID_ROWS": valid_rows,
            "QUIET_REFERENCE_ROWS": quiet_rows,
            "STATIC_PLATEAU_CANDIDATE": plateau,
            "roles": roles,
            "node_bindings": binding,
            "node_masks": node_masks,
        }
    return {
        "per_node": per_node,
        "actions": actions,
        "coverage": coverage,
        "domain_rows": domain_rows,
        "quiet_reference_rows": quiet_rows,
    }


def verify_chain_result_binding(
    records: Sequence[Mapping[str, Any]],
    time_ns: np.ndarray,
    row_lookup: Mapping[tuple[str, str], np.ndarray],
) -> bool:
    for record in records:
        key = (str(record["action"]), str(record["node"]))
        if key not in row_lookup:
            return False
        expected = row_hash(
            str(record["action"]),
            str(record["node"]),
            str(record["array_key"]),
            np.asarray(row_lookup[key], int),
            time_ns,
        )
        if expected != record["row_hash"]:
            return False
    return True


def inject_left_yaw(rotation: np.ndarray, alpha_rad: float) -> np.ndarray:
    c, s = math.cos(alpha_rad), math.sin(alpha_rad)
    gauge = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    return np.einsum("ij,njk->nik", gauge, np.asarray(rotation, float))


def strap_slip_diagnostic(
    time_ns: np.ndarray,
    rotation: np.ndarray,
    valid: np.ndarray,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    cfg = contract["strap_slip_diagnostic"]
    time_ns = np.asarray(time_ns, np.int64)
    rotation = np.asarray(rotation, float)
    valid = np.asarray(valid, bool)
    dt = np.r_[np.nan, np.diff(time_ns) / 1e9]
    support = max(2, round(float(cfg["minimum_pre_post_support_s"]) / float(np.nanmedian(dt[1:]))))
    candidates = []
    for row in range(support, len(time_ns) - support):
        if not valid[row - 1] or not valid[row] or dt[row] > float(cfg["maximum_transition_duration_s"]):
            continue
        local_step = Rotation.from_matrix(rotation[row - 1].T @ rotation[row]).magnitude()
        if local_step < float(cfg["minimum_persistent_pose_step_rad"]):
            continue
        pre_valid = valid[row - support : row]
        post_valid = valid[row : row + support]
        if not np.all(pre_valid) or not np.all(post_valid):
            continue
        pre = Rotation.from_matrix(rotation[row - support : row]).mean().as_matrix()
        post = Rotation.from_matrix(rotation[row : row + support]).mean().as_matrix()
        persistent = Rotation.from_matrix(pre.T @ post).magnitude()
        if persistent >= float(cfg["minimum_persistent_pose_step_rad"]):
            candidates.append(
                {
                    "row": row,
                    "time_ns": int(time_ns[row]),
                    "instantaneous_step_rad": float(local_step),
                    "persistent_pre_post_step_rad": float(persistent),
                }
            )
    return {
        "classification": "STRAP_SLIP_DIAGNOSTIC_FAILURE" if candidates else "NO_STRAP_SLIP_DETECTED",
        "candidates": candidates,
        "consumed_as_activity_evidence": False,
    }


def frame_manifest() -> dict[str, Any]:
    return {
        "schema": "biospur-r3d-q2-frame-manifest-v1",
        "matrices": [
            {
                "matrix_name": "R_Ni_Bi = quaternion_to_matrix(Q2.q_wxyz[node_i])",
                "source_frame": "B_i sensor/board frame",
                "destination_frame": "N_i per-node gravity-aligned navigation frame",
                "active_or_passive": "ACTIVE",
                "multiplication_order": "v_Ni = R_Ni_Bi * v_Bi; gyro increments multiply quaternion on the right",
                "whether_destination_heading_is_shared_across_nodes": False,
                "observable_axes": ["gravity/tilt roll", "gravity/tilt pitch"],
                "unobservable_gauges": ["independent constant yaw alpha_i about N_i +Z"],
            },
            {
                "matrix_name": "R_parent^T * R_child (historical R3C)",
                "source_frame": "B_child only if N_parent == N_child, which is unproven",
                "destination_frame": "B_parent only if N_parent == N_child, which is unproven",
                "active_or_passive": "COMPOSED_ACTIVE_MATRIX_WITH_UNBOUND_INTERMEDIATE_FRAMES",
                "multiplication_order": "R_Np_Bp^T * R_Nc_Bc",
                "whether_destination_heading_is_shared_across_nodes": False,
                "observable_axes": [],
                "unobservable_gauges": ["alpha_parent", "alpha_child"],
            },
            {
                "matrix_name": "DeltaR_i(t) = R_i(t-dt)^T * R_i(t) (R3D)",
                "source_frame": "B_i(t)",
                "destination_frame": "B_i(t-dt)",
                "active_or_passive": "ACTIVE_RIGHT_INCREMENT",
                "multiplication_order": "R_Ni_Bi(t-dt)^T * R_Ni_Bi(t)",
                "whether_destination_heading_is_shared_across_nodes": "NOT_REQUIRED",
                "observable_axes": ["increment magnitude only"],
                "unobservable_gauges": ["direction is not published before D0"],
            },
        ],
        "analytic_gauge_transform": {
            "node_transform": "R_i' = G_i(alpha_i) R_i; G_i(alpha_i)=Rz(alpha_i)",
            "historical_pair_transform": "R_parent'^T R_child' = R_parent^T G_parent^T G_child R_child",
            "historical_pair_invariant_for_independent_yaw": False,
            "exception": "Only G_parent == G_child cancels",
            "replacement_increment_transform": "R_i'(t-dt)^T R_i'(t) = R_i(t-dt)^T G_i^T G_i R_i(t)",
            "replacement_increment_invariant_for_constant_independent_yaw": True,
        },
        "Q2_DESTINATION_FRAMES_SHARED_ACROSS_NODES": False,
    }
