#!/usr/bin/env python3
"""Threaded CDC drain with batched logging and live health watchdogs."""

from __future__ import annotations

import queue
import os
import threading
import time
from dataclasses import dataclass

from fusion_session import (
    FrameError,
    FrameStreamDecoder,
    LineChannel,
    SessionError,
    frame_to_line,
    serial,
)


@dataclass
class DrainHealth:
    decoded_queue_high_water: int = 0
    raw_backlog_high_water: int = 0
    log_queue_high_water: int = 0
    decoded_queue_drops: int = 0
    log_queue_drops: int = 0
    red_markers: int = 0
    reader_exceptions: int = 0
    raw_queue_high_water: int = 0
    raw_queue_drops: int = 0
    raw_bytes_submitted: int = 0
    raw_bytes_written: int = 0
    payload_decode_errors: int = 0


class _RawBinaryWriter:
    """Non-blocking reader-side tee; preserves bytes and COBS delimiters."""
    def __init__(self, raw_file, health: DrainHealth, queue_chunks: int = 65536):
        self.raw_file=raw_file;self.health=health
        self.items: queue.Queue[bytes]=queue.Queue(maxsize=queue_chunks)
        self.stop_event=threading.Event();self.error: Exception|None=None
        self.thread=threading.Thread(target=self._run,name="fusion-raw-writer",daemon=True);self.thread.start()
    def submit(self, data: bytes) -> bool:
        chunk=bytes(data);self.health.raw_bytes_submitted+=len(chunk)
        try:self.items.put_nowait(chunk)
        except queue.Full:
            self.health.raw_queue_drops+=1;return False
        self.health.raw_queue_high_water=max(self.health.raw_queue_high_water,self.items.qsize());return True
    def _run(self):
        try:
            while not self.stop_event.is_set() or not self.items.empty():
                try:first=self.items.get(timeout=.1)
                except queue.Empty:continue
                batch=[first];size=len(first)
                while size<1<<20:
                    try:b=self.items.get_nowait()
                    except queue.Empty:break
                    batch.append(b);size+=len(b)
                self.raw_file.write(b"".join(batch));self.health.raw_bytes_written+=size
        except Exception as exc:self.error=exc
    def close(self):
        self.stop_event.set();self.thread.join(timeout=15)
        if self.thread.is_alive():raise SessionError("raw writer did not stop")
        if self.error:raise SessionError(f"raw writer failed: {self.error}")
        self.raw_file.flush();os.fsync(self.raw_file.fileno())


class _BatchedLogWriter:
    def __init__(
        self,
        log_file,
        health: DrainHealth,
        *,
        queue_records: int = 65536,
        batch_records: int = 256,
        flush_interval_s: float = 0.25,
    ) -> None:
        self.log_file = log_file
        self.health = health
        self.batch_records = batch_records
        self.flush_interval_s = flush_interval_s
        self.items: queue.Queue[str] = queue.Queue(maxsize=queue_records)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="fusion-log-writer", daemon=True
        )
        self.thread.start()

    def submit(self, text: str) -> None:
        try:
            self.items.put_nowait(text)
        except queue.Full:
            self.health.log_queue_drops += 1
            return
        self.health.log_queue_high_water = max(
            self.health.log_queue_high_water, self.items.qsize()
        )

    def _run(self) -> None:
        batch: list[str] = []
        next_flush = time.monotonic() + self.flush_interval_s
        while (
            not self.stop_event.is_set()
            or not self.items.empty()
            or bool(batch)
        ):
            timeout = max(0.0, next_flush - time.monotonic())
            try:
                batch.append(self.items.get(timeout=timeout))
            except queue.Empty:
                pass
            while len(batch) < self.batch_records:
                try:
                    batch.append(self.items.get_nowait())
                except queue.Empty:
                    break
            now = time.monotonic()
            if batch and (
                len(batch) >= self.batch_records
                or now >= next_flush
                or (self.stop_event.is_set() and self.items.empty())
            ):
                self.log_file.write("".join(batch))
                self.log_file.flush()
                batch.clear()
                next_flush = now + self.flush_interval_s

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=10.0)
        if self.thread.is_alive():
            raise SessionError("batched log writer did not stop")
        self.log_file.flush()
        os.fsync(self.log_file.fileno())


