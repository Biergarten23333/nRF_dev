from __future__ import annotations

from dataclasses import replace
import hashlib

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.production_root_admission_policy import (
    AdmissionDisposition,
    DiagnosticContinuitySpec,
    DiagnosticReanchorSpec,
    DiagnosticRootAdmissionFixture,
    DiagnosticRootAdmissionMachine,
    DiagnosticRootAdmissionPolicy,
    DiagnosticStatisticalSpec,
    PreparedContinuityInput,
    PreparedRootAdmissionInput,
    PreparedStatisticalInput,
    ProductionRootAdmissionMachine,
    load_production_root_admission_policy,
)
from biospur_fusion.root_r3.models import RootState


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _state(time_s: float, position=(0.0, 0.0, 0.0), tail=()) -> RootState:
    vector = np.zeros(9, dtype=float)
    vector[:3] = position
    if tail:
        vector[3:] = tail
    return RootState(time_s, vector, np.eye(9, dtype=float))


def _policy() -> DiagnosticRootAdmissionPolicy:
    # All numbers below are explicit synthetic mechanism-fixture payloads.  The
    # type cannot create or promote an unqualified production owner.
    statistical = DiagnosticStatisticalSpec(
        0.01,
        ((3, 11.34),),
        3,
    )
    continuity = DiagnosticContinuitySpec(
        (0.0, 0.005, 0.12),
        (0.05, 0.055, 0.09),
        _sha("transition-model"),
    )
    reanchor = DiagnosticReanchorSpec(
        0.005,
        0.12,
        3,
        0.24,
        10,
        4,
    )
    return DiagnosticRootAdmissionPolicy(
        statistical, continuity, reanchor, "synthetic mechanism fixture only",
    )


def _prepared(
    policy: DiagnosticRootAdmissionPolicy,
    *,
    sequence: int,
    time_s: float,
    trusted=("N0",),
    full_delta=(0.20, 0.0, 0.0),
    bounded_delta=(0.02, 0.0, 0.0),
    integrity=True,
    root_only=True,
) -> PreparedRootAdmissionInput:
    predicted = _state(time_s)
    tail = () if len(trusted) == 1 else (0.1, 0, 0, 0.01, 0, 0)
    bounded_tail = () if len(trusted) == 1 else (0.02, 0, 0, 0.002, 0, 0)
    full = _state(time_s, full_delta, tail=tail)
    bounded = _state(time_s, bounded_delta, tail=bounded_tail)
    stat = PreparedStatisticalInput(
        np.array([0.1, 0.0, 0.0]),
        np.eye(3),
        3,
        policy.statistical.false_admission_probability,
        policy.statistical.threshold_for_dof(3),
        3,
        True,
        integrity,
        True,
        policy.statistical.digest,
    )

    def continuity(candidate: RootState) -> PreparedContinuityInput:
        delta = candidate.vector[:3] - predicted.vector[:3]
        return PreparedContinuityInput(
            policy.continuity.prediction_horizons_s,
            np.stack((delta, delta * 1.02, delta * 1.1)),
            policy.continuity.transition_model_sha256,
            policy.continuity.digest,
        )

    nodes = tuple(f"N{index}" for index in range(10))
    counts = {node: (4 if node in trusted else 0) for node in nodes}
    return PreparedRootAdmissionInput(
        f"event-{sequence}", sequence, time_s, time_s,
        predicted, full, bounded, stat, continuity(full), continuity(bounded),
        nodes, tuple(trusted), counts, root_only, policy.digest,
    )


