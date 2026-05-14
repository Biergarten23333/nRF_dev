#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path

import serial
from serial import SerialException

from run_autopos_sweep_loop import UUIDS, round_capture


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


def reopen_port(port: str) -> serial.Serial:
    return open_port(port, 20.0)


def write_cmd(ser: serial.Serial, cmd: str) -> None:
    ser.write((cmd + "\n").encode())
    ser.flush()


def drain_boot(ser: serial.Serial, logf, port: str, timeout_s: float) -> serial.Serial:
    deadline = time.time() + timeout_s
    saw_mode = False
    saw_uart = False
    while time.time() < deadline:
        try:
            data = ser.read(4096)
        except (SerialException, OSError):
            logf.write("--- SERIAL DISCONNECTED, REOPEN ---\n")
            try:
                ser.close()
            except Exception:
                pass
            ser = reopen_port(port)
            logf.write("--- SERIAL REOPENED ---\n")
            continue
        if data:
            text = data.decode("utf-8", "ignore")
            logf.write(text)
            if "Control mode loaded: RECV" in text:
                saw_mode = True
            if "UART control ready:" in text:
                saw_uart = True
            if saw_mode and saw_uart:
                return ser
        else:
            time.sleep(0.05)
    return ser


def send_cmd_and_collect(
    ser: serial.Serial,
    logf,
    port: str,
    cmd: str,
    pause_s: float,
    resend_after_reopen: bool = True,
) -> serial.Serial:
    logf.write(f">>> {cmd}\n")
    try:
        write_cmd(ser, cmd)
    except Exception:
        try:
            ser.close()
        except Exception:
            pass
        ser = reopen_port(port)
        write_cmd(ser, cmd)

    end = time.time() + pause_s
    saw_reopen = False
    while time.time() < end:
        try:
            data = ser.read(4096)
        except (SerialException, OSError):
            logf.write("--- SERIAL DISCONNECTED, REOPEN ---\n")
            try:
                ser.close()
            except Exception:
                pass
            ser = reopen_port(port)
            logf.write("--- SERIAL REOPENED ---\n")
            saw_reopen = True
            continue
        if data:
            logf.write(data.decode("utf-8", "ignore"))
        else:
            time.sleep(0.05)

    if saw_reopen and resend_after_reopen:
        logf.write(f">>> RESEND {cmd}\n")
        write_cmd(ser, cmd)
        end = time.time() + max(0.6, pause_s)
        while time.time() < end:
            try:
                data = ser.read(4096)
            except (SerialException, OSError):
                logf.write("--- SERIAL DISCONNECTED, REOPEN ---\n")
                try:
                    ser.close()
                except Exception:
                    pass
                ser = reopen_port(port)
                logf.write("--- SERIAL REOPENED ---\n")
                continue
            if data:
                logf.write(data.decode("utf-8", "ignore"))
            else:
                time.sleep(0.05)

    return ser


def wait_for_patterns(
    ser: serial.Serial,
    logf,
    port: str,
    timeout_s: float,
    patterns: list[str],
) -> tuple[serial.Serial, dict[str, bool]]:
    deadline = time.time() + timeout_s
    seen = {pattern: False for pattern in patterns}
    while time.time() < deadline:
        try:
            data = ser.read(4096)
        except (SerialException, OSError):
            logf.write("--- SERIAL DISCONNECTED, REOPEN ---\n")
            try:
                ser.close()
            except Exception:
                pass
            ser = reopen_port(port)
            logf.write("--- SERIAL REOPENED ---\n")
            continue
        if not data:
            time.sleep(0.05)
            continue

        text = data.decode("utf-8", "ignore")
        logf.write(text)
        for pattern in patterns:
            if pattern in text:
                seen[pattern] = True
        if all(seen.values()):
            break
    return ser, seen


