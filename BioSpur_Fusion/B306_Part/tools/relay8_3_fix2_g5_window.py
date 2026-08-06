#!/usr/bin/env python3
"""FIX2 G5 300-second fleet window with the standing S5 gates."""

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
from relay8_3_t567_provision import tag_read
from relay8_tag_control import wait_master_status


SLOTS = {
    "BSF3C79": 1, "BSFC2CC": 2, "BSF44AD": 3, "BSF6C53": 4,
    "BSF8BC4": 5, "BSF1120": 6, "BSF31CC": 7, "BSFAA61": 8,
    "BSFEC35": 9, "BSFB165": 10,
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def u32_delta(a: int, b: int) -> int:
    return (b - a) & 0xFFFFFFFF


def status_snapshot(channel, label: str) -> dict:
    result = {}
    for node in SLOTS:
        errors = []
        for attempt in range(1, 4):
            try:
                reply = tag_read(channel, node, "BEACON_STATUS", "BEACON ")
                fields = parse_fields(reply["reply"]["text"])
                result[node] = {"attempt": attempt, "fields": fields, "transport": reply}
                break
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                if attempt < 3:
                    time.sleep(5.0)
        if node not in result:
            raise RuntimeError(f"{label} {node} BEACON_STATUS exhausted: {errors}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--duration-s", type=float, default=300.0)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing existing output: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result: dict = {
        "started": now(), "duration_requested_s": args.duration_s,
        "imu_off": True, "tag_master_absent": True,
    }
    channel = None
    with args.output.with_suffix(".cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None), log, "FUSION",
                decoded_queue_records=524288, backlog_red_records=65536,
                raw_backlog_red_bytes=65536, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_guard"] = decode_guard(channel, 15.0)
            result["master_status"] = wait_master_status(channel)
            result["status_start"] = status_snapshot(channel, "start")

            rows: dict[str, list[dict[str, str]]] = defaultdict(list)
            disconnects = []
            reset_alarms = []
            malformed = []
            decoder_before = channel.binary_decoder.errors
            started_mono = time.monotonic()
            deadline = started_mono + args.duration_s
            last_progress = started_mono
            while time.monotonic() < deadline:
                line = channel.read(deadline)
                if line is None:
                    continue
                if line.startswith("FUSION_UWB proto=7 name="):
                    fields = parse_fields(line)
                    node = fields.get("name")
                    if node in SLOTS:
                        rows[node].append(fields)
                elif line.startswith("FUSION_DISCONNECTED "):
                    disconnects.append(line)
                elif "TAG_RESET_DETECTED " in line:
                    reset_alarms.append(line)
                elif line.startswith("FUSION_MALFORMED "):
                    malformed.append(line)
                if time.monotonic() - last_progress >= 60.0:
                    elapsed = time.monotonic() - started_mono
                    print(
                        f"G5 progress {elapsed:.0f}/{args.duration_s:.0f}s "
                        f"uwb_min={min((len(rows[n]) for n in SLOTS), default=0)}",
                        flush=True,
                    )
                    last_progress = time.monotonic()
            ended_mono = time.monotonic()
            result["status_end"] = status_snapshot(channel, "end")

            # Fusion-Master resources only; this sends no tag command.
            channel.send("RESOURCES")
            resource_deadline = time.monotonic() + 5.0
            resources = []
            while time.monotonic() < resource_deadline:
                line = channel.read(resource_deadline)
                if line and line.startswith(("FUSION_RESOURCE_", "FUSION_STACK ")):
                    resources.append({"line": line, "fields": parse_fields(line)})

            table = []
            failures = []
            for node, slot in SLOTS.items():
                data = rows[node]
                sweep_delta = 0
                frame_delta = 0
                rate = 0.0
                if len(data) >= 2:
                    sweep_delta = u32_delta(int(data[0]["sweep"], 0), int(data[-1]["sweep"], 0))
                    frame_delta = int(data[-1]["frame_us"], 0) - int(data[0]["frame_us"], 0)
                    if frame_delta > 0:
                        rate = sweep_delta * 1_000_000.0 / frame_delta
                pairs = max(0, len(data) - 1)
                plus1 = sum(
                    ((int(right["sf_mod16"], 0) - int(left["sf_mod16"], 0)) & 0xF) == 1
                    for left, right in zip(data, data[1:])
                )
                plus1_fraction = plus1 / pairs if pairs else 0.0
                sf_invalid = sum(r.get("sf_valid") != "1" for r in data)
                valid_ranges = sum(int(r.get("valid", "0"), 0) != 0 for r in data)
                start_fields = result["status_start"][node]["fields"]
                end_fields = result["status_end"][node]["fields"]
                rxarm_delta = u32_delta(int(start_fields["rxarm"], 0), int(end_fields["rxarm"], 0))
                row = {
                    "node": node, "slot": slot, "records": len(data),
                    "sweep_delta": sweep_delta, "frame_delta_us": frame_delta,
                    "rate_hz": rate, "mod16_plus1_numerator": plus1,
                    "mod16_plus1_denominator": pairs,
                    "mod16_plus1_fraction": plus1_fraction,
                    "sf_invalid": sf_invalid, "valid_range_frames": valid_ranges,
                    "rxarm_delta": rxarm_delta,
                    "beacon_start_lock": start_fields.get("lock"),
                    "beacon_end_lock": end_fields.get("lock"),
                }
                row["pass"] = (
                    len(data) >= 2000 and 7.8 <= rate <= 8.8
                    and plus1_fraction >= 0.999 and sf_invalid == 0
                    and valid_ranges > 0 and rxarm_delta == 0
                    and start_fields.get("lock") == "1" and end_fields.get("lock") == "1"
                )
                if not row["pass"]:
                    failures.append(node)
                table.append(row)
            health = channel.health_snapshot()
            global_pass = (
                not failures and not disconnects and not malformed and not reset_alarms
                and channel.binary_decoder.errors - decoder_before == 0
                and health.get("decoded_queue_drops", 0) == 0
                and health.get("raw_backlog_red_events", 0) == 0
            )
            result.update(
                ended=now(), duration_actual_s=ended_mono-started_mono,
                rows=table, failures=failures, disconnects=disconnects,
                malformed=malformed, false_positive_reset_alarms=reset_alarms,
                decoder_errors=channel.binary_decoder.errors-decoder_before,
                resources=resources, host_drain=health,
                status="PASS" if global_pass else "FAIL",
            )
            for row in table:
                print(
                    f"{row['node']} rate={row['rate_hz']:.6f} "
                    f"mod16={row['mod16_plus1_fraction']:.6f} "
                    f"rxarm={row['rxarm_delta']} pass={row['pass']}", flush=True,
                )
            print(
                f"GLOBAL {result['status']} disconnects={len(disconnects)} "
                f"decoder_errors={result['decoder_errors']} reset_alarms={len(reset_alarms)} "
                f"resources={len(resources)}", flush=True,
            )
            return 0 if global_pass else 2
        finally:
            if channel is not None:
                result.setdefault("host_drain", channel.health_snapshot())
                channel.close()
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
