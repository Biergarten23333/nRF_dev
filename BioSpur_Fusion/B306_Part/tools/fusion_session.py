#!/usr/bin/env python3
"""Ordered BioSpur Fusion session orchestration over the Fusion Master CDC."""

from __future__ import annotations

import argparse
import fcntl
import glob
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # Offline parser tests do not require pyserial.
    serial = None
    list_ports = None


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG_ROOT = REPO_ROOT / "B306_Part" / "logs"
DEFAULT_STATE = DEFAULT_LOG_ROOT / "fusion_session_active.json"
FUSION_USB_VID = 0x2FE3
FUSION_USB_PID = 0x10F4
FUSION_USB_PRODUCT = "BioSpur Fusion Master"

REPLY_RE = re.compile(
    r"^FUSION_REPLY\b.*\bsource=(B306|TAG)\s+correlation=(\d+)\s+text=(.*)$"
)
FIELD_RE = re.compile(r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)")

ANOMALY_COUNTERS = (
    "crc",
    "header",
    "ring_drop",
    "sweep_drop",
    "duplicate",
    "reorder",
    "drop_unsub",
    "drop_err",
    "uart_restarts",
    "edge_qdrop",
    "orphan_strobe",
    "orphan_edge",
    "orphan_frame",
    "imu_i2c_err",
    "relay_timeout",
    "malformed",
    "logger_drop",
)


class SessionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Reply:
    source: str
    correlation: int
    text: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_fields(line: str) -> dict[str, str]:
    return {match.group(1): match.group(2) for match in FIELD_RE.finditer(line)}


def parse_reply(line: str) -> Reply | None:
    match = REPLY_RE.match(line)
    if match is None:
        return None
    return Reply(match.group(1), int(match.group(2)), match.group(3))


def u32_delta(start: int, end: int) -> int:
    return (end - start) & 0xFFFFFFFF


def imu_sequence_gaps(lines: Iterable[str]) -> tuple[int, int]:
    previous_seq: int | None = None
    previous_n = 0
    gaps = 0
    records = 0
    for line in lines:
        if not line.startswith("FUSION_IMU "):
            continue
        fields = parse_fields(line)
        if "seq" not in fields or "n" not in fields:
            gaps += 1
            continue
        seq = int(fields["seq"], 0)
        count = int(fields["n"], 0)
        if previous_seq is not None:
            expected = (previous_seq + previous_n) & 0xFFFF
            if seq != expected:
                gaps += 1
        previous_seq = seq
        previous_n = count
        records += 1
    return gaps, records


def counter_deltas(
    baseline: dict[str, str], final: dict[str, str], names: Iterable[str]
) -> dict[str, int]:
    deltas: dict[str, int] = {}
    for name in names:
        if name not in baseline or name not in final:
            raise SessionError(f"telemetry field missing: {name}")
        deltas[name] = u32_delta(int(baseline[name], 0), int(final[name], 0))
    return deltas


