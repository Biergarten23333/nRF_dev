#!/usr/bin/env python3
"""Reconstruct missed IMU deadlines and test their 50 ms phase structure."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


FIELD_RE = re.compile(r"(\w+)=([^ ]+)")
NODES = ("BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4")
PERIOD_US = 5_000
CONNECTION_INTERVAL_US = 50_000


def parse_fields(line: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in FIELD_RE.finditer(line)}


def shortest_circular_width(values: np.ndarray, fraction: float) -> float | None:
    if values.size == 0:
        return None
    ordered = np.sort(np.mod(values, CONNECTION_INTERVAL_US))
    count = math.ceil(fraction * ordered.size)
    doubled = np.concatenate((ordered, ordered + CONNECTION_INTERVAL_US))
    widths = doubled[count - 1 : count - 1 + ordered.size] - doubled[: ordered.size]
    return float(np.min(widths))


def circular_mean(values: np.ndarray) -> tuple[float | None, float | None]:
    if values.size == 0:
        return None, None
    vectors = np.exp(
        2j * np.pi * np.mod(values, CONNECTION_INTERVAL_US)
        / CONNECTION_INTERVAL_US
    )
    mean = np.mean(vectors)
    phase = float(
        (np.angle(mean) % (2.0 * np.pi))
        * CONNECTION_INTERVAL_US
        / (2.0 * np.pi)
    )
    return phase, float(abs(mean))


def fit_migration(
    elapsed_s: np.ndarray, phases_us: np.ndarray
) -> dict[str, float | None]:
    if phases_us.size < 10:
        return {
            "slope_us_per_s": None,
            "clock_offset_ppm": None,
            "onset_resultant": None,
            "corrected_center_us": None,
        }
    slopes = np.linspace(-100.0, 100.0, 4_001)
    best_slope = 0.0
    best_resultant = -1.0
    best_center = 0.0
    for slope in slopes:
        corrected = np.mod(
            phases_us - slope * elapsed_s, CONNECTION_INTERVAL_US
        )
        center, resultant = circular_mean(corrected)
        assert center is not None and resultant is not None
        if resultant > best_resultant:
            best_slope = float(slope)
            best_resultant = float(resultant)
            best_center = float(center)
    return {
        "slope_us_per_s": best_slope,
        # One microsecond of phase migration per second is one ppm.
        "clock_offset_ppm": best_slope,
        "onset_resultant": best_resultant,
        "corrected_center_us": best_center,
    }


def load_run(raw_path: Path, analysis_path: Path) -> dict[str, object]:
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    start = float(analysis["started_monotonic"])
    end = float(analysis["ended_monotonic"])
    records: dict[str, list[dict[str, object]]] = defaultdict(list)
    unpaired: dict[str, list[int]] = defaultdict(list)
    first_device_ts: dict[str, int] = {}

    with raw_path.open(encoding="utf-8", errors="replace") as source:
        for raw in source:
            split = raw.rstrip().split(" FUSION_RX ", 1)
            if len(split) != 2:
                continue
            prefix, payload = split
            prefix_fields = prefix.split()
            if len(prefix_fields) < 2:
                continue
            host_monotonic = float(prefix_fields[1])
            if not start <= host_monotonic <= end:
                continue
            parsed = parse_fields(payload)
            node = parsed.get("name")
            if node not in NODES:
                continue

            if payload.startswith("FUSION_IMU "):
                base = int(parsed["base_us"], 0)
                samples = [
                    base + int(encoded.split(",", 1)[0], 0)
                    for encoded in parsed["samples"].split(";")
                    if encoded
                ]
                if not samples:
                    continue
                first_device_ts.setdefault(node, samples[0])
                records[node].append(
                    {
                        "host_monotonic": host_monotonic,
                        "sequence": int(parsed["seq"], 0),
                        "count": int(parsed["n"], 0),
                        "samples": samples,
                    }
                )
            elif (
                payload.startswith("FUSION_UWB ")
                and parsed.get("verdict") != "healthy"
                and parsed.get("frame_us") not in (None, "-")
            ):
                unpaired[node].append(int(parsed["frame_us"], 0))

    nodes: dict[str, dict[str, object]] = {}
    for node in NODES:
        missing: list[int] = []
        episodes: list[dict[str, int]] = []
        sequence_discontinuities = 0
        previous: dict[str, object] | None = None
        for record in records.get(node, []):
            samples = list(record["samples"])
            pairs = list(zip(samples, samples[1:]))
            if previous is not None:
                expected = (
                    int(previous["sequence"]) + int(previous["count"])
                ) & 0xFFFF
                if int(record["sequence"]) == expected:
                    pairs.insert(0, (int(previous["last_sample"]), samples[0]))
                else:
                    # Health recovery discards a partial batch and resets the
                    # deadline lattice. It is not imu_missed_deadlines.
                    sequence_discontinuities += 1
            for before, after in pairs:
                delta = after - before
                if delta <= PERIOD_US or delta % PERIOD_US != 0:
                    continue
                episode_points = list(
                    range(before + PERIOD_US, after, PERIOD_US)
                )
                if not episode_points:
                    continue
                missing.extend(episode_points)
                episodes.append(
                    {
                        "onset_us": episode_points[0],
                        "missing_count": len(episode_points),
                        "duration_us": len(episode_points) * PERIOD_US,
                    }
                )
            previous = {
                "sequence": record["sequence"],
                "count": record["count"],
                "last_sample": samples[-1],
            }

        origin = first_device_ts.get(node)
        missing_array = np.asarray(missing, dtype=np.float64)
        onset_array = np.asarray(
            [episode["onset_us"] for episode in episodes], dtype=np.float64
        )
        orphan_array = np.asarray(unpaired.get(node, []), dtype=np.float64)
        if origin is None:
            missing_elapsed = np.asarray([], dtype=np.float64)
            onset_elapsed = np.asarray([], dtype=np.float64)
            orphan_elapsed = np.asarray([], dtype=np.float64)
        else:
            missing_elapsed = (missing_array - origin) / 1_000_000.0
            onset_elapsed = (onset_array - origin) / 1_000_000.0
            orphan_elapsed = (orphan_array - origin) / 1_000_000.0
        missing_phase = np.mod(missing_array, CONNECTION_INTERVAL_US)
        onset_phase = np.mod(onset_array, CONNECTION_INTERVAL_US)
        orphan_phase = np.mod(orphan_array, CONNECTION_INTERVAL_US)
        migration = fit_migration(onset_elapsed, onset_phase)
        slope = migration["slope_us_per_s"]

        corrected_onsets = onset_phase
        corrected_missing = missing_phase
        corrected_orphans = orphan_phase
        if slope is not None:
            corrected_onsets = np.mod(
                onset_phase - float(slope) * onset_elapsed,
                CONNECTION_INTERVAL_US,
            )
            corrected_missing = np.mod(
                missing_phase - float(slope) * missing_elapsed,
                CONNECTION_INTERVAL_US,
            )
            corrected_orphans = np.mod(
                orphan_phase - float(slope) * orphan_elapsed,
                CONNECTION_INTERVAL_US,
            )
        orphan_center, orphan_resultant = circular_mean(corrected_orphans)
        orphan_migration = fit_migration(orphan_elapsed, orphan_phase)
        nodes[node] = {
            "first_device_ts_us": origin,
            "records": len(records.get(node, [])),
            "reconstructed_missing_samples": len(missing),
            "loss_episodes": len(episodes),
            "sequence_discontinuities_excluded": sequence_discontinuities,
            "mean_missing_per_episode": (
                float(np.mean([row["missing_count"] for row in episodes]))
                if episodes
                else None
            ),
            "maximum_missing_per_episode": (
                max(row["missing_count"] for row in episodes)
                if episodes
                else None
            ),
            "migration": migration,
            "onset_band_width_ms": {
                key: (
                    None if value is None else value / 1_000.0
                )
                for key, value in {
                    "central_50_percent": shortest_circular_width(
                        corrected_onsets, 0.50
                    ),
                    "central_80_percent": shortest_circular_width(
                        corrected_onsets, 0.80
                    ),
                    "central_90_percent": shortest_circular_width(
                        corrected_onsets, 0.90
                    ),
                    "central_95_percent": shortest_circular_width(
                        corrected_onsets, 0.95
                    ),
                }.items()
            },
            "missing_point_band_width_ms": {
                key: (
                    None if value is None else value / 1_000.0
                )
                for key, value in {
                    "central_50_percent": shortest_circular_width(
                        corrected_missing, 0.50
                    ),
                    "central_80_percent": shortest_circular_width(
                        corrected_missing, 0.80
                    ),
                    "central_90_percent": shortest_circular_width(
                        corrected_missing, 0.90
                    ),
                    "central_95_percent": shortest_circular_width(
                        corrected_missing, 0.95
                    ),
                }.items()
            },
            "unpaired_uwb_records": len(orphan_array),
            "unpaired_corrected_center_us": orphan_center,
            "unpaired_corrected_resultant": orphan_resultant,
            "unpaired_migration": orphan_migration,
            "_plot": {
                "missing_elapsed": missing_elapsed,
                "missing_phase": missing_phase,
                "onset_elapsed": onset_elapsed,
                "onset_phase": onset_phase,
                "orphan_elapsed": orphan_elapsed,
                "orphan_phase": orphan_phase,
            },
        }

    return {
        "raw_log": str(raw_path),
        "analysis": str(analysis_path),
        "started_monotonic": start,
        "ended_monotonic": end,
        "duration_s": float(analysis["duration_s"]),
        "reported_missed_deadlines": {
            node: int(
                analysis["per_node"][node]["hard_anomaly_deltas"].get(
                    "imu_missed_deadlines", 0
                )
            )
            for node in NODES
        },
        "nodes": nodes,
    }


def plot_runs(runs: list[tuple[str, dict[str, object]]], output: Path) -> None:
    figure, axes = plt.subplots(
        len(NODES),
        len(runs),
        figsize=(15, 15),
        sharex="col",
        sharey=True,
        constrained_layout=True,
    )
    for column, (label, run) in enumerate(runs):
        for row, node in enumerate(NODES):
            axis = axes[row, column]
            metrics = run["nodes"][node]
            plot = metrics["_plot"]
            elapsed = plot["missing_elapsed"]
            phase = plot["missing_phase"]
            if len(phase):
                stride = max(1, math.ceil(len(phase) / 20_000))
                axis.scatter(
                    elapsed[::stride] / 60.0,
                    phase[::stride] / 1_000.0,
                    s=1.0,
                    alpha=0.28,
                    color="#1769aa",
                    rasterized=True,
                    label="missing IMU deadline",
                )
            else:
                axis.text(
                    0.5,
                    0.5,
                    "no reconstructed misses",
                    transform=axis.transAxes,
                    ha="center",
                    va="center",
                    color="0.4",
                )
            orphan_elapsed = plot["orphan_elapsed"]
            orphan_phase = plot["orphan_phase"]
            if len(orphan_phase):
                axis.scatter(
                    orphan_elapsed / 60.0,
                    orphan_phase / 1_000.0,
                    s=10,
                    marker="x",
                    linewidths=0.7,
                    color="#d84315",
                    alpha=0.7,
                    rasterized=True,
                    label="unpaired UWB frame",
                )
            migration = metrics["migration"]
            slope = migration["slope_us_per_s"]
            center = migration["corrected_center_us"]
            if (
                slope is not None
                and center is not None
                and float(migration["onset_resultant"]) >= 0.30
            ):
                track_time = np.linspace(0.0, run["duration_s"], 4_000)
                track_phase = np.mod(
                    float(center) + float(slope) * track_time,
                    CONNECTION_INTERVAL_US,
                )
                jumps = np.abs(np.diff(track_phase)) > 25_000.0
                track_phase[1:][jumps] = np.nan
                axis.plot(
                    track_time / 60.0,
                    track_phase / 1_000.0,
                    color="#c62828",
                    linewidth=1.1,
                    label="best migrating onset track",
                )
            ppm = migration["clock_offset_ppm"]
            resultant = migration["onset_resultant"]
            suffix = (
                ""
                if ppm is None
                else f", drift={ppm:+.2f} ppm, R={resultant:.3f}"
            )
            axis.set_title(
                f"{label} — {node}\n"
                f"missing={metrics['reconstructed_missing_samples']}, "
                f"episodes={metrics['loss_episodes']}{suffix}",
                fontsize=9,
            )
            axis.set_ylim(0.0, 50.0)
            axis.set_yticks(range(0, 51, 5))
            axis.grid(True, linewidth=0.3, alpha=0.35)
            if column == 0:
                axis.set_ylabel("TIMER2 phase mod 50 ms")
            if row == len(NODES) - 1:
                axis.set_xlabel("elapsed device time (min)")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    if handles:
        figure.legend(
            handles,
            labels,
            loc="upper center",
            bbox_to_anchor=(0.5, 1.012),
            ncol=3,
        )
    figure.savefig(
        output,
        dpi=180,
        bbox_inches="tight",
    )
    plt.close(figure)


def plot_episode_onsets(
    runs: list[tuple[str, dict[str, object]]], output: Path
) -> None:
    figure, axes = plt.subplots(
        len(NODES),
        len(runs),
        figsize=(15, 15),
        sharex="col",
        sharey=True,
        constrained_layout=True,
    )
    for column, (label, run) in enumerate(runs):
        for row, node in enumerate(NODES):
            axis = axes[row, column]
            metrics = run["nodes"][node]
            plot = metrics["_plot"]
            elapsed = plot["onset_elapsed"]
            phase = plot["onset_phase"]
            if len(phase):
                axis.scatter(
                    elapsed / 60.0,
                    phase / 1_000.0,
                    s=2.0,
                    alpha=0.4,
                    color="#1769aa",
                    rasterized=True,
                    label="loss-episode onset",
                )
            else:
                axis.text(
                    0.5,
                    0.5,
                    "no loss episodes",
                    transform=axis.transAxes,
                    ha="center",
                    va="center",
                    color="0.4",
                )
            orphan_elapsed = plot["orphan_elapsed"]
            orphan_phase = plot["orphan_phase"]
            if len(orphan_phase):
                axis.scatter(
                    orphan_elapsed / 60.0,
                    orphan_phase / 1_000.0,
                    s=10,
                    marker="x",
                    linewidths=0.7,
                    color="#d84315",
                    alpha=0.65,
                    rasterized=True,
                    label="unpaired UWB frame",
                )
            migration = metrics["migration"]
            slope = migration["slope_us_per_s"]
            center = migration["corrected_center_us"]
            resultant = migration["onset_resultant"]
            if (
                slope is not None
                and center is not None
                and float(resultant) >= 0.30
            ):
                track_time = np.linspace(0.0, run["duration_s"], 4_000)
                track_phase = np.mod(
                    float(center) + float(slope) * track_time,
                    CONNECTION_INTERVAL_US,
                )
                jumps = np.abs(np.diff(track_phase)) > 25_000.0
                track_phase[1:][jumps] = np.nan
                axis.plot(
                    track_time / 60.0,
                    track_phase / 1_000.0,
                    color="#c62828",
                    linewidth=1.1,
                    label="coherent onset fit",
                )
            ppm = migration["clock_offset_ppm"]
            suffix = (
                ""
                if ppm is None
                else f", drift={ppm:+.2f} ppm, R={resultant:.3f}"
            )
            axis.set_title(
                f"{label} — {node}\n"
                f"episodes={metrics['loss_episodes']}{suffix}",
                fontsize=9,
            )
            axis.set_ylim(0.0, 50.0)
            axis.set_yticks(range(0, 51, 5))
            axis.grid(True, linewidth=0.3, alpha=0.35)
            if column == 0:
                axis.set_ylabel("onset phase mod 50 ms")
            if row == len(NODES) - 1:
                axis.set_xlabel("elapsed device time (min)")
    handles, labels = axes[0, 0].get_legend_handles_labels()
    if handles:
        figure.legend(
            handles,
            labels,
            loc="upper center",
            bbox_to_anchor=(0.5, 1.012),
            ncol=3,
        )
    figure.savefig(
        output,
        dpi=180,
        bbox_inches="tight",
    )
    plt.close(figure)


def strip_plot_arrays(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: strip_plot_arrays(item)
            for key, item in value.items()
            if key != "_plot"
        }
    if isinstance(value, list):
        return [strip_plot_arrays(item) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--formal-raw", type=Path, required=True)
    parser.add_argument("--formal-analysis", type=Path, required=True)
    parser.add_argument("--replicate-raw", type=Path, required=True)
    parser.add_argument("--replicate-analysis", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)

    formal = load_run(args.formal_raw, args.formal_analysis)
    replicate = load_run(args.replicate_raw, args.replicate_analysis)
    plot_runs(
        [("formal v27 run", formal), ("independent replicate", replicate)],
        args.output_dir / "connection_collision_phase.png",
    )
    plot_episode_onsets(
        [("formal v27 run", formal), ("independent replicate", replicate)],
        args.output_dir / "connection_collision_onsets.png",
    )
    result = {
        "method": {
            "missing_reconstruction": (
                "Consecutive accepted sample timestamps on an unchanged "
                "sequence segment define the 5000 us deadline lattice. "
                "Every absent interior lattice point is one missed deadline. "
                "Sequence-discontinuous health-recovery gaps are excluded."
            ),
            "phase": "missing_timestamp_us modulo 50000 us",
            "migration_fit": (
                "Grid-search -100..+100 us/s; choose the slope maximizing "
                "circular concentration of loss-episode onset phases. "
                "1 us/s phase drift equals 1 ppm."
            ),
            "connection_anchor": (
                "NOT RECOVERED: logs expose CI and record arrival times, "
                "but no controller connection-event anchor timestamp."
            ),
        },
        "formal": strip_plot_arrays(formal),
        "replicate": strip_plot_arrays(replicate),
    }
    (args.output_dir / "phase_analysis.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
