from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tools import diagnose_c2_phase_c_prefix as diagnostic
from tools import build_c2_full_session_ten_node_ab as factory
from biospur_fusion.root_r3.models import RootState
from biospur_fusion.c2_uwb_root_world.continuous_full_session import (
    load_continuous_session_inventory,
)


def _observer() -> diagnostic.PhaseCPrefixObserver:
    coordinator = SimpleNamespace(
        consume_record_ticket=lambda _ticket: None,
        _validate_record_batch=lambda _ticket, _events: "ok",
    )
    return diagnostic.PhaseCPrefixObserver(
        SimpleNamespace(coordinator=coordinator), 0.0,
    )


def _state(*, position=(0.0, 0.0, 0.0), velocity=(0.0, 0.0, 0.0),
           bias=(0.0, 0.0, 0.0), covariance_scale=1.0, time_s=1.0):
    return RootState(
        time_s,
        np.asarray((*position, *velocity, *bias), dtype=float),
        np.eye(9) * covariance_scale,
    )


def test_preregistered_boundary_resource_and_envelope_contract_are_exact():
    assert diagnostic.EVENT_CAP == 268_749
    assert diagnostic.RECORD_COUNT == 36_549
    assert diagnostic.INTERNAL_SECONDS == 1_500.0
    assert diagnostic.OUTER_SECONDS == 1_560
    assert diagnostic.RLIMIT_AS_BYTES == 1 << 30
    assert diagnostic.MAX_OUTPUT_BYTES == 5 << 20
    assert diagnostic.HISTORICAL_BOUNDARY == (
        36_547,
        (1212258, 221493385, 221493563,
         "a66833d6760717411d7ab881619480f69954fd0791165fb8460b085ef021a127"),
        268_739,
    )
    assert diagnostic.FOLLOWING_BOUNDARY == (
        36_548,
        (1212259, 221493563, 221493741,
         "336d460cfba53359e594e5f59c1428e43e325494ffa3d4a693b5a8dd02dc98b1"),
        268_749,
    )
    _clock, _clocks, anchors, _delays, _tag_delay = factory._static_inputs()
    assert np.array_equal(diagnostic.LOWER_M, np.min(anchors, axis=0) - 0.75)
    assert np.array_equal(diagnostic.UPPER_M, np.max(anchors, axis=0) + 0.75)


def test_admission_effect_observer_accepts_only_p_only_authenticated_effect():
    observer = _observer()
    predicted = _state(position=(1.0, 2.0, 3.0), velocity=(4.0, 5.0, 6.0),
                       bias=(0.1, 0.2, 0.3))
    candidate = _state(position=(1.2, 2.1, 2.9), velocity=(4.0, 5.0, 6.0),
                       bias=(0.1, 0.2, 0.3))
    plan = SimpleNamespace(
        imu_prediction=predicted,
        measurement_candidate=candidate,
        decision=SimpleNamespace(
            availability_applied_velocity_delta_mps=np.zeros(3),
        ),
    )
    prepared = SimpleNamespace(
        causal_transaction=SimpleNamespace(root_plan=plan),
    )
    observer._observe_admission_effect(prepared)
    assert observer.uwb_position_only

    changed_velocity = _state(
        position=(1.2, 2.1, 2.9), velocity=(4.0, 5.1, 6.0),
        bias=(0.1, 0.2, 0.3),
    )
    plan.measurement_candidate = changed_velocity
    observer._observe_admission_effect(prepared)
    assert not observer.uwb_position_only


