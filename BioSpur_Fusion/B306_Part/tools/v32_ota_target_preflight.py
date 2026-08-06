#!/usr/bin/env python3
"""Immediate, read-mostly identity/idle gate before one v32 OTA transaction."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from fusion_session import SessionError, parse_fields, resolve_fusion_port


MASTER_MARKER = "dk-fusion-imu-relay-v28"
NODES = (
    "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4", "BSF1120",
    "BSF31CC", "BSFAA61", "BSFEC35", "BSFB165",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True, choices=NODES)
    parser.add_argument("--expected-marker", required=True)
    parser.add_argument("--expected-master-marker", default=MASTER_MARKER)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--observe-s", type=float, default=5.0)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {"status": "IN_PROGRESS", "node": args.node}
    channel: ThreadedLineChannel | None = None
    with (args.out_dir / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(args.fusion_port), log, "FUSION",
                decoded_queue_records=32768, backlog_red_records=8192,
                raw_backlog_red_bytes=8192, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["port"] = channel.port
            result["decode_before_send"] = decode_guard(channel, 15.0)
            master = wait_master_status(channel)
            result["master_status"] = master
            if f"marker={args.expected_master_marker}" not in master:
                raise SessionError(f"master marker mismatch: {master}")

            ping = b306_command(channel, args.node, "PING", "PONG ")
            result["ping"] = ping
            text = str(ping["text"])
            if f"name={args.node}" not in text or f"fw={args.expected_marker}" not in text:
                raise SessionError(f"target identity mismatch: {text}")

            boundary = channel.discard_pending(f"{args.node}_pre_ota_idle_start")
            uwb = 0
            imu = 0
            latest_imu_active = None
            deadline = time.monotonic() + args.observe_s
            while time.monotonic() < deadline:
                line = channel.read(min(deadline, time.monotonic() + 0.5))
                if line is None:
                    continue
                fields = parse_fields(line)
                if fields.get("name") != args.node:
                    continue
                if line.startswith("FUSION_UWB "):
                    uwb += 1
                elif line.startswith("FUSION_IMU "):
                    imu += 1
                elif line.startswith("FUSION_TELEMETRY "):
                    latest_imu_active = fields.get("imu_active")
            result["idle"] = {
                "boundary": boundary, "duration_s": args.observe_s,
                "uwb_records": uwb, "imu_records": imu,
                "latest_imu_active": latest_imu_active,
            }
            if uwb != 0 or imu != 0 or latest_imu_active != "0":
                raise SessionError(f"target not idle: {result['idle']}")
            result["status"] = "PASS"
            return 0
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if channel is not None:
                result["host_drain"] = channel.health_snapshot()
                channel.close()
            (args.out_dir / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
