#!/usr/bin/env python3
"""Preserve and probe the live BSF1120 control failure without resetting it."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from ota_timing_evidence import classify_control, records

TARGET = "BSF1120"
PEERS = ("BSF6C53", TARGET, "BSF31CC")
MASTER_MARKER = "dk-fusion-imu-relay-v36"


def drain(channel, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        channel.read(deadline)


def probe(channel, node: str) -> dict:
    sent = time.monotonic()
    try:
        reply = b306_command(channel, node, "PING", "PONG ")
        return {"node": node, "sent_monotonic": sent, "reply": reply}
    except Exception as exc:
        return {"node": node, "sent_monotonic": sent,
                "error": f"{type(exc).__name__}: {exc}"}


def telemetry_fields(payload: str) -> dict:
    parsed = parse_fields(payload)
    keys = ("node_ms", "reset_reason", "ctrl_rx", "ctrl_bad_bsf", "publisher_count",
            "notify_ok", "relay_tx", "relay_ack", "relay_timeout", "q_drop_ctl",
            "drop_err", "logger_drop")
    return {key: parsed.get(key) for key in keys if key in parsed}


def analyze(log_path: Path, rounds: list[dict]) -> dict:
    rows = records(log_path)
    target_telemetry = [row for row in rows if row.payload.startswith(
        "FUSION_TELEMETRY proto=") and f"name={TARGET} " in row.payload]
    result_rounds = []
    target_probes = [probe for round_row in rounds for probe in round_row["probes"]
                     if probe["node"] == TARGET]
    for number, item in enumerate(target_probes, 1):
        sent = item["sent_monotonic"]
        host = next((row for row in rows if row.monotonic >= sent and
                     row.direction == "FUSION_TX" and row.payload == f"{TARGET} PING"), None)
        window_start = host.monotonic if host else sent
        next_host = next((row.monotonic for row in rows
                          if row.monotonic > window_start and row.direction == "FUSION_TX"),
                         window_start + 9)
        window = [row for row in rows if window_start <= row.monotonic < next_host]
        tx = next((row for row in window if row.payload.startswith(
            f"FUSION_COMMAND_TX target={TARGET} ")), None)
        reject = next((row for row in window if "FUSION_COMMAND_REJECT" in row.payload), None)
        replies = [row for row in window if row.payload.startswith("FUSION_REPLY ")]
        matching = [row for row in replies if f"name={TARGET} " in row.payload and
                    f"text=PONG name={TARGET} " in row.payload]
        before = [row for row in target_telemetry if row.monotonic < sent]
        after = [row for row in target_telemetry if row.monotonic > sent]
        pre = telemetry_fields(before[-1].payload) if before else {}
        post = telemetry_fields(after[0].payload) if after else {}
        tx_err = int(parse_fields(tx.payload).get("err", "-1"), 0) if tx else None
        classification = classify_control(
            command_tx=tx is not None, tx_err=tx_err, rejected=reject is not None,
            ctrl_before=int(pre["ctrl_rx"], 0) if "ctrl_rx" in pre else None,
            ctrl_after=int(post["ctrl_rx"], 0) if "ctrl_rx" in post else None,
            raw_reply=bool(matching), correlation_matches=bool(item.get("reply")))
        result_rounds.append({"round": number, "host_probe": item,
            "command_tx": tx.raw if tx else None, "reject": reject.raw if reject else None,
            "raw_replies": [row.raw for row in replies], "matching_reply": [row.raw for row in matching],
            "telemetry_before": pre, "telemetry_after": post,
            "streaming_continued": bool(before and after), "classification": classification})
    kinds = {kind: sum(1 for row in rows if row.payload.startswith(kind) and
                       (f"name={TARGET} " in row.payload or kind in
                        ("FUSION_COMMAND_TX", "FUSION_COMMAND_REJECT", "FUSION_REPLY")))
             for kind in ("FUSION_UWB", "FUSION_IMU", "FUSION_TELEMETRY",
                          "FUSION_COMMAND_TX", "FUSION_COMMAND_REJECT", "FUSION_REPLY")}
    return {"schema": "biospur-bsf1120-control-diagnostic-v1",
            "raw_log": str(log_path),
            "raw_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(),
            "duration_s": rows[-1].monotonic - rows[0].monotonic if rows else 0,
            "record_counts": kinds, "target_rounds": result_rounds}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--reanalyze", action="store_true",
                        help="read the existing raw log/result without device access")
    args = parser.parse_args()
    if args.reanalyze:
        result_path = args.out_dir / "result.json"
        value = json.loads(result_path.read_text(encoding="utf-8"))
        value["analysis"] = analyze(args.out_dir / "fusion_cdc.log", value["rounds"])
        result_path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        return 0
    args.out_dir.mkdir(parents=True, exist_ok=False)
    log_path = args.out_dir / "fusion_cdc.log"
    result = {"schema": "biospur-bsf1120-control-capture-v1",
              "started": datetime.now(timezone.utc).astimezone().isoformat(), "rounds": []}
    channel = None
    started = time.monotonic()
    try:
        with log_path.open("x", encoding="utf-8", buffering=1) as log:
            channel = ThreadedLineChannel(resolve_fusion_port(args.fusion_port), log, "FUSION",
                decoded_queue_records=65536, backlog_red_records=8192,
                raw_backlog_red_bytes=8192, stall_red_s=1)
            channel.transport_mode = "binary"; channel.text_pending.clear()
            result["decode_before_send"] = decode_guard(channel, 15)
            channel.send("MASTER STATUS"); drain(channel, 2)
            channel.send("LIST"); drain(channel, 3)
            drain(channel, 5)
            for number in range(1, 4):
                round_row = {"round": number, "probes": []}
                for node in PEERS:
                    round_row["probes"].append(probe(channel, node))
                result["rounds"].append(round_row)
                drain(channel, 10)
            remaining = 60.0 - (time.monotonic() - started)
            if remaining > 0:
                drain(channel, remaining)
    finally:
        if channel:
            channel.close()
    result["ended"] = datetime.now(timezone.utc).astimezone().isoformat()
    result["analysis"] = analyze(log_path, result["rounds"])
    (args.out_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
