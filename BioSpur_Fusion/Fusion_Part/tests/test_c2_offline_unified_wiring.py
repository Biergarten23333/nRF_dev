from __future__ import annotations

from dataclasses import replace
import copy

import numpy as np
import pytest

from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_root_world import offline_unified_wiring as u3
from biospur_fusion.c2_uwb_root_world.causal_update_guard import (
    ReachabilityClass,
    ReachabilityEnvelope,
)
from biospur_fusion.c2_uwb_root_world.u0 import UwbRow
from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter, RootFilterConfig
from biospur_fusion.root_r3.models import RootState


ANCHORS = np.array([
    [1., 0., 0.], [0., 1., 0.], [0., 0., 1.], [-1., -1., -1.],
    [2., 0., 0.], [0., 2., 0.], [0., 0., 2.], [-2., -2., -2.],
])


def _clock(node="N0"):
    return DirectNodeLinkClock(node, 1_000., 0., 0, 0, 1_000_000)


def _row(node="N0", strobe=60_000, frame=100_000, root=np.zeros(3)):
    ranges = tuple(int(round(np.linalg.norm(ANCHORS[i] - root) * 1000)) for i in range(8))
    return UwbRow(node, 0, 1, 1, strobe, frame, tuple(range(8)), ranges,
                  (100, 200, 300, 400, 500, 600, 700, 800), (100,) * 8, 0xff)


