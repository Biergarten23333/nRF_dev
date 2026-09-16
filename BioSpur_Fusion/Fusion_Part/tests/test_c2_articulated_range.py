from __future__ import annotations

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from biospur_fusion.c2_uwb_calibration import articulated_range
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


def test_corrected_proxy_fk_preserves_writable_c_contiguous_fast_path(monkeypatch) -> None:
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    corrections = {segment: np.zeros(3) for segment in SEGMENTS}
    corrections["forearm_right"][:] = [0.01, -0.02, 0.03]
    expected = corrected_proxy_points(rotations, corrections, _geometry())
    observed = []

    class ObservedRotation:
        @staticmethod
        def from_rotvec(value):
            observed.append(value)
            return Rotation.from_rotvec(value)

    monkeypatch.setattr(articulated_range, "Rotation", ObservedRotation)
    actual = corrected_proxy_points(rotations, corrections, _geometry())

    assert len(observed) == len(SEGMENTS)
    for segment, value in zip(SEGMENTS, observed):
        assert value.flags.writeable and value.flags.c_contiguous
        assert np.shares_memory(value, corrections[segment])
    for point in expected:
        np.testing.assert_array_equal(actual[point], expected[point])


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


def test_legacy_path_is_identical_when_link_information_weights_change() -> None:
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    root = np.array([1.7, 1.3, 1.0])
    unit = _perfect_links(root, rotations, geometry)
    weighted = [
        SharedRangeLink(**{
            **link.__dict__, "information_weight": 0.17 + 0.08 * (index % 8)
        })
        for index, link in enumerate(unit)
    ]

    baseline = solve_articulated_ranges(
        unit, anchors_m=_anchors(), base_rotations_world=rotations,
        geometry=geometry, initial_root_m=root,
    )
    candidate = solve_articulated_ranges(
        weighted, anchors_m=_anchors(), base_rotations_world=rotations,
        geometry=geometry, initial_root_m=root,
        fixed_root_position_m=None,
    )

    assert baseline.reason == candidate.reason
    assert baseline.nfev == candidate.nfev
    np.testing.assert_array_equal(baseline.root_position_m, candidate.root_position_m)
    np.testing.assert_array_equal(baseline.physical_residual_m, candidate.physical_residual_m)
    np.testing.assert_array_equal(baseline.effective_weight, candidate.effective_weight)
    np.testing.assert_array_equal(baseline.root_covariance_m2, candidate.root_covariance_m2)


def test_fixed_root_mode_conditions_pose_without_refitting_root() -> None:
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    root = np.array([1.7, 1.3, 1.0])
    links = [
        SharedRangeLink(**{
            **link.__dict__, "information_weight": 0.35 + 0.05 * (index % 8)
        })
        for index, link in enumerate(_perfect_links(root, rotations, geometry))
    ]

    result = solve_articulated_ranges(
        links, anchors_m=_anchors(), base_rotations_world=rotations,
        geometry=geometry, initial_root_m=root,
        fixed_root_position_m=root,
    )

    assert result.success
    np.testing.assert_array_equal(result.root_position_m, root)
    assert result.root_covariance_m2 is None
    assert result.hinge_projection["root_covariance_status"] == (
        "UNAVAILABLE_FIXED_ROOT_NOT_ESTIMATED"
    )
    assert np.isfinite(result.physical_residual_m).all()


