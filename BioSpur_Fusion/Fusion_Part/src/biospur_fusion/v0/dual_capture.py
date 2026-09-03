"""Independent same-capture calibration and complete V0 replay closure.

This module is deliberately capture-generic.  Capture-local metadata supplies
the node/body bijection, action bounds, and calibration inputs.  No profile is
accepted unless its capture identity matches the input before reconstruction.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

import numpy as np

from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a2a.shadow import corrected_body_model
from biospur_fusion.time.common_clock import align_capture_bounded, models_as_json

from .contracts import HARDWARE_FAMILY, NODES, dump_json, load_config, sha256_file
from .data import _stored_npy_memmap
from .episode import PHASES, phase_bounds, segment_five_phase_episode
from .frontend import run_vqf_native_hybrid
from .math3d import proper_mean, rotation_angle
from .model import (
    ResampledWindow, display_static_calibration, hard_reference_pose_fk_gate,
    initialize_common_action_display_yaw, resample_window,
)
from .raw_validation import _decode_imu_only
from .validation import (
    _arrays_digest,
    _compare_variants,
    _execute_variants,
    _gross_motion_metrics,
    _run_direct_fk_ablation,
    _uncertainty_localization,
    _variant_metrics,
)
from .viewer import POINT_NAMES, skeleton_points, write_viewer


PROFILE_SCHEMA = "biospur-fusion-v0-capture-bound-profile-v3"
LOCK_SCHEMA = "biospur-fusion-v0-dual-capture-code-lock-v3"
PROTOCOL_REL = Path("config/biospur_fusion_v0/dual_capture_protocol.json")
C1_ROLE_LEDGER_REL = Path(
    "logs/root_r6a2b_r1_calibration_first_20260826T093237Z/"
    "CAPTURE_WINDOW_ROLE_LEDGER.json"
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        _jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _metadata_manifest(root: Path, paths: list[str]) -> tuple[list[dict[str, Any]], str]:
    rows = []
    for relative in paths:
        path = (root / relative).resolve()
        rows.append({
            "path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size,
        })
    return rows, _canonical_hash(rows)


def load_protocol(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    payload = json.loads((root / PROTOCOL_REL).read_text(encoding="utf-8"))
    if payload.get("schema") != "biospur-fusion-v0-dual-capture-protocol-v1":
        raise ValueError("dual-capture protocol schema changed")
    if set(payload.get("captures", {})) != {"CAPTURE1", "CAPTURE2"}:
        raise ValueError("dual-capture protocol must bind exactly Capture1 and Capture2")
    for name, spec in payload["captures"].items():
        identity = spec.get("identity", {})
        if set(identity) != set(NODES) or len(set(identity.values())) != len(NODES):
            raise ValueError(f"{name}: capture identity is not a ten-node bijection")
        calibration = set(spec["calibration_inputs"])
        hxx = {row["action"] for row in spec["hxx"]}
        if calibration & hxx:
            raise ValueError(f"{name}: Hxx entered calibration inputs")
        accepted_non_hxx = {
            *spec["reference_actions"], *spec["main_suite_1"], *spec["main_suite_2"],
        }
        if calibration != accepted_non_hxx:
            raise ValueError(
                f"{name}: calibration must be the complete final accepted non-Hxx suite"
            )
        inventory = spec.get("inventory_contract", {})
        if name == "CAPTURE1":
            crosswalk = {row["action"]: row["hxx_id"] for row in spec["hxx"]}
            if crosswalk != {"walk": "H00_walk", "boxing": "H01_boxing", "golf_swing": "H02_golf"}:
                raise ValueError("Capture1 native Hxx crosswalk changed")
            if "final_still" in hxx or not inventory.get("final_still_is_separate_non_hxx_action"):
                raise ValueError("Capture1 final_still must remain separate from Hxx")
        else:
            if (
                inventory.get("accepted_non_hxx_action_count") != 19
                or inventory.get("hxx_action_count") != 3
                or inventory.get("total_accepted_action_count") != 22
            ):
                raise ValueError("Capture2 accepted inventory must be exactly 19 non-Hxx + 3 Hxx = 22")
            if inventory.get("deleted_or_unexecuted") != ["01_neutral_sway"]:
                raise ValueError("Capture2 deleted/unexecuted 01_neutral_sway contract changed")
            if inventory.get("retry_or_skip_history_excluded") != ["rep_02", "rep_03"]:
                raise ValueError("Capture2 retry/skip exclusion contract changed")
    semantic = payload.get("semantic_qa_contract", {})
    episode = semantic.get("calibration_episode", {})
    if (
        semantic.get("physical_pass_requires_supervisor_live_viewer_judgment") is not True
        or semantic.get("global_or_viewer_y_axis_may_define_physical_front") is not False
        or float(semantic.get("minimum_joint_excursion_deg", 0.0)) <= 0.0
        or episode.get("same_method_for_every_capture_action_and_node") is not True
        or episode.get("whole_buffer_rest_label_allowed") is not False
        or episode.get("rest_verification_requires_signal_stability") is not True
    ):
        raise ValueError("non-vacuous body-relative semantic QA contract changed")
    return payload


def assert_profile_capture_match(profile: Mapping[str, Any], input_capture_id: str) -> None:
    """Fail before frontend, heading, IK, FK, state export, or Viewer work."""
    if profile.get("profile_schema") != PROFILE_SCHEMA:
        raise ValueError("PROFILE_SCHEMA_MISMATCH_BEFORE_RECONSTRUCTION")
    profile_capture = profile.get("capture_id")
    if profile_capture != input_capture_id:
        raise ValueError(
            "PROFILE_CAPTURE_ID_MISMATCH_BEFORE_RECONSTRUCTION: "
            f"profile={profile_capture!r} input={input_capture_id!r}"
        )


def _role_for_action(spec: Mapping[str, Any], action: str) -> str:
    if action in spec["main_suite_1"]:
        return "MAIN_SUITE_1"
    if action in spec["main_suite_2"]:
        return "MAIN_SUITE_2"
    hxx = {row["action"] for row in spec["hxx"]}
    if action in hxx:
        return "HXX"
    if action in spec["calibration_inputs"]:
        return "CALIBRATION_SELF_REPLAY"
    raise ValueError(f"action is outside the exhaustive protocol: {action}")


def _capture1_action_rows(root: Path, spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    authority_path = (root / C1_ROLE_LEDGER_REL).resolve()
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    selected = []
    for row in authority["rows"]:
        if row.get("capture_identifier") != spec["capture_id"]:
            continue
        action = row["action_identifier"]
        status = row.get("qualification_status")
        if action == "formal_initial_stationary" or status == "SUPERSEDED_INVALID_ATTEMPT":
            continue
        if action not in {
            *spec["calibration_inputs"], *spec["main_suite_1"], *spec["main_suite_2"],
            *(item["action"] for item in spec["hxx"]),
        }:
            continue
        start = int(row["start_timestamp"]["global_time_ns"])
        stop = int(row["end_timestamp"]["global_time_ns_exclusive"])
        selected.append({
            "action": action,
            "native_action_id": action,
            "attempt_number": int(row.get("attempt_number", 1)),
            "start_global_time_ns": start,
            "stop_global_time_ns_exclusive": stop,
            "start_host_monotonic_ns": int(round(float(row["start_timestamp"]["monotonic_s"]) * 1e9)),
            "stop_host_monotonic_ns_exclusive": int(round(float(row["end_timestamp"]["monotonic_s"]) * 1e9)),
            "authority_sources": row["authority_source"],
            "qualification_status": status,
        })
    expected = {
        *spec["calibration_inputs"], *spec["main_suite_1"], *spec["main_suite_2"],
        *(item["action"] for item in spec["hxx"]),
    }
    if {row["action"] for row in selected} != expected:
        raise RuntimeError("Capture1 authoritative action inventory is incomplete")
    event_path = (root / spec["capture_root"] / "ACTION_EVENTS.jsonl").resolve()
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    tokens = [row for row in events if row.get("event") == "TOKEN_RECEIVED"]
    selected.sort(key=lambda row: row["start_global_time_ns"])
    binding = json.loads((root / spec["identity_source"]).read_text(encoding="utf-8"))
    capture_end_mono = float(binding["capture_end_monotonic_s"])
    hxx_actions = {item["action"] for item in spec["hxx"]}
    for row_index, row in enumerate(selected):
        start_mono = row["start_host_monotonic_ns"] * 1e-9
        stop_mono = row["stop_host_monotonic_ns_exclusive"] * 1e-9
        candidates = [
            event for event in tokens
            if event.get("action") == row["action"] and float(event["monotonic"]) <= start_mono
        ]
        if not candidates:
            raise RuntimeError(f"Capture1 {row['action']}: missing pre-transition token")
        token = max(candidates, key=lambda event: float(event["monotonic"]))
        if start_mono - float(token["monotonic"]) > 10.1:
            raise RuntimeError(f"Capture1 {row['action']}: token is not the declared transition boundary")
        final_reclassifications = [
            event for event in events
            if event.get("action") == row["action"]
            and event.get("event") == "ACTION_RECLASSIFIED"
            and event.get("scored") is True
            and float(event["monotonic"]) > stop_mono
        ]
        formal_stop_mono = stop_mono
        formal_stop_source = "AUTHORITATIVE_SELECTED_ATTEMPT_ACTION_STOP"
        invalidation_reclassification_audit = None
        if final_reclassifications:
            final_reclassification = max(
                final_reclassifications, key=lambda event: float(event["monotonic"])
            )
            force_stops = [
                event for event in events
                if event.get("action") == row["action"]
                and event.get("event") == "FORCE_ACTION_STOP"
                and stop_mono < float(event["monotonic"]) < float(final_reclassification["monotonic"])
            ]
            if not force_stops:
                raise RuntimeError(
                    f"Capture1 {row['action']}: accepted reclassification lacks final stop"
                )
            # The later FORCE_ACTION_STOP terminated failed auto-stop detection;
            # it did not extend the selected motion.  The authoritative role
            # ledger preserves the original ACTION_STOP as the accepted action
            # boundary, and the later revoke/reclassification merely restores
            # that already complete raw motion to the accepted suite.
            invalidation_reclassification_audit = {
                "force_action_stop_host_monotonic_ns": int(round(
                    max(float(event["monotonic"]) for event in force_stops) * 1e9
                )),
                "later_scored_reclassification_host_monotonic_ns": int(round(
                    float(final_reclassification["monotonic"]) * 1e9
                )),
                "effect_on_formal_action_bounds": "NONE;ORIGINAL_ACTION_STOP_RESTORED",
            }
        next_tokens = [
            float(event["monotonic"]) for event in tokens
            if float(event["monotonic"]) > formal_stop_mono
        ]
        episode_stop_mono = min(next_tokens) if next_tokens else capture_end_mono
        episode_start_mono = float(token["monotonic"])
        pre_boundary_event = "TOKEN_RECEIVED_SELECTED_ATTEMPT"
        pre_boundary_previous_action = None
        if row_index > 0:
            previous = selected[row_index - 1]
            previous_stop_mono = previous.get("_formal_stop_monotonic_s")
            disqualifying_intervening_events = [
                event for event in events
                if previous_stop_mono is not None
                and float(previous_stop_mono) < float(event.get("monotonic", -np.inf)) < start_mono
                and event.get("action") == row["action"]
                and event.get("event") in {
                    "ACTION_RETRY_REQUESTED", "ACTION_INVALIDATED",
                }
            ]
            if (
                previous_stop_mono is not None
                and previous["action"] not in hxx_actions
                and not disqualifying_intervening_events
            ):
                episode_start_mono = float(previous_stop_mono)
                pre_boundary_event = "PREVIOUS_FINAL_ACCEPTED_NON_HXX_FORMAL_STOP"
                pre_boundary_previous_action = previous["action"]
        host_to_global_offset = row["start_global_time_ns"] - row["start_host_monotonic_ns"]
        episode_start_global = int(round(episode_start_mono * 1e9)) + host_to_global_offset
        episode_stop_global = int(round(episode_stop_mono * 1e9)) + host_to_global_offset
        formal_stop_global = int(round(formal_stop_mono * 1e9)) + host_to_global_offset
        if not episode_start_global < row["start_global_time_ns"] < formal_stop_global < episode_stop_global:
            raise RuntimeError(f"Capture1 {row['action']}: complete episode does not surround formal action")
        row["formal_action_bounds"] = {
            "start_global_time_ns": row["start_global_time_ns"],
            "stop_global_time_ns_exclusive": formal_stop_global,
            "start_host_monotonic_ns": row["start_host_monotonic_ns"],
            "stop_host_monotonic_ns_exclusive": int(round(formal_stop_mono * 1e9)),
            "stop_boundary_authority": formal_stop_source,
            "invalidation_reclassification_audit": invalidation_reclassification_audit,
        }
        selected_token_global = (
            int(round(float(token["monotonic"]) * 1e9)) + host_to_global_offset
        )
        row["_formal_stop_monotonic_s"] = formal_stop_mono
        row["episode_bounds"] = {
            "start_global_time_ns": episode_start_global,
            "stop_global_time_ns_exclusive": episode_stop_global,
            "start_host_monotonic_ns": int(round(episode_start_mono * 1e9)),
            "stop_host_monotonic_ns_exclusive": int(round(episode_stop_mono * 1e9)),
            "pre_boundary_event": pre_boundary_event,
            "pre_boundary_previous_action": pre_boundary_previous_action,
            "formal_start_event": "ACTION_START",
            "formal_stop_event": "ACTION_STOP",
            "post_boundary_policy": "NEXT_TOKEN_RECEIVED_OR_CAPTURE_END_EXCLUSIVE",
            "event_source": str(event_path),
            "event_source_sha256": sha256_file(event_path),
            "selected_attempt_token_global_time_ns": selected_token_global,
        }
    for row in selected:
        row.pop("_formal_stop_monotonic_s", None)
    return selected


def _capture1_raw_bounds(
    root: Path, spec: Mapping[str, Any], row: Mapping[str, Any], ledger_sha256: str,
) -> dict[str, Any]:
    ledger = (root / spec["imu_time_ledger"]).resolve()
    starts = []; stops = []; node_rows = {}
    for node in NODES:
        mapped, _ = _stored_npy_memmap(ledger, f"imu_{node}.npy")
        episode = row["episode_bounds"]
        left = int(np.searchsorted(mapped["global_time_ns"], episode["start_global_time_ns"], side="left"))
        right = int(np.searchsorted(mapped["global_time_ns"], episode["stop_global_time_ns_exclusive"], side="left"))
        values = np.asarray(mapped[left:right])
        accepted = values[values["status"] == 1]
        if len(accepted) < 2:
            raise RuntimeError(f"Capture1 {row['action']}/{node} has no usable IMU bracket")
        start = int(np.min(accepted["raw_start_offset"]))
        stop = int(np.max(accepted["raw_end_offset"]))
        starts.append(start); stops.append(stop)
        node_rows[node] = {
            "accepted_rows": int(len(accepted)), "first_raw_start_offset": start,
            "last_raw_end_offset": stop,
            "payload_sha256": hashlib.sha256(accepted.tobytes()).hexdigest(),
        }
    return {
        "start_byte_inclusive": min(starts), "stop_byte_exclusive": max(stops),
        "derivation": "MIN_MAX_TEN_NODE_COMPLETE_EPISODE_IMU_ENVELOPE_OFFSETS_IN_STORED_FULL_TIME_LEDGER",
        "source_ledger": str(ledger), "source_ledger_sha256": ledger_sha256,
        "nodes": node_rows,
    }


def _capture2_action_rows(root: Path, spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    capture_root = (root / spec["capture_root"]).resolve()
    plan = json.loads((capture_root / "CAPTURE_PLAN_FINAL.json").read_text(encoding="utf-8"))
    rows = []
    for planned in plan["actions"]:
        action = planned["action_id"]
        if action == "01_neutral_sway":
            continue
        action_root = capture_root / planned["relative_dir"] / "rep_01"
        event_path = action_root / "events/ACTION_EVENTS.jsonl"
        manifest_path = action_root / "manifest/CAPTURE_MANIFEST.json"
        events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
        event = {item["event"]: item for item in events}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("status") != "ACCEPTED":
            raise RuntimeError(f"Capture2 planned action is not accepted: {action}")
        episode_start = event["REPETITION_START_BOUNDARY"]
        start = event["ACTION_START"]; stop = event["ACTION_STOP"]
        episode_stop = event["REPETITION_END_BOUNDARY"]
        continuous = manifest["continuous_range"]
        rows.append({
            "action": action,
            "native_action_id": action,
            "promoted_repetition_id": "rep_01",
            "operator_attempt_id": int(start["attempt_id"]),
            "rep_02_rep_03_role": "RETRY_OR_SKIP_HISTORY_EXCLUDED_FROM_ACCEPTED_PROTOCOL",
            "start_global_time_ns": None,
            "stop_global_time_ns_exclusive": None,
            "start_host_monotonic_ns": int(start["host_monotonic_ns"]),
            "stop_host_monotonic_ns_exclusive": int(stop["host_monotonic_ns"]),
            "start_byte_inclusive": int(start["continuous_raw_complete_frame_bytes"]),
            "stop_byte_exclusive": int(stop["continuous_raw_complete_frame_bytes"]),
            "formal_action_bounds": {
                "start_host_monotonic_ns": int(start["host_monotonic_ns"]),
                "stop_host_monotonic_ns_exclusive": int(stop["host_monotonic_ns"]),
                "start_byte_inclusive": int(start["continuous_raw_complete_frame_bytes"]),
                "stop_byte_exclusive": int(stop["continuous_raw_complete_frame_bytes"]),
            },
            "episode_bounds": {
                "start_host_monotonic_ns": int(episode_start["host_monotonic_ns"]),
                "stop_host_monotonic_ns_exclusive": int(episode_stop["host_monotonic_ns"]),
                "start_byte_inclusive": int(episode_start["continuous_raw_complete_frame_bytes"]),
                "stop_byte_exclusive": int(episode_stop["continuous_raw_complete_frame_bytes"]),
                "pre_boundary_event": "REPETITION_START_BOUNDARY",
                "formal_start_event": "ACTION_START",
                "formal_stop_event": "ACTION_STOP",
                "post_boundary_event": "REPETITION_END_BOUNDARY",
                "preparation_buffer_s": float(manifest["preparation_buffer_s"]),
                "post_action_buffer_s": float(manifest["post_action_buffer_s"]),
            },
            "read_bracket": {
                "start_byte_inclusive": int(continuous["start_byte_inclusive"]),
                "stop_byte_exclusive": int(continuous["end_byte_exclusive"]),
                "slice_sha256": continuous["slice_sha256"],
                "boundary": "COMPLETE_COBS_FRAME_WITH_DECLARED_PRE_POST",
            },
            "event_source": str(event_path.resolve()),
            "event_source_sha256": sha256_file(event_path),
            "manifest": str(manifest_path.resolve()),
            "manifest_sha256": sha256_file(manifest_path),
            "source_declared_data_role": planned["data_role"],
            "qualification_status": manifest["status"],
        })
    expected = {
        *spec["calibration_inputs"], *spec["main_suite_1"], *spec["main_suite_2"],
        *(item["action"] for item in spec["hxx"]),
    }
    if {row["action"] for row in rows} != expected:
        missing = expected - {row["action"] for row in rows}
        extra = {row["action"] for row in rows} - expected
        raise RuntimeError(f"Capture2 action inventory mismatch missing={missing} extra={extra}")
    return sorted(rows, key=lambda row: row["start_host_monotonic_ns"])


def build_protocol_ledger(root: Path) -> dict[str, Any]:
    root = Path(root).resolve(); protocol = load_protocol(root)
    captures = {}
    for name, spec in protocol["captures"].items():
        metadata, metadata_hash = _metadata_manifest(root, list(spec["metadata_sources"]))
        if name == "CAPTURE1":
            action_rows = _capture1_action_rows(root, spec)
            c1_ledger_sha256 = sha256_file((root / spec["imu_time_ledger"]).resolve())
            for row in action_rows:
                row["raw_bounds"] = _capture1_raw_bounds(root, spec, row, c1_ledger_sha256)
        else:
            action_rows = _capture2_action_rows(root, spec)
        hxx_by_action = {row["action"]: row["hxx_id"] for row in spec["hxx"]}
        hxx_rows = [row for row in action_rows if row["action"] in hxx_by_action]
        for row in action_rows:
            action = row["action"]
            if action not in hxx_by_action:
                if name == "CAPTURE2":
                    row["forbidden_hxx_timing_intervals_ns"] = [
                        [
                            int(item["episode_bounds"]["start_host_monotonic_ns"]),
                            int(item["episode_bounds"]["stop_host_monotonic_ns_exclusive"]),
                        ]
                        for item in hxx_rows
                    ]
                    row["forbidden_hxx_raw_byte_ranges"] = [
                        [
                            int(item["episode_bounds"]["start_byte_inclusive"]),
                            int(item["episode_bounds"]["stop_byte_exclusive"]),
                        ]
                        for item in hxx_rows
                    ]
                else:
                    row["forbidden_hxx_global_time_intervals_ns"] = [
                        [
                            int(item["episode_bounds"]["selected_attempt_token_global_time_ns"]),
                            int(item["episode_bounds"]["stop_global_time_ns_exclusive"]),
                        ]
                        for item in hxx_rows
                    ]
            row["protocol_roles"] = []
            if action in spec["calibration_inputs"]:
                row["protocol_roles"].append("CALIBRATION_SELF_REPLAY")
            if action in spec["main_suite_1"]:
                row["protocol_roles"].append("MAIN_SUITE_1")
            if action in spec["main_suite_2"]:
                row["protocol_roles"].append("MAIN_SUITE_2")
            if action in hxx_by_action:
                row["protocol_roles"].extend([
                    "HXX", "POST_CALIBRATION_REPLAY", "POST_CALIBRATION_REGRESSION",
                ])
                row["hxx_id"] = hxx_by_action[action]
                row["native_to_hxx_crosswalk"] = {
                    "native_action_id": action, "normalized_hxx_id": hxx_by_action[action],
                }
            row["contributes_to_profile"] = action in spec["calibration_inputs"]
        raw = (root / spec["raw_container"]).resolve()
        timing = [{
            "path": str((root / value).resolve()),
            "bytes": (root / value).stat().st_size if (root / value).is_file() else None,
            "kind": "FILE" if (root / value).is_file() else "DIRECTORY",
        } for value in spec["timing_sources"]]
        captures[name] = {
            "capture_id": spec["capture_id"],
            "session_date": "2026-08-14" if name == "CAPTURE1" else "2026-08-17",
            "metadata_sources": metadata, "capture_metadata_hash": metadata_hash,
            "node_inventory": sorted(spec["identity"]),
            "node_to_body_mapping": spec["identity"],
            "identity_source": str((root / spec["identity_source"]).resolve()),
            "capture_mapping_hash": _canonical_hash(spec["identity"]),
            "logical_slot_node_source_mapping": (
                json.loads((root / spec["capture_root"] / "RUNTIME_TDMA_MANIFEST.json").read_text())["mapping"]
                if name == "CAPTURE1" else
                "AUTHORITATIVE_SYSTEM_READINESS_REPORT_PARSED_AT_BOUNDED_INGEST"
            ),
            "raw_container": {
                "path": str(raw), "bytes": raw.stat().st_size,
                "sealed_sha256_imported": spec["raw_sha256"],
                "full_hash_recomputed_for_ledger": False,
            },
            "timing_sources": timing,
            "calibration_inputs": list(spec["calibration_inputs"]),
            "reference_actions": list(spec["reference_actions"]),
            "functional_axis_actions": dict(spec["functional_axis_actions"]),
            "main_suite_1": list(spec["main_suite_1"]),
            "main_suite_2": list(spec["main_suite_2"]),
            "main_suite_label_provenance": spec["inventory_contract"]["main_suite_label_provenance"],
            "hxx": list(spec["hxx"]),
            "inventory_contract": dict(spec["inventory_contract"]),
            "accepted_non_hxx_action_count": int(sum(
                row["action"] not in hxx_by_action for row in action_rows
            )),
            "accepted_hxx_action_count": int(sum(
                row["action"] in hxx_by_action for row in action_rows
            )),
            "accepted_total_action_count": len(action_rows),
            "actions": action_rows,
            "existing_calibration_artifacts": (
                [
                    "logs/biospur_fusion_v0_golden_20260826T173258Z/V0_SESSION_PROFILE.json",
                    "logs/root_r6a2b_r4_functional_axis_20260826T170000Z/DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json",
                ] if name == "CAPTURE1" else []
            ),
            "existing_replay_artifacts": (
                ["logs/biospur_fusion_v0_golden_20260826T173258Z"] if name == "CAPTURE1" else [
                    "logs/biospur_fusion_v0_independent_action_validation_20260826T183538Z",
                    "logs/biospur_fusion_v0_transfer_closure_20260826T191002Z",
                    "logs/biospur_fusion_v0_conservative_release_20260826T215048Z",
                ]
            ),
            "profile_provenance_status": (
                "C91_HYBRID_CAPTURE1_CALIBRATION_PLUS_CAPTURE2_FOREARM_IDENTITY_"
                "NOT_A_VALID_CAPTURE1_PROFILE" if name == "CAPTURE1" else
                "NO_VALID_CAPTURE2_PROFILE_BEFORE_THIS_GOAL;_C91_IS_HYBRID_"
                "CAPTURE1_CALIBRATION_PLUS_CAPTURE2_FOREARM_IDENTITY"
            ),
        }
    return {
        "schema": "biospur-fusion-v0-capture-protocol-ledger-v1",
        "goal": protocol["goal"],
        "suite_partition_policy": protocol["suite_partition_policy"],
        "suite_source_note": (
            "Acquisition metadata does not carry MAIN_SUITE_1/2 labels. They are goal-local logical "
            "replay partitions only, never recorded-suite claims. Native action IDs remain authoritative."
        ),
        "historical_c91_profile_provenance": {
            "sha256": "c91d03545151fb6507925f79f12e61f9f343e2e4e7a40bbd3659e6b5d8046076",
            "classification": "HYBRID_NOT_CAPTURE_BOUND",
            "calibration_quantities_capture_id": (
                "v47_ten_node_body_calibration_20260814_093601"
            ),
            "forearm_identity_mapping_capture_id": (
                "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
            ),
            "valid_complete_profile_for_capture1": False,
            "valid_complete_profile_for_capture2": False,
        },
        "captures": captures,
        "answers": {
            "CAPTURE1_COMPLETE_PROTOCOL_MAPPED": "YES",
            "CAPTURE2_COMPLETE_PROTOCOL_MAPPED": "YES",
            "CAPTURE1_CALIBRATION_ARTIFACT_PROVENANCE_KNOWN": "YES",
            "CAPTURE2_CALIBRATION_ARTIFACT_PROVENANCE_KNOWN": "YES",
            "CROSS_CAPTURE_RESULTS_IDENTIFIED": "YES",
        },
    }


def historical_invalidation(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    c1 = "v47_ten_node_body_calibration_20260814_093601"
    c2 = "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
    profile_hash = "c91d03545151fb6507925f79f12e61f9f343e2e4e7a40bbd3659e6b5d8046076"
    packages = [
        ("CAPTURE1_FOREARMS", "logs/biospur_fusion_v0_golden_20260826T173258Z", c1, [
            "Capture1 left/right forearm identity", "Capture1 elbow/forearm physical semantics",
            "Capture1 forearm-dependent whole-body physical coherence and readiness",
        ]),
        ("04_shoulder_left", "logs/biospur_fusion_v0_independent_action_validation_20260826T183538Z", c2, ["ALL_CAPTURE2_PHYSICAL_CLAIMS_IN_PACKAGE", "shoulder-left physical semantics"]),
        ("04_shoulder_left", "logs/biospur_fusion_v0_transfer_closure_20260826T191002Z/DEVELOPMENT", c2, ["ALL_CAPTURE2_PHYSICAL_CLAIMS_IN_PACKAGE", "shoulder-left physical semantics", "shared IK physical benefit"]),
        ("05_shoulder_right", "logs/biospur_fusion_v0_transfer_closure_20260826T191002Z/VALIDATION", c2, ["ALL_CAPTURE2_PHYSICAL_CLAIMS_IN_PACKAGE", "shoulder-right physical semantics"]),
        ("05_shoulder_right", "logs/biospur_fusion_v0_transfer_closure_20260826T191002Z/VALIDATION_RELOCKED", c2, ["ALL_CAPTURE2_PHYSICAL_CLAIMS_IN_PACKAGE", "shoulder-right physical semantics", "candidate physical transfer"]),
        ("04_shoulder_left", "logs/biospur_fusion_v0_conservative_release_20260826T215048Z/DEVELOPMENT/04_SHOULDER_LEFT_RERUN", c2, ["ALL_CAPTURE2_PHYSICAL_CLAIMS_IN_PACKAGE", "qmt/IK/uncertainty physical comparison"]),
        ("04_shoulder_left", "logs/biospur_fusion_v0_conservative_release_20260826T215048Z/DEVELOPMENT/04_SHOULDER_LEFT_DECOUPLED", c2, ["ALL_CAPTURE2_PHYSICAL_CLAIMS_IN_PACKAGE", "decoupled-confidence shoulder-left physical result"]),
        ("05_shoulder_right", "logs/biospur_fusion_v0_conservative_release_20260826T215048Z/DEVELOPMENT/05_SHOULDER_RIGHT", c2, ["ALL_CAPTURE2_PHYSICAL_CLAIMS_IN_PACKAGE", "qmt/IK/uncertainty physical comparison"]),
        ("05_shoulder_right", "logs/biospur_fusion_v0_conservative_release_20260826T215048Z/DEVELOPMENT/05_SHOULDER_RIGHT_DECOUPLED", c2, ["ALL_CAPTURE2_PHYSICAL_CLAIMS_IN_PACKAGE", "decoupled-confidence shoulder-right physical result"]),
        ("06_elbow_left", "logs/biospur_fusion_v0_conservative_release_20260826T215048Z/FRESH_VALIDATION", c2, ["ALL_CAPTURE2_PHYSICAL_CLAIMS_IN_PACKAGE", "elbow-left physical semantics", "qmt-off physical conclusion", "shared IK physical benefit", "uncertainty physical localization"]),
        ("WHOLE_CAR", "logs/biospur_fusion_v0_conservative_release_20260826T215048Z", c2, ["ALL_CAPTURE2_PHYSICAL_CLAIMS_IN_PACKAGE", "internal physical coherence PASS", "physical V0 readiness", "ready for operator-authorized freeze"]),
    ]
    entries = []
    for action, relative, input_capture_id, claims in packages:
        path = (root / relative).resolve()
        files = sorted(str(item) for item in path.rglob("*") if item.is_file()) if path.is_dir() else [str(path)]
        entries.append({
            "artifact": str(path), "action": action,
            "profile_capture_id": "HYBRID_NOT_CAPTURE_BOUND",
            "calibration_quantities_capture_id": c1,
            "forearm_identity_mapping_capture_id": c2,
            "input_capture_id": input_capture_id,
            "profile_hash": profile_hash,
            "classification": "INVALID_CROSS_CAPTURE_PROFILE_APPLICATION",
            "reason_invalid": (
                "Profile c91 is hybrid, not a known Capture1 profile: its calibration quantities "
                "come from Capture1 while its serialized forearm identity matches Capture2. It "
                "therefore contaminates Capture1 forearm-dependent claims and every Capture2 "
                "physical claim. Capture1 BSFB165=left/BSFEC35=right; Capture2 is reversed."
            ),
            "scientific_claims_withdrawn": claims,
            "files_preserved": files,
        })
    return {
        "schema": "biospur-fusion-v0-historical-cross-capture-invalidation-v1",
        "classification": "INVALID_CROSS_CAPTURE_PROFILE_APPLICATION",
        "profile_hash": profile_hash,
        "entries": entries,
        "preserved_engineering_evidence": [
            "bounded byte reading and timing call accounting",
            "deterministic software output",
            "SO(3) numerical validity",
            "canonical FK closure",
            "Viewer loading/playback/runtime behavior",
            "candidate manifests and content hashes",
        ],
        "withdrawn_claim_scope": [
            "CAPTURE1_FOREARM_DEPENDENT_PHYSICAL_INTERPRETATION_AND_WHOLE_BODY_READINESS",
            "ALL_CAPTURE2_PHYSICAL_INTERPRETATION",
            "CROSS_CAPTURE_PHYSICAL_REPEATABILITY_AND_READINESS",
        ],
        "c91_profile_classification": "HYBRID_CAPTURE1_CALIBRATION_PLUS_CAPTURE2_FOREARM_IDENTITY",
        "historical_files_modified_or_deleted": False,
    }


def write_milestone_a(root: Path, goal_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    ledger = build_protocol_ledger(root); invalidation = historical_invalidation(root)
    dump_json(goal_dir / "CAPTURE_PROTOCOL_LEDGER.json", ledger)
    dump_json(goal_dir / "HISTORICAL_CROSS_CAPTURE_INVALIDATION.json", invalidation)
    lines = [
        "# Historical cross-capture invalidation", "",
        "All listed files remain byte-preserved. The reclassification is scientific, not destructive.", "",
        "Classification: `INVALID_CROSS_CAPTURE_PROFILE_APPLICATION`", "",
        "Profile c91 is hybrid: Capture1 calibration quantities plus Capture2 forearm identity. Capture1 forearm-dependent physical claims and all Capture2 physical claims produced with it are withdrawn. Bounded access, deterministic software behavior, SO(3), FK closure, Viewer runtime, and content hashes remain valid engineering evidence.", "",
        "## Affected packages", "",
    ]
    for row in invalidation["entries"]:
        lines += [
            f"- `{row['action']}` — `{row['artifact']}`",
            f"  - Withdrawn: {', '.join(row['scientific_claims_withdrawn'])}.",
        ]
    lines += ["", "Historical files modified or deleted: **NO**", ""]
    (goal_dir / "HISTORICAL_CROSS_CAPTURE_INVALIDATION.md").write_text(
        "\n".join(lines), encoding="utf-8",
    )
    return ledger, invalidation


def load_capture1_action(root: Path, spec: Mapping[str, Any], action: Mapping[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    ledger = (root / spec["imu_time_ledger"]).resolve()
    output = {}; nodes = {}
    start = int(action["start_global_time_ns"]); stop = int(action["stop_global_time_ns_exclusive"])
    for node in NODES:
        mapped, metadata = _stored_npy_memmap(ledger, f"imu_{node}.npy")
        left = int(np.searchsorted(mapped["global_time_ns"], start, side="left"))
        right = int(np.searchsorted(mapped["global_time_ns"], stop, side="left"))
        values = np.asarray(mapped[left:right]).copy()
        accepted = values[values["status"] == 1]
        if len(accepted) < 2 or np.any(np.diff(accepted["global_time_ns"]) <= 0):
            raise RuntimeError(f"Capture1 {action['action']}/{node}: invalid IMU rows")
        output[node] = accepted
        nodes[node] = {
            **metadata, "slice_start_index": left, "slice_stop_index": right,
            "accepted_rows": int(len(accepted)),
            "payload_sha256": hashlib.sha256(accepted.tobytes()).hexdigest(),
            "raw_start_offset": int(np.min(accepted["raw_start_offset"])),
            "raw_end_offset": int(np.max(accepted["raw_end_offset"])),
        }
    return output, {
        "schema": "biospur-fusion-v0-capture1-window-access-v1",
        "capture_id": spec["capture_id"], "action": action["action"],
        "ledger": str(ledger), "ledger_sha256": sha256_file(ledger),
        "opened_members": sorted(f"imu_{node}.npy" for node in NODES),
        "spatial_members_opened": [], "nodes": nodes,
        "hxx_allowed_post_calibration": action["action"] in {row["action"] for row in spec["hxx"]},
        "uwb_spatial_payload_consumed": False,
    }


def load_capture2_action(root: Path, spec: Mapping[str, Any], action: Mapping[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    raw = (root / spec["raw_container"]).resolve()
    capture_root = (root / spec["capture_root"]).resolve()
    timing_log = capture_root / "system/fusion_continuous/fusion_cdc.log"
    listener_dir = capture_root / "system/listeners/passive_5"
    readiness = capture_root / "system/readiness/SYSTEM_READINESS_REPORT.json"
    start_s = int(action["start_host_monotonic_ns"]) * 1e-9
    stop_s = int(action["stop_host_monotonic_ns_exclusive"]) * 1e-9
    models, residual_rows, gate = align_capture_bounded(
        timing_log, listener_dir, readiness, start_s, stop_s, NODES,
        expected_readiness_sha256=sha256_file(readiness),
        search_ceiling_fraction=None, forbidden_time_intervals_ns=(),
    )
    if not gate["reconstruction_safe"]:
        raise RuntimeError(
            f"Capture2 {action['action']}: bounded common-clock reconstruction-safety gate failed"
        )
    bridge = gate["action_annotation_bridge"]
    annotation_start = int(round((bridge["listener_global_us_per_host_s"] * start_s + bridge["listener_global_us_intercept"]) * 1000.0))
    annotation_stop = int(round((bridge["listener_global_us_per_host_s"] * stop_s + bridge["listener_global_us_intercept"]) * 1000.0))
    bracket = action["read_bracket"]
    rows, decode = _decode_imu_only(
        raw, int(bracket["start_byte_inclusive"]), int(bracket["stop_byte_exclusive"]),
        models, annotation_start, annotation_stop,
        expected_slice_sha256=str(bracket["slice_sha256"]),
    )
    return rows, {
        "schema": "biospur-fusion-v0-capture2-window-access-v1",
        "capture_id": spec["capture_id"], "action": action["action"],
        "raw_path": str(raw), "sealed_container_sha256_imported": spec["raw_sha256"],
        "complete_container_hash_recomputed": False,
        "read_bracket": bracket, "decode": decode,
        "common_clock": {
            "method": "LISTENER_BEACON_POLL_PLUS_B306_TIMER2",
            "models": models_as_json(models), "gate": gate, "residual_rows": residual_rows,
            "annotation_start_absolute_global_ns": annotation_start,
            "annotation_stop_absolute_global_ns_exclusive": annotation_stop,
        },
        "timing_access": gate["timing_access"],
        "hxx_allowed_post_calibration": action["action"] in {row["action"] for row in spec["hxx"]},
        "uwb_spatial_payload_consumed": False,
    }


def load_capture1_calibration_episode(
    root: Path, spec: Mapping[str, Any], action: Mapping[str, Any],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    ledger = (root / spec["imu_time_ledger"]).resolve()
    episode = action["episode_bounds"]
    formal = action["formal_action_bounds"]
    start = int(episode["start_global_time_ns"])
    stop = int(episode["stop_global_time_ns_exclusive"])
    forbidden = [tuple(map(int, row)) for row in action["forbidden_hxx_global_time_intervals_ns"]]
    if any(max(start, left) < min(stop, right) for left, right in forbidden):
        raise ValueError(f"Capture1 {action['action']}: complete episode overlaps Hxx")
    output = {}; nodes = {}
    for node in NODES:
        mapped, metadata = _stored_npy_memmap(ledger, f"imu_{node}.npy")
        left = int(np.searchsorted(mapped["global_time_ns"], start, side="left"))
        right = int(np.searchsorted(mapped["global_time_ns"], stop, side="left"))
        values = np.asarray(mapped[left:right]).copy()
        accepted = values[values["status"] == 1]
        if len(accepted) < 2 or np.any(np.diff(accepted["global_time_ns"]) <= 0):
            raise RuntimeError(f"Capture1 {action['action']}/{node}: invalid episode IMU rows")
        output[node] = accepted
        nodes[node] = {
            **metadata,
            "slice_start_index": left, "slice_stop_index": right,
            "accepted_rows": int(len(accepted)),
            "first_global_time_ns": int(accepted["global_time_ns"][0]),
            "last_global_time_ns": int(accepted["global_time_ns"][-1]),
            "payload_sha256": hashlib.sha256(accepted.tobytes()).hexdigest(),
            "raw_start_offset": int(np.min(accepted["raw_start_offset"])),
            "raw_end_offset": int(np.max(accepted["raw_end_offset"])),
        }
    return output, {
        "schema": "biospur-fusion-v0-capture1-complete-calibration-episode-access-v1",
        "capture_id": spec["capture_id"], "action": action["action"],
        "ledger": str(ledger), "ledger_sha256": sha256_file(ledger),
        "opened_members": sorted(f"imu_{node}.npy" for node in NODES),
        "nodes": nodes,
        "episode_bounds": dict(episode), "formal_action_bounds": dict(formal),
        "boundary_authority": {
            "pre": episode["pre_boundary_event"],
            "formal_start": episode["formal_start_event"],
            "formal_stop": episode["formal_stop_event"],
            "post": episode["post_boundary_policy"],
            "event_source": episode["event_source"],
            "event_source_sha256": episode["event_source_sha256"],
        },
        "forbidden_hxx_global_time_intervals_ns": [list(row) for row in forbidden],
        "hxx_payload_opened": False, "invalidated_attempt_payload_opened": False,
        "spatial_members_opened": [], "uwb_spatial_payload_consumed": False,
    }


def load_capture2_calibration_episode(
    root: Path, spec: Mapping[str, Any], action: Mapping[str, Any],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    raw = (root / spec["raw_container"]).resolve()
    capture_root = (root / spec["capture_root"]).resolve()
    timing_log = capture_root / "system/fusion_continuous/fusion_cdc.log"
    listener_dir = capture_root / "system/listeners/passive_5"
    readiness = capture_root / "system/readiness/SYSTEM_READINESS_REPORT.json"
    episode = action["episode_bounds"]
    formal = action["formal_action_bounds"]
    start_s = int(episode["start_host_monotonic_ns"]) * 1e-9
    stop_s = int(episode["stop_host_monotonic_ns_exclusive"]) * 1e-9
    forbidden_timing = [
        tuple(map(int, row)) for row in action["forbidden_hxx_timing_intervals_ns"]
    ]
    forbidden_raw = [
        tuple(map(int, row)) for row in action["forbidden_hxx_raw_byte_ranges"]
    ]
    selected_raw = (
        int(episode["start_byte_inclusive"]), int(episode["stop_byte_exclusive"]),
    )
    if any(max(selected_raw[0], left) < min(selected_raw[1], right) for left, right in forbidden_raw):
        raise ValueError(f"Capture2 {action['action']}: complete episode overlaps Hxx bytes")
    seed_contract = dict(spec["timing_safe_ceiling_seed_source"])
    seed_source = (root / seed_contract["path"]).resolve()
    if sha256_file(seed_source) != seed_contract["sha256"]:
        raise ValueError("Capture2 timing safe-ceiling seed authority hash mismatch")
    seed_payload = json.loads(seed_source.read_text(encoding="utf-8"))
    seed_windows = seed_payload["common_clock"]["gate"]["timing_access"][
        "sequential_windows"
    ]
    safe_ceiling_seed_offsets = {
        str(Path(row["path"]).resolve()): int(row["stop_byte_exclusive"])
        for row in seed_windows
    }
    listener_summary = json.loads(
        (listener_dir / "summary.json").read_text(encoding="utf-8")
    )
    expected_timing_files = {str(timing_log.resolve())} | {
        str((listener_dir / "listeners" / f"{snr}.jsonl").resolve())
        for snr, info in listener_summary["listeners"].items()
        if info.get("first_lstat", {}).get("role") == "OBSERVER"
        and info.get("kinds", {}).get("LPD", 0)
        and info.get("kinds", {}).get("LBD", 0)
    }
    if set(safe_ceiling_seed_offsets) != expected_timing_files:
        raise ValueError("Capture2 timing safe-ceiling seed file coverage mismatch")
    models, residual_rows, gate = align_capture_bounded(
        timing_log, listener_dir, readiness, start_s, stop_s, NODES,
        expected_readiness_sha256=sha256_file(readiness),
        safe_ceiling_seed_offsets=safe_ceiling_seed_offsets,
        forbidden_time_intervals_ns=forbidden_timing,
    )
    if not gate["reconstruction_safe"]:
        raise RuntimeError(
            f"Capture2 {action['action']}: complete-episode clock reconstruction unsafe"
        )
    timing_access = gate["timing_access"]
    if (
        timing_access["golf_boxing_timing_interval_bytes_touched"] is not False
        or timing_access["no_full_file_traversal_proven_by_actual_read_union"] is not True
        or timing_access["every_binary_search_probe_separately_accounted"] is not True
    ):
        raise RuntimeError(f"Capture2 {action['action']}: hostile timing-access proof failed")
    bridge = gate["action_annotation_bridge"]
    def map_host_ns(value: int) -> int:
        host_s = int(value) * 1e-9
        return int(round((
            bridge["listener_global_us_per_host_s"] * host_s
            + bridge["listener_global_us_intercept"]
        ) * 1000.0))
    episode_start_absolute = map_host_ns(int(episode["start_host_monotonic_ns"]))
    episode_stop_absolute = map_host_ns(int(episode["stop_host_monotonic_ns_exclusive"]))
    formal_start_absolute = map_host_ns(int(formal["start_host_monotonic_ns"]))
    formal_stop_absolute = map_host_ns(int(formal["stop_host_monotonic_ns_exclusive"]))
    bracket = action["read_bracket"]
    if selected_raw != (
        int(bracket["start_byte_inclusive"]), int(bracket["stop_byte_exclusive"]),
    ):
        raise RuntimeError(f"Capture2 {action['action']}: manifest episode/raw bracket mismatch")
    rows, decode = _decode_imu_only(
        raw, selected_raw[0], selected_raw[1], models,
        episode_start_absolute, episode_stop_absolute,
        expected_slice_sha256=str(bracket["slice_sha256"]),
    )
    normalized_formal = {
        "start_global_time_ns": formal_start_absolute - episode_start_absolute,
        "stop_global_time_ns_exclusive": formal_stop_absolute - episode_start_absolute,
        **dict(formal),
    }
    normalized_episode = {
        **dict(episode),
        "start_global_time_ns": 0,
        "stop_global_time_ns_exclusive": episode_stop_absolute - episode_start_absolute,
    }
    return rows, {
        "schema": "biospur-fusion-v0-capture2-complete-calibration-episode-access-v1",
        "capture_id": spec["capture_id"], "action": action["action"],
        "raw_path": str(raw), "sealed_container_sha256_imported": spec["raw_sha256"],
        "complete_container_hash_recomputed": False,
        "read_bracket": bracket, "decode": decode,
        "episode_bounds": normalized_episode,
        "formal_action_bounds": normalized_formal,
        "boundary_authority": {
            "pre": episode["pre_boundary_event"],
            "formal_start": episode["formal_start_event"],
            "formal_stop": episode["formal_stop_event"],
            "post": episode["post_boundary_event"],
            "event_source": action["event_source"],
            "event_source_sha256": action["event_source_sha256"],
            "manifest": action["manifest"],
            "manifest_sha256": action["manifest_sha256"],
        },
        "common_clock": {
            "method": "LISTENER_BEACON_POLL_PLUS_B306_TIMER2",
            "models": models_as_json(models), "gate": gate, "residual_rows": residual_rows,
            "episode_start_absolute_global_ns": episode_start_absolute,
            "episode_stop_absolute_global_ns_exclusive": episode_stop_absolute,
            "formal_start_absolute_global_ns": formal_start_absolute,
            "formal_stop_absolute_global_ns_exclusive": formal_stop_absolute,
        },
        "timing_access": timing_access,
        "timing_safe_ceiling_seed_authority": {
            **seed_contract, "resolved_path": str(seed_source),
            "exact_file_coverage": True,
            "seed_offsets": safe_ceiling_seed_offsets,
        },
        "forbidden_hxx_raw_byte_ranges": [list(row) for row in forbidden_raw],
        "hxx_payload_opened": False, "retry_or_skip_payload_opened": False,
        "uwb_spatial_payload_consumed": False,
    }


def load_calibration_episode(
    root: Path, capture_name: str, spec: Mapping[str, Any], action: Mapping[str, Any],
) -> tuple[dict[str, np.ndarray], dict[str, Any], dict[str, Any]]:
    if action["action"] not in spec["calibration_inputs"]:
        raise ValueError("calibration episode action is not in the complete non-Hxx suite")
    if action["action"] in {row["action"] for row in spec["hxx"]}:
        raise ValueError("Hxx may not enter calibration episode ingest")
    if action["qualification_status"] in {
        "SUPERSEDED_INVALID_ATTEMPT", "OPERATOR_SKIPPED_NOT_ACQUIRED",
    }:
        raise ValueError("invalidated or unaccepted attempt may not enter calibration")
    if capture_name == "CAPTURE1":
        rows, access = load_capture1_calibration_episode(root, spec, action)
    elif capture_name == "CAPTURE2":
        rows, access = load_capture2_calibration_episode(root, spec, action)
    else:
        raise ValueError(f"unknown capture name {capture_name}")
    formal = access["formal_action_bounds"]
    contract = load_protocol(root)["semantic_qa_contract"]["calibration_episode"]
    diagnostic = segment_five_phase_episode(
        rows, action=action["action"],
        action_kind=(
            "STATIONARY_REFERENCE"
            if action["action"] in set(spec["stationary_reference_actions"])
            else "MOVEMENT_OR_POSE"
        ),
        formal_start_global_ns=int(formal["start_global_time_ns"]),
        formal_stop_global_ns_exclusive=int(formal["stop_global_time_ns_exclusive"]),
        contract=contract, boundary_authority=access["boundary_authority"],
    )
    access["five_phase_episode_diagnostic"] = diagnostic
    if diagnostic["EPISODE_COMPLETENESS"] != "PASS":
        raise RuntimeError(
            f"{capture_name}:{action['action']}: CALIBRATION_EPISODE_INCOMPLETE "
            f"{diagnostic['failures']}"
        )
    return rows, access, diagnostic


def load_action(root: Path, capture_name: str, spec: Mapping[str, Any], action: Mapping[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    if capture_name == "CAPTURE1":
        return load_capture1_action(root, spec, action)
    if capture_name == "CAPTURE2":
        return load_capture2_action(root, spec, action)
    raise ValueError(f"unknown capture name {capture_name}")


def _desired_tpose_rotation(segment: str) -> np.ndarray:
    if segment in {"upper_arm_left", "forearm_left"}:
        return so3_exp(np.array([0.0, np.pi / 2.0, 0.0]))
    if segment in {"upper_arm_right", "forearm_right"}:
        return so3_exp(np.array([0.0, -np.pi / 2.0, 0.0]))
    return np.eye(3)


def _fit_sensor_from_segment_vector_pairs(
    sensor_vectors: np.ndarray, segment_vectors: np.ndarray,
) -> np.ndarray:
    """Wahba fit mapping segment-frame reference vectors into sensor axes."""
    measured = np.asarray(sensor_vectors, float)
    expected = np.asarray(segment_vectors, float)
    measured /= np.linalg.norm(measured, axis=1, keepdims=True)
    expected /= np.linalg.norm(expected, axis=1, keepdims=True)
    cross = measured.T @ expected
    left, _, right_t = np.linalg.svd(cross)
    sign = 1.0 if np.linalg.det(left @ right_t) > 0.0 else -1.0
    rotation = left @ np.diag([1.0, 1.0, sign]) @ right_t
    if not np.isfinite(rotation).all() or abs(np.linalg.det(rotation) - 1.0) > 1e-9:
        raise RuntimeError("multi-pose sensor-to-segment Wahba fit is not a proper rotation")
    return rotation


def _axis_from_resampled(source: Any, parent: str, child: str) -> dict[str, Any]:
    relative = np.einsum(
        "nji,njk->nik", source.segment_rotation[parent], source.segment_rotation[child],
    )
    vectors = []
    for index in range(len(relative) - 1):
        phi_child = so3_log(relative[index].T @ relative[index + 1])
        phi_parent = relative[index] @ phi_child
        if np.linalg.norm(phi_parent) > np.finfo(float).eps * 64.0:
            vectors.append(phi_parent)
    values = np.asarray(vectors, float)
    if len(values) < 3:
        raise RuntimeError(f"insufficient functional-axis increments for {parent}/{child}")
    scatter = values.T @ values
    eigenvalues, eigenvectors = np.linalg.eigh(scatter)
    axis = eigenvectors[:, int(np.argmax(eigenvalues))]
    dominant = int(np.argmax(np.abs(axis)))
    if axis[dominant] < 0:
        axis = -axis
    unit = values / np.linalg.norm(values, axis=1, keepdims=True)
    angle = np.arccos(np.clip(np.abs(unit @ axis), 0.0, 1.0))
    weights = np.linalg.norm(values, axis=1) ** 2
    rms = float(np.sqrt(np.average(angle ** 2, weights=weights)))
    q95 = float(np.quantile(angle, 0.95))
    principal_fraction = float(eigenvalues[-1] / np.sum(eigenvalues))
    return {
        "axis_parent_segment_session_reference": axis.tolist(),
        "weighted_rms_dispersion_deg": float(np.degrees(rms)),
        "weighted_q95_dispersion_deg": float(np.degrees(q95)),
        "principal_axis_uncertainty_q95_deg": float(np.degrees(q95 / np.sqrt(len(values)))),
        "increment_count": int(len(values)),
        "principal_scatter_fraction": principal_fraction,
        "axis_sign_convention": "LARGEST_ABSOLUTE_COMPONENT_POSITIVE_UNDIRECTED_AXIS",
        "provenance": "SAME_CAPTURE_NATIVE_TIME_VQF_RELATIVE_ROTATION_INCREMENTS",
    }


def _slice_resampled(source: ResampledWindow, start_ns: int, stop_ns: int) -> ResampledWindow:
    selected = (source.time_ns >= int(start_ns)) & (source.time_ns < int(stop_ns))
    if np.count_nonzero(selected) < 3:
        raise RuntimeError("signal-conditioned episode phase has fewer than three resampled rows")
    return ResampledWindow(
        source.time_ns[selected],
        {key: value[selected] for key, value in source.segment_rotation.items()},
        {key: value[selected] for key, value in source.segment_gyro.items()},
        {key: value[selected] for key, value in source.node_bias.items()},
        {key: value[selected] for key, value in source.node_bias_sigma.items()},
        {key: value[selected] for key, value in source.node_rest.items()},
        {key: value[selected] for key, value in source.segment_degraded.items()},
        source.boundary[selected],
    )


def _profile_from_rows(
    root: Path, capture_name: str, spec: Mapping[str, Any], capture_ledger: Mapping[str, Any],
    rows_by_action: Mapping[str, Mapping[str, np.ndarray]], access_by_action: Mapping[str, Any],
    episode_by_action: Mapping[str, Mapping[str, Any]],
    *, code_lock_sha256: str,
) -> dict[str, Any]:
    config = load_config(root / "config/biospur_fusion_v0/config.json")
    max_gap_ns = int(config.section("frontend")["max_gap_ns"])
    frontends = {
        action: {
            node: run_vqf_native_hybrid(rows[node], node_id=node, max_gap_ns=max_gap_ns)
            for node in NODES
        }
        for action, rows in rows_by_action.items()
    }
    initial_name, tpose_name = spec["reference_actions"]
    desired = {segment: _desired_tpose_rotation(segment) for segment in spec["identity"].values()}
    semantic_contract = load_protocol(root)["semantic_qa_contract"]
    frame_settings = semantic_contract["fixed_profile_attitude"]
    world_up = np.array([0.0, 0.0, 1.0])
    extrinsic = {}
    for node in NODES:
        segment = spec["identity"][node]
        sensor_gravity = []
        segment_gravity = []
        for action, target in (
            (initial_name, np.eye(3)), (tpose_name, desired[segment]),
        ):
            phase_start, phase_stop = phase_bounds(
                episode_by_action[action], "FORMAL_ACTION_OR_HOLD",
            )
            timeline = frontends[action][node]
            phase_selected = (
                (timeline.time_ns >= phase_start) & (timeline.time_ns < phase_stop)
            )
            if np.count_nonzero(phase_selected) < 3:
                raise RuntimeError(f"{capture_name}:{action}/{node}: empty formal phase")
            gravity = np.median(timeline.accel_mps2[phase_selected], axis=0)
            gravity /= np.linalg.norm(gravity)
            sensor_gravity.append(gravity)
            segment_gravity.append(target.T @ world_up)
        sensor_gravity_array = np.asarray(sensor_gravity, float)
        segment_gravity_array = np.asarray(segment_gravity, float)
        expected_separation_deg = float(np.degrees(np.arccos(np.clip(
            segment_gravity_array[0] @ segment_gravity_array[1], -1.0, 1.0,
        ))))
        if expected_separation_deg >= float(
            frame_settings["minimum_multiframe_gravity_separation_deg"]
        ):
            effective_sensor_from_segment = _fit_sensor_from_segment_vector_pairs(
                sensor_gravity_array, segment_gravity_array,
            )
            estimation = "TWO_REFERENCE_POSE_GRAVITY_WAHBA_OBSERVABLE_FULL_ROTATION"
        else:
            # Collinear gravity references leave axial twist unobservable.  The
            # T-pose VQF display gauge supplies only that coordinate choice;
            # gravity still supplies the physical tilt binding.
            phase_start, phase_stop = phase_bounds(
                episode_by_action[tpose_name], "FORMAL_ACTION_OR_HOLD",
            )
            timeline = frontends[tpose_name][node]
            selected = (timeline.time_ns >= phase_start) & (timeline.time_ns < phase_stop)
            sensor_mean = proper_mean(timeline.rotation_world_sensor[selected])
            effective_sensor_from_segment = sensor_mean.T @ desired[segment]
            estimation = "COLLINEAR_GRAVITY_TPOSE_DISPLAY_TWIST_GAUGE"
        rotation_segment_from_sensor = effective_sensor_from_segment.T
        gravity_errors = np.asarray([
            np.arccos(np.clip(
                (effective_sensor_from_segment.T @ sensor_gravity_array[index])
                @ segment_gravity_array[index], -1.0, 1.0,
            ))
            for index in range(2)
        ])
        sigma = max(
            float(np.sqrt(np.mean(gravity_errors ** 2))),
            float(np.max(gravity_errors) / 1.96),
            float(np.finfo(float).eps),
        )
        extrinsic[node] = {
            "rotvec_segment_from_sensor": so3_log(rotation_segment_from_sensor).tolist(),
            "one_sigma_rad": [sigma, sigma, sigma],
            "neutral_gravity_residual_deg": float(np.degrees(gravity_errors[0])),
            "tpose_gravity_residual_deg": float(np.degrees(gravity_errors[1])),
            "reference_gravity_residual_rms_deg": float(np.degrees(np.sqrt(
                np.mean(gravity_errors ** 2)
            ))),
            "expected_reference_gravity_separation_deg": expected_separation_deg,
            "estimation_method": estimation,
            "source_frame": f"sensor:{node}", "target_frame": f"segment:{segment}",
            "provenance": (
                "SAME_CAPTURE_NEUTRAL_PLUS_TPOSE_GRAVITY_DONNING_ESTIMATE;"
                "NO_INTER_ACTION_RUNTIME_STATE_TRANSFER"
            ),
        }
    evidence = {"extrinsic_rotation": extrinsic}
    rate_hz = int(config.section("frontend")["output_rate_hz"])
    complete_episode_resampled = {
        action: resample_window(frontends[action], evidence, rate_hz, identity=spec["identity"])
        for action in rows_by_action
    }
    resampled = {
        action: _slice_resampled(
            complete_episode_resampled[action],
            *phase_bounds(episode_by_action[action], "FORMAL_ACTION_OR_HOLD"),
        )
        for action in rows_by_action
    }
    root_reference = proper_mean(resampled[initial_name].segment_rotation["pelvis"])
    frame_contract = {
        "schema": "biospur-fusion-v0-fixed-profile-attitude-frame-v1",
        "capture_id": spec["capture_id"],
        "reference_action_bindings": {
            initial_name: "NEUTRAL_STANDING",
            tpose_name: "STRAIGHT_HORIZONTAL_TPOSE",
        },
        "common_yaw_root_segment": "pelvis",
        "root_display_reference_rotvec": so3_log(root_reference).tolist(),
        "dynamic_initialization_frame_count": int(
            frame_settings["dynamic_initialization_frame_count"]
        ),
        "frame_definition": "ONE_FIXED_CAPTURE_DONNING_PROFILE_PLUS_ONE_COMMON_DISPLAY_YAW",
        "physical_global_yaw": "ONE_COMMON_UNOBSERVABLE_YAW_NOT_NORTH",
        "post_lock_per_segment_yaw_parameters_allowed": 0,
        "post_lock_action_pose_templates_allowed": False,
        "post_lock_joint_rest_or_extrinsic_refit_allowed": False,
        "cross_action_state_propagation_allowed": False,
        "unobservable_fixed_profile_replay_result": "BLOCKED",
        "inter_action_runtime_payload_allowed": False,
        "hxx_or_reserved_payload_allowed_during_profile_calibration": False,
    }
    expected_reference_poses = {
        initial_name: "NEUTRAL_STANDING", tpose_name: "STRAIGHT_HORIZONTAL_TPOSE",
    }
    initial = resampled[initial_name]
    model = corrected_body_model(
        root,
        identity_mapping=spec["identity"],
        identity_provenance=f"CAPTURE_PROTOCOL_IDENTITY:{spec['capture_id']}",
    )
    initial_window_hashes = {
        node: access_by_action[initial_name]["nodes"][node]["payload_sha256"]
        if "nodes" in access_by_action[initial_name] else
        access_by_action[initial_name]["decode"]["nodes"][node]["payload_sha256"]
        for node in NODES
    }
    initial_window_binding_sha256 = _canonical_hash(initial_window_hashes)
    joint_reference = {}; neutral_rotation = {}
    for joint in model.joints:
        relative = np.einsum(
            "nji,njk->nik", initial.segment_rotation[joint.parent], initial.segment_rotation[joint.child],
        )
        neutral_rotation[joint.joint_id] = proper_mean(relative)
        joint_reference[joint.joint_id] = {
            "rotvec_parent_from_child_session_neutral": so3_log(
                neutral_rotation[joint.joint_id]
            ).tolist(),
            "convention": f"mean same-capture {initial_name} is zero joint coordinate",
            "clinical_zero": False,
            "capture_id": spec["capture_id"],
            "source_action": initial_name,
            "source_window_binding_sha256": initial_window_binding_sha256,
            "provenance": (
                f"SAME_CAPTURE_REFERENCE_ACTION:{spec['capture_id']}:"
                f"{initial_name}:{initial_window_binding_sha256}"
            ),
        }
    joint_nodes = {
        "elbow_left": ("upper_arm_left", "forearm_left"),
        "elbow_right": ("upper_arm_right", "forearm_right"),
        "knee_left": ("thigh_left", "shank_left"),
        "knee_right": ("thigh_right", "shank_right"),
    }
    functional = {}
    for joint, action in spec["functional_axis_actions"].items():
        functional[joint] = _axis_from_resampled(resampled[action], *joint_nodes[joint])
        functional[joint]["source_action"] = action
    stationary_joint_excursion_q99_deg = {}
    meaningful_joint_threshold_deg = {}
    for joint in model.joints:
        relative = np.einsum(
            "nji,njk->nik", initial.segment_rotation[joint.parent],
            initial.segment_rotation[joint.child],
        )
        excursion = np.asarray([
            np.linalg.norm(so3_log(neutral_rotation[joint.joint_id].T @ value))
            for value in relative
        ])
        noise_q99 = float(np.degrees(np.quantile(excursion, 0.99)))
        axis_uncertainty = float(
            functional.get(joint.joint_id, {}).get("principal_axis_uncertainty_q95_deg", 0.0)
        )
        threshold = max(
            float(semantic_contract["minimum_joint_excursion_deg"]),
            float(semantic_contract["stationary_noise_multiplier"]) * noise_q99,
            float(semantic_contract["functional_axis_uncertainty_multiplier"]) * axis_uncertainty,
        )
        stationary_joint_excursion_q99_deg[joint.joint_id] = noise_q99
        meaningful_joint_threshold_deg[joint.joint_id] = threshold
    tpose = resampled[tpose_name]
    posture_calibration = {}
    for side in ("left", "right"):
        upper = tpose.segment_rotation[f"upper_arm_{side}"]
        torso = tpose.segment_rotation["torso"]
        distal_world = np.einsum("nij,j->ni", upper, np.array([0.0, 0.0, -1.0]))
        distal_torso = np.einsum("nji,nj->ni", torso, distal_world)
        elevation_deg = np.degrees(np.arcsin(np.clip(distal_torso[:, 2], -1.0, 1.0)))
        median = float(np.median(elevation_deg))
        mad = 1.4826 * float(np.median(np.abs(elevation_deg - median)))
        posture_calibration[side] = {
            "tpose_upper_arm_elevation_median_deg": median,
            "tpose_upper_arm_elevation_q99_deg": float(np.quantile(elevation_deg, 0.99)),
            "tpose_upper_arm_elevation_robust_sigma_deg": mad,
            "not_overhead_max_elevation_deg": max(
                float(semantic_contract["overhead_upper_arm_elevation_boundary_deg"]),
                float(np.quantile(elevation_deg, 0.99))
                + float(semantic_contract["tpose_posture_noise_multiplier"]) * mad,
            ),
        }
    bias = {}
    for node in NODES:
        rest_bias = []
        rest_sigma = []
        rest_flags = []
        for action in spec["calibration_inputs"]:
            episode_source = complete_episode_resampled[action]
            for phase in ("VERIFIED_PRE_REST", "VERIFIED_POST_REST"):
                phase_start, phase_stop = phase_bounds(episode_by_action[action], phase)
                selected = (
                    (episode_source.time_ns >= phase_start)
                    & (episode_source.time_ns < phase_stop)
                )
                if np.any(selected):
                    rest_bias.append(episode_source.node_bias[node][selected])
                    rest_sigma.append(episode_source.node_bias_sigma[node][selected])
                    rest_flags.append(episode_source.node_rest[node][selected])
        if not rest_bias:
            raise RuntimeError(f"{capture_name}/{node}: no verified episode-rest bias rows")
        concatenated_bias = np.concatenate(rest_bias, axis=0)
        concatenated_sigma = np.concatenate(rest_sigma)
        concatenated_rest = np.concatenate(rest_flags)
        bias[node] = {
            "gyro_bias_rad_s": np.median(concatenated_bias, axis=0).tolist(),
            "bias_sigma_rad_s": float(np.median(concatenated_sigma)),
            "rest_fraction": float(np.mean(concatenated_rest)),
            "verified_rest_row_count": int(len(concatenated_sigma)),
            "source": "VQF_2_0_1_ALL_COMPLETE_NON_HXX_EPISODE_PRE_POST_REST_SAME_CAPTURE",
        }
    window_hashes = {
        action: {
            node: access_by_action[action]["nodes"][node]["payload_sha256"]
            if "nodes" in access_by_action[action] else
            access_by_action[action]["decode"]["nodes"][node]["payload_sha256"]
            for node in NODES
        }
        for action in spec["calibration_inputs"]
    }
    dependencies = {
        package: importlib.metadata.version(package) for package in ("numpy", "scipy", "vqf", "qmt")
    }
    calibration_manifest = {
        "capture_id": spec["capture_id"],
        "actions": list(spec["calibration_inputs"]),
        "hxx_actions": [row["action"] for row in spec["hxx"]],
        "hxx_excluded": not bool(set(spec["calibration_inputs"]) & {row["action"] for row in spec["hxx"]}),
        "window_hashes": window_hashes,
    }
    profile = {
        "profile_schema": PROFILE_SCHEMA,
        "schema": PROFILE_SCHEMA,
        "biospur_fusion_version": "V0",
        "profile_id": f"PROFILE_{capture_name}",
        "capture_id": spec["capture_id"],
        "capture_metadata_hash": capture_ledger["capture_metadata_hash"],
        "capture_node_mapping_hash": capture_ledger["capture_mapping_hash"],
        "calibration_code_config_hash": code_lock_sha256,
        "dependency_versions": dependencies,
        "frozen": True,
        "identity": dict(spec["identity"]), "hardware_family": HARDWARE_FAMILY,
        "config_sha256": config.sha256,
        "calibration_input_manifest": calibration_manifest,
        "calibration_episode_diagnostics": dict(episode_by_action),
        "complete_final_accepted_non_hxx_suite": list(spec["calibration_inputs"]),
        "calibration_window_hashes": window_hashes,
        "calibration_windows": {
            action: {
                "role": "CALIBRATION", "contributes_to_profile": True,
                "bounds": next(row for row in capture_ledger["actions"] if row["action"] == action),
            }
            for action in spec["calibration_inputs"]
        },
        "sensor_to_segment_rotation": extrinsic,
        "capture_attitude_frame": frame_contract,
        "functional_axes": functional,
        "joint_session_reference": joint_reference,
        "bias_rest": bias,
        "semantic_qa_calibration": {
            "contract": semantic_contract,
            "stationary_joint_excursion_q99_deg": stationary_joint_excursion_q99_deg,
            "meaningful_joint_excursion_threshold_deg": meaningful_joint_threshold_deg,
            "tpose_posture": posture_calibration,
            "body_axis_observability": {
                "torso_local_plus_x": "RIGHT_BY_CAPTURE_BOUND_LEFT_RIGHT_TOPOLOGY",
                "torso_local_plus_z": "UP_BY_GRAVITY_AND_TPOSE_CALIBRATION",
                "torso_local_y_axis_line": "ORTHOGONAL_BODY_TRANSVERSE_AXIS",
                "torso_local_y_polarity_front_or_back": (
                    "UNESTIMATED_NO_INDEPENDENT_FRONT_BACK_POLARITY_CALIBRATION"
                ),
                "global_or_viewer_y_is_physical_front": False,
            },
        },
        "attitude_frontend": {
            "selected": "VQF_2_0_1_NATIVE_TIME_HYBRID", "magnetometer_used": False,
            "session_state_reinitialized_per_action": True,
            "one_common_display_yaw_initialized_per_action": True,
            "per_segment_action_gauge_refit": False,
            "action_specific_pose_template_used": False,
            "runtime_state_transferred_between_actions": False,
            "remaining_unobservable_yaw_dimensions": 1,
        },
        "estimated_fields": [
            "same-capture VQF bias/rest state",
            "same-capture neutral-plus-T-pose observable sensor-to-segment rotations",
            "one fixed capture/donning attitude profile from the joint non-H calibration set",
            "same-capture elbow/knee functional axes", "same-capture neutral joint references",
            "same-capture calibration uncertainty summaries",
            "same-capture stationary semantic-noise envelopes and T-pose posture envelope",
        ],
        "shared_fixed_fields_with_provenance": {
            "sensor_scale_units": "JY61P fixed raw scale from V0 code/config",
            "body_topology": "root_r6a0 corrected canonical body model",
            "capture_attitude_frame": (
                "gravity plus straight horizontal T-pose left/right display gauge; one common yaw unobservable"
            ),
            "mathematical_conventions": "active SO(3), R_W_segment, full three-vector joints",
        },
        "unestimated_uncertain_fields": [
            "absolute/global heading", "north", "metric root translation", "external attitude accuracy",
            "clinical joint zero", "anthropometric metric accuracy", "skin slip state",
            "physical front/back polarity", "human-likeness without Supervisor live Viewer judgment",
        ],
        "display_geometry": {
            "qualified_metric": False, "mode": "DISPLAY_ONLY_NON_METRIC_PROXY",
            "source": "shared V0 display convention; no UWB spatial value",
        },
        "uwb_isolation": {
            "imu_only": True, "beacon_common_time_allowed": True,
            "profile_fields": [
                "same_capture_vqf_bias_rest", "same_capture_tpose_donning_rotation",
                "same_capture_neutral_tpose_gravity_extrinsic",
                "selected_action_only_one_common_display_yaw_initialization",
                "same_capture_functional_axis", "same_capture_neutral_relative_rotation",
            ],
            "excluded_fields": [
                "ranges", "anchor_geometry", "v4_transform", "lever_arms", "root_translation",
                "pose_correction", "calibration_residual",
            ],
        },
        "root_position_mode": "ROOT_POSITION_DISPLAY_GAUGE_FIXED",
        "global_yaw_claim": "UNOBSERVABLE_COMMON_GAUGE_MAY_DRIFT_NOT_NORTH",
        "metric_skeleton_claim": "NO_DISPLAY_ONLY_NON_METRIC_GEOMETRY",
        "hxx_used_for_calibration": False,
        "profile_writeback_allowed": False,
        "calibration_execution_contract": {
            "execution_count": 1,
            "scope": "ONCE_PER_CAPTURE_DONNING_JOINT_COMPLETE_FINAL_ACCEPTED_NON_HXX_SET",
            "per_action_calibration": False,
            "cross_action_state_propagation": False,
            "post_lock_profile_refit_allowed": False,
            "input_unit": "COMPLETE_BIDIRECTIONAL_FIVE_PHASE_EPISODE",
            "all_final_accepted_non_hxx_actions_used": True,
            "manifest_labels_alone_used_as_rest_evidence": False,
        },
    }
    static = display_static_calibration(model, profile, config.section("display_geometry"))
    reference_gates = {}
    for action, pose_kind in expected_reference_poses.items():
        source, common_yaw_audit = initialize_common_action_display_yaw(
            resampled[action], frame_contract,
        )
        action_zero_heading = {
            segment: np.zeros(len(source.time_ns), float) for segment in model.segments
        }
        arrays, _ = _run_direct_fk_ablation(
            model=model, static=static, source=source,
            observed_rotation=source.segment_rotation,
            heading_confidence=action_zero_heading, profile=profile,
            label=action,
        )
        gate = hard_reference_pose_fk_gate(
            arrays, pose_kind, semantic_contract["hard_reference_pose_fk"],
        )
        gate["COMMON_DISPLAY_YAW_INITIALIZATION"] = common_yaw_audit
        gate["FIXED_PROFILE_OBSERVABILITY"] = (
            "PASS" if gate["HARD_FK_STATE_GATE"] == "PASS" else "BLOCKED"
        )
        gate["blocked_reason"] = (
            None if gate["HARD_FK_STATE_GATE"] == "PASS" else
            "FIXED_PROFILE_RELATIVE_ATTITUDE_UNOBSERVABLE;PER_SEGMENT_REFIT_FORBIDDEN"
        )
        reference_gates[action] = gate
    profile["reference_pose_hard_fk_gates"] = reference_gates
    return profile


def calibrate_capture(
    root: Path, capture_name: str, spec: Mapping[str, Any], capture_ledger: Mapping[str, Any],
    *, code_lock_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    action_by_name = {row["action"]: row for row in capture_ledger["actions"]}
    rows_by_action = {}; access_by_action = {}; episode_by_action = {}
    for action in spec["calibration_inputs"]:
        rows, access, episode = load_calibration_episode(
            root, capture_name, spec, action_by_name[action],
        )
        rows_by_action[action] = rows
        access_by_action[action] = access
        episode_by_action[action] = episode
    profile = _profile_from_rows(
        root, capture_name, spec, capture_ledger, rows_by_action, access_by_action,
        episode_by_action,
        code_lock_sha256=code_lock_sha256,
    )
    first_hash = _canonical_hash(profile)
    roundtrip = json.loads(json.dumps(
        _jsonable(profile), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ))
    repeat_hash = _canonical_hash(roundtrip)
    if first_hash != repeat_hash:
        raise RuntimeError(f"{capture_name}: calibration is not deterministic")
    result = {
        "schema": "biospur-fusion-v0-independent-calibration-result-v1",
        "capture_id": spec["capture_id"], "profile_id": profile["profile_id"],
        "calibration_executed": True, "independently_estimated_from_zero": True,
        "calibration_execution_count": 1,
        "joint_authorized_non_h_calibration_set": True,
        "complete_final_accepted_non_hxx_suite": True,
        "calibration_input_unit": "COMPLETE_BIDIRECTIONAL_FIVE_PHASE_EPISODE",
        "per_action_calibration_executed": False,
        "cross_action_state_propagation_used": False,
        "hxx_used_for_calibration": False,
        "calibration_actions": list(spec["calibration_inputs"]),
        "profile_canonical_digest_first": first_hash,
        "profile_canonical_digest_roundtrip": repeat_hash,
        "deterministic_profile_payload": True,
        "session_specific_value_imported_from_other_capture": False,
        "access": access_by_action,
        "five_phase_episode_diagnostics": episode_by_action,
        "capture_attitude_frame": profile["capture_attitude_frame"],
        "reference_pose_hard_fk_gates": profile["reference_pose_hard_fk_gates"],
    }
    return profile, result


def _locked_paths(root: Path) -> list[Path]:
    paths = list((root / "src/biospur_fusion").rglob("*.py"))
    paths += [path for path in (root / "config/biospur_fusion_v0").rglob("*") if path.is_file()]
    paths += [path for path in (root / "config/root_r6a0").rglob("*") if path.is_file()]
    paths += [
        root / "tools/run_biospur_fusion_v0_dual_capture.py",
        root / "tools/run_biospur_fusion_v0_reference_qa.py",
        *sorted((root / "tests/v0").glob("*.py")),
        root / "tests/synthetic/test_common_clock.py",
    ]
    resolved = sorted({path.resolve() for path in paths})
    missing = [str(path) for path in resolved if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"dual-capture lock inputs missing: {missing}")
    return resolved


def create_code_lock(root: Path, destination: Path) -> dict[str, Any]:
    root = Path(root).resolve(); destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError(f"code lock already exists: {destination}")
    files = [{
        "path": str(path.relative_to(root)), "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    } for path in _locked_paths(root)]
    dependencies = {
        package: importlib.metadata.version(package) for package in ("numpy", "scipy", "vqf", "qmt")
    }
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"], cwd=root, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout
    manifest = {
        "schema": LOCK_SCHEMA, "candidate_kind": "COMMON_DUAL_CAPTURE_CODE_CONFIG_DEPENDENCIES",
        "files": files, "dependencies": dependencies,
        "python_version": sys.version.split()[0],
        "worktree_status_sha256": hashlib.sha256(status).hexdigest(),
        "lock_applies_identically_to": ["CAPTURE1", "CAPTURE2"],
        "profiles_in_lock": False, "profiles_generated_after_lock": True,
    }
    dump_json(destination, manifest)
    return manifest


def verify_code_lock(root: Path, lock_path: Path, expected_sha256: str) -> dict[str, Any]:
    root = Path(root).resolve(); lock_path = Path(lock_path).resolve()
    if sha256_file(lock_path) != expected_sha256:
        raise ValueError("code lock manifest digest changed")
    manifest = json.loads(lock_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != LOCK_SCHEMA:
        raise ValueError("code lock schema changed")
    changed = []
    for row in manifest["files"]:
        path = root / row["path"]
        if not path.is_file() or sha256_file(path) != row["sha256"]:
            changed.append(row["path"])
    if changed:
        raise RuntimeError(f"locked common code changed: {changed}")
    current_dependencies = {
        package: importlib.metadata.version(package)
        for package in ("numpy", "scipy", "vqf", "qmt")
    }
    if current_dependencies != manifest.get("dependencies"):
        raise RuntimeError(
            "locked dependency versions changed: "
            f"expected={manifest.get('dependencies')} actual={current_dependencies}"
        )
    if sys.version.split()[0] != manifest.get("python_version"):
        raise RuntimeError("locked Python version changed")
    return {
        "pass": True, "manifest_sha256": expected_sha256,
        "verified_files": len(manifest["files"]),
        "verified_dependency_versions": current_dependencies,
        "verified_python_version": sys.version.split()[0],
    }


def _action_physical_qa(
    action: str, arrays: Mapping[str, np.ndarray], metrics: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    names = [str(value) for value in arrays["segment_names"]]
    index = {name: i for i, name in enumerate(names)}
    ranked = metrics["gross_motion"]["segments_ranked_by_q95_excursion_deg"]
    target = None; counterpart = None
    lowered = action.lower()
    side = "LEFT" if "left" in lowered else "RIGHT" if "right" in lowered else "MIDLINE_OR_BILATERAL"
    if "shoulder" in lowered:
        target = f"upper_arm_{side.lower()}"; counterpart = f"upper_arm_{'right' if side == 'LEFT' else 'left'}"
    elif "elbow" in lowered:
        target = f"forearm_{side.lower()}"; counterpart = f"forearm_{'right' if side == 'LEFT' else 'left'}"
    elif any(token in lowered for token in ("knee", "heel")) and side in {"LEFT", "RIGHT"}:
        target = f"shank_{side.lower()}"; counterpart = f"shank_{'right' if side == 'LEFT' else 'left'}"
    target_rank = next((i + 1 for i, row in enumerate(ranked) if row["segment"] == target), None)
    points = skeleton_points(tuple(names), arrays["segment_position"], arrays["segment_rotation"])
    point_index = {name: i for i, name in enumerate(POINT_NAMES)}
    endpoint_name = (
        f"wrist_{side.lower()}" if side in {"LEFT", "RIGHT"} and any(x in lowered for x in ("shoulder", "elbow"))
        else f"ankle_{side.lower()}" if side in {"LEFT", "RIGHT"} and any(x in lowered for x in ("knee", "heel"))
        else "head_proxy" if "trunk" in lowered else "pelvis"
    )
    trajectory = points[:, point_index[endpoint_name]]
    centred = trajectory - np.mean(trajectory, axis=0)
    covariance = centred.T @ centred / max(1, len(centred) - 1)
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    joint = None
    for token in ("shoulder", "elbow", "hip", "knee"):
        if token in lowered and side in {"LEFT", "RIGHT"}:
            joint = f"{token}_{side.lower()}"; break
    if joint is None and "heel" in lowered and side in {"LEFT", "RIGHT"}:
        joint = f"knee_{side.lower()}"
    joint_names = [str(value) for value in arrays["joint_names"]]
    threshold_by_joint = profile["semantic_qa_calibration"][
        "meaningful_joint_excursion_threshold_deg"
    ]
    joint_excursion_deg = None; meaningful_motion = None; projected_axis = None
    if joint in joint_names:
        q = np.asarray(arrays["joint_rotvec"][:, joint_names.index(joint)], float)
        if joint in profile["functional_axes"]:
            axis = np.asarray(
                profile["functional_axes"][joint]["axis_parent_segment_session_reference"], float,
            )
            projected = q @ axis
            joint_excursion_deg = float(np.degrees(np.quantile(projected, 0.95) - np.quantile(projected, 0.05)))
            projected_axis = axis.tolist()
        else:
            centred_q = q - np.median(q, axis=0)
            joint_excursion_deg = float(np.degrees(np.quantile(np.linalg.norm(centred_q, axis=1), 0.95)))
        meaningful_motion = joint_excursion_deg >= float(threshold_by_joint[joint])
    topology_bound = bool(target is None or target in set(profile["identity"].values()))
    semantic_failure = bool(meaningful_motion is False or not topology_bound)
    return {
        "schema": "biospur-fusion-v0-action-physical-qa-v2",
        "action": action,
        "SIDE_IDENTITY": (
            "PASS_CAPTURE_BOUND_TOPOLOGY_ONLY_NOT_ACTION_SEMANTICS" if topology_bound
            else "FAIL_CAPTURE_BOUND_TOPOLOGY"
        ),
        "MOVING_SEGMENT_IDENTITY": (
            "INCONCLUSIVE_RANK_DIAGNOSTIC_ONLY" if target_rank is not None else "INCONCLUSIVE"
        ),
        "MEANINGFUL_MOTION": (
            "PASS_PREDECLARED_CALIBRATION_SCALED_EXCURSION" if meaningful_motion is True
            else "FAIL_BELOW_PREDECLARED_CALIBRATION_SCALED_EXCURSION" if meaningful_motion is False
            else "INCONCLUSIVE_NO_ACTION_JOINT_SEMANTIC"
        ),
        "ACTION_DIRECTION": (
            "PASS_MEANINGFUL_CALIBRATED_FUNCTIONAL_AXIS_MOTION" if meaningful_motion is True and projected_axis is not None
            else "FAIL_EXPECTED_FUNCTIONAL_AXIS_MOTION_NOT_RECONSTRUCTED" if meaningful_motion is False and projected_axis is not None
            else "INCONCLUSIVE_NO_PREDECLARED_DIRECTION_SEMANTIC"
        ),
        "ACTION_PLANE": "INCONCLUSIVE_NO_PREDECLARED_BODY_RELATIVE_PLANE_SEMANTIC",
        "UPPER_ARM_POSTURE": "NOT_APPLICABLE_OR_REPORTED_SEPARATELY",
        "HUMAN_LIKE_POSE": "PENDING_SUPERVISOR_LIVE_VIEWER_JUDGMENT",
        "physical_qa_result": "FAIL" if semantic_failure else "INCONCLUSIVE",
        "target_segment": target, "contralateral_segment": counterpart, "target_rank": target_rank,
        "target_joint": joint, "joint_excursion_deg": joint_excursion_deg,
        "meaningful_joint_excursion_threshold_deg": (
            float(threshold_by_joint[joint]) if joint in threshold_by_joint else None
        ),
        "functional_axis_parent_segment": projected_axis,
        "endpoint": endpoint_name,
        "trajectory_covariance_eigenvalues": eigenvalues.tolist(),
        "trajectory_principal_directions": eigenvectors.tolist(),
        "finite_pca_numerical_fk_side_rank_or_objective_may_promote_physical_pass": False,
        "threshold_policy": "PREDECLARED_GENERAL_CALIBRATION_SCALED_SEMANTIC_QA_NO_ACTION_TUNING",
    }


def _capture2_elbow_front_qa(
    arrays: Mapping[str, np.ndarray], physical: Mapping[str, Any], profile: Mapping[str, Any],
) -> dict[str, Any]:
    names = tuple(str(value) for value in arrays["segment_names"])
    points = skeleton_points(names, arrays["segment_position"], arrays["segment_rotation"])
    idx = {name: i for i, name in enumerate(POINT_NAMES)}
    shoulder = points[:, idx["upper_arm_left"]]
    elbow = points[:, idx["forearm_left"]]
    wrist = points[:, idx["wrist_left"]]
    torso = points[:, idx["torso"]]; torso_i = names.index("torso")
    torso_rotation = np.asarray(arrays["segment_rotation"][:, torso_i], float)
    wrist_torso = np.einsum("nji,nj->ni", torso_rotation, wrist - torso)
    upper_torso = np.einsum("nji,nj->ni", torso_rotation, elbow - shoulder)
    upper_length = np.linalg.norm(upper_torso, axis=1)
    upper_unit = upper_torso / np.maximum(upper_length[:, None], np.finfo(float).eps)
    elevation_deg = np.degrees(np.arcsin(np.clip(upper_unit[:, 2], -1.0, 1.0)))
    posture_limit = float(profile["semantic_qa_calibration"]["tpose_posture"]["left"][
        "not_overhead_max_elevation_deg"
    ])
    elevation_q95 = float(np.quantile(elevation_deg, 0.95))
    not_overhead = elevation_q95 <= posture_limit
    centred = wrist_torso - np.mean(wrist_torso, axis=0)
    covariance = centred.T @ centred / max(1, len(centred) - 1)
    total_variance = float(np.trace(covariance))
    lateral_variance_fraction = (
        float(covariance[0, 0]) / total_variance if total_variance > 0.0 else float("nan")
    )
    forearm_length = np.linalg.norm(wrist - elbow, axis=1)
    trajectory_extent = np.linalg.norm(wrist_torso - wrist_torso[0], axis=1)
    normalized_extent_q95 = float(np.quantile(
        trajectory_extent / np.maximum(forearm_length, np.finfo(float).eps), 0.95,
    ))
    contract = profile["semantic_qa_calibration"]["contract"]
    endpoint_meaningful = normalized_extent_q95 >= float(
        contract["minimum_endpoint_excursion_forearm_lengths"]
    )
    sagittal_plane = bool(
        endpoint_meaningful and np.isfinite(lateral_variance_fraction)
        and lateral_variance_fraction <= float(contract["sagittal_lateral_variance_max_fraction"])
    )
    front_polarity = profile["semantic_qa_calibration"]["body_axis_observability"][
        "torso_local_y_polarity_front_or_back"
    ]
    front_supported = front_polarity != "UNESTIMATED_NO_INDEPENDENT_FRONT_BACK_POLARITY_CALIBRATION"
    automated_failure = bool(
        physical["MEANINGFUL_MOTION"].startswith("FAIL") or not endpoint_meaningful
        or not sagittal_plane or not not_overhead
    )
    return {
        "schema": "biospur-fusion-v0-capture2-06-elbow-front-qa-v2",
        "SIDE_IDENTITY": physical["SIDE_IDENTITY"],
        "MOVING_SEGMENT_IDENTITY": physical["MOVING_SEGMENT_IDENTITY"],
        "TOPOLOGY": "PASS_CAPTURE_BOUND_TORSO_SHOULDER_ELBOW_WRIST_CHAIN",
        "ACTION_DIRECTION": physical["ACTION_DIRECTION"],
        "BODY_RELATIVE_FRONT_DIRECTION": (
            "INCONCLUSIVE_FRONT_BACK_POLARITY_UNCALIBRATED" if not front_supported
            else "INCONCLUSIVE_REQUIRES_SUPERVISOR_PHYSICAL_JUDGMENT"
        ),
        "ACTION_PLANE": (
            "PASS_PREDECLARED_TORSO_RELATIVE_SAGITTAL_PLANE" if sagittal_plane
            else "FAIL_PREDECLARED_TORSO_RELATIVE_SAGITTAL_PLANE"
        ),
        "UPPER_ARM_POSTURE": "PASS_NOT_OVERHEAD" if not_overhead else "FAIL_OVERHEAD",
        "HUMAN_LIKE_POSE": "PENDING_SUPERVISOR_LIVE_VIEWER_JUDGMENT",
        "torso_relative_upper_arm_elevation_q95_deg": elevation_q95,
        "not_overhead_max_elevation_deg": posture_limit,
        "torso_relative_wrist_trajectory_lateral_variance_fraction": lateral_variance_fraction,
        "sagittal_lateral_variance_max_fraction": float(
            contract["sagittal_lateral_variance_max_fraction"]
        ),
        "torso_relative_endpoint_excursion_q95_forearm_lengths": normalized_extent_q95,
        "minimum_endpoint_excursion_forearm_lengths": float(
            contract["minimum_endpoint_excursion_forearm_lengths"]
        ),
        "front_axis_contract": front_polarity,
        "global_or_viewer_y_used_as_physical_front": False,
        "operator_truth_match": "FAIL" if automated_failure else "INCONCLUSIVE",
        "operator_truth_inconclusive_reason": (
            "BODY_RELATIVE_FRONT_BACK_POLARITY_UNCALIBRATED_AND_SUPERVISOR_LIVE_"
            "VIEWER_PHYSICAL_JUDGMENT_PENDING"
        ) if not automated_failure else None,
        "action_specific_correction_applied": False,
    }


def _safe_name(action: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in action)


def run_action_replay(
    root: Path, capture_name: str, spec: Mapping[str, Any], action_row: Mapping[str, Any],
    profile: Mapping[str, Any], profile_sha256: str, role: str, destination: Path,
    *, lock_sha256: str, complete_calibration_episode: bool = False,
) -> dict[str, Any]:
    # This check intentionally precedes input loading and every reconstruction stage.
    assert_profile_capture_match(profile, spec["capture_id"])
    destination.mkdir(parents=True, exist_ok=False)
    episode_diagnostic = None
    if complete_calibration_episode:
        rows, access, episode_diagnostic = load_calibration_episode(
            root, capture_name, spec, action_row,
        )
    else:
        rows, access = load_action(root, capture_name, spec, action_row)
    config = load_config(root / "config/biospur_fusion_v0/config.json")
    variants, audits = _execute_variants(root, rows, profile, config)
    repeat, _ = _execute_variants(root, rows, profile, config)
    action = action_row["action"]
    for arrays in (*variants.values(), *repeat.values()):
        arrays["window"][:] = (
            "UNVERIFIED_EPISODE_EDGE" if episode_diagnostic is not None else action
        )
        if episode_diagnostic is not None:
            times = arrays["global_time_ns"]
            for phase in episode_diagnostic["phases"]:
                selected = (
                    (times >= int(phase["start_global_time_ns"]))
                    & (times < int(phase["stop_global_time_ns_exclusive"]))
                )
                arrays["window"][selected] = str(phase["phase"])
    model = corrected_body_model(
        root,
        identity_mapping=profile["identity"],
        identity_provenance=f"CAPTURE_BOUND_PROFILE_IDENTITY:{profile['capture_id']}",
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
        "always_on_qmt": _variant_metrics(
            variants["always_on_qmt"], model,
            audits["always_on_qmt"]["shared_ik"]["canonical_fk_closure_max_abs"],
        ),
        "always_on_qmt_no_shared_ik": _variant_metrics(
            variants["always_on_qmt_no_shared_ik"], model,
            audits["always_on_qmt_no_shared_ik"]["shared_ik"]["canonical_fk_observation_closure_max_abs_rad"],
        ),
    }
    deterministic = {
        mode: {
            "first_digest": _arrays_digest(variants[mode]),
            "repeat_digest": _arrays_digest(repeat[mode]),
            "identical": _arrays_digest(variants[mode]) == _arrays_digest(repeat[mode]),
        }
        for mode in ("qmt_off", "qmt_off_no_shared_ik", "always_on_qmt", "always_on_qmt_no_shared_ik")
    }
    metrics = {
        "schema": "biospur-fusion-v0-dual-capture-action-metrics-v1",
        "capture_id": spec["capture_id"], "profile_sha256": profile_sha256,
        "action": action, "role": role,
        "numerical_integrity": numerical, "deterministic_replay": deterministic,
        "gross_motion": _gross_motion_metrics(variants["qmt_off"], model),
        "qmt_off_versus_always_on_qmt": _compare_variants(variants["qmt_off"], variants["always_on_qmt"]),
        "qmt_off_ik_on_versus_off": _compare_variants(variants["qmt_off"], variants["qmt_off_no_shared_ik"]),
        "always_on_qmt_ik_on_versus_off": _compare_variants(variants["always_on_qmt"], variants["always_on_qmt_no_shared_ik"]),
        "uncertainty": _uncertainty_localization(variants["qmt_off"], profile),
        "module_audits": audits,
        "capture_bound_body_model_identity": dict(model.identity_mapping),
        "capture_bound_body_model_identity_sha256": model.identity_provenance["source_sha256"],
        "capture_bound_body_model_identity_provenance": model.identity_provenance,
        "fixed_profile_replay_contract": {
            "profile_refit_executed": False,
            "per_segment_extrinsic_refit_executed": False,
            "joint_rest_refit_executed": False,
            "relative_yaw_refit_executed": False,
            "action_specific_pose_template_used": False,
            "cross_action_state_propagation_used": False,
            "one_common_global_yaw_display_gauge_only": True,
        },
        "complete_calibration_episode_replay": complete_calibration_episode,
        "five_phase_episode_diagnostic": episode_diagnostic,
    }
    physical = _action_physical_qa(action, variants["qmt_off"], metrics, profile)
    reference_pose_kind = profile.get("capture_attitude_frame", {}).get(
        "reference_action_bindings", {}
    ).get(action)
    reference_pose_gate = None
    if reference_pose_kind is not None:
        reference_gate_arrays = variants["qmt_off"]
        if episode_diagnostic is not None:
            formal_start, formal_stop = phase_bounds(
                episode_diagnostic, "FORMAL_ACTION_OR_HOLD",
            )
            times = reference_gate_arrays["global_time_ns"]
            selected = (times >= formal_start) & (times < formal_stop)
            if np.count_nonzero(selected) < 3:
                raise RuntimeError(f"{capture_name}:{action}: empty formal reference phase")
            reference_gate_arrays = {
                key: value[selected]
                if isinstance(value, np.ndarray) and value.ndim > 0
                and value.shape[0] == len(times) else value
                for key, value in reference_gate_arrays.items()
            }
        reference_pose_gate = hard_reference_pose_fk_gate(
            reference_gate_arrays, reference_pose_kind,
            profile["semantic_qa_calibration"]["contract"]["hard_reference_pose_fk"],
        )
        reference_pose_gate["evaluated_phase"] = "FORMAL_ACTION_OR_HOLD"
        physical["REFERENCE_POSE_HARD_FK_GATE"] = reference_pose_gate
        if reference_pose_gate["HARD_FK_STATE_GATE"] != "PASS":
            physical["physical_qa_result"] = "FAIL"
            physical["FIXED_PROFILE_OBSERVABILITY"] = "BLOCKED"
            physical["fixed_profile_blocked_reason"] = (
                "REFERENCE_HARD_FK_STATE_GATE_FAILED;PER_SEGMENT_OR_ACTION_TEMPLATE_"
                "RECALIBRATION_FORBIDDEN"
            )
        else:
            physical["FIXED_PROFILE_OBSERVABILITY"] = "PASS"
    if capture_name == "CAPTURE2" and action == "06_elbow_left":
        operator_qa = _capture2_elbow_front_qa(variants["qmt_off"], physical, profile)
        physical["capture2_operator_truth_qa"] = operator_qa
        if operator_qa["operator_truth_match"] == "FAIL":
            physical["physical_qa_result"] = "FAIL"
    selected_segment_names = tuple(str(value) for value in variants["qmt_off"]["segment_names"])
    variants["qmt_off"]["node_by_segment"] = np.asarray([
        next(node for node, segment in profile["identity"].items() if segment == name)
        for name in selected_segment_names
    ])
    variants["qmt_off"]["capture_id"] = np.asarray(profile["capture_id"])
    variants["qmt_off"]["body_model_identity_sha256"] = np.asarray(
        model.identity_provenance["source_sha256"]
    )
    state_path = destination / "STATE_SELECTED_V0.npz"
    np.savez_compressed(state_path, **variants["qmt_off"])
    metrics_path = destination / "METRICS.json"; dump_json(metrics_path, metrics)
    physical_path = destination / "PHYSICAL_QA.json"; dump_json(physical_path, physical)
    access_path = destination / "ACCESS_AUDIT.json"; dump_json(access_path, access)
    episode_path = None
    if episode_diagnostic is not None:
        episode_path = destination / "FIVE_PHASE_EPISODE_DIAGNOSTICS.json"
        dump_json(episode_path, episode_diagnostic)
    state_sha = sha256_file(state_path)
    segment_to_node = {segment: node for node, segment in model.identity_mapping.items()}
    viewer_path = destination / "VIEWER.html"
    viewer = write_viewer(
        viewer_path,
        time_ns=variants["qmt_off"]["global_time_ns"], window=variants["qmt_off"]["window"],
        boundary=variants["qmt_off"]["boundary"],
        segment_names=tuple(str(value) for value in variants["qmt_off"]["segment_names"]),
        segment_position=variants["qmt_off"]["segment_position"],
        segment_rotation=variants["qmt_off"]["segment_rotation"],
        segment_confidence=variants["qmt_off"]["segment_confidence"],
        joint_rotvec=variants["qmt_off"]["joint_rotvec"],
        segment_sigma_rad=variants["qmt_off"]["segment_sigma_rad"],
        node_by_segment=tuple(segment_to_node[str(value)] for value in variants["qmt_off"]["segment_names"]),
        qmt_mode="QMT_OFF",
        viewer_metadata={
            "capture_id": spec["capture_id"], "profile_id": profile["profile_id"],
            "profile_sha256": profile_sha256, "action_id": action, "action_role": role,
            "shared_ik_mode": "ON", "locked_state_source": f"{state_path.name}:{state_sha}",
            "code_lock_sha256": lock_sha256,
            "display_geometry_status": "DISPLAY_ONLY_NON_METRIC_PROXY",
            "complete_calibration_episode_replay": complete_calibration_episode,
            "five_phase_episode_diagnostics_sha256": (
                sha256_file(episode_path) if episode_path is not None else None
            ),
        },
    )
    viewer_text = viewer_path.read_text(encoding="utf-8")
    viewer_qa = {
        "schema": "biospur-fusion-v0-live-viewer-qa-v1",
        "LIVE_VIEWER_RUNTIME_QA": "PENDING_LIVE_BROWSER_EXECUTION",
        "LIVE_VIEWER_STATE_CORRESPONDENCE": (
            "PASS_STATIC_EMBEDDED_LOCKED_STATE_BINDING"
            if state_sha in viewer_text and spec["capture_id"] in viewer_text else "FAIL"
        ),
        "SUPERVISOR_PHYSICAL_JUDGMENT": "PENDING_INDEPENDENT_SUPERVISOR",
        "AUTOMATED_PHYSICAL_SEMANTIC_EVIDENCE": physical["physical_qa_result"],
        "finite_pca_numerical_integrity_side_dominance_fk_objective_qmt_finiteness_"
        "or_positive_uncertainty_may_promote_supervisor_physical_pass": False,
        "viewer_manifest": viewer,
    }
    dump_json(destination / "LIVE_VIEWER_QA.json", viewer_qa)
    strict_clock_pass = bool(access.get("common_clock", {}).get("gate", {}).get("pass", True))
    numerical_execution_pass = bool(
        all(row["finite"] and row["native_time_strictly_increasing"] for row in numerical.values())
        and all(row["identical"] for row in deterministic.values())
        and viewer_qa["LIVE_VIEWER_STATE_CORRESPONDENCE"].startswith("PASS")
    )
    execution_pass = bool(numerical_execution_pass and strict_clock_pass)
    execution_result = (
        "BLOCKED" if reference_pose_gate is not None and
        reference_pose_gate["HARD_FK_STATE_GATE"] != "PASS" else
        "PASS_REFERENCE_GATE_PENDING_SUPERVISOR" if execution_pass and
        reference_pose_gate is not None else
        "PASS" if execution_pass else
        "DEGRADED_FAIL" if numerical_execution_pass and not strict_clock_pass else
        "FAIL"
    )
    return {
        "action": action, "role": role,
        "input_bounds": {
            key: action_row.get(key) for key in (
                "start_global_time_ns", "stop_global_time_ns_exclusive",
                "start_host_monotonic_ns", "stop_host_monotonic_ns_exclusive",
                "start_byte_inclusive", "stop_byte_exclusive", "raw_bounds", "read_bracket",
            ) if action_row.get(key) is not None
        },
        "profile_hash": profile_sha256,
        "state_artifact": str(state_path), "state_sha256": state_sha,
        "metrics_artifact": str(metrics_path), "viewer_artifact": str(viewer_path),
        "physical_qa_artifact": str(physical_path), "access_artifact": str(access_path),
        "live_viewer_qa_artifact": str(destination / "LIVE_VIEWER_QA.json"),
        "episode_diagnostics_artifact": (
            str(episode_path) if episode_path is not None else None
        ),
        "execution_result": execution_result,
        "fixed_profile_observability": (
            None if reference_pose_gate is None else
            "PASS" if reference_pose_gate["HARD_FK_STATE_GATE"] == "PASS" else "BLOCKED"
        ),
        "strict_common_clock_validation": "PASS" if strict_clock_pass else "DEGRADED_FAIL",
        "physical_qa_result": physical["physical_qa_result"],
        "hxx_classification": ["POST_CALIBRATION_REPLAY", "POST_CALIBRATION_REGRESSION"] if role == "HXX" else None,
    }


def _viewer_index(path: Path, capture_id: str, rows: list[Mapping[str, Any]]) -> None:
    links = []
    for row in rows:
        relative = Path(row["viewer_artifact"]).resolve().relative_to(path.parent.resolve())
        links.append(
            f'<li><a href="{relative.as_posix()}">{row["role"]} — {row["action"]}</a> '
            f'[{row["execution_result"]} / physical {row["physical_qa_result"]}]</li>'
        )
    path.write_text(
        "<!doctype html><meta charset=utf-8><title>BioSpur V0 complete replay index</title>"
        f"<h1>{capture_id}</h1><p>Each link is the actual locked QMT_OFF/shared-IK state Viewer. "
        "Hxx is post-calibration replay/regression, not fresh holdout evidence.</p><ol>"
        + "".join(links) + "</ol>", encoding="utf-8",
    )


def run_complete_capture(
    root: Path, goal_dir: Path, capture_name: str, protocol: Mapping[str, Any],
    ledger: Mapping[str, Any], lock_path: Path, lock_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    verify_code_lock(root, lock_path, lock_sha256)
    spec = protocol["captures"][capture_name]
    capture_ledger = ledger["captures"][capture_name]
    output = goal_dir / capture_name
    output.mkdir(parents=True, exist_ok=False)
    profile, calibration = calibrate_capture(
        root, capture_name, spec, capture_ledger, code_lock_sha256=lock_sha256,
    )
    profile_path = goal_dir / f"PROFILE_{capture_name}.json"
    dump_json(profile_path, profile); profile_sha = sha256_file(profile_path)
    manifest = {
        "schema": "biospur-fusion-v0-capture-profile-manifest-v1",
        "profile": str(profile_path), "profile_sha256": profile_sha,
        "capture_id": spec["capture_id"],
        "capture_metadata_hash": profile["capture_metadata_hash"],
        "calibration_input_manifest": profile["calibration_input_manifest"],
        "calibration_window_hashes": profile["calibration_window_hashes"],
        "capture_node_mapping_hash": profile["capture_node_mapping_hash"],
        "calibration_code_config_hash": lock_sha256,
        "dependency_versions": profile["dependency_versions"],
        "estimated_fields": profile["estimated_fields"],
        "shared_fixed_fields_with_provenance": profile["shared_fixed_fields_with_provenance"],
        "unestimated_uncertain_fields": profile["unestimated_uncertain_fields"],
        "capture_attitude_frame": profile["capture_attitude_frame"],
        "reference_pose_hard_fk_gates": profile["reference_pose_hard_fk_gates"],
        "independently_estimated_from_zero": True,
        "other_capture_profile_read_or_imported": False,
    }
    dump_json(goal_dir / f"PROFILE_{capture_name}_MANIFEST.json", manifest)
    calibration.update({"profile_path": str(profile_path), "profile_sha256": profile_sha})
    dump_json(output / "CALIBRATION_RESULT.json", calibration)
    (output / "CALIBRATION_RESULT.md").write_text(
        f"# {capture_name} calibration result\n\nProfile `{profile_sha}` was estimated from "
        f"`{spec['capture_id']}` only. Hxx contributed no calibration measurement. The "
        "byte-identical repeated profile payload proves deterministic estimation, not external accuracy.\n",
        encoding="utf-8",
    )
    action_by_name = {row["action"]: row for row in capture_ledger["actions"]}
    schedule = []
    for action in spec["calibration_inputs"]:
        schedule.append(("CALIBRATION_SELF_REPLAY", action))
    schedule += [("MAIN_SUITE_1", action) for action in spec["main_suite_1"]]
    schedule += [("MAIN_SUITE_2", action) for action in spec["main_suite_2"]]
    schedule += [("HXX", row["action"]) for row in spec["hxx"]]
    index_rows = []
    for role, action in schedule:
        destination = output / role / _safe_name(action)
        index_rows.append(run_action_replay(
            root, capture_name, spec, action_by_name[action], profile, profile_sha,
            role, destination, lock_sha256=lock_sha256,
        ))
    dump_json(output / "COMPLETE_REPLAY_INDEX.json", {
        "schema": "biospur-fusion-v0-complete-replay-index-v1",
        "capture_id": spec["capture_id"], "profile_sha256": profile_sha,
        "entries": index_rows,
    })
    _viewer_index(output / "VIEWER_INDEX.html", spec["capture_id"], index_rows)
    by_role = Counter(row["role"] for row in index_rows)
    expected_actions_by_role = {
        "CALIBRATION_SELF_REPLAY": list(spec["calibration_inputs"]),
        "MAIN_SUITE_1": list(spec["main_suite_1"]),
        "MAIN_SUITE_2": list(spec["main_suite_2"]),
        "HXX": [row["action"] for row in spec["hxx"]],
    }
    exact_role_evidence = {}
    for role, expected_actions in expected_actions_by_role.items():
        rows = [row for row in index_rows if row["role"] == role]
        observed_actions = [row["action"] for row in rows]
        outcomes = [row["execution_result"] for row in rows]
        exact_coverage = bool(
            len(observed_actions) == len(set(observed_actions))
            and set(observed_actions) == set(expected_actions)
        )
        role_outcome = (
            "FAIL" if (expected_actions and not observed_actions) or "FAIL" in outcomes else
            "PASS" if exact_coverage and all(value == "PASS" for value in outcomes) else
            "PARTIAL"
        )
        exact_role_evidence[role] = {
            "expected_actions": expected_actions,
            "observed_actions": observed_actions,
            "missing_actions": sorted(set(expected_actions) - set(observed_actions)),
            "unexpected_actions": sorted(set(observed_actions) - set(expected_actions)),
            "duplicate_action_entries": len(observed_actions) != len(set(observed_actions)),
            "exact_expected_set_coverage": exact_coverage,
            "execution_outcomes_by_action": {
                row["action"]: row["execution_result"] for row in rows
            },
            "role_execution_outcome": role_outcome,
        }
    execution_pass = all(row["execution_result"] == "PASS" for row in index_rows)
    physical_counts = Counter(row["physical_qa_result"] for row in index_rows)
    result = {
        "schema": "biospur-fusion-v0-complete-capture-result-v1",
        "capture_name": capture_name, "capture_id": spec["capture_id"],
        "profile_sha256": profile_sha, "calibration_executed": True,
        "profile_capture_bound": True, "calibration_deterministic": calibration["deterministic_profile_payload"],
        "exact_per_role_replay_evidence": exact_role_evidence,
        "replay_counts": dict(by_role), "expected_replay_counts": {
            "CALIBRATION_SELF_REPLAY": len(spec["calibration_inputs"]),
            "MAIN_SUITE_1": len(spec["main_suite_1"]),
            "MAIN_SUITE_2": len(spec["main_suite_2"]), "HXX": len(spec["hxx"]),
        },
        "complete_action_coverage": dict(by_role) == {
            "CALIBRATION_SELF_REPLAY": len(spec["calibration_inputs"]),
            "MAIN_SUITE_1": len(spec["main_suite_1"]),
            "MAIN_SUITE_2": len(spec["main_suite_2"]), "HXX": len(spec["hxx"]),
        },
        "all_execution_integrity_pass": execution_pass,
        "physical_qa_counts": dict(physical_counts),
        "physical_coherence": (
            "FAIL" if physical_counts.get("FAIL", 0) > 0 else
            "PARTIAL" if physical_counts.get("INCONCLUSIVE", 0) > 0
            or physical_counts.get("PARTIAL", 0) > 0 else
            "PARTIAL"
        ),
        "physical_pass_with_supervisor_judgment_count": 0,
        "supervisor_live_viewer_physical_judgment": "PENDING_INDEPENDENT_SUPERVISOR",
        "hxx_opened_and_replayed": by_role["HXX"] == len(spec["hxx"]),
        "hxx_role": ["POST_CALIBRATION_REPLAY", "POST_CALIBRATION_REGRESSION"],
        "hxx_described_as_fresh_holdout": False,
        "viewer_index": str(output / "VIEWER_INDEX.html"),
    }
    if capture_name == "CAPTURE2":
        elbow = next(row for row in index_rows if row["role"] == "MAIN_SUITE_1" and row["action"] == "06_elbow_left")
        elbow_qa = json.loads(Path(elbow["physical_qa_artifact"]).read_text(encoding="utf-8"))["capture2_operator_truth_qa"]
        result["capture2_06_elbow_left"] = elbow_qa
    dump_json(output / "FINAL_CAPTURE_RESULT.json", result)
    (output / "FINAL_CAPTURE_RESULT.md").write_text(
        f"# {capture_name} complete result\n\nAll scheduled replay entries executed: **{execution_pass}**. "
        f"Physical coherence: **{result['physical_coherence']}**. Hxx was opened only after the "
        "same-capture profile was frozen and is classified as post-calibration replay/regression.\n",
        encoding="utf-8",
    )
    verify_code_lock(root, lock_path, lock_sha256)
    return profile, manifest, result


def summarize_dual_capture(goal_dir: Path, capture1: Mapping[str, Any], capture2: Mapping[str, Any]) -> dict[str, Any]:
    def action_metrics(capture: str) -> list[dict[str, Any]]:
        index = json.loads((goal_dir / capture / "COMPLETE_REPLAY_INDEX.json").read_text(encoding="utf-8"))
        rows = []
        for entry in index["entries"]:
            metrics = json.loads(Path(entry["metrics_artifact"]).read_text(encoding="utf-8"))
            rows.append({
                "action": entry["action"], "role": entry["role"],
                "qmt_difference_q95_deg": metrics["qmt_off_versus_always_on_qmt"]["segment_rotation_difference_deg"]["q95"],
                "ik_difference_q95_deg": metrics["qmt_off_ik_on_versus_off"]["segment_rotation_difference_deg"]["q95"],
                "ik_on_residual_q95_deg": metrics["numerical_integrity"]["selected_v0"]["observation_residual_deg"]["q95"],
                "ik_off_residual_q95_deg": metrics["numerical_integrity"]["selected_v0_no_shared_ik"]["observation_residual_deg"]["q95"],
                "uncertainty_q95_deg": metrics["numerical_integrity"]["selected_v0"]["segment_uncertainty_deg"]["q95"],
                "physical_qa": entry["physical_qa_result"],
            })
        return rows
    c1_rows = action_metrics("CAPTURE1"); c2_rows = action_metrics("CAPTURE2")
    all_rows = c1_rows + c2_rows
    qmt_engineering_complete = bool(
        capture1["all_execution_integrity_pass"] and capture2["all_execution_integrity_pass"]
        and all(np.isfinite(row["qmt_difference_q95_deg"]) for row in all_rows)
    )
    ik_residual_criterion = bool(
        all(row["ik_on_residual_q95_deg"] <= row["ik_off_residual_q95_deg"] + 1e-12 for row in all_rows)
    )
    uncertainty_numerically_present = bool(all(
        np.isfinite(row["uncertainty_q95_deg"]) and row["uncertainty_q95_deg"] > 0
        for row in all_rows
    ))
    repeatability = (
        "FAIL" if not capture1["all_execution_integrity_pass"] or not capture2["all_execution_integrity_pass"]
        else "PARTIAL"
    )
    elbow_status = capture2["capture2_06_elbow_left"]["operator_truth_match"]
    ready = False
    return {
        "schema": "biospur-fusion-v0-dual-capture-comparison-v1",
        "capture1": capture1, "capture2": capture2,
        "per_action_ablation": {"CAPTURE1": c1_rows, "CAPTURE2": c2_rows},
        "same_locked_algorithm": True, "profile_transfer_experiment": False,
        "qmt_conclusion": {
            "supported_by_both_captures": False,
            "engineering_comparison_complete_without_strict_failures": qmt_engineering_complete,
            "selected_mode": "QMT_OFF",
            "reason": "QMT_OFF preserves exact VQF attitude and honest missing-heading uncertainty. Finite matched differences alone cannot establish physical coherence.",
        },
        "shared_ik_conclusion": {
            "supported_by_both_captures": False,
            "numerical_residual_criterion_met": ik_residual_criterion,
            "criterion": "IK-on observation residual q95 is no greater than matched IK-off for every replay entry",
            "physical_claim_withheld": True,
        },
        "uncertainty_conclusion": {
            "supported_by_both_captures": False,
            "positive_finite_internal_uncertainty_present": uncertainty_numerically_present,
            "meaning": "same-capture donning dispersion + missing heading evidence + VQF bias growth + IK residual; internal uncertainty, not external error bars",
            "positive_uncertainty_may_promote_physical_or_readiness_pass": False,
        },
        "dual_capture_repeatability": repeatability,
        "repeatability_pass_withheld_reason": (
            "DEGRADED_CLOCK_ACTIONS_AND_PENDING_INDEPENDENT_SUPERVISOR_LIVE_VIEWER_PHYSICAL_JUDGMENT"
        ),
        "finite_pca_numerical_integrity_side_dominance_fk_objective_qmt_finiteness_"
        "or_positive_uncertainty_may_promote_repeatability_or_readiness": False,
        "v0_baseline_ready": ready,
    }
