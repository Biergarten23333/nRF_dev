#!/usr/bin/env python3
"""Run relay8.2 S6 and S7 at COUNT=12/120 ms, then stop safely.

This runner has no S8/endurance path by design.  Every exit attempts the
guaranteed terminal: IMU STOP, composed idle for all ten tags, 90 s quiet
witness, main beacon back at 100 ms, and closed capture streams.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import analyze_relay8_1_overnight as ana
import batch_g_overnight as bg
from batch_g_day_h3 import SLOT_MAP
from batch_g_overnight import NODES, TAG_NUMBER, u32_delta, utc_now, write_json
from fusion_session import SessionError, parse_fields, parse_reply
from listener_array_run import wait_listener_preflight
from r4_final_capture import start_listener_collector, stop_listener_collector
from relay8_1_overnight_run import OvernightRunner
from relay8_2_guard_slot_verification import (
    PERIOD_US,
    analyze_guard,
    configure_guard,
    guard_active_cfg,
    main_period,
)
from relay8_2_s3_fix_verification import fields_from_status, status_metrics


MASTER_MARKER = "dk-fusion-imu-relay-v29"
# Two-sided tolerance derived from the compliant S7 W spread (8.327-8.333 Hz)
# plus one 30-minute count quantum.  The centre comes from configuration.
RATE_TOLERANCE_HZ = 0.007
IMU_RATE_GATE_HZ = 199.8
QUERY_PERIOD_S = 10.0
DATA_LIVENESS_S = 2.0
bg.MASTER_MARKER = MASTER_MARKER


def command_expect(runner: OvernightRunner, command: str, prefix: str) -> str:
    assert runner.channel is not None
    runner.channel.send(command)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        line = runner.channel.read(deadline)
        if line and line.startswith(prefix):
            return line
    raise SessionError(f"timeout waiting for {prefix!r} after {command!r}")


def require_full_service(runner: OvernightRunner, label: str) -> dict[str, object]:
    row = runner.service_context(12.0)
    write_json(runner.root / f"{label}_service_gate.json", row)
    if row.get("classifier") != "VALID_ON_5000":
        raise SessionError(f"service classifier refused: {row.get('classifier')}")
    table = row.get("table", {})
    bad = {
        node: table.get(node)
        for node in NODES
        if not isinstance(table.get(node), dict)
        or table[node].get("class") != "FULL"
    }
    if bad:
        raise SessionError(f"service gate not ten x FULL: {bad}")
    row["gate"] = "PASS"
    row["redraws"] = 0
    return row


def capture_full(
    runner: OvernightRunner, root: Path, label: str, duration_s: float
) -> dict[str, object]:
    """Capture both planes while polling BEACON_STATUS without blocking."""
    assert runner.channel is not None
    channel = runner.channel
    started = time.monotonic()
    deadline = started + duration_s
    next_send = {
        node: started + index * (QUERY_PERIOD_S / len(NODES))
        for index, node in enumerate(NODES)
    }
    next_cfg_send = {node: value + 0.5 for node, value in next_send.items()}
    provisioning_state: dict[str, str] = {}
    status_series: dict[str, list[dict[str, object]]] = {node: [] for node in NODES}
    query_rows: list[dict[str, object]] = []
    records: Counter[str] = Counter()
    imu_records: Counter[str] = Counter()
    imu_samples: Counter[str] = Counter()
    malformed: list[str] = []
    disconnects: list[str] = []
    silent_events: list[dict[str, object]] = []
    silent_active: set[str] = set()
    last_uwb = {node: started for node in NODES}
    last_imu = {node: started for node in NODES}
    decoder_before = channel.binary_decoder.errors
    last_progress = started

    while time.monotonic() < deadline:
        now = time.monotonic()
        for node in [node for node in NODES if next_send[node] <= now]:
            command = f"{node} TAG RAW BEACON_STATUS"
            channel.send(command)
            query_rows.append({"node": node, "sent_monotonic": time.monotonic(), "command": command})
            next_send[node] += QUERY_PERIOD_S
        for node in [node for node in NODES if next_cfg_send[node] <= now]:
            command = f"{node} TAG RAW CFG_STATUS"
            channel.send(command)
            query_rows.append({"node": node, "sent_monotonic": time.monotonic(), "command": command})
            next_cfg_send[node] += QUERY_PERIOD_S
        wake = min(deadline, min(next_send.values()), min(next_cfg_send.values()), time.monotonic() + 0.25)
        line = channel.read(wake)
        if line is not None:
            fields = parse_fields(line)
            node = fields.get("name")
            if node in NODES:
                runner.alive.seen(node, time.monotonic())
            if line.startswith("FUSION_UWB ") and node in NODES:
                records[node] += 1
                last_uwb[node] = time.monotonic()
                silent_active.discard(node)
            elif line.startswith("FUSION_IMU ") and node in NODES:
                n = int(fields.get("n", "0"), 0)
                imu_records[node] += 1
                imu_samples[node] += n
                last_imu[node] = time.monotonic()
                silent_active.discard(node)
            elif line.startswith("FUSION_DISCONNECTED "):
                disconnects.append(line)
            elif line.startswith("FUSION_MALFORMED "):
                malformed.append(line)
            reply = parse_reply(line)
            if (
                reply is not None
                and node in NODES
                and reply.source == "TAG"
                and reply.text.startswith("BEACON ")
            ):
                status_series[node].append(
                    {
                        "received_monotonic": time.monotonic(),
                        "correlation": reply.correlation,
                        "text": reply.text,
                        "fields": fields_from_status(reply.text),
                        "sweep": runner.latest_sweep.get(node),
                    }
                )
            if (reply is not None and node in NODES and reply.source == "TAG"
                    and reply.text.startswith("CFG ")):
                reply_fields = parse_fields(reply.text)
                provisioning_state[node] = reply_fields.get("state", "UNKNOWN")

        now = time.monotonic()
        for node in NODES:
            if now - last_uwb[node] > DATA_LIVENESS_S and now - last_imu[node] > DATA_LIVENESS_S and node not in silent_active:
                silent_active.add(node)
                expected_silent = provisioning_state.get(node) in {
                    "UNPROVISIONED", "COMMANDED_IDLE", "AWAITING_BEACON"
                }
                silent_events.append(
                    {
                        "utc": utc_now(),
                        "node": node,
                        "uwb_silent_s": now - last_uwb[node],
                        "imu_silent_s": now - last_imu[node],
                        "ble_alive_book": runner.alive.is_alive(node),
                        "host_health": channel.health_snapshot(),
                        "provisioning_state": provisioning_state.get(node, "UNKNOWN"),
                        "suppressed": expected_silent,
                    }
                )
                print(
                    f"{'DATA_PLANE_SILENT_INFO' if expected_silent else 'DATA_PLANE_SILENT'} node={node} threshold_s={DATA_LIVENESS_S:.1f} "
                    f"uwb_s={now - last_uwb[node]:.3f} imu_s={now - last_imu[node]:.3f}",
                    flush=True,
                )
                channel.send("LIST")
        if now - last_progress >= 60.0:
            elapsed = now - started
            print(
                f"{label} progress {elapsed:.0f}/{duration_s:.0f}s "
                f"uwb_min={min(records.values() or [0])} "
                f"imu_samples_min={min(imu_samples.values() or [0])}",
                flush=True,
            )
            last_progress = now

    ended = time.monotonic()
    result = {
        "label": label,
        "started_monotonic": started,
        "ended_monotonic": ended,
        "started_utc": utc_now(),
        "duration_s": ended - started,
        "query_period_s_per_node": QUERY_PERIOD_S,
        "query_rows": query_rows,
        "status_series": status_series,
        "records": dict(records),
        "imu_records": dict(imu_records),
        "imu_samples": dict(imu_samples),
        "disconnects": disconnects,
        "malformed": malformed,
        "silent_events": silent_events,
        "decoder_errors": channel.binary_decoder.errors - decoder_before,
        "host_drain": channel.health_snapshot(),
    }
    write_json(root / f"{label}_capture.json", result)
    return result


def parse_full_metrics(
    root: Path,
    capture: dict[str, object],
    listener_dir: Path,
    *,
    led_before: str,
    led_after: str,
    configured_period_us: int = PERIOD_US,
    post_ota_same_power_cycle: bool = False,
) -> dict[str, object]:
    """Extend the proven 120-ms UWB analysis with IMU and transport gates."""
    # analyze_guard provides tag-domain rate, epoch matching, window miss,
    # rxarm and main/sub field accounting at the correct 120 ms grid.
    base = analyze_guard(root, capture, listener_dir)
    start = float(capture["started_monotonic"])
    end = float(capture["ended_monotonic"])
    imu_batches: dict[str, list[dict[str, int]]] = defaultdict(list)
    queues: dict[str, dict[str, dict[str, int]]] = {}
    telemetry: dict[str, dict[str, dict[str, int]]] = {}
    sweep_continuity: dict[str, list[dict[str, object]]] = defaultdict(list)
    last_sweep: dict[str, tuple[float, int]] = {}
    for host, line in ana.iter_fusion(root / "fusion_cdc.log", start, end):
        fields = parse_fields(line)
        node = fields.get("name")
        if node not in NODES:
            continue
        if "FUSION_UWB " in line and "sweep" in fields:
            sweep = int(fields["sweep"], 0)
            previous = last_sweep.get(node)
            if previous is not None and sweep < previous[1]:
                sweep_continuity[node].append({
                    "host_monotonic": host,
                    "before": previous[1],
                    "after": sweep,
                })
            last_sweep[node] = (host, sweep)
        elif "FUSION_IMU " in line:
            samples = ana.parse_imu(fields)
            imu_batches[node].append(
                {
                    "seq": int(fields.get("seq", "0"), 0),
                    "n": int(fields.get("n", "0"), 0),
                    "base": int(fields.get("base_us", "0"), 0),
                    "last_offset": samples[-1][0] if samples else 0,
                }
            )
        elif "FUSION_QUEUE " in line:
            ana.first_last_update(queues, node, fields, ana.QUEUE_GATES)
        elif "FUSION_TELEMETRY " in line:
            ana.first_last_update(
                telemetry,
                node,
                fields,
                ana.HARD_TELEMETRY + ("imu_i2c_err", "imu_hreset"),
            )

    gate_rows: dict[str, object] = {}
    for node in NODES:
        batches = imu_batches[node]
        delivered = sum(row["n"] for row in batches)
        gaps = 0
        missing = 0
        for left, right in zip(batches, batches[1:]):
            expected = (left["seq"] + left["n"]) & 0xFFFF
            delta = (right["seq"] - expected) & 0xFFFF
            if delta:
                gaps += 1
                if delta < 0x8000:
                    missing += delta
        if len(batches) >= 2:
            first_us = batches[0]["base"]
            last_us = batches[-1]["base"] + batches[-1]["last_offset"]
            imu_hz = (delivered - 1) / ((last_us - first_us) / 1e6) if last_us > first_us else 0.0
        else:
            imu_hz = 0.0
        q_delta = ana.deltas(queues.get(node, {"first": {}, "last": {}}), ana.QUEUE_GATES)
        t_delta = ana.deltas(
            telemetry.get(node, {"first": {}, "last": {}}),
            ana.HARD_TELEMETRY + ("imu_i2c_err", "imu_hreset"),
        )
        gated_telemetry = tuple(key for key in ana.HARD_TELEMETRY if key != "reorder")
        uwb = base["nodes"][node]
        nominal_hz = 1_000_000.0 / configured_period_us
        reorder_gate_applies = not post_ota_same_power_cycle
        node_pass = (
            nominal_hz - RATE_TOLERANCE_HZ <= float(uwb["tag_domain_rate_hz"])
            <= nominal_hz + RATE_TOLERANCE_HZ
            and float(uwb["delta_mod16_plus1_fraction"]) >= 0.999
            and float(uwb["listener_absolute_epoch_exact_fraction"]) == 1.0
            and uwb["status"].get("miss_fraction") is not None
            and float(uwb["status"]["miss_fraction"]) < 0.01
            and uwb["status"].get("rxarm_delta") == 0
            and imu_hz >= IMU_RATE_GATE_HZ
            and gaps == 0
            and all(q_delta.get(key, 0) == 0 for key in ana.QUEUE_GATES)
            and all(t_delta.get(key, 0) == 0 for key in gated_telemetry)
            and not sweep_continuity[node]
            and (not reorder_gate_applies or t_delta.get("reorder", 0) == 0)
        )
        gate_rows[node] = {
            **uwb,
            "imu_samples": delivered,
            "imu_rate_hz": imu_hz,
            "imu_gap_events": gaps,
            "imu_missing_samples": missing,
            "queue_deltas": q_delta,
            "telemetry_deltas": t_delta,
            "configured_nominal_hz": nominal_hz,
            "rate_gate_low_hz": nominal_hz - RATE_TOLERANCE_HZ,
            "rate_gate_high_hz": nominal_hz + RATE_TOLERANCE_HZ,
            "sweep_discontinuities": sweep_continuity[node],
            "reorder_gate_applies": reorder_gate_applies,
            "pass": node_pass,
        }

    led_a = parse_fields(led_before)
    led_b = parse_fields(led_after)
    led_delta = {
        key: u32_delta(int(led_a[key], 0), int(led_b[key], 0))
        for key in ana.LED_COUNTERS
        if key in led_a and key in led_b
    }
    host_red = capture.get("host_drain", {}).get("red_markers", [])
    listener = base["listener_field"]
    global_pass = (
        all(bool(row["pass"]) for row in gate_rows.values())
        and not capture["disconnects"]
        and not capture["malformed"]
        and int(capture["decoder_errors"]) == 0
        and not host_red
        and not capture["silent_events"]
        and all(value == 0 for value in led_delta.values())
        and bool(listener.get("sub_slaved"))
        and listener.get("main_start_fail_fraction") is not None
        and float(listener["main_start_fail_fraction"]) <= 0.01
    )
    result = {
        "pass": global_pass,
        "nodes": gate_rows,
        "led_counter_deltas": led_delta,
        "disconnects": capture["disconnects"],
        "malformed": capture["malformed"],
        "decoder_errors": capture["decoder_errors"],
        "host_red_markers": host_red,
        "silent_events": capture["silent_events"],
        "listener_field": listener,
        "listener_audit": base["listener_audit"],
        "reorder_forensics": base["reorder_forensics"],
        "gate_scope": {
            "configured_period_us": configured_period_us,
            "post_ota_same_power_cycle": post_ota_same_power_cycle,
            "reorder_gate_applies": not post_ota_same_power_cycle,
        },
    }
    write_json(root / "FULL_LOAD_ANALYSIS.json", result)
    return result


def run_stage(
    root: Path,
    *,
    duration_s: float,
    label: str,
    fusion_port: str | None,
    rebuild_spacing: bool,
    layout: Path,
) -> tuple[dict[str, object], dict[str, object]]:
    root.mkdir(parents=True)
    runner = OvernightRunner(root, fusion_port, 1.5)
    runner.period_us = 100_000
    runner.slot_map = dict(SLOT_MAP)
    runner.win_map = {node: 1 for node in NODES}
    collector = None
    collector_handle = None
    listener_dir: Path | None = None
    state: dict[str, object] = {"stage": label, "started": utc_now(), "status": "IN_PROGRESS"}
    analysis: dict[str, object] = {"pass": False, "nodes": {}}
    cleanup: dict[str, object] = {"pass": False}
    try:
        runner.open()
        state["fleet"] = runner.wait_fleet_ready(None, 240.0)
        state["ledexpect"] = command_expect(runner, "LEDEXPECT 10", "LEDEXPECT ")
        if rebuild_spacing:
            state["spacing"] = runner.rebuild_spacing(f"{label.lower()}_entry")
        else:
            state["spacing"] = runner.wait_fleet_ready("ON", 180.0)
        runner.list_peers()
        state["service"] = require_full_service(runner, label.lower())
        state["main120"] = main_period(runner, 120, f"{label.lower()}_entry")
        time.sleep(12.0)
        state["cfg"] = configure_guard(runner)
        time.sleep(15.0)

        collector, collector_handle, listener_dir = start_listener_collector(
            root, label="continuous_listener_capture", duration_s=duration_s + 420.0
        )
        runner.listener_collector_active = True
        runner.listener_dir = listener_dir
        state["listener_preflight"] = wait_listener_preflight(listener_dir, collector, timeout_s=25.0)
        entry = capture_full(runner, root, f"{label.lower()}_entry_30s", 30.0)
        missing = sorted(set(NODES) - set(entry["records"]))
        unlocked = [
            node for node in NODES
            if not entry["status_series"][node]
            or entry["status_series"][node][-1]["fields"].get("lock") != "1"
        ]
        if missing or unlocked:
            raise SessionError(f"entry behavior failed missing={missing} unlocked={unlocked}")
        state["entry"] = entry
        state["imu_start"] = runner.start_imu10()
        failed_imu = [node for node, row in state["imu_start"].items() if row.get("status") != "PASS"]
        if failed_imu:
            raise SessionError(f"IMU START failed: {failed_imu}")
        state["ledclear"] = runner.ledclear()
        led_before = runner.ledstat()
        formal = capture_full(runner, root, label.lower(), duration_s)
        led_after = runner.ledstat()
        state["led_before"] = led_before
        state["led_after"] = led_after
        state["formal"] = formal
        # The array collector writes summary.json only on close.  Close its
        # formal witness now, before offline analysis consumes that summary.
        state["listener_stop"] = stop_listener_collector(collector, collector_handle)
        collector = collector_handle = None
        runner.listener_collector_active = False
        analysis = parse_full_metrics(
            root, formal, listener_dir, led_before=led_before, led_after=led_after
        )
        if label == "S6":
            ana.LAYOUT = layout
            products = root / "products"
            products.mkdir()
            positions, imu = ana.plot_products(
                root / "fusion_cdc.log",
                float(formal["started_monotonic"]),
                float(formal["ended_monotonic"]),
                products,
            )
            state["products"] = {"positions": positions, "imu": imu, "layout": str(layout)}
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if runner.channel is not None:
            try:
                cleanup = runner.cleanup(f"{label} guaranteed terminal")
            except Exception as exc:
                cleanup = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
        if collector is not None or collector_handle is not None:
            state["listener_stop"] = stop_listener_collector(collector, collector_handle)
            runner.listener_collector_active = False
        if runner.channel is not None:
            try:
                state["main100"] = main_period(runner, 100, f"{label.lower()}_terminal")
            except Exception as exc:
                state["main100"] = {"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}
            runner.channel.close()
            if runner.raw is not None:
                runner.raw.close()
        state["cleanup"] = cleanup
        cleanup_pass = cleanup.get("status") == "PASS" and state.get("main100", {}).get("decoded", {}).get("status") == "PASS"
        state["cleanup_pass"] = cleanup_pass
        state["status"] = "PASS" if analysis.get("pass") and cleanup_pass else "FAIL"
        state["ended"] = utc_now()
        write_json(root / "RUN_STATE.json", state)
    return analysis, state


def write_w_report(root: Path, result: dict[str, object], state: dict[str, object]) -> None:
    lines = [
        "# S7 W gate report",
        "",
        f"Date: {utc_now()}",
        "Configuration: COUNT=12, 120,000 us, slots 1-10, slot 11 empty, IMU 200 Hz batch 10.",
        "Field witness: two beacon telemetry streams plus five passive observers.",
        "",
        "| BSF | slot | UWB Hz | IMU Hz | IMU gaps | qdrop I/U | Δmod16 +1 | epoch exact | miss | rxarm Δ | verdict |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|",
    ]
    for node in sorted(NODES, key=lambda item: SLOT_MAP[item]):
        row = result.get("nodes", {}).get(node, {})
        status = row.get("status", {})
        q = row.get("queue_deltas", {})
        miss = status.get("miss_fraction")
        lines.append(
            f"| {node} | {SLOT_MAP[node]} | {float(row.get('tag_domain_rate_hz', 0)):.6f} | "
            f"{float(row.get('imu_rate_hz', 0)):.3f} | {row.get('imu_gap_events', '-')} | "
            f"{q.get('q_drop_imu', '-')}/{q.get('q_drop_uwb', '-')} | "
            f"{float(row.get('delta_mod16_plus1_fraction', 0)):.6f} | "
            f"{float(row.get('listener_absolute_epoch_exact_fraction', 0)):.6f} | "
            f"{float(miss) if miss is not None else float('nan'):.6f} | "
            f"{status.get('rxarm_delta', '-')} | {'PASS' if row.get('pass') else 'FAIL'} |"
        )
    verdict = "PASS" if result.get("pass") and state.get("cleanup_pass") else "FAIL"
    lines += [
        "",
        f"- DK LED counter deltas: `{result.get('led_counter_deltas')}`.",
        f"- Decoder errors: `{result.get('decoder_errors')}`; disconnects: `{len(result.get('disconnects', []))}`; host RED: `{result.get('host_red_markers')}`.",
        f"- Main delayed-start failure fraction: `{result.get('listener_field', {}).get('main_start_fail_fraction')}`.",
        f"- Sub remained SLAVED: `{result.get('listener_field', {}).get('sub_slaved')}`.",
        f"- Guaranteed terminal (IMU STOP + composed idle x10 + 90 s quiet + main 100 ms): `{'PASS' if state.get('cleanup_pass') else 'FAIL'}`.",
        "- S8/endurance: **DEFERRED; NOT STARTED**.",
        "",
        f"**W VERDICT: {verdict}**",
    ]
    if verdict == "PASS":
        lines += ["", "**the ten-node full-load qualification is CLOSED at batch 10, all ten gated, no waivers.**"]
    (root / "W_GATE_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--layout", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--s6-seconds", type=float, default=600.0)
    parser.add_argument("--s7-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    if args.evidence_root.exists():
        raise SystemExit(f"refusing existing evidence root: {args.evidence_root}")
    if not args.layout.is_file():
        raise SystemExit(f"missing layout: {args.layout}")
    args.evidence_root.mkdir(parents=True)
    overall = {"started": utc_now(), "s8": "DEFERRED_NOT_STARTED"}
    s6, s6_state = run_stage(
        args.evidence_root / "s6",
        duration_s=args.s6_seconds,
        label="S6",
        fusion_port=args.fusion_port,
        rebuild_spacing=True,
        layout=args.layout,
    )
    overall["s6"] = {"pass": s6.get("pass"), "cleanup_pass": s6_state.get("cleanup_pass")}
    # S6's prompt explicitly defines data products, not acceptance gates.
    # Its gate-shaped diagnostics are carried into the report, while only a
    # missing formal window/products or a failed safe terminal blocks S7.
    s6_complete = (
        s6_state.get("cleanup_pass")
        and "formal" in s6_state
        and "products" in s6_state
    )
    overall["s6"]["complete"] = bool(s6_complete)
    if not s6_complete:
        overall["status"] = "STOPPED_S6_FAIL"
        write_json(args.evidence_root / "S6_S7_STATE.json", overall)
        return 1
    s7, s7_state = run_stage(
        args.evidence_root / "s7",
        duration_s=args.s7_seconds,
        label="S7",
        fusion_port=args.fusion_port,
        rebuild_spacing=False,
        layout=args.layout,
    )
    write_w_report(args.evidence_root / "s7", s7, s7_state)
    overall["s7"] = {"pass": s7.get("pass"), "cleanup_pass": s7_state.get("cleanup_pass")}
    overall["status"] = "PASS_WAITING_S8" if s7.get("pass") and s7_state.get("cleanup_pass") else "STOPPED_S7_FAIL"
    overall["ended"] = utc_now()
    write_json(args.evidence_root / "S6_S7_STATE.json", overall)
    return 0 if overall["status"] == "PASS_WAITING_S8" else 1


if __name__ == "__main__":
    raise SystemExit(main())
