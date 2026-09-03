from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.v0.c2_progressive.functional_geometry import (
    AlignedPair,
    _center_terms,
)
from biospur_fusion.v0.c2_progressive.nonhinge_heading import (
    NonhingeSharedNuisanceSufficientStatistics,
    PersistentNonhingeHeadingLikelihoodOwner,
    _SHARED_NUISANCE_PARAMETER_BASIS_SHA256,
    _candidate_independent_loewner_upper_envelope,
    _candidate_independent_psd_upper_envelope,
    _circular_convolve,
    _circular_transition_conditional_gaussian,
    _joint_linear_gaussian_nuisance_log_marginal,
    _rao_blackwellized_joint_heading_step,
    edge_local_joint_acceleration_heading_log_likelihood,
)
from biospur_fusion.v0.c2_progressive.segment_frames import EdgeConnectionVectors
from biospur_fusion.v0.c2_progressive.timebase import PairAlignment


def _qmt_wxyz(rotation: Rotation, count: int) -> np.ndarray:
    xyzw = rotation.as_quat()
    return np.tile(xyzw[[3, 0, 1, 2]], (count, 1))


def _fixture(*, yaw_deg: float, horizontal_motion: bool) -> tuple[
    AlignedPair, EdgeConnectionVectors, np.ndarray, np.ndarray,
]:
    count = 1200
    dt = 0.005
    t = np.arange(count, dtype=float) * dt
    if horizontal_motion:
        joint_world = np.column_stack((
            1.2 * np.sin(1.7 * t) + 0.3 * np.cos(4.1 * t),
            0.9 * np.cos(1.1 * t) + 0.2 * np.sin(3.3 * t),
            9.80665 + 0.4 * np.sin(0.7 * t),
        ))
    else:
        joint_world = np.tile(np.array((0.0, 0.0, 9.80665)), (count, 1))
    parent_world_sensor = Rotation.identity()
    child_physical_world_sensor = Rotation.from_euler("z", yaw_deg, degrees=True)
    # A 6-axis VQF has no absolute initial yaw observation.  The synthetic raw
    # vector retains the physical sensor yaw while the exported quaternion's
    # yaw gauge starts at zero; the edge-local owner must infer the difference.
    child_vqf_world_sensor = Rotation.identity()
    parent_lever = np.array((0.11, -0.035, 0.025))
    child_lever = np.array((-0.085, 0.045, 0.018))
    parent_gyro = np.column_stack((
        0.15 * np.sin(0.9 * t),
        0.12 * np.cos(1.3 * t),
        0.08 * np.sin(1.9 * t),
    ))
    child_gyro = np.column_stack((
        0.11 * np.cos(0.8 * t),
        -0.14 * np.sin(1.2 * t),
        0.09 * np.cos(1.6 * t),
    ))
    if not horizontal_motion:
        parent_gyro[:] = 0.0
        child_gyro[:] = 0.0
    spans = (slice(0, 600), slice(600, 1200))
    parent_acc = np.empty((count, 3), dtype=float)
    child_acc = np.empty((count, 3), dtype=float)
    for span in spans:
        parent_terms, _, _ = _center_terms(
            parent_gyro[span], dt=dt, window=21, polynomial=3,
        )
        child_terms, _, _ = _center_terms(
            child_gyro[span], dt=dt, window=21, polynomial=3,
        )
        parent_sensor_joint = parent_world_sensor.inv().apply(joint_world[span])
        child_sensor_joint = child_physical_world_sensor.inv().apply(
            joint_world[span]
        )
        parent_acc[span] = parent_sensor_joint - np.einsum(
            "nij,j->ni", parent_terms, parent_lever,
        )
        child_acc[span] = child_sensor_joint - np.einsum(
            "nij,j->ni", child_terms, child_lever,
        )
    time = np.arange(count, dtype=float) * dt
    time[600:] += 0.75
    indices = np.arange(count, dtype=np.int64)
    pair = AlignedPair(
        edge="shoulder_left",
        action="05_shoulder_left",
        parent_acc=parent_acc,
        child_acc=child_acc,
        parent_gyro=parent_gyro,
        child_gyro=child_gyro,
        parent_observed_time_s=time,
        child_observed_time_s=time + 0.001,
        parent_boot_epoch=np.zeros(count, dtype=np.int64),
        child_boot_epoch=np.zeros(count, dtype=np.int64),
        alignment=PairAlignment(
            parent_indices=indices,
            child_indices=indices,
            lag_samples=0,
            report={"lag_uncertainty_s": 0.001},
        ),
        contiguous_spans=spans,
        provenance={"owner": "SYNTHETIC_OWNER_BOUND_NONHINGE_FIXTURE"},
    )
    connection = EdgeConnectionVectors(
        edge="shoulder_left",
        parent="torso",
        child="upper_arm_left",
        parent_sensor_to_joint_m=parent_lever,
        child_sensor_to_joint_m=child_lever,
        covariance_m2=np.eye(6) * 1e-6,
    )
    return (
        pair,
        connection,
        _qmt_wxyz(parent_world_sensor, count),
        _qmt_wxyz(child_vqf_world_sensor, count),
    )


