from __future__ import annotations

import numpy as np
from dataclasses import replace

from audit_c2_dynamic_other_body_occlusion_o0 import (
    _body_classification,
    _causal_geometry_features,
    _loo_training_rows,
    _per_link_time_s,
    _ray_capsule_intervals,
    _signed_range_innovation_m,
    _strict_preceding_index,
)
from biospur_fusion.c2_uwb_calibration.shared_root import SharedRangeLink
from biospur_fusion.c2_uwb_calibration.shared_root import solve_shared_root


def test_ray_capsule_chord_has_metric_units_and_expected_sign() -> None:
    intervals = _ray_capsule_intervals(
        np.array([0.0, 0.0, 0.0]),
        np.array([10.0, 0.0, 0.0]),
        np.array([5.0, 0.0, -1.0]),
        np.array([5.0, 0.0, 1.0]),
        0.25,
    )
    assert len(intervals) == 1
    assert np.allclose(intervals[0], (4.75, 5.25), atol=1e-10)


def test_exact_clock_strict_floor_t_round_and_innovation_sign() -> None:
    absolute = np.asarray([100_000_000, 105_000_000, 110_000_000], dtype=float)
    assert _strict_preceding_index(absolute, 110_000_000.0) == 1

    class Clock:
        @staticmethod
        def seconds(value: float) -> float:
            return value * 1e-6

    class Raw:
        strobe_us = 1000
        t_round_us = np.asarray([0.0, 0.0, 8000.0])

    assert np.isclose(_per_link_time_s(Clock(), Raw(), 2), 0.005)
    assert np.isclose(_signed_range_innovation_m(5.2, 5.0), 0.2)
    assert np.isclose(_signed_range_innovation_m(4.8, 5.0), -0.2)


def test_loo_training_excludes_exact_target_identity() -> None:
    exact = []
    for node in ("left", "right"):
        for anchor in range(4):
            exact.append({
                "link": SharedRangeLink(
                    node=node,
                    anchor=anchor,
                    range_m=4.0,
                    tag_offset_world_m=np.zeros(3),
                    link_dt_s=anchor * 0.001,
                    sigma_m=0.1,
                )
            })
    target_index = 2
    target = exact[target_index]["link"]
    shared = _loo_training_rows(exact, target_index, same_node=False)
    same = _loo_training_rows(exact, target_index, same_node=True)
    assert len(shared) == 7
    assert len(same) == 3
    assert all(row["link"] is not target for row in shared)
    assert all(row["link"] is not target for row in same)
    assert {row["link"].node for row in same} == {"left"}


def test_held_range_mutation_cannot_change_same_node_or_shared_loo_prediction() -> None:
    anchors = np.asarray([
        [0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0], [4.0, 4.0, 0.0],
        [0.0, 0.0, 3.0], [4.0, 0.0, 3.0], [0.0, 4.0, 3.0], [4.0, 4.0, 3.0],
    ])
    true_root = np.array([2.0, 1.8, 1.1])
    exact = []
    for node, offset in (("left", np.zeros(3)), ("right", np.array([0.2, 0.0, 0.0]))):
        for anchor in range(8):
            exact.append({
                "link": SharedRangeLink(
                    node=node,
                    anchor=anchor,
                    range_m=float(np.linalg.norm(anchors[anchor] - (true_root + offset))),
                    tag_offset_world_m=offset,
                    link_dt_s=0.0,
                    sigma_m=0.1,
                )
            })
    target_index = 0

    def roots(rows: list[dict[str, object]]) -> tuple[np.ndarray, np.ndarray]:
        same = [row["link"] for row in _loo_training_rows(rows, target_index, same_node=True)]
        shared = [row["link"] for row in _loo_training_rows(rows, target_index, same_node=False)]
        same_result = solve_shared_root(same, anchors_m=anchors, initial_root_m=np.array([1.8, 2.0, 1.0]))
        shared_result = solve_shared_root(shared, anchors_m=anchors, initial_root_m=np.array([1.8, 2.0, 1.0]))
        assert same_result.success and shared_result.success
        return same_result.root_position_m, shared_result.root_position_m

    before = roots(exact)
    mutated = [{"link": row["link"]} for row in exact]
    mutated[target_index]["link"] = replace(
        mutated[target_index]["link"], range_m=999.0
    )
    after = roots(mutated)
    assert np.array_equal(before[0], after[0])
    assert np.array_equal(before[1], after[1])