@pytest.mark.parametrize(
    "future,rejected",
    ((False, False), (False, True), (True, False), (True, True)),
)
def test_native_effect_compares_to_exact_post_imu_candidate(future, rejected):
    observer = _observer()
    imu = _state(
        position=(2.0, 3.0, 4.0), velocity=(5.0, 6.0, 7.0),
        bias=(0.1, 0.2, 0.3), covariance_scale=2.0, time_s=4.0,
    )
    delta = np.zeros(3) if rejected else np.array((0.2, -0.1, 0.05))
    candidate_vector = imu.vector.copy()
    candidate_vector[3:6] += delta
    candidate = RootState(imu.time_s, candidate_vector, imu.covariance.copy())
    velocity_plan = SimpleNamespace(
        _candidate_bundle=SimpleNamespace(
            snapshots=[SimpleNamespace(state=candidate)],
        ),
    )
    if future:
        future_imu = SimpleNamespace(
            imu_plan=SimpleNamespace(candidate_state=imu),
        )
        current_imu = None
        root_plan = SimpleNamespace(imu_velocity_plan=velocity_plan)
    else:
        future_imu = None
        current_imu = SimpleNamespace(candidate_state=imu)
        root_plan = velocity_plan
    prepared = SimpleNamespace(
        drift_plan=SimpleNamespace(result=SimpleNamespace(
            consumed_observation_digest="a" * 64,
            velocity_delta_mps=delta,
        )),
        history_plan=SimpleNamespace(
            future_imu=future_imu,
            current_imu=current_imu,
            root_plan=root_plan,
        ),
    )
    observer._observe_native_effect(prepared)
    assert observer.drift_velocity_only
    assert observer.bias_unchanged_by_drift


class _FakeDriftOwner:
    def __init__(self):
        self.pending = []
        self.pending_count = 0
        self.config = SimpleNamespace(maximum_velocity_step_mps=0.5)
        self.last_time = None
        self.revision = 0
        self.stream_owner_digest = "a" * 64

    def commit(self, plan):
        result = plan.result
        if result.kind == "ADMISSION":
            self.pending.append(plan.package)
        elif result.consumed_observation_digest is not None:
            self.pending.pop(0)
            self.last_time = plan.native_time_s
        self.pending_count = len(self.pending)
        self.revision += 1

    def snapshot(self):
        return SimpleNamespace(
            _state=SimpleNamespace(last_consumed_native200_time_s=self.last_time),
        )


def test_queued_observation_is_not_counted_as_consumed_then_is_consumed_once():
    observer = _observer()
    owner = _FakeDriftOwner()
    observation = SimpleNamespace(
        measurement_time_s=0.9, source_sequence=17, tag_id="SHARED_ROOT",
    )
    package = SimpleNamespace(
        digest="1" * 64, availability_time_ns=1_000_000_000,
        observation=observation, admission_digest="a" * 64,
        root_plan_digest="b" * 64, packet_digest="c" * 64,
        epoch_digest="d" * 64, anchors_used=(0, 2, 7),
        trusted_nodes=("BSFC200",),
    )
    admission = SimpleNamespace(
        result=SimpleNamespace(
            kind="ADMISSION", accepted=True, reason="QUEUED",
            consumed_observation_digest=package.digest,
            velocity_delta_mps=np.zeros(3),
        ),
        package=package,
        _candidate_state=SimpleNamespace(pending=(package,)),
        digest="2" * 64,
    )
    before = owner.pending_count
    owner.commit(admission)
    observer._observe_drift_commit(
        owner, admission, before_pending=before,
        after_pending=owner.pending_count,
    )
    assert observer.drift == {"queued": 1}
    assert not observer.consumed_observations
    assert observer.pending_admission_identities == [{
        "package_digest": "1" * 64,
        "admission_digest": "a" * 64,
        "root_plan_digest": "b" * 64,
        "packet_digest": "c" * 64,
        "epoch_digest": "d" * 64,
        "measurement_time_s": 0.9,
        "availability_time_ns": 1_000_000_000,
        "source_sequence": 17,
        "tag_id": "SHARED_ROOT",
        "anchors_used": [0, 2, 7],
        "trusted_nodes": ["BSFC200"],
    }]

    native = SimpleNamespace(
        result=SimpleNamespace(
            kind="NATIVE200", accepted=True, reason="ACCEPTED",
            consumed_observation_digest=package.digest,
            velocity_delta_mps=np.array((0.1, 0.0, 0.0)),
        ),
        _base_state=SimpleNamespace(pending=(package,)),
        _dependency_plan=None, native_time_s=1.1, digest="3" * 64,
    )
    before = owner.pending_count
    owner.commit(native)
    observer._observe_drift_commit(
        owner, native, before_pending=before,
        after_pending=owner.pending_count,
    )
    assert observer.drift == {"queued": 1, "consumed": 1, "accepted": 1}
    assert observer.drift_duplicate_or_replay == 0


