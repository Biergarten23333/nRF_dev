#!/usr/bin/env python3
"""Host-fixed G3-W retry followed conditionally by H4 endurance."""

from __future__ import annotations

import argparse
import csv
import glob
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_day_h3 import (
    HARD_LEDGER,
    SLOT10,
    SLOT_MAP,
    DayRunner,
    lbstat_counters,
    listener_state,
)
from batch_g_overnight import (
    MAIN_MARKER,
    MAIN_SNR,
    MASTER_MARKER,
    NODES,
    RATE_GATE_HZ,
    SUB_MARKER,
    SUB_SNR,
    TAG_NUMBER,
    active_cfg,
    utc_now,
)
from coldstart_fusion_control import decode_guard
from capacity_ramp import RecordingAssembler
from fusion_session import SessionError, parse_fields, resolve_fusion_port
from pre_ramp_hardening import request_list


EXPECTED_LISTENER_SNRS = (
    "760181725",
    "760184545",
    "760184548",
    "760184753",
    "760184767",
    "760184784",
    "760184964",
)
ANCHOR_CONTROL_PORT = (
    "/dev/serial/by-id/"
    "usb-Master_Anchor_Master_Anchor_Control_87EA2F4A526C5A02-if00"
)


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class AsyncObservedLineChannel(ThreadedLineChannel):
    def __init__(self, *args, observer, **kwargs):
        self.observer = observer
        self.consumer_actions: list[dict[str, object]] = []
        self.consumer_action_results: list[dict[str, object]] = []
        super().__init__(*args, **kwargs)

    def schedule_consumer_action(
        self, when: float, label: str, callback
    ) -> None:
        self.consumer_actions.append(
            {"when": when, "label": label, "callback": callback}
        )
        self.consumer_actions.sort(key=lambda row: float(row["when"]))

    def _run_due_consumer_actions(self) -> None:
        while (
            self.consumer_actions
            and time.monotonic() >= float(self.consumer_actions[0]["when"])
        ):
            action = self.consumer_actions.pop(0)
            started = time.monotonic()
            row: dict[str, object] = {
                "label": action["label"],
                "scheduled_monotonic": action["when"],
                "started_monotonic": started,
            }
            try:
                row["result"] = action["callback"]()
                row["status"] = "COMPLETE"
            except Exception as exc:
                row["status"] = "ERROR"
                row["error"] = f"{type(exc).__name__}: {exc}"
            row["ended_monotonic"] = time.monotonic()
            row["blocking_duration_s"] = (
                float(row["ended_monotonic"]) - started
            )
            self.consumer_action_results.append(row)

    def read(self, deadline: float) -> str | None:
        self._run_due_consumer_actions()
        if self.consumer_actions:
            deadline = min(deadline, float(self.consumer_actions[0]["when"]))
        line = super().read(deadline)
        if line is not None:
            self.observer(line)
        return line


