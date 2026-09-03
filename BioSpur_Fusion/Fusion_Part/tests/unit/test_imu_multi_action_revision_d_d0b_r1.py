import json
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.imu_multi_action_revision_d.d0b_r1_generator import SEGMENTS
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_model import (
    PRODUCT_DIMENSION,
    articulated_pose_directions,
    blind_initialization,
    product_layout,
)
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_replay import (
    NODE_NAMES,
    calibration_payload,
    replay,
)


def contract():
    path = Path(__file__).parents[2] / "config/imu_multi_action_revision_d_d0b_r1/R1_SYNTHETIC_MODEL_CONTRACT.json"
    return json.loads(path.read_text())


def nominal_product():
    value = np.zeros(PRODUCT_DIMENSION)
    # Board longitudinal axes avoid the S2 chart's pole; functional axes +X.
    for index in range(10):
        value[2 * index + 1] = -np.pi / 2 if index >= 2 else np.pi / 2
    return value


def replay_input(count=8):
    time_ns = np.arange(count, dtype=np.int64) * 20_000_000
    rotation = np.tile(np.eye(3), (count, 10, 1, 1))
    gyro = np.zeros((count, 10, 3))
    gyro[:, 4, 0] = 0.4
    valid = np.ones((count, 10), bool)
    nodes = tuple(f"N{index}" for index in range(10))
    mapping = {node: segment for node, segment in zip(nodes, SEGMENTS)}
    return dict(time_ns=time_ns, rotation=rotation, gyro_rad_s=gyro, valid=valid, node_order=nodes, node_to_segment=mapping,
                lengths=contract()["generic_rendering_lengths_m"], maximum_gap_s=0.03)


def test_product_removes_all_joint_zero_coordinates():
    layout = product_layout()
    assert layout[-1]["stop"] == 47
    assert not any("zero" in item["name"] for item in layout)
    assert contract()["r1_product"]["joint_zero"]["disposition"] == "CAPTURE_DEFINED_REPORTING_CONVENTION_NOT_ESTIMATED"


def test_static_pose_is_one_shared_articulated_tree_not_ten_free_directions():
    neutral = articulated_pose_directions("t_pose", np.zeros(17))
    root_changed = articulated_pose_directions("t_pose", np.r_[0.0, 0.1, 0.0, np.zeros(14)])
    assert all(not np.allclose(neutral[name], root_changed[name]) for name in SEGMENTS)
    shoulder = np.zeros(17); shoulder[5] = 0.2
    shoulder_changed = articulated_pose_directions("t_pose", shoulder)
    assert not np.allclose(neutral["upper_arm_L"], shoulder_changed["upper_arm_L"])
    assert not np.allclose(neutral["forearm_L"], shoulder_changed["forearm_L"])
    assert np.allclose(neutral["upper_arm_R"], shoulder_changed["upper_arm_R"])


def test_replay_is_label_blind_and_parameter_interventions_change_physics():
    x = nominal_product()
    args = replay_input()
    base = replay(calibration_payload(x, "0" * 64), **args)
    assert base["graphical_nodes"].shape == (8, len(NODE_NAMES), 3)

    changed_axis = x.copy(); changed_axis[5] += 0.2
    axis_output = replay(calibration_payload(changed_axis, "0" * 64), **args)
    assert not np.allclose(base["segment_directions"][:, 2], axis_output["segment_directions"][:, 2])
    assert not np.allclose(base["graphical_nodes"], axis_output["graphical_nodes"])

    changed_function = x.copy(); changed_function[33] += 0.3
    function_output = replay(calibration_payload(changed_function, "0" * 64), **args)
    assert not np.allclose(base["joint_coordinates"], function_output["joint_coordinates"])

    forbidden = calibration_payload(x, "0" * 64); forbidden["actions"] = ["arms"]
    with pytest.raises(ValueError, match="forbidden"):
        replay(forbidden, **args)


def test_replay_negative_control_delete_actual_parameter_read_fails():
    payload = calibration_payload(nominal_product(), "0" * 64)
    del payload["product_coordinates"]
    with pytest.raises(KeyError):
        replay(payload, **replay_input())
