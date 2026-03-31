#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass


@dataclass
class AnchorBleRecord:
    ble_addr: str
    rssi: int | None
    company_id: int
    device_uuid_hex: str
    anchor_id_runtime: int
    role_code: int
    role: str
    raw_mfg_hex: str


ROLE_NAME = {
    0: "unset",
    1: "master",
    2: "matrix",
    3: "responder",
}


def parse_anchor_mfg(company_id: int, payload: bytes, addr: str, rssi: int | None) -> AnchorBleRecord | None:
    # Expected payload (without company_id):
    # 'B''S''A' + ver + uuid16 + anchor_id_runtime + role
    if company_id != 0xFFFF:
        return None
    if len(payload) < 20:
        return None
    if payload[0:3] != b"BSA":
        return None
    if payload[3] != 0x01:
        return None
    uuid = payload[4:20]
    if len(payload) < 22:
        return None
    anchor_id_runtime = payload[20]
    role_code = payload[21]
    return AnchorBleRecord(
        ble_addr=addr,
        rssi=rssi,
        company_id=company_id,
        device_uuid_hex=uuid.hex().upper(),
        anchor_id_runtime=anchor_id_runtime,
        role_code=role_code,
        role=ROLE_NAME.get(role_code, "unknown"),
        raw_mfg_hex=payload.hex().upper(),
    )


async def do_scan(timeout_s: float) -> list[AnchorBleRecord]:
    try:
        from bleak import BleakScanner
    except ImportError as exc:
        raise RuntimeError("Bleak not installed. Install with: pip install bleak") from exc

    devices = await BleakScanner.discover(timeout=timeout_s, return_adv=True)
    records: list[AnchorBleRecord] = []
    for _addr, (dev, adv) in devices.items():
        mfg = adv.manufacturer_data or {}
        for cid, payload in mfg.items():
            rec = parse_anchor_mfg(cid, bytes(payload), dev.address, adv.rssi)
            if rec:
                records.append(rec)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan BLE advertisements and map unified anchor identification payloads.")
    parser.add_argument("--timeout-s", type=float, default=8.0)
    parser.add_argument("--json", action="store_true", help="Print JSON array")
    args = parser.parse_args()

    records = asyncio.run(do_scan(args.timeout_s))
    if args.json:
        print(json.dumps([asdict(r) for r in records], indent=2))
        return 0

    print("BLE_ADDR,RSSI,COMPANY_ID,DEVICE_UUID,ANCHOR_ID_RUNTIME,ROLE_CODE,ROLE")
    for r in records:
        print(
            f"{r.ble_addr},{'' if r.rssi is None else r.rssi},0x{r.company_id:04X},"
            f"{r.device_uuid_hex},{r.anchor_id_runtime},{r.role_code},{r.role}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
