#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path

import serial
from serial import SerialException

from run_autopos_round import UUIDS

_LIVE_LINE_BUFFERS: dict[int, str] = {}


def auto_timeout_for_sw_sets(sw_sets: int) -> int:
    # Empirical default:
    # - 10 sets stays around the historical 480s budget
    # - 100 sets expands to about 30 minutes
    return max(480, 360 + (15 * sw_sets))


def should_print_live_line(line: str, verbose: int) -> bool:
    if verbose >= 2:
        return True

    if verbose == 1:
        return "ANCHOR candidate ignored:" not in line

    return (
        "SW-" in line or
        "AUTOPOS apply success:" in line or
        "AUTOPOS apply failed:" in line or
        "AUTOPOS anchor " in line and " role verified" in line or
        "PRECHECK FAIL:" in line or
        "END=" in line
    )


def flush_live_buffer(logf, verbose: int) -> None:
    key = id(logf)
    tail = _LIVE_LINE_BUFFERS.pop(key, "")
    if tail and should_print_live_line(tail, verbose):
        sys.stdout.write(tail)
        sys.stdout.flush()


def emit(logf, text: str, live_output: bool, verbose: int = 2) -> None:
    logf.write(text)
    if live_output:
        key = id(logf)
        buffer = _LIVE_LINE_BUFFERS.get(key, "") + text
        lines = buffer.splitlines(keepends=True)
        tail = ""
        if lines and not lines[-1].endswith("\n"):
            tail = lines.pop()
        _LIVE_LINE_BUFFERS[key] = tail
        for line in lines:
            if should_print_live_line(line, verbose):
                sys.stdout.write(line)
        sys.stdout.flush()


def open_port(port: str, timeout_s: float) -> serial.Serial:
    deadline = time.time() + timeout_s
    last_exc = None
    while time.time() < deadline:
        try:
            return serial.Serial(port, 115200, timeout=0.2)
        except Exception as exc:
            last_exc = exc
            time.sleep(0.4)
    raise last_exc


def write_cmd(ser: serial.Serial, cmd: str) -> None:
    ser.write((cmd + "\n").encode())
    ser.flush()


def reopen_port(port: str) -> serial.Serial:
    return open_port(port, 20.0)


def collect_for(
    ser: serial.Serial,
    logf,
    duration_s: float,
    port: str,
    live_output: bool,
    verbose: int,
) -> tuple[serial.Serial, bool]:
    end = time.time() + duration_s
    saw_reopen = False
    while time.time() < end:
        try:
            data = ser.read(4096)
        except (SerialException, OSError):
            emit(logf, "--- SERIAL DISCONNECTED, REOPEN ---\n", live_output, verbose)
            try:
                ser.close()
            except Exception:
                pass
            ser = reopen_port(port)
            emit(logf, "--- SERIAL REOPENED ---\n", live_output, verbose)
            saw_reopen = True
            continue
        if data:
            text = data.decode("utf-8", "ignore")
            emit(logf, text, live_output, verbose)
        else:
            time.sleep(0.05)
    return ser, saw_reopen


def send_cmd_collect(
    ser: serial.Serial,
    logf,
    port: str,
    cmd: str,
    pause_s: float,
    live_output: bool,
    verbose: int,
    resend_after_reopen: bool = True,
) -> serial.Serial:
    emit(logf, f">>> {cmd}\n", live_output, verbose)
    try:
        write_cmd(ser, cmd)
    except Exception:
        try:
            ser.close()
        except Exception:
            pass
        ser = reopen_port(port)
        write_cmd(ser, cmd)
    ser, saw_reopen = collect_for(ser, logf, pause_s, port, live_output, verbose)
    if saw_reopen and resend_after_reopen:
        emit(logf, f">>> RESEND {cmd}\n", live_output, verbose)
        write_cmd(ser, cmd)
        ser, _ = collect_for(ser, logf, max(0.6, pause_s), port, live_output, verbose)
    return ser


