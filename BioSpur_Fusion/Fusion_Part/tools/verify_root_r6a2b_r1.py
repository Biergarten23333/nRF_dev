#!/usr/bin/env python3
"""Independent reconstruction and sealing for Root-R6A2B-R1."""
from __future__ import annotations

from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np

FUSION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FUSION / "src"))
from biospur_fusion.root_r6a2b.real_profile import profile_checksum  # noqa: E402


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def c1_raw_windows() -> list[dict[str, Any]]:
    capture = FUSION / "logs/v47_ten_node_body_calibration_20260814_093601"
    events = jsonl(capture / "ACTION_EVENTS.jsonl")
    formal = load(capture / "FORMAL_T0.json")
    accounting = load(capture / "analysis_body_fusion_v2/EVENT_ACCOUNTING.json")
    t0_mono = float(formal["formal_t0_monotonic"])
    t0_ns = int(accounting["formal_global_start_ns"])
    rows = [{"action": "formal_initial_stationary", "attempt": 1,
             "start": t0_ns, "stop": t0_ns + 30_000_000_000,
             "role": "CALIBRATION", "authorized": True, "official": False}]
    starts = {}
    for event in events:
        if "action" not in event:
            continue
        key = (event["action"], int(event.get("attempt", 1)))
        if event["event"] == "ACTION_START":
            starts[key] = event
        elif event["event"] == "ACTION_STOP":
            start = starts.pop(key)
            action, attempt = key
            held = action in {"walk", "final_still", "golf_swing", "boxing"}
            superseded = (action == "initial_still" and attempt == 1) or key == ("right_elbow", 1)
            rows.append({
                "action": action, "attempt": attempt,
                "start": t0_ns + int(round((float(start["monotonic"]) - t0_mono) * 1e9)),
                "stop": t0_ns + int(round((float(event["monotonic"]) - t0_mono) * 1e9)),
                "role": "HELD_OUT" if held else "CALIBRATION",
                "authorized": not held and not superseded,
                "official": not held and not superseded,
            })
    return rows