def _likelihood(
    pair: AlignedPair,
    connection: EdgeConnectionVectors,
    parent_quaternion: np.ndarray,
    child_quaternion: np.ndarray,
    *,
    gap_orientation_variance_rad2: float = 0.0,
    coherent_calibration_scale: float = 0.0,
) -> tuple[np.ndarray, dict, NonhingeSharedNuisanceSufficientStatistics]:
    calibration_covariance = np.eye(24, dtype=float) * 1e-10
    calibration_covariance[0:3, 0:3] = (
        np.eye(3) * coherent_calibration_scale**2
    )
    calibration_covariance[3:6, 3:6] = (
        np.eye(3) * (0.05 * coherent_calibration_scale) ** 2
    )
    calibration_covariance[6:15, 6:15] = (
        np.eye(9) * (0.1 * coherent_calibration_scale) ** 2
    )
    calibration_covariance[15:24, 15:24] = (
        np.eye(9) * (0.01 * coherent_calibration_scale) ** 2
    )
    calibration_covariance += np.eye(24, dtype=float) * 1e-10
    gap_covariance = np.tile(
        np.eye(3) * gap_orientation_variance_rad2,
        (len(pair.parent_acc), 1, 1),
    )
    return edge_local_joint_acceleration_heading_log_likelihood(
        pair=pair,
        connection=connection,
        parent_quaternion_world_sensor_wxyz=parent_quaternion,
        child_quaternion_world_sensor_wxyz=child_quaternion,
        delta_grid_rad=np.deg2rad(np.arange(360, dtype=float)),
        sample_period_s=0.005,
        savgol_window_samples=21,
        savgol_polynomial=3,
        estimation_rate_hz=1.0,
        effective_epoch_cap=16,
        parent_accelerometer_covariance_m2_s4=np.eye(3) * 0.01,
        child_accelerometer_covariance_m2_s4=np.eye(3) * 0.01,
        parent_gyroscope_covariance_rad2_s2=np.eye(3) * 1e-5,
        child_gyroscope_covariance_rad2_s2=np.eye(3) * 1e-5,
        parent_gap_orientation_covariance_rad2=gap_covariance,
        child_gap_orientation_covariance_rad2=gap_covariance,
        parent_calibration_parameter_covariance=calibration_covariance,
        child_calibration_parameter_covariance=calibration_covariance,
        noise_sigma_multiplier=3.0,
    )


def test_full_r3_joint_acceleration_likelihood_recovers_yaw_without_hard_lock() -> None:
    pair, connection, parent_quaternion, child_quaternion = _fixture(
        yaw_deg=60.0, horizontal_motion=True,
    )
    log_likelihood, report, statistics = _likelihood(
        pair, connection, parent_quaternion, child_quaternion,
    )
    grid = np.deg2rad(np.arange(360, dtype=float))
    assert int(np.argmax(log_likelihood)) == 60
    assert report["effective_epoch_count"] == 6
    assert report["full_r3_center_levers_used"] is True
    assert report["gap_orientation_covariance_propagated_per_row"] is True
    assert (
        report["calibration_common_mode_repeated_as_independent_epoch_noise"]
        is False
    )
    assert report["multimodal_full_grid_retained"] is True
    assert report["argmax_used_as_hard_heading_lock"] is False
    assert report["rom_pose_label_pixel_or_carried_zero_used"] is False

    owner = PersistentNonhingeHeadingLikelihoodOwner(
        branch_ids=("branch",),
        nonhinge_edges=("shoulder_left",),
        delta_grid_rad=grid,
        gap_diffusion_rad2_s=1e-4,
        unknown_interval_variance_floor_rad2=0.01,
    )
    result = owner.process(
        branch_id="branch",
        chronological_index=0,
        likelihood_log_weights=log_likelihood,
        pair=pair,
        likelihood_report=report,
        shared_nuisance_statistics=statistics,
    )
    assert np.isclose(np.sum(result.posterior_weights), 1.0)
    assert len(result.posterior_weights) == 360
    assert result.report["hard_heading_lock_created"] is False


def test_vertical_low_information_remains_uniform_and_hinge_edge_is_rejected() -> None:
    pair, connection, parent_quaternion, child_quaternion = _fixture(
        yaw_deg=115.0, horizontal_motion=False,
    )
    log_likelihood, report, _ = _likelihood(
        pair, connection, parent_quaternion, child_quaternion,
    )
    assert float(np.ptp(log_likelihood)) < 2e-5
    assert report["information_gain_from_uniform_nats"] < 1e-10

    with pytest.raises(ValueError, match="nonhinge edges only"):
        _likelihood(
            replace(pair, edge="elbow_left"),
            replace(connection, edge="elbow_left"),
            parent_quaternion,
            child_quaternion,
        )


def _posterior(log_likelihood: np.ndarray) -> np.ndarray:
    shifted = log_likelihood - np.max(log_likelihood)
    weights = np.exp(shifted)
    return weights / np.sum(weights)


