#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import serial
from serial import SerialException

FILTER_RE = re.compile(
    r"OTA target filter:\s*token=([-\d]+)\s+name=([^\s]+)\s+prefix=([^\s]+)\s+uuid=([0-9A-F\-]+|-)",
    re.IGNORECASE,
)
NAME_ACK_RE = re.compile(r"ota_target name rc=([-\d]+)\s+value=([^\s]+|-)", re.IGNORECASE)
PREFIX_ACK_RE = re.compile(r"ota_target prefix rc=([-\d]+)\s+value=([^\s]+|-)", re.IGNORECASE)
MODE_RE = re.compile(r"Control status:\s*mode=([A-Z]+)")
LOADED_MODE_RE = re.compile(r"Control mode loaded:\s*([A-Z]+)")
INIT_RE = re.compile(r"initiate rc=([-\d]+)")
SYSTEM_TARGET_RE = re.compile(r"System target:\s*kind=([a-zA-Z]+)")


@dataclass
class RunResult:
    port: str
    target_name: str
    target_prefix: str
    target_selection_ready: bool
    target_restore_ready: bool
    target_transport_ready: bool
    phase_a_ok: bool
    phase_b_ok: bool
    reboot_seen: bool
    reconnect_ok: bool
    mode_ota_loaded_after_reboot: bool
    uart_ready_after_reboot: bool
    restored_line_seen: bool
    name_ack_seen: bool
    name_pre_reboot_latched: bool
    name_restored_after_reboot: bool
    filter_name_pre: str
    filter_name_post: str
    filter_prefix_pre: str
    filter_prefix_post: str
    initiate_sent: bool
    initiate_rc: int | None
    ota_started: bool
    dfu_ready_seen: bool
    ota_cmd_issued_seen: bool
    ota_wait_fail_seen: bool
    ota_gate_fail_seen: bool
    ota_upload_started_seen: bool
    ota_upload_progress_seen: bool
    ota_upload_complete_seen: bool
    ota_pending_test_seen: bool
    ota_reset_request_seen: bool
    ota_pending_recovery_reset_seen: bool
    ota_command_sequence_seen: bool
    ota_later_fail_seen: bool
    ota_success_seen: bool
    controller_returned_to_recv: bool
    classification: str
    blocker: str
    serial_lost: bool
    reason: str
    log_path: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reboot-aware strict-name Tag OTA launcher.")
    p.add_argument("--port", required=True)
    p.add_argument("--target-name", required=True, help="BLE Tag name, e.g. BSF66F")
    p.add_argument("--target-prefix", default="BS")
    p.add_argument("--out-dir", default="")
    p.add_argument("--timeout-s", type=float, default=300.0)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--reconnect-timeout-s", type=float, default=45.0)
    p.add_argument("--force-kill-port-owner", action="store_true")
    p.add_argument("--phase-a-only", action="store_true")
    return p.parse_args()


def _is_exclusive_lock_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "exclusively lock port" in msg or (isinstance(exc, SerialException) and getattr(exc, "errno", None) == 11)


def _force_kill_port_owners(port: str, logf, t0: float) -> bool:
    dev = os.path.realpath(port)
    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] serial_lock force_kill begin dev={dev}\n")
    logf.flush()
    try:
        cp = subprocess.run(["fuser", dev], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] serial_lock force_kill failed reason=fuser_not_found\n")
        logf.flush()
        return False
    text = (cp.stdout or "") + "\n" + (cp.stderr or "")
    pids = sorted({int(x) for x in re.findall(r"\b(\d+)\b", text)})
    pids = [pid for pid in pids if pid not in (0, os.getpid())]
    for pid in pids:
        try:
            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] serial_lock force_kill term pid={pid}\n")
            logf.flush()
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    time.sleep(0.8)
    for pid in pids:
        try:
            os.kill(pid, 0)
            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] serial_lock force_kill kill pid={pid}\n")
            logf.flush()
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    time.sleep(0.4)
    return bool(pids)


