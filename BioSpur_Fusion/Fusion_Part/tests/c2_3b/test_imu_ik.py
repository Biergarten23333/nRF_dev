from __future__ import annotations

import math

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_3b_imu_ik.contracts import (
    CENTRAL_PROFILE,
    EDGE_ROWS,
    PROJECTOR_HUBER_CHORD,
    SEGMENTS,
    AxisPair,
)
from biospur_fusion.c2_3b_imu_ik.axis_factor import (
    projector_huber_pseudo_residual,
    projector_huber_pseudo_residual_parent,
)
from biospur_fusion.c2_3b_imu_ik.equivariance_validation import (
    validate_numerical_equivariance,
)
from biospur_fusion.c2_3b_imu_ik.absolute_step_validation import (
    validate_abs01,
    validate_abs02,
)
from biospur_fusion.c2_3b_imu_ik.metrics import first_order_mask, second_order_mask
from biospur_fusion.c2_3b_imu_ik.provenance import (
    load_axes,
    load_display_geometries,
    load_episodes,
    verify_safe_bindings,
    verify_runtime,
)
from biospur_fusion.c2_3b_imu_ik.proxy import closed_segment_distance, screen_frame
from biospur_fusion.c2_3b_imu_ik.solver import solve_frame
from biospur_fusion.c2_3b_imu_ik.synthetic_generator import make_case
from biospur_fusion.c2_3b_imu_ik.synthetic_validation import (
    validate_cut_locus,
    validate_full_circle,
    validate_neg08,
    validate_neg09,
    validate_negative_fixtures,
)


def test_runtime_and_orientation_only_projection() -> None:
    assert len(verify_safe_bindings()) == 13
    runtime = verify_runtime(full_records=False)
    assert runtime["interpreter"].startswith("1643dacd")
    episodes, output = load_episodes()
    assert len(episodes) == 21
    assert sum(int(row.full_body_valid.sum()) for row in episodes.values()) == 14521
    assert np.linalg.det(output) < 0.0


def test_exact_synthetic_frame_is_unchanged() -> None:
    case = make_case("SYN00_EXACT_DYNAMIC")
    axes = {
        axis.name: AxisPair(
            axis.name,
            axis.parent,
            axis.child,
            axis.parent_axis,
            axis.child_axis,
        )
        for axis in case.estimator_axes
    }
    result = solve_frame(case.measured[100], axes, CENTRAL_PROFILE)
    assert result.valid
    assert np.max(np.abs(result.matrices - case.truth[100])) < 1e-12


def test_projector_huber_cost_is_exact() -> None:
    axis = np.array([1.0, 0.0, 0.0])
    for angle in (0.0, math.radians(5.0), math.radians(30.0), math.pi / 2.0):
        child = np.array(
            [
                [math.cos(angle), -math.sin(angle), 0.0],
                [math.sin(angle), math.cos(angle), 0.0],
                [0.0, 0.0, 1.0],
            ]
        )
        residual = projector_huber_pseudo_residual(
            np.eye(3), child, axis, axis, 0.5
        )
        chord = math.sin(min(angle, math.pi - angle))
        huber = (
            0.5 * chord * chord
            if chord <= PROJECTOR_HUBER_CHORD
            else PROJECTOR_HUBER_CHORD * (chord - 0.5 * PROJECTOR_HUBER_CHORD)
        )
        assert math.isclose(
            0.5 * float(residual @ residual), 0.5 * huber, abs_tol=1e-15
        )


def test_antiparallel_axis_is_same_line_and_single_flip_invariant() -> None:
    axis = np.array([1.0, 0.0, 0.0])
    residual = projector_huber_pseudo_residual(
        np.eye(3),
        np.diag([-1.0, -1.0, 1.0]),
        axis,
        axis,
        1.0,
    )
    flipped = projector_huber_pseudo_residual(
        np.eye(3), np.eye(3), axis, -axis, 1.0
    )
    assert np.linalg.norm(residual) <= 1e-12
    assert np.array_equal(flipped, np.zeros(9))


def test_parent_local_projector_does_not_depend_on_common_root() -> None:
    relative = Rotation.from_rotvec(np.array([0.2, -0.1, 0.3])).as_matrix()
    parent_axis = np.array([1.0, 0.0, 0.0])
    child_axis = np.array([0.0, 1.0, 0.0])
    reference = projector_huber_pseudo_residual_parent(
        parent_axis, relative, child_axis, 1.0
    )
    assert np.array_equal(
        reference,
        projector_huber_pseudo_residual_parent(
            parent_axis, relative, -child_axis, 1.0
        ),
    )


def test_mask_gaps_break_transitions_and_triples() -> None:
    valid = np.array([True, True, False, True, True, True])
    assert first_order_mask(valid).tolist() == [False, True, False, False, True, True]
    assert second_order_mask(valid).tolist() == [False, False, False, False, True, False]


def test_proxy_crossing_and_accepted_exact_frame() -> None:
    assert closed_segment_distance(
        np.array([-1.0, 0.0, 0.0]),
        np.array([1.0, 0.0, 0.0]),
        np.array([0.0, -1.0, 0.0]),
        np.array([0.0, 1.0, 0.0]),
    ) == 0.0
    episodes, output = load_episodes()
    geometry = load_display_geometries()[1]
    frame = episodes["00"].matrices[70]
    result = screen_frame(frame, frame, geometry, output)
    assert result.passed


def test_all_negative_mutations_reach_their_named_gate() -> None:
    _, output = load_episodes()
    results = validate_negative_fixtures(load_display_geometries()[1], output)
    assert len(results) == 7
    assert all(row["passed"] and row["rejected"] for row in results)


def test_projective_fixture_contracts() -> None:
    full_circle = validate_full_circle(CENTRAL_PROFILE)
    cut = validate_cut_locus()
    neg08 = validate_neg08(CENTRAL_PROFILE)
    neg09 = validate_neg09(CENTRAL_PROFILE)
    assert not full_circle["passed"]
    assert sum(
        row["message"] == "SO3_LOG_CUT_AT_STATE"
        for row in full_circle["values"]["runs"]
    ) == 4
    assert cut["values"]["near_cut_solver_run_count"] == 36
    assert cut["checks"]["exact_cut_fail_closed"]
    assert all(
        passed
        for name, passed in cut["checks"].items()
        if name != "all_near_cut_runs_converge"
    )
    assert neg08["named_gates"] == [
        "KNOWN_TRUTH_MAX_ERROR_1E-6_RAD",
        "POSE_CHANGE_MAX_1E-6_RAD",
    ]
    assert not neg09["passed"]
    assert not neg09["checks"]["all_runs_converge"]


def test_parent_local_preregistered_numerical_gates() -> None:
    result = validate_numerical_equivariance()
    assert result["passed"]
    assert all(row["passed"] for row in result["gates"])


def test_absolute_step_preregistered_numeric_gates() -> None:
    abs01 = validate_abs01()
    abs02 = validate_abs02()
    assert abs01["passed"]
    assert abs01["values"]["zero_to_worst_disparity"] == 14573675019.138222
    assert abs02["passed"]
    assert abs02["values"]["exact_log_cut_mutation"]["message"] == "SO3_LOG_CUT_AT_STATE"
