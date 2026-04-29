#!/usr/bin/env python3
from __future__ import annotations

import re


FORBIDDEN_PREFIX = "6"
REQUIRED_PREFIX = "7"
FORBIDDEN_KNOWN_SNR = "683234364"


def validate_anchor_probe_snr(snr: str, context: str) -> str:
    value = (snr or "").strip()
    if not value:
        raise ValueError(f"{context}: probe serial is empty")
    if not value.isdigit():
        raise ValueError(f"{context}: probe serial must be numeric, got '{value}'")
    norm = value.lstrip("0") or "0"
    if norm.startswith(FORBIDDEN_PREFIX):
        raise ValueError(
            f"{context}: forbidden probe serial '{value}' (6xxxxxx blocked; nRF52840 path is excluded)"
        )
    if not norm.startswith(REQUIRED_PREFIX):
        raise ValueError(
            f"{context}: probe serial '{value}' is not an anchor probe (expected 7xxxxxx)"
        )
    return norm


def validate_anchor_serial_port(port: str, context: str) -> str:
    value = (port or "").strip()
    if not value:
        raise ValueError(f"{context}: serial port is empty")
    # Anchor test policy only allows J-Link by-id paths that encode 7xxxxxx probe IDs.
    if value.startswith("/dev/serial/by-id/"):
        match = re.search(r"SEGGER_J-Link_(\d+)-if\d+", value)
        if match:
            validate_anchor_probe_snr(match.group(1), f"{context} (port)")
        else:
            raise ValueError(
                f"{context}: expected /dev/serial/by-id/usb-SEGGER_J-Link_0007xxxxxx-if00, got '{value}'"
            )
    return value


def assert_no_forbidden_master_flash_cmd(cmd: list[str], context: str) -> None:
    text = " ".join(cmd)
    if "flash_master_noninteractive.sh" in text:
        raise ValueError(
            f"{context}: forbidden command path detected: flash_master_noninteractive.sh"
        )
    if FORBIDDEN_KNOWN_SNR in text:
        raise ValueError(
            f"{context}: forbidden master probe serial {FORBIDDEN_KNOWN_SNR} detected in command"
        )
