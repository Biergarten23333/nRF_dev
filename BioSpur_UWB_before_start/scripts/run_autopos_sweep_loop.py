#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

import serial
from serial import SerialException, SerialTimeoutException

from run_autopos_round import UUIDS

_LIVE_LINE_BUFFERS: dict[int, str] = {}
_PROGRESS_LINE_LEN = 24
_PROGRESS_ACTIVE = False
_LAST_PROGRESS_LINE = ""
_LAST_PROGRESS_PRINTED_LEN = 0


def auto_timeout_for_sw_sets(sw_sets: int) -> int:
    # Empirical default:
    # - 10 sets stays around the historical 480s budget
    # - 100 sets expands to about 30 minutes
    return max(480, 360 + (15 * sw_sets))


def suggested_retry_timeout_s(result: dict, requested_timeout_s: int) -> int:
    """
    If a round timed out after making some progress, extend the next attempt
    based on observed throughput instead of reusing the same too-small window.
    """
    sw_count = int(result.get("sw_count") or 0)
    collect_elapsed_s = result.get("collect_elapsed_s")
    target_sw_sets = int(round_capture.target_sw_sets or 0)
    if sw_count <= 0 or target_sw_sets <= 0 or not isinstance(collect_elapsed_s, (int, float)):
        return max(requested_timeout_s, auto_timeout_for_sw_sets(target_sw_sets or 1))

    observed_ratio = sw_count / float(target_sw_sets)
    if observed_ratio <= 0.0:
        return max(requested_timeout_s, auto_timeout_for_sw_sets(target_sw_sets))

    estimated_total_s = collect_elapsed_s / observed_ratio
    # Give the next attempt a little slack over the linear estimate.
    return max(
        requested_timeout_s,
        int(estimated_total_s * 1.15),
        auto_timeout_for_sw_sets(target_sw_sets),
    )


