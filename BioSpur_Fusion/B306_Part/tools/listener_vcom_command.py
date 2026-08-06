#!/usr/bin/env python3
"""Send one allow-listed listener VCOM command after a decode guard.

The stable SEGGER SNR, registered baud, parsed firmware marker, and one
known-format line are all proven before the write.  This prevents an open
ttyACM endpoint from being mistaken for target identity.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from listener_array_collector import ParseError, parse_listener_line


PORT_TEMPLATE = "/dev/serial/by-id/usb-SEGGER_J-Link_000{}-if00"
ALLOWED_COMMANDS = {
    "MODE_IDLE",
    "MODE_LISTEN",
    "MODE_QUERY",
    "BEACON_STATUS",
    "BEACON_PERIOD 100",
    "BEACON_PERIOD 110",
    "BEACON_PERIOD 120",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="milliseconds"
    )


def open_no_modem_pulse(port: Path, baud: int) -> serial.Serial:
    handle = serial.Serial()
    handle.port = str(port)
    handle.baudrate = baud
    handle.timeout = 0.1
    handle.write_timeout = 2.0
    handle.exclusive = True
    handle.dsrdtr = False
    handle.rtscts = False
    handle.dtr = False
    handle.rts = False
    handle.open()
    handle.dtr = False
    handle.rts = False
    return handle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snr", required=True)
    parser.add_argument("--baud", type=int, default=460800)
    parser.add_argument("--expected-marker", required=True)
    parser.add_argument("--command", choices=sorted(ALLOWED_COMMANDS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--guard-timeout-s", type=float, default=10.0)
    parser.add_argument("--post-seconds", type=float, default=5.0)
    args = parser.parse_args()

    if not args.snr.isdecimal():
        parser.error("--snr must be decimal digits")
    port = Path(PORT_TEMPLATE.format(args.snr))
    expected_name = f"usb-SEGGER_J-Link_000{args.snr}-if00"
    if port.name != expected_name or not port.is_symlink():
        raise SystemExit(f"stable SNR endpoint missing: {port}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise SystemExit(f"refusing existing output: {args.output}")

    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": utc_now(),
        "snr": args.snr,
        "port": str(port),
        "resolved": str(port.resolve()),
        "baud": args.baud,
        "dtr": False,
        "rts": False,
        "expected_marker": args.expected_marker,
        "command": args.command,
        "guard": None,
        "sent": False,
        "post_lines": [],
    }
    handle = open_no_modem_pulse(port, args.baud)
    buffer = bytearray()
    try:
        guard_deadline = time.monotonic() + args.guard_timeout_s
        guard: dict[str, object] | None = None
        while time.monotonic() < guard_deadline and guard is None:
            chunk = handle.read(max(1, min(4096, handle.in_waiting or 1)))
            if not chunk:
                continue
            buffer.extend(chunk)
            while b"\n" in buffer:
                raw, _, remainder = buffer.partition(b"\n")
                buffer = bytearray(remainder)
                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                try:
                    kind, fields = parse_listener_line(line)
                except (ParseError, ValueError):
                    continue
                marker = str(fields.get("marker", ""))
                if marker != args.expected_marker:
                    continue
                guard = {
                    "utc": utc_now(),
                    "kind": kind,
                    "marker": marker,
                    "line": line,
                }
                break
        if guard is None:
            result["status"] = "FAIL"
            result["error"] = "decode-before-send guard timed out"
            return_code = 1
        else:
            result["guard"] = guard
            payload = (args.command + "\n").encode("ascii")
            handle.write(payload)
            handle.flush()
            result["sent"] = True
            result["sent_utc"] = utc_now()
            result["sent_bytes"] = len(payload)
            post_deadline = time.monotonic() + args.post_seconds
            post_lines: list[str] = []
            while time.monotonic() < post_deadline:
                chunk = handle.read(max(1, min(4096, handle.in_waiting or 1)))
                if not chunk:
                    continue
                buffer.extend(chunk)
                while b"\n" in buffer:
                    raw, _, remainder = buffer.partition(b"\n")
                    buffer = bytearray(remainder)
                    post_lines.append(
                        raw.decode("utf-8", errors="replace").rstrip("\r")
                    )
            result["post_lines"] = post_lines
            expected_reply = {
                "MODE_IDLE": "MODE=IDLE",
                "MODE_LISTEN": "MODE=LISTEN",
                "MODE_QUERY": "MODE=",
                "BEACON_STATUS": "LBSTAT;",
                "BEACON_PERIOD 100": "LBSTAT;",
                "BEACON_PERIOD 110": "LBSTAT;",
                "BEACON_PERIOD 120": "LBSTAT;",
            }[args.command]
            result["reply_seen"] = any(
                expected_reply in line for line in post_lines
            )
            if args.command.startswith("BEACON_PERIOD "):
                period_ms = args.command.rsplit(" ", 1)[1]
                result[f"period_{period_ms}_seen"] = any(
                    line.startswith("LBSTAT;")
                    and f";{period_ms}000;" in line
                    and ";MAIN;" in line
                    for line in post_lines
                )
                result["reply_seen"] = bool(
                    result["reply_seen"]
                    and result[f"period_{period_ms}_seen"]
                )
            result["status"] = "PASS" if result["reply_seen"] else "FAIL"
            if not result["reply_seen"]:
                result["error"] = f"expected reply not seen: {expected_reply}"
            return_code = 0 if result["status"] == "PASS" else 1
    finally:
        handle.close()
        result["ended_utc"] = utc_now()
        args.output.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
