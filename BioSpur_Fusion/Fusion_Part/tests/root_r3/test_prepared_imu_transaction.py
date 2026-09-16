from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.root_r3.estimator import (
    CausalDelayedRootFilter,
    RootTranslationEdgeMode,
    _root_imu_bundle_digest,
)
from biospur_fusion.root_r3.models import ImuSample, RootState


def _root():
    return CausalDelayedRootFilter(RootState(0.0, np.zeros(9), np.eye(9)))


def _sample(*, valid=True):
    return ImuSample(0.005, 0.006, np.array([0.1, 0.0, 9.80665]), np.eye(3), 7, valid, False)


def _owner(root):
    token=root.publication_token()
    return (token.revision,token.digest,token.state.vector.tobytes(),token.state.covariance.tobytes(),
            root.mode,root.future_imu_count,root.late_imu_rejected)


def _full_owner(root):
    return _root_imu_bundle_digest(root._prepare_position_rollback())


def test_prepared_accept_is_inert_until_commit_and_exact_legacy_equivalent():
    prepared=_root(); legacy=_root(); before=_full_owner(prepared)
    plan=prepared.prepare_imu_transaction(_sample(),edge_mode=RootTranslationEdgeMode.INERTIAL,
                                          following_input_mode=RootTranslationEdgeMode.INERTIAL)
    assert _full_owner(prepared)==before and plan.accepted
    prepared.prevalidate_prepared_imu(plan)
    assert prepared.commit_prepared_imu(plan)
    assert legacy.add_imu(_sample(),edge_mode=RootTranslationEdgeMode.INERTIAL,
                          following_input_mode=RootTranslationEdgeMode.INERTIAL)
    assert _full_owner(prepared)==_full_owner(legacy)


def test_rejected_prepare_is_noop_and_commit_matches_legacy_side_effects():
    prepared=_root(); legacy=_root(); before=_full_owner(prepared)
    plan=prepared.prepare_imu_transaction(_sample(valid=False))
    assert not plan.accepted and _full_owner(prepared)==before
    assert not prepared.commit_prepared_imu(plan)
    assert not legacy.add_imu(_sample(valid=False))
    assert _full_owner(prepared)==_full_owner(legacy)


def test_stale_replay_forged_and_foreign_plans_reject_without_mutation():
    first=_root(); second=_root(); plan=first.prepare_imu_transaction(_sample())
    before=_full_owner(first); second_before=_full_owner(second)
    with pytest.raises(RuntimeError,match="STALE_FORGED_OR_FOREIGN"):
        first.prevalidate_prepared_imu(replace(plan,digest="0"*64))
    with pytest.raises(RuntimeError,match="STALE_FORGED_OR_FOREIGN"):
        second.prevalidate_prepared_imu(plan)
    assert _full_owner(first)==before and _full_owner(second)==second_before
    assert first.commit_prepared_imu(plan)
    committed=_full_owner(first)
    with pytest.raises(RuntimeError,match="STALE_FORGED_OR_FOREIGN"):
        first.commit_prepared_imu(plan)
    assert _full_owner(first)==committed


def test_two_parties_prepare_before_either_commits():
    a,b=_root(),_root(); pa=a.prepare_imu_transaction(_sample()); pb=b.prepare_imu_transaction(_sample())
    a.prevalidate_prepared_imu(pa); b.prevalidate_prepared_imu(pb)
    assert a.commit_prepared_imu(pa) and b.commit_prepared_imu(pb)
    assert _owner(a)==_owner(b)


def test_hidden_emission_after_prepare_rejects_without_clobbering_owner():
    root = _root()
    plan = root.prepare_imu_transaction(_sample())
    root.emit(0.0)
    after_emit = _full_owner(root)
    with pytest.raises(RuntimeError, match="STALE_FORGED_OR_FOREIGN"):
        root.commit_prepared_imu(plan)
    assert _full_owner(root) == after_emit


def test_committed_imu_plan_rolls_back_exactly_and_remains_consumed():
    root = _root()
    before = _full_owner(root)
    plan = root.prepare_imu_transaction(_sample())
    assert root.commit_prepared_imu(plan)
    root.rollback_committed_prepared_imu(plan)
    assert _full_owner(root) == before
    with pytest.raises(RuntimeError, match="STALE_FORGED_OR_FOREIGN"):
        root.commit_prepared_imu(plan)


def test_two_root_commit_failure_rolls_back_both_then_continues_on_later_imu():
    a, b = _root(), _root()
    a_before, b_before = _full_owner(a), _full_owner(b)
    first_a = a.prepare_imu_transaction(_sample())
    first_b = b.prepare_imu_transaction(_sample())
    a.prevalidate_prepared_imu(first_a)
    b.prevalidate_prepared_imu(first_b)
    assert a.commit_prepared_imu(first_a)
    try:
        assert b.commit_prepared_imu(first_b)
        raise RuntimeError("injected post-B-commit coordinator failure")
    except RuntimeError as error:
        assert "injected post-B" in str(error)
        b.rollback_committed_prepared_imu(first_b)
        a.rollback_committed_prepared_imu(first_a)
    assert _full_owner(a) == a_before
    assert _full_owner(b) == b_before

    later = ImuSample(
        0.010, 0.011, np.array([0.1, 0.0, 9.80665]), np.eye(3), 8,
        True, False,
    )
    later_a = a.prepare_imu_transaction(later)
    later_b = b.prepare_imu_transaction(later)
    a.prevalidate_prepared_imu(later_a)
    b.prevalidate_prepared_imu(later_b)
    assert a.commit_prepared_imu(later_a)
    assert b.commit_prepared_imu(later_b)
    assert _full_owner(a) == _full_owner(b)