def evaluate_uwb_window(
    lines: list[str],
    baseline: dict[str, str],
    final: dict[str, str],
    duration_s: float,
    require_imu: bool,
) -> dict:
    frame_delta = u32_delta(int(baseline["frames"], 0), int(final["frames"], 0))
    rise_delta = u32_delta(int(baseline["rise_n"], 0), int(final["rise_n"], 0))
    node_dt_s = u32_delta(
        int(baseline["node_ms"], 0), int(final["node_ms"], 0)
    ) / 1000.0
    effective_s = max(node_dt_s, duration_s * 0.75)
    uwb_lines = [line for line in lines if line.startswith("FUSION_UWB ")]
    healthy_uwb = sum(" verdict=healthy " in f" {line} " for line in uwb_lines)
    rate_hz = frame_delta / effective_s if effective_s > 0 else 0.0
    deltas = counter_deltas(baseline, final, ANOMALY_COUNTERS)
    seq_gaps, imu_records = imu_sequence_gaps(lines)

    reasons: list[str] = []
    if not (8.0 <= rate_hz <= 12.0):
        reasons.append(f"UWB frame rate {rate_hz:.3f} Hz outside 8..12")
    if frame_delta == 0 or rise_delta == 0:
        reasons.append("UART frame or strobe counter did not rise")
    if abs(frame_delta - rise_delta) > 1:
        reasons.append(
            f"strobe/frame delta mismatch frames={frame_delta} rise={rise_delta}"
        )
    if len(uwb_lines) < max(1, int(duration_s * 8.0)):
        reasons.append(f"too few FUSION_UWB records: {len(uwb_lines)}")
    if healthy_uwb != len(uwb_lines):
        reasons.append(
            f"non-healthy UWB records: {len(uwb_lines) - healthy_uwb}"
        )
    nonzero = {name: value for name, value in deltas.items() if value != 0}
    if nonzero:
        reasons.append(f"anomaly counter deltas nonzero: {nonzero}")
    if require_imu:
        imu_rate = int(final.get("imu_rate", "0"), 0)
        imu_batch = int(final.get("imu_batch", "0"), 0)
        expected_records = (
            duration_s * imu_rate / imu_batch if imu_batch > 0 else 0.0
        )
        if final.get("imu_active") != "1":
            reasons.append("IMU not active at sentinel end")
        if imu_records < max(1, int(expected_records * 0.80)):
            reasons.append(
                f"too few FUSION_IMU records: {imu_records}, "
                f"expected about {expected_records:.0f}"
            )
        if seq_gaps != 0:
            reasons.append(f"IMU sequence gaps: {seq_gaps}")

    return {
        "pass": not reasons,
        "reasons": reasons,
        "duration_s": duration_s,
        "node_dt_s": node_dt_s,
        "frame_delta": frame_delta,
        "rise_delta": rise_delta,
        "frame_rate_hz": rate_hz,
        "uwb_records": len(uwb_lines),
        "healthy_uwb_records": healthy_uwb,
        "counter_deltas": deltas,
        "imu_records": imu_records,
        "imu_seq_gaps": seq_gaps,
    }


class LineChannel:
    def __init__(self, port: str, log_file, label: str):
        if serial is None:
            raise SessionError("pyserial is required for hardware operation")
        if "SEGGER_J-Link_" in port:
            raise SessionError(f"refusing J-Link CDC port: {port}")
        self.port = port
        self.log_file = log_file
        self.label = label
        self.device = serial.Serial()
        self.device.port = port
        self.device.baudrate = 115200
        self.device.timeout = 0.10
        self.device.write_timeout = 1.0
        self.device.dtr = False
        self.device.rts = False
        self.device.open()
        self.device.dtr = False
        self.device.rts = False

    def close(self) -> None:
        self.device.close()

    def _record(self, direction: str, line: str) -> None:
        self.log_file.write(
            f"{time.time():.6f} {time.monotonic():.6f} "
            f"{self.label}_{direction} {line}\n"
        )
        self.log_file.flush()

    def send(self, line: str) -> None:
        self._record("TX", line)
        self.device.write((line + "\n").encode("utf-8"))
        self.device.flush()

    def read(self, deadline: float) -> str | None:
        while time.monotonic() < deadline:
            raw = self.device.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip("\r\n")
            if line:
                self._record("RX", line)
                return line
        return None

    def collect(self, duration_s: float) -> list[str]:
        deadline = time.monotonic() + duration_s
        lines: list[str] = []
        while time.monotonic() < deadline:
            line = self.read(deadline)
            if line is not None:
                lines.append(line)
        return lines


