#!/usr/bin/env python3
"""Token-gated DK-v26 five-minute machine acceptance capture."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import BSFS, ensure_imu_stopped
from coldstart_fusion_control import (
    decode_guard,
    list_gate,
    phase_imu_start,
)
from fusion_session import LineChannel, SessionError, parse_fields, resolve_fusion_port
from listener_array_run import COLLECTOR, wait_listener_preflight


HARD_TELEMETRY_COUNTERS = (
    "crc",
    "header",
    "ring_drop",
    "duplicate",
    "reorder",
    "drop_err",
    "uart_restarts",
    "relay_timeout",
    "logger_drop",
)
HARD_QUEUE_COUNTERS = (
    "q_drop_imu",
    "q_drop_uwb",
    "q_drop_ctl",
    "abort_imu",
    "abort_uwb",
    "abort_ctl",
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def wait_prefix(channel: LineChannel, prefix: str, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is not None and line.startswith(prefix):
            return line
    raise SessionError(f"timeout waiting for {prefix!r}")


def drain_until(channel: LineChannel, deadline: float) -> None:
    while time.monotonic() < deadline:
        channel.read(min(deadline, time.monotonic() + 0.5))


def phase_imu_stop_confirmed(channel: LineChannel) -> dict[str, object]:
    """Make STOP idempotent and gate on the independent active=0 readback.

    An already-idle JY61P can return an I2C-health failure from IMU STOP even
    though the producer is inactive.  For this capture the safety property is
    active=0, so use the established capacity helper that always confirms it
    with IMU STATUS instead of treating STOP's health transcript as the state
    proof.
    """
    return {
        "imu_stop": {
            node: ensure_imu_stopped(channel, node)
            for node in BSFS
        }
    }


def u32_delta(first: int, last: int) -> int:
    return (last - first) & 0xFFFFFFFF


def read_logged_rows(path: Path) -> list[tuple[float, str]]:
    rows: list[tuple[float, str]] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            parts = raw.rstrip("\n").split(" ", 3)
            if len(parts) != 4 or parts[2] != "FUSION_RX":
                continue
            rows.append((float(parts[1]), parts[3]))
    return rows


def sequence_faults(lines: list[str]) -> int:
    faults = 0
    have = False
    last_seq = 0
    last_uptime = 0
    for line in lines:
        fields = parse_fields(line)
        seq = int(fields["pkt"], 0)
        uptime = int(fields["node_ms"], 0)
        if not have or uptime < last_uptime:
            have = True
        elif ((seq - last_seq) & 0xFFFFFFFF) != 1:
            faults += 1
        last_seq = seq
        last_uptime = uptime
    return faults


def counter_deltas(
    rows: list[tuple[float, str]],
    start: float,
    end: float,
    prefix: str,
    counters: tuple[str, ...],
) -> dict[str, dict[str, int | None]]:
    per_node: dict[str, list[dict[str, str]]] = defaultdict(list)
    for stamp, line in rows:
        if start <= stamp <= end and line.startswith(prefix):
            fields = parse_fields(line)
            if fields.get("name") in BSFS:
                per_node[fields["name"]].append(fields)
    result: dict[str, dict[str, int | None]] = {}
    for node in BSFS:
        samples = per_node[node]
        result[node] = {
            key: (
                u32_delta(int(samples[0].get(key, "0"), 0),
                          int(samples[-1].get(key, "0"), 0))
                if len(samples) >= 2
                else None
            )
            for key in counters
        }
        result[node]["samples"] = len(samples)
    return result


def listener_role_failures(summary: dict[str, object]) -> list[str]:
    """Validate witness output according to the deployed listener role."""
    failures: list[str] = []
    listeners = summary.get("listeners", {})
    if not isinstance(listeners, dict):
        return ["listener summary has no listeners map"]
    required_by_role = {
        "OBSERVER": ("LSTAT", "LPD", "LRD"),
        "MAIN": ("LSTAT", "LBTX"),
        "SLAVED": ("LSTAT", "LBD"),
    }
    for snr, value in listeners.items():
        if not isinstance(value, dict):
            failures.append(f"{snr}: invalid summary")
            continue
        first = value.get("first_lstat", {})
        role = first.get("role") if isinstance(first, dict) else None
        kinds = value.get("kinds", {})
        if value.get("error"):
            failures.append(f"{snr}: {value['error']}")
        if int(value.get("parse_errors", 0)) != 0:
            failures.append(f"{snr}: parse errors")
        if int(value.get("serial_errors", 0)) != 0:
            failures.append(f"{snr}: serial errors")
        if role not in required_by_role:
            failures.append(f"{snr}: unknown role {role!r}")
            continue
        for kind in required_by_role[role]:
            if not isinstance(kinds, dict) or int(kinds.get(kind, 0)) == 0:
                failures.append(f"{snr}: {role} missing required kind {kind}")
    return failures


def analyze(
    log_path: Path,
    formal_start: float,
    imu_command_start: float,
    imu_active_start: float,
    formal_end: float,
    decoder_errors: int,
    ledstat_close: str,
    listener_summary: dict[str, object],
) -> dict[str, object]:
    rows = read_logged_rows(log_path)
    duration = formal_end - formal_start
    imu_duration = formal_end - imu_active_start
    nodes: dict[str, object] = {}
    machine_pass = decoder_errors == 0
    for node in BSFS:
        uwb_lines = [
            line for stamp, line in rows
            if formal_start <= stamp <= formal_end
            and line.startswith("FUSION_UWB ")
            and parse_fields(line).get("name") == node
        ]
        imu_pre = [
            line for stamp, line in rows
            if formal_start <= stamp < imu_command_start
            and line.startswith("FUSION_IMU ")
            and parse_fields(line).get("name") == node
        ]
        imu_lines = [
            line for stamp, line in rows
            if imu_active_start <= stamp <= formal_end
            and line.startswith("FUSION_IMU ")
            and parse_fields(line).get("name") == node
        ]
        imu_samples = sum(int(parse_fields(line)["n"], 0) for line in imu_lines)
        node_result = {
            "uwb_records": len(uwb_lines),
            "uwb_rate_hz": len(uwb_lines) / duration,
            "uwb_sequence_faults": sequence_faults(uwb_lines),
            "imu_records_before_start": len(imu_pre),
            "imu_records": len(imu_lines),
            "imu_samples": imu_samples,
            "imu_rate_hz": imu_samples / imu_duration,
        }
        node_result["pass"] = (
            9.5 <= node_result["uwb_rate_hz"] <= 10.5
            and node_result["uwb_sequence_faults"] == 0
            and node_result["imu_records_before_start"] == 0
            and 190.0 <= node_result["imu_rate_hz"] <= 210.0
        )
        machine_pass = machine_pass and bool(node_result["pass"])
        nodes[node] = node_result

    telemetry = counter_deltas(
        rows, formal_start, formal_end,
        "FUSION_TELEMETRY ", HARD_TELEMETRY_COUNTERS,
    )
    queue = counter_deltas(
        rows, formal_start, formal_end,
        "FUSION_QUEUE ", HARD_QUEUE_COUNTERS,
    )
    for per_node in (telemetry, queue):
        for node in BSFS:
            for key, value in per_node[node].items():
                if key == "samples":
                    machine_pass = machine_pass and int(value or 0) >= 2
                else:
                    machine_pass = machine_pass and value == 0

    faults = [
        line for stamp, line in rows
        if formal_start <= stamp <= formal_end
        and (
            line.startswith("FUSION_DISCONNECTED ")
            or line.startswith("FUSION_MALFORMED ")
        )
    ]
    led = parse_fields(ledstat_close)
    led_clean = led.get("latch") == "0" and led.get("mask") == "0x00"
    listener_failures = listener_role_failures(listener_summary)
    machine_pass = (
        machine_pass
        and not faults
        and led_clean
        and not listener_failures
    )
    return {
        "pass": machine_pass,
        "duration_s": duration,
        "imu_duration_s": imu_duration,
        "nodes": nodes,
        "telemetry_delta": telemetry,
        "queue_delta": queue,
        "disconnect_or_malformed": faults,
        "decoder_errors": decoder_errors,
        "ledstat_close": ledstat_close,
        "ledstat_clean": led_clean,
        "listener_acceptance_failures": listener_failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    listener_dir = args.out_dir / "listeners"
    log_path = args.out_dir / "fusion_cdc.log"
    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started": now(),
        "duration_s": 300.0,
        "segments": {"uwb_only_s": 180.0, "uwb_imu_s": 120.0},
        "expected_nodes": BSFS,
    }
    (args.out_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    collector_cmd = [
        sys.executable,
        str(COLLECTOR),
        "--out-dir",
        str(listener_dir),
        "--duration",
        "390",
        "--require-kind",
        "LSTAT",
    ]
    collector_log = (args.out_dir / "listener_process.log").open(
        "x", encoding="utf-8", buffering=1
    )
    collector: subprocess.Popen[str] | None = None
    channel: LineChannel | None = None
    imu_started = False
    formal_start = imu_command_start = imu_active_start = formal_end = 0.0
    try:
        collector = subprocess.Popen(
            collector_cmd,
            cwd=Path(__file__).resolve().parents[2],
            stdout=collector_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        result["listener_preflight"] = wait_listener_preflight(
            listener_dir, collector
        )
        with log_path.open("x", encoding="utf-8", buffering=1) as log:
            channel = LineChannel(
                resolve_fusion_port(args.fusion_port), log, "FUSION"
            )
            result["port"] = channel.port
            result["decode_before_send"] = decode_guard(channel, 15.0)
            result["list_pre"] = list_gate(channel)
            result["imu_pre"] = phase_imu_stop_confirmed(channel)
            channel.send("LEDEXPECT 5")
            result["ledexpect"] = wait_prefix(channel, "LEDEXPECT ", 5.0)
            channel.send("LEDCLEAR")
            result["ledclear"] = wait_prefix(channel, "LEDCLEAR ", 5.0)
            channel.send("LEDSTAT")
            ledstat_start = wait_prefix(channel, "LEDSTAT ", 5.0)
            result["ledstat_start"] = ledstat_start
            if (
                parse_fields(ledstat_start).get("latch") != "0"
                or parse_fields(ledstat_start).get("mask") != "0x00"
            ):
                raise SessionError(f"start LEDSTAT not clean: {ledstat_start}")

            channel.binary_decoder.errors = 0
            formal_start = time.monotonic()
            formal_deadline = formal_start + 300.0
            print("DKV26_PHASE UWB_ONLY START t=0", flush=True)
            drain_until(channel, formal_start + 180.0)

            imu_command_start = time.monotonic()
            print("DKV26_PHASE IMU_START_COMMANDS t=180", flush=True)
            result["imu_start"] = phase_imu_start(channel)
            imu_started = True
            imu_active_start = time.monotonic()
            print(
                f"DKV26_PHASE IMU_FLOW START t={imu_active_start - formal_start:.3f}",
                flush=True,
            )
            drain_until(channel, formal_deadline)
            formal_end = time.monotonic()
            print("DKV26_PHASE IMU_STOP", flush=True)
            result["imu_stop"] = phase_imu_stop_confirmed(channel)
            imu_started = False
            drain_until(channel, time.monotonic() + 2.0)
            channel.send("LEDSTAT")
            ledstat_close = wait_prefix(channel, "LEDSTAT ", 5.0)
            result["ledstat_close"] = ledstat_close
            result["decoder_errors"] = channel.binary_decoder.errors

        channel.close()
        channel = None
        if collector is not None and collector.poll() is None:
            collector.send_signal(signal.SIGINT)
        if collector is not None:
            result["collector_return_code"] = collector.wait(timeout=30.0)
        listener_summary = json.loads(
            (listener_dir / "summary.json").read_text(encoding="utf-8")
        )
        result["listener_summary"] = listener_summary
        result["machine"] = analyze(
            log_path, formal_start, imu_command_start, imu_active_start,
            formal_end, int(result["decoder_errors"]),
            str(result["ledstat_close"]), listener_summary,
        )
        result["status"] = (
            "PASS" if result["machine"]["pass"] else "FAIL"
        )
    except BaseException as exc:
        result["status"] = "FAIL"
        result["error"] = f"{type(exc).__name__}: {exc}"
        if channel is not None and imu_started:
            try:
                result["imu_stop_after_error"] = phase_imu_stop_confirmed(channel)
                imu_started = False
            except Exception as stop_exc:
                result["imu_stop_after_error_error"] = (
                    f"{type(stop_exc).__name__}: {stop_exc}"
                )
    finally:
        if channel is not None:
            channel.close()
        if collector is not None and collector.poll() is None:
            collector.send_signal(signal.SIGINT)
            try:
                result["collector_return_code"] = collector.wait(timeout=30.0)
            except subprocess.TimeoutExpired:
                collector.terminate()
                result["collector_return_code"] = collector.wait(timeout=10.0)
                result["collector_forced_terminate"] = True
        collector_log.close()
        result["ended"] = now()
        (args.out_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(json.dumps({
        "status": result["status"],
        "machine": result.get("machine"),
        "result": str(args.out_dir / "result.json"),
    }, indent=2, sort_keys=True), flush=True)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
