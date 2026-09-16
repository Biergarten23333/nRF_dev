from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json
import time
import numpy as np
import pytest

from biospur_fusion.root_r3 import RootState
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    ContinuousEvent, UwbTimer2Fields,
)
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
    ContinuousGroupAudit, StalePoseLinkDiagnostic,
)
from biospur_fusion.c2_articulated_biomechanics.hinge_temporal import (
    ObsoleteNative200SourcePairDiagnostic,
)
from tools import diagnose_c2_full_session_first_divergence as module
import test_c2_continuous_group_epoch_owner as group_fixtures
import test_c2_full_session_ten_node_ab as session_fixtures


def _state(x=0.0, vx=0.0, time_s=1.0):
    return RootState(time_s, np.array([x, 0., 0., vx, 0., 0., 0., 0., 0.]), np.eye(9))


def _drift_decision(*, accepted=True, reason="ACCEPTED_VELOCITY_ONLY",
                    condition=2.0, bias_fit_condition=np.inf):
    return module.DriftCorrectionDecision(
        accepted=accepted, reason=reason, reference_epoch_s=1.0,
        row_count=3 if accepted else 0, rank=3 if accepted else 0,
        condition=condition,
        scaled_singular_values=np.array([3., 2., 1.]) if accepted else np.empty(0),
        velocity_delta_mps=np.array([.2, 0., 0.]) if accepted else np.zeros(3),
        accelerometer_bias_delta_mps2=np.zeros(3),
        node=np.array(["pelvis", "left_ankle", "right_ankle"])
            if accepted else np.empty(0, dtype="U1"),
        anchor=np.array([0, 1, 2], dtype=np.int64)
            if accepted else np.empty(0, dtype=np.int64),
        lag_s=np.array([.1, .2, .3]) if accepted else np.empty(0),
        innovation_difference_m=np.array([.01, .02, .03])
            if accepted else np.empty(0),
        effective_weight=np.ones(3) if accepted else np.empty(0),
        bias_fit_status="NOT_CONFIGURED", bias_fit_condition=bias_fit_condition,
    )


class _Root:
    def __init__(self, state): self.state, self.revision = state, 1
    @property
    def current_state(self): return self.state
    def publication_token(self):
        return SimpleNamespace(state=self.state, revision=self.revision,
                               digest=(f"{self.revision:064x}"))


class _Branch:
    def __init__(self, state):
        root = _Root(state)
        self._composition = SimpleNamespace(engine=SimpleNamespace(root=root),
            history=SimpleNamespace(frames=()), consensus_drift=None)


class _Coordinator:
    def __init__(self, state):
        self._a, self._b = _Branch(state), _Branch(state)
        self._initializer = None; self.events = 0; self.total = 0; self.calls = []
        self.transactions = []
        self.consume_record_ticket = lambda _ticket: None
    def _atomic_ab(self, *args, **kwargs): self.calls.append("SCALAR")
    def _atomic_ab_native200_batch(self, *args, **kwargs): self.calls.append("BATCH")
    def _atomic_ab_gap_native200(self, *args, **kwargs): self.calls.append("GAP_ENDPOINT")
    def audit(self):
        return SimpleNamespace(events=self.events, ab_transaction_total=self.total,
            ab_transaction_journal=tuple(self.transactions))


def _owners(state):
    region = SimpleNamespace(start_offset=0, stop_offset=1000, region_id="03_region")
    return SimpleNamespace(coordinator=_Coordinator(state),
        reader=SimpleNamespace(inventory=SimpleNamespace(regions=(region,))))


def _ticket(count=1):
    return SimpleNamespace(record_ordinal=7, raw_identity=(9, 100, 200, "a"*64),
        event_digests=("b"*64,)*count, sensor_identity_digests=("c"*64,)*count)


def _observer(initial, consume):
    owners = _owners(initial)
    observer = module.FirstBPhysicalCrossingObserver(owners, time.monotonic())
    observer.consume = lambda ticket: consume(observer, owners.coordinator, ticket)
    observer.hooks_installed = True
    observer.anchor_lower_m = np.array([-1., -1., -1.])
    observer.anchor_upper_m = np.array([1., 1., 1.])
    observer._capture_state("b", initial, publication_revision=1,
                            publication_digest=f"{1:064x}")
    return observer, owners.coordinator


def _commit(observer, coordinator, state, route="SCALAR"):
    root = coordinator._b._composition.engine.root
    root.state = state; root.revision += 1; coordinator.events += 1
    observer._active_operation = {"route": route, "order": 1, "events": []}
    observer._capture_state("b", state, publication_revision=root.revision,
                            publication_digest=f"{root.revision:064x}")


@pytest.mark.parametrize("route", ("SCALAR", "BATCH", "GAP_ENDPOINT"))
def test_successful_record_captures_first_crossing_and_route(route):
    def consume(observer, coordinator, _ticket):
        _commit(observer, coordinator, _state(x=1.01, time_s=1.005), route)
    observer, _ = _observer(_state(), consume)
    with pytest.raises(module._Crossing): observer(_ticket())
    assert observer.crossing["causal_operation"]["route"] == route
    assert observer.crossing["record_transaction"]["record_completed"]
    assert observer.crossing["post_policy"]["inside_anchor_envelope"] is False


def test_speed_crossing_and_earliest_publication_only():
    def consume(observer, coordinator, _ticket):
        _commit(observer, coordinator, _state(vx=12.1, time_s=1.005))
        first = observer._staged_crossing["post_publication"]["publication_digest"]
        _commit(observer, coordinator, _state(x=2., vx=13., time_s=1.01))
        assert observer._staged_crossing["post_publication"]["publication_digest"] == first
    observer, _ = _observer(_state(), consume)
    with pytest.raises(module._Crossing): observer(_ticket(2))
    assert observer.crossing["post_policy"]["speed_mps"] == pytest.approx(12.1)


def test_crossing_from_rolled_back_record_is_discarded():
    def consume(observer, coordinator, _ticket):
        before = coordinator._b._composition.engine.root.state
        _commit(observer, coordinator, _state(x=1.1, time_s=1.005))
        coordinator._b._composition.engine.root.state = before
        raise RuntimeError("record rollback")
    observer, coordinator = _observer(_state(), consume)
    with pytest.raises(RuntimeError, match="record rollback"): observer(_ticket())
    assert observer.crossing is None and observer._staged_crossing is None
    assert coordinator._b._composition.engine.root.state.vector.tobytes() == _state().vector.tobytes()


def test_rollback_cursor_restore_allows_reused_revision_digest_crossing():
    attempts = 0
    def consume(observer, coordinator, _ticket):
        nonlocal attempts
        attempts += 1
        root = coordinator._b._composition.engine.root
        base_state, base_revision = root.state, root.revision
        _commit(observer, coordinator, _state(x=1.1, time_s=1.005))
        if attempts == 1:
            root.state, root.revision = base_state, base_revision
            coordinator.events -= 1
            raise RuntimeError("rolled back")
    observer, _ = _observer(_state(), consume)
    with pytest.raises(RuntimeError): observer(_ticket())
    with pytest.raises(module._Crossing): observer(_ticket())
    assert observer.crossing["post_publication"]["publication_revision"] == 2


def test_successful_return_with_rollback_disposition_cannot_seal_crossing():
    def consume(observer, coordinator, _ticket):
        _commit(observer, coordinator, _state(x=1.1, time_s=1.005))
        coordinator.transactions.append({"disposition": "ROLLBACK"})
        coordinator.total += 1
    observer, _ = _observer(_state(), consume)
    with pytest.raises(module._Stop, match="ROLLBACK_DISPOSITION"):
        observer(_ticket())
    assert observer.crossing is None


def test_initial_out_of_policy_and_invalid_state_fail_closed():
    observer, _ = _observer(_state(x=2.), lambda *_: None)
    with pytest.raises(module._Stop, match="INITIAL_B_NOT_IN_POLICY"):
        observer(_ticket())


def test_cap_and_deadline_stop_before_consume():
    called = []
    observer, coordinator = _observer(_state(), lambda *_: called.append(1))
    coordinator.events = module.EVENT_CAP
    with pytest.raises(module._Stop, match="BEFORE_CONSUME"): observer(_ticket())
    assert called == []
    observer.started -= module.INTERNAL_SECONDS + 1
    with pytest.raises(module._Stop, match="DEADLINE"): observer(_ticket())


def test_one_prebootstrap_record_is_consumed_without_owner_assertion():
    owners = _owners(_state()); coordinator = owners.coordinator
    coordinator._a = coordinator._b = None
    observer = module.FirstBPhysicalCrossingObserver(owners, time.monotonic())
    observer.consume = lambda _ticket: setattr(coordinator, "events", coordinator.events + 1)
    observer(_ticket())
    assert observer.records == 1 and observer._active_record is None
    assert observer.crossing is None and observer.invalid_state is None


def test_multiple_prebootstrap_records_remain_clean():
    owners = _owners(_state()); coordinator = owners.coordinator
    coordinator._a = coordinator._b = None
    observer = module.FirstBPhysicalCrossingObserver(owners, time.monotonic())
    observer.consume = lambda _ticket: setattr(coordinator, "events", coordinator.events + 1)
    observer(_ticket()); second = _ticket(); second.record_ordinal = 8; observer(second)
    assert observer.records == 2 and observer._seen_b_publications == set()


def test_asymmetric_bootstrap_owner_presence_fails_closed_with_evidence():
    owners = _owners(_state()); coordinator = owners.coordinator
    coordinator._b = None
    observer = module.FirstBPhysicalCrossingObserver(owners, time.monotonic())
    observer.consume = lambda _ticket: setattr(coordinator, "events", coordinator.events + 1)
    with pytest.raises(module._Stop, match="ASYMMETRIC_BOOTSTRAP"):
        observer(_ticket())
    assert observer.invalid_state == {"reason": "ASYMMETRIC_BOOTSTRAP_OWNER_PRESENCE",
        "a_present": True, "b_present": False, "record_ordinal": 7,
        "events_after": 1}


