#!/usr/bin/env python3
"""Issue the one-pass, training-range-only diagnostic activation."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN_RELATIVE = Path("logs/c2_basis_progressive_20260829T102836Z")
AMENDMENT_RELATIVE = (
    RUN_RELATIVE
    / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
)
SEAL_RELATIVE = (
    RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_021_SOURCE_CORRECTION_001.json"
)
USER_AMENDMENT_RELATIVE = RUN_RELATIVE / "USER_CALIBRATION_POSTERIOR_AMENDMENT_001.json"
USER_AMENDMENT_SHA256 = "3465eb0705d0e308fbbd0f0d48f363917d007437b6754404483474c93c4960c8"
USER_VALIDATION_RELATIVE = RUN_RELATIVE / "USER_CALIBRATION_POSTERIOR_AMENDMENT_VALIDATION_001.json"
USER_VALIDATION_SHA256 = "16a1fc118de5681dacfd793b13b4fa0d73b51063379fd967420b14696b524872"
FOCUSED_GATE_RELATIVE = RUN_RELATIVE / "CONTINUATION_SPRINT/FOCUSED_CALIBRATION_TEST_GATE_008.json"
ONLINE_OWNER_FOCUSED_GATE_RELATIVE = (
    RUN_RELATIVE / "CONTINUATION_SPRINT/ONLINE_BRANCH_OWNER_FOCUSED_GATE_002.json"
)
BOUNDED_CENTER_GATE_RELATIVE = RUN_RELATIVE / "CONTINUATION_SPRINT/BOUNDED_CENTER_BUDGET_ROLLBACK_GATE_001.json"
USER_OWNER_AMENDMENT_RELATIVE = (
    RUN_RELATIVE / "USER_ONLINE_BRANCH_POSTERIOR_OWNER_AMENDMENT_002.json"
)
USER_OWNER_AMENDMENT_SHA256 = (
    "0dc5b1dfbada882aa7fd6d070e9f10480f278df34da28902a2b917268154cf27"
)
ACTIVATION_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ACTIVATION_009.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _semantic_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _binding(relative: Path) -> dict[str, str]:
    return {"path": str(relative), "sha256": _sha(WORKSPACE / relative)}


def _load(relative: Path) -> dict[str, Any]:
    path = WORKSPACE / relative
    if not path.is_file() or path.stat().st_mode & 0o222:
        raise RuntimeError(f"diagnostic activation input is absent or mutable: {relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("diagnostic activation requires canonical Fusion_Part")
    amendment = _load(AMENDMENT_RELATIVE)
    seal = _load(SEAL_RELATIVE)
    user_amendment = _load(USER_AMENDMENT_RELATIVE)
    user_owner_amendment = _load(USER_OWNER_AMENDMENT_RELATIVE)
    user_validation = _load(USER_VALIDATION_RELATIVE)
    focused_gate = _load(FOCUSED_GATE_RELATIVE)
    online_owner_gate = _load(ONLINE_OWNER_FOCUSED_GATE_RELATIVE)
    bounded_center_gate = _load(BOUNDED_CENTER_GATE_RELATIVE)
    from biospur_fusion.v0.c2_progressive.pipeline_runtime import (
        _validated_bounded_center_diagnostic_gate,
    )
    bounded_center_validation = _validated_bounded_center_diagnostic_gate(
        WORKSPACE,
        WORKSPACE / BOUNDED_CENTER_GATE_RELATIVE,
    )
    if (
        _sha(WORKSPACE / USER_AMENDMENT_RELATIVE) != USER_AMENDMENT_SHA256
        or _sha(WORKSPACE / USER_OWNER_AMENDMENT_RELATIVE)
        != USER_OWNER_AMENDMENT_SHA256
        or _sha(WORKSPACE / USER_VALIDATION_RELATIVE) != USER_VALIDATION_SHA256
        or amendment.get("schema")
        != "biospur-c2-active-parameter-registry-prefit-amendment-v2"
        or seal.get("schema") != "biospur-c2-p2-prefit-registry-seal-v2"
        or seal.get("amendment") != _binding(AMENDMENT_RELATIVE)
        or seal.get("settings_semantic_sha256")
        != _semantic_sha(amendment["effective_settings"])
        or seal.get("qualified_source_hashes") != amendment.get("qualified_source_hashes")
        or user_amendment.get("decision", {}).get("heldout_rule") != "UNCHANGED"
        or user_owner_amendment.get("schema")
        != "biospur-c2-user-online-branch-posterior-owner-amendment-v1"
        or user_owner_amendment.get("status")
        != "ACTIVE_APPEND_ONLY_USER_AUTHORITY_OWNER_REPLACEMENT"
        or user_owner_amendment.get("governance", {}).get(
            "maximum_successor_seals_for_this_continuation"
        ) != 1
        or user_owner_amendment.get("governance", {}).get(
            "heldout_rule_changed"
        ) is not False
        or user_validation.get("pass") is not True
        or focused_gate.get("status") != "PASS_FOCUSED_OWNER_ONLY"
        or focused_gate.get("failed") != 0
        or focused_gate.get("payload_opened") is not False
        or focused_gate.get("heldout_opened") is not False
        or online_owner_gate.get("schema")
        != "biospur-c2-online-branch-owner-focused-gate-v1"
        or online_owner_gate.get("status") != "PASS_FOCUSED_OWNER_AND_RENDERER"
        or online_owner_gate.get("failed") != 0
        or online_owner_gate.get("payload_opened") is not False
        or online_owner_gate.get("heldout_opened") is not False
        or online_owner_gate.get("qualified_source_hashes")
        != seal.get("qualified_source_hashes")
        or online_owner_gate.get("settings_semantic_sha256")
        != seal.get("settings_semantic_sha256")
        or bounded_center_validation.get("owner_state_hashes_equal") is not True
        or bounded_center_validation.get("mismatched_top_level_components") != []
    ):
        raise RuntimeError("diagnostic activation prerequisites are inconsistent")
    document = {
        "schema": "biospur-c2-real-training-range-diagnostic-activation-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "activation_role": "REAL_TRAINING_RANGE_DIAGNOSTIC_ONLY",
        "prefit_registry_seal": _binding(SEAL_RELATIVE),
        "settings_semantic_sha256": seal["settings_semantic_sha256"],
        "qualified_source_hashes": seal["qualified_source_hashes"],
        "user_calibration_posterior_amendment": _binding(USER_AMENDMENT_RELATIVE),
        "user_online_branch_posterior_owner_amendment": _binding(
            USER_OWNER_AMENDMENT_RELATIVE
        ),
        "user_calibration_posterior_amendment_validation": _binding(USER_VALIDATION_RELATIVE),
        "focused_calibration_owner_test_gate": _binding(FOCUSED_GATE_RELATIVE),
        "online_branch_owner_focused_gate": _binding(
            ONLINE_OWNER_FOCUSED_GATE_RELATIVE
        ),
        "bounded_center_budget_rollback_gate": _binding(BOUNDED_CENTER_GATE_RELATIVE),
        "bounded_center_budget_rollback_validation": bounded_center_validation,
        "execution_authorized": True,
        "training_ranges_only": True,
        "heldout_opened": False,
        "full_qualification_complete": False,
        "fresh_raw_verification_complete": False,
        "recapture_or_separate_calibration_motion": False,
        "scientific_pass_authorized": False,
        "render_label": "REAL C2 TRAINING-RANGE DIAGNOSTIC / NOT FRESH-VERIFIED / NOT PASS",
        "scope": (
            "ONE_PRIMARY_CAUSAL_DIAGNOSTIC_RUNTIME;SEALED_PREFIT_RANGES_ONLY;"
            "NO_HELDOUT;NO_FRESH_OR_FINAL_PASS_CLAIM"
        ),
    }
    path = WORKSPACE / ACTIVATION_RELATIVE
    with path.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)
    print(json.dumps({
        "activation": _binding(ACTIVATION_RELATIVE),
        "execution_authorized": True,
        "training_ranges_only": True,
        "heldout_opened": False,
        "scientific_pass_authorized": False,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
