#!/usr/bin/env python3
"""Read-only simultaneous Fusion/listener capture for relay8 O1."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from fusion_session import parse_fields, resolve_fusion_port


ROOT = Path(__file__).resolve().parents[2]
LISTENERS = ROOT / "B306_Part/host/listener_array_collector.py"
DW_TICKS_PER_SECOND = 499_200_000 * 128
DW_MODULUS = 1 << 40


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--duration", type=float, default=330.0)
    parser.add_argument("--node", default="BSFEC35")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    listener_dir = args.out_dir / "listeners"
    listener_stdout = (args.out_dir / "listener_collector.stdout.log").open(
        "x", encoding="utf-8", buffering=1
    )
    listener = subprocess.Popen(
        [
            sys.executable, str(LISTENERS), "--out-dir", str(listener_dir),
            "--duration", str(args.duration), "--baud", "460800",
            "--require-kind", "LBD", "--require-kind", "LPD",
        ],
        cwd=ROOT, stdout=listener_stdout, stderr=subprocess.STDOUT, text=True,
    )
    summary: dict[str, object] = {
        "status": "IN_PROGRESS", "started": now(), "node": args.node,
        "duration_s": args.duration, "read_only": True,
    }
    channel: ThreadedLineChannel | None = None
    records: list[dict[str, object]] = []
    first_seen: float | None = None
    last_seen: float | None = None
    start = time.monotonic()
    log_path = args.out_dir / "fusion_cdc.log"
    target_path = args.out_dir / "target_uwb.jsonl"
    try:
        with log_path.open("x", encoding="utf-8", buffering=1) as log, target_path.open(
            "x", encoding="utf-8", buffering=1
        ) as target_log:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None), log, "FUSION", decoded_queue_records=65536,
                backlog_red_records=8192, raw_backlog_red_bytes=8192, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            deadline = start + args.duration
            prefix = f"FUSION_UWB proto=7 name={args.node} "
            next_health = start + 1.0
            while time.monotonic() < deadline:
                now_mono = time.monotonic()
                line = channel.read(min(deadline, now_mono + 0.25))
                if line and line.startswith(prefix):
                    fields = parse_fields(line)
                    item = {
                        "host_monotonic": now_mono,
                        "master_ms": int(fields["master_ms"]),
                        "node_ms": int(fields["node_ms"]),
                        "sweep": int(fields["sweep"]),
                        "poll_tx": int(fields["poll_tx"], 16),
                        "sf_valid": int(fields["sf_valid"]),
                        "sf_mod16": int(fields["sf_mod16"]),
                    }
                    records.append(item)
                    target_log.write(json.dumps(item, sort_keys=True) + "\n")
                    first_seen = first_seen or now_mono
                    last_seen = now_mono
                now_mono = time.monotonic()
                if now_mono >= next_health:
                    health = channel.health_snapshot()
                    if health["red_markers"]:
                        raise RuntimeError(f"Fusion drain watchdog RED: {health}")
                    if first_seen is None and now_mono - start > 15.0:
                        raise RuntimeError("zero-progress alarm: no target UWB in 15 s")
                    if last_seen is not None and now_mono - last_seen > 2.0:
                        raise RuntimeError("zero-progress alarm: target UWB stalled >2 s")
                    next_health = now_mono + 1.0
            summary["host_drain"] = channel.health_snapshot()
        listener_rc = listener.wait(timeout=45.0)
        if listener_rc != 0:
            raise RuntimeError(f"listener collector rc={listener_rc}")
        if len(records) < 2:
            raise RuntimeError(f"only {len(records)} target records")
        deltas = [
            (b["poll_tx"] - a["poll_tx"]) % DW_MODULUS
            for a, b in zip(records, records[1:])
        ]
        tag_duration_s = sum(deltas) / DW_TICKS_PER_SECOND
        valid = sum(int(row["sf_valid"]) for row in records)
        grid_ok = sum(
            ((int(b["sf_mod16"]) - int(a["sf_mod16"])) & 0xF) == 1
            for a, b in zip(records, records[1:])
            if int(a["sf_valid"]) and int(b["sf_valid"])
        )
        grid_pairs = sum(
            1 for a, b in zip(records, records[1:])
            if int(a["sf_valid"]) and int(b["sf_valid"])
        )
        summary.update({
            "status": "PASS", "target_records": len(records),
            "tag_domain_duration_s": tag_duration_s,
            "tag_domain_hz": (len(records) - 1) / tag_duration_s,
            "sf_valid_records": valid,
            "sf_grid_pairs": grid_pairs, "sf_grid_plus1_pairs": grid_ok,
            "listener_summary": str(listener_dir / "summary.json"),
        })
        return 0
    except Exception as exc:
        summary.update({"status": "FAIL", "error": f"{type(exc).__name__}: {exc}"})
        if listener.poll() is None:
            listener.terminate()
            try:
                listener.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                listener.kill()
                listener.wait(timeout=5.0)
        return 2
    finally:
        if channel is not None:
            summary.setdefault("host_drain", channel.health_snapshot())
            channel.close()
        listener_stdout.close()
        summary["ended"] = now()
        (args.out_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    raise SystemExit(main())