def test_first_record_installing_both_owners_installs_hooks_and_captures_state():
    owners = _owners(_state()); coordinator = owners.coordinator
    coordinator._a = coordinator._b = None
    real, _frames, _real_ticket = session_fixtures._real_record_coordinator(1)
    group_fixtures._enable_consensus_drift(real._b)
    observer = module.FirstBPhysicalCrossingObserver(owners, time.monotonic())
    observer.anchor_lower_m = np.full(3, -100.)
    observer.anchor_upper_m = np.full(3, 100.)
    def locate(_ticket):
        coordinator._a, coordinator._b = real._a, real._b
        coordinator.events += 1
    observer.consume = locate
    observer(_ticket())
    assert observer.hooks_installed is True
    assert observer._last_b is not None
    assert observer._active_record is None


def test_atomic_route_wrappers_call_original_once_and_restore_context():
    observer, coordinator = _observer(_state(), lambda *_: None)
    event = SimpleNamespace(event_id="e", kind="IMU", node_id="n",
        common_global_ns=1, availability_global_ns=2)
    coordinator._atomic_ab(event)
    coordinator._atomic_ab_native200_batch((event,))
    coordinator._atomic_ab_gap_native200(event, event)
    assert coordinator.calls == ["SCALAR", "BATCH", "GAP_ENDPOINT"]
    assert observer._active_operation is None


def test_result_validator_is_fail_closed_on_schema_and_promotion():
    pre = module._state_doc(_state(), 1, "1"*64)
    post = module._state_doc(_state(x=1.01, time_s=1.005), 2, "2"*64)
    result = {"schema": module.SCHEMA, "status": "FIRST_B_PHYSICAL_CROSSING_CAPTURED",
        "authorization": "STOP", "diagnostic_only": True, "product_ready": False,
        "scientific_pass": False, "fusion_decisions_modified": False, "failure": None,
        "stop_reason": "FIRST_COMMITTED_B_PHYSICAL_CROSSING",
        "observer_failures": [],
        "crossing": {"pre_publication": pre, "post_publication": post,
            "post_policy": {"inside_anchor_envelope": False,
                "speed_mps": 0., "speed_limit_mps": 12.,
                "lower_m": [-1., -1., -1.], "upper_m": [1., 1., 1.]},
            "causal_operation": {"route": "SCALAR", "events": [{"event_id": "e"}]},
            "plan_or_decision": {"operation": "commit_admission"},
            "record_transaction": {"record_completed": True, "rollback_observed": False,
                "raw_identity": [1, 2, 3, "a"*64], "event_digests": ["b"*64],
                "region_id": "03", "pre_ab": {"a": deepcopy(pre), "b": deepcopy(pre)},
                "post_ab": {"a": deepcopy(post), "b": deepcopy(post)}}},
        "anchor_envelope": {"anchors_sha256": "d"*64, "margin_m": 0.75,
            "lower_m": [-1., -1., -1.], "upper_m": [1., 1., 1.]},
        "provenance": {"self_sha256": module._sha(module.Path(module.__file__).resolve()),
            "source_sha256": {"src/x.py": "a"*64}},
        "events": 1, "limits": {"event_cap": module.EVENT_CAP,
            "internal_seconds": module.INTERNAL_SECONDS, "outer_seconds": module.OUTER_SECONDS,
            "rlimit_as_bytes": 1 << 30, "threads": 1,
            "maximum_output_bytes": module.MAX_OUTPUT_BYTES, "retry": False,
            "resume_capable": False}}
    module.validate_result(result)
    for key, value in (("schema", "tampered"), ("product_ready", True),
                       ("scientific_pass", True)):
        changed = deepcopy(result); changed[key] = value
        with pytest.raises(ValueError): module.validate_result(changed)
    for key in ("root_state_canonical_sha256", "covariance_sha256"):
        changed = deepcopy(result); changed["crossing"]["post_publication"][key] = "0" * 64
        with pytest.raises(ValueError): module.validate_result(changed)
    mutations = (
        lambda item: item["anchor_envelope"].__setitem__("margin_m", 0.7),
        lambda item: item["anchor_envelope"].__setitem__("anchors_sha256", "short"),
        lambda item: item["crossing"]["post_policy"].__setitem__("upper_m", [2., 1., 1.]),
        lambda item: item["crossing"]["post_policy"].__setitem__("speed_mps", 1.),
        lambda item: item["crossing"]["post_policy"].__setitem__("speed_limit_mps", 13.),
        lambda item: item["crossing"]["record_transaction"]["pre_ab"]["a"]
            .__setitem__("covariance_sha256", "0" * 64),
        lambda item: item["crossing"]["record_transaction"]["post_ab"]["b"]
            .__setitem__("root_state_canonical_sha256", "0" * 64),
    )
    for mutate in mutations:
        changed = deepcopy(result); mutate(changed)
        with pytest.raises(ValueError): module.validate_result(changed)


def _real_owners(coordinator, ticket):
    coordinator._initializer.preworld_pose_omissions = 0
    region = SimpleNamespace(start_offset=ticket.raw_identity[1] - 1,
        stop_offset=ticket.raw_identity[2] + 1, region_id="03_synthetic")
    return SimpleNamespace(coordinator=coordinator,
        reader=SimpleNamespace(inventory=SimpleNamespace(regions=(region,))))


def test_actual_owner_batch_crossing_nonzero_index_is_inert_and_binds_frames():
    observed, _frames, ticket = session_fixtures._real_record_coordinator(4)
    control, _control_frames, control_ticket = session_fixtures._real_record_coordinator(4)
    group_fixtures._enable_consensus_drift(observed._b)
    group_fixtures._enable_consensus_drift(control._b)
    observer = module.FirstBPhysicalCrossingObserver(
        _real_owners(observed, ticket), time.monotonic(),
    )
    observer.anchor_lower_m = np.array([-100., -100., -100.])
    observer.anchor_upper_m = np.array([0.02165, 100., 100.])
    observer._install_hooks()
    with pytest.raises(module._Crossing): observer(ticket)
    control.consume_record_ticket(control_ticket)

    crossing = observer.crossing
    assert crossing["causal_operation"]["route"] == "BATCH"
    assert crossing["causal_operation"]["sequential_frame_index"] == 2
    assert crossing["previous_native_identity"]["sample_index"] == 1
    assert crossing["triggering_native_identity"]["sample_index"] == 2
    assert crossing["plan_or_decision"]["operation"] == "commit_native200_batch"
    assert len(crossing["plan_or_decision"]["prepared"]["frames"]) == 4
    assert session_fixtures._coordinator_state_bytes(observed) == (
        session_fixtures._coordinator_state_bytes(control)
    )


def test_actual_owner_batch_crossing_complete_evidence_is_deterministic():
    observed_owners = []
    crossing_documents = []
    for _ in range(2):
        coordinator, _frames, ticket = session_fixtures._real_record_coordinator(4)
        group_fixtures._enable_consensus_drift(coordinator._b)
        observer = module.FirstBPhysicalCrossingObserver(
            _real_owners(coordinator, ticket), time.monotonic(),
        )
        observer.anchor_lower_m = np.array([-100., -100., -100.])
        observer.anchor_upper_m = np.array([0.02165, 100., 100.])
        observer._install_hooks()
        with pytest.raises(module._Crossing): observer(ticket)
        observed_owners.append(coordinator)
        crossing_documents.append(module.json.dumps(
            module._jsonable(observer.crossing), sort_keys=True, separators=(",", ":"),
        ))

    control, _frames, control_ticket = session_fixtures._real_record_coordinator(4)
    group_fixtures._enable_consensus_drift(control._b)
    control.consume_record_ticket(control_ticket)
    control_bytes = session_fixtures._coordinator_state_bytes(control)
    assert crossing_documents[0] == crossing_documents[1]
    assert all(session_fixtures._coordinator_state_bytes(owner) == control_bytes
               for owner in observed_owners)


def test_actual_owner_scalar_drift_override_crossing_is_inert_and_bound():
    observed, _frames, ticket = session_fixtures._real_record_coordinator(2, force_scalar=True)
    control, _frames2, control_ticket = session_fixtures._real_record_coordinator(2, force_scalar=True)
    group_fixtures._enable_consensus_drift(observed._b)
    group_fixtures._enable_consensus_drift(control._b)
    observer = module.FirstBPhysicalCrossingObserver(_real_owners(observed, ticket), time.monotonic())
    observer.anchor_lower_m = np.array([-100., -100., -100.])
    observer.anchor_upper_m = np.array([0.02145, 100., 100.])
    observer._install_hooks()
    with pytest.raises(module._Crossing): observer(ticket)
    control.consume_record_ticket(control_ticket)
    crossing = observer.crossing
    assert crossing["causal_operation"]["route"] == "SCALAR"
    assert crossing["triggering_native_identity"] is not None
    assert crossing["plan_or_decision"]["operation"] == "commit_native200"
    assert crossing["plan_or_decision"]["prepared"]["drift_result"] is not None
    assert session_fixtures._coordinator_state_bytes(observed) == session_fixtures._coordinator_state_bytes(control)


def test_actual_owner_gap_endpoint_hook_captures_endpoint_and_suffix_inertly():
    observed, _frames, ticket = session_fixtures._real_gap_record_coordinator()
    control, _frames2, control_ticket = session_fixtures._real_gap_record_coordinator()
    group_fixtures._enable_consensus_drift(observed._b)
    group_fixtures._enable_consensus_drift(control._b)
    observer = module.FirstBPhysicalCrossingObserver(_real_owners(observed, ticket), time.monotonic())
    observer.anchor_lower_m = np.full(3, -1e6); observer.anchor_upper_m = np.full(3, 1e6)
    observer._install_hooks(); before_seen = len(observer._seen_b_publications)
    observer(ticket); control.consume_record_ticket(control_ticket)
    # One compound GAP endpoint plus its nine-frame batch suffix.
    assert len(observer._seen_b_publications) - before_seen == 10
    assert session_fixtures._coordinator_state_bytes(observed) == session_fixtures._coordinator_state_bytes(control)


