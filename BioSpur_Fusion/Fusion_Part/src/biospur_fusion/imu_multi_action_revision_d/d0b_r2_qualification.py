"""Preflight and qualification utilities for D0B-R2."""
from __future__ import annotations

import copy
import hashlib
import math
from typing import Any, Callable, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_engineering_v1.common_time import build_common_timeline
from biospur_fusion.imu_multi_action_engineering_v1.q2 import run_q2_frontend_v1

from .d0b_r1_model import PRODUCT_DIMENSION, R1Observation, blind_initialization, production_jacobian
from .d0b_r2_lineage import FactorSelection, R2Objective, factor_value_map, freeze_selection
from .r3d_activity import analyze_broad_actions


def build_observation(
    imus: Mapping[str, np.ndarray], windows: Mapping[str, tuple[int, int]],
    node_to_segment: Mapping[str, str], contract: Mapping[str, Any],
    r3d_contract: Mapping[str, Any], chain_map: Mapping[str, Any],
) -> tuple[R1Observation, dict[str, Any]]:
    q2, q2_audit, _, _ = run_q2_frontend_v1(imus, windows, contract["q2"])
    timeline = build_common_timeline(q2, min(value[0] for value in windows.values()), max(value[1] for value in windows.values()), contract["common_time"])
    r3d = analyze_broad_actions(timeline, windows, chain_map, node_to_segment, r3d_contract)
    observation = R1Observation(timeline.time_ns, timeline.node_order, timeline.rotation, timeline.gyro_rad_s, timeline.valid, dict(windows), dict(node_to_segment), r3d["actions"])
    return observation, {"q2": q2_audit, "common_time": timeline.accounting, "action_status": {key: value["status"] for key, value in r3d["actions"].items()}}


def phase_time_bounds(selection: FactorSelection, windows: Mapping[str, tuple[int, int]]) -> tuple[int, int]:
    start, stop = windows[selection.action_id]
    if selection.phase_id in ("STATIC_PLATEAU", "BROAD_ACTIVE"): return start, stop
    if selection.phase_id == "CURL": return start, start + (stop - start) // 2
    if selection.phase_id == "PRONATION_SUPINATION": return start + (stop - start) // 2 + 1, stop
    first = start + (stop - start) // 3; second = start + 2 * (stop - start) // 3
    if selection.phase_id == "LEFT_TURN": return start, first
    if selection.phase_id == "RIGHT_TURN": return first + 1, second
    if selection.phase_id == "FORWARD_FLEXION_RECOVERY": return second + 1, stop
    raise ValueError(selection.phase_id)