def _entropy(weights: np.ndarray) -> float:
    return -float(np.sum(weights * np.log(weights)))


def test_increasing_gap_orientation_uncertainty_cannot_sharpen_s1() -> None:
    pair, connection, parent_quaternion, child_quaternion = _fixture(
        yaw_deg=60.0, horizontal_motion=True,
    )
    base_log, base_report, base_statistics = _likelihood(
        pair, connection, parent_quaternion, child_quaternion,
    )
    gap_log, gap_report, gap_statistics = _likelihood(
        pair, connection, parent_quaternion, child_quaternion,
        gap_orientation_variance_rad2=0.25,
    )
    assert np.isfinite(base_log).all() and np.isfinite(gap_log).all()
    base = _posterior(base_log)
    gap = _posterior(gap_log)
    assert _entropy(gap) >= _entropy(base) - 1e-12
    assert float(np.max(gap)) <= float(np.max(base)) + 1e-12
    assert (
        gap_report["information_gain_from_uniform_nats"]
        <= base_report["information_gain_from_uniform_nats"] + 1e-12
    )
    assert gap_report["candidate_dependent_covariance_used_in_score"] is True
    assert gap_report["candidate_covariance_logdet_included"] is True
    assert gap_report[
        "gap_and_clock_epistemic_covariance_can_create_heading_evidence"
    ] is False
    assert gap_report[
        "gap_and_clock_covariance_determinant_or_alignment_counted_as_functional_motion_information"
    ] is False
    assert gap_report["epistemic_gap_clock_envelope_eigen_trace_range"][
        "maximum_trace"
    ] > base_report["epistemic_gap_clock_envelope_eigen_trace_range"][
        "maximum_trace"
    ]
    assert gap_report["covariance_owner_chosen_by_resultant_or_visual_sharpness"] is False
    assert gap_report["argmax_used_as_hard_heading_lock"] is False

    grid = np.deg2rad(np.arange(360, dtype=float))
    owner_results = []
    for log_likelihood, report, statistics in (
        (base_log, base_report, base_statistics),
        (gap_log, gap_report, gap_statistics),
    ):
        owner = PersistentNonhingeHeadingLikelihoodOwner(
            branch_ids=("branch",),
            nonhinge_edges=("shoulder_left",),
            delta_grid_rad=grid,
            gap_diffusion_rad2_s=1e-4,
            unknown_interval_variance_floor_rad2=0.01,
        )
        owner_results.append(owner.process(
            branch_id="branch",
            chronological_index=0,
            likelihood_log_weights=log_likelihood,
            pair=pair,
            likelihood_report=report,
            shared_nuisance_statistics=statistics,
        ).posterior_weights)
    assert _entropy(owner_results[1]) >= _entropy(owner_results[0]) - 1e-12
    assert float(np.max(owner_results[1])) <= float(
        np.max(owner_results[0])
    ) + 1e-12


def test_no_motion_anisotropic_gap_uncertainty_cannot_create_heading() -> None:
    pair, connection, parent_quaternion, child_quaternion = _fixture(
        yaw_deg=115.0, horizontal_motion=False,
    )
    base_log, base_report, _ = _likelihood(
        pair, connection, parent_quaternion, child_quaternion,
    )
    gap_log, gap_report, _ = _likelihood(
        pair, connection, parent_quaternion, child_quaternion,
        gap_orientation_variance_rad2=0.25,
    )
    base = _posterior(base_log)
    gap = _posterior(gap_log)
    assert _entropy(gap) >= _entropy(base) - 1e-12
    assert gap_report["information_gain_from_uniform_nats"] < 1e-10
    assert float(np.ptp(gap_log)) < 2e-5
    assert gap_report[
        "gap_and_clock_epistemic_covariance_can_create_heading_evidence"
    ] is False
    active_zero = gap_report[
        "zero_residual_covariance_only_information_ablation"
    ]["ACTIVE_WHITE_PLUS_COMMON_EPISTEMIC_ENVELOPE_ZERO_RESIDUAL"]
    raw_zero = gap_report[
        "zero_residual_covariance_only_information_ablation"
    ]["RAW_HETEROSCEDASTIC_GAP_CLOCK_ZERO_RESIDUAL_DIAGNOSTIC_ONLY"]
    gap_only_zero = gap_report[
        "zero_residual_covariance_only_information_ablation"
    ]["RAW_WHITE_PLUS_HETEROSCEDASTIC_GAP_ZERO_RESIDUAL_DIAGNOSTIC_ONLY"]
    clock_only_zero = gap_report[
        "zero_residual_covariance_only_information_ablation"
    ]["RAW_WHITE_PLUS_HETEROSCEDASTIC_CLOCK_ZERO_RESIDUAL_DIAGNOSTIC_ONLY"]
    assert active_zero["information_gain_from_uniform_nats"] < 1e-10
    assert raw_zero["information_gain_from_uniform_nats"] >= 0.0
    assert gap_only_zero["information_gain_from_uniform_nats"] >= 0.0
    assert clock_only_zero["information_gain_from_uniform_nats"] >= 0.0
    assert gap_report[
        "zero_residual_covariance_only_information_used_for_physical_branch_selection_or_viewer_pose"
    ] is False


