import copy
import hashlib
import json
import pickle
from decimal import Decimal, ROUND_HALF_EVEN
from types import SimpleNamespace

import numpy as np
import pytest
from dataclasses import replace
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    ContinuousEvent,
    ImuTimer2Fields,
    UwbTimer2Fields,
    canonical_clock_global_ns,
)
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
    ASSEMBLY_HORIZON_NS,
    CONTINUOUS_HINGE_RETENTION_CONTRACT,
    AuthoritativeContinuousGroupComposition,
    AuthoritativeContinuousHistoryOwner,
    AuthoritativeGroupSidecars,
    ContinuousGroupEpochOwner,
    GroupEpochMaterialization,
    LowLinkMeasurementUsabilityRejection,
    NATIVE200_HISTORY_CAPACITY,
    OwnedNative200HistoryFrame,
    StalePoseLinkDiagnostic,
    StalePoseLinkUnavailable,
    canonical_group_availability_time_s,
    canonical_epoch_bucket,
)
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import (
    EPOCH_PERIOD_NS,
    MAXIMUM_POSE_AGE_NS,
    group_epoch_times_ns,
)
from biospur_fusion.c2_uwb_root_world.u0 import UwbRow
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
    BoundGroupPacket,
    RobustCandidateMeasurementRejection,
)
from biospur_fusion.c2_uwb_root_world.continuous_consensus_drift import (
    ContinuousConsensusDriftOwner,
)
from biospur_fusion.root_r3.estimator import RootTranslationEdgeMode
from biospur_fusion.c2_uwb_root_world.split_fusion import (
    FixedLagConsensusDriftConfig,
)
from biospur_fusion.c2_articulated_biomechanics.hinge_temporal import (
    HingeTemporalEvidenceError,
    ObsoleteNative200SourcePair,
    ObsoleteNative200SourcePairDiagnostic,
)
import biospur_fusion.c2_uwb_root_world.owner_bound_async_worker as legacy_owner
import biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner as group_module
from biospur_fusion.c2_uwb_calibration.articulated_range import (
    SEGMENTS,
    corrected_proxy_points,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.ingest.events import RawByteProvenance
from biospur_fusion.root_r3.models import ImuSample, SystemMode


NODES = tuple(f"BSFC2{index:02d}" for index in range(10))
POSE_AGE_LIMIT_NS = int(MAXIMUM_POSE_AGE_NS)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _row(node, timer_us, *, links=8):
    valid = (1 << links) - 1
    return UwbRow(
        node, 0, timer_us, timer_us, timer_us, timer_us + 8,
        tuple(range(8)), tuple(2000 + x for x in range(8)),
        tuple(100 + x for x in range(8)), tuple(90 for _ in range(8)), valid,
    )


def _uwb(node, common_ns, *, links=8, availability_ns=None, region=None):
    timer_us = common_ns // 1000
    row = _row(node, timer_us, links=links)
    action_index = 0 if region is None else -1
    action_id = "00_initial_still" if region is None else region
    return ContinuousEvent(
        f"{node}:{timer_us}:{links}", "UWB", action_index, action_id,
        common_ns, common_ns + 20_000 if availability_ns is None else availability_ns,
        node, 0, "B306_TIMER2", "a" * 64, "b" * 64, "c" * 64,
        "host-only", row, uwb_timer2=UwbTimer2Fields(timer_us, timer_us + 8),
        region_id=region,
    )


def _imu(common_ns, availability_ns):
    timer_us = common_ns // 1000
    return ContinuousEvent(
        f"imu:{timer_us}", "IMU", 0, "00_initial_still", common_ns,
        availability_ns, NODES[0], 0, "B306_TIMER2", "a" * 64,
        "b" * 64, "c" * 64, "host-only", object(),
        imu_timer2=ImuTimer2Fields(timer_us - 5, timer_us),
    )


class Composition:
    def __init__(self, *, fail_commit=False, fail_gap_commit=False,
                 stale_pose=None, root_ns=None, obsolete_pair=None):
        self.estimator_revision = 0
        self.native_revision = 0
        self.committed = []
        self.fail_commit = fail_commit
        self.fail_gap_commit = fail_gap_commit
        self.stale_pose = stale_pose
        self.root_ns = root_ns
        self.obsolete_pair = obsolete_pair
        self.obsolete_fallback_authorizations = []

    def clone(self):
        return copy.deepcopy(self)

    def mutable_owner_tokens(self):
        return frozenset((id(self), id(self.committed)))

    def snapshot(self):
        return copy.deepcopy((self.estimator_revision, self.native_revision, self.committed))

    def restore(self, snapshot):
        self.estimator_revision, self.native_revision, self.committed = copy.deepcopy(snapshot)

    def prepare_native200(self, event):
        return (self.native_revision, event.event_id)

    def commit_native200(self, prepared):
        assert prepared[0] == self.native_revision
        self.native_revision += 1

    def prepare_gap(self, event):
        return ("gap", event.event_id)

    def commit_gap(self, prepared):
        self.committed.append(prepared)
        if self.fail_gap_commit:
            raise RuntimeError("injected gap commit")

    def materialize_group(self, rows, *, availability_global_ns, member_region_identities, evidence_class):
        if self.stale_pose is not None:
            raise StalePoseLinkUnavailable(self.stale_pose)
        assert type(availability_global_ns) is int
        availability_time_s = availability_global_ns * 1e-9
        manifest = {
            "nodes": [row.node for row in rows], "strobe": [row.strobe_us for row in rows],
            "availability_global_ns": availability_global_ns,
            "regions": member_region_identities,
            "class": evidence_class,
        }
        packet_digest = _digest({"packet": manifest})
        epoch_digest = _digest({"epoch": manifest})
        audit_digest = _digest({"audit": manifest})
        packet = SimpleNamespace(
            pose_links=tuple(range(80)), digest=packet_digest,
            event=SimpleNamespace(availability_time_s=availability_time_s),
        )
        epoch = SimpleNamespace(pose_token_digest="d" * 64)
        return GroupEpochMaterialization(packet, epoch, audit_digest, epoch_digest)

    def prepare_admission(
        self, packet, epoch, *,
        allow_obsolete_native200_root_fallback=False,
    ):
        self.obsolete_fallback_authorizations.append(
            allow_obsolete_native200_root_fallback
        )
        if self.obsolete_pair is not None:
            if not allow_obsolete_native200_root_fallback:
                raise ObsoleteNative200SourcePair(self.obsolete_pair)
        packet_ns = round(packet.event.availability_time_s * 1e9)
        if self.root_ns is not None and self.root_ns > packet_ns:
            raise ValueError("root publication is from the future")
        candidate = SimpleNamespace(
            causal_transaction=object(), public_candidate_digest=_digest({"packet": packet.digest}),
            prepared_result=SimpleNamespace(
                accepted=True,
                reason=("ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
                        if self.obsolete_pair is not None else "ACCEPTED"),
                pose_token_digest="d" * 64,
            ),
            pose_digest="d" * 64,
            packet_availability_ns=packet_ns,
            trusted_partition=tuple(NODES[:4]),
            articulated_rejection_diagnostic=None,
            robust_candidate_rejection_diagnostic=None,
            obsolete_native200_source_pair_diagnostic=(
                self.obsolete_pair
                if allow_obsolete_native200_root_fallback else None
            ),
        )
        return candidate

    def commit_admission(self, prepared):
        if self.fail_commit:
            raise RuntimeError("injected composition commit")
        self.estimator_revision += 1
        self.committed.append((prepared.public_candidate_digest,
                               prepared.packet_availability_ns))


def _owner(composition=None):
    composition = composition or Composition()
    return ContinuousGroupEpochOwner(
        composition=composition, static_nodes=NODES,
        acquired_action_ids=frozenset(("00_initial_still", "02_t_pose")),
    ), composition


def _complete(owner, *, base_ns=1_000_000, commit=True, region=None):
    prepared = None
    for index, node in enumerate(NODES):
        event = _uwb(node, base_ns + index * 1000, region=region)
        prepared = owner.prepare_continuous_event(event, commit_uwb=commit)
        owner.commit_continuous_event(prepared)
    return prepared


def test_complete_group_prepares_once_and_a_discards_b_commits_identical_candidate():
    seed, _ = _owner()
    left = seed.clone()
    right = seed.clone()
    _complete(left, commit=False)
    _complete(right, commit=True)
    left_comp = left._composition
    right_comp = right._composition
    assert left_comp.estimator_revision == 0
    assert right_comp.estimator_revision == 1
    assert left.journal[-1].candidate_digest == right.journal[-1].candidate_digest
    assert left.journal[-1].evidence_class == "ACTION_EVIDENCE"
    a_audit, b_audit = left.admission_journal[-1], right.admission_journal[-1]
    assert a_audit.branch == "A_BASELINE"
    assert a_audit.prepared_accepted is True
    assert a_audit.commit_intent is False
    assert not a_audit.commit_attempted and not a_audit.commit_succeeded
    assert a_audit.outcome == "BASELINE_NO_UWB_COMMIT"
    assert b_audit.branch == "B_UWB"
    assert b_audit.prepared_accepted is True
    assert b_audit.commit_intent and b_audit.commit_attempted
    assert b_audit.commit_succeeded and b_audit.outcome == "UWB_COMMIT_SUCCEEDED"
    assert a_audit.candidate_digest == b_audit.candidate_digest
    assert a_audit.packet_digest == b_audit.packet_digest
    assert a_audit.epoch_digest == b_audit.epoch_digest


def test_continuous_snapshot_restores_admission_journal_exactly():
    owner, _composition = _owner()
    before = owner.continuous_snapshot()
    _complete(owner, commit=True)
    assert owner.admission_journal[-1].outcome == "UWB_COMMIT_SUCCEEDED"
    owner.restore_continuous_snapshot(before)
    assert owner.continuous_snapshot() == before
    assert owner.admission_journal == ()


def test_prepared_measurement_rejection_is_inert_and_audited_without_commit():
    owner, composition = _owner()
    original_prepare = composition.prepare_admission

    def reject(packet, epoch):
        accepted = original_prepare(packet, epoch)
        return SimpleNamespace(
            causal_transaction=None,
            public_candidate_digest=accepted.public_candidate_digest,
            prepared_result=SimpleNamespace(
                accepted=False, reason="ARTICULATED_RANGE_REJECTED",
                pose_token_digest=accepted.pose_digest,
            ),
            pose_digest=accepted.pose_digest,
            trusted_partition=(NODES[0], NODES[1]),
            articulated_rejection_diagnostic=None,
        )

    composition.prepare_admission = reject
    before = composition.snapshot()
    _complete(owner, commit=True)
    assert composition.snapshot() == before
    audit = owner.admission_journal[-1]
    assert audit.prepared_accepted is False
    assert audit.prepared_reason == "ARTICULATED_RANGE_REJECTED"
    assert audit.commit_intent is True
    assert not audit.commit_attempted and not audit.commit_succeeded
    assert audit.outcome == "PREPARED_REJECTED_NO_COMMIT"


def test_native200_record_capability_is_owner_bound_one_shot_and_skips_snapshot():
    owner, composition = _owner()
    other, _ = _owner()
    authority = object()
    other_authority = object()
    owner.bind_native200_record_coordinator(authority)
    other.bind_native200_record_coordinator(other_authority)
    with pytest.raises(RuntimeError, match="already bound"):
        owner.bind_native200_record_coordinator(object())
    with pytest.raises(RuntimeError, match="foreign"):
        owner._issue_native200_record_capability(authority=object())
    calls = 0
    original_snapshot = composition.snapshot

    def counted_snapshot():
        nonlocal calls
        calls += 1
        return original_snapshot()

    composition.snapshot = counted_snapshot
    capability = owner._issue_native200_record_capability(authority=authority)
    with pytest.raises(RuntimeError, match="nested"):
        owner._issue_native200_record_capability(authority=authority)
    prepared = owner.prepare_continuous_event(
        _imu(5_000_000, 5_020_000), commit_uwb=False,
        _record_capability=capability,
    )
    owner.commit_continuous_event(prepared)
    # The record owner captures its single rollback delta at issuance; no
    # per-event composition snapshot is taken beneath that capability.
    assert calls == 1
    before_other = other.continuous_snapshot()
    with pytest.raises(RuntimeError, match="stale or foreign"):
        other.prepare_continuous_event(
            _imu(10_000_000, 10_020_000), commit_uwb=False,
            _record_capability=capability,
        )
    assert other.continuous_snapshot() == before_other

    owner._close_native200_record_capability(capability, authority=authority)
    with pytest.raises(RuntimeError, match="stale or foreign"):
        owner.prepare_continuous_event(
            _imu(10_000_000, 10_020_000), commit_uwb=False,
            _record_capability=capability,
        )
    replacement = owner._issue_native200_record_capability(authority=authority)
    with pytest.raises(RuntimeError, match="stale or foreign"):
        owner.prepare_continuous_event(
            _imu(10_000_000, 10_020_000), commit_uwb=False,
            _record_capability=capability,
        )
    with pytest.raises(RuntimeError, match="requires IMU chronology"):
        owner.prepare_continuous_event(
            _uwb(NODES[0], 10_000_000), commit_uwb=False,
            _record_capability=replacement,
        )
    retained = owner.prepare_continuous_event(
        _imu(10_000_000, 10_020_000), commit_uwb=False,
        _record_capability=replacement,
    )
    owner._close_native200_record_capability(replacement, authority=authority)
    before_retained = owner.continuous_snapshot()
    with pytest.raises(RuntimeError, match="stale or foreign"):
        owner.commit_continuous_event(retained)
    assert owner.continuous_snapshot() == before_retained
    calls = 0

    owner.commit_continuous_event(owner.prepare_continuous_event(
        _imu(10_000_000, 10_020_000), commit_uwb=False,
    ))
    assert calls == 1


def test_native200_record_delta_rolls_back_once_after_mutation():
    owner, _composition = _owner()
    authority = object()
    owner.bind_native200_record_coordinator(authority)
    before = owner.continuous_snapshot()
    capability = owner._issue_native200_record_capability(authority=authority)
    prepared = owner.prepare_continuous_event(
        _imu(5_000_000, 5_020_000), commit_uwb=False,
        _record_capability=capability,
    )
    owner.commit_continuous_event(prepared)
    owner._rollback_native200_record_capability(
        capability, authority=authority,
    )
    assert owner.continuous_snapshot() == before
    with pytest.raises(RuntimeError, match="stale or foreign"):
        owner._rollback_native200_record_capability(
            capability, authority=authority,
        )


def test_duplicate_tie_prefers_more_valid_links_then_earlier_strobe():
    owner, _ = _owner()
    first = _uwb(NODES[0], 2_000_000, links=4)
    owner.commit_continuous_event(owner.prepare_continuous_event(first, commit_uwb=False))
    better = _uwb(NODES[0], 2_001_000, links=8)
    owner.commit_continuous_event(owner.prepare_continuous_event(better, commit_uwb=False))
    equal_later = _uwb(NODES[0], 2_002_000, links=8)
    owner.commit_continuous_event(owner.prepare_continuous_event(equal_later, commit_uwb=False))
    pending = owner.continuous_snapshot()[1]
    selected = next(iter(pending.values()))[0]
    assert selected.row.strobe_us == better.uwb_timer2.strobe_timer2_us


def test_low_link_row_is_typed_inert_keeps_bucket_open_and_valid_replacement_recovers():
    owner, composition = _owner()
    base_ns = 1_000_000
    owner.commit_continuous_event(owner.prepare_continuous_event(
        _uwb(NODES[1], base_ns + 1_000, links=8), commit_uwb=True,
    ))
    pending_before = copy.deepcopy(owner.continuous_snapshot()[1])
    composition_before = composition.snapshot()
    low_event = _uwb(NODES[0], base_ns, links=3)
    low = owner.prepare_continuous_event(low_event, commit_uwb=True)
    assert owner.continuous_snapshot()[1] == pending_before
    assert composition.snapshot() == composition_before
    owner.commit_continuous_event(low)
    assert owner.continuous_snapshot()[1] == pending_before
    assert composition.snapshot() == composition_before
    audit = owner.journal[-1]
    assert audit.reason == "LOW_LINK_MEASUREMENT_REJECTED"
    assert audit.low_link_measurement == LowLinkMeasurementUsabilityRejection(
        low_event.event_id, NODES[0], 0, base_ns // 1_000,
        base_ns // 1_000, base_ns // 1_000, base_ns // 1_000 + 8,
        0, 0, "a" * 64, "b" * 64, "c" * 64, 3,
    )

    owner.commit_continuous_event(owner.prepare_continuous_event(
        _uwb(NODES[0], base_ns, links=4), commit_uwb=True,
    ))
    for index, node in enumerate(NODES[2:], start=2):
        owner.commit_continuous_event(owner.prepare_continuous_event(
            _uwb(node, base_ns + index * 1_000), commit_uwb=True,
        ))
    assert composition.estimator_revision == 1
    assert owner.journal[-1].reason == "PREPARED_COMPLETE_GROUP"


def test_low_link_rejection_journal_is_bounded():
    owner, _composition = _owner()
    for index in range(70):
        event = _uwb(NODES[index % 10], 1_000_000 + index, links=index % 4)
        owner.commit_continuous_event(
            owner.prepare_continuous_event(event, commit_uwb=True)
        )
    assert len(owner.journal) == 64
    assert owner.counters["LOW_LINK_MEASUREMENT_REJECTED"] == 70
    assert all(row.reason == "LOW_LINK_MEASUREMENT_REJECTED" for row in owner.journal)


def test_expired_partial_group_is_prepared_from_authoritative_availability_and_late_is_diagnostic():
    owner, composition = _owner()
    event = _uwb(NODES[0], 1_000_000)
    owner.commit_continuous_event(owner.prepare_continuous_event(event, commit_uwb=True))
    deadline = EPOCH_PERIOD_NS + ASSEMBLY_HORIZON_NS
    tick = _imu(10_000_000, deadline)
    owner.commit_continuous_event(owner.prepare_continuous_event(tick, commit_uwb=False))
    assert owner.journal[-1].reason == "PREPARED_COMPLETE_GROUP"
    assert owner.journal[-1].nodes == (NODES[0],)
    assert composition.estimator_revision == 1
    late = _uwb(NODES[1], 2_000_000, availability_ns=deadline + 1)
    owner.commit_continuous_event(owner.prepare_continuous_event(late, commit_uwb=True))
    assert owner.journal[-1].reason == "LATE_SEALED_BUCKET_ROW"


@pytest.mark.parametrize("count", (1, 4, 7))
def test_expired_runtime_partial_body_groups_prepare_once_without_discard(count):
    owner, composition = _owner()
    base_ns = 1_000_000
    for index, node in enumerate(NODES[:count]):
        prepared = owner.prepare_continuous_event(
            _uwb(node, base_ns + index * 1_000), commit_uwb=True,
        )
        owner.commit_continuous_event(prepared)
    assert composition.estimator_revision == 0
    deadline = EPOCH_PERIOD_NS + ASSEMBLY_HORIZON_NS
    tick = owner.prepare_continuous_event(
        _imu(10_000_000, deadline), commit_uwb=False,
    )
    owner.commit_continuous_event(tick)
    assert composition.estimator_revision == 1
    assert owner.journal[-1].reason == "PREPARED_COMPLETE_GROUP"
    assert owner.journal[-1].nodes == NODES[:count]
    assert owner.counters.get("INCOMPLETE_GROUP", 0) == 0


def test_expired_partial_a_is_inert_and_later_commit_failure_rolls_back():
    base_ns = 1_000_000
    deadline = EPOCH_PERIOD_NS + ASSEMBLY_HORIZON_NS
    baseline, baseline_composition = _owner()
    for index, node in enumerate(NODES[:4]):
        baseline.commit_continuous_event(baseline.prepare_continuous_event(
            _uwb(node, base_ns + index * 1_000), commit_uwb=False,
        ))
    baseline.commit_continuous_event(baseline.prepare_continuous_event(
        _imu(10_000_000, deadline), commit_uwb=False,
    ))
    assert baseline_composition.estimator_revision == 0
    assert baseline.admission_journal[-1].outcome == "BASELINE_NO_UWB_COMMIT"

    owner, composition = _owner(Composition(fail_commit=True))
    for index, node in enumerate(NODES[:4]):
        owner.commit_continuous_event(owner.prepare_continuous_event(
            _uwb(node, base_ns + index * 1_000), commit_uwb=True,
        ))
    before = owner.continuous_snapshot()
    prepared = owner.prepare_continuous_event(
        _imu(10_000_000, deadline), commit_uwb=False,
    )
    assert owner.continuous_snapshot() == before
    with pytest.raises(RuntimeError, match="injected composition commit"):
        owner.commit_continuous_event(prepared)
    after = owner.continuous_snapshot()
    assert after[:6] == before[:6]
    assert after[6][:-1] == before[6]
    assert after[6][-1].outcome == "EVENT_COMMIT_FAILED_ROLLED_BACK:RuntimeError"
    assert composition.estimator_revision == 0
    assert owner.admission_journal[-1].outcome == (
        "EVENT_COMMIT_FAILED_ROLLED_BACK:RuntimeError"
    )


def test_public_sparse_robust_rejection_is_inert_and_later_group_recovers():
    class SparseRejectComposition(Composition):
        reject = True

        def prepare_admission(
            self, packet, epoch, *,
            allow_obsolete_native200_root_fallback=False,
        ):
            accepted = super().prepare_admission(
                packet, epoch,
                allow_obsolete_native200_root_fallback=(
                    allow_obsolete_native200_root_fallback
                ),
            )
            if not self.reject:
                return accepted
            diagnostic = RobustCandidateMeasurementRejection(
                packet.digest, 0, "e" * 64, 0.001, 1_000_000,
                round(packet.event.availability_time_s * 1e9), 3, 4,
                NODES[:4], tuple((node, anchor) for node in NODES[:4]
                                  for anchor in range(4)),
                "REJECT_GEOMETRY", False, 2, "POSITIVE_INFINITY", 7, 3.5,
            )
            return SimpleNamespace(
                causal_transaction=None,
                public_candidate_digest=accepted.public_candidate_digest,
                prepared_result=SimpleNamespace(
                    accepted=False,
                    reason="REJECT_GEOMETRY",
                    pose_token_digest=accepted.pose_digest,
                ),
                pose_digest=accepted.pose_digest,
                trusted_partition=NODES[:4],
                articulated_rejection_diagnostic=None,
                robust_candidate_rejection_diagnostic=diagnostic,
            )

    composition = SparseRejectComposition()
    owner, _ = _owner(composition)
    for index, node in enumerate(NODES[:4]):
        owner.commit_continuous_event(owner.prepare_continuous_event(
            _uwb(node, 1_000_000 + index * 1_000), commit_uwb=True,
        ))
    estimator_before = composition.snapshot()
    deadline = EPOCH_PERIOD_NS + ASSEMBLY_HORIZON_NS
    owner.commit_continuous_event(owner.prepare_continuous_event(
        _uwb(
            NODES[4], EPOCH_PERIOD_NS + 1_000_000,
            availability_ns=deadline,
        ),
        commit_uwb=True,
    ))
    assert composition.snapshot() == estimator_before
    assert composition.estimator_revision == 0
    audit = owner.admission_journal[-1]
    assert audit.outcome == "PREPARED_REJECTED_NO_COMMIT"
    assert isinstance(audit.diagnostic, RobustCandidateMeasurementRejection)
    assert audit.trusted_partition == NODES[:4]

    composition.reject = False
    _complete(owner, base_ns=EPOCH_PERIOD_NS + 1_000_000, commit=True)
    assert composition.estimator_revision == 1
    assert owner.admission_journal[-1].outcome == "UWB_COMMIT_SUCCEEDED"


def test_gap_and_mixed_members_are_dynamic_only_and_never_action_evidence():
    region = "UNASSIGNED_INTER_ACTION_GAP"
    gap_owner, _ = _owner()
    _complete(gap_owner, base_ns=3_000_000, region=region)
    assert gap_owner.journal[-1].evidence_class == "DYNAMIC_ONLY"

    mixed, _ = _owner()
    for index, node in enumerate(NODES):
        event = _uwb(node, 4_000_000 + index * 1000, region=region if index else None)
        item = mixed.prepare_continuous_event(event, commit_uwb=False)
        mixed.commit_continuous_event(item)
    assert mixed.journal[-1].evidence_class == "DYNAMIC_ONLY"


def test_foreign_double_and_injected_commit_failure_preserve_owner_and_composition():
    owner, composition = _owner(Composition(fail_commit=True))
    for index, node in enumerate(NODES[:-1]):
        item = owner.prepare_continuous_event(_uwb(node, 5_000_000 + index * 1000), commit_uwb=True)
        owner.commit_continuous_event(item)
    before = owner.continuous_snapshot()
    final = owner.prepare_continuous_event(_uwb(NODES[-1], 5_009_000), commit_uwb=True)
    with pytest.raises(RuntimeError, match="injected"):
        owner.commit_continuous_event(final)
    after = owner.continuous_snapshot()
    assert after[:6] == before[:6]
    assert after[6][:-1] == before[6]
    assert after[6][-1].outcome == "EVENT_COMMIT_FAILED_ROLLED_BACK:RuntimeError"
    failed = owner.admission_journal[-1]
    assert failed.commit_intent and failed.commit_attempted
    assert not failed.commit_succeeded
    assert failed.outcome == "EVENT_COMMIT_FAILED_ROLLED_BACK:RuntimeError"
    other, _ = _owner()
    with pytest.raises(RuntimeError, match="FOREIGN"):
        other.commit_continuous_event(final)
    composition.fail_commit = False
    owner.commit_continuous_event(final)
    with pytest.raises(RuntimeError, match="STALE"):
        owner.commit_continuous_event(final)


def test_preparation_is_inert_and_outer_rollback_can_restore_after_group_commit():
    owner, composition = _owner()
    for index, node in enumerate(NODES[:-1]):
        item = owner.prepare_continuous_event(_uwb(node, 6_000_000 + index * 1000), commit_uwb=True)
        owner.commit_continuous_event(item)
    before = owner.continuous_snapshot()
    final = owner.prepare_continuous_event(_uwb(NODES[-1], 6_009_000), commit_uwb=True)
    assert owner.continuous_snapshot() == before
    owner.commit_continuous_event(final)
    assert composition.estimator_revision == 1
    owner.restore_continuous_snapshot(before)
    assert owner.continuous_snapshot() == before
    assert composition.estimator_revision == 0


def test_pending_capacity_and_row_inventory_are_bounded_without_flush_at_regions():
    owner, _ = _owner()
    for bucket in range(3):
        ns = bucket * EPOCH_PERIOD_NS + 1_000_000
        item = owner.prepare_continuous_event(_uwb(NODES[bucket], ns), commit_uwb=False)
        owner.commit_continuous_event(item)
    pending = owner.continuous_snapshot()[1]
    assert len(pending) <= 3
    assert sum(len(rows) for rows in pending.values()) <= 30


def test_stale_pose_exact_ns_limit_and_terminal_classification():
    history = object.__new__(AuthoritativeContinuousHistoryOwner)
    frame = SimpleNamespace(source_global_ns=1_000_000)
    history._retired_frames = []
    history._frames = [frame]
    assert history._strict_floor(1_000_000 + POSE_AGE_LIMIT_NS) is frame
    query = 1_000_000 + POSE_AGE_LIMIT_NS + 1
    with pytest.raises(StalePoseLinkUnavailable) as retryable:
        history._strict_floor(query)
    assert retryable.value.diagnostic == StalePoseLinkDiagnostic(
        query, 1_000_000, POSE_AGE_LIMIT_NS + 1, False,
    )
    history._frames.append(SimpleNamespace(source_global_ns=query))
    with pytest.raises(StalePoseLinkUnavailable) as terminal:
        history._strict_floor(query)
    assert terminal.value.diagnostic == replace(
        retryable.value.diagnostic, terminal=True,
    )


def test_retryable_stale_group_defers_until_committed_source_then_succeeds():
    diagnostic = StalePoseLinkDiagnostic(
        10_005_001, 5_000_000, POSE_AGE_LIMIT_NS + 1, False,
    )
    owner, composition = _owner(Composition(stale_pose=diagnostic))
    _complete(owner, base_ns=1_000_000, commit=True)
    assert owner.counters["STALE_POSE_LINK_DEFERRED"] == 1
    assert len(owner.continuous_snapshot()[1]) == 1
    first_source = _imu(20_000_000, 20_020_000)
    owner.commit_continuous_event(
        owner.prepare_continuous_event(first_source, commit_uwb=False)
    )
    assert composition.native_revision == 1
    assert composition.estimator_revision == 0
    composition.stale_pose = None
    second_source = _imu(25_000_000, 25_020_000)
    composition.root_ns = second_source.availability_global_ns
    owner.commit_continuous_event(
        owner.prepare_continuous_event(second_source, commit_uwb=False)
    )
    assert composition.native_revision == 2
    assert composition.estimator_revision == 1
    assert owner.continuous_snapshot()[1] == {}
    accepted = owner.journal[-1]
    assert accepted.original_group_availability_ns == 1_029_000
    assert accepted.effective_processing_availability_ns == 25_020_000
    assert accepted.retry_delay_ns == 23_991_000
    assert composition.committed[-1][1] == 25_020_000


def test_combined_retry_commits_admission_before_triggering_native200():
    class OrderedComposition(Composition):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.order = []

        def commit_admission(self, prepared):
            self.order.append("admission")
            super().commit_admission(prepared)

        def commit_native200(self, prepared):
            self.order.append("native200")
            super().commit_native200(prepared)

    diagnostic = StalePoseLinkDiagnostic(
        10_005_001, 5_000_000, POSE_AGE_LIMIT_NS + 1, False,
    )
    composition = OrderedComposition(stale_pose=diagnostic)
    owner, _ = _owner(composition)
    _complete(owner, base_ns=1_000_000, commit=True)
    first = owner.prepare_continuous_event(
        _imu(20_000_000, 20_020_000), commit_uwb=False,
    )
    owner.commit_continuous_event(first)
    assert composition.order == ["native200"]

    composition.stale_pose = None
    trigger = owner.prepare_continuous_event(
        _imu(25_000_000, 25_020_000), commit_uwb=False,
    )
    disposition = owner.prepared_group_disposition(trigger)
    assert disposition.reason == "PREPARED_ADMISSION"
    owner.commit_continuous_event(trigger)
    assert composition.order[-2:] == ["admission", "native200"]
    assert composition.estimator_revision == 1
    assert composition.native_revision == 2


def test_later_native_failure_rolls_back_earlier_combined_admission():
    class FailingNativeComposition(Composition):
        fail_native = False

        def commit_native200(self, prepared):
            super().commit_native200(prepared)
            if self.fail_native:
                raise RuntimeError("INJECTED_LATE_NATIVE_FAILURE")

    diagnostic = StalePoseLinkDiagnostic(
        10_005_001, 5_000_000, POSE_AGE_LIMIT_NS + 1, False,
    )
    composition = FailingNativeComposition(stale_pose=diagnostic)
    owner, _ = _owner(composition)
    _complete(owner, base_ns=1_000_000, commit=True)
    owner.commit_continuous_event(owner.prepare_continuous_event(
        _imu(20_000_000, 20_020_000), commit_uwb=False,
    ))
    composition.stale_pose = None
    composition.fail_native = True
    prepared = owner.prepare_continuous_event(
        _imu(25_000_000, 25_020_000), commit_uwb=False,
    )
    before_owner = owner.continuous_snapshot()
    before_composition = composition.snapshot()
    with pytest.raises(RuntimeError, match="INJECTED_LATE_NATIVE_FAILURE"):
        owner.commit_continuous_event(prepared)
    after = owner.continuous_snapshot()
    assert after[:6] == before_owner[:6]
    assert after[6][:-1] == before_owner[6]
    assert after[6][-1].outcome == "EVENT_COMMIT_FAILED_ROLLED_BACK:RuntimeError"
    assert composition.snapshot() == before_composition
    audit = owner.admission_journal[-1]
    assert audit.commit_attempted is True
    assert audit.commit_succeeded is False
    assert audit.outcome == "EVENT_COMMIT_FAILED_ROLLED_BACK:RuntimeError"


def test_retry_effective_availability_ab_parity_and_future_plus_one_fails():
    diagnostic = StalePoseLinkDiagnostic(
        10_005_001, 5_000_000, POSE_AGE_LIMIT_NS + 1, False,
    )
    owners = []
    for commit in (False, True):
        owner, composition = _owner(Composition(stale_pose=diagnostic))
        _complete(owner, base_ns=1_000_000, commit=commit)
        source = _imu(20_000_000, 20_020_000)
        owner.commit_continuous_event(
            owner.prepare_continuous_event(source, commit_uwb=False)
        )
        composition.stale_pose = None
        trigger = _imu(25_000_000, 25_020_000)
        composition.root_ns = trigger.availability_global_ns
        owner.commit_continuous_event(
            owner.prepare_continuous_event(trigger, commit_uwb=False)
        )
        owners.append((owner, composition))
    left, right = owners
    assert left[0].journal[-1].effective_processing_availability_ns == 25_020_000
    assert left[0].journal[-1].packet_digest == right[0].journal[-1].packet_digest
    assert left[0].journal[-1].epoch_digest == right[0].journal[-1].epoch_digest
    assert left[1].estimator_revision == 0
    assert right[1].estimator_revision == 1

    owner, composition = _owner(Composition(stale_pose=diagnostic))
    _complete(owner, base_ns=1_000_000, commit=True)
    source = _imu(20_000_000, 20_020_000)
    owner.commit_continuous_event(owner.prepare_continuous_event(source, commit_uwb=False))
    composition.stale_pose = None
    trigger = _imu(25_000_000, 25_020_000)
    composition.root_ns = trigger.availability_global_ns + 1
    before = owner.continuous_snapshot()
    with pytest.raises(ValueError, match="root publication is from the future"):
        owner.prepare_continuous_event(trigger, commit_uwb=False)
    assert owner.continuous_snapshot() == before


def test_terminal_stale_group_is_nonfatal_and_later_group_succeeds():
    diagnostic = StalePoseLinkDiagnostic(
        10_005_001, 5_000_000, POSE_AGE_LIMIT_NS + 1, True,
    )
    owner, composition = _owner(Composition(stale_pose=diagnostic))
    before = (composition.estimator_revision, composition.native_revision)
    _complete(owner, base_ns=1_000_000, commit=True)
    assert (composition.estimator_revision, composition.native_revision) == before
    assert owner.journal[-1].reason == "STALE_POSE_LINK_REJECTED"
    assert owner.journal[-1].stale_pose_link == diagnostic
    assert owner.continuous_snapshot()[1] == {}
    composition.stale_pose = None
    _complete(owner, base_ns=EPOCH_PERIOD_NS + 1_000_000, commit=True)
    assert composition.estimator_revision == 1
    assert owner.journal[-1].reason == "PREPARED_COMPLETE_GROUP"
    assert NATIVE200_HISTORY_CAPACITY == 52


def test_gap_suppresses_retryable_group_then_failure_rolls_back_as_one_event():
    diagnostic = StalePoseLinkDiagnostic(
        10_005_001, 5_000_000, POSE_AGE_LIMIT_NS + 1, False,
    )
    owner, composition = _owner(Composition(
        stale_pose=diagnostic, fail_gap_commit=True,
    ))
    _complete(owner, base_ns=1_000_000, commit=True)
    assert owner.journal[-1].reason == "STALE_POSE_LINK_DEFERRED"
    composition.stale_pose = None
    before = composition.snapshot()
    before_owner = owner.continuous_snapshot()
    gap = ContinuousEvent(
        "gap-after-deferred", "GAP", -1, "UNASSIGNED_INTER_ACTION_GAP",
        25_000_000, 25_020_000, NODES[0], 0, "B306_TIMER2",
        "a" * 64, "b" * 64, "c" * 64, "", object(),
        gap_start_global_ns=20_000_000, gap_covariance_growth=0.005,
        region_id="UNASSIGNED_INTER_ACTION_GAP",
    )
    prepared = owner.prepare_continuous_event(gap, commit_uwb=False)
    disposition = owner.prepared_group_disposition(prepared)
    assert disposition.admission is None
    assert disposition.reason == "NO_COMPLETE_GROUP"
    assert prepared.token.journal[-1].reason == "INCOMPLETE_GROUP_SUPPRESSED_AT_GAP"
    with pytest.raises(RuntimeError, match="injected gap commit"):
        owner.commit_continuous_event(prepared)
    assert composition.snapshot() == before
    assert owner.continuous_snapshot() == before_owner
    assert not owner.admission_journal


def test_delayed_ten_node_obsolete_pair_uses_guarded_root_fallback_and_commits_b():
    diagnostic = ObsoleteNative200SourcePairDiagnostic(
        30_000, 30_000_000, 35_000, 35_000_000, 4,
    )
    stale = StalePoseLinkDiagnostic(
        10_005_001, 5_000_000, POSE_AGE_LIMIT_NS + 1, False,
    )
    owner, composition = _owner(Composition(stale_pose=stale, obsolete_pair=diagnostic))
    before_composition = composition.snapshot()
    _complete(owner, base_ns=1_000_000, commit=True)
    assert owner.journal[-1].reason == "STALE_POSE_LINK_DEFERRED"
    composition.stale_pose = None
    trigger = _imu(25_000_000, 25_020_000)
    owner.commit_continuous_event(
        owner.prepare_continuous_event(trigger, commit_uwb=False)
    )
    assert composition.estimator_revision == before_composition[0] + 1
    assert composition.native_revision == before_composition[1] + 1
    assert composition.obsolete_fallback_authorizations == [True]
    prepared = owner.journal[-1]
    assert prepared.reason == "PREPARED_COMPLETE_GROUP"
    assert prepared.original_group_availability_ns == 1_029_000
    assert prepared.effective_processing_availability_ns == 25_020_000
    assert prepared.retry_delay_ns == 23_991_000
    admission = owner.admission_journal[-1]
    assert admission.prepared_reason == (
        "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
    )
    assert admission.diagnostic == diagnostic
    assert admission.commit_succeeded
    assert admission.outcome == "UWB_COMMIT_SUCCEEDED"
    assert owner.continuous_snapshot()[1] == {}


def test_fresh_ten_node_group_does_not_opt_in_to_obsolete_root_fallback():
    owner, composition = _owner()
    _complete(owner, base_ns=1_000_000, commit=True)
    assert composition.obsolete_fallback_authorizations == [False]
    assert composition.estimator_revision == 1
    assert owner.admission_journal[-1].prepared_reason == "ACCEPTED"


def test_complete_group_natural_assembly_obsolete_pair_retries_root_only_fallback():
    diagnostic = ObsoleteNative200SourcePairDiagnostic(
        4_260_209_875, 234_961_076_526_129,
        4_260_274_875, 234_961_141_525_340, 0,
    )
    owner, composition = _owner(Composition(obsolete_pair=diagnostic))
    availability_ns = 234_961_160_136_962
    prepared = None
    for index, node in enumerate(NODES):
        event = _uwb(
            node, 234_961_080_000_000 + index * 1_000,
            availability_ns=availability_ns,
        )
        prepared = owner.prepare_continuous_event(event, commit_uwb=True)
        owner.commit_continuous_event(prepared)
    assert composition.obsolete_fallback_authorizations == [False, True]
    assert composition.estimator_revision == 1
    assert prepared.token.admission is not None
    admission = owner.admission_journal[-1]
    assert admission.prepared_reason == (
        "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
    )
    assert admission.diagnostic == diagnostic
    assert admission.commit_succeeded
    group = owner.journal[-1]
    assert group.reason == "PREPARED_COMPLETE_GROUP"
    assert group.original_group_availability_ns == availability_ns
    assert group.effective_processing_availability_ns == availability_ns
    assert group.retry_delay_ns == 0


def test_complete_group_does_not_fallback_to_future_latest_pair():
    availability_ns = 234_961_160_136_962
    diagnostic = ObsoleteNative200SourcePairDiagnostic(
        4_260_209_875, 234_961_076_526_129,
        4_260_294_875, availability_ns + 1, 0,
    )
    owner, composition = _owner(Composition(obsolete_pair=diagnostic))
    for index, node in enumerate(NODES):
        prepared = owner.prepare_continuous_event(_uwb(
            node, 234_961_080_000_000 + index * 1_000,
            availability_ns=availability_ns,
        ), commit_uwb=True)
        owner.commit_continuous_event(prepared)
    assert composition.obsolete_fallback_authorizations == [False]
    assert composition.estimator_revision == 0
    assert owner.journal[-1].reason == "OBSOLETE_NATIVE200_SOURCE_PAIR"


def test_delayed_one_node_group_does_not_opt_in_to_obsolete_root_fallback():
    diagnostic = ObsoleteNative200SourcePairDiagnostic(
        30_000, 30_000_000, 35_000, 35_000_000, 4,
    )
    owner, composition = _owner(Composition(obsolete_pair=diagnostic))
    owner.commit_continuous_event(owner.prepare_continuous_event(
        _uwb(NODES[0], 1_000_000), commit_uwb=True,
    ))
    deadline = EPOCH_PERIOD_NS + ASSEMBLY_HORIZON_NS
    owner.commit_continuous_event(owner.prepare_continuous_event(
        _imu(10_000_000, deadline), commit_uwb=False,
    ))
    assert composition.obsolete_fallback_authorizations == [False]
    assert composition.estimator_revision == 0
    assert owner.journal[-1].reason == "OBSOLETE_NATIVE200_SOURCE_PAIR"
    assert owner.journal[-1].obsolete_native200_source_pair == diagnostic


def test_delayed_sparse_multi_node_obsolete_pair_prepares_root_fallback_once():
    diagnostic = ObsoleteNative200SourcePairDiagnostic(
        30_000, 30_000_000, 35_000, 35_000_000, 4,
    )
    stale = StalePoseLinkDiagnostic(
        10_005_001, 5_000_000, POSE_AGE_LIMIT_NS + 1, False,
    )
    owner, composition = _owner(Composition(
        stale_pose=stale, obsolete_pair=diagnostic,
    ))
    for index, node in enumerate(NODES[:4]):
        owner.commit_continuous_event(owner.prepare_continuous_event(
            _uwb(node, 1_000_000 + index * 1_000), commit_uwb=True,
        ))
    deadline = EPOCH_PERIOD_NS + ASSEMBLY_HORIZON_NS
    first = owner.prepare_continuous_event(
        _imu(10_000_000, deadline), commit_uwb=False,
    )
    owner.commit_continuous_event(first)
    assert owner.journal[-1].reason == "STALE_POSE_LINK_DEFERRED"
    composition.stale_pose = None
    second = owner.prepare_continuous_event(
        _imu(15_000_000, deadline + 5_000_000), commit_uwb=False,
    )
    disposition = owner.prepared_group_disposition(second)
    assert disposition.admission is not None
    assert disposition.admission.prepared_accepted is True
    assert disposition.admission.prepared_reason == (
        "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
    )
    assert disposition.admission.diagnostic == diagnostic
    owner.commit_continuous_event(second)
    assert composition.estimator_revision == 1
    assert owner.admission_journal[-1].outcome == "UWB_COMMIT_SUCCEEDED"
    assert owner.journal[-1].reason == "PREPARED_COMPLETE_GROUP"


def test_only_typed_obsolete_pair_is_nonfatal_generic_hinge_error_remains_fatal():
    class GenericCadenceComposition(Composition):
        def prepare_admission(self, packet, epoch, **_kwargs):
            raise HingeTemporalEvidenceError("HINGE_TEMPORAL_SOURCE_CADENCE_INVALID")

    owner, composition = _owner(GenericCadenceComposition())
    before = composition.snapshot()
    with pytest.raises(
        HingeTemporalEvidenceError, match="HINGE_TEMPORAL_SOURCE_CADENCE_INVALID"
    ):
        _complete(owner, base_ns=1_000_000, commit=True)
    assert composition.snapshot() == before
    assert owner.journal == ()


def test_packet_availability_is_authoritative_after_frame_lower_bound():
    from test_c2_authoritative_articulated_fusion import _engine_and_packet, _epoch

    engine, packet = _engine_and_packet()
    delayed_s = packet.event.availability_time_s + 0.01
    delayed_event = replace(packet.event, availability_time_s=delayed_s)
    delayed = BoundGroupPacket(
        packet.static_owner_digest, delayed_event, packet.pose_links,
        packet.information_weights, packet.a_sigma_owner, packet.b_sigma_owner,
        packet.b_shadow_owner,
        availability_global_ns=packet.availability_global_ns + 10_000_000,
    )
    plan = engine.robust.prepare(engine.static, engine.root, delayed)
    assert plan.availability_s == delayed_s

    publish_engine, publish_packet = _engine_and_packet()
    publish_epoch = _epoch(publish_engine, publish_packet)
    publish_event = replace(publish_packet.event, availability_time_s=delayed_s)
    publish_packet = BoundGroupPacket(
        publish_packet.static_owner_digest, publish_event, publish_packet.pose_links,
        publish_packet.information_weights, publish_packet.a_sigma_owner,
        publish_packet.b_sigma_owner, publish_packet.b_shadow_owner,
        availability_global_ns=publish_packet.availability_global_ns + 10_000_000,
    )
    prepared = publish_engine.prepare_admission(
        publish_packet, replace(publish_epoch, availability_time_s=delayed_s),
    )
    result = publish_engine.commit_admission(prepared)
    assert result.accepted
    assert publish_engine.root.publication_token().time_s == delayed_s

    early_event = replace(packet.event, availability_time_s=packet.event.availability_time_s - 0.001)
    early = BoundGroupPacket(
        packet.static_owner_digest, early_event, packet.pose_links,
        packet.information_weights, packet.a_sigma_owner, packet.b_sigma_owner,
        packet.b_shadow_owner,
        availability_global_ns=packet.availability_global_ns - 1_000_000,
    )
    with pytest.raises(ValueError, match="frame-derived lower bound"):
        engine.robust.prepare(engine.static, engine.root, early)


def test_large_canonical_ns_availability_gates_before_float_conversion():
    canonical_ns = 234_910_144_244_198
    raw_clock_ns = float(canonical_ns) + 0.1875
    assert canonical_clock_global_ns(raw_clock_ns) == canonical_ns
    assert canonical_ns * 1e-9 * 1e9 < raw_clock_ns
    rows = tuple(_row(node, 1_000) for node in NODES)
    clocks = {
        node: SimpleNamespace(link_time_ns=lambda **_kwargs: raw_clock_ns)
        for node in NODES
    }

    availability_s = canonical_group_availability_time_s(
        rows, clocks=clocks, availability_global_ns=canonical_ns,
    )
    assert availability_s == canonical_ns * 1e-9
    with pytest.raises(ValueError, match="precedes group frame lower bound"):
        canonical_group_availability_time_s(
            rows, clocks=clocks, availability_global_ns=canonical_ns - 1,
        )


@pytest.mark.parametrize("value", (
    -180_000_000, -60_000_001, -60_000_000, -59_999_999,
    0, 59_999_999, 60_000_000, 60_000_001,
    179_999_999, 180_000_000, 180_000_001,
))
def test_nearest_epoch_bucket_matches_independent_half_even_oracle(value):
    expected = int((Decimal(value) / Decimal(EPOCH_PERIOD_NS)).quantize(
        Decimal("1"), rounding=ROUND_HALF_EVEN,
    ))
    assert canonical_epoch_bucket(value) == expected


def _owned_frame(
    engine, packet, timer_us, revision, *, angle_rad=0.0,
    abrupt_forearm=False, contact=False,
):
    mapping = engine.native200_clock_mapping_owner(
        node="BSFC2CC", clock_owner_sha256=engine.native200_clock_owner_sha256,
    )
    global_ns = mapping.global_ns(timer_us)
    # The nominal stream moves continuously through root IMU propagation while
    # keeping one unchanged articulated correction generation.
    rigid = np.eye(3)
    rotations = {segment: rigid.copy() for segment in SEGMENTS}
    if abrupt_forearm:
        rotations["forearm_right"] = rigid @ Rotation.from_rotvec([0.08, 0.0, 0.0]).as_matrix()
    zero = {segment: np.zeros(3) for segment in SEGMENTS}
    points = corrected_proxy_points(rotations, zero, engine.pose.geometry)
    offsets = {node: points[point] for node, point in NODE_TO_PROXY_POINT.items()}
    velocity = {node: np.zeros(3) for node in NODE_TO_PROXY_POINT}
    if angle_rad or abrupt_forearm:
        velocity["BSFB165"] = np.array([0.0, 0.1, 0.0])
    template = packet.b_shadow_owner.snapshots[0]
    constraints = {"ankle_left": points["ankle_left"]} if contact else {}
    sample = ImuSample(
        global_ns * 1e-9, (global_ns + 100_000) * 1e-9,
        np.array([0.2, 0.0, 9.80665]), np.eye(3), revision,
    )
    return OwnedNative200HistoryFrame(
        node="BSFC2CC", boot_epoch=mapping.boot_epoch,
        timer2_base_us=timer_us - 5_000, source_timer_us=timer_us,
        source_global_ns=global_ns, clock_mapping_digest=mapping.digest,
        clock_owner_sha256=engine.native200_clock_owner_sha256,
        clock_source_sha256="c" * 64, publication_revision=revision,
        source_frame=revision, action_id="00_initial_still", imu_sample=sample,
        base_rotations_world=rotations, offsets_world_m=offsets,
        offset_velocities_world_mps=velocity, normals_world=template.normals_world,
        joints_relative_world_m=points, point_constraints_world_m=constraints,
        raw_provenance=RawByteProvenance(
            revision, revision * 32, revision * 32 + 32, "d" * 64,
        ),
        imu_owner_sha256="e" * 64, publication_owner_sha256="f" * 64,
        pose_publication_digest=_digest({"publication": revision}),
        base_pose_owner_digest=engine.native200_base_pose_owner_digest,
        body_proxy_owner_sha256=_digest({"body": revision}),
        contact_owner_digest=_digest({"contact": revision, "active": contact}),
        provenance="accepted upstream native200 pose/FK/contact publication",
    )


def _real_owner_fixture(*, abrupt=False):
    from test_c2_authoritative_articulated_fusion import _engine_and_packet

    engine, packet = _engine_and_packet(
        hinge_temporal_retention_contract=CONTINUOUS_HINGE_RETENTION_CONTRACT,
    )
    history = AuthoritativeContinuousHistoryOwner(
        engine=engine, a_sigma_owner=packet.a_sigma_owner,
        b_sigma_owner=packet.b_sigma_owner,
        b_shadow_provenance="existing body-shadow geometry owner",
        history_provenance="real continuous native200 pose/contact history",
        hinge_retention_contract=CONTINUOUS_HINGE_RETENTION_CONTRACT,
    )
    composition = AuthoritativeContinuousGroupComposition(
        engine=engine, history=history,
        clone_factory=lambda: (_ for _ in ()).throw(RuntimeError("unused fixture clone")),
    )
    owner = ContinuousGroupEpochOwner(
        composition=composition,
        static_nodes=tuple(sorted(engine.static.clocks)),
        acquired_action_ids=frozenset(("00_initial_still", "02_t_pose")),
    )
    # Phase the final native tick 4.038 us inside the sealed 5.005 ms
    # strict-floor bound of the latest anchor link.
    timers = tuple(range(130_408, 155_409, 5_000))
    for revision, timer_us in enumerate(timers):
        angle = 0.0005 * revision
        frame = _owned_frame(
            engine, packet, timer_us, revision,
            angle_rad=angle,
            abrupt_forearm=abrupt and revision == len(timers) - 1,
            contact=revision == len(timers) - 1,
        )
        event = ContinuousEvent(
            f"native:{revision}", "IMU", 0, "00_initial_still",
            frame.source_global_ns, round(frame.imu_sample.availability_time_s * 1e9),
            frame.node, frame.boot_epoch, "B306_TIMER2", frame.clock_mapping_digest,
            frame.clock_owner_sha256, frame.clock_source_sha256, "host-only", frame,
            imu_timer2=ImuTimer2Fields(timer_us - 5_000, timer_us),
        )
        plan = owner.prepare_continuous_event(event, commit_uwb=False)
        owner.commit_continuous_event(plan)
    shifted = tuple(
        replace(row, strobe_us=row.strobe_us + 100_000, frame_us=row.frame_us + 100_000)
        for row in packet.event.payload
    )
    _epochs, measurement_ns, frame_ns = group_epoch_times_ns(
        shifted, clocks=engine.static.clocks,
    )
    token = engine.root.publication_token()
    root = token.state.vector[:3] + (measurement_ns * 1e-9 - token.time_s) * token.state.vector[3:6]
    current = history.frames[-1]
    rows = []
    for row in shifted:
        ranges = []
        for anchor in range(8):
            query = engine.static.clocks[row.node].link_time_ns(
                event_boot_epoch=row.boot, strobe_us=row.strobe_us,
                t_round_us=row.t_round_us[anchor],
            )
            tag = root + current.offsets_world_m[row.node]
            tag = tag + (query - measurement_ns) * 1e-9 * token.state.vector[3:6]
            ranges.append(round(1000 * (
                np.linalg.norm(tag - engine.static.anchors_m[anchor])
                + engine.static.anchor_delay_m[anchor] + engine.static.tag_delay_m
            )))
        rows.append(replace(row, ranges_mm=tuple(ranges)))
    return owner, tuple(rows), max(frame_ns, 200_100_000)


def _enable_consensus_drift(owner):
    owner._composition.consensus_drift = ContinuousConsensusDriftOwner(
        config=FixedLagConsensusDriftConfig(
            minimum_lag_s=0.10, maximum_lag_s=0.30,
            update_period_s=0.10, minimum_consensus_pairs=1,
            rank_relative_tolerance=1e-2,
            maximum_velocity_step_mps=0.05, covariance_floor=1e-12,
        ),
        stream_owner_digest="9" * 64,
    )
    return owner._composition.consensus_drift


def _append_real_native(owner):
    composition = owner._composition
    prior = composition.history.frames[-1]
    timer_us = prior.source_timer_us + 5_000
    revision = prior.publication_revision + 1
    frame = _owned_frame(
        composition.engine, _engine_and_packet_for_frame(), timer_us, revision,
    )
    event = ContinuousEvent(
        f"b3:{revision}", "IMU", 0, "00_initial_still",
        frame.source_global_ns,
        round(frame.imu_sample.availability_time_s * 1e9),
        frame.node, frame.boot_epoch, "B306_TIMER2",
        frame.clock_mapping_digest, frame.clock_owner_sha256,
        frame.clock_source_sha256, "host-only", frame,
        imu_timer2=ImuTimer2Fields(timer_us - 5_000, timer_us),
    )
    plan = composition.prepare_native200(event)
    composition.commit_native200(plan)
    return event


def _composition_fingerprint(composition):
    root = composition.engine.root.publication_token()
    pose = composition.engine.pose.publication_token()
    return (
        root.revision, root.digest, root.state.vector.tobytes(),
        root.state.covariance.tobytes(), pose.revision, pose.digest,
        composition.engine.robust.revision,
        pickle.dumps(composition.engine.robust.trackers, protocol=5),
        composition.history._revision,
        tuple(frame.digest for frame in composition.history.frames),
        None if composition.consensus_drift is None
        else composition.consensus_drift.owner_digest,
    )


def _real_gap_event(owner, *, duration_ns=20_000_000):
    engine = owner._composition.engine
    frame = owner._composition.history.frames[-1]
    start_ns = int(round(engine.root.current_state.time_s * 1e9))
    end_ns = start_ns + duration_ns
    return ContinuousEvent(
        f"gap:{start_ns}:{end_ns}", "GAP", -1,
        "UNASSIGNED_INTER_ACTION_GAP", end_ns, end_ns,
        frame.node, frame.boot_epoch, "B306_TIMER2", frame.clock_mapping_digest,
        frame.clock_owner_sha256, frame.clock_source_sha256, "", object(),
        gap_start_global_ns=start_ns,
        gap_covariance_growth=duration_ns * 1e-9,
        region_id="UNASSIGNED_INTER_ACTION_GAP",
    )


def _next_native200_events(owner, count):
    engine = owner._composition.engine
    template = owner._composition.history.frames[-1]
    mapping = engine.native200_clock_mapping_owner(
        node=template.node, clock_owner_sha256=template.clock_owner_sha256,
    )
    events = []
    for index in range(count):
        revision = template.publication_revision + index + 1
        timer_us = template.source_timer_us + (index + 1) * 5_000
        global_ns = mapping.global_ns(timer_us)
        rotations = {
            segment: Rotation.from_rotvec(
                [0.0002 * revision, -0.0001 * revision, 0.00005 * revision]
            ).as_matrix()
            for segment in SEGMENTS
        }
        sample = ImuSample(
            global_ns * 1e-9, (global_ns + 100_000) * 1e-9,
            np.array([0.2, 0.0, 9.80665]), np.eye(3), revision,
        )
        frame = replace(
            template, timer2_base_us=timer_us - 5_000,
            source_timer_us=timer_us, source_global_ns=global_ns,
            publication_revision=revision, source_frame=revision,
            imu_sample=sample, base_rotations_world=rotations,
            raw_provenance=RawByteProvenance(
                99, 1_000, 2_000, "9" * 64, index,
            ),
            pose_publication_digest=_digest({"batch": revision}), digest="",
        )
        events.append(ContinuousEvent(
            f"native-batch:{revision}", "IMU", 0, "00_initial_still",
            global_ns, global_ns + 100_000, frame.node, frame.boot_epoch,
            "B306_TIMER2", frame.clock_mapping_digest,
            frame.clock_owner_sha256, frame.clock_source_sha256, "", frame,
            imu_timer2=ImuTimer2Fields(timer_us - 5_000, timer_us),
        ))
    return tuple(events)


def test_real_native200_batch_matches_scalar_and_stops_at_uwb_or_gap_boundary():
    scalar, _rows, _availability = _real_owner_fixture()
    batched, _rows2, _availability2 = _real_owner_fixture()
    scalar_events = _next_native200_events(scalar, 3)
    batch_events = _next_native200_events(batched, 3)
    for event in scalar_events:
        scalar.commit_continuous_event(
            scalar.prepare_continuous_event(event, commit_uwb=False)
        )

    authority = object()
    batched.bind_native200_record_coordinator(authority)
    capability = batched._issue_native200_record_capability(authority=authority)
    prepared = batched.prepare_native200_batch(
        batch_events, _record_capability=capability,
    )
    batched.commit_native200_batch(prepared)
    batched._close_native200_record_capability(capability, authority=authority)

    assert batched._composition.history.revision == scalar._composition.history.revision
    assert tuple(frame.digest for frame in batched._composition.history.frames) == tuple(
        frame.digest for frame in scalar._composition.history.frames
    )
    assert (
        batched._composition.engine.root.publication_token().digest
        == scalar._composition.engine.root.publication_token().digest
    )
    assert (
        batched._composition.engine.pose.publication_token().digest
        == scalar._composition.engine.pose.publication_token().digest
    )

    pending = _uwb(NODES[0], batch_events[-1].common_global_ns + 1_000_000)
    batched.commit_continuous_event(
        batched.prepare_continuous_event(pending, commit_uwb=False)
    )
    assert not batched.native200_batch_boundary_free()
    next_event = _next_native200_events(batched, 1)
    assert batched.native200_record_batch_safe(next_event)
    bucket = next(iter(batched._pending))
    retained = batched._pending[bucket][0]
    batched._pending[bucket] = (retained,) * 10
    capability = batched._issue_native200_record_capability(authority=authority)
    with pytest.raises(RuntimeError, match="PENDING_UWB_BOUNDARY"):
        batched.prepare_native200_batch(
            next_event, _record_capability=capability,
        )
    batched._close_native200_record_capability(capability, authority=authority)
    batched._pending.clear()
    gap = _real_gap_event(batched)
    batched.commit_continuous_event(
        batched.prepare_continuous_event(gap, commit_uwb=False)
    )
    assert batched._composition.history.frames == ()


def test_native200_record_batch_classifier_owns_deadline_cardinality_and_shape():
    owner, _rows, _availability = _real_owner_fixture()
    event = _next_native200_events(owner, 1)[0]
    assert owner.native200_record_batch_safe((event,))

    bucket = event.common_global_ns // EPOCH_PERIOD_NS
    deadline = (bucket + 1) * EPOCH_PERIOD_NS + ASSEMBLY_HORIZON_NS
    def at_availability(value):
        sample = replace(
            event.payload_owner.imu_sample,
            availability_time_s=value * 1e-9,
        )
        frame = replace(event.payload_owner, imu_sample=sample, digest="")
        return replace(
            event, availability_global_ns=value, payload_owner=frame,
        )

    owner._pending[bucket] = (object(),)
    assert owner.native200_record_batch_safe((
        at_availability(deadline - 1),
    ))
    assert not owner.native200_record_batch_safe((
        at_availability(deadline),
    ))
    assert not owner.native200_record_batch_safe((
        at_availability(deadline + 1),
    ))
    authority = object()
    owner.bind_native200_record_coordinator(authority)
    capability = owner._issue_native200_record_capability(authority=authority)
    with pytest.raises(RuntimeError, match="PENDING_UWB_BOUNDARY"):
        owner.prepare_native200_batch(
            (at_availability(deadline),),
            _record_capability=capability,
        )
    owner._close_native200_record_capability(capability, authority=authority)
    owner._pending[bucket] = (object(),) * 10
    assert not owner.native200_record_batch_safe((event,))
    owner._pending.clear()
    assert not owner.native200_record_batch_safe((_uwb(NODES[0], deadline),))
    assert not owner.native200_record_batch_safe(())


def test_real_gap_is_atomic_mean_invariant_psd_and_fixed_owner_inert():
    short, _rows, _availability = _real_owner_fixture()
    long, _rows2, _availability2 = _real_owner_fixture()
    before_root = short._composition.engine.root.publication_token()
    before_pose = short._composition.engine.pose.publication_token()
    before_generation = short._composition.engine.pose.hinge_continuity_generation
    before_static = short._composition.engine.static.digest
    before_model = short._composition.engine.model_digest
    before_history_revision = short._composition.history.revision
    plan = short.prepare_continuous_event(
        _real_gap_event(short, duration_ns=20_000_000), commit_uwb=False,
    )
    assert short._composition.engine.root.publication_token().digest == before_root.digest
    assert short._composition.engine.pose.publication_token().digest == before_pose.digest
    assert short._composition.history.revision == before_history_revision
    short.commit_continuous_event(plan)
    after = short._composition.engine.root.publication_token()
    assert after.state.vector.tobytes() == before_root.state.vector.tobytes()
    assert np.isfinite(after.state.covariance).all()
    assert np.linalg.eigvalsh(after.state.covariance).min() >= -1e-12
    assert np.trace(after.state.covariance) > np.trace(before_root.state.covariance)
    assert short._composition.history.frames == ()
    assert short._composition.engine.pose.hinge_continuity_generation == before_generation + 1
    assert short._composition.engine.static.digest == before_static
    assert short._composition.engine.model_digest == before_model

    long_before = long._composition.engine.root.publication_token()
    long.commit_continuous_event(long.prepare_continuous_event(
        _real_gap_event(long, duration_ns=40_000_000), commit_uwb=False,
    ))
    short_growth = np.trace(after.state.covariance - before_root.state.covariance)
    long_after = long._composition.engine.root.publication_token()
    long_growth = np.trace(long_after.state.covariance - long_before.state.covariance)
    assert long_growth > short_growth > 0.0
    assert long_after.state.vector.tobytes() == long_before.state.vector.tobytes()


def test_real_gap_rollback_and_post_gap_native200_reseed_then_consecutive_pair():
    owner, _rows, _availability = _real_owner_fixture()
    engine = owner._composition.engine
    template = owner._composition.history.frames[-1]
    snapshot = owner.continuous_snapshot()
    root_before = engine.root.publication_token()
    pose_before = engine.pose.publication_token()
    history_before = owner._composition.history.frames
    gap = _real_gap_event(owner, duration_ns=20_000_000)
    owner.commit_continuous_event(owner.prepare_continuous_event(gap, commit_uwb=False))
    owner.restore_continuous_snapshot(snapshot)
    assert engine.root.publication_token().digest == root_before.digest
    assert engine.pose.publication_token().digest == pose_before.digest
    assert owner._composition.history.frames == history_before

    owner.commit_continuous_event(owner.prepare_continuous_event(gap, commit_uwb=False))
    for revision, timer_us in zip(
        (template.publication_revision + 20, template.publication_revision + 21),
        (template.source_timer_us + 25_000, template.source_timer_us + 30_000),
    ):
        frame = replace(
            _owned_frame(engine, _engine_and_packet_for_frame(), timer_us, revision),
            action_id="02_t_pose", digest="",
        )
        event = ContinuousEvent(
            f"post-gap:{revision}", "IMU", 2, "02_t_pose",
            frame.source_global_ns, round(frame.imu_sample.availability_time_s * 1e9),
            frame.node, frame.boot_epoch, "B306_TIMER2", frame.clock_mapping_digest,
            frame.clock_owner_sha256, frame.clock_source_sha256, "", frame,
            imu_timer2=ImuTimer2Fields(timer_us - 5_000, timer_us),
        )
        owner.commit_continuous_event(owner.prepare_continuous_event(event, commit_uwb=False))
    assert len(owner._composition.history.frames) == 2
    assert owner._composition.history.frames[0].publication_revision == template.publication_revision + 20
    assert engine.pose.publication_token().latest_sample_s == pytest.approx(
        owner._composition.history.frames[-1].source_global_ns * 1e-9
    )


def test_gap_endpoint_native200_matches_root_gap_ingest_and_next_frame_is_normal():
    owner, _rows, _availability = _real_owner_fixture()
    composition = owner._composition
    history = composition.history
    engine = composition.engine
    prior = history.frames[-1]
    reference = copy.deepcopy(engine.root)
    timer_us = prior.source_timer_us + 38_914
    frame = _owned_frame(
        engine, _engine_and_packet_for_frame(), timer_us,
        prior.publication_revision + 1,
    )
    endpoint = ContinuousEvent(
        "gap-endpoint", "IMU", -1, "FULL_SESSION_CONTINUOUS_00_TO_19",
        frame.source_global_ns, round(frame.imu_sample.availability_time_s * 1e9),
        frame.node, frame.boot_epoch, "B306_TIMER2", frame.clock_mapping_digest,
        frame.clock_owner_sha256, frame.clock_source_sha256, "", frame,
        imu_timer2=ImuTimer2Fields(frame.timer2_base_us, frame.source_timer_us),
        region_id="FULL_SESSION_CONTINUOUS_00_TO_19",
    )
    duration_ns = frame.source_global_ns - prior.source_global_ns
    gap = ContinuousEvent(
        "gap-to-endpoint", "GAP", -1, "FULL_SESSION_CONTINUOUS_00_TO_19",
        frame.source_global_ns, endpoint.availability_global_ns,
        frame.node, frame.boot_epoch, "B306_TIMER2", frame.clock_mapping_digest,
        frame.clock_owner_sha256, frame.clock_source_sha256, "", None,
        gap_start_global_ns=prior.source_global_ns,
        gap_covariance_growth=duration_ns * 1e-9,
        region_id="FULL_SESSION_CONTINUOUS_00_TO_19",
    )
    before = _composition_fingerprint(composition)
    for bad_sample in (
        replace(frame.imu_sample, m1_valid=False),
        replace(frame.imu_sample, m1_reset=True),
    ):
        bad_frame = replace(frame, imu_sample=bad_sample, digest="")
        with pytest.raises(ValueError, match="not M1-valid"):
            history.prepare_gap_native200(
                gap, replace(endpoint, payload_owner=bad_frame),
            )
    operational_mode = engine.root.mode
    for bad_mode in (
        SystemMode.INITIALIZING,
        SystemMode.TIME_INVALID,
        SystemMode.M1_RESET_RECOVERY,
    ):
        engine.root._mode = bad_mode
        with pytest.raises(RuntimeError, match="operational root mode"):
            history.prepare_gap_native200(gap, endpoint)
    engine.root._mode = operational_mode
    prepared = owner.prepare_continuous_event(
        gap, commit_uwb=False, _gap_endpoint_event=endpoint,
    )
    assert _composition_fingerprint(composition) == before
    root_plan = prepared.token.gap_prepared.history_plan
    with pytest.raises(RuntimeError, match="TAMPERED_CONTINUOUS_GAP_ENDPOINT_PLAN"):
        history.commit_gap_native200(replace(
            root_plan, endpoint_identity_digest="0" * 64,
        ))
    alternate_sample = replace(
        frame.imu_sample, source_sequence=frame.imu_sample.source_sequence + 1,
    )
    alternate_root = engine.root._prepare_no_update_gap(
        gap_start_time_s=gap.gap_start_global_ns * 1e-9,
        gap_end_time_s=gap.common_global_ns * 1e-9,
        availability_time_s=gap.availability_global_ns * 1e-9,
        post_gap_sample=alternate_sample,
        source_gap_owner=root_plan.root_plan.source_gap_owner,
        following_input_mode=RootTranslationEdgeMode.INERTIAL,
    )
    substituted = replace(root_plan, root_plan=alternate_root, digest="")
    substituted = replace(
        substituted,
        digest=group_module._prepared_gap_native200_digest(substituted),
    )
    with pytest.raises(RuntimeError, match="TAMPERED_CONTINUOUS_GAP_ENDPOINT_PLAN"):
        history.commit_gap_native200(substituted)
    foreign_owner, _foreign_rows, _foreign_availability = _real_owner_fixture()
    foreign = foreign_owner._composition.history
    with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN_CONTINUOUS_GAP_ENDPOINT_PLAN"):
        foreign.commit_gap_native200(root_plan)
    assert _composition_fingerprint(composition) == before
    assert reference.ingest_imu_after_source_gap(
        frame.imu_sample,
        gap_start_time_s=reference.current_state.time_s,
        source_gap_owner=root_plan.root_plan.source_gap_owner,
        following_input_mode=RootTranslationEdgeMode.INERTIAL,
    )

    owner.commit_continuous_event(prepared)
    with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN_CONTINUOUS_GAP_ENDPOINT_PLAN"):
        history.commit_gap_native200(root_plan)

    actual = engine.root.current_state
    expected = reference.current_state
    assert actual.time_s == expected.time_s == frame.imu_sample.measurement_time_s
    assert actual.vector.tobytes() == expected.vector.tobytes()
    assert actual.covariance.tobytes() == expected.covariance.tobytes()
    assert engine.root.mode == reference.mode
    assert history.frames == (frame,)
    next_event = _next_native200_events(owner, 1)[0]
    composition.commit_native200(composition.prepare_native200(next_event))
    assert len(history.frames) == 2
    assert history.frames[-1].source_timer_us - frame.source_timer_us == 5_000


def _engine_and_packet_for_frame():
    from test_c2_authoritative_articulated_fusion import _engine_and_packet
    _engine, packet = _engine_and_packet(
        hinge_temporal_retention_contract=CONTINUOUS_HINGE_RETENTION_CONTRACT,
    )
    return packet


def test_continuous_retention_must_be_owned_when_pose_is_constructed():
    from test_c2_authoritative_articulated_fusion import _engine_and_packet

    engine, packet = _engine_and_packet()
    before = engine.pose.publication_token()
    with pytest.raises(ValueError, match="different retention contract"):
        AuthoritativeContinuousHistoryOwner(
            engine=engine, a_sigma_owner=packet.a_sigma_owner,
            b_sigma_owner=packet.b_sigma_owner,
            b_shadow_provenance="existing body-shadow geometry owner",
            history_provenance="source-owned native200 history",
            hinge_retention_contract=CONTINUOUS_HINGE_RETENTION_CONTRACT,
        )
    assert engine.pose.publication_token() == before
    assert not hasattr(engine.pose, "configure_hinge_temporal_retention")


def test_concrete_history_advances_real_root_pose_and_builds_owned_sidecars():
    owner, rows, availability_ns = _real_owner_fixture()
    history = owner._composition.history
    assert history.revision == 6 and len(history.frames) == 6
    assert owner._composition.engine.root.publication_token().revision >= 6
    assert owner._composition.engine.pose.publication_token().latest_sample_s == pytest.approx(
        history.frames[-1].source_global_ns * 1e-9
    )
    materialized = owner._composition.materialize_group(
        rows, availability_global_ns=availability_ns,
        member_region_identities=("00_initial_still",) * 10,
        evidence_class="ACTION_EVIDENCE",
    )
    assert len(materialized.packet.pose_links) == 80
    assert len(materialized.packet.b_shadow_owner.snapshots) == 10
    assert all(link.pose_time_ns < link.query_time_ns for link in materialized.packet.pose_links)
    assert materialized.epoch.native200_source_pair.current_global_ns == history.frames[-1].source_global_ns
    assert materialized.epoch.point_constraints_world_m.keys() == {"ankle_left"}
    assert NATIVE200_HISTORY_CAPACITY == 52


@pytest.mark.parametrize("count", (1, 4, 7))
def test_concrete_history_materializes_complete_sidecars_for_runtime_subset(count):
    owner, rows, availability_ns = _real_owner_fixture()
    subset = rows[:count]
    materialized = owner._composition.materialize_group(
        subset, availability_global_ns=availability_ns,
        member_region_identities=("00_initial_still",) * count,
        evidence_class="ACTION_EVIDENCE",
    )
    nodes = {row.node for row in subset}
    assert len(materialized.packet.event.payload) == count
    assert len(materialized.packet.pose_links) == 8 * count
    assert {row.node for row in materialized.packet.pose_links} == nodes
    assert {row.node for row in materialized.packet.b_shadow_owner.snapshots} == nodes


def test_concrete_composition_real_rows_ab_candidate_parity_and_single_commit():
    left, rows, availability_ns = _real_owner_fixture()
    right, right_rows, right_availability_ns = _real_owner_fixture()
    for owner, payload, available, commit in (
        (left, rows, availability_ns, False),
        (right, right_rows, right_availability_ns, True),
    ):
        before = owner._composition.engine.root.publication_token().revision
        for row in sorted(payload, key=lambda item: item.node):
            clock = owner._composition.engine.static.clocks[row.node]
            common_ns = int(round(clock.link_time_ns(
                event_boot_epoch=row.boot, strobe_us=row.strobe_us, t_round_us=0.0,
            )))
            event = ContinuousEvent(
                f"real:{row.node}:{row.sequence}", "UWB", 0, "00_initial_still",
                common_ns, available, row.node, row.boot, "B306_TIMER2",
                "a" * 64, "b" * 64, "c" * 64, "host-only", row,
                uwb_timer2=UwbTimer2Fields(row.strobe_us, row.frame_us),
            )
            plan = owner.prepare_continuous_event(event, commit_uwb=commit)
            owner.commit_continuous_event(plan)
        expected = before + (1 if commit else 0)
        assert owner._composition.engine.root.publication_token().revision == expected
    assert left.journal[-1].candidate_digest == right.journal[-1].candidate_digest
    assert left.journal[-1].packet_digest == right.journal[-1].packet_digest
    assert left.journal[-1].epoch_digest == right.journal[-1].epoch_digest


def test_real_complete_group_obsolete_pair_uses_root_fallback_and_queues_drift(monkeypatch):
    owner, rows, availability_ns = _real_owner_fixture()
    composition = owner._composition
    drift = _enable_consensus_drift(owner)
    stale_materialized = composition.materialize_group(
        rows, availability_global_ns=availability_ns,
        member_region_identities=("00_initial_still",) * 10,
        evidence_class="ACTION_EVIDENCE",
    )
    template = composition.history.frames[-1]
    timer_us = template.source_timer_us + 5_000
    revision = template.publication_revision + 1
    frame = _owned_frame(
        composition.engine, _engine_and_packet_for_frame(), timer_us, revision,
    )
    event = ContinuousEvent(
        f"superseding:{revision}", "IMU", 0, "00_initial_still",
        frame.source_global_ns, round(frame.imu_sample.availability_time_s * 1e9),
        frame.node, frame.boot_epoch, "B306_TIMER2", frame.clock_mapping_digest,
        frame.clock_owner_sha256, frame.clock_source_sha256, "host-only", frame,
        imu_timer2=ImuTimer2Fields(timer_us - 5_000, timer_us),
    )
    owner.commit_continuous_event(owner.prepare_continuous_event(event, commit_uwb=False))
    rebound_epoch = replace(
        stale_materialized.epoch,
        pose_token_digest=composition.engine.pose.publication_token().digest,
    )
    stale_materialized = replace(
        stale_materialized, epoch=rebound_epoch,
        epoch_digest=composition.engine._epoch_digest(rebound_epoch),
    )
    before_root = composition.engine.root.publication_token()
    monkeypatch.setattr(composition, "materialize_group", lambda *_args, **_kwargs: stale_materialized)
    def feed(payload, *, available, commit):
        for row in sorted(payload, key=lambda item: item.node):
            clock = composition.engine.static.clocks[row.node]
            common_ns = int(round(clock.link_time_ns(
                event_boot_epoch=row.boot, strobe_us=row.strobe_us,
                t_round_us=0.0,
            )))
            uwb = ContinuousEvent(
                f"integration:{row.node}:{row.strobe_us}", "UWB", 0,
                "00_initial_still", common_ns, available, row.node, row.boot,
                "B306_TIMER2", "a" * 64, "b" * 64, "c" * 64, "host-only",
                row, uwb_timer2=UwbTimer2Fields(row.strobe_us, row.frame_us),
            )
            owner.commit_continuous_event(
                owner.prepare_continuous_event(uwb, commit_uwb=commit)
            )
    feed(rows, available=availability_ns, commit=True)
    assert owner.journal[-1].reason == "PREPARED_COMPLETE_GROUP"
    admission = owner.admission_journal[-1]
    assert admission.prepared_reason == (
        "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
    )
    assert admission.diagnostic is not None
    after_root = composition.engine.root.publication_token()
    assert after_root.state.vector[:3].tobytes() != before_root.state.vector[:3].tobytes()
    assert after_root.state.vector[3:9].tobytes() == before_root.state.vector[3:9].tobytes()
    assert drift.pending_count == 1
    package = drift.snapshot()._state.pending[0]
    assert package.observation.availability_time_s == pytest.approx(
        availability_ns * 1e-9)
    assert package.trusted_nodes == admission.trusted_partition

    while composition.history.frames[-1].source_global_ns < availability_ns:
        previous_pending = drift.pending_count
        event = _append_real_native(owner)
        if event.common_global_ns < availability_ns:
            assert drift.pending_count == previous_pending
    assert drift.pending_count == 0
    assert drift.snapshot()._state.last_consumed_native200_time_s == pytest.approx(
        event.common_global_ns * 1e-9)


def test_abrupt_native200_hinge_jump_remains_fail_closed_without_dynamic_evidence():
    owner, rows, availability_ns = _real_owner_fixture(abrupt=True)
    engine = owner._composition.engine

    def estimator_owner_bytes():
        root = engine.root.publication_token()
        return (
            root.digest, root.state.vector.tobytes(), root.state.covariance.tobytes(),
            engine.pose.publication_token().digest, engine.robust.revision,
            pickle.dumps(engine.robust.trackers, protocol=5),
            tuple(
                (frame.digest, frame.imu_owner_sha256, frame.pose_publication_digest)
                for frame in owner._composition.history.frames
            ),
        )

    before = estimator_owner_bytes()
    for row in sorted(rows, key=lambda item: item.node):
        clock = owner._composition.engine.static.clocks[row.node]
        common_ns = int(round(clock.link_time_ns(
            event_boot_epoch=row.boot, strobe_us=row.strobe_us, t_round_us=0.0,
        )))
        event = ContinuousEvent(
            f"abrupt:{row.node}:{row.sequence}", "UWB", 0, "00_initial_still",
            common_ns, availability_ns, row.node, row.boot, "B306_TIMER2",
            "a" * 64, "b" * 64, "c" * 64, "host-only", row,
            uwb_timer2=UwbTimer2Fields(row.strobe_us, row.frame_us),
        )
        plan = owner.prepare_continuous_event(event, commit_uwb=True)
        if plan.token.admission is not None:
            assert not plan.token.admission.prepared_result.accepted
            assert estimator_owner_bytes() == before
        owner.commit_continuous_event(plan)
    assert estimator_owner_bytes() == before
    assert owner.journal[-1].reason == "PREPARED_COMPLETE_GROUP"


@pytest.mark.parametrize("count", (1, 4, 7, 10))
def test_continuous_prepared_admission_uses_one_shared_fk_for_trusted_partition(
    monkeypatch, count,
):
    """Genuine packet validity chooses x/10; publication is still one skeleton."""
    owner, rows, availability_ns = _real_owner_fixture()
    composition = owner._composition
    engine = composition.engine
    history = composition.history.frames
    source_identity = tuple(
        (frame.source_global_ns, frame.imu_owner_sha256, frame.pose_publication_digest)
        for frame in history
    )
    assert np.all(np.diff([frame.source_timer_us for frame in history]) == 5_000)
    assert np.all(np.diff([frame.source_global_ns for frame in history]) > 5_000_000)
    assert all(
        frame.source_global_ns
        == engine.native200_clock_mapping_owner(
            node=frame.node, clock_owner_sha256=frame.clock_owner_sha256,
        ).global_ns(frame.source_timer_us)
        for frame in history
    )

    retained_indices = (0, 1, 2, 5) if count == 4 else tuple(range(count))
    retained = {rows[index].node for index in retained_indices}
    # Excluded nodes remain production-shaped and keep four canonical links.
    # Four simultaneous 1 mm ranges to distinct room anchors are physically
    # inconsistent, so the real adaptive fit must reject them rather than an
    # outer structural-validity gate removing the row.
    packet_rows = tuple(
        row if row.node in retained else replace(
            row, valid_mask=0x0F,
            ranges_mm=(1, 1, 1, 1, *row.ranges_mm[4:]),
        )
        for row in rows
    )
    excluded_nodes = {
        row.node for row in packet_rows if row.node not in retained
    }
    def fail_legacy(*args, **kwargs):
        raise AssertionError("legacy _execute_group path used")

    monkeypatch.setattr(legacy_owner, "_execute_group", fail_legacy)
    upstream_imu_identity_before = tuple(frame.imu_owner_sha256 for frame in history)
    root_before = engine.root.publication_token()
    pose_before = engine.pose.publication_token()
    robust_before = engine.robust.revision
    prepared = None
    for row in sorted(packet_rows, key=lambda item: item.node):
        clock = engine.static.clocks[row.node]
        common_ns = int(round(clock.link_time_ns(
            event_boot_epoch=row.boot, strobe_us=row.strobe_us, t_round_us=0.0,
        )))
        event = ContinuousEvent(
            f"partition:{count}:{row.node}:{row.sequence}",
            "UWB", 0, "00_initial_still", common_ns, availability_ns,
            row.node, row.boot, "B306_TIMER2", "a" * 64, "b" * 64,
            "c" * 64, "host-only", row,
            uwb_timer2=UwbTimer2Fields(row.strobe_us, row.frame_us),
        )
        prepared = owner.prepare_continuous_event(event, commit_uwb=True)
        if prepared.token.admission is None:
            owner.commit_continuous_event(prepared)
    assert prepared is not None and prepared.token.admission is not None
    admission = prepared.token.admission
    materialized_epoch = admission.epoch
    source = materialized_epoch.native200_source_pair
    assert source.previous_global_ns < source.current_global_ns
    assert source.current_global_ns < round(materialized_epoch.measurement_time_s * 1e9)
    assert source.current_global_ns == history[-1].source_global_ns
    assert source.previous_global_ns == history[-2].source_global_ns

    selection_plan = engine.robust.prepare(engine.static, engine.root, admission.packet)
    assert set(selection_plan.selection.trusted_nodes) == retained
    assert len(selection_plan.selection.trusted_nodes) == count
    assessments = {
        assessment.node: assessment
        for assessment in selection_plan.selection.assessments
    }
    assert set(assessments) == retained
    assert all(assessments[node].trusted for node in retained)
    assert all(assessments[node].reason == "TRUSTED" for node in retained)
    # Physically impossible delay-corrected ranges are removed before the
    # adaptive solve.  Their nodes therefore cannot become trusted, but they
    # remain in the fixed ten-node output inventory via FK propagation.
    assert not (set(assessments) & excluded_nodes)
    assert excluded_nodes == set(engine.static.clocks) - retained

    result = admission.prepared_result
    assert result.accepted
    assert admission.root_only is (count == 1)
    assert result.direct_nodes == result.trusted_nodes
    assert set(result.direct_nodes) == retained
    assert set(result.propagated_nodes) == set(engine.static.clocks) - retained
    assert len(result.direct_nodes) == count
    assert len(result.propagated_nodes) == 10 - count
    assert set(result.node_position_m) == set(engine.static.clocks)
    assert not hasattr(result, "provisional_node_root_m")

    points = corrected_proxy_points(
        materialized_epoch.base_rotations_world,
        result.segment_correction_rotvec,
        engine.pose.geometry,
    )
    for node, point in NODE_TO_PROXY_POINT.items():
        np.testing.assert_allclose(
            result.node_position_m[node], result.root_position_m + points[point],
            rtol=0.0, atol=1e-12,
        )
    # Per-node provisional roots are selection diagnostics only; none is a
    # separately published node Cartesian state.
    for assessment in selection_plan.selection.assessments:
        assert assessment.root_position_m.shape == (3,)
        assert not np.array_equal(
            assessment.root_position_m, result.node_position_m[assessment.node]
        )

    # Preparation, including real adaptive selection and IK, is inert until
    # the outer continuous owner atomically commits its prepared token.
    assert engine.root.publication_token().digest == root_before.digest
    assert engine.pose.publication_token().digest == pose_before.digest
    assert engine.robust.revision == robust_before
    owner.commit_continuous_event(prepared)
    assert engine.root.publication_token().revision == root_before.revision + 1
    assert engine.robust.revision == robust_before + 1
    if count == 1:
        assert engine.pose.publication_token().digest == pose_before.digest
    else:
        assert engine.pose.publication_token().revision == pose_before.revision + 1
    assert np.linalg.norm(
        engine.root.current_state.vector[:3] - root_before.state.vector[:3]
    ) > 0.0
    # Phase A authoritative UWB admission is position-only: cross covariance
    # must not inject accelerometer-bias state.
    assert (
        engine.root.current_state.vector[6:9].tobytes()
        == root_before.state.vector[6:9].tobytes()
    )
    assert tuple(
        frame.imu_owner_sha256 for frame in composition.history.frames
    ) == upstream_imu_identity_before
    assert tuple(
        (frame.source_global_ns, frame.imu_owner_sha256, frame.pose_publication_digest)
        for frame in composition.history.frames
    ) == source_identity


def test_b3_uwb_only_admission_queues_exact_observation_transactionally():
    owner, rows, availability_ns = _real_owner_fixture()
    drift = _enable_consensus_drift(owner)
    before = owner.continuous_snapshot()
    prepared = None
    for row in sorted(rows, key=lambda item: item.node):
        clock = owner._composition.engine.static.clocks[row.node]
        common_ns = int(round(clock.link_time_ns(
            event_boot_epoch=row.boot, strobe_us=row.strobe_us,
            t_round_us=0.0,
        )))
        event = ContinuousEvent(
            f"b3-uwb:{row.node}", "UWB", 0, "00_initial_still",
            common_ns, availability_ns, row.node, row.boot,
            "B306_TIMER2", "a" * 64, "b" * 64, "c" * 64,
            "host-only", row,
            uwb_timer2=UwbTimer2Fields(row.strobe_us, row.frame_us),
        )
        prepared = owner.prepare_continuous_event(event, commit_uwb=True)
        owner.commit_continuous_event(prepared)
    assert drift.pending_count == 1
    package = drift._state.pending[0]
    admission = prepared.token.admission
    assert package.admission_digest == admission.public_candidate_digest
    assert package.root_plan_digest == admission.causal_transaction.root_plan_digest
    candidate_batch = _next_native200_events(owner, 16)
    assert owner.native200_record_batch_classification(
        candidate_batch,
    ) == "complete_pending"
    preavailability_frames = 0
    while (
        owner._composition.engine.root.current_state.time_s
        < package.availability_time_ns * 1e-9
    ):
        event = _append_real_native(owner)
        if event.common_global_ns < package.availability_time_ns:
            preavailability_frames += 1
            assert drift.pending_count == 1
    assert drift.pending_count == 0
    assert preavailability_frames > 0
    assert drift._state.last_consumed_native200_time_s is not None
    assert drift._state.last_consumed_native200_time_s >= (
        package.availability_time_ns * 1e-9
    )
    owner.restore_continuous_snapshot(before)
    assert drift.pending_count == 0


def test_b3_uwb_admission_and_drift_failure_restore_all_composition_owners(
    monkeypatch,
):
    owner, rows, availability_ns = _real_owner_fixture()
    drift = _enable_consensus_drift(owner)
    prepared = None
    for row in sorted(rows, key=lambda item: item.node):
        clock = owner._composition.engine.static.clocks[row.node]
        common_ns = int(round(clock.link_time_ns(
            event_boot_epoch=row.boot, strobe_us=row.strobe_us,
            t_round_us=0.0,
        )))
        event = ContinuousEvent(
            f"b3-rollback:{row.node}", "UWB", 0, "00_initial_still",
            common_ns, availability_ns, row.node, row.boot,
            "B306_TIMER2", "a" * 64, "b" * 64, "c" * 64,
            "host-only", row,
            uwb_timer2=UwbTimer2Fields(row.strobe_us, row.frame_us),
        )
        prepared = owner.prepare_continuous_event(event, commit_uwb=True)
        if prepared.token.admission is None:
            owner.commit_continuous_event(prepared)
    assert prepared.token.admission is not None
    assert prepared.token.consensus_admission is not None
    before = _composition_fingerprint(owner._composition)
    original = ContinuousConsensusDriftOwner.commit

    def fail_after_drift_commit(self, plan):
        original(self, plan)
        raise RuntimeError("injected after consensus drift commit")

    monkeypatch.setattr(ContinuousConsensusDriftOwner, "commit", fail_after_drift_commit)
    with pytest.raises(RuntimeError, match="injected after consensus drift commit"):
        owner.commit_continuous_event(prepared)
    assert _composition_fingerprint(owner._composition) == before
    assert drift.pending_count == 0
    with pytest.raises(RuntimeError):
        owner.commit_continuous_event(prepared)


def test_b3_same_event_future_base_consumes_exact_frame_once():
    owner, rows, availability_ns = _real_owner_fixture()
    for _ in range(8):
        _append_real_native(owner)
    drift = _enable_consensus_drift(owner)
    composition = owner._composition
    materialized = composition.materialize_group(
        rows, availability_global_ns=availability_ns,
        member_region_identities=("00_initial_still",) * 10,
        evidence_class="ACTION_EVIDENCE",
    )
    admission = composition.prepare_admission(
        materialized.packet, materialized.epoch,
        allow_obsolete_native200_root_fallback=True,
    )
    prior = composition.history.frames[-1]
    timer_us = prior.source_timer_us + 5_000
    frame = _owned_frame(
        composition.engine, _engine_and_packet_for_frame(), timer_us,
        prior.publication_revision + 1,
    )
    event = ContinuousEvent(
        "b3-same-event", "IMU", 0, "00_initial_still",
        frame.source_global_ns,
        round(frame.imu_sample.availability_time_s * 1e9),
        frame.node, frame.boot_epoch, "B306_TIMER2",
        frame.clock_mapping_digest, frame.clock_owner_sha256,
        frame.clock_source_sha256, "host-only", frame,
        imu_timer2=ImuTimer2Fields(timer_us - 5_000, timer_us),
    )
    drift_admission = composition.prepare_consensus_admission(admission)
    before = _composition_fingerprint(composition)
    prepared = composition.prepare_native200(
        event, admission=admission, commit_admission=True,
        consensus_admission=drift_admission,
    )
    assert _composition_fingerprint(composition) == before
    assert prepared.drift_plan._dependency_plan is drift_admission
    assert prepared.history_plan.frame_digest == frame.digest
    composition.commit_admission(admission)
    composition.commit_native200(prepared)
    assert drift.pending_count == 0
    assert (
        drift._state.last_consumed_native200_time_s
        == frame.imu_sample.measurement_time_s
    )
    with pytest.raises(RuntimeError):
        composition.commit_native200(prepared)


def _prepare_b3_same_event_outer_token(owner):
    for _ in range(8):
        _append_real_native(owner)
    composition = owner._composition
    rows_owner, rows, availability_ns = _real_owner_fixture()
    del rows_owner
    materialized = composition.materialize_group(
        rows, availability_global_ns=availability_ns,
        member_region_identities=("00_initial_still",) * 10,
        evidence_class="ACTION_EVIDENCE",
    )
    admission = composition.prepare_admission(
        materialized.packet, materialized.epoch,
        allow_obsolete_native200_root_fallback=True,
    )
    prior = composition.history.frames[-1]
    timer_us = prior.source_timer_us + 5_000
    frame = _owned_frame(
        composition.engine, _engine_and_packet_for_frame(), timer_us,
        prior.publication_revision + 1,
    )
    event = ContinuousEvent(
        "b3-outer-same-event", "IMU", 0, "00_initial_still",
        frame.source_global_ns,
        round(frame.imu_sample.availability_time_s * 1e9),
        frame.node, frame.boot_epoch, "B306_TIMER2",
        frame.clock_mapping_digest, frame.clock_owner_sha256,
        frame.clock_source_sha256, "host-only", frame,
        imu_timer2=ImuTimer2Fields(timer_us - 5_000, timer_us),
    )
    drift_admission = composition.prepare_consensus_admission(admission)
    native = composition.prepare_native200(
        event, admission=admission, commit_admission=True,
        consensus_admission=drift_admission,
    )
    ordinary = owner.prepare_continuous_event(event, commit_uwb=False)
    token = replace(
        ordinary.token, admission=admission, commit_admission=True,
        consensus_admission=drift_admission,
        consensus_admission_embedded=True,
        native200_prepared=native,
        composition_snapshot=composition.snapshot(),
    )
    return replace(ordinary, token=token), native


def test_b3_same_event_post_drift_failure_restores_all_and_is_one_shot(
    monkeypatch,
):
    owner, _rows, _availability_ns = _real_owner_fixture()
    drift = _enable_consensus_drift(owner)
    prepared, native = _prepare_b3_same_event_outer_token(owner)
    before = _composition_fingerprint(owner._composition)
    original = ContinuousConsensusDriftOwner.commit

    def fail_after_drift_commit(self, plan):
        original(self, plan)
        raise RuntimeError("injected after same-event drift commit")

    monkeypatch.setattr(ContinuousConsensusDriftOwner, "commit", fail_after_drift_commit)
    with pytest.raises(RuntimeError, match="injected after same-event drift commit"):
        owner.commit_continuous_event(prepared)
    assert _composition_fingerprint(owner._composition) == before
    assert drift.pending_count == 0
    assert native.drift_plan._commit_state.consumed
    assert native.drift_admission_plan._commit_state.consumed
    with pytest.raises(RuntimeError):
        owner.commit_continuous_event(prepared)


def test_b3_native_override_binds_frame_sample_source_pair_and_pose():
    owner, _rows, _availability_ns = _real_owner_fixture()
    _enable_consensus_drift(owner)
    _prepared, native = _prepare_b3_same_event_outer_token(owner)
    history = native.history_plan
    frame = history.native.frame
    changed_sample = replace(
        frame.imu_sample, source_sequence=frame.imu_sample.source_sequence + 1,
    )
    changed_frame = replace(frame, imu_sample=changed_sample, digest="")
    tampered_native = (
        replace(history.native, frame=changed_frame),
        replace(history.native, source_pair=None),
        replace(history.native, previous_base_pose=None),
    )
    for changed in tampered_native:
        bad = replace(history, native=changed)
        with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN_NATIVE200_ROOT_OVERRIDE"):
            owner._composition.history.commit_native200_root_override(bad)
    with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN_NATIVE200_ROOT_OVERRIDE"):
        owner._composition.history.commit_native200_root_override(
            replace(history, frame_digest="0" * 64),
        )


def test_b3_gap_suppresses_partial_admission_and_clears_consensus_state():
    owner, rows, availability_ns = _real_owner_fixture()
    drift = _enable_consensus_drift(owner)
    composition = owner._composition
    materialized = composition.materialize_group(
        rows, availability_global_ns=availability_ns,
        member_region_identities=("00_initial_still",) * 10,
        evidence_class="ACTION_EVIDENCE",
    )
    admission = composition.prepare_admission(
        materialized.packet, materialized.epoch,
    )
    staged = composition.prepare_consensus_admission(admission)
    composition.commit_consensus_admission(staged)
    ledger = drift.cumulative_absolute_position_correction_m.copy()
    assert drift.pending_count == 1
    for row in sorted(rows[:5], key=lambda item: item.node):
        shifted = replace(
            row, strobe_us=row.strobe_us + EPOCH_PERIOD_NS // 1_000,
            frame_us=row.frame_us + EPOCH_PERIOD_NS // 1_000,
        )
        clock = composition.engine.static.clocks[row.node]
        common_ns = int(round(clock.link_time_ns(
            event_boot_epoch=shifted.boot, strobe_us=shifted.strobe_us,
            t_round_us=0.0,
        )))
        event = ContinuousEvent(
            f"b3-partial-gap:{row.node}", "UWB", 0, "00_initial_still",
            common_ns, availability_ns + EPOCH_PERIOD_NS, row.node, row.boot,
            "B306_TIMER2", "a" * 64, "b" * 64, "c" * 64,
            "host-only", shifted,
            uwb_timer2=UwbTimer2Fields(shifted.strobe_us, shifted.frame_us),
        )
        owner.commit_continuous_event(
            owner.prepare_continuous_event(event, commit_uwb=True)
        )
    prepared = owner.prepare_continuous_event(
        _real_gap_event(owner), commit_uwb=True,
    )
    assert prepared.token.admission is None
    owner.commit_continuous_event(prepared)
    assert drift.pending_count == 0
    np.testing.assert_array_equal(
        drift.cumulative_absolute_position_correction_m, ledger,
    )
    assert owner.journal[-1].reason == "INCOMPLETE_GROUP_SUPPRESSED_AT_GAP"


def test_complete_uwb_group_commits_before_following_gap_without_suppression():
    owner, rows, availability_ns = _real_owner_fixture()
    _enable_consensus_drift(owner)
    for row in sorted(rows, key=lambda item: item.node):
        clock = owner._composition.engine.static.clocks[row.node]
        common_ns = int(round(clock.link_time_ns(
            event_boot_epoch=row.boot, strobe_us=row.strobe_us,
            t_round_us=0.0,
        )))
        event = ContinuousEvent(
            f"complete-before-gap:{row.node}", "UWB", 0,
            "00_initial_still", common_ns, availability_ns,
            row.node, row.boot, "B306_TIMER2", "a" * 64, "b" * 64,
            "c" * 64, "host-only", row,
            uwb_timer2=UwbTimer2Fields(row.strobe_us, row.frame_us),
        )
        owner.commit_continuous_event(
            owner.prepare_continuous_event(event, commit_uwb=True)
        )
    assert owner.admission_journal[-1].outcome == "UWB_COMMIT_SUCCEEDED"
    committed_admissions = len(owner.admission_journal)
    gap = _real_gap_event(owner)
    gap = replace(
        gap, availability_global_ns=max(
            gap.availability_global_ns, availability_ns,
        ),
    )
    owner.commit_continuous_event(owner.prepare_continuous_event(
        gap, commit_uwb=False,
    ))
    assert len(owner.admission_journal) == committed_admissions
    assert owner.journal[-1].reason != "INCOMPLETE_GROUP_SUPPRESSED_AT_GAP"