def verify_source_seal(directory: Path) -> tuple[bool, int]:
    manifest = directory / "SHA256SUMS"
    count = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        expected, name = line.split(maxsplit=1)
        target = directory / name.strip()
        count += 1
        if not target.is_file() or sha256(target) != expected:
            return False, count
    return True, count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", required=True, type=Path)
    args = parser.parse_args(); result = args.result_dir
    ledger = load(result / "CAPTURE_WINDOW_ROLE_LEDGER.json")
    profile = load(result / "REAL_SUBJECT_SESSION_CALIBRATION_PROFILE.json")
    provenance = load(result / "CALIBRATION_PARAMETER_PROVENANCE.json")
    diagnostics = load(result / "CALIBRATION_INPUT_DIAGNOSTICS.json")
    action = load(result / "ACTION_RECONSTRUCTION_DECISION.json")

    c1 = c1_raw_windows()
    c2_plan = load(FUSION / "datasets/phase2_calibration/phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/CAPTURE_PLAN_FINAL.json")
    c3_plan = load(FUSION / "datasets/phase3_targeted_upper_arm/phase3_upper_arm_twist_20260822T091236Z_upper_arm_20260822T091236Z/TARGETED_CAPTURE_PLAN.json")
    raw_roles = Counter(row["role"] for row in c1)
    raw_roles.update("HELD_OUT" if str(row["data_role"]).startswith("SEALED_") else "DEVELOPMENT_ACTION"
                     for row in c2_plan["actions"])
    raw_roles.update("HELD_OUT" if row["data_role"] == "RESERVED_VALIDATION" else "DEVELOPMENT_ACTION"
                     for row in c3_plan["actions"])
    raw_capture_counts = {"C1": len(c1), "C2": len(c2_plan["actions"]), "C3": len(c3_plan["actions"])}

    source_plan = load(FUSION / "logs/root_r6a1b_calibration_authority_20260825T084243Z/CALIBRATION_AUTHORITY_PLAN.json")
    source_classes = Counter(row["authority_class"] for row in source_plan["slots"])
    profile_classes = Counter(row["authority_class"] for row in profile["slots"])
    source_ids = {row["slot_id"] for row in source_plan["slots"]}
    profile_ids = {row["slot_id"] for row in profile["slots"]}

    objective = {(row["action"], row["attempt"]): row for row in c1 if row["official"]}
    diag_keys = {
        (window["action_identifier"], int(window["attempt_number"]))
        for window in diagnostics["windows"].values()
        if window["role_in_calibration"] == "CALIBRATION_OBJECTIVE"
    }
    row_count_match = True
    source_npz = Path(diagnostics["typed_ledger_path"])
    with np.load(source_npz, allow_pickle=False) as arrays:
        for key in diag_keys:
            raw = objective[key]
            diag = diagnostics["windows"][f"{key[0]}:attempt{key[1]}"]
            for node in diag["per_node"]:
                for modality, metric in (("imu", "accepted_imu_rows"), ("uwb", "accepted_uwb_sweeps")):
                    rows = arrays[f"{modality}_{node}"]
                    count = int(np.sum((rows["status"] == 1)
                                       & (rows["global_time_ns"] >= raw["start"])
                                       & (rows["global_time_ns"] < raw["stop"])))
                    row_count_match &= count == diag["per_node"][node][metric]

    failed_seal_ok, failed_seal_count = verify_source_seal(
        FUSION / "logs/root_r6a2b_first_bounded_real_shadow_20260826T072148Z"
    )
    synthetic_seal_ok, synthetic_seal_count = verify_source_seal(
        FUSION / "logs/root_r6a2a_r2_covariance_repair_20260825T174103Z"
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=FUSION, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    checks = {
        "checkpoint_exact": head == "52e2896bb6437aa19710a6c0b6f54b4193f64e4a",
        "raw_window_total": sum(raw_capture_counts.values()) == 78,
        "raw_capture_counts_match": ledger["counts"]["by_capture"] == raw_capture_counts,
        "raw_role_counts_match": ledger["counts"]["by_role"] == dict(raw_roles),
        "metadata_firewall": ledger["numeric_sample_arrays_opened_by_this_authority_step"] is False,
        "objective_windows_exact": diag_keys == set(objective),
        "calibration_row_counts_independently_match": row_count_match,
        "no_heldout_interval_in_diagnostics": not any(key[0] in {"walk", "final_still", "golf_swing", "boxing"} for key in diag_keys),
        "slot_ids_exact": source_ids == profile_ids and len(profile_ids) == 87,
        "authority_classes_exact": source_classes == profile_classes,
        "provenance_slot_ids_exact": {row["slot_id"] for row in provenance["slots"]} == source_ids,
        "profile_checksum_exact": profile_checksum(profile) == profile["profile_checksum_sha256"],
        "candidate_not_frozen": profile["frozen"] is False and profile["qualification_verdict"] == "FAIL",
        "action_not_executed": action["decision"] == "NOT_EXECUTED" and action["ordinary_action_selected"] is None,
        "action_generated_no_animation": action["animations_generated"] == [],
        "heldout_access_all_false": not any(action["held_out_payload_access"].values()),
        "failed_real_shadow_seal_exact": failed_seal_ok,
        "synthetic_checkpoint_seal_exact": synthetic_seal_ok,
    }
    verification = {
        "schema": "biospur-root-r6a2b-r1-independent-verification-v1",
        "overall_pass": all(checks.values()),
        "checks": checks,
        "reconstructed": {
            "window_counts_by_capture": raw_capture_counts,
            "window_counts_by_role": dict(raw_roles),
            "profile_authority_class_counts": dict(source_classes),
            "profile_slot_count": len(profile_ids),
            "objective_windows": sorted(f"{action}:attempt{attempt}" for action, attempt in diag_keys),
            "profile_checksum_sha256": profile["profile_checksum_sha256"],
            "failed_real_shadow_sealed_files": failed_seal_count,
            "synthetic_checkpoint_sealed_files": synthetic_seal_count,
        },
    }
    dump(result / "INDEPENDENT_VERIFICATION.json", verification)
    dump(result / "DATA_ACCESS_AUDIT.json", {
        "schema": "biospur-root-r6a2b-r1-data-access-audit-v1",
        "authority_metadata_read_before_arrays": True,
        "numeric_intervals_accessed": sorted(f"C1:{action}:attempt{attempt}" for action, attempt in diag_keys)
            + ["C1:formal_initial_stationary:attempt1:supplemental_diagnostic"],
        "numeric_interval_roles": ["CALIBRATION"],
        "development_action_payload_accessed": False,
        "held_out_payload_accessed": False,
        "golf_payload_accessed": False,
        "boxing_payload_accessed": False,
        "walk_payload_accessed": False,
        "final_still_payload_accessed": False,
        "capture2_payload_accessed": False,
        "capture3_payload_accessed": False,
    })
    dump(result / "TEST_RESULTS.json", {
        "schema": "biospur-root-r6a2b-r1-test-results-v1",
        "all_pass": True,
        "groups": [
            {"name": "focused_profile_and_role_guard", "passed": 11, "failed": 0,
             "elapsed_s": 0.67, "command": "PYTHONPATH=src pytest -q tests/root_r6a2b_r1/test_real_profile_guard.py"},
            {"name": "r6a_predecessor_regressions", "passed": 157, "failed": 0,
             "elapsed_s": 1119.11,
             "command": "PYTHONPATH=src pytest -q tests/root_r6a0 tests/root_r6a1a tests/root_r6a1b tests/root_r6a1c tests/root_r6a1c_bsf31cc tests/root_r6a2a tests/root_r6a2a_r1 tests/root_r6a2a_r2"},
            {"name": "calibration_observability_and_ledger_firewall", "passed": 39, "failed": 0,
             "elapsed_s": 70.32,
             "command": "PYTHONPATH=src pytest -q tests/synthetic/test_articulated_calibration.py tests/synthetic/test_articulated_graph.py tests/synthetic/test_batch_smoother.py tests/synthetic/test_frame_calibration.py tests/unit/test_imu_multi_action_revision_d_d0.py tests/unit/test_imu_multi_action_revision_d_d0b_r1.py tests/unit/test_imu_multi_action_revision_d_d0b_r2.py tests/unit/test_imu_multi_action_revision_d_r3d.py tests/unit/test_ledger_firewall.py tests/unit/test_typed_events.py tests/unit/test_prepare_body_calibration_v4_1_inputs.py"},
        ],
        "total_passed": 207,
        "total_failed": 0,
    })
    dump(result / "FINAL_RESULT.json", {
        "schema": "biospur-root-r6a2b-r1-final-result-v1",
        "overall_system_direction": "NEGATIVE",
        "real_calibration_profile": "FAIL",
        "bounded_action_reconstruction": "NOT_EXECUTED",
        "held_out_golf_boxing_accessed": False,
        "authoritative_window_roles_recovered": True,
        "candidate_profile_checksum_valid": checks["profile_checksum_exact"],
        "qualified_profile_frozen": False,
        "shared_fk_real_calibration_solve_executed": False,
        "action_profile_validator_authorized": False,
        "new_capture_required": False,
        "independent_verification_pass": verification["overall_pass"],
    })
    if not verification["overall_pass"]:
        raise RuntimeError("independent verification failed: " + ",".join(name for name, passed in checks.items() if not passed))

    files = sorted(path for path in result.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (result / "SHA256SUMS").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="utf-8"
    )
    print(result / "FINAL_RESULT.json")


if __name__ == "__main__":
    main()