def test_production_loader_is_empty_and_diagnostic_is_non_promotable(tmp_path) -> None:
    policy = _policy()
    invented = tmp_path / "invented.json"
    invented.write_text("{}")
    with pytest.raises(RuntimeError, match="REGISTRY_EMPTY"):
        load_production_root_admission_policy(invented)
    with pytest.raises(TypeError, match="registry-issued"):
        ProductionRootAdmissionMachine(policy, _state(0.0))
    fixture = DiagnosticRootAdmissionFixture(
        "biospur.c2.diagnostic_root_admission_fixture.v1",
        "synthetic only",
        _sha("diagnostic-2"),
    )
    assert not fixture.production_qualified
    assert not fixture.product_ready
    assert not fixture.scientific_pass
    assert not policy.product_ready
    assert not policy.scientific_pass


def test_normal_credible_continuous_update_commits_immediately() -> None:
    policy = _policy()
    machine = DiagnosticRootAdmissionMachine(policy, _state(0.0))
    assert not machine.product_ready and not machine.scientific_pass
    item = _prepared(
        policy, sequence=1, time_s=1.0, trusted=("N0", "N1"),
        full_delta=(0.02, 0.0, 0.0), bounded_delta=(0.01, 0.0, 0.0),
    )
    before_prepare = machine.owner_bytes()
    prepared = machine.prepare(item)
    assert machine.owner_bytes() == before_prepare
    assert prepared.decision.disposition is AdmissionDisposition.ACCEPT_COMMIT_ROOT
    assert prepared.decision.reason == "STATISTICALLY_CREDIBLE_ATOMIC_UPDATE_COMMITTED"
    machine.commit(prepared)
    np.testing.assert_array_equal(machine.current_state.vector, item.measurement_candidate.vector)


def test_multi_node_direct_partition_rejects_any_under_minimum_link_node() -> None:
    policy = _policy()
    machine = DiagnosticRootAdmissionMachine(policy, _state(0.0))
    item = _prepared(
        policy, sequence=1, time_s=1.0, trusted=("N0", "N1"),
        full_delta=(0.02, 0.0, 0.0), bounded_delta=(0.01, 0.0, 0.0),
    )
    counts = dict(item.valid_anchor_links_by_node)
    counts["N1"] = 0
    item = replace(item, valid_anchor_links_by_node=counts, digest="")
    before = machine.owner_bytes()
    decision = machine.commit(machine.prepare(item))
    assert decision.disposition is AdmissionDisposition.REJECT_NO_EVENT
    assert decision.reason == "TRUSTED_NODE_LINK_GEOMETRY_REJECT"
    assert machine.owner_bytes() == before


def test_prepared_link_inventory_is_immutable_after_digest() -> None:
    policy = _policy()
    item = _prepared(policy, sequence=1, time_s=1.0, trusted=("N0", "N1"))
    with pytest.raises(TypeError):
        item.valid_anchor_links_by_node["N1"] = 0


def test_one_node_requires_temporal_evidence_then_commits_current_bounded_state() -> None:
    policy = _policy()
    machine = DiagnosticRootAdmissionMachine(policy, _state(0.0))
    held_candidates = []
    for sequence, time_s in enumerate((1.0, 1.12), 1):
        item = _prepared(policy, sequence=sequence, time_s=time_s)
        held_candidates.append(item.bounded_reanchor_candidate)
        before_root = machine.current_state
        prepared = machine.prepare(item)
        assert prepared.decision.disposition is AdmissionDisposition.HOLD_TEMPORAL_EVIDENCE
        assert prepared.decision.direct_nodes == ("N0",)
        assert len(prepared.decision.propagated_nodes) == 9
        machine.commit(prepared)
        np.testing.assert_array_equal(machine.current_state.vector, before_root.vector)
    current = _prepared(policy, sequence=3, time_s=1.24, bounded_delta=(0.03, 0.0, 0.0))
    prepared = machine.prepare(current)
    assert prepared.decision.disposition is AdmissionDisposition.ACCEPT_COMMIT_ROOT
    assert prepared.decision.reason == "FRESH_CAUSAL_BOUNDED_REANCHOR_COMMITTED"
    machine.commit(prepared)
    np.testing.assert_array_equal(
        machine.current_state.vector, current.bounded_reanchor_candidate.vector,
    )
    assert not np.array_equal(machine.current_state.vector, held_candidates[-1].vector)


