#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import time

import serial


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset a board with nrfjprog, then read its serial port."
    )
    parser.add_argument("snr", help="SEGGER serial number")
    parser.add_argument("port", help="Serial port path, preferably /dev/serial/by-id/...")
    parser.add_argument(
        "--duration",
        type=float,
        default=8.0,
        help="Read duration in seconds after reset",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Serial baud rate",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=2.0,
        help="Seconds to wait for the serial device to reappear after reset",
    )
    args = parser.parse_args()

    subprocess.run(
        ["nrfjprog", "--reset", "-f", "NRF52", "--snr", args.snr],
        check=False,
    )

    deadline = time.time() + args.settle
    while time.time() < deadline:
        if os.path.exists(args.port):
            break
        time.sleep(0.1)

    end = time.time() + args.duration
    while time.time() < end:
        try:
            with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
                while time.time() < end:
                    data = ser.readline()
                    if not data:
                        continue
                    text = data.decode("utf-8", errors="replace").rstrip()
                    if text:
                        print(text)
                        sys.stdout.flush()
                break
        except (serial.SerialException, OSError):
            time.sleep(0.2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
