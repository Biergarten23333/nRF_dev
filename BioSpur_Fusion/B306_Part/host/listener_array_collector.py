#!/usr/bin/env python3
"""Read-only simultaneous collector for the seven installed UWB listeners.

The listener VCOM streams are opened by stable SEGGER SNR.  This tool never
writes to a serial port and never invokes a J-Link command.  Each byte stream
is archived independently; a central queue writes a merged JSONL index whose
listener identity is assigned before the record leaves its reader thread.

The frozen listener prints only the low 32 bits of the DW1000 RX timestamp.
TimestampUnwrapper uses the independently printed listener uptime (1 ms
resolution) to choose the correct number of 2**32 wraps.  The result is a
relative timestamp within a continuity segment, not a recovered raw 40-bit
counter.  A reported listener self-recovery starts a new continuity segment.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import queue
import signal
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, BinaryIO, TextIO


DW_TICKS_PER_SECOND = 499_200_000 * 128
DW_TICKS_PER_MS = DW_TICKS_PER_SECOND / 1000.0
DW_LO32_MODULUS = 1 << 32
DW_LO32_WRAP_SECONDS = DW_LO32_MODULUS / DW_TICKS_PER_SECOND

FORBIDDEN_SNRS = {"1050070698", "760185886"}


@dataclasses.dataclass(frozen=True)
class Listener:
    key: str
    snr: str
    position: str
    designated_beacon: bool = False

    @property
    def port(self) -> str:
        return f"/dev/serial/by-id/usb-SEGGER_J-Link_000{self.snr}-if00"


# The seven requested in Batch A: four anchor-pair midpoints plus the three
# vertical-profile positions.  The two later face listeners are not in scope.
LISTENERS: tuple[Listener, ...] = (
    Listener("LAE", "760184753", "A-E anchor-pair midpoint"),
    Listener("LBF", "760184548", "B-F anchor-pair midpoint"),
    Listener("LCG", "760181725", "C-G anchor-pair midpoint", True),
    Listener("LDH", "760184784", "D-H anchor-pair midpoint"),
    Listener("LLOW", "760184964", "vertical-profile LOW"),
    Listener("LMID", "760184767", "vertical-profile MID"),
    Listener("LHIGH", "760184545", "vertical-profile HIGH, about 2.3 m", True),
)

LISTENER_BY_SNR = {listener.snr: listener for listener in LISTENERS}


INTEGER_FIELDS = {
    "ver",
    "listener_id",
    "near_anchor_id",
    "listener_t_ms",
    "accepted_polls",
    "accepted_resps",
    "accepted_beacons",
    "beacons",
    "poll_seq",
    "resp_seq",
    "beacon_seq",
    "tag_id",
    "anchor_id",
    "beacon_index",
    "superframe_counter",
    "schedule_generation",
    "beacon_flags",
    "cycle_period_us",
    "tx_offset_us",
    "rx_ts_lo32",
    "carrier_integrator",
    "fp_index",
    "fp1",
    "fp2",
    "fp3",
    "cir_pwr",
    "rxpacc",
    "std_noise",
    "frame_len",
    "rcph",
    "rxtofs",
    "ttcki",
    "agc",
    "good_frames",
    "ignored_nonpoll",
    "ignored_poll_mask",
    "bad_header",
    "too_long",
    "rx_errors",
    "cir_captures",
    "ring_drops",
    "self_recover",
    "rx_enable_failures",
    "fps",
    "evc_fcg",
    "evc_fce",
    "evc_ovr",
    "evc_sto",
    "offset",
    "len",
    "acc_len",
    "tx_ok",
    "tx_miss",
    "missed_main",
    "rx_poll",
    "rx_resp",
    "rx_other",
}

HEX_FIELDS = {
    "src",
    "dst",
    "poll_mask",
    "last_status",
    "last_src",
    "last_dst",
    "last_code",
    "rx_ts40",
    "scheduled_tx40",
    "actual_tx40",
}


class ParseError(ValueError):
    """A recognized listener record did not match the frozen grammar."""


def _typed(field: str, value: str) -> Any:
    if field in INTEGER_FIELDS:
        return int(value, 10)
    if field in HEX_FIELDS:
        return int(value, 16)
    return value


def _parse_fixed_and_kv(
    parts: list[str], fields: tuple[str, ...], *, minimum_parts: int | None = None
) -> dict[str, Any]:
    needed = minimum_parts if minimum_parts is not None else len(fields) + 1
    if len(parts) < needed:
        raise ParseError(f"{parts[0]}: expected at least {needed} fields, got {len(parts)}")
    result: dict[str, Any] = {}
    for name, value in zip(fields, parts[1 : len(fields) + 1], strict=True):
        result[name] = _typed(name, value)
    for token in parts[len(fields) + 1 :]:
        if "=" not in token:
            raise ParseError(f"{parts[0]}: malformed extension {token!r}")
        name, value = token.split("=", 1)
        result[name] = _typed(name, value)
    return result


LPD_FIELDS = (
    "ver",
    "listener_id",
    "near_anchor_id",
    "listener_t_ms",
    "accepted_polls",
    "poll_seq",
    "tag_id",
    "src",
    "dst",
    "rx_ts_lo32",
    "carrier_integrator",
    "fp_index",
    "fp1",
    "fp2",
    "fp3",
    "cir_pwr",
    "rxpacc",
    "std_noise",
    "frame_len",
    "poll_mask",
)

LRD_FIELDS = (
    "ver",
    "listener_id",
    "near_anchor_id",
    "listener_t_ms",
    "accepted_resps",
    "resp_seq",
    "anchor_id",
    "src",
    "dst",
    "rx_ts_lo32",
    "carrier_integrator",
    "fp_index",
    "fp1",
    "fp2",
    "fp3",
    "cir_pwr",
    "rxpacc",
    "std_noise",
    "frame_len",
)

LBD_FIELDS = (
    "ver",
    "listener_id",
    "near_anchor_id",
    "listener_t_ms",
    "accepted_beacons",
    "beacon_seq",
    "beacon_index",
    "src",
    "dst",
    "rx_ts_lo32",
    "carrier_integrator",
    "superframe_counter",
    "schedule_generation",
    "beacon_flags",
    "cycle_period_us",
    "tx_offset_us",
    "fp_index",
    "fp1",
    "fp2",
    "fp3",
    "cir_pwr",
    "rxpacc",
    "std_noise",
    "frame_len",
)

LBTX_FIELDS = (
    "ver",
    "listener_id",
    "beacon_index",
    "superframe_counter",
    "schedule_generation",
    "beacon_flags",
    "cycle_period_us",
    "tx_offset_us",
)

LBSTAT_FIELDS = (
    "ver",
    "listener_id",
    "role",
    "cycle_period_us",
    "schedule_generation",
    "superframe_counter",
    "missed_main",
    "tx_ok",
    "tx_miss",
)

LCIRM_FIELDS = (
    "ver",
    "listener_id",
    "near_anchor_id",
    "accepted_polls",
    "poll_seq",
    "tag_id",
    "poll_mask",
    "rx_ts_lo32",
    "carrier_integrator",
    "fp_index",
    "fp1",
    "fp2",
    "fp3",
    "cir_pwr",
    "rxpacc",
    "acc_len",
)

LSTAT_FIELDS = (
    "ver",
    "listener_id",
    "near_anchor_id",
    "good_frames",
    "accepted_polls",
    "ignored_nonpoll",
    "ignored_poll_mask",
    "bad_header",
    "too_long",
    "rx_errors",
    "cir_captures",
    "last_status",
    "last_src",
    "last_dst",
    "last_code",
    "ring_drops",
    "self_recover",
    "rx_enable_failures",
    "fps",
)


def parse_listener_line(line: str) -> tuple[str, dict[str, Any]]:
    """Parse one frozen-listener line without assigning a source listener."""

    text = line.strip()
    if not text:
        return "EMPTY", {}
    parts = text.split(";")
    kind = parts[0]
    if kind == "LPD":
        return kind, _parse_fixed_and_kv(parts, LPD_FIELDS)
    if kind == "LRD":
        return kind, _parse_fixed_and_kv(parts, LRD_FIELDS)
    if kind == "LBD":
        return kind, _parse_fixed_and_kv(parts, LBD_FIELDS)
    if kind == "LBTX":
        return kind, _parse_fixed_and_kv(parts, LBTX_FIELDS)
    if kind == "LBSTAT":
        return kind, _parse_fixed_and_kv(parts, LBSTAT_FIELDS)
    if kind == "LCIRM":
        return kind, _parse_fixed_and_kv(parts, LCIRM_FIELDS)
    if kind == "LCIRD":
        if len(parts) != 6:
            raise ParseError(f"LCIRD: expected 6 fields, got {len(parts)}")
        return kind, {
            "ver": int(parts[1]),
            "accepted_polls": int(parts[2]),
            "offset": int(parts[3]),
            "len": int(parts[4]),
            "hex": parts[5],
        }
    if kind == "LCIRE":
        if len(parts) != 4:
            raise ParseError(f"LCIRE: expected 4 fields, got {len(parts)}")
        return kind, {
            "ver": int(parts[1]),
            "accepted_polls": int(parts[2]),
            "acc_len": int(parts[3]),
        }
    if kind == "LSTAT":
        return kind, _parse_fixed_and_kv(parts, LSTAT_FIELDS)
    if text.startswith("MODE="):
        return "MODE", {"text": text}
    if text.startswith("BioSpur co-located UWB listener start"):
        return "BANNER", {"text": text}
    if text.startswith("listener RX-only poll diagnostics ready"):
        return "READY", {"text": text}
    return "OTHER", {"text": text}


class TimestampUnwrapper:
    """Recover a relative DW timeline from low-32 RX ticks plus 1 ms uptime."""

    def __init__(self) -> None:
        self.segment = 0
        self.prev_raw: int | None = None
        self.prev_listener_ms: int | None = None
        self.prev_unwrapped: int | None = None
        self.residual_ticks_max = 0

    def new_segment(self) -> None:
        self.segment += 1
        self.prev_raw = None
        self.prev_listener_ms = None
        self.prev_unwrapped = None

    def add(self, raw: int, listener_ms: int) -> dict[str, int | float]:
        if not 0 <= raw < DW_LO32_MODULUS:
            raise ValueError(f"rx_ts_lo32 out of range: {raw}")
        if not 0 <= listener_ms < (1 << 32):
            raise ValueError(f"listener_t_ms out of range: {listener_ms}")

        if self.prev_raw is None:
            self.prev_raw = raw
            self.prev_listener_ms = listener_ms
            self.prev_unwrapped = raw
            return {
                "rx_unwrapped_ticks": raw,
                "rx_segment": self.segment,
                "lo32_extra_wraps": 0,
                "unwrap_residual_ticks": 0,
                "unwrap_residual_ns": 0.0,
                "unwrap_choice_margin_ns": None,
            }

        assert self.prev_listener_ms is not None
        assert self.prev_unwrapped is not None
        dt_ms = (listener_ms - self.prev_listener_ms) & 0xFFFFFFFF
        raw_delta = (raw - self.prev_raw) & 0xFFFFFFFF
        expected = dt_ms * DW_TICKS_PER_MS
        extra_wraps = max(0, round((expected - raw_delta) / DW_LO32_MODULUS))
        delta = raw_delta + extra_wraps * DW_LO32_MODULUS
        residual_ticks = int(round(delta - expected))
        # The adjacent wrap choice changes the residual magnitude by this
        # margin.  A small value means the low-32 reconstruction is intrinsically
        # close to ambiguous; downstream analysis must cross-check other
        # listeners instead of silently trusting/averaging that point.
        choice_margin_ticks = max(
            0, DW_LO32_MODULUS - 2 * abs(residual_ticks)
        )
        unwrapped = self.prev_unwrapped + delta

        self.prev_raw = raw
        self.prev_listener_ms = listener_ms
        self.prev_unwrapped = unwrapped
        self.residual_ticks_max = max(self.residual_ticks_max, abs(residual_ticks))
        return {
            "rx_unwrapped_ticks": unwrapped,
            "rx_segment": self.segment,
            "lo32_extra_wraps": extra_wraps,
            "unwrap_residual_ticks": residual_ticks,
            "unwrap_residual_ns": residual_ticks * 1e9 / DW_TICKS_PER_SECOND,
            "unwrap_choice_margin_ns": (
                choice_margin_ticks * 1e9 / DW_TICKS_PER_SECOND
            ),
        }


def make_archive_record(
    listener: Listener,
    source_record_index: int,
    raw_line: bytes,
    arrival_epoch_ns: int,
    arrival_monotonic_ns: int,
    unwrapper: TimestampUnwrapper,
) -> dict[str, Any]:
    decoded = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
    record: dict[str, Any] = {
        "listener_key": listener.key,
        "listener_snr": listener.snr,
        "source_record_index": source_record_index,
        "arrival_epoch_ns": arrival_epoch_ns,
        "arrival_monotonic_ns": arrival_monotonic_ns,
        "raw": decoded,
    }
    try:
        kind, parsed = parse_listener_line(decoded)
        record["kind"] = kind
        record["parsed_ok"] = True
        record["fields"] = parsed
        if kind in {"LPD", "LRD", "LBD"}:
            record.update(
                unwrapper.add(parsed["rx_ts_lo32"], parsed["listener_t_ms"])
            )
    except (ParseError, ValueError) as exc:
        record["kind"] = decoded.split(";", 1)[0] if decoded else "EMPTY"
        record["parsed_ok"] = False
        record["parse_error"] = str(exc)
        record["fields"] = {}
    return record


def make_index_record(record: dict[str, Any]) -> dict[str, Any]:
    """Produce the compact merged-index row; identity is copied, never inferred."""

    fields = record.get("fields", {})
    return {
        "listener_key": record["listener_key"],
        "listener_snr": record["listener_snr"],
        "source_record_index": record["source_record_index"],
        "arrival_epoch_ns": record["arrival_epoch_ns"],
        "arrival_monotonic_ns": record["arrival_monotonic_ns"],
        "kind": record["kind"],
        "parsed_ok": record["parsed_ok"],
        "listener_t_ms": fields.get("listener_t_ms"),
        "rx_ts_lo32": fields.get("rx_ts_lo32"),
        "rx_unwrapped_ticks": record.get("rx_unwrapped_ticks"),
        "rx_segment": record.get("rx_segment"),
        "lo32_extra_wraps": record.get("lo32_extra_wraps"),
        "unwrap_residual_ns": record.get("unwrap_residual_ns"),
        "unwrap_choice_margin_ns": record.get("unwrap_choice_margin_ns"),
        "src": fields.get("src"),
        "dst": fields.get("dst"),
        "sequence": fields.get(
            "poll_seq", fields.get("resp_seq", fields.get("beacon_seq"))
        ),
    }


@dataclasses.dataclass
class ReaderStats:
    listener_key: str
    listener_snr: str
    port: str
    bytes_read: int = 0
    records: int = 0
    incomplete_bytes: int = 0
    parse_errors: int = 0
    serial_errors: int = 0
    max_in_waiting: int = 0
    kinds: Counter[str] = dataclasses.field(default_factory=Counter)
    first_lstat: dict[str, Any] | None = None
    last_lstat: dict[str, Any] | None = None
    last_self_recover: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result = dataclasses.asdict(self)
        result["kinds"] = dict(self.kinds)
        if self.first_lstat is not None and self.last_lstat is not None:
            result["firmware_counter_delta"] = {
                key: self.last_lstat.get(key, 0) - self.first_lstat.get(key, 0)
                for key in (
                    "good_frames",
                    "accepted_polls",
                    "rx_errors",
                    "ring_drops",
                    "self_recover",
                    "rx_enable_failures",
                )
            }
            result["event_counter_delta_mod4096"] = {
                key: (
                    self.last_lstat.get(key, 0) - self.first_lstat.get(key, 0)
                )
                % 4096
                for key in ("evc_fcg", "evc_fce", "evc_ovr", "evc_sto")
            }
        else:
            result["firmware_counter_delta"] = None
            result["event_counter_delta_mod4096"] = None
        return result


def open_read_only_serial(port: str, baud: int):
    """Open VCOM with DTR/RTS low before open; no write API is used."""

    import serial

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baud
    ser.timeout = 0.1
    ser.write_timeout = 0
    ser.exclusive = True
    ser.dsrdtr = False
    ser.rtscts = False
    ser.dtr = False
    ser.rts = False
    ser.open()
    ser.dtr = False
    ser.rts = False
    return ser


def reader_worker(
    listener: Listener,
    ser: Any,
    raw_file: BinaryIO,
    parsed_file: TextIO,
    output_queue: queue.Queue[dict[str, Any]],
    deadline_monotonic: float,
    stop_event: threading.Event,
    stats: ReaderStats,
) -> None:
    buffer = bytearray()
    unwrapper = TimestampUnwrapper()
    try:
        while not stop_event.is_set() and time.monotonic() < deadline_monotonic:
            try:
                waiting = int(getattr(ser, "in_waiting", 0))
                stats.max_in_waiting = max(stats.max_in_waiting, waiting)
                chunk = ser.read(max(1, min(4096, waiting or 1)))
            except Exception as exc:  # serial implementation supplies exact type
                stats.serial_errors += 1
                stats.error = f"{type(exc).__name__}: {exc}"
                stop_event.set()
                break
            if not chunk:
                continue
            arrival_epoch_ns = time.time_ns()
            arrival_monotonic_ns = time.monotonic_ns()
            stats.bytes_read += len(chunk)
            raw_file.write(chunk)
            raw_file.flush()
            buffer.extend(chunk)
            while True:
                newline = buffer.find(b"\n")
                if newline < 0:
                    break
                line = bytes(buffer[: newline + 1])
                del buffer[: newline + 1]
                stats.records += 1
                record = make_archive_record(
                    listener,
                    stats.records,
                    line,
                    arrival_epoch_ns,
                    arrival_monotonic_ns,
                    unwrapper,
                )
                stats.kinds[record["kind"]] += 1
                if not record["parsed_ok"]:
                    stats.parse_errors += 1
                if record["kind"] == "LSTAT" and record["parsed_ok"]:
                    fields = record["fields"]
                    if stats.first_lstat is None:
                        stats.first_lstat = dict(fields)
                    stats.last_lstat = dict(fields)
                    current_recover = fields.get("self_recover")
                    if (
                        stats.last_self_recover is not None
                        and current_recover is not None
                        and current_recover > stats.last_self_recover
                    ):
                        unwrapper.new_segment()
                    stats.last_self_recover = current_recover
                parsed_file.write(json.dumps(record, separators=(",", ":")) + "\n")
                # The Batch-A runner uses a parsed LSTAT from every stream as
                # its pre-CFG liveness gate.  Make that gate externally visible
                # immediately without forcing a disk flush for every CIR row.
                if record["kind"] == "LSTAT":
                    parsed_file.flush()
                output_queue.put(make_index_record(record))
        if buffer:
            stats.incomplete_bytes = len(buffer)
    finally:
        parsed_file.flush()
        raw_file.flush()
        os.fsync(parsed_file.fileno())
        os.fsync(raw_file.fileno())


def validate_roster() -> None:
    snrs = [listener.snr for listener in LISTENERS]
    if len(snrs) != 7 or len(set(snrs)) != 7:
        raise RuntimeError("listener roster must contain seven unique SNRs")
    bad = set(snrs) & FORBIDDEN_SNRS
    if bad:
        raise RuntimeError(f"forbidden SNR in listener roster: {sorted(bad)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture the seven frozen passive UWB listener VCOM streams."
    )
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--duration", required=True, type=float)
    parser.add_argument("--baud", type=int, default=460800)
    parser.add_argument(
        "--require-kind",
        action="append",
        default=[],
        help="Fail unless every listener emits this record kind (repeatable).",
    )
    return parser.parse_args()


def inventory_payload(baud: int) -> dict[str, Any]:
    return {
        "firmware_marker": "Batch C role-specific; read marker from LSTAT",
        "firmware_hex_sha256": None,
        "baud": baud,
        "timestamp_output": "DW1000 RX timestamp low 32 bits",
        "dw_tick_hz": DW_TICKS_PER_SECOND,
        "lo32_wrap_seconds": DW_LO32_WRAP_SECONDS,
        "listeners": [dataclasses.asdict(listener) | {"port": listener.port} for listener in LISTENERS],
    }


def main() -> int:
    args = parse_args()
    validate_roster()
    if args.duration <= 0:
        raise SystemExit("--duration must be positive")
    if args.out_dir.exists() and any(args.out_dir.iterdir()):
        raise SystemExit(f"refusing non-empty output directory: {args.out_dir}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    listener_dir = args.out_dir / "listeners"
    listener_dir.mkdir()

    (args.out_dir / "inventory.json").write_text(
        json.dumps(inventory_payload(args.baud), indent=2) + "\n",
        encoding="utf-8",
    )

    serials: dict[str, Any] = {}
    files: list[Any] = []
    try:
        # Open all seven before establishing the formal start/deadline.
        for listener in LISTENERS:
            if listener.snr in FORBIDDEN_SNRS:
                raise RuntimeError(f"refusing forbidden SNR {listener.snr}")
            if not os.path.exists(listener.port):
                raise RuntimeError(
                    f"missing stable VCOM for {listener.key} SNR={listener.snr}: "
                    f"{listener.port}"
                )
            serials[listener.snr] = open_read_only_serial(listener.port, args.baud)

        start_epoch_ns = time.time_ns()
        start_monotonic = time.monotonic()
        deadline = start_monotonic + args.duration
        stop_event = threading.Event()

        def stop_handler(_signum: int, _frame: Any) -> None:
            stop_event.set()

        signal.signal(signal.SIGINT, stop_handler)
        signal.signal(signal.SIGTERM, stop_handler)

        output_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        stats_by_snr: dict[str, ReaderStats] = {}
        threads: list[threading.Thread] = []

        for listener in LISTENERS:
            raw_file = (listener_dir / f"{listener.snr}.raw.log").open("wb")
            parsed_file = (listener_dir / f"{listener.snr}.jsonl").open(
                "w", encoding="utf-8"
            )
            files.extend((raw_file, parsed_file))
            stats = ReaderStats(listener.key, listener.snr, listener.port)
            stats_by_snr[listener.snr] = stats
            thread = threading.Thread(
                target=reader_worker,
                args=(
                    listener,
                    serials[listener.snr],
                    raw_file,
                    parsed_file,
                    output_queue,
                    deadline,
                    stop_event,
                    stats,
                ),
                name=f"listener-{listener.snr}",
            )
            threads.append(thread)
            thread.start()

        merged_count = 0
        merged_path = args.out_dir / "merged_index.jsonl"
        with merged_path.open("w", encoding="utf-8") as merged:
            while any(thread.is_alive() for thread in threads) or not output_queue.empty():
                try:
                    record = output_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                merged.write(json.dumps(record, separators=(",", ":")) + "\n")
                merged_count += 1
            merged.flush()
            os.fsync(merged.fileno())

        for thread in threads:
            thread.join()

        end_epoch_ns = time.time_ns()
        result = {
            "start_epoch_ns": start_epoch_ns,
            "end_epoch_ns": end_epoch_ns,
            "requested_duration_s": args.duration,
            "actual_duration_s": (end_epoch_ns - start_epoch_ns) / 1e9,
            "merged_records": merged_count,
            "listeners": {
                snr: stats.as_dict() for snr, stats in stats_by_snr.items()
            },
        }
        failures: list[str] = []
        for snr, stats in stats_by_snr.items():
            if stats.error:
                failures.append(f"{snr}: {stats.error}")
            for required in args.require_kind:
                if stats.kinds[required] == 0:
                    failures.append(f"{snr}: missing required kind {required}")
        result["acceptance_failures"] = failures
        result["pass"] = not failures
        (args.out_dir / "summary.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        return 0 if not failures else 2
    finally:
        for ser in serials.values():
            try:
                ser.close()
            except Exception:
                pass
        for handle in files:
            try:
                handle.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