def test_fixed_root_analytic_jacobian_matches_branch_safe_oracles(monkeypatch) -> None:
    root = np.array([1.7, 1.3, 1.0])
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    links = _perfect_links(root, rotations, _geometry())
    actual_least_squares = articulated_range.least_squares
    captured = {}

    def observed(fun, x0, *args, **kwargs):
        captured.update(fun=fun, jac=kwargs["jac"], x0=x0.copy(), bounds=kwargs["bounds"])
        return actual_least_squares(fun, x0, *args, **kwargs)

    monkeypatch.setattr(articulated_range, "least_squares", observed)
    result = solve_articulated_ranges(
        links, anchors_m=_anchors(), base_rotations_world=rotations,
        geometry=_geometry(), initial_root_m=root, fixed_root_position_m=root,
        active_segments=SEGMENTS,
    )
    assert result.success

    interior = captured["x0"] + np.linspace(-0.03, 0.03, len(captured["x0"]))
    analytic = captured["jac"](interior)
    step = 1e-6
    central = np.column_stack([
        (
            captured["fun"](interior + np.eye(len(interior))[index] * step)
            - captured["fun"](interior - np.eye(len(interior))[index] * step)
        ) / (2.0 * step)
        for index in range(len(interior))
    ])
    np.testing.assert_allclose(analytic, central, atol=1e-7, rtol=1e-5)

    direction = np.sin(np.arange(len(interior)) + 1.0)
    direction /= np.linalg.norm(direction)
    errors = []
    for size in (1e-2, 5e-3, 2.5e-3, 1.25e-3):
        oracle = (
            captured["fun"](interior + size * direction)
            - captured["fun"](interior - size * direction)
        ) / (2.0 * size)
        errors.append(float(np.linalg.norm(oracle - analytic @ direction)))
    assert all(left >= 3.0 * right for left, right in zip(errors, errors[1:]))

    upper = np.asarray(captured["bounds"][1])
    near_upper = upper - 1e-8
    feasible = -np.ones_like(near_upper) / np.sqrt(len(near_upper))
    bound_jacobian = captured["jac"](near_upper)
    bound_step = 1e-6
    one_sided = (
        captured["fun"](near_upper + bound_step * feasible)
        - captured["fun"](near_upper)
    ) / bound_step
    np.testing.assert_allclose(
        bound_jacobian @ feasible, one_sided, atol=2e-5, rtol=2e-4
    )


@pytest.mark.parametrize(
    ("range_delta_m", "use_facing", "expected_branch"),
    [(0.24, False, "huber"), (0.12, True, "facing"), (0.24, True, "both")],
)
def test_fixed_root_analytic_jacobian_active_weight_branches(
    monkeypatch, range_delta_m, use_facing, expected_branch
) -> None:
    root = np.array([1.7, 1.3, 1.0])
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    links = _perfect_links(root, rotations, _geometry())
    links[0] = SharedRangeLink(**{
        **links[0].__dict__, "range_m": links[0].range_m + range_delta_m,
        "facing_score": -0.5,
    })
    actual = articulated_range.least_squares
    captured = {}

    def observed(fun, x0, *args, **kwargs):
        captured.update(fun=fun, jac=kwargs["jac"], x0=x0.copy())
        return actual(fun, x0, *args, **kwargs)

    monkeypatch.setattr(articulated_range, "least_squares", observed)
    solve_articulated_ranges(
        links, anchors_m=_anchors(), base_rotations_world=rotations,
        geometry=_geometry(), initial_root_m=root, fixed_root_position_m=root,
        active_segments=SEGMENTS, use_facing_nlos_prior=use_facing,
    )
    x = captured["x0"]
    jacobian = captured["jac"](x)
    step = 1e-7
    central = np.column_stack([
        (
            captured["fun"](x + np.eye(len(x))[index] * step)
            - captured["fun"](x - np.eye(len(x))[index] * step)
        ) / (2.0 * step)
        for index in range(len(x))
    ])
    np.testing.assert_allclose(jacobian, central, atol=1e-7, rtol=1e-5)
    standardized = range_delta_m / links[0].sigma_m
    assert (standardized > 2.5) == (expected_branch in {"huber", "both"})
    assert (range_delta_m > links[0].sigma_m and use_facing) == (
        expected_branch in {"facing", "both"}
    )