def test_training_range_mutation_can_change_loo_but_not_causal_geometry() -> None:
    anchors = np.asarray([
        [0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0], [4.0, 4.0, 0.0],
        [0.0, 0.0, 3.0], [4.0, 0.0, 3.0], [0.0, 4.0, 3.0], [4.0, 4.0, 3.0],
    ])
    root = np.array([2.0, 1.8, 1.1])
    exact = []
    for anchor in range(8):
        exact.append({
            "link": SharedRangeLink(
                node="BSFEC35",
                anchor=anchor,
                range_m=float(np.linalg.norm(anchors[anchor] - root)),
                tag_offset_world_m=np.zeros(3),
                link_dt_s=0.0,
                sigma_m=0.1,
            ),
            "causal_prior_tag_origin_m": np.array([2.0, 2.0, 1.0]),
            "prior": {
                "normals": {"BSFEC35": np.array([1.0, 0.0, 0.0])},
                "joints": _far_joints(),
            },
        })
    target = exact[0]
    training = [row["link"] for row in _loo_training_rows(exact, 0, same_node=True)]
    first = solve_shared_root(training, anchors_m=anchors, initial_root_m=np.array([1.8, 2.0, 1.0]))
    mutated = [{**row, "link": row["link"]} for row in exact]
    for index in range(1, len(mutated)):
        mutated[index]["link"] = replace(mutated[index]["link"], range_m=mutated[index]["link"].range_m + 0.4)
    second_training = [row["link"] for row in _loo_training_rows(mutated, 0, same_node=True)]
    second = solve_shared_root(second_training, anchors_m=anchors, initial_root_m=np.array([1.8, 2.0, 1.0]))
    assert first.success and second.success
    assert not np.allclose(first.root_position_m, second.root_position_m)

    first_geometry = _causal_geometry_features(target, target["link"], anchors)
    second_geometry = _causal_geometry_features(mutated[0], mutated[0]["link"], anchors)
    assert np.array_equal(first_geometry[0], second_geometry[0])
    assert first_geometry[1] == second_geometry[1]
    assert first_geometry[2] == second_geometry[2]


def _far_joints() -> dict[str, np.ndarray]:
    return {
        "pelvis_center": np.array([0.0, 10.0, 0.0]),
        "shoulder_mid": np.array([0.0, 10.0, 1.0]),
        "shoulder_left": np.array([0.0, 10.0, 1.0]),
        "elbow_left": np.array([0.0, 10.0, 0.5]),
        "wrist_left": np.array([0.0, 0.0, 0.0]),
        "shoulder_right": np.array([0.0, 10.0, 1.0]),
        "elbow_right": np.array([0.0, 10.0, 0.5]),
        "wrist_right": np.array([0.0, 10.0, 0.0]),
        "hip_left": np.array([-0.1, 10.0, 0.0]),
        "knee_left": np.array([-0.1, 10.0, -0.5]),
        "ankle_left": np.array([-0.1, 10.0, -1.0]),
        "hip_right": np.array([0.1, 10.0, 0.0]),
        "knee_right": np.array([0.1, 10.0, -0.5]),
        "ankle_right": np.array([0.1, 10.0, -1.0]),
    }


def test_origin_connected_other_body_capsule_is_ambiguous_not_clipped_blockage() -> None:
    joints = _far_joints()
    joints["pelvis_center"] = np.zeros(3)
    joints["shoulder_mid"] = np.array([0.0, 0.0, 1.0])
    result = _body_classification(
        node="BSFC2CC",
        origin=np.zeros(3),
        anchor=np.array([10.0, 0.0, 0.0]),
        joints_relative=joints,
    )
    for envelope in result["envelopes"].values():
        assert envelope["state"] == "OTHER_BODY_NEAR_FIELD_AMBIGUOUS"
        assert envelope["union_usable_chord_length_m"] == 0.0
        assert envelope["minimum_usable_normalized_signed_clearance"] is None
        torso = next(row for row in envelope["per_occluder"] if row["segment"] == "torso")
        assert torso["near_field_ambiguous_chord_intervals_m_from_tag"]
        assert not torso["usable_ray_chord_intervals_m_from_tag"]


def test_distinct_far_other_body_capsule_is_blocked_with_continuous_descriptors() -> None:
    joints = _far_joints()
    joints["shoulder_right"] = np.array([5.0, 0.0, -1.0])
    joints["elbow_right"] = np.array([5.0, 0.0, 1.0])
    joints["wrist_right"] = np.array([5.0, 10.0, 1.0])
    result = _body_classification(
        node="BSFEC35",
        origin=np.zeros(3),
        anchor=np.array([10.0, 0.0, 0.0]),
        joints_relative=joints,
    )
    envelope = result["envelopes"]["small"]
    assert envelope["state"] == "OTHER_BODY_BLOCKED_PROXY"
    upper = next(row for row in envelope["per_occluder"] if row["segment"] == "upper_arm_right")
    assert np.isclose(upper["usable_chord_length_m"], 0.07, atol=1e-9)
    assert upper["normalized_signed_clearance"] < 0.0
    assert 0.0 < upper["first_usable_intersection_fraction"] < 1.0
    assert upper["solid_angle_proxy_sr"] > 0.0
    assert envelope["union_usable_chord_length_m"] > 0.0
    assert result["emitter_reconstruction_error_m"] == 0.0