def test_rejected_correction_consumes_once_with_zero_root_delta():
    observer = _observer()
    owner = _FakeDriftOwner()
    package = SimpleNamespace(digest="4" * 64, availability_time_ns=1_000_000_000)
    owner.pending = [package]
    owner.pending_count = 1
    rejected = SimpleNamespace(
        result=SimpleNamespace(
            kind="NATIVE200", accepted=False, reason="WARMUP",
            consumed_observation_digest=package.digest,
            velocity_delta_mps=np.zeros(3),
        ),
        _base_state=SimpleNamespace(pending=(package,)),
        _dependency_plan=None, native_time_s=1.2, digest="5" * 64,
    )
    before = owner.pending_count
    owner.commit(rejected)
    observer._observe_drift_commit(
        owner, rejected, before_pending=before,
        after_pending=owner.pending_count,
    )
    assert observer.drift["rejected"] == 1
    assert observer.drift["consumed"] == 1
    assert observer.drift_duplicate_or_replay == 0


def test_nonfinite_velocity_delta_is_explicitly_fail_closed():
    observer = _observer()
    owner = _FakeDriftOwner()
    package = SimpleNamespace(digest="6" * 64, availability_time_ns=1_000_000_000)
    owner.pending = [package]
    owner.pending_count = 1
    plan = SimpleNamespace(
        result=SimpleNamespace(
            kind="NATIVE200", accepted=True, reason="BROKEN",
            consumed_observation_digest=package.digest,
            velocity_delta_mps=np.array((np.nan, 0.0, 0.0)),
        ),
        _base_state=SimpleNamespace(pending=(package,)),
        _dependency_plan=None, native_time_s=1.2, digest="9" * 64,
    )
    before = owner.pending_count
    owner.commit(plan)
    observer._observe_drift_commit(
        owner, plan, before_pending=before, after_pending=owner.pending_count,
    )
    assert not observer.drift_deltas_finite
    assert observer.drift_maximum_delta_norm_mps == 0.0


def test_every_publication_capture_retains_transient_violation():
    observer = _observer()
    observer._capture_state(
        "b", _state(position=(10.0, 0.0, 0.0), velocity=(13.0, 0.0, 0.0)),
    )
    observer._capture_state("b", _state(position=(1.0, 1.0, 1.0)))
    assert not observer.b_inside_volume
    assert not observer.b_speed_bounded
    assert observer.branch["b"]["maximum_speed_mps"] == 13.0


def test_state_validity_uses_root_contract_and_seals_first_failure_evidence():
    covariance = np.eye(9)
    covariance[0, 1] = 5e-11
    within_contract = RootState(2.0, np.zeros(9), covariance)
    validation = diagnostic._state_validation(within_contract)
    assert validation["valid"]
    assert validation["maximum_covariance_skew"] == pytest.approx(5e-11)

    observer = _observer()
    observer._capture_state(
        "a", within_contract, publication_revision=4,
        publication_digest="e" * 64,
    )
    assert observer.all_states_valid
    assert observer.first_state_failure is None

    asymmetric = np.eye(9)
    asymmetric[0, 1] = 1e-3
    invalid = SimpleNamespace(
        time_s=2.5, vector=np.zeros(9), covariance=asymmetric,
    )
    observer._capture_state(
        "b", invalid, publication_revision=9,
        publication_digest="f" * 64,
    )
    assert not observer.all_states_valid
    assert observer.first_state_failure == {
        "branch": "b",
        "time_s": 2.5,
        "publication_revision": 9,
        "publication_digest": "f" * 64,
        "reason": "ROOT_COVARIANCE_ASYMMETRIC",
        "maximum_covariance_skew": pytest.approx(1e-3),
        "minimum_symmetric_covariance_eigenvalue": pytest.approx(0.9995),
    }
    observer._capture_state(
        "a", SimpleNamespace(
            time_s=3.0, vector=np.zeros(9), covariance=-np.eye(9),
        ), publication_revision=10, publication_digest="0" * 64,
    )
    assert observer.first_state_failure["publication_revision"] == 9


