from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.v0.c2_progressive.viewer_proxy import (
    assess_fixed_landmark_proxy_trajectory,
    bind_joint_support_factor_identifiers,
    candidate_rotation_tangent_second_moment_rad2,
    circular_frechet_medoid_display_summary,
    deterministic_joint_conditional_quantile_support,
    deterministic_joint_posterior_quantile_support,
    joint_physical_legal_frechet_medoid_display_summary,
    reexpress_world_segment_with_candidate_frame,
    viewer_graphical_spine_proxy_vector,
)


def test_circular_display_medoid_is_not_posterior_map_and_retains_support() -> None:
    grid = np.linspace(-np.pi, np.pi, 8, endpoint=False)
    # One isolated cell is the MAP, while three lower individual weights form
    # the minimum expected circular-distance cluster.
    weights = np.asarray([0.29, 0.01, 0.01, 0.23, 0.23, 0.22, 0.005, 0.005])
    weights /= np.sum(weights)
    result = circular_frechet_medoid_display_summary(
        delta_grid_rad=grid,
        posterior_weights=weights,
        owner_binding_sha256="owner-binding",
    )
    assert result.grid_index != int(np.argmax(weights))
    assert result.report["all_support_cells_retained"]
    assert not result.report["representative_is_posterior_map_or_argmax"]
    assert result.tangent_second_moment_rad2 > 0.0


def test_graphical_spine_surface_scale_is_not_constrained_to_torso_z() -> None:
    pelvis = Rotation.from_euler("y", 50.0, degrees=True).as_matrix()
    torso = Rotation.from_euler("x", -35.0, degrees=True).as_matrix()
    vector, report = viewer_graphical_spine_proxy_vector(
        world_from_pelvis_segment=pelvis,
        world_from_torso_segment=torso,
        graphical_display_scale_m=0.420,
        mapping_authority={
            "owner": "PREREGISTERED_VIEWER_ONLY_GRAPHICAL_SPINE_MAPPING",
            "surface_measurements_are_internal_truth": False,
            "surface_scalar_is_3d_vector": False,
            "surface_scalar_constrained_to_torso_plus_z": False,
            "uncertainty_or_sensitivity": "TWO_DIRECTION_AND_TWO_SCALE_SUPPORT",
            "authority_sha256": "authority-binding",
        },
    )
    assert np.isclose(np.linalg.norm(vector), 0.420)
    assert not np.allclose(vector / np.linalg.norm(vector), torso[:, 2])
    assert not np.allclose(vector / np.linalg.norm(vector), pelvis[:, 2])
    assert report["surface_scalar_is_3d_vector"] is False
    assert report["surface_scalar_constrained_to_torso_plus_z"] is False
    covariance = np.asarray(report["direction_tangent_second_moment_rad2"])
    assert np.trace(covariance) > 0.0


def test_distal_candidate_second_moment_is_actual_tangent_rad2() -> None:
    angles = np.linspace(-np.pi, np.pi, 12, endpoint=False)
    candidates = Rotation.from_rotvec(
        np.column_stack((np.zeros(len(angles)), angles, np.zeros(len(angles))))
    ).as_matrix()
    weights = np.exp(0.25 * np.cos(angles))
    weights /= np.sum(weights)
    summary = circular_frechet_medoid_display_summary(
        delta_grid_rad=angles,
        posterior_weights=weights,
        owner_binding_sha256="distal-owner-binding",
    )
    covariance, report = candidate_rotation_tangent_second_moment_rad2(
        candidate_sensor_from_segment=candidates,
        posterior_weights=weights,
        representative_index=summary.grid_index,
    )
    assert np.trace(covariance) > 0.0
    assert np.min(np.linalg.eigvalsh(covariance)) >= -1e-12
    assert report["raw_fraction_squared_relabelled_as_tangent_rad2"] is False
    assert report["full_distribution_retained"]


def test_candidate_frame_reexpression_preserves_world_sensor_motion() -> None:
    world_segment = Rotation.from_euler(
        "zyx", [[10.0, 20.0, 30.0], [35.0, -15.0, 5.0]], degrees=True,
    ).as_matrix()
    old_segment_from_sensor = Rotation.from_euler(
        "xyz", [15.0, 5.0, -25.0], degrees=True,
    ).as_matrix()
    candidate_sensor_from_segment = Rotation.from_euler(
        "xyz", [-30.0, 20.0, 12.0], degrees=True,
    ).as_matrix()
    expected_world_sensor = np.einsum(
        "nij,jk->nik", world_segment, old_segment_from_sensor,
    )
    result = reexpress_world_segment_with_candidate_frame(
        world_from_segment=world_segment,
        old_segment_from_sensor=old_segment_from_sensor,
        candidate_sensor_from_segment=candidate_sensor_from_segment,
    )
    observed_world_sensor = np.einsum(
        "nij,jk->nik", result, candidate_sensor_from_segment.T,
    )
    assert np.allclose(observed_world_sensor, expected_world_sensor, atol=1e-12)


def test_joint_support_is_deterministic_product_quantile_not_marginal_choice() -> None:
    factors = {
        "hinge_branch": np.array([0.7, 0.3]),
        "shoulder_left": np.array([0.1, 0.2, 0.7]),
        "forearm_left_s1": np.array([0.25, 0.25, 0.25, 0.25]),
    }
    first = deterministic_joint_posterior_quantile_support(
        posterior_weights_by_factor=factors,
        sample_power=4,
        owner_binding_sha256="owner",
    )
    second = deterministic_joint_posterior_quantile_support(
        posterior_weights_by_factor=factors,
        sample_power=4,
        owner_binding_sha256="owner",
    )
    assert first.factor_names == tuple(factors)
    assert first.support_indices.shape == (16, 3)
    assert np.array_equal(first.support_indices, second.support_indices)
    assert first.report["marginal_representatives_chosen_before_physical_gate"] is False


