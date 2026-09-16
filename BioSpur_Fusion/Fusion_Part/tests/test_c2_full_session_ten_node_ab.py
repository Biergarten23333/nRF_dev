from __future__ import annotations

import ast
import pickle
import time
from dataclasses import fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import pytest
import numpy as np

import biospur_fusion.c2_coupled_progressive.full_session_ten_node_ab as module
import test_c2_continuous_group_epoch_owner as group_fixtures
from tools import build_c2_full_session_ten_node_ab as factory
from biospur_fusion.c2_coupled_progressive.continuous_frontend import ContinuousEvent
from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
from biospur_fusion.root_r3 import RootState
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionRecordTicket,
)


class _Group:
    def __init__(self, *, fail=False):
        self.value = 0
        self.fail = fail
        self.flags = []
        self.counters = {}
        self._admission_journal = ()

    @property
    def admission_journal(self):
        return self._admission_journal

    def continuous_snapshot(self):
        return self.value

    def bind_native200_record_coordinator(self, authority):
        self.record_coordinator_authority = authority

    def restore_continuous_snapshot(self, value):
        self.value = value

    def prepare_continuous_event(self, event, *, commit_uwb):
        self.flags.append(commit_uwb)
        return event

    def commit_continuous_event(self, prepared):
        self.value += 1
        if self.fail:
            raise RuntimeError("injected second-branch failure")

    def prepared_group_disposition(self, _prepared):
        return SimpleNamespace(
            provenance_digest=None, reason="NO_COMPLETE_GROUP", admission=None,
        )


def _coordinator():
    value = object.__new__(module.FullSessionTenNodeABCoordinator)
    value._body = None
    value._initializer = None
    value._a = value._b = None
    value._pending = {}
    value._deferred = None
    value._events = value._imus = value._uwbs = value._frames = value._dropouts = 0
    value._bootstrap_bucket = None
    value._bootstrap_watermark = None
    value._prebootstrap_uwb_events = 0
    value._prebootstrap_not_applied = 0
    value._prebootstrap_duplicates = 0
    value._preworld_uwb_groups = 0
    value._deferred_pose_not_ready = 0
    value._deferred_pose_retries = 0
    value._deferred_pose_expired = 0
    value._deferred_pose_finish_rejected = 0
    value._bootstrap_measurement_rejected = 0
    value._bootstrap_measurement_rejection_reasons = {}
    value._bootstrap_uwb_availability_ns = None
    value._bootstrap_pose_readiness_availability_ns = None
    value._last_pelvis_timer = value._last_pelvis_ns = None
    import hashlib
    value._chain = hashlib.sha256()
    value._ab_transaction_journal = ()
    value._ab_transaction_total = 0
    value._record_batch_ab_stage = None
    value._batched_records = value._batched_frames = value._scalar_frames = 0
    value._unrouted_frames = 0
    value._scalar_fallbacks = {}
    value._FullSessionTenNodeABCoordinator__record_transaction_key = object()
    value._FullSessionTenNodeABCoordinator__group_record_authority = object()
    value._FullSessionTenNodeABCoordinator__record_transaction_ordinal = 0
    value._FullSessionTenNodeABCoordinator__active_record_transaction = None
    return value


def _semantic_state(value):
    if isinstance(value, np.ndarray):
        return ("ndarray", value.dtype.str, value.shape, value.tobytes())
    if isinstance(value, Enum):
        return ("enum", type(value).__qualname__, value.name)
    if is_dataclass(value):
        return (
            "dataclass", type(value).__qualname__,
            tuple((field.name, _semantic_state(getattr(value, field.name)))
                  for field in fields(value)),
        )
    if isinstance(value, dict) or hasattr(value, "items"):
        return (
            "mapping",
            tuple(sorted(
                ((_semantic_state(key), _semantic_state(item))
                 for key, item in value.items()),
                key=repr,
            )),
        )
    if isinstance(value, (tuple, list)):
        return (type(value).__name__, tuple(_semantic_state(item) for item in value))
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    return (type(value).__qualname__, pickle.dumps(value, protocol=5))


def _coordinator_state_bytes(value):
    initializer = value._initializer
    return pickle.dumps(_semantic_state((
        None if value._a is None else value._a.continuous_snapshot(),
        None if value._b is None else value._b.continuous_snapshot(),
        value._pending, value._deferred, value._events, value._imus,
        value._uwbs, value._frames, value._dropouts, value._bootstrap_bucket,
        value._bootstrap_watermark, value._prebootstrap_uwb_events,
        value._prebootstrap_not_applied, value._prebootstrap_duplicates,
        value._preworld_uwb_groups, value._deferred_pose_not_ready,
        value._deferred_pose_retries, value._deferred_pose_expired,
        value._deferred_pose_finish_rejected,
        value._bootstrap_measurement_rejected,
        value._bootstrap_measurement_rejection_reasons,
        value._bootstrap_uwb_availability_ns,
        value._bootstrap_pose_readiness_availability_ns,
        value._last_pelvis_timer, value._last_pelvis_ns,
        value._chain.digest(), initializer._frames,
        value._batched_records, value._batched_frames,
        value._scalar_frames, value._unrouted_frames, value._scalar_fallbacks,
        initializer._preworld_pose_omissions, initializer._revision,
        initializer._located,
    )), protocol=5)


def _uwb(node, ns=120_000_000):
    return SimpleNamespace(
        event_id=f"uwb:{node}:{ns}", kind="UWB", node_id=node,
        common_global_ns=ns, availability_global_ns=ns,
        boot_epoch=1, clock_domain="B306_TIMER2",
        clock_mapping_digest="1" * 64, clock_owner_sha256="2" * 64,
        clock_source_sha256="3" * 64,
        payload_owner=SimpleNamespace(node=node, sweep=ns // 1000),
    )


def _frame(*, timer, source_ns, availability_ns, digest):
    return SimpleNamespace(
        source_timer_us=timer, source_global_ns=source_ns,
        imu_sample=SimpleNamespace(availability_time_s=availability_ns * 1e-9),
        digest=digest, node="BSFC2CC", boot_epoch=7,
        clock_mapping_digest="2" * 64, clock_owner_sha256="3" * 64,
        clock_source_sha256="4" * 64, timer2_base_us=timer - 5_000,
    )


def test_first_complete_ten_node_bucket_is_owned_and_installs_ab():
    coordinator = _coordinator()
    a, b = _Group(), _Group()

    class Initializer:
        installation = SimpleNamespace(a=a, b=b)

        def bootstrap_pose_readiness(self, events):
            return SimpleNamespace(status=module.BootstrapPoseReadinessStatus.READY)

        def prepare_first_group_outcome(self, events):
            assert tuple(event.node_id for event in events) == tuple(sorted(NODE_TO_SEGMENT))
            return SimpleNamespace(
                accepted=True, prepared=SimpleNamespace(installation=self.installation),
            )

        def commit_first_group(self, prepared):
            assert prepared.installation is self.installation
            return self.installation

    coordinator._initializer = Initializer()
    for node in tuple(NODE_TO_SEGMENT)[:-1]:
        coordinator._bootstrap_or_route_uwb(_uwb(node))
        assert coordinator._a is None
    coordinator._bootstrap_or_route_uwb(_uwb(tuple(NODE_TO_SEGMENT)[-1]))
    assert (coordinator._a, coordinator._b) == (a, b)
    assert coordinator._bootstrap_bucket == 1
    assert coordinator._pending == {}


def test_bootstrap_second_owner_bind_failure_precedes_initializer_commit():
    coordinator = _coordinator()
    events = tuple(_uwb(node) for node in sorted(NODE_TO_SEGMENT))
    left = _Group()

    class RejectBinding(_Group):
        def bind_native200_record_coordinator(self, authority):
            raise RuntimeError("injected B authority bind failure")

    installation = SimpleNamespace(a=left, b=RejectBinding())

    class Initializer:
        commits = 0

        def bootstrap_pose_readiness(self, supplied):
            assert supplied == events
            return SimpleNamespace(status=module.BootstrapPoseReadinessStatus.READY)

        def prepare_first_group_outcome(self, supplied):
            assert supplied == events
            return SimpleNamespace(
                accepted=True,
                prepared=SimpleNamespace(installation=installation),
            )

        def commit_first_group(self, prepared):
            self.commits += 1
            return prepared.installation

    initializer = Initializer()
    coordinator._initializer = initializer
    deferred = module._DeferredBootstrapBucket(
        1, events, module._deferred_event_digest(events), 120_000_000,
    )
    before = (coordinator._a, coordinator._b, coordinator._bootstrap_bucket,
              coordinator._bootstrap_watermark, coordinator._deferred)
    with pytest.raises(RuntimeError, match="B authority bind"):
        coordinator._install_bootstrap(
            deferred, readiness_availability_ns=120_000_000,
        )
    assert initializer.commits == 0
    assert (coordinator._a, coordinator._b, coordinator._bootstrap_bucket,
            coordinator._bootstrap_watermark, coordinator._deferred) == before


def test_postbootstrap_uwb_uses_false_true_and_rolls_back_both():
    coordinator = _coordinator()
    coordinator._a, coordinator._b = _Group(), _Group()
    coordinator._a.bind_native200_record_coordinator(
        coordinator._FullSessionTenNodeABCoordinator__group_record_authority,
    )
    coordinator._b.bind_native200_record_coordinator(
        coordinator._FullSessionTenNodeABCoordinator__group_record_authority,
    )
    coordinator._atomic_ab(_uwb("BSFC2CC"), uwb=True)
    assert coordinator._a.flags == [False]
    assert coordinator._b.flags == [True]
    assert (coordinator._a.value, coordinator._b.value) == (1, 1)

    coordinator._a, coordinator._b = _Group(), _Group(fail=True)
    coordinator._a.bind_native200_record_coordinator(
        coordinator._FullSessionTenNodeABCoordinator__group_record_authority,
    )
    coordinator._b.bind_native200_record_coordinator(
        coordinator._FullSessionTenNodeABCoordinator__group_record_authority,
    )
    with pytest.raises(RuntimeError, match="second-branch"):
        coordinator._atomic_ab(_uwb("BSFC2CC"), uwb=False)
    assert (coordinator._a.value, coordinator._b.value) == (0, 0)


@pytest.mark.parametrize(
    "failure_site",
    ("a_prepare", "b_prepare", "provenance", "a_commit", "b_commit", "publish"),
)
def test_postbootstrap_uwb_failures_restore_every_owner(
    monkeypatch, failure_site,
):
    coordinator = _coordinator()
    coordinator._a, _rows, _availability = group_fixtures._real_owner_fixture()
    coordinator._b, _rows, _availability = group_fixtures._real_owner_fixture()
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    authority = coordinator._FullSessionTenNodeABCoordinator__group_record_authority
    coordinator._a.bind_native200_record_coordinator(authority)
    coordinator._b.bind_native200_record_coordinator(authority)
    event = group_fixtures._uwb(group_fixtures.NODES[0], 1_000_000)
    before = _coordinator_state_bytes(coordinator)
    message = f"INJECTED_{failure_site.upper()}"

    if failure_site in {"a_prepare", "b_prepare"}:
        owner = coordinator._a if failure_site == "a_prepare" else coordinator._b
        monkeypatch.setattr(
            owner, "prepare_continuous_event",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(message)),
        )
    elif failure_site == "provenance":
        monkeypatch.setattr(
            coordinator._b, "prepared_group_disposition",
            lambda _prepared: SimpleNamespace(
                provenance_digest="f" * 64, reason="HOSTILE", admission=None,
            ),
        )
        message = "A/B raw-group provenance diverged"
    elif failure_site in {"a_commit", "b_commit"}:
        owner = coordinator._a if failure_site == "a_commit" else coordinator._b
        actual = owner.commit_continuous_event

        def fail_commit(prepared, *, _actual=actual):
            _actual(prepared)
            raise RuntimeError(message)

        monkeypatch.setattr(owner, "commit_continuous_event", fail_commit)
    elif failure_site == "publish":
        monkeypatch.setattr(
            coordinator, "_publish_ab_transaction",
            lambda *_args: (_ for _ in ()).throw(RuntimeError(message)),
        )
    with pytest.raises(RuntimeError, match=message):
        coordinator._atomic_ab(event, uwb=True)
    assert _coordinator_state_bytes(coordinator) == before
    assert coordinator._ab_transaction_journal == ()
    assert coordinator._ab_transaction_total == 0


