from __future__ import annotations

import ast
import inspect
from dataclasses import replace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_3a_kinematics import (
    DISPLAY_PROXY_SCOPE,
    EPISODE_KEYS,
    FrozenC2Kinematics3A,
    HOLDOUT_EPISODE_KEYS,
    JACOBIAN_COLUMNS,
    POINT_NAMES,
    SEGMENTS,
    FutureFusionSlots,
    UnknownStateSlot,
    load_frozen_c2_3a,
    load_frozen_c2_hxx_diagnostics,
    sha256_file,
    verify_frozen_c2,
)
from biospur_fusion.c2_3a_kinematics.provenance import (
    FORMAL_MANIFEST,
    FORMAL_SEAL,
    PRIMARY_TRAJECTORY,
    WORKSPACE,
)
from biospur_fusion.c2_coupled_progressive.contracts import load_effective_config
from biospur_fusion.c2_coupled_progressive.renderer import (
    display_models,
    joints_for_frame,
)


@pytest.fixture(scope="module")
def kinematics():
    return load_frozen_c2_3a()


@pytest.fixture(scope="module")
def renderer_inputs(kinematics):
    trajectory = {
        "trajectory": {
            episode: {
                segment: {
                    "time_root_s": kinematics.series(episode, segment).time_root_s,
                    "quat_world_segment_wxyz": kinematics.series(
                        episode, segment
                    ).quat_world_segment_wxyz,
                    "mask": kinematics.series(episode, segment).mask,
                }
                for segment in SEGMENTS
            }
            for episode in EPISODE_KEYS
        },
        "output_coordinate_convention": {
            "schema": "biospur-c2-capture-wide-output-coordinates-v1",
            "matrix_world_output_from_internal": (
                kinematics.output_matrix_world_display_from_internal
            ),
        },
    }
    config = load_effective_config()
    model = display_models(config)[1]
    assert model.name == "middle_proxy"
    return trajectory, config, model


@pytest.fixture(scope="module")
def holdout_diagnostics():
    return load_frozen_c2_hxx_diagnostics()


def test_formal_freeze_gate_and_trajectory_remain_unchanged(kinematics) -> None:
    before = {
        "seal": sha256_file(WORKSPACE / FORMAL_SEAL),
        "manifest": sha256_file(WORKSPACE / FORMAL_MANIFEST),
        "trajectory": sha256_file(WORKSPACE / PRIMARY_TRAJECTORY),
    }
    assert before == {
        "seal": "f41317208851eb3b0037b1463dd45aa3935d258161756f9549203603ef885534",
        "manifest": "e4edfa682daa6c3002212d8c3a8e0992e0c4cb562938434d4d2f75ad43f87acb",
        "trajectory": "0f3ce2f9765508829de66d6681d17b433af6b54167c83619d9ddaffa13fbccdd",
    }
    for episode in EPISODE_KEYS:
        valid = np.flatnonzero(kinematics.episodes[episode].valid_frame_mask)
        kinematics.forward_kinematics(episode, int(valid[0]))
        kinematics.point_jacobian(episode, int(valid[-1]), "wrist_right")
    after_verification = verify_frozen_c2()
    after = {
        "seal": after_verification.seal_sha256,
        "manifest": after_verification.manifest_sha256,
        "trajectory": sha256_file(WORKSPACE / PRIMARY_TRAJECTORY),
    }
    assert after == before
    assert after_verification.bound_file_count == 132
    assert after_verification.bound_total_bytes == 1_261_349_621


def test_every_exposed_quaternion_is_exact_and_read_only(kinematics) -> None:
    with np.load(WORKSPACE / PRIMARY_TRAJECTORY, allow_pickle=False) as archive:
        for episode in EPISODE_KEYS:
            for segment in SEGMENTS:
                exposed = kinematics.series(
                    episode, segment
                ).quat_world_segment_wxyz
                frozen = archive[
                    f"trajectory/{episode}/{segment}/quat_world_segment_wxyz"
                ]
                assert np.array_equal(exposed, frozen, equal_nan=True)
                assert not exposed.flags.writeable
                assert kinematics.series(
                    episode, segment
                ).orientation_covariance_rad2 is None
                with pytest.raises(ValueError):
                    exposed[0, 0] = exposed[0, 0]


