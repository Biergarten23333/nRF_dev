from copy import deepcopy
from dataclasses import replace
import pickle

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world import continuous_consensus_drift as module
from biospur_fusion.c2_uwb_root_world.continuous_consensus_drift import (
    ContinuousConsensusDriftOwner,
)
from biospur_fusion.c2_uwb_root_world.split_fusion import (
    FixedLagConsensusDriftConfig,
)
from biospur_fusion.root_r3.models import PositionObservation, RootState
from test_c2_authoritative_articulated_fusion import (
    _engine_and_packet,
    _epoch,
    _packet_with_nodes,
)


def _config(*, pairs=1, cap=0.05):
    return FixedLagConsensusDriftConfig(
        minimum_lag_s=0.10,
        maximum_lag_s=0.30,
        update_period_s=0.10,
        minimum_consensus_pairs=pairs,
        rank_relative_tolerance=1e-2,
        maximum_velocity_step_mps=cap,
        covariance_floor=1e-12,
    )


def _owner(**kwargs):
    return ContinuousConsensusDriftOwner(
        config=_config(**kwargs), stream_owner_digest="d" * 64,
    )


def _accepted(count=1):
    engine, packet = _engine_and_packet(clock_owner_sha256="a" * 64)
    packet = _packet_with_nodes(packet, count)
    admission = engine.prepare_admission(packet, _epoch(engine, packet))
    assert admission.prepared_result.accepted
    return admission


def _state(time_s, velocity=None):
    vector = np.zeros(9)
    if velocity is not None:
        vector[3:6] = velocity
    return RootState(time_s, vector, np.eye(9) * 0.1)


def _package(owner, *, measurement, availability, sequence, position,
             anchors=(0, 1, 2, 3), trusted=("N0",)):
    staged = owner.prepare_admission(_accepted(1))
    owner.commit(staged)
    base = owner._state.pending[0]
    observation = PositionObservation(
        measurement, availability, np.asarray(position, float),
        np.eye(3) * 0.01, base.observation.tag_id, tuple(anchors),
        base.observation.quality_state, True, True, sequence,
    )
    observation = module._readonly_observation(observation)
    blank = replace(
        base, observation=observation,
        availability_time_ns=int(round(availability * 1e9)),
        trusted_nodes=tuple(trusted), anchors_used=tuple(anchors),
        post_absolute_position_at_measurement_m=module._readonly_array(
            np.zeros(3), (3,)
        ),
        applied_absolute_position_delta_m=module._readonly_array(
            np.zeros(3), (3,)
        ),
        cumulative_absolute_position_correction_m=module._readonly_array(
            np.zeros(3), (3,)
        ),
        digest="",
    )
    package = replace(blank, digest=module._package_digest(blank))
    # Test construction above uses the public admission path to obtain the
    # schema, then starts a fresh owner for isolated scheduling assertions.
    fresh = _owner(cap=owner.config.maximum_velocity_step_mps,
                   pairs=owner.config.minimum_consensus_pairs)
    return fresh, package


def _install_pending(owner, packages):
    owner._state = replace(
        owner._state, pending=tuple(packages), revision=1,
        last_measurement_time_s=packages[-1].observation.measurement_time_s,
        last_availability_time_ns=packages[-1].availability_time_ns,
        last_source_sequence=packages[-1].observation.source_sequence,
    )


@pytest.mark.parametrize("count", (1, 4, 10))
def test_admission_retains_exact_b1_facts_without_alias(count):
    admission = _accepted(count)
    owner = _owner()
    before = owner.owner_digest
    plan = owner.prepare_admission(admission)
    assert owner.owner_digest == before
    result = owner.commit(plan)
    assert result.accepted and result.reason == "QUEUED"
    package = owner._state.pending[0]
    observation = admission.root_observation
    assert package.observation is not observation
    assert package.observation.root_position_m is not observation.root_position_m
    assert package.observation.covariance_m2 is not observation.covariance_m2
    assert not package.observation.root_position_m.flags.writeable
    assert not package.observation.covariance_m2.flags.writeable
    assert module._observation_digest(package.observation) == module._observation_digest(observation)
    assert package.trusted_nodes == admission.trusted_partition
    assert package.anchors_used == observation.anchors
    assert package.packet_digest == admission.packet_digest
    assert package.epoch_digest == admission.epoch_digest
    assert package.root_plan_digest == admission.causal_transaction.root_plan_digest
    assert package.admission_digest == admission.public_candidate_digest
    assert package.availability_time_ns == admission.packet.availability_global_ns


