#!/usr/bin/env python3
"""Run the two-phase 2026-07-21 BioSpur overnight validation.

The launcher is deliberately the only owner of the Master_Tag and listener
serial ports.  It performs two bounded configuration transitions, records all
raw traffic, and never attempts firmware update, flash, or late-night recovery.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import serial


ROOT = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion")
TAG_PORT = Path(
    "/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00"
)
ANCHOR_PORT = Path(
    "/dev/serial/by-id/usb-Master_Anchor_Master_Anchor_Control_87EA2F4A526C5A02-if00"
)
LISTENERS = {
    "760184753": "A-E-midpoint-mid",
    "760184548": "B-F-midpoint-mid",
    "760181725": "C-G-midpoint-mid",
    "760184784": "D-H-midpoint-mid",
    "760184964": "vertical-low",
    "760184767": "vertical-mid",
    "760184545": "vertical-high",
    "760181879": "AEDH-E-H-upper",
    "760186115": "BFCG-B-C-lower",
}
LISTENER_PORT = "/dev/serial/by-id/usb-SEGGER_J-Link_000{}-if00"
EXPECTED_MARKER = "tag-fusion-link-v2-absdeadline3"
FORMAL_SECONDS = 3600.0
TRANSITION_AT_SECONDS = 3900.0
PHASE2_TAGS = ("BS9336", "BS955A", "BSCCF4", "BS065F")
FALLBACK_TAGS = ("BS9336", "BS955A", "BSCCF4")
RTT_TOOL = ROOT / "B306_Part/tools/capture_jlink_rtt.py"
RTT_SERIAL = 683234364
RTT_ADDRESS = 0x20002010


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def open_serial(path: Path, baud: int) -> serial.Serial:
    handle = serial.Serial()
    handle.port = str(path)
    handle.baudrate = baud
    handle.timeout = 0.2
    handle.write_timeout = 2.0
    handle.exclusive = True
    handle.dtr = False
    handle.rts = False
    handle.open()
    return handle


class SerialTap:
    """Line logger plus bounded recent-line query for one serial stream."""

    def __init__(self, name: str, port: Path, baud: int, output: Path, started: float):
        self.name = name
        self.port = port
        self.baud = baud
        self.output_path = output
        self.started = started
        self.handle: serial.Serial | None = None
        self.output = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.condition = threading.Condition()
        # A 10-minute four-tag bin is about 24k TR lines.  Keep twice that so
        # heartbeat analysis never silently loses the front of a bin.
        self.recent: deque[tuple[float, str]] = deque(maxlen=50000)
        self.error: str | None = None
        self.bytes = 0
        self.lines = 0

    def start(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output = self.output_path.open("w", encoding="utf-8", buffering=1)
        self.handle = open_serial(self.port, self.baud)
        self.output.write(
            f"# OPEN utc={utc_now()} mono={time.monotonic() - self.started:.6f} "
            f"port={self.port} resolved={self.port.resolve()} baud={self.baud} "
            "DTR=0 RTS=0\n"
        )
        self.thread = threading.Thread(target=self._reader, name=f"tap-{self.name}", daemon=True)
        self.thread.start()

    def _reader(self) -> None:
        assert self.handle is not None and self.output is not None
        try:
            while not self.stop_event.is_set():
                raw = self.handle.read_until(b"\n", 16384)
                if not raw:
                    continue
                now = time.monotonic()
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                self.bytes += len(raw)
                self.lines += 1
                self.output.write(
                    f"{datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')} "
                    f"mono={now - self.started:.6f} {text}\n"
                )
                with self.condition:
                    self.recent.append((now, text))
                    self.condition.notify_all()
        except Exception as exc:  # evidence, never an unbounded reconnect loop
            self.error = f"{type(exc).__name__}: {exc}"
            if self.output:
                self.output.write(f"# ERROR utc={utc_now()} error={self.error}\n")
            with self.condition:
                self.condition.notify_all()

    def send(self, command: str) -> float:
        if self.handle is None:
            raise RuntimeError(f"{self.name} is not open")
        when = time.monotonic()
        assert self.output is not None
        self.output.write(f"# SEND utc={utc_now()} mono={when - self.started:.6f} command={command}\n")
        self.handle.write((command + "\n").encode("utf-8"))
        self.handle.flush()
        return when

    def lines_since(self, since: float) -> list[str]:
        with self.condition:
            return [line for when, line in self.recent if when >= since]

    def wait_collect(self, since: float, seconds: float) -> list[str]:
        deadline = time.monotonic() + seconds
        with self.condition:
            while time.monotonic() < deadline and not self.error:
                self.condition.wait(timeout=min(0.2, deadline - time.monotonic()))
        return self.lines_since(since)

    def command(self, command: str, seconds: float) -> list[str]:
        since = self.send(command)
        return self.wait_collect(since, seconds)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)
        if self.handle:
            self.handle.close()
        if self.output:
            self.output.write(
                f"# CLOSE utc={utc_now()} bytes={self.bytes} lines={self.lines} error={self.error}\n"
            )
            self.output.close()


class Suite:
    def __init__(self, run_dir: Path, phase2_hours: float):
        self.run_dir = run_dir
        self.raw = run_dir / "raw"
        self.state = run_dir / "state"
        self.started = time.monotonic()
        self.phase2_seconds = phase2_hours * 3600.0
        self.events = (run_dir / "EVENTS.log").open("a", encoding="utf-8", buffering=1)
        self.tag: SerialTap | None = None
        self.listeners: dict[str, SerialTap] = {}
        self.rtt: subprocess.Popen | None = None
        self.t0: float | None = None
        self.stop_requested = False
        self.phase2_mode = "not-started"
        self.processes: list[dict] = []

    def event(self, kind: str, **fields) -> None:
        mono = time.monotonic() - self.started
        suffix = " ".join(f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in fields.items())
        line = f"{utc_now()} mono={mono:.6f} event={kind} {suffix}".rstrip()
        print(line, flush=True)
        self.events.write(line + "\n")

    def write_json(self, name: str, data) -> None:
        path = self.state / name
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def manifest(self) -> None:
        expected = datetime.now(timezone.utc).astimezone() + timedelta(
            seconds=max(0.0, TRANSITION_AT_SECONDS + self.phase2_seconds - (time.monotonic() - (self.t0 or self.started)))
        )
        streams = []
        if self.tag:
            streams.append({"name": "Master_Tag App-CDC", "pid": os.getpid(), "port": str(TAG_PORT), "output": str(self.tag.output_path)})
        streams.extend(
            {"name": f"listener-{snr}", "pid": os.getpid(), "port": str(tap.port), "output": str(tap.output_path)}
            for snr, tap in self.listeners.items()
        )
        data = {
            "launcher_pid": os.getpid(),
            "launcher_purpose": "two-phase formal-plus-overnight suite; sole serial owner",
            "launched_at": utc_now(),
            "expected_end": expected.isoformat(timespec="seconds"),
            "kill_condition": "launcher past expected_end+10min, serial tap error, or explicit operator abort",
            "safe_kill": f"kill -TERM {os.getpid()}",
            "streams": streams,
            "children": self.processes,
        }
        self.write_json("PROCESS_MANIFEST.json", data)

    def sleep_until(self, absolute: float, label: str) -> bool:
        while not self.stop_requested:
            remaining = absolute - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(1.0, remaining))
        self.event("WAIT_ABORTED", label=label)
        return False

    def command(self, command: str, seconds: float = 2.0) -> list[str]:
        assert self.tag
        self.event("TAG_COMMAND", command=command, timeout_s=seconds)
        lines = self.tag.command(command, seconds)
        self.event("TAG_COMMAND_DONE", command=command, lines=len(lines))
        return lines

    def anchor_responder(self) -> None:
        tap = SerialTap("anchor", ANCHOR_PORT, 115200, self.raw / "anchor-responder.log", self.started)
        tap.start()
        try:
            time.sleep(1.0)
            lines = tap.command("anchor role all responder", 7.0)
            ready = sum("ready=8/8" in line for line in lines)
            acks = sum("OK RUNTIME_RESTART_REQUESTED" in line for line in lines)
            if ready < 3 or acks < 24:
                raise RuntimeError(f"anchor responder gate failed: ready8={ready}/3 acks={acks}/24")
            self.event("ANCHOR_RESPONDER_GATE", ready8=ready, acks=acks, verdict="PASS")
        finally:
            tap.stop()

    def start_listeners(self) -> None:
        for snr, location in LISTENERS.items():
            port = Path(LISTENER_PORT.format(snr))
            tap = SerialTap(
                f"listener-{snr}", port, 460800,
                self.raw / "listeners" / f"listener-{snr}-{location}.log", self.started,
            )
            tap.start()
            self.listeners[snr] = tap
        time.sleep(1.5)
        errors = {snr: tap.error for snr, tap in self.listeners.items() if tap.error}
        if errors:
            raise RuntimeError(f"listener start failure: {errors}")
        self.event("LISTENERS_STARTED", count=len(self.listeners))

    def start_rtt(self, output: Path, duration: float, purpose: str) -> subprocess.Popen:
        command = [
            sys.executable, str(RTT_TOOL),
            "--serial-number", str(RTT_SERIAL),
            "--device", "NRF52840_XXAA",
            "--address", hex(RTT_ADDRESS),
            "--duration-s", str(duration),
            "--output", str(output),
        ]
        console = output.with_suffix(output.suffix + ".console")
        stream = console.open("w", encoding="utf-8")
        child = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT)
        stream.close()
        self.processes.append(
            {"pid": child.pid, "purpose": purpose, "output": str(output),
             "kill_condition": "launcher abort or 60s past expected duration"}
        )
        self.event("RTT_STARTED", pid=child.pid, purpose=purpose, output=str(output), serial=RTT_SERIAL)
        self.manifest()
        return child

    def b306_preflight(self) -> None:
        output = self.raw / "b306-preflight.log"
        child = self.start_rtt(output, 5.0, "fresh-boot B306 preflight")
        try:
            rc = child.wait(timeout=15.0)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=5.0)
            raise RuntimeError("B306 preflight recorder exceeded bounded timeout")
        if rc != 0 or not output.exists():
            raise RuntimeError(f"B306 preflight capture failed rc={rc}")
        text = output.read_text(encoding="utf-8", errors="replace")
        node_values = [int(value) for value in re.findall(r"\bnode_ms=(\d+)", text)]
        if not node_values:
            raise RuntimeError("B306 preflight has no FUSION records")
        # The hardware rollover is at 71.58 min.  Requiring <5 min proves the
        # operator's requested power cycle without consuming formal margin.
        fresh = min(node_values) < 300_000
        self.event("B306_FRESH_BOOT_GATE", node_ms_min=min(node_values), node_ms_max=max(node_values), verdict="PASS" if fresh else "FAIL")
        if not fresh:
            raise RuntimeError("B306 was not freshly power-cycled (node_ms >= 300000)")

    @staticmethod
    def temperature_from(lines: list[str]) -> list[tuple[int, int]]:
        values = []
        for line in lines:
            match = re.search(r";T,(-?\d+),(-?\d+)(?:\s|$)", line)
            if match:
                values.append((int(match.group(1)), int(match.group(2))))
        return values

    def capture_temperature(self, label: str) -> None:
        lines = self.command("cmd CAPTURE OFF", 3.0)
        values = self.temperature_from(lines)
        self.event("TEMPERATURE", label=label, samples=values[-3:])
        self.command("cmd CAPTURE PARAM 350 400", 3.0)
        lines = self.command("cmd CAPTURE ON", 4.0)
        if not any("CAPTURE_OK STATE=ON" in line for line in lines):
            raise RuntimeError(f"CAPTURE ON failed after {label} temperature sample")

    def phase1_setup(self) -> None:
        assert self.tag
        self.command("ota_target name BS065F", 1.0)
        version = self.command("cmd VERSION", 2.0)
        if not any(EXPECTED_MARKER in line for line in version):
            raise RuntimeError(f"marker gate failed: expected {EXPECTED_MARKER}")
        self.event("MARKER_GATE", marker=EXPECTED_MARKER, verdict="PASS")
        self.anchor_responder()

        # Temperature is read only while CAPTURE is off, then CAP is restored.
        self.capture_temperature("phase1-start")
        self.command("tdma clear", 1.5)
        self.command("tdma roster BS065F motion", 1.5)
        configured = self.command("tdma rebalance", 5.0)
        cfg = [line for line in configured if "CFG_OK TAG=" in line and "LIVE=1" in line]
        if len(cfg) < 1:
            raise RuntimeError("Phase-1 CFG_OK LIVE=1 gate failed")
        self.command("cmd BSL_STATUS", 2.5)
        status = self.command("cmd BSL_STATUS", 2.5)
        gate = [line for line in status if "BSLSTAT;" in line]
        required = ("ci=350", "sup=400", "reqci=350", "reqsup=400", "ciok=1", "supok=1", "cpmode=CAP")
        if not any(all(token in line for token in required) for line in gate):
            raise RuntimeError("Phase-1 CAP parameter/readback gate failed")
        reset = self.command("cmd BSL_LATE_RESET", 2.5)
        if not any("BSLLATE_RESET_OK EN=1" in line for line in reset):
            raise RuntimeError("lateness reset gate failed")
        self.event("PHASE1_SETUP_GATE", cfg_ok=len(cfg), verdict="PASS")

    def read_histogram(self, label: str) -> None:
        before = self.command("cmd BSL_LATE_SUMMARY", 1.8)
        pages = {}
        attempts = {}
        for page in range(8):
            attempts[page] = 0
            for _ in range(2):
                attempts[page] += 1
                lines = self.command(f"cmd BSL_LATE_HIST {page}", 1.8)
                hits = [line for line in lines if f"BSLLATEH;1;page={page};" in line]
                if hits:
                    pages[page] = hits[-1]
                    break
        after = self.command("cmd BSL_LATE_SUMMARY", 1.8)
        result = {
            "label": label, "utc": utc_now(), "pages": pages, "attempts": attempts,
            "summary_before": [line for line in before if "BSLLATE;1;" in line],
            "summary_after": [line for line in after if "BSLLATE;1;" in line],
            "atomic": False,
            "strength": "bounded by before/after cumulative summaries",
        }
        self.write_json(f"hist-{label}.json", result)
        self.event("LIVE_HISTOGRAM", label=label, pages=len(pages), verdict="PASS" if len(pages) == 8 else "DEGRADED")

    def b306_heartbeat(self, label: str) -> None:
        path = self.raw / "b306-rtt-formal.log"
        size = path.stat().st_size if path.exists() else 0
        records = 0
        last = ""
        if path.exists():
            with path.open("rb") as stream:
                stream.seek(max(0, size - 262144))
                tail = stream.read().decode("utf-8", errors="replace")
            lines = [line for line in tail.splitlines() if line.startswith("FUSION_TELEMETRY")]
            records = tail.count("FUSION_UWB ")
            last = lines[-1] if lines else ""
        self.event("B306_HEARTBEAT", label=label, bytes=size, uwb_records_in_tail=records, last_telemetry=last)

    def formal_phase(self) -> None:
        output = self.raw / "b306-rtt-formal.log"
        self.rtt = self.start_rtt(output, TRANSITION_AT_SECONDS, "B306 formal authority T0 through phase transition")
        self.t0 = time.monotonic()
        self.event("PHASE1_START", t0_utc=utc_now(), formal_seconds=FORMAL_SECONDS, expected_slots=36000)
        self.manifest()
        self.command("cmd BSL_STATUS", 1.8)
        self.command("cmd BSL_LATE_SUMMARY", 1.8)

        for minute in range(5, 60, 5):
            if not self.sleep_until(self.t0 + minute * 60.0, f"phase1-{minute}m"):
                return
            self.b306_heartbeat(f"phase1-{minute}m")
            self.command("cmd BSL_STATUS", 1.8)
            self.command("cmd BSL_LATE_SUMMARY", 1.8)
            if minute % 10 == 0:
                self.read_histogram(f"phase1-{minute:02d}m")

        # Stop the cumulative lateness instrument immediately at the formal
        # endpoint.  Evidence reads and the end-only temperature sample happen
        # afterwards and therefore cannot extend the 3600 s population.
        if not self.sleep_until(self.t0 + FORMAL_SECONDS, "phase1-formal-end"):
            return
        self.b306_heartbeat("phase1-60m")
        self.command("cmd BSL_LATE_FREEZE", 1.8)
        self.command("cmd BSL_LATE_SUMMARY", 1.8)
        self.command("cmd BSL_STATUS", 1.8)
        self.read_histogram("phase1-60m")
        for page in range(16):
            self.command(f"cmd BSL_LATE_TAIL {page}", 1.8)
        self.event("PHASE1_FORMAL_END", elapsed_s=time.monotonic() - self.t0)
        self.capture_temperature("phase1-end")

    @staticmethod
    def parse_tr(lines: list[str], seconds: float) -> dict[str, dict]:
        data = defaultdict(lambda: {"n": 0, "ge7": 0, "ge8": 0})
        for line in lines:
            match = re.search(r"\b(BS[0-9A-F]{4}) notify: (TR;[^\r\n]+)", line, re.I)
            if not match:
                continue
            name = match.group(1).upper()
            fields = match.group(2).split(";")
            if len(fields) < 7:
                continue
            try:
                valid = int(fields[6], 16).bit_count()
            except ValueError:
                continue
            data[name]["n"] += 1
            data[name]["ge7"] += valid >= 7
            data[name]["ge8"] += valid >= 8
        result = {}
        for name, row in data.items():
            n = row["n"]
            result[name] = {
                "n": n, "hz": n / seconds,
                "ge7": row["ge7"] / n if n else 0.0,
                "ge8": row["ge8"] / n if n else 0.0,
            }
        return result

    def listener_pollers(self, since: float) -> dict[str, int]:
        pollers = Counter()
        for tap in self.listeners.values():
            for line in tap.lines_since(since):
                if "LPD;1;" not in line:
                    continue
                fields = line[line.index("LPD;1;"):].split(";")
                # LPD v1: prefix,version,listener,near,now,seq_count,seq,
                # peer_id,src,dst,...  Poll source is therefore field 8.
                if len(fields) > 8 and re.fullmatch(r"0xB1[0-9A-Fa-f]{2}", fields[8]):
                    pollers[fields[8].lower()] += 1
        return dict(pollers)

    def apply_roster(self, tags: tuple[str, ...]) -> list[str]:
        self.command("tdma clear", 1.5)
        for tag in tags:
            self.command(f"tdma roster {tag} motion", 1.5)
        return self.command("tdma auto 1", 6.0)

    def verify_roster(self, tags: tuple[str, ...], cfg_lines: list[str], label: str) -> tuple[bool, dict]:
        cfg_ok = [line for line in cfg_lines if "CFG_OK TAG=" in line and "LIVE=1" in line]
        assigned = [line for line in cfg_lines if "CFG assigned[" in line]
        slots = set()
        masks = set()
        for line in assigned:
            sm = re.search(r"slot=(\d+)/(\d+).*mask=0x([0-9A-Fa-f]+)", line)
            if sm:
                slots.add((int(sm.group(1)), int(sm.group(2))))
                masks.add(int(sm.group(3), 16))

        since = time.monotonic()
        tr_lines = self.tag.wait_collect(since, 30.0) if self.tag else []
        rates = self.parse_tr(tr_lines, 30.0)
        pollers = self.listener_pollers(since)
        rate_ok = all(tag in rates and 8.5 <= rates[tag]["hz"] <= 10.5 for tag in tags)
        poller_ok = len(pollers) >= len(tags)
        cfg_gate = len(cfg_ok) >= len(tags) and len(slots) >= len(tags) and len(masks) >= len(tags)
        ok = cfg_gate and rate_ok and poller_ok
        evidence = {
            "label": label, "cfg_ok_live": len(cfg_ok), "assigned": assigned,
            "distinct_slots": sorted(slots), "distinct_masks": sorted(masks),
            "rates": rates, "listener_pollers": pollers,
            "gates": {"cfg": cfg_gate, "rate": rate_ok, "on_air": poller_ok}, "pass": ok,
        }
        self.write_json(f"{label}-entry-gate.json", evidence)
        self.event("ROSTER_GATE", label=label, verdict="PASS" if ok else "FAIL", evidence=evidence)
        return ok, evidence

    def phase_transition(self) -> None:
        assert self.t0
        self.sleep_until(self.t0 + TRANSITION_AT_SECONDS, "phase-transition")
        self.event("PHASE_TRANSITION_START")
        self.command("cmd_all REBOOT", 3.0)
        time.sleep(16.0)  # bounded, proven reconnect settle; no conditionless wait
        cfg = self.apply_roster(PHASE2_TAGS)
        ok, evidence = self.verify_roster(PHASE2_TAGS, cfg, "four-tag-attempt1")
        if not ok:
            # Exactly one retry, no reboot/repeated rapid CFG loop.
            self.event("FOUR_TAG_RETRY", reason=evidence)
            cfg = self.command("tdma auto 1", 6.0)
            ok, evidence = self.verify_roster(PHASE2_TAGS, cfg, "four-tag-attempt2")
        if ok:
            self.phase2_mode = "four-tag-distinct-slot"
            self.event("PHASE2_ENTRY", mode=self.phase2_mode)
            return

        self.event("FOUR_TAG_ENTRY_FAILED", evidence=evidence)
        self.command("cmd_all REBOOT", 3.0)
        time.sleep(16.0)
        cfg = self.apply_roster(FALLBACK_TAGS)
        ok, fallback = self.verify_roster(FALLBACK_TAGS, cfg, "three-tag-fallback")
        if ok:
            self.phase2_mode = "three-tag-known-good-fallback"
            self.event("PHASE2_ENTRY", mode=self.phase2_mode)
        else:
            self.phase2_mode = "free-run-after-fallback-failure"
            self.event("FALLBACK_FAILED_STOP_TOUCHING", evidence=fallback)

    def phase2(self) -> None:
        started = time.monotonic()
        expected_tags = PHASE2_TAGS if self.phase2_mode.startswith("four-tag") else FALLBACK_TAGS
        bins = []
        for index in range(1, int(self.phase2_seconds // 600.0) + 1):
            begin = time.monotonic()
            if not self.sleep_until(started + index * 600.0, f"phase2-bin-{index}"):
                break
            lines = self.tag.lines_since(begin) if self.tag else []
            rates = self.parse_tr(lines, 600.0)
            pollers = self.listener_pollers(begin)
            row = {"bin": index, "utc": utc_now(), "rates": rates, "pollers": pollers}
            bins.append(row)
            self.write_json("phase2-heartbeats.json", bins)
            self.event("PHASE2_HEARTBEAT", bin=index, mode=self.phase2_mode, rates=rates, pollers=pollers,
                       missing=[tag for tag in expected_tags if tag not in rates])
        remainder_end = started + self.phase2_seconds
        self.sleep_until(remainder_end, "phase2-end")
        self.event("PHASE2_END", mode=self.phase2_mode, elapsed_s=time.monotonic() - started)

    def run(self) -> None:
        self.event("LAUNCHER_START", pid=os.getpid(), run_dir=str(self.run_dir))
        self.start_listeners()
        self.tag = SerialTap("master-tag", TAG_PORT, 115200, self.raw / "master-tag-all-night.log", self.started)
        self.tag.start()
        time.sleep(2.0)
        self.manifest()
        self.b306_preflight()
        self.phase1_setup()
        self.formal_phase()
        if not self.stop_requested:
            self.phase_transition()
            self.phase2()

    def close(self) -> None:
        self.stop_requested = True
        if self.rtt and self.rtt.poll() is None:
            self.rtt.terminate()
            try:
                self.rtt.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.rtt.kill()
        if self.tag:
            self.tag.stop()
        for tap in self.listeners.values():
            tap.stop()
        self.event("LAUNCHER_END", phase2_mode=self.phase2_mode)
        self.events.close()


def inventory() -> dict:
    ports = {"master_tag": TAG_PORT, "anchor_master": ANCHOR_PORT}
    ports.update({f"listener_{snr}": Path(LISTENER_PORT.format(snr)) for snr in LISTENERS})
    port_status = {
        name: {"path": str(path), "exists": path.exists(), "resolved": str(path.resolve()) if path.exists() else None}
        for name, path in ports.items()
    }
    free = shutil.disk_usage(ROOT).free
    # Measured listener logs are roughly 55-60 MB/h each.  Reserve 75 MB/h
    # per listener plus 25% for Tag/B306/metadata, for an 8 h whole-night cap.
    estimate = int(9 * 75_000_000 * 8 * 1.25)
    required = estimate * 2
    return {
        "utc": utc_now(), "ports": port_status,
        "disk": {"free_bytes": free, "estimate_bytes": estimate, "required_2x_bytes": required,
                 "pass": free >= required, "basis": "9 listeners * 75 MB/h * 8 h * 1.25"},
        "rtt": {"tool": str(RTT_TOOL), "exists": RTT_TOOL.exists(), "serial": RTT_SERIAL,
                "address": hex(RTT_ADDRESS)},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--phase2-hours", type=float, default=6.5)
    parser.add_argument("--preflight", action="store_true", help="read-only inventory; no ports are opened")
    args = parser.parse_args()

    status = inventory()
    print(json.dumps(status, indent=2))
    if args.preflight:
        return 0 if all(row["exists"] for row in status["ports"].values()) and status["disk"]["pass"] else 2
    if args.run_dir is None:
        parser.error("--run-dir is required unless --preflight is used")
    if args.phase2_hours <= 0 or args.phase2_hours > 12:
        parser.error("--phase2-hours must be in (0, 12]")
    if not all(row["exists"] for row in status["ports"].values()):
        raise SystemExit("required by-id port missing")
    if not status["disk"]["pass"]:
        raise SystemExit("disk gate failed; formal run may proceed only without listener byproducts")
    if args.run_dir.exists():
        allowed_prelaunch = {"PREDICTIONS.md", "launcher-console.log"}
        unexpected = [path.name for path in args.run_dir.iterdir() if path.name not in allowed_prelaunch]
        if unexpected:
            raise SystemExit(f"run directory contains unexpected files: {unexpected}")
        if not (args.run_dir / "PREDICTIONS.md").exists():
            raise SystemExit("PREDICTIONS.md must be written before launch")
    else:
        raise SystemExit("run directory and pre-registered PREDICTIONS.md must exist before launch")
    (args.run_dir / "raw/listeners").mkdir(parents=True, exist_ok=True)
    (args.run_dir / "state").mkdir(parents=True, exist_ok=True)
    (args.run_dir / "state/inventory.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    suite = Suite(args.run_dir, args.phase2_hours)

    def stop(_signum, _frame):
        suite.stop_requested = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        suite.run()
        return 0
    except Exception as exc:
        suite.event("FATAL", error=f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        suite.close()


if __name__ == "__main__":
    raise SystemExit(main())
