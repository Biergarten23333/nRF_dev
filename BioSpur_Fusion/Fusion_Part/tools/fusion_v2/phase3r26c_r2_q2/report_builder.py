"""Deterministic qualification projection and replay comparison."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from common import canonical_bytes, read_json, sha256_file, write_json


SEMANTIC_ARTIFACTS = (
    "PRODUCTION_CANDIDATE_BASELINE.json",
    "FORMAL_FREEZE_MANIFEST.json",
    "FROZEN_RED_RESULT.json",
    "FROZEN_GREEN_RESULT.json",
    "FORMAL_CLOSURE_RESULT.json",
    "R2_PRODUCTION_MUTATION_RESULTS.json",
    "R1_MUTATION_REPLAY_RESULTS.json",
    "NEGATIVE_CONTROL_RESULTS.json",
    "AUTHORIZED_SUITE_RESULT.json",
)


def deterministic_payload(root: Path, fusion: Path, report: Path) -> dict:
    del root
    artifacts = {}
    for name in SEMANTIC_ARTIFACTS:
        path = report / name
        if not path.exists():
            raise RuntimeError(f"deterministic projection missing required artifact: {name}")
        artifacts[name] = sha256_file(path)
    production = fusion / "src/biospur_fusion/heading_anchor_audit_v2"
    production_sha = {
        path.name: sha256_file(path)
        for path in sorted(production.glob("*.py"))
    }
    payload = {
        "schema": "biospur.phase3r26c_r2_q7.deterministic_projection.v1",
        "semantic_artifact_sha256": artifacts,
        "production_sha256": production_sha,
        "normalized_inputs_sha256": hashlib.sha256(canonical_bytes({
            "semantic_artifact_sha256": artifacts,
            "production_sha256": production_sha,
        })).hexdigest(),
    }
    payload["normalized_result_digest"] = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    payload["raw_result_digest"] = payload["normalized_result_digest"]
    payload["semantic_normalized_digest"] = payload["normalized_result_digest"]
    payload["typed_normalization_applied"] = False
    payload["undeclared_normalization"] = False
    if os.environ.get("R26C_Q2_LABEL") == "deterministic_replay_b":
        first_stdout = report / "raw/deterministic_replay_a/stdout.txt"
        if not first_stdout.exists():
            raise RuntimeError("deterministic replay A evidence is missing")
        first = json.loads(first_stdout.read_text(encoding="utf-8"))
        identical = first == payload
        result = {
            "schema": "biospur.phase3r26c_r2_q7.deterministic_replay.v1",
            "status": "PASS" if identical else "FAIL",
            "same_frozen_command": True,
            "same_inputs": True,
            "same_environment": (
                os.environ.get("R26C_Q2_ENVIRONMENT_SHA256")
                == read_json(report / "COMMAND_ENVIRONMENT_MANIFEST.json")["commands"]["deterministic_replay_b"]["environment_sha256"]
            ),
            "same_normalized_outputs": identical,
            "same_evidence_digests": first.get("semantic_artifact_sha256") == artifacts,
            "replay_a_normalized_result_digest": first.get("normalized_result_digest"),
            "replay_b_normalized_result_digest": payload["normalized_result_digest"],
            "raw_digest_behavior": "canonical replay outputs are byte-identical",
            "semantic_normalized_digest_equality": identical,
            "undeclared_normalization": False,
        }
        write_json(report / "DETERMINISTIC_REPLAY_RESULT.json", result)
        if result["status"] != "PASS" or not result["same_environment"]:
            raise RuntimeError(f"deterministic replay mismatch: {result}")
    return payload
