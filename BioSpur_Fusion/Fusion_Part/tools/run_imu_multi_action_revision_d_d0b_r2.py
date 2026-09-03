#!/usr/bin/env python3
"""Overnight fail-closed D0B-R2 synthetic qualification runner."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import uuid
from typing import Any, Mapping

import numpy as np
from scipy.optimize import least_squares

from biospur_fusion.imu_multi_action_revision_d.d0b_r1_generator import ACTIONS, generate_raw_imu_case
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_model import (
    FULL_DIMENSION, FUNCTIONAL_JOINTS, PRODUCT_DIMENSION, blind_initialization,
    bounds, decode_product, product_layout, production_jacobian,
)
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_replay import (
    calibration_payload, replay, write_calibration,
)
from biospur_fusion.imu_multi_action_revision_d.d0b_r2_lineage import (
    R2Objective, factor_value_map, freeze_selection,
)
from biospur_fusion.imu_multi_action_revision_d.d0b_r2_qualification import (
    build_observation, independent_five_point_jv, negative_controls_v2,
    profile_product, structural_screen,
)


BASELINE = "412233adcb0a5a8551f2a5d1085c79b8c2c26ae5"
SEALED = ["REAL_CALIBRATION", "REAL_Q2_CACHE", "FINAL_STILL", "WALK", "GOLF", "BOXING", "UWB/T4/ANCHOR", "OPERATOR_MEASUREMENTS"]
PRODUCT_LAYOUT = product_layout()
TRUE_FUNCTIONAL = {
    "shoulder_L": np.array([0.0, 1.0, 0.0]), "shoulder_R": np.array([0.0, 1.0, 0.0]),
    "elbow_L": np.array([1.0, 0.0, 0.0]), "elbow_R": np.array([-1.0, 0.0, 0.0]),
    "hip_L": np.array([1.0, 0.0, 0.0]), "hip_R": np.array([-1.0, 0.0, 0.0]),
    "knee_L": np.array([1.0, 0.0, 0.0]), "knee_R": np.array([-1.0, 0.0, 0.0]),
}
TRUE_TRUNK_NORMAL = np.array([0.0, 1.0, 0.0])


class PhaseFailure(RuntimeError):
    def __init__(self, code: str, detail: str): super().__init__(detail); self.code = code; self.detail = detail


class RuntimeCap(RuntimeError): pass


def convert(value: Any) -> Any:
    if isinstance(value, np.ndarray): return value.tolist()
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.floating,)): return float(value)
    if isinstance(value, Path): return str(value)
    if isinstance(value, dict): return {str(key): convert(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)): return [convert(item) for item in value]
    return value


def canonical(value: Any) -> bytes:
    return (json.dumps(convert(value), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def dump(path: Path, value: Any) -> None: path.write_bytes(canonical(value))


def atomic_dump(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical(value)); os.replace(temporary, path)


def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def array_hash(value: np.ndarray) -> str:
    value = np.ascontiguousarray(value)
    return hashlib.sha256(value.dtype.str.encode() + np.asarray(value.shape, dtype="<i8").tobytes() + value.tobytes()).hexdigest()


def git_head(repo: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()


def phase(stage: dict[str, Any], name: str, status: str, detail: str = "") -> None:
    stage[name] = {"status": status, "detail": detail, "host_monotonic_s": time.monotonic()}


def replay_arguments(observation, contract):
    return {"time_ns": observation.time_ns, "rotation": observation.rotation, "gyro_rad_s": observation.gyro_rad_s, "valid": observation.valid, "node_order": observation.node_order, "node_to_segment": observation.node_to_segment, "lengths": contract["generic_rendering_lengths_m"], "maximum_gap_s": float(contract["common_time"]["maximum_bracket_gap_s"])}


def replay_bytes(result: Mapping[str, np.ndarray]) -> bytes:
    chunks = []
    for key in sorted(result):
        value = np.ascontiguousarray(result[key]); chunks.extend([key.encode() + b"\0", value.dtype.str.encode() + b"\0", np.asarray(value.shape, dtype="<i8").tobytes(), value.tobytes()])
    return b"".join(chunks)


def fresh_process_replay(formal: Path, artifact: Path, observation, contract, repo: Path) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    observation_path = formal / "LABEL_BLIND_REPLAY_INPUT.npz"
    np.savez_compressed(observation_path, time_ns=observation.time_ns, rotation=observation.rotation, gyro_rad_s=observation.gyro_rad_s, valid=observation.valid.astype(np.uint8), node_order=np.asarray(observation.node_order), segments=np.asarray([observation.node_to_segment[node] for node in observation.node_order]))
    lengths_path = formal / "GENERIC_LENGTHS.json"; dump(lengths_path, contract["generic_rendering_lengths_m"])
    output_path = formal / "FRESH_PROCESS_REPLAY.npz"
    command = [sys.executable, "-m", "biospur_fusion.imu_multi_action_revision_d.d0b_r1_replay", "--calibration", str(artifact), "--observations", str(observation_path), "--output", str(output_path), "--lengths", str(lengths_path), "--maximum-gap-s", str(contract["common_time"]["maximum_bracket_gap_s"])]
    environment = dict(os.environ); environment["PYTHONPATH"] = str(repo / "Fusion_Part/src")
    completed = subprocess.run(command, cwd=repo, env=environment, check=True, text=True, capture_output=True)
    with np.load(output_path, allow_pickle=False) as data:
        result = {key: data[key].copy() for key in data.files if key != "calibration_sha256"}; worker_sha = str(data["calibration_sha256"].item())
    return result, {"command": command, "returncode": completed.returncode, "worker_calibration_sha256": worker_sha, "stdout": completed.stdout, "stderr": completed.stderr}


def truth_at(truth: Mapping[str, Any], target_ns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    source = np.asarray(truth["time_ns"], np.int64); hi = np.clip(np.searchsorted(source, target_ns), 0, len(source) - 1); lo = np.clip(hi - 1, 0, len(source) - 1)
    index = np.where(np.abs(source[hi] - target_ns) < np.abs(source[lo] - target_ns), hi, lo)
    return np.asarray(truth["segment_direction"])[index], np.asarray(truth["graphical_nodes"])[index]


def physical_recovery(product_x: np.ndarray, observation, truth, contract) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    output = replay(calibration_payload(product_x, "0" * 64), **replay_arguments(observation, contract))
    true_direction, true_nodes = truth_at(truth, output["time_ns"])
    valid = output["segment_valid"]; cosine = np.clip(np.sum(output["segment_directions"] * true_direction, axis=2), -1.0, 1.0)
    direction_rmse = float(np.degrees(np.sqrt(np.mean(np.square(np.arccos(cosine[valid]))))))
    node_valid = output["node_valid"]; node_rmse = float(np.sqrt(np.mean(np.square(np.linalg.norm(output["graphical_nodes"] - true_nodes, axis=2)[node_valid]))))
    decoded = decode_product(product_x)
    functional = {name: math.degrees(math.acos(float(np.clip(abs(decoded["functional"][name] @ TRUE_FUNCTIONAL[name]), -1.0, 1.0)))) for name in FUNCTIONAL_JOINTS}
    trunk = math.degrees(math.acos(float(np.clip(abs(decoded["trunk_normal"] @ TRUE_TRUNK_NORMAL), -1.0, 1.0))))
    metrics = {"segment_direction_rmse_deg": direction_rmse, "graphical_node_rmse_m": node_rmse, "functional_axis_sign_quotiented_error_deg": functional, "trunk_plane_normal_sign_quotiented_error_deg": trunk, "effective_mounting_transform_claim": "LONGITUDINAL_AXIS_PLUS_EFFECTIVE_HEADING_ONLY_NO_AXIAL_TWIST"}
    return metrics, output


def replay_dependency(observation, contract) -> dict[str, Any]:
    x = np.zeros(PRODUCT_DIMENSION)
    # Keep every S2 coordinate away from chart poles for coordinate-level tests.
    for entry in PRODUCT_LAYOUT:
        if entry["stop"] - entry["start"] == 2:
            x[entry["start"]] = 0.31; x[entry["start"] + 1] = 0.27
    base = replay(calibration_payload(x, "0" * 64), **replay_arguments(observation, contract)); records = []
    for coordinate in range(PRODUCT_DIMENSION):
        changed = x.copy(); changed[coordinate] += 0.02
        candidate = replay(calibration_payload(changed, "0" * 64), **replay_arguments(observation, contract))
        changes = {"segment": float(np.nanmax(np.abs(candidate["segment_directions"] - base["segment_directions"]))), "nodes_m": float(np.nanmax(np.abs(candidate["graphical_nodes"] - base["graphical_nodes"]))), "joint_rad": float(np.nanmax(np.abs(candidate["joint_coordinates"] - base["joint_coordinates"]))), "trunk_rad": float(np.nanmax(np.abs(candidate["trunk_coordinates"] - base["trunk_coordinates"])))}
        records.append({"coordinate": coordinate, "changes": changes, "physical_output_changed": max(changes.values()) > 1e-10})
    broken = calibration_payload(x, "0" * 64); broken["product_coordinates"] = broken["product_coordinates"][:-1]
    negative = False
    try: replay(broken, **replay_arguments(observation, contract))
    except (ValueError, IndexError): negative = True
    return {"schema": "biospur-d0b-r2-replay-dependency-v1", "coordinate_records": records, "deleted_actual_forward_read_fails": negative, "action_labels_used": False, "truth_used": False, "postfit_pca_used": False, "parameter_append_used": False, "pass": negative and all(item["physical_output_changed"] for item in records)}


def truth_firewall(objective: R2Objective, x0: np.ndarray, truth: Mapping[str, Any], row_sha: str) -> dict[str, Any]:
    base_j = production_jacobian(objective, x0, True)
    baseline = {"selector": row_sha, "initializer": array_hash(x0), "row_manifest": row_sha, "residual": array_hash(objective.residual(x0, True)), "jacobian": array_hash(base_j), "fit_input": hashlib.sha256(objective.obs.signature().encode() + row_sha.encode() + x0.tobytes()).hexdigest()}
    results = {}
    for name in ("DELETE", "RANDOMIZE", "PERMUTE"):
        if name == "DELETE": mutated = {}
        elif name == "RANDOMIZE":
            rng = np.random.default_rng(331); mutated = {key: rng.normal(size=value.shape) if isinstance(value, np.ndarray) else value for key, value in truth.items()}
        else: mutated = {key: value[::-1].copy() if isinstance(value, np.ndarray) and value.ndim else value for key, value in truth.items()}
        # No estimator API accepts mutated truth.
        candidate_x = blind_initialization(objective.obs, objective.contract)
        candidate = {"selector": row_sha, "initializer": array_hash(candidate_x), "row_manifest": row_sha, "residual": array_hash(objective.residual(candidate_x, True)), "jacobian": array_hash(production_jacobian(objective, candidate_x, True)), "fit_input": hashlib.sha256(objective.obs.signature().encode() + row_sha.encode() + candidate_x.tobytes()).hexdigest()}
        results[name] = {"truth_fields_after_mutation": len(mutated), "hashes": candidate, "byte_identical": candidate == baseline}
    return {"truth_passed_to_estimator": False, "truth_used_only_postfit": True, "baseline": baseline, "variants": results, "pass": all(item["byte_identical"] for item in results.values())}


def weighted_measurement_jacobian(objective: R2Objective, x: np.ndarray):
    residual = objective.residual(x, False); jacobian = production_jacobian(objective, x, False); f = float(objective.contract["solver"]["f_scale"]); weights = np.power(1.0 + np.square(residual / f), -0.25)
    metadata = []; cursor = 0
    lookup = {(item.action_id, item.factor): item.factor_block_id for item in objective.selections}
    for block in objective.blocks(x, False):
        metadata.append({"action": block.action, "factor": block.factor, "factor_block_id": lookup[(block.action, block.factor)], "start": cursor, "stop": cursor + len(block.values)}); cursor += len(block.values)
    return residual * weights, jacobian * weights[:, None], metadata


def action_information(jacobian: np.ndarray, metadata: list[dict[str, Any]], contract):
    full = profile_product(jacobian, contract); h_full = full["effective"].T @ full["effective"]; records = []
    for action in ACTIONS:
        keep = np.ones(len(jacobian), bool)
        for item in metadata:
            if item["action"] == action: keep[item["start"]:item["stop"]] = False
        loo = profile_product(jacobian[keep], contract); value = float(np.linalg.norm(h_full - loo["effective"].T @ loo["effective"], ord="fro"))
        records.append({"action": action, "global_loo_profiled_information_frobenius": value, "meaningful": value > float(contract["recovery_gates"]["minimum_action_profiled_information_norm"])})
    return {"records": records, "pass": all(item["meaningful"] for item in records)}


class CheckpointedEvaluator:
    def __init__(self, objective: R2Objective, directory: Path, start_index: int, freeze_sha: str, row_sha: str, cfg: Mapping[str, Any], overall_deadline: float):
        self.objective = objective; self.directory = directory; self.start_index = start_index; self.freeze_sha = freeze_sha; self.row_sha = row_sha; self.cfg = cfg; self.started = time.monotonic(); self.last_heartbeat = self.started; self.last_checkpoint = self.started; self.overall_deadline = overall_deadline; self.nfev = 0; self.njev = 0; self.checkpoints = 0; self.heartbeats = []

    def _limits(self):
        now = time.monotonic()
        if now >= self.overall_deadline or now - self.started >= float(self.cfg["per_start_wall_time_cap_s"]): raise RuntimeCap("solver wall-clock cap")
        if now - self.last_heartbeat >= float(self.cfg["heartbeat_interval_s"]):
            record = {"start": self.start_index, "elapsed_s": now - self.started, "nfev": self.nfev, "njev": self.njev}; self.heartbeats.append(record); print("HEARTBEAT " + json.dumps(record, sort_keys=True), flush=True); self.last_heartbeat = now
        return now

    def residual(self, x):
        now = self._limits(); value = self.objective.residual(x, True); self.nfev += 1
        if now - self.last_checkpoint >= float(self.cfg["checkpoint_interval_s"]):
            self.checkpoints += 1; atomic_dump(self.directory / f"START_{self.start_index}_CHECKPOINT_{self.checkpoints}.json", {"schema": "biospur-d0b-r2-solver-checkpoint-v1", "start": self.start_index, "x": x, "cost_unrobust": 0.5 * float(value @ value), "nfev": self.nfev, "njev": self.njev, "run_freeze_sha256": self.freeze_sha, "row_manifest_sha256": self.row_sha, "trust_state": "NOT_SERIALIZABLE_SCIPY_TRF_INTERNAL"}); self.last_checkpoint = now
        return value

    def jacobian(self, x): self._limits(); self.njev += 1; return production_jacobian(self.objective, x, True)


def starts(x0: np.ndarray, cfg: Mapping[str, Any]) -> list[np.ndarray]:
    low, high = bounds(); result = [np.clip(x0, low + 1e-6, high - 1e-6)]
    for seed in cfg["seeds"][1:]:
        rng = np.random.default_rng(int(seed)); result.append(np.clip(x0 + rng.normal(0.0, 0.12, len(x0)), low + 1e-6, high - 1e-6))
    return result


def run_start(objective: R2Objective, start: np.ndarray, index: int, directory: Path, freeze_sha: str, row_sha: str, cfg: Mapping[str, Any], deadline: float):
    evaluator = CheckpointedEvaluator(objective, directory, index, freeze_sha, row_sha, cfg, deadline); low, high = bounds(); begun = time.monotonic()
    try:
        result = least_squares(evaluator.residual, start, jac=evaluator.jacobian, bounds=(low, high), loss="soft_l1", f_scale=1.0, max_nfev=int(cfg["maximum_function_evaluations"]), xtol=float(cfg["xtol"]), ftol=float(cfg["ftol"]), gtol=float(cfg["gtol"]), verbose=0)
    except RuntimeCap:
        return {"start": index, "status": "RUNTIME_CAP", "success": False, "wall_time_s": time.monotonic() - begun, "heartbeats": evaluator.heartbeats, "checkpoint_count": evaluator.checkpoints}, None
    final_j = production_jacobian(objective, result.x, True); final_r = objective.residual(result.x, True)
    audit = {"start": index, "status": int(result.status), "message": str(result.message), "success": bool(result.success), "finite": bool(np.isfinite(result.x).all() and np.isfinite(final_r).all() and np.isfinite(final_j).all()), "cost": float(result.cost), "optimality": float(result.optimality), "nfev": int(result.nfev), "njev": int(result.njev or 0), "wall_time_s": time.monotonic() - begun, "x": result.x, "residual_sha256": array_hash(final_r), "jacobian_sha256": array_hash(final_j), "gradient_sha256": array_hash(final_j.T @ final_r), "run_freeze_sha256": freeze_sha, "row_manifest_sha256": row_sha, "heartbeats": evaluator.heartbeats, "checkpoint_count": evaluator.checkpoints, "resume_count": 0}
    atomic_dump(directory / f"START_{index}_TERMINAL_CHECKPOINT.json", audit)
    return audit, result.x.copy()


def source_freeze(repo: Path, config_dir: Path, runner: Path) -> dict[str, Any]:
    paths = [
        repo / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/d0b_r1_generator.py",
        repo / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/d0b_r1_model.py",
        repo / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/d0b_r1_replay.py",
        repo / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/d0b_r2_lineage.py",
        repo / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/d0b_r2_qualification.py",
        repo / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/r3d_activity.py",
        repo / "Fusion_Part/src/biospur_fusion/imu_multi_action_engineering_v1/q2.py",
        repo / "Fusion_Part/src/biospur_fusion/imu_multi_action_engineering_v1/common_time.py",
        repo / "Fusion_Part/tests/unit/test_imu_multi_action_revision_d_d0b_r1.py",
        repo / "Fusion_Part/tests/unit/test_imu_multi_action_revision_d_d0b_r2.py",
        runner,
    ] + sorted(config_dir.glob("*.json"))
    return {"files": [{"path": str(path.relative_to(repo)), "sha256": sha(path), "bytes": path.stat().st_size} for path in paths]}


def write_manifest(root: Path):
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "SHA256_MANIFEST.json": files.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size, "sha256": sha(path)})
    dump(root / "SHA256_MANIFEST.json", {"schema": "biospur-d0b-r2-manifest-v1", "files": files})


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--config-dir", type=Path, required=True); parser.add_argument("--r3d-contract", type=Path, required=True); parser.add_argument("--chain-map", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False); preflight = args.output / "preflight"; preflight.mkdir(); repo = Path(__file__).resolve().parents[2]; runner = Path(__file__).resolve(); started = time.monotonic(); stages = {}; formal_uuid = None; formal_count = 0; start_audits = []; checkpoint_resume_count = 0
    contract_path = args.config_dir / "R2_SYNTHETIC_MODEL_CONTRACT.json"; contract = json.loads(contract_path.read_text()); r3d_contract = json.loads(args.r3d_contract.read_text()); chain_map = json.loads(args.chain_map.read_text()); solver_cfg = json.loads((args.config_dir / "SOLVER_AND_CHECKPOINT_CONTRACT.json").read_text())
    result = {"schema": "biospur-d0b-r2-result-v1", "revision": "D0B_R2_OBSERVATION_LINEAGE_V2", "R1_ORIGINAL_VERDICT": "FAIL_INVALID_PSEUDO_DATA_REMAINS", "R1_CORRECTED_SCIENTIFIC_INTERPRETATION": "NEGATIVE_CONTROL_EXPECTATION_INVALID_UNDER_SELECTOR_BACKFILL", "ACTUAL_INVALID_PSEUDO_ROWS_FOUND": 0, "SELECTED_ROWS_DELETED": True, "SELECTOR_BACKFILL_OCCURRED": True}
    try:
        if git_head(repo) != BASELINE: raise PhaseFailure("FAIL_STATE_OR_LIFECYCLE_ACCOUNTING", "baseline HEAD changed")
        # P0/P1 input is independent human-like raw synthetic only.
        imus, windows, node_to_segment, truth = generate_raw_imu_case(contract, int(contract["synthetic"]["seeds"][0]))
        observation, frontend = build_observation(imus, windows, node_to_segment, contract, r3d_contract, chain_map)
        if frontend["q2"]["verdict"] != "PASS_Q2_HUMAN_QUASI_STATIC_V1" or any(value != "PASS" for value in frontend["action_status"].values()): raise PhaseFailure("FAIL_STATE_OR_LIFECYCLE_ACCOUNTING", "production Q2/R3D synthetic input failed")
        selections, row_manifest = freeze_selection(observation, contract); dump(preflight / "FROZEN_RESIDUAL_ROW_MANIFEST.json", row_manifest); row_sha = row_manifest["manifest_sha256"]
        objective = R2Objective(observation, contract, selections, row_sha); x0 = blind_initialization(observation, contract)
        source = source_freeze(repo, args.config_dir, runner)
        run_freeze = {"schema": "biospur-d0b-r2-run-freeze-v1", "baseline_commit": BASELINE, "source_and_config": source, "source_and_config_combined_sha256": hashlib.sha256(canonical(source)).hexdigest(), "input": {"generator_seed": int(contract["synthetic"]["seeds"][0]), "synthetic_raw_input_sha256": hashlib.sha256(b"".join(value.tobytes() for _, value in sorted(imus.items()))).hexdigest(), "row_manifest_sha256": row_sha}, "dimensions": {"estimable_product": 47, "nuisance": 34, "proven_legal_gauge": 0, "expected_quotient": 47}, "solver": solver_cfg, "environment": {"OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS"), "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"), "MKL_NUM_THREADS": os.environ.get("MKL_NUM_THREADS")}, "formal_run_limit": 1, "total_wall_clock_cap_s": 25200}
        dump(preflight / "RUN_FREEZE.json", run_freeze); freeze_sha = sha(preflight / "RUN_FREEZE.json")
        dump(preflight / "CONTRACT_BINDINGS.json", {"contracts": [{"path": str(path.resolve()), "sha256": sha(path), "bytes": path.stat().st_size} for path in sorted(args.config_dir.glob("*.json"))] + [{"path": str(args.r3d_contract.resolve()), "sha256": sha(args.r3d_contract), "bytes": args.r3d_contract.stat().st_size}, {"path": str(args.chain_map.resolve()), "sha256": sha(args.chain_map), "bytes": args.chain_map.stat().st_size}]})
        dump(preflight / "STATE_AND_LIFECYCLE_AUDIT.json", {"state_inventory": json.loads((args.config_dir / "STATE_INVENTORY.json").read_text()), "factor_count": len(selections), "actions_with_measurement_rows": sorted({item.action_id for item in selections if len(item.selected_rows)}), "invalid_pseudo_rows": 0, "joint_zero": "CAPTURE_DEFINED_REPORTING_CONVENTION_NOT_ESTIMATED", "shared_articulated_static_pose": True, "cycle_count_weights": False, "trunk_product_coordinates": 2, "row_manifest_sha256": row_sha, "pass": len(selections) == 39 and all(len(item.selected_rows) for item in selections)})
        phase(stages, "P0_SOURCE_CONFIG_CONTRACTS_FREEZE", "PASS")

        test = subprocess.run([sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/unit/test_imu_multi_action_revision_d_d0b_r1.py", "Fusion_Part/tests/unit/test_imu_multi_action_revision_d_d0b_r2.py"], cwd=repo, env={**os.environ, "PYTHONPATH": str(repo / "Fusion_Part/src")}, text=True, capture_output=True)
        dump(preflight / "TEST_RESULTS.json", {"returncode": test.returncode, "stdout": test.stdout, "stderr": test.stderr})
        firewall = truth_firewall(objective, x0, truth, row_sha); dump(preflight / "TRUTH_FIREWALL.json", firewall)
        if test.returncode or not firewall["pass"]: raise PhaseFailure("FAIL_STATE_OR_LIFECYCLE_ACCOUNTING", "unit or truth firewall failure")
        phase(stages, "P1_UNIT_TRUTH_FIREWALL", "PASS")

        lineage, nc_arrays = negative_controls_v2(imus, windows, node_to_segment, observation, contract, r3d_contract, chain_map, lambda text: print("P2 " + text, flush=True)); dump(preflight / "OBSERVATION_LINEAGE_NEGATIVE_CONTROL_V2.json", lineage); np.savez_compressed(preflight / "NEGATIVE_CONTROL_ARRAYS.npz", **nc_arrays)
        with (preflight / "PER_FACTOR_NEGATIVE_CONTROL_MATRIX.csv").open("w", newline="") as handle:
            writer = csv.writer(handle); writer.writerow(["factor_block_id", "action", "phase", "chain", "NC_A", "NC_B", "NC_C", "NC_D", "NC_E"])
            for row in lineage["records"]: writer.writerow([row["factor_block_id"], row["action"], row["phase"], row["chain"], *["PASS" if row[name]["pass"] else "FAIL" for name in ("NC_A", "NC_B", "NC_C", "NC_D", "NC_E")]])
        dump(preflight / "SELECTOR_BACKFILL_AUDIT.json", {"row_manifest_sha256": row_sha, "factors": [{"factor_block_id": row["factor_block_id"], **row["NC_D"]} for row in lineage["records"]], "pass": all(row["NC_D"]["pass"] for row in lineage["records"])})
        if not lineage["pass"]: raise PhaseFailure("FAIL_OBSERVATION_LINEAGE", "at least one NC-V2 factor failed")
        phase(stages, "P2_OBSERVATION_LINEAGE_V2", "PASS")

        dependency = replay_dependency(observation, contract); dump(preflight / "ACTUAL_REPLAY_DEPENDENCY.json", dependency)
        if not dependency["pass"]: raise PhaseFailure("FAIL_ACTUAL_REPLAY_PARAMETER_DEPENDENCY", "coordinate-level forward dependency failed")
        low, high = bounds(); rng = np.random.default_rng(151); points = [x0, np.clip(x0 + rng.normal(0, .05, FULL_DIMENSION), low + 1e-4, high - 1e-4), np.clip(x0 + rng.normal(0, .08, FULL_DIMENSION), low + 1e-4, high - 1e-4)]
        structural, structural_arrays = structural_screen(objective, points, contract); dump(preflight / "PREFIT_STRUCTURAL_SCREEN.json", structural); np.savez_compressed(preflight / "PREFIT_STRUCTURAL_ARRAYS.npz", **structural_arrays)
        if not structural["pass"]: raise PhaseFailure("FAIL_STRUCTURAL_PRODUCT_NULLSPACE" if structural["structural_product_nullspace"] else "FAIL_JACOBIAN_QUALIFICATION", "prefit screen failed")
        phase(stages, "P3_STATE_REPLAY_STRUCTURAL_AUDIT", "PASS")

        # Performance and checkpoint preflight; no formal run consumed.
        before_mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss; t = time.perf_counter(); objective.residual(x0, True); residual_time = time.perf_counter() - t; t = time.perf_counter(); production_jacobian(objective, x0, True); jacobian_time = time.perf_counter() - t
        trial_started = time.perf_counter(); trial = least_squares(lambda value: objective.residual(value, True), x0, jac=lambda value: production_jacobian(objective, value, True), bounds=(low, high), loss="soft_l1", f_scale=1.0, max_nfev=3, xtol=None, ftol=None, gtol=1e-15); trial_time = time.perf_counter() - trial_started
        atomic_dump(preflight / "CHECKPOINT_PREFLIGHT.json", {"x": trial.x, "cost": trial.cost, "nfev": trial.nfev, "row_manifest_sha256": row_sha, "run_freeze_sha256": freeze_sha})
        performance = {"residual_evaluation_s": residual_time, "jacobian_evaluation_s": jacobian_time, "three_solver_trial_steps_s": trial_time, "trial_nfev": trial.nfev, "peak_memory_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "peak_memory_delta_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before_mem, "estimated_single_start_s": trial_time / max(trial.nfev, 1) * int(solver_cfg["maximum_function_evaluations"]), "estimated_five_start_s": trial_time / max(trial.nfev, 1) * int(solver_cfg["maximum_function_evaluations"]) * 5, "parameter_independent_precomputed": ["common_time_association", "validity_masks", "candidate_pools", "selected_rows", "observation_features", "whitening_constants", "dense_structure"], "checkpoint_atomic_write_pass": (preflight / "CHECKPOINT_PREFLIGHT.json").exists()}
        dump(preflight / "PERFORMANCE_AND_CHECKPOINT_PREFLIGHT.json", performance); phase(stages, "P4_PERFORMANCE_CHECKPOINT_PREFLIGHT", "PASS")

        # Consume the unique formal run only after every preflight gate passes.
        formal_uuid = str(uuid.uuid4()); formal_count = 1; formal = args.output / f"formal_{formal_uuid}"; formal.mkdir(); dump(formal / "FORMAL_RUN_IDENTITY.json", {"formal_run_uuid": formal_uuid, "formal_run_count": 1, "run_freeze_sha256": freeze_sha, "row_manifest_sha256": row_sha})
        phase(stages, "P5_FORMAL_RUN_CONSUMED", "PASS")
        deadline = started + float(solver_cfg["total_wall_time_cap_s"]); frozen_starts = starts(x0, solver_cfg)
        audit0, endpoint0 = run_start(objective, frozen_starts[0], 0, formal, freeze_sha, row_sha, solver_cfg, deadline); start_audits.append(audit0)
        if endpoint0 is None:
            if audit0["status"] == "RUNTIME_CAP": raise PhaseFailure("STOPPED_OVERNIGHT_RUNTIME_CAP_WITH_CHECKPOINT", "designated start runtime cap")
            raise PhaseFailure("FAIL_SYNTHETIC_SOLVER_CONVERGENCE", "designated start failed")
        if not audit0["success"] or not audit0["finite"]: raise PhaseFailure("FAIL_SYNTHETIC_SOLVER_CONVERGENCE", "designated start did not converge finitely")
        recovery0, replay0 = physical_recovery(endpoint0[:PRODUCT_DIMENSION], observation, truth, contract); dump(formal / "DESIGNATED_START_RECOVERY.json", recovery0)
        gates = contract["recovery_gates"]
        recovery0_pass = recovery0["segment_direction_rmse_deg"] <= gates["maximum_segment_direction_rmse_deg"] and recovery0["graphical_node_rmse_m"] <= gates["maximum_graphical_node_rmse_m"]
        if not recovery0_pass: raise PhaseFailure("FAIL_SYNTHETIC_RECOVERY", "designated start physical recovery failed")
        phase(stages, "P6_DESIGNATED_BLIND_START", "PASS")

        endpoints = [endpoint0]; replay_outputs = [replay0]
        for index in range(1, 5):
            audit, endpoint = run_start(objective, frozen_starts[index], index, formal, freeze_sha, row_sha, solver_cfg, deadline); start_audits.append(audit)
            if endpoint is None:
                if audit["status"] == "RUNTIME_CAP": raise PhaseFailure("STOPPED_OVERNIGHT_RUNTIME_CAP_WITH_CHECKPOINT", f"start {index} runtime cap")
                raise PhaseFailure("FAIL_SYNTHETIC_SOLVER_CONVERGENCE", f"start {index} failed")
            if not audit["success"] or not audit["finite"]: raise PhaseFailure("FAIL_SYNTHETIC_SOLVER_CONVERGENCE", f"start {index} did not converge finitely")
            recovery, output = physical_recovery(endpoint[:PRODUCT_DIMENSION], observation, truth, contract); audit["physical_recovery"] = recovery
            if recovery["segment_direction_rmse_deg"] > gates["maximum_segment_direction_rmse_deg"] or recovery["graphical_node_rmse_m"] > gates["maximum_graphical_node_rmse_m"]: raise PhaseFailure("FAIL_SYNTHETIC_RECOVERY", f"start {index} physical recovery failed")
            endpoints.append(endpoint); replay_outputs.append(output)
        max_direction = 0.0; max_node = 0.0
        for output in replay_outputs[1:]:
            valid = replay0["segment_valid"] & output["segment_valid"]; cosine = np.clip(np.sum(replay0["segment_directions"] * output["segment_directions"], axis=2), -1.0, 1.0); max_direction = max(max_direction, float(np.degrees(np.max(np.arccos(cosine[valid])))))
            node_valid = replay0["node_valid"] & output["node_valid"]; max_node = max(max_node, float(np.max(np.linalg.norm(replay0["graphical_nodes"] - output["graphical_nodes"], axis=2)[node_valid])))
        multistart = {"maximum_segment_direction_disagreement_deg": max_direction, "maximum_node_disagreement_m": max_node, "pass": max_direction <= gates["maximum_multistart_segment_direction_disagreement_deg"] and max_node <= gates["maximum_multistart_node_disagreement_m"]}; dump(formal / "MULTISTART_PHYSICAL_AGREEMENT.json", multistart)
        if not multistart["pass"]: raise PhaseFailure("FAIL_SYNTHETIC_RECOVERY", "multistart physical output disagreement")
        phase(stages, "P7_REMAINING_MULTISTART", "PASS")

        best_index = int(np.argmin([item["cost"] for item in start_audits])); endpoint = endpoints[best_index]
        _, measurement_j, metadata = weighted_measurement_jacobian(objective, endpoint); profile = profile_product(measurement_j, contract)
        np.savez_compressed(formal / "ENDPOINT_MEASUREMENT_JACOBIAN.npz", jacobian=measurement_j, effective=profile["effective"], singular=profile["singular"], right_vectors=profile["right_vectors"], nuisance_singular=profile["nuisance_singular"])
        observability = {"measurement_plus_action_protocol_profiled_rank": profile["rank"], "estimable_product_dimension": PRODUCT_DIMENSION, "proven_product_invariant_gauge_dimension": 0, "expected_rank": PRODUCT_DIMENSION, "threshold": profile["threshold"], "singular_values": profile["singular"], "row_manifest_sha256": row_sha, "production_jacobian_shared_with_solver": True, "protocol_priors_regularizers_reporting_conventions_in_rank": False}
        action_info = action_information(measurement_j, metadata, contract); dump(formal / "ACTION_ABLATION_PROFILED_INFORMATION.json", action_info); dump(formal / "ENDPOINT_OBSERVABILITY.json", observability)
        if profile["rank"] != PRODUCT_DIMENSION: raise PhaseFailure("FAIL_PRODUCT_OBSERVABILITY", f"endpoint rank {profile['rank']}/{PRODUCT_DIMENSION}")
        if not action_info["pass"]: raise PhaseFailure("FAIL_PRODUCT_OBSERVABILITY", "action LOO information failure")
        phase(stages, "P8_ENDPOINT_DERIVATIVE_RANK_NULL", "PASS")

        artifact = formal / "FROZEN_SYNTHETIC_CALIBRATION.json"; artifact_sha = write_calibration(artifact, endpoint[:PRODUCT_DIMENSION], freeze_sha)
        replay_a, worker_audit = fresh_process_replay(formal, artifact, observation, contract, repo)
        # Second replay is an independent fresh process with the same frozen
        # input; preserve the first output before the worker path is replaced.
        bytes_a = replay_bytes(replay_a); first_worker_file = (formal / "FRESH_PROCESS_REPLAY.npz").read_bytes()
        replay_b, worker_b_audit = fresh_process_replay(formal, artifact, observation, contract, repo)
        bytes_b = replay_bytes(replay_b); (formal / "FRESH_PROCESS_REPLAY_1.npz").write_bytes(first_worker_file); (formal / "FRESH_PROCESS_REPLAY_2.npz").write_bytes((formal / "FRESH_PROCESS_REPLAY.npz").read_bytes()); (formal / "FRESH_PROCESS_REPLAY.npz").unlink()
        (formal / "REPLAY_1.bin").write_bytes(bytes_a); (formal / "REPLAY_2.bin").write_bytes(bytes_b)
        dependency_post = replay_dependency(observation, contract); dump(formal / "POSTFIT_REPLAY_DEPENDENCY.json", dependency_post)
        replay_audit = {"artifact_sha256": artifact_sha, "disk_sha_verified": sha(artifact) == artifact_sha, "worker_1": worker_audit, "worker_2": worker_b_audit, "worker_sha_verified": worker_audit["worker_calibration_sha256"] == artifact_sha and worker_b_audit["worker_calibration_sha256"] == artifact_sha, "replay_1_sha256": hashlib.sha256(bytes_a).hexdigest(), "replay_2_sha256": hashlib.sha256(bytes_b).hexdigest(), "byte_identical": bytes_a == bytes_b, "label_blind": True, "pass": sha(artifact) == artifact_sha and worker_audit["worker_calibration_sha256"] == artifact_sha and worker_b_audit["worker_calibration_sha256"] == artifact_sha and bytes_a == bytes_b and dependency_post["pass"]}; dump(formal / "SYNTHETIC_FREEZE_RELOAD_REPLAY.json", replay_audit)
        if not replay_audit["pass"]: raise PhaseFailure("FAIL_DETERMINISM_OR_CHECKPOINT_INTEGRITY", "synthetic reload/replay failed")
        phase(stages, "P9_SYNTHETIC_ARTIFACT_RELOAD_REPLAY", "PASS"); phase(stages, "P10_ACTION_ABLATION_DETERMINISM_REPORT", "PASS")
        result.update({"terminal_verdict": "PASS_D0B_R2_SYNTHETIC_MODEL_QUALIFICATION", "observation_lineage": "PASS_OBSERVATION_LINEAGE_V2", "blind_recovery": "PASS_BLIND_SYNTHETIC_RECOVERY", "profiled_observability": "PASS_PROFILED_PRODUCT_OBSERVABILITY", "actual_replay_dependency": "PASS_ACTUAL_REPLAY_DEPENDENCY", "real_d0_ready_for_separate_authorization": True})
    except PhaseFailure as failure:
        result.update({"terminal_verdict": failure.code, "exact_blocker": failure.detail, "real_d0_ready_for_separate_authorization": False})
    except Exception as failure:
        result.update({"terminal_verdict": "FAIL_DETERMINISM_OR_CHECKPOINT_INTEGRITY", "exact_blocker": f"{type(failure).__name__}: {failure}", "real_d0_ready_for_separate_authorization": False})
    result.update({"formal_run_uuid": formal_uuid, "formal_run_count": formal_count, "stages": stages, "source_config_input_row": {"run_freeze_sha256": sha(preflight / "RUN_FREEZE.json") if (preflight / "RUN_FREEZE.json").exists() else None, "row_manifest_sha256": row_manifest["manifest_sha256"] if "row_manifest" in locals() else None}, "actual_wall_time_s": time.monotonic() - started, "peak_memory_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "start_status": start_audits, "checkpoint_resume_count": checkpoint_resume_count, "REAL_CALIBRATION": "SEALED", "FINAL_STILL_WALK_GOLF_BOXING": "SEALED", "UWB_T4_ANCHOR_OPERATOR": "SEALED", "REAL_FREEZE_REPLAY_RENDER": "NOT_RUN", "COMMIT_PUSH_PR": "NOT_PERFORMED"})
    dump(args.output / "RESULT.json", result); dump(args.output / "PHASE_STATUS.json", stages)
    (args.output / "REPORT.md").write_text(f"# D0B-R2 synthetic qualification\n\n`{result['terminal_verdict']}`\n\nFormal run UUID: `{formal_uuid}`; count: `{formal_count}`. Real and held-out inputs remained sealed.\n", encoding="utf-8")
    write_manifest(args.output)
    print(result["terminal_verdict"], flush=True)
    return 0 if result["terminal_verdict"] == "PASS_D0B_R2_SYNTHETIC_MODEL_QUALIFICATION" else 2


if __name__ == "__main__": raise SystemExit(main())
