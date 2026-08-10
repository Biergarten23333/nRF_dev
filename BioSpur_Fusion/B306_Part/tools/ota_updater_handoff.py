#!/usr/bin/env python3
"""Capture and validate the DK updater handoff before restoring the Master."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from jlink_rtt_transport import JLinkRttTransport
from ota_build_identity import atomic_write

SCHEMA = "biospur-updater-stage-v1"
STAGES = (
    "UPDATER_BOOTED", "TARGET_SCANNING", "TARGET_CONNECTED",
    "SMP_DISCOVERED", "SMP_SUBSCRIBED", "UPLOAD_STARTED",
    "UPLOAD_PROGRESS", "UPLOAD_COMPLETE", "SECONDARY_HASH_VERIFIED",
    "PENDING_SET", "REBOOT_QUEUED", "READY_FOR_CONFIRM",
)
TERMINALS = {"READY_FOR_CONFIRM", "FAILED", "PHASE_TIMEOUT"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
PROGRESS = re.compile(r"OTA upload progress: (\d+)% \((\d+)/(\d+) bytes\)")


class UpdaterEvidenceError(ValueError):
    pass


@dataclass
class UpdaterStateMachine:
    run_id: str
    node: str
    expected_image_sha: str
    records: list[dict] = field(default_factory=list)
    highest: int = -1
    terminal: str | None = None
    pending_request: bool = False
    reboot_request: bool = False

    def __post_init__(self) -> None:
        if not self.run_id or not re.fullmatch(r"[A-Za-z0-9_.:-]+", self.run_id):
            raise UpdaterEvidenceError("invalid run_id")
        if not re.fullmatch(r"BSF[0-9A-F]{4}", self.node):
            raise UpdaterEvidenceError("invalid requested node")
        if not HEX64.fullmatch(self.expected_image_sha):
            raise UpdaterEvidenceError("invalid expected image SHA")

    def _emit(self, stage: str, now: float, **extra) -> dict:
        if self.terminal is not None:
            raise UpdaterEvidenceError("record after terminal")
        if stage in STAGES:
            index = STAGES.index(stage)
            if stage == "UPLOAD_PROGRESS":
                if self.highest < STAGES.index("UPLOAD_STARTED"):
                    raise UpdaterEvidenceError("upload progress before upload start")
            elif index <= self.highest:
                raise UpdaterEvidenceError(f"stage regression or duplicate: {stage}")
            self.highest = max(self.highest, index)
        record = {
            "schema": SCHEMA, "run_id": self.run_id, "target_node": self.node,
            "monotonic_timestamp": now, "stage": stage,
            "expected_image_sha": self.expected_image_sha,
            "observed_image_sha": extra.pop("observed_image_sha", None),
            "progress_current": extra.pop("progress_current", None),
            "progress_total": extra.pop("progress_total", None),
            "error_code": extra.pop("error_code", None),
            "error_context": extra.pop("error_context", None),
            **extra,
        }
        self.records.append(record)
        if stage in TERMINALS:
            self.terminal = stage
        return record

    def feed(self, line: str, now: float) -> dict | None:
        if "BioSpur fast BLE OTA master ready" in line:
            return self._emit("UPDATER_BOOTED", now)
        if "OTA_INITIAL_SCAN armed" in line:
            return self._emit("TARGET_SCANNING", now)
        if "Connected target evidence: verified=1" in line:
            if f"name={self.node}" not in line:
                return self._emit("FAILED", now, error_code="WRONG_NODE",
                                  error_context=line)
            if self.highest >= STAGES.index("PENDING_SET"):
                # Expected post-reset reacquisition. READY is authorized by
                # the accepted reset response, not by duplicating early stages.
                return None
            return self._emit("TARGET_CONNECTED", now)
        if line.strip() == "DFU SMP service ready":
            return self._emit("SMP_DISCOVERED", now)
        if "OTA SMP subscribe ok: rc=0" in line:
            return self._emit("SMP_SUBSCRIBED", now)
        if line.startswith("OTA upload starting:"):
            return self._emit("UPLOAD_STARTED", now)
        match = PROGRESS.search(line)
        if match:
            return self._emit("UPLOAD_PROGRESS", now,
                              progress_percent=int(match.group(1)),
                              progress_current=int(match.group(2)),
                              progress_total=int(match.group(3)))
        if line.strip() == "OTA upload complete":
            return self._emit("UPLOAD_COMPLETE", now)
        if (line.startswith("OTA_STATE_READ parsed=1 expected=1")
                and "expected_secondary=1" in line
                and "secondary_present=1" in line
                and self.highest >= STAGES.index("UPLOAD_COMPLETE")):
            return self._emit("SECONDARY_HASH_VERIFIED", now,
                              observed_image_sha=self.expected_image_sha,
                              secondary_verified=True)
        if line.strip() == "OTA pending/test request":
            self.pending_request = True
            return None
        if (self.pending_request and
                "OTA command done: group=0x0001 cmd=0x00 status=0" in line):
            self.pending_request = False
            return self._emit("PENDING_SET", now, pending_set=True)
        if line.strip() == "OTA reset request":
            self.reboot_request = True
            return None
        if (self.reboot_request and
                "OTA command done: group=0x0000 cmd=0x05 status=0" in line):
            self.reboot_request = False
            self._emit("REBOOT_QUEUED", now, reboot_queued=True)
            return self._emit("READY_FOR_CONFIRM", now,
                              observed_image_sha=self.expected_image_sha,
                              secondary_verified=True, pending_set=True,
                              reboot_queued=True)
        if "OTA_STATE:verify_failed" in line or "OTA auto-start failed:" in line:
            return self._emit("FAILED", now, error_code="UPDATER_FAILED",
                              error_context=line)
        return None

    def timeout(self, now: float) -> dict:
        return self._emit("PHASE_TIMEOUT", now, error_code="UPDATER_CUTOFF",
                          error_context=(self.records[-1]["stage"]
                                         if self.records else "NO_RECORD"))


def validate_terminal(records: list[dict], *, run_id: str, node: str,
                      expected_image_sha: str) -> str:
    machine = UpdaterStateMachine(run_id, node, expected_image_sha)
    expected = [stage for stage in STAGES if stage != "UPLOAD_PROGRESS"]
    observed: list[str] = []
    terminal = None
    for record in records:
        required = {"schema", "run_id", "target_node", "monotonic_timestamp",
                    "stage", "expected_image_sha", "observed_image_sha",
                    "progress_current", "progress_total", "error_code",
                    "error_context"}
        if not required.issubset(record):
            raise UpdaterEvidenceError("missing structured record field")
        if record["schema"] != SCHEMA or record["run_id"] != run_id:
            raise UpdaterEvidenceError("schema/run_id mismatch")
        if record["target_node"] != node:
            raise UpdaterEvidenceError("requested node mismatch")
        if record["expected_image_sha"] != expected_image_sha:
            raise UpdaterEvidenceError("expected SHA mismatch")
        stage = record["stage"]
        if terminal is not None:
            raise UpdaterEvidenceError("contradictory terminal records")
        if stage in TERMINALS:
            terminal = stage
        elif stage != "UPLOAD_PROGRESS":
            observed.append(stage)
    if terminal is None:
        raise UpdaterEvidenceError("non-terminal updater evidence")
    if terminal == "READY_FOR_CONFIRM":
        if observed != expected[:-1]:
            raise UpdaterEvidenceError("READY_FOR_CONFIRM missing predecessor")
        last = records[-1]
        if not (last.get("observed_image_sha") == expected_image_sha
                and last.get("secondary_verified") is True
                and last.get("pending_set") is True
                and last.get("reboot_queued") is True):
            raise UpdaterEvidenceError("incomplete READY_FOR_CONFIRM")
    return terminal


def capture_updater_terminal(*, run_id: str, node: str, expected_image_sha: str,
                             updater_cutoff: float, raw_path: Path,
                             parsed_path: Path, serial_number: int = 683234364,
                             rtt_address: int = 0x20002010,
                             clock: Callable[[], float] = time.monotonic,
                             transport=None) -> str:
    machine = UpdaterStateMachine(run_id, node, expected_image_sha)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    link = transport or JLinkRttTransport(serial_number=serial_number,
        device="NRF52840_XXAA", address=rtt_address)
    buffer = b""
    try:
        link.open()
        with raw_path.open("xb") as raw:
            while machine.terminal is None and clock() < updater_cutoff:
                data = link.read(4096)
                if not data:
                    time.sleep(0.01)
                    continue
                raw.write(data); raw.flush(); buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    machine.feed(line.decode("utf-8", "replace").rstrip("\r"), clock())
                    atomic_write(parsed_path, {"schema":"biospur-updater-capture-v1",
                        "run_id":run_id, "node":node,
                        "expected_image_sha":expected_image_sha,
                        "updater_cutoff":updater_cutoff,
                        "terminal":machine.terminal, "records":machine.records})
        if machine.terminal is None:
            machine.timeout(clock())
        validate_terminal(machine.records, run_id=run_id, node=node,
                          expected_image_sha=expected_image_sha)
        atomic_write(parsed_path, {"schema":"biospur-updater-capture-v1",
            "run_id":run_id, "node":node, "expected_image_sha":expected_image_sha,
            "updater_cutoff":updater_cutoff, "terminal":machine.terminal,
            "records":machine.records})
        return machine.terminal
    finally:
        link.close()
