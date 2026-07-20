#!/usr/bin/env python3
"""Push APOS anchor layout to Tags and verify by readback.

This script treats APOS like OTA version matching:
  1. target one Tag at a time
  2. send each APOS row and require APOS_OK from that Tag
  3. send APOS_COMMIT and require APOS_COMMIT_OK
  4. send APOS_STATUS and compare all coordinates

It intentionally does not rely on the Master's "apos rc" alone.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import TextIO

import serial
from serial import SerialException


APOS_OK_RE = re.compile(
    r"(?P<tag>BS[0-9A-F]{4}|NUS|BLE(?:\[\d+[^\]]*\])?) notify:\s+"
    r"APOS_OK ID=(?P<id>\d+) X=(?P<x>-?\d+) Y=(?P<y>-?\d+) Z=(?P<z>-?\d+)"
)
APOS_STATUS_RE = re.compile(
    r"(?P<tag>BS[0-9A-F]{4}|NUS|BLE(?:\[\d+[^\]]*\])?) notify:\s+"
    r"APOS (?P<id>\d+) (?P<x>-?\d+) (?P<y>-?\d+) (?P<z>-?\d+)"
)
APOS_DONE_RE = re.compile(
    r"(?P<tag>BS[0-9A-F]{4}|NUS|BLE(?:\[\d+[^\]]*\])?) notify:\s+"
    r"APOS_STATUS_DONE SRC=(?P<src>\S+)"
)
APOS_COMMIT_RE = re.compile(
    r"(?P<tag>BS[0-9A-F]{4}|NUS|BLE(?:\[\d+[^\]]*\])?) notify:\s+"
    r"APOS_COMMIT_OK N=(?P<n>\d+)"
)


DEFAULT_LAYOUT = {
    0: (0, 0, 0),
    1: (4738, 0, 0),
    2: (3986, 3719, 34),
    3: (-455, 2738, 0),
    4: (66, -44, 1735),
    5: (4411, 71, 1552),
    6: (3851, 3760, 1640),
    7: (-553, 2722, 1561),
}


def parse_targets(value: str) -> list[str]:
    return [x.strip().upper() for x in re.split(r"[,\s]+", value.strip()) if x.strip()]


def parse_layout_file(path: Path) -> dict[int, tuple[int, int, int]]:
    text = path.read_text(encoding="utf-8")
    layout: dict[int, tuple[int, int, int]] = {}
    if path.suffix.lower() == ".json":
        obj = json.loads(text)
        anchors = obj.get("anchors", obj) if isinstance(obj, dict) else obj
        if isinstance(anchors, dict):
            iterable = anchors.values()
        else:
            iterable = anchors
        for item in iterable:
            aid = int(item.get("id", item.get("anchor_id")))
            layout[aid] = (int(item["x_mm"]), int(item["y_mm"]), int(item["z_mm"]))
        return layout

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.upper().startswith("APOS "):
            parts = line.split()
            if len(parts) != 5:
                raise ValueError(f"Bad APOS line: {line}")
            _, aid, x, y, z = parts
            layout[int(aid)] = (int(x), int(y), int(z))
            continue
        parts = re.split(r"[,\s]+", line)
        if len(parts) == 4:
            aid, x, y, z = parts
            layout[int(aid)] = (int(x), int(y), int(z))
            continue
        raise ValueError(f"Bad layout line: {line}")
    return layout


def open_serial(port: str, baud: int, timeout: float = 0.2, retries: int = 120) -> serial.Serial:
    last: Exception | None = None
    for _ in range(retries):
        try:
            return serial.Serial(port, baud, timeout=timeout, write_timeout=2)
        except (OSError, SerialException) as exc:
            last = exc
            time.sleep(0.25)
    raise last if last is not None else RuntimeError("serial open failed")


def write_log(logf: TextIO, text: str, live: bool = True) -> None:
    logf.write(text)
    logf.flush()
    if live:
        print(text, end="", flush=True)


def read_lines(ser: serial.Serial, logf: TextIO, duration_s: float, live: bool = False) -> list[str]:
    deadline = time.time() + duration_s
    lines: list[str] = []
    buf = b""
    while time.time() < deadline:
        try:
            data = ser.read(ser.in_waiting or 1)
        except (OSError, SerialException):
            raise
        if not data:
            continue
        buf += data
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            line = raw.decode("utf-8", errors="replace").rstrip("\r")
            lines.append(line)
            write_log(logf, line + "\n", live=live)
    if buf:
        line = buf.decode("utf-8", errors="replace").rstrip("\r")
        lines.append(line)
        write_log(logf, line + "\n", live=live)
    return lines


def send_cmd(ser: serial.Serial, logf: TextIO, cmd: str, wait_s: float, live: bool = False) -> list[str]:
    write_log(logf, f">>> {cmd}\n", live=True)
    ser.write((cmd + "\n").encode("utf-8"))
    ser.flush()
    return read_lines(ser, logf, wait_s, live=live)


def send_with_reopen(
    ser: serial.Serial,
    port: str,
    baud: int,
    logf: TextIO,
    cmd: str,
    wait_s: float,
    live: bool = False,
) -> tuple[serial.Serial, list[str]]:
    try:
        return ser, send_cmd(ser, logf, cmd, wait_s, live=live)
    except (OSError, SerialException) as exc:
        write_log(logf, f"[WARN] serial error after {cmd!r}: {exc}; reopening\n")
        try:
            ser.close()
        except Exception:
            pass
        ser = open_serial(port, baud)
        time.sleep(1.0)
        try:
            read_lines(ser, logf, 1.0)
        except (OSError, SerialException) as exc2:
            write_log(logf, f"[WARN] serial drain after reopen failed: {exc2}; reopening again\n")
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(1.5)
            ser = open_serial(port, baud)
            time.sleep(1.0)
        return ser, send_cmd(ser, logf, cmd, wait_s, live=live)


def line_has_target(line: str, target: str) -> bool:
    # APOS verification must be attributed to the requested Tag only. Generic
    # NUS/BLE lines are intentionally ignored because three Tags can be online.
    return f"{target} notify:" in line


def wait_for_target_ready(
    ser: serial.Serial,
    port: str,
    baud: int,
    logf: TextIO,
    target: str,
    *,
    reset_recv_mode: bool = False,
) -> serial.Serial:
    setup = []
    if reset_recv_mode:
        # This may reboot Master_Tag when stale BLE links cannot be cleared, so
        # use it only for initial recovery, not every APOS retry.
        setup.append(("mode recv", 3.0))
    setup += [
        ("device kind tag", 1.0),
        ("ota_target token -1", 0.4),
        ("ota_target prefix BS", 0.4),
        ("ota_target uuid -", 0.4),
        ("conn", 8.0),
    ]
    text = ""
    for cmd, wait_s in setup:
        ser, lines = send_with_reopen(ser, port, baud, logf, cmd, wait_s)
        text += "\n".join(lines) + "\n"
    if f"{target} notify: CFG_OK" not in text and f"{target} notify:" not in text:
        # Give discovery one more chance.
        ser, lines = send_with_reopen(ser, port, baud, logf, "conn", 12.0)
        text += "\n".join(lines) + "\n"
    return ser


def send_tag_command_expect(
    ser: serial.Serial,
    port: str,
    baud: int,
    logf: TextIO,
    cmd: str,
    expected_re: re.Pattern[str],
    target: str,
    attempts: int = 4,
    wait_s: float = 2.0,
) -> tuple[serial.Serial, bool, list[dict[str, object]]]:
    attempt_results: list[dict[str, object]] = []
    # New Master_Tag firmware supports APOS_TO, an atomic one-line target+send
    # command. This avoids stale runtime target state such as target_name="-".
    wire_cmd = f"APOS_TO {target} {cmd}" if cmd.startswith("APOS") else cmd
    for attempt in range(1, attempts + 1):
        ser, lines = send_with_reopen(ser, port, baud, logf, wire_cmd, wait_s)
        text = "\n".join(lines)
        matched = False
        for line in lines:
            if not line_has_target(line, target):
                continue
            if expected_re.search(line):
                matched = True
                break
        rc_ok = "rc=" not in text or "rc=-" not in text
        attempt_results.append({"attempt": attempt, "matched": matched, "rc_ok": rc_ok})
        if matched:
            return ser, True, attempt_results
        # Reconnect/re-arm the target if the command did not get through.
        ser = wait_for_target_ready(ser, port, baud, logf, target)
        time.sleep(0.3)
    return ser, False, attempt_results


def collect_status(
    ser: serial.Serial,
    port: str,
    baud: int,
    logf: TextIO,
    target: str,
    layout: dict[int, tuple[int, int, int]],
) -> tuple[serial.Serial, dict[str, object]]:
    result: dict[str, object] = {
        "status_seen": False,
        "source": "",
        "readback": {},
        "mismatches": [],
        "layout_match": False,
    }
    for attempt in range(1, 5):
        ser, lines = send_with_reopen(ser, port, baud, logf, f"APOS_TO {target} APOS_STATUS", 4.0)
        readback: dict[int, tuple[int, int, int]] = {}
        source = ""
        done = False
        for line in lines:
            if not line_has_target(line, target):
                continue
            m = APOS_STATUS_RE.search(line)
            if m:
                readback[int(m.group("id"))] = (
                    int(m.group("x")),
                    int(m.group("y")),
                    int(m.group("z")),
                )
                continue
            d = APOS_DONE_RE.search(line)
            if d:
                done = True
                source = d.group("src")
        if done and len(readback) >= len(layout):
            mismatches = []
            for aid, expected in sorted(layout.items()):
                actual = readback.get(aid)
                if actual != expected:
                    mismatches.append({"id": aid, "expected": expected, "actual": actual})
            result.update(
                {
                    "status_seen": True,
                    "source": source,
                    "readback": {str(k): v for k, v in sorted(readback.items())},
                    "mismatches": mismatches,
                    "layout_match": not mismatches,
                }
            )
            return ser, result
        result["last_attempt"] = attempt
        ser = wait_for_target_ready(ser, port, baud, logf, target)
    return ser, result


def push_one_target(
    ser: serial.Serial,
    port: str,
    baud: int,
    logf: TextIO,
    target: str,
    layout: dict[int, tuple[int, int, int]],
    *,
    reset_recv_mode: bool = False,
) -> tuple[serial.Serial, dict[str, object]]:
    summary: dict[str, object] = {
        "target": target,
        "apos_rows": {},
        "commit_ok": False,
        "layout_match": False,
    }
    ser = wait_for_target_ready(
        ser, port, baud, logf, target, reset_recv_mode=reset_recv_mode
    )

    # Do not switch runtime profile here. APOS_TO targets each command
    # atomically, and APOS_STATUS readback is the source of truth.
    summary["mode_aota_ok"] = None
    summary["mode_attempts"] = []

    all_rows_ok = True
    for aid, (x, y, z) in sorted(layout.items()):
        cmd = f"APOS {aid} {x} {y} {z}"
        row_re = re.compile(
            rf"notify:\s+APOS_OK ID={aid}\s+X={x}\s+Y={y}\s+Z={z}(?:\s|$)"
        )
        ser, ok, attempts = send_tag_command_expect(
            ser, port, baud, logf, cmd, row_re, target, attempts=5, wait_s=1.5
        )
        summary["apos_rows"][str(aid)] = {"ok": ok, "attempts": attempts}
        all_rows_ok = all_rows_ok and ok

    commit_re = re.compile(r"notify:\s+APOS_COMMIT_OK N=8")
    ser, commit_ok, commit_attempts = send_tag_command_expect(
        ser, port, baud, logf, "APOS_COMMIT", commit_re, target, attempts=5, wait_s=2.5
    )
    summary["commit_ok"] = commit_ok
    summary["commit_attempts"] = commit_attempts

    ser, status = collect_status(ser, port, baud, logf, target, layout)
    summary["status"] = status
    summary["layout_match"] = bool(all_rows_ok and commit_ok and status.get("layout_match"))
    print(
        f"APOS_VERIFY target={target} rows_ok={int(all_rows_ok)} "
        f"commit_ok={int(commit_ok)} layout_match={summary['layout_match']} "
        f"source={status.get('source','-')}",
        flush=True,
    )
    return ser, summary


def main() -> int:
    ap = argparse.ArgumentParser(description="Push APOS layout and verify per-Tag readback.")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--targets", default="BSF66F,BS2DCE,BSDC91")
    ap.add_argument("--layout-file", help="JSON, APOS text, or id x y z text file")
    ap.add_argument("--use-default-layout", action="store_true", help="Use current V3 free/loose APOS layout")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--reset-master", action="store_true", help="Run 'mode recv' before first target")
    args = ap.parse_args()

    targets = parse_targets(args.targets)
    if not targets:
        raise SystemExit("No targets")
    if args.layout_file:
        layout = parse_layout_file(Path(args.layout_file))
    elif args.use_default_layout:
        layout = dict(DEFAULT_LAYOUT)
    else:
        raise SystemExit("Provide --layout-file or --use-default-layout")
    missing = sorted(set(range(8)) - set(layout))
    if missing:
        raise SystemExit(f"Layout missing anchors: {missing}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "push_apos.log"
    summary: dict[str, object] = {
        "port": args.port,
        "targets": targets,
        "layout": {str(k): v for k, v in sorted(layout.items())},
        "per_target": {},
    }

    with log_path.open("w", encoding="utf-8") as logf:
        ser = open_serial(args.port, args.baud)
        try:
            read_lines(ser, logf, 1.0)
            for idx, target in enumerate(targets):
                write_log(logf, f"=== APOS TARGET {target} ===\n")
                ser, target_summary = push_one_target(
                    ser,
                    args.port,
                    args.baud,
                    logf,
                    target,
                    layout,
                    reset_recv_mode=(idx == 0 and args.reset_master),
                )
                summary["per_target"][target] = target_summary
                (out_dir / "summary.json").write_text(
                    json.dumps(summary, indent=2) + "\n", encoding="utf-8"
                )
        finally:
            try:
                ser.close()
            except Exception:
                pass

    all_match = all(
        bool(v.get("layout_match"))
        for v in (summary.get("per_target") or {}).values()
        if isinstance(v, dict)
    ) and len(summary.get("per_target") or {}) == len(targets)
    summary["all_layout_match"] = all_match
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"APOS_VERIFY_ALL layout_match={all_match} summary={out_dir / 'summary.json'}")
    return 0 if all_match else 2


if __name__ == "__main__":
    raise SystemExit(main())
