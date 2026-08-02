#!/usr/bin/env python3
"""Guarded one-board composed-IDLE preflight for the v32 OTA ladder."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from batch_g_control import relay_command_patient
from capacity_ramp import RecordingAssembler, b306_command, collect
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from fusion_session import LineChannel, SessionError, parse_fields, resolve_fusion_port
from pre_ramp_hardening import request_list


MASTER_MARKER = "dk-fusion-imu-relay-v28"
DEFAULT_B306_MARKER = "b306-imu-relay-v31"
TAG_NUMBER = {
    "BSF3C79": 1,
    "BSFC2CC": 2,
    "BSF44AD": 3,
    "BSF6C53": 4,
    "BSF8BC4": 5,
    "BSF1120": 6,
    "BSF31CC": 7,
    "BSFAA61": 8,
    "BSFB165": 9,
    "BSFEC35": 10,
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True, choices=tuple(TAG_NUMBER))
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--witness-s", type=float, default=90.0)
    parser.add_argument("--expected-marker", default=DEFAULT_B306_MARKER)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)

    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started": now(),
        "node": args.node,
        "physical_arrangement": (
            f"{args.node} only powered, approximately 30 cm from DK 683234364; "
            "other nine boards docked/off"
        ),
    }
    channel: LineChannel | None = None
    with (args.out_dir / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = LineChannel(resolve_fusion_port(args.fusion_port), log, "FUSION")
            result["port"] = channel.port
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_before_send"] = decode_guard(channel, 15.0)

            master = wait_master_status(channel)
            result["master_status"] = master
            if f"marker={MASTER_MARKER}" not in master:
                raise SessionError(f"Fusion Master marker mismatch: {master}")

            assembler = RecordingAssembler()
            counters: dict[str, int] = {}
            collect(channel, assembler, 1.0)
            listing = request_list(channel, assembler, counters, (args.node,))
            result["list"] = listing
            aggregate = listing["aggregate"]
            if (
                aggregate.get("count") != "1"
                or aggregate.get("ready") != "1"
                or set(listing["peers"]) != {args.node}
            ):
                raise SessionError(f"single-peer identity gate failed: {listing}")

            ping = b306_command(channel, args.node, "PING", "PONG ")
            result["ping"] = ping
            if (
                f"name={args.node}" not in str(ping["text"])
                or f"fw={args.expected_marker}" not in str(ping["text"])
            ):
                raise SessionError(f"B306 identity/marker mismatch: {ping['text']}")

            imu = b306_command(channel, args.node, "IMU STATUS", "IMU ")
            result["imu_before"] = imu
            if "active=0 " not in f"{imu['text']} ":
                raise SessionError(f"IMU is active before OTA: {imu['text']}")

            tag = TAG_NUMBER[args.node]
            command = (
                f"CFG TAG={tag} SLOT={tag} COUNT=10 PERIOD=10 ACTIVE=9 "
                "EPOCH=5000 BEACON_SYNC=0 BEACON_WIN_N=1 DW_ANCHOR=0 "
                "RUN=0 PMODE=3"
            )
            result["command"] = command
            relayed = relay_command_patient(
                channel, args.node, command, "CFG_OK ", attempts=1, reply_timeout_s=85.0
            )
            result["relay"] = relayed
            ack = str(relayed["reply"]["text"])
            required = (
                f"TAG={tag}", "SLOT=0/1", "PERIOD=25", "ACTIVE=25", "GEN=0",
                "BEACON_SYNC=0", "BEACON_WIN_N=1", "DW_ANCHOR=0",
                "LIVE=1", "RUN=0", "STATE=ARMED",
            )
            missing = [token for token in required if token not in ack]
            if missing:
                raise SessionError(f"normalized composed-IDLE echo missing {missing}: {ack}")

            uwb: Counter[str] = Counter()
            start = time.monotonic()
            deadline = start + args.witness_s
            while time.monotonic() < deadline:
                line = channel.read(min(deadline, time.monotonic() + 0.5))
                if line and line.startswith("FUSION_UWB "):
                    name = parse_fields(line).get("name")
                    if name == args.node:
                        uwb[name] += 1
            elapsed = time.monotonic() - start
            result["uwb_witness"] = {
                "duration_s": elapsed,
                "count": uwb[args.node],
                "rate_hz": uwb[args.node] / elapsed,
                "pass": uwb[args.node] <= 1,
            }
            if uwb[args.node] > 1:
                raise SessionError(f"UWB did not become idle: {uwb[args.node]} records")

            imu_after = b306_command(channel, args.node, "IMU STATUS", "IMU ")
            result["imu_after"] = imu_after
            if "active=0 " not in f"{imu_after['text']} ":
                raise SessionError(f"IMU is active after idle gate: {imu_after['text']}")
            result["status"] = "PASS"
            return 0
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if channel is not None:
                channel.close()
            result["ended"] = now()
            write_json(args.out_dir / "result.json", result)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
