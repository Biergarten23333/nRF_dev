#!/usr/bin/env python3
"""Phase E3 V-C1 three-stream capture and E4 host-latency analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from fusion_session import (
    ANOMALY_COUNTERS,
    FusionController,
    LineChannel,
    SessionError,
    acquire_owner_lock,
    counter_deltas,
    imu_sequence_gaps,
    parse_fields,
    parse_reply,
    resolve_fusion_port,
    u32_delta,
)
from phase_c_long_validation import distribution, imu_timing


RAW_RE = re.compile(
    r"^(?P<wall>[0-9.]+) (?P<mono>[0-9.]+) FUSION_RX (?P<line>.*)$"
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (
        ordered[upper] - ordered[lower]
    ) * (position - lower)


def unwrap_u32(values: list[int]) -> list[int]:
    if not values:
        return []
    result = [values[0]]
    high = 0
    previous = values[0]
    for value in values[1:]:
        if value < previous and previous - value > 0x80000000:
            high += 1 << 32
        result.append(high + value)
        previous = value
    return result


def linear_latency(points: list[tuple[int, float]]) -> dict[str, object]:
    if len(points) < 2:
        return {"count": len(points), "error": "insufficient points"}
    raw_device = [point[0] for point in points]
    device = unwrap_u32(raw_device)
    host_us = [point[1] * 1_000_000.0 for point in points]
    mean_x = sum(device) / len(device)
    mean_y = sum(host_us) / len(host_us)
    variance_x = sum((value - mean_x) ** 2 for value in device)
    slope = (
        sum(
            (x_value - mean_x) * (y_value - mean_y)
            for x_value, y_value in zip(device, host_us)
        )
        / variance_x
    )
    intercept = mean_y - slope * mean_x
    residuals = [
        y_value - (intercept + slope * x_value)
        for x_value, y_value in zip(device, host_us)
    ]
    lower = min(residuals)
    normalized = [value - lower for value in residuals]
    bins_ms = list(range(0, 61, 5))
    histogram = {
        f"[{lo},{hi})ms": sum(
            lo * 1000.0 <= value < hi * 1000.0 for value in normalized
        )
        for lo, hi in zip(bins_ms, bins_ms[1:])
    }
    histogram["[60,inf)ms"] = sum(
        value >= 60_000.0 for value in normalized
    )
    return {
        "count": len(points),
        "fit_host_us_per_device_us": slope,
        "fit_device_rate_error_ppm": (slope - 1.0) * 1_000_000.0,
        "signed_residual_us": {
            "minimum": lower,
            "p50": percentile(residuals, 0.50),
            "p95": percentile(residuals, 0.95),
            "maximum": max(residuals),
        },
        "lower_envelope_normalized_latency_us": {
            "minimum": 0.0,
            "p50": percentile(normalized, 0.50),
            "p95": percentile(normalized, 0.95),
            "p99": percentile(normalized, 0.99),
            "maximum": max(normalized),
            "histogram": histogram,
        },
        "interpretation": (
            "The fit removes the unknown constant clock offset. The "
            "lower-envelope-normalized values measure variable pipeline "
            "latency; absolute constant latency is not identifiable from "
            "unsynchronised host and node clocks."
        ),
    }


def parse_capture_raw(
    path: Path, start_mono: float, end_mono: float
) -> tuple[list[str], list[tuple[int, float]], list[tuple[int, float]], list[str]]:
    lines: list[str] = []
    uwb_points: list[tuple[int, float]] = []
    imu_points: list[tuple[int, float]] = []
    connection_lines: list[str] = []
    for raw in path.read_text(errors="replace").splitlines():
        match = RAW_RE.match(raw)
        if match is None:
            continue
        mono = float(match.group("mono"))
        line = match.group("line")
        if line.startswith(
            ("FUSION_BRIDGE_READY ", "FUSION_CI_CURRENT ", "FUSION_CI_UPDATED ")
        ):
            connection_lines.append(line)
        if not (start_mono <= mono <= end_mono):
            continue
        lines.append(line)
        fields = parse_fields(line)
        if line.startswith("FUSION_UWB ") and "frame_us" in fields:
            uwb_points.append((int(fields["frame_us"], 0), mono))
        elif line.startswith("FUSION_IMU ") and "base_us" in fields:
            base = int(fields["base_us"], 0)
            sample_deltas = []
            for encoded in fields.get("samples", "").split(";"):
                if encoded:
                    sample_deltas.append(int(encoded.split(",", 1)[0], 0))
            if sample_deltas:
                imu_points.append(
                    ((base + max(sample_deltas)) & 0xFFFFFFFF, mono)
                )
    return lines, uwb_points, imu_points, connection_lines


def command(controller: FusionController, text: str, prefix: str):
    return controller.command(
        text,
        lambda reply: reply.startswith(prefix),
        allow_resend_after_tx=False,
    )


def relay_command(controller: FusionController, text: str) -> dict[str, object]:
    queued = command(controller, text, "RELAY_QUEUED")
    reply_line = controller.read_until(
        lambda line: (
            (reply := parse_reply(line)) is not None
            and reply.source == "TAG"
            and reply.correlation == queued.correlation
        ),
        2.2,
        f"{text} TAG reply correlation={queued.correlation}",
    )
    reply = parse_reply(reply_line)
    assert reply is not None
    if reply.text == "TIMEOUT":
        raise SessionError(f"{text}: tag relay timed out")
    return {"command": text, "queued": queued.__dict__, "tag": reply.__dict__}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--port")
    parser.add_argument("--bsf", default="BSF3C79")
    parser.add_argument("--duration-s", type=float, default=1800.0)
    parser.add_argument("--control-period-s", type=float, default=60.0)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=False)
    predictions = {
        "written_before_cdc_open": True,
        "duration_s": args.duration_s,
        "preflight": "Remote B306 REBOOT before the long capture.",
        "capture": {
            "imu_rate_hz": 200,
            "imu_batch": 2,
            "imu_sequence_gaps": 0,
            "device_error_deltas": 0,
            "uwb_rate_hz_range": [8.0, 12.0],
            "all_uwb_records_healthy": True,
            "periodic_local_and_tag_control_all_acknowledged": True,
        },
        "latency": {
            "predicted_dominant_term": "BLE connection interval",
            "predicted_shape": "approximately uniform",
            "predicted_width_us": 50000,
            "slave_latency": 0,
        },
    }
    write_json(args.out_dir / "predictions.json", predictions)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json(args.out_dir / "summary.json", summary)

    owner_lock = acquire_owner_lock(args.out_dir.parent)
    channel = None
    controller = None
    imu_started = False
    raw_path = args.out_dir / "raw.log"
    try:
        port = resolve_fusion_port(args.port)
        summary["port"] = port
        with raw_path.open("a", buffering=1) as raw_log:
            channel = LineChannel(port, raw_log, "FUSION")
            controller = FusionController(channel, args.bsf, args.timeout, 3)
            summary["bridge_before_reboot"] = controller.ensure_bridge()
            summary["reboot"] = controller.reboot_preflight()
            summary["bridge_after_reboot"] = controller.ensure_bridge()
            ping = command(controller, "PING", "PONG ")
            if "fw=b306-imu-relay-v18-cr2" not in ping.text:
                raise SessionError(f"unexpected B306 marker: {ping.text}")
            summary["ping"] = ping.__dict__
            status = command(controller, "IMU STATUS", "IMU ")
            if "active=0 " not in f"{status.text} ":
                raise SessionError(f"preflight IMU not stopped: {status.text}")
            summary["initial_status"] = status.__dict__
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

            capture_start = time.monotonic()
            capture_end = capture_start + args.duration_s
            summary["capture_start_monotonic"] = capture_start
            raw_log.write(
                f"{time.time():.6f} {capture_start:.6f} "
                "FUSION_EVENT CAPTURE_START\n"
            )
            raw_log.flush()
            controls: list[dict[str, object]] = []
            next_control = capture_start + args.control_period_s
            relay_cycle = ("TAG PING", "TAG STATUS", "TAG RAW VERSION")
            control_index = 0
            while time.monotonic() < capture_end:
                deadline = min(next_control, capture_end)
                controller.collect(max(0.0, deadline - time.monotonic()))
                if next_control > capture_end:
                    break
                local = command(controller, "STATUS", "STATUS ")
                relay = relay_command(
                    controller, relay_cycle[control_index % len(relay_cycle)]
                )
                controls.append(
                    {
                        "elapsed_s": time.monotonic() - capture_start,
                        "local": local.__dict__,
                        "relay": relay,
                    }
                )
                control_index += 1
                next_control += args.control_period_s
            capture_end_actual = time.monotonic()
            raw_log.write(
                f"{time.time():.6f} {capture_end_actual:.6f} "
                "FUSION_EVENT CAPTURE_END\n"
            )
            raw_log.flush()
            summary["capture_end_monotonic"] = capture_end_actual
            summary["controls"] = controls

            controller.collect(1.2)
            final = controller.latest_telemetry
            if final is None:
                raise SessionError("capture lacked final telemetry")
            summary["final_before_stop"] = final
            stop = command(controller, "IMU STOP", "IMU STOP ")
            summary["stop"] = stop.__dict__
            imu_started = False
            summary["post_stop_counters"] = controller.counters()
            summary["post_stop_telemetry"] = controller.wait_telemetry()

        lines, uwb_points, imu_points, connection_lines = parse_capture_raw(
            raw_path, capture_start, capture_end_actual
        )
        gaps, imu_records = imu_sequence_gaps(lines)
        imu_lines = [line for line in lines if line.startswith("FUSION_IMU ")]
        imu_samples = sum(
            int(parse_fields(line).get("n", "0"), 0) for line in imu_lines
        )
        uwb_lines = [line for line in lines if line.startswith("FUSION_UWB ")]
        healthy_uwb = sum(
            " verdict=healthy " in f" {line} " for line in uwb_lines
        )
        pair_deltas = [
            int(parse_fields(line)["pair_dt_us"], 0)
            for line in uwb_lines
            if parse_fields(line).get("pair_dt_us") not in (None, "-")
        ]
        node_dt_s = u32_delta(
            int(baseline["node_ms"], 0), int(final["node_ms"], 0)
        ) / 1000.0
        frame_delta = u32_delta(
            int(baseline["frames"], 0), int(final["frames"], 0)
        )
        anomaly_deltas = counter_deltas(baseline, final, ANOMALY_COUNTERS)
        nonzero = {
            key: value for key, value in anomaly_deltas.items() if value != 0
        }
        disconnects = [
            line for line in lines if line.startswith("FUSION_DISCONNECTED ")
        ]
        ci_records = [
            {"line": line, **parse_fields(line)} for line in connection_lines
        ]
        final_ci = ci_records[-1] if ci_records else {}
        expected_controls = math.floor(
            args.duration_s / args.control_period_s
        )
        analysis = {
            "host_elapsed_s": capture_end_actual - capture_start,
            "node_elapsed_s": node_dt_s,
            "imu_records": imu_records,
            "imu_samples": imu_samples,
            "imu_sequence_gaps": gaps,
            "imu_timing": imu_timing(lines),
            "uwb_records": len(uwb_lines),
            "healthy_uwb_records": healthy_uwb,
            "uwb_frame_delta": frame_delta,
            "uwb_rate_hz": frame_delta / node_dt_s if node_dt_s else 0.0,
            "strobe_to_frame_delta": distribution(pair_deltas),
            "anomaly_counter_deltas": anomaly_deltas,
            "nonzero_anomaly_counter_deltas": nonzero,
            "unexpected_disconnects": disconnects,
            "control_count": len(summary["controls"]),
            "expected_control_count": expected_controls,
            "connection_records": ci_records,
            "final_connection_parameters": final_ci,
            "latency": {
                "uwb_from_uart_callback_frame_stamp": linear_latency(uwb_points),
                "imu_from_last_trigger_in_record": linear_latency(imu_points),
                "all_records": linear_latency(uwb_points + imu_points),
            },
        }
        gates = {
            "imu_sequence": gaps == 0,
            "imu_sample_fraction": (
                imu_samples >= args.duration_s * 200.0 * 0.99
            ),
            "uwb_health": (
                len(uwb_lines) > 0 and healthy_uwb == len(uwb_lines)
            ),
            "uwb_pairing": len(pair_deltas) == len(uwb_lines),
            "uwb_rate": 8.0 <= analysis["uwb_rate_hz"] <= 12.0,
            "device_counters": not nonzero,
            "connection": not disconnects,
            "control_stream": len(summary["controls"]) == expected_controls,
            "slave_latency": final_ci.get("latency") == "0",
            "imu_stopped": summary["stop"]["text"].startswith("IMU STOP OK "),
        }
        analysis["gates"] = gates
        analysis["pass"] = all(gates.values())
        summary["analysis"] = analysis
        summary["status"] = "PASS" if analysis["pass"] else "FAILED"
        summary["raw_sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
        summary["completed_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(args.out_dir / "analysis.json", analysis)
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        summary["completed_utc"] = datetime.now(timezone.utc).isoformat()
        if imu_started and controller is not None:
            try:
                summary["rollback_stop"] = command(
                    controller, "IMU STOP", "IMU STOP "
                ).__dict__
                imu_started = False
            except Exception as rollback_exc:
                summary["rollback_stop_error"] = str(rollback_exc)
        raise
    finally:
        if channel is not None:
            channel.close()
        owner_lock.close()
        write_json(args.out_dir / "summary.json", summary)

    print(f"PHASE E3/E4 VERDICT: {summary['status']}", flush=True)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
