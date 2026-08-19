from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .core import classify_pelvis_chain, sha256_file, write_json

REQUIRED_SCOPE = [
    "OPERATOR_MAPPED_SESSION_SCOPE",
    "HISTORICALLY_EXPOSED_RETROSPECTIVE_DEVELOPMENT_ONLY",
    "GLOBAL_WORLD_YAW_UNAVAILABLE",
    "AUTOMATIC_NODE_ASSOCIATION_DEFERRED",
    "OPEN_SENSE_NOT_STARTED",
    "PHASE4_NOT_STARTED",
    "NO_UWB_MEASUREMENT_CONSUMPTION",
    "NO_EXTERNAL_ACCURACY_OR_CLINICAL_CLAIM",
]


class ValidationError(RuntimeError):
    pass


def validate_raw_metrics(metrics: Mapping, rules: Mapping) -> dict:
    expected = classify_pelvis_chain(metrics["pelvis_authority"])
    if metrics["pelvis_chain_classification"] != expected:
        raise ValidationError("pelvis-chain classification was not independently derived")
    if metrics["route"] == "A" and expected != "DIRECTED_CHAIN_COMPLETE_BOUNDED":
        raise ValidationError("Route A requires a complete bounded directed chain")
    if metrics["route"] in {"B", "C"} and metrics["single_candidate_created"]:
        raise ValidationError("Route B/C cannot create a single heading table")
    if metrics["consumption"] != rules["required_zero_consumption"]:
        raise ValidationError("forbidden consumer count is nonzero")
    if metrics["opensense_common_heading_prerequisite_ready"]:
        raise ValidationError("R2.4 cannot publish OpenSense readiness")
    if metrics["opensense_full_input_pipeline_ready"]:
        raise ValidationError("R2.4 cannot publish full OpenSense readiness")
    if metrics["r23_reproduction"]["exact_match"] is not True:
        raise ValidationError("R2.3 baseline reproduction failed")
    if metrics["actual_symmetry"]["generator_rank"] != 9:
        raise ValidationError("actual generator rank must be computed as nine")
    if metrics["actual_symmetry"]["exact_branch_count"] != 2 ** metrics["actual_symmetry"]["generator_rank"]:
        raise ValidationError("branch count does not follow computed GF(2) rank")
    if metrics["route"] == "C" and not metrics["minimal_capture_plan_created"]:
        raise ValidationError("Route C requires a machine-readable capture plan")
    for qualifier in REQUIRED_SCOPE:
        if qualifier not in metrics["scope_qualifiers"]:
            raise ValidationError(f"missing scope qualifier {qualifier}")
    verdict = (
        "RETROSPECTIVE_PHASE3R24_EXISTING_EVIDENCE_SUPPORTS_UNIQUE_COMMON_HEADING_CANDIDATE"
        if metrics["route"] == "A" and metrics["single_candidate_created"]
        else "PARTIAL_PHASE3R24_APPROXIMATE_DONNING_HEADING_ENSEMBLE_ONLY"
        if metrics["route"] == "B"
        else "FAIL_PHASE3R24_EXISTING_EVIDENCE_CANNOT_ANCHOR_PELVIS_TO_PROTOCOL"
    )
    return {
        "schema": "biospur-phase3r24-independent-verdict-v1",
        "verdict": verdict,
        "route": metrics["route"],
        "pelvis_chain_classification": expected,
        "opensense_common_heading_prerequisite_ready": False,
        "opensense_full_input_pipeline_ready": False,
        "scope_qualifiers": REQUIRED_SCOPE,
        "consumption": dict(metrics["consumption"]),
        "gates": {
            "baseline_reproduced": True,
            "physical_anchor_proven": expected == "DIRECTED_CHAIN_COMPLETE_BOUNDED",
            "single_table_allowed": metrics["route"] == "A" and metrics["single_candidate_created"],
            "all_pi_sign_ambiguity_resolved": metrics["actual_symmetry"].get("remaining_generator_rank", 9) == 0,
        },
    }


def manifest(directory: Path, names: list[str]) -> dict:
    return {
        "schema": "biospur-phase3r24-output-manifest-v1",
        "files": [
            {"name": name, "sha256": sha256_file(directory / name),
             "bytes": (directory / name).stat().st_size}
            for name in sorted(names)
        ],
    }