def resolve_fusion_port(explicit: str | None) -> str:
    if explicit:
        if "SEGGER_J-Link_" in explicit:
            raise SessionError(f"refusing J-Link CDC port: {explicit}")
        return explicit
    if list_ports is None:
        raise SessionError("pyserial port discovery unavailable")
    matches = []
    for port in list_ports.comports():
        product = port.product or port.description or ""
        if (
            port.vid == FUSION_USB_VID
            and port.pid == FUSION_USB_PID
            and FUSION_USB_PRODUCT in product
        ):
            matches.append(port.device)
    if len(matches) != 1:
        raise SessionError(
            f"expected one {FUSION_USB_PRODUCT} {FUSION_USB_VID:04X}:"
            f"{FUSION_USB_PID:04X}, found {matches}"
        )
    return matches[0]


def resolve_master_tag_port(explicit: str | None) -> str:
    if explicit:
        if "SEGGER_J-Link_" in explicit:
            raise SessionError(f"refusing J-Link CDC port: {explicit}")
        return explicit
    patterns = (
        "/dev/serial/by-id/usb-Master_Tag_*Control_*-if00",
        "/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_*-if00",
    )
    matches: list[str] = []
    for pattern in patterns:
        matches.extend(glob.glob(pattern))
    matches = list(dict.fromkeys(sorted(matches)))
    if len(matches) != 1:
        raise SessionError(f"expected one Master_Tag App CDC, found {matches}")
    return matches[0]


class FusionController:
    def __init__(
        self,
        channel: LineChannel,
        bsf: str,
        timeout_s: float,
        max_attempts: int,
    ):
        self.channel = channel
        self.bsf = bsf
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.latest_telemetry: dict[str, str] | None = None

    def _observe(self, line: str) -> None:
        if line.startswith("FUSION_TELEMETRY "):
            self.latest_telemetry = parse_fields(line)

    def read_until(
        self, predicate: Callable[[str], bool], timeout_s: float, what: str
    ) -> str:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            line = self.channel.read(deadline)
            if line is None:
                break
            self._observe(line)
            if predicate(line):
                return line
        raise SessionError(f"timeout waiting for {what} after {timeout_s:.1f}s")

    def collect(self, duration_s: float) -> list[str]:
        deadline = time.monotonic() + duration_s
        lines: list[str] = []
        while time.monotonic() < deadline:
            line = self.channel.read(deadline)
            if line is not None:
                self._observe(line)
                lines.append(line)
        return lines

    def ensure_bridge(self) -> dict[str, str]:
        for attempt in range(1, self.max_attempts + 1):
            self.channel.send("LIST")
            try:
                line = self.read_until(
                    lambda item: item.startswith("FUSION_LIST "),
                    self.timeout_s,
                    f"LIST reply attempt {attempt}/{self.max_attempts}",
                )
            except SessionError:
                if attempt == self.max_attempts:
                    raise
                continue
            fields = parse_fields(line)
            if (
                fields.get("count") == "1"
                and fields.get("name") == self.bsf
                and fields.get("subscribed") == "1"
            ):
                return fields
            if attempt == self.max_attempts:
                raise SessionError(f"Fusion bridge not ready for {self.bsf}: {line}")
            time.sleep(0.5)
        raise AssertionError("unreachable")

    def command(
        self,
        command: str,
        text_predicate: Callable[[str], bool],
        source: str = "B306",
        allow_resend_after_tx: bool = False,
    ) -> Reply:
        full_line = f"{self.bsf} {command}"
        for attempt in range(1, self.max_attempts + 1):
            self.channel.send(full_line)
            deadline = time.monotonic() + self.timeout_s
            tx_seen = False
            while time.monotonic() < deadline:
                line = self.channel.read(deadline)
                if line is None:
                    break
                self._observe(line)
                if (
                    line.startswith("FUSION_COMMAND_TX ")
                    and f"line={full_line}" in line
                ):
                    if " err=0 " not in f" {line} ":
                        raise SessionError(f"command transport failed: {line}")
                    tx_seen = True
                    continue
                if line.startswith("FUSION_COMMAND_REJECT "):
                    raise SessionError(f"command rejected: {line}")
                reply = parse_reply(line)
                if (
                    tx_seen
                    and reply is not None
                    and reply.source == source
                    and text_predicate(reply.text)
                ):
                    return reply
            if tx_seen and not allow_resend_after_tx:
                raise SessionError(
                    f"{command}: transmitted but no matching {source} reply; "
                    "not retransmitting a possibly executed command"
                )
            if attempt == self.max_attempts:
                raise SessionError(
                    f"{command}: no matching reply after {attempt} attempts"
                )
        raise AssertionError("unreachable")

    def wait_telemetry(self, newer_than_ms: int | None = None) -> dict[str, str]:
        def suitable(line: str) -> bool:
            if not line.startswith("FUSION_TELEMETRY "):
                return False
            fields = parse_fields(line)
            if newer_than_ms is None:
                return True
            return int(fields.get("node_ms", "0"), 0) > newer_than_ms

        line = self.read_until(suitable, self.timeout_s + 2.0, "fresh telemetry")
        return parse_fields(line)

    def relay_cfg(self, cfg: str) -> Reply:
        queued = self.command(
            cfg, lambda text: text.startswith("RELAY_QUEUED"), source="B306"
        )
        return_reply = self.read_until(
            lambda line: (
                (reply := parse_reply(line)) is not None
                and reply.source == "TAG"
                and reply.correlation == queued.correlation
            ),
            2.2,
            f"TAG relay ACK correlation={queued.correlation}",
        )
        reply = parse_reply(return_reply)
        assert reply is not None
        if not reply.text.startswith("CFG_OK") or "LIVE=1" not in reply.text:
            raise SessionError(f"TAG CFG rejected or incomplete: {reply.text}")
        return reply

    def reboot_preflight(self) -> dict[str, str]:
        self.command(
            "REBOOT",
            lambda text: text.startswith("REBOOT QUEUED"),
            allow_resend_after_tx=False,
        )
        self.read_until(
            lambda line: line.startswith("FUSION_BRIDGE_READY ")
            and f"name={self.bsf}" in line,
            30.0,
            "B306 disconnect/reconnect and bridge discovery",
        )
        telemetry = self.wait_telemetry()
        if int(telemetry.get("node_ms", "999999"), 0) > 30000:
            raise SessionError(
                f"post-reboot node_ms is not fresh: {telemetry.get('node_ms')}"
            )
        return telemetry

    def counters(self) -> dict[str, str]:
        first = self.command("COUNTERS", lambda text: text.startswith("CTR1 "))
        second_line = self.read_until(
            lambda line: (
                (reply := parse_reply(line)) is not None
                and reply.source == "B306"
                and reply.correlation == first.correlation
                and reply.text.startswith("CTR2 ")
            ),
            self.timeout_s,
            f"CTR2 correlation={first.correlation}",
        )
        second = parse_reply(second_line)
        assert second is not None
        return {"ctr1": first.text, "ctr2": second.text}


