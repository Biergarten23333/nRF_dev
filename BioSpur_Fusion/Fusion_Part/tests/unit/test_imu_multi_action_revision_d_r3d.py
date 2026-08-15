import json
from pathlib import Path

import numpy as np

from biospur_fusion.imu_multi_action_revision_d.r3d_activity import (
    frame_manifest,
    incremental_activity,
    inject_left_yaw,
    row_hash,
    verify_chain_result_binding,
)
from biospur_fusion.imu_multi_action_revision_d.r3d_synthetic import generate_case, qualify


ROOT = Path(__file__).resolve().parents[3]
CONTRACT = json.loads(
    (ROOT / "Fusion_Part/config/imu_multi_action_revision_d_r3d/R3D_BROAD_ACTIVITY_CONTRACT.json").read_text()
)


def test_q2_frames_are_not_claimed_shared():
    manifest = frame_manifest()
    assert manifest["Q2_DESTINATION_FRAMES_SHARED_ACROSS_NODES"] is False
    assert manifest["analytic_gauge_transform"]["historical_pair_invariant_for_independent_yaw"] is False
    assert manifest["analytic_gauge_transform"]["replacement_increment_invariant_for_constant_independent_yaw"] is True


def test_per_node_increment_is_independent_left_yaw_invariant():
    case = generate_case("true_relative")
    reference = incremental_activity(case["time_ns"], case["rotation"]["CHILD"], case["valid"]["CHILD"], CONTRACT)
    for alpha in CONTRACT["gauge_qualification"]["yaw_injections_rad"]:
        candidate = incremental_activity(case["time_ns"], inject_left_yaw(case["rotation"]["CHILD"], alpha), case["valid"]["CHILD"], CONTRACT)
        assert np.array_equal(np.isfinite(reference["rate_rad_s"]), np.isfinite(candidate["rate_rad_s"]))
        finite = np.isfinite(reference["rate_rad_s"])
        assert np.max(np.abs(reference["rate_rad_s"][finite] - candidate["rate_rad_s"][finite])) <= 1e-12


def test_row_binding_negative_control():
    case = generate_case("true_relative")
    rows = np.array([10, 11, 12, 20], dtype=int)
    key = "synthetic__CHILD__broad_active_rows"
    record = {"action": "synthetic", "node": "CHILD", "array_key": key, "row_hash": row_hash("synthetic", "CHILD", key, rows, case["time_ns"])}
    assert verify_chain_result_binding([record], case["time_ns"], {("synthetic", "CHILD"): rows})
    corrupt = dict(record, array_key="synthetic__PARENT__broad_active_rows")
    assert not verify_chain_result_binding([corrupt], case["time_ns"], {("synthetic", "CHILD"): rows})


def test_full_synthetic_qualification():
    first = qualify(CONTRACT)
    second = qualify(CONTRACT)
    assert first["pass"], first
    assert second["pass"], second
    assert first == second
    assert first["controls"]["common_rigid_motion_not_functional_axis_evidence"]
    assert first["controls"]["strap_slip_is_diagnostic_failure"]
