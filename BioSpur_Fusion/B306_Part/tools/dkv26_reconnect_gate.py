#!/usr/bin/env python3
"""Bounded post-flash reconnect and LED-console gate for DK-v26."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import BSFS
from coldstart_fusion_control import _ensure_spacing, decode_guard
from fusion_session import LineChannel, SessionError, parse_fields, resolve_fusion_port


EXPECTED_MARKER = "dk-fusion-imu-relay-v26"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def wait_prefix(channel: LineChannel, prefix: str, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is not None and line.startswith(prefix):
            return line
    raise SessionError(f"timeout waiting for {prefix!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)

    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started": now(),
        "expected_marker": EXPECTED_MARKER,
        "expected_nodes": BSFS,
        "writes": [
            "SPACING ON if required",
            "LEDEXPECT 5",
            "LEDSTAT",
            "LEDCLEAR",
            "LEDSTAT",
        ],
    }
    channel: LineChannel | None = None
    with (args.out_dir / "fusion_cdc.log").open(
        "x", encoding="utf-8", buffering=1
    ) as log:
        try:
            channel = LineChannel(
                resolve_fusion_port(args.fusion_port), log, "FUSION"
            )
            result["port"] = channel.port
            result["decode_before_send"] = decode_guard(channel, 15.0)

            spacing = _ensure_spacing(channel)
            result["spacing"] = spacing
            result["list"] = spacing["list"]

            channel.send("MASTER STATUS")
            master_line = wait_prefix(channel, "FUSION_MASTER_STATUS ", 5.0)
            master = parse_fields(master_line)
            if master.get("marker") != EXPECTED_MARKER:
                raise SessionError(f"marker mismatch: {master_line}")
            result["master_status"] = master_line

            channel.send("LEDEXPECT 5")
            expect_line = wait_prefix(channel, "LEDEXPECT ", 5.0)
            expect = parse_fields(expect_line)
            if expect.get("value") != "5" or expect.get("ready") != "5":
                raise SessionError(f"LEDEXPECT gate failed: {expect_line}")
            result["ledexpect"] = expect_line

            channel.send("LEDSTAT")
            preclear_line = wait_prefix(channel, "LEDSTAT ", 5.0)
            result["ledstat_preclear"] = preclear_line

            # The reader was absent while the freshly flashed master resumed
            # five high-rate streams, and SPACING ON intentionally cycled all
            # peers. Preserve those deployment-boundary latches above, then
            # arm the formal runtime baseline only after CDC and all links
            # are stable.
            channel.send("LEDCLEAR")
            result["ledclear"] = wait_prefix(channel, "LEDCLEAR ", 5.0)
            result["decoder_errors_before_arm"] = (
                channel.binary_decoder.errors
            )
            channel.binary_decoder.errors = 0
            channel.send("LEDSTAT")
            ledstat_line = wait_prefix(channel, "LEDSTAT ", 5.0)
            ledstat = parse_fields(ledstat_line)
            required = {
                "expect": "5",
                "ready": "5",
                "latch": "0",
                "mask": "0x00",
            }
            mismatch = {
                key: (ledstat.get(key), expected)
                for key, expected in required.items()
                if ledstat.get(key) != expected
            }
            if mismatch:
                raise SessionError(
                    f"LEDSTAT clean gate failed {mismatch}: {ledstat_line}"
                )
            result["ledstat"] = ledstat_line
            # Hold a short armed interval so the post-clear result is not
            # merely an instantaneous snapshot.
            armed_deadline = time.monotonic() + 2.0
            while time.monotonic() < armed_deadline:
                channel.read(armed_deadline)
            channel.send("LEDSTAT")
            armed_line = wait_prefix(channel, "LEDSTAT ", 5.0)
            armed = parse_fields(armed_line)
            if armed.get("latch") != "0" or armed.get("mask") != "0x00":
                raise SessionError(
                    f"LEDSTAT re-latched during armed hold: {armed_line}"
                )
            result["ledstat_armed_hold"] = armed_line
            result["decoder_errors"] = channel.binary_decoder.errors
            if channel.binary_decoder.errors != 0:
                raise SessionError(
                    f"decoder errors={channel.binary_decoder.errors}"
                )
            result["status"] = "PASS"
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if channel is not None:
                channel.close()
            result["ended"] = now()
            (args.out_dir / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
