import json
from pathlib import Path

import numpy as np

from biospur_fusion.imu_multi_action_revision_d.r3_cycle import (
    _cycle_wave,
    _run_synthetic_angle,
    run_r3_synthetic_qualification,
)


ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "Fusion_Part/config/imu_multi_action_revision_d_r3/R3_CYCLE_DEFINITION.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text())


def test_r3_contract_separates_bout_cycle_and_post_neutral():
    assert CONTRACT["claims_are_independent"] == [
        "ACTIVE_BOUT_VALID",
        "CYCLE_TOPOLOGY_VALID",
        "POST_NEUTRAL_AVAILABLE",
    ]
    cycle = CONTRACT["cycle_topology"]
    assert not cycle["quiet_plateau_required"]
    assert not cycle["exact_pre_pose_return_required"]
    assert not cycle["post_neutral_required"]
    assert not cycle["fitted_functional_axis_required"]


def test_r3_uses_correct_primary_chains():
    assert CONTRACT["arms"]["left_primary_chain"] == ["torso", "upper_arm_L"]
    assert CONTRACT["arms"]["right_primary_chain"] == ["torso", "upper_arm_R"]
    assert CONTRACT["arms"]["forearm_role"].endswith("DIAGNOSTIC_ONLY")
    assert CONTRACT["heel_to_butt"]["left_primary_chain"] == ["thigh_L", "shank_L"]
    assert CONTRACT["heel_to_butt"]["right_primary_chain"] == ["thigh_R", "shank_R"]


def test_all_mandatory_r3_synthetic_controls_pass():
    result = run_r3_synthetic_qualification(CONTRACT)
    assert result["terminal_outcome"] == "PASS_R3_SYNTHETIC_CYCLE_QUALIFICATION"
    assert all(result["controls"].values())
    assert all(result["required_outcomes"].values())
    assert not result["real_capture_accessed"]


def test_partial_return_is_amplitude_relative_not_exact_neutral():
    result = _run_synthetic_angle(_cycle_wave([0.6] * 5, low_fraction=0.30), CONTRACT)
    assert result["detected_repetition_count"] == 5
    assert min(item["recovery_fraction"] for item in result["complete_cycles"]) >= 0.60
    assert any(item["end_excursion_rad"] > 0.10 for item in result["complete_cycles"])


def test_partial_final_repetition_is_disclosed_not_forced():
    result = _run_synthetic_angle(_cycle_wave([0.55] * 5, partial_final=True), CONTRACT)
    assert result["detected_repetition_count"] == 4
    assert len(result["partial_repetitions"]) == 1


def test_rigid_chain_and_forearm_only_cannot_create_primary_arm_cycle():
    motion = _cycle_wave([0.55] * 5)
    no_shoulder_excursion = _run_synthetic_angle(np.zeros_like(motion), CONTRACT, parent_angle=motion)
    assert no_shoulder_excursion["detected_repetition_count"] == 0


def test_invalid_gap_is_not_crossed():
    motion = _cycle_wave([0.55] * 5)
    invalid = np.zeros(len(motion), bool)
    invalid[80:90] = True
    result = _run_synthetic_angle(motion, CONTRACT, invalid=invalid)
    assert result["invalid_row_count"] == 10
    assert result["detected_repetition_count"] < 5


def test_synthetic_replay_is_deterministic():
    first = run_r3_synthetic_qualification(CONTRACT)
    second = run_r3_synthetic_qualification(CONTRACT)
    assert first == second
