#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import time
from pathlib import Path

import serial


ANCHOR_LINE_RE = re.compile(r"ANCHOR: .*ANCHOR_ID:\s*([A-Z])", re.IGNORECASE)
UUID_LINE_RE = re.compile(r"DEVICE_UUID:\s*([0-9A-F]+)", re.IGNORECASE)
PORT_SN_RE = re.compile(r"usb-SEGGER_J-Link_0*([0-9]{9})-if")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Resolve Anchor diagnostic serial port non-interactively.")
    ap.add_argument("--target-anchor-id", default="A")
    ap.add_argument("--target-uuid", default="")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--read-window-s", type=float, default=3.5)
    return ap.parse_args()


def lsof_busy(path: str) -> bool:
    cp = subprocess.run(["lsof", path], check=False, capture_output=True, text=True)
    return cp.returncode == 0 and len(cp.stdout.strip().splitlines()) > 1


def reset_by_snr(snr: str) -> None:
    cmd_file = Path(f"/tmp/jlink_reset_{snr}.cmd")
    cmd_file.write_text(
        "Device nRF52832_XXAA\n"
        "SelectInterface SWD\n"
        "Speed 4000\n"
        "Connect\n"
        "Reset\n"
        "Go\n"
        "Exit\n",
        encoding="utf-8",
    )
    try:
        subprocess.run(
            ["JLinkExe", "-NoGui", "1", "-SelectEmuBySN", snr, "-CommanderScript", str(cmd_file)],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        try:
            cmd_file.unlink()
        except FileNotFoundError:
            pass


def read_window(port: str, baud: int, sec: float) -> list[str]:
    lines: list[str] = []
    ser = serial.Serial(port, baud, timeout=0.2, exclusive=True, dsrdtr=False, rtscts=False)
    try:
        ser.reset_input_buffer()
        t_end = time.time() + sec
        while time.time() < t_end:
            raw = ser.readline()
            if not raw:
                continue
            s = raw.decode("utf-8", errors="replace").strip()
            if s:
                lines.append(s)
    finally:
        ser.close()
    return lines


def main() -> int:
    args = parse_args()
    target_anchor = args.target_anchor_id.strip().upper()
    target_uuid = args.target_uuid.strip().upper()
    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    candidates = sorted(glob.glob("/dev/serial/by-id/usb-SEGGER_J-Link_000760*-if00"))
    result = {
        "target_anchor_id": target_anchor,
        "target_uuid": target_uuid,
        "resolved": False,
        "port": "",
        "snr": "",
        "evidence": "",
        "probed": [],
    }

    for port in candidates:
        m = PORT_SN_RE.search(port)
        if not m:
            continue
        snr = m.group(1)
        if lsof_busy(port):
            result["probed"].append({"port": port, "snr": snr, "status": "busy"})
            continue

        try:
            reset_by_snr(snr)
            lines = read_window(port, args.baud, args.read_window_s)
        except Exception as e:
            result["probed"].append({"port": port, "snr": snr, "status": f"error:{e}"})
            continue

        anchor_id = ""
        uuid = ""
        for line in lines:
            ma = ANCHOR_LINE_RE.search(line)
            if ma:
                anchor_id = ma.group(1).upper()
            mu = UUID_LINE_RE.search(line)
            if mu:
                uuid = mu.group(1).upper()
            if anchor_id and uuid:
                break

        entry = {
            "port": port,
            "snr": snr,
            "status": "ok",
            "anchor_id": anchor_id,
            "uuid": uuid,
            "lines": len(lines),
        }
        result["probed"].append(entry)

        if anchor_id == target_anchor:
            if target_uuid and uuid and uuid != target_uuid:
                continue
            result["resolved"] = True
            result["port"] = port
            result["snr"] = snr
            result["evidence"] = f"anchor_id={anchor_id} uuid={uuid}"
            break

    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["resolved"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
