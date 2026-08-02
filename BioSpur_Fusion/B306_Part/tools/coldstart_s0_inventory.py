#!/usr/bin/env python3
"""Read-only stable-identity inventory for the complete Fusion cold start.

This tool deliberately has no serial-write path.  It archives the current
by-id/USB/process state and passively decodes the two B120 control streams and
the Fusion Master host-binary stream.  Listener VCOMs are handled by
``listener_array_collector.py`` so each endpoint has exactly one owner.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import serial
from serial.tools import list_ports

from fusion_host_binary import FrameStreamDecoder, frame_to_line


ROOT = Path(__file__).resolve().parents[2]
BY_ID = Path("/dev/serial/by-id")
EXPECTED_LISTENERS = {
    "760181725",
    "760184545",
    "760184548",
    "760184753",
    "760184767",
    "760184784",
    "760184964",
}
FORBIDDEN_SNR = "001057782457"
TAG_PORT = BY_ID / "usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00"
ANCHOR_PORT = (
    BY_ID / "usb-Master_Anchor_Master_Anchor_Control_87EA2F4A526C5A02-if00"
)
FUSION_PORT = (
    BY_ID / "usb-BioSpur_BioSpur_Fusion_Master_8D3AC42D4D90FAE8-if00"
)
EXPECTED_TAG_MARKER = "master-tag-carrier-v2-fix12-relay7"
EXPECTED_FUSION_MARKER = "dk-fusion-imu-relay-v25"


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(
        timespec="milliseconds"
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def run_read_only(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        command, check=False, capture_output=True, text=True
    )
    return {
        "command": command,
        "return_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def open_read_only(port: Path, baud: int) -> serial.Serial:
    handle = serial.Serial()
    handle.port = str(port)
    handle.baudrate = baud
    handle.timeout = 0.1
    handle.write_timeout = 0
    handle.exclusive = True
    handle.dsrdtr = False
    handle.rtscts = False
    handle.dtr = False
    handle.rts = False
    handle.open()
    handle.dtr = False
    handle.rts = False
    return handle


def read_text_port(
    name: str,
    port: Path,
    duration_s: float,
    output_dir: Path,
) -> dict[str, Any]:
    raw_path = output_dir / f"{name}.raw.bin"
    line_path = output_dir / f"{name}.lines.log"
    lines: list[str] = []
    byte_count = 0
    started = time.monotonic()
    buffer = bytearray()
    error: str | None = None
    try:
        handle = open_read_only(port, 115200)
        try:
            with raw_path.open("wb") as raw, line_path.open(
                "w", encoding="utf-8", buffering=1
            ) as text:
                text.write(
                    f"# OPEN utc={utc_now()} port={port} "
                    f"resolved={port.resolve()} baud=115200 DTR=0 RTS=0 READ_ONLY=1\n"
                )
                while time.monotonic() - started < duration_s:
                    chunk = handle.read(max(1, min(4096, handle.in_waiting or 1)))
                    if not chunk:
                        continue
                    byte_count += len(chunk)
                    raw.write(chunk)
                    raw.flush()
                    buffer.extend(chunk)
                    while b"\n" in buffer:
                        raw_line, _, remainder = buffer.partition(b"\n")
                        buffer = bytearray(remainder)
                        line = raw_line.decode(
                            "utf-8", errors="replace"
                        ).rstrip("\r")
                        lines.append(line)
                        text.write(f"{utc_now()} {line}\n")
        finally:
            handle.close()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return {
        "name": name,
        "port": str(port),
        "resolved": str(port.resolve()) if port.exists() else None,
        "baud": 115200,
        "dtr": False,
        "rts": False,
        "read_only": True,
        "duration_s": time.monotonic() - started,
        "bytes": byte_count,
        "lines": len(lines),
        "sample_lines": lines[:30],
        "tag_marker_seen": any(EXPECTED_TAG_MARKER in line for line in lines),
        "error": error,
    }


def read_fusion_port(
    duration_s: float, output_dir: Path
) -> dict[str, Any]:
    raw_path = output_dir / "fusion_master.raw.bin"
    line_path = output_dir / "fusion_master.decoded.log"
    decoder = FrameStreamDecoder()
    frames = 0
    kinds: Counter[int] = Counter()
    decoded_lines: list[str] = []
    byte_count = 0
    started = time.monotonic()
    error: str | None = None
    try:
        handle = open_read_only(FUSION_PORT, 115200)
        try:
            with raw_path.open("wb") as raw, line_path.open(
                "w", encoding="utf-8", buffering=1
            ) as text:
                text.write(
                    f"# OPEN utc={utc_now()} port={FUSION_PORT} "
                    f"resolved={FUSION_PORT.resolve()} baud=115200 "
                    "DTR=0 RTS=0 READ_ONLY=1\n"
                )
                while time.monotonic() - started < duration_s:
                    chunk = handle.read(max(1, min(4096, handle.in_waiting or 1)))
                    if not chunk:
                        continue
                    byte_count += len(chunk)
                    raw.write(chunk)
                    raw.flush()
                    for frame in decoder.feed(chunk):
                        frames += 1
                        kinds[frame.kind] += 1
                        try:
                            line = frame_to_line(frame)
                        except Exception as exc:
                            line = (
                                f"DECODE_ERROR kind={frame.kind} "
                                f"error={type(exc).__name__}:{exc}"
                            )
                        decoded_lines.append(line)
                        text.write(f"{utc_now()} {line}\n")
        finally:
            handle.close()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return {
        "name": "fusion_master",
        "port": str(FUSION_PORT),
        "resolved": (
            str(FUSION_PORT.resolve()) if FUSION_PORT.exists() else None
        ),
        "baud": 115200,
        "dtr": False,
        "rts": False,
        "read_only": True,
        "duration_s": time.monotonic() - started,
        "bytes": byte_count,
        "frames": frames,
        "frame_kinds": dict(sorted(kinds.items())),
        "decoder_errors": decoder.errors,
        "sample_lines": decoded_lines[:30],
        "marker_seen": any(
            EXPECTED_FUSION_MARKER in line for line in decoded_lines
        ),
        "error": error,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=15.0)
    args = parser.parse_args()
    if args.duration_s <= 0:
        parser.error("--duration-s must be positive")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise SystemExit(f"refusing non-empty output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    started_utc = utc_now()
    by_id: dict[str, str] = {}
    if BY_ID.is_dir():
        for entry in sorted(BY_ID.iterdir()):
            if entry.is_symlink():
                by_id[entry.name] = os.readlink(entry)

    ports = []
    for info in sorted(list_ports.comports(), key=lambda item: item.device):
        ports.append(
            {
                "device": info.device,
                "description": info.description,
                "product": info.product,
                "manufacturer": info.manufacturer,
                "serial_number": info.serial_number,
                "vid": None if info.vid is None else f"{info.vid:04X}",
                "pid": None if info.pid is None else f"{info.pid:04X}",
                "location": info.location,
            }
        )

    static = {
        "started_utc": started_utc,
        "by_id": by_id,
        "serial_ports": ports,
        "processes": run_read_only(["ps", "-eo", "pid=,args="]),
        "holders": run_read_only(
            ["fuser", "-v"]
            + [str(Path("/dev") / f"ttyACM{index}") for index in range(128)]
        ),
    }
    write_json(args.output_dir / "static_inventory.json", static)

    results: dict[str, dict[str, Any]] = {}
    workers = {
        "tag_master": lambda: read_text_port(
            "tag_master", TAG_PORT, args.duration_s, args.output_dir
        ),
        "anchor_master": lambda: read_text_port(
            "anchor_master", ANCHOR_PORT, args.duration_s, args.output_dir
        ),
        "fusion_master": lambda: read_fusion_port(
            args.duration_s, args.output_dir
        ),
    }

    def run_worker(key: str) -> None:
        results[key] = workers[key]()

    threads = [
        threading.Thread(target=run_worker, args=(key,), name=f"s0-{key}")
        for key in workers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    listener_names = {
        name.removeprefix("usb-SEGGER_J-Link_000").removesuffix("-if00")
        for name in by_id
        if name.startswith("usb-SEGGER_J-Link_000") and name.endswith("-if00")
    }
    fusion_usb = [
        port
        for port in ports
        if port["vid"] == "2FE3" and port["pid"] == "10F4"
    ]
    failures: list[str] = []
    missing_listeners = sorted(EXPECTED_LISTENERS - listener_names)
    if missing_listeners:
        failures.append(f"missing listener SNRs: {missing_listeners}")
    if FORBIDDEN_SNR in listener_names or any(
        FORBIDDEN_SNR in name for name in by_id
    ):
        failures.append(f"forbidden SNR present: {FORBIDDEN_SNR}")
    for required in (TAG_PORT, ANCHOR_PORT, FUSION_PORT):
        if not required.exists():
            failures.append(f"missing stable endpoint: {required}")
    if len(fusion_usb) != 1:
        failures.append(
            f"Fusion Master VID:PID 2FE3:10F4 count={len(fusion_usb)}"
        )
    for key, result in results.items():
        if result.get("error"):
            failures.append(f"{key}: {result['error']}")
    if results.get("anchor_master", {}).get("lines", 0) == 0:
        failures.append("Anchor Master produced no passive decoded line")
    if results.get("tag_master", {}).get("lines", 0) == 0:
        failures.append("Tag Master produced no passive decoded line")
    if not results.get("tag_master", {}).get("tag_marker_seen", False):
        failures.append(
            f"Tag Master marker not seen passively: {EXPECTED_TAG_MARKER}"
        )
    if results.get("fusion_master", {}).get("frames", 0) == 0:
        failures.append("Fusion Master produced no valid host-binary frame")
    if results.get("fusion_master", {}).get("decoder_errors", 0):
        failures.append(
            "Fusion Master host-binary decoder recorded framing/CRC errors"
        )

    summary = {
        "started_utc": started_utc,
        "ended_utc": utc_now(),
        "read_only": True,
        "expected_listener_snrs": sorted(EXPECTED_LISTENERS),
        "found_expected_listener_snrs": sorted(
            EXPECTED_LISTENERS & listener_names
        ),
        "forbidden_snr": FORBIDDEN_SNR,
        "fusion_usb_matches": fusion_usb,
        "results": results,
        "failures": failures,
        "status": "PASS" if not failures else "FAIL",
    }
    write_json(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
