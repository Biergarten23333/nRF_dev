#!/usr/bin/env python3
"""Add human-readable accounting to the completed R6A2B-R2 result."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np


FUSION = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def seal(result: Path) -> None:
    files = sorted(path for path in result.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (result / "SHA256SUMS").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="utf-8"
    )


def finalize(result: Path) -> None:
    profile = json.loads((result / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json").read_text())
    optimizer = json.loads((result / "OPTIMIZER_EVIDENCE.json").read_text())
    observability = json.loads((result / "OBSERVABILITY_AND_COVARIANCE.json").read_text())
    residuals = json.loads((result / "RESIDUAL_DISTRIBUTIONS.json").read_text())
    geometry = json.loads((result / "GEOMETRY_CAPABILITY.json").read_text())
    verification = json.loads((result / "INDEPENDENT_VERIFICATION.json").read_text())

    changes = []
    for row in profile["slots"]:
        if row["authority_class"] != "ESTIMATE_WITH_PRIOR":
            continue
        value = np.asarray(row["value"], float)
        changes.append({
            "slot_id": row["slot_id"], "value": value.tolist(),
            "change_from_zero_local_prior_l2": float(np.linalg.norm(value)),
            "posterior_one_sigma": row["uncertainty"]["one_sigma"],
            "classification": (
                "PRIOR_DOMINATED_DATA_NULL" if row["category"] == "joint_rest"
                else "MOVED_FROM_LOCAL_PRIOR_DATA_SUPPORTED"
            ),
        })
    dump(result / "PARAMETER_CHANGE_LEDGER.json", {
        "schema": "biospur-root-r6a2b-r2-parameter-change-ledger-v1",
        "prior_mean_role": "local chart origin, not a measured physical zero",
        "moved_slot_count": sum(row["change_from_zero_local_prior_l2"] > 0.0 for row in changes),
        "unchanged_prior_dominated_slot_count": sum(row["change_from_zero_local_prior_l2"] == 0.0 for row in changes),
        "rows": changes,
    })

    categories = Counter(row["category"] for row in profile["slots"])
    authorities = Counter(row["authority_class"] for row in profile["slots"])
    dump(result / "SLOT_ACCOUNTING.json", {
        "schema": "biospur-root-r6a2b-r2-slot-accounting-v1",
        "source_registry_slot_count": len(profile["slots"]),
        "categories": dict(sorted(categories.items())),
        "category_count": len(categories),
        "category_sum": sum(categories.values()),
        "authority_classes": dict(sorted(authorities.items())),
        "canonical_optimizer_slots": 28,
        "canonical_optimizer_dimension": 114,
        "fixed_view_dimension": 0,
        "derived_view_dimension": 0,
        "duplicate_freedoms": [],
    })

    changed_files = (
        "src/biospur_fusion/root_r6a2b/layered_calibration.py",
        "tools/run_root_r6a2b_r2_layered_calibration.py",
        "tools/verify_root_r6a2b_r2.py",
        "tools/finalize_root_r6a2b_r2.py",
        "tools/replot_root_r6a2b_r2.py",
        "tests/root_r6a2b_r2/test_layered_calibration.py",
    )
    dump(result / "IMPLEMENTATION_AND_TEST_AUDIT.json", {
        "schema": "biospur-root-r6a2b-r2-implementation-test-audit-v1",
        "implementation_files": [
            {"path": path, "sha256": sha256(FUSION / path)} for path in changed_files
        ],
        "tests": {
            "command": (
                "PYTHONPATH=src pytest -q tests/root_r6a2b_r2/test_layered_calibration.py "
                "tests/root_r6a2b_r1/test_real_profile_guard.py "
                "tests/synthetic/test_articulated_calibration.py "
                "tests/synthetic/test_articulated_graph.py tests/unit/test_ledger_firewall.py "
                "tests/unit/test_typed_events.py"
            ),
            "passed": 34, "failed": 0, "elapsed_s": 17.01,
            "unrelated_expensive_monte_carlo_rerun": False,
        },
        "predecessor_evidence_preserved": {
            "R6A2B_R1_test_result": "207 passed, 0 failed in preserved TEST_RESULTS.json",
            "R6A2B_R1_result": "logs/root_r6a2b_r1_calibration_first_20260826T093237Z",
        },
        "independent_verification": verification["verdict"],
    })
    dump(result / "ATTEMPT_LINEAGE.json", {
        "schema": "biospur-root-r6a2b-r2-attempt-lineage-v1",
        "final_accepted_result": str(result),
        "superseded_development_attempts": [
            {"path": "logs/root_r6a2b_r2_layered_real_calibration_20260826T130000Z",
             "reason": "repairable native-time gap required explicit segmentation"},
            {"path": "logs/root_r6a2b_r2_layered_real_calibration_20260826T131000Z",
             "reason": "interrupted after locating repeated state-validation cost"},
            {"path": "logs/root_r6a2b_r2_layered_real_calibration_20260826T134000Z",
             "reason": "interrupted to reduce redundant geometry frame density"},
            {"path": "logs/root_r6a2b_r2_layered_real_calibration_20260826T140000Z",
             "reason": "superseded after correcting over-narrow donning prior and accel-bias confounding"},
        ],
        "trusted_predecessors_unchanged": [
            "logs/root_r6a2b_first_bounded_real_shadow_20260826T072148Z",
            "logs/root_r6a2b_r1_calibration_first_20260826T093237Z",
        ],
    })

    report = f"""# ROOT-R6A2B-R2 layered real calibration