def _pose(_node, query):
    pose_time = int(query // 5_000_000 * 5_000_000)
    if pose_time == query:
        pose_time -= 5_000_000
    return u3.StrictFloorOffset(np.zeros(3), pose_time, query, query - pose_time, pose_time // 5_000_000)


def _root():
    return CausalDelayedRootFilter(
        RootState(.05, np.zeros(9), np.eye(9) * .1),
        RootFilterConfig(fixed_lag_s=.2, nis_limit_3d=1e9), inertial=False)


def _envelope(displacement=10.):
    return ReachabilityEnvelope(
        ReachabilityClass.NOMINAL, displacement, 10., 100., 1., 10., 100.,
        1., 1., 1., .01, 2, 10., 1e8, "synthetic explicit fixture envelope")


def _run(row=None, root=None):
    return u3.execute_offline_root_group(
        root=_root() if root is None else root,
        rows=(_row() if row is None else row,), clocks={"N0": _clock()},
        strict_floor_offset=_pose, anchors_m=ANCHORS,
        anchor_delay_m=np.zeros(8), tag_delay_m=0.,
        sigma_for_quality=lambda _quality: .1, nominal_envelope=_envelope())


def test_offline_boundary_is_irrevocably_non_scientific_and_online_blocked():
    boundary = u3.OfflineU3Boundary()
    assert boundary.execution_class == "OFFLINE_ONLY"
    assert boundary.online_status == "ONLINE_BLOCKED"
    assert not boundary.calibrated_R and not boundary.scientific_pass and not boundary.production_ready
    assert boundary.u2_qualification_seal_sha256.startswith("772483e")
    with pytest.raises(RuntimeError, match="OFFLINE_ONLY"):
        u3.require_offline_mode(offline=False)
    with pytest.raises(RuntimeError, match="OFFLINE_ONLY"):
        u3.require_offline_mode(offline=True, production=True)


def test_exact_per_link_half_round_epochs_and_frame_availability():
    row = _row()
    epochs, measurement, availability = u3.group_epoch_times_ns((row,), clocks={"N0": _clock()})
    expected = 1_000. * (60_000 + .5 * np.arange(100, 900, 100))
    assert np.array_equal(epochs, expected)
    assert measurement == np.median(expected)
    assert availability == 100_000_000.


def test_strict_floor_offsets_are_past_and_age_bounded():
    result = _run()
    assert result.link_audit
    assert all(0 < row.pose_age_ns <= 5_005_000 for row in result.link_audit)
    assert all(row.pose_time_ns < row.link_time_ns for row in result.link_audit)
    with pytest.raises(ValueError, match="stale"):
        u3.StrictFloorOffset(np.zeros(3), 0, 6_000_000., 6_000_000., 0)


def test_one_candidate_solve_and_one_transaction_per_group(monkeypatch):
    solves = transactions = 0
    real_solve = u3.solve_shared_root
    real_transaction = u3.execute_causal_update_transaction
    def solve(*args, **kwargs):
        nonlocal solves
        solves += 1
        return real_solve(*args, **kwargs)
    def transaction(*args, **kwargs):
        nonlocal transactions
        transactions += 1
        return real_transaction(*args, **kwargs)
    monkeypatch.setattr(u3, "solve_shared_root", solve)
    monkeypatch.setattr(u3, "execute_causal_update_transaction", transaction)
    result = _run()
    assert result.candidate.success and result.candidate.rank == 3
    assert solves == transactions == 1
    assert result.candidate_solver_calls == 1
    assert result.transaction_calls == 1
    assert result.boundary.covariance_owner == "DIAGNOSTIC_NODE_COUNT_FLOOR_ONLY"


def test_x_over_ten_trust_provenance_and_diagnostic_covariance():
    result = _run()
    assert result.selection.mode == "SINGLE_NODE_ROOT_TRANSLATION"
    assert result.selection.trusted_nodes == ("N0",)
    assert result.selection.assessments[0].reason == "TRUSTED"
    assert result.covariance_minimum_std_m == pytest.approx(.12 * np.sqrt(10))
    assert np.array_equal(
        result.covariance_m2,
        np.eye(3) * result.covariance_minimum_std_m ** 2,
    )
    assert result.boundary.covariance_owner == "DIAGNOSTIC_NODE_COUNT_FLOOR_ONLY"


def test_partition_and_row_order_determinism():
    row0 = _row("N0")
    row1 = _row("N1")
    clocks = {"N0": _clock("N0"), "N1": _clock("N1")}
    args = dict(clocks=clocks, strict_floor_offset=_pose, anchor_delay_m=np.zeros(8),
                tag_delay_m=0., sigma_for_quality=lambda _: .1)
    first = u3.build_causal_links((row0, row1), **args)
    second = u3.build_causal_links((row1, row0), **args)
    assert [(x.node, x.anchor, x.link_dt_s) for x in first[0]] == [
        (x.node, x.anchor, x.link_dt_s) for x in second[0]]


def test_invalid_missing_duplicate_and_noncanonical_groups_fail_closed():
    with pytest.raises(ValueError, match="empty"):
        u3.group_epoch_times_ns((), clocks={})
    with pytest.raises(ValueError, match="duplicate node"):
        u3.group_epoch_times_ns((_row(), _row()), clocks={"N0": _clock()})
    bad = replace(_row(), anchor_ids=(1, 0, 2, 3, 4, 5, 6, 7))
    with pytest.raises(ValueError, match="noncanonical"):
        u3.group_epoch_times_ns((bad,), clocks={"N0": _clock()})


def test_raw_row_is_immutable_by_execution():
    row = _row()
    before = copy.deepcopy(row)
    _run(row)
    assert row == before


def test_rejected_transaction_retains_imu_prediction_owner_bytes():
    root = _root()
    row = _row(root=np.array([.1, 0., 0.]))
    before = root.publication_token()
    result = u3.execute_offline_root_group(
        root=root, rows=(row,), clocks={"N0": _clock()}, strict_floor_offset=_pose,
        anchors_m=ANCHORS, anchor_delay_m=np.zeros(8), tag_delay_m=0.,
        sigma_for_quality=lambda _: .1, nominal_envelope=_envelope(1e-6))
    after = root.publication_token()
    assert result.transaction.rejection_recorded and not result.transaction.root_committed
    assert before.state.vector.tobytes() == after.state.vector.tobytes()
    assert before.state.covariance.tobytes() == after.state.covariance.tobytes()


def test_epoch_cadence_is_exact_8_33_hz_and_gap_fails():
    u3.validate_epoch_cadence([1_000_000_000., 1_120_000_000., 1_240_000_000.])
    with pytest.raises(ValueError, match="8.33"):
        u3.validate_epoch_cadence([1_000_000_000., 1_240_000_000.])


def test_current_range_changes_candidate_not_pose_owner():
    seen = []
    def owner(node, query):
        seen.append((node, query))
        return _pose(node, query)
    common = dict(clocks={"N0": _clock()}, strict_floor_offset=owner,
                  anchor_delay_m=np.zeros(8), tag_delay_m=0., sigma_for_quality=lambda _: .1)
    first = u3.build_causal_links((_row(),), **common)
    calls = tuple(seen); seen.clear()
    changed = replace(_row(), ranges_mm=tuple(x + 50 for x in _row().ranges_mm))
    second = u3.build_causal_links((changed,), **common)
    assert tuple(seen) == calls
    assert [(x.link_time_ns, x.pose_time_ns) for x in first[1]] == [
        (x.link_time_ns, x.pose_time_ns) for x in second[1]]
    assert [x.range_m for x in first[0]] != [x.range_m for x in second[0]]


def test_bad_node_ranges_are_isolated_before_adaptive_selection():
    good = _row("N0")
    bad = replace(
        _row("N1"), valid_mask=0x0F,
        ranges_mm=(1, 1, 1, 1, *_row("N1").ranges_mm[4:]),
    )
    links, audits, _measurement, _availability = u3.build_causal_links(
        (good, bad), clocks={"N0": _clock("N0"), "N1": _clock("N1")},
        strict_floor_offset=_pose, anchor_delay_m=np.full(8, 0.01),
        tag_delay_m=0.01, sigma_for_quality=lambda _: .1,
    )
    assert {link.node for link in links} == {"N0"}
    assert {row.node for row in audits} == {"N0"}
