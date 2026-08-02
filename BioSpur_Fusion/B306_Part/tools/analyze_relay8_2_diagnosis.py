#!/usr/bin/env python3
"""Offline D1-D3 diagnosis for the relay8.1 overnight capture."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path


NODES = (
    "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
    "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35",
)
TARGETS = ("BSF1120", "BSF3C79")
DW_TICKS_PER_US = 63_897.6
U32 = 1 << 32
FIELD_RE = re.compile(r"(?:^|\s)([A-Za-z0-9_]+)=([^\s]+)")


def fields(line: str) -> dict[str, str]:
    return dict(FIELD_RE.findall(line))


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * q
    lo = math.floor(position)
    hi = math.ceil(position)
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - position) + values[hi] * (position - lo)


def distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "min": min(values) if values else None,
        "p10": percentile(values, 0.10),
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p99": percentile(values, 0.99),
        "max": max(values) if values else None,
    }


def u32_delta(a: int, b: int) -> int:
    return (b - a) & 0xFFFFFFFF


def analyze_d1(snapshots_path: Path) -> dict[str, object]:
    snapshots = json.loads(snapshots_path.read_text(encoding="utf-8"))
    series: dict[str, list[tuple[float, int, int, int, int]]] = defaultdict(list)
    for snap in snapshots:
        when = datetime.fromisoformat(snap["utc"]).timestamp()
        for node, row in snap.get("beacon_status", {}).items():
            f = row.get("fields", {}) if isinstance(row, dict) else {}
            if node in NODES and "rx" in f and "miss" in f:
                series[node].append(
                    (when, int(f["rx"], 0), int(f["miss"], 0),
                     int(f.get("lock", "0"), 0), int(f.get("rebase", "0"), 0))
                )
    result: dict[str, object] = {}
    for node in NODES:
        rows = series.get(node, [])
        avg_gaps: list[float] = []
        miss_fractions: list[float] = []
        interval_rows = []
        total_rx = total_miss = 0
        for left, right in zip(rows, rows[1:]):
            dt = right[0] - left[0]
            drx = u32_delta(left[1], right[1])
            dmiss = u32_delta(left[2], right[2])
            if dt <= 0 or drx > 1_000_000 or dmiss > 10_000_000:
                continue
            total_rx += drx
            total_miss += dmiss
            if drx:
                avg_gaps.append(dt / drx)
            if drx + dmiss:
                miss_fractions.append(dmiss / (drx + dmiss))
            interval_rows.append({
                "start_epoch_s": left[0], "duration_s": dt,
                "rx_delta": drx, "miss_delta": dmiss,
                "interval_average_valid_gap_s": dt / drx if drx else None,
            })
        gap50 = percentile(avg_gaps, 0.5)
        # This is only a boundary/timeout-derived magnitude range, not a fit.
        implied = None
        if gap50 and gap50 > 1.0:
            implied = {
                "lower_from_500us_ppm": 500.0 / gap50,
                "upper_from_600us_ppm": 600.0 / gap50,
            }
        result[node] = {
            "snapshots": len(rows),
            "rx_delta_observed": total_rx,
            "miss_delta_observed": total_miss,
            "interval_average_valid_gap_s": distribution(avg_gaps),
            "interval_miss_fraction": distribution(miss_fractions),
            "implied_drift_magnitude_if_boundary_limited_ppm": implied,
            "intervals": interval_rows,
        }
    return {
        "nodes": result,
        "observability": {
            "exact_inter_reception_distribution": False,
            "broad_reacquisition_count": False,
            "predicted_origin_phase_error": False,
            "reason": (
                "BEACON_STATUS is a sparse cumulative snapshot. It exports rx/miss "
                "counts but neither per-reception timestamps, a broad-vs-narrow "
                "counter, nor next_window_origin40/phase error. Listener records "
                "provide the true beacon origin only in the listener DW domain."
            ),
        },
    }


def analyze_d2(listener_path: Path, tag_src: int = 0xB102) -> dict[str, object]:
    last_transaction_ticks: int | None = None
    last_poll_ticks: int | None = None
    last_beacon_ticks: int | None = None
    tail_to_origin_us: list[float] = []
    poll_to_origin_us: list[float] = []
    rows = []
    record_count = 0
    relevant = 0
    with listener_path.open(encoding="utf-8") as handle:
        for raw in handle:
            record_count += 1
            row = json.loads(raw)
            if not row.get("parsed_ok"):
                continue
            kind = row.get("kind")
            f = row.get("fields", {})
            ticks = row.get("rx_unwrapped_ticks")
            if not isinstance(ticks, int):
                continue
            if kind == "LPD" and int(f.get("src", -1)) == tag_src:
                last_poll_ticks = ticks
                last_transaction_ticks = ticks
                relevant += 1
            elif kind == "LRD" and int(f.get("dst", -1)) == tag_src:
                last_transaction_ticks = ticks
                relevant += 1
            elif (kind == "LBD" and int(f.get("beacon_index", -1)) == 0
                  and int(f.get("src", -1)) == 0xBC00):
                if last_beacon_ticks is not None and ticks <= last_beacon_ticks:
                    continue
                period_ticks = (ticks - last_beacon_ticks) if last_beacon_ticks is not None else None
                period_us = period_ticks / DW_TICKS_PER_US if period_ticks else None
                last_beacon_ticks = ticks
                tail = None
                poll_tail = None
                if last_transaction_ticks is not None:
                    value = (ticks - last_transaction_ticks) / DW_TICKS_PER_US
                    if 0.0 < value < 110_000.0:
                        tail = value
                if last_poll_ticks is not None:
                    value = (ticks - last_poll_ticks) / DW_TICKS_PER_US
                    if 0.0 < value < 110_000.0:
                        poll_tail = value
                # A missed beacon at this observer merges two superframes and
                # makes the prior epoch's transaction look ~110 ms old.  Only
                # a consecutive-main pair can bound the slot-10 tail.
                consecutive_main = period_us is not None and 105_000.0 <= period_us <= 115_000.0
                if tail is not None and consecutive_main:
                    tail_to_origin_us.append(tail)
                    if poll_tail is not None:
                        poll_to_origin_us.append(poll_tail)
                    rows.append({
                        "superframe_counter": int(f.get("superframe_counter", 0)),
                        "tail_to_origin_us": tail,
                        "poll_to_origin_us": poll_tail,
                        "period_us": period_us,
                    })
                # Transactions from an earlier superframe are not a tail
                # observation for the next one.  Start a fresh epoch bucket.
                last_transaction_ticks = None
                last_poll_ticks = None
    return {
        "listener": str(listener_path),
        "tag_source_internal": f"0x{tag_src:04X}",
        "listener_records_scanned": record_count,
        "tag_transactions_seen": relevant,
        "epochs_with_observed_tail": len(rows),
        "last_observed_transaction_to_next_origin_us": distribution(tail_to_origin_us),
        "expected_slot_tail_cluster_under_20ms_us": distribution(
            [value for value in tail_to_origin_us if value < 20_000.0]
        ),
        "cross_phase_cluster_over_90ms_us": distribution(
            [value for value in tail_to_origin_us if value > 90_000.0]
        ),
        "poll_to_next_origin_us": distribution(poll_to_origin_us),
        "software_budget_to_window_start_us": distribution(
            [value - 500.0 for value in tail_to_origin_us
             if value < 20_000.0]
        ),
        "limitations": (
            "A passive listener can miss the true final response, so these tail "
            "values are upper bounds on CPU time available after the real final "
            "transaction. The logs contain no tag CPU service timestamp and no "
            "per-epoch beacon-accepted marker, so required software time and the "
            "caught-vs-missed partition are not resolvable offline."
        ),
        "sample_rows": rows[:200],
    }


def analyze_d3(fusion_path: Path) -> dict[str, object]:
    stats = {
        node: {
            "kinds": defaultdict(lambda: {"count": 0, "first": None, "last": None,
                                           "previous": None, "gaps": []}),
            "telemetry": [], "queue": [], "link_events": [],
        } for node in TARGETS
    }
    with fusion_path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not any(node in line for node in TARGETS):
                continue
            parts = line.split(" ", 3)
            if len(parts) < 4:
                continue
            try:
                host = float(parts[1])
            except ValueError:
                continue
            f = fields(line)
            node = f.get("name")
            if node not in TARGETS:
                continue
            kind = None
            for candidate in ("FUSION_UWB", "FUSION_IMU", "FUSION_TELEMETRY", "FUSION_QUEUE"):
                if candidate in line:
                    kind = candidate
                    break
            if kind in ("FUSION_UWB", "FUSION_IMU"):
                row = stats[node]["kinds"][kind]
                row["count"] += 1
                row["first"] = host if row["first"] is None else row["first"]
                if row["previous"] is not None and host - row["previous"] > 30.0:
                    row["gaps"].append({"start": row["previous"], "end": host,
                                        "duration_s": host - row["previous"]})
                row["previous"] = host
                row["last"] = host
            elif kind == "FUSION_TELEMETRY":
                stats[node]["telemetry"].append({"host": host, **f})
            elif kind == "FUSION_QUEUE":
                stats[node]["queue"].append({"host": host, **f})
            elif "FUSION_CONNECTED" in line or "FUSION_DISCONNECTED" in line:
                stats[node]["link_events"].append({"host": host, "line": line.strip()})
    result = {}
    keys = (
        "node_ms", "frames", "last_sweep", "have", "subscribed", "reorder",
        "uart_restarts", "uart_err", "drop_err", "notify_errno",
        "imu_records", "imu_active", "imu_i2c_err", "imu_hreset",
        "imu_hrecover_ok", "imu_hrecover_fail", "reset_reason",
    )
    for node, data in stats.items():
        kinds = data["kinds"]
        major_gaps = []
        for stream_name in ("FUSION_UWB", "FUSION_IMU"):
            for gap in kinds[stream_name]["gaps"]:
                if gap["duration_s"] >= 300.0:
                    major_gaps.append({"stream": stream_name, **gap})
        major_gaps.sort(key=lambda row: row["start"])
        first_major_gap = major_gaps[0] if major_gaps else None
        stall_start = first_major_gap["start"] if first_major_gap else None
        last_data = max(
            (kinds[k]["last"] for k in ("FUSION_UWB", "FUSION_IMU")
             if kinds[k]["last"] is not None), default=None,
        )
        telemetry = data["telemetry"]
        before = after = None
        if last_data is not None:
            for row in telemetry:
                if row["host"] <= last_data:
                    before = row
                elif after is None:
                    after = row
        telemetry_before_stall = telemetry_after_stall = None
        queue_before_stall = queue_after_stall = None
        if stall_start is not None:
            for row in telemetry:
                if row["host"] <= stall_start:
                    telemetry_before_stall = row
                elif telemetry_after_stall is None:
                    telemetry_after_stall = row
            for row in data["queue"]:
                if row["host"] <= stall_start:
                    queue_before_stall = row
                elif queue_after_stall is None:
                    queue_after_stall = row
        def select(row):
            return ({"host": row["host"], **{k: row.get(k) for k in keys if k in row}}
                    if row else None)
        result[node] = {
            "streams": {
                k: {key: value for key, value in dict(v).items() if key != "previous"}
                for k, v in kinds.items()
            },
            "last_data_host_monotonic": last_data,
            "first_major_gap": first_major_gap,
            "major_gaps": major_gaps,
            "last_telemetry_before_first_stall": select(telemetry_before_stall),
            "first_telemetry_after_first_stall": select(telemetry_after_stall),
            "last_queue_before_first_stall": queue_before_stall,
            "first_queue_after_first_stall": queue_after_stall,
            "last_telemetry_before_data_end": select(before),
            "first_telemetry_after_data_end": select(after),
            "last_telemetry": select(telemetry[-1] if telemetry else None),
            "last_queue": data["queue"][-1] if data["queue"] else None,
            "link_events": data["link_events"],
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", required=True, type=Path)
    parser.add_argument("--listener", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = {
        "d1": analyze_d1(args.capture_root / "snapshots.json"),
        "d2": analyze_d2(args.listener),
        "d3": analyze_d3(args.capture_root / "fusion_cdc.log"),
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