def test_terminal_uwb_failure_restores_branch_admission_journals(
    monkeypatch,
):
    coordinator = _coordinator()
    coordinator._a, _ = group_fixtures._owner(group_fixtures.Composition())
    coordinator._b, _ = group_fixtures._owner(group_fixtures.Composition())
    authority = coordinator._FullSessionTenNodeABCoordinator__group_record_authority
    coordinator._a.bind_native200_record_coordinator(authority)
    coordinator._b.bind_native200_record_coordinator(authority)
    events = tuple(
        group_fixtures._uwb(node, 1_000_000 + index * 1_000)
        for index, node in enumerate(group_fixtures.NODES)
    )
    for event in events[:-1]:
        coordinator._atomic_ab(event, uwb=True)

    before_a = coordinator._a.continuous_snapshot()
    before_b = coordinator._b.continuous_snapshot()
    before_ab = coordinator._ab_transaction_journal
    before_total = coordinator._ab_transaction_total
    message = "INJECTED_TERMINAL_PUBLISH"
    monkeypatch.setattr(
        coordinator, "_publish_ab_transaction",
        lambda *_args: (_ for _ in ()).throw(RuntimeError(message)),
    )

    with pytest.raises(RuntimeError, match=message):
        coordinator._atomic_ab(events[-1], uwb=True)
    assert coordinator._a.continuous_snapshot() == before_a
    assert coordinator._b.continuous_snapshot() == before_b
    assert coordinator._a.admission_journal == before_a[-1]
    assert coordinator._b.admission_journal == before_b[-1]
    assert not any(
        audit.outcome == "UWB_COMMIT_SUCCEEDED"
        for audit in coordinator._b.admission_journal
    )
    assert coordinator._ab_transaction_journal == before_ab
    assert coordinator._ab_transaction_total == before_total


def test_complete_natural_assembly_obsolete_fallback_is_ab_atomic_and_retryable():
    diagnostic = group_fixtures.ObsoleteNative200SourcePairDiagnostic(
        4_260_209_875, 234_961_076_526_129,
        4_260_274_875, 234_961_141_525_340, 0,
    )
    coordinator = _coordinator()
    a_composition = group_fixtures.Composition(obsolete_pair=diagnostic)
    b_composition = group_fixtures.Composition(
        obsolete_pair=diagnostic, fail_commit=True,
    )
    coordinator._a, _ = group_fixtures._owner(a_composition)
    coordinator._b, _ = group_fixtures._owner(b_composition)
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    authority = coordinator._FullSessionTenNodeABCoordinator__group_record_authority
    coordinator._a.bind_native200_record_coordinator(authority)
    coordinator._b.bind_native200_record_coordinator(authority)
    availability_ns = 234_961_160_136_962
    events = tuple(
        group_fixtures._uwb(
            node, 234_961_080_000_000 + index * 1_000,
            availability_ns=availability_ns,
        )
        for index, node in enumerate(group_fixtures.NODES)
    )
    for event in events[:-1]:
        coordinator._atomic_ab(event, uwb=True)

    before = _coordinator_state_bytes(coordinator)
    with pytest.raises(RuntimeError, match="injected composition commit"):
        coordinator._atomic_ab(events[-1], uwb=True)
    assert _coordinator_state_bytes(coordinator) == before
    assert coordinator._ab_transaction_journal == ()
    assert coordinator._ab_transaction_total == 0

    b_composition.fail_commit = False
    coordinator._atomic_ab(events[-1], uwb=True)
    assert coordinator._ab_transaction_total == 1
    pair = coordinator._ab_transaction_journal[-1]
    assert pair.a.admission.prepared_reason == (
        "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
    )
    assert pair.a.admission.outcome == "BASELINE_NO_UWB_COMMIT"
    assert pair.b.admission.prepared_reason == (
        "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
    )
    assert pair.b.admission.outcome == "UWB_COMMIT_SUCCEEDED"
    assert pair.a.admission.candidate_digest == pair.b.admission.candidate_digest
    assert pair.a.admission.diagnostic == diagnostic
    assert pair.b.admission.diagnostic == diagnostic


def test_coordinator_pairs_independent_no_admission_and_success_dispositions():
    from test_c2_continuous_group_epoch_owner import (
        Composition, NODES, POSE_AGE_LIMIT_NS, _owner, _uwb as group_uwb,
    )
    from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
        StalePoseLinkDiagnostic,
    )

    stale = StalePoseLinkDiagnostic(
        10_005_001, 5_000_000, POSE_AGE_LIMIT_NS + 1, False,
    )
    left, _ = _owner(Composition(stale_pose=stale))
    right, _ = _owner(Composition())
    coordinator = _coordinator()
    coordinator._a, coordinator._b = left, right
    for index, node in enumerate(NODES):
        coordinator._atomic_ab(
            group_uwb(node, 1_000_000 + index * 1_000), uwb=True,
        )

    assert len(coordinator._ab_transaction_journal) == 1
    assert coordinator._ab_transaction_total == 1
    pair = coordinator._ab_transaction_journal[0]
    assert pair.a.reason == "STALE_POSE_LINK_DEFERRED"
    assert pair.a.admission is None
    assert pair.b.reason == "PREPARED_ADMISSION"
    assert pair.b.admission.outcome == "UWB_COMMIT_SUCCEEDED"

    metrics = factory._WholeSessionMetrics(0.0)
    metrics.consume_authoritative_admissions(SimpleNamespace(
        audit=lambda: SimpleNamespace(
            ab_transaction_journal=coordinator._ab_transaction_journal,
            ab_transaction_total=coordinator._ab_transaction_total,
        ),
    ))
    assert metrics.admission_pairs == 1
    assert metrics.accepted == {"b": 1}
    assert metrics.rejection_reasons["a"] == {"STALE_POSE_LINK_DEFERRED": 1}


def test_bootstrap_is_bounded_and_accounts_duplicates_and_old_buckets():
    coordinator = _coordinator()
    first = tuple(NODE_TO_SEGMENT)[0]
    coordinator._bootstrap_or_route_uwb(_uwb(first, 120_000_000))
    coordinator._bootstrap_or_route_uwb(_uwb(first, 120_000_000))
    assert coordinator._prebootstrap_duplicates == 1
    assert coordinator._prebootstrap_not_applied == 1
    for bucket in range(2, 8):
        coordinator._bootstrap_or_route_uwb(
            _uwb(first, bucket * module.EPOCH_PERIOD_NS)
        )
    assert len(coordinator._pending) <= module.MAX_PENDING_BUCKETS
    assert coordinator._prebootstrap_not_applied >= 4


def test_complete_group_before_pose_is_deferred_then_bootstraps_exactly_once():
    coordinator = _coordinator()
    a, b = _Group(), _Group()

    class Initializer:
        calls = 0
        commits = 0
        installation = SimpleNamespace(a=a, b=b)
        accepted = []

        @property
        def preworld_pose_omissions(self):
            return 0

        def accept_native200(self, frame):
            self.accepted.append(frame.digest)

        def bootstrap_pose_readiness(self, events):
            self.calls += 1
            status = (
                module.BootstrapPoseReadinessStatus.READY
                if self.calls >= 3
                else module.BootstrapPoseReadinessStatus.RETRYABLE_POSE_NOT_READY
            )
            return SimpleNamespace(status=status)

        def prepare_first_group_outcome(self, events):
            assert tuple(event.node_id for event in events) == tuple(sorted(NODE_TO_SEGMENT))
            assert all(event.common_global_ns == 120_000_000 for event in events)
            assert all(event.availability_global_ns == 120_000_000 for event in events)
            return SimpleNamespace(
                accepted=True, prepared=SimpleNamespace(installation=self.installation),
            )

        def commit_first_group(self, prepared):
            assert prepared.installation is self.installation
            self.commits += 1
            return self.installation

    initializer = Initializer()
    coordinator._initializer = initializer
    for node in NODE_TO_SEGMENT:
        coordinator._bootstrap_or_route_uwb(_uwb(node, 120_000_000))
    assert coordinator._a is None
    assert coordinator._deferred is not None
    original_digest = coordinator._deferred.event_digest
    assert coordinator._preworld_uwb_groups == 1
    assert coordinator._prebootstrap_not_applied == 0

    deferred_before = coordinator._deferred
    coordinator._bootstrap_or_route_uwb(_uwb(next(iter(NODE_TO_SEGMENT))))
    assert coordinator._deferred == deferred_before
    assert coordinator._prebootstrap_duplicates == 1
    assert coordinator._prebootstrap_not_applied == 1

    coordinator._accept_frame(_frame(
        timer=100_000, source_ns=100_000_000,
        availability_ns=130_000_000, digest="5" * 64,
    ))
    assert coordinator._a is None
    assert coordinator._deferred.event_digest == original_digest
    assert coordinator._deferred.retries == 1
    assert coordinator._prebootstrap_not_applied == 1

    coordinator._accept_frame(_frame(
        timer=105_000, source_ns=115_000_000,
        availability_ns=140_000_000, digest="6" * 64,
    ))
    assert (coordinator._a, coordinator._b) == (a, b)
    assert initializer.commits == 1
    assert initializer.accepted == ["5" * 64, "6" * 64]
    assert coordinator._deferred is None
    assert coordinator._bootstrap_watermark == 1
    assert coordinator._bootstrap_uwb_availability_ns == 120_000_000
    assert coordinator._bootstrap_pose_readiness_availability_ns == 140_000_000
    assert coordinator._deferred_pose_not_ready == 1
    assert coordinator._deferred_pose_retries == 2
    assert coordinator._prebootstrap_uwb_events == 11
    assert coordinator._prebootstrap_uwb_events == 10 + coordinator._prebootstrap_not_applied

    # The two released frames were installed into both branches by the
    # initializer.  Only a genuinely subsequent frame is routed again.
    coordinator._accept_frame(_frame(
        timer=110_000, source_ns=120_000_000,
        availability_ns=145_000_000, digest="7" * 64,
    ))
    assert a.flags == [False] and b.flags == [False]
    assert (a.value, b.value) == (1, 1)


def test_deferred_bucket_expires_when_pose_chronology_reaches_uwb_epoch():
    coordinator = _coordinator()

    class Initializer:
        preworld_pose_omissions = 0

        def accept_native200(self, frame):
            pass

        def bootstrap_pose_readiness(self, events):
            status = (
                module.BootstrapPoseReadinessStatus.TERMINALLY_MISSED
                if getattr(self, "accepted_once", False)
                else module.BootstrapPoseReadinessStatus.RETRYABLE_POSE_NOT_READY
            )
            self.accepted_once = True
            return SimpleNamespace(status=status)

        def prepare_first_group_outcome(self, events):
            raise AssertionError("terminal readiness must not enter the solver")

    coordinator._initializer = Initializer()
    for node in NODE_TO_SEGMENT:
        coordinator._bootstrap_or_route_uwb(_uwb(node, 120_000_000))
    coordinator._accept_frame(_frame(
        timer=100_000, source_ns=120_000_000,
        availability_ns=150_000_000, digest="8" * 64,
    ))
    assert coordinator._a is coordinator._b is coordinator._deferred is None
    assert coordinator._deferred_pose_expired == 1
    assert coordinator._prebootstrap_not_applied == 10
    assert coordinator._bootstrap_watermark == 1
    assert coordinator._deferred_pose_retries == 1
    assert module.MAXIMUM_POSE_AGE_NS == 5_005_000.0


def test_unresolved_deferred_bucket_is_explicitly_rejected_at_finish():
    coordinator = _coordinator()
    events = tuple(_uwb(node) for node in sorted(NODE_TO_SEGMENT))
    coordinator._deferred = module._DeferredBootstrapBucket(
        1, events, module._deferred_event_digest(events), 120_000_000,
    )
    coordinator._seal_deferred_rejection(expired=False)
    assert coordinator._deferred is None
    assert coordinator._deferred_pose_finish_rejected == 1
    assert coordinator._prebootstrap_not_applied == 10


@pytest.mark.parametrize("rejection_reason", (
    "NO_TRUSTED_NODE",
    "ROBUST_SELECTED_LINK_RESIDUAL_RMS_REJECTED",
))
def test_measurement_rejected_group_is_sealed_then_later_group_bootstraps(
    rejection_reason,
):
    coordinator = _coordinator()
    a, b = _Group(), _Group()

    class Initializer:
        preworld_pose_omissions = 0
        calls = 0
        installation = SimpleNamespace(a=a, b=b)

        def bootstrap_pose_readiness(self, events):
            return SimpleNamespace(status=module.BootstrapPoseReadinessStatus.READY)

        def prepare_first_group_outcome(self, events):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(
                    accepted=False, prepared=None, reason=rejection_reason,
                )
            return SimpleNamespace(
                accepted=True,
                prepared=SimpleNamespace(installation=self.installation),
                reason="ACCEPTED",
            )

        def commit_first_group(self, prepared):
            assert prepared.installation is self.installation
            return self.installation

    coordinator._initializer = Initializer()
    for node in NODE_TO_SEGMENT:
        coordinator._bootstrap_or_route_uwb(_uwb(node, 120_000_000))
    assert coordinator._a is None
    assert coordinator._bootstrap_measurement_rejected == 1
    assert coordinator._bootstrap_measurement_rejection_reasons == {
        rejection_reason: 1,
    }
    assert coordinator._prebootstrap_not_applied == 10
    assert coordinator._bootstrap_watermark == 1
    for node in NODE_TO_SEGMENT:
        coordinator._bootstrap_or_route_uwb(_uwb(node, 240_000_000))
    assert (coordinator._a, coordinator._b) == (a, b)
    assert coordinator._bootstrap_bucket == 2
    assert coordinator._prebootstrap_uwb_events == 20
    assert coordinator._prebootstrap_uwb_events == 10 + coordinator._prebootstrap_not_applied


