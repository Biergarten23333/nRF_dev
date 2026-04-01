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


CONNECT_RE = re.compile(r"Connect start:\s+(.+?)\s+token=([-\d]+)\s+name=(.+)$")
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
    op_states: list[str]
    notes: str
    log_path: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Repeat OTA targeting runs and verify identity-safe convergence.")
    p.add_argument("--port", default="/dev/serial/by-id/usb-SEGGER_J-Link_000683234364-if00")
    p.add_argument("--target-uuid", required=True, help="32-hex stable UUID of intended target")
    p.add_argument("--target-name", default="", help="Optional exact name constraint")
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--trial-timeout-s", type=float, default=120.0)
    p.add_argument("--flash-image", default="build-master-control-anchor-ota-20260331f/master_control/zephyr/zephyr.hex")
    p.add_argument("--skip-flash", action="store_true")
    p.add_argument("--out-dir", default="")
    return p.parse_args()


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
            return TrialResult(trial, "", "", False, False, False, False, False, f"flash_failed rc={rc}", str(flash_log))

    pre_scan = scan_snapshot(trial_dir / "scan_pre.csv")

    target_uuid = args.target_uuid.upper().strip()
    commands = [
        "status\n",
        "device kind anchor\n",
        f"ota_target uuid {target_uuid}\n",
        f"ota_target name {args.target_name}\n" if args.target_name else "ota_target name -\n",
        "ota_target token -1\n",
        # Force a mode cycle so each trial starts from a clean OTA state-machine path.
        "mode recv\n",
        "mode ota\n",
    ]

    selected_addr = ""
    selected_uuid = ""
    verified = False
    ota_started = False
    ota_completed = False
    blocked_identity = False
    op_states: list[str] = []
    notes = ""
    log_path = trial_dir / "ota_trial.log"

    with log_path.open("w", encoding="utf-8") as log:
        t0 = time.time()
        cmd_index = 0
        next_cmd_at = [1.5, 2.0, 2.5, 3.0, 3.5, 4.3, 9.0]
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
                while cmd_index < len(commands) and rel >= next_cmd_at[cmd_index]:
                    cmd = commands[cmd_index]
                    s.write(cmd.encode("utf-8"))
                    log.write(f"[HOST_CMD {rel:7.2f}s] {cmd}")
                    log.flush()
                    cmd_index += 1

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
            m2 = EVIDENCE_RE.search(line)
            if m2:
                verified = m2.group(1) == "1"
                selected_uuid = m2.group(2).strip().upper()
            m3 = STATE_RE.search(line)
            if m3:
                st = m3.group(1).strip()
                if not op_states or op_states[-1] != st:
                    op_states.append(st)
            if "OTA upload starting" in line:
                ota_started = True
            if "OTA upload complete" in line:
                ota_completed = True
            if "OTA start blocked: identity not verified" in line:
                blocked_identity = True
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

    if selected_addr == "":
        notes = "no_connect_event"
    elif blocked_identity:
        notes = "blocked_identity"
    elif ota_completed and target_match and post_ota_rediscovered:
        notes = "ota_completed_target_verified"
    elif ota_completed and not target_match:
        notes = "ota_completed_but_target_mismatch"
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
