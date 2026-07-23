#!/usr/bin/env python3
"""Explicit-probe bidirectional SEGGER RTT transport."""

from __future__ import annotations

import time

try:
    import pylink
except ImportError:  # Offline parser tests do not require pylink.
    pylink = None


class JLinkRttError(RuntimeError):
    pass


class JLinkRttTransport:
    def __init__(
        self,
        *,
        serial_number: int,
        device: str,
        address: int,
        speed_khz: int = 4000,
        up_channel: int = 0,
        down_channel: int = 0,
    ):
        if pylink is None:
            raise JLinkRttError(
                "pylink is required for RTT hardware operation; "
                "use /usr/bin/python3 on this workstation"
            )
        self.serial_number = serial_number
        self.device = device
        self.address = address
        self.speed_khz = speed_khz
        self.up_channel = up_channel
        self.down_channel = down_channel
        self.probe = pylink.JLink()
        self.started = False

    def open(self, *, reset_target: bool = False, timeout_s: float = 5.0) -> None:
        self.probe.open(serial_no=self.serial_number)
        self.probe.set_tif(pylink.enums.JLinkInterfaces.SWD)
        self.probe.connect(self.device, speed=self.speed_khz, verbose=False)
        if reset_target:
            self.probe.reset(halt=False)
        self.probe.rtt_start(block_address=self.address)
        deadline = time.monotonic() + timeout_s
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                up_count = self.probe.rtt_get_num_up_buffers()
                down_count = self.probe.rtt_get_num_down_buffers()
                if (
                    up_count > self.up_channel
                    and down_count > self.down_channel
                ):
                    self.started = True
                    return
            except pylink.errors.JLinkRTTException as exc:
                last_error = exc
            time.sleep(0.05)
        suffix = f": {last_error}" if last_error is not None else ""
        raise JLinkRttError(
            f"RTT buffers unavailable at 0x{self.address:08x} "
            f"(up={self.up_channel}, down={self.down_channel}){suffix}"
        )

    def read(self, max_bytes: int = 4096) -> bytes:
        if not self.started:
            raise JLinkRttError("RTT transport is not open")
        return bytes(self.probe.rtt_read(self.up_channel, max_bytes))

    def write(self, data: bytes, *, timeout_s: float = 2.0) -> None:
        if not self.started:
            raise JLinkRttError("RTT transport is not open")
        offset = 0
        deadline = time.monotonic() + timeout_s
        while offset < len(data):
            written = self.probe.rtt_write(
                self.down_channel, list(data[offset:])
            )
            if written > 0:
                offset += written
                continue
            if time.monotonic() >= deadline:
                raise JLinkRttError(
                    f"RTT down-buffer write timed out after "
                    f"{offset}/{len(data)} bytes"
                )
            time.sleep(0.005)

    def write_line(self, line: str, *, timeout_s: float = 2.0) -> None:
        payload = (line.rstrip("\r\n") + "\n").encode("utf-8")
        self.write(payload, timeout_s=timeout_s)

    def close(self) -> None:
        try:
            if self.started:
                self.probe.rtt_stop()
        except Exception:
            pass
        self.started = False
        try:
            self.probe.close()
        except Exception:
            pass
