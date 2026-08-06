#!/usr/bin/env python3
"""Read-only B306 v34 S2 pre-OTA PING and confirmation inventory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from fusion_session import SessionError, resolve_fusion_port


NODES = (
    "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4", "BSF1120",
    "BSF31CC", "BSFAA61", "BSFEC35", "BSFB165",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--expected-marker", default="b306-imu-relay-v33")
    parser.add_argument("--master-marker", default="dk-fusion-imu-relay-v29")
    parser.add_argument(
        "--marker-exception", action="append", default=[], metavar="NODE=MARKER",
        help="expected per-node marker override for a recorded quarantine",
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    result: dict[str, object] = {"status": "IN_PROGRESS", "nodes": {}}
    channel = None
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
            if f"marker={args.master_marker}" not in master:
                raise SessionError(f"master marker mismatch: {master}")
            marker_exceptions = dict(item.split("=", 1) for item in args.marker_exception)
            for node in NODES:
                ping = b306_command(channel, node, "PING", "PONG ")
                img = b306_command(
                    channel, node, "BOOT CONFIRM STATUS", "BOOT CONFIRM STATUS "
                )
                row = {"ping": ping, "imgstat": img}
                result["nodes"][node] = row
                ptext = str(ping["text"])
                itext = str(img["text"])
                expected_marker = marker_exceptions.get(node, args.expected_marker)
                if f"name={node}" not in ptext or f"fw={expected_marker}" not in ptext:
                    raise SessionError(f"{node} identity/version mismatch: {ptext}")
                if "confirmed=1" not in itext:
                    raise SessionError(f"{node} not confirmed before OTA: {itext}")
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
