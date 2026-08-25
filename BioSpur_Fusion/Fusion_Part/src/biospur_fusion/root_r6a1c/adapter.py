"""Fail-closed prerequisite adapter for a future Root-R6A2 consumer.

This is not fusion code.  It only validates versioned contract ownership and
authorization boundaries before a later stage may construct any state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


CORRECT_NODE_MAP = {
    "BSFEC35": "forearm_left",
    "BSFB165": "forearm_right",
    "BSFAA61": "upper_arm_left",
    "BSF1120": "upper_arm_right",
    "BSF31CC": "torso",
    "BSFC2CC": "pelvis",
    "BSF44AD": "thigh_left",
    "BSF3C79": "thigh_right",
    "BSF6C53": "shank_left",
    "BSF8BC4": "shank_right",
}

OBSOLETE_WRIST_MAP = {
    **CORRECT_NODE_MAP,
    "BSFEC35": "forearm_right",
    "BSFB165": "forearm_left",
}

MODES = {
    "SYNTHETIC_FAULT_ARCHITECTURE",
    "REAL_SHADOW",
    "REAL_BODY_UPDATE",
}


@dataclass(frozen=True)
class AuthorizationDecision:
    mode: str
    authorized: bool
    blockers: tuple[str, ...]
    contract_schema: str = "biospur-root-r6a1c-r6a2-prerequisite-decision-v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.contract_schema,
            "mode": self.mode,
            "authorized": self.authorized,
            "blockers": list(self.blockers),
        }


def _mapping_blockers(mapping: Mapping[str, str]) -> list[str]:
    blockers: list[str] = []
    if len(mapping) != 10 or len(set(mapping)) != 10:
        blockers.append("UNKNOWN_OR_DUPLICATE_NODE_IDENTITY")
    if dict(mapping) == OBSOLETE_WRIST_MAP:
        blockers.append("OBSOLETE_WRIST_MAP")
    if dict(mapping) != CORRECT_NODE_MAP:
        blockers.append("NODE_IDENTITY_MAP_NOT_R6A1C_V1")
    if mapping.get("BSFC2CC") != "pelvis" or "BSFC22C" in mapping:
        blockers.append("PELVIS_IDENTITY_NOT_BSFC2CC")
    return blockers


def evaluate_r6a2_request(request: Mapping[str, Any]) -> AuthorizationDecision:
    """Return every fail-closed blocker without constructing a body state."""
    mode = str(request.get("mode", "UNKNOWN"))
    blockers: list[str] = []
    if mode not in MODES:
        blockers.append("UNKNOWN_MODE")
    mapping = request.get("node_identity_map")
    if not isinstance(mapping, Mapping):
        blockers.append("UNKNOWN_OR_DUPLICATE_NODE_IDENTITY")
    else:
        blockers.extend(_mapping_blockers({str(key): str(value) for key, value in mapping.items()}))
    if request.get("identity_contract_schema") != "biospur-root-r6a1c-node-identity-donning-contract-v1":
        blockers.append("IDENTITY_CONTRACT_VERSION_MISSING")
    if request.get("donning_contract_schema") != "biospur-root-r6a1c-node-identity-donning-contract-v1":
        blockers.append("DONNING_CONTRACT_VERSION_MISSING")
    if request.get("deferred_measurement_schema") != "biospur-root-r6a1c-deferred-measurement-contract-v1":
        blockers.append("DEFERRED_MEASUREMENT_CONTRACT_VERSION_MISSING")
    if request.get("hardware_frame_schema") != "biospur-root-r6a1c-hardware-frame-lever-audit-v1":
        blockers.append("HARDWARE_FRAME_CONTRACT_VERSION_MISSING")
    if request.get("world_frame_schema") != "biospur-root-r6a1c-world-frame-bridge-contract-v1":
        blockers.append("WORLD_FRAME_CONTRACT_VERSION_MISSING")

    owners = request.get("independent_parameter_owners", ())
    derived = request.get("derived_parameter_ids", ())
    if len(owners) != len(set(owners)) or set(owners) & set(derived):
        blockers.append("DUPLICATE_INDEPENDENT_PARAMETER_OWNERSHIP")

    geometry_kind = request.get("geometry_kind")
    hardware_revisions = request.get("hardware_revision_ids", {})
    if mode == "SYNTHETIC_FAULT_ARCHITECTURE":
        if geometry_kind != "SYNTHETIC_TEST_ONLY":
            blockers.append("SYNTHETIC_GEOMETRY_LABEL_MISSING")
        if request.get("synthetic_world_gauge") is not True:
            blockers.append("SYNTHETIC_WORLD_GAUGE_MISSING")
        if request.get("writes_real_registry") is not False:
            blockers.append("SYNTHETIC_VALUE_PRESENTED_AS_REAL")
        if not hardware_revisions or any(not str(value).startswith("SYNTHETIC_") for value in hardware_revisions.values()):
            blockers.append("MISSING_HARDWARE_REVISION")
    elif mode in {"REAL_SHADOW", "REAL_BODY_UPDATE"}:
        if geometry_kind == "SYNTHETIC_TEST_ONLY":
            blockers.append("SYNTHETIC_GEOMETRY_PRESENTED_AS_REAL")
        if request.get("all_real_geometry_qualified") is not True:
            blockers.append("NULL_REAL_GEOMETRY_PRESENTED_AS_CALIBRATED")
        if not hardware_revisions or any(value in (None, "", "UNKNOWN") for value in hardware_revisions.values()):
            blockers.append("MISSING_HARDWARE_REVISION")
        if request.get("signed_axis_validation") != "VERIFIED_REAL_CAPTURE":
            blockers.append("SIGNED_AXIS_VALIDATION_PENDING")
        if request.get("process_noise_qualified") is not True:
            blockers.append("PROCESS_NOISE_UNQUALIFIED")
        if request.get("hardware_levers_qualified") is not True:
            blockers.append("HARDWARE_LEVER_UNQUALIFIED")
        if request.get("v4_to_navigation_qualified") is not True:
            blockers.append("UNQUALIFIED_V4_TO_NAVIGATION_BRIDGE")
        anchor_capture = request.get("anchor_capture_binding")
        clock_capture = request.get("clock_capture_binding")
        if not anchor_capture or not clock_capture or anchor_capture != clock_capture:
            blockers.append("CROSS_CAPTURE_CLOCK_OR_ANCHOR_REUSE")
        if request.get("clock_boot_epochs_match") is not True:
            blockers.append("CLOCK_BOOT_EPOCH_MISMATCH")
    if mode == "REAL_BODY_UPDATE":
        blockers.append("ROOT_R6A1C_REAL_BODY_UPDATE_PROHIBITED")
    return AuthorizationDecision(mode, not blockers, tuple(dict.fromkeys(blockers)))
