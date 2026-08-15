from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.imu_multi_action_s2.human_synthetic import (
    generate_human_motion_synthetic,
)
from biospur_fusion.imu_multi_action_s2.observability import S2UnifiedProblem
from biospur_fusion.imu_multi_action_s2.segmentation import segment_action_phases


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "imu_only_multi_action_centerline_calibration_v1_s2"
TEMPLATE = ROOT / "config" / "generic_template_motion_demo_v1" / "GENERIC_ADULT_PROXY_V1.json"


def _inputs() -> tuple[dict, dict]:
    return (json.loads((CONFIG / "s2_gates_v1.json").read_text()),
            json.loads(TEMPLATE.read_text()))


@pytest.fixture(scope="module")
def synthetic() -> tuple[dict, object, dict]:
    gates, template = _inputs()
    dataset = generate_human_motion_synthetic(gates, template, seed=2201)
    segmentation = segment_action_phases(dataset, gates)
    return gates, dataset, segmentation


def test_action_contract_uses_correct_human_semantics() -> None:
    contract = json.loads((CONFIG / "OPERATOR_ACTION_CONTRACT.json").read_text())
    text = json.dumps(contract)
    assert "LEFT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK" in text
    assert "RIGHT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK" in text
    assert "LEFT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION" in text
    assert "RIGHT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION" in text
    assert "validation-only" not in text.lower()
    heel_rows = [row for row in contract["actions"] if row["raw_ledger_label"] in ("left_heel", "right_heel")]
    assert all("calf raise" in row["explicitly_not"] for row in heel_rows)


def test_signal_driven_segmentation_recovers_compound_phases(synthetic) -> None:
    _, _, segmentation = synthetic
    assert segmentation["pass"] is True
    assert segmentation["truth_boundaries_read"] is False
    assert segmentation["equal_duration_split_used"] is False
    phases = {row["semantic_phase"]: row for row in segmentation["segments"]}
    assert phases["LEFT_ARM_RAISE_LOWER"]["detected_repetition_count"] == 5
    assert phases["RIGHT_ARM_RAISE_LOWER"]["detected_repetition_count"] == 5
    assert phases["BILATERAL_ARM_RAISE_LOWER"]["detected_repetition_count"] == 5
    assert phases["LEFT_ELBOW_CURL"]["detected_repetition_count"] == 5
    assert phases["LEFT_FOREARM_PRONATION_SUPINATION"]["detected_repetition_count"] == 5
    assert phases["RIGHT_RETURN_STILL"]["sample_count"] >= 400
    assert phases["LEFT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK"]["detected_repetition_count"] == 4
    assert phases["RIGHT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION"]["detected_repetition_count"] == 4
    for name in ("TRUNK_LEFT_ROTATION", "TRUNK_RIGHT_ROTATION", "TRUNK_FORWARD_BEND_AND_RECOVER"):
        assert phases[name]["detected_repetition_count"] == 3


def test_all_semantic_phases_enter_one_shared_objective(synthetic) -> None:
    gates, dataset, segmentation = synthetic
    _, template = _inputs()
    problem = S2UnifiedProblem(dataset, segmentation, gates, template)
    blocks = problem.residual_blocks(np.zeros(problem.parameter_count))
    actions = {action for action, _, _ in blocks}
    required = {
        "LEFT_ARM_RAISE_LOWER", "RIGHT_ARM_RAISE_LOWER", "BILATERAL_ARM_RAISE_LOWER",
        "LEFT_ELBOW_CURL", "RIGHT_ELBOW_CURL",
        "LEFT_FOREARM_PRONATION_SUPINATION", "RIGHT_FOREARM_PRONATION_SUPINATION",
        "LEFT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK", "RIGHT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK",
        "LEFT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION", "RIGHT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION",
        "TRUNK_LEFT_ROTATION", "TRUNK_RIGHT_ROTATION", "TRUNK_FORWARD_BEND_AND_RECOVER",
    }
    assert required <= actions
    assert problem.parameter_count == 55


def test_no_exact_human_robot_constraints_or_changed_rank_threshold() -> None:
    gates, _ = _inputs()
    assert gates["observability"]["relative_singular_value_threshold"] == 1e-6
    assert gates["noise_floors"]["functional_off_axis_rad_s"] > 0
    assert gates["noise_floors"]["shared_point_accel_mps2"] > 0
    model = (CONFIG / "HUMAN_MOTION_MODEL_CONTRACT.md").read_text()
    assert "off-axis" in model
    assert "full three-degree-of-freedom pelvis and torso orientations" in model


def test_s2_runner_has_no_real_input_interface() -> None:
    runner = ROOT / "tools" / "run_multi_action_calibration_s2_human_observability.py"
    source = runner.read_text()
    for forbidden_argument in (
        "--ledger", "--capture", "--uwb", "--t4", "--walk", "--final-still",
        "--operator-measurements",
    ):
        assert forbidden_argument not in source
    assert "generate_human_motion_synthetic" in source


def test_failed_synthetic_run_never_serializes_real_calibration() -> None:
    runner = (ROOT / "tools" / "run_multi_action_calibration_s2_human_observability.py").read_text()
    assert "FROZEN_CALIBRATION.json" not in runner
    assert ".mp4" not in runner
    assert ".gif" not in runner
