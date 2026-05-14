#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import serial


REPO_ROOT = Path(__file__).resolve().parents[1]

ANCHORS: dict[str, tuple[str, str]] = {
    "A": ("760184781", "/dev/serial/by-id/usb-SEGGER_J-Link_000760184781-if00"),
    "B": ("760185876", "/dev/serial/by-id/usb-SEGGER_J-Link_000760185876-if00"),
    "C": ("760185878", "/dev/serial/by-id/usb-SEGGER_J-Link_000760185878-if00"),
    "D": ("760186081", "/dev/serial/by-id/usb-SEGGER_J-Link_000760186081-if00"),
    "E": ("760185904", "/dev/serial/by-id/usb-SEGGER_J-Link_000760185904-if00"),
    "F": ("760186124", "/dev/serial/by-id/usb-SEGGER_J-Link_000760186124-if00"),
    "G": ("760185889", "/dev/serial/by-id/usb-SEGGER_J-Link_000760185889-if00"),
    "H": ("760186121", "/dev/serial/by-id/usb-SEGGER_J-Link_000760186121-if00"),
}

PROF_RE = re.compile(
    r"Responder prof anchor=(?P<anchor>\d+) samples=(?P<samples>\d+)"
    r"(?: attempts=(?P<attempts>\d+) misses=(?P<misses>\d+) "
    r"avg_us frame=(?P<avg_frame>\d+) ts=(?P<avg_ts>\d+) txprog=(?P<avg_txprog>\d+) start=(?P<avg_start>\d+) starttx=(?P<avg_starttx>\d+) "
    r"max_us frame=(?P<max_frame>\d+) ts=(?P<max_ts>\d+) txprog=(?P<max_txprog>\d+) start=(?P<max_start>\d+) starttx=(?P<max_starttx>\d+) "
    r"min_slack_uus=(?P<min_slack>-?\d+) last_miss_slack_uus=(?P<last_miss_slack>-?\d+) resp_delay_uus=(?P<resp_delay>\d+))?"
)


def run(cmd: list[str], log_path: Path, *, env: dict[str, str] | None = None, timeout_s: float | None = None) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as logf:
        logf.write("$ " + " ".join(cmd) + "\n")
        logf.flush()
        subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            check=True,
            text=True,
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=timeout_s,
        )


def run_capture_command(cmd: list[str], log_path: Path, *, timeout_s: float | None = None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as logf:
        logf.write("$ " + " ".join(cmd) + "\n")
        logf.flush()
        cp = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=logf,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
        )
    return cp.returncode


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_and_flash(delay_uus: int, marker: str, out_dir: Path) -> None:
    env = dict(**__import__("os").environ)
    env["ANCHOR_EXTRA_CMAKE_ARGS"] = (
        f"-DAPP_ANCHOR_RESP_DELAY_UUS={delay_uus} "
        "-DAPP_ANCHOR_RESPONDER_ADV_INT_MIN_MS=5000 "
        "-DAPP_ANCHOR_RESPONDER_ADV_INT_MAX_MS=10000 "
        "-DAPP_ANCHOR_RESPONDER_DIAG_PERIOD_MS=5000 "
        "-DAPP_ANCHOR_VERBOSE_RESPONDER=0 "
        "-DAPP_ANCHOR_VERBOSE_RESPONDER_ERRORS=0 "
        "-DAPP_ANCHOR_RESPONDER_PRINTK_ENABLE=0 "
        "-DAPP_ANCHOR_RESPONDER_PROFILE_ENABLE=1"
    )
    run(
        [
            "scripts/build_anchor_ota_control_bundle.sh",
            f"build-anchor-unified-ota-{marker}",
            f"build-master-control-anchor-ota-{marker}",
            marker,
        ],
        out_dir / "build_anchor_bundle.log",
        env=env,
    )
    run(
        ["scripts/build_master_control_b120_m1.sh", f"build-master-control-b120-m1-{marker}"],
        out_dir / "build_b120.log",
    )

    flash_env = dict(**__import__("os").environ)
    flash_env["B120_SNR"] = "960148546"
    flash_env["BIOSPUR_FLASH_PREFER_NRFJPROG"] = "0"
    run(
        [
            "scripts/flash_master_control_b120_m1_noninteractive.sh",
            f"build-master-control-b120-m1-{marker}/zephyr/merged_domains.hex",
        ],
        out_dir / "flash_b120.log",
        env=flash_env,
    )
    run(
        ["scripts/flash_all_anchors.sh", f"build-anchor-unified-ota-{marker}"],
        out_dir / "flash_anchors.log",
        env=flash_env,
    )