def test_joint_representative_can_only_select_a_pre_gated_legal_body() -> None:
    loss = np.array([
        [0.0, 1.0, 1.0],
        [1.0, 0.0, 4.0],
        [1.0, 4.0, 0.0],
    ])
    result = joint_physical_legal_frechet_medoid_display_summary(
        pairwise_squared_geodesic_loss_rad2=loss,
        physical_legal_mask=np.array([False, True, True]),
        physical_gate_binding_sha256="gate",
        owner_binding_sha256="owner",
    )
    assert result.support_row == 1
    assert result.report["physical_gate_applied_before_representative_selection"] is True
    assert result.report["minimum_loss_tie_count"] == 2
    assert result.report["minimum_loss_tie_break_implies_unique_physical_solution"] is False


def test_joint_conditional_support_uses_selected_branch_factor_law() -> None:
    support = deterministic_joint_conditional_quantile_support(
        branch_weights=np.array([0.5, 0.5]),
        conditional_weights_by_factor={
            "distal": np.array([
                [1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0],
            ]),
        },
        sample_power=3,
        owner_binding_sha256="conditional-owner",
    )
    for branch_index, distal_index in support.support_indices:
        assert int(distal_index) == (0 if int(branch_index) == 0 else 2)
    assert support.report[
        "branch_conditional_support_replaced_by_independent_marginal"
    ] is False


def test_factor_identifier_bijection_is_permutation_safe_and_rejects_duplicates() -> None:
    binding = bind_joint_support_factor_identifiers(
        authority_factor_names=("prefix15_hinge_branch", "hip_left", "shank_left"),
        produced_factor_names=("shank_left", "hinge_branch", "hip_left"),
        produced_to_authority_alias={"hinge_branch": "prefix15_hinge_branch"},
    )
    assert binding.produced_column_by_authority.tolist() == [1, 2, 0]
    produced = np.array([[30, 10, 20]])
    assert produced[:, binding.produced_column_by_authority].tolist() == [[10, 20, 30]]
    assert binding.report["column_order_chosen_by_result_or_weight"] is False
    with np.testing.assert_raises(ValueError):
        bind_joint_support_factor_identifiers(
            authority_factor_names=("a", "a"),
            produced_factor_names=("a", "b"),
            produced_to_authority_alias={},
        )


def _viewer_gate_fixture(*, crossed: bool = False, spine_down: bool = False):
    rows = 5
    left = -0.2 if crossed else 0.2
    z = -0.4 if spine_down else 0.4
    positions = {
        "hip_mid_landmark_proxy": np.zeros((rows, 3)),
        "shoulder_mid_landmark_proxy": np.tile([0.0, 0.0, z], (rows, 1)),
    }
    for label in ("elbow", "wrist", "knee", "ankle"):
        positions[f"{label}_left_landmark_proxy"] = np.tile([0.0, left, -0.2], (rows, 1))
        positions[f"{label}_right_landmark_proxy"] = np.tile([0.0, -left, -0.2], (rows, 1))
    settings = {
        "bilateral_crossing": {
            "gross_crossing_margin_m": 0.03,
            "gross_sustained_evidence_fraction": 0.6,
        },
        "gross_gravity_wrong_hemisphere_margin_rad": np.deg2rad(10.0),
        "gravity_evidence": {"gross_sustained_evidence_fraction": 0.6},
    }
    direction = {
        name: 1.0 for name in (
            "upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right",
        )
    }
    return positions, np.tile(np.eye(3), (rows, 1, 1)), direction, settings


def test_viewer_gate_checks_the_rendered_graph_not_sensor_fk_nodes() -> None:
    positions, pelvis, direction, settings = _viewer_gate_fixture()
    legal = assess_fixed_landmark_proxy_trajectory(
        landmark_positions_m=positions,
        world_from_pelvis_segment=pelvis,
        proximal_functional_direction_dot_viewer_minus_z=direction,
        physical_settings=settings,
    )
    assert legal.display_legal
    positions, pelvis, direction, settings = _viewer_gate_fixture(crossed=True)
    crossed = assess_fixed_landmark_proxy_trajectory(
        landmark_positions_m=positions,
        world_from_pelvis_segment=pelvis,
        proximal_functional_direction_dot_viewer_minus_z=direction,
        physical_settings=settings,
    )
    assert not crossed.display_legal
    assert any("WRIST_CROSSING" in value for value in crossed.report["hard_rejection_codes"])


def test_viewer_gate_rejects_wrong_spine_and_proximal_direction_hemispheres() -> None:
    positions, pelvis, direction, settings = _viewer_gate_fixture(spine_down=True)
    direction["thigh_right"] = -1.0
    result = assess_fixed_landmark_proxy_trajectory(
        landmark_positions_m=positions,
        world_from_pelvis_segment=pelvis,
        proximal_functional_direction_dot_viewer_minus_z=direction,
        physical_settings=settings,
    )
    assert not result.display_legal
    assert "VIEWER_GRAPHICAL_SPINE_WRONG_GRAVITY_HEMISPHERE" in result.report[
        "hard_rejection_codes"
    ]
    assert "VIEWER_THIGH_RIGHT_LONGITUDINAL_HEMISPHERE_REVERSED" in result.report[
        "hard_rejection_codes"
    ]
