#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import serial


DEFAULT_H_UUID = "B1E487C2B1FD740D1442206A1857DFA1"
US_H_ANTENNA_CENTER_OFFSET_MM = int(os.environ.get("BIOSPUR_US_H_ANTENNA_CENTER_OFFSET_MM", "107"))


def add_us_antenna_center_fields(row: dict[str, str]) -> dict[str, str]:
    row["ant_center_offset_mm"] = str(US_H_ANTENNA_CENTER_OFFSET_MM)
    for src, dst in [
        ("latest_mm", "latest_ant_center_mm"),
        ("median_mm", "median_ant_center_mm"),
        ("mean_mm", "mean_ant_center_mm"),
        ("min_mm", "min_ant_center_mm"),
        ("max_mm", "max_ant_center_mm"),
    ]:
        value = row.get(src)
        if value in (None, ""):
            continue
        try:
            row[dst] = str(int(round(float(value) + US_H_ANTENNA_CENTER_OFFSET_MM)))
        except ValueError:
            pass
    return row


def run_stream(cmd: list[str], log_path: Path, cwd: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[PIPE] run: {' '.join(cmd)}", flush=True)
    print(f"[PIPE] log: {log_path}", flush=True)
    with log_path.open("w", encoding="utf-8", buffering=1) as logf:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            logf.write(line)
        return proc.wait()


def run_json(cmd: list[str], log_path: Path, cwd: Path) -> dict:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[PIPE] json: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    log_path.write_text(proc.stdout, encoding="utf-8")
    text = proc.stdout.strip()
    try:
        start = text.index("{")
        return json.loads(text[start:])
    except Exception as exc:
        return {
            "error": f"json_parse_failed:{exc}",
            "returncode": proc.returncode,
            "stdout": proc.stdout,
        }


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": f"read_json_failed:{exc}", "path": str(path)}


def parse_us_status(resp: str) -> dict[str, str]:
    out: dict[str, str] = {"raw": resp}
    parts = resp.strip().split(";")
    if parts:
        out["prefix"] = parts[0]
    if len(parts) > 1:
        out["state"] = parts[1]
    for part in parts[2:]:
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip()] = value.strip()
    return add_us_antenna_center_fields(out)


def read_serial_for(ser: serial.Serial, duration_s: float) -> str:
    deadline = time.time() + duration_s
    chunks: list[str] = []
    while time.time() < deadline:
        data = ser.read(4096)
        if data:
            chunks.append(data.decode("utf-8", "ignore"))
    return "".join(chunks)


def send_serial_command(ser: serial.Serial, cmd: str, wait_s: float) -> str:
    ser.write((cmd + "\n").encode("utf-8"))
    ser.flush()
    return read_serial_for(ser, wait_s)


def wait_for_anchor_ctrl_ready(ser: serial.Serial, uuid: str, timeout_s: float) -> str:
    deadline = time.time() + timeout_s
    chunks: list[str] = []
    needle = uuid.upper()
    while time.time() < deadline:
        data = ser.read(4096)
        if not data:
            continue
        text = data.decode("utf-8", "ignore")
        chunks.append(text)
        if "ANCHOR_CTRL" in text and "link ready" in text and needle in text.upper():
            break
    return "".join(chunks)


def extract_anchor_notify(transcript: str) -> str:
    matches = re.findall(r"ANCHOR_CTRL\[\d+\]\s+notify:\s*(.+)", transcript)
    if matches:
        return matches[-1].strip()
    if "BLE cmd not sent" in transcript:
        return "ERR:BLE_CMD_NOT_SENT"
    if "cmd rc=" in transcript:
        return "ERR:CMD_RC"
    return ""


