#!/usr/bin/env python3
"""Decode a one-channel DSView .dsl capture into pulse timing records.

DSView v1.3 stores a one-channel logic trace as LSB-first packed samples in
the ``L-0/<block>`` members of a zip archive.  This tool streams one block at
a time, so multi-gigabit captures do not have to be expanded on disk.
"""

from __future__ import annotations

import argparse
import configparser
import csv
import re
import statistics
import zipfile
from collections import Counter
from pathlib import Path

import numpy as np


def parse_rate(text: str) -> int:
    match = re.fullmatch(r"\s*([0-9.]+)\s*([kKmMgG]?)Hz\s*", text)
    if match is None:
        raise ValueError(f"unsupported DSView samplerate: {text!r}")
    scale = {"": 1, "k": 1_000, "m": 1_000_000, "g": 1_000_000_000}
    return round(float(match.group(1)) * scale[match.group(2).lower()])


def percentile(values: list[int], q: float) -> float:
    if not values:
        return float("nan")
    return float(np.percentile(np.asarray(values, dtype=np.uint64), q))


def decode_edges(
    archive: zipfile.ZipFile, total_samples: int, total_blocks: int
) -> tuple[int, int, list[int], list[int]]:
    rising: list[int] = []
    falling: list[int] = []
    sample_base = 0
    previous: int | None = None
    initial = 0

    for block_index in range(total_blocks):
        packed = archive.read(f"L-0/{block_index}")
        bits = np.unpackbits(np.frombuffer(packed, dtype=np.uint8), bitorder="little")
        remaining = total_samples - sample_base
        if remaining <= 0:
            break
        if bits.size > remaining:
            bits = bits[:remaining]
        if bits.size == 0:
            continue

        first = int(bits[0])
        if previous is None:
            initial = first
        elif first != previous:
            (rising if first else falling).append(sample_base)

        transitions = np.flatnonzero(bits[1:] != bits[:-1]) + 1
        for local_index in transitions.tolist():
            new_level = int(bits[local_index])
            (rising if new_level else falling).append(sample_base + local_index)

        previous = int(bits[-1])
        sample_base += int(bits.size)

    if sample_base != total_samples:
        raise ValueError(
            f"decoded {sample_base} samples, header declares {total_samples}"
        )
    return initial, int(previous or 0), rising, falling


