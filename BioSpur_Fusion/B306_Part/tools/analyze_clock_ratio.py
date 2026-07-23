#!/usr/bin/env python3
"""Compare DW1000 poll-TX time against the B306 hardware-capture timer."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

import numpy as np


DW1000_TICK_HZ = 499.2e6 * 128.0
DW1000_MASK = (1 << 40) - 1
B306_MASK = (1 << 32) - 1


def log_fields(line: str) -> dict[str, str]:
    return {
        key: value
        for token in line.split()[2:]
        if "=" in token
        for key, value in [token.split("=", 1)]
    }


def load_records(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as stream:
            records = list(csv.DictReader(stream))
    else:
        records = [
            log_fields(line)
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.startswith("FUSION_UWB ")
        ]

    records = [
        record
        for record in records
        if record.get("poll_tx") not in (None, "0", "0000000000")
        and record.get("strobe_us") not in (None, "0", "NA")
    ]
    if len(records) < 2:
        raise ValueError("need at least two records with poll_tx and strobe_us")
    return records


def percentile(values: np.ndarray, q: float) -> float:
    return float(np.percentile(values, q))


def select_contiguous_segment(
    records: list[dict[str, str]], max_gap_us: int
) -> tuple[list[dict[str, str]], int, int, int]:
    """Select the longest run whose B306 intervals can be resolved safely.

    RTT can retain a short startup tail before a logger attaches.  A later live
    record then follows after many DW1000 40-bit wraps, which cannot be inferred
    from the two endpoints.  Split at such long B306 gaps instead of silently
    treating the masked DW1000 delta as the complete elapsed time.
    """

    segments: list[tuple[int, list[dict[str, str]]]] = [(0, [records[0]])]
    for index, (previous, current) in enumerate(zip(records, records[1:]), start=1):
        previous_b306 = int(previous["strobe_us"])
        current_b306 = int(current["strobe_us"])
        b306_delta = (current_b306 - previous_b306) & B306_MASK
        if b306_delta == 0 or b306_delta > max_gap_us:
            segments.append((index, [current]))
        else:
            segments[-1][1].append(current)

    usable = [segment for segment in segments if len(segment[1]) >= 2]
    if not usable:
        raise ValueError("no contiguous segment contains at least two records")
    start, selected = max(usable, key=lambda segment: len(segment[1]))
    selected_number = segments.index((start, selected)) + 1
    return selected, len(segments), selected_number, start


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("records", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--interval-csv", type=Path)
    parser.add_argument(
        "--max-gap-s",
        type=float,
        default=1.0,
        help=(
            "split input at larger B306 gaps before masked DW1000 arithmetic "
            "(default: 1.0 s)"
        ),
    )
    args = parser.parse_args()

    if args.max_gap_s <= 0:
        parser.error("--max-gap-s must be positive")

    input_records = load_records(args.records)
    records, segment_count, selected_segment, selected_start = (
        select_contiguous_segment(input_records, round(args.max_gap_s * 1e6))
    )
    ratios: list[float] = []
    dw_deltas: list[int] = []
    b306_deltas: list[int] = []
    dw1000_wraps = 0
    b306_wraps = 0

    for previous, current in zip(records, records[1:]):
        previous_dw = int(previous["poll_tx"], 16)
        current_dw = int(current["poll_tx"], 16)
        previous_b306 = int(previous["strobe_us"])
        current_b306 = int(current["strobe_us"])
        b306_wraps += current_b306 < previous_b306
        b306_delta = (current_b306 - previous_b306) & B306_MASK
        if b306_delta == 0:
            raise ValueError("duplicate B306 timestamp in selected segment")

        dw1000_wraps += current_dw < previous_dw
        dw_delta = (current_dw - previous_dw) & DW1000_MASK
        ratio = (dw_delta / DW1000_TICK_HZ) / (b306_delta / 1e6)
        dw_deltas.append(dw_delta)
        b306_deltas.append(b306_delta)
        ratios.append(ratio)

    ratio_values = np.asarray(ratios, dtype=np.float64)
    dw_values = np.asarray(dw_deltas, dtype=np.float64)
    b306_values = np.asarray(b306_deltas, dtype=np.float64)
    aggregate_ratio = (dw_values.sum() / DW1000_TICK_HZ) / (
        b306_values.sum() / 1e6
    )

    slot_histogram = Counter(max(1, round(value / 100_000)) for value in b306_deltas)
    lines = [
        f"source={args.records}",
        f"input_records={len(input_records)}",
        f"segment_count={segment_count}",
        f"selected_segment={selected_segment}",
        f"selected_input_start={selected_start}",
        f"selected_input_end={selected_start + len(records) - 1}",
        f"discarded_records={len(input_records) - len(records)}",
        f"max_gap_s={args.max_gap_s:.6f}",
        f"records={len(records)}",
        f"intervals={len(ratios)}",
        f"dw1000_wraps={dw1000_wraps}",
        f"b306_wraps={b306_wraps}",
        f"dw1000_elapsed_s={dw_values.sum() / DW1000_TICK_HZ:.9f}",
        f"b306_elapsed_s={b306_values.sum() / 1e6:.9f}",
        f"aggregate_ratio={aggregate_ratio:.12f}",
        f"aggregate_error_ppm={(aggregate_ratio - 1.0) * 1e6:.3f}",
        f"interval_ratio_mean={ratio_values.mean():.12f}",
        f"interval_error_mean_ppm={(ratio_values.mean() - 1.0) * 1e6:.3f}",
        f"interval_ratio_median={np.median(ratio_values):.12f}",
        f"interval_ratio_std={ratio_values.std():.12f}",
        f"interval_std_ppm={ratio_values.std() * 1e6:.3f}",
        f"interval_ratio_min={ratio_values.min():.12f}",
        f"interval_ratio_p01={percentile(ratio_values, 1):.12f}",
        f"interval_ratio_p05={percentile(ratio_values, 5):.12f}",
        f"interval_ratio_p95={percentile(ratio_values, 95):.12f}",
        f"interval_ratio_p99={percentile(ratio_values, 99):.12f}",
        f"interval_ratio_max={ratio_values.max():.12f}",
        "slot_interval_hist="
        + ",".join(f"{key}:{slot_histogram[key]}" for key in sorted(slot_histogram)),
    ]

    for target_seconds in (1, 5, 10, 30):
        chunks: list[float] = []
        chunk_dw = 0
        chunk_b306 = 0
        for dw_delta, b306_delta in zip(dw_deltas, b306_deltas):
            chunk_dw += dw_delta
            chunk_b306 += b306_delta
            if chunk_b306 >= target_seconds * 1_000_000:
                chunks.append(
                    (chunk_dw / DW1000_TICK_HZ) / (chunk_b306 / 1e6)
                )
                chunk_dw = 0
                chunk_b306 = 0
        if chunks:
            values = np.asarray(chunks, dtype=np.float64)
            prefix = f"chunk_{target_seconds}s"
            lines.extend(
                [
                    f"{prefix}_count={len(values)}",
                    f"{prefix}_mean={values.mean():.12f}",
                    f"{prefix}_std_ppm={values.std() * 1e6:.3f}",
                    f"{prefix}_min_ppm={(values.min() - 1.0) * 1e6:.3f}",
                    f"{prefix}_max_ppm={(values.max() - 1.0) * 1e6:.3f}",
                ]
            )

    output = "\n".join(lines) + "\n"
    if args.summary:
        args.summary.write_text(output, encoding="utf-8")

    if args.interval_csv:
        with args.interval_csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                [
                    "interval",
                    "previous_sweep",
                    "current_sweep",
                    "dw1000_delta_ticks",
                    "dw1000_delta_s",
                    "b306_delta_us",
                    "ratio",
                    "error_ppm",
                ]
            )
            for index, (previous, current, dw_delta, b306_delta, ratio) in enumerate(
                zip(records, records[1:], dw_deltas, b306_deltas, ratios), start=1
            ):
                writer.writerow(
                    [
                        index,
                        previous.get("sweep", ""),
                        current.get("sweep", ""),
                        dw_delta,
                        f"{dw_delta / DW1000_TICK_HZ:.12f}",
                        b306_delta,
                        f"{ratio:.12f}",
                        f"{(ratio - 1.0) * 1e6:.3f}",
                    ]
                )

    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
