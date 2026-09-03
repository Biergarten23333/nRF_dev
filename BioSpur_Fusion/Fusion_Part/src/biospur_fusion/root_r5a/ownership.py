"""Exact physical-event ownership ledger for separate raw and T4 solves."""
from __future__ import annotations

from pathlib import Path
import hashlib

import numpy as np

from biospur_fusion.root_r4.contracts import FactorLedger, LineageError
from biospur_fusion.root_r4.data import C1Data

from .data import SupportDefinition
from .provenance import sha256


def write_ownership_ledger(output: Path, data: C1Data, support: SupportDefinition) -> dict:
    used = ((data.t4_used_mask[:, None] >> data.anchor_id) & 1).astype(bool)
    raw_owner = np.where(data.raw_valid, 1, 0).astype(np.uint8)
    t4_replacement_owner = np.where(used, 2, 0).astype(np.uint8)
    path = output / "EVENT_OWNERSHIP_LEDGER.npz"
    arrays = dict(
        node_index=data.node_index, source_index=data.source_index,
        epoch=data.epoch, sweep=data.sweep, raw_record_index=data.raw_record_index,
        anchor_id=data.anchor_id, raw_valid=data.raw_valid, t4_used_mask=data.t4_used_mask,
        raw_representation_owner=raw_owner,
        t4_replacement_representation_owner=t4_replacement_owner,
        matched_support_event=support.matched_event_mask,
    )
    canonical = hashlib.sha256()
    for name, value in sorted(arrays.items()):
        array = np.ascontiguousarray(value); key = name.encode()
        canonical.update(len(key).to_bytes(4, "little")); canonical.update(key)
        canonical.update(str(array.dtype).encode()); canonical.update(np.asarray(array.shape, np.int64).tobytes()); canonical.update(array.tobytes())
    np.savez_compressed(path, **arrays)
    # Exercise the same production-grade collision ledger on deterministic real samples.
    checks = []
    for event in np.flatnonzero(support.matched_event_mask)[:500]:
        raw = FactorLedger()
        for slot in np.flatnonzero(data.raw_valid[event]):
            raw.add_raw_factor(data.raw_event_id(int(event), int(slot)), data.raw_event_id(int(event), int(slot)))
        t4 = FactorLedger(); t4.add_t4_factor(data.t4_event_id(int(event)), data.constituent_ids(int(event)))
        checks.append(raw.audit()["pass"] and t4.audit()["pass"])
    mutation_detected = False; message = None
    event = int(np.flatnonzero(support.matched_event_mask)[0]); constituents = data.constituent_ids(event)
    ledger = FactorLedger(); ledger.add_raw_factor("mutation", constituents[0])
    try:
        ledger.add_t4_factor(data.t4_event_id(event), constituents)
    except LineageError as error:
        mutation_detected = True; message = str(error)
    if not all(checks) or not mutation_detected:
        raise RuntimeError("same-event ownership qualification failed")
    return {
        "schema": "biospur.root_r5a.event_ownership_ledger.v1", "path": path.name,
        "bytes": path.stat().st_size, "sha256": sha256(path), "events": data.event_count,
        "canonical_array_payload_sha256": canonical.hexdigest(),
        "raw_valid_links": data.raw_event_count, "exact_t4_associations": data.event_count,
        "raw_owner_code": 1, "t4_replacement_owner_code": 2,
        "DIRECT_EVENT_DOUBLE_COUNT_PREVENTED": True,
        "CROSS_EPOCH_AND_COMMON_MODE_INDEPENDENCE_NOT_ESTABLISHED": True,
        "T4_RAW_RECORD_ASSOCIATION_CLOSED": True,
        "T4_FULL_FUNCTIONAL_DEPENDENCY_NOT_YET_PROVEN": True,
        "separate_solve_contract": "raw and T4 ownership arrays describe mutually exclusive separate representations; they are never summed",
        "sampled_factor_ledger_checks": len(checks), "duplicate_mutation_detected": mutation_detected,
        "duplicate_mutation_message": message,
    }
