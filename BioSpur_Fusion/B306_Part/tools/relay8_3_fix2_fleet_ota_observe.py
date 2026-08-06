#!/usr/bin/env python3
"""FIX2 merged G3/G4: OTA fleet, then observe existing schedules before reads."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from relay8_1_batch_ota import run_ota
from relay8_3_t567_provision import tag_read
from relay8_tag_control import wait_master_status


ORDER = (
    ("BSF3C79", "BS065F", 1), ("BSFC2CC", "BSE88E", 2),
    ("BSF44AD", "BS6F3A", 3), ("BSF6C53", "BSF8E0", 4),
    ("BSF8BC4", "BSEFD2", 5), ("BSF1120", "BSB10B", 6),
    ("BSF31CC", "BS8251", 7), ("BSFAA61", "BSF572", 8),
    ("BSFEC35", "BSDB1B", 9), ("BSFB165", "BS1150", 10),
)
MARKER = "tag-fusion-link-relay8.3-fix2"
IMAGE_HASH = "7085b1f79e8f3b276c786d44a2da5cb8733089204d5bf6ee9c3370b2a75bd435"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def observe_existing_schedule(node: str, slot: int, board: Path) -> dict:
    result: dict = {"started": now(), "zero_tag_commands_before_witness": True}
    channel = None
    with (board / "postota_fusion.cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None), log, "FUSION",
                decoded_queue_records=131072, backlog_red_records=16384,
                raw_backlog_red_bytes=16384, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_before_send"] = decode_guard(channel, 15.0)
            result["master_status"] = wait_master_status(channel)

            prefix = f"FUSION_UWB proto=7 name={node} "
            deadline = time.monotonic() + 60.0
            rows: list[dict[str, str]] = []
            first_seen = None
            while time.monotonic() < deadline:
                read_deadline = deadline if first_seen is None else min(deadline, first_seen + 8.0)
                line = channel.read(read_deadline)
                if line is None:
                    break
                if not line.startswith(prefix):
                    continue
                fields = parse_fields(line)
                if first_seen is None:
                    first_seen = time.monotonic()
                rows.append(fields)
                if time.monotonic() >= first_seen + 8.0:
                    break
            if not rows:
                raise RuntimeError("no autonomous UWB record before first tag command")
            if any(r.get("logical") != str(slot) for r in rows):
                raise RuntimeError("autonomous UWB record used wrong assigned slot")
            rate = None
            if len(rows) >= 2:
                sweep_delta = (int(rows[-1]["sweep"], 0) - int(rows[0]["sweep"], 0)) & 0xFFFFFFFF
                frame_delta_us = int(rows[-1]["frame_us"], 0) - int(rows[0]["frame_us"], 0)
                if frame_delta_us > 0:
                    rate = sweep_delta * 1_000_000.0 / frame_delta_us
            result["autonomous_witness"] = {
                "records": len(rows), "provisional_rate_hz": rate,
                "first": rows[0], "last": rows[-1],
            }

            # The autonomous witness is complete. Read-only tag queries may now follow.
            version = tag_read(channel, node, "VERSION", "VERSION ")
            imgstat = tag_read(channel, node, "IMGSTAT", "IMGSTAT ")
            cfg = tag_read(channel, node, "CFG_STATUS", "CFG ")
            beacon = tag_read(channel, node, "BEACON_STATUS", "BEACON ")
            vf = parse_fields(version["reply"]["text"])
            imf = parse_fields(imgstat["reply"]["text"])
            cf = parse_fields(cfg["reply"]["text"])
            bf = parse_fields(beacon["reply"]["text"])
            if vf.get("fw") != MARKER:
                raise RuntimeError(f"VERSION mismatch: {vf}")
            if imf.get("hash") != IMAGE_HASH or imf.get("confirmed") != "1":
                raise RuntimeError(f"IMGSTAT mismatch: {imf}")
            required = {
                "slot": f"{slot}/12", "run": "1", "state": "RANGING",
                "stored": "1", "pslot": f"{slot}/12", "pperiod": "10", "psync": "1",
            }
            if any(cf.get(k) != v for k, v in required.items()):
                raise RuntimeError(f"running/stored mismatch: {cf}")
            if cf.get("legacy") not in {"0", "1"}:
                raise RuntimeError(f"missing legacy diagnostic: {cf}")
            if bf.get("sync") != "1" or bf.get("lock") != "1":
                raise RuntimeError(f"beacon not locked: {bf}")
            result.update(
                ended=now(), status="PASS_AUTONOMOUS_CONNECTED",
                version=version, imgstat=imgstat, cfg_status=cfg, beacon_status=beacon,
                legacy=int(cf["legacy"]), boot=int(imf["boot"], 0),
                resetreas=imf["resetreas"], running_slot=cf["slot"], stored_slot=cf["pslot"],
                host_drain=channel.health_snapshot(),
            )
            return result
        finally:
            if channel is not None:
                result.setdefault("host_drain", channel.health_snapshot())
                channel.close()
            write(board / "postota_observation.json", result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    args = parser.parse_args()
    root = args.evidence_root
    root.mkdir(parents=True, exist_ok=False)
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    prior = {r["node"]: r for r in inventory["rows"]}
    ledger: list[dict] = []
    write(root / "ledger.json", ledger)

    for ordinal, (node, target, slot) in enumerate(ORDER, 1):
        board = root / f"{ordinal:02d}_{node.lower()}"
        board.mkdir()
        row: dict = {
            "node": node, "slot": slot, "started": now(), "status": "IN_PROGRESS",
            "prior_firmware": prior[node]["firmware"], "prior_confirmed": prior[node]["confirmed"],
            "prior_boot": int(prior[node]["boot"], 0), "ota_write_retries": 0,
            "nvs_writes": 0, "reprovisioned": False,
        }
        ledger.append(row)
        write(root / "ledger.json", ledger)
        print(f"{node} START slot={slot}", flush=True)
        try:
            ota = run_ota(target, board)
            row["ota"] = {
                k: ota.get(k) for k in (
                    "classification", "target_selection_ready", "phase_a_ok", "phase_b_ok",
                    "ota_upload_complete_seen", "ota_pending_test_seen", "ota_reset_request_seen",
                    "ota_success_seen", "controller_returned_to_recv",
                )
            }
            write(root / "ledger.json", ledger)
            observation = observe_existing_schedule(node, slot, board)
            if observation["boot"] <= row["prior_boot"]:
                raise RuntimeError("boot counter did not increment across OTA reboot")
            row.update(
                ended=now(), status="COMPLETE_AUTONOMOUS_CONNECTED",
                confirmed=1, firmware=MARKER, boot=observation["boot"],
                resetreas=observation["resetreas"], legacy=observation["legacy"],
                provisional_rate_hz=observation["autonomous_witness"]["provisional_rate_hz"],
                autonomous_records=observation["autonomous_witness"]["records"],
                running_slot=observation["running_slot"], stored_slot=observation["stored_slot"],
            )
            print(
                f"{node} COMPLETE legacy={row['legacy']} boot={row['boot']} "
                f"resetreas={row['resetreas']} provisional_rate={row['provisional_rate_hz']}",
                flush=True,
            )
        except Exception as exc:
            row.update(ended=now(), status="QUARANTINED", error=f"{type(exc).__name__}: {exc}")
            print(f"{node} QUARANTINED {row['error']}", flush=True)
        write(root / "ledger.json", ledger)

    complete = sum(r["status"] == "COMPLETE_AUTONOMOUS_CONNECTED" for r in ledger)
    summary = {
        "ended": now(), "complete": complete, "quarantined": len(ledger) - complete,
        "nvs_writes": 0, "reprovisioned": 0, "awaiting_tag_master_unplug": True,
        "ledger": ledger,
    }
    write(root / "summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("complete", "quarantined", "nvs_writes")}, indent=2))
    return 0 if complete == len(ORDER) else 2


if __name__ == "__main__":
    raise SystemExit(main())
