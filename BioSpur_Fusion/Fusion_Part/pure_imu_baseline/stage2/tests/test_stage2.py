from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np

from pure_imu_baseline.config import NODE_ORDER, PARENT_CHILD, SEGMENT_ORDER
from pure_imu_baseline.math3d import conjugate, from_axis_angle, multiply, normalize
from pure_imu_baseline.skeleton import JOINT_NAMES, forward_kinematics, relative_quaternions
from pure_imu_baseline.stage2.diagnostics import (analyze_capture,
    model_and_projection_diagnostics, quaternion_angle_rad,
    quaternion_tilt_rad, quaternion_yaw_rad)
from pure_imu_baseline.stage2.exporter import (_capture_page, _viewer_arrays,
    pack_arrays, unpack_arrays)
from pure_imu_baseline.stage2.pipeline import _correction_contract


def _synthetic_data(extra_forearm_yaw: bool = False, pelvis_gap: bool = False):
    n = 241
    t = np.arange(n)/60.0
    yaw = 0.5*t/t[-1]
    q = np.stack([np.cos(yaw/2), np.zeros(n), np.zeros(n), np.sin(yaw/2)], axis=1)
    q_gb = np.repeat(q[:, None, :], len(SEGMENT_ORDER), axis=1)
    if extra_forearm_yaw:
        index = SEGMENT_ORDER.index("forearm_left")
        extra = np.stack([np.cos(yaw/2), np.zeros(n), np.zeros(n), np.sin(yaw/2)], axis=1)
        q_gb[:, index] = normalize(multiply(q_gb[:, index], extra))
    valid = np.ones((n, len(SEGMENT_ORDER)), dtype=bool)
    reset = np.zeros_like(valid)
    reset[0] = True
    if pelvis_gap:
        index = SEGMENT_ORDER.index("pelvis")
        valid[100:131, index] = False
        reset[131, index] = True
    relative_q, relative_valid = relative_quaternions(q_gb, valid)
    positions, available = forward_kinematics(q_gb, valid)
    q_gb = q_gb.astype(np.float32)
    q_gb[~valid] = np.nan
    return {
        "time_s": t,
        "node_ids": np.array(NODE_ORDER),
        "segment_names": np.array(SEGMENT_ORDER),
        "q_GB_wxyz": q_gb,
        "valid": valid,
        "filter_reset": reset,
        "relative_names": np.array([f"{a}->{b}" for a, b in PARENT_CHILD]),
        "q_parent_child_wxyz": relative_q,
        "relative_valid": relative_valid,
        "joint_names": np.array(JOINT_NAMES),
        "joint_positions_m": positions,
        "joint_available": available,
    }


def test_quaternion_diagnostic_conventions():
    q = from_axis_angle([0, 0, 1], np.pi/2)
    assert np.isclose(quaternion_yaw_rad(q), np.pi/2)
    assert np.isclose(quaternion_tilt_rad(q), 0)
    assert np.isclose(quaternion_angle_rad(q, -q), 0)


def test_common_yaw_and_relative_heading_are_separated():
    shared = _synthetic_data()
    diagnostics, metrics, _ = analyze_capture("1", shared)
    assert abs(diagnostics["common_global_yaw"]["net_change_deg"]-np.rad2deg(0.5)) < 0.1
    assert np.nanmax(np.abs(metrics["inter_segment_heading_spread_deg"])) < 1e-3

    articulated = _synthetic_data(extra_forearm_yaw=True)
    _, metrics, _ = analyze_capture("1", articulated)
    assert np.nanmax(metrics["inter_segment_heading_spread_deg"]) > 10


def test_gap_is_marked_and_not_fabricated():
    data = _synthetic_data(pelvis_gap=True)
    diagnostics, _, events = analyze_capture("1", data)
    gaps = diagnostics["gap_reset_effects"]
    assert len(gaps) == 1
    assert gaps[0]["node_id"] == "BSFC2CC"
    assert gaps[0]["unobserved_interval_s"] > 0.5
    assert any(event["type"] == "gap" for event in events)