def master_anchor_us_cmd(anchor_port: str, uuid: str, cmd: str, out_dir: Path, name: str,
                         wait_s: float = 1.2, setup_wait_s: float = 12.0) -> tuple[dict, str]:
    """Send an ultrasound command to Anchor H through the Master_Anchor UART/NUS bridge."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"{name}.log"
    transcript: list[str] = []
    with serial.Serial(anchor_port, 115200, timeout=0.15) as ser:
        transcript.append(read_serial_for(ser, 0.4))
        for setup_cmd, setup_wait in [
            (f"ota_target uuid {uuid}", 1.0),
            ("conn", 0.8),
        ]:
            transcript.append(f"\n>>> {setup_cmd}\n")
            transcript.append(send_serial_command(ser, setup_cmd, setup_wait))
        transcript.append(f"\n>>> cmd {cmd}\n")
        transcript.append(send_serial_command(ser, f"cmd {cmd}", wait_s))
        resp = extract_anchor_notify("".join(transcript))
        if not resp or resp.startswith("ERR:"):
            transcript.append(wait_for_anchor_ctrl_ready(ser, uuid, setup_wait_s))
            transcript.append(f"\n>>> cmd {cmd}  # retry after H ready\n")
            transcript.append(send_serial_command(ser, f"cmd {cmd}", wait_s))
    text = "".join(transcript)
    log_path.write_text(text, encoding="utf-8")
    resp = extract_anchor_notify(text)
    return {"log_path": str(log_path), "raw": text}, resp


def write_us_csv(csv_path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "cycle",
        "timestamp",
        "phase",
        "prefix",
        "state",
        "attempts",
        "target",
        "ok",
        "timeout",
        "latest_mm",
        "latest_ant_center_mm",
        "median_mm",
        "median_ant_center_mm",
        "mean_mm",
        "mean_ant_center_mm",
        "min_mm",
        "min_ant_center_mm",
        "max_mm",
        "max_ant_center_mm",
        "ant_center_offset_mm",
        "echo_us",
        "trig",
        "echo",
        "raw",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def wait_for_us_done(anchor_port: str, uuid: str, cycle: int, out_dir: Path,
                     duration_s: int) -> tuple[bool, list[dict[str, str]]]:
    rows: list[dict[str, str]] = []
    deadline = time.time() + max(duration_s + 35, 60)
    poll_idx = 0

    while time.time() < deadline:
        poll_idx += 1
        _, resp = master_anchor_us_cmd(
            anchor_port,
            uuid,
            "US?",
            out_dir,
            f"us_status_{poll_idx:03d}",
            wait_s=1.6,
            setup_wait_s=5.0,
        )
        row = parse_us_status(resp)
        row.update({
            "cycle": str(cycle),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "phase": "poll",
        })
        rows.append(row)
        print(f"[PIPE] US poll cycle={cycle} idx={poll_idx}: {resp}", flush=True)
        if row.get("state") == "DONE":
            return True, rows
        if row.get("state") == "DISABLED":
            return False, rows
        time.sleep(2.0)

    return False, rows


def sweep_success(summary: dict, order: str) -> bool:
    if not isinstance(summary, dict):
        return False
    guard = summary.get("session_role_guard_result")
    if isinstance(guard, dict) and not guard.get("success"):
        return False
    rounds = summary.get("rounds")
    if not isinstance(rounds, dict):
        return False
    return all(isinstance(rounds.get(a), dict) and rounds[a].get("success") for a in order)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run 3x [SW100 -> H ultrasound 30s -> BSF66F 120s motion capture]."
    )
    parser.add_argument("--anchor-port", default=os.environ.get("BIOSPUR_ANCHOR_PORT", ""))
    parser.add_argument("--tag-port", default=os.environ.get("BIOSPUR_TAG_PORT", ""))
    parser.add_argument("--anchor-snr", default=os.environ.get("BIOSPUR_ANCHOR_SNR", "960148546"))
    parser.add_argument("--tag-snr", default=os.environ.get("BIOSPUR_TAG_SNR", "1050070698"))
    parser.add_argument("--h-uuid", default=DEFAULT_H_UUID)
    parser.add_argument("--tag", default="BSF66F")
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--sw-sets", type=int, default=100)
    parser.add_argument("--us-duration-s", type=int, default=30)
    parser.add_argument("--motion-duration-s", type=float, default=120.0)
    parser.add_argument("--tr-hz", type=int, default=10)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    if not args.anchor_port:
        raise SystemExit("--anchor-port is required")
    if not args.tag_port:
        raise SystemExit("--tag-port is required")
    if args.anchor_port == args.tag_port:
        raise SystemExit("anchor and tag ports must be different")

    cwd = Path(__file__).resolve().parents[1]
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "success": False,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "anchor_port": args.anchor_port,
        "tag_port": args.tag_port,
        "h_uuid": args.h_uuid,
        "tag": args.tag,
        "cycles_requested": args.cycles,
        "cycles": [],
    }

    all_ok = True
    for cycle in range(1, args.cycles + 1):
        cycle_dir = out_root / f"cycle_{cycle:02d}"
        cycle_dir.mkdir(parents=True, exist_ok=True)
        cycle_summary: dict = {"cycle": cycle, "success": False, "dir": str(cycle_dir)}

        sweep_dir = cycle_dir / f"autopos_sweep{args.sw_sets}"
        sweep_cmd = [
            sys.executable,
            "scripts/run_autopos_sweep_loop.py",
            "--port",
            args.anchor_port,
            "--order",
            "ABCDEFGH",
            "--sw-sets",
            str(args.sw_sets),
            "--prewarm-sw-sets",
            "0",
            "--timeout-s",
            "2400",
            "--no-final-responder",
            "--out-dir",
            str(sweep_dir),
            "--verbose",
            "0",
        ]
        rc = run_stream(sweep_cmd, cycle_dir / "autopos_sweep.console.log", cwd)
        sweep_summary = load_json(sweep_dir / "summary.json")
        cycle_summary["sweep_returncode"] = rc
        cycle_summary["sweep_summary"] = str(sweep_dir / "summary.json")
        if rc != 0 or not sweep_success(sweep_summary, "ABCDEFGH"):
            cycle_summary["error"] = "sweep_failed"
            summary["cycles"].append(cycle_summary)
            all_ok = False
            break

        restore_dir = cycle_dir / "post_sweep_responder_verify"
        restore_cmd = [
            sys.executable,
            "scripts/verify_all_anchor_responder_runtime.py",
            "--port",
            args.anchor_port,
            "--retry-count",
            "2",
            "--command-timeout-s",
            "60",
            "--scan-timeout-s",
            "35",
            "--out-dir",
            str(restore_dir),
        ]
        rc = run_stream(restore_cmd, cycle_dir / "post_sweep_responder.console.log", cwd)
        cycle_summary["post_sweep_responder_returncode"] = rc
        cycle_summary["post_sweep_responder_dir"] = str(restore_dir)
        if rc != 0:
            cycle_summary["error"] = "post_sweep_responder_restore_failed"
            summary["cycles"].append(cycle_summary)
            all_ok = False
            break

        us_dir = cycle_dir / "ultrasound_H_30s"
        us_dir.mkdir(parents=True, exist_ok=True)
        us_rows: list[dict[str, str]] = []

        _, off_resp_pre = master_anchor_us_cmd(args.anchor_port, args.h_uuid, "USOFF", us_dir, "usoff_before")
        us_rows.append({
            "cycle": str(cycle),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "phase": "off_before",
            **parse_us_status(off_resp_pre),
        })

        _, on_resp = master_anchor_us_cmd(
            args.anchor_port,
            args.h_uuid,
            f"USON {args.us_duration_s}",
            us_dir,
            "uson",
            wait_s=1.2,
        )
        us_rows.append({
            "cycle": str(cycle),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "phase": "on",
            **parse_us_status(on_resp),
        })
        if not on_resp.startswith("OK USON"):
            cycle_summary["error"] = f"uson_failed:{on_resp}"
            write_us_csv(us_dir / "ultrasound_H.csv", us_rows)
            summary["cycles"].append(cycle_summary)
            all_ok = False
            break

        done, poll_rows = wait_for_us_done(
            args.anchor_port,
            args.h_uuid,
            cycle,
            us_dir,
            args.us_duration_s,
        )
        us_rows.extend(poll_rows)
        _, off_resp = master_anchor_us_cmd(args.anchor_port, args.h_uuid, "USOFF", us_dir, "usoff_after")
        us_rows.append({
            "cycle": str(cycle),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "phase": "off_after",
            **parse_us_status(off_resp),
        })
        _, post_resp = master_anchor_us_cmd(args.anchor_port, args.h_uuid, "US?", us_dir, "us_status_after_usoff")
        post_row = {
            "cycle": str(cycle),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "phase": "post_off_status",
            **parse_us_status(post_resp),
        }
        us_rows.append(post_row)
        write_us_csv(us_dir / "ultrasound_H.csv", us_rows)
        cycle_summary["ultrasound_csv"] = str(us_dir / "ultrasound_H.csv")
        cycle_summary["ultrasound_done"] = done
        cycle_summary["ultrasound_final_status"] = post_resp
        cycle_summary["ultrasound_ant_center_offset_mm"] = US_H_ANTENNA_CENTER_OFFSET_MM
        cycle_summary["ultrasound_final_latest_ant_center_mm"] = post_row.get("latest_ant_center_mm", "")
        cycle_summary["ultrasound_final_median_ant_center_mm"] = post_row.get("median_ant_center_mm", "")
        cycle_summary["ultrasound_final_mean_ant_center_mm"] = post_row.get("mean_ant_center_mm", "")
        if not done:
            cycle_summary["error"] = "ultrasound_not_done"
            summary["cycles"].append(cycle_summary)
            all_ok = False
            break
        if "RUNNING" in post_resp:
            cycle_summary["error"] = "ultrasound_still_running_after_usoff"
            summary["cycles"].append(cycle_summary)
            all_ok = False
            break

        motion_dir = cycle_dir / f"motion_{args.tag}_{int(args.motion_duration_s)}s"
        motion_cmd = [
            sys.executable,
            "scripts/run_recv_tdma_capture.py",
            "--port",
            args.tag_port,
            "--controller-reset-snr",
            args.tag_snr,
            "--duration",
            str(args.motion_duration_s),
            "--targets",
            args.tag,
            "--tr-hz",
            str(args.tr_hz),
            "--anchor-preflight-port",
            args.anchor_port,
            "--anchor-responder-settle-s",
            "10.0",
            "--reuse-tag-links",
            "--tag-link-timeout-s",
            "30.0",
            "--out-dir-exact",
            "--out-dir",
            str(motion_dir),
        ]
        rc = run_stream(motion_cmd, cycle_dir / "motion_capture.console.log", cwd)
        motion_summaries = [motion_dir / "summary.json"] if (motion_dir / "summary.json").exists() else []
        motion_summary = load_json(motion_summaries[-1]) if motion_summaries else {"success": False, "error": "missing_summary"}
        cycle_summary["motion_returncode"] = rc
        cycle_summary["motion_summary"] = str(motion_summaries[-1]) if motion_summaries else ""
        if rc != 0 or not motion_summary.get("success"):
            cycle_summary["error"] = "motion_capture_failed"
            summary["cycles"].append(cycle_summary)
            all_ok = False
            break

        cycle_summary["success"] = True
        summary["cycles"].append(cycle_summary)
        (out_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    summary["success"] = all_ok and len(summary["cycles"]) == args.cycles and all(c.get("success") for c in summary["cycles"])
    (out_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary["success"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
