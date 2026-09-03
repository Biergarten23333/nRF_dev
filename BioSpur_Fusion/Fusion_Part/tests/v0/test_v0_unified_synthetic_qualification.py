import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from biospur_fusion.v0.synthetic_qualification import (
    rank_lineage,
    recovery_record,
    sign_handedness_alternatives,
)
from biospur_fusion.v0.unified_calibration import (
    FULL_DIMENSION,
    LEVER_ARM_NUISANCE_DIMENSION,
    NUISANCE_DIMENSION,
    POSE_NUISANCE_DIMENSION,
    PRODUCT_DIMENSION,
    UnifiedCalibrationObjective,
    _heading_profile,
    bounds,
    decode_product,
)
from biospur_fusion.v0.unified_synthetic import B3_ACTIONS, B4_EN_BLOC, B5_NEGATIVE_CONTROLS, generate_unified_case
from tools.run_v0_observability_synthetic_qualification import (
    b4_excitation_audit,
    b4_negative_control_audit,
    b5_excitation_audit,
    b5_negative_control_audit,
)


ROOT = Path(__file__).parents[2]


def contract():
    return json.loads((ROOT / "config/biospur_fusion_v0_observability_first/SYNTHETIC_QUALIFICATION_CONTRACT.json").read_text())


def test_unified_state_has_nine_headings_and_declared_dimensions():
    assert PRODUCT_DIMENSION == 55
    assert POSE_NUISANCE_DIMENSION == 34
    assert LEVER_ARM_NUISANCE_DIMENSION == 54
    assert NUISANCE_DIMENSION == 88
    assert FULL_DIMENSION == 143
    observation, truth, _ = generate_unified_case(contract(), 17011, noisy=False)
    product = decode_product(truth)
    assert product["headings"]["pelvis"] == 0.0
    assert len(product["headings"]) == 10
    assert observation.source == "EXACT_TIME_RESOLVED_UNIFIED_SYNTHETIC"
    assert observation.accel_mps2 is not None
    assert observation.accel_mps2.shape == observation.gyro_rad_s.shape


def test_functional_axis_is_fixed_in_parent_board_not_world():
    cfg = contract()
    observation, truth, _ = generate_unified_case(cfg, 17011, noisy=False)
    objective = UnifiedCalibrationObjective(observation, cfg)
    product = decode_product(truth)
    rows = objective._rows("squats", ("thigh_L", "shank_L"))
    world = objective.functional_axis_world(product, "knee_L", rows)
    expected = np.einsum(
        "nij,j->ni",
        objective.corrected_rotation(product, "thigh_L", rows),
        product["functional"]["knee_L"],
    )
    assert np.array_equal(world, expected)
    assert np.max(np.linalg.norm(world - world[0], axis=1)) > 1e-4


def test_rank_lineage_separates_raw_soft_and_parameter_prior_evidence():
    cfg = contract()
    observation, truth, _ = generate_unified_case(cfg, 17011, noisy=False)
    lineage = rank_lineage(UnifiedCalibrationObjective(observation, cfg), truth)
    stages = lineage["stages"]
    raw = stages["RAW_TIME_RESOLVED_MEASUREMENT"]
    raw_zero = stages["RAW_PLUS_MEASURED_NEUTRAL_ZERO_DEFINITIONS"]
    combined = stages["ALL_MEASUREMENT_AND_SOFT_PROTOCOL"]
    assert raw["heading_conditioned_on_all_other_coordinates"]["rank"] == 9
    assert raw["profiled_product_rank"] == 48
    assert raw_zero["profiled_product_rank"] == 55
    assert combined["profiled_product_rank"] == 55
    assert lineage["parameter_only_priors_add_rank"] is False
    assert lineage["b5_lever_arm_measurement_subspace"]["rank"] == 33
    assert lineage["b5_lever_arm_measurement_subspace"]["nullity"] == 21
    assert lineage["b5_lever_arm_measurement_subspace"]["bounds_or_priors_entered_rows"] is False


def test_synthetic_generator_and_objective_are_deterministic_and_truth_sealed():
    cfg = contract()
    first, truth_first, metadata_first = generate_unified_case(cfg, 17029, noisy=True)
    second, truth_second, metadata_second = generate_unified_case(cfg, 17029, noisy=True)
    assert first.signature() == second.signature()
    assert np.array_equal(truth_first, truth_second)
    assert np.array_equal(metadata_first["segment_directions"], metadata_second["segment_directions"])
    assert not hasattr(first, "truth")


