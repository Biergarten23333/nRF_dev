from __future__ import annotations

from dataclasses import replace
import hashlib
import pickle

import numpy as np
import pytest

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CAPTURE2_PROTOCOL_SLOTS,
    ContinuousEvent,
    ImuTimer2Fields,
    UwbTimer2Fields,
)
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionEventRouter,
    FullSessionUwbEventTicket,
    _FullSessionDeliveryOwner,
    _event_digest,
    _sensor_identity_digest,
)
from biospur_fusion.c2_uwb_root_world.continuous_root_ab import uwb_row_from_event
from biospur_fusion.c2_uwb_root_world.continuous_tight_root_owner import (
    AuditedC2TightRootRejection,
    C2TightRangeDelayedRootOwner,
    PreparedC2TightRootTransaction,
    PreparedC2TightRootRejection,
    SkippedC2TightRootEvent,
)
from biospur_fusion.c2_uwb_root_world.diagnostic_c2_static_owner import (
    DiagnosticC2StaticOwner,
)
from biospur_fusion.c2_uwb_root_world.diagnostic_pelvis_orientation import PELVIS_NODE
from biospur_fusion.c2_uwb_root_world.split_fusion import (
    FixedLagDriftConfig,
    FixedLagRangeDriftCorrector,
)
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel
from biospur_fusion.ingest.events import (
    EventStatus,
    RawByteProvenance,
    RecordType,
    TypedEvent,
)
from biospur_fusion.root_r3 import CausalDelayedRootFilter, ImuSample, RootState


def _ticket(event: ContinuousEvent) -> FullSessionUwbEventTicket:
    return _delivery(event).issue(event).dispatch()


def _delivery(*events: ContinuousEvent) -> _FullSessionDeliveryOwner:
    router = object.__new__(FullSessionEventRouter)
    router._pending_events = tuple(events)
    router._pending_event_digests = tuple(_event_digest(event) for event in events)
    router._pending_sensor_digests = tuple(
        _sensor_identity_digest(event) for event in events
    )
    router._pending_cursor = 0
    router._delivery_authority = None
    return _FullSessionDeliveryOwner(router)


def _imu_child_from(event: ContinuousEvent):
    timer = event.uwb_timer2.strobe_timer2_us
    record = replace(
        event.payload_owner, record_type=RecordType.IMU,
        payload={"base_timer2_us": timer - 5_000, "delta_us": 5_000,
                 "acc_raw": [0, 0, 2048], "gyro_raw": [0, 0, 0]},
    )
    imu = replace(
        event, kind="IMU", payload_owner=record,
        imu_timer2=ImuTimer2Fields(timer - 5_000, timer), uwb_timer2=None,
    )
    return _delivery(imu).issue(imu).dispatch()


