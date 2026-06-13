from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from serial.tools import list_ports


B120_BY_ID_DEFAULT = (
    "/dev/serial/by-id/usb-BioSpur-GR_BioSpur-GR_51D4A5716A4C5551-if00"
)
B120_VID = 0x2FE3
B120_PID = 0x10F2
B120_SERIAL = "51D4A5716A4C5551"
B120_PRODUCT_HINTS = ("BioSpur-GR", "GR-Master")

GLOVE_PORT_HINT = "/dev/ttyUSB0"
GLOVE_VID = 0x1A86
GLOVE_PID = 0x7523


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str
    hwid: str
    vid: int | None
    pid: int | None
    serial_number: str | None
    manufacturer: str | None
    product: str | None


def list_serial_ports() -> list[PortInfo]:
    ports: list[PortInfo] = []
    for p in list_ports.comports():
        ports.append(
            PortInfo(
                device=p.device,
                description=p.description or "",
                hwid=p.hwid or "",
                vid=p.vid,
                pid=p.pid,
                serial_number=p.serial_number,
                manufacturer=p.manufacturer,
                product=p.product,
            )
        )
    return ports


def format_ports(ports: Iterable[PortInfo] | None = None) -> str:
    rows = []
    for p in ports if ports is not None else list_serial_ports():
        vid_pid = (
            f"{p.vid:04X}:{p.pid:04X}" if p.vid is not None and p.pid is not None else "----:----"
        )
        rows.append(
            f"{p.device} desc={p.description!r} vidpid={vid_pid} "
            f"serial={p.serial_number!r} product={p.product!r}"
        )
    return "\n".join(rows)


def _existing(path: str) -> str | None:
    return path if Path(path).exists() else None


def resolve_b120_port(explicit: str | None = None) -> str:
    if explicit:
        found = _existing(explicit)
        if found:
            return found
        raise FileNotFoundError(f"B120 explicit port does not exist: {explicit}")

    found = _existing(B120_BY_ID_DEFAULT)
    if found:
        return found

    for p in list_serial_ports():
        if p.vid == B120_VID and p.pid == B120_PID and p.serial_number == B120_SERIAL:
            return p.device
        text = " ".join(
            part or "" for part in (p.description, p.product, p.manufacturer, p.hwid)
        )
        if p.vid == B120_VID and p.pid == B120_PID and any(
            hint in text for hint in B120_PRODUCT_HINTS
        ):
            return p.device

    raise FileNotFoundError(
        "Could not find BioSpur-GR B120 port. Known ports:\n" + format_ports()
    )


def resolve_glove_port(explicit: str | None = None) -> str:
    if explicit:
        found = _existing(explicit)
        if found:
            return found
        raise FileNotFoundError(f"Glove explicit port does not exist: {explicit}")

    found = _existing(GLOVE_PORT_HINT)
    if found:
        return found

    for p in list_serial_ports():
        if p.vid == GLOVE_VID and p.pid == GLOVE_PID:
            return p.device

    raise FileNotFoundError(
        "Could not find ACEBOTT/CH340 glove port. Known ports:\n" + format_ports()
    )
