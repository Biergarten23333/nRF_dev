#!/usr/bin/env python3
"""Cold-reboot one exact DWM tag and prove it returned with the expected marker."""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from pathlib import Path

import serial

DEFAULT_PORT = (
    "/dev/serial/by-id/"
    "usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00"
)


def open_safe(port: Path) -> serial.Serial:
    if "/dev/serial/by-id/" not in str(port):
        raise RuntimeError("refusing transient port; use an exact /dev/serial/by-id path")
    handle = serial.Serial()
    handle.port = str(port)
    handle.baudrate = 115200
    handle.timeout = 0.05
    handle.write_timeout = 2.0
    handle.exclusive = True
    handle.dtr = False
    handle.rts = False
    handle.open()
    return handle


class Transcript:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("w", encoding="utf-8")
        self.started = time.monotonic()

    def write(self, text: str) -> None:
        line = f"[{time.monotonic() - self.started:8.3f}] {text}"
        print(line, end="", flush=True)
        self.file.write(line)
        self.file.flush()

    def close(self) -> None:
        self.file.close()


def command(handle: serial.Serial, transcript: Transcript, text: str,
            seconds: float) -> str:
    transcript.write(f">>> {text}\n")
    handle.write((text + "\n").encode())
    handle.flush()
    deadline = time.monotonic() + seconds
    chunks: list[bytes] = []
    while time.monotonic() < deadline:
        data = handle.read(handle.in_waiting or 1)
        if data:
            chunks.append(data)
    response = b"".join(chunks).decode(errors="replace")
    if response:
        transcript.write(response if response.endswith("\n") else response + "\n")
    return response


def version_matches(text: str, target: str, marker: str) -> bool:
    return any(
        target in line and f"VERSION fw={marker}" in line
        for line in text.splitlines()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, type=str.upper)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--port", type=Path, default=Path(DEFAULT_PORT))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()

    if not re.fullmatch(r"BS[0-9A-F]{4}", args.target):
        parser.error("--target must be exact BS%04X")
    if args.timeout < 10:
        parser.error("--timeout must be at least 10 seconds")
    if not args.port.exists():
        raise SystemExit(f"Master Tag control port missing: {args.port}")

    log = args.log or Path("UWB_Part/logs") / (
        f"remote_reboot_{args.target.lower()}_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )
    transcript = Transcript(log)
    handle = open_safe(args.port)
    try:
        transcript.write(
            f"OPEN port={args.port} resolved={args.port.resolve()} DTR=0 RTS=0 "
            f"target={args.target} marker={args.marker}\n"
        )
        command(handle, transcript, f"ota_target name {args.target}", 1.0)
        before = command(handle, transcript, "cmd VERSION", 3.0)
        if not version_matches(before, args.target, args.marker):
            transcript.write("REMOTE_REBOOT_PREFLIGHT_FAIL stage=pre_version\n")
            return 1

        reboot = command(handle, transcript, "cmd REBOOT", 3.0)
        reboot_ack = "REBOOTING" in reboot
        reconnect_seen = "Connected[" in reboot
        if not reboot_ack:
            transcript.write(
                "REMOTE_REBOOT_PREFLIGHT_WARN stage=reboot_ack "
                "detail=notification_may_be_lost_before_disconnect\n"
            )

        deadline = time.monotonic() + args.timeout
        attempt = 0
        while time.monotonic() < deadline:
            time.sleep(1.5)
            attempt += 1
            response = command(handle, transcript, "cmd VERSION", 2.5)
            reconnect_seen = reconnect_seen or "Connected[" in response
            if version_matches(response, args.target, args.marker) and (
                reboot_ack or reconnect_seen
            ):
                transcript.write(
                    f"REMOTE_REBOOT_PREFLIGHT_PASS attempts={attempt} "
                    f"target={args.target} marker={args.marker} "
                    f"evidence={'ack' if reboot_ack else 'reconnect'}\n"
                )
                return 0
        transcript.write(
            "REMOTE_REBOOT_PREFLIGHT_FAIL stage=post_version_timeout "
            f"reboot_ack={int(reboot_ack)} reconnect_seen={int(reconnect_seen)}\n"
        )
        return 1
    finally:
        handle.close()
        transcript.close()


if __name__ == "__main__":
    raise SystemExit(main())
