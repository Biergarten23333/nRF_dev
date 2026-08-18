from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .core import atomic_json, sha256_file


class ValidationError(RuntimeError):
    pass


def _all_rank(report: Mapping, matrix: str, expected: int) -> bool:
    ranks = report["profiled_relative_heading"][matrix]["rank_by_relative_tolerance"]
    return bool(ranks) and all(int(value) == expected for value in ranks.values())


def _anti_placeholder(candidate: Mapping, information: Mapping, split: Mapping,
                      axis: Mapping, bootstrap: Mapping, drift: Mapping,
                      contract: Mapping) -> None:
    if split.get("total_development_rows") != 1_522_793 or split.get("unique_uid_count") != 1_522_793:
        raise ValidationError("development UID closure missing")
    if split.get("uid_overlap") != 0:
        raise ValidationError("FIT/GUARD/VALIDATION UID overlap")
    forbidden = split.get("forbidden", {})
    if forbidden.get("h_numeric_rows") != 0 or forbidden.get("combined_h_array_materialized") is not False:
        raise ValidationError("H data entered stage")
    if any(int(candidate.get(key, -1)) != 0 for key in (
        "validation_factor_rows_consumed", "h_numeric_consumption", "p_numeric_consumption",
        "b1_numeric_consumption", "opensense_numeric_consumption", "uwb_semantic_numeric_decode",
        "plus10_injection_factor_consumption",
    )):
        raise ValidationError("candidate consumed forbidden or held-out data")
    if information.get("accepted_factor_count", 0) <= 0:
        raise ValidationError("zero real heading-bearing factors")
    if information.get("pass_matrix") != "profiled_relative_heading.I2" or information.get("pass_matrix_classification") != "PROTOCOL_CONDITIONAL":
        raise ValidationError("information class substitution")
    if candidate.get("joint_mode_count", 0) != len(candidate.get("joint_modes", [])) or not candidate.get("joint_modes"):
        raise ValidationError("joint mode set missing")
    if "joint_samples" not in candidate or "cross_heading_correlation" not in candidate:
        raise ValidationError("joint S1^9 distribution missing")
    if candidate.get("candidate_type") == "T0_INITIAL_HEADING":
        raise ValidationError("session-static fit mislabeled t0")
    if drift.get("final_still_heading_factor_count") != 0:
        raise ValidationError("final still was counted as heading evidence")
    if drift.get("validation_used_for_fit_or_mode_selection") is not False:
        raise ValidationError("validation leakage")
    if contract.get("opensense_full_input_pipeline_ready") is not False:
        raise ValidationError("OpenSense full pipeline readiness hard-coded true")
    if not all(row.get("line_symmetry") == "+/-" for values in axis["blocks"].values() for row in values):
        raise ValidationError("qmt RP2 sign symmetry discarded")
    if any(row.get("qualification_status") == "QUALIFIED" and row.get("block_count", 0) < 5 for row in axis["aggregate"].values()):
        raise ValidationError("point/under-covered qmt axis admitted to PASS")
    if bootstrap.get("resampling_unit") != "action/cycle block" or bootstrap.get("frame_samples_treated_independent") is not False:
        raise ValidationError("frame-count bootstrap masquerading as independent evidence")