class ThreadedLineChannel(LineChannel):
    """LineChannel whose only serial reader never performs file I/O."""

    def __init__(
        self,
        port: str,
        log_file,
        label: str,
        *,
        decoded_queue_records: int = 65536,
        backlog_red_records: int = 8192,
        raw_backlog_red_bytes: int = 8192,
        stall_red_s: float = 1.0,
        raw_file=None,
        raw_queue_chunks: int = 65536,
    ) -> None:
        self.health = DrainHealth()
        self._decoded: queue.Queue[str] = queue.Queue(
            maxsize=decoded_queue_records
        )
        self._backlog_red_records = backlog_red_records
        self._raw_backlog_red_bytes = raw_backlog_red_bytes
        self._stall_red_s = stall_red_s
        self._reader_stop = threading.Event()
        self._watchdog_stop = threading.Event()
        self._closed = False
        self._reader_heartbeat = time.monotonic()
        self._reader_stall_latched = False
        self._backlog_latched = False
        self._raw_backlog_latched = False
        self._raw_writer = (_RawBinaryWriter(raw_file,self.health,raw_queue_chunks)
                            if raw_file is not None else None)
        self._log_writer = _BatchedLogWriter(log_file, self.health)
        super().__init__(port, log_file, label)
        self._start_reader()
        self._watchdog = threading.Thread(
            target=self._watchdog_loop,
            name="fusion-drain-watchdog",
            daemon=True,
        )
        self._watchdog.start()

    def _open(self) -> None:
        if not self.port.startswith("/dev/pts/"):
            super()._open()
            return
        # PTYs used by the offline soak do not implement modem-control
        # ioctls. Production CDC paths retain LineChannel's strict DTR/RTS
        # handling above.
        self.device = serial.Serial()
        self.device.port = self.port
        self.device.baudrate = 115200
        self.device.timeout = 0.10
        self.device.write_timeout = 1.0
        self.device.open()

    def _record(self, direction: str, line: str) -> None:
        self._log_writer.submit(
            f"{time.time():.6f} {time.monotonic():.6f} "
            f"{self.label}_{direction} {line}\n"
        )

    def _note_red(self, kind: str, detail: str) -> None:
        self.health.red_markers += 1
        self._record("RED", f"HOST_DRAIN_RED kind={kind} {detail}")

    def _enqueue_decoded(self, line: str) -> None:
        # Archive the decoded record at the drain boundary.  The write itself
        # is queued to _BatchedLogWriter, so this remains non-blocking and a
        # slow consumer cannot create an unlogged interval.
        self._record("RX", line)
        try:
            self._decoded.put_nowait(line)
        except queue.Full:
            self.health.decoded_queue_drops += 1
            self._note_red(
                "decoded_queue_full",
                f"limit={self._decoded.maxsize}",
            )
            return
        depth = self._decoded.qsize()
        self.health.decoded_queue_high_water = max(
            self.health.decoded_queue_high_water, depth
        )
        if depth > self._backlog_red_records and not self._backlog_latched:
            self._backlog_latched = True
            self._note_red(
                "decoded_backlog",
                f"records={depth} threshold={self._backlog_red_records}",
            )
        elif depth < self._backlog_red_records // 2:
            self._backlog_latched = False

    def _consume(self, raw: bytes) -> None:
        if self.transport_mode is None:
            self.text_pending.extend(raw)
            if 0 in self.text_pending:
                self.transport_mode = "binary"
                raw = bytes(self.text_pending)
                self.text_pending.clear()
            elif b"\n" in self.text_pending:
                self.transport_mode = "text"
                raw = b""
            else:
                return
        if self.transport_mode == "binary":
            frames = self.binary_decoder.feed(raw)
            for frame in frames:
                try:
                    line = frame_to_line(frame)
                except FrameError as exc:
                    self.health.payload_decode_errors += 1
                    self._record("DECODE_ERROR", str(exc))
                    continue
                if line:
                    self._enqueue_decoded(line)
            return
        self.text_pending.extend(raw)
        while b"\n" in self.text_pending:
            record, _, remainder = self.text_pending.partition(b"\n")
            self.text_pending = bytearray(remainder)
            line = record.decode("utf-8", errors="replace").strip("\r")
            if line:
                self._enqueue_decoded(line)

    def _reader_loop(self) -> None:
        while not self._reader_stop.is_set():
            self._reader_heartbeat = time.monotonic()
            try:
                waiting = self.device.in_waiting
                self.health.raw_backlog_high_water = max(
                    self.health.raw_backlog_high_water, waiting
                )
                if (
                    waiting > self._raw_backlog_red_bytes
                    and not self._raw_backlog_latched
                ):
                    self._raw_backlog_latched = True
                    self._note_red(
                        "serial_input_backlog",
                        f"bytes={waiting} threshold={self._raw_backlog_red_bytes}",
                    )
                elif waiting < self._raw_backlog_red_bytes // 2:
                    self._raw_backlog_latched = False
                raw = self.device.read(max(1, min(16384, waiting)))
                self._reader_heartbeat = time.monotonic()
                if raw:
                    if self._raw_writer is not None and not self._raw_writer.submit(raw):
                        self._note_red("raw_queue_full",f"limit={self._raw_writer.items.maxsize}")
                    self._consume(raw)
            except Exception as exc:
                if self._reader_stop.is_set():
                    break
                self.health.reader_exceptions += 1
                self._note_red(
                    "reader_exception",
                    f"type={type(exc).__name__} text={exc}",
                )
                time.sleep(0.05)

    def _start_reader(self) -> None:
        self._reader_stop.clear()
        self._reader_heartbeat = time.monotonic()
        self._reader = threading.Thread(
            target=self._reader_loop,
            name="fusion-cdc-drain",
            daemon=True,
        )
        self._reader.start()

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop.wait(0.1):
            age = time.monotonic() - self._reader_heartbeat
            if age > self._stall_red_s and not self._reader_stall_latched:
                self._reader_stall_latched = True
                self._note_red(
                    "reader_stall",
                    f"age_s={age:.3f} threshold_s={self._stall_red_s:.3f}",
                )
            elif age <= self._stall_red_s / 2:
                self._reader_stall_latched = False

    def read(self, deadline: float) -> str | None:
        while time.monotonic() < deadline:
            timeout = min(0.1, max(0.0, deadline - time.monotonic()))
            try:
                line = self._decoded.get(timeout=timeout)
            except queue.Empty:
                continue
            return line
        return None

    def discard_pending(self, reason: str) -> dict[str, object]:
        discarded = 0
        kinds: dict[str, int] = {}
        while True:
            try:
                line = self._decoded.get_nowait()
            except queue.Empty:
                break
            discarded += 1
            kind = line.split(" ", 1)[0]
            kinds[kind] = kinds.get(kind, 0) + 1
        result = {
            "reason": reason,
            "discarded_records": discarded,
            "kinds": kinds,
            "monotonic": time.monotonic(),
        }
        self._record(
            "BOUNDARY",
            "HOST_DRAIN_BOUNDARY "
            f"reason={reason} discarded_records={discarded} kinds={kinds}",
        )
        return result

    def quiesce_reader_and_drain(self, reason: str) -> dict[str, object]:
        """Stop serial ingestion, then account for and empty decoded backlog."""
        self._reader_stop.set()
        if hasattr(self, "_reader"):
            self._reader.join(timeout=2.0)
        return self.discard_pending(reason)

    def health_snapshot(self) -> dict[str, object]:
        return {
            "decoded_queue_depth": self._decoded.qsize(),
            "decoded_queue_limit": self._decoded.maxsize,
            "decoded_queue_high_water": self.health.decoded_queue_high_water,
            "raw_backlog_high_water": self.health.raw_backlog_high_water,
            "log_queue_high_water": self.health.log_queue_high_water,
            "decoded_queue_drops": self.health.decoded_queue_drops,
            "log_queue_drops": self.health.log_queue_drops,
            "red_markers": self.health.red_markers,
            "reader_exceptions": self.health.reader_exceptions,
            "raw_queue_depth": self._raw_writer.items.qsize() if self._raw_writer else 0,
            "raw_queue_high_water": self.health.raw_queue_high_water,
            "raw_queue_drops": self.health.raw_queue_drops,
            "raw_bytes_submitted": self.health.raw_bytes_submitted,
            "raw_bytes_written": self.health.raw_bytes_written,
            "frame_crc_decode_errors": self.binary_decoder.errors,
            "payload_decode_errors": self.health.payload_decode_errors,
            "reader_heartbeat_age_s": time.monotonic()
            - self._reader_heartbeat,
            "backlog_red_threshold_records": self._backlog_red_records,
            "raw_backlog_red_threshold_bytes": self._raw_backlog_red_bytes,
            "stall_red_threshold_s": self._stall_red_s,
        }

    def reopen(self, timeout_s: float = 20.0) -> None:
        self._reader_stop.set()
        self._reader.join(timeout=2.0)
        try:
            self.device.close()
        except Exception:
            pass
        deadline = time.monotonic() + timeout_s
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self._open()
                self.binary_decoder = FrameStreamDecoder()
                self.text_pending.clear()
                self.transport_mode = None
                self.discard_pending("reopen")
                self._start_reader()
                self._record("REOPEN", f"port={self.port}")
                return
            except (OSError, serial.SerialException) as exc:
                last_error = exc
                time.sleep(0.2)
        raise SessionError(
            f"timed out reopening {self.label} CDC {self.port}: {last_error}"
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._reader_stop.set()
        if hasattr(self, "_reader"):
            self._reader.join(timeout=2.0)
        self._watchdog_stop.set()
        if hasattr(self, "_watchdog"):
            self._watchdog.join(timeout=2.0)
        if self.device is not None:
            self.device.close()
        self._log_writer.close()
        if self._raw_writer is not None:
            self._raw_writer.close()
