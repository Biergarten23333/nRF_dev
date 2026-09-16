from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.root_r3 import CausalDelayedRootFilter, PositionObservation, RootState


def _root() -> CausalDelayedRootFilter:
    vector = np.zeros(9)
    vector[3:6] = (0.2, -0.1, 0.05)
    vector[6:9] = (0.01, 0.02, -0.03)
    return CausalDelayedRootFilter(RootState(0.0, vector, np.eye(9)))


def _position_plan(root: CausalDelayedRootFilter):
    return root.prepare_position(
        PositionObservation(
            0.0, 0.0, np.array([0.02, -0.01, 0.01]), np.eye(3),
            "BSFC2CC", (0, 1, 2, 3), source_sequence=7,
        ),
        state_update_indices=(0, 1, 2),
    )


def _fingerprint(root: CausalDelayedRootFilter):
    token = root.publication_token()
    return (
        token.revision, token.digest, token.state.vector.tobytes(),
        token.state.covariance.tobytes(), root.health_snapshot(),
        root.future_imu_count, root.future_uwb_count,
        root.preavailability_output_count, root.late_imu_rejected,
        tuple(root._emission_times),
    )


def test_position_velocity_prepare_is_inert_and_commit_preserves_bias_and_covariance():
    root = _root()
    position_plan = _position_plan(root)
    before = _fingerprint(root)
    before_bias = root.current_state.vector[6:9].tobytes()
    plan = root.prepare_position_velocity_transaction(
        position_plan,
        velocity_delta_mps=np.array([0.01, -0.005, 0.002]),
        maximum_velocity_step_mps=0.02,
        owner="C2_TEST_VELOCITY_ONLY",
    )
    assert _fingerprint(root) == before
    root.prevalidate_position_velocity_transaction(position_plan, plan)
    decision = root.commit_position_velocity_transaction(position_plan, plan)
    assert decision.accepted
    assert root.current_state.vector[6:9].tobytes() == before_bias
    assert root.current_state.covariance.tobytes() == position_plan.measurement_candidate.covariance.tobytes()


def test_position_velocity_plan_is_owner_bound_stale_and_replay_safe():
    root = _root()
    position_plan = _position_plan(root)
    plan = root.prepare_position_velocity_transaction(
        position_plan, velocity_delta_mps=np.zeros(3),
        maximum_velocity_step_mps=0.02, owner="C2_TEST",
    )
    other = _root()
    other_position_plan = _position_plan(other)
    for target, target_position_plan, candidate in (
        (other, other_position_plan, plan),
        (root, position_plan, replace(plan, digest="0" * 64)),
    ):
        before = _fingerprint(target)
        with pytest.raises(RuntimeError, match="STALE_FORGED_OR_FOREIGN"):
            target.prevalidate_position_velocity_transaction(
                target_position_plan, candidate,
            )
        assert _fingerprint(target) == before
    root.commit_position_velocity_transaction(position_plan, plan)
    committed = _fingerprint(root)
    with pytest.raises(RuntimeError, match="STALE_ROOT_POSITION_PLAN"):
        root.commit_position_velocity_transaction(position_plan, plan)
    assert _fingerprint(root) == committed


def test_committed_position_velocity_plan_has_exact_one_shot_atomic_rollback():
    root = _root()
    position_plan = _position_plan(root)
    before = _fingerprint(root)
    plan = root.prepare_position_velocity_transaction(
        position_plan, velocity_delta_mps=np.array([0.01, 0.0, 0.0]),
        maximum_velocity_step_mps=0.02, owner="C2_TEST",
    )
    root.commit_position_velocity_transaction(position_plan, plan)
    root.rollback_committed_position_velocity_transaction(plan)
    assert _fingerprint(root) == before
    with pytest.raises(RuntimeError, match="STALE_FORGED_OR_FOREIGN"):
        root.commit_position_velocity_transaction(position_plan, plan)


def test_position_velocity_delta_bound_rejects_without_mutation():
    root = _root()
    position_plan = _position_plan(root)
    before = _fingerprint(root)
    with pytest.raises(ValueError, match="exceeds"):
        root.prepare_position_velocity_transaction(
            position_plan, velocity_delta_mps=np.array([0.03, 0.0, 0.0]),
            maximum_velocity_step_mps=0.02, owner="C2_TEST",
        )
    assert _fingerprint(root) == before


def test_hidden_emission_after_prepare_makes_plan_stale_without_clobbering_it():
    root = _root()
    position_plan = _position_plan(root)
    plan = root.prepare_position_velocity_transaction(
        position_plan, velocity_delta_mps=np.zeros(3),
        maximum_velocity_step_mps=0.02, owner="C2_TEST",
    )
    root.emit(0.0)
    after_emit = _fingerprint(root)
    with pytest.raises(RuntimeError, match="STALE_FORGED_OR_FOREIGN"):
        root.commit_position_velocity_transaction(position_plan, plan)
    assert _fingerprint(root) == after_emit
