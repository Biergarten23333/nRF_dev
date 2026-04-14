#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
import traceback
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


def sw_line_min_quality(master: str, line: str) -> int | None:
    try:
        fields = line.split(f"SW-{master},", 1)[1].split(",")
    except IndexError:
        return None

    qualities = []
    for idx in range(0, len(fields), 3):
        if idx + 2 >= len(fields):
            break
        try:
            qualities.append(int(fields[idx + 2]))
        except ValueError:
            continue
    if not qualities:
        return None
    return min(qualities)


def sw_line_pairs_below_quality(master: str, line: str, threshold: int) -> list[str]:
    if threshold <= 0:
        return []
    try:
        fields = line.split(f"SW-{master},", 1)[1].split(",")
    except IndexError:
        return []

    bad = []
    for idx in range(0, len(fields), 3):
        if idx + 2 >= len(fields):
            break
        peer = fields[idx]
        try:
            quality = int(fields[idx + 2])
        except ValueError:
            continue
        if quality < threshold:
            bad.append(peer)
    return bad


def should_print_live_line(line: str, verbose: int) -> bool:
    if verbose >= 2:
        return True

    if verbose == 1:
        return "ANCHOR candidate ignored:" not in line

    return (
        "SW-" in line or
        "AUTOPOS apply success:" in line or
        "AUTOPOS sweep listen attach:" in line or
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


def collect_for_text(
    ser: serial.Serial,
    logf,
    duration_s: float,
    port: str,
    live_output: bool,
    verbose: int,
) -> tuple[serial.Serial, bool, str]:
    end = time.time() + duration_s
    saw_reopen = False
    chunks = []
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
            chunks.append(text)
            emit(logf, text, live_output, verbose)
        else:
            time.sleep(0.05)
    return ser, saw_reopen, "".join(chunks)


def send_cmd_collect_text(
    ser: serial.Serial,
    logf,
    port: str,
    cmd: str,
    pause_s: float,
    live_output: bool,
    verbose: int,
    resend_after_reopen: bool = True,
) -> tuple[serial.Serial, str]:
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
    ser, saw_reopen, text = collect_for_text(ser, logf, pause_s, port, live_output, verbose)
    if saw_reopen and resend_after_reopen:
        emit(logf, f">>> RESEND {cmd}\n", live_output, verbose)
        write_cmd(ser, cmd)
        ser, _, more = collect_for_text(ser, logf, max(0.6, pause_s), port, live_output, verbose)
        text += more
    return ser, text


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
    ser, status_text = send_cmd_collect_text(
        ser,
        logf,
        port,
        "status",
        0.8,
        live_output,
        verbose,
        resend_after_reopen=False,
    )
    if "Control status: mode=OTA" in status_text:
        emit(logf, "PRECHECK: OTA mode detected; switching through RECV before AUTOPOS\n", live_output, verbose)
        ser = send_cmd_collect(
            ser,
            logf,
            port,
            "mode recv",
            3.0,
            live_output,
            verbose,
            resend_after_reopen=False,
        )
        ser, _ = collect_for(ser, logf, 2.0, port, live_output, verbose)
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
    elif "Control status: mode=AUTOPOS" in status_text:
        ser = send_cmd_collect(
            ser,
            logf,
            port,
            "autopos detach",
            9.5,
            live_output,
            verbose,
            resend_after_reopen=False,
        )
    else:
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


def wait_for_patterns(
    ser: serial.Serial,
    logf,
    port: str,
    patterns: list[str],
    timeout_s: float,
    live_output: bool,
    verbose: int,
) -> tuple[serial.Serial, bool, str]:
    deadline = time.time() + timeout_s
    chunks = []
    while time.time() < deadline:
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
            chunks.append(text)
            emit(logf, text, live_output, verbose)
            merged = "".join(chunks)
            if all(p in merged for p in patterns):
                return ser, True, merged
        else:
            time.sleep(0.05)
    return ser, False, "".join(chunks)


def quarantine_tag_for_sweep(
    ser: serial.Serial,
    logf,
    port: str,
    target_name: str,
    live_output: bool,
    verbose: int,
) -> tuple[serial.Serial, bool]:
    emit(logf, f"PRECHECK: quarantining tag target {target_name} for sweep\n", live_output, verbose)

    ser, status_text = send_cmd_collect_text(
        ser,
        logf,
        port,
        "status",
        0.8,
        live_output,
        verbose,
        resend_after_reopen=False,
    )
    if "Control status: mode=AUTOPOS" in status_text:
        ser = send_cmd_collect(
            ser,
            logf,
            port,
            "mode recv",
            3.0,
            live_output,
            verbose,
            resend_after_reopen=False,
        )
        ser, _ = collect_for(ser, logf, 1.5, port, live_output, verbose)
    elif "Control status: mode=OTA" in status_text:
        ser = send_cmd_collect(
            ser,
            logf,
            port,
            "mode recv",
            3.0,
            live_output,
            verbose,
            resend_after_reopen=False,
        )
        ser, _ = collect_for(ser, logf, 1.5, port, live_output, verbose)

    ser = send_cmd_collect(ser, logf, port, "device kind tag", 3.2, live_output, verbose)
    ser, target_text = send_cmd_collect_text(
        ser,
        logf,
        port,
        f"ota_target name {target_name}",
        1.0,
        live_output,
        verbose,
    )

    # Ack line can be dropped during serial backlog; verify with ota_target show as well.
    ack_ok = (f"ota_target name rc=0 value={target_name.lower()}" in target_text) or \
             (f"ota_target name rc=0 value={target_name}" in target_text)
    ser, show_text = send_cmd_collect_text(
        ser,
        logf,
        port,
        "ota_target show",
        0.8,
        live_output,
        verbose,
    )
    show_ok = (f"name={target_name.lower()}" in show_text) or (f"name={target_name}" in show_text)
    if not (ack_ok or show_ok):
        emit(logf, f"PRECHECK FAIL: ota_target name {target_name} not acknowledged\n", live_output, verbose)
        return ser, False

    # Ready signals can also be "already happened" and not re-emit (especially if Tag is
    # already connected and streaming CM). Also note: "DISC complete/CFG_OK" may arrive
    # during the *ota_target show* collection window (serial backlog), so include it.
    ready_text = target_text + show_text
    # Treat seeing CFG_OK/CM/BLE-ready as "ready" (DISC complete may not re-emit).
    ready_ok = (
        ("BLE[0] link ready" in ready_text) or
        (f"{target_name} notify: CFG_OK" in ready_text) or
        (("DISC complete[0]" in ready_text) and ("CFG_OK" in ready_text)) or
        (f"{target_name} notify: CM;" in ready_text)
    )
    if not ready_ok:
        ser, ready_ok, _ = wait_for_patterns(
            ser,
            logf,
            port,
            [f"{target_name} notify: CFG_OK"],
            12.0,
            live_output,
            verbose,
        )
    if not ready_ok:
        # If Tag is already streaming CM, accept it as ready.
        ser, cm_ok, _ = wait_for_patterns(
            ser,
            logf,
            port,
            [f"{target_name} notify: CM;"],
            2.5,
            live_output,
            verbose,
        )
        ready_ok = ready_ok or cm_ok
    if not ready_ok:
        ser = send_cmd_collect(ser, logf, port, "conn", 0.8, live_output, verbose)
        ser, ready_ok, _ = wait_for_patterns(
            ser,
            logf,
            port,
            [f"{target_name} notify: CFG_OK"],
            25.0,
            live_output,
            verbose,
        )
        if not ready_ok:
            ser, cm_ok, _ = wait_for_patterns(
                ser,
                logf,
                port,
                [f"{target_name} notify: CM;"],
                3.0,
                live_output,
                verbose,
            )
            ready_ok = ready_ok or cm_ok
        if not ready_ok:
            emit(logf, f"PRECHECK FAIL: tag {target_name} not connected/ready in RECV\n", live_output, verbose)
            return ser, False

    ser, mode_text = send_cmd_collect_text(
        ser,
        logf,
        port,
        "cmd MODE AOTA",
        1.0,
        live_output,
        verbose,
    )
    mode_ok = "MODE_OK MODE=AOTA" in mode_text
    if not mode_ok:
        ser, mode_ok, more_mode_text = wait_for_patterns(
            ser,
            logf,
            port,
            ["MODE_OK MODE=AOTA"],
            8.0,
            live_output,
            verbose,
        )
        mode_text += more_mode_text
    if not mode_ok:
        emit(logf, f"PRECHECK FAIL: tag {target_name} did not enter AOTA\n", live_output, verbose)
        if "MODE_BAD" in mode_text or "cmd rc=-128" in mode_text:
            emit(logf, "PRECHECK DETAIL: Tag command path rejected MODE AOTA\n", live_output, verbose)
        return ser, False

    ser, stream_text = send_cmd_collect_text(
        ser,
        logf,
        port,
        "cmd STREAM OFF",
        0.8,
        live_output,
        verbose,
    )
    stream_ok = "STREAM_OK OFF" in stream_text
    if not stream_ok:
        ser, stream_ok, more_stream_text = wait_for_patterns(
            ser,
            logf,
            port,
            ["STREAM_OK OFF"],
            6.0,
            live_output,
            verbose,
        )
        stream_text += more_stream_text
    if not stream_ok:
        if "UNKNOWN_CMD" in stream_text or "cmd rc=-128" in stream_text:
            emit(
                logf,
                f"PRECHECK WARN: tag {target_name} did not support STREAM OFF; continuing because MODE AOTA already quarantined UWB activity\n",
                live_output,
                verbose,
            )
        else:
            emit(logf, f"PRECHECK FAIL: tag {target_name} did not ack STREAM OFF\n", live_output, verbose)
            return ser, False

    emit(logf, f"PRECHECK PASS: tag {target_name} online but quarantined for sweep (MODE AOTA, STREAM OFF best-effort)\n", live_output, verbose)
    return ser, True


def round_capture(
    port: str,
    master: str,
    out_dir: Path,
    timeout_s: int,
    warmup_min_quality: int,
    quiet_tag_name: str | None,
    live_output: bool = True,
    verbose: int = 2,
) -> dict:
    log_path = out_dir / "master.log"
    result = {
        "master": master,
        "success": False,
        "sw_seen": False,
        "apply_success_seen": False,
        "sweep_ready_seen": False,
        "verified_count": 0,
        "sw_line": "",
        "sw_lines": [],
        "sw_count": 0,
        "warmup_min_quality": warmup_min_quality,
        "warmup_sw_lines": [],
        "warmup_sw_count": 0,
        "min_quality_seen": None,
        "pairs_below_quality": {},
        "log_path": str(log_path),
        "error": "",
        "warnings": [],
    }
    verified = set()
    ser = None
    try:
        ser = open_port(port, 20.0)
        with open(log_path, "w", buffering=1) as logf:
            emit(logf, f"PORT={port}\n", live_output, verbose)
            emit(logf, f"START={time.strftime('%Y-%m-%d %H:%M:%S')}\n", live_output, verbose)
            emit(logf, f"MASTER={master}\n", live_output, verbose)
            try:
                time.sleep(1.0)
                while ser.in_waiting:
                    data = ser.read(ser.in_waiting)
                    if data:
                        emit(logf, data.decode("utf-8", "ignore"), live_output, verbose)
            except Exception:
                # Without this, early serial instability produces a nearly-empty log.
                result["error"] = "serial_drain_failed"
                emit(logf, "PRECHECK FAIL: serial drain failed\n", live_output, verbose)
                emit(logf, traceback.format_exc() + "\n", live_output, verbose)
                emit(logf, f"END={time.strftime('%Y-%m-%d %H:%M:%S')}\n", live_output, verbose)
                flush_live_buffer(logf, verbose)
                return result

            if quiet_tag_name:
                ser, quiet_ok = quarantine_tag_for_sweep(
                    ser,
                    logf,
                    port,
                    quiet_tag_name,
                    live_output,
                    verbose,
                )
                if not quiet_ok:
                    # Don't hard-fail sweep collection on quiet-tag precheck.
                    # The sweep data is still valuable for diagnosis, and in many
                    # cases the Tag is already offline/unconnected so it cannot
                    # influence the anchor sweep anyway.
                    warn = f"tag_quiet_failed:{quiet_tag_name}"
                    result["warnings"].append(warn)
                    emit(logf, f"PRECHECK WARN: tag quarantine not reached ({warn}); continuing sweep\n", live_output, verbose)

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
                        if f"AUTOPOS sweep listen attach: master={master}" in line:
                            result["sweep_ready_seen"] = True
                        if (result["apply_success_seen"] and result["sweep_ready_seen"] and
                                f"SW-{master}," in line):
                            line = line.strip()
                            min_quality = sw_line_min_quality(master, line)
                            result["sw_seen"] = True
                            result["sw_line"] = line
                            result["sw_lines"].append(line)
                            result["sw_count"] = len(result["sw_lines"])
                            if min_quality is not None:
                                current_min = result["min_quality_seen"]
                                if current_min is None or min_quality < current_min:
                                    result["min_quality_seen"] = min_quality
                                if warmup_min_quality > 0 and min_quality < warmup_min_quality:
                                    result["warmup_sw_lines"].append(line)
                                    result["warmup_sw_count"] = len(result["warmup_sw_lines"])
                                    result["pairs_below_quality"][line] = (
                                        sw_line_pairs_below_quality(master, line, warmup_min_quality)
                                    )
                    if (result["apply_success_seen"] and result["sweep_ready_seen"] and
                            result["sw_count"] >= round_capture.target_sw_sets):
                        result["success"] = True
                        break
                else:
                    time.sleep(0.1)

            result["verified_count"] = len(verified)
            if not result["success"]:
                if not result["apply_success_seen"]:
                    result["error"] = "apply_success_not_seen"
                elif not result["sweep_ready_seen"]:
                    result["error"] = "sweep_ready_not_seen"
                elif not result["sw_seen"]:
                    result["error"] = "sw_not_seen"
                else:
                    result["error"] = f"insufficient_sw_sets:{result['sw_count']}/{round_capture.target_sw_sets}"
            emit(logf, f"END={time.strftime('%Y-%m-%d %H:%M:%S')}\n", live_output, verbose)
            flush_live_buffer(logf, verbose)
    except Exception:
        # Ensure the per-round log always captures the actual failure root cause.
        # This prevents "logs are empty" when something explodes early.
        result["error"] = result["error"] or "exception"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", buffering=1) as logf:
                emit(logf, "FATAL: exception in round_capture\n", live_output, verbose)
                emit(logf, traceback.format_exc() + "\n", live_output, verbose)
                emit(logf, f"END={time.strftime('%Y-%m-%d %H:%M:%S')}\n", live_output, verbose)
                flush_live_buffer(logf, verbose)
        except Exception:
            pass
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
    parser.add_argument(
        "--warmup-min-quality",
        type=int,
        default=90,
        help="Annotate post-ready SW lines whose minimum peer quality is below this value; does not block or discard sweep data. Use 0 to disable.",
    )
    parser.add_argument("--verbose", type=int, choices=[0, 1, 2], default=1,
                        help="Live stdout verbosity: 0=SW-X/failures only, 1=normal without ignored scan noise, 2=full flow")
    parser.add_argument(
        "--quiet-tag-name",
        default="BSF66F",
        help="Before each sweep round, connect to this Tag in RECV and force MODE AOTA + STREAM OFF so it stays online but does not influence anchor sweep. Use - to disable.",
    )
    parser.add_argument("--out-dir", required=True, help="Output directory")
    parser.add_argument(
        "--no-live-output",
        action="store_true",
        help="Do not mirror runtime logs to stdout; write to log files only.",
    )
    args = parser.parse_args()

    if args.sw_sets < 1:
        raise SystemExit("--sw-sets must be >= 1")
    if args.warmup_min_quality < 0 or args.warmup_min_quality > 100:
        raise SystemExit("--warmup-min-quality must be between 0 and 100")

    if args.timeout_s is None:
        args.timeout_s = auto_timeout_for_sw_sets(args.sw_sets)

    round_capture.target_sw_sets = args.sw_sets

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "port": args.port,
        "order": list(args.order),
        "sw_sets": args.sw_sets,
        "warmup_min_quality": args.warmup_min_quality,
        "timeout_s": args.timeout_s,
        "verbose": args.verbose,
        "quiet_tag_name": None if args.quiet_tag_name == "-" else args.quiet_tag_name,
        "rounds": {},
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    for master in args.order:
        round_dir = out_dir / f"round_{master}"
        round_dir.mkdir(parents=True, exist_ok=True)
        print(f"=== AUTOPOS SWEEP {master} ===", flush=True)
        try:
            result = round_capture(
                args.port,
                master,
                round_dir,
                args.timeout_s,
                args.warmup_min_quality,
                None if args.quiet_tag_name == "-" else args.quiet_tag_name,
                live_output=not args.no_live_output,
                verbose=args.verbose,
            )
        except Exception:
            # Defensive: round_capture itself is hardened, but still ensure the outer loop
            # writes a summary.json instead of crashing and leaving "empty" outputs.
            result = {
                "master": master,
                "success": False,
                "sw_seen": False,
                "apply_success_seen": False,
                "sweep_ready_seen": False,
                "verified_count": 0,
                "sw_line": "",
                "sw_lines": [],
                "sw_count": 0,
                "warmup_min_quality": args.warmup_min_quality,
                "warmup_sw_lines": [],
                "warmup_sw_count": 0,
                "min_quality_seen": None,
                "pairs_below_quality": {},
                "log_path": str((round_dir / "master.log").resolve()),
                "error": "exception_in_round_capture",
            }
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
