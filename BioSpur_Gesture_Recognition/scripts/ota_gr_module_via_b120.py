#!/usr/bin/env python3
import argparse
import sys
import time

import serial


DEFAULT_PORT = "/dev/serial/by-id/usb-BioSpur-GR_BioSpur-GR_51D4A5716A4C5551-if00"


def write_line(ser, line):
    ser.write((line + "\n").encode("ascii"))
    ser.flush()


def read_lines_until(ser, timeout_s, stop_predicate):
    deadline = time.time() + timeout_s
    lines = []
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").rstrip()
        print(line, flush=True)
        lines.append(line)
        if stop_predicate(line):
            return lines, True
    return lines, False


def main():
    parser = argparse.ArgumentParser(
        description="Trigger GR module BLE OTA through the BioSpur-GR B120 USB CDC bridge."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=0.2, write_timeout=2.0) as ser:
        time.sleep(0.5)
        ser.reset_input_buffer()

        write_line(ser, "status")
        read_lines_until(ser, 2.0, lambda line: "status ok" in line)

        write_line(ser, "ota")

        def done(line):
            failure_tokens = (
                "OTA blocked",
                "OTA erase failed",
                "OTA upload failed",
                "OTA reset failed",
                "connect failed",
                "DFU SMP service not found",
            )
            if any(token in line for token in failure_tokens):
                raise RuntimeError(line)
            return "OTA sequence complete" in line

        try:
            _, ok = read_lines_until(ser, args.timeout, done)
        except RuntimeError as exc:
            print(f"OTA failed: {exc}", file=sys.stderr)
            return 1

        if not ok:
            print("OTA timed out", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
