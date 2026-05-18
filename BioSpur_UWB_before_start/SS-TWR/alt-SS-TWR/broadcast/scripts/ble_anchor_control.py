#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Any

from bleak import BleakClient, BleakScanner

SVC_UUID = "2f2b8f40-84e0-4be6-b6bf-2fd95f39d3f0"
STATE_UUID = "2f2b8f40-84e0-4be6-b6bf-2fd95f39d3f1"
ACTIVE_UUID = "2f2b8f40-84e0-4be6-b6bf-2fd95f39d3f2"
PENDING_UUID = "2f2b8f40-84e0-4be6-b6bf-2fd95f39d3f3"
CONTROL_UUID = "2f2b8f40-84e0-4be6-b6bf-2fd95f39d3f4"
RESULT_UUID = "2f2b8f40-84e0-4be6-b6bf-2fd95f39d3f5"


@dataclass
class Target:
    addr: str
    rssi: int | None
    device_uuid_hex: str
    anchor_id_cfg: int
    role_code: int
    ble_device: Any | None = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BLE control plane helper for unified anchors.")
    p.add_argument("--ble-addr", help="Target BLE address")
    p.add_argument("--device-uuid", help="Target stable uuid (32 hex chars)")
    p.add_argument("--scan-timeout", type=float, default=8.0)
    p.add_argument("--set-role", choices=["unset", "master", "matrix", "responder"])
    p.add_argument("--set-label", help="U or A..H")
    p.add_argument("--set-generation", type=int)
    p.add_argument(
        "--cmd",
        action="append",
        default=[],
        help="Send a raw anchor BLE control command, e.g. 'US?', 'USON 30', or 'USOFF'. Can be repeated.",
    )
    p.add_argument("--validate", action="store_true")
    p.add_argument("--commit", action="store_true")
    p.add_argument("--reboot", action="store_true")
    p.add_argument("--sync", action="store_true", help="Sync pending from active")
    p.add_argument("--json", action="store_true")
    return p.parse_args()


def parse_anchor_mfg(company_id: int, payload: bytes) -> tuple[str, int, int] | None:
    if company_id != 0xFFFF:
        return None
    if len(payload) < 22:
        return None
    if payload[0:3] != b"BSA" or payload[3] != 0x01:
        return None
    return payload[4:20].hex().upper(), payload[20], payload[21]


async def resolve_target(args: argparse.Namespace) -> Target:
    if args.ble_addr:
        dev = await BleakScanner.find_device_by_address(args.ble_addr, timeout=args.scan_timeout)
        return Target(
            addr=args.ble_addr,
            rssi=None,
            device_uuid_hex="",
            anchor_id_cfg=-1,
            role_code=-1,
            ble_device=dev,
        )

    if not args.device_uuid:
        raise RuntimeError("either --ble-addr or --device-uuid is required")

    want = args.device_uuid.strip().replace("-", "").replace(":", "").upper()
    devices = await BleakScanner.discover(timeout=args.scan_timeout, return_adv=True)
    for _addr, (dev, adv) in devices.items():
        for cid, payload in (adv.manufacturer_data or {}).items():
            parsed = parse_anchor_mfg(cid, bytes(payload))
            if not parsed:
                continue
            device_uuid_hex, anchor_id_cfg, role_code = parsed
            if device_uuid_hex == want:
                return Target(
                    addr=dev.address,
                    rssi=adv.rssi,
                    device_uuid_hex=device_uuid_hex,
                    anchor_id_cfg=anchor_id_cfg,
                    role_code=role_code,
                    ble_device=dev,
                )
    raise RuntimeError(f"device_uuid not found in scan: {want}")


async def read_text(client: BleakClient, uuid: str) -> str:
    data = await client.read_gatt_char(uuid)
    return data.decode("utf-8", errors="replace").strip()


async def write_cmd(client: BleakClient, cmd: str, *, allow_disconnect: bool = False) -> str:
    payload = cmd.encode("ascii")
    await client.write_gatt_char(CONTROL_UUID, payload, response=False)
    await asyncio.sleep(0.12)
    try:
        return await read_text(client, RESULT_UUID)
    except Exception:
        if allow_disconnect:
            return "OK DISCONNECT_EXPECTED"
        raise


