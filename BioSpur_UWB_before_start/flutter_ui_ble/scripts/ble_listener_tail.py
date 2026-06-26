#!/usr/bin/env python3
"""Tail the BioSpur BLE Listener CDC stream for Flutter."""

from __future__ import annotations

import argparse
import glob
import sys
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:  # pragma: no cover - runtime dependency check
    raise SystemExit(f"pyserial is required: {exc}") from exc


DEFAULT_GLOB = "/dev/serial/by-id/*BioSpur_BLE_Listener*"


def find_listener_port() -> str:
    matches = sorted(glob.glob(DEFAULT_GLOB))
    if matches:
        return matches[0]

    for port in list_ports.comports():
        if port.vid == 0x2FE3 and port.pid == 0x10F3:
            return port.device
        text = " ".join(
            str(x or "")
            for x in [
                port.device,
                port.serial_number,
                port.manufacturer,
                port.product,
                port.description,
            ]
        )
        if "BioSpur_BLE_Listener" in text:
            return port.device

    raise RuntimeError("BioSpur_BLE_Listener CDC port not found")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="auto")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    port = find_listener_port() if args.port in ("", "auto") else args.port
    if not Path(port).exists():
        raise RuntimeError(f"serial port does not exist: {port}")

    print(f"[tail] port={port} baud={args.baud}", flush=True)
    with serial.Serial(port, args.baud, timeout=0.2, dsrdtr=False, rtscts=False) as ser:
        ser.dtr = True
        ser.rts = True
        while True:
            raw = ser.readline()
            if not raw:
                time.sleep(0.02)
                continue
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line:
                try:
                    print(line, flush=True)
                except BrokenPipeError:
                    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
    except KeyboardInterrupt:
        raise SystemExit(0)