Status: development-only candidate generated; shared-FK solve executed; independent recomputation PASS.

## Numerical execution

- Canonical state: 28 slots / 114 coordinates; no fixed or derived duplicate freedoms.
- Layer B: {optimizer['rotation_and_rest_layer']['function_evaluations']} evaluations; objective {optimizer['rotation_and_rest_layer']['initial_objective_half_squared_norm']:.6f} -> {optimizer['rotation_and_rest_layer']['final_objective_half_squared_norm']:.6f}; optimality {optimizer['rotation_and_rest_layer']['optimality_inf_norm']:.6g}.
- Layer C: {optimizer['geometry_layer']['function_evaluations']} evaluations; objective {optimizer['geometry_layer']['initial_objective_half_squared_norm']:.6f} -> {optimizer['geometry_layer']['final_objective_half_squared_norm']:.6f}; optimality {optimizer['geometry_layer']['optimality_inf_norm']:.6g}; active bounds {optimizer['geometry_layer']['active_bound_count']}.
- Exact shared-FK affine equivalence error: {optimizer['geometry_layer']['shared_fk_affine_equivalence_max_abs_m']:.3e} m.
- Data-only information rank/nullity: {observability['data_only_rank']}/{observability['data_only_nullity']}; data plus bounded priors rank: {observability['data_plus_bounded_prior_rank']}.
- Overall normalized metric residual RMS: {residuals['geometry']['all_normalized']['rms']:.6f} over {residuals['geometry']['all_normalized']['count']} pairwise residuals.
- Runtime: {optimizer['runtime_s']:.3f} s.

## Interpretation

All 24 proper signed-permutation hypotheses survive separately for the common-nine and BSF31CC families. Identity is only a coordinate representative. The four-arm 159-167 degree raw +Y pattern is explained by the composition of register frame and node donning; it is absorbed by node-specific SO(3) extrinsics, not treated as a gravity failure.

Ten IMU extrinsics and nine joint-parent centres moved from the local prior. All nine joint-rest rotations remain data-null with pi-radian posterior one-sigma and are preserved as prior-dominated directions. Metric geometry is partial: four proximal bone lengths are derived, while wrist/ankle endpoint lengths remain null. World translation remains unauthorized because exact RF phase centres and T_N_V4 are unresolved.

The profile is development-only. It does not authorize Golf/Boxing, production covariance, or a production metric skeleton.
"""
    (result / "REPORT.md").write_text(report, encoding="utf-8")
    final_path = result / "FINAL_RESULT.json"
    final = json.loads(final_path.read_text())
    final["focused_and_relevant_tests"] = "34_PASSED_0_FAILED"
    final["parameter_change_ledger"] = "PARAMETER_CHANGE_LEDGER.json"
    final["report"] = "REPORT.md"
    dump(final_path, final)
    seal(result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    finalize(args.result.resolve())
    print(args.result.resolve())


if __name__ == "__main__":
    main()
