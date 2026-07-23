#!/usr/bin/env python3
"""Align DSView UWB_RDY pulses with B306 per-sweep RTT records."""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import Counter
from pathlib import Path

import numpy as np


def fields(line: str) -> dict[str, str]:
    return {
        key: value
        for token in line.split()[2:]
        if "=" in token
        for key, value in [token.split("=", 1)]
    }


def load_rtt(path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    records: list[dict[str, str]] = []
    telemetry: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("FUSION_UWB "):
            record = fields(line)
            if record.get("strobe_us") not in (None, "0", "NA"):
                records.append(record)
        elif line.startswith("FUSION_TELEMETRY "):
            telemetry.append(fields(line))
    return records, telemetry


def load_pulses(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def is_long(value_us: float) -> bool:
    return value_us > 150_000


def alignment_index(
    pulses: list[dict[str, str]], records: list[dict[str, str]], match_count: int = 500
) -> tuple[int, int]:
    count = min(match_count, len(pulses) - 1)
    ds_flags = [
        is_long(
            (float(pulses[index]["rise_s"]) - float(pulses[index - 1]["rise_s"]))
            * 1_000_000.0
        )
        for index in range(1, count + 1)
    ]
    candidates: list[tuple[int, int]] = []
    for start in range(len(records) - count - 1):
        record_flags = [
            is_long(
                int(records[start + index]["strobe_us"])
                - int(records[start + index - 1]["strobe_us"])
            )
            for index in range(1, count + 1)
        ]
        score = sum(left == right for left, right in zip(ds_flags, record_flags))
        candidates.append((score, start))
    best_score = max(score for score, _ in candidates)
    best = [start for score, start in candidates if score == best_score]
    if best_score != count or len(best) != 1:
        raise ValueError(
            f"cadence alignment is not unique: score={best_score}/{count}, candidates={best}"
        )
    return best[0], count


def pct(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values), q))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("rtt", type=Path)
    parser.add_argument("pulses", type=Path)
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--aligned-csv", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    records, telemetry = load_rtt(args.rtt)
    pulses = load_pulses(args.pulses)
    start, match_count = alignment_index(pulses, records)
    window_pulses = [pulse for pulse in pulses if float(pulse["rise_s"]) < args.duration]
    window_records = records[start : start + len(window_pulses)]
    if len(window_records) != len(window_pulses):
        raise ValueError("RTT log does not cover the requested DSView window")

    mismatched_intervals = 0
    for index in range(1, len(window_pulses)):
        ds_interval = (
            float(window_pulses[index]["rise_s"])
            - float(window_pulses[index - 1]["rise_s"])
        ) * 1_000_000.0
        b306_interval = int(window_records[index]["strobe_us"]) - int(
            window_records[index - 1]["strobe_us"]
        )
        mismatched_intervals += is_long(ds_interval) != is_long(b306_interval)
    if mismatched_intervals:
        raise ValueError(f"DSView/B306 cadence mismatches: {mismatched_intervals}")

    if args.aligned_csv:
        with args.aligned_csv.open("w", newline="", encoding="utf-8") as stream:
            columns = [
                "pulse",
                "rise_sample",
                "fall_sample",
                "width_samples",
                "rise_s",
                "width_us",
                "master_ms",
                "node_ms",
                "sweep",
                "poll_tx",
                "strobe_us",
                "rise_us",
                "fall_us",
                "pair_dt_us",
                "verdict",
                "edge",
                "candidates",
                "strobe_sent",
                "valid",
            ]
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for pulse, record in zip(window_pulses, window_records):
                writer.writerow({key: pulse.get(key, record.get(key, "")) for key in columns})

    ds_intervals = [
        (
            float(window_pulses[index]["rise_s"])
            - float(window_pulses[index - 1]["rise_s"])
        ) * 1_000_000.0
        for index in range(1, len(window_pulses))
    ]
    b306_intervals = [
        int(window_records[index]["strobe_us"])
        - int(window_records[index - 1]["strobe_us"])
        for index in range(1, len(window_records))
    ]
    widths = [float(pulse["width_us"]) for pulse in window_pulses]
    pair_dt = [int(record["pair_dt_us"]) for record in window_records]
    expected_slots = round(args.duration * 10)
    missing_slots = expected_slots - len(window_pulses)

    cases = Counter()
    verdicts = Counter(record["verdict"] for record in window_records)
    for record in window_records:
        sent = record["strobe_sent"] == "1"
        received = record["strobe_us"] not in ("0", "NA")
        cases[(sent, received)] += 1

    sweep_gaps = sum(
        max(
            0,
            int(window_records[index]["sweep"])
            - int(window_records[index - 1]["sweep"])
            - 1,
        )
        for index in range(1, len(window_records))
    )
    counter_names = [
        "orphan_strobe",
        "orphan_edge",
        "orphan_frame",
        "near_window",
        "edge_qdrop",
    ]
    baseline = records[start - 1] if start else window_records[0]
    counter_deltas = {
        name: int(window_records[-1][name]) - int(baseline[name]) for name in counter_names
    }
    rise_delta = int(window_records[-1]["rise_n"]) - int(baseline["rise_n"])
    fall_delta = int(window_records[-1]["fall_n"]) - int(baseline["fall_n"])
    valid_loss = sum(int(record["valid"], 16) != 0xFF for record in window_records)
    edge_counts = Counter(record["edge"] for record in window_records)
    capture_flags = sorted({record["capture_flags"] for record in window_records})
    non_single_candidates = sum(record["candidates"] != "1" for record in window_records)

    scale = sum(ds_intervals) / sum(b306_intervals)
    lines = [
        f"duration_s={args.duration:.6f}",
        f"alignment_record_index={start}",
        f"alignment_match_intervals={match_count}",
        f"alignment_first_sweep={window_records[0]['sweep']}",
        f"alignment_first_master_ms={window_records[0]['master_ms']}",
        f"alignment_first_strobe_us={window_records[0]['strobe_us']}",
        f"dsview_pulses={len(window_pulses)}",
        f"b306_records={len(window_records)}",
        f"expected_10hz_slots={expected_slots}",
        f"missing_10hz_slots={missing_slots}",
        f"slot_loss_fraction={missing_slots / expected_slots:.9f}",
        f"dsview_b306_cadence_mismatches={mismatched_intervals}",
        f"dsview_clock_scale_vs_b306={scale:.12f}",
        f"dsview_width_us_min={min(widths):.3f}",
        f"dsview_width_us_p50={pct(widths, 50):.3f}",
        f"dsview_width_us_p99={pct(widths, 99):.3f}",
        f"dsview_width_us_max={max(widths):.3f}",
        f"pair_dt_us_min={min(pair_dt)}",
        f"pair_dt_us_p50={pct(pair_dt, 50):.3f}",
        f"pair_dt_us_p99={pct(pair_dt, 99):.3f}",
        f"pair_dt_us_max={max(pair_dt)}",
        f"case_sent1_received1={cases[(True, True)]}",
        f"case_sent1_received0={cases[(True, False)]}",
        f"case_sent0_received0={cases[(False, False)]}",
        f"case_sent0_received1={cases[(False, True)]}",
        f"verdicts=" + ",".join(f"{key}:{verdicts[key]}" for key in sorted(verdicts)),
        f"sweep_counter_gaps={sweep_gaps}",
        f"valid_mask_not_ff={valid_loss}",
        f"rise_n_delta={rise_delta}",
        f"fall_n_delta={fall_delta}",
        "edge_counts=" + ",".join(f"{key}:{edge_counts[key]}" for key in sorted(edge_counts)),
        f"capture_flags={','.join(capture_flags)}",
        f"non_single_candidate_records={non_single_candidates}",
    ]
    lines.extend(f"{name}_delta={counter_deltas[name]}" for name in counter_names)
    if telemetry:
        lines.extend(
            [
                f"telemetry_malformed_last={telemetry[-1].get('malformed', 'NA')}",
                f"telemetry_logger_drop_last={telemetry[-1].get('logger_drop', 'NA')}",
            ]
        )
    output = "\n".join(lines) + "\n"
    if args.summary:
        args.summary.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