def test_actual_owner_ordinary_gap_publication_hook_is_inert():
    observed, _rows, _availability = group_fixtures._real_owner_fixture()
    control, _rows2, _availability2 = group_fixtures._real_owner_fixture()
    group_fixtures._enable_consensus_drift(observed)
    group_fixtures._enable_consensus_drift(control)
    coordinator = session_fixtures._coordinator()
    coordinator._a, coordinator._b = group_fixtures._real_owner_fixture()[0], observed
    authority = coordinator._FullSessionTenNodeABCoordinator__group_record_authority
    coordinator._a.bind_native200_record_coordinator(authority); coordinator._b.bind_native200_record_coordinator(authority)
    coordinator._initializer = SimpleNamespace(_frames=(), _preworld_pose_omissions=0,
        preworld_pose_omissions=0, _revision=0, _located=None)
    fake_ticket = SimpleNamespace(raw_identity=(1, 1, 2, "a"*64))
    observer = module.FirstBPhysicalCrossingObserver(_real_owners(coordinator, fake_ticket), time.monotonic())
    observer.anchor_lower_m = np.full(3, -1e6); observer.anchor_upper_m = np.full(3, 1e6)
    observer._install_hooks(); before_seen = len(observer._seen_b_publications)
    gap = group_fixtures._real_gap_event(observed)
    coordinator._atomic_ab(gap, uwb=False)
    assert len(observer._seen_b_publications) == before_seen + 1
    # Observation changes neither the committed mean nor the gap transaction.
    control_plan = control.prepare_continuous_event(group_fixtures._real_gap_event(control), commit_uwb=False)
    control.commit_continuous_event(control_plan)
    assert group_fixtures._composition_fingerprint(observed._composition) == group_fixtures._composition_fingerprint(control._composition)


def _uwb_event(owner, row, availability_ns):
    clock = owner._composition.engine.static.clocks[row.node]
    common_ns = int(round(clock.link_time_ns(
        event_boot_epoch=row.boot, strobe_us=row.strobe_us, t_round_us=0.0,
    )))
    return ContinuousEvent(
        f"localizer-uwb:{row.node}", "UWB", 0, "00_initial_still",
        common_ns, availability_ns, row.node, row.boot, "B306_TIMER2",
        "a"*64, "b"*64, "c"*64, "host-only", row,
        uwb_timer2=UwbTimer2Fields(row.strobe_us, row.frame_us),
    )


def test_actual_owner_accepted_uwb_admission_crossing_binds_observation():
    def setup():
        coordinator = session_fixtures._coordinator()
        coordinator._a, _a_rows, _ = group_fixtures._real_owner_fixture()
        coordinator._b, rows, availability = group_fixtures._real_owner_fixture()
        authority = coordinator._FullSessionTenNodeABCoordinator__group_record_authority
        coordinator._a.bind_native200_record_coordinator(authority)
        coordinator._b.bind_native200_record_coordinator(authority)
        coordinator._initializer = SimpleNamespace(_frames=(), _preworld_pose_omissions=0,
            preworld_pose_omissions=0, _revision=0, _located=None)
        group_fixtures._enable_consensus_drift(coordinator._b)
        return coordinator, rows, availability
    coordinator, rows, availability = setup()
    control, control_rows, control_availability = setup()
    fake_ticket = SimpleNamespace(raw_identity=(1, 1, 2, "a"*64))
    observer = module.FirstBPhysicalCrossingObserver(
        _real_owners(coordinator, fake_ticket), time.monotonic(),
    )
    observer.anchor_lower_m = np.array([-100., -100., -100.])
    observer.anchor_upper_m = np.array([0.0214, 100., 100.])
    observer._install_hooks()
    observer._active_record = {"previous_b_native_identity":
        module._frame_doc(coordinator._b._composition.history.frames[-1])}
    for row in sorted(rows, key=lambda item: item.node):
        coordinator._atomic_ab(_uwb_event(coordinator._b, row, availability), uwb=True)
    for row in sorted(control_rows, key=lambda item: item.node):
        control._atomic_ab(_uwb_event(control._b, row, control_availability), uwb=True)
    crossing = observer._staged_crossing
    assert crossing is not None
    assert crossing["plan_or_decision"]["operation"] == "commit_admission"
    prepared = crossing["plan_or_decision"]["prepared"]
    assert prepared["root_observation"] is not None
    assert prepared["root_plan_digest"] is not None
    assert len(prepared["trusted_partition"]) == 10
    assert group_fixtures._composition_fingerprint(coordinator._b._composition) == (
        group_fixtures._composition_fingerprint(control._b._composition)
    )


def test_postbootstrap_invalid_state_is_staged_and_fails_after_record():
    bad_covariance = np.eye(9); bad_covariance[0, 0] = -1.0
    bad = object.__new__(RootState)
    object.__setattr__(bad, "time_s", 1.005)
    object.__setattr__(bad, "vector", np.zeros(9))
    object.__setattr__(bad, "covariance", bad_covariance)
    def consume(observer, coordinator, _ticket):
        root = coordinator._b._composition.engine.root
        root.state = bad; root.revision += 1; coordinator.events += 1
        observer._capture_state("b", bad, publication_revision=root.revision,
                                publication_digest=f"{root.revision:064x}")
    observer, _ = _observer(_state(), consume)
    with pytest.raises(module._Stop, match="INVALID_ROOTSTATE_BEFORE_CROSSING"):
        observer(_ticket())
    assert observer.invalid_state["validation"]["valid"] is False


def test_crossing_evidence_is_deterministic_for_identical_committed_records():
    documents = []
    for _ in range(2):
        def consume(observer, coordinator, _ticket):
            _commit(observer, coordinator, _state(x=1.01, time_s=1.005), "SCALAR")
        observer, _coordinator = _observer(_state(), consume)
        with pytest.raises(module._Crossing): observer(_ticket())
        documents.append(module.json.dumps(module._jsonable(observer.crossing),
                                           sort_keys=True, separators=(",", ":")))
    assert documents[0] == documents[1]


