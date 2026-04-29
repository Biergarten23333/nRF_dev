#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
import time
import tempfile

import serial


def _reset_with_jlink(snr: str) -> bool:
    jlink = subprocess.run(
        ["bash", "-lc", "command -v JLinkExe"],
        check=False,
        capture_output=True,
        text=True,
    )
    jlink_path = (jlink.stdout or "").strip()
    if not jlink_path:
        return False

    with tempfile.NamedTemporaryFile("w", suffix=".jlink", delete=False) as f:
        cmd_file = f.name
        f.write("Device nRF52832_XXAA\n")
        f.write("SelectInterface SWD\n")
        f.write("Speed 4000\n")
        f.write("Connect\n")
        f.write("Reset\n")
        f.write("Go\n")
        f.write("Exit\n")

    try:
        proc = subprocess.run(
            [
                jlink_path,
                "-NoGui",
                "1",
                "-SelectEmuBySN",
                snr,
                "-CommanderScript",
                cmd_file,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return True
        combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        if combined.strip():
            print(combined, file=sys.stderr)
        return False
    finally:
        try:
            os.unlink(cmd_file)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset a board with nrfjprog, then read its serial port."
    )
    parser.add_argument("snr", help="SEGGER serial number")
    parser.add_argument("port", help="Serial port path, preferably /dev/serial/by-id/...")
    parser.add_argument(
        "--duration",
        type=float,
        default=8.0,
        help="Read duration in seconds after reset",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Serial baud rate",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=2.0,
        help="Seconds to wait for the serial device to reappear after reset",
    )
    args = parser.parse_args()

    # Prevent VSCode Nordic background hotplug scanner from racing J-Link access
    # and triggering interactive probe-selection dialogs.
    subprocess.run(
        ["pkill", "-f", "nrfutil-device --json list --hotplug"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.2)

    # Use SN-pinned JLink reset to avoid interactive probe-selection popup.
    if not _reset_with_jlink(args.snr):
        print(
            f"[error] failed to issue SN-pinned reset via JLinkExe for {args.snr}",
            file=sys.stderr,
        )
        return 3

    deadline = time.time() + args.settle
    while time.time() < deadline:
        if os.path.exists(args.port):
            break
        time.sleep(0.1)

    end = time.time() + args.duration
    while time.time() < end:
        try:
            with serial.Serial(args.port, args.baud, timeout=0.2) as ser:
                while time.time() < end:
                    data = ser.readline()
                    if not data:
                        continue
                    text = data.decode("utf-8", errors="replace").rstrip()
                    if text:
                        print(text)
                        sys.stdout.flush()
                break
        except (serial.SerialException, OSError):
            time.sleep(0.2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
