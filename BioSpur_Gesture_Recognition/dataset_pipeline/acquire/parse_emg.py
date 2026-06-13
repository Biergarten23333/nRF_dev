from __future__ import annotations

import re
from typing import Any


RECV_HEX_RE = re.compile(
    r"^RECV_HEX\s+"
    r"mcu_ms=(?P<mcu_ms>\d+)\s+"
    r"type=(?P<type>\S+)\s+"
    r"seq=(?P<seq>\d+)\s+"
    r"dev=(?P<dev>0x[0-9A-Fa-f]+|\d+)\s+"
    r"ts=(?P<ts>\d+)\s+"
    r"len=(?P<len>\d+)\s+"
    r"samples=(?P<samples>\d+)\s+"
    r"mask=(?P<mask>0x[0-9A-Fa-f]+|\d+)\s+"
    r"rate=(?P<rate>\d+)\s+"
    r"data=(?P<data>[0-9A-Fa-f]+)"
)


def parse_recv_hex(line: str) -> dict[str, Any] | None:
    match = RECV_HEX_RE.match(line.strip())
    if not match:
        return None

    groups = match.groupdict()
    return {
        "type": groups["type"],
        "seq": int(groups["seq"]),
        "device_id": groups["dev"],
        "device_ts": int(groups["ts"]),
        "mcu_ms": int(groups["mcu_ms"]),
        "payload_len": int(groups["len"]),
        "samples": int(groups["samples"]),
        "channel_mask": groups["mask"],
        "sample_rate_sps": int(groups["rate"]),
        "data_hex": groups["data"].upper(),
    }


def emg_record_from_line(host_time_ns: int, line: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "host_time_ns": host_time_ns,
        "source": "b120",
        "line": line,
    }
    parsed = parse_recv_hex(line)
    if parsed is not None:
        record.update(parsed)
    return record