def _followup(monkeypatch):
    owners = _owners(_state())
    observer = module.CrossingUwbResponseObserver(owners, time.monotonic())
    observer.hooks_installed = True
    observer.anchor_lower_m = np.array([-1., -1., -1.])
    observer.anchor_upper_m = np.array([1., 1., 1.])
    observer.crossing = {"sealed": True}
    digest = module.hashlib.sha256(module.json.dumps(
        observer.crossing, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    monkeypatch.setattr(module, "SEALED_CROSSING_DIGEST", digest)
    return observer, owners.coordinator


def _followup_record(ordinal=36816, *, accepted=True):
    return {"record_ordinal": ordinal, "raw_identity": [1, 2, 3, "a"*64],
        "record_ab_transactions": [{"branch": "B_UWB",
            "prepared_accepted": accepted,
            "prepared_reason": "ACCEPTED" if accepted else "REJECTED"}]}


def _effect(operation, *, consumed=None, availability_ns=1_010_000_000,
            frame_ns=1_010_000_000):
    pre = module._state_doc(_state(time_s=1.), 1, "1"*64)
    post_state = (_state(x=.1, time_s=1.) if operation == "commit_admission"
                  else _state(vx=.2, time_s=frame_ns * 1e-9))
    post = module._state_doc(post_state, 2, "2"*64)
    package = {"digest": "d"*64, "availability_time_ns": availability_ns}
    prepared = {"frame": {"source_global_ns": frame_ns},
        "drift_result": {"consumed_observation_digest": consumed,
            "accepted": consumed is not None, "reason": "TEST"},
        "imu_candidate": pre, "final_root_candidate": post}
    return {"operation": operation, "prepared": prepared, "result": {},
        "pre_root": pre, "post_root": post, "pre_drift": {"pending": []},
        "post_drift": {"pending": [package]}}


def test_followup_accepted_queue_delayed_availability_then_exact_consume(monkeypatch):
    observer, _ = _followup(monkeypatch)
    observer._record_effects = [_effect("commit_admission"),
        _effect("commit_consensus_admission")]
    observer._after_successful_record(_followup_record())
    assert observer.admission["velocity_and_bias_byte_inert"]
    assert observer.queue["package"]["digest"] == "d"*64

    observer._record_effects = [_effect("commit_native200", consumed=None,
        frame_ns=1_005_000_000)]
    observer._after_successful_record(_followup_record(36817))
    assert observer.native_response is None

    observer._record_effects = [_effect("commit_native200", consumed="d"*64,
        frame_ns=1_010_000_000)]
    with pytest.raises(module._FollowupComplete):
        observer._after_successful_record(_followup_record(36818))
    assert observer.native_response["eligible_by_integer_time"]
    assert observer.native_response["position_bias_covariance_byte_inert"]


def test_followup_first_eligible_none_stops_before_later_consume(monkeypatch):
    observer, _ = _followup(monkeypatch)
    observer._record_effects = [_effect("commit_admission"),
        _effect("commit_consensus_admission")]
    observer._after_successful_record(_followup_record())
    observer._record_effects = [_effect("commit_native200", consumed=None,
        frame_ns=1_010_000_000)]
    with pytest.raises(module._Stop, match="DID_NOT_CONSUME"):
        observer._after_successful_record(_followup_record(36817))
    assert observer.native_response["first_eligible_native"]["frame"]["source_global_ns"] == 1_010_000_000
    assert observer.native_response["consumed_observation_digest"] is None


def test_followup_batch_bypass_fails_at_first_eligible_frame(monkeypatch):
    observer, _ = _followup(monkeypatch)
    observer._record_effects = [_effect("commit_admission"),
        _effect("commit_consensus_admission")]
    observer._after_successful_record(_followup_record())
    effect = _effect("commit_native200_batch", consumed=None)
    effect["prepared"]["frames"] = [
        {"source_global_ns": 1_005_000_000, "digest": "1"*64},
        {"source_global_ns": 1_010_000_000, "digest": "2"*64},
        {"source_global_ns": 1_015_000_000, "digest": "3"*64},
    ]
    observer._record_effects = [effect]
    with pytest.raises(module._Stop, match="DID_NOT_CONSUME"):
        observer._after_successful_record(_followup_record(36817))
    assert len(observer.native_observation_order) == 2
    assert observer.native_response["first_eligible_native"]["frame_index"] == 1


@pytest.mark.parametrize("consumed,reason", (
    (None, "DID_NOT_CONSUME"), ("e"*64, "CONSUMED_WRONG_PACKAGE"),
))
def test_followup_call_preserves_committed_first_eligible_violation_evidence(
        monkeypatch, consumed, reason):
    observer, _ = _followup(monkeypatch)
    observer.admission = {"audit": {"branch": "B_UWB", "prepared_accepted": True}}
    observer.queue = {"package": {"digest": "d"*64,
        "availability_time_ns": 1_010_000_000}}
    observer.consume = lambda _ticket: observer._record_effects.append(
        _effect("commit_native200", consumed=consumed, frame_ns=1_010_000_000))
    with pytest.raises(module._FollowupViolation, match=reason):
        observer(_ticket())
    assert observer.native_response is not None
    assert observer.native_response["record"]["record_completed"] is True
    assert observer.native_observation_order[-1]["eligible"] is True


def test_followup_call_preserves_successful_exact_consume_evidence(monkeypatch):
    observer, _ = _followup(monkeypatch)
    observer.admission = {"audit": {"branch": "B_UWB", "prepared_accepted": True}}
    observer.queue = {"package": {"digest": "d"*64,
        "availability_time_ns": 1_010_000_000}}
    observer.consume = lambda _ticket: observer._record_effects.append(
        _effect("commit_native200", consumed="d"*64, frame_ns=1_010_000_000))
    with pytest.raises(module._FollowupComplete, match="CONSUMED_QUEUED_PACKAGE"):
        observer(_ticket())
    assert observer.native_response["consumed_observation_digest"] == "d"*64


def test_followup_rejected_admission_has_no_package_or_native_consume(monkeypatch):
    observer, _ = _followup(monkeypatch)
    observer._record_effects = []
    with pytest.raises(module._FollowupComplete):
        observer._after_successful_record(_followup_record(accepted=False))
    assert observer.queue is None
    assert observer.native_response == {"not_applicable": True, "reason": "REJECTED"}


def test_followup_rollback_restores_staged_response(monkeypatch):
    observer, _ = _followup(monkeypatch)
    def fail(_ticket):
        observer.admission = {"transient": True}
        raise RuntimeError("rollback")
    observer.consume = fail
    with pytest.raises(RuntimeError, match="rollback"):
        observer(_ticket())
    assert observer.admission is None and observer.queue is None
    assert observer.native_response is None


def test_followup_record_cap_stops_before_consume(monkeypatch):
    observer, _ = _followup(monkeypatch); called = []
    observer.consume = lambda _ticket: called.append(1)
    ticket = _ticket(); ticket.record_ordinal = module.FOLLOWUP_RECORD_CAP_EXCLUSIVE
    with pytest.raises(module._Stop, match="RECORD_CAP_BEFORE_CONSUME"):
        observer(ticket)
    assert called == []


def _pending_row(node, event_id):
    return SimpleNamespace(row=SimpleNamespace(node=node),
                           event=SimpleNamespace(event_id=event_id))


def _group_owner(*, revision, pending, journal=(), watermark=1_958_008):
    owner = SimpleNamespace(_revision=revision, _pending={
        bucket: tuple(_pending_row(node, f"{bucket}:{node}") for node in nodes)
        for bucket, nodes in pending.items()}, _finalized_watermark=watermark,
        journal=tuple(journal), counters={})
    return owner


def _target_record(ordinal=36_833, *, transactions=()):
    expected = module.TARGET_COMPLETE_BUCKET_RECORDS[ordinal]
    return {"record_ordinal": ordinal,
        "raw_identity": list(expected["raw_identity"]),
        "event_digests": [expected["event_digest"]],
        "region_id": "03_pelvis_hula_circle",
        "record_ab_transactions": list(transactions),
        "record_completed": True, "rollback_observed": False}


def _group_visibility_observer(monkeypatch, owner):
    observer, coordinator = _followup(monkeypatch)
    owner._composition = coordinator._b._composition
    coordinator._b = owner
    return observer


def test_group_visibility_explains_older_bucket_blocking(monkeypatch):
    nodes = [f"N{i}" for i in range(10)]
    before = _group_owner(revision=10,
        pending={1_958_008: nodes[:3], 1_958_009: nodes[:9]})
    after = _group_owner(revision=11,
        pending={1_958_008: nodes[:3], 1_958_009: nodes})
    observer = _group_visibility_observer(monkeypatch, after)
    observer._group_before = module._group_owner_state(before)
    with pytest.raises(module._GroupDispositionComplete):
        observer._capture_target_group_visibility(_target_record())
    evidence = observer.decisive_group_disposition
    assert evidence["reason"] == "BLOCKED_BY_OLDER_PENDING_BUCKET"
    assert evidence["attempted_bucket"] is None
    assert evidence["after"]["pending"] == [
        {"bucket": 1_958_008, "row_count": 3, "nodes": nodes[:3],
         "event_ids": [f"1958008:N{i}" for i in range(3)]},
        {"bucket": 1_958_009, "row_count": 10, "nodes": nodes,
         "event_ids": [f"1958009:N{i}" for i in range(10)]}]


@pytest.mark.parametrize("reason,diagnostic_field,terminal", (
    ("STALE_POSE_LINK_DEFERRED", "stale_pose_link", False),
    ("STALE_POSE_LINK_REJECTED", "stale_pose_link", True),
    ("OBSOLETE_NATIVE200_SOURCE_PAIR", "obsolete_native200_source_pair", True),
))
def test_group_visibility_seals_pre_admission_disposition(
        monkeypatch, reason, diagnostic_field, terminal):
    diagnostic = (StalePoseLinkDiagnostic(10_000_000, 0, 10_000_000, terminal)
                  if diagnostic_field == "stale_pose_link" else
                  ObsoleteNative200SourcePairDiagnostic(1, 1, 2, 2, 0))
    kwargs = {diagnostic_field: diagnostic}
    audit = ContinuousGroupAudit(
        1_958_009, reason, tuple(f"N{i}" for i in range(10)),
        ("03_pelvis_hula_circle",) * 10, "ACTION_EVIDENCE", **kwargs)
    before = _group_owner(revision=10,
        pending={1_958_009: [f"N{i}" for i in range(9)]})
    after_pending = ({1_958_009: [f"N{i}" for i in range(10)]}
                     if not terminal else {})
    after = _group_owner(revision=11, pending=after_pending, journal=(audit,),
                         watermark=1_958_008 if not terminal else 1_958_009)
    observer = _group_visibility_observer(monkeypatch, after)
    observer._group_before = module._group_owner_state(before)
    with pytest.raises(module._GroupDispositionComplete):
        observer._capture_target_group_visibility(_target_record())
    evidence = observer.decisive_group_disposition
    assert evidence["reason"] == reason
    assert evidence["terminal"] is terminal
    assert evidence[diagnostic_field] == module._project(diagnostic)


def test_group_visibility_admission_path_is_control_not_preadmission_stop(monkeypatch):
    audit = ContinuousGroupAudit(
        1_958_009, "PREPARED_COMPLETE_GROUP", tuple(f"N{i}" for i in range(10)),
        ("03_pelvis_hula_circle",) * 10, "ACTION_EVIDENCE")
    before = _group_owner(revision=10,
        pending={1_958_009: [f"N{i}" for i in range(9)]})
    after = _group_owner(revision=11, pending={}, journal=(audit,),
                         watermark=1_958_009)
    observer = _group_visibility_observer(monkeypatch, after)
    observer._group_before = module._group_owner_state(before)
    observer._capture_target_group_visibility(_target_record(transactions=({
        "b": {"reason": "PREPARED_ADMISSION", "admission": {
            "bucket": 1_958_009, "branch": "B_UWB"}}},)))
    assert observer.decisive_group_disposition is None
    assert observer.group_visibility[-1]["attempted_bucket"] == 1_958_009


def test_group_visibility_rolls_back_and_target_identity_is_exact(monkeypatch):
    owner = _group_owner(revision=11, pending={})
    observer = _group_visibility_observer(monkeypatch, owner)
    observer.group_visibility = [{"stable": True}]
    observer.consume = lambda _ticket: (_ for _ in ()).throw(RuntimeError("rollback"))
    with pytest.raises(RuntimeError, match="rollback"):
        observer(_ticket())
    assert observer.group_visibility == [{"stable": True}]
    observer._group_before = module._group_owner_state(owner)
    record = _target_record(); record["raw_identity"][0] += 1
    with pytest.raises(module._Stop, match="IDENTITY_MISMATCH"):
        observer._capture_target_group_visibility(record)


def _valid_group_disposition_result(monkeypatch):
    diagnostic = StalePoseLinkDiagnostic(10_000_000, 0, 10_000_000, False)
    audit = ContinuousGroupAudit(
        1_958_009, "STALE_POSE_LINK_DEFERRED",
        tuple(f"N{i}" for i in range(10)),
        ("03_pelvis_hula_circle",) * 10, "ACTION_EVIDENCE",
        stale_pose_link=diagnostic)
    before = _group_owner(revision=10,
        pending={1_958_009: [f"N{i}" for i in range(9)]})
    after = _group_owner(revision=11,
        pending={1_958_009: [f"N{i}" for i in range(10)]}, journal=(audit,))
    observer = _group_visibility_observer(monkeypatch, after)
    observer._group_before = module._group_owner_state(before)
    with pytest.raises(module._GroupDispositionComplete):
        observer._capture_target_group_visibility(_target_record())
    crossing = {"sealed": True}
    sealed = module._canonical_digest(crossing)
    monkeypatch.setattr(module, "SEALED_CROSSING_DIGEST", sealed)
    limits = {"event_cap": module.FOLLOWUP_EVENT_CAP,
        "internal_seconds": module.INTERNAL_SECONDS,
        "outer_seconds": module.OUTER_SECONDS, "rlimit_as_bytes": 1 << 30,
        "threads": 1, "maximum_output_bytes": module.MAX_OUTPUT_BYTES,
        "retry": False, "resume_capable": False,
        "record_cap_exclusive": module.FOLLOWUP_RECORD_CAP_EXCLUSIVE,
        "cap_basis": {"sealed_crossing_record_ordinal": 36_815,
            "sealed_crossing_events_after": 270_708,
            "following_complete_record_boundaries": 64,
            "maximum_events_per_record": 16}}
    return {"schema": module.FOLLOWUP_SCHEMA,
        "status": "FIRST_B_GROUP_DISPOSITION_CAPTURED", "authorization": "STOP",
        "diagnostic_only": True, "product_ready": False,
        "scientific_pass": False, "fusion_decisions_modified": False,
        "failure": None,
        "stop_reason": "FIRST_COMPLETE_BUCKET_PRE_ADMISSION_DISPOSITION",
        "sealed_crossing_digest": sealed, "crossing": crossing, "limits": limits,
        "events": 270_825, "records": 36_834, "observer_failures": [],
        "b_group_visibility": observer.group_visibility,
        "decisive_b_group_disposition": observer.decisive_group_disposition}


def _reseal_group_evidence(result):
    evidence = result["decisive_b_group_disposition"]
    evidence["evidence_sha256"] = module._canonical_digest({
        key: value for key, value in evidence.items() if key != "evidence_sha256"})
    result["b_group_visibility"][-1] = evidence


def test_group_disposition_validator_accepts_fully_bound_evidence(monkeypatch):
    module.validate_followup_result(_valid_group_disposition_result(monkeypatch))


@pytest.mark.parametrize("category", (
    "crossing", "limits", "counts", "observer", "raw", "record_completion",
    "state_sha", "pending_cardinality", "revision", "journal_delta", "reason",
    "flags", "diagnostic", "evidence_sha",
))
def test_group_disposition_validator_rejects_each_tamper(monkeypatch, category):
    result = deepcopy(_valid_group_disposition_result(monkeypatch))
    evidence = result["decisive_b_group_disposition"]
    if category == "crossing": result["crossing"] = {"sealed": False}
    elif category == "limits": result["limits"]["event_cap"] += 1
    elif category == "counts": result["events"] += 1
    elif category == "observer": result["observer_failures"] = ["failure"]
    elif category == "raw": evidence["record_identity"]["raw_identity"][0] += 1
    elif category == "record_completion": evidence["record_identity"]["record_completed"] = False
    elif category == "state_sha": evidence["before"]["state_sha256"] = "0" * 64
    elif category == "pending_cardinality":
        evidence["after"]["pending"][0]["row_count"] = 9
        body = {key: value for key, value in evidence["after"].items()
                if key != "state_sha256"}
        evidence["after"]["state_sha256"] = module._canonical_digest(body)
    elif category == "revision":
        evidence["after"]["revision"] += 1
        body = {key: value for key, value in evidence["after"].items()
                if key != "state_sha256"}
        evidence["after"]["state_sha256"] = module._canonical_digest(body)
    elif category == "journal_delta": evidence["journal_delta"] = []
    elif category == "reason": evidence["reason"] = "BLOCKED_BY_OLDER_PENDING_BUCKET"
    elif category == "flags": evidence["nonterminal"] = False
    elif category == "diagnostic": evidence["stale_pose_link"] = None
    elif category == "evidence_sha": evidence["evidence_sha256"] = "0" * 64
    if category != "evidence_sha": _reseal_group_evidence(result)
    with pytest.raises(ValueError, match="invalid B group-disposition evidence"):
        module.validate_followup_result(result)


def _valid_fixed_target_result():
    target = module.TARGET_COMPLETE_BUCKET_RECORDS[36_833]
    packet_digest, epoch_digest, admission_digest = "1"*64, "2"*64, "3"*64
    root_plan_digest = "4"*64
    trusted = tuple(f"N{i}" for i in range(10))
    before_owner = _group_owner(revision=10,
        pending={1_958_009: [f"N{i}" for i in range(9)]})
    group_audit = ContinuousGroupAudit(
        1_958_009, "PREPARED_COMPLETE_GROUP", tuple(f"N{i}" for i in range(10)),
        ("03_pelvis_hula_circle",) * 10, "ACTION_EVIDENCE",
        packet_digest, epoch_digest, admission_digest)
    after_owner = _group_owner(revision=11, pending={}, journal=(group_audit,),
                               watermark=1_958_009)
    before = module._group_owner_state(before_owner)
    after = module._group_owner_state(after_owner)
    group = {"target_bucket": 1_958_009, "record_identity": {
        "record_ordinal": 36_833, "raw_identity": list(target["raw_identity"]),
        "event_digest": target["event_digest"], "region_id": "03_pelvis_hula_circle",
        "record_completed": True, "rollback_observed": False},
        "before": before, "after": after,
        "journal_delta": [module._project(group_audit)],
        "paired_b_dispositions": [], "attempted_bucket": 1_958_009,
        "reason": "PREPARED_COMPLETE_GROUP", "terminal": False,
        "nonterminal": False, "stale_pose_link": None,
        "obsolete_native200_source_pair": None}
    group["evidence_sha256"] = module._canonical_digest(group)
    state0 = module._state_doc(_state(x=0., time_s=1.), 1, "1"*64)
    state1 = module._state_doc(_state(x=.1, time_s=1.), 2, "2"*64)
    def readonly(value):
        array = np.asarray(value, dtype=float).copy()
        array.setflags(write=False)
        return array

    observation = module.PositionObservation(
        1., 1.01, readonly([.1, 0., 0.]), readonly(np.eye(3) * .1),
        "ROOT", tuple(range(8)), source_sequence=1,
    )
    a_audit = {"bucket": 1_958_009, "branch": "A_BASELINE",
        "prepared_accepted": True,
        "prepared_reason": "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR",
        "outcome": "BASELINE_NO_UWB_COMMIT", "packet_digest": packet_digest,
        "epoch_digest": epoch_digest, "candidate_digest": admission_digest,
        "source_sequence": 1, "source_identity": ["ROOT", 0, 1, 2],
        "commit_intent": False, "commit_attempted": False,
        "commit_succeeded": False, "diagnostic": None,
        "diagnostic_digest": None, "trusted_partition": list(trusted)}
    b_audit = {"bucket": 1_958_009, "branch": "B_UWB",
        "prepared_accepted": True,
        "prepared_reason": "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR",
        "outcome": "UWB_COMMIT_SUCCEEDED", "packet_digest": packet_digest,
        "epoch_digest": epoch_digest, "candidate_digest": admission_digest,
        "source_sequence": 1, "source_identity": ["ROOT", 0, 1, 2],
        "commit_intent": True, "commit_attempted": True,
        "commit_succeeded": True, "diagnostic": None,
        "diagnostic_digest": None, "trusted_partition": list(trusted)}
    record = {"record_ordinal": 36_833, "raw_identity": list(target["raw_identity"]),
        "events_after": target["events_after"], "event_digests": [target["event_digest"]],
        "record_completed": True, "rollback_observed": False,
        "record_ab_transactions": [{"a": {"admission": a_audit},
                                     "b": {"admission": b_audit}}]}
    admission = {"record": record, "audit": b_audit,
        "operation": "commit_admission", "pre_root": state0, "post_root": state1,
        "prepared": {"root_observation": module._project(observation),
                     "root_plan_digest": root_plan_digest,
                     "packet_digest": packet_digest,
                     "packet_availability_global_ns": 1_010_000_000,
                     "trusted_partition": list(trusted)},
        "velocity_and_bias_byte_inert": True,
        "applied_position_delta_m": [.1, 0., 0.]}
    blank_package = module.ContinuousConsensusObservation(
        observation, 1_010_000_000, trusted, tuple(range(8)), packet_digest,
        epoch_digest, root_plan_digest, admission_digest,
        readonly([.1, 0., 0.]), readonly([.1, 0., 0.]),
        readonly([.1, 0., 0.]), "",
    )
    package_object = replace(
        blank_package, digest=module._package_digest(blank_package),
    )
    package = module._project(package_object)
    eligible = {"record_ordinal": 36_834, "operation": "commit_native200",
        "frame_index": 0, "frame": {"source_global_ns": 1_010_000_000},
        "availability_time_ns": 1_010_000_000, "eligible": True}
    response = {"record": {"record_completed": True, "rollback_observed": False},
        "prepared": {"drift_result": {
            "consumed_observation_digest": package["digest"],
            "decision": module._project(_drift_decision())}},
        "consumed_observation_digest": package["digest"], "eligible_by_integer_time": True,
        "first_eligible_native": eligible,
        "position_bias_covariance_byte_inert": True,
        "velocity_delta_mps": [.2, 0., 0.]}
    crossing = {"pre_publication": state0,
        "post_publication": module._state_doc(_state(x=1.1, time_s=1.02), 3, "3"*64),
        "post_policy": {"inside_anchor_envelope": False, "speed_mps": 0.,
                        "speed_limit_mps": 12., "lower_m": [-1., -1., -1.],
                        "upper_m": [1., 1., 1.]},
        "record_transaction": {"record_ordinal": 36_844}}
    checkpoint = {"counterfactual_crossing_sha256": module.SEALED_CROSSING_DIGEST,
        "record_identity": {"record_ordinal": 36_815,
            "raw_identity": list(module.OLD_CROSSING_RECORD["raw_identity"]),
            "event_digests": ["e"*64], "events_after": 270_708,
            "record_completed": True, "rollback_observed": False},
        "post_b": state0, "policy": {"inside_anchor_envelope": True,
            "speed_mps": 0., "speed_limit_mps": 12.,
            "lower_m": [-1., -1., -1.], "upper_m": [1., 1., 1.]}}
    limits = {"event_cap": module.FOLLOWUP_EVENT_CAP,
        "internal_seconds": module.INTERNAL_SECONDS, "outer_seconds": module.OUTER_SECONDS,
        "rlimit_as_bytes": 1 << 30, "threads": 1,
        "maximum_output_bytes": module.MAX_OUTPUT_BYTES, "retry": False,
        "resume_capable": False, "record_cap_exclusive": module.FOLLOWUP_RECORD_CAP_EXCLUSIVE,
        "cap_basis": {"sealed_crossing_record_ordinal": 36_815,
            "sealed_crossing_events_after": 270_708,
            "following_complete_record_boundaries": 64,
            "maximum_events_per_record": 16}}
    return {"schema": module.FIXED_TARGET_SCHEMA,
        "status": "FIXED_TARGET_CHAIN_CAPTURED", "authorization": "STOP",
        "diagnostic_only": True, "product_ready": False, "scientific_pass": False,
        "fusion_decisions_modified": False, "failure": None,
        "stop_reason": "TARGET_CHAIN_RESOLVED_WITH_LATER_PHYSICAL_CROSSING",
        "limits": limits, "events": 270_920, "records": 36_846,
        "observer_failures": [], "sealed_crossing_digest": module.SEALED_CROSSING_DIGEST,
        "anchor_envelope": {"anchors_sha256": "5"*64,
            "lower_m": [-1., -1., -1.], "upper_m": [1., 1., 1.],
            "margin_m": .75},
        "old_crossing_checkpoint": checkpoint, "target_record": target,
        "b_group_visibility": [group], "first_subsequent_b_admission": admission,
        "queued_drift_package": {"package": package},
        "first_eligible_native_response": response,
        "native_observation_order": [eligible],
        "target_chain_resolution": "FIRST_ELIGIBLE_NATIVE_CONSUMED_QUEUED_PACKAGE",
        "crossing": crossing}


def test_drift_decision_projection_preserves_real_array_types_and_audit_infinity():
    projected = module._project(_drift_decision())
    assert projected["node"]["dtype"].startswith("<U")
    assert projected["node"]["values"] == ["pelvis", "left_ankle", "right_ankle"]
    assert projected["anchor"]["dtype"] == "<i8"
    assert projected["condition"] == 2.0
    assert projected["bias_fit_condition"] == {
        "schema": module.AUDIT_NONFINITE_SCHEMA,
        "owner_type": "DriftCorrectionDecision",
        "field": "bias_fit_condition", "value": "POSITIVE_INFINITY"}
    assert module._validate_drift_decision_projection(projected)
    json.dumps(projected, allow_nan=False)


def test_drift_decision_projection_roundtrips_rank_zero_warmup(tmp_path):
    projected = module._project(_drift_decision(
        accepted=False, reason="INSUFFICIENT_HISTORY", condition=np.inf))
    for field in ("condition", "bias_fit_condition"):
        assert projected[field]["value"] == "POSITIVE_INFINITY"
    path = tmp_path / "warmup.json"
    module._write(path, {"decision": projected})
    loaded = json.loads(path.read_text())
    assert loaded == {"decision": projected}
    assert module._validate_drift_decision_projection(loaded["decision"])


def test_drift_decision_projection_roundtrips_accepted_bias_disabled(tmp_path):
    projected = module._project(_drift_decision())
    path = tmp_path / "accepted.json"
    module._write(path, {"decision": projected, "finite_evidence": [0., 1.]})
    loaded = json.loads(path.read_text())
    assert loaded["decision"] == projected
    assert loaded["finite_evidence"] == [0., 1.]
    assert module._validate_drift_decision_projection(loaded["decision"])


@pytest.mark.parametrize("field", ("condition", "bias_fit_condition"))
def test_drift_decision_projection_rejects_nan_audit(field):
    kwargs = {field: np.nan}
    with pytest.raises(ValueError, match="audit is NaN"):
        module._project(_drift_decision(**kwargs))


def test_drift_decision_projection_preserves_negative_sign_but_write_rejects(tmp_path):
    projected = module._project(_drift_decision(condition=-np.inf))
    assert projected["condition"]["value"] == "NEGATIVE_INFINITY"
    assert not module._validate_drift_decision_projection(projected)
    with pytest.raises(ValueError, match="invalid audit nonfinite marker"):
        module._write(tmp_path / "negative.json", {"decision": projected})


def test_drift_decision_validator_rejects_accepted_rank_three_infinite_condition():
    projected = module._project(_drift_decision(condition=np.inf))
    assert projected["accepted"] is True and projected["rank"] == 3
    assert projected["condition"]["value"] == "POSITIVE_INFINITY"
    assert not module._validate_drift_decision_projection(projected)


def test_drift_decision_validator_rejects_accepted_bias_fit_infinite_condition():
    decision = replace(_drift_decision(), bias_fit_status="BIAS_FIT_ACCEPTED",
                       bias_fit_condition=np.inf)
    projected = module._project(decision)
    assert projected["bias_fit_condition"]["value"] == "POSITIVE_INFINITY"
    assert not module._validate_drift_decision_projection(projected)


@pytest.mark.parametrize("value", (np.inf, -np.inf, np.nan))
def test_unexpected_nonfinite_scientific_values_fail_closed(tmp_path, value):
    with pytest.raises(ValueError, match="nonfinite ndarray"):
        module._project(np.array([value]))
    with pytest.raises(ValueError, match="nonfinite scientific value"):
        module._write(tmp_path / "bad.json", {"root_state": {"vector": [value]}})


def test_drift_audit_marker_is_rejected_outside_exact_typed_field(tmp_path):
    marker = module._audit_nonfinite("condition", np.inf)
    with pytest.raises(ValueError, match="unbound audit nonfinite marker"):
        module._write(tmp_path / "unbound.json", {"condition": marker})
    decision = module._project(_drift_decision())
    decision["condition"] = "POSITIVE_INFINITY"
    with pytest.raises(ValueError, match="invalid drift audit scalar"):
        module._write(tmp_path / "string.json", {"decision": decision})


def test_fixed_target_validator_accepts_symbolic_drift_audit_roundtrip(tmp_path):
    result = _valid_fixed_target_result()
    path = tmp_path / "fixed.json"
    module._write(path, result)
    loaded = json.loads(path.read_text())
    module.validate_fixed_target_result(loaded)


def test_fixed_target_validator_accepts_complete_causal_chain():
    result = json.loads(json.dumps(_valid_fixed_target_result()))
    module.validate_fixed_target_result(result)


def test_fixed_target_validator_accepts_explicit_downstream_rejection():
    result = _valid_fixed_target_result()
    audit = result["first_subsequent_b_admission"]["audit"]
    audit.update({"prepared_accepted": False, "prepared_reason": "ROBUST_REJECTED",
                  "outcome": "UWB_REJECTED"})
    result["first_subsequent_b_admission"] = {
        "record": result["first_subsequent_b_admission"]["record"],
        "audit": audit, "operation": "NO_COMMIT_ADMISSION"}
    result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["b"]["admission"] = audit
    result.update({"status": "FIXED_TARGET_DOWNSTREAM_REJECTION_CAPTURED",
        "stop_reason": "TARGET_B_ADMISSION_SCIENTIFICALLY_REJECTED",
        "target_chain_resolution": "FIRST_SUBSEQUENT_B_UWB_REJECTED",
        "queued_drift_package": None,
        "first_eligible_native_response": {"not_applicable": True,
                                            "reason": "ROBUST_REJECTED"},
        "native_observation_order": [], "crossing": None})
    module.validate_fixed_target_result(result)


def test_fixed_target_validator_accepts_terminal_a_baseline_optimizer_rejection():
    result = _valid_fixed_target_result()
    a_audit = result["first_subsequent_b_admission"]["record"][
        "record_ab_transactions"][0]["a"]["admission"]
    a_audit["prepared_accepted"] = False
    a_audit["prepared_reason"] = "OPTIMIZER_FAILURE"
    a_audit["diagnostic"] = {"solver_reason": "OPTIMIZER_FAILURE", "nfev": 50}
    a_audit["diagnostic_digest"] = module._digest_payload(a_audit["diagnostic"])
    module.validate_fixed_target_result(result)


def test_fixed_target_package_digest_requires_production_readonly_arrays():
    result = _valid_fixed_target_result()
    document = result["queued_drift_package"]["package"]
    observed = document["observation"]

    def array(projected, *, readonly):
        value = np.asarray(projected["values"], dtype=np.dtype(projected["dtype"]))
        value.setflags(write=not readonly)
        return value

    def package(*, readonly):
        observation = module.PositionObservation(
            observed["measurement_time_s"], observed["availability_time_s"],
            array(observed["root_position_m"], readonly=readonly),
            array(observed["covariance_m2"], readonly=readonly),
            observed["tag_id"], tuple(observed["anchors"]),
            observed["quality_state"], observed["frame_valid"],
            observed["physical_point_valid"], observed["source_sequence"])
        return module.ContinuousConsensusObservation(
            observation, document["availability_time_ns"],
            tuple(document["trusted_nodes"]), tuple(document["anchors_used"]),
            document["packet_digest"], document["epoch_digest"],
            document["root_plan_digest"], document["admission_digest"],
            array(document["post_absolute_position_at_measurement_m"], readonly=readonly),
            array(document["applied_absolute_position_delta_m"], readonly=readonly),
            array(document["cumulative_absolute_position_correction_m"], readonly=readonly),
            document["digest"])

    assert module._package_digest(package(readonly=False)) != document["digest"]
    assert module._package_digest(package(readonly=True)) == document["digest"]


@pytest.mark.parametrize("field", (
    "old_policy", "old_state", "target_raw", "admission_raw", "admission_event",
    "obsolete", "b_reason", "a_outcome", "a_bucket", "a_prepared",
    "a_rejection_reason",
    "a_intent", "a_attempted", "a_succeeded", "a_packet", "a_epoch", "a_sequence",
    "a_source", "a_diagnostic",
    "position_only", "package", "coordinated_package", "velocity",
    "package_bytes", "eligible_order", "crossing_policy", "later_crossing", "rollback",
))
def test_fixed_target_validator_rejects_causal_tamper(field):
    result = deepcopy(_valid_fixed_target_result())
    group = result["b_group_visibility"][-1]
    if field == "old_policy": result["old_crossing_checkpoint"]["policy"]["inside_anchor_envelope"] = False
    elif field == "old_state":
        state = result["old_crossing_checkpoint"]["post_b"]
        state["vector"][0] = 2.
        state["root_state_canonical_sha256"], state["covariance_sha256"] = (
            module._state_document_hashes(state["time_s"], np.asarray(state["vector"]),
                                          np.asarray(state["covariance"])))
    elif field == "target_raw": group["record_identity"]["raw_identity"][0] += 1
    elif field == "admission_raw": result["first_subsequent_b_admission"]["record"]["raw_identity"][0] += 1
    elif field == "admission_event": result["first_subsequent_b_admission"]["record"]["event_digests"] = ["f"*64]
    elif field == "obsolete": group["obsolete_native200_source_pair"] = {"bad": True}
    elif field == "b_reason": result["first_subsequent_b_admission"]["audit"]["prepared_reason"] = "ACCEPTED"
    elif field == "a_outcome": result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["a"]["admission"]["outcome"] = "BAD"
    elif field == "a_bucket": result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["a"]["admission"]["bucket"] += 1
    elif field == "a_prepared": result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["a"]["admission"]["prepared_accepted"] = None
    elif field == "a_rejection_reason":
        a_audit = result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["a"]["admission"]
        a_audit["prepared_accepted"] = False
        a_audit["prepared_reason"] = ""
    elif field == "a_intent": result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["a"]["admission"]["commit_intent"] = True
    elif field == "a_attempted": result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["a"]["admission"]["commit_attempted"] = True
    elif field == "a_succeeded": result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["a"]["admission"]["commit_succeeded"] = True
    elif field == "a_packet": result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["a"]["admission"]["packet_digest"] = "7"*64
    elif field == "a_epoch": result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["a"]["admission"]["epoch_digest"] = "7"*64
    elif field == "a_sequence": result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["a"]["admission"]["source_sequence"] += 1
    elif field == "a_source": result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["a"]["admission"]["source_identity"][2] += 1
    elif field == "a_diagnostic":
        a_audit = result["first_subsequent_b_admission"]["record"]["record_ab_transactions"][0]["a"]["admission"]
        a_audit["diagnostic"] = {"reason": "OPTIMIZER_FAILURE"}
        a_audit["diagnostic_digest"] = "7"*64
    elif field == "position_only": result["first_subsequent_b_admission"]["velocity_and_bias_byte_inert"] = False
    elif field == "package": result["queued_drift_package"]["package"]["digest"] = "f"*64
    elif field == "coordinated_package":
        package = result["queued_drift_package"]["package"]
        package["admission_digest"] = "6"*64
        observed = package["observation"]
        def array(document):
            return np.asarray(document["values"], dtype=np.dtype(document["dtype"]))
        observation = module.PositionObservation(
            observed["measurement_time_s"], observed["availability_time_s"],
            array(observed["root_position_m"]), array(observed["covariance_m2"]),
            observed["tag_id"], tuple(observed["anchors"]), observed["quality_state"],
            observed["frame_valid"], observed["physical_point_valid"],
            observed["source_sequence"])
        rebuilt = module.ContinuousConsensusObservation(
            observation, package["availability_time_ns"], tuple(package["trusted_nodes"]),
            tuple(package["anchors_used"]), package["packet_digest"],
            package["epoch_digest"], package["root_plan_digest"],
            package["admission_digest"], array(package["post_absolute_position_at_measurement_m"]),
            array(package["applied_absolute_position_delta_m"]),
            array(package["cumulative_absolute_position_correction_m"]), "")
        package["digest"] = module._package_digest(rebuilt)
        result["first_eligible_native_response"]["consumed_observation_digest"] = package["digest"]
        result["first_eligible_native_response"]["prepared"]["drift_result"]["consumed_observation_digest"] = package["digest"]
    elif field == "package_bytes":
        projected = result["queued_drift_package"]["package"]["post_absolute_position_at_measurement_m"]
        projected["values"][0] += .01
        array = np.asarray(projected["values"], dtype=np.dtype(projected["dtype"]))
        projected["sha256"] = module.hashlib.sha256(array.tobytes()).hexdigest()
    elif field == "velocity": result["first_eligible_native_response"]["velocity_delta_mps"] = [.6, 0., 0.]
    elif field == "eligible_order": result["native_observation_order"].insert(0, {
        **result["native_observation_order"][0], "record_ordinal": 36_833})
    elif field == "crossing_policy": result["crossing"]["post_policy"]["inside_anchor_envelope"] = True
    elif field == "later_crossing": result["crossing"]["record_transaction"]["record_ordinal"] = 36_833
    elif field == "rollback": result["first_subsequent_b_admission"]["record"]["rollback_observed"] = True
    if field in {"target_raw", "obsolete"}:
        group["evidence_sha256"] = module._canonical_digest({key: value
            for key, value in group.items() if key != "evidence_sha256"})
    with pytest.raises(ValueError, match="invalid fixed bucket"):
        module.validate_fixed_target_result(result)


def _fixed_ticket(ordinal, raw_identity, event_digest):
    return SimpleNamespace(record_ordinal=ordinal, raw_identity=raw_identity,
        event_digests=(event_digest,), sensor_identity_digests=("c"*64,))


def test_fixed_target_observer_runs_checkpoint_target_queue_and_later_crossing():
    owners = _owners(_state()); coordinator = owners.coordinator
    owners.reader.inventory.regions = (SimpleNamespace(
        start_offset=0, stop_offset=300_000_000, region_id="03_region"),)
    observer = module.FixedBucket1958009Observer(owners, time.monotonic())
    observer.hooks_installed = True
    observer.anchor_lower_m = np.array([-1., -1., -1.])
    observer.anchor_upper_m = np.array([1., 1., 1.])
    observer._capture_state("b", _state(), publication_revision=1,
                            publication_digest=f"{1:064x}")
    branch = coordinator._b
    branch._revision = 10
    branch._pending = {1_958_009: tuple(
        _pending_row(f"N{i}", f"1958009:N{i}") for i in range(9))}
    branch._finalized_watermark = 1_958_008
    branch.journal = (); branch.counters = {}
    target = module.TARGET_COMPLETE_BUCKET_RECORDS[36_833]
    group_audit = ContinuousGroupAudit(
        1_958_009, "PREPARED_COMPLETE_GROUP", tuple(f"N{i}" for i in range(10)),
        ("03_pelvis_hula_circle",) * 10, "ACTION_EVIDENCE")
    a_audit = {"bucket": 1_958_009, "branch": "A_BASELINE",
        "prepared_accepted": True, "outcome": "BASELINE_NO_UWB_COMMIT"}
    b_audit = {"bucket": 1_958_009, "branch": "B_UWB",
        "prepared_accepted": True,
        "prepared_reason": "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR",
        "outcome": "UWB_COMMIT_SUCCEEDED"}

    def consume(ticket):
        if ticket.record_ordinal == 36_815:
            coordinator.events += 1
        elif ticket.record_ordinal == 36_833:
            coordinator.events += 1; branch._revision += 1
            branch._pending = {}; branch._finalized_watermark = 1_958_009
            branch.journal = (group_audit,)
            coordinator.transactions.append({"a": {"admission": a_audit},
                                             "b": {"admission": b_audit}})
            coordinator.total += 1
            observer._record_effects.extend((_effect("commit_admission"),
                                             _effect("commit_consensus_admission")))
        else:
            _commit(observer, coordinator, _state(x=1.1, time_s=1.02))
            observer._record_effects.append(_effect(
                "commit_native200", consumed="d"*64, frame_ns=1_010_000_000))
    observer.consume = consume

    coordinator.events = 270_707
    observer(_fixed_ticket(36_815, module.OLD_CROSSING_RECORD["raw_identity"], "e"*64))
    assert observer.old_crossing_checkpoint["policy"]["inside_anchor_envelope"]
    coordinator.events = 270_824
    observer(_fixed_ticket(36_833, target["raw_identity"], target["event_digest"]))
    assert observer.admission["audit"] == b_audit
    assert observer.queue["package"]["digest"] == "d"*64
    coordinator.events = 270_899
    with pytest.raises(module._FollowupComplete, match="LATER_PHYSICAL_CROSSING"):
        observer(_fixed_ticket(36_844, (1, 2, 3, "f"*64), "a"*64))
    assert observer.native_response["consumed_observation_digest"] == "d"*64
    assert observer.crossing["record_transaction"]["record_ordinal"] == 36_844


def _fixed_real_group_setup():
    coordinator, _frames, _ticket = session_fixtures._real_record_coordinator(1)
    coordinator._initializer.preworld_pose_omissions = 0
    a_owner, rows, availability_ns = group_fixtures._real_owner_fixture()
    b_owner, _b_rows, _b_availability = group_fixtures._real_owner_fixture()
    authority = coordinator._FullSessionTenNodeABCoordinator__group_record_authority
    a_owner.bind_native200_record_coordinator(authority)
    b_owner.bind_native200_record_coordinator(authority)
    coordinator._a, coordinator._b = a_owner, b_owner
    drift = group_fixtures._enable_consensus_drift(b_owner)

    def uwb_event(row, availability=availability_ns):
        clock = a_owner._composition.engine.static.clocks[row.node]
        common_ns = int(round(clock.link_time_ns(
            event_boot_epoch=row.boot, strobe_us=row.strobe_us, t_round_us=0.)))
        return ContinuousEvent(
            f"fixed-real:{row.node}", "UWB", 0, "00_initial_still",
            common_ns, availability, row.node, row.boot, "B306_TIMER2",
            "a"*64, "b"*64, "c"*64, "host-only", row,
            uwb_timer2=UwbTimer2Fields(row.strobe_us, row.frame_us))

    ordered = sorted(rows, key=lambda row: row.node)
    for row in ordered[:-1]:
        coordinator._atomic_ab(uwb_event(row), uwb=True)
    return coordinator, a_owner, b_owner, drift, ordered, uwb_event


def test_fixed_target_real_owner_fallback_queue_consume_and_later_crossing(
    monkeypatch,
):
    (coordinator, a_owner, b_owner, drift, ordered,
     uwb_event) = _fixed_real_group_setup()
    # Reuse the existing real-owner obsolete-pair fixture shape: the group
    # waits while thirteen native frames commit, then its final row arrives.
    for _ in range(13):
        coordinator._atomic_ab(
            group_fixtures._next_native200_events(a_owner, 1)[0], uwb=False)
    latest_ns = a_owner._composition.history.frames[-1].source_global_ns
    final_uwb = uwb_event(ordered[-1], latest_ns + 20_000_000)
    synthetic_target = dict(module.TARGET_COMPLETE_BUCKET_RECORDS[36_833])
    synthetic_target["bucket"] = 1
    monkeypatch.setitem(module.TARGET_COMPLETE_BUCKET_RECORDS, 36_833, synthetic_target)
    region = SimpleNamespace(start_offset=0, stop_offset=300_000_000,
                             region_id="03_synthetic")
    owners = SimpleNamespace(coordinator=coordinator,
        reader=SimpleNamespace(inventory=SimpleNamespace(regions=(region,))))
    observer = module.FixedBucket1958009Observer(owners, time.monotonic())
    observer.anchor_lower_m = np.full(3, -100.)
    observer.anchor_upper_m = np.array([.024, 100., 100.])
    observer._install_hooks()
    native_events = []

    def consume(ticket):
        if ticket.record_ordinal == 36_815:
            coordinator._events += 1
        elif ticket.record_ordinal == 36_833:
            coordinator._atomic_ab(final_uwb, uwb=True)
            coordinator._events += 1
        else:
            event = group_fixtures._next_native200_events(a_owner, 1)[0]
            native_events.append(event)
            coordinator._atomic_ab(event, uwb=False)
            coordinator._events += 1

    observer.consume = consume
    coordinator._events = 270_707
    observer(_fixed_ticket(36_815, module.OLD_CROSSING_RECORD["raw_identity"], "e"*64))
    coordinator._events = 270_824
    observer(_fixed_ticket(36_833, synthetic_target["raw_identity"],
                           synthetic_target["event_digest"]))
    audit = observer.admission["audit"]
    assert audit["prepared_reason"] == "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
    assert audit["outcome"] == "UWB_COMMIT_SUCCEEDED"
    assert observer.admission["velocity_and_bias_byte_inert"]
    assert drift.pending_count == 1
    package_digest = observer.queue["package"]["digest"]

    completed = False
    for offset in range(1, 9):
        coordinator._events = 270_824 + offset
        ticket = _fixed_ticket(36_833 + offset,
            (1_212_593, 221_554_405, 221_554_613, f"{offset:064x}"),
            f"{offset + 100:064x}")
        try:
            observer(ticket)
        except module._FollowupComplete as stopped:
            assert stopped.reason == "TARGET_CHAIN_RESOLVED_WITH_LATER_PHYSICAL_CROSSING"
            completed = True
            break
    assert completed
    assert observer.native_response["consumed_observation_digest"] == package_digest
    assert observer.native_response["position_bias_covariance_byte_inert"]
    assert observer.native_response["first_eligible_native"] == next(
        item for item in observer.native_observation_order if item["eligible"])
    assert drift.pending_count == 0
    assert observer.crossing["record_transaction"]["record_ordinal"] > 36_833


def test_fixed_target_real_owner_rejection_is_explicit_and_queues_nothing(
    monkeypatch,
):
    (coordinator, a_owner, b_owner, drift, ordered,
     uwb_event) = _fixed_real_group_setup()
    composition = b_owner._composition
    original_prepare = composition.prepare_admission

    def reject(packet, epoch, **kwargs):
        accepted = original_prepare(packet, epoch, **kwargs)
        return replace(accepted, causal_transaction=None, sidecar_ticket=None,
            prepared_result=replace(accepted.prepared_result, accepted=False,
                                    reason="ARTICULATED_RANGE_REJECTED"))

    monkeypatch.setattr(composition, "prepare_admission", reject)
    synthetic_target = dict(module.TARGET_COMPLETE_BUCKET_RECORDS[36_833])
    synthetic_target["bucket"] = 1
    monkeypatch.setitem(module.TARGET_COMPLETE_BUCKET_RECORDS, 36_833, synthetic_target)
    owners = SimpleNamespace(coordinator=coordinator,
        reader=SimpleNamespace(inventory=SimpleNamespace(regions=(SimpleNamespace(
            start_offset=0, stop_offset=300_000_000, region_id="03_synthetic"),))))
    observer = module.FixedBucket1958009Observer(owners, time.monotonic())
    observer.anchor_lower_m = np.full(3, -100.)
    observer.anchor_upper_m = np.full(3, 100.)
    observer._install_hooks()
    observer.old_crossing_checkpoint = {"real_owner": True}
    final = uwb_event(ordered[-1])
    observer.consume = lambda _ticket: (
        coordinator._atomic_ab(final, uwb=True),
        setattr(coordinator, "_events", coordinator._events + 1))
    coordinator._events = 270_824
    with pytest.raises(module._FollowupComplete,
                       match="TARGET_B_ADMISSION_SCIENTIFICALLY_REJECTED"):
        observer(_fixed_ticket(36_833, synthetic_target["raw_identity"],
                               synthetic_target["event_digest"]))
    assert observer.admission["audit"]["prepared_accepted"] is False
    assert observer.admission["audit"]["outcome"] == "PREPARED_REJECTED_NO_COMMIT"
    assert observer.queue is None and drift.pending_count == 0


def test_fixed_target_real_owner_commit_failure_restores_owner_and_cursors(
    monkeypatch,
):
    (coordinator, _a_owner, b_owner, drift, ordered,
     uwb_event) = _fixed_real_group_setup()
    synthetic_target = dict(module.TARGET_COMPLETE_BUCKET_RECORDS[36_833])
    synthetic_target["bucket"] = 1
    monkeypatch.setitem(module.TARGET_COMPLETE_BUCKET_RECORDS, 36_833, synthetic_target)
    owners = SimpleNamespace(coordinator=coordinator,
        reader=SimpleNamespace(inventory=SimpleNamespace(regions=(SimpleNamespace(
            start_offset=0, stop_offset=300_000_000, region_id="03_synthetic"),))))
    observer = module.FixedBucket1958009Observer(owners, time.monotonic())
    observer.anchor_lower_m = np.full(3, -100.)
    observer.anchor_upper_m = np.full(3, 100.)
    observer._install_hooks(); observer.old_crossing_checkpoint = {"stable": True}
    before = group_fixtures._composition_fingerprint(b_owner._composition)
    original = b_owner._composition.commit_consensus_admission

    def fail_after_commit(plan):
        original(plan)
        raise RuntimeError("injected real-owner consensus failure")

    monkeypatch.setattr(b_owner._composition, "commit_consensus_admission",
                        fail_after_commit)
    final = uwb_event(ordered[-1])
    observer.consume = lambda _ticket: coordinator._atomic_ab(final, uwb=True)
    coordinator._events = 270_824
    with pytest.raises(RuntimeError, match="injected real-owner"):
        observer(_fixed_ticket(36_833, synthetic_target["raw_identity"],
                               synthetic_target["event_digest"]))
    assert group_fixtures._composition_fingerprint(b_owner._composition) == before
    assert observer.admission is None and observer.queue is None
    assert observer.group_visibility == [] and drift.pending_count == 0


def test_fixed_target_target_record_rollback_restores_diagnostic_cursors():
    owners = _owners(_state()); coordinator = owners.coordinator
    owners.reader.inventory.regions = (SimpleNamespace(
        start_offset=0, stop_offset=300_000_000, region_id="03_region"),)
    observer = module.FixedBucket1958009Observer(owners, time.monotonic())
    observer.hooks_installed = True
    observer.anchor_lower_m = np.array([-1., -1., -1.])
    observer.anchor_upper_m = np.array([1., 1., 1.])
    observer.old_crossing_checkpoint = {"stable": True}
    branch = coordinator._b
    branch._revision = 10; branch._pending = {}; branch._finalized_watermark = None
    branch.journal = (); branch.counters = {}
    target = module.TARGET_COMPLETE_BUCKET_RECORDS[36_833]
    def fail(_ticket):
        observer.admission = {"transient": True}
        observer.queue = {"transient": True}
        observer.group_visibility.append({"transient": True})
        raise RuntimeError("transaction rollback")
    observer.consume = fail
    coordinator.events = 270_824
    with pytest.raises(RuntimeError, match="transaction rollback"):
        observer(_fixed_ticket(36_833, target["raw_identity"], target["event_digest"]))
    assert observer.admission is None and observer.queue is None
    assert observer.group_visibility == []
    assert observer.old_crossing_checkpoint == {"stable": True}
