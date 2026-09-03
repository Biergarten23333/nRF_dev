#!/usr/bin/env python3
"""D0B-R1 synthetic-only structural model qualification.

The phase order is intentionally strict.  No real-data path exists in this
runner, and a failed phase writes a terminal report before returning nonzero.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_engineering_v1.common_time import build_common_timeline
from biospur_fusion.imu_multi_action_engineering_v1.q2 import run_q2_frontend_v1
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_generator import ACTIONS, SEGMENTS, generate_raw_imu_case
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_model import (
    FULL_DIMENSION,
    NUISANCE_DIMENSION,
    PRODUCT_DIMENSION,
    R1Objective,
    R1Observation,
    angles_from_axis,
    blind_initialization,
    decode_product,
    fit_multistart,
    product_layout,
    production_jacobian,
    yaw,
)
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_replay import (
    NODE_NAMES,
    calibration_payload,
    load_calibration,
    replay,
    write_calibration,
)
from biospur_fusion.imu_multi_action_revision_d.r3d_activity import analyze_broad_actions


BASELINE = "412233adcb0a5a8551f2a5d1085c79b8c2c26ae5"
MEASUREMENT_CLASSES = {"MEASURED_OBSERVATION", "PROTOCOL_CONDITIONED_MEASUREMENT"}
SEALED = ["real_calibration", "final_still", "golf", "boxing", "walk", "UWB/T4", "Anchor", "operator_measurements"]


def canonical(value: Any) -> bytes:
    def convert(item: Any) -> Any:
        if isinstance(item, np.ndarray): return item.tolist()
        if isinstance(item, (np.integer,)): return int(item)
        if isinstance(item, (np.floating,)): return float(item)
        if isinstance(item, dict): return {str(key): convert(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)): return [convert(val) for val in item]
        return item
    return (json.dumps(convert(value), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def dump(path: Path, value: Any) -> None:
    path.write_bytes(canonical(value))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def array_hash(value: np.ndarray) -> str:
    value = np.ascontiguousarray(value)
    h = hashlib.sha256(value.dtype.str.encode() + np.asarray(value.shape, dtype="<i8").tobytes() + value.tobytes())
    return h.hexdigest()


def git_head(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()


def row_slices(objective: R1Objective, x: np.ndarray, include_nonmeasurement: bool) -> list[tuple[Any, slice]]:
    result = []; cursor = 0
    for block in objective.blocks(x, include_nonmeasurement):
        result.append((block, slice(cursor, cursor + len(block.values))))
        cursor += len(block.values)
    return result


def build_case(contract: Mapping[str, Any], r3d_contract: Mapping[str, Any], chain_map: Mapping[str, Any], seed: int):
    imus, windows, node_to_segment, truth = generate_raw_imu_case(contract, seed)
    q2, q2_audit, _, _ = run_q2_frontend_v1(imus, windows, contract["q2"])
    if q2_audit["verdict"] != "PASS_Q2_HUMAN_QUASI_STATIC_V1":
        raise RuntimeError(f"production Q2 failed: {q2_audit['verdict']}")
    timeline = build_common_timeline(q2, min(value[0] for value in windows.values()), max(value[1] for value in windows.values()), contract["common_time"])
    r3d = analyze_broad_actions(timeline, windows, chain_map, node_to_segment, r3d_contract)
    action_status = {name: item["status"] for name, item in r3d["actions"].items()}
    if any(value != "PASS" for value in action_status.values()):
        raise RuntimeError(f"R3D synthetic action input failed: {action_status}")
    observation = R1Observation(timeline.time_ns, timeline.node_order, timeline.rotation, timeline.gyro_rad_s, timeline.valid, windows, node_to_segment, r3d["actions"])
    return imus, windows, truth, timeline, r3d, q2_audit, observation


def block_key(block: Any) -> tuple[str, str]:
    return block.action, block.factor


def lineage_negative_controls(objective: R1Objective, x: np.ndarray) -> dict[str, Any]:
    baseline_blocks = {block_key(block): block for block in objective.blocks(x, True) if block.classification in MEASUREMENT_CLASSES}
    records = []
    for key, block in baseline_blocks.items():
        row = int(block.rows[0]); node = block.node_pair[-1]
        node_index = objective.obs.node_order.index(node)
        rotation = objective.obs.rotation.copy(); gyro = objective.obs.gyro_rad_s.copy(); valid = objective.obs.valid.copy()
        if block.factor.startswith("articulated_static_direction"):
            rotation[row, node_index] = Rotation.from_rotvec([0.031, -0.017, 0.023]).as_matrix() @ rotation[row, node_index]
        else:
            gyro[row, node_index] += np.array([0.19, -0.11, 0.07])
        changed_obs = R1Observation(objective.obs.time_ns, objective.obs.node_order, rotation, gyro, valid, objective.obs.windows, objective.obs.node_to_segment, objective.obs.r3d_actions)
        changed = {block_key(item): item for item in R1Objective(changed_obs, objective.contract).blocks(x, True) if item.classification in MEASUREMENT_CLASSES}[key]
        changed_norm = float(np.linalg.norm(changed.values - block.values))

        valid_deleted = objective.obs.valid.copy()
        for deleted_node in block.node_pair:
            deleted_index = objective.obs.node_order.index(deleted_node)
            valid_deleted[block.rows, deleted_index] = False
        deleted_obs = R1Observation(objective.obs.time_ns, objective.obs.node_order, objective.obs.rotation, objective.obs.gyro_rad_s, valid_deleted, objective.obs.windows, objective.obs.node_to_segment, objective.obs.r3d_actions)
        deleted_keys = {block_key(item) for item in R1Objective(deleted_obs, objective.contract).blocks(x, True) if item.classification in MEASUREMENT_CLASSES}
        records.append({
            "action": key[0], "factor": key[1], "changed_observation_residual_l2": changed_norm,
            "changed_observation_changes_residual": changed_norm > 0.0,
            "deleted_observation_block_absent": key not in deleted_keys,
        })
    return {"rows": records, "pass": bool(records) and all(row["changed_observation_changes_residual"] and row["deleted_observation_block_absent"] for row in records)}


def replay_args(observation: R1Observation, contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "time_ns": observation.time_ns, "rotation": observation.rotation,
        "gyro_rad_s": observation.gyro_rad_s, "valid": observation.valid,
        "node_order": observation.node_order, "node_to_segment": observation.node_to_segment,
        "lengths": contract["generic_rendering_lengths_m"],
        "maximum_gap_s": float(contract["common_time"]["maximum_bracket_gap_s"]),
    }


def replay_dependency_audit(x: np.ndarray, observation: R1Observation, contract: Mapping[str, Any]) -> dict[str, Any]:
    base_payload = calibration_payload(x[:PRODUCT_DIMENSION], sha(Path(contract["_path"])))
    args = replay_args(observation, contract)
    base = replay(base_payload, **args)
    rows = []
    for entry in product_layout():
        delta = 0.02
        changed = x[:PRODUCT_DIMENSION].copy(); changed[entry["start"]] += delta
        candidate = replay(calibration_payload(changed, base_payload["contract_sha256"]), **args)
        metrics = {
            "segment_direction_max_abs": float(np.nanmax(np.abs(candidate["segment_directions"] - base["segment_directions"]))),
            "graphical_node_max_abs_m": float(np.nanmax(np.abs(candidate["graphical_nodes"] - base["graphical_nodes"]))),
            "joint_coordinate_max_abs_rad": float(np.nanmax(np.abs(candidate["joint_coordinates"] - base["joint_coordinates"]))),
            "trunk_coordinate_max_abs_rad": float(np.nanmax(np.abs(candidate["trunk_coordinates"] - base["trunk_coordinates"]))),
        }
        rows.append({"parameter": entry["name"], "block": entry["block"], **metrics, "physical_output_changed": max(metrics.values()) > 1e-10})
    broken = dict(base_payload); broken.pop("product_coordinates")
    negative_control = False
    try: replay(broken, **args)
    except KeyError: negative_control = True
    return {"interventions": rows, "delete_actual_read_negative_control": negative_control, "pass": negative_control and all(row["physical_output_changed"] for row in rows)}


def truth_firewall(objective: R1Objective, x0: np.ndarray, truth: Mapping[str, Any]) -> dict[str, Any]:
    baseline = {
        "observation": objective.obs.signature(),
        "initializer": array_hash(x0),
        "residual": array_hash(objective.residual(x0, True)),
        "jacobian": array_hash(production_jacobian(objective, x0, True)),
        "fit_input": hashlib.sha256(objective.obs.signature().encode() + x0.tobytes()).hexdigest(),
    }
    variants = {}
    for name in ("delete", "randomize", "permute"):
        if name == "delete": mutated = {}
        elif name == "randomize":
            rng = np.random.default_rng(991); mutated = {key: rng.normal(size=np.asarray(value).shape) if isinstance(value, np.ndarray) else value for key, value in truth.items()}
        else:
            mutated = {key: np.asarray(value)[::-1].copy() if isinstance(value, np.ndarray) and np.asarray(value).ndim else value for key, value in truth.items()}
        # Deliberately no estimator API accepts ``mutated``. Re-evaluate every
        # estimator stage from the unchanged observation object.
        candidate_x = blind_initialization(objective.obs, objective.contract)
        candidate = {
            "observation": objective.obs.signature(), "initializer": array_hash(candidate_x),
            "residual": array_hash(objective.residual(candidate_x, True)),
            "jacobian": array_hash(production_jacobian(objective, candidate_x, True)),
            "fit_input": hashlib.sha256(objective.obs.signature().encode() + candidate_x.tobytes()).hexdigest(),
            "mutated_truth_field_count": len(mutated),
        }
        variants[name] = {"hashes": candidate, "byte_identical": all(candidate[key] == baseline[key] for key in baseline)}
    return {"truth_used_only_for_post_fit_evaluation": True, "truth_passed_to_estimator": False, "baseline": baseline, "variants": variants, "pass": all(item["byte_identical"] for item in variants.values())}


def truth_on_timeline(truth: Mapping[str, Any], target_ns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    source = np.asarray(truth["time_ns"], np.int64)
    hi = np.clip(np.searchsorted(source, target_ns), 0, len(source) - 1)
    lo = np.clip(hi - 1, 0, len(source) - 1)
    use_hi = np.abs(source[hi] - target_ns) < np.abs(source[lo] - target_ns)
    index = np.where(use_hi, hi, lo)
    return np.asarray(truth["segment_direction"])[index], np.asarray(truth["graphical_nodes"])[index]


def replay_errors(result: Mapping[str, np.ndarray], truth: Mapping[str, Any]) -> dict[str, float]:
    true_direction, true_nodes = truth_on_timeline(truth, result["time_ns"])
    valid = np.asarray(result["segment_valid"], bool)
    cosine = np.clip(np.sum(result["segment_directions"] * true_direction, axis=2), -1.0, 1.0)
    angle = np.arccos(cosine)[valid]
    node_valid = np.asarray(result["node_valid"], bool)
    displacement = np.linalg.norm(result["graphical_nodes"] - true_nodes, axis=2)[node_valid]
    return {"segment_direction_rmse_deg": float(np.degrees(np.sqrt(np.mean(np.square(angle))))), "graphical_node_rmse_m": float(np.sqrt(np.mean(np.square(displacement))))}


def fresh_process_replay(output: Path, product: np.ndarray, observation: R1Observation, contract: Mapping[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    artifact = output / "FROZEN_SYNTHETIC_CALIBRATION.json"
    contract_sha = sha(Path(contract["_path"]))
    calibration_sha = write_calibration(artifact, product, contract_sha)
    obs_path = output / "LABEL_BLIND_REPLAY_INPUT.npz"
    np.savez_compressed(obs_path, time_ns=observation.time_ns, rotation=observation.rotation, gyro_rad_s=observation.gyro_rad_s, valid=observation.valid.astype(np.uint8), node_order=np.asarray(observation.node_order), segments=np.asarray([observation.node_to_segment[node] for node in observation.node_order]))
    lengths_path = output / "GENERIC_LENGTHS.json"; dump(lengths_path, contract["generic_rendering_lengths_m"])
    replay_path = output / "FRESH_PROCESS_REPLAY.npz"
    env = dict(os.environ); source_root = str(Path(__file__).resolve().parents[1] / "src"); env["PYTHONPATH"] = source_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    command = [sys.executable, "-m", "biospur_fusion.imu_multi_action_revision_d.d0b_r1_replay", "--calibration", str(artifact), "--observations", str(obs_path), "--output", str(replay_path), "--lengths", str(lengths_path), "--maximum-gap-s", str(contract["common_time"]["maximum_bracket_gap_s"])]
    completed = subprocess.run(command, check=True, env=env, text=True, capture_output=True)
    with np.load(replay_path, allow_pickle=False) as data:
        arrays = {key: data[key].copy() for key in data.files if key != "calibration_sha256"}
        worker_sha = str(data["calibration_sha256"].item())
    _, loaded_sha = load_calibration(artifact)
    return arrays, {"command": command, "returncode": completed.returncode, "calibration_sha256": calibration_sha, "worker_calibration_sha256": worker_sha, "reload_sha256": loaded_sha, "sha_verified": calibration_sha == worker_sha == loaded_sha}


def weighted_measurement_jacobian(objective: R1Objective, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    residual = objective.residual(x, False)
    jacobian = production_jacobian(objective, x, False)
    scale = float(objective.contract["solver"]["f_scale"])
    weights = np.power(1.0 + np.square(residual / scale), -0.25)
    metadata = []; cursor = 0
    for block in objective.blocks(x, False):
        metadata.append({"action": block.action, "factor": block.factor, "start": cursor, "stop": cursor + len(block.values)})
        cursor += len(block.values)
    return residual * weights, jacobian * weights[:, None], metadata


def profile_product(jacobian: np.ndarray, contract: Mapping[str, Any]) -> dict[str, Any]:
    jp = jacobian[:, :PRODUCT_DIMENSION]; jn = jacobian[:, PRODUCT_DIMENSION:]
    un, sn, _ = np.linalg.svd(jn, full_matrices=False)
    nuisance_threshold = max(float(contract["observability"]["absolute_singular_value_threshold"]), float(contract["observability"]["relative_singular_value_threshold"]) * (float(sn[0]) if len(sn) else 0.0))
    nuisance_rank = int(np.sum(sn > nuisance_threshold))
    qn = un[:, :nuisance_rank]
    effective = jp - qn @ (qn.T @ jp) if nuisance_rank else jp.copy()
    _, singular, vh = np.linalg.svd(effective, full_matrices=False)
    threshold = max(float(contract["observability"]["absolute_singular_value_threshold"]), float(contract["observability"]["relative_singular_value_threshold"]) * (float(singular[0]) if len(singular) else 0.0))
    rank = int(np.sum(singular > threshold))
    return {"effective": effective, "singular": singular, "right_vectors": vh, "rank": rank, "threshold": threshold, "nuisance_rank": nuisance_rank, "nuisance_singular": sn}


def action_information(jacobian: np.ndarray, metadata: list[dict[str, Any]], contract: Mapping[str, Any]) -> dict[str, Any]:
    full = profile_product(jacobian, contract); h_full = full["effective"].T @ full["effective"]
    rows = []
    for action in ACTIONS:
        keep = np.ones(len(jacobian), bool)
        for item in metadata:
            if item["action"] == action: keep[item["start"]:item["stop"]] = False
        loo = profile_product(jacobian[keep], contract)
        h_loo = loo["effective"].T @ loo["effective"]
        value = float(np.linalg.norm(h_full - h_loo, ord="fro"))
        rows.append({"action": action, "global_leave_one_action_out_profiled_information_frobenius": value, "nonzero_meaningful": value > float(contract["recovery_gates"]["minimum_action_profiled_information_norm"])})
    return {"rows": rows, "all_actions_nonzero": all(item["nonzero_meaningful"] for item in rows), "t_pose_incremental_profiled_information_frobenius": next(item["global_leave_one_action_out_profiled_information_frobenius"] for item in rows if item["action"] == "t_pose")}


def trunk_tradeoff_scan(objective: R1Objective, endpoint: np.ndarray, replay_base: Mapping[str, np.ndarray], contract: Mapping[str, Any]) -> dict[str, Any]:
    product = decode_product(endpoint[:PRODUCT_DIMENSION]); rows = []
    torso_heading_index = 20  # pelvis omitted; torso is first retained heading.
    for alpha in contract["observability"]["finite_null_scan_rad"]:
        candidate = endpoint.copy(); candidate[torso_heading_index] += float(alpha)
        rotated = yaw(float(alpha)) @ product["trunk_normal"]
        candidate[45:47] = angles_from_axis(rotated)
        residual_delta = objective.residual(candidate, False) - objective.residual(endpoint, False)
        output = replay(calibration_payload(candidate[:PRODUCT_DIMENSION], "0" * 64), **replay_args(objective.obs, contract))
        rows.append({"alpha_rad": alpha, "measurement_residual_l2": float(np.linalg.norm(residual_delta)), "segment_max_abs": float(np.nanmax(np.abs(output["segment_directions"] - replay_base["segment_directions"]))), "node_max_abs_m": float(np.nanmax(np.abs(output["graphical_nodes"] - replay_base["graphical_nodes"]))), "joint_max_abs_rad": float(np.nanmax(np.abs(output["joint_coordinates"] - replay_base["joint_coordinates"]))), "trunk_max_abs_rad": float(np.nanmax(np.abs(output["trunk_coordinates"] - replay_base["trunk_coordinates"])))})
    return {"transform": "effective_heading:torso += alpha; trunk_normal <- Rz(alpha)*trunk_normal", "rows": rows, "legal_gauge": all(item["measurement_residual_l2"] <= 1e-8 and max(item["segment_max_abs"], item["node_max_abs_m"], item["joint_max_abs_rad"], item["trunk_max_abs_rad"]) <= 1e-8 for item in rows)}


def manifest(output: Path) -> None:
    files = []
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "SHA256_MANIFEST.json": files.append({"path": path.name, "bytes": path.stat().st_size, "sha256": sha(path)})
    dump(output / "SHA256_MANIFEST.json", {"schema": "biospur-d0b-r1-sha256-manifest-v1", "files": files})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--r3d-contract", type=Path, required=True)
    parser.add_argument("--chain-map", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=False)
    repo = Path(__file__).resolve().parents[2]
    head = git_head(repo)
    if head != BASELINE: raise RuntimeError(f"baseline changed: {head}")
    contract = json.loads(args.contract.read_text()); contract["_path"] = str(args.contract.resolve())
    r3d_contract = json.loads(args.r3d_contract.read_text()); chain_map = json.loads(args.chain_map.read_text())
    state: dict[str, Any] = {"schema": "biospur-d0b-r1-result-v1", "baseline_commit": head, "terminal_outcome": None, "real_d0_ready_for_separate_authorization": False}
    dump(args.output / "DATA_ACCESS_AUDIT.json", {"opened": ["D0B_R1_SYNTHETIC_GENERATOR", "PRODUCTION_Q2_SOURCE", "R3D_CONTRACT", "R3C_CHAIN_MAP"], "sealed_inputs_opened": [], "sealed": SEALED, "real_calibration_payload_opened": False})
    try:
        seed = int(contract["synthetic"]["seeds"][0])
        _, _, truth, timeline, r3d, q2_audit, observation = build_case(contract, r3d_contract, chain_map, seed)
        objective = R1Objective(observation, contract); x0 = blind_initialization(observation, contract)
        blocks = objective.blocks(x0, True)
        classifications = {name: 0 for name in contract["residual_classifications"]}
        for block in blocks: classifications[block.classification] += len(block.values)
        classifications["INVALID_PSEUDO_DATA"] = 0
        lineage = objective.observation_lineage(x0)
        dump(args.output / "RESIDUAL_EVIDENCE_CLASSIFICATION.json", {"row_counts": classifications, "joint_zero_rows": 0, "joint_zero_disposition": contract["r1_product"]["joint_zero"]["disposition"], "lineage": lineage})
        negative = lineage_negative_controls(objective, x0); dump(args.output / "OBSERVATION_LINEAGE_NEGATIVE_CONTROLS.json", negative)
        if not negative["pass"]: raise PhaseFailure("FAIL_INVALID_PSEUDO_DATA_REMAINS", "measurement observation negative control failed")
        required_actions = {block.action for block in blocks if block.classification in MEASUREMENT_CLASSES}
        if required_actions != set(ACTIONS): raise PhaseFailure("FAIL_ELEVEN_ACTION_SHARED_OBJECTIVE", f"actions in objective: {sorted(required_actions)}")
        static_coordinates = contract["static_articulated_nuisance"]
        if static_coordinates["free_per_segment_latent"] or static_coordinates["total_coordinates"] != NUISANCE_DIMENSION:
            raise PhaseFailure("FAIL_STATIC_LATENT_ABSORBS_MOUNTING", "shared static state contract invalid")

        dependency = replay_dependency_audit(x0, observation, contract); dump(args.output / "ACTUAL_REPLAY_DEPENDENCY_AUDIT.json", dependency)
        if not dependency["pass"]: raise PhaseFailure("FAIL_ACTUAL_REPLAY_DEPENDENCY", "publishable parameter did not change a physical replay output")
        firewall = truth_firewall(objective, x0, truth); dump(args.output / "TRUTH_FIREWALL_AUDIT.json", firewall)
        if not firewall["pass"]: raise PhaseFailure("FAIL_TRUTH_FIREWALL", "truth mutation changed estimator stage")

        fit_started = time.monotonic(); fits = fit_multistart(objective, x0, contract, seed); wall = time.monotonic() - fit_started
        fit_json = [{**item, "x": item["x"].tolist()} for item in fits]
        dump(args.output / "BLIND_MULTISTART_FIT.json", {"seed": seed, "wall_time_s": wall, "starts": fit_json})
        if not all(item["success"] and item["finite"] for item in fits): raise PhaseFailure("FAIL_SYNTHETIC_SOLVER_NOT_CONVERGED", "at least one blind deterministic start did not converge finitely")
        best = min(fits, key=lambda item: item["cost"])
        fresh, fresh_audit = fresh_process_replay(args.output, best["x"][:PRODUCT_DIMENSION], observation, contract)
        dump(args.output / "FRESH_PROCESS_REPLAY_AUDIT.json", fresh_audit)
        if not fresh_audit["sha_verified"]: raise PhaseFailure("FAIL_ACTUAL_REPLAY_DEPENDENCY", "fresh-process canonical SHA verification failed")
        errors = replay_errors(fresh, truth)
        gates = contract["recovery_gates"]
        per_start = []
        for item in fits:
            output = replay(calibration_payload(item["x"][:PRODUCT_DIMENSION], "0" * 64), **replay_args(observation, contract))
            per_start.append((item["start"], output, replay_errors(output, truth)))
        direction_disagreement = 0.0; node_disagreement = 0.0
        for _, candidate, _ in per_start:
            valid = fresh["segment_valid"] & candidate["segment_valid"]
            cosine = np.clip(np.sum(fresh["segment_directions"] * candidate["segment_directions"], axis=2), -1.0, 1.0)
            direction_disagreement = max(direction_disagreement, float(np.degrees(np.max(np.arccos(cosine[valid])))))
            node_valid = fresh["node_valid"] & candidate["node_valid"]
            node_disagreement = max(node_disagreement, float(np.max(np.linalg.norm(fresh["graphical_nodes"] - candidate["graphical_nodes"], axis=2)[node_valid])))
        recovery = {"best": errors, "per_start": [{"start": index, **value} for index, _, value in per_start], "maximum_multistart_segment_direction_disagreement_deg": direction_disagreement, "maximum_multistart_node_disagreement_m": node_disagreement}
        recovery["pass"] = errors["segment_direction_rmse_deg"] <= gates["maximum_segment_direction_rmse_deg"] and errors["graphical_node_rmse_m"] <= gates["maximum_graphical_node_rmse_m"] and direction_disagreement <= gates["maximum_multistart_segment_direction_disagreement_deg"] and node_disagreement <= gates["maximum_multistart_node_disagreement_m"]
        dump(args.output / "SYNTHETIC_PHYSICAL_RECOVERY.json", recovery)
        if not recovery["pass"]: raise PhaseFailure("FAIL_SYNTHETIC_RECOVERY", "physical centerline recovery or multistart output agreement failed")

        _, measurement_jacobian, metadata = weighted_measurement_jacobian(objective, best["x"])
        profile = profile_product(measurement_jacobian, contract)
        np.savez_compressed(args.output / "MEASUREMENT_ONLY_JACOBIAN_AND_SPECTRUM.npz", jacobian=measurement_jacobian, effective_product_jacobian=profile["effective"], singular_values=profile["singular"], nuisance_singular_values=profile["nuisance_singular"], product_right_vectors=profile["right_vectors"])
        action_info = action_information(measurement_jacobian, metadata, contract); dump(args.output / "ACTION_PROFILED_INFORMATION.json", action_info)
        if not action_info["all_actions_nonzero"]: raise PhaseFailure("FAIL_ELEVEN_ACTION_SHARED_OBJECTIVE", "an action has no meaningful nuisance-profiled information")
        if action_info["t_pose_incremental_profiled_information_frobenius"] <= gates["minimum_action_profiled_information_norm"]: raise PhaseFailure("FAIL_TPOSE_MEASUREMENT_DEPENDENCY", "T-pose has no global incremental profiled information")
        trunk_scan = trunk_tradeoff_scan(objective, best["x"], fresh, contract); dump(args.output / "TRUNK_FINITE_TRANSFORM_SCAN.json", trunk_scan)
        observability = {"product_dimension_after_declared_gauge_quotient": PRODUCT_DIMENSION, "measurement_only_profiled_rank": profile["rank"], "threshold": profile["threshold"], "singular_values": profile["singular"].tolist(), "nuisance_rank": profile["nuisance_rank"]}
        dump(args.output / "MEASUREMENT_ONLY_OBSERVABILITY.json", observability)
        if profile["rank"] != PRODUCT_DIMENSION: raise PhaseFailure("FAIL_MEASUREMENT_ONLY_PROFILED_OBSERVABILITY", f"rank {profile['rank']}/{PRODUCT_DIMENSION}")

        # A second full generator/Q2/fit configuration is required, not merely
        # a repeated Jacobian at the first truth point.
        second_seed = int(contract["synthetic"]["seeds"][1])
        _, _, truth2, _, _, _, observation2 = build_case(contract, r3d_contract, chain_map, second_seed)
        objective2 = R1Objective(observation2, contract); x02 = blind_initialization(observation2, contract)
        fits2 = fit_multistart(objective2, x02, contract, second_seed)
        dump(args.output / "SECOND_CONFIGURATION_MULTISTART.json", {"seed": second_seed, "starts": [{**item, "x": item["x"].tolist()} for item in fits2]})
        if not all(item["success"] and item["finite"] for item in fits2): raise PhaseFailure("FAIL_SYNTHETIC_SOLVER_NOT_CONVERGED", "second independent configuration did not converge")
        best2 = min(fits2, key=lambda item: item["cost"])
        replay2 = replay(calibration_payload(best2["x"][:PRODUCT_DIMENSION], "0" * 64), **replay_args(observation2, contract)); errors2 = replay_errors(replay2, truth2)
        if errors2["segment_direction_rmse_deg"] > gates["maximum_segment_direction_rmse_deg"] or errors2["graphical_node_rmse_m"] > gates["maximum_graphical_node_rmse_m"]: raise PhaseFailure("FAIL_SYNTHETIC_RECOVERY", "second independent configuration recovery failed")
        state.update({"terminal_outcome": "PASS_D0B_R1_SYNTHETIC_MODEL_QUALIFICATION", "real_d0_ready_for_separate_authorization": True, "first_configuration_recovery": recovery, "second_configuration_recovery": errors2, "measurement_only_profiled_rank": profile["rank"], "product_dimension": PRODUCT_DIMENSION})
    except PhaseFailure as failure:
        state.update({"terminal_outcome": failure.code, "failure_detail": failure.detail})
    except Exception as failure:
        state.update({"terminal_outcome": "FAIL_SYNTHETIC_MODEL_PIPELINE_EXCEPTION", "failure_detail": f"{type(failure).__name__}: {failure}"})
    state.update({"real_calibration_data": "NOT_OPENED", "real_d0_objective_jacobian_solver": "NOT_RUN", "freeze": "NOT_CREATED", "real_replay_render": "NOT_RUN", "sealed": SEALED, "commit_push": "NOT_PERFORMED"})
    dump(args.output / "RESULT.json", state)
    report = f"# D0B-R1 synthetic structural qualification\n\n`{state['terminal_outcome']}`\n\nThe immutable 412233ad checkpoint was not modified. This runner opened synthetic raw IMU only, passed it through production Q2 and R3D broad activity, and did not open real or held-out data.\n"
    (args.output / "REPORT.md").write_text(report, encoding="utf-8")
    manifest(args.output)
    print(state["terminal_outcome"])
    return 0 if state["terminal_outcome"] == "PASS_D0B_R1_SYNTHETIC_MODEL_QUALIFICATION" else 2


class PhaseFailure(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail); self.code = code; self.detail = detail


if __name__ == "__main__":
    raise SystemExit(main())