class _FakeRoot:
    def __init__(self, state):
        self.current_state = state
        self.revision = 0

    def set_state(self, state):
        self.current_state = state
        self.revision += 1

    def publication_token(self):
        return SimpleNamespace(
            state=self.current_state, revision=self.revision,
            digest=hashlib.sha256(
                self.current_state.vector.tobytes()
                + self.current_state.covariance.tobytes()
                + str(self.revision).encode()
            ).hexdigest(),
        )


class _FakeEngine:
    def __init__(self, state, anchors):
        self.root = _FakeRoot(state)
        self.static = SimpleNamespace(anchors_m=anchors)

    def add_imu(self, state):
        self.root.set_state(state)
        return True


class _FakeComposition:
    def __init__(self, state, anchors, drift):
        self.engine = _FakeEngine(state, anchors)
        self.consensus_drift = drift

    def commit_native200(self, prepared):
        self.engine.root.set_state(prepared.final_state)
        self.consensus_drift.commit(prepared.drift_plan)

    def commit_admission(self, _prepared):
        return None

    def commit_gap(self, prepared):
        self.engine.root.set_state(prepared.final_state)


def _native_plan(imu, candidate, package, *, native_time=1.1):
    velocity_plan = SimpleNamespace(
        _candidate_bundle=SimpleNamespace(
            snapshots=[SimpleNamespace(state=candidate)],
        ),
    )
    drift_plan = SimpleNamespace(
        result=SimpleNamespace(
            kind="NATIVE200", accepted=True, reason="ACCEPTED",
            consumed_observation_digest=package.digest,
            velocity_delta_mps=candidate.vector[3:6] - imu.vector[3:6],
        ),
        _base_state=SimpleNamespace(pending=(package,)),
        _dependency_plan=None, native_time_s=native_time, digest="8" * 64,
    )
    return SimpleNamespace(
        drift_plan=drift_plan,
        history_plan=SimpleNamespace(
            native=SimpleNamespace(frame=SimpleNamespace(
                digest="9" * 64, node="BSFC200", boot_epoch=0,
                source_timer_us=1_100_000, source_global_ns=1_100_000_000,
                imu_sample=SimpleNamespace(
                    availability_time_s=native_time, source_sequence=23,
                ),
                publication_revision=11,
                pose_publication_digest="6" * 64,
                raw_provenance=SimpleNamespace(
                    record_index=31, start_offset=100, end_offset=200,
                    encoded_sha256="5" * 64, sample_index=2,
                ),
            )),
            future_imu=None,
            current_imu=SimpleNamespace(candidate_state=imu),
            root_plan=velocity_plan,
        ),
        final_state=candidate,
    )