def format_eta_seconds(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--"
    total = int(round(seconds))
    mins, secs = divmod(total, 60)
    if mins >= 60:
        hours, mins = divmod(mins, 60)
        return f"{hours}h{mins:02d}m{secs:02d}s"
    return f"{mins}m{secs:02d}s"


def render_round_progress(
    master: str,
    round_idx: int,
    round_total: int,
    sw_count: int,
    sw_target: int,
    stage: str,
    total_elapsed_s: float,
    round_elapsed_s: float,
    eta_s: float | None,
    warmup_count: int = 0,
    warmup_target: int = 0,
) -> str:
    if stage == "warmup" and warmup_target > 0:
        percent = max(0, min(99, int((warmup_count / warmup_target) * 100)))
    elif stage == "sweeping" and sw_target > 0:
        percent = max(0, min(100, int((sw_count / sw_target) * 100)))
    elif stage == "done":
        percent = 100
    elif stage == "failed":
        percent = max(0, min(99, int((sw_count / max(sw_target, 1)) * 100)))
    else:
        percent = 0
    filled = max(0, min(_PROGRESS_LINE_LEN, int(round((percent / 100.0) * _PROGRESS_LINE_LEN))))
    bar = "#" * filled + "." * (_PROGRESS_LINE_LEN - filled)
    warmup_part = ""
    if warmup_target > 0:
        warmup_part = f" warmup={warmup_count}/{warmup_target}"
    return (
        f"[SW-{master} {round_idx}/{round_total}] [{bar}] {percent:3d}% "
        f"sw-set={sw_count}/{sw_target} stage={stage:<10} "
        f"{warmup_part}"
        f"elapsed={int(total_elapsed_s):4d}s round={int(round_elapsed_s):4d}s "
        f"eta[SW-{master}]={format_eta_seconds(eta_s):>8}"
    )


def render_session_progress(phase: str, status: str, elapsed_s: float) -> str:
    return (
        f"[SESSION {phase:<9}] "
        f"status={status:<64} "
        f"elapsed={int(elapsed_s):4d}s"
    )


def _terminal_width() -> int:
    try:
        return max(20, os.get_terminal_size(sys.stdout.fileno()).columns)
    except OSError:
        return 120


def _progress_for_terminal(line: str) -> str:
    width = _terminal_width()
    if len(line) >= width:
        return line[: max(1, width - 1)]
    return line


def _clear_progress_line() -> None:
    global _LAST_PROGRESS_PRINTED_LEN
    if _LAST_PROGRESS_PRINTED_LEN <= 0:
        sys.stdout.write("\r")
        return
    sys.stdout.write("\r" + (" " * _LAST_PROGRESS_PRINTED_LEN) + "\r")


def write_live_output(text: str) -> None:
    """Print normal logs above the live progress line."""
    global _PROGRESS_ACTIVE
    if not text:
        return
    had_progress = _PROGRESS_ACTIVE and _LAST_PROGRESS_LINE
    if had_progress:
        _clear_progress_line()
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    if had_progress:
        _write_progress_line(_LAST_PROGRESS_LINE)
    sys.stdout.flush()


def _write_progress_line(line: str) -> None:
    global _PROGRESS_ACTIVE, _LAST_PROGRESS_LINE, _LAST_PROGRESS_PRINTED_LEN
    line = _progress_for_terminal(line.lstrip("\r"))
    clear_len = max(_LAST_PROGRESS_PRINTED_LEN - len(line), 0)
    sys.stdout.write("\r" + line + (" " * clear_len))
    sys.stdout.flush()
    _PROGRESS_ACTIVE = True
    _LAST_PROGRESS_LINE = line
    _LAST_PROGRESS_PRINTED_LEN = len(line)


def print_round_progress(
    master: str,
    round_idx: int,
    round_total: int,
    sw_count: int,
    sw_target: int,
    stage: str,
    total_elapsed_s: float,
    round_elapsed_s: float,
    eta_s: float | None,
    warmup_count: int = 0,
    warmup_target: int = 0,
) -> None:
    _write_progress_line(
        render_round_progress(
            master,
            round_idx,
            round_total,
            sw_count,
            sw_target,
            stage,
            total_elapsed_s,
            round_elapsed_s,
            eta_s,
            warmup_count,
            warmup_target,
        )
    )


def print_session_progress(phase: str, status: str, elapsed_s: float) -> None:
    _write_progress_line(render_session_progress(phase, status, elapsed_s))


def finish_progress_line(final_line: str | None = None) -> None:
    global _PROGRESS_ACTIVE, _LAST_PROGRESS_LINE, _LAST_PROGRESS_PRINTED_LEN
    line = final_line if final_line is not None else _LAST_PROGRESS_LINE
    if line:
        _clear_progress_line()
        sys.stdout.write(_progress_for_terminal(line.lstrip("\r")) + "\n")
    else:
        sys.stdout.write("\n")
    sys.stdout.flush()
    _PROGRESS_ACTIVE = False
    _LAST_PROGRESS_LINE = ""
    _LAST_PROGRESS_PRINTED_LEN = 0


def finish_round_progress() -> None:
    finish_progress_line()


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


def parse_sw_line_triplets(master: str, line: str) -> dict[str, tuple[int, int]]:
    try:
        fields = line.split(f"SW-{master},", 1)[1].split(",")
    except IndexError:
        return {}

    parsed: dict[str, tuple[int, int]] = {}
    for idx in range(0, len(fields), 3):
        if idx + 2 >= len(fields):
            break
        peer = fields[idx].strip()
        if not peer:
            continue
        try:
            distance = int(fields[idx + 1])
            quality = int(fields[idx + 2])
        except ValueError:
            continue
        parsed[peer] = (distance, quality)
    return parsed


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


def summarize_round_warnings(master: str, sw_lines: list[str], sw_seen: bool) -> list[str]:
    warnings: list[str] = []
    if not sw_seen or not sw_lines:
        warnings.append(f"Anchor {master}: no output as Master")
        return warnings

    peer_zero_only_rounds: dict[str, bool] = {}
    seen_any_peer: set[str] = set()
    for line in sw_lines:
        parsed = parse_sw_line_triplets(master, line)
        for peer, (distance, quality) in parsed.items():
            seen_any_peer.add(peer)
            zero = (distance == 0 and quality == 0)
            if peer not in peer_zero_only_rounds:
                peer_zero_only_rounds[peer] = zero
            else:
                peer_zero_only_rounds[peer] = peer_zero_only_rounds[peer] and zero

    for peer in sorted(seen_any_peer):
        if peer_zero_only_rounds.get(peer, False):
            warnings.append(f"Anchor {peer}: no output as Matrix during SW-{master}")
    return warnings


def summarize_global_warnings(rounds: dict) -> list[str]:
    warnings: list[str] = []
    no_master = []
    matrix_missing: dict[str, list[str]] = {}
    matrix_low_quality: dict[str, list[tuple[str, int]]] = {}

    for master, result in sorted(rounds.items()):
        if not result.get("sw_seen") or not result.get("sw_lines"):
            no_master.append(master)
        for warning in result.get("warnings", []):
            m = re.fullmatch(r"Anchor ([A-Z]): no output as Matrix during SW-([A-Z])", warning)
            if m:
                peer, sw_master = m.group(1), m.group(2)
                matrix_missing.setdefault(peer, []).append(sw_master)
        for line in result.get("sw_lines", []):
            parsed = parse_sw_line_triplets(master, line)
            for peer, (_, quality) in parsed.items():
                if quality <= 85:
                    matrix_low_quality.setdefault(peer, []).append((master, quality))

    for master in no_master:
        warnings.append(f"WARNING: Anchor {master}: no output as Master")
    for peer in sorted(matrix_missing):
        rounds_str = ",".join(matrix_missing[peer])
        warnings.append(f"WARNING: Anchor {peer}: no output as Matrix in rounds {rounds_str}")
        warnings.append(
            f"WARNING: Check Anchor {peer} status; repeated matrix silence usually means responder path, role transition, or UWB RX issue."
        )
    for peer in sorted(matrix_low_quality):
        by_round: dict[str, int] = {}
        for master, quality in matrix_low_quality[peer]:
            current = by_round.get(master)
            if current is None or quality < current:
                by_round[master] = quality
        low_rounds = sorted(by_round.items())
        if not low_rounds:
            continue
        rounds_str = ",".join(master for master, _ in low_rounds)
        minq = min(q for _, q in low_rounds)
        warnings.append(
            f"WARNING: Anchor {peer}: low quality as Matrix in rounds {rounds_str} (minq<={minq})"
        )
        warnings.append(
            f"WARNING: Check Anchor {peer} status; low matrix quality usually points to RF path, antenna delay mismatch, weak supply, or placement/orientation issue."
        )
    return warnings


def format_duration_brief(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    total = max(0, int(round(seconds)))
    mins, secs = divmod(total, 60)
    if mins >= 60:
        hours, mins = divmod(mins, 60)
        return f"{hours}h{mins:02d}m{secs:02d}s"
    return f"{mins}m{secs:02d}s"


def build_run_summary_lines(summary: dict) -> list[str]:
    lines = ["=== SUMMARY ==="]
    lines.append(f"Total elapsed: {format_duration_brief(summary.get('total_elapsed_s'))}")
    guard_result = summary.get("session_role_guard_result")
    if isinstance(guard_result, dict):
        lines.append(
            "Session guard: "
            + ("matrix ok" if guard_result.get("success") else f"matrix failed ({guard_result.get('error', '-')})")
        )
    final_result = summary.get("session_final_responder_result")
    if isinstance(final_result, dict):
        if final_result.get("success"):
            sent = final_result.get("sent_count")
            ready = final_result.get("ready_count")
            target = final_result.get("ready_target")
            if sent is not None and ready is not None and target is not None:
                lines.append(
                    f"Session finalizer: responder ok sent={sent} ready={ready}/{target}"
                )
            else:
                lines.append("Session finalizer: responder ok")
        else:
            lines.append(
                f"Session finalizer: responder failed ({final_result.get('error', '-')})"
            )
    lines.append(
        "Per-round sets: "
        f"requested={summary.get('sw_sets', '--')} "
        f"prewarm={summary.get('prewarm_sw_sets', 0)} "
        f"device={summary.get('device_sw_sets', '--')}"
    )
    rounds = summary.get("rounds", {})
    for master in summary.get("order", []):
        result = rounds.get(master)
        if not isinstance(result, dict):
            continue
        lines.append(
            f"SW-{master}: total={format_duration_brief(result.get('total_elapsed_s'))} "
            f"precheck={format_duration_brief(result.get('precheck_elapsed_s'))} "
            f"switch={format_duration_brief(result.get('switch_elapsed_s'))} "
            f"warmup={format_duration_brief(result.get('warmup_elapsed_s'))} "
            f"collect={format_duration_brief(result.get('collect_elapsed_s'))} "
            f"sw={result.get('sw_count', 0)}/{summary.get('sw_sets', '--')} "
            f"raw={result.get('device_sw_count', 0)} "
            f"discarded={result.get('warmup_discarded_count', 0)} "
            f"reconnect_retry={'yes' if result.get('reconnect_retry_seen') else 'no'}"
        )
    slow_switch_threshold_s = summary.get("slow_switch_threshold_s", 10.0)
    slow_rounds = []
    retry_rounds = []
    for master in summary.get("order", []):
        result = rounds.get(master)
        if not isinstance(result, dict):
            continue
        switch_s = result.get("switch_elapsed_s")
        if isinstance(switch_s, (int, float)) and switch_s >= slow_switch_threshold_s:
            slow_rounds.append((master, switch_s))
        if result.get("reconnect_retry_seen"):
            retry_rounds.append(master)
    lines.append("Slow switch rounds:")
    if slow_rounds:
        for master, switch_s in slow_rounds:
            lines.append(f"SW-{master} switch unusually slow: {format_duration_brief(switch_s)}")
    else:
        lines.append("none")
    lines.append("Reconnect retry rounds:")
    if retry_rounds:
        lines.append(",".join(f"SW-{master}" for master in retry_rounds))
    else:
        lines.append("none")
    return lines


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
        write_live_output(tail)


def emit(logf, text: str, live_output: bool, verbose: int = 2) -> None:
    logf.write(text)
    if live_output:
        # If the caller uses stdout as the log sink (e.g. quarantine_tags.py),
        # don't double-print via the live-output path.
        if logf in (sys.stdout, sys.stderr):
            sys.stdout.flush()
            return
        key = id(logf)
        buffer = _LIVE_LINE_BUFFERS.get(key, "") + text
        lines = buffer.splitlines(keepends=True)
        tail = ""
        if lines and not lines[-1].endswith("\n"):
            tail = lines.pop()
        _LIVE_LINE_BUFFERS[key] = tail
        for line in lines:
            if should_print_live_line(line, verbose):
                write_live_output(line)


def open_port(port: str, timeout_s: float) -> serial.Serial:
    deadline = time.time() + timeout_s
    last_exc = None
    while time.time() < deadline:
        try:
            ser = serial.Serial(port, 115200, timeout=0.2, write_timeout=5.0)
            time.sleep(0.8)
            _best_effort_reset_serial_buffers(ser)
            time.sleep(0.2)
            return ser
        except Exception as exc:
            last_exc = exc
            time.sleep(0.4)
    raise last_exc


def _best_effort_reset_serial_buffers(ser: serial.Serial) -> None:
    try:
        ser.reset_input_buffer()
    except Exception:
        pass
    try:
        ser.reset_output_buffer()
    except Exception:
        pass


def _write_bytes_with_recovery(ser: serial.Serial, payload: bytes) -> serial.Serial:
    try:
        ser.write(payload)
        ser.flush()
        return ser
    except SerialTimeoutException:
        _best_effort_reset_serial_buffers(ser)
        time.sleep(0.2)
        try:
            ser.write(payload)
            ser.flush()
            return ser
        except SerialTimeoutException:
            port = getattr(ser, "port", None)
            try:
                ser.close()
            except Exception:
                pass
            if not port:
                raise
            ser = reopen_port(port)
            _best_effort_reset_serial_buffers(ser)
            time.sleep(0.8)
            ser.write(payload)
            ser.flush()
            return ser


def write_cmd(ser: serial.Serial, cmd: str) -> serial.Serial:
    return _write_bytes_with_recovery(ser, (cmd + "\n").encode())


def write_cmds(ser: serial.Serial, cmds: list[str]) -> serial.Serial:
    if not cmds:
        return ser
    return _write_bytes_with_recovery(ser, ("".join(c + "\n" for c in cmds)).encode())


def reopen_port(port: str) -> serial.Serial:
    return open_port(port, 20.0)


def collect_for(
    ser: serial.Serial,
    logf,
    duration_s: float,
    port: str,
    live_output: bool,
    verbose: int,
    progress_cb=None,
    text_filter=None,
) -> tuple[serial.Serial, bool]:
    end = time.time() + duration_s
    saw_reopen = False
    while time.time() < end:
        if progress_cb is not None:
            progress_cb()
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
            emit(logf, text_filter(text) if text_filter is not None else text, live_output, verbose)
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
    progress_cb=None,
    text_filter=None,
) -> tuple[serial.Serial, bool, str]:
    end = time.time() + duration_s
    saw_reopen = False
    chunks = []
    while time.time() < end:
        if progress_cb is not None:
            progress_cb()
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
            emit(logf, text_filter(text) if text_filter is not None else text, live_output, verbose)
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
    progress_cb=None,
    text_filter=None,
) -> tuple[serial.Serial, str]:
    if progress_cb is not None:
        progress_cb()
    emit(logf, f">>> {cmd}\n", live_output, verbose)
    try:
        ser = write_cmd(ser, cmd)
    except Exception:
        try:
            ser.close()
        except Exception:
            pass
        ser = reopen_port(port)
        ser = write_cmd(ser, cmd)
    ser, saw_reopen, text = collect_for_text(
        ser,
        logf,
        pause_s,
        port,
        live_output,
        verbose,
        progress_cb=progress_cb,
        text_filter=text_filter,
    )
    if saw_reopen and resend_after_reopen:
        if progress_cb is not None:
            progress_cb()
        emit(logf, f">>> RESEND {cmd}\n", live_output, verbose)
        ser = write_cmd(ser, cmd)
        ser, _, more = collect_for_text(
            ser,
            logf,
            max(0.6, pause_s),
            port,
            live_output,
            verbose,
            progress_cb=progress_cb,
            text_filter=text_filter,
        )
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
    progress_cb=None,
    text_filter=None,
) -> serial.Serial:
    if progress_cb is not None:
        progress_cb()
    emit(logf, f">>> {cmd}\n", live_output, verbose)
    try:
        ser = write_cmd(ser, cmd)
    except Exception:
        try:
            ser.close()
        except Exception:
            pass
        ser = reopen_port(port)
        ser = write_cmd(ser, cmd)
    ser, saw_reopen = collect_for(
        ser,
        logf,
        pause_s,
        port,
        live_output,
        verbose,
        progress_cb=progress_cb,
        text_filter=text_filter,
    )
    if saw_reopen and resend_after_reopen:
        if progress_cb is not None:
            progress_cb()
        emit(logf, f">>> RESEND {cmd}\n", live_output, verbose)
        ser = write_cmd(ser, cmd)
        ser, _ = collect_for(
            ser,
            logf,
            max(0.6, pause_s),
            port,
            live_output,
            verbose,
            progress_cb=progress_cb,
            text_filter=text_filter,
        )
    return ser


