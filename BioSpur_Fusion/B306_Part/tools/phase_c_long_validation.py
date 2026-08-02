#!/usr/bin/env python3
"""Five-minute Phase-C health acceptance over Fusion Master native CDC."""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from fusion_session import (
    ANOMALY_COUNTERS,
    FusionController,
    LineChannel,
    SessionError,
    counter_deltas,
    imu_sequence_gaps,
    parse_fields,
    resolve_fusion_port,
    u32_delta,
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return (
        ordered[lower]
        + (ordered[upper] - ordered[lower]) * (position - lower)
    )


def distribution(values: list[int]) -> dict[str, object]:
    edges = [
        0,
        14500,
        15000,
        15500,
        16000,
        16500,
        17000,
        17500,
        18000,
        20000,
        50000,
        100000,
    ]
    histogram = {}
    for lower, upper in zip(edges, edges[1:]):
        histogram[f"[{lower},{upper})"] = sum(
            lower <= value < upper for value in values
        )
    histogram[f"[{edges[-1]},inf)"] = sum(
        value >= edges[-1] for value in values
    )
    return {
        "count": len(values),
        "minimum_us": min(values) if values else None,
        "p50_us": percentile(values, 0.50),
        "p90_us": percentile(values, 0.90),
        "p99_us": percentile(values, 0.99),
        "maximum_us": max(values) if values else None,
        "histogram_us": histogram,
    }


def imu_timing(lines: list[str]) -> dict[str, object]:
    sample_timestamps: list[int] = []
    intra_record_deltas: list[int] = []
    record_base_timestamps: list[int] = []
    sample_count = 0
    for line in lines:
        if not line.startswith("FUSION_IMU "):
            continue
        fields = parse_fields(line)
        if "base_us" not in fields or "samples" not in fields:
            continue
        base = int(fields["base_us"], 0)
        record_base_timestamps.append(base)
        for encoded in fields["samples"].split(";"):
            delta = int(encoded.split(",", 1)[0], 0)
            intra_record_deltas.append(delta)
            sample_timestamps.append((base + delta) & 0xFFFFFFFF)
            sample_count += 1

    sample_deltas = [
        u32_delta(previous, current)
        for previous, current in zip(
            sample_timestamps, sample_timestamps[1:]
        )
    ]
    timestamp_span_us = sum(sample_deltas)
    missed_deadlines = sum(
        max(0, delta // 5000 - 1)
        for delta in sample_deltas
        if delta >= 5000
    )
    record_base_deltas = [
        u32_delta(previous, current)
        for previous, current in zip(
            record_base_timestamps, record_base_timestamps[1:]
        )
    ]
    return {
        "sample_count": sample_count,
        "effective_sample_rate_hz": (
            (sample_count - 1) * 1_000_000.0 / timestamp_span_us
            if sample_count > 1 and timestamp_span_us else 0.0
        ),
        "timestamp_span_us": timestamp_span_us,
        "intra_record_delta_values_us": sorted(set(intra_record_deltas)),
        "sample_delta_distribution": distribution(sample_deltas),
        "record_base_delta_distribution": distribution(record_base_deltas),
        "deadline_gaps_over_5ms": sum(
            delta > 5000 for delta in sample_deltas
        ),
        "deadline_slots_skipped": missed_deadlines,
        "scheduler_policy": (
            "absolute deadline; missed periods are skipped, not replayed"
        ),
    }


def command(controller: FusionController, text: str, prefix: str):
    return controller.command(
        text,
        lambda reply: reply.startswith(prefix),
        allow_resend_after_tx=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port")
    parser.add_argument("--bsf", default="BSF3C79")
    parser.add_argument("--duration-s", type=float, default=300.0)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=False)
    predictions = {
        "written_before_cdc_open": True,
        "duration_s": args.duration_s,
        "preflight": (
            "The immediately preceding verified cr2 OTA reboot is the "
            "fresh-uptime authority; no additional REBOOT is issued."
        ),
        "acceptance": {
            "imu_rate_hz": 200,
            "imu_batch": 2,
            "minimum_sample_fraction": 0.99,
            "imu_sequence_gaps": 0,
            "uwb_rate_hz_range": [8.0, 12.0],
            "all_uwb_records_healthy": True,
            "all_anomaly_counter_deltas_zero": True,
            "unexpected_disconnects": 0,
            "stop_must_succeed": True,
        },
    }
    write_json(args.out_dir / "predictions.json", predictions)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json(args.out_dir / "summary.json", summary)

    channel = None
    imu_started = False
    try:
        port = resolve_fusion_port(args.port)
        summary["port"] = port
        with (args.out_dir / "raw.log").open("a", buffering=1) as raw_log:
            channel = LineChannel(port, raw_log, "FUSION")
            controller = FusionController(channel, args.bsf, args.timeout, 3)
            summary["bridge"] = controller.ensure_bridge()
            status = command(controller, "IMU STATUS", "IMU ")
            summary["initial_status"] = status.__dict__
            if "active=1 " in f"{status.text} ":
                raise SessionError("preflight IMU was unexpectedly active")
            summary["clear"] = command(
                controller, "COUNTERS CLEAR", "COUNTERS CLEARED"
            ).__dict__
            summary["rate"] = command(
                controller, "IMU RATE=200", "IMU RATE OK "
            ).__dict__
            summary["batch"] = command(
                controller, "IMU BATCH=2", "IMU BATCH OK "
            ).__dict__
            baseline = controller.wait_telemetry()
            summary["baseline"] = baseline
            start = controller.command(
                "IMU START",
                lambda text: (
                    text.startswith("IMU START OK ")
                    and "61=0001:P" in text
                    and "03=000B:P" in text
                    and "1F=0002:P" in text
                    and "volatile=1" in text
                    and "saved=0" in text
                ),
                allow_resend_after_tx=False,
            )
            summary["start"] = start.__dict__
            imu_started = True
            started = time.monotonic()
            lines = controller.collect(args.duration_s)
            elapsed = time.monotonic() - started
            final = controller.latest_telemetry
            if final is None:
                raise SessionError("long capture lacked final telemetry")
            summary["final"] = final
            stop = command(controller, "IMU STOP", "IMU STOP ")
            summary["stop"] = stop.__dict__
            imu_started = False

            gaps, records = imu_sequence_gaps(lines)
            imu_lines = [
                line for line in lines if line.startswith("FUSION_IMU ")
            ]
            samples = sum(
                int(parse_fields(line).get("n", "0"), 0)
                for line in imu_lines
            )
            uwb_lines = [
                line for line in lines if line.startswith("FUSION_UWB ")
            ]
            pair_deltas = [
                int(parse_fields(line)["pair_dt_us"], 0)
                for line in uwb_lines
                if parse_fields(line).get("pair_dt_us") not in (None, "-")
            ]
            healthy_uwb = sum(
                " verdict=healthy " in f" {line} " for line in uwb_lines
            )
            node_dt_s = u32_delta(
                int(baseline["node_ms"], 0),
                int(final["node_ms"], 0),
            ) / 1000.0
            frame_delta = u32_delta(
                int(baseline["frames"], 0), int(final["frames"], 0)
            )
            anomaly_deltas = counter_deltas(
                baseline, final, ANOMALY_COUNTERS
            )
            nonzero = {
                key: value
                for key, value in anomaly_deltas.items()
                if value != 0
            }
            disconnects = [
                line
                for line in lines
                if line.startswith("FUSION_DISCONNECTED ")
            ]
            expected_samples = args.duration_s * 200.0
            uwb_rate = frame_delta / node_dt_s if node_dt_s else 0.0
            analysis = {
                "host_elapsed_s": elapsed,
                "node_elapsed_s": node_dt_s,
                "imu_records": records,
                "imu_samples": samples,
                "expected_imu_samples": expected_samples,
                "imu_sample_fraction": samples / expected_samples,
                "imu_timing": imu_timing(lines),
                "imu_sequence_gaps": gaps,
                "uwb_records": len(uwb_lines),
                "healthy_uwb_records": healthy_uwb,
                "strobe_to_frame_delta": distribution(pair_deltas),
                "uwb_frame_delta": frame_delta,
                "uwb_rate_hz": uwb_rate,
                "anomaly_counter_deltas": anomaly_deltas,
                "nonzero_anomaly_counter_deltas": nonzero,
                "unexpected_disconnects": disconnects,
                "final_imu_active_before_stop": final.get("imu_active"),
            }
            passes = {
                "sample_count": samples >= expected_samples * 0.99,
                "sequence": gaps == 0,
                "uwb_health": (
                    len(uwb_lines) > 0
                    and healthy_uwb == len(uwb_lines)
                ),
                "pair_distribution_complete": (
                    len(pair_deltas) == len(uwb_lines)
                ),
                "uwb_rate": 8.0 <= uwb_rate <= 12.0,
                "counters": not nonzero,
                "connection": not disconnects,
                "active_before_stop": final.get("imu_active") == "1",
                "stop": stop.text.startswith("IMU STOP OK "),
            }
            analysis["gates"] = passes
            analysis["pass"] = all(passes.values())
            summary["analysis"] = analysis
            summary["status"] = "PASS" if analysis["pass"] else "FAILED"
            summary["completed_utc"] = datetime.now(timezone.utc).isoformat()
            write_json(args.out_dir / "analysis.json", analysis)
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        summary["completed_utc"] = datetime.now(timezone.utc).isoformat()
        if imu_started and channel is not None:
            try:
                controller = FusionController(channel, args.bsf, args.timeout, 1)
                summary["rollback_stop"] = command(
                    controller, "IMU STOP", "IMU STOP "
                ).__dict__
            except Exception as rollback_exc:
                summary["rollback_stop_error"] = str(rollback_exc)
        raise
    finally:
        if channel is not None:
            channel.close()
        write_json(args.out_dir / "summary.json", summary)

    print(f"PHASE C LONG VERDICT: {summary['status']}", flush=True)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
