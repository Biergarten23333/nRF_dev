"""Bounded diagnostic epoch assembly over exact routed UWB events."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType

import numpy as np

from biospur_fusion.c2_coupled_progressive.continuous_frontend import ContinuousEvent, ContinuousClockOwner, continuous_clock_owner_digest
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import canonical_epoch_bucket
from biospur_fusion.ingest.events import RecordType, TypedEvent

from .action00_gap02_diagnostic_plan import Action00Gap02DiagnosticPlan, DiagnosticEventRouter, EXPECTED_GAP_ID
from .continuous_root_ab import uwb_row_from_event
from .diagnostic_pelvis_orientation import DiagnosticPelvisGapOrientationOwner, DiagnosticPelvisOrientationFrame, PELVIS_NODE


def _epoch_digest(events, bucket, role, plan_digest, router_digest, gauge_digest):
    return hashlib.sha256(json.dumps({"schema":"biospur.c2.diagnostic_uwb_epoch.v1",
        "events":[event.event_id for event in events],"bucket":bucket,"role":role,
        "plan":plan_digest,"router":router_digest,"gauge":gauge_digest},
        sort_keys=True,separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class DiagnosticUwbEpoch:
    events: tuple[ContinuousEvent, ...]
    bucket: int
    role: str
    plan_digest: str
    router_digest: str
    gauge_digest: str
    digest: str

    def __post_init__(self):
        if self.role not in ("BOOTSTRAP","FLOW") or not self.events:
            raise ValueError("invalid diagnostic UWB epoch role/inventory")
        actual=_epoch_digest(self.events,self.bucket,self.role,self.plan_digest,self.router_digest,self.gauge_digest)
        if self.digest != actual: raise ValueError("diagnostic UWB epoch digest mismatch")


class DiagnosticUwbEpochAssembler:
    """Select the first eligible bootstrap group and bound later UWB flow."""
    maximum_pending_buckets = 3

    def __init__(self, *, plan: Action00Gap02DiagnosticPlan, clock_owner: ContinuousClockOwner,
                 router: DiagnosticEventRouter, pelvis_owner: DiagnosticPelvisGapOrientationOwner,
                 gauge: DiagnosticPelvisOrientationFrame):
        if type(plan) is not Action00Gap02DiagnosticPlan or type(router) is not DiagnosticEventRouter or router.plan is not plan:
            raise TypeError("assembler requires the exact diagnostic plan/router")
        if type(clock_owner) is not ContinuousClockOwner or router.clock_owner is not clock_owner or continuous_clock_owner_digest(clock_owner) != plan.clock_owner_digest:
            raise ValueError("assembler clock owner mismatch")
        if type(pelvis_owner) is not DiagnosticPelvisGapOrientationOwner or pelvis_owner.plan is not plan or pelvis_owner.clock_owner is not clock_owner:
            raise ValueError("assembler pelvis issuer mismatch")
        pelvis_owner.validate_owned_frame(gauge)
        if gauge.region_id != "00_initial_still":
            raise ValueError("assembler requires the owned Action00 pelvis gauge")
        self._plan=plan; self._clock=clock_owner; self._router=router; self._pelvis_owner=pelvis_owner; self._gauge=gauge
        self._pending={}; self._seen=set(); self._bootstrap=False
        self._finalized=set(); self._last_measurement={}; self._last_availability={}
        self._retired=0; self._emitted=0

    def owner_bytes(self):
        return json.dumps({"pending": {str(k): sorted(v) for k,v in sorted(self._pending.items())},
            "seen": sorted(self._seen), "bootstrap": self._bootstrap,
            "finalized": sorted((region,bucket) for region,bucket in self._finalized),
            "last_measurement": dict(sorted(self._last_measurement.items())),
            "last_availability": dict(sorted(self._last_availability.items())),
            "retired": self._retired, "emitted": self._emitted},
            sort_keys=True,separators=(",", ":")).encode()

    def ingest(self, event: ContinuousEvent) -> DiagnosticUwbEpoch | None:
        self._router.validate_owned_event(event)
        if event.kind != "UWB" or type(event.payload_owner) is not TypedEvent or event.payload_owner.record_type is not RecordType.UWB:
            raise TypeError("epoch assembler accepts routed UWB only")
        if event.event_id in self._seen: raise ValueError("diagnostic UWB replay")
        previous_availability=self._last_availability.get(event.node_id)
        if previous_availability is not None and event.availability_global_ns < previous_availability: raise ValueError("diagnostic UWB availability regressed")
        previous=self._last_measurement.get(event.node_id)
        if previous is not None and event.common_global_ns <= previous: raise ValueError("diagnostic UWB measurement replayed or regressed")
        row=uwb_row_from_event(event.payload_owner)
        valid=sum(bool(row.valid_mask & (1<<a)) and row.anchor_ids[a] == a
                  and 0 < row.ranges_mm[a] < 0xffff
                  and np.isfinite(row.t_round_us[a]) and row.t_round_us[a] >= 0
                  for a in range(8))
        if valid < 4: raise ValueError("diagnostic UWB row has fewer than four valid links")
        allowed=(event.action_id == "00_initial_still" or
                 (event.action_id == EXPECTED_GAP_ID and event.region_id == EXPECTED_GAP_ID and event.node_id == PELVIS_NODE) or
                 (event.action_id == "02_t_pose" and event.node_id == PELVIS_NODE))
        if not allowed: raise ValueError("diagnostic UWB route is outside Action00/pelvis gap/Action02")
        bucket=canonical_epoch_bucket(event.common_global_ns); region=event.region_id or event.action_id
        key=(region,bucket)
        if key in self._finalized:
            raise ValueError("diagnostic UWB bucket is already finalized")
        pending={key:dict(value) for key,value in self._pending.items()}
        retired=self._retired
        finalized=set(self._finalized)
        candidate_measurement=dict(self._last_measurement); candidate_measurement[event.node_id]=event.common_global_ns
        expected=set(self._plan.expected_nodes)
        for old_key in sorted(pending,key=lambda item:(next(i for i,r in enumerate(self._plan.regions) if r.region_id==item[0]),item[1])):
            old_region,old_bucket=old_key; missing=expected-set(pending[old_key])
            region_advanced=next(i for i,r in enumerate(self._plan.regions) if r.region_id==region) > next(i for i,r in enumerate(self._plan.regions) if r.region_id==old_region)
            nodes_advanced=old_region=="00_initial_still" and missing and all(
                node in candidate_measurement and canonical_epoch_bucket(candidate_measurement[node])>old_bucket for node in missing)
            if region_advanced or nodes_advanced:
                retired+=len(pending.pop(old_key)); finalized.add(old_key)
            else: break
        group=pending.setdefault(key,{})
        if group:
            first=next(iter(group.values()))
            if (first.action_id, first.region_id) != (event.action_id, event.region_id):
                raise ValueError("diagnostic UWB bucket mixes action/region owners")
        if event.node_id in group: raise ValueError("duplicate diagnostic node in epoch")
        if len(pending) > self.maximum_pending_buckets: raise OverflowError("diagnostic UWB pending capacity exceeded")
        group[event.node_id]=event
        self._pending=pending; self._retired=retired; self._finalized=finalized
        self._seen.add(event.event_id)
        self._last_measurement[event.node_id]=event.common_global_ns
        self._last_availability[event.node_id]=event.availability_global_ns
        complete=(event.action_id == "00_initial_still" and set(group)==expected)
        pelvis_complete=(event.action_id != "00_initial_still" and set(group)=={PELVIS_NODE})
        if not (complete or pelvis_complete): return None
        oldest=min(self._pending,key=lambda item:(next(i for i,r in enumerate(self._plan.regions) if r.region_id==item[0]),item[1]))
        if oldest != key: return None
        events=tuple(group[node] for node in sorted(group)); del self._pending[key]
        self._finalized.add(key)
        role="FLOW"
        if complete and not self._bootstrap:
            if min(x.common_global_ns for x in events) < round(self._gauge.availability_time_s*1e9) or min(x.availability_global_ns for x in events) < round(self._gauge.availability_time_s*1e9):
                self._retired += len(events); return None
            self._bootstrap=True; role="BOOTSTRAP"
        self._emitted += 1
        router_digest=hashlib.sha256(self._router.owner_bytes()).hexdigest()
        digest=_epoch_digest(events,bucket,role,self._plan.digest,router_digest,self._gauge.digest)
        return DiagnosticUwbEpoch(events,bucket,role,self._plan.digest,router_digest,self._gauge.digest,digest)
