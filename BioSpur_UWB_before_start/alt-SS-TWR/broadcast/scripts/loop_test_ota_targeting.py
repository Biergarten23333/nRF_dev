#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import serial
from serial import SerialException

from master_control_port import (
    assert_not_jlink_when_biospur_available,
    preferred_master_control_port,
)


CONNECT_RE = re.compile(r"Connect start:\s+(.+?)\s+token=([-\d]+)\s+name=(.+)$")
CONNECTED_LINE_RE = re.compile(r"^Connected:\s+(.+)$")
EVIDENCE_RE = re.compile(r"Connected target evidence:\s+verified=(\d+)\s+uuid=([0-9A-F\-]+)\s+name=(.+?)\s+token=([-\d]+)")
SCAN_DECISION_RE = re.compile(r"Scan decision:\s+(.+?)\s+accept=(\d+).+target_uuid=([0-9A-F\-]+)")
STATE_RE = re.compile(r"OTA_STATE:([a-z_]+)\s+detail=(.+)$")


@dataclass
class TrialResult:
    trial: int
    selected_addr: str
    selected_uuid: str
    verified: bool
    ota_started: bool
    ota_completed: bool
    blocked_identity: bool
    post_ota_rediscovered: bool
    post_ota_target_present: bool
    target_match: bool
    wrong_target_started: bool
    recv_bg_interference: bool
    first_upload_tx_seen: bool
    first_upload_rsp_seen: bool
    upload_progressed: bool
    op_states: list[str]
    notes: str
    log_path: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Repeat OTA targeting runs and verify identity-safe convergence.")
    p.add_argument(
        "--port",
        default=preferred_master_control_port(
            "/dev/serial/by-id/usb-SEGGER_J-Link_000683234364-if00"
        ),
    )
    p.add_argument("--target-uuid", required=True, help="32-hex stable UUID of intended target")
    p.add_argument("--target-name", default="", help="Optional exact name constraint")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--trial-timeout-s", type=float, default=120.0)
    p.add_argument("--flash-image", default="build-master-control-anchor-ota-20260331f/master_control/zephyr/zephyr.hex")
    p.add_argument("--skip-flash", action="store_true")
    p.add_argument("--out-dir", default="")
    p.add_argument("--direct-ota-mode", action="store_true",
                   help="Skip preflip through RECV mode; switch/use OTA mode directly.")
    args = p.parse_args()
    assert_not_jlink_when_biospur_available(args.port)
    return args


def run_cmd(cmd: list[str], log_path: Path) -> int:
    with log_path.open("w", encoding="utf-8") as f:
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=False)
    return proc.returncode


def scan_snapshot(path: Path) -> dict[str, dict[str, str]]:
    with path.open("w", encoding="utf-8") as out:
        proc = subprocess.run(["python3", "scripts/scan_and_map.py", "--timeout-s", "8"], stdout=out, stderr=subprocess.STDOUT, check=False)
    if proc.returncode != 0:
        return {}
    rows: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            uuid = (row.get("DEVICE_UUID") or "").strip().upper()
            if uuid:
                rows[uuid] = row
    return rows