def wait_for_autopos_idle(
    ser: serial.Serial,
    logf,
    port: str,
    timeout_s: float,
    live_output: bool,
    verbose: int,
    progress_cb=None,
) -> tuple[serial.Serial, bool]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if progress_cb is not None:
            progress_cb()
        emit(logf, ">>> autopos status\n", live_output, verbose)
        try:
            ser = write_cmd(ser, "autopos status")
        except Exception:
            try:
                ser.close()
            except Exception:
                pass
            ser = reopen_port(port)
            ser = write_cmd(ser, "autopos status")

        pause_end = time.time() + 1.4
        while time.time() < pause_end:
            if progress_cb is not None:
                progress_cb()
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
    progress_cb=None,
    force_clean: bool = False,
) -> tuple[serial.Serial, bool]:
    if force_clean:
        emit(logf, "PRECHECK: forcing RECV clean-slate before AUTOPOS\n", live_output, verbose)
        ser = send_cmd_collect(
            ser,
            logf,
            port,
            "mode recv",
            3.2,
            live_output,
            verbose,
            resend_after_reopen=True,
            progress_cb=progress_cb,
        )
        ser, _ = collect_for(ser, logf, 2.0, port, live_output, verbose, progress_cb=progress_cb)
        ser = send_cmd_collect(
            ser,
            logf,
            port,
            "mode autopos",
            1.8,
            live_output,
            verbose,
            resend_after_reopen=True,
            progress_cb=progress_cb,
        )
        ser, _ = collect_for(ser, logf, 1.0, port, live_output, verbose, progress_cb=progress_cb)
        ser, idle_ok = wait_for_autopos_idle(
            ser,
            logf,
            port,
            10.0,
            live_output,
            verbose,
            progress_cb=progress_cb,
        )
        if not idle_ok:
            emit(
                logf,
                "PRECHECK WARN: AUTOPOS idle not reached; continuing with best-effort sweep start\n",
                live_output,
                verbose,
            )
        return ser, True

    if progress_cb is not None:
        progress_cb()
    ser, status_text = send_cmd_collect_text(
        ser,
        logf,
        port,
        "status",
        0.8,
        live_output,
        verbose,
        resend_after_reopen=False,
        progress_cb=progress_cb,
    )
    if (
        "Control status: mode=AUTOPOS" not in status_text and
        "Control status: mode=RECV" not in status_text
    ):
        emit(
            logf,
            "PRECHECK WARN: status mode not observed; retrying status once before forcing clean-slate\n",
            live_output,
            verbose,
        )
        ser, retry_status_text = send_cmd_collect_text(
            ser,
            logf,
            port,
            "status",
            1.0,
            live_output,
            verbose,
            resend_after_reopen=False,
            progress_cb=progress_cb,
        )
        status_text += retry_status_text
    if "Control status: mode=AUTOPOS" in status_text:
        ser, autopos_text = send_cmd_collect_text(
            ser,
            logf,
            port,
            "autopos status",
            1.0,
            live_output,
            verbose,
            resend_after_reopen=False,
            progress_cb=progress_cb,
        )
        if "AUTOPOS: mode=AUTOPOS state=failed" not in autopos_text:
            emit(
                logf,
                "PRECHECK: reusing existing AUTOPOS session\n",
                live_output,
                verbose,
            )
            return ser, True
        emit(
            logf,
            "PRECHECK WARN: AUTOPOS state=failed; forcing RECV clean-slate before re-entering AUTOPOS\n",
            live_output,
            verbose,
        )

    if "Control status: mode=RECV" not in status_text:
        if progress_cb is not None:
            progress_cb()
        emit(logf, "PRECHECK: forcing RECV clean-slate before AUTOPOS\n", live_output, verbose)
        ser = send_cmd_collect(
            ser,
            logf,
            port,
            "mode recv",
            3.2,
            live_output,
            verbose,
            resend_after_reopen=True,
            progress_cb=progress_cb,
        )
        ser, _ = collect_for(ser, logf, 2.0, port, live_output, verbose, progress_cb=progress_cb)
    if progress_cb is not None:
        progress_cb()
    ser = send_cmd_collect(
        ser,
        logf,
        port,
        "mode autopos",
        1.8,
        live_output,
        verbose,
        resend_after_reopen=True,
        progress_cb=progress_cb,
    )
    ser, _ = collect_for(ser, logf, 1.0, port, live_output, verbose, progress_cb=progress_cb)
    ser, idle_ok = wait_for_autopos_idle(
        ser,
        logf,
        port,
        10.0,
        live_output,
        verbose,
        progress_cb=progress_cb,
    )
    if not idle_ok:
        if progress_cb is not None:
            progress_cb()
        emit(
            logf,
            "PRECHECK WARN: AUTOPOS detach did not settle cleanly; forcing RECV clean-slate before re-entering AUTOPOS\n",
            live_output,
            verbose,
        )
        ser = send_cmd_collect(
            ser,
            logf,
            port,
            "mode recv",
            3.0,
            live_output,
            verbose,
            resend_after_reopen=False,
            progress_cb=progress_cb,
        )
        ser, _ = collect_for(ser, logf, 2.0, port, live_output, verbose, progress_cb=progress_cb)
        if progress_cb is not None:
            progress_cb()
        ser = send_cmd_collect(
            ser,
            logf,
            port,
            "mode autopos",
            1.6,
            live_output,
            verbose,
            resend_after_reopen=False,
            progress_cb=progress_cb,
        )
        ser, _ = collect_for(ser, logf, 0.8, port, live_output, verbose, progress_cb=progress_cb)
        ser, idle_ok = wait_for_autopos_idle(
            ser,
            logf,
            port,
            5.0,
            live_output,
            verbose,
            progress_cb=progress_cb,
        )
    if not idle_ok:
        emit(
            logf,
            "PRECHECK WARN: AUTOPOS idle not reached; continuing with best-effort sweep start\n",
            live_output,
            verbose,
        )
    return ser, True


def bootstrap_reset_all_autopos(
    ser: serial.Serial,
    logf,
    port: str,
    live_output: bool,
    verbose: int,
    progress_cb=None,
) -> tuple[serial.Serial, bool]:
    emit(logf, "PRECHECK: bootstrap anchor reset all autopos\n", live_output, verbose)
    ser, reset_text = send_cmd_collect_text(
        ser,
        logf,
        port,
        "anchor reset all autopos",
        130.0,
        live_output,
        verbose,
        resend_after_reopen=False,
        progress_cb=progress_cb,
    )
    if "uuid not mapped" in reset_text:
        emit(logf, "PRECHECK WARN: bootstrap reset saw uuid-not-mapped; check map ordering\n",
             live_output, verbose)
    if "anchor reset rc=" not in reset_text:
        emit(logf, "PRECHECK WARN: bootstrap reset command did not report completion\n",
             live_output, verbose)

    deadline = time.time() + 90.0
    ok = False
    while time.time() < deadline:
        if progress_cb is not None:
            progress_cb()
        ser, text = send_cmd_collect_text(
            ser,
            logf,
            port,
            "anchor version all",
            2.0,
            live_output,
            verbose,
            resend_after_reopen=False,
            progress_cb=progress_cb,
        )
        matrix_count = len(re.findall(r"ANCHOR_VERSION .* role=matrix", text))
        if matrix_count >= len(UUIDS):
            ok = True
            break
        emit(
            logf,
            f"PRECHECK: bootstrap wait matrix_count={matrix_count}/{len(UUIDS)}\n",
            live_output,
            verbose,
        )
        time.sleep(1.0)

    if ok:
        emit(logf, "PRECHECK: bootstrap reset all autopos complete\n", live_output, verbose)
    else:
        emit(logf, "PRECHECK WARN: bootstrap reset all autopos did not verify all matrix anchors\n",
             live_output, verbose)
    return ser, ok


def ensure_autopos_maps(
    ser: serial.Serial,
    logf,
    port: str,
    live_output: bool,
    verbose: int,
    context: dict | None = None,
    progress_cb=None,
    status_cb=None,
) -> serial.Serial:
    if context is not None and context.get("autopos_initialized", False):
        if status_cb is not None:
            status_cb("reuse AUTOPOS map")
        emit(logf, "PRECHECK: reusing existing AUTOPOS map\n", live_output, verbose)
        return ser
    for label, uuid in UUIDS.items():
        expected = f"AUTOPOS map set: {label}={uuid}"
        confirmed = False
        for attempt in range(1, 4):
            if status_cb is not None:
                if attempt == 1:
                    status_cb(f"map {label}")
                else:
                    status_cb(f"map {label} retry {attempt}")
            ser, text = send_cmd_collect_text(
                ser,
                logf,
                port,
                f"autopos map {label} {uuid}",
                0.8,
                live_output,
                verbose,
                progress_cb=progress_cb,
            )
            if expected in text:
                confirmed = True
                break
            emit(
                logf,
                f"PRECHECK WARN: autopos map {label} not confirmed on attempt {attempt}; retrying\n",
                live_output,
                verbose,
            )
            ser, _ = collect_for(ser, logf, 0.2, port, live_output, verbose, progress_cb=progress_cb)
        if not confirmed:
            emit(
                logf,
                f"PRECHECK WARN: autopos map confirm failed for {label}; continuing best-effort\n",
                live_output,
                verbose,
            )

    if context is not None:
        context["autopos_initialized"] = True
    return ser


def anchor_role_counts(text: str) -> dict[str, int]:
    counts = {"matrix": 0, "responder": 0, "master": 0, "other": 0}
    for role in re.findall(r"ANCHOR_VERSION .* role=([a-zA-Z_-]+)", text):
        role = role.lower()
        if role in counts:
            counts[role] += 1
        else:
            counts["other"] += 1
    return counts


def scan_anchor_role_counts(timeout_s: float = 6.0) -> dict[str, int]:
    counts = {"matrix": 0, "responder": 0, "master": 0, "other": 0}
    script = Path(__file__).resolve().with_name("scan_and_map.py")
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--timeout-s", str(timeout_s), "--json"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return counts
    if proc.returncode != 0:
        return counts
    try:
        records = json.loads(proc.stdout or "[]")
    except Exception:
        return counts
    seen = set()
    uuid_to_label = {uuid: label for label, uuid in UUIDS.items()}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        uuid = str(rec.get("device_uuid_hex", "")).upper()
        label = uuid_to_label.get(uuid)
        if not label or label in seen:
            continue
        seen.add(label)
        role = str(rec.get("role", "other")).lower()
        if role in counts:
            counts[role] += 1
        else:
            counts["other"] += 1
    return counts


