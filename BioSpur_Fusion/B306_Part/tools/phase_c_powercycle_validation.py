#!/usr/bin/env python3
"""Phase-C mid-capture Fusion-PCB power-cycle acceptance over native CDC."""

from __future__ import annotations

import argparse
import json
import sys
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
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def command(controller: FusionController, text: str, prefix: str):
    return controller.command(
        text,
        lambda reply: reply.startswith(prefix),
        allow_resend_after_tx=False,
    )


def imu_records(lines: list[str]) -> int:
    return sum(line.startswith("FUSION_IMU ") for line in lines)


def healthy_uwb(lines: list[str]) -> tuple[int, int]:
    total = sum(line.startswith("FUSION_UWB ") for line in lines)
    healthy = sum(
        line.startswith("FUSION_UWB ")
        and " verdict=healthy " in f" {line} "
        for line in lines
    )
    return healthy, total


def wait_disconnect_and_reconnect(
    controller: FusionController, timeout_s: float
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    disconnected = None
    reconnect = None
    lines: list[str] = []
    while time.monotonic() < deadline:
        line = controller.channel.read(min(deadline, time.monotonic() + 0.5))
        if line is None:
            continue
        controller._observe(line)
        lines.append(line)
        if disconnected is None and line.startswith("FUSION_DISCONNECTED "):
            disconnected = {"line": line, "utc": utc_now()}
            print("POWER-CYCLE DISCONNECT OBSERVED", flush=True)
            continue
        if (
            disconnected is not None
            and line.startswith("FUSION_BRIDGE_READY ")
            and "name=BSF3C79 " in f"{line} "
        ):
            reconnect = {"line": line, "utc": utc_now()}
            break
    if disconnected is None:
        raise SessionError("no Fusion-PCB BLE disconnect observed")
    if reconnect is None:
        raise SessionError("no BSF3C79 reconnect before timeout")
    return {
        "disconnect": disconnected,
        "reconnect": reconnect,
        "lines": lines,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--bsf", default="BSF3C79")
    parser.add_argument("--port")
    parser.add_argument("--pre-seconds", type=float, default=15.0)
    parser.add_argument("--post-seconds", type=float, default=30.0)
    parser.add_argument("--powercycle-timeout", type=float, default=300.0)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=False)
    predictions = {
        "written_before_cdc_open": True,
        "pre_seconds": args.pre_seconds,
        "post_seconds": args.post_seconds,
        "prediction": [
            "pre-cycle IMU and UWB streams are healthy",
            "whole-board power removal causes a BLE disconnect",
            "B306 reconnects and reports BOOT_RESET class=1 latched",
            "volatile 61=0001,03=000B,1F=0002 recovery passes",
            "post-reconnect IMU restart is clean with no new health fault",
            "the cross-reboot exclusion window uses the host disconnect gap",
        ],
    }
    write_json(args.out_dir / "predictions.json", predictions)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": utc_now(),
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
                summary["initial_stop"] = command(
                    controller, "IMU STOP", "IMU STOP OK "
                ).__dict__
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
            summary["pre_baseline"] = baseline
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
            summary["pre_start"] = start.__dict__
            imu_started = True
            pre_lines = controller.collect(args.pre_seconds)
            pre_final = controller.latest_telemetry
            if pre_final is None:
                raise SessionError("no pre-cycle telemetry")
            summary["pre_final"] = pre_final
            pre_gaps, pre_record_count = imu_sequence_gaps(pre_lines)
            pre_uwb_healthy, pre_uwb_total = healthy_uwb(pre_lines)
            summary["pre_analysis"] = {
                "imu_records": pre_record_count,
                "imu_sequence_gaps": pre_gaps,
                "uwb_healthy": pre_uwb_healthy,
                "uwb_total": pre_uwb_total,
                "anomaly_counter_deltas": counter_deltas(
                    baseline, pre_final, ANOMALY_COUNTERS
                ),
            }
            pre_nonzero = {
                key: value
                for key, value in summary["pre_analysis"][
                    "anomaly_counter_deltas"
                ].items()
                if value != 0
            }
            write_json(args.out_dir / "summary.json", summary)

            print("", flush=True)
            print("#" * 72, flush=True)
            print("### START — FUSION PCB POWER OFF, WAIT 5 s, POWER ON NOW ###", flush=True)
            print("#" * 72, flush=True)
            print("", flush=True)

            cycle = wait_disconnect_and_reconnect(
                controller, args.powercycle_timeout
            )
            summary["power_cycle"] = cycle
            imu_started = False

            post_status = command(controller, "IMU STATUS", "IMU ")
            summary["post_reconnect_status"] = post_status.__dict__
            post_baseline = controller.wait_telemetry()
            summary["post_baseline"] = post_baseline
            post_start = controller.command(
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
            summary["post_start"] = post_start.__dict__
            imu_started = True
            post_lines = controller.collect(args.post_seconds)
            post_final = controller.latest_telemetry
            if post_final is None:
                raise SessionError("no post-cycle telemetry")
            summary["post_final"] = post_final
            repeated_disconnects = [
                line
                for line in post_lines
                if line.startswith("FUSION_DISCONNECTED ")
            ]
            repeated_reconnects = [
                line
                for line in post_lines
                if line.startswith("FUSION_BRIDGE_READY ")
            ]
            post_gaps, post_record_count = imu_sequence_gaps(post_lines)
            post_uwb_healthy, post_uwb_total = healthy_uwb(post_lines)
            stop = command(controller, "IMU STOP", "IMU STOP ")
            imu_started = False
            summary["stop"] = stop.__dict__
            summary["post_analysis"] = {
                "imu_records": post_record_count,
                "imu_sequence_gaps": post_gaps,
                "uwb_healthy": post_uwb_healthy,
                "uwb_total": post_uwb_total,
                "unexpected_disconnects": repeated_disconnects,
                "reconnects_after_unexpected_disconnect": repeated_reconnects,
                "anomaly_counter_deltas": counter_deltas(
                    post_baseline, post_final, ANOMALY_COUNTERS
                ),
            }

            health_ok = (
                "h=1/0/1" in post_status.text
                and "hr=1/0" in post_status.text
                and "verify=P" in post_status.text
                and "61=0001" in post_status.text
                and "03=000B" in post_status.text
                and "1F=0002" in post_status.text
            )
            pre_ok = (
                pre_record_count > 0
                and pre_gaps == 0
                and pre_uwb_total > 0
                and pre_uwb_healthy == pre_uwb_total
                and not pre_nonzero
            )
            post_non_health = {
                key: value
                for key, value in summary["post_analysis"][
                    "anomaly_counter_deltas"
                ].items()
                if value != 0
            }
            post_ok = (
                post_record_count > 0
                and post_gaps == 0
                and post_uwb_total > 0
                and post_uwb_healthy == post_uwb_total
                and not post_non_health
                and not repeated_disconnects
                and stop.text.startswith("IMU STOP OK ")
            )
            summary["acceptance"] = {
                "pre_stream_pass": pre_ok,
                "boot_reset_classification_pass": health_ok,
                "post_stream_pass": post_ok,
                "cross_reboot_window_authority": "HOST_DISCONNECT_GAP",
                "pass": pre_ok and health_ok and post_ok,
            }
            summary["status"] = (
                "PASS" if summary["acceptance"]["pass"] else "FAILED"
            )
            summary["completed_utc"] = utc_now()
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        summary["completed_utc"] = utc_now()
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

    print(f"PHASE C POWER-CYCLE VERDICT: {summary['status']}", flush=True)
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SessionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
