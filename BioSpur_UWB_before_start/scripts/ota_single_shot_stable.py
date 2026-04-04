#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import serial
from serial import SerialException


FILTER_RE = re.compile(
    r"OTA target filter:\s*token=([-\d]+)\s+name=([^\s]+)\s+prefix=([^\s]+)\s+uuid=([0-9A-F\-]+|-)",
    re.IGNORECASE,
)
MODE_RE = re.compile(r"Control status:\s*mode=([A-Z]+)")
INIT_RE = re.compile(r"initiate rc=([-\d]+)")


@dataclass
class RunResult:
    port: str
    target_uuid: str
    mode_ota_seen: bool
    uuid_ack_seen: bool
    filter_uuid_seen: str
    filter_uuid_match: bool
    initiate_sent: bool
    initiate_rc: int | None
    ota_started: bool
    serial_lost: bool
    reason: str
    log_path: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Single-session stable OTA launcher (fail-fast).")
    p.add_argument("--port", required=True)
    p.add_argument("--target-uuid", required=True)
    p.add_argument("--out-dir", default="")
    p.add_argument("--timeout-s", type=float, default=90.0)
    p.add_argument("--baud", type=int, default=115200)
    return p.parse_args()


def send_cmd(ser: serial.Serial, cmd: str, logf, t0: float) -> None:
    ser.write((cmd + "\n").encode("utf-8"))
    logf.write(f"[HOST_CMD {time.time()-t0:7.2f}s] {cmd}\n")
    logf.flush()


def main() -> int:
    args = parse_args()
    target_uuid = args.target_uuid.strip().upper()
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path(f"logs/ota_single_shot_stable_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "single_shot.log"
    summary_path = out_dir / "summary.json"

    mode_ota_seen = False
    uuid_ack_seen = False
    filter_uuid_seen = ""
    filter_uuid_match = False
    initiate_sent = False
    initiate_rc: int | None = None
    ota_started = False
    serial_lost = False
    reason = ""

    t0 = time.time()
    ser = None
    try:
        ser = serial.Serial(
            args.port,
            args.baud,
            timeout=0.2,
            exclusive=True,
            dsrdtr=False,
            rtscts=False,
        )
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        with log_path.open("w", encoding="utf-8") as logf:
            logf.write(f"[HOST_EVT {time.time()-t0:7.2f}s] serial_opened_once port={args.port}\n")
            logf.flush()

            # Required order:
            # a) mode ota
            # b) set target uuid
            # c) print/read back target filter
            # d) verify exact UUID (not "-")
            # e) initiate
            phase = "wait_mode"
            mode_deadline = time.time() + 40.0
            run_deadline = time.time() + args.timeout_s
            filter_query_count = 0
            last_filter_query = 0.0
            mode_tx_count = 0
            last_mode_tx = 0.0

            while time.time() < run_deadline:
                if phase == "wait_mode":
                    now = time.time()
                    if mode_tx_count == 0 or (now - last_mode_tx > 1.8 and mode_tx_count < 20):
                        send_cmd(ser, "mode ota", logf, t0)
                        send_cmd(ser, "status", logf, t0)
                        mode_tx_count += 1
                        last_mode_tx = now
                try:
                    raw = ser.readline()
                except SerialException as e:
                    serial_lost = True
                    reason = f"serial_lost:{e}"
                    logf.write(f"[HOST_EVT {time.time()-t0:7.2f}s] {reason}\n")
                    logf.flush()
                    break

                if not raw:
                    if phase == "wait_mode" and time.time() > mode_deadline:
                        reason = "mode_ota_not_confirmed"
                        break
                    if phase == "wait_filter":
                        now = time.time()
                        if now - last_filter_query > 1.8 and filter_query_count < 4:
                            send_cmd(ser, "device kind anchor", logf, t0)
                            send_cmd(ser, "status", logf, t0)
                            filter_query_count += 1
                            last_filter_query = now
                    continue

                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                logf.write(line + "\n")
                logf.flush()

                mm = MODE_RE.search(line)
                if mm and mm.group(1).upper() == "OTA":
                    mode_ota_seen = True
                    if phase == "wait_mode":
                        send_cmd(ser, f"ota_target uuid {target_uuid}", logf, t0)
                        phase = "wait_uuid_ack"

                if "Control mode loaded: OTA" in line and phase == "wait_mode":
                    mode_ota_seen = True
                    send_cmd(ser, f"ota_target uuid {target_uuid}", logf, t0)
                    phase = "wait_uuid_ack"

                if "ota_target uuid rc=0" in line and phase == "wait_uuid_ack":
                    uuid_ack_seen = True
                    send_cmd(ser, "device kind anchor", logf, t0)
                    send_cmd(ser, "status", logf, t0)
                    filter_query_count = 1
                    last_filter_query = time.time()
                    phase = "wait_filter"

                mf = FILTER_RE.search(line)
                if mf:
                    filter_uuid_seen = mf.group(4).strip().upper()
                    filter_uuid_match = (filter_uuid_seen == target_uuid)
                    if phase == "wait_filter":
                        if filter_uuid_match:
                            send_cmd(ser, "initiate", logf, t0)
                            initiate_sent = True
                            phase = "wait_initiate"
                        else:
                            reason = f"target_uuid_not_latched:{filter_uuid_seen}"
                            break

                mi = INIT_RE.search(line)
                if mi:
                    initiate_rc = int(mi.group(1))
                    if initiate_rc != 0:
                        reason = f"initiate_rc_{initiate_rc}"
                        break
                    phase = "wait_ota_start"

                if "OTA upload starting" in line:
                    ota_started = True
                    reason = "control_plane_stable_ota_started"
                    break

            if not reason:
                reason = "timeout_without_ota_start"
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    result = RunResult(
        port=args.port,
        target_uuid=target_uuid,
        mode_ota_seen=mode_ota_seen,
        uuid_ack_seen=uuid_ack_seen,
        filter_uuid_seen=filter_uuid_seen or "-",
        filter_uuid_match=filter_uuid_match,
        initiate_sent=initiate_sent,
        initiate_rc=initiate_rc,
        ota_started=ota_started,
        serial_lost=serial_lost,
        reason=reason,
        log_path=str(log_path),
    )
    summary_path.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(asdict(result), indent=2))

    if serial_lost:
        return 3
    if not mode_ota_seen:
        return 4
    if not uuid_ack_seen:
        return 5
    if not filter_uuid_match:
        return 6
    if initiate_rc is not None and initiate_rc != 0:
        return 7
    if not ota_started:
        return 8
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