class MasterTagController:
    def __init__(self, channel: LineChannel, max_attempts: int):
        self.channel = channel
        self.max_attempts = max_attempts

    def _send_collect(self, command: str, wait_s: float) -> list[str]:
        self.channel.send(command)
        return self.channel.collect(wait_s)

    def configure(self, tag_name: str, hz: int) -> dict:
        last_lines: list[str] = []
        for attempt in range(1, self.max_attempts + 1):
            lines: list[str] = []
            for command, wait_s in (
                ("ota_target token -1", 0.5),
                (f"ota_target name {tag_name}", 0.5),
                ("ota_target prefix -", 0.5),
                ("ota_target uuid -", 0.5),
                ("mode recv", 8.0),
                ("device kind tag", 2.0),
                ("conn", 12.0),
                ("cmd_all CFG_STOP", 1.0),
                ("tdma hold 1", 0.5),
                ("tdma clear", 1.2),
                (f"tdma freq motion {hz}", 0.5),
                (f"tdma roster {tag_name} motion", 0.5),
                ("tdma hold 0", 1.0),
                ("tdma rebalance", 1.2),
                ("tdma show", 1.0),
            ):
                lines.extend(self._send_collect(command, wait_s))
            last_lines = lines
            cfg_lines = [line for line in lines if "CFG_OK" in line]
            if cfg_lines:
                return {
                    "attempt": attempt,
                    "commands": [
                        "ota_target token -1",
                        f"ota_target name {tag_name}",
                        "ota_target prefix -",
                        "ota_target uuid -",
                        "mode recv",
                        "device kind tag",
                        "conn",
                        "cmd_all CFG_STOP",
                        "tdma hold 1",
                        "tdma clear",
                        f"tdma freq motion {hz}",
                        f"tdma roster {tag_name} motion",
                        "tdma hold 0",
                        "tdma rebalance",
                        "tdma show",
                    ],
                    "cfg_evidence": cfg_lines,
                }
        raise SessionError(
            f"Master_Tag TDMA configuration lacked CFG_OK after "
            f"{self.max_attempts} attempts; tail={last_lines[-8:]}"
        )

    def clear(self) -> list[str]:
        lines = self._send_collect("cmd_all CFG_STOP", 1.0)
        lines.extend(self._send_collect("tdma clear", 1.2))
        return lines


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_predictions(run_dir: Path) -> None:
    (run_dir / "PREDICTIONS.md").write_text(
        """# Pre-registered Phase-C predictions

Written before opening either serial device.

1. S4 proves both UART frames and RDY rises increasing at 8–12 Hz; `LIVE=1`
   alone never passes the gate.
2. During the 10 s S7 sentinel, UWB stays at 8–12 Hz, every observed UWB
   verdict is healthy, all transport/orphan/error counter deltas are zero,
   and IMU sequence gaps are zero.
3. If S7 fails, `IMU STOP` is sent and acknowledged while UWB is left running.
4. For the later 30 min V-C1 gate, all listed UWB/drop/orphan anomaly deltas
   remain zero and every 60 s relay command is correlated and acknowledged.
5. The dynamic handshake should show a constant IMU-to-UWB timing offset of
   the I2C pull latency rather than a motion-dependent drift.
"""
    )


