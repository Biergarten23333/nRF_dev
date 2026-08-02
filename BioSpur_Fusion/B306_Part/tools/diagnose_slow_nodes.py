#!/usr/bin/env python3
"""Offline time-series diagnosis for the five-node 30-minute qualification."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

from fusion_session import parse_fields


NODES = ("BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4")
COLORS = {
    "BSF3C79": "#d62728",
    "BSFC2CC": "#ff7f0e",
    "BSF44AD": "#2ca02c",
    "BSF6C53": "#9467bd",
    "BSF8BC4": "#1f77b4",
}
RAW_RE = re.compile(
    r"^(?P<wall>[0-9.]+) (?P<mono>[0-9.]+) FUSION_RX (?P<line>.*)$"
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def fit_host_from_device(
    points: list[tuple[int, float]],
) -> tuple[float, float, np.ndarray]:
    device = np.asarray([point[0] for point in points], dtype=np.float64)
    host_us = np.asarray(
        [point[1] * 1_000_000.0 for point in points], dtype=np.float64
    )
    slope, intercept = np.polyfit(device, host_us, 1)
    residual = host_us - (slope * device + intercept)
    return float(slope), float(intercept), residual


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    return float(np.percentile(np.asarray(values), fraction * 100.0))


def correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    if np.std(left) == 0.0 or np.std(right) == 0.0:
        return None
    return float(np.corrcoef(left, right)[0, 1])


def counter_at(rows: list[tuple[float, int]], elapsed_s: float) -> int:
    value = 0
    for stamp, current in rows:
        if stamp > elapsed_s:
            break
        value = current
    return value


def counter_buckets(
    rows: list[tuple[float, int]],
    bucket_count: int,
    bucket_s: float,
) -> list[int]:
    return [
        counter_at(rows, (index + 1) * bucket_s)
        - counter_at(rows, index * bucket_s)
        for index in range(bucket_count)
    ]


def detect_counter_events(
    telemetry: list[tuple[float, dict[str, str]]],
    field: str,
) -> list[dict[str, float | int]]:
    previous = 0
    result: list[dict[str, float | int]] = []
    for elapsed, row in telemetry:
        current = int(row.get(field, "0"), 0)
        if current > previous:
            result.append(
                {
                    "elapsed_s": elapsed,
                    "delta": current - previous,
                    "value": current,
                }
            )
        previous = current
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-log", type=Path, required=True)
    parser.add_argument("--analysis-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)

    formal = json.loads(args.analysis_json.read_text())
    start = float(formal["started_monotonic"])
    end = float(formal["ended_monotonic"])
    duration_s = end - start
    bucket_s = 60.0
    # The formal collector runs a few milliseconds beyond exactly 30 minutes.
    # Only complete one-minute buckets are comparable; a millisecond-scale
    # tail must not be divided by its tiny partial width and plotted as Hz.
    bucket_count = int(duration_s // bucket_s)

    telemetry: dict[str, list[tuple[float, dict[str, str]]]] = {
        node: [] for node in NODES
    }
    imu_records: dict[
        str, list[tuple[float, int, list[int]]]
    ] = {node: [] for node in NODES}
    connection_events: list[dict[str, object]] = []

    with args.raw_log.open(errors="replace") as source:
        for raw in source:
            match = RAW_RE.match(raw)
            if match is None:
                continue
            mono = float(match.group("mono"))
            if not start <= mono <= end:
                continue
            elapsed = mono - start
            line = match.group("line")
            fields = parse_fields(line)
            node = fields.get("name")
            if line.startswith("FUSION_TELEMETRY ") and node in telemetry:
                telemetry[node].append((elapsed, fields))
            elif line.startswith("FUSION_IMU ") and node in imu_records:
                deltas = [
                    int(encoded.split(",", 1)[0], 0)
                    for encoded in fields.get("samples", "").split(";")
                    if encoded
                ]
                if deltas:
                    imu_records[node].append(
                        (mono, int(fields["base_us"], 0), deltas)
                    )
            elif line.startswith(
                (
                    "FUSION_CONNECTED ",
                    "FUSION_DISCONNECTED ",
                    "FUSION_CI_CURRENT ",
                    "FUSION_CI_UPDATED ",
                    "FUSION_BRIDGE_READY ",
                )
            ):
                connection_events.append(
                    {"elapsed_s": elapsed, "line": line}
                )

    minute_rows: list[dict[str, object]] = []
    result: dict[str, object] = {
        "window": {
            "start_monotonic": start,
            "end_monotonic": end,
            "duration_s": duration_s,
            "bucket_s": bucket_s,
        },
        "connection_events_in_window": connection_events,
        "per_node": {},
    }
    cumulative: dict[str, list[tuple[float, int]]] = {}
    orphan: dict[str, list[tuple[float, int]]] = {}
    rate: dict[str, list[float]] = {}
    latency_p50: dict[str, list[float]] = {}
    latency_p95: dict[str, list[float]] = {}
    latency_max: dict[str, list[float]] = {}

    for node in NODES:
        cumulative[node] = [(0.0, 0)] + [
            (elapsed, int(row.get("imu_missed_deadlines", "0"), 0))
            for elapsed, row in telemetry[node]
        ]
        orphan[node] = [(0.0, 0)] + [
            (elapsed, int(row.get("orphan_frame", "0"), 0))
            for elapsed, row in telemetry[node]
        ]
        points = [
            (base + max(deltas), mono)
            for mono, base, deltas in imu_records[node]
        ]
        slope, intercept, residual = fit_host_from_device(points)
        residual = residual - float(np.min(residual))

        produced_bucket_counts = [0] * bucket_count
        latency_values: dict[int, list[float]] = defaultdict(list)
        for index, (mono, base, deltas) in enumerate(imu_records[node]):
            for delta in deltas:
                predicted_host_s = (
                    (slope * (base + delta) + intercept) / 1_000_000.0
                )
                bucket = int((predicted_host_s - start) // bucket_s)
                if 0 <= bucket < bucket_count:
                    produced_bucket_counts[bucket] += 1
            host_bucket = int((mono - start) // bucket_s)
            if 0 <= host_bucket < bucket_count:
                latency_values[host_bucket].append(float(residual[index]))

        rate[node] = []
        latency_p50[node] = []
        latency_p95[node] = []
        latency_max[node] = []
        missed_delta = counter_buckets(
            cumulative[node], bucket_count, bucket_s
        )
        orphan_delta = counter_buckets(orphan[node], bucket_count, bucket_s)
        for bucket in range(bucket_count):
            width = min(bucket_s, duration_s - bucket * bucket_s)
            rate_value = produced_bucket_counts[bucket] / width
            latencies = latency_values.get(bucket, [])
            p50 = percentile(latencies, 0.50)
            p95 = percentile(latencies, 0.95)
            maximum = max(latencies) if latencies else None
            rate[node].append(rate_value)
            latency_p50[node].append(float("nan") if p50 is None else p50)
            latency_p95[node].append(float("nan") if p95 is None else p95)
            latency_max[node].append(
                float("nan") if maximum is None else maximum
            )
            minute_rows.append(
                {
                    "node": node,
                    "minute": bucket,
                    "elapsed_start_s": bucket * bucket_s,
                    "elapsed_end_s": min(
                        duration_s, (bucket + 1) * bucket_s
                    ),
                    "imu_effective_hz": rate_value,
                    "missed_deadlines_delta": missed_delta[bucket],
                    "orphan_frame_delta": orphan_delta[bucket],
                    "latency_p50_us": p50,
                    "latency_p95_us": p95,
                    "latency_max_us": maximum,
                }
            )

        x = np.asarray(
            [stamp for stamp, _ in cumulative[node]], dtype=np.float64
        )
        y = np.asarray(
            [value for _, value in cumulative[node]], dtype=np.float64
        )
        linear_slope, linear_intercept = np.polyfit(x, y, 1)
        predicted = linear_slope * x + linear_intercept
        ss_residual = float(np.sum((y - predicted) ** 2))
        ss_total = float(np.sum((y - np.mean(y)) ** 2))
        linear_r2 = 1.0 - ss_residual / ss_total if ss_total else 1.0
        first_five = missed_delta[:5]
        last_five = missed_delta[-5:]
        i2c_events = detect_counter_events(
            telemetry[node], "imu_i2c_err"
        )
        class2_events = detect_counter_events(
            telemetry[node], "imu_hreset"
        )
        result["per_node"][node] = {
            "formal_total_missed_deadlines": formal["per_node"][node][
                "hard_anomaly_deltas"
            ]["imu_missed_deadlines"],
            "telemetry_final_missed_deadlines": cumulative[node][-1][1],
            "formal_total_orphan_frames": formal["per_node"][node][
                "hard_anomaly_deltas"
            ]["orphan_frame"],
            "telemetry_final_orphan_frames": orphan[node][-1][1],
            "cumulative_linear_fit": {
                "slope_missed_per_s": float(linear_slope),
                "r2": linear_r2,
            },
            "missed_per_minute": missed_delta,
            "first_5min_mean_missed_per_minute": (
                float(np.mean(first_five)) if first_five else None
            ),
            "last_5min_mean_missed_per_minute": (
                float(np.mean(last_five)) if last_five else None
            ),
            "effective_hz_per_minute": rate[node],
            "latency_p95_us_per_minute": latency_p95[node],
            "missed_latency_p95_correlation": correlation(
                [float(value) for value in missed_delta],
                [
                    0.0 if math.isnan(value) else value
                    for value in latency_p95[node]
                ],
            ),
            "i2c_error_events": i2c_events,
            "class2_events": class2_events,
            "device_to_host_fit": {
                "host_us_per_device_us": slope,
                "rate_error_ppm": (slope - 1.0) * 1_000_000.0,
            },
        }

    c53_missed = [
        row["missed_deadlines_delta"]
        for row in minute_rows
        if row["node"] == "BSF6C53"
    ]
    c53_orphan = [
        row["orphan_frame_delta"]
        for row in minute_rows
        if row["node"] == "BSF6C53"
    ]
    result["bsf6c53_missed_orphan_correlation"] = correlation(
        [float(value) for value in c53_missed],
        [float(value) for value in c53_orphan],
    )

    with (args.output_dir / "minute_buckets.csv").open(
        "w", newline="", encoding="utf-8"
    ) as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=list(minute_rows[0]))
        writer.writeheader()
        writer.writerows(minute_rows)
    write_json(args.output_dir / "analysis.json", result)

    minutes = np.arange(bucket_count, dtype=np.float64) + 0.5
    fig, axes = plt.subplots(4, 1, figsize=(14, 16), sharex=True)
    for node in NODES:
        total = result["per_node"][node]["formal_total_missed_deadlines"]
        axes[0].plot(
            [stamp / 60.0 for stamp, _ in cumulative[node]],
            [value for _, value in cumulative[node]],
            label=f"{node} (total {total:,})",
            color=COLORS[node],
            linewidth=2,
        )
        for event_field, marker in (
            ("class2_events", "x"),
            ("i2c_error_events", "D"),
        ):
            events = result["per_node"][node][event_field]
            scatter_options = {
                "marker": marker,
                "color": COLORS[node],
                "s": 45,
                "zorder": 5,
            }
            if marker == "D":
                scatter_options["edgecolors"] = "black"
            axes[0].scatter(
                [float(event["elapsed_s"]) / 60.0 for event in events],
                [
                    counter_at(
                        cumulative[node], float(event["elapsed_s"])
                    )
                    for event in events
                ],
                **scatter_options,
            )
        axes[1].plot(
            minutes, rate[node], label=node, color=COLORS[node], linewidth=2
        )
        axes[3].plot(
            minutes,
            np.asarray(latency_p95[node]) / 1000.0,
            label=node,
            color=COLORS[node],
            linewidth=2,
        )
    axes[0].set_ylabel("Cumulative missed deadlines")
    axes[0].set_title("B306 source loss across the formal 30-minute window")
    handles, labels = axes[0].get_legend_handles_labels()
    handles.extend(
        (
            Line2D(
                [0], [0], marker="x", linestyle="None", color="black",
                label="class-2 event",
            ),
            Line2D(
                [0], [0], marker="D", linestyle="None", color="black",
                label="I2C error increment",
            ),
        )
    )
    labels.extend(("class-2 event", "I2C error increment"))
    axes[0].legend(handles, labels, ncol=4, fontsize=9)
    axes[0].grid(alpha=0.25)
    axes[1].axhline(200.0, color="black", linestyle="--", linewidth=1)
    axes[1].set_ylabel("Effective IMU rate (Hz)")
    axes[1].set_title("One-minute source-rate buckets")
    axes[1].grid(alpha=0.25)

    c53_missed_cumulative = cumulative["BSF6C53"]
    c53_orphan_cumulative = orphan["BSF6C53"]
    axes[2].plot(
        [stamp / 60.0 for stamp, _ in c53_missed_cumulative],
        [value for _, value in c53_missed_cumulative],
        color=COLORS["BSF6C53"],
        label="BSF6C53 missed deadlines",
        linewidth=2,
    )
    orphan_axis = axes[2].twinx()
    orphan_axis.plot(
        [stamp / 60.0 for stamp, _ in c53_orphan_cumulative],
        [value for _, value in c53_orphan_cumulative],
        color="#111111",
        label="BSF6C53 unpaired UWB frames",
        linewidth=2,
    )
    axes[2].set_ylabel("Cumulative missed deadlines")
    orphan_axis.set_ylabel("Cumulative unpaired UWB")
    axes[2].set_title("BSF6C53 local IMU loss versus UWB pairing loss")
    axes[2].grid(alpha=0.25)
    lines, labels = axes[2].get_legend_handles_labels()
    lines2, labels2 = orphan_axis.get_legend_handles_labels()
    axes[2].legend(lines + lines2, labels + labels2, loc="upper left")

    axes[3].set_ylabel("Latency residual p95 (ms)")
    axes[3].set_xlabel("Elapsed time (minutes)")
    axes[3].set_title(
        "Per-minute lower-envelope-normalized host-arrival latency"
    )
    axes[3].grid(alpha=0.25)
    axes[3].legend(ncol=3, fontsize=9)
    fig.tight_layout()
    fig.savefig(args.output_dir / "slow_nodes_timeline.png", dpi=160)
    fig.savefig(args.output_dir / "slow_nodes_timeline.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