def test_increasing_capture_wide_calibration_uncertainty_cannot_sharpen_s1() -> None:
    pair, connection, parent_quaternion, child_quaternion = _fixture(
        yaw_deg=60.0, horizontal_motion=True,
    )
    base_log, base_report, base_statistics = _likelihood(
        pair, connection, parent_quaternion, child_quaternion,
    )
    common_log, common_report, common_statistics = _likelihood(
        pair, connection, parent_quaternion, child_quaternion,
        coherent_calibration_scale=0.5,
    )
    base = _posterior(base_log)
    common = _posterior(common_log)
    assert _entropy(common) >= _entropy(base) - 1e-12
    assert float(np.max(common)) <= float(np.max(base)) + 1e-12
    assert (
        common_report["information_gain_from_uniform_nats"]
        <= base_report["information_gain_from_uniform_nats"] + 1e-12
    )
    assert common_report["argmax_used_as_hard_heading_lock"] is False
    assert float(np.max(common_statistics.shared_covariance)) >= float(
        np.max(base_statistics.shared_covariance)
    )


def test_rotating_anisotropic_candidate_covariance_cannot_buy_likelihood() -> None:
    first = np.diag((9.0, 1.0, 0.25))
    rotation = Rotation.from_euler("z", 67.0, degrees=True).as_matrix()
    second = rotation @ first @ rotation.T
    envelope = _candidate_independent_psd_upper_envelope((first, second))
    assert np.allclose(envelope, np.eye(3) * 9.0, atol=1e-12, rtol=0.0)
    assert float(np.min(np.linalg.eigvalsh(envelope - first))) >= -1e-12
    assert float(np.min(np.linalg.eigvalsh(envelope - second))) >= -1e-12
    residual = np.array((1.0, -0.4, 0.2))
    first_score = float(residual @ np.linalg.solve(envelope, residual))
    second_score = float(residual @ np.linalg.solve(envelope, residual))
    assert first_score == second_score


def test_normalized_gaussian_score_is_rotation_equivariant() -> None:
    covariance = np.array(
        ((4.0, 0.8, 0.0), (0.8, 1.5, 0.2), (0.0, 0.2, 0.6)),
        dtype=float,
    )
    residual = np.array((1.2, -0.4, 0.7), dtype=float)
    rotation = Rotation.from_euler("zyx", (51.0, -18.0, 9.0), degrees=True).as_matrix()

    def normalized_log_score(value: np.ndarray, noise: np.ndarray) -> float:
        return -0.5 * (
            float(value @ np.linalg.solve(noise, value))
            + float(np.linalg.slogdet(noise)[1])
        )

    original = normalized_log_score(residual, covariance)
    rotated = normalized_log_score(
        rotation @ residual, rotation @ covariance @ rotation.T,
    )
    assert np.isclose(original, rotated, atol=1e-12, rtol=0.0)


def test_covariance_inflation_is_penalized_by_exact_logdet_at_zero_residual() -> None:
    residual = np.zeros(3, dtype=float)
    narrow = np.eye(3)
    broad = np.eye(3) * 9.0
    narrow_score = -0.5 * (
        float(residual @ np.linalg.solve(narrow, residual))
        + float(np.linalg.slogdet(narrow)[1])
    )
    broad_score = -0.5 * (
        float(residual @ np.linalg.solve(broad, residual))
        + float(np.linalg.slogdet(broad)[1])
    )
    assert broad_score < narrow_score


def test_loewner_envelope_is_permutation_invariant_and_dominates_grid() -> None:
    base = np.diag((0.4, 1.2, 2.5))
    candidates = tuple(
        Rotation.from_euler("z", angle, degrees=True).as_matrix()
        @ base
        @ Rotation.from_euler("z", angle, degrees=True).as_matrix().T
        for angle in (0.0, 31.0, 87.0, 143.0)
    )
    forward = _candidate_independent_loewner_upper_envelope(candidates)
    reverse = _candidate_independent_loewner_upper_envelope(candidates[::-1])
    assert np.allclose(forward, reverse, atol=1e-12, rtol=0.0)
    for candidate in candidates:
        assert float(np.min(np.linalg.eigvalsh(forward - candidate))) >= -1e-12


def _scalar_shared_bias_statistics(
    residual: np.ndarray,
    *,
    nuisance_jacobian: float,
    nuisance_variance: float,
    reference_mean: float = 0.0,
) -> NonhingeSharedNuisanceSufficientStatistics:
    values = np.asarray(residual, dtype=float)
    jacobian = float(nuisance_jacobian)
    return NonhingeSharedNuisanceSufficientStatistics(
        heading_information=np.ones(len(values), dtype=float),
        shared_score=np.full((len(values), 1), jacobian, dtype=float),
        shared_covariance=np.array(((nuisance_variance,),), dtype=float),
        nuisance_normal=np.full(
            (len(values), 1, 1), jacobian**2, dtype=float,
        ),
        nuisance_score=(jacobian * values)[:, None],
        residual_quadratic=values**2,
        independent_covariance_log_determinant=np.zeros(
            len(values), dtype=float,
        ),
        residual_dimension=1,
        shared_nuisance_reference_mean=np.array((reference_mean,), dtype=float),
        parameter_basis_sha256=_SHARED_NUISANCE_PARAMETER_BASIS_SHA256,
    )


