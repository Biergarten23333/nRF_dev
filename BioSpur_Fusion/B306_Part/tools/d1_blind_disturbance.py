#!/usr/bin/env python3
"""D1 supervised 30-minute blind disturbance capture."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from relay8_3_t567_provision import tag_read
from relay8_tag_control import wait_master_status

SLOTS = {
    "BSF3C79": 1, "BSFC2CC": 2, "BSF44AD": 3, "BSF6C53": 4,
    "BSF8BC4": 5, "BSF1120": 6, "BSF31CC": 7, "BSFAA61": 8,
    "BSFEC35": 9, "BSFB165": 10,
}
ACTIVE_S = 2.0
REJOIN_GAP_S = 3.0
IMU_OBSERVE_DELAY_S = 10.0


def wall() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def stamp() -> str:
    return f"mono={time.monotonic():.6f} wall={wall()}"


def bounded_tag_read(channel, node: str, command: str, prefix: str) -> dict:
    errors = []
    for attempt in range(1, 4):
        try:
            reply = tag_read(channel, node, command, prefix)
            reply["bounded_attempt"] = attempt
            return reply
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if attempt < 3:
                time.sleep(5.0)
    raise RuntimeError(f"{node} {command} exhausted bounded reads: {errors}")


def ensure_imu(channel, node: str) -> dict:
    status = b306_command(channel, node, "IMU STATUS", "IMU ")
    fields = parse_fields(status["text"])
    if fields.get("active") == "1" and fields.get("rate") == "200" and fields.get("batch") == "10":
        return {"already_correct": True, "status": status}
    if fields.get("active") == "1":
        raise RuntimeError(f"{node} IMU active with unexpected settings: {fields}")
    rate = b306_command(channel, node, "IMU RATE=200", "IMU RATE OK ")
    batch = b306_command(channel, node, "IMU BATCH=10", "IMU BATCH OK ")
    try:
        start = b306_command(channel, node, "IMU START", "IMU START OK ")
    except Exception as exc:
        # START is not retransmitted after an ambiguous acknowledgement. Read state instead.
        confirm = b306_command(channel, node, "IMU STATUS", "IMU ")
        confirmed = parse_fields(confirm["text"])
        if confirmed.get("active") != "1" or confirmed.get("rate") != "200" or confirmed.get("batch") != "10":
            raise
        return {"already_correct": False, "rate": rate, "batch": batch,
                "start_ack": f"{type(exc).__name__}: {exc}", "confirm": confirm}
    return {"already_correct": False, "rate": rate, "batch": batch, "start": start}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--duration-s", type=float, default=1800.0)
    args = ap.parse_args()
    root = args.root
    root.mkdir(parents=True, exist_ok=True)
    result = {
        "started_setup": wall(), "duration_requested_s": args.duration_s,
        "imu_reissue_delay_s": IMU_OBSERVE_DELAY_S, "events": [],
        "interventions": [], "minute_ticks": [], "preflight": {},
    }
    channel = None
    out = root / "d1_capture.json"
    log_path = root / "d1_capture.cdc.log"
    if out.exists() or log_path.exists():
        raise SystemExit("refusing existing D1 capture outputs")
    log = None
    try:
        log = log_path.open("x", encoding="utf-8", buffering=1)
        if True:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None), log, "FUSION",
                decoded_queue_records=1048576, backlog_red_records=131072,
                raw_backlog_red_bytes=131072, stall_red_s=2.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["preflight"]["decode_guard"] = decode_guard(channel, 15.0)
            result["preflight"]["master"] = wait_master_status(channel)

            cfg = {}
            beacon = {}
            for node, slot in SLOTS.items():
                c = bounded_tag_read(channel, node, "CFG_STATUS", "CFG ")
                cf = parse_fields(c["reply"]["text"])
                b = bounded_tag_read(channel, node, "BEACON_STATUS", "BEACON ")
                bf = parse_fields(b["reply"]["text"])
                cfg[node] = cf
                beacon[node] = bf
                expected = {
                    "slot": f"{slot}/12", "period": "10", "sync": "1",
                    "stored": "1", "pslot": f"{slot}/12",
                    "pperiod": "10", "psync": "1",
                }
                bad = {k: (cf.get(k), v) for k, v in expected.items() if cf.get(k) != v}
                if bad or bf.get("lock") != "1":
                    raise RuntimeError(f"configuration assertion failed {node}: {bad} beacon={bf}")
            result["preflight"]["cfg"] = cfg
            result["preflight"]["beacon"] = beacon
            result["preflight"]["field_contract"] = {
                "count": 12, "period_ms": 10, "slots": list(range(1, 11)),
                "tail_guard_slot": 11, "main_beacon_us": 120000,
                "sub": "SLAVED", "tag_master": "unplugged",
                "basis": "G5 terminal state plus ten locked BEACON_STATUS replies",
            }

            imu_setup = {}
            for node in SLOTS:
                imu_setup[node] = ensure_imu(channel, node)
            result["preflight"]["imu_setup"] = imu_setup

            # Configuration assertion includes live evidence from both streams.
            seen_uwb, seen_imu = set(), set()
            assertion_deadline = time.monotonic() + 30.0
            while time.monotonic() < assertion_deadline and (len(seen_uwb) < 10 or len(seen_imu) < 10):
                line = channel.read(assertion_deadline)
                if not line:
                    continue
                f = parse_fields(line)
                n = f.get("name")
                if n in SLOTS and line.startswith("FUSION_UWB "):
                    seen_uwb.add(n)
                elif n in SLOTS and line.startswith("FUSION_IMU "):
                    seen_imu.add(n)
            if len(seen_uwb) != 10 or len(seen_imu) != 10:
                raise RuntimeError(f"live assertion failed uwb={len(seen_uwb)} imu={len(seen_imu)}")

            open_mono = time.monotonic()
            deadline = open_mono + args.duration_s
            result["window_open"] = {"monotonic": open_mono, "wall": wall()}
            print(f"=== D1 WINDOW OPEN — 30:00 — DISTURB FREELY FROM NOW === {stamp()}", flush=True)
            print("disturb whichever boards you like, however many, at whatever times — but leave at least the last 3 minutes undisturbed", flush=True)
            print("do not say anything about what you did, during the run", flush=True)
            print("write down afterwards what you did and roughly when, for the detection comparison", flush=True)

            last_uwb = {n: open_mono for n in SLOTS}
            last_imu = {n: open_mono for n in SLOTS}
            last_sweep = {}
            outage_start = {}
            pending_intervention = {}
            next_tick = open_mono + 60.0
            next_disk = open_mono + 10.0
            abort = None
            while time.monotonic() < deadline:
                now_m = time.monotonic()
                read_until = min(deadline, next_tick, next_disk, now_m + 0.5)
                line = channel.read(read_until)
                now_m = time.monotonic()
                if line:
                    f = parse_fields(line)
                    node = f.get("name")
                    if node in SLOTS and line.startswith("FUSION_UWB "):
                        previous = last_uwb[node]
                        sweep = int(f.get("sweep", "0"), 0)
                        backward = node in last_sweep and sweep < last_sweep[node]
                        gap = now_m - previous
                        last_uwb[node] = now_m
                        last_sweep[node] = sweep
                        if node in outage_start or gap >= REJOIN_GAP_S or backward:
                            start = outage_start.pop(node, previous)
                            event = {
                                "node": node, "detected_by": "uwb_rejoin",
                                "stream_stop_mono": start, "first_uwb_mono": now_m,
                                "outage_s": now_m - start, "sweep_backward": backward,
                                "first_uwb_fields": f, "wall": wall(),
                            }
                            result["events"].append(event)
                            pending_intervention[node] = now_m + IMU_OBSERVE_DELAY_S
                    elif node in SLOTS and line.startswith("FUSION_IMU "):
                        last_imu[node] = now_m
                    elif line.startswith("FUSION_DISCONNECTED "):
                        if node in SLOTS:
                            outage_start.setdefault(node, now_m)
                        result["events"].append({"detected_by": "ble_disconnect", "node": node, "monotonic": now_m, "wall": wall(), "line": line})
                    elif line.startswith("FUSION_CONNECTED "):
                        result["events"].append({"detected_by": "ble_connect", "node": node, "monotonic": now_m, "wall": wall(), "line": line})
                    elif "TAG_RESET_DETECTED " in line:
                        result["events"].append({"detected_by": "tag_reset_detected", "node": node, "monotonic": now_m, "wall": wall(), "line": line})

                for node in SLOTS:
                    if now_m - last_uwb[node] >= REJOIN_GAP_S:
                        outage_start.setdefault(node, last_uwb[node])
                for node, due in list(pending_intervention.items()):
                    if now_m < due:
                        continue
                    autonomous = last_imu[node] >= due - IMU_OBSERVE_DELAY_S
                    row = {"node": node, "due_mono": due, "sent_mono": now_m, "wall": wall(), "autonomous_imu_seen": autonomous}
                    if not autonomous:
                        try:
                            row["rate"] = b306_command(channel, node, "IMU RATE=200", "IMU RATE OK ")
                            row["batch"] = b306_command(channel, node, "IMU BATCH=10", "IMU BATCH OK ")
                            row["start"] = b306_command(channel, node, "IMU START", "IMU START OK ")
                            row["status"] = "REISSUED"
                        except Exception as exc:
                            row["status"] = "FAILED"
                            row["error"] = f"{type(exc).__name__}: {exc}"
                    else:
                        row["status"] = "NOT_NEEDED_AUTONOMOUS"
                    result["interventions"].append(row)
                    del pending_intervention[node]

                if now_m >= next_disk:
                    free = shutil.disk_usage(root).free
                    if free < 5 * 1024**3:
                        abort = f"disk near full: {free} bytes free"
                        break
                    if all(now_m - max(last_uwb[n], last_imu[n]) > 15.0 for n in SLOTS):
                        abort = "all ten nodes gone"
                        break
                    if not channel._reader.is_alive():
                        abort = "Fusion Master down"
                        break
                    next_disk += 10.0
                if now_m >= next_tick:
                    minute = int((next_tick - open_mono) // 60)
                    uwb_n = sum(next_tick - last_uwb[n] <= ACTIVE_S for n in SLOTS)
                    imu_n = sum(next_tick - last_imu[n] <= ACTIVE_S for n in SLOTS)
                    remaining = max(0, int(round((deadline - next_tick) / 60)))
                    tick = {"minute": minute, "remaining_min": remaining, "uwb": uwb_n, "imu": imu_n, "monotonic": next_tick, "wall": wall()}
                    result["minute_ticks"].append(tick)
                    print(f"D1 T+{minute:02d}:00 / 30:00 — {uwb_n}/10 ranging — {imu_n}/10 IMU — remaining {remaining:02d}:00", flush=True)
                    next_tick += 60.0

            close_mono = time.monotonic()
            result["window_close"] = {"monotonic": close_mono, "wall": wall()}
            result["abort"] = abort
            result["status"] = "ABORTED" if abort else "CAPTURE_COMPLETE"
            print(f"=== D1 WINDOW CLOSED — capture complete — do not disturb further === {stamp()}", flush=True)
            if abort:
                print(f"D1 ABORT: {abort}", flush=True)
            result["host_health"] = channel.health_snapshot()
            channel.send("RESOURCES")
            resource_deadline = time.monotonic() + 5.0
            result["resources"] = []
            while time.monotonic() < resource_deadline:
                line = channel.read(resource_deadline)
                if line and line.startswith(("FUSION_RESOURCE_", "FUSION_STACK ")):
                    result["resources"].append(line)
            return 2 if abort else 0
    finally:
        if channel is not None:
            result.setdefault("host_health", channel.health_snapshot())
            channel.close()
        if log is not None:
            log.close()
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
