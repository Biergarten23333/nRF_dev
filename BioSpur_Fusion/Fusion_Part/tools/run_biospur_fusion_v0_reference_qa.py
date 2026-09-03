#!/usr/bin/env python3
"""Build the four fixed-profile reference Viewers, then stop for Supervisor QA."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

from biospur_fusion.v0.contracts import dump_json, sha256_file
from biospur_fusion.v0.dual_capture import (
    calibrate_capture,
    create_code_lock,
    load_calibration_episode,
    load_protocol,
    run_action_replay,
    verify_code_lock,
    write_milestone_a,
)


REFERENCE_ROLE = "REFERENCE_VIEWER_QA"
EXPECTED_REFERENCE_ACTIONS = {
    "CAPTURE1": ("initial_still", "t_pose"),
    "CAPTURE2": ("00_initial_still", "02_t_pose"),
}
ARTIFACT_FIELDS = {
    "profile": "profile_artifact",
    "viewer": "viewer_artifact",
    "state": "state_artifact",
    "metrics": "metrics_artifact",
    "physical_qa": "physical_qa_artifact",
    "access_audit": "access_artifact",
    "live_viewer_qa": "live_viewer_qa_artifact",
    "episode_diagnostics": "episode_diagnostics_artifact",
}


def _focused_tests(root: Path) -> dict[str, Any]:
    command = [
        str(root / ".venv-v0/bin/python"), "-m", "pytest", "-q",
        "tests/v0/test_v0_dual_capture.py", "tests/v0/test_v0_raw_validation.py",
        "tests/synthetic/test_common_clock.py",
    ]
    completed = subprocess.run(
        command, cwd=root, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, check=False,
        env={**dict(os.environ), "PYTHONPATH": str(root / "src")},
    )
    return {
        "command": command,
        "returncode": completed.returncode,
        "pass": completed.returncode == 0,
        "output": completed.stdout,
    }


def _prelock_episode_diagnostics(
    root: Path, protocol: Mapping[str, Any], ledger: Mapping[str, Any],
) -> dict[str, Any]:
    captures = {}
    for capture_name in ("CAPTURE1", "CAPTURE2"):
        spec = protocol["captures"][capture_name]
        action_by_name = {
            row["action"]: row for row in ledger["captures"][capture_name]["actions"]
        }
        actions = {}
        for action in spec["calibration_inputs"]:
            _rows, access, diagnostic = load_calibration_episode(
                root, capture_name, spec, action_by_name[action],
            )
            timing = access.get("timing_access", {})
            actions[action] = {
                "diagnostic": diagnostic,
                "access_proof": {
                    "hxx_payload_opened": access["hxx_payload_opened"],
                    "invalidated_attempt_payload_opened": access.get(
                        "invalidated_attempt_payload_opened", False,
                    ),
                    "retry_or_skip_payload_opened": access.get(
                        "retry_or_skip_payload_opened", False,
                    ),
                    "all_sequential_timing_rows_within_episode_plus_two_superframes":
                        timing.get(
                            "all_sequential_timing_rows_within_action_plus_two_superframes"
                        ),
                    "every_binary_search_probe_separately_accounted": timing.get(
                        "every_binary_search_probe_separately_accounted"
                    ),
                    "no_full_file_traversal_proven_by_actual_read_union": timing.get(
                        "no_full_file_traversal_proven_by_actual_read_union"
                    ),
                    "hxx_timing_interval_bytes_touched": timing.get(
                        "golf_boxing_timing_interval_bytes_touched"
                    ),
                },
            }
        captures[capture_name] = {
            "expected_complete_non_hxx_actions": list(spec["calibration_inputs"]),
            "observed_actions": list(actions),
            "exact_coverage": list(actions) == list(spec["calibration_inputs"]),
            "all_episode_completeness_pass": all(
                row["diagnostic"]["EPISODE_COMPLETENESS"] == "PASS"
                for row in actions.values()
            ),
            "actions": actions,
        }
    return {
        "schema": "biospur-fusion-v0-prelock-complete-calibration-episode-audit-v1",
        "method": "ONE_GENERAL_SIGNAL_STABILITY_ONSET_OFFSET_RECOVERY_SEGMENTER",
        "profile_estimation_executed": False,
        "HISTORICAL_GOLF_BOXING_CONTAINER_BYTES_TOUCHED": "YES",
        "CURRENT_GOAL_GOLF_BOXING_FIELDS": "NO",
        "captures": captures,
        "all_31_complete_non_hxx_episodes_pass": all(
            row["exact_coverage"] and row["all_episode_completeness_pass"]
            for row in captures.values()
        ),
    }


def _write_profile(
    goal: Path, capture_name: str, spec: Mapping[str, Any], profile: Mapping[str, Any],
    calibration: Mapping[str, Any],
) -> tuple[Path, str]:
    profile_path = goal / f"PROFILE_{capture_name}.json"
    dump_json(profile_path, profile)
    profile_sha = sha256_file(profile_path)
    capture_dir = goal / capture_name
    capture_dir.mkdir(parents=True, exist_ok=False)
    dump_json(capture_dir / "CALIBRATION_RESULT.json", {
        **calibration,
        "profile_path": str(profile_path.resolve()),
        "profile_sha256": profile_sha,
        "profile_locked_before_reference_replay": True,
        "authorized_post_lock_replay_scope": "TWO_REFERENCE_ACTIONS_ONLY",
    })
    dump_json(goal / f"PROFILE_{capture_name}_LOCK.json", {
        "schema": "biospur-fusion-v0-capture-profile-lock-v1",
        "capture": capture_name,
        "capture_id": spec["capture_id"],
        "path": str(profile_path.resolve()),
        "sha256": profile_sha,
        "calibration_execution_count": calibration["calibration_execution_count"],
        "calibration_scope": "ONCE_PER_CAPTURE_DONNING_JOINT_AUTHORIZED_NON_H_SET",
        "post_lock_refit_allowed": False,
    })
    return profile_path, profile_sha


def _assert_profile_unchanged(path: Path, expected_sha256: str) -> None:
    if sha256_file(path) != expected_sha256:
        raise RuntimeError(f"locked capture profile changed: {path}")


def _artifact_binding(entry: Mapping[str, Any], profile_path: Path) -> dict[str, Any]:
    augmented = dict(entry)
    augmented["profile_artifact"] = str(profile_path.resolve())
    artifacts = {}
    for artifact_class, field in ARTIFACT_FIELDS.items():
        path = Path(augmented[field]).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"missing reference artifact: {path}")
        artifacts[artifact_class] = {
            "path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size,
        }
    return {
        "entry_id": f"{entry['capture']}:{REFERENCE_ROLE}:{entry['action']}",
        "capture": entry["capture"],
        "capture_id": entry["capture_id"],
        "role": REFERENCE_ROLE,
        "action": entry["action"],
        "execution_result": entry["execution_result"],
        "fixed_profile_observability": entry["fixed_profile_observability"],
        "artifacts": artifacts,
        "required_external_judgments": {
            "LIVE_VIEWER_RUNTIME_QA": ["PASS", "FAIL"],
            "LIVE_VIEWER_STATE_CORRESPONDENCE": ["PASS", "FAIL"],
            "SUPERVISOR_PHYSICAL_JUDGMENT": ["PASS", "FAIL", "INCONCLUSIVE"],
            "SUPERVISOR_REFERENCE_ACCEPTANCE": ["ACCEPT", "REJECT"],
        },
    }


def run_reference_gate(root: Path, goal: Path) -> dict[str, Any]:
    root = root.resolve()
    goal = goal.resolve()
    goal.mkdir(parents=True, exist_ok=True)
    forbidden_existing = [
        goal / "CODE_CANDIDATE_LOCK_MANIFEST.json",
        goal / "RUN_STATUS.json",
        goal / "REFERENCE_VIEWER_QA_TEMPLATE.json",
        goal / "CAPTURE1",
        goal / "CAPTURE2",
    ]
    if any(path.exists() for path in forbidden_existing):
        raise ValueError("reference gate destination already contains execution artifacts")

    protocol = load_protocol(root)
    for capture_name, expected in EXPECTED_REFERENCE_ACTIONS.items():
        actual = tuple(protocol["captures"][capture_name]["reference_actions"])
        if actual != expected:
            raise ValueError(f"{capture_name}: reference action inventory changed")
    ledger, _ = write_milestone_a(root, goal)

    pre_lock = _focused_tests(root)
    if not pre_lock["pass"]:
        dump_json(goal / "REGRESSION_RESULTS.json", {"pre_lock": pre_lock})
        raise RuntimeError("focused reference-gate tests failed before lock")
    prelock_episode_path = goal / "PRELOCK_FIVE_PHASE_EPISODE_DIAGNOSTICS.json"
    prelock_episode = _prelock_episode_diagnostics(root, protocol, ledger)
    dump_json(prelock_episode_path, prelock_episode)
    if not prelock_episode["all_31_complete_non_hxx_episodes_pass"]:
        raise RuntimeError("complete calibration episode prelock audit failed")
    lock_path = goal / "CODE_CANDIDATE_LOCK_MANIFEST.json"
    create_code_lock(root, lock_path)
    lock_sha = sha256_file(lock_path)
    post_lock = _focused_tests(root)
    if not post_lock["pass"]:
        dump_json(goal / "REGRESSION_RESULTS.json", {
            "pre_lock": pre_lock, "post_lock": post_lock, "code_lock_sha256": lock_sha,
        })
        raise RuntimeError("focused reference-gate tests failed after lock")
    dump_json(goal / "REGRESSION_RESULTS.json", {
        "pre_lock": pre_lock, "post_lock": post_lock, "code_lock_sha256": lock_sha,
    })
    verify_code_lock(root, lock_path, lock_sha)

    entries = []
    complete_episode_diagnostics = {}
    for capture_name in ("CAPTURE1", "CAPTURE2"):
        spec = protocol["captures"][capture_name]
        capture_ledger = ledger["captures"][capture_name]
        profile, calibration = calibrate_capture(
            root, capture_name, spec, capture_ledger, code_lock_sha256=lock_sha,
        )
        profile_path, profile_sha = _write_profile(
            goal, capture_name, spec, profile, calibration,
        )
        complete_episode_diagnostics[capture_name] = calibration[
            "five_phase_episode_diagnostics"
        ]
        action_by_name = {row["action"]: row for row in capture_ledger["actions"]}
        for action in EXPECTED_REFERENCE_ACTIONS[capture_name]:
            _assert_profile_unchanged(profile_path, profile_sha)
            result = run_action_replay(
                root, capture_name, spec, action_by_name[action], profile, profile_sha,
                REFERENCE_ROLE, goal / capture_name / REFERENCE_ROLE / action,
                lock_sha256=lock_sha, complete_calibration_episode=True,
            )
            _assert_profile_unchanged(profile_path, profile_sha)
            entries.append({
                **result, "capture": capture_name, "capture_id": spec["capture_id"],
            })
        verify_code_lock(root, lock_path, lock_sha)

    complete_episode_path = goal / "FIVE_PHASE_EPISODE_DIAGNOSTICS.json"
    dump_json(complete_episode_path, {
        "schema": "biospur-fusion-v0-complete-calibration-episode-diagnostics-v1",
        "candidate_lock_sha256": lock_sha,
        "capture_action_count": {"CAPTURE1": 12, "CAPTURE2": 19},
        "exact_complete_non_hxx_episode_count": 31,
        "captures": complete_episode_diagnostics,
    })

    if [(row["capture"], row["action"]) for row in entries] != [
        ("CAPTURE1", "initial_still"), ("CAPTURE1", "t_pose"),
        ("CAPTURE2", "00_initial_still"), ("CAPTURE2", "02_t_pose"),
    ]:
        raise RuntimeError("reference replay scope escaped the exact four-entry inventory")
    bindings = [
        _artifact_binding(row, goal / f"PROFILE_{row['capture']}.json") for row in entries
    ]
    template_path = goal / "REFERENCE_VIEWER_QA_TEMPLATE.json"
    dump_json(template_path, {
        "schema": "biospur-fusion-v0-supervisor-reference-viewer-qa-template-v2",
        "immutable": True,
        "candidate_lock_sha256": lock_sha,
        "reference_viewer_count": 4,
        "all_four_must_be_accepted_before_any_other_replay": True,
        "HISTORICAL_GOLF_BOXING_CONTAINER_BYTES_TOUCHED": "YES",
        "CURRENT_GOAL_GOLF_BOXING_FIELDS": "NO",
        "prelock_episode_diagnostics_artifact": {
            "path": str(prelock_episode_path.resolve()),
            "sha256": sha256_file(prelock_episode_path),
            "bytes": prelock_episode_path.stat().st_size,
        },
        "complete_episode_diagnostics_artifact": {
            "path": str(complete_episode_path.resolve()),
            "sha256": sha256_file(complete_episode_path),
            "bytes": complete_episode_path.stat().st_size,
        },
        "entries": bindings,
    })
    template_sha = sha256_file(template_path)
    verify_code_lock(root, lock_path, lock_sha)
    status = {
        "schema": "biospur-fusion-v0-reference-viewer-gate-status-v1",
        "run_state": "AWAITING_SUPERVISOR_REFERENCE_VIEWER_QA",
        "authoritative_final_result_issued": False,
        "whole_car_goal_completed": False,
        "candidate_lock_sha256": lock_sha,
        "reference_viewer_qa_template": str(template_path),
        "reference_viewer_qa_template_sha256": template_sha,
        "reference_viewer_count": 4,
        "HISTORICAL_GOLF_BOXING_CONTAINER_BYTES_TOUCHED": "YES",
        "CURRENT_GOAL_GOLF_BOXING_FIELDS": "NO",
        "reference_entries": [
            {
                "capture": row["capture"], "action": row["action"],
                "execution_result": row["execution_result"],
                "fixed_profile_observability": row["fixed_profile_observability"],
                "viewer_artifact": row["viewer_artifact"],
            }
            for row in entries
        ],
        "calibration_self_replay_executed": False,
        "main_suite_executed": False,
        "hxx_executed": False,
        "capture3_executed": False,
        "next_authorized_transition": "SUPERVISOR_REVIEW_AND_ACCEPT_ALL_FOUR_REFERENCE_VIEWERS",
    }
    dump_json(goal / "RUN_STATUS.json", status)
    (goal / "RUN_STATUS.md").write_text(
        "# Reference Viewer gate status\n\n"
        "`AWAITING_SUPERVISOR_REFERENCE_VIEWER_QA`\n\n"
        "Exactly four fixed-profile reference Viewers were generated. No calibration "
        "self-replay, main-suite, Hxx, or Capture3 action was executed. All four require "
        "independent runtime, state-correspondence, physical, and explicit acceptance "
        "judgments before any broader replay is authorized.\n",
        encoding="utf-8",
    )
    return status


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--goal-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = run_reference_gate(root, args.goal_dir)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