def test_nonzero_reference_rao_step_matches_closed_form() -> None:
    residual = np.array((0.3, 1.0, -0.4), dtype=float)
    prior_mean = np.array((2.0,), dtype=float)
    prior_covariance = np.array(((0.5,),), dtype=float)
    reference = 1.0
    statistics = _scalar_shared_bias_statistics(
        residual,
        nuisance_jacobian=1.0,
        nuisance_variance=0.5,
        reference_mean=reference,
    )
    weights, _, _, report = _rao_blackwellized_joint_heading_step(
        prior_weights=np.full(3, 1.0 / 3.0),
        conditional_mean=np.tile(prior_mean, (3, 1)),
        conditional_covariance=np.tile(prior_covariance, (3, 1, 1)),
        previous_external_mean=prior_mean,
        previous_external_covariance=prior_covariance,
        current_external_mean=prior_mean,
        current_external_covariance=prior_covariance,
        statistics=statistics,
        transition_variance_rad2=0.0,
    )
    shifted = residual + prior_mean[0] - reference
    expected_log = -0.5 * (
        shifted**2 / (1.0 + prior_covariance[0, 0])
        + np.log1p(prior_covariance[0, 0])
    )
    assert np.allclose(weights, _posterior(expected_log), atol=1e-12, rtol=0.0)
    assert report["cross_action_reference_rebased_in_absolute_parameter_basis"] is True


def test_two_action_zero_diffusion_matches_exact_path_enumeration() -> None:
    first_residual = np.array((-0.4, 0.2, 0.8), dtype=float)
    second_residual = np.array((0.1, -0.3, 0.5), dtype=float)
    prior_mean = np.array((0.7,), dtype=float)
    prior_covariance = np.array(((0.25,),), dtype=float)
    first = _scalar_shared_bias_statistics(
        first_residual,
        nuisance_jacobian=1.0,
        nuisance_variance=0.25,
        reference_mean=0.2,
    )
    second = _scalar_shared_bias_statistics(
        second_residual,
        nuisance_jacobian=1.0,
        nuisance_variance=0.25,
        reference_mean=-0.1,
    )
    weights, means, covariances, _ = _rao_blackwellized_joint_heading_step(
        prior_weights=np.full(3, 1.0 / 3.0),
        conditional_mean=np.tile(prior_mean, (3, 1)),
        conditional_covariance=np.tile(prior_covariance, (3, 1, 1)),
        previous_external_mean=prior_mean,
        previous_external_covariance=prior_covariance,
        current_external_mean=prior_mean,
        current_external_covariance=prior_covariance,
        statistics=first,
        transition_variance_rad2=0.0,
    )
    weights, _, _, _ = _rao_blackwellized_joint_heading_step(
        prior_weights=weights,
        conditional_mean=means,
        conditional_covariance=covariances,
        previous_external_mean=prior_mean,
        previous_external_covariance=prior_covariance,
        current_external_mean=prior_mean,
        current_external_covariance=prior_covariance,
        statistics=second,
        transition_variance_rad2=0.0,
    )
    shifted_first = first_residual + prior_mean[0] - 0.2
    shifted_second = second_residual + prior_mean[0] + 0.1
    normal = np.full((3, 1, 1), 2.0, dtype=float)
    score = (shifted_first + shifted_second)[:, None]
    quadratic = shifted_first**2 + shifted_second**2
    exact_log, _ = _joint_linear_gaussian_nuisance_log_marginal(
        nuisance_normal=normal,
        nuisance_score=score,
        residual_quadratic=quadratic,
        independent_covariance_log_determinant=np.zeros(3),
        shared_covariance=prior_covariance,
    )
    assert np.allclose(weights, _posterior(exact_log), atol=1e-11, rtol=0.0)


