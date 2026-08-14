import copy
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.calibration.anthropometry_v4_1 import (
    AnthropometryV41,
    DIRECT_SOLVER_REQUIRED,
    DERIVED_SOLVER_REQUIRED,
    NODE_TO_SEGMENT,
    SensorPlacementV41,
)
from biospur_fusion.calibration.articulated_batch import CalibrationSamples, SEGMENTS
from biospur_fusion.calibration.centerline_quotient_v4_1 import (
    BASE_PARAMETER_COUNT,
    LIMB_SEGMENTS,
    CenterlineQuotientProblemV41,
    QuotientStaticV41,
    evaluate_gate_decisions,
    physical_difference,
    predict_antennas,
    predict_joint_centres,
    quotient_observability,
    rank_from_singular_values,
)


def gates():
    path = (Path(__file__).resolve().parents[3]
            / "Fusion_Part/config/body_calibration_v4_1/invariance_gates_v4_1.json")
    return json.loads(path.read_text(encoding="utf-8"))


def anthropometry():
    direct = {name: 0.3 for name in DIRECT_SOLVER_REQUIRED}
    derived = {name: 0.3 for name in DERIVED_SOLVER_REQUIRED}
    derived["hip_joint_centre_vertical_offset"] = -0.04
    placements = {}
    for node, segment in NODE_TO_SEGMENT.items():
        placements[node] = SensorPlacementV41(
            node=node,
            segment=segment,
            landmark=f"{segment}_landmark",
            pcb_phase_centre_to_enclosure_m=np.zeros(3),
            capture_prior_m=np.zeros(3),
            capture_sigma_m=np.full(3, 0.005),
            capture_lower_m=np.full(3, -0.05),
            capture_upper_m=np.full(3, 0.05),
            capture_status="CALIBRATION_ESTIMATED",
            capture_source="synthetic enclosure fixture",
            estimate_as_nuisance=True,
        )
    return AnthropometryV41(
        source_sha256="synthetic",
        direct_surface_m=direct,
        direct_surface_sigma_m={name: 0.004 for name in direct},
        derived_joint_center_m=derived,
        derived_joint_center_sigma_m={name: 0.006 for name in derived},
        placements=placements,
    )


def fixture(count=18):
    rng = np.random.default_rng(4141)
    anthro = anthropometry()
    r_nv = Rotation.from_euler("yx", [0.12, -0.08]).as_matrix()
    pelvis = Rotation.from_rotvec([0.08, -0.05, 0.03]).as_matrix()
    torso = Rotation.from_rotvec([-0.05, 0.07, -0.02]).as_matrix()
    axes = {name: rng.normal(size=3) for name in LIMB_SEGMENTS}
    axes = {name: value / np.linalg.norm(value) for name, value in axes.items()}
    truth = QuotientStaticV41(
        r_nv, pelvis, torso, axes,
        {node: np.zeros(3) for node in NODE_TO_SEGMENT},
    )
    q = np.empty((count, len(SEGMENTS), 3, 3))
    for knot in range(count):
        phase = 2 * np.pi * knot / count
        for segment in range(len(SEGMENTS)):
            q[knot, segment] = Rotation.from_euler(
                "xyz",
                [0.4 * np.sin(phase + 0.19 * segment),
                 0.3 * np.cos(2 * phase + 0.11 * segment),
                 0.2 * np.sin(3 * phase + 0.17 * segment)],
            ).as_matrix()
    blank = CalibrationSamples(
        np.arange(count, dtype=np.int64) * 100_000_000,
        np.asarray(["arms" if knot < count / 2 else "squats" for knot in range(count)]),
        np.zeros((count, len(SEGMENTS), 3)),
        np.tile(np.eye(3) * 4e-6, (count, len(SEGMENTS), 1, 1)),
        q,
        np.ones((count, len(SEGMENTS)), bool),
    )
    antenna, _ = predict_antennas(blank, truth, anthro)
    root = np.c_[
        0.2 * np.sin(np.linspace(0, 2, count)),
        0.1 * np.cos(np.linspace(0, 1, count)),
        np.ones(count),
    ]
    p_v4 = np.einsum("ij,ntj->nti", r_nv.T, antenna + root[:, None])
    samples = CalibrationSamples(
        blank.time_ns, blank.action, p_v4, blank.covariance_v4_m2, q, blank.valid_position)
    return samples, truth, anthro


def test_joint_centres_and_antennas_are_independent_predictions():
    samples, truth, anthro = fixture(8)
    changed_offsets = {node: value.copy() for node, value in truth.capture_enclosure_to_landmark_m.items()}
    changed_offsets["BSFB165"] = np.array([0.01, 0.0, 0.0])
    changed = QuotientStaticV41(
        truth.R_N_from_V4,
        truth.R_pelvis_from_sensor,
        truth.R_torso_from_sensor,
        truth.limb_axis_sensor,
        changed_offsets,
    )
    joints_before, _ = predict_joint_centres(samples, truth, anthro)
    joints_after, _ = predict_joint_centres(samples, changed, anthro)
    antennas_before, _ = predict_antennas(samples, truth, anthro)
    antennas_after, _ = predict_antennas(samples, changed, anthro)
    assert np.array_equal(joints_before, joints_after)
    assert np.max(np.abs(antennas_before - antennas_after)) > 1e-4