def run_tag_cm_capture(
    port: str,
    out_dir: Path,
    target_name: str,
    wait_mode_ok_s: float,
    capture_s: float,
    min_cm_lines: int,
) -> dict:
    log_path = out_dir / "tag_cm.log"
    result = {
        "success": False,
        "mode_ok_seen": False,
        "cm_line_count": 0,
        "first_cm_line": "",
        "log_path": str(log_path),
        "error": "",
    }

    ser = None
    try:
        ser = open_port(port, 20.0)
        with open(log_path, "w", buffering=1) as logf:
            logf.write(f"PORT={port}\n")
            logf.write(f"START={time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            logf.write(f"TARGET={target_name}\n")
            time.sleep(1.0)
            while ser.in_waiting:
                data = ser.read(ser.in_waiting)
                if data:
                    logf.write(data.decode("utf-8", "ignore"))

            ser = send_cmd_and_collect(ser, logf, port, "device kind tag", 0.6)
            ser = send_cmd_and_collect(ser, logf, port, f"ota_target name {target_name}", 0.6)
            ser = send_cmd_and_collect(ser, logf, port, "mode recv", 2.5, resend_after_reopen=False)
            ser = drain_boot(ser, logf, port, 8.0)

            # Re-assert post-reboot target selection so the RECV session matches
            # the previously known-good manual sequence exactly.
            ser = send_cmd_and_collect(ser, logf, port, "device kind tag", 0.8)
            ser = send_cmd_and_collect(ser, logf, port, f"ota_target name {target_name}", 0.8)
            ser = send_cmd_and_collect(ser, logf, port, "conn", 1.0)

            ser, pre_mcal_seen = wait_for_patterns(
                ser,
                logf,
                port,
                20.0,
                [
                    f"Connected[0]:",
                    "DISC complete[0]",
                    f"{target_name} notify: CFG_OK",
                ],
            )
            if not all(pre_mcal_seen.values()):
                result["error"] = (
                    "pre_mcal_session_not_ready:"
                    + ",".join(
                        f"{k}={int(v)}" for k, v in pre_mcal_seen.items()
                    )
                )
                return result

            ser = send_cmd_and_collect(ser, logf, port, "oneshot MCAL", 0.5)

            # Do not hard-gate on MODE_OK. Some deployed Tag builds do not emit a
            # deterministic MODE_OK marker for MCAL, but CM lines are still valid.
            deadline = time.time() + max(wait_mode_ok_s, capture_s)
            mode_deadline = time.time() + wait_mode_ok_s
            capture_deadline = time.time() + capture_s
            while time.time() < deadline:
                try:
                    data = ser.read(4096)
                except (SerialException, OSError):
                    logf.write("--- SERIAL DISCONNECTED, REOPEN ---\n")
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser = reopen_port(port)
                    logf.write("--- SERIAL REOPENED ---\n")
                    continue
                if not data:
                    time.sleep(0.05)
                    continue

                text = data.decode("utf-8", "ignore")
                logf.write(text)
                if (not result["mode_ok_seen"] and time.time() < mode_deadline and
                        f"{target_name} notify: MODE_OK MODE=CAL LIVE=1" in text):
                    result["mode_ok_seen"] = True

                for line in text.splitlines():
                    if f"{target_name} notify: CM;" not in line:
                        continue
                    if time.time() > capture_deadline:
                        continue
                    result["cm_line_count"] += 1
                    if not result["first_cm_line"]:
                        result["first_cm_line"] = line.strip()
                    if result["cm_line_count"] >= min_cm_lines:
                        result["success"] = True
                        return result

            if result["cm_line_count"] < min_cm_lines:
                result["error"] = f"insufficient_cm_lines:{result['cm_line_count']}/{min_cm_lines}"
            return result
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Loop AUTOPOS sweep, then switch Tag target into MCAL and wait for BLE CM output."
    )
    parser.add_argument("--port", required=True, help="52840 CDC serial port")
    parser.add_argument("--order", default="ABCDEFGH", help="AUTOPOS master order, e.g. ABCDEFGH")
    parser.add_argument("--timeout-s", type=int, default=480, help="Per AUTOPOS round timeout")
    parser.add_argument("--sw-sets", type=int, default=10, help="Required SW lines per AUTOPOS round")
    parser.add_argument("--warmup-min-quality", type=int, default=90, help="Informational only; does not gate success")
    parser.add_argument("--target-name", default="BSF66F", help="Tag BLE target name")
    parser.add_argument(
        "--quiet-tag-name",
        default="",
        help="Before each sweep round, quarantine this powered-on Tag into MODE AOTA so it stays online but does not influence anchor sweep. Default: same as --target-name. Use '-' to disable.",
    )
    parser.add_argument("--cm-wait-s", type=float, default=120.0, help="Wait timeout for MODE_OK and CM after sweep")
    parser.add_argument("--cm-capture-s", type=float, default=120.0, help="Post-MCAL CM capture window")
    parser.add_argument("--min-cm-lines", type=int, default=1, help="Required aggregated CM lines")
    parser.add_argument("--loops", type=int, default=0, help="Loop count, 0 means infinite")
    parser.add_argument("--out-dir", required=True, help="Output directory")
    args = parser.parse_args()

    if args.sw_sets < 1:
        raise SystemExit("--sw-sets must be >= 1")
    if args.min_cm_lines < 1:
        raise SystemExit("--min-cm-lines must be >= 1")

    round_capture.target_sw_sets = args.sw_sets
    if args.quiet_tag_name == "":
        args.quiet_tag_name = args.target_name
    if args.quiet_tag_name == "-":
        args.quiet_tag_name = None

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "port": args.port,
        "order": list(args.order),
        "sw_sets": args.sw_sets,
        "target_name": args.target_name,
        "loops": args.loops,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "iterations": [],
    }

    iteration = 0
    while args.loops == 0 or iteration < args.loops:
        iteration += 1
        iteration_dir = out_dir / f"iter_{iteration:03d}"
        iteration_dir.mkdir(parents=True, exist_ok=True)

        iter_summary = {
            "iteration": iteration,
            "autopos": {"rounds": {}},
            "tag_cm": None,
            "success": False,
        }

        autopos_failed = False
        for master in args.order:
            round_dir = iteration_dir / f"round_{master}"
            round_dir.mkdir(parents=True, exist_ok=True)
            result = round_capture(
                args.port,
                master,
                round_dir,
                args.timeout_s,
                args.warmup_min_quality,
                args.quiet_tag_name,
                live_output=True,
                verbose=0,
            )
            iter_summary["autopos"]["rounds"][master] = result
            if not result["success"]:
                autopos_failed = True
                break

        if not autopos_failed:
            tag_dir = iteration_dir / "tag_cm"
            tag_dir.mkdir(parents=True, exist_ok=True)
            tag_result = run_tag_cm_capture(
                args.port,
                tag_dir,
                args.target_name,
                args.cm_wait_s,
                args.cm_capture_s,
                args.min_cm_lines,
            )
            iter_summary["tag_cm"] = tag_result
            iter_summary["success"] = tag_result["success"]

        summary["iterations"].append(iter_summary)
        summary["finished_iteration"] = iteration
        with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)

        print(json.dumps(iter_summary, indent=2), flush=True)

        if iter_summary["success"]:
            summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
            return 0

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