def test_measurement_epoch_posterior_is_not_processing_horizon_candidate():
    admission = _accepted(1)
    plan = admission.causal_transaction.root_plan
    owner = _owner()
    owner.commit(owner.prepare_admission(admission))
    package = owner._state.pending[0]
    expected = (
        admission.root_observation.root_position_m
        - plan.decision.innovation_m
        + plan.decision.applied_position_delta_m
    )
    np.testing.assert_array_equal(
        package.post_absolute_position_at_measurement_m, expected,
    )
    assert plan.processing_time_s > admission.root_observation.measurement_time_s
    assert (
        package.post_absolute_position_at_measurement_m.tobytes()
        != plan.measurement_candidate.position_m.tobytes()
    )


def test_rejected_and_replayed_admissions_are_inert():
    owner = _owner()
    rejected = _engine_and_packet(nominal_displacement=1e-9)[0:2]
    engine, packet = rejected
    admission = engine.prepare_admission(packet, _epoch(engine, packet))
    before = owner.owner_digest
    result = owner.commit(owner.prepare_admission(admission))
    assert not result.accepted and owner.owner_digest == before
    accepted = _accepted(1)
    owner.commit(owner.prepare_admission(accepted))
    after = owner.owner_digest
    with pytest.raises(RuntimeError, match="REPLAYED"):
        owner.prepare_admission(accepted)
    assert owner.owner_digest == after


def test_preavailability_is_noop_and_fresh_noop_plans_do_not_collide():
    owner, package = _package(
        _owner(), measurement=0.10, availability=0.20, sequence=1,
        position=[0.0, 0.0, 0.0],
    )
    _install_pending(owner, (package,))
    before = owner.owner_digest
    first = owner.prepare_native200(_state(0.195))
    assert owner.commit(first).reason == "OBSERVATION_NOT_AVAILABLE"
    assert owner.owner_digest == before and owner.pending_count == 1
    with pytest.raises(RuntimeError, match="CONSUMED"):
        owner.commit(first)
    second = owner.prepare_native200(_state(0.195))
    assert owner.commit(second).reason == "OBSERVATION_NOT_AVAILABLE"
    assert owner.owner_digest == before


def test_first_eligible_frame_consumes_oldest_only_and_two_need_two_frames():
    owner, first = _package(
        _owner(), measurement=0.00, availability=0.05, sequence=1,
        position=[0.0, 0.0, 0.0], anchors=(0, 1, 2, 3), trusted=("N0",),
    )
    _, second = _package(
        _owner(), measurement=0.12, availability=0.17, sequence=2,
        position=[0.024, 0.0, 0.0], anchors=(4, 5, 6, 7),
        trusted=("N1", "N2", "N3", "N4"),
    )
    _install_pending(owner, (first, second))
    result1 = owner.commit(owner.prepare_native200(_state(0.20)))
    assert result1.consumed_observation_digest == first.digest
    assert result1.reason == "FIXED_LAG_WARMUP"
    assert owner.pending_count == 1
    result2 = owner.commit(owner.prepare_native200(_state(0.205)))
    assert result2.consumed_observation_digest == second.digest
    assert owner.pending_count == 0
    assert result2.accepted
    assert 0.0 < np.linalg.norm(result2.velocity_delta_mps) <= 0.05 + 1e-12
    np.testing.assert_array_equal(
        result2.decision.accelerometer_bias_delta_mps2, np.zeros(3),
    )


def test_late_processing_uses_stable_stream_identity_with_variable_provenance():
    owner, first = _package(
        _owner(), measurement=0.00, availability=0.05, sequence=1,
        position=[0.0, 0.0, 0.0], anchors=(0, 1, 2, 3), trusted=("N0",),
    )
    _, second = _package(
        _owner(), measurement=0.12, availability=0.17, sequence=2,
        position=[0.012, 0.0, 0.0], anchors=(2, 3, 6, 7),
        trusted=tuple(f"N{i}" for i in range(10)),
    )
    _install_pending(owner, (first, second))
    owner.commit(owner.prepare_native200(_state(0.30)))
    result = owner.commit(owner.prepare_native200(_state(0.305)))
    assert result.reason != "SOURCE_IDENTITY_MISMATCH"
    assert result.reason != "STATE_NOT_AT_OBSERVATION_AVAILABILITY"