def wait_for_autopos_idle(
    ser: serial.Serial,
    logf,
    port: str,
    timeout_s: float,
    live_output: bool,
    verbose: int,
) -> tuple[serial.Serial, bool]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        emit(logf, ">>> autopos status\n", live_output, verbose)
        try:
            write_cmd(ser, "autopos status")
        except Exception:
            try:
                ser.close()
            except Exception:
                pass
            ser = reopen_port(port)
            write_cmd(ser, "autopos status")

        pause_end = time.time() + 1.4
        while time.time() < pause_end:
            try:
                data = ser.read(4096)
            except (SerialException, OSError):
                emit(logf, "--- SERIAL DISCONNECTED, REOPEN ---\n", live_output, verbose)
                try:
                    ser.close()
                except Exception:
                    pass
                ser = reopen_port(port)
                emit(logf, "--- SERIAL REOPENED ---\n", live_output, verbose)
                break
            if data:
                text = data.decode("utf-8", "ignore")
                emit(logf, text, live_output, verbose)
                if "AUTOPOS: mode=AUTOPOS state=idle" in text:
                    return ser, True
            else:
                time.sleep(0.05)
    return ser, False


def preflight_clean_autopos_start(
    ser: serial.Serial,
    logf,
    port: str,
    live_output: bool,
    verbose: int,
) -> tuple[serial.Serial, bool]:
    # Reset AUTOPOS state in-place without a RECV reboot boundary.
    ser = send_cmd_collect(
        ser,
        logf,
        port,
        "status",
        0.8,
        live_output,
        verbose,
        resend_after_reopen=False,
    )
    ser = send_cmd_collect(
        ser,
        logf,
        port,
        "mode autopos",
        1.6,
        live_output,
        verbose,
        resend_after_reopen=False,
    )
    ser, _ = collect_for(ser, logf, 0.8, port, live_output, verbose)
    ser, idle_ok = wait_for_autopos_idle(ser, logf, port, 8.0, live_output, verbose)
    if not idle_ok:
        ser = send_cmd_collect(
            ser,
            logf,
            port,
            "mode autopos",
            1.6,
            live_output,
            verbose,
            resend_after_reopen=False,
        )
        ser, _ = collect_for(ser, logf, 0.8, port, live_output, verbose)
        ser, idle_ok = wait_for_autopos_idle(ser, logf, port, 5.0, live_output, verbose)
    return ser, idle_ok