def test_only_actual_pelvis_timer_dropout_emits_gap():
    coordinator = _coordinator()
    coordinator._a, coordinator._b = _Group(), _Group()
    emitted = []
    coordinator._atomic_ab = lambda event, *, uwb: emitted.append((event.kind, uwb))
    coordinator._atomic_ab_gap_native200 = (
        lambda gap, endpoint, **_kwargs:
        emitted.extend(((gap.kind, False), (endpoint.kind, False)))
    )
    base = SimpleNamespace(
        source_timer_us=10_000, source_global_ns=10_000_000,
        imu_sample=SimpleNamespace(availability_time_s=0.010),
        digest="1" * 64, node="BSFC2CC", boot_epoch=7,
        clock_mapping_digest="2" * 64, clock_owner_sha256="3" * 64,
        clock_source_sha256="4" * 64, timer2_base_us=5_000,
    )
    coordinator._accept_frame(base)
    coordinator._accept_frame(SimpleNamespace(
        **{**vars(base), "source_timer_us": 15_000,
           "source_global_ns": 15_000_000,
           "timer2_base_us": 10_000, "digest": "5" * 64,
           "imu_sample": SimpleNamespace(availability_time_s=0.015)}))
    coordinator._accept_frame(SimpleNamespace(
        **{**vars(base), "source_timer_us": 25_000,
           "source_global_ns": 25_000_000,
           "timer2_base_us": 20_000, "digest": "6" * 64,
           "imu_sample": SimpleNamespace(availability_time_s=0.025)}))
    assert emitted == [("IMU", False), ("IMU", False), ("GAP", False), ("IMU", False)]
    assert coordinator._dropouts == 1


def test_real_failure_boundary_routes_one_gap_endpoint_transaction():
    coordinator = _coordinator()
    coordinator._a, coordinator._b = _Group(), _Group()
    coordinator._last_pelvis_timer = 4_294_964_875
    coordinator._last_pelvis_ns = 234_995_831_103_996
    captured = []
    coordinator._atomic_ab_gap_native200 = (
        lambda gap, endpoint, **kwargs: captured.append((gap, endpoint, kwargs))
    )
    frame = SimpleNamespace(
        source_timer_us=4_295_003_789,
        source_global_ns=234_995_870_017_523,
        imu_sample=SimpleNamespace(availability_time_s=234_995.915_016_977),
        digest="c0aa95587f4e103817f638a2cf911e2b1c09e714e6674e855405e16710d2063e",
        node="BSFC2CC", boot_epoch=7,
        clock_mapping_digest="2" * 64, clock_owner_sha256="3" * 64,
        clock_source_sha256="4" * 64, timer2_base_us=4_295_003_789,
    )

    coordinator._accept_frame(frame)

    assert len(captured) == 1
    gap, endpoint, kwargs = captured[0]
    assert gap.gap_start_global_ns == 234_995_831_103_996
    assert gap.common_global_ns == endpoint.common_global_ns == 234_995_870_017_523
    assert endpoint.payload_owner is frame
    assert frame.source_timer_us - 4_294_964_875 == 38_914
    assert kwargs == {"record_transaction": None, "raw_identity": None}
    assert coordinator._dropouts == 1
    assert coordinator._frames == 1


def test_real_38914us_gap_endpoint_commits_once_then_rebatches_suffix():
    coordinator, _unused, _ticket = _real_record_coordinator(1)
    prior = coordinator._a._composition.history.frames[-1]
    engine = coordinator._a._composition.engine
    raw_digest = "34e7e0d445e7c92126a8691dba433264aad610d3fb1be864097b3677a77b0d4f"
    frames = []
    first_timer = prior.source_timer_us + 38_914
    for index in range(10):
        timer_us = first_timer + index * 5_000
        frame = group_fixtures._owned_frame(
            engine, group_fixtures._engine_and_packet_for_frame(), timer_us,
            prior.publication_revision + index + 1,
        )
        frame = replace(
            frame,
            raw_provenance=group_fixtures.RawByteProvenance(
                1_224_192, 223_666_569, 223_666_747, raw_digest, index,
            ),
            digest="",
        )
        frames.append(frame)
    frames = tuple(frames)

    class Body:
        def ingest_record_batch(self, _events, *, authority, consumer):
            for frame in frames:
                consumer(frame)

    coordinator._body = Body()
    coordinator._last_pelvis_timer = prior.source_timer_us
    coordinator._last_pelvis_ns = prior.source_global_ns
    coordinator.consume_record_ticket(_native200_record_ticket(frames))

    assert coordinator._dropouts == 1
    assert coordinator._scalar_fallbacks == {"malformed_or_discontinuity": 1}
    assert coordinator._scalar_frames == 1
    assert coordinator._batched_frames == 9
    assert coordinator._batched_records == 1
    for branch in (coordinator._a, coordinator._b):
        assert branch._composition.history.frames == frames
        assert branch._composition.engine.root.current_state.time_s == (
            frames[-1].imu_sample.measurement_time_s
        )
        assert branch._composition.engine.root.late_imu_rejected == 0


def test_control_module_never_reads_action_or_region_labels():
    tree = ast.parse(Path(module.__file__).read_text())
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert {"action_index", "action_id", "region_id", "host_time_label"}.isdisjoint(attributes)
    source = Path(module.__file__).read_text()
    assert "bootstrap_action00_root" not in source
    assert "ProspectiveAction00Initializer" not in source
    assert "prospective_action00_initializer" not in source
    assert "DiagnosticUwbEpochAssembler" not in source
    run = next(node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and node.name == "run")
    calls = [node for node in ast.walk(run) if isinstance(node, ast.Call)]
    assert sum(isinstance(call.func, ast.Attribute)
               and call.func.attr == "consume_record_batches" for call in calls) == 1
    assert not any(isinstance(call.func, ast.Attribute)
                   and call.func.attr == "consume" for call in calls)


def test_group_owned_diagnostic_snapshot_is_immutable_and_coordinator_pairs_it():
    state = RootState(1.0, np.arange(9.0), np.eye(9))
    token = SimpleNamespace(revision=4, time_s=1.0, digest="a" * 64)
    root = SimpleNamespace(current_state=state, publication_token=lambda: token)
    group = object.__new__(module.ContinuousGroupEpochOwner)
    group._composition = SimpleNamespace(engine=SimpleNamespace(root=root))
    group._counters = {"ACCEPTED": 2}
    group._journal = ()
    group._admission_journal = ()
    snapshot = group.diagnostic_snapshot()
    state.vector[0] = 99.0
    assert snapshot.root_state.vector[0] == 0.0
    assert snapshot.root_state.vector.flags.writeable is False
    assert snapshot.counters == (("ACCEPTED", 2),)
    assert snapshot.direct_nodes is None and snapshot.propagated_nodes is None

    coordinator = _coordinator()
    coordinator._a = coordinator._b = group
    published = coordinator.diagnostic_publication()
    assert published.a.publication_digest == "a" * 64
    assert published.b.publication_revision == 4


def test_batch_bridge_constructs_no_child_tickets_and_is_one_shot(monkeypatch):
    from test_c2_full_session_body_pose import _clock, _event

    event = _event(next(iter(NODE_TO_SEGMENT)), 0, _clock())
    raw = event.payload_owner.raw
    delivered = []

    class Owner:
        consumed = False

        def _deliver_record(self, ticket, consumer):
            if self.consumed:
                raise RuntimeError("replayed")
            self.consumed = True
            consumer((event,))

    class Body:
        def ingest_record_batch(self, events, *, authority, consumer):
            delivered.extend(events)

    coordinator = _coordinator()
    coordinator._body = Body()
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    coordinator._FullSessionTenNodeABCoordinator__body_batch_authority = object()
    owner = Owner()
    ticket = FullSessionRecordTicket(
        0, (raw.record_index, raw.start_offset, raw.end_offset, raw.encoded_sha256),
        ("1" * 64,), ("2" * 64,), object(), owner,
    )
    monkeypatch.setattr(module, "FullSessionImuEventTicket",
                        lambda *args, **kwargs: pytest.fail("child IMU ticket constructed"))
    monkeypatch.setattr(module, "FullSessionUwbEventTicket",
                        lambda *args, **kwargs: pytest.fail("child UWB ticket constructed"))
    coordinator.consume_record_ticket(ticket)
    assert delivered == [event]
    assert (coordinator._events, coordinator._imus, coordinator._uwbs) == (1, 1, 0)
    with pytest.raises(RuntimeError, match="replayed"):
        coordinator.consume_record_ticket(ticket)

    failing = _coordinator()
    failing._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    failing._FullSessionTenNodeABCoordinator__body_batch_authority = object()

    class FailingBody:
        def ingest_record_batch(self, events, *, authority, consumer):
            consumer(object())
            raise RuntimeError("injected post-frame failure")

    failing._body = FailingBody()
    def mutate_owned_frame_state(_frame):
        failing._frames += 1
        failing._a.value += 1
        failing._b.value += 1
        failing._initializer._frames += ("emitted",)
        failing._initializer._revision += 1

    failing._accept_frame = mutate_owned_frame_state
    failing_owner = Owner()
    failing_ticket = FullSessionRecordTicket(
        0, (raw.record_index, raw.start_offset, raw.end_offset, raw.encoded_sha256),
        ("1" * 64,), ("2" * 64,), object(), failing_owner,
    )
    failing._a, failing._b = _Group(), _Group()
    failing._pending = {7: {"sentinel": "pending"}}
    failing._deferred = SimpleNamespace(bucket=6, retries=2)
    before = _coordinator_state_bytes(failing)
    with pytest.raises(RuntimeError, match="post-frame"):
        failing.consume_record_ticket(failing_ticket)
    assert _coordinator_state_bytes(failing) == before


def _terminal_admission(branch, *, succeeded):
    return module.ContinuousAdmissionAudit(
        7, "1" * 64, "2" * 64, "3" * 64, 8,
        ("BSFC2CC", 7, 8), ("BSFC2CC", "BSFC2E4"), branch,
        True, "ACCEPTED", branch == "B_UWB", branch == "B_UWB",
        succeeded if branch == "B_UWB" else False,
        ("UWB_COMMIT_SUCCEEDED" if branch == "B_UWB"
         else "BASELINE_NO_UWB_COMMIT"), "4" * 64, "4" * 64, None,
    )


def _stage_successful_ab(coordinator, provenance="4" * 64):
    left = _terminal_admission("A_BASELINE", succeeded=False)
    right = _terminal_admission("B_UWB", succeeded=True)
    coordinator._a._admission_journal = (left,)
    coordinator._b._admission_journal = (right,)
    coordinator._publish_ab_transaction(
        module.PreparedGroupDisposition(provenance, "PREPARED_ADMISSION", left),
        module.PreparedGroupDisposition(provenance, "PREPARED_ADMISSION", right),
    )


def _two_event_record_ticket(events):
    class Owner:
        consumed = False

        def _deliver_record(self, ticket, consumer):
            if self.consumed:
                raise RuntimeError("replayed")
            self.consumed = True
            consumer(events)

    return FullSessionRecordTicket(
        0, (5, 10, 20, "5" * 64), ("6" * 64, "7" * 64),
        ("8" * 64, "9" * 64), object(), Owner(),
    )


def _native200_record_ticket(frames):
    raw = frames[0].raw_provenance
    raw_identity = (
        raw.record_index, raw.start_offset, raw.end_offset, raw.encoded_sha256,
    )

    class Owner:
        consumed = False

        def _deliver_record(self, ticket, consumer):
            if self.consumed:
                raise RuntimeError("replayed")
            self.consumed = True
            consumer(tuple(object() for _ in frames))

    count = len(frames)
    return FullSessionRecordTicket(
        0, raw_identity,
        tuple(f"{index + 1:064x}" for index in range(count)),
        tuple(f"{index + 101:064x}" for index in range(count)),
        object(), Owner(),
    )


def _continuous_event_record_ticket(events):
    raw = events[0].payload_owner.raw
    raw_identity = (
        raw.record_index, raw.start_offset, raw.end_offset, raw.encoded_sha256,
    )

    class Owner:
        consumed = False

        def _deliver_record(self, ticket, consumer):
            if self.consumed:
                raise RuntimeError("replayed")
            self.consumed = True
            consumer(events)

    return FullSessionRecordTicket(
        0, raw_identity,
        tuple(f"{index + 1:064x}" for index in range(len(events))),
        tuple(f"{index + 101:064x}" for index in range(len(events))),
        object(), Owner(),
    )


