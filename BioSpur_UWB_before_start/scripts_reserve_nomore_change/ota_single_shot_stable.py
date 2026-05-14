#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
import time
import os
import signal
from dataclasses import dataclass, asdict
from pathlib import Path

import serial
from serial import SerialException, SerialTimeoutException


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
    ota_pending_recovery_reset_seen: bool
    ota_command_sequence_seen: bool
    ota_later_fail_seen: bool
    ota_success_seen: bool
    # Legacy field name kept for summary/backward compatibility.
    # In strict mode this no longer means "anchor-side logs were visible".
    # It now only means strict mode was allowed to proceed without forbidden
    # Anchor USB/RTT observability gating.
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
        "--force-kill-port-owner",
        action="store_true",
        help=(
            "If the master_control CDC port is exclusively locked by another process, "
            "try to identify the owning PID(s) (via fuser) and terminate them, then retry. "
            "DANGEROUS: only use when you are sure no other critical session is using the port."
        ),
    )
    p.add_argument(
        "--anchor-port",
        default="/dev/serial/by-id/usb-SEGGER_J-Link_000760184781-if00",
        help="Deprecated debug-only anchor observability port. Strict mode ignores Anchor USB data paths.",
    )
    p.add_argument(
        "--anchor-map-json",
        default="logs/anchor_diag_map.json",
        help="Deprecated debug-only anchor observability map. Strict mode does not use it for hard gating.",
    )
    p.add_argument(
        "--anchor-observability-backend",
        choices=["auto", "serial", "rtt"],
        default="auto",
        help="Deprecated debug-only anchor observability backend. Strict mode ignores Anchor USB/RTT backends.",
    )
    p.add_argument("--anchor-baud", type=int, default=115200)
    p.add_argument("--anchor-rtt-device", default="nRF52832_XXAA")
    p.add_argument("--anchor-rtt-if", default="SWD")
    p.add_argument("--anchor-rtt-speed", default="4000")
    p.add_argument("--anchor-rtt-channel", default="0")
    p.add_argument(
        "--anchor-preflight-timeout-s",
        type=float,
        default=10.0,
        help="Deprecated debug-only timeout. Strict mode does not wait on Anchor USB/RTT output.",
    )
    p.add_argument(
        "--anchor-reset-preflight",
        action="store_true",
        help="Deprecated debug-only option. Strict mode never resets anchors via direct debug access.",
    )
    p.add_argument(
        "--phase-a-only",
        action="store_true",
        help="Stop after proving pre-reboot UUID latch; do not send mode ota/initiate.",
    )
    p.add_argument(
        "--pre-reset",
        action="store_true",
        help="After entering OTA mode and reaching DFU-ready, issue ota_reset once, then stop so a second run can perform the real upload.",
    )
    return p.parse_args()


def _is_exclusive_lock_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    if "exclusively lock port" in msg:
        return True
    if isinstance(exc, SerialException) and getattr(exc, "errno", None) == 11:
        return True
    return False


def _force_kill_port_owners(port: str, logf, t0: float) -> bool:
    # Resolve by-id symlink to the actual /dev/ttyACM* path so fuser works.
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
    pids = sorted({int(x) for x in re.findall(r"\\b(\\d+)\\b", text)})
    pids = [pid for pid in pids if pid not in (0, os.getpid())]
    if not pids:
        logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] serial_lock force_kill none_found dev={dev}\n")
        logf.flush()
        return False

    for pid in pids:
        try:
            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] serial_lock force_kill term pid={pid}\n")
            logf.flush()
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError:
            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] serial_lock force_kill permission_denied pid={pid}\n")
            logf.flush()

    # Give processes a moment to exit gracefully, then SIGKILL any survivors.
    time.sleep(0.8)
    survivors = []
    for pid in pids:
        try:
            os.kill(pid, 0)
            survivors.append(pid)
        except Exception:
            pass
    for pid in survivors:
        try:
            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] serial_lock force_kill kill pid={pid}\n")
            logf.flush()
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

    time.sleep(0.4)
    logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] serial_lock force_kill done pids={pids}\n")
    logf.flush()
    return True


