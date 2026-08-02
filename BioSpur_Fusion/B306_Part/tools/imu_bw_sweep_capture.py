#!/usr/bin/env python3
"""Capture one volatile JY61P bandwidth candidate over Fusion Master CDC."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fusion_session import (
    FusionController,
    LineChannel,
    SessionError,
    resolve_fusion_port,
)


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def command(controller: FusionController, text: str, prefix: str):
    return controller.command(
        text,
        lambda reply: reply.startswith(prefix),
        allow_resend_after_tx=False,
    )


def run(args) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=False)
    summary: dict = {
        "status": "IN_PROGRESS",
        "protocol": "B3 continuous multi-axis motion",
        "bandwidth_request": f"{args.bandwidth:04X}",
        "duration_s": args.duration_s,
    }
    channel = None
    controller = None
    imu_started = False
    bandwidth_changed = False
    gyrocal_changed = False
    try:
        with (args.out_dir / "raw.log").open("a", buffering=1) as raw:
            port = resolve_fusion_port(args.port)
            summary["fusion_port"] = port
            channel = LineChannel(port, raw, "FUSION")
            controller = FusionController(channel, args.bsf, 8.0, 3)
            summary["bridge"] = controller.ensure_bridge()
            status = command(controller, "IMU STATUS", "IMU ")
            summary["status_before"] = status.__dict__
            if "active=0 " not in f" {status.text} ":
                raise SessionError(f"IMU must be stopped: {status.text}")
            summary["bandwidth_before"] = command(
                controller, "IMU REG=1F", "IMU REG OK "
            ).__dict__
            applied = command(
                controller, f"IMU BW={args.bandwidth:04X}", "IMU BW OK "
            )
            summary["bandwidth_applied"] = applied.__dict__
            required = (
                f"request={args.bandwidth:04X}",
                f"readback={args.bandwidth:04X}",
                "volatile=1",
                "saved=0",
            )
            if any(token not in applied.text for token in required):
                raise SessionError(f"BW readback mismatch: {applied.text}")
            bandwidth_changed = True
            summary["bandwidth_verify"] = command(
                controller, "IMU REG=1F", "IMU REG OK "
            ).__dict__
            summary["rate"] = command(
                controller, "IMU RATE=200", "IMU RATE OK "
            ).__dict__
            summary["batch"] = command(
                controller, "IMU BATCH=2", "IMU BATCH OK "
            ).__dict__
            baseline = controller.wait_telemetry()
            summary["baseline"] = baseline
            started = controller.command(
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
            summary["start"] = started.__dict__
            imu_started = True
            gyrocal_changed = True
            print(
                f"B3_CAPTURE_ACTIVE bw={args.bandwidth:04X} "
                f"duration={args.duration_s:.1f}s",
                flush=True,
            )
            controller.collect(args.duration_s)
            if controller.latest_telemetry is None:
                raise SessionError("capture lacked final telemetry")
            summary["final"] = controller.latest_telemetry
            summary["stop"] = command(
                controller, "IMU STOP", "IMU STOP OK "
            ).__dict__
            imu_started = False
            restored_bw = command(
                controller, f"IMU BW={args.restore_bandwidth:04X}", "IMU BW OK "
            )
            summary["bandwidth_restore"] = restored_bw.__dict__
            if f"readback={args.restore_bandwidth:04X}" not in restored_bw.text:
                raise SessionError(f"BW restore mismatch: {restored_bw.text}")
            bandwidth_changed = False
            restored_61 = command(
                controller, "IMU REG=61 VAL=0000", "IMU REG OK "
            )
            summary["gyrocal_restore"] = restored_61.__dict__
            if "readback=0000" not in restored_61.text:
                raise SessionError(f"0x61 restore mismatch: {restored_61.text}")
            gyrocal_changed = False
            summary["final_bw_verify"] = command(
                controller, "IMU REG=1F", "IMU REG OK "
            ).__dict__
            summary["final_61_verify"] = command(
                controller, "IMU REG=61", "IMU REG OK "
            ).__dict__
            summary["status"] = "COMPLETE"
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        raise
    finally:
        if controller is not None and imu_started:
            try:
                summary["rollback_stop"] = command(
                    controller, "IMU STOP", "IMU STOP "
                ).__dict__
            except Exception as exc:
                summary["rollback_stop_error"] = str(exc)
        if controller is not None and bandwidth_changed:
            try:
                summary["rollback_bw"] = command(
                    controller,
                    f"IMU BW={args.restore_bandwidth:04X}",
                    "IMU BW ",
                ).__dict__
            except Exception as exc:
                summary["rollback_bw_error"] = str(exc)
        if controller is not None and gyrocal_changed:
            try:
                summary["rollback_61"] = command(
                    controller, "IMU REG=61 VAL=0000", "IMU REG "
                ).__dict__
            except Exception as exc:
                summary["rollback_61_error"] = str(exc)
        if channel is not None:
            channel.close()
        write_json(args.out_dir / "summary.json", summary)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--bandwidth", required=True, type=lambda text: int(text, 16))
    parser.add_argument("--restore-bandwidth", type=lambda text: int(text, 16), default=4)
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--bsf", default="BSF3C79")
    parser.add_argument("--port")
    args = parser.parse_args()
    try:
        return run(args)
    except SessionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