def pair_high_pulses(rising: list[int], falling: list[int]) -> list[tuple[int, int]]:
    pulses: list[tuple[int, int]] = []
    fall_index = 0
    for rise in rising:
        while fall_index < len(falling) and falling[fall_index] <= rise:
            fall_index += 1
        if fall_index == len(falling):
            break
        pulses.append((rise, falling[fall_index]))
        fall_index += 1
    return pulses


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    with zipfile.ZipFile(args.capture) as archive:
        config = configparser.ConfigParser()
        config.read_string(archive.read("header").decode("utf-8"))
        header = config["header"]
        total_samples = header.getint("total samples")
        total_blocks = header.getint("total blocks")
        samplerate_hz = parse_rate(header["samplerate"])
        initial, final, rising, falling = decode_edges(
            archive, total_samples, total_blocks
        )

    pulses = pair_high_pulses(rising, falling)
    widths = [fall - rise for rise, fall in pulses]
    intervals = [pulses[i][0] - pulses[i - 1][0] for i in range(1, len(pulses))]
    normal_intervals = [
        value for value in intervals if 0.5 * samplerate_hz / 10 < value < 1.5 * samplerate_hz / 10
    ]
    slot_intervals = [max(1, round(value / (samplerate_hz / 10))) for value in intervals]

    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "pulse",
                    "rise_sample",
                    "fall_sample",
                    "width_samples",
                    "rise_s",
                    "fall_s",
                    "width_us",
                    "interval_ms",
                ]
            )
            previous_rise: int | None = None
            for index, (rise, fall) in enumerate(pulses):
                interval_ms = ""
                if previous_rise is not None:
                    interval_ms = f"{(rise - previous_rise) * 1e3 / samplerate_hz:.6f}"
                writer.writerow(
                    [
                        index,
                        rise,
                        fall,
                        fall - rise,
                        f"{rise / samplerate_hz:.9f}",
                        f"{fall / samplerate_hz:.9f}",
                        f"{(fall - rise) * 1e6 / samplerate_hz:.3f}",
                        interval_ms,
                    ]
                )
                previous_rise = rise

    width_hist = Counter(widths)
    slot_hist = Counter(slot_intervals)
    expected_slots = sum(slot_intervals)
    missing_slots = sum(value - 1 for value in slot_intervals)
    main_width_count = sum(width_hist[value] for value in (53, 54, 55))
    lines = [
        f"file={args.capture}",
        f"samplerate_hz={samplerate_hz}",
        f"total_samples={total_samples}",
        f"duration_s={total_samples / samplerate_hz}",
        f"initial_level={initial}",
        f"final_level={final}",
        f"rising_edges={len(rising)}",
        f"falling_edges={len(falling)}",
        f"complete_pulses={len(pulses)}",
        f"width_samples_min={min(widths) if widths else 'NA'}",
        f"width_samples_median={statistics.median(widths) if widths else 'NA'}",
        f"width_samples_max={max(widths) if widths else 'NA'}",
        f"width_us_min={min(widths) * 1e6 / samplerate_hz:.3f}" if widths else "width_us_min=NA",
        f"width_us_p50={percentile(widths, 50) * 1e6 / samplerate_hz:.3f}" if widths else "width_us_p50=NA",
        f"width_us_p99={percentile(widths, 99) * 1e6 / samplerate_hz:.3f}" if widths else "width_us_p99=NA",
        f"width_us_max={max(widths) * 1e6 / samplerate_hz:.3f}" if widths else "width_us_max=NA",
        f"width_main_53_55_count={main_width_count}",
        f"width_main_53_55_fraction={main_width_count / len(widths):.9f}" if widths else "width_main_53_55_fraction=NA",
        f"interval_count={len(intervals)}",
        f"interval_ms_min={min(intervals) * 1e3 / samplerate_hz:.6f}" if intervals else "interval_ms_min=NA",
        f"interval_ms_p50={percentile(intervals, 50) * 1e3 / samplerate_hz:.6f}" if intervals else "interval_ms_p50=NA",
        f"interval_ms_p99={percentile(intervals, 99) * 1e3 / samplerate_hz:.6f}" if intervals else "interval_ms_p99=NA",
        f"interval_ms_max={max(intervals) * 1e3 / samplerate_hz:.6f}" if intervals else "interval_ms_max=NA",
        f"normal_interval_count={len(normal_intervals)}",
        f"normal_interval_ms_mean={statistics.fmean(normal_intervals) * 1e3 / samplerate_hz:.6f}" if normal_intervals else "normal_interval_ms_mean=NA",
        f"normal_interval_ms_std={statistics.pstdev(normal_intervals) * 1e3 / samplerate_hz:.6f}" if normal_intervals else "normal_interval_ms_std=NA",
        f"normal_interval_ms_min={min(normal_intervals) * 1e3 / samplerate_hz:.6f}" if normal_intervals else "normal_interval_ms_min=NA",
        f"normal_interval_ms_max={max(normal_intervals) * 1e3 / samplerate_hz:.6f}" if normal_intervals else "normal_interval_ms_max=NA",
        f"long_intervals_gt150ms={sum(value > 0.15 * samplerate_hz for value in intervals)}",
        f"estimated_missing_100ms_slots={missing_slots}",
        f"expected_slots_between_first_last={expected_slots}",
        f"pulse_delivery_fraction={len(pulses) / (expected_slots + 1):.9f}" if expected_slots else "pulse_delivery_fraction=NA",
        "width_samples_hist=" + ",".join(f"{key}:{width_hist[key]}" for key in sorted(width_hist)),
        "slot_interval_hist=" + ",".join(f"{key}:{slot_hist[key]}" for key in sorted(slot_hist)),
    ]
    output = "\n".join(lines) + "\n"
    if args.summary:
        args.summary.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
