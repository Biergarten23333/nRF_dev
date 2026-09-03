#!/usr/bin/env python3
"""Calibration-first R6A2B-R1 authority recovery and execution entry point."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from biospur_fusion.root_r6a2b.real_profile import (  # noqa: E402
    EXPECTED_NODE_FAMILIES,
    PROFILE_SCHEMA,
    seal_profile,
    validate_real_profile,
)

FUSION = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def c1_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    capture = FUSION / "logs/v47_ten_node_body_calibration_20260814_093601"
    events_path = capture / "ACTION_EVENTS.jsonl"
    manifest_path = FUSION / "config/captures/v47_ten_node_body_calibration_20260814_093601.json"
    events = jsonl(events_path); manifest = load(manifest_path)
    formal = load(capture / "FORMAL_T0.json")
    accounting = load(capture / "analysis_body_fusion_v2/EVENT_ACCOUNTING.json")
    t0_mono = float(formal["formal_t0_monotonic"]); t0_ns = int(accounting["formal_global_start_ns"])

    rows: list[dict[str, Any]] = [{
        "capture_identifier": capture.name,
        "action_identifier": "formal_initial_stationary",
        "attempt_number": 1,
        "start_timestamp": {"global_time_ns": t0_ns, "monotonic_s": t0_mono},
        "end_timestamp": {"global_time_ns_exclusive": t0_ns + 30_000_000_000, "monotonic_s": t0_mono + 30.0},
        "duration_s": 30.0,
        "declared_pose_or_action": "initial stationary formal startup gate; not the operator-labelled neutral-standing action",
        "role": "CALIBRATION",
        "authority_source": [str(capture / "FORMAL_T0.json"), "binding R6A2B-R1 operator statement"],
        "authority_source_sha256": [sha256(capture / "FORMAL_T0.json")],
        "sample_access_authorized_in_task": True,
        "qualification_status": "AUTHORIZED_INITIAL_STATIONARY_BUT_NOT_NEUTRAL_POSE_LABEL",
    }]
    rejected = {("right_elbow", 1)}
    starts: dict[tuple[str, int], dict[str, Any]] = {}
    for event in events:
        action = event.get("action")
        if action is None:
            continue
        key = (str(action), int(event.get("attempt", 1)))
        if event.get("event") == "ACTION_START":
            starts[key] = event
        elif event.get("event") == "ACTION_STOP" and key in starts:
            start = starts.pop(key)
            start_ns = t0_ns + int(round((float(start["monotonic"]) - t0_mono) * 1e9))
            stop_ns = t0_ns + int(round((float(event["monotonic"]) - t0_mono) * 1e9))
            action_id, attempt = key
            superseded = key in rejected or (action_id == "initial_still" and attempt == 1)
            held = action_id in {"walk", "final_still", "golf_swing", "boxing"}
            rows.append({
                "capture_identifier": capture.name,
                "action_identifier": action_id,
                "attempt_number": attempt,
                "start_timestamp": {"global_time_ns": start_ns, "monotonic_s": float(start["monotonic"]), "epoch_s": float(start["epoch"])},
                "end_timestamp": {"global_time_ns_exclusive": stop_ns, "monotonic_s": float(event["monotonic"]), "epoch_s": float(event["epoch"])},
                "duration_s": (stop_ns - start_ns) * 1e-9,
                "declared_pose_or_action": str(start.get("description", action_id)),
                "role": "HELD_OUT" if held else "CALIBRATION",
                "authority_source": [str(events_path), str(manifest_path), "binding R6A2B-R1 operator statement"],
                "authority_source_sha256": [sha256(events_path), sha256(manifest_path)],
                "sample_access_authorized_in_task": bool(not held and not superseded),
                "qualification_status": (
                    "HELD_OUT_SEALED" if held else
                    "SUPERSEDED_INVALID_ATTEMPT" if superseded else
                    "OFFICIAL_CALIBRATION_WINDOW"
                ),
                "neutral_standing_alias": bool(action_id == "initial_still" and attempt == 2),
            })
    conflict = {
        "capture": "C1",
        "conflict": "legacy manifest heldout_actions lists walk and final_still only; binding R6A2B-R1 additionally seals golf_swing and boxing",
        "resolution": "binding operator statement supersedes legacy omission; all four are HELD_OUT and inaccessible",
        "legacy_value": manifest["heldout_actions"],
    }
    return rows, conflict


def _phase2_action_events(
    capture: Path, relative_dir: str, action_id: str
) -> tuple[Path, Path, list[dict[str, Any]], dict[str, Any]]:
    rep = capture / relative_dir / "rep_01"
    manifest_path = rep / "manifest/CAPTURE_MANIFEST.json"
    manifest = load(manifest_path)
    event_path = rep / "events/ACTION_EVENTS.jsonl"
    rows = [row for row in jsonl(event_path) if row.get("event") in {"ACTION_START", "ACTION_STOP"}]
    if len(rows) != 2:
        raise RuntimeError(f"non-unique promoted phase2 bracket: {action_id}")
    return event_path, manifest_path, rows, manifest


def c2_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    capture = FUSION / "datasets/phase2_calibration/phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
    plan_path = capture / "CAPTURE_PLAN_FINAL.json"; table_path = capture / "subject/ACTUAL_ACTION_EXECUTION_TABLE.md"
    plan = load(plan_path); rows = []
    for action in plan["actions"]:
        action_id = action["action_id"]
        event_path, manifest_path, pair, manifest = _phase2_action_events(
            capture, action["relative_dir"], action_id
        )
        by = {row["event"]: row for row in pair}; start, stop = by["ACTION_START"], by["ACTION_STOP"]
        held = str(action["data_role"]).startswith("SEALED_") or action_id in {"H00_walk", "H01_boxing", "H02_golf"}
        rows.append({
            "capture_identifier": capture.name,
            "action_identifier": action_id,
            "attempt_number": int(manifest["attempt_id"]),
            "start_timestamp": {"host_monotonic_ns": int(start["host_monotonic_ns"]), "utc": start["utc"], "raw_complete_frame_bytes": int(start["continuous_raw_complete_frame_bytes"])},
            "end_timestamp": {"host_monotonic_ns_exclusive": int(stop["host_monotonic_ns"]), "utc": stop["utc"], "raw_complete_frame_bytes": int(stop["continuous_raw_complete_frame_bytes"])},
            "duration_s": (int(stop["host_monotonic_ns"]) - int(start["host_monotonic_ns"])) * 1e-9,
            "declared_pose_or_action": action["instruction_zh"],
            "source_declared_data_role": action["data_role"],
            "role": "HELD_OUT" if held else "DEVELOPMENT_ACTION",
            "authority_source": [str(plan_path), str(table_path), str(event_path), str(manifest_path), "binding R6A2B-R1 operator statement"],
            "authority_source_sha256": [sha256(plan_path), sha256(table_path), sha256(event_path), sha256(manifest_path)],
            "sample_access_authorized_in_task": False,
            "conditional_action_access_after_profile_pass": bool(not held),
            "qualification_status": "HELD_OUT_SEALED" if held else "CONDITIONAL_ONE_ACTION_ONLY_AFTER_REAL_PROFILE_PASS",
        })
    return rows, {
        "capture": "C2",
        "conflict": "CAPTURE_PLAN_FINAL labels non-held-out windows PHASE2_CALIBRATION, while binding R6A2B-R1 restricts this real subject/session profile calibration to official C1 windows",
        "resolution": "C2 non-held-out windows are DEVELOPMENT_ACTION candidates for this task; none may enter the C1 calibration objective",
    }


def c3_rows() -> list[dict[str, Any]]:
    capture = FUSION / "datasets/phase3_targeted_upper_arm/phase3_upper_arm_twist_20260822T091236Z_upper_arm_20260822T091236Z"
    plan_path = capture / "TARGETED_CAPTURE_PLAN.json"; events_path = capture / "ACTION_EVENTS.jsonl"
    plan = load(plan_path); events = jsonl(events_path)
    event_map = {(row["action_id"], row["event"]): row for row in events}
    rows = []
    for action in plan["actions"]:
        action_id = action["action_id"]
        start, stop = event_map[(action_id, "ACTION_START")], event_map[(action_id, "ACTION_STOP")]
        held = action["data_role"] == "RESERVED_VALIDATION"
        rows.append({
            "capture_identifier": capture.name,
            "action_identifier": action_id,
            "attempt_number": 1,
            "start_timestamp": {"monotonic_ns": int(start["monotonic_ns"]), "raw_bytes_written": int(start["boundary"]["raw_bytes_written"])},
            "end_timestamp": {"monotonic_ns_exclusive": int(stop["monotonic_ns"]), "raw_bytes_written": int(stop["boundary"]["raw_bytes_written"])},
            "duration_s": (int(stop["monotonic_ns"]) - int(start["monotonic_ns"])) * 1e-9,
            "declared_pose_or_action": action["instruction_zh"],
            "source_declared_data_role": action["data_role"],
            "source_family": action["family"],
            "role": "HELD_OUT" if held else "DEVELOPMENT_ACTION",
            "authority_source": [str(plan_path), str(events_path), "binding R6A2B-R1 operator statement"],
            "authority_source_sha256": [sha256(plan_path), sha256(events_path)],
            "sample_access_authorized_in_task": False,
            "conditional_action_access_after_profile_pass": bool(not held),
            "qualification_status": "HELD_OUT_SEALED" if held else "CONDITIONAL_ONE_ACTION_ONLY_AFTER_REAL_PROFILE_PASS",
        })
    return rows


def classify_previous(c1: list[dict[str, Any]]) -> list[dict[str, Any]]:
    old = [
        ("PREVIOUS_WINDOW_A", 2924756071417, 2954756071417),
        ("PREVIOUS_WINDOW_B", 3115724244760, 3145724244760),
    ]
    results = []
    for name, start, stop in old:
        containing = [row for row in c1 if (
            row["start_timestamp"].get("global_time_ns", 2**63 - 1) <= start
            and row["end_timestamp"].get("global_time_ns_exclusive", -1) >= stop
        )]
        if len(containing) != 1:
            raise RuntimeError(f"previous window classification ambiguous: {name} -> {len(containing)}")
        row = containing[0]
        results.append({
            "previous_window": name,
            "start_global_time_ns": start,
            "stop_global_time_ns_exclusive": stop,
            "actual_action_identifier": row["action_identifier"],
            "actual_attempt_number": row["attempt_number"],
            "authoritative_role": row["role"],
            "valid_for_previous_use": False,
            "reason": (
                "30 s interior of superseded initial_still attempt 1; not the official neutral-standing attempt 2 and not valid initialization evidence"
                if name.endswith("A") else
                "30 s payload-ranked interior of the Capture1 arms calibration movement; calibration evidence was misused as an ordinary development-action window"
            ),
            "containing_official_interval": {
                "start_global_time_ns": row["start_timestamp"]["global_time_ns"],
                "stop_global_time_ns_exclusive": row["end_timestamp"]["global_time_ns_exclusive"],
            },
        })
    return results


def authority(result: Path) -> Path:
    result.mkdir(parents=True, exist_ok=True)
    c1, conflict1 = c1_rows(); c2, conflict2 = c2_rows(); c3 = c3_rows()
    previous = classify_previous(c1)
    ledger = {
        "schema": "biospur-root-r6a2b-r1-capture-window-role-ledger-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "checkpoint": "52e2896bb6437aa19710a6c0b6f54b4193f64e4a",
        "authority_precedence": [
            "current binding R6A2B-R1 operator statement",
            "promoted per-action manifest and event bracket",
            "final capture plan / Capture1 action ledger",
            "legacy capture manifest",
        ],
        "rows": c1 + c2 + c3,
        "counts": {
            "total": len(c1) + len(c2) + len(c3),
            "by_capture": {"C1": len(c1), "C2": len(c2), "C3": len(c3)},
            "by_role": {role: sum(row["role"] == role for row in c1 + c2 + c3) for role in ("CALIBRATION", "DEVELOPMENT_ACTION", "HELD_OUT")},
            "sample_access_authorized_now": sum(row["sample_access_authorized_in_task"] for row in c1 + c2 + c3),
        },
        "official_capture1_calibration_windows": [
            {key: row[key] for key in ("action_identifier", "attempt_number", "start_timestamp", "end_timestamp", "duration_s", "qualification_status")}
            for row in c1 if row["role"] == "CALIBRATION" and row["sample_access_authorized_in_task"]
        ],
        "semantic_aliases": {
            "neutral_standing": "C1 initial_still attempt 2",
            "initial_still": "C1 initial_still attempt 2",
            "note": "neutral-standing and initial-still are two semantic names for one official interval, not independent evidence windows",
        },
        "previous_r6a2b_windows": previous,
        "documentation_conflicts": [conflict1, conflict2, {
            "capture": "C1 identity",
            "conflict": "legacy C1 mapping swaps BSFEC35/BSFB165 forearm sides relative to the current corrected node map",
            "resolution": "R6A2B-R1 corrected binding controls: BSFEC35=left forearm, BSFB165=right forearm",
        }],
        "held_out_payload_access": {
            "golf": False, "boxing": False, "walk": False, "final_still": False,
            "reserved_validation": False,
        },
        "numeric_sample_arrays_opened_by_this_authority_step": False,
    }
    path = result / "CAPTURE_WINDOW_ROLE_LEDGER.json"; dump(path, ledger)
    a, b = previous
    report = f"""# Previous R6A2B window classification

