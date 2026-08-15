#!/usr/bin/env python3
"""Audit S2 Attempt A without opening any real or held-out payload."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
S0 = ROOT / "Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_imu_only_multi_action_centerline_calibration_v1_s0_s1_structural_repair/S0_S1_RESULT.json"
ATTEMPT = ROOT / "Fusion_Part/logs/imu_only_multi_action_centerline_calibration_v1_s2_synthetic"
REPLAY2 = ROOT / "Fusion_Part/logs/imu_only_multi_action_centerline_calibration_v1_s2_synthetic_replay2"
CONFIG = ROOT / "Fusion_Part/config/imu_only_multi_action_centerline_calibration_v1_s2"
OUTPUT = ROOT / "Fusion_Part/logs/imu_only_multi_action_centerline_calibration_v1_s2_attempt_a_audit"
SOURCE = ROOT / "Fusion_Part/src/biospur_fusion/imu_multi_action_s2"
RUNNER = ROOT / "Fusion_Part/tools/run_multi_action_calibration_s2_human_observability.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name: str, value: dict) -> None:
    (OUTPUT / name).write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n")


def directory_hashes(directory: Path) -> dict[str, str]:
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(directory.rglob("*")) if path.is_file()}


def s2_target(block: str, column_offset: int) -> tuple[str, str, str]:
    if block.startswith("hinge:"):
        return (block.replace("hinge:", "functional:"), "REPARAMETERIZED",
                "Same unit-vector tangent coordinate, renamed as a non-clinical best-fit functional axis.")
    if block.startswith(("frame:", "axis:", "heading:")):
        return (block, "RETAINED",
                "Same static physical coordinate family; S2 uses a new observation-derived base point.")
    if block == "zeros":
        return ("NONE", "DELETED",
                "Joint zero/sign state was omitted from S2 even though the product contract lists non-clinical zero conventions.")
    if block.startswith("yaw_delta:"):
        return ("NONE", "DELETED",
                "Dynamic yaw-spline state was replaced by fixed Q2 orientation samples; no Schur elimination or equivalent dynamic-state proof exists.")
    return ("NONE", "DELETED", "No audited S2 mapping.")


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    s0 = json.loads(S0.read_text())
    rows = []
    for block_row in s0["repair_before_after"]["before"]["parameter_table"]:
        block = block_row["block"]
        for offset, column in enumerate(block_row["columns"]):
            target, status, reason = s2_target(block, offset)
            affects_output = block.startswith(("frame:", "axis:", "heading:", "yaw_delta:")) or block == "zeros"
            rows.append({
                "s0_column": int(column),
                "s0_parameter_name": f"{block}:{offset}",
                "s0_block": block,
                "unit": block_row["unit"],
                "s2_correspondence": target,
                "classification": status,
                "mathematical_transform": ("identity/tangent-base change" if status in ("RETAINED", "REPARAMETERIZED") else "NONE"),
                "participates_in_s2_residual": status in ("RETAINED", "REPARAMETERIZED"),
                "participates_in_nuisance_profile": status in ("RETAINED", "REPARAMETERIZED"),
                "affects_publishable_output": affects_output,
                "physical_reason": reason,
            })
    assert len(rows) == 371 and [row["s0_column"] for row in rows] == list(range(371))
    deleted_required = [row["s0_column"] for row in rows if row["classification"] == "DELETED" and row["affects_publishable_output"]]
    write("S0_TO_S2_PARAMETER_CROSSWALK.json", {
        "schema": "biospur-s0-to-s2-parameter-crosswalk-v1",
        "s0_parameter_count": 371,
        "s2_candidate_parameter_count": 55,
        "all_s0_columns_accounted": True,
        "classifications": ["RETAINED", "REPARAMETERIZED", "ANALYTICALLY_COMPOSED", "SCHUR_PROFILED_NUISANCE", "LEGITIMATE_GAUGE_REMOVED", "PRODUCT_OUTPUT_REMOVED", "FIXED", "DELETED"],
        "rows": rows,
        "s2_added_blocks": [
            "functional:hip_L:parent", "functional:hip_L:child",
            "functional:hip_R:parent", "functional:hip_R:child",
        ],
        "deleted_required_s0_columns": deleted_required,
        "verdict": "FAIL_REDUCED_STATE_INVALID",
    })
    write("FULL_AND_PROFILED_STATE_INVENTORY.json", {
        "schema": "biospur-s2-full-profiled-state-inventory-v1",
        "verdict": "FAIL_REDUCED_STATE_INVALID",
        "full_J_shape": None,
        "full_J_status": "NOT_CONSTRUCTED",
        "reduced_candidate_J_shape": [38400, 55],
        "profiled_J_shape": None,
        "profiled_J_status": "NOT_A_VALIDATED_SCHUR_COMPLEMENT",
        "profiled_parameter_blocks": [],
        "schur_or_pseudoinverse_tolerance": None,
        "remaining_gauges": ["common whole-body global yaw", "root translation"],
        "state_inventory": {
            "Theta_shared_static": "55 candidate coordinates present",
            "dynamic_trajectory_X_t": "ABSENT_FROM_OPTIMIZER; Q2 orientation arrays are treated as fixed observations",
            "gyro_bias": "MEDIAN_INITIAL_STILL_ESTIMATE_FIXED; not optimized/profiled",
            "accelerometer_bias": "MEDIAN_INITIAL_STILL_ESTIMATE_FIXED; not optimized/profiled",
            "lever_arms": "FIXED_FROM_GENERIC_TEMPLATE_FORMULAE; not optimized/profiled",
            "functional_frames": "elbow/knee/hip parent and child axes retained as static tangent coordinates",
            "initial_relative_heading": "nine static heading coordinates, pelvis coordinate fixed as display gauge",
            "joint_zero_and_sign": "S0 four zero coordinates deleted; no equivalent profile",
            "old_v_alpha_embedding": "frame:torso three-vector plus heading:torso scalar only",
            "old_v_alpha_profile_nuisance": "only the other 54 reduced static coordinates; no dynamic state, bias, lever-arm, or joint-zero nuisance",
        },
        "invalid_reductions": [
            "320 S0 yaw-spline dynamic coordinates deleted without analytic or Schur equivalence",
            "four joint-zero coordinates deleted",
            "dynamic pelvis/torso/limb SO(3), root/velocity, and bias nuisance required by the S2 contract are absent",
            "generic-template lever arms are fixed and match the synthetic generator geometry family",
        ],
        "scientific_observability_conclusion": "NOT_ESTABLISHED",
    })
    canonical = json.loads((ATTEMPT / "MULTISTART_STABILITY.json").read_text())
    write("ATTEMPT_A_DISPOSITION.json", {
        "schema": "biospur-s2-attempt-a-disposition-v1",
        "attempt_name": "S2_ATTEMPT_A_SOLVER_BUDGET_EXHAUSTED",
        "top_level_verdict": "FAIL_SYNTHETIC_RECOVERY",
        "primary_failure_reason": "SOLVER_NOT_CONVERGED",
        "recovery_evaluability": "NOT_ESTABLISHED",
        "scientific_observability_conclusion": "NOT_ESTABLISHED",
        "all_five_starts_success": canonical["all_converged"],
        "maximum_function_evaluations": 8,
        "nonconverged_output_metrics_are_descriptive_only": True,
        "replay2_interpretation": "THE_SAME_NONCONVERGED_FAILURE_IS_REPRODUCIBLE",
        "forbidden_inferences": ["SCIENTIFIC_RECOVERY_FAILS", "OLD_NULL_IS_REPAIRED", "OLD_NULL_REMAINS", "SYNTHETIC_ACTIONS_ARE_INSUFFICIENT"],
    })
    write("SOLVER_CONTRACT_AUDIT.json", {
        "schema": "biospur-s2-solver-contract-audit-v1",
        "canonical_runner": {
            "path": str(RUNNER),
            "config_path": str(CONFIG / "s2_gates_v1.json"),
            "max_nfev": 8,
            "result": "all five starts status=0; maximum evaluations exceeded",
        },
        "manual_diagnostic": {
            "reported_max_nfev": 60,
            "provenance": "OPERATOR_STEERING_TRANSCRIPT_ONLY",
            "persisted_code_path": None,
            "persisted_result_artifact": None,
            "audit_conclusion": "The interactive/debug contract was not persisted and is not canonical evidence. The mismatch was caused by an explicit ad-hoc solver argument rather than the versioned config.",
        },
        "hidden_contract_allowed": False,
        "revision_b_requirement": "one versioned solver contract shared by diagnostics and canonical runner",
    })
    write("ATTEMPT_A_SHA256.json", {
        "schema": "biospur-s2-attempt-a-sha256-v1",
        "config_files": directory_hashes(CONFIG),
        "source_files_current_post_attempt_a": directory_hashes(SOURCE) | {str(RUNNER.relative_to(ROOT)): digest(RUNNER)},
        "attempt_a_output_files": directory_hashes(ATTEMPT),
        "replay2_output_files": directory_hashes(REPLAY2),
        "source_provenance_warning": "Source was not snapshotted by Attempt A before later audit edits; these source hashes are post-attempt and must not be mislabelled as exact executed-source hashes.",
    })
    write("DATA_ACCESS_AUDIT.json", {
        "opened": [str(S0), str(ATTEMPT), str(REPLAY2), str(CONFIG), str(SOURCE), str(RUNNER)],
        "real_capture_payload_opened": False,
        "uwb_t4_opened": False,
        "operator_measurements_opened": False,
        "held_out_opened": False,
        "hardware_accessed": False,
    })
    print(json.dumps({"verdict": "FAIL_REDUCED_STATE_INVALID", "output": str(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
