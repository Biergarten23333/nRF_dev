from __future__ import annotations

import numpy as np

from biospur_fusion.c2_uwb_calibration.adaptive_nodes import (
    adaptive_root_minimum_std_m,
    propagation_mode,
    select_trusted_body_nodes,
)
from biospur_fusion.c2_uwb_calibration.articulated_range import (
    NODE_KINEMATIC_PATHS,
    active_segments_for_nodes,
)
from biospur_fusion.c2_uwb_calibration.shared_root import SharedRangeLink


NODES = tuple(NODE_KINEMATIC_PATHS)


def _anchors() -> np.ndarray:
    return np.asarray([
        [0.0, 0.0, 0.0], [4.0, 0.0, 0.0],
        [4.0, 3.0, 0.0], [0.0, 3.0, 0.0],
        [0.0, 0.0, 2.2], [4.0, 0.0, 2.2],
        [4.0, 3.0, 2.2], [0.0, 3.0, 2.2],
    ])


def _links(nodes: tuple[str, ...], *, corrupt: str | None = None):
    anchors = _anchors()
    root = np.array([1.7, 1.3, 1.0])
    rows = []
    for node_index, node in enumerate(nodes):
        offset = np.array([0.03 * node_index, -0.01 * node_index, 0.0])
        for anchor in range(8):
            measured = float(np.linalg.norm(anchors[anchor] - (root + offset)))
            if node == corrupt:
                measured += 1.5 if anchor % 2 else -0.6
            rows.append(SharedRangeLink(
                node=node,
                anchor=anchor,
                range_m=measured,
                tag_offset_world_m=offset,
                link_dt_s=0.0,
                sigma_m=0.08,
                facing_score=0.0,
            ))
    return rows, anchors, root


def test_single_trusted_node_is_root_translation_only() -> None:
    links, anchors, root = _links((NODES[0],))
    selected = select_trusted_body_nodes(
        links, anchors_m=anchors, initial_root_m=root + 0.2
    )

    assert selected.mode == "SINGLE_NODE_ROOT_TRANSLATION"
    assert selected.trusted_nodes == (NODES[0],)
    assert len(selected.trusted_links) == 8


def test_x_of_ten_rejects_bad_node_and_propagates_remaining_body() -> None:
    observed = NODES[:4]
    links, anchors, root = _links(observed, corrupt=observed[-1])
    selected = select_trusted_body_nodes(
        links, anchors_m=anchors, initial_root_m=root
    )

    assert selected.mode == "PARTIAL_NODE_FK_PROPAGATION"
    assert set(selected.trusted_nodes) == set(observed[:-1])
    assert not next(
        row for row in selected.assessments if row.node == observed[-1]
    ).trusted


def test_ten_of_ten_enters_full_articulated_consensus() -> None:
    links, anchors, root = _links(NODES)
    selected = select_trusted_body_nodes(
        links, anchors_m=anchors, initial_root_m=root
    )

    assert selected.mode == "FULL_NODE_CONSTRAINED_CONSENSUS"
    assert selected.trusted_nodes == tuple(sorted(NODES))
    assert len(selected.trusted_links) == 80


def test_active_segments_follow_only_observed_kinematic_paths() -> None:
    active = active_segments_for_nodes(("BSFC2CC", "BSFEC35"))

    assert active == ("torso", "upper_arm_left", "forearm_left")
    assert "upper_arm_right" not in active
    assert propagation_mode(0) == "NO_TRUSTED_NODE"
    assert propagation_mode(10) == "FULL_NODE_CONSTRAINED_CONSENSUS"
    assert adaptive_root_minimum_std_m(1) > adaptive_root_minimum_std_m(10)