def acquire_owner_lock(log_root: Path):
    log_root.mkdir(parents=True, exist_ok=True)
    lock_file = (log_root / ".fusion_session.lock").open("a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise SessionError("another Fusion rig owner is active") from exc
    return lock_file


def validate_bsf(value: str) -> str:
    value = value.upper()
    if re.fullmatch(r"BSF[0-9A-F]{4}", value) is None:
        raise argparse.ArgumentTypeError("expected BSF followed by four hex digits")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-root", type=Path, default=DEFAULT_LOG_ROOT)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    subparsers = parser.add_subparsers(dest="action", required=True)

    start = subparsers.add_parser("start", help="run S1..S7")
    start.add_argument("--bsf", required=True, type=validate_bsf)
    start.add_argument("--path", required=True, choices=("master", "relay"))
    start.add_argument("--port")
    start.add_argument("--master-port")
    start.add_argument("--tag-name", default="BS065F")
    start.add_argument("--tag-id", type=int, default=1)
    start.add_argument("--slot", type=int, default=0)
    start.add_argument("--count", type=int, default=10)
    start.add_argument("--period", type=int, default=10)
    start.add_argument("--active", type=int, default=9)
    start.add_argument("--epoch", type=int, default=5000)
    start.add_argument("--hz", type=int, default=10)
    start.add_argument("--imu-rate", type=int, choices=(50, 100, 200), default=200)
    start.add_argument("--imu-batch", type=int, choices=range(1, 6), default=2)
    start.add_argument("--timeout", type=float, default=5.0)
    start.add_argument("--max-attempts", type=int, default=3)
    start.add_argument("--proof-seconds", type=float, default=4.0)
    start.add_argument("--sentinel-seconds", type=float, default=10.0)
    start.add_argument(
        "--no-preflight-reboot",
        dest="preflight_reboot",
        action="store_false",
        help="debug only; formal sessions require the default remote reboot",
    )
    start.set_defaults(preflight_reboot=True)

    stop = subparsers.add_parser("stop", help="run T1..T3")
    stop.add_argument("--port")
    stop.add_argument("--master-port")
    stop.add_argument("--bsf", type=validate_bsf)
    stop.add_argument("--path", choices=("master", "relay"))
    stop.add_argument("--timeout", type=float, default=5.0)
    stop.add_argument("--max-attempts", type=int, default=3)
    stop.add_argument("--clear-tdma", action="store_true")
    return parser


def validate_start_args(args) -> None:
    if args.max_attempts < 1:
        raise SessionError("--max-attempts must be >=1")
    if args.timeout <= 0 or args.proof_seconds <= 0 or args.sentinel_seconds <= 0:
        raise SessionError("timeouts and observation windows must be positive")
    if not (0 <= args.tag_id <= 255):
        raise SessionError("--tag-id must be 0..255")
    if args.slot < 0 or args.count <= 0 or args.slot >= args.count:
        raise SessionError("require 0 <= slot < count")


def run_start(args) -> int:
    validate_start_args(args)
    if args.state_file.exists():
        raise SessionError(
            f"active-state file exists; stop or inspect it first: {args.state_file}"
        )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = args.log_root / f"fusion_session_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    write_predictions(run_dir)
    summary: dict = {
        "action": "start",
        "started_utc": utc_now(),
        "status": "IN_PROGRESS",
        "bsf": args.bsf,
        "path": args.path,
        "run_dir": str(run_dir),
        "steps": {},
    }
    write_json(run_dir / "summary.json", summary)

    fusion_channel = None
    master_channel = None
    imu_started = False
    try:
        with (run_dir / "raw.log").open("a", buffering=1) as raw_log:
            fusion_port = resolve_fusion_port(args.port)
            summary["fusion_port"] = fusion_port
            fusion_channel = LineChannel(fusion_port, raw_log, "FUSION")
            controller = FusionController(
                fusion_channel, args.bsf, args.timeout, args.max_attempts
            )
            summary["steps"]["P0_LIST"] = controller.ensure_bridge()
            if args.preflight_reboot:
                summary["steps"]["P0_REBOOT"] = controller.reboot_preflight()
                summary["steps"]["P0_LIST_AFTER_REBOOT"] = controller.ensure_bridge()

            # S1
            ping = controller.command(
                "PING",
                lambda text: text.startswith("PONG ")
                and f"name={args.bsf}" in text
                and "proto=" in text,
                allow_resend_after_tx=True,
            )
            summary["steps"]["S1"] = ping.__dict__

            # S2
            status = controller.command(
                "STATUS",
                lambda text: text.startswith("STATUS ")
                and "verify=PASS" in text
                and "imu=0/" in text,
                allow_resend_after_tx=True,
            )
            summary["steps"]["S2"] = status.__dict__
            controller.wait_telemetry()

            # S3
            if args.path == "relay":
                cfg = (
                    f"TAG CFG id={args.tag_id} slot={args.slot} "
                    f"count={args.count} period={args.period} "
                    f"active={args.active} epoch={args.epoch}"
                )
                tag_reply = controller.relay_cfg(cfg)
                summary["steps"]["S3"] = {
                    "path": "relay",
                    "command": cfg,
                    "reply": tag_reply.__dict__,
                }
            else:
                master_port = resolve_master_tag_port(args.master_port)
                summary["master_port"] = master_port
                master_channel = LineChannel(master_port, raw_log, "MASTER_TAG")
                master = MasterTagController(master_channel, args.max_attempts)
                summary["steps"]["S3"] = {
                    "path": "master",
                    **master.configure(args.tag_name, args.hz),
                }

            # S4
            baseline = controller.wait_telemetry()
            proof_lines = controller.collect(args.proof_seconds)
            final = controller.latest_telemetry
            if (
                final is None
                or final.get("node_ms") == baseline.get("node_ms")
            ):
                raise SessionError("S4 lacked final telemetry")
            proof = evaluate_uwb_window(
                proof_lines, baseline, final, args.proof_seconds, require_imu=False
            )
            summary["steps"]["S4"] = proof
            if not proof["pass"]:
                raise SessionError(f"S4 UWB proof failed: {proof['reasons']}")

            # S5
            clear_reply = controller.command(
                "COUNTERS CLEAR",
                lambda text: text == "COUNTERS CLEARED",
                allow_resend_after_tx=False,
            )
            clean_baseline = controller.wait_telemetry()
            summary["steps"]["S5"] = {
                "reply": clear_reply.__dict__,
                "telemetry": clean_baseline,
            }

            # Configure the two IMU knobs while still stopped, then S6.
            rate_reply = controller.command(
                f"IMU RATE={args.imu_rate}",
                lambda text: text.startswith("IMU RATE OK "),
                allow_resend_after_tx=False,
            )
            batch_reply = controller.command(
                f"IMU BATCH={args.imu_batch}",
                lambda text: text.startswith("IMU BATCH OK "),
                allow_resend_after_tx=False,
            )
            try:
                start_reply = controller.command(
                    "IMU START",
                    lambda text: text.startswith("IMU START OK "),
                    allow_resend_after_tx=False,
                )
            except Exception as start_exc:
                try:
                    stop_reply = controller.command(
                        "IMU STOP",
                        lambda text: text.startswith("IMU STOP OK "),
                        allow_resend_after_tx=False,
                    )
                    summary["steps"]["S6_UNCERTAIN_ROLLBACK"] = (
                        stop_reply.__dict__
                    )
                except Exception as rollback_exc:
                    summary["steps"]["S6_UNCERTAIN_ROLLBACK"] = {
                        "error": str(rollback_exc)
                    }
                    raise SessionError(
                        f"IMU START outcome uncertain: {start_exc}; "
                        f"IMU STOP rollback also failed: {rollback_exc}"
                    ) from start_exc
                raise
            imu_started = True
            summary["steps"]["S6"] = {
                "rate": rate_reply.__dict__,
                "batch": batch_reply.__dict__,
                "start": start_reply.__dict__,
            }

            # S7
            try:
                sentinel_baseline = controller.wait_telemetry()
                sentinel_lines = controller.collect(args.sentinel_seconds)
                sentinel_final = controller.latest_telemetry
                if (
                    sentinel_final is None
                    or sentinel_final.get("node_ms")
                    == sentinel_baseline.get("node_ms")
                ):
                    raise SessionError("S7 lacked final telemetry")
                sentinel = evaluate_uwb_window(
                    sentinel_lines,
                    sentinel_baseline,
                    sentinel_final,
                    args.sentinel_seconds,
                    require_imu=True,
                )
                summary["steps"]["S7"] = sentinel
                if not sentinel["pass"]:
                    raise SessionError(
                        f"S7 coexistence sentinel failed: {sentinel['reasons']}"
                    )
            except Exception as sentinel_exc:
                if imu_started:
                    try:
                        stop_reply = controller.command(
                            "IMU STOP",
                            lambda text: text.startswith("IMU STOP OK "),
                            allow_resend_after_tx=False,
                        )
                        imu_started = False
                        summary["steps"]["S7_ROLLBACK"] = stop_reply.__dict__
                    except Exception as rollback_exc:
                        summary["steps"]["S7_ROLLBACK"] = {
                            "error": str(rollback_exc)
                        }
                        raise SessionError(
                            f"{sentinel_exc}; IMU STOP rollback also failed: "
                            f"{rollback_exc}"
                        ) from sentinel_exc
                raise

            summary["status"] = "RUNNING"
            summary["completed_utc"] = utc_now()
            state = {
                "status": "RUNNING",
                "bsf": args.bsf,
                "path": args.path,
                "tag_name": args.tag_name,
                "started_utc": summary["completed_utc"],
                "run_dir": str(run_dir),
                "fusion_port": fusion_port,
                "master_port": summary.get("master_port"),
            }
            args.state_file.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.state_file, state)
            write_json(run_dir / "summary.json", summary)
            print(f"SESSION RUNNING: {run_dir}")
            return 0
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        summary["completed_utc"] = utc_now()
        summary["imu_started_when_failed"] = imu_started
        write_json(run_dir / "summary.json", summary)
        raise
    finally:
        if master_channel is not None:
            master_channel.close()
        if fusion_channel is not None:
            fusion_channel.close()


