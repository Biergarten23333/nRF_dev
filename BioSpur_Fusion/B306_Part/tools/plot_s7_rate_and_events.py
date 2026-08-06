#!/usr/bin/env python3
"""Create rolling-rate plots and operator-event annotations for S7."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

from analyze_relay8_1_overnight import parse_imu
from batch_g_overnight import NODES
from fusion_session import parse_fields


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    capture = json.loads(args.capture.read_text(encoding="utf-8"))
    start = float(capture["started_monotonic"])
    end = float(capture["ended_monotonic"])
    uwb: dict[str, list[int]] = defaultdict(list)
    imu: dict[str, list[int]] = defaultdict(list)
    activity: dict[str, list[tuple[float, float]]] = defaultdict(list)

    with args.log.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.split(" ", 3)
            if len(parts) < 4:
                continue
            try:
                host = float(parts[1])
            except ValueError:
                continue
            if not start <= host <= end:
                continue
            fields = parse_fields(line)
            node = fields.get("name")
            if node not in NODES:
                continue
            if "FUSION_UWB " in line and "frame_us" in fields:
                uwb[node].append(int(fields["frame_us"], 0))
            elif "FUSION_IMU " in line:
                samples = parse_imu(fields)
                base = int(fields.get("base_us", "0"), 0)
                imu[node].extend(base + offset for offset, _axes in samples)
                if samples:
                    gyro = [
                        math.sqrt(float(a[3] ** 2 + a[4] ** 2 + a[5] ** 2))
                        for _offset, a in samples
                    ]
                    activity[node].append((host, max(gyro)))

    # 60 s trailing windows, emitted every 10 s, in each B306's TIMER2 domain.
    # This rejects USB/CDC backlog bunching while retaining real missing
    # records/samples because the node clock continues across those gaps.
    rows: list[dict[str, object]] = []
    for node in NODES:
        # Host delivery may reorder buffered records (notably BSF44AD in this
        # run); bisect requires source-time ordering.
        uwb[node].sort()
        imu[node].sort()
    times = np.arange(60.0, (end - start) + 0.001, 10.0)
    for stop in times:
        begin = stop - 60.0
        for node in NODES:
            u0 = uwb[node][0] if uwb[node] else 0
            i0 = imu[node][0] if imu[node] else 0
            u_begin = u0 + int(begin * 1e6)
            u_stop = u0 + int(stop * 1e6)
            i_begin = i0 + int(begin * 1e6)
            i_stop = i0 + int(stop * 1e6)
            u_count = bisect.bisect_right(uwb[node], u_stop) - bisect.bisect_left(uwb[node], u_begin)
            i_count = bisect.bisect_right(imu[node], i_stop) - bisect.bisect_left(imu[node], i_begin)
            rows.append(
                {
                    "minute": stop / 60.0,
                    "node": node,
                    "uwb_hz": u_count / 60.0,
                    "imu_hz": i_count / 60.0,
                    "uwb_records": u_count,
                    "imu_samples": i_count,
                }
            )
    with (args.output / "rate_vs_time_60s.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
    colors = plt.cm.tab10(np.linspace(0, 1, len(NODES)))
    for color, node in zip(colors, NODES):
        selected = [row for row in rows if row["node"] == node]
        x = [float(row["minute"]) for row in selected]
        axes[0].plot(x, [float(row["uwb_hz"]) for row in selected], label=node, color=color, lw=1.2)
        axes[1].plot(x, [float(row["imu_hz"]) for row in selected], label=node, color=color, lw=1.2)
    axes[0].axhline(8.25, color="black", ls="--", lw=1, label="UWB gate 8.25 Hz")
    axes[1].axhline(199.8, color="black", ls="--", lw=1, label="IMU gate 199.8 Hz")
    event_lines = (
        (248006.04, "all 10 picked up (reported)"),
        (248612.50, "BSFAA61 arm loop (reported)"),
    )
    for when, label in event_lines:
        minute = (when - start) / 60.0
        for axis in axes:
            axis.axvline(minute, color="grey", ls=":", lw=1)
        axes[0].text(minute, axes[0].get_ylim()[1], label, rotation=90, va="top", ha="right", fontsize=8)
    axes[0].set_ylabel("UWB delivered rate (Hz)")
    axes[1].set_ylabel("IMU delivered rate (samples/s)")
    axes[1].set_xlabel("S7 W elapsed time (min)")
    axes[0].set_title("Ten-node W: 60 s rolling UWB rate")
    axes[1].set_title("Ten-node W: 60 s rolling IMU rate")
    axes[0].legend(ncol=4, fontsize=7)
    axes[1].legend(ncol=4, fontsize=7)
    axes[0].grid(alpha=0.2)
    axes[1].grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(args.output / "rate_vs_time_60s.png", dpi=180)
    plt.close(fig)

    # Data-derived event peaks.  The chat timestamp is only a search bound;
    # each node's peak is located from gyro magnitude in the raw IMU data.
    windows = {
        "sequential_pickup_all10": (max(start, 247900.0), 248030.0, list(NODES)),
        "BSFAA61_arm_loop": (248550.0, 248630.0, ["BSFAA61"]),
    }
    events: dict[str, object] = {}
    for label, (a, b, nodes) in windows.items():
        found = {}
        for node in nodes:
            points = [(t, score) for t, score in activity[node] if a <= t <= b]
            baseline = np.asarray([score for _t, score in activity[node]], dtype=float)
            median = float(np.median(baseline)) if baseline.size else 0.0
            mad = float(np.median(np.abs(baseline - median))) if baseline.size else 0.0
            threshold = max(30.0, median + 8.0 * mad)
            peak = max(points, key=lambda row: row[1]) if points else (None, None)
            active = [t for t, score in points if score >= threshold]
            found[node] = {
                "threshold_raw_gyro_norm": threshold,
                "peak_host_monotonic": peak[0],
                "peak_raw_gyro_norm": peak[1],
                "first_threshold_crossing": min(active) if active else None,
                "last_threshold_crossing": max(active) if active else None,
                "detected": bool(active),
            }
        events[label] = {"search_window": [a, b], "nodes": found}
    (args.output / "operator_event_detection.json").write_text(
        json.dumps(events, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
