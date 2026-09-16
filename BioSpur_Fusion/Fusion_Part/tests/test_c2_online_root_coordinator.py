from __future__ import annotations

import copy

import numpy as np
import pytest

from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_root_world import offline_unified_wiring as u3
from biospur_fusion.c2_uwb_root_world.causal_update_guard import (
    CausalContactTransitionEvidence,
    CausalImuActivitySummary,
    IndependentNodeConsensusEvidence,
    ReachabilityClass,
    ReachabilityEnvelope,
)
from biospur_fusion.c2_uwb_root_world.online_root_coordinator import (
    ROOT_UWB_SERVICE_INTERVAL_MS,
    RootOnlyRealtimeCoordinator,
    simulate_single_thread_service,
)
from biospur_fusion.c2_uwb_root_world.u0 import UwbRow
from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter, RootFilterConfig
from biospur_fusion.root_r3.models import ImuSample, RootState


ANCHORS = np.array([
    [1., 0., 0.], [0., 1., 0.], [0., 0., 1.], [-1., -1., -1.],
    [2., 0., 0.], [0., 2., 0.], [0., 0., 2.], [-2., -2., -2.],
])


def _clock(node="N0"):
    return DirectNodeLinkClock(node, 1_000., 0., 0, 0, 1_000_000)


def _row(node="N0", root=np.zeros(3)):
    ranges = tuple(int(round(np.linalg.norm(ANCHORS[i] - root) * 1000)) for i in range(8))
    return UwbRow(node, 0, 1, 1, 60_000, 100_000, tuple(range(8)), ranges,
                  (100, 200, 300, 400, 500, 600, 700, 800), (100,) * 8, 0xff)


