"""Exact dependency graph construction and dual-layer factor policies."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contracts import FactorLedger, LineageError
from .data import C1Data


@dataclass(frozen=True)
class DependencyPolicyResult:
    name: str
    ledger: FactorLedger
    t4_factor_events: tuple[int, ...]
    raw_factor_events: tuple[tuple[int, int], ...]
    t4_role: str

    def audit(self) -> dict:
        return {"name": self.name, "t4_role": self.t4_role,
                "t4_factor_count": len(self.t4_factor_events),
                "raw_factor_count": len(self.raw_factor_events), **self.ledger.audit()}


def t4_only(data: C1Data, events: np.ndarray | None = None) -> DependencyPolicyResult:
    chosen = np.arange(data.event_count) if events is None else np.asarray(events, int)
    ledger = FactorLedger()
    for event in chosen:
        ledger.add_t4_factor(data.t4_event_id(int(event)), data.constituent_ids(int(event)))
    return DependencyPolicyResult("T4_ONLY", ledger, tuple(map(int, chosen)), (), "ACTIVE_LIKELIHOOD")


def raw_only(data: C1Data, events: np.ndarray | None = None, *, t4_role: str = "DIAGNOSTIC_ONLY") -> DependencyPolicyResult:
    chosen = np.arange(data.event_count) if events is None else np.asarray(events, int)
    ledger = FactorLedger(); rows: list[tuple[int, int]] = []
    for event in chosen:
        for slot in np.flatnonzero(data.raw_valid[event]):
            ledger.add_raw_factor(f"RAW:{data.raw_event_id(int(event), int(slot))}", data.raw_event_id(int(event), int(slot)))
            rows.append((int(event), int(slot)))
    return DependencyPolicyResult("RAW_ONLY", ledger, (), tuple(rows), t4_role)


def t4_initialized_raw(data: C1Data, events: np.ndarray | None = None) -> DependencyPolicyResult:
    return raw_only(data, events, t4_role="NUMERICAL_INITIALIZER_AND_HEALTH_CONTEXT_NOT_A_FACTOR")


def disjoint_hybrid(data: C1Data, events: np.ndarray | None = None) -> DependencyPolicyResult:
    chosen = np.arange(data.event_count) if events is None else np.asarray(events, int)
    # An epoch is indivisible: all tag records from even epochs use raw factors;
    # all records from odd epochs use their replacing T4 factor.
    parity = data.epoch[chosen] & 1
    t4_events = chosen[parity == 1]; raw_events = chosen[parity == 0]
    ledger = FactorLedger(); raw_rows: list[tuple[int, int]] = []
    for event in t4_events:
        ledger.add_t4_factor(data.t4_event_id(int(event)), data.constituent_ids(int(event)))
    for event in raw_events:
        for slot in np.flatnonzero(data.raw_valid[event]):
            ledger.add_raw_factor(f"RAW:{data.raw_event_id(int(event), int(slot))}", data.raw_event_id(int(event), int(slot)))
            raw_rows.append((int(event), int(slot)))
    return DependencyPolicyResult("DISJOINT_EPOCH_REPLACEMENT", ledger, tuple(map(int, t4_events)),
                                  tuple(raw_rows), "ACTIVE_ONLY_ON_ODD_EPOCHS_REPLACING_CONSTITUENT_RANGES")


def negative_controls(data: C1Data) -> dict:
    event = 0
    members = data.constituent_ids(event)
    results = {}
    ledger = FactorLedger(); ledger.add_t4_factor(data.t4_event_id(event), members)
    try:
        ledger.add_raw_factor("NC_NAIVE_RAW", members[0])
        results["naive_raw_plus_t4"] = {"detected": False}
    except LineageError as error:
        results["naive_raw_plus_t4"] = {"detected": True, "classification": "REJECTED_NAIVE_T4_RAW_DOUBLE_COUNTING",
                                         "error": str(error)}
    ledger = FactorLedger(); ledger.add_raw_factor("RAW_A", members[0])
    try:
        ledger.add_raw_factor("RAW_B", members[0]); results["raw_event_two_factors"] = {"detected": False}
    except LineageError as error:
        results["raw_event_two_factors"] = {"detected": True, "error": str(error)}
    corrupted = list(members); corrupted[0] = data.raw_event_id(1, 0)
    actual = set(members); reported = set(corrupted)
    results["constituent_lineage_corruption"] = {
        "detected": actual != reported,
        "missing": sorted(actual - reported), "unexpected": sorted(reported - actual),
    }
    production_rejections = {
        "per_node_world_rotations": "REJECTED_ONE_COMMON_FRAME_REQUIRED",
        "free_scale": "DIAGNOSTIC_COUNTERFACTUAL_NOT_AUTHORIZED_FRAME",
        "reflection": "DIAGNOSTIC_COUNTERFACTUAL_NOT_AUTHORIZED_FRAME",
        "single_node_authorization": "SINGLE_NODE_ALIGNMENT_DIAGNOSTIC",
    }
    return {"schema": "biospur.root_r4.dual_layer_negative_controls.v1", "controls": results,
            "production_builder_rejections": production_rejections,
            "all_required_detected": all(value.get("detected", True) for value in results.values())}


def dependency_graph_summary(data: C1Data) -> dict:
    examples = []
    for event in np.linspace(0, data.event_count - 1, 12, dtype=int):
        examples.append({
            "t4_event_id": data.t4_event_id(int(event)),
            "capture": 1, "tag_id": data.nodes[int(data.node_index[event])],
            "sweep": int(data.sweep[event]), "epoch": int(data.epoch[event]),
            "source_index": int(data.source_index[event]), "raw_record_index": int(data.raw_record_index[event]),
            "constituent_raw_event_ids": list(data.constituent_ids(int(event))),
        })
    return {
        "schema": "biospur.root_r4.raw_t4_dependency_graph.v1",
        "edge_definition": "raw link -> raw record/sweep -> canonical T4 preprocessing/solver -> T4 event",
        "exact_relation": "node_index+source_index selects one RAW_UWB_TAG_TRAJECTORIES row; T4 used_mask selects exact raw link IDs",
        "t4_events": data.event_count, "exact_lineage_events": data.event_count,
        "conservative_envelope_events": 0, "unresolved_events": 0,
        "examples": examples,
    }
