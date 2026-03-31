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
CONFIG_ADDR_DEFAULT = 0x0007E000
CONFIG_PAGE_SIZE = 4096

ROLE_MAP = {
    "master": 1,
    "matrix": 2,
    "responder": 3,
}


def preflight_probe_env() -> None:
    subprocess.run(["pkill", "-f", "nrfutil-device --json list --hotplug"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(cmd: list[str], check: bool = True) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return (proc.stdout or "") + (proc.stderr or "")


def parse_anchor_id(val: str) -> int:
    v = val.strip().upper()
    if len(v) != 1 or v < "A" or v > "H":
        raise argparse.ArgumentTypeError("anchor-id must be A..H")
    return ord(v) - ord("A") + 1  # 1..8


def parse_uuid_hex(val: str) -> bytes:
    s = val.strip().replace("-", "").replace(":", "").lower()
    if len(s) != 32:
        raise argparse.ArgumentTypeError("device-uuid must be 16 bytes (32 hex chars)")
    try:
        return bytes.fromhex(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


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


def read_uid_words(snr: str) -> tuple[int, int]:
    uid = jlink_savebin(snr, 0x10000060, 8)
    uid0, uid1 = struct.unpack("<II", uid)
    return uid0, uid1


def derive_uuid_from_uid(uid0: int, uid1: int) -> bytes:
    uid = struct.pack("<II", uid0, uid1)
    tail = bytes((b ^ (0xA5 + i)) & 0xFF for i, b in enumerate(uid))
    return uid + tail


def pack_config(anchor_id: int, role: int, device_uuid: bytes) -> bytes:
    head = struct.pack("<IBB2s16s", CONFIG_MAGIC, anchor_id, role, b"\x00\x00", device_uuid)
    crc = crc32_le(head)
    return head + struct.pack("<I", crc)


def ihex_line(addr: int, rectype: int, data: bytes) -> str:
    count = len(data)
    body = bytes([count, (addr >> 8) & 0xFF, addr & 0xFF, rectype]) + data
    checksum = ((~sum(body) + 1) & 0xFF)
    return ":" + body.hex().upper() + f"{checksum:02X}"


def make_ihex(addr: int, payload: bytes) -> str:
    lines: list[str] = []
    upper = (addr >> 16) & 0xFFFF
    lines.append(ihex_line(0, 0x04, struct.pack(">H", upper)))
    offset = addr & 0xFFFF
    pos = 0
    while pos < len(payload):
        chunk = payload[pos : pos + 16]
        lines.append(ihex_line(offset + pos, 0x00, chunk))
        pos += len(chunk)
    lines.append(":00000001FF")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision anchor_config_t to unified anchor firmware.")
    parser.add_argument("--probe-serial", required=True, help="J-Link serial number")
    parser.add_argument("--anchor-id", required=True, type=parse_anchor_id, help="A..H")
    parser.add_argument("--role", required=True, choices=sorted(ROLE_MAP.keys()))
    parser.add_argument("--config-addr", default=hex(CONFIG_ADDR_DEFAULT), help="Flash address, e.g. 0x0007E000")
    parser.add_argument("--device-uuid", type=parse_uuid_hex, help="Optional 16-byte UUID (32 hex chars)")
    parser.add_argument("--use-ble-scan", action="store_true",
                        help="Scan BLE advertisements and require target device_uuid to be visible before provisioning.")
    parser.add_argument("--ble-scan-timeout-s", type=float, default=8.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    preflight_probe_env()
    snr = args.probe_serial
    if not snr.startswith("7"):
        raise RuntimeError(f"anchor provisioning only allows 7xxxxxx probes, got: {snr}")
    role = ROLE_MAP[args.role]
    config_addr = int(args.config_addr, 0)

    uid0, uid1 = read_uid_words(snr)
    device_uuid = args.device_uuid if args.device_uuid else derive_uuid_from_uid(uid0, uid1)
    device_uuid_hex = device_uuid.hex().upper()

    if args.use_ble_scan:
        out = run(
            [
                "python3",
                "scripts/scan_and_map.py",
                "--timeout-s",
                str(args.ble_scan_timeout_s),
                "--json",
            ]
        )
        seen = False
        try:
            scanned = json.loads(out)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"failed to parse scan JSON:\n{out}") from exc
        for row in scanned:
            if str(row.get("device_uuid_hex", "")).upper() == device_uuid_hex:
                seen = True
                break
        if not seen:
            raise RuntimeError(
                "target device_uuid not observed in BLE scan before provisioning. "
                f"device_uuid={device_uuid_hex}"
            )
    blob = pack_config(args.anchor_id, role, device_uuid)

    payload = {
        "probe_serial": snr,
        "anchor_id": args.anchor_id,
        "anchor_label": chr(ord("A") + args.anchor_id - 1),
        "role": args.role,
        "role_code": role,
        "config_addr": hex(config_addr),
        "config_magic": hex(CONFIG_MAGIC),
        "mcu_uid": f"{uid0:08X}{uid1:08X}",
        "device_uuid": device_uuid_hex,
        "crc32": f"{crc32_le(blob[:-4]):08X}",
        "blob_len": len(blob),
        "use_ble_scan": bool(args.use_ble_scan),
    }

    print(json.dumps(payload, indent=2))

    if args.dry_run:
        print("[dry-run] skipping flash write")
        return 0

    with tempfile.NamedTemporaryFile(prefix="anchor_cfg_", suffix=".hex", delete=False) as tf_hex:
        tf_hex.write(make_ihex(config_addr, blob).encode("utf-8"))
        hex_path = tf_hex.name

    run(["scripts/reset_then_flash.sh", snr, hex_path])
    if args.verify:
        rb = jlink_savebin(snr, config_addr, len(blob))
        if rb != blob:
            raise RuntimeError("readback mismatch after provisioning write")

    with tempfile.NamedTemporaryFile(prefix="anchor_config_", suffix=".bin", delete=False) as tf:
        tf.write(blob)
        path = tf.name
    print(f"[ok] provisioning complete, local blob copy: {path}, config_hex: {hex_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