def test_external_prior_change_is_information_ratio_not_covariance_overwrite() -> None:
    first_residual = np.array((-0.2, 0.4, 0.9), dtype=float)
    second_residual = np.array((0.3, -0.1, 0.6), dtype=float)
    old_mean = np.array((0.2,), dtype=float)
    old_covariance = np.array(((0.4,),), dtype=float)
    new_mean = np.array((0.35,), dtype=float)
    new_covariance = np.array(((0.2,),), dtype=float)
    first = _scalar_shared_bias_statistics(
        first_residual,
        nuisance_jacobian=1.0,
        nuisance_variance=0.4,
        reference_mean=-0.15,
    )
    second = _scalar_shared_bias_statistics(
        second_residual,
        nuisance_jacobian=1.0,
        nuisance_variance=0.2,
        reference_mean=0.1,
    )
    weights, means, covariances, _ = _rao_blackwellized_joint_heading_step(
        prior_weights=np.full(3, 1.0 / 3.0),
        conditional_mean=np.tile(old_mean, (3, 1)),
        conditional_covariance=np.tile(old_covariance, (3, 1, 1)),
        previous_external_mean=old_mean,
        previous_external_covariance=old_covariance,
        current_external_mean=old_mean,
        current_external_covariance=old_covariance,
        statistics=first,
        transition_variance_rad2=0.0,
    )
    weights, _, _, report = _rao_blackwellized_joint_heading_step(
        prior_weights=weights,
        conditional_mean=means,
        conditional_covariance=covariances,
        previous_external_mean=old_mean,
        previous_external_covariance=old_covariance,
        current_external_mean=new_mean,
        current_external_covariance=new_covariance,
        statistics=second,
        transition_variance_rad2=0.0,
    )
    shifted_first = first_residual + new_mean[0] + 0.15
    shifted_second = second_residual + new_mean[0] - 0.1
    exact_log, _ = _joint_linear_gaussian_nuisance_log_marginal(
        nuisance_normal=np.full((3, 1, 1), 2.0),
        nuisance_score=(shifted_first + shifted_second)[:, None],
        residual_quadratic=shifted_first**2 + shifted_second**2,
        independent_covariance_log_determinant=np.zeros(3),
        shared_covariance=new_covariance,
    )
    assert np.allclose(weights, _posterior(exact_log), atol=1e-11, rtol=0.0)
    assert report["external_prior_information_ratio_update_applied"] is True


def test_nonzero_heading_diffusion_is_chronological_not_terminal_blur() -> None:
    residual = np.array((-1.5, 0.0, 1.5), dtype=float)
    flat = np.zeros(3, dtype=float)
    covariance = np.array(((0.3,),), dtype=float)
    mean = np.zeros(1, dtype=float)
    first = _scalar_shared_bias_statistics(
        residual,
        nuisance_jacobian=0.0,
        nuisance_variance=0.3,
    )
    second = _scalar_shared_bias_statistics(
        flat,
        nuisance_jacobian=0.0,
        nuisance_variance=0.3,
    )

    def run(transition: float) -> tuple[np.ndarray, dict]:
        weights, means, covariances, _ = _rao_blackwellized_joint_heading_step(
            prior_weights=np.full(3, 1.0 / 3.0),
            conditional_mean=np.tile(mean, (3, 1)),
            conditional_covariance=np.tile(covariance, (3, 1, 1)),
            previous_external_mean=mean,
            previous_external_covariance=covariance,
            current_external_mean=mean,
            current_external_covariance=covariance,
            statistics=first,
            transition_variance_rad2=0.0,
        )
        return _rao_blackwellized_joint_heading_step(
            prior_weights=weights,
            conditional_mean=means,
            conditional_covariance=covariances,
            previous_external_mean=mean,
            previous_external_covariance=covariance,
            current_external_mean=mean,
            current_external_covariance=covariance,
            statistics=second,
            transition_variance_rad2=transition,
        )[::3]

    zero_weights, zero_report = run(0.0)
    diffuse_weights, diffuse_report = run(0.2)
    assert _entropy(diffuse_weights) > _entropy(zero_weights)
    assert diffuse_report["heading_transition_variance_rad2"] == 0.2
    assert diffuse_report["heading_transition_zero_is_exact_no_mixture_case"] is False


