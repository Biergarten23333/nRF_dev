#!/usr/bin/env python3
"""Corrected read-only adjudication after FIX2 fleet OTA."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from relay8_3_t567_provision import tag_read
from relay8_tag_control import wait_master_status


MAP = (
    ("BSF3C79", 1), ("BSFC2CC", 2), ("BSF44AD", 3), ("BSF6C53", 4),
    ("BSF8BC4", 5), ("BSF1120", 6), ("BSF31CC", 7), ("BSFAA61", 8),
    ("BSFEC35", 9), ("BSFB165", 10),
)
MARKER = "tag-fusion-link-relay8.3-fix2"
IMAGE_HASH = "7085b1f79e8f3b276c786d44a2da5cb8733089204d5bf6ee9c3370b2a75bd435"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_bounded(channel, node: str, command: str, prefix: str) -> tuple[dict, int]:
    errors = []
    for attempt in range(1, 4):
        try:
            return tag_read(channel, node, command, prefix), attempt
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if attempt < 3:
                time.sleep(5.0)
    raise RuntimeError(f"{command} exhausted bounded reads: {errors}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--baseline-readback", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing existing output: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    prior = {r["node"]: r for r in json.loads(args.inventory.read_text())["rows"]}
    if args.baseline_readback is not None:
        baseline = json.loads(args.baseline_readback.read_text())["rows"]
        prior = {r["node"]: {"boot": r["boot"]} for r in baseline}
    result: dict = {"started": now(), "read_only": True, "nvs_writes": 0, "rows": []}
    channel = None
    with args.output.with_suffix(".cdc.log").open("x", encoding="utf-8", buffering=1) as log:
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
            for node, slot in MAP:
                row: dict = {"node": node, "slot": slot, "status": "IN_PROGRESS"}
                result["rows"].append(row)
                try:
                    version, va = read_bounded(channel, node, "VERSION", "VERSION ")
                    imgstat, ia = read_bounded(channel, node, "IMGSTAT", "IMGSTAT ")
                    cfg, ca = read_bounded(channel, node, "CFG_STATUS", "CFG ")
                    beacon, ba = read_bounded(channel, node, "BEACON_STATUS", "BEACON ")
                    vf = parse_fields(version["reply"]["text"])
                    imf = parse_fields(imgstat["reply"]["text"])
                    cf = parse_fields(cfg["reply"]["text"])
                    bf = parse_fields(beacon["reply"]["text"])
                    required = {
                        "slot": f"{slot}/12", "run": "1", "state": "RANGING",
                        "stored": "1", "pslot": f"{slot}/12", "pperiod": "10", "psync": "1",
                    }
                    if vf.get("fw") != MARKER:
                        raise RuntimeError(f"VERSION mismatch: {vf}")
                    if imf.get("hash") != IMAGE_HASH or imf.get("confirmed") != "1":
                        raise RuntimeError(f"IMGSTAT mismatch: {imf}")
                    if any(cf.get(k) != v for k, v in required.items()):
                        raise RuntimeError(f"running/stored mismatch: {cf}")
                    if cf.get("legacy") not in {"0", "1"}:
                        raise RuntimeError(f"legacy field missing: {cf}")
                    if bf.get("sync") != "1" or bf.get("lock") != "1":
                        raise RuntimeError(f"beacon lock mismatch: {bf}")
                    boot = int(imf["boot"], 0)
                    prior_boot = int(prior[node]["boot"], 0) if isinstance(prior[node]["boot"], str) else int(prior[node]["boot"])
                    if boot <= prior_boot:
                        raise RuntimeError("boot counter did not increment")
                    row.update(
                        status="PASS", firmware=vf["fw"], confirmed=1,
                        image_hash=imf["hash"], boot=boot, prior_boot=prior_boot,
                        resetreas=imf["resetreas"], legacy=int(cf["legacy"]),
                        running=cf["slot"], stored=cf["pslot"], count=cf.get("count"),
                        period=cf.get("period"), beacon_sync=bf.get("sync"), beacon_lock=bf.get("lock"),
                        attempts={"version": va, "imgstat": ia, "cfg_status": ca, "beacon_status": ba},
                        version=version, imgstat=imgstat, cfg_status=cfg, beacon_status=beacon,
                    )
                    print(
                        f"{node} PASS slot={slot} legacy={row['legacy']} boot={boot} "
                        f"resetreas={row['resetreas']}", flush=True,
                    )
                except Exception as exc:
                    row.update(status="FAIL", error=f"{type(exc).__name__}: {exc}")
                    print(f"{node} FAIL {row['error']}", flush=True)
            passed = sum(r["status"] == "PASS" for r in result["rows"])
            result.update(
                ended=now(), status="PASS" if passed == 10 else "PARTIAL",
                passed=passed, failed=10-passed, host_drain=channel.health_snapshot(),
            )
            return 0 if passed == 10 else 2
        finally:
            if channel is not None:
                result.setdefault("host_drain", channel.health_snapshot())
                channel.close()
            args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