def open_serial(port: str, baud: int, *, force_kill_port_owner: bool = False, logf=None, t0: float = 0.0) -> serial.Serial:
    """
    Open the master_control CDC port robustly.

    After SWD flashing or mode-switch reboots the USB CDC device can briefly
    disappear and re-enumerate. Keep a short retry window so higher-level OTA
    runners don't fail immediately on transient FileNotFoundError.
    """
    deadline = time.monotonic() + 30.0
    last_exc: Exception | None = None
    kill_attempted = False
    while time.monotonic() < deadline:
        try:
            return serial.Serial(
                port,
                baud,
                timeout=0.2,
                write_timeout=2.0,
                exclusive=False,
                dsrdtr=False,
                rtscts=False,
            )
        except Exception as exc:
            last_exc = exc
            if force_kill_port_owner and (not kill_attempted) and _is_exclusive_lock_error(exc) and logf is not None:
                kill_attempted = True
                _force_kill_port_owners(port, logf, t0)
            time.sleep(0.4)
    assert last_exc is not None
    raise last_exc


def send_cmd(ser: serial.Serial, cmd: str, logf, t0: float) -> None:
    payload = (cmd + "\n").encode("utf-8")
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            ser.write(payload)
            ser.flush()
            logf.write(f"[HOST_CMD {time.monotonic()-t0:7.2f}s] {cmd}\n")
            logf.flush()
            return
        except (SerialTimeoutException, SerialException, OSError) as exc:
            last_exc = exc
            logf.write(
                f"[HOST_EVT {time.monotonic()-t0:7.2f}s] serial_write_failed "
                f"attempt={attempt}/3 cmd={cmd!r} err={exc}\n"
            )
            logf.flush()
            try:
                ser.reset_output_buffer()
                ser.reset_input_buffer()
            except Exception:
                pass
            time.sleep(0.35)
    assert last_exc is not None
    raise RuntimeError(f"serial_write_failed:{cmd}:{last_exc}") from last_exc


def read_line(ser: serial.Serial) -> str | None:
    try:
        raw = ser.readline()
    except SerialException:
        raise
    if not raw:
        return None
    return raw.decode("utf-8", errors="replace").rstrip("\r\n")


def wait_uart_ready(
    ser: serial.Serial,
    logf,
    t0: float,
    *,
    probe_status: bool = True,
    timeout_s: float = 25.0,
) -> tuple[bool, str]:
    boot_ready = False
    boot_mode = ""
    boot_ready_deadline = time.monotonic() + timeout_s
    last_probe = 0.0
    while time.monotonic() < boot_ready_deadline:
        now = time.monotonic()
        if probe_status and now - last_probe > 2.0:
            send_cmd(ser, "status", logf, t0)
            last_probe = now
        line = read_line(ser)
        if line is None:
            continue
        logf.write(line + "\n")
        logf.flush()
        m_mode = MODE_RE.search(line)
        if m_mode:
            boot_mode = m_mode.group(1).upper()
        if (
            "UART control ready" in line or
            "Control status:" in line or
            "OTA target filter:" in line
        ):
            boot_ready = True
            break
    return boot_ready, boot_mode


def reconnect_after_mode_switch(port: str, baud: int, timeout_s: float) -> serial.Serial | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            ser = open_serial(port, baud)
            ser.reset_input_buffer()
            return ser
        except SerialException:
            time.sleep(0.35)
    return None


def wait_uart_ready_with_reconnect(
    port: str,
    baud: int,
    logf,
    t0: float,
    *,
    timeout_s: float,
    probe_status: bool = True,
) -> tuple[serial.Serial | None, bool, str]:
    deadline = time.monotonic() + timeout_s
    ser: serial.Serial | None = None
    while time.monotonic() < deadline:
        if ser is None:
            ser = reconnect_after_mode_switch(port, baud, min(2.0, max(0.1, deadline - time.monotonic())))
            if ser is None:
                continue
            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A cleanup serial_reconnected port={port}\n")
            logf.flush()
        try:
            boot_ready, boot_mode = wait_uart_ready(
                ser,
                logf,
                t0,
                probe_status=probe_status,
                timeout_s=min(6.0, max(0.5, deadline - time.monotonic())),
            )
            if boot_ready:
                return ser, True, boot_mode
        except SerialException:
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            time.sleep(0.25)
            continue
    if ser is not None:
        try:
            ser.close()
        except Exception:
            pass
    return None, False, ""


def parse_anchor_snr_from_port(port: str) -> str | None:
    m = re.search(r"usb-SEGGER_J-Link_0*([0-9]{9})-if", port)
    if m:
        return m.group(1)
    return None


