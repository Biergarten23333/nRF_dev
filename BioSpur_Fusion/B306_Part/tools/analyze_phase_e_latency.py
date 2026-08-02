#!/usr/bin/env python3
"""Decompose Phase-E latency into node-to-DK and DK-to-host components."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from fusion_session import parse_fields
from phase_e_vc1_validation import linear_latency


RAW_RE = re.compile(
    r"^[0-9.]+ (?P<mono>[0-9.]+) FUSION_RX (?P<line>.*)$"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    raw_lines = args.raw.read_text(errors="replace").splitlines()
    capture_start = None
    capture_end = None
    for raw in raw_lines:
        if "FUSION_EVENT CAPTURE_START" in raw:
            capture_start = float(raw.split()[1])
        elif "FUSION_EVENT CAPTURE_END" in raw:
            capture_end = float(raw.split()[1])
    if capture_start is None or capture_end is None:
        raise SystemExit("CAPTURE_START/CAPTURE_END markers not found")

    node_to_dk: dict[str, list[tuple[int, float]]] = {
        "uwb": [],
        "imu": [],
    }
    dk_to_host: dict[str, list[tuple[int, float]]] = {
        "uwb": [],
        "imu": [],
    }
    for raw in raw_lines:
        match = RAW_RE.match(raw)
        if match is None:
            continue
        mono = float(match.group("mono"))
        if not (capture_start <= mono <= capture_end):
            continue
        line = match.group("line")
        fields = parse_fields(line)
        if "master_ms" not in fields:
            continue
        master_us = int(fields["master_ms"], 0) * 1000
        if line.startswith("FUSION_UWB ") and "frame_us" in fields:
            kind = "uwb"
            node_us = int(fields["frame_us"], 0)
        elif line.startswith("FUSION_IMU ") and "base_us" in fields:
            sample_deltas = [
                int(encoded.split(",", 1)[0], 0)
                for encoded in fields.get("samples", "").split(";")
                if encoded
            ]
            if not sample_deltas:
                continue
            kind = "imu"
            node_us = (
                int(fields["base_us"], 0) + max(sample_deltas)
            ) & 0xFFFFFFFF
        else:
            continue
        node_to_dk[kind].append((node_us, master_us / 1_000_000.0))
        dk_to_host[kind].append((master_us, mono))

    result = {
        "method": {
            "node_to_dk": (
                "Fit B306 TIMER2 event stamp to DK master_ms. This includes "
                "B306 publication, BLE scheduling, and DK logger admission."
            ),
            "dk_to_host": (
                "Fit DK master_ms to host serial-read monotonic time. This "
                "isolates DK logging, USB CDC, kernel, and host-reader jitter."
            ),
            "warning": (
                "master_ms has 1 ms quantisation; constant offsets remain "
                "unidentifiable and are removed by each fit."
            ),
        },
        "node_to_dk": {
            kind: linear_latency(points)
            for kind, points in node_to_dk.items()
        },
        "dk_to_host": {
            kind: linear_latency(points)
            for kind, points in dk_to_host.items()
        },
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