def round_capture(
    port: str,
    master: str,
    out_dir: Path,
    timeout_s: int,
    live_output: bool = True,
    verbose: int = 2,
) -> dict:
    log_path = out_dir / "master.log"
    result = {
        "master": master,
        "success": False,
        "sw_seen": False,
        "apply_success_seen": False,
        "verified_count": 0,
        "sw_line": "",
        "sw_lines": [],
        "sw_count": 0,
        "log_path": str(log_path),
        "error": "",
    }
    verified = set()
    ser = None
    try:
        ser = open_port(port, 20.0)
        with open(log_path, "w", buffering=1) as logf:
            emit(logf, f"PORT={port}\n", live_output, verbose)
            emit(logf, f"START={time.strftime('%Y-%m-%d %H:%M:%S')}\n", live_output, verbose)
            emit(logf, f"MASTER={master}\n", live_output, verbose)
            time.sleep(1.0)
            while ser.in_waiting:
                data = ser.read(ser.in_waiting)
                if data:
                    emit(logf, data.decode("utf-8", "ignore"), live_output, verbose)

            ser, preflight_ok = preflight_clean_autopos_start(
                ser,
                logf,
                port,
                live_output,
                verbose,
            )
            if not preflight_ok:
                result["error"] = "autopos_idle_not_reached"
                emit(logf, "PRECHECK FAIL: AUTOPOS idle not reached\n", live_output, verbose)
                emit(logf, f"END={time.strftime('%Y-%m-%d %H:%M:%S')}\n", live_output, verbose)
                flush_live_buffer(logf, verbose)
                return result

            cmds = []
            cmds.append(("device kind anchor", 0.35))
            for label, uuid in UUIDS.items():
                cmds.append((f"autopos map {label} {uuid}", 0.35))
            cmds.extend(
                [
                    (f"autopos round {master}", 0.5),
                    ("autopos status", 0.5),
                    ("autopos apply", 0.5),
                ]
            )

            for cmd, pause_s in cmds:
                ser = send_cmd_collect(
                    ser,
                    logf,
                    port,
                    cmd,
                    pause_s,
                    live_output,
                    verbose,
                )

            deadline = time.time() + timeout_s
            status_marks = {30, 60, 120, 180, 240, 300, 360, 420}
            sent_marks = set()
            while time.time() < deadline:
                elapsed = int(timeout_s - (deadline - time.time()))
                if (not result["apply_success_seen"] and
                        elapsed in status_marks and elapsed not in sent_marks):
                    cmd = "autopos status"
                    emit(logf, f">>> {cmd} @t={elapsed}\n", live_output, verbose)
                    try:
                        write_cmd(ser, cmd)
                    except Exception:
                        try:
                            ser.close()
                        except Exception:
                            pass
                        ser = reopen_port(port)
                        write_cmd(ser, cmd)
                    sent_marks.add(elapsed)

                try:
                    data = ser.read(4096)
                except (SerialException, OSError):
                    emit(logf, "--- SERIAL DISCONNECTED, REOPEN ---\n", live_output, verbose)
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = reopen_port(port)
                    emit(logf, "--- SERIAL REOPENED ---\n", live_output, verbose)
                    continue

                if data:
                    text = data.decode("utf-8", "ignore")
                    emit(logf, text, live_output, verbose)
                    for line in text.splitlines():
                        if "AUTOPOS anchor " in line and " role verified" in line:
                            parts = line.split("AUTOPOS anchor ", 1)[1]
                            verified.add(parts.split(" ", 1)[0])
                        if f"AUTOPOS apply success: master={master}" in line:
                            result["apply_success_seen"] = True
                        if f"SW-{master}," in line:
                            line = line.strip()
                            result["sw_seen"] = True
                            result["sw_line"] = line
                            result["sw_lines"].append(line)
                            result["sw_count"] = len(result["sw_lines"])
                    if result["apply_success_seen"] and result["sw_count"] >= round_capture.target_sw_sets:
                        result["success"] = True
                        break
                else:
                    time.sleep(0.1)

            result["verified_count"] = len(verified)
            if not result["success"]:
                if not result["apply_success_seen"]:
                    result["error"] = "apply_success_not_seen"
                elif not result["sw_seen"]:
                    result["error"] = "sw_not_seen"
                else:
                    result["error"] = f"insufficient_sw_sets:{result['sw_count']}/{round_capture.target_sw_sets}"
            emit(logf, f"END={time.strftime('%Y-%m-%d %H:%M:%S')}\n", live_output, verbose)
            flush_live_buffer(logf, verbose)
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AUTOPOS sweep loop A-H and capture SW-master lines")
    parser.add_argument("--port", required=True, help="52840 CDC serial port")
    parser.add_argument("--order", default="ABCDEFGH", help="Master order to run, e.g. ABCDEFGH or BCD")
    parser.add_argument("--timeout-s", type=int, default=None, help="Per-round timeout; defaults scale with --sw-sets")
    parser.add_argument("--sw-sets", type=int, default=1, help="Required SW lines per round before finishing")
    parser.add_argument("--verbose", type=int, choices=[0, 1, 2], default=1,
                        help="Live stdout verbosity: 0=SW-X/failures only, 1=normal without ignored scan noise, 2=full flow")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    parser.add_argument(
        "--no-live-output",
        action="store_true",
        help="Do not mirror runtime logs to stdout; write to log files only.",
    )
    args = parser.parse_args()

    if args.sw_sets < 1:
        raise SystemExit("--sw-sets must be >= 1")

    if args.timeout_s is None:
        args.timeout_s = auto_timeout_for_sw_sets(args.sw_sets)

    round_capture.target_sw_sets = args.sw_sets

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "port": args.port,
        "order": list(args.order),
        "sw_sets": args.sw_sets,
        "timeout_s": args.timeout_s,
        "verbose": args.verbose,
        "rounds": {},
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    for master in args.order:
        round_dir = out_dir / f"round_{master}"
        round_dir.mkdir(parents=True, exist_ok=True)
        print(f"=== AUTOPOS SWEEP {master} ===", flush=True)
        result = round_capture(
            args.port,
            master,
            round_dir,
            args.timeout_s,
            live_output=not args.no_live_output,
            verbose=args.verbose,
        )
        summary["rounds"][master] = result
        with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps(result, indent=2), flush=True)
        if not result["success"]:
            return 1

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
