#!/usr/bin/env python3
"""Execute the locked BioSpur V0 dual-capture closure."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from biospur_fusion.v0.contracts import dump_json, sha256_file
from biospur_fusion.v0.dual_capture import (
    PROFILE_SCHEMA,
    create_code_lock,
    load_protocol,
    run_complete_capture,
    summarize_dual_capture,
    verify_code_lock,
    write_milestone_a,
)


def _pipeline_audit(goal: Path) -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "biospur-fusion-v0-capture-bound-profile-v3",
        "title": "BioSpur Fusion V0 capture-bound profile",
        "type": "object",
        "required": [
            "profile_schema", "profile_id", "capture_id", "capture_metadata_hash",
            "calibration_input_manifest", "calibration_window_hashes",
            "capture_node_mapping_hash", "calibration_code_config_hash",
            "dependency_versions", "estimated_fields", "shared_fixed_fields_with_provenance",
            "unestimated_uncertain_fields", "identity", "sensor_to_segment_rotation",
            "functional_axes", "joint_session_reference", "bias_rest",
            "semantic_qa_calibration", "capture_attitude_frame",
            "reference_pose_hard_fk_gates",
        ],
        "properties": {
            "profile_schema": {"const": PROFILE_SCHEMA},
            "capture_id": {"type": "string", "minLength": 1},
            "capture_metadata_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "capture_node_mapping_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "calibration_code_config_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "semantic_qa_calibration": {"type": "object"},
            "capture_attitude_frame": {"type": "object"},
            "reference_pose_hard_fk_gates": {"type": "object"},
            "hxx_used_for_calibration": {"const": False},
            "profile_writeback_allowed": {"const": False}
        },
        "additionalProperties": True,
        "runtime_guard": "profile.capture_id == input.capture_id before reconstruction"
    }
    dump_json(goal / "CALIBRATION_PROFILE_SCHEMA.json", schema)
    (goal / "CALIBRATION_PIPELINE_AUDIT.md").write_text(
        """# Calibration pipeline audit

The historical `V0_SESSION_PROFILE.json` was not merely a filename-bound calibration. It imported Capture1 Layer-B donning rotations and R4 functional axes, estimated Capture1 VQF bias/rest and neutral relative rotations, but serialized a single hard-coded identity map. That map matches Capture2's forearm labels and conflicts with Capture1's authoritative mapping. The historical profile therefore cannot serve either capture as an honest complete session profile.

Its correct provenance classification is `HYBRID_NOT_CAPTURE_BOUND`: Capture1
calibration quantities plus Capture2 forearm identity. Accordingly, Capture1
forearm-dependent physical claims and all Capture2 physical claims produced
with c91 are withdrawn, while engineering evidence remains preserved.

The repaired common pipeline estimates the following once per capture/donning from the joint authorized non-H calibration set: capture-local node/body mapping binding, VQF bias/rest state, neutral-plus-T-pose gravity-observable sensor-to-segment rotations and their dispersion, neutral joint references, elbow/knee functional axes and dispersion, and calibration-derived uncertainty summaries. Replay reinitializes dynamic VQF state for each selected action and may apply exactly one common world-z display-gauge coordinate transform to every segment. It never refits per-segment extrinsics, joint rest, relative yaw, or an action pose template, never propagates state across actions, and never writes runtime state back into the profile. A fixed-profile reference posture that remains unobservable is reported `BLOCKED` rather than recalibrated.

Shared values are limited to source/algorithm, dependency versions, JY61P scale/unit conventions, the canonical body topology, mathematical conventions, and non-metric display geometry. Absolute heading/north, metric root translation, clinical zero, external accuracy, anthropometric accuracy, and skin-slip state remain unestimated.

Capture1 obtains identity from its capture binding and action/time/raw bounds from its full stored time ledger plus action authority. Capture2 obtains identity from sealed node-to-body ground truth and obtains source/clock mapping from the capture readiness report during bounded ingest. No universal node prefix or cross-capture mapping is used.

Calibration inputs are declared before execution. Hxx is disjoint from each calibration manifest and is opened only after the corresponding profile is frozen. Hxx is classified as `POST_CALIBRATION_REPLAY` and `POST_CALIBRATION_REGRESSION`, never fresh holdout evidence.

The runtime guard checks `profile.capture_id == input.capture_id` before loading action payload or entering VQF, heading, IK, FK, state export, or Viewer generation. Identifier rewriting after load is not available.

QMT_OFF preserves exact VQF attitude observations for shared IK while separately reporting zero independent heading evidence and retaining the missing-heading uncertainty term. Always-on qmt and IK-off are matched diagnostics under the same code and profile.

The bounded common-clock contract separates strict validation quality from
reconstruction safety. A strict coverage or clean-residual miss remains a
failed action/capture readiness result, while states, metrics, and Viewers may
still be produced as explicitly degraded evidence only when integer
resolution, minimum clean pairs, maximum gap, rejection, raw-residual, boot,
and monotonicity safety gates all pass. The clock sigma includes observed
residual dispersion and worst-endpoint linear-prediction dispersion.

