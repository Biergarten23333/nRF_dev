#!/usr/bin/env python3
"""Independent verifier for a Root-R6A2A result tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np


FUSION = Path(__file__).resolve().parents[2]
SRC = FUSION / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from biospur_fusion.root_r6a2a.contracts import (
    ALL_NODES,
    COMMON_NINE,
    FAMILY_BSF31CC,
    FAMILY_COMMON_NINE,
    registry_from_sealed_addendum,
)
from biospur_fusion.root_r6a2a.qualification import run_qualification
from biospur_fusion.root_r6a2a.shadow import build_synthetic_calibration, corrected_body_model, truth_state


REQUIRED = {
    "FINAL_RESULT.md",
    "FINAL_RESULT.json",
    "SYSTEM_ARCHITECTURE_CONTRACT.json",
    "UNIFIED_HARDWARE_REGISTRY.json",
    "SYNTHETIC_REAL_ISOLATION_CONTRACT.json",
    "STATE_AND_OWNERSHIP_CONTRACT.json",
    "MEASUREMENT_HEALTH_AND_ATTRIBUTION_CONTRACT.json",
    "DEGRADED_MODE_MATRIX.json",
    "FAULT_INJECTION_MANIFEST.json",
    "FAULT_INJECTION_QUALIFICATION.json",
    "RECOVERY_AND_REENTRY_CONTRACT.json",
    "COVARIANCE_QUALIFICATION.json",
    "SYNTHETIC_SCENARIO_RESULTS.json",
    "PREDECESSOR_REGRESSION_RESULTS.json",
    "PROTECTED_HASHES_BEFORE_AFTER.json",
    "QUALIFICATION_GATES.json",
    "TEST_RESULTS.json",
    "IMPLEMENTATION_FILES.json",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add(checks: list[dict[str, Any]], name: str, passed: bool, detail: Any = None) -> None:
    checks.append({"name": name, "pass": bool(passed), "detail": detail})


def checksum_check(root: Path) -> tuple[bool, dict[str, Any]]:
    manifest = root / "SHA256SUMS"
    if not manifest.is_file():
        return False, {"reason": "SHA256SUMS missing"}
    expected: dict[str, str] = {}
    for line in manifest.read_text().splitlines():
        digest, name = line.split("  ", 1)
        expected[name] = digest
    actual_names = {path.name for path in root.iterdir() if path.is_file() and path.name != "SHA256SUMS"}
    failures = [name for name, digest in expected.items() if not (root / name).is_file() or sha256_file(root / name) != digest]
    return set(expected) == actual_names and not failures, {
        "manifest_entries": len(expected),
        "actual_files": len(actual_names),
        "missing_or_mismatched": failures,
        "complete": set(expected) == actual_names,
    }


def independent_bone_and_fk_check(fusion: Path) -> dict[str, Any]:
    registry = registry_from_sealed_addendum(fusion)
    model = corrected_body_model(fusion)
    calibration = build_synthetic_calibration(model, registry)
    incoming = {joint.child: joint for joint in model.joints}
    outgoing = {joint.parent: joint for joint in model.joints}
    distal_point = {
        "forearm_left": "wrist_left",
        "forearm_right": "wrist_right",
        "shank_left": "ankle_left",
        "shank_right": "ankle_right",
    }
    calculated = {}
    errors = {}
    for segment in (
        "upper_arm_left", "forearm_left", "upper_arm_right", "forearm_right",
        "thigh_left", "shank_left", "thigh_right", "shank_right",
    ):
        start = calibration.vector(incoming[segment].child_offset_slot, 3)
        if segment in outgoing:
            end = calibration.vector(outgoing[segment].parent_offset_slot, 3)
        else:
            point_id = distal_point[segment]
            point = next(item for item in model.derived_points if item.point_id == point_id)
            end = calibration.vector(point.offset_slot, 3)
        value = float(np.linalg.norm(end - start))
        declared = float(calibration.vector(f"bone_length:{segment}", 1)[0])
        calculated[segment] = value
        errors[segment] = abs(value - declared)
    closure = []
    for time_s in (0.0, 0.17, 0.43, 0.89, 1.2):
        state = truth_state(model, time_s, 6201)
        closure.append(float(np.max(np.abs(model.all_predictions(state, calibration)["kinematic_residuals"]))))
    return {
        "calculated_bone_lengths_m": calculated,
        "maximum_declared_length_error_m": max(errors.values()),
        "maximum_fk_joint_closure_m": max(closure),
        "pass": max(errors.values()) < 1e-12 and max(closure) < 1e-12,
    }


def run_pytest(paths: list[str], result: Path | None = None) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.setdefault("OPENBLAS_NUM_THREADS", "1")
    environment.setdefault("OMP_NUM_THREADS", "1")
    if result is not None:
        environment["R6A2A_RESULT_DIR"] = str(result)
    process = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *paths],
        cwd=FUSION,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return {"pass": process.returncode == 0, "returncode": process.returncode, "output": process.stdout}


def verify(root: Path, rerun_predecessors: bool, require_checksums: bool, check_only: bool) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    present = {path.name for path in root.iterdir() if path.is_file()}
    missing = sorted(REQUIRED - present)
    add(checks, "required_artifacts_present", not missing, missing)
    if missing:
        return {"schema": "biospur-root-r6a2a-independent-verification-v1", "all_pass": False, "checks": checks}

    final = json.loads((root / "FINAL_RESULT.json").read_text())
    implementation = json.loads((root / "IMPLEMENTATION_FILES.json").read_text())
    implementation_failures = [
        row["path"] for row in implementation["files"]
        if not (FUSION / row["path"]).is_file() or sha256_file(FUSION / row["path"]) != row["sha256"]
    ]
    add(checks, "implementation_hashes_match", not implementation_failures, implementation_failures)

    reconstructed = registry_from_sealed_addendum(FUSION).as_dict()
    recorded = json.loads((root / "UNIFIED_HARDWARE_REGISTRY.json").read_text())
    add(checks, "unified_registry_reconstructed_from_sealed_source", reconstructed == recorded)
    exact_family = (
        recorded["family_by_node"].get("BSF31CC") == FAMILY_BSF31CC
        and all(recorded["family_by_node"].get(node) == FAMILY_COMMON_NINE for node in COMMON_NINE)
        and set(recorded["family_by_node"]) == set(ALL_NODES)
    )
    add(checks, "exact_family_membership_and_no_cross_reuse", exact_family and recorded["cross_family_mechanical_transform_reuse"] == "FORBIDDEN")

    isolation = json.loads((root / "SYNTHETIC_REAL_ISOLATION_CONTRACT.json").read_text())
    ledger_path = Path(isolation["real_registry"]["path"])
    ledger = json.loads(ledger_path.read_text())
    isolation_pass = (
        sha256_file(ledger_path) == isolation["real_registry"]["sha256"]
        and len(ledger["slots"]) == 87
        and all(row["value"] is None and row["status"] == "FROZEN_UNCERTAIN" for row in ledger["slots"])
        and isolation["real_registry"]["writes_performed"] == 0
    )
    add(checks, "synthetic_real_isolation_reread", isolation_pass)
    protected = json.loads((root / "PROTECTED_HASHES_BEFORE_AFTER.json").read_text())
    add(checks, "protected_predecessors_byte_exact", protected["all_exact"] and protected["before"] == protected["after"])

    if check_only:
        report_path = root / "INDEPENDENT_VERIFICATION.json"
        prior = json.loads(report_path.read_text()) if report_path.is_file() else {}
        add(checks, "prior_independent_rerun_passed", prior.get("all_pass") is True)
    else:
        rerun = run_qualification(FUSION)
        recorded_gates = json.loads((root / "QUALIFICATION_GATES.json").read_text())
        add(checks, "all_28_gates_independently_rerun", rerun["gates"]["all_pass"] and rerun["gates"] == recorded_gates, {"passed": rerun["gates"]["passed"]})
        primary_scenarios = json.loads((root / "SYNTHETIC_SCENARIO_RESULTS.json").read_text())["scenarios"]
        replay_match = all(
            rerun["scenario_results"][name]["deterministic_replay_sha256"] == result["deterministic_replay_sha256"]
            for name, result in primary_scenarios.items()
        )
        add(checks, "mandatory_fault_scenarios_independently_replayed", replay_match, len(primary_scenarios))
        bone = independent_bone_and_fk_check(FUSION)
        add(checks, "bone_invariance_and_shared_fk_independently_recalculated", bone["pass"], bone)
        covariance = rerun["covariance"]
        covariance_pass = (
            covariance["all_finite"]
            and covariance["maximum_symmetry_error"] <= 1e-10
            and covariance["minimum_eigenvalue"] >= -1e-10
            and covariance["outage_root_covariance_growth"] > 0.0
            and covariance["outage_yaw_covariance_growth"] > 0.0
        )
        add(checks, "covariance_symmetry_psd_and_outage_growth_recalculated", covariance_pass, covariance)
        recovery = rerun["scenario_results"]["uwb_full_outage_recovery"]
        global_transitions = [row["to"] for row in recovery["health_transitions"] if row["scope"] == "global_observability"]
        recovery_pass = global_transitions == ["SUSPECT", "DEGRADED", "ISOLATED", "RECOVERING", "REQUALIFYING", "HEALTHY"] and "CONTROLLED_REENTRY" in recovery["metrics"]["mode_sequence"] and recovery["metrics"]["maximum_uwb_correction_m"] <= 0.0800001
        add(checks, "recovery_transition_log_independently_verified", recovery_pass, global_transitions)
        r6a2a_tests = run_pytest(["tests/root_r6a2a"], root)
        add(checks, "r6a2a_tests_rerun", r6a2a_tests["pass"], r6a2a_tests["output"])
        if rerun_predecessors:
            predecessor = run_pytest(["tests/root_r6a0", "tests/root_r6a1a", "tests/root_r6a1b", "tests/root_r6a1c", "tests/root_r6a1c_bsf31cc"])
            add(checks, "predecessor_suites_rerun", predecessor["pass"], predecessor["output"])
        else:
            recorded_predecessor = json.loads((root / "PREDECESSOR_REGRESSION_RESULTS.json").read_text())
            add(checks, "predecessor_suites_recorded_green", recorded_predecessor["all_pass"], recorded_predecessor["output"])

    final_pass = (
        final["principal_verdict"] == "PASS_ROOT_R6A2A_SYNTHETIC_FAULT_AWARE_WHOLE_BODY_SHADOW_QUALIFIED"
        and final["root_r6a2a_synthetic_integrated_shadow_qualified"] is True
        and final["root_r6a2b_bounded_real_shadow_ready"] is False
        and final["real_body_update_authorized"] is False
        and final["production_fusion_authorized"] is False
    )
    add(checks, "narrow_final_verdict_and_readiness", final_pass)
    if require_checksums:
        valid, detail = checksum_check(root)
        add(checks, "final_checksums_complete_and_valid", valid, detail)
    report = {
        "schema": "biospur-root-r6a2a-independent-verification-v1",
        "primary_report_trusted_without_reread": False,
        "check_only": check_only,
        "checks": checks,
        "check_count": len(checks),
        "all_pass": all(row["pass"] for row in checks),
        "verifier_sha256": sha256_file(Path(__file__)),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--rerun-predecessors", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--require-checksums", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = args.result.resolve()
    report = verify(root, args.rerun_predecessors, args.require_checksums, args.check_only)
    if args.write_report:
        (root / "INDEPENDENT_VERIFICATION.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    raise SystemExit(0 if report["all_pass"] else 1)


if __name__ == "__main__":
    main()
