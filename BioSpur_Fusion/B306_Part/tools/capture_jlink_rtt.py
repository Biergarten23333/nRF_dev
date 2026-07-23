#!/usr/bin/env python3
"""Capture a fixed-address SEGGER RTT up-buffer through an explicit probe."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pylink


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial-number", type=int, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--speed-khz", type=int, default=4000)
    parser.add_argument("--address", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--duration-s", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    probe = pylink.JLink()
    byte_count = 0
    started = time.monotonic()
    try:
        probe.open(serial_no=args.serial_number)
        probe.set_tif(pylink.enums.JLinkInterfaces.SWD)
        probe.connect(args.device, speed=args.speed_khz, verbose=False)
        probe.rtt_start(block_address=args.address)
        buffer_count = 0
        discovery_deadline = time.monotonic() + 5.0
        while time.monotonic() < discovery_deadline:
            try:
                buffer_count = probe.rtt_get_num_up_buffers()
                break
            except pylink.errors.JLinkRTTException:
                time.sleep(0.05)
        if buffer_count <= args.channel:
            raise RuntimeError(f"RTT up-buffer {args.channel} is unavailable")

        with args.output.open("wb") as output:
            deadline = started + args.duration_s
            while time.monotonic() < deadline:
                data = probe.rtt_read(args.channel, 4096)
                if data:
                    output.write(bytes(data))
                    output.flush()
                    byte_count += len(data)
                else:
                    time.sleep(0.01)
    finally:
        try:
            probe.rtt_stop()
        except Exception:
            pass
        probe.close()

    elapsed = time.monotonic() - started
    print(
        f"RTT_CAPTURE_OK serial={args.serial_number} address=0x{args.address:08x} "
        f"elapsed_s={elapsed:.3f} bytes={byte_count} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
