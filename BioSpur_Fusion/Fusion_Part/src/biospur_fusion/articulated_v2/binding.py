from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


ROLES = (
    "pelvis", "torso", "upper_arm_left", "forearm_left", "upper_arm_right",
    "forearm_right", "thigh_left", "shank_left", "thigh_right", "shank_right",
)
EXPECTED_NODES = (
    "BSF1120", "BSF31CC", "BSF3C79", "BSF44AD", "BSF6C53",
    "BSF8BC4", "BSFAA61", "BSFB165", "BSFC2CC", "BSFEC35",
)


@dataclass(frozen=True)
class FrozenMappingBinding:
    binding_id: str
    schema_version: str
    capture_id: str
    session_id: str
    donning_id: str
    node_to_role: Mapping[str, str]
    authority_source: str
    provenance_sha256: str
    validity: str
    operator_confirmed: bool
    automatic_association_status: str

    def __post_init__(self) -> None:
        mapping = dict(self.node_to_role)
        if set(mapping) != set(EXPECTED_NODES):
            raise ValueError("TEN_UNIQUE_HARDWARE_IDS_REQUIRED")
        if len(mapping) != 10 or set(mapping.values()) != set(ROLES):
            raise ValueError("TEN_UNIQUE_SEMANTIC_ROLES_REQUIRED")
        if "BSFC22C" in mapping or any(x != x.upper() for x in mapping):
            raise ValueError("unknown ID, typo, or case ambiguity")
        if self.authority_source not in {"OPERATOR_RECORDED", "AUTOMATIC_VALIDATED"}:
            raise ValueError("unaccepted mapping authority")
        if self.authority_source == "OPERATOR_RECORDED" and not self.operator_confirmed:
            raise ValueError("EXPLICIT_OPERATOR_CONFIRMATION_REQUIRED")
        if not self.capture_id or not self.session_id or not self.donning_id:
            raise ValueError("MAPPING_BINDING_REQUIRED")
        object.__setattr__(self, "node_to_role", MappingProxyType(mapping))

    def role_to_node(self) -> Mapping[str, str]:
        return MappingProxyType({role: node for node, role in self.node_to_role.items()})


class OperatorRecordedMappingProvider:
    def load(self, payload: dict, *, expected_capture: str, expected_session: str, expected_donning: str) -> FrozenMappingBinding:
        if payload.get("authority_source") != "OPERATOR_RECORDED":
            raise ValueError("AutoMapping Top-1 substitution rejected")
        expected = (expected_capture, expected_session, expected_donning)
        actual = (payload.get("capture_id"), payload.get("session_id"), payload.get("donning_id"))
        if actual != expected:
            raise ValueError("NO_CROSS_DONNING_SILENT_REUSE")
        return FrozenMappingBinding(
            binding_id=payload["binding_id"], schema_version=payload["schema_version"],
            capture_id=payload["capture_id"], session_id=payload["session_id"], donning_id=payload["donning_id"],
            node_to_role=payload["node_to_role"], authority_source=payload["authority_source"],
            provenance_sha256=payload["provenance_sha256"], validity=payload["validity"],
            operator_confirmed=payload["operator_confirmed"],
            automatic_association_status=payload["automatic_association_status"],
        )