def test_s2_chart_does_not_exclude_valid_near_vertical_sensor_axis():
    cfg = contract()
    _, truth, _ = generate_unified_case(cfg, 17029, noisy=False)
    lower, upper = bounds()
    assert truth[15] > 1.45
    assert lower[15] < truth[15] < upper[15]
    assert upper[15] < np.pi / 2.0


def test_heading_profiler_separates_relative_headings_from_static_root_yaw():
    cfg = contract()
    observation, truth, metadata = generate_unified_case(cfg, 17011, noisy=False)
    objective = UnifiedCalibrationObjective(observation, cfg)
    diagnostic_coordinates = np.r_[truth[20:29], truth[PRODUCT_DIMENSION + 2], truth[PRODUCT_DIMENSION + 19]]
    profiled = _heading_profile(observation, cfg, diagnostic_coordinates)
    recovery = recovery_record(objective, profiled, metadata["segment_directions"])
    assert recovery["segment_direction_rmse_deg"] < 0.5


def test_bilateral_relations_are_noise_free_truth_consistent():
    cfg = contract()
    observation, truth, _ = generate_unified_case(cfg, 17011, noisy=False)
    blocks = UnifiedCalibrationObjective(observation, cfg).blocks(truth, True)
    bilateral = [block for block in blocks if block.factor.startswith("time_resolved_bilateral_")]
    assert len(bilateral) == 4
    cost = sum(float(np.sum(np.sqrt(1.0 + block.values * block.values) - 1.0)) for block in bilateral)
    assert cost < 0.03


def test_b3_minimal_motions_are_distinct_complete_same_rest_episodes():
    cfg = contract()
    observation, _, metadata = generate_unified_case(cfg, 17011, noisy=False)
    assert set(metadata["b3_episode_audit"]) == set(B3_ACTIONS)
    for action in B3_ACTIONS:
        audit = metadata["b3_episode_audit"][action]
        assert audit["complete_rest_transition_action_return_same_rest"] is True
        assert audit["same_rest_max_segment_geodesic_deg"] == 0.0
        assert set(audit["phase_row_counts"]) == {
            "PRE_REST",
            "TRANSITION_TO_ACTION",
            "FORMAL_ACTION",
            "TRANSITION_TO_POST_REST",
            "POST_REST",
        }
        assert all(value > 0 for value in audit["phase_row_counts"].values())
        assert observation.r3d_actions[action]["b3_minimal_motion"] is True
        assert metadata["b3_label_distinctions"][action]


def test_b3_factors_are_truth_consistent_and_orient_trunk_handedness():
    cfg = contract()
    observation, truth, _ = generate_unified_case(cfg, 17011, noisy=False)
    objective = UnifiedCalibrationObjective(observation, cfg)
    blocks = [
        block for block in objective.blocks(truth, True)
        if block.action in B3_ACTIONS
        and not block.factor.startswith("b5_joint_center_specific_force_closure")
    ]
    assert len(blocks) == 9
    cost = sum(float(np.sum(np.sqrt(1.0 + block.values * block.values) - 1.0)) for block in blocks)
    assert cost < 0.01
    alternatives = {item["alternative"]: item["delta_cost"] for item in sign_handedness_alternatives(objective, truth)}
    assert alternatives["flip_trunk_flex_and_lateral"] > 5.0
    assert alternatives["flip_trunk_lateral_and_axial"] > 5.0

    labelled = next(block for block in blocks if block.factor == "time_resolved_labelled_left_right_trunk_lateral_direction")
    derivatives = []
    for coordinate in range(45, 48):
        candidate = truth.copy()
        candidate[coordinate] += 1e-6
        perturbed = next(
            block for block in objective.blocks(candidate, True)
            if block.factor == labelled.factor
        )
        derivatives.append((perturbed.values - labelled.values) / 1e-6)
    assert np.linalg.norm(np.column_stack(derivatives)) > 0.1


