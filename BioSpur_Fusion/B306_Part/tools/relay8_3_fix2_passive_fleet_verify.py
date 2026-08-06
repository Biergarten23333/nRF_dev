#!/usr/bin/env python3
"""Passive, zero-command FIX2 fleet behavioural verification."""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port


SLOTS = {
    "BSF3C79": 1, "BSFC2CC": 2, "BSF44AD": 3, "BSF6C53": 4,
    "BSF8BC4": 5, "BSF1120": 6, "BSF31CC": 7, "BSFAA61": 8,
    "BSFEC35": 9, "BSFB165": 10,
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--duration-s", type=float, default=30.0)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing existing output: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    result: dict = {
        "started": now(), "duration_s": args.duration_s, "passive": True,
        "tag_commands_sent": 0, "tag_master_cdc_absent": True,
        "tag_master_jlink_absent": True,
    }
    channel = None
    with args.output.with_suffix(".cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None), log, "FUSION",
                decoded_queue_records=262144, backlog_red_records=32768,
                raw_backlog_red_bytes=32768, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_guard"] = decode_guard(channel, 15.0)
            deadline = time.monotonic() + args.duration_s
            while time.monotonic() < deadline:
                line = channel.read(deadline)
                if line is None or not line.startswith("FUSION_UWB proto=7 name="):
                    continue
                fields = parse_fields(line)
                node = fields.get("name")
                if node in SLOTS:
                    rows[node].append(fields)
            table = []
            failures = []
            for node, slot in SLOTS.items():
                data = rows[node]
                row: dict = {"node": node, "assigned_slot": slot, "records": len(data)}
                if len(data) >= 2:
                    sweep_delta = (int(data[-1]["sweep"], 0) - int(data[0]["sweep"], 0)) & 0xFFFFFFFF
                    frame_delta = int(data[-1]["frame_us"], 0) - int(data[0]["frame_us"], 0)
                    rate = sweep_delta * 1_000_000.0 / frame_delta if frame_delta > 0 else 0.0
                else:
                    sweep_delta, frame_delta, rate = 0, 0, 0.0
                sf_valid = sum(r.get("sf_valid") == "1" for r in data)
                valid_ranges = sum(int(r.get("valid", "0"), 0) != 0 for r in data)
                row.update(
                    sweep_delta=sweep_delta, frame_delta_us=frame_delta,
                    rate_hz=rate, sf_valid=sf_valid,
                    sf_valid_fraction=sf_valid / len(data) if data else 0.0,
                    valid_range_frames=valid_ranges,
                    first=data[0] if data else None, last=data[-1] if data else None,
                )
                row["pass"] = (
                    len(data) >= 20 and 7.8 <= rate <= 8.8
                    and row["sf_valid_fraction"] == 1.0 and valid_ranges > 0
                )
                if not row["pass"]:
                    failures.append(node)
                table.append(row)
                print(
                    f"{node} slot={slot} records={len(data)} rate={rate:.6f} "
                    f"sf_valid={row['sf_valid_fraction']:.3f} pass={row['pass']}", flush=True,
                )
            result.update(
                ended=now(), status="PASS" if not failures else "FAIL",
                rows=table, failures=failures, host_drain=channel.health_snapshot(),
            )
            return 0 if not failures else 2
        finally:
            if channel is not None:
                result.setdefault("host_drain", channel.health_snapshot())
                channel.close()
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
