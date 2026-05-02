#!/usr/bin/env python3
from __future__ import annotations

import glob
from pathlib import Path


BIO_SPUR_GLOB = "/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_*-if00"


def find_master_control_ports() -> list[str]:
    return sorted(glob.glob(BIO_SPUR_GLOB))


def preferred_master_control_port(fallback: str | None = None) -> str | None:
    ports = find_master_control_ports()
    if ports:
        return ports[0]
    return fallback


def assert_not_jlink_when_biospur_available(port: str) -> None:
    ports = find_master_control_ports()
    if not ports:
        return

    path = Path(port)
    if "SEGGER_J-Link_" in path.name:
        raise SystemExit(
            "Wrong master control port selected: "
            f"{port}. Active BLE control runtime is available on {ports[0]}."
        )
