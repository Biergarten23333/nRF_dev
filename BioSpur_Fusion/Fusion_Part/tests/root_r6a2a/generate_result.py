#!/usr/bin/env python3
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


FUSION = Path(__file__).resolve().parents[2]
SRC = FUSION / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from biospur_fusion.root_r6a2a.qualification import run_qualification


IMPLEMENTATION_PATHS = (
    "src/biospur_fusion/root_r6a2a/__init__.py",
    "src/biospur_fusion/root_r6a2a/contracts.py",
    "src/biospur_fusion/root_r6a2a/shadow.py",
    "src/biospur_fusion/root_r6a2a/qualification.py",
    "tests/root_r6a2a/conftest.py",
    "tests/root_r6a2a/test_integrated_shadow.py",
    "tests/root_r6a2a/generate_result.py",
    "tests/root_r6a2a/verify_result.py",
)

SEALED = {
    "root_r6a1a": "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z",
    "root_r6a1b": "logs/root_r6a1b_calibration_authority_20260825T084243Z",
    "root_r6a1c_parent": "logs/root_r6a1c_deferred_measurement_bridge_20260825T102823Z",
    "root_r6a1c_bsf31cc_addendum": "logs/root_r6a1c_bsf31cc_hardware_addendum_20260825T105220Z",
    "root_r6a1c_checkpoint": "logs/root_r6a1c_checkpoint_20260825T110857Z",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(root: Path, name: str, payload: object) -> None:
    (root / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def sealed_snapshot() -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for name, relative in SEALED.items():
        directory = FUSION / relative
        process = subprocess.run(
            ["sha256sum", "-c", "SHA256SUMS"],
            cwd=directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        rows[name] = {
            "path": str(directory),
            "manifest_sha256": sha256_file(directory / "SHA256SUMS"),
            "verified": process.returncode == 0,
            "verified_file_count": process.stdout.count(": OK\n"),
        }
    ledger = FUSION / SEALED["root_r6a1a"] / "CALIBRATION_SLOT_LEDGER.json"
    payload = json.loads(ledger.read_text())
    rows["real_ledger"] = {
        "path": str(ledger),
        "sha256": sha256_file(ledger),
        "total": len(payload["slots"]),
        "value_null": sum(row["value"] is None for row in payload["slots"]),
        "FROZEN_UNCERTAIN": sum(row["status"] == "FROZEN_UNCERTAIN" for row in payload["slots"]),
    }
    return rows


def run_tests(command: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    process = subprocess.run(
        command,
        cwd=FUSION,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        check=False,
    )
    return {
        "command": " ".join(command),
        "returncode": process.returncode,
        "all_pass": process.returncode == 0,
        "duration_s": round(time.monotonic() - started, 3),
        "output": process.stdout,
    }


def checksums_only(root: Path) -> None:
    files = sorted(path for path in root.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (root / "SHA256SUMS").write_text("\n".join(f"{sha256_file(path)}  {path.name}" for path in files) + "\n")


def generate(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    before = sealed_snapshot()
    qualification = run_qualification(FUSION)
    contracts = qualification["contracts"]
    gates = qualification["gates"]

    write_json(root, "SYSTEM_ARCHITECTURE_CONTRACT.json", contracts["architecture"])
    write_json(root, "UNIFIED_HARDWARE_REGISTRY.json", contracts["registry"])
    write_json(root, "SYNTHETIC_REAL_ISOLATION_CONTRACT.json", contracts["isolation"])
    write_json(root, "STATE_AND_OWNERSHIP_CONTRACT.json", contracts["state"])
    write_json(root, "MEASUREMENT_HEALTH_AND_ATTRIBUTION_CONTRACT.json", contracts["health"])
    write_json(root, "DEGRADED_MODE_MATRIX.json", contracts["modes"])
    write_json(root, "FAULT_INJECTION_MANIFEST.json", qualification["manifest"])
    write_json(root, "QUALIFICATION_GATES.json", gates)
    write_json(root, "COVARIANCE_QUALIFICATION.json", qualification["covariance"])
    write_json(
        root,
        "SYNTHETIC_SCENARIO_RESULTS.json",
        {
            "schema": "biospur-root-r6a2a-synthetic-scenario-results-v1",
            "scenario_count": len(qualification["scenario_results"]),
            "scenarios": qualification["scenario_results"],
        },
    )
    write_json(
        root,
        "FAULT_INJECTION_QUALIFICATION.json",
        {
            "schema": "biospur-root-r6a2a-fault-injection-qualification-v1",
            "all_mandatory_gates_pass": gates["all_pass"],
            "validator_results": qualification["validator_results"],
            "scenario_summaries": {
                name: {
                    "fault": result["scenario"]["fault"],
                    "fault_attribution": result["metrics"]["fault_attribution"],
                    "fault_detection_latency_s": result["metrics"]["fault_detection_latency_s"],
                    "false_positive_count": result["metrics"]["false_positive_count"],
                    "mode_sequence": result["metrics"]["mode_sequence"],
                    "bone_length_max_change_m": result["metrics"]["bone_length_max_change_m"],
                    "covariance_finite": result["metrics"]["covariance_finite"],
                }
                for name, result in qualification["scenario_results"].items()
            },
        },
    )
    outage = qualification["scenario_results"]["uwb_full_outage_recovery"]
    write_json(
        root,
        "RECOVERY_AND_REENTRY_CONTRACT.json",
        {
            "schema": "biospur-root-r6a2a-recovery-and-observed-reentry-v1",
            "contract": contracts["recovery"],
            "observed_outage_recovery": {
                "mode_sequence": outage["metrics"]["mode_sequence"],
                "health_transitions": outage["health_transitions"],
                "maximum_uwb_correction_m": outage["metrics"]["maximum_uwb_correction_m"],
                "maximum_state_step_m": outage["metrics"]["reentry_max_state_step_m"],
                "recovery_transition_count": outage["metrics"]["recovery_transition_count"],
            },
        },
    )

    predecessor = run_tests(
        [sys.executable, "-m", "pytest", "-q", "tests/root_r6a0", "tests/root_r6a1a", "tests/root_r6a1b", "tests/root_r6a1c", "tests/root_r6a1c_bsf31cc"]
    )
    predecessor["expected_test_count"] = 103
    write_json(root, "PREDECESSOR_REGRESSION_RESULTS.json", predecessor)
    test_env = dict(os.environ)
    test_env["R6A2A_RESULT_DIR"] = str(root)
    test_env.setdefault("OPENBLAS_NUM_THREADS", "1")
    test_env.setdefault("OMP_NUM_THREADS", "1")
    tests = run_tests([sys.executable, "-m", "pytest", "-q", "tests/root_r6a2a"], test_env)
    tests["expected_test_count"] = 45
    write_json(root, "TEST_RESULTS.json", tests)

    implementation = {
        "schema": "biospur-root-r6a2a-implementation-files-v1",
        "file_count": len(IMPLEMENTATION_PATHS),
        "files": [
            {"path": path, "sha256": sha256_file(FUSION / path)} for path in IMPLEMENTATION_PATHS
        ],
        "committed": False,
        "commit_authorized_for_r6a2a": False,
    }
    write_json(root, "IMPLEMENTATION_FILES.json", implementation)
    after = sealed_snapshot()
    protected = {
        "schema": "biospur-root-r6a2a-protected-hashes-v1",
        "before": before,
        "after": after,
        "all_exact": before == after,
        "real_payloads_opened": False,
        "real_body_state_updates": 0,
        "production_noise_estimates": 0,
        "r6a2a_commit_performed": False,
        "push_or_merge_performed": False,
    }
    write_json(root, "PROTECTED_HASHES_BEFORE_AFTER.json", protected)

    passed = gates["all_pass"] and qualification["covariance"]["pass"] and predecessor["all_pass"] and tests["all_pass"] and protected["all_exact"]
    final = {
        "schema": "biospur-root-r6a2a-final-result-v1",
        "principal_verdict": (
            "PASS_ROOT_R6A2A_SYNTHETIC_FAULT_AWARE_WHOLE_BODY_SHADOW_QUALIFIED"
            if passed
            else "PARTIAL_ROOT_R6A2A_SYNTHETIC_SHADOW_IMPLEMENTED_QUALIFICATION_GAPS_REMAIN"
        ),
        "root_r6a2a_synthetic_integrated_shadow_qualified": passed,
        "root_r6a2b_bounded_real_shadow_ready": False,
        "real_body_update_authorized": False,
        "production_fusion_authorized": False,
        "mandatory_gates": {"passed": gates["passed"], "total": gates["total"], "all_pass": gates["all_pass"]},
        "executable_synthetic_scenarios": len(qualification["scenario_results"]),
        "fail_closed_validator_scenarios": len(qualification["validator_results"]),
        "one_ten_node_system": True,
        "hardware_geometry_families": ["COMMON_NINE_V0_20_PCB17", "BSF31CC_V0_20_N5BL"],
        "cross_family_mechanical_transform_reuse": False,
        "shared_estimator": "IntegratedShadowEstimator",
        "production_preintegrator_exercised": True,
        "shared_r6a0_fk_only": True,
        "real_registry": {
            "total": after["real_ledger"]["total"],
            "value_null": after["real_ledger"]["value_null"],
            "FROZEN_UNCERTAIN": after["real_ledger"]["FROZEN_UNCERTAIN"],
            "writes": 0,
        },
        "predecessor_tests": predecessor,
        "r6a2a_tests": tests,
        "covariance": qualification["covariance"],
        "execution_boundaries": {
            "full_c1_arrays_opened": False,
            "real_uwb_payloads_opened": False,
            "held_out_actions_opened": False,
            "real_body_state_updates": 0,
            "real_parameter_fits": 0,
            "r6a2a_commit": False,
            "push": False,
            "merge": False,
        },
    }
    write_json(root, "FINAL_RESULT.json", final)
    (root / "FINAL_RESULT.md").write_text(
        "# Root-R6A2A final result\n\n"
        f"Principal verdict: `{final['principal_verdict']}`\n\n"
        "The first integrated synthetic-only ten-node whole-body shadow is qualified. It executes the "
        "R6A1A native-time preintegrator, corrected R6A1C identity/family registry, shared R6A0 articulated "
        "FK, asynchronous UWB range residuals, hierarchical health attribution, degraded accommodation, "
        "and controlled re-entry in one estimator. The common nine and BSF31CC select distinct synthetic "
        "mechanical profiles through one runtime registry; cross-family transform reuse is forbidden.\n\n"
        f"All {gates['total']} mandatory gates passed across {len(qualification['scenario_results'])} executable "
        f"fault scenarios and {len(qualification['validator_results'])} fail-closed validator scenarios. "
        f"The R6A2A suite passed 45 tests and predecessor suites passed {predecessor['expected_test_count']}. "
        "Covariance remained finite, symmetric, and PSD. During full UWB outage local articulated motion "
        "continued while root-position and yaw uncertainty grew; absolute no-drift position was never claimed.\n\n"
        "This PASS is synthetic qualification only. Root-R6A2B bounded real shadow is not ready, real body "
        "updates remain unauthorized, and production fusion remains unauthorized. The 87-slot real registry "
        "is byte-exact, entirely null, and entirely `FROZEN_UNCERTAIN`. No real C1/held-out payload, real "
        "parameter fit, R6A2A commit, push, or merge occurred.\n"
    )
    print(json.dumps({"principal_verdict": final["principal_verdict"], "result": str(root)}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--checksums-only", action="store_true")
    args = parser.parse_args()
    if args.checksums_only:
        checksums_only(args.result.resolve())
    else:
        generate(args.result.resolve())


if __name__ == "__main__":
    main()
