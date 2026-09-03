"""Execute and package one locked V0 validation from a raw COBS action."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from biospur_fusion.root_r6a2a.shadow import corrected_body_model

from .contracts import (
    IDENTITY, assert_profile_boundary, dump_json, load_config, load_release_mode,
    sha256_file,
)
from .raw_validation import load_raw_action_imu_only
from .validation import (
    _arrays_digest, _compare_variants, _execute_variants, _gross_motion_metrics,
    _uncertainty_localization, _variant_metrics, validate_predeclaration,
    verify_candidate_lock, write_checksums,
)
from .viewer import write_viewer


RELEASE_VARIANTS = (
    "qmt_off", "qmt_off_no_shared_ik",
    "always_on_qmt", "always_on_qmt_no_shared_ik",
)


def run_raw_development_action(root: Path, predeclaration_path: Path, output: Path) -> dict:
    """Execute the full V0 vertical slice on declared development-regression data."""
    root = Path(root).resolve(); output = Path(output).resolve()
    predeclaration_path = Path(predeclaration_path).resolve()
    predeclaration = json.loads(predeclaration_path.read_text(encoding="utf-8"))
    profile_path = Path(str(predeclaration["profile"])).resolve()
    profile_sha_before = sha256_file(profile_path)
    if profile_sha_before != predeclaration["profile_sha256"]:
        raise ValueError("frozen profile SHA-256 mismatch before development action")
    profile = json.loads(profile_path.read_text(encoding="utf-8")); assert_profile_boundary(profile)
    config = load_config(root / "config/biospur_fusion_v0/config.json")
    if profile.get("config_sha256") != config.sha256:
        raise ValueError("frozen profile and active configuration disagree")

    rows, access = load_raw_action_imu_only(predeclaration)
    variants, audits = _execute_variants(root, rows, profile, config)
    repeat, _ = _execute_variants(root, rows, profile, config)
    label = str(predeclaration["action_identifier"])
    for arrays in variants.values(): arrays["window"][:] = label
    for arrays in repeat.values(): arrays["window"][:] = label
    deterministic = {
        name: {
            "first_digest": _arrays_digest(arrays),
            "repeat_digest": _arrays_digest(repeat[name] | {"window": arrays["window"]}),
            "identical": _arrays_digest(arrays) == _arrays_digest(repeat[name] | {"window": arrays["window"]}),
        }
        for name, arrays in variants.items()
    }
    model = corrected_body_model(
        root, identity_mapping=profile["identity"],
        identity_provenance=(
            "PROFILE_IDENTITY:"
            f"{profile.get('capture_id', profile.get('profile_kind', 'LEGACY_V0'))}"
        ),
    )
    numerical = {
        "full": _variant_metrics(
            variants["full"], model, audits["full"]["shared_ik"]["canonical_fk_closure_max_abs"],
        ),
        "no_shared_ik": _variant_metrics(
            variants["no_shared_ik"], model,
            audits["no_shared_ik"]["canonical_fk_observation_closure_max_abs_rad"],
        ),
        "no_relative_heading": _variant_metrics(
            variants["no_relative_heading"], model,
            audits["no_relative_heading"]["shared_ik"]["canonical_fk_closure_max_abs"],
        ),
    }
    metrics = {
        "schema": "biospur-fusion-v0-development-action-metrics-v1",
        "expected_gross_motion_semantics": predeclaration["expected_gross_motion_semantics"],
        "numerical_integrity": numerical,
        "deterministic_replay": deterministic,
        "gross_motion": _gross_motion_metrics(variants["full"], model),
        "full_versus_no_shared_ik": _compare_variants(
            variants["full"], variants["no_shared_ik"],
        ),
        "full_versus_no_relative_heading": _compare_variants(
            variants["full"], variants["no_relative_heading"],
        ),
        "module_audits": audits,
        "access_trace": access,
        "threshold_policy": "NO_ACTION_SPECIFIC_PRODUCT_THRESHOLDS",
    }
    uncertainty = _uncertainty_localization(variants["full"], profile)
    np.savez_compressed(output / "SHOULDER_LEFT_STATE.npz", **variants["full"])
    dump_json(output / "SHOULDER_LEFT_METRICS.json", metrics)
    dump_json(output / "SHOULDER_LEFT_UNCERTAINTY_LOCALIZATION.json", uncertainty)
    dump_json(output / "ACTION_ISOLATION_AND_IDENTITY.json", access)
    component = {
        "schema": "biospur-fusion-v0-development-component-influence-v1",
        "relative_heading_executed": audits["full"]["relative_heading"]["relative_heading_executed"],
        "shared_ik_feedback_executed": audits["full"]["shared_ik"]["shared_ik_feedback_executed"],
        "full_versus_no_shared_ik": metrics["full_versus_no_shared_ik"],
        "full_versus_no_relative_heading": metrics["full_versus_no_relative_heading"],
    }
    dump_json(output / "COMPONENT_INFLUENCE.json", component)
    viewer = write_viewer(
        output / "SHOULDER_LEFT_VIEWER.html",
        time_ns=variants["full"]["global_time_ns"], window=variants["full"]["window"],
        boundary=variants["full"]["boundary"],
        segment_names=tuple(str(x) for x in variants["full"]["segment_names"]),
        segment_position=variants["full"]["segment_position"],
        segment_rotation=variants["full"]["segment_rotation"],
        segment_confidence=variants["full"]["segment_confidence"],
        joint_rotvec=variants["full"]["joint_rotvec"],
    )
    profile_sha_after = sha256_file(profile_path)
    profile_immutability = {
        "profile": str(profile_path), "sha256_before": profile_sha_before,
        "sha256_after": profile_sha_after,
        "byte_exact_unchanged": profile_sha_before == profile_sha_after,
        "runtime_state_written_back": False,
    }
    dump_json(output / "PROFILE_IMMUTABILITY_DEVELOPMENT.json", profile_immutability)
    repairs = {
        "schema": "biospur-fusion-v0-development-causal-repairs-v1",
        "failing_evidence_preserved": str(
            root / "logs/biospur_fusion_v0_independent_action_validation_20260826T183538Z"
        ),
        "repairs": [
            {
                "defect": "FULL_CONTAINER_HASH_AND_FULL_TIMING_SCAN_CROSSED_ACTION_BOUNDARY",
                "cause": "container identity verification and linear timing readers were unbounded",
                "general_correction": "import sealed container identity, hash/read exact action bytes, and binary-seek bounded timing logs",
                "action_specific_constant_added": False,
            },
            {
                "defect": "CAPTURE_IDENTITY_WAS_NOT_EXPLICIT",
                "cause": "logical slot was converted to an address by a universal prefix assumption",
                "general_correction": "join capture readiness node/tag/slot/DWM boot authority to bounded Listener LPD/LRD evidence with exactly-once validation",
                "action_specific_constant_added": False,
            },
            {
                "defect": "PUBLIC_SWEEP_WAS_FORCED_EQUAL_TO_ON_AIR_POLL_SEQUENCE",
                "cause": "independent counter origins were treated as identical",
                "general_correction": "derive the constant sequence offset after discrete epoch association; keep TIMER2/UWB Beacon as measurement time",
                "action_specific_constant_added": False,
            },
            {
                "defect": "ONE_TAG_HAD_NO_DIRECT_LISTENER_POLL_OBSERVATION",
                "cause": "bounded observer coverage was sparse for BSFC2CC",
                "general_correction": "require a unique fleet bijection and learn per-listener/per-anchor response timing offsets from same-window non-spatial timing metadata",
                "action_specific_constant_added": False,
            },
        ],
        "uwb_spatial_leakage": False,
    }
    dump_json(output / "DEVELOPMENT_CAUSAL_REPAIRS.json", repairs)
    integrity = bool(
        access["common_clock"]["gate"]["pass"]
        and access["decode"]["raw_access"]["all_reads_within_declared_action"]
        and access["access_sentinels"]["timing_io_instrumented_at_os_call_layer"]
        and access["access_sentinels"]["all_sequential_timing_rows_within_action_plus_two_superframes"]
        and access["access_sentinels"]["every_binary_search_probe_separately_accounted"]
        and access["access_sentinels"]["no_full_timing_file_traversal_proven"]
        and access["access_sentinels"]["golf_boxing_timing_interval_bytes_touched"] is False
        and all(row["finite"] and row["native_time_strictly_increasing"] for row in numerical.values())
        and all(row["identical"] for row in deterministic.values())
        and profile_immutability["byte_exact_unchanged"]
    )
    result = {
        "schema": "biospur-fusion-v0-development-action-result-v1",
        "full_vertical_slice_executed": True,
        "automatic_execution_integrity_pass": integrity,
        "action": label, "attempt_number": predeclaration["attempt_number"],
        "metrics": metrics, "uncertainty": uncertainty,
        "viewer": viewer, "viewer_qa": "PENDING_VISUAL_INSPECTION",
        "profile_immutability": profile_immutability,
        "HISTORICAL_GOLF_BOXING_CONTAINER_BYTES_TOUCHED": "YES",
        "CURRENT_GOAL_GOLF_BOXING_BYTES_TOUCHED": "NO",
        "GOLF_BOXING_MEASUREMENTS_DECODED": "NO",
        "GOLF_BOXING_USED_FOR_TUNING": "NO",
        "GOLF_BOXING_USED_FOR_SCORING": "NO",
    }
    dump_json(output / "DEVELOPMENT_RESULT.json", result)
    return result


def run_raw_locked_action_validation(root: Path, predeclaration_path: Path, output: Path) -> dict:
    root = Path(root).resolve(); output = Path(output).resolve()
    predeclaration_path = Path(predeclaration_path).resolve()
    predeclaration = json.loads(predeclaration_path.read_text(encoding="utf-8"))
    predecl_check = validate_predeclaration(root, predeclaration)
    manifest_path = Path(str(predeclaration["candidate_manifest"]))
    if not manifest_path.is_absolute(): manifest_path = root / manifest_path
    profile_path = Path(str(predeclaration["profile"]))
    if not profile_path.is_absolute(): profile_path = root / profile_path
    candidate_before = verify_candidate_lock(
        root, manifest_path, str(predeclaration["candidate_manifest_sha256"]),
    )
    profile_sha_before = sha256_file(profile_path)
    if profile_sha_before != predeclaration["profile_sha256"]:
        raise ValueError("frozen profile SHA-256 mismatch before action access")
    profile = json.loads(profile_path.read_text(encoding="utf-8")); assert_profile_boundary(profile)
    config = load_config(root / "config/biospur_fusion_v0/config.json")
    release_mode = load_release_mode(root / "config/biospur_fusion_v0/release_mode.json")
    if profile.get("config_sha256") != config.sha256:
        raise ValueError("frozen profile and active configuration disagree")

    # First action-payload access. All role, metadata, candidate, and profile
    # checks above have already passed.
    rows, access = load_raw_action_imu_only(predeclaration)
    variants, audits = _execute_variants(root, rows, profile, config)
    repeat, _ = _execute_variants(root, rows, profile, config)
    label = str(predeclaration["action_identifier"])
    for arrays in variants.values(): arrays["window"][:] = label
    for arrays in repeat.values(): arrays["window"][:] = label
    deterministic = {
        name: {
            "first_digest": _arrays_digest(arrays),
            "repeat_digest": _arrays_digest(repeat[name]),
            "identical": _arrays_digest(arrays) == _arrays_digest(repeat[name]),
        }
        for name, arrays in variants.items() if name in RELEASE_VARIANTS
    }
    model = corrected_body_model(
        root, identity_mapping=profile["identity"],
        identity_provenance=(
            "PROFILE_IDENTITY:"
            f"{profile.get('capture_id', profile.get('profile_kind', 'LEGACY_V0'))}"
        ),
    )
    numerical = {
        "selected_v0": _variant_metrics(
            variants["qmt_off"], model,
            audits["qmt_off"]["shared_ik"]["canonical_fk_closure_max_abs"],
        ),
        "selected_v0_no_shared_ik": _variant_metrics(
            variants["qmt_off_no_shared_ik"], model,
            audits["qmt_off_no_shared_ik"]["shared_ik"]["canonical_fk_observation_closure_max_abs_rad"],
        ),
        "qmt_off_reference": _variant_metrics(
            variants["qmt_off"], model,
            audits["qmt_off"]["shared_ik"]["canonical_fk_closure_max_abs"],
        ),
        "always_on_qmt": _variant_metrics(
            variants["always_on_qmt"], model,
            audits["always_on_qmt"]["shared_ik"]["canonical_fk_closure_max_abs"],
        ),
        "always_on_qmt_no_shared_ik": _variant_metrics(
            variants["always_on_qmt_no_shared_ik"], model,
            audits["always_on_qmt_no_shared_ik"]["shared_ik"]["canonical_fk_observation_closure_max_abs_rad"],
        ),
    }
    metrics = {
        "schema": "biospur-fusion-v0-independent-raw-action-metrics-v1",
        "predeclared_expected_gross_motion_semantics": predeclaration["expected_gross_motion_semantics"],
        "numerical_integrity": numerical,
        "deterministic_replay": deterministic,
        "selected_v0_mode": release_mode["selected_v0_mode"],
        "gross_motion": _gross_motion_metrics(variants["qmt_off"], model),
        "selected_v0_versus_no_shared_ik": _compare_variants(
            variants["qmt_off"], variants["qmt_off_no_shared_ik"],
        ),
        "selected_v0_versus_always_on_qmt": _compare_variants(
            variants["qmt_off"], variants["always_on_qmt"],
        ),
        "always_on_qmt_versus_no_shared_ik": _compare_variants(
            variants["always_on_qmt"], variants["always_on_qmt_no_shared_ik"],
        ),
        "module_audits": audits,
        "common_clock_gate": access["common_clock"]["gate"],
        "threshold_policy": "NO_NEW_PRODUCT_THRESHOLDS_PHYSICAL_INVARIANTS_AND_ABLATIONS_ONLY",
    }
    uncertainty = _uncertainty_localization(variants["qmt_off"], profile)
    np.savez_compressed(output / "ACTION_STATE_SELECTED_V0.npz", **variants["qmt_off"])
    np.savez_compressed(output / "ACTION_STATE_SELECTED_NO_SHARED_IK.npz", **variants["qmt_off_no_shared_ik"])
    np.savez_compressed(output / "ACTION_STATE_QMT_OFF_REFERENCE.npz", **variants["qmt_off"])
    np.savez_compressed(output / "ACTION_STATE_ALWAYS_ON_QMT.npz", **variants["always_on_qmt"])
    np.savez_compressed(output / "ACTION_STATE_ALWAYS_ON_QMT_NO_SHARED_IK.npz", **variants["always_on_qmt_no_shared_ik"])
    dump_json(output / "ACTION_VALIDATION_METRICS.json", metrics)
    dump_json(output / "UNCERTAINTY_LOCALIZATION.json", uncertainty)
    viewer = write_viewer(
        output / "ACTION_VIEWER.html",
        time_ns=variants["qmt_off"]["global_time_ns"], window=variants["qmt_off"]["window"],
        boundary=variants["qmt_off"]["boundary"],
        segment_names=tuple(str(x) for x in variants["qmt_off"]["segment_names"]),
        segment_position=variants["qmt_off"]["segment_position"],
        segment_rotation=variants["qmt_off"]["segment_rotation"],
        segment_confidence=variants["qmt_off"]["segment_confidence"],
        joint_rotvec=variants["qmt_off"]["joint_rotvec"],
        segment_sigma_rad=variants["qmt_off"]["segment_sigma_rad"],
        node_by_segment=tuple(
            {segment: node for node, segment in IDENTITY.items()}[str(segment)]
            for segment in variants["qmt_off"]["segment_names"]
        ),
        qmt_mode="QMT_OFF",
    )
    profile_sha_after = sha256_file(profile_path)
    candidate_after = verify_candidate_lock(
        root, manifest_path, str(predeclaration["candidate_manifest_sha256"]),
    )
    raw_input = predeclaration["raw_input"]
    start_byte = int(raw_input.get("start_byte_inclusive", raw_input.get("start_byte_exclusive")))
    stop_byte = int(raw_input.get("stop_byte_exclusive", raw_input.get("stop_byte_inclusive")))
    exact_open = bool(
        access["raw_path"] == str(Path(str(predeclaration["ledger"])).resolve())
        and access["sealed_container_sha256_imported"]
            == predeclaration.get("sealed_container_sha256", predeclaration.get("ledger_sha256"))
        and access["action_slice_sha256"] == raw_input["slice_sha256"]
        and access["decode"]["raw_byte_start_inclusive"] == start_byte
        and access["decode"]["raw_byte_stop_exclusive"] == stop_byte
        and access["decode"]["raw_access"]["actual_read_intervals"] == [[start_byte, stop_byte]]
    )
    immutability = {
        "schema": "biospur-fusion-v0-raw-action-access-and-immutability-v1",
        "predeclaration_sha256": sha256_file(predeclaration_path),
        "exact_predeclared_action_opened": exact_open,
        "access": access,
        "candidate_before": candidate_before, "candidate_after": candidate_after,
        "candidate_modified_after_action_access": False,
        "profile_sha256_before": profile_sha_before, "profile_sha256_after": profile_sha_after,
        "profile_byte_exact_unchanged": profile_sha_before == profile_sha_after,
        "capture1_used_only_through_frozen_profile": True,
        "capture1_payload_opened_by_validation_runner": False,
        "runtime_state_written_back_to_profile": False,
        "opened_payload_classes": access["opened_payload_classes"],
        "HISTORICAL_GOLF_BOXING_CONTAINER_BYTES_TOUCHED": "YES",
        "CURRENT_GOAL_GOLF_BOXING_BYTES_TOUCHED": "NO",
        "GOLF_BOXING_MEASUREMENTS_DECODED": "NO",
        "GOLF_BOXING_USED_FOR_TUNING": "NO",
        "GOLF_BOXING_USED_FOR_SCORING": "NO",
        "SELECTED_ACTION_PREVIOUSLY_DECODED": predeclaration["SELECTED_ACTION_PREVIOUSLY_DECODED"],
        "SELECTED_ACTION_PREVIOUSLY_RECONSTRUCTED": predeclaration["SELECTED_ACTION_PREVIOUSLY_RECONSTRUCTED"],
        "SELECTED_ACTION_PREVIOUSLY_USED_FOR_TUNING": predeclaration["SELECTED_ACTION_PREVIOUSLY_USED_FOR_TUNING"],
        "uwb_spatial_payload_opened_or_consumed": False,
        "uwb_beacon_timing_only": True,
    }
    dump_json(output / "ACCESS_AND_PROFILE_IMMUTABILITY.json", immutability)
    dump_json(output / "ACCESS_AUDIT.json", immutability)
    hard_integrity = bool(
        exact_open and access["common_clock"]["gate"]["pass"]
        and access["access_sentinels"]["timing_io_instrumented_at_os_call_layer"]
        and access["access_sentinels"]["all_sequential_timing_rows_within_action_plus_two_superframes"]
        and access["access_sentinels"]["every_binary_search_probe_separately_accounted"]
        and access["access_sentinels"]["no_full_timing_file_traversal_proven"]
        and access["access_sentinels"]["golf_boxing_timing_interval_bytes_touched"] is False
        and all(row["finite"] and row["native_time_strictly_increasing"] for row in numerical.values())
        and all(row["identical"] for row in deterministic.values())
        and profile_sha_before == profile_sha_after
    )
    result = {
        "schema": "biospur-fusion-v0-independent-raw-action-final-result-v1",
        "classification": {
            "OVERALL_SYSTEM_DIRECTION": "MIXED",
            "SELECTED_V0_MODE": "QMT_OFF",
            "INDEPENDENT_ORDINARY_ACTION_SELECTED": "YES",
            "INDEPENDENT_ACTION_RECONSTRUCTION_EXECUTED": "YES",
            "CALIBRATION_PROFILE_FROZEN_BEFORE_ACTION": "YES",
            "V0_CANDIDATE_LOCKED_DURING_VALIDATION": "YES",
            "CANDIDATE_MODIFIED_AFTER_ACTION_ACCESS": "NO",
            "FINAL_V0_FROZEN": "NO",
            "INTERNAL_PHYSICAL_COHERENCE": "INCONCLUSIVE",
            "READY_FOR_OPERATOR_AUTHORIZED_V0_FREEZE": "NO",
        },
        "automatic_execution_integrity_pass": hard_integrity,
        "selected_action": {
            "capture_identifier": predeclaration["capture_identifier"],
            "action_identifier": label, "attempt_number": predeclaration["attempt_number"],
            "start_global_time_ns": int(predeclaration["start_global_time_ns"]),
            "stop_global_time_ns_exclusive": int(predeclaration["stop_global_time_ns_exclusive"]),
            "normalized_common_time_window": True,
            "start_host_monotonic_ns": int(raw_input["start_host_monotonic_ns"]),
            "stop_host_monotonic_ns_exclusive": int(raw_input["stop_host_monotonic_ns_exclusive"]),
            "start_byte_inclusive": start_byte,
            "stop_byte_exclusive": stop_byte,
            "role": predeclaration["role"],
        },
        "candidate_manifest_sha256": predeclaration["candidate_manifest_sha256"],
        "release_mode_sha256": release_mode["sha256"],
        "profile_sha256_before": profile_sha_before, "profile_sha256_after": profile_sha_after,
        "opened_payload_classes": access["opened_payload_classes"],
        "HISTORICAL_GOLF_BOXING_CONTAINER_BYTES_TOUCHED": "YES",
        "CURRENT_GOAL_GOLF_BOXING_BYTES_TOUCHED": "NO",
        "GOLF_BOXING_MEASUREMENTS_DECODED": "NO",
        "GOLF_BOXING_USED_FOR_TUNING": "NO",
        "GOLF_BOXING_USED_FOR_SCORING": "NO",
        "SELECTED_ACTION_PREVIOUSLY_DECODED": predeclaration["SELECTED_ACTION_PREVIOUSLY_DECODED"],
        "SELECTED_ACTION_PREVIOUSLY_RECONSTRUCTED": predeclaration["SELECTED_ACTION_PREVIOUSLY_RECONSTRUCTED"],
        "SELECTED_ACTION_PREVIOUSLY_USED_FOR_TUNING": predeclaration["SELECTED_ACTION_PREVIOUSLY_USED_FOR_TUNING"],
        "uwb_spatial_access": "NONE_BEACON_AND_STROBE_TIMING_ONLY",
        "state_continuity": {name: row["continuous_segment_step_deg"] for name, row in numerical.items()},
        "fk_closure": {name: row["canonical_fk_closure_max_abs"] for name, row in numerical.items()},
        "gross_action_semantic_result": "PENDING_EVIDENCE_INTERPRETATION_AND_LIVE_VIEWER_QA",
        "selected_v0_versus_no_shared_ik_result": "PENDING_EVIDENCE_INTERPRETATION",
        "selected_v0_versus_always_on_qmt_result": "PENDING_EVIDENCE_INTERPRETATION",
        "dominant_uncertainty_locations": uncertainty["top_one_percent"],
        "viewer": viewer, "live_viewer_qa": "NOT_YET_PERFORMED_NOT_CLAIMED",
        "external_accuracy_claim_boundary": "NO_EXTERNAL_MOCAP_TRUTH_NO_CENTIMETRE_OR_DEGREE_ACCURACY_CLAIM",
        "release_action": "NO_COMMIT_NO_FREEZE_WAIT_FOR_EVIDENCE_INTERPRETATION",
    }
    dump_json(output / "FINAL_RESULT.json", result)
    (output / "FINAL_RESULT.md").write_text(
        "\n".join(f"{key}: {value}" for key, value in result["classification"].items())
        + "\n\n# Independent ordinary-action validation\n\n"
        + "Execution artifacts are complete. Physical interpretation and live Viewer QA are pending; "
        + "this provisional classification is not a release decision.\n",
        encoding="utf-8",
    )
    write_checksums(output)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[3]
    parser.add_argument("--predeclaration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_raw_locked_action_validation(root, args.predeclaration, args.output)
    print(json.dumps({
        "output": str(args.output.resolve()),
        "automatic_execution_integrity_pass": result["automatic_execution_integrity_pass"],
        "classification": result["classification"],
    }, sort_keys=True))
    return 0 if result["automatic_execution_integrity_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
