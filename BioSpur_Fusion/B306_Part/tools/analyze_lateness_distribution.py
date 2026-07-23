#!/usr/bin/env python3
"""Decode paged BSLLATE telemetry into auditable tables and statistics."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


STAMP_RE = re.compile(r"\[\s*\d+\.\d+\]\s*")
SUMMARY_RE = re.compile(r"BSLLATE;1;([^\r\n]+)")
HIST_RE = re.compile(
    r"BSLLATEH;1;page=(\d+);start_tick=(-?\d+);counts=([0-9,]*)"
)
TAIL_RE = re.compile(
    r"BSLLATET;1;page=(\d+);offset=(\d+);pairs=([0-9:,\-]*)"
)


def fields(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in text.split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def percentile_from_hist(hist: dict[int, int], fraction: float) -> int | None:
    total = sum(hist.values())
    if not total:
        return None
    rank = max(1, math.ceil(total * fraction))
    seen = 0
    for tick, count in sorted(hist.items()):
        seen += count
        if seen >= rank:
            return tick
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--extra-log", type=Path, action="append", default=[])
    args = parser.parse_args()

    input_logs = [args.log, *args.extra_log]
    clean = "\n".join(
        STAMP_RE.sub("", path.read_text(encoding="utf-8", errors="replace"))
        for path in input_logs
    )
    summaries = [fields(match.group(1)) for match in SUMMARY_RE.finditer(clean)]
    if not summaries:
        raise SystemExit("no BSLLATE summary found")
    summary = summaries[-1]
    tick_hz = int(summary["tick_hz"])

    hist_pages: dict[int, tuple[int, list[int]]] = {}
    for match in HIST_RE.finditer(clean):
        page = int(match.group(1))
        counts = [int(value) for value in match.group(3).split(",") if value]
        hist_pages[page] = (int(match.group(2)), counts)
    histogram: dict[int, int] = {}
    for _, (start_tick, counts) in sorted(hist_pages.items()):
        for offset, count in enumerate(counts):
            histogram[start_tick + offset] = count

    tail_pages: dict[int, list[tuple[int, int]]] = {}
    for match in TAIL_RE.finditer(clean):
        values: list[tuple[int, int]] = []
        for pair in match.group(3).split(","):
            if pair:
                sample, tick = pair.split(":", 1)
                values.append((int(sample), int(tick)))
        tail_pages[int(match.group(1))] = values
    tail = [value for page in sorted(tail_pages) for value in tail_pages[page]]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "lateness-histogram.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["lateness_ticks", "lateness_us", "count"])
        for tick, count in sorted(histogram.items()):
            writer.writerow([tick, f"{tick * 1_000_000 / tick_hz:.6f}", count])
    with (args.output_dir / "lateness-tail.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_index", "lateness_ticks", "lateness_us", "mod35"])
        for sample, tick in tail:
            writer.writerow(
                [sample, tick, f"{tick * 1_000_000 / tick_hz:.6f}", sample % 35]
            )

    plotted = [(tick, count) for tick, count in sorted(histogram.items()) if count]
    figure, axis = plt.subplots(figsize=(9, 4.8), constrained_layout=True)
    axis.bar(
        [tick * 1_000_000 / tick_hz for tick, _ in plotted],
        [count for _, count in plotted],
        width=0.8 * 1_000_000 / tick_hz,
        color="#276FBF",
    )
    axis.axvline(1000.0, color="#C73E1D", linestyle="--", linewidth=1.2,
                 label="1 ms")
    axis.set_xlabel("Signed final-stage lateness (us)")
    axis.set_ylabel("Attempts")
    axis.set_title("BS065F final wait lateness, CI=437.5 ms")
    axis.grid(axis="y", alpha=0.25)
    axis.legend()
    figure.savefig(args.output_dir / "lateness-histogram.png", dpi=160)
    plt.close(figure)

    samples = int(summary["samples"])
    under = int(summary["under"])
    over = int(summary["over"])
    hist_total = sum(histogram.values())
    total_from_bins = hist_total + under + over
    mean_ticks = int(summary["sum_tick"]) / samples if samples else math.nan
    second_moment = int(summary["sumsq_tick"]) / samples if samples else math.nan
    std_ticks = (
        math.sqrt(max(0.0, second_moment - mean_ticks * mean_ticks))
        if samples
        else math.nan
    )
    occupied = [tick for tick, count in histogram.items() if count]
    adjacent_occupied_fraction = (
        sum(1 for tick in occupied if histogram.get(tick + 1, 0) > 0)
        / max(1, len(occupied) - 1)
    )
    tail_mod35 = [0] * 35
    for sample, _ in tail:
        tail_mod35[sample % 35] += 1

    result = {
        "sources": [str(path) for path in input_logs],
        "summary": summary,
        "samples": samples,
        "tick_hz": tick_hz,
        "tick_period_us": 1_000_000 / tick_hz,
        "histogram_pages": sorted(hist_pages),
        "tail_pages": sorted(tail_pages),
        "histogram_total": hist_total,
        "underflow": under,
        "overflow": over,
        "total_from_bins": total_from_bins,
        "count_matches_summary": total_from_bins == samples,
        "mean_ticks": mean_ticks,
        "mean_us": mean_ticks * 1_000_000 / tick_hz,
        "std_ticks": std_ticks,
        "std_us": std_ticks * 1_000_000 / tick_hz,
        "percentiles_ticks": {
            str(percent): percentile_from_hist(histogram, percent / 100)
            for percent in (1, 5, 50, 90, 95, 99, 99.5, 99.9)
        },
        "occupied_bins": len(occupied),
        "adjacent_occupied_fraction": adjacent_occupied_fraction,
        "tail_values_recovered": len(tail),
        "tail_mod35_counts": tail_mod35,
    }
    (args.output_dir / "lateness-summary.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        f"samples={samples}",
        f"tick_hz={tick_hz}",
        f"tick_period_us={result['tick_period_us']:.9f}",
        f"range_ticks={summary['min_tick']}..{summary['max_tick']}",
        f"range_us={int(summary['min_tick']) * 1_000_000 / tick_hz:.3f}.."
        f"{int(summary['max_tick']) * 1_000_000 / tick_hz:.3f}",
        f"mean_us={result['mean_us']:.3f}",
        f"std_us={result['std_us']:.3f}",
        f"hist_total={hist_total} under={under} over={over} "
        f"count_ok={result['count_matches_summary']}",
        f"tail_total={summary['tail_total']} tail_stored={summary['tail_stored']} "
        f"tail_recovered={len(tail)}",
        f"occupied_bins={len(occupied)} adjacent_occupied_fraction="
        f"{adjacent_occupied_fraction:.6f}",
        f"percentiles_ticks={json.dumps(result['percentiles_ticks'], sort_keys=True)}",
        f"tail_mod35_counts={','.join(str(value) for value in tail_mod35)}",
    ]
    (args.output_dir / "lateness-summary.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
