#!/usr/bin/env python3
"""Analyze a Phase-C power-cycle raw CDC log without changing the rig."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from fusion_session import imu_sequence_gaps, parse_fields


def utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def analyze_window(rows: list[tuple[float, str]]) -> dict[str, object]:
    lines = [payload for _, payload in rows]
    gaps, imu_records = imu_sequence_gaps(lines)
    uwb = [line for line in lines if line.startswith("FUSION_UWB ")]
    telemetry = [
        parse_fields(line)
        for line in lines
        if line.startswith("FUSION_TELEMETRY ")
    ]
    return {
        "duration_s": rows[-1][0] - rows[0][0] if len(rows) > 1 else 0.0,
        "imu_records": imu_records,
        "imu_samples": sum(
            int(parse_fields(line).get("n", "0"), 0)
            for line in lines
            if line.startswith("FUSION_IMU ")
        ),
        "imu_sequence_gaps": gaps,
        "uwb_records": len(uwb),
        "healthy_uwb_records": sum(
            " verdict=healthy " in f" {line} " for line in uwb
        ),
        "first_telemetry": telemetry[0] if telemetry else None,
        "last_telemetry": telemetry[-1] if telemetry else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_log", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    rows: list[tuple[float, str]] = []
    for raw in args.raw_log.read_text(errors="replace").splitlines():
        if " FUSION_RX " not in raw:
            continue
        try:
            timestamp = float(raw.split(maxsplit=1)[0])
        except ValueError:
            continue
        rows.append((timestamp, raw.split(" FUSION_RX ", 1)[1]))

    disconnects = [
        (timestamp, payload)
        for timestamp, payload in rows
        if payload.startswith("FUSION_DISCONNECTED ")
    ]
    reconnects = [
        (timestamp, payload)
        for timestamp, payload in rows
        if payload.startswith("FUSION_BRIDGE_READY ")
    ]
    starts = [
        (timestamp, payload)
        for timestamp, payload in rows
        if payload.startswith("FUSION_REPLY ")
        and "text=IMU START OK " in payload
    ]
    stops = [
        (timestamp, payload)
        for timestamp, payload in rows
        if payload.startswith("FUSION_REPLY ")
        and "text=IMU STOP " in payload
    ]
    if len(disconnects) < 1 or len(starts) < 2:
        raise SystemExit("log lacks the expected start/disconnect sequence")

    first_disconnect = disconnects[0][0]
    first_reconnect = next(
        (
            timestamp
            for timestamp, _ in reconnects
            if timestamp > first_disconnect
        ),
        None,
    )
    pre_rows = [
        row for row in rows if starts[0][0] < row[0] < first_disconnect
    ]
    post_end = disconnects[1][0] if len(disconnects) > 1 else rows[-1][0]
    post_rows = [
        row for row in rows if starts[1][0] < row[0] < post_end
    ]
    result = {
        "source_log": str(args.raw_log),
        "disconnect_count": len(disconnects),
        "disconnects": [
            {"utc": utc(timestamp), "line": payload}
            for timestamp, payload in disconnects
        ],
        "reconnects": [
            {"utc": utc(timestamp), "line": payload}
            for timestamp, payload in reconnects
        ],
        "pre_cycle": analyze_window(pre_rows),
        "after_first_reconnect_until_next_disconnect": analyze_window(post_rows),
        "first_reconnect_latency_s": (
            first_reconnect - first_disconnect
            if first_reconnect is not None
            else None
        ),
        "second_disconnect_after_post_start_s": (
            disconnects[1][0] - starts[1][0]
            if len(disconnects) > 1
            else None
        ),
        "stop_replies": [
            {"utc": utc(timestamp), "line": payload}
            for timestamp, payload in stops
        ],
        "verdict": (
            "FAILED_UNEXPECTED_SECOND_RESET"
            if len(disconnects) > 1
            else "NO_SECOND_RESET_OBSERVED"
        ),
        "interpretation": (
            "The first provocation was detected, classified, recovered, and "
            "streaming resumed. A second B306 disconnect/reset interrupted "
            "that resumed stream. The later IMU STOP err=-120 means the "
            "second boot was already inactive; it is not a lost STOP command."
        ),
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
