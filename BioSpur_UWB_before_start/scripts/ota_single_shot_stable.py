#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import serial
from serial import SerialException


FILTER_RE = re.compile(
    r"OTA target filter:\s*token=([-\d]+)\s+name=([^\s]+)\s+prefix=([^\s]+)\s+uuid=([0-9A-F\-]+|-)",
    re.IGNORECASE,
)
UUID_ACK_RE = re.compile(r"ota_target uuid rc=([-\d]+)\s+value=([0-9A-F\-]+|-)", re.IGNORECASE)
MODE_RE = re.compile(r"Control status:\s*mode=([A-Z]+)")
INIT_RE = re.compile(r"initiate rc=([-\d]+)")
SYSTEM_TARGET_RE = re.compile(r"System target:\s*kind=([a-zA-Z]+)")
NUS_STAGE_RE = re.compile(r"OTA NUS stage:\s*(enabled|disabled)", re.IGNORECASE)


def classify_phase_a_reason(
    *,
    uuid_ack_seen: bool,
    filter_seen: bool,
    filter_uuid: str,
    serial_lost: bool,
    target_uuid: str,
) -> str:
    if serial_lost:
        return "phase_a_serial_instability"
    if not uuid_ack_seen and not filter_seen:
        return "phase_a_no_ack_no_filter"
    if not uuid_ack_seen:
        return "phase_a_no_ack"
    if not filter_seen:
        return "phase_a_no_filter_readback"
    if filter_uuid != target_uuid:
        return f"phase_a_wrong_uuid_in_filter:{filter_uuid}"
    return "phase_a_uuid_not_latched"


@dataclass
class RunResult:
    port: str
    target_uuid: str
    phase_a_ok: bool
    phase_b_ok: bool
    reboot_seen: bool
    reconnect_ok: bool
    mode_ota_loaded_after_reboot: bool
    uart_ready_after_reboot: bool
    restored_line_seen: bool
    uuid_ack_seen: bool
    uuid_pre_reboot_latched: bool
    uuid_restored_after_reboot: bool
    filter_uuid_pre: str
    filter_uuid_post: str
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
    ota_command_sequence_seen: bool
    ota_later_fail_seen: bool
    ota_success_seen: bool
    target_observability_available: bool
    anchor_lines: int
    anchor_bt_rx_seen: bool
    anchor_bt_drop_seen: bool
    anchor_ingress_seen: bool
    anchor_done_seen: bool
    anchor_notify_seen: bool
    classification: str
    blocker: str
    serial_lost: bool
    reason: str
    log_path: str
    anchor_log_path: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Reboot-aware strict UUID OTA launcher with hard gates.")
    p.add_argument("--port", required=True)
    p.add_argument("--target-uuid", required=True)
    p.add_argument("--out-dir", default="")
    p.add_argument("--timeout-s", type=float, default=180.0)
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--reconnect-timeout-s", type=float, default=45.0)
    p.add_argument(
        "--anchor-port",
        default="/dev/serial/by-id/usb-SEGGER_J-Link_000760186071-if00",
        help="Anchor A diagnostic serial port (required for observability preflight/classification).",
    )
    p.add_argument(
        "--anchor-map-json",
        default="logs/anchor_diag_map.json",
        help="Resolved anchor diag map JSON (from resolve_anchor_diag_port.py).",
    )
    p.add_argument("--anchor-baud", type=int, default=115200)
    p.add_argument(
        "--anchor-preflight-timeout-s",
        type=float,
        default=10.0,
        help="Seconds to wait for non-empty Anchor diagnostic output before OTA starts.",
    )
    p.add_argument(
        "--anchor-reset-preflight",
        action="store_true",
        help="Reset anchor by explicit SN during preflight to trigger deterministic boot logs.",
    )
    p.add_argument(
        "--phase-a-only",
        action="store_true",
        help="Stop after proving pre-reboot UUID latch; do not send mode ota/initiate.",
    )
    return p.parse_args()


def open_serial(port: str, baud: int) -> serial.Serial:
    return serial.Serial(
        port,
        baud,
        timeout=0.2,
        exclusive=True,
        dsrdtr=False,
        rtscts=False,
    )


def send_cmd(ser: serial.Serial, cmd: str, logf, t0: float) -> None:
    ser.write((cmd + "\n").encode("utf-8"))
    logf.write(f"[HOST_CMD {time.monotonic()-t0:7.2f}s] {cmd}\n")
    logf.flush()


def read_line(ser: serial.Serial) -> str | None:
    try:
        raw = ser.readline()
    except SerialException:
        raise
    if not raw:
        return None
    return raw.decode("utf-8", errors="replace").rstrip("\r\n")