def run_stop(args) -> int:
    if not args.state_file.exists():
        raise SessionError(f"no active session state: {args.state_file}")
    state = json.loads(args.state_file.read_text())
    bsf = args.bsf or state["bsf"]
    path = args.path or state["path"]
    run_dir = Path(state["run_dir"])
    summary_path = run_dir / "stop_summary.json"
    summary: dict = {
        "action": "stop",
        "started_utc": utc_now(),
        "status": "IN_PROGRESS",
        "bsf": bsf,
        "path": path,
        "clear_tdma": args.clear_tdma,
        "steps": {},
    }
    fusion_channel = None
    master_channel = None
    try:
        with (run_dir / "raw.log").open("a", buffering=1) as raw_log:
            # Re-resolve by USB identity on every invocation; ttyACM numbering
            # may change across the pre-session reboot or an unrelated USB event.
            fusion_port = resolve_fusion_port(args.port)
            fusion_channel = LineChannel(fusion_port, raw_log, "FUSION")
            controller = FusionController(
                fusion_channel, bsf, args.timeout, args.max_attempts
            )
            controller.ensure_bridge()
            # T1
            stopped = controller.command(
                "IMU STOP",
                lambda text: text.startswith("IMU STOP OK "),
                allow_resend_after_tx=False,
            )
            summary["steps"]["T1"] = stopped.__dict__
            # T2
            summary["steps"]["T2"] = {
                "replies": controller.counters(),
                "telemetry": controller.wait_telemetry(),
            }
            # T3
            if args.clear_tdma:
                if path == "relay":
                    queued = controller.command(
                        "TAG TDMA CLEAR",
                        lambda text: text.startswith("RELAY_QUEUED"),
                        allow_resend_after_tx=False,
                    )
                    tag_line = controller.read_until(
                        lambda line: (
                            (reply := parse_reply(line)) is not None
                            and reply.source == "TAG"
                            and reply.correlation == queued.correlation
                        ),
                        2.2,
                        "TAG TDMA CLEAR/REBOOT ACK",
                    )
                    summary["steps"]["T3"] = {
                        "queued": queued.__dict__,
                        "tag": parse_reply(tag_line).__dict__,
                    }
                else:
                    master_port = resolve_master_tag_port(
                        args.master_port or state.get("master_port")
                    )
                    master_channel = LineChannel(
                        master_port, raw_log, "MASTER_TAG"
                    )
                    master = MasterTagController(master_channel, args.max_attempts)
                    summary["steps"]["T3"] = {
                        "path": "master",
                        "evidence": master.clear(),
                    }
            summary["status"] = "STOPPED"
            summary["completed_utc"] = utc_now()
            write_json(summary_path, summary)
            args.state_file.unlink()
            print(f"SESSION STOPPED: {run_dir}")
            return 0
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        summary["completed_utc"] = utc_now()
        write_json(summary_path, summary)
        raise
    finally:
        if master_channel is not None:
            master_channel.close()
        if fusion_channel is not None:
            fusion_channel.close()


def main() -> int:
    args = build_parser().parse_args()
    lock_file = acquire_owner_lock(args.log_root)
    try:
        return run_start(args) if args.action == "start" else run_stop(args)
    except SessionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


if __name__ == "__main__":
    raise SystemExit(main())
