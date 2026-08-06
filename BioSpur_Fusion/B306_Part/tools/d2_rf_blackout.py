#!/usr/bin/env python3
"""D2 ten-minute blind RF-blackout capture with passive listeners."""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from d1_blind_disturbance import SLOTS, bounded_tag_read
from fusion_session import parse_fields, resolve_fusion_port
from listener_array_run import wait_listener_preflight

ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = ROOT / "B306_Part/host/listener_array_collector.py"
ACTIVE_S = 2.0


def wall() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def stamp() -> str:
    return f"mono={time.monotonic():.6f} wall={wall()}"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def wait_v30_master(channel: ThreadedLineChannel) -> str:
    channel.send("MASTER STATUS")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if not line or not line.startswith("FUSION_MASTER_STATUS "):
            continue
        fields = parse_fields(line)
        if fields.get("marker") != "dk-fusion-imu-relay-v30":
            raise RuntimeError(f"Fusion Master marker mismatch: {line}")
        if fields.get("count") != "10" or fields.get("ready") != "10":
            raise RuntimeError(f"Fusion fleet not 10/10 ready: {line}")
        return line
    raise RuntimeError("Fusion Master status timed out")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--duration-s", type=float, default=600.0)
    args = ap.parse_args()
    root = args.root
    root.mkdir(parents=True, exist_ok=True)
    capture = root / ("capture_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    capture.mkdir(exist_ok=False)
    listener_dir = capture / "listener_array"
    summary_path = capture / "d2_capture.json"
    result: dict[str, object] = {
        "status": "SETUP", "setup_started_wall": wall(),
        "duration_s": args.duration_s, "preflight": {}, "minute_ticks": [],
        "events": [], "read_only_during_window": True,
    }
    collector_log = (capture / "listener_collector.stdout.log").open("x", encoding="utf-8", buffering=1)
    cdc_log = (capture / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1)
    collector = subprocess.Popen(
        [sys.executable, str(COLLECTOR), "--out-dir", str(listener_dir),
         "--duration", str(args.duration_s + 180.0), "--baud", "460800",
         "--require-kind", "LSTAT", "--require-kind", "LPD", "--require-kind", "LRD"],
        cwd=ROOT, stdout=collector_log, stderr=subprocess.STDOUT, text=True,
    )
    channel = None
    abort = None
    try:
        result["preflight"]["listeners"] = wait_listener_preflight(listener_dir, collector, 30.0)
        channel = ThreadedLineChannel(
            resolve_fusion_port(None), cdc_log, "FUSION", decoded_queue_records=1048576,
            backlog_red_records=131072, raw_backlog_red_bytes=131072, stall_red_s=2.0,
        )
        channel.transport_mode = "binary"
        channel.text_pending.clear()
        result["preflight"]["decode_guard"] = decode_guard(channel, 15.0)
        result["preflight"]["master"] = wait_v30_master(channel)
        nodes = {}
        for node, slot in SLOTS.items():
            cfg = bounded_tag_read(channel, node, "CFG_STATUS", "CFG ")
            cf = parse_fields(cfg["reply"]["text"])
            beacon = bounded_tag_read(channel, node, "BEACON_STATUS", "BEACON ")
            bf = parse_fields(beacon["reply"]["text"])
            imu = b306_command(channel, node, "IMU STATUS", "IMU ")
            imf = parse_fields(imu["text"])
            expected = {"slot": f"{slot}/12", "period": "10", "sync": "1",
                        "stored": "1", "pslot": f"{slot}/12", "pperiod": "10", "psync": "1"}
            bad = {k: [cf.get(k), v] for k, v in expected.items() if cf.get(k) != v}
            if bad or bf.get("lock") != "1" or any(imf.get(k) != v for k, v in
                    {"active": "1", "rate": "200", "batch": "10"}.items()):
                raise RuntimeError(f"configuration assertion failed {node}: cfg={bad} beacon={bf} imu={imf}")
            nodes[node] = {"cfg": cf, "beacon": bf, "imu": imf}
        result["preflight"]["nodes"] = nodes
        result["preflight"]["contract"] = {
            "count": 12, "period_ms": 10, "slots": list(range(1, 11)), "slot_11": "empty",
            "main_beacon_us": 120000, "sub": "SLAVED", "tag_master": "operator-confirmed unplugged",
            "imu": "200 Hz / batch 10",
        }
        seen_u, seen_i = set(), set()
        live_deadline = time.monotonic() + 30.0
        while time.monotonic() < live_deadline and (len(seen_u) < 10 or len(seen_i) < 10):
            line = channel.read(live_deadline)
            if not line:
                continue
            fields = parse_fields(line); node = fields.get("name")
            if node in SLOTS and line.startswith("FUSION_UWB "): seen_u.add(node)
            if node in SLOTS and line.startswith("FUSION_IMU "): seen_i.add(node)
        if len(seen_u) != 10 or len(seen_i) != 10:
            raise RuntimeError(f"live assertion failed uwb={len(seen_u)}/10 imu={len(seen_i)}/10")

        opened = time.monotonic(); deadline = opened + args.duration_s
        result["window_open"] = {"monotonic": opened, "wall": wall()}
        result["status"] = "WINDOW_OPEN"
        write_json(summary_path, result)
        print(f"=== D2 WINDOW OPEN — 10:00 — PLACE ONE NON-BSFAA61 BOARD IN RF SHIELD WHENEVER YOU CHOOSE === {stamp()}", flush=True)
        print("Do not report which board or when. Leave the final 2 minutes undisturbed.", flush=True)
        last_u = {n: opened for n in SLOTS}; last_i = {n: opened for n in SLOTS}
        next_tick = opened + 60.0; next_health = opened + 10.0
        while time.monotonic() < deadline:
            now_m = time.monotonic()
            line = channel.read(min(deadline, next_tick, next_health, now_m + 0.5))
            now_m = time.monotonic()
            if line:
                f = parse_fields(line); node = f.get("name")
                if node in SLOTS and line.startswith("FUSION_UWB "): last_u[node] = now_m
                elif node in SLOTS and line.startswith("FUSION_IMU "): last_i[node] = now_m
                elif line.startswith(("FUSION_CONNECTED ", "FUSION_DISCONNECTED ")) or "TAG_RESET_DETECTED " in line:
                    result["events"].append({"monotonic": now_m, "wall": wall(), "line": line})
            if now_m >= next_health:
                free = shutil.disk_usage(root).free
                if free < 5 * 1024**3: abort = f"disk near full: {free} bytes free"
                elif all(now_m - max(last_u[n], last_i[n]) > 15 for n in SLOTS): abort = "all ten nodes gone"
                elif not channel._reader.is_alive(): abort = "Fusion Master down"
                elif collector.poll() is not None: abort = f"listener collector exited rc={collector.returncode}"
                if abort: break
                next_health += 10.0
            if now_m >= next_tick:
                minute = int(round((next_tick - opened) / 60)); remaining = 10 - minute
                un = sum(next_tick - last_u[n] <= ACTIVE_S for n in SLOTS)
                inn = sum(next_tick - last_i[n] <= ACTIVE_S for n in SLOTS)
                tick = {"minute": minute, "remaining_min": remaining, "uwb": un, "imu": inn,
                        "monotonic": next_tick, "wall": wall()}
                result["minute_ticks"].append(tick)
                print(f"D2 T+{minute:02d}:00 / 10:00 — {un}/10 ranging — {inn}/10 IMU — remaining {remaining:02d}:00", flush=True)
                next_tick += 60.0
        closed = time.monotonic()
        result["window_close"] = {"monotonic": closed, "wall": wall()}
        result["abort"] = abort
        result["status"] = "ABORTED" if abort else "CAPTURE_COMPLETE"
        print(f"=== D2 WINDOW CLOSED — capture complete — do not disturb further === {stamp()}", flush=True)
        if abort: print(f"D2 ABORT: {abort}", flush=True)
        result["host_health"] = channel.health_snapshot()
    except BaseException as exc:
        result["status"] = "FAILED_BEFORE_OR_DURING_WINDOW"
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if channel is not None:
            result.setdefault("host_health", channel.health_snapshot()); channel.close()
        if collector.poll() is None: collector.send_signal(signal.SIGINT)
        try: result["listener_return_code"] = collector.wait(timeout=30.0)
        except subprocess.TimeoutExpired:
            collector.terminate(); result["listener_return_code"] = collector.wait(timeout=10.0)
        cdc_log.close(); collector_log.close()
        result["ended_wall"] = wall()
        write_json(summary_path, result)
    return 2 if abort else 0


if __name__ == "__main__":
    raise SystemExit(main())