def test_authentic_body_prepared_row_failure_rolls_back_through_coordinator_and_retries(
    monkeypatch,
):
    from test_c2_full_session_body_pose import _authentic_body, _owned_state_bytes

    def setup():
        coordinator = _coordinator()
        body, producer, body_authority, _clock, events = _authentic_body(3)
        coordinator._body = body
        coordinator._FullSessionTenNodeABCoordinator__body_batch_authority = body_authority
        coordinator._a, coordinator._b = _Group(), _Group()
        coordinator._initializer = SimpleNamespace(
            _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
        )
        return coordinator, body, producer, events

    coordinator, body, producer, events = setup()
    clean, clean_body, _clean_producer, clean_events = setup()
    before_coordinator = _coordinator_state_bytes(coordinator)
    before_body = body._record_batch_snapshot(events[0].node_id)
    original = producer._publication_for_pelvis_event_with_row
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected coordinator consumer failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(producer, "_publication_for_pelvis_event_with_row", fail_second)
    with pytest.raises(RuntimeError, match="coordinator consumer failure"):
        coordinator.consume_record_ticket(_continuous_event_record_ticket(events))
    assert _coordinator_state_bytes(coordinator) == before_coordinator
    after_body = body._record_batch_snapshot(events[0].node_id)
    assert after_body.block is before_body.block
    for name in before_body.__dataclass_fields__:
        if name != "block":
            assert pickle.dumps(getattr(after_body, name), 5) == pickle.dumps(
                getattr(before_body, name), 5,
            )
    assert coordinator._record_batch_ab_stage is None
    assert coordinator._FullSessionTenNodeABCoordinator__active_record_transaction is None

    monkeypatch.setattr(producer, "_publication_for_pelvis_event_with_row", original)
    coordinator.consume_record_ticket(_continuous_event_record_ticket(events))
    clean.consume_record_ticket(_continuous_event_record_ticket(clean_events))
    assert _owned_state_bytes(body) == _owned_state_bytes(clean_body)
    assert _coordinator_state_bytes(coordinator) == _coordinator_state_bytes(clean)


def _real_record_coordinator(count, *, force_scalar=False):
    coordinator = _coordinator()
    coordinator._a, _rows, _availability = group_fixtures._real_owner_fixture()
    coordinator._b, _rows, _availability = group_fixtures._real_owner_fixture()
    authority = coordinator._FullSessionTenNodeABCoordinator__group_record_authority
    coordinator._a.bind_native200_record_coordinator(authority)
    coordinator._b.bind_native200_record_coordinator(authority)
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    coordinator._FullSessionTenNodeABCoordinator__body_batch_authority = object()
    events = group_fixtures._next_native200_events(coordinator._a, count)
    frames = tuple(event.payload_owner for event in events)

    class Body:
        def ingest_record_batch(self, _events, *, authority, consumer):
            for frame in frames:
                consumer(frame)

    coordinator._body = Body()
    coordinator._validate_record_batch = lambda *_args: "IMU"
    if force_scalar:
        coordinator._a.native200_record_batch_classification = (
            lambda _events: "other_fail_closed"
        )
        coordinator._b.native200_record_batch_classification = (
            lambda _events: "other_fail_closed"
        )
    return coordinator, frames, _native200_record_ticket(frames)


def _real_gap_record_coordinator():
    coordinator, _frames, _ticket = _real_record_coordinator(1)
    prior = coordinator._a._composition.history.frames[-1]
    engine = coordinator._a._composition.engine
    raw_digest = "34e7e0d445e7c92126a8691dba433264aad610d3fb1be864097b3677a77b0d4f"
    frames = []
    first_timer = prior.source_timer_us + 38_914
    for index in range(10):
        timer_us = first_timer + index * 5_000
        frame = group_fixtures._owned_frame(
            engine, group_fixtures._engine_and_packet_for_frame(), timer_us,
            prior.publication_revision + index + 1,
        )
        frames.append(replace(
            frame,
            raw_provenance=group_fixtures.RawByteProvenance(
                1_224_192, 223_666_569, 223_666_747, raw_digest, index,
            ),
            digest="",
        ))
    frames = tuple(frames)

    class Body:
        def ingest_record_batch(self, _events, *, authority, consumer):
            for frame in frames:
                consumer(frame)

    coordinator._body = Body()
    coordinator._last_pelvis_timer = prior.source_timer_us
    coordinator._last_pelvis_ns = prior.source_global_ns
    return coordinator, frames, _native200_record_ticket(frames)


@pytest.mark.parametrize("stage", ("after_a", "after_b", "before_finalize"))
def test_gap_endpoint_record_failure_restores_every_owner(monkeypatch, stage):
    coordinator, _frames, ticket = _real_gap_record_coordinator()
    before = _coordinator_state_bytes(coordinator)
    if stage in {"after_a", "after_b"}:
        branch = coordinator._a if stage == "after_a" else coordinator._b
        original = branch.commit_continuous_event

        def commit_then_fail(prepared):
            original(prepared)
            raise RuntimeError(f"injected {stage}")

        monkeypatch.setattr(branch, "commit_continuous_event", commit_then_fail)
    else:
        monkeypatch.setattr(
            coordinator, "_validate_record_transaction_finalization",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("injected before_finalize")
            ),
        )

    with pytest.raises(RuntimeError, match=f"injected {stage}"):
        coordinator.consume_record_ticket(ticket)

    assert _coordinator_state_bytes(coordinator) == before
    assert coordinator._FullSessionTenNodeABCoordinator__active_record_transaction is None
    assert coordinator._a._ContinuousGroupEpochOwner__record_capability is None
    assert coordinator._b._ContinuousGroupEpochOwner__record_capability is None


def test_gap_endpoint_rejects_raw_identity_tamper_and_swapped_branch_plan():
    coordinator, frames, ticket = _real_gap_record_coordinator()
    transaction = coordinator._begin_record_transaction(ticket.raw_identity)
    gap = coordinator._gap_event(frames[0])
    endpoint = coordinator._frame_event(frames[0])
    with pytest.raises(RuntimeError, match="stale or foreign native200 record transaction"):
        coordinator._atomic_ab_gap_native200(
            gap, endpoint, record_transaction=transaction,
            raw_identity=(ticket.raw_identity[0] + 1, *ticket.raw_identity[1:]),
        )
    prepared_a = coordinator._a.prepare_continuous_event(
        gap, commit_uwb=False,
        _record_capability=transaction.a_capability,
        _gap_endpoint_event=endpoint,
    )
    with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN_CONTINUOUS_GROUP_PLAN"):
        coordinator._b.commit_continuous_event(prepared_a)
    coordinator._revoke_record_transaction(transaction)


def _real_pending_record_coordinator(
    count, *, link_delay_us, materialize_diagnostics=(),
    a_materialize_diagnostics=None, b_materialize_diagnostics=None,
):
    coordinator = _coordinator()
    coordinator._a, rows, availability = group_fixtures._real_owner_fixture()
    coordinator._b, _right_rows, _right_availability = (
        group_fixtures._real_owner_fixture()
    )
    authority = coordinator._FullSessionTenNodeABCoordinator__group_record_authority
    coordinator._a.bind_native200_record_coordinator(authority)
    coordinator._b.bind_native200_record_coordinator(authority)
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    coordinator._FullSessionTenNodeABCoordinator__body_batch_authority = object()
    branch_sequences = (
        materialize_diagnostics if a_materialize_diagnostics is None
        else a_materialize_diagnostics,
        materialize_diagnostics if b_materialize_diagnostics is None
        else b_materialize_diagnostics,
    )
    for owner, diagnostics in zip((coordinator._a, coordinator._b), branch_sequences):
        if diagnostics:
            composition = owner._composition
            actual_materialize = composition.materialize_group
            outcomes = iter(diagnostics)

            def sequenced_materialize(*args, _actual=actual_materialize,
                                     _outcomes=outcomes, **kwargs):
                diagnostic = next(_outcomes, None)
                if diagnostic is not None:
                    raise group_fixtures.StalePoseLinkUnavailable(diagnostic)
                return _actual(*args, **kwargs)

            composition.materialize_group = sequenced_materialize
    delayed = tuple(
        replace(
            row, strobe_us=row.strobe_us + link_delay_us,
            frame_us=row.frame_us + link_delay_us,
        )
        for row in rows
    )
    for row in sorted(delayed, key=lambda item: item.node):
        clock = coordinator._a._composition.engine.static.clocks[row.node]
        common_ns = int(round(clock.link_time_ns(
            event_boot_epoch=row.boot, strobe_us=row.strobe_us,
            t_round_us=0.0,
        )))
        coordinator._atomic_ab(ContinuousEvent(
            f"pending:{row.node}:{row.sequence}:{link_delay_us}", "UWB", 0,
            "00_initial_still", common_ns,
            availability + link_delay_us * 1_000,
            row.node, row.boot, "B306_TIMER2", "a" * 64, "b" * 64,
            "c" * 64, "host-only", row,
            uwb_timer2=group_fixtures.UwbTimer2Fields(
                row.strobe_us, row.frame_us,
            ),
        ), uwb=True)
    assert coordinator._a.journal[-1].reason == "STALE_POSE_LINK_DEFERRED"
    assert coordinator._b.journal[-1].reason == "STALE_POSE_LINK_DEFERRED"
    events = group_fixtures._next_native200_events(coordinator._a, count)
    frames = tuple(event.payload_owner for event in events)

    class Body:
        def ingest_record_batch(self, _events, *, authority, consumer):
            for frame in frames:
                consumer(frame)

    coordinator._body = Body()
    coordinator._validate_record_batch = lambda *_args: "IMU"
    return coordinator, frames, _native200_record_ticket(frames)


def _consume_record_all_scalar(coordinator, frames, ticket):
    snapshot = coordinator._record_batch_snapshot()
    transaction = coordinator._begin_record_transaction(ticket.raw_identity)
    coordinator._record_batch_ab_stage = []
    try:
        for frame in frames:
            coordinator._accept_frame(
                frame, _record_transaction=transaction,
                _record_raw_identity=ticket.raw_identity,
                _routing_accounted=True,
            )
        coordinator._imus += len(frames)
        for digest in ticket.sensor_identity_digests:
            coordinator._chain.update(bytes.fromhex(digest))
        coordinator._events += len(frames)
        coordinator._close_record_transaction(
            transaction, raw_identity=ticket.raw_identity,
        )
    except BaseException:
        coordinator._record_batch_ab_stage = None
        if coordinator._FullSessionTenNodeABCoordinator__active_record_transaction is transaction:
            coordinator._revoke_record_transaction(transaction)
        coordinator._restore_record_batch_snapshot(snapshot)
        raise
    staged = tuple(coordinator._record_batch_ab_stage)
    coordinator._record_batch_ab_stage = None
    coordinator._publish_ab_transactions(staged)


def _real_record_state(coordinator, *, include_routing=True):
    branches = []
    for owner in (coordinator._a, coordinator._b):
        engine = owner._composition.engine
        branches.append((
            engine.root.publication_token().digest,
            engine.pose.publication_token().digest,
            owner._composition.history.revision,
            tuple(frame.digest for frame in owner._composition.history.frames),
            owner._revision, tuple(sorted(owner.counters.items())),
            owner.journal, owner.admission_journal,
        ))
    return (
        tuple(branches), coordinator._events, coordinator._imus,
        coordinator._uwbs, coordinator._frames, coordinator._dropouts,
        coordinator._chain.digest(), coordinator._ab_transaction_journal,
        coordinator._ab_transaction_total, coordinator._last_pelvis_timer,
        coordinator._last_pelvis_ns,
        None if not include_routing else (
            coordinator._batched_records, coordinator._batched_frames,
            coordinator._scalar_frames, coordinator._unrouted_frames,
            tuple(sorted(coordinator._scalar_fallbacks.items())),
        ),
    )


@pytest.mark.parametrize("count", (1, 10, 16))
def test_real_record_route_batch_is_exact_to_scalar_and_closes_capabilities(count):
    batched, _frames, batch_ticket = _real_record_coordinator(count)
    scalar, _frames, scalar_ticket = _real_record_coordinator(
        count, force_scalar=True,
    )
    batched.consume_record_ticket(batch_ticket)
    scalar.consume_record_ticket(scalar_ticket)
    assert _real_record_state(
        batched, include_routing=False,
    ) == _real_record_state(scalar, include_routing=False)
    assert (batched._batched_records, batched._batched_frames,
            batched._scalar_frames) == (1, count, 0)
    assert batched._batched_frames + batched._scalar_frames == batched._frames
    assert (scalar._batched_records, scalar._batched_frames,
            scalar._scalar_frames) == (0, 0, count)
    assert batched._a._ContinuousGroupEpochOwner__record_capability is None
    assert batched._b._ContinuousGroupEpochOwner__record_capability is None
    assert batched._FullSessionTenNodeABCoordinator__active_record_transaction is None
    assert batched._record_batch_ab_stage is None


