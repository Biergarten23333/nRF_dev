#!/usr/bin/env python3
"""Targeted Master-side BSF1120 peer redraw with bounded read-only verification."""

from __future__ import annotations

import argparse, hashlib, json, time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from qualify_ota_confirmation_timing import NODES, collect_list, read_ping

TARGET = "BSF1120"


def collect(channel, seconds: float) -> list[str]:
    deadline = time.monotonic() + seconds; lines = []
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is not None: lines.append(line)
    return lines


def snapshot(channel) -> dict:
    aggregate, peers = collect_list(channel)
    return {"aggregate": aggregate, "peers": peers}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port"); parser.add_argument("--compact-existing",action="store_true"); args = parser.parse_args()
    if args.compact_existing:
      path=args.out_dir/"result.json"; value=json.loads(path.read_text(encoding="utf-8"))
      lines=value.get("reconnect_lines",[])
      value["reconnect_evidence"]=[line for line in lines if any(token in line for token in
        ("RECONNECT","DISCONNECTED","BRIDGE_READY","SCAN_STARTED","SCAN_WAITING"))]
      value["reconnect_observed_records"]=len(lines); value.pop("reconnect_lines",None)
      path.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n",encoding="utf-8");return 0
    args.out_dir.mkdir(parents=True, exist_ok=False); log_path=args.out_dir/"fusion_cdc.log"
    result={"schema":"biospur-targeted-peer-recovery-v1","target":TARGET,
            "started":datetime.now(timezone.utc).astimezone().isoformat()}
    channel=None
    try:
      with log_path.open("x",encoding="utf-8",buffering=1) as log:
        channel=ThreadedLineChannel(resolve_fusion_port(args.fusion_port),log,"FUSION",
            decoded_queue_records=65536,backlog_red_records=8192,
            raw_backlog_red_bytes=8192,stall_red_s=1)
        channel.transport_mode="binary";channel.text_pending.clear()
        result["decode_before_send"]=decode_guard(channel,15)
        result["before"]=snapshot(channel)
        channel.send(f"{TARGET} RECONNECT")
        result["reconnect_started_monotonic"]=time.monotonic()
        result["reconnect_lines"]=collect(channel,30)
        result["after"]=snapshot(channel)
        result["target_pings"]=[read_ping(channel,TARGET) for _ in range(3)]
        try: result["target_status"]=b306_command(channel,TARGET,"STATUS","STATUS ")
        except Exception as exc: result["target_status_error"]=f"{type(exc).__name__}: {exc}"
        result["other_peer_pings"]={node:read_ping(channel,node) for node in NODES if node!=TARGET}
        stream=collect(channel,10); result["post_stream_counts"]={kind:sum(
            1 for line in stream if line.startswith(kind) and f"name={TARGET} " in line)
            for kind in ("FUSION_UWB","FUSION_IMU","FUSION_TELEMETRY")}
        peers={parse_fields(line).get("name"):parse_fields(line) for line in result["after"]["peers"]}
        result["pass"]=(set(peers)==set(NODES) and all(peers[n].get("connected")=="1" and
            peers[n].get("subscribed")=="1" for n in NODES) and
            all(parse_fields(p.get("text"," ")).get("name")==TARGET for p in result["target_pings"]) and
            "target_status" in result and
            all(parse_fields(p.get("text"," ")).get("name")==n for n,p in result["other_peer_pings"].items()) and
            all(result["post_stream_counts"][kind]>0 for kind in result["post_stream_counts"]))
    finally:
      if channel:channel.close()
    result["ended"]=datetime.now(timezone.utc).astimezone().isoformat()
    result["raw_sha256"]=hashlib.sha256(log_path.read_bytes()).hexdigest()
    (args.out_dir/"result.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return 0 if result.get("pass") else 2

if __name__=="__main__":raise SystemExit(main())