def test_installed_hooks_are_inert_and_see_mid_batch_and_root_override_commits():
    _clock, _clocks, anchors, _delays, _tag_delay = factory._static_inputs()
    initial = _state(position=(1.0, 1.0, 1.0), time_s=0.5)
    observed_drift = _FakeDriftOwner()
    control_drift = _FakeDriftOwner()
    observed_a = _FakeComposition(initial, anchors, None)
    observed_b = _FakeComposition(initial, anchors, observed_drift)
    control_a = _FakeComposition(initial, anchors, None)
    control_b = _FakeComposition(initial, anchors, control_drift)
    coordinator = SimpleNamespace(
        _a=SimpleNamespace(_composition=observed_a),
        _b=SimpleNamespace(_composition=observed_b),
        consume_record_ticket=lambda _ticket: None,
        _validate_record_batch=lambda _ticket, _events: "ok",
    )
    observer = diagnostic.PhaseCPrefixObserver(
        SimpleNamespace(coordinator=coordinator), 0.0,
    )
    observer._install_hooks()

    admission_plan = SimpleNamespace(
        imu_prediction=_state(
            position=(1.0, 1.0, 1.0), velocity=(0.2, 0.0, 0.0), time_s=0.5,
        ),
        measurement_candidate=_state(
            position=(1.1, 1.0, 1.0), velocity=(0.2, 0.0, 0.0), time_s=0.5,
        ),
        decision=SimpleNamespace(
            availability_applied_velocity_delta_mps=np.zeros(3),
        ),
    )
    admission = SimpleNamespace(
        causal_transaction=SimpleNamespace(root_plan=admission_plan),
    )
    observed_b.commit_admission(admission)
    control_b.commit_admission(admission)
    assert observer.uwb_position_only

    # The real batch owner calls engine.add_imu once per frame.  Exercise the
    # same boundary, including a transient violation followed by recovery.
    for state in (
        _state(position=(1.1, 1.0, 1.0), time_s=0.6),
        _state(position=(10.0, 1.0, 1.0), velocity=(13.0, 0.0, 0.0), time_s=0.7),
        _state(position=(1.2, 1.0, 1.0), time_s=0.8),
    ):
        assert observed_a.engine.add_imu(state) == control_a.engine.add_imu(state)
        assert observed_b.engine.add_imu(state) == control_b.engine.add_imu(state)

    package = SimpleNamespace(digest="7" * 64, availability_time_ns=900_000_000)
    observed_drift.pending = [package]
    observed_drift.pending_count = 1
    control_drift.pending = [package]
    control_drift.pending_count = 1
    imu = _state(
        position=(1.25, 1.0, 1.0), velocity=(0.3, 0.0, 0.0),
        covariance_scale=1.5, time_s=1.0,
    )
    vector = imu.vector.copy()
    vector[3] += 0.1
    final = RootState(imu.time_s, vector, imu.covariance.copy())
    observed_plan = _native_plan(imu, final, package)
    control_plan = _native_plan(imu, final, package)
    observed_b.commit_native200(observed_plan)
    control_b.commit_native200(control_plan)

    assert observed_a.engine.root.current_state.vector.tobytes() == (
        control_a.engine.root.current_state.vector.tobytes()
    )
    assert observed_b.engine.root.current_state.vector.tobytes() == (
        control_b.engine.root.current_state.vector.tobytes()
    )
    assert observed_b.engine.root.current_state.covariance.tobytes() == (
        control_b.engine.root.current_state.covariance.tobytes()
    )
    assert observed_drift.pending_count == control_drift.pending_count == 0
    assert observed_drift.revision == control_drift.revision
    assert not observer.b_inside_volume
    assert not observer.b_speed_bounded
    assert observer.drift["accepted"] == 1
    assert observer.drift_velocity_only
    assert observer.next_native_stream_identities == [{
        "consumed_package_digest": "7" * 64,
        "frame_digest": "9" * 64,
        "node": "BSFC200",
        "boot_epoch": 0,
        "source_timer_us": 1_100_000,
        "source_global_ns": 1_100_000_000,
        "availability_time_ns": 1_100_000_000,
        "sample_source_sequence": 23,
        "publication_revision": 11,
        "pose_publication_digest": "6" * 64,
        "raw_identity": [31, 100, 200, "5" * 64, 2],
    }]
    assert not observer.observer_failures


