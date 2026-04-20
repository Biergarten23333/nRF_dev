#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path


UUIDS = {
    "A": "4DC6B8187E33803AE8601FB0D7992B96",
    "B": "B9179575C776C98F1CB132DD6EDC6223",
    "C": "CEE5A7EFCB35F8A56B430047629F5309",
    "D": "AB14CCA262A092E70EB26B0ACB0A394B",
    "E": "A892AF05DD59CF0D0D3408AD74F364A1",
    "F": "840C68591E90019821AACFF1B73AAA34",
    "G": "B3087BC3D87CCCD316AEDC6B71D6677F",
    "H": "1EABFBEC28B8053FBB0D5C448112AE93",
}

UPLOAD_PROGRESS_RE = re.compile(r"OTA upload progress:\s*(\d+)%")


def detect_stage(log_path: Path) -> tuple[str, int | None]:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "starting", None

    if "ota_success_observed" in text or "OTA command sequence sent" in text:
        return "success-seen", 100
    if "OTA pending-state recovery reset request" in text:
        return "recovery-reset", None
    if "OTA reset request" in text:
        return "reset", None
    if "OTA pending/test request" in text:
        return "pending-test", None
    if "OTA upload complete" in text:
        return "upload-complete", 100

    matches = UPLOAD_PROGRESS_RE.findall(text)
    if matches:
        return "upload", int(matches[-1])

    if "OTA upload starting" in text:
        return "upload", 0
    if "DFU SMP service ready" in text:
        return "dfu-ready", None
    if "Connected: " in text:
        return "connected", None
    if "mode ota (already ota)" in text:
        return "already-ota", None
    if "Control mode loaded: OTA" in text:
        return "ota-mode", None
    if "phase=A anchor_ready_uuid_write" in text or "ota_target uuid rc=0" in text:
        return "targeting", None
    return "starting", None


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--"
    total = int(round(seconds))
    mins, secs = divmod(total, 60)
    if mins >= 60:
        hours, mins = divmod(mins, 60)
        return f"{hours}h{mins:02d}m"
    return f"{mins}m{secs:02d}s"


def estimate_eta(stage: str, percent: int | None, started_at: float) -> str:
    if stage != "upload" or percent is None or percent <= 0:
        return "--"
    elapsed = time.monotonic() - started_at
    if elapsed <= 0:
        return "--"
    total_est = elapsed / (percent / 100.0)
    remaining = total_est - elapsed
    return format_eta(remaining)