def resolve_anchor_port_from_mapping(mapping: object, target_uuid: str) -> str | None:
    entry = resolve_anchor_mapping_entry(mapping, target_uuid)
    if isinstance(entry, dict):
        port = entry.get("port")
        if isinstance(port, str) and port:
            return port
    return None


def resolve_anchor_mapping_entry(mapping: object, target_uuid: str) -> dict | None:
    if not isinstance(mapping, dict):
        return None

    by_uuid = mapping.get("by_uuid")
    if isinstance(by_uuid, dict):
        entry = by_uuid.get(target_uuid.upper())
        if isinstance(entry, dict):
            return entry

    entry = mapping.get(target_uuid.upper())
    if isinstance(entry, dict):
        return entry

    if mapping.get("resolved") and mapping.get("port"):
        return mapping

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


def reset_via_nrfjprog(snr: str) -> tuple[bool, str]:
    try:
        cp = subprocess.run(
            ["nrfjprog", "--snr", snr, "--reset", "-f", "NRF52"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False, "nrfjprog_not_found"
    details = (cp.stdout or "") + (cp.stderr or "")
    return cp.returncode == 0, details.strip()


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


class RTTCapture:
    def __init__(self, snr: str, log_path: Path, *, device: str, interface: str, speed: str, channel: str):
        self.snr = snr
        self.log_path = log_path
        self.device = device
        self.interface = interface
        self.speed = speed
        self.channel = channel
        self.lines = 0
        self.bt_rx_seen = False
        self.bt_drop_seen = False
        self.ingress_seen = False
        self.done_seen = False
        self.notify_seen = False
        self.start_error = ""
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._offset = 0

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")
        stderr_path = self.log_path.with_suffix(self.log_path.suffix + ".rtt.stderr")
        err_fh = stderr_path.open("w", encoding="utf-8")
        self._proc = subprocess.Popen(
            [
                "JLinkRTTLogger",
                "-Device", self.device,
                "-If", self.interface,
                "-Speed", self.speed,
                "-USB", self.snr,
                "-RTTChannel", self.channel,
                str(self.log_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=err_fh,
            text=True,
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _mark_line(self, line: str) -> None:
        self.lines += 1
        if "ANCHOR_SMP_BT_RX" in line:
            self.bt_rx_seen = True
        if "ANCHOR_SMP_BT_DROP" in line:
            self.bt_drop_seen = True
        if "ANCHOR_SMP_INGRESS" in line:
            self.ingress_seen = True
        if "ANCHOR_SMP_DONE" in line:
            self.done_seen = True
            if "rsp_generated=1" in line or "notify_send_rc=0" in line or "notify_send_rc=unknown" in line:
                self.notify_seen = True

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._proc is not None and self._proc.poll() is not None and not self.log_path.exists():
                self.start_error = f"JLinkRTTLogger exited rc={self._proc.returncode}"
                break
            try:
                if self.log_path.exists():
                    with self.log_path.open("r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(self._offset)
                        chunk = fh.read()
                        self._offset = fh.tell()
                else:
                    chunk = ""
            except Exception as e:
                self.start_error = str(e)
                break
            if chunk:
                for line in chunk.splitlines():
                    if line:
                        self._mark_line(line)
            time.sleep(0.1)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=1.0)

    def wait_for_lines(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.lines > 0:
                return True
            if self.start_error:
                return False
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
    anchor_snr = parse_anchor_snr_from_port(anchor_port)
    anchor_backend = args.anchor_observability_backend
    map_path = Path(args.anchor_map_json)
    mapping_entry = None
    if map_path.exists():
        try:
            mapping = json.loads(map_path.read_text(encoding="utf-8"))
            mapping_entry = resolve_anchor_mapping_entry(mapping, target_uuid)
            mapped_port = resolve_anchor_port_from_mapping(mapping, target_uuid)
            if mapped_port:
                anchor_port = mapped_port
                anchor_snr = parse_anchor_snr_from_port(anchor_port)
            if isinstance(mapping_entry, dict):
                snr_from_map = mapping_entry.get("snr")
                if isinstance(snr_from_map, str) and snr_from_map:
                    anchor_snr = snr_from_map
        except Exception:
            pass
    if anchor_backend == "auto":
        anchor_backend = "rtt" if anchor_snr else "serial"

    phase_a_ok = False
    phase_b_ok = False
    phase_b_skipped_already_ota = False
    target_selection_ready = False
    target_restore_ready = False
    target_transport_ready = False
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
    ota_pending_recovery_reset_seen = False
    ota_command_sequence_seen = False
    ota_later_fail_seen = False
    ota_success_seen = False
    target_observability_available = True
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
    anchor_cap: AnchorCapture | RTTCapture | None = None

    with log_path.open("w", encoding="utf-8") as logf:
        try:
            # Strict mode never uses Anchor USB data paths for health gating.
            logf.write(
                f"[HOST_EVT {time.monotonic()-t0:7.2f}s] strict_preflight anchor_usb_policy=power_only "
                "anchor_observability_gate=disabled evidence=controller_uuid_restore_dfu\n"
            )
            if args.anchor_observability_backend != "auto":
                logf.write(
                    f"[HOST_EVT {time.monotonic()-t0:7.2f}s] strict_preflight ignoring_anchor_backend={args.anchor_observability_backend}\n"
                )
            if args.anchor_reset_preflight:
                logf.write(
                    f"[HOST_EVT {time.monotonic()-t0:7.2f}s] strict_preflight ignoring_anchor_reset_preflight=1\n"
                )
            logf.flush()
            if args.phase_a_only:
                logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A only; stop after UUID latch proof\n")
                logf.flush()

            # Phase A: pre-reboot control session
            ser = open_serial(
                args.port,
                args.baud,
                force_kill_port_owner=bool(args.force_kill_port_owner),
                logf=logf,
                t0=t0,
            )
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            logf.write(f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A serial_opened port={args.port}\n")
            logf.flush()
            # Freshly-enumerated CDC can reject immediate writes; settle and
            # drain boot chatter before active status probing.
            settle_deadline = time.monotonic() + 1.5
            while time.monotonic() < settle_deadline:
                line = read_line(ser)
                if line is None:
                    continue
                logf.write(line + "\n")
                logf.flush()

            # Wait until control UART is actually ready; commands sent before
            # this line can be silently dropped during boot/scan startup.
            boot_ready, boot_mode = wait_uart_ready(ser, logf, t0)
            if not boot_ready:
                reason = "phase_a_uart_not_ready"
                raise RuntimeError(reason)

            if boot_mode == "OTA":
                logf.write(
                    f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=A cleanup existing host OTA mode before new target session\n"
                )
                logf.flush()
                send_cmd(ser, "mode recv", logf, t0)
                mode_switch_deadline = time.monotonic() + 12.0
                while time.monotonic() < mode_switch_deadline:
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
                ser, boot_ready, boot_mode = wait_uart_ready_with_reconnect(
                    args.port,
                    args.baud,
                    logf,
                    t0,
                    timeout_s=args.reconnect_timeout_s,
                )
                if not boot_ready:
                    reason = "phase_a_cleanup_reconnect_timeout"
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
                    target_selection_ready = True
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
                if "mode ota (already ota) -> initiate rc=" in line.lower():
                    m_init_inline = INIT_RE.search(line)
                    phase_b_skipped_already_ota = True
                    phase_b_ok = True
                    target_restore_ready = True
                    mode_ota_loaded_after_reboot = True
                    uuid_restored_after_reboot = True
                    filter_uuid_post = target_uuid
                    if m_init_inline:
                        initiate_sent = True
                        initiate_rc = int(m_init_inline.group(1))
                    logf.write(
                        f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=B skipped restore wait because device already in OTA mode\n"
                    )
                    logf.flush()
                    break
                if "DFU SMP service ready" in line:
                    phase_b_skipped_already_ota = True
                    phase_b_ok = True
                    target_restore_ready = True
                    target_transport_ready = True
                    mode_ota_loaded_after_reboot = True
                    uuid_restored_after_reboot = True
                    filter_uuid_post = target_uuid
                    dfu_ready_seen = True
                    logf.write(
                        f"[HOST_EVT {time.monotonic()-t0:7.2f}s] phase=B skipped restore wait because transport is already ready in OTA mode\n"
                    )
                    logf.flush()
                    break

            if not phase_b_skipped_already_ota:
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None

            # Phase B: reconnect after reboot and prove restore
            if not phase_b_skipped_already_ota:
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
                        target_restore_ready = True
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
            if not phase_b_skipped_already_ota:
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
                    target_transport_ready = True
                    if args.pre_reset:
                        send_cmd(ser, "ota_reset", logf, t0)
                        reason = "ota_manual_pre_reset_requested"
                        break

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
        ota_pending_recovery_reset_seen=ota_pending_recovery_reset_seen,
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
    if ota_success_seen:
        return 0
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