@pytest.mark.parametrize("mutation", (
    "measurement_integrity", "nis_threshold", "full_source", "bounded_source",
    "policy_source", "continuity_effect", "one_node_geometry", "one_node_pose",
    "one_node_state_leak",
))
def test_rejection_is_byte_exact_no_event(mutation: str) -> None:
    policy = _policy()
    machine = DiagnosticRootAdmissionMachine(policy, _state(0.0))
    item = _prepared(policy, sequence=1, time_s=1.0)
    if mutation == "measurement_integrity":
        item = replace(item, statistical=replace(item.statistical, measurement_integrity_valid=False), digest="")
    elif mutation == "nis_threshold":
        item = replace(item, statistical=replace(item.statistical, nis_threshold=99.0), digest="")
    elif mutation == "full_source":
        item = replace(item, continuity=replace(item.continuity, source_digest=_sha("foreign")), digest="")
    elif mutation == "bounded_source":
        item = replace(item, bounded_reanchor_continuity=replace(item.bounded_reanchor_continuity, source_digest=_sha("foreign")), digest="")
    elif mutation == "policy_source":
        item = replace(item, source_digest=_sha("foreign"), digest="")
    elif mutation == "continuity_effect":
        bad = replace(item.bounded_reanchor_continuity, position_effect_m=np.array([[0.2, 0, 0], [0.2, 0, 0], [0.2, 0, 0]]))
        item = replace(item, bounded_reanchor_candidate=_state(1.0, (0.2, 0, 0)), bounded_reanchor_continuity=bad, digest="")
    elif mutation == "one_node_geometry":
        counts = dict(item.valid_anchor_links_by_node)
        counts["N0"] = 3
        item = replace(item, valid_anchor_links_by_node=counts, digest="")
    elif mutation == "one_node_state_leak":
        leaked = item.bounded_reanchor_candidate.vector.copy()
        leaked[3] = 0.01
        item = replace(
            item,
            bounded_reanchor_candidate=RootState(
                item.measurement_time_s,
                leaked,
                item.bounded_reanchor_candidate.covariance,
            ),
            digest="",
        )
    else:
        item = replace(item, root_translation_only=False, digest="")
    before = machine.owner_bytes()
    decision = machine.commit(machine.prepare(item))
    assert decision.disposition is AdmissionDisposition.REJECT_NO_EVENT
    assert machine.owner_bytes() == before


def test_gap_restart_replay_one_shot_snapshot_rollback_and_clone_identity() -> None:
    policy = _policy()
    machine = DiagnosticRootAdmissionMachine(policy, _state(0.0))
    initial = machine.snapshot()
    first = _prepared(policy, sequence=1, time_s=1.0)
    prepared = machine.prepare(first)
    machine.commit(prepared)
    with pytest.raises(RuntimeError, match="STALE_REPLAYED_OR_FOREIGN"):
        machine.commit(prepared)
    after_first = machine.owner_bytes()
    stale = machine.commit(machine.prepare(first))
    assert stale.disposition is AdmissionDisposition.REJECT_NO_EVENT
    assert machine.owner_bytes() == after_first
    gap = _prepared(policy, sequence=2, time_s=1.30)
    gap_decision = machine.commit(machine.prepare(gap))
    assert gap_decision.temporal_count == 1
    clone = machine.clone()
    assert clone.owner_bytes() == machine.owner_bytes()
    assert not np.shares_memory(clone.current_state.vector, machine.current_state.vector)
    with pytest.raises(RuntimeError, match="FOREIGN_ROOT_ADMISSION_ROLLBACK"):
        clone.rollback(machine.snapshot())
    machine.rollback(initial)
    assert machine.revision == 0
    np.testing.assert_array_equal(machine.current_state.vector, _state(0.0).vector)
