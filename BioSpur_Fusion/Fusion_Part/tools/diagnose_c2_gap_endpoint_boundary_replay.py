#!/usr/bin/env python3
"""One-shot observer for the C2 GAP/right-endpoint repair boundary.

This diagnostic reuses the canonical full-session factory and the already
qualified Phase-C publication observer.  It does not alter fusion decisions.
It stops only after the target raw record and the immediately following
delivered raw record have both committed completely.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import resource
import signal
import time
from typing import Any, Mapping

import numpy as np

from biospur_fusion.c2_uwb_root_world.full_session_body_pose import SESSION_ID
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
    _gap_endpoint_identity_digest,
)
from tools.build_c2_full_session_ten_node_ab import (
    CONSENSUS_DRIFT_CONFIG_SOURCE,
    EXPECTED,
    ROOT,
    _jsonable,
    build,
)
from tools.diagnose_c2_full_session_continuous_delivery import (
    FullSessionDeliveryObserver,
    _branch_result,
    _source_hashes,
)
from tools.diagnose_c2_phase_c_prefix import (
    MAX_SPEED_MPS,
    RLIMIT_AS_BYTES,
    _sha,
)


SCHEMA = "biospur.c2.gap-endpoint-boundary-replay.v1"
STATUS_PASS = "GAP_ENDPOINT_BOUNDARY_REPLAY_PASS"
TARGET_RAW_IDENTITY = (
    1_224_192,
    223_666_569,
    223_666_747,
    "34e7e0d445e7c92126a8691dba433264aad610d3fb1be864097b3677a77b0d4f",
)
TARGET_ENDPOINT_GLOBAL_NS = 234_995_870_017_523
TARGET_GAP_START_GLOBAL_NS = 234_995_831_103_996
TARGET_ENDPOINT_TIMER_US = 4_295_003_789
TARGET_PRIOR_TIMER_US = 4_294_964_875
TARGET_ENDPOINT_AVAILABILITY_NS = 234_995_915_016_977
TARGET_ENDPOINT_EVENT_ID = (
    "v47:1224192:0:223666569:223666747:"
    "34e7e0d445e7c92126a8691dba433264aad610d3fb1be864097b3677a77b0d4f"
)
TARGET_ENDPOINT_EVENT_DIGEST = (
    "c0aa95587f4e103817f638a2cf911e2b1c09e714e6674e855405e16710d2063e"
)
TARGET_EVENTS_BEFORE = 343_176
TARGET_EVENTS_AFTER = 343_186
TARGET_RECORDS_BEFORE = 46_673
TARGET_RECORD_EVENT_COUNT = 10
EVENT_CAP = 350_000
INTERNAL_SECONDS = 1_800.0
OUTER_SECONDS = 1_860
MAX_OUTPUT_BYTES = 5 << 20
FROZEN_FILES = {
    "src/biospur_fusion/c2_coupled_progressive/continuous_group_epoch_owner.py":
        "34217c5b297004acfb9f6d3041f77b14d07463dce67876ee0270a337a9a04457",
    "src/biospur_fusion/c2_coupled_progressive/full_session_ten_node_ab.py":
        "667e13f87ed338f929c0394250594f0a9d640988290c3397b64e5ca08f6b6239",
    "tests/test_c2_continuous_group_epoch_owner.py":
        "20fccfb7d363e18d6378c3241a183f012edffc849cad73fbd0eeb36ea29215d0",
    "tests/test_c2_full_session_ten_node_ab.py":
        "efccfa67c40254a5cb41871234112aca4e87990df162cc4c75f4d86eb5831b3a",
}


class _BoundaryReached(RuntimeError):
    pass


class _Deadline(RuntimeError):
    pass


def _raw_identity(frame: object) -> tuple[int, int, int, str]:
    raw = frame.raw_provenance
    return (
        int(raw.record_index), int(raw.start_offset), int(raw.end_offset),
        str(raw.encoded_sha256),
    )


def _matching_frames(branch: object, identity: tuple[int, int, int, str]) -> tuple[object, ...]:
    return tuple(
        frame for frame in branch._composition.history.frames
        if _raw_identity(frame) == identity
    )


def _branch_gap_state(branch: object) -> dict[str, Any]:
    pending = dict(branch._pending)
    root_token = branch._composition.engine.root.publication_token()
    return {
        "pending_buckets": len(pending),
        "pending_bucket_sizes": [len(pending[key]) for key in sorted(pending)],
        "complete_pending_buckets": sum(len(rows) == 10 for rows in pending.values()),
        "suppressed_counter": int(branch._counters.get(
            "INCOMPLETE_GROUP_SUPPRESSED_AT_GAP", 0,
        )),
        "revision": int(branch._revision),
        "history_revision": int(branch._composition.history.revision),
        "root_publication_revision": int(root_token.revision),
        "root_publication_digest": str(root_token.digest),
        "hinge_continuity_generation": int(
            branch._composition.engine.pose.hinge_continuity_generation
        ),
        "late_imu_rejected": int(branch._composition.engine.root.late_imu_rejected),
    }


def _drift_state(owner: object) -> dict[str, Any]:
    state = owner.snapshot()._state
    corrector = state.corrector
    derivative_empty = (
        len(corrector._history) == 0
        and len(corrector._pending_design) == 0
        and len(corrector._pending_observed) == 0
        and len(corrector._pending_inverse_variance) == 0
        and corrector._last_update_s is None
        and np.array_equal(corrector._bias_position_jacobian_s2, np.zeros((3, 3)))
        and np.array_equal(corrector._bias_velocity_jacobian_s, np.zeros((3, 3)))
        and corrector._last_bias_rotation is None
        and corrector._last_bias_measurement_time_s is None
    )
    return {
        "revision": int(owner.revision),
        "pending_count": int(owner.pending_count),
        "pending_package_digests": [str(package.digest) for package in state.pending],
        "derivative_history_empty": bool(derivative_empty),
        "cumulative_ledger_m": np.asarray(
            state.cumulative_absolute_position_correction_m,
        ).tolist(),
    }


class BoundaryReplayObserver(FullSessionDeliveryObserver):
    """Observe one exact repaired GAP record and one committed successor."""

    def __init__(self, owners: object, started: float) -> None:
        super().__init__(owners, started)
        self.target_seen = False
        self.target_gap_calls = 0
        self.target_drift_gap_commits = 0
        self.target_before: dict[str, Any] | None = None
        self.target_after: dict[str, Any] | None = None
        self.following: dict[str, Any] | None = None
        self._target_active = False
        self._active_raw_identity: tuple[int, int, int, str] | None = None
        self._active_event_digests: tuple[str, ...] = ()
        coordinator = owners.coordinator
        self._original_gap_endpoint = coordinator._atomic_ab_gap_native200
        coordinator._atomic_ab_gap_native200 = self._observed_gap_endpoint

    def _observe_drift_commit(
        self, drift_owner: object, plan: object, *,
        before_pending: int, after_pending: int,
    ) -> None:
        if self._target_active and plan.result.kind == "GAP":
            self.target_drift_gap_commits += 1
        super()._observe_drift_commit(
            drift_owner, plan,
            before_pending=before_pending, after_pending=after_pending,
        )

    def _observed_gap_endpoint(
        self, gap: object, endpoint: object, **kwargs: object,
    ) -> None:
        frame = endpoint.payload_owner
        identity = _raw_identity(frame)
        if identity != TARGET_RAW_IDENTITY:
            self._original_gap_endpoint(gap, endpoint, **kwargs)
            return
        if self.target_gap_calls:
            raise RuntimeError("target GAP endpoint was attempted more than once")
        coordinator = self.owners.coordinator
        if self._active_raw_identity != TARGET_RAW_IDENTITY:
            raise RuntimeError("target GAP endpoint escaped its raw-record transaction")
        drift = coordinator._b._composition.consensus_drift
        if drift is None:
            raise RuntimeError("target GAP endpoint lacks B drift owner")
        self.target_before = {
            "events": int(coordinator._events),
            "records": int(self.records),
            "dropouts": int(coordinator._dropouts),
            "frames": int(coordinator._frames),
            "batched_records": int(coordinator._batched_records),
            "batched_frames": int(coordinator._batched_frames),
            "scalar_frames": int(coordinator._scalar_frames),
            "malformed_fallbacks": int(coordinator._scalar_fallbacks.get(
                "malformed_or_discontinuity", 0,
            )),
            "last_pelvis_timer_us": int(coordinator._last_pelvis_timer),
            "last_pelvis_global_ns": int(coordinator._last_pelvis_ns),
            "a": _branch_gap_state(coordinator._a),
            "b": _branch_gap_state(coordinator._b),
            "drift": _drift_state(drift),
            "gap_cleared_packages": int(self.gap_cleared_chain.count),
            "gap_cleared_package_digests": sorted(
                self.gap_cleared_package_digests
            ),
        }
        self.target_gap_calls += 1
        self._original_gap_endpoint(gap, endpoint, **kwargs)
        self.target_after = {
            "endpoint_global_ns": int(frame.source_global_ns),
            "endpoint_timer_us": int(frame.source_timer_us),
            "endpoint_availability_ns": round(
                float(frame.imu_sample.availability_time_s) * 1e9,
            ),
            "endpoint_event_id": str(endpoint.event_id),
            "endpoint_identity_digest": _gap_endpoint_identity_digest(endpoint),
            "source_event_id": (
                f"v47:{identity[0]}:{int(frame.raw_provenance.sample_index)}:"
                f"{identity[1]}:{identity[2]}:{identity[3]}"
            ),
            "source_event_digest": (
                None if not self._active_event_digests
                else self._active_event_digests[
                    int(frame.raw_provenance.sample_index)
                ]
            ),
            "endpoint_frame_digest": str(frame.digest),
            "gap_start_global_ns": int(gap.gap_start_global_ns),
            "gap_end_global_ns": int(gap.common_global_ns),
            "a": _branch_gap_state(coordinator._a),
            "b": _branch_gap_state(coordinator._b),
            "drift": _drift_state(drift),
            "gap_cleared_packages": int(self.gap_cleared_chain.count),
            "gap_cleared_package_digests": sorted(
                self.gap_cleared_package_digests
            ),
        }

    def _record_end(self, ticket: object, before_events: int) -> None:
        coordinator = self.owners.coordinator
        identity = tuple(ticket.raw_identity)
        if identity == TARGET_RAW_IDENTITY:
            if self.target_seen:
                raise RuntimeError("target raw record was delivered more than once")
            self.target_seen = True
            frames = {
                name: _matching_frames(branch, TARGET_RAW_IDENTITY)
                for name, branch in (("a", coordinator._a), ("b", coordinator._b))
            }
            assert self.target_after is not None
            self.target_after.update({
                "event_count": len(tuple(ticket.event_digests)),
                "record_ordinal": int(ticket.record_ordinal),
                "events_before": before_events,
                "events_after": int(coordinator._events),
                "records_after": int(self.records),
                "dropouts_after": int(coordinator._dropouts),
                "frames_after": int(coordinator._frames),
                "batched_records_after": int(coordinator._batched_records),
                "batched_frames_after": int(coordinator._batched_frames),
                "scalar_frames_after": int(coordinator._scalar_frames),
                "malformed_fallbacks_after": int(
                    coordinator._scalar_fallbacks.get(
                        "malformed_or_discontinuity", 0,
                    )
                ),
                "history": {
                    name: {
                        "count": len(rows),
                        "sample_indices": [
                            int(row.raw_provenance.sample_index) for row in rows
                        ],
                        "source_global_ns": [int(row.source_global_ns) for row in rows],
                        "frame_digests": [str(row.digest) for row in rows],
                    }
                    for name, rows in frames.items()
                },
            })
            return
        if self.target_seen and self.following is None:
            frames = {
                name: _matching_frames(branch, identity)
                for name, branch in (("a", coordinator._a), ("b", coordinator._b))
            }
            self.following = {
                "raw_identity": list(identity),
                "record_ordinal": int(ticket.record_ordinal),
                "event_count": len(tuple(ticket.event_digests)),
                "events_before": before_events,
                "events_after": int(coordinator._events),
                "records_after": int(self.records),
                "native_frames": {name: len(rows) for name, rows in frames.items()},
                "history_frame_digests": {
                    name: [str(row.digest) for row in rows]
                    for name, rows in frames.items()
                },
            }
            raise _BoundaryReached("FIRST_COMPLETE_RECORD_AFTER_TARGET")

    def __call__(self, ticket: object) -> None:
        if time.monotonic() - self.started >= INTERNAL_SECONDS:
            raise _Deadline("INTERNAL_1800S_DEADLINE")
        coordinator = self.owners.coordinator
        before_events = int(coordinator._events)
        count = len(tuple(ticket.event_digests))
        if before_events + count > EVENT_CAP:
            raise RuntimeError("boundary replay exceeded 350000-event hard cap")
        identity = tuple(ticket.raw_identity)
        self._active_raw_identity = identity
        self._active_event_digests = tuple(ticket.event_digests)
        self._target_active = identity == TARGET_RAW_IDENTITY
        try:
            super().__call__(ticket)
        finally:
            self._target_active = False
            self._active_raw_identity = None
            self._active_event_digests = ()
        self._record_end(ticket, before_events)


def _target_repair_gates(observer: BoundaryReplayObserver) -> dict[str, bool]:
    before = observer.target_before or {}
    after = observer.target_after or {}
    history = after.get("history", {})
    a_before, b_before = before.get("a", {}), before.get("b", {})
    a_after, b_after = after.get("a", {}), after.get("b", {})
    drift_before, drift_after = before.get("drift", {}), after.get("drift", {})
    return {
        "exact_target_record_boundary": (
            before.get("events") == TARGET_EVENTS_BEFORE
            and before.get("records") == TARGET_RECORDS_BEFORE
            and after.get("events_before") == TARGET_EVENTS_BEFORE
            and after.get("events_after") == TARGET_EVENTS_AFTER
            and after.get("records_after") == TARGET_RECORDS_BEFORE + 1
            and after.get("event_count") == TARGET_RECORD_EVENT_COUNT
            and after.get("record_ordinal") == TARGET_RECORDS_BEFORE
        ),
        "compound_ab_gap_endpoint_once": (
            observer.target_gap_calls == 1
            and after.get("endpoint_global_ns") == TARGET_ENDPOINT_GLOBAL_NS
            and after.get("endpoint_timer_us") == TARGET_ENDPOINT_TIMER_US
            and after.get("endpoint_availability_ns")
            == TARGET_ENDPOINT_AVAILABILITY_NS
            and after.get("source_event_id") == TARGET_ENDPOINT_EVENT_ID
            and after.get("source_event_digest") == TARGET_ENDPOINT_EVENT_DIGEST
            and after.get("gap_start_global_ns") == TARGET_GAP_START_GLOBAL_NS
            and after.get("gap_end_global_ns") == TARGET_ENDPOINT_GLOBAL_NS
            and before.get("last_pelvis_timer_us") == TARGET_PRIOR_TIMER_US
            and before.get("last_pelvis_global_ns") == TARGET_GAP_START_GLOBAL_NS
            and after.get("endpoint_timer_us")
            - before.get("last_pelvis_timer_us", 0) == 38_914
        ),
        "endpoint_once_plus_nine_frame_suffix": all(
            history.get(name, {}).get("count") == 10
            and history.get(name, {}).get("sample_indices") == list(range(10))
            and history.get(name, {}).get("source_global_ns", []).count(
                TARGET_ENDPOINT_GLOBAL_NS,
            ) == 1
            for name in ("a", "b")
        ),
        "target_suffix_rebatched": (
            after.get("dropouts_after") == before.get("dropouts", -1) + 1
            and after.get("frames_after") == before.get("frames", -1) + 10
            and after.get("scalar_frames_after") == before.get("scalar_frames", -1) + 1
            and after.get("batched_frames_after") == before.get("batched_frames", -1) + 9
            and after.get("batched_records_after") == before.get("batched_records", -1) + 1
            and after.get("malformed_fallbacks_after")
            == before.get("malformed_fallbacks", -1) + 1
        ),
        "late_imu_rejected_unchanged": all(
            after_branch.get("late_imu_rejected")
            == before_branch.get("late_imu_rejected")
            for before_branch, after_branch in (
                (a_before, a_after), (b_before, b_after),
            )
        ),
        "incomplete_groups_cleared_once_no_complete_suppressed": all(
            before_branch.get("complete_pending_buckets") == 0
            and all(
                0 <= int(size) < 10
                for size in before_branch.get("pending_bucket_sizes", ())
            )
            and after_branch.get("pending_buckets") == 0
            and after_branch.get("suppressed_counter")
            == before_branch.get("suppressed_counter", -1)
               + before_branch.get("pending_buckets", -2)
            for before_branch, after_branch in (
                (a_before, a_after), (b_before, b_after),
            )
        ),
        "b_drift_gap_clear_once": (
            observer.target_drift_gap_commits == 1
            and drift_after.get("revision") == drift_before.get("revision", -1) + 1
            and drift_after.get("pending_count") == 0
            and drift_after.get("derivative_history_empty") is True
            and drift_after.get("cumulative_ledger_m")
            == drift_before.get("cumulative_ledger_m")
            and after.get("gap_cleared_packages")
            == before.get("gap_cleared_packages", -1)
               + drift_before.get("pending_count", -2)
            and set(after.get("gap_cleared_package_digests", ()))
               - set(before.get("gap_cleared_package_digests", ()))
            == set(drift_before.get("pending_package_digests", ()))
        ),
        "one_group_history_root_transition_per_branch": all(
            after_branch.get("revision") == before_branch.get("revision", -1) + 2
            and after_branch.get("history_revision")
            == before_branch.get("history_revision", -1) + 2
            and after_branch.get("root_publication_revision")
            == before_branch.get("root_publication_revision", -1) + 1
            and after_branch.get("hinge_continuity_generation")
            == before_branch.get("hinge_continuity_generation", -1) + 1
            for before_branch, after_branch in (
                (a_before, a_after), (b_before, b_after),
            )
        ),
        "following_complete_native_record_committed": (
            observer.following is not None
            and observer.following.get("records_after") == TARGET_RECORDS_BEFORE + 2
            and observer.following.get("events_before") == TARGET_EVENTS_AFTER
            and observer.following.get("record_ordinal")
            == TARGET_RECORDS_BEFORE + 1
            and observer.following.get("events_after", EVENT_CAP + 1) <= EVENT_CAP
            and all(observer.following.get("native_frames", {}).get(name, 0) >= 1
                    for name in ("a", "b"))
        ),
    }


def _limits() -> dict[str, Any]:
    return {
        "event_cap": EVENT_CAP,
        "internal_seconds": INTERNAL_SECONDS,
        "outer_seconds": OUTER_SECONDS,
        "rlimit_as_bytes": RLIMIT_AS_BYTES,
        "threads": 1,
        "maximum_output_bytes": MAX_OUTPUT_BYTES,
        "retry": False,
        "resume_capable": False,
    }


def _failure_result(
    *, started: float, expected_self_sha256: str, error: BaseException,
    stop_reason: str, source_before: Mapping[str, str],
    source_after: Mapping[str, str], events: int, records: int,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "GAP_ENDPOINT_BOUNDARY_REPLAY_FAIL",
        "authorization": "STOP",
        "diagnostic_only": True,
        "product_ready": False,
        "scientific_pass": False,
        "failure": {"type": type(error).__name__, "message": str(error)},
        "stop_reason": stop_reason,
        "limits": _limits(),
        "provenance": {
            "session_id": SESSION_ID,
            "expected_self_sha256": expected_self_sha256,
            "observed_self_sha256": _sha(Path(__file__).resolve()),
            "frozen_files": FROZEN_FILES,
            "source_sha256_before": dict(source_before),
            "source_sha256_after": dict(source_after),
        },
        "boundary": {"events": events, "records": records},
        "gates": {
            "no_exception": False,
            "source_hashes_stable": bool(source_before) and source_before == source_after,
            "peak_rss_within_1gib": (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
                <= RLIMIT_AS_BYTES
            ),
            "result_payload_within_5mib": True,
        },
        "wall_s": time.monotonic() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def run(*, expected_self_sha256: str) -> dict[str, Any]:
    started = time.monotonic()
    source_before: dict[str, str] = {}
    source_after: dict[str, str] = {}
    owners = observer = None
    failure: BaseException | None = None
    stop_reason = "SOURCE_COMPLETED_BEFORE_BOUNDARY"
    previous = signal.getsignal(signal.SIGALRM)

    def deadline(*_args: object) -> None:
        raise _Deadline("INTERNAL_1800S_DEADLINE")

    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, INTERNAL_SECONDS)
    try:
        if _sha(Path(__file__).resolve()) != expected_self_sha256:
            raise RuntimeError("boundary harness SHA differs from wrapper binding")
        for relative, expected in FROZEN_FILES.items():
            if _sha(ROOT / relative) != expected:
                raise RuntimeError(f"frozen repair file changed: {relative}")
        source_before = _source_hashes()
        owners = build()
        observer = BoundaryReplayObserver(owners, started)
        owners.coordinator.consume_record_ticket = observer
        owners.coordinator.run(owners.reader)
    except _BoundaryReached as stop:
        stop_reason = str(stop)
    except BaseException as error:
        failure = error
        stop_reason = str(error) if isinstance(error, _Deadline) else "UNEXPECTED_EXCEPTION"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)
    try:
        source_after = _source_hashes()
    except BaseException as error:
        if failure is None:
            failure = error
            stop_reason = "SOURCE_HASH_FINALIZATION_FAILED"
    audit = None if owners is None else owners.coordinator.audit()
    events = 0 if audit is None else int(audit.events)
    records = 0 if observer is None else int(observer.records)
    if (
        failure is not None or owners is None or observer is None
        or owners.coordinator._a is None or owners.coordinator._b is None
        or not observer.hooks_installed or observer.following is None
    ):
        return _failure_result(
            started=started, expected_self_sha256=expected_self_sha256,
            error=failure or RuntimeError("boundary ended before complete successor"),
            stop_reason=stop_reason, source_before=source_before,
            source_after=source_after, events=events, records=records,
        )

    coordinator = owners.coordinator
    drift_owner = coordinator._b._composition.consensus_drift
    publication = coordinator.diagnostic_publication()
    repair_gates = _target_repair_gates(observer)
    a_total = sum(observer.a_uwb.values())
    b_total = sum(observer.b_uwb.values())
    pending = () if drift_owner is None else drift_owner.snapshot()._state.pending
    disposition = (
        observer.drift["consumed"] + observer.gap_cleared_chain.count + len(pending)
    )
    gates = {
        **repair_gates,
        "stopped_only_after_first_complete_successor": (
            stop_reason == "FIRST_COMPLETE_RECORD_AFTER_TARGET"
        ),
        "same_ab_time_and_pose_stream": (
            observer.same_root_time and observer.same_history_stream
        ),
        "all_root_states_valid": observer.all_states_valid,
        "b_inside_anchor_envelope_plus_0_75m": (
            observer.b_inside_volume and observer.envelope_matches_owner
        ),
        "b_speed_at_most_12mps": observer.b_speed_bounded,
        "uwb_position_only": observer.uwb_position_only,
        "drift_finite_velocity_only_bounded": (
            observer.drift_deltas_finite and observer.drift_velocity_only
            and math.isfinite(observer.drift_maximum_delta_norm_mps)
            and observer.drift_maximum_delta_norm_mps <= 0.50
        ),
        "drift_causal_exactly_once": (
            observer.drift_duplicate_or_replay == 0
            and observer.drift["consumed"]
            == observer.drift["accepted"] + observer.drift["rejected"]
        ),
        "ab_transactions_conserved_no_rollback": (
            observer.ab_transaction_chain.count == int(audit.ab_transaction_total)
            and a_total == int(audit.ab_transaction_total)
            and b_total == int(audit.ab_transaction_total)
            and observer.outer_rollback_count == 0
        ),
        "drift_queue_dispositions_conserved": (
            observer.drift["queued"] == disposition
            and len(observer.queued_package_digests) == observer.drift["queued"]
        ),
        "continuous_owner_not_reset": (
            drift_owner is not None
            and drift_owner is observer.initial_drift_owner
            and id(drift_owner) == observer.initial_drift_owner_token
            and observer.drift_owner_unchanged
            and observer.drift_revision_monotonic
        ),
        "no_exception_observer_failure_or_publication_rejection": (
            failure is None and not observer.observer_failures
        ),
        "source_hashes_stable": source_before == source_after,
        "peak_rss_within_1gib": (
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
            <= RLIMIT_AS_BYTES
        ),
        "result_payload_within_5mib": True,
    }
    passed = all(gates.values())
    config = None if drift_owner is None else asdict(drift_owner.config)
    return {
        "schema": SCHEMA,
        "status": STATUS_PASS if passed else "GAP_ENDPOINT_BOUNDARY_REPLAY_FAIL",
        "authorization": "BOUNDARY_REPAIR_VERIFIED" if passed else "STOP",
        "diagnostic_only": True,
        "product_ready": False,
        "scientific_pass": False,
        "failure": None,
        "stop_reason": stop_reason,
        "limits": _limits(),
        "provenance": {
            "session_id": SESSION_ID,
            "expected_self_sha256": expected_self_sha256,
            "observed_self_sha256": _sha(Path(__file__).resolve()),
            "config_source_sha256": EXPECTED[CONSENSUS_DRIFT_CONFIG_SOURCE],
            "config": config,
            "frozen_files": FROZEN_FILES,
            "source_sha256_before": source_before,
            "source_sha256_after": source_after,
        },
        "boundary": {
            "events": events,
            "records": records,
            "event_cap": EVENT_CAP,
            "target_raw_identity": list(TARGET_RAW_IDENTITY),
            "target": observer.target_after,
            "following_complete_record": observer.following,
            "event_chain_sha256": audit.event_chain_sha256,
        },
        "a": _branch_result(observer, "a", publication.a.root_state),
        "b": _branch_result(observer, "b", publication.b.root_state),
        "repair": {
            "target_gap_calls": observer.target_gap_calls,
            "target_b_drift_gap_commits": observer.target_drift_gap_commits,
            "before": observer.target_before,
            "after": observer.target_after,
        },
        "drift": {
            **dict(observer.drift),
            "pending_count": len(pending),
            "gap_cleared_chain": observer.gap_cleared_chain.result(),
            "maximum_velocity_delta_norm_mps": observer.drift_maximum_delta_norm_mps,
            "duplicate_or_replay_count": observer.drift_duplicate_or_replay,
        },
        "transactions": {
            "ab_total": int(audit.ab_transaction_total),
            "a_disposition_total": a_total,
            "b_disposition_total": b_total,
            "outer_rollback_count": observer.outer_rollback_count,
            "chain": observer.ab_transaction_chain.result(),
        },
        "performance": {
            "routing_calls": observer.routing_calls,
            "instrumented_consume_wall_s": observer.core_consume_wall_s,
            "observer_hook_wall_s": observer.hook_observer_wall_s,
            "consume_minus_hook_wall_s": observer.consume_minus_hook_wall_s,
        },
        "gates": gates,
        "wall_s": time.monotonic() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def validate_result(result: Mapping[str, Any]) -> None:
    if result.get("schema") != SCHEMA or result.get("status") != STATUS_PASS:
        raise ValueError("boundary replay did not pass")
    if result.get("authorization") != "BOUNDARY_REPAIR_VERIFIED":
        raise ValueError("boundary replay authorization mismatch")
    if result.get("diagnostic_only") is not True:
        raise ValueError("boundary replay must remain diagnostic-only")
    if result.get("product_ready") is not False or result.get("scientific_pass") is not False:
        raise ValueError("boundary replay must not promote product/science")
    if result.get("failure") is not None:
        raise ValueError("passing boundary replay contains failure")
    if result.get("stop_reason") != "FIRST_COMPLETE_RECORD_AFTER_TARGET":
        raise ValueError("boundary replay stop reason mismatch")
    if result.get("limits") != _limits():
        raise ValueError("boundary replay limits mismatch")
    provenance = result.get("provenance", {})
    if provenance.get("session_id") != "FULL_SESSION_CONTINUOUS_00_TO_19":
        raise ValueError("boundary replay session identity mismatch")
    if provenance.get("frozen_files") != FROZEN_FILES:
        raise ValueError("boundary replay frozen repair provenance mismatch")
    boundary = result.get("boundary", {})
    if tuple(boundary.get("target_raw_identity", ())) != TARGET_RAW_IDENTITY:
        raise ValueError("boundary replay target identity mismatch")
    if boundary.get("events", EVENT_CAP + 1) > EVENT_CAP:
        raise ValueError("boundary replay exceeded event cap")
    gates = result.get("gates", {})
    if not gates or not all(gates.values()):
        raise ValueError("boundary replay has a failed gate")


def _write_new(path: Path, value: Mapping[str, Any]) -> None:
    payload = (json.dumps(
        _jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ) + "\n").encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise RuntimeError("boundary replay evidence exceeds 5 MiB")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o444,
    )
    try:
        if os.write(descriptor, payload) != len(payload):
            raise RuntimeError("short boundary replay evidence write")
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