@pytest.mark.parametrize("count", (1, 10, 16))
def test_real_record_route_batches_before_incomplete_pending_deadline(count):
    batched, frames, batch_ticket = _real_record_coordinator(count)
    scalar, scalar_frames, scalar_ticket = _real_record_coordinator(
        count, force_scalar=True,
    )

    def seed_incomplete(coordinator, first_frame):
        pending = group_fixtures._uwb(
            group_fixtures.NODES[0], first_frame.source_global_ns - 1_000_000,
        )
        for owner in (coordinator._a, coordinator._b):
            prepared = owner.prepare_continuous_event(pending, commit_uwb=False)
            owner.commit_continuous_event(prepared)

    seed_incomplete(batched, frames[0])
    seed_incomplete(scalar, scalar_frames[0])
    events = tuple(batched._frame_event(frame) for frame in frames)
    assert batched._a.native200_record_batch_safe(events)
    assert batched._b.native200_record_batch_safe(events)
    batched.consume_record_ticket(batch_ticket)
    scalar.consume_record_ticket(scalar_ticket)
    assert _real_record_state(
        batched, include_routing=False,
    ) == _real_record_state(scalar, include_routing=False)
    assert (batched._batched_records, batched._batched_frames,
            batched._scalar_frames) == (1, count, 0)
    assert batched._batched_frames + batched._scalar_frames == batched._frames
    assert len(batched._a._pending) == len(batched._b._pending) == 1


def test_batch_classifier_does_not_duplicate_authoritative_prepare(monkeypatch):
    coordinator, _frames, ticket = _real_record_coordinator(16)
    calls = {"a": 0, "b": 0}
    for branch, owner in (("a", coordinator._a), ("b", coordinator._b)):
        composition = owner._composition
        monkeypatch.setattr(
            composition, "validate_native200_batch_chronology",
            lambda _events: (_ for _ in ()).throw(
                AssertionError("classifier entered detailed validation")
            ),
            raising=False,
        )
        original = composition.prepare_native200_batch

        def counted(events, *, _branch=branch, _original=original):
            calls[_branch] += 1
            return _original(events)

        monkeypatch.setattr(composition, "prepare_native200_batch", counted)
    coordinator.consume_record_ticket(ticket)
    assert calls == {"a": 1, "b": 1}


def test_record_frame_router_falls_back_at_pending_and_discontinuous_boundaries(monkeypatch):
    coordinator, frames, _ticket = _real_record_coordinator(2)
    batched = []
    scalar = []
    monkeypatch.setattr(
        coordinator, "_atomic_ab_native200_batch",
        lambda events, **_kwargs: batched.append(tuple(events)),
    )
    monkeypatch.setattr(
        coordinator, "_accept_frame",
        lambda frame, **_kwargs: scalar.append(frame),
    )
    monkeypatch.setattr(
        coordinator, "_atomic_ab_gap_native200",
        lambda _gap, endpoint, **_kwargs: scalar.append(endpoint.payload_owner),
    )
    transaction = coordinator._begin_record_transaction(
        coordinator._frame_raw_identity(frames[0]),
    )
    final_availability = frames[-1].imu_sample.availability_time_s * 1e9
    expired_bucket = int(
        (final_availability - group_fixtures.ASSEMBLY_HORIZON_NS)
        // group_fixtures.EPOCH_PERIOD_NS
    ) - 1
    coordinator._a._pending[expired_bucket] = ()
    coordinator._b._pending[expired_bucket] = ()
    coordinator._accept_record_frames(
        frames, record_transaction=transaction,
        raw_identity=coordinator._frame_raw_identity(frames[0]),
    )
    assert batched == [] and scalar == list(frames)
    assert coordinator._scalar_fallbacks == {"deadline_or_after": 2}
    coordinator._a._pending.clear(); coordinator._b._pending.clear()
    coordinator._last_pelvis_timer = frames[0].source_timer_us - 6_000
    coordinator._last_pelvis_ns = frames[0].source_global_ns - 6_000_000
    coordinator._accept_record_frames(
        frames[:1], record_transaction=transaction,
        raw_identity=coordinator._frame_raw_identity(frames[0]),
    )
    assert scalar[-1] is frames[0]
    assert coordinator._scalar_fallbacks == {
        "deadline_or_after": 2, "malformed_or_discontinuity": 1,
    }
    coordinator._close_record_transaction(
        transaction, raw_identity=coordinator._frame_raw_identity(frames[0]),
    )


def test_record_frame_router_uses_scalar_on_ab_batch_safety_disagreement(
    monkeypatch,
):
    coordinator, frames, _ticket = _real_record_coordinator(2)
    batched = []
    scalar = []
    monkeypatch.setattr(
        coordinator, "_atomic_ab_native200_batch",
        lambda events, **_kwargs: batched.append(tuple(events)),
    )
    monkeypatch.setattr(
        coordinator, "_accept_frame",
        lambda frame, **_kwargs: scalar.append(frame),
    )
    monkeypatch.setattr(
        coordinator._a, "native200_record_batch_classification",
        lambda _events: "batch_safe",
    )
    monkeypatch.setattr(
        coordinator._b, "native200_record_batch_classification",
        lambda _events: "deadline_or_after",
    )
    raw_identity = coordinator._frame_raw_identity(frames[0])
    transaction = coordinator._begin_record_transaction(raw_identity)
    coordinator._accept_record_frames(
        frames, record_transaction=transaction, raw_identity=raw_identity,
    )
    assert batched == []
    assert scalar == list(frames)
    assert coordinator._scalar_fallbacks == {"A_B_disagreement": 2}
    coordinator._close_record_transaction(
        transaction, raw_identity=raw_identity,
    )


def test_record_frame_router_counts_complete_pending_fallback(monkeypatch):
    coordinator, frames, _ticket = _real_record_coordinator(2)
    scalar = []
    monkeypatch.setattr(
        coordinator, "_accept_frame",
        lambda frame, **_kwargs: scalar.append(frame),
    )
    future_bucket = frames[-1].source_global_ns // group_fixtures.EPOCH_PERIOD_NS
    coordinator._a._pending[future_bucket] = (object(),) * 10
    coordinator._b._pending[future_bucket] = (object(),) * 10
    raw_identity = coordinator._frame_raw_identity(frames[0])
    transaction = coordinator._begin_record_transaction(raw_identity)
    coordinator._accept_record_frames(
        frames, record_transaction=transaction, raw_identity=raw_identity,
    )
    assert scalar == list(frames)
    assert coordinator._scalar_fallbacks == {"complete_pending": 2}
    coordinator._close_record_transaction(
        transaction, raw_identity=raw_identity,
    )


@pytest.mark.parametrize("resolved_after", (1, 2, 4, None))
def test_complete_pending_reclassifies_only_the_untouched_suffix(
    monkeypatch, resolved_after,
):
    coordinator, frames, _ticket = _real_record_coordinator(4)
    scalar = []
    batches = []

    def classification(_events):
        return (
            "batch_safe"
            if resolved_after is not None and len(scalar) >= resolved_after
            else "complete_pending"
        )

    monkeypatch.setattr(
        coordinator._a, "native200_record_batch_classification", classification,
    )
    monkeypatch.setattr(
        coordinator._b, "native200_record_batch_classification", classification,
    )
    monkeypatch.setattr(
        coordinator, "_accept_frame",
        lambda frame, **_kwargs: scalar.append(frame),
    )
    monkeypatch.setattr(
        coordinator, "_atomic_ab_native200_batch",
        lambda events, **_kwargs: batches.append(tuple(events)),
    )
    raw_identity = coordinator._frame_raw_identity(frames[0])
    transaction = coordinator._begin_record_transaction(raw_identity)
    coordinator._accept_record_frames(
        frames, record_transaction=transaction, raw_identity=raw_identity,
    )
    scalar_count = len(frames) if resolved_after is None else resolved_after
    assert scalar == list(frames[:scalar_count])
    assert [len(batch) for batch in batches] == (
        [] if scalar_count == len(frames) else [len(frames) - scalar_count]
    )
    assert coordinator._scalar_fallbacks == {
        "complete_pending": scalar_count,
    }
    assert coordinator._scalar_frames == scalar_count
    assert coordinator._batched_frames == len(frames) - scalar_count
    assert coordinator._batched_records == int(scalar_count < len(frames))
    coordinator._close_record_transaction(
        transaction, raw_identity=raw_identity,
    )


def test_b_only_drift_pending_scalarizes_until_resolved_then_rebatches_suffix(
    monkeypatch,
):
    coordinator, frames, _ticket = _real_record_coordinator(6)
    scalar = []
    batches = []

    monkeypatch.setattr(
        coordinator._a, "native200_record_batch_classification",
        lambda _events: "batch_safe",
    )
    monkeypatch.setattr(
        coordinator._b, "native200_record_batch_classification",
        lambda _events: "complete_pending" if len(scalar) < 3 else "batch_safe",
    )
    monkeypatch.setattr(
        coordinator, "_accept_frame",
        lambda frame, **_kwargs: scalar.append(frame),
    )
    monkeypatch.setattr(
        coordinator, "_atomic_ab_native200_batch",
        lambda events, **_kwargs: batches.append(tuple(events)),
    )
    raw_identity = coordinator._frame_raw_identity(frames[0])
    transaction = coordinator._begin_record_transaction(raw_identity)
    coordinator._accept_record_frames(
        frames, record_transaction=transaction, raw_identity=raw_identity,
    )
    assert scalar == list(frames[:3])
    assert batches == [tuple(
        coordinator._frame_event(frame) for frame in frames[3:]
    )]
    assert coordinator._scalar_fallbacks == {"complete_pending": 3}
    assert coordinator._scalar_frames == 3
    assert coordinator._batched_frames == 3
    assert coordinator._batched_records == 1
    coordinator._close_record_transaction(
        transaction, raw_identity=raw_identity,
    )


def test_real_b_only_pending_consumes_first_eligible_once_and_preserves_a():
    mixed, mixed_frames, mixed_ticket = _real_record_coordinator(16)
    scalar, scalar_frames, scalar_ticket = _real_record_coordinator(16)
    all_batch, _batch_frames, batch_ticket = _real_record_coordinator(16)

    def queue_b(owner):
        drift = group_fixtures._enable_consensus_drift(owner)
        _fixture, rows, availability_ns = group_fixtures._real_owner_fixture()
        composition = owner._composition
        materialized = composition.materialize_group(
            rows, availability_global_ns=availability_ns,
            member_region_identities=("00_initial_still",) * 10,
            evidence_class="ACTION_EVIDENCE",
        )
        admission = composition.prepare_admission(
            materialized.packet, materialized.epoch,
        )
        consensus = composition.prepare_consensus_admission(admission)
        composition.commit_admission(admission)
        composition.commit_consensus_admission(consensus)
        assert drift.pending_count == 1
        return drift, availability_ns

    mixed_drift, availability_ns = queue_b(mixed._b)
    scalar_drift, scalar_availability_ns = queue_b(scalar._b)
    assert scalar_availability_ns == availability_ns
    a_before = mixed._a._composition.engine.root.publication_token().digest

    mixed.consume_record_ticket(mixed_ticket)
    _consume_record_all_scalar(scalar, scalar_frames, scalar_ticket)
    all_batch.consume_record_ticket(batch_ticket)

    assert mixed_drift.pending_count == scalar_drift.pending_count == 0
    assert mixed_drift._state.last_consumed_native200_time_s == (
        scalar_drift._state.last_consumed_native200_time_s
    )
    assert mixed_drift._state.last_consumed_native200_time_s >= (
        availability_ns * 1e-9
    )
    assert mixed._scalar_frames == 9
    assert mixed._batched_frames == 7
    assert mixed._batched_records == 1
    assert mixed._scalar_fallbacks == {"complete_pending": 9}
    assert _real_record_state(
        mixed, include_routing=False,
    ) == _real_record_state(scalar, include_routing=False)
    assert group_fixtures._composition_fingerprint(
        mixed._a._composition,
    ) == group_fixtures._composition_fingerprint(all_batch._a._composition)
    assert mixed._a._composition.engine.root.publication_token().digest != a_before


def test_complete_pending_suffix_disagreement_stays_scalar(monkeypatch):
    coordinator, frames, _ticket = _real_record_coordinator(4)
    scalar = []
    batches = []
    monkeypatch.setattr(
        coordinator._a, "native200_record_batch_classification",
        lambda _events: "complete_pending" if not scalar else "batch_safe",
    )
    monkeypatch.setattr(
        coordinator._b, "native200_record_batch_classification",
        lambda _events: "complete_pending" if not scalar else "deadline_or_after",
    )
    monkeypatch.setattr(
        coordinator, "_accept_frame",
        lambda frame, **_kwargs: scalar.append(frame),
    )
    monkeypatch.setattr(
        coordinator, "_atomic_ab_native200_batch",
        lambda events, **_kwargs: batches.append(tuple(events)),
    )
    raw_identity = coordinator._frame_raw_identity(frames[0])
    transaction = coordinator._begin_record_transaction(raw_identity)
    coordinator._accept_record_frames(
        frames, record_transaction=transaction, raw_identity=raw_identity,
    )
    assert scalar == list(frames)
    assert batches == []
    assert coordinator._scalar_fallbacks == {
        "complete_pending": 1, "A_B_disagreement": 3,
    }
    coordinator._close_record_transaction(
        transaction, raw_identity=raw_identity,
    )