@pytest.mark.parametrize("switch", ["huber", "facing"])
def test_fixed_root_analytic_jacobian_owns_exact_nonsmooth_switch(
    monkeypatch, switch
) -> None:
    root = np.array([1.7, 1.3, 1.0])
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    geometry = _geometry()
    points = corrected_proxy_points(
        rotations, {segment: np.zeros(3) for segment in SEGMENTS}, geometry
    )
    anchors = _anchors()
    anchors[0] = root + points["shoulder_mid"] - np.array([1.0, 0.0, 0.0])
    links = [
        SharedRangeLink(
            node=node, anchor=anchor,
            range_m=float(np.linalg.norm(anchors[anchor] - (root + points[name]))),
            tag_offset_world_m=points[name], link_dt_s=0.0,
            sigma_m=0.125, facing_score=0.0,
        )
        for node, name in NODE_TO_PROXY_POINT.items()
        for anchor in range(8)
    ]
    delta = 0.3125 if switch == "huber" else 0.125
    links[0] = SharedRangeLink(**{
        **links[0].__dict__, "range_m": links[0].range_m + delta,
        "facing_score": -0.5,
    })
    actual = articulated_range.least_squares
    captured = {}

    def observed(fun, x0, *args, **kwargs):
        captured.update(fun=fun, jac=kwargs["jac"], x0=x0.copy())
        return actual(fun, x0, *args, **kwargs)

    monkeypatch.setattr(articulated_range, "least_squares", observed)
    solve_articulated_ranges(
        links, anchors_m=anchors, base_rotations_world=rotations,
        geometry=geometry, initial_root_m=root, fixed_root_position_m=root,
        active_segments=SEGMENTS, use_facing_nlos_prior=(switch == "facing"),
    )
    vector = captured["x0"]
    jacobian = captured["jac"](vector)
    assert jacobian.shape == (len(links) + 6 * len(SEGMENTS), 3 * len(SEGMENTS))
    assert np.isfinite(jacobian).all()
    # Equality belongs to the inner branch (both production predicates use
    # strict ``>``).  Move along the feasible side that reduces positive
    # innovation and compare the owned one-sided derivative at the switch.
    direction = -jacobian[0] / np.linalg.norm(jacobian[0])
    step = 1e-8
    feasible_one_sided = (
        captured["fun"](vector + step * direction) - captured["fun"](vector)
    ) / step
    np.testing.assert_allclose(
        jacobian @ direction, feasible_one_sided, atol=2e-6, rtol=2e-5
    )


def test_fixed_root_analytic_jacobian_zero_distance_fails_closed() -> None:
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    points = corrected_proxy_points(
        rotations, {segment: np.zeros(3) for segment in SEGMENTS}, _geometry()
    )
    node = next(iter(NODE_TO_PROXY_POINT))
    root = _anchors()[0] - points[NODE_TO_PROXY_POINT[node]]
    links = _perfect_links(root, rotations, _geometry())
    links[0] = SharedRangeLink(**{
        **links[0].__dict__, "range_m": np.finfo(float).eps,
    })
    result = solve_articulated_ranges(
        links, anchors_m=_anchors(), base_rotations_world=rotations,
        geometry=_geometry(), initial_root_m=root, fixed_root_position_m=root,
        active_segments=SEGMENTS,
    )
    assert not result.success
    assert result.reason == "NUMERICAL_FAILURE"


@pytest.mark.parametrize("mode", ["wrong_shape", "nonfinite"])
def test_fixed_root_malformed_analytic_jacobian_fails_closed(monkeypatch, mode) -> None:
    root = np.array([1.7, 1.3, 1.0])
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    actual = articulated_range.least_squares

    def malformed(fun, x0, *args, **kwargs):
        jacobian = kwargs["jac"]

        def bad(value):
            result = jacobian(value)
            return result[:, :-1] if mode == "wrong_shape" else result * np.nan

        return actual(fun, x0, *args, **{**kwargs, "jac": bad})

    monkeypatch.setattr(articulated_range, "least_squares", malformed)
    result = solve_articulated_ranges(
        _perfect_links(root, rotations, _geometry()), anchors_m=_anchors(),
        base_rotations_world=rotations, geometry=_geometry(), initial_root_m=root,
        fixed_root_position_m=root, active_segments=SEGMENTS,
    )
    assert not result.success
    assert result.reason == "NUMERICAL_FAILURE"


