#!/usr/bin/env python3
"""Bounded 00--19 C2 shared-root UWB/IMU diagnostic.

Each action runs in a killable child with a local wall limit.  The sequence
stops on the first failed action or mechanism gate; extending a timeout or
starting another attempt is intentionally not part of this runner.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import numpy as np

from biospur_fusion.c2_uwb_root_world.calibration import CALIBRATION_ORDER


ROOT = Path(__file__).resolve().parents[1]
ACTION_RUNNER = ROOT / "tools/run_c2_h01_shared_root_imu_fusion.py"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


STRICT_STATIC_ROOT_ACTIONS = {
    "00_initial_still",
    "02_t_pose",
    "17_final_still",
}


def _mechanism_checks(result: dict[str, Any], action: str) -> dict[str, bool]:
    counts = result["counts"]
    trajectory = result["trajectory_diagnostics"]
    attempted = int(counts["position_updates_attempted_after_bootstrap"])
    accepted = int(counts["position_updates_accepted"])
    return {
        "diagnostic_completed": (
            result["status"] == "C2_SHARED_ROOT_IMU_DIAGNOSTIC_COMPLETE"
        ),
        "decode_errors_zero": int(counts["decode_errors"]) == 0,
        "at_least_95pct_position_updates_accepted": (
            attempted > 0 and accepted / attempted >= 0.95
        ),
        "fused_root_inside_expanded_anchor_volume": (
            float(trajectory["fused_fraction_outside_expanded_anchor_volume"])
            == 0.0
        ),
        "position_influence_cap_below_action_threshold": (
            float(trajectory["position_influence_cap_fraction"])
            <= (0.05 if action in STRICT_STATIC_ROOT_ACTIONS else 0.50)
        ),
        "finite_fused_velocity": np.isfinite([
            trajectory["fused_root_velocity_end_norm_mps"],
            trajectory["fused_root_velocity_p95_norm_mps"],
        ]).all().item(),
    }


def run(
    output: Path,
    *,
    per_action_timeout_s: float = 90.0,
    total_timeout_s: float = 1_200.0,
    resume: bool = False,
    link_selection: str = "all",
    node_selection: str = "adaptive",
) -> dict[str, Any]:
    if output.exists() and not resume:
        raise FileExistsError(output)
    if not 10.0 <= per_action_timeout_s <= 180.0:
        raise ValueError("per-action timeout must be between 10 and 180 seconds")
    if not per_action_timeout_s <= total_timeout_s <= 1_800.0:
        raise ValueError("total timeout must be between one action and 30 minutes")
    output.mkdir(parents=True, exist_ok=resume)
    started = time.perf_counter()
    contract = {
        "schema": "biospur.c2.calibration_shared_root_imu_bounded_run.v1",
        "actions": list(CALIBRATION_ORDER),
        "per_action_timeout_s": per_action_timeout_s,
        "total_timeout_s": total_timeout_s,
        "action_runner": str(ACTION_RUNNER),
        "action_runner_sha256": _sha256(ACTION_RUNNER),
        "stop_policy": "FIRST_CHILD_FAILURE_OR_FIRST_MECHANISM_GATE_FAILURE",
        "retry_policy": "NO_AUTOMATIC_RETRY",
        "scientific_pass_possible": False,
        "link_selection": link_selection,
        "node_selection": node_selection,
    }
    contract_path = output / "RUN_CONTRACT.json"
    if contract_path.exists():
        existing_contract = json.loads(contract_path.read_text())
        if existing_contract != contract:
            raise ValueError("resume contract differs from existing run")
    else:
        _write_json(contract_path, contract)

    checkpoint_path = output / "CHECKPOINT.json"
    checkpoint = (
        json.loads(checkpoint_path.read_text())
        if resume and checkpoint_path.exists() else {}
    )
    base_wall_s = float(checkpoint.get("elapsed_wall_s", 0.0))
    results: dict[str, Any] = {}
    for action in checkpoint.get("completed_actions", []):
        result_path = output / action / "RESULT.json"
        result = json.loads(result_path.read_text())
        checks = _mechanism_checks(result, action)
        prior = checkpoint["results"][action]
        results[action] = {
            **prior,
            "checks": checks,
            "mechanism_gate_pass": all(checks.values()),
        }
    stopped_reason = None
    for action in CALIBRATION_ORDER:
        if action in results:
            if not results[action]["mechanism_gate_pass"]:
                stopped_reason = f"{action}:MECHANISM_GATE_FAILURE"
                break
            continue
        elapsed = base_wall_s + time.perf_counter() - started
        remaining = total_timeout_s - elapsed
        if remaining < 10.0:
            stopped_reason = "TOTAL_RUNTIME_BOUND"
            break
        action_output = output / action
        command = [
            sys.executable,
            str(ACTION_RUNNER),
            "--output",
            str(action_output),
            "--action",
            action,
            "--link-selection",
            link_selection,
            "--node-selection",
            node_selection,
        ]
        action_started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": "src:."},
                capture_output=True,
                text=True,
                timeout=min(per_action_timeout_s, remaining),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            (output / f"{action}.stdout.log").write_text(exc.stdout or "")
            (output / f"{action}.stderr.log").write_text(exc.stderr or "")
            stopped_reason = f"{action}:RUNTIME_BOUND"
            break
        (output / f"{action}.stdout.log").write_text(completed.stdout)
        (output / f"{action}.stderr.log").write_text(completed.stderr)
        if completed.returncode != 0:
            stopped_reason = f"{action}:CHILD_EXIT_{completed.returncode}"
            break
        result_path = action_output / "RESULT.json"
        result = json.loads(result_path.read_text())
        checks = _mechanism_checks(result, action)
        results[action] = {
            "result": str(result_path),
            "result_sha256": _sha256(result_path),
            "child_wall_s": time.perf_counter() - action_started,
            "checks": checks,
            "mechanism_gate_pass": all(checks.values()),
            "trajectory_diagnostics": result["trajectory_diagnostics"],
            "counts": result["counts"],
        }
        _write_json(output / "CHECKPOINT.json", {
            "completed_actions": list(results),
            "results": results,
            "elapsed_wall_s": base_wall_s + time.perf_counter() - started,
        })
        if not all(checks.values()):
            stopped_reason = f"{action}:MECHANISM_GATE_FAILURE"
            break

    complete = len(results) == len(CALIBRATION_ORDER)
    mechanism_pass = complete and all(
        row["mechanism_gate_pass"] for row in results.values()
    )
    aggregate = {
        "schema": "biospur.c2.calibration_shared_root_imu_aggregate.v1",
        "status": (
            "MECHANISM_GATE_PASS_00_TO_19"
            if mechanism_pass else "STOPPED_FAIL_CLOSED"
        ),
        "mechanism_qualification_pass": mechanism_pass,
        "scientific_pass": False,
        "completed_action_count": len(results),
        "required_action_count": len(CALIBRATION_ORDER),
        "stopped_reason": stopped_reason,
        "wall_s": base_wall_s + time.perf_counter() - started,
        "results": results,
        "boundary": (
            "NO_EXTERNAL_WORLD_GROUND_TRUTH;DISPLAY_PROXY_TAG_OFFSETS;"
            "MECHANISM_AND_DRIFT_CONTAINMENT_ONLY"
        ),
    }
    _write_json(output / "AGGREGATE.json", aggregate)
    sealed = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    (output / "SHA256SUMS").write_text("".join(
        f"{_sha256(path)}  {path.name}\n"
        for path in sealed if path.is_file()
    ))
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--per-action-timeout-s", type=float, default=90.0)
    parser.add_argument("--total-timeout-s", type=float, default=1_200.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--link-selection", choices=("all", "top4_facing"), default="all"
    )
    parser.add_argument(
        "--node-selection", choices=("adaptive", "all_available"),
        default="adaptive",
    )
    args = parser.parse_args()
    result = run(
        args.output.resolve(),
        per_action_timeout_s=args.per_action_timeout_s,
        total_timeout_s=args.total_timeout_s,
        resume=args.resume,
        link_selection=args.link_selection,
        node_selection=args.node_selection,
    )
    print(json.dumps({
        key: result[key] for key in (
            "status", "mechanism_qualification_pass", "scientific_pass",
            "completed_action_count", "required_action_count",
            "stopped_reason", "wall_s",
        )
    }, indent=2))
    raise SystemExit(0 if result["mechanism_qualification_pass"] else 1)


if __name__ == "__main__":
    main()
