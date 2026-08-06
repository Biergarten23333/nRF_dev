#!/usr/bin/env python3
"""One-shot composed-idle preparation for the v33 OTA fleet."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_control import relay_command_patient
from batch_g_overnight_core import composed_idle_cfg
from capacity_ramp import RecordingAssembler, collect
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from fusion_session import SessionError, parse_fields, resolve_fusion_port
from pre_ramp_hardening import request_list

NODES = ("BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
         "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35")
TAG = {node: index + 1 for index, node in enumerate(NODES)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--witness-s", type=float, default=10.0)
    parser.add_argument("--skip", action="append", default=[])
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {"status": "IN_PROGRESS", "commands": {}}
    channel = None
    with (args.out_dir / "fusion_cdc.log").open("x", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(resolve_fusion_port(args.fusion_port), log, "FUSION")
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_before_send"] = decode_guard(channel, 15.0)
            master = wait_master_status(channel)
            result["master"] = master
            if "marker=dk-fusion-imu-relay-v29" not in master:
                raise SessionError(f"wrong master: {master}")
            assembler = RecordingAssembler()
            collect(channel, assembler, 1.0)
            listing = request_list(channel, assembler, {}, NODES)
            result["list"] = listing
            if set(listing["peers"]) != set(NODES):
                raise SessionError(f"identity set mismatch: {listing}")
            for node in NODES:
                if node in args.skip:
                    result["commands"][node] = {"delivery": "SKIPPED_ALREADY_ACKED"}
                    continue
                command = composed_idle_cfg(TAG[node], TAG[node], 11)
                reply = relay_command_patient(channel, node, command, "CFG_OK ",
                                              attempts=1, reply_timeout_s=30.0)
                text = str(reply["reply"]["text"])
                required = ("BEACON_SYNC=0", "RUN=0", "STATE=ARMED", "LIVE=1")
                if any(token not in text for token in required):
                    raise SessionError(f"bad idle echo for {node}: {text}")
                result["commands"][node] = {"command": command, "reply": reply}
            channel.discard_pending("phase3_idle_witness")
            counts = {node: 0 for node in NODES}
            deadline = time.monotonic() + args.witness_s
            while time.monotonic() < deadline:
                line = channel.read(min(deadline, time.monotonic() + 0.5))
                if line and line.startswith("FUSION_UWB "):
                    node = parse_fields(line).get("name")
                    if node in counts:
                        counts[node] += 1
            result["witness"] = {"seconds": args.witness_s, "uwb": counts}
            if any(counts.values()):
                raise SessionError(f"UWB remained active: {counts}")
            result["status"] = "PASS"
            return 0
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if channel is not None:
                result["host"] = channel.health_snapshot()
                channel.close()
            (args.out_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, SessionError, ValueError) as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(2)