def test_informative_no_update_informative_preserves_rao_chronology() -> None:
    pair, _, _, _ = _fixture(yaw_deg=60.0, horizontal_motion=True)
    grid = np.deg2rad(np.arange(360, dtype=float))
    first_residual = np.arctan2(np.sin(grid - 0.4), np.cos(grid - 0.4))
    second_residual = np.arctan2(np.sin(grid - 0.7), np.cos(grid - 0.7))
    first = _scalar_shared_bias_statistics(
        first_residual, nuisance_jacobian=0.3, nuisance_variance=0.4,
    )
    second = _scalar_shared_bias_statistics(
        second_residual, nuisance_jacobian=0.3, nuisance_variance=0.4,
    )
    owner = PersistentNonhingeHeadingLikelihoodOwner(
        branch_ids=("branch",),
        nonhinge_edges=("shoulder_left",),
        delta_grid_rad=grid,
        gap_diffusion_rad2_s=0.01,
        unknown_interval_variance_floor_rad2=0.2,
    )
    owner.process(
        branch_id="branch",
        chronological_index=0,
        likelihood_log_weights=-0.5 * first_residual**2,
        pair=pair,
        likelihood_report={},
        shared_nuisance_statistics=first,
    )
    missing_pair = replace(
        pair,
        action="MISSING",
        parent_observed_time_s=pair.parent_observed_time_s + 10.0,
        child_observed_time_s=pair.child_observed_time_s + 10.0,
    )
    no_update = owner.record_action_no_update(
        branch_id="branch",
        edge="shoulder_left",
        chronological_index=1,
        action="MISSING",
        cause="TEST_GAP",
        timing_pair=missing_pair,
    )
    later_pair = replace(
        pair,
        action="LATER",
        parent_observed_time_s=pair.parent_observed_time_s + 20.0,
        child_observed_time_s=pair.child_observed_time_s + 20.0,
    )
    actual = owner.process(
        branch_id="branch",
        chronological_index=2,
        likelihood_log_weights=-0.5 * second_residual**2,
        pair=later_pair,
        likelihood_report={},
        shared_nuisance_statistics=second,
    ).posterior_weights
    covariance = np.array(((0.4,),), dtype=float)
    mean = np.zeros(1, dtype=float)
    weights, means, covariances, _ = _rao_blackwellized_joint_heading_step(
        prior_weights=np.full(360, 1.0 / 360.0),
        conditional_mean=np.tile(mean, (360, 1)),
        conditional_covariance=np.tile(covariance, (360, 1, 1)),
        previous_external_mean=mean,
        previous_external_covariance=covariance,
        current_external_mean=mean,
        current_external_covariance=covariance,
        statistics=first,
        transition_variance_rad2=0.0,
    )
    weights, means, covariances = _circular_transition_conditional_gaussian(
        weights=weights,
        conditional_mean=means,
        conditional_covariance=covariances,
        variance_rad2=(
            float(missing_pair.parent_observed_time_s[0])
            - float(pair.parent_observed_time_s[-1])
        ) * 0.01,
    )
    expected, _, _, _ = _rao_blackwellized_joint_heading_step(
        prior_weights=weights,
        conditional_mean=means,
        conditional_covariance=covariances,
        previous_external_mean=mean,
        previous_external_covariance=covariance,
        current_external_mean=mean,
        current_external_covariance=covariance,
        statistics=second,
        transition_variance_rad2=(
            float(later_pair.parent_observed_time_s[0])
            - float(missing_pair.parent_observed_time_s[-1])
        ) * 0.01,
    )
    assert np.allclose(actual, expected, atol=1e-12, rtol=0.0)
    assert no_update.report["timing_provenance"]["kind"] == (
        "EXACT_ALIGNED_PAIR_BOUNDARY"
    )
    assert actual is not None


def test_unknown_no_update_floor_is_not_reapplied_as_stale_elapsed_gap() -> None:
    pair, _, _, _ = _fixture(yaw_deg=40.0, horizontal_motion=True)
    grid = np.deg2rad(np.arange(360, dtype=float))
    residual = np.arctan2(np.sin(grid - 0.5), np.cos(grid - 0.5))
    statistics = _scalar_shared_bias_statistics(
        residual, nuisance_jacobian=0.2, nuisance_variance=0.3,
    )
    owner = PersistentNonhingeHeadingLikelihoodOwner(
        branch_ids=("branch",),
        nonhinge_edges=("shoulder_left",),
        delta_grid_rad=grid,
        gap_diffusion_rad2_s=0.01,
        unknown_interval_variance_floor_rad2=0.2,
    )
    owner.process(
        branch_id="branch",
        chronological_index=0,
        likelihood_log_weights=-0.5 * residual**2,
        pair=pair,
        likelihood_report={},
        shared_nuisance_statistics=statistics,
    )
    missing = owner.record_action_no_update(
        branch_id="branch",
        edge="shoulder_left",
        chronological_index=1,
        action="MISSING_WITHOUT_EDGE_TIME",
        cause="TEST_UNKNOWN_INTERVAL",
    )
    later_pair = replace(
        pair,
        action="LATER",
        parent_observed_time_s=pair.parent_observed_time_s + 20.0,
        child_observed_time_s=pair.child_observed_time_s + 20.0,
    )
    later = owner.process(
        branch_id="branch",
        chronological_index=2,
        likelihood_log_weights=-0.5 * residual**2,
        pair=later_pair,
        likelihood_report={},
        shared_nuisance_statistics=statistics,
    )
    assert missing.report["diffusion_variance_rad2"] == 0.2
    assert later.report["diffusion_variance_rad2"] == 0.0
    assert later.report["diffusion_cause"] == (
        "PREVIOUS_UNKNOWN_INTERVAL_FLOOR_ALREADY_APPLIED"
    )


def test_joint_marginal_matches_direct_scalar_gaussian_integration() -> None:
    residual = np.array((-1.0, -0.25, 0.5), dtype=float)
    variance = 0.36
    statistics = _scalar_shared_bias_statistics(
        residual, nuisance_jacobian=1.0, nuisance_variance=variance,
    )
    analytic, report = _joint_linear_gaussian_nuisance_log_marginal(
        nuisance_normal=statistics.nuisance_normal,
        nuisance_score=statistics.nuisance_score,
        residual_quadratic=statistics.residual_quadratic,
        independent_covariance_log_determinant=(
            statistics.independent_covariance_log_determinant
        ),
        shared_covariance=statistics.shared_covariance,
    )
    expected = -0.5 * (
        residual**2 / (1.0 + variance) + np.log1p(variance)
    )
    assert np.allclose(analytic, expected, atol=1e-12, rtol=0.0)
    assert report["candidate_log_determinant_included"] is True
    assert report[
        "joint_linear_gaussian_shared_nuisance_marginal_performed"
    ] is True


