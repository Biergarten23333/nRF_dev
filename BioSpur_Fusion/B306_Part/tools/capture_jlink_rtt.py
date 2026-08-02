#!/usr/bin/env python3
"""Capture and optionally command fixed-address RTT through an explicit probe."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from jlink_rtt_transport import JLinkRttTransport


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial-number", type=int, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--speed-khz", type=int, default=4000)
    parser.add_argument("--address", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument("--down-channel", type=int, default=0)
    parser.add_argument("--duration-s", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--until-text",
        action="append",
        default=[],
        help="Exit early after every required byte string has appeared; repeatable.",
    )
    parser.add_argument(
        "--fail-text",
        action="append",
        default=[],
        help="Abort immediately if this byte string appears; repeatable.",
    )
    parser.add_argument(
        "--post-match-s",
        type=float,
        default=0.0,
        help="Optional short evidence tail after all --until-text strings match.",
    )
    parser.add_argument(
        "--command",
        action="append",
        default=[],
        help="Line to write to the RTT down-channel after discovery; repeatable.",
    )
    parser.add_argument(
        "--command-file",
        type=Path,
        help="Write each non-empty line from this file to the RTT down-channel.",
    )
    parser.add_argument(
        "--reset-target",
        action="store_true",
        help="Reset and run the explicitly selected target before RTT discovery.",
    )
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    transport = JLinkRttTransport(
        serial_number=args.serial_number,
        device=args.device,
        address=args.address,
        speed_khz=args.speed_khz,
        up_channel=args.channel,
        down_channel=args.down_channel,
    )
    byte_count = 0
    commands = list(args.command)
    if args.command_file is not None:
        commands.extend(
            line
            for raw in args.command_file.read_text(encoding="utf-8").splitlines()
            if (line := raw.strip())
        )
    started = time.monotonic()
    required = {item.encode("utf-8"): False for item in args.until_text}
    forbidden = tuple(item.encode("utf-8") for item in args.fail_text)
    scan_tail = b""
    matched_at: float | None = None
    failure: str | None = None
    try:
        transport.open(reset_target=args.reset_target)
        for command in commands:
            transport.write_line(command)

        with args.output.open("wb") as output:
            deadline = started + args.duration_s
            while time.monotonic() < deadline:
                data = transport.read(4096)
                if data:
                    output.write(data)
                    output.flush()
                    byte_count += len(data)
                    scan = scan_tail + data
                    for token in required:
                        if token in scan:
                            required[token] = True
                    for token in forbidden:
                        if token in scan:
                            failure = token.decode("utf-8", errors="replace")
                            break
                    if failure is not None:
                        break
                    if required and all(required.values()) and matched_at is None:
                        matched_at = time.monotonic()
                    longest = max(
                        [len(token) for token in (*required.keys(), *forbidden)] or [1]
                    )
                    scan_tail = scan[-longest:]
                else:
                    time.sleep(0.01)
                if matched_at is not None and time.monotonic() - matched_at >= args.post_match_s:
                    break
    finally:
        transport.close()

    elapsed = time.monotonic() - started
    if failure is not None:
        raise RuntimeError(
            f"RTT failure marker observed: {failure}; serial={args.serial_number} "
            f"output={args.output}"
        )
    missing = [
        token.decode("utf-8", errors="replace")
        for token, seen in required.items()
        if not seen
    ]
    if missing:
        raise RuntimeError(
            f"RTT required marker(s) missing before timeout: {missing}; "
            f"serial={args.serial_number} output={args.output}"
        )
    print(
        f"RTT_CAPTURE_OK serial={args.serial_number} address=0x{args.address:08x} "
        f"elapsed_s={elapsed:.3f} bytes={byte_count} commands={len(commands)} "
        f"required_markers={len(required)} early_exit={int(matched_at is not None)} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