def test_mixed_scalar_batch_failure_restores_record_and_routing(monkeypatch):
    coordinator, _frames, ticket = _real_record_coordinator(10)
    before = _coordinator_state_bytes(coordinator)

    def classification(_events):
        return "complete_pending" if coordinator._frames == 0 else "batch_safe"

    monkeypatch.setattr(
        coordinator._a, "native200_record_batch_classification",
        lambda _events: "batch_safe",
    )
    monkeypatch.setattr(
        coordinator._b, "native200_record_batch_classification", classification,
    )

    def fail_after_a(events, **kwargs):
        transaction = coordinator._validate_record_transaction(
            kwargs["record_transaction"], raw_identity=kwargs["raw_identity"],
        )
        prepared = transaction.a_owner.prepare_native200_batch(
            events, _record_capability=transaction.a_capability,
        )
        transaction.a_owner.commit_native200_batch(prepared)
        raise RuntimeError("injected mixed-route B failure")

    monkeypatch.setattr(coordinator, "_atomic_ab_native200_batch", fail_after_a)
    with pytest.raises(RuntimeError, match="injected mixed-route B failure"):
        coordinator.consume_record_ticket(ticket)
    assert _coordinator_state_bytes(coordinator) == before
    assert coordinator._a._ContinuousGroupEpochOwner__record_capability is None
    assert coordinator._b._ContinuousGroupEpochOwner__record_capability is None


@pytest.mark.parametrize(
    ("count", "link_delay_us", "expected_scalar", "resolved"),
    ((4, 5_000, 3, True), (4, 10_000, 4, True),
     (4, 20_000, 4, False), (4, 25_000, 4, False)),
)
def test_real_pending_group_suffix_route_matches_all_scalar(
    count, link_delay_us, expected_scalar, resolved,
):
    mixed, _frames, mixed_ticket = _real_pending_record_coordinator(
        count, link_delay_us=link_delay_us,
    )
    scalar, scalar_frames, scalar_ticket = _real_pending_record_coordinator(
        count, link_delay_us=link_delay_us,
    )
    mixed.consume_record_ticket(mixed_ticket)
    _consume_record_all_scalar(scalar, scalar_frames, scalar_ticket)
    assert _real_record_state(
        mixed, include_routing=False,
    ) == _real_record_state(scalar, include_routing=False)
    assert mixed._scalar_fallbacks == {"complete_pending": expected_scalar}
    assert mixed._scalar_frames == expected_scalar
    assert mixed._batched_frames == count - expected_scalar
    assert bool(mixed._a._pending) is not resolved
    assert bool(mixed._b._pending) is not resolved
    assert mixed._a.journal == scalar._a.journal
    assert mixed._b.journal == scalar._b.journal
    assert mixed._a.admission_journal == scalar._a.admission_journal
    assert mixed._b.admission_journal == scalar._b.admission_journal


def _typed_stale(*, terminal):
    return group_fixtures.StalePoseLinkDiagnostic(
        10_005_001, 5_000_000, group_fixtures.POSE_AGE_LIMIT_NS + 1,
        terminal,
    )


@pytest.mark.parametrize("terminal", (False, True))
def test_real_pending_typed_first_boundary_resolution_or_terminal_rejection(
    terminal,
):
    retryable = _typed_stale(terminal=False)
    sequence = (
        (retryable, _typed_stale(terminal=True))
        if terminal else (None, retryable)
    )
    mixed, _frames, mixed_ticket = _real_pending_record_coordinator(
        4, link_delay_us=0 if terminal else 1,
        materialize_diagnostics=sequence,
    )
    scalar, scalar_frames, scalar_ticket = _real_pending_record_coordinator(
        4, link_delay_us=0 if terminal else 1,
        materialize_diagnostics=sequence,
    )
    mixed.consume_record_ticket(mixed_ticket)
    _consume_record_all_scalar(scalar, scalar_frames, scalar_ticket)
    assert _real_record_state(
        mixed, include_routing=False,
    ) == _real_record_state(scalar, include_routing=False)
    expected_scalar = 1 if terminal else 2
    assert (mixed._scalar_frames, mixed._batched_frames,
            mixed._batched_records) == (expected_scalar, 4 - expected_scalar, 1)
    assert mixed._scalar_fallbacks == {"complete_pending": expected_scalar}
    for owner in (mixed._a, mixed._b):
        assert owner._pending == {}
        if terminal:
            assert owner.journal[-1].reason == "STALE_POSE_LINK_REJECTED"
            assert owner.counters["STALE_POSE_LINK_DEFERRED"] == 1
            assert owner.counters["STALE_POSE_LINK_REJECTED"] == 1
            assert owner.admission_journal == ()
            assert owner._finalized_watermark is not None
        else:
            assert owner.journal[-1].reason == "OBSOLETE_NATIVE200_SOURCE_PAIR"
            assert owner.counters["STALE_POSE_LINK_DEFERRED"] == 2
            assert owner.counters["OBSOLETE_NATIVE200_SOURCE_PAIR"] == 1
            assert owner.journal[-1].obsolete_native200_source_pair is not None
            assert owner.admission_journal == ()
            assert owner._finalized_watermark is not None


def test_real_pending_one_branch_resolution_disagreement_is_scalar():
    retryable = _typed_stale(terminal=False)
    coordinator, _frames, ticket = _real_pending_record_coordinator(
        4, link_delay_us=1,
        a_materialize_diagnostics=(None, None),
        b_materialize_diagnostics=(None, None, retryable),
    )
    before = _coordinator_state_bytes(coordinator)
    with pytest.raises(RuntimeError, match="A/B raw-group provenance diverged"):
        coordinator.consume_record_ticket(ticket)
    assert _coordinator_state_bytes(coordinator) == before
    assert coordinator._a._ContinuousGroupEpochOwner__record_capability is None
    assert coordinator._b._ContinuousGroupEpochOwner__record_capability is None
    assert coordinator._FullSessionTenNodeABCoordinator__active_record_transaction is None
    assert coordinator._record_batch_ab_stage is None


def test_real_pending_mixed_suffix_failure_restores_outer_record(monkeypatch):
    retryable = _typed_stale(terminal=False)
    coordinator, _frames, ticket = _real_pending_record_coordinator(
        4, link_delay_us=1, materialize_diagnostics=(None, retryable),
    )
    before = _coordinator_state_bytes(coordinator)
    actual = coordinator._atomic_ab_native200_batch

    def fail_after_a(events, **kwargs):
        transaction = coordinator._validate_record_transaction(
            kwargs["record_transaction"], raw_identity=kwargs["raw_identity"],
        )
        prepared = transaction.a_owner.prepare_native200_batch(
            events, _record_capability=transaction.a_capability,
        )
        transaction.a_owner.commit_native200_batch(prepared)
        raise RuntimeError("INJECTED_REAL_SUFFIX_BATCH_FAILURE")

    monkeypatch.setattr(coordinator, "_atomic_ab_native200_batch", fail_after_a)
    with pytest.raises(RuntimeError, match="INJECTED_REAL_SUFFIX_BATCH_FAILURE"):
        coordinator.consume_record_ticket(ticket)
    assert _coordinator_state_bytes(coordinator) == before
    assert coordinator._a._ContinuousGroupEpochOwner__record_capability is None
    assert coordinator._b._ContinuousGroupEpochOwner__record_capability is None


def test_record_external_and_prebootstrap_frames_are_unrouted():
    legacy, frames, _ticket = _real_record_coordinator(1)
    legacy._accept_frame(frames[0])
    assert (legacy._frames, legacy._unrouted_frames,
            legacy._batched_frames, legacy._scalar_frames) == (1, 1, 0, 0)

    prebootstrap, frames, _ticket = _real_record_coordinator(1)
    accepted = []
    prebootstrap._a = prebootstrap._b = None
    prebootstrap._initializer = SimpleNamespace(
        accept_native200=accepted.append,
    )
    prebootstrap._deferred = None
    prebootstrap._accept_frame(frames[0])
    assert accepted == [frames[0]]
    assert (prebootstrap._frames, prebootstrap._unrouted_frames,
            prebootstrap._batched_frames, prebootstrap._scalar_frames) == (
                1, 1, 0, 0,
            )


