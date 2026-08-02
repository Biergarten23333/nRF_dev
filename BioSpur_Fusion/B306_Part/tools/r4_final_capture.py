#!/usr/bin/env python3
"""Arm, capture, and safely terminate the token-gated R4 final window.

This runner deliberately does not analyze the formal window.  It records the
raw Fusion plane and the seven passive listener streams, performs the mandated
terminal cleanup, and leaves verdict work for the later ANALYZE token.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import batch_g_stallfix as stallfix
from batch_g_day_h3 import SLOT10, SLOT_MAP, lbstat_counters, listener_state
from batch_g_overnight import (
    LEDGER_FIELDS,
    MAIN_MARKER,
    MAIN_SNR,
    NODES,
    SUB_MARKER,
    SUB_SNR,
    TAG_NUMBER,
    active_cfg,
    u32_delta,
    utc_now,
)
from batch_g_overnight_core import CfgTiming, composed_idle_cfg
from capacity_ramp import b306_command
from fusion_session import FusionController, SessionError, parse_fields, parse_reply
from listener_array_run import wait_listener_preflight


MASTER_MARKER = "dk-fusion-imu-relay-v29"
BEHAVIORAL_CFG_NODES = frozenset(NODES)
BEHAVIORAL_CFG_NOTE = (
    "config proven behaviorally (command-reply path degraded)"
)
COLLECTOR = Path(__file__).resolve().parents[1] / "host" / "listener_array_collector.py"
SLOT10_WAIVER = (
    "slot-10 rate deficit is a documented relay7 limitation "
    "(structural last-slot geometry; fix scheduled for relay8); "
    "nine of ten gated."
)

# StallfixRunner.open() resolves this module global at call time.
stallfix.MASTER_MARKER = MASTER_MARKER


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class R4Runner(stallfix.StallfixRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.imu_batch_values: dict[str, set[int]] = {
            node: set() for node in NODES
        }
        self.formal_last_uwb: dict[str, float] = {}
        self.uwb_seen_count: Counter[str] = Counter()

    def observe_line(self, line: str) -> None:
        super().observe_line(line)
        fields = parse_fields(line)
        node = fields.get("name")
        if node not in NODES:
            return
        if line.startswith("FUSION_IMU ") and "n" in fields:
            self.imu_batch_values[node].add(int(fields["n"], 0))
        elif line.startswith("FUSION_UWB "):
            self.formal_last_uwb[node] = time.monotonic()
            self.uwb_seen_count[node] += 1

    def snapshot(self, label: str) -> dict[str, object]:
        """Snapshot without sending a doomed active-state tag query to BSF3C79."""
        assert self.channel is not None
        listing = self.list_peers()
        connected = set(listing["peers"])
        statuses: dict[str, object] = {}
        for node in NODES:
            if node not in connected or not self.alive.is_alive(node):
                self.health[node].observe(False, False)
                continue
            if node in BEHAVIORAL_CFG_NODES:
                available = node in self.latest_sweep
                self.health[node].observe(available, True)
                statuses[node] = {
                    "proof_method": "behavioral",
                    "annotation": BEHAVIORAL_CFG_NOTE,
                    "query_suppressed": (
                        "active-state command replies are pathologically delayed"
                    ),
                    "sweep_at_reply": self.latest_sweep.get(node),
                    "degraded": self.health[node].degraded,
                }
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
                    statuses[node] = {"attempt": attempt, "error": str(exc)}
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
            "main": self.listener_status(MAIN_SNR, MAIN_MARKER, f"{label}_main"),
            "sub": self.listener_status(SUB_SNR, SUB_MARKER, f"{label}_sub"),
        }
        self.snapshots.append(snapshot)
        progress: dict[str, object] = {}
        for node, status in statuses.items():
            sweep = status.get("sweep_at_reply")
            if sweep is None:
                continue
            previous = self.zero_progress_last.get(node)
            if previous is not None and int(sweep) == previous:
                self.zero_progress_misses[node] += 1
            else:
                self.zero_progress_misses[node] = 0
            self.zero_progress_last[node] = int(sweep)
            red = self.zero_progress_misses[node] >= 2
            progress[node] = {
                "sweep": int(sweep),
                "previous": previous,
                "consecutive_zero_progress": self.zero_progress_misses[node],
                "red": red,
            }
            if red:
                event = {
                    "utc": utc_now(),
                    "label": label,
                    "node": node,
                    "sweep": int(sweep),
                }
                self.zero_progress_red.append(event)
        snapshot["zero_progress"] = progress
        snapshot["host_drain"] = self.channel.health_snapshot()
        self.checkpoint()
        return snapshot

    def start_imu10(self) -> dict[str, object]:
        assert self.channel is not None
        results: dict[str, object] = {}
        for node in NODES:
            try:
                rate = b306_command(
                    self.channel, node, "IMU RATE=200", "IMU RATE OK "
                )
                batch = b306_command(
                    self.channel, node, "IMU BATCH=10", "IMU BATCH OK "
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

    def send_tag_cfg_echo(self, node: str, command: str) -> dict[str, object]:
        """Send once and return promptly on the tag's definitive TIMEOUT.

        The inherited behavior-first helper waits 90 seconds after TIMEOUT.
        The fleet behavioral-proof amendment makes the data-plane result the
        acceptance evidence, so a TIMEOUT is retained without resending.
        """
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
            timing.completion = "dispatch_uncertain"
            timing.reply = f"{type(exc).__name__}: {exc}"
            self.checkpoint()
            return timing.as_dict()
        deadline = time.monotonic() + 15.0
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
            elif reply.text == "TIMEOUT":
                timing.completion = "tag_timeout"
            else:
                timing.completion = "unexpected_reply"
            self.checkpoint()
            return timing.as_dict()
        timing.completed_monotonic = time.monotonic()
        timing.completion = "reply_timeout"
        self.checkpoint()
        return timing.as_dict()

    def send_tag_cfg_patient(
        self, node: str, command: str, *, deadline_s: float
    ) -> dict[str, object]:
        """One dispatch with a long wait for the original correlation's ACK."""
        assert self.channel is not None
        timing = CfgTiming(
            node=node,
            command=command,
            dispatched_monotonic=time.monotonic(),
        )
        self.cfg_timings.append(timing)
        controller = FusionController(
            self.channel, node, timeout_s=8.0, max_attempts=3
        )
        queued = controller.command(
            f"TAG RAW {command}",
            lambda text: text.startswith("RELAY_QUEUED"),
            source="B306",
            allow_resend_after_tx=False,
        )
        deadline = time.monotonic() + deadline_s
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
            timing.reply = reply.text
            if reply.text.startswith("CFG_OK ") and "LIVE=1" in reply.text:
                timing.completed_monotonic = time.monotonic()
                timing.completion = "cfg_ok"
                self.checkpoint()
                return timing.as_dict()
            if reply.text != "TIMEOUT":
                timing.completed_monotonic = time.monotonic()
                timing.completion = "unexpected_reply"
                self.checkpoint()
                return timing.as_dict()
            timing.completion = "tag_timeout_pending_late_ack"
        timing.completed_monotonic = time.monotonic()
        timing.completion = "no_cfg_ok_behavior_required"
        self.checkpoint()
        return timing.as_dict()

    def capture_formal(self, label: str, duration_s: float) -> dict[str, object]:
        """Raw capture plus an operational per-node zero-progress alarm."""
        assert self.channel is not None
        first_sweep: dict[str, int] = {}
        last_sweep: dict[str, int] = {}
        previous_sweep: dict[str, int] = {}
        sweep_missing: Counter[str] = Counter()
        sweep_duplicates: Counter[str] = Counter()
        sweep_reorders: Counter[str] = Counter()
        records: Counter[str] = Counter()
        imu_samples: Counter[str] = Counter()
        imu_records: Counter[str] = Counter()
        imu_n_values: dict[str, set[int]] = {node: set() for node in NODES}
        first_ledger: dict[str, dict[str, int]] = {}
        last_ledger: dict[str, dict[str, int]] = {}
        disconnects: list[str] = []
        malformed: list[str] = []
        decoder_before = self.channel.binary_decoder.errors
        started = time.monotonic()
        self.formal_last_uwb = {node: started for node in NODES}
        deadline = started + duration_s
        while time.monotonic() < deadline and not self.stop_requested:
            line = self.channel.read(min(deadline, time.monotonic() + 0.5))
            now = time.monotonic()
            stalled = [
                node
                for node in NODES
                if now - self.formal_last_uwb.get(node, started) > 30.0
            ]
            if stalled:
                event = {
                    "utc": utc_now(),
                    "label": label,
                    "nodes": stalled,
                    "threshold_s": 30.0,
                }
                self.zero_progress_red.append(event)
                raise SessionError(f"formal zero-progress RED: {stalled}")
            if line is None:
                if now - self.last_line_monotonic > 120.0:
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
                self.alive.seen(node, now)
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
                n = int(fields.get("n", "0"), 0)
                imu_records[node] += 1
                imu_samples[node] += n
                imu_n_values[node].add(n)
            elif line.startswith("FUSION_TELEMETRY ") and node in NODES:
                row = {
                    key: int(fields[key], 0)
                    for key in LEDGER_FIELDS
                    if key in fields
                }
                if node not in first_ledger:
                    first_ledger[node] = dict(row)
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
            "started_utc": utc_now(),
            "started_monotonic": started,
            "ended_monotonic": ended,
            "duration_s": ended - started,
            "first_sweep": first_sweep,
            "last_sweep": last_sweep,
            "records": dict(records),
            "sweep_missing": dict(sweep_missing),
            "sweep_duplicates": dict(sweep_duplicates),
            "sweep_reorders": dict(sweep_reorders),
            "imu_records": dict(imu_records),
            "imu_samples": dict(imu_samples),
            "imu_n_values": {
                node: sorted(values) for node, values in imu_n_values.items()
            },
            "ledger_deltas": ledger_deltas,
            "disconnects": disconnects,
            "malformed": malformed,
            "decoder_errors": self.channel.binary_decoder.errors - decoder_before,
            "host_drain_end": self.channel.health_snapshot(),
            "zero_progress_red": list(self.zero_progress_red),
        }
        write_json(self.root / f"{label}_capture.json", result)
        return result

    def cleanup(self, reason: str) -> dict[str, object]:
        """Stop all producers, then prove 90 s quiet from each last UWB record."""
        if self.cleanup_started:
            return {"status": "already_started", "reason": reason}
        self.cleanup_started = True
        results: dict[str, object] = {
            "started": utc_now(),
            "reason": reason,
            "witness_clock": "per-node last observed FUSION_UWB record",
            "nodes": {},
        }
        if self.channel is None:
            results["status"] = "no_channel"
            return results
        survivors = [node for node in NODES if self.alive.is_alive(node)]
        if set(survivors) != set(NODES):
            results["survivor_error"] = {
                "expected": list(NODES),
                "actual": survivors,
            }

        # IMU STOP is B306-local and must not wait behind the pathological
        # BSF3C79 tag-reply path.  Stop all ten first.
        for node in survivors:
            results["nodes"][node] = {"imu_stop": self.stop_imu(node)}

        # Dispatch all tag-idle requests first.  Active-from-idle reply
        # starvation is per tag; parallel dispatch lets the ten tags reach
        # cessation concurrently instead of serializing ten pathological ACKs.
        pending: dict[str, dict[str, object]] = {}
        for node in survivors:
            row = results["nodes"][node]
            command = composed_idle_cfg(
                TAG_NUMBER[node], self.slot_map[node], 11
            )
            timing = CfgTiming(
                node=node,
                command=command,
                dispatched_monotonic=time.monotonic(),
            )
            self.cfg_timings.append(timing)
            row["idle_dispatched_monotonic"] = timing.dispatched_monotonic
            row["uwb_count_at_idle_dispatch"] = self.uwb_seen_count[node]
            try:
                queued = FusionController(
                    self.channel, node, timeout_s=8.0, max_attempts=3
                ).command(
                    f"TAG RAW {command}",
                    lambda text: text.startswith("RELAY_QUEUED"),
                    source="B306",
                    allow_resend_after_tx=False,
                )
                pending[node] = {
                    "correlation": queued.correlation,
                    "timing": timing,
                    "timeouts": [],
                }
                row["idle_queued"] = queued.__dict__
            except Exception as exc:
                timing.completed_monotonic = time.monotonic()
                timing.completion = "dispatch_uncertain"
                timing.reply = f"{type(exc).__name__}: {exc}"
                row["idle_cfg"] = timing.as_dict()
                row["idle_error"] = timing.reply

        ack_deadline = time.monotonic() + 120.0
        while pending and time.monotonic() < ack_deadline:
            line = self.channel.read(ack_deadline)
            if line is None:
                continue
            reply = parse_reply(line)
            node = parse_fields(line).get("name")
            if reply is None or node not in pending or reply.source != "TAG":
                continue
            state = pending[node]
            if reply.correlation != state["correlation"]:
                continue
            timing = state["timing"]
            assert isinstance(timing, CfgTiming)
            if reply.text == "TIMEOUT":
                state["timeouts"].append(time.monotonic())
                timing.reply = reply.text
                timing.completion = "tag_timeout_pending_late_ack"
                continue
            timing.completed_monotonic = time.monotonic()
            timing.reply = reply.text
            if reply.text.startswith("CFG_OK ") and "LIVE=1" in reply.text:
                timing.completion = "cfg_ok"
            else:
                timing.completion = "unexpected_reply"
            row = results["nodes"][node]
            row["idle_cfg"] = timing.as_dict()
            row["tag_timeout_events"] = state["timeouts"]
            row["idle_reply_monotonic"] = timing.completed_monotonic
            row["uwb_count_at_idle_reply"] = self.uwb_seen_count[node]
            row["last_uwb_at_idle_reply"] = self.formal_last_uwb.get(node)
            del pending[node]
            self.checkpoint()

        for node, state in pending.items():
            timing = state["timing"]
            assert isinstance(timing, CfgTiming)
            timing.completed_monotonic = time.monotonic()
            timing.completion = "no_cfg_ok_behavior_required"
            row = results["nodes"][node]
            row["idle_cfg"] = timing.as_dict()
            row["tag_timeout_events"] = state["timeouts"]
            row["idle_reply_monotonic"] = timing.completed_monotonic
            row["uwb_count_at_idle_reply"] = self.uwb_seen_count[node]
            row["last_uwb_at_idle_reply"] = self.formal_last_uwb.get(node)
        self.checkpoint()

        witness_started = time.monotonic()
        quiet_reference = {
            node: self.formal_last_uwb.get(
                node, float(results["nodes"][node]["idle_reply_monotonic"])
            )
            for node in survivors
        }
        absolute_deadline = witness_started + 210.0
        while time.monotonic() < absolute_deadline:
            now = time.monotonic()
            quiet_for = {
                node: now
                - max(
                    quiet_reference[node],
                    self.formal_last_uwb.get(node, quiet_reference[node]),
                )
                for node in survivors
            }
            if quiet_for and all(value >= 90.0 for value in quiet_for.values()):
                break
            self.channel.read(min(absolute_deadline, now + 0.5))
        witness_ended = time.monotonic()
        results["quiet_witness"] = {
            "started_monotonic": witness_started,
            "ended_monotonic": witness_ended,
            "elapsed_after_all_replies_s": witness_ended - witness_started,
            "absolute_cap_s": 210.0,
        }
        for node in survivors:
            row = results["nodes"][node]
            last_uwb = max(
                quiet_reference[node],
                self.formal_last_uwb.get(node, quiet_reference[node]),
            )
            quiet_s = witness_ended - last_uwb
            row["observed_cessation_reference_monotonic"] = last_uwb
            row["final_quiet_s"] = quiet_s
            row["uwb_records_after_idle_dispatch"] = (
                self.uwb_seen_count[node] - row["uwb_count_at_idle_dispatch"]
            )
            row["uwb_records_after_idle_reply"] = (
                self.uwb_seen_count[node] - row["uwb_count_at_idle_reply"]
            )
            row["idle_pass"] = (
                row.get("idle_cfg", {}).get("completion")
                in ("cfg_ok", "no_cfg_ok_behavior_required")
                and quiet_s >= 90.0
            )
            row["idle_proof_method"] = (
                "CFG_OK plus observed cessation"
                if row.get("idle_cfg", {}).get("completion") == "cfg_ok"
                else "observed UWB cessation; command-reply path degraded"
            )
        results["ended"] = utc_now()
        results["status"] = (
            "PASS"
            if set(survivors) == set(NODES)
            and all(
                row.get("imu_stop", {}).get("ok")
                and row.get("idle_pass")
                for row in results["nodes"].values()
            )
            else "PARTIAL"
        )
        write_json(self.root / "end_state.json", results)
        return results


