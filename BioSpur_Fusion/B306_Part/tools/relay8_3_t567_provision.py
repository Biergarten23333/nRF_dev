#!/usr/bin/env python3
"""S4 write-then-reboot provisioning with behavioral acceptance."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_control import relay_command_patient
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from pre_ramp_hardening import request_list
from capacity_ramp import RecordingAssembler
from relay8_tag_control import wait_master_status

MAP = (
    ("BSF3C79", 1), ("BSFC2CC", 2), ("BSF44AD", 3), ("BSF6C53", 4),
    ("BSF8BC4", 5), ("BSF1120", 6), ("BSF31CC", 7), ("BSFAA61", 8),
    ("BSFEC35", 9), ("BSFB165", 10),
)
EXPECTED_HASH = "7d9664d67a3bc87e2c00a9162f3e022baba68f508b89e009e47a625fd29b2009"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def tag_read(channel: ThreadedLineChannel, node: str, command: str, prefix: str) -> dict:
    return relay_command_patient(
        channel, node, command, prefix, attempts=1,
        reply_timeout_s=100.0 if node == "BSFB165" else 25.0,
    )


def behavioral_witness(channel: ThreadedLineChannel, node: str, duration_s: float = 15.0) -> dict:
    deadline = time.monotonic() + 90.0
    prefix = f"FUSION_UWB proto=7 name={node} "
    rows: list[dict[str, str]] = []
    first_seen = None
    while time.monotonic() < deadline:
        line = channel.read(deadline if first_seen is None else min(deadline, first_seen + duration_s))
        if line is None:
            break
        if not line.startswith(prefix):
            continue
        fields = parse_fields(line)
        if first_seen is None:
            first_seen = time.monotonic()
        rows.append(fields)
        if time.monotonic() >= first_seen + duration_s:
            break
    if len(rows) < 20:
        raise RuntimeError(f"{node} behavioral witness has only {len(rows)} UWB records")
    first, last = rows[0], rows[-1]
    sweep_delta = (int(last["sweep"], 0) - int(first["sweep"], 0)) & 0xFFFFFFFF
    frame_delta_us = int(last["frame_us"], 0) - int(first["frame_us"], 0)
    if frame_delta_us <= 0:
        raise RuntimeError(f"{node} nonpositive frame time span")
    rate_hz = sweep_delta * 1_000_000.0 / frame_delta_us
    sf_valid_fraction = sum(r.get("sf_valid") == "1" for r in rows) / len(rows)
    valid_frames = sum(int(r.get("valid", "0"), 0) != 0 for r in rows)
    if not 7.8 <= rate_hz <= 8.8:
        raise RuntimeError(f"{node} rate out of 8.333 Hz band: {rate_hz:.6f}")
    if sf_valid_fraction != 1.0 or valid_frames == 0:
        raise RuntimeError(
            f"{node} behavior invalid sf_valid={sf_valid_fraction} valid_frames={valid_frames}"
        )
    return {
        "records": len(rows), "sweep_delta": sweep_delta,
        "frame_delta_us": frame_delta_us, "tag_domain_rate_hz": rate_hz,
        "sf_valid_fraction": sf_valid_fraction, "valid_range_frames": valid_frames,
        "first": rows[0], "last": rows[-1],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    args = parser.parse_args()
    root = args.evidence_root
    root.mkdir(parents=True, exist_ok=False)
    registry = json.loads(args.registry.read_text())
    expected = {node: slot for node, slot in MAP}
    if registry.get("assignments") != expected:
        raise RuntimeError("host registry does not match accepted S3 map")
    state: dict = {"started": now(), "status": "IN_PROGRESS", "nodes": {}}
    write_json(root / "state.json", state)
    channel = None
    with (root / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None), log, "FUSION",
                decoded_queue_records=131072, backlog_red_records=16384,
                raw_backlog_red_bytes=16384, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            state["decode_before_send"] = decode_guard(channel, 15.0)
            state["master_status"] = wait_master_status(channel)
            listing = request_list(channel, RecordingAssembler(), {}, tuple(expected))
            if set(listing["peers"]) != set(expected):
                raise RuntimeError(f"fleet identity mismatch: {sorted(listing['peers'])}")
            state["preflight_list"] = listing
            write_json(root / "state.json", state)

            for node, slot in MAP:
                row: dict = {"slot": slot, "started": now(), "status": "IN_PROGRESS", "write_attempts": 0}
                state["nodes"][node] = row
                write_json(root / "state.json", state)
                print(f"{node} SLOT={slot} START", flush=True)
                try:
                    ping = b306_command(channel, node, "PING", "PONG ")
                    version = tag_read(channel, node, "VERSION", "VERSION ")
                    imgstat = tag_read(channel, node, "IMGSTAT", "IMGSTAT ")
                    if "fw=tag-fusion-link-relay8.3" not in version["reply"]["text"]:
                        raise RuntimeError("pre-write marker mismatch")
                    if EXPECTED_HASH not in imgstat["reply"]["text"] or "confirmed=1" not in imgstat["reply"]["text"]:
                        raise RuntimeError("pre-write image mismatch")
                    before_cfg = tag_read(channel, node, "CFG_STATUS", "CFG ")
                    before_fields = parse_fields(before_cfg["reply"]["text"])
                    if before_fields.get("state") != "UNPROVISIONED" or before_fields.get("stored") != "0":
                        raise RuntimeError(f"unsafe pre-write state: {before_fields}")
                    row["prewrite"] = {"ping": ping, "version": version, "imgstat": imgstat, "cfg": before_cfg}

                    command = (
                        f"CFG TAG={slot} SLOT={slot} COUNT=12 MASK=0x{1 << slot:04X} "
                        "PERIOD=10 ACTIVE=9 EPOCH=5000 BEACON_SYNC=1 BEACON_WIN_N=1 "
                        "DW_ANCHOR=0 RUN=1 PMODE=0 PERSIST=1"
                    )
                    row["command"] = command
                    row["write_attempts"] = 1
                    persist = tag_read(channel, node, command, "CFG_PERSIST_OK ")
                    row["persist_transport"] = persist
                    write_json(root / "state.json", state)

                    row["reboot_attempts"] = 1
                    reboot = tag_read(channel, node, "REBOOT", "REBOOTING")
                    row["reboot_transport"] = reboot
                    behavior = behavioral_witness(channel, node)
                    row["behavior"] = behavior

                    cfg = tag_read(channel, node, "CFG_STATUS", "CFG ")
                    cfg_fields = parse_fields(cfg["reply"]["text"])
                    beacon = tag_read(channel, node, "BEACON_STATUS", "BEACON ")
                    beacon_fields = parse_fields(beacon["reply"]["text"])
                    post_img = tag_read(channel, node, "IMGSTAT", "IMGSTAT ")
                    img_fields = parse_fields(post_img["reply"]["text"])
                    required_cfg = {
                        "slot": f"{slot}/12", "state": "RANGING", "stored": "1",
                        "pslot": f"{slot}/12", "pperiod": "10", "psync": "1",
                    }
                    if any(cfg_fields.get(k) != v for k, v in required_cfg.items()):
                        raise RuntimeError(f"stored/running mismatch: {cfg_fields}")
                    if beacon_fields.get("lock") != "1" or beacon_fields.get("sync") != "1":
                        raise RuntimeError(f"beacon not locked: {beacon_fields}")
                    row.update(
                        ended=now(), status="COMPLETE", cfg_status=cfg,
                        beacon_status=beacon, imgstat=post_img,
                        boot=int(cfg_fields["boot"], 0), resetreas=cfg_fields["resetreas"],
                    )
                    if row["resetreas"] == "00000001":
                        row["prominent_reset_observation"] = "commanded reboot returned RESETPIN"
                    print(
                        f"{node} COMPLETE slot={slot} rate={behavior['tag_domain_rate_hz']:.4f} "
                        f"boot={row['boot']} resetreas={row['resetreas']}", flush=True,
                    )
                except Exception as exc:
                    row.update(ended=now(), status="FAIL", error=f"{type(exc).__name__}: {exc}")
                    state["status"] = "FAIL"
                    write_json(root / "state.json", state)
                    raise
                write_json(root / "state.json", state)

            slots = []
            for node, _ in MAP:
                cfg = tag_read(channel, node, "CFG_STATUS", "CFG ")
                fields = parse_fields(cfg["reply"]["text"])
                state["nodes"][node]["fleet_readback"] = cfg
                if fields.get("stored") != "1" or "pslot" not in fields:
                    raise RuntimeError(f"{node} missing persisted schedule")
                pslot, count = (int(x, 0) for x in fields["pslot"].split("/"))
                if not 1 <= pslot <= 10 or count != 12:
                    raise RuntimeError(f"{node} persisted slot outside occupied set: {fields}")
                slots.append(pslot)
            if sorted(slots) != list(range(1, 11)) or 11 in slots:
                raise RuntimeError(f"fleet persisted slot set invalid: {slots}")
            state.update(
                ended=now(), status="PASS_AWAITING_MANUAL_POR",
                persisted_slot_set=sorted(slots), guard_slot_11_empty=True,
                host_drain=channel.health_snapshot(),
            )
            write_json(root / "state.json", state)
            registry["status"] = "provisioned_awaiting_manual_por"
            registry["provision_evidence"] = str(root)
            write_json(args.registry, registry)
            print("PROVISION PASS — AWAITING MANUAL POR", flush=True)
            return 0
        finally:
            if channel is not None:
                state.setdefault("host_drain", channel.health_snapshot())
                channel.close()
            write_json(root / "state.json", state)


if __name__ == "__main__":
    raise SystemExit(main())
