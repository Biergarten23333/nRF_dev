#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Run 3-tag RECV/TDMA capture while recording passive UWB listener UL data."
    )
    ap.add_argument(
        "--listener-port",
        default="/dev/serial/by-id/usb-SEGGER_J-Link_000760185886-if00",
        help="Pure UWB listener serial port.",
    )
    ap.add_argument(
        "--out-dir",
        default=f"logs/recv_tdma_with_listener_{time.strftime('%Y%m%d_%H%M%S')}",
        help="Output directory for both capture streams.",
    )
    ap.add_argument(
        "--listener-extra-s",
        type=float,
        default=360.0,
        help="Keep listener running this many seconds longer than RECV capture. This must cover anchor preflight and controller recovery time.",
    )
    ap.add_argument(
        "capture_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to scripts/run_recv_tdma_capture.py after '--'.",
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    base = Path(args.out_dir)
    base.mkdir(parents=True, exist_ok=True)

    capture_args = list(args.capture_args)
    if capture_args and capture_args[0] == "--":
        capture_args = capture_args[1:]

    duration = 180.0
    for idx, item in enumerate(capture_args):
        if item == "--duration" and idx + 1 < len(capture_args):
            duration = float(capture_args[idx + 1])
            break

    if "--out-dir" not in capture_args:
        capture_args.extend(["--out-dir", str(base / "recv")])

    listener_cmd = [
        sys.executable,
        "scripts/capture_uwb_listener.py",
        "--port",
        args.listener_port,
        "--duration",
        str(duration + args.listener_extra_s),
        "--out-dir",
        str(base / "listener"),
    ]
    capture_cmd = [sys.executable, "scripts/run_recv_tdma_capture.py", *capture_args]

    (base / "commands.json").write_text(
        json.dumps(
            {
                "listener_cmd": listener_cmd,
                "capture_cmd": capture_cmd,
                "duration_s": duration,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"[WRAP] out_dir={base}", flush=True)
    print(f"[WRAP] listener: {' '.join(listener_cmd)}", flush=True)
    listener = subprocess.Popen(listener_cmd)
    time.sleep(1.0)
    print(f"[WRAP] capture: {' '.join(capture_cmd)}", flush=True)
    capture = subprocess.run(capture_cmd, check=False)

    if listener.poll() is None:
        try:
            listener.terminate()
            listener.wait(timeout=5)
        except subprocess.TimeoutExpired:
            listener.kill()
            listener.wait(timeout=5)

    summary = {
        "capture_returncode": capture.returncode,
        "listener_returncode": listener.returncode,
        "out_dir": str(base),
    }
    (base / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return capture.returncode


if __name__ == "__main__":
    raise SystemExit(main())