def start_listener_collector(
    root: Path, *, label: str, duration_s: float
) -> tuple[subprocess.Popen[str], object, Path]:
    out_dir = root / label
    log_path = root / f"{label}.log"
    handle = log_path.open("x", encoding="utf-8", buffering=1)
    command = [
        sys.executable,
        str(COLLECTOR),
        "--out-dir",
        str(out_dir),
        "--duration",
        str(duration_s),
        "--require-kind",
        "LSTAT",
        "--require-kind",
        "LPD",
        "--require-kind",
        "LRD",
    ]
    process = subprocess.Popen(
        command,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, handle, out_dir


def circular_distance_us(a: float, b: float, period_us: float) -> float:
    direct = abs(a - b)
    return min(direct, period_us - direct)


def behavioral_slot_proof(listener_dir: Path) -> dict[str, object]:
    """Prove all ten configured slots from passive listener timestamps."""
    merged = listener_dir / "merged_index.jsonl"
    rows_by_listener: dict[str, list[dict[str, object]]] = {}
    with merged.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if not row.get("parsed_ok") or row.get("rx_unwrapped_ticks") is None:
                continue
            if row.get("kind") not in ("LBD", "LPD"):
                continue
            rows_by_listener.setdefault(str(row["listener_snr"]), []).append(row)

    ticks_per_us = 63_897.6
    per_node: dict[str, object] = {}
    for node in NODES:
        source = 0xB100 + TAG_NUMBER[node]
        slot = SLOT_MAP[node]
        expected_main = ((slot + 1) * 10_000.0) % 110_000.0
        expected_sub = (expected_main - 6_000.0) % 110_000.0
        per_listener: dict[str, object] = {}
        passing = 0
        for snr, rows in rows_by_listener.items():
            rows.sort(key=lambda row: int(row["arrival_monotonic_ns"]))
            last_beacon: int | None = None
            phases: list[float] = []
            for row in rows:
                timestamp = int(row["rx_unwrapped_ticks"])
                if row["kind"] == "LBD":
                    last_beacon = timestamp
                    continue
                if row.get("src") != source or last_beacon is None:
                    continue
                delta_us = (timestamp - last_beacon) / ticks_per_us
                if 0.0 <= delta_us <= 110_000.0:
                    phases.append(delta_us)
            phases.sort()
            median = phases[len(phases) // 2] if phases else None
            distance = (
                min(
                    circular_distance_us(median, expected_main, 110_000.0),
                    circular_distance_us(median, expected_sub, 110_000.0),
                )
                if median is not None
                else None
            )
            phase_matches = distance is not None and distance <= 2_500.0
            passed = len(phases) >= 20 and phase_matches
            if passed:
                passing += 1
            per_listener[snr] = {
                "pairs": len(phases),
                "median_phase_us": median,
                "min_phase_us": phases[0] if phases else None,
                "max_phase_us": phases[-1] if phases else None,
                "distance_to_nearest_expected_us": distance,
                "phase_matches": phase_matches,
                "slot_phase_pass": passed,
            }
        per_node[node] = {
            "on_air_src": f"0x{source:04X}",
            "expected_slot": slot,
            "expected_main_phase_us": expected_main,
            "expected_sub_relative_phase_us": expected_sub,
            "tolerance_us": 2_500.0,
            "listeners_passing": passing,
            "required_listeners": 3,
            "per_listener": per_listener,
            "pass": passing >= 3,
        }
    result = {
        "nodes": per_node,
        "nodes_observed": [
            node
            for node, row in per_node.items()
            if any(
                int(listener["pairs"]) > 0
                for listener in row["per_listener"].values()
            )
        ],
        "nodes_with_three_listener_proof": [
            node for node, row in per_node.items() if bool(row["pass"])
        ],
        "observed_slot_mismatches": [
            node
            for node, row in per_node.items()
            if any(
                int(listener["pairs"]) >= 5
                and not bool(listener["phase_matches"])
                for listener in row["per_listener"].values()
            )
        ],
        "annotation": BEHAVIORAL_CFG_NOTE,
        "not_a_measurement_waiver": True,
        "coverage_rule": (
            "Passive listeners are corroboration: every listener with at least "
            "five pairs must match the commanded slot. Missing listener coverage "
            "does not override the per-node Fusion data-plane proof."
        ),
    }
    result["no_observed_slot_mismatches"] = not bool(
        result["observed_slot_mismatches"]
    )
    return result


def stop_listener_collector(
    process: subprocess.Popen[str] | None, handle: object | None
) -> dict[str, object]:
    result: dict[str, object] = {"started": process is not None}
    if process is not None:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=20.0)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10.0)
        result["returncode"] = process.returncode
    if handle is not None:
        handle.close()
    return result


