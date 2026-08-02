#!/usr/bin/env python3
"""Unattended relay8.1 ten-node capture to battery depletion.

This runner starts only after the Tag Master OTA batch has ended.  It never
opens the Tag Master port and never performs a reset, flash, or OTA.
"""

from __future__ import annotations

import argparse
import json
import signal
import time
from pathlib import Path

from batch_g_day_h3 import SLOT_MAP
from batch_g_overnight import (
    NODES,
    Runner as BaseRunner,
    TAG_NUMBER,
    active_cfg,
    utc_now,
    write_json,
)
from fusion_session import SessionError, parse_fields
from listener_array_run import wait_listener_preflight
from r4_final_capture import (
    R4Runner,
    start_listener_collector,
    stop_listener_collector,
)
from v32_service_gate import summarize_qos


class OvernightRunner(R4Runner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.listener_collector_active = False
        self.listener_dir: Path | None = None
        self.zero_progress_events: list[dict[str, object]] = []

    def listener_status(self, snr: str, marker: str, label: str) -> dict[str, object]:
        if self.listener_collector_active:
            return {
                "continuous_collector": True,
                "listener_snr": snr,
                "expected_marker": marker,
                "evidence": str(self.listener_dir) if self.listener_dir else None,
            }
        return super().listener_status(snr, marker, label)

    def fusion_snapshot(self, label: str) -> dict[str, object]:
        """Use BaseRunner's tag status snapshot without touching listener VCOM."""
        return BaseRunner.snapshot(self, label)

    def service_context(self, duration_s: float = 12.0) -> dict[str, object]:
        assert self.channel is not None
        listing = self._raw_list()
        aggregate = listing["aggregate"]
        generation = int(aggregate.get("spacing_generation", "-1"), 0)
        boundary = self.channel.discard_pending("overnight_service_context")
        lines: list[str] = []
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            line = self.channel.read(deadline)
            if line is not None:
                lines.append(line)
        try:
            table = summarize_qos(
                lines,
                duration_s,
                current_generation=generation,
                nodes=tuple(NODES),
            )
            classifier = "VALID_ON_5000"
        except Exception as exc:
            table = {}
            classifier = f"REFUSED: {type(exc).__name__}: {exc}"
        return {
            "duration_s": duration_s,
            "boundary": boundary,
            "regime": aggregate,
            "classifier": classifier,
            "table": table,
            "gate_role": "context_only_never_blocks_capture",
        }

    def configure_once(self, nodes: tuple[str, ...]) -> dict[str, object]:
        results: dict[str, object] = {}
        for node in nodes:
            command = active_cfg(
                TAG_NUMBER[node], SLOT_MAP[node], count=11, beacon_win_n=1
            )
            try:
                row = self.send_tag_cfg_echo(node, command)
                row["behavioral_acceptance_authorized"] = row.get("completion") in (
                    "tag_timeout",
                    "reply_timeout",
                )
                results[node] = row
            except Exception as exc:
                results[node] = {
                    "completion": "dispatch_or_observation_failure",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            self.slot_map[node] = SLOT_MAP[node]
            self.win_map[node] = 1
            self.checkpoint()
        return results

    def alive_nodes(self) -> tuple[str, ...]:
        return tuple(node for node in NODES if self.alive.is_alive(node))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--hard-stop-hours", type=float, default=12.0)
    args = parser.parse_args()
    if args.evidence_root.exists():
        raise SystemExit(f"refusing existing evidence root: {args.evidence_root}")
    if args.hard_stop_hours <= 0:
        raise SystemExit("hard stop must be positive")

    runner = OvernightRunner(
        args.evidence_root, args.fusion_port, args.hard_stop_hours
    )
    runner.period_us = 100_000
    runner.slot_map = dict(SLOT_MAP)
    runner.win_map = {node: 1 for node in NODES}
    collector = None
    collector_handle = None
    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started": utc_now(),
        "remaining_capacity_context": True,
        "full_charge_endurance_record": False,
        "slot_map": SLOT_MAP,
        "hard_stop_hours": args.hard_stop_hours,
        "tag_master_activity": "none",
        "power_cycle": "none",
    }

    def request_stop(_signum, _frame) -> None:
        runner.stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        runner.open()
        try:
            ready = runner.wait_fleet_ready("ON", timeout_s=180.0)
            result["fusion_ready"] = ready
        except Exception as exc:
            result["fusion_ready_error"] = f"{type(exc).__name__}: {exc}"
            raw = runner._raw_list()
            result["fusion_ready_fallback"] = raw
            aggregate = raw["aggregate"]
            if set(raw["peers"]) and (
                aggregate.get("spacing") != "ON"
                or aggregate.get("spacing_us") != "5000"
            ):
                result["spacing_rebuild"] = runner.rebuild_spacing(
                    "overnight_required_on5000"
                )

        initial = runner.list_peers()
        result["initial_list"] = initial
        if not runner.alive_nodes():
            raise SessionError("fleet has zero live tags before capture")
        result["service_context"] = runner.service_context()

        # Beacon VCOM commands finish before the continuous listener collector
        # opens those ports.  No listener port is transmitted to afterwards.
        result["main_period110"] = runner.set_main_period(110, "overnight_entry")
        runner.period_us = 110_000
        time.sleep(10.0)
        result["field_before_collector"] = runner.fusion_snapshot(
            "field_before_collector"
        )

        collector, collector_handle, listener_dir = start_listener_collector(
            args.evidence_root,
            label="continuous_listener_capture",
            duration_s=args.hard_stop_hours * 3600.0 + 1800.0,
        )
        runner.listener_collector_active = True
        runner.listener_dir = listener_dir
        try:
            result["listener_preflight"] = wait_listener_preflight(
                listener_dir, collector, timeout_s=25.0
            )
        except Exception as exc:
            result["listener_preflight_error"] = f"{type(exc).__name__}: {exc}"

        live_at_cfg = runner.alive_nodes()
        result["tag_cfg110"] = runner.configure_once(live_at_cfg)
        result["behavioral_entry"] = runner.capture("entry_behavior_30s", 30.0)
        result["behavioral_entry_records"] = {
            node: result["behavioral_entry"]["records"].get(node, 0)
            for node in live_at_cfg
        }
        if not any(result["behavioral_entry_records"].values()):
            raise SessionError("fleet has zero live tags after active CFG")

        result["imu_start"] = runner.start_imu10()
        result["imu_behavior_smoke"] = runner.capture("imu_behavior_20s", 20.0)

        # The following boundary prospectively designates the first six
        # five-minute chunks as W.  Fusion and listener raw streams remain open
        # continuously across snapshots and every later endurance chunk.
        assert runner.channel is not None
        result["w_boundary"] = runner.channel.discard_pending("W_start")
        result["w_started"] = utc_now()
        result["w_before"] = runner.fusion_snapshot("w_before")
        runner.started_monotonic = time.monotonic()
        runner.hard_deadline = (
            runner.started_monotonic + args.hard_stop_hours * 3600.0
        )

        chunks: list[dict[str, object]] = []
        w_chunk_count = 6
        terminal_reason = "hard_stop"
        while time.monotonic() < runner.hard_deadline and not runner.stop_requested:
            live_before = runner.alive_nodes()
            if not live_before:
                terminal_reason = "fleet_death"
                break
            duration = min(300.0, runner.hard_deadline - time.monotonic())
            capture = runner.capture(f"capture_{len(chunks):03d}", duration)
            zero = [node for node in live_before if capture["records"].get(node, 0) == 0]
            if zero:
                event = {
                    "utc": utc_now(),
                    "chunk": len(chunks),
                    "nodes": zero,
                    "kind": "zero_progress_alarm",
                }
                runner.zero_progress_events.append(event)
            try:
                snapshot = runner.fusion_snapshot(
                    f"snapshot_{len(chunks):03d}"
                )
                snapshot_error = None
            except Exception as exc:
                snapshot = None
                snapshot_error = f"{type(exc).__name__}: {exc}"
            collector_rc = collector.poll() if collector is not None else None
            row = {
                "index": len(chunks),
                "capture": capture,
                "snapshot_index": (
                    len(runner.snapshots) - 1 if snapshot is not None else None
                ),
                "snapshot_error": snapshot_error,
                "alive_before": live_before,
                "alive_after": runner.alive_nodes(),
                "zero_progress": zero,
                "host_drain": runner.channel.health_snapshot(),
                "listener_collector_returncode": collector_rc,
            }
            chunks.append(row)
            result["chunks"] = chunks
            result["zero_progress_events"] = runner.zero_progress_events
            if len(chunks) == w_chunk_count:
                result["w_ended"] = utc_now()
                result["w_chunk_indices"] = list(range(w_chunk_count))
                print("W WINDOW COMPLETE — ENDURANCE CONTINUES", flush=True)
            write_json(args.evidence_root / "OVERNIGHT_RUN_STATE.json", result)
            runner.checkpoint()
            print(
                f"CHUNK {len(chunks):03d} alive={len(runner.alive_nodes())} "
                f"zero={','.join(zero) if zero else '-'}",
                flush=True,
            )

        if runner.stop_requested:
            terminal_reason = "external_signal"
        elif time.monotonic() >= runner.hard_deadline:
            terminal_reason = "hard_stop_12h"
        result["terminal_reason"] = terminal_reason
        result["capture_ended"] = utc_now()
        result["cleanup"] = runner.cleanup(terminal_reason)
        runner.listener_collector_active = False
        result["listener_collector"] = stop_listener_collector(
            collector, collector_handle
        )
        collector = None
        collector_handle = None
        result["terminal_main100"] = runner.set_main_period(
            100, "overnight_terminal"
        )
        runner.period_us = 100_000
        result["status"] = "CAPTURE_COMPLETE"
    except Exception as exc:
        result["status"] = "ABORTED"
        result["error"] = f"{type(exc).__name__}: {exc}"
        try:
            result["cleanup"] = runner.cleanup("exception_terminal")
        except Exception as cleanup_exc:
            result["cleanup_error"] = (
                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            )
        if collector is not None or collector_handle is not None:
            runner.listener_collector_active = False
            result["listener_collector"] = stop_listener_collector(
                collector, collector_handle
            )
            collector = None
            collector_handle = None
        try:
            result["terminal_main100"] = runner.set_main_period(
                100, "overnight_exception_terminal"
            )
        except Exception as main_exc:
            result["terminal_main100_error"] = (
                f"{type(main_exc).__name__}: {main_exc}"
            )
    finally:
        result["ended"] = utc_now()
        if runner.channel is not None:
            result["host_drain_final"] = runner.channel.health_snapshot()
        args.evidence_root.mkdir(parents=True, exist_ok=True)
        write_json(args.evidence_root / "OVERNIGHT_RUN_STATE.json", result)
        runner.summary["relay8_1_overnight"] = result
        runner.checkpoint()
        if runner.channel is not None:
            runner.channel.close()
        if runner.raw is not None:
            runner.raw.close()

    return 0 if result["status"] == "CAPTURE_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
