from __future__ import annotations

import numpy as np

from biospur_fusion.c2_uwb_calibration.shared_root import (
    SharedRangeLink,
    estimate_leave_node_uncertainty,
    evaluate_shared_root_residuals,
    solve_shared_root,
)


def _anchors() -> np.ndarray:
    return np.asarray(
        [
            [0.0, 0.0, 0.0], [4.0, 0.0, 0.0],
            [4.0, 3.0, 0.0], [0.0, 3.0, 0.0],
            [0.0, 0.0, 2.0], [4.0, 0.0, 2.0],
            [4.0, 3.0, 2.0], [0.0, 3.0, 2.0],
        ]
    )


def test_shared_root_recovers_one_root_from_multiple_tags() -> None:
    anchors = _anchors()
    expected = np.array([1.7, 1.2, 0.9])
    offsets = {
        "pelvis": np.array([0.0, 0.0, 0.0]),
        "left_wrist": np.array([-0.35, 0.1, 0.35]),
        "right_ankle": np.array([0.15, -0.05, -0.75]),
    }
    links = []
    for node, offset in offsets.items():
        for anchor in range(8):
            links.append(
                SharedRangeLink(
                    node=node,
                    anchor=anchor,
                    range_m=float(np.linalg.norm(anchors[anchor] - (expected + offset))),
                    tag_offset_world_m=offset,
                    link_dt_s=0.0,
                    sigma_m=0.1,
                )
            )
    result = solve_shared_root(
        links, anchors_m=anchors, initial_root_m=np.array([2.0, 1.5, 1.0])
    )
    assert result.success
    assert result.reason == "ACCEPTED"
    assert result.rank == 3
    assert len(result.nodes_used) == 3
    np.testing.assert_allclose(result.root_position_m, expected, atol=1e-11)
    np.testing.assert_allclose(result.residuals_m, 0.0, atol=1e-11)
    np.testing.assert_allclose(
        evaluate_shared_root_residuals(
            links, anchors_m=anchors, root_position_m=expected
        ),
        0.0,
        atol=1e-11,
    )


def test_shared_root_applies_causal_velocity_at_each_link_epoch() -> None:
    anchors = _anchors()
    expected = np.array([1.5, 1.0, 0.8])
    velocity = np.array([0.8, -0.2, 0.1])
    offset = np.array([0.1, 0.0, 0.2])
    links = []
    for anchor, dt in enumerate(np.linspace(-0.004, 0.004, 8)):
        tag = expected + offset + dt * velocity
        links.append(
            SharedRangeLink(
                node="node",
                anchor=anchor,
                range_m=float(np.linalg.norm(anchors[anchor] - tag)),
                tag_offset_world_m=offset,
                link_dt_s=float(dt),
                sigma_m=0.1,
            )
        )
    result = solve_shared_root(
        links,
        anchors_m=anchors,
        initial_root_m=np.array([2.0, 1.5, 1.0]),
        root_velocity_mps=velocity,
    )
    assert result.success
    np.testing.assert_allclose(result.root_position_m, expected, atol=1e-11)


def test_shared_root_rejects_insufficient_or_duplicate_links() -> None:
    anchors = _anchors()
    base = SharedRangeLink("node", 0, 1.0, np.zeros(3), 0.0, 0.1)
    short = solve_shared_root(
        [base] * 3, anchors_m=anchors, initial_root_m=np.ones(3)
    )
    assert not short.success
    assert short.reason == "FEWER_THAN_FOUR_LINKS"
    duplicate = solve_shared_root(
        [base] * 4, anchors_m=anchors, initial_root_m=np.ones(3)
    )
    assert not duplicate.success
    assert duplicate.reason == "DUPLICATE_NODE_ANCHOR_LINK"


def test_leave_node_uncertainty_is_positive_and_uses_each_node_once() -> None:
    anchors = _anchors()
    expected = np.array([1.7, 1.2, 0.9])
    links = []
    for node, offset in {
        "a": np.array([0.0, 0.0, 0.0]),
        "b": np.array([0.2, 0.0, 0.1]),
        "c": np.array([-0.1, 0.1, -0.2]),
        "d": np.array([0.0, -0.2, 0.3]),
    }.items():
        for anchor in range(8):
            links.append(SharedRangeLink(
                node=node,
                anchor=anchor,
                range_m=float(np.linalg.norm(anchors[anchor] - (expected + offset))),
                tag_offset_world_m=offset,
                link_dt_s=0.0,
                sigma_m=0.1,
            ))
    uncertainty = estimate_leave_node_uncertainty(
        links,
        anchors_m=anchors,
        initial_root_m=np.array([2.0, 1.5, 1.0]),
        minimum_std_m=0.12,
    )
    assert uncertainty.successful_leave_node_solves == 4
    assert uncertainty.expected_leave_node_solves == 4
    assert uncertainty.leave_node_roots_m.shape == (4, 3)
    np.testing.assert_allclose(
        uncertainty.covariance_m2, np.eye(3) * 0.12**2, atol=1e-12
    )