def test_stream_identity_evidence_remains_bounded_at_observed_scale():
    observer = _observer()
    pending_identity = {
        "package_digest": "1" * 64,
        "admission_digest": "2" * 64,
        "root_plan_digest": "3" * 64,
        "packet_digest": "4" * 64,
        "epoch_digest": "5" * 64,
        "measurement_time_s": 123.0,
        "availability_time_ns": 123_000_000_000,
        "source_sequence": 7,
        "tag_id": "SHARED_ROOT",
        "anchors_used": list(range(8)),
        "trusted_nodes": [f"BSFC2{index:02d}" for index in range(10)],
    }
    native_identity = {
        "consumed_package_digest": "1" * 64,
        "frame_digest": "6" * 64,
        "node": "BSFC200",
        "boot_epoch": 0,
        "source_timer_us": 123_000_000,
        "source_global_ns": 123_000_000_000,
        "availability_time_ns": 123_001_000_000,
        "sample_source_sequence": 8,
        "publication_revision": 9,
        "pose_publication_digest": "7" * 64,
        "raw_identity": [1, 2, 3, "8" * 64, 4],
    }
    observer.pending_admission_identities = [pending_identity] * 620
    observer.next_native_stream_identities = [native_identity] * 620
    payload = json.dumps({
        "pending": observer.pending_admission_identities,
        "native": observer.next_native_stream_identities,
    }, sort_keys=True, separators=(",", ":")).encode()
    assert len(payload) < diagnostic.MAX_OUTPUT_BYTES


def test_hook_time_is_separate_from_instrumented_consume(monkeypatch):
    observer = _observer()
    ticks = iter((10.0, 10.25))
    monkeypatch.setattr(diagnostic.time, "monotonic", lambda: next(ticks))
    assert observer._timed_hook(lambda value: value + 1, 4) == 5
    observer.core_consume_wall_s = 1.0
    assert observer.hook_observer_wall_s == 0.25
    assert observer.consume_minus_hook_wall_s == 0.75


def test_bootstrap_installs_hooks_before_new_owners_can_publish():
    _clock, _clocks, anchors, _delays, _tag_delay = factory._static_inputs()
    initial = _state(position=(1.0, 1.0, 1.0), time_s=0.5)
    observed_a = _FakeComposition(initial, anchors, None)
    observed_b = _FakeComposition(initial, anchors, _FakeDriftOwner())
    installation = SimpleNamespace(
        a=SimpleNamespace(_composition=observed_a),
        b=SimpleNamespace(_composition=observed_b),
    )
    initializer = SimpleNamespace(
        commit_first_group=lambda _prepared: installation,
    )
    coordinator = SimpleNamespace(
        _a=None, _b=None, _initializer=initializer,
        consume_record_ticket=lambda _ticket: None,
        _validate_record_batch=lambda _ticket, _events: "ok",
    )
    observer = diagnostic.PhaseCPrefixObserver(
        SimpleNamespace(coordinator=coordinator), 0.0,
    )

    installed = initializer.commit_first_group(object())
    assert installed is installation
    assert observer.hooks_installed
    assert observer.bootstrap_hook_installs == 1
    coordinator._a, coordinator._b = installed.a, installed.b

    # The first possible publication after ownership installation is already
    # intercepted; the initializer commit itself only installs prepared owners.
    violating = _state(
        position=(10.0, 1.0, 1.0), velocity=(13.0, 0.0, 0.0), time_s=0.6,
    )
    assert observed_b.engine.add_imu(violating)
    assert not observer.b_inside_volume
    assert not observer.b_speed_bounded


def test_drift_owner_replacement_and_revision_reset_are_detected():
    observer = _observer()
    owner = SimpleNamespace(revision=4)
    composition = SimpleNamespace(consensus_drift=owner)
    observer.owners.coordinator._b = SimpleNamespace(_composition=composition)
    observer.initial_drift_owner = owner
    observer.initial_drift_owner_token = id(owner)
    observer.last_drift_revision = 4
    owner.revision = 3
    observer._check_drift_owner()
    assert not observer.drift_revision_monotonic
    composition.consensus_drift = SimpleNamespace(revision=5)
    observer._check_drift_owner()
    assert not observer.drift_owner_unchanged


