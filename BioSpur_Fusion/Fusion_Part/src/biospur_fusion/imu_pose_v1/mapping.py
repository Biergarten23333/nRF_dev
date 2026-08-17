from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .types import SEGMENTS


EXPECTED_NODES = {
    "BSF1120", "BSF31CC", "BSF3C79", "BSF44AD", "BSF6C53",
    "BSF8BC4", "BSFAA61", "BSFB165", "BSFC2CC", "BSFEC35",
}
H9 = EXPECTED_NODES - {"BSFC2CC"}


@dataclass(frozen=True)
class FrozenOperatorMapping:
    node_to_segment: Mapping[str, str]
    capture_id: str
    session_id: str
    donning_id: str
    authority: str

    @classmethod
    def from_payload(cls, payload: dict, *, capture_id: str, session_id: str, donning_id: str) -> "FrozenOperatorMapping":
        mapping = payload.get("mapping", payload.get("node_to_role"))
        if payload.get("binding_authority", payload.get("authority_source")) != "OPERATOR_RECORDED_POST_CAPTURE":
            raise ValueError("operator-recorded post-capture authority required")
        scope = payload.get("scope", {})
        for key, expected in (("capture_id", capture_id), ("session_id", session_id), ("donning_id", donning_id)):
            observed = payload.get(key, scope.get(key))
            if observed is not None and observed != expected:
                raise ValueError(f"cross-scope mapping: {key}")
        if set(mapping) != EXPECTED_NODES or set(mapping.values()) != set(SEGMENTS) or len(mapping) != len(set(mapping.values())):
            raise ValueError("mapping must be an exact 10x10 bijection")
        if "BSFC22C" in mapping or mapping.get("BSFC2CC") != "pelvis":
            raise ValueError("BSFC2CC identity/layout invariant")
        return cls(MappingProxyType(dict(mapping)), capture_id, session_id, donning_id, "OPERATOR_RECORDED_POST_CAPTURE")

    def segment_for(self, node_id: str) -> str:
        return self.node_to_segment[node_id]

    def assert_pooling(self, nodes: set[str]) -> None:
        if "BSFC2CC" in nodes:
            raise ValueError("BSFC2CC cannot enter H9 mounting pool")
        if not nodes <= H9:
            raise ValueError("unknown H9 member")
