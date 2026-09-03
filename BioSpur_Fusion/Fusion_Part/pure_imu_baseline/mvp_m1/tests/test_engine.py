from __future__ import annotations

import numpy as np

from pure_imu_baseline.mvp_m1.adapters import replay_packets
from pure_imu_baseline.mvp_m1.config import load_config
from pure_imu_baseline.mvp_m1.engine import (COMMAND_CLEAR, COMMAND_RECENTER,
    GaugeCommand, PoseEngine, apply_global_gauge, normalized_working_view, qz)


def fixture(n=120):
    t = np.arange(n)/60.0
    q = np.zeros((n, 10, 4), np.float32); q[..., 0] = 1
    q[:, 1] = qz(0.4).astype(np.float32)
    valid = np.ones((n, 10), bool); reset = np.zeros_like(valid); reset[0] = True
    positions = np.zeros((n, 14, 3), np.float32); positions[:, :, 0] = np.arange(14)
    return {"time_s": t, "q_GB_wxyz": q, "valid": valid, "filter_reset": reset,
            "joint_positions_m": positions, "joint_available": np.ones((n, 14), bool),
            "segment_names": np.array(["torso","pelvis","upper_arm_left","forearm_left","upper_arm_right","forearm_right","thigh_left","shank_left","thigh_right","shank_right"])}


def test_explicit_common_gauge_only():
    raw = fixture(); out = PoseEngine(load_config()).process(raw, [GaugeCommand(30, COMMAND_RECENTER), GaugeCommand(90, COMMAND_CLEAR)])
    assert out["global_yaw_gauge_epoch"][29] == 0
    assert out["global_yaw_gauge_epoch"][30] == 1
    assert out["global_yaw_gauge_epoch"][90] == 2
    assert np.nanmax(np.abs(out["working_q_PC_wxyz"]-out["display_q_PC_wxyz"])) <= 1e-12
    assert len(out["manual_recenter_events"]) == 2


def test_reset_cannot_trigger_gauge():
    raw = fixture(); raw["filter_reset"][60] = True
    out = PoseEngine(load_config()).process(raw)
    assert np.all(out["global_yaw_gauge_rad"] == 0)
    assert np.all(out["global_yaw_gauge_epoch"] == 0)


def test_working_view_does_not_mutate_raw():
    raw = fixture(); before = raw["q_GB_wxyz"].copy()
    work = normalized_working_view(raw["q_GB_wxyz"], raw["valid"])
    assert np.array_equal(before, raw["q_GB_wxyz"])
    assert np.nanmax(abs(np.linalg.norm(work, axis=-1)-1)) <= 1e-12


def test_translation_is_not_applied():
    raw = fixture(); work = normalized_working_view(raw["q_GB_wxyz"], raw["valid"])
    gamma = np.full(len(raw["time_s"]), 0.6)
    _, positions = apply_global_gauge(work, raw["joint_positions_m"], raw["valid"], raw["joint_available"], gamma)
    assert np.array_equal(positions[:, 0], raw["joint_positions_m"][:, 0])


def test_streaming_packet_path_matches_batch_default_gauge():
    raw = fixture(8)
    batch = PoseEngine(load_config()).process(raw)
    stream = PoseEngine(load_config())
    rows = [stream.process_packet(packet) for packet in replay_packets(raw)]
    assert np.allclose(np.stack([row["working_q_GB_wxyz"] for row in rows]), batch["working_q_GB_wxyz"], equal_nan=True)
    assert np.allclose(np.stack([row["display_joint_positions_m"] for row in rows]), batch["display_joint_positions_m"], equal_nan=True)
    assert np.array_equal(np.stack([row["epoch_per_node"] for row in rows]), batch["epoch_per_node"])
    assert np.array_equal(np.stack([row["quality_state"] for row in rows]), batch["quality_state"])