def _copy_imus(imus: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    return {node: value.copy() for node, value in imus.items()}


def _required_nodes(selection: FactorSelection) -> tuple[str, ...]:
    return tuple(item for item in (selection.parent_node, selection.child_node) if item is not None)


def _changed_observation(observation: R1Observation, selection: FactorSelection) -> R1Observation:
    rotation = observation.rotation.copy(); gyro = observation.gyro_rad_s.copy()
    row = int(selection.selected_rows[0]); child = observation.node_order.index(selection.child_node)
    if selection.phase_id == "STATIC_PLATEAU":
        rotation[row, child] = Rotation.from_rotvec([0.031, -0.017, 0.023]).as_matrix() @ rotation[row, child]
    else:
        gyro[row, child] += np.array([0.19, -0.11, 0.07])
    return R1Observation(observation.time_ns, observation.node_order, rotation, gyro, observation.valid.copy(), observation.windows, observation.node_to_segment, observation.r3d_actions)


def _factor_reference_set(selection: FactorSelection) -> set[tuple[int, str, str]]:
    return {(int(row), node, selection.source_channel) for row in selection.selected_rows for node in _required_nodes(selection)}


def _constant_frame_aligned_relative_gyro_rms(observation: R1Observation, selection: FactorSelection) -> float:
    """Gauge-invariant residual after one constant SO(3) frame alignment."""
    rows = selection.selected_rows
    parent = observation.node_order.index(selection.parent_node)
    child = observation.node_order.index(selection.child_node)
    p = observation.gyro_rad_s[rows, parent]
    c = observation.gyro_rad_s[rows, child]
    u, _, vh = np.linalg.svd(p.T @ c)
    rotation = u @ np.diag([1.0, 1.0, np.linalg.det(u @ vh)]) @ vh
    residual = p - np.einsum("ij,nj->ni", rotation, c)
    return float(np.sqrt(np.mean(np.square(residual))))


def negative_controls_v2(
    imus: Mapping[str, np.ndarray], windows: Mapping[str, tuple[int, int]], node_to_segment: Mapping[str, str],
    observation: R1Observation, contract: Mapping[str, Any], r3d_contract: Mapping[str, Any], chain_map: Mapping[str, Any],
    heartbeat: Callable[[str], None] | None = None,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    selections, manifest = freeze_selection(observation, contract)
    x = blind_initialization(observation, contract)
    baseline = R2Objective(observation, contract, selections, manifest["manifest_sha256"])
    base_values = factor_value_map(baseline, x)
    rng = np.random.default_rng(81271); direction = rng.normal(size=len(x)); direction /= np.linalg.norm(direction)
    h = 1e-5
    base_plus = factor_value_map(baseline, x + h * direction); base_minus = factor_value_map(baseline, x - h * direction)
    base_jv = {key: (base_plus[key] - base_minus[key]) / (2.0 * h) for key in base_values}
    records = []
    selection_lookup = {item.factor_block_id: item for item in selections}
    references = {item.factor_block_id: _factor_reference_set(item) for item in selections}
    for index, selection in enumerate(selections):
        if heartbeat: heartbeat(f"NC {index + 1}/{len(selections)} {selection.factor_block_id}")
        # NC-A: remove the complete factor-observation channel after the
        # shared Q2 frontend but before factor feature extraction/selection.
        # The Q2 initial gravity reference is shared infrastructure and must
        # not be destroyed merely to ablate one downstream factor.
        phase_start, phase_stop = phase_time_bounds(selection, windows)
        deleted_valid = observation.valid.copy()
        phase_rows = np.flatnonzero((observation.time_ns >= phase_start) & (observation.time_ns <= phase_stop))
        for node in _required_nodes(selection):
            deleted_valid[phase_rows, observation.node_order.index(node)] = False
        deleted_observation = R1Observation(observation.time_ns, observation.node_order, observation.rotation, observation.gyro_rad_s, deleted_valid, observation.windows, observation.node_to_segment, observation.r3d_actions)
        deleted_selections, _ = freeze_selection(deleted_observation, contract)
        deleted_target = next(item for item in deleted_selections if item.factor_block_id == selection.factor_block_id)
        nc_a = {
            "factor_status": deleted_target.status,
            "residual_rows": int(len(deleted_target.selected_rows) * deleted_target.residual_components_per_sample),
            "cross_action_backfill": 0, "cross_phase_backfill": 0, "cross_chain_backfill": 0,
            "pass": deleted_target.status == "NO_SOURCE_SUPPORT" and not len(deleted_target.selected_rows),
        }

        # NC-B: fixed row set; change underlying observation only.
        changed_obs = _changed_observation(observation, selection)
        changed_obj = R2Objective(changed_obs, contract, selections, manifest["manifest_sha256"])
        changed_values = factor_value_map(changed_obj, x)
        changed_plus = factor_value_map(changed_obj, x + h * direction); changed_minus = factor_value_map(changed_obj, x - h * direction)
        changed_jv = (changed_plus[selection.factor_block_id] - changed_minus[selection.factor_block_id]) / (2.0 * h)
        target_change = float(np.linalg.norm(changed_values[selection.factor_block_id] - base_values[selection.factor_block_id]))
        cost_change = abs(float(0.5 * changed_values[selection.factor_block_id] @ changed_values[selection.factor_block_id] - 0.5 * base_values[selection.factor_block_id] @ base_values[selection.factor_block_id]))
        jv_change = float(np.linalg.norm(changed_jv - base_jv[selection.factor_block_id]))
        unrelated = [key for key in base_values if references[key].isdisjoint(references[selection.factor_block_id])]
        unrelated_max = max((float(np.max(np.abs(changed_values[key] - base_values[key]))) for key in unrelated), default=0.0)
        nc_b = {"target_residual_l2_change": target_change, "target_cost_absolute_change": cost_change, "target_jv_l2_change": jv_change, "unrelated_factor_max_absolute_change": unrelated_max, "frozen_manifest_sha256": manifest["manifest_sha256"], "pass": target_change > 0.0 and cost_change > 0.0 and jv_change > 0.0 and unrelated_max == 0.0}

        # NC-D: selected-row withholding permits only same-pool backfill.
        withheld, _ = freeze_selection(observation, contract, withhold_selected={selection.factor_block_id: selection.selected_rows})
        backfill = next(item for item in withheld if item.factor_block_id == selection.factor_block_id)
        allowed_remaining = set(selection.candidate_rows.tolist()) - set(selection.selected_rows.tolist())
        backfill_set = set(backfill.selected_rows.tolist())
        same_allowlist = backfill_set.issubset(allowed_remaining)
        expected_count = min(len(selection.selected_rows), len(allowed_remaining))
        nc_d = {"backfill_allowed": True, "backfill_within_same_allowlist": same_allowlist, "duplicate_rows": len(backfill.selected_rows) - len(backfill_set), "baseline_selected_count": len(selection.selected_rows), "remaining_support_count": len(allowed_remaining), "backfill_count": len(backfill.selected_rows), "ineligible_due_to_insufficient_remaining_support": len(allowed_remaining) == 0, "pass": same_allowlist and len(backfill.selected_rows) == expected_count and len(backfill.selected_rows) == len(backfill_set)}

        # NC-E: mutate one row outside the target candidate pool. Frozen target
        # rows and values must remain byte-identical.
        outside = np.setdiff1d(np.arange(len(observation.time_ns)), selection.candidate_rows)
        sham_obs = observation
        if len(outside):
            sham_rotation = observation.rotation.copy(); sham_gyro = observation.gyro_rad_s.copy(); row = int(outside[0]); child = observation.node_order.index(selection.child_node)
            if selection.phase_id == "STATIC_PLATEAU": sham_rotation[row, child] = Rotation.from_rotvec([0.04, 0.0, 0.0]).as_matrix() @ sham_rotation[row, child]
            else: sham_gyro[row, child] += np.array([0.2, 0.0, 0.0])
            sham_obs = R1Observation(observation.time_ns, observation.node_order, sham_rotation, sham_gyro, observation.valid.copy(), observation.windows, observation.node_to_segment, observation.r3d_actions)
        sham_obj = R2Objective(sham_obs, contract, selections, manifest["manifest_sha256"]); sham_value = factor_value_map(sham_obj, x)[selection.factor_block_id]
        nc_e = {"outside_candidate_row": int(outside[0]) if len(outside) else None, "row_manifest_unchanged": True, "residual_byte_identical": sham_value.tobytes() == base_values[selection.factor_block_id].tobytes(), "pass": sham_value.tobytes() == base_values[selection.factor_block_id].tobytes()}
        records.append({"factor_block_id": selection.factor_block_id, "action": selection.action_id, "phase": selection.phase_id, "chain": selection.chain_id, "NC_A": nc_a, "NC_B": nc_b, "NC_D": nc_d, "NC_E": nc_e})

    # NC-C is action-wise at raw synthetic level. All nodes in the action's
    # factor chains receive one common raw rigid-motion stream.
    nc_c_actions = {}
    dynamic_actions = sorted({item.action_id for item in selections if item.phase_id != "STATIC_PLATEAU"})
    for action in dynamic_actions:
        if heartbeat: heartbeat(f"NC-C {action}")
        relevant = sorted({node for item in selections if item.action_id == action for node in _required_nodes(item)})
        common = _copy_imus(imus); reference = relevant[0]; start, stop = windows[action]
        reference_mask = (common[reference]["global_time_ns"] >= start) & (common[reference]["global_time_ns"] <= stop)
        reference_indices = np.flatnonzero(reference_mask)
        for node in relevant[1:]:
            node_mask = (common[node]["global_time_ns"] >= start) & (common[node]["global_time_ns"] <= stop)
            indices = np.flatnonzero(node_mask)
            if len(indices) != len(reference_indices): raise ValueError("synthetic node timelines differ")
            common[node]["gyro_raw"][indices] = common[reference]["gyro_raw"][reference_indices]
            common[node]["acc_raw"][indices] = common[reference]["acc_raw"][reference_indices]
        common_observation, _ = build_observation(common, windows, node_to_segment, contract, r3d_contract, chain_map)
        common_selections, _ = freeze_selection(common_observation, contract)
        action_rows = []
        for selection in (item for item in selections if item.action_id == action):
            common_selection = next(item for item in common_selections if item.factor_block_id == selection.factor_block_id)
            if not len(common_selection.selected_rows):
                action_rows.append({"factor_block_id": selection.factor_block_id, "status": "NO_SOURCE_SUPPORT", "pass": True}); continue
            base_rms = _constant_frame_aligned_relative_gyro_rms(observation, selection)
            common_rms = _constant_frame_aligned_relative_gyro_rms(common_observation, common_selection)
            absolute = float(contract["lineage_v2"]["common_rigid_motion_absolute_noise_floor_rad_s"])
            ratio_gate = float(contract["lineage_v2"]["common_rigid_motion_maximum_relative_information_ratio"])
            passed = common_rms <= max(absolute, ratio_gate * base_rms)
            action_rows.append({"factor_block_id": selection.factor_block_id, "statistic": "BEST_CONSTANT_SO3_FRAME_ALIGNED_RELATIVE_GYRO_RMS", "baseline_relative_gyro_rms_rad_s": base_rms, "common_rigid_relative_gyro_rms_rad_s": common_rms, "maximum_allowed_rad_s": max(absolute, ratio_gate * base_rms), "pass": passed})
        nc_c_actions[action] = {"common_mode_nodes": relevant, "factors": action_rows, "pass": all(item["pass"] for item in action_rows)}
    for record in records:
        record["NC_C"] = next(item for item in nc_c_actions[record["action"]]["factors"] if item["factor_block_id"] == record["factor_block_id"]) if record["action"] in nc_c_actions else {"status": "NOT_APPLICABLE_STATIC_POSE_FACTOR", "pass": True}
    passed = all(all(record[name]["pass"] for name in ("NC_A", "NC_B", "NC_C", "NC_D", "NC_E")) for record in records)
    arrays = {
        "factor_ids": np.asarray([item.factor_block_id for item in selections]),
        "candidate_counts": np.asarray([len(item.candidate_rows) for item in selections], np.int64),
        "selected_counts": np.asarray([len(item.selected_rows) for item in selections], np.int64),
        "nc_a_rows": np.asarray([record["NC_A"]["residual_rows"] for record in records], np.int64),
        "nc_b_residual_change": np.asarray([record["NC_B"]["target_residual_l2_change"] for record in records]),
        "nc_b_jv_change": np.asarray([record["NC_B"]["target_jv_l2_change"] for record in records]),
        "nc_d_backfill_counts": np.asarray([record["NC_D"]["backfill_count"] for record in records], np.int64),
    }
    return {"schema": "biospur-d0b-r2-observation-lineage-negative-control-v2", "row_manifest_sha256": manifest["manifest_sha256"], "factor_count": len(records), "tests_not_first_failure_aborted": True, "records": records, "nc_c_actions": nc_c_actions, "pass": passed}, arrays


def independent_five_point_jv(objective: R2Objective, x: np.ndarray, direction: np.ndarray, include_nonmeasurement: bool, step: float = 2e-5) -> np.ndarray:
    return (-objective.residual(x + 2 * step * direction, include_nonmeasurement) + 8 * objective.residual(x + step * direction, include_nonmeasurement) - 8 * objective.residual(x - step * direction, include_nonmeasurement) + objective.residual(x - 2 * step * direction, include_nonmeasurement)) / (12 * step)


def profile_product(jacobian: np.ndarray, contract: Mapping[str, Any]) -> dict[str, Any]:
    jp = jacobian[:, :PRODUCT_DIMENSION]; jn = jacobian[:, PRODUCT_DIMENSION:]
    un, sn, _ = np.linalg.svd(jn, full_matrices=False)
    threshold_n = max(float(contract["observability"]["absolute_singular_value_threshold"]), float(contract["observability"]["relative_singular_value_threshold"]) * (float(sn[0]) if len(sn) else 0.0))
    rank_n = int(np.sum(sn > threshold_n)); qn = un[:, :rank_n]
    effective = jp - qn @ (qn.T @ jp) if rank_n else jp.copy()
    _, singular, vh = np.linalg.svd(effective, full_matrices=False)
    threshold = max(float(contract["observability"]["absolute_singular_value_threshold"]), float(contract["observability"]["relative_singular_value_threshold"]) * (float(singular[0]) if len(singular) else 0.0))
    return {"effective": effective, "singular": singular, "right_vectors": vh, "rank": int(np.sum(singular > threshold)), "threshold": threshold, "nuisance_rank": rank_n, "nuisance_singular": sn}


def structural_screen(objective: R2Objective, points: list[np.ndarray], contract: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    rng = np.random.default_rng(99173); records = []; arrays = {}
    for index, point in enumerate(points):
        jacobian = production_jacobian(objective, point, False)
        direction = rng.normal(size=len(point)); direction /= np.linalg.norm(direction)
        oracle = independent_five_point_jv(objective, point, direction, False)
        product = jacobian @ direction
        absolute = float(np.max(np.abs(product - oracle))); relative = float(np.linalg.norm(product - oracle) / max(np.linalg.norm(oracle), 1e-15))
        profile = profile_product(jacobian, contract)
        records.append({"point": index, "measurement_rows": len(jacobian), "jv_max_absolute_error": absolute, "jv_relative_l2_error": relative, "nuisance_rank": profile["nuisance_rank"], "profiled_product_rank": profile["rank"], "profiled_product_dimension": PRODUCT_DIMENSION, "threshold": profile["threshold"], "bottom_singular_values": profile["singular"][-10:].tolist()})
        arrays[f"point_{index}_singular"] = profile["singular"]
        arrays[f"point_{index}_right_vectors"] = profile["right_vectors"]
    jv_abs = float(contract["jacobian_v2"]["maximum_jv_absolute_error"]); jv_rel = float(contract["jacobian_v2"]["maximum_jv_relative_error"])
    passed = all((item["jv_max_absolute_error"] <= jv_abs or item["jv_relative_l2_error"] <= jv_rel) and item["profiled_product_rank"] == PRODUCT_DIMENSION for item in records)
    return {"schema": "biospur-d0b-r2-prefit-structural-screen-v1", "points": records, "protocol_priors_in_rank": False, "declared_sparsity": "DENSE_EXPLICIT", "structural_product_nullspace": not all(item["profiled_product_rank"] == PRODUCT_DIMENSION for item in records), "pass": passed}, arrays
