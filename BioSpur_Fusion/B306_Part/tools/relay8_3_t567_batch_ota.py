#!/usr/bin/env python3
"""S3: deploy relay8.3 T5/T6/T7 to the remaining nine tags."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_control import relay_command_patient
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from relay8_1_batch_ota import bounded_readback, run_ota
from relay8_batch_ota import witness_no_target_uwb
from relay8_tag_control import wait_master_status

OLD_MARKER = "tag-fusion-link-relay8.2"
OLD_HASH = "dacecc59e5b6fd8d1197e2f6ae57cb2673f1113f4f7902f81d64819190080d3f"
NEW_MARKER = "tag-fusion-link-relay8.3"
NEW_HASH = "7d9664d67a3bc87e2c00a9162f3e022baba68f508b89e009e47a625fd29b2009"
ORDER = (
    ("BSFC2CC", "BSE88E"),
    ("BSF44AD", "BS6F3A"),
    ("BSF6C53", "BSF8E0"),
    ("BSF8BC4", "BSEFD2"),
    ("BSF1120", "BSB10B"),
    ("BSF31CC", "BS8251"),
    ("BSFAA61", "BSF572"),
    ("BSFEC35", "BSDB1B"),
    ("BSFB165", "BS1150"),
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def assert_image(result: dict, marker: str, image_hash: str) -> dict[str, str]:
    version = result["query"]["version"]["reply"]["text"]
    imgstat = result["query"]["imgstat"]["reply"]["text"]
    if f"fw={marker}" not in version:
        raise RuntimeError(f"marker mismatch: {version}")
    if image_hash not in imgstat or "confirmed=1" not in imgstat:
        raise RuntimeError(f"image/confirmation mismatch: {imgstat}")
    return {"version": version, "imgstat": imgstat}


def cfg_status(node: str, out_dir: Path) -> dict:
    out_dir.mkdir()
    result: dict = {"node": node, "status": "IN_PROGRESS", "attempts": []}
    channel = None
    with (out_dir / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
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
            started = time.monotonic()
            reply = None
            for attempt in range(1, 13):
                scheduled = started + (attempt - 1) * 30.0
                if time.monotonic() < scheduled:
                    time.sleep(scheduled - time.monotonic())
                row: dict = {"attempt": attempt}
                try:
                    reply = relay_command_patient(
                        channel, node, "CFG_STATUS", "CFG ", attempts=1,
                        reply_timeout_s=25.0,
                    )
                    row.update(status="ANSWERED", reply=reply)
                    result["attempts"].append(row)
                    break
                except Exception as exc:
                    row.update(status="NO_ANSWER", error=f"{type(exc).__name__}: {exc}")
                    result["attempts"].append(row)
            if reply is None:
                raise RuntimeError("CFG_STATUS unavailable within bound")
            fields = parse_fields(reply["reply"]["text"])
            expected = {"slot": "none", "run": "0", "state": "UNPROVISIONED", "stored": "0"}
            if any(fields.get(k) != v for k, v in expected.items()) or not fields.get("reason"):
                raise RuntimeError(f"T7 mismatch: {fields}")
            result.update(status="PASS", reply=reply, fields=fields)
            return result
        finally:
            if channel is not None:
                result["host_drain"] = channel.health_snapshot()
                channel.close()
            (out_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=Path)
    args = parser.parse_args()
    root = args.evidence_root
    root.mkdir(parents=True, exist_ok=False)
    ledger: list[dict] = []
    failures = 0
    for ordinal, (node, target) in enumerate(ORDER, start=2):
        board = root / f"{ordinal:02d}_{node.lower()}"
        board.mkdir()
        row: dict = {"node": node, "started": now(), "status": "IN_PROGRESS", "ota_write_retries": 0}
        ledger.append(row)
        (root / "ledger.json").write_text(json.dumps(ledger, indent=2) + "\n")
        print(f"{node} START", flush=True)
        try:
            before = bounded_readback(node, board / "preota_query", discontinuity_epoch=None, poll_window_s=120.0)
            assert_image(before, OLD_MARKER, OLD_HASH)
            ota = run_ota(target, board)
            time.sleep(15.0)
            after = bounded_readback(node, board / "postota_query", discontinuity_epoch=None, poll_window_s=360.0)
            evidence = assert_image(after, NEW_MARKER, NEW_HASH)
            cfg = cfg_status(node, board / "postota_cfg_status")
            witness_no_target_uwb(node, board / "postota_silent_witness", duration_s=6.0)
            fields = cfg["fields"]
            row.update(
                ended=now(), status="COMPLETE", marker=NEW_MARKER,
                imgstat_hash=NEW_HASH, confirmed=1,
                boot=int(fields["boot"], 0), resetreas=fields["resetreas"],
                silent_state=fields["state"], silent_reason=fields["reason"],
                read_query_attempts={
                    "preota_version": len(before.get("version_attempts", [])),
                    "preota_imgstat": len(before.get("imgstat_attempts", [])),
                    "postota_version": len(after.get("version_attempts", [])),
                    "postota_imgstat": len(after.get("imgstat_attempts", [])),
                    "postota_cfg_status": len(cfg.get("attempts", [])),
                },
                version=evidence["version"], imgstat=evidence["imgstat"],
                ota_classification=ota.get("classification"), safe_state="unprovisioned_silent",
            )
            print(f"{node} COMPLETE boot={row['boot']} resetreas={row['resetreas']}", flush=True)
        except Exception as exc:
            failures += 1
            row.update(
                ended=now(), status="QUARANTINED",
                error=f"{type(exc).__name__}: {exc}",
                state_change_after_failure="none", ota_write_retries=0,
            )
            print(f"{node} QUARANTINED {row['error']}", flush=True)
        (root / "ledger.json").write_text(json.dumps(ledger, indent=2) + "\n")
    summary = {"ended": now(), "failures": failures, "complete": len(ORDER) - failures, "ledger": ledger}
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"complete": summary["complete"], "failures": failures}, indent=2))
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