def _event(static: DiagnosticC2StaticOwner, *, node: str = PELVIS_NODE,
           action_index: int = 0, sequence: int = 1,
           strobe_offset_us: int = 0,
           truth: np.ndarray | None = None,
           velocity: np.ndarray | None = None) -> tuple[ContinuousEvent, float]:
    direct = static.clocks[node]
    strobe = direct.first_timer_us + 200_000 + int(strobe_offset_us)
    rounds = np.arange(2_000, 2_800, 100, dtype=int)
    link_ns = np.array([
        direct.link_time_ns(
            event_boot_epoch=direct.boot_epoch, strobe_us=strobe,
            t_round_us=int(value),
        ) for value in rounds
    ])
    reference_s = float(np.median(link_ns)) * 1e-9
    truth = np.array([2.0, 1.2, 0.9]) if truth is None else np.asarray(truth, float)
    velocity = np.zeros(3) if velocity is None else np.asarray(velocity, float)
    dt = link_ns * 1e-9 - reference_s
    bias = static.anchor_delay_m + static.tag_delay_m
    ranges = np.linalg.norm(
        truth[None, :] + dt[:, None] * velocity[None, :] - static.anchors_m,
        axis=1,
    ) + bias
    frame = strobe + 4_000
    common_ns = int(round(direct.a_ns_per_us * strobe + direct.b_ns))
    availability_ns = int(round(direct.a_ns_per_us * frame + direct.b_ns))
    raw = RawByteProvenance(
        sequence, 100 + sequence * 100, 180 + sequence * 100,
        hashlib.sha256(f"owned-{node}-{sequence}".encode()).hexdigest(), 0,
    )
    payload = {
        "packet_sequence": sequence, "sweep": sequence,
        "strobe_us": strobe, "frame_us": frame,
        "anchor_id": list(range(8)),
        "range_mm": [int(round(value * 1_000.0)) for value in ranges],
        "t_round_us": rounds.tolist(), "quality_percent": [100] * 8,
        "valid_mask": 0xFF, "identity": 1, "node_ms": 1,
    }
    record = TypedEvent(
        node, direct.boot_epoch, RecordType.UWB, sequence, strobe,
        common_ns, 100, 0, payload, {}, EventStatus.DECODED, raw,
    )
    event = ContinuousEvent(
        f"v47:{raw.record_index}:0:{raw.start_offset}:{raw.end_offset}:{raw.encoded_sha256}",
        "UWB", action_index, CAPTURE2_PROTOCOL_SLOTS[action_index].action_id,
        common_ns, availability_ns, node, direct.boot_epoch, "B306_TIMER2",
        "1" * 64, "2" * 64, "3" * 64, "diagnostic-label", record,
        uwb_timer2=UwbTimer2Fields(strobe, frame),
    )
    return event, reference_s


def _fixture(*, action_index: int = 0, position_offset=(0.04, -0.03, 0.02)):
    static = DiagnosticC2StaticOwner.from_sealed_archives()
    event, reference_s = _event(static, action_index=action_index)
    vector = np.zeros(9)
    vector[:3] = np.array([2.0, 1.2, 0.9]) + np.asarray(position_offset)
    vector[3:6] = (0.1, -0.04, 0.02)
    vector[6:9] = (0.01, -0.02, 0.03)
    root = CausalDelayedRootFilter(
        RootState(reference_s, vector, np.eye(9)), static.root_config,
    )
    drift = FixedLagRangeDriftCorrector(FixedLagDriftConfig())
    owner = C2TightRangeDelayedRootOwner(root=root, drift=drift, static=static)
    return static, event, root, drift, owner


def _owner_for_event_state(static, event, *, position, velocity):
    direct = static.clocks[PELVIS_NODE]
    row = uwb_row_from_event(event.payload_owner)
    valid = [slot for slot in range(8) if row.valid_mask & (1 << slot)]
    reference_s = float(np.median([
        direct.link_time_ns(
            event_boot_epoch=row.boot, strobe_us=row.strobe_us,
            t_round_us=row.t_round_us[slot],
        ) for slot in valid
    ])) * 1e-9
    vector = np.zeros(9)
    vector[:3] = np.asarray(position, float)
    vector[3:6] = np.asarray(velocity, float)
    root = CausalDelayedRootFilter(
        RootState(reference_s, vector, np.eye(9)), static.root_config,
    )
    drift = FixedLagRangeDriftCorrector(FixedLagDriftConfig())
    owner = C2TightRangeDelayedRootOwner(root=root, drift=drift, static=static)
    return root, drift, owner


def _root_bytes(root: CausalDelayedRootFilter) -> bytes:
    return pickle.dumps(root._prepare_position_rollback(), protocol=5)


def _fingerprint(root, drift, owner):
    return _root_bytes(root), drift.owner_digest(), owner.owner_digest()


def test_likelihood_information_is_prior_covariance_independent_and_factor_bound():
    static, event, root, _drift, owner = _fixture()
    row = uwb_row_from_event(event.payload_owner)
    direct = static.clocks[PELVIS_NODE]
    clock = ClockModel(direct.boot_epoch, direct.a_ns_per_us, direct.b_ns, 0.0)
    state = root.current_state
    altered_prior = RootState(state.time_s, state.vector.copy(), np.eye(9) * 99.0)
    first = owner._likelihood(state, row, clock)
    second = owner._likelihood(altered_prior, row, clock)
    np.testing.assert_array_equal(first[3], second[3])
    assert first[4] == second[4]
    assert first[0].factors.anchors == second[0].factors.anchors


