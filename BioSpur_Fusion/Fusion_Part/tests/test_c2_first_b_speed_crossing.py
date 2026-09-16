from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace
import time

import numpy as np
import pytest

from biospur_fusion.root_r3 import PositionObservation, RootState
from biospur_fusion.c2_uwb_root_world.continuous_consensus_drift import (
    ContinuousConsensusObservation,
    _package_digest,
)
from tools import diagnose_c2_first_b_speed_crossing as module
from tools import diagnose_c2_full_session_first_divergence as base
import test_c2_continuous_group_epoch_owner as group_fixtures
import test_c2_full_session_ten_node_ab as session_fixtures
from test_c2_full_session_first_divergence import _fixed_real_group_setup


def _readonly(values, shape):
    result = np.asarray(values, dtype=float).reshape(shape).copy()
    result.setflags(write=False)
    return result


def _package_document():
    observation = PositionObservation(
        1.0, 1.01, _readonly([1.0, 2.0, 0.8], (3,)),
        _readonly(np.eye(3) * 0.01, (3, 3)), "pelvis", tuple(range(8)),
        "NOMINAL", True, True, 7,
    )
    blank = ContinuousConsensusObservation(
        observation, 1_010_000_000,
        ("n0", "n1", "n2", "n3"), tuple(range(8)),
        "1" * 64, "2" * 64, "3" * 64, "4" * 64,
        _readonly([1.0, 2.0, 0.8], (3,)),
        _readonly([0.01, -0.02, 0.0], (3,)),
        _readonly([0.1, -0.2, 0.0], (3,)), "",
    )
    package = replace(blank, digest=_package_digest(blank))
    return base._project(package)


def _state_document(*, velocity=(0.0, 0.0, 0.0), position=(0.0, 0.0, 0.0),
                    bias=(0.0, 0.0, 0.0), time_s=1.0, digest="5" * 64):
    vector = np.asarray((*position, *velocity, *bias), dtype=float)
    return base._state_doc(RootState(time_s, vector, np.eye(9)), 1, digest)


def _velocity_prepared(delta=(0.2, 0.0, 0.0)):
    before = _state_document(velocity=(1.0, 2.0, 3.0))
    after = _state_document(velocity=tuple(np.array([1.0, 2.0, 3.0]) + delta))
    return {"imu_candidate": before, "final_root_candidate": after,
            "drift_result": {"velocity_delta_mps": base._project(
                np.asarray(delta, dtype=float))}}


def test_projected_consensus_package_roundtrips_production_digest():
    document = _package_document()
    assert module._authenticated_package(document)
    document["trusted_nodes"][0] = "tampered"
    assert not module._authenticated_package(document)


def test_projected_array_rejects_byte_and_shape_tampering():
    document = base._project(np.array([1.0, 2.0, 3.0]))
    assert np.array_equal(module._projected_array(document, (3,)), [1., 2., 3.])
    document["values"][1] = 9.0
    with pytest.raises(ValueError, match="byte digest"):
        module._projected_array(document, (3,))


def test_velocity_substep_is_bounded_and_nonvelocity_byte_inert():
    valid, delta = module._velocity_only_effect(_velocity_prepared())
    assert valid
    assert delta == [0.19999999999999996, 0.0, 0.0]

    position_tamper = _velocity_prepared()
    position_tamper["final_root_candidate"]["vector"][0] = 0.01
    assert not module._velocity_only_effect(position_tamper)[0]

    assert not module._velocity_only_effect(_velocity_prepared((0.5000001, 0., 0.)))[0]


def test_package_availability_must_match_observation_exactly():
    document = _package_document()
    document["availability_time_ns"] += 1
    assert not module._authenticated_package(document)


def test_real_owner_batch_binds_all_a_frames_including_nonzero_index():
    coordinator, frames, ticket = session_fixtures._real_record_coordinator(4)
    coordinator._initializer.preworld_pose_omissions = 0
    group_fixtures._enable_consensus_drift(coordinator._b)
    raw = ticket.raw_identity
    owners = SimpleNamespace(
        coordinator=coordinator,
        reader=SimpleNamespace(inventory=SimpleNamespace(regions=(SimpleNamespace(
            start_offset=raw[1] - 1, stop_offset=raw[2] + 1,
            region_id="00_initial_still"),))),
    )
    observer = module.FirstBSpeedCrossingObserver(owners, time.monotonic())
    observer.consume = coordinator.consume_record_ticket
    observer._install_hooks()
    observer(ticket)

    rows = sorted(observer._a_publications_by_frame.values(),
                  key=lambda item: item["sequential_frame_index"])
    assert [row["sequential_frame_index"] for row in rows] == [0, 1, 2, 3]
    assert [row["native_identity"]["digest"] for row in rows] == [
        frame.digest for frame in frames]
    assert round(rows[2]["publication"]["time_s"] * 1e9) == frames[2].source_global_ns