def test_complete_topology_frames_axes_and_unknowns_are_explicit(kinematics) -> None:
    assert len(kinematics.node_to_segment) == 10
    assert tuple(kinematics.segment_frames) == SEGMENTS
    assert len(kinematics.joint_edges) == 9
    assert {edge.name for edge in kinematics.joint_edges if edge.joint_kind == "hinge"} == {
        "elbow_left",
        "elbow_right",
        "knee_left",
        "knee_right",
    }
    assert set(kinematics.hinge_axes) == {
        "elbow_left",
        "elbow_right",
        "knee_left",
        "knee_right",
    }
    for axis in kinematics.hinge_axes.values():
        assert axis.source_license == "LicenseRef-Unspecified"
        assert axis.covariance_rad2 is None
        assert not axis.rerun_or_reimplementation_allowed
        assert np.isclose(np.linalg.norm(axis.parent_axis_reset_segment), 1.0)
        assert np.isclose(np.linalg.norm(axis.child_axis_reset_segment), 1.0)
        assert not axis.parent_axis_reset_segment.flags.writeable
        assert not axis.child_axis_reset_segment.flags.writeable

    assert not kinematics.geometry.physical_joint_centre_geometry
    assert not kinematics.geometry.uwb_antenna_prediction_geometry
    assert kinematics.geometry.scope == DISPLAY_PROXY_SCOPE
    assert not kinematics.future_slots.numerical_consumer_in_3a
    assert len(kinematics.future_slots.antenna_phase_centres) == 10
    for slot in (
        *kinematics.future_slots.antenna_phase_centres.values(),
        kinematics.future_slots.body_occlusion_geometry,
        kinematics.future_slots.world_from_frozen_root,
        kinematics.future_slots.root_translation_velocity_drift,
    ):
        assert slot.classification in {"MEASURE", "SOFT_PRIOR", "UNOBSERVABLE"}
        assert slot.value is None
        assert slot.covariance is None
        assert slot.provenance is None


def test_h01_h02_are_separate_exact_no_refit_diagnostics(
    kinematics, holdout_diagnostics
) -> None:
    assert tuple(holdout_diagnostics.episodes) == HOLDOUT_EPISODE_KEYS
    assert not set(holdout_diagnostics.episodes) & set(kinematics.episodes)
    assert not holdout_diagnostics.calibration_refit_on_hxx
    assert not holdout_diagnostics.holdout_action_semantics_used_for_fit
    assert not holdout_diagnostics.viewer_ik_rebase_retarget_or_repair
    assert not holdout_diagnostics.scientific_pass
    assert np.array_equal(
        holdout_diagnostics.output_matrix_world_display_from_internal,
        kinematics.output_matrix_world_display_from_internal,
    )
    expected_counts = {"H01_boxing": 601, "H02_golf": 600}
    holdout_path = (
        WORKSPACE
        / "logs/c2_hxx_frozen_replay_20260831_220900/"
        "HXX_FROZEN_C2_REPLAY_TRAJECTORY.npz"
    )
    with np.load(holdout_path, allow_pickle=False) as archive:
        for episode in HOLDOUT_EPISODE_KEYS:
            assert holdout_diagnostics.episodes[episode].frame_count == expected_counts[
                episode
            ]
            for segment in SEGMENTS:
                series = holdout_diagnostics.series(episode, segment)
                assert np.array_equal(
                    series.quat_world_segment_wxyz,
                    archive[
                        f"trajectory/{episode}/{segment}/quat_world_segment_wxyz"
                    ],
                )
                assert np.array_equal(
                    series.time_root_s,
                    archive[f"trajectory/{episode}/{segment}/time_root_s"],
                )
                assert np.array_equal(
                    series.mask,
                    archive[f"trajectory/{episode}/{segment}/mask"],
                )
                assert not series.quat_world_segment_wxyz.flags.writeable
                assert series.orientation_covariance_rad2 is None


def test_hxx_uwb_proxy_uses_same_named_joint_points_as_calibration(
    kinematics, holdout_diagnostics
) -> None:
    from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
        FrozenHoldoutBodyProxy,
        NODE_TO_PROXY_POINT,
    )

    adapter = FrozenHoldoutBodyProxy.create(holdout_diagnostics, kinematics)
    alignment = np.eye(3)
    offsets, normals, frame = adapter.at_fraction(
        "H01_boxing", 0.5, alignment
    )
    expected = joints_for_frame(
        adapter.trajectory,
        "H01_boxing",
        frame,
        adapter.model,
        adapter.config,
        apply_output_coordinates=False,
    )
    pelvis = expected["pelvis_center"]
    assert set(offsets) == set(NODE_TO_PROXY_POINT)
    assert set(normals) == set(NODE_TO_PROXY_POINT)
    for node, point in NODE_TO_PROXY_POINT.items():
        assert np.array_equal(offsets[node], expected[point] - pelvis)
        assert np.isclose(np.linalg.norm(normals[node]), 1.0)