def test_every_estimated_offset_is_in_actual_measurement_and_posterior_jacobian():
    samples, truth, anthro = fixture(12)
    problem = CenterlineQuotientProblemV41(samples, anthro)
    vector = problem.initial_vector(truth)
    assert len(vector) == BASE_PARAMETER_COUNT + 3 * len(NODE_TO_SEGMENT)
    assert len([name for name in problem.parameter_names if name.startswith("capture_offset.")]) == 30
    measurement = problem.numerical_jacobian(vector, include_priors=False, relative_step=2e-6)
    posterior = problem.numerical_jacobian(vector, include_priors=True, relative_step=2e-6)
    assert measurement.shape[1] == posterior.shape[1] == len(vector)
    assert np.all(np.linalg.norm(measurement[:, BASE_PARAMETER_COUNT:], axis=0) > 0)
    assert np.all(np.linalg.norm(posterior[:, BASE_PARAMETER_COUNT:], axis=0) > 0)
    report = quotient_observability(problem, vector, gates())
    assert report["all_estimated_sensor_placements_in_jacobian"]


def test_physical_difference_separates_joint_and_antenna_displacement():
    samples, truth, anthro = fixture(8)
    problem = CenterlineQuotientProblemV41(samples, anthro)
    left = problem.initial_vector(truth)
    right = left.copy()
    right[BASE_PARAMETER_COUNT] += 0.001
    result = physical_difference(problem, left, right)
    assert result["maximum_joint_centre_displacement_mm"] == 0.0
    assert result["maximum_antenna_displacement_mm"] > 0.5


def test_observability_threshold_is_loaded_from_gate_json():
    value = gates()
    rank_default, threshold_default = rank_from_singular_values(np.array([1.0, 5e-7]), value)
    changed = copy.deepcopy(value)
    changed["execution_gates"]["observability_relative_singular_value_threshold"] = 1e-8
    rank_changed, threshold_changed = rank_from_singular_values(np.array([1.0, 5e-7]), changed)
    assert rank_default == 1 and rank_changed == 2
    assert threshold_default != threshold_changed


def test_changing_every_declared_execution_gate_changes_its_decision():
    value = gates()
    g = value["execution_gates"]
    metrics = {
        "null_axis_rad": g["maximum_segment_axis_angular_change_rad"] * 0.75,
        "null_joint_m": g["maximum_joint_centre_displacement_m"] * 0.75,
        "null_antenna_m": g["maximum_antenna_displacement_m"] * 0.75,
        "repeat_axis_rad": g["repeatability_maximum_segment_axis_angular_change_rad"] * 0.75,
        "repeat_joint_m": g["repeatability_maximum_joint_centre_displacement_m"] * 0.75,
        "repeat_antenna_m": g["repeatability_maximum_antenna_displacement_m"] * 0.75,
        "optimizer_relative_cost": g["optimizer_maximum_relative_cost_difference"] * 0.75,
        "model_median": g["model_mismatch_maximum_normalized_residual_median"] * 0.75,
        "model_p95": g["model_mismatch_maximum_normalized_residual_p95"] * 0.75,
        "offset_shift_sigma": g["sensor_offset_maximum_posterior_shift_sigma"] * 0.75,
        "offset_bound_clearance_fraction": g["sensor_offset_minimum_bound_clearance_fraction"] * 1.25,
        "offset_profile_axis_rad": g["sensor_offset_profile_maximum_segment_axis_angular_change_rad"] * 0.75,
        "offset_profile_joint_m": g["sensor_offset_profile_maximum_joint_centre_displacement_m"] * 0.75,
        "offset_profile_antenna_m": g["sensor_offset_profile_maximum_antenna_displacement_m"] * 0.75,
    }
    gate_to_decision = {
        "maximum_segment_axis_angular_change_rad": "null_axis",
        "maximum_joint_centre_displacement_m": "null_joint",
        "maximum_antenna_displacement_m": "null_antenna",
        "repeatability_maximum_segment_axis_angular_change_rad": "repeat_axis",
        "repeatability_maximum_joint_centre_displacement_m": "repeat_joint",
        "repeatability_maximum_antenna_displacement_m": "repeat_antenna",
        "optimizer_maximum_relative_cost_difference": "optimizer_cost",
        "model_mismatch_maximum_normalized_residual_median": "model_median",
        "model_mismatch_maximum_normalized_residual_p95": "model_p95",
        "sensor_offset_maximum_posterior_shift_sigma": "offset_shift",
        "sensor_offset_minimum_bound_clearance_fraction": "offset_clearance",
        "sensor_offset_profile_maximum_segment_axis_angular_change_rad": "offset_profile_axis",
        "sensor_offset_profile_maximum_joint_centre_displacement_m": "offset_profile_joint",
        "sensor_offset_profile_maximum_antenna_displacement_m": "offset_profile_antenna",
    }
    assert set(g) == set(gate_to_decision) | {"observability_relative_singular_value_threshold"}
    baseline = evaluate_gate_decisions(metrics, value)
    assert baseline["pass"]
    for gate_name, decision_name in gate_to_decision.items():
        changed = copy.deepcopy(value)
        if gate_name == "sensor_offset_minimum_bound_clearance_fraction":
            changed["execution_gates"][gate_name] *= 2.0
        else:
            changed["execution_gates"][gate_name] *= 0.5
        decision = evaluate_gate_decisions(metrics, changed)
        assert baseline[decision_name] != decision[decision_name], gate_name
