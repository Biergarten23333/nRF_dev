from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.root_r3 import (
    CausalDelayedRootFilter,
    ImuSample,
    RootFilterConfig,
    RootState,
    PositionObservation,
)
from biospur_fusion.root_r3.estimator import _root_imu_bundle_digest


def _root() -> CausalDelayedRootFilter:
    vector=np.array([.2,-.1,.4,.3,-.2,.1,.01,-.02,.03])
    covariance=np.eye(9)*.2
    covariance[0,3]=covariance[3,0]=.04
    return CausalDelayedRootFilter(
        RootState(0.,vector,covariance),RootFilterConfig(),inertial=True)


def _imu_plan(root: CausalDelayedRootFilter):
    return root.prepare_imu_transaction(ImuSample(
        .005,.006,np.array([0.,0.,9.80665]),np.eye(3),1))


def _owner_digest(root: CausalDelayedRootFilter) -> str:
    return _root_imu_bundle_digest(root._prepare_position_rollback())


def _position_plan(root: CausalDelayedRootFilter):
    observation = PositionObservation(
        0.0, 0.0, np.array([.205, -.102, .401]), np.eye(3) * .04,
        "shared-root", (0, 1, 2, 3), "LOS", True, True, 1,
    )
    plan = root.prepare_position(
        observation, processing_time_s=0.0,
        state_update_indices=(0, 1, 2),
    )
    assert plan.decision.accepted
    return plan


def _compound(root: CausalDelayedRootFilter, imu_plan=None):
    source=_imu_plan(root) if imu_plan is None else imu_plan
    plan=root.prepare_imu_velocity_transaction(
        source,velocity_delta_mps=np.array([.02,-.01,.005]),
        maximum_velocity_step_mps=.05,owner="C2_CONSENSUS_DRIFT")
    return source,plan


def test_prepare_is_inert_and_candidate_changes_only_imu_velocity():
    root=_root(); imu,compound=_compound(root); before=_owner_digest(root)
    assert _owner_digest(root)==before
    candidate=compound._candidate_bundle.snapshots[-1].state
    assert candidate.vector[:3].tobytes()==imu.candidate_state.vector[:3].tobytes()
    assert candidate.vector[6:9].tobytes()==imu.candidate_state.vector[6:9].tobytes()
    assert candidate.covariance.tobytes()==imu.candidate_state.covariance.tobytes()
    expected=imu.candidate_state.vector[3:6].copy()
    expected+=compound.velocity_delta_mps
    assert candidate.vector[3:6].tobytes()==expected.tobytes()


def test_detached_prepare_exception_leaves_complete_live_owner_inert(monkeypatch):
    root=_root(); imu=_imu_plan(root); before=_owner_digest(root)

    def fail_on_detached(instance,*args,**kwargs):
        assert instance is not root
        raise RuntimeError("injected detached constraint preparation failure")

    monkeypatch.setattr(
        CausalDelayedRootFilter,"prepare_current_constraint",fail_on_detached)
    with pytest.raises(RuntimeError,match="injected detached"):
        root.prepare_imu_velocity_transaction(
            imu,velocity_delta_mps=np.array([.02,-.01,.005]),
            maximum_velocity_step_mps=.05,owner="C2_CONSENSUS_DRIFT")
    assert _owner_digest(root)==before


def test_compound_commit_consumes_itself_and_underlying_imu_plan():
    root=_root(); imu,compound=_compound(root)
    assert root.commit_imu_velocity_transaction(imu,compound)
    committed=_owner_digest(root)
    for operation in (
        lambda: root.commit_imu_velocity_transaction(imu,compound),
        lambda: root.commit_prepared_imu(imu),
    ):
        with pytest.raises(RuntimeError): operation()
        assert _owner_digest(root)==committed


def test_imu_commit_first_makes_compound_stale():
    root=_root(); imu,compound=_compound(root)
    assert root.commit_prepared_imu(imu)
    committed=_owner_digest(root)
    with pytest.raises(RuntimeError):
        root.commit_imu_velocity_transaction(imu,compound)
    assert _owner_digest(root)==committed


@pytest.mark.parametrize("mutation",[
    lambda plan: replace(plan,digest="0"*64),
    lambda plan: replace(plan,owner="tampered"),
    lambda plan: replace(plan,maximum_velocity_step_mps=.001),
    lambda plan: replace(plan,velocity_delta_mps=np.zeros(3)),
])
def test_tampered_compound_plan_rejects_without_mutation(mutation):
    root=_root(); imu,compound=_compound(root); before=_owner_digest(root)
    with pytest.raises(RuntimeError,match="STALE_FORGED_OR_FOREIGN"):
        root.commit_imu_velocity_transaction(imu,mutation(compound))
    assert _owner_digest(root)==before


def test_foreign_plan_and_detached_clone_are_isolated():
    root=_root(); other=_root(); imu,compound=_compound(root)
    before=_owner_digest(other)
    with pytest.raises(RuntimeError):
        other.commit_imu_velocity_transaction(_imu_plan(other),compound)
    assert _owner_digest(other)==before
    detached=deepcopy(root)
    detached_imu,detached_compound=_compound(detached)
    detached.commit_imu_velocity_transaction(detached_imu,detached_compound)
    assert _owner_digest(root)!=_owner_digest(detached)