@pytest.mark.parametrize("failure_site", ("mid_root", "hinge"))
def test_real_record_batch_failure_restores_both_branches_and_revokes_capability(
    monkeypatch, failure_site,
):
    coordinator, frames, ticket = _real_record_coordinator(10)
    before = _real_record_state(coordinator)
    engine = coordinator._b._composition.engine
    if failure_site == "mid_root":
        actual = engine.add_imu
        calls = 0

        def fail_after_mutation(sample):
            nonlocal calls
            calls += 1
            result = actual(sample)
            if calls == 3:
                raise RuntimeError("INJECTED_MID_ROOT_BATCH_FAILURE")
            return result

        monkeypatch.setattr(engine, "add_imu", fail_after_mutation)
        message = "INJECTED_MID_ROOT_BATCH_FAILURE"
    else:
        temporal = engine.pose._CausalArticulatedPose__hinge_temporal_owner
        actual = temporal._commit_from_pose
        calls = 0

        def fail_hinge(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("INJECTED_HINGE_BATCH_FAILURE")
            return actual(*args, **kwargs)

        monkeypatch.setattr(temporal, "_commit_from_pose", fail_hinge)
        message = "INJECTED_HINGE_BATCH_FAILURE"
    with pytest.raises(RuntimeError, match=message):
        coordinator.consume_record_ticket(ticket)
    assert _real_record_state(coordinator) == before
    assert coordinator._a._ContinuousGroupEpochOwner__record_capability is None
    assert coordinator._b._ContinuousGroupEpochOwner__record_capability is None
    assert coordinator._FullSessionTenNodeABCoordinator__active_record_transaction is None
    assert coordinator._record_batch_ab_stage is None


def test_real_record_batch_service_p95_is_within_native_record_budget():
    warmup, _frames, warmup_ticket = _real_record_coordinator(16)
    warmup.consume_record_ticket(warmup_ticket)
    samples = []
    for _attempt in range(20):
        coordinator, frames, ticket = _real_record_coordinator(16)
        started = time.perf_counter()
        coordinator.consume_record_ticket(ticket)
        elapsed = time.perf_counter() - started
        samples.append(elapsed)
    p95 = sorted(samples)[18]
    print("N16_RECORD_SAMPLES_S=" + ",".join(f"{value:.9f}" for value in samples))
    print(f"N16_RECORD_P95_S={p95:.9f}")
    assert p95 <= 0.072, samples
    assert p95 / 16 <= 0.0045, samples


@pytest.mark.parametrize("count", (1, 10, 16))
def test_postbootstrap_imu_record_has_one_outer_ab_snapshot_and_no_frame_snapshots(count):
    coordinator = _coordinator()
    coordinator._a, left = group_fixtures._owner()
    coordinator._b, right = group_fixtures._owner()
    coordinator._a.bind_native200_record_coordinator(
        coordinator._FullSessionTenNodeABCoordinator__group_record_authority,
    )
    coordinator._b.bind_native200_record_coordinator(
        coordinator._FullSessionTenNodeABCoordinator__group_record_authority,
    )
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    calls = {"a": 0, "b": 0}
    left_snapshot, right_snapshot = left.snapshot, right.snapshot

    def count_left():
        calls["a"] += 1
        return left_snapshot()

    def count_right():
        calls["b"] += 1
        return right_snapshot()

    left.snapshot = count_left
    right.snapshot = count_right
    coordinator._record_batch_snapshot()
    raw_identity = (1, 2, 3, "4" * 64)
    transaction = coordinator._begin_record_transaction(raw_identity)
    for index in range(count):
        coordinator._atomic_ab(
            group_fixtures._imu((index + 1) * 5_000_000, (index + 1) * 5_000_000 + 20_000),
            uwb=False, _record_transaction=transaction,
            _record_raw_identity=raw_identity,
        )
    gap = ContinuousEvent(
        "gap:record", "GAP", -1, "UNASSIGNED_INTER_ACTION_GAP",
        (count + 2) * 5_000_000, (count + 2) * 5_000_000,
        group_fixtures.NODES[0], 0, "B306_TIMER2", "a" * 64,
        "b" * 64, "c" * 64, "", object(),
        gap_start_global_ns=(count + 1) * 5_000_000,
        gap_covariance_growth=0.005,
        region_id="UNASSIGNED_INTER_ACTION_GAP",
    )
    coordinator._atomic_ab(
        gap, uwb=False, _record_transaction=transaction,
        _record_raw_identity=raw_identity,
    )
    coordinator._close_record_transaction(transaction, raw_identity=raw_identity)
    assert calls == {"a": 1, "b": 1}
    assert left.native_revision == right.native_revision == count
    with pytest.raises(RuntimeError, match="stale or foreign"):
        coordinator._atomic_ab(
            group_fixtures._imu((count + 1) * 5_000_000,
                                (count + 1) * 5_000_000 + 20_000),
            uwb=False, _record_transaction=transaction,
            _record_raw_identity=raw_identity,
        )
    replacement = coordinator._begin_record_transaction((5, 6, 7, "8" * 64))
    with pytest.raises(RuntimeError, match="stale or foreign"):
        coordinator._atomic_ab(
            group_fixtures._imu((count + 1) * 5_000_000,
                                (count + 1) * 5_000_000 + 20_000),
            uwb=False, _record_transaction=replacement,
            _record_raw_identity=raw_identity,
        )
    coordinator._close_record_transaction(
        replacement, raw_identity=(5, 6, 7, "8" * 64),
    )

    coordinator._atomic_ab(
        group_fixtures._imu((count + 1) * 5_000_000,
                            (count + 1) * 5_000_000 + 20_000),
        uwb=False,
    )
    # The direct/legacy path intentionally retains both its A/B transaction
    # snapshot and each branch's local composition snapshot.
    assert calls == {"a": 4, "b": 4}
    coordinator._atomic_ab(
        group_fixtures._uwb(group_fixtures.NODES[0],
                            (count + 3) * 5_000_000),
        uwb=True,
    )
    assert calls == {"a": 6, "b": 6}


def test_real_record_issues_exactly_one_owner_delta_and_no_legacy_full_snapshot(
    monkeypatch,
):
    coordinator, _frames, ticket = _real_record_coordinator(16)
    calls = {"a": 0, "b": 0}
    for label, owner in (("a", coordinator._a), ("b", coordinator._b)):
        composition_snapshot = owner._composition.snapshot

        def count_composition(*, _label=label, _snapshot=composition_snapshot):
            calls[_label] += 1
            return _snapshot()

        monkeypatch.setattr(owner._composition, "snapshot", count_composition)
        monkeypatch.setattr(
            owner, "continuous_snapshot",
            lambda: (_ for _ in ()).throw(
                AssertionError("legacy full owner snapshot must not run")
            ),
        )
    coordinator.consume_record_ticket(ticket)
    assert calls == {"a": 1, "b": 1}
    assert coordinator._a._ContinuousGroupEpochOwner__record_capability is None
    assert coordinator._b._ContinuousGroupEpochOwner__record_capability is None


def test_postbootstrap_record_later_frame_failure_restores_outer_ab_state(monkeypatch):
    coordinator = _coordinator()
    coordinator._a, left = group_fixtures._owner()
    coordinator._b, right = group_fixtures._owner()
    coordinator._a.bind_native200_record_coordinator(
        coordinator._FullSessionTenNodeABCoordinator__group_record_authority,
    )
    coordinator._b.bind_native200_record_coordinator(
        coordinator._FullSessionTenNodeABCoordinator__group_record_authority,
    )
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    coordinator._FullSessionTenNodeABCoordinator__body_batch_authority = object()

    class Body:
        def ingest_record_batch(self, events, *, authority, consumer):
            for event in events:
                consumer(event)

    coordinator._body = Body()
    coordinator._accept_frame = lambda event, **kwargs: coordinator._atomic_ab(
        event, uwb=False, **kwargs,
    )
    monkeypatch.setattr(coordinator, "_validate_record_batch", lambda *_: "IMU")
    original_commit = right.commit_native200
    commits = 0

    def fail_second(prepared):
        nonlocal commits
        commits += 1
        original_commit(prepared)
        if commits == 2:
            raise RuntimeError("injected later native200 commit failure")

    right.commit_native200 = fail_second
    before_a = coordinator._a.continuous_snapshot()
    before_b = coordinator._b.continuous_snapshot()
    before_chain = coordinator._chain.digest()
    events = (
        group_fixtures._imu(5_000_000, 5_020_000),
        group_fixtures._imu(10_000_000, 10_020_000),
    )
    with pytest.raises(RuntimeError, match="later native200"):
        coordinator.consume_record_ticket(_two_event_record_ticket(events))
    assert coordinator._a.continuous_snapshot() == before_a
    assert coordinator._b.continuous_snapshot() == before_b
    assert coordinator._chain.digest() == before_chain
    assert coordinator._events == coordinator._imus == 0
    assert coordinator._FullSessionTenNodeABCoordinator__active_record_transaction is None


def test_nested_record_stage_rejects_before_capability_mint(monkeypatch):
    coordinator = _coordinator()
    coordinator._a, _ = group_fixtures._owner()
    coordinator._b, _ = group_fixtures._owner()
    coordinator._a.bind_native200_record_coordinator(
        coordinator._FullSessionTenNodeABCoordinator__group_record_authority,
    )
    coordinator._b.bind_native200_record_coordinator(
        coordinator._FullSessionTenNodeABCoordinator__group_record_authority,
    )
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    coordinator._record_batch_ab_stage = []
    monkeypatch.setattr(coordinator, "_validate_record_batch", lambda *_: "IMU")
    with pytest.raises(RuntimeError, match="nested record-batch"):
        coordinator.consume_record_ticket(_two_event_record_ticket((object(), object())))
    assert coordinator._FullSessionTenNodeABCoordinator__active_record_transaction is None
    assert coordinator._a._ContinuousGroupEpochOwner__record_capability is None
    assert coordinator._b._ContinuousGroupEpochOwner__record_capability is None


def test_prebootstrap_imu_record_retains_legacy_path(monkeypatch):
    coordinator = _coordinator()
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    coordinator._FullSessionTenNodeABCoordinator__body_batch_authority = object()
    observed = []

    class Body:
        def ingest_record_batch(self, events, *, authority, consumer):
            observed.extend(events)

    coordinator._body = Body()
    monkeypatch.setattr(coordinator, "_validate_record_batch", lambda *_: "IMU")
    coordinator.consume_record_ticket(_two_event_record_ticket((object(), object())))
    assert len(observed) == 2
    assert coordinator._FullSessionTenNodeABCoordinator__active_record_transaction is None


def test_record_cleanup_preserves_primary_failure_and_always_restores(monkeypatch):
    coordinator = _coordinator()
    coordinator._a, _ = group_fixtures._owner()
    coordinator._b, _ = group_fixtures._owner()
    authority = coordinator._FullSessionTenNodeABCoordinator__group_record_authority
    coordinator._a.bind_native200_record_coordinator(authority)
    coordinator._b.bind_native200_record_coordinator(authority)
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    coordinator._FullSessionTenNodeABCoordinator__body_batch_authority = object()

    class Body:
        def ingest_record_batch(self, events, *, authority, consumer):
            for event in events:
                consumer(event)

    coordinator._body = Body()
    monkeypatch.setattr(coordinator, "_validate_record_batch", lambda *_: "IMU")
    before_a = coordinator._a.continuous_snapshot()
    before_b = coordinator._b.continuous_snapshot()
    coordinator._accept_frame = lambda event, **kwargs: coordinator._atomic_ab(
        event, uwb=False, **kwargs,
    )
    original_close = coordinator._a._close_native200_record_capability

    def close_then_fail(capability, *, authority):
        original_close(capability, authority=authority)
        raise RuntimeError("injected capability close failure")

    coordinator._a._close_native200_record_capability = close_then_fail
    events = (
        group_fixtures._imu(5_000_000, 5_020_000),
        group_fixtures._imu(10_000_000, 10_020_000),
    )
    with pytest.raises(RuntimeError, match="capability close failure"):
        coordinator.consume_record_ticket(_two_event_record_ticket(events))
    assert coordinator._a.continuous_snapshot() == before_a
    assert coordinator._b.continuous_snapshot() == before_b
    assert coordinator._FullSessionTenNodeABCoordinator__active_record_transaction is None
    assert coordinator._a._ContinuousGroupEpochOwner__record_capability is None
    assert coordinator._b._ContinuousGroupEpochOwner__record_capability is None


def test_outer_record_batch_publishes_staged_success_only_after_all_events(monkeypatch):
    coordinator = _coordinator()
    coordinator._a, coordinator._b = _Group(), _Group()
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    coordinator._FullSessionTenNodeABCoordinator__body_batch_authority = object()

    class Body:
        def ingest_record_batch(self, events, *, authority, consumer):
            for event in events:
                consumer(event)

    coordinator._body = Body()
    observed = []

    def accept(frame):
        observed.append(frame)
        if len(observed) == 1:
            _stage_successful_ab(coordinator)

    coordinator._accept_frame = accept
    monkeypatch.setattr(coordinator, "_validate_record_batch", lambda *_: "IMU")
    ticket = _two_event_record_ticket((object(), object()))
    coordinator.consume_record_ticket(ticket)
    assert len(observed) == 2
    assert len(coordinator._ab_transaction_journal) == 1
    assert coordinator._ab_transaction_total == 1
    pair = coordinator._ab_transaction_journal[0]
    assert pair.b.admission.outcome == "UWB_COMMIT_SUCCEEDED"
    metrics = factory._WholeSessionMetrics(0.0)
    metrics.consume_authoritative_admissions(SimpleNamespace(
        audit=lambda: SimpleNamespace(ab_transaction_journal=(pair,),
                                      ab_transaction_total=1),
    ))
    assert metrics.admission_pairs == 1
    assert metrics.b_commit_succeeded == 1


def test_outer_record_batch_later_event_failure_replaces_staged_success_with_rollback(
    monkeypatch,
):
    coordinator = _coordinator()
    coordinator._a, coordinator._b = _Group(), _Group()
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    coordinator._FullSessionTenNodeABCoordinator__body_batch_authority = object()

    class Body:
        def ingest_record_batch(self, events, *, authority, consumer):
            for event in events:
                consumer(event)

    coordinator._body = Body()
    calls = 0

    def accept(_frame):
        nonlocal calls
        calls += 1
        if calls == 1:
            _stage_successful_ab(coordinator)
            coordinator._frames += 1
        else:
            raise RuntimeError("later event failed")

    coordinator._accept_frame = accept
    monkeypatch.setattr(coordinator, "_validate_record_batch", lambda *_: "IMU")
    before = _coordinator_state_bytes(coordinator)
    with pytest.raises(RuntimeError, match="later event failed"):
        coordinator.consume_record_ticket(_two_event_record_ticket((object(), object())))
    assert _coordinator_state_bytes(coordinator) == before
    assert len(coordinator._ab_transaction_journal) == 1
    pair = coordinator._ab_transaction_journal[0]
    assert pair.a.reason == pair.b.reason == "OUTER_RECORD_BATCH_ROLLED_BACK"
    assert pair.a.admission is pair.b.admission is None
    metrics = factory._WholeSessionMetrics(0.0)
    metrics.consume_authoritative_admissions(SimpleNamespace(
        audit=lambda: SimpleNamespace(ab_transaction_journal=(pair,),
                                      ab_transaction_total=1),
    ))
    assert metrics.admission_pairs == 1
    assert metrics.b_commit_succeeded == 0
    assert metrics.rejection_reasons["b"] == {"OUTER_RECORD_BATCH_ROLLED_BACK": 1}


def test_outer_record_audit_publish_failure_restores_then_emits_one_rollback(
    monkeypatch,
):
    coordinator = _coordinator()
    coordinator._a, coordinator._b = _Group(), _Group()
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )
    coordinator._FullSessionTenNodeABCoordinator__body_batch_authority = object()

    class Body:
        def ingest_record_batch(self, events, *, authority, consumer):
            for event in events:
                consumer(event)

    coordinator._body = Body()
    coordinator._accept_frame = lambda _frame: _stage_successful_ab(coordinator)
    monkeypatch.setattr(coordinator, "_validate_record_batch", lambda *_: "IMU")
    publish = coordinator._publish_ab_transactions
    calls = 0

    def fail_first_after_mutation(rows):
        nonlocal calls
        calls += 1
        publish(rows)
        if calls == 1:
            raise RuntimeError("injected paired audit publication failure")

    monkeypatch.setattr(coordinator, "_publish_ab_transactions", fail_first_after_mutation)
    with pytest.raises(RuntimeError, match="paired audit publication failure"):
        coordinator.consume_record_ticket(
            _two_event_record_ticket((object(), object())),
        )
    assert calls == 2
    assert coordinator._ab_transaction_total == 1
    assert len(coordinator._ab_transaction_journal) == 1
    pair = coordinator._ab_transaction_journal[0]
    assert pair.a.reason == pair.b.reason == "OUTER_RECORD_BATCH_ROLLED_BACK"


def _finalize_validation_fixture():
    from test_c2_full_session_body_pose import _authentic_body

    coordinator = _coordinator()
    body, _producer, body_authority, _clock, events = _authentic_body(2)
    coordinator._body = body
    coordinator._FullSessionTenNodeABCoordinator__body_batch_authority = body_authority
    coordinator._a, _ = group_fixtures._owner()
    coordinator._b, _ = group_fixtures._owner()
    authority = coordinator._FullSessionTenNodeABCoordinator__group_record_authority
    coordinator._a.bind_native200_record_coordinator(authority)
    coordinator._b.bind_native200_record_coordinator(authority)
    coordinator._initializer = SimpleNamespace(
        _frames=(), _preworld_pose_omissions=0, _revision=0, _located=None,
    )

    def accept_record_frames(_frames, **kwargs):
        for index in range(2):
            coordinator._atomic_ab(
                group_fixtures._imu(
                    (index + 1) * 5_000_000,
                    (index + 1) * 5_000_000 + 20_000,
                ),
                uwb=False,
                _record_transaction=kwargs["record_transaction"],
                _record_raw_identity=kwargs["raw_identity"],
            )
        _stage_successful_ab(coordinator)

    coordinator._accept_record_frames = accept_record_frames
    return coordinator, body, events, _continuous_event_record_ticket(events)