def wait_all_anchor_role(
    ser: serial.Serial,
    logf,
    port: str,
    role: str,
    timeout_s: float,
    live_output: bool,
    verbose: int,
    context: dict | None = None,
) -> tuple[serial.Serial, bool, dict[str, int]]:
    deadline = time.time() + timeout_s
    last_counts = {"matrix": 0, "responder": 0, "master": 0, "other": 0}
    while time.time() < deadline:
        last_counts = scan_anchor_role_counts(timeout_s=4.0)
        if last_counts.get(role, 0) >= len(UUIDS):
            emit(
                logf,
                (
                    f"SESSION: role verify via BLE scan succeeded "
                    f"for role={role} "
                    f"matrix={last_counts.get('matrix', 0)} "
                    f"responder={last_counts.get('responder', 0)} "
                    f"master={last_counts.get('master', 0)} "
                    f"other={last_counts.get('other', 0)}\n"
                ),
                live_output,
                verbose,
            )
            return ser, True, last_counts
        emit(
            logf,
            (
                f"SESSION: wait all {role} via BLE scan role_counts="
                f"matrix={last_counts.get('matrix', 0)} "
                f"responder={last_counts.get('responder', 0)} "
                f"master={last_counts.get('master', 0)} "
                f"other={last_counts.get('other', 0)}\n"
            ),
            live_output,
            verbose,
        )
        time.sleep(1.0)
    emit(
        logf,
        (
            f"SESSION: role verify via BLE scan failed "
            f"for role={role} "
            f"matrix={last_counts.get('matrix', 0)} "
            f"responder={last_counts.get('responder', 0)} "
            f"master={last_counts.get('master', 0)} "
            f"other={last_counts.get('other', 0)}\n"
        ),
        live_output,
        verbose,
    )
    return ser, False, last_counts


def wait_scan_role_counts(
    role: str,
    timeout_s: float,
    poll_s: float = 2.0,
) -> tuple[bool, dict[str, int]]:
    deadline = time.time() + timeout_s
    last_counts = {"matrix": 0, "responder": 0, "master": 0, "other": 0}
    while time.time() < deadline:
        last_counts = scan_anchor_role_counts(timeout_s=min(6.0, max(2.0, poll_s)))
        if last_counts.get(role, 0) >= len(UUIDS):
            return True, last_counts
        time.sleep(poll_s)
    return False, last_counts


def session_prepare_matrix(
    port: str,
    out_dir: Path,
    live_output: bool,
    verbose: int,
    context: dict,
) -> tuple[bool, dict]:
    log_path = out_dir / "session_role_guard.log"
    result = {
        "success": False,
        "log_path": str(log_path),
        "initial_role_counts": {},
        "final_role_counts": {},
        "action": "anchor role all matrix",
        "error": "",
    }
    ser = None
    session_started_at = time.time()
    session_status = {"text": "open port"}

    def set_status(text: str) -> None:
        session_status["text"] = text

    def progress_now() -> None:
        print_session_progress("PREP", session_status["text"], time.time() - session_started_at)

    try:
        ser = open_port(port, 60.0)
        with open(log_path, "w", buffering=1) as logf:
            emit(logf, f"PORT={port}\n", live_output, verbose)
            emit(logf, f"START={time.strftime('%Y-%m-%d %H:%M:%S')}\n", live_output, verbose)
            emit(logf, "SESSION: prepare anchors for sweep; required role=matrix\n", live_output, verbose)
            progress_now()
            time.sleep(0.25)
            while ser.in_waiting:
                data = ser.read(ser.in_waiting)
                if data:
                    emit(logf, data.decode("utf-8", "ignore"), live_output, verbose)

            set_status("clean AUTOPOS state")
            context["autopos_initialized"] = False
            ser, _ = preflight_clean_autopos_start(
                ser,
                logf,
                port,
                live_output,
                verbose,
                progress_cb=progress_now,
                force_clean=True,
            )
            set_status("build AUTOPOS map")
            ser = ensure_autopos_maps(
                ser,
                logf,
                port,
                live_output,
                verbose,
                context=context,
                progress_cb=progress_now,
                status_cb=set_status,
            )
            set_status("verify matrix roles")
            ser, ok, counts = wait_all_anchor_role(
                ser,
                logf,
                port,
                "matrix",
                3.0,
                live_output,
                verbose,
                context=context,
            )
            result["initial_role_counts"] = counts
            if not ok:
                emit(
                    logf,
                    "SESSION: switching all anchors to runtime matrix before sweep (idempotent guard)\n",
                    live_output,
                    verbose,
                )
                set_status("all anchors -> matrix")
                ser, role_text = send_cmd_collect_text(
                    ser,
                    logf,
                    port,
                    "anchor role all matrix",
                    18.0,
                    live_output,
                    verbose,
                    resend_after_reopen=False,
                    progress_cb=progress_now,
                )
                command_ok = (
                    "anchor role rc=0 target=all role=matrix" in role_text
                    or "anchor role all matrix runtime sent=" in role_text
                    or "anchor role all matrix runtime repeat sent=" in role_text
                )
                if not command_ok:
                    emit(
                        logf,
                        "SESSION WARN: matrix role guard command did not report completion; continuing to verification\n",
                        live_output,
                        verbose,
                    )

                set_status("verify matrix roles")
                ser, ok, counts = wait_all_anchor_role(
                    ser,
                    logf,
                    port,
                    "matrix",
                    20.0,
                    live_output,
                    verbose,
                    context=context,
                )
            result["final_role_counts"] = counts
            if not ok:
                emit(
                    logf,
                    "SESSION WARN: matrix command sent, but not all anchors were verified via anchor version all; retrying once\n",
                    live_output,
                    verbose,
                )
                ser, role_text = send_cmd_collect_text(
                    ser,
                    logf,
                    port,
                    "anchor role all matrix",
                    12.0,
                    live_output,
                    verbose,
                    resend_after_reopen=False,
                    progress_cb=progress_now,
                )
                if (
                    "anchor role rc=0 target=all role=matrix" in role_text
                    or "anchor role all matrix runtime sent=" in role_text
                    or "anchor role all matrix runtime repeat sent=" in role_text
                ):
                    ser, ok, counts = wait_all_anchor_role(
                        ser,
                        logf,
                        port,
                        "matrix",
                        12.0,
                        live_output,
                        verbose,
                        context=context,
                    )
                    result["final_role_counts"] = counts

            if not ok:
                emit(
                    logf,
                    "SESSION: runtime matrix did not converge; forcing anchor reset all autopos\n",
                    live_output,
                    verbose,
                )
                set_status("reset all -> autopos")
                ser, reset_text = send_cmd_collect_text(
                    ser,
                    logf,
                    port,
                    "anchor reset all autopos",
                    130.0,
                    live_output,
                    verbose,
                    resend_after_reopen=False,
                    progress_cb=progress_now,
                )
                result["reset_fallback_used"] = True
                result["reset_command_seen"] = "anchor reset rc=" in reset_text
                set_status("verify matrix roles")
                ser, ok, counts = wait_all_anchor_role(
                    ser,
                    logf,
                    port,
                    "matrix",
                    60.0,
                    live_output,
                    verbose,
                    context=context,
                )
                result["final_role_counts"] = counts

            if not ok:
                result["success"] = False
                result["error"] = "matrix_guard_verify_failed"
                emit(logf, "SESSION FAIL: matrix role guard did not verify all anchors as matrix\n",
                     live_output, verbose)
                set_status("matrix guard failed")
                finish_progress_line(render_session_progress("PREP", "matrix guard failed", time.time() - session_started_at))
                return False, result

            result["success"] = True
            emit(logf, "SESSION: matrix role guard verified; continuing into sweep\n", live_output, verbose)
            set_status("matrix guard verified")
            finish_progress_line(render_session_progress("PREP", "matrix guard verified", time.time() - session_started_at))
            return True, result
    except Exception as exc:
        result["error"] = f"{exc.__class__.__name__}: {exc}"
        finish_progress_line(render_session_progress("PREP", f"failed: {exc.__class__.__name__}", time.time() - session_started_at))
        return False, result
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass


