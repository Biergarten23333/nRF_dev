#!/usr/bin/env python3
"""Autonomous Batch-G G2b/G3/endurance runner.

This program is launched only after the operator's literal power-on token.
All Fusion-node commands and records use the Fusion Master CDC plane.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from batch_g_overnight_core import (
    AliveBook,
    CfgTiming,
    SnapshotHealth,
    active_cfg,
    composed_idle_cfg,
    tag_domain_rate_hz,
    u32_delta,
)
from capacity_ramp import RecordingAssembler, b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import (
    FusionController,
    LineChannel,
    SessionError,
    parse_fields,
    parse_reply,
    resolve_fusion_port,
)
from pre_ramp_hardening import request_list


MASTER_MARKER = "dk-fusion-imu-relay-v27"
MAIN_SNR = "760184545"
SUB_SNR = "760181725"
MAIN_MARKER = "listener-beacon-main-v6.1"
SUB_MARKER = "listener-beacon-sub-v10.2"
NODES = (
    "BSF3C79",
    "BSFC2CC",
    "BSF44AD",
    "BSF6C53",
    "BSF8BC4",
    "BSF1120",
    "BSF31CC",
    "BSFAA61",
    "BSFB165",
    "BSFEC35",
)
TAG_NUMBER = {node: index for index, node in enumerate(NODES, 1)}
INITIAL_SLOTS = {node: index for index, node in enumerate(NODES, 1)}
LEDGER_FIELDS = (
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
RATE_GATE_HZ = 0.99 * (1000.0 / 110.0)


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="milliseconds"
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class ObservedLineChannel(LineChannel):
    """LineChannel that exposes every decoded record to run bookkeeping."""

    def __init__(self, *args, observer, **kwargs):
        self.observer = observer
        super().__init__(*args, **kwargs)

    def read(self, deadline: float) -> str | None:
        line = super().read(deadline)
        if line is not None:
            self.observer(line)
        return line


class Runner:
    def __init__(
        self,
        root: Path,
        fusion_port: str | None,
        hard_stop_hours: float,
    ) -> None:
        self.root = root
        self.fusion_port = fusion_port
        self.hard_stop_hours = hard_stop_hours
        self.channel: LineChannel | None = None
        self.raw = None
        self.started_monotonic = time.monotonic()
        self.hard_deadline = (
            self.started_monotonic + hard_stop_hours * 3600.0
        )
        self.stop_requested = False
        self.cfg_timings: list[CfgTiming] = []
        self.snapshots: list[dict[str, object]] = []
        self.health = {node: SnapshotHealth() for node in NODES}
        self.alive = AliveBook(NODES)
        self.slot_map = dict(INITIAL_SLOTS)
        self.win_map = {node: 1 for node in NODES}
        self.period_us = 100_000
        self.phases: dict[str, object] = {}
        self.imu_started: set[str] = set()
        self.latest_sweep: dict[str, int] = {}
        self.latest_ledger: dict[str, dict[str, int]] = {}
        self.disconnect_lines: list[str] = []
        self.last_line_monotonic = time.monotonic()
        self.master_reader_restarts = 0
        self.cleanup_started = False
        self.summary: dict[str, object] = {
            "status": "IN_PROGRESS",
            "started": utc_now(),
            "hard_stop_hours": hard_stop_hours,
            "nodes": NODES,
            "excluded": (
                "OTA",
                "J-Link",
                "firmware builds",
                "Tag Master",
                "54L15",
                "anchor role changes",
                "GUARD changes",
                "RESP_SPACING changes",
                "DW-domain anchoring enablement",
            ),
        }

    def checkpoint(self) -> None:
        self.summary.update(
            {
                "updated": utc_now(),
                "period_us": self.period_us,
                "slot_map": self.slot_map,
                "win_map": self.win_map,
                "cfg_timings": [row.as_dict() for row in self.cfg_timings],
                "alive_epochs": self.alive.snapshot(),
                "snapshot_count": len(self.snapshots),
                "phases": self.phases,
            }
        )
        write_json(self.root / "run_state.json", self.summary)
        write_json(self.root / "snapshots.json", self.snapshots)

    def open(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw = (self.root / "fusion_cdc.log").open(
            "x", encoding="utf-8", buffering=1
        )
        self.channel = ObservedLineChannel(
            resolve_fusion_port(self.fusion_port),
            self.raw,
            "FUSION",
            observer=self.observe_line,
        )
        self.channel.transport_mode = "binary"
        self.channel.text_pending.clear()
        self.summary["fusion_port"] = self.channel.port
        self.summary["decode_before_send"] = decode_guard(
            self.channel, 15.0
        )
        self.channel.send("MASTER STATUS")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            line = self.channel.read(deadline)
            if line and line.startswith("FUSION_MASTER_STATUS "):
                if parse_fields(line).get("marker") != MASTER_MARKER:
                    raise SessionError(f"master marker mismatch: {line}")
                self.summary["master_status"] = line
                break
        else:
            raise SessionError("missing Fusion Master status")

    def observe_line(self, line: str) -> None:
        fields = parse_fields(line)
        node = fields.get("name")
        now = time.monotonic()
        self.last_line_monotonic = now
        if node in NODES:
            self.alive.seen(node, now)
        if line.startswith("FUSION_UWB ") and node in NODES:
            self.latest_sweep[node] = int(fields["sweep"], 0)
        elif line.startswith("FUSION_TELEMETRY ") and node in NODES:
            latest = self.latest_ledger.setdefault(node, {})
            latest.update(
                {
                    key: int(fields[key], 0)
                    for key in LEDGER_FIELDS
                    if key in fields
                }
            )
        elif line.startswith("FUSION_DISCONNECTED "):
            self.disconnect_lines.append(line)

    def list_peers(self) -> dict[str, object]:
        assert self.channel is not None
        assembler = RecordingAssembler()
        counters: dict[str, int] = {}
        listing = request_list(self.channel, assembler, counters, NODES)
        aggregate = listing["aggregate"]
        if (
            aggregate.get("spacing") != "ON"
            or aggregate.get("spacing_us") != "5000"
        ):
            raise SessionError(f"5 ms spacing gate failed: {aggregate}")
        now = time.monotonic()
        connected = {
            node
            for node, row in listing["peers"].items()
            if row.get("connected") == "1" and row.get("subscribed") == "1"
        }
        for node in NODES:
            if node in connected:
                self.alive.connected(
                    node, now, self.latest_ledger.get(node)
                )
            elif self.alive.is_alive(node):
                # The central reconnects automatically.  Three bounded LIST
                # polls are the reconnect observation; there is no retry storm.
                recovered = False
                for _ in range(3):
                    time.sleep(5.0)
                    retry = request_list(
                        self.channel, RecordingAssembler(), {}, NODES
                    )
                    row = retry["peers"].get(node, {})
                    if (
                        row.get("connected") == "1"
                        and row.get("subscribed") == "1"
                    ):
                        recovered = True
                        listing = retry
                        break
                if not recovered:
                    self.alive.disconnected(
                        node,
                        time.monotonic(),
                        "BLE drop / presumed battery",
                        self.latest_ledger.get(node),
                    )
        return listing

    def send_tag_cfg(self, node: str, command: str) -> dict[str, object]:
        """Dispatch exactly once, then wait patiently for correlated completion."""
        assert self.channel is not None
        if "BEACON_SYNC=" not in command or "DW_ANCHOR=0" not in command:
            raise SessionError(f"unsafe implicit CFG refused: {command}")
        timing = CfgTiming(
            node=node,
            command=command,
            dispatched_monotonic=time.monotonic(),
        )
        self.cfg_timings.append(timing)
        controller = FusionController(
            self.channel, node, timeout_s=8.0, max_attempts=3
        )
        try:
            queued = controller.command(
                f"TAG RAW {command}",
                lambda text: text.startswith("RELAY_QUEUED"),
                source="B306",
                allow_resend_after_tx=False,
            )
        except Exception as exc:
            timing.completed_monotonic = time.monotonic()
            timing.completion = "dispatch_uncertain_behavior_required"
            timing.reply = f"{type(exc).__name__}: {exc}"
            self.checkpoint()
            return timing.as_dict()
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            line = self.channel.read(deadline)
            if line is None:
                continue
            reply = parse_reply(line)
            if (
                reply is None
                or parse_fields(line).get("name") != node
                or reply.source != "TAG"
                or reply.correlation != queued.correlation
            ):
                continue
            timing.completed_monotonic = time.monotonic()
            timing.reply = reply.text
            if reply.text.startswith("CFG_OK ") and "LIVE=1" in reply.text:
                timing.completion = "cfg_ok"
                self.checkpoint()
                return timing.as_dict()
            if reply.text == "TIMEOUT":
                timing.completion = "tag_timeout_pending_behavior"
                continue
            timing.completion = "unexpected_reply"
            self.checkpoint()
            return timing.as_dict()
        timing.completed_monotonic = time.monotonic()
        timing.completion = "no_cfg_ok_behavior_required"
        self.checkpoint()
        return timing.as_dict()

    def set_main_period(self, period_ms: int, label: str) -> dict[str, object]:
        if period_ms not in (100, 110, 120):
            raise SessionError("refusing non-authorized beacon period")
        output = self.root / f"{label}_main_period_{period_ms}.json"
        tool = Path(__file__).with_name("listener_vcom_command.py")
        completed = subprocess.run(
            (
                sys.executable,
                str(tool),
                "--snr",
                MAIN_SNR,
                "--expected-marker",
                MAIN_MARKER,
                "--command",
                f"BEACON_PERIOD {period_ms}",
                "--output",
                str(output),
                "--post-seconds",
                "8",
            ),
            check=False,
            text=True,
            capture_output=True,
        )
        decoded = None
        if output.exists():
            try:
                decoded = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                decoded = None
        result = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "evidence": str(output),
            "decoded": decoded,
            f"period_{period_ms}_seen": bool(
                decoded and decoded.get(f"period_{period_ms}_seen")
            ),
        }
        if completed.returncode != 0:
            raise SessionError(f"main period command failed: {result}")
        self.period_us = period_ms * 1000
        return result

    def listener_status(
        self, snr: str, marker: str, label: str
    ) -> dict[str, object]:
        output = self.root / f"{label}_{snr}.json"
        tool = Path(__file__).with_name("listener_vcom_command.py")
        completed = subprocess.run(
            (
                sys.executable,
                str(tool),
                "--snr",
                snr,
                "--expected-marker",
                marker,
                "--command",
                "BEACON_STATUS",
                "--output",
                str(output),
                "--post-seconds",
                "3",
            ),
            check=False,
            text=True,
            capture_output=True,
        )
        decoded = None
        if output.exists():
            try:
                decoded = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                decoded = None
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "evidence": str(output),
            "decoded": decoded,
        }

    def snapshot(self, label: str) -> dict[str, object]:
        assert self.channel is not None
        listing = self.list_peers()
        connected = set(listing["peers"])
        statuses: dict[str, object] = {}
        for node in NODES:
            if node not in connected or not self.alive.is_alive(node):
                self.health[node].observe(False, False)
                continue
            reply_received = False
            for attempt in range(1, 3):
                try:
                    controller = FusionController(
                        self.channel, node, timeout_s=8.0, max_attempts=2
                    )
                    queued = controller.command(
                        "TAG RAW BEACON_STATUS",
                        lambda text: text.startswith("RELAY_QUEUED"),
                        source="B306",
                        allow_resend_after_tx=False,
                    )
                    line = controller.read_until(
                        lambda row: (
                            (reply := parse_reply(row)) is not None
                            and parse_fields(row).get("name") == node
                            and reply.source == "TAG"
                            and reply.correlation == queued.correlation
                            and reply.text.startswith("BEACON ")
                        ),
                        8.0,
                        f"{node} BEACON_STATUS",
                    )
                    reply = parse_reply(line)
                    assert reply is not None
                    statuses[node] = {
                        "attempt": attempt,
                        "text": reply.text,
                        "fields": parse_fields(reply.text),
                        "sweep_at_reply": self.latest_sweep.get(node),
                    }
                    reply_received = True
                    break
                except SessionError as exc:
                    statuses[node] = {
                        "attempt": attempt,
                        "error": str(exc),
                    }
            self.health[node].observe(reply_received, True)
            statuses[node]["degraded"] = self.health[node].degraded
        self.channel.send("LEDSTAT")
        ledstat = None
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            line = self.channel.read(deadline)
            if line and line.startswith("LEDSTAT "):
                ledstat = line
                break
        snapshot = {
            "label": label,
            "utc": utc_now(),
            "monotonic": time.monotonic(),
            "period_us": self.period_us,
            "list": listing,
            "beacon_status": statuses,
            "ledstat": ledstat,
            "main": self.listener_status(
                MAIN_SNR, MAIN_MARKER, f"{label}_main"
            ),
            "sub": self.listener_status(
                SUB_SNR, SUB_MARKER, f"{label}_sub"
            ),
        }
        self.snapshots.append(snapshot)
        self.checkpoint()
        return snapshot

    def capture(self, label: str, duration_s: float) -> dict[str, object]:
        assert self.channel is not None
        first_sweep: dict[str, int] = {}
        last_sweep: dict[str, int] = {}
        previous_sweep: dict[str, int] = {}
        sweep_missing: Counter[str] = Counter()
        sweep_duplicates: Counter[str] = Counter()
        sweep_reorders: Counter[str] = Counter()
        records: Counter[str] = Counter()
        imu_samples: Counter[str] = Counter()
        first_ledger: dict[str, dict[str, int]] = {}
        last_ledger: dict[str, dict[str, int]] = {}
        disconnects: list[str] = []
        malformed: list[str] = []
        decoder_before = self.channel.binary_decoder.errors
        started = time.monotonic()
        deadline = started + duration_s
        while time.monotonic() < deadline and not self.stop_requested:
            line = self.channel.read(
                min(deadline, time.monotonic() + 0.5)
            )
            if line is None:
                if time.monotonic() - self.last_line_monotonic > 120.0:
                    if self.master_reader_restarts >= 1:
                        raise SessionError(
                            "Fusion Master CDC silent after one reader restart"
                        )
                    self.channel.reopen(timeout_s=20.0)
                    self.channel.transport_mode = "binary"
                    self.channel.text_pending.clear()
                    self.master_reader_restarts += 1
                    self.last_line_monotonic = time.monotonic()
                continue
            fields = parse_fields(line)
            node = fields.get("name")
            if node in NODES:
                self.alive.seen(node, time.monotonic())
            if line.startswith("FUSION_UWB ") and node in NODES:
                sweep = int(fields["sweep"], 0)
                first_sweep.setdefault(node, sweep)
                last_sweep[node] = sweep
                records[node] += 1
                if node in previous_sweep:
                    delta = u32_delta(previous_sweep[node], sweep)
                    if delta == 0:
                        sweep_duplicates[node] += 1
                    elif delta > 0x80000000:
                        sweep_reorders[node] += 1
                    elif delta > 1:
                        sweep_missing[node] += delta - 1
                previous_sweep[node] = sweep
            elif line.startswith("FUSION_IMU ") and node in NODES:
                imu_samples[node] += int(fields.get("n", "0"), 0)
            elif line.startswith("FUSION_TELEMETRY ") and node in NODES:
                row = {
                    key: int(fields[key], 0)
                    for key in LEDGER_FIELDS
                    if key in fields
                }
                first_ledger.setdefault(node, {}).update(
                    {
                        key: value
                        for key, value in row.items()
                        if key not in first_ledger.get(node, {})
                    }
                )
                last_ledger.setdefault(node, {}).update(row)
            elif line.startswith("FUSION_DISCONNECTED "):
                disconnects.append(line)
            elif line.startswith("FUSION_MALFORMED "):
                malformed.append(line)
        ended = time.monotonic()
        ledger_deltas = {
            node: {
                key: u32_delta(value, last_ledger[node][key])
                for key, value in first_ledger.get(node, {}).items()
                if key in last_ledger.get(node, {})
            }
            for node in NODES
        }
        result = {
            "label": label,
            "started_monotonic": started,
            "ended_monotonic": ended,
            "duration_s": ended - started,
            "first_sweep": first_sweep,
            "last_sweep": last_sweep,
            "records": dict(records),
            "sweep_missing": dict(sweep_missing),
            "sweep_duplicates": dict(sweep_duplicates),
            "sweep_reorders": dict(sweep_reorders),
            "imu_samples": dict(imu_samples),
            "ledger_deltas": ledger_deltas,
            "disconnects": disconnects,
            "malformed": malformed,
            "decoder_errors": (
                self.channel.binary_decoder.errors - decoder_before
            ),
        }
        write_json(self.root / f"{label}_capture.json", result)
        return result

    def analyze_window(
        self,
        label: str,
        before: dict[str, object],
        capture: dict[str, object],
        after: dict[str, object],
    ) -> dict[str, object]:
        rows: dict[str, object] = {}
        for node in NODES:
            pre = before["beacon_status"].get(node, {})
            post = after["beacon_status"].get(node, {})
            first_sweep = pre.get("sweep_at_reply")
            last_sweep = post.get("sweep_at_reply")
            if (
                first_sweep is None
                or last_sweep is None
                or "fields" not in pre
                or "fields" not in post
            ):
                rows[node] = {"available": False}
                continue
            a = pre["fields"]
            b = post["fields"]
            rate = tag_domain_rate_hz(
                first_sweep,
                last_sweep,
                int(a["counter"], 0),
                int(b["counter"], 0),
                self.period_us,
            )
            rx_delta = u32_delta(int(a["rx"], 0), int(b["rx"], 0))
            miss_delta = u32_delta(
                int(a["miss"], 0), int(b["miss"], 0)
            )
            attempts = rx_delta + miss_delta
            rows[node] = {
                "available": True,
                "tag_domain_rate_hz": rate,
                "sweep_delta": u32_delta(first_sweep, last_sweep),
                "superframe_delta": u32_delta(
                    int(a["counter"], 0), int(b["counter"], 0)
                ),
                "rx_delta": rx_delta,
                "miss_delta": miss_delta,
                "window_miss_fraction": (
                    miss_delta / attempts if attempts else None
                ),
                "lock_before": a.get("lock"),
                "lock_after": b.get("lock"),
                "gen_before": a.get("gen"),
                "gen_after": b.get("gen"),
                "ledger_deltas": capture["ledger_deltas"].get(node, {}),
                "sweep_missing": capture["sweep_missing"].get(node, 0),
                "sweep_duplicates": capture["sweep_duplicates"].get(node, 0),
                "sweep_reorders": capture["sweep_reorders"].get(node, 0),
            }
        result = {
            "label": label,
            "period_us": self.period_us,
            "nodes": rows,
            "capture": capture,
            "field_status": {
                "main_before": before["main"],
                "main_after": after["main"],
                "sub_before": before["sub"],
                "sub_after": after["sub"],
            },
        }
        write_json(self.root / f"{label}_analysis.json", result)
        return result

    def measured_window(self, label: str, duration_s: float) -> dict[str, object]:
        before = self.snapshot(f"{label}_before")
        capture = self.capture(label, duration_s)
        after = self.snapshot(f"{label}_after")
        return self.analyze_window(label, before, capture, after)

    def configure_active(
        self,
        nodes: tuple[str, ...],
        *,
        slot_overrides: dict[str, int] | None = None,
        win_overrides: dict[str, int] | None = None,
    ) -> dict[str, object]:
        slot_overrides = slot_overrides or {}
        win_overrides = win_overrides or {}
        results: dict[str, object] = {}
        for node in nodes:
            slot = slot_overrides.get(node, self.slot_map[node])
            win = win_overrides.get(node, self.win_map[node])
            results[node] = self.send_tag_cfg(
                node,
                active_cfg(
                    TAG_NUMBER[node], slot, count=11, beacon_win_n=win
                ),
            )
            self.slot_map[node] = slot
            self.win_map[node] = win
        return results

    def start_imu(self, nodes: tuple[str, ...]) -> dict[str, object]:
        assert self.channel is not None
        results: dict[str, object] = {}
        for node in nodes:
            try:
                rate = b306_command(
                    self.channel, node, "IMU RATE=200", "IMU RATE OK "
                )
                batch = b306_command(
                    self.channel, node, "IMU BATCH=5", "IMU BATCH OK "
                )
                start = b306_command(
                    self.channel, node, "IMU START", "IMU START OK "
                )
                required = (
                    "61=0001:P",
                    "03=000B:P",
                    "1F=0002:P",
                    "volatile=1",
                    "saved=0",
                )
                if any(token not in start["text"] for token in required):
                    raise SessionError(
                        f"{node} incomplete IMU START: {start['text']}"
                    )
                self.imu_started.add(node)
                results[node] = {
                    "status": "PASS",
                    "rate": rate,
                    "batch": batch,
                    "start": start,
                }
            except Exception as exc:
                results[node] = {
                    "status": "FAIL",
                    "error": f"{type(exc).__name__}: {exc}",
                }
        return results

    def stop_imu(self, node: str) -> dict[str, object]:
        assert self.channel is not None
        try:
            stop = b306_command(
                self.channel, node, "IMU STOP", "IMU STOP "
            )
            status = b306_command(
                self.channel, node, "IMU STATUS", "IMU "
            )
            ok = "active=0 " in f"{status['text']} "
            self.imu_started.discard(node)
            return {"ok": ok, "stop": stop, "status": status}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def cleanup(self, reason: str) -> dict[str, object]:
        if self.cleanup_started:
            return {"status": "already_started", "reason": reason}
        self.cleanup_started = True
        results: dict[str, object] = {
            "started": utc_now(),
            "reason": reason,
            "nodes": {},
        }
        if self.channel is None:
            results["status"] = "no_channel"
            return results
        survivors: list[str] = []
        for node in NODES:
            if not self.alive.is_alive(node):
                continue
            survivors.append(node)
            row: dict[str, object] = {"imu_stop": self.stop_imu(node)}
            try:
                row["idle_cfg"] = self.send_tag_cfg(
                    node,
                    composed_idle_cfg(
                        TAG_NUMBER[node],
                        self.slot_map[node],
                        11,
                    ),
                )
            except Exception as exc:
                row["idle_pass"] = False
                row["error"] = f"{type(exc).__name__}: {exc}"
            results["nodes"][node] = row
            self.checkpoint()
        # One common 90 s window is a full 90 s witness for every survivor
        # and avoids serially adding another 90 s per unit after dispatch.
        witness = self.capture("terminal_idle_witness_all", 90.0)
        for node in survivors:
            row = results["nodes"][node]
            row["idle_witness_records"] = witness["records"].get(node, 0)
            row["idle_pass"] = (
                row.get("idle_cfg") is not None
                and row["idle_witness_records"] <= 1
            )
        results["ended"] = utc_now()
        results["status"] = (
            "PASS"
            if all(row.get("idle_pass") for row in results["nodes"].values())
            else "PARTIAL"
        )
        write_json(self.root / "end_state.json", results)
        return results

    def run(self) -> None:
        self.open()
        initial = self.snapshot("initial")
        ready = initial["list"]["aggregate"].get("ready")
        if ready != "10":
            raise SessionError(f"initial fleet is not 10/10 ready: {ready}")

        self.phases["g2b_activate"] = self.configure_active(NODES)
        self.phases["g2b_period110"] = self.set_main_period(
            110, "g2b"
        )
        g2b1 = self.measured_window("g2b1_reproduce", 300.0)
        self.phases["g2b1"] = g2b1
        target = g2b1["nodes"]["BSFEC35"]
        reproduces = bool(
            target.get("available")
            and target["tag_domain_rate_hz"] < 8.8
            and target["window_miss_fraction"] is not None
            and target["window_miss_fraction"] >= 0.5
        )
        branch: dict[str, object] = {"reproduces": reproduces}

        if reproduces:
            slot5 = next(
                node for node, slot in self.slot_map.items() if slot == 5
            )
            swap = {"BSFEC35": 5, slot5: 10}
            self.phases["g2b2_cfg"] = self.configure_active(
                ("BSFEC35", slot5), slot_overrides=swap
            )
            g2b2 = self.measured_window("g2b2_swap", 600.0)
            self.phases["g2b2"] = g2b2
            ec = g2b2["nodes"]["BSFEC35"].get(
                "tag_domain_rate_hz", 0.0
            )
            other = g2b2["nodes"][slot5].get("tag_domain_rate_hz", 0.0)
            if other < 9.0 and ec >= 9.0:
                attribution = "structural_last_slot"
            elif ec < 9.0 and other >= 9.0:
                attribution = "unit_specific_BSFEC35"
            elif ec < 9.0 and other < 9.0:
                attribution = "interaction_both_degraded"
            else:
                attribution = "neither_degraded"
            branch.update(
                {
                    "slot5_original": slot5,
                    "attribution": attribution,
                    "bsfec35_hz": ec,
                    "slot10_occupant_hz": other,
                }
            )
            self.phases["g2b3_cfg"] = self.configure_active(
                (slot5,), win_overrides={slot5: 3}
            )
            g2b3 = self.measured_window("g2b3_win3", 600.0)
            self.phases["g2b3"] = g2b3
            slot10_rate = g2b3["nodes"][slot5].get(
                "tag_domain_rate_hz", 0.0
            )
            branch["mitigated_slot10_node"] = slot5
            branch["mitigated_slot10_rate_hz"] = slot10_rate
            g3_allowed = slot10_rate >= 9.0
            measured_not_gated = (
                "BSFEC35"
                if attribution == "unit_specific_BSFEC35"
                else None
            )
        else:
            branch["attribution"] = "draw_or_session_dependent"
            g3_allowed = True
            measured_not_gated = "BSFEC35"
        self.phases["g2b_branch"] = branch
        self.checkpoint()

        current = self.list_peers()
        alive_nodes = tuple(
            node
            for node in NODES
            if node in current["peers"] and self.alive.is_alive(node)
        )
        if g3_allowed:
            label = "g3" if len(alive_nodes) == 10 else "g3_prime"
            g3 = self.measured_window(label, 1800.0)
            strict = [
                node for node in alive_nodes if node != measured_not_gated
            ]
            node_gates = {
                node: (
                    g3["nodes"][node].get("available", False)
                    and g3["nodes"][node]["tag_domain_rate_hz"]
                    >= RATE_GATE_HZ
                    and g3["nodes"][node]["sweep_missing"] == 0
                    and g3["nodes"][node]["sweep_duplicates"] == 0
                    and g3["nodes"][node]["sweep_reorders"] == 0
                    and g3["nodes"][node]["lock_before"] == "1"
                    and g3["nodes"][node]["lock_after"] == "1"
                    and g3["nodes"][node]["gen_before"]
                    == g3["nodes"][node]["gen_after"]
                    and all(
                        key in g3["nodes"][node]["ledger_deltas"]
                        for key in (
                            "duplicate",
                            "reorder",
                            "drop_err",
                            "ring_drop",
                            "crc",
                            "header",
                        )
                    )
                    and all(
                        value == 0
                        for key, value in g3["nodes"][node][
                            "ledger_deltas"
                        ].items()
                        if key in (
                            "duplicate",
                            "reorder",
                            "drop_err",
                            "ring_drop",
                            "crc",
                            "header",
                        )
                    )
                )
                for node in strict
            }
            sub_rows = []
            for key in ("sub_before", "sub_after"):
                decoded = (
                    g3["field_status"].get(key, {}).get("decoded") or {}
                )
                sub_rows.extend(decoded.get("post_lines", []))
            sub_slaved = (
                bool(sub_rows)
                and any(";SLAVED;" in line for line in sub_rows)
                and not any(line.startswith("LBTX;") for line in sub_rows)
            )
            self.phases["g3_verdict"] = {
                "label": label,
                "official_ten_node": len(alive_nodes) == 10,
                "measured_not_gated": measured_not_gated,
                "node_gates": node_gates,
                "sub_slaved": sub_slaved,
                "decoder_errors": g3["capture"]["decoder_errors"],
                "malformed": g3["capture"]["malformed"],
                "disconnects": g3["capture"]["disconnects"],
                "pass": (
                    all(node_gates.values())
                    and sub_slaved
                    and g3["capture"]["decoder_errors"] == 0
                    and not g3["capture"]["malformed"]
                ),
                "analysis": g3,
            }
        else:
            self.phases["g3_verdict"] = {
                "label": "G3_NOT_RUN",
                "reason": "G2b did not produce an acceptable slot-10 rate",
            }
        self.checkpoint()

        listing = self.list_peers()
        alive_nodes = tuple(
            node
            for node in NODES
            if node in listing["peers"] and self.alive.is_alive(node)
        )
        self.phases["marathon_imu_start"] = self.start_imu(alive_nodes)
        marathon_chunks: list[dict[str, object]] = []
        marathon_deadline = self.hard_deadline - 20.0 * 60.0
        while (
            time.monotonic() < marathon_deadline
            and alive_nodes
            and not self.stop_requested
        ):
            remaining = marathon_deadline - time.monotonic()
            chunk = self.capture(
                f"marathon_{len(marathon_chunks):03d}",
                min(300.0, remaining),
            )
            snap = self.snapshot(
                f"marathon_snapshot_{len(marathon_chunks):03d}"
            )
            alive_nodes = tuple(
                node
                for node in NODES
                if node in snap["list"]["peers"] and self.alive.is_alive(node)
            )
            marathon_chunks.append(
                {
                    "capture": chunk,
                    "snapshot_index": len(self.snapshots) - 1,
                    "alive": alive_nodes,
                }
            )
            self.phases["marathon"] = marathon_chunks
            self.checkpoint()

        self.phases["cleanup"] = self.cleanup("normal terminal")
        self.summary["status"] = "COMPLETE"
        self.summary["ended"] = utc_now()
        self.checkpoint()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--hard-stop-hours", type=float, default=8.0)
    args = parser.parse_args()
    if args.hard_stop_hours <= 0:
        parser.error("--hard-stop-hours must be positive")
    runner = Runner(
        args.evidence_root, args.fusion_port, args.hard_stop_hours
    )

    def request_stop(_signum, _frame) -> None:
        runner.stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        runner.run()
        return 0
    except Exception as exc:
        runner.summary["status"] = "FAILED"
        runner.summary["error"] = f"{type(exc).__name__}: {exc}"
        try:
            runner.phases["cleanup"] = runner.cleanup(
                "exception terminal"
            )
        except Exception as cleanup_exc:
            runner.summary["cleanup_error"] = (
                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            )
        runner.checkpoint()
        return 2
    finally:
        if runner.channel is not None:
            runner.channel.close()
        if runner.raw is not None:
            runner.raw.close()


if __name__ == "__main__":
    raise SystemExit(main())