The old direct-real-shadow windows were not authoritative evaluation windows.

| Old window | Exact actual action | Authoritative role | Classification |
|---|---|---|---|
| `[{a['start_global_time_ns']}, {a['stop_global_time_ns_exclusive']})` | `initial_still` attempt 1 | `CALIBRATION` but superseded | Invalid as neutral initialization: it is an interior 30 s slice of the superseded first attempt, while attempt 2 is the official neutral-standing interval. |
| `[{b['start_global_time_ns']}, {b['stop_global_time_ns_exclusive']})` | `arms` attempt 1 | `CALIBRATION` | Invalid as an ordinary action evaluation: it is a payload-ranked interior slice of a protocol calibration movement. |

The official C1 neutral-standing/initial-still evidence is one interval: `initial_still` attempt 2. The labels are semantic aliases, not two independent likelihood windows.

Authority conflicts are retained in `CAPTURE_WINDOW_ROLE_LEDGER.json`. The current binding statement seals Golf and Boxing in addition to legacy-held-out Walk and Final Still. No held-out payload or numeric sample array was opened while producing this classification.
"""
    (result / "PREVIOUS_R6A2B_WINDOW_CLASSIFICATION.md").write_text(report, encoding="utf-8")
    return path


NODE_TO_SEGMENT = {
    "BSFEC35": "forearm_left", "BSFB165": "forearm_right",
    "BSFAA61": "upper_arm_left", "BSF1120": "upper_arm_right",
    "BSF31CC": "torso", "BSFC2CC": "pelvis",
    "BSF44AD": "thigh_left", "BSF3C79": "thigh_right",
    "BSF6C53": "shank_left", "BSF8BC4": "shank_right",
}
NODES = tuple(NODE_TO_SEGMENT)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def _accepted(rows: np.ndarray, start_ns: int, stop_ns: int) -> np.ndarray:
    return rows[
        (rows["status"] == 1)
        & (rows["global_time_ns"] >= start_ns)
        & (rows["global_time_ns"] < stop_ns)
    ]


def _vector_summary(values: np.ndarray) -> dict[str, Any]:
    if len(values) == 0:
        return {"count": 0, "mean": None, "standard_deviation": None}
    return {
        "count": int(len(values)),
        "mean": np.mean(values, axis=0).tolist(),
        "standard_deviation": np.std(values, axis=0).tolist(),
    }


def _window_diagnostics(result: Path, ledger: dict[str, Any]) -> dict[str, Any]:
    source = FUSION / "logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/TIME_EVENT_LEDGER.npz"
    objective = [
        row for row in ledger["rows"]
        if row["capture_identifier"] == "v47_ten_node_body_calibration_20260814_093601"
        and row["role"] == "CALIBRATION"
        and row["qualification_status"] == "OFFICIAL_CALIBRATION_WINDOW"
        and row["sample_access_authorized_in_task"]
    ]
    formal = next(row for row in ledger["rows"] if row["action_identifier"] == "formal_initial_stationary")
    windows = [formal] + objective
    diagnostics: dict[str, Any] = {
        "schema": "biospur-root-r6a2b-r1-calibration-input-diagnostics-v1",
        "typed_ledger_path": str(source),
        "typed_ledger_sha256": sha256(source),
        "selection_before_array_access": True,
        "held_out_intervals_selected_or_summarized": False,
        "objective_window_count": len(objective),
        "supplemental_diagnostic_window_count": 1,
        "windows": {},
    }
    with np.load(source, allow_pickle=False) as arrays:
        for row in windows:
            key = f"{row['action_identifier']}:attempt{row['attempt_number']}"
            start_ns = int(row["start_timestamp"]["global_time_ns"])
            stop_ns = int(row["end_timestamp"]["global_time_ns_exclusive"])
            window = {
                "action_identifier": row["action_identifier"],
                "attempt_number": row["attempt_number"],
                "role_in_calibration": (
                    "SUPPLEMENTAL_INITIAL_STATIONARY_DIAGNOSTIC_NOT_OBJECTIVE"
                    if row is formal else "CALIBRATION_OBJECTIVE"
                ),
                "start_global_time_ns": start_ns,
                "stop_global_time_ns_exclusive": stop_ns,
                "duration_s": row["duration_s"],
                "window_binding_sha256": _canonical_hash({
                    "action_identifier": row["action_identifier"],
                    "attempt_number": row["attempt_number"],
                    "start_global_time_ns": start_ns,
                    "stop_global_time_ns_exclusive": stop_ns,
                }),
                "per_node": {},
            }
            for node in NODES:
                imu = _accepted(arrays[f"imu_{node}"], start_ns, stop_ns)
                uwb = _accepted(arrays[f"uwb_{node}"], start_ns, stop_ns)
                acc = imu["acc_raw"].astype(float) / 2048.0 * 9.80665
                gyro = np.deg2rad(imu["gyro_raw"].astype(float) / 16.384)
                dt = np.diff(imu["global_time_ns"].astype(np.int64)) * 1e-9
                valid_ranges: list[float] = []
                for sweep in uwb:
                    mask = int(sweep["valid_mask"])
                    valid_ranges.extend(
                        float(sweep["range_mm"][index]) / 1000.0
                        for index in range(8) if mask & (1 << index)
                    )
                acc_norm = np.linalg.norm(acc, axis=1) if len(acc) else np.empty(0)
                gyro_norm = np.linalg.norm(gyro, axis=1) if len(gyro) else np.empty(0)
                mean_acc = np.mean(acc, axis=0) if len(acc) else np.full(3, np.nan)
                angle = (
                    float(np.degrees(np.arccos(np.clip(mean_acc[1] / np.linalg.norm(mean_acc), -1.0, 1.0))))
                    if len(acc) and np.linalg.norm(mean_acc) > 0 else None
                )
                window["per_node"][node] = {
                    "segment": NODE_TO_SEGMENT[node],
                    "hardware_family": EXPECTED_NODE_FAMILIES[node],
                    "accepted_imu_rows": int(len(imu)),
                    "accepted_uwb_sweeps": int(len(uwb)),
                    "valid_uwb_scalar_observations": int(len(valid_ranges)),
                    "boot_epochs_imu": sorted(map(int, np.unique(imu["boot_epoch"]))) if len(imu) else [],
                    "boot_epochs_uwb": sorted(map(int, np.unique(uwb["boot_epoch"]))) if len(uwb) else [],
                    "native_accepted_time": {
                        "first_global_time_ns": int(imu["global_time_ns"][0]) if len(imu) else None,
                        "last_global_time_ns": int(imu["global_time_ns"][-1]) if len(imu) else None,
                        "median_dt_s": float(np.median(dt)) if len(dt) else None,
                        "nonpositive_dt": int(np.sum(dt <= 0)) if len(dt) else 0,
                    },
                    "specific_force_mps2": {
                        **_vector_summary(acc),
                        "norm_mean": float(np.mean(acc_norm)) if len(acc_norm) else None,
                        "norm_standard_deviation": float(np.std(acc_norm)) if len(acc_norm) else None,
                        "mean_angle_to_device_plus_y_deg": angle,
                    },
                    "angular_rate_rad_s": {
                        **_vector_summary(gyro),
                        "norm_rms": float(np.sqrt(np.mean(gyro_norm ** 2))) if len(gyro_norm) else None,
                        "norm_p99": float(np.quantile(gyro_norm, .99)) if len(gyro_norm) else None,
                    },
                    "uwb_range_m": {
                        "count": int(len(valid_ranges)),
                        "median": float(np.median(valid_ranges)) if valid_ranges else None,
                        "p05": float(np.quantile(valid_ranges, .05)) if valid_ranges else None,
                        "p95": float(np.quantile(valid_ranges, .95)) if valid_ranges else None,
                    },
                }
            window["totals"] = {
                metric: int(sum(node[metric] for node in window["per_node"].values()))
                for metric in ("accepted_imu_rows", "accepted_uwb_sweeps", "valid_uwb_scalar_observations")
            }
            diagnostics["windows"][key] = window

    neutral = diagnostics["windows"]["initial_still:attempt2"]
    angles = {
        node: row["specific_force_mps2"]["mean_angle_to_device_plus_y_deg"]
        for node, row in neutral["per_node"].items()
    }
    diagnostics["neutral_gravity_consistency"] = {
        "sealed_expectation": "stationary specific force approximately device +Y",
        "per_node_angle_deg": angles,
        "maximum_angle_deg": max(angles.values()),
        "nodes_over_35_deg": sorted(node for node, angle in angles.items() if angle > 35.0),
        "qualification": "FAIL" if any(angle > 35.0 for angle in angles.values()) else "PASS",
        "interpretation": "raw register-frame evidence; no signed-permutation or yaw assumption was fitted",
    }
    tpose = diagnostics["windows"]["t_pose:attempt1"]
    pairs = (("BSFEC35", "BSFB165"), ("BSFAA61", "BSF1120"),
             ("BSF44AD", "BSF3C79"), ("BSF6C53", "BSF8BC4"))
    symmetry = []
    for left, right in pairs:
        a = np.asarray(tpose["per_node"][left]["specific_force_mps2"]["mean"], float)
        b = np.asarray(tpose["per_node"][right]["specific_force_mps2"]["mean"], float)
        symmetry.append({
            "left": left, "right": right,
            "mean_specific_force_angle_deg": float(np.degrees(np.arccos(np.clip(
                float(a @ b) / (np.linalg.norm(a) * np.linalg.norm(b)), -1.0, 1.0
            )))),
            "accepted_imu_row_ratio_left_over_right": (
                tpose["per_node"][left]["accepted_imu_rows"]
                / tpose["per_node"][right]["accepted_imu_rows"]
            ),
        })
    diagnostics["t_pose_consistency"] = {
        "bilateral_raw_register_frame": symmetry,
        "biomechanical_pose_qualification": "NOT_EVALUATED_WITHOUT_QUALIFIED_IMU_EXTRINSICS_AND_JOINT_GEOMETRY",
    }
    dump(result / "CALIBRATION_INPUT_DIAGNOSTICS.json", diagnostics)
    return diagnostics


def _candidate_slots() -> tuple[list[dict[str, Any]], dict[str, int]]:
    authority_path = FUSION / "logs/root_r6a1b_calibration_authority_20260825T084243Z/CALIBRATION_AUTHORITY_PLAN.json"
    source = load(authority_path)
    layout_path = FUSION.parent / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
    layout = load(layout_path)
    anchors = {int(row["id"]): row for row in layout["anchors"]}
    clock_path = FUSION / "logs/root_r6a1c_deferred_measurement_bridge_20260825T102823Z/WORLD_FRAME_BRIDGE_CONTRACT.json"
    clocks = load(clock_path)["clock_relationships"]["models"]
    boot_epochs = {node: int(row["boot_epoch"]) for node, row in clocks.items()}
    rows = []
    for source_row in source["slots"]:
        row = {
            "slot_id": source_row["slot_id"],
            "category": source_row["category"],
            "authority_class": source_row["authority_class"],
            "node/joint/anchor": source_row["node/joint/anchor"],
            "mathematical_type": source_row["mathematical_type"],
            "dimension": source_row["dimension"],
            "source_frame": source_row["source_frame"],
            "target_frame": source_row["target_frame"],
            "value": None,
            "uncertainty": source_row["uncertainty_model"],
            "provenance": {
                "authority_plan": str(authority_path),
                "authority_plan_sha256": sha256(authority_path),
                "candidate_source_artifact": source_row.get("candidate_source_artifact"),
                "candidate_source_checksum": source_row.get("source_checksum"),
            },
            "observability_dependencies": source_row["observability_dependencies"],
            "status": "UNRESOLVED",
            "qualification_action": source_row["qualification_action"],
            "independently_free": source_row["independently_free"],
        }
        category = row["category"]
        entity = source_row["entity_id"]
        if category == "anchor_position":
            item = anchors[int(entity)]
            row["value"] = [item[axis] / 1000.0 for axis in ("x_mm", "y_mm", "z_mm")]
            row["status"] = "QUALIFIED_CAPTURE_BOUND_IMPORT"
            row["provenance"].update({"source": str(layout_path), "source_sha256": sha256(layout_path)})
        elif category == "anchor_delay":
            item = anchors[int(entity)]
            row["value"] = [item["d_anchor_mm"] / 1000.0]
            row["status"] = "QUALIFIED_CAPTURE_BOUND_IMPORT"
            row["provenance"].update({"source": str(layout_path), "source_sha256": sha256(layout_path)})
        elif category == "time_relationship":
            item = clocks[str(entity)]
            row["value"] = [item["a_ns_per_us"], item["b_ns"]]
            row["uncertainty"] = {
                "one_sigma": [0.1, item["sigma_ns"]],
                "units": ["ns/us", "ns"],
                "capture_bound": True,
            }
            row["status"] = "QUALIFIED_CAPTURE_BOUND_IMPORT"
            row["provenance"].update({"source": str(clock_path), "source_sha256": sha256(clock_path),
                                      "boot_epoch": item["boot_epoch"]})
        elif row["authority_class"] == "ESTIMATE_WITH_PRIOR":
            row["status"] = "UNRESOLVED_SOLVER_NOT_AUTHORIZED"
        elif row["authority_class"] == "MEASURE_DIRECTLY":
            row["status"] = "UNRESOLVED_DIRECT_MEASUREMENT_ABSENT"
        elif row["authority_class"] == "DERIVE_NOT_INDEPENDENT":
            row["status"] = "UNRESOLVED_DEPENDENCY"
        elif row["authority_class"] == "FIX_BY_CONVENTION":
            row["status"] = "UNRESOLVED_NUMERIC_CONVENTION_OR_FRAME_BRIDGE"
        elif row["authority_class"] == "BLOCKED_MISSING_DEFINITION":
            row["status"] = "SOURCE_PLAN_BLOCKED_R6A1C_DERIVED_VIEW_DEPENDENCIES_UNRESOLVED"
        rows.append(row)
    return rows, boot_epochs


def calibration_audit(result: Path) -> Path:
    ledger_path = result / "CAPTURE_WINDOW_ROLE_LEDGER.json"
    if not ledger_path.exists():
        raise RuntimeError("authority-only step must complete before calibration arrays are opened")
    ledger = load(ledger_path)
    if ledger.get("numeric_sample_arrays_opened_by_this_authority_step") is not False:
        raise RuntimeError("authority ledger did not preserve metadata-only boundary")
    diagnostics = _window_diagnostics(result, ledger)
    slots, boot_epochs = _candidate_slots()
    objective_windows = [
        {
            "action_identifier": row["action_identifier"],
            "attempt_number": row["attempt_number"],
            "start_global_time_ns": row["start_timestamp"]["global_time_ns"],
            "stop_global_time_ns_exclusive": row["end_timestamp"]["global_time_ns_exclusive"],
            "window_binding_sha256": diagnostics["windows"][
                f"{row['action_identifier']}:attempt{row['attempt_number']}"
            ]["window_binding_sha256"],
        }
        for row in ledger["rows"]
        if row.get("qualification_status") == "OFFICIAL_CALIBRATION_WINDOW"
        and row.get("sample_access_authorized_in_task")
    ]
    capture_id = "v47_ten_node_body_calibration_20260814_093601"
    profile_unsealed = {
        "schema": PROFILE_SCHEMA,
        "profile_kind": "REAL_SUBJECT_SESSION",
        "profile_version": "R6A2B-R1-CANDIDATE-001",
        "qualification_verdict": "FAIL",
        "frozen": False,
        "binding": {
            "subject_id": None,
            "session_id": capture_id,
            "capture_id": capture_id,
            "raw_capture_sha256": "a491520739400064db520377ec87a9331feb6274cd42a7e6d9aad57a2b93d56a",
            "boot_epochs": boot_epochs,
            "hardware_families": EXPECTED_NODE_FAMILIES,
            "cross_family_mechanical_reuse": False,
        },
        "calibration_windows": objective_windows,
        "supplemental_initial_stationary_diagnostic": {
            key: diagnostics["windows"]["formal_initial_stationary:attempt1"][key]
            for key in ("start_global_time_ns", "stop_global_time_ns_exclusive", "window_binding_sha256")
        },
        "static_profile_freeze": "NOT_CREATED",
        "calibration_solve": "NOT_STARTED_FAIL_CLOSED_PRECONDITION",
        "calibration_code": {
            "shared_fk": str(FUSION / "src/biospur_fusion/root_r6a2a/shadow.py"),
            "shared_fk_sha256": sha256(FUSION / "src/biospur_fusion/root_r6a2a/shadow.py"),
            "historical_batch_adapter": str(FUSION / "src/biospur_fusion/calibration/articulated_batch.py"),
            "historical_batch_adapter_sha256": sha256(FUSION / "src/biospur_fusion/calibration/articulated_batch.py"),
            "profile_validator": str(FUSION / "src/biospur_fusion/root_r6a2b/real_profile.py"),
            "profile_validator_sha256": sha256(FUSION / "src/biospur_fusion/root_r6a2b/real_profile.py"),
        },
        "configuration": {
            "authority_plan_sha256": sha256(FUSION / "logs/root_r6a1b_calibration_authority_20260825T084243Z/CALIBRATION_AUTHORITY_PLAN.json"),
            "minimal_state_sha256": sha256(FUSION / "logs/root_r6a1b_calibration_authority_20260825T084243Z/MINIMAL_IDENTIFIABLE_STATE.json"),
            "window_ledger_sha256": sha256(ledger_path),
        },
        "slots": slots,
    }
    profile = seal_profile(profile_unsealed)
    profile_path = result / "REAL_SUBJECT_SESSION_CALIBRATION_PROFILE.json"
    dump(profile_path, profile)

    class_counts = {
        name: sum(row["authority_class"] == name for row in slots)
        for name in sorted({row["authority_class"] for row in slots})
    }
    status_counts = {
        name: sum(row["status"] == name for row in slots)
        for name in sorted({row["status"] for row in slots})
    }
    provenance = {
        "schema": "biospur-root-r6a2b-r1-calibration-parameter-provenance-v1",
        "source_slot_count": len(slots),
        "authority_class_counts": class_counts,
        "status_counts": status_counts,
        "resolved_capture_bound_imports": [row["slot_id"] for row in slots if row["status"] == "QUALIFIED_CAPTURE_BOUND_IMPORT"],
        "unresolved": [{"slot_id": row["slot_id"], "authority_class": row["authority_class"],
                        "status": row["status"], "qualification_action": row["qualification_action"]}
                       for row in slots if row["value"] is None],
        "slots": slots,
        "manufactured_values_entered": False,
        "session_assumption_values_entered": False,
    }
    dump(result / "CALIBRATION_PARAMETER_PROVENANCE.json", provenance)

    d0b = FUSION / "logs/v47_ten_node_body_calibration_20260814_093601/analysis_imu_multi_action_revision_d_d0b_synthetic_20260816/D0B_DATA_AND_PRIOR_OBSERVABILITY.json"
    d0b_null = FUSION / "logs/v47_ten_node_body_calibration_20260814_093601/analysis_imu_multi_action_revision_d_d0b_synthetic_20260816/D0B_NULLSPACE_AUDIT.json"
    d0b_result = load(d0b); nulls = load(d0b_null)
    observability = {
        "schema": "biospur-root-r6a2b-r1-calibration-observability-v1",
        "authorized_r6a1b_static_state": {"slot_count": 28, "dimension": 114,
                                           "real_data_jacobian": "NOT_EVALUATED",
                                           "real_data_rank": None, "real_data_nullity": None},
        "preexisting_scientific_precondition": {
            "scope": "separate 95-coordinate Revision-D structural objective; not reported as the 114-dimensional real rank",
            "source": str(d0b), "source_sha256": sha256(d0b),
            "data_only": d0b_result["data_only"],
            "data_plus_protocol_prior": d0b_result["data_plus_protocol_prior"],
            "null_direction_count": len(nulls["directions"]),
            "exact_blocker": "TORSO_EFFECTIVE_HEADING_VS_TRUNK_FUNCTIONAL_FRAME_TRADEOFF",
        },
        "shared_fk_connection": {
            "topology_loaded_and_audited": True,
            "real_calibration_objective_evaluated": False,
            "reason": "the reusable 45-parameter adapter optimizes forbidden duplicate body dimensions and does not implement the authorized 28-slot state; the qualified 114-dimensional adapter does not exist",
        },
        "posterior_uncertainty": "NOT_AVAILABLE_NO_AUTHORIZED_SOLVE",
        "bounds": "R6A1B authority contracts retained; no bound was changed",
        "leave_one_calibration_action_out": "NOT_EVALUATED_NO_AUTHORIZED_BASE_SOLVE",
        "left_right_consistency": diagnostics["t_pose_consistency"],
    }
    dump(result / "CALIBRATION_OBSERVABILITY.json", observability)

    unresolved_count = sum(row["value"] is None for row in slots)
    qualification = {
        "schema": "biospur-root-r6a2b-r1-calibration-qualification-v1",
        "verdict": "FAIL",
        "candidate_profile_frozen": False,
        "ordinary_action_reconstruction_authorized": False,
        "calibration_windows": objective_windows,
        "window_row_counts": {key: value["totals"] for key, value in diagnostics["windows"].items()},
        "ten_node_coverage": {
            "pass": all(all(row["accepted_imu_rows"] > 0 and row["accepted_uwb_sweeps"] > 0
                            for row in window["per_node"].values())
                        for window in diagnostics["windows"].values()),
            "nodes": list(NODES),
        },
        "neutral_gravity_consistency": diagnostics["neutral_gravity_consistency"],
        "t_pose_consistency": diagnostics["t_pose_consistency"],
        "parameter_accounting": {"total": len(slots), "authority_classes": class_counts,
                                  "resolved_values": len(slots) - unresolved_count,
                                  "unresolved_values": unresolved_count},
        "joint_closure": "NOT_EVALUATED_JOINT_GEOMETRY_UNRESOLVED",
        "fixed_bone_invariance": "NOT_EVALUATED_BONE_GEOMETRY_UNRESOLVED",
        "residual_distributions": {
            "imu": "NOT_EVALUATED_NO_AUTHORIZED_SHARED_OBJECTIVE",
            "uwb": "NOT_EVALUATED_NO_AUTHORIZED_SHARED_OBJECTIVE",
            "raw_input_distributions": "CALIBRATION_INPUT_DIAGNOSTICS.json",
        },
        "covariance": {"finite": None, "symmetric": None, "psd": None,
                       "status": "NOT_EVALUATED_NO_POSTERIOR"},
        "leave_one_action_out": "NOT_EVALUATED_NO_AUTHORIZED_BASE_SOLVE",
        "no_manufactured_or_session_assumption_values": True,
        "hard_failures": [
            f"{unresolved_count}_REQUIRED_SLOT_VALUES_UNRESOLVED_AFTER_26_CAPTURE_BOUND_IMPORTS",
            "DIRECT_DISTAL_LANDMARK_METROLOGY_ABSENT",
            "FIXED_JOINT_CHILD_NUMERIC_CONVENTIONS_NOT_AUTHORIZED",
            "WORLD_MODEL_GAUGE_BRIDGE_UNQUALIFIED",
            "AUTHORIZED_114_DIMENSIONAL_SHARED_FK_CALIBRATION_ADAPTER_ABSENT",
            "PREEXISTING_REVISION_D_SCIENTIFIC_NULLSPACE_92_OF_95_WITH_PRIORS",
            "FULL_SIGNED_AXIS_QUALIFICATION_PENDING",
            "PRODUCTION_BIAS_PROCESS_NOISE_UNQUALIFIED",
            "IMU_TO_UWB_PHASE_CENTRE_LEVERS_UNQUALIFIED",
        ],
        "downstream_claims_prevented": {
            "joint_centre_and_bone_geometry": "direct landmarks, joint parent/rest estimates, and fixed child conventions unresolved",
            "absolute_or_world_translation": "V4-to-navigation/world bridge and RF phase-centre levers unresolved",
            "full_segment_orientation": "torso heading/trunk frame nullspace and signed-axis qualification unresolved",
            "covariance_calibration": "real posterior and production process-noise provenance absent",
        },
        "recapture_required": False,
        "recapture_assessment": "existing C1 calibration windows have ten-node IMU/UWB coverage; current blockers are model authorization, metrology, frame bridge, and observability, not missing capture rows",
    }
    dump(result / "CALIBRATION_QUALIFICATION.json", qualification)

    validation = validate_real_profile(
        profile,
        expected_capture_id=capture_id,
        expected_session_id=capture_id,
        expected_boot_epochs=boot_epochs,
        action_authority={"role": "DEVELOPMENT_ACTION", "sample_access_authorized_in_task": True},
    )
    dump(result / "PROFILE_VALIDATOR_RESULT.json", {
        "schema": "biospur-root-r6a2b-r1-profile-validator-result-v1",
        "authorized": validation.authorized,
        "failures": list(validation.failures),
        "checks": dict(validation.checks),
        "computed_checksum_sha256": validation.computed_checksum_sha256,
        "stored_checksum_sha256": profile["profile_checksum_sha256"],
        "action_payload_opened": False,
    })
    dump(result / "ACTION_RECONSTRUCTION_DECISION.json", {
        "schema": "biospur-root-r6a2b-r1-action-decision-v1",
        "decision": "NOT_EXECUTED",
        "reason": "real profile validator refused the candidate before action selection or payload access",
        "profile_validator_failures": list(validation.failures),
        "ordinary_action_selected": None,
        "static_profile_changed_during_action": False,
        "animations_generated": [],
        "held_out_payload_access": {"golf": False, "boxing": False, "walk": False,
                                    "final_still": False, "reserved_validation": False},
    })
    dump(result / "CALIBRATION_IMPLEMENTATION_AUDIT.json", {
        "schema": "biospur-root-r6a2b-r1-calibration-implementation-audit-v1",
        "reusable": {
            "shared_topology_fk_and_estimator": "src/biospur_fusion/root_r6a2a/shadow.py",
            "historical_45_parameter_batch_fk": "src/biospur_fusion/calibration/articulated_batch.py",
            "capture_frontends": "src/biospur_fusion/calibration/real_capture.py",
        },
        "historical_batch_incompatibilities": [
            "corrected forearm identity map is not used",
            "13 broad body geometry values are independently optimized instead of the 28-slot ownership model",
            "joint-parent and joint-rest slots are not the static variables",
            "dynamic root/articulated states and asynchronous native-time factors are not jointly optimized",
            "all UWB rows are processed before calibration-window restriction",
        ],
        "previous_real_runner_fail_open": {
            "could_start_without_qualified_profile": True,
            "constructed_session_values_for_all_87_slots": True,
            "declared_real_calibration_authority": False,
            "guard_inserted_before_payload_access": [
                "src/biospur_fusion/root_r6a2b/real_shadow.py",
                "src/biospur_fusion/root_r6a2b/protocol_windows.py",
            ],
        },
        "current_runner_consumed_static_values": [
            "world gauge", "8 anchor positions", "8 anchor delays", "10 ClockModels",
            "9 joint-parent centres", "9 joint-child centres", "9 joint-rest rotations",
            "8 bone lengths", "10 IMU extrinsics", "10 tag levers", "5 anatomical points",
        ],
    })
    return profile_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--authority-only", action="store_true")
    mode.add_argument("--calibration-audit", action="store_true")
    args = parser.parse_args()
    print(authority(args.result_dir) if args.authority_only else calibration_audit(args.result_dir))


if __name__ == "__main__":
    main()
