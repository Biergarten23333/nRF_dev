#!/usr/bin/env python3
"""Regenerate R6A2B-R2 angular plots without connecting window gaps."""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from biospur_fusion.root_r6a0.math3d import so3_log


def rotation_magnitude_deg(rotations: np.ndarray) -> np.ndarray:
    flat = rotations.reshape((-1, 3, 3))
    values = np.asarray([np.linalg.norm(so3_log(rotation)) for rotation in flat])
    return np.degrees(values.reshape(rotations.shape[:-2]))


def replot(result: Path) -> None:
    replay = np.load(result / "CALIBRATION_WINDOW_REPLAY.npz", allow_pickle=False)
    times = replay["time_ns"]
    windows = replay["window"]
    node_names = replay["node_names"]
    segment_names = replay["segment_names"]
    joint_names = replay["joint_names"]
    relative_time = (times - times[0]) * 1e-9
    node_angle = rotation_magnitude_deg(replay["node_rotation"])
    segment_angle = rotation_magnitude_deg(replay["segment_rotation"])
    joint_angle = np.degrees(np.linalg.norm(replay["joint_rotvec"], axis=2))
    labels = list(dict.fromkeys(str(value) for value in windows))

    orientation_limit = max(1.0, float(max(np.max(node_angle), np.max(segment_angle))) * 1.02)
    figure, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    for series_index, name in enumerate(node_names):
        for window_index, window in enumerate(labels):
            mask = windows == window
            axes[0].plot(relative_time[mask], node_angle[mask, series_index], lw=.55,
                         label=str(name) if window_index == 0 else None)
    for series_index, name in enumerate(segment_names):
        for window_index, window in enumerate(labels):
            mask = windows == window
            axes[1].plot(relative_time[mask], segment_angle[mask, series_index], lw=.55,
                         label=str(name) if window_index == 0 else None)
    boundaries = [relative_time[np.flatnonzero(windows == window)[0]] for window in labels[1:]]
    for axis in axes:
        for boundary in boundaries:
            axis.axvline(boundary, color="black", lw=.35, alpha=.25)
        axis.set_ylim(0, orientation_limit); axis.grid(alpha=.25); axis.legend(ncol=5, fontsize=6)
    axes[0].set_ylabel("node orientation |log R| [deg]")
    axes[1].set_ylabel("segment |log R| [deg]"); axes[1].set_xlabel("capture-global offset [s]")
    figure.suptitle("R6A2B-R2 native-time orientation replay | window gaps not connected | one fixed scale")
    figure.tight_layout(); figure.savefig(result / "ORIENTATION_TRAJECTORIES_FIXED_SCALE.png", dpi=170); plt.close(figure)

    joint_limit = max(1.0, float(np.max(joint_angle)) * 1.02)
    figure, axis = plt.subplots(figsize=(14, 5))
    for series_index, name in enumerate(joint_names):
        for window_index, window in enumerate(labels):
            mask = windows == window
            axis.plot(relative_time[mask], joint_angle[mask, series_index], lw=.65,
                      label=str(name) if window_index == 0 else None)
    for boundary in boundaries:
        axis.axvline(boundary, color="black", lw=.35, alpha=.25)
    axis.set_ylim(0, joint_limit); axis.set_xlabel("capture-global offset [s]")
    axis.set_ylabel("principal joint rotvec magnitude [deg]"); axis.grid(alpha=.25)
    axis.legend(ncol=5, fontsize=7)
    axis.set_title("Body-relative joint trajectories | window gaps not connected | fixed scale")
    figure.tight_layout(); figure.savefig(result / "JOINT_ANGLES_FIXED_SCALE.png", dpi=170); plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    replot(args.result.resolve())


if __name__ == "__main__":
    main()