def run_trial(args: argparse.Namespace, out_dir: Path, trial: int) -> TrialResult:
    trial_dir = out_dir / f"trial_{trial:02d}"
    trial_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_flash:
        flash_log = trial_dir / "flash_52840.log"
        rc = run_cmd(["scripts/flash_master_noninteractive.sh", args.flash_image], flash_log)
        if rc != 0:
            return TrialResult(
                trial=trial,
                selected_addr="",
                selected_uuid="",
                verified=False,
                ota_started=False,
                ota_completed=False,
                blocked_identity=False,
                post_ota_rediscovered=False,
                post_ota_target_present=False,
                target_match=False,
                wrong_target_started=False,
                recv_bg_interference=False,
                first_upload_tx_seen=False,
                first_upload_rsp_seen=False,
                upload_progressed=False,
                op_states=[],
                notes=f"flash_failed rc={rc}",
                log_path=str(flash_log),
            )

    pre_scan = scan_snapshot(trial_dir / "scan_pre.csv")

    target_uuid = args.target_uuid.upper().strip()
    selected_addr = ""
    selected_uuid = ""
    verified = False
    ota_started = False
    ota_completed = False
    blocked_identity = False
    ota_failed = False
    recv_bg_interference = False
    first_upload_tx_seen = False
    first_upload_rsp_seen = False
    upload_progressed = False
    op_states: list[str] = []
    notes = ""
    log_path = trial_dir / "ota_trial.log"

    with log_path.open("w", encoding="utf-8") as log:
        t0 = time.time()
        current_mode = ""
        initiate_sent = False
        initiate_ack_seen = False
        device_anchor_sent = False
        device_anchor_ack = False
        device_anchor_last_tx = 0.0
        device_anchor_retry = 0
        mode_recv_sent = False
        mode_ota_sent = False
        mode_ota_ack = False
        mode_ota_last_tx = 0.0
        mode_ota_retry = 0
        recv_loaded = False
        ota_loaded = False
        target_cfg_sent = False
        cfg_phase = 0
        cfg_phase_started_at = 0.0
        cfg_last_tx_at = 0.0
        cfg_retry_count = 0
        direct_status_last_tx = 0.0
        ota_session_active = False
        s = None

        while time.time() - t0 < args.trial_timeout_s:
            if s is None:
                try:
                    s = serial.Serial(args.port, 115200, timeout=0.2)
                    s.reset_input_buffer()
                    s.reset_output_buffer()
                    log.write(f"[HOST_EVT {time.time()-t0:7.2f}s] serial_opened port={args.port}\n")
                    log.flush()
                    time.sleep(0.2)
                except SerialException as e:
                    log.write(f"[HOST_EVT {time.time()-t0:7.2f}s] serial_open_wait err={e}\n")
                    log.flush()
                    time.sleep(0.4)
                    continue

            rel = time.time() - t0
            try:
                if (not device_anchor_ack) and rel >= 0.2:
                    now = time.time()
                    if (not device_anchor_sent) or (
                        now - device_anchor_last_tx >= 1.5 and device_anchor_retry < 8
                    ):
                        s.write(b"device kind anchor\n")
                        log.write(f"[HOST_CMD {rel:7.2f}s] device kind anchor\n")
                        log.flush()
                        device_anchor_sent = True
                        device_anchor_last_tx = now
                        device_anchor_retry += 1

                if (not mode_recv_sent) and rel >= 0.8:
                    if args.direct_ota_mode:
                        mode_recv_sent = True
                    else:
                        s.write(b"mode recv\n")
                        log.write(f"[HOST_CMD {rel:7.2f}s] mode recv\n")
                        log.flush()
                        mode_recv_sent = True

                line = s.readline().decode("utf-8", errors="replace").rstrip("\r\n")
            except SerialException as e:
                log.write(f"[HOST_EVT {time.time()-t0:7.2f}s] serial_lost err={e}\n")
                log.flush()
                try:
                    s.close()
                except Exception:
                    pass
                s = None
                time.sleep(0.4)
                continue

            if not line:
                continue
            log.write(line + "\n")
            log.flush()

            m = CONNECT_RE.search(line)
            if m:
                selected_addr = m.group(1).strip()
            m_conn = CONNECTED_LINE_RE.search(line)
            if m_conn and not selected_addr:
                selected_addr = m_conn.group(1).strip()
            m2 = EVIDENCE_RE.search(line)
            if m2:
                verified = m2.group(1) == "1"
                selected_uuid = m2.group(2).strip().upper()
            m3 = STATE_RE.search(line)
            if m3:
                st = m3.group(1).strip()
                if not op_states or op_states[-1] != st:
                    op_states.append(st)
                if st == "ota_uploading":
                    ota_started = True
            if "OTA session acquired" in line:
                ota_session_active = True
            if "OTA session released" in line:
                ota_session_active = False
            if "OTA first IMG_UPLOAD tx prep" in line:
                first_upload_tx_seen = True
            if "OTA rsp part:" in line and "cmd=0x01" in line:
                first_upload_rsp_seen = True
            if "OTA upload progress:" in line:
                try:
                    pct = int(line.split("OTA upload progress:", 1)[1].split("%", 1)[0].strip())
                    if pct >= 2:
                        upload_progressed = True
                except Exception:
                    pass
            if "OTA upload ack:" in line:
                m_off = re.search(r"off=(\d+)", line)
                if m_off and int(m_off.group(1)) > 64:
                    upload_progressed = True

            if ota_session_active:
                if "Connected[0]:" in line or "Scanning for BS*" in line:
                    recv_bg_interference = True
                if "SCAN hit:" in line and "bs=BSF66F" in line:
                    recv_bg_interference = True
            if "device kind set: anchor" in line or "OTA NUS stage: disabled" in line:
                device_anchor_ack = True
            if "Control status: mode=" in line:
                try:
                    current_mode = line.split("mode=", 1)[1].split()[0].strip()
                    if current_mode.upper() == "RECV":
                        recv_loaded = True
                    elif current_mode.upper() == "OTA":
                        ota_loaded = True
                        mode_ota_ack = True
                except Exception:
                    pass
            elif "Control mode loaded: OTA" in line:
                current_mode = "OTA"
                ota_loaded = True
                mode_ota_ack = True
            elif "Control mode loaded: RECV" in line:
                current_mode = "RECV"
                recv_loaded = True

            if ((recv_loaded and not args.direct_ota_mode) or (args.direct_ota_mode and mode_recv_sent)) and (not mode_ota_ack):
                now = time.time()
                if (not mode_ota_sent) or (now - mode_ota_last_tx >= 1.5 and mode_ota_retry < 8):
                    s.write(b"mode ota\n")
                    log.write(f"[HOST_CMD {time.time()-t0:7.2f}s] mode ota\n")
                    log.flush()
                    mode_ota_sent = True
                    mode_ota_last_tx = now
                    mode_ota_retry += 1
                    if args.direct_ota_mode:
                        s.write(b"status\n")
                        log.write(f"[HOST_CMD {time.time()-t0:7.2f}s] status\n")
                        log.flush()
                        direct_status_last_tx = time.time()

            if args.direct_ota_mode and mode_ota_sent and (not mode_ota_ack):
                now = time.time()
                if now - direct_status_last_tx >= 2.0:
                    s.write(b"status\n")
                    log.write(f"[HOST_CMD {time.time()-t0:7.2f}s] status\n")
                    log.flush()
                    direct_status_last_tx = now

            if mode_ota_ack and (not target_cfg_sent):
                cfg_phase = 1
                cfg_phase_started_at = time.time()
                cfg_last_tx_at = 0.0
                cfg_retry_count = 0
                target_cfg_sent = True
                s.write(b"status\n")
                log.write(f"[HOST_CMD {time.time()-t0:7.2f}s] status\n")
                log.flush()

            # In direct-OTA mode, CDC re-enumeration + noisy log streams can
            # delay/garble status parsing. Avoid blocking forever on ota_loaded.
            if args.direct_ota_mode and mode_ota_ack and (not target_cfg_sent) and rel >= 4.0:
                cfg_phase = 1
                cfg_phase_started_at = time.time()
                cfg_last_tx_at = 0.0
                cfg_retry_count = 0
                target_cfg_sent = True
                s.write(b"status\n")
                log.write(f"[HOST_CMD {time.time()-t0:7.2f}s] status\n")
                log.flush()

            if target_cfg_sent and not initiate_sent:
                now = time.time()
                need_tx = (cfg_last_tx_at == 0.0) or (now - cfg_last_tx_at >= 2.0 and cfg_retry_count < 3)
                if need_tx:
                    if cfg_phase == 1:
                        cmd = f"ota_target uuid {target_uuid}\n"
                    elif cfg_phase == 2:
                        cmd = f"ota_target name {args.target_name}\n" if args.target_name else "ota_target name -\n"
                    elif cfg_phase == 3:
                        cmd = "ota_target token -1\n"
                    elif cfg_phase == 4:
                        cmd = "initiate\n"
                    else:
                        cmd = ""
                    if cmd:
                        s.write(cmd.encode("utf-8"))
                        log.write(f"[HOST_CMD {time.time()-t0:7.2f}s] {cmd}")
                        log.flush()
                        cfg_last_tx_at = now
                        cfg_retry_count += 1

                if cfg_phase in (1, 2, 3) and (time.time() - cfg_phase_started_at > 20.0):
                    ota_failed = True
                    notes = f"cfg_phase_timeout_{cfg_phase}"
                    break
                if cfg_phase == 4 and (time.time() - cfg_phase_started_at > 25.0):
                    ota_failed = True
                    notes = "initiate_timeout"
                    break

            if "initiate rc=" in line:
                initiate_ack_seen = True
                initiate_sent = True
            if "ota_target uuid rc=0" in line and cfg_phase == 1:
                cfg_phase = 2
                cfg_phase_started_at = time.time()
                cfg_last_tx_at = 0.0
                cfg_retry_count = 0
            if "ota_target name rc=0" in line and cfg_phase == 2:
                cfg_phase = 3
                cfg_phase_started_at = time.time()
                cfg_last_tx_at = 0.0
                cfg_retry_count = 0
            if "ota_target token rc=0" in line and cfg_phase == 3:
                cfg_phase = 4
                cfg_phase_started_at = time.time()
                cfg_last_tx_at = 0.0
                cfg_retry_count = 0
            if "initiate rc=" in line and cfg_phase == 4:
                cfg_phase = 5
            if "OTA upload starting" in line:
                ota_started = True
            if "OTA upload complete" in line:
                ota_completed = True
            if "OTA start blocked: identity not verified" in line:
                blocked_identity = True
            if ("OTA erase failed:" in line or
                "OTA upload failed:" in line or
                "OTA reset failed:" in line or
                "OTA state read failed:" in line):
                ota_failed = True
                break
            if "Disconnected:" in line and (ota_completed or blocked_identity):
                break

        if s is not None:
            try:
                s.close()
            except Exception:
                pass

    post_scan = scan_snapshot(trial_dir / "scan_post.csv")
    target_match = (selected_uuid == target_uuid and verified)
    post_ota_target_present = target_uuid in post_scan
    post_ota_rediscovered = post_ota_target_present
    wrong_target_started = ota_started and selected_uuid not in ("", target_uuid)

    if selected_addr == "" and not ota_started and "ota_uploading" not in op_states:
        notes = "no_connect_event"
    elif blocked_identity:
        notes = "blocked_identity"
    elif ota_completed and target_match and post_ota_rediscovered:
        notes = "ota_completed_target_verified"
    elif ota_completed and not target_match:
        notes = "ota_completed_but_target_mismatch"
    elif ota_failed:
        notes = "ota_failed"
    else:
        notes = "incomplete"

    # Non-target safety quick check (labels/roles snapshot unchanged for discovered peers).
    drift = []
    for uuid, row in pre_scan.items():
        if uuid == target_uuid:
            continue
        if uuid not in post_scan:
            continue
        p = pre_scan[uuid]
        q = post_scan[uuid]
        if (p.get("ANCHOR_LABEL"), p.get("ROLE")) != (q.get("ANCHOR_LABEL"), q.get("ROLE")):
            drift.append(uuid)
    if drift:
        notes += f";non_target_drift={','.join(drift)}"
    if recv_bg_interference:
        notes += ";recv_bg_interference=1"
    if first_upload_tx_seen and not first_upload_rsp_seen:
        notes += ";first_upload_rsp_missing=1"
    if first_upload_rsp_seen and not upload_progressed:
        notes += ";upload_not_progressed=1"

    return TrialResult(
        trial=trial,
        selected_addr=selected_addr,
        selected_uuid=selected_uuid,
        verified=verified,
        ota_started=ota_started,
        ota_completed=ota_completed,
        blocked_identity=blocked_identity,
        post_ota_rediscovered=post_ota_rediscovered,
        post_ota_target_present=post_ota_target_present,
        target_match=target_match,
        wrong_target_started=wrong_target_started,
        recv_bg_interference=recv_bg_interference,
        first_upload_tx_seen=first_upload_tx_seen,
        first_upload_rsp_seen=first_upload_rsp_seen,
        upload_progressed=upload_progressed,
        op_states=op_states,
        notes=notes,
        log_path=str(log_path),
    )