def test_b4_is_complete_two_phase_same_rest_and_measurement_only():
    cfg = contract()
    observation, truth, metadata = generate_unified_case(cfg, 17011, noisy=False)
    objective = UnifiedCalibrationObjective(observation, cfg)
    episode = metadata["b4_episode_audit"]
    assert episode["complete_rest_transition_action_return_same_rest"] is True
    assert episode["same_rest_max_segment_geodesic_deg"] == 0.0
    assert observation.r3d_actions[B4_EN_BLOC]["b4_supported_braced_en_bloc"] is True
    assert set(observation.r3d_actions[B4_EN_BLOC]["B4_COMMON_RATE_PHASE_ROWS"]) == {
        "COMMON_ROTATION_AXIS_A", "COMMON_ROTATION_AXIS_B",
    }
    audit = b4_excitation_audit(objective, metadata, cfg["recovery_gates"])
    assert audit["pass"] is True
    blocks = [block for block in objective.blocks(truth, True) if block.action == B4_EN_BLOC]
    assert len(blocks) == 18
    assert all(block.classification == "MEASURED_OBSERVATION" for block in blocks)
    assert all(block.parameter_blocks == ("EFFECTIVE_RELATIVE_HEADING", "WHOLE_CAR_RIGID_COMMON_RATE") for block in blocks)
    assert all(
        "axis" not in block.prediction_equation.lower()
        and "yaw" not in block.prediction_equation.lower()
        for block in blocks
    )
    cost = sum(float(np.sum(np.sqrt(1.0 + block.values * block.values) - 1.0)) for block in blocks)
    assert cost < 1e-9

    wrong = truth.copy()
    wrong[20:29] += 0.5
    wrong_blocks = [block for block in objective.blocks(wrong, True) if block.action == B4_EN_BLOC]
    wrong_cost = sum(float(np.sum(np.sqrt(1.0 + block.values * block.values) - 1.0)) for block in wrong_blocks)
    assert wrong_cost > 1.0


def test_b4_negative_controls_all_fail_closed_for_declared_mechanism():
    result = b4_negative_control_audit(contract())
    assert result["pass"] is True
    assert {item["control"] for item in result["records"]} == {
        "segment_articulation",
        "insufficient_second_axis",
        "timing_mismatch",
        "noisy_near_zero_rates",
    }
    assert all(item["eligibility_pass"] is False for item in result["records"])
    assert all(item["mechanism_detected"] is True for item in result["records"])


def test_b5_joint_center_closure_is_measurement_only_and_common_force_cancels():
    cfg = contract()
    observation, truth, metadata = generate_unified_case(cfg, 17011, noisy=False)
    objective = UnifiedCalibrationObjective(observation, cfg)
    audit = b5_excitation_audit(objective, truth, metadata, cfg["recovery_gates"])
    assert audit["pass"] is True
    blocks = [
        block for block in objective.blocks(truth, True)
        if block.factor.startswith("b5_joint_center_specific_force_closure")
    ]
    assert len(blocks) == 20
    assert all(block.classification == "MEASURED_OBSERVATION" for block in blocks)
    assert all("B5_SENSOR_TO_JOINT_LEVER_ARM_NUISANCE" in block.parameter_blocks for block in blocks)
    assert not any(
        block.classification == "PARAMETER_ONLY_PRIOR"
        and "B5_SENSOR_TO_JOINT_LEVER_ARM_NUISANCE" in block.parameter_blocks
        for block in objective.blocks(truth, True)
    )

    product = decode_product(truth)
    common_navigation_force = np.array([0.31, -0.22, 9.80665])
    accel = observation.accel_mps2.copy()
    for segment_index, segment in enumerate(objective.obs.node_order):
        anatomical_segment = observation.node_to_segment[segment]
        rows = np.arange(len(observation.time_ns))
        rotation = objective.corrected_rotation(product, anatomical_segment, rows)
        accel[:, segment_index] += np.einsum(
            "nji,j->ni", rotation, common_navigation_force,
        )
    shifted = replace(observation, accel_mps2=accel)
    shifted_blocks = [
        block for block in UnifiedCalibrationObjective(shifted, cfg).blocks(truth, True)
        if block.factor.startswith("b5_joint_center_specific_force_closure")
    ]
    assert len(shifted_blocks) == len(blocks)
    for baseline, candidate in zip(blocks, shifted_blocks, strict=True):
        assert baseline.action == candidate.action
        assert baseline.factor == candidate.factor
        assert np.allclose(baseline.values, candidate.values, atol=2e-13, rtol=0.0)


def test_b5_negative_controls_all_fail_closed_for_declared_mechanism():
    result = b5_negative_control_audit(contract())
    assert result["pass"] is True
    assert {item["control"] for item in result["records"]} == set(B5_NEGATIVE_CONTROLS)
    assert all(item["eligibility_pass"] is False for item in result["records"])
    assert all(item["mechanism_detected"] is True for item in result["records"])