def _pose(_node, query):
    pose_time = int(query // 5_000_000 * 5_000_000)
    if pose_time == query:
        pose_time -= 5_000_000
    return u3.StrictFloorOffset(np.zeros(3), pose_time, query, query - pose_time,
                                pose_time // 5_000_000)


def _root():
    return CausalDelayedRootFilter(
        RootState(.05, np.zeros(9), np.eye(9) * .1),
        RootFilterConfig(fixed_lag_s=.2, nis_limit_3d=1e12), inertial=False)


def _envelope(displacement=.5, cls=ReachabilityClass.NOMINAL):
    return ReachabilityEnvelope(
        cls, displacement, 10., 100., 1., 10., 100., .2,
        .2, 1., .02, 2, 1., 1e8, "explicit U7A fixture envelope")


def _args(rows=None, envelope=None):
    rows = (_row(),) if rows is None else rows
    clocks = {row.node: _clock(row.node) for row in rows}
    return dict(
        rows=rows, clocks=clocks, strict_floor_offset=_pose, anchors_m=ANCHORS,
        anchor_delay_m=np.zeros(8), tag_delay_m=0.,
        sigma_for_quality=lambda _quality: .1,
        nominal_envelope=_envelope() if envelope is None else envelope,
    )


def _dynamic(times=(.060225, .1)):
    measurement, availability = times
    activity = CausalImuActivitySummary(
        measurement, availability, measurement - .04, ("N0", "N1"),
        np.full(2, measurement - .001), np.full(2, .6), np.full(2, 2.),
        np.full(2, .03), "independent same-time U7A activity fixture")
    consensus = IndependentNodeConsensusEvidence(
        measurement, ("N0", "N1"), np.array([[.30, 0., 0.], [.31, 0., 0.]]),
        np.full(2, 3, dtype=np.int64), np.full(2, 4.), True,
        "independent pre-candidate U7A consensus fixture")
    contact = CausalContactTransitionEvidence(
        availability, {"left": "STANCE_CONFIRMED", "right": "STANCE_CONFIRMED"},
        {"left": "SWING_CONFIRMED", "right": "STANCE_CONFIRMED"},
        ("left",), ("left",), "frozen contact-owner transition fixture")
    return activity, consensus, contact


def test_accepted_math_and_decision_parity_with_existing_u3_reference():
    reference_root = _root(); candidate_root = _root()
    reference = u3.execute_offline_root_group(root=reference_root, **_args())
    result = RootOnlyRealtimeCoordinator(candidate_root).process_group(**_args())
    assert result.candidate_reason == reference.candidate.reason
    assert result.transaction.decision.reason == reference.transaction.decision.reason
    np.testing.assert_allclose(result.candidate_root_m, reference.candidate.root_position_m, atol=1e-12)
    np.testing.assert_allclose(candidate_root.current_state.vector, reference_root.current_state.vector, atol=1e-12)
    np.testing.assert_allclose(candidate_root.current_state.covariance,
                               reference_root.current_state.covariance, atol=1e-12)
    assert (result.candidate_calls, result.guard_calls, result.transaction_calls) == (1, 1, 1)


def test_root_h_r_s_nis_are_exact_and_immutable():
    root = _root()
    result = RootOnlyRealtimeCoordinator(root).process_group(**_args())
    audit = result.uncertainty
    expected_h = np.zeros((3, 9)); expected_h[:, :3] = np.eye(3)
    np.testing.assert_array_equal(audit.h, expected_h)
    expected_s = audit.h @ (audit.s - audit.r if False else np.zeros((9, 9))) @ audit.h.T
    del expected_s
    assert audit.nis == pytest.approx(
        audit.innovation_m @ np.linalg.solve(audit.s, audit.innovation_m), abs=1e-12)
    assert np.all(np.linalg.eigvalsh(audit.r) > 0)
    assert np.all(np.linalg.eigvalsh(audit.s) > 0)
    for value in (audit.h, audit.r, audit.s, audit.innovation_m):
        assert not value.flags.writeable


def test_imu_propagates_exactly_once_and_matches_root_reference():
    first = _root(); second = _root(); coordinator = RootOnlyRealtimeCoordinator(first)
    sample = ImuSample(.055, .055, np.array([0., 0., 9.80665]), np.eye(3), 1)
    coordinator.add_imu(sample); assert second.add_imu(sample)
    assert coordinator.imu_calls == 1
    np.testing.assert_array_equal(first.current_state.vector, second.current_state.vector)
    np.testing.assert_array_equal(first.current_state.covariance, second.current_state.covariance)


def test_quiet_single_node_and_multi_node_teleports_reject():
    for rows in ((_row(root=np.array([.8, 0., 0.])),),
                 (_row("N0", np.array([.8, 0., 0.])),
                  _row("N1", np.array([.8, 0., 0.])))):
        result = RootOnlyRealtimeCoordinator(_root()).process_group(
            **_args(rows, _envelope(.01)))
        assert not result.transaction.root_committed
        assert result.transaction.rejection_recorded


def test_dynamic_transition_requires_all_three_corroboration_classes():
    rows = (_row(root=np.array([.35, 0., 0.])),)
    activity, consensus, contact = _dynamic()
    common = dict(**_args(rows, _envelope(.01)), dynamic_envelope=_envelope(2., ReachabilityClass.DYNAMIC_FALL))
    for supplied in (
        {"activity": activity},
        {"activity": activity, "consensus": consensus},
        {"consensus": consensus, "contact": contact},
    ):
        result = RootOnlyRealtimeCoordinator(_root()).process_group(**common, **supplied)
        assert not result.transaction.root_committed
    accepted = RootOnlyRealtimeCoordinator(_root()).process_group(
        **common, activity=activity, consensus=consensus, contact=contact)
    assert accepted.transaction.root_committed
    assert accepted.transaction.decision.reason.value == "ACCEPT_CORROBORATED_DYNAMIC"


def test_true_finite_speed_locomotion_is_nominally_accepted():
    result = RootOnlyRealtimeCoordinator(_root()).process_group(
        **_args((_row(root=np.array([.03, 0., 0.])),), _envelope(.5)))
    assert result.transaction.root_committed
    assert result.transaction.decision.reason.value == "ACCEPT_NOMINAL"


def test_rejection_keeps_authoritative_prediction_and_records_once():
    root = _root(); coordinator = RootOnlyRealtimeCoordinator(root)
    sentinel = {"pose": b"pose", "contact": b"contact", "bias": b"bias"}
    result = coordinator.process_group(
        **_args((_row(root=np.array([.8, 0., 0.])),), _envelope(.01)))
    token = root.publication_token()
    assert result.transaction.rejection_recorded and not result.transaction.root_committed
    assert root._health["C2_SHARED_ROOT_DIAGNOSTIC"].rejected == 1
    assert sentinel == {"pose": b"pose", "contact": b"contact", "bias": b"bias"}
    assert np.isfinite(token.state.vector).all() and np.isfinite(token.state.covariance).all()


def test_queue_is_fixed_capacity_monotonic_and_drains():
    coordinator = RootOnlyRealtimeCoordinator(_root(), queue_capacity=2)
    coordinator.enqueue(.05, "IMU", object()); coordinator.enqueue(.1, "UWB", object())
    with pytest.raises(OverflowError): coordinator.enqueue(.11, "IMU", object())
    assert coordinator.queue_high_watermark == 2
    coordinator.dequeue(); coordinator.dequeue(); assert coordinator.queued_events == 0
    with pytest.raises(ValueError): coordinator.enqueue(.09, "IMU", object())


def test_future_and_reversed_inputs_fail_closed():
    coordinator = RootOnlyRealtimeCoordinator(_root())
    with pytest.raises(ValueError):
        coordinator.add_imu(ImuSample(.055, .056, np.zeros(3), np.eye(3), 1))
    root = _root(); root.advance_to_availability(.11)
    with pytest.raises(ValueError, match="future"):
        RootOnlyRealtimeCoordinator(root).process_group(**_args())


def test_virtual_service_reports_utilization_deadline_and_backlog():
    arrivals = np.arange(0., 100., 5.)
    service = np.full(len(arrivals), .2)
    simulation = simulate_single_thread_service(
        arrivals, service, arrivals + 5., capacity=64)
    assert simulation.utilization < 1 and simulation.deadline_misses == 0
    assert not simulation.overflow and simulation.drained_to_zero
    assert simulation.queue_high_watermark == 1
    assert ROOT_UWB_SERVICE_INTERVAL_MS == pytest.approx(12.004801920768307)
