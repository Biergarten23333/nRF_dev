#!/usr/bin/env python3
"""G3 read-only VERSION + IMGSTAT inventory for all ten Fusion nodes."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from relay8_3_t567_provision import tag_read
from relay8_tag_control import wait_master_status


NODES = (
    "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
    "BSF1120", "BSF31CC", "BSFAA61", "BSFEC35", "BSFB165",
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--nodes", nargs="+", choices=NODES, default=list(NODES))
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing existing output: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log_path = args.output.with_suffix(".cdc.log")
    result: dict = {"started": now(), "read_only": True, "rows": []}
    channel = None
    with log_path.open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None), log, "FUSION",
                decoded_queue_records=65536, backlog_red_records=8192,
                raw_backlog_red_bytes=8192, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_before_send"] = decode_guard(channel, 15.0)
            result["master_status"] = wait_master_status(channel)
            for node in args.nodes:
                version = tag_read(channel, node, "VERSION", "VERSION ")
                imgstat = tag_read(channel, node, "IMGSTAT", "IMGSTAT ")
                vf = parse_fields(version["reply"]["text"])
                imf = parse_fields(imgstat["reply"]["text"])
                row = {
                    "node": node,
                    "firmware": vf.get("fw"),
                    "hash": imf.get("hash"),
                    "confirmed": imf.get("confirmed"),
                    "boot": imf.get("boot"),
                    "resetreas": imf.get("resetreas"),
                    "version": version,
                    "imgstat": imgstat,
                }
                result["rows"].append(row)
                print(
                    f"{node} fw={row['firmware']} confirmed={row['confirmed']} "
                    f"boot={row['boot']} resetreas={row['resetreas']}",
                    flush=True,
                )
            result["status"] = "PASS"
            result["ended"] = now()
            result["host_drain"] = channel.health_snapshot()
        except Exception as exc:
            result.update(status="FAIL_STOP", ended=now(), error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            if channel is not None:
                result.setdefault("host_drain", channel.health_snapshot())
                channel.close()
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
