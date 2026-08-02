#!/usr/bin/env python3
"""Put the remaining-nine tag radios into composed idle exactly once each."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_control import relay_command_patient
from capacity_ramp import RecordingAssembler, collect
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from fusion_session import SessionError, parse_fields, resolve_fusion_port
from pre_ramp_hardening import request_list


MASTER_MARKER = "dk-fusion-imu-relay-v28"
ORDER = (
    "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF1120",
    "BSF31CC", "BSFAA61", "BSFEC35", "BSFB165",
)
TAG_CFG = {
    "BSF3C79": (1, 1, 10),
    "BSFC2CC": (2, 2, 10),
    "BSF44AD": (3, 3, 10),
    "BSF6C53": (4, 4, 10),
    "BSF1120": (6, 6, 11),
    "BSF31CC": (7, 7, 11),
    "BSFAA61": (8, 8, 11),
    "BSFEC35": (10, 10, 11),
    "BSFB165": (9, 9, 11),
}


def idle_cfg(node: str) -> str:
    tag, slot, count = TAG_CFG[node]
    return (
        f"CFG TAG={tag} SLOT={slot} COUNT={count} PERIOD=10 ACTIVE=9 EPOCH=5000 "
        "BEACON_SYNC=0 BEACON_WIN_N=1 DW_ANCHOR=0 RUN=0 PMODE=3"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--settle-s", type=float, default=5.0)
    parser.add_argument("--witness-s", type=float, default=20.0)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {"status": "IN_PROGRESS", "commands": {}}
    channel: ThreadedLineChannel | None = None
    with (args.out_dir / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(args.fusion_port), log, "FUSION",
                decoded_queue_records=65536, backlog_red_records=8192,
                raw_backlog_red_bytes=8192, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["port"] = channel.port
            result["decode_before_send"] = decode_guard(channel, 15.0)
            master = wait_master_status(channel)
            result["master_status"] = master
            if f"marker={MASTER_MARKER}" not in master:
                raise SessionError(f"master marker mismatch: {master}")

            assembler = RecordingAssembler()
            collect(channel, assembler, 1.0)
            listing = request_list(channel, assembler, {}, ORDER)
            result["list"] = listing
            if set(listing["peers"]) != set(ORDER):
                raise SessionError(f"remaining-nine identity set mismatch: {sorted(listing['peers'])}")

            for node in ORDER:
                command = idle_cfg(node)
                try:
                    reply = relay_command_patient(
                        channel, node, command, "CFG_OK ", attempts=1,
                        reply_timeout_s=20.0,
                    )
                    text = str(reply["reply"]["text"])
                    expected = TAG_CFG[node]
                    required = (
                        f"TAG={expected[0]}", f"SLOT={expected[1]}/{expected[2]}",
                        "BEACON_SYNC=0", "DW_ANCHOR=0", "LIVE=1",
                        "RUN=0", "STATE=IDLE",
                    )
                    if any(token not in text for token in required):
                        raise SessionError(f"incomplete idle echo for {node}: {text}")
                    result["commands"][node] = {
                        "command": command, "delivery": "ACK", "reply": reply,
                    }
                except SessionError as exc:
                    # The command was sent exactly once. Do not retransmit a
                    # possibly executed state change; the field witness below
                    # is the authoritative behavioral gate.
                    result["commands"][node] = {
                        "command": command,
                        "delivery": "ACK_NOT_OBSERVED_NO_RETRY",
                        "error": str(exc),
                    }

            settle_deadline = time.monotonic() + args.settle_s
            while time.monotonic() < settle_deadline:
                channel.read(min(settle_deadline, time.monotonic() + 0.5))
            boundary = channel.discard_pending("batch_idle_witness_start")
            counts = {node: 0 for node in ORDER}
            start = time.monotonic()
            deadline = start + args.witness_s
            while time.monotonic() < deadline:
                line = channel.read(min(deadline, time.monotonic() + 0.5))
                if line and line.startswith("FUSION_UWB "):
                    node = parse_fields(line).get("name")
                    if node in counts:
                        counts[node] += 1
            result["witness"] = {
                "boundary": boundary,
                "duration_s": time.monotonic() - start,
                "uwb_records": counts,
                "pass": all(value == 0 for value in counts.values()),
            }
            if not result["witness"]["pass"]:
                raise SessionError(f"composed-idle witness failed: {counts}")
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
