from __future__ import annotations

import numpy as np

from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import (
    DirectShadowEvidence,
)
from biospur_fusion.c2_uwb_root_world.action00_shadow_trace import (
    C2_BODY_NODES,
    NodeSweepInput,
    evaluate_body_epoch,
    group_rows_by_pelvis_epoch,
)
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow
from biospur_fusion.root_r3.models import RootState


ANCHORS = np.array([
    [0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [4.0, 4.0, 0.0], [0.0, 4.0, 0.0],
    [0.0, 0.0, 2.5], [4.0, 0.0, 2.5], [4.0, 4.0, 2.5], [0.0, 4.0, 2.5],
])
CLOCK = ClockModel(0, 1000.0, 0.0, 10.0)


def _evidence(node: str, weight: float = 1.0):
    return tuple(
        DirectShadowEvidence(
            node, 1.0, 0.0, 0.0, weight, 1.0, weight, (), (), (),
        )
        for _ in range(8)
    )


def _row(node: str, strobe_us: int, root: np.ndarray, offset: np.ndarray,
         *, corrupt: bool = False) -> UwbRow:
    ranges = np.linalg.norm(ANCHORS - (root + offset), axis=1)
    if corrupt:
        ranges = ranges + np.array([1.5, -0.7, 0.9, -1.0, 1.2, -0.8, 0.6, -0.9])
        ranges = np.maximum(ranges, 0.1)
    return UwbRow(
        node=node, boot=0, sequence=1, sweep=1, strobe_us=strobe_us,
        frame_us=strobe_us + 100, anchor_ids=tuple(range(8)),
        ranges_mm=tuple(int(round(value * 1000.0)) for value in ranges),
        t_round_us=(0,) * 8, quality=(100,) * 8, valid_mask=0xFF,
        identity=1, node_ms=1,
    )


def _state(time_s: float, root: np.ndarray) -> RootState:
    return RootState(time_s, np.r_[root, np.zeros(6)], np.eye(9) * 0.1)


def test_groups_asynchronous_nodes_by_consecutive_pelvis_sweeps() -> None:
    rows = []
    root = np.array([2.0, 2.0, 1.0])
    for base in (1_000_000, 1_120_000, 1_240_000):
        rows.append(_row("BSFC2CC", base, root, np.zeros(3)))
        rows.append(_row("BSF31CC", base + 50_000, root, np.array([0.0, 0.0, 0.5])))
    groups = group_rows_by_pelvis_epoch(rows, {node: CLOCK for node in C2_BODY_NODES})
    assert len(groups) == 2
    assert [row.node for row in groups[0]] == ["BSF31CC", "BSFC2CC"]
    assert all(len({row.node for row in group}) == len(group) for group in groups)


def test_exact_two_node_epoch_is_direct_and_input_state_is_unchanged() -> None:
    root = np.array([2.0, 2.0, 1.0])
    predicted = _state(1.05, root + np.array([0.03, -0.02, 0.01]))
    before_vector = predicted.vector.copy()
    before_covariance = predicted.covariance.copy()
    sweeps = []
    for node, offset, strobe in (
        ("BSFC2CC", np.zeros(3), 1_050_000),
        ("BSF31CC", np.array([0.0, 0.0, 0.5]), 1_050_000),
    ):
        sweeps.append(NodeSweepInput(
            _row(node, strobe, root, offset), CLOCK, offset, np.zeros(3),
            _evidence(node), 1_049_000_000, 1_000_000.0,
        ))
    fact = evaluate_body_epoch(
        epoch_sequence=7, imu_prediction=predicted, sweeps=sweeps,
        anchors_m=ANCHORS,
    )
    assert fact.direct_nodes == ("BSF31CC", "BSFC2CC")
    assert len(fact.propagated_nodes) == 8
    assert fact.shared_result is not None and fact.shared_result.success
    np.testing.assert_allclose(fact.shared_result.root_position_m, root, atol=8e-4)
    np.testing.assert_array_equal(predicted.vector, before_vector)
    np.testing.assert_array_equal(predicted.covariance, before_covariance)
    assert all(len(node.links) == 8 for node in fact.node_facts)


def test_bad_node_is_excluded_and_reported_as_propagated() -> None:
    root = np.array([2.0, 2.0, 1.0])
    good = "BSFC2CC"
    bad = "BSF31CC"
    sweeps = (
        NodeSweepInput(
            _row(good, 1_000_000, root, np.zeros(3)), CLOCK,
            np.zeros(3), np.zeros(3), _evidence(good),
            999_000_000, 1_000_000.0,
        ),
        NodeSweepInput(
            _row(bad, 1_000_000, root, np.array([0.0, 0.0, 0.5]), corrupt=True),
            CLOCK, np.array([0.0, 0.0, 0.5]), np.zeros(3), _evidence(bad),
            999_000_000, 1_000_000.0,
        ),
    )
    fact = evaluate_body_epoch(
        epoch_sequence=0, imu_prediction=_state(1.0, root), sweeps=sweeps,
        anchors_m=ANCHORS,
    )
    assert good in fact.direct_nodes
    assert bad in fact.propagated_nodes
    bad_fact = next(item for item in fact.node_facts if item.node == bad)
    assert not bad_fact.direct_usable
    assert bad_fact.direct_reason == "NODE_RESIDUAL_RMS_REJECT"
    assert fact.shared_result is not None and fact.shared_result.success


def test_external_world_seed_decouples_range_solve_from_drifted_imu() -> None:
    root = np.array([2.0, 2.0, 1.0])
    drifted = _state(1.0, np.array([25.0, -18.0, 9.0]))
    sweep = NodeSweepInput(
        _row("BSFC2CC", 1_000_000, root, np.zeros(3)), CLOCK,
        np.zeros(3), np.zeros(3), _evidence("BSFC2CC"),
        999_000_000, 1_000_000.0,
    )

    fact = evaluate_body_epoch(
        epoch_sequence=0,
        imu_prediction=drifted,
        sweeps=(sweep,),
        anchors_m=ANCHORS,
        solver_seed_root_m=root + np.array([0.1, -0.1, 0.05]),
        solver_seed_velocity_mps=np.zeros(3),
        root_bounds_m=(ANCHORS.min(axis=0) - 0.75, ANCHORS.max(axis=0) + 0.75),
    )

    assert fact.direct_nodes == ("BSFC2CC",)
    np.testing.assert_allclose(fact.shared_result.root_position_m, root, atol=8e-4)