def parse_anchor_snr_from_port(port: str) -> str | None:
    m = re.search(r"usb-SEGGER_J-Link_0*([0-9]{9})-if", port)
    if m:
        return m.group(1)
    return None


def lsof_port_users(port: str) -> list[str]:
    try:
        cp = subprocess.run(
            ["lsof", port],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return []
    if cp.returncode != 0:
        return []
    lines = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
    return lines[1:] if len(lines) > 1 else []


def reset_via_jlink(snr: str) -> None:
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


class AnchorCapture:
    def __init__(self, port: str, baud: int, log_path: Path):
        self.port = port
        self.baud = baud
        self.log_path = log_path
        self.lines = 0
        self.bt_rx_seen = False
        self.bt_drop_seen = False
        self.ingress_seen = False
        self.done_seen = False
        self.notify_seen = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ser: serial.Serial | None = None
        self._fh = None
        self.start_error = ""

    def start(self) -> None:
        self._fh = self.log_path.open("w", encoding="utf-8")
        self._ser = serial.Serial(
            self.port,
            self.baud,
            timeout=0.2,
            exclusive=True,
            dsrdtr=False,
            rtscts=False,
        )
        self._ser.reset_input_buffer()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        assert self._ser is not None
        assert self._fh is not None
        while not self._stop.is_set():
            try:
                raw = self._ser.readline()
            except Exception as e:
                self.start_error = str(e)
                break
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            self._fh.write(line + "\n")
            self._fh.flush()
            self.lines += 1
            if "ANCHOR_SMP_BT_RX" in line:
                self.bt_rx_seen = True
            if "ANCHOR_SMP_BT_DROP" in line:
                self.bt_drop_seen = True
            if "ANCHOR_SMP_INGRESS" in line:
                self.ingress_seen = True
            if "ANCHOR_SMP_DONE" in line:
                self.done_seen = True
                if "rsp_generated=1" in line or "notify_send_rc=0" in line:
                    self.notify_seen = True

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        if self._fh is not None:
            self._fh.close()

    def wait_for_lines(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.lines > 0:
                return True
            time.sleep(0.1)
        return False


def main() -> int:
    args = parse_args()
    target_uuid = args.target_uuid.strip().upper()
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path(f"logs/ota_single_shot_stable_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "single_shot.log"
    summary_path = out_dir / "summary.json"
    run_summary_path = out_dir.parent / "run_summary.json"
    anchor_log_path = out_dir.parent / "anchorA_diag.log"
    anchor_port = args.anchor_port
    map_path = Path(args.anchor_map_json)
    if map_path.exists():
        try:
            mapping = json.loads(map_path.read_text(encoding="utf-8"))
            if mapping.get("resolved") and mapping.get("port"):
                anchor_port = str(mapping["port"])
        except Exception:
            pass

    phase_a_ok = False
    phase_b_ok = False
    reboot_seen = False
    reconnect_ok = False
    mode_ota_loaded_after_reboot = False
    uart_ready_after_reboot = False
    restored_line_seen = False
    uuid_ack_seen = False
    uuid_pre_reboot_latched = False
    uuid_restored_after_reboot = False
    filter_uuid_pre = "-"
    filter_uuid_post = "-"
    filter_seen_pre = False
    system_target_anchor_seen = False
    ota_nus_disabled_seen = False
    anchor_ready_uuid_sent = False
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
    ota_command_sequence_seen = False
    ota_later_fail_seen = False
    ota_success_seen = False
    target_observability_available = False
    anchor_lines = 0
    anchor_bt_rx_seen = False
    anchor_bt_drop_seen = False
    anchor_ingress_seen = False
    anchor_done_seen = False
    anchor_notify_seen = False
    classification = "UNSET"
    blocker = ""
    serial_lost = False
    reason = ""

    t0 = time.monotonic()
    run_deadline = t0 + args.timeout_s
    ser: serial.Serial | None = None
    anchor_cap: AnchorCapture | None = None

    with log_path.open("w", encoding="utf-8") as logf:
        try:
            # Observability preflight
            if args.phase_a_only:
                target_observability_available = True
                logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A only; anchor preflight skipped\n")
                logf.flush()
            else:
                port_users = lsof_port_users(anchor_port)
                if port_users:
                    reason = "target_observability_unavailable:anchor_port_busy"
                    blocker = reason
                    logf.write(
                        f"[HOST_EVT {time.monotonic()-t0:7.2f}s] anchor_port_busy port={anchor_port} users={len(port_users)}\n"
                    )
                    for ln in port_users[:6]:
                        logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] lsof {ln}\n")
                    logf.flush()
                    raise RuntimeError(reason)

                anchor_cap = AnchorCapture(anchor_port, args.anchor_baud, anchor_log_path)
                anchor_cap.start()
                logf.write(
                    f"[HOST_EVT {time.monotonic()-t0:7.2f}s] anchor_capture_started port={anchor_port} log={anchor_log_path}\n"
                )
                logf.flush()

                if args.anchor_reset_preflight:
                    anchor_snr = parse_anchor_snr_from_port(anchor_port)
                    if anchor_snr:
                        reset_via_jlink(anchor_snr)
                        logf.write(
                            f"[HOST_EVT {time.monotonic()-t0:7.2f}s] anchor_reset_preflight method=jlink snr={anchor_snr}\n"
                        )
                        logf.flush()

                if not anchor_cap.wait_for_lines(args.anchor_preflight_timeout_s):
                    reason = "target_observability_unavailable:anchor_lines_zero"
                    blocker = "target observability unavailable"
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] {reason}\n")
                    logf.flush()
                    raise RuntimeError(reason)

                target_observability_available = True

            # Phase A: pre-reboot control session
            ser = open_serial(args.port, args.baud)
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A serial_opened port={args.port}\n")
            logf.flush()

            # Wait until control UART is actually ready; commands sent before
            # this line can be silently dropped during boot/scan startup.
            boot_ready = False
            boot_ready_deadline = time.monotonic() + 25.0
            last_probe = 0.0
            while time.monotonic() < boot_ready_deadline:
                now = time.monotonic()
                if now - last_probe > 2.0:
                    send_cmd(ser, "status", logf, t0)
                    last_probe = now
                line = read_line(ser)
                if line is None:
                    continue
                logf.write(line + "\n")
                logf.flush()
                if (
                    "UART control ready" in line or
                    "Control status:" in line or
                    "OTA target filter:" in line
                ):
                    boot_ready = True
                    break
            if not boot_ready:
                reason = "phase_a_uart_not_ready"
                raise RuntimeError(reason)

            send_cmd(ser, "status", logf, t0)
            send_cmd(ser, "device kind anchor", logf, t0)
            send_cmd(ser, "device show", logf, t0)
            send_cmd(ser, "status", logf, t0)

            phase_a_deadline = time.monotonic() + 25.0
            next_anchor_retry = time.monotonic() + 2.0
            next_uuid_retry = time.monotonic() + 2.5
            next_show_retry = time.monotonic() + 1.5
            while time.monotonic() < phase_a_deadline:
                now = time.monotonic()
                if (not system_target_anchor_seen or not ota_nus_disabled_seen) and now >= next_anchor_retry:
                    send_cmd(ser, "device kind anchor", logf, t0)
                    send_cmd(ser, "device show", logf, t0)
                    send_cmd(ser, "status", logf, t0)
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A retry_anchor_kind\n")
                    logf.flush()
                    next_anchor_retry = now + 2.0
                if system_target_anchor_seen and ota_nus_disabled_seen and not uuid_ack_seen and now >= next_uuid_retry:
                    send_cmd(ser, f"ota_target uuid {target_uuid}", logf, t0)
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A retry_uuid_write\n")
                    logf.flush()
                    next_uuid_retry = now + 2.5
                if system_target_anchor_seen and ota_nus_disabled_seen and ((not filter_seen_pre) or filter_uuid_pre != target_uuid) and now >= next_show_retry:
                    send_cmd(ser, "ota_target show", logf, t0)
                    send_cmd(ser, "status", logf, t0)
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A retry_filter_readback\n")
                    logf.flush()
                    next_show_retry = now + 1.5

                try:
                    line = read_line(ser)
                except SerialException as e:
                    serial_lost = True
                    reason = f"phase_a_serial_lost:{e}"
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] {reason}\n")
                    logf.flush()
                    raise RuntimeError(reason)

                if line is None:
                    continue
                logf.write(line + "\n")
                logf.flush()

                m_sys = SYSTEM_TARGET_RE.search(line)
                if m_sys and m_sys.group(1).strip().lower() == "anchor":
                    system_target_anchor_seen = True
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A system_target_anchor\n")
                    logf.flush()

                m_nus = NUS_STAGE_RE.search(line)
                if m_nus and m_nus.group(1).strip().lower() == "disabled":
                    ota_nus_disabled_seen = True
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A ota_nus_disabled\n")
                    logf.flush()

                if system_target_anchor_seen and ota_nus_disabled_seen and not anchor_ready_uuid_sent:
                    send_cmd(ser, f"ota_target uuid {target_uuid}", logf, t0)
                    send_cmd(ser, "ota_target show", logf, t0)
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A anchor_ready_uuid_write\n")
                    logf.flush()
                    anchor_ready_uuid_sent = True
                    next_uuid_retry = time.monotonic() + 2.5
                    next_show_retry = time.monotonic() + 1.5

                m_ack = UUID_ACK_RE.search(line)
                if m_ack and int(m_ack.group(1)) == 0 and m_ack.group(2).upper() == target_uuid:
                    uuid_ack_seen = True
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A uuid_ack_ok\n")
                    logf.flush()

                m_filter = FILTER_RE.search(line)
                if m_filter:
                    filter_seen_pre = True
                    filter_uuid_pre = m_filter.group(4).strip().upper()
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A filter_readback uuid={filter_uuid_pre}\n")
                    logf.flush()
                    if filter_uuid_pre == target_uuid:
                        uuid_pre_reboot_latched = uuid_ack_seen

                if uuid_pre_reboot_latched and system_target_anchor_seen and ota_nus_disabled_seen:
                    phase_a_ok = True
                    break

            if not phase_a_ok:
                if not system_target_anchor_seen:
                    reason = "phase_a_system_target_not_anchor"
                    raise RuntimeError(reason)
                if not ota_nus_disabled_seen:
                    reason = "phase_a_ota_nus_not_disabled"
                    raise RuntimeError(reason)
                base_reason = classify_phase_a_reason(
                    uuid_ack_seen=uuid_ack_seen,
                    filter_seen=filter_seen_pre,
                    filter_uuid=filter_uuid_pre,
                    serial_lost=serial_lost,
                    target_uuid=target_uuid,
                )
                reason = f"{base_reason} ack={int(uuid_ack_seen)} filter={filter_uuid_pre}"
                raise RuntimeError(reason)

            if args.phase_a_only:
                reason = "phase_a_pass"
                classification = "PHASE_A_PASS"
                blocker = ""
                raise RuntimeError(reason)

            send_cmd(ser, "mode ota", logf, t0)

            # Expect disconnect due to warm reboot
            phase_reboot_deadline = time.monotonic() + 12.0
            while time.monotonic() < phase_reboot_deadline:
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

            try:
                ser.close()
            except Exception:
                pass
            ser = None

            # Phase B: reconnect after reboot and prove restore
            reconnect_deadline = time.monotonic() + args.reconnect_timeout_s
            while time.monotonic() < reconnect_deadline:
                try:
                    ser = open_serial(args.port, args.baud)
                    reconnect_ok = True
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=B serial_reconnected port={args.port}\n")
                    logf.flush()
                    ser.reset_input_buffer()
                    break
                except SerialException:
                    time.sleep(0.35)

            if not reconnect_ok or ser is None:
                reason = "phase_b_reconnect_timeout"
                raise RuntimeError(reason)

            phase_b_deadline = min(time.monotonic() + 60.0, run_deadline)
            last_status_tx = 0.0
            while time.monotonic() < phase_b_deadline:
                now = time.monotonic()
                if now - last_status_tx > 2.0:
                    send_cmd(ser, "status", logf, t0)
                    send_cmd(ser, "ota_target show", logf, t0)
                    last_status_tx = now

                try:
                    line = read_line(ser)
                except SerialException as e:
                    serial_lost = True
                    reason = f"phase_b_serial_lost:{e}"
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] {reason}\n")
                    logf.flush()
                    raise RuntimeError(reason)

                if line is None:
                    continue

                logf.write(line + "\n")
                logf.flush()

                if "Control mode loaded: OTA" in line:
                    mode_ota_loaded_after_reboot = True
                if "UART control ready" in line:
                    uart_ready_after_reboot = True
                if "OTA target restored:" in line:
                    restored_line_seen = (f"UUID={target_uuid}" in line.upper())

                m_mode = MODE_RE.search(line)
                if m_mode and m_mode.group(1).upper() == "OTA":
                    mode_ota_loaded_after_reboot = True

                m_filter = FILTER_RE.search(line)
                if m_filter:
                    filter_uuid_post = m_filter.group(4).strip().upper()
                    if filter_uuid_post == target_uuid:
                        uuid_restored_after_reboot = True

                phase_b_ok = (
                    mode_ota_loaded_after_reboot and
                    uart_ready_after_reboot and
                    restored_line_seen and
                    uuid_restored_after_reboot
                )
                if phase_b_ok:
                    break

            if not phase_b_ok:
                reason = (
                    "phase_b_restore_not_proven "
                    f"mode={int(mode_ota_loaded_after_reboot)} "
                    f"uart={int(uart_ready_after_reboot)} "
                    f"restored={int(restored_line_seen)} "
                    f"filter={filter_uuid_post}"
                )
                raise RuntimeError(reason)

            # Phase C: initiate only after restore proof
            send_cmd(ser, "initiate", logf, t0)
            initiate_sent = True

            phase_c_deadline = run_deadline
            while time.monotonic() < phase_c_deadline:
                try:
                    line = read_line(ser)
                except SerialException as e:
                    serial_lost = True
                    reason = f"phase_c_serial_lost:{e}"
                    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] {reason}\n")
                    logf.flush()
                    break

                if line is None:
                    continue
                logf.write(line + "\n")
                logf.flush()

                m_init = INIT_RE.search(line)
                if m_init:
                    initiate_rc = int(m_init.group(1))
                    if initiate_rc != 0:
                        reason = f"initiate_rc_{initiate_rc}"
                        break

                if "DFU SMP service ready" in line:
                    dfu_ready_seen = True

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
            if anchor_cap is not None:
                anchor_cap.stop()
                anchor_lines = anchor_cap.lines
                anchor_bt_rx_seen = anchor_cap.bt_rx_seen
                anchor_bt_drop_seen = anchor_cap.bt_drop_seen
                anchor_ingress_seen = anchor_cap.ingress_seen
                anchor_done_seen = anchor_cap.done_seen
                anchor_notify_seen = anchor_cap.notify_seen
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass

    if not target_observability_available:
        classification = "OBS_UNAVAILABLE"
        blocker = blocker or "target observability unavailable"
    elif args.phase_a_only and phase_a_ok:
        classification = "PHASE_A_PASS"
        blocker = ""
    elif ota_started and not ota_later_fail_seen:
        classification = "D"
        blocker = ""
    elif ota_started and ota_later_fail_seen:
        classification = "E"
        blocker = "later OTA execution failure after upload started"
    elif ota_gate_fail_seen or ota_wait_fail_seen:
        if not anchor_bt_rx_seen:
            classification = "A1"
            blocker = "request did not reach anchor BLE SMP transport"
        elif anchor_bt_rx_seen and not anchor_ingress_seen:
            classification = "A2"
            blocker = "request reached BLE SMP transport but was dropped before mgmt ingress"
        elif anchor_ingress_seen and not anchor_done_seen:
            classification = "B"
            blocker = "anchor handler path did not complete"
        else:
            classification = "C"
            blocker = "anchor completed request but host receive/delivery timed out"
    else:
        classification = "E"
        blocker = reason or "uncategorized transport execution result"

    result = RunResult(
        port=args.port,
        target_uuid=target_uuid,
        phase_a_ok=phase_a_ok,
        phase_b_ok=phase_b_ok,
        reboot_seen=reboot_seen,
        reconnect_ok=reconnect_ok,
        mode_ota_loaded_after_reboot=mode_ota_loaded_after_reboot,
        uart_ready_after_reboot=uart_ready_after_reboot,
        restored_line_seen=restored_line_seen,
        uuid_ack_seen=uuid_ack_seen,
        uuid_pre_reboot_latched=uuid_pre_reboot_latched,
        uuid_restored_after_reboot=uuid_restored_after_reboot,
        filter_uuid_pre=filter_uuid_pre,
        filter_uuid_post=filter_uuid_post,
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
        ota_command_sequence_seen=ota_command_sequence_seen,
        ota_later_fail_seen=ota_later_fail_seen,
        ota_success_seen=ota_success_seen,
        target_observability_available=target_observability_available,
        anchor_lines=anchor_lines,
        anchor_bt_rx_seen=anchor_bt_rx_seen,
        anchor_bt_drop_seen=anchor_bt_drop_seen,
        anchor_ingress_seen=anchor_ingress_seen,
        anchor_done_seen=anchor_done_seen,
        anchor_notify_seen=anchor_notify_seen,
        classification=classification,
        blocker=blocker,
        serial_lost=serial_lost,
        reason=reason,
        log_path=str(log_path),
        anchor_log_path=str(anchor_log_path),
    )
    summary_path.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    run_summary_path.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(asdict(result), indent=2))

    if classification == "PHASE_A_PASS":
        return 0
    if classification == "OBS_UNAVAILABLE":
        return 20
    if not uuid_pre_reboot_latched:
        return 10
    if not uuid_restored_after_reboot:
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
