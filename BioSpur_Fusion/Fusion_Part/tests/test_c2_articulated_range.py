from __future__ import annotations

import numpy as np

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from biospur_fusion.c2_uwb_calibration.articulated_range import (
    SEGMENTS,
    corrected_proxy_points,
    solve_articulated_ranges,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.c2_uwb_calibration.shared_root import SharedRangeLink


def _geometry() -> DisplayProxyGeometry:
    return DisplayProxyGeometry(
        torso_height_m=0.50,
        hip_span_m=0.22,
        shoulder_span_m=0.38,
        segment_length_m={
            "upper_arm_left": 0.30, "forearm_left": 0.25,
            "upper_arm_right": 0.30, "forearm_right": 0.25,
            "thigh_left": 0.43, "shank_left": 0.40,
            "thigh_right": 0.43, "shank_right": 0.40,
        },
    )


def _anchors() -> np.ndarray:
    return np.asarray([
        [0.0, 0.0, 0.0], [4.0, 0.0, 0.0],
        [4.0, 3.0, 0.0], [0.0, 3.0, 0.0],
        [0.0, 0.0, 2.2], [4.0, 0.0, 2.2],
        [4.0, 3.0, 2.2], [0.0, 3.0, 2.2],
    ])


def _perfect_links(
    root: np.ndarray,
    rotations: dict[str, np.ndarray],
    geometry: DisplayProxyGeometry,
) -> list[SharedRangeLink]:
    anchors = _anchors()
    zero = {segment: np.zeros(3) for segment in SEGMENTS}
    points = corrected_proxy_points(rotations, zero, geometry)
    return [
        SharedRangeLink(
            node=node,
            anchor=anchor,
            range_m=float(np.linalg.norm(anchors[anchor] - (root + points[name]))),
            tag_offset_world_m=points[name],
            link_dt_s=0.0,
            sigma_m=0.08,
            facing_score=0.0,
        )
        for node, name in NODE_TO_PROXY_POINT.items()
        for anchor in range(8)
    ]


def test_corrected_proxy_fk_preserves_shared_joint_construction() -> None:
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    corrections = {segment: np.zeros(3) for segment in SEGMENTS}

    points = corrected_proxy_points(rotations, corrections, _geometry())

    np.testing.assert_allclose(
        points["wrist_left"] - points["elbow_left"], [0.0, 0.0, -0.25]
    )
    np.testing.assert_allclose(
        points["ankle_right"] - points["knee_right"], [0.0, 0.0, -0.40]
    )


def test_articulated_raw_ranges_recover_root_without_breaking_fk() -> None:
    anchors = _anchors()
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    zero = {segment: np.zeros(3) for segment in SEGMENTS}
    points = corrected_proxy_points(rotations, zero, geometry)
    expected_root = np.array([1.7, 1.3, 1.0])
    links = []
    for node, point_name in NODE_TO_PROXY_POINT.items():
        tag = expected_root + points[point_name]
        for anchor in range(8):
            links.append(SharedRangeLink(
                node=node,
                anchor=anchor,
                range_m=float(np.linalg.norm(anchors[anchor] - tag)),
                tag_offset_world_m=points[point_name],
                link_dt_s=0.0,
                sigma_m=0.08,
                facing_score=0.0,
            ))

    result = solve_articulated_ranges(
        links,
        anchors_m=anchors,
        base_rotations_world=rotations,
        geometry=geometry,
        initial_root_m=expected_root + np.array([0.06, -0.04, 0.03]),
    )

    assert result.success
    np.testing.assert_allclose(result.root_position_m, expected_root, atol=4e-3)
    assert result.maximum_joint_closure_m == 0.0
    assert max(np.linalg.norm(value) for value in result.segment_correction_rotvec.values()) < 7e-3


def test_facing_prior_never_downweights_negative_or_outward_consistent_range() -> None:
    anchors = _anchors()
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    zero = {segment: np.zeros(3) for segment in SEGMENTS}
    points = corrected_proxy_points(rotations, zero, geometry)
    root = np.array([1.7, 1.3, 1.0])
    node = next(iter(NODE_TO_PROXY_POINT))
    point = points[NODE_TO_PROXY_POINT[node]]
    true_range = float(np.linalg.norm(anchors[0] - (root + point)))
    links = [
        SharedRangeLink(node, anchor, float(np.linalg.norm(anchors[anchor] - (root + point))),
                        point, 0.0, 0.08, 1.0)
        for anchor in range(4)
    ]
    links[0] = SharedRangeLink(node, 0, true_range - 0.05, point, 0.0, 0.08, -1.0)

    result = solve_articulated_ranges(
        links, anchors_m=anchors, base_rotations_world=rotations,
        geometry=geometry, initial_root_m=root,
    )

    assert result.success
    assert result.facing_nlos_weight[0] == 1.0


def test_inactive_segment_corrections_are_not_refit() -> None:
    anchors = _anchors()
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    previous = {segment: np.zeros(3) for segment in SEGMENTS}
    previous["forearm_right"] = np.array([0.03, -0.02, 0.01])
    points = corrected_proxy_points(rotations, previous, geometry)
    root = np.array([1.7, 1.3, 1.0])
    links = []
    for node, point_name in NODE_TO_PROXY_POINT.items():
        tag = root + points[point_name]
        for anchor in range(8):
            links.append(SharedRangeLink(
                node=node,
                anchor=anchor,
                range_m=float(np.linalg.norm(anchors[anchor] - tag)),
                tag_offset_world_m=points[point_name],
                link_dt_s=0.0,
                sigma_m=0.08,
                facing_score=0.0,
            ))

    result = solve_articulated_ranges(
        links,
        anchors_m=anchors,
        base_rotations_world=rotations,
        geometry=geometry,
        initial_root_m=root,
        previous_correction_rotvec=previous,
        active_segments=("torso",),
    )

    assert result.success
    assert result.active_segments == ("torso",)
    assert result.pose_observable_rank >= 1
    np.testing.assert_array_equal(
        result.segment_correction_rotvec["forearm_right"],
        previous["forearm_right"],
    )


def test_inferred_foothold_is_a_joint_residual_in_articulated_solve() -> None:
    anchors = _anchors()
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    zero = {segment: np.zeros(3) for segment in SEGMENTS}
    points = corrected_proxy_points(rotations, zero, geometry)
    expected_root = np.array([1.7, 1.3, 1.0])
    links = []
    # Deliberately weak one-node UWB geometry with a small common positive
    # range bias.  The world ankle residual must pull the connected solve back
    # toward the planted point; it is not applied later as a display offset.
    node = "BSFC2CC"
    point_name = NODE_TO_PROXY_POINT[node]
    tag = expected_root + points[point_name]
    for anchor in range(4):
        links.append(SharedRangeLink(
            node=node,
            anchor=anchor,
            range_m=float(np.linalg.norm(anchors[anchor] - tag) + 0.08),
            tag_offset_world_m=points[point_name],
            link_dt_s=0.0,
            sigma_m=0.08,
            facing_score=0.0,
        ))
    initial = expected_root + np.array([0.05, -0.03, 0.04])
    unconstrained = solve_articulated_ranges(
        links, anchors_m=anchors, base_rotations_world=rotations,
        geometry=geometry, initial_root_m=initial, active_segments=("pelvis",),
    )
    constrained = solve_articulated_ranges(
        links, anchors_m=anchors, base_rotations_world=rotations,
        geometry=geometry, initial_root_m=initial,
        active_segments=("pelvis", "thigh_left", "shank_left"),
        point_constraints_world_m={
            "ankle_left": expected_root + points["ankle_left"]
        },
    )

    assert unconstrained.success and constrained.success
    assert constrained.maximum_joint_closure_m < 0.04
    assert np.linalg.norm(constrained.root_position_m - expected_root) < np.linalg.norm(
        unconstrained.root_position_m - expected_root
    )


def test_hinge_projector_runs_once_after_raw_solve_and_owns_returned_pose() -> None:
    anchors = _anchors()
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    expected_root = np.array([1.7, 1.3, 1.0])
    calls: list[dict[str, np.ndarray]] = []

    def projector(_base, raw):
        calls.append({name: value.copy() for name, value in raw.items()})
        projected = {name: value.copy() for name, value in raw.items()}
        # A small post-solve change is enough to establish that the returned
        # pose is owned by the projector rather than the raw optimizer.
        projected["forearm_left"] += np.array([1e-4, 0.0, 0.0])
        return projected, {"post_projection_all_inside_rom": True}

    result = solve_articulated_ranges(
        _perfect_links(expected_root, rotations, geometry),
        anchors_m=anchors,
        base_rotations_world=rotations,
        geometry=geometry,
        initial_root_m=expected_root + np.array([0.06, -0.04, 0.03]),
        hinge_projector=projector,
    )

    assert result.success
    assert len(calls) == 1
    np.testing.assert_allclose(
        result.segment_correction_rotvec["forearm_left"],
        calls[0]["forearm_left"] + np.array([1e-4, 0.0, 0.0]),
    )
    assert result.hinge_projection["projection_applied"] is True
    assert result.hinge_projection["range_projection_gate"] is True


def test_post_projection_range_regression_fails_closed() -> None:
    anchors = _anchors()
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    root = np.array([1.7, 1.3, 1.0])
    calls = 0

    def projector(_base, raw):
        nonlocal calls
        calls += 1
        projected = {name: value.copy() for name, value in raw.items()}
        projected["torso"] = np.array([0.05, 0.0, 0.0])
        return projected, {"post_projection_all_inside_rom": True}

    result = solve_articulated_ranges(
        _perfect_links(root, rotations, geometry),
        anchors_m=anchors,
        base_rotations_world=rotations,
        geometry=geometry,
        initial_root_m=root,
        hinge_projector=projector,
    )

    assert calls == 1
    assert not result.success
    assert result.reason == "OPTIMIZER_OR_PROJECTED_GEOMETRY_FAILURE"
    assert result.hinge_projection["range_projection_gate"] is False
    assert result.hinge_projection["projected_range_median_abs_m"] > 0.0


def test_post_projection_foothold_regression_fails_closed() -> None:
    anchors = _anchors()
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    zero = {segment: np.zeros(3) for segment in SEGMENTS}
    points = corrected_proxy_points(rotations, zero, geometry)
    root = np.array([1.7, 1.3, 1.0])
    node = "BSFC2CC"
    links = [
        SharedRangeLink(
            node=node,
            anchor=anchor,
            range_m=float(np.linalg.norm(anchors[anchor] - root)),
            tag_offset_world_m=points[NODE_TO_PROXY_POINT[node]],
            link_dt_s=0.0,
            sigma_m=0.08,
            facing_score=0.0,
        )
        for anchor in range(4)
    ]

    def projector(_base, raw):
        projected = {name: value.copy() for name, value in raw.items()}
        projected["shank_left"] = np.array([0.18, 0.0, 0.0])
        return projected, {"post_projection_all_inside_rom": True}

    result = solve_articulated_ranges(
        links,
        anchors_m=anchors,
        base_rotations_world=rotations,
        geometry=geometry,
        initial_root_m=root,
        active_segments=("pelvis",),
        point_constraints_world_m={"ankle_left": root + points["ankle_left"]},
        point_constraint_axes=(0, 1),
        hinge_projector=projector,
    )

    assert not result.success
    assert result.reason == "PROJECTED_FOOTHOLD_GATE_FAILURE"
    assert result.hinge_projection["range_projection_gate"] is True
    assert result.hinge_projection["foothold_projection_gate"] is False


def test_point_constraint_accepts_range_tradeoff_when_full_objective_improves() -> None:
    anchors = _anchors()
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    zero = {segment: np.zeros(3) for segment in SEGMENTS}
    points = corrected_proxy_points(rotations, zero, geometry)
    root = np.array([1.7, 1.3, 1.0])
    ankle = root + points["ankle_left"]
    links = [
        SharedRangeLink(
            node="BSF6C53", anchor=anchor,
            range_m=float(np.linalg.norm(anchors[anchor] - ankle)),
            tag_offset_world_m=points["ankle_left"], link_dt_s=0.0,
            sigma_m=0.08, facing_score=0.0,
        )
        for anchor in range(8)
    ]

    def identity_projector(_base, raw):
        return (
            {name: value.copy() for name, value in raw.items()},
            {"post_projection_all_inside_rom": True},
        )

    result = solve_articulated_ranges(
        links, anchors_m=anchors, base_rotations_world=rotations,
        geometry=geometry, initial_root_m=root,
        active_segments=("pelvis", "thigh_left", "shank_left"),
        point_constraints_world_m={
            "ankle_left": ankle + np.array([0.10, 0.0, 0.0])
        },
        point_constraint_axes=(0, 1), hinge_projector=identity_projector,
    )

    assert result.success
    metrics = result.hinge_projection
    assert metrics["projected_range_median_abs_m"] > metrics[
        "prefit_range_median_abs_m"
    ]
    assert metrics["range_projection_gate"] is False
    assert metrics["joint_projection_gate"] is True
    assert metrics["projection_acceptance_gate"] is True
    assert metrics["projected_full_residual_objective"] < metrics[
        "prefit_full_residual_objective"
    ]


def test_point_constraint_rejects_projector_when_full_objective_worsens() -> None:
    anchors = _anchors()
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    zero = {segment: np.zeros(3) for segment in SEGMENTS}
    points = corrected_proxy_points(rotations, zero, geometry)
    root = np.array([1.7, 1.3, 1.0])
    ankle = root + points["ankle_left"]
    links = [
        SharedRangeLink(
            node="BSF6C53", anchor=anchor,
            range_m=float(np.linalg.norm(anchors[anchor] - ankle)),
            tag_offset_world_m=points["ankle_left"], link_dt_s=0.0,
            sigma_m=0.08, facing_score=0.0,
        )
        for anchor in range(8)
    ]

    def harmful_projector(_base, raw):
        projected = {name: value.copy() for name, value in raw.items()}
        projected["shank_left"] = np.array([0.0, 0.10, 0.0])
        return projected, {"post_projection_all_inside_rom": True}

    result = solve_articulated_ranges(
        links, anchors_m=anchors, base_rotations_world=rotations,
        geometry=geometry, initial_root_m=root,
        active_segments=("pelvis", "thigh_left", "shank_left"),
        point_constraints_world_m={
            "ankle_left": ankle + np.array([0.10, 0.0, 0.0])
        },
        point_constraint_axes=(0, 1), hinge_projector=harmful_projector,
    )

    assert not result.success
    assert result.reason == "PROJECTED_JOINT_OBJECTIVE_FAILURE"
    metrics = result.hinge_projection
    assert metrics["foothold_projection_gate"] is True
    assert metrics["joint_projection_gate"] is False
    assert metrics["projection_acceptance_gate"] is False
    assert metrics["projected_full_residual_objective"] > metrics[
        "prefit_full_residual_objective"
    ]
