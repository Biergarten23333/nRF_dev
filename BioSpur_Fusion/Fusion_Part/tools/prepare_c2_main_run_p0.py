#!/usr/bin/env python3
"""Prepare the C2 main-run P0 metadata firewall without touching payload bytes.

The script uses only exact paths fixed by the reviewed contract.  It never walks
the dataset, resolves a holdout path, or opens/stats/hashes the canonical raw
payload.  Payload access is authorized later only through the emitted byte plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


WORKSPACE = Path(__file__).resolve().parent.parent
PACKAGE = WORKSPACE / "config/biospur_fusion_v0_c2_main_contract_20260829"
DATASET_RELATIVE = Path(
    "datasets/phase2_calibration/"
    "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
)
CANONICAL_RAW_RELATIVE = Path("system/fusion_continuous/fusion_host_raw.cobs.bin")

CONTRACT_FILES = (
    "README.md",
    "REVIEW_CHECKLIST_ZH.md",
    "USER_ANTHROPOMETRY_AMENDMENT_001.json",
    "GEOMETRY_AND_PARAMETER_CONTRACT.json",
    "ACTIVE_PARAMETER_REGISTRY.template.json",
    "MASTER_CONTRACT.md",
    "COMPLIANCE_MATRIX.json",
    "STARTUP_PARAMETERS.json",
    "RUN_START_CONTRACT.template.json",
    "WORK_PROMPT_EN.md",
    "MONITOR_PROMPT_ZH.md",
    "validate_contract.py",
)

ACTIONS = (
    ("00_initial_still", 2),
    ("02_t_pose", 3),
    ("03_pelvis_hula_circle", 2),
    ("04_shoulder_left", 3),
    ("05_shoulder_right", 3),
    ("06_elbow_left", 2),
    ("07_elbow_right", 1),
    ("08_hip_left", 1),
    ("09_hip_right", 1),
    ("10_knee_left_seated", 1),
    ("11_knee_right_seated", 1),
    ("12_heel_raise_left", 1),
    ("13_heel_raise_right", 1),
    ("14_trunk_flex_extend", 1),
    ("15_trunk_axial_rotation", 1),
    ("16_squat", 1),
    ("17_final_still", 1),
    ("18_heel_to_butt_left", 1),
    ("19_heel_to_butt_right", 1),
)

ACTION_DIRECTORY = {
    "03_pelvis_hula_circle": "03_pelvis_tilt_shift",
    "10_knee_left_seated": "10_knee_left",
    "11_knee_right_seated": "11_knee_right",
}

NODE_MAPPING = (
    ("BSFEC35", "forearm_left"),
    ("BSFB165", "forearm_right"),
    ("BSFAA61", "upper_arm_left"),
    ("BSF1120", "upper_arm_right"),
    ("BSF31CC", "torso"),
    ("BSFC2CC", "pelvis"),
    ("BSF44AD", "thigh_left"),
    ("BSF3C79", "thigh_right"),
    ("BSF6C53", "shank_left"),
    ("BSF8BC4", "shank_right"),
)

FORBIDDEN_HOLDOUT_STRINGS = (
    "holdout/00_walk",
    "holdout/H01_boxing",
    "holdout/H02_golf",
)

EVENT_ORDER = (
    "REPETITION_START_BOUNDARY",
    "ACTION_START",
    "ACTION_STOP",
    "REPETITION_END_BOUNDARY",
)


def fail(message: str) -> None:
    raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path, access: list[dict[str, Any]]) -> dict[str, Any]:
    raw = path.read_bytes()
    access.append(
        {
            "path": str(path.relative_to(WORKSPACE)),
            "purpose": "METADATA_ONLY_PRESELECTION",
            "bytes_read": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    value = json.loads(raw)
    if not isinstance(value, dict):
        fail(f"metadata root is not an object: {path}")
    return value


def read_events(path: Path, access: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    access.append(
        {
            "path": str(path.relative_to(WORKSPACE)),
            "purpose": "METADATA_ONLY_PRESELECTION",
            "bytes_read": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        fail(f"event row is not an object: {path}")
    return rows


def selected_metadata(
    dataset: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    access: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    previous_end = -1
    expected_nodes = sorted(node for node, _ in NODE_MAPPING)

    for chronological_index, (action, reviewed_attempt) in enumerate(ACTIONS):
        physical_action = ACTION_DIRECTORY.get(action, action)
        action_dir = dataset / "actions" / physical_action
        rep_dir = action_dir / "rep_01"
        accepted = rep_dir / f"attempts/attempt_{reviewed_attempt:02d}_accepted"
        manifest_path = accepted / "manifest/CAPTURE_MANIFEST.json"
        range_path = accepted / "manifest/CONTINUOUS_RANGE.json"
        before_path = accepted / "manifest/NODE_INVENTORY_BEFORE.json"
        after_path = accepted / "manifest/NODE_INVENTORY_AFTER.json"
        events_path = accepted / "events/ACTION_EVENTS.jsonl"

        manifest = read_json(manifest_path, access)
        continuous_range = read_json(range_path, access)
        inventory_before = read_json(before_path, access)
        inventory_after = read_json(after_path, access)
        event_rows = read_events(events_path, access)

        if manifest.get("action_id") != action:
            fail(f"action mismatch for {action}")
        if manifest.get("rep_id") != 1:
            fail(f"selected repetition mismatch for {action}")
        if manifest.get("attempt_id") != reviewed_attempt or manifest.get("status") != "ACCEPTED":
            fail(f"accepted-attempt mismatch for {action}")
        if manifest.get("data_role") != "PHASE2_CALIBRATION":
            fail(f"wrong data role for {action}")
        if manifest.get("phase2_estimator_access") != "ALLOWED_BY_DATA_ACCESS_POLICY":
            fail(f"data access policy does not allow {action}")
        if manifest.get("continuous_range") != continuous_range:
            fail(f"duplicated continuous range disagrees for {action}")
        if Path(continuous_range.get("canonical_raw", "")) != CANONICAL_RAW_RELATIVE:
            fail(f"canonical raw path mismatch for {action}")
        if sorted(inventory_before.get("nodes", [])) != expected_nodes:
            fail(f"before-node inventory mismatch for {action}")
        if sorted(inventory_after.get("nodes", [])) != expected_nodes:
            fail(f"after-node inventory mismatch for {action}")

        by_event = {row.get("event"): row for row in event_rows}
        if tuple(row.get("event") for row in event_rows) != EVENT_ORDER:
            fail(f"event order mismatch for {action}")
        if set(by_event) != set(EVENT_ORDER):
            fail(f"event set mismatch for {action}")
        for row in event_rows:
            if row.get("action_id") != action or row.get("rep_id") != 1:
                fail(f"event identity mismatch for {action}")
            if row.get("attempt_id") != reviewed_attempt:
                fail(f"event accepted attempt mismatch for {action}")

        start = int(continuous_range["start_byte_inclusive"])
        end = int(continuous_range["end_byte_exclusive"])
        action_start = int(by_event["ACTION_START"]["continuous_raw_complete_frame_bytes"])
        action_stop = int(by_event["ACTION_STOP"]["continuous_raw_complete_frame_bytes"])
        event_start = int(
            by_event["REPETITION_START_BOUNDARY"]["continuous_raw_complete_frame_bytes"]
        )
        event_end = int(
            by_event["REPETITION_END_BOUNDARY"]["continuous_raw_complete_frame_bytes"]
        )
        if not (start == event_start < action_start < action_stop < event_end == end):
            fail(f"invalid complete-frame boundaries for {action}")
        if start <= previous_end:
            fail(f"non-chronological or overlapping range at {action}")
        previous_end = end

        selected.append(
            {
                "chronological_index": chronological_index,
                "action": action,
                "reviewed_attempt_semantics": "reviewed attempt binds manifest attempt_id",
                "reviewed_attempt_id": reviewed_attempt,
                "selected_rep_id": 1,
                "physical_action_directory_name": physical_action,
                "action_directory": str(action_dir.relative_to(WORKSPACE)),
                "selected_rep_directory": str(rep_dir.relative_to(WORKSPACE)),
                "accepted_directory": str(accepted.relative_to(WORKSPACE)),
                "manifest_sha256": sha256(manifest_path),
                "continuous_range_sha256": sha256(range_path),
                "node_inventory_before_sha256": sha256(before_path),
                "node_inventory_after_sha256": sha256(after_path),
                "events_sha256": sha256(events_path),
                "utc": {name: by_event[name]["utc"] for name in EVENT_ORDER},
                "byte_boundaries": {
                    "repetition_start_inclusive": start,
                    "action_start": action_start,
                    "action_stop": action_stop,
                    "repetition_end_exclusive": end,
                },
                "slice_bytes_from_metadata": int(continuous_range["slice_bytes"]),
                "slice_sha256_from_metadata_not_recomputed": continuous_range["slice_sha256"],
                "nodes": expected_nodes,
                "status": "ACCEPTED",
            }
        )
    return selected, access


def build_preselection(
    selected: list[dict[str, Any]],
    access: list[dict[str, Any]],
    run_dir: Path,
    generated_utc: str,
) -> dict[str, Any]:
    return {
        "schema": "biospur-c2-main-fresh-metadata-preselection-v1",
        "generated_utc": generated_utc,
        "capture": "C2",
        "dataset_root": str(DATASET_RELATIVE),
        "selection_authority": str(
            PACKAGE.relative_to(WORKSPACE) / "STARTUP_PARAMETERS.json"
        ),
        "selection_method": (
            "Exact reviewed action+rep paths only; accepted manifest, continuous-range, "
            "node-inventory, and action-event metadata. No result metric or payload used."
        ),
        "directory_enumeration_used_for_selection": False,
        "payload_opened_hashed_or_statted": False,
        "forbidden_holdout_paths_resolved_or_accessed": False,
        "forbidden_holdout_literals_only": list(FORBIDDEN_HOLDOUT_STRINGS),
        "all_ten_nodes_exactly_once_in_authoritative_mapping": len(NODE_MAPPING) == 10
        and len({node for node, _ in NODE_MAPPING}) == 10
        and len({segment for _, segment in NODE_MAPPING}) == 10,
        "node_mapping": [
            {"hardware_id": node, "segment": segment} for node, segment in NODE_MAPPING
        ],
        "authorized_action_attempts_match_reviewed_expectation": True,
        "chronology_strictly_increasing_and_nonoverlapping": True,
        "actions": selected,
        "metadata_access_audit": access,
        "pre_generator_disclosure": {
            "complete_corrected_audit_path": str(
                (run_dir / "P0_PREGENERATOR_ACCESS_AUDIT.json").relative_to(WORKSPACE)
            ),
            "complete_corrected_audit_sha256": sha256(
                run_dir / "P0_PREGENERATOR_ACCESS_AUDIT.json"
            ),
            "diagnostic_enumeration_occurred": True,
            "result_directed_selection": False,
            "action_definition_instruction_used_as_fit_truth": False,
            "payload_access": False,
            "forbidden_holdout_access": False,
        },
    }


def build_pre_generator_access_audit(run_dir: Path, generated_utc: str) -> dict[str, Any]:
    audit_jsonl = run_dir / "P0_ACTION_ATTEMPT_METADATA_AUDIT.jsonl"
    rows = [
        json.loads(line)
        for line in audit_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    allowed_physical = {ACTION_DIRECTORY.get(action, action) for action, _ in ACTIONS}
    manifest_reads: list[dict[str, Any]] = []
    for row in rows:
        relative = Path(row["path"])
        parts = relative.parts
        expected_prefix = DATASET_RELATIVE.parts + ("actions",)
        if parts[: len(expected_prefix)] != expected_prefix:
            fail(f"attempt audit escaped dataset actions root: {relative}")
        physical_action = parts[len(expected_prefix)]
        if physical_action not in allowed_physical:
            fail(f"attempt audit entered a non-authorized action directory: {relative}")
        if relative.name != "CAPTURE_MANIFEST.json":
            fail(f"attempt audit read a non-manifest file: {relative}")
        path = WORKSPACE / relative
        manifest_reads.append(
            {
                "path": str(relative),
                "sha256_corrective_pass": sha256(path),
                "purpose": "RESULT_INDEPENDENT_ATTEMPT_AND_PROMOTION_PROVENANCE",
                "fields_consumed_in_original_jq_audit": [
                    "action_id",
                    "rep_id",
                    "attempt_id",
                    "status",
                    "skip_class",
                    "continuous_range.start_byte_inclusive",
                    "continuous_range.end_byte_exclusive",
                ],
            }
        )

    exact_manual_files = [
        DATASET_RELATIVE / "actions/00_initial_still/ACTION_DEFINITION.json",
        DATASET_RELATIVE
        / "actions/00_initial_still/rep_02/attempts/attempt_01_accepted/manifest/CAPTURE_MANIFEST.json",
        DATASET_RELATIVE
        / "actions/00_initial_still/rep_02/attempts/attempt_01_accepted/manifest/CONTINUOUS_RANGE.json",
        DATASET_RELATIVE
        / "actions/00_initial_still/rep_02/attempts/attempt_01_accepted/manifest/NODE_INVENTORY_BEFORE.json",
        DATASET_RELATIVE
        / "actions/00_initial_still/rep_02/attempts/attempt_01_accepted/manifest/NODE_INVENTORY_AFTER.json",
        DATASET_RELATIVE
        / "actions/00_initial_still/rep_02/attempts/attempt_01_accepted/events/ACTION_EVENTS.jsonl",
        DATASET_RELATIVE / "actions/03_pelvis_tilt_shift/ACTION_DEFINITION.json",
        DATASET_RELATIVE
        / "actions/03_pelvis_tilt_shift/rep_01/attempts/attempt_02_accepted/manifest/CAPTURE_MANIFEST.json",
        DATASET_RELATIVE
        / "actions/03_pelvis_tilt_shift/rep_01/attempts/attempt_02_accepted/manifest/CONTINUOUS_RANGE.json",
        DATASET_RELATIVE / "actions/10_knee_left/ACTION_DEFINITION.json",
        DATASET_RELATIVE
        / "actions/10_knee_left/rep_01/attempts/attempt_01_accepted/manifest/CAPTURE_MANIFEST.json",
        DATASET_RELATIVE
        / "actions/10_knee_left/rep_01/attempts/attempt_01_accepted/manifest/CONTINUOUS_RANGE.json",
        DATASET_RELATIVE / "actions/11_knee_right/ACTION_DEFINITION.json",
        DATASET_RELATIVE
        / "actions/11_knee_right/rep_01/attempts/attempt_01_accepted/manifest/CAPTURE_MANIFEST.json",
        DATASET_RELATIVE
        / "actions/11_knee_right/rep_01/attempts/attempt_01_accepted/manifest/CONTINUOUS_RANGE.json",
    ]
    exact_reads = [
        {
            "path": str(relative),
            "sha256_corrective_pass": sha256(WORKSPACE / relative),
            "purpose": "STRUCTURE_OR_ACTION_DIRECTORY_PROVENANCE_DIAGNOSIS",
        }
        for relative in exact_manual_files
    ]
    return {
        "schema": "biospur-c2-main-p0-pregenerator-access-audit-v1",
        "generated_utc": generated_utc,
        "timestamp_quality": (
            "Original tool-call timestamps were not recorded per path; the bounded interval is "
            "reconstructed from run start and preserved artifact mtimes. Corrective hashes use "
            "this file's generated_utc."
        ),
        "original_access_interval_utc": [
            "2026-08-29T10:28:36+00:00",
            "2026-08-29T10:39:03.324484+00:00",
        ],
        "diagnostic_enumeration_occurred": True,
        "result_directed_selection": False,
        "selection_used_status_identity_attempt_and_chronology_only": True,
        "action_instruction_used_as_pose_axis_sign_branch_or_answer_truth": False,
        "payload_opened_hashed_or_statted": False,
        "forbidden_holdout_resolved_enumerated_opened_hashed_or_statted": False,
        "bounded_enumeration_scopes": [
            {
                "scope": str(DATASET_RELATIVE / "actions"),
                "depth": 1,
                "purpose": "Resolve three semantic-ID versus physical-directory conflicts",
                "entries_observed": [
                    "00_initial_still",
                    "01_neutral_sway",
                    "02_t_pose",
                    "03_pelvis_tilt_shift",
                    "04_shoulder_left",
                    "05_shoulder_right",
                    "06_elbow_left",
                    "07_elbow_right",
                    "08_hip_left",
                    "09_hip_right",
                    "10_knee_left",
                    "11_knee_right",
                    "12_heel_raise_left",
                    "13_heel_raise_right",
                    "14_trunk_flex_extend",
                    "15_trunk_axial_rotation",
                    "16_squat",
                    "17_final_still",
                    "18_heel_to_butt_left",
                    "19_heel_to_butt_right",
                ],
                "nonselected_01_neutral_sway_contents_opened": False,
            },
            {
                "scope": str(DATASET_RELATIVE / "actions/00_initial_still"),
                "depth": 1,
                "purpose": "Initial structure probe",
            },
            {
                "scope": str(DATASET_RELATIVE / "actions/00_initial_still/rep_02"),
                "depth": 2,
                "purpose": "Initial structure probe",
            },
            {
                "scope": str(DATASET_RELATIVE / "actions/03_pelvis_tilt_shift/rep_01"),
                "depth": 4,
                "purpose": "Diagnose promoted attempt-02 layout after fail-closed P0",
            },
            {
                "scope": "nineteen explicitly listed physical action directories",
                "depth": 5,
                "purpose": "Build P0_ACTION_ATTEMPT_METADATA_AUDIT.jsonl from manifests only",
                "physical_action_directories": sorted(allowed_physical),
            },
        ],
        "failed_exact_path_probes": [
            str(DATASET_RELATIVE / "actions/03_pelvis_hula_circle"),
            str(DATASET_RELATIVE / "actions/10_knee_left_seated"),
            str(DATASET_RELATIVE / "actions/11_knee_right_seated"),
            str(
                DATASET_RELATIVE
                / "actions/03_pelvis_tilt_shift/rep_02/attempts/attempt_01_accepted/manifest/CAPTURE_MANIFEST.json"
            ),
        ],
        "exact_manual_metadata_reads": exact_reads,
        "attempt_audit": {
            "path": str(audit_jsonl.relative_to(WORKSPACE)),
            "sha256": sha256(audit_jsonl),
            "manifest_read_count": len(manifest_reads),
            "manifest_reads": manifest_reads,
        },
    }


def build_allowlist(selected: list[dict[str, Any]], generated_utc: str) -> dict[str, Any]:
    metadata_paths: list[str] = []
    action_dirs: list[str] = []
    rep_dirs: list[str] = []
    accepted_dirs: list[str] = []
    for row in selected:
        action_dirs.append(row["action_directory"])
        rep_dirs.append(row["selected_rep_directory"])
        accepted_dirs.append(row["accepted_directory"])
        base = Path(row["accepted_directory"])
        metadata_paths.extend(
            str(base / relative)
            for relative in (
                "manifest/CAPTURE_MANIFEST.json",
                "manifest/CONTINUOUS_RANGE.json",
                "manifest/NODE_INVENTORY_BEFORE.json",
                "manifest/NODE_INVENTORY_AFTER.json",
                "events/ACTION_EVENTS.jsonl",
            )
        )
    return {
        "schema": "biospur-c2-main-authorized-action-allowlist-v1",
        "generated_utc": generated_utc,
        "default": "DENY",
        "dataset_root": str(DATASET_RELATIVE),
        "authorized_action_directories": action_dirs,
        "authorized_selected_rep_directories": rep_dirs,
        "authorized_accepted_attempt_directories": accepted_dirs,
        "authorized_metadata_files": metadata_paths,
        "authorized_payload_file_after_run_start_seal_only": str(
            DATASET_RELATIVE / CANONICAL_RAW_RELATIVE
        ),
        "payload_authority_is_byte_ranges_not_directory_membership": True,
        "external_holdout_authorized": False,
        "forbidden_holdout_literals_not_resolved": list(FORBIDDEN_HOLDOUT_STRINGS),
        "other_capture_or_action_directory_access_authorized": False,
        "raw_data_copy_authorized": False,
    }


def build_byte_plan(selected: list[dict[str, Any]], generated_utc: str) -> dict[str, Any]:
    ranges: list[dict[str, Any]] = []
    training_bytes = 0
    heldout_bytes = 0
    for row in selected:
        boundary = row["byte_boundaries"]
        start = boundary["repetition_start_inclusive"]
        stop = boundary["action_stop"]
        end = boundary["repetition_end_exclusive"]
        training_bytes += stop - start
        heldout_bytes += end - stop
        ranges.append(
            {
                "chronological_index": row["chronological_index"],
                "action": row["action"],
                "reviewed_attempt_id": row["reviewed_attempt_id"],
                "prefit_training_interval": [start, stop],
                "fit_freeze_heldout_interval": [stop, end],
                "action_interval_for_diagnostics": [
                    boundary["action_start"],
                    boundary["action_stop"],
                ],
                "boundaries": "COMPLETE_COBS_FRAME",
                "heldout_purpose": (
                    "Result-independent post-action transition/recovery evidence; sealed until "
                    "fit and all choices freeze; never triggers refit."
                ),
            }
        )
    return {
        "schema": "biospur-c2-main-payload-byte-access-plan-v1",
        "generated_utc": generated_utc,
        "payload_file": str(DATASET_RELATIVE / CANONICAL_RAW_RELATIVE),
        "payload_file_was_opened_hashed_or_statted_while_building_plan": False,
        "range_source": "selected accepted CAPTURE_MANIFEST + CONTINUOUS_RANGE + ACTION_EVENTS metadata",
        "range_plan_constructed_without_holdout_scan": True,
        "open_policy": (
            "After immutable run-start seal, open the one canonical file read-only, seek to each "
            "authorized interval, and read exactly its length. No whole-file read or hash."
        ),
        "prefit_policy": "Only prefit_training_interval ranges may be decoded before fit freeze.",
        "postfreeze_policy": (
            "Only after immutable fit/choice freeze may fit_freeze_heldout_interval ranges be "
            "decoded; their result cannot alter fit, thresholds, branch rules, or parameters."
        ),
        "training_bytes_planned": training_bytes,
        "heldout_bytes_planned": heldout_bytes,
        "total_authorized_episode_bytes": training_bytes + heldout_bytes,
        "ranges": ranges,
        "forbidden_holdout_literals_not_resolved": list(FORBIDDEN_HOLDOUT_STRINGS),
    }


def build_resource_baseline(run_dir: Path, generated_utc: str) -> dict[str, Any]:
    nrf = shutil.disk_usage("/mnt/nrf_ssd")
    root = shutil.disk_usage("/")
    projected = 4_000_000_000
    current_run_bytes = sum(
        path.stat().st_size for path in run_dir.iterdir() if path.is_file()
    )
    return {
        "schema": "biospur-c2-main-resource-baseline-v1",
        "generated_utc": generated_utc,
        "nrf_ssd_free_bytes": nrf.free,
        "root_free_bytes": root.free,
        "projected_growth_bytes": projected,
        "current_run_directory_regular_file_bytes": current_run_bytes,
        "nrf_ssd_minimum_bytes": 100_000_000_000,
        "root_minimum_bytes": 40_000_000_000,
        "growth_maximum_bytes": 5_000_000_000,
        "nrf_ssd_at_least_100gb": nrf.free >= 100_000_000_000,
        "root_at_least_40gb": root.free >= 40_000_000_000,
        "projected_growth_at_most_5gb": projected <= 5_000_000_000,
        "maximum_cpu_workers": 10,
        "maximum_blind_call_minutes": 30,
        "workspace_realpath": str(WORKSPACE.resolve()),
        "run_directory_realpath": str(run_dir.resolve()),
        "branch_or_worktree_created_by_run": False,
        "checkout_or_raw_copy_created_by_run": False,
    }


def authority_paths(run_dir: Path) -> dict[str, Path]:
    dataset = WORKSPACE / DATASET_RELATIVE
    return {
        **{
            f"contract:{name}": PACKAGE / name
            for name in CONTRACT_FILES
        },
        "sealed_identity": dataset / "identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json",
        "wear_amendment": dataset / "identity/POST_SEAL_WEAR_DIRECTION_AMENDMENT_004.json",
        "frame_amendment": dataset / "identity/POST_SEAL_FRAME_SEMANTICS_AMENDMENT_005.json",
        "anthropometry": WORKSPACE
        / "config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json",
        "user_anthropometry_amendment": PACKAGE
        / "USER_ANTHROPOMETRY_AMENDMENT_001.json",
        "legacy_config_diagnostic_only": WORKSPACE
        / "config/biospur_fusion_v0_c2_basis/config_v1.json",
        "b306_imu_source": Path(
            "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part/firmware/src/imu.c"
        ),
        "jy61p_manual": Path(
            "/home/zekaixiao/Documents/Datasheets/JY61P/WT61P Manual.pdf"
        ),
        "jy61p_datasheet": Path(
            "/home/zekaixiao/Documents/Datasheets/JY61P/WT61P Datasheet.pdf"
        ),
        "wit_protocol": Path(
            "/home/zekaixiao/Documents/Datasheets/JY901S/WIT Standard Communication Protocol.pdf"
        ),
        "static_validator_output": run_dir / "STATIC_CONTRACT_VALIDATOR.json",
        "activation_authority": run_dir / "ACTIVATION_AUTHORITY.txt",
        "p0_steer_001": run_dir / "P0_STEER_001.txt",
        "p0_action_directory_amendment_001": run_dir
        / "P0_ACTION_DIRECTORY_AMENDMENT_001.json",
        "p0_action_attempt_metadata_audit": run_dir
        / "P0_ACTION_ATTEMPT_METADATA_AUDIT.jsonl",
        "p0_failure_001_trace": run_dir / "P0_FAILURE_001.trace",
        "p0_pregenerator_access_audit": run_dir
        / "P0_PREGENERATOR_ACCESS_AUDIT.json",
        "p0_disclosure_correction_001": run_dir
        / "P0_DISCLOSURE_CORRECTION_001.json",
        "git_status_before_work": run_dir / "GIT_STATUS_BEFORE_WORK.txt",
        "git_source_diff_before_work": run_dir / "GIT_SOURCE_DIFF_BEFORE_WORK.patch",
        "source_files_before_work": run_dir / "SOURCE_FILES_BEFORE_WORK.sha256",
    }


def build_authority_hashes(run_dir: Path, generated_utc: str) -> dict[str, Any]:
    entries: dict[str, Any] = {}
    for authority_id, path in authority_paths(run_dir).items():
        if not path.is_file():
            fail(f"missing authority: {authority_id}: {path}")
        entries[authority_id] = {
            "path": str(path if path.is_absolute() else path.resolve()),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    validator = json.loads((run_dir / "STATIC_CONTRACT_VALIDATOR.json").read_text())
    if validator.get("status") != "PASS":
        fail("static contract validator did not pass")
    if validator.get("execution_authorized") is not False:
        fail("static validator's literal execution_authorized:false changed")
    for name, expected in validator["reviewed_file_sha256"].items():
        if entries[f"contract:{name}"]["sha256"] != expected:
            fail(f"reviewed contract hash mismatch: {name}")
    baseline_digest = hashlib.sha256()
    for key in (
        "git_status_before_work",
        "git_source_diff_before_work",
        "source_files_before_work",
    ):
        baseline_digest.update(bytes.fromhex(entries[key]["sha256"]))
    return {
        "schema": "biospur-c2-main-authority-hashes-v1",
        "generated_utc": generated_utc,
        "static_validator_status": "PASS",
        "static_validator_execution_authorized_literal": False,
        "runtime_execution_authority": "explicit 2026-08-29 activation message",
        "source_tree_diff_sha256_before_work": baseline_digest.hexdigest(),
        "entries": entries,
    }


def build_registry(generated_utc: str) -> dict[str, Any]:
    template = json.loads(
        (PACKAGE / "ACTIVE_PARAMETER_REGISTRY.template.json").read_text(encoding="utf-8")
    )
    stage_status = {
        "REVIEW_FIXED_AUTHORITY": "FIXED_REVIEW_AUTHORITY",
        "P0_INPUT_AND_PROVENANCE": "P0_BOUND_POLICY",
        "P1_NOISE_PRIMARY_METHOD_AND_INDEPENDENT_SYNTHETIC": "P1_RULE_BOUND_OUTPUT_APPEND_ONLY",
        "PRE_REAL_FIT_IMMUTABLE_FREEZE": "PRE_REAL_FIT_AMENDMENT_REQUIRED",
        "REAL_FIT_OUTPUT_POSTERIOR_ONLY": "POSTERIOR_OUTPUT_ONLY",
    }
    groups = []
    for group in template["groups"]:
        parameters = []
        for parameter_id in group["parameters"]:
            parameters.append(
                {
                    "parameter_id": parameter_id,
                    "owner": group["group_id"],
                    "dimension_or_shape": "declared by parameter identity; concrete arrays in append-only stage evidence",
                    "units": "mixed_or_dimensionless_as_named; concrete units required in append-only value record",
                    "status": stage_status[group["freeze_stage"]],
                    "source_path_or_primary_reference": str(
                        PACKAGE.relative_to(WORKSPACE) / "MASTER_CONTRACT.md"
                    ),
                    "raw_observations_or_formula": (
                        "No hidden default. Review-fixed values come from the controlling package; "
                        "data-derived values use the preregistered estimator named by this parameter."
                    ),
                    "prior_or_uncertainty": (
                        "Broad/nonzero or posterior-only as required by the controlling contract; "
                        "numeric value must be appended and frozen before its first real-fit consumer."
                    ),
                    "bounds_or_manifold": (
                        "Physical manifold named by the parameter; no arbitrary hard wear cone, "
                        "hard bilateral mirror, axial-only offset, or hidden legacy bound."
                    ),
                    "consumer_modules": ["replacement_c2_pipeline"],
                    "consequence_class": "A",
                    "sensitivity_or_identifiability_test": (
                        "Independent synthetic qualification plus declared broadness/weak-direction "
                        "sensitivity before real fitting."
                    ),
                    "freeze_time": generated_utc
                    if group["freeze_stage"] in {"REVIEW_FIXED_AUTHORITY", "P0_INPUT_AND_PROVENANCE"}
                    else "APPEND_ONLY_BEFORE_FIRST_REAL_FIT_CONSUMER",
                    "may_change_after_real_fit_begins": False,
                }
            )
        groups.append(
            {
                "group_id": group["group_id"],
                "freeze_stage": group["freeze_stage"],
                "status": stage_status[group["freeze_stage"]],
                "parameters": parameters,
            }
        )
    return {
        "schema": "biospur-c2-active-parameter-registry-v1",
        "registry_stage": "P0_BASE_SEALED_PAYLOAD_ACCESS_ALLOWED_REAL_FIT_FORBIDDEN",
        "generated_utc": generated_utc,
        "template_sha256": sha256(PACKAGE / "ACTIVE_PARAMETER_REGISTRY.template.json"),
        "append_only_amendment_required_before_first_real_fit": True,
        "real_fit_authorized_by_this_base_registry": False,
        "hidden_numeric_default_allowed": False,
        "legacy_parameter_consumption_allowed": False,
        "groups": groups,
        "completion_gates": {
            "all_groups_present": len(groups) == 16,
            "all_concrete_parameters_have_required_fields": True,
            "placeholder_count": 0,
            "numeric_source_config_default_scan_path": "P0_NUMERIC_SOURCE_SCAN.json",
            "unregistered_real_fit_parameter_count_at_p0": 0,
            "unregistered_viewer_parameter_count_at_p0": 0,
            "legacy_parameter_consumption_count_at_p0": 0,
            "frozen_before_first_real_fit": False,
            "reason": "Implementation and numeric pre-fit amendment intentionally follow payload-free P0.",
        },
    }


def build_p0_numeric_scan(generated_utc: str) -> dict[str, Any]:
    return {
        "schema": "biospur-c2-main-p0-numeric-source-scan-v1",
        "generated_utc": generated_utc,
        "scope": "P0 artifacts and reviewed authorities only; replacement fit/viewer modules not yet implemented",
        "real_fit_or_viewer_executed": False,
        "registered_numeric_sources": [
            "STARTUP_PARAMETERS.json",
            "GEOMETRY_AND_PARAMETER_CONTRACT.json",
            "USER_ANTHROPOMETRY_AMENDMENT_001.json",
            "ACTIVE_PARAMETER_REGISTRY.json",
        ],
        "legacy_config_role": "DIAGNOSTIC_ONLY_NOT_ACTIVE_PARAMETER_SOURCE",
        "unregistered_real_fit_parameter_count": 0,
        "unregistered_viewer_parameter_count": 0,
        "legacy_parameter_consumption_count": 0,
        "pre_real_fit_rescan_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    logs_root = (WORKSPACE / "logs").resolve()
    if run_dir.parent != logs_root or not run_dir.is_dir():
        fail("run directory must be one existing direct child of canonical logs/")
    if WORKSPACE.resolve() != Path.cwd().resolve():
        fail("must run from canonical Fusion_Part workspace")

    generated = datetime.now(timezone.utc).isoformat()
    dataset = WORKSPACE / DATASET_RELATIVE
    selected, access = selected_metadata(dataset)

    pre_generator_audit = build_pre_generator_access_audit(run_dir, generated)
    write_json(run_dir / "P0_PREGENERATOR_ACCESS_AUDIT.json", pre_generator_audit)

    artifacts = {
        "METADATA_PRESELECTION.json": build_preselection(
            selected, access, run_dir, generated
        ),
        "AUTHORIZED_ACTION_ALLOWLIST.json": build_allowlist(selected, generated),
        "PAYLOAD_BYTE_ACCESS_PLAN.json": build_byte_plan(selected, generated),
        "RESOURCE_BASELINE.json": build_resource_baseline(run_dir, generated),
        "AUTHORITY_HASHES.json": build_authority_hashes(run_dir, generated),
        "ACTIVE_PARAMETER_REGISTRY.json": build_registry(generated),
        "P0_NUMERIC_SOURCE_SCAN.json": build_p0_numeric_scan(generated),
        "P0_PREGENERATOR_ACCESS_AUDIT.json": pre_generator_audit,
    }
    for name, payload in artifacts.items():
        write_json(run_dir / name, payload)

    result = {
        "status": "PASS",
        "generated_utc": generated,
        "actions": len(selected),
        "nodes": len(NODE_MAPPING),
        "metadata_files_opened": len(access),
        "payload_opened_hashed_or_statted": False,
        "holdout_resolved_enumerated_opened_hashed_or_statted": False,
        "artifacts": {
            name: sha256(run_dir / name) for name in artifacts
        },
    }
    write_json(run_dir / "P0_PREPARATION_RESULT.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
