from __future__ import annotations

import numpy as np

from biospur_fusion.calibration_v2.phase2r.segmentation import segment_cycles


def synthetic_node(repetitions=5, correction=True, fatigue=True):
    t = np.arange(0, 30, .005)
    phase = 2 * np.pi * repetitions * t / 30
    gyro = np.column_stack([1200 * np.sin(phase), 300 * np.cos(phase), 100 * np.sin(.5 * phase)])
    if correction:
        gyro[(t > 12.2) & (t < 12.7), 2] += 1800
    if fatigue:
        gyro *= np.linspace(1, .7, len(t))[:, None]
    acc = np.column_stack([300 * np.sin(phase), 200 * np.cos(phase), np.full(len(t), 16384)])
    return {"timer2_us": (t * 1e6).astype(np.int64), "gyro_raw": gyro, "acc_raw": acc}


def test_variable_repetition_and_correction_not_forced_to_three():
    imu = {f"N{i}": synthetic_node(repetitions=5 + i % 2) for i in range(10)}
    result = segment_cycles(imu, "04_shoulder_left")
    assert result["assumed_repetition_count"] is None
    assert len(result["cycles"]) != 3
    assert result["unassigned_intervals"]


def test_standing_not_hard_zeroed_or_given_fake_cycles():
    imu = {f"N{i}": synthetic_node() for i in range(10)}
    result = segment_cycles(imu, "00_initial_still")
    assert result["cycles"] == []
    assert result["status"] == "LOW_DYNAMIC_CONTEXT_NO_CYCLE_ASSUMPTION"


def test_segmentation_sensitivity_changes_boundaries_not_raw_lineage():
    imu = {f"N{i}": synthetic_node() for i in range(10)}
    low = segment_cycles(imu, "06_elbow_left", .8)
    high = segment_cycles(imu, "06_elbow_left", 1.2)
    assert low["settings_scale"] != high["settings_scale"]
    assert all(c["boundary_uncertainty_s"] > 0 for c in low["cycles"])
