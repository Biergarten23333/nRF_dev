#!/usr/bin/env python3
import argparse
import time
from pathlib import Path

import serial
from serial import SerialException


UUIDS = {
    "A": "F3BB7A04104F9CB8561DDDACB9E53714",
    "B": "B9179575C776C98F1CB132DD6EDC6223",
    "C": "CEE5A7EFCB35F8A56B430047629F5309",
    "D": "B2B5FA625534A8C617135DCAFC9E036A",
    "E": "A892AF05DD59CF0D0D3408AD74F364A1",
    "F": "840C68591E90019821AACFF1B73AAA34",
    "G": "B3087BC3D87CCCD316AEDC6B71D6677F",
    "H": "CF12E703AC1A118F6AB440AB05B0BA23",
}


def open_port(port: str, timeout_s: float) -> serial.Serial:
    deadline = time.time() + timeout_s
    last_exc = None
    while time.time() < deadline:
        try:
            return serial.Serial(port, 115200, timeout=0.2)
        except Exception as exc:  # pragma: no cover - device dependent
            last_exc = exc
            time.sleep(0.4)
    raise last_exc


def drain_until(ser: serial.Serial, logf, port: str, until: float) -> serial.Serial:
    while time.time() < until:
        try:
            data = ser.read(4096)
            if data:
                logf.write(data.decode("utf-8", "ignore"))
        except (SerialException, OSError):  # pragma: no cover - device dependent
            logf.write("--- SERIAL DISCONNECTED, REOPEN ---\n")
            try:
                ser.close()
            except Exception:
                pass
            ser = open_port(port, 20.0)
            logf.write("--- SERIAL REOPENED ---\n")
    return ser


def send_cmd(ser: serial.Serial, logf, port: str, cmd: str) -> serial.Serial:
    try:
        ser.write((cmd + "\n").encode())
        ser.flush()
        return ser
    except Exception:  # pragma: no cover - device dependent
        try:
            ser.close()
        except Exception:
            pass
        ser = open_port(port, 20.0)
        ser.write((cmd + "\n").encode())
        ser.flush()
        return ser


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AUTOPOS round with reconnect-safe CDC logging")
    parser.add_argument("--port", required=True, help="52840 CDC serial port")
    parser.add_argument("--master", required=True, choices=sorted(UUIDS.keys()), help="Anchor label to stage as master")
    parser.add_argument("--duration-s", type=int, default=200, help="Capture duration after apply")
    parser.add_argument("--out-dir", required=True, help="Output directory for logs")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "master.log"

    status_marks = [20, 60, 120, 180]
    pre_cmds = [("status", 1.0), ("mode autopos", 1.8)]
    for label, uuid in UUIDS.items():
        pre_cmds.append((f"autopos map {label} {uuid}", 0.35))
    pre_cmds.extend(
        [
            (f"autopos round {args.master}", 0.5),
            ("autopos status", 0.5),
            ("autopos apply", 0.5),
        ]
    )

    with open(log_path, "w", buffering=1) as logf:
        logf.write(f"PORT={args.port}\n")
        logf.write(f"START={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        logf.write(f"MASTER={args.master}\n")
        ser = open_port(args.port, 20.0)
        ser.write(b"\n")
        ser.flush()

        for cmd, pause_s in pre_cmds:
            logf.write(f">>> {cmd}\n")
            ser = send_cmd(ser, logf, args.port, cmd)
            ser = drain_until(ser, logf, args.port, time.time() + pause_s)

        start = time.time()
        deadline = start + args.duration_s
        status_idx = 0
        while time.time() < deadline:
            elapsed = int(time.time() - start)
            if status_idx < len(status_marks) and elapsed >= status_marks[status_idx]:
                cmd = "autopos status"
                logf.write(f">>> {cmd} @t={elapsed}\n")
                ser = send_cmd(ser, logf, args.port, cmd)
                status_idx += 1
            ser = drain_until(ser, logf, args.port, min(time.time() + 0.25, deadline))

        logf.write(f"END={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        try:
            ser.close()
        except Exception:
            pass

    print(log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
