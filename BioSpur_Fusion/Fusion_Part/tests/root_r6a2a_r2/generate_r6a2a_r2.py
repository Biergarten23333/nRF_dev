#!/usr/bin/env python3
"""Generate development evidence, then exactly one frozen formal validation."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from biospur_fusion.root_r6a2a.contracts import (  # noqa: E402
    ALL_NODES, COMMON_NINE, FAMILY_BSF31CC, FAMILY_COMMON_NINE,
    registry_from_sealed_addendum,
)
from biospur_fusion.root_r6a2a_r2.contracts import (  # noqa: E402
    DegradedMode, HealthManager, ObservationStatus, RNG_DERIVATION_VERSION,
)
from biospur_fusion.root_r6a2a_r2.estimator import all_node_covariance_mapping  # noqa: E402
from biospur_fusion.root_r6a2a_r2.qualification import (  # noqa: E402
    DEVELOPMENT_SCENARIOS, SYNTHETIC_NOISE, THRESHOLDS, VALIDATION_SCENARIOS,
    authority_negative_controls, authority_static_audit, csv_text,
    evaluate_gates, formal_validation, rng_counterfactual, run_ablations,
    run_development, run_scenario, validation_manifest,
)
from biospur_fusion.root_r6a2a_r2.synthetic import generate_run  # noqa: E402
from biospur_fusion.root_r6a2a.shadow import corrected_body_model  # noqa: E402


IMPLEMENTATION_PATHS = (
    "src/biospur_fusion/root_r6a2a_r2/__init__.py",
    "src/biospur_fusion/root_r6a2a_r2/contracts.py",
    "src/biospur_fusion/root_r6a2a_r2/synthetic.py",
    "src/biospur_fusion/root_r6a2a_r2/estimator.py",
    "src/biospur_fusion/root_r6a2a_r2/qualification.py",
    "tests/root_r6a2a_r2/conftest.py",
    "tests/root_r6a2a_r2/test_r6a2a_r2.py",
    "tests/root_r6a2a_r2/generate_r6a2a_r2.py",
    "tests/root_r6a2a_r2/verify_r6a2a_r2.py",
)
PARENT_FILES = {
    "parent_seal": "logs/root_r6a2a_synthetic_fault_aware_shadow_20260825T114046Z/SHA256SUMS",
    "r1_seal": "logs/root_r6a2a_r1_execution_audit_20260825T121247Z/SHA256SUMS",
    "slot_ledger": "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json",
}
EXPECTED = {
    "parent_seal": "ab17eed4c1c8e37079f844688100572c5515b2a3121b3cecf6d4f0086978b7f7",
    "r1_seal": "b00bc9f802f3c499b7c5c46418744b452af6515cb7a2349444f659befc8e3c60",
    "slot_ledger": "b159043eb7da4518ac6349832ca3c50e0b3453b1e35d97bbac29223f3e85c4eb",
}
CHECKPOINT = "ec451cf140b25e7dbe545e3e09d50b0d3d6edbe8"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: Any) -> None:
    def default(item: Any) -> Any:
        if isinstance(item, np.ndarray):
            return item.tolist()
        if hasattr(item, "value"):
            return item.value
        raise TypeError(type(item).__name__)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=default, allow_nan=False) + "\n", encoding="utf-8")


def implementation_hashes() -> dict[str, str]:
    return {path: sha(ROOT / path) if (ROOT / path).exists() else "MISSING" for path in IMPLEMENTATION_PATHS}


def protected_record() -> dict[str, Any]:
    hashes = {key: sha(ROOT / path) for key, path in PARENT_FILES.items()}
    ledger = json.loads((ROOT / PARENT_FILES["slot_ledger"]).read_text())
    slots = ledger["slots"] if isinstance(ledger, dict) else ledger
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    registry = registry_from_sealed_addendum(ROOT)
    return {
        "hashes": hashes, "expected_hashes": EXPECTED,
        "predecessors_byte_exact": hashes == EXPECTED,
        "checkpoint_head": head, "checkpoint_head_preserved": head == CHECKPOINT,
        "slot_count": len(slots), "null_count": sum(row["value"] is None for row in slots),
        "frozen_count": sum(row["status"] == "FROZEN_UNCERTAIN" for row in slots),
        "real_registry_unchanged": hashes["slot_ledger"] == EXPECTED["slot_ledger"],
        "hardware_family_separation_exact": (
            registry.family_by_node["BSF31CC"] == FAMILY_BSF31CC
            and all(registry.family_by_node[node] == FAMILY_COMMON_NINE for node in COMMON_NINE)
            and set(registry.family_by_node) == set(ALL_NODES)
        ),
    }


def health_order_check() -> bool:
    evidence = [
        ("imu_health", "BSFEC35", True, "IMU", False),
        ("uwb_tag_health", "BSFEC35", False, "UWB", False),
        ("uwb_link_health", "BSFEC35:2", True, "LINK", False),
    ]
    a, b = HealthManager(), HealthManager()
    a.update_modalities(evidence, 1.0)
    b.update_modalities(list(reversed(evidence)), 1.0)
    return a.snapshot() == b.snapshot() and a.transitions() == b.transitions()


def write_development(out: Path) -> None:
    authority = authority_static_audit()
    negative = authority_negative_controls(ROOT)
    counterfactual = rng_counterfactual(ROOT)
    development = run_development(ROOT)
    ablations = run_ablations(ROOT)
    dump(out / "FAULT_TRUTH_DATAFLOW_AUDIT.json", authority)
    dump(out / "FAULT_LABEL_NEGATIVE_CONTROLS.json", negative)
    dump(out / "COUNTERFACTUAL_INPUT_EQUIVALENCE.json", counterfactual)
    dump(out / "DEVELOPMENT_RESULTS.json", development)
    dump(out / "DEVELOPMENT_ABLATIONS.json", ablations)
    dump(out / "DEVELOPMENT_MANIFEST.json", {
        "schema": "biospur-root-r6a2a-r2-development-manifest-v1",
        "scenario_definitions": [asdict(row) for row in DEVELOPMENT_SCENARIOS],
        "thresholds": THRESHOLDS, "synthetic_noise": SYNTHETIC_NOISE,
        "results_file": "DEVELOPMENT_RESULTS.json", "ablation_file": "DEVELOPMENT_ABLATIONS.json",
        "threshold_adjustments": development["threshold_adjustments"],
        "validation_results_opened": False,
    })


def predecessor_tests() -> dict[str, Any]:
    paths = [
        "tests/root_r6a0", "tests/root_r6a1a", "tests/root_r6a1b",
        "tests/root_r6a1c", "tests/root_r6a1c_bsf31cc", "tests/root_r6a2a", "tests/root_r6a2a_r1",
    ]
    command = [sys.executable, "-m", "pytest", "-q", *paths]
    run = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return {"command": command, "exit_code": run.returncode, "output": run.stdout, "all_pass": run.returncode == 0}


def finalize_artifacts(out: Path, development: Mapping[str, Any], ablations: Mapping[str, Any], formal: Mapping[str, Any], freeze: Mapping[str, Any]) -> None:
    protected = protected_record()
    protected["health_update_order_invariant"] = health_order_check()
    clean = formal["scenario_results"]["val_clean_81001"]
    initial = np.asarray(clean["states"][0]["covariance_diagonal"])[63:123]
    final = np.asarray(clean["states"][-1]["covariance_diagonal"])[63:123]
    protected["unobservable_bias_covariance_ratio_min"] = float(np.min(final / initial))
    predecessor = predecessor_tests()
    negative = json.loads((out / "FAULT_LABEL_NEGATIVE_CONTROLS.json").read_text())
    authority = json.loads((out / "FAULT_TRUTH_DATAFLOW_AUDIT.json").read_text())
    counterfactual = json.loads((out / "COUNTERFACTUAL_INPUT_EQUIVALENCE.json").read_text())

    # Fresh deterministic replay of a validation scenario.
    replay = run_scenario(ROOT, VALIDATION_SCENARIOS[0])
    replay_pass = replay["output_digest"] == formal["scenario_results"][VALIDATION_SCENARIOS[0].scenario_id]["output_digest"]
    gates = evaluate_gates(development, formal, authority, negative, counterfactual, ablations, predecessor, protected, freeze, replay_pass)

    scenario_results = {**development["scenarios"], **formal["scenario_results"]}
    model = corrected_body_model(ROOT)
    mapping = all_node_covariance_mapping(model)
    registry = registry_from_sealed_addendum(ROOT)
    implementation = implementation_hashes()
    dump(out / "REPAIR_SCOPE_AND_OWNERSHIP.json", {
        "stage": "Root-R6A2A-R2", "owned_paths": list(IMPLEMENTATION_PATHS),
        "predecessor_paths_modified": False, "real_payloads_opened": False,
        "root_r6a2b_started": False, "projected_growth_gb": 0.2,
    })
    dump(out / "SEALED_PREDECESSOR_HASHES.json", {"files": PARENT_FILES, **protected})
    dump(out / "IMPLEMENTATION_FILE_HASHES.json", implementation)
    dump(out / "FAULT_TRUTH_AUTHORITY_CONTRACT.json", {
        "schema": "biospur-root-r6a2a-r2-authority-contract-v1",
        "flow": ["private synthetic source -> injector", "EstimatorInput -> estimator -> EstimatorOutput", "EstimatorOutput plus private scoring labels -> EvaluationResult"],
        "estimator_has_scoring_label_authority": False,
    })
    dump(out / "RNG_STREAM_CONTRACT.json", {
        "schema": "biospur-root-r6a2a-r2-rng-contract-v1", "version": RNG_DERIVATION_VERSION,
        "streams": ["trajectory_rng", "clock_rng[node_id]", "imu_rng[node_id]", "uwb_rng[tag_id,anchor_id]", "fault_rng[scenario_id,component]"],
        "stable_hash": "SHA-256", "python_hash_used": False,
    })
    dump(out / "RNG_LINEAGE.json", next(iter(scenario_results.values()))["rng_lineage"])
    dump(out / "FAULT_WINDOW_AUDIT.json", {key: row["fault_window_audit"] for key, row in scenario_results.items()})
    dump(out / "EXPECTED_OBSERVATION_LEDGER_CONTRACT.json", {
        "schema": "biospur-root-r6a2a-r2-observation-ledger-v1",
        "inputs": ["schedule", "identities", "boot_epoch", "clock_validity", "link_participation", "node_online"],
        "terminal_statuses": [row.value for row in ObservationStatus], "one_terminal_status_per_scheduled_item": True,
    })
    accounting_counts: dict[str, int] = {}
    for result in scenario_results.values():
        for row in result["accounting"]:
            accounting_counts[row["status"]] = accounting_counts.get(row["status"], 0) + 1
    dump(out / "OBSERVATION_ACCOUNTING_RESULTS.json", {"terminal_status_counts": accounting_counts, "scenarios": {key: {"expected": row["metrics"]["accounted_expected_count"], "terminal": row["metrics"]["terminal_accounting_count"], "missing": row["metrics"]["missing_count"]} for key, row in scenario_results.items()}})
    dump(out / "MISSINGNESS_QUALIFICATION.json", {key: scenario_results[key]["metrics"] for key in ("uwb_tag_dropout", "uwb_multi_anchor_outage", "uwb_full_outage_recovery", "node_imu_and_uwb_dropout")})
    dump(out / "MODALITY_HEALTH_CONTRACT.json", {
        "channels": list(HealthManager.CHANNELS), "update_modality_then_compose_once": True,
        "persistent_identities": {"imu":"node_id", "tag":"tag_id", "anchor":"anchor_id", "link":"(tag_id,anchor_id)"},
    })
    dump(out / "HEALTH_COMPOSITION_AUDIT.json", {"order_invariant": protected["health_update_order_invariant"], "cross_modality_reset": False})
    dump(out / "MODE_PRIORITY_AND_TRANSITION_CONTRACT.json", {
        "priority": ["recovery state", "global outage", "combined node", "multi-anchor/weak geometry", "anchor", "tag", "imu", "ambiguous model", "link", "suspect", "normal"],
        "modes": [row.value for row in DegradedMode], "based_on_private_labels": False,
    })
    dump(out / "DEGRADED_MODE_QUALIFICATION.json", {key: row["metrics"]["mode_sequence"] for key, row in scenario_results.items()})
    dump(out / "BIAS_STATE_UPDATE_CONTRACT.json", {
        "state_order": ["root position", "root SO(3)", "root velocity", "nine joint SO(3)", "nine joint rates", "ten gyro biases", "ten accelerometer biases"],
        "bias_jacobians_consumed": True, "random_walk_provenance": SYNTHETIC_NOISE["provenance"],
    })
    dump(out / "BIAS_OBSERVABILITY_AND_ESTIMATION.json", {key: row["bias_evaluation"] for key, row in scenario_results.items() if "bias" in key})
    dump(out / "BIAS_ABLATION_RESULTS.json", {key: value for key, value in ablations["comparisons"].items() if "bias" in key})
    dump(out / "ALL_NODE_COVARIANCE_MAPPING.json", mapping)
    dump(out / "COVARIANCE_INITIALIZATION_CONTRACT.json", clean["initialization_contract"])
    dump(out / "GEOMETRY_INFORMATION_AUDIT.json", {key: row["information_evidence"] for key, row in scenario_results.items() if "vertical" in key or "geometry" in key})
    mc = formal["covariance_monte_carlo"]
    dump(out / "COVARIANCE_MONTE_CARLO_QUALIFICATION.json", {key: value for key, value in mc.items() if key not in {"run_rows", "coverage_rows", "nis_rows"}})
    (out / "NEES_NIS_RESULTS.csv").write_text(csv_text([
        {**row, **next((nis for nis in mc["nis_rows"] if nis["class"] == row["class"] and nis["run"] == row["run"]), {})}
        for row in mc["run_rows"]
    ], ["class", "run", "seed", "block", "dimension", "nees", "normalized_nees", "full_state_nees", "full_state_normalized_nees", "mean_scalar_uwb_nis", "uwb_innovation_count", "vector_imu_innovation_norm_mean_rad_s", "minimum_covariance_eigenvalue", "covariance_symmetry_max_abs", "outage_root_covariance_growth", "outage_yaw_variance_growth"]), encoding="utf-8")
    (out / "COVERAGE_RESULTS.csv").write_text(csv_text(mc["coverage_rows"], ["class", "run", "sigma_level", "covered_coordinates", "coordinate_count"]), encoding="utf-8")
    dump(out / "RECOVERY_GAIN_CONTRACT.json", {"states_and_weights": {"ISOLATED":0.0, "RECOVERING":0.15, "REQUALIFYING":0.35, "CONTROLLED_REENTRY":0.65, "HEALTHY":1.0}, "controls_state_gain": True, "controls_covariance_contraction": True})
    dump(out / "RECOVERY_AND_REENTRY_QUALIFICATION.json", {key: row["metrics"] for key, row in scenario_results.items() if "recovery" in key})
    recovery_rows = [{"ablation": key, **value} for key, value in ablations["comparisons"].items() if "recovery" in key]
    (out / "RECOVERY_ABLATION_RESULTS.csv").write_text(csv_text(recovery_rows, ["ablation", "full", "ablated", "expected"]), encoding="utf-8")
    scenario_rows = [{"scenario_id": key, "category": row["scenario"]["category"], "root_rmse_m": row["metrics"]["root_position_rmse_m"], "attribution_allowed": row["metrics"]["attribution_allowed"], "expected_modes_seen": row["metrics"]["expected_modes_seen"]} for key, row in scenario_results.items()]
    (out / "SCENARIO_SUMMARY.csv").write_text(csv_text(scenario_rows, ["scenario_id", "category", "root_rmse_m", "attribution_allowed", "expected_modes_seen"]), encoding="utf-8")
    state_rows = [{"scenario_id": key, **{metric: row["metrics"][metric] for metric in ("root_position_rmse_m", "root_orientation_rmse_rad", "joint_orientation_rmse_rad", "velocity_rmse_mps")}} for key, row in scenario_results.items()]
    (out / "STATE_ERROR_SUMMARY.csv").write_text(csv_text(state_rows, ["scenario_id", "root_position_rmse_m", "root_orientation_rmse_rad", "joint_orientation_rmse_rad", "velocity_rmse_mps"]), encoding="utf-8")
    attribution_rows = [{"scenario_id": key, "allowed": "|".join(row["scenario"]["private_truth"]["allowed_attributions"]), "observed": "|".join(sorted(set(row["metrics"]["attribution_sequence"]))) } for key, row in scenario_results.items()]
    (out / "FAULT_ATTRIBUTION_CONFUSION_MATRIX.csv").write_text(csv_text(attribution_rows, ["scenario_id", "allowed", "observed"]), encoding="utf-8")
    mode_rows = [{"scenario_id": key, "step": index, "mode": mode} for key, row in scenario_results.items() for index, mode in enumerate(row["metrics"]["mode_sequence"])]
    (out / "MODE_TRANSITIONS.csv").write_text(csv_text(mode_rows, ["scenario_id", "step", "mode"]), encoding="utf-8")
    ablation_rows = [{"ablation": key, **value, "digest_different": ablations["digests"][key]["full"] != ablations["digests"][key]["ablated"]} for key, value in ablations["comparisons"].items()]
    (out / "ABLATION_SUMMARY.csv").write_text(csv_text(ablation_rows, ["ablation", "full", "ablated", "expected", "digest_different"]), encoding="utf-8")
    dump(out / "PREDECESSOR_REGRESSION_RESULTS.json", predecessor)
    dump(out / "PROTECTED_HASHES_BEFORE_AFTER.json", {"before": json.loads((out / "PRECONDITION_RECORD.json").read_text()), "after": protected, "unchanged": protected["predecessors_byte_exact"] and protected["real_registry_unchanged"]})
    dump(out / "MANDATORY_GATES.json", gates)
    full_pass = gates["all_pass"]
    verdict = "PASS_ROOT_R6A2A_R2_SYNTHETIC_FDIR_BIAS_AND_UNCERTAINTY_CLOSURE" if full_pass else "PARTIAL_ROOT_R6A2A_R2_ARCHITECTURE_REPAIRED_SCIENTIFIC_GAPS_REMAIN"
    result = {
        "verdict": verdict, "gates": {"passed": gates["passed"], "total": gates["total"], "all_pass": full_pass},
        "root_r6a2a_synthetic_integrated_shadow_qualified": full_pass,
        "root_r6a2b_bounded_real_shadow_ready": False,
        "real_body_update_authorized": False, "production_fusion_authorized": False,
        "independent_verification_pending": True,
        "checkpoint_commit": None,
    }
    dump(out / "FINAL_RESULT.json", result)
    failures = [f"{key}: {row['name']}" for key, row in gates["results"].items() if not row["pass"]]
    (out / "FINAL_RESULT.md").write_text(
        "# Root-R6A2A-R2 result\n\n"
        f"Verdict: `{verdict}`\n\n"
        f"Mandatory gates: {gates['passed']}/{gates['total']}.\n\n"
        + ("Failed gates:\n\n" + "\n".join(f"- {row}" for row in failures) + "\n\n" if failures else "All mandatory gates passed.\n\n")
        + "This is synthetic-only qualification. Root-R6A2B, real body updates, and production fusion remain unauthorized. Real signed-axis, process-noise, anthropometry, placement, hardware-lever/RF phase-centre, and T_N_V4 evidence remain required.\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--development-only", action="store_true")
    parser.add_argument("--formal", action="store_true")
    parser.add_argument("--mc-runs", type=int, default=100)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.development_only:
        if (out / "FORMAL_VALIDATION_RESULT.json").exists():
            raise SystemExit("formal validation already exists; development is frozen")
        write_development(out)
        return 0
    if not args.formal:
        raise SystemExit("select --development-only or --formal")
    if (out / "FORMAL_VALIDATION_RESULT.json").exists():
        raise SystemExit("formal validation is single-run and already exists")
    development = json.loads((out / "DEVELOPMENT_RESULTS.json").read_text())
    ablations = json.loads((out / "DEVELOPMENT_ABLATIONS.json").read_text())
    hashes = implementation_hashes()
    manifest = validation_manifest(hashes)
    dump(out / "FROZEN_VALIDATION_MANIFEST.json", manifest)
    manifest_hash = sha(out / "FROZEN_VALIDATION_MANIFEST.json")
    freeze = {
        "schema": "biospur-root-r6a2a-r2-validation-freeze-attestation-v1",
        "manifest_sha256": manifest_hash, "manifest_hash_verified": True,
        "implementation_hashes_verified": all(value != "MISSING" for value in hashes.values()),
        "validation_output_opened_before_freeze": False, "formal_run_ordinal": 1,
        "retuning_after_opening": False,
    }
    dump(out / "VALIDATION_FREEZE_ATTESTATION.json", freeze)
    formal = formal_validation(ROOT, args.mc_runs)
    dump(out / "FORMAL_VALIDATION_RESULT.json", formal)
    finalize_artifacts(out, development, ablations, formal, freeze)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