def test_authenticated_raw_offset_maps_boundary_to_action03_without_session_reset():
    inventory = load_continuous_session_inventory(diagnostic.ROOT)
    coordinator = SimpleNamespace(
        consume_record_ticket=lambda _ticket: None,
        _validate_record_batch=lambda _ticket, _events: "ok",
    )
    owners = SimpleNamespace(
        coordinator=coordinator,
        reader=SimpleNamespace(inventory=inventory),
    )
    observer = diagnostic.PhaseCPrefixObserver(owners, 0.0)
    ticket = SimpleNamespace(raw_identity=diagnostic.FOLLOWING_BOUNDARY[1])
    region = observer._source_region(ticket)
    assert region.region_id == "03_pelvis_hula_circle"
    assert (region.start_offset, region.stop_offset) == (
        219_301_057, 221_736_553,
    )
    assert diagnostic.SESSION_ID == "FULL_SESSION_CONTINUOUS_00_TO_19"


def test_record_observer_uses_o1_owner_state_and_stops_at_exact_cap(monkeypatch):
    region = SimpleNamespace(
        region_id="03_pelvis_hula_circle", kind="ACTION",
        action_id="03_pelvis_hula_circle",
        start_offset=219_301_057, stop_offset=221_736_553,
    )
    coordinator = SimpleNamespace(
        _events=268_739,
        _ab_transaction_total=0, _ab_transaction_journal=(),
        _batched_records=1, _batched_frames=9,
        _scalar_frames=1, _unrouted_frames=0,
    )

    def consume(ticket):
        coordinator._events += len(ticket.event_digests)

    coordinator.consume_record_ticket = consume
    coordinator.audit = lambda: (_ for _ in ()).throw(
        AssertionError("per-record canonical audit must not be materialized")
    )
    owners = SimpleNamespace(
        coordinator=coordinator,
        reader=SimpleNamespace(inventory=SimpleNamespace(regions=(region,))),
    )
    observer = diagnostic.PhaseCPrefixObserver(
        owners, diagnostic.time.monotonic(),
    )
    monkeypatch.setattr(observer, "_install_hooks", lambda: None)
    monkeypatch.setattr(observer, "_capture_roots", lambda: None)
    ticket = SimpleNamespace(
        event_digests=("00" * 32,) * 10,
        raw_identity=diagnostic.FOLLOWING_BOUNDARY[1],
        record_ordinal=diagnostic.FOLLOWING_BOUNDARY[0],
    )
    with pytest.raises(diagnostic._Stop, match="EXACT_EVENT_CAP"):
        observer(ticket)
    assert observer.records == 1
    assert observer.routing_calls == 1
    assert observer.actions == {"03_pelvis_hula_circle"}
    assert observer.regions == {"03_pelvis_hula_circle"}
    assert observer.boundaries[diagnostic.EVENT_CAP]["raw_identity"] == list(
        diagnostic.FOLLOWING_BOUNDARY[1]
    )


def test_root_capture_reads_exact_owner_states_without_materializing_publication():
    a_state = _state(position=(0.5, 0.6, 0.7), time_s=2.0)
    b_state = _state(position=(0.8, 0.9, 1.0), time_s=2.0)
    frames = (SimpleNamespace(digest="f" * 64),)

    def branch(state):
        composition = SimpleNamespace(
            engine=SimpleNamespace(root=SimpleNamespace(current_state=state)),
            history=SimpleNamespace(frames=frames),
        )
        return SimpleNamespace(_composition=composition)

    coordinator = SimpleNamespace(
        _a=branch(a_state), _b=branch(b_state),
        consume_record_ticket=lambda _ticket: None,
        _validate_record_batch=lambda _ticket, _events: "ok",
        diagnostic_publication=lambda: (_ for _ in ()).throw(
            AssertionError("record observer must not materialize publication")
        ),
    )
    observer = diagnostic.PhaseCPrefixObserver(
        SimpleNamespace(coordinator=coordinator), 0.0,
    )
    observer._capture_roots()
    assert observer.same_root_time
    assert observer.same_history_stream
    assert np.array_equal(
        observer.branch["a"]["previous_record_position_m"], a_state.position_m,
    )
    assert np.array_equal(
        observer.branch["b"]["previous_record_position_m"], b_state.position_m,
    )