Physical semantics use the capture-bound torso/pelvis/segment frames, never a
global Viewer axis. Same-capture initial-still and T-pose data establish
stationary noise and upper-arm posture envelopes; elbow/knee functional-axis
uncertainty contributes to a non-zero meaningful-motion threshold. Finite PCA,
numerical integrity, side dominance, FK closure, objective fit, qmt finiteness,
or positive uncertainty are engineering diagnostics and cannot promote a
physical, repeatability, or readiness PASS. Front/back polarity remains
unestimated without independent calibration and is therefore INCONCLUSIVE.
Every Viewer has separate live-runtime, locked-state-correspondence, and
independent Supervisor physical-judgment fields.

Neutral standing and straight horizontal T-pose are also guarded directly on
canonical-FK segment positions and rotations. Elevated, bent, asymmetric, or
wrong-side arms hard-fail even if joint coordinates are small, FK closure is
exact, confidence is high, or a self-report claims success. The seated-neutral
registration template binds only an unsigned sagittal-axis display
representative; it does not calibrate front/back polarity.

The executable BodyModel is instantiated from each capture-bound profile
identity; no real-capture default device map exists. The Capture1 and Capture2
forearm reversal therefore propagates through shared IK, saved state bindings,
metrics, and Viewer node labels. Session-relative joint-reference provenance
binds the exact profile capture, reference action, and calibration-window hash.

