#!/usr/bin/env python3
"""Independent raw-evidence verifier for the R6A2A-R2 covariance repair."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import chi2


EXPECTED_CLASSES = (
    "clean", "global_uwb_outage", "low_vertical_geometry", "observable_gyro_bias",
)
EXPECTED_CRITICAL_PATH_CLASSES = {
    "BLOCKS_BOUNDED_REAL_SHADOW", "BLOCKS_PRODUCTION_ONLY", "CAN_USE_BOUNDED_PRIOR",
    "DERIVED_NOT_INDEPENDENT", "ALREADY_CAPTURE_BOUND_AND_IMPORTABLE",
    "OPTIONAL_ACCURACY_IMPROVEMENT",
}
NEES_BOUNDS = (0.05, 20.0)  # broad legacy guard; chi-square gate is stricter
COVERAGE_BOUNDS = {1: (0.40, 0.90), 2: (0.80, 1.0), 3: (0.94, 1.0)}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wilson(successes: int, trials: int, z: float = 1.959963984540054) -> list[float]:
    p = successes / trials
    denominator = 1.0 + z * z / trials
    centre = (p + z * z / (2.0 * trials)) / denominator
    half = z * np.sqrt(p * (1.0 - p) / trials + z * z / (4.0 * trials * trials)) / denominator
    return [float(centre - half), float(centre + half)]


def close(a: float, b: float, tolerance: float = 1e-12) -> bool:
    return abs(a - b) <= tolerance * max(1.0, abs(a), abs(b))


def verify(result: Path) -> dict[str, Any]:
    ledger = json.loads((result / "VALIDATION_ATTEMPT_LEDGER.json").read_text(encoding="utf-8"))
    final_attempt = ledger["attempts"][-1]
    formal = json.loads((result / final_attempt["result"]).read_text(encoding="utf-8"))
    stored = formal["covariance_monte_carlo"]["headline_classes"]
    run_rows = formal["covariance_monte_carlo"]["run_rows"]
    coverage_rows = formal["covariance_monte_carlo"]["coverage_rows"]
    nis_rows = formal["covariance_monte_carlo"]["nis_rows"]
    recomputed: dict[str, Any] = {}
    class_checks = []
    all_seeds: list[int] = []
    for kind in EXPECTED_CLASSES:
        rows = [row for row in run_rows if row["class"] == kind]
        all_seeds.extend(int(row["seed"]) for row in rows)
        dimension = int(rows[0]["dimension"])
        normalized = np.asarray([row["normalized_nees"] for row in rows])
        bounds = [
            float(chi2.ppf(0.025, len(rows) * dimension) / (len(rows) * dimension)),
            float(chi2.ppf(0.975, len(rows) * dimension) / (len(rows) * dimension)),
        ]
        coverage = {}
        coverage_pass = True
        for level in (1, 2, 3):
            selected = [row for row in coverage_rows if row["class"] == kind and int(row["sigma_level"]) == level]
            successes = sum(int(row["covered_coordinates"]) for row in selected)
            trials = sum(int(row["coordinate_count"]) for row in selected)
            fraction = successes / trials
            coverage[str(level)] = {
                "fraction": fraction, "wilson_95": wilson(successes, trials),
                "successes": successes, "trials": trials,
            }
            coverage_pass &= COVERAGE_BOUNDS[level][0] <= fraction <= COVERAGE_BOUNDS[level][1]
        nrows = [row for row in nis_rows if row["class"] == kind and row["mean_raw_nominal_scalar_uwb_nis"] is not None]
        raw_nis = float(np.mean([row["mean_raw_nominal_scalar_uwb_nis"] for row in nrows])) if nrows else None
        effective_nis = float(np.mean([row["mean_effective_weighted_scalar_uwb_nis"] for row in nrows])) if nrows else None
        mean_nees = float(np.mean(normalized))
        row_pass = (
            len(rows) == 100 and len({int(row["seed"]) for row in rows}) == 100
            and bounds[0] <= mean_nees <= bounds[1]
            and NEES_BOUNDS[0] <= mean_nees <= NEES_BOUNDS[1]
            and coverage_pass
            and all(float(row["minimum_covariance_eigenvalue"]) >= -1e-10 for row in rows)
            and all(float(row["covariance_symmetry_max_abs"]) <= 1e-9 for row in rows)
        )
        stored_row = stored[kind]
        exact_summary = (
            close(mean_nees, float(stored_row["mean_normalized_nees"]))
            and all(close(a, b) for a, b in zip(bounds, stored_row["chi_square_95_mean_normalized_nees_bounds"], strict=True))
            and all(close(coverage[str(level)]["fraction"], stored_row["coverage"][str(level)]["fraction"]) for level in (1, 2, 3))
            and (raw_nis is None or close(raw_nis, float(stored_row["mean_raw_nominal_scalar_uwb_nis_dof_1"])))
            and (effective_nis is None or close(effective_nis, float(stored_row["mean_effective_weighted_scalar_uwb_nis_dof_1"])))
        )
        recomputed[kind] = {
            "independent_run_count": len(rows), "dimension": dimension,
            "mean_normalized_nees": mean_nees,
            "chi_square_95_mean_normalized_nees_bounds": bounds,
            "coverage": coverage,
            "mean_raw_nominal_scalar_uwb_nis_dof_1": raw_nis,
            "mean_effective_weighted_scalar_uwb_nis_dof_1": effective_nis,
            "acceptance_pass": row_pass, "stored_summary_exact": exact_summary,
        }
        class_checks.append(row_pass and exact_summary)
    freeze = json.loads((result / final_attempt["seed_manifest"]).read_text(encoding="utf-8"))
    frozen_hashes_match = all(
        value != "MISSING" and sha(result.parents[1] / name) == value
        for name, value in freeze["implementation_hashes"].items()
    )
    gates = json.loads((result / "MANDATORY_GATES.json").read_text(encoding="utf-8"))
    critical = json.loads((result / "BOUNDED_REAL_SHADOW_CRITICAL_PATH.json").read_text(encoding="utf-8"))
    critical_classes = {row["classification"] for row in critical["prerequisites"]}
    checks = {
        "raw_headline_recomputation_exact": all(class_checks),
        "four_classes_exactly_100_runs": len(run_rows) == 400 and set(stored) == set(EXPECTED_CLASSES),
        "all_qualification_seeds_unique": len(all_seeds) == len(set(all_seeds)),
        "qualification_seeds_outside_failed_and_development_ranges": all(seed >= 131000 for seed in all_seeds),
        "implementation_hashes_still_frozen": frozen_hashes_match,
        "all_36_gates_pass": gates["passed"] == gates["total"] == 36 and gates["all_pass"],
        "attempt_ledger_complete_and_unhidden": (
            len(ledger["attempts"]) >= 1
            and ledger["attempts"][-1]["status"] == "PASS"
            and all(row["status"] in {"PASS", "FAIL"} for row in ledger["attempts"])
            and sum(row["status"] == "PASS" for row in ledger["attempts"]) == 1
        ),
        "critical_path_uses_only_allowed_classes": critical_classes <= EXPECTED_CRITICAL_PATH_CLASSES,
        "critical_path_contains_every_class": critical_classes == EXPECTED_CRITICAL_PATH_CLASSES,
        "real_fusion_not_executed": critical["real_fusion_executed"] is False,
        "real_calibration_slots_written": critical["real_calibration_slots_written"] is False,
    }
    return {
        "schema": "biospur-root-r6a2a-r2-covariance-repair-independent-verification-v1",
        "recomputed_headline_metrics": recomputed,
        "checks": checks,
        "pass": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args()
    result = args.result.resolve()
    report = verify(result)
    (result / "INDEPENDENT_VERIFICATION.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8",
    )
    print(json.dumps({"pass": report["pass"], "checks": report["checks"]}, sort_keys=True))
    return 0 if report["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