def test_record_observer_deadline_is_fail_closed_before_core_consume(monkeypatch):
    observer = _observer()
    monkeypatch.setattr(diagnostic.time, "monotonic", lambda: 1_500.0)
    with pytest.raises(diagnostic._Stop, match="INTERNAL_1500S_DEADLINE"):
        observer(SimpleNamespace())
    assert observer.routing_calls == 0


def test_prebootstrap_failure_still_returns_a_fail_closed_result(monkeypatch):
    self_sha = hashlib.sha256(Path(diagnostic.__file__).read_bytes()).hexdigest()
    monkeypatch.setattr(diagnostic, "_source_hashes", lambda: {"source": "same"})
    monkeypatch.setattr(
        diagnostic, "build", lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    result = diagnostic.run(expected_self_sha256=self_sha)
    assert result["status"] == "PHASE_C_PREFIX_MECHANISM_FAIL"
    assert result["authorization"] == "STOP"
    assert result["failure"] == {"type": "RuntimeError", "message": "boom"}
    assert result["gates"]["no_reconstruction_or_transaction_exception"] is False


def test_missing_b_drift_owner_finalization_returns_fail_result(monkeypatch):
    self_sha = hashlib.sha256(Path(diagnostic.__file__).read_bytes()).hexdigest()

    class FakeObserver:
        def __init__(self, owners, _started):
            self.owners = owners
            self.hooks_installed = True
            self.records = 7
            self.initial_drift_owner = object()

        def __call__(self, _ticket):
            return None

    class FakeCoordinator:
        def __init__(self):
            composition = SimpleNamespace(consensus_drift=None)
            self._a = SimpleNamespace(_composition=composition)
            self._b = SimpleNamespace(_composition=composition)
            self.consume_record_ticket = lambda _ticket: None

        def run(self, _reader):
            raise diagnostic._Stop("EXACT_EVENT_CAP")

        def audit(self):
            return SimpleNamespace(events=123)

    owners = SimpleNamespace(coordinator=FakeCoordinator(), reader=object())
    monkeypatch.setattr(diagnostic, "_source_hashes", lambda: {"source": "same"})
    monkeypatch.setattr(diagnostic, "build", lambda: owners)
    monkeypatch.setattr(diagnostic, "PhaseCPrefixObserver", FakeObserver)
    result = diagnostic.run(expected_self_sha256=self_sha)
    assert result["status"] == "PHASE_C_PREFIX_MECHANISM_FAIL"
    assert result["authorization"] == "STOP"
    assert result["stop_reason"] == "B_DRIFT_OWNER_MISSING_OR_REPLACED"
    assert result["boundary"] == {"events": 123, "records": 7, "observed": {}}
    assert result["failure"] == {
        "type": "RuntimeError",
        "message": "B consensus drift owner missing or replaced",
    }


def test_result_writer_is_exclusive_and_enforces_cap(tmp_path, monkeypatch):
    target = tmp_path / "RESULT.json"
    diagnostic._write_new(target, {"ok": True})
    with pytest.raises(FileExistsError):
        diagnostic._write_new(target, {"ok": True})
    monkeypatch.setattr(diagnostic, "MAX_OUTPUT_BYTES", 4)
    with pytest.raises(RuntimeError, match="exceeds 5 MiB"):
        diagnostic._write_new(tmp_path / "TOO_BIG.json", {"large": "payload"})


def test_bounded_wrapper_binds_harness_sha_and_all_resource_gates():
    wrapper = (
        Path(diagnostic.__file__).with_name("run_c2_phase_c_prefix_bounded.sh")
    ).read_text()
    assert "--expected-self-sha256 \"$diagnostic_sha\"" in wrapper
    assert "timeout --foreground --signal=TERM --kill-after=5s 1560" in wrapper
    assert "ulimit -v 1048576" in wrapper
    assert "OPENBLAS_NUM_THREADS=1" in wrapper
    assert "NUMEXPR_NUM_THREADS=1" in wrapper
    assert "bytes <= 5242880" in wrapper
    assert "PROCESS_FINAL.txt" in wrapper
    assert "START_HASHES.txt" in wrapper and "END_HASHES.txt" in wrapper