def state_field_equals(state: str, key: str, value: str) -> bool:
    for token in state.split():
        if token == f"{key}={value}":
            return True
    return False


async def open_anchor_session(client_target: Any) -> tuple[BleakClient, dict]:
    client = BleakClient(client_target, timeout=12.0)
    await client.connect()
    services = await client.get_services()
    if SVC_UUID.lower() not in {s.uuid.lower() for s in services}:
        await client.disconnect()
        raise RuntimeError(f"control service not found on {client_target}: {SVC_UUID}")

    snapshot = {
        "state": await read_text(client, STATE_UUID),
        "active": await read_text(client, ACTIVE_UUID),
        "pending": await read_text(client, PENDING_UUID),
        "result": await read_text(client, RESULT_UUID),
    }
    return client, snapshot


async def run(args: argparse.Namespace) -> dict:
    target = await resolve_target(args)
    out: dict = {
        "target": {
            "addr": target.addr,
            "rssi": target.rssi,
            "device_uuid_hex": target.device_uuid_hex,
            "anchor_id_cfg": target.anchor_id_cfg,
            "role_code": target.role_code,
        },
        "commands": [],
    }

    client_target = target.ble_device if target.ble_device is not None else target.addr
    wants_config_change = any([
        args.set_role is not None,
        args.set_label is not None,
        args.set_generation is not None,
        args.validate,
        args.commit,
        args.reboot,
    ])
    client, snapshot = await open_anchor_session(client_target)
    try:
        out["state_before"] = snapshot["state"]
        out["active_before"] = snapshot["active"]
        out["pending_before"] = snapshot["pending"]
        out["result_before"] = snapshot["result"]

        if wants_config_change and state_field_equals(snapshot["state"], "busy", "1"):
            resp = await write_cmd(client, "REBOOT", allow_disconnect=True)
            out["commands"].append({"cmd": "REBOOT(prep)", "resp": resp})
            await client.disconnect()
            await asyncio.sleep(2.0)
            client, snapshot = await open_anchor_session(client_target)
            out["state_config"] = snapshot["state"]
            out["active_config"] = snapshot["active"]
            out["pending_config"] = snapshot["pending"]
            out["result_config"] = snapshot["result"]

        if args.sync:
            resp = await write_cmd(client, "SYNC")
            out["commands"].append({"cmd": "SYNC", "resp": resp})
        for raw_cmd in args.cmd:
            cmd = raw_cmd.strip()
            if not cmd:
                continue
            resp = await write_cmd(client, cmd)
            out["commands"].append({"cmd": cmd, "resp": resp})
        if args.set_role:
            cmd = f"R {args.set_role.upper()}"
            resp = await write_cmd(client, cmd)
            out["commands"].append({"cmd": cmd, "resp": resp})
        if args.set_label:
            cmd = f"L {args.set_label.strip().upper()}"
            resp = await write_cmd(client, cmd)
            out["commands"].append({"cmd": cmd, "resp": resp})
        if args.set_generation is not None:
            cmd = f"G {args.set_generation}"
            resp = await write_cmd(client, cmd)
            out["commands"].append({"cmd": cmd, "resp": resp})
        if args.validate:
            resp = await write_cmd(client, "VALIDATE")
            out["commands"].append({"cmd": "VALIDATE", "resp": resp})
        if args.commit:
            resp = await write_cmd(client, "COMMIT")
            out["commands"].append({"cmd": "COMMIT", "resp": resp})
        if args.reboot:
            resp = await write_cmd(client, "REBOOT", allow_disconnect=True)
            out["commands"].append({"cmd": "REBOOT", "resp": resp})

        if not args.reboot:
            out["state_after"] = await read_text(client, STATE_UUID)
            out["active_after"] = await read_text(client, ACTIVE_UUID)
            out["pending_after"] = await read_text(client, PENDING_UUID)
            out["result_after"] = await read_text(client, RESULT_UUID)
    finally:
        if client.is_connected:
            await client.disconnect()

    return out


def main() -> int:
    args = parse_args()
    result = asyncio.run(run(args))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2))
    for row in result.get("commands", []):
        if not str(row.get("resp", "")).startswith("OK"):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