The common code lock covers the entire BioSpur Python source tree and the
complete V0 and Root-R6A0 config trees, including every imported transitive
model, factor, preintegration, adapter, and contract module. Verification
re-hashes all files and re-checks exact Python and numpy/scipy/vqf/qmt versions.
""",
        encoding="utf-8",
    )


def _run_focused_tests(root: Path) -> dict[str, Any]:
    command = [
        sys.executable, "-m", "pytest", "-q",
        "tests/v0", "tests/synthetic/test_common_clock.py",
    ]
    completed = subprocess.run(command, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return {
        "command": command, "returncode": completed.returncode,
        "output": completed.stdout, "pass": completed.returncode == 0,
    }


def _expected_replay_actions(spec: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "CALIBRATION_SELF_REPLAY": list(spec["calibration_inputs"]),
        "MAIN_SUITE_1": list(spec["main_suite_1"]),
        "MAIN_SUITE_2": list(spec["main_suite_2"]),
        "HXX": [row["action"] for row in spec["hxx"]],
    }


def _derive_reviewed_replay_evidence(
    protocol: dict[str, Any], template: dict[str, Any],
) -> dict[str, Any]:
    allowed_results = {"PASS", "DEGRADED_FAIL", "FAIL"}
    evidence: dict[str, Any] = {}
    for capture in ("CAPTURE1", "CAPTURE2"):
        expected_by_role = _expected_replay_actions(protocol["captures"][capture])
        capture_rows = [row for row in template["entries"] if row["capture"] == capture]
        roles: dict[str, Any] = {}
        for role, expected_actions in expected_by_role.items():
            rows = [row for row in capture_rows if row["role"] == role]
            observed_actions = [row["action"] for row in rows]
            results = [row.get("execution_result") for row in rows]
            if any(result not in allowed_results for result in results):
                raise ValueError(f"{capture}:{role}: invalid persisted execution result")
            exact_coverage = bool(
                len(observed_actions) == len(set(observed_actions))
                and set(observed_actions) == set(expected_actions)
            )
            if (expected_actions and not observed_actions) or "FAIL" in results:
                outcome = "FAIL"
            elif exact_coverage and all(result == "PASS" for result in results):
                outcome = "PASS"
            else:
                outcome = "PARTIAL"
            roles[role] = {
                "expected_actions": expected_actions,
                "observed_actions": observed_actions,
                "missing_actions": sorted(set(expected_actions) - set(observed_actions)),
                "unexpected_actions": sorted(set(observed_actions) - set(expected_actions)),
                "duplicate_action_entries": len(observed_actions) != len(set(observed_actions)),
                "exact_expected_set_coverage": exact_coverage,
                "execution_outcomes_by_action": {
                    row["action"]: row["execution_result"] for row in rows
                },
                "role_execution_outcome": outcome,
            }
        evidence[capture] = {"roles": roles}
    return evidence


def _finalize(
    goal: Path, comparison: dict[str, Any], regressions: dict[str, Any],
    live_qa: dict[str, Any], replay_evidence: dict[str, Any],
) -> dict[str, Any]:
    c1 = comparison["capture1"]; c2 = comparison["capture2"]
    qmt = comparison["qmt_conclusion"]["supported_by_both_captures"]
    ik = comparison["shared_ik_conclusion"]["supported_by_both_captures"]
    uncertainty = comparison["uncertainty_conclusion"]["supported_by_both_captures"]
    elbow = live_qa["CAPTURE2_06_ELBOW_LEFT_IN_FRONT"]
    c1_physical = live_qa["CAPTURE1_SUPERVISOR_PHYSICAL_COHERENCE"]
    c2_physical = live_qa["CAPTURE2_SUPERVISOR_PHYSICAL_COHERENCE"]
    physical = live_qa["SUPERVISOR_PHYSICAL_JUDGMENT"]
    c1_roles = replay_evidence["CAPTURE1"]["roles"]
    c2_roles = replay_evidence["CAPTURE2"]["roles"]
    review_complete = bool(live_qa["review_complete"])
    if live_qa.get("LIVE_VIEWER_QA") not in {"PASS", "PARTIAL", "FAIL", "NOT_ACHIEVED"}:
        raise ValueError("final LIVE_VIEWER_QA enum is invalid")
    if c1_physical not in {"PASS", "PARTIAL", "FAIL"} or c2_physical not in {
        "PASS", "PARTIAL", "FAIL",
    }:
        raise ValueError("final per-capture physical-coherence enum is invalid")
    if not review_complete or any(
        value not in {"PASS", "FAIL", "INCONCLUSIVE"}
        for value in (
            live_qa["LIVE_VIEWER_RUNTIME_QA"],
            live_qa["LIVE_VIEWER_STATE_CORRESPONDENCE"],
            live_qa["SUPERVISOR_PHYSICAL_JUDGMENT"],
        )
    ):
        raise ValueError("authoritative final requires completed non-pending external Viewer QA")
    ready = bool(
        comparison["v0_baseline_ready"]
        and live_qa["LIVE_VIEWER_QA"] == "PASS"
        and elbow == "PASS"
    )
    classification = {
        "OVERALL_SYSTEM_DIRECTION": "POSITIVE" if ready else "MIXED" if physical != "FAIL" else "NEGATIVE",
        "WHOLE_CAR_GOAL_COMPLETED": "YES" if review_complete else "NO",
        "CAPTURE1_CALIBRATION_EXECUTED": "YES",
        "CAPTURE1_PROFILE_CAPTURE_BOUND": "YES",
        "CAPTURE1_CALIBRATION_SELF_REPLAY": c1_roles["CALIBRATION_SELF_REPLAY"]["role_execution_outcome"],
        "CAPTURE1_MAIN_SUITE_1_REPLAYED": "YES" if c1_roles["MAIN_SUITE_1"]["exact_expected_set_coverage"] else "NO",
        "CAPTURE1_MAIN_SUITE_2_REPLAYED": "YES" if c1_roles["MAIN_SUITE_2"]["exact_expected_set_coverage"] else "NO",
        "CAPTURE1_HXX_REPLAYED": "YES" if c1_roles["HXX"]["exact_expected_set_coverage"] else "NO",
        "CAPTURE1_PHYSICAL_COHERENCE": c1_physical,
        "CAPTURE2_CALIBRATION_EXECUTED": "YES",
        "CAPTURE2_PROFILE_CAPTURE_BOUND": "YES",
        "CAPTURE2_CALIBRATION_SELF_REPLAY": c2_roles["CALIBRATION_SELF_REPLAY"]["role_execution_outcome"],
        "CAPTURE2_MAIN_SUITE_1_REPLAYED": "YES" if c2_roles["MAIN_SUITE_1"]["exact_expected_set_coverage"] else "NO",
        "CAPTURE2_MAIN_SUITE_2_REPLAYED": "YES" if c2_roles["MAIN_SUITE_2"]["exact_expected_set_coverage"] else "NO",
        "CAPTURE2_HXX_REPLAYED": "YES" if c2_roles["HXX"]["exact_expected_set_coverage"] else "NO",
        "CAPTURE2_06_ELBOW_LEFT_IN_FRONT": elbow,
        "CAPTURE2_PHYSICAL_COHERENCE": c2_physical,
        "SAME_CODE_CONFIG_USED_FOR_BOTH_CAPTURES": "YES",
        "CROSS_CAPTURE_SESSION_PROFILE_REUSE": "NO",
        "PROFILE_MISMATCH_RUNTIME_GUARD": "PASS" if regressions["post_lock"]["pass"] else "FAIL",
        "ALL_HXX_ACTIONS_OPENED_AND_REPLAYED": "YES" if c1_roles["HXX"]["exact_expected_set_coverage"] and c2_roles["HXX"]["exact_expected_set_coverage"] else "NO",
        "QMT_CONCLUSION_SUPPORTED_BY_BOTH_CAPTURES": "YES" if qmt else "NO",
        "SHARED_IK_CONCLUSION_SUPPORTED_BY_BOTH_CAPTURES": "YES" if ik else "NO",
        "UNCERTAINTY_CONCLUSION_SUPPORTED_BY_BOTH_CAPTURES": "YES" if uncertainty else "NO",
        "DUAL_CAPTURE_REPEATABILITY": comparison["dual_capture_repeatability"],
        "INTERNAL_PHYSICAL_COHERENCE": physical,
        "V0_BASELINE_READY": "YES" if ready else "NO",
        "READY_FOR_OPERATOR_AUTHORIZED_V0_FREEZE": "YES" if ready else "NO",
        "FINAL_V0_FROZEN": "NO",
        "LIVE_VIEWER_QA": live_qa["LIVE_VIEWER_QA"],
        "LIVE_VIEWER_RUNTIME_QA": live_qa["LIVE_VIEWER_RUNTIME_QA"],
        "LIVE_VIEWER_STATE_CORRESPONDENCE": live_qa["LIVE_VIEWER_STATE_CORRESPONDENCE"],
        "SUPERVISOR_PHYSICAL_JUDGMENT": live_qa["SUPERVISOR_PHYSICAL_JUDGMENT"],
    }
    incomplete_roles = [
        f"{capture}:{role}"
        for capture, capture_evidence in replay_evidence.items()
        for role, role_evidence in capture_evidence["roles"].items()
        if not role_evidence["exact_expected_set_coverage"]
    ]
    calibration_statuses = {
        "CAPTURE1": c1_roles["CALIBRATION_SELF_REPLAY"]["role_execution_outcome"],
        "CAPTURE2": c2_roles["CALIBRATION_SELF_REPLAY"]["role_execution_outcome"],
    }
    if live_qa["LIVE_VIEWER_RUNTIME_QA"] != "PASS" or live_qa["LIVE_VIEWER_STATE_CORRESPONDENCE"] != "PASS":
        actual_blocker = (
            "Post-review Viewer qualification failed: runtime="
            f"{live_qa['LIVE_VIEWER_RUNTIME_QA']}, state-correspondence="
            f"{live_qa['LIVE_VIEWER_STATE_CORRESPONDENCE']}, Supervisor physical="
            f"{live_qa['SUPERVISOR_PHYSICAL_JUDGMENT']}."
        )
    elif c1_physical != "PASS" or c2_physical != "PASS" or elbow != "PASS":
        actual_blocker = (
            "Post-review independent physical evidence did not pass: Capture1="
            f"{c1_physical}, Capture2={c2_physical}, Capture2 06_elbow_left in-front="
            f"{elbow}. Runtime and state correspondence do not substitute for physical coherence."
        )
    elif any(value != "PASS" for value in calibration_statuses.values()):
        actual_blocker = f"Calibration-self-replay evidence is not fully passing: {calibration_statuses}."
    elif incomplete_roles:
        actual_blocker = f"Exact expected replay-set coverage is incomplete for: {incomplete_roles}."
    elif comparison["dual_capture_repeatability"] != "PASS":
        actual_blocker = (
            "Strict dual-capture repeatability failed because preserved degraded common-clock "
            "actions remain DEGRADED_FAIL."
        )
    elif not qmt or not ik or not uncertainty:
        unsupported = [
            name for name, supported in (
                ("QMT", qmt), ("shared IK", ik), ("uncertainty", uncertainty),
            ) if not supported
        ]
        actual_blocker = (
            "Post-review V0 readiness remains unachieved because the locked dual-capture "
            f"comparison does not support: {unsupported}."
        )
    else:
        actual_blocker = (
            "Post-review V0 readiness remains unachieved because the locked comparison's "
            "v0_baseline_ready criterion is false."
        )
    questions = {
        "1": "Capture1 independently estimated its VQF bias/rest state, T-pose donning rotations, neutral joint references, four functional axes, and internal calibration uncertainty from its declared same-capture calibration inputs.",
        "2": "Capture2 independently estimated the same field classes from its own declared calibration inputs and sealed capture-local node/body mapping.",
        "3": "YES. Both profile payloads were generated from zero after the common code lock and each was reproduced byte-identically.",
        "4": "NO session-specific calibration value crossed captures. Shared values are code/hardware/mathematical/display constants only.",
        "5": (
            "Reviewed calibration-self-replay outcomes, derived only from that role, are "
            f"Capture1={calibration_statuses['CAPTURE1']} and Capture2={calibration_statuses['CAPTURE2']}; "
            f"independent per-capture physical judgments are Capture1={c1_physical} and Capture2={c2_physical}."
        ),
        "6": f"Capture1 exact Main Suite coverage: Suite 1={classification['CAPTURE1_MAIN_SUITE_1_REPLAYED']}, Suite 2={classification['CAPTURE1_MAIN_SUITE_2_REPLAYED']}.",
        "7": f"Capture1 exact Hxx coverage: {classification['CAPTURE1_HXX_REPLAYED']}.",
        "8": f"Capture2 exact Main Suite coverage: Suite 1={classification['CAPTURE2_MAIN_SUITE_1_REPLAYED']}, Suite 2={classification['CAPTURE2_MAIN_SUITE_2_REPLAYED']}.",
        "9": f"Capture2 exact Hxx coverage: {classification['CAPTURE2_HXX_REPLAYED']}.",
        "10": (
            "The explicit external judgment bound to CAPTURE2:MAIN_SUITE_1:06_elbow_left "
            f"classified the performed in-front claim as {elbow}; it was not inferred from generic physical QA."
        ),
        "11": "QMT_OFF is retained as the conservative engineering mode because it preserves exact VQF attitude and honest missing-heading uncertainty; physical coherence across both captures remains unestablished.",
        "12": "Shared IK has a matched numerical ablation on every replay, but no physical-help claim is promoted from residual or smoothing behavior.",
        "13": "The uncertainty is an internal model/observability diagnostic, not a physical error bar and not readiness evidence.",
        "14": (
            f"Aggregate LIVE_VIEWER_QA={live_qa['LIVE_VIEWER_QA']}. Its reviewed dimensions remain separated: runtime="
            f"{live_qa['LIVE_VIEWER_RUNTIME_QA']}; state-correspondence="
            f"{live_qa['LIVE_VIEWER_STATE_CORRESPONDENCE']}; Supervisor physical="
            f"{live_qa['SUPERVISOR_PHYSICAL_JUDGMENT']}. Algorithm repeatability is "
            f"{comparison['dual_capture_repeatability']}."
        ),
        "15": "YES" if ready else "NO",
        "16": "No post-review system-level blocker remains." if ready else actual_blocker,
    }
    result = {
        "schema": "biospur-fusion-v0-dual-capture-final-result-v1",
        "classification": classification, "questions": questions,
        "comparison": comparison,
        "historical_cross_capture_classification": "INVALID_CROSS_CAPTURE_PROFILE_APPLICATION",
        "live_viewer_qa": live_qa,
        "exact_replay_role_evidence": replay_evidence,
        "authoritative_final_issued_after_external_viewer_qa": review_complete,
        "historical_evidence_deleted": False, "formal_v0_freeze_created": False,
    }
    dump_json(goal / "FINAL_RESULT.json", result)
    lead = "\n".join(f"{key}: {value}" for key, value in classification.items())
    qlines = "\n".join(f"{key}. {value}" for key, value in questions.items())
    (goal / "FINAL_RESULT.md").write_text(
        f"# BioSpur Fusion V0 dual-capture closure\n\n```text\n{lead}\n```\n\n"
        "Both complete captures used the same locked implementation and independently estimated profiles. "
        "The JSON preserves exact expected/observed action sets and per-entry execution outcomes for every role. Hxx is post-calibration replay/regression, not fresh holdout evidence.\n\n"
        f"## Required questions\n\n{qlines}\n",
        encoding="utf-8",
    )
    return result


def _write_live_viewer_qa_template(goal: Path) -> tuple[dict[str, Any], str]:
    entries = []
    for capture in ("CAPTURE1", "CAPTURE2"):
        index = json.loads((goal / capture / "COMPLETE_REPLAY_INDEX.json").read_text())
        for row in index["entries"]:
            entry_id = f"{capture}:{row['role']}:{row['action']}"
            artifact_paths = {
                "profile": goal / f"PROFILE_{capture}.json",
                "viewer": Path(row["viewer_artifact"]),
                "state": Path(row["state_artifact"]),
                "metrics": Path(row["metrics_artifact"]),
                "physical_qa": Path(row["physical_qa_artifact"]),
                "access_audit": Path(row["access_artifact"]),
                "live_viewer_qa": Path(row["live_viewer_qa_artifact"]),
            }
            artifacts = {
                name: {"path": str(path.resolve()), "sha256": sha256_file(path)}
                for name, path in artifact_paths.items()
            }
            artifact_binding_sha256 = hashlib.sha256(json.dumps(
                artifacts, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            entries.append({
                "entry_id": entry_id,
                "capture": capture, "action": row["action"], "role": row["role"],
                "execution_result": row["execution_result"],
                "artifacts": artifacts,
                "artifact_binding_sha256": artifact_binding_sha256,
                "required_external_fields": {
                    "artifact_binding_sha256": artifact_binding_sha256,
                    "LIVE_VIEWER_RUNTIME_QA": ["PASS", "FAIL"],
                    "LIVE_VIEWER_STATE_CORRESPONDENCE": ["PASS", "FAIL"],
                    "SUPERVISOR_PHYSICAL_JUDGMENT": ["PASS", "FAIL", "INCONCLUSIVE"],
                    "SUPERVISOR_NOTES": "non-empty string",
                },
            })
            if capture == "CAPTURE2" and row["role"] == "MAIN_SUITE_1" and row["action"] == "06_elbow_left":
                entries[-1]["required_external_fields"]["CAPTURE2_06_ELBOW_LEFT_IN_FRONT"] = [
                    "PASS", "FAIL", "INCONCLUSIVE",
                ]
    payload = {
        "schema": "biospur-fusion-v0-live-viewer-qa-template-v1",
        "entries": entries,
        "viewer_count": len(entries),
        "run_state": "AWAITING_SUPERVISOR_VIEWER_QA",
        "immutable_after_locked_run": True,
        "external_review_must_cover_exact_entry_set": True,
        "locked_profiles_states_metrics_or_viewers_may_be_mutated_by_review": False,
        "physical_pass_inferred_from_runtime_or_state_correspondence": False,
    }
    path = goal / "LIVE_VIEWER_QA_TEMPLATE.json"
    dump_json(path, payload)
    return payload, sha256_file(path)


def _verify_template_artifacts(template: dict[str, Any]) -> dict[str, Any]:
    required_classes = {
        "profile", "viewer", "state", "metrics", "physical_qa",
        "access_audit", "live_viewer_qa",
    }
    entries = template.get("entries", [])
    entry_ids = [entry.get("entry_id") for entry in entries]
    if not entries or len(entry_ids) != len(set(entry_ids)):
        raise ValueError("immutable Viewer QA template entry set is empty or duplicated")
    verified = []
    for entry in entries:
        artifacts = entry.get("artifacts", {})
        if set(artifacts) != required_classes:
            raise ValueError(f"{entry.get('entry_id')}: incomplete immutable artifact binding")
        binding = hashlib.sha256(json.dumps(
            artifacts, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        if binding != entry.get("artifact_binding_sha256"):
            raise ValueError(f"{entry.get('entry_id')}: artifact binding digest changed")
        for artifact_class, authority in artifacts.items():
            path = Path(authority["path"])
            if not path.is_file() or sha256_file(path) != authority["sha256"]:
                raise ValueError(
                    f"{entry.get('entry_id')}: immutable {artifact_class} artifact mutation"
                )
            verified.append({
                "entry_id": entry["entry_id"], "artifact_class": artifact_class,
                "path": str(path), "sha256": authority["sha256"],
            })
    return {
        "verified_artifact_references": len(verified),
        "verification_rows_sha256": hashlib.sha256(json.dumps(
            verified, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest(),
    }


def _record_and_validate_supervisor_qa(
    goal: Path, external_path: Path,
) -> dict[str, Any]:
    goal = goal.resolve(); external_path = external_path.resolve()
    status = json.loads((goal / "RUN_STATUS.json").read_text())
    if status.get("run_state") != "AWAITING_SUPERVISOR_VIEWER_QA":
        raise ValueError("locked run is not awaiting Supervisor Viewer QA")
    template_path = goal / "LIVE_VIEWER_QA_TEMPLATE.json"
    if sha256_file(template_path) != status["live_viewer_qa_template_sha256"]:
        raise ValueError("immutable Viewer QA template changed")
    template = json.loads(template_path.read_text())
    first_actual_artifact_verification = _verify_template_artifacts(template)
    external = json.loads(external_path.read_text())
    if external.get("schema") != "biospur-fusion-v0-supervisor-live-viewer-qa-v1":
        raise ValueError("Supervisor Viewer QA schema mismatch")
    expected = {row["entry_id"]: row for row in template["entries"]}
    observed_rows = external.get("entries", [])
    observed = {row.get("entry_id"): row for row in observed_rows}
    if len(observed) != len(observed_rows) or set(observed) != set(expected):
        raise ValueError("Supervisor Viewer QA must cover the exact template entry set once")
    runtime = []; correspondence = []; physical = []
    physical_by_capture: dict[str, list[str]] = {"CAPTURE1": [], "CAPTURE2": []}
    elbow_in_front: str | None = None
    recorded_rows = []
    for entry_id, authority in expected.items():
        row = observed[entry_id]
        if row.get("artifact_binding_sha256") != authority["artifact_binding_sha256"]:
            raise ValueError(f"{entry_id}: external QA artifact binding mismatch")
        if row.get("LIVE_VIEWER_RUNTIME_QA") not in {"PASS", "FAIL"}:
            raise ValueError(f"{entry_id}: invalid live runtime judgment")
        if row.get("LIVE_VIEWER_STATE_CORRESPONDENCE") not in {"PASS", "FAIL"}:
            raise ValueError(f"{entry_id}: invalid state-correspondence judgment")
        if row.get("SUPERVISOR_PHYSICAL_JUDGMENT") not in {"PASS", "FAIL", "INCONCLUSIVE"}:
            raise ValueError(f"{entry_id}: invalid Supervisor physical judgment")
        if not isinstance(row.get("SUPERVISOR_NOTES"), str) or not row["SUPERVISOR_NOTES"].strip():
            raise ValueError(f"{entry_id}: Supervisor notes are required")
        runtime.append(row["LIVE_VIEWER_RUNTIME_QA"])
        correspondence.append(row["LIVE_VIEWER_STATE_CORRESPONDENCE"])
        physical.append(row["SUPERVISOR_PHYSICAL_JUDGMENT"])
        physical_by_capture[authority["capture"]].append(
            row["SUPERVISOR_PHYSICAL_JUDGMENT"]
        )
        if entry_id == "CAPTURE2:MAIN_SUITE_1:06_elbow_left":
            elbow_in_front = row.get("CAPTURE2_06_ELBOW_LEFT_IN_FRONT")
            if elbow_in_front not in {"PASS", "FAIL", "INCONCLUSIVE"}:
                raise ValueError(
                    f"{entry_id}: explicit external in-front judgment is required"
                )
        elif "CAPTURE2_06_ELBOW_LEFT_IN_FRONT" in row:
            raise ValueError(
                f"{entry_id}: in-front judgment is only valid on the exact elbow entry"
            )
        recorded_rows.append(dict(row))
    if elbow_in_front is None:
        raise ValueError("exact Capture2 06_elbow_left entry is absent from Viewer QA")

    def aggregate(values: list[str]) -> str:
        return (
            "FAIL" if "FAIL" in values else
            "PASS" if values and all(value == "PASS" for value in values) else
            "INCONCLUSIVE"
        )

    runtime_result = "PASS" if all(x == "PASS" for x in runtime) else "FAIL"
    correspondence_result = "PASS" if all(x == "PASS" for x in correspondence) else "FAIL"
    physical_result = aggregate(physical)
    live_viewer_result = (
        "FAIL" if "FAIL" in {runtime_result, correspondence_result, physical_result} else
        "PASS" if {runtime_result, correspondence_result, physical_result} == {"PASS"} else
        "PARTIAL"
    )
    capture1_physical = aggregate(physical_by_capture["CAPTURE1"])
    capture2_physical = aggregate(physical_by_capture["CAPTURE2"])
    summary = {
        "schema": "biospur-fusion-v0-recorded-supervisor-live-viewer-qa-v1",
        "review_complete": True,
        "external_source": str(external_path),
        "external_source_sha256": sha256_file(external_path),
        "template_sha256": status["live_viewer_qa_template_sha256"],
        "actual_artifacts_rehashed_before_external_qa_acceptance": (
            first_actual_artifact_verification
        ),
        "entry_count": len(recorded_rows), "entries": recorded_rows,
        "LIVE_VIEWER_QA": live_viewer_result,
        "LIVE_VIEWER_RUNTIME_QA": runtime_result,
        "LIVE_VIEWER_STATE_CORRESPONDENCE": correspondence_result,
        "SUPERVISOR_PHYSICAL_JUDGMENT": physical_result,
        "CAPTURE1_SUPERVISOR_PHYSICAL_COHERENCE": (
            "PARTIAL" if capture1_physical == "INCONCLUSIVE" else capture1_physical
        ),
        "CAPTURE2_SUPERVISOR_PHYSICAL_COHERENCE": (
            "PARTIAL" if capture2_physical == "INCONCLUSIVE" else capture2_physical
        ),
        "CAPTURE2_06_ELBOW_LEFT_IN_FRONT": elbow_in_front,
        "locked_profiles_states_metrics_or_viewers_mutated": False,
    }
    return summary


def _persist_live_qa_after_second_rehash(
    goal: Path, status: dict[str, Any], template: dict[str, Any], live_qa: dict[str, Any],
) -> dict[str, Any]:
    """Re-hash locked artifacts a second time before writing accepted root QA."""
    template_path = goal / "LIVE_VIEWER_QA_TEMPLATE.json"
    if sha256_file(template_path) != status["live_viewer_qa_template_sha256"]:
        raise ValueError("immutable Viewer QA template changed before final result")
    persisted = dict(live_qa)
    persisted["actual_artifacts_rehashed_immediately_before_final_result"] = (
        _verify_template_artifacts(template)
    )
    dump_json(goal / "LIVE_VIEWER_QA.json", persisted)
    return persisted


def run_all(root: Path, goal: Path) -> dict[str, Any]:
    root = root.resolve(); goal = goal.resolve()
    goal.mkdir(parents=True, exist_ok=True)
    protocol = load_protocol(root)
    ledger, _ = write_milestone_a(root, goal)
    _pipeline_audit(goal)
    pre_lock = _run_focused_tests(root)
    if not pre_lock["pass"]:
        dump_json(goal / "REGRESSION_RESULTS.json", {"pre_lock": pre_lock})
        raise RuntimeError("focused dual-capture tests failed before lock")
    lock_path = goal / "CODE_CANDIDATE_LOCK_MANIFEST.json"
    create_code_lock(root, lock_path)
    lock_sha = sha256_file(lock_path)
    post_lock = _run_focused_tests(root)
    if not post_lock["pass"]:
        dump_json(goal / "REGRESSION_RESULTS.json", {"pre_lock": pre_lock, "post_lock": post_lock})
        raise RuntimeError("focused dual-capture tests failed after lock")
    regressions = {"pre_lock": pre_lock, "post_lock": post_lock, "code_lock_sha256": lock_sha}
    dump_json(goal / "REGRESSION_RESULTS.json", regressions)
    verify_code_lock(root, lock_path, lock_sha)
    _, _, capture1 = run_complete_capture(root, goal, "CAPTURE1", protocol, ledger, lock_path, lock_sha)
    _, _, capture2 = run_complete_capture(root, goal, "CAPTURE2", protocol, ledger, lock_path, lock_sha)
    template, template_sha = _write_live_viewer_qa_template(goal)
    comparison = summarize_dual_capture(goal, capture1, capture2)
    dump_json(goal / "DUAL_CAPTURE_COMPARISON.json", comparison)
    dump_json(goal / "QMT_IK_ABLATION.json", {
        "schema": "biospur-fusion-v0-dual-capture-qmt-ik-ablation-v1",
        "qmt": comparison["qmt_conclusion"], "shared_ik": comparison["shared_ik_conclusion"],
        "per_action": comparison["per_action_ablation"],
    })
    (goal / "DUAL_CAPTURE_COMPARISON.md").write_text(
        f"# Dual-capture comparison\n\nRepeatability: **{comparison['dual_capture_repeatability']}**. "
        f"V0 baseline ready: **{comparison['v0_baseline_ready']}**. This is a same-algorithm, "
        "same-capture-profile comparison, not profile transfer.\n", encoding="utf-8",
    )
    (goal / "QMT_IK_ABLATION.md").write_text(
        "# QMT and shared-IK ablation\n\nEvery replay action in both captures executed QMT_OFF IK-on/off and "
        "always-on-qmt IK-on/off with identical frontend initialization. See the JSON for per-action distributions.\n",
        encoding="utf-8",
    )
    (goal / "REGRESSION_RESULTS.md").write_text(
        f"# Regression results\n\nPre-lock focused guard: **{pre_lock['pass']}**. Post-lock focused guard: "
        f"**{post_lock['pass']}**. Code lock: `{lock_sha}`.\n\n```text\n{post_lock['output']}\n```\n",
        encoding="utf-8",
    )
    status = {
        "schema": "biospur-fusion-v0-dual-capture-run-status-v1",
        "run_state": "AWAITING_SUPERVISOR_VIEWER_QA",
        "authoritative_final_result_issued": False,
        "whole_car_goal_completed": False,
        "code_lock_sha256": lock_sha,
        "live_viewer_qa_template": str(goal / "LIVE_VIEWER_QA_TEMPLATE.json"),
        "live_viewer_qa_template_sha256": template_sha,
        "viewer_count": template["viewer_count"],
        "next_authorized_transition": (
            "VALIDATE_EXACT_EXTERNAL_SUPERVISOR_QA_THEN_ISSUE_AUTHORITATIVE_FINAL"
        ),
    }
    dump_json(goal / "RUN_STATUS.json", status)
    (goal / "RUN_STATUS.md").write_text(
        "# Dual-capture locked run status\n\n"
        "`AWAITING_SUPERVISOR_VIEWER_QA`\n\n"
        "Both locked capture runs and numerical artifacts are complete. No authoritative "
        "`FINAL_RESULT` has been issued. The immutable Viewer QA template binds every "
        "capture profile and every per-replay Viewer, state, metrics, PHYSICAL_QA, "
        "ACCESS_AUDIT, LIVE_VIEWER_QA, action, capture, and role by exact path and SHA-256. Independent live runtime, "
        "state-correspondence, and physical judgments must be recorded before finalization.\n",
        encoding="utf-8",
    )
    return status


def finalize_after_supervisor_qa(root: Path, goal: Path, external_qa: Path) -> dict[str, Any]:
    root = root.resolve(); goal = goal.resolve()
    if (goal / "FINAL_RESULT.json").exists() or (goal / "FINAL_RESULT.md").exists():
        raise ValueError("authoritative final result already exists")
    status = json.loads((goal / "RUN_STATUS.json").read_text())
    lock_path = goal / "CODE_CANDIDATE_LOCK_MANIFEST.json"
    verify_code_lock(root, lock_path, status["code_lock_sha256"])
    live_qa = _record_and_validate_supervisor_qa(goal, external_qa)
    template_path = goal / "LIVE_VIEWER_QA_TEMPLATE.json"
    if sha256_file(template_path) != status["live_viewer_qa_template_sha256"]:
        raise ValueError("immutable Viewer QA template changed before final result")
    template = json.loads(template_path.read_text())
    replay_evidence = _derive_reviewed_replay_evidence(load_protocol(root), template)
    comparison = json.loads((goal / "DUAL_CAPTURE_COMPARISON.json").read_text())
    regressions = json.loads((goal / "REGRESSION_RESULTS.json").read_text())
    verify_code_lock(root, lock_path, status["code_lock_sha256"])
    live_qa = _persist_live_qa_after_second_rehash(
        goal, status, template, live_qa,
    )
    result = _finalize(goal, comparison, regressions, live_qa, replay_evidence)
    dump_json(goal / "RUN_STATUS_AFTER_SUPERVISOR_QA.json", {
        "schema": "biospur-fusion-v0-dual-capture-run-status-v1",
        "run_state": "AUTHORITATIVE_FINAL_ISSUED",
        "authoritative_final_result_issued": True,
        "whole_car_goal_completed": result["classification"]["WHOLE_CAR_GOAL_COMPLETED"] == "YES",
        "prior_run_status_sha256": sha256_file(goal / "RUN_STATUS.json"),
        "live_viewer_qa_sha256": sha256_file(goal / "LIVE_VIEWER_QA.json"),
        "final_result_sha256": sha256_file(goal / "FINAL_RESULT.json"),
        "locked_profiles_states_metrics_or_viewers_mutated": False,
    })
    verify_code_lock(root, lock_path, status["code_lock_sha256"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--goal-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--finalize-viewer-qa", type=Path)
    args = parser.parse_args()
    if args.all:
        result = run_all(root, args.goal_dir)
    else:
        result = finalize_after_supervisor_qa(
            root, args.goal_dir, args.finalize_viewer_qa,
        )
    print(json.dumps(result.get("classification", result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