def test_same_or_reversed_native200_frame_cannot_consume_second_package():
    owner, first = _package(
        _owner(), measurement=0.00, availability=0.05, sequence=1,
        position=[0.0, 0.0, 0.0],
    )
    _, second = _package(
        _owner(), measurement=0.12, availability=0.17, sequence=2,
        position=[0.01, 0.0, 0.0],
    )
    _install_pending(owner, (first, second))
    owner.commit(owner.prepare_native200(_state(0.20)))
    before = owner.owner_digest
    for time_s in (0.20, 0.19):
        with pytest.raises(RuntimeError, match="OUT_OF_ORDER"):
            owner.prepare_native200(_state(time_s))
        assert owner.owner_digest == before and owner.pending_count == 1


def test_rank_rejection_still_consumes_history_and_applies_zero_delta():
    owner, first = _package(
        _owner(pairs=2), measurement=0.00, availability=0.05, sequence=1,
        position=[0.0, 0.0, 0.0],
    )
    _, second = _package(
        _owner(pairs=2), measurement=0.12, availability=0.17, sequence=2,
        position=[0.02, 0.0, 0.0],
    )
    _install_pending(owner, (first, second))
    owner.commit(owner.prepare_native200(_state(0.20)))
    result = owner.commit(owner.prepare_native200(_state(0.205)))
    assert not result.accepted
    assert result.reason == "INSUFFICIENT_FIXED_LAG_ROWS"
    np.testing.assert_array_equal(result.velocity_delta_mps, np.zeros(3))
    assert owner.pending_count == 0


def test_plan_authentication_stale_foreign_tamper_and_commit_exception(monkeypatch):
    owner = _owner()
    plan = owner.prepare_admission(_accepted(1))
    foreign = _owner()
    with pytest.raises(RuntimeError, match="FOREIGN"):
        foreign.commit(plan)
    tampered = replace(plan, candidate_owner_digest="0" * 64)
    with pytest.raises(RuntimeError, match="FORGED"):
        owner.commit(tampered)
    before = owner.owner_digest
    original = owner._install_state
    calls = 0

    def fail_once(state):
        nonlocal calls
        calls += 1
        original(state)
        if calls == 1:
            raise RuntimeError("injected")

    monkeypatch.setattr(owner, "_install_state", fail_once)
    with pytest.raises(RuntimeError, match="injected"):
        owner.commit(plan)
    assert owner.owner_digest == before
    with pytest.raises(RuntimeError, match="CONSUMED"):
        owner.commit(plan)


def test_stale_plan_snapshot_restore_and_clone_are_detached():
    owner = _owner()
    snapshot = owner.snapshot()
    first = owner.prepare_admission(_accepted(1))
    stale = owner.prepare_admission(_accepted(1))
    owner.commit(first)
    with pytest.raises(RuntimeError, match="STALE"):
        owner.commit(stale)
    changed = owner.owner_digest
    clone = owner.clone()
    assert clone.owner_digest == changed
    clone.commit(clone.prepare_gap())
    assert owner.owner_digest == changed
    owner.restore(snapshot)
    assert owner.pending_count == 0
    with pytest.raises(RuntimeError, match="FOREIGN"):
        clone.restore(snapshot)


def test_gap_clears_pending_and_derivative_history_but_preserves_ledger():
    owner = _owner()
    owner.commit(owner.prepare_admission(_accepted(1)))
    ledger = owner.cumulative_absolute_position_correction_m.tobytes()
    owner.commit(owner.prepare_gap())
    assert owner.pending_count == 0
    assert owner.cumulative_absolute_position_correction_m.tobytes() == ledger
    corrector = owner._state.corrector
    assert not corrector._history
    assert not corrector._pending_design
    assert not corrector._pending_observed
    assert not corrector._pending_inverse_variance
    assert corrector._last_update_s is None


def test_full_decision_is_digest_bound():
    owner, package = _package(
        _owner(), measurement=0.0, availability=0.05, sequence=1,
        position=[0.0, 0.0, 0.0],
    )
    _install_pending(owner, (package,))
    plan = owner.prepare_native200(_state(0.05))
    changed_decision = replace(plan.result.decision, row_count=99)
    tampered = replace(plan, result=replace(plan.result, decision=changed_decision))
    with pytest.raises(RuntimeError, match="FORGED"):
        owner.commit(tampered)
