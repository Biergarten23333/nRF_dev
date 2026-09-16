from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.root_r3 import (
    CausalDelayedRootFilter,
    ImuSample,
    PositionObservation,
    RootState,
)
from biospur_fusion.root_r3.estimator import _root_imu_bundle_digest


def _root() -> CausalDelayedRootFilter:
    root = CausalDelayedRootFilter(RootState(0.0, np.zeros(9), np.eye(9)))
    assert root.add_imu(ImuSample(
        0.005, 0.005, np.array([0.4, 0.0, 9.80665]), np.eye(3), 1,
    ))
    return root


def _full_digest(root: CausalDelayedRootFilter) -> str:
    return _root_imu_bundle_digest(root._prepare_position_rollback())


def _full_owner(root: CausalDelayedRootFilter):
    return (
        _full_digest(root),
        tuple(sorted(root._CausalDelayedRootFilter__consumed_imu_plans)),
        tuple(sorted(root._CausalDelayedRootFilter__consumed_position_velocity_plans)),
        tuple(sorted(root._CausalDelayedRootFilter__consumed_position_rejection_plans)),
    )


def _observation(reference: float, availability: float) -> PositionObservation:
    return PositionObservation(
        reference, availability, np.array([0.01, 0.0, 0.0]), np.eye(3),
        "BSFC2CC", (0, 1, 2, 3), source_sequence=2,
    )


def test_causal_state_predicts_between_imu_without_mutation_and_binds_position():
    root = _root()
    before = _full_owner(root)
    token = root.prepare_causal_state(0.006, 0.010)
    assert _full_owner(root) == before
    assert token.state.time_s == 0.006
    assert token.state.vector.tobytes() != root.current_state.vector.tobytes()

    plan = root.prepare_position_from_causal_state(
        token, _observation(0.006, 0.010), state_update_indices=(0, 1, 2),
    )
    assert plan.observation.measurement_time_s == token.reference_time_s
    assert plan.processing_time_s == token.availability_time_s
    assert plan.causal_state_digest == token.digest
    assert _full_owner(root) == before
    repeated = root.prepare_position_from_causal_state(
        token, _observation(0.006, 0.010), state_update_indices=(0, 1, 2),
    )
    assert repeated.digest == plan.digest
    assert _full_owner(root) == before


def test_causal_state_commit_is_one_shot_rollback_safe_and_continues():
    root = _root()
    before = _full_owner(root)
    token = root.prepare_causal_state(0.006, 0.010)
    position = root.prepare_position_from_causal_state(
        token, _observation(0.006, 0.010), state_update_indices=(0, 1, 2),
    )
    compound = root.prepare_position_velocity_transaction(
        position, velocity_delta_mps=np.zeros(3),
        maximum_velocity_step_mps=0.02, owner="CAUSAL_STATE_TEST",
    )
    root.commit_position_velocity_transaction(position, compound)
    with pytest.raises(RuntimeError, match="STALE_ROOT_POSITION_PLAN"):
        root.commit_position_velocity_transaction(position, compound)
    root.rollback_committed_position_velocity_transaction(compound)
    assert _full_digest(root) == before[0]
    assert _full_owner(root)[2] == (compound.digest,)
    with pytest.raises(RuntimeError, match="STALE_FORGED_OR_FOREIGN"):
        root.commit_position_velocity_transaction(position, compound)

    later = root.prepare_causal_state(0.007, 0.011)
    later_position = root.prepare_position_from_causal_state(
        later, _observation(0.007, 0.011), state_update_indices=(0, 1, 2),
    )
    later_compound = root.prepare_position_velocity_transaction(
        later_position, velocity_delta_mps=np.zeros(3),
        maximum_velocity_step_mps=0.02, owner="CAUSAL_STATE_TEST_LATER",
    )
    root.commit_position_velocity_transaction(later_position, later_compound)


def test_exception_after_causal_prepare_leaves_complete_owner_unchanged():
    root = _root()
    before = _full_owner(root)
    token = root.prepare_causal_state(0.006, 0.010)
    with pytest.raises(ValueError):
        root.prepare_position_from_causal_state(
            token,
            replace(_observation(0.006, 0.010), covariance_m2=np.zeros((2, 2))),
            state_update_indices=(0, 1, 2),
        )
    assert _full_owner(root) == before


def test_historical_causal_state_is_byte_exact_committed_state():
    root = _root()
    committed = root.committed_state_at(0.0025)
    token = root.prepare_causal_state(0.0025, root.current_state.time_s)
    assert token.state.time_s == committed.state.time_s
    assert token.state.vector.tobytes() == committed.state.vector.tobytes()
    assert token.state.covariance.tobytes() == committed.state.covariance.tobytes()


def test_causal_state_foreign_tampered_and_stale_are_full_noops():
    root = _root()
    other = _root()
    token = root.prepare_causal_state(0.006, 0.010)
    cases = (
        (other, token),
        (root, replace(token, state=RootState(
            token.state.time_s, token.state.vector + 1.0, token.state.covariance,
        ))),
    )
    for target, candidate in cases:
        before = _full_owner(target)
        with pytest.raises(RuntimeError, match="CAUSAL_STATE"):
            target.prepare_position_from_causal_state(
                candidate, _observation(0.006, 0.010),
                state_update_indices=(0, 1, 2),
            )
        assert _full_owner(target) == before

    stale = root.prepare_causal_state(0.006, 0.010)
    root.emit(root.current_state.time_s)
    after_emit = _full_owner(root)
    with pytest.raises(RuntimeError, match="CAUSAL_STATE"):
        root.prepare_position_from_causal_state(
            stale, _observation(0.006, 0.010), state_update_indices=(0, 1, 2),
        )
    assert _full_owner(root) == after_emit


def test_non_token_is_rejected_before_field_access_and_is_inert():
    root = _root()
    before = _full_owner(root)
    with pytest.raises(RuntimeError, match="CAUSAL_STATE"):
        root.prepare_position_from_causal_state(
            object(), _observation(0.006, 0.010),
            state_update_indices=(0, 1, 2),
        )
    assert _full_owner(root) == before


def test_causal_state_rejects_reference_after_availability_without_mutation():
    root = _root()
    before = _full_owner(root)
    with pytest.raises(ValueError, match="reference/availability"):
        root.prepare_causal_state(0.011, 0.010)
    assert _full_owner(root) == before