def _assert_body_delta_equal(body, before):
    after = body._record_batch_snapshot(before.node)
    assert after.block is before.block
    for name in before.__dataclass_fields__:
        if name != "block":
            assert pickle.dumps(getattr(after, name), protocol=5) == pickle.dumps(
                getattr(before, name), protocol=5,
            )


def test_a_finalize_validation_failure_retains_b_delta_and_restores_all(monkeypatch):
    coordinator, body, events, ticket = _finalize_validation_fixture()
    before = _coordinator_state_bytes(coordinator)
    before_body = body._record_batch_snapshot(events[0].node_id)
    b_rollback = coordinator._b._rollback_native200_record_capability
    observed = []

    def observe_b_rollback(capability, *, authority):
        observed.append((capability.state.status, capability.state.delta is not None))
        return b_rollback(capability, authority=authority)

    monkeypatch.setattr(
        coordinator._b, "_rollback_native200_record_capability", observe_b_rollback,
    )
    monkeypatch.setattr(
        coordinator._a, "_validate_finalize_native200_record_capability",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected A finalize validation failure")
        ),
    )
    with pytest.raises(RuntimeError, match="A finalize validation failure"):
        coordinator.consume_record_ticket(ticket)
    assert observed == [("CLOSED", True)]
    assert _coordinator_state_bytes(coordinator) == before
    _assert_body_delta_equal(body, before_body)
    assert coordinator._ab_transaction_total == 1
    assert len(coordinator._ab_transaction_journal) == 1
    pair = coordinator._ab_transaction_journal[0]
    assert pair.a.reason == pair.b.reason == "OUTER_RECORD_BATCH_ROLLED_BACK"


def test_body_finalize_validation_failure_restores_all_and_preserves_primary(
    monkeypatch,
):
    coordinator, body, events, ticket = _finalize_validation_fixture()
    before = _coordinator_state_bytes(coordinator)
    before_body = body._record_batch_snapshot(events[0].node_id)
    monkeypatch.setattr(
        body, "_validate_finalize_record_batch_capability",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected body finalize validation failure")
        ),
    )
    with pytest.raises(RuntimeError, match="body finalize validation failure") as caught:
        coordinator.consume_record_ticket(ticket)
    assert str(caught.value) == "injected body finalize validation failure"
    assert _coordinator_state_bytes(coordinator) == before
    _assert_body_delta_equal(body, before_body)
    assert coordinator._ab_transaction_total == 1
    assert len(coordinator._ab_transaction_journal) == 1
    pair = coordinator._ab_transaction_journal[0]
    assert pair.a.reason == pair.b.reason == "OUTER_RECORD_BATCH_ROLLED_BACK"


def test_whole_session_metrics_consume_initial_and_over_64_authoritative_pairs():
    def row(branch, index, *, fallback=False):
        accepted_reason = (
            "ACCEPTED_ROOT_FALLBACK_ARTICULATED_REJECTED:IK_FAILED"
            if fallback else "ACCEPTED"
        )
        digest = f"{index:064x}"
        diagnostic = SimpleNamespace(solver_reason="IK_FAILED") if fallback else None
        return SimpleNamespace(
            branch=branch, packet_digest=digest, epoch_digest=digest,
            candidate_digest=digest, bucket=index, source_sequence=index,
            source_identity=("pelvis", index), trusted_partition=("n0", "n1"),
            prepared_accepted=True, prepared_reason=accepted_reason,
            commit_intent=branch == "B_UWB",
            commit_attempted=branch == "B_UWB",
            commit_succeeded=branch == "B_UWB",
            outcome=("BASELINE_NO_UWB_COMMIT" if branch == "A_BASELINE"
                     else "UWB_COMMIT_SUCCEEDED"),
            pre_pose_digest="e" * 64,
            result_pose_digest=("e" * 64 if fallback else "d" * 64),
            diagnostic_digest=("f" * 64 if fallback else None),
            diagnostic=diagnostic,
        )

    journals = {"a": [], "b": [], "pairs": [], "total": 0}
    coordinator = SimpleNamespace(audit=lambda: SimpleNamespace(
        a_admission_journal=tuple(journals["a"]),
        b_admission_journal=tuple(journals["b"]),
        ab_transaction_journal=tuple(journals["pairs"]),
        ab_transaction_total=journals["total"],
    ))
    metrics = factory._WholeSessionMetrics(0.0)
    for index in range(70):
        journals["a"].append(row("A_BASELINE", index, fallback=index == 0))
        journals["b"].append(row("B_UWB", index, fallback=index == 0))
        journals["pairs"].append(SimpleNamespace(
            provenance_digest=f"{index + 100:064x}",
            a=SimpleNamespace(reason="PREPARED_ADMISSION",
                              admission=journals["a"][-1]),
            b=SimpleNamespace(reason="PREPARED_ADMISSION",
                              admission=journals["b"][-1]),
        ))
        journals["total"] += 1
        journals["a"][:] = journals["a"][-64:]
        journals["b"][:] = journals["b"][-64:]
        journals["pairs"][:] = journals["pairs"][-64:]
        metrics.consume_authoritative_admissions(coordinator)
    assert metrics.admission_pairs == 70
    assert metrics.accepted == {"a": 70, "b": 70}
    assert metrics.b_commit_intent == metrics.b_commit_attempted == 70
    assert metrics.b_commit_succeeded == 70
    assert metrics.root_fallback_accepted == {"b": 1}
    assert metrics.articulated_rejected_root_fallback_accepted == {"b": 1}
    assert metrics.obsolete_source_pair_root_fallback_accepted == {}
    assert metrics.primary_articulated_accepted == {"b": 69}

    divergent = factory._WholeSessionMetrics(0.0)
    journals["pairs"][:] = [SimpleNamespace(
        provenance_digest="bad", a=object(), b=object(),
    )]
    journals["total"] = 1
    with pytest.raises(RuntimeError, match="provenance is invalid"):
        divergent.consume_authoritative_admissions(coordinator)


def test_metrics_classify_obsolete_fallback_and_gate_branch_pose_before_accounting():
    def admission(branch, reason, *, result_pose_digest="6" * 64):
        return SimpleNamespace(
            branch=branch, packet_digest="1" * 64, epoch_digest="2" * 64,
            candidate_digest="3" * 64, bucket=1, source_sequence=2,
            source_identity=("pelvis", 3), trusted_partition=("n0", "n1"),
            prepared_accepted=True, prepared_reason=reason,
            commit_intent=branch == "B_UWB",
            commit_attempted=branch == "B_UWB",
            commit_succeeded=branch == "B_UWB",
            outcome=("BASELINE_NO_UWB_COMMIT" if branch == "A_BASELINE"
                     else "UWB_COMMIT_SUCCEEDED"),
            pre_pose_digest="6" * 64,
            result_pose_digest=result_pose_digest,
            diagnostic_digest="4" * 64, diagnostic=SimpleNamespace(kind="obsolete"),
        )

    reason = "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
    pair = SimpleNamespace(
        provenance_digest="5" * 64,
        a=SimpleNamespace(reason="PREPARED_ADMISSION",
                          admission=admission("A_BASELINE", reason)),
        b=SimpleNamespace(reason="PREPARED_ADMISSION",
                          admission=admission("B_UWB", reason)),
    )
    coordinator = SimpleNamespace(audit=lambda: SimpleNamespace(
        ab_transaction_journal=(pair,), ab_transaction_total=1,
    ))
    metrics = factory._WholeSessionMetrics(0.0)
    metrics.consume_authoritative_admissions(coordinator)
    assert metrics.accepted == {"a": 1, "b": 1}
    assert metrics.root_fallback_accepted == {"b": 1}
    assert metrics.obsolete_source_pair_root_fallback_accepted == {"b": 1}
    assert metrics.articulated_rejected_root_fallback_accepted == {}
    assert metrics.root_fallback_reasons == {reason: 1}
    assert metrics.pose_publication_checks == {
        "OBSOLETE_NATIVE200_SOURCE_PAIR_ROOT_FALLBACK": 1,
    }
    assert metrics.latest_pose_publication_check == {
        "classification": "OBSOLETE_NATIVE200_SOURCE_PAIR_ROOT_FALLBACK",
        "pre_pose_digest": "6" * 64,
        "result_pose_digest": "6" * 64,
        "pose_unchanged": True,
    }

    mismatched_pair = SimpleNamespace(
        provenance_digest="5" * 64, a=pair.a,
        b=SimpleNamespace(
            reason="PREPARED_ADMISSION",
            admission=admission("B_UWB", reason, result_pose_digest="7" * 64),
        ),
    )
    rejected = factory._WholeSessionMetrics(0.0)
    with pytest.raises(RuntimeError, match="changed pose/joint state"):
        rejected.consume_authoritative_admissions(SimpleNamespace(
            audit=lambda: SimpleNamespace(
                ab_transaction_journal=(mismatched_pair,), ab_transaction_total=1,
            ),
        ))
    assert rejected.admission_pairs == 0
    assert rejected.accepted == {}

    primary_pair = SimpleNamespace(
        provenance_digest="8" * 64,
        a=SimpleNamespace(reason="PREPARED_ADMISSION",
                          admission=admission("A_BASELINE", "ACCEPTED")),
        b=SimpleNamespace(
            reason="PREPARED_ADMISSION",
            admission=admission("B_UWB", "ACCEPTED", result_pose_digest="9" * 64),
        ),
    )
    primary = factory._WholeSessionMetrics(0.0)
    primary.consume_authoritative_admissions(SimpleNamespace(
        audit=lambda: SimpleNamespace(
            ab_transaction_journal=(primary_pair,), ab_transaction_total=1,
        ),
    ))
    assert primary.primary_articulated_accepted == {"b": 1}
    assert primary.latest_pose_publication_check["pose_unchanged"] is False


def test_whole_session_metrics_absolute_cursor_counts_repeated_provenance_once_per_occurrence():
    disposition = SimpleNamespace(reason="STALE_POSE_LINK_DEFERRED", admission=None)
    repeated = SimpleNamespace(provenance_digest="a" * 64,
                               a=disposition, b=disposition)
    unique = SimpleNamespace(provenance_digest="b" * 64,
                             a=disposition, b=disposition)
    state = {"journal": [repeated], "total": 1}
    coordinator = SimpleNamespace(audit=lambda: SimpleNamespace(
        ab_transaction_journal=tuple(state["journal"]),
        ab_transaction_total=state["total"],
    ))
    metrics = factory._WholeSessionMetrics(0.0)
    metrics.consume_authoritative_admissions(coordinator)
    state["journal"].append(repeated); state["total"] = 2
    metrics.consume_authoritative_admissions(coordinator)
    state["journal"].append(unique); state["total"] = 3
    metrics.consume_authoritative_admissions(coordinator)
    metrics.consume_authoritative_admissions(coordinator)
    assert metrics.admission_pairs == 3
    assert metrics._last_ab_transaction_total == 3
    assert metrics.rejection_reasons["a"] == {"STALE_POSE_LINK_DEFERRED": 3}


def test_whole_session_metrics_absolute_cursor_fails_on_overflow_regression_and_forgery():
    disposition = SimpleNamespace(reason="NO_ADMISSION", admission=None)
    pair = SimpleNamespace(provenance_digest="c" * 64,
                           a=disposition, b=disposition)

    overflow = factory._WholeSessionMetrics(0.0)
    with pytest.raises(RuntimeError, match="overflowed observer cursor"):
        overflow.consume_authoritative_admissions(SimpleNamespace(
            audit=lambda: SimpleNamespace(
                ab_transaction_journal=(pair,) * 64, ab_transaction_total=65,
            ),
        ))

    regression = factory._WholeSessionMetrics(0.0)
    current = {"journal": (pair,), "total": 1}
    coordinator = SimpleNamespace(audit=lambda: SimpleNamespace(
        ab_transaction_journal=current["journal"],
        ab_transaction_total=current["total"],
    ))
    regression.consume_authoritative_admissions(coordinator)
    current.update(journal=(), total=0)
    with pytest.raises(RuntimeError, match="counter regressed"):
        regression.consume_authoritative_admissions(coordinator)

    forged = factory._WholeSessionMetrics(0.0)
    with pytest.raises(RuntimeError, match="counter is invalid"):
        forged.consume_authoritative_admissions(SimpleNamespace(
            audit=lambda: SimpleNamespace(
                ab_transaction_journal=(pair,), ab_transaction_total=0,
            ),
        ))