def open_serial(port: str, baud: int, *, force_kill_port_owner: bool = False, logf=None, t0: float = 0.0) -> serial.Serial:
    deadline = time.monotonic() + 30.0
    last_exc: Exception | None = None
    kill_attempted = False
    while time.monotonic() < deadline:
        try:
            return serial.Serial(port, baud, timeout=0.2, exclusive=True, dsrdtr=False, rtscts=False)
        except Exception as exc:
            last_exc = exc
            if force_kill_port_owner and (not kill_attempted) and _is_exclusive_lock_error(exc) and logf is not None:
                kill_attempted = True
                _force_kill_port_owners(port, logf, t0)
            time.sleep(0.4)
    assert last_exc is not None
    raise last_exc


def send_cmd(ser: serial.Serial, cmd: str, logf, t0: float) -> None:
    ser.write((cmd + "\n").encode("utf-8"))
    ser.flush()
    logf.write(f"[HOST_CMD {time.monotonic()-t0:7.2f}s] {cmd}\n")
    logf.flush()


def read_line(ser: serial.Serial) -> str | None:
    raw = ser.readline()
    if not raw:
        return None
    return raw.decode("utf-8", errors="replace").rstrip("\r\n")


def wait_uart_ready(ser: serial.Serial, logf, t0: float, *, probe_status: bool = True, timeout_s: float = 25.0) -> tuple[bool, str]:
    boot_mode = ""
    deadline = time.monotonic() + timeout_s
    last_probe = 0.0
    while time.monotonic() < deadline:
        now = time.monotonic()
        if probe_status and now - last_probe > 2.0:
            send_cmd(ser, "status", logf, t0)
            last_probe = now
        line = read_line(ser)
        if line is None:
            continue
        logf.write(line + "\n")
        logf.flush()
        m_mode = MODE_RE.search(line) or LOADED_MODE_RE.search(line)
        if m_mode:
            boot_mode = m_mode.group(1).upper()
        if "UART control ready" in line or "Control status:" in line or "OTA target filter:" in line:
            return True, boot_mode
    return False, boot_mode


def wait_uart_ready_with_reconnect(port: str, baud: int, logf, t0: float, *, timeout_s: float) -> tuple[serial.Serial | None, bool, str]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            ser = open_serial(port, baud)
            ser.reset_input_buffer()
            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] serial_reconnected port={port}\n")
            logf.flush()
            ready, mode = wait_uart_ready(ser, logf, t0, timeout_s=min(8.0, max(1.0, deadline - time.monotonic())))
            if ready:
                return ser, True, mode
            ser.close()
        except SerialException:
            time.sleep(0.35)
    return None, False, ""


def drain_serial_for(ser: serial.Serial, logf, t0: float, *, timeout_s: float) -> tuple[bool, bool]:
    serial_lost = False
    reboot_seen = False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            line = read_line(ser)
        except SerialException as exc:
            serial_lost = True
            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] serial_disconnect_during_recovery err={exc}\n")
            logf.flush()
            break
        if line is None:
            continue
        logf.write(line + "\n")
        logf.flush()
        if "rebooting" in line.lower():
            reboot_seen = True
            break
    return serial_lost, reboot_seen


def read_lines_for(ser: serial.Serial, logf, t0: float, *, timeout_s: float) -> list[str]:
    lines: list[str] = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        line = read_line(ser)
        if line is None:
            continue
        lines.append(line)
        logf.write(line + "\n")
        logf.flush()
    return lines