def test_batch_cannot_consume_pending_consensus_observation():
    coordinator, frames, _ticket = session_fixtures._real_record_coordinator(4)
    drift = group_fixtures._enable_consensus_drift(coordinator._b)
    composition = coordinator._b._composition
    package = _package_document()
    # The production classifier is the hard boundary: a pending drift package
    # routes the record to scalar publication, never batch consumption.
    candidate = replace(drift._state, pending=(SimpleNamespace(
        availability_time_ns=package["availability_time_ns"],),))
    drift._state = candidate
    assert not composition.native200_batch_boundary_free()
    events = tuple(group_fixtures._next_native200_events(coordinator._b, 4))
    assert coordinator._b.native200_record_batch_classification(events) == "complete_pending"


def test_frame_key_binds_operation_raw_record_and_sample_index():
    frame = {"digest": "a" * 64, "source_global_ns": 10,
             "sample_index": 2, "raw_identity": [7, 8, 9, "b" * 64]}
    key = module.FirstBSpeedCrossingObserver._frame_key(3, frame)
    changed = dict(frame, sample_index=3)
    assert key != module.FirstBSpeedCrossingObserver._frame_key(3, changed)
    with pytest.raises(ValueError, match="raw identity"):
        module.FirstBSpeedCrossingObserver._frame_key(3, dict(frame, raw_identity=[]))


def _real_scalar_chain():
    coordinator, a_owner, _b_owner, drift, ordered, uwb_event = (
        _fixed_real_group_setup())
    for _ in range(13):
        coordinator._atomic_ab(
            group_fixtures._next_native200_events(a_owner, 1)[0], uwb=False)
    latest_ns = a_owner._composition.history.frames[-1].source_global_ns
    owners = SimpleNamespace(coordinator=coordinator,
        reader=SimpleNamespace(inventory=SimpleNamespace(regions=())))
    observer = module.FirstBSpeedCrossingObserver(owners, time.monotonic())
    observer._install_hooks()

    before_total = coordinator.audit().ab_transaction_total
    coordinator._atomic_ab(
        uwb_event(ordered[-1], latest_ns + 20_000_000), uwb=True)
    after = coordinator.audit()
    start = after.ab_transaction_total - len(after.ab_transaction_journal)
    transactions = after.ab_transaction_journal[before_total - start:]
    observer._record_ledger({"record_ordinal": 1,
        "record_ab_transactions": base._jsonable(transactions),
        "record_completed": True, "rollback_observed": False})
    assert drift.pending_count == 1

    consumed = None
    for offset in range(1, 9):
        coordinator._atomic_ab(
            group_fixtures._next_native200_events(a_owner, 1)[0], uwb=False)
        if drift.pending_count == 0:
            consumed = offset
            break
    assert consumed is not None
    observer._record_ledger({"record_ordinal": 2,
        "record_ab_transactions": [], "record_completed": True,
        "rollback_observed": False})
    return observer, consumed


def test_real_owner_scalar_consumption_is_first_eligible_and_velocity_only():
    observer, consumed_offset = _real_scalar_chain()
    chain = observer._derive_completed_chain()
    assert consumed_offset == 4
    assert chain is not None
    assert module._authenticated_package(chain["package"])
    effect = chain["consume_effect"]
    assert effect["operation"] == "commit_native200"
    assert effect["drift_velocity_only"]
    assert chain["first_eligible_native"]["previous_root_time_ns"] \
        < chain["first_eligible_native"]["availability_time_ns"] \
        <= chain["first_eligible_native"]["frame"]["source_global_ns"]


def test_first_eligible_proof_fails_closed_when_preceding_frame_is_eligible():
    observer, _ = _real_scalar_chain()
    records = deepcopy(observer.recent_causal_records)
    consume_entry = records[-1]
    consume_effect = next(effect for effect in consume_entry["b_effects"]
        if (effect.get("prepared", {}).get("drift_result") or {}).get(
            "consumed_observation_digest") is not None)
    package = next(iter(records[0]["b_effects"][1]["post_drift"]["pending"]))
    consume_effect["pre_root"]["time_s"] = (
        int(package["availability_time_ns"]) + 1) * 1e-9
    observer.recent_causal_records.clear()
    observer.recent_causal_records.extend(records)
    assert observer._derive_completed_chain() is None


