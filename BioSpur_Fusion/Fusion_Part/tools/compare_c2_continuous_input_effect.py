#!/usr/bin/env python3
"""Isolate omitted inter-action samples using identical IMU corrections.

This checks the input repair, not UWB fusion quality. Both branches use the
same VQF configuration and IMU samples at every compared timestamp.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation
from vqf import VQF


def window_only_quaternions(acc, gyro, regions, offsets):
    """Reproduce retained filter state with inter-action updates omitted."""
    selected = np.zeros(len(offsets), dtype=bool)
    for region in regions:
        if region["kind"] == "ACTION":
            selected |= ((offsets >= region["start_offset"])
                         & (offsets < region["stop_offset"]))
    result = np.full((len(offsets), 4), np.nan)
    estimator = VQF(0.005, magDistRejectionEnabled=False)
    result[selected] = estimator.updateBatch(
        np.ascontiguousarray(gyro[selected]),
        np.ascontiguousarray(acc[selected]),
    )["quat6D"]
    return selected, result


def acceleration_stats(acc, quaternions):
    world = Rotation.from_quat(quaternions[:, [1, 2, 3, 0]]).apply(acc.copy())
    linear = world - [0.0, 0.0, 9.80665]
    tilt = np.degrees(np.arctan2(np.linalg.norm(world[:, :2], axis=1), world[:, 2]))
    return {
        "samples": len(acc),
        "mean_linear_acceleration_mps2": linear.mean(axis=0).tolist(),
        "rms_linear_acceleration_mps2": float(np.sqrt(np.mean(np.sum(linear**2, axis=1)))),
        "median_acceleration_direction_from_vertical_deg": float(np.median(tilt)),
        # This is the integrator input impulse, not a measured true velocity.
        "nominal_5ms_velocity_increment_mps": (linear.sum(axis=0) * 0.005).tolist(),
    }


def compare(frontend: Path, output: Path):
    manifest = json.loads((frontend / "RESULT.json").read_text())
    result = {
        "role": "MATCHED_INPUT_CONTINUOUS_VS_WINDOW_ONLY_DIAGNOSTIC",
        "frontend": str(frontend.resolve()),
        "only_difference": "INTER_ACTION_IMU_UPDATES_INCLUDED_OR_OMITTED",
        "scientific_pass": False,
        "uwb_fusion_evaluated": False,
        "nodes": {},
    }
    for node in manifest["nodes"]:
        with np.load(frontend / f"{node}.npz", allow_pickle=False) as data:
            acc, gyro = data["acc_mps2"], data["gyro_rads"]
            offsets, time = data["start_offset"], data["time_us"]
            continuous = data["quat_vqf_sensor_wxyz"]
            selected, omitted = window_only_quaternions(acc, gyro, manifest["regions"], offsets)
            dot = np.abs(np.sum(continuous[selected] * omitted[selected], axis=1))
            angular_difference = np.degrees(2.0 * np.arccos(np.clip(dot, 0.0, 1.0)))
            rows = []
            for region in manifest["regions"]:
                if region["kind"] != "ACTION":
                    continue
                indices = np.flatnonzero((offsets >= region["start_offset"])
                                         & (offsets < region["stop_offset"]))
                if len(indices) == 0:
                    raise ValueError(f"missing action samples: {node}/{region['action_id']}")
                first = indices[time[indices] < time[indices[0]] + 2_000_000]
                rows.append({
                    "action": region["action_id"], "action_samples": len(indices),
                    "whole_action_continuous": acceleration_stats(acc[indices], continuous[indices]),
                    "whole_action_omitted_gaps": acceleration_stats(acc[indices], omitted[indices]),
                    "first_two_seconds_continuous": acceleration_stats(acc[first], continuous[first]),
                    "first_two_seconds_omitted_gaps": acceleration_stats(acc[first], omitted[first]),
                })
            result["nodes"][node] = {
                "continuous_samples": len(acc),
                "window_only_samples": int(selected.sum()),
                "restored_inter_action_samples": int((~selected).sum()),
                "whole_calibration_common_rows": {
                    "continuous": acceleration_stats(acc[selected], continuous[selected]),
                    "omitted_gaps": acceleration_stats(acc[selected], omitted[selected]),
                    "quaternion_difference_median_deg": float(np.median(angular_difference)),
                    "quaternion_difference_max_deg": float(np.max(angular_difference)),
                },
                "actions": rows,
            }
    with output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    compare(args.frontend, args.output)
