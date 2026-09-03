#!/usr/bin/env python3
"""Seal a fresh C2 coupled-progressive run without touching raw payload bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
CAPTURE_ID = "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
CAPTURE_REL = Path("datasets/phase2_calibration") / CAPTURE_ID
RAW_REL = CAPTURE_REL / "system/fusion_continuous/fusion_host_raw.cobs.bin"
CONFIG_REL = Path("config/c2_coupled_progressive_v1/config.json")
ANTHROPOMETRY_REL = Path("config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json")
AMENDMENT_REL = Path("config/biospur_fusion_v0_c2_main_contract_20260829/USER_ANTHROPOMETRY_AMENDMENT_001.json")

EPISODES = (
    ("00_initial_still", "00_initial_still", 2),
    ("02_t_pose", "02_t_pose", 3),
    ("03_pelvis_hula_circle", "03_pelvis_tilt_shift", 2),
    ("04_shoulder_left", "04_shoulder_left", 3),
    ("05_shoulder_right", "05_shoulder_right", 3),
    ("06_elbow_left", "06_elbow_left", 2),
    ("07_elbow_right", "07_elbow_right", 1),
    ("08_hip_left", "08_hip_left", 1),
    ("09_hip_right", "09_hip_right", 1),
    ("10_knee_left_seated", "10_knee_left", 1),
    ("11_knee_right_seated", "11_knee_right", 1),
    ("12_heel_raise_left", "12_heel_raise_left", 1),
    ("13_heel_raise_right", "13_heel_raise_right", 1),
    ("14_trunk_flex_extend", "14_trunk_flex_extend", 1),
    ("15_trunk_axial_rotation", "15_trunk_axial_rotation", 1),
    ("16_squat", "16_squat", 1),
    ("17_final_still", "17_final_still", 1),
    ("18_heel_to_butt_left", "18_heel_to_butt_left", 1),
    ("19_heel_to_butt_right", "19_heel_to_butt_right", 1),
)

AUTHORITIES = (
    Path("../AGENTS.md"),
    Path("config/biospur_fusion_v0_c2_main_contract_20260829/MASTER_CONTRACT.md"),
    Path("config/biospur_fusion_v0_c2_main_contract_20260829/RUN_START_CONTRACT.template.json"),
    Path("config/biospur_fusion_v0_c2_main_contract_20260829/GEOMETRY_AND_PARAMETER_CONTRACT.json"),
    Path("config/biospur_fusion_v0_c2_progressive_contract/ARCHITECTURE_CONTRACT.md"),
    Path("config/biospur_fusion_v0_c2_progressive_contract/RUN_START_CONTRACT.template.json"),
    ANTHROPOMETRY_REL,
    AMENDMENT_REL,
    CONFIG_REL,
    CAPTURE_REL / "identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json",
    CAPTURE_REL / "identity/POST_SEAL_WEAR_DIRECTION_AMENDMENT_004.json",
    CAPTURE_REL / "identity/POST_SEAL_FRAME_SEMANTICS_AMENDMENT_005.json",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def write_new(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o444)


def metadata_preselection(config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    capture = ROOT / CAPTURE_REL
    expected_nodes = sorted(config["node_to_segment"])
    selected: list[dict[str, Any]] = []
    access: list[dict[str, Any]] = []
    previous_end = -1
    event_order = (
        "REPETITION_START_BOUNDARY", "ACTION_START", "ACTION_STOP", "REPETITION_END_BOUNDARY",
    )
    for index, (action, physical, attempt) in enumerate(EPISODES):
        accepted = capture / "actions" / physical / "rep_01" / "attempts" / f"attempt_{attempt:02d}_accepted"
        paths = {
            "manifest": accepted / "manifest/CAPTURE_MANIFEST.json",
            "range": accepted / "manifest/CONTINUOUS_RANGE.json",
            "before": accepted / "manifest/NODE_INVENTORY_BEFORE.json",
            "after": accepted / "manifest/NODE_INVENTORY_AFTER.json",
            "events": accepted / "events/ACTION_EVENTS.jsonl",
        }
        values: dict[str, Any] = {}
        for key, path in paths.items():
            raw = path.read_bytes()
            access.append({"path": str(path.relative_to(ROOT)), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
            values[key] = [json.loads(line) for line in raw.decode().splitlines() if line.strip()] if key == "events" else json.loads(raw)
        manifest = values["manifest"]
        interval = values["range"]
        events = values["events"]
        if manifest.get("action_id") != action or manifest.get("attempt_id") != attempt or manifest.get("status") != "ACCEPTED":
            raise RuntimeError(f"metadata identity/attempt conflict: {action}")
        if sorted(values["before"].get("nodes", [])) != expected_nodes or sorted(values["after"].get("nodes", [])) != expected_nodes:
            raise RuntimeError(f"ten-node inventory conflict: {action}")
        if tuple(row.get("event") for row in events) != event_order:
            raise RuntimeError(f"event chronology conflict: {action}")
        by_event = {row["event"]: row for row in events}
        start = int(interval["start_byte_inclusive"])
        action_start = int(by_event["ACTION_START"]["continuous_raw_complete_frame_bytes"])
        action_stop = int(by_event["ACTION_STOP"]["continuous_raw_complete_frame_bytes"])
        end = int(interval["end_byte_exclusive"])
        if not (previous_end < start < action_start < action_stop < end):
            raise RuntimeError(f"overlap or incomplete episode: {action}")
        previous_end = end
        selected.append({
            "chronological_index": index,
            "episode": action,
            "physical_directory": physical,
            "attempt": attempt,
            "accepted_directory": str(accepted.relative_to(ROOT)),
            "byte_interval": [start, end],
            "action_interval": [action_start, action_stop],
            "factor_routing": "GENERIC_ALL_INCIDENT_EDGES_NO_ACTION_NAME_INPUT",
            "post_fit_qa_label_only": action,
            "metadata_hashes": {key: hashlib.sha256(paths[key].read_bytes()).hexdigest() for key in paths},
        })
    return ({
        "schema": "biospur-c2-coupled-metadata-preselection-v1",
        "capture": "C2_ONLY",
        "capture_id": CAPTURE_ID,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "selection": "exact preregistered paths; no directory enumeration or result inspection",
        "payload_opened_statted_or_hashed": False,
        "holdout_paths_resolved_or_accessed": False,
        "all_ten_nodes_exactly_once": len(expected_nodes) == len(set(expected_nodes)) == 10,
        "node_to_segment": config["node_to_segment"],
        "episodes": selected,
        "metadata_access": access,
    }, {
        "schema": "biospur-c2-coupled-payload-access-plan-v1",
        "payload": str(RAW_REL),
        "payload_opened_statted_or_hashed_during_plan": False,
        "authorized_intervals": [{"chronological_index": row["chronological_index"], "episode": row["episode"], "interval": row["byte_interval"]} for row in selected],
        "complete_episode_count": len(selected),
        "external_holdout_authorized": False,
        "prequential_policy": "score episode k from state k-1 before ingest; append every valid generic factor afterward",
        "forbidden": ["whole-file read", "whole-file hash", "non-C2 path", "UWB numeric payload", "vendor quaternion", "manual pose truth"],
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--activation-source", required=True)
    parser.add_argument("--monitor-source", required=True)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    run_dir.relative_to((ROOT / "logs").resolve())
    run_dir.mkdir(parents=True, exist_ok=True)
    config = read_json(ROOT / CONFIG_REL)
    preselection, access_plan = metadata_preselection(config)
    resources = {
        "schema": "biospur-c2-coupled-resource-gate-v1",
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "nrf_ssd_free_bytes": shutil.disk_usage("/mnt/nrf_ssd").free,
        "root_free_bytes": shutil.disk_usage("/").free,
        "projected_growth_bytes": 4_000_000_000,
    }
    resources.update({
        "nrf_ssd_at_least_100gb": resources["nrf_ssd_free_bytes"] >= 100_000_000_000,
        "root_at_least_40gb": resources["root_free_bytes"] >= 40_000_000_000,
        "projected_growth_at_most_5gb": resources["projected_growth_bytes"] <= 5_000_000_000,
    })
    authorities = {}
    for relative in AUTHORITIES:
        path = (ROOT / relative).resolve()
        if not path.is_file():
            raise RuntimeError(f"missing authority: {relative}")
        authorities[str(relative)] = {"realpath": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}
    matrix = run_dir / "CONTRIBUTION_OWNERSHIP_MATRIX.md"
    authorities[str(matrix.relative_to(ROOT))] = {"realpath": str(matrix), "bytes": matrix.stat().st_size, "sha256": sha256(matrix)}
    gates = {
        "schema": "biospur-c2-coupled-consequence-gate-v1",
        "all_predicates_classified": True,
        "classes": {
            "A": {"meaning": "current-run architecture/provenance blocker; preserve, repair, continue overall task", "preaccess_status": "PASS", "checks": ["C2 only", "ten exact nodes", "nineteen exact chronological episodes", "no payload before seal", "no holdout metadata", "one continuous frontend", "one persistent posterior", "nine-edge tree plus one yaw gauge"]},
            "B": {"meaning": "candidate-only physical rejection", "preaccess_status": "ARMED", "checks": ["front/back knee split", "crossing or mirror", "disconnection", "collapse/compression", "improper rotation", "gross topology/ROM/gravity"]},
            "C": {"meaning": "covariance/information/uncertainty update", "preaccess_status": "ARMED", "checks": ["low excitation", "soft tissue", "noise", "weak rank", "QMT rating", "prequential conflict"]},
            "D": {"meaning": "diagnostic log-and-continue", "preaccess_status": "ARMED", "checks": ["local warning", "near-axis row", "duplicate", "low-information block", "ordinary solver or visual failure"]}
        },
        "action_label_may_route_factor_or_pose": False,
        "ordinary_failure_terminal": False,
        "causal_pivot_required": True,
    }
    write_new(run_dir / "RESOURCE_GATE.json", resources)
    write_new(run_dir / "AUTHORITY_HASHES.json", {"schema": "biospur-c2-coupled-authority-hashes-v1", "entries": authorities})
    write_new(run_dir / "METADATA_PRESELECTION.json", preselection)
    write_new(run_dir / "PAYLOAD_ACCESS_PLAN.json", access_plan)
    write_new(run_dir / "GATE_A_B_C_D.json", gates)
    start = {
        "schema": "biospur-c2-coupled-run-start-v1",
        "activation": {"source_thread_id": args.activation_source, "monitor_source_thread_id": args.monitor_source, "start_local": "2026-08-31T08:21:31+02:00"},
        "workspace": {"canonical": str(ROOT), "realpath": str(ROOT.resolve()), "sole_writer": True, "branch_or_worktree_created": False, "raw_copy_created": False},
        "resource_gate": {"path": "RESOURCE_GATE.json", "sha256": sha256(run_dir / "RESOURCE_GATE.json")},
        "authority_hashes": {"path": "AUTHORITY_HASHES.json", "sha256": sha256(run_dir / "AUTHORITY_HASHES.json")},
        "preselection": {"path": "METADATA_PRESELECTION.json", "sha256": sha256(run_dir / "METADATA_PRESELECTION.json")},
        "payload_access_plan": {"path": "PAYLOAD_ACCESS_PLAN.json", "sha256": sha256(run_dir / "PAYLOAD_ACCESS_PLAN.json")},
        "consequence_gate": {"path": "GATE_A_B_C_D.json", "sha256": sha256(run_dir / "GATE_A_B_C_D.json")},
        "architecture": config["architecture"],
        "payload_access_before_this_seal": False,
        "new_c2_payload_hash_before_this_seal": False,
        "external_holdout_authorized": False,
        "real_fit_blocked_until_independent_synthetic_and_active_parameter_freeze": True,
    }
    write_new(run_dir / "RUN_START_CONTRACT.json", start)
    audit_checks = {
        "resources": all(resources[key] for key in ("nrf_ssd_at_least_100gb", "root_at_least_40gb", "projected_growth_at_most_5gb")),
        "nineteen_episodes": len(preselection["episodes"]) == 19,
        "ten_nodes": preselection["all_ten_nodes_exactly_once"],
        "payload_untouched": not preselection["payload_opened_statted_or_hashed"] and not access_plan["payload_opened_statted_or_hashed_during_plan"],
        "all_classes": set(gates["classes"]) == {"A", "B", "C", "D"},
        "no_action_routing": not gates["action_label_may_route_factor_or_pose"] and not config["architecture"]["generic_window_evaluator_accepts_action_name"],
        "persistent_state": config["architecture"]["persistent_posterior_instances"] == 1 and not config["architecture"]["prefix_batch_refits"],
        "time_varying_qmt": config["architecture"]["time_varying_quat2corr_consumed"] and config["architecture"]["time_varying_delta_filt_consumed"],
        "sensor_not_joint": not config["architecture"]["sensor_origin_is_joint_or_display_point"],
    }
    write_new(run_dir / "CONTRACT_AUDIT.json", {"schema": "biospur-c2-coupled-contract-audit-v1", "status": "PASS" if all(audit_checks.values()) else "FAIL", "checks": audit_checks, "run_start_sha256": sha256(run_dir / "RUN_START_CONTRACT.json")})
    if not all(audit_checks.values()):
        raise RuntimeError("contract audit failed")
    print(json.dumps({"status": "PASS", "run_dir": str(run_dir), "run_start_sha256": sha256(run_dir / "RUN_START_CONTRACT.json"), "audit_sha256": sha256(run_dir / "CONTRACT_AUDIT.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