def require_cfg_echo(node: str, row: dict[str, object], slot: int) -> None:
    if row.get("completion") != "cfg_ok":
        raise SessionError(f"{node} missing CFG_OK: {row}")
    reply = str(row.get("reply", ""))
    required = (
        f"SLOT={slot}/11",
        "PERIOD=10",
        "BEACON_SYNC=1",
        "BEACON_WIN_N=1",
        "DW_ANCHOR=0",
        "LIVE=1",
    )
    if any(token not in reply for token in required):
        raise SessionError(f"{node} incomplete CFG_OK: {reply}")


def require_locked(snapshot: dict[str, object]) -> None:
    failures: list[str] = []
    generations: set[str] = set()
    for node in NODES:
        status = snapshot["beacon_status"].get(node, {})
        if node in BEHAVIORAL_CFG_NODES:
            if status.get("proof_method") != "behavioral" or status.get("sweep_at_reply") is None:
                failures.append(f"{node}: behavioral sweep proof missing")
            continue
        fields = status.get("fields", {})
        generations.add(str(fields.get("gen")))
        required = {
            "sync": "1",
            "lock": "1",
            "promoted": "0",
            "dw": "0",
            "win": "1",
        }
        bad = {key: (fields.get(key), value) for key, value in required.items() if fields.get(key) != value}
        if bad:
            failures.append(f"{node}: {bad}")
    sub = listener_state(snapshot["sub"])
    if not sub["slaved_seen"] or sub["tx_records"] != 0:
        failures.append(f"sub not clean SLAVED: {sub}")
    if generations and (
        len(generations) != 1 or None in generations or "None" in generations
    ):
        failures.append(f"generation mismatch: {sorted(generations)}")
    if failures:
        raise SessionError(f"preformal lock gate failed: {failures}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()
    if args.evidence_root.exists():
        raise SystemExit(f"refusing existing evidence root: {args.evidence_root}")

    runner = R4Runner(args.evidence_root, args.fusion_port, 1.25)
    runner.period_us = 100_000
    runner.slot_map = dict(SLOT_MAP)
    runner.win_map = {node: 1 for node in NODES}
    collector: subprocess.Popen[str] | None = None
    collector_handle: object | None = None
    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started": utc_now(),
        "master_marker": MASTER_MARKER,
        "slot_map": SLOT_MAP,
        "slot10": SLOT10,
        "slot10_waiver": SLOT10_WAIVER,
        "behavioral_cfg_waiver": {
            "nodes": list(NODES),
            "annotation": BEHAVIORAL_CFG_NOTE,
            "not_a_measurement_waiver": True,
            "all_r4_measurement_gates_unchanged": True,
            "terminal_witness_origin": "last observed UWB record",
            "debt": (
                "relay8 active-from-idle CFG_OK invisibility; BSF3C79 joins "
                "BSFB165 on the command-path watch list"
            ),
        },
        "formal_analysis": "DEFERRED UNTIL LITERAL ANALYZE TOKEN",
    }
    formal_complete = False
    try:
        runner.open()
        result["initial_ready"] = runner.wait_fleet_ready("ON")
        # Populate AliveBook before any fallible operation so every exception
        # path cleans all ten nodes instead of treating an empty book as PASS.
        result["initial_alive_book"] = runner.list_peers()
        if not all(runner.alive.is_alive(node) for node in NODES):
            raise SessionError("initial alive bookkeeping is not 10/10")
        result["setup_led_latch_archive"] = runner.ledclear()

        cfg: dict[str, object] = {}
        for node in NODES:
            command = active_cfg(
                TAG_NUMBER[node],
                SLOT_MAP[node],
                count=11,
                beacon_win_n=1,
            )
            if node in BEHAVIORAL_CFG_NODES:
                row = runner.send_tag_cfg_echo(node, command)
                row["high_level_attempt"] = 1
                if row.get("completion") == "cfg_ok":
                    require_cfg_echo(node, row, SLOT_MAP[node])
                    method = "CFG_OK"
                elif row.get("completion") in ("tag_timeout", "reply_timeout"):
                    method = "BEHAVIOR_PENDING"
                else:
                    raise SessionError(
                        f"{node} active CFG dispatch was not definitive: {row}"
                    )
                cfg[node] = {
                    "attempts": [row],
                    "accepted": row,
                    "proof_method": method,
                    "annotation": BEHAVIORAL_CFG_NOTE,
                    "not_a_measurement_waiver": True,
                }
                continue
            attempts: list[dict[str, object]] = []
            for attempt in range(1, 3):
                row = runner.send_tag_cfg_echo(node, command)
                row["high_level_attempt"] = attempt
                attempts.append(row)
                if row.get("completion") == "cfg_ok":
                    require_cfg_echo(node, row, SLOT_MAP[node])
                    break
                if attempt < 2:
                    time.sleep(2.0)
            else:
                raise SessionError(
                    f"{node} missing CFG_OK after two separate sends: {attempts}"
                )
            cfg[node] = {"attempts": attempts, "accepted": attempts[-1]}
        result["tag_cfg110"] = cfg
        result["main_period110"] = runner.set_main_period(110, "r4_entry")
        runner.period_us = 110_000
        time.sleep(12.0)

        collector, collector_handle, behavior_listener_dir = start_listener_collector(
            args.evidence_root,
            label="fleet_behavior_listener_capture",
            duration_s=35.0,
        )
        result["behavior_listener_preflight"] = wait_listener_preflight(
            behavior_listener_dir, collector, timeout_s=20.0
        )
        result["behavior_fusion_capture"] = runner.capture(
            "fleet_behavior_20s", 20.0
        )
        result["behavior_listener_collector"] = stop_listener_collector(
            collector, collector_handle
        )
        collector = None
        collector_handle = None
        result["behavior_slot_proof"] = behavioral_slot_proof(
            behavior_listener_dir
        )
        behavioral_records = {
            node: int(result["behavior_fusion_capture"]["records"].get(node, 0))
            for node in NODES
        }
        result["behavioral_cfg_acceptance"] = {
            "nodes": list(NODES),
            "uwb_records_20s": behavioral_records,
            "minimum_records": 150,
            "slot_listener_proof": result["behavior_slot_proof"],
            "annotation": BEHAVIORAL_CFG_NOTE,
            "not_a_measurement_waiver": True,
            "pass": (
                all(count >= 150 for count in behavioral_records.values())
                and bool(
                    result["behavior_slot_proof"][
                        "no_observed_slot_mismatches"
                    ]
                )
            ),
        }
        if not result["behavioral_cfg_acceptance"]["pass"]:
            raise SessionError(
                f"fleet behavioral CFG proof failed: "
                f"{result['behavioral_cfg_acceptance']}"
            )
        result["entry_snapshot"] = runner.snapshot("r4_entry")
        require_locked(result["entry_snapshot"])

        result["imu_start"] = runner.start_imu10()
        if any(row.get("status") != "PASS" for row in result["imu_start"].values()):
            raise SessionError(f"IMU batch-10 start failed: {result['imu_start']}")
        runner.imu_batch_values = {node: set() for node in NODES}
        result["imu_batch10_smoke"] = runner.capture("imu_batch10_smoke", 12.0)
        bad_batches = {
            node: sorted(runner.imu_batch_values[node])
            for node in NODES
            if runner.imu_batch_values[node] != {10}
        }
        if bad_batches:
            raise SessionError(f"IMU batch-10 wire proof failed: {bad_batches}")

        # Preserve any setup-only latch, then clear it immediately before the
        # formal baseline so the window's aggregate latch delta is meaningful.
        result["preformal_ledclear"] = runner.ledclear()
        result["formal_before"] = runner.snapshot("r4_formal_before")
        require_locked(result["formal_before"])
        led_fields = parse_fields(str(result["formal_before"].get("ledstat", "")))
        if led_fields.get("latch") != "0" or led_fields.get("mask") != "0x00":
            raise SessionError(f"formal LED baseline not clean: {result['formal_before']['ledstat']}")

        assert runner.channel is not None
        result["formal_boundary"] = runner.channel.discard_pending(
            "r4_formal_after_snapshot"
        )
        result["formal_host_drain_before"] = runner.channel.health_snapshot()
        collector, collector_handle, listener_dir = start_listener_collector(
            args.evidence_root,
            label="r4_listener_capture",
            duration_s=1860.0,
        )
        result["listener_preflight"] = wait_listener_preflight(
            listener_dir, collector, timeout_s=20.0
        )
        result["formal_capture"] = runner.capture_formal("r4_formal_1800s", 1800.0)
        formal_complete = True

        result["listener_collector"] = stop_listener_collector(
            collector, collector_handle
        )
        collector = None
        collector_handle = None
        listener_summary = listener_dir / "summary.json"
        result["listener_summary"] = (
            json.loads(listener_summary.read_text(encoding="utf-8"))
            if listener_summary.exists()
            else {"missing": True}
        )
        result["formal_after"] = runner.snapshot("r4_formal_after")
        result["main_start_fail_before"] = lbstat_counters(
            result["formal_before"]["main"]
        )
        result["main_start_fail_after"] = lbstat_counters(
            result["formal_after"]["main"]
        )
        result["status"] = "WINDOW_COMPLETE_ANALYSIS_DEFERRED"
    except Exception as exc:
        result["status"] = "FAILED_BEFORE_ANALYSIS"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if collector is not None or collector_handle is not None:
            result["listener_collector_terminal"] = stop_listener_collector(
                collector, collector_handle
            )
        try:
            result["cleanup"] = runner.cleanup("R4 guaranteed terminal")
        except Exception as exc:
            result["cleanup_error"] = f"{type(exc).__name__}: {exc}"
        try:
            result["terminal_main100"] = runner.set_main_period(
                100, "r4_terminal"
            )
            runner.period_us = 100_000
        except Exception as exc:
            result["terminal_main100_error"] = f"{type(exc).__name__}: {exc}"
        result["ended"] = utc_now()
        result["formal_complete"] = formal_complete
        if runner.channel is not None:
            result["host_drain_final"] = runner.channel.health_snapshot()
        write_json(args.evidence_root / "R4_RUN_STATE.json", result)
        runner.summary["r4_final"] = result
        runner.checkpoint()
        if runner.channel is not None:
            runner.channel.close()
        if runner.raw is not None:
            runner.raw.close()

    cleanup_ok = result.get("cleanup", {}).get("status") == "PASS"
    main100_ok = "terminal_main100" in result
    if formal_complete and cleanup_ok and main100_ok:
        print("R4 WINDOW COMPLETE — FIELD SAFE — BOARDS SAFE TO RE-DOCK", flush=True)
        print("WAITING FOR TOKEN: ANALYZE", flush=True)
        return 0
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
