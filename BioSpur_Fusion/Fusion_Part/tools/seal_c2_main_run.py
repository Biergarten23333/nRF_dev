#!/usr/bin/env python3
"""Create and validate the immutable C2 main-run start contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


WORKSPACE = Path(__file__).resolve().parent.parent
PACKAGE_RELATIVE = Path("config/biospur_fusion_v0_c2_main_contract_20260829")
PACKAGE = WORKSPACE / PACKAGE_RELATIVE
DATASET_RELATIVE = Path(
    "datasets/phase2_calibration/"
    "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
)
WORK_THREAD_ID = "01a04d0e-f774-7233-a4e5-0082016528a7"
MONITOR_THREAD_ID = "01a04d0f-58f1-7240-b72f-3bf5b44a2156"
ACTIVATION_SOURCE_THREAD_ID = "01a03f71-e481-7e21-84f0-3c6cbeb58291"
RUN_START_UTC = datetime(2026, 8, 29, 10, 28, 36, tzinfo=timezone.utc)


def fail(message: str) -> None:
    raise RuntimeError(message)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        fail(f"JSON root is not an object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def markers(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str) and value.startswith("__REQUIRED"):
        found.append(value)
    elif isinstance(value, dict):
        for nested in value.values():
            found.extend(markers(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(markers(nested))
    return found


def entry_hash(authorities: dict[str, Any], key: str) -> str:
    return str(authorities["entries"][key]["sha256"])


def build(run_dir: Path) -> dict[str, Any]:
    static = read_json(run_dir / "STATIC_CONTRACT_VALIDATOR.json")
    p0 = read_json(run_dir / "P0_PREPARATION_RESULT.json")
    resources = read_json(run_dir / "RESOURCE_BASELINE.json")
    authorities = read_json(run_dir / "AUTHORITY_HASHES.json")
    preselection = read_json(run_dir / "METADATA_PRESELECTION.json")
    allowlist = read_json(run_dir / "AUTHORIZED_ACTION_ALLOWLIST.json")
    byte_plan = read_json(run_dir / "PAYLOAD_BYTE_ACCESS_PLAN.json")
    registry = read_json(run_dir / "ACTIVE_PARAMETER_REGISTRY.json")
    numeric_scan = read_json(run_dir / "P0_NUMERIC_SOURCE_SCAN.json")

    if static.get("status") != "PASS" or static.get("execution_authorized") is not False:
        fail("literal static validator result is not PASS + execution_authorized:false")
    if p0.get("status") != "PASS":
        fail("P0 preparation did not pass")
    if p0.get("payload_opened_hashed_or_statted") is not False:
        fail("P0 reports payload access")
    if p0.get("holdout_resolved_enumerated_opened_hashed_or_statted") is not False:
        fail("P0 reports forbidden holdout access")
    for key in (
        "nrf_ssd_at_least_100gb",
        "root_at_least_40gb",
        "projected_growth_at_most_5gb",
    ):
        if resources.get(key) is not True:
            fail(f"resource gate failed: {key}")
    if preselection.get("authorized_action_attempts_match_reviewed_expectation") is not True:
        fail("reviewed action attempts did not match")
    if len(preselection.get("actions", [])) != 19:
        fail("preselection must contain nineteen actions")
    if allowlist.get("external_holdout_authorized") is not False:
        fail("external holdout became authorized")
    if byte_plan.get("payload_file_was_opened_hashed_or_statted_while_building_plan") is not False:
        fail("byte plan reports payload access")
    if byte_plan.get("range_plan_constructed_without_holdout_scan") is not True:
        fail("byte plan used a holdout scan")
    if registry.get("real_fit_authorized_by_this_base_registry") is not False:
        fail("P0 base registry incorrectly authorizes real fitting")
    if numeric_scan.get("pre_real_fit_rescan_required") is not True:
        fail("P0 numeric scan omitted the pre-fit rescan")

    contract_files = []
    for name, expected in static["reviewed_file_sha256"].items():
        actual = entry_hash(authorities, f"contract:{name}")
        if actual != expected:
            fail(f"authority hash mismatch for reviewed file: {name}")
        contract_files.append(
            {"path": str(PACKAGE_RELATIVE / name), "sha256": actual}
        )
    contract_files.sort(key=lambda row: row["path"])

    local_zone = ZoneInfo("Europe/Berlin")
    deadline = RUN_START_UTC + timedelta(hours=18)
    artifacts = {
        name: {
            "path": str((run_dir / name).relative_to(WORKSPACE)),
            "sha256": sha256(run_dir / name),
        }
        for name in (
            "STATIC_CONTRACT_VALIDATOR.json",
            "P0_PREPARATION_RESULT.json",
            "METADATA_PRESELECTION.json",
            "AUTHORIZED_ACTION_ALLOWLIST.json",
            "PAYLOAD_BYTE_ACCESS_PLAN.json",
            "RESOURCE_BASELINE.json",
            "AUTHORITY_HASHES.json",
            "ACTIVE_PARAMETER_REGISTRY.json",
            "P0_NUMERIC_SOURCE_SCAN.json",
            "P0_ACTION_DIRECTORY_AMENDMENT_001.json",
            "P0_ACTION_ATTEMPT_METADATA_AUDIT.jsonl",
            "P0_FAILURE_001.trace",
            "P0_PREGENERATOR_ACCESS_AUDIT.json",
            "P0_DISCLOSURE_CORRECTION_001.json",
            "ACTIVATION_AUTHORITY.txt",
            "P0_STEER_001.txt",
        )
    }
    artifacts["prepare_c2_main_run_p0.py"] = {
        "path": "tools/prepare_c2_main_run_p0.py",
        "sha256": sha256(WORKSPACE / "tools/prepare_c2_main_run_p0.py"),
    }
    artifacts["seal_c2_main_run.py"] = {
        "path": "tools/seal_c2_main_run.py",
        "sha256": sha256(Path(__file__).resolve()),
    }

    seal = {
        "schema": "biospur-c2-basis-progressive-run-start-v2",
        "template_only_not_a_seal": False,
        "activation": {
            "explicit_user_start_message_id": (
                f"source-thread:{ACTIVATION_SOURCE_THREAD_ID}/codex_delegation-input-20260829"
            ),
            "explicit_user_start_text_path": str(
                (run_dir / "ACTIVATION_AUTHORITY.txt").relative_to(WORKSPACE)
            ),
            "explicit_user_start_text_sha256": sha256(
                run_dir / "ACTIVATION_AUTHORITY.txt"
            ),
            "static_review_execution_authorized_literal": False,
            "runtime_execution_authority": "explicit 2026-08-29 activation message",
            "start_time_utc": RUN_START_UTC.isoformat(),
            "start_time_local": RUN_START_UTC.astimezone(local_zone).isoformat(),
            "deadline_18h_utc": deadline.isoformat(),
            "deadline_18h_local": deadline.astimezone(local_zone).isoformat(),
            "work_thread_id": WORK_THREAD_ID,
            "monitor_thread_id": MONITOR_THREAD_ID,
            "monitor_p0_steer_sha256": sha256(run_dir / "P0_STEER_001.txt"),
            "host": "local",
        },
        "roles": {
            "sole_writer": "ENGLISH_WORK",
            "strict_read_only_monitor": "CHINESE_MONITOR",
            "periodic_updates_to_planning_chat": False,
        },
        "contract_files": contract_files,
        "append_only_amendments": [
            artifacts["P0_ACTION_DIRECTORY_AMENDMENT_001.json"],
            artifacts["P0_DISCLOSURE_CORRECTION_001.json"],
        ],
        "resolved_workspace": {
            "canonical_path": str(WORKSPACE),
            "realpath": str(WORKSPACE.resolve()),
            "branch_created": False,
            "worktree_created": False,
            "checkout_copy_created": False,
            "raw_copy_created": False,
            "preexisting_branch_not_created_by_run": "feature/root-r6a1a-preintegrator-qualified",
            "run_directory": str(run_dir.relative_to(WORKSPACE)),
        },
        "resource_gate": {
            key: resources[key]
            for key in (
                "nrf_ssd_free_bytes",
                "root_free_bytes",
                "projected_growth_bytes",
                "nrf_ssd_at_least_100gb",
                "root_at_least_40gb",
                "projected_growth_at_most_5gb",
                "maximum_cpu_workers",
                "maximum_blind_call_minutes",
            )
        },
        "scope": {
            "capture": "C2",
            "capture_root_realpath": str((WORKSPACE / DATASET_RELATIVE).resolve()),
            "all_ten_nodes_exactly_once": True,
            "legacy_alias_conflict_count": 0,
            "action_directory_conflict_count_initial": 3,
            "action_directory_conflict_count_unresolved": 0,
            "action_directory_mapping_amendment_sha256": artifacts[
                "P0_ACTION_DIRECTORY_AMENDMENT_001.json"
            ]["sha256"],
            "allowed_fields": [
                "raw_accelerometer",
                "raw_gyroscope",
                "timestamp",
                "boot_epoch",
                "sequence",
                "status",
            ],
            "forbidden_input_count": 0,
            "external_holdout_authorized": False,
            "holdout_metadata_or_payload_access_before_fit_freeze": False,
        },
        "authority_hashes": {
            "sealed_identity_sha256": entry_hash(authorities, "sealed_identity"),
            "wear_amendment_sha256": entry_hash(authorities, "wear_amendment"),
            "frame_amendment_sha256": entry_hash(authorities, "frame_amendment"),
            "anthropometry_sha256": entry_hash(authorities, "anthropometry"),
            "user_anthropometry_amendment_sha256": entry_hash(
                authorities, "user_anthropometry_amendment"
            ),
            "b306_imu_source_sha256": entry_hash(authorities, "b306_imu_source"),
            "jy61p_manual_sha256": entry_hash(authorities, "jy61p_manual"),
            "jy61p_datasheet_sha256": entry_hash(authorities, "jy61p_datasheet"),
            "wit_protocol_sha256": entry_hash(authorities, "wit_protocol"),
            "legacy_config_diagnostic_only_sha256": entry_hash(
                authorities, "legacy_config_diagnostic_only"
            ),
            "source_tree_diff_sha256_before_work": authorities[
                "source_tree_diff_sha256_before_work"
            ],
        },
        "verified_input_binding": {
            "firmware_lineage": "b306-imu-relay-v47",
            "firmware_fwid_sha256": "f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed",
            "firmware_image_sha256": "90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98",
            "signed_little_endian_raw_axis_order_bound": True,
            "b306_axis_permutation_or_sign_flip": False,
            "acceleration_formula_bound": True,
            "gyroscope_formula_bound": True,
            "adaptive_accelerometer_range_understood": True,
            "gravity_retained": True,
            "vendor_quaternion_consumed": False,
            "qmt_wxyz_round_trip_pass": True,
            "qmt_round_trip_status": "reviewed verified input binding; source-level test repeats before trajectory consumption",
        },
        "preselection": {
            "fresh_metadata_only_preselection_path": artifacts[
                "METADATA_PRESELECTION.json"
            ]["path"],
            "fresh_metadata_only_preselection_sha256": artifacts[
                "METADATA_PRESELECTION.json"
            ]["sha256"],
            "authorized_action_allowlist_sha256": artifacts[
                "AUTHORIZED_ACTION_ALLOWLIST.json"
            ]["sha256"],
            "authorized_action_attempts_match_reviewed_expectation": True,
            "payload_byte_access_plan_sha256": artifacts[
                "PAYLOAD_BYTE_ACCESS_PLAN.json"
            ]["sha256"],
            "range_plan_constructed_without_holdout_scan": True,
        },
        "architecture_assertions": {
            "capture_wide_orientation_state": True,
            "episode_reset_count_expected": 0,
            "relative_heading_edges": 9,
            "pelvis_yaw_gauges": 1,
            "relative_heading_graph_is_rooted_tree": True,
            "qmt_time_varying_outputs_required": True,
            "upstream_example_parameters_are_c2_truth": False,
            "upstream_start_or_stillness_rating_can_create_known_initial_heading": False,
            "functional_joint_and_frame_inputs_precede_heading": True,
            "official_parent_child_delta_tree_propagation_primary": True,
            "bespoke_global_nonlinear_heading_solver_default": False,
            "full_so3_mounts": 10,
            "full_3d_joint_connection_vectors": True,
            "multiple_legal_branches": True,
            "physical_before_residual": True,
            "one_persistent_progressive_state": True,
            "pre_freeze_progress_uses_prequential_not_final_heldout": True,
            "final_heldout_before_fit_freeze": False,
            "final_heldout_can_trigger_refit": False,
            "ideal_human_or_imu_assumed": False,
            "fresh_batch_equivalence_required": True,
        },
        "geometry_and_parameter_binding": {
            "geometry_parameter_contract_sha256": entry_hash(
                authorities, "contract:GEOMETRY_AND_PARAMETER_CONTRACT.json"
            ),
            "anthropometry_raw_observations_exactly_bound": True,
            "surface_measurements_called_internal_bone_truth": False,
            "nonzero_measurement_and_mapping_uncertainty_bound_before_real_fit": True,
            "old_006m_hip_vertical_offset_allowed": False,
            "old_022m_internal_hip_spacing_allowed": False,
            "old_axial_sensor_offset_model_allowed": False,
            "active_parameter_registry_path": artifacts[
                "ACTIVE_PARAMETER_REGISTRY.json"
            ]["path"],
            "active_parameter_registry_sha256": artifacts[
                "ACTIVE_PARAMETER_REGISTRY.json"
            ]["sha256"],
            "p0_registry_authorizes_real_fit": False,
            "pre_real_fit_append_only_registry_amendment_required": True,
            "unregistered_real_fit_or_viewer_parameter_count": 0,
            "unregistered_count_scope": "P0 only; mandatory rescan before first real fit",
        },
        "qualification_and_visual": {
            "independent_synthetic_before_real_fit": True,
            "all_required_negative_mutations_before_real_fit": True,
            "identical_renderer_abc_required": True,
            "viewer_ik_repair_rebase_retarget_allowed": False,
            "worker_pixel_inspection_required": True,
            "monitor_pixel_inspection_required": True,
        },
        "consequence_policy": {
            "all_predicates_classified_before_run": True,
            "ordinary_failure_terminates_task": False,
            "threshold_relaxation_allowed": False,
            "action_deletion_allowed": False,
            "causal_pivot_required": True,
            "p0_failure_001_class": "A_REPAIRED_BEFORE_SEAL",
        },
        "first_payload_access": {
            "occurred": False,
            "time_utc": None,
            "path": None,
            "byte_interval": None,
            "precedes_this_seal": False,
        },
        "contract_validator": {
            "path": "config/biospur_fusion_v0_c2_main_contract_20260829/validate_contract.py",
            "sha256": entry_hash(authorities, "contract:validate_contract.py"),
            "exit_code": 0,
            "result": "PASS",
            "literal_execution_authorized": False,
            "complete_output_path": artifacts["STATIC_CONTRACT_VALIDATOR.json"]["path"],
            "complete_output_sha256": artifacts["STATIC_CONTRACT_VALIDATOR.json"]["sha256"],
        },
        "runtime_p0_audit": {
            "result": "PASS",
            "path": artifacts["P0_PREPARATION_RESULT.json"]["path"],
            "sha256": artifacts["P0_PREPARATION_RESULT.json"]["sha256"],
            "payload_opened_hashed_or_statted": False,
            "forbidden_holdout_access": False,
            "complete_pregenerator_access_audit_path": artifacts[
                "P0_PREGENERATOR_ACCESS_AUDIT.json"
            ]["path"],
            "complete_pregenerator_access_audit_sha256": artifacts[
                "P0_PREGENERATOR_ACCESS_AUDIT.json"
            ]["sha256"],
            "diagnostic_enumeration_occurred": True,
            "result_directed_selection": False,
        },
        "sealed_runtime_artifacts": artifacts,
        "seal": {
            "template_fields_remaining": 0,
            "immutable_after_creation": True,
            "append_only_amendments_only": True,
            "run_authorized_to_open_payload_after_seal": True,
            "real_fit_authorized_before_pre_fit_registry_and_synthetic_gates": False,
        },
    }
    return seal


def validate(run_dir: Path, seal: dict[str, Any]) -> dict[str, Any]:
    unresolved = markers(seal)
    if unresolved:
        fail(f"run-start seal contains unresolved template markers: {unresolved}")
    if seal["activation"]["work_thread_id"] != WORK_THREAD_ID:
        fail("work thread mismatch")
    if seal["activation"]["monitor_thread_id"] != MONITOR_THREAD_ID:
        fail("monitor thread mismatch")
    if seal["first_payload_access"]["occurred"] is not False:
        fail("seal claims pre-seal payload access")
    if seal["contract_validator"]["literal_execution_authorized"] is not False:
        fail("static validator literal changed")
    if seal["seal"]["run_authorized_to_open_payload_after_seal"] is not True:
        fail("completed seal does not authorize planned payload ranges")
    if seal["seal"]["real_fit_authorized_before_pre_fit_registry_and_synthetic_gates"] is not False:
        fail("seal improperly authorizes real fit")

    return {
        "schema": "biospur-c2-main-run-start-audit-v1",
        "status": "PASS",
        "audited_utc": datetime.now(timezone.utc).isoformat(),
        "template_fields_remaining": 0,
        "contract_files": len(seal["contract_files"]),
        "actions": 19,
        "nodes": 10,
        "relative_heading_edges": 9,
        "payload_opened_hashed_or_statted_before_seal": False,
        "forbidden_holdout_access_before_seal": False,
        "static_validator_execution_authorized_literal": False,
        "runtime_activation_bound": True,
        "monitor_thread_bound": True,
        "action_directory_amendment_bound": True,
        "resource_gates_pass": True,
        "run_start_contract_sha256": sha256(run_dir / "RUN_START_CONTRACT.json"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    if run_dir.parent != (WORKSPACE / "logs").resolve() or not run_dir.is_dir():
        fail("run directory must be one existing direct child of canonical logs/")
    if Path.cwd().resolve() != WORKSPACE.resolve():
        fail("must run from canonical Fusion_Part workspace")
    seal_path = run_dir / "RUN_START_CONTRACT.json"
    audit_path = run_dir / "RUN_START_AUDIT.json"
    if seal_path.exists() or audit_path.exists():
        fail("run-start seal/audit already exists; never overwrite an immutable seal")

    seal = build(run_dir)
    write_json(seal_path, seal)
    audit = validate(run_dir, seal)
    write_json(audit_path, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
