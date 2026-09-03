"""Fail-closed authorization for real subject/session calibration profiles.

The checksum covers the complete profile except the checksum field itself.
Validation is deliberately independent of estimator state so it can run before
any action payload is opened.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping


PROFILE_SCHEMA = "biospur-real-subject-session-calibration-profile-v1"
EXPECTED_SLOT_COUNT = 87
EXPECTED_AUTHORITY_COUNTS = {
    "FIX_BY_CONVENTION": 10,
    "IMPORT_FROZEN_PROVENANCE": 26,
    "MEASURE_DIRECTLY": 4,
    "ESTIMATE_WITH_PRIOR": 28,
    "DERIVE_NOT_INDEPENDENT": 18,
    "BLOCKED_MISSING_DEFINITION": 1,
}
EXPECTED_NODE_FAMILIES = {
    "BSF1120": "COMMON_NINE_V0_20_PCB17",
    "BSF3C79": "COMMON_NINE_V0_20_PCB17",
    "BSF44AD": "COMMON_NINE_V0_20_PCB17",
    "BSF6C53": "COMMON_NINE_V0_20_PCB17",
    "BSF8BC4": "COMMON_NINE_V0_20_PCB17",
    "BSFAA61": "COMMON_NINE_V0_20_PCB17",
    "BSFB165": "COMMON_NINE_V0_20_PCB17",
    "BSFC2CC": "COMMON_NINE_V0_20_PCB17",
    "BSFEC35": "COMMON_NINE_V0_20_PCB17",
    "BSF31CC": "BSF31CC_V0_20_N5BL",
}


@dataclass(frozen=True)
class ProfileValidation:
    authorized: bool
    failures: tuple[str, ...]
    checks: Mapping[str, bool]
    computed_checksum_sha256: str | None


def canonical_profile_bytes(profile: Mapping[str, Any]) -> bytes:
    payload = dict(profile)
    payload.pop("profile_checksum_sha256", None)
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def profile_checksum(profile: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_profile_bytes(profile)).hexdigest()


def seal_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    sealed = dict(profile)
    sealed["profile_checksum_sha256"] = profile_checksum(sealed)
    return sealed


def assert_profile_unchanged(profile: Mapping[str, Any], frozen_checksum: str) -> None:
    """Enforce the static-profile freeze across an action reconstruction."""
    if profile_checksum(profile) != frozen_checksum:
        raise RuntimeError("STATIC_CALIBRATION_CHANGED_DURING_ACTION")


def _contains_synthetic(value: Any) -> bool:
    if isinstance(value, str):
        upper = value.upper()
        return "SYNTHETIC" in upper or "TEST_ONLY" in upper or "PLACEHOLDER" in upper
    if isinstance(value, Mapping):
        return any(_contains_synthetic(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_synthetic(item) for item in value)
    return False


def validate_real_profile(
    profile: Mapping[str, Any] | None,
    *,
    expected_capture_id: str,
    expected_session_id: str,
    expected_boot_epochs: Mapping[str, int],
    action_authority: Mapping[str, Any],
) -> ProfileValidation:
    """Return an auditable refusal instead of constructing missing defaults."""
    if profile is None:
        return ProfileValidation(False, ("PROFILE_ABSENT",), {"profile_present": False}, None)

    checks: dict[str, bool] = {}
    failures: list[str] = []

    def check(name: str, passed: bool, failure: str) -> None:
        checks[name] = bool(passed)
        if not passed:
            failures.append(failure)

    check("schema", profile.get("schema") == PROFILE_SCHEMA, "PROFILE_SCHEMA_MISMATCH")
    check("real_kind", profile.get("profile_kind") == "REAL_SUBJECT_SESSION", "PROFILE_NOT_REAL")
    check("not_synthetic", not _contains_synthetic(profile), "SYNTHETIC_OR_PLACEHOLDER_PROFILE")
    check("qualification_pass", profile.get("qualification_verdict") == "PASS", "PROFILE_QUALIFICATION_NOT_PASS")
    check("frozen", profile.get("frozen") is True, "PROFILE_NOT_FROZEN")

    binding = profile.get("binding", {})
    check("capture_binding", binding.get("capture_id") == expected_capture_id, "CAPTURE_BINDING_MISMATCH")
    check("session_binding", binding.get("session_id") == expected_session_id, "SESSION_BINDING_MISMATCH")
    check(
        "boot_epoch_binding",
        binding.get("boot_epochs") == dict(expected_boot_epochs),
        "BOOT_EPOCH_BINDING_MISMATCH",
    )
    check(
        "hardware_family_binding",
        binding.get("hardware_families") == EXPECTED_NODE_FAMILIES,
        "HARDWARE_FAMILY_BINDING_MISMATCH",
    )

    slots = profile.get("slots")
    slot_rows = slots if isinstance(slots, list) else []
    ids = [row.get("slot_id") for row in slot_rows if isinstance(row, Mapping)]
    check("slot_count", len(slot_rows) == EXPECTED_SLOT_COUNT, "PROFILE_SLOT_COUNT_MISMATCH")
    check("slot_ids_unique", len(ids) == len(set(ids)), "PROFILE_SLOT_IDS_NOT_UNIQUE")
    counts = {
        name: sum(row.get("authority_class") == name for row in slot_rows)
        for name in EXPECTED_AUTHORITY_COUNTS
    }
    check("authority_accounting", counts == EXPECTED_AUTHORITY_COUNTS, "PROFILE_AUTHORITY_ACCOUNTING_MISMATCH")
    unresolved = [
        row.get("slot_id") for row in slot_rows
        if row.get("value") is None or row.get("status") not in {
            "QUALIFIED", "QUALIFIED_CAPTURE_BOUND_IMPORT", "FIXED_BY_CONVENTION", "DERIVED_QUALIFIED"
        }
    ]
    check("required_slots_resolved", not unresolved, "PROFILE_REQUIRED_FIELDS_NULL_OR_UNQUALIFIED")

    role = action_authority.get("role")
    access = action_authority.get("sample_access_authorized_in_task")
    check("action_is_development", role == "DEVELOPMENT_ACTION", "ACTION_NOT_DEVELOPMENT")
    check("action_access_authorized", access is True, "ACTION_ACCESS_NOT_AUTHORIZED")
    check("action_not_calibration", role != "CALIBRATION", "CALIBRATION_AS_ACTION_MISUSE")
    check("action_not_heldout", role != "HELD_OUT", "HELD_OUT_ACTION_MISUSE")

    stored = profile.get("profile_checksum_sha256")
    computed: str | None
    try:
        computed = profile_checksum(profile)
    except (TypeError, ValueError):
        computed = None
    check(
        "checksum",
        isinstance(stored, str) and computed is not None and stored == computed,
        "PROFILE_CHECKSUM_MISMATCH",
    )
    return ProfileValidation(not failures, tuple(failures), checks, computed)
