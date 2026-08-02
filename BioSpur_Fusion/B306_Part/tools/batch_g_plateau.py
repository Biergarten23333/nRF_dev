#!/usr/bin/env python3
"""Batch-G connection plateau and two-minute transport gate."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from coldstart_fusion_control import decode_guard
from fusion_session import (
    LineChannel,
    SessionError,
    parse_fields,
    resolve_fusion_port,
    u32_delta,
)
from pre_ramp_hardening import (
    TelemetryAssembler,
    collect_for,
    request_list,
    request_resources,
    wait_for_telemetry,
)


EXPECTED_MARKER = "dk-fusion-imu-relay-v27"
HARD_DEVICE_FIELDS = (
    "crc",
    "header",
    "ring_drop",
    "duplicate",
    "reorder",
    "drop_err",
    "notify_errno",
    "uart_restarts",
    "uart_err",
    "logger_drop",
    "imu_i2c_err",
    "imu_missed_deadlines",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="milliseconds"
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def wait_prefix(channel: LineChannel, prefix: str, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is not None and line.startswith(prefix):
            return line
    raise SessionError(f"timeout waiting for {prefix!r}")


def parse_names(text: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in text.split(",") if item.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--peers", required=True)
    parser.add_argument("--expect-uwb-peers", default="")
    parser.add_argument("--duration-s", type=float, default=120.0)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()

    peers = parse_names(args.peers)
    expect_uwb = parse_names(args.expect_uwb_peers)
    if not 1 <= len(peers) <= 10 or len(set(peers)) != len(peers):
        raise SessionError("--peers requires 1..10 distinct BSF identities")
    if not set(expect_uwb).issubset(peers):
        raise SessionError("--expect-uwb-peers must be a subset of --peers")
    if args.duration_s <= 0:
        raise SessionError("--duration-s must be positive")
    args.out_dir.mkdir(parents=True, exist_ok=False)

    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started": utc_now(),
        "expected_marker": EXPECTED_MARKER,
        "expected_peers": peers,
        "expected_uwb_peers": expect_uwb,
        "duration_requested_s": args.duration_s,
        "writes": ("LIST", f"LEDEXPECT {len(peers)}", "LEDSTAT", "LEDCLEAR"),
    }
    write_json(args.out_dir / "summary.json", result)

    counters: dict[str, int] = {}
    line_kinds: Counter[str] = Counter()
    uwb_counts: Counter[str] = Counter()
    imu_counts: Counter[str] = Counter()
    assembler = TelemetryAssembler()
    channel: LineChannel | None = None

    with (args.out_dir / "fusion_cdc.log").open(
        "x", encoding="utf-8", buffering=1
    ) as raw:
        try:
            channel = LineChannel(
                resolve_fusion_port(args.fusion_port), raw, "FUSION"
            )
            result["port"] = channel.port
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_before_send"] = decode_guard(channel, 15.0)

            channel.send("MASTER STATUS")
            master_line = wait_prefix(
                channel, "FUSION_MASTER_STATUS ", 5.0
            )
            master = parse_fields(master_line)
            if master.get("marker") != EXPECTED_MARKER:
                raise SessionError(f"marker mismatch: {master_line}")
            result["master_status"] = master_line

            start_list = request_list(channel, assembler, counters, peers)
            aggregate = start_list["aggregate"]
            if (
                aggregate.get("count") != str(len(peers))
                or aggregate.get("ready") != str(len(peers))
                or set(start_list["peers"]) != set(peers)
            ):
                raise SessionError(f"peer gate failed: {start_list}")
            if (
                aggregate.get("spacing") != "ON"
                or aggregate.get("spacing_us") != "5000"
            ):
                raise SessionError(
                    "5 ms spacing is not already active; "
                    f"refusing an implicit reconnect: {aggregate}"
                )
            expected_link = {
                "interval_units": "40",
                "latency": "0",
                "timeout_units": "400",
                "phy_tx": "2",
                "phy_rx": "2",
            }
            link_errors: dict[str, dict[str, tuple[str | None, str]]] = {}
            for name in peers:
                row = start_list["peers"][name]
                mismatch = {
                    key: (row.get(key), value)
                    for key, value in expected_link.items()
                    if row.get(key) != value
                }
                if mismatch:
                    link_errors[name] = mismatch
            if link_errors:
                raise SessionError(f"link parameter gate failed: {link_errors}")
            result["start_list"] = start_list

            channel.send(f"LEDEXPECT {len(peers)}")
            expect_line = wait_prefix(channel, "LEDEXPECT ", 5.0)
            expect = parse_fields(expect_line)
            if (
                expect.get("value") != str(len(peers))
                or expect.get("ready") != str(len(peers))
            ):
                raise SessionError(f"LEDEXPECT gate failed: {expect_line}")
            result["ledexpect"] = expect_line

            channel.send("LEDSTAT")
            result["ledstat_before_clear"] = wait_prefix(
                channel, "LEDSTAT ", 5.0
            )
            channel.send("LEDCLEAR")
            result["ledclear"] = wait_prefix(channel, "LEDCLEAR ", 5.0)
            channel.binary_decoder.errors = 0

            start_resources = request_resources(channel, assembler, counters)
            baseline = wait_for_telemetry(
                channel, assembler, peers, counters, timeout_s=12.0
            )

            started = time.monotonic()
            deadline = started + args.duration_s
            while time.monotonic() < deadline:
                line = channel.read(min(deadline, time.monotonic() + 0.5))
                if line is None:
                    continue
                assembler.observe(line)
                kind = line.split(" ", 1)[0]
                line_kinds[kind] += 1
                fields = parse_fields(line)
                name = fields.get("name")
                if kind == "FUSION_UWB" and name in peers:
                    uwb_counts[name] += 1
                elif kind == "FUSION_IMU" and name in peers:
                    imu_counts[name] += 1
            elapsed = time.monotonic() - started

            final = wait_for_telemetry(
                channel, assembler, peers, counters, timeout_s=12.0
            )
            end_list = request_list(channel, assembler, counters, peers)
            end_resources = request_resources(channel, assembler, counters)
            channel.send("LEDSTAT")
            ledstat_line = wait_prefix(channel, "LEDSTAT ", 5.0)
            ledstat = parse_fields(ledstat_line)

            hard_deltas = {
                name: {
                    field: u32_delta(
                        int(baseline[name][field], 0),
                        int(final[name][field], 0),
                    )
                    for field in HARD_DEVICE_FIELDS
                }
                for name in peers
            }
            cdc_deltas = {
                field: int(end_resources["summary"][field], 0)
                - int(start_resources["summary"][field], 0)
                for field in ("cdc_drop_bytes", "cdc_drop_records")
            }
            end_aggregate = end_list["aggregate"]
            rates = {
                name: uwb_counts[name] / elapsed for name in peers
            }
            gates = {
                "peer_set_and_ready_start": True,
                "peer_set_and_ready_end": (
                    end_aggregate.get("count") == str(len(peers))
                    and end_aggregate.get("ready") == str(len(peers))
                    and set(end_list["peers"]) == set(peers)
                ),
                "spacing_5000_start": True,
                "spacing_5000_end": (
                    end_aggregate.get("spacing") == "ON"
                    and end_aggregate.get("spacing_us") == "5000"
                ),
                "zero_disconnect_lines": (
                    line_kinds["FUSION_DISCONNECTED"] == 0
                ),
                "zero_malformed_lines": (
                    line_kinds["FUSION_MALFORMED"] == 0
                ),
                "zero_device_hard_deltas": all(
                    value == 0
                    for row in hard_deltas.values()
                    for value in row.values()
                ),
                "zero_cdc_drop_delta": all(
                    value == 0 for value in cdc_deltas.values()
                ),
                "zero_decoder_errors": channel.binary_decoder.errors == 0,
                "imu_stopped": (
                    sum(imu_counts.values()) == 0
                    and all(final[name].get("imu_active") == "0" for name in peers)
                ),
                "expected_uwb_present": all(
                    rates[name] >= 8.0 for name in expect_uwb
                ),
                "ledstat_clean": (
                    ledstat.get("expect") == str(len(peers))
                    and ledstat.get("ready") == str(len(peers))
                    and ledstat.get("latch") == "0"
                    and ledstat.get("mask") == "0x00"
                ),
            }
            result.update(
                {
                    "status": "PASS" if all(gates.values()) else "FAIL",
                    "duration_measured_s": elapsed,
                    "end_list": end_list,
                    "start_resources": start_resources,
                    "end_resources": end_resources,
                    "hard_device_deltas": hard_deltas,
                    "cdc_drop_deltas": cdc_deltas,
                    "line_counts": dict(line_kinds),
                    "uwb_counts": dict(uwb_counts),
                    "uwb_rates_hz": rates,
                    "imu_counts": dict(imu_counts),
                    "ledstat": ledstat_line,
                    "decoder_errors": channel.binary_decoder.errors,
                    "gates": gates,
                    "pass": all(gates.values()),
                }
            )
            if not result["pass"]:
                raise SessionError(f"plateau gate failed: {gates}")
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if channel is not None:
                channel.close()
            result["ended"] = utc_now()
            write_json(args.out_dir / "summary.final.json", result)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
