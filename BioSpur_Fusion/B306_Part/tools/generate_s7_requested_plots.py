#!/usr/bin/env python3
"""Generate the operator-requested 30-minute and motion-event plots."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from analyze_relay8_1_overnight import parse_imu, solve_positions_worker
from batch_g_overnight import NODES
from fusion_session import parse_fields


ACC_SCALE = 2048.0
GYRO_SCALE = 2000.0 / 32768.0
ACTIVITY_FLOOR = 30.0


def log_rows(path: Path, start: float, end: float):
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.split(" ", 3)
            if len(parts) < 4:
                continue
            try:
                host = float(parts[1])
            except ValueError:
                continue
            if start <= host <= end:
                yield host, line, parse_fields(line)


def converted(axes: list[int]) -> np.ndarray:
    return np.asarray(
        [
            axes[0] / ACC_SCALE,
            axes[1] / ACC_SCALE,
            axes[2] / ACC_SCALE,
            axes[3] * GYRO_SCALE,
            axes[4] * GYRO_SCALE,
            axes[5] * GYRO_SCALE,
        ],
        dtype=float,
    )


def find_motion_events(activity: dict[str, list[tuple[float, float]]]) -> dict[str, object]:
    pickup: dict[str, object] = {}
    for node in NODES:
        all_scores = np.asarray([score for _host, score in activity[node]], dtype=float)
        median = float(np.median(all_scores))
        mad = float(np.median(np.abs(all_scores - median)))
        threshold = max(ACTIVITY_FLOOR, median + 8.0 * mad)
        candidates = [(host, score) for host, score in activity[node] if 247900.0 <= host <= 248030.0]
        peak_host, peak_score = max(candidates, key=lambda row: row[1])
        onset_threshold = max(threshold, 0.10 * peak_score)
        local = [
            host
            for host, score in candidates
            if peak_host - 10.0 <= host <= peak_host + 10.0 and score >= onset_threshold
        ]
        pickup[node] = {
            "threshold_raw_gyro_norm": threshold,
            "onset_threshold_raw_gyro_norm": onset_threshold,
            "onset_host": min(local),
            "end_host": max(local),
            "peak_host": peak_host,
            "peak_raw_gyro_norm": peak_score,
        }
    shared_origin = min(float(row["onset_host"]) for row in pickup.values())
    shared_end = max(float(row["end_host"]) for row in pickup.values())

    node = "BSFAA61"
    all_scores = np.asarray([score for _host, score in activity[node]], dtype=float)
    median = float(np.median(all_scores))
    mad = float(np.median(np.abs(all_scores - median)))
    threshold = max(ACTIVITY_FLOOR, median + 8.0 * mad)
    candidates = [(host, score) for host, score in activity[node] if 248530.0 <= host <= 248640.0]
    peak_host, peak_score = max(candidates, key=lambda row: row[1])
    onset_threshold = max(threshold, 0.10 * peak_score)
    active = [
        host
        for host, score in candidates
        if peak_host - 20.0 <= host <= peak_host + 30.0 and score >= onset_threshold
    ]
    loop = {
        "node": node,
        "threshold_raw_gyro_norm": threshold,
        "onset_threshold_raw_gyro_norm": onset_threshold,
        "onset_host": min(active),
        "end_host": max(active),
        "peak_host": peak_host,
        "peak_raw_gyro_norm": peak_score,
    }
    return {
        "pickup": pickup,
        "pickup_shared_origin_host": shared_origin,
        "pickup_shared_end_host": shared_end,
        "large_loop": loop,
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--layout", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "imu_30min").mkdir(exist_ok=True)
    (args.output / "pickup_sequence").mkdir(exist_ok=True)
    (args.output / "large_loop").mkdir(exist_ok=True)

    capture = json.loads(args.capture.read_text(encoding="utf-8"))
    start = float(capture["started_monotonic"])
    end = float(capture["ended_monotonic"])
    layout = json.loads(args.layout.read_text(encoding="utf-8"))
    anchors = np.asarray(
        [[row["x_mm"], row["y_mm"], row["z_mm"]] for row in layout["anchors"]],
        dtype=float,
    )

    # Pass 1: locate the motion events from the complete IMU stream.
    activity: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for host, line, fields in log_rows(args.log, start, end):
        node = fields.get("name")
        if node not in NODES or "FUSION_IMU " not in line:
            continue
        samples = parse_imu(fields)
        if samples:
            score = max(
                math.sqrt(float(a[3] ** 2 + a[4] ** 2 + a[5] ** 2))
                for _offset, a in samples
            )
            activity[node].append((host, score))
    events = find_motion_events(activity)
    (args.output / "motion_event_bounds.json").write_text(
        json.dumps(events, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    pickup_t0 = float(events["pickup_shared_origin_host"])
    pickup_end = float(events["pickup_shared_end_host"])
    pickup_left = pickup_t0 - 2.0
    pickup_right = max(pickup_t0 + 20.0, pickup_end + 4.0)
    loop = events["large_loop"]
    loop_left = float(loop["onset_host"]) - 5.0
    loop_right = float(loop["end_host"]) + 5.0

    # Pass 2: full UWB for the 30-minute 3D result; streaming IMU stats and
    # decimation; full-resolution samples only for the two short events.
    range_frames: dict[str, list[tuple[float, list[int]]]] = defaultdict(list)
    imu_plot: dict[str, list[tuple[int, np.ndarray]]] = defaultdict(list)
    imu_pickup: dict[str, list[tuple[float, np.ndarray]]] = defaultdict(list)
    imu_loop: list[tuple[float, np.ndarray]] = []
    imu_sum = {node: np.zeros(6) for node in NODES}
    imu_sumsq = {node: np.zeros(6) for node in NODES}
    imu_count = {node: 0 for node in NODES}
    decimation = {node: 0 for node in NODES}
    first_node_us: dict[str, int] = {}

    for host, line, fields in log_rows(args.log, start, end):
        node = fields.get("name")
        if node not in NODES:
            continue
        if "FUSION_UWB " in line and "ranges" in fields:
            ranges = [65535] * 8
            for item in fields["ranges"].split(","):
                if ":" not in item:
                    continue
                index, value = item.split(":", 1)
                if 0 <= int(index) < 8:
                    ranges[int(index)] = int(value)
            range_frames[node].append((host, ranges))
        elif "FUSION_IMU " in line:
            samples = parse_imu(fields)
            if not samples:
                continue
            base = int(fields.get("base_us", "0"), 0)
            last_offset = samples[-1][0]
            for offset, axes in samples:
                values = converted(axes)
                node_us = base + offset
                first_node_us.setdefault(node, node_us)
                imu_sum[node] += values
                imu_sumsq[node] += values * values
                imu_count[node] += 1
                if decimation[node] % 30 == 0:
                    imu_plot[node].append((node_us, values))
                decimation[node] += 1
                common_host = host + (offset - last_offset) / 1e6
                if pickup_left <= common_host <= pickup_right:
                    imu_pickup[node].append((common_host, values))
                if node == "BSFAA61" and loop_left <= common_host <= loop_right:
                    imu_loop.append((common_host, values))

    jobs = [(node, anchors.tolist(), range_frames[node]) for node in NODES]
    positions: dict[str, list[tuple[float, float, float, float]]] = {}
    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:
        for node, solved in pool.map(solve_positions_worker, jobs):
            positions[node] = sorted(solved, key=lambda row: row[0])

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # 1. Complete 30-minute UWB 3D plot with RMS in the legend and table.
    position_rows: list[dict[str, object]] = []
    fig = plt.figure(figsize=(14, 10))
    ax = fig.add_subplot(111, projection="3d")
    colors = plt.cm.tab10(np.linspace(0, 1, len(NODES)))
    for color, node in zip(colors, NODES):
        values = np.asarray([row[1:] for row in positions[node]], dtype=float)
        mean = values.mean(axis=0)
        axis_rms = np.sqrt(np.mean((values - mean) ** 2, axis=0))
        radial_rms = float(np.sqrt(np.mean(np.sum((values - mean) ** 2, axis=1))))
        attempted = len(range_frames[node])
        position_rows.append(
            {
                "node": node,
                "attempted": attempted,
                "solved": len(values),
                "mean_x_mm": mean[0],
                "mean_y_mm": mean[1],
                "mean_z_mm": mean[2],
                "rms_x_mm": axis_rms[0],
                "rms_y_mm": axis_rms[1],
                "rms_z_mm": axis_rms[2],
                "rms_radial_mm": radial_rms,
            }
        )
        display = values[:: max(1, len(values) // 3000)]
        ax.scatter(display[:, 0], display[:, 1], display[:, 2], s=2, alpha=0.22, color=color)
        ax.scatter(*mean, s=55, color=color, label=f"{node} RMS={radial_rms:.1f} mm")
    ax.scatter(anchors[:, 0], anchors[:, 1], anchors[:, 2], marker="^", s=85, color="black", label="anchors")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_zlabel("z (mm)")
    ax.set_title("S7 W full 30 min: ten-node UWB 3D point clouds\nRMS is scatter about each node's own 30-min mean; intentional motion included")
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(args.output / "uwb_3d_30min_with_rms.png", dpi=200)
    plt.close(fig)
    with (args.output / "uwb_30min_rms.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(position_rows[0]))
        writer.writeheader()
        writer.writerows(position_rows)

    # 2. Complete 30-minute six-axis plot per IMU.
    imu_rows: list[dict[str, object]] = []
    axis_names = ("ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps")
    for node in NODES:
        count = imu_count[node]
        mean = imu_sum[node] / count
        std = np.sqrt(np.maximum(0.0, imu_sumsq[node] / count - mean * mean))
        row: dict[str, object] = {"node": node, "samples": count, "plot_decimation": 30}
        for index, name in enumerate(axis_names):
            row[f"{name}_mean"] = mean[index]
            row[f"{name}_std"] = std[index]
        imu_rows.append(row)
        data = imu_plot[node]
        t = np.asarray([(node_us - first_node_us[node]) / 60e6 for node_us, _values in data])
        values = np.asarray([values for _node_us, values in data])
        fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
        for index, name in enumerate(axis_names[:3]):
            axes[0].plot(t, values[:, index], lw=0.55, label=name)
        for index, name in enumerate(axis_names[3:], start=3):
            axes[1].plot(t, values[:, index], lw=0.55, label=name)
        axes[0].set_ylabel("acceleration (g)")
        axes[1].set_ylabel("angular rate (deg/s)")
        axes[1].set_xlabel("node elapsed time (min)")
        axes[0].set_xlim(0, 30)
        axes[0].legend(ncol=3)
        axes[1].legend(ncol=3)
        axes[0].grid(alpha=0.2)
        axes[1].grid(alpha=0.2)
        axes[0].set_title(f"{node}: complete S7 W 30-min six-axis IMU (display decimation 30:1)")
        fig.tight_layout()
        fig.savefig(args.output / "imu_30min" / f"{node}_imu_6axis_30min.png", dpi=170)
        plt.close(fig)
    with (args.output / "imu_30min_stats.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(imu_rows[0]))
        writer.writeheader()
        writer.writerows(imu_rows)

    # 3. One common-origin plot per board for the sequential pickup event.
    shared_xlim = (-2.0, pickup_right - pickup_t0)
    pickup_rows: list[dict[str, object]] = []
    for node in NODES:
        event = events["pickup"][node]
        pos = np.asarray(
            [row for row in positions[node] if pickup_left <= row[0] <= pickup_right],
            dtype=float,
        )
        base_pos = np.median(pos[pos[:, 0] < pickup_t0, 1:], axis=0)
        delta = pos[:, 1:] - base_pos
        radial = np.linalg.norm(delta, axis=1)
        imu_data = imu_pickup[node]
        it = np.asarray([host - pickup_t0 for host, _values in imu_data])
        iv = np.asarray([values for _host, values in imu_data])
        pt = pos[:, 0] - pickup_t0
        fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
        for index, name in enumerate(("dx", "dy", "dz")):
            axes[0].plot(pt, delta[:, index], lw=1.0, label=f"{name} mm")
        axes[0].plot(pt, radial, color="black", lw=1.0, label="3D displacement mm")
        for index, name in enumerate(axis_names[:3]):
            axes[1].plot(it, iv[:, index], lw=0.7, label=name)
        for index, name in enumerate(axis_names[3:], start=3):
            axes[2].plot(it, iv[:, index], lw=0.7, label=name)
        peak_t = float(event["peak_host"]) - pickup_t0
        onset_t = float(event["onset_host"]) - pickup_t0
        for axis in axes:
            axis.axvline(onset_t, color="green", ls=":", lw=1, label="detected onset" if axis is axes[0] else None)
            axis.axvline(peak_t, color="red", ls=":", lw=1, label="gyro peak" if axis is axes[0] else None)
            axis.set_xlim(*shared_xlim)
            axis.grid(alpha=0.2)
        axes[0].set_ylabel("UWB displacement (mm)")
        axes[1].set_ylabel("acceleration (g)")
        axes[2].set_ylabel("angular rate (deg/s)")
        axes[2].set_xlabel("seconds from first detected action across all ten boards")
        axes[0].legend(ncol=6, fontsize=8)
        axes[1].legend(ncol=3)
        axes[2].legend(ncol=3)
        axes[0].set_title(f"{node}: sequential pickup on the shared ten-board time axis")
        fig.tight_layout()
        fig.savefig(args.output / "pickup_sequence" / f"{node}_pickup_uwb_imu.png", dpi=170)
        plt.close(fig)
        pickup_rows.append(
            {
                "node": node,
                "onset_s_from_shared_origin": onset_t,
                "peak_s_from_shared_origin": peak_t,
                "end_s_from_shared_origin": float(event["end_host"]) - pickup_t0,
            }
        )
    with (args.output / "pickup_sequence" / "pickup_order.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(pickup_rows[0]))
        writer.writeheader()
        writer.writerows(sorted(pickup_rows, key=lambda row: float(row["onset_s_from_shared_origin"])))

    # 4. Separate BSFAA61 large-loop UWB trajectory and six-axis IMU plots.
    node = "BSFAA61"
    pos = np.asarray([row for row in positions[node] if loop_left <= row[0] <= loop_right], dtype=float)
    loop_t0 = float(loop["onset_host"])
    rel_t = pos[:, 0] - loop_t0
    fig = plt.figure(figsize=(12, 9))
    ax = fig.add_subplot(111, projection="3d")
    ax.plot(
        pos[:, 1],
        pos[:, 2],
        pos[:, 3],
        color="black",
        lw=1.0,
        alpha=0.55,
        label="time-ordered raw trajectory",
        zorder=1,
    )
    scatter = ax.scatter(pos[:, 1], pos[:, 2], pos[:, 3], c=rel_t, cmap="viridis", s=12)
    ax.scatter(*pos[0, 1:], marker="o", s=100, color="green", label="window start")
    ax.scatter(*pos[-1, 1:], marker="X", s=110, color="red", label="window end")
    fig.colorbar(scatter, ax=ax, label="seconds from detected loop onset")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    ax.set_zlabel("z (mm)")
    ax.set_title("BSFAA61 large arm loop: UWB 3D trajectory")
    ax.legend()
    fig.tight_layout()
    fig.savefig(args.output / "large_loop" / "BSFAA61_large_loop_uwb_3d.png", dpi=200)
    plt.close(fig)

    idata = imu_loop
    it = np.asarray([host - loop_t0 for host, _values in idata])
    iv = np.asarray([values for _host, values in idata])
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    for index, name in enumerate(axis_names[:3]):
        axes[0].plot(it, iv[:, index], lw=0.7, label=name)
    for index, name in enumerate(axis_names[3:], start=3):
        axes[1].plot(it, iv[:, index], lw=0.7, label=name)
    axes[0].set_ylabel("acceleration (g)")
    axes[1].set_ylabel("angular rate (deg/s)")
    axes[1].set_xlabel("seconds from detected loop onset")
    axes[0].legend(ncol=3)
    axes[1].legend(ncol=3)
    axes[0].grid(alpha=0.2)
    axes[1].grid(alpha=0.2)
    axes[0].set_title("BSFAA61 large arm loop: six-axis IMU")
    fig.tight_layout()
    fig.savefig(args.output / "large_loop" / "BSFAA61_large_loop_imu_6axis.png", dpi=180)
    plt.close(fig)

    before = pos[(pos[:, 0] >= float(loop["onset_host"]) - 5.0) & (pos[:, 0] <= float(loop["onset_host"]) - 1.0), 1:]
    after = pos[(pos[:, 0] >= float(loop["end_host"]) + 1.0) & (pos[:, 0] <= float(loop["end_host"]) + 5.0), 1:]
    before_mean = np.median(before, axis=0)
    after_mean = np.median(after, axis=0)
    return_delta = after_mean - before_mean
    loop_summary = {
        "detected_onset_host": loop["onset_host"],
        "detected_end_host": loop["end_host"],
        "detected_peak_host": loop["peak_host"],
        "pre_loop_median_mm": before_mean.tolist(),
        "post_loop_median_mm": after_mean.tolist(),
        "return_delta_xyz_mm": return_delta.tolist(),
        "return_displacement_mm": float(np.linalg.norm(return_delta)),
        "uwb_points": len(pos),
        "imu_samples": len(idata),
    }
    (args.output / "large_loop" / "BSFAA61_large_loop_summary.json").write_text(
        json.dumps(loop_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    outputs = sorted(path for path in args.output.rglob("*") if path.is_file())
    with (args.output / "SHA256SUMS").open("w", encoding="utf-8") as handle:
        for path in outputs:
            handle.write(f"{sha256(path)}  {path.relative_to(args.output)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