def test_tight_owner_import_closure_forbids_legacy_action_orientation():
    import ast
    from pathlib import Path
    import biospur_fusion.c2_uwb_root_world.continuous_tight_root_owner as target

    tree = ast.parse(Path(target.__file__).read_text())
    imported = {
        node.module or "" for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any("diagnostic_pelvis_orientation" in name for name in imported)
    assert not any("action00_gap02" in name for name in imported)


def test_absolute_mle_and_bounded_root_drift_residuals_are_distinct_same_factors():
    static, event, root, _drift, owner = _fixture(position_offset=(0.20, 0.0, 0.0))
    row = uwb_row_from_event(event.payload_owner)
    direct = static.clocks[PELVIS_NODE]
    clock = ClockModel(direct.boot_epoch, direct.a_ns_per_us, direct.b_ns, 0.0)
    prepared, mle_raw, pseudo, covariance, _digest = owner._likelihood(
        root.current_state, row, clock,
    )
    position_plan = root.prepare_position(
        __import__("biospur_fusion.root_r3", fromlist=["PositionObservation"]).PositionObservation(
            root.current_state.time_s, event.availability_global_ns * 1e-9,
            pseudo, covariance, event.event_id, mle_raw.anchors,
        ), state_update_indices=(0, 1, 2),
    )
    bounded = owner._drift_decision_at_bounded_position(
        prepared.factors,
        position_m=position_plan.measurement_candidate.position_m,
        velocity_mps=position_plan.measurement_candidate.velocity_mps,
        iterations=mle_raw.iterations,
    )
    assert not np.array_equal(mle_raw.innovations_m, bounded.innovations_m)
    np.testing.assert_array_equal(mle_raw.measured_ranges_m, bounded.measured_ranges_m)
    assert mle_raw.anchors == bounded.anchors


def test_pelvis_commit_is_p_only_plus_velocity_only_and_a_is_untouched():
    _static, event, root, drift, owner = _fixture()
    a = CausalDelayedRootFilter(root.current_state, root.config)
    a_before = _root_bytes(a)
    root_before = root.current_state
    plan = owner.prepare(_ticket(event), expected_root=root.publication_token())
    assert type(plan) is PreparedC2TightRootTransaction
    pending = owner._C2TightRangeDelayedRootOwner__pending
    position_only_velocity = (
        pending.position_plan.snapshots[-1].state.vector[3:6].copy()
    )
    position_only_covariance = (
        pending.position_plan.snapshots[-1].state.covariance.copy()
    )
    position, temporal = owner.commit(plan)
    assert position.accepted
    assert a_before == _root_bytes(a)
    np.testing.assert_allclose(
        root.current_state.vector[3:6],
        position_only_velocity + plan.velocity_delta_mps,
        rtol=0.0, atol=0.0,
    )
    np.testing.assert_array_equal(root.current_state.vector[6:9], root_before.vector[6:9])
    np.testing.assert_array_equal(
        root.current_state.covariance, position_only_covariance,
    )
    if temporal.accepted:
        np.testing.assert_array_equal(temporal.accelerometer_bias_delta_mps2, np.zeros(3))
    assert drift.owner_digest() != FixedLagRangeDriftCorrector().owner_digest()


def test_legal_between_imu_uwb_uses_root_owned_future_reference_prediction():
    static, event, _root, _drift, _owner = _fixture()
    _row = uwb_row_from_event(event.payload_owner)
    _direct = static.clocks[PELVIS_NODE]
    reference_s = float(np.median([
        _direct.link_time_ns(
            event_boot_epoch=_row.boot, strobe_us=_row.strobe_us,
            t_round_us=value,
        ) for value in _row.t_round_us
    ])) * 1e-9
    vector = np.zeros(9)
    vector[:3] = (2.04, 1.17, 0.92)
    root = CausalDelayedRootFilter(
        RootState(reference_s - 0.001, vector, np.eye(9)), static.root_config,
    )
    drift = FixedLagRangeDriftCorrector(FixedLagDriftConfig())
    owner = C2TightRangeDelayedRootOwner(root=root, drift=drift, static=static)
    before = _fingerprint(root, drift, owner)
    plan = owner.prepare(_ticket(event), expected_root=root.publication_token())
    assert type(plan) in {PreparedC2TightRootTransaction, PreparedC2TightRootRejection}
    assert _fingerprint(root, drift, owner)[0:2] == before[0:2]
    owner.commit(plan)


def test_authenticated_non_pelvis_is_audited_skip_and_full_noop():
    static, _event0, root, drift, owner = _fixture()
    other = next(node for node in static.clocks if node != PELVIS_NODE)
    event, _ = _event(static, node=other)
    before = _fingerprint(root, drift, owner)
    result = owner.prepare(_ticket(event), expected_root=root.publication_token())
    assert type(result) is SkippedC2TightRootEvent
    assert result.reason == "AUTHENTICATED_NON_PELVIS_UWB_AUDIT_ONLY"
    assert _fingerprint(root, drift, owner) == before


@pytest.mark.parametrize("valid_mask", (0x00, 0x01, 0x03, 0x07))
def test_insufficient_links_are_audited_inert_and_later_event_continues(valid_mask):
    static, event, root, drift, owner = _fixture()
    later, _later_reference = _event(
        static, sequence=2, strobe_offset_us=120_000,
        truth=root.current_state.position_m,
    )
    record = replace(
        event.payload_owner,
        global_time_ns=None, global_time_sigma_ns=None,
        payload={**event.payload_owner.payload, "valid_mask": valid_mask},
    )
    rejected_event = replace(event, payload_owner=record)
    later = replace(later, payload_owner=replace(
        later.payload_owner, global_time_ns=None, global_time_sigma_ns=None,
    ))
    delivery = _delivery(rejected_event, later)
    rejected_ticket = delivery.issue(rejected_event).dispatch()
    before = _fingerprint(root, drift, owner)
    rejected = owner.prepare(
        rejected_ticket, expected_root=root.publication_token(),
    )
    assert type(rejected) is SkippedC2TightRootEvent
    assert rejected.reason == "REJECT_FEWER_THAN_FOUR_LINKS"
    assert _fingerprint(root, drift, owner) == before
    assert owner._C2TightRangeDelayedRootOwner__pending is None

    with pytest.raises(RuntimeError, match="REPLAYED"):
        owner.prepare(rejected_ticket, expected_root=root.publication_token())
    assert _fingerprint(root, drift, owner) == before

    assert later.common_global_ns > rejected_event.common_global_ns
    assert later.availability_global_ns > rejected_event.availability_global_ns
    continuation = owner.prepare(
        delivery.issue(later).dispatch(), expected_root=root.publication_token(),
    )
    assert type(continuation) in {
        PreparedC2TightRootTransaction, PreparedC2TightRootRejection,
    }
    owner.commit(continuation)


@pytest.mark.parametrize("anchor_ids", (
    (0, 1, 2, 3, 4, 5, 6, 6),
    (1, 0, 2, 3, 4, 5, 6, 7),
    (0, 1, 2, 3, 4, 5, 6, 8),
))
def test_noncanonical_anchor_identity_is_audited_inert_and_later_event_continues(
    anchor_ids,
):
    static, event, root, drift, owner = _fixture()
    later, _ = _event(static, sequence=2, strobe_offset_us=120_000)
    poisoned = replace(event, payload_owner=replace(
        event.payload_owner,
        payload={**event.payload_owner.payload, "anchor_id": list(anchor_ids)},
    ))
    delivery = _delivery(poisoned, later)
    before = _fingerprint(root, drift, owner)
    skipped = owner.prepare(
        delivery.issue(poisoned).dispatch(),
        expected_root=root.publication_token(),
    )
    assert type(skipped) is SkippedC2TightRootEvent
    assert skipped.reason == "REJECT_ANCHOR_IDENTITY_INVALID"
    assert _fingerprint(root, drift, owner) == before
    assert owner._C2TightRangeDelayedRootOwner__pending is None

    continuation = owner.prepare(
        delivery.issue(later).dispatch(), expected_root=root.publication_token(),
    )
    assert type(continuation) in {
        PreparedC2TightRootTransaction, PreparedC2TightRootRejection,
    }
    owner.commit(continuation)


@pytest.mark.parametrize(("method_name", "message"), (
    ("_likelihood", "raw range derivative is singular"),
    ("_likelihood", "raw range factor geometry failed rank/condition"),
    ("_likelihood", "raw likelihood derivative is singular"),
    ("_likelihood", "raw likelihood position geometry is rank deficient"),
    ("_likelihood", "raw likelihood position geometry failed condition gate"),
    ("_drift_decision_at_bounded_position", "bounded root range derivative is singular"),
    ("_drift_decision_at_bounded_position", "bounded root raw geometry failed condition gate"),
))
def test_expected_raw_geometry_failures_are_audited_inert_and_continue(
    monkeypatch, method_name, message,
):
    static, event, root, drift, owner = _fixture()
    later, _ = _event(static, sequence=2, strobe_offset_us=120_000)
    delivery = _delivery(event, later)
    before = _fingerprint(root, drift, owner)
    original = getattr(owner, method_name)

    def fail_geometry(*_args, **_kwargs):
        raise ValueError(message)

    monkeypatch.setattr(owner, method_name, fail_geometry)
    skipped = owner.prepare(
        delivery.issue(event).dispatch(), expected_root=root.publication_token(),
    )
    assert type(skipped) is SkippedC2TightRootEvent
    assert skipped.reason == "REJECT_RAW_GEOMETRY"
    assert _fingerprint(root, drift, owner) == before
    assert owner._C2TightRangeDelayedRootOwner__pending is None

    monkeypatch.setattr(owner, method_name, original)
    continuation = owner.prepare(
        delivery.issue(later).dispatch(), expected_root=root.publication_token(),
    )
    assert type(continuation) in {
        PreparedC2TightRootTransaction, PreparedC2TightRootRejection,
    }
    owner.commit(continuation)


def test_real_factorizer_singular_geometry_is_audited_inert_and_continues():
    static, event, _root, _drift, _owner = _fixture()
    singular = replace(event, payload_owner=replace(
        event.payload_owner,
        payload={**event.payload_owner.payload, "t_round_us": [2_000] * 8},
    ))
    root, drift, owner = _owner_for_event_state(
        static, singular, position=static.anchors_m[0], velocity=np.zeros(3),
    )
    later, _ = _event(
        static, sequence=2, strobe_offset_us=120_000,
        truth=root.current_state.position_m,
    )
    delivery = _delivery(singular, later)
    singular_ticket = delivery.issue(singular).dispatch()
    before = _fingerprint(root, drift, owner)
    skipped = owner.prepare(
        singular_ticket, expected_root=root.publication_token(),
    )
    assert type(skipped) is SkippedC2TightRootEvent
    assert skipped.reason == "REJECT_RAW_GEOMETRY"
    assert _fingerprint(root, drift, owner) == before
    with pytest.raises(RuntimeError, match="REPLAYED"):
        owner.prepare(singular_ticket, expected_root=root.publication_token())
    continuation = owner.prepare(
        delivery.issue(later).dispatch(), expected_root=root.publication_token(),
    )
    assert type(continuation) in {
        PreparedC2TightRootTransaction, PreparedC2TightRootRejection,
    }
    owner.commit(continuation)


def test_real_rank_deficient_four_link_geometry_is_audited_inert_and_continues():
    static, event, _root, _drift, _owner = _fixture()
    rounds = [2_000, 2_000, 2_000, 4_000, 2_000, 2_000, 2_000, 2_000]
    rank_deficient = replace(event, payload_owner=replace(
        event.payload_owner,
        payload={**event.payload_owner.payload, "t_round_us": rounds,
                 "valid_mask": 0x0F},
    ))
    direct = static.clocks[PELVIS_NODE]
    clock = ClockModel(direct.boot_epoch, direct.a_ns_per_us, direct.b_ns, 0.0)
    strobe = rank_deficient.uwb_timer2.strobe_timer2_us
    dt_s = (clock.seconds(strobe + 0.5 * rounds[3])
            - clock.seconds(strobe + 0.5 * rounds[0]))
    velocity = np.array([0.0, 0.0, static.anchors_m[3, 2] / dt_s])
    root, drift, owner = _owner_for_event_state(
        static, rank_deficient, position=np.array([2.0, 1.0, 0.0]),
        velocity=velocity,
    )
    later, _ = _event(
        static, sequence=2, strobe_offset_us=120_000,
        truth=root.current_state.position_m,
    )
    delivery = _delivery(rank_deficient, later)
    rank_ticket = delivery.issue(rank_deficient).dispatch()
    before = _fingerprint(root, drift, owner)
    skipped = owner.prepare(
        rank_ticket, expected_root=root.publication_token(),
    )
    assert type(skipped) is SkippedC2TightRootEvent
    assert skipped.reason == "REJECT_RAW_GEOMETRY"
    assert _fingerprint(root, drift, owner) == before
    with pytest.raises(RuntimeError, match="REPLAYED"):
        owner.prepare(rank_ticket, expected_root=root.publication_token())
    continuation = owner.prepare(
        delivery.issue(later).dispatch(), expected_root=root.publication_token(),
    )
    assert type(continuation) in {
        PreparedC2TightRootTransaction, PreparedC2TightRootRejection,
    }
    owner.commit(continuation)


def test_unexpected_likelihood_value_error_propagates_without_owner_mutation(monkeypatch):
    _static, event, root, drift, owner = _fixture()
    before = _fingerprint(root, drift, owner)

    def fail_unexpected(*_args, **_kwargs):
        raise ValueError("unexpected likelihood programming failure")

    monkeypatch.setattr(owner, "_likelihood", fail_unexpected)
    with pytest.raises(ValueError, match="unexpected likelihood programming failure"):
        owner.prepare(_ticket(event), expected_root=root.publication_token())
    assert _fingerprint(root, drift, owner) == before
    assert owner._C2TightRangeDelayedRootOwner__pending is None


def test_tight_owner_rejects_authenticated_imu_child_without_consuming_state():
    _static, event, root, drift, owner = _fixture()
    before = _fingerprint(root, drift, owner)
    with pytest.raises(TypeError, match="authenticated UWB ticket"):
        owner.prepare(
            _imu_child_from(event), expected_root=root.publication_token(),
        )
    assert _fingerprint(root, drift, owner) == before


def test_label_permutation_does_not_change_sensor_or_numerical_outcome():
    fixtures = [_fixture(action_index=index) for index in (0, 2)]
    results = []
    for _static, event, root, _drift, owner in fixtures:
        plan = owner.prepare(_ticket(event), expected_root=root.publication_token())
        owner.commit(plan)
        results.append((
            plan.sensor_identity_digest, plan.raw_factor_digest,
            plan.likelihood_information_digest, root.current_state.vector.tobytes(),
            root.current_state.covariance.tobytes(),
        ))
    assert results[0] == results[1]


def test_stale_foreign_tampered_and_replayed_plans_are_full_noops():
    _static, event, root, drift, owner = _fixture()
    plan = owner.prepare(_ticket(event), expected_root=root.publication_token())
    before = _fingerprint(root, drift, owner)
    with pytest.raises(RuntimeError, match="STALE_FORGED_REPLAYED_OR_FOREIGN"):
        owner.commit(replace(plan, digest="0" * 64))
    assert _fingerprint(root, drift, owner) == before
    owner.commit(plan)
    committed = _fingerprint(root, drift, owner)
    with pytest.raises(RuntimeError, match="STALE_FORGED_REPLAYED_OR_FOREIGN"):
        owner.commit(plan)
    assert _fingerprint(root, drift, owner) == committed

    _s2, e2, r2, d2, foreign = _fixture()
    foreign_before = _fingerprint(r2, d2, foreign)
    with pytest.raises(RuntimeError, match="STALE_FORGED_REPLAYED_OR_FOREIGN"):
        foreign.commit(plan)
    assert _fingerprint(r2, d2, foreign) == foreign_before


def test_injected_drift_commit_failure_rolls_back_and_rejection_can_continue(monkeypatch):
    static, event, root, drift, owner = _fixture()
    next_event, next_reference_s = _event(
        static, sequence=2, strobe_offset_us=120_000,
    )
    delivery = _delivery(event, next_event)
    plan = owner.prepare(
        delivery.issue(event).dispatch(), expected_root=root.publication_token(),
    )
    root_before = _root_bytes(root)
    drift_before = drift.owner_digest()
    ledger_before = owner._C2TightRangeDelayedRootOwner__ledger.tobytes()
    revision_before = owner._C2TightRangeDelayedRootOwner__revision
    original = FixedLagRangeDriftCorrector.commit_prepared

    def commit_then_fail(self, candidate):
        original(self, candidate)
        raise RuntimeError("injected drift participant failure")

    monkeypatch.setattr(FixedLagRangeDriftCorrector, "commit_prepared", commit_then_fail)
    with pytest.raises(RuntimeError, match="injected drift"):
        owner.commit(plan)
    assert _root_bytes(root) == root_before
    assert drift.owner_digest() == drift_before
    assert owner._C2TightRangeDelayedRootOwner__ledger.tobytes() == ledger_before
    assert owner._C2TightRangeDelayedRootOwner__revision == revision_before
    assert owner._C2TightRangeDelayedRootOwner__pending is None
    with pytest.raises(RuntimeError):
        owner.commit(plan)

    monkeypatch.setattr(FixedLagRangeDriftCorrector, "commit_prepared", original)
    assert next_event.common_global_ns > event.common_global_ns
    assert next_event.availability_global_ns > event.availability_global_ns
    assert root.add_imu(ImuSample(
        next_reference_s, next_reference_s,
        np.array([0.0, 0.0, 9.80665]), np.eye(3), 99,
    ))
    next_plan = owner.prepare(
        delivery.issue(next_event).dispatch(), expected_root=root.publication_token(),
    )
    owner.commit(next_plan)
    assert drift.owner_digest() != drift_before


def test_ordinary_position_rejection_commits_only_health_and_continues_same_owner():
    static, event, root, drift, owner = _fixture(position_offset=(20.0, 0.0, 0.0))
    later, next_reference_s = _event(
        static, sequence=2, strobe_offset_us=120_000,
        truth=root.current_state.position_m,
    )
    delivery = _delivery(event, later)
    numerical_before = (
        root.current_state.vector.tobytes(), root.current_state.covariance.tobytes(),
        drift.owner_digest(),
        owner._C2TightRangeDelayedRootOwner__ledger.tobytes(),
    )
    rejected = owner.prepare(
        delivery.issue(event).dispatch(), expected_root=root.publication_token(),
    )
    assert type(rejected) is PreparedC2TightRootRejection
    audit = owner.commit(rejected)
    assert type(audit) is AuditedC2TightRootRejection
    assert not audit.numerical_state_mutated and not audit.drift_mutated
    assert (
        root.current_state.vector.tobytes(), root.current_state.covariance.tobytes(),
        drift.owner_digest(),
        owner._C2TightRangeDelayedRootOwner__ledger.tobytes(),
    ) == numerical_before
    assert root.add_imu(ImuSample(
        next_reference_s, next_reference_s,
        np.array([0.0, 0.0, 9.80665]), np.eye(3), 99,
    ))
    continuation = owner.prepare(
        delivery.issue(later).dispatch(), expected_root=root.publication_token(),
    )
    assert type(continuation) in {
        PreparedC2TightRootTransaction, PreparedC2TightRootRejection,
    }