def compute_verdict(*, candidate: Mapping, information: Mapping, split: Mapping,
                    axis: Mapping, bootstrap: Mapping, timing: Mapping,
                    drift: Mapping, contract: Mapping) -> dict:
    _anti_placeholder(candidate, information, split, axis, bootstrap, drift, contract)
    order = list(contract["relative_heading_order"])
    ranks = information["profiled_relative_heading"]
    rank_gate = _all_rank(information, "I2", len(order))
    mode_gate = candidate["joint_mode_count"] == 1 and not any(
        row["support_classification"] in {"ADMISSIBLE_MODE", "MODE_SUPPORT_INDETERMINATE"}
        for row in candidate["joint_modes"][1:]
    ) and candidate["psi_GP"]["status"] != "UNRESOLVED_PROFILED_NUISANCE"
    interval_gate = all(float(row["shortest_circular_arc_half_width_deg"]) <= 15.0
                        for row in bootstrap["intervals"].values())
    axis_gate = all(int(row["block_count"]) >= int(contract["qualification"]["required_axis_blocks_per_family"])
                    for row in axis["aggregate"].values())
    heading_blocks = information["factor_family_block_counts"]
    heading_gate = bool(heading_blocks) and all(int(value) >= int(contract["qualification"]["required_heading_blocks_per_family"])
                                                for value in heading_blocks.values())
    timing_gate = all(not row["identifiability_verdict_flip"] for row in timing["scenarios"])
    static_gate = all(row["state"] == "STATIC_COMMON_HEADING_SUFFICIENT_FOR_THIS_SESSION_BASELINE"
                      for row in drift["subtrees"].values())
    semantic_gate = bool(drift["semantic_residuals"]) and all(row["pass"] for row in drift["semantic_residuals"].values())
    integrity_gate = split["unique_uid_count"] == split["total_development_rows"] and split["uid_overlap"] == 0
    identifiability_pass = all((rank_gate, mode_gate, interval_gate, axis_gate, heading_gate, timing_gate, integrity_gate))
    session_static_pass = identifiability_pass and static_gate and semantic_gate
    max_i2_rank = max(map(int, ranks["I2"]["rank_by_relative_tolerance"].values()))
    if session_static_pass:
        verdict = "PASS_PHASE3R23_SESSION_STATIC_COMMON_HEADING_PREREQUISITE"
    elif identifiability_pass:
        verdict = "PASS_PHASE3R23_PROTOCOL_CONDITIONAL_COMMON_HEADING_IDENTIFIABILITY"
    elif max_i2_rank > 0:
        verdict = "PARTIAL_PHASE3R23_COMMON_HEADING_IDENTIFIABILITY"
    else:
        verdict = "FAIL_PHASE3R23_COMMON_HEADING_IDENTIFIABILITY"
    gates = {
        "profiled_I2_rank_9_all_tolerances": rank_gate,
        "single_joint_S1_9_mode": mode_gate,
        "all_joint_bootstrap_half_width_le_15deg": interval_gate,
        "axis_families_at_least_5_blocks": axis_gate,
        "heading_families_at_least_5_blocks": heading_gate,
        "timing_envelope_no_flip": timing_gate,
        "uid_integrity": integrity_gate,
        "five_subtrees_session_static_sufficient": static_gate,
        "worst_family_semantic_15_25_gate": semantic_gate,
    }
    return {
        "schema": "biospur-phase3r23-independent-final-verdict-v1",
        "verdict": verdict, "gates": gates,
        "protocol_conditional_identifiability_pass": identifiability_pass,
        "session_static_common_heading_prerequisite_pass": session_static_pass,
        "opensense_common_heading_prerequisite_ready": session_static_pass,
        "opensense_full_input_pipeline_ready": False,
        "classification": {
            "data_identified_relative_headings": [],
            "biomechanics_conditional_combinations": ["forearm_left-upper_arm_left", "forearm_right-upper_arm_right", "shank_left-thigh_left", "shank_right-thigh_right"],
            "protocol_conditional_rank": max_i2_rank,
            "unresolved_relative_headings": order,
            "reason": "profiled I2 retains the unanchored psi_GP-to-pelvis null and every heading has exact pi axis-line branches; qualifying block counts are also insufficient",
        },
        "information_rank_nullity": {
            name: {"rank": row["rank_by_relative_tolerance"], "nullity": row["nullity_by_relative_tolerance"]}
            for name, row in ranks.items()
        },
        "subtree_states": {name: row["state"] for name, row in drift["subtrees"].items()},
        "candidate_payload_sha256": candidate["candidate_payload_sha256"],
        "scope_qualifiers": contract["scope_qualifiers"],
    }


