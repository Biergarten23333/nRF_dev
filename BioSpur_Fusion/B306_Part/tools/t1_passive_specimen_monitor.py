#!/usr/bin/env python3
"""Bounded, receive-only monitor for the three retained T1 specimens."""

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from fusion_session import parse_fields, resolve_fusion_port


NODES = ("BSF3C79", "BSF44AD", "BSFAA61")


def wall_time() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--duration-s", type=float, default=900.0)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    result = {
        "status": "RUNNING",
        "receive_only": True,
        "duration_requested_s": args.duration_s,
        "started_wall": wall_time(),
        "nodes": {node: {"counts": {}, "events": []} for node in NODES},
    }
    counts = {node: Counter() for node in NODES}
    with (args.output_dir / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        channel = ThreadedLineChannel(
            resolve_fusion_port(None), log, "FUSION",
            decoded_queue_records=262144, backlog_red_records=32768,
            raw_backlog_red_bytes=32768, stall_red_s=2,
        )
        started = None
        try:
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            started = time.monotonic()
            deadline = started + args.duration_s
            while time.monotonic() < deadline:
                line = channel.read(deadline)
                if not line:
                    continue
                fields = parse_fields(line)
                node = fields.get("name")
                if node not in NODES:
                    continue
                kind = line.split(" ", 1)[0]
                counts[node][kind] += 1
                if kind in {
                    "FUSION_CONNECTED", "FUSION_DISCONNECTED",
                    "FUSION_BRIDGE_READY", "FUSION_QOS",
                }:
                    result["nodes"][node]["events"].append({
                        "elapsed_s": time.monotonic() - started,
                        "wall": wall_time(), "line": line,
                    })
            result["status"] = "COMPLETE"
        except KeyboardInterrupt:
            result["status"] = "INTERRUPTED"
            result["stop_reason"] = "KeyboardInterrupt"
        except BaseException as exc:
            result["status"] = "FAILED"
            result["stop_reason"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            # The closeout writer runs on every exit path, so a status left at
            # RUNNING would make a stopped capture look live to any later
            # reader. Nothing below may leave RUNNING in the file.
            if result["status"] == "RUNNING":
                result["status"] = "INTERRUPTED"
                result.setdefault("stop_reason", "closeout reached without a terminal status")
            if started is not None:
                result["duration_actual_s"] = time.monotonic() - started
            result["ended_wall"] = wall_time()
            result["health"] = channel.health_snapshot()
            channel.close()
            for node in NODES:
                result["nodes"][node]["counts"] = dict(sorted(counts[node].items()))
            (args.output_dir / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )


if __name__ == "__main__":
    main()
