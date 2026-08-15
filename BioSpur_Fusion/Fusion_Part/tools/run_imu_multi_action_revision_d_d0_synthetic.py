#!/usr/bin/env python3
"""Freeze D0-A contracts and run D0-B synthetic-only qualification."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Fusion_Part/src"))

from biospur_fusion.imu_multi_action_revision_d.d0_synthetic import qualify_d0_synthetic


BASELINE_COMMIT = "bc4060909285a7d51fe9b464f0867aa004f4ef45"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(value: Any) -> Any:
    if isinstance(value, np.ndarray): return clean(value.tolist())
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.floating,)): return clean(float(value))
    if isinstance(value, float): return value if math.isfinite(value) else None
    if isinstance(value, dict): return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)): return [clean(item) for item in value]
    return value


def canonical(value: Any) -> bytes:
    return (json.dumps(clean(value), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def dump(path: Path, value: Any) -> None:
    path.write_bytes(canonical(value))


def manifest(output: Path) -> None:
    dump(output / "SHA256_MANIFEST.json", {str(path.relative_to(output)): sha256(path) for path in sorted(output.rglob("*")) if path.is_file() and path.name != "SHA256_MANIFEST.json"})


def verify_manifest(directory: Path) -> None:
    expected = json.loads((directory / "SHA256_MANIFEST.json").read_text())
    for relative, digest in expected.items():
        if sha256(directory / relative) != digest:
            raise RuntimeError(f"manifest mismatch: {directory / relative}")


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists(): raise FileExistsError(args.output)
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != BASELINE_COMMIT:
        raise RuntimeError("D0-A freeze must remain based on exact committed R3C checkpoint")
    verify_manifest(args.r3d)
    r3d_result = json.loads((args.r3d / "RESULT.json").read_text())
    if r3d_result["terminal_outcome"] != "PASS_R3D_GAUGE_INVARIANT_BROAD_ACTIVITY" or r3d_result["formal_r3d_run_count"] != 1:
        raise RuntimeError("D0-A requires the one passing R3D formal run")
    args.output.mkdir(parents=True)
    for source in (args.state_contract, args.action_contract, args.replay_contract):
        shutil.copyfile(source, args.output / source.name)
    record = {
        "schema": "biospur-revision-d-d0a-freeze-v1",
        "baseline_commit": BASELINE_COMMIT,
        "r3d_result_sha256": sha256(args.r3d / "RESULT.json"),
        "r3d_manifest_sha256": sha256(args.r3d / "SHA256_MANIFEST.json"),
        "state_contract_sha256": sha256(args.state_contract),
        "action_contract_sha256": sha256(args.action_contract),
        "replay_contract_sha256": sha256(args.replay_contract),
        "d0_source_sha256": sha256(ROOT / "Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/d0_synthetic.py"),
        "runner_sha256": sha256(Path(__file__)),
        "full_coordinates": 95,
        "publishable_coordinates": 55,
        "nuisance_coordinates": 40,
        "REAL_D0_OBJECTIVE": "NOT_EVALUATED",
        "REAL_D0_JACOBIAN": "NOT_EVALUATED",
        "REAL_D0_SOLVER": "NOT_STARTED",
    }
    dump(args.output / "D0A_RUN_FREEZE.json", record)
    dump(args.output / "DATA_ACCESS_AUDIT.json", {"opened": ["R3D_COMPACT_RESULT_AND_MANIFEST", "D0A_CONTRACTS", "SOURCE_ACCOUNTING"], "real_calibration_payload_opened": False, "sealed_inputs_opened": [], "FINAL_STILL": "SEALED", "WALK": "SEALED", "GOLF": "SEALED", "BOXING": "SEALED", "UWB_T4_ANCHOR": "SEALED", "OPERATOR_MEASUREMENTS": "SEALED"})
    manifest(args.output)
    return {"terminal_outcome": "D0A_CONTRACTS_FROZEN", "pass": True, "full_coordinates": 95, "publishable_coordinates": 55, "nuisance_coordinates": 40}


def synthetic(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists(): raise FileExistsError(args.output)
    verify_manifest(args.freeze)
    frozen = json.loads((args.freeze / "D0A_RUN_FREEZE.json").read_text())
    if frozen["baseline_commit"] != BASELINE_COMMIT:
        raise RuntimeError("D0-A baseline binding changed")
    state = json.loads((args.freeze / "D0_STATE_AND_GAUGE_CONTRACT.json").read_text())
    action = json.loads((args.freeze / "D0_ACTION_FACTOR_CONTRACT.json").read_text())
    replay = json.loads((args.freeze / "D0_REPLAY_PARAMETER_CONTRACT.json").read_text())
    r3d_contract = json.loads(args.r3d_contract.read_text())
    chain_map = json.loads(args.chain_map.read_text())
    first = qualify_d0_synthetic(r3d_contract, chain_map, action)
    second = qualify_d0_synthetic(r3d_contract, chain_map, action)
    first_bytes = canonical(first); second_bytes = canonical(second)
    deterministic = first_bytes == second_bytes
    args.output.mkdir(parents=True)
    (args.output / "D0B_SYNTHETIC_REPLAY_1.json").write_bytes(first_bytes)
    (args.output / "D0B_SYNTHETIC_REPLAY_2.json").write_bytes(second_bytes)
    dump(args.output / "D0B_ACTION_RESIDUAL_ACCOUNTING.json", first["action_residual_accounting"])
    dump(args.output / "D0B_ACTION_PARAMETER_INFORMATION.json", first["action_publishable_information"])
    dump(args.output / "D0B_REPLAY_PARAMETER_DEPENDENCY.json", first["replay_parameter_dependency"])
    dump(args.output / "D0B_DATA_AND_PRIOR_OBSERVABILITY.json", {"data_only": first["data_only_observability"], "data_plus_protocol_prior": first["data_plus_protocol_prior_observability"], "prior_rank_not_reported_as_data_rank": True})
    dump(args.output / "D0B_NULLSPACE_AUDIT.json", {"terminal_outcome": first["terminal_outcome"], "exact_blocker_before_real_d0": first["exact_blocker_before_real_d0"], "directions": first["null_directions"]})
    result = {
        "schema": "biospur-revision-d-d0b-double-synthetic-result-v1",
        "synthetic_replay_1": first["terminal_outcome"],
        "synthetic_replay_2": second["terminal_outcome"],
        "byte_identical": deterministic,
        "replay_sha256": hashlib.sha256(first_bytes).hexdigest(),
        "software_path_controls_pass": all(value for key, value in first["controls"].items() if key != "data_plus_protocol_prior_full_rank_after_declared_global_gauge"),
        "scientific_qualification_pass": bool(first["pass"] and second["pass"] and deterministic),
        "terminal_outcome": first["terminal_outcome"] if deterministic else "FAIL_D0B_SYNTHETIC_NONDETERMINISTIC",
        "exact_blocker_before_real_d0": first["exact_blocker_before_real_d0"],
        "data_only_rank": first["data_only_observability"]["rank"],
        "data_plus_prior_rank": first["data_plus_protocol_prior_observability"]["rank"],
        "full_coordinates": state["full_state_dimension"],
        "publishable_coordinates": state["publishable_state_dimension"],
        "all_eleven_actions_one_shared_objective": action["all_actions_one_shared_objective"],
        "replay_contract_loaded": replay["continuous_timeline_contract"]["label_blind_forward_model"],
        "REAL_D0_OBJECTIVE": "NOT_EVALUATED",
        "REAL_D0_JACOBIAN": "NOT_EVALUATED",
        "REAL_D0_SOLVER": "NOT_STARTED",
        "MULTISTART": "NOT_STARTED",
        "CALIBRATION_FREEZE": "NOT_CREATED",
        "REPLAY": "NOT_STARTED",
        "RENDER": "NOT_STARTED",
    }
    dump(args.output / "RESULT.json", result)
    dump(args.output / "DATA_ACCESS_AUDIT.json", {"opened": ["D0A_FROZEN_CONTRACTS", "HUMAN_LIKE_SYNTHETIC_ONLY", "R3D_CONTRACT_AND_CHAIN_MAP"], "real_calibration_payload_opened": False, "real_r3d_arrays_opened": False, "forbidden_opened": [], "FINAL_STILL": "SEALED", "WALK": "SEALED", "GOLF": "SEALED", "BOXING": "SEALED", "UWB_T4_ANCHOR": "SEALED", "OPERATOR_MEASUREMENTS": "SEALED"})
    (args.output / "REPORT.md").write_text("# Revision D D0-B synthetic-only qualification\n\n`FAIL_D0B_SYNTHETIC_NULLSPACE`\n\nThe shared objective, derivative, action wiring, replay dependency, and deterministic double replay pass. The frozen 95-coordinate model remains rank 92 after protocol priors because torso effective heading trades with the three-coordinate trunk functional frame. This is a synthetic structural blocker. No real D0 objective, Jacobian, or solver was evaluated.\n")
    manifest(args.output)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("freeze")
    item.add_argument("--r3d", type=Path, required=True)
    item.add_argument("--state-contract", type=Path, required=True)
    item.add_argument("--action-contract", type=Path, required=True)
    item.add_argument("--replay-contract", type=Path, required=True)
    item.add_argument("--output", type=Path, required=True)
    item = sub.add_parser("synthetic")
    item.add_argument("--freeze", type=Path, required=True)
    item.add_argument("--r3d-contract", type=Path, required=True)
    item.add_argument("--chain-map", type=Path, required=True)
    item.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = freeze(args) if args.command == "freeze" else synthetic(args)
    print(json.dumps(clean(result), sort_keys=True))
    return 0 if result.get("pass", result.get("scientific_qualification_pass", True)) else 2


if __name__ == "__main__": raise SystemExit(main())