def test_projection_does_not_mutate_model_geometry():
    data = _synthetic_data(extra_forearm_yaw=True)
    before = data["joint_positions_m"].copy()
    result = model_and_projection_diagnostics(data)
    assert np.array_equal(before, data["joint_positions_m"], equal_nan=True)
    assert result["maximum_abs_bone_length_error_m"] < 2e-6
    upper = result["bones"]["upper_arm_left"]
    assert upper["model_length_m"]["maximum"]-upper["model_length_m"]["minimum"] < 2e-6
    shoulder = result["bones"]["shoulder_width"]
    assert shoulder["camera_projection"]["WORLD_FIXED_FRONT"]["apparent_to_true_ratio"]["minimum"] < 0.99


def test_typed_array_payload_roundtrip_is_exact():
    data = _synthetic_data()
    _, metrics, _ = analyze_capture("1", data)
    arrays = _viewer_arrays(data, metrics)
    payload, schema = pack_arrays(arrays)
    compressed = gzip.compress(payload, mtime=0)
    decoded = unpack_arrays(gzip.decompress(compressed), schema)
    for name, original in arrays.items():
        assert np.array_equal(original, decoded[name], equal_nan=True)


def test_viewer_page_contains_every_required_control_and_camera():
    metadata = {"capture": "1", "frames": 2, "duration_s": 1.0, "display_rate_hz": 1,
                "arrays": {}, "node_ids": list(NODE_ORDER), "segment_names": list(SEGMENT_ORDER),
                "joint_names": list(JOINT_NAMES), "edges": [], "segment_origins": [], "events": [],
                "calibration_window_s": [0, 1], "ghost_frame": 0, "ground_z_m": -0.85,
                "colors": {}, "frame_contract": {}, "payload": {}}
    page = _capture_page("1", metadata)
    for control in ("capture-select", "play-pause", "step-back", "step-forward", "speed-select",
                    "camera-mode", "reset-camera", "timeline", "timestamp-input", "jump-time",
                    "event-select", "jump-event", "toggle-joints", "toggle-segments", "toggle-sensors",
                    "toggle-global-axes", "toggle-body-axes", "toggle-segment-frames",
                    "toggle-ground", "toggle-ghost"):
        assert f'id="{control}"' in page
    for mode in ("WORLD_FIXED_FRONT", "WORLD_FIXED_SIDE", "WORLD_FIXED_TOP", "FREE_ORBIT",
                 "PELVIS_FRONT_LOCKED", "PELVIS_SIDE_LOCKED"):
        assert mode in page
    assert "http://" not in page and "https://" not in page


def test_frozen_stage1_deterministic_samples_are_finite():
    path = Path("/tmp/biospur_pure_imu_baseline_c123_20260823T091031Z/CAPTURE1_REPLAY_DATA.npz")
    with np.load(path, allow_pickle=False) as archive:
        q = archive["q_GB_wxyz"]
        p = archive["joint_positions_m"]
        valid = archive["valid"]
        available = archive["joint_available"]
    for frame in (120, len(q)//2, len(q)-120):
        assert np.all(np.isfinite(q[frame][valid[frame]]))
        assert np.allclose(np.linalg.norm(q[frame][valid[frame]], axis=1), 1, atol=2e-6)
        assert np.all(np.isfinite(p[frame][available[frame]]))


def test_correction_contract_freezes_raw_evidence_and_gaps():
    contract = _correction_contract()
    text = " ".join(contract["immutable_inputs"] + contract["required_behavior"] + contract["forbidden_behavior"])
    assert "raw VQF q_GS" in text
    assert "raw q_GB" in text
    assert "never bridge unavailable samples" in text
    assert contract["status"] == "FROZEN_FOR_NEXT_SOFTWARE_ONLY_STAGE"