def serial_reset_anchors(out_dir: Path, labels: list[str]) -> None:
    log_path = out_dir / "serial_reset.log"
    with log_path.open("w", encoding="utf-8") as logf:
        for label in labels:
            _, port = ANCHORS[label]
            logf.write(f"{datetime.now().isoformat(timespec='seconds')} {label} {port} REBOOT\n")
            try:
                with serial.Serial(port, 115200, timeout=0.2) as ser:
                    ser.write(b"REBOOT\n")
                    ser.flush()
                    end = time.time() + 0.8
                    while time.time() < end:
                        data = ser.read(512)
                        if data:
                            logf.write(data.decode("utf-8", "ignore"))
            except Exception as exc:
                logf.write(f"ERR {label} {type(exc).__name__}: {exc}\n")
            logf.flush()


def _tail_anchor(label: str, port: str, stop: threading.Event, out_path: Path) -> None:
    with out_path.open("w", encoding="utf-8", buffering=1) as logf:
        while not stop.is_set():
            try:
                with serial.Serial(port, 115200, timeout=0.2) as ser:
                    while not stop.is_set():
                        line = ser.readline()
                        if not line:
                            continue
                        text = line.decode("utf-8", "ignore").rstrip()
                        if text:
                            logf.write(f"{time.time():.3f} {label} {text}\n")
            except Exception as exc:
                logf.write(f"{time.time():.3f} {label} SERIAL_ERR {type(exc).__name__}: {exc}\n")
                stop.wait(1.0)


def start_anchor_serial_loggers(out_dir: Path, labels: list[str]) -> tuple[threading.Event, list[threading.Thread]]:
    stop = threading.Event()
    threads: list[threading.Thread] = []
    serial_dir = out_dir / "anchor_serial"
    serial_dir.mkdir(parents=True, exist_ok=True)
    for label in labels:
        _, port = ANCHORS[label]
        thread = threading.Thread(
            target=_tail_anchor,
            args=(label, port, stop, serial_dir / f"{label}.log"),
            daemon=True,
        )
        thread.start()
        threads.append(thread)
    return stop, threads


def stop_anchor_serial_loggers(stop: threading.Event, threads: list[threading.Thread]) -> None:
    stop.set()
    for thread in threads:
        thread.join(timeout=2.0)


def latest_capture_session(cycle_dir: Path, capture_base: Path) -> Path:
    dirs = [p for p in cycle_dir.glob(f"{capture_base.name}_*") if p.is_dir()]
    if capture_base.exists() and capture_base.is_dir():
        dirs.extend(p for p in capture_base.iterdir() if p.is_dir())
    if not dirs:
        raise FileNotFoundError(f"no capture session found in {cycle_dir}")
    return max(dirs, key=lambda p: p.stat().st_mtime)


