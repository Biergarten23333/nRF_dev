#!/usr/bin/env python3
"""Print per-node rolling UWB/IMU rates from an active Fusion CDC log."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from analyze_relay8_1_overnight import parse_imu
from batch_g_overnight import NODES
from fusion_session import parse_fields
from delivered_rate import delivered_rate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--seconds", type=float, default=60.0)
    args = parser.parse_args()
    rows: list[tuple[float, str]] = []
    with args.log.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.split(" ", 3)
            if len(parts) < 4:
                continue
            try:
                host = float(parts[1])
            except ValueError:
                continue
            rows.append((host, line))
    end = max((host for host, _line in rows), default=0.0)
    start = end - args.seconds
    uwb: dict[str, list[int]] = defaultdict(list)
    imu: dict[str, list[int]] = defaultdict(list)
    for host, line in rows:
        if host < start:
            continue
        fields = parse_fields(line)
        node = fields.get("name")
        if node not in NODES:
            continue
        if "FUSION_UWB " in line and "frame_us" in fields:
            uwb[node].append(int(fields["frame_us"], 0))
        elif "FUSION_IMU " in line:
            base = int(fields.get("base_us", "0"), 0)
            for offset, _axes in parse_imu(fields):
                imu[node].append(base + offset)
    print(f"window={start:.3f}..{end:.3f} seconds={args.seconds:g}")
    print("node uwb_hz imu_hz uwb_records imu_samples")
    for node in NODES:
        u = uwb[node]
        i = imu[node]
        ur = delivered_rate(len(u), args.seconds, u, stream="uwb", max_rate_hz=1000/120)
        ir = delivered_rate(len(i), args.seconds, i, stream="imu", max_rate_hz=200)
        flags = ",".join(ur.flags + ir.flags) or "-"
        print(f"{node} {ur.delivered_rate_hz:.4f} {ir.delivered_rate_hz:.3f} {len(u)} {len(i)} {flags}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
