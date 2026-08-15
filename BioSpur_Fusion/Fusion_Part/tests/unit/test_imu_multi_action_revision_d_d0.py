import json
from pathlib import Path

from biospur_fusion.imu_multi_action_revision_d.d0_synthetic import LAYOUT, qualify_d0_synthetic


ROOT = Path(__file__).resolve().parents[3]
R3D = json.loads((ROOT / "Fusion_Part/config/imu_multi_action_revision_d_r3d/R3D_BROAD_ACTIVITY_CONTRACT.json").read_text())
CHAIN_MAP = json.loads((ROOT / "Fusion_Part/config/imu_multi_action_revision_d_r3c/R3C_ACTION_CHAIN_MAP.json").read_text())
D0 = json.loads((ROOT / "Fusion_Part/config/imu_multi_action_revision_d_d0/D0_ACTION_FACTOR_CONTRACT.json").read_text())


def test_d0a_state_accounting_and_minimal_trunk_frame():
    assert LAYOUT["dimension"] == 95
    assert LAYOUT["publishable_dimension"] == 55
    assert LAYOUT["nuisance_dimension"] == 40
    trunk = next(item for item in LAYOUT["entries"] if item["name"] == "trunk_functional_frame")
    assert trunk["stop"] - trunk["start"] == 3


def test_d0b_shared_objective_wiring_and_frozen_nullspace_failure():
    result = qualify_d0_synthetic(R3D, CHAIN_MAP, D0)
    assert result["terminal_outcome"] == "FAIL_D0B_SYNTHETIC_NULLSPACE"
    assert result["data_only_observability"]["rank"] == 72
    assert result["data_plus_protocol_prior_observability"]["rank"] == 92
    assert result["data_plus_protocol_prior_observability"]["nullity"] == 3
    assert result["exact_blocker_before_real_d0"] == "TORSO_EFFECTIVE_HEADING_VS_TRUNK_FUNCTIONAL_FRAME_TRADEOFF"
    assert result["controls"]["all_eleven_actions_in_one_shared_objective"]
    assert result["controls"]["all_eleven_actions_have_publishable_data_information"]
    assert result["controls"]["directional_jv_matches_five_point"]
    assert not result["controls"]["data_plus_protocol_prior_full_rank_after_declared_global_gauge"]