def session_finalize_responder(
    port: str,
    out_dir: Path,
    live_output: bool,
    verbose: int,
    context: dict,
) -> dict:
    log_path = out_dir / "session_final_responder.log"
    result = {
        "success": False,
        "log_path": str(log_path),
        "role_counts": {},
        "error": "",
        "command_sent": False,
    }
    ser = None
    session_started_at = time.time()
    session_status = {"text": "open port"}

    def set_status(text: str) -> None:
        session_status["text"] = text

    def progress_now() -> None:
        print_session_progress("FINAL", session_status["text"], time.time() - session_started_at)

    def runtime_responder_ack_ok(role_text: str) -> tuple[bool, dict[str, int]]:
        info: dict[str, int] = {}
        command_ok = (
            "anchor role rc=0 target=all role=responder" in role_text
            or "anchor role all responder runtime sent=" in role_text
            or "anchor role all responder runtime repeat sent=" in role_text
            or "anchor role all responder runtime final sent=" in role_text
        )
        matches = re.findall(
            r"anchor role all responder runtime (?:repeat |final )?sent=(\d+) ready=(\d+)/(\d+)",
            role_text,
        )
        if matches:
            sent, ready, target = map(int, matches[-1])
            info["sent_count"] = sent
            info["ready_count"] = ready
            info["ready_target"] = target
            return command_ok and sent >= len(UUIDS) and ready >= len(UUIDS) and target >= len(UUIDS), info
        return False, info

    def send_responder_and_verify(
        ser: serial.Serial,
        logf,
        label: str,
        timeout_s: float,
        scan_timeout_s: float,
    ) -> tuple[serial.Serial, bool, dict[str, int], str]:
        set_status(label)
        ser, role_text = send_cmd_collect_text(
            ser,
            logf,
            port,
            "anchor role all responder",
            timeout_s,
            live_output,
            verbose,
            resend_after_reopen=False,
            progress_cb=progress_now,
        )
        command_sent = (
            "anchor role rc=0 target=all role=responder" in role_text
            or "anchor role all responder runtime sent=" in role_text
            or "anchor role all responder runtime repeat sent=" in role_text
            or "anchor role all responder runtime final sent=" in role_text
        )
        result["command_sent"] = command_sent
        ack_ok, info = runtime_responder_ack_ok(role_text)
        result.update(info)
        if ack_ok:
            counts = {"matrix": 0, "responder": len(UUIDS), "master": 0, "other": 0}
            result["role_counts"] = counts
            return ser, True, counts, role_text
        if command_sent:
            set_status("scan responder roles")
            ok, counts = wait_scan_role_counts("responder", scan_timeout_s, poll_s=2.0)
            result["role_counts"] = counts
            return ser, ok, counts, role_text
        return ser, False, result.get("role_counts", {}), role_text

    try:
        ser = open_port(port, 60.0)
        with open(log_path, "w", buffering=1) as logf:
            emit(logf, f"PORT={port}\n", live_output, verbose)
            emit(logf, f"START={time.strftime('%Y-%m-%d %H:%M:%S')}\n", live_output, verbose)
            emit(logf, "SESSION: finalize sweep; switch all anchors to runtime responder\n", live_output, verbose)
            progress_now()
            set_status("clean AUTOPOS state")
            context["autopos_initialized"] = False
            ser, _ = preflight_clean_autopos_start(
                ser,
                logf,
                port,
                live_output,
                verbose,
                progress_cb=progress_now,
                force_clean=True,
            )
            set_status("build AUTOPOS map")
            ser = ensure_autopos_maps(
                ser,
                logf,
                port,
                live_output,
                verbose,
                context=context,
                progress_cb=progress_now,
                status_cb=set_status,
            )
            ok = False
            counts = {}
            ser, ok, counts, role_text = send_responder_and_verify(
                ser,
                logf,
                "all anchors -> responder",
                30.0,
                20.0,
            )
            if not result.get("command_sent", False):
                emit(
                    logf,
                    "SESSION WARN: all-responder command did not report completion; continuing to verification\n",
                    live_output,
                    verbose,
                )
            elif ok:
                emit(
                    logf,
                    "SESSION: all-responder runtime broadcast acknowledged by 8/8 ready anchors\n",
                    live_output,
                    verbose,
                )
            else:
                emit(
                    logf,
                    (
                        f"SESSION: BLE-scan responder verify "
                        f"matrix={counts.get('matrix', 0)} "
                        f"responder={counts.get('responder', 0)} "
                        f"master={counts.get('master', 0)} "
                        f"other={counts.get('other', 0)}\n"
                    ),
                    live_output,
                    verbose,
                )
            if not ok:
                emit(
                    logf,
                    "SESSION WARN: responder command sent, but BLE scan did not yet show all anchors in responder; retrying once\n",
                    live_output,
                    verbose,
                )
                ser, ok, counts, role_text = send_responder_and_verify(
                    ser,
                    logf,
                    "all anchors -> responder retry",
                    12.0,
                    15.0,
                )
                if ok:
                    emit(
                        logf,
                        "SESSION: all-responder runtime retry acknowledged by 8/8 ready anchors\n",
                        live_output,
                        verbose,
                    )
                elif result.get("command_sent", False):
                    emit(
                        logf,
                        (
                            f"SESSION: BLE-scan responder verify after retry "
                            f"matrix={counts.get('matrix', 0)} "
                            f"responder={counts.get('responder', 0)} "
                            f"master={counts.get('master', 0)} "
                            f"other={counts.get('other', 0)}\n"
                        ),
                        live_output,
                        verbose,
                    )

            if not ok:
                emit(
                    logf,
                    "SESSION WARN: responder retry still incomplete; rebuilding AUTOPOS control links and retrying responder\n",
                    live_output,
                    verbose,
                )
                set_status("reconnect anchors for responder")
                context["autopos_initialized"] = False
                ser, _ = preflight_clean_autopos_start(
                    ser,
                    logf,
                    port,
                    live_output,
                    verbose,
                    progress_cb=progress_now,
                    force_clean=True,
                )
                set_status("rebuild AUTOPOS map")
                ser = ensure_autopos_maps(
                    ser,
                    logf,
                    port,
                    live_output,
                    verbose,
                    context=context,
                    progress_cb=progress_now,
                    status_cb=set_status,
                )
                ser, ok, counts, role_text = send_responder_and_verify(
                    ser,
                    logf,
                    "all anchors -> responder after reconnect",
                    20.0,
                    20.0,
                )
                if ok:
                    emit(
                        logf,
                        "SESSION: responder converged after control-link reconnect\n",
                        live_output,
                        verbose,
                    )
                else:
                    emit(
                        logf,
                        (
                            f"SESSION: BLE-scan responder verify after reconnect "
                            f"matrix={counts.get('matrix', 0)} "
                            f"responder={counts.get('responder', 0)} "
                            f"master={counts.get('master', 0)} "
                            f"other={counts.get('other', 0)}\n"
                        ),
                        live_output,
                        verbose,
                    )

            if not ok:
                emit(
                    logf,
                    "SESSION: runtime responder did not converge; forcing anchor reset all responder\n",
                    live_output,
                    verbose,
                )
                set_status("reset all -> responder")
                ser, reset_text = send_cmd_collect_text(
                    ser,
                    logf,
                    port,
                    "anchor reset all responder",
                    40.0,
                    live_output,
                    verbose,
                    resend_after_reopen=False,
                    progress_cb=progress_now,
                )
                result["reset_fallback_used"] = True
                result["reset_command_seen"] = "anchor reset rc=" in reset_text
                ack_ok, info = runtime_responder_ack_ok(reset_text)
                result.update(info)
                if ack_ok:
                    ok = True
                    counts = {"matrix": 0, "responder": len(UUIDS), "master": 0, "other": 0}
                    result["role_counts"] = counts
                    emit(
                        logf,
                        "SESSION: reset-responder command acknowledged by 8/8 ready anchors\n",
                        live_output,
                        verbose,
                    )
                else:
                    set_status("scan responder roles")
                    ok, counts = wait_scan_role_counts("responder", 120.0, poll_s=3.0)
                    result["role_counts"] = counts
                    emit(
                        logf,
                        (
                            f"SESSION: BLE-scan responder verify after reset "
                            f"matrix={counts.get('matrix', 0)} "
                            f"responder={counts.get('responder', 0)} "
                            f"master={counts.get('master', 0)} "
                            f"other={counts.get('other', 0)}\n"
                        ),
                        live_output,
                        verbose,
                    )

            result["success"] = bool(ok)
            if ok:
                emit(logf, "SESSION: all anchors responder\n", live_output, verbose)
                set_status("all anchors switch back to responder")
                finish_progress_line(
                    render_session_progress("FINAL", "all anchors switch back to responder", time.time() - session_started_at)
                )
            else:
                result["error"] = "responder_verify_failed"
                emit(logf, "SESSION FAIL: all anchors did not verify as responder\n", live_output, verbose)
                set_status("responder verify failed")
                finish_progress_line(
                    render_session_progress("FINAL", "responder verify failed", time.time() - session_started_at)
                )
            return result
    except Exception as exc:
        result["error"] = f"{exc.__class__.__name__}: {exc}"
        finish_progress_line(render_session_progress("FINAL", f"failed: {exc.__class__.__name__}", time.time() - session_started_at))
        return result
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass


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
    strict: bool,
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

    # Multi-Tag safety:
    # First clear filter and stop scan churn best-effort, then program target sequentially.
    try:
        ser = send_cmd_collect(ser, logf, port, "ota_target prefix -", 0.8, live_output, verbose)
        ser = send_cmd_collect(ser, logf, port, "ota_target name -", 0.8, live_output, verbose)
        ser, _ = collect_for(ser, logf, 0.4, port, live_output, verbose)
    except Exception:
        pass

    ser = send_cmd_collect(ser, logf, port, "device kind tag", 1.2, live_output, verbose)
    ser, _, _ = wait_for_patterns(
        ser,
        logf,
        port,
        ["device kind set: tag"],
        8.0,
        live_output,
        verbose,
    )
    ser, name_text = send_cmd_collect_text(
        ser,
        logf,
        port,
        f"ota_target name {target_name}",
        1.2,
        live_output,
        verbose,
    )
    ser = send_cmd_collect(ser, logf, port, "ota_target prefix BS", 1.0, live_output, verbose)
    # Kick a fresh scan cycle with the newly-programmed filter.
    ser = send_cmd_collect(ser, logf, port, "scan", 0.6, live_output, verbose)

    # Verify target via command ack and show output.
    ack_ok = (f"ota_target name rc=0 value={target_name.lower()}" in name_text) or \
             (f"ota_target name rc=0 value={target_name}" in name_text)
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
    ready_text = name_text + show_text
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
        # Explicit connect (after filter is in place).
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
            level = "FAIL" if strict else "WARN"
            emit(logf, f"PRECHECK {level}: tag {target_name} not connected/ready in RECV\n", live_output, verbose)
            return ser, False

    def try_stream_off_aliases(cur_ser: serial.Serial) -> tuple[serial.Serial, bool, str, bool]:
        """
        Try multiple STREAM OFF command spellings for firmware compatibility.
        Returns: (ser, ok, merged_text, unsupported)
        """
        cmds = [
            "cmd STREAM OFF",
            "cmd STREAMON 0",
            "cmd STREAM 0",
        ]
        merged_text = ""
        unsupported_hits = 0
        for c in cmds:
            cur_ser, txt = send_cmd_collect_text(
                cur_ser,
                logf,
                port,
                c,
                0.8,
                live_output,
                verbose,
            )
            merged_text += txt
            ok = ("STREAM_OK OFF" in txt) or ("STREAM=OFF" in txt)
            if not ok:
                cur_ser, ok, more_stream_text = wait_for_patterns(
                    cur_ser,
                    logf,
                    port,
                    ["STREAM_OK OFF", "STREAM=OFF"],
                    3.0,
                    live_output,
                    verbose,
                )
                merged_text += more_stream_text
            if ok:
                return cur_ser, True, merged_text, False
            if "UNKNOWN_CMD" in txt or "cmd rc=-128" in txt:
                unsupported_hits += 1
        return cur_ser, False, merged_text, unsupported_hits == len(cmds)

    # Fast path: STREAM OFF directly (no mode switch), then fallback to MODE AOTA.
    quarantine_mode = "unknown"
    ser, stream_ok, stream_text, stream_unsupported = try_stream_off_aliases(ser)
    if stream_ok:
        quarantine_mode = "STREAM_OFF_ONLY"
    else:
        emit(
            logf,
            f"PRECHECK INFO: STREAM OFF direct path not ready for {target_name}; fallback MODE AOTA\n",
            live_output,
            verbose,
        )

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
            level = "FAIL" if strict else "WARN"
            emit(logf, f"PRECHECK {level}: tag {target_name} did not enter AOTA\n", live_output, verbose)
            if "MODE_BAD" in mode_text or "cmd rc=-128" in mode_text:
                emit(logf, "PRECHECK DETAIL: Tag command path rejected MODE AOTA\n", live_output, verbose)
            return ser, False

        # Retry STREAM OFF after MODE AOTA.
        ser, stream_ok, stream_text, stream_unsupported = try_stream_off_aliases(ser)
        if not stream_ok:
            if stream_unsupported or "UNKNOWN_CMD" in stream_text or "cmd rc=-128" in stream_text:
                emit(
                    logf,
                    f"PRECHECK WARN: tag {target_name} did not support STREAM OFF; keeping MODE AOTA quarantine\n",
                    live_output,
                    verbose,
                )
                quarantine_mode = "MODE_AOTA_ONLY"
            else:
                level = "FAIL" if strict else "WARN"
                emit(logf, f"PRECHECK {level}: tag {target_name} did not ack STREAM OFF\n", live_output, verbose)
                return ser, False
        else:
            quarantine_mode = "MODE_AOTA_PLUS_STREAM_OFF"

    emit(
        logf,
        f"PRECHECK PASS: tag {target_name} quarantined for sweep ({quarantine_mode})\n",
        live_output,
        verbose,
    )
    # Stop any additional Tag churn after we are done (prevents accidental extra Tag connects in noisy RF).
    try:
        ser = send_cmd_collect(ser, logf, port, "ota_target prefix -", 0.8, live_output, verbose)
        ser = send_cmd_collect(ser, logf, port, "ota_target name -", 0.8, live_output, verbose)
    except Exception:
        pass
    return ser, True


