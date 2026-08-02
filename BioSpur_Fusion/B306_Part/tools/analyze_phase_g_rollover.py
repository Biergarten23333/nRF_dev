#!/usr/bin/env python3
"""Analyze the pre-registered Phase G TIMER2 rollover acceptance run."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path


FIELD_RE = re.compile(r"(?P<key>[A-Za-z0-9_]+)=(?P<value>\S+)")
KINDS = ("FUSION_UWB", "FUSION_IMU", "FUSION_TELEMETRY", "FUSION_HEALTH")
ERROR_FIELDS = (
    "crc",
    "header",
    "ring_drop",
    "sweep_drop",
    "duplicate",
    "reorder",
    "drop_err",
    "uart_restarts",
    "edge_qdrop",
    "orphan_strobe",
    "orphan_edge",
    "orphan_frame",
    "imu_i2c_err",
    "imu_hreset",
    "imu_hfrozen",
    "imu_hrate",
    "imu_hcanary",
    "imu_hplaus",
    "imu_hdead",
    "imu_hident",
    "imu_hi2c",
    "imu_hrecover_fail",
    "malformed",
    "logger_drop",
)
DELTA_HIST_LABELS = (
    "<=-101",
    "-100..-51",
    "-50..-21",
    "-20..-11",
    "-10..-6",
    "-5",
    "-4",
    "-3",
    "-2",
    "-1",
    "0",
    "+1",
    "+2",
    "+3",
    "+4",
    "+5",
    "+6..+10",
    "+11..+20",
    "+21..+50",
    "+51..+100",
    ">=+101",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="default: <run_dir>/phase_g_analysis.json",
    )
    return parser.parse_args()


def fields(text: str) -> dict[str, str]:
    return {match["key"]: match["value"] for match in FIELD_RE.finditer(text)}


def payload(line: str) -> tuple[str, str] | None:
    for kind in KINDS:
        offset = line.find(kind + " ")
        if offset >= 0:
            return kind, line[offset:]
    return None


def as_int(item: dict[str, str], key: str) -> int:
    return int(item[key], 0)


def percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def distribution(values: list[int]) -> dict:
    histogram_edges = (
        14_000,
        14_250,
        14_500,
        14_750,
        15_000,
        15_250,
        15_500,
        16_000,
        16_500,
        17_000,
        17_500,
    )
    histogram_labels = (
        "<14000",
        "14000..14249",
        "14250..14499",
        "14500..14749",
        "14750..14999",
        "15000..15249",
        "15250..15499",
        "15500..15999",
        "16000..16499",
        "16500..16999",
        "17000..17499",
        ">=17500",
    )
    histogram = {label: 0 for label in histogram_labels}
    for value in values:
        bucket = next(
            (
                index
                for index, edge in enumerate(histogram_edges)
                if value < edge
            ),
            len(histogram_edges),
        )
        histogram[histogram_labels[bucket]] += 1
    return {
        "count": len(values),
        "mean_us": statistics.fmean(values) if values else None,
        "p50_us": percentile(values, 0.50),
        "p90_us": percentile(values, 0.90),
        "p99_us": percentile(values, 0.99),
        "max_us": max(values) if values else None,
        "min_us": min(values) if values else None,
        "histogram_us": histogram,
    }


def monotonic(values: list[int]) -> dict:
    failures = [
        {
            "index": index,
            "previous": previous,
            "current": current,
            "delta": current - previous,
        }
        for index, (previous, current) in enumerate(
            zip(values, values[1:]), start=1
        )
        if current <= previous
    ]
    return {
        "count": len(values),
        "strict": not failures,
        "failures": failures[:20],
    }


def counter_delta(
    first: dict[str, str], last: dict[str, str], key: str
) -> int | None:
    if key not in first or key not in last:
        return None
    return (int(last[key], 0) - int(first[key], 0)) & 0xFFFFFFFF


def parse_delta_pages(pages: dict) -> dict | None:
    try:
        page_fields = [
            fields(pages[str(page)]["text"]) for page in range(3)
        ]
        histogram: list[int] = []
        for item in page_fields:
            histogram.extend(int(value, 0) for value in item["h"].split(","))
        if len(histogram) != len(DELTA_HIST_LABELS):
            return None
        return {
            "count_at_page0": int(page_fields[0]["n"], 0),
            "minimum_ms_to_page0": int(page_fields[0]["min_ms"], 0),
            "maximum_ms_to_page0": int(page_fields[0]["max_ms"], 0),
            "maximum_absolute_ms_to_page0": int(
                page_fields[0]["maxabs_ms"], 0
            ),
            "histogram": dict(zip(DELTA_HIST_LABELS, histogram)),
            "histogram_sum_across_pages": sum(histogram),
            "page_skew_observations": (
                sum(histogram) - int(page_fields[0]["n"], 0)
            ),
        }
    except (KeyError, TypeError, ValueError):
        return None


def residual_window(start: dict, end: dict) -> dict:
    histogram = {
        label: end["parsed"]["histogram"][label]
        - start["parsed"]["histogram"][label]
        for label in DELTA_HIST_LABELS
    }
    histogram_sum = sum(histogram.values())
    fractions = {
        label: value / histogram_sum if histogram_sum else None
        for label, value in histogram.items()
    }
    return {
        "target_duration_s": (
            end["target_elapsed_s"] - start["target_elapsed_s"]
        ),
        "actual_duration_s": (
            end["actual_elapsed_s"] - start["actual_elapsed_s"]
        ),
        "count_delta_at_page0": (
            end["parsed"]["count_at_page0"]
            - start["parsed"]["count_at_page0"]
        ),
        "histogram": histogram,
        "histogram_sum_across_pages": histogram_sum,
        "fractions": fractions,
        "page_skew_delta": (
            end["parsed"]["page_skew_observations"]
            - start["parsed"]["page_skew_observations"]
        ),
    }


def main() -> int:
    args = parse_args()
    raw_path = args.run_dir / "raw.log"
    summary_path = args.run_dir / "summary.json"
    output = args.output or args.run_dir / "phase_g_analysis.json"
    summary = json.loads(summary_path.read_text())

    uwb: list[dict[str, str]] = []
    imu: list[dict[str, str]] = []
    telemetry: list[dict[str, str]] = []
    health: list[dict[str, str]] = []
    disconnect_lines: list[str] = []
    bridge_ready_lines: list[str] = []
    malformed_raw_lines: list[str] = []

    formal_active = False
    formal_found = False
    for raw in raw_path.read_text(errors="replace").splitlines():
        if "text=IMU START OK " in f"{raw} ":
            formal_active = True
            formal_found = True
            continue
        if formal_active and re.search(
            r"FUSION(?:_RTT)?_TX BSF[0-9A-Fa-f]{4} IMU STOP$", raw
        ):
            break
        if not formal_active:
            continue
        if "FUSION_DISCONNECTED" in raw:
            disconnect_lines.append(raw)
        if "FUSION_BRIDGE_READY" in raw:
            bridge_ready_lines.append(raw)
        parsed = payload(raw)
        if parsed is None:
            continue
        kind, text = parsed
        item = fields(text)
        try:
            if kind == "FUSION_UWB":
                required = (
                    "frame_us",
                    "strobe_us",
                    "rise_us",
                    "fall_us",
                    "pair_dt_us",
                    "sweep",
                )
                if not all(key in item for key in required):
                    malformed_raw_lines.append(text)
                else:
                    uwb.append(item)
            elif kind == "FUSION_IMU":
                if not all(key in item for key in ("seq", "base_us", "n")):
                    malformed_raw_lines.append(text)
                else:
                    imu.append(item)
            elif kind == "FUSION_TELEMETRY":
                telemetry.append(item)
            elif kind == "FUSION_HEALTH":
                health.append(item)
        except ValueError:
            malformed_raw_lines.append(text)

    if not formal_found:
        raise SystemExit("formal IMU START marker not found")
    if not telemetry:
        raise SystemExit("no telemetry records")
    timer_bits = as_int(telemetry[-1], "timer_bits")
    period_us = 1 << timer_bits
    boundary_us = period_us
    exclusion_us = 2_000_000
    comparison_us = 300_000_000

    uwb_numeric = []
    for item in uwb:
        if any(item[key] == "-" for key in (
            "frame_us", "strobe_us", "rise_us", "fall_us", "pair_dt_us"
        )):
            continue
        uwb_numeric.append(
            {
                key: as_int(item, key)
                for key in (
                    "frame_us",
                    "strobe_us",
                    "rise_us",
                    "fall_us",
                    "pair_dt_us",
                    "sweep",
                )
            }
        )

    imu_numeric = [
        {
            "seq": as_int(item, "seq"),
            "base_us": as_int(item, "base_us"),
            "n": as_int(item, "n"),
        }
        for item in imu
    ]

    seq_failures = []
    missing_samples = 0
    for index, (previous, current) in enumerate(
        zip(imu_numeric, imu_numeric[1:]), start=1
    ):
        expected = (previous["seq"] + previous["n"]) & 0xFFFF
        if current["seq"] != expected:
            missing = (current["seq"] - expected) & 0xFFFF
            missing_samples += missing
            seq_failures.append(
                {
                    "index": index,
                    "previous_seq": previous["seq"],
                    "previous_n": previous["n"],
                    "expected": expected,
                    "current": current["seq"],
                    "missing": missing,
                    "base_us": current["base_us"],
                    "offset_from_boundary_us": (
                        current["base_us"] - boundary_us
                    ),
                }
            )

    before_latency = [
        row["pair_dt_us"]
        for row in uwb_numeric
        if boundary_us - exclusion_us - comparison_us
        <= row["strobe_us"]
        < boundary_us - exclusion_us
    ]
    after_latency = [
        row["pair_dt_us"]
        for row in uwb_numeric
        if boundary_us + exclusion_us
        < row["strobe_us"]
        <= boundary_us + exclusion_us + comparison_us
    ]
    latency_before = distribution(before_latency)
    latency_after = distribution(after_latency)
    latency_change = {
        key: (
            latency_after[key] - latency_before[key]
            if latency_after[key] is not None
            and latency_before[key] is not None
            else None
        )
        for key in ("mean_us", "p50_us", "p90_us", "p99_us", "max_us")
    }

    first_telemetry = summary.get("baseline", telemetry[0])
    last_telemetry = summary.get("final", telemetry[-1])
    deltas = {
        key: counter_delta(first_telemetry, last_telemetry, key)
        for key in ERROR_FIELDS
    }
    wrap_delta = counter_delta(
        first_telemetry, last_telemetry, "timer_wraps"
    )

    wrap_transitions = []
    for previous, current in zip(telemetry, telemetry[1:]):
        if (
            "timer_wraps" in previous
            and "timer_wraps" in current
            and previous["timer_wraps"] != current["timer_wraps"]
        ):
            wrap_transitions.append(
                {
                    "previous_node_ms": as_int(previous, "node_ms"),
                    "previous_wraps": as_int(previous, "timer_wraps"),
                    "current_node_ms": as_int(current, "node_ms"),
                    "current_wraps": as_int(current, "timer_wraps"),
                }
            )

    health_events = []
    last_health_counts = {
        key: as_int(telemetry[0], key)
        for key in (
            "imu_hreset",
            "imu_hfrozen",
            "imu_hrate",
            "imu_hcanary",
            "imu_hplaus",
            "imu_hdead",
            "imu_hident",
            "imu_hi2c",
        )
        if key in telemetry[0]
    }
    for item in telemetry[1:]:
        current_counts = {
            key: as_int(item, key) for key in last_health_counts
        }
        if current_counts != last_health_counts:
            last_good_hex, fault_hex, recovered_hex = item["imu_hwin"].split("/")
            fault_us = int(fault_hex, 16)
            recovered_us = int(recovered_hex, 16)
            health_events.append(
                {
                    "node_ms": as_int(item, "node_ms"),
                    "class_state": item["imu_health"],
                    "counter_before": last_health_counts,
                    "counter_after": current_counts,
                    "last_good_us": int(last_good_hex, 16),
                    "fault_us": fault_us,
                    "recovered_us": recovered_us,
                    "fault_offset_from_boundary_us": fault_us - boundary_us,
                    "recovery_offset_from_boundary_us": (
                        recovered_us - boundary_us
                    ),
                    "inside_exclusion": (
                        abs(fault_us - boundary_us) <= exclusion_us
                        or abs(recovered_us - boundary_us) <= exclusion_us
                    ),
                }
            )
            last_health_counts = current_counts

    cdc_drop = {
        "first": as_int(health[0], "cdc_drop_bytes") if health else None,
        "last": as_int(health[-1], "cdc_drop_bytes") if health else None,
    }
    cdc_drop["delta"] = (
        cdc_drop["last"] - cdc_drop["first"]
        if cdc_drop["first"] is not None
        else None
    )

    boundary_rows = [
        row
        for row in uwb_numeric
        if abs(row["strobe_us"] - boundary_us) <= exclusion_us
    ]
    boundary_imu = [
        row
        for row in imu_numeric
        if abs(row["base_us"] - boundary_us) <= exclusion_us
    ]
    for failure in seq_failures:
        failure["inside_boundary_exclusion"] = (
            abs(failure["offset_from_boundary_us"]) <= exclusion_us
        )
        failure["health_event_index"] = next(
            (
                index
                for index, event in enumerate(health_events)
                if event["fault_us"] - exclusion_us
                <= failure["base_us"]
                <= event["recovered_us"] + exclusion_us
            ),
            None,
        )
    boundary_seq_failures = [
        failure
        for failure in seq_failures
        if failure["inside_boundary_exclusion"]
    ]
    unexplained_seq_failures = [
        failure
        for failure in seq_failures
        if failure["health_event_index"] is None
    ]

    delta_pages = {
        page: item["text"]
        for page, item in summary.get("delta", {}).items()
    }
    delta_parsed = parse_delta_pages(summary.get("delta", {}))
    residual_snapshots = {}
    for label, snapshot in summary.get("delta_snapshots", {}).items():
        parsed = parse_delta_pages(snapshot.get("pages", {}))
        residual_snapshots[label] = snapshot | {"parsed": parsed}
    residual_windows = None
    if (
        set(residual_snapshots)
        >= {"before_start", "before_end", "after_start", "after_end"}
        and all(item["parsed"] is not None for item in residual_snapshots.values())
    ):
        before_window = residual_window(
            residual_snapshots["before_start"],
            residual_snapshots["before_end"],
        )
        after_window = residual_window(
            residual_snapshots["after_start"],
            residual_snapshots["after_end"],
        )
        fraction_delta = {
            label: (
                after_window["fractions"][label]
                - before_window["fractions"][label]
            )
            for label in DELTA_HIST_LABELS
        }
        residual_windows = {
            "before": before_window,
            "after": after_window,
            "after_minus_before_fraction": fraction_delta,
            "total_variation_distance": (
                sum(abs(value) for value in fraction_delta.values()) / 2.0
            ),
            "maximum_absolute_bin_fraction_change": max(
                abs(value) for value in fraction_delta.values()
            ),
            "interpretation": (
                "descriptive G4 comparison; sensor-health residuals are not "
                "a Verdict-A gate"
            ),
        }
    verdict_a = {
        "timer_wrap_exactly_one": wrap_delta == 1,
        "wrap_transition_count": len(wrap_transitions),
        "uwb_all_timestamps_strict": all(
            monotonic([row[key] for row in uwb_numeric])["strict"]
            for key in ("frame_us", "strobe_us", "rise_us", "fall_us")
        ),
        "imu_base_strict": monotonic(
            [row["base_us"] for row in imu_numeric]
        )["strict"],
        "zero_boundary_imu_sequence_gaps": not boundary_seq_failures,
        "zero_unexplained_imu_sequence_gaps": not unexplained_seq_failures,
        "zero_transport_counter_deltas": all(
            value == 0
            for key, value in deltas.items()
            if key not in (
                "imu_i2c_err",
                "imu_hreset",
                "imu_hfrozen",
                "imu_hrate",
                "imu_hcanary",
                "imu_hplaus",
                "imu_hdead",
                "imu_hident",
                "imu_hi2c",
            )
            and value is not None
        ),
        "no_disconnect_during_capture": not disconnect_lines,
        "no_boundary_health_confounder": not any(
            event["inside_exclusion"] for event in health_events
        ),
        "host_cdc_drop_delta_zero": cdc_drop["delta"] == 0,
        "host_malformed_lines_zero": not malformed_raw_lines,
    }
    verdict_a["pass"] = all(
        value is True
        for key, value in verdict_a.items()
        if key not in ("wrap_transition_count",)
    ) and len(wrap_transitions) == 1

    result = {
        "run_dir": str(args.run_dir),
        "timer_bits": timer_bits,
        "boundary_us": boundary_us,
        "exclusion_us": exclusion_us,
        "comparison_window_us": comparison_us,
        "counts": {
            "uwb_records": len(uwb),
            "uwb_numeric_records": len(uwb_numeric),
            "imu_records": len(imu_numeric),
            "imu_samples": sum(row["n"] for row in imu_numeric),
            "telemetry_records": len(telemetry),
        },
        "timer_wrap_delta": wrap_delta,
        "timer_wrap_transitions": wrap_transitions,
        "monotonic": {
            key: monotonic([row[key] for row in uwb_numeric])
            for key in ("frame_us", "strobe_us", "rise_us", "fall_us")
        }
        | {
            "imu_base_us": monotonic(
                [row["base_us"] for row in imu_numeric]
            )
        },
        "imu_sequence": {
            "gap_events": len(seq_failures),
            "missing_samples": missing_samples,
            "boundary_gap_events": len(boundary_seq_failures),
            "unexplained_gap_events": len(unexplained_seq_failures),
            "failures": seq_failures[:100],
        },
        "counter_deltas": deltas,
        "host": {
            "disconnect_lines": disconnect_lines,
            "bridge_ready_count": len(bridge_ready_lines),
            "cdc_drop_bytes": cdc_drop,
            "malformed_raw_lines": malformed_raw_lines[:20],
        },
        "boundary": {
            "uwb_records_in_exclusion": boundary_rows,
            "imu_records_in_exclusion_count": len(boundary_imu),
            "first_imu_in_exclusion": boundary_imu[:1],
            "last_imu_in_exclusion": boundary_imu[-1:],
        },
        "latency": {
            "before": latency_before,
            "after": latency_after,
            "after_minus_before": latency_change,
        },
        "health_events": health_events,
        "delta_pages": delta_pages,
        "delta_parsed": delta_parsed,
        "residual_snapshots": residual_snapshots,
        "residual_windows": residual_windows,
        "verdict_a": verdict_a,
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        f"PHASE_G_ANALYSIS verdict_a={'PASS' if verdict_a['pass'] else 'FAIL'} "
        f"wrap_delta={wrap_delta} imu_gaps={len(seq_failures)} "
        f"health_events={len(health_events)} output={output}"
    )
    return 0 if verdict_a["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
