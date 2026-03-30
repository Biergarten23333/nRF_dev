#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time

import serial


VALID_ROLES = {"master", "matrix", "responder"}


def read_line(ser: serial.Serial, timeout_s: float) -> str | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace").strip()
        if line:
            return line
    return None


def wait_for_ready(ser: serial.Serial, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        line = read_line(ser, min(0.6, max(0.1, deadline - time.time())))
        if line is None:
            continue
        print(f"<< {line}")
        if "UART ROLE SWITCH READY" in line.upper():
            return
    raise TimeoutError("UART role switch ready banner not observed")


def send_cmd_expect(ser: serial.Serial, cmd: str, timeout_s: float,
                    expect_prefixes: tuple[str, ...]) -> str:
    ser.write((cmd + "\r\n").encode("ascii"))
    ser.flush()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        line = read_line(ser, min(0.6, max(0.1, deadline - time.time())))
        if line is None:
            continue
        print(f"<< {line}")
        for p in expect_prefixes:
            if line.upper().startswith(p):
                return line
    raise TimeoutError(f"timeout waiting response for '{cmd}', expected {expect_prefixes}")


def expect_ok(resp: str, step: str) -> None:
    up = resp.upper()
    if up.startswith("OK"):
        return
    if up.startswith("ERR:"):
        raise RuntimeError(f"{step} failed: {resp}")
    raise RuntimeError(f"{step} unexpected response: {resp}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Switch unified-anchor role/anchor over UART commands.")
    p.add_argument("--port", required=True)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--role", choices=sorted(VALID_ROLES))
    p.add_argument("--anchor-id", help="A..H")
    p.add_argument("--save", action="store_true")
    p.add_argument("--reboot", action="store_true")
    p.add_argument("--timeout", type=float, default=4.0)
    return p.parse_args()


def validate_anchor_id(anchor_id: str) -> str:
    if anchor_id is None:
        return ""
    v = anchor_id.strip().upper()
    if len(v) != 1 or v < "A" or v > "H":
        raise ValueError("anchor-id must be A..H")
    return v


def main() -> int:
    args = parse_args()
    try:
        anchor_id = validate_anchor_id(args.anchor_id) if args.anchor_id else ""
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    try:
        with serial.Serial(args.port, args.baud, timeout=0.25) as ser:
            time.sleep(0.15)
            ser.reset_input_buffer()
            wait_for_ready(ser, max(args.timeout, 10.0))

            print(">> STATUS")
            status = send_cmd_expect(ser, "STATUS", args.timeout,
                                     ("ANCHOR: UNIFIED;", "ERR:"))
            if status.upper().startswith("ERR:"):
                raise RuntimeError(f"STATUS failed: {status}")

            if args.role:
                print(f">> ROLE SET {args.role}")
                resp = send_cmd_expect(ser, f"ROLE SET {args.role}", args.timeout, ("OK", "ERR:"))
                expect_ok(resp, "ROLE SET")

            if anchor_id:
                print(f">> ANCHOR SET {anchor_id}")
                resp = send_cmd_expect(ser, f"ANCHOR SET {anchor_id}", args.timeout, ("OK", "ERR:"))
                expect_ok(resp, "ANCHOR SET")

            if args.save:
                print(">> CONFIG SAVE")
                resp = send_cmd_expect(ser, "CONFIG SAVE", max(args.timeout, 8.0), ("OK", "ERR:"))
                expect_ok(resp, "CONFIG SAVE")

            if args.reboot:
                print(">> REBOOT")
                resp = send_cmd_expect(ser, "REBOOT", args.timeout, ("OK", "ERR:"))
                expect_ok(resp, "REBOOT")
                time.sleep(0.7)

            print(">> STATUS")
            status2 = send_cmd_expect(ser, "STATUS", max(args.timeout, 8.0),
                                      ("ANCHOR: UNIFIED;", "ERR:"))
            if status2.upper().startswith("ERR:"):
                raise RuntimeError(f"final STATUS failed: {status2}")

        return 0
    except (TimeoutError, RuntimeError, serial.SerialException) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
