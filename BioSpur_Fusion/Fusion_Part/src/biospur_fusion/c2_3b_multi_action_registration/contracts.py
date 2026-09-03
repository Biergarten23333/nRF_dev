"""Fail-closed ownership of the approved Revision 4 and 5 PRECODE gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


APPROVED_GATE = "logs/c2_3b_multi_action_registration_precode_revision_20260902_202054"
APPROVED_GATE_SHA256 = "464a37f181039c455d689f6ee85f093e3c33a5360088bacda69769b0cedb061a"
STATE_GATE = "logs/c2_3b_multi_action_registration_precode_revision_20260902_210920"
STATE_GATE_SHA256 = "4570481562b16903328bdaf69940b7ab3ff94b8c2ea237b0d983b71204a6bad8"
DONNING_SHA256 = "937bf8728a34aec86cdf4de0eb8bb63340e44e0b3f15d47e48413d39cb9b3d14"
MODEL_SHA256 = "89822dfe7a98a491b63249dcb43233d26174337c2710385e0d50d81c607e63f8"
PROFILE_IDS = ("WEAR_SIGMA_30DEG", "WEAR_SIGMA_45DEG", "WEAR_SIGMA_60DEG")
PROFILE_SIGMA_RAD = (
    0.5235987755982988,
    0.7853981633974483,
    1.0471975511965976,
)
SEEDS = (101, 211, 307, 401, 503, 601, 701, 809)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _verify_checksum_list(
    gate: Path, expected_list_sha256: str, expected_count: int, revision: str,
) -> int:
    rows = (gate / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    if sha256_file(gate / "SHA256SUMS") != expected_list_sha256:
        raise ValueError(f"approved {revision} checksum-list hash changed")
    seen: set[str] = set()
    for row in rows:
        expected, relative = row.split("  ./", 1)
        if relative in seen or len(expected) != 64:
            raise ValueError("malformed or duplicate approved checksum row")
        seen.add(relative)
        path = gate / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"approved {revision} object changed: {relative}")
    if len(rows) != expected_count:
        raise ValueError(f"approved {revision} object count changed: {len(rows)}")
    return len(rows)


def load_approved_contract(repo_root: Path) -> dict[str, Any]:
    """Load and deeply pin the monitor-approved synthetic-only contract."""

    root = repo_root.resolve()
    gate = root / APPROVED_GATE
    object_count = _verify_checksum_list(gate, APPROVED_GATE_SHA256, 32, "Revision 4")
    state_gate = root / STATE_GATE
    state_object_count = _verify_checksum_list(
        state_gate, STATE_GATE_SHA256, 19, "Revision 5",
    )
    supersession = _load(gate / "SUPERSESSION_CONTRACT_R4.json")
    donning = _load(gate / "DONNING_SOFT_VECTOR_CONTRACT.json")
    static = _load(gate / "STATIC_REGISTRATION_SOFT_PRIOR_R4.json")
    synthetic = _load(gate / "SYNTHETIC_WEAR_SENSITIVITY_CONTRACT_R4.json")
    state_binding = _load(state_gate / "STATE_INITIALIZATION_BINDING_R5.json")
    roundtrip = _load(state_gate / "SINGLE_CASE_ROUNDTRIP_CONTRACT_R5.json")
    runtime = _load(state_gate / "RUNTIME_AND_CACHE_CONTRACT_R5.json")
    authorized_source = _load(state_gate / "AUTHORIZED_SOURCE_CHANGE_R5.json")
    artifact_path = root / donning["artifact"]["path"]
    model_path = root / donning["model_owner"]["path"]
    if sha256_file(artifact_path) != DONNING_SHA256:
        raise ValueError("donning artifact hash changed")
    if sha256_file(model_path) != MODEL_SHA256:
        raise ValueError("official model hash changed")
    ids = tuple(row["id"] for row in static["profiles"])
    sigmas = tuple(float(row["sigma_wear_rad"]) for row in static["profiles"])
    if ids != PROFILE_IDS or sigmas != PROFILE_SIGMA_RAD:
        raise ValueError("wear sensitivity profiles changed")
    if synthetic["run_matrix"]["totals"] != {
        "logical_runs": 13992,
        "opensense_calls": 648,
    }:
        raise ValueError("synthetic run matrix changed")
    if synthetic["profiles"]["all_must_pass"] is not True:
        raise ValueError("all-profile qualification changed")
    if supersession["implementation_authorized"] is not False:
        raise ValueError("PRECODE phase flag unexpectedly changed")
    if state_binding["authorization"]["allowed_after_literal_approve"] != (
        "Only implement this initialization and aggregate-report correction, focused tests, "
        "and rerun the unchanged synthetic axis/registration stage as attempt 003."
    ):
        raise ValueError("Revision 5 authorization changed")
    if runtime["scope"] != (
        "axis_attempt_003 only; no heading, OpenSense, real C2 or full 13992-row "
        "synthetic completion is authorized by this revision"
    ):
        raise ValueError("Revision 5 runtime scope changed")
    if authorized_source["only_paths_allowed_to_change"] != [
        "src/biospur_fusion/c2_3b_multi_action_registration/contracts.py",
        "src/biospur_fusion/c2_3b_multi_action_registration/synthetic_axis_stage.py",
        "tests/test_c2_3b_multi_action_registration.py",
    ]:
        raise ValueError("Revision 5 source boundary changed")
    return {
        "gate": gate,
        "gate_sha256": APPROVED_GATE_SHA256,
        "object_count": object_count,
        "state_gate": state_gate,
        "state_gate_sha256": STATE_GATE_SHA256,
        "state_object_count": state_object_count,
        "supersession": supersession,
        "donning": donning,
        "static": static,
        "synthetic": synthetic,
        "state_binding": state_binding,
        "roundtrip": roundtrip,
        "runtime": runtime,
        "authorized_source": authorized_source,
        "artifact_path": artifact_path,
        "model_path": model_path,
    }
