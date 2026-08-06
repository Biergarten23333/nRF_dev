#!/usr/bin/env python3
"""F2: prove relay8.3-fix1 settings persistence end to end on BSF3C79 only."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from relay8_3_t567_batch_ota import cfg_status
from relay8_3_t567_provision import behavioral_witness, tag_read
from relay8_tag_control import wait_master_status

NODE = "BSF3C79"
MARKER = "tag-fusion-link-relay8.3-fix1"
IMAGE_HASH = "b7395454e971aae771ec28aa469614c7bcbe5acef8f1d4f85f5852fcedc10530"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=Path)
    args = parser.parse_args()
    root = args.evidence_root
    root.mkdir(parents=True, exist_ok=False)
    result: dict = {"started": now(), "node": NODE, "status": "IN_PROGRESS", "write_attempts": 0}
    write(root / "result.json", result)

    channel = None
    with (root / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None), log, "FUSION",
                decoded_queue_records=65536, backlog_red_records=8192,
                raw_backlog_red_bytes=8192, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_before_send"] = decode_guard(channel, 15.0)
            result["master_status"] = wait_master_status(channel)

            version = tag_read(channel, NODE, "VERSION", "VERSION ")
            imgstat = tag_read(channel, NODE, "IMGSTAT", "IMGSTAT ")
            if f"fw={MARKER}" not in version["reply"]["text"]:
                raise RuntimeError("post-OTA marker mismatch")
            if IMAGE_HASH not in imgstat["reply"]["text"] or "confirmed=1" not in imgstat["reply"]["text"]:
                raise RuntimeError("post-OTA hash/confirmation mismatch")
            image_fields = parse_fields(imgstat["reply"]["text"])
            if image_fields.get("boot") != "1":
                raise RuntimeError(f"expected first fix1 boot=1: {image_fields}")
            result["postota"] = {"version": version, "imgstat": imgstat}

            before = tag_read(channel, NODE, "CFG_STATUS", "CFG ")
            before_fields = parse_fields(before["reply"]["text"])
            required_before = {"slot": "none", "run": "0", "state": "UNPROVISIONED", "stored": "0", "reason": "absent"}
            if any(before_fields.get(k) != v for k, v in required_before.items()):
                raise RuntimeError(f"step2 expected absent/unprovisioned: {before_fields}")
            result["step2_before"] = before

            command = (
                "CFG TAG=1 SLOT=1 COUNT=12 MASK=0x0002 PERIOD=10 ACTIVE=9 "
                "EPOCH=5000 BEACON_SYNC=1 BEACON_WIN_N=1 DW_ANCHOR=0 "
                "RUN=1 PMODE=0 PERSIST=1"
            )
            result["persist_command"] = command
            result["write_attempts"] = 1
            persisted = tag_read(channel, NODE, command, "CFG_PERSIST_OK ")
            result["step3_persist"] = persisted
            write(root / "result.json", result)

            after_write = tag_read(channel, NODE, "CFG_STATUS", "CFG ")
            after_write_fields = parse_fields(after_write["reply"]["text"])
            required_write = {"run": "0", "stored": "1", "pslot": "1/12", "pperiod": "10", "psync": "1"}
            if any(after_write_fields.get(k) != v for k, v in required_write.items()):
                raise RuntimeError(f"step4 stored/idle mismatch: {after_write_fields}")
            result["step4_after_write"] = after_write

            reboot = tag_read(channel, NODE, "REBOOT", "REBOOTING")
            result["step5_reboot"] = reboot

            # No tag command is sent between REBOOT and this behavioral witness.
            behavior = behavioral_witness(channel, NODE, duration_s=15.0)
            result["step6_behavior_before_any_postboot_command"] = behavior

            cfg = tag_read(channel, NODE, "CFG_STATUS", "CFG ")
            beacon = tag_read(channel, NODE, "BEACON_STATUS", "BEACON ")
            final_img = tag_read(channel, NODE, "IMGSTAT", "IMGSTAT ")
            cfg_fields = parse_fields(cfg["reply"]["text"])
            beacon_fields = parse_fields(beacon["reply"]["text"])
            final_fields = parse_fields(final_img["reply"]["text"])
            required_cfg = {"slot": "1/12", "state": "RANGING", "stored": "1", "pslot": "1/12", "pperiod": "10", "psync": "1"}
            if any(cfg_fields.get(k) != v for k, v in required_cfg.items()):
                raise RuntimeError(f"step6 loaded/ranging mismatch: {cfg_fields}")
            if beacon_fields.get("sync") != "1" or beacon_fields.get("lock") != "1":
                raise RuntimeError(f"step6 beacon not locked: {beacon_fields}")
            if final_fields.get("boot") != "2":
                raise RuntimeError(f"step7 boot counter is not 2: {final_fields}")
            if final_fields.get("resetreas") != "00000004":
                raise RuntimeError(f"step8 commanded reboot reset cause is not SREQ: {final_fields}")

            result.update(
                ended=now(), status="PASS_F2_ONE_TAG", step6_cfg=cfg,
                step6_beacon=beacon, step7_imgstat=final_img,
                boot=2, resetreas="00000004", image_hash=IMAGE_HASH,
                write_retries=0, host_drain=channel.health_snapshot(),
            )
            write(root / "result.json", result)
            print(json.dumps({
                "status": result["status"], "node": NODE,
                "rate_hz": behavior["tag_domain_rate_hz"],
                "boot": 2, "resetreas": "00000004",
            }, indent=2))
            return 0
        except Exception as exc:
            result.update(ended=now(), status="FAIL_STOP", error=f"{type(exc).__name__}: {exc}")
            write(root / "result.json", result)
            raise
        finally:
            if channel is not None:
                result.setdefault("host_drain", channel.health_snapshot())
                channel.close()
            write(root / "result.json", result)


if __name__ == "__main__":
    raise SystemExit(main())