def parse_quiet_tag_names(quiet_tag_name: str | None) -> list[str]:
    """
    Parse Tag BLE names to quarantine before each sweep round.

    Backward compatible:
    - legacy: --quiet-tag-name BSF66F
    - new:    --quiet-tag-name 'BSF66F,BS2DCE,BSDC91' (comma/space separated)
    - use '-' or empty to disable
    """
    if quiet_tag_name is None:
        return []
    s = quiet_tag_name.strip()
    if s == "" or s == "-":
        return []
    parts = [p for p in re.split(r"[,\s]+", s) if p and p != "-"]
    out: list[str] = []
    seen = set()
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def discover_quiet_tag_names_auto(
    ser: serial.Serial,
    logf,
    port: str,
    live_output: bool,
    verbose: int,
) -> tuple[serial.Serial, list[str]]:
    """
    Auto-discover Tag BLE names to quarantine before sweep.

    Rule:
    - include BSxxxx devices
    - exclude anchors (known anchor UUIDs or names containing "Anchor")
    """
    ser, status_text = send_cmd_collect_text(
        ser, logf, port, "status", 0.8, live_output, verbose, resend_after_reopen=False
    )
    if "Control status: mode=AUTOPOS" in status_text or "Control status: mode=OTA" in status_text:
        ser = send_cmd_collect(
            ser, logf, port, "mode recv", 3.0, live_output, verbose, resend_after_reopen=False
        )
        ser, _ = collect_for(ser, logf, 1.2, port, live_output, verbose)

    # Clear target filters and request a broad BS scan snapshot.
    setup_cmds = [
        "device kind tag",
        "ota_target token -1",
        "ota_target name -",
        "ota_target prefix BS",
        "ota_target uuid -",
        "scan",
    ]
    for c in setup_cmds:
        emit(logf, f">>> {c}\n", live_output, verbose)
    ser = write_cmds(ser, setup_cmds)
    ser, _, scan_text = collect_for_text(ser, logf, 4.0, port, live_output, verbose)

    known_anchor_uuids = {u.upper() for u in UUIDS.values()}
    found: list[str] = []
    seen = set()

    for line in scan_text.splitlines():
        if "SCAN hit:" not in line:
            continue
        m_bs = re.search(r"\bbs=(BS[A-Z0-9]+)\b", line)
        if not m_bs:
            continue
        bs_name = m_bs.group(1).upper()
        m_name = re.search(r"\bname=([^ ]+)", line)
        dev_name = (m_name.group(1) if m_name else "").lower()
        if "anchor" in dev_name:
            continue
        m_uuid = re.search(r"\buuid=([A-F0-9]{32}|-)\b", line)
        adv_uuid = m_uuid.group(1).upper() if m_uuid else "-"
        if adv_uuid in known_anchor_uuids:
            continue
        if "target=anchor" in line:
            continue
        if bs_name in seen:
            continue
        seen.add(bs_name)
        found.append(bs_name)

    emit(logf, f"PRECHECK AUTO: discovered quiet tags={found}\n", live_output, verbose)
    return ser, found