def test_fixed_root_early_failure_has_no_numeric_covariance_placeholder() -> None:
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    root = np.array([1.7, 1.3, 1.0])
    links = _perfect_links(root, rotations, geometry)[:3]

    legacy = solve_articulated_ranges(
        links, anchors_m=_anchors(), base_rotations_world=rotations,
        geometry=geometry, initial_root_m=root,
    )
    explicit_legacy = solve_articulated_ranges(
        links, anchors_m=_anchors(), base_rotations_world=rotations,
        geometry=geometry, initial_root_m=root, fixed_root_position_m=None,
    )
    fixed = solve_articulated_ranges(
        links, anchors_m=_anchors(), base_rotations_world=rotations,
        geometry=geometry, initial_root_m=root, fixed_root_position_m=root,
    )

    assert legacy.reason == explicit_legacy.reason == fixed.reason == "FEWER_THAN_FOUR_LINKS"
    np.testing.assert_array_equal(legacy.root_covariance_m2, explicit_legacy.root_covariance_m2)
    assert fixed.root_covariance_m2 is None
    assert fixed.hinge_projection["root_covariance_status"] == (
        "UNAVAILABLE_FIXED_ROOT_NOT_ESTIMATED"
    )


def test_fixed_root_projected_failure_has_no_numeric_covariance_placeholder() -> None:
    geometry = _geometry()
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    root = np.array([1.7, 1.3, 1.0])

    def harmful_projector(_base, raw):
        projected = {name: value.copy() for name, value in raw.items()}
        projected["torso"] = np.array([0.05, 0.0, 0.0])
        return projected, {"post_projection_all_inside_rom": True}

    result = solve_articulated_ranges(
        _perfect_links(root, rotations, geometry), anchors_m=_anchors(),
        base_rotations_world=rotations, geometry=geometry,
        initial_root_m=root, fixed_root_position_m=root,
        hinge_projector=harmful_projector,
    )

    assert not result.success
    assert result.reason == "PROJECTED_JOINT_OBJECTIVE_FAILURE"
    assert result.root_covariance_m2 is None
    assert result.hinge_projection["root_covariance_status"] == (
        "UNAVAILABLE_FIXED_ROOT_NOT_ESTIMATED"
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
    assert result.reason == "PROJECTED_JOINT_OBJECTIVE_FAILURE"
    assert result.hinge_projection["range_projection_gate"] is False
    assert result.hinge_projection["joint_projection_gate"] is False
    assert result.hinge_projection["projection_acceptance_owner"] == (
        "FULL_EXISTING_NORMALIZED_RESIDUAL_OBJECTIVE"
    )
    assert result.hinge_projection["projected_range_median_abs_m"] > 0.0


def test_no_point_range_median_tradeoff_accepts_full_objective_improvement() -> None:
    root = np.array([1.7, 1.3, 1.0])
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    geometry = _geometry()
    biases = (-0.028010702851892402, -0.03069480376792692,
              0.11544006382117132, -0.14177361695474588,
              0.08856502774148209, -0.13187673156436874,
              -0.039754907123990005, -0.10085678021066533)
    links = [
        SharedRangeLink(**{**link.__dict__, "range_m": link.range_m + biases[index]})
        for index, link in enumerate(_perfect_links(root, rotations, geometry)[:8])
    ]

    def identity_projector(_base, raw):
        return ({name: value.copy() for name, value in raw.items()},
                {"post_projection_all_inside_rom": True})

    result = solve_articulated_ranges(
        links, anchors_m=_anchors(), base_rotations_world=rotations,
        geometry=geometry, initial_root_m=root + np.array([0.06, -0.04, 0.03]),
        hinge_projector=identity_projector,
    )

    metrics = result.hinge_projection
    assert result.success
    assert metrics["point_constraints_present"] is False
    assert metrics["range_projection_gate"] is False
    assert metrics["joint_projection_gate"] is metrics["projection_acceptance_gate"] is True
    assert metrics["projection_acceptance_owner"] == (
        "FULL_EXISTING_NORMALIZED_RESIDUAL_OBJECTIVE"
    )
    assert metrics["projection_acceptance_branch"] == "JOINT_FULL_RESIDUAL"
    assert metrics["projected_range_median_abs_m"] > metrics["prefit_range_median_abs_m"]
    assert metrics["projected_full_residual_objective"] < metrics[
        "prefit_full_residual_objective"
    ]


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
    assert metrics["projection_acceptance_owner"] == (
        "FULL_EXISTING_NORMALIZED_RESIDUAL_OBJECTIVE"
    )
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
    assert metrics["projection_acceptance_owner"] == (
        "FULL_EXISTING_NORMALIZED_RESIDUAL_OBJECTIVE"
    )
    assert metrics["projected_full_residual_objective"] > metrics[
        "prefit_full_residual_objective"
    ]
