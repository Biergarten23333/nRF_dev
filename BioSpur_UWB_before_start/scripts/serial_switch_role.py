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


def wait_for_ready(ser: serial.Serial, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        line = read_line(ser, min(0.6, max(0.1, deadline - time.time())))
        if line is None:
            continue
        print(f"<< {line}")
        if "UART ROLE SWITCH READY" in line.upper():
            return True
    return False


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


def send_reboot_best_effort(ser: serial.Serial, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ser.write(b"RB\r\n")
        ser.flush()
        t0 = time.time()
        while time.time() - t0 < 0.8:
            line = read_line(ser, 0.2)
            if line is None:
                continue
            print(f"<< {line}")
            up = line.upper()
            if up.startswith("OK"):
                return True
            if "*** BOOTING NRF CONNECT SDK" in up:
                return True
        time.sleep(0.15)
    return False


def query_status_or_role(ser: serial.Serial, timeout_s: float) -> str:
    for _ in range(3):
        print(">> STATUS")
        try:
            line = send_cmd_expect(ser, "STATUS", timeout_s, ("ANCHOR: UNIFIED;", "ERR:"))
            if line.upper().startswith("ERR:BAD_CMD"):
                continue
            return line
        except TimeoutError:
            pass

    print("[warn] STATUS unavailable, fallback to ROLE?")
    for _ in range(5):
        print(">> ROLE?")
        try:
            line = send_cmd_expect(ser, "ROLE?", timeout_s, ("ROLE:", "ERR:"))
            if line.upper().startswith("ERR:BAD_CMD"):
                continue
            return line
        except TimeoutError:
            continue
    raise TimeoutError("status probe failed after STATUS/ROLE? retries")


def expect_ok(resp: str, step: str) -> None:
    up = resp.upper()
    if up.startswith("OK"):
        return
    if up.startswith("ERR:"):
        raise RuntimeError(f"{step} failed: {resp}")
    raise RuntimeError(f"{step} unexpected response: {resp}")


def run_mutation_cmd(ser: serial.Serial, cmd: str, timeout_s: float, step: str) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        ser.write((cmd + "\r\n").encode("ascii"))
        ser.flush()
        t0 = time.time()
        while time.time() - t0 < 0.9:
            line = read_line(ser, 0.12)
            if line is None:
                continue
            print(f"<< {line}")
            up = line.upper()
            if up.startswith("ERR:BAD_CMD"):
                continue
            if up.startswith("OK") or up.startswith("ERR:"):
                expect_ok(line, step)
                return
        time.sleep(0.2)
    raise TimeoutError(f"{step} timeout waiting OK/ERR")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Switch unified-anchor role/anchor over UART commands.")
    p.add_argument("--port", required=True)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--role", choices=sorted(VALID_ROLES))
    p.add_argument("--anchor-id", help="A..H")
    p.add_argument("--save", action="store_true")
    p.add_argument("--reboot", action="store_true")
    p.add_argument("--boot-window-reboot", action="store_true",
                   help="Send REBOOT first, then apply changes during boot window.")
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
            boot_window_mode = bool(args.boot_window_reboot)
            if args.boot_window_reboot:
                print(">> REBOOT (boot-window mode)")
                if not send_reboot_best_effort(ser, max(args.timeout, 8.0)):
                    print("[warn] reboot ACK not observed; continuing")
                time.sleep(0.5)
            if not wait_for_ready(ser, max(args.timeout, 10.0)):
                print("[warn] ready banner not observed; continue with command probe")

            if not boot_window_mode:
                status = query_status_or_role(ser, args.timeout)
                if status.upper().startswith("ERR:"):
                    raise RuntimeError(f"STATUS failed: {status}")

            if args.role:
                role_cmd = f"ROLE SET {args.role}"
                if boot_window_mode:
                    role_cmd = {"master": "M", "matrix": "X", "responder": "P"}[args.role]
                print(f">> {role_cmd}")
                run_mutation_cmd(
                    ser,
                    role_cmd,
                    max(args.timeout, 10.0) if boot_window_mode else args.timeout,
                    "ROLE SET",
                )

            if anchor_id:
                anchor_cmd = f"ANCHOR SET {anchor_id}"
                if boot_window_mode:
                    anchor_cmd = f"ID {anchor_id}"
                print(f">> {anchor_cmd}")
                run_mutation_cmd(
                    ser,
                    anchor_cmd,
                    max(args.timeout, 10.0) if boot_window_mode else args.timeout,
                    "ANCHOR SET",
                )

            if args.save:
                save_cmd = "CONFIG SAVE"
                if boot_window_mode:
                    save_cmd = "S"
                print(f">> {save_cmd}")
                run_mutation_cmd(
                    ser,
                    save_cmd,
                    max(args.timeout, 10.0) if boot_window_mode else max(args.timeout, 8.0),
                    "CONFIG SAVE",
                )

            if args.reboot:
                reboot_cmd = "REBOOT"
                if boot_window_mode:
                    reboot_cmd = "RB"
                print(f">> {reboot_cmd}")
                run_mutation_cmd(ser, reboot_cmd, args.timeout, "REBOOT")
                time.sleep(0.7)

            try:
                status2 = query_status_or_role(ser, max(args.timeout, 8.0))
                if status2.upper().startswith("ERR:"):
                    raise RuntimeError(f"final STATUS failed: {status2}")
            except TimeoutError:
                if boot_window_mode:
                    print("[warn] final STATUS timeout in boot-window mode; "
                          "commands already acknowledged")
                else:
                    raise

        return 0
    except (TimeoutError, RuntimeError, serial.SerialException) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
