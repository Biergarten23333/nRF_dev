from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
from types import SimpleNamespace
import time

import numpy as np
import pytest

from tools import diagnose_c2_gap_endpoint_boundary_replay as module
import test_c2_continuous_group_epoch_owner as group_fixtures
import test_c2_full_session_ten_node_ab as session_fixtures


def _branch_state(*, pending=1, suppressed=4, revision=10, history=20,
                  publication=30, generation=40, late=0):
    return {
        "pending_buckets": pending,
        "pending_bucket_sizes": [3] * pending,
        "complete_pending_buckets": 0,
        "suppressed_counter": suppressed,
        "revision": revision,
        "history_revision": history,
        "root_publication_revision": publication,
        "root_publication_digest": "a" * 64,
        "hinge_continuity_generation": generation,
        "late_imu_rejected": late,
    }


def _repair_observer():
    before_a = _branch_state()
    before_b = _branch_state()
    after_a = _branch_state(
        pending=0, suppressed=5, revision=12, history=22,
        publication=31, generation=41,
    )
    after_b = dict(after_a)
    target_ns = module.TARGET_ENDPOINT_GLOBAL_NS
    history = {
        name: {
            "count": 10,
            "sample_indices": list(range(10)),
            "source_global_ns": [target_ns + 5_000_000 * index for index in range(10)],
            "frame_digests": [str(index) * 64 for index in range(10)],
        }
        for name in ("a", "b")
    }
    return SimpleNamespace(
        target_gap_calls=1,
        target_drift_gap_commits=1,
        target_before={
            "events": module.TARGET_EVENTS_BEFORE,
            "records": module.TARGET_RECORDS_BEFORE,
            "dropouts": 7,
            "frames": 100,
            "batched_records": 8,
            "batched_frames": 90,
            "scalar_frames": 10,
            "malformed_fallbacks": 2,
            "last_pelvis_timer_us": module.TARGET_PRIOR_TIMER_US,
            "last_pelvis_global_ns": module.TARGET_GAP_START_GLOBAL_NS,
            "a": before_a,
            "b": before_b,
            "drift": {
                "revision": 50,
                "pending_count": 1,
                "pending_package_digests": ["b" * 64],
                "derivative_history_empty": False,
                "cumulative_ledger_m": [1.0, 2.0, 3.0],
            },
            "gap_cleared_packages": 4,
            "gap_cleared_package_digests": ["c" * 64],
        },
        target_after={
            "endpoint_global_ns": target_ns,
            "endpoint_timer_us": module.TARGET_ENDPOINT_TIMER_US,
            "endpoint_availability_ns": module.TARGET_ENDPOINT_AVAILABILITY_NS,
            "endpoint_event_id": module.TARGET_ENDPOINT_EVENT_ID,
            "source_event_id": module.TARGET_ENDPOINT_EVENT_ID,
            "source_event_digest": module.TARGET_ENDPOINT_EVENT_DIGEST,
            "gap_start_global_ns": module.TARGET_GAP_START_GLOBAL_NS,
            "gap_end_global_ns": target_ns,
            "record_ordinal": module.TARGET_RECORDS_BEFORE,
            "event_count": 10,
            "events_before": module.TARGET_EVENTS_BEFORE,
            "events_after": module.TARGET_EVENTS_AFTER,
            "records_after": module.TARGET_RECORDS_BEFORE + 1,
            "dropouts_after": 8,
            "frames_after": 110,
            "batched_records_after": 9,
            "batched_frames_after": 99,
            "scalar_frames_after": 11,
            "malformed_fallbacks_after": 3,
            "a": after_a,
            "b": after_b,
            "drift": {
                "revision": 51,
                "pending_count": 0,
                "pending_package_digests": [],
                "derivative_history_empty": True,
                "cumulative_ledger_m": [1.0, 2.0, 3.0],
            },
            "gap_cleared_packages": 5,
            "gap_cleared_package_digests": ["b" * 64, "c" * 64],
            "history": history,
        },
        following={
            "record_ordinal": module.TARGET_RECORDS_BEFORE + 1,
            "events_before": module.TARGET_EVENTS_AFTER,
            "events_after": module.TARGET_EVENTS_AFTER + 16,
            "records_after": module.TARGET_RECORDS_BEFORE + 2,
            "native_frames": {"a": 16, "b": 16},
        },
    )


def test_exact_boundary_gate_contract_and_fail_closed_variants():
    observer = _repair_observer()
    assert all(module._target_repair_gates(observer).values())

    observer.following["native_frames"]["b"] = 0
    assert not module._target_repair_gates(observer)[
        "following_complete_native_record_committed"
    ]
    observer = _repair_observer()
    observer.target_after["history"]["a"]["source_global_ns"][1] = (
        module.TARGET_ENDPOINT_GLOBAL_NS
    )
    assert not module._target_repair_gates(observer)[
        "endpoint_once_plus_nine_frame_suffix"
    ]
    observer = _repair_observer()
    observer.target_before["a"]["pending_bucket_sizes"] = [10]
    observer.target_before["a"]["complete_pending_buckets"] = 1
    assert not module._target_repair_gates(observer)[
        "incomplete_groups_cleared_once_no_complete_suppressed"
    ]