class StallfixRunner(DayRunner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.zero_progress_last: dict[str, int] = {}
        self.zero_progress_misses: dict[str, int] = {node: 0 for node in NODES}
        self.zero_progress_red: list[dict[str, object]] = []
        self.qos_timeline: list[dict[str, object]] = []

    def observe_line(self, line: str) -> None:
        super().observe_line(line)
        if not line.startswith("FUSION_QOS "):
            return
        fields = parse_fields(line)
        node = fields.get("name")
        if node not in NODES:
            return
        row: dict[str, object] = {
            "observed_monotonic": time.monotonic(),
            "name": node,
            "line": line,
        }
        for key in ("reports", "crc_ok", "crc_error", "rx_timeout"):
            if key in fields:
                row[key] = int(fields[key], 0)
        self.qos_timeline.append(row)

    def qos_context(
        self, label: str, started: float, ended: float
    ) -> dict[str, object]:
        rows = [
            row
            for row in self.qos_timeline
            if started <= float(row["observed_monotonic"]) <= ended
        ]
        result = qos_context_vs_nine(rows, target="BSF31CC")
        result.update(
            {
                "label": label,
                "started_monotonic": started,
                "ended_monotonic": ended,
            }
        )
        return result

    def open(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw = (self.root / "fusion_cdc.log").open(
            "x", encoding="utf-8", buffering=1
        )
        self.channel = AsyncObservedLineChannel(
            resolve_fusion_port(self.fusion_port),
            self.raw,
            "FUSION",
            observer=self.observe_line,
            backlog_red_records=8192,
            stall_red_s=1.0,
        )
        self.channel.transport_mode = "binary"
        self.channel.text_pending.clear()
        self.summary["fusion_port"] = self.channel.port
        self.summary["decode_before_send"] = decode_guard(self.channel, 15.0)
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

    def snapshot(self, label: str) -> dict[str, object]:
        snapshot = super().snapshot(label)
        progress: dict[str, object] = {}
        for node, status in snapshot["beacon_status"].items():
            sweep = status.get("sweep_at_reply")
            if sweep is None:
                continue
            previous = self.zero_progress_last.get(node)
            if previous is not None and sweep == previous:
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
                print(
                    f"RED ZERO_PROGRESS node={node} label={label} "
                    f"sweep={sweep}",
                    flush=True,
                )
        snapshot["zero_progress"] = progress
        snapshot["host_drain"] = self.channel.health_snapshot()
        self.checkpoint()
        return snapshot

    def ledstat(self) -> str:
        assert self.channel is not None
        self.channel.send("LEDSTAT")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            line = self.channel.read(deadline)
            if line and line.startswith("LEDSTAT "):
                return line
        raise SessionError("LEDSTAT timeout")

    def ledclear(self) -> dict[str, str]:
        assert self.channel is not None
        before = self.ledstat()
        self.channel.send("LEDCLEAR")
        deadline = time.monotonic() + 5.0
        cleared = None
        while time.monotonic() < deadline:
            line = self.channel.read(deadline)
            if line and line.startswith("LEDCLEAR "):
                cleared = line
                break
        if cleared is None:
            raise SessionError("LEDCLEAR timeout")
        after = self.ledstat()
        fields = parse_fields(after)
        if fields.get("latch") != "0" or fields.get("mask") != "0x00":
            raise SessionError(f"LED latch did not clear: {after}")
        return {"before": before, "clear": cleared, "after": after}

    def _raw_list(self) -> dict[str, object]:
        assert self.channel is not None
        return request_list(
            self.channel, RecordingAssembler(), {}, tuple(NODES)
        )

    def wait_fleet_ready(
        self, spacing: str | None, timeout_s: float = 180.0
    ) -> dict[str, object]:
        if spacing not in (None, "OFF", "ON"):
            raise ValueError("spacing must be OFF, ON, or None")
        expected_us = (
            None if spacing is None else ("5000" if spacing == "ON" else "7500")
        )
        deadline = time.monotonic() + timeout_s
        observations: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            try:
                listing = self._raw_list()
            except SessionError as exc:
                observations.append({"error": str(exc)})
                time.sleep(1.0)
                continue
            aggregate = listing["aggregate"]
            peers = listing["peers"]
            ready_peers = {
                node
                for node, row in peers.items()
                if row.get("connected") == "1"
                and row.get("subscribed") == "1"
            }
            row = {
                "aggregate": aggregate,
                "peer_count": len(peers),
                "ready_peers": sorted(ready_peers),
            }
            observations.append(row)
            if (
                aggregate.get("count") == "10"
                and aggregate.get("ready") == "10"
                and (
                    spacing is None
                    or (
                        aggregate.get("spacing") == spacing
                        and aggregate.get("spacing_us") == expected_us
                    )
                )
                and set(peers) == set(NODES)
                and ready_peers == set(NODES)
            ):
                return {
                    "spacing": spacing,
                    "expected_us": expected_us,
                    "listing": listing,
                    "observations": observations,
                }
            time.sleep(1.0)
        raise SessionError(
            f"fleet did not reach 10/10 spacing={spacing}: "
            f"{observations[-3:]}"
        )

    def spacing_transition(self, mode: str) -> dict[str, object]:
        assert self.channel is not None
        if mode not in ("OFF", "ON"):
            raise ValueError("spacing mode must be OFF or ON")
        expected_us = "5000" if mode == "ON" else "7500"
        self.channel.send(f"SPACING {mode}")
        lines: list[str] = []
        deadline = time.monotonic() + 120.0
        applied: dict[str, str] | None = None
        while time.monotonic() < deadline:
            line = self.channel.read(deadline)
            if line is None:
                continue
            if line.startswith("FUSION_SPACING"):
                lines.append(line)
            if not line.startswith("FUSION_SPACING "):
                continue
            fields = parse_fields(line)
            if fields.get("state") == "FAILED":
                raise SessionError(f"SPACING {mode} failed: {line}")
            if (
                fields.get("state") in ("APPLIED", "UNCHANGED")
                and fields.get("mode") == mode
                and fields.get("applied_us") == expected_us
            ):
                applied = fields
                break
        if applied is None:
            raise SessionError(f"SPACING {mode} apply timeout: {lines}")
        ready = self.wait_fleet_ready(mode)
        return {"mode": mode, "applied": applied, "lines": lines, "ready": ready}

    def rebuild_spacing(self, label: str) -> dict[str, object]:
        # The mode is volatile across a DK power loss.  Accept either mode as
        # the initial observation, then still issue the prescribed OFF->ON
        # sequence.  ON is the transition that guarantees the final full
        # reconnect when the boot default was already OFF.
        before = self.wait_fleet_ready(None)
        off = self.spacing_transition("OFF")
        on = self.spacing_transition("ON")
        before_generation = int(
            before["listing"]["aggregate"].get("spacing_generation", "0"), 0
        )
        after_generation = int(
            on["ready"]["listing"]["aggregate"].get(
                "spacing_generation", "0"
            ), 0
        )
        result = {
            "label": label,
            "before": before,
            "off": off,
            "on": on,
            "generation_before": before_generation,
            "generation_after": after_generation,
            "pass": after_generation > before_generation,
        }
        write_json(self.root / f"{label}_spacing_rebuild.json", result)
        if not result["pass"]:
            raise SessionError(
                "spacing generation failed to advance: "
                f"{before_generation}->{after_generation}"
            )
        return result

    def measured_window(
        self,
        label: str,
        duration_s: float,
        consumer_actions: list[tuple[float, str, object]] | None = None,
    ) -> dict[str, object]:
        before = self.snapshot(f"{label}_before")
        ledstat_before = self.ledstat()
        boundary = self.channel.discard_pending(
            f"{label}_formal_start_after_snapshot"
        )
        host_before = self.channel.health_snapshot()
        action_start_index = len(self.channel.consumer_action_results)
        action_base = time.monotonic()
        for offset_s, action_label, callback in consumer_actions or []:
            self.channel.schedule_consumer_action(
                action_base + offset_s, action_label, callback
            )
        capture = self.capture(label, duration_s)
        # A due action is normally executed from read().  Run the final check
        # explicitly so an action exactly on the end boundary cannot vanish.
        self.channel._run_due_consumer_actions()
        if self.channel.consumer_actions:
            raise SessionError(
                f"{label} ended with pending consumer actions: "
                f"{self.channel.consumer_actions}"
            )
        host_capture_end = self.channel.health_snapshot()
        ledstat_capture_end = self.ledstat()
        after = self.snapshot(f"{label}_after")
        measured = self.analyze_window(label, before, capture, after)
        measured["dk_ledstat_before"] = ledstat_before
        measured["dk_ledstat_capture_end"] = ledstat_capture_end
        measured["host_boundary"] = boundary
        measured["host_drain_before"] = host_before
        measured["host_drain_capture_end"] = host_capture_end
        measured["host_drain_after_snapshot"] = self.channel.health_snapshot()
        measured["consumer_actions"] = self.channel.consumer_action_results[
            action_start_index:
        ]
        measured["bsf31cc_qos_vs_nine"] = self.qos_context(
            label,
            float(capture["started_monotonic"]),
            float(capture["ended_monotonic"]),
        )
        write_json(self.root / f"{label}_analysis.json", measured)
        return measured


def _qos_totals(rows: list[dict[str, object]]) -> dict[str, int]:
    return {
        key: sum(int(row.get(key, 0)) for row in rows)
        for key in ("reports", "crc_ok", "crc_error", "rx_timeout")
    }


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def qos_context_vs_nine(
    rows: list[dict[str, object]], *, target: str
) -> dict[str, object]:
    """Compare one link with the pooled other-nine connection exposure."""
    target_rows = [row for row in rows if row.get("name") == target]
    control_rows = [row for row in rows if row.get("name") != target]
    expected_controls = set(NODES) - {target}
    controls_present = {str(row.get("name")) for row in control_rows}
    target_totals = _qos_totals(target_rows)
    control_totals = _qos_totals(control_rows)
    target_crc_trials = target_totals["crc_ok"] + target_totals["crc_error"]
    control_crc_trials = control_totals["crc_ok"] + control_totals["crc_error"]
    target_crc = _ratio(target_totals["crc_error"], target_crc_trials)
    control_crc = _ratio(control_totals["crc_error"], control_crc_trials)
    target_timeout = _ratio(
        target_totals["rx_timeout"], target_totals["reports"]
    )
    control_timeout = _ratio(
        control_totals["rx_timeout"], control_totals["reports"]
    )
    return {
        "definition": {
            "control": "pooled other nine Fusion links",
            "crc_error_fraction": "crc_error / (crc_ok + crc_error)",
            "rx_timeout_per_report": "rx_timeout / reports",
            "pooling": "sum raw counters across controls before division",
        },
        "target": target,
        "qos_rows": len(rows),
        "target_rows": len(target_rows),
        "control_rows": len(control_rows),
        "controls_present": sorted(controls_present),
        "controls_missing": sorted(expected_controls - controls_present),
        "target_totals": target_totals,
        "control_totals": control_totals,
        "target_crc_error_fraction": target_crc,
        "control_crc_error_fraction": control_crc,
        "crc_error_fraction_ratio_target_vs_nine": (
            _ratio(target_crc, control_crc)
            if target_crc is not None and control_crc is not None
            else None
        ),
        "target_rx_timeout_per_report": target_timeout,
        "control_rx_timeout_per_report": control_timeout,
        "rx_timeout_per_report_ratio_target_vs_nine": (
            _ratio(target_timeout, control_timeout)
            if target_timeout is not None and control_timeout is not None
            else None
        ),
        "instrument_complete": bool(
            target_rows and controls_present == expected_controls
        ),
    }


def write_h4_qos_timeline(path: Path, chunks: list[dict[str, object]]) -> None:
    fields = (
        "chunk", "utc", "target_reports", "control_reports",
        "target_crc_error_fraction", "control_crc_error_fraction",
        "crc_error_fraction_ratio_target_vs_nine",
        "target_rx_timeout_per_report", "control_rx_timeout_per_report",
        "rx_timeout_per_report_ratio_target_vs_nine", "instrument_complete",
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for chunk in chunks:
            qos = chunk.get("bsf31cc_qos_vs_nine", {})
            target_totals = qos.get("target_totals", {})
            control_totals = qos.get("control_totals", {})
            writer.writerow(
                {
                    "chunk": chunk.get("index"),
                    "utc": chunk.get("utc"),
                    "target_reports": target_totals.get("reports"),
                    "control_reports": control_totals.get("reports"),
                    **{
                        key: qos.get(key)
                        for key in fields
                        if key not in (
                            "chunk", "utc", "target_reports", "control_reports"
                        )
                    },
                }
            )


def led_queue(line: object) -> int:
    if not isinstance(line, str):
        raise SessionError("missing LEDSTAT line")
    return int(parse_fields(line)["queue"], 0)


def window_gate(
    measured: dict[str, object], *, strict_rates: bool
) -> dict[str, object]:
    capture = measured["capture"]
    nodes: dict[str, object] = {}
    for node in NODES:
        row = measured["nodes"].get(node, {})
        reasons: list[str] = []
        if not row.get("available"):
            reasons.append("measurement unavailable")
        else:
            if strict_rates and node != SLOT10 and row.get(
                "tag_domain_rate_hz", 0.0
            ) < RATE_GATE_HZ:
                reasons.append("rate below 9.00 Hz")
            for key in (
                "sweep_missing",
                "sweep_duplicates",
                "sweep_reorders",
            ):
                if row.get(key, 0) != 0:
                    reasons.append(f"{key}={row.get(key)}")
            if row.get("lock_before") != "1" or row.get("lock_after") != "1":
                reasons.append("lock not held")
            if row.get("gen_before") != row.get("gen_after"):
                reasons.append("generation changed")
            for key in HARD_LEDGER:
                value = row.get("ledger_deltas", {}).get(key)
                if value is None:
                    reasons.append(f"ledger {key} unavailable")
                elif value != 0:
                    reasons.append(f"ledger {key}={value}")
        nodes[node] = {
            "rate_gated": node != SLOT10,
            "pass": not reasons,
            "reasons": reasons,
            "measurement": row,
        }
    queue_before = led_queue(measured["dk_ledstat_before"])
    queue_after = led_queue(measured["dk_ledstat_capture_end"])
    host_before = measured["host_drain_before"]
    host_end = measured["host_drain_capture_end"]
    host_deltas = {
        key: int(host_end[key]) - int(host_before[key])
        for key in (
            "decoded_queue_drops",
            "log_queue_drops",
            "red_markers",
            "reader_exceptions",
        )
    }
    sub_before = listener_state(measured["field_status"]["sub_before"])
    sub_after = listener_state(measured["field_status"]["sub_after"])
    sub_pass = bool(
        sub_before["slaved_seen"]
        and sub_after["slaved_seen"]
        and sub_before["tx_records"] == 0
        and sub_after["tx_records"] == 0
    )
    transport_pass = bool(
        capture["decoder_errors"] == 0
        and not capture["malformed"]
        and not capture["disconnects"]
        and queue_after - queue_before == 0
        and all(value == 0 for value in host_deltas.values())
    )
    # Slot 10 waives only its rate.  Loss, reorder, lock, generation, and
    # ledger integrity remain hard gates for all ten nodes.
    strict_nodes_pass = all(row["pass"] for row in nodes.values())
    return {
        "pass": strict_nodes_pass and transport_pass and sub_pass,
        "nodes": nodes,
        "dk_queue_before": queue_before,
        "dk_queue_after": queue_after,
        "dk_queue_delta": queue_after - queue_before,
        "host_drain_deltas": host_deltas,
        "transport_pass": transport_pass,
        "sub_pass": sub_pass,
        "sub_before": sub_before,
        "sub_after": sub_after,
    }


def stability_gate(measured: dict[str, object]) -> dict[str, object]:
    """P1's deliberately narrow gate: DK queue and host loss only."""
    full = window_gate(measured, strict_rates=False)
    sequence_failures = {
        node: {
            key: measured["nodes"].get(node, {}).get(key, 0)
            for key in (
                "sweep_missing",
                "sweep_duplicates",
                "sweep_reorders",
            )
            if measured["nodes"].get(node, {}).get(key, 0) != 0
        }
        for node in NODES
    }
    sequence_failures = {
        node: row for node, row in sequence_failures.items() if row
    }
    return {
        **full,
        "pass": full["transport_pass"] and not sequence_failures,
        "scope": "DK queue-fault delta and host loss only",
        "sequence_failures": sequence_failures,
    }


def p1_beacon_query_gate(measured: dict[str, object]) -> dict[str, object]:
    rows = measured.get("consumer_actions", [])
    reasons: list[str] = []
    if len(rows) != 5:
        reasons.append(f"query_count={len(rows)} expected=5")
    for row in rows:
        result = row.get("result", {})
        if row.get("status") != "COMPLETE":
            reasons.append(f"{row.get('label')} status={row.get('status')}")
        elif result.get("returncode") != 0 or result.get("decoded") is None:
            reasons.append(f"{row.get('label')} delivery/decode failed")
    return {"pass": not reasons, "reasons": reasons, "queries": rows}


def p1_failure_nodes(
    measured: dict[str, object], gate: dict[str, object]
) -> list[str]:
    failed = set(gate.get("sequence_failures", {}))
    for node, row in measured.get("nodes", {}).items():
        ledger = row.get("ledger_deltas", {})
        if any(int(ledger.get(key, 0)) != 0 for key in HARD_LEDGER):
            failed.add(node)
    return sorted(failed)


def anchor_responder_gate(evidence_root: Path) -> dict[str, object]:
    """Run the established 8/8 verifier before opening the Fusion CDC."""
    tool = (
        Path(__file__).resolve().parents[2]
        / "UWB_Part/2026-07-15-FREEZE/scripts/ops"
        / "verify_all_anchor_responder_runtime.py"
    )
    out_dir = evidence_root / "p0_anchor_responder"
    completed = subprocess.run(
        (
            sys.executable,
            str(tool),
            "--out-dir",
            str(out_dir),
            "--port",
            ANCHOR_CONTROL_PORT,
            "--retry-count",
            "3",
        ),
        check=False,
        text=True,
        capture_output=True,
    )
    result = {
        "command": [
            sys.executable,
            str(tool),
            "--out-dir",
            str(out_dir),
            "--port",
            ANCHOR_CONTROL_PORT,
            "--retry-count",
            "3",
        ],
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "evidence_root": str(out_dir),
    }
    write_json(evidence_root / "P0_ANCHOR_GATE.json", result)
    if completed.returncode != 0:
        raise SessionError(f"anchor responder 8/8 gate failed: {result}")
    return result


def listener_link_gate(evidence_root: Path) -> dict[str, object]:
    links = {
        snr: sorted(glob.glob(f"/dev/serial/by-id/*{snr}*"))
        for snr in EXPECTED_LISTENER_SNRS
    }
    result = {
        "expected_snrs": EXPECTED_LISTENER_SNRS,
        "links": links,
        "missing": [snr for snr, rows in links.items() if not rows],
    }
    write_json(evidence_root / "P0_LISTENER_LINKS.json", result)
    if result["missing"]:
        raise SessionError(f"listener permanent-link gate failed: {result}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--f4-manifest", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--hard-stop-hours", type=float, default=12.0)
    args = parser.parse_args()
    if not args.f4_manifest.is_file() or args.f4_manifest.stat().st_size == 0:
        raise SessionError("F4 archive manifest missing; LEDCLEAR forbidden")
    args.evidence_root.mkdir(parents=True, exist_ok=False)

    runner = StallfixRunner(
        args.evidence_root, args.fusion_port, args.hard_stop_hours
    )
    runner.period_us = 100_000
    runner.slot_map = dict(SLOT_MAP)
    runner.win_map = {node: 1 for node in NODES}
    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started": utc_now(),
        "f4_manifest": str(args.f4_manifest),
        "slot10": SLOT10,
        "slot10_waiver": (
            "slot-10 rate deficit is a documented relay7 limitation "
            "(structural last-slot geometry; fix scheduled for relay8); "
            "nine of ten gated."
        ),
    }
    stop_requested = False

    def stop_handler(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True
        runner.stop_requested = True

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    try:
        result["p0_listener_links"] = listener_link_gate(
            args.evidence_root
        )
        result["p0_anchor_gate"] = anchor_responder_gate(
            args.evidence_root
        )
        runner.open()
        initial_ready = runner.wait_fleet_ready(None)
        result["p0_list"] = initial_ready
        result["p0_ledclear"] = runner.ledclear()
        result["p0_spacing_rebuild"] = runner.rebuild_spacing("p0")

        cfg = {}
        for node in NODES:
            cfg[node] = runner.send_tag_cfg(
                node,
                active_cfg(TAG_NUMBER[node], SLOT_MAP[node], count=11),
            )
        result["p0_cfg"] = cfg
        result["p0_period110"] = runner.set_main_period(110, "p0")
        runner.period_us = 110_000
        time.sleep(12.0)
        p0_snapshot = runner.snapshot("p0_locked")
        p0_fail = {
            node: row
            for node, row in p0_snapshot["beacon_status"].items()
            if row.get("fields", {}).get("lock") != "1"
            or row.get("fields", {}).get("sync") != "1"
        }
        generations = {
            row.get("fields", {}).get("gen")
            for row in p0_snapshot["beacon_status"].values()
            if row.get("fields", {}).get("gen") is not None
        }
        if p0_fail or len(generations) != 1:
            raise SessionError(
                f"P0 lock/generation gate failed: {p0_fail}, {generations}"
            )
        result["p0_snapshot_index"] = len(runner.snapshots) - 1

        def query_plan(prefix: str) -> list[tuple[float, str, object]]:
            rows = []
            for index, (offset, snr, marker, role) in enumerate(
                (
                    (30.0, MAIN_SNR, MAIN_MARKER, "main"),
                    (90.0, SUB_SNR, SUB_MARKER, "sub"),
                    (150.0, MAIN_SNR, MAIN_MARKER, "main"),
                    (210.0, SUB_SNR, SUB_MARKER, "sub"),
                    (270.0, MAIN_SNR, MAIN_MARKER, "main"),
                ),
                1,
            ):
                action_label = f"{prefix}_query_{index}_{role}"
                rows.append(
                    (
                        offset,
                        action_label,
                        lambda snr=snr, marker=marker, label=action_label: (
                            runner.listener_status(snr, marker, label)
                        ),
                    )
                )
            return rows

        def run_p1(attempt: int) -> tuple[dict[str, object], dict[str, object]]:
            label = f"p1_stability_attempt{attempt}"
            measured = runner.measured_window(
                label, 300.0, query_plan(label)
            )
            gate = stability_gate(measured)
            gate["bsf31cc_qos_vs_nine"] = measured[
                "bsf31cc_qos_vs_nine"
            ]
            query_gate = p1_beacon_query_gate(measured)
            gate["beacon_query_gate"] = query_gate
            gate["pass"] = gate["pass"] and query_gate["pass"]
            gate["failure_nodes"] = p1_failure_nodes(measured, gate)
            return measured, gate

        result["p1_attempts"] = []
        p1, p1_gate = run_p1(1)
        result["p1_attempts"].append(p1_gate)
        if not p1_gate["pass"]:
            failure_nodes = set(p1_gate["failure_nodes"])
            if "BSF31CC" in failure_nodes or not failure_nodes:
                result["status"] = (
                    "P1_FAIL_BSF31CC"
                    if "BSF31CC" in failure_nodes
                    else "P1_FAIL_UNATTRIBUTED"
                )
                result["cleanup"] = runner.cleanup(result["status"])
                result["terminal_main100"] = runner.set_main_period(
                    100, "p1_failure_terminal"
                )
                runner.period_us = 100_000
                return 3

            # A first failure attributed only to other node(s) selects the
            # one authorized lottery-branch rebuild and identical retry.
            result["p1_retry_ledclear"] = runner.ledclear()
            result["p1_retry_spacing_rebuild"] = runner.rebuild_spacing(
                "p1_retry"
            )
            p1, p1_gate = run_p1(2)
            result["p1_attempts"].append(p1_gate)
            if not p1_gate["pass"]:
                result["status"] = "P1_SECOND_CONSECUTIVE_FAIL"
                result["cleanup"] = runner.cleanup(result["status"])
                result["terminal_main100"] = runner.set_main_period(
                    100, "p1_second_failure_terminal"
                )
                runner.period_us = 100_000
                return 3
        result["p1"] = p1_gate

        p2 = runner.measured_window("p2_g3w_retry", 1800.0)
        p2_gate = window_gate(p2, strict_rates=True)
        p2_gate["bsf31cc_qos_vs_nine"] = p2[
            "bsf31cc_qos_vs_nine"
        ]
        result["p2"] = p2_gate
        if not p2_gate["pass"]:
            result["status"] = "G3-W_FAIL"
            result["cleanup"] = runner.cleanup("P2 G3-W failure")
            result["terminal_main100"] = runner.set_main_period(
                100, "p2_failure_terminal"
            )
            runner.period_us = 100_000
            return 4
        result["status"] = "G3-W_PASS_H4_RUNNING"
        write_json(args.evidence_root / "STALLFIX_RUN_STATE.json", result)

        imu = runner.start_imu(NODES)
        result["p3_imu_start"] = imu
        if not all(row.get("status") == "PASS" for row in imu.values()):
            raise SessionError(f"H4 IMU start failed: {imu}")
        h4_started = time.monotonic()
        h4_capture_deadline = runner.hard_deadline - 15.0 * 60.0
        chunks: list[dict[str, object]] = []
        while not stop_requested and time.monotonic() < h4_capture_deadline:
            listing = runner.list_peers()
            alive = tuple(
                node
                for node in NODES
                if node in listing["peers"] and runner.alive.is_alive(node)
            )
            if not alive:
                break
            boundary = runner.channel.discard_pending(
                f"h4_chunk_{len(chunks):03d}_start"
            )
            capture = runner.capture(f"h4_chunk_{len(chunks):03d}", 300.0)
            snapshot = runner.snapshot(f"h4_snapshot_{len(chunks):03d}")
            qos_context = runner.qos_context(
                f"h4_chunk_{len(chunks):03d}",
                float(capture["started_monotonic"]),
                float(capture["ended_monotonic"]),
            )
            chunk = {
                "index": len(chunks),
                "utc": utc_now(),
                "alive": alive,
                "boundary": boundary,
                "capture": capture,
                "bsf31cc_qos_vs_nine": qos_context,
                "snapshot_index": len(runner.snapshots) - 1,
                "host_drain": runner.channel.health_snapshot(),
            }
            chunks.append(chunk)
            write_json(args.evidence_root / "H4_CHUNKS.json", chunks)
            write_h4_qos_timeline(
                args.evidence_root / "H4_BSF31CC_QOS_TIMELINE.csv", chunks
            )
            if runner.zero_progress_red:
                result["zero_progress_red"] = runner.zero_progress_red
                raise SessionError("H4 zero-progress RED")
        result["p3"] = {
            "started_monotonic": h4_started,
            "ended_monotonic": time.monotonic(),
            "capture_deadline_monotonic": h4_capture_deadline,
            "terminal_reserve_s": 900.0,
            "chunks": len(chunks),
            "stop_requested": stop_requested,
            "alive_epochs": runner.alive.snapshot(),
            "host_drain": runner.channel.health_snapshot(),
        }
        result["cleanup"] = runner.cleanup("H4 terminal")
        result["terminal_main100"] = runner.set_main_period(100, "terminal")
        runner.period_us = 100_000
        result["status"] = "COMPLETE"
        return 0
    except Exception as exc:
        result["status"] = "FAILED"
        result["error"] = f"{type(exc).__name__}: {exc}"
        try:
            result["cleanup"] = runner.cleanup("stallfix exception")
            if runner.period_us != 100_000:
                result["terminal_main100"] = runner.set_main_period(
                    100, "terminal_exception"
                )
                runner.period_us = 100_000
            else:
                result["terminal_main100"] = {
                    "status": "not_needed",
                    "period_us": runner.period_us,
                }
        except Exception as cleanup_exc:
            result["cleanup_error"] = (
                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            )
        return 2
    finally:
        result["ended"] = utc_now()
        result["zero_progress_red"] = runner.zero_progress_red
        if runner.channel is not None:
            result["host_drain_final"] = runner.channel.health_snapshot()
        write_json(args.evidence_root / "STALLFIX_RUN_STATE.json", result)
        runner.checkpoint()
        if runner.channel is not None:
            runner.channel.close()
        if runner.raw is not None:
            runner.raw.close()
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
