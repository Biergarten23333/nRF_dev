from __future__ import annotations

import hashlib

import numpy as np
import pytest

from biospur_fusion.v0.c2_progressive.distal_longitudinal import (
    CompleteS1DistalLongitudinalOwner,
    complete_s1_sensor_from_segment_candidates,
    complete_s1_prior_binding,
    recompute_distal_motion_through_candidate_coordinates,
)


def _sha(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return hashlib.sha256(array.view(np.uint8)).hexdigest()


def _owner() -> tuple[CompleteS1DistalLongitudinalOwner, np.ndarray, np.ndarray]:
    grid = np.linspace(-np.pi, np.pi, 360, endpoint=False)
    prior = np.exp(0.35 * np.cos(grid - 0.4))
    prior /= np.sum(prior)
    owner = CompleteS1DistalLongitudinalOwner(
        segment="shank_left",
        delta_grid_rad=grid,
        registered_prior_weights=prior,
        prior_binding=complete_s1_prior_binding(
            segment="shank_left",
            delta_grid_rad=grid,
            registered_prior_weights=prior,
            source_authority_sha256="1" * 64,
        ),
    )
    return owner, grid, owner.posterior().prior_weights


def _process(
    owner: CompleteS1DistalLongitudinalOwner,
    *,
    index: int,
    residual: np.ndarray,
    angular: np.ndarray,
) -> None:
    owner.process_gauge_invariant_action(
        chronological_index=index,
        action=f"action_{index:02d}",
        joint_acceleration_residual_world_mps2=residual,
        relative_angular_velocity_perpendicular_rads=angular,
        owner_input_binding={
            "schema": "biospur-c2-distal-longitudinal-complete-motion-input-v1",
            "segment": "shank_left",
            "chronological_index": index,
            "action": f"action_{index:02d}",
            "joint_acceleration_residual_world_mps2_sha256": _sha(residual),
            "relative_angular_velocity_perpendicular_rads_sha256": _sha(angular),
            "action_pose_truth_used": False,
            "pixel_or_manual_axis_selection_used": False,
        },
    )


def test_complete_s1_motion_gauge_does_not_create_false_distal_axis_information() -> None:
    owner, grid, prior = _owner()
    time = np.linspace(0.0, 2.0, 401)
    residual = np.column_stack((
        0.2 * np.sin(2.0 * time),
        0.1 * np.cos(3.0 * time),
        0.15 * np.sin(5.0 * time),
    ))
    angular = np.abs(0.3 * np.sin(4.0 * time))
    _process(owner, index=0, residual=residual, angular=angular)
    posterior = owner.posterior()
    assert len(posterior.delta_grid_rad) == 360
    assert np.array_equal(posterior.delta_grid_rad, grid)
    assert np.array_equal(posterior.posterior_weights, prior)
    assert posterior.report["motion_information_gain_nats"] == 0.0
    assert posterior.report["complete_motion_likelihood_evaluated"]
    assert not posterior.report["complete_motion_identifies_longitudinal_gauge"]
    assert not posterior.report["hard_argmax_or_map_exposed"]
    assert not posterior.report["tuned_distal_endpoint_pose_authorized"]


def test_repeated_complete_motion_does_not_multiply_shared_gauge_into_concentration() -> None:
    owner, _, prior = _owner()
    residual = np.tile(np.array([[0.4, -0.2, 0.1]]), (64, 1))
    angular = np.full(64, 0.25)
    _process(owner, index=0, residual=residual, angular=angular)
    _process(owner, index=1, residual=residual, angular=angular)
    posterior = owner.posterior()
    assert np.array_equal(posterior.posterior_weights, prior)
    assert posterior.action_count == 2
    assert all(
        row["candidate_dependent_motion_information_gain_nats"] == 0.0
        for row in posterior.report["action_audits"]
    )


def test_complete_s1_owner_rejects_incomplete_grid_and_nonchronological_input() -> None:
    grid = np.linspace(-1.0, 1.0, 16)
    prior = np.ones(16) / 16.0
    with pytest.raises(ValueError, match="complete S1"):
        CompleteS1DistalLongitudinalOwner(
            segment="forearm_left",
            delta_grid_rad=grid,
            registered_prior_weights=prior,
            prior_binding={
                "schema": "biospur-c2-distal-longitudinal-complete-s1-prior-binding-v1",
                "segment": "forearm_left",
                "delta_grid_rad_sha256": _sha(grid),
                "registered_prior_weights_sha256": _sha(prior),
                "source_authority_sha256": "2" * 64,
            },
        )

    owner, _, _ = _owner()
    residual = np.zeros((3, 3))
    angular = np.zeros(3)
    with pytest.raises(ValueError, match="exactly chronologically"):
        _process(owner, index=1, residual=residual, angular=angular)


def test_coordinate_mutation_recomputes_identical_complete_motion_residual() -> None:
    grid = np.linspace(-np.pi, np.pi, 360, endpoint=False)
    hinge = np.array([0.2, 0.9, -0.1])
    hinge /= np.linalg.norm(hinge)
    candidates = complete_s1_sensor_from_segment_candidates(
        signed_hinge_axis_sensor=hinge,
        delta_grid_rad=grid,
    )
    rows = 17
    angles = np.linspace(-0.4, 0.7, rows)
    world_sensor = np.asarray([
        np.array([
            [np.cos(value), -np.sin(value), 0.0],
            [np.sin(value), np.cos(value), 0.0],
            [0.0, 0.0, 1.0],
        ])
        for value in angles
    ])
    acc = np.column_stack((
        0.3 * np.sin(angles), 0.2 * np.cos(angles), 9.7 + 0.1 * np.sin(angles),
    ))
    omega = np.column_stack((angles, 0.4 - angles, 0.2 + angles**2))
    alpha = np.column_stack((np.ones(rows), -0.5 * np.ones(rows), 2.0 * angles))
    lever = np.array([0.03, 0.21, -0.05])
    joint, candidate_omega, candidate_axis = (
        recompute_distal_motion_through_candidate_coordinates(
            candidate_sensor_from_segment=candidates,
            world_from_sensor=world_sensor,
            accelerometer_sensor_mps2=acc,
            angular_velocity_sensor_rads=omega,
            angular_acceleration_sensor_rads2=alpha,
            sensor_to_joint_lever_m=lever,
            signed_hinge_axis_sensor=hinge,
        )
    )
    expected_joint_sensor = (
        acc + np.cross(alpha, lever) + np.cross(omega, np.cross(omega, lever))
    )
    expected_joint = np.einsum("nij,nj->ni", world_sensor, expected_joint_sensor)
    expected_omega = np.einsum("nij,nj->ni", world_sensor, omega)
    expected_axis = np.einsum("nij,j->ni", world_sensor, hinge)
    assert np.max(np.abs(joint - expected_joint[None, :, :])) < 1e-12
    assert np.max(np.abs(candidate_omega - expected_omega[None, :, :])) < 1e-12
    assert np.max(np.abs(candidate_axis - expected_axis[None, :, :])) < 1e-12