def test_repeated_actions_cannot_concentrate_through_shared_nuisance_floor() -> None:
    pair, _, _, _ = _fixture(yaw_deg=60.0, horizontal_motion=True)
    grid = np.deg2rad(np.arange(360, dtype=float))
    residual = np.arctan2(
        np.sin(grid - np.deg2rad(60.0)),
        np.cos(grid - np.deg2rad(60.0)),
    )
    action_log = -0.5 * residual**2
    shared_floor = 0.25
    owner = PersistentNonhingeHeadingLikelihoodOwner(
        branch_ids=("branch",),
        nonhinge_edges=("shoulder_left",),
        delta_grid_rad=grid,
        gap_diffusion_rad2_s=0.0,
        unknown_interval_variance_floor_rad2=0.01,
    )
    results = []
    statistics = _scalar_shared_bias_statistics(
        residual,
        nuisance_jacobian=1.0,
        nuisance_variance=shared_floor,
    )
    for episode in range(12):
        time_shift = float(episode) * 10.0
        episode_pair = replace(
            pair,
            action=f"DUPLICATE_{episode:02d}",
            parent_observed_time_s=pair.parent_observed_time_s + time_shift,
            child_observed_time_s=pair.child_observed_time_s + time_shift,
        )
        results.append(owner.process(
            branch_id="branch",
            chronological_index=episode,
            likelihood_log_weights=action_log,
            pair=episode_pair,
            likelihood_report={
                "shared_heading_variance_floor_rad2": shared_floor,
            },
            shared_nuisance_statistics=statistics,
        ))
    point_mass = np.zeros(len(grid), dtype=float)
    point_mass[60] = 1.0
    common_floor_kernel = _circular_convolve(point_mass, shared_floor)
    assert float(np.max(results[-1].posterior_weights)) <= (
        float(np.max(common_floor_kernel)) + 5e-3
    )
    assert results[-1].report["joint_marginal_model"][
        "shared_nuisance_repeated_as_independent_action_prior"
    ] is False
    assert owner.audit()[
        "shared_center_or_calibration_nuisance_repeated_per_action"
    ] is False
    assert results[-1].report[
        "joint_linear_gaussian_nuisance_marginal_performed"
    ] is True
    assert results[-1].report[
        "joint_irls_nuisance_marginal_performed"
    ] is False


def test_low_sensitivity_action_cannot_poison_later_shared_floor() -> None:
    pair, _, _, _ = _fixture(yaw_deg=60.0, horizontal_motion=True)
    grid = np.deg2rad(np.arange(360, dtype=float))
    owner = PersistentNonhingeHeadingLikelihoodOwner(
        branch_ids=("branch",),
        nonhinge_edges=("shoulder_left",),
        delta_grid_rad=grid,
        gap_diffusion_rad2_s=0.0,
        unknown_interval_variance_floor_rad2=0.01,
    )
    weak_statistics = _scalar_shared_bias_statistics(
        np.zeros(len(grid), dtype=float),
        nuisance_jacobian=1e-6,
        nuisance_variance=1.0,
    )
    weak = owner.process(
        branch_id="branch",
        chronological_index=0,
        likelihood_log_weights=np.zeros(len(grid), dtype=float),
        pair=pair,
        likelihood_report={"shared_heading_variance_floor_rad2": 1e12},
        shared_nuisance_statistics=weak_statistics,
    )
    strong_pair = replace(
        pair,
        action="STRONG",
        parent_observed_time_s=pair.parent_observed_time_s + 10.0,
        child_observed_time_s=pair.child_observed_time_s + 10.0,
    )
    strong_residual = np.arctan2(
        np.sin(grid - np.deg2rad(45.0)),
        np.cos(grid - np.deg2rad(45.0)),
    )
    strong_statistics = _scalar_shared_bias_statistics(
        strong_residual,
        nuisance_jacobian=np.sqrt(0.05),
        nuisance_variance=1.0,
    )
    strong_log = 8.0 * np.cos(grid - np.deg2rad(45.0))
    strong = owner.process(
        branch_id="branch",
        chronological_index=1,
        likelihood_log_weights=strong_log,
        pair=strong_pair,
        likelihood_report={"shared_heading_variance_floor_rad2": 0.05},
        shared_nuisance_statistics=strong_statistics,
    )
    assert weak.report["historical_max_of_low_sensitivity_action_ratio_used"] is False
    assert strong.report["historical_max_of_low_sensitivity_action_ratio_used"] is False
    assert strong.report[
        "joint_linear_gaussian_nuisance_marginal_performed"
    ] is True
    assert strong.report["joint_irls_nuisance_marginal_performed"] is False
    assert strong.report["static_prefix_then_total_diffusion_used"] is False
    assert strong.report["joint_marginal_model"][
        "shared_nuisance_repeated_as_independent_action_prior"
    ] is False
    weak_resultant = abs(np.sum(weak.posterior_weights * np.exp(1j * grid)))
    strong_resultant = abs(np.sum(strong.posterior_weights * np.exp(1j * grid)))
    assert strong_resultant > weak_resultant + 0.1
