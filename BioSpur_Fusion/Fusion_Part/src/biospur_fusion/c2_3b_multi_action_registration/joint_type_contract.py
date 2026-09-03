"""Fail-closed ownership for the approved joint-type mechanism micro-stage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


JOINT_TYPE_GATE = (
    "logs/c2_3b_multi_action_registration_precode_joint_type_binding_20260902_230543"
)
JOINT_TYPE_GATE_SHA256 = (
    "28738fcdbae18df717532afd79992b45bf57fafa3c6b4f4f669879edf0d5face"
)
JOINT_TYPE_GATE_OBJECTS = 10
PREFIT_BOUNDARY = (
    "BLOCKED_AT_STATIC_MOUNT_SECOND_VECTOR_OWNER_"
    "thigh_left_thigh_right_shank_left_shank_right"
)


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


def _verify_tree(path: Path, expected_sha256: str, expected_count: int) -> None:
    checksum = path / "SHA256SUMS"
    if sha256_file(checksum) != expected_sha256:
        raise ValueError(f"checksum-list digest changed: {path}")
    rows = checksum.read_text(encoding="utf-8").splitlines()
    if len(rows) != expected_count:
        raise ValueError(f"checksum object count changed: {path}: {len(rows)}")
    seen: set[str] = set()
    for row in rows:
        expected, relative = row.split("  ./", 1)
        if relative in seen or len(expected) != 64:
            raise ValueError(f"malformed checksum row: {path}: {row}")
        seen.add(relative)
        target = path / relative
        if not target.is_file() or sha256_file(target) != expected:
            raise ValueError(f"sealed object changed: {target}")


def load_joint_type_contract(repo_root: Path) -> dict[str, Any]:
    """Verify the literal-approved gate and return its executable bindings."""

    root = repo_root.resolve()
    gate = root / JOINT_TYPE_GATE
    _verify_tree(gate, JOINT_TYPE_GATE_SHA256, JOINT_TYPE_GATE_OBJECTS)
    manifest = _load(gate / "MANIFEST.json")
    qmt = _load(gate / "QMT_TERMINATION_BINDING.json")
    runtime = _load(gate / "RUNTIME_CACHE_DISK_BINDING.json")
    if manifest["implementation_authorized"] is not False:
        raise ValueError("sealed PRECODE phase changed")
    if manifest["gate_outcome"] != PREFIT_BOUNDARY:
        raise ValueError("prefit boundary changed")
    if qmt["source"]["sha256"] != (
        "492a7f5afbda2787b2c8726e56245d4226883dcf939b0a1f76cadfcdd72e1bfa"
    ):
        raise ValueError("QMT source owner changed")
    if runtime["official_call_totals"]["opensim_assembly"] != 2624:
        raise ValueError("official call total changed")
    if runtime["disk"]["projected_peak_transient_bytes"] != 1610612736:
        raise ValueError("transient disk ceiling changed")
    if runtime["gate_outcome"] != PREFIT_BOUNDARY:
        raise ValueError("runtime prefit boundary changed")
    for key in (
        "broad_synthetic_authorized",
        "heading_authorized",
        "opensense_authorized",
        "real_c2_authorized",
        "a_mutation_authorized",
    ):
        if runtime[key] is not False:
            raise ValueError(f"execution firewall changed: {key}")
    if runtime["uwb_consumed"] is not False:
        raise ValueError("UWB firewall changed")
    return {
        "gate": gate,
        "manifest": manifest,
        "qmt": qmt,
        "runtime": runtime,
        "gate_sha256": JOINT_TYPE_GATE_SHA256,
        "prefit_boundary": PREFIT_BOUNDARY,
    }