def render_progress(label: str, attempt: int, max_attempts: int, stage: str, percent: int | None, started_at: float) -> str:
    elapsed = int(time.monotonic() - started_at)
    if percent is None:
        bar = "." * 20
        pct = "--"
    else:
        filled = max(0, min(20, percent // 5))
        bar = "#" * filled + "." * (20 - filled)
        pct = f"{percent:3d}"
    eta = estimate_eta(stage, percent, started_at)
    return f"\r[{label}] attempt {attempt}/{max_attempts} [{bar}] {pct}% stage={stage:<14} elapsed={elapsed:4d}s eta={eta:<6}"


def run_with_progress(cmd: list[str], log_path: Path, *, label: str, attempt: int, max_attempts: int) -> subprocess.CompletedProcess[str]:
    started_at = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    captured: list[str] = []
    last_line = ""
    while True:
        rc = proc.poll()
        stage, percent = detect_stage(log_path)
        display_stage = stage
        display_percent = percent
        if rc is None and stage == "success-seen":
            display_stage = "finalizing"
            display_percent = 100
        elif rc is not None:
            if rc == 0:
                display_stage = "done"
                display_percent = 100
            elif stage == "success-seen":
                display_stage = "finalizing"
                display_percent = 100
        line = render_progress(label, attempt, max_attempts, display_stage, display_percent, started_at)
        if line != last_line:
            print(line, end="", flush=True)
            last_line = line
        if rc is not None:
            break
        time.sleep(0.5)

    if proc.stdout is not None:
        captured_text = proc.stdout.read()
        if captured_text:
            captured.append(captured_text)
    print()
    stdout_text = "".join(captured)
    if stdout_text.strip():
        print(stdout_text.strip())
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout=stdout_text, stderr=None)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deploy current embedded Anchor OTA payload to A-H via strict BLE UUID OTA.")
    p.add_argument("--port", required=True, help="52840 CDC control port")
    p.add_argument("--out-dir", required=True, help="Root artifact directory")
    p.add_argument("--timeout-s", type=int, default=900, help="Per-anchor OTA timeout")
    p.add_argument("--order", default="ABCDEFGH", help="Anchor deployment order, e.g. ABCDEFGH")
    p.add_argument(
        "--force-kill-port-owner",
        action="store_true",
        help=(
            "If the master_control CDC port is exclusively locked by another process, "
            "try to terminate the owning PID(s) and retry. DANGEROUS."
        ),
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    out_root = Path(args.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    deploy_summary: dict[str, object] = {
        "port": args.port,
        "timeout_s": args.timeout_s,
        "order": list(args.order),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rounds": {},
    }

    for label in args.order:
        uuid = UUIDS[label]
        round_dir = out_root / f"anchor_{label}"
        round_dir.mkdir(parents=True, exist_ok=True)
        entry: dict[str, object] = {"uuid": uuid, "attempts": []}
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            stage_dir = round_dir / f"stage{attempt}"
            cmd = [
                "python3",
                "scripts/ota_single_shot_stable.py",
                "--timeout-s",
                str(args.timeout_s),
                "--port",
                args.port,
                "--target-uuid",
                uuid,
                "--out-dir",
                str(stage_dir),
            ]
            if args.force_kill_port_owner:
                cmd.append("--force-kill-port-owner")
            print(f"=== OTA {label} {uuid} attempt={attempt}/{max_attempts} ===", flush=True)
            cp = run_with_progress(cmd, stage_dir / "single_shot.log", label=label, attempt=attempt, max_attempts=max_attempts)
            attempt_entry: dict[str, object] = {
                "attempt": attempt,
                "returncode": cp.returncode,
                "summary_json": str(stage_dir / "summary.json"),
            }
            run_summary = None
            try:
                with open(stage_dir / "summary.json", "r", encoding="utf-8") as f:
                    run_summary = json.load(f)
                    attempt_entry["summary"] = run_summary
            except Exception as exc:
                attempt_entry["summary_read_error"] = repr(exc)
            retry_pending_state = False
            try:
                log_text = (stage_dir / "single_shot.log").read_text(encoding="utf-8", errors="replace")
                retry_pending_state = (
                    "OTA pending-state recovery reset request" in log_text or
                    "OTA erase failed: 6" in log_text or
                    "OTA upload-gate recovery reset request" in log_text
                )
            except Exception:
                pass
            attempt_entry["retry_pending_state"] = retry_pending_state
            recovery_only = isinstance(run_summary, dict) and (
                run_summary.get("reason") == "ota_pending_state_recovery_reset" or
                bool(run_summary.get("ota_pending_recovery_reset_seen"))
            )
            attempt_entry["recovery_only"] = recovery_only
            cast_attempts = entry["attempts"]
            assert isinstance(cast_attempts, list)
            cast_attempts.append(attempt_entry)

            if recovery_only and attempt < max_attempts:
                print(f"--- retrying {label} after pending-state recovery reset completion ---", flush=True)
                time.sleep(8.0)
                continue

            if cp.returncode == 0:
                entry["returncode"] = 0
                entry["summary_json"] = str(stage_dir / "summary.json")
                entry["summary"] = run_summary
                break
            if retry_pending_state and attempt < max_attempts:
                print(f"--- retrying {label} after pending-state recovery reset ---", flush=True)
                time.sleep(8.0)
                continue
            entry["returncode"] = cp.returncode
            entry["summary_json"] = str(stage_dir / "summary.json")
            if run_summary is not None:
                entry["summary"] = run_summary
            break
        deploy_summary["rounds"][label] = entry
        with open(out_root / "deploy_summary.json", "w", encoding="utf-8") as f:
            json.dump(deploy_summary, f, indent=2)
        final_rc = int(entry.get("returncode", 1))
        if final_rc != 0:
            return final_rc

    deploy_summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(out_root / "deploy_summary.json", "w", encoding="utf-8") as f:
        json.dump(deploy_summary, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