def test_observer_discards_same_frame_evidence_when_record_fails():
    coordinator, _frames, ticket = session_fixtures._real_record_coordinator(3)
    coordinator._initializer.preworld_pose_omissions = 0
    group_fixtures._enable_consensus_drift(coordinator._b)
    raw = ticket.raw_identity
    owners = SimpleNamespace(coordinator=coordinator,
        reader=SimpleNamespace(inventory=SimpleNamespace(regions=(SimpleNamespace(
            start_offset=raw[1] - 1, stop_offset=raw[2] + 1,
            region_id="00_initial_still"),))))
    observer = module.FirstBSpeedCrossingObserver(owners, time.monotonic())
    observer._install_hooks()

    def consume_then_fail(value):
        coordinator.consume_record_ticket(value)
        raise RuntimeError("injected post-record observer failure")

    observer.consume = consume_then_fail
    with pytest.raises(RuntimeError, match="injected"):
        observer(ticket)
    assert observer._a_publications_by_frame == {}
    assert list(observer.recent_causal_records) == []


def _valid_result_document():
    observer, _ = _real_scalar_chain()
    chain = observer._derive_completed_chain()
    frame = deepcopy(chain["first_eligible_native"]["frame"])
    frame_ns = int(frame["source_global_ns"])
    pre = _state_document(velocity=(11.9, 0., 0.),
                          time_s=(frame_ns - 5_000_000) * 1e-9,
                          digest="6" * 64)
    post = _state_document(velocity=(12.1, 0., 0.),
                           time_s=frame_ns * 1e-9, digest="7" * 64)
    same_a = _state_document(velocity=(11.8, 0., 0.),
                             time_s=frame_ns * 1e-9, digest="8" * 64)
    record = {"record_ordinal": 3, "raw_identity": frame["raw_identity"],
        "event_digests": ["9" * 64], "record_completed": True,
        "rollback_observed": False,
        "pre_ab": {"a": pre, "b": pre},
        "post_ab": {"a": same_a, "b": post},
        "record_ab_transactions": []}
    operation = {"route": "SCALAR", "order": 5,
        "events": [{"event_id": "imu", "kind": "IMU", "node_id": "pelvis",
                    "common_global_ns": frame_ns,
                    "availability_global_ns": frame_ns + 100_000}]}
    crossing = {"pre_publication": pre, "post_publication": post,
        "post_policy": {"speed_mps": 12.1, "speed_limit_mps": 12.0,
                        "criterion": "VELOCITY_NORM_ONLY"},
        "causal_operation": operation, "triggering_native_identity": frame,
        "same_frame_a": {"operation_order": 5,
            "sequential_frame_index": None, "native_identity": deepcopy(frame),
            "publication": same_a},
        "velocity_delta_mps": [0.1999999999999993, 0., 0.],
        "record_transaction": record,
        "causal_ledger_digest": chain["digest"]}
    limits = {"event_cap": module.EVENT_CAP,
        "internal_seconds": module.INTERNAL_SECONDS,
        "outer_seconds": module.OUTER_SECONDS,
        "rlimit_as_bytes": 1 << 30, "threads": 1,
        "maximum_output_bytes": module.MAX_OUTPUT_BYTES,
        "retry": False, "resume_capable": False,
        "ledger_record_limit": module.LEDGER_RECORD_LIMIT}
    return {"schema": module.SCHEMA,
        "status": "FIRST_B_SPEED_CROSSING_CAPTURED",
        "authorization": "STOP", "diagnostic_only": True,
        "product_ready": False, "scientific_pass": False,
        "fusion_decisions_modified": False, "failure": None,
        "stop_reason": "FIRST_COMMITTED_B_PHYSICAL_CROSSING",
        "limits": limits, "events": 10, "records": 3,
        "crossing": crossing,
        "most_recent_completed_b_uwb_drift_chain": chain,
        "recent_causal_ledger": list(observer.recent_causal_records),
        "observer_failures": [], "invalid_state": None,
        "provenance": {"source_sha256": {"src/example.py": "a" * 64},
            "factory_inputs": {},
            "base_observer_sha256": base._sha(base.Path(base.__file__).resolve()),
            "self_sha256": base._sha(module.Path(module.__file__).resolve())},
        "wall_s": 1.0, "peak_rss_kib": 1}


def test_result_validator_accepts_exact_chain_and_rejects_same_frame_tamper():
    result = _valid_result_document()
    module.validate_result(result)
    result["crossing"]["same_frame_a"]["native_identity"]["sample_index"] += 1
    with pytest.raises(ValueError, match="invalid first B speed-crossing"):
        module.validate_result(result)
