import json
from pathlib import Path

from biospur_fusion.imu_multi_action_revision_d.r3b_topology import synthetic_qualification


ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "Fusion_Part/config/imu_multi_action_revision_d_r3b"
CONTRACT = json.loads((CONFIG / "R3B_SIGNAL_DERIVED_ACTION_CONTRACT.json").read_text())
REFERENCE = json.loads((CONFIG / "R3B_REFERENCE_AND_NEUTRAL_SEMANTICS.json").read_text())
CHAINS = json.loads((CONFIG / "R3B_ACTION_CHAIN_MAP.json").read_text())


def test_reference_quality_is_not_active_motion_gate():
    assert not CONTRACT["baseline"]["legacy_retained_fraction_is_active_bout_gate"]
    assert CONTRACT["reference_quality"]["LOW_disables_zero_factor"]
    assert not CONTRACT["reference_quality"]["LOW_disables_active_motion"]


def test_final_still_remains_sealed():
    assert REFERENCE["final_still"]["status"] == "SEALED"
    assert REFERENCE["final_still"]["samples_must_not_be_accessed"]


def test_primary_chain_semantics():
    actions = CHAINS["actions"]
    assert actions["arms"]["primary_chains"]["left"] == ["torso", "upper_arm_L"]
    assert actions["left_knee"]["primary_chains"]["hip_L"] == ["pelvis", "thigh_L"]
    assert actions["left_heel"]["primary_chains"]["knee_L"] == ["thigh_L", "shank_L"]
    assert actions["trunk"]["primary_chains"]["trunk"] == ["pelvis", "torso"]


def test_complete_human_tolerant_synthetic_suite_passes():
    result = synthetic_qualification(CONTRACT)
    assert result["terminal_outcome"] == "PASS_R3B_SYNTHETIC_QUALIFICATION"
    assert all(result["controls"].values())
    assert not result["real_capture_accessed"]


def test_synthetic_qualification_is_deterministic():
    assert synthetic_qualification(CONTRACT) == synthetic_qualification(CONTRACT)