def test_bound_and_rejected_imu_fail_before_mutation():
    root=_root(); imu=_imu_plan(root); before=_owner_digest(root)
    with pytest.raises(ValueError,match="exceeds"):
        root.prepare_imu_velocity_transaction(
            imu,velocity_delta_mps=np.array([.06,0.,0.]),
            maximum_velocity_step_mps=.05,owner="C2_CONSENSUS_DRIFT")
    rejected=root.prepare_imu_transaction(ImuSample(
        0.,0.,np.array([0.,0.,9.80665]),np.eye(3),2))
    assert not rejected.accepted
    with pytest.raises(ValueError,match="accepted IMU"):
        root.prepare_imu_velocity_transaction(
            rejected,velocity_delta_mps=np.zeros(3),
            maximum_velocity_step_mps=.05,owner="C2_CONSENSUS_DRIFT")
    assert _owner_digest(root)==before


def test_commit_exception_restores_base_but_both_plans_stay_consumed(monkeypatch):
    root=_root(); imu,compound=_compound(root); before=_owner_digest(root)
    original=CausalDelayedRootFilter._rollback_prevalidated_position
    calls=[]
    def fail_after_candidate(owner,bundle):
        original(owner,bundle)
        calls.append(1)
        if len(calls)==1:
            raise RuntimeError("injected after candidate assignment")
    monkeypatch.setattr(
        CausalDelayedRootFilter,"_rollback_prevalidated_position",fail_after_candidate)
    with pytest.raises(RuntimeError,match="injected"):
        root.commit_imu_velocity_transaction(imu,compound)
    assert _owner_digest(root)==before
    with pytest.raises(RuntimeError): root.commit_prepared_imu(imu)
    with pytest.raises(RuntimeError):
        root.commit_imu_velocity_transaction(imu,compound)


def test_successful_compound_rollback_restores_base_and_keeps_consumed():
    root=_root(); imu,compound=_compound(root); before=_owner_digest(root)
    root.commit_imu_velocity_transaction(imu,compound)
    root.rollback_committed_imu_velocity_transaction(compound)
    assert _owner_digest(root)==before
    with pytest.raises(RuntimeError): root.commit_prepared_imu(imu)
    with pytest.raises(RuntimeError):
        root.commit_imu_velocity_transaction(imu,compound)


def test_future_position_imu_velocity_prepare_is_inert_and_commits_in_order():
    root = _root(); position = _position_plan(root)
    before = _owner_digest(root)
    sample = ImuSample(
        .005, .006, np.array([0., 0., 9.80665]), np.eye(3), 2,
    )
    future = root.prepare_imu_after_position_transaction(
        position, sample=sample, frame_digest="f" * 64,
    )
    compound = root.prepare_imu_velocity_after_position_transaction(
        position, future, velocity_delta_mps=np.array([.02, 0., 0.]),
        maximum_velocity_step_mps=.05, owner="C2_CONSENSUS_DRIFT",
    )
    assert _owner_digest(root) == before
    root._apply_prevalidated_position(root._prevalidate_position_plan(position))
    assert root.commit_imu_velocity_after_position_transaction(
        position, future, compound,
    )
    expected = future.imu_plan.candidate_state.velocity_mps.copy()
    expected[0] += .02
    np.testing.assert_array_equal(root.current_state.velocity_mps, expected)


def test_future_prepare_exception_restores_root_and_position_remains_committable(monkeypatch):
    root = _root(); position = _position_plan(root); before = _owner_digest(root)
    sample = ImuSample(
        .005, .006, np.array([0., 0., 9.80665]), np.eye(3), 2,
    )
    original = CausalDelayedRootFilter.prepare_imu_transaction
    def fail(owner, *args, **kwargs):
        assert owner is root
        raise RuntimeError("injected future IMU failure")
    monkeypatch.setattr(CausalDelayedRootFilter, "prepare_imu_transaction", fail)
    with pytest.raises(RuntimeError, match="injected"):
        root.prepare_imu_after_position_transaction(
            position, sample=sample, frame_digest="f" * 64,
        )
    assert _owner_digest(root) == before
    monkeypatch.setattr(CausalDelayedRootFilter, "prepare_imu_transaction", original)
    root._apply_prevalidated_position(root._prevalidate_position_plan(position))
    assert root.publication_token().revision == position.base_revision + 1


def test_future_root_plan_rejects_wrong_position_frame_and_tamper():
    root = _root(); position = _position_plan(root)
    sample = ImuSample(
        .005, .006, np.array([0., 0., 9.80665]), np.eye(3), 2,
    )
    future = root.prepare_imu_after_position_transaction(
        position, sample=sample, frame_digest="f" * 64,
    )
    compound = root.prepare_imu_velocity_after_position_transaction(
        position, future, velocity_delta_mps=np.zeros(3),
        maximum_velocity_step_mps=.05, owner="C2_CONSENSUS_DRIFT",
    )
    root._apply_prevalidated_position(root._prevalidate_position_plan(position))
    for bad in (
        replace(compound, frame_digest="0" * 64),
        replace(compound, future_imu_digest="0" * 64),
        replace(compound, digest="0" * 64),
    ):
        with pytest.raises(RuntimeError):
            root.commit_imu_velocity_after_position_transaction(
                position, future, bad,
            )
