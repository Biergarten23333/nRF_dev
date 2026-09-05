#!/usr/bin/env python3
"""One-shot withheld-epoch A/B for the C2 root-filter influence cap.

This is deliberately not a parameter sweep.  It compares the existing 5 cm
cap with one predeclared 12 cm candidate on 16/H01/H02.  Both variants consume
the same alternating half of the UWB root observations; the other half is used
only for temporal holdout scoring.
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


ROOT = Path(__file__).resolve().parents[1]
ACTION_RUNNER = ROOT / "tools/run_c2_h01_shared_root_imu_fusion.py"
ACTIONS = ("16_squat", "H01_boxing", "H02_golf")
VARIANTS = {
    "baseline_005m": 0.05,
    "candidate_012m": 0.12,
}
PER_RUN_TIMEOUT_S = 90.0
TOTAL_TIMEOUT_S = 600.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def _withheld_errors(npz_path: Path) -> dict[str, Any]:
    data = np.load(npz_path)
    time_s = np.asarray(data["time_s"], dtype=float)
    fused = np.asarray(data["fused_root_position_world_m"], dtype=float)
    uwb_time = np.asarray(data["shared_root_time_s"], dtype=float)
    uwb_position = np.asarray(data["shared_root_position_world_m"], dtype=float)
    used = np.asarray(data["shared_root_used_for_filter"], dtype=bool)
    withheld = ~used
    in_bounds = withheld & (uwb_time >= time_s[0]) & (uwb_time <= time_s[-1])
    if int(np.sum(in_bounds)) < 50:
        raise RuntimeError("insufficient withheld UWB epochs for scoring")
    predicted = np.column_stack([
        np.interp(uwb_time[in_bounds], time_s, fused[:, axis])
        for axis in range(3)
    ])
    errors = np.linalg.norm(predicted - uwb_position[in_bounds], axis=1)
    return {
        "withheld_epoch_count": int(len(errors)),
        "median_position_error_m": float(np.median(errors)),
        "p95_position_error_m": float(np.percentile(errors, 95)),
        "maximum_position_error_m": float(np.max(errors)),
    }


def run(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    contract = {
        "schema": "biospur.c2.influence_cap_withheld_ab_contract.v1",
        "actions": list(ACTIONS),
        "variants": VARIANTS,
        "change_under_test": "MAXIMUM_POSITION_INFLUENCE_ONLY",
        "unchanged": [
            "BEACON_CLOCK",
            "RAW_RANGE_POLICY",
            "SHARED_ROOT_SOLVER",
            "MEASUREMENT_COVARIANCE",
            "IMU",
            "FK",
            "FILTER_PROCESS_NOISE",
        ],
        "training_epochs": "BOOTSTRAP_PLUS_ALTERNATING_ODD_INDEXED_ROOTS",
        "withheld_epochs": "ALTERNATING_EVEN_INDEXED_ROOTS_NEVER_APPLIED",
        "acceptance": (
            "CANDIDATE_MEDIAN_AND_P95_WITHHELD_ERROR_NON_WORSE_ON_ALL_ACTIONS;"
            "STRICTLY_BETTER_AGGREGATE_MEDIAN_AND_P95;CAP_FRACTION_LOWER;"
            "ZERO_VOLUME_EXCURSION;NO_VELOCITY_OR_BIAS_DIVERGENCE"
        ),
        "automatic_retry": False,
        "third_candidate_allowed": False,
        "per_run_timeout_s": PER_RUN_TIMEOUT_S,
        "total_timeout_s": TOTAL_TIMEOUT_S,
        "action_runner": str(ACTION_RUNNER),
        "action_runner_sha256": _sha256(ACTION_RUNNER),
        "scientific_pass_possible": False,
    }
    _write_json(output / "RUN_CONTRACT.json", contract)

    records: dict[str, dict[str, Any]] = {}
    for variant, cap_m in VARIANTS.items():
        records[variant] = {}
        for action in ACTIONS:
            remaining = TOTAL_TIMEOUT_S - (time.perf_counter() - started)
            if remaining < 10.0:
                raise RuntimeError("TOTAL_RUNTIME_BOUND")
            destination = output / variant / action
            command = [
                sys.executable,
                str(ACTION_RUNNER),
                "--output", str(destination),
                "--action", action,
                "--maximum-position-influence-m", str(cap_m),
                "--update-stride", "2",
                "--update-offset", "0",
            ]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": "src:tools:."},
                capture_output=True,
                text=True,
                timeout=min(PER_RUN_TIMEOUT_S, remaining),
                check=False,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            (destination.parent / f"{action}.stdout.log").write_text(
                completed.stdout
            )
            (destination.parent / f"{action}.stderr.log").write_text(
                completed.stderr
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"{variant}/{action}:CHILD_EXIT_{completed.returncode}"
                )
            result_path = destination / "RESULT.json"
            result = json.loads(result_path.read_text())
            npz_path = destination / result["output"]
            records[variant][action] = {
                "result": str(result_path),
                "result_sha256": _sha256(result_path),
                "withheld": _withheld_errors(npz_path),
                "trajectory": result["trajectory_diagnostics"],
                "counts": result["counts"],
                "wall_s": result["wall_s"],
            }

    comparisons: dict[str, Any] = {}
    for action in ACTIONS:
        baseline = records["baseline_005m"][action]
        candidate = records["candidate_012m"][action]
        baseline_withheld = baseline["withheld"]
        candidate_withheld = candidate["withheld"]
        checks = {
            "median_withheld_error_non_worse": (
                candidate_withheld["median_position_error_m"]
                <= baseline_withheld["median_position_error_m"]
            ),
            "p95_withheld_error_non_worse": (
                candidate_withheld["p95_position_error_m"]
                <= baseline_withheld["p95_position_error_m"]
            ),
            "position_cap_fraction_lower": (
                candidate["trajectory"]["position_influence_cap_fraction"]
                < baseline["trajectory"]["position_influence_cap_fraction"]
            ),
            "zero_volume_excursion": (
                candidate["trajectory"][
                    "fused_fraction_outside_expanded_anchor_volume"
                ] == 0.0
            ),
            "velocity_not_divergent": (
                candidate["trajectory"]["fused_root_velocity_p95_norm_mps"]
                <= max(
                    2.0,
                    1.25 * baseline["trajectory"][
                        "fused_root_velocity_p95_norm_mps"
                    ],
                )
            ),
            "accelerometer_bias_not_divergent": (
                candidate["trajectory"]["accelerometer_bias_change_norm_mps2"]
                <= max(
                    0.5,
                    1.5 * baseline["trajectory"][
                        "accelerometer_bias_change_norm_mps2"
                    ],
                )
            ),
        }
        comparisons[action] = {
            "baseline": baseline_withheld,
            "candidate": candidate_withheld,
            "checks": checks,
        }

    baseline_median = float(np.median([
        records["baseline_005m"][action]["withheld"][
            "median_position_error_m"
        ] for action in ACTIONS
    ]))
    candidate_median = float(np.median([
        records["candidate_012m"][action]["withheld"][
            "median_position_error_m"
        ] for action in ACTIONS
    ]))
    baseline_p95 = float(np.median([
        records["baseline_005m"][action]["withheld"][
            "p95_position_error_m"
        ] for action in ACTIONS
    ]))
    candidate_p95 = float(np.median([
        records["candidate_012m"][action]["withheld"][
            "p95_position_error_m"
        ] for action in ACTIONS
    ]))
    per_action_pass = all(
        all(comparison["checks"].values())
        for comparison in comparisons.values()
    )
    aggregate_checks = {
        "median_withheld_error_strictly_better": candidate_median < baseline_median,
        "p95_withheld_error_strictly_better": candidate_p95 < baseline_p95,
    }
    accepted = per_action_pass and all(aggregate_checks.values())
    aggregate = {
        "schema": "biospur.c2.influence_cap_withheld_ab_result.v1",
        "status": "CANDIDATE_ACCEPTED" if accepted else "CANDIDATE_REJECTED",
        "candidate_accepted": accepted,
        "mechanism_qualification_pass": accepted,
        "scientific_pass": False,
        "comparisons": comparisons,
        "aggregate": {
            "baseline_median_of_action_medians_m": baseline_median,
            "candidate_median_of_action_medians_m": candidate_median,
            "baseline_median_of_action_p95_m": baseline_p95,
            "candidate_median_of_action_p95_m": candidate_p95,
            "checks": aggregate_checks,
        },
        "records": records,
        "wall_s": time.perf_counter() - started,
        "boundary": (
            "TEMPORAL_UWB_HOLDOUT_CONSISTENCY_ONLY;NO_EXTERNAL_POSITION_TRUTH;"
            "NO_THIRD_CANDIDATE"
        ),
    }
    _write_json(output / "FINAL_RESULT.json", aggregate)
    top_level = [output / "RUN_CONTRACT.json", output / "FINAL_RESULT.json"]
    (output / "SHA256SUMS").write_text("".join(
        f"{_sha256(path)}  {path.name}\n" for path in top_level
    ))
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.output.resolve())
    print(json.dumps({
        "status": result["status"],
        "candidate_accepted": result["candidate_accepted"],
        "aggregate": result["aggregate"],
        "wall_s": result["wall_s"],
    }, indent=2))
    raise SystemExit(0 if result["candidate_accepted"] else 1)


if __name__ == "__main__":
    main()