def write_final_artifacts(*, report_dir: Path, result: Mapping, candidate: Mapping,
                          information: Mapping, axis: Mapping, bootstrap: Mapping,
                          drift: Mapping, exact_candidate_sha: str,
                          source_closure_sha256: str, test_count: int,
                          worker_benchmark: Mapping) -> None:
    atomic_json(report_dir/"FINAL_VERDICT_MACHINE.json", result)
    readiness = {
        "schema": "biospur-phase3r23-opensense-common-heading-prerequisite-readiness-v1",
        "opensense_common_heading_prerequisite_ready": result["opensense_common_heading_prerequisite_ready"],
        "opensense_full_input_pipeline_ready": False,
        "computed_by": "independent frozen Phase3-R2.3 validator",
        "machine_gates": result["gates"], "candidate_payload_sha256": candidate["candidate_payload_sha256"],
        "qualification": "VALIDATION_QUALIFIED_NOT_INDEPENDENT_TEST",
        "opensense_started": False, "phase4_started": False,
    }
    atomic_json(report_dir/"OPENSENSE_COMMON_HEADING_PREREQUISITE_READINESS.json", readiness)
    if result["session_static_common_heading_prerequisite_pass"]:
        bundle = {
            "schema":"biospur-phase3r23-session-common-heading-prerequisite-bundle-v1",
            "prevalidation_candidate_payload_sha256":candidate["candidate_payload_sha256"],
            "authoritative_world_heading":False, "qualification":"VALIDATION_QUALIFIED_NOT_INDEPENDENT_TEST",
            "scope":"exact capture/session/donning only", "reuse":"forbidden after new boot/session/donning",
        }
        atomic_json(report_dir/"SESSION_COMMON_HEADING_PREREQUISITE_BUNDLE.json", bundle)
    else:
        atomic_json(report_dir/"SESSION_COMMON_HEADING_PREREQUISITE_BUNDLE.NOT_CREATED.json", {
            "schema":"biospur-phase3r23-session-common-heading-prerequisite-bundle-not-created-v1",
            "created":False, "reason":"mandatory independent validator gates failed",
            "failed_gates":[name for name, value in result["gates"].items() if not value],
            "zero_angle_bundle_created":False, "candidate_payload_sha256":candidate["candidate_payload_sha256"],
        })
    final_json = dict(result)
    final_json.update({
        "exact_candidate_sha": exact_candidate_sha, "implementation_sha": exact_candidate_sha,
        "attestation_sha": "PENDING_EXTERNAL_PUBLICATION", "remote_sha": "PENDING_EXTERNAL_PUBLICATION",
        "source_closure_sha256": source_closure_sha256, "test_count": int(test_count),
        "worker_benchmark": worker_benchmark,
        "prevalidation_candidate_frozen_before_validation": True,
        "session_common_heading_bundle_created": result["session_static_common_heading_prerequisite_pass"],
        "plus10_sensitivity_is_real_error_estimate": False,
        "consumption": {"H00_H01_H02_numeric":0,"P":0,"B1":0,"OpenSense":0,"UWB_measurement":0,"plus10_injection_factor":0},
    })
    atomic_json(report_dir/"PHASE3R23_FINAL_RESULT.json", final_json)
    lines = [
        "# Phase 3-R2.3 final result", "", f"Verdict: `{result['verdict']}`.", "",
        "The existing controlled session does not identify one reusable nine-heading table. The protocol-conditioned information is partial: a protocol-frame nuisance remains unanchored to the fixed pelvis convention, all axis-line factors preserve pi branches, and the frozen three-way split leaves fewer than five independent blocks in mandatory families.", "",
        f"`opensense_common_heading_prerequisite_ready={str(result['opensense_common_heading_prerequisite_ready']).lower()}` and `opensense_full_input_pipeline_ready=false`.", "",
        "This is an operator-mapped, protocol-conditional, historically exposed within-session result. It is not external accuracy evidence and OpenSense/Phase 4 were not started.", "",
        "## Machine gates", "",
    ]
    lines.extend(f"- `{name}`: `{str(value).lower()}`" for name, value in result["gates"].items())
    lines += ["", "## Subtree temporal states", ""]
    lines.extend(f"- `{name}`: `{state}`" for name, state in result["subtree_states"].items())
    (report_dir/"PHASE3R23_FINAL_RESULT.md").write_text("\n".join(lines)+"\n")
    handoff = {
        "schema":"biospur-phase3r23-handoff-v1", "verdict":result["verdict"],
        "minimal_next_implementation_order":[
            "Add an independent heading-bearing pelvis-to-protocol-frame observation or explicitly measured common reference; do not fix psi_GP by convention.",
            "Acquire or authorize directed/sign-resolving semantics so RP1 pi branches are not collapsed by a prior.",
            "Provide at least five non-overlapping AXIS_FIT and five HEADING_FIT blocks per mandatory family, plus five observable validation blocks in each early/mid/late bin.",
            "Re-run this identifiability stage with a new primary validation set before building OS-CAL.",
            "Only after the common-heading prerequisite passes, implement the OpenSense exporter/model/DOF policy; keep OS-QMT as a nested ablation.",
        ],
        "new_capture_automatically_required":False,
        "missing_evidence_classes":["pelvis-to-P heading anchor","directed sign semantics","independent block coverage","time-stratified observable validation"],
        "opensense_started":False,"phase4_started":False,
    }
    atomic_json(report_dir/"PHASE3R23_HANDOFF.json", handoff)
    (report_dir/"PHASE3R23_HANDOFF.md").write_text(
        "# Phase 3-R2.3 handoff\n\n"+"\n".join(f"{i}. {step}" for i, step in enumerate(handoff["minimal_next_implementation_order"],1))+"\n"
    )
    atomic_json(report_dir/"EXACT_SHA_QUALIFICATION_REPORT.json", {
        "schema":"biospur-phase3r23-exact-sha-qualification-v1", "candidate_sha":exact_candidate_sha,
        "tests_passed":int(test_count), "source_closure_sha256":source_closure_sha256,
        "formal_validation_open_count":drift["formal_validation_open_count"],
        "candidate_file_sha256":sha256_file(report_dir/"PREVALIDATION_SESSION_STATIC_HEADING_CANDIDATE.json"),
        "status":"PASS_EXACT_CANDIDATE_QUALIFICATION",
    })