def _target_frame(coordinator):
    prior = coordinator._a._composition.history.frames[-1]
    coordinator._last_pelvis_timer = prior.source_timer_us
    coordinator._last_pelvis_ns = prior.source_global_ns
    engine = coordinator._a._composition.engine
    frame = group_fixtures._owned_frame(
        engine, group_fixtures._engine_and_packet_for_frame(),
        prior.source_timer_us + 38_914,
        prior.publication_revision + 1,
    )
    return replace(
        frame,
        raw_provenance=group_fixtures.RawByteProvenance(
            *module.TARGET_RAW_IDENTITY, 0,
        ),
        digest="",
    )


def test_actual_continuous_event_hook_calls_original_once_and_is_observation_only():
    observed, _frames, _ticket = session_fixtures._real_record_coordinator(1)
    control, _frames2, _ticket2 = session_fixtures._real_record_coordinator(1)
    group_fixtures._enable_consensus_drift(observed._b)
    group_fixtures._enable_consensus_drift(control._b)
    observed_frame = _target_frame(observed)
    control_frame = _target_frame(control)

    owners = SimpleNamespace(
        coordinator=observed,
        reader=SimpleNamespace(inventory=SimpleNamespace(regions=())),
    )
    observer = module.BoundaryReplayObserver(owners, 0.0)
    observer._install_hooks()
    observer._target_active = True
    observer._active_raw_identity = module.TARGET_RAW_IDENTITY
    observer._active_event_digests = (module.TARGET_ENDPOINT_EVENT_DIGEST,)
    observed_gap = observed._gap_event(observed_frame)
    observed_endpoint = observed._frame_event(observed_frame)
    control._atomic_ab_gap_native200(
        control._gap_event(control_frame), control._frame_event(control_frame),
    )
    observer._observed_gap_endpoint(observed_gap, observed_endpoint)

    assert observer.target_gap_calls == 1
    assert observer.target_drift_gap_commits == 1
    assert observer.target_after["endpoint_event_id"] == observed_endpoint.event_id
    assert observer.target_after["source_event_id"] == module.TARGET_ENDPOINT_EVENT_ID
    assert observer.target_after["source_event_digest"] == module.TARGET_ENDPOINT_EVENT_DIGEST
    assert len(observer.target_after["endpoint_identity_digest"]) == 64
    assert session_fixtures._real_record_state(
        observed, include_routing=False,
    ) == session_fixtures._real_record_state(control, include_routing=False)
    with pytest.raises(RuntimeError, match="more than once"):
        observer._observed_gap_endpoint(observed_gap, observed_endpoint)


def test_actual_hook_failure_does_not_record_success(monkeypatch):
    coordinator, _frames, _ticket = session_fixtures._real_record_coordinator(1)
    group_fixtures._enable_consensus_drift(coordinator._b)
    frame = _target_frame(coordinator)
    owners = SimpleNamespace(
        coordinator=coordinator,
        reader=SimpleNamespace(inventory=SimpleNamespace(regions=())),
    )
    observer = module.BoundaryReplayObserver(owners, 0.0)
    observer._target_active = True
    observer._active_raw_identity = module.TARGET_RAW_IDENTITY
    observer._active_event_digests = (module.TARGET_ENDPOINT_EVENT_DIGEST,)
    observer._original_gap_endpoint = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("injected target rollback")
    )
    with pytest.raises(RuntimeError, match="injected target rollback"):
        observer._observed_gap_endpoint(
            coordinator._gap_event(frame), coordinator._frame_event(frame),
        )
    assert observer.target_after is None


def _passing_result():
    return {
        "schema": module.SCHEMA,
        "status": module.STATUS_PASS,
        "authorization": "BOUNDARY_REPAIR_VERIFIED",
        "diagnostic_only": True,
        "product_ready": False,
        "scientific_pass": False,
        "failure": None,
        "stop_reason": "FIRST_COMPLETE_RECORD_AFTER_TARGET",
        "limits": module._limits(),
        "provenance": {
            "session_id": "FULL_SESSION_CONTINUOUS_00_TO_19",
            "frozen_files": module.FROZEN_FILES,
        },
        "boundary": {
            "target_raw_identity": list(module.TARGET_RAW_IDENTITY),
            "events": module.TARGET_EVENTS_AFTER + 16,
        },
        "gates": {"all": True},
    }