def test_uwb_world_to_display_binding_composes_both_coordinate_frames() -> None:
    from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
        output_from_uwb_world,
    )

    output_from_internal = np.diag([-1.0, 1.0, 1.0])
    uwb_from_internal = np.array([
        [0.0, 1.0, 0.0],
        [-1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ])
    binding = output_from_uwb_world(
        output_from_internal, uwb_from_internal
    )
    internal = np.array([0.2, -0.4, 1.1])
    uwb = uwb_from_internal @ internal
    np.testing.assert_allclose(binding @ uwb, output_from_internal @ internal)
    assert not np.allclose(binding, np.diag([1.0, -1.0, 1.0]))


def test_all_19_episode_direct_fk_matches_frozen_renderer(
    kinematics, renderer_inputs
) -> None:
    trajectory, config, model = renderer_inputs
    maximum_error_m = 0.0
    compared_frames = 0
    for episode in EPISODE_KEYS:
        for frame in np.flatnonzero(kinematics.episodes[episode].valid_frame_mask):
            actual = kinematics.forward_kinematics(
                episode, int(frame), coordinates="display"
            )
            expected = joints_for_frame(
                trajectory,
                episode,
                int(frame),
                model,
                config,
                apply_output_coordinates=True,
            )
            compared_frames += 1
            for point in POINT_NAMES:
                error_m = float(np.max(np.abs(actual[point] - expected[point])))
                maximum_error_m = max(maximum_error_m, error_m)
                assert error_m <= 1e-12
    assert compared_frames == 13_320
    assert maximum_error_m <= 1e-12


def test_internal_and_display_fk_differ_only_by_frozen_reflection(
    kinematics,
) -> None:
    matrix = kinematics.output_matrix_world_display_from_internal
    assert np.isclose(np.linalg.det(matrix), -1.0, atol=1e-12)
    for episode in EPISODE_KEYS:
        frames = np.flatnonzero(kinematics.episodes[episode].valid_frame_mask)
        for frame in (int(frames[0]), int(frames[len(frames) // 2]), int(frames[-1])):
            internal = kinematics.forward_kinematics(
                episode, frame, coordinates="internal"
            )
            display = kinematics.forward_kinematics(
                episode, frame, coordinates="display"
            )
            for point in POINT_NAMES:
                assert np.allclose(
                    display[point], matrix @ internal[point], rtol=0.0, atol=1e-15
                )


def _single_frame_renderer_trajectory(kinematics, episode: str, frame: int) -> dict:
    return {
        "trajectory": {
            episode: {
                segment: {
                    "quat_world_segment_wxyz": np.array(
                        [kinematics.series(
                            episode, segment
                        ).quat_world_segment_wxyz[frame]],
                        dtype=float,
                    )
                }
                for segment in SEGMENTS
            }
        },
        "output_coordinate_convention": {
            "schema": "biospur-c2-capture-wide-output-coordinates-v1",
            "matrix_world_output_from_internal": (
                kinematics.output_matrix_world_display_from_internal
            ),
        },
    }


def test_display_proxy_jacobians_match_central_finite_differences(
    kinematics, renderer_inputs
) -> None:
    _, config, model = renderer_inputs
    # These finite-difference steps and bounds are test-only engineering
    # tolerances. They are not fusion covariance, sensor noise, or body-model
    # constants. The four halvings demonstrate the expected O(h^2) central-FD
    # convergence before floating-point cancellation dominates.
    steps = (2e-3, 1e-3, 5e-4, 2.5e-4)
    maximum_error_by_step: list[float] = []
    cases = (("00", 0), ("09", 350), ("18", 700))
    for step in steps:
        maximum_error = 0.0
        for episode, frame in cases:
            base = _single_frame_renderer_trajectory(kinematics, episode, frame)
            analytical = {
                point: kinematics.point_jacobian(
                    episode, frame, point, coordinates="display"
                ).dense
                for point in POINT_NAMES
            }
            for segment_index, segment in enumerate(SEGMENTS):
                source = base["trajectory"][episode][segment][
                    "quat_world_segment_wxyz"
                ][0]
                rotation = Rotation.from_quat(source[[1, 2, 3, 0]])
                for axis in range(3):
                    delta = np.zeros(3)
                    delta[axis] = step
                    plus = rotation * Rotation.from_rotvec(delta)
                    minus = rotation * Rotation.from_rotvec(-delta)
                    plus_q = plus.as_quat()[[3, 0, 1, 2]]
                    minus_q = minus.as_quat()[[3, 0, 1, 2]]
                    base["trajectory"][episode][segment][
                        "quat_world_segment_wxyz"
                    ][0] = plus_q
                    plus_points = joints_for_frame(
                        base,
                        episode,
                        0,
                        model,
                        config,
                        apply_output_coordinates=True,
                    )
                    base["trajectory"][episode][segment][
                        "quat_world_segment_wxyz"
                    ][0] = minus_q
                    minus_points = joints_for_frame(
                        base,
                        episode,
                        0,
                        model,
                        config,
                        apply_output_coordinates=True,
                    )
                    base["trajectory"][episode][segment][
                        "quat_world_segment_wxyz"
                    ][0] = source
                    column = 3 * (1 + segment_index) + axis
                    for point in POINT_NAMES:
                        finite_difference = (
                            plus_points[point] - minus_points[point]
                        ) / (2.0 * step)
                        error = float(
                            np.max(
                                np.abs(
                                    finite_difference
                                    - analytical[point][:, column]
                                )
                            )
                        )
                        maximum_error = max(maximum_error, error)
        maximum_error_by_step.append(maximum_error)
    assert all(
        finer <= 0.30 * coarser
        for coarser, finer in zip(
            maximum_error_by_step, maximum_error_by_step[1:]
        )
    )
    assert maximum_error_by_step[-1] <= 3e-8
    root_expected = kinematics.output_matrix_world_display_from_internal
    for point in POINT_NAMES:
        assert np.array_equal(
            kinematics.point_jacobian("00", 0, point).dense[:, :3],
            root_expected,
        )
        assert kinematics.point_jacobian("00", 0, point).columns == JACOBIAN_COLUMNS
        assert not kinematics.point_jacobian(
            "00", 0, point
        ).physical_or_uwb_prediction_allowed


def test_action_metadata_and_inert_future_slots_cannot_change_pose(
    kinematics,
) -> None:
    action_description = {"08": "arbitrary renamed UI annotation"}
    action_description["08"] = "another label with no numerical owner"
    explicit_slots = FutureFusionSlots.unset(kinematics.node_to_segment)
    supplied = load_frozen_c2_3a(future_slots=explicit_slots)
    supplied.future_slots.assert_compatible(tuple(kinematics.node_to_segment))
    for episode in EPISODE_KEYS:
        for segment in SEGMENTS:
            assert np.array_equal(
                supplied.series(episode, segment).quat_world_segment_wxyz,
                kinematics.series(episode, segment).quat_world_segment_wxyz,
            )
        for frame in np.flatnonzero(kinematics.episodes[episode].valid_frame_mask):
            baseline_fk = kinematics.forward_kinematics(episode, int(frame))
            supplied_fk = supplied.forward_kinematics(episode, int(frame))
            for point in POINT_NAMES:
                assert np.array_equal(supplied_fk[point], baseline_fk[point])

    invalid = dict(explicit_slots.antenna_phase_centres)
    first_node = next(iter(invalid))
    invalid[first_node] = UnknownStateSlot(
        state_id="U01",
        name="invented_zero",
        classification="MEASURE",
        owner="none",
        validation="forbidden test input",
        value=np.zeros(3),  # type: ignore[arg-type]
        covariance=np.eye(3),  # type: ignore[arg-type]
        provenance="invented",  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="only absent future slots"):
        replace(explicit_slots, antenna_phase_centres=invalid).assert_compatible(
            tuple(kinematics.node_to_segment)
        )


def test_3a_api_and_dependency_surface_contains_no_solver_or_calibration_import() -> None:
    package = WORKSPACE / "src/biospur_fusion/c2_3a_kinematics"
    forbidden_import_roots = {
        "qmt",
        "biospur_fusion.uwb",
        "biospur_fusion.c2_coupled_progressive.pose_reset_avatar",
        "biospur_fusion.c2_coupled_progressive.holdout_replay",
        "scipy.optimize",
    }
    observed_imports: set[str] = set()
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                observed_imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                observed_imports.add(node.module)
    assert not {
        name
        for name in observed_imports
        if any(name == root or name.startswith(f"{root}.") for root in forbidden_import_roots)
    }

    public_parameters = {
        *inspect.signature(load_frozen_c2_3a).parameters,
        *inspect.signature(FrozenC2Kinematics3A.forward_kinematics).parameters,
        *inspect.signature(FrozenC2Kinematics3A.point_jacobian).parameters,
    }
    assert "action_name" not in public_parameters
    assert "range" not in public_parameters
    assert "uwb" not in public_parameters
