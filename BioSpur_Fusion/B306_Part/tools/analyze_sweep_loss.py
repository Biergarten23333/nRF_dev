#!/usr/bin/env python3
"""Summarize one connection-interval sweep-loss run.

The J-Link RTT reader can receive a short stale prefix when it attaches to an
already-running target.  Select the longest contiguous strobe segment, then
measure an independent fixed-duration window from its first complete record.
DSView is measured on its own time axis over the same nominal duration; the
two recorders need not share a capture-start instant for the loss-rate test.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path


FIELD_RE = re.compile(r"(?:^|\s)([A-Za-z0-9_]+)=([^\s]+)")
LOG_TIMESTAMP_RE = re.compile(r"\[\s*\d+\.\d+\]\s*")


def fields(line: str) -> dict[str, str]:
    return {key: value for key, value in FIELD_RE.findall(line)}


def load_complete_rtt_records(path: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    required = {"strobe_us", "sweep", "verdict", "edge_qdrop", "orphan_edge"}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("FUSION_UWB "):
            continue
        record = fields(line)
        if required.issubset(record) and record["strobe_us"].isdigit():
            records.append(record)
    if not records:
        raise ValueError(f"no complete FUSION_UWB records in {path}")
    return records


def split_segments(
    records: list[dict[str, str]], max_gap_us: int = 1_000_000
) -> list[list[dict[str, str]]]:
    segments: list[list[dict[str, str]]] = [[]]
    previous: int | None = None
    for record in records:
        current = int(record["strobe_us"])
        if previous is not None and (current <= previous or current - previous > max_gap_us):
            segments.append([])
        segments[-1].append(record)
        previous = current
    return [segment for segment in segments if segment]


def load_dsview_window(path: Path, duration_s: float) -> list[float]:
    rises: list[float] = []
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            rise_s = float(row["rise_s"])
            if rise_s < duration_s:
                rises.append(rise_s)
    return rises


def missing_slots_from_times(times: list[float], period_s: float = 0.1) -> int:
    """Count skipped slots from gaps without assuming an exact oscillator rate.

    A fixed 300 s boundary can contain 2999, 3000, or 3001 edges depending on
    capture phase and clock error.  Only a multi-period gap is evidence that a
    strobe was actually lost.
    """

    return sum(
        max(0, round((current - previous) / period_s) - 1)
        for previous, current in zip(times, times[1:])
    )


def load_bslstat(path: Path, capture_only: bool = True) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = LOG_TIMESTAMP_RE.sub("", raw_line)
        marker = line.find("BSLSTAT;1;")
        if marker < 0:
            continue
        record = {
            key: value
            for key, value in re.findall(r"([a-z]+)=([A-Za-z0-9-]+)", line[marker:])
        }
        if "gen" not in record or "spinlate" not in record or "slplate" not in record:
            continue
        if capture_only and record.get("cpmode") != "CAP":
            continue
        records.append(record)
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("rtt", type=Path)
    parser.add_argument("pulses", type=Path)
    parser.add_argument("--setup-log", type=Path, required=True)
    parser.add_argument("--post-log", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    records = load_complete_rtt_records(args.rtt)
    segments = split_segments(records)
    selected_index, selected = max(enumerate(segments), key=lambda item: len(item[1]))
    start_us = int(selected[0]["strobe_us"])
    stop_us = start_us + round(args.duration * 1_000_000)
    window = [record for record in selected if int(record["strobe_us"]) < stop_us]

    nominal_slots = round(args.duration * 10)
    b306_times_s = [int(record["strobe_us"]) / 1_000_000 for record in window]
    b306_missing = missing_slots_from_times(b306_times_s)
    b306_attempted = len(window) + b306_missing
    dsview_times_s = load_dsview_window(args.pulses, args.duration)
    dsview_pulses = len(dsview_times_s)
    dsview_missing = missing_slots_from_times(dsview_times_s)
    dsview_attempted = dsview_pulses + dsview_missing

    slot_hist: Counter[int] = Counter()
    for previous, current in zip(window, window[1:]):
        delta_us = int(current["strobe_us"]) - int(previous["strobe_us"])
        slot_hist[max(1, round(delta_us / 100_000))] += 1

    pre_records = load_bslstat(args.setup_log)
    post_records = load_bslstat(args.post_log)
    if not pre_records or not post_records:
        raise ValueError("missing complete capture-mode BSLSTAT pre/post record")
    # Logs are chronological.  Choosing max(gen) is wrong when the initial
    # serial drain contains a stale pre-reset status with a larger counter.
    pre = pre_records[-1]
    post = post_records[-1]

    gen_delta = int(post["gen"]) - int(pre["gen"])
    slplate_delta = int(post["slplate"]) - int(pre["slplate"])
    spinlate_delta = int(post["spinlate"]) - int(pre["spinlate"])
    attempted_slots = gen_delta + slplate_delta + spinlate_delta

    verdicts = Counter(record["verdict"] for record in window)
    final_counter_names = ["edge_qdrop", "orphan_strobe", "orphan_edge", "orphan_frame"]
    counter_deltas = {
        name: int(window[-1][name]) - int(window[0][name]) for name in final_counter_names
    }

    lines = [
        f"duration_s={args.duration:.6f}",
        f"input_complete_records={len(records)}",
        f"segment_count={len(segments)}",
        f"selected_segment={selected_index + 1}",
        f"selected_segment_records={len(selected)}",
        f"selected_start_strobe_us={start_us}",
        f"b306_records={len(window)}",
        f"nominal_10hz_slots={nominal_slots}",
        f"b306_nominal_count_difference={len(window) - nominal_slots}",
        f"b306_missing_slots={b306_missing}",
        f"b306_loss_fraction={b306_missing / b306_attempted:.9f}",
        "b306_slot_interval_hist="
        + ",".join(f"{key}:{slot_hist[key]}" for key in sorted(slot_hist)),
        f"dsview_pulses={dsview_pulses}",
        f"dsview_nominal_count_difference={dsview_pulses - nominal_slots}",
        f"dsview_missing_slots={dsview_missing}",
        f"dsview_loss_fraction={dsview_missing / dsview_attempted:.9f}",
        f"recorder_missing_difference={b306_missing - dsview_missing}",
        "verdicts=" + ",".join(f"{key}:{verdicts[key]}" for key in sorted(verdicts)),
        f"bsl_pre_gen={pre['gen']}",
        f"bsl_post_gen={post['gen']}",
        f"bsl_gen_delta={gen_delta}",
        f"bsl_pre_slplate={pre['slplate']}",
        f"bsl_post_slplate={post['slplate']}",
        f"bsl_slplate_delta={slplate_delta}",
        f"bsl_pre_spinlate={pre['spinlate']}",
        f"bsl_post_spinlate={post['spinlate']}",
        f"bsl_spinlate_delta={spinlate_delta}",
        f"bsl_accounted_slots={attempted_slots}",
        f"bsl_skip_fraction={((slplate_delta + spinlate_delta) / attempted_slots):.9f}",
        f"bsl_ci={post.get('ci', 'NA')}",
        f"bsl_sup={post.get('sup', 'NA')}",
        f"bsl_reqci={post.get('reqci', 'NA')}",
        f"bsl_reqsup={post.get('reqsup', 'NA')}",
        f"bsl_ciok={post.get('ciok', 'NA')}",
        f"bsl_supok={post.get('supok', 'NA')}",
        f"bsl_cpmode={post.get('cpmode', 'NA')}",
    ]
    lines.extend(f"{name}_delta={counter_deltas[name]}" for name in final_counter_names)
    output = "\n".join(lines) + "\n"
    if args.summary:
        args.summary.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
