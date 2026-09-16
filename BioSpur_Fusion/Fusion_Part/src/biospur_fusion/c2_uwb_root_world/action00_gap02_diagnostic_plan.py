"""No-raw contract for the diagnostic Capture2 Action00--gap--Action02 A/B.

This module deliberately stops at source/event ownership.  It neither opens the
capture nor constructs a root estimator.  The eventual one-shot runner must
feed every original decoded IMU/UWB event through :class:`DiagnosticEventRouter`
and use the returned envelopes as its only sensor chronology.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from biospur_fusion.c2_coupled_progressive.action00_engineering_reader import (
    FORMAL_MANIFEST_SHA256,
    SOURCE_SHA256,
    SOURCE_STAT_IDENTITY,
)
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    ActionInterval,
    ContinuousClockOwner,
    ContinuousEvent,
    SourceBoundGap,
    continuous_clock_owner_digest,
    validate_event_clock,
)
from biospur_fusion.c2_coupled_progressive.continuous_stage2_adapter import (
    EventRegionOwner,
    adapt_verified_record,
)
from biospur_fusion.ingest.events import RawByteProvenance, RecordType, TypedEvent

from .continuous_full_session import ContinuousRegion, load_continuous_session_inventory


PLAN_SCHEMA = "biospur.c2.action00_gap_action02.root_diagnostic_plan.v1"
PLAN_ROLE = "ACTION00_REAL_INTERVAL_ACTION02_ROOT_DIAGNOSTIC"
EXPECTED_ACTIONS = ("00_initial_still", "02_t_pose")
EXPECTED_GAP_ID = "GAP_00_initial_still_TO_02_t_pose"
MISSING_RESULT_SHA256 = (
    "ac08ae6d22d3be9ef6933450c07b6d7a13b65341735e535dcb95c1c564be61fc"
)
MISSING_POLICY_DIGEST = (
    "802a322874da1f4f0544f8b6931ad8b1dff4967523370866e6a868b07d464d12"
)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _continuous_event_digest(event: ContinuousEvent) -> str:
    record = event.payload_owner
    if type(record) is not TypedEvent or record.raw is None:
        raise TypeError("diagnostic routed event lacks exact TypedEvent/raw owner")
    raw = record.raw
    return _digest({
        "event": (event.event_id, event.kind, event.action_index, event.action_id,
                  event.region_id, event.common_global_ns, event.availability_global_ns,
                  event.node_id, event.boot_epoch, event.clock_domain,
                  event.clock_mapping_digest, event.clock_owner_sha256,
                  event.clock_source_sha256),
        "record": (record.node_id, record.boot_epoch, record.record_type.name,
                   record.sequence, record.node_timer_us, record.global_time_ns,
                   record.status.name, record.payload),
        "raw": (raw.record_index, raw.sample_index, raw.start_offset,
                raw.end_offset, raw.encoded_sha256),
    })


def _region_payload(row: ContinuousRegion) -> dict[str, object]:
    return {
        "ordinal": row.ordinal,
        "region_id": row.region_id,
        "kind": row.kind,
        "byte_interval": [row.start_offset, row.stop_offset],
        "common_time_interval_ns": [row.start_ns, row.stop_ns],
        "action_index": row.action_index,
        "action_id": row.action_id,
        "slice_sha256": row.expected_sha256,
    }


@dataclass(frozen=True)
class DiagnosticBranchContract:
    branch_a_translation: str = "PURE_IMU_INERTIAL_TRANSLATION"
    branch_b_translation: str = "IDENTICAL_IMU_INERTIAL_TRANSLATION_PLUS_UWB_POSITION_ONLY"
    bootstrap: str = "FIRST_ROBUST_TEN_NODE_ACTION00_BODY_CONSENSUS_NO_ROOT_PRIOR"
    orientation_history: str = "ONE_PERSISTENT_NATIVE200_VQF_PER_NODE_SHARED_BY_A_AND_B"
    pose_fk_history: str = "ONE_NATIVE200_POSE_FK_HISTORY_SHARED_BY_A_AND_B"
    gap_semantics: str = "IDENTICAL_A_B_PROCESS_EVERY_REAL_UNLABELLED_IMU_NO_HOLD_NO_INTERPOLATION"
    terminal_missing_semantics: str = "AUDIT_ONLY_NO_TRANSLATION_MODE_CHANGE"
    uwb_semantics: str = "B_POSITION_ONLY_VELOCITY_AND_IMU_BIAS_BYTE_INERT"
    root_jump_semantics: str = "TRANSACTIONAL_HUMAN_NON_TELEPORT_GATE_REQUIRED"

    def __post_init__(self) -> None:
        if tuple(vars(self).values()) != (
            "PURE_IMU_INERTIAL_TRANSLATION",
            "IDENTICAL_IMU_INERTIAL_TRANSLATION_PLUS_UWB_POSITION_ONLY",
            "FIRST_ROBUST_TEN_NODE_ACTION00_BODY_CONSENSUS_NO_ROOT_PRIOR",
            "ONE_PERSISTENT_NATIVE200_VQF_PER_NODE_SHARED_BY_A_AND_B",
            "ONE_NATIVE200_POSE_FK_HISTORY_SHARED_BY_A_AND_B",
            "IDENTICAL_A_B_PROCESS_EVERY_REAL_UNLABELLED_IMU_NO_HOLD_NO_INTERPOLATION",
            "AUDIT_ONLY_NO_TRANSLATION_MODE_CHANGE",
            "B_POSITION_ONLY_VELOCITY_AND_IMU_BIAS_BYTE_INERT",
            "TRANSACTIONAL_HUMAN_NON_TELEPORT_GATE_REQUIRED",
        ):
            raise ValueError("diagnostic A/B execution contract cannot be weakened")

    @property
    def product_ready(self) -> bool:
        return False

    @property
    def scientific_pass(self) -> bool:
        return False


@dataclass(frozen=True)
class Action00Gap02DiagnosticPlan:
    regions: tuple[ContinuousRegion, ContinuousRegion, ContinuousRegion]
    source_audit_path: str
    source_audit_sha256: str
    clock_owner_digest: str
    expected_nodes: tuple[str, ...]
    branch_contract: DiagnosticBranchContract
    digest: str = ""
    schema: str = PLAN_SCHEMA
    role: str = PLAN_ROLE

    def __post_init__(self) -> None:
        if self.schema != PLAN_SCHEMA or self.role != PLAN_ROLE:
            raise ValueError("diagnostic plan role/schema mismatch")
        if tuple(row.kind for row in self.regions) != (
            "ACTION", "INTER_ACTION_GAP", "ACTION",
        ):
            raise ValueError("diagnostic plan must contain Action00, real interval, Action02")
        if tuple(row.region_id for row in self.regions) != (
            EXPECTED_ACTIONS[0], EXPECTED_GAP_ID, EXPECTED_ACTIONS[1],
        ):
            raise ValueError("diagnostic plan region identity mismatch")
        if tuple(row.action_id for row in (self.regions[0], self.regions[2])) != EXPECTED_ACTIONS:
            raise ValueError("diagnostic plan action identity mismatch")
        for left, right in zip(self.regions, self.regions[1:]):
            if left.stop_offset != right.start_offset or left.stop_ns != right.start_ns:
                raise ValueError("diagnostic regions are not one contiguous source chronology")
        if len(self.expected_nodes) != 10 or tuple(sorted(set(self.expected_nodes))) != self.expected_nodes:
            raise ValueError("diagnostic plan requires the exact ten-node inventory")
        if len(self.clock_owner_digest) != 64 or len(self.source_audit_sha256) != 64:
            raise ValueError("diagnostic plan owner digest is invalid")
        payload = {
            "schema": self.schema,
            "role": self.role,
            "regions": [_region_payload(row) for row in self.regions],
            "source": {
                "container_sha256": SOURCE_SHA256,
                "formal_manifest_sha256": FORMAL_MANIFEST_SHA256,
                "stat_identity": list(SOURCE_STAT_IDENTITY),
                "audit_path": self.source_audit_path,
                "audit_sha256": self.source_audit_sha256,
            },
            "clock_owner_digest": self.clock_owner_digest,
            "expected_nodes": list(self.expected_nodes),
            "missing_initialization": {
                "result_sha256": MISSING_RESULT_SHA256,
                "policy_digest": MISSING_POLICY_DIGEST,
                "status": "MISSING",
            },
            "branch_contract": vars(self.branch_contract),
            "claims": {"product_ready": False, "scientific_pass": False},
        }
        actual = _digest(payload)
        if self.digest and self.digest != actual:
            raise ValueError("diagnostic plan digest mismatch")
        object.__setattr__(self, "digest", actual)

    @property
    def start_offset(self) -> int:
        return self.regions[0].start_offset

    @property
    def stop_offset(self) -> int:
        return self.regions[-1].stop_offset

    @property
    def product_ready(self) -> bool:
        return False

    @property
    def scientific_pass(self) -> bool:
        return False


def load_action00_gap02_diagnostic_plan(
    root: Path, clock_owner: ContinuousClockOwner,
) -> Action00Gap02DiagnosticPlan:
    """Build the fixed diagnostic plan from existing typed owners, without raw I/O."""
    if type(clock_owner) is not ContinuousClockOwner or len(clock_owner.bindings) != 10:
        raise TypeError("diagnostic plan requires the authoritative ten-node clock owner")
    inventory = load_continuous_session_inventory(Path(root).resolve())
    selected = tuple(row for row in inventory.regions if row.ordinal <= 2)
    if len(selected) != 3:
        raise ValueError("continuous inventory lacks the exact Action00/gap/Action02 prefix")
    return Action00Gap02DiagnosticPlan(
        regions=selected,  # type: ignore[arg-type]
        source_audit_path=str(inventory.source_audit.resolve()),
        source_audit_sha256=inventory.source_audit_sha256,
        clock_owner_digest=continuous_clock_owner_digest(clock_owner),
        expected_nodes=tuple(sorted(row.node_id for row in clock_owner.bindings)),
        branch_contract=DiagnosticBranchContract(),
    )


@dataclass(frozen=True)
class DiagnosticRouteAudit:
    event_count: int
    events_by_region: Mapping[str, int]
    imu_by_region_and_node: Mapping[str, Mapping[str, int]]
    exact_5ms_imu_edges: int
    imu_dropout_edges: int
    event_identity_sha256: str


class DiagnosticEventRouter:
    """Atomically map original records into the fixed three-region chronology."""

    def __init__(self, plan: Action00Gap02DiagnosticPlan, clock_owner: ContinuousClockOwner) -> None:
        if type(plan) is not Action00Gap02DiagnosticPlan:
            raise TypeError("event router requires the typed diagnostic plan")
        if continuous_clock_owner_digest(clock_owner) != plan.clock_owner_digest:
            raise ValueError("diagnostic event router clock-owner mismatch")
        self.plan = plan
        self.clock_owner = clock_owner
        self._event_ids: set[str] = set()
        self._event_digests: dict[str, str] = {}
        self._counts: Counter[str] = Counter()
        self._imu_counts: dict[str, Counter[str]] = {
            row.region_id: Counter() for row in plan.regions
        }
        self._last_imu_us: dict[str, int] = {}
        self._last_availability_ns: dict[str, int] = {}
        self._exact_5ms_edges = 0
        self._dropout_edges = 0
        self._identity = hashlib.sha256()

    def owner_bytes(self) -> bytes:
        """Canonical rollback fingerprint for the mutable routing watermarks."""
        return json.dumps({
            "event_ids": sorted(self._event_ids),
            "event_digests": dict(sorted(self._event_digests.items())),
            "counts": dict(sorted(self._counts.items())),
            "imu_counts": {
                key: dict(sorted(value.items()))
                for key, value in sorted(self._imu_counts.items())
            },
            "last_imu_us": dict(sorted(self._last_imu_us.items())),
            "last_availability_ns": dict(sorted(self._last_availability_ns.items())),
            "exact_5ms_edges": self._exact_5ms_edges,
            "dropout_edges": self._dropout_edges,
            "identity_sha256": self._identity.copy().hexdigest(),
        }, sort_keys=True, separators=(",", ":")).encode()

    @staticmethod
    def _owns_raw(row: ContinuousRegion, raw: RawByteProvenance) -> bool:
        return row.start_offset <= raw.start_offset < raw.end_offset <= row.stop_offset

    def _region_for(self, record: TypedEvent, common_ns: int) -> ContinuousRegion:
        if record.raw is None:
            raise ValueError("diagnostic event lacks original raw-byte identity")
        byte_matches = tuple(row for row in self.plan.regions if self._owns_raw(row, record.raw))
        time_matches = tuple(row for row in self.plan.regions if row.contains_ns(common_ns))
        if len(byte_matches) != 1 or len(time_matches) != 1 or byte_matches[0] != time_matches[0]:
            raise ValueError("diagnostic event byte/time region ownership mismatch")
        return byte_matches[0]

    def _route_owned(self, record: TypedEvent, *, availability_global_ns: int) -> ContinuousEvent:
        """Prepare fully, then commit router watermarks; rejection is byte-inert."""
        if type(record) is not TypedEvent or record.record_type not in (RecordType.IMU, RecordType.UWB):
            raise TypeError("diagnostic router accepts original IMU/UWB TypedEvent only")
        if type(record.sequence) is not int or not 0 <= record.sequence <= 0xFFFF:
            raise ValueError("diagnostic source sequence is not uint16")
        binding = self.clock_owner.binding_for(record.node_id)
        common_ns = binding.global_ns(record.node_timer_us)
        region = self._region_for(record, common_ns)
        if region.kind == "ACTION":
            owner = EventRegionOwner(action=ActionInterval(
                region.action_index, region.action_id, region.start_ns, region.stop_ns,
            ))
        else:
            owner = EventRegionOwner(gap=SourceBoundGap(
                region.region_id, region.start_ns, region.stop_ns,
                self.plan.regions[0].action_id, self.plan.regions[2].action_id,
                self.plan.source_audit_path, self.plan.regions[0].expected_sha256,
                self.plan.source_audit_path, self.plan.regions[2].expected_sha256,
            ))
        event = adapt_verified_record(
            record, availability_global_ns=availability_global_ns,
            region_owner=owner, clock_owner=self.clock_owner,
        )
        validate_event_clock(event, self.clock_owner)
        if event.event_id in self._event_ids:
            raise ValueError("diagnostic event identity replay")
        previous_availability = self._last_availability_ns.get(event.node_id)
        if previous_availability is not None and availability_global_ns < previous_availability:
            raise ValueError("diagnostic node availability chronology regressed")
        delta_us: int | None = None
        if event.kind == "IMU":
            previous = self._last_imu_us.get(event.node_id)
            if previous is not None:
                delta_us = event.imu_timer2.trigger_timer2_us - previous
                if delta_us <= 0:
                    raise ValueError("diagnostic IMU hardware chronology replayed or regressed")

        # Commit only after every owner and chronology check has passed.
        self._event_ids.add(event.event_id)
        self._event_digests[event.event_id] = _continuous_event_digest(event)
        self._last_availability_ns[event.node_id] = availability_global_ns
        self._counts[region.region_id] += 1
        if event.kind == "IMU":
            self._imu_counts[region.region_id][event.node_id] += 1
            self._last_imu_us[event.node_id] = event.imu_timer2.trigger_timer2_us
            if delta_us is not None:
                if delta_us == 5_000:
                    self._exact_5ms_edges += 1
                else:
                    self._dropout_edges += 1
        self._identity.update((event.event_id + "\n").encode("ascii"))
        return event

    def route_original_record(self, records: tuple[TypedEvent, ...]) -> tuple[ContinuousEvent, ...]:
        """Route one canonical decoded v47 record with source-derived availability.

        IMU batches become available at their final TIMER2 sample.  A UWB record
        becomes available at its owned frame TIMER2.  The method rejects mixed or
        partial raw-record identity before mutating any routing watermark.
        """
        if not records:
            raise ValueError("canonical decoded record is empty")
        if any(type(row) is not TypedEvent or row.raw is None for row in records):
            raise TypeError("canonical decoded record lacks TypedEvent provenance")
        identities = {
            (row.raw.record_index, row.raw.start_offset, row.raw.end_offset, row.raw.encoded_sha256)
            for row in records
        }
        if len(identities) != 1 or len({row.node_id for row in records}) != 1:
            raise ValueError("decoded record rows do not share exact source identity")
        kinds = {row.record_type for row in records}
        binding = self.clock_owner.binding_for(records[0].node_id)
        if kinds == {RecordType.IMU}:
            availability = binding.global_ns(max(int(row.node_timer_us) for row in records))
        elif kinds == {RecordType.UWB} and len(records) == 1:
            frame_us = records[0].payload.get("frame_us")
            if type(frame_us) is not int:
                raise ValueError("decoded UWB lacks source-owned frame availability")
            availability = binding.global_ns(frame_us)
        else:
            raise ValueError("canonical source record mixes measurement kinds")
        snapshot = (
            set(self._event_ids), dict(self._event_digests), Counter(self._counts),
            {key: Counter(value) for key, value in self._imu_counts.items()},
            dict(self._last_imu_us), dict(self._last_availability_ns),
            self._exact_5ms_edges, self._dropout_edges, self._identity.copy(),
        )
        produced: list[ContinuousEvent] = []
        try:
            for row in records:
                produced.append(self._route_owned(row, availability_global_ns=availability))
        except BaseException:
            (
                self._event_ids, self._event_digests, self._counts, self._imu_counts,
                self._last_imu_us, self._last_availability_ns,
                self._exact_5ms_edges, self._dropout_edges, self._identity,
            ) = snapshot
            raise
        return tuple(produced)

    def validate_owned_event(self, event: ContinuousEvent) -> None:
        """Prove an unchanged event was emitted by this exact router instance."""
        if type(event) is not ContinuousEvent:
            raise TypeError("diagnostic event must be a ContinuousEvent")
        expected = self._event_digests.get(event.event_id)
        if expected is None or expected != _continuous_event_digest(event):
            raise ValueError("event is foreign to or mutated after diagnostic routing")

    def finish(self) -> DiagnosticRouteAudit:
        expected = set(self.plan.expected_nodes)
        if any(set(self._imu_counts[row.region_id]) != expected for row in self.plan.regions):
            raise RuntimeError("diagnostic source chronology does not conserve ten-node IMU coverage")
        return DiagnosticRouteAudit(
            event_count=len(self._event_ids),
            events_by_region=MappingProxyType(dict(sorted(self._counts.items()))),
            imu_by_region_and_node=MappingProxyType({
                region: MappingProxyType(dict(sorted(counts.items())))
                for region, counts in sorted(self._imu_counts.items())
            }),
            exact_5ms_imu_edges=self._exact_5ms_edges,
            imu_dropout_edges=self._dropout_edges,
            event_identity_sha256=self._identity.hexdigest(),
        )