@pytest.mark.parametrize("mutation", [
    lambda row: row["limits"].update({"retry": True}),
    lambda row: row.update({"product_ready": True}),
    lambda row: row["boundary"].update({"events": module.EVENT_CAP + 1}),
    lambda row: row["gates"].update({"all": False}),
    lambda row: row.update({"stop_reason": "INTERNAL_1800S_DEADLINE"}),
])
def test_result_validator_fails_closed(mutation):
    row = _passing_result()
    mutation(row)
    with pytest.raises(ValueError):
        module.validate_result(row)


def test_result_validator_accepts_only_preregistered_boundary():
    module.validate_result(_passing_result())


def test_frozen_hashes_and_wrapper_limits_are_literal():
    root = Path(__file__).resolve().parents[1]
    for relative, expected in module.FROZEN_FILES.items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected
    wrapper = (root / "tools/run_c2_gap_endpoint_boundary_replay_bounded.sh").read_text()
    assert "ulimit -v 1048576" in wrapper
    assert "--kill-after=5s 1860" in wrapper
    assert "bytes <= 5242880" in wrapper
    assert "OPENBLAS_NUM_THREADS=1" in wrapper
    assert "retry" not in wrapper.lower()


def test_module_contains_no_raw_path_or_resume_mechanism():
    source = Path(module.__file__).read_text()
    assert "/mnt/" not in source
    assert "resume" not in source.lower() or '"resume_capable": False' in source
    assert "TARGET_EVENTS_BEFORE = 343_176" in source
    assert "TARGET_RECORDS_BEFORE = 46_673" in source


def _bare_observer(*, events=module.TARGET_EVENTS_AFTER):
    coordinator = SimpleNamespace(_events=events)
    observer = object.__new__(module.BoundaryReplayObserver)
    observer.started = time.monotonic()
    observer.owners = SimpleNamespace(coordinator=coordinator)
    observer.records = module.TARGET_RECORDS_BEFORE + 1
    observer.target_seen = True
    observer.target_before = None
    observer.target_after = {}
    observer.following = None
    observer.target_gap_calls = 0
    observer.target_drift_gap_commits = 0
    observer._target_active = False
    observer._active_raw_identity = None
    observer._active_event_digests = ()
    return observer, coordinator


def _ticket(*, ordinal=module.TARGET_RECORDS_BEFORE + 1, count=16):
    return SimpleNamespace(
        raw_identity=(9, 10, 11, "d" * 64),
        record_ordinal=ordinal,
        event_digests=("e" * 64,) * count,
    )


def test_stop_is_after_successor_consume_and_requires_native_record(monkeypatch):
    observer, coordinator = _bare_observer()
    sequence = []

    def consumed(self, ticket):
        sequence.append("consume-returned")
        self.records += 1
        coordinator._events += len(ticket.event_digests)

    monkeypatch.setattr(
        module.FullSessionDeliveryObserver, "__call__", consumed,
    )
    monkeypatch.setattr(module, "_matching_frames", lambda *_args: (SimpleNamespace(digest="f" * 64),))
    coordinator._a = coordinator._b = object()
    with pytest.raises(module._BoundaryReached):
        observer(_ticket())
    sequence.append("stopped")
    assert sequence == ["consume-returned", "stopped"]
    assert observer.following["record_ordinal"] == 46_674
    assert observer.following["native_frames"] == {"a": 1, "b": 1}

    observer, coordinator = _bare_observer()
    coordinator._a = coordinator._b = object()
    monkeypatch.setattr(module, "_matching_frames", lambda *_args: ())
    with pytest.raises(module._BoundaryReached):
        observer(_ticket())
    assert not module._target_repair_gates(observer)[
        "following_complete_native_record_committed"
    ]


def test_failure_cap_and_deadline_cannot_false_pass(monkeypatch):
    observer, coordinator = _bare_observer(events=module.EVENT_CAP - 1)
    called = False

    def consumed(_self, _ticket):
        nonlocal called
        called = True

    monkeypatch.setattr(module.FullSessionDeliveryObserver, "__call__", consumed)
    with pytest.raises(RuntimeError, match="hard cap"):
        observer(_ticket(count=2))
    assert called is False and observer.following is None

    observer, _coordinator = _bare_observer()
    observer.started = 0.0
    with pytest.raises(module._Deadline):
        observer(_ticket())
    assert observer.following is None

    observer, _coordinator = _bare_observer()
    observer.target_seen = False
    target = _ticket(ordinal=module.TARGET_RECORDS_BEFORE, count=10)
    target.raw_identity = module.TARGET_RAW_IDENTITY
    monkeypatch.setattr(
        module.FullSessionDeliveryObserver,
        "__call__",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("target rolled back")),
    )
    with pytest.raises(RuntimeError, match="target rolled back"):
        observer(target)
    assert observer.target_seen is False and observer.following is None
