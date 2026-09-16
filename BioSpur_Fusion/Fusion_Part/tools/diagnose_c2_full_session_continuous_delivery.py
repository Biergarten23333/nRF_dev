#!/usr/bin/env python3
"""Bounded monolithic C2 00--19 continuous-session delivery gate."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, fields as dataclass_fields
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import signal
import time
from typing import Any, Mapping

import numpy as np

from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FULL_WINDOW_SHA256,
    HASH_EVIDENCE_SHA256,
    SOURCE_SHA256,
)
from biospur_fusion.c2_uwb_root_world.full_session_body_pose import SESSION_ID
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
    ContinuousAdmissionAudit,
)
from tools.build_c2_full_session_ten_node_ab import (
    CONSENSUS_DRIFT_CONFIG_SOURCE,
    EXPECTED,
    ROOT,
    _jsonable,
    build,
)
from tools.diagnose_c2_phase_c_prefix import (
    LOWER_M,
    MAX_SPEED_MPS,
    REPORT_ONLY_STEP_M,
    RLIMIT_AS_BYTES,
    UPPER_M,
    PhaseCPrefixObserver,
    _branch_result,
    _sha,
    _state_summary,
)


SCHEMA = "biospur.c2.full-session-continuous-00-19-delivery.v1"
STATUS_PASS = "FULL_SESSION_CONTINUOUS_00_TO_19_PASS"
EXPECTED_CONTAINER_RECORDS = 441_297
EXPECTED_DELIVERED_RECORDS = 374_309
EXPECTED_EVENTS = 2_752_100
EXPECTED_REGIONS = 37
EXPECTED_ROUTE_IDENTITY_SHA256 = (
    "c08e6c33840a7d48272879d49c546f20be716b002c7dc85b99e12632a6f49c3e"
)
EXPECTED_EVENTS_BY_REGION_SHA256 = (
    "5875e606980a6ec91f4deebc318338b19c9fc8e18ede86e10f32a52b24d73f03"
)
EXPECTED_INVENTORY_SHA256 = (
    "47a0566bc456b436ee378f06751a3bed9b649643cd27e779062c82b815846d51"
)
EXPECTED_OPTIMIZED_POSE_SOURCE_SHA256 = (
    "c5786753ee0428e18fb6d7b1377325f271e4229f0bf625a04dcc6200fc1b3ae1"
)
INTERNAL_SECONDS = 10_800.0
OUTER_SECONDS = 11_100
MAX_OUTPUT_BYTES = 16 << 20
CHECKPOINT_EVENT_THRESHOLDS = tuple(range(250_000, 2_750_001, 250_000)) + (
    EXPECTED_EVENTS,
)
ZERO_DIGEST = "0" * 64
ADMISSION_AUDIT_CHAIN_SCHEMA = (
    "biospur.c2.continuous-admission-audit-chain-fact.v1"
)
ADMISSION_AUDIT_FIELDS = (
    "bucket", "packet_digest", "epoch_digest", "candidate_digest",
    "source_sequence", "source_identity", "trusted_partition", "branch",
    "prepared_accepted", "prepared_reason", "commit_intent",
    "commit_attempted", "commit_succeeded", "outcome", "pre_pose_digest",
    "result_pose_digest", "diagnostic_digest", "diagnostic",
)


class _Deadline(RuntimeError):
    pass


def _canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(
        _jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _chain(previous: str, value: object) -> str:
    return hashlib.sha256(
        bytes.fromhex(previous) + bytes.fromhex(_canonical_digest(value))
    ).hexdigest()


def _admission_audit_digest(audit: ContinuousAdmissionAudit) -> str:
    if type(audit) is not ContinuousAdmissionAudit:
        raise TypeError("continuous admission audit owner type mismatch")
    actual_fields = tuple(field.name for field in dataclass_fields(type(audit)))
    if actual_fields != ADMISSION_AUDIT_FIELDS:
        raise RuntimeError("continuous admission audit schema changed")
    return _canonical_digest({
        "schema": ADMISSION_AUDIT_CHAIN_SCHEMA,
        "fields": {
            "bucket": audit.bucket,
            "packet_digest": audit.packet_digest,
            "epoch_digest": audit.epoch_digest,
            "candidate_digest": audit.candidate_digest,
            "source_sequence": audit.source_sequence,
            "source_identity": audit.source_identity,
            "trusted_partition": audit.trusted_partition,
            "branch": audit.branch,
            "prepared_accepted": audit.prepared_accepted,
            "prepared_reason": audit.prepared_reason,
            "commit_intent": audit.commit_intent,
            "commit_attempted": audit.commit_attempted,
            "commit_succeeded": audit.commit_succeeded,
            "outcome": audit.outcome,
            "pre_pose_digest": audit.pre_pose_digest,
            "result_pose_digest": audit.result_pose_digest,
            "diagnostic_digest": audit.diagnostic_digest,
            "diagnostic": audit.diagnostic,
        },
    })


class _CompactChain:
    def __init__(self) -> None:
        self.count = 0
        self.digest = ZERO_DIGEST
        self.first: object | None = None
        self.last: object | None = None

    def add(self, value: object) -> None:
        canonical = _jsonable(value)
        if self.first is None:
            self.first = canonical
        self.last = canonical
        self.digest = _chain(self.digest, canonical)
        self.count += 1

    def result(self) -> dict[str, object]:
        return {
            "count": self.count,
            "chain_sha256": self.digest,
            "first": self.first,
            "last": self.last,
        }


def _inventory_rows(inventory: object, hashes: tuple[str, ...]) -> list[dict[str, Any]]:
    regions = tuple(inventory.regions)
    if len(regions) != len(hashes):
        raise RuntimeError("full delivery inventory/hash cardinality mismatch")
    return [{
        "ordinal": int(region.ordinal),
        "region_id": str(region.region_id),
        "kind": str(region.kind),
        "action_id": None if region.action_id is None else str(region.action_id),
        "start_offset": int(region.start_offset),
        "stop_offset": int(region.stop_offset),
        "start_ns": int(region.start_ns),
        "stop_ns": int(region.stop_ns),
        "expected_sha256": region.expected_sha256,
        "observed_sha256": digest,
    } for region, digest in zip(regions, hashes)]


def _inventory_contract_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in (
        "ordinal", "region_id", "kind", "action_id", "start_offset",
        "stop_offset", "start_ns", "stop_ns", "expected_sha256",
    )} for row in rows]


def _native_frame_identity(frame: object) -> dict[str, Any]:
    raw = frame.raw_provenance
    return {
        "frame_digest": str(frame.digest),
        "node": str(frame.node),
        "boot_epoch": int(frame.boot_epoch),
        "source_timer_us": int(frame.source_timer_us),
        "source_global_ns": int(frame.source_global_ns),
        "publication_revision": int(frame.publication_revision),
        "source_frame": int(frame.source_frame),
        "pose_publication_digest": str(frame.pose_publication_digest),
        "raw_identity": [
            int(raw.record_index), int(raw.start_offset), int(raw.end_offset),
            str(raw.encoded_sha256), int(raw.sample_index),
        ],
    }


def _pending_summary(
    pending: tuple[object, ...], final_native_frame: object,
) -> dict[str, Any]:
    chain = _CompactChain()
    availability_ns_rows = []
    for package in pending:
        availability_ns = int(package.availability_time_ns)
        availability_ns_rows.append(availability_ns)
        chain.add({
            "digest": package.digest,
            "admission_digest": package.admission_digest,
            "root_plan_digest": package.root_plan_digest,
            "availability_time_ns": int(package.availability_time_ns),
            "trusted_nodes": tuple(package.trusted_nodes),
            "anchors_used": tuple(package.anchors_used),
        })
    final_native = _native_frame_identity(final_native_frame)
    final_native_time_ns = int(final_native["source_global_ns"])
    strictly_future = all(
        value > final_native_time_ns for value in availability_ns_rows
    )
    return {
        **chain.result(),
        "oldest_availability_time_ns": min(availability_ns_rows, default=None),
        "latest_availability_time_ns": max(availability_ns_rows, default=None),
        "authenticated_final_native_frame": final_native,
        "all_pending_strictly_future": strictly_future,
        "eligible_pending_count": sum(
            value <= final_native_time_ns for value in availability_ns_rows
        ),
    }


class FullSessionDeliveryObserver(PhaseCPrefixObserver):
    """Reuse Phase-C physical hooks with bounded full-session evidence."""

    def __init__(self, owners: object, started: float) -> None:
        super().__init__(owners, started)
        self.next_checkpoint_index = 0
        self.active_region_id: str | None = None
        self.region_metrics = {
            str(region.region_id): {
                "kind": str(region.kind),
                "action_id": (
                    None if region.action_id is None else str(region.action_id)
                ),
                "delivered_records": 0,
                "events": 0,
                "root_publications": {"a": 0, "b": 0},
                "maximum_speed_mps": {"a": 0.0, "b": 0.0},
                "minimum_position_m": {
                    "a": np.full(3, np.inf), "b": np.full(3, np.inf),
                },
                "maximum_position_m": {
                    "a": np.full(3, -np.inf), "b": np.full(3, -np.inf),
                },
                "record_chain": _CompactChain(),
            }
            for region in self.source_regions
        }
        self.drift_decision_chain = _CompactChain()
        self.pending_admission_chain = _CompactChain()
        self.next_native_chain = _CompactChain()
        self.gap_cleared_chain = _CompactChain()
        self.queued_package_digests: set[str] = set()
        self.gap_cleared_package_digests: set[str] = set()
        self.ab_transaction_chain = _CompactChain()
        self.outer_rollback_count = 0
        # The prefix observer's lists remain bounded at zero in this subclass.
        self.drift_decision_digests.clear()
        self.pending_admission_identities.clear()
        self.next_native_stream_identities.clear()

    def _capture_checkpoint(self, events: int) -> None:
        while (
            self.next_checkpoint_index < len(CHECKPOINT_EVENT_THRESHOLDS)
            and events >= CHECKPOINT_EVENT_THRESHOLDS[self.next_checkpoint_index]
        ):
            threshold = CHECKPOINT_EVENT_THRESHOLDS[self.next_checkpoint_index]
            coordinator = self.owners.coordinator
            row = {
                "schema": "C2_FULL_PROGRESS_METRICS_ONLY_NOT_RESUMABLE_V1",
                "resume_capable": False,
                "threshold_events": threshold,
                "observed_events": events,
                "delivered_records": self.records,
                "wall_s": time.monotonic() - self.started,
                "pending_drift": (
                    None if coordinator._b is None
                    else coordinator._b._composition.consensus_drift.pending_count
                ),
                "batched_records": int(coordinator._batched_records),
                "batched_frames": int(coordinator._batched_frames),
                "scalar_frames": int(coordinator._scalar_frames),
            }
            self.checkpoints.append(row)
            print(json.dumps(row, sort_keys=True, separators=(",", ":")), flush=True)
            self.next_checkpoint_index += 1

    def _capture_state(self, name: str, state: object, **kwargs: object) -> None:
        super()._capture_state(name, state, **kwargs)
        if self.active_region_id is None:
            return
        metrics = self.region_metrics[self.active_region_id]
        position = np.asarray(state.vector[:3], dtype=float)
        speed = float(np.linalg.norm(state.vector[3:6]))
        metrics["root_publications"][name] += 1
        metrics["maximum_speed_mps"][name] = max(
            metrics["maximum_speed_mps"][name], speed,
        )
        metrics["minimum_position_m"][name] = np.minimum(
            metrics["minimum_position_m"][name], position,
        )
        metrics["maximum_position_m"][name] = np.maximum(
            metrics["maximum_position_m"][name], position,
        )

    def _capture_pending_admission_identity(self, package: object) -> None:
        observation = package.observation
        self.queued_package_digests.add(str(package.digest))
        self.pending_admission_chain.add({
            "package_digest": package.digest,
            "admission_digest": package.admission_digest,
            "root_plan_digest": package.root_plan_digest,
            "packet_digest": package.packet_digest,
            "epoch_digest": package.epoch_digest,
            "measurement_time_s": float(observation.measurement_time_s),
            "availability_time_ns": int(package.availability_time_ns),
            "source_sequence": int(observation.source_sequence),
            "tag_id": str(observation.tag_id),
            "anchors_used": tuple(package.anchors_used),
            "trusted_nodes": tuple(package.trusted_nodes),
        })

    def _capture_next_native_stream_identity(self, prepared: object) -> None:
        before = len(self.next_native_stream_identities)
        super()._capture_next_native_stream_identity(prepared)
        for row in self.next_native_stream_identities[before:]:
            self.next_native_chain.add(row)
        del self.next_native_stream_identities[:]

    def _observe_drift_commit(
        self, drift_owner: object, plan: object, *,
        before_pending: int, after_pending: int,
    ) -> None:
        if plan.result.kind == "GAP":
            for package in plan._base_state.pending:
                self.gap_cleared_package_digests.add(str(package.digest))
                self.gap_cleared_chain.add({
                    "package_digest": package.digest,
                    "admission_digest": package.admission_digest,
                    "root_plan_digest": package.root_plan_digest,
                    "availability_time_ns": int(package.availability_time_ns),
                    "trusted_nodes": tuple(package.trusted_nodes),
                    "anchors_used": tuple(package.anchors_used),
                    "gap_plan_digest": plan.digest,
                })
        before = len(self.drift_decision_digests)
        super()._observe_drift_commit(
            drift_owner, plan,
            before_pending=before_pending, after_pending=after_pending,
        )
        for digest in self.drift_decision_digests[before:]:
            self.drift_decision_chain.add(digest)
        del self.drift_decision_digests[:]

    def _capture_transactions(self) -> None:
        coordinator = self.owners.coordinator
        total = int(coordinator._ab_transaction_total)
        journal = tuple(coordinator._ab_transaction_journal)
        start = total - len(journal)
        seen = self.ab_transaction_chain.count
        if seen < start or seen > total:
            raise RuntimeError("full delivery transaction observer lost journal")
        for pair in journal[seen - start:]:
            row = {
                "provenance_digest": pair.provenance_digest,
                "a_reason": pair.a.reason,
                "b_reason": pair.b.reason,
                "a_admission_digest": (
                    None if pair.a.admission is None
                    else _admission_audit_digest(pair.a.admission)
                ),
                "b_admission_digest": (
                    None if pair.b.admission is None
                    else _admission_audit_digest(pair.b.admission)
                ),
            }
            self.ab_transaction_chain.add(row)
            self.outer_rollback_count += int(
                pair.a.reason == "OUTER_RECORD_BATCH_ROLLED_BACK"
                or pair.b.reason == "OUTER_RECORD_BATCH_ROLLED_BACK"
            )
        super()._capture_transactions()

    def __call__(self, ticket: object) -> None:
        call_started = time.monotonic()
        if time.monotonic() - self.started >= INTERNAL_SECONDS:
            raise _Deadline("INTERNAL_10800S_DEADLINE")
        coordinator = self.owners.coordinator
        before = int(coordinator._events)
        count = len(tuple(ticket.event_digests))
        if before + count > EXPECTED_EVENTS:
            raise RuntimeError("full delivery event count exceeded exact EOF")
        source_region = self._source_region(ticket)
        region_id = str(source_region.region_id)
        self.active_region_id = region_id
        core_started = time.monotonic()
        self.routing_calls += 1
        try:
            self.consume(ticket)
        finally:
            core_elapsed = time.monotonic() - core_started
            self.core_consume_wall_s += core_elapsed
        self.records += 1
        self.regions.add(region_id)
        if source_region.kind == "ACTION":
            self.actions.add(str(source_region.action_id))
        metrics = self.region_metrics[region_id]
        metrics["delivered_records"] += 1
        metrics["events"] += count
        metrics["record_chain"].add({
            "record_ordinal": int(ticket.record_ordinal),
            "raw_identity": tuple(ticket.raw_identity),
            "event_digests": tuple(ticket.event_digests),
        })
        self._install_hooks()
        self._capture_transactions()
        self._capture_roots()
        events = int(coordinator._events)
        self.observer_self_wall_s += time.monotonic() - call_started - core_elapsed
        tail = time.monotonic()
        self._capture_checkpoint(events)
        self.observer_self_wall_s += time.monotonic() - tail

    def region_result(self) -> dict[str, Any]:
        result = {}
        for region_id, metrics in self.region_metrics.items():
            def vector(name: str, branch: str) -> list[float] | None:
                value = metrics[name][branch]
                return None if not np.isfinite(value).all() else value.tolist()
            result[region_id] = {
                "verdict_scope": "REPORT_ONLY_NOT_INDEPENDENT",
                "kind": metrics["kind"],
                "action_id": metrics["action_id"],
                "delivered_records": metrics["delivered_records"],
                "events": metrics["events"],
                "root_publications": metrics["root_publications"],
                "maximum_speed_mps": metrics["maximum_speed_mps"],
                "minimum_position_m": {
                    branch: vector("minimum_position_m", branch)
                    for branch in ("a", "b")
                },
                "maximum_position_m": {
                    branch: vector("maximum_position_m", branch)
                    for branch in ("a", "b")
                },
                "record_chain": metrics["record_chain"].result(),
            }
        return result


def _source_paths() -> tuple[Path, ...]:
    return tuple(sorted((ROOT / "src/biospur_fusion").rglob("*.py"))) + (
        ROOT / "tools/build_c2_full_session_ten_node_ab.py",
        ROOT / "tools/diagnose_c2_phase_c_prefix.py",
        ROOT / "tools/diagnose_c2_full_session_continuous_delivery.py",
        CONSENSUS_DRIFT_CONFIG_SOURCE,
    )


def _source_hashes() -> dict[str, str]:
    return {str(path.relative_to(ROOT)): _sha(path) for path in _source_paths()}


def _failure_result(
    *, started: float, expected_self_sha256: str, reason: str,
    error: BaseException, source_before: Mapping[str, str],
    source_after: Mapping[str, str], events: int = 0, records: int = 0,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "FULL_SESSION_CONTINUOUS_00_TO_19_FAIL",
        "authorization": "STOP",
        "product_ready": False,
        "scientific_pass": False,
        "failure": {"type": type(error).__name__, "message": str(error)},
        "stop_reason": reason,
        "limits": {
            "internal_seconds": INTERNAL_SECONDS,
            "outer_seconds": OUTER_SECONDS,
            "rlimit_as_bytes": RLIMIT_AS_BYTES,
            "threads": 1,
            "maximum_output_bytes": MAX_OUTPUT_BYTES,
            "retry": False,
            "resume_capable": False,
        },
        "provenance": {
            "session_id": SESSION_ID,
            "expected_self_sha256": expected_self_sha256,
            "observed_self_sha256": _sha(Path(__file__).resolve()),
            "source_sha256_before": dict(source_before),
            "source_sha256_after": dict(source_after),
        },
        "boundary": {"events": events, "delivered_records": records},
        "gates": {
            "exact_eof_inventory": False,
            "no_exception": False,
            "source_hashes_stable": bool(source_before) and source_before == source_after,
            "peak_rss_within_1gib": (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
                <= RLIMIT_AS_BYTES
            ),
        },
        "wall_s": time.monotonic() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def run(*, expected_self_sha256: str) -> dict[str, Any]:
    started = time.monotonic()
    source_before: dict[str, str] = {}
    source_after: dict[str, str] = {}
    owners = observer = stream = None
    failure: BaseException | None = None
    stop_reason = "NOT_STARTED"
    previous = signal.getsignal(signal.SIGALRM)

    def deadline(*_args: object) -> None:
        raise _Deadline("INTERNAL_10800S_DEADLINE")

    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, INTERNAL_SECONDS)
    try:
        if _sha(Path(__file__).resolve()) != expected_self_sha256:
            raise RuntimeError("full delivery harness SHA differs from wrapper binding")
        source_before = _source_hashes()
        if source_before.get(
            "src/biospur_fusion/c2_uwb_calibration/causal_articulated_pose.py"
        ) != EXPECTED_OPTIMIZED_POSE_SOURCE_SHA256:
            raise RuntimeError("optimized pose source SHA is not preregistered")
        owners = build()
        observer = FullSessionDeliveryObserver(owners, started)
        owners.coordinator.consume_record_ticket = observer
        stream, _coordinator_audit = owners.coordinator.run(owners.reader)
        stop_reason = "EXACT_EOF"
    except BaseException as error:
        failure = error
        stop_reason = (
            str(error) if isinstance(error, _Deadline) else "UNEXPECTED_EXCEPTION"
        )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)
    try:
        source_after = _source_hashes()
    except BaseException as error:
        if failure is None:
            failure = error
            stop_reason = "SOURCE_HASH_FINALIZATION_FAILED"
    if (
        failure is not None or owners is None or observer is None or stream is None
        or owners.coordinator._a is None or owners.coordinator._b is None
        or not observer.hooks_installed
    ):
        audit = None if owners is None else owners.coordinator.audit()
        return _failure_result(
            started=started, expected_self_sha256=expected_self_sha256,
            reason=stop_reason,
            error=failure or RuntimeError("full delivery ended before bootstrap/EOF"),
            source_before=source_before, source_after=source_after,
            events=0 if audit is None else int(audit.events), records=(
                0 if observer is None else observer.records
            ),
        )

    audit = owners.coordinator.audit()
    publication = owners.coordinator.diagnostic_publication()
    drift_owner = owners.coordinator._b._composition.consensus_drift
    if drift_owner is None or drift_owner is not observer.initial_drift_owner:
        return _failure_result(
            started=started, expected_self_sha256=expected_self_sha256,
            reason="B_DRIFT_OWNER_MISSING_OR_REPLACED",
            error=RuntimeError("B drift owner missing/replaced at EOF"),
            source_before=source_before, source_after=source_after,
            events=int(audit.events), records=observer.records,
        )

    access = dict(stream.access_audit)
    route = stream.route_audit
    inventory = _inventory_rows(owners.reader.inventory, owners.reader._hashes)
    inventory_digest = _canonical_digest(_inventory_contract_rows(inventory))
    events_by_region_digest = _canonical_digest(dict(route.events_by_region))
    expected_region_ids = {row["region_id"] for row in inventory}
    expected_action_ids = {
        row["action_id"] for row in inventory if row["kind"] == "ACTION"
    }
    pending = tuple(drift_owner.snapshot()._state.pending)
    a_frames = owners.coordinator._a._composition.history.frames
    b_frames = owners.coordinator._b._composition.history.frames
    if not b_frames:
        return _failure_result(
            started=started, expected_self_sha256=expected_self_sha256,
            reason="NO_AUTHENTICATED_FINAL_NATIVE_FRAME",
            error=RuntimeError("B history has no final authenticated native frame"),
            source_before=source_before, source_after=source_after,
            events=int(audit.events), records=observer.records,
        )
    pending_result = _pending_summary(pending, b_frames[-1])
    anchors = np.asarray(
        owners.coordinator._b._composition.engine.static.anchors_m, dtype=float,
    )
    owner_lower_m = np.min(anchors, axis=0) - 0.75
    owner_upper_m = np.max(anchors, axis=0) + 0.75
    exact_eof = (
        stop_reason == "EXACT_EOF"
        and int(access.get("nonempty_records", -1)) == EXPECTED_CONTAINER_RECORDS
        and observer.records == EXPECTED_DELIVERED_RECORDS
        and int(access.get("decoded_events", -1)) == EXPECTED_EVENTS
        and int(access.get("routed_events", -1)) == EXPECTED_EVENTS
        and int(route.event_count) == EXPECTED_EVENTS
        and int(audit.events) == EXPECTED_EVENTS
        and len(inventory) == EXPECTED_REGIONS
    )
    config = drift_owner.config
    a_disposition_total = sum(observer.a_uwb.values())
    b_disposition_total = sum(observer.b_uwb.values())
    trusted_admission_total = sum(observer.trusted_partition_histogram.values())
    accounted_drift_packages = (
        observer.drift["consumed"] + observer.gap_cleared_chain.count
        + pending_result["count"]
    )
    pending_package_digests = {str(package.digest) for package in pending}
    disposition_package_digests = (
        observer.consumed_observations
        | observer.gap_cleared_package_digests
        | pending_package_digests
    )
    disjoint_drift_dispositions = (
        not (observer.consumed_observations
             & observer.gap_cleared_package_digests)
        and not (observer.consumed_observations & pending_package_digests)
        and not (observer.gap_cleared_package_digests & pending_package_digests)
    )
    gates = {
        "exact_eof_counts": exact_eof,
        "exact_source_window_and_route_chain": (
            access.get("window_sha256") == FULL_WINDOW_SHA256
            and access.get("hash_evidence_sha256") == HASH_EVIDENCE_SHA256
            and route.identity_sha256 == EXPECTED_ROUTE_IDENTITY_SHA256
            and events_by_region_digest == EXPECTED_EVENTS_BY_REGION_SHA256
            and inventory_digest == EXPECTED_INVENTORY_SHA256
            and set(route.events_by_region) == expected_region_ids
        ),
        "one_continuous_session_all_37_regions_no_action_reset": (
            set(observer.regions) == expected_region_ids
            and set(observer.actions) == expected_action_ids
            and SESSION_ID == "FULL_SESSION_CONTINUOUS_00_TO_19"
            and drift_owner.stream_owner_digest == observer.stream_owner_digest
            and drift_owner is observer.initial_drift_owner
            and id(drift_owner) == observer.initial_drift_owner_token
            and observer.drift_owner_unchanged
            and observer.drift_revision_monotonic
        ),
        "a_drift_absent": observer.a_drift_absent,
        "b_drift_present_only": observer.b_drift_present,
        "same_ab_event_time_and_pose_stream": (
            observer.same_root_time and observer.same_history_stream
            and publication.a.root_state.time_s == publication.b.root_state.time_s
            and tuple(frame.digest for frame in a_frames)
            == tuple(frame.digest for frame in b_frames)
        ),
        "all_root_states_valid": observer.all_states_valid,
        "b_inside_anchor_envelope_plus_0_75m": observer.b_inside_volume,
        "anchor_envelope_is_exact_owner_bounds_plus_0_75m": (
            observer.envelope_matches_owner
            and np.array_equal(LOWER_M, owner_lower_m)
            and np.array_equal(UPPER_M, owner_upper_m)
        ),
        "b_speed_at_most_12mps": observer.b_speed_bounded,
        "uwb_absolute_updates_position_only": observer.uwb_position_only,
        "drift_finite_velocity_only_at_most_0_50mps": (
            observer.drift_deltas_finite
            and observer.drift_velocity_only
            and math.isfinite(observer.drift_maximum_delta_norm_mps)
            and observer.drift_maximum_delta_norm_mps <= 0.50
        ),
        "at_least_one_accepted_drift_correction": (
            observer.drift["accepted"] >= 1
        ),
        "drift_causal_oldest_exactly_once": (
            observer.drift_duplicate_or_replay == 0
            and observer.drift["consumed"]
            == observer.drift["accepted"] + observer.drift["rejected"]
            and observer.drift_decision_chain.count == observer.drift["consumed"]
            and observer.pending_admission_chain.count == observer.drift["queued"]
            and observer.next_native_chain.count == observer.drift["consumed"]
        ),
        "ab_dispositions_and_transactions_conserved": (
            observer.ab_transaction_chain.count == int(audit.ab_transaction_total)
            and a_disposition_total == int(audit.ab_transaction_total)
            and b_disposition_total == int(audit.ab_transaction_total)
        ),
        "accepted_uwb_to_drift_handoff_conserved": (
            observer.b_uwb["accepted"] == trusted_admission_total
            and trusted_admission_total == observer.drift["queued"]
            and observer.drift["queued"] == observer.pending_admission_chain.count
            and observer.drift["queued"] == accounted_drift_packages
            and len(observer.queued_package_digests) == observer.drift["queued"]
            and len(observer.consumed_observations) == observer.drift["consumed"]
            and len(observer.gap_cleared_package_digests)
            == observer.gap_cleared_chain.count
            and len(pending_package_digests) == pending_result["count"]
            and disjoint_drift_dispositions
            and observer.queued_package_digests == disposition_package_digests
        ),
        "no_eligible_pending_at_eof": (
            pending_result["eligible_pending_count"] == 0
            and pending_result["all_pending_strictly_future"] is True
        ),
        "bias_disabled_and_unchanged": (
            config.acceleration_bias is None
            and observer.bias_unchanged_by_drift
        ),
        "trusted_partition_sizes_1_to_10": (
            bool(observer.trusted_partition_histogram)
            and all(1 <= int(value) <= 10
                    for value in observer.trusted_partition_histogram)
        ),
        "no_exception_observer_failure_or_rollback_leak": (
            failure is None and not observer.observer_failures
            and observer.outer_rollback_count == 0
        ),
        "source_hashes_stable": source_before == source_after,
        "peak_rss_within_1gib": (
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
            <= RLIMIT_AS_BYTES
        ),
        "result_payload_within_16mib": True,
    }
    passed = all(gates.values())
    return {
        "schema": SCHEMA,
        "status": STATUS_PASS if passed else "FULL_SESSION_CONTINUOUS_00_TO_19_FAIL",
        "authorization": "DELIVERY_COMPLETE" if passed else "STOP",
        "product_ready": False,
        "scientific_pass": False,
        "failure": None,
        "stop_reason": stop_reason,
        "limits": {
            "internal_seconds": INTERNAL_SECONDS,
            "outer_seconds": OUTER_SECONDS,
            "rlimit_as_bytes": RLIMIT_AS_BYTES,
            "threads": 1,
            "maximum_output_bytes": MAX_OUTPUT_BYTES,
            "retry": False,
            "resume_capable": False,
            "progress_checkpoints_are_metrics_only": True,
        },
        "provenance": {
            "session_id": SESSION_ID,
            "expected_self_sha256": expected_self_sha256,
            "observed_self_sha256": _sha(Path(__file__).resolve()),
            "optimized_pose_source_sha256": (
                source_before[
                    "src/biospur_fusion/c2_uwb_calibration/causal_articulated_pose.py"
                ]
            ),
            "source_sha256": SOURCE_SHA256,
            "full_window_sha256": FULL_WINDOW_SHA256,
            "hash_evidence_sha256": HASH_EVIDENCE_SHA256,
            "config_source_sha256": EXPECTED[CONSENSUS_DRIFT_CONFIG_SOURCE],
            "config": asdict(config),
            "stream_owner_digest": drift_owner.stream_owner_digest,
            "source_sha256_before": source_before,
            "source_sha256_after": source_after,
        },
        "boundary": {
            "container_records": int(access["nonempty_records"]),
            "delivered_records": observer.records,
            "events": int(audit.events),
            "regions": len(inventory),
            "region_inventory_sha256": inventory_digest,
            "events_by_region_sha256": events_by_region_digest,
            "reader_route_identity_sha256": route.identity_sha256,
            "coordinator_event_chain_sha256": audit.event_chain_sha256,
            "source_window_sha256": access["window_sha256"],
        },
        "inventory": inventory,
        "a": _branch_result(observer, "a", publication.a.root_state),
        "b": _branch_result(observer, "b", publication.b.root_state),
        "drift": {
            **dict(observer.drift),
            "maximum_velocity_delta_norm_mps": (
                observer.drift_maximum_delta_norm_mps
            ),
            "duplicate_or_replay_count": observer.drift_duplicate_or_replay,
            "decision_chain": observer.drift_decision_chain.result(),
            "admission_chain": observer.pending_admission_chain.result(),
            "native_consumption_chain": observer.next_native_chain.result(),
            "gap_cleared_chain": observer.gap_cleared_chain.result(),
            "pending_at_eof": pending_result,
            "bias_enabled": False,
        },
        "transactions": {
            "admission_audit_chain_schema": ADMISSION_AUDIT_CHAIN_SCHEMA,
            "ab_total": int(audit.ab_transaction_total),
            "a_disposition_total": a_disposition_total,
            "b_disposition_total": b_disposition_total,
            "b_accepted_uwb": int(observer.b_uwb["accepted"]),
            "trusted_admission_total": trusted_admission_total,
            "drift_packages_accounted": accounted_drift_packages,
            "outer_rollback_count": observer.outer_rollback_count,
            "chain": observer.ab_transaction_chain.result(),
        },
        "state_validity": {
            "contract": "RootState finite + allclose transpose atol=1e-10 + Cholesky",
            "first_failure": observer.first_state_failure,
        },
        "physical_contract": {
            "anchor_envelope_margin_m": 0.75,
            "owner_derived_lower_m": owner_lower_m.tolist(),
            "owner_derived_upper_m": owner_upper_m.tolist(),
            "preregistered_lower_m": LOWER_M.tolist(),
            "preregistered_upper_m": UPPER_M.tolist(),
            "maximum_root_speed_mps": MAX_SPEED_MPS,
        },
        "trusted_partition_size_histogram": dict(
            observer.trusted_partition_histogram
        ),
        "per_region_report_only": observer.region_result(),
        "progress_checkpoints": observer.checkpoints,
        "performance": {
            "instrumented_routing_and_consume_wall_s": observer.core_consume_wall_s,
            "observer_hook_wall_s": observer.hook_observer_wall_s,
            "consume_minus_observer_hook_wall_s": observer.consume_minus_hook_wall_s,
            "observer_self_wall_s": observer.observer_self_wall_s,
            "routing_calls": observer.routing_calls,
            "diagnostic_routing_metrics": _jsonable(
                audit.diagnostic_routing_metrics
            ),
        },
        "gates": gates,
        "report_only": {
            "per_region_metrics_are_independent_verdicts": False,
            "per_record_displacement_sentinel_m": REPORT_ONLY_STEP_M,
            "step_is_acceptance_gate": False,
        },
        "wall_s": time.monotonic() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def validate_result(result: Mapping[str, Any]) -> None:
    if result.get("schema") != SCHEMA:
        raise ValueError("full continuous delivery schema mismatch")
    if result.get("status") != STATUS_PASS:
        raise ValueError("full continuous 00-19 delivery did not pass")
    if result.get("authorization") != "DELIVERY_COMPLETE":
        raise ValueError("full continuous delivery lacks terminal authorization")
    if result.get("product_ready") is not False:
        raise ValueError("full delivery must not promote product readiness")
    if result.get("scientific_pass") is not False:
        raise ValueError("full delivery must not promote scientific validity")
    if result.get("failure") is not None:
        raise ValueError("passing full delivery contains a failure")
    if result.get("stop_reason") != "EXACT_EOF":
        raise ValueError("full delivery did not terminate at exact EOF")
    limits = result.get("limits", {})
    expected_limits = {
        "internal_seconds": INTERNAL_SECONDS,
        "outer_seconds": OUTER_SECONDS,
        "rlimit_as_bytes": RLIMIT_AS_BYTES,
        "threads": 1,
        "maximum_output_bytes": MAX_OUTPUT_BYTES,
        "retry": False,
        "resume_capable": False,
        "progress_checkpoints_are_metrics_only": True,
    }
    if dict(limits) != expected_limits:
        raise ValueError("full continuous delivery limits mismatch")
    boundary = result.get("boundary", {})
    expected = {
        "container_records": EXPECTED_CONTAINER_RECORDS,
        "delivered_records": EXPECTED_DELIVERED_RECORDS,
        "events": EXPECTED_EVENTS,
        "regions": EXPECTED_REGIONS,
    }
    if any(boundary.get(key) != value for key, value in expected.items()):
        raise ValueError("full continuous delivery boundary mismatch")
    if result.get("provenance", {}).get(
        "optimized_pose_source_sha256"
    ) != EXPECTED_OPTIMIZED_POSE_SOURCE_SHA256:
        raise ValueError("optimized production source provenance mismatch")
    if result.get("provenance", {}).get("session_id") != (
        "FULL_SESSION_CONTINUOUS_00_TO_19"
    ):
        raise ValueError("full continuous session identity mismatch")
    expected_report_only = {
        "per_region_metrics_are_independent_verdicts": False,
        "per_record_displacement_sentinel_m": REPORT_ONLY_STEP_M,
        "step_is_acceptance_gate": False,
    }
    if result.get("report_only") != expected_report_only:
        raise ValueError("full delivery report-only contract mismatch")
    per_region = result.get("per_region_report_only", {})
    if (
        len(per_region) != EXPECTED_REGIONS
        or any(row.get("verdict_scope") != "REPORT_ONLY_NOT_INDEPENDENT"
               for row in per_region.values())
    ):
        raise ValueError("per-region report-only scope mismatch")
    gates = result.get("gates", {})
    if not gates or not all(gates.values()):
        raise ValueError("full continuous delivery has a failed gate")


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    payload = (
        json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"),
                   allow_nan=False) + "\n"
    ).encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise RuntimeError("full continuous delivery evidence exceeds 16 MiB")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o444,
    )
    try:
        if os.write(descriptor, payload) != len(payload):
            raise RuntimeError("short full continuous delivery write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-self-sha256", required=True)
    args = parser.parse_args()
    result = run(expected_self_sha256=args.expected_self_sha256)
    _write_new(args.output, result)
    return 0 if result["status"] == STATUS_PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
