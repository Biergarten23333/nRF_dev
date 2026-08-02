#!/usr/bin/env python3
"""Controlled CFG_STOP reproduction with raw lines and device counters."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from fusion_session import LineChannel, parse_fields, resolve_fusion_port, u32_delta
from pre_ramp_hardening import TelemetryAssembler


def collect(
    channel: LineChannel,
    assembler: TelemetryAssembler,
    duration_s: float,
) -> list[tuple[float, str]]:
    rows: list[tuple[float, str]] = []
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is None:
            continue
        stamp = time.monotonic()
        assembler.observe(line)
        rows.append((stamp, line))
    return rows


def wait_telemetry(
    channel: LineChannel,
    assembler: TelemetryAssembler,
    target: str,
    timeout_s: float = 4.0,
) -> tuple[dict[str, str], list[tuple[float, str]]]:
    previous = assembler.latest.get(target, {}).get("node_ms")
    rows: list[tuple[float, str]] = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        chunk = collect(channel, assembler, min(0.25, deadline - time.monotonic()))
        rows.extend(chunk)
        latest = assembler.latest.get(target)
        if latest is not None and latest.get("node_ms") != previous:
            return dict(latest), rows
    raise RuntimeError(f"no fresh telemetry from {target}")


def deltas(before: dict[str, str], after: dict[str, str]) -> dict[str, int]:
    fields = ("frames", "rise_n", "fall_n", "notify_ok", "drop_err", "ctrl_rx")
    return {
        field: u32_delta(int(before[field], 0), int(after[field], 0))
        for field in fields
        if field in before and field in after
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port")
    parser.add_argument("--target", default="BSF44AD")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=False)
    raw_path = args.output_dir / "raw.log"
    summary_path = args.output_dir / "summary.json"
    assembler = TelemetryAssembler()
    port = resolve_fusion_port(args.port)

    with raw_path.open("w", encoding="utf-8") as raw:
        channel = LineChannel(port, raw, "FUSION")
        try:
            collect(channel, assembler, 1.0)
            channel.send(f"{args.target} TAG RAW CFG_STATUS")
            preflight = collect(channel, assembler, 2.0)
            before, rows = wait_telemetry(channel, assembler, args.target)
            preflight.extend(rows)

            try:
                channel.send(f"{args.target} TAG RAW CFG_STOP")
                stopped = collect(channel, assembler, 5.0)
                after, rows = wait_telemetry(channel, assembler, args.target)
                stopped.extend(rows)
            finally:
                # CFG_STOP remains forbidden operationally. End in the proved
                # radio-quiet state even if the reproduction read fails.
                channel.send(f"{args.target} TAG RAW MODE IDLE")
                cleanup = collect(channel, assembler, 3.0)
                quiet, rows = wait_telemetry(channel, assembler, args.target)
                cleanup.extend(rows)
        finally:
            channel.close()

    target_uwb = [
        (stamp, line, parse_fields(line))
        for stamp, line in stopped
        if line.startswith("FUSION_UWB ")
        and parse_fields(line).get("name") == args.target
    ]
    host_spacing_ms = [
        (target_uwb[index][0] - target_uwb[index - 1][0]) * 1000.0
        for index in range(1, len(target_uwb))
    ]
    node_spacing_ms = [
        u32_delta(
            int(target_uwb[index - 1][2]["node_ms"], 0),
            int(target_uwb[index][2]["node_ms"], 0),
        )
        for index in range(1, len(target_uwb))
        if "node_ms" in target_uwb[index][2]
        and "node_ms" in target_uwb[index - 1][2]
    ]
    sweep_sequence = [
        fields.get("sweep", "?") for _, _, fields in target_uwb
    ]
    replies = [
        line
        for _, line in preflight + stopped + cleanup
        if line.startswith("FUSION_REPLY ")
        and parse_fields(line).get("name") == args.target
    ]

    summary = {
        "start_utc": datetime.now(timezone.utc).isoformat(),
        "port": port,
        "target": args.target,
        "device_counters_before": before,
        "device_counters_after_cfg_stop": after,
        "device_counters_after_mode_idle": quiet,
        "device_counter_deltas_cfg_stop": deltas(before, after),
        "device_counter_deltas_mode_idle": deltas(after, quiet),
        "cfg_stop_uwb_records": len(target_uwb),
        "host_spacing_ms": host_spacing_ms,
        "host_spacing_p50_ms": (
            statistics.median(host_spacing_ms) if host_spacing_ms else None
        ),
        "node_spacing_ms": node_spacing_ms,
        "sweep_sequence": sweep_sequence,
        "raw_uwb_lines": [line for _, line, _ in target_uwb],
        "replies": replies,
        "cleanup": "MODE IDLE",
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
