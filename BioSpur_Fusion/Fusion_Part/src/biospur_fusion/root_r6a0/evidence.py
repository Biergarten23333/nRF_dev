"""Evidence ancestry firewall, including adapters around verified prior contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from biospur_fusion.ingest.events import TypedEvent
from biospur_fusion.root_r4.contracts import FactorLedger as RootR4FactorLedger

from .contracts import (
    ActivationState,
    EvidenceRecord,
    EvidenceRepresentation,
    FactorProposal,
    FaultDomain,
)


class EvidenceConflict(ValueError):
    """A raw physical event would influence more than one active likelihood."""


def typed_event_adapter(event: TypedEvent, *, sample_index: int = 0) -> EvidenceRecord:
    """Wrap the existing typed ingest without changing its clock semantics."""
    record_type = event.record_type.value
    if record_type not in ("IMU", "UWB"):
        raise ValueError("only measurement events can become Root-R6A0 evidence")
    representation = EvidenceRepresentation.RAW_IMU if record_type == "IMU" else EvidenceRepresentation.RAW_UWB
    physical_uid = f"{event.node_id}:boot={event.boot_epoch}:{record_type}:seq={event.sequence}:sample={sample_index}"
    if event.global_time_ns is not None:
        measurement_time_s = event.global_time_ns / 1e9
        covariance_provenance = f"common_clock_sigma_ns={event.global_time_sigma_ns}"
    else:
        measurement_time_s = float(event.node_timer_us) / 1e6
        covariance_provenance = "NODE_CLOCK_ONLY_GLOBAL_MAPPING_UNAVAILABLE"
    payload_ref = None
    if event.raw is not None:
        payload_ref = (
            f"sha256={event.raw.encoded_sha256}:record={event.raw.record_index}:"
            f"bytes={event.raw.start_offset}:{event.raw.end_offset}:sample={event.raw.sample_index}"
        )
    domains = (FaultDomain.SINGLE_EVENT, FaultDomain.IMU_NODE) if record_type == "IMU" else (
        FaultDomain.SINGLE_EVENT, FaultDomain.TAG_ANCHOR_LINK, FaultDomain.TAG,
        FaultDomain.ANCHOR, FaultDomain.CLOCK_TIMING,
    )
    return EvidenceRecord(
        event_uid=physical_uid,
        physical_event_uid=physical_uid,
        representation=representation,
        raw_ancestry=frozenset({physical_uid}),
        measurement_time_s=measurement_time_s,
        availability_time_s=event.master_arrival_ms / 1000.0,
        owner_id=event.node_id,
        covariance_provenance=covariance_provenance,
        fault_domains=domains,
        payload_ref=payload_ref,
    )


def derived_evidence(*, event_uid: str, physical_event_uid: str,
                     representation: EvidenceRepresentation, raw_ancestry: Iterable[str],
                     measurement_time_s: float, availability_time_s: float | None,
                     owner_id: str, covariance_provenance: str,
                     fault_domains: tuple[FaultDomain, ...], payload_ref: str | None = None) -> EvidenceRecord:
    if representation not in (EvidenceRepresentation.M1_DERIVED, EvidenceRepresentation.T4_DERIVED):
        raise ValueError("derived_evidence requires M1 or T4 representation")
    return EvidenceRecord(
        event_uid, physical_event_uid, representation, frozenset(raw_ancestry),
        measurement_time_s, availability_time_s, owner_id, covariance_provenance,
        fault_domains, payload_ref,
    )


@dataclass
class EvidenceLedger:
    """One ancestry owner per active likelihood, across both UWB and IMU layers."""

    records: dict[str, EvidenceRecord] = field(default_factory=dict)
    active_factor_by_raw_uid: dict[str, str] = field(default_factory=dict)
    factor_ancestry: dict[str, frozenset[str]] = field(default_factory=dict)

    def register(self, record: EvidenceRecord) -> None:
        if record.event_uid in self.records:
            raise EvidenceConflict(f"duplicate evidence UID {record.event_uid}")
        self.records[record.event_uid] = record

    def activate(self, proposal: FactorProposal, evidence_uids: Iterable[str]) -> None:
        if proposal.factor_id in self.factor_ancestry:
            raise EvidenceConflict(f"duplicate factor UID {proposal.factor_id}")
        if proposal.activation_state in (ActivationState.DISABLED, ActivationState.BLOCKED):
            self.factor_ancestry[proposal.factor_id] = frozenset()
            return
        records = []
        for uid in evidence_uids:
            if uid not in self.records:
                raise EvidenceConflict(f"unregistered evidence UID {uid}")
            records.append(self.records[uid])
        ancestry = frozenset().union(*(record.raw_ancestry for record in records)) if records else proposal.raw_ancestry
        if proposal.raw_ancestry and ancestry != proposal.raw_ancestry:
            raise EvidenceConflict("factor proposal ancestry differs from registered evidence ancestry")
        collisions = {uid: self.active_factor_by_raw_uid[uid] for uid in ancestry if uid in self.active_factor_by_raw_uid}
        if collisions:
            raise EvidenceConflict(f"DEPENDENT_EVIDENCE_DOUBLE_COUNT_REJECTED: {collisions}")
        self.factor_ancestry[proposal.factor_id] = ancestry
        for uid in ancestry:
            self.active_factor_by_raw_uid[uid] = proposal.factor_id

    def audit(self) -> dict:
        flattened = [uid for members in self.factor_ancestry.values() for uid in members]
        return {
            "registered_evidence": len(self.records),
            "active_factors": len(self.factor_ancestry),
            "claimed_raw_ancestry": len(self.active_factor_by_raw_uid),
            "duplicate_claim_count": len(flattened) - len(set(flattened)),
            "maximum_active_factors_per_raw_event": 1 if flattened else 0,
            "pass": len(flattened) == len(set(flattened)),
        }


@dataclass
class RootR4LineageAdapter:
    """Focused wrapper that re-verifies the mature raw/T4 ownership kernel."""

    ledger: RootR4FactorLedger = field(default_factory=RootR4FactorLedger)

    def add_raw(self, factor_id: str, raw_uid: str) -> None:
        self.ledger.add_raw_factor(factor_id, raw_uid)

    def add_t4(self, factor_id: str, raw_uids: Iterable[str]) -> None:
        self.ledger.add_t4_factor(factor_id, tuple(raw_uids))

    def audit(self) -> Mapping[str, object]:
        return self.ledger.audit()
