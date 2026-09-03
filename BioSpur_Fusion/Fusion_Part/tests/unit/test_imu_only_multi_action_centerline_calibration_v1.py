from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_v1.core import (
    canonical_json_bytes,
    interpolate_rotations_so3,
    olsson_weighted_residual,
)
from biospur_fusion.imu_multi_action_v1.synthetic import (
    ACTIONS,
    generate_synthetic_dataset,
    run_synthetic_truth_gate,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT/"config"/"imu_only_multi_action_centerline_calibration_v1"


def _gates() -> dict:
    return json.loads((CONFIG/"gates_v1.json").read_text(encoding="utf-8"))


def test_action_factor_map_covers_exactly_eleven_calibration_actions() -> None:
    mapping = json.loads((CONFIG/"ACTION_FACTOR_MAP.json").read_text(encoding="utf-8"))
    assert tuple(mapping["actions"]) == tuple(sorted(ACTIONS))
    assert mapping["actions"]["left_heel"]["node_placement_verdict"] == (
        "NOT_SUPPORTED_BY_NODE_PLACEMENT_FOR_FOOT_DOF"
    )
    assert mapping["actions"]["right_heel"]["estimation_factors"] == []
    assert mapping["labels_allowed_during_replay_estimation"] is False


def test_frame_and_gauge_contract_is_single_pelvis_yaw_gauge() -> None:
    frames = json.loads((CONFIG/"FRAME_AND_GAUGE_CONVENTIONS.json").read_text(encoding="utf-8"))
    assert frames["q2_output"] == "R_N_i_from_B_i(t)"
    assert frames["common_global_yaw_gauge"]["count"] == 1
    assert frames["common_global_yaw_gauge"]["fix"] == (
        "pelvis heading at initial reference equals zero"
    )
    assert frames["common_global_yaw_gauge"]["physical_claim"] is False


def test_olsson_residual_matches_declared_weighted_equations() -> None:
    hp = np.array([0.0, 1.0, 0.0])
    hc = np.array([0.0, 1.0, 0.0])
    wp = np.array([[2.0, 3.0, 4.0]])
    wc = np.array([[1.0, 5.0, 2.0]])
    ap = np.array([[0.5, 9.0, 1.5]])
    ac = np.array([[0.1, 8.0, 2.0]])
    gyro_sigma = 0.2
    accel_sigma = 0.5
    actual = olsson_weighted_residual(hp, hc, wp, wc, ap, ac,
                                      gyro_sigma, accel_sigma)
    angular = np.linalg.norm(np.cross(wp[0], hp))-np.linalg.norm(np.cross(wc[0], hc))
    wa = 1.0/math.sqrt(1.0+(np.linalg.norm(ap[0])-np.linalg.norm(ac[0]))**2)
    acceleration = hp@ap[0]-hc@ac[0]
    expected = np.array([
        angular/(math.sqrt(2.0)*gyro_sigma),
        wa*acceleration/(math.sqrt(2.0)*accel_sigma),
    ])
    np.testing.assert_allclose(actual, expected, rtol=0.0, atol=1e-14)


def test_so3_interpolation_uses_timestamps_and_rotation_geometry() -> None:
    source_t = np.array([0, 1_000_000_000], dtype=np.int64)
    source_R = Rotation.from_rotvec(np.array([[0.0, 0.0, 0.0],
                                              [0.0, 0.0, math.pi/2]])).as_matrix()
    got = interpolate_rotations_so3(source_t, source_R,
                                    np.array([250_000_000], dtype=np.int64))[0]
    expected = Rotation.from_euler("z", 22.5, degrees=True).as_matrix()
    np.testing.assert_allclose(got, expected, rtol=0.0, atol=1e-12)


def test_synthetic_generator_has_ten_nodes_continuous_timeline_and_zero_uwb() -> None:
    dataset, truth = generate_synthetic_dataset(_gates())
    assert len(dataset.nodes) == 10
    assert tuple(dataset.action_windows) == ACTIONS
    assert max(truth.mounting_angle_deg.values()) <= 60.0
    first_times = next(iter(dataset.nodes.values())).time_ns
    for stream in dataset.nodes.values():
        np.testing.assert_array_equal(stream.time_ns, first_times)
        assert np.all(np.diff(stream.time_ns) > 0)
    assert not hasattr(next(iter(dataset.nodes.values())), "uwb")


def test_synthetic_gate_is_deterministic_and_stops_on_exact_extra_nullspace() -> None:
    first = run_synthetic_truth_gate(_gates())
    second = run_synthetic_truth_gate(_gates())
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first["verdict"] == "FAIL_SYNTHETIC_RECOVERY"
    assert first["stop_before_real_capture"] is True
    assert first["jacobian_nullity"] == 1
    null = first["null_directions"][0]
    assert null["finite_physical_perturbation"]["classification"] == (
        "TORSO_BOARD_FRAME_AXIAL_ROTATION_VS_INITIAL_RELATIVE_HEADING_TRADEOFF"
    )
    assert {row["name"] for row in null["dominant_parameters"][:4]} == {
        "heading:torso", "frame:torso:x", "frame:torso:y", "frame:torso:z"
    }
    assert null["finite_physical_perturbation"]["residual_delta_l2_norm"] < 1e-8
    assert all(
        max(value["parent_axis_error_deg"], value["child_axis_error_deg"]) <= 2.0
        for value in first["functional_axis_recovery"].values()
    )
