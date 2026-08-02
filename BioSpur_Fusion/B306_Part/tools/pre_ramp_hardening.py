#!/usr/bin/env python3
"""Five-peer resource snapshot, alias check, and connection-stability soak."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from fusion_session import (
    DEFAULT_LOG_ROOT,
    LineChannel,
    SessionError,
    parse_fields,
    resolve_fusion_port,
    u32_delta,
)


DEFAULT_PEERS = ("BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4")


class TelemetryAssembler:
    def __init__(self) -> None:
        self.pending: dict[str, tuple[int, set[int], dict[str, str]]] = {}
        self.latest: dict[str, dict[str, str]] = {}
        self.completed_records = 0

    def observe(self, line: str) -> None:
        if not line.startswith("FUSION_TELEMETRY "):
            return
        fields = parse_fields(line)
        name = fields.get("name")
        if name is None:
            return
        part = fields.get("part")
        if part is None:
            self.latest[name] = fields
            self.completed_records += 1
            return
        try:
            index_text, count_text = part.split("/", 1)
            index = int(index_text)
            count = int(count_text)
        except (ValueError, TypeError):
            return
        record = fields.get("record")
        if record is None or not (1 <= index <= count):
            return
        current = self.pending.get(record)
        if current is None or current[0] != count:
            seen: set[int] = set()
            merged: dict[str, str] = {}
        else:
            _, seen, merged = current
        seen.add(index)
        merged.update(fields)
        merged.pop("part", None)
        merged["parts"] = str(count)
        self.pending[record] = (count, seen, merged)
        if len(seen) == count:
            self.latest[name] = dict(merged)
            self.completed_records += 1
            del self.pending[record]
        while len(self.pending) > 32:
            del self.pending[next(iter(self.pending))]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def collect_for(
    channel: LineChannel,
    duration_s: float,
    assembler: TelemetryAssembler,
    counters: dict[str, int],
    retained: list[str] | None = None,
) -> None:
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is None:
            continue
        assembler.observe(line)
        prefix = line.split(" ", 1)[0]
        counters[prefix] = counters.get(prefix, 0) + 1
        if line.count("FUSION_") > 1:
            counters["FUSION_BOUNDARY_CORRUPT"] = (
                counters.get("FUSION_BOUNDARY_CORRUPT", 0) + 1
            )
        if retained is not None:
            retained.append(line)


def wait_for_telemetry(
    channel: LineChannel,
    assembler: TelemetryAssembler,
    expected: tuple[str, ...],
    counters: dict[str, int],
    timeout_s: float = 8.0,
) -> dict[str, dict[str, str]]:
    previous_node_ms = {
        name: int(assembler.latest.get(name, {}).get("node_ms", "-1"), 0)
        for name in expected
    }
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        collect_for(
            channel,
            min(0.5, deadline - time.monotonic()),
            assembler,
            counters,
        )
        if all(
            name in assembler.latest
            and int(assembler.latest[name].get("node_ms", "-1"), 0)
            > previous_node_ms[name]
            for name in expected
        ):
            return {name: dict(assembler.latest[name]) for name in expected}
    missing = [name for name in expected if name not in assembler.latest]
    raise SessionError(f"missing complete split telemetry for {missing}")


def request_list(
    channel: LineChannel,
    assembler: TelemetryAssembler,
    counters: dict[str, int],
    expected: tuple[str, ...],
) -> dict:
    lines: list[str] = []
    channel.send("LIST")
    collect_for(channel, 3.0, assembler, counters, lines)
    list_rows = [
        parse_fields(line) for line in lines if line.startswith("FUSION_LIST ")
    ]
    peer_rows = {
        fields["name"]: fields
        for line in lines
        if line.startswith("FUSION_PEER ")
        and (fields := parse_fields(line)).get("name") in expected
    }
    if not list_rows:
        raise SessionError("LIST produced no FUSION_LIST row")
    return {"aggregate": list_rows[-1], "peers": peer_rows}


def request_resources(
    channel: LineChannel,
    assembler: TelemetryAssembler,
    counters: dict[str, int],
) -> dict:
    lines: list[str] = []
    channel.send("RESOURCES")
    collect_for(channel, 3.0, assembler, counters, lines)
    summaries = [
        parse_fields(line)
        for line in lines
        if line.startswith("FUSION_RESOURCE_SUMMARY ")
    ]
    pools = [
        parse_fields(line)
        for line in lines
        if line.startswith("FUSION_RESOURCE_POOL ")
    ]
    stacks = [
        parse_fields(line)
        for line in lines
        if line.startswith("FUSION_STACK ")
    ]
    if not summaries:
        raise SessionError("RESOURCES produced no summary")
    return {"summary": summaries[-1], "pools": pools, "stacks": stacks}


def counter_delta(
    before: dict[str, str], after: dict[str, str], field: str
) -> int:
    if field not in before or field not in after:
        raise SessionError(f"missing telemetry counter {field}")
    return u32_delta(int(before[field], 0), int(after[field], 0))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port")
    parser.add_argument("--duration-s", type=float, default=1800.0)
    parser.add_argument(
        "--peers",
        default=",".join(DEFAULT_PEERS),
        help="comma-separated expected BSF identities",
    )
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    expected = tuple(item.strip() for item in args.peers.split(",") if item)
    if len(expected) != 5 or len(set(expected)) != 5:
        raise SessionError("exactly five distinct peers are required")
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = DEFAULT_LOG_ROOT / f"pre_ramp_hardening_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=False)

    raw_path = output_dir / "raw.log"
    summary_path = output_dir / "summary.json"
    counters: dict[str, int] = {}
    assembler = TelemetryAssembler()
    port = resolve_fusion_port(args.port)
    start_utc = utc_now()

    with raw_path.open("w", encoding="utf-8") as raw:
        channel = LineChannel(port, raw, "FUSION")
        try:
            collect_for(channel, 2.0, assembler, counters)
            start_list = request_list(channel, assembler, counters, expected)
            if (
                start_list["aggregate"].get("count") != "5"
                or start_list["aggregate"].get("ready") != "5"
                or set(start_list["peers"]) != set(expected)
            ):
                raise SessionError(f"five-peer bridge not ready: {start_list}")

            start_resources = request_resources(channel, assembler, counters)
            before_alias = wait_for_telemetry(
                channel, assembler, expected, counters
            )
            channel.send(f"{expected[0]} STATUS")
            collect_for(channel, 3.0, assembler, counters)
            after_alias = wait_for_telemetry(
                channel, assembler, expected, counters
            )
            alias_deltas = {
                name: counter_delta(
                    before_alias[name], after_alias[name], "ctrl_rx"
                )
                for name in expected
            }
            alias_pass = (
                alias_deltas[expected[0]] >= 1
                and all(alias_deltas[name] == 0 for name in expected[1:])
            )

            baseline = {
                name: dict(after_alias[name]) for name in expected
            }
            soak_start = time.monotonic()
            collect_for(
                channel, args.duration_s, assembler, counters
            )
            soak_elapsed = time.monotonic() - soak_start
            final = wait_for_telemetry(
                channel, assembler, expected, counters
            )
            end_list = request_list(channel, assembler, counters, expected)
            end_resources = request_resources(channel, assembler, counters)
        finally:
            channel.close()

    anomaly_fields = ("malformed", "logger_drop")
    anomaly_deltas = {
        name: {
            field: counter_delta(baseline[name], final[name], field)
            for field in anomaly_fields
        }
        for name in expected
    }
    parameter_fields = (
        "interval_units",
        "latency",
        "timeout_units",
        "phy_tx",
        "phy_rx",
    )
    parameters_unchanged = all(
        all(
            start_list["peers"][name].get(field)
            == end_list["peers"].get(name, {}).get(field)
            for field in parameter_fields
        )
        for name in expected
    )
    imu_stopped = all(
        baseline[name].get("imu_active") == "0"
        and final[name].get("imu_active") == "0"
        for name in expected
    )
    cdc_drop_unchanged = all(
        start_resources["summary"].get(field)
        == end_resources["summary"].get(field)
        for field in ("cdc_drop_bytes", "cdc_drop_records")
    )
    gates = {
        "ready_five_start": start_list["aggregate"].get("ready") == "5",
        "ready_five_end": end_list["aggregate"].get("ready") == "5",
        "zero_disconnect_lines": counters.get("FUSION_DISCONNECTED", 0) == 0,
        "zero_malformed_lines": counters.get("FUSION_MALFORMED", 0) == 0,
        "zero_boundary_corruption": counters.get(
            "FUSION_BOUNDARY_CORRUPT", 0
        )
        == 0,
        "zero_cdc_drop_delta": cdc_drop_unchanged,
        "zero_malformed_counter_delta": all(
            row["malformed"] == 0 for row in anomaly_deltas.values()
        ),
        "zero_logger_drop_delta": all(
            row["logger_drop"] == 0 for row in anomaly_deltas.values()
        ),
        "parameters_unchanged": parameters_unchanged,
        "imu_stopped": imu_stopped,
        "per_peer_counter_isolation": alias_pass,
        "split_telemetry_complete": all(
            final[name].get("parts") == "2" for name in expected
        ),
    }
    summary = {
        "start_utc": start_utc,
        "end_utc": utc_now(),
        "port": port,
        "expected_peers": expected,
        "duration_requested_s": args.duration_s,
        "duration_measured_s": soak_elapsed,
        "start_list": start_list,
        "end_list": end_list,
        "start_resources": start_resources,
        "end_resources": end_resources,
        "alias_target": expected[0],
        "alias_ctrl_rx_deltas": alias_deltas,
        "telemetry_counter_deltas": anomaly_deltas,
        "line_counts": counters,
        "gates": gates,
        "pass": all(gates.values()),
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SessionError, OSError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