def analyze_anchor_profiles(serial_dir: Path) -> dict[str, Any]:
    per_anchor: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for path in sorted(serial_dir.glob("*.log")):
        label = path.stem
        counter = Counter()
        max_start = 0
        max_txprog = 0
        min_slack: int | None = None
        samples = 0
        attempts = 0
        misses = 0
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            match = PROF_RE.search(line)
            if not match:
                continue
            gd = match.groupdict()
            if gd.get("samples") == "0":
                counter["empty"] += 1
                continue
            samples += int(gd["samples"])
            attempts += int(gd.get("attempts") or 0)
            misses += int(gd.get("misses") or 0)
            max_start = max(max_start, int(gd.get("max_start") or 0))
            max_txprog = max(max_txprog, int(gd.get("max_txprog") or 0))
            slack = int(gd.get("min_slack") or 0)
            min_slack = slack if min_slack is None else min(min_slack, slack)
            rows.append({"anchor": label, **{k: v for k, v in gd.items() if v is not None}})
        per_anchor[label] = {
            "samples": samples,
            "attempts": attempts,
            "misses": misses,
            "miss_rate": round(misses / attempts, 6) if attempts else None,
            "max_start_us": max_start,
            "max_txprog_us": max_txprog,
            "min_slack_uus": min_slack,
            "empty_profile_lines": counter["empty"],
            "log": str(path),
        }
    return {"per_anchor": per_anchor, "rows": rows}


def write_profile_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_capture_cycle(args: argparse.Namespace, delay_uus: int, cycle_dir: Path) -> dict[str, Any]:
    cycle_dir.mkdir(parents=True, exist_ok=True)
    serial_reset_anchors(cycle_dir, args.anchor_labels)
    time.sleep(args.after_reset_wait_s)

    stop, threads = start_anchor_serial_loggers(cycle_dir, args.anchor_labels)
    try:
        capture_base = cycle_dir / "capture"
        capture_rc = run_capture_command(
            [
                sys.executable,
                "scripts/run_recv_tdma_capture.py",
                "--port", args.port,
                "--duration", str(args.duration),
                "--targets", args.targets,
                "--profiles", args.profiles,
                "--static-hz", str(args.static_hz),
                "--roto-hz", str(args.roto_hz),
                "--motion-hz", str(args.motion_hz),
                "--cm-probe-target", args.cm_probe_target,
                "--out-dir", str(capture_base),
            ],
            cycle_dir / "capture_driver.log",
            timeout_s=args.capture_timeout_s,
        )
    finally:
        stop_anchor_serial_loggers(stop, threads)

    session_dir = latest_capture_session(cycle_dir, cycle_dir / "capture")
    run(
        [
            sys.executable,
            "scripts/analyze_recv_tdma_reject_rootcause.py",
            "--session-dir",
            str(session_dir),
            "--out-json",
            str(cycle_dir / "reject_rootcause.json"),
            "--out-md",
            str(cycle_dir / "reject_rootcause.md"),
        ],
        cycle_dir / "analysis_driver.log",
    )
    profile = analyze_anchor_profiles(cycle_dir / "anchor_serial")
    write_profile_csv(cycle_dir / "anchor_profile_rows.csv", profile["rows"])
    reject = json.loads((cycle_dir / "reject_rootcause.json").read_text(encoding="utf-8"))
    capture_summary = json.loads((session_dir / "summary.json").read_text(encoding="utf-8"))
    out = {
        "capture_returncode": capture_rc,
        "delay_uus": delay_uus,
        "cycle_dir": str(cycle_dir),
        "session_dir": str(session_dir),
        "capture_success": capture_summary.get("success"),
        "cm_all": capture_summary.get("cm_all"),
        "cs_all": capture_summary.get("cs_all"),
        "per_tag": {
            tag: {
                "status_counts": reject["results"].get(tag, {}).get("cm_status_counts", {}),
                "frame_ok_distribution": reject["results"].get(tag, {}).get("frame_ok_distribution", {}),
                "reject_reason_counts": reject["results"].get(tag, {}).get("reject_reason_counts", {}),
            }
            for tag in ("BSF66F", "BS2DCE", "BSDC91")
        },
        "anchor_profile": profile["per_anchor"],
    }
    (cycle_dir / "cycle_summary.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def write_md(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Responder Profile Overnight",
        "",
        "| Cycle | Delay | Status | Session | Notes |",
        "|---|---:|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row.get('cycle')} | {row.get('delay_uus')} | {row.get('status')} | "
            f"`{row.get('session_dir', '-')}` | {row.get('notes', '-')} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Build/flash/profile anchor responder delay overnight")
    p.add_argument("--port", default="/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00")
    p.add_argument("--delays", default="1000,800")
    p.add_argument("--hours", type=float, default=8.0)
    p.add_argument("--duration", type=int, default=300)
    p.add_argument("--cycles-per-delay", type=int, default=2)
    p.add_argument("--targets", default="BSF66F,BS2DCE,BSDC91")
    p.add_argument("--profiles", default="BSF66F:static,BS2DCE:roto,BSDC91:roto")
    p.add_argument("--static-hz", type=int, default=5)
    p.add_argument("--roto-hz", type=int, default=10)
    p.add_argument("--motion-hz", type=int, default=5)
    p.add_argument("--cm-probe-target", default="BSF66F")
    p.add_argument("--after-reset-wait-s", type=float, default=8.0)
    p.add_argument("--capture-timeout-s", type=float, default=900.0)
    p.add_argument("--out-dir", default=None)
    p.add_argument("--skip-build-flash", action="store_true")
    p.add_argument("--anchor-labels", nargs="+", default=list(ANCHORS.keys()))
    return p


