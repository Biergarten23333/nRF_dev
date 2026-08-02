#!/usr/bin/env python3
"""Send a timed command sequence to a BioSpur native-USB control port safely.

``--step`` retains the original command-then-wait behavior.  Use ``--at`` for
validation deadlines: each command is sent at an absolute offset from opening
the port, so response time from earlier commands cannot pull a later deadline
forward or make the caller misread a relative delay as an absolute one.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import serial


def parse_step(value: str) -> tuple[float, str]:
    wait_text, separator, command = value.partition(":")
    if not separator or not command.strip():
        raise argparse.ArgumentTypeError("step must be WAIT_SECONDS:COMMAND")
    try:
        wait_seconds = float(wait_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("step wait must be numeric") from exc
    if wait_seconds < 0:
        raise argparse.ArgumentTypeError("step wait must be non-negative")
    return wait_seconds, command.strip()


def parse_at(value: str) -> tuple[float, str]:
    at_text, separator, command = value.partition(":")
    if not separator or not command.strip():
        raise argparse.ArgumentTypeError("deadline must be ELAPSED_SECONDS:COMMAND")
    try:
        elapsed_seconds = float(at_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("deadline offset must be numeric") from exc
    if elapsed_seconds < 0:
        raise argparse.ArgumentTypeError("deadline offset must be non-negative")
    return elapsed_seconds, command.strip()


def open_no_modem_pulse(port: str, baud: int) -> serial.Serial:
    handle = serial.Serial()
    handle.port = port
    handle.baudrate = baud
    handle.timeout = 0.05
    handle.write_timeout = 2.0
    handle.exclusive = True
    # Set the inactive levels before open; native nRF USB CDC must not see a
    # DTR/RTS reset pulse merely because the host starts an operation.
    handle.dtr = False
    handle.rts = False
    handle.open()
    return handle


def drain(handle: serial.Serial, deadline: float, log, started: float) -> None:
    while time.monotonic() < deadline:
        data = handle.read(handle.in_waiting or 1)
        if not data:
            continue
        text = data.decode("utf-8", errors="replace")
        stamped = f"[{time.monotonic() - started:9.3f}] {text}"
        log.write(stamped)
        log.flush()


def send_command(handle: serial.Serial, command: str, log, started: float) -> None:
    log.write(f"[{time.monotonic() - started:9.3f}] >>> {command}\n")
    log.flush()
    handle.write((command + "\n").encode("utf-8"))
    handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--log", type=Path, required=True)
    schedule = parser.add_mutually_exclusive_group(required=True)
    schedule.add_argument(
        "--step",
        action="append",
        type=parse_step,
        metavar="WAIT_SECONDS:COMMAND",
        help="send COMMAND, then drain for WAIT_SECONDS (legacy semantics)",
    )
    schedule.add_argument(
        "--at",
        action="append",
        type=parse_at,
        metavar="ELAPSED_SECONDS:COMMAND",
        help="send COMMAND at an absolute offset from port-open time",
    )
    parser.add_argument("--initial-drain", type=float, default=0.5)
    parser.add_argument(
        "--final-drain",
        type=float,
        default=1.5,
        help="seconds to collect replies after the last absolute command",
    )
    args = parser.parse_args()

    if args.at:
        previous = -1.0
        for elapsed_seconds, _ in args.at:
            if elapsed_seconds < previous:
                parser.error("--at deadlines must be nondecreasing")
            previous = elapsed_seconds

    args.log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with args.log.open("w", encoding="utf-8") as log:
        handle = open_no_modem_pulse(args.port, args.baud)
        try:
            log.write(
                f"[{0.0:9.3f}] OPEN {args.port} resolved={Path(args.port).resolve()} "
                "DTR=0 RTS=0\n"
            )
            drain(handle, time.monotonic() + args.initial_drain, log, started)
            if args.step:
                for wait_seconds, command in args.step:
                    send_command(handle, command, log, started)
                    drain(handle, time.monotonic() + wait_seconds, log, started)
            else:
                for elapsed_seconds, command in args.at:
                    absolute_deadline = started + elapsed_seconds
                    drain(handle, absolute_deadline, log, started)
                    actual = time.monotonic() - started
                    log.write(
                        f"[{actual:9.3f}] DEADLINE requested={elapsed_seconds:.3f} "
                        f"error_ms={(actual - elapsed_seconds) * 1000.0:.3f}\n"
                    )
                    send_command(handle, command, log, started)
                drain(handle, time.monotonic() + args.final_drain, log, started)
        finally:
            handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