def round_capture(
    port: str,
    master: str,
    out_dir: Path,
    timeout_s: int,
    warmup_min_quality: int,
    quiet_tag_name: str | None,
    quiet_tag_retries: int,
    quiet_tag_required: bool,
    context: dict | None = None,
    round_idx: int = 1,
    round_total: int = 1,
    command_started_at: float | None = None,
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
        "device_sw_count": 0,
        "warmup_min_quality": warmup_min_quality,
        "warmup_sw_lines": [],
        "warmup_sw_count": 0,
        "warmup_discarded_count": 0,
        "min_quality_seen": None,
        "pairs_below_quality": {},
        "log_path": str(log_path),
        "error": "",
        "warnings": [],
        "reconnect_retry_seen": False,
        "reconnect_retry_lines": [],
        "sweep_done_seen": False,
        "precheck_elapsed_s": None,
        "switch_elapsed_s": None,
        "warmup_elapsed_s": None,
        "collect_elapsed_s": None,
        "total_elapsed_s": None,
    }
    verified = set()
    ser = None
    round_started_at = time.time()
    if command_started_at is None:
        command_started_at = round_started_at
    raw_sweep_started_at: float | None = None
    formal_sweep_started_at: float | None = None
    precheck_done_at: float | None = None
    stage = "starting"
    raw_sw_seen_for_process = 0
    raw_sw_seen_for_log = 0

    def progress_now(current_stage: str, eta_s: float | None = None) -> None:
        print_round_progress(
            master,
            round_idx,
            round_total,
            result["sw_count"],
            round_capture.target_sw_sets,
            current_stage,
            time.time() - command_started_at,
            time.time() - round_started_at,
            eta_s,
            result["warmup_discarded_count"],
            round_capture.prewarm_sw_sets,
        )

    try:
        ser = open_port(port, 60.0)
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

            stage = "precheck"
            progress_now(stage)
            emit(
                logf,
                "PRECHECK: BSxxxx tag quarantine disabled; master/matrix anchors ignore tag polls during sweep\n",
                live_output,
                verbose,
            )
            if context is not None:
                emit(
                    logf,
                    "PRECHECK: context "
                    f"session_autopos_ready={int(bool(context.get('session_autopos_ready', False)))} "
                    f"autopos_initialized={int(bool(context.get('autopos_initialized', False)))}\n",
                    live_output,
                    verbose,
                )

            reuse_autopos_session = bool(context and context.get("session_autopos_ready", False))
            if reuse_autopos_session:
                emit(logf, "PRECHECK: reusing session AUTOPOS state from matrix guard\n", live_output, verbose)
            else:
                ser, preflight_ok = preflight_clean_autopos_start(
                    ser,
                    logf,
                    port,
                    live_output,
                    verbose,
                    progress_cb=lambda: progress_now("precheck"),
                )
                if not preflight_ok:
                    result["error"] = "autopos_idle_not_reached"
                    emit(logf, "PRECHECK FAIL: AUTOPOS idle not reached\n", live_output, verbose)
                    emit(logf, f"END={time.strftime('%Y-%m-%d %H:%M:%S')}\n", live_output, verbose)
                    flush_live_buffer(logf, verbose)
                    finish_round_progress()
                    return result

            ser = ensure_autopos_maps(
                ser,
                logf,
                port,
                live_output,
                verbose,
                context=context,
                progress_cb=lambda: progress_now("precheck"),
            )

            if context is not None and context.get("bootstrap_autopos_reset", False) and not context.get("bootstrap_done", False):
                context["bootstrap_done"] = True
                bootstrap_ok = False
                try:
                    ser, bootstrap_ok = bootstrap_reset_all_autopos(
                        ser,
                        logf,
                        port,
                        live_output,
                        verbose,
                        progress_cb=lambda: progress_now("precheck"),
                    )
                except Exception as exc:
                    result["warnings"].append(
                        f"bootstrap reset all autopos failed once and was skipped: {exc.__class__.__name__}: {exc}"
                    )
                    emit(
                        logf,
                        f"PRECHECK WARN: bootstrap reset all autopos failed once: {exc.__class__.__name__}: {exc}\n",
                        live_output,
                        verbose,
                    )
                if not bootstrap_ok:
                    result["warnings"].append("bootstrap reset all autopos did not verify all anchors as matrix")

            def filter_runtime_text(text: str) -> str:
                nonlocal raw_sw_seen_for_log
                if round_capture.prewarm_sw_sets <= 0:
                    return text
                out_lines: list[str] = []
                for line in text.splitlines(keepends=True):
                    stripped = line.rstrip("\n")
                    if f"SW-{master}," in stripped:
                        raw_sw_seen_for_log += 1
                        if raw_sw_seen_for_log <= round_capture.prewarm_sw_sets:
                            minq = sw_line_min_quality(master, stripped)
                            out_lines.append(
                                f"[AUTOPOS] SW-{master} warmup discard "
                                f"{raw_sw_seen_for_log}/{round_capture.prewarm_sw_sets}"
                                + (f" minq={minq}\n" if minq is not None else "\n")
                            )
                            continue
                    out_lines.append(line)
                return "".join(out_lines)

            def reset_sweep_counters_for_history_replay() -> None:
                nonlocal raw_sw_seen_for_process, raw_sw_seen_for_log, raw_sweep_started_at, formal_sweep_started_at, stage
                raw_sw_seen_for_process = 0
                raw_sw_seen_for_log = 0
                raw_sweep_started_at = None
                formal_sweep_started_at = None
                stage = "switching"
                result["sw_seen"] = False
                result["sw_line"] = ""
                result["sw_lines"] = []
                result["sw_count"] = 0
                result["device_sw_count"] = 0
                result["warmup_sw_lines"] = []
                result["warmup_sw_count"] = 0
                result["warmup_discarded_count"] = 0
                result["min_quality_seen"] = None
                result["pairs_below_quality"] = {}
                result["sweep_done_seen"] = False

            def process_runtime_text(text: str) -> None:
                nonlocal stage, raw_sweep_started_at, formal_sweep_started_at, raw_sw_seen_for_process
                for line in text.splitlines():
                    if "AUTOPOS anchor " in line and " role verified" in line:
                        parts = line.split("AUTOPOS anchor ", 1)[1]
                        verified.add(parts.split(" ", 1)[0])
                    if "forcing control-link reconnect retry" in line:
                        result["reconnect_retry_seen"] = True
                        result["reconnect_retry_lines"].append(line.strip())
                    if "AUTOPOS apply success:" in line:
                        result["apply_success_seen"] = True
                        stage = "switching"
                    if f"AUTOPOS sweep listen attach: master={master}" in line:
                        result["sweep_ready_seen"] = True
                        stage = "sweeping"
                    if f"SWEEP_DONE master={master}" in line:
                        result["sweep_done_seen"] = True
                    if f"SW-{master}," in line:
                        line = line.strip()
                        raw_sw_seen_for_process += 1
                        result["device_sw_count"] = raw_sw_seen_for_process
                        if raw_sweep_started_at is None:
                            raw_sweep_started_at = time.time()
                        if (not result["apply_success_seen"] and
                                "SW lines observed without AUTOPOS apply success" not in result["warnings"]):
                            result.setdefault("warnings", []).append(
                                "SW lines observed without AUTOPOS apply success"
                            )
                        if not result["sweep_ready_seen"]:
                            result["sweep_ready_seen"] = True
                        if raw_sw_seen_for_process <= round_capture.prewarm_sw_sets:
                            result["warmup_discarded_count"] = raw_sw_seen_for_process
                            stage = "warmup"
                            continue
                        stage = "sweeping"
                        if formal_sweep_started_at is None:
                            formal_sweep_started_at = time.time()
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
                    elif "SW-" in line and f"SW-{master}," not in line:
                        m = re.search(r"SW-([A-H]),", line)
                        if m:
                            stale_master = m.group(1)
                            warning = (
                                f"stale SW-{stale_master} observed while preparing SW-{master}; "
                                "finite-master image should auto-return it to matrix"
                            )
                            if warning not in result["warnings"]:
                                result["warnings"].append(warning)
                                emit(logf, f"PRECHECK WARN: {warning}\n", live_output, verbose)

            def recover_sw_lines_from_result_history(reason: str) -> bool:
                nonlocal ser
                emit(
                    logf,
                    f"RECOVER: requesting AUTOPOS result history ({reason})\n",
                    live_output,
                    verbose,
                )
                try:
                    cur_ser, show_text = send_cmd_collect_text(
                        ser,
                        logf,
                        port,
                        "autopos result show",
                        2.0,
                        live_output,
                        verbose,
                        resend_after_reopen=True,
                        progress_cb=lambda: progress_now("sweeping"),
                    )
                except Exception as exc:
                    emit(
                        logf,
                        f"RECOVER WARN: autopos result show failed: {exc.__class__.__name__}: {exc}\n",
                        live_output,
                        verbose,
                    )
                    return False

                history_lines = []
                for line in show_text.splitlines():
                    if line.startswith("AUTOPOS history["):
                        history_lines.append(line)
                if not history_lines:
                    emit(logf, "RECOVER WARN: no AUTOPOS history lines returned\n", live_output, verbose)
                    return False

                history_payload = "\n".join(history_lines) + "\n"
                ser = cur_ser
                reset_sweep_counters_for_history_replay()
                process_runtime_text(history_payload)
                emit(
                    logf,
                    "RECOVER: replayed AUTOPOS history "
                    f"entries={len(history_lines)} sw={result['sw_count']}/{round_capture.target_sw_sets} "
                    f"raw={result['device_sw_count']} sweep_done={int(bool(result['sweep_done_seen']))}\n",
                    live_output,
                    verbose,
                )
                return True

            precheck_done_at = time.time()

            round_cmd = f"autopos round {master} {round_capture.device_sw_sets}"
            round_confirmed = False
            ser, round_text = send_cmd_collect_text(
                ser,
                logf,
                port,
                round_cmd,
                1.5,
                live_output,
                verbose,
                progress_cb=lambda: progress_now("switching"),
                text_filter=filter_runtime_text,
            )
            process_runtime_text(round_text)
            if f"AUTOPOS round staged: master={master}" in round_text:
                round_confirmed = True
            else:
                emit(
                    logf,
                    f"PRECHECK WARN: round staging for {master} not confirmed on first try; retrying once\n",
                    live_output,
                    verbose,
                )
                ser, round_text = send_cmd_collect_text(
                    ser,
                    logf,
                    port,
                    round_cmd,
                    2.0,
                    live_output,
                    verbose,
                    progress_cb=lambda: progress_now("switching"),
                    text_filter=filter_runtime_text,
                )
                process_runtime_text(round_text)
                if f"AUTOPOS round staged: master={master}" in round_text:
                    round_confirmed = True
            if not round_confirmed:
                ser, status_text = send_cmd_collect_text(
                    ser,
                    logf,
                    port,
                    "autopos status",
                    1.0,
                    live_output,
                    verbose,
                    progress_cb=lambda: progress_now("switching"),
                    text_filter=filter_runtime_text,
                )
                process_runtime_text(status_text)
                status_ok = (
                    (
                        f"state=staged staged={master}" in status_text or
                        f"state=ready staged={master}" in status_text or
                        f"state=running staged={master}" in status_text
                    )
                    and f"sets={round_capture.device_sw_sets}" in status_text
                )
                if status_ok:
                    round_confirmed = True
                    emit(
                        logf,
                        f"PRECHECK: round staging for {master} confirmed via autopos status\n",
                        live_output,
                        verbose,
                    )
            if not round_confirmed:
                raise RuntimeError(f"autopos round staging failed for {master}")

            for cmd, pause_s in [("autopos status", 0.5), ("autopos apply", 0.5)]:
                stage = "switching"
                print_round_progress(
                    master,
                    round_idx,
                    round_total,
                    result["sw_count"],
                    round_capture.target_sw_sets,
                    stage,
                    time.time() - command_started_at,
                    time.time() - round_started_at,
                    None,
                    result["warmup_discarded_count"],
                    round_capture.prewarm_sw_sets,
                )
                ser, cmd_text = send_cmd_collect_text(
                    ser,
                    logf,
                    port,
                    cmd,
                    pause_s,
                    live_output,
                    verbose,
                    progress_cb=lambda: progress_now(stage),
                    text_filter=filter_runtime_text,
                )
                process_runtime_text(cmd_text)

            deadline = time.time() + timeout_s
            status_marks = {30, 60, 120, 180, 240, 300, 360, 420}
            sent_marks = set()
            while time.time() < deadline:
                elapsed = int(timeout_s - (deadline - time.time()))
                eta_s = None
                if formal_sweep_started_at is not None and result["sw_count"] > 0 and round_capture.target_sw_sets > 0:
                    sweep_elapsed = max(0.001, time.time() - formal_sweep_started_at)
                    total_est = sweep_elapsed / (result["sw_count"] / round_capture.target_sw_sets)
                    eta_s = max(0.0, total_est - sweep_elapsed)
                print_round_progress(
                    master,
                    round_idx,
                    round_total,
                    result["sw_count"],
                    round_capture.target_sw_sets,
                    stage,
                    time.time() - command_started_at,
                    time.time() - round_started_at,
                    eta_s,
                    result["warmup_discarded_count"],
                    round_capture.prewarm_sw_sets,
                )
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
                    emit(logf, filter_runtime_text(text), live_output, verbose)
                    process_runtime_text(text)
                    if result["sw_count"] >= round_capture.target_sw_sets:
                        result["success"] = True
                        stage = "done"
                        break
                    if result["sweep_done_seen"]:
                        stage = "failed"
                        break
                else:
                    time.sleep(0.1)

            result["verified_count"] = len(verified)
            if result["apply_success_seen"]:
                result["warnings"] = [
                    w for w in result["warnings"]
                    if w != "SW lines observed without AUTOPOS apply success"
                ]
            if not result["success"] and result["sw_count"] < round_capture.target_sw_sets:
                history_replayed = False
                if result["sweep_done_seen"]:
                    history_replayed = recover_sw_lines_from_result_history("SWEEP_DONE seen before target reached")
                if not history_replayed:
                    ser, final_status_text = send_cmd_collect_text(
                        ser,
                        logf,
                        port,
                        "autopos status",
                        1.0,
                        live_output,
                        verbose,
                        resend_after_reopen=True,
                        progress_cb=lambda: progress_now("sweeping"),
                        text_filter=filter_runtime_text,
                    )
                    process_runtime_text(final_status_text)
                    status_indicates_complete = (
                        f"last_success={master}" in final_status_text and
                        f"sets={round_capture.device_sw_sets}" in final_status_text
                    )
                    if status_indicates_complete:
                        recover_sw_lines_from_result_history("autopos status indicates round completed")
                if result["sw_count"] >= round_capture.target_sw_sets:
                    result["success"] = True
                    stage = "done"
            if not result["success"]:
                if not result["sweep_ready_seen"]:
                    result["error"] = "sweep_ready_not_seen"
                elif not result["sw_seen"]:
                    result["error"] = "sw_not_seen"
                else:
                    result["error"] = f"insufficient_sw_sets:{result['sw_count']}/{round_capture.target_sw_sets}"
                stage = "failed"
            result["warnings"] = result["warnings"] + summarize_round_warnings(
                master, result["sw_lines"], result["sw_seen"]
            )
            round_finished_at = time.time()
            result["total_elapsed_s"] = round_finished_at - round_started_at
            if precheck_done_at is not None:
                result["precheck_elapsed_s"] = max(0.0, precheck_done_at - round_started_at)
                switch_end = raw_sweep_started_at or round_finished_at
                result["switch_elapsed_s"] = max(0.0, switch_end - precheck_done_at)
            else:
                result["precheck_elapsed_s"] = 0.0
                result["switch_elapsed_s"] = None
            if raw_sweep_started_at is not None and formal_sweep_started_at is not None:
                result["warmup_elapsed_s"] = max(0.0, formal_sweep_started_at - raw_sweep_started_at)
            else:
                result["warmup_elapsed_s"] = 0.0
            collect_anchor = formal_sweep_started_at or raw_sweep_started_at or precheck_done_at or round_started_at
            result["collect_elapsed_s"] = max(0.0, round_finished_at - collect_anchor)
            print_round_progress(
                master,
                round_idx,
                round_total,
                result["sw_count"],
                round_capture.target_sw_sets,
                stage,
                time.time() - command_started_at,
                time.time() - round_started_at,
                0.0 if stage == "done" else None,
                result["warmup_discarded_count"],
                round_capture.prewarm_sw_sets,
            )
            finish_round_progress()
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
                print_round_progress(
                    master,
                    round_idx,
                    round_total,
                    result["sw_count"],
                    round_capture.target_sw_sets,
                    "failed",
                    time.time() - command_started_at,
                    time.time() - round_started_at,
                    None,
                )
                finish_round_progress()
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
        "--prewarm-sw-sets",
        type=int,
        default=10,
        help="Per-round SW lines to discard as warm-up before counting/logging formal sweep data. Use 0 to disable.",
    )
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
        default="-",
        help="Legacy Tag quarantine control. Use - (default) to disable heavy quarantine. 'auto' or a list like 'BSF66F,BS2DCE' retains the old MODE AOTA + STREAM OFF flow.",
    )
    parser.add_argument(
        "--quiet-tag-retries",
        type=int,
        default=3,
        help="Retry count per Tag quarantine attempt before giving up.",
    )
    parser.add_argument(
        "--quiet-tag-required",
        action="store_true",
        help="If set, abort the sweep round when any configured/discovered Tag cannot be quarantined.",
    )
    parser.add_argument("--out-dir", required=True, help="Output directory")
    parser.add_argument(
        "--no-live-output",
        action="store_true",
        help="Do not mirror runtime logs to stdout; write to log files only.",
    )
    parser.add_argument(
        "--no-bootstrap-autopos-reset",
        action="store_true",
        help="Skip the one-shot 'anchor reset all autopos' bootstrap at the start of the sweep.",
    )
    parser.add_argument(
        "--no-session-role-guard",
        action="store_true",
        help="Skip session start role guard. By default, all anchors are verified/switched to matrix before SW-A.",
    )
    parser.add_argument(
        "--no-final-responder",
        action="store_true",
        help="Skip switching all anchors to runtime responder after the full sweep succeeds.",
    )
    parser.add_argument(
        "--round-retries",
        type=int,
        default=2,
        help="Retry a failed master round this many extra times before giving up.",
    )
    args = parser.parse_args()

    if args.sw_sets < 1:
        raise SystemExit("--sw-sets must be >= 1")
    if args.prewarm_sw_sets < 0:
        raise SystemExit("--prewarm-sw-sets must be >= 0")
    if args.warmup_min_quality < 0 or args.warmup_min_quality > 100:
        raise SystemExit("--warmup-min-quality must be between 0 and 100")

    if args.timeout_s is None:
        args.timeout_s = auto_timeout_for_sw_sets(args.sw_sets)

    round_capture.target_sw_sets = args.sw_sets
    round_capture.prewarm_sw_sets = args.prewarm_sw_sets
    round_capture.device_sw_sets = args.sw_sets + args.prewarm_sw_sets

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resume-friendly: if the caller reuses an out-dir (e.g. round_F finished but
    # round_G/H need a rerun), preserve existing per-round results in summary.json
    # instead of overwriting them with only the new --order subset.
    summary_path = out_dir / "summary.json"
    summary: dict = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = {}
    if not isinstance(summary, dict):
        summary = {}
    summary.setdefault("rounds", {})
    if not isinstance(summary.get("rounds"), dict):
        summary["rounds"] = {}
    if "started_at" not in summary:
        summary["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # Always refresh run parameters to match the current invocation.
    summary.update({
        "port": args.port,
        "order": list(args.order),
        "sw_sets": args.sw_sets,
        "prewarm_sw_sets": args.prewarm_sw_sets,
        "device_sw_sets": args.sw_sets + args.prewarm_sw_sets,
        "warmup_min_quality": args.warmup_min_quality,
        "timeout_s": args.timeout_s,
        "verbose": args.verbose,
        "quiet_tag_names_config": None if args.quiet_tag_name == "-" else args.quiet_tag_name,
        "quiet_tag_names": parse_quiet_tag_names(None if args.quiet_tag_name == "-" else args.quiet_tag_name),
        "quiet_tag_retries": args.quiet_tag_retries,
        "quiet_tag_required": bool(args.quiet_tag_required),
        "bootstrap_autopos_reset": not bool(args.no_bootstrap_autopos_reset),
        "session_role_guard": not bool(args.no_session_role_guard),
        "final_responder": not bool(args.no_final_responder),
        "slow_switch_threshold_s": 10.0,
    })
    command_started_at = time.time()
    run_context = {
        "autopos_initialized": False,
        "bootstrap_autopos_reset": not bool(args.no_bootstrap_autopos_reset),
        "bootstrap_done": False,
        "session_autopos_ready": False,
    }

    if not args.no_session_role_guard:
        print("=== SESSION ROLE GUARD: MATRIX ===", flush=True)
        ok, guard_result = session_prepare_matrix(
            args.port,
            out_dir,
            live_output=not args.no_live_output,
            verbose=args.verbose,
            context=run_context,
        )
        summary["session_role_guard_result"] = guard_result
        with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps(guard_result, indent=2), flush=True)
        if not ok:
            return 1
        run_context["session_autopos_ready"] = True

    for idx, master in enumerate(args.order, start=1):
        round_dir = out_dir / f"round_{master}"
        round_dir.mkdir(parents=True, exist_ok=True)
        print(f"=== AUTOPOS SWEEP {master} ===", flush=True)
        result = None
        attempt_count = max(0, int(args.round_retries)) + 1
        attempt_timeout_s = args.timeout_s
        for attempt in range(1, attempt_count + 1):
            if attempt > 1:
                print(
                    f"=== RETRY SWEEP {master} attempt={attempt}/{attempt_count} timeout_s={attempt_timeout_s} ===",
                    flush=True,
                )
                time.sleep(2.0)
            try:
                result = round_capture(
                    args.port,
                    master,
                    round_dir,
                    attempt_timeout_s,
                    args.warmup_min_quality,
                    None if args.quiet_tag_name == "-" else args.quiet_tag_name,
                    args.quiet_tag_retries,
                    bool(args.quiet_tag_required),
                    context=run_context,
                    round_idx=idx,
                    round_total=len(args.order),
                    command_started_at=command_started_at,
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
                    "device_sw_count": 0,
                    "warmup_min_quality": args.warmup_min_quality,
                    "warmup_sw_lines": [],
                    "warmup_sw_count": 0,
                    "warmup_discarded_count": 0,
                    "min_quality_seen": None,
                    "pairs_below_quality": {},
                    "log_path": str((round_dir / "master.log").resolve()),
                    "error": "exception_in_round_capture",
                    "warnings": [f"Anchor {master}: no output as Master"],
                    "reconnect_retry_seen": False,
                    "reconnect_retry_lines": [],
                    "precheck_elapsed_s": None,
                    "switch_elapsed_s": None,
                    "warmup_elapsed_s": None,
                    "collect_elapsed_s": None,
                    "total_elapsed_s": None,
                }
            summary["rounds"][master] = result
            with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
            print(json.dumps(result, indent=2), flush=True)
            if result.get("success"):
                run_context["session_autopos_ready"] = True
                break
            run_context["session_autopos_ready"] = False
            run_context["autopos_initialized"] = False
            if attempt >= attempt_count:
                break
            next_timeout_s = suggested_retry_timeout_s(result, attempt_timeout_s)
            print(
                f"ROUND RETRY: SW-{master} failed ({result.get('error', '-')}); "
                f"next timeout_s={next_timeout_s}",
                flush=True,
            )
            attempt_timeout_s = next_timeout_s
        if not result["success"]:
            return 1

    if not args.no_final_responder:
        print("=== SESSION FINALIZER: RESPONDER ===", flush=True)
        final_result = session_finalize_responder(
            args.port,
            out_dir,
            live_output=not args.no_live_output,
            verbose=args.verbose,
            context=run_context,
        )
        summary["session_final_responder_result"] = final_result
        with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps(final_result, indent=2), flush=True)
        if not final_result.get("success"):
            return 1

    summary["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    summary["total_elapsed_s"] = time.time() - command_started_at
    summary["warnings"] = summarize_global_warnings(summary["rounds"])
    with open(out_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    for line in build_run_summary_lines(summary):
        print(line, flush=True)
    if summary["warnings"]:
        print("=== WARNINGS ===", flush=True)
        for warning in summary["warnings"]:
            print(warning, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
