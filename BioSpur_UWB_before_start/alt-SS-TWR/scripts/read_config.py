#!/usr/bin/env python3
from __future__ import annotations

import argparse
import binascii
import json
import struct
import subprocess
import tempfile
from pathlib import Path

CONFIG_MAGIC = 0xB105F00D
SCHEMA_VERSION_MAX = 2
CONFIG_ADDR_DEFAULT = 0x0007E000

ROLE_NAME = {
    0: "unset",
    1: "master",
    2: "matrix",
    3: "responder",
}


def preflight_probe_env() -> None:
    subprocess.run(["pkill", "-f", "nrfutil-device --json list --hotplug"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return (proc.stdout or "") + (proc.stderr or "")


def crc32_le(data: bytes) -> int:
    return binascii.crc32(data) & 0xFFFFFFFF


def jlink_savebin(snr: str, addr: int, size: int) -> bytes:
    with tempfile.NamedTemporaryFile(prefix="jlink_cmd_", suffix=".jlink", delete=False) as tf_cmd:
        cmd_path = tf_cmd.name
    with tempfile.NamedTemporaryFile(prefix="jlink_dump_", suffix=".bin", delete=False) as tf_bin:
        bin_path = tf_bin.name
    script = (
        "Device nRF52832_XXAA\n"
        "SelectInterface SWD\n"
        "Speed 4000\n"
        "Connect\n"
        f"SaveBin {bin_path},{hex(addr)},{size}\n"
        "Exit\n"
    )
    Path(cmd_path).write_text(script, encoding="utf-8")
    run(["JLinkExe", "-NoGui", "1", "-SelectEmuBySN", snr, "-CommanderScript", cmd_path])
    data = Path(bin_path).read_bytes()
    if len(data) < size:
        raise RuntimeError(f"SaveBin returned {len(data)} bytes, expected at least {size}")
    return data[:size]


def main() -> int:
    parser = argparse.ArgumentParser(description="Read anchor_config_t from flash and print JSON.")
    parser.add_argument("--probe-serial", required=True, help="J-Link serial number")
    parser.add_argument("--config-addr", default=hex(CONFIG_ADDR_DEFAULT), help="Flash config address")
    args = parser.parse_args()

    preflight_probe_env()
    snr = args.probe_serial
    if not snr.startswith("7"):
        raise RuntimeError(f"anchor config read only allows 7xxxxxx probes, got: {snr}")
    addr = int(args.config_addr, 0)
    blob = jlink_savebin(snr, addr, 32)

    schema_version = None
    flags = None
    generation = None

    # Try v2 first.
    magic, schema_v2, anchor_id_v2, role_v2, flags_v2, generation_v2, device_uuid_v2, crc_v2 = struct.unpack(
        "<IBBBBI16sI", blob
    )
    crc_calc_v2 = crc32_le(blob[:-4])
    valid_v2 = (
        magic == CONFIG_MAGIC
        and 0 <= anchor_id_v2 <= 8
        and 0 <= role_v2 <= 3
        and 1 <= schema_v2 <= SCHEMA_VERSION_MAX
        and crc_calc_v2 == crc_v2
    )

    if valid_v2:
        anchor_id = anchor_id_v2
        role = role_v2
        device_uuid = device_uuid_v2
        crc_stored = crc_v2
        crc_calc = crc_calc_v2
        schema_version = schema_v2
        flags = flags_v2
        generation = generation_v2
        valid = True
    else:
        # Fallback to legacy v1 layout.
        blob28 = blob[:28]
        magic, anchor_id, role, _reserved, device_uuid, crc_stored = struct.unpack("<IBB2s16sI", blob28)
        crc_calc = crc32_le(blob28[:-4])
        valid = (
            magic == CONFIG_MAGIC
            and 1 <= anchor_id <= 8
            and 0 <= role <= 3
            and crc_calc == crc_stored
        )
        if valid:
            schema_version = 1
            flags = 0
            generation = 0

    out = {
        "probe_serial": snr,
        "config_addr": hex(addr),
        "magic": hex(magic),
        "schema_version": schema_version,
        "anchor_id": anchor_id,
        "anchor_label": ("U" if anchor_id == 0 else chr(ord("A") + anchor_id - 1)) if 0 <= anchor_id <= 8 else None,
        "role_code": role,
        "role": ROLE_NAME.get(role, "unknown"),
        "flags": flags,
        "generation": generation,
        "device_uuid": device_uuid.hex().upper(),
        "crc_stored": f"{crc_stored:08X}",
        "crc_calc": f"{crc_calc:08X}",
        "valid": valid,
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