def recover_active_ota_session(
    ser: serial.Serial,
    *,
    port: str,
    baud: int,
    reconnect_timeout_s: float,
    logf,
    t0: float,
) -> tuple[serial.Serial | None, bool, str]:
    """Clear a stale OTA session and return a ready controller serial handle.

    ota_reset only clears ota_session_active after a DFU target is already ready.
    For a stale active session without a ready target, force a mode handoff to
    RECV because firmware clears the OTA session in master_ota_prepare_mode_switch.
    """
    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] ota_busy_recovery begin\n")
    logf.flush()
    try:
        send_cmd(ser, "ota_reset", logf, t0)
        drain_serial_for(ser, logf, t0, timeout_s=5.0)
    except SerialException as exc:
        logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] ota_reset serial_lost err={exc}\n")
        logf.flush()

    try:
        send_cmd(ser, "mode recv", logf, t0)
        drain_serial_for(ser, logf, t0, timeout_s=12.0)
    except SerialException as exc:
        logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] mode_recv serial_lost err={exc}\n")
        logf.flush()

    try:
        ser.close()
    except Exception:
        pass

    new_ser, ready, mode = wait_uart_ready_with_reconnect(
        port,
        baud,
        logf,
        t0,
        timeout_s=reconnect_timeout_s,
    )
    if ready:
        logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] ota_busy_recovery ready mode={mode or '-'}\n")
        logf.flush()
    return new_ser, ready, mode


def restore_controller_to_recv(
    *,
    port: str,
    baud: int,
    reconnect_timeout_s: float,
    logf,
    t0: float,
) -> bool:
    ser: serial.Serial | None = None
    try:
        ser = open_serial(port, baud, logf=logf, t0=t0)
        ser.reset_input_buffer()
        logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] restore_recv begin\n")
        logf.flush()
        send_cmd(ser, "mode recv", logf, t0)
        drain_serial_for(ser, logf, t0, timeout_s=12.0)
    except Exception as exc:
        logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] restore_recv command_failed err={exc}\n")
        logf.flush()
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

    ser2, ready, mode = wait_uart_ready_with_reconnect(
        port,
        baud,
        logf,
        t0,
        timeout_s=reconnect_timeout_s,
    )
    if ser2 is not None:
        try:
            ser2.close()
        except Exception:
            pass
    ok = bool(ready and mode == "RECV")
    logf.write(
        f"[HOST_EVT {time.monotonic()-t0:7.2f}s] restore_recv result ready={int(ready)} mode={mode or '-'} ok={int(ok)}\n"
    )
    logf.flush()
    return ok


def norm_name(name: str) -> str:
    return name.strip().lower()