def main() -> int:
    args = parse_args()
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path(f"logs/ota_target_loop_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[TrialResult] = []
    for i in range(1, args.trials + 1):
        res = run_trial(args, out_dir, i)
        results.append(res)
        print(
            f"trial={i} selected_uuid={res.selected_uuid or '-'} "
            f"verified={int(res.verified)} ota_started={int(res.ota_started)} "
            f"ota_completed={int(res.ota_completed)} post_rediscovered={int(res.post_ota_rediscovered)} "
            f"target_match={int(res.target_match)} wrong_target_started={int(res.wrong_target_started)} notes={res.notes}"
        )

    summary = {
        "target_uuid": args.target_uuid.upper(),
        "trials": [asdict(r) for r in results],
        "trial_count": len(results),
        "target_match_count": sum(1 for r in results if r.target_match),
        "successful_trials": sum(1 for r in results if r.ota_completed and r.target_match and r.post_ota_rediscovered),
        "ota_completed_count": sum(1 for r in results if r.ota_completed),
        "blocked_identity_count": sum(1 for r in results if r.blocked_identity),
        "wrong_target_trials": sum(1 for r in results if r.wrong_target_started),
        "recv_bg_interference_trials": sum(1 for r in results if r.recv_bg_interference),
        "first_upload_rsp_missing_trials": sum(1 for r in results if r.first_upload_tx_seen and not r.first_upload_rsp_seen),
        "upload_not_progressed_trials": sum(1 for r in results if r.first_upload_rsp_seen and not r.upload_progressed),
        "post_ota_readback_success_count": sum(1 for r in results if r.post_ota_rediscovered),
        "non_target_drift_trials": sum(1 for r in results if "non_target_drift=" in r.notes),
        "safety_converged": all(not r.wrong_target_started for r in results),
        "execution_converged": all(r.ota_completed and r.target_match and r.post_ota_rediscovered for r in results),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["execution_converged"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
