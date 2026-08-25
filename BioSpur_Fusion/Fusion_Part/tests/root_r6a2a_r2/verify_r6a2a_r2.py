#!/usr/bin/env python3
"""Independent R6A2A-R2 evidence verifier and final checksum sealer."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from biospur_fusion.root_r6a2a.contracts import ALL_NODES  # noqa: E402
from biospur_fusion.root_r6a2a_r2.qualification import (  # noqa: E402
    authority_negative_controls, authority_static_audit, rng_counterfactual,
)
from biospur_fusion.root_r6a2a_r2.estimator import all_node_covariance_mapping  # noqa: E402
from biospur_fusion.root_r6a2a.shadow import corrected_body_model  # noqa: E402


EXPECTED = {
    "logs/root_r6a2a_synthetic_fault_aware_shadow_20260825T114046Z/SHA256SUMS": "ab17eed4c1c8e37079f844688100572c5515b2a3121b3cecf6d4f0086978b7f7",
    "logs/root_r6a2a_r1_execution_audit_20260825T121247Z/SHA256SUMS": "b00bc9f802f3c499b7c5c46418744b452af6515cb7a2349444f659befc8e3c60",
    "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json": "b159043eb7da4518ac6349832ca3c50e0b3453b1e35d97bbac29223f3e85c4eb",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def verify(out: Path) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["sealed_predecessors"] = all(sha(ROOT / path) == digest for path, digest in EXPECTED.items())
    implementation = json.loads((out / "IMPLEMENTATION_FILE_HASHES.json").read_text())
    checks["implementation_hashes"] = all((ROOT / path).exists() and sha(ROOT / path) == digest for path, digest in implementation.items())
    checks["authority_boundary"] = authority_static_audit()["pass"]
    checks["negative_controls_rerun"] = authority_negative_controls(ROOT)["pass"]
    checks["rng_isolation_rerun"] = rng_counterfactual(ROOT)["pass"]
    accounting = json.loads((out / "OBSERVATION_ACCOUNTING_RESULTS.json").read_text())
    checks["accounting_recount"] = all(row["expected"] == row["terminal"] for row in accounting["scenarios"].values())
    formal = json.loads((out / "FORMAL_VALIDATION_RESULT.json").read_text())
    persistent_ids = {
        item["persistent_id"]
        for scenario in formal["scenario_results"].values() for item in scenario["accounting"]
    }
    checks["persistent_health_identity_reconstruction"] = all(not value.startswith("m8") for value in persistent_ids)
    modes = formal["scenario_results"]["val_combined_dropout_81047"]["metrics"]["mode_sequence"]
    checks["combined_mode"] = "SINGLE_NODE_IMU_AND_UWB_DEGRADED" in modes
    bias = formal["scenario_results"]["val_bias_long_81083"]["metrics"]
    checks["bias_recalculation"] = bias["bias_total_update_count"] > 0 and abs(bias["bias_estimate_at_evaluation_rad_s"][0]) > 0.01
    mapping = all_node_covariance_mapping(corrected_body_model(ROOT))
    checks["all_node_mapping_recalculation"] = set(mapping["nodes"]) == set(ALL_NODES) and all(not row["discarded"] for row in mapping["nodes"].values())
    mc = formal["covariance_monte_carlo"]
    recomputed = {}
    for kind in mc["headline_classes"]:
        rows = [row for row in mc["run_rows"] if row["class"] == kind]
        recomputed[kind] = float(np.mean([row["normalized_nees"] for row in rows]))
    checks["nees_recalculation"] = all(
        abs(recomputed[kind] - mc["headline_classes"][kind]["mean_normalized_nees"]) <= 1e-12
        for kind in recomputed
    )
    coverage_recomputed = {}
    for kind in mc["headline_classes"]:
        coverage_recomputed[kind] = {}
        for level in (1, 2, 3):
            rows = [row for row in mc["coverage_rows"] if row["class"] == kind and row["sigma_level"] == level]
            coverage_recomputed[kind][str(level)] = sum(row["covered_coordinates"] for row in rows) / sum(row["coordinate_count"] for row in rows)
    checks["coverage_recalculation"] = all(
        abs(coverage_recomputed[kind][level] - mc["headline_classes"][kind]["coverage"][level]["fraction"]) <= 1e-12
        for kind in coverage_recomputed for level in ("1", "2", "3")
    )
    recovery = json.loads((out / "DEVELOPMENT_ABLATIONS.json").read_text())["comparisons"]["recovery_ramp_disabled"]
    checks["recovery_state_and_covariance_effect"] = recovery["full"] < recovery["ablated"]
    manifest = json.loads((out / "FROZEN_VALIDATION_MANIFEST.json").read_text())
    freeze = json.loads((out / "VALIDATION_FREEZE_ATTESTATION.json").read_text())
    checks["development_validation_separation"] = not manifest["validation_output_opened_before_freeze"] and sha(out / "FROZEN_VALIDATION_MANIFEST.json") == freeze["manifest_sha256"]
    checks["no_validation_retuning"] = formal["formal_run_ordinal"] == 1 and not formal["rerun"] and not formal["retuned_after_opening"]
    predecessor_paths = [
        "tests/root_r6a0", "tests/root_r6a1a", "tests/root_r6a1b", "tests/root_r6a1c",
        "tests/root_r6a1c_bsf31cc", "tests/root_r6a2a", "tests/root_r6a2a_r1",
    ]
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", *predecessor_paths], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    checks["predecessor_tests_rerun"] = run.returncode == 0
    ledger = json.loads((ROOT / next(path for path in EXPECTED if path.endswith("CALIBRATION_SLOT_LEDGER.json"))).read_text())
    slots = ledger["slots"]
    checks["real_slots_recount"] = len(slots) == 87 and all(row["value"] is None and row["status"] == "FROZEN_UNCERTAIN" for row in slots)
    scope = json.loads((out / "REPAIR_SCOPE_AND_OWNERSHIP.json").read_text())
    checks["no_real_payload_access"] = not scope["real_payloads_opened"] and not scope["root_r6a2b_started"]
    gates = json.loads((out / "MANDATORY_GATES.json").read_text())
    checks["primary_gate_recount"] = gates["passed"] == sum(row["pass"] for row in gates["results"].values()) and gates["total"] == 36
    return {
        "schema": "biospur-root-r6a2a-r2-independent-verification-v1",
        "checks": checks, "recomputed_mean_normalized_nees": recomputed,
        "recomputed_coverage": coverage_recomputed,
        "predecessor_test_output": run.stdout,
        "pass": all(checks.values()),
        "primary_summary_booleans_trusted_without_recomputation": False,
    }


def seal(out: Path) -> dict[str, Any]:
    files = sorted(path for path in out.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    rows = [f"{sha(path)}  {path.name}" for path in files]
    (out / "SHA256SUMS").write_text("\n".join(rows) + "\n", encoding="utf-8")
    failures = []
    for row in rows:
        digest, name = row.split("  ", 1)
        if sha(out / name) != digest:
            failures.append(name)
    return {"entry_count": len(rows), "failures": failures, "pass": not failures}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    result = verify(out)
    dump(out / "INDEPENDENT_VERIFICATION.json", result)
    final = json.loads((out / "FINAL_RESULT.json").read_text())
    final["independent_verification_pending"] = False
    final["independent_verification_pass"] = result["pass"]
    if not result["pass"] and final["verdict"].startswith("PASS_"):
        final["verdict"] = "BLOCKED_ROOT_R6A2A_R2_EVIDENCE_OR_REPLAY_INTEGRITY_FAILURE"
        final["root_r6a2a_synthetic_integrated_shadow_qualified"] = False
    dump(out / "FINAL_RESULT.json", final)
    markdown = (out / "FINAL_RESULT.md").read_text()
    markdown += f"\nIndependent verification: {'PASS' if result['pass'] else 'FAIL'}.\n"
    (out / "FINAL_RESULT.md").write_text(markdown, encoding="utf-8")
    seal_result = seal(out)
    print(json.dumps({"verification_pass": result["pass"], "seal": seal_result}, sort_keys=True))
    return 0 if result["pass"] and seal_result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