def main() -> int:
    args = build_parser().parse_args()
    started = datetime.now()
    stop_at = started + timedelta(hours=args.hours)
    base_dir = Path(args.out_dir or f"logs/responder_profile_overnight_{started.strftime('%Y%m%d_%H%M%S')}")
    base_dir.mkdir(parents=True, exist_ok=True)
    delays = [int(x.strip()) for x in args.delays.split(",") if x.strip()]
    rows: list[dict[str, Any]] = []
    cycle_no = 1

    round_no = 1
    while datetime.now() < stop_at:
        for delay in delays:
            if datetime.now() >= stop_at:
                break
            marker = f"anchor-prof-quietble-resp{delay}-{timestamp()}"
            delay_dir = base_dir / f"round{round_no:02d}_resp{delay}_{timestamp()}"
            delay_dir.mkdir(parents=True, exist_ok=True)
            if not args.skip_build_flash:
                build_and_flash(delay, marker, delay_dir)
            for _ in range(args.cycles_per_delay):
                if datetime.now() >= stop_at:
                    break
                cycle_dir = delay_dir / f"cycle_{cycle_no:02d}"
                row: dict[str, Any] = {
                    "cycle": cycle_no,
                    "round": round_no,
                    "delay_uus": delay,
                    "marker": marker,
                    "status": "running",
                    "session_dir": "-",
                    "notes": "-",
                }
                rows.append(row)
                try:
                    summary = run_capture_cycle(args, delay, cycle_dir)
                    rc = summary.get("capture_returncode", 0)
                    row["status"] = "ok" if rc == 0 else f"capture_rc_{rc}"
                    row["session_dir"] = summary["session_dir"]
                    row["notes"] = f"cm={summary.get('cm_all')} cs={summary.get('cs_all')}"
                    row["summary"] = summary
                except subprocess.TimeoutExpired as exc:
                    row["status"] = "timeout"
                    row["notes"] = f"timeout cmd={exc.cmd}"
                except subprocess.CalledProcessError as exc:
                    row["status"] = "fail"
                    row["notes"] = f"rc={exc.returncode}"
                except Exception as exc:
                    row["status"] = "fail"
                    row["notes"] = f"{type(exc).__name__}: {exc}"
                finally:
                    row["finished_at"] = datetime.now().isoformat(timespec="seconds")
                    (base_dir / "overnight_summary.json").write_text(
                        json.dumps(rows, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    write_md(base_dir / "overnight_summary.md", rows)
                cycle_no += 1
        round_no += 1

    print(base_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