def main() -> int:
    args = parse_args()
    target_name = args.target_name.strip()
    target_name_l = norm_name(target_name)
    target_prefix = args.target_prefix.strip() or "BS"
    target_prefix_l = target_prefix.lower()
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path(f"logs/ota_single_tag_{target_name}_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "single_shot.log"
    summary_path = out_dir / "summary.json"
    run_summary_path = out_dir.parent / "run_summary.json"

    phase_a_ok = False
    phase_b_ok = False
    target_selection_ready = False
    target_restore_ready = False
    target_transport_ready = False
    reboot_seen = False
    reconnect_ok = False
    mode_ota_loaded_after_reboot = False
    uart_ready_after_reboot = False
    restored_line_seen = False
    name_ack_seen = False
    name_pre_reboot_latched = False
    name_restored_after_reboot = False
    filter_name_pre = "-"
    filter_name_post = "-"
    filter_prefix_pre = "-"
    filter_prefix_post = "-"
    initiate_sent = False
    initiate_rc: int | None = None
    ota_started = False
    dfu_ready_seen = False
    ota_cmd_issued_seen = False
    ota_wait_fail_seen = False
    ota_gate_fail_seen = False
    ota_upload_started_seen = False
    ota_upload_progress_seen = False
    ota_upload_complete_seen = False
    ota_pending_test_seen = False
    ota_reset_request_seen = False
    ota_pending_recovery_reset_seen = False
    ota_command_sequence_seen = False
    ota_later_fail_seen = False
    ota_success_seen = False
    controller_returned_to_recv = False
    classification = "UNSET"
    blocker = ""
    serial_lost = False
    reason = ""

    t0 = time.monotonic()
    run_deadline = t0 + args.timeout_s
    ser: serial.Serial | None = None
    direct_ota_ready = False

    with log_path.open("w", encoding="utf-8") as logf:
        try:
            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] tag_ota target={target_name} prefix={target_prefix}\n")
            logf.flush()
            ser = open_serial(args.port, args.baud, force_kill_port_owner=args.force_kill_port_owner, logf=logf, t0=t0)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A serial_opened port={args.port}\n")
            logf.flush()

            boot_ready, boot_mode = wait_uart_ready(ser, logf, t0)
            if not boot_ready:
                reason = "phase_a_uart_not_ready"
                raise RuntimeError(reason)

            if boot_mode == "OTA":
                # If the previous run already staged this exact Tag target and
                # rebooted into OTA, do not bounce back to RECV.  Just prove the
                # restored target and continue to initiate.
                ota_probe_deadline = time.monotonic() + 6.0
                last_probe = 0.0
                while time.monotonic() < ota_probe_deadline:
                    now = time.monotonic()
                    if now - last_probe > 1.5:
                        send_cmd(ser, "status", logf, t0)
                        send_cmd(ser, "ota_target show", logf, t0)
                        last_probe = now
                    line = read_line(ser)
                    if line is None:
                        continue
                    logf.write(line + "\n")
                    logf.flush()
                    if "DFU SMP service ready" in line:
                        dfu_ready_seen = True
                        target_transport_ready = True
                    m_filter = FILTER_RE.search(line)
                    if m_filter:
                        filter_name_pre = m_filter.group(2).strip().lower()
                        filter_prefix_pre = m_filter.group(3).strip().lower()
                        filter_name_post = filter_name_pre
                        filter_prefix_post = filter_prefix_pre
                        logf.write(
                            f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A ota_boot_filter_readback name={filter_name_pre} prefix={filter_prefix_pre}\n"
                        )
                        logf.flush()
                        if filter_name_pre == target_name_l and filter_prefix_pre == target_prefix_l:
                            phase_a_ok = True
                            phase_b_ok = True
                            target_selection_ready = True
                            target_restore_ready = True
                            name_pre_reboot_latched = True
                            name_restored_after_reboot = True
                            mode_ota_loaded_after_reboot = True
                            uart_ready_after_reboot = True
                            restored_line_seen = True
                            break
                if phase_a_ok and phase_b_ok:
                    send_cmd(ser, "initiate", logf, t0)
                    initiate_sent = True
                    direct_ota_ready = True


                if not direct_ota_ready:
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] cleanup existing OTA mode -> RECV\n")
                    logf.flush()
                    send_cmd(ser, "mode recv", logf, t0)
                    deadline = time.monotonic() + 12.0
                    while time.monotonic() < deadline:
                        try:
                            line = read_line(ser)
                        except SerialException:
                            break
                        if line is None:
                            continue
                        logf.write(line + "\n")
                        logf.flush()
                        if "rebooting" in line.lower():
                            break
                    try:
                        ser.close()
                    except Exception:
                        pass
                    ser, boot_ready, boot_mode = wait_uart_ready_with_reconnect(args.port, args.baud, logf, t0, timeout_s=args.reconnect_timeout_s)
                    if ser is None or not boot_ready:
                        reason = "phase_a_cleanup_reconnect_timeout"
                        raise RuntimeError(reason)
    
            if not direct_ota_ready:
                system_target_tag_seen = False
                prefix_ack_seen = False
                filter_seen_pre = False

                def consume_phase_a_lines(lines: list[str]) -> None:
                    nonlocal system_target_tag_seen, prefix_ack_seen, filter_seen_pre
                    nonlocal filter_name_pre, filter_prefix_pre, name_ack_seen
                    nonlocal name_pre_reboot_latched
                    for line in lines:
                        m_sys = SYSTEM_TARGET_RE.search(line)
                        if m_sys and m_sys.group(1).strip().lower() == "tag":
                            system_target_tag_seen = True
                            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A system_target_tag\n")
                            logf.flush()

                        m_pfx = PREFIX_ACK_RE.search(line)
                        if m_pfx and int(m_pfx.group(1)) == 0 and norm_name(m_pfx.group(2)) == target_prefix_l:
                            prefix_ack_seen = True

                        m_ack = NAME_ACK_RE.search(line)
                        if m_ack and int(m_ack.group(1)) == 0 and norm_name(m_ack.group(2)) == target_name_l:
                            name_ack_seen = True
                            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A name_ack_ok\n")
                            logf.flush()

                        m_filter = FILTER_RE.search(line)
                        if m_filter:
                            filter_seen_pre = True
                            filter_name_pre = m_filter.group(2).strip().lower()
                            filter_prefix_pre = m_filter.group(3).strip().lower()
                            logf.write(
                                f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A filter_readback name={filter_name_pre} prefix={filter_prefix_pre}\n"
                            )
                            logf.flush()
                            if filter_name_pre == target_name_l and filter_prefix_pre == target_prefix_l:
                                name_pre_reboot_latched = True

                try:
                    send_cmd(ser, "status", logf, t0)
                    consume_phase_a_lines(read_lines_for(ser, logf, t0, timeout_s=0.8))

                    if boot_mode != "RECV":
                        send_cmd(ser, "mode recv", logf, t0)
                        consume_phase_a_lines(read_lines_for(ser, logf, t0, timeout_s=8.0))

                    for attempt in range(1, 5):
                        send_cmd(ser, "device kind tag", logf, t0)
                        consume_phase_a_lines(read_lines_for(ser, logf, t0, timeout_s=4.0))

                        send_cmd(ser, f"ota_target prefix {target_prefix}", logf, t0)
                        consume_phase_a_lines(read_lines_for(ser, logf, t0, timeout_s=1.5))

                        send_cmd(ser, f"ota_target name {target_name}", logf, t0)
                        consume_phase_a_lines(read_lines_for(ser, logf, t0, timeout_s=1.5))

                        send_cmd(ser, "ota_target show", logf, t0)
                        consume_phase_a_lines(read_lines_for(ser, logf, t0, timeout_s=1.0))

                        send_cmd(ser, "device show", logf, t0)
                        consume_phase_a_lines(read_lines_for(ser, logf, t0, timeout_s=1.0))

                        if system_target_tag_seen and name_pre_reboot_latched:
                            phase_a_ok = True
                            target_selection_ready = True
                            break
                        logf.write(
                            f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A retry_tag_target attempt={attempt}\n"
                        )
                        logf.flush()
                except SerialException as e:
                    serial_lost = True
                    reason = f"phase_a_serial_lost:{e}"
                    raise RuntimeError(reason)
    
                if not phase_a_ok:
                    reason = (
                        "phase_a_tag_target_not_latched "
                        f"tag={int(system_target_tag_seen)} prefix_ack={int(prefix_ack_seen)} "
                        f"name_ack={int(name_ack_seen)} filter_seen={int(filter_seen_pre)} "
                        f"filter={filter_prefix_pre}/{filter_name_pre}"
                    )
                    raise RuntimeError(reason)
    
                if args.phase_a_only:
                    classification = "PHASE_A_PASS"
                    reason = "phase_a_pass"
                    raise RuntimeError(reason)

                # b61-era broadcast Tags may keep streaming TR/TS after
                # OTA_PREPARE, which races the SMP subscribe gate.  Put the
                # selected Tag into AOTA immediately before the Master reboots
                # to OTA mode so no later RECV CFG can re-enable TDMA.  The
                # target may not be the first ready peer when several BS* Tags
                # are advertising, so keep trying until this specific command
                # is actually sent/acknowledged.
                aota_ok = False
                aota_deadline = time.monotonic() + 14.0
                send_cmd(ser, "conn", logf, t0)
                consume_phase_a_lines(read_lines_for(ser, logf, t0, timeout_s=1.0))
                while time.monotonic() < aota_deadline and not aota_ok:
                    send_cmd(ser, "cmd MODE AOTA", logf, t0)
                    for line in read_lines_for(ser, logf, t0, timeout_s=1.2):
                        consume_phase_a_lines([line])
                        if "MODE_OK MODE=AOTA" in line or "BLE cmd sent" in line:
                            aota_ok = True
                    if not aota_ok:
                        time.sleep(0.4)
                if not aota_ok:
                    logf.write(
                        f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A warn_aota_not_confirmed target={target_name}\n"
                    )
                    logf.flush()

                send_cmd(ser, "mode ota", logf, t0)
                reboot_deadline = time.monotonic() + 12.0
                while time.monotonic() < reboot_deadline:
                    try:
                        line = read_line(ser)
                    except SerialException as e:
                        reboot_seen = True
                        logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A serial_disconnect_expected err={e}\n")
                        logf.flush()
                        break
                    if line is None:
                        continue
                    logf.write(line + "\n")
                    logf.flush()
                    if "rebooting" in line.lower():
                        reboot_seen = True
                        break
                    if "mode ota (already ota) -> initiate rc=" in line.lower():
                        mode_ota_loaded_after_reboot = True
                        name_restored_after_reboot = True
                        phase_b_ok = True
                        target_restore_ready = True
                        m_init_inline = INIT_RE.search(line)
                        if m_init_inline:
                            initiate_sent = True
                            initiate_rc = int(m_init_inline.group(1))
                        break
    
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
    
                if not phase_b_ok:
                    reconnect_deadline = time.monotonic() + args.reconnect_timeout_s
                    while time.monotonic() < reconnect_deadline:
                        try:
                            ser = open_serial(args.port, args.baud)
                            reconnect_ok = True
                            ser.reset_input_buffer()
                            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=B serial_reconnected port={args.port}\n")
                            logf.flush()
                            break
                        except SerialException:
                            time.sleep(0.35)
                    if not reconnect_ok or ser is None:
                        reason = "phase_b_reconnect_timeout"
                        raise RuntimeError(reason)
    
                    phase_b_deadline = min(time.monotonic() + 60.0, run_deadline)
                    last_probe = 0.0
                    while time.monotonic() < phase_b_deadline:
                        now = time.monotonic()
                        if now - last_probe > 2.0:
                            send_cmd(ser, "status", logf, t0)
                            send_cmd(ser, "ota_target show", logf, t0)
                            last_probe = now
                        try:
                            line = read_line(ser)
                        except SerialException as e:
                            # nRF5340 CDC can enumerate before the application has
                            # finished the RECV->OTA reboot.  Treat a second
                            # brief disconnect during Phase B as part of the
                            # reboot, not as a hard failure.
                            logf.write(
                                f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=B serial_reconnect_needed err={e}\n"
                            )
                            logf.flush()
                            try:
                                ser.close()
                            except Exception:
                                pass
                            ser = None
                            while time.monotonic() < phase_b_deadline:
                                try:
                                    ser = open_serial(args.port, args.baud)
                                    ser.reset_input_buffer()
                                    reconnect_ok = True
                                    logf.write(
                                        f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=B serial_reconnected_again port={args.port}\n"
                                    )
                                    logf.flush()
                                    break
                                except SerialException:
                                    time.sleep(0.35)
                            if ser is None:
                                serial_lost = True
                                reason = f"phase_b_serial_lost:{e}"
                                raise RuntimeError(reason)
                            last_probe = 0.0
                            continue
                        if line is None:
                            continue
                        logf.write(line + "\n")
                        logf.flush()

                        if "Control mode loaded: OTA" in line:
                            mode_ota_loaded_after_reboot = True
                        if "UART control ready" in line:
                            uart_ready_after_reboot = True
                        if "OTA target restored:" in line:
                            restored_line_seen = (target_name_l in line.lower())
                        if "DFU SMP service ready" in line:
                            dfu_ready_seen = True
                            target_transport_ready = True
                        m_mode = MODE_RE.search(line) or LOADED_MODE_RE.search(line)
                        if m_mode and m_mode.group(1).upper() == "OTA":
                            mode_ota_loaded_after_reboot = True
                        m_filter = FILTER_RE.search(line)
                        if m_filter:
                            filter_name_post = m_filter.group(2).strip().lower()
                            filter_prefix_post = m_filter.group(3).strip().lower()
                            if filter_name_post == target_name_l and filter_prefix_post == target_prefix_l:
                                name_restored_after_reboot = True

                        phase_b_ok = mode_ota_loaded_after_reboot and name_restored_after_reboot
                        if phase_b_ok:
                            target_restore_ready = True
                            break

                    if not phase_b_ok:
                        reason = (
                            "phase_b_restore_not_proven "
                            f"mode={int(mode_ota_loaded_after_reboot)} uart={int(uart_ready_after_reboot)} "
                            f"restored_line={int(restored_line_seen)} filter={filter_prefix_post}/{filter_name_post}"
                        )
                        raise RuntimeError(reason)
    
            if not initiate_sent:
                send_cmd(ser, "initiate", logf, t0)
                initiate_sent = True

            while time.monotonic() < run_deadline:
                try:
                    line = read_line(ser)
                except SerialException as e:
                    serial_lost = True
                    reason = f"phase_c_serial_lost:{e}"
                    break
                if line is None:
                    continue
                logf.write(line + "\n")
                logf.flush()

                m_init = INIT_RE.search(line)
                if m_init:
                    initiate_rc = int(m_init.group(1))
                    if initiate_rc != 0:
                        if initiate_rc == -16 and not ota_pending_recovery_reset_seen:
                            logf.write(
                                f"[HOST_EVT {time.monotonic()-t0:7.2f}s] initiate_busy_auto_reset\n"
                            )
                            logf.flush()
                            ota_reset_request_seen = True
                            ota_pending_recovery_reset_seen = True
                            recovered_ser, recovered_ready, recovered_mode = recover_active_ota_session(
                                ser,
                                port=args.port,
                                baud=args.baud,
                                reconnect_timeout_s=args.reconnect_timeout_s,
                                logf=logf,
                                t0=t0,
                            )
                            ser = recovered_ser
                            reason = (
                                "ota_pending_state_recovery_reset_completed"
                                if recovered_ready
                                else "ota_pending_state_recovery_reset_failed"
                            )
                            logf.write(
                                f"[HOST_EVT {time.monotonic()-t0:7.2f}s] initiate_busy_recovery_result "
                                f"ready={int(recovered_ready)} mode={recovered_mode or '-'}\n"
                            )
                            logf.flush()
                            break
                        reason = f"initiate_rc_{initiate_rc}"
                        break

                if "DFU SMP service ready" in line:
                    dfu_ready_seen = True
                    target_transport_ready = True
                if "OTA command issued" in line:
                    ota_cmd_issued_seen = True
                if "OTA command wait failed" in line or "OTA timeout context" in line:
                    ota_wait_fail_seen = True
                if "OTA upload gate failed" in line:
                    ota_gate_fail_seen = True
                    reason = "ota_gate_failed_after_dfu_ready"
                    break
                if "OTA upload starting" in line:
                    ota_upload_started_seen = True
                    ota_started = True
                if "OTA upload progress:" in line:
                    ota_upload_progress_seen = True
                    ota_started = True
                if "OTA upload complete" in line:
                    ota_upload_complete_seen = True
                    ota_started = True
                if "OTA pending/test request" in line:
                    ota_pending_test_seen = True
                    ota_started = True
                if "OTA reset request" in line:
                    ota_reset_request_seen = True
                    ota_started = True
                if "OTA pending-state recovery reset request" in line:
                    ota_pending_recovery_reset_seen = True
                    reason = "ota_pending_state_recovery_reset"
                    break
                if "OTA upload-gate recovery reset request" in line:
                    ota_pending_recovery_reset_seen = True
                    reason = "ota_upload_gate_recovery_reset"
                    break
                if "OTA command sequence sent" in line:
                    ota_command_sequence_seen = True
                    ota_success_seen = True
                    ota_started = True
                    reason = "ota_success_observed"
                    break
                if "OTA succeeded" in line or "OTA complete" in line:
                    ota_success_seen = True
                    ota_started = True
                    reason = "ota_success_observed"
                    break
                if "OTA failed" in line or "upload failed" in line:
                    ota_later_fail_seen = True
                    reason = "ota_later_execution_failure"
                    break

            if not reason:
                reason = "phase_c_timeout_without_transport_result"

        except RuntimeError:
            pass
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass

        controller_returned_to_recv = restore_controller_to_recv(
            port=args.port,
            baud=args.baud,
            reconnect_timeout_s=args.reconnect_timeout_s,
            logf=logf,
            t0=t0,
        )

    if args.phase_a_only and phase_a_ok:
        classification = "PHASE_A_PASS"
        blocker = ""
    elif not phase_a_ok:
        classification = "A0"
        blocker = reason or "phase_a_target_selection_not_proven"
    elif not phase_b_ok:
        classification = "B0"
        blocker = reason or "phase_b_restore_not_proven"
    elif ota_started and not ota_later_fail_seen:
        classification = "D"
        blocker = ""
    elif ota_started and ota_later_fail_seen:
        classification = "E"
        blocker = "later OTA execution failure after upload started"
    elif ota_gate_fail_seen or ota_wait_fail_seen:
        classification = "C"
        blocker = reason or "ota transport timeout/gate failure"
    else:
        classification = "E"
        blocker = reason or "uncategorized transport execution result"

    result = RunResult(
        port=args.port,
        target_name=target_name,
        target_prefix=target_prefix,
        target_selection_ready=target_selection_ready,
        target_restore_ready=target_restore_ready,
        target_transport_ready=target_transport_ready,
        phase_a_ok=phase_a_ok,
        phase_b_ok=phase_b_ok,
        reboot_seen=reboot_seen,
        reconnect_ok=reconnect_ok,
        mode_ota_loaded_after_reboot=mode_ota_loaded_after_reboot,
        uart_ready_after_reboot=uart_ready_after_reboot,
        restored_line_seen=restored_line_seen,
        name_ack_seen=name_ack_seen,
        name_pre_reboot_latched=name_pre_reboot_latched,
        name_restored_after_reboot=name_restored_after_reboot,
        filter_name_pre=filter_name_pre,
        filter_name_post=filter_name_post,
        filter_prefix_pre=filter_prefix_pre,
        filter_prefix_post=filter_prefix_post,
        initiate_sent=initiate_sent,
        initiate_rc=initiate_rc,
        ota_started=ota_started,
        dfu_ready_seen=dfu_ready_seen,
        ota_cmd_issued_seen=ota_cmd_issued_seen,
        ota_wait_fail_seen=ota_wait_fail_seen,
        ota_gate_fail_seen=ota_gate_fail_seen,
        ota_upload_started_seen=ota_upload_started_seen,
        ota_upload_progress_seen=ota_upload_progress_seen,
        ota_upload_complete_seen=ota_upload_complete_seen,
        ota_pending_test_seen=ota_pending_test_seen,
        ota_reset_request_seen=ota_reset_request_seen,
        ota_pending_recovery_reset_seen=ota_pending_recovery_reset_seen,
        ota_command_sequence_seen=ota_command_sequence_seen,
        ota_later_fail_seen=ota_later_fail_seen,
        ota_success_seen=ota_success_seen,
        controller_returned_to_recv=controller_returned_to_recv,
        classification=classification,
        blocker=blocker,
        serial_lost=serial_lost,
        reason=reason,
        log_path=str(log_path),
    )
    summary = asdict(result)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    run_summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))

    if classification == "PHASE_A_PASS":
        return 0
    if ota_success_seen:
        return 0
    if not name_pre_reboot_latched:
        return 10
    if not name_restored_after_reboot:
        return 11
    if not initiate_sent:
        return 12
    if initiate_rc is not None and initiate_rc != 0:
        return 13
    if ota_gate_fail_seen:
        return 15
    if not ota_started:
        return 14
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
